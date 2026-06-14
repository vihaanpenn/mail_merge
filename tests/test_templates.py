import pytest

from mailmerge.templates import (
    TemplateError,
    build_variables,
    load_template,
    render,
    render_email,
)
from tests.conftest import add_contact


def test_load_template_ok(ctx):
    subject, body = load_template("warm", ctx.templates_dir)
    assert "{company}" in subject
    assert "{first_name}" in body


def test_load_template_missing(ctx):
    with pytest.raises(TemplateError):
        load_template("does_not_exist", ctx.templates_dir)


def test_load_template_no_subject(base_dir, ctx):
    (base_dir / "templates" / "bad.txt").write_text("no subject line here\n\nbody")
    with pytest.raises(TemplateError):
        load_template("bad", ctx.templates_dir)


def test_render_tolerant_keeps_unknown():
    out = render("hi {first_name} {unknown_var}", {"first_name": "Ada"})
    assert out == "hi Ada {unknown_var}"


def test_build_variables_exposes_extra_columns(ctx):
    cid = add_contact(ctx, extra={"Stage/Size": "Series B", "Region": "Bay Area"})
    row = ctx.db.get_contact(cid)
    variables = build_variables(row, ctx.cfg["sender"])
    assert variables["stage_size"] == "Series B"
    assert variables["region"] == "Bay Area"
    assert variables["first_name"] == "Ada"
    assert variables["my_name"] == "Vihaan"


def test_render_email_clean(ctx):
    cid = add_contact(ctx)
    row = ctx.db.get_contact(cid)
    subject, body, leftovers = render_email("warm", ctx.templates_dir, row, ctx.cfg["sender"])
    assert "Drone Co" in subject
    assert "Ada" in body
    assert leftovers == []


def test_render_email_reports_unknown_placeholder(base_dir, ctx):
    # A template referencing a field that doesn't exist must surface as a leftover
    # (this is the guardrail against typo'd / unmapped columns going out).
    (base_dir / "templates" / "typo.txt").write_text(
        "Subject: hi {company}\n\nHi {first_name}, {mystery_field}.\n", encoding="utf-8"
    )
    cid = add_contact(ctx)
    row = ctx.db.get_contact(cid)
    _, _, leftovers = render_email("typo", ctx.templates_dir, row, ctx.cfg["sender"])
    assert "{mystery_field}" in leftovers


def test_empty_required_field_renders_blank_not_leftover(ctx):
    # An empty *known* field renders to empty; it is caught by the required-field
    # check in the pipeline, not by leftover detection.
    cid = add_contact(ctx, personalization="")
    row = ctx.db.get_contact(cid)
    _, body, leftovers = render_email("warm", ctx.templates_dir, row, ctx.cfg["sender"])
    assert leftovers == []
    assert "{personalization}" not in body
