import datetime as dt

import pytest

from mailmerge.utils import (
    confidence_rank,
    days_since,
    deep_merge,
    first_name_from,
    normalize_email,
    parse_iso,
    render_table,
    slug,
    truncate,
    unresolved_placeholders,
    valid_email_format,
)


@pytest.mark.parametrize("raw,expected", [
    ("Company Name", "company_name"),
    ("Bay Area?", "bay_area"),
    ("  Stage/Size  ", "stage_size"),
    ("Email Confidence", "email_confidence"),
    ("LinkedIn URL!!!", "linkedin_url"),
    ("already_slug", "already_slug"),
    ("MiXeD-CASE  spaces", "mixed_case_spaces"),
    ("123 Numbers 456", "123_numbers_456"),
    ("____", ""),
    ("é accent", "accent"),
])
def test_slug(raw, expected):
    assert slug(raw) == expected


@pytest.mark.parametrize("name,expected", [
    ("Ada Lovelace", "Ada"),
    ("Dr. Grace Hopper", "Grace"),
    ("Mr. Alan Turing", "Alan"),
    ("Prof. Katherine Johnson", "Katherine"),
    ("Madonna", "Madonna"),
    ("", ""),
    ("   ", ""),
    ("Hedy", "Hedy"),
    ("ms. rosalind franklin", "rosalind"),
])
def test_first_name_from(name, expected):
    assert first_name_from(name) == expected


@pytest.mark.parametrize("conf,rank_expected_high", [
    ("High", True), ("high", True), ("HIGH", True),
])
def test_confidence_high(conf, rank_expected_high):
    assert (confidence_rank(conf) == 3) == rank_expected_high


def test_confidence_ordering():
    assert confidence_rank("High") > confidence_rank("Medium") > confidence_rank("Low") > confidence_rank("")


@pytest.mark.parametrize("conf", ["", None, "nonsense", "unknown", "  "])
def test_confidence_unknown_is_zero(conf):
    assert confidence_rank(conf) == 0


@pytest.mark.parametrize("addr,ok", [
    ("ada@nimbusrobotics.io", True),
    ("a@b.co", True),
    ("first.last+tag@sub.domain.com", True),
    ("not-an-email", False),
    ("a@b", False),
    ("@b.co", False),
    ("a@@b.co", False),
    ("a b@c.co", False),
    ("", False),
    ("trailing@space.co ", False),
])
def test_valid_email_format(addr, ok):
    assert valid_email_format(addr) is ok


@pytest.mark.parametrize("raw,expected", [
    ("  Ada@Drone.CO ", "ada@drone.co"),
    ("GRACE@HELIOS.COM", "grace@helios.com"),
    (None, ""),
    ("   ", ""),
    ("Already@low.er", "already@low.er"),
])
def test_normalize_email(raw, expected):
    assert normalize_email(raw) == expected


def test_unresolved_placeholders_sorted_unique():
    assert unresolved_placeholders("hi {first_name}", "{company} {company}") == ["{company}", "{first_name}"]


@pytest.mark.parametrize("text", ["nothing here", "", "no braces at all"])
def test_unresolved_placeholders_none(text):
    assert unresolved_placeholders(text) == []


def test_days_since_none_inputs():
    assert days_since(None) is None
    assert days_since("not-a-date") is None
    assert days_since("") is None


def test_days_since_value():
    ten_ago = (dt.datetime.now() - dt.timedelta(days=10)).isoformat(timespec="seconds")
    assert 9.9 < days_since(ten_ago) < 10.1


def test_days_since_reference():
    base = dt.datetime(2025, 1, 10, 12, 0, 0)
    earlier = dt.datetime(2025, 1, 5, 12, 0, 0).isoformat(timespec="seconds")
    assert days_since(earlier, reference=base) == pytest.approx(5.0)


def test_parse_iso():
    assert parse_iso("2025-06-09T10:00:00").year == 2025
    assert parse_iso(None) is None
    assert parse_iso("garbage") is None


def test_deep_merge_nested():
    base = {"a": 1, "b": {"x": 1, "y": 2}}
    out = deep_merge(base, {"b": {"y": 9, "z": 3}, "c": 4})
    assert out == {"a": 1, "b": {"x": 1, "y": 9, "z": 3}, "c": 4}
    assert base["b"] == {"x": 1, "y": 2}  # original untouched


def test_deep_merge_none_section_keeps_base():
    base = {"sending": {"daily_cap": 40}}
    assert deep_merge(base, {"sending": None})["sending"] == {"daily_cap": 40}


def test_deep_merge_override_scalar_with_dict():
    assert deep_merge({"a": 1}, {"a": {"x": 2}}) == {"a": {"x": 2}}


def test_deep_merge_empty_override():
    assert deep_merge({"a": 1}, None) == {"a": 1}
    assert deep_merge({"a": 1}, {}) == {"a": 1}


@pytest.mark.parametrize("value,length,ends_ellipsis", [
    ("short", 10, False),
    ("a very long sentence indeed that runs on", 10, True),
    ("", 5, False),
])
def test_truncate(value, length, ends_ellipsis):
    out = truncate(value, length)
    assert (out.endswith("…")) is ends_ellipsis
    assert len(out) <= length


def test_truncate_collapses_whitespace():
    assert truncate("a    b\n\nc", 80) == "a b c"


@pytest.mark.parametrize("value", [None, 123, 4.5])
def test_truncate_handles_non_strings(value):
    # must never raise on non-string DB values
    assert isinstance(truncate(value), str)


def test_render_table_shape():
    out = render_table(["a", "b"], [[1, 2], [33, 4]])
    assert "a" in out and "33" in out
    assert out.count("\n") == 3  # header, separator, 2 rows


def test_render_table_handles_none_cells():
    out = render_table(["x"], [[None]])
    assert "x" in out
