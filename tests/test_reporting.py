from mailmerge.reporting import _pct, contact_detail, status_report
from tests.conftest import add_contact
from tests.fakedata import seed_contacts


def test_pct():
    assert _pct(1, 4) == "25.0%"
    assert _pct(0, 0) == "—"
    assert _pct(3, 3) == "100.0%"


def test_status_report_empty(ctx):
    out = status_report(ctx)
    assert "MAILMERGE STATUS" in out
    assert "Contacts" in out


def test_status_report_with_data(ctx):
    seed_contacts(ctx.db)
    out = status_report(ctx)
    assert "By status:" in out
    assert "Activity:" in out
    assert "Queue:" in out


def test_status_report_reply_rate(ctx):
    cid = add_contact(ctx)
    ctx.db.record_message(cid, campaign="c", template="warm", step=0, subject="s",
                          body="b", message_id="<1@x>", status="sent")
    ctx.db.record_reply(contact_id=cid, uid="INBOX:1:1", message_id="<r@x>", in_reply_to=None,
                        from_addr="ada@drone.co", subject="Re", snippet="thx",
                        classification="reply", received_at=None)
    out = status_report(ctx)
    assert "genuine replies" in out


def test_contact_detail_missing(ctx):
    assert "No contact with id" in contact_detail(ctx, 9999)


def test_contact_detail_basic(ctx):
    cid = add_contact(ctx)
    out = contact_detail(ctx, cid)
    assert "Ada Lovelace" in out and "Drone Co" in out
    assert "Outbound (0)" in out and "Inbound (0)" in out


def test_contact_detail_with_history(ctx):
    cid = add_contact(ctx)
    ctx.db.record_message(cid, campaign="c", template="warm", step=0, subject="Hi there",
                          body="b", message_id="<1@x>", status="sent")
    ctx.db.record_reply(contact_id=cid, uid="INBOX:1:1", message_id="<r@x>", in_reply_to=None,
                        from_addr="ada@drone.co", subject="Re: Hi", snippet="thanks!",
                        classification="reply", received_at="2025-06-09T10:00:00")
    out = contact_detail(ctx, cid)
    assert "Outbound (1)" in out and "Inbound (1)" in out
    assert "thanks!" in out
