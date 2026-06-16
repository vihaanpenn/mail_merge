"""Dummy data and in-memory fakes shared across the test suite.

Everything here is fictional. The fakes (FakeSMTP / FakeIMAP) let us exercise
the real send and sync code paths fully offline — no network, no real mail.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Dummy contacts
# ---------------------------------------------------------------------------

# Logical-field dicts ready for db.upsert_contact (good, clean rows).
CONTACTS = [
    {"email": "ada@nimbusrobotics.io", "first_name": "Ada", "full_name": "Ada Lovelace",
     "company": "Nimbus Robotics", "title": "VP Engineering", "confidence": "High",
     "personalization": "your warehouse pick-and-place arms"},
    {"email": "grace@heliosdrones.com", "first_name": "Grace", "full_name": "Grace Hopper",
     "company": "Helios Drones", "title": "Co-Founder & CTO", "confidence": "High",
     "personalization": "your BVLOS delivery autopilot"},
    {"email": "alan@atlasiot.dev", "first_name": "Alan", "full_name": "Alan Turing",
     "company": "Atlas IoT", "title": "Head of Hardware", "confidence": "Medium",
     "personalization": "your cellular sensor mesh and OTA pipeline"},
    {"email": "katherine@orbitedge.ai", "first_name": "Katherine", "full_name": "Katherine Johnson",
     "company": "Orbit Edge", "title": "Founder & CEO", "confidence": "High",
     "personalization": "your low-power MLSoC for robotics"},
    {"email": "hedy@marinerobotics.co", "first_name": "Hedy", "full_name": "Hedy Lamarr",
     "company": "Marine Robotics", "title": "Director of Engineering", "confidence": "Medium",
     "personalization": "your underwater thruster controllers"},
    {"email": "nikola@voltedge.io", "first_name": "Nikola", "full_name": "Nikola Tesla",
     "company": "Volt Edge", "title": "VP Engineering", "confidence": "High",
     "personalization": "your fast-charging power electronics"},
    {"email": "rosalind@helixbio.com", "first_name": "Rosalind", "full_name": "Rosalind Franklin",
     "company": "Helix Bio", "title": "Hardware Lead", "confidence": "Low",
     "personalization": "your lab automation robots"},
]

# A spreadsheet exactly as a user would supply it, including messy edge cases and
# extra columns (Region, Stage) that must be preserved verbatim as template vars.
CONTACTS_CSV = """Company Name,Contact Name,Contact Title,Email,Email Confidence,Personalization,Region,Stage
Nimbus Robotics,Ada Lovelace,VP Engineering,ada@nimbusrobotics.io,High,your pick-and-place arms,Bay Area,Series B
Helios Drones,Dr. Grace Hopper,Co-Founder & CTO,grace@heliosdrones.com,High,your BVLOS autopilot,Bay Area,Series A
Atlas IoT,Alan Turing,Head of Hardware, alan@atlasiot.dev ,Medium,your sensor mesh,Bay Area,Series B
Orbit Edge,Katherine Johnson,Founder & CEO,KATHERINE@ORBITEDGE.AI,High,your low-power MLSoC,Bay Area,Series C
No Email Co,Nobody Here,Founder,,Low,nothing,SoCal,Seed
Bad Email Co,Bad Row,Founder,not-an-email,High,a hook,SoCal,Seed
No Hook Co,Quiet Person,VP Eng,quiet@nohookco.com,High,,Bay Area,Series A
Low Conf Co,Faint Signal,Founder,maybe@lowconfco.com,Low,a faint hook,Bay Area,Seed
Nimbus Robotics,Ada Dup,VP Engineering,ada@nimbusrobotics.io,High,dup row same email,Bay Area,Series B
"""

# The default column mapping (mirrors config.DEFAULT_CONFIG).
COLUMNS = {
    "company": "Company Name",
    "name": "Contact Name",
    "title": "Contact Title",
    "email": "Email",
    "confidence": "Email Confidence",
    "personalization": "Personalization",
}


def seed_contacts(db, contacts=None):
    """Insert a batch of clean dummy contacts; return list of ids."""
    ids = []
    for c in (contacts if contacts is not None else CONTACTS):
        cid, _ = db.upsert_contact(dict(c))
        ids.append(cid)
    return ids


# ---------------------------------------------------------------------------
# Raw email builders
# ---------------------------------------------------------------------------

def make_raw(from_addr, subject, body="Thanks, this is helpful!", *, headers=None) -> bytes:
    """Build a simple text/plain RFC822 message as bytes."""
    lines = [f"From: {from_addr}", f"Subject: {subject}"]
    for k, v in (headers or {}).items():
        lines.append(f"{k}: {v}")
    lines.append("Content-Type: text/plain; charset=utf-8")
    lines.append("Date: Mon, 09 Jun 2025 10:00:00 -0700")
    return ("\n".join(lines) + "\n\n" + body).encode("utf-8")


def make_html(from_addr, subject, html="<p>Hello <b>there</b>, thanks!</p>") -> bytes:
    raw = (
        f"From: {from_addr}\nSubject: {subject}\nMIME-Version: 1.0\n"
        f'Content-Type: multipart/alternative; boundary="BD"\n'
        f"Date: Mon, 09 Jun 2025 10:00:00 -0700\n\n"
        f"--BD\nContent-Type: text/html; charset=utf-8\n\n{html}\n--BD--\n"
    )
    return raw.encode("utf-8")


def make_dsn(failed_recipient, *, from_addr="mailer-daemon@googlemail.com") -> bytes:
    """A standard multipart/report delivery-status notification (bounce)."""
    raw = (
        f"From: Mail Delivery Subsystem <{from_addr}>\n"
        f"Subject: Delivery Status Notification (Failure)\n"
        f"MIME-Version: 1.0\n"
        f'Content-Type: multipart/report; report-type=delivery-status; boundary="DSN"\n'
        f"Date: Mon, 09 Jun 2025 10:00:00 -0700\n\n"
        f"--DSN\nContent-Type: text/plain\n\n"
        f"Your message could not be delivered.\n"
        f"--DSN\nContent-Type: message/delivery-status\n\n"
        f"Final-Recipient: rfc822; {failed_recipient}\nAction: failed\nStatus: 5.1.1\n"
        f"--DSN--\n"
    )
    return raw.encode("utf-8")


# ---------------------------------------------------------------------------
# In-memory SMTP / IMAP fakes
# ---------------------------------------------------------------------------

class FakeSMTP:
    """Stand-in SMTP server. Raises on message indexes listed in `fail_idx`."""

    def __init__(self, fail_idx=()):
        self.sent = []
        self.fail_idx = set(fail_idx)
        self.i = 0

    def send_message(self, msg):
        idx = self.i
        self.i += 1
        if idx in self.fail_idx:
            raise RuntimeError("smtp transient error")
        self.sent.append(msg)

    def quit(self):
        pass


class FakeIMAP:
    """Stand-in IMAP server holding (uid:str, raw:bytes) messages."""

    def __init__(self, messages, uidvalidity="999001"):
        self.messages = list(messages)
        self.uidvalidity = uidvalidity
        self.logged_out = False
        self.selected = None

    def status(self, mailbox, what):
        return ("OK", [f"{mailbox} (UIDVALIDITY {self.uidvalidity})".encode()])

    def select(self, mailbox, readonly=False):
        self.selected = mailbox
        return ("OK", [str(len(self.messages)).encode()])

    def uid(self, command, *args):
        command = command.upper()
        if command == "SEARCH":
            return ("OK", [b" ".join(u.encode() for u, _ in self.messages)])
        if command == "FETCH":
            want = args[0].decode() if isinstance(args[0], bytes) else str(args[0])
            for u, raw in self.messages:
                if u == want:
                    return ("OK", [(f"{u} (UID {u} BODY[] {{{len(raw)}}})".encode(), raw)])
            return ("OK", [None])
        return ("OK", [b""])

    def logout(self):
        self.logged_out = True
