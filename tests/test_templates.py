import pytest

from mailmerge.templates import (
    TemplateError,
    build_variables,
    list_templates,
    load_template,
    render,
    render_email,
)
from tests.conftest import add_contact


def test_list_templates(ctx):
    assert set(list_templates(ctx.templates_dir)) >= {"warm", "followup1", "followup2"}


def test_load_template_ok(ctx):
    subject, body = load_template("warm", ctx.templates_dir)
    assert "{company}" in subject and "{first_name}" in body


def test_load_template_missing(ctx):
    with pytest.raises(TemplateError):
        load_template("does_not_exist", ctx.templates_dir)


def test_load_template_no_subject(base_dir, ctx):
    (base_dir / "templates" / "bad.txt").write_text("no subject line\n\nbody", encoding="utf-8")
    with pytest.raises(TemplateError):
        load_template("bad", ctx.templates_dir)


def test_load_template_subject_with_colons(base_dir, ctx):
    (base_dir / "templates" / "colon.txt").write_text(
        "Subject: re: re: hello {company}\n\nHi {first_name}\n", encoding="utf-8"
    )
    subject, _ = load_template("colon", ctx.templates_dir)
    assert subject == "re: re: hello {company}"


@pytest.mark.parametrize("template,variables,expected", [
    ("hi {first_name}", {"first_name": "Ada"}, "hi Ada"),
    ("hi {first_name} {unknown}", {"first_name": "Ada"}, "hi Ada {unknown}"),
    ("no vars", {}, "no vars"),
    ("{a}{b}", {"a": "1", "b": "2"}, "12"),
])
def test_render_tolerant(template, variables, expected):
    assert render(template, variables) == expected


def test_build_variables_core_fields(ctx):
    cid = add_contact(ctx)
    v = build_variables(ctx.db.get_contact(cid), ctx.cfg["sender"])
    assert v["first_name"] == "Ada"
    assert v["company"] == "Drone Co"
    assert v["my_name"] == "Vihaan"
    assert v["my_email"] == "me@example.com"


def test_build_variables_exposes_extra_columns(ctx):
    cid = add_contact(ctx, extra={"Stage/Size": "Series B", "Region": "Bay Area"})
    v = build_variables(ctx.db.get_contact(cid), ctx.cfg["sender"])
    assert v["stage_size"] == "Series B" and v["region"] == "Bay Area"


def test_build_variables_derives_first_name_when_missing(ctx):
    cid = add_contact(ctx, first_name="", full_name="Grace Hopper")
    v = build_variables(ctx.db.get_contact(cid), ctx.cfg["sender"])
    assert v["first_name"] == "Grace"


def test_build_variables_accepts_plain_dict(ctx):
    v = build_variables({"full_name": "Ada Lovelace", "company": "X"}, ctx.cfg["sender"])
    assert v["first_name"] == "Ada"


def test_build_variables_handles_bad_extra_json(ctx):
    cid = add_contact(ctx)
    ctx.db.conn.execute("UPDATE contacts SET extra='not json' WHERE id=?", (cid,))
    ctx.db.conn.commit()
    v = build_variables(ctx.db.get_contact(cid), ctx.cfg["sender"])
    assert v["company"] == "Drone Co"  # does not crash


def test_render_email_clean(ctx):
    cid = add_contact(ctx)
    subject, body, leftovers = render_email("warm", ctx.templates_dir,
                                            ctx.db.get_contact(cid), ctx.cfg["sender"])
    assert "Drone Co" in subject and "Ada" in body and leftovers == []


@pytest.mark.parametrize("tpl", ["warm", "followup1", "followup2"])
def test_render_email_all_templates_clean(ctx, tpl):
    cid = add_contact(ctx)
    _, _, leftovers = render_email(tpl, ctx.templates_dir, ctx.db.get_contact(cid), ctx.cfg["sender"])
    assert leftovers == []


def test_render_email_reports_unknown_placeholder(base_dir, ctx):
    (base_dir / "templates" / "typo.txt").write_text(
        "Subject: hi {company}\n\nHi {first_name}, {mystery_field}.\n", encoding="utf-8"
    )
    cid = add_contact(ctx)
    _, _, leftovers = render_email("typo", ctx.templates_dir, ctx.db.get_contact(cid), ctx.cfg["sender"])
    assert "{mystery_field}" in leftovers


def test_empty_required_field_renders_blank_not_leftover(ctx):
    cid = add_contact(ctx, personalization="")
    _, body, leftovers = render_email("warm", ctx.templates_dir, ctx.db.get_contact(cid), ctx.cfg["sender"])
    assert leftovers == [] and "{personalization}" not in body
