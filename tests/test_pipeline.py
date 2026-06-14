import datetime as dt

from mailmerge.pipeline import (
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


def test_select_initial_confidence_filter(ctx):
    add_contact(ctx, email="hi@a.co", confidence="High")
    add_contact(ctx, email="lo@a.co", confidence="Low")
    add_contact(ctx, email="blank@a.co", confidence="")
    selected = select_initial(ctx)  # min_confidence defaults to Medium
    emails = {c["email"] for c in selected}
    assert emails == {"hi@a.co"}


def test_prepare_jobs_skips_missing_required(ctx):
    cid = add_contact(ctx, personalization="")
    contact = ctx.db.get_contact(cid)
    jobs, skipped = prepare_jobs(ctx, [(contact, 0, "warm")], check_mx=False)
    assert jobs == []
    assert any("personalization" in reason for _, reason in skipped)


def test_prepare_jobs_dedupes_within_run(ctx):
    c1 = add_contact(ctx, email="dup@a.co")
    # second contact, same address is impossible (unique), so simulate via two rows
    contact = ctx.db.get_contact(c1)
    jobs, skipped = prepare_jobs(
        ctx, [(contact, 0, "warm"), (contact, 0, "warm")], check_mx=False
    )
    assert len(jobs) == 1
    assert any("duplicate" in r for _, r in skipped)


def test_initial_jobs_render(ctx):
    add_contact(ctx)
    jobs, skipped = initial_jobs(ctx, template="warm", check_mx=False)
    assert len(jobs) == 1
    assert jobs[0].subject and "{" not in jobs[0].subject


def test_select_followups_due_after_wait(ctx):
    cid = add_contact(ctx)
    # Simulate the initial send, then backdate so step 1 (wait 4d) is due.
    ctx.db.record_message(cid, campaign="c", template="warm", step=0,
                          subject="s", body="b", message_id="<1@x>", status="sent")
    backdate(ctx, cid, 5)
    due = select_followups(ctx)
    assert len(due) == 1
    contact, step_cfg = due[0]
    assert step_cfg["step"] == 1


def test_select_followups_not_due_too_soon(ctx):
    cid = add_contact(ctx)
    ctx.db.record_message(cid, campaign="c", template="warm", step=0,
                          subject="s", body="b", message_id="<1@x>", status="sent")
    backdate(ctx, cid, 1)  # only 1 day; step 1 needs 4
    assert select_followups(ctx) == []
    # ...but --force overrides the wait.
    assert len(select_followups(ctx, force=True)) == 1


def test_followups_skip_replied(ctx):
    cid = add_contact(ctx)
    ctx.db.record_message(cid, campaign="c", template="warm", step=0,
                          subject="s", body="b", message_id="<1@x>", status="sent")
    backdate(ctx, cid, 10)
    ctx.db.mark_replied(cid)
    assert select_followups(ctx) == []  # never chase someone who replied


def test_followup_progression_step2(ctx):
    cid = add_contact(ctx)
    # already sent initial + followup1; now step 2 should be due
    ctx.db.record_message(cid, campaign="c", template="warm", step=0,
                          subject="s", body="b", message_id="<1@x>", status="sent")
    ctx.db.record_message(cid, campaign="c", template="followup1", step=1,
                          subject="s", body="b", message_id="<2@x>", status="sent")
    backdate(ctx, cid, 10)
    due = select_followups(ctx)
    assert len(due) == 1
    assert due[0][1]["step"] == 2
    # render the follow-up job
    jobs, _ = followup_jobs(ctx, check_mx=False)
    assert jobs and jobs[0].step == 2 and jobs[0].template == "followup2"


def test_followup_exhausted_after_last_step(ctx):
    cid = add_contact(ctx)
    for step, tpl in [(0, "warm"), (1, "followup1"), (2, "followup2")]:
        ctx.db.record_message(cid, campaign="c", template=tpl, step=step,
                              subject="s", body="b", message_id=f"<{step}@x>", status="sent")
    backdate(ctx, cid, 30)
    assert select_followups(ctx) == []  # no step 3 configured
