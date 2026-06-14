import json

from mailmerge.db import STATUS_CONTACTED, STATUS_NEW
from tests.conftest import add_contact


def test_upsert_insert_then_update(ctx):
    cid, created = ctx.db.upsert_contact({"email": "a@b.co", "company": "B Co"})
    assert created is True
    cid2, created2 = ctx.db.upsert_contact({"email": "A@B.CO", "title": "CTO"})
    assert created2 is False
    assert cid == cid2  # matched on normalized email
    row = ctx.db.get_contact(cid)
    assert row["company"] == "B Co"  # preserved
    assert row["title"] == "CTO"     # added


def test_upsert_does_not_blank_existing(ctx):
    cid = add_contact(ctx, email="x@y.co", personalization="original hook")
    # Re-import a partial row with empty personalization must NOT erase it.
    ctx.db.upsert_contact({"email": "x@y.co", "personalization": "", "title": "New Title"})
    row = ctx.db.get_contact(cid)
    assert row["personalization"] == "original hook"
    assert row["title"] == "New Title"


def test_upsert_merges_extra(ctx):
    ctx.db.upsert_contact({"email": "x@y.co", "extra": {"region": "Bay Area"}})
    ctx.db.upsert_contact({"email": "x@y.co", "extra": {"stage": "Series A"}})
    row = ctx.db.get_contact_by_email("x@y.co")
    extra = json.loads(row["extra"])
    assert extra == {"region": "Bay Area", "stage": "Series A"}


def test_record_message_advances_lifecycle(ctx):
    cid = add_contact(ctx)
    assert ctx.db.get_contact(cid)["status"] == STATUS_NEW
    ctx.db.record_message(
        cid, campaign="c", template="warm", step=0, subject="s", body="b",
        message_id="<1@x>", status="sent",
    )
    row = ctx.db.get_contact(cid)
    assert row["status"] == STATUS_CONTACTED
    assert row["last_step"] == 0
    assert row["last_contacted_at"] is not None


def test_record_message_error_does_not_advance(ctx):
    cid = add_contact(ctx)
    ctx.db.record_message(
        cid, campaign="c", template="warm", step=0, subject="s", body="b",
        message_id=None, status="error", error="boom",
    )
    row = ctx.db.get_contact(cid)
    assert row["status"] == STATUS_NEW
    assert row["last_step"] == -1


def test_find_contact_by_message_id(ctx):
    cid = add_contact(ctx)
    ctx.db.record_message(
        cid, campaign="c", template="warm", step=0, subject="s", body="b",
        message_id="<abc@example.com>", status="sent",
    )
    found = ctx.db.find_contact_by_message_id("<abc@example.com>")
    assert found is not None and found["id"] == cid
    assert ctx.db.find_contact_by_message_id("<nope@x>") is None


def test_sent_today_and_totals(ctx):
    cid = add_contact(ctx)
    ctx.db.record_message(cid, campaign="c", template="warm", step=0,
                          subject="s", body="b", message_id="<1@x>", status="sent")
    assert ctx.db.sent_today() == 1
    totals = ctx.db.totals()
    assert totals["messages_sent"] == 1
    assert totals["contacts"] == 1


def test_mark_replied_and_bounced(ctx):
    cid = add_contact(ctx)
    ctx.db.mark_replied(cid)
    assert ctx.db.get_contact(cid)["status"] == "replied"
    cid2 = add_contact(ctx, email="b@b.co")
    ctx.db.mark_bounced(cid2)
    assert ctx.db.get_contact(cid2)["status"] == "bounced"


def test_list_contacts_filters(ctx):
    add_contact(ctx, email="a@a.co", company="Alpha", status=STATUS_NEW)
    add_contact(ctx, email="b@b.co", company="Beta", status=STATUS_CONTACTED)
    assert len(ctx.db.list_contacts(status=STATUS_NEW)) == 1
    assert len(ctx.db.list_contacts(search="Beta")) == 1
    assert len(ctx.db.list_contacts()) == 2


def test_add_tag_dedupes(ctx):
    cid = add_contact(ctx)
    ctx.db.add_tag(cid, "priority")
    ctx.db.add_tag(cid, "priority")
    ctx.db.add_tag(cid, "warm-intro")
    assert ctx.db.get_contact(cid)["tags"] == "priority,warm-intro"


def test_mark_bounced_does_not_downgrade_replied(ctx):
    cid = add_contact(ctx)
    ctx.db.mark_replied(cid)
    ctx.db.mark_bounced(cid)
    row = ctx.db.get_contact(cid)
    assert row["status"] == "replied"        # not clobbered
    assert row["bounced_at"] is not None     # but the bounce is still recorded


def test_tag_match_is_anchored(ctx):
    cid = add_contact(ctx)
    ctx.db.add_tag(cid, "vip")
    assert len(ctx.db.list_contacts(tag="vip")) == 1
    assert len(ctx.db.list_contacts(tag="vi")) == 0   # substring must not match


def test_search_escapes_like_wildcards(ctx):
    add_contact(ctx, email="a@a.co", company="Alpha")
    assert len(ctx.db.list_contacts(search="Alpha")) == 1
    assert len(ctx.db.list_contacts(search="Al_ha")) == 0  # _ is literal, not wildcard


def test_limit_zero_returns_empty(ctx):
    add_contact(ctx)
    assert ctx.db.list_contacts(limit=0) == []


def test_empty_extra_keys_not_stored(ctx):
    ctx.db.upsert_contact({"email": "z@z.co", "extra": {"region": "", "stage": "Series A"}})
    extra = json.loads(ctx.db.get_contact_by_email("z@z.co")["extra"])
    assert extra == {"stage": "Series A"}


def test_count_followups_sent(ctx):
    cid = add_contact(ctx)
    ctx.db.record_message(cid, campaign="c", template="warm", step=0, subject="s",
                          body="b", message_id="<0@x>", status="sent")
    ctx.db.record_message(cid, campaign="c", template="followup1", step=1, subject="s",
                          body="b", message_id="<1@x>", status="sent")
    assert ctx.db.count_followups_sent(cid) == 1  # step 0 (initial) excluded
