from mailmerge import sender
from mailmerge.pipeline import Job
from tests.conftest import add_contact


class FakeServer:
    """Stand-in SMTP server: fails on the message indexes in `fail_idx`."""

    def __init__(self, fail_idx=()):
        self.sent = []
        self.fail_idx = set(fail_idx)
        self.i = 0

    def send_message(self, msg):
        idx = self.i
        self.i += 1
        if idx in self.fail_idx:
            raise RuntimeError("smtp boom")
        self.sent.append(msg)

    def quit(self):
        pass


def make_job(cid, email, step=0, template="warm"):
    return Job(contact_id=cid, email=email, company="Co", name="N",
               step=step, template=template, subject="hello", body="body")


def test_failed_send_not_counted_against_cap(ctx, monkeypatch):
    # First send fails, second succeeds. With cap=1, the failure must NOT consume
    # the cap — the successful one still goes out and only it counts.
    monkeypatch.setattr(sender, "smtp_connect", lambda c: FakeServer(fail_idx={0}))
    monkeypatch.setattr(sender.time, "sleep", lambda *a, **k: None)

    c1 = add_contact(ctx, email="a@a.co")
    c2 = add_contact(ctx, email="b@b.co")
    jobs = [make_job(c1, "a@a.co"), make_job(c2, "b@b.co")]

    sent = sender.send_jobs(ctx, jobs, campaign="c", daily_cap=1)
    assert sent == 1

    statuses = sorted(m["status"] for m in ctx.db.list_messages())
    assert statuses == ["error", "sent"]
    # The contact whose send errored stays 'new' (eligible to retry next run).
    assert ctx.db.get_contact(c1)["status"] == "new"
    assert ctx.db.get_contact(c2)["status"] == "contacted"


def test_build_message_sanitizes_headers_and_sets_date(ctx):
    cid = add_contact(ctx)
    job = make_job(cid, "x@y.co")
    job.subject = "Hello\r\nBcc: evil@example.com"      # attempted header injection
    msg = sender.build_message(ctx, "x@y.co", job, attach_resume=False)
    assert "\n" not in msg["Subject"] and "\r" not in msg["Subject"]
    assert "Bcc: evil@example.com" in msg["Subject"]   # folded into the subject text
    assert msg["Bcc"] is None                          # no injected header
    assert msg["Date"]                                 # Date header present
    assert msg["Message-ID"]
