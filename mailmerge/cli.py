"""Command-line interface for mailmerge.

Run ``mailmerge --help`` or ``python -m mailmerge --help`` to see commands.
The default flow is: init -> import -> validate -> preview -> test -> send,
then sync / followup / status on an ongoing basis.
"""

from __future__ import annotations

import argparse
import csv
import glob
import shutil
import sys
from pathlib import Path

from . import __version__
from .context import Context
from .contacts import import_file
from .pipeline import Job, followup_jobs, initial_jobs, prepare_jobs, select_followups
from .reporting import contact_detail, status_report
from .templates import list_templates
from .utils import render_table, slug, truncate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _confirm_send() -> bool:
    try:
        return input("Type SEND to send these emails (anything else cancels): ").strip() == "SEND"
    except EOFError:
        return False


def _effective_cap(ctx: Context, override: int | None) -> tuple[int, int]:
    """Return (configured_cap, remaining_today) after subtracting today's sends."""
    cap = override if override is not None else int(ctx.cfg["sending"]["daily_cap"])
    remaining = max(0, cap - ctx.db.sent_today())
    return cap, remaining


def _template(ctx: Context, override: str | None) -> str:
    return override or ctx.cfg["sending"]["default_template"]


def _campaign(ctx: Context, override: str | None) -> str:
    return override or ctx.cfg["sending"].get("campaign", "default")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init(ctx: Context, args: argparse.Namespace) -> int:
    base = ctx.base_dir
    for d in ("data", "output/preview", "resume", "templates"):
        (base / d).mkdir(parents=True, exist_ok=True)

    cfg_path = ctx.resolve(args.config)
    example = base / "config.example.yaml"
    if not cfg_path.exists() and example.exists():
        shutil.copy(example, cfg_path)
        print(f"Created {cfg_path} (from config.example.yaml) — edit your name/email/paths.")
    elif cfg_path.exists():
        print(f"Config already present: {cfg_path}")
    else:
        print("No config.example.yaml found; running on built-in defaults.")

    print(f"Database ready: {ctx.db_path}")
    print("\nNext:")
    print("  1. edit config.yaml (sender, paths)")
    print("  2. export EMAIL_APP_PASSWORD=...   (Gmail App Password)")
    print("  3. mailmerge import data/contacts.csv")
    print("  4. mailmerge validate && mailmerge preview")
    return 0


def cmd_import(ctx: Context, args: argparse.Namespace) -> int:
    path = ctx.resolve(args.path or ctx.cfg["contacts"]["path"])
    if not path.exists():
        print(f"Contacts file not found: {path}")
        return 1
    columns = ctx.cfg["contacts"]["columns"]
    result = import_file(ctx.db, path, columns, source=args.source or "")

    print(f"Imported from {path.name}:")
    print(f"  added   : {result.added}")
    print(f"  updated : {result.updated}")
    print(f"  skipped : {result.skipped}")
    for ident, reason in result.skip_reasons[:15]:
        print(f"    - [{ident}] {reason}")
    if len(result.skip_reasons) > 15:
        print(f"    ... and {len(result.skip_reasons) - 15} more")
    print(f"\nDatabase now holds {ctx.db.totals()['contacts']} contacts.")
    return 0


def cmd_contacts(ctx: Context, args: argparse.Namespace) -> int:
    rows = ctx.db.list_contacts(
        status=args.status, search=args.search, tag=args.tag, limit=args.limit
    )
    if not rows:
        print("No matching contacts.")
        return 0
    table = render_table(
        ["id", "company", "name", "email", "status", "step", "last contacted"],
        [[r["id"], truncate(r["company"], 24), truncate(r["full_name"], 20),
          truncate(r["email"], 30), r["status"], r["last_step"],
          (r["last_contacted_at"] or "")[:10]] for r in rows],
    )
    print(table)
    print(f"\n{len(rows)} contact(s).")
    return 0


def cmd_show(ctx: Context, args: argparse.Namespace) -> int:
    print(contact_detail(ctx, args.id))
    return 0 if ctx.db.get_contact(args.id) is not None else 1


def cmd_set(ctx: Context, args: argparse.Namespace) -> int:
    contact = ctx.db.get_contact(args.id)
    if contact is None:
        print(f"No contact with id {args.id}.")
        return 1

    updates: dict[str, str] = {}
    if args.status:
        updates["status"] = args.status
    if args.personalization is not None:
        updates["personalization"] = args.personalization
    if args.note is not None:
        updates["notes"] = args.note
    for field in ("company", "title", "full_name", "confidence", "email"):
        value = getattr(args, field, None)
        if value is not None:
            updates[field] = value

    if updates:
        ctx.db.update_contact(args.id, **updates)
    if args.tag:
        ctx.db.add_tag(args.id, args.tag)
    if not updates and not args.tag:
        print("Nothing to change. Pass --status / --tag / --note / --personalization / etc.")
        return 1

    print(contact_detail(ctx, args.id))
    return 0


def cmd_validate(ctx: Context, args: argparse.Namespace) -> int:
    from .db import STATUS_NEW
    from .pipeline import select_initial

    template = _template(ctx, args.template)
    jobs, skipped = initial_jobs(ctx, template=template, check_mx=False)
    counts = ctx.db.status_counts()
    new_total = counts.get(STATUS_NEW, 0)
    # New contacts dropped before rendering because they're below min_confidence.
    below_conf = new_total - len(select_initial(ctx))

    print("=== CONTACT POOL VALIDATION ===")
    print(f"Template under test : {template}")
    print(f"Total contacts      : {ctx.db.totals()['contacts']}")
    print("Status breakdown    : " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"min_confidence      : {ctx.cfg['contacts']['min_confidence']}")
    print(f"required_fields     : {', '.join(ctx.cfg['contacts']['required_fields'])}")
    print(f"--> below confidence: {below_conf} (excluded before render)")
    print(f"--> would SEND (new): {len(jobs)}")
    print(f"--> would SKIP      : {len(skipped)}")
    if skipped:
        print("\nSkip reasons (first 25):")
        for ident, reason in skipped[:25]:
            print(f"  - [{ident}] {reason}")
        if len(skipped) > 25:
            print(f"  ... and {len(skipped) - 25} more")
    due = len(select_followups(ctx))
    print(f"\nFollow-ups due now  : {due}")
    return 0


def cmd_preview(ctx: Context, args: argparse.Namespace) -> int:
    template = _template(ctx, args.template)
    jobs, skipped = initial_jobs(ctx, template=template, check_mx=False)
    if args.limit is not None:
        jobs = jobs[: args.limit]

    out_dir = ctx.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"*__{glob.escape(template)}.txt"):
        old.unlink()

    summary = out_dir / "preview_summary.csv"
    with open(summary, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["#", "company", "name", "email", "subject", "file"])
        for i, job in enumerate(jobs, start=1):
            fname = f"{i:03d}_{(slug(job.company)[:40] or 'company')}__{template}.txt"
            (out_dir / fname).write_text(
                f"To: {job.name} <{job.email}>\n"
                f"From: {ctx.cfg['sender']['name']} <{ctx.cfg['sender']['email']}>\n"
                f"Subject: {job.subject}\n"
                f"{'-' * 60}\n{job.body}\n",
                encoding="utf-8",
            )
            writer.writerow([i, job.company, job.name, job.email, job.subject, fname])

    print(f"Preview written: {len(jobs)} email(s) -> {out_dir}")
    print(f"Summary index  : {summary}")
    print(f"Skipped        : {len(skipped)} (run `mailmerge validate` for reasons)")
    return 0


def cmd_test(ctx: Context, args: argparse.Namespace) -> int:
    from .sender import send_jobs

    if not args.to:
        print("`mailmerge test` requires --to your@email.com")
        return 1
    template = _template(ctx, args.template)
    # Render from any contact (regardless of status) so a test always works.
    items = [(c, 0, template) for c in ctx.db.list_contacts(limit=50)]
    jobs, _ = prepare_jobs(ctx, items, check_mx=False)
    jobs = jobs[: (args.limit if args.limit is not None else 2)]
    if not jobs:
        print("No renderable contacts to test. Import some and fill personalization.")
        return 1

    print(f"TEST: sending {len(jobs)} sample(s) to {args.to} (no real prospect is contacted).")
    sent = send_jobs(
        ctx, jobs, campaign="test", daily_cap=len(jobs),
        force_to=args.to, attach_resume=not args.no_attachment, record=False,
    )
    print(f"Done. {sent} test email(s) sent to {args.to}. Check formatting + attachment.")
    return 0


def _do_send(ctx: Context, jobs: list[Job], skipped, args, *, kind: str, campaign: str) -> int:
    from .sender import send_jobs

    if args.limit is not None:
        jobs = jobs[: args.limit]
    cap, remaining = _effective_cap(ctx, args.daily_cap)
    to_send = min(len(jobs), remaining)

    if to_send == 0:
        already = ctx.db.sent_today()
        print(f"Nothing to send. ({len(jobs)} ready, {len(skipped)} skipped, "
              f"{already} already sent today, cap {cap}.)")
        return 0

    rp = ctx.resume_path
    resume_note = f"attached: {rp.name}" if rp.exists() else "WARNING: resume file NOT found"

    print("\n" + "=" * 64)
    print(f"ABOUT TO SEND REAL {kind.upper()} EMAILS FROM YOUR ACCOUNT")
    print("=" * 64)
    print(f"  From          : {ctx.cfg['sender']['name']} <{ctx.cfg['sender']['email']}>")
    print(f"  Campaign      : {campaign}")
    print(f"  Resume        : {resume_note}")
    print(f"  Ready to send : {len(jobs)}   (skipped this run: {len(skipped)})")
    print(f"  Daily cap     : {cap}  | already sent today: {ctx.db.sent_today()}  "
          f"| will send up to {to_send} now")
    print(f"  Pace          : {ctx.cfg['sending']['delay_min_seconds']}-"
          f"{ctx.cfg['sending']['delay_max_seconds']}s between each")
    print("=" * 64)
    print("Each person receives an individual email and only sees themselves.")

    if not args.yes and not _confirm_send():
        print("Cancelled. Nothing was sent.")
        return 0

    print("\nSending...\n")
    sent = send_jobs(
        ctx, jobs, campaign=campaign, daily_cap=remaining,
        attach_resume=not args.no_attachment,
    )
    print(f"\nDone. {sent} email(s) processed. Logged to the database.")
    leftover = len(jobs) - sent
    if leftover > 0:
        print(f"{leftover} more queued — re-run later (tomorrow, to respect the cap).")
    return 0


def cmd_send(ctx: Context, args: argparse.Namespace) -> int:
    template = _template(ctx, args.template)
    campaign = _campaign(ctx, args.campaign)
    jobs, skipped = initial_jobs(ctx, template=template)
    return _do_send(ctx, jobs, skipped, args, kind="initial", campaign=campaign)


def cmd_followup(ctx: Context, args: argparse.Namespace) -> int:
    campaign = _campaign(ctx, args.campaign)
    jobs, skipped = followup_jobs(ctx, force=args.force)
    if not jobs:
        print("No follow-ups are due right now.")
        if args.force:
            print("(--force was set but still nothing matched; everyone replied/bounced "
                  "or has no further steps.)")
        return 0
    return _do_send(ctx, jobs, skipped, args, kind="follow-up", campaign=campaign)


def cmd_sync(ctx: Context, args: argparse.Namespace) -> int:
    from .inbox import sync

    print("Reading replies over IMAP (read-only; your mail is not marked read)...")
    result = sync(ctx, lookback_days=args.lookback_days)
    print(f"Scanned {result.scanned} message(s); {result.new} newly matched.")
    print(f"  replies     : {result.replies}")
    print(f"  auto/OOO    : {result.auto_replies}")
    print(f"  bounces     : {result.bounces}")
    print(f"  unmatched   : {result.unmatched} (from senders we don't have on file)")
    if result.replies:
        print("\nNew replies — see them with: mailmerge replies --type reply")
    return 0


def cmd_replies(ctx: Context, args: argparse.Namespace) -> int:
    rows = ctx.db.list_replies(classification=args.type)
    if not rows:
        print("No replies recorded yet. Run `mailmerge sync` after sending.")
        return 0
    table = render_table(
        ["when", "type", "company", "from", "subject"],
        [[(r["received_at"] or "")[:16], r["classification"], truncate(r["company"] or "", 20),
          truncate(r["from_addr"] or "", 28), truncate(r["subject"] or "", 36)] for r in rows],
    )
    print(table)
    print(f"\n{len(rows)} reply record(s).")
    return 0


def cmd_status(ctx: Context, args: argparse.Namespace) -> int:
    print(status_report(ctx))
    return 0


def cmd_templates(ctx: Context, args: argparse.Namespace) -> int:
    names = list_templates(ctx.templates_dir)
    print("Templates in", ctx.templates_dir)
    for name in names:
        print(f"  - {name}")
    if not names:
        print("  (none)")
    return 0


def cmd_export(ctx: Context, args: argparse.Namespace) -> int:
    rows = ctx.db.list_contacts(status=args.status, search=args.search, tag=args.tag)
    out = ctx.resolve(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "email", "full_name", "company", "title", "status",
                         "last_step", "last_contacted_at", "replied_at", "tags", "personalization"])
        for r in rows:
            writer.writerow([r["id"], r["email"], r["full_name"], r["company"], r["title"],
                             r["status"], r["last_step"], r["last_contacted_at"],
                             r["replied_at"], r["tags"], r["personalization"]])
    print(f"Exported {len(rows)} contact(s) -> {out}")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mailmerge",
        description="Database-backed 1:1 cold-outreach mailer with reply tracking and follow-ups.",
    )
    parser.add_argument("--version", action="version", version=f"mailmerge {__version__}")
    parser.add_argument("--config", default="config.yaml", help="Path to config file.")
    parser.add_argument("--base-dir", default=None, help="Project root (default: current dir).")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create config, database, and folders.").set_defaults(func=cmd_init)

    p = sub.add_parser("import", help="Import/refresh contacts from a CSV/XLSX into the database.")
    p.add_argument("path", nargs="?", default=None, help="Spreadsheet path (default from config).")
    p.add_argument("--source", default=None, help="Label stored on imported rows.")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("contacts", help="List contacts (filterable).")
    p.add_argument("--status", default=None)
    p.add_argument("--search", default=None)
    p.add_argument("--tag", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_contacts)

    p = sub.add_parser("show", help="Show one contact's full history.")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("set", help="Adjust a contact in the database.")
    p.add_argument("id", type=int)
    p.add_argument("--status", choices=["new", "contacted", "replied", "bounced",
                                        "unsubscribed", "do_not_contact"])
    p.add_argument("--tag", default=None)
    p.add_argument("--note", default=None)
    p.add_argument("--personalization", default=None)
    p.add_argument("--company", default=None)
    p.add_argument("--title", default=None)
    p.add_argument("--full-name", dest="full_name", default=None)
    p.add_argument("--confidence", default=None)
    p.add_argument("--email", default=None)
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("validate", help="Show how many contacts are eligible to send.")
    p.add_argument("--template", default=None)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("preview", help="Render eligible emails to files (sends nothing).")
    p.add_argument("--template", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("test", help="Send sample emails to your own address.")
    p.add_argument("--to", required=False, help="Your own address to receive the test.")
    p.add_argument("--template", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--no-attachment", action="store_true")
    p.set_defaults(func=cmd_test)

    for name, help_text, fn in (
        ("send", "Send first-touch emails to eligible new contacts.", cmd_send),
        ("followup", "Send the next follow-up to contacts who are due.", cmd_followup),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--template", default=None)
        p.add_argument("--campaign", default=None)
        p.add_argument("--daily-cap", type=int, default=None)
        p.add_argument("--limit", type=int, default=None)
        p.add_argument("--no-attachment", action="store_true")
        p.add_argument("--yes", action="store_true", help="Skip the typed SEND confirmation.")
        if name == "followup":
            p.add_argument("--force", action="store_true",
                           help="Ignore wait_days and send all outstanding steps now.")
        p.set_defaults(func=fn)

    p = sub.add_parser("sync", help="Read replies back over IMAP and update contacts.")
    p.add_argument("--lookback-days", type=int, default=None)
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("replies", help="List inbound replies (filter by type).")
    p.add_argument("--type", choices=["reply", "auto_reply", "ooo", "bounce"], default=None)
    p.set_defaults(func=cmd_replies)

    sub.add_parser("status", help="Show the overall dashboard.").set_defaults(func=cmd_status)
    sub.add_parser("templates", help="List available templates.").set_defaults(func=cmd_templates)

    p = sub.add_parser("export", help="Export (filtered) contacts to CSV.")
    p.add_argument("--out", default="output/contacts_export.csv")
    p.add_argument("--status", default=None)
    p.add_argument("--search", default=None)
    p.add_argument("--tag", default=None)
    p.set_defaults(func=cmd_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    base_dir = Path(args.base_dir).resolve() if args.base_dir else Path.cwd()

    ctx = Context.create(base_dir, args.config)
    try:
        return args.func(ctx, args)
    finally:
        ctx.close()


if __name__ == "__main__":
    sys.exit(main())
