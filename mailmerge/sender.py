"""SMTP sending: one individual message per contact, throttled and logged.

Every send records a `messages` row (so we never double-email and can thread
replies back), embeds a generated Message-ID, and tags itself with custom
headers used later by the inbox sync to attribute replies to a contact.
"""

from __future__ import annotations

import os
import random
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .context import Context
from .pipeline import Job


def smtp_password(ctx: Context) -> str:
    env_var = ctx.cfg["auth"].get("password_env", "EMAIL_APP_PASSWORD")
    password = os.environ.get(env_var)
    if not password:
        sys.exit(
            f"No email password found in environment variable {env_var}.\n"
            f"Set it (it is never written to disk):\n"
            f"  macOS/Linux:  export {env_var}='your-app-password'\n"
            f"  Windows PS:   $env:{env_var}='your-app-password'\n"
            f"Gmail App Passwords: https://myaccount.google.com/apppasswords"
        )
    return password


def smtp_connect(ctx: Context) -> smtplib.SMTP:
    auth = ctx.cfg["auth"]
    password = smtp_password(ctx)
    server = smtplib.SMTP(auth["smtp_host"], int(auth["smtp_port"]), timeout=30)
    server.ehlo()
    # Verify the server certificate + hostname before sending credentials.
    server.starttls(context=ssl.create_default_context())
    server.ehlo()
    server.login(ctx.cfg["sender"]["email"], password)
    return server


def _hdr(value: str) -> str:
    """Collapse CR/LF so a stray newline in a field can never inject headers."""
    return " ".join(str(value or "").splitlines()).strip()


def build_message(
    ctx: Context,
    to_addr: str,
    job: Job,
    *,
    attach_resume: bool = True,
) -> EmailMessage:
    sender = ctx.cfg["sender"]
    msg = EmailMessage()
    from_name = _hdr(sender.get("name", ""))
    from_email = _hdr(sender.get("email", ""))
    msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"] = to_addr
    msg["Subject"] = _hdr(job.subject)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=from_email.split("@")[-1] or None)
    # Custom headers let inbox sync attribute replies even if threading breaks.
    msg["X-Mailmerge-Contact"] = str(job.contact_id)
    msg["X-Mailmerge-Step"] = str(job.step)
    msg.set_content(job.body)

    if attach_resume:
        rp = ctx.resume_path
        if rp.exists():
            maintype, subtype = "application", "pdf"
            if rp.suffix.lower() in (".doc", ".docx"):
                maintype, subtype = "application", "octet-stream"
            msg.add_attachment(
                rp.read_bytes(), maintype=maintype, subtype=subtype, filename=rp.name
            )
    return msg


def send_jobs(
    ctx: Context,
    jobs: list[Job],
    *,
    campaign: str,
    daily_cap: int,
    force_to: str | None = None,
    attach_resume: bool = True,
    record: bool = True,
) -> int:
    """Send a list of jobs at a human pace. Returns how many were processed.

    ``force_to`` (test mode) redirects every email to one address and suppresses
    real logging. ``daily_cap`` already accounts for what has been sent today.
    """
    sending = ctx.cfg["sending"]
    dmin = sending["delay_min_seconds"]
    dmax = sending["delay_max_seconds"]

    server = smtp_connect(ctx)
    sent_count = 0  # only successful sends count against the cap / are returned
    try:
        for job in jobs:
            if sent_count >= daily_cap:
                print(f"\nReached the cap of {daily_cap} for this run. "
                      f"Re-run later to continue where you left off.")
                break
            recipient = force_to or job.email
            status, error, message_id = "sent", "", None
            try:
                msg = build_message(ctx, recipient, job, attach_resume=attach_resume)
                message_id = msg["Message-ID"]
                server.send_message(msg)
            except Exception as exc:  # keep going on individual failures
                status, error = "error", str(exc)

            print(f"  {status:<5} step {job.step} -> {recipient}  ({job.company})"
                  + (f"  [{error}]" if error else ""))

            if record and force_to is None:
                ctx.db.record_message(
                    job.contact_id,
                    campaign=campaign,
                    template=job.template,
                    step=job.step,
                    subject=job.subject,
                    body=job.body,
                    message_id=message_id,
                    status=status,
                    error=error,
                )
            # A failed send is not retried here, but it must not consume the cap
            # or be reported as sent — the contact stays eligible for next run.
            if status != "sent":
                continue
            sent_count += 1
            if sent_count < daily_cap and job is not jobs[-1]:
                time.sleep(random.uniform(dmin, dmax))
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return sent_count
