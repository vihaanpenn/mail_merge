from email import message_from_bytes, message_from_string

import pytest

from mailmerge import inbox
from tests.conftest import add_contact
from tests.fakedata import make_dsn, make_html, make_raw


def msg(headers: str, body: str = "thanks!"):
    return message_from_string(headers.strip() + "\n\n" + body)


# -- classify --------------------------------------------------------------

@pytest.mark.parametrize("subject", [
    "Re: would love to talk",
    "Re: your warehouse arms",
    "thanks for reaching out",
])
def test_classify_plain_reply(subject):
    m = msg(f"From: ada@nimbus.io\nSubject: {subject}")
    assert inbox.classify(m, "ada@nimbus.io", subject) == "reply"


@pytest.mark.parametrize("header", [
    "Auto-Submitted: auto-replied",
    "X-Autoreply: yes",
    "X-Autorespond: yes",
])
def test_classify_auto_reply_headers(header):
    m = msg(f"From: ada@nimbus.io\nSubject: Re: hi\n{header}")
    assert inbox.classify(m, "ada@nimbus.io", "Re: hi") == "auto_reply"


@pytest.mark.parametrize("subject", [
    "Out of Office",
    "Automatic reply: out of office until Monday",
    "I am on vacation",
    "Currently on leave",
])
def test_classify_ooo(subject):
    m = msg(f"From: ada@nimbus.io\nSubject: {subject}")
    assert inbox.classify(m, "ada@nimbus.io", subject) == "ooo"


def test_classify_giveaway_is_not_ooo():
    subj = "Re: our giveaway is live"
    m = msg(f"From: ada@nimbus.io\nSubject: {subj}\nAuto-Submitted: auto-replied")
    assert inbox.classify(m, "ada@nimbus.io", subj) == "auto_reply"


@pytest.mark.parametrize("sender", [
    "mailer-daemon@google.com",
    "postmaster@example.com",
    "Mail Delivery System <maild@relay.net>",
])
def test_classify_bounce_by_sender(sender):
    subj = "Delivery Status Notification (Failure)"
    m = msg(f"From: {sender}\nSubject: {subj}")
    addr = sender.split("<")[-1].strip(">") if "<" in sender else sender
    assert inbox.classify(m, addr, subj) == "bounce"


def test_classify_bounce_by_content_type():
    m = message_from_bytes(make_dsn("ada@nimbus.io", from_addr="noreply@relay.net"))
    assert inbox.classify(m, "noreply@relay.net", str(m.get("Subject"))) == "bounce"


def test_classify_threaded_bounce_phrase_is_reply():
    subj = "Re: your question about address not found"
    m = msg(f"From: jane@acme.com\nSubject: {subj}")
    assert inbox.classify(m, "jane@acme.com", subj, threaded=True) == "reply"
    assert inbox.classify(m, "jane@acme.com", subj, threaded=False) == "bounce"


# -- message-id parsing ----------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("<a@x>", ["<a@x>"]),
    ("<a@x> <b@y>", ["<a@x>", "<b@y>"]),
    ("<a@x>\n <b@y>", ["<a@x>", "<b@y>"]),
    (None, []),
    ("", []),
    ("no brackets", []),
])
def test_message_ids(value, expected):
    assert inbox._message_ids(value) == expected


# -- body extraction -------------------------------------------------------

def test_get_text_body_plain():
    m = message_from_bytes(make_raw("a@b.co", "hi", "plain body content"))
    assert "plain body content" in inbox._get_text_body(m)


def test_get_text_body_prefers_plain_over_html():
    raw = (
        "From: a@b.co\nSubject: hi\nMIME-Version: 1.0\n"
        'Content-Type: multipart/alternative; boundary="BB"\n\n'
        "--BB\nContent-Type: text/plain\n\nthe plain part\n"
        "--BB\nContent-Type: text/html\n\n<p>the html part</p>\n--BB--\n"
    )
    assert "the plain part" in inbox._get_text_body(message_from_string(raw))


def test_get_text_body_html_only_stripped():
    m = message_from_bytes(make_html("a@b.co", "hi", "<p>Hello <b>world</b>!</p>"))
    out = inbox._get_text_body(m)
    assert "Hello world" in out and "<" not in out


def test_strip_html_removes_scripts():
    out = inbox._strip_html("<style>x{}</style><p>keep &amp; this</p><script>bad()</script>")
    assert "keep & this" in out and "bad()" not in out


# -- matching --------------------------------------------------------------

def test_match_by_thread(ctx):
    cid = add_contact(ctx)
    ctx.db.record_message(cid, campaign="c", template="warm", step=0, subject="s",
                          body="b", message_id="<sent-123@me.com>", status="sent")
    m = msg("From: stranger@elsewhere.com\nSubject: Re: s\nIn-Reply-To: <sent-123@me.com>")
    found = inbox._match_contact(ctx, m, "stranger@elsewhere.com", "reply", "thanks")
    assert found is not None and found["id"] == cid


def test_match_by_references_header(ctx):
    cid = add_contact(ctx)
    ctx.db.record_message(cid, campaign="c", template="warm", step=0, subject="s",
                          body="b", message_id="<sent-9@me.com>", status="sent")
    m = msg("From: x@y.co\nSubject: Re: s\nReferences: <other@z> <sent-9@me.com>")
    found = inbox._match_contact(ctx, m, "x@y.co", "reply", "thanks")
    assert found["id"] == cid


def test_match_by_from_address(ctx):
    cid = add_contact(ctx, email="ada@drone.co")
    m = msg("From: Ada <ada@drone.co>\nSubject: Re: s")
    assert inbox._match_contact(ctx, m, "ada@drone.co", "reply", "x")["id"] == cid


def test_from_contact_precedence_over_thread(ctx):
    a = add_contact(ctx, email="ada@drone.co")
    b = add_contact(ctx, email="bob@other.co", company="Other")
    ctx.db.record_message(a, campaign="c", template="warm", step=0, subject="s",
                          body="b", message_id="<a-thread@me.com>", status="sent")
    m = msg("From: bob@other.co\nSubject: Re: s\nIn-Reply-To: <a-thread@me.com>")
    assert inbox._match_contact(ctx, m, "bob@other.co", "reply", "x")["id"] == b


def test_match_bounce_by_body_recipient(ctx):
    cid = add_contact(ctx, email="ada@drone.co")
    m = msg("From: mailer-daemon@google.com\nSubject: Undeliverable")
    body = "Your message to ada@drone.co could not be delivered. 550 user unknown."
    assert inbox._match_contact(ctx, m, "mailer-daemon@google.com", "bounce", body)["id"] == cid


def test_match_bounce_via_full_dsn(ctx):
    cid = add_contact(ctx, email="ada@nimbus.io")
    m = message_from_bytes(make_dsn("ada@nimbus.io"))
    found = inbox._match_contact(ctx, m, "mailer-daemon@googlemail.com", "bounce", "could not deliver")
    assert found is not None and found["id"] == cid


def test_no_match_returns_none(ctx):
    add_contact(ctx, email="ada@drone.co")
    m = msg("From: stranger@nowhere.com\nSubject: hi")
    assert inbox._match_contact(ctx, m, "stranger@nowhere.com", "reply", "x") is None


# -- uidvalidity helper ----------------------------------------------------

def test_read_uidvalidity_parses():
    class C:
        def status(self, mailbox, what):
            return ("OK", [b"INBOX (UIDVALIDITY 424242)"])
    assert inbox._read_uidvalidity(C(), "INBOX") == "424242"


def test_read_uidvalidity_failure_default():
    class C:
        def status(self, mailbox, what):
            raise RuntimeError("nope")
    assert inbox._read_uidvalidity(C(), "INBOX") == "0"
