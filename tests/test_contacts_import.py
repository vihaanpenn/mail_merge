import json

import pytest

from mailmerge.contacts import ImportResult, import_file, row_to_contact
from tests.fakedata import COLUMNS, CONTACTS_CSV


def write_csv(base_dir, text=CONTACTS_CSV, name="contacts.csv"):
    path = base_dir / "data" / name
    path.write_text(text, encoding="utf-8")
    return path


# -- row_to_contact --------------------------------------------------------

def test_row_to_contact_maps_fields():
    row = {"Company Name": "Nimbus Robotics", "Contact Name": "Dr. Ada Lovelace",
           "Contact Title": "VP", "Email": "ADA@Nimbus.IO ", "Email Confidence": "High",
           "Personalization": "arms", "Region": "Bay Area", "Stage": "Series B"}
    c = row_to_contact(row, COLUMNS)
    assert c["email"] == "ada@nimbus.io"
    assert c["first_name"] == "Ada"
    assert c["company"] == "Nimbus Robotics"
    assert c["extra"] == {"Region": "Bay Area", "Stage": "Series B"}


def test_row_to_contact_handles_missing_columns():
    c = row_to_contact({"Email": "a@b.co"}, COLUMNS)
    assert c["email"] == "a@b.co" and c["company"] == ""


def test_row_to_contact_strips_whitespace():
    c = row_to_contact({"Email": "  a@b.co  ", "Company Name": "  Co  "}, COLUMNS)
    assert c["email"] == "a@b.co" and c["company"] == "Co"


# -- import_file -----------------------------------------------------------

def test_import_counts(ctx, base_dir):
    result = import_file(ctx.db, write_csv(base_dir), COLUMNS)
    # 9 rows: 4 good + 2 hook/conf rows imported + 1 dup update; 2 skipped (no email, bad email)
    assert isinstance(result, ImportResult)
    assert result.skipped == 2
    assert result.added >= 4
    assert result.updated >= 1


def test_import_skip_reasons(ctx, base_dir):
    result = import_file(ctx.db, write_csv(base_dir), COLUMNS)
    reasons = " ".join(r for _, r in result.skip_reasons)
    assert "no email" in reasons and "invalid email" in reasons


def test_import_dedupes_on_email(ctx, base_dir):
    import_file(ctx.db, write_csv(base_dir), COLUMNS)
    # the two Nimbus rows share ada@nimbusrobotics.io -> one contact
    nimbus = ctx.db.get_contact_by_email("ada@nimbusrobotics.io")
    assert nimbus is not None
    assert len([c for c in ctx.db.list_contacts() if c["email"] == "ada@nimbusrobotics.io"]) == 1


def test_import_normalizes_uppercase_email(ctx, base_dir):
    import_file(ctx.db, write_csv(base_dir), COLUMNS)
    assert ctx.db.get_contact_by_email("katherine@orbitedge.ai") is not None


def test_import_preserves_extra_columns(ctx, base_dir):
    import_file(ctx.db, write_csv(base_dir), COLUMNS)
    extra = json.loads(ctx.db.get_contact_by_email("ada@nimbusrobotics.io")["extra"])
    assert extra.get("Region") == "Bay Area" and extra.get("Stage") == "Series B"


def test_reimport_is_idempotent(ctx, base_dir):
    path = write_csv(base_dir)
    import_file(ctx.db, path, COLUMNS)
    n1 = ctx.db.totals()["contacts"]
    result = import_file(ctx.db, path, COLUMNS)
    assert result.added == 0
    assert ctx.db.totals()["contacts"] == n1


def test_import_records_source(ctx, base_dir):
    import_file(ctx.db, write_csv(base_dir), COLUMNS, source="june-list")
    assert ctx.db.get_contact_by_email("ada@nimbusrobotics.io")["source"] == "june-list"


def test_import_default_source_is_filename(ctx, base_dir):
    import_file(ctx.db, write_csv(base_dir, name="leads.csv"), COLUMNS)
    assert ctx.db.get_contact_by_email("ada@nimbusrobotics.io")["source"] == "leads.csv"


def test_import_tsv(ctx, base_dir):
    tsv = "Email\tCompany Name\tContact Name\nzed@tsv.co\tTSV Co\tZed Zee\n"
    path = base_dir / "data" / "contacts.tsv"
    path.write_text(tsv, encoding="utf-8")
    result = import_file(ctx.db, path, COLUMNS)
    assert result.added == 1
    assert ctx.db.get_contact_by_email("zed@tsv.co")["company"] == "TSV Co"


def test_import_unsupported_format(ctx, base_dir):
    path = base_dir / "data" / "contacts.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError):
        import_file(ctx.db, path, COLUMNS)


def test_import_empty_file(ctx, base_dir):
    path = base_dir / "data" / "empty.csv"
    path.write_text("Email,Company Name\n", encoding="utf-8")
    result = import_file(ctx.db, path, COLUMNS)
    assert result.total == 0


def test_import_result_total(ctx, base_dir):
    result = import_file(ctx.db, write_csv(base_dir), COLUMNS)
    assert result.total == result.added + result.updated + result.skipped


def test_import_manual_edit_survives_reimport(ctx, base_dir):
    path = write_csv(base_dir)
    import_file(ctx.db, path, COLUMNS)
    cid = ctx.db.get_contact_by_email("quiet@nohookco.com")["id"]
    ctx.db.update_contact(cid, personalization="hand-written hook")
    import_file(ctx.db, path, COLUMNS)  # CSV has empty hook for this row
    assert ctx.db.get_contact(cid)["personalization"] == "hand-written hook"
