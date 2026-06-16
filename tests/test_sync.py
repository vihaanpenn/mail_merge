import pytest

from mailmerge import inbox
from tests.conftest import add_contact
from tests.fakedata import FakeIMAP, make_dsn, make_raw, seed_contacts


def install_inbox(ctx, monkeypatch, messages, uidvalidity="999001"):
    fake = FakeIMAP(messages, uidvalidity=uidvalidity)
    monkeypatch.setattr(inbox, "imap_connect", lambda c: fake)
    return fake


def test_sync_disabled_exits(ctx):
    ctx.cfg["imap"]["enabled"] = False
    with pytest.raises(SystemExit):
        inbox.sync(ctx)


def test_sync_classifies_and_updates(ctx, monkeypatch):
    seed_contacts(ctx.db)
    ada = ctx.db.get_contact_by_email("ada@nimbusrobotics.io")
    ctx.db.record_message(ada["id"], campaign="c", template="warm", step=0, subject="s",
                          body="b", message_id="<sent-ada@me.com>", status="sent")

    messages = [
        ("101", make_raw("ada@nimbusrobotics.io", "Re: s", "yes, let's talk!",
                         headers={"In-Reply-To": "<sent-ada@me.com>"})),
        ("102", make_dsn("grace@heliosdrones.com")),
        ("103", make_raw("alan@atlasiot.dev", "Automatic reply",
                         headers={"Auto-Submitted": "auto-replied"})),
        ("104", make_raw("stranger@nowhere.com", "Re: something random")),
    ]
    fake = install_inbox(ctx, monkeypatch, messages)

    result = inbox.sync(ctx)
    assert result.scanned == 4
    assert result.new == 3
    assert result.replies == 1
    assert result.bounces == 1
    assert result.auto_replies == 1
    assert result.unmatched == 1
    assert fake.logged_out is True

    assert ctx.db.get_contact_by_email("ada@nimbusrobotics.io")["status"] == "replied"
    assert ctx.db.get_contact_by_email("grace@heliosdrones.com")["status"] == "bounced"
    assert ctx.db.get_contact_by_email("alan@atlasiot.dev")["status"] == "new"  # auto-reply: unchanged


def test_sync_is_idempotent(ctx, monkeypatch):
    seed_contacts(ctx.db)
    messages = [("201", make_raw("ada@nimbusrobotics.io", "Re: hi", "thanks"))]
    install_inbox(ctx, monkeypatch, messages)
    first = inbox.sync(ctx)
    assert first.new == 1
    second = inbox.sync(ctx)
    assert second.new == 0  # uid already recorded


def test_sync_uidvalidity_in_key(ctx, monkeypatch):
    seed_contacts(ctx.db)
    messages = [("1", make_raw("ada@nimbusrobotics.io", "Re: hi", "thanks"))]
    install_inbox(ctx, monkeypatch, messages, uidvalidity="555")
    inbox.sync(ctx)
    rows = ctx.db.list_replies()
    assert rows[0]["uid"] == "INBOX:555:1"


def test_sync_threaded_bounce_phrase_stays_reply(ctx, monkeypatch):
    # regression: a genuine threaded reply quoting a bounce phrase must not bounce
    cid = add_contact(ctx, email="jane@acme.com")
    ctx.db.record_message(cid, campaign="c", template="warm", step=0, subject="s",
                          body="b", message_id="<sent-jane@me.com>", status="sent")
    messages = [
        ("301", make_raw("jane@acme.com", "Re: your question about address not found",
                        "yes I'm interested", headers={"In-Reply-To": "<sent-jane@me.com>"})),
    ]
    install_inbox(ctx, monkeypatch, messages)
    result = inbox.sync(ctx)
    assert result.replies == 1 and result.bounces == 0
    assert ctx.db.get_contact(cid)["status"] == "replied"


def test_sync_records_snippet_and_from(ctx, monkeypatch):
    seed_contacts(ctx.db)
    messages = [("401", make_raw("ada@nimbusrobotics.io", "Re: hi", "Sounds great, Tuesday works"))]
    install_inbox(ctx, monkeypatch, messages)
    inbox.sync(ctx)
    reply = ctx.db.list_replies()[0]
    assert reply["from_addr"] == "ada@nimbusrobotics.io"
    assert "Tuesday" in reply["snippet"]


def test_sync_lookback_arg_accepted(ctx, monkeypatch):
    seed_contacts(ctx.db)
    install_inbox(ctx, monkeypatch, [])
    result = inbox.sync(ctx, lookback_days=7)
    assert result.scanned == 0
