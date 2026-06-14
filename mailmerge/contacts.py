"""Import contacts from a spreadsheet into the database (idempotent upsert).

Re-running an import is safe and is the intended way to keep the database in
sync with an evolving list: each row upserts on email, send/reply history is
preserved, and any non-standard columns are kept verbatim in `extra`.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .db import Database
from .utils import first_name_from, normalize_email, slug, valid_email_format


@dataclass
class ImportResult:
    added: int = 0
    updated: int = 0
    skipped: int = 0
    skip_reasons: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.added + self.updated + self.skipped


def _read_rows(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv", ".txt"):
        delim = "\t" if suffix == ".tsv" else ","
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            return [dict(r) for r in csv.DictReader(fh, delimiter=delim)]
    if suffix in (".xlsx", ".xlsm", ".xls"):
        try:
            import pandas as pd  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on env
            raise RuntimeError(
                "Reading .xlsx requires pandas + openpyxl:\n"
                "  pip install pandas openpyxl\n"
                "or save your sheet as .csv."
            ) from exc
        df = pd.read_excel(path, dtype=str).fillna("")
        return df.to_dict(orient="records")
    raise RuntimeError(f"Unsupported contacts format: {suffix} (use .csv or .xlsx)")


def row_to_contact(row: dict, columns: dict) -> dict:
    """Map one spreadsheet row to the contact-field shape `upsert_contact` wants."""
    def col(logical: str) -> str:
        header = columns.get(logical)
        if header is None:
            return ""
        value = row.get(header)
        return "" if value is None else str(value).strip()

    full_name = col("name")
    known_headers = {h for h in columns.values() if h}
    extra = {
        k: ("" if v is None else str(v).strip())
        for k, v in row.items()
        if k not in known_headers and k is not None
    }

    return {
        "email": normalize_email(col("email")),
        "first_name": first_name_from(full_name),
        "full_name": full_name,
        "company": col("company"),
        "title": col("title"),
        "confidence": col("confidence"),
        "personalization": col("personalization"),
        "extra": extra,
    }


def import_file(db: Database, path: Path, columns: dict, *, source: str = "") -> ImportResult:
    rows = _read_rows(path)
    result = ImportResult()
    source = source or path.name

    for i, row in enumerate(rows, start=1):
        contact = row_to_contact(row, columns)
        ident = contact["email"] or contact["company"] or f"row {i}"
        email = contact["email"]

        if not email:
            result.skipped += 1
            result.skip_reasons.append((ident, "no email address"))
            continue
        if not valid_email_format(email):
            result.skipped += 1
            result.skip_reasons.append((ident, f"invalid email format: {email}"))
            continue

        contact["source"] = source
        _, created = db.upsert_contact(contact)
        if created:
            result.added += 1
        else:
            result.updated += 1

    return result
