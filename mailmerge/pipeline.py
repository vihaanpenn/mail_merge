"""Turn database contacts into ready-to-send, fully-checked jobs.

This is the gatekeeper that guarantees nothing half-baked goes out: a job is
only produced when the contact passes the confidence filter, every required
field is present, the rendered email has no leftover {placeholders}, the address
is well-formed, and (optionally) its domain can actually receive mail.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .context import Context
from .db import TERMINAL_STATUSES, STATUS_CONTACTED, STATUS_NEW
from .templates import build_variables, render_email
from .utils import confidence_rank, days_since, normalize_email
from .validation import address_mx_ok, dns_available, valid_format


@dataclass
class Job:
    contact_id: int
    email: str
    company: str
    name: str
    step: int
    template: str
    subject: str
    body: str


def _ident(contact: sqlite3.Row) -> str:
    return contact["email"] or contact["company"] or f"contact {contact['id']}"


def select_initial(ctx: Context) -> list[sqlite3.Row]:
    """Contacts that have never been emailed and clear the confidence bar."""
    min_rank = confidence_rank(ctx.cfg["contacts"]["min_confidence"])
    return [
        c for c in ctx.db.list_contacts(status=STATUS_NEW)
        if confidence_rank(c["confidence"]) >= min_rank
    ]


def select_followups(ctx: Context, *, force: bool = False) -> list[tuple[sqlite3.Row, dict]]:
    """Contacts whose next follow-up step is due. Returns (contact, step_cfg) pairs.

    The next step is chosen by *how many follow-ups have already been sent*
    (position in the sorted step list), not by arithmetic on a step number — so
    a non-contiguous or reordered `steps` config still advances correctly and a
    step is never skipped or repeated.
    """
    fcfg = ctx.cfg["followups"]
    if not fcfg.get("enabled", True):
        return []
    steps_sorted = sorted(fcfg.get("steps", []), key=lambda s: int(s["step"]))
    if not steps_sorted:
        return []

    due: list[tuple[sqlite3.Row, dict]] = []
    for contact in ctx.db.list_contacts(status=STATUS_CONTACTED):
        if contact["status"] in TERMINAL_STATUSES:
            continue
        done = ctx.db.count_followups_sent(contact["id"])
        if done >= len(steps_sorted):
            continue  # cadence exhausted
        step_cfg = steps_sorted[done]
        elapsed = days_since(contact["last_contacted_at"])
        if force or (elapsed is not None and elapsed >= float(step_cfg["wait_days"])):
            due.append((contact, step_cfg))
    return due


def prepare_jobs(
    ctx: Context,
    items: list[tuple[sqlite3.Row, int, str]],
    *,
    check_mx: bool | None = None,
) -> tuple[list[Job], list[tuple[str, str]]]:
    """Render and fully validate each (contact, step, template). Returns (jobs, skipped)."""
    required = ctx.cfg["contacts"]["required_fields"]
    do_format = ctx.cfg["verification"]["check_format"]
    do_mx = ctx.cfg["verification"]["check_mx"] if check_mx is None else check_mx
    sender = ctx.cfg["sender"]

    jobs: list[Job] = []
    skipped: list[tuple[str, str]] = []
    seen: set[str] = set()

    for contact, step, template in items:
        ident = _ident(contact)
        addr = normalize_email(contact["email"])

        if not addr:
            skipped.append((ident, "no email address"))
            continue
        if do_format and not valid_format(addr):
            skipped.append((ident, f"invalid email format: {addr}"))
            continue
        if addr in seen:
            skipped.append((ident, "duplicate address within this run"))
            continue

        try:
            subject, body, leftovers = render_email(template, ctx.templates_dir, contact, sender)
        except Exception as exc:  # template error
            skipped.append((ident, f"template error: {exc}"))
            continue

        variables = build_variables(contact, sender)
        problems = [f"missing required field: {f}" for f in required if not variables.get(f)]
        problems += [f"unresolved placeholder: {p}" for p in leftovers]
        if problems:
            skipped.append((ident, "; ".join(problems)))
            continue

        seen.add(addr)
        jobs.append(Job(
            contact_id=contact["id"],
            email=addr,
            company=contact["company"] or "",
            name=contact["full_name"] or "",
            step=step,
            template=template,
            subject=subject,
            body=body,
        ))

    # MX is the slow check, so only run it on jobs that already passed everything.
    if do_mx and jobs:
        if dns_available():
            survivors: list[Job] = []
            for job in jobs:
                if address_mx_ok(job.email):
                    survivors.append(job)
                else:
                    skipped.append((job.email, "domain has no MX record (cannot receive mail)"))
            jobs = survivors

    return jobs, skipped


def initial_jobs(ctx: Context, *, template: str, check_mx: bool | None = None):
    items = [(c, 0, template) for c in select_initial(ctx)]
    return prepare_jobs(ctx, items, check_mx=check_mx)


def followup_jobs(ctx: Context, *, force: bool = False, check_mx: bool | None = None):
    items = [(c, int(s["step"]), s["template"]) for c, s in select_followups(ctx, force=force)]
    return prepare_jobs(ctx, items, check_mx=check_mx)
