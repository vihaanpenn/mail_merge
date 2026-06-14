"""Plain-text template loading and tolerant {placeholder} rendering.

A template file is: a ``Subject: ...`` first line, a blank line, then the body.
Every contact field, every spreadsheet column (slugified), and the sender's
signature fields are exposed as ``{placeholders}``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .utils import first_name_from, slug, unresolved_placeholders


class TemplateError(Exception):
    pass


class _Tolerant(dict):
    """A format_map dict that leaves unknown {keys} intact for later detection."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def list_templates(templates_dir: Path) -> list[str]:
    return sorted(t.stem for t in templates_dir.glob("*.txt"))


def load_template(name: str, templates_dir: Path) -> tuple[str, str]:
    path = templates_dir / f"{name}.txt"
    if not path.exists():
        avail = ", ".join(list_templates(templates_dir)) or "(none)"
        raise TemplateError(
            f"Template '{name}' not found at {path}\nAvailable templates: {avail}"
        )
    text = path.read_text(encoding="utf-8")
    subject_line, _, body = text.partition("\n")
    if not subject_line.lower().startswith("subject:"):
        raise TemplateError(
            f"Template {path.name} must begin with a 'Subject: ...' line, "
            f"then a blank line, then the body."
        )
    subject = subject_line.split(":", 1)[1].strip()
    return subject, body.lstrip("\n")


def render(part: str, variables: dict[str, str]) -> str:
    return part.format_map(_Tolerant(variables))


def build_variables(contact: sqlite3.Row | dict, sender: dict) -> dict[str, str]:
    """Assemble the placeholder dictionary for a single contact."""
    if isinstance(contact, sqlite3.Row):
        contact = dict(contact)

    variables: dict[str, str] = {}

    # Arbitrary spreadsheet columns preserved in `extra` become {slug} vars.
    extra = contact.get("extra")
    if isinstance(extra, str):
        try:
            extra = json.loads(extra or "{}")
        except json.JSONDecodeError:
            extra = {}
    for key, value in (extra or {}).items():
        variables[slug(key)] = "" if value is None else str(value).strip()

    full_name = (contact.get("full_name") or "").strip()
    first = (contact.get("first_name") or "").strip() or first_name_from(full_name)

    variables.update({
        "full_name": full_name,
        "first_name": first,
        "company": (contact.get("company") or "").strip(),
        "title": (contact.get("title") or "").strip(),
        "email": (contact.get("email") or "").strip(),
        "confidence": (contact.get("confidence") or "").strip(),
        "personalization": (contact.get("personalization") or "").strip(),
        "my_name": sender.get("name", ""),
        "my_phone": sender.get("phone", ""),
        "my_links": sender.get("links", ""),
        "my_email": sender.get("email", ""),
    })
    return variables


def render_email(
    template_name: str,
    templates_dir: Path,
    contact: sqlite3.Row | dict,
    sender: dict,
) -> tuple[str, str, list[str]]:
    """Render (subject, body) for a contact and return any leftover placeholders."""
    subject_tpl, body_tpl = load_template(template_name, templates_dir)
    variables = build_variables(contact, sender)
    subject = render(subject_tpl, variables)
    body = render(body_tpl, variables)
    leftovers = unresolved_placeholders(subject, body)
    return subject, body, leftovers
