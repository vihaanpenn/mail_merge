"""Small, dependency-free helpers shared across the package."""

from __future__ import annotations

import copy
import datetime as dt
import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PLACEHOLDER_RE = re.compile(r"\{[a-z0-9_]+\}")

# High > Medium > Low > blank. Used to filter out low-quality addresses.
CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1, "": 0}

HONORIFICS = {
    "mr", "mrs", "ms", "dr", "prof", "sir", "madam",
    "mr.", "mrs.", "ms.", "dr.", "prof.",
}


def now_iso() -> str:
    """Current local time as a stable, sortable ISO-8601 string (no micros)."""
    return dt.datetime.now().isoformat(timespec="seconds")


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def days_since(value: str | None, *, reference: dt.datetime | None = None) -> float | None:
    """Whole-and-fractional days elapsed since an ISO timestamp, or None."""
    parsed = parse_iso(value)
    if parsed is None:
        return None
    ref = reference or dt.datetime.now()
    return (ref - parsed).total_seconds() / 86400.0


def slug(text: object) -> str:
    """Normalize an arbitrary string (e.g. a column header) into a variable name."""
    lowered = re.sub(r"[^a-z0-9]+", "_", str(text).lower())
    return re.sub(r"_+", "_", lowered).strip("_")


def normalize_email(addr: object) -> str:
    return ("" if addr is None else str(addr)).strip().lower()


def valid_email_format(addr: str) -> bool:
    return bool(EMAIL_RE.match(addr or ""))


def first_name_from(full_name: str) -> str:
    """Best-effort first name, skipping a leading honorific."""
    tokens = (full_name or "").split()
    if not tokens:
        return ""
    if len(tokens) > 1 and tokens[0].lower() in HONORIFICS:
        return tokens[1]
    return tokens[0]


def unresolved_placeholders(*texts: str) -> list[str]:
    """Return any leftover {placeholders} across the given rendered texts."""
    found: set[str] = set()
    for text in texts:
        found.update(PLACEHOLDER_RE.findall(text or ""))
    return sorted(found)


def confidence_rank(value: str | None) -> int:
    return CONFIDENCE_RANK.get((value or "").strip().lower(), 0)


def truncate(text: object, length: int = 80) -> str:
    text = " ".join(str(text if text is not None else "").split())
    return text if len(text) <= length else text[: length - 1] + "…"


def deep_merge(base: dict, override: dict | None) -> dict:
    """Recursively merge ``override`` into a copy of ``base``.

    A ``None`` override for a key whose default is a dict is ignored, so an empty
    YAML section (e.g. ``sending:`` with nothing under it, which parses to None)
    keeps the built-in defaults instead of wiping the whole section.

    The result shares no mutable state with ``base`` (deep-copied), so mutating a
    loaded config can never corrupt the module-level defaults.
    """
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(out.get(key), dict):
            if value is None:
                continue
            if isinstance(value, dict):
                out[key] = deep_merge(out[key], value)
                continue
        out[key] = copy.deepcopy(value)
    return out


def render_table(headers: list[str], rows: list[list[object]]) -> str:
    """A tiny, dependency-free fixed-width table renderer for CLI output."""
    cols = [str(h) for h in headers]
    str_rows = [[("" if c is None else str(c)) for c in row] for row in rows]
    widths = [len(c) for c in cols]
    for row in str_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(row: list[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))

    sep = "  ".join("-" * w for w in widths)
    lines = [fmt(cols), sep]
    lines.extend(fmt(row) for row in str_rows)
    return "\n".join(lines)
