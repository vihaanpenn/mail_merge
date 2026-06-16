# mailmerge — The Complete Handbook

> A database-backed, 1:1 cold-outreach operation for genuine job-search emailing.
> This document covers **every aspect of the system in depth**: the philosophy,
> the architecture, every module and database table, every command, the sending
> and reply-tracking engines, the research methodology behind the prospect list,
> the outreach playbook, deliverability, security, testing, and troubleshooting.

**Repository:** <https://github.com/vihaanpenn/mail_merge>
**Audience:** the operator (you), and any future maintainer.
**Status:** v1.0.0 — 282 passing tests, adversarially reviewed.

---

## Table of contents

1. [What this is and why it exists](#1-what-this-is-and-why-it-exists)
2. [The operation at a glance](#2-the-operation-at-a-glance)
3. [Core concepts](#3-core-concepts)
4. [System architecture](#4-system-architecture)
5. [Module-by-module deep dive](#5-module-by-module-deep-dive)
6. [The database, in incredible detail](#6-the-database-in-incredible-detail)
7. [The contact lifecycle state machine](#7-the-contact-lifecycle-state-machine)
8. [Installation and setup](#8-installation-and-setup)
9. [Configuration reference](#9-configuration-reference)
10. [CLI command reference](#10-cli-command-reference)
11. [The sending pipeline](#11-the-sending-pipeline)
12. [Templates and personalization](#12-templates-and-personalization)
13. [The follow-up engine](#13-the-follow-up-engine)
14. [Inbox sync and reply intelligence](#14-inbox-sync-and-reply-intelligence)
15. [Safety, deliverability, and guardrails](#15-safety-deliverability-and-guardrails)
16. [The research methodology (building the prospect list)](#16-the-research-methodology-building-the-prospect-list)
17. [Email-finding and verification](#17-email-finding-and-verification)
18. [The outreach playbook](#18-the-outreach-playbook)
19. [Daily and weekly operating routine](#19-daily-and-weekly-operating-routine)
20. [Testing](#20-testing)
21. [Security and privacy](#21-security-and-privacy)
22. [Troubleshooting](#22-troubleshooting)
23. [Extensibility and roadmap](#23-extensibility-and-roadmap)
24. [Appendix A: full schema DDL](#appendix-a-full-schema-ddl)
25. [Appendix B: glossary](#appendix-b-glossary)
26. [Appendix C: FAQ](#appendix-c-faq)

---

## 1. What this is and why it exists

`mailmerge` is a command-line tool for running **personal, one-to-one outreach**
during a job search. You maintain a database of companies and people you have a
genuine reason to contact; the tool renders an individually-tailored email for
each person from a plain-text template, sends them one at a time from your own
email account at a human pace, reads the replies back over IMAP, classifies
them, and runs a polite multi-step follow-up cadence — while keeping a complete,
queryable history of everything.

### What it is not

It is deliberately **not a bulk-email / spam platform**:

- Every recipient receives an **individual message** and only ever sees
  themselves in the `To:` line. There is no BCC blasting and no mailing-list
  semantics.
- There are **no tracking pixels**, no open/click beacons, no link rewriting.
- It does not buy, scrape indiscriminately, or share contact lists.
- It throttles and caps itself precisely so it *cannot* behave like a blaster.

### The design thesis

Good cold outreach is a **research problem, not a volume problem**. The bottleneck
is finding the right companies, writing a credible personalized hook, and
following up politely — not sending faster. So the tool optimizes for:

1. **A durable, adjustable database** as the single source of truth.
2. **Hard guardrails** that make a careless mistake (a half-filled template, a
   double-email, a bounce-storm) structurally difficult.
3. **Closing the loop** — reading replies back so the system always knows who
   replied, who bounced, and who is due for a nudge.

### Origin

The first version was a single 694-line script (`send_emails.py`) that read a CSV
and sent emails. This system is the "full operation" it grew into: a proper
Python package with a SQLite database, IMAP reply ingestion, a follow-up engine,
a reporting dashboard, a full subcommand CLI, and a 282-case test suite.

---

## 2. The operation at a glance

```
            import                 send                  sync
 spreadsheet ─────▶  DATABASE  ─────────▶  recipients  ───────▶  replies read back
   (CSV/XLSX)        (SQLite)    │                       │        & classified
       ▲                         │                       │
       │ re-import / `set`       │ followup              ▼
       └── adjust freely         └──────────────  contact status updated
                                   (skips repliers/bounces)
```

The lifecycle of a single prospect:

1. **Research** a company → add a row (company, hook, and eventually a verified email).
2. **Import** the row into the database (`new`).
3. **Validate / preview** to confirm the rendered email is correct.
4. **Send** the first-touch email (`contacted`, step 0).
5. **Sync** your inbox — if they reply, they become `replied` (cadence stops);
   if it bounces, `bounced` (cadence stops).
6. **Follow up** on a schedule if there is no reply (step 1, step 2, …).
7. **Report** — track reply rate, bounces, who is due, what's queued.

---

## 3. Core concepts

| Concept | Meaning |
|---|---|
| **Contact** | A person/company you might email. The atomic unit; deduped by email. |
| **Message** | One outbound email we generated (sent / error / test). Full history is kept. |
| **Reply** | One inbound message matched back from IMAP, classified by type. |
| **Campaign** | A free-text label stamped on each send, for grouping/reporting. |
| **Step** | Position in the cadence. Step 0 = first touch; steps 1..N = follow-ups. |
| **Status** | The contact's lifecycle state (`new`, `contacted`, `replied`, …). |
| **Confidence** | How sure you are the email address is correct (`High`/`Medium`/`Low`). |
| **Personalization** | The per-company hook that fills "…your work on ___". |
| **Job** | A fully-rendered, fully-validated email ready to send (in-memory only). |

---

## 4. System architecture

### 4.1 Layered design

```
            ┌─────────────────────────────────────────────┐
   CLI      │  cli.py  — argparse subcommands, I/O, prompts │
            └───────────────┬─────────────────────────────┘
                            │ builds once, threads everywhere
            ┌───────────────▼─────────────────────────────┐
  Context   │  context.py — config + db + resolved paths    │
            └───────────────┬─────────────────────────────┘
        ┌───────────────────┼────────────────────────────────┐
 Engine  │ pipeline.py   sender.py   inbox.py   followup logic │
        │ (select+render+ (SMTP)     (IMAP read  reporting.py  │
        │  validate)                  + classify)              │
        └───────────────────┼────────────────────────────────┘
        ┌───────────────────▼────────────────────────────────┐
 Data    │ db.py (SQLite)  contacts.py (import)  templates.py  │
        │ config.py  validation.py  utils.py                   │
        └─────────────────────────────────────────────────────┘
```

### 4.2 Design principles

- **The database is the source of truth.** Spreadsheets are an *input format*,
  never the system of record. You can re-import freely without losing history.
- **Pure core, side-effects at the edges.** Rendering, selection, and
  classification are pure and unit-tested; SMTP/IMAP/file I/O live in thin,
  mockable modules (`sender`, `inbox`, `cli`).
- **One `Context` object** carries config + db + resolved paths so no module
  re-derives where things live.
- **Fail safe, not open.** Anything ambiguous (missing field, unresolved
  placeholder, low confidence, no MX) results in *skipping*, never sending.
- **Zero heavy dependencies.** Standard library for everything that ships mail or
  talks to SQLite; only `PyYAML` is required, with `dnspython` recommended.

### 4.3 Dependencies

| Package | Required? | Used for |
|---|---|---|
| `PyYAML` | Yes | parsing `config.yaml` |
| `dnspython` | Recommended | MX-record verification (`verification.check_mx`) |
| `pandas` + `openpyxl` | Optional | importing `.xlsx` contact lists |
| `pytest` | Dev | the test suite |

Everything else — SMTP, IMAP, email parsing, SQLite, CSV — is Python standard
library.

---

## 5. Module-by-module deep dive

The package lives in `mailmerge/`. Each module has a single, sharp responsibility.

### 5.1 `utils.py` — shared, dependency-free helpers

- `now_iso()` — local time as a stable, sortable `YYYY-MM-DDTHH:MM:SS` string.
- `parse_iso()` / `days_since()` — timestamp parsing and elapsed-days math
  (drives follow-up timing).
- `slug()` — normalizes an arbitrary spreadsheet header into a variable name
  (`"Stage/Size"` → `stage_size`).
- `normalize_email()` — trims + lowercases (the canonical dedupe key).
- `valid_email_format()` — the always-on syntax check.
- `first_name_from()` — best-effort first name, skipping honorifics (`"Dr. Grace
  Hopper"` → `Grace`).
- `confidence_rank()` — maps `High/Medium/Low/blank` to `3/2/1/0`.
- `unresolved_placeholders()` — finds leftover `{tokens}` after rendering.
- `deep_merge()` — recursively merges user config over defaults. **Deep-copies**
  so a loaded/mutated config can never corrupt the module-level defaults, and a
  `None` override (an empty YAML section) keeps the default instead of wiping it.
- `truncate()` / `render_table()` — small CLI formatting helpers.

### 5.2 `config.py` — configuration

Holds `DEFAULT_CONFIG` (the full default tree) and `load_config()`, which merges
`config.yaml` over the defaults via `deep_merge`. The `Config` class is a dict
subclass with `.resolve(base_dir, path)` and `.password(env_var)` helpers and a
`.source` attribute (the file it loaded, or `None`). **Passwords are never read
from config** — only from environment variables named by `auth.password_env` /
`imap.password_env`.

### 5.3 `context.py` — the runtime context

`Context` ties together `cfg`, `db`, and `base_dir`, and exposes resolved paths
as properties: `templates_dir`, `resume_path`, `output_dir`, `db_path`.
`Context.create(base_dir, config_path)` loads config, opens the database, and
returns a ready context. Every command receives one `Context`.

### 5.4 `db.py` — SQLite persistence

The heart of the system. A `Database` class wrapping a `sqlite3` connection with
`row_factory = Row`, `PRAGMA foreign_keys = ON`, and `PRAGMA user_version` for
migrations. See [§6](#6-the-database-in-incredible-detail) for the full schema and
every method.

### 5.5 `contacts.py` — spreadsheet import

`import_file(db, path, columns, source)` reads `.csv` / `.tsv` (stdlib) or
`.xlsx` (pandas), maps each row to contact fields via the configured column map,
preserves unmapped columns in `extra`, and **upserts on email**. Returns an
`ImportResult` (`added`, `updated`, `skipped`, `skip_reasons`). Rows with no
email or a malformed email are skipped and reported.

### 5.6 `templates.py` — rendering

Loads a template (`Subject:` line, blank line, body), exposes a **tolerant**
`format_map` that leaves unknown `{tokens}` intact (so they can be detected and
the row skipped rather than crashing), and `build_variables()` assembles the
placeholder dict for a contact: core fields, every `extra` column as `{slug}`,
and the sender's signature fields (`{my_name}`, `{my_phone}`, `{my_links}`,
`{my_email}`). `render_email()` returns `(subject, body, leftover_placeholders)`.

### 5.7 `validation.py` — address checks

`valid_format()` (syntax) and `address_mx_ok()` (does the domain advertise an MX
record?). MX uses `dnspython` if installed and is **cached per-domain**; if
`dnspython` is absent it returns `True` (cannot check → do not block). `dns_available()`
reports whether checks can run.

### 5.8 `pipeline.py` — selection + the validation gatekeeper

- `select_initial()` — `new` contacts that clear the confidence bar.
- `select_followups()` — `contacted` contacts whose next step is due (count-based).
- `prepare_jobs()` — renders and **fully validates** each candidate into a `Job`,
  or records a skip reason. This is the single chokepoint that guarantees nothing
  half-baked goes out. See [§11](#11-the-sending-pipeline).

### 5.9 `sender.py` — SMTP sending

`smtp_connect()` (verified STARTTLS), `build_message()` (header sanitization,
`Date`, `Message-ID`, custom threading headers, résumé attachment), and
`send_jobs()` (the throttled send loop with correct daily-cap accounting). See
[§11](#11-the-sending-pipeline).

### 5.10 `inbox.py` — IMAP reply ingestion

`imap_connect()` (verified TLS), `classify()` (reply / auto-reply / OOO /
bounce), `_get_text_body()` (plain-preferred, HTML-stripped fallback),
`_match_contact()` (sender → thread → bounce-body precedence), and `sync()` (the
read-only scan that records replies and updates statuses). See
[§14](#14-inbox-sync-and-reply-intelligence).

### 5.11 `reporting.py` — dashboards

`status_report()` (counts, reply rate, queue, follow-ups due) and
`contact_detail()` (one contact's full inbound + outbound history).

### 5.12 `cli.py` — the command-line interface

`argparse` with one subparser per command. `main(argv)` builds a `Context` and
dispatches. Every command returns an integer exit code. See
[§10](#10-cli-command-reference).

---

## 6. The database, in incredible detail

The database is a single SQLite file (default `data/mailmerge.db`). It is the
**system of record**: spreadsheets feed it, the CLI mutates it, and reports read
it. It is designed to be **continuously re-imported and adjusted** without losing
send/reply history.

### 6.1 Why SQLite

- **Durable + queryable.** History survives, and you can run ad-hoc SQL.
- **Zero-ops.** A single file, no server, atomic transactions.
- **Portable.** Copy the file to back up; it is git-ignored so real data never
  lands in version control.

### 6.2 Tables

#### `contacts` — people/companies and their lifecycle

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | autoincrement |
| `email` | TEXT **UNIQUE** | normalized (trim+lowercase); the dedupe key |
| `first_name` | TEXT | derived on import if absent |
| `full_name` | TEXT | |
| `company` | TEXT | |
| `title` | TEXT | |
| `confidence` | TEXT | `High` / `Medium` / `Low` / blank |
| `personalization` | TEXT | the per-company hook |
| `status` | TEXT, default `new` | lifecycle state ([§7](#7-the-contact-lifecycle-state-machine)) |
| `tags` | TEXT | comma-separated |
| `notes` | TEXT | freeform |
| `source` | TEXT | where the row came from (import label) |
| `extra` | TEXT (JSON), default `{}` | every unmapped spreadsheet column |
| `last_step` | INTEGER, default `-1` | highest step sent (-1 = never) |
| `last_contacted_at` | TEXT | timestamp of most recent send |
| `replied_at` | TEXT | set when a genuine reply arrives |
| `bounced_at` | TEXT | set on bounce |
| `created_at` / `updated_at` | TEXT | bookkeeping |

#### `messages` — every outbound email

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `contact_id` | INTEGER FK → contacts | `ON DELETE CASCADE` |
| `campaign` | TEXT | grouping label |
| `template` | TEXT | which template was used |
| `step` | INTEGER, default 0 | 0 = first touch |
| `subject` / `body` | TEXT | the exact rendered content (audit trail) |
| `message_id` | TEXT | the RFC `Message-ID` we generated (for threading) |
| `status` | TEXT | `sent` / `error` / (test sends are not recorded) |
| `error` | TEXT | the exception text if the send failed |
| `sent_at` | TEXT | timestamp |

#### `replies` — every inbound message we matched

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `contact_id` | INTEGER FK → contacts | `ON DELETE SET NULL` |
| `uid` | TEXT **UNIQUE** | `mailbox:uidvalidity:uid` — the dedupe key |
| `message_id` | TEXT | the reply's own Message-ID |
| `in_reply_to` | TEXT | the header that threaded it to us |
| `from_addr` | TEXT | normalized sender |
| `subject` | TEXT | truncated |
| `snippet` | TEXT | first ~280 chars of the body |
| `classification` | TEXT | `reply` / `auto_reply` / `ooo` / `bounce` |
| `received_at` | TEXT | parsed from the `Date` header |
| `created_at` | TEXT | when we recorded it |

#### `meta` — key/value bookkeeping

Stores `schema_version` and leaves room for future settings.

### 6.3 Indexes

`idx_contacts_status`, `idx_messages_contact`, `idx_messages_msgid`,
`idx_replies_contact` — the columns the hot queries filter/join on.

### 6.4 The "adjustable database" — upsert semantics

`upsert_contact(fields)` is the workhorse that makes re-importing safe:

- **Match on normalized email.** `Ada@Drone.CO` and `ada@drone.co ` are the same
  contact.
- **Non-empty-only overwrite.** On update, a core field is overwritten **only if
  the incoming value is non-empty**. So re-importing a raw spreadsheet with a
  blank cell will *never* erase a value you typed in by hand. This is what lets
  you enrich contacts in the app and keep re-importing the source sheet.
- **`extra` is merged, not replaced**, and empty-string keys are dropped (so the
  JSON stays clean and consistent across insert and update paths).
- **History is preserved.** Upsert touches only the contact row; `messages` and
  `replies` are untouched.

Returns `(contact_id, created)`.

### 6.5 Lifecycle-advancing writes

`record_message(..., status='sent')` is the only thing that advances a contact:
it sets `last_step`, `last_contacted_at`, and promotes `new → contacted` (it
**never** downgrades an existing `replied`/`bounced`/etc.). A `status='error'`
record advances nothing — the contact stays eligible to retry next run.

`mark_replied()` sets `replied_at` + `status='replied'`. `mark_bounced()` sets
`bounced_at` + `status='bounced'`, **except** it will not downgrade a contact who
already `replied` (a reply outranks a later bounce notice).

### 6.6 Query helpers

`list_contacts()` (filter by status/search/tag/limit, with LIKE metacharacters
**escaped** and tags matched on comma boundaries so `vip` ≠ `viplead`),
`find_contact_by_message_id()` (threads inbound replies), `sent_today()`
(daily-cap accounting), `count_followups_sent()` (drives cadence position),
`status_counts()` and `totals()` (reporting).

---

## 7. The contact lifecycle state machine

```
                 import
                   │
                   ▼
   ┌───────┐  send (step 0)  ┌───────────┐
   │  new  ├────────────────▶│ contacted │
   └───┬───┘                 └─────┬─────┘
       │ set --status              │ followup (step 1..N)
       │                           │  (loops; never past last step)
       │                           │
       │            sync: reply ───┼──────────────▶ ┌─────────┐
       │            sync: bounce ──┼──────────────▶ │ replied │ (terminal)
       │                           │                └─────────┘
       │                           └──────────────▶ ┌─────────┐
       │                                            │ bounced │ (terminal)
       │  set --status unsubscribed / do_not_contact└─────────┘
       └──────────────────────────────────────────▶ unsubscribed / do_not_contact (terminal)
```

| Status | Meaning | Eligible for first send? | Eligible for follow-up? |
|---|---|---|---|
| `new` | imported, never emailed | ✅ (if confidence ≥ min) | — |
| `contacted` | ≥1 email sent | — | ✅ (if a step is due) |
| `replied` | a genuine reply arrived | ❌ | ❌ |
| `bounced` | the address failed | ❌ | ❌ |
| `unsubscribed` | opted out | ❌ | ❌ |
| `do_not_contact` | you excluded them | ❌ | ❌ |

The four **terminal** statuses (`replied`, `bounced`, `unsubscribed`,
`do_not_contact`) make a contact permanently ineligible for any automated
outreach. You move contacts in/out of states with `mailmerge set <id> --status`.

---

## 8. Installation and setup

### 8.1 Prerequisites

- Python **3.10+**.
- A Gmail (or other SMTP/IMAP) account from which to send and read replies.

### 8.2 Install

```bash
cd cold-email-outreach
pip install -r requirements.txt     # PyYAML + dnspython
pip install -e .                    # installs the `mailmerge` command

# optional extras
pip install -e ".[excel]"           # import .xlsx lists
pip install -e ".[dev]"             # pytest
pip install -e ".[all]"             # everything
```

You can always run without installing: `python -m mailmerge <command>`.

### 8.3 Scaffold

```bash
mailmerge init
```

Creates `config.yaml` (from `config.example.yaml`), the SQLite database, and the
`data/`, `output/preview/`, `resume/`, `templates/` folders.

### 8.4 Gmail App Password (the credential)

Your password is **never written to disk**. It is read from an environment
variable at run time, and the *same* password is used for sending (SMTP) and
reading (IMAP).

1. Enable 2-Step Verification: <https://myaccount.google.com/security>
2. Create an App Password: <https://myaccount.google.com/apppasswords>
3. Export it:

```bash
export EMAIL_APP_PASSWORD='your-16-char-app-password'      # macOS/Linux
$env:EMAIL_APP_PASSWORD='your-16-char-app-password'        # Windows PowerShell
```

### 8.5 Your data

- Put your résumé at the path in `config.yaml` (`resume.path`, default
  `resume/Vihaan_Resume.pdf`).
- Put your contact list at `data/contacts.csv` and import it:

```bash
mailmerge import data/contacts.csv
```

---

## 9. Configuration reference

`config.yaml` (created by `init`, git-ignored). Every key:

```yaml
sender:
  name:  "Vihaan Ravishankar"     # the From display name + {my_name}
  email: "you@gmail.com"          # the address you send from AND read replies on
  phone: "+1 (555) 555-5555"      # {my_phone} in signatures
  links: "linkedin.com/in/you - you.dev"   # {my_links}

auth:
  smtp_host: "smtp.gmail.com"     # Outlook: smtp-mail.outlook.com
  smtp_port: 587                  # 587 = STARTTLS
  password_env: "EMAIL_APP_PASSWORD"   # env var name (never the password itself)

imap:
  enabled: true                   # set false to disable `sync`
  host: "imap.gmail.com"          # Outlook: outlook.office365.com
  port: 993
  mailbox: "INBOX"
  password_env: "EMAIL_APP_PASSWORD"   # usually the same app password
  lookback_days: 30               # only scan mail newer than this when syncing

database:
  path: "data/mailmerge.db"       # the SQLite source of truth

contacts:
  path: "data/contacts.csv"       # default sheet for `import`
  columns:                        # map logical fields → your EXACT headers
    company:         "Company Name"
    name:            "Contact Name"
    title:           "Contact Title"
    email:           "Email"
    confidence:      "Email Confidence"
    personalization: "Personalization"
  required_fields: ["first_name", "company", "personalization"]   # all must render non-empty
  min_confidence: "Medium"        # skip below this (High > Medium > Low)

resume:
  path: "resume/Vihaan_Resume.pdf"

sending:
  default_template: "warm"        # file in templates/ (no .txt)
  campaign: "default"             # label stamped on each send
  daily_cap: 40                   # max successful sends per run
  delay_min_seconds: 35           # randomized pause between sends
  delay_max_seconds: 90
  dry_run_output_dir: "output/preview"

followups:
  enabled: true
  steps:
    - { step: 1, template: "followup1", wait_days: 4 }
    - { step: 2, template: "followup2", wait_days: 7 }

verification:
  check_format: true              # always-on syntax check
  check_mx: true                  # verify the domain can receive mail (needs dnspython)
```

**Notes**

- An **empty section** (e.g. `sending:` with nothing under it) keeps the built-in
  defaults rather than wiping them — `deep_merge` handles this.
- `min_confidence: "Medium"` means blank/unknown confidence is treated as below
  Medium and excluded.
- `required_fields` uses the rendered variable names (`first_name`, `company`,
  `personalization` by default). Add `title` etc. to make them mandatory too.

---

## 10. CLI command reference

Global flags: `--config <path>` (default `config.yaml`), `--base-dir <dir>`
(default current directory), `--version`. Every command returns an integer exit
code (`0` success, `1` handled error, `2` argparse usage error).

| Command | Purpose | Sends mail? |
|---|---|---|
| `init` | create config, database, and folders | no |
| `import [path] [--source L]` | import/refresh contacts (upsert on email) | no |
| `contacts [--status][--search][--tag][--limit]` | list contacts | no |
| `show <id>` | one contact's full history | no |
| `set <id> [--status][--tag][--note][--personalization][--company][--title][--full-name][--confidence][--email]` | adjust a contact | no |
| `validate [--template]` | who's eligible / skipped and why | no |
| `preview [--template][--limit]` | render eligible emails to files | no |
| `test --to <addr> [--template][--limit][--no-attachment]` | send samples to yourself | **to you only** |
| `send [--template][--campaign][--daily-cap][--limit][--no-attachment][--yes]` | first-touch send | **yes** (confirms) |
| `followup [...same flags...][--force]` | send the next due follow-up step | **yes** (confirms) |
| `sync [--lookback-days N]` | read replies over IMAP, update contacts | no |
| `replies [--type reply\|auto_reply\|ooo\|bounce]` | list inbound mail | no |
| `status` | the dashboard | no |
| `export --out <file> [--status][--search][--tag]` | export filtered contacts to CSV | no |
| `templates` | list available templates | no |

### 10.1 Command details

- **`init`** — idempotent; honors `--config` (creates the config path you pass).
  Won't overwrite an existing config.
- **`import`** — upserts; prints `added / updated / skipped` and the first 15
  skip reasons. Re-run any time the sheet changes.
- **`validate`** — shows totals, the status breakdown, how many are *below
  confidence* (excluded before rendering), how many *would send*, and the first 25
  *skip* reasons (missing field, unresolved placeholder, etc.), plus follow-ups
  due. **Sends nothing.**
- **`preview`** — writes one `.txt` per eligible email to `output/preview/` plus a
  `preview_summary.csv` index. `--limit 0` writes zero (the limit is honored
  literally). **Sends nothing.**
- **`test`** — renders from *any* contact (regardless of status) and sends the
  samples to `--to` only; these are **not logged** and do not change any contact.
- **`send`** — first-touch only (`new`, confidence ≥ min, all required fields).
  Prints a summary and requires you to type `SEND` (skip with `--yes`). The
  effective cap is `daily_cap − already sent today`.
- **`followup`** — sends the next due step to `contacted` contacts who haven't
  replied/bounced. `--force` ignores `wait_days`. **Run `sync` first.**
- **`sync`** — read-only IMAP scan; prints counts of replies/auto/bounce/unmatched.
- **`set`** — the manual override for the database (status, tags, notes, hook,
  even the email). Returns `1` for a missing id.
- **`show`** — returns `1` for a missing id (scriptable).

---

## 11. The sending pipeline

A contact becomes a sent email only by passing through a strict pipeline. Nothing
short-circuits it.

```
 contacts ──select──▶ candidates ──prepare_jobs (the gatekeeper)──▶ Jobs ──send_jobs──▶ SMTP
                                   │                                       │
                                   ├─ confidence filter (initial only)     ├─ verified STARTTLS
                                   ├─ valid email format                   ├─ header sanitization
                                   ├─ in-run de-duplication                ├─ Date + Message-ID
                                   ├─ render template                      ├─ X-Mailmerge-* headers
                                   ├─ all required fields non-empty        ├─ résumé attachment
                                   ├─ no leftover {placeholders}           ├─ daily cap (successes only)
                                   └─ MX record exists (optional)          └─ 35–90s randomized pace
```

### 11.1 Selection

- **First-touch:** `select_initial()` returns `new` contacts whose confidence ≥
  `min_confidence`.
- **Follow-up:** `select_followups()` returns `contacted` contacts (excluding
  terminal statuses) whose next step is due — see [§13](#13-the-follow-up-engine).

### 11.2 The gatekeeper (`prepare_jobs`)

For each candidate it: checks email format → de-dupes within the run → renders the
template → confirms **every** `required_field` is non-empty → confirms **no**
`{placeholder}` is left unresolved → (optionally) confirms the domain has an MX
record. Any failure produces a *skip reason*, never a partial send. Survivors
become `Job` objects (`contact_id, email, company, name, step, template, subject,
body`).

### 11.3 Sending (`send_jobs`)

- Connects via **verified STARTTLS** (certificate + hostname checked).
- Builds each message with sanitized headers (CR/LF stripped so a stray newline
  in a name/subject can't inject headers), a real `Date`, a generated
  `Message-ID`, and `X-Mailmerge-Contact` / `X-Mailmerge-Step` headers used later
  to attribute replies.
- Attaches the résumé (PDF or DOCX) unless `--no-attachment`.
- **Daily cap counts successful sends only.** A failed send is recorded as
  `error`, does *not* consume the cap, and leaves the contact eligible to retry.
- Sleeps a random **35–90 s** between sends.
- In **test mode** (`force_to`), every email is redirected to your address and
  nothing is logged.

---

## 12. Templates and personalization

### 12.1 Format

A template is a plain-text file in `templates/`: a `Subject:` line, a blank line,
then the body.

```
Subject: would love to talk to {company}

Hi {first_name},

I'm interested in {company}'s work on {personalization}.

Best,
{my_name}
{my_phone} - {my_links}
```

### 12.2 Available placeholders

- **Contact:** `{first_name} {full_name} {company} {title} {email} {confidence}
  {personalization}`
- **Signature:** `{my_name} {my_phone} {my_links} {my_email}`
- **Any unmapped spreadsheet column**, slugified: a `Stage/Size` column becomes
  `{stage_size}`.

### 12.3 The guardrail

Rendering is **tolerant**: an unknown `{token}` is left in place, then detected by
`prepare_jobs`, which *skips* that row. This is why a literal `{personalization}`
can never go out — a missing hook either trips the required-field check or the
leftover-placeholder check.

### 12.4 Writing a good hook (`personalization`)

The hook fills "…your work on **___**" and "{company}'s work on **___**". It
should be a concrete noun phrase grounded in what the company actually builds:

- ✅ "your BVLOS delivery autopilot"
- ✅ "your low-power MLSoC for robotics"
- ❌ "your company" / "your great work" (generic = looks templated)

The four shipped templates: `warm` (relationship-first), `direct` (role-first),
`followup1` (gentle nudge), `followup2` (graceful last note).

---

## 13. The follow-up engine

### 13.1 Configuration

```yaml
followups:
  enabled: true
  steps:
    - { step: 1, template: "followup1", wait_days: 4 }
    - { step: 2, template: "followup2", wait_days: 7 }
```

`wait_days` is measured from the contact's **most recent** contact
(`last_contacted_at`).

### 13.2 How "due" is computed

`select_followups()` is **count-based**, not arithmetic-on-step-number:

1. Skip contacts who are not `contacted` (so anyone `replied`/`bounced`/etc. is
   excluded automatically).
2. `done = count of follow-up messages already sent` (steps ≥ 1, status `sent`).
3. If `done >= len(steps)`, the cadence is exhausted → skip.
4. Otherwise the next step is `steps_sorted[done]`. It's **due** if
   `--force`, or if `days_since(last_contacted_at) >= wait_days`.

This makes the cadence robust to non-contiguous step numbers (e.g. steps `1` and
`3`) and to manual status changes — a step is never skipped or repeated.

### 13.3 Operating rule

**Always `sync` before `followup`.** Otherwise a person who replied yesterday but
hasn't been ingested yet could receive a nudge. The whole point of reading
replies back is to never chase someone who already engaged.

---

## 14. Inbox sync and reply intelligence

`mailmerge sync` scans your inbox over IMAP and turns raw inbound mail into
structured, attributed `replies` rows + contact status changes.

### 14.1 Read-only by design

It uses `BODY.PEEK[]`, so reading a message **never sets the `\Seen` flag** —
your actual inbox is untouched. It only scans mail newer than
`imap.lookback_days`.

### 14.2 Classification (`classify`)

In order:

1. **Auto-reply / OOO** — if the message has `Auto-Submitted: auto…`,
   `X-Autoreply`/`X-Autorespond`, or an auto-ish subject. It's `ooo` when the
   subject matches a word-boundary out-of-office pattern (so "give**away**" does
   *not* count), else `auto_reply`.
2. **Bounce** — declared only on **strong** signals: a daemon sender
   (`mailer-daemon`, `postmaster`, …) or a `multipart/report` DSN. A subject-only
   bounce phrase ("address not found") is trusted **only if the message is not
   threaded** to one of our sent emails — so a genuine reply that quotes such a
   phrase is not mis-marked as a bounce.
3. **Reply** — everything else.

### 14.3 Attribution (`_match_contact`)

In precedence order:

1. **The actual sender** — if `From:` is a known contact, attribute to them
   (correctly handles a colleague replying on a forwarded thread).
2. **Thread** — match `In-Reply-To` / `References` against our stored
   `Message-ID`s.
3. **Bounce body** — for DSNs, scan the *entire* serialized message (including
   the `message/delivery-status` and embedded original) for the failed recipient,
   then match against known contacts.

Unmatched replies/bounces are counted but not stored (keeps the table clean).

### 14.4 Status effects

- `reply` → `mark_replied` (cadence stops).
- `bounce` → `mark_bounced` (cadence stops; never downgrades a prior `replied`).
- `auto_reply` / `ooo` → recorded only; the contact keeps its place in the cadence
  (an autoresponder is not a real reply).

### 14.5 De-duplication

Each message's dedupe key is `mailbox:uidvalidity:uid`. Including **UIDVALIDITY**
means that if the server renumbers UIDs (mailbox migration/recreation), old and
new keys won't collide and a genuinely new message won't be skipped.

---

## 15. Safety, deliverability, and guardrails

### 15.1 Built-in guardrails (always on)

| Guardrail | What it prevents |
|---|---|
| Default commands send nothing | accidental sends; only `send`/`followup` mail real people |
| Typed `SEND` confirmation | sending before you've reviewed the summary |
| Required-field + leftover-placeholder skip | a half-filled template going out |
| Never double-email | sending the same step twice (history-tracked) |
| Daily cap (successes only) | volume spikes that hurt deliverability |
| 35–90 s randomized pace | looking like a blaster |
| Confidence filter | emailing low-confidence/guessed addresses |
| MX check | emailing domains that can't receive mail (bounces) |
| Verified TLS (SMTP+IMAP) | leaking your app password to a MITM |
| Header sanitization | header injection via a malicious field |
| `BODY.PEEK` sync | disturbing your real inbox |

### 15.2 Deliverability best practices (operator responsibilities)

The tool protects your *account*; these protect your *reputation*:

- **Warm up.** Start at a low `daily_cap` (10–20) and ramp slowly.
- **Verify addresses** before sending — bounces are the fastest way to tank a
  sender reputation. Use the MX check plus a verifier for anything pattern-guessed.
- **Personalize genuinely.** Generic hooks read as spam to both humans and filters.
- **Honor opt-outs immediately** — `mailmerge set <id> --status unsubscribed`.
- **Send during business hours**, in the recipient's timezone where possible.
- **Keep volume sane.** This is 1:1 outreach; a few dozen high-quality emails a
  day beats hundreds of low-quality ones.
- Gmail's own SPF/DKIM/DMARC apply automatically when you send through their SMTP.

---

## 16. The research methodology (building the prospect list)

The prospect database is built by **grounded research**, not by asking a model to
recall company names (which hallucinates). The method:

1. **Niche fan-out.** Outreach targets are partitioned into ~26 niches adjacent to
   the candidate's profile (embedded/IoT/firmware/hardware/robotics/autonomy):
   warehouse & logistics robotics, humanoid robots, autonomous vehicles,
   drones/UAV, lidar/radar/sensors, edge-AI silicon & semiconductors, industrial
   IoT, power electronics & EV, batteries & energy, agtech robotics, medical
   devices & surgical robotics, space/satellites, defense/aerospace,
   AR/VR/wearables, manufacturing automation & 3D printing, maritime/underwater
   robotics, construction & field robotics, last-mile delivery robots, climate
   hardware, networking/RF hardware, smart-home IoT, quantum hardware, lab
   automation, inspection/utility robotics, autonomous trucking, and
   kitchen/retail/service robotics.

2. **Grounded extraction.** A research agent per niche searches the web for real
   listing pages (Y Combinator company directory, Built In SF, Wellfound,
   Tracxn, Crunchbase lists, curated articles), fetches the most promising ones,
   and extracts the company name, Bay Area city, what they build, and funding
   stage. **Every company carries a source URL.** Companies that can't be tied to
   a real source, or that aren't headquartered in the Bay Area, are omitted; large
   public incumbents (Tesla, Apple, NVIDIA, …) are excluded in favor of
   startups/scaleups.

3. **De-duplication.** Companies are normalized (lowercased, punctuation and
   common suffixes stripped) and de-duped across niches.

4. **Hook generation.** Each company gets a `personalization` hook in the
   candidate's voice, grounded in the company's actual product.

5. **Backfill.** If the unique count is under target, additional broad-net rounds
   run with an "already-found" exclusion list until the target is reached or
   returns diminish.

6. **Output.** Results are written to `data/prospects_bayarea.csv` in the tool's
   schema, with `Email` / `Email Confidence` / `Contact Name` / `Contact Title`
   **left blank** and a `Source URL` column preserved.

### 16.1 What is and isn't guaranteed

- ✅ Company names, locations, and focus are **grounded in real sources**.
- ⚠️ Funding stage and current operating status should be **spot-checked** — the
  startup landscape changes weekly.
- ❌ Emails and named contacts are **not fabricated**. You must source and verify
  them (next section) before any real send. Because the tool skips blank-email
  rows, an un-enriched prospect can never be emailed by accident.

---

## 17. Email-finding and verification

A prospect row is not sendable until it has a verified email. The workflow:

1. **Find a named contact.** For each target company, identify a relevant person
   (hiring manager, eng lead, founder) via the company site / LinkedIn. Put their
   name and title in `Contact Name` / `Contact Title`.
2. **Find the email.** Common company patterns (`first@`, `first.last@`,
   `flast@`), or a finder tool (Hunter, Apollo, Clearbit, RocketReach).
3. **Verify it.** Run it through a verifier (NeverBounce, ZeroBounce) and/or rely
   on the tool's MX check. Set `Email Confidence` to `High` / `Medium` / `Low`
   based on how it was obtained (verified-direct = High, pattern-guessed = Low).
4. **Re-import.** `mailmerge import` upserts the enriched row; your manual edits
   to the hook/notes survive because empty cells never overwrite.

`min_confidence: "Medium"` means pattern-guessed `Low` addresses are held back
until you upgrade them — a deliberate bounce-prevention default.

---

## 18. The outreach playbook

A job-search-specific strategy layered on top of the tool.

### 18.1 Targeting

- Start from the niches you're genuinely strong in and excited about (for this
  operator: embedded/IoT/firmware, autonomy, field-deployed hardware).
- Prefer **startups/scaleups** where a single email reaches someone who can act
  (founder, eng lead) over large companies routed through an ATS.
- Tag tiers: `mailmerge set <id> --tag dream` for top targets so you can run
  `mailmerge contacts --tag dream` and treat them with extra care.

### 18.2 The message

- **Subject:** specific and personal ("Embedded/IoT engineer (Penn MS) — would
  love to talk to {company}").
- **First line:** about *them*, not you — the hook.
- **Body:** who you are in one or two credible lines (Penn MS, Somo AI, Cell
  Propulsion → field deployments), one specific reason this company, one clear ask
  (a 20-minute conversation), résumé attached.
- **Length:** short. They're busy.

### 18.3 Cadence

- Step 0 (`warm` or `direct`) → wait 4 days → step 1 (`followup1`) → wait 7 days →
  step 2 (`followup2`, graceful last note). Then stop.
- **Never** more than the configured steps. Three touches over ~two weeks is
  polite; more is pestering.
- Always `sync` before each `followup` run so repliers are excluded.

### 18.4 Handling replies

- `mailmerge replies --type reply` to see who responded; reply personally and
  promptly (the tool stops the automated cadence for them automatically).
- `--type bounce` to find addresses to fix or drop.
- `--type ooo` / `auto_reply` are informational — those people stay in the cadence.

### 18.5 Etiquette

- One person per company unless you have a reason for more.
- Respect "no" and "not now" — `set --status do_not_contact`.
- Keep daily volume low and quality high.

---

## 19. Daily and weekly operating routine

**Daily (10 minutes):**

```bash
mailmerge sync                 # 1. pull in replies/bounces FIRST
mailmerge status               # 2. see reply rate, queue, who's due
mailmerge followup             # 3. send due nudges (repliers auto-excluded)
mailmerge send --daily-cap 20  # 4. start a few new threads
mailmerge replies --type reply # 5. respond to humans personally
```

**Weekly:**

- Enrich new prospects (find + verify emails), then `mailmerge import`.
- Review `mailmerge contacts --status bounced` and fix/drop.
- Review `--status replied` and make sure every warm reply got a human follow-up.
- Adjust templates if your reply rate is low.

---

## 20. Testing

```bash
pip install -e ".[dev]"
pytest -q          # 282 passed
```

The suite is **fully offline** — no network, no real mail — using fixtures with
dummy data and in-memory `FakeSMTP` / `FakeIMAP` transports (`tests/fakedata.py`).

| Area | What's covered |
|---|---|
| `test_utils` | slug, email/name parsing, dates, deep-merge, tables |
| `test_config` | defaults, YAML merge, empty sections, path resolution |
| `test_db` | upsert/lifecycle, filters, messages, replies, aggregates |
| `test_contacts_import` | CSV/TSV import, dedupe, extra columns, idempotency |
| `test_templates` | loading, tolerant render, variables, leftovers |
| `test_pipeline` | confidence/required-field/MX gating, follow-up cadence |
| `test_inbox` | classify matrix, body extraction, matching precedence |
| `test_sync` | full IMAP sync via FakeIMAP (classify/match/dedupe/UIDVALIDITY) |
| `test_sender` | cap accounting, failed-send handling, header sanitization |
| `test_reporting` | dashboard + per-contact history |
| `test_context` | path resolution, db creation |
| `test_cli` | every subcommand end-to-end via `main()` |

The suite is also a **specification**: each guardrail in [§15](#15-safety-deliverability-and-guardrails)
has a corresponding regression test.

---

## 21. Security and privacy

- **Credentials never touch disk.** Passwords come only from environment
  variables; nothing logs or writes them.
- **Encrypted, verified transport.** SMTP STARTTLS and IMAP SSL both use a
  verifying SSL context (certificate + hostname), defeating MITM password theft.
- **Header injection is blocked** — all header-bound fields are CR/LF-sanitized.
- **Secrets and data are git-ignored:** `.env`, `config.yaml`, `data/*.csv`
  (except the example), `data/*.db`, and `resume/*` never enter version control.
- **Inbox reads are non-destructive** (`BODY.PEEK`).
- **Your data is local.** The SQLite file lives on your machine; back it up by
  copying the file.

---

## 22. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `No email password found in environment variable …` | `export EMAIL_APP_PASSWORD=…` (Gmail App Password, not your login password). |
| SMTP `535` auth error | Use an **App Password**; ensure 2-Step Verification is on. |
| `(no config at …; using built-in defaults)` on stderr | You ran without `config.yaml`; fine for `validate`/`preview`. Run `mailmerge init` to create one. |
| Everyone is skipped at `validate` | Missing `personalization`, blank confidence with `min_confidence: Medium`, or unmapped columns. Read the skip reasons. |
| `import` says "Contacts file not found" | Wrong path; pass it explicitly or set `contacts.path`. |
| `.xlsx` import errors | `pip install pandas openpyxl`, or save as `.csv`. |
| MX checks always pass | `dnspython` not installed (`pip install dnspython`). |
| `sync` finds nothing | Increase `--lookback-days`; confirm IMAP host/port and that the app password has IMAP access. |
| A real reply got marked bounced | Fixed in v1.0 (threaded bounce-phrase guard); ensure you're on the latest. |
| Follow-up went to someone who replied | You skipped `sync` before `followup`. Always sync first. |
| `python3` can't read files under `~/Desktop` (macOS) | TCC: grant Full Disk Access to your terminal/host app, or work outside `~/Desktop`. |

---

## 23. Extensibility and roadmap

The architecture is built to extend cleanly:

- **Email-finder integration.** `verification.provider` is reserved for wiring a
  paid verifier/finder; add a module and call it from `pipeline`/`contacts`.
- **HTML emails.** `build_message` currently sends `text/plain`; add an
  `add_alternative(..., subtype='html')` path and an HTML template variant.
- **Scheduling.** Wrap `sync` + `followup` in a cron/launchd job (the CLI is
  exit-code clean and `--yes` enables unattended runs).
- **A web UI / dashboard.** `reporting.py` already computes the metrics; a small
  Flask/FastAPI layer over `db.py` would give a browser view.
- **More channels.** The contact/message/reply model generalizes beyond email.
- **CRM export.** `export` already dumps filtered contacts; add a sync to a CRM if
  needed.

---

## Appendix A: full schema DDL

```sql
CREATE TABLE contacts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    email             TEXT    NOT NULL UNIQUE,
    first_name        TEXT    DEFAULT '',
    full_name         TEXT    DEFAULT '',
    company           TEXT    DEFAULT '',
    title             TEXT    DEFAULT '',
    confidence        TEXT    DEFAULT '',
    personalization   TEXT    DEFAULT '',
    status            TEXT    NOT NULL DEFAULT 'new',
    tags              TEXT    DEFAULT '',
    notes             TEXT    DEFAULT '',
    source            TEXT    DEFAULT '',
    extra             TEXT    DEFAULT '{}',
    last_step         INTEGER NOT NULL DEFAULT -1,
    last_contacted_at TEXT,
    replied_at        TEXT,
    bounced_at        TEXT,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);

CREATE TABLE messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id  INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    campaign    TEXT    DEFAULT '',
    template    TEXT    DEFAULT '',
    step        INTEGER NOT NULL DEFAULT 0,
    subject     TEXT    DEFAULT '',
    body        TEXT    DEFAULT '',
    message_id  TEXT,
    status      TEXT    NOT NULL,
    error       TEXT    DEFAULT '',
    sent_at     TEXT    NOT NULL
);

CREATE TABLE replies (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id     INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    uid            TEXT UNIQUE,
    message_id     TEXT,
    in_reply_to    TEXT,
    from_addr      TEXT,
    subject        TEXT,
    snippet        TEXT,
    classification TEXT,
    received_at    TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE meta ( key TEXT PRIMARY KEY, value TEXT );

CREATE INDEX idx_contacts_status  ON contacts(status);
CREATE INDEX idx_messages_contact ON messages(contact_id);
CREATE INDEX idx_messages_msgid   ON messages(message_id);
CREATE INDEX idx_replies_contact  ON replies(contact_id);
```

---

## Appendix B: glossary

- **DSN** — Delivery Status Notification; the `multipart/report` bounce a mail
  server sends when delivery fails.
- **UIDVALIDITY** — an IMAP value that changes when a mailbox's UIDs are
  renumbered; included in our dedupe key.
- **STARTTLS** — upgrades a plaintext SMTP connection to TLS; we verify the cert.
- **MX record** — the DNS record that says which server receives a domain's mail.
- **Step** — cadence position; 0 = first touch, 1.. = follow-ups.
- **Terminal status** — `replied`/`bounced`/`unsubscribed`/`do_not_contact`; ends
  all automated outreach for a contact.
- **Hook** — the `personalization` phrase that makes an email specific to a
  company.

---

## Appendix C: FAQ

**Can this send to a whole list at once?**
No — by design. It sends individually, throttled and capped.

**Will re-importing my spreadsheet wipe my edits?**
No. Empty cells never overwrite existing values; history is preserved.

**What if a send fails midway?**
That message is logged as `error`, doesn't consume the daily cap, and the contact
stays eligible — just run `send` again.

**How do I stop emailing someone?**
`mailmerge set <id> --status do_not_contact` (or `unsubscribed`).

**Does it read my whole inbox?**
Only mail newer than `imap.lookback_days`, read-only (`BODY.PEEK`), and it only
stores messages it can tie to a contact.

**Can I run it unattended (cron)?**
Yes — `send`/`followup` accept `--yes` to skip the typed confirmation, and the
CLI returns clean exit codes.

**Where's my data?**
In `data/mailmerge.db` on your machine. Back it up by copying the file. It is
git-ignored.

---

*Generated as part of the mailmerge operation. For the quick version, see
[QUICKSTART.md](../QUICKSTART.md); for the overview, see [README.md](../README.md).*
