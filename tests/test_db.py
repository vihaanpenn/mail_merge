import json

import pytest

from mailmerge.db import (
    STATUS_BOUNCED,
    STATUS_CONTACTED,
    STATUS_NEW,
    STATUS_REPLIED,
    Database,
)
from tests.conftest import add_contact
from tests.fakedata import CONTACTS, seed_contacts


# -- upsert ----------------------------------------------------------------

def test_upsert_insert_returns_created(ctx):
    cid, created = ctx.db.upsert_contact({"email": "a@b.co", "company": "B Co"})
    assert created is True and cid > 0


def test_upsert_match_on_normalized_email(ctx):
    cid, _ = ctx.db.upsert_contact({"email": "a@b.co", "company": "B Co"})
    cid2, created2 = ctx.db.upsert_contact({"email": "A@B.CO", "title": "CTO"})
    assert cid == cid2 and created2 is False
    row = ctx.db.get_contact(cid)
    assert row["company"] == "B Co" and row["title"] == "CTO"


def test_upsert_requires_email(ctx):
    with pytest.raises(ValueError):
        ctx.db.upsert_contact({"company": "No Email"})


def test_upsert_does_not_blank_existing(ctx):
    cid = add_contact(ctx, email="x@y.co", personalization="original hook")
    ctx.db.upsert_contact({"email": "x@y.co", "personalization": "", "title": "New Title"})
    row = ctx.db.get_contact(cid)
    assert row["personalization"] == "original hook"
    assert row["title"] == "New Title"


def test_upsert_merges_extra(ctx):
    ctx.db.upsert_contact({"email": "x@y.co", "extra": {"region": "Bay Area"}})
    ctx.db.upsert_contact({"email": "x@y.co", "extra": {"stage": "Series A"}})
    extra = json.loads(ctx.db.get_contact_by_email("x@y.co")["extra"])
    assert extra == {"region": "Bay Area", "stage": "Series A"}


def test_upsert_extra_accepts_json_string(ctx):
    ctx.db.upsert_contact({"email": "x@y.co", "extra": json.dumps({"region": "SoCal"})})
    assert json.loads(ctx.db.get_contact_by_email("x@y.co")["extra"]) == {"region": "SoCal"}


def test_empty_extra_keys_not_stored(ctx):
    ctx.db.upsert_contact({"email": "z@z.co", "extra": {"region": "", "stage": "Series A"}})
    assert json.loads(ctx.db.get_contact_by_email("z@z.co")["extra"]) == {"stage": "Series A"}


def test_upsert_sets_timestamps(ctx):
    cid = add_contact(ctx)
    row = ctx.db.get_contact(cid)
    assert row["created_at"] and row["updated_at"]


# -- lookups & filters -----------------------------------------------------

def test_seed_and_count(ctx):
    seed_contacts(ctx.db)
    assert ctx.db.totals()["contacts"] == len(CONTACTS)


def test_get_missing_returns_none(ctx):
    assert ctx.db.get_contact(9999) is None
    assert ctx.db.get_contact_by_email("nobody@nowhere.com") is None


def test_list_filter_status(ctx):
    add_contact(ctx, email="a@a.co", status=STATUS_NEW)
    add_contact(ctx, email="b@b.co", status=STATUS_CONTACTED)
    assert len(ctx.db.list_contacts(status=STATUS_NEW)) == 1
    assert len(ctx.db.list_contacts(status=STATUS_CONTACTED)) == 1


@pytest.mark.parametrize("term,n", [
    ("Nimbus", 1),
    ("robotics", 3),   # Nimbus Robotics, Marine Robotics, + Orbit Edge ("...for robotics")
    ("zzz", 0),
])
def test_list_search(ctx, term, n):
    seed_contacts(ctx.db)
    assert len(ctx.db.list_contacts(search=term)) == n


def test_list_search_matches_personalization(ctx):
    add_contact(ctx, email="p@p.co", personalization="quantum widgets")
    assert len(ctx.db.list_contacts(search="quantum")) == 1


def test_tag_match_is_anchored(ctx):
    cid = add_contact(ctx)
    ctx.db.add_tag(cid, "vip")
    assert len(ctx.db.list_contacts(tag="vip")) == 1
    assert len(ctx.db.list_contacts(tag="vi")) == 0
    assert len(ctx.db.list_contacts(tag="iplead")) == 0


def test_search_escapes_like_wildcards(ctx):
    add_contact(ctx, email="a@a.co", company="Alpha")
    assert len(ctx.db.list_contacts(search="Alpha")) == 1
    assert len(ctx.db.list_contacts(search="Al_ha")) == 0
    assert len(ctx.db.list_contacts(search="Alph%")) == 0


def test_list_limit(ctx):
    seed_contacts(ctx.db)
    assert len(ctx.db.list_contacts(limit=3)) == 3


def test_limit_zero_returns_empty(ctx):
    add_contact(ctx)
    assert ctx.db.list_contacts(limit=0) == []


def test_list_combined_filters(ctx):
    a = add_contact(ctx, email="a@a.co", company="Alpha", status=STATUS_CONTACTED)
    ctx.db.add_tag(a, "vip")
    add_contact(ctx, email="b@b.co", company="Beta", status=STATUS_CONTACTED)
    rows = ctx.db.list_contacts(status=STATUS_CONTACTED, tag="vip")
    assert len(rows) == 1 and rows[0]["email"] == "a@a.co"


# -- mutation helpers ------------------------------------------------------

def test_update_contact_bumps_updated_at(ctx):
    cid = add_contact(ctx)
    before = ctx.db.get_contact(cid)["updated_at"]
    ctx.db.update_contact(cid, title="New Title")
    assert ctx.db.get_contact(cid)["title"] == "New Title"
    assert ctx.db.get_contact(cid)["updated_at"] >= before


def test_update_contact_noop(ctx):
    cid = add_contact(ctx)
    ctx.db.update_contact(cid)  # no fields -> should not raise
    assert ctx.db.get_contact(cid) is not None


def test_set_status(ctx):
    cid = add_contact(ctx)
    ctx.db.set_status(cid, "do_not_contact")
    assert ctx.db.get_contact(cid)["status"] == "do_not_contact"


def test_add_tag_dedupes(ctx):
    cid = add_contact(ctx)
    ctx.db.add_tag(cid, "priority")
    ctx.db.add_tag(cid, "priority")
    ctx.db.add_tag(cid, "warm-intro")
    assert ctx.db.get_contact(cid)["tags"] == "priority,warm-intro"


def test_add_tag_missing_contact_noop(ctx):
    ctx.db.add_tag(9999, "ghost")  # should silently no-op


# -- messages --------------------------------------------------------------

def test_record_message_advances_lifecycle(ctx):
    cid = add_contact(ctx)
    ctx.db.record_message(cid, campaign="c", template="warm", step=0, subject="s",
                          body="b", message_id="<1@x>", status="sent")
    row = ctx.db.get_contact(cid)
    assert row["status"] == STATUS_CONTACTED
    assert row["last_step"] == 0 and row["last_contacted_at"]


def test_record_message_error_does_not_advance(ctx):
    cid = add_contact(ctx)
    ctx.db.record_message(cid, campaign="c", template="warm", step=0, subject="s",
                          body="b", message_id=None, status="error", error="boom")
    row = ctx.db.get_contact(cid)
    assert row["status"] == STATUS_NEW and row["last_step"] == -1


def test_record_message_keeps_replied_status(ctx):
    cid = add_contact(ctx)
    ctx.db.mark_replied(cid)
    ctx.db.record_message(cid, campaign="c", template="warm", step=1, subject="s",
                          body="b", message_id="<2@x>", status="sent")
    # a 'replied' contact should not be downgraded back to 'contacted'
    assert ctx.db.get_contact(cid)["status"] == STATUS_REPLIED


def test_list_messages_for_contact(ctx):
    cid = add_contact(ctx)
    for i in range(3):
        ctx.db.record_message(cid, campaign="c", template="warm", step=i, subject="s",
                              body="b", message_id=f"<{i}@x>", status="sent")
    assert len(ctx.db.list_messages(cid)) == 3
    assert len(ctx.db.list_messages()) == 3


def test_count_followups_sent_excludes_initial(ctx):
    cid = add_contact(ctx)
    ctx.db.record_message(cid, campaign="c", template="warm", step=0, subject="s",
                          body="b", message_id="<0@x>", status="sent")
    ctx.db.record_message(cid, campaign="c", template="followup1", step=1, subject="s",
                          body="b", message_id="<1@x>", status="sent")
    ctx.db.record_message(cid, campaign="c", template="followup2", step=2, subject="s",
                          body="b", message_id="<2@x>", status="error")
    assert ctx.db.count_followups_sent(cid) == 1  # only sent step>=1


def test_find_contact_by_message_id(ctx):
    cid = add_contact(ctx)
    ctx.db.record_message(cid, campaign="c", template="warm", step=0, subject="s",
                          body="b", message_id="<abc@example.com>", status="sent")
    assert ctx.db.find_contact_by_message_id("<abc@example.com>")["id"] == cid
    assert ctx.db.find_contact_by_message_id("<nope@x>") is None
    assert ctx.db.find_contact_by_message_id("") is None


def test_sent_today(ctx):
    cid = add_contact(ctx)
    ctx.db.record_message(cid, campaign="c", template="warm", step=0, subject="s",
                          body="b", message_id="<1@x>", status="sent")
    assert ctx.db.sent_today() == 1


def test_sent_today_ignores_other_days(ctx):
    cid = add_contact(ctx)
    ctx.db.record_message(cid, campaign="c", template="warm", step=0, subject="s",
                          body="b", message_id="<1@x>", status="sent")
    ctx.db.conn.execute("UPDATE messages SET sent_at='2020-01-01T09:00:00'")
    ctx.db.conn.commit()
    assert ctx.db.sent_today() == 0


# -- replies & lifecycle ---------------------------------------------------

def test_mark_replied(ctx):
    cid = add_contact(ctx)
    ctx.db.mark_replied(cid)
    row = ctx.db.get_contact(cid)
    assert row["status"] == STATUS_REPLIED and row["replied_at"]


def test_mark_bounced(ctx):
    cid = add_contact(ctx)
    ctx.db.mark_bounced(cid)
    row = ctx.db.get_contact(cid)
    assert row["status"] == STATUS_BOUNCED and row["bounced_at"]


def test_mark_bounced_does_not_downgrade_replied(ctx):
    cid = add_contact(ctx)
    ctx.db.mark_replied(cid)
    ctx.db.mark_bounced(cid)
    row = ctx.db.get_contact(cid)
    assert row["status"] == STATUS_REPLIED and row["bounced_at"] is not None


def test_record_reply_and_list(ctx):
    cid = add_contact(ctx)
    ctx.db.record_reply(contact_id=cid, uid="INBOX:1:1", message_id="<r@x>",
                        in_reply_to="<s@x>", from_addr="ada@drone.co", subject="Re: hi",
                        snippet="thanks", classification="reply", received_at=None)
    assert len(ctx.db.list_replies()) == 1
    assert len(ctx.db.list_replies(classification="reply")) == 1
    assert len(ctx.db.list_replies(classification="bounce")) == 0


def test_reply_uid_dedup(ctx):
    cid = add_contact(ctx)
    args = dict(contact_id=cid, uid="INBOX:1:1", message_id="<r@x>", in_reply_to=None,
                from_addr="ada@drone.co", subject="Re", snippet="x",
                classification="reply", received_at=None)
    ctx.db.record_reply(**args)
    ctx.db.record_reply(**args)  # ignored on UNIQUE(uid)
    assert ctx.db.reply_uid_seen("INBOX:1:1") is True
    assert ctx.db.reply_uid_seen("INBOX:1:2") is False
    assert len(ctx.db.list_replies()) == 1


# -- aggregates ------------------------------------------------------------

def test_status_counts(ctx):
    add_contact(ctx, email="a@a.co", status=STATUS_NEW)
    add_contact(ctx, email="b@b.co", status=STATUS_CONTACTED)
    counts = ctx.db.status_counts()
    assert counts[STATUS_NEW] == 1 and counts[STATUS_CONTACTED] == 1


def test_totals_shape(ctx):
    seed_contacts(ctx.db)
    totals = ctx.db.totals()
    for key in ("contacts", "messages_sent", "messages_error", "replies",
                "auto_replies", "bounces", "sent_today"):
        assert key in totals


# -- meta & schema ---------------------------------------------------------

def test_meta_roundtrip(ctx):
    ctx.db.set_meta("foo", "bar")
    assert ctx.db.get_meta("foo") == "bar"
    assert ctx.db.get_meta("missing", "default") == "default"


def test_schema_version_recorded(ctx):
    assert ctx.db.get_meta("schema_version") is not None


def test_reopen_database_persists(ctx, base_dir):
    cid = add_contact(ctx)
    ctx.db.close()
    db2 = Database(base_dir / "data" / "test.db")
    assert db2.get_contact(cid) is not None
    db2.close()
