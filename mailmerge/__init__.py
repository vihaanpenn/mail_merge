"""mailmerge - a personal, database-backed 1:1 cold-outreach mailer.

A small operations toolkit for genuine, individual job-search outreach:

  * contacts live in a SQLite database you can re-import and adjust over time
  * emails are rendered per-contact from plain-text templates
  * sends are throttled, capped, deduped, and fully logged
  * replies are read back over IMAP, classified, and matched to contacts
  * follow-ups are scheduled as a multi-step cadence that never chases someone
    who already replied or bounced

It is deliberately NOT a bulk spam tool: every recipient gets an individual
message and only ever sees themselves.
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
