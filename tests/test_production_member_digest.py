"""ONE production e-mail per member per day (owner 2026-11-19).

Pinned: publishing a revision mails nobody (the revision, the Discussion message and the acknowledgement stay at once); several
revisions of a day and several productions make ONE mail per member; nothing new makes none; an urgent revision is mailed at once and
is listed in the next digest as already sent; a restart or a second run the same day sends nothing twice and a missed day is sent at
the next run with everything since; the reminders count from the digest.  Mail goes through a fake deliver; everything lives in tmp dirs.
"""
from __future__ import annotations

import contextlib
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from motor_ai_sim.digest import build as B
from motor_ai_sim.digest import member_daily as MD
from motor_ai_sim.digest import store as DS
from motor_ai_sim.production import models, revision_ack as RK, revision_alert as RA, routes as R, store
from tests.test_production_revision_alert import FakeMail, bil

DIE = "CIANO14 40 new"
CFG1, CFG2 = "L12", "L20"
OWNER, ENG, OEM = "owner@example.com", "eng@example.com", "factory@example.com"
USER = {"email": ENG, "role": "engineer"}


def ts(day: int, hour: int = 12) -> float:
    return datetime(2026, 11, day, hour, 0, tzinfo=timezone.utc).timestamp()


class Out:
    """The fake SMTP: collects the messages."""

    def __init__(self, fail=()):
        self.msgs, self.fail = [], set(fail)

    def __call__(self, msg):
        if msg["To"] in self.fail:
            raise RuntimeError("smtp down")
        self.msgs.append(msg)

    def to(self, email):
        return [m for m in self.msgs if m["To"] == email]


@contextlib.contextmanager
def world(configs=(CFG1,), members=None):
    keys = ("MOTRES_PRODUCTION_ROOT", "MOTRES_DIGEST_ROOT", "MAILING_LOG_DIR", MD.ENV_ON, MD.ENV_HOUR, MD.ENV_TZ)
    saved = {k: os.environ.get(k) for k in keys}
    root = Path(tempfile.mkdtemp())
    os.environ.update({"MOTRES_PRODUCTION_ROOT": str(root / "prod"), "MOTRES_DIGEST_ROOT": str(root / "dig"),
                       "MAILING_LOG_DIR": str(root / "mail")})
    for k in (MD.ENV_ON, MD.ENV_HOUR, MD.ENV_TZ):
        os.environ.pop(k, None)
    old_send, mail = RA.SEND, FakeMail()
    RA.SEND = mail
    import motor_ai_sim.digest.build as bld
    real_resolve, real_motor = bld.resolve_recipients, bld.motor_visible
    mem = members if members is not None else [{"email": OWNER, "role": "owner"}, {"email": ENG, "role": "engineer"}, {"email": OEM, "role": "oem"}]
    bld.resolve_recipients = lambda group, settings, **kw: ([{"email": e, "name": e.split("@")[0], "role": r, "lang": "zh-CN" if r == "oem" else "en"}
                                                             for e, r in group.members], [])
    bld.motor_visible = lambda *a, **k: True
    try:
        for cfg in configs:
            prod = models.normalize_production({"members": mem}, DIE, cfg)
            store.save_file(DIE, cfg, "production.json", prod)
            for f, key in (("documents.json", "documents"), ("revisions.json", "revisions"), ("threads.json", "threads")):
                store.save_file(DIE, cfg, f, {key: []})
        yield mail
    finally:
        RA.SEND, bld.resolve_recipients, bld.motor_visible = old_send, real_resolve, real_motor
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def publish(cfg=CFG1, summary="Shaft thread added", **kw):
    return R._publish_revision(DIE, cfg, USER, bil(summary, "增加轴螺纹"), [bil("M5 thread", "M5 螺纹")], **kw)


def stamp_audit_at(cfg, when):
    """The audit of the last event is read with its own time; move every event of this production to ``when`` (the fake clock)."""
    p = store.audit_path(DIE, cfg)
    import json
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    for r in rows:
        r["time"] = datetime.fromtimestamp(when, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_publishing_mails_nobody_but_records_everything_at_once():
    with world() as mail:
        rev = publish()
        assert mail.sent == []
        assert rev["alert"]["mode"] == "digest" and {r["mail"] for r in rev["alert"]["recipients"]} == {"digest"}
        assert rev["alert"]["thread_id"] and rev["number"] == "1.0"                       # the Discussion message and the revision are there
        assert store.load_file(DIE, CFG1, "revisions.json")["revisions"][0]["alert"]["mode"] == "digest"


def test_several_revisions_in_a_day_and_two_productions_make_one_mail_per_member():
    with world(configs=(CFG1, CFG2)):
        publish(CFG1, "First"); publish(CFG1, "Second"); publish(CFG2, "Third")
        for c in (CFG1, CFG2):
            stamp_audit_at(c, ts(19, 1))
        out = Out()
        rows = MD.run(ts(19, 1) + 8 * 3600, deliver=out, delay_s=0, sleep=lambda s: None, base_url="https://x.example", sender="n@x.example")
        assert sorted(r["email"] for r in rows) == sorted([OWNER, ENG, OEM]) and all(r["status"] == "sent" for r in rows)
        assert len(out.msgs) == 3                                                         # one mail per member, not one per revision
        body = out.to(OWNER)[0].get_body(("plain",)).get_content()
        for needle in ("First", "Second", "Third", "Revision 1.0", "Revision 1.1", "Open and acknowledge", "已发布", "打开并确认"):
            assert needle in body, needle
        assert body.count("Open and acknowledge: ") == 2                                  # one link per production
        assert body.count("/production/") == 4                                            # 2 productions x (EN + ZH) link lines, none on the lines
        assert "2 productions" in out.to(OWNER)[0]["Subject"] and "生产更新" in out.to(OWNER)[0]["Subject"]


def test_nothing_new_makes_no_mail_and_a_second_run_the_same_day_sends_nothing():
    with world():
        out = Out()
        assert MD.run(ts(19, 0), deliver=out, delay_s=0, sleep=lambda s: None) == [] and out.msgs == []
        publish()
        stamp_audit_at(CFG1, ts(19, 1))
        MD.run(ts(19, 1) + 8 * 3600, deliver=out, delay_s=0, sleep=lambda s: None)
        n = len(out.msgs)
        assert n == 3
        MD.run(ts(19, 1) + 9 * 3600, deliver=out, delay_s=0, sleep=lambda s: None)       # the hourly timer runs again
        assert len(out.msgs) == n
        MD.run(ts(20, 1) + 8 * 3600, deliver=out, delay_s=0, sleep=lambda s: None)       # next day, nothing new
        assert len(out.msgs) == n


def test_it_waits_for_the_send_hour_in_the_configured_zone():
    with world():
        publish()
        stamp_audit_at(CFG1, ts(19, 0))
        out = Out()
        # 06:00 UTC = 14:00 Shanghai: due; 23:00 UTC the day before = 07:00 Shanghai: not due
        assert MD.run(ts(18, 23), deliver=out, delay_s=0, sleep=lambda s: None) == []
        assert MD.due(ts(18, 23)) is False and MD.due(ts(19, 0)) is True               # 00:00 UTC = 08:00 Shanghai
        os.environ[MD.ENV_HOUR] = "10"
        assert MD.due(ts(19, 0)) is False and MD.due(ts(19, 3)) is True


def test_a_restart_between_the_send_and_the_state_file_neither_repeats_nor_loses():
    with world():
        publish()
        stamp_audit_at(CFG1, ts(19, 1))
        out = Out()
        now = ts(19, 1) + 8 * 3600
        MD.run(now, deliver=out, delay_s=0, sleep=lambda s: None)
        st = MD.load_state()
        st["through"].clear()                                                           # the process died before committing
        st["pending"] = {OWNER: {"campaign": MD.campaign_id(now), "through": now}}
        MD.save_state(st)
        again = Out()
        MD.run(now + 600, deliver=again, delay_s=0, sleep=lambda s: None)
        assert again.msgs == []                                                         # the log says sent: not twice
        assert MD.load_state()["through"][OWNER] >= now                                 # and the watermark was committed from the log


def test_a_missed_day_is_sent_at_the_next_run_with_everything_since():
    with world():
        publish(summary="Monday change")
        stamp_audit_at(CFG1, ts(16, 1))
        out = Out(fail=(OWNER,))
        MD.run(ts(16, 1) + 8 * 3600, deliver=out, delay_s=0, sleep=lambda s: None)     # the owner's mail fails; ENG and OEM got theirs
        assert [m["To"] for m in out.msgs].count(OWNER) == 0 and len(out.msgs) == 2
        later = Out()
        MD.run(ts(18, 1) + 8 * 3600, deliver=later, delay_s=0, sleep=lambda s: None)   # two days later
        assert [m["To"] for m in later.msgs] == [OWNER]                                # only the one who missed it
        assert "Monday change" in later.msgs[0].get_body(("plain",)).get_content()


def test_an_urgent_revision_is_mailed_at_once_and_listed_in_the_next_digest_as_already_sent():
    with world() as mail:
        rev = publish(urgent=True)
        assert rev["alert"]["mode"] == "urgent" and len(mail.sent) == 3
        stamp_audit_at(CFG1, ts(19, 1))
        out = Out()
        MD.run(ts(19, 1) + 8 * 3600, deliver=out, delay_s=0, sleep=lambda s: None)
        body = out.to(ENG)[0].get_body(("plain",)).get_content()
        assert "already e-mailed when it was published" in body and "发布时已邮件通知" in body


def test_the_member_sees_only_what_his_role_may_see():
    with world():
        store.save_file(DIE, CFG1, "documents.json", {"documents": []})
        publish()
        stamp_audit_at(CFG1, ts(19, 1))
        out = Out()
        MD.run(ts(19, 1) + 8 * 3600, deliver=out, delay_s=0, sleep=lambda s: None)
        assert out.to(OEM)[0]["Subject"].startswith("[MOTRES]") and "Shaft thread added" in out.to(OEM)[0].get_body(("plain",)).get_content()


def test_the_digest_stamps_the_revision_and_the_reminders_count_from_it():
    with world():
        publish()
        rev = store.load_file(DIE, CFG1, "revisions.json")["revisions"][0]
        pub = RK._ts(rev["alert"]["sent_at"])
        prod = store.load_file(DIE, CFG1, "production.json")
        # before any digest: nothing to remind about, even 100 h after the publication
        rows = RK.pending(DIE, CFG1, prod, [rev], pub + 100 * 3600)
        assert rows and not any(r["alerted"] for r in rows)
        stamp_audit_at(CFG1, ts(19, 1))
        digest_at = ts(19, 1) + 8 * 3600
        MD.run(digest_at, deliver=Out(), delay_s=0, sleep=lambda s: None)
        rev = store.load_file(DIE, CFG1, "revisions.json")["revisions"][0]
        assert {x["email"] for x in rev["digest_sent"]} == {OWNER, ENG, OEM}
        got = RK._ts([x for x in rev["digest_sent"] if x["email"] == OEM][0]["sent_at"])
        rows = RK.pending(DIE, CFG1, prod, [rev], got + 30 * 3600)
        assert [(r["email"], r["alerted"], int(r["hours"])) for r in rows] == [(OEM, True, 30)]    # 30 h after the DIGEST, not the publication
        assert RK.due_level(rows[0]["hours"], rows[0]["reminders"]) == 24


def test_the_old_per_production_digests_do_not_send_production_groups_while_this_is_on():
    from motor_ai_sim.digest import approval as AP
    seen = {}
    real = B.run
    B.run = lambda now=None, *, scopes=None, write=True, results=None: seen.setdefault("scopes", scopes) or []
    try:
        AP.run_daily(ts(19, 1), send=False)
        assert seen["scopes"] is not None and all(not s.startswith("production:") for s in seen["scopes"])
    finally:
        B.run = real
