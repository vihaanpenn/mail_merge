"""Configuration loading: built-in defaults merged with an optional YAML file.

Passwords are never read from config; they come from environment variables named
by ``auth.password_env`` / ``imap.password_env`` and are resolved at run time.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from .utils import deep_merge

DEFAULT_CONFIG: dict[str, Any] = {
    "sender": {
        "name": "Your Name",
        "email": "you@example.com",
        "phone": "+1 (555) 555-5555",
        "links": "linkedin.com/in/you - yourportfolio.com",
    },
    "auth": {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        # Password is read from this environment variable, never stored on disk.
        "password_env": "EMAIL_APP_PASSWORD",
    },
    "imap": {
        # Reading replies back is optional; leave enabled to use `mailmerge sync`.
        "enabled": True,
        "host": "imap.gmail.com",
        "port": 993,
        "mailbox": "INBOX",
        # Defaults to the SMTP app password if this env var is unset.
        "password_env": "EMAIL_APP_PASSWORD",
        # Only scan messages newer than this many days when syncing.
        "lookback_days": 30,
    },
    "database": {
        "path": "data/mailmerge.db",
    },
    "contacts": {
        # Default source spreadsheet for `mailmerge import`.
        "path": "data/contacts.csv",
        "columns": {
            "company": "Company Name",
            "name": "Contact Name",
            "title": "Contact Title",
            "email": "Email",
            "confidence": "Email Confidence",
            "personalization": "Personalization",
        },
        # A contact is not eligible to send unless ALL of these render non-empty.
        "required_fields": ["first_name", "company", "personalization"],
        # Skip contacts below this email-confidence level (High > Medium > Low).
        "min_confidence": "Medium",
    },
    "resume": {
        "path": "resume/Your_Resume.pdf",
    },
    "sending": {
        "default_template": "warm",
        "campaign": "default",
        "daily_cap": 40,
        "delay_min_seconds": 35,
        "delay_max_seconds": 90,
        "dry_run_output_dir": "output/preview",
    },
    "followups": {
        # Each step beyond the initial email. `wait_days` is measured from the
        # most recent contact. A contact that replied or bounced is never chased.
        "enabled": True,
        "steps": [
            {"step": 1, "template": "followup1", "wait_days": 4},
            {"step": 2, "template": "followup2", "wait_days": 7},
        ],
    },
    "verification": {
        "check_format": True,
        "check_mx": True,
    },
}


class Config(dict):
    """A dict subclass with a couple of convenience accessors and a source path."""

    source: Path | None = None

    def section(self, name: str) -> dict:
        return self.get(name, {})

    def resolve(self, base_dir: Path, path: str | os.PathLike) -> Path:
        p = Path(path)
        return p if p.is_absolute() else (base_dir / p)

    def password(self, env_var: str) -> str | None:
        return os.environ.get(env_var)


def load_config(path: str | os.PathLike, base_dir: Path) -> Config:
    """Load config from ``path`` (relative to ``base_dir``), merged over defaults."""
    cfg = Config(DEFAULT_CONFIG)
    p = Path(path)
    if not p.is_absolute():
        p = base_dir / p

    if p.exists():
        try:
            import yaml  # type: ignore
        except ImportError:
            sys.exit(
                "Found a config file but PyYAML is not installed.\n"
                "  pip install pyyaml\n"
                "(or remove the config to run on built-in defaults)."
            )
        with open(p, "r", encoding="utf-8") as fh:
            user_cfg = yaml.safe_load(fh) or {}
        cfg = Config(deep_merge(DEFAULT_CONFIG, user_cfg))
        cfg.source = p
    else:
        print(f"(no config at {p}; using built-in defaults)", file=sys.stderr)
        cfg.source = None
    return cfg
