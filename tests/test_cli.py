import pytest

from mailmerge import inbox, sender
from mailmerge.cli import main
from mailmerge.context import Context
from tests.conftest import run_cli
from tests.fakedata import FakeIMAP, FakeSMTP


def open_ctx(project):
    return Context.create(project, "config.yaml")


def imported(project):
    """Run import once and return the project for chaining."""
    run_cli(project, "import", "data/contacts.csv")
    return project


# -- argparse plumbing -----------------------------------------------------

def test_no_command_errors():
    with pytest.raises(SystemExit):
        main([])


def test_version_exits(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])


def test_unknown_command_errors():
    with pytest.raises(SystemExit):
        main(["--base-dir", "/tmp", "frobnicate"])


# -- init ------------------------------------------------------------------

def test_init_bare_dir(tmp_path):
    assert run_cli(tmp_path, "init") == 0
    assert (tmp_path / "data" / "mailmerge.db").exists()
    assert (tmp_path / "templates").exists()
    assert (tmp_path / "output" / "preview").exists()


# -- import ----------------------------------------------------------------

def test_import_default_path(project, capsys):
    assert run_cli(project, "import") == 0
    assert "Imported" in capsys.readouterr().out
    ctx = open_ctx(project)
    # 6 unique emails (the two Nimbus rows dedupe; two rows skipped for bad/no email)
    assert ctx.db.totals()["contacts"] == 6
    ctx.close()


def test_import_explicit_path(project):
    assert run_cli(project, "import", "data/contacts.csv") == 0


def test_import_missing_file_returns_1(project):
    assert run_cli(project, "import", "data/nope.csv") == 1


# -- contacts / show -------------------------------------------------------

def test_contacts_list(project, capsys):
    imported(project)
    assert run_cli(project, "contacts") == 0
    assert "Nimbus Robotics" in capsys.readouterr().out


def test_contacts_filter_status(project, capsys):
    imported(project)
    assert run_cli(project, "contacts", "--status", "new") == 0


def test_contacts_search(project, capsys):
    imported(project)
    run_cli(project, "contacts", "--search", "Helios")
    assert "Helios" in capsys.readouterr().out


def test_contacts_empty_message(project, capsys):
    assert run_cli(project, "contacts") == 0
    assert "No matching contacts" in capsys.readouterr().out


def test_show_existing(project, capsys):
    imported(project)
    ctx = open_ctx(project)
    cid = ctx.db.list_contacts()[0]["id"]
    ctx.close()
    assert run_cli(project, "show", str(cid)) == 0


def test_show_missing_returns_1(project):
    imported(project)
    assert run_cli(project, "show", "9999") == 1


# -- set -------------------------------------------------------------------

def test_set_status(project):
    imported(project)
    ctx = open_ctx(project)
    cid = ctx.db.get_contact_by_email("ada@nimbusrobotics.io")["id"]
    ctx.close()
    assert run_cli(project, "set", str(cid), "--status", "do_not_contact") == 0
    ctx = open_ctx(project)
    assert ctx.db.get_contact(cid)["status"] == "do_not_contact"
    ctx.close()


def test_set_personalization_and_tag(project):
    imported(project)
    ctx = open_ctx(project)
    cid = ctx.db.get_contact_by_email("ada@nimbusrobotics.io")["id"]
    ctx.close()
    assert run_cli(project, "set", str(cid), "--personalization", "new hook", "--tag", "vip") == 0
    ctx = open_ctx(project)
    row = ctx.db.get_contact(cid)
    assert row["personalization"] == "new hook" and "vip" in row["tags"]
    ctx.close()


def test_set_no_args_returns_1(project):
    imported(project)
    assert run_cli(project, "set", "1") == 1


def test_set_missing_contact_returns_1(project):
    imported(project)
    assert run_cli(project, "set", "9999", "--status", "new") == 1


# -- validate / preview ----------------------------------------------------

def test_validate(project, capsys):
    imported(project)
    assert run_cli(project, "validate") == 0
    out = capsys.readouterr().out
    assert "would SEND" in out and "below confidence" in out


def test_preview_writes_files(project):
    imported(project)
    assert run_cli(project, "preview") == 0
    files = list((project / "output" / "preview").glob("*__warm.txt"))
    assert len(files) == 4  # ada, grace, alan, katherine


def test_preview_limit_zero(project):
    imported(project)
    assert run_cli(project, "preview", "--limit", "0") == 0
    assert list((project / "output" / "preview").glob("*__warm.txt")) == []


# -- export ----------------------------------------------------------------

def test_export(project):
    imported(project)
    assert run_cli(project, "export", "--out", "output/export.csv") == 0
    assert (project / "output" / "export.csv").exists()


def test_export_nested_path(project):
    imported(project)
    assert run_cli(project, "export", "--out", "output/a/b/c.csv") == 0
    assert (project / "output" / "a" / "b" / "c.csv").exists()


# -- status / replies / templates -----------------------------------------

def test_status(project, capsys):
    imported(project)
    assert run_cli(project, "status") == 0
    assert "MAILMERGE STATUS" in capsys.readouterr().out


def test_replies_empty(project, capsys):
    imported(project)
    assert run_cli(project, "replies") == 0
    assert "No replies" in capsys.readouterr().out


def test_templates(project, capsys):
    assert run_cli(project, "templates") == 0
    assert "warm" in capsys.readouterr().out


# -- send / test / followup / sync (mocked transports) ---------------------

def test_send_with_yes(project, monkeypatch):
    imported(project)
    fake = FakeSMTP()
    monkeypatch.setattr(sender, "smtp_connect", lambda c: fake)
    monkeypatch.setattr(sender.time, "sleep", lambda *a, **k: None)
    assert run_cli(project, "send", "--yes", "--daily-cap", "2") == 0
    assert len(fake.sent) == 2
    ctx = open_ctx(project)
    assert ctx.db.sent_today() == 2
    ctx.close()


def test_test_command_requires_to(project):
    imported(project)
    assert run_cli(project, "test") == 1


def test_test_command_sends_to_self(project, monkeypatch):
    imported(project)
    fake = FakeSMTP()
    monkeypatch.setattr(sender, "smtp_connect", lambda c: fake)
    monkeypatch.setattr(sender.time, "sleep", lambda *a, **k: None)
    assert run_cli(project, "test", "--to", "me@example.com", "--limit", "1") == 0
    assert fake.sent and fake.sent[0]["To"] == "me@example.com"
    ctx = open_ctx(project)
    assert ctx.db.list_messages() == []   # test sends are not logged
    ctx.close()


def test_followup_none_due(project, capsys):
    imported(project)
    assert run_cli(project, "followup") == 0
    assert "No follow-ups are due" in capsys.readouterr().out


def test_sync_via_cli(project, monkeypatch):
    imported(project)
    messages = [("1", b"From: ada@nimbusrobotics.io\nSubject: Re: hi\n\nthanks")]
    monkeypatch.setattr(inbox, "imap_connect", lambda c: FakeIMAP(messages))
    assert run_cli(project, "sync") == 0
    ctx = open_ctx(project)
    assert ctx.db.get_contact_by_email("ada@nimbusrobotics.io")["status"] == "replied"
    ctx.close()
