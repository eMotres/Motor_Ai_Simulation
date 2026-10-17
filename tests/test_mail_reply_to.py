"""MAIL_REPLY_TO puts a Reply-To on every message that has none (owner 2026-10-17: answers go to support@, not the SMTP account)."""
from email.message import EmailMessage

from motor_ai_sim import auth_email as AE


class _SMTP:
    sent = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self, **k):
        pass

    def login(self, *a):
        pass

    def send_message(self, msg):
        _SMTP.sent.append(msg)


def test_reply_to_is_added_only_when_missing(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "u@example.com")
    monkeypatch.setenv("SMTP_PASS", "x")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("MAIL_REPLY_TO", "support@example.com")
    monkeypatch.setattr(AE, "_smtp_pass", lambda: "x")
    monkeypatch.setattr(AE.smtplib, "SMTP", _SMTP)
    _SMTP.sent.clear()
    m = EmailMessage()
    m["To"], m["Subject"] = "a@b.com", "s"
    m.set_content("b")
    AE.deliver(m)
    own = EmailMessage()
    own["To"], own["Subject"], own["Reply-To"] = "a@b.com", "s", "person@c.com"
    own.set_content("b")
    AE.deliver(own)
    assert _SMTP.sent[0]["Reply-To"] == "support@example.com" and _SMTP.sent[1]["Reply-To"] == "person@c.com"
