"""Activity digests (motor_ai_sim.digest): collector, ONE text per group, approval-only sending, settings, unsubscribe.

Owner rules 2026-10-26 under test:

* nothing is sent without an explicit approval in Admin (no automatic mode, no code path that sends from the scheduler);
* ONE text per working group = what EVERY recipient may see (no costs unless shared with the factory, no internal notes);
  the group's Chinese-speaking members make it bilingual (English first);
* text + HTML only, no images, no attachments; every item links to the exact app page (a path ``return_path`` accepts);
* a period with no activity makes no digest; one draft per (group, period).

Temp stores and a fake SMTP only; no real e-mail is ever sent.
"""
from __future__ import annotations

import ast
import email as emaillib
import json
import re
import smtplib
from email import policy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import return_path as RP
from motor_ai_sim.api import app
from tests.test_comm_prefs import ADMIN, ANNA, BOB, LI, _login, env  # noqa: F401

client = TestClient(app)
T0 = 1_790_000_000.0          # "now" of every run (2026-09-21 in UTC)
PID = "erium-x1"
DIE, CFG = "CIANO14 40 new", "L12"
SRC = Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim" / "digest"


def at(offset_s: float) -> str:
    from motor_ai_sim.digest import store as DS
    return DS.iso(T0 + offset_s)


@pytest.fixture()
def d(env, tmp_path, monkeypatch):                       # noqa: F811
    """The comm-prefs environment plus project/production/digest roots and a small world."""
    from motor_ai_sim.development import store as PS
    from motor_ai_sim.digest import routes as DR
    from motor_ai_sim.digest import store as DS
    from motor_ai_sim.production import models as PM
    from motor_ai_sim.production import store as PR
    U = env["U"]
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "production"))
    monkeypatch.setenv("MOTRES_DIGEST_ROOT", str(tmp_path / "digests"))
    monkeypatch.setattr(DR, "SEND_DELAY_S", 0.0)
    (tmp_path / "projects").mkdir()
    for who in (ANNA, BOB, LI):
        U.set_nda(who, True, by="admin")
    U.create_user("dan@example.com", "password-dan", name="Dan")

    PS.create_project({"id": PID, "title": "Erium X1", "customer_org": "Erium", "status": "design", "created_at": at(-99999),
                       "updated_at": at(-99999), "members": [{"email": ANNA, "role": "owner"}, {"email": BOB, "role": "customer"}],
                       "linked_motors": [{"die": "CIANO28 85", "config": "L12", "status": "current"},
                                         {"die": "CIANO28 85", "config": "T9", "status": "trial"}], "notes": "", "pending_invites": []})
    PS.create_project({"id": "other-job", "title": "Other job", "status": "design", "created_at": at(-99999), "updated_at": at(-99999),
                       "members": [{"email": ANNA, "role": "owner"}, {"email": "dan@example.com", "role": "customer"}],
                       "linked_motors": [], "notes": "", "pending_invites": []})

    def audit(action, entity, eid, details=None, when=-3600, who=ANNA):
        PS.append_jsonl(PID, "audit.jsonl", {"time": at(when), "actor_email": who, "action": action, "entity": entity,
                                             "entity_id": eid, "details": details or {}})
    audit("motor.note", "motor", "CIANO28 85/L12", {"old": "", "new": "Gap 0.8 mm, check the winding"})
    audit("motor.link", "motor", "CIANO28 85/T9")
    audit("submit", "spec_version", "2", {})
    audit("update", "project", PID, {"fields": ["notes"]})
    audit("read", "project", PID)                                   # reading is not activity
    PS.append_jsonl(PID, "thread.jsonl", {"id": "m1", "author_email": BOB, "created_at": at(-1800), "text": "When do we get the next numbers?"})

    prod = PM.normalize_production({"title": {"en": "CIANO14 40 build", "zh": "CIANO14 40 生产", "src_lang": "en",
                                              "status": {"en": "confirmed", "zh": "confirmed"}},
                                    "members": [{"email": ANNA, "role": "owner"}, {"email": LI, "role": "oem"}]}, DIE, CFG)
    PR.save_file(DIE, CFG, "production.json", prod)
    PR.save_file(DIE, CFG, "documents.json", {"documents": [
        {"id": "doc_pub1", "name": "assembly-photo.jpg", "internal": False, "uploaded_at": at(-3000)},
        {"id": "doc_int1", "name": "internal-cost-sheet.xlsx", "internal": True, "uploaded_at": at(-3000)}]})
    PR.save_file(DIE, CFG, "threads.json", {"threads": [{"id": "th_1", "messages": [
        {"id": "msg_1", "author_email": LI, "text": {"en": "Stator ready", "zh": "定子已完成", "src_lang": "zh",
                                                    "status": {"en": "machine", "zh": "confirmed"}}}]}]})

    def paudit(action, entity, eid, snap=None, when=-3600, who=ANNA):
        PR.append_audit(DIE, CFG, {"time": at(when), "who": who, "action": action, "entity": entity, "entity_id": eid,
                                   "snapshot": snap or {}})
    paudit("publish_revision", "revision", "1.1", {"revision": {"summary": {"en": "Slot liner thicker", "zh": "槽绝缘加厚",
                                                                           "src_lang": "en", "status": {"en": "confirmed", "zh": "machine"}},
                                                               "changes": [{"en": "a"}, {"en": "b"}]}})
    paudit("update", "costs", "costs", {"costs": {}})
    paudit("create", "document", "doc_pub1", {"doc": {"name": "assembly-photo.jpg"}})
    paudit("create", "document", "doc_int1", {"doc": {"name": "internal-cost-sheet.xlsx"}})
    paudit("create", "thread_message", "msg_1", {"thread_id": "th_1"}, who=LI)
    paudit("update", "bom", "bom", {"rows": []})
    paudit("download", "document", "doc_pub1", {})                  # a download is not activity

    results = {"cruise": {"em_run": {"computed_at": at(-1800), "numbers": {"T_avg_Nm": 12.34, "efficiency_pct": 95.1, "P_mech_W": 4100}},
                          "coupled": {"computed_at": at(-1700), "coil_temp_c": 128.4}}}
    env.update(PS=PS, PR=PR, PM=PM, DS=DS, DR=DR, results=lambda die, cfg: results if cfg in ("L12", "T9") else {})
    env["dtmp"] = tmp_path
    return env


def run(d, now=T0, **kw):
    from motor_ai_sim.digest import build as B
    return B.run(now, results=d["results"], **kw)


def draft_of(d, scope):
    return d["DS"].load_draft(d["DS"].get_state(scope)["draft_id"])


PSCOPE, XSCOPE = f"project:{PID}", f"production:{DIE}/{CFG}"


# -- the collector ---------------------------------------------------------------------------------------------------------------------

def test_project_collector_lists_activity_not_reads(d):
    from motor_ai_sim.digest import collect as C
    items = C.collect_project(d["PS"].get_project(PID), T0 - 7200, T0, results=d["results"])
    kinds = sorted(i.kind for i in items)
    assert kinds == ["message", "motor_added", "motor_note", "notes", "results", "results", "spec_version"]
    notes = next(i for i in items if i.kind == "notes")
    assert "Gap" not in notes.en and C.INTERNAL in notes.requires            # internal notes: only THAT they changed
    res = [i for i in items if i.kind == "results"]
    assert any("T 12.3 N·m" in i.en and "η 95.1 %" in i.en and "winding 128 °C" in i.en for i in res)
    trial = next(i for i in res if "T9" in i.en)
    assert C.INTERNAL in trial.requires                                      # a trial variant is the engineers' scratch work


def test_production_collector_marks_machine_text_and_internal(d):
    from motor_ai_sim.digest import collect as C
    items = C.collect_production(d["PR"].production_root() and json.loads((d["PR"].path_for(DIE, CFG) / "production.json").read_text()),
                                 T0 - 7200, T0)
    by = {i.kind: i for i in items if i.kind != "document"}
    assert "Slot liner thicker" in by["revision"].en and "(2 changes)" in by["revision"].en
    assert by["revision"].zh.startswith("版本 1.1") and "[机器翻译]" in by["revision"].zh          # the machine-made Chinese is marked
    assert "[machine-translated]" in by["message"].en and "定子已完成" in by["message"].zh          # the Chinese original, English is the machine side
    assert C.COSTS in by["costs"].requires
    docs = {i.key: i for i in items if i.kind == "document"}
    assert C.INTERNAL in docs["doc:doc_int1"].requires and not docs["doc:doc_pub1"].requires
    assert not any("download" in i.en.lower() for i in items)


def test_every_item_links_to_an_exact_page_the_app_accepts(d):
    from motor_ai_sim.digest import collect as C
    items = (C.collect_project(d["PS"].get_project(PID), T0 - 7200, T0, results=d["results"])
             + C.collect_production(json.loads((d["PR"].path_for(DIE, CFG) / "production.json").read_text()), T0 - 7200, T0))
    assert items
    for i in items:
        assert i.link and RP.safe_app_path(i.link) == i.link, i
    links = {i.kind: i.link for i in items}
    assert links["message"] in ("/production/CIANO14%2040%20new/L12/discussion?item=th_1", "/projects/erium-x1/messages?item=m1")
    assert links["revision"] == "/production/CIANO14%2040%20new/L12/changes?item=1.1"
    assert links["motor_note"] == "/projects/erium-x1/motors?die=CIANO28%2085&config=L12"


def test_no_activity_makes_no_digest(d):
    rows = run(d, now=T0 + 40 * 86400)                               # far later: nothing happened in the last day
    assert {r["action"] for r in rows} == {"skipped"}
    assert d["DS"].list_drafts() == []


# -- ONE text per group = lowest common access ---------------------------------------------------------------------------------------------

def test_factory_group_text_has_no_costs_no_internal_and_both_languages(d):
    run(d, scopes=[XSCOPE])
    draft = draft_of(d, XSCOPE)
    text = draft["text_body"]
    assert "Cost sheet" not in text and "internal-cost-sheet" not in text and "成本表" not in text
    assert "Revision 1.1 published" in text and "assembly-photo.jpg" in text and "Bill of materials updated" in text
    assert draft["bilingual"] and "版本 1.1 已发布" in text and text.index("Revision 1.1") < text.index("版本 1.1")   # English first
    assert sorted(r["email"] for r in draft["recipients"]) == sorted([ANNA, LI])
    assert "costs" not in draft["caps"] and "internal" not in draft["caps"]


def test_shared_costs_reach_the_factory_text(d):
    d["PR"].mutate_file(DIE, CFG, "production.json", lambda p: p.update({"share_costs_with_oem": True}))
    run(d, scopes=[XSCOPE])
    text = draft_of(d, XSCOPE)["text_body"]
    assert "Cost sheet updated" in text and "internal-cost-sheet" not in text      # costs shared, internal files still ours


def test_engineering_only_group_sees_everything_in_english(d):
    d["PR"].mutate_file(DIE, CFG, "production.json", lambda p: p.update({"members": [{"email": ANNA, "role": "owner"}]}))
    run(d, scopes=[XSCOPE])
    draft = draft_of(d, XSCOPE)
    assert not draft["bilingual"] and "版本" not in draft["text_body"]
    assert "Cost sheet updated" in draft["text_body"] and "internal-cost-sheet.xlsx" in draft["text_body"]


def test_project_with_a_customer_hides_internal_notes_and_trial_variants(d):
    run(d, scopes=[PSCOPE])
    draft = draft_of(d, PSCOPE)
    text = draft["text_body"]
    assert "Internal project notes" not in text and "T9" not in text and "Design variant added" not in text
    assert "Specification version 2 submitted" in text and "New message from Bob" in text
    assert "Design step note, CIANO28 85 / L12: Gap 0.8 mm" in text
    assert "T 12.3 N·m" in text and not draft["bilingual"]
    assert sorted(r["email"] for r in draft["recipients"]) == sorted([ANNA, BOB])


def test_project_without_customers_shows_engineers_the_internal_lines(d):
    d["PS"].mutate_project(PID, lambda p: p.update({"members": [{"email": ANNA, "role": "owner"}]}))
    run(d, scopes=[PSCOPE])
    text = draft_of(d, PSCOPE)["text_body"]
    assert "Internal project notes updated" in text and "Design variant added: CIANO28 85 / T9" in text


def test_a_customer_only_ever_sees_their_own_project(d):
    run(d, scopes=[PSCOPE, "project:other-job"])
    assert "dan@example.com" not in json.dumps(draft_of(d, PSCOPE)["recipients"])
    assert d["DS"].get_state("project:other-job") .get("draft_id") is None     # nothing happened there: no draft, nothing to leak
    assert "Other job" not in draft_of(d, PSCOPE)["text_body"]


def test_at_most_twenty_lines_and_a_more_link(d):
    for i in range(30):
        d["PS"].append_jsonl(PID, "thread.jsonl", {"id": f"x{i}", "author_email": BOB, "created_at": at(-600 - i), "text": f"note {i}"})
    run(d, scopes=[PSCOPE])
    draft = draft_of(d, PSCOPE)
    assert len(draft["items"]) == 20 and draft["more"] >= 10
    assert f"and {draft['more']} more" in draft["text_body"] and "/projects/erium-x1" in draft["text_body"]


# -- the message ----------------------------------------------------------------------------------------------------------------------------

def test_message_is_text_and_html_only_with_one_body_for_everybody(d):
    from motor_ai_sim.digest import build as B
    run(d, scopes=[XSCOPE])
    draft = draft_of(d, XSCOPE)
    msgs = {e: B.build_message(draft, e, base="https://aerostator.com", sender="noreply@aerostator.com") for e in (ANNA, LI)}
    for e, m in msgs.items():
        assert m.get_content_type() == "multipart/alternative"
        assert [p.get_content_type() for p in m.walk() if not p.is_multipart()] == ["text/plain", "text/html"]   # no attachment
        html_part = m.get_body(preferencelist=("html",)).get_content()
        assert "<img" not in html_part.lower() and "background-image" not in html_part.lower() and "url(" not in html_part.lower()
        assert m["List-Unsubscribe"].startswith("<https://aerostator.com/api/digest/unsubscribe/")
        assert m["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click" and m["To"] == e
    bodies = [m.get_body(preferencelist=("plain",)).get_content().split("\n--\n")[0] for m in msgs.values()]
    assert bodies[0] == bodies[1] == draft["text_body"].rstrip("\n") or bodies[0].rstrip("\n") == bodies[1].rstrip("\n")
    foot = [m.get_body(preferencelist=("plain",)).get_content().split("\n--\n")[1] for m in msgs.values()]
    assert foot[0] != foot[1]                                                           # only the personal links differ
    assert "https://aerostator.com/production/CIANO14%2040%20new/L12/changes?item=1.1" in bodies[0]
    assert "What changed since" in draft["subject"] or "what changed" in draft["subject"]
    assert draft["subject"].startswith("CIANO14 40 build - what changed ") and "CIANO14 40 生产" in draft["subject"]


# -- run: drafts only, never mail ------------------------------------------------------------------------------------------------------------

def test_run_makes_drafts_and_sends_nothing(d):
    def no_smtp(*a, **k):
        raise AssertionError("a run reached SMTP")
    d["mp"].setattr(smtplib, "SMTP", no_smtp)
    d["mp"].setattr(smtplib, "SMTP_SSL", no_smtp)
    d["mp"].setattr(d["E"], "send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a run mailed")))
    rows = run(d)
    assert {r["scope"]: r["action"] for r in rows if r["action"] != "skipped"} == {PSCOPE: "drafted", XSCOPE: "drafted"}
    assert d["delivered"] == [] and d["outbox"] == []
    assert {x["status"] for x in d["DS"].list_drafts()} == {"pending"}


def test_dry_run_writes_nothing(d):
    rows = run(d, write=False)
    assert any(r["action"] == "drafted" for r in rows)
    assert d["DS"].list_drafts() == [] and d["DS"].read_log() == []


def test_mode_off_makes_no_draft_and_auto_does_not_exist(d):
    DS = d["DS"]
    assert DS.MODES == ("approve", "off") and DS.get_settings(PSCOPE)["mode"] == "approve"          # approve is the default
    with pytest.raises(ValueError):
        DS.set_settings(PSCOPE, {"mode": "auto"}, by=ADMIN)
    with pytest.raises(ValueError):
        DS.validate_settings_update({"mode": "automatic"})
    (d["dtmp"] / "digests").mkdir(exist_ok=True)
    (d["dtmp"] / "digests" / "settings.json").write_text(json.dumps({PSCOPE: {"mode": "auto"}}), encoding="utf-8")
    assert DS.get_settings(PSCOPE)["mode"] == "approve"                                             # an old value reads as approve
    DS.set_settings(PSCOPE, {"mode": "off"}, by=ADMIN)
    rows = {r["scope"]: r for r in run(d)}
    assert rows[PSCOPE]["reason"] == "mode_off" and DS.get_state(PSCOPE).get("draft_id") is None


def test_weekly_waits_for_its_week(d):
    d["DS"].set_settings(PSCOPE, {"frequency": "weekly"}, by=ADMIN)
    d["DS"].set_state(PSCOPE, cursor=at(-2 * 86400))
    assert {r["scope"]: r for r in run(d)}[PSCOPE]["reason"] == "not_due"
    d["DS"].set_state(PSCOPE, cursor=at(-7 * 86400))
    assert {r["scope"]: r for r in run(d)}[PSCOPE]["action"] == "drafted"


def test_one_draft_per_period_and_send_is_idempotent(d):
    from motor_ai_sim.digest import approval as AP
    run(d, scopes=[PSCOPE])
    first = d["DS"].get_state(PSCOPE)["draft_id"]
    rows = run(d, now=T0 + 600, scopes=[PSCOPE])                      # the timer fires again: the SAME draft is refreshed
    assert rows[0]["action"] == "refreshed" and d["DS"].get_state(PSCOPE)["draft_id"] == first
    assert len([x for x in d["DS"].list_drafts() if x["scope"] == PSCOPE]) == 1
    res = AP.send_draft(first, approved_by=ADMIN, deliver=lambda m: d["delivered"].append(m), delay_s=0, sleep=lambda s: None)
    assert res["sent"] == 2 and sorted(m["To"] for m in d["delivered"]) == sorted([ANNA, BOB])
    with pytest.raises(AP.ApprovalError) as e:                         # a second send of the same digest
        AP.send_draft(first, approved_by=ADMIN, deliver=lambda m: d["delivered"].append(m), delay_s=0, sleep=lambda s: None)
    assert e.value.code == "not_pending" and len(d["delivered"]) == 2
    assert run(d, now=T0 + 3600, scopes=[PSCOPE])[0]["action"] == "skipped"       # nothing new since the sent period
    assert d["DS"].get_state(PSCOPE)["cursor"] == d["DS"].load_draft(first)["period"]["end"]
    # the send log keeps a crashed/duplicate attempt from mailing the same person twice
    from motor_ai_sim import mailing
    assert {r["user"] for r in mailing.read_log(d["DS"].sendlog_file(first)) if r["status"] == "sent"} == {ANNA, BOB}


def test_discard_moves_on_and_logs_who(d):
    from motor_ai_sim.digest import approval as AP
    run(d, scopes=[PSCOPE])
    did = d["DS"].get_state(PSCOPE)["draft_id"]
    AP.discard_draft(did, by=ADMIN)
    assert d["DS"].load_draft(did)["status"] == "discarded" and d["delivered"] == []
    assert any(r["event"] == "discarded" and r["by"] == ADMIN for r in d["DS"].read_log(PSCOPE))
    assert run(d, now=T0 + 3600, scopes=[PSCOPE])[0]["action"] == "skipped"


# -- no path sends without an explicit approval ------------------------------------------------------------------------------------------------------

def test_send_needs_a_named_approver(d):
    from motor_ai_sim.digest import approval as AP
    run(d, scopes=[PSCOPE])
    did = d["DS"].get_state(PSCOPE)["draft_id"]
    for who in ("", "   ", None, "not-an-address"):
        with pytest.raises(AP.ApprovalError) as e:
            AP.send_draft(did, approved_by=who, deliver=lambda m: d["delivered"].append(m), delay_s=0)
        assert e.value.code == "approval_required"
    assert d["delivered"] == [] and d["DS"].load_draft(did)["status"] == "pending"


def _names(py: Path) -> set:
    """Every identifier, attribute and imported name of a module (docstrings and comments do not count)."""
    tree = ast.parse(py.read_text(encoding="utf-8"))
    out = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            out |= {a.name for a in n.names} | {(n.module or "").split(".")[-1]}
        elif isinstance(n, ast.Import):
            out |= {a.name.split(".")[-1] for a in n.names}
    return out


def test_only_the_approval_module_can_reach_smtp_and_only_one_route_calls_it():
    """Static guard: the package mails through ONE function; the scheduler path imports no delivery code."""
    delivery = {"deliver", "smtplib", "send_message", "sendmail", "SMTP", "SMTP_SSL", "send_draft", "approval", "send"}
    reach = {py.name: _names(py) & delivery for py in sorted(SRC.glob("*.py"))}
    assert {k for k, v in reach.items() if v} == {"approval.py", "routes.py"}, reach
    for scheduler in ("build.py", "collect.py", "store.py", "__main__.py", "__init__.py"):
        assert not reach[scheduler], (scheduler, reach[scheduler])
    assert "mailing" not in _names(SRC / "__main__.py") and "mailing" not in _names(SRC / "store.py")
    tree = ast.parse((SRC / "routes.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "send_draft"]
    assert len(calls) == 1                                                  # one call site: the admin "Send" route
    fn = next(f for f in ast.walk(tree) if isinstance(f, ast.AsyncFunctionDef) and f.name == "admin_send")
    assert any(c in ast.walk(fn) for c in calls)
    routes_src = (SRC / "routes.py").read_text(encoding="utf-8")
    admin_send_src = routes_src.split("async def admin_send")[1].split("@admin_router")[0]
    assert 'body.get("approve") is not True' in routes_src and "Depends(auth.require_admin)" in routes_src.split("def admin_send")[0][-200:] + admin_send_src[:200]
    modes_line = (SRC / "store.py").read_text(encoding="utf-8").split("MODES = ")[1].splitlines()[0]
    assert "auto" not in modes_line


def test_the_cli_has_no_send_and_never_mails(d, capsys):
    from motor_ai_sim.digest.__main__ import main
    assert main(["run", "--send"]) == 2 and "no --send" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["run", "--send-now"])
    assert main(["run", "--dry-run", "--json"]) == 0
    assert d["delivered"] == [] and d["DS"].list_drafts() == []


def test_send_route_needs_admin_and_the_explicit_approve_flag(d):
    run(d, scopes=[PSCOPE])
    did = d["DS"].get_state(PSCOPE)["draft_id"]
    url = f"/api/admin/digests/{did}/send"
    assert client.post(url, json={"approve": True}).status_code in (401, 403)
    owner = _login(ANNA)
    assert client.post(url, json={"approve": True}, headers=owner).status_code == 403
    admin = _login(ADMIN)
    for body in ({}, {"approve": "yes"}, {"approve": 1}, {"send": True}, None):
        r = client.post(url, json=body, headers=admin) if body is not None else client.post(url, headers=admin)
        assert r.status_code == 422, body
    assert d["delivered"] == [] and d["DS"].load_draft(did)["status"] == "pending"
    r = client.post(url, json={"approve": True}, headers=admin)
    assert r.status_code == 200 and r.json()["sent"] == 2
    assert d["DS"].load_draft(did)["sent"]["by"] == ADMIN
    assert client.post(url, json={"approve": True}, headers=admin).status_code == 409
    assert len(d["delivered"]) == 2


def test_send_rechecks_recipients_at_send_time(d):
    from motor_ai_sim.digest import approval as AP
    run(d, scopes=[XSCOPE])
    did = d["DS"].get_state(XSCOPE)["draft_id"]
    d["PR"].mutate_file(DIE, CFG, "production.json",
                        lambda p: p.update({"members": [m for m in p["members"] if m["email"] != LI] + [{"email": BOB, "role": "oem"}]}))
    res = AP.send_draft(did, approved_by=ADMIN, deliver=lambda m: d["delivered"].append(m), delay_s=0, sleep=lambda s: None)
    assert [m["To"] for m in d["delivered"]] == [ANNA]                  # Li left: not mailed; Bob joined later: not on the approved list
    assert {"email": LI, "reason": "no_longer_a_recipient"} in res["skipped"]


def test_factory_member_without_nda_gets_nothing(d):
    d["U"].set_nda(LI, False)
    run(d, scopes=[XSCOPE])
    draft = draft_of(d, XSCOPE)
    assert [r["email"] for r in draft["recipients"]] == [ANNA]
    assert {"email": LI, "reason": "nda_missing"} in draft["skipped"] and not draft["bilingual"]


# -- recipients: choice, opt-out, unsubscribe ------------------------------------------------------------------------------------------------------------

def test_chosen_recipients_and_member_opt_out(d):
    d["DS"].set_settings(PSCOPE, {"recipients": "chosen", "chosen": [ANNA]}, by=ADMIN)
    run(d, scopes=[PSCOPE])
    assert [r["email"] for r in draft_of(d, PSCOPE)["recipients"]] == [ANNA]
    d["DS"].set_settings(PSCOPE, {"recipients": "all"}, by=ADMIN)
    d["DS"].set_optout(PSCOPE, BOB, True, by=BOB)
    run(d, now=T0 + 60, scopes=[PSCOPE])
    draft = draft_of(d, PSCOPE)
    assert [r["email"] for r in draft["recipients"]] == [ANNA] and {"email": BOB, "reason": "left_this_digest"} in draft["skipped"]


def test_unsubscribe_link_get_is_harmless_post_leaves_this_digest_only(d):
    from motor_ai_sim.digest import build as B
    run(d, scopes=[PSCOPE])
    draft = draft_of(d, PSCOPE)
    url = B.unsub_url(BOB, PSCOPE, "http://testserver")
    path = url.replace("http://testserver", "")
    page = client.get(path)
    assert page.status_code == 200 and "<form" in page.text and d["DS"].get_settings(PSCOPE)["optouts"] == []     # a scanner's GET changes nothing
    done = client.post(path)
    assert done.status_code == 200 and d["DS"].get_settings(PSCOPE)["optouts"] == [BOB]
    assert d["CP"].get(BOB)["unsubscribed_all"] is False                                                        # only this digest
    assert client.post(path).status_code == 200                                                                  # idempotent
    bad = path[:-3] + "xyz"
    assert client.post(bad).status_code == 400 and client.get(bad).status_code == 400
    other = path.replace(DS_tok(PSCOPE), DS_tok(XSCOPE))                                                         # a token is bound to its group
    assert client.post(other).status_code == 400
    from motor_ai_sim.digest import approval as AP
    res = AP.send_draft(draft["id"], approved_by=ADMIN, deliver=lambda m: d["delivered"].append(m), delay_s=0, sleep=lambda s: None)
    assert [m["To"] for m in d["delivered"]] == [ANNA] and {"email": BOB, "reason": "left_this_digest"} in res["skipped"]


def DS_tok(scope):
    from motor_ai_sim.digest import store as DS
    return DS.scope_token(scope)


def test_unsubscribed_from_all_mail_is_never_mailed(d):
    d["CP"].unsubscribe_all(BOB, source="unsubscribe")
    run(d, scopes=[PSCOPE])
    assert [r["email"] for r in draft_of(d, PSCOPE)["recipients"]] == [ANNA]


# -- settings and Admin routes -----------------------------------------------------------------------------------------------------------------------------

def test_project_settings_owner_and_admin_only_and_logged(d):
    url = f"/api/digest/settings/project/{PID}"
    assert client.get(url, headers=_login(BOB)).status_code == 404                  # a customer cannot see or change them
    assert client.put(url, json={"mode": "off"}, headers=_login(BOB)).status_code == 404
    owner = _login(ANNA)
    r = client.get(url, headers=owner)
    assert r.status_code == 200 and r.json()["mode"] == "approve" and r.json()["frequency"] == "daily"
    assert {m["email"] for m in r.json()["members"]} == {ANNA, BOB}
    assert client.put(url, json={"mode": "auto"}, headers=owner).status_code == 422
    assert client.put(url, json={"mode": "automatic", "frequency": "hourly"}, headers=owner).status_code == 422
    assert client.put(url, json={"frequency": "hourly"}, headers=owner).status_code == 422
    ok = client.put(url, json={"mode": "off", "frequency": "weekly", "recipients": "chosen", "chosen": [ANNA]}, headers=owner)
    assert ok.status_code == 200 and ok.json()["mode"] == "off" and ok.json()["updated_by"] == ANNA
    log = [x for x in d["DS"].read_log(PSCOPE) if x["event"] == "settings"]
    assert log[-1]["by"] == ANNA and log[-1]["changed"]["mode"] == ["approve", "off"] and log[-1]["ts"]
    assert client.get(f"/api/digest/settings/project/other-job", headers=owner).status_code == 200      # owner of that one too
    assert client.get(f"/api/digest/settings/project/nope", headers=owner).status_code == 404


def test_production_settings_are_admin_only(d):
    url = f"/api/digest/settings/production/{DIE}/{CFG}"
    assert client.get(url, headers=_login(ANNA)).status_code == 404
    assert client.get(url, headers=_login(LI)).status_code == 404
    admin = _login(ADMIN)
    r = client.get(url, headers=admin)
    assert r.status_code == 200 and {m["email"] for m in r.json()["will_receive"]} == {ANNA, LI}
    assert client.put(url, json={"mode": "off"}, headers=admin).json()["mode"] == "off"


def test_member_can_leave_and_rejoin_a_digest(d):
    bob = _login(BOB)
    mine = client.get("/api/digest/mine", headers=bob).json()["digests"]
    assert [m["scope"] for m in mine] == [PSCOPE] and mine[0]["opted_out"] is False
    assert client.put("/api/digest/mine", json={"scope": PSCOPE, "opted_out": True}, headers=bob).status_code == 200
    assert d["DS"].get_settings(PSCOPE)["optouts"] == [BOB]
    assert client.put("/api/digest/mine", json={"scope": XSCOPE, "opted_out": True}, headers=bob).status_code == 422   # not their group


def test_admin_list_preview_discard(d):
    import types
    from motor_ai_sim.digest import build as B
    d["mp"].setattr(B, "time", types.SimpleNamespace(time=lambda: T0))            # "now" of the fixture world
    admin = _login(ADMIN)
    r = client.post("/api/admin/digests/run", json={}, headers=admin)
    assert r.status_code == 200 and d["delivered"] == []
    lst = client.get("/api/admin/digests", headers=admin).json()
    assert len(lst["pending"]) == 2 and lst["history"] == []
    xd = next(x for x in lst["pending"] if x["scope"] == XSCOPE)
    det = client.get(f"/api/admin/digests/{xd['id']}", headers=admin).json()
    assert {r["email"] for r in det["recipients"]} == {ANNA, LI} and det["items"] and "Revision 1.1" in det["text_body"]
    pv = client.get(f"/api/admin/digests/{xd['id']}/preview?recipient={LI}", headers=admin).json()
    assert pv["to"] == LI and "Stop updates for this production" in pv["text"] and "不再接收此生产的更新" in pv["text"]
    assert "<img" not in pv["html"].lower()
    assert client.get(f"/api/admin/digests/{xd['id']}/preview?recipient=eve@example.com", headers=admin).status_code == 404
    assert client.get("/api/admin/digests", headers=_login(ANNA)).status_code == 403
    assert client.post(f"/api/admin/digests/{xd['id']}/discard", headers=admin).json()["status"] == "discarded"
    assert client.post(f"/api/admin/digests/{xd['id']}/discard", headers=admin).status_code == 409
    after = client.get("/api/admin/digests", headers=admin).json()
    assert len(after["pending"]) == 1 and after["history"][0]["status"] == "discarded"
    assert d["delivered"] == []


# -- sample digests (written only when DIGEST_SAMPLES_DIR is set; see docs/DIGEST.md) --------------------------------------------------------------------------

@pytest.mark.skipif(not __import__("os").environ.get("DIGEST_SAMPLES_DIR"), reason="set DIGEST_SAMPLES_DIR to write the two sample digests")
def test_render_sample_digests(d):
    """The Erium development project and the G2 production from fixtures, rendered exactly as a recipient gets them."""
    import os
    out = Path(os.environ["DIGEST_SAMPLES_DIR"])
    out.mkdir(parents=True, exist_ok=True)
    PM, PR = d["PM"], d["PR"]
    gd, gc = "CILN28", "G2-L40"
    prod = PM.normalize_production({"title": {"en": "G2 generator L40", "zh": "G2 发电机 L40", "src_lang": "en",
                                              "status": {"en": "confirmed", "zh": "confirmed"}},
                                    "members": [{"email": ANNA, "role": "owner"}, {"email": LI, "role": "oem"}]}, gd, gc)
    PR.save_file(gd, gc, "production.json", prod)
    PR.save_file(gd, gc, "documents.json", {"documents": [{"id": "doc_g2a", "name": "stator-winding-photo-03.jpg", "internal": False},
                                                          {"id": "doc_g2b", "name": "supplier-quote-internal.pdf", "internal": True}]})
    PR.save_file(gd, gc, "threads.json", {"threads": [{"id": "th_g2", "messages": [
        {"id": "msg_g2", "author_email": LI, "text": {"en": "Magnets arrive on Thursday", "zh": "磁钢周四到货", "src_lang": "zh",
                                                       "status": {"en": "machine", "zh": "confirmed"}}}]}]})
    PR.save_file(gd, gc, "proposals.json", {"proposals": [{"id": "pr_g2", "text": {"en": "Use 8x5 mm magnets", "zh": "使用 8x5 毫米磁钢", "src_lang": "en",
                                                                                  "status": {"en": "confirmed", "zh": "machine"}}}]})

    def paudit(action, entity, eid, snap=None, when=-3600, who=ANNA):
        PR.append_audit(gd, gc, {"time": at(when), "who": who, "action": action, "entity": entity, "entity_id": eid, "snapshot": snap or {}})
    paudit("publish_revision", "revision", "2.3", {"revision": {"summary": {"en": "Rotor sleeve 0.3 mm thicker", "zh": "转子护套加厚 0.3 毫米", "src_lang": "en",
                                                                         "status": {"en": "confirmed", "zh": "machine"}}, "changes": [{"en": "x"}, {"en": "y"}, {"en": "z"}]}}, when=-5400)
    paudit("create", "document", "doc_g2a", {"doc": {"name": "stator-winding-photo-03.jpg"}}, who=LI)
    paudit("create", "document", "doc_g2b", {"doc": {"name": "supplier-quote-internal.pdf"}})
    paudit("create", "thread_message", "msg_g2", {"thread_id": "th_g2"}, who=LI)
    paudit("create", "proposal", "pr_g2", {"proposal": {"text": {"en": "Use 8x5 mm magnets", "zh": "使用 8x5 毫米磁钢", "src_lang": "en",
                                                               "status": {"en": "confirmed", "zh": "machine"}}}}, who=LI)
    paudit("accepted", "proposal", "pr_g2")
    paudit("update", "bom", "bom", {"rows": []})
    paudit("update", "costs", "costs", {"costs": {}})
    run(d, scopes=[PSCOPE, f"production:{gd}/{gc}"])
    for scope, name in ((PSCOPE, "erium_project_digest.html"), (f"production:{gd}/{gc}", "g2_production_digest.html")):
        draft = d["DS"].load_draft(d["DS"].get_state(scope)["draft_id"])
        who = draft["recipients"][-1]["email"]
        r = __import__("motor_ai_sim.digest.build", fromlist=["x"]).render_for(draft, who, base="https://aerostator.com")
        def clean(s):             # the signed links of a sample carry the TEST secret: show their shape, not a token
            s = re.sub(r"(/api/digest/unsubscribe/[A-Za-z0-9_-]+/)[A-Za-z0-9_.-]+", r"\1<signed-token>", s)
            return re.sub(r"([?&]t=)[A-Za-z0-9_.-]+", r"\1<signed-token>", s)
        subject_to = "Subject: " + r["subject"] + "\nTo: " + who + "\n\n" + r["text"]
        (out / name).write_text(clean(r["html"]), encoding="utf-8")
        (out / name.replace(".html", ".txt")).write_text(clean(subject_to), encoding="utf-8")
        assert "<img" not in r["html"].lower()
