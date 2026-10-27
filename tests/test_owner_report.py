"""The owner's daily report (motor_ai_sim.owner_report), owner 2026-10-27.

Under test: the 24 h window before 06:00 Europe/Ljubljana; the fixed section order (users, activity, projects, productions, motors,
digests) in body and summary; an empty day is still a mail ("no changes"); a broken source is shown, not hidden; the recipients are
locked to the owner; the scheduled run sends once per date across restarts / processes; the digest DRAFTS are generated at the run
and appear with their full text and an Admin link, while nothing is ever sent to a group's recipients; the admin routes.

Temp stores and a fake SMTP only.  No real address is used.
"""
from __future__ import annotations

import ast
import inspect
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.owner_report import build as B
from motor_ai_sim.owner_report import run as R
from motor_ai_sim.owner_report import send as S
from motor_ai_sim.owner_report import sources as SRC
from tests.test_comm_prefs import ADMIN, ANNA, BOB, LI, _login, env  # noqa: F401
from tests.test_digest import PID, DIE, CFG, PSCOPE, XSCOPE, T0, d  # noqa: F401

client = TestClient(app)
DATE = "2026-10-28"
SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim"
W0, W1 = B.window_for(DATE)
WORLD_DAY = B.report_date_for(T0 + 17 * 3600)             # the report whose window holds the digest world's events (T0 - 1 h ...)


@pytest.fixture()
def o(d, tmp_path, monkeypatch):                         # noqa: F811
    """The digest world plus isolated owner-report, audit, cluster, revision and ledger stores."""
    from motor_ai_sim import duty_results as DR
    monkeypatch.setenv("MOTRES_OWNER_REPORT_ROOT", str(tmp_path / "owner_report"))
    monkeypatch.setenv("CLUSTER_MONITOR_DIR", str(tmp_path / "cluster"))
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    monkeypatch.setenv("CAD_DOWNLOAD_LOG", str(tmp_path / "cad_downloads.jsonl"))
    monkeypatch.setenv("DESIGN_AGENT_STORE_DIR", str(tmp_path / "design_agent"))
    monkeypatch.setenv("MOTRES_MOTOR_REVISIONS_ROOT", str(tmp_path / "motor_revisions"))
    monkeypatch.setenv("MOTRES_MOTOR_TESTS_ROOT", str(tmp_path / "motor_tests"))
    monkeypatch.delenv("OWNER_REPORT_TO", raising=False)
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)
    monkeypatch.setattr(DR, "shared_store_path", lambda: tmp_path / "shared_duty_results.json")
    d["mp"].setattr("motor_ai_sim.nda.admin_address", lambda: ADMIN)
    d["otmp"] = tmp_path
    return d


# -- fixture rows for the pure builder ---------------------------------------------------------------------------------------------

def line(ts_off, text, path="", who="", side=""):
    return B.Line(W0 + ts_off, text, path, who, side)


def fixture_sources(**over) -> B.Sources:
    base = dict(
        users=lambda s, e: [{
            "email": "new.user@example.com", "name": "New User", "side": "customer", "company": "Example Motors", "country": "DE",
            "signup": "email", "verified": "verified 2026-10-27T09:00 by link", "nda": "online, signed 2026-10-27, version v3",
            "verification": "VERIFIED - checks passed [website_ok]", "agreement": "accepted 2026-10-27 mode FREE (v2, Example Motors)",
            "data_sharing": "consent given 2026-10-27", "projects": ["Example X1 (customer)"],
            "first_actions": ["first sign-in 27 09:02 (login)", "modules configure, motors", "runs em 2"],
            "created": "27 08:59 (Europe/Ljubljana)", "path": "/?tab=admin&section=users"}],
        activity=lambda s, e: [{
            "email": "new.user@example.com", "name": "New User", "side": "customer", "country": "DE", "logins": 2, "ips": ["ip:a0745a1a6d56"],
            "active_s": 2700, "sessions": 2, "tabs": [{"tab": "configure", "seconds": 1500}], "runs": 3, "failed": 1,
            "run_kinds": {"em": 2, "thermal": 1}, "downloads": {"reports": 1, "3D/STEP": 1}, "assistant": 4,
            "errors": ["run failed: em CIANO28 85/L12"]},
            {"email": "staff.member@example.com", "name": "", "side": "staff", "logins": 1, "ips": [], "active_s": 600, "sessions": 1}],
        projects=lambda s, e: [B.Block("Project: Example X1 [design]", [
            line(3600, "specification version 2 submitted", "/projects/example-x1/specification", "anna@example.com", "customer"),
            line(7200, "variant linked: CIANO28 85/L12", "/projects/example-x1/motors?die=CIANO28%2085&config=L12", "eng@example.com", "staff")],
            "/projects/example-x1")],
        productions=lambda s, e: [B.Block("Production: Example build [DIE / CFG]", [
            line(1800, "Revision 1.2 published: Slot liner thicker", "/production/DIE/CFG/changes?item=1.2", "", "system")],
            "/production/DIE/CFG")],
        motors=lambda s, e: [B.Block("Runs finished", [line(5000, "em run FAILED: CIANO28 85/L12", "", "new.user@example.com", "customer")])],
        digests=lambda s, e: [{
            "id": "d-0123456789abcdef", "scope": "project:example-x1", "kind": "project", "title": "Example X1", "status": "pending",
            "new": True, "period_start": "2026-10-27T05:00:00Z", "period_end": "2026-10-28T05:00:00Z",
            "recipients": [{"email": "anna@example.com", "role": "owner", "lang": "en"}, {"email": "li@example.com", "role": "oem", "lang": "zh-CN"}],
            "text": "Example X1\nWhat changed since 2026-10-27:\n\n- Specification version 2 submitted\n  https://app.example.test/projects/example-x1/specification\n",
            "path": B.admin_path("digests", draft="d-0123456789abcdef")}])
    base.update(over)
    return B.Sources(**base)


def empty_sources(**over) -> B.Sources:
    base = {k: (lambda s, e: []) for k in B.SECTION_ORDER}
    base.update(over)
    return B.Sources(**base)


# -- the window and the order ------------------------------------------------------------------------------------------------------

def test_window_is_the_24_hours_before_0600_ljubljana():
    s, e = B.window_for("2026-10-28")
    assert e - s == 86400.0
    assert B.local_stamp(e).endswith("06:00") and B.local_stamp(s).endswith("06:00")
    assert B.local_stamp(e).startswith("28") and B.local_stamp(s).startswith("27")
    s2, e2 = B.window_for("2026-03-29")                       # daylight saving starts that night: the window is 23 h, never a wrong hour
    assert e2 - s2 == 23 * 3600.0 and B.local_stamp(s2).endswith("06:00") and B.local_stamp(e2).endswith("06:00")
    s3, e3 = B.window_for("2026-10-25")                       # and ends that night (last Sunday of October): 25 h
    assert e3 - s3 == 25 * 3600.0


def test_the_report_date_that_is_due():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    z = ZoneInfo("Europe/Ljubljana")
    assert B.report_date_for(datetime(2026, 10, 28, 6, 0, tzinfo=z).timestamp()) == "2026-10-28"
    assert B.report_date_for(datetime(2026, 10, 28, 9, 30, tzinfo=z).timestamp()) == "2026-10-28"
    assert B.report_date_for(datetime(2026, 10, 28, 5, 59, tzinfo=z).timestamp()) == "2026-10-27"


def test_section_order_is_pinned_in_the_body_and_the_summary():
    assert B.SECTION_ORDER == ("users", "activity", "projects", "productions", "motors", "digests")
    assert [B.TITLES[s] for s in B.SECTION_ORDER] == ["New users", "User activity", "Projects", "Productions", "Motors", "Digests"]
    rep = B.build(DATE, fixture_sources(), base_url="https://app.example.test")
    assert [s.id for s in rep.sections] == list(B.SECTION_ORDER)
    heads = ["1. New users", "2. User activity", "3. Projects", "4. Productions", "5. Motors", "6. Digests"]
    pos = [rep.text.index(h) for h in heads]
    assert pos == sorted(pos)
    hpos = [rep.html.index(h) for h in heads]
    assert hpos == sorted(hpos)
    titles = ["New users", "User activity", "Projects", "Productions", "Motors", "Digests"]
    spos = [rep.summary.index(t) for t in titles]
    assert spos == sorted(spos)
    assert rep.summary.startswith("New users 1 | User activity 2 | Projects 2 | Productions 1 | Motors 1 | Digests 1")


# -- the content -------------------------------------------------------------------------------------------------------------------

def test_empty_day_is_a_heartbeat():
    rep = B.build(DATE, empty_sources(), base_url="https://app.example.test")
    assert rep.text.count("no changes") >= 5 and "no digests today" in rep.text
    assert "no digests today" in rep.summary and rep.summary.count("no changes") == 5
    assert rep.counts == {s: 0 for s in B.SECTION_ORDER}
    assert rep.subject == f"AeroStator owner report {DATE}" and "<html" in rep.html and "no changes" in rep.html


def test_sections_from_fixtures_one_line_per_item_staff_and_customer_marked():
    rep = B.build(DATE, fixture_sources(), base_url="https://app.example.test")
    t = rep.text
    # new users: the full record
    for needle in ("new.user@example.com", "Company: Example Motors", "Country: DE", "Sign-up: email", "NDA: online, signed 2026-10-27, version v3",
                   "Verification: VERIFIED", "Agreement: accepted 2026-10-27 mode FREE", "Data sharing: consent given",
                   "Projects: Example X1 (customer)", "First actions: first sign-in", "[customer]"):
        assert needle in t, needle
    # activity: one line per user with sign-ins, IPs, time, modules, runs, downloads, assistant, errors
    act = next(ln for ln in t.splitlines() if ln.startswith("- new.user@example.com (New User)") and "sign-in" in ln)
    for needle in ("2 sign-ins (client ip:a0745a1a6d56)", "45 min active, 2 session(s)", "configure 25 min", "runs 3, 1 failed (em 2, thermal 1)",
                   "downloads: reports 1, 3D/STEP 1", "assistant 4", "run failed"):
        assert needle in act, needle
    assert "[staff]" in t and "[staff]" in next(ln for ln in t.splitlines() if "staff.member@example.com" in ln)
    # projects / productions / motors: a link to the exact page, absolute
    assert "-> https://app.example.test/projects/example-x1/specification" in t
    assert "-> https://app.example.test/production/DIE/CFG/changes?item=1.2" in t
    assert "[customer] specification version 2 submitted - anna@example.com" in t
    assert "[staff] variant linked: CIANO28 85/L12 - eng@example.com" in t
    assert "em run FAILED" in t
    assert 'href="https://app.example.test/projects/example-x1/specification"' in rep.html


def test_digest_section_has_the_full_text_recipients_and_the_admin_link():
    rep = B.build(DATE, fixture_sources(), base_url="https://app.example.test")
    t = rep.text
    assert "Recipients (2): anna@example.com (owner), li@example.com (oem)" in t
    assert "Specification version 2 submitted" in t and "https://app.example.test/projects/example-x1/specification" in t
    assert ("https://app.example.test/?tab=admin&section=digests&draft=d-0123456789abcdef") in t
    assert "NEW" in t and "Preview and Send" in t


def test_a_broken_source_is_shown_not_reported_as_no_changes():
    def boom(s, e):
        raise RuntimeError("ledger locked")
    rep = B.build(DATE, fixture_sources(motors=boom), base_url="https://app.example.test")
    motors = next(s for s in rep.sections if s.id == "motors")
    assert motors.error and "source unavailable: RuntimeError: ledger locked" in rep.text
    assert "Motors unavailable" in rep.summary and any("Motors" in e for e in rep.errors)
    assert "Source problems" in rep.text and "Source problems" in rep.html


# -- recipients ------------------------------------------------------------------------------------------------------------------------

def test_recipients_are_locked_to_the_owner(o, monkeypatch):
    assert S.recipients() == [ADMIN]                                   # default = the admin address
    monkeypatch.setenv("OWNER_REPORT_TO", f"{ANNA}, {ADMIN}; {BOB}")   # customers cannot be configured in
    assert S.recipients() == [ADMIN]
    monkeypatch.setenv("OWNER_REPORT_TO", f"{ANNA},{LI}")
    assert S.recipients() == [ADMIN]                                   # nothing allowed left: back to the owner
    assert "to" not in inspect.signature(S.send_report).parameters and "recipients" not in inspect.signature(S.send_report).parameters
    rep = B.build(DATE, empty_sources(), base_url="https://app.example.test")
    got = []
    res = S.send_report(rep, deliver=got.append)
    assert res["sent"] and res["to"] == [ADMIN]
    assert got[0]["To"] == ADMIN and not got[0]["Cc"] and not got[0]["Bcc"]
    parts = {p.get_content_type() for p in got[0].walk()}
    assert parts == {"multipart/alternative", "text/plain", "text/html"}            # text + HTML, no images, no attachments


def test_a_second_admin_account_may_be_configured(o, monkeypatch):
    o["U"].create_user("second.admin@example.com", "password-second", role="admin")
    monkeypatch.setenv("OWNER_REPORT_TO", f"{ADMIN}, second.admin@example.com")
    assert S.recipients() == [ADMIN, "second.admin@example.com"]


def test_send_failure_is_reported_not_raised(o):
    def down(msg):
        raise OSError("connection refused")
    res = S.send_report(B.build(DATE, empty_sources()), deliver=down)
    assert not res["sent"] and "OSError" in res["error"]


# -- the schedule: once per date, across restarts -------------------------------------------------------------------------------

def due(day="2026-10-28", hour=6, minute=5):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    y, m, dd = (int(x) for x in day.split("-"))
    return datetime(y, m, dd, hour, minute, tzinfo=ZoneInfo("Europe/Ljubljana")).timestamp()


def test_the_run_sends_once_per_date_across_restarts(o):
    got = []
    kw = dict(deliver=got.append, sources=empty_sources(), make_drafts=False)
    r1 = R.run_daily(now=due(), **kw)
    assert r1["status"] == "sent" and r1["date"] == "2026-10-28" and len(got) == 1
    for _ in range(3):                                                   # the timer fires again, the process restarts: nothing more
        again = R.run_daily(now=due(minute=40), **kw)
        assert again["status"] == "already_sent"
    assert len(got) == 1
    assert json.loads((o["otmp"] / "owner_report" / "state.json").read_text())["last_sent_date"] == "2026-10-28"
    nxt = R.run_daily(now=due("2026-10-29"), **kw)                       # the next morning is a new date
    assert nxt["status"] == "sent" and len(got) == 2
    forced = R.run_daily(now=due("2026-10-29", minute=30), force=True, **kw)
    assert forced["status"] == "sent" and len(got) == 3


def test_a_failed_send_is_retried_and_a_held_lock_blocks_a_second_process(o):
    got = []

    def down(msg):
        raise OSError("smtp down")
    r = R.run_daily(now=due(), deliver=down, sources=empty_sources(), make_drafts=False)
    assert r["status"] == "failed" and S.get_state().get("last_sent_date") is None
    assert R.run_daily(now=due(hour=7), deliver=got.append, sources=empty_sources(), make_drafts=False)["status"] == "sent"
    with S.RunLock() as lock:                                            # "another process" is mid-run
        assert lock.held
        busy = R.run_daily(now=due("2026-10-29"), deliver=got.append, sources=empty_sources(), make_drafts=False)
    assert busy["status"] == "busy" and len(got) == 1
    assert R.run_daily(now=due("2026-10-29"), deliver=got.append, sources=empty_sources(), make_drafts=False)["status"] == "sent"


def test_a_stale_lock_is_taken_over(o):
    import os
    lk = o["otmp"] / "owner_report" / "run.lock"
    lk.parent.mkdir(parents=True, exist_ok=True)
    lk.write_text("999 0")
    old = time.time() - S.LOCK_STALE_S - 60
    os.utime(lk, (old, old))
    with S.RunLock() as lock:
        assert lock.held


def test_dry_run_sends_nothing_and_writes_nothing(o):
    got = []
    r = R.run_daily(now=due(), dry_run=True, deliver=got.append, sources=fixture_sources())
    assert r["status"] == "dry_run" and not got and not (o["otmp"] / "owner_report").exists()
    assert o["DS"].list_drafts() == []                                   # no drafts made either


# -- the digest drafts at the run ------------------------------------------------------------------------------------------------

def test_the_run_makes_the_drafts_and_puts_their_text_in_the_report_without_mailing_the_groups(o, monkeypatch):
    from motor_ai_sim.digest import approval as AP
    approve_mode(o)                                                      # groups on manual approval: the drafts wait
    spy = []
    monkeypatch.setattr(AP, "send_draft", lambda *a, **k: spy.append(a) or {})
    assert o["DS"].list_drafts() == []
    got = []
    now = B.window_for(WORLD_DAY)[1]
    res = R.run_daily(now=now + 300, deliver=got.append, make_drafts=True)
    assert res["status"] == "sent"
    drafts = o["DS"].list_drafts()
    assert {x["scope"] for x in drafts} >= {PSCOPE, XSCOPE}              # drafts made by the digest package's own scheduler function
    assert all(x["status"] == "pending" for x in drafts)
    text = got[0].get_body(preferencelist=("plain",)).get_content()
    html_body = got[0].get_body(preferencelist=("html",)).get_content()
    for x in drafts:
        assert x["text_body"].strip().splitlines()[0] in text           # the full digest text, as the recipients would get it
        assert f"section=digests&draft={x['id']}" in text and f"section=digests&draft={x['id']}" in html_body.replace("&amp;", "&")
        for r in x["recipients"]:
            assert r["email"] in text                                    # the owner's own mail lists who would get it
    xd = next(x for x in drafts if x["scope"] == XSCOPE)
    assert "Revision 1.1 published" in text and "版本 1.1 已发布" in text   # English and the Chinese part
    assert "6. Digests" in text and "Waiting (" in text and "Preview and Send (Admin -> Digests)" in text
    # nothing went to any group recipient: the report and the "digests ready" notice, both to the owner only
    assert len(got) == 2 and {m["To"] for m in got} == {ADMIN}
    assert got[0]["Subject"] == f"AeroStator owner report {res['date']} · {len(drafts)} digests to approve"
    assert got[1]["Subject"] == f"{len(drafts)} digests ready to check" and got[1]["X-AeroStator-Internal"] == "owner-notice"
    assert not spy and o["delivered"] == []                              # no digest was sent, no auth mail either
    assert all(d_["status"] == "pending" for d_ in o["DS"].list_drafts()) and xd["recipients"]


def test_no_draft_means_no_digests_today(o):
    got = []
    res = R.run_daily(now=B.window_for("2026-12-30")[1] + 60, deliver=got.append, make_drafts=True, sources=SRC.default_sources())
    assert res["status"] == "sent"
    text = got[0].get_body(preferencelist=("plain",)).get_content()
    assert "no digests today" in text


def test_a_failing_draft_run_does_not_stop_the_report(o, monkeypatch):
    from motor_ai_sim.digest import build as DB

    def broken(*a, **k):
        raise RuntimeError("store locked")
    monkeypatch.setattr(DB, "run", broken)                              # the scheduled run calls build.run first
    got = []
    res = R.run_daily(now=due(), deliver=got.append, sources=empty_sources())
    assert res["status"] == "sent"
    assert "digests could not be made" in got[0].get_body(preferencelist=("plain",)).get_content()


# -- the real readers -----------------------------------------------------------------------------------------------------------

def test_new_users_have_the_full_record(o):
    U = o["U"]
    now = time.time()
    U.register_self("fresh.customer@example.com", "a long unique pass phrase 1", name="Fresh Customer")
    U.mark_verified("fresh.customer@example.com", by="link")
    U.set_nda("fresh.customer@example.com", True, by="online", version="v3", company="Fresh GmbH", record_id="r1")
    U.set_data_sharing("fresh.customer@example.com", True, "data_sharing_consent_v1", "2026-10-27T10:00:00Z")
    o["PS"].mutate_project(PID, lambda p: p["members"].append({"email": "fresh.customer@example.com", "role": "customer"}))
    o["PS"].sync_membership_index(PID, [], [{"email": "fresh.customer@example.com", "role": "customer"}])
    from motor_ai_sim import sessions as S_
    S_.record_event("login", email="fresh.customer@example.com", ip="198.51.100.4")
    with U._LOCK:                                                          # the fixture accounts count as old: only the fresh one is new
        users = U._load()
        for e_ in users:
            if e_ != "fresh.customer@example.com":
                users[e_]["created"] = "2020-01-01T00:00:00"
        U._save(users)
    rows = SRC.new_users(now - 120, now + 120)
    row = next(r for r in rows if r["email"] == "fresh.customer@example.com")
    assert row["name"] == "Fresh Customer" and row["side"] == "customer" and row["signup"] == "email"
    assert row["verified"].startswith("verified") and "by link" in row["verified"]
    assert row["nda"].startswith("online") and "v3" in row["nda"]
    assert row["company"] == "Fresh GmbH" and row["data_sharing"].startswith("consent given")
    assert any("Erium X1" in p and "customer" in p for p in row["projects"])
    assert any("first sign-in" in a for a in row["first_actions"])
    assert row["verification"] == "no case"
    assert {r["email"] for r in rows} == {"fresh.customer@example.com"}   # accounts older than the window are not new


def test_user_activity_from_the_real_stores(o):
    from motor_ai_sim import activity as ACT
    from motor_ai_sim import job_usage as JU
    from motor_ai_sim import sessions as S_
    from motor_ai_sim import usage_stats as US
    from motor_ai_sim.log_redaction import hash_ip
    now = time.time()
    S_.record_event("login", email=ANNA, ip="203.0.113.9")
    S_.record_event("login", email=ANNA, ip="203.0.113.9")
    S_.record_event("reject", email=BOB, reason="idle_timeout")
    ACT.record_beat(ANNA, "sid-anna-0001", "configure", now - 200)
    ACT.record_beat(ANNA, "sid-anna-0001", "configure", now - 150)
    JU.record({"run_id": "r1", "ts_start": now - 90, "ts_end": now - 60, "user": ANNA, "client": "web", "kind": "em_transient",
               "machine": "CIANO28 85/L12", "wall_s": 30, "cpu_s": 20, "status": "ok"})
    JU.record({"run_id": "r2", "ts_start": now - 50, "ts_end": now - 40, "user": ANNA, "client": "web", "kind": "thermal",
               "machine": "CIANO28 85/L12", "wall_s": 10, "cpu_s": 8, "status": "failed"})
    US.note(ANNA, "report_pdf", "")
    rows = SRC.user_activity(now - 3600, now + 3600)
    a = next(r for r in rows if r["email"] == ANNA)
    assert a["logins"] == 2 and a["ips"] == [hash_ip("203.0.113.9")] and a["side"] == "customer"
    assert a["sessions"] >= 1 and a["active_s"] > 0 and a["tabs"][0]["tab"] == "configure"
    assert a["runs"] == 2 and a["failed"] == 1 and a["run_kinds"] == {"em": 1, "thermal": 1}
    assert a["downloads"].get("reports") == 1
    assert any("run failed" in e for e in a["errors"])
    b = next(r for r in rows if r["email"] == BOB)
    assert any("idle_timeout" in e for e in b["errors"])
    assert B.activity_line(a).startswith(f"{ANNA}")


def test_projects_productions_and_digests_from_the_real_stores(o):
    s, e = B.window_for(WORLD_DAY)               # the day that holds the world's events
    assert s < T0 - 3600 and T0 < e
    from motor_ai_sim.development import store as PS
    PS.append_jsonl(PID, "audit.jsonl", {"time": T0 - 1800, "actor_email": ANNA, "action": "post",   # the real API's audit
                                         "entity": "thread_message", "entity_id": "msg_x"})          # row of a posted message
    blocks = SRC._fmt_projects(s, e)
    proj = next(b for b in blocks if b.title.startswith("Project: Erium X1"))
    texts = [ln.text for ln in proj.lines]
    assert all(isinstance(t, str) and t for t in texts)                 # the post row is skipped, never a line without text
    assert any("specification version 2 submitted" in t for t in texts) and any("variant linked: CIANO28 85/T9" in t for t in texts)
    assert any(t.startswith("New message from") for t in texts)
    assert not any(" read " in f" {t} " for t in texts)                  # reading is not activity
    sub = next(ln for ln in proj.lines if "specification version 2" in ln.text)
    assert sub.who == ANNA and sub.side == "customer" and sub.path == "/projects/erium-x1/specification"
    prod = next(b for b in SRC._fmt_productions(s, e))
    ptexts = [ln.text for ln in prod.lines]
    assert any(t.startswith("Revision 1.1 published") for t in ptexts) and any("Bill of materials updated" in t for t in ptexts)
    assert not any("download" in t.lower() for t in ptexts)
    assert all(ln.path.startswith("/") for b in (proj, prod) for ln in b.lines if ln.path)


def test_motors_section_reads_audit_revisions_runs_and_results(o):
    from motor_ai_sim import admin_audit as AA
    from motor_ai_sim import duty_results as DR
    from motor_ai_sim import job_usage as JU
    now = time.time()
    AA.record(ADMIN, "die.rename", "CIANO28 85", details={"to": "CIANO28 85b"})
    AA.record(ADMIN, "die.access", "CIANO28 85", subject=ANNA)
    AA.record(ADMIN, "session.list", "x")                                # not a catalogue action
    JU.record({"run_id": "m1", "ts_start": now - 90, "ts_end": now - 60, "user": ANNA, "client": "web", "kind": "coupled",
               "machine": "CIANO28 85/L12", "wall_s": 30, "cpu_s": 20, "status": "failed"})
    DR.shared_store_path().write_text(json.dumps({"version": 1, "results": {"CIANO28 85": {"L12": {"cruise": {
        "em_run": {"computed_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "numbers": {"T_avg_Nm": 12.3, "efficiency_pct": 95.0}}}}}}}))
    revs = o["otmp"] / "motor_revisions"
    revs.mkdir()
    (revs / "revisions.json").write_text(json.dumps({"lineages": {"l1": {"id": "l1", "title": "x", "revisions": [
        {"rev": "B", "die": "CIANO28 85", "config": "L12", "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + "+00:00",
         "created_by": ADMIN, "reason": "thicker liner", "agreed_with_client": True}]}}}))
    blocks = {b.title: b for b in SRC._fmt_motors(now - 600, now + 600)}
    assert set(blocks) == {"Catalogue and access", "Product revisions", "Runs finished", "New results (shared ledger)"}
    assert [ln.text for ln in blocks["Catalogue and access"].lines if ln.text.startswith("die.")]
    assert "session.list" not in " ".join(ln.text for ln in blocks["Catalogue and access"].lines)
    assert "Rev B of CIANO28 85 / L12: thicker liner (agreed with client)" in blocks["Product revisions"].lines[0].text
    assert any("coupled run FAILED" in ln.text for ln in blocks["Runs finished"].lines)
    assert "T 12.3 N·m" in blocks["New results (shared ledger)"].lines[0].text


def test_the_full_report_from_the_real_stores_renders(o):
    rep = R.build_report(WORLD_DAY, now=T0, make_drafts=False)
    assert [s.id for s in rep.sections] == list(B.SECTION_ORDER) and not rep.errors
    assert rep.counts["projects"] >= 4 and rep.counts["productions"] >= 3
    assert "Erium X1" in rep.text and "https://aerostator.com/projects/erium-x1" in rep.text


# -- routes ---------------------------------------------------------------------------------------------------------------------

def test_routes_are_admin_only_and_send_to_the_owner_only(o):
    url = "/api/admin/owner-report/preview"
    assert client.get(url).status_code in (401, 403)
    assert client.get(url, headers=_login(ANNA)).status_code == 403
    assert client.post("/api/admin/owner-report/send", json={}, headers=_login(ANNA)).status_code == 403
    admin = _login(ADMIN)
    r = client.get(url + "?date=2026-10-28", headers=admin)
    assert r.status_code == 200
    j = r.json()
    assert j["date"] == "2026-10-28" and j["to"] == [ADMIN] and "text" in j and "<html" in j["html"]
    assert [k for k in j["counts"]] == list(B.SECTION_ORDER)
    assert o["delivered"] == [] and o["DS"].list_drafts() == []          # a preview sends nothing and makes no drafts
    assert client.get(url + "?date=2026-13-45", headers=admin).status_code == 422
    assert client.get(url + "?date=yesterday", headers=admin).status_code == 422
    r = client.post("/api/admin/owner-report/send", json={"date": "2026-10-28", "to": ANNA, "recipients": [BOB]}, headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "sent" and r.json()["to"] == [ADMIN]
    assert len(o["delivered"]) == 1 and o["delivered"][0]["To"] == ADMIN                # the body cannot name another recipient


# -- structure: one sending module; the digest package untouched ---------------------------------------------------------------------

def test_only_send_py_reaches_smtp_and_the_digest_package_has_no_sending_path_added():
    pkg = SRC_DIR / "owner_report"
    reach = {"smtplib", "deliver", "send_message", "sendmail", "SMTP", "SMTP_SSL"}
    users = []
    for p in sorted(pkg.glob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} \
            | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        if names & reach:
            users.append(p.name)
    assert users == ["send.py"]
    for p in (SRC_DIR / "digest").glob("*.py"):                       # the customer digest never imports the owner report
        assert "owner_report" not in p.read_text(encoding="utf-8"), p.name


# -- the "N digests ready to check" notice (owner 2026-10-27) -----------------------------------------------------------------------

def approve_mode(o):
    """Both groups of the digest world on manual approval, so the run leaves their drafts waiting."""
    for scope in (PSCOPE, XSCOPE):
        o["DS"].set_settings(scope, {"mode": "approve"}, by=ADMIN)


def _notices(got):
    return [m for m in got if m["X-AeroStator-Internal"] == "owner-notice"]


def test_new_drafts_make_one_short_notice_to_the_owner_with_a_deep_link_each(o):
    approve_mode(o)
    got = []
    now = B.window_for(WORLD_DAY)[1]
    res = R.run_daily(now=now + 300, deliver=got.append, make_drafts=True)
    assert res["status"] == "sent" and res["notice"]["status"] == "sent"
    drafts = o["DS"].list_drafts()
    notices = _notices(got)
    assert len(notices) == 1 and notices[0]["To"] == ADMIN and not notices[0]["Cc"] and not notices[0]["Bcc"]
    assert notices[0]["Subject"] == f"{len(drafts)} digests ready to check"
    text = notices[0].get_body(preferencelist=("plain",)).get_content()
    html_body = notices[0].get_body(preferencelist=("html",)).get_content()
    assert {p.get_content_type() for p in notices[0].walk()} == {"multipart/alternative", "text/plain", "text/html"}
    for x in drafts:                                       # one line per draft: title, items, recipients - and the link to open it
        n_items = len(x["items"]) + x["more"]
        assert f"{x['kind'].capitalize()}: {x['title']} - {n_items} item" in text and f"{len(x['recipients'])} recipient" in text
        assert f"https://aerostator.com/?tab=admin&section=digests&draft={x['id']}" in text
        assert f"section=digests&draft={x['id']}" in html_body.replace("&amp;", "&")
    assert "https://aerostator.com/?tab=admin&section=digests\n" in text and "Nothing has been sent" in text
    for x in drafts:                                       # the notice carries no digest text and no customer address
        assert x["items"][0]["en"] not in text and all(r["email"] not in text for r in x["recipients"] if r["email"] != ADMIN)
    # the same drafts on a forced second run are not new: no second notice
    again = R.run_daily(now=now + 600, deliver=got.append, make_drafts=True, force=True)
    assert again["status"] == "sent" and again["notice"]["status"] == "none" and len(_notices(got)) == 1


def test_no_new_draft_means_no_notice(o):
    got = []
    res = R.run_daily(now=B.window_for("2026-12-30")[1] + 60, deliver=got.append, make_drafts=True)
    assert res["status"] == "sent" and res["notice"]["status"] == "none"
    assert _notices(got) == [] and len(got) == 1
    assert got[0]["Subject"].endswith("2026-12-30") and "to approve" not in got[0]["Subject"]


def test_a_failed_notice_is_retried_once_and_never_duplicated(o):
    approve_mode(o)
    got = []
    down = [True]

    def flaky(msg):
        if msg["X-AeroStator-Internal"] == "owner-notice" and down[0]:
            raise OSError("smtp down")
        got.append(msg)
    now = B.window_for(WORLD_DAY)[1]
    r1 = R.run_daily(now=now + 300, deliver=flaky, make_drafts=True)
    assert r1["status"] == "sent" and r1["notice"]["status"] == "failed" and _notices(got) == []      # the report went out, the notice did not
    assert S.get_state()["notice_pending"]                                                           # remembered for the next run
    down[0] = False
    r2 = R.run_daily(now=now + 900, deliver=flaky, make_drafts=True, force=True)
    assert r2["notice"]["status"] == "sent" and len(_notices(got)) == 1
    assert S.get_state()["notice_pending"] == [] and len(S.get_state()["notified"]) == len(o["DS"].list_drafts())
    r3 = R.run_daily(now=now + 1200, deliver=flaky, make_drafts=True, force=True)
    assert r3["notice"]["status"] == "none" and len(_notices(got)) == 1


def test_a_draft_the_owner_already_handled_is_not_announced(o):
    approve_mode(o)
    now = B.window_for(WORLD_DAY)[1]
    rows, err = R.make_digest_drafts(now)
    assert not err and R.new_draft_ids(rows)
    for did in R.new_draft_ids(rows):
        o["DS"].save_draft(dict(o["DS"].load_draft(did), status="discarded"))
    R.queue_notice(R.new_draft_ids(rows))
    got = []
    assert R.send_pending_notice(deliver=got.append)["status"] == "none" and got == []


def test_the_notice_builder_and_its_lock():
    assert B.build_notice([]) is None
    n = B.build_notice([{"id": "d-1", "kind": "project", "title": "Example X1", "items": 1, "recipients": 1}], "https://app.example.test")
    assert n.subject == "1 digest ready to check" and "Project: Example X1 - 1 item, 1 recipient" in n.text
    assert "https://app.example.test/?tab=admin&section=digests&draft=d-1" in n.text
    assert "to" not in inspect.signature(S.send_notice).parameters and "recipients" not in inspect.signature(S.send_notice).parameters


def sent_row(**over):
    row = {"id": "d-aaaaaaaaaaaaaaaa", "scope": "project:demo-x1", "kind": "project", "title": "Demo X1", "status": "sent", "state": "sent_auto",
           "items": 3, "sent": 2, "failed": 0, "skipped": [{"email": "gone@example.com", "reason": "unsubscribed_all"}],
           "recipients": [{"email": "a@example.com", "role": "owner", "lang": "en"}, {"email": "b@example.com", "role": "customer", "lang": "en"}],
           "period_start": "2026-10-27T04:00:00Z", "period_end": "2026-10-28T04:00:00Z", "sent_at": "2026-10-28T04:00:09Z",
           "path": B.admin_path("digests", draft="d-aaaaaaaaaaaaaaaa")}
    row.update(over)
    return row


def test_the_report_lists_the_digests_sent_automatically_and_those_still_waiting():
    waiting = fixture_sources().digests(0, 0)
    bad = dict(waiting[0], id="d-bbbbbbbbbbbbbbbb", title="Demo Y2", problem="smtp down",
               path=B.admin_path("digests", draft="d-bbbbbbbbbbbbbbbb"))
    rep = B.build(DATE, empty_sources(digests=lambda s, e: [sent_row(), bad] + waiting), base_url="https://app.example.test")
    t = rep.text
    assert "Sent automatically (1)" in t and "Project: Demo X1 - sent automatically to 2 recipients, 3 items" in t
    assert "https://app.example.test/?tab=admin&section=digests&draft=d-aaaaaaaaaaaaaaaa" in t       # the history entry
    assert "Skipped at send time: gone@example.com (unsubscribed from all e-mail)" in t
    assert "Waiting (2)" in t and "AUTOMATIC SEND FAILED (smtp down)" in t and "NEW" in t
    assert "Sent automatically (1)" in rep.html and "History entry" in rep.html and "AUTOMATIC SEND FAILED (smtp down)" in rep.html
    assert rep.subject == f"AeroStator owner report {DATE} · 1 digest sent automatically · 2 digests to approve · 1 auto-send problem"
    assert rep.counts["digests"] == 3
    failed = B.build(DATE, empty_sources(digests=lambda s, e: [sent_row(sent=1, failed=1)]), base_url="https://app.example.test")
    assert "FAILED for 1" in failed.text and failed.subject.endswith("· 1 digest sent automatically · 1 auto-send problem")


def test_the_report_subject_counts_the_digests_to_approve():
    one = B.build(DATE, empty_sources(digests=lambda s, e: fixture_sources().digests(s, e)), base_url="https://app.example.test")
    assert one.subject == f"AeroStator owner report {DATE} · 1 digest to approve"
    none = B.build(DATE, empty_sources(), base_url="https://app.example.test")
    assert none.subject == f"AeroStator owner report {DATE}"
    two = B.build(DATE, empty_sources(digests=lambda s, e: fixture_sources().digests(s, e) * 2), base_url="https://app.example.test")
    assert two.subject.endswith("· 2 digests to approve")


# -- automatic sending (owner decision 2026-10-28) --------------------------------------------------------------------------------

def test_the_daily_run_sends_the_auto_groups_and_the_report_lists_them_without_a_ready_notice(o):
    o["mp"].setattr(o["E"], "smtp_configured", lambda: True)
    got = []
    now = B.window_for(WORLD_DAY)[1]
    res = R.run_daily(now=now + 300, deliver=got.append, make_drafts=True, sources=SRC.default_sources())
    assert res["status"] == "sent" and res["notice"]["status"] == "none"
    assert len(got) == 1 and got[0]["To"] == ADMIN                       # only the owner report went to the owner; no "ready to check"
    assert sorted(m["To"] for m in o["delivered"]) == sorted([ANNA, BOB, ANNA, LI])                  # the groups got their digests
    drafts = o["DS"].list_drafts()
    assert {x["status"] for x in drafts} == {"sent"} and {x["sent"]["by"] for x in drafts} == {"auto (daily 06:00)"}
    text = got[0].get_body(preferencelist=("plain",)).get_content()
    assert "Sent automatically (2)" in text and "Waiting (" not in text
    assert "Project: Erium X1 - sent automatically to 2 recipients" in text and "Production: CIANO14 40 build - sent automatically to 2 recipients" in text
    for x in drafts:
        assert f"section=digests&draft={x['id']}" in text
    assert got[0]["Subject"] == f"AeroStator owner report {res['date']} · 2 digests sent automatically"
    assert res["report"].counts["digests"] == 2
    # a forced second run: nothing new to send, the sent digests belong to this report only once
    again = R.run_daily(now=now + 600, deliver=got.append, make_drafts=True, force=True, sources=SRC.default_sources())
    assert again["status"] == "sent" and len(o["delivered"]) == 4


def test_a_group_on_manual_approval_stays_in_the_waiting_part_of_the_report(o):
    o["mp"].setattr(o["E"], "smtp_configured", lambda: True)
    o["DS"].set_settings(PSCOPE, {"mode": "approve"}, by=ADMIN)
    got = []
    res = R.run_daily(now=B.window_for(WORLD_DAY)[1] + 300, deliver=got.append, make_drafts=True, sources=SRC.default_sources())
    text = got[0].get_body(preferencelist=("plain",)).get_content()
    assert "Sent automatically (1)" in text and "Waiting (1)" in text and "Production: CIANO14 40 build - sent automatically" in text
    assert got[0]["Subject"] == f"AeroStator owner report {res['date']} · 1 digest sent automatically · 1 digest to approve"
    assert res["notice"]["status"] == "sent" and res["notice"]["count"] == 1                           # only the waiting one is announced
    assert sorted(m["To"] for m in o["delivered"]) == sorted([ANNA, LI])


def test_a_failed_automatic_send_is_reported_with_its_reason(o):
    o["mp"].setattr(o["E"], "smtp_configured", lambda: False)
    got = []
    res = R.run_daily(now=B.window_for(WORLD_DAY)[1] + 300, deliver=got.append, make_drafts=True, sources=SRC.default_sources())
    assert o["delivered"] == [] and {x["status"] for x in o["DS"].list_drafts()} == {"pending"}
    assert res["notice"]["status"] == "none"                             # auto groups: the report names the problem, no "ready to check"
    text = got[0].get_body(preferencelist=("plain",)).get_content()
    assert "Sent automatically" not in text and "Waiting (2)" in text and text.count("AUTOMATIC SEND FAILED (E-mail (SMTP) is not configured") == 2
    assert got[0]["Subject"].endswith("· 2 digests to approve · 2 auto-send problems")


def test_a_run_with_send_digests_off_makes_drafts_but_sends_none_and_the_admin_route_uses_it(o, monkeypatch):
    o["mp"].setattr(o["E"], "smtp_configured", lambda: True)
    got = []
    res = R.run_daily(now=B.window_for(WORLD_DAY)[1] + 300, deliver=got.append, make_drafts=True, send_digests=False,
                      sources=SRC.default_sources())
    assert res["status"] == "sent" and o["delivered"] == [] and {x["status"] for x in o["DS"].list_drafts()} == {"pending"}
    assert res["notice"]["status"] == "none"                             # the auto groups' drafts go out at the next scheduled run
    seen = {}
    monkeypatch.setattr(R, "run_daily", lambda **kw: seen.update(kw) or {"status": "sent", "date": "2026-10-28", "to": [ADMIN]})
    r = client.post("/api/admin/owner-report/send", json={"date": "2026-10-28"}, headers=_login(ADMIN))
    assert r.status_code == 200 and seen["send_digests"] is False and seen["force"] is True
