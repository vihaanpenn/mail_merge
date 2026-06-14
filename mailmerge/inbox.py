"""Read replies back over IMAP, classify them, and attribute them to contacts.

This closes the loop: after you send, `sync` scans your inbox (read-only — it
uses BODY.PEEK so it never marks your mail as read), figures out which inbound
messages are genuine replies vs auto-replies vs bounces, links each to the
contact it came from, and updates that contact's status accordingly.
"""

from __future__ import annotations

import datetime as dt
import email
import html as html_mod
import imaplib
import os
import re
import ssl
import sys
from dataclasses import dataclass
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime

from .context import Context
from .utils import normalize_email, truncate

EMAIL_IN_TEXT_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
UIDVALIDITY_RE = re.compile(rb"UIDVALIDITY (\d+)")

# Senders / content types that authoritatively indicate a bounce (DSN).
BOUNCE_SENDERS = ("mailer-daemon", "postmaster", "mail delivery", "maild")
# Subject phrases that *suggest* a bounce — only trusted when the message is not
# threaded to one of our own sent messages (a real reply can quote these words).
BOUNCE_SUBJECTS = (
    "undelivered", "delivery status notification", "delivery has failed",
    "returned mail", "mail delivery failed", "failure notice",
    "undeliverable", "address not found",
)
AUTO_SUBJECTS = (
    "out of office", "automatic reply", "auto-reply", "autoreply",
    "auto: ", "away from", "on vacation", "on leave", "annual leave",
)
OOO_RE = re.compile(
    r"\b(out of office|on vacation|on leave|away from|annual leave|parental leave|maternity|paternity)\b",
    re.IGNORECASE,
)


@dataclass
class SyncResult:
    scanned: int = 0
    new: int = 0
    replies: int = 0
    auto_replies: int = 0
    bounces: int = 0
    unmatched: int = 0


def imap_password(ctx: Context) -> str:
    env_var = ctx.cfg["imap"].get("password_env") or ctx.cfg["auth"].get("password_env")
    password = os.environ.get(env_var or "")
    if not password:
        sys.exit(
            f"No IMAP password found in environment variable {env_var}.\n"
            f"For Gmail this is the same App Password you send with."
        )
    return password


def imap_connect(ctx: Context) -> imaplib.IMAP4_SSL:
    icfg = ctx.cfg["imap"]
    # Verify the server certificate and hostname (default context does both).
    conn = imaplib.IMAP4_SSL(
        icfg["host"], int(icfg.get("port", 993)), ssl_context=ssl.create_default_context()
    )
    conn.login(ctx.cfg["sender"]["email"], imap_password(ctx))
    return conn


# ---------------------------------------------------------------------------
# Body extraction
# ---------------------------------------------------------------------------

def _decode_part(part: Message) -> str:
    try:
        return part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        return payload.decode(part.get_content_charset() or "utf-8", "replace")


def _strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html_mod.unescape(raw)).strip()


def _get_text_body(msg: Message) -> str:
    """Best-effort human-readable body: prefer text/plain, fall back to stripped HTML."""
    if msg.is_multipart():
        html_fallback = ""
        for part in msg.walk():
            if part.is_multipart():
                continue
            if "attachment" in str(part.get("Content-Disposition", "")).lower():
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
                return _decode_part(part)
            if ctype == "text/html" and not html_fallback:
                html_fallback = _strip_html(_decode_part(part))
        return html_fallback
    body = _decode_part(msg)
    return _strip_html(body) if msg.get_content_type() == "text/html" else body


def _bounce_scan_text(msg: Message, fallback: str) -> str:
    """Text to scan for the failed recipient of a DSN. Includes the whole
    serialized message (so addresses buried in delivery-status / message/rfc822
    parts are recovered, not just the human-readable notice) plus the already-
    extracted body as a fallback."""
    try:
        serialized = msg.as_string()
    except Exception:
        serialized = ""
    return serialized + "\n" + (fallback or "")


def _message_ids(value: str | None) -> list[str]:
    if not value:
        return []
    return re.findall(r"<[^>]+>", value)


# ---------------------------------------------------------------------------
# Classification & matching
# ---------------------------------------------------------------------------

def classify(msg: Message, from_addr: str, subject: str, *, threaded: bool = False) -> str:
    """reply / auto_reply / ooo / bounce.

    A bounce is only declared on strong signals (daemon sender or a DSN
    multipart/report). Subject-phrase heuristics are trusted only when the
    message is NOT threaded to one of our sent emails — otherwise a genuine
    reply that happens to quote "address not found" would be marked bounced.
    """
    subj = (subject or "").lower()
    sender = (from_addr or "").lower()

    if (msg.get("Auto-Submitted", "").lower().startswith("auto")
            or msg.get("X-Autoreply")
            or msg.get("X-Autorespond")
            or any(s in subj for s in AUTO_SUBJECTS)):
        return "ooo" if OOO_RE.search(subj) else "auto_reply"

    strong_bounce = (
        any(s in sender for s in BOUNCE_SENDERS)
        or msg.get_content_type() == "multipart/report"
    )
    weak_bounce = any(s in subj for s in BOUNCE_SUBJECTS)
    if strong_bounce or (weak_bounce and not threaded):
        return "bounce"

    return "reply"


def _thread_contact(ctx: Context, msg: Message):
    for ref in _message_ids(msg.get("In-Reply-To")) + _message_ids(msg.get("References")):
        contact = ctx.db.find_contact_by_message_id(ref)
        if contact:
            return contact
    return None


def _match_contact(ctx: Context, msg: Message, from_addr: str, classification: str, body: str):
    # 1) The actual sender is themselves a known contact — most trustworthy, and
    #    correctly attributes a forwarded reply to whoever actually wrote it.
    if from_addr:
        contact = ctx.db.get_contact_by_email(from_addr)
        if contact:
            return contact

    # 2) Thread the reply back to the specific message we sent.
    contact = _thread_contact(ctx, msg)
    if contact:
        return contact

    # 3) Bounces come from a mail daemon; recover the failed recipient from the
    #    full DSN body (delivery-status + embedded original message).
    if classification == "bounce":
        for candidate in EMAIL_IN_TEXT_RE.findall(_bounce_scan_text(msg, body)):
            contact = ctx.db.get_contact_by_email(candidate)
            if contact:
                return contact
    return None


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def _read_uidvalidity(conn: imaplib.IMAP4_SSL, mailbox: str) -> str:
    try:
        typ, data = conn.status(mailbox, "(UIDVALIDITY)")
        if typ == "OK" and data:
            m = UIDVALIDITY_RE.search(data[0] if isinstance(data[0], bytes) else str(data[0]).encode())
            if m:
                return m.group(1).decode()
    except Exception:
        pass
    return "0"


def sync(ctx: Context, *, lookback_days: int | None = None) -> SyncResult:
    if not ctx.cfg["imap"].get("enabled", True):
        sys.exit("IMAP is disabled in config (imap.enabled: false).")

    icfg = ctx.cfg["imap"]
    mailbox = icfg.get("mailbox", "INBOX")
    lookback = lookback_days if lookback_days is not None else int(icfg.get("lookback_days", 30))

    conn = imap_connect(ctx)
    result = SyncResult()
    try:
        # UIDVALIDITY guards against the server renumbering UIDs out from under us.
        uidvalidity = _read_uidvalidity(conn, mailbox)
        conn.select(mailbox, readonly=True)
        since = (dt.date.today() - dt.timedelta(days=lookback)).strftime("%d-%b-%Y")
        typ, data = conn.uid("SEARCH", None, "SINCE", since)
        if typ != "OK":
            return result
        uids = data[0].split()
        result.scanned = len(uids)

        for raw_uid in uids:
            uid_key = f"{mailbox}:{uidvalidity}:{raw_uid.decode()}"
            if ctx.db.reply_uid_seen(uid_key):
                continue

            # PEEK so we never set the \Seen flag on the user's mail.
            typ, fetched = conn.uid("FETCH", raw_uid, "(BODY.PEEK[])")
            if typ != "OK" or not fetched or not isinstance(fetched[0], tuple):
                continue
            msg = email.message_from_bytes(fetched[0][1])

            from_addr = normalize_email(parseaddr(msg.get("From", ""))[1])
            subject = str(msg.get("Subject", ""))
            body = _get_text_body(msg)
            threaded = _thread_contact(ctx, msg) is not None
            classification = classify(msg, from_addr, subject, threaded=threaded)
            contact = _match_contact(ctx, msg, from_addr, classification, body)

            try:
                received_at = parsedate_to_datetime(msg.get("Date", "")).isoformat()
            except Exception:
                received_at = None

            # Only persist mail we can tie to a contact, to keep the table clean.
            if contact is None:
                if classification in ("reply", "bounce"):
                    result.unmatched += 1
                continue

            ctx.db.record_reply(
                contact_id=contact["id"],
                uid=uid_key,
                message_id=(msg.get("Message-ID") or "").strip() or None,
                in_reply_to=(msg.get("In-Reply-To") or "").strip() or None,
                from_addr=from_addr,
                subject=truncate(subject, 200),
                snippet=truncate(body, 280),
                classification=classification,
                received_at=received_at,
            )
            result.new += 1

            if classification == "reply":
                result.replies += 1
                ctx.db.mark_replied(contact["id"], received_at)
            elif classification == "bounce":
                result.bounces += 1
                ctx.db.mark_bounced(contact["id"], received_at)
            else:  # auto_reply / ooo: record, but keep chasing
                result.auto_replies += 1
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return result
