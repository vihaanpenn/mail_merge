import datetime as dt

import pytest

from mailmerge.pipeline import (
    Job,
    followup_jobs,
    initial_jobs,
    prepare_jobs,
    select_followups,
    select_initial,
)
from tests.conftest import add_contact


def backdate(ctx, cid, days):
    when = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec="seconds")
    ctx.db.update_contact(cid, last_contacted_at=when)


def send_initial(ctx, cid):
    ctx.db.record_message(cid, campaign="c", template="warm", step=0, subject="s",
                          body="b", message_id=f"<i{cid}@x>", status="sent")


# -- select_initial --------------------------------------------------------

@pytest.mark.parametrize("min_conf,expected", [
    ("Low", {"hi@a.co", "med@a.co", "lo@a.co"}),
    ("Medium", {"hi@a.co", "med@a.co"}),
    ("High", {"hi@a.co"}),
])
def test_select_initial_confidence(ctx, min_conf, expected):
    ctx.cfg["contacts"]["min_confidence"] = min_conf
    add_contact(ctx, email="hi@a.co", confidence="High")
    add_contact(ctx, email="med@a.co", confidence="Medium")
    add_contact(ctx, email="lo@a.co", confidence="Low")
    add_contact(ctx, email="blank@a.co", confidence="")
    assert {c["email"] for c in select_initial(ctx)} == expected


def test_select_initial_excludes_contacted(ctx):
    cid = add_contact(ctx, email="a@a.co")
    add_contact(ctx, email="b@b.co")
    send_initial(ctx, cid)  # a@a.co becomes contacted
    assert {c["email"] for c in select_initial(ctx)} == {"b@b.co"}


def test_select_initial_blank_confidence_excluded_at_medium(ctx):
    add_contact(ctx, email="blank@a.co", confidence="")
    assert select_initial(ctx) == []


# -- prepare_jobs ----------------------------------------------------------

def test_prepare_jobs_clean(ctx):
    cid = add_contact(ctx)
    jobs, skipped = prepare_jobs(ctx, [(ctx.db.get_contact(cid), 0, "warm")], check_mx=False)
    assert len(jobs) == 1 and skipped == []
    assert isinstance(jobs[0], Job)


def test_prepare_jobs_skips_missing_required(ctx):
    cid = add_contact(ctx, personalization="")
    jobs, skipped = prepare_jobs(ctx, [(ctx.db.get_contact(cid), 0, "warm")], check_mx=False)
    assert jobs == [] and any("personalization" in r for _, r in skipped)


def test_prepare_jobs_custom_required_fields(ctx):
    ctx.cfg["contacts"]["required_fields"] = ["first_name", "company", "personalization", "title"]
    cid = add_contact(ctx, title="")
    jobs, skipped = prepare_jobs(ctx, [(ctx.db.get_contact(cid), 0, "warm")], check_mx=False)
    assert jobs == [] and any("title" in r for _, r in skipped)


def test_prepare_jobs_skips_unknown_placeholder(base_dir, ctx):
    (base_dir / "templates" / "typo.txt").write_text(
        "Subject: hi {company}\n\n{mystery}\n", encoding="utf-8")
    cid = add_contact(ctx)
    jobs, skipped = prepare_jobs(ctx, [(ctx.db.get_contact(cid), 0, "typo")], check_mx=False)
    assert jobs == [] and any("placeholder" in r for _, r in skipped)


def test_prepare_jobs_template_error(ctx):
    cid = add_contact(ctx)
    jobs, skipped = prepare_jobs(ctx, [(ctx.db.get_contact(cid), 0, "ghost")], check_mx=False)
    assert jobs == [] and any("template" in r for _, r in skipped)


def test_prepare_jobs_dedupes_within_run(ctx):
    cid = add_contact(ctx, email="dup@a.co")
    contact = ctx.db.get_contact(cid)
    jobs, skipped = prepare_jobs(ctx, [(contact, 0, "warm"), (contact, 0, "warm")], check_mx=False)
    assert len(jobs) == 1 and any("duplicate" in r for _, r in skipped)


def test_prepare_jobs_mx_filter(ctx, monkeypatch):
    import mailmerge.pipeline as pl
    monkeypatch.setattr(pl, "dns_available", lambda: True)
    monkeypatch.setattr(pl, "address_mx_ok", lambda addr: addr.endswith("@good.co"))
    add_contact(ctx, email="ok@good.co")
    bad = add_contact(ctx, email="bad@nomx.co")
    items = [(ctx.db.get_contact(c["id"]), 0, "warm") for c in ctx.db.list_contacts()]
    jobs, skipped = prepare_jobs(ctx, items, check_mx=True)
    assert {j.email for j in jobs} == {"ok@good.co"}
    assert any("MX" in r for _, r in skipped)


def test_initial_jobs(ctx):
    add_contact(ctx)
    jobs, _ = initial_jobs(ctx, template="warm", check_mx=False)
    assert len(jobs) == 1 and "{" not in jobs[0].subject


# -- select_followups ------------------------------------------------------

def test_followup_due_after_wait(ctx):
    cid = add_contact(ctx)
    send_initial(ctx, cid)
    backdate(ctx, cid, 5)
    due = select_followups(ctx)
    assert len(due) == 1 and due[0][1]["step"] == 1


def test_followup_not_due_too_soon(ctx):
    cid = add_contact(ctx)
    send_initial(ctx, cid)
    backdate(ctx, cid, 1)
    assert select_followups(ctx) == []
    assert len(select_followups(ctx, force=True)) == 1


def test_followup_skips_replied(ctx):
    cid = add_contact(ctx)
    send_initial(ctx, cid)
    backdate(ctx, cid, 10)
    ctx.db.mark_replied(cid)
    assert select_followups(ctx) == []


def test_followup_skips_bounced(ctx):
    cid = add_contact(ctx)
    send_initial(ctx, cid)
    backdate(ctx, cid, 10)
    ctx.db.mark_bounced(cid)
    assert select_followups(ctx) == []


def test_followup_progression_step2(ctx):
    cid = add_contact(ctx)
    send_initial(ctx, cid)
    ctx.db.record_message(cid, campaign="c", template="followup1", step=1, subject="s",
                          body="b", message_id="<f1@x>", status="sent")
    backdate(ctx, cid, 10)
    due = select_followups(ctx)
    assert len(due) == 1 and due[0][1]["step"] == 2
    jobs, _ = followup_jobs(ctx, check_mx=False)
    assert jobs and jobs[0].step == 2 and jobs[0].template == "followup2"


def test_followup_exhausted(ctx):
    cid = add_contact(ctx)
    for step, tpl in [(0, "warm"), (1, "followup1"), (2, "followup2")]:
        ctx.db.record_message(cid, campaign="c", template=tpl, step=step, subject="s",
                              body="b", message_id=f"<{step}@x>", status="sent")
    backdate(ctx, cid, 30)
    assert select_followups(ctx) == []


def test_followup_disabled(ctx):
    ctx.cfg["followups"]["enabled"] = False
    cid = add_contact(ctx)
    send_initial(ctx, cid)
    backdate(ctx, cid, 30)
    assert select_followups(ctx) == []


def test_followup_no_steps_configured(ctx):
    ctx.cfg["followups"]["steps"] = []
    cid = add_contact(ctx)
    send_initial(ctx, cid)
    backdate(ctx, cid, 30)
    assert select_followups(ctx) == []


def test_followup_non_contiguous_steps(ctx):
    # steps 1 and 3 (no 2): progression must still advance by position
    ctx.cfg["followups"]["steps"] = [
        {"step": 1, "template": "followup1", "wait_days": 4},
        {"step": 3, "template": "followup2", "wait_days": 4},
    ]
    cid = add_contact(ctx)
    send_initial(ctx, cid)
    backdate(ctx, cid, 10)
    due = select_followups(ctx)
    assert due[0][1]["step"] == 1
    # after sending followup1 (recorded as step 1), the next is the step-3 entry
    ctx.db.record_message(cid, campaign="c", template="followup1", step=1, subject="s",
                          body="b", message_id="<f1@x>", status="sent")
    backdate(ctx, cid, 10)
    due2 = select_followups(ctx)
    assert due2[0][1]["step"] == 3


def test_followup_no_timestamp_only_with_force(ctx):
    # manually 'contacted' with no recorded send -> no timestamp -> only force fires
    cid = add_contact(ctx, status="contacted")
    assert select_followups(ctx) == []
    assert len(select_followups(ctx, force=True)) == 1
