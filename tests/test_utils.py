from mailmerge.utils import (
    confidence_rank,
    days_since,
    deep_merge,
    first_name_from,
    normalize_email,
    render_table,
    slug,
    truncate,
    unresolved_placeholders,
    valid_email_format,
)


def test_slug():
    assert slug("Company Name") == "company_name"
    assert slug("Bay Area?") == "bay_area"
    assert slug("  Stage/Size  ") == "stage_size"


def test_first_name_from():
    assert first_name_from("Ada Lovelace") == "Ada"
    assert first_name_from("Dr. Grace Hopper") == "Grace"
    assert first_name_from("") == ""
    assert first_name_from("Madonna") == "Madonna"


def test_confidence_rank_ordering():
    assert confidence_rank("High") > confidence_rank("Medium") > confidence_rank("Low")
    assert confidence_rank("") == 0
    assert confidence_rank(None) == 0
    assert confidence_rank("nonsense") == 0


def test_valid_email_format():
    assert valid_email_format("a@b.co")
    assert not valid_email_format("not-an-email")
    assert not valid_email_format("a@b")
    assert not valid_email_format("")


def test_normalize_email():
    assert normalize_email("  Ada@Drone.CO ") == "ada@drone.co"
    assert normalize_email(None) == ""


def test_unresolved_placeholders():
    assert unresolved_placeholders("hi {first_name}", "{company} ok") == ["{company}", "{first_name}"]
    assert unresolved_placeholders("nothing here") == []


def test_days_since_none_and_value():
    assert days_since(None) is None
    assert days_since("not-a-date") is None
    # ~10 days ago
    import datetime as dt
    ten_ago = (dt.datetime.now() - dt.timedelta(days=10)).isoformat(timespec="seconds")
    val = days_since(ten_ago)
    assert 9.9 < val < 10.1


def test_deep_merge():
    base = {"a": 1, "b": {"x": 1, "y": 2}}
    out = deep_merge(base, {"b": {"y": 9, "z": 3}, "c": 4})
    assert out == {"a": 1, "b": {"x": 1, "y": 9, "z": 3}, "c": 4}
    # original untouched
    assert base["b"] == {"x": 1, "y": 2}


def test_deep_merge_none_section_keeps_base():
    # An empty YAML section parses to None and must NOT wipe the default dict.
    base = {"sending": {"daily_cap": 40}}
    out = deep_merge(base, {"sending": None})
    assert out["sending"] == {"daily_cap": 40}


def test_truncate():
    assert truncate("short", 10) == "short"
    assert truncate("a very long sentence indeed", 10).endswith("…")


def test_render_table():
    out = render_table(["a", "b"], [[1, 2], [33, 4]])
    assert "a" in out and "33" in out
    assert out.count("\n") == 3  # header, sep, 2 rows
