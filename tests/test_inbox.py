from email import message_from_string

from mailmerge import inbox
from tests.conftest import add_contact


def msg(headers: str, body: str = "thanks!"):
    return message_from_string(headers.strip() + "\n\n" + body)


def test_classify_reply():
    m = msg("From: ada@drone.co\nSubject: Re: would love to talk")
    assert inbox.classify(m, "ada@drone.co", "Re: would love to talk") == "reply"


def test_classify_auto_reply_header():
    m = msg("From: ada@drone.co\nSubject: Re: hello\nAuto-Submitted: auto-replied")
    assert inbox.classify(m, "ada@drone.co", "Re: hello") == "auto_reply"


def test_classify_out_of_office():
    subj = "Automatic reply: Out of office"
    m = msg(f"From: ada@drone.co\nSubject: {subj}")
    assert inbox.classify(m, "ada@drone.co", subj) == "ooo"


def test_classify_bounce_by_sender():
    subj = "Delivery Status Notification (Failure)"
    m = msg(f"From: mailer-daemon@google.com\nSubject: {subj}")
    assert inbox.classify(m, "mailer-daemon@google.com", subj) == "bounce"


def test_classify_threaded_reply_with_bounce_phrase_is_not_bounce():
    # A genuine human reply that happens to quote a bounce phrase must stay a reply
    # when it's threaded to one of our sent emails.
    subj = "Re: your question about address not found"
    m = msg(f"From: jane@acme.com\nSubject: {subj}")
    assert inbox.classify(m, "jane@acme.com", subj, threaded=True) == "reply"
    assert inbox.classify(m, "jane@acme.com", subj, threaded=False) == "bounce"


def test_classify_giveaway_is_not_ooo():
    # bare 'away' must not trigger OOO (word-boundary matching)
    subj = "Re: our giveaway is live"
    m = msg(f"From: jane@acme.com\nSubject: {subj}\nAuto-Submitted: auto-replied")
    assert inbox.classify(m, "jane@acme.com", subj) == "auto_reply"


def test_message_ids_parsing():
    assert inbox._message_ids("<a@x> <b@y>") == ["<a@x>", "<b@y>"]
    assert inbox._message_ids(None) == []


def test_match_by_thread(ctx):
    cid = add_contact(ctx)
    ctx.db.record_message(cid, campaign="c", template="warm", step=0, subject="s",
                          body="b", message_id="<sent-123@me.com>", status="sent")
    m = msg("From: someone-else@elsewhere.com\nSubject: Re: s\n"
            "In-Reply-To: <sent-123@me.com>")
    found = inbox._match_contact(ctx, m, "someone-else@elsewhere.com", "reply", "thanks")
    assert found is not None and found["id"] == cid


def test_match_by_from_address(ctx):
    cid = add_contact(ctx, email="ada@drone.co")
    m = msg("From: Ada <ada@drone.co>\nSubject: Re: s")
    found = inbox._match_contact(ctx, m, "ada@drone.co", "reply", "thanks")
    assert found is not None and found["id"] == cid


def test_match_bounce_by_body_recipient(ctx):
    cid = add_contact(ctx, email="ada@drone.co")
    m = msg("From: mailer-daemon@google.com\nSubject: Undeliverable")
    body = "Your message to ada@drone.co could not be delivered. 550 user unknown."
    found = inbox._match_contact(ctx, m, "mailer-daemon@google.com", "bounce", body)
    assert found is not None and found["id"] == cid


def test_no_match_returns_none(ctx):
    add_contact(ctx, email="ada@drone.co")
    m = msg("From: stranger@nowhere.com\nSubject: hi")
    assert inbox._match_contact(ctx, m, "stranger@nowhere.com", "reply", "x") is None


def test_from_contact_precedence_over_thread(ctx):
    # If a known contact (Bob) replies on Ada's thread, attribute it to Bob,
    # the actual sender — not to Ada just because the thread matches.
    a = add_contact(ctx, email="ada@drone.co")
    b = add_contact(ctx, email="bob@other.co", company="Other")
    ctx.db.record_message(a, campaign="c", template="warm", step=0, subject="s",
                          body="b", message_id="<a-thread@me.com>", status="sent")
    m = msg("From: bob@other.co\nSubject: Re: s\nIn-Reply-To: <a-thread@me.com>")
    found = inbox._match_contact(ctx, m, "bob@other.co", "reply", "x")
    assert found["id"] == b


def test_match_bounce_via_full_dsn_body(ctx):
    # Standard DSN: failed recipient lives in the embedded original message,
    # not the human-readable text/plain notice.
    cid = add_contact(ctx, email="ada@drone.co")
    raw = (
        "From: mailer-daemon@google.com\nSubject: Delivery Status Notification (Failure)\n"
        'MIME-Version: 1.0\nContent-Type: multipart/report; report-type=delivery-status; boundary="B"\n\n'
        "--B\nContent-Type: text/plain\n\nYour message could not be delivered.\n"
        "--B\nContent-Type: message/delivery-status\n\n"
        "Final-Recipient: rfc822; ada@drone.co\nAction: failed\nStatus: 5.1.1\n"
        "--B--\n"
    )
    m = message_from_string(raw)
    found = inbox._match_contact(ctx, m, "mailer-daemon@google.com", "bounce", "Your message could not be delivered.")
    assert found is not None and found["id"] == cid


def test_get_text_body_html_only():
    raw = (
        "From: a@b.co\nSubject: hi\nMIME-Version: 1.0\n"
        'Content-Type: multipart/alternative; boundary="BB"\n\n'
        "--BB\nContent-Type: text/html\n\n<p>Hello <b>world</b></p>\n--BB--\n"
    )
    m = message_from_string(raw)
    assert "Hello world" in inbox._get_text_body(m)


def test_get_text_body_multipart():
    raw = (
        "From: a@b.co\nSubject: hi\nMIME-Version: 1.0\n"
        'Content-Type: multipart/alternative; boundary="BB"\n\n'
        "--BB\nContent-Type: text/plain\n\nplain body here\n"
        "--BB\nContent-Type: text/html\n\n<p>html body</p>\n--BB--\n"
    )
    m = message_from_string(raw)
    assert "plain body here" in inbox._get_text_body(m)
