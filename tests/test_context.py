from mailmerge.context import Context


def test_context_paths(ctx, base_dir):
    assert ctx.templates_dir == base_dir / "templates"
    assert ctx.resume_path == base_dir / "resume" / "Your_Resume.pdf"
    assert ctx.output_dir == base_dir / "output" / "preview"
    assert ctx.db_path == base_dir / "data" / "test.db"


def test_context_resolve_relative(ctx, base_dir):
    assert ctx.resolve("foo/bar.txt") == base_dir / "foo" / "bar.txt"


def test_context_resolve_absolute(ctx, tmp_path):
    target = tmp_path / "abs.txt"
    assert ctx.resolve(str(target)) == target


def test_context_create_opens_db(project):
    ctx = Context.create(project, "config.yaml")
    try:
        assert ctx.db_path == project / "data" / "mailmerge.db"
        assert ctx.templates_dir == project / "templates"
        assert ctx.db.totals()["contacts"] == 0
    finally:
        ctx.close()


def test_context_create_makes_db_file(project):
    ctx = Context.create(project, "config.yaml")
    ctx.close()
    assert (project / "data" / "mailmerge.db").exists()
