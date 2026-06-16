# mailmerge

A database-backed, **1:1 cold-outreach mailer** for genuine job-search outreach.

It renders a personalized email per contact, sends each one individually from
your own account at a human pace, reads the replies back over IMAP, classifies
them, and runs a polite multi-step follow-up cadence — all on top of a SQLite
database you can keep importing into and adjusting over time.

> **This is not a bulk spam tool.** Every recipient gets an individual message
> and only ever sees themselves. There are no BCC blasts, no tracking pixels, no
> list buying. It is built to make *careful, personal* outreach less tedious.

---

## Why it exists

The first version was a single script that read a CSV and sent emails. This is
the full operation it grew into:

| Capability | v0 (script) | mailmerge (this) |
|---|---|---|
| Contact storage | flat CSV | **SQLite database**, re-importable & editable |
| Personalization | per-row template | per-row template + arbitrary columns as `{vars}` |
| Sending | one-shot | throttled, daily-capped, deduped, resumable |
| Sent tracking | append-only CSV | full message history per contact |
| **Reading replies** | ✗ | **IMAP sync**: reply / auto-reply / OOO / bounce |
| **Follow-ups** | ✗ | **multi-step cadence**, skips repliers & bounces |
| Lifecycle | none | `new → contacted → replied / bounced / …` |
| Reporting | ✗ | status dashboard, per-contact history, export |
| Tests | ✗ | full pytest suite |

---

## How it works (the lifecycle)

```
            import                send                 sync
 spreadsheet ─────▶ DATABASE ─────────▶ recipients ───────▶ replies read back
   (CSV/XLSX)        (SQLite)   │                    │       & classified
       ▲                        │                    │
       │ re-import / `set`      │ follow-up          ▼
       └── adjust freely        └───────────  contact status updated
                                  (skips repliers/bounces)
```

Each contact moves through a lifecycle:

- **new** — imported, never emailed
- **contacted** — at least one email sent (tracks which step)
- **replied** — a genuine reply came back (follow-ups stop)
- **bounced** — the address failed (follow-ups stop)
- **unsubscribed / do_not_contact** — set by you; never contacted again

---

## Install

Requires **Python 3.10+**.

```bash
pip install -r requirements.txt     # PyYAML + dnspython
pip install -e .                    # installs the `mailmerge` command

# optional extras
pip install -e ".[excel]"           # import contacts straight from .xlsx
pip install -e ".[dev]"             # pytest
pip install -e ".[all]"             # everything
```

You can also run it without installing: `python -m mailmerge <command>`.

---

## Setup

### 1. Scaffold

```bash
mailmerge init
```

Creates `config.yaml` (from `config.example.yaml`), the SQLite database, and the
`data/ logs/ output/ resume/ templates/` folders.

### 2. Configure

Edit `config.yaml` — your name/email, SMTP + IMAP hosts, the resume path, and how
your spreadsheet columns map to the logical fields. See
[Configuration](#configuration) below.

### 3. Credentials (Gmail)

Your password is **never** stored on disk — it's read from an environment
variable at run time. For Gmail you need an **App Password**:

1. Enable 2-Step Verification: <https://myaccount.google.com/security>
2. Create an App Password: <https://myaccount.google.com/apppasswords>
3. Export it (the same password is used for both sending and reading):

```bash
export EMAIL_APP_PASSWORD='your-16-char-app-password'
```

### 4. Your data

Put your resume in `resume/` (match `resume.path` in config), then load contacts:

```bash
cp data/contacts.example.csv data/contacts.csv   # edit with your list
mailmerge import data/contacts.csv
```

---

## The database is the source of truth

After import, everything lives in `data/mailmerge.db`. The key property: **you
can keep adjusting it.**

- **Re-import any time.** `mailmerge import` upserts on email. Existing contacts
  are updated, new ones added, send/reply history preserved. Crucially, an empty
  cell in your sheet will **not** wipe a value you already have — so you can
  enrich rows in the app and re-import the raw sheet without losing your edits.
- **Edit individual contacts** without touching the spreadsheet:

```bash
mailmerge set 42 --personalization "your work on on-device inference"
mailmerge set 42 --tag priority --note "met at the robotics meetup"
mailmerge set 42 --status do_not_contact        # exclude from all outreach
```

- **Arbitrary columns are preserved.** Any spreadsheet column you don't map is
  stored alongside the contact and exposed to templates as `{slugified_header}`
  (e.g. a `Stage/Size` column becomes `{stage_size}`).

---

## Commands

Run `mailmerge --help` or `mailmerge <command> --help` for full options.

| Command | What it does |
|---|---|
| `init` | Create config, database, and folders. |
| `import [path]` | Import/refresh contacts from CSV/XLSX (upsert on email). |
| `contacts` | List contacts. Filters: `--status --search --tag --limit`. |
| `show <id>` | Full history for one contact (outbound + inbound). |
| `set <id> …` | Adjust a contact: `--status --tag --note --personalization`, etc. |
| `validate` | Who's eligible to send, who's skipped, and why. Sends nothing. |
| `preview` | Render eligible emails to `output/preview/`. Sends nothing. |
| `test --to you@…` | Send sample emails to **your own** address. |
| `send` | Send first-touch emails to eligible new contacts (confirms first). |
| `followup` | Send the next follow-up step to anyone who's due. |
| `sync` | Read replies back over IMAP; classify and update contacts. |
| `replies [--type]` | List inbound mail: `reply / auto_reply / ooo / bounce`. |
| `status` | The dashboard: counts, reply rate, queue, follow-ups due. |
| `export --out f.csv` | Export (filterable) contacts to CSV. |
| `templates` | List available templates. |

### A normal day

```bash
mailmerge sync                 # 1. pull in any replies first
mailmerge status               # 2. see where things stand
mailmerge followup             # 3. nudge people who are due (skips repliers)
mailmerge send --daily-cap 20  # 4. start a few new threads
```

### Safety guardrails (always on)

- **Default commands send nothing.** Only `send` and `followup` mail real
  people, and both print a summary and require you to type `SEND`
  (use `--yes` to skip in automation).
- **Skips half-baked rows.** A contact is dropped if it's missing a required
  field (default: first name, company, personalization) or if a rendered email
  still contains an unresolved `{placeholder}` — so a literal `{personalization}`
  can never go out.
- **Never double-emails.** Every send is recorded; the same step is never sent
  to the same person twice.
- **Daily cap + jitter.** Sends stop at the cap (default 40) and pause a random
  35–90s between messages. Re-run later to continue where you left off.
- **Confidence + MX filtering.** Skips low-confidence addresses and (with
  `dnspython`) domains that can't receive mail.
- **Read-only inbox sync.** `sync` uses IMAP `BODY.PEEK`, so reading replies
  never marks your mail as read.

---

## Follow-ups

Configured under `followups` in `config.yaml`:

```yaml
followups:
  enabled: true
  steps:
    - { step: 1, template: "followup1", wait_days: 4 }
    - { step: 2, template: "followup2", wait_days: 7 }
```

`mailmerge followup` finds every contacted person whose next step is due
(`wait_days` measured from their most recent email) and who has **not** replied
or bounced, then sends that step. `--force` ignores the wait; `--daily-cap`
and `--limit` apply as usual.

Order matters: run `mailmerge sync` first so anyone who replied is excluded.

---

## Templates

Plain-text files in `templates/`. First line is the subject, then a blank line,
then the body:

```
Subject: would love to talk to {company}

Hi {first_name},

I'm interested in {company}'s work on {personalization}.

Best,
{my_name}
{my_phone} - {my_links}
```

Available placeholders: `{first_name} {full_name} {company} {title} {email}
{personalization} {confidence}`, your signature fields `{my_name} {my_phone}
{my_links} {my_email}`, plus any unmapped spreadsheet column as `{slug}`.
Ships with `warm`, `direct`, `followup1`, `followup2`.

---

## Configuration

`config.yaml` (created by `init`, ignored by git). Highlights:

```yaml
sender:   { name, email, phone, links }      # From + signature
auth:     { smtp_host, smtp_port, password_env }
imap:     { enabled, host, port, mailbox, password_env, lookback_days }
database: { path }                           # data/mailmerge.db
contacts:
  columns: { company, name, title, email, confidence, personalization }
  required_fields: [first_name, company, personalization]
  min_confidence: Medium                     # High > Medium > Low
sending:  { default_template, campaign, daily_cap, delay_min_seconds, delay_max_seconds }
followups: { enabled, steps: [...] }
verification: { check_format, check_mx }
```

See [`config.example.yaml`](config.example.yaml) for the fully-commented version.

**Other providers:** set `auth.smtp_host` / `imap.host` accordingly — e.g. Outlook
uses `smtp-mail.outlook.com` and `outlook.office365.com`.

---

## Project layout

```
mailmerge/
  cli.py          # argparse subcommands (the entry point)
  context.py      # ties config + database + resolved paths together
  config.py       # defaults merged with config.yaml
  db.py           # SQLite schema + data access (the source of truth)
  contacts.py     # CSV/XLSX import -> upsert
  templates.py    # load + tolerant {placeholder} rendering
  pipeline.py     # select & fully-validate contacts into send-ready jobs
  sender.py       # SMTP send loop (threading headers, throttle, cap)
  inbox.py        # IMAP read-back: classify + match replies to contacts
  reporting.py    # status dashboard + per-contact history
  validation.py   # email format + MX checks
  utils.py        # shared helpers
templates/        # warm, direct, followup1, followup2
data/             # contacts.csv + mailmerge.db (git-ignored)
tests/            # pytest suite
```

---

## Testing

```bash
pip install -e ".[dev]"
pytest -q
```

A ~280-case suite, all offline (no network, no real mail) using fixtures with
dummy data and in-memory SMTP/IMAP fakes. It covers utilities, config loading,
the database upsert/lifecycle, contact import & dedupe, template rendering, the
eligibility pipeline, follow-up cadence, reply classification/matching, the full
IMAP sync, SMTP sending (cap accounting, header sanitization), reporting, and
the end-to-end CLI for every subcommand.

---

## Responsible use

Send to people for whom you have a genuine, individual reason to reach out.
Keep volumes low, honor opt-outs immediately (`set <id> --status unsubscribed`),
and don't use this for unsolicited bulk mail. Deliverability and good manners
go hand in hand.

## License

MIT
