"""SQLite persistence layer.

The database is the single source of truth. It is designed to be *re-imported and
adjusted continuously*: importing a spreadsheet upserts on email, so you can keep
editing your list and re-running import without losing send/reply history.

Tables
------
contacts : people you might contact, plus their lifecycle state
messages : every outbound email we generated (sent / test / preview / error)
replies  : every inbound message we matched back from IMAP, classified
meta     : schema version + small key/value bookkeeping
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from .utils import now_iso

SCHEMA_VERSION = 1

# Lifecycle statuses a contact can hold.
STATUS_NEW = "new"
STATUS_CONTACTED = "contacted"
STATUS_REPLIED = "replied"
STATUS_BOUNCED = "bounced"
STATUS_UNSUBSCRIBED = "unsubscribed"
STATUS_DO_NOT_CONTACT = "do_not_contact"

# Statuses that make a contact ineligible for any (further) outreach.
TERMINAL_STATUSES = {
    STATUS_REPLIED,
    STATUS_BOUNCED,
    STATUS_UNSUBSCRIBED,
    STATUS_DO_NOT_CONTACT,
}

# Core contact columns that map directly to spreadsheet fields. Anything else
# from an imported row is preserved verbatim in the JSON `extra` column.
CONTACT_CORE_FIELDS = (
    "first_name",
    "full_name",
    "company",
    "title",
    "confidence",
    "personalization",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    email             TEXT    NOT NULL UNIQUE,
    first_name        TEXT    DEFAULT '',
    full_name         TEXT    DEFAULT '',
    company           TEXT    DEFAULT '',
    title             TEXT    DEFAULT '',
    confidence        TEXT    DEFAULT '',
    personalization   TEXT    DEFAULT '',
    status            TEXT    NOT NULL DEFAULT 'new',
    tags              TEXT    DEFAULT '',
    notes             TEXT    DEFAULT '',
    source            TEXT    DEFAULT '',
    extra             TEXT    DEFAULT '{}',
    last_step         INTEGER NOT NULL DEFAULT -1,
    last_contacted_at TEXT,
    replied_at        TEXT,
    bounced_at        TEXT,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id  INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    campaign    TEXT    DEFAULT '',
    template    TEXT    DEFAULT '',
    step        INTEGER NOT NULL DEFAULT 0,
    subject     TEXT    DEFAULT '',
    body        TEXT    DEFAULT '',
    message_id  TEXT,
    status      TEXT    NOT NULL,
    error       TEXT    DEFAULT '',
    sent_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS replies (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id     INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    uid            TEXT UNIQUE,
    message_id     TEXT,
    in_reply_to    TEXT,
    from_addr      TEXT,
    subject        TEXT,
    snippet        TEXT,
    classification TEXT,
    received_at    TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_contacts_status   ON contacts(status);
CREATE INDEX IF NOT EXISTS idx_messages_contact  ON messages(contact_id);
CREATE INDEX IF NOT EXISTS idx_messages_msgid    ON messages(message_id);
CREATE INDEX IF NOT EXISTS idx_replies_contact   ON replies(contact_id);
"""


def _like_escape(value: str) -> str:
    """Escape SQL LIKE metacharacters so user input matches literally (ESCAPE '\\')."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Database:
    """Thin, well-typed wrapper around a SQLite connection."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    # -- lifecycle ---------------------------------------------------------

    def _init_schema(self) -> None:
        self.conn.executescript(_SCHEMA)
        current = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if current < SCHEMA_VERSION:
            # Room for future ALTER-based migrations keyed on `current`.
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.set_meta("schema_version", str(SCHEMA_VERSION))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # -- meta --------------------------------------------------------------

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    # -- contacts ----------------------------------------------------------

    def upsert_contact(self, fields: dict[str, Any]) -> tuple[int, bool]:
        """Insert or update a contact by email. Returns (contact_id, created).

        Send history is preserved on update. Only non-empty incoming values
        overwrite existing core fields, so re-importing a partial sheet never
        blanks out data you already have. The `extra` JSON is merged.
        """
        email = (fields.get("email") or "").strip().lower()
        if not email:
            raise ValueError("upsert_contact requires a non-empty email")

        now = now_iso()
        existing = self.get_contact_by_email(email)
        incoming_extra = fields.get("extra") or {}
        if isinstance(incoming_extra, str):
            incoming_extra = json.loads(incoming_extra or "{}")
        # Never persist empty-string columns into `extra` (keeps insert/update
        # paths consistent and avoids permanently-stuck blank keys).
        incoming_extra = {k: v for k, v in incoming_extra.items() if v not in (None, "")}

        if existing is None:
            extra = json.dumps(incoming_extra)
            cur = self.conn.execute(
                """
                INSERT INTO contacts
                    (email, first_name, full_name, company, title, confidence,
                     personalization, status, tags, notes, source, extra,
                     created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    email,
                    fields.get("first_name", ""),
                    fields.get("full_name", ""),
                    fields.get("company", ""),
                    fields.get("title", ""),
                    fields.get("confidence", ""),
                    fields.get("personalization", ""),
                    fields.get("status", STATUS_NEW),
                    fields.get("tags", ""),
                    fields.get("notes", ""),
                    fields.get("source", ""),
                    extra,
                    now,
                    now,
                ),
            )
            self.conn.commit()
            return int(cur.lastrowid), True

        # Update path: only overwrite core fields when a non-empty value arrives.
        updates: dict[str, Any] = {}
        for field in CONTACT_CORE_FIELDS:
            value = fields.get(field)
            if value not in (None, "") and value != existing[field]:
                updates[field] = value
        for field in ("tags", "notes", "source"):
            value = fields.get(field)
            if value not in (None, ""):
                updates[field] = value

        merged_extra = dict(json.loads(existing["extra"] or "{}"))
        merged_extra.update(incoming_extra)
        updates["extra"] = json.dumps(merged_extra)
        updates["updated_at"] = now

        sets = ", ".join(f"{k}=?" for k in updates)
        self.conn.execute(
            f"UPDATE contacts SET {sets} WHERE id=?",
            (*updates.values(), existing["id"]),
        )
        self.conn.commit()
        return int(existing["id"]), False

    def get_contact_by_email(self, email: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM contacts WHERE email=?", (email.strip().lower(),)
        ).fetchone()

    def get_contact(self, contact_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM contacts WHERE id=?", (contact_id,)
        ).fetchone()

    def list_contacts(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        tag: str | None = None,
        limit: int | None = None,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if tag:
            # Exact tag membership against the comma-delimited list, with LIKE
            # metacharacters escaped so a tag like "v_p" can't act as a wildcard.
            clauses.append("(',' || tags || ',') LIKE ? ESCAPE '\\'")
            params.append(f"%,{_like_escape(tag)},%")
        if search:
            clauses.append(
                "(email LIKE ? ESCAPE '\\' OR company LIKE ? ESCAPE '\\' "
                "OR full_name LIKE ? ESCAPE '\\' OR personalization LIKE ? ESCAPE '\\')"
            )
            params.extend([f"%{_like_escape(search)}%"] * 4)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM contacts {where} ORDER BY company, full_name"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return list(self.conn.execute(sql, params).fetchall())

    def update_contact(self, contact_id: int, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(
            f"UPDATE contacts SET {sets} WHERE id=?",
            (*fields.values(), contact_id),
        )
        self.conn.commit()

    def set_status(self, contact_id: int, status: str) -> None:
        self.update_contact(contact_id, status=status)

    def add_tag(self, contact_id: int, tag: str) -> None:
        row = self.get_contact(contact_id)
        if row is None:
            return
        tags = [t for t in (row["tags"] or "").split(",") if t]
        if tag not in tags:
            tags.append(tag)
        self.update_contact(contact_id, tags=",".join(tags))

    # -- messages ----------------------------------------------------------

    def record_message(
        self,
        contact_id: int,
        *,
        campaign: str,
        template: str,
        step: int,
        subject: str,
        body: str,
        message_id: str | None,
        status: str,
        error: str = "",
    ) -> int:
        now = now_iso()
        cur = self.conn.execute(
            """
            INSERT INTO messages
                (contact_id, campaign, template, step, subject, body,
                 message_id, status, error, sent_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (contact_id, campaign, template, step, subject, body,
             message_id, status, error, now),
        )
        # Advance the contact's lifecycle only on a real, successful send.
        if status == "sent":
            self.conn.execute(
                "UPDATE contacts SET last_step=?, last_contacted_at=?, "
                "status=CASE WHEN status='new' THEN 'contacted' ELSE status END, "
                "updated_at=? WHERE id=?",
                (step, now, now, contact_id),
            )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_messages(self, contact_id: int | None = None) -> list[sqlite3.Row]:
        if contact_id is None:
            return list(self.conn.execute(
                "SELECT * FROM messages ORDER BY sent_at DESC"
            ).fetchall())
        return list(self.conn.execute(
            "SELECT * FROM messages WHERE contact_id=? ORDER BY sent_at", (contact_id,)
        ).fetchall())

    def count_followups_sent(self, contact_id: int) -> int:
        """How many follow-up steps (step >= 1) have actually been sent to a contact.

        Used to pick the next follow-up by *position* rather than by literal step
        number, so follow-ups work even if configured steps are non-contiguous.
        """
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM messages "
            "WHERE contact_id=? AND status='sent' AND step >= 1",
            (contact_id,),
        ).fetchone()
        return int(row["n"])

    def find_contact_by_message_id(self, message_id: str) -> sqlite3.Row | None:
        """Match an outbound Message-ID (used to link inbound replies to a thread)."""
        if not message_id:
            return None
        row = self.conn.execute(
            "SELECT c.* FROM messages m JOIN contacts c ON c.id = m.contact_id "
            "WHERE m.message_id=? ORDER BY m.sent_at DESC LIMIT 1",
            (message_id,),
        ).fetchone()
        return row

    def sent_today(self, *, reference_date: str | None = None) -> int:
        day = (reference_date or now_iso())[:10]
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM messages "
            "WHERE status='sent' AND substr(sent_at,1,10)=?",
            (day,),
        ).fetchone()
        return int(row["n"])

    # -- replies -----------------------------------------------------------

    def reply_uid_seen(self, uid: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM replies WHERE uid=?", (uid,)
        ).fetchone()
        return row is not None

    def record_reply(
        self,
        *,
        contact_id: int | None,
        uid: str,
        message_id: str | None,
        in_reply_to: str | None,
        from_addr: str,
        subject: str,
        snippet: str,
        classification: str,
        received_at: str | None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO replies
                (contact_id, uid, message_id, in_reply_to, from_addr, subject,
                 snippet, classification, received_at, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (contact_id, uid, message_id, in_reply_to, from_addr, subject,
             snippet, classification, received_at, now_iso()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_replies(self, classification: str | None = None) -> list[sqlite3.Row]:
        if classification:
            return list(self.conn.execute(
                "SELECT r.*, c.company AS company FROM replies r "
                "LEFT JOIN contacts c ON c.id = r.contact_id "
                "WHERE r.classification=? ORDER BY r.received_at DESC",
                (classification,),
            ).fetchall())
        return list(self.conn.execute(
            "SELECT r.*, c.company AS company FROM replies r "
            "LEFT JOIN contacts c ON c.id = r.contact_id "
            "ORDER BY r.received_at DESC"
        ).fetchall())

    def mark_replied(self, contact_id: int, when: str | None = None) -> None:
        self.update_contact(
            contact_id, status=STATUS_REPLIED, replied_at=when or now_iso()
        )

    def mark_bounced(self, contact_id: int, when: str | None = None) -> None:
        # Record the bounce, but never downgrade someone who already replied.
        row = self.get_contact(contact_id)
        when = when or now_iso()
        if row is not None and row["status"] == STATUS_REPLIED:
            self.update_contact(contact_id, bounced_at=when)
        else:
            self.update_contact(contact_id, status=STATUS_BOUNCED, bounced_at=when)

    # -- aggregate reporting ----------------------------------------------

    def status_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM contacts GROUP BY status"
        ).fetchall()
        return {r["status"]: int(r["n"]) for r in rows}

    def totals(self) -> dict[str, int]:
        def scalar(sql: str, params: Iterable[Any] = ()) -> int:
            return int(self.conn.execute(sql, tuple(params)).fetchone()[0])

        return {
            "contacts": scalar("SELECT COUNT(*) FROM contacts"),
            "messages_sent": scalar("SELECT COUNT(*) FROM messages WHERE status='sent'"),
            "messages_error": scalar("SELECT COUNT(*) FROM messages WHERE status='error'"),
            "replies": scalar("SELECT COUNT(*) FROM replies WHERE classification='reply'"),
            "auto_replies": scalar("SELECT COUNT(*) FROM replies WHERE classification IN ('auto_reply','ooo')"),
            "bounces": scalar("SELECT COUNT(*) FROM replies WHERE classification='bounce'"),
            "sent_today": self.sent_today(),
        }
