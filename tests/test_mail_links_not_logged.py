"""Verification / reset links of a mail that was not sent are never written to the log."""
from __future__ import annotations

import logging


def test_the_mail_links_are_not_written_to_the_log_when_the_mail_is_not_sent(monkeypatch, caplog):
    from motor_ai_sim import auth_email as E
    from motor_ai_sim.routes import auth_local as AL
    monkeypatch.setattr(E, "send", lambda *a, **k: False)
    monkeypatch.setattr(AL.U, "issue_short_link", lambda *a, **k: "SECRETLINKID123")
    monkeypatch.setattr(AL.U, "mark_pending_approval", lambda *a, **k: None)
    with caplog.at_level(logging.WARNING):
        AL._send_verification("someone@example.test")
        AL._send_reset_mail("someone@example.test")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "SECRETLINKID123" not in text and "http" not in text
    assert "verification mail" in text and "password-reset mail" in text and "someone@example.test" in text
