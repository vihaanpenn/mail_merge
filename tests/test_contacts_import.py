import json

from mailmerge.contacts import import_file, row_to_contact

COLUMNS = {
    "company": "Company Name",
    "name": "Contact Name",
    "title": "Contact Title",
    "email": "Email",
    "confidence": "Email Confidence",
    "personalization": "Personalization",
}

CSV = """Company Name,Contact Name,Contact Title,Email,Email Confidence,Personalization,Region
Drone Co,Ada Lovelace,VP Eng,ada@drone.co,High,your flight stack,Bay Area
IoT Co,Grace Hopper,CTO,grace@iot.co,High,your sensor platform,Bay Area
No Email Co,Nobody,Founder,,Low,nothing,SoCal
Bad Email Co,Bad Row,Founder,not-an-email,High,hook,SoCal
Dup Co,Ada Again,Eng,ada@drone.co,High,dup hook,Bay Area
"""


def write_csv(base_dir):
    path = base_dir / "data" / "contacts.csv"
    path.write_text(CSV, encoding="utf-8")
    return path


def test_row_to_contact_maps_and_preserves_extra():
    row = {
        "Company Name": "Drone Co", "Contact Name": "Dr. Ada Lovelace",
        "Contact Title": "VP", "Email": "ADA@Drone.CO ", "Email Confidence": "High",
        "Personalization": "x", "Region": "Bay Area", "Stage": "Series B",
    }
    c = row_to_contact(row, COLUMNS)
    assert c["email"] == "ada@drone.co"
    assert c["first_name"] == "Ada"
    assert c["extra"] == {"Region": "Bay Area", "Stage": "Series B"}


def test_import_dedupes_and_skips(ctx, base_dir):
    path = write_csv(base_dir)
    result = import_file(ctx.db, path, COLUMNS)
    # 5 rows: ada, grace, (no email -> skip), (bad email -> skip), ada dup (update)
    assert result.added == 2
    assert result.updated == 1
    assert result.skipped == 2
    assert ctx.db.totals()["contacts"] == 2


def test_import_preserves_extra_columns(ctx, base_dir):
    path = write_csv(base_dir)
    import_file(ctx.db, path, COLUMNS)
    row = ctx.db.get_contact_by_email("ada@drone.co")
    assert json.loads(row["extra"]).get("Region") == "Bay Area"


def test_reimport_is_idempotent(ctx, base_dir):
    path = write_csv(base_dir)
    import_file(ctx.db, path, COLUMNS)
    result = import_file(ctx.db, path, COLUMNS)
    assert result.added == 0
    assert ctx.db.totals()["contacts"] == 2
