"""Human-readable status dashboard and per-contact history."""

from __future__ import annotations

from .context import Context
from .pipeline import select_followups, select_initial
from .utils import render_table, truncate


def _pct(part: int, whole: int) -> str:
    return f"{(100.0 * part / whole):.1f}%" if whole else "—"


def status_report(ctx: Context) -> str:
    db = ctx.db
    totals = db.totals()
    counts = db.status_counts()
    sent = totals["messages_sent"]
    replies = totals["replies"]

    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("MAILMERGE STATUS")
    lines.append("=" * 60)
    lines.append(f"Database          : {ctx.db_path}")
    lines.append(f"Contacts          : {totals['contacts']}")
    lines.append("")

    status_rows = [[s, n] for s, n in sorted(counts.items())]
    lines.append("By status:")
    lines.append(render_table(["status", "count"], status_rows))
    lines.append("")

    lines.append("Activity:")
    activity = [
        ["emails sent (all time)", sent],
        ["sent today", totals["sent_today"]],
        ["send errors", totals["messages_error"]],
        ["genuine replies", f"{replies}  ({_pct(replies, sent)} of sent)"],
        ["auto-replies / OOO", totals["auto_replies"]],
        ["bounces", f"{totals['bounces']}  ({_pct(totals['bounces'], sent)} of sent)"],
    ]
    lines.append(render_table(["metric", "value"], activity))
    lines.append("")

    eligible = len(select_initial(ctx))
    due = len(select_followups(ctx))
    lines.append("Queue:")
    lines.append(render_table(
        ["metric", "value"],
        [["new contacts ready for first email", eligible],
         ["follow-ups due now", due]],
    ))
    lines.append("=" * 60)
    return "\n".join(lines)


def contact_detail(ctx: Context, contact_id: int) -> str:
    db = ctx.db
    c = db.get_contact(contact_id)
    if c is None:
        return f"No contact with id {contact_id}."

    lines = [
        "=" * 60,
        f"[{c['id']}] {c['full_name'] or '(no name)'} — {c['company']}",
        "=" * 60,
        f"Email       : {c['email']}",
        f"Title       : {c['title']}",
        f"Status      : {c['status']}",
        f"Confidence  : {c['confidence']}",
        f"Last step   : {c['last_step']}  (last contacted {c['last_contacted_at'] or 'never'})",
        f"Tags        : {c['tags'] or '—'}",
        f"Personalize : {c['personalization'] or '—'}",
        f"Notes       : {c['notes'] or '—'}",
        "",
    ]

    messages = db.list_messages(contact_id)
    lines.append(f"Outbound ({len(messages)}):")
    if messages:
        lines.append(render_table(
            ["when", "step", "template", "status", "subject"],
            [[m["sent_at"], m["step"], m["template"], m["status"], truncate(m["subject"], 40)]
             for m in messages],
        ))
    else:
        lines.append("  (none)")
    lines.append("")

    replies = [r for r in db.list_replies() if r["contact_id"] == contact_id]
    lines.append(f"Inbound ({len(replies)}):")
    if replies:
        lines.append(render_table(
            ["when", "type", "subject", "snippet"],
            [[r["received_at"], r["classification"], truncate(r["subject"], 30),
              truncate(r["snippet"], 40)] for r in replies],
        ))
    else:
        lines.append("  (none)")
    lines.append("=" * 60)
    return "\n".join(lines)
