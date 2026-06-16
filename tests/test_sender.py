import pytest

from mailmerge import sender
from mailmerge.pipeline import Job
from tests.conftest import add_contact
from tests.fakedata import FakeSMTP


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(sender.time, "sleep", lambda *a, **k: None)


def make_job(cid, email, step=0, template="warm", subject="hello", body="body"):
    return Job(contact_id=cid, email=email, company="Co", name="N",
               step=step, template=template, subject=subject, body=body)


# -- smtp_password ---------------------------------------------------------

def test_smtp_password_missing_exits(ctx, monkeypatch):
    monkeypatch.delenv("EMAIL_APP_PASSWORD", raising=False)
    with pytest.raises(SystemExit):
        sender.smtp_password(ctx)


def test_smtp_password_from_env(ctx, monkeypatch):
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "hunter2")
    assert sender.smtp_password(ctx) == "hunter2"


# -- build_message ---------------------------------------------------------

def test_build_message_basic(ctx):
    cid = add_contact(ctx)
    m = sender.build_message(ctx, "x@y.co", make_job(cid, "x@y.co"), attach_resume=False)
    assert m["To"] == "x@y.co"
    assert "Vihaan" in m["From"] and "me@example.com" in m["From"]
    assert m["Subject"] == "hello"
    assert m["Message-ID"] and m["Date"]
    assert m["X-Mailmerge-Contact"] == str(cid)
    assert m["X-Mailmerge-Step"] == "0"


def test_build_message_from_without_name(ctx):
    ctx.cfg["sender"]["name"] = ""
    m = sender.build_message(ctx, "x@y.co", make_job(1, "x@y.co"), attach_resume=False)
    assert m["From"] == "me@example.com"


def test_build_message_sanitizes_headers(ctx):
    job = make_job(1, "x@y.co", subject="Hello\r\nBcc: evil@example.com")
    m = sender.build_message(ctx, "x@y.co", job, attach_resume=False)
    assert "\n" not in m["Subject"] and "\r" not in m["Subject"]
    assert "Bcc: evil@example.com" in m["Subject"]
    assert m["Bcc"] is None


def test_build_message_no_attachment_when_missing(ctx):
    m = sender.build_message(ctx, "x@y.co", make_job(1, "x@y.co"), attach_resume=True)
    assert not any(p.get_filename() for p in m.walk())


def test_build_message_attaches_pdf(ctx, base_dir):
    (base_dir / "resume").mkdir()
    (base_dir / "resume" / "Your_Resume.pdf").write_bytes(b"%PDF-1.4 fake")
    m = sender.build_message(ctx, "x@y.co", make_job(1, "x@y.co"), attach_resume=True)
    names = [p.get_filename() for p in m.walk() if p.get_filename()]
    assert names == ["Your_Resume.pdf"]


def test_build_message_attaches_docx(ctx, base_dir):
    ctx.cfg["resume"]["path"] = "resume/cv.docx"
    (base_dir / "resume").mkdir()
    (base_dir / "resume" / "cv.docx").write_bytes(b"PK fake docx")
    m = sender.build_message(ctx, "x@y.co", make_job(1, "x@y.co"), attach_resume=True)
    att = [p for p in m.walk() if p.get_filename() == "cv.docx"][0]
    assert att.get_content_type() == "application/octet-stream"


# -- send_jobs -------------------------------------------------------------

def test_send_all_within_cap(ctx, monkeypatch):
    fake = FakeSMTP()
    monkeypatch.setattr(sender, "smtp_connect", lambda c: fake)
    c1 = add_contact(ctx, email="a@a.co")
    c2 = add_contact(ctx, email="b@b.co")
    jobs = [make_job(c1, "a@a.co"), make_job(c2, "b@b.co")]
    sent = sender.send_jobs(ctx, jobs, campaign="c", daily_cap=10)
    assert sent == 2 and len(fake.sent) == 2
    assert ctx.db.get_contact(c1)["status"] == "contacted"


def test_send_respects_cap(ctx, monkeypatch):
    monkeypatch.setattr(sender, "smtp_connect", lambda c: FakeSMTP())
    ids = [add_contact(ctx, email=f"c{i}@a.co") for i in range(5)]
    jobs = [make_job(c, f"c{i}@a.co") for i, c in enumerate(ids)]
    sent = sender.send_jobs(ctx, jobs, campaign="c", daily_cap=2)
    assert sent == 2
    assert ctx.db.sent_today() == 2


def test_failed_send_not_counted_against_cap(ctx, monkeypatch):
    monkeypatch.setattr(sender, "smtp_connect", lambda c: FakeSMTP(fail_idx={0}))
    c1 = add_contact(ctx, email="a@a.co")
    c2 = add_contact(ctx, email="b@b.co")
    jobs = [make_job(c1, "a@a.co"), make_job(c2, "b@b.co")]
    sent = sender.send_jobs(ctx, jobs, campaign="c", daily_cap=1)
    assert sent == 1
    assert sorted(m["status"] for m in ctx.db.list_messages()) == ["error", "sent"]
    assert ctx.db.get_contact(c1)["status"] == "new"          # eligible to retry
    assert ctx.db.get_contact(c2)["status"] == "contacted"


def test_send_records_message_rows(ctx, monkeypatch):
    monkeypatch.setattr(sender, "smtp_connect", lambda c: FakeSMTP())
    cid = add_contact(ctx, email="a@a.co")
    sender.send_jobs(ctx, [make_job(cid, "a@a.co", template="warm")], campaign="spring", daily_cap=5)
    m = ctx.db.list_messages(cid)[0]
    assert m["campaign"] == "spring" and m["template"] == "warm" and m["status"] == "sent"


def test_test_mode_redirects_and_does_not_record(ctx, monkeypatch):
    fake = FakeSMTP()
    monkeypatch.setattr(sender, "smtp_connect", lambda c: fake)
    cid = add_contact(ctx, email="real@prospect.co")
    jobs = [make_job(cid, "real@prospect.co")]
    sent = sender.send_jobs(ctx, jobs, campaign="test", daily_cap=5,
                            force_to="me@example.com", record=False)
    assert sent == 1
    assert fake.sent[0]["To"] == "me@example.com"   # redirected
    assert ctx.db.list_messages() == []             # not logged
    assert ctx.db.get_contact(cid)["status"] == "new"


def test_send_no_attachment_flag(ctx, monkeypatch, base_dir):
    (base_dir / "resume").mkdir()
    (base_dir / "resume" / "Your_Resume.pdf").write_bytes(b"%PDF fake")
    fake = FakeSMTP()
    monkeypatch.setattr(sender, "smtp_connect", lambda c: fake)
    cid = add_contact(ctx, email="a@a.co")
    sender.send_jobs(ctx, [make_job(cid, "a@a.co")], campaign="c", daily_cap=5,
                     attach_resume=False)
    assert not any(p.get_filename() for p in fake.sent[0].walk())
