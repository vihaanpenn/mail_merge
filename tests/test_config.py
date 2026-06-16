import pytest

from mailmerge.config import DEFAULT_CONFIG, Config, load_config


def test_load_defaults_when_missing(tmp_path):
    cfg = load_config("nope.yaml", tmp_path)
    assert cfg["sending"]["daily_cap"] == 40
    assert cfg.source is None


def test_load_merges_yaml(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "sender:\n  name: Custom Name\nsending:\n  daily_cap: 5\n", encoding="utf-8")
    cfg = load_config("config.yaml", tmp_path)
    assert cfg["sender"]["name"] == "Custom Name"
    assert cfg["sending"]["daily_cap"] == 5
    assert cfg["sending"]["delay_min_seconds"] == 35  # default preserved
    assert cfg.source is not None


def test_load_empty_section_keeps_defaults(tmp_path):
    (tmp_path / "config.yaml").write_text("sending:\nsender:\n  name: X\n", encoding="utf-8")
    cfg = load_config("config.yaml", tmp_path)
    assert cfg["sending"]["daily_cap"] == 40   # not wiped by the empty section
    assert cfg["sender"]["name"] == "X"


def test_resolve_relative(tmp_path):
    cfg = load_config("nope.yaml", tmp_path)
    assert cfg.resolve(tmp_path, "data/x.db") == tmp_path / "data" / "x.db"


def test_resolve_absolute(tmp_path):
    cfg = load_config("nope.yaml", tmp_path)
    target = tmp_path / "abs.db"
    assert cfg.resolve(tmp_path, str(target)) == target


def test_password_reads_env(tmp_path, monkeypatch):
    cfg = load_config("nope.yaml", tmp_path)
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "secret")
    assert cfg.password("EMAIL_APP_PASSWORD") == "secret"
    assert cfg.password("DEFINITELY_MISSING_VAR") is None


def test_section_helper(tmp_path):
    cfg = load_config("nope.yaml", tmp_path)
    assert cfg.section("sender")["email"] == "you@example.com"
    assert cfg.section("nonexistent") == {}


@pytest.mark.parametrize("section", [
    "sender", "auth", "imap", "database", "contacts",
    "resume", "sending", "followups", "verification",
])
def test_default_sections_present(section):
    assert section in DEFAULT_CONFIG


def test_config_is_dict_subclass():
    assert isinstance(Config(DEFAULT_CONFIG), dict)


def test_default_followups_two_steps():
    assert len(DEFAULT_CONFIG["followups"]["steps"]) == 2
