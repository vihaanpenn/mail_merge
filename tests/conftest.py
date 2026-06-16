"""Shared pytest fixtures: a tmp project dir, a database, and a Context."""

from __future__ import annotations

import pytest

from mailmerge.config import DEFAULT_CONFIG, Config
from mailmerge.context import Context
from mailmerge.db import Database
from mailmerge.utils import deep_merge

WARM = (
    "Subject: would love to talk to {company}\n\n"
    "Hi {first_name}, I'm interested in {company}'s work on {personalization}.\n"
    "Best,\n{my_name}\n"
)
FOLLOWUP1 = (
    "Subject: re: {company}\n\nHi {first_name}, gentle nudge on {personalization}.\n{my_name}\n"
)
FOLLOWUP2 = (
    "Subject: last note {company}\n\nHi {first_name}, last one re {personalization}.\n{my_name}\n"
)


@pytest.fixture
def base_dir(tmp_path):
    (tmp_path / "templates").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "templates" / "warm.txt").write_text(WARM, encoding="utf-8")
    (tmp_path / "templates" / "followup1.txt").write_text(FOLLOWUP1, encoding="utf-8")
    (tmp_path / "templates" / "followup2.txt").write_text(FOLLOWUP2, encoding="utf-8")
    return tmp_path


@pytest.fixture
def ctx(base_dir):
    cfg = Config(deep_merge(DEFAULT_CONFIG, {
        "verification": {"check_mx": False},  # no network in tests
        "sender": {"name": "Vihaan", "email": "me@example.com"},
        "database": {"path": "data/test.db"},
    }))
    db = Database(base_dir / "data" / "test.db")
    context = Context(base_dir, cfg, db)
    yield context
    context.close()


def add_contact(ctx, **overrides):
    """Insert a contact with sensible defaults; returns its id."""
    fields = {
        "email": "ada@drone.co",
        "first_name": "Ada",
        "full_name": "Ada Lovelace",
        "company": "Drone Co",
        "title": "VP Eng",
        "confidence": "High",
        "personalization": "your flight-control stack",
    }
    fields.update(overrides)
    cid, _ = ctx.db.upsert_contact(fields)
    return cid


PROJECT_CONFIG = """
sender:
  name: "Vihaan Ravishankar"
  email: "me@example.com"
  phone: "+1 (555) 010-0101"
  links: "linkedin.com/in/vihaan - vihaan.dev"
auth:
  smtp_host: "smtp.example.com"
  smtp_port: 587
  password_env: "TEST_SMTP_PASSWORD"
imap:
  enabled: true
  host: "imap.example.com"
  password_env: "TEST_SMTP_PASSWORD"
database:
  path: "data/mailmerge.db"
verification:
  check_format: true
  check_mx: false
"""


@pytest.fixture
def project(tmp_path):
    """A complete on-disk project (config + templates + contacts.csv) for CLI tests."""
    from tests.fakedata import CONTACTS_CSV

    (tmp_path / "templates").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "templates" / "warm.txt").write_text(WARM, encoding="utf-8")
    (tmp_path / "templates" / "direct.txt").write_text(WARM.replace("would love", "want"), encoding="utf-8")
    (tmp_path / "templates" / "followup1.txt").write_text(FOLLOWUP1, encoding="utf-8")
    (tmp_path / "templates" / "followup2.txt").write_text(FOLLOWUP2, encoding="utf-8")
    (tmp_path / "config.yaml").write_text(PROJECT_CONFIG, encoding="utf-8")
    (tmp_path / "data" / "contacts.csv").write_text(CONTACTS_CSV, encoding="utf-8")
    return tmp_path


def run_cli(project_dir, *args):
    """Invoke the CLI exactly like the console entry point. Returns the exit code."""
    from mailmerge.cli import main

    return main(["--base-dir", str(project_dir), "--config", "config.yaml", *args])
