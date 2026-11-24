"""R3 of the production revision alerts (owner 2026-11-15): acknowledgement status, reminders after 24 h and 72 h (fake clock),
the owner's daily report line, no mail from a sandbox."""
from __future__ import annotations

import io
import contextlib
import os

import pytest

from motor_ai_sim.digest import store as DS
from motor_ai_sim.production import models, revision_ack as RK, revision_alert as RA, routes as R, store
from tests.test_production_revision_alert import CFG, DIE, ENG, OEM1, OEM2, OWNER, bil, production, publish

H = 3600.0


def t0_of(rev):
    return DS.parse_ts(rev["alert"]["sent_at"])


def ack(email, number="1.0"):
    def m(d):
        for r in d["revisions"]:
            if r["number"] == number:
                r.setdefault("acknowledgements", []).append({"email": email, "time": models._now()})
    store.mutate_file(DIE, CFG, "revisions.json", m)


def revisions():
    return store.load_file(DIE, CFG, "revisions.json")["revisions"]


def test_only_the_factory_must_acknowledge_and_the_status_lists_everybody():
    with production():
        rev = publish()
        ack(OEM1)
        prod = store.load_file(DIE, CFG, "production.json")
        rows = {r["email"]: r for r in RK.ack_status(prod, revisions()[0])}
        assert list(rows) == [OWNER, ENG, OEM1, OEM2]
        assert [r["required"] for r in rows.values()] == [False, False, True, True]
        assert rows[OEM1]["acked_at"] and rows[OEM2]["acked_at"] is None and rows[ENG]["acked_at"] is None
        assert [m["email"] for m in RK.required_members(prod)] == [OEM1, OEM2]
        assert rev["alert"]["sent_at"]


def test_no_reminder_before_24_hours_one_at_24_one_at_72_and_never_twice():
    with production() as mail:
        rev = publish()
        t0 = t0_of(rev)
        mail.sent.clear()
        assert RK.run_reminders(t0 + 23.9 * H) == [] and mail.sent == []
        rows = RK.run_reminders(t0 + 24.1 * H)
        assert sorted((r["email"], r["level"], r["mail"]) for r in rows) == [(OEM1, 24, "sent"), (OEM2, 24, "sent")]
        assert sorted(m[0] for m in mail.sent) == [OEM1, OEM2]                                  # never the engineer or the owner
        subject, body = mail.sent[0][1], mail.sent[0][2]
        assert "Reminder (24 h)" in subject and "action required: acknowledge" in subject and "提醒（24 小时）" in subject
        assert "revision 1.0 was published 24 hours ago" in body and "版本 1.0 已于 24 小时前发布" in body and "/production/" in body and "item=1.0" in body
        assert RA.SUPERSEDED_SENTENCE_EN in body and RA.SUPERSEDED_SENTENCE_ZH in body
        assert RK.run_reminders(t0 + 30 * H) == [] and RK.run_reminders(t0 + 60 * H) == []     # not again at the same level
        rows72 = RK.run_reminders(t0 + 72.5 * H)
        assert sorted((r["email"], r["level"]) for r in rows72) == [(OEM1, 72), (OEM2, 72)]
        assert "Reminder (72 h)" in mail.sent[-1][1]
        assert RK.run_reminders(t0 + 100 * H) == [] and RK.run_reminders(t0 + 500 * H) == []   # nothing after 72 h
        assert len(mail.sent) == 4
        assert [x["level"] for x in revisions()[0]["reminders"] if x["email"] == OEM1] == [24, 72]
        audit = [__import__("json").loads(x) for x in store.audit_path(DIE, CFG).read_text().splitlines()]
        assert len([a for a in audit if a["action"] == "revision_reminder"]) == 4


def test_a_member_who_acknowledges_gets_no_further_reminder():
    with production() as mail:
        rev = publish()
        t0 = t0_of(rev)
        mail.sent.clear()
        RK.run_reminders(t0 + 25 * H)
        ack(OEM1)
        mail.sent.clear()
        rows = RK.run_reminders(t0 + 73 * H)
        assert [(r["email"], r["level"]) for r in rows] == [(OEM2, 72)] and [m[0] for m in mail.sent] == [OEM2]


def test_a_missed_run_sends_only_the_level_that_is_due_now():
    with production() as mail:
        rev = publish()
        t0 = t0_of(rev)
        mail.sent.clear()
        rows = RK.run_reminders(t0 + 80 * H)
        assert sorted((r["email"], r["level"]) for r in rows) == [(OEM1, 72), (OEM2, 72)] and len(mail.sent) == 2


def test_a_failed_mail_is_not_recorded_and_the_next_run_tries_again():
    with production() as mail:
        rev = publish()
        t0 = t0_of(rev)
        mail.sent.clear()
        mail.fail = {OEM1}
        rows = RK.run_reminders(t0 + 25 * H)
        assert {(r["email"], r["mail"]) for r in rows} == {(OEM1, "failed"), (OEM2, "sent")}
        mail.fail = set()
        rows2 = RK.run_reminders(t0 + 26 * H)
        assert [(r["email"], r["level"], r["mail"]) for r in rows2] == [(OEM1, 24, "sent")]
        assert RK.run_reminders(t0 + 27 * H) == []


def test_an_old_revision_without_an_alert_is_listed_for_the_owner_but_never_mailed():
    legacy = {"number": "1.0", "date": "2026-10-01T08:00:00Z", "author_email": ENG, "summary": bil("old"), "changes": [], "acknowledgements": []}
    with production(revisions=[legacy]) as mail:
        now = DS.parse_ts("2026-11-15T08:00:00Z")
        assert RK.run_reminders(now) == [] and mail.sent == []
        rows = RK.report_rows(now)
        assert [(r["email"], r["number"], r["alerted"]) for r in rows] == [(OEM1, "1.0", False), (OEM2, "1.0", False)]
        assert int(rows[0]["hours"]) == 24 * 45


def test_a_sandbox_sends_no_reminder_and_never_calls_the_mail_module():
    from motor_ai_sim import auth_email
    saved = {k: os.environ.pop(k, None) for k in ("SMTP_USER", "SMTP_PASS", "SMTP_PASSWORD")}
    real, calls = auth_email._send, []
    auth_email._send = lambda *a, **k: calls.append(a) or True
    try:
        with production() as _mail:
            RA.SEND = RA._send_default
            rev = publish()
            rows = RK.run_reminders(t0_of(rev) + 25 * H)
            assert {r["mail"] for r in rows} == {"not_configured"} and calls == []
            again = RK.run_reminders(t0_of(rev) + 26 * H)
            assert {r["mail"] for r in again} == {"not_configured"} and calls == []             # nothing was delivered: still due, never recorded
            assert not store.load_file(DIE, CFG, "revisions.json")["revisions"][0].get("reminders")
    finally:
        auth_email._send = real
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_the_owner_report_has_one_line_per_waiting_member_and_none_when_everybody_acknowledged():
    from motor_ai_sim.owner_report import sources as SRC
    with production():
        rev = publish()
        now = t0_of(rev) + 50 * H
        blocks = SRC._unacknowledged_revisions(now)
        assert len(blocks) == 1 and blocks[0].title == "Revisions waiting for the factory's acknowledgement"
        texts = [ln.text for ln in blocks[0].lines]
        assert len(texts) == 2 and all("NOT acknowledged by" in t and "50 h since the alert" in t and f"{DIE} / {CFG}" in t for t in texts)
        assert {ln.side for ln in blocks[0].lines} == {"factory"} and "changes" in blocks[0].lines[0].path
        RK.run_reminders(now)
        texts2 = [ln.text for ln in SRC._unacknowledged_revisions(now)[0].lines]
        assert all("(24 h reminder sent)" in t for t in texts2)
        ack(OEM1)
        assert len(SRC._unacknowledged_revisions(now)[0].lines) == 1
        ack(OEM2)
        assert SRC._unacknowledged_revisions(now) == []


def test_the_daily_run_sends_the_reminders_and_a_failure_is_reported_not_raised():
    from motor_ai_sim.owner_report import run as RUN
    with production() as mail:
        rev = publish()
        mail.sent.clear()
        assert RUN.send_revision_reminders(t0_of(rev) + 25 * H) == "" and sorted(m[0] for m in mail.sent) == [OEM1, OEM2]
        real = RK.run_reminders

        def boom(now):
            raise RuntimeError("disk full")
        RK.run_reminders = boom
        try:
            err = RUN.send_revision_reminders(t0_of(rev) + 30 * H)
        finally:
            RK.run_reminders = real
        assert err.startswith("revision reminders could not be sent: RuntimeError")


def test_the_command_line_lists_and_sends():
    with production() as mail:
        rev = publish()
        real_time = RK.time.time
        RK.time.time = lambda: t0_of(rev) + 26 * H
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                assert RK.main([]) == 0
            text = out.getvalue()
            assert OEM1 in text and "waiting 26 h" in text and "due: 24h reminder" in text and mail.sent[-1][1].startswith("[MOTRES] ") is True
            n = len(mail.sent)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                assert RK.main(["--send"]) == 0
            assert "2 reminder(s)" in out.getvalue() and len(mail.sent) == n + 2
        finally:
            RK.time.time = real_time
