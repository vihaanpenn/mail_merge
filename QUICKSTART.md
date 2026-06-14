# Quickstart

```bash
# 0. install (core deps; add extras with: pip install -e ".[all]")
pip install -r requirements.txt
pip install -e .                      # gives you the `mailmerge` command

# 1. scaffold config, database, and folders
mailmerge init                        # creates config.yaml from the example

# 2. edit config.yaml  -> your name, email, smtp/imap hosts, resume path
#    then set your Gmail App Password (https://myaccount.google.com/apppasswords)
export EMAIL_APP_PASSWORD='your-16-char-app-password'      # macOS/Linux
#   $env:EMAIL_APP_PASSWORD='your-16-char-app-password'    # Windows PowerShell

# 3. load your list into the database (re-run any time it changes)
cp data/contacts.example.csv data/contacts.csv   # then fill it in
mailmerge import data/contacts.csv

# 4. walk these IN ORDER
mailmerge validate                    # who's eligible / who's skipped and why
mailmerge preview                     # render every email to output/preview/
mailmerge test --to you@gmail.com     # send yourself a sample
mailmerge send                        # real send (type SEND to confirm)

# 5. ongoing
mailmerge sync                        # read replies back, classify, mark contacts
mailmerge followup                    # send the next step to anyone who's due
mailmerge status                      # the dashboard
```

**Key things to know**

- **`send` is the only command that mails real people**, and it asks you to type
  `SEND` first. `preview` writes files; `test` only mails you.
- The **SQLite database** (`data/mailmerge.db`) is the source of truth. Re-import
  your sheet whenever it changes — it upserts on email and never blanks out data
  you've already filled in, so your edits and send history are preserved.
- A contact is **skipped** unless it has a first name, a valid email,
  confidence ≥ Medium, and a filled-in **Personalization** hook.
- It **never double-emails** and caps sends per run (default 40). Re-run over a
  few days to work through a large list.
- **Follow-ups never chase** anyone who replied or bounced. Run `mailmerge sync`
  before `mailmerge followup` so replies are accounted for first.

Full details: see [README.md](README.md).
