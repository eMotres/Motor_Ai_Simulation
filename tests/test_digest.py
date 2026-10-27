"""Activity digests (motor_ai_sim.digest): collector, ONE text per group, approved and automatic sending, settings, unsubscribe.

Owner rules under test (2026-10-26, changed by the owner decision 2026-10-28):

* three modes: ``auto`` (default: the DAILY scheduled run sends each new draft through the admin send path), ``approve`` (the draft
  waits for an admin's explicit approval in Admin) and ``off``; build.run and the admin "make drafts now" route never send;
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
    for who in (ANNA, BOB):               # linking a motor to a project grants its members the configuration (development/motors.py)
        U.set_motor_grants(who, all_motors=False, dies=[], configs=["CIANO28 85/L12", "CIANO28 85/T9"])
    from motor_ai_sim.routes import family as _fam           # the catalog die on disk (a temp copy: the live config is never touched)
    die_dir = tmp_path / "catalog" / "CIANO28 85"
    die_dir.mkdir(parents=True)
    for cfg_name in ("L12", "T9"):
        (die_dir / f"{cfg_name}.yaml").write_text("name: x"+chr(10), encoding="utf-8")
    monkeypatch.setattr(_fam, "_die_dir", lambda die: tmp_path / "catalog" / str(die))

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


def go(d, draft_id, **kw):
    """Approve and send: the admin sends the text whose hash he previewed (the draft's current hash)."""
    from motor_ai_sim.digest import approval as AP
    from motor_ai_sim.digest import build as B
    kw.setdefault("approved_by", ADMIN)
    kw.setdefault("content_hash", B.content_hash(d["DS"].load_draft(draft_id)))
    kw.setdefault("deliver", lambda m: d["delivered"].append(m))
    return AP.send_draft(draft_id, delay_s=0, sleep=lambda s: None, **kw)


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
    assert any(i.en.startswith("cruise: 12.3 N·m at 4.1 kW, efficiency 95.1 %; steady state: winding 128 °C") for i in res)
    assert all(i.group and i.group[0].startswith("New results, CIANO28 85 / ") for i in res)       # the variant is the heading, not repeated
    trial = next(i for i in res if "T9" in i.group[0])
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
    assert "12.3 N·m at 4.1 kW" in text and "New results, CIANO28 85 / L12:" in text and not draft["bilingual"]
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


# -- build.run: drafts only, never mail ------------------------------------------------------------------------------------------------------------

def test_build_run_makes_drafts_and_sends_nothing_even_in_auto_mode(d):
    def no_smtp(*a, **k):
        raise AssertionError("a run reached SMTP")
    d["mp"].setattr(smtplib, "SMTP", no_smtp)
    d["mp"].setattr(smtplib, "SMTP_SSL", no_smtp)
    d["mp"].setattr(d["E"], "send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a run mailed")))
    d["mp"].setattr(d["E"], "deliver", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a run delivered")))
    assert d["DS"].get_settings(PSCOPE)["mode"] == "auto"                        # the default, and build.run still only drafts
    rows = run(d)
    assert {r["scope"]: r["action"] for r in rows if r["action"] != "skipped"} == {PSCOPE: "drafted", XSCOPE: "drafted"}
    assert d["delivered"] == [] and d["outbox"] == []
    assert {x["status"] for x in d["DS"].list_drafts()} == {"pending"}


def test_dry_run_writes_nothing(d):
    rows = run(d, write=False)
    assert any(r["action"] == "drafted" for r in rows)
    assert d["DS"].list_drafts() == [] and d["DS"].read_log() == []


def test_modes_auto_is_the_default_and_unknown_modes_are_refused(d):
    DS = d["DS"]
    assert DS.MODES == ("auto", "approve", "off") and DS.get_settings(PSCOPE)["mode"] == "auto"           # auto is the default
    with pytest.raises(ValueError):
        DS.validate_settings_update({"mode": "automatic"})
    assert DS.set_settings(PSCOPE, {"mode": "approve"}, by=ADMIN)["mode"] == "approve"
    assert DS.set_settings(PSCOPE, {"mode": "auto"}, by=ADMIN)["mode"] == "auto"
    DS.set_settings(PSCOPE, {"mode": "off"}, by=ADMIN)
    rows = {r["scope"]: r for r in run(d)}
    assert rows[PSCOPE]["reason"] == "mode_off" and DS.get_state(PSCOPE).get("draft_id") is None


def test_stored_approve_that_is_only_the_old_default_migrates_to_auto_a_deliberate_choice_stays(d):
    DS = d["DS"]
    root = d["dtmp"] / "digests"
    root.mkdir(exist_ok=True)
    # the shape written before 2026-10-28: every record says approve (the old default), also those made by an opt-out; one group's
    # approve was chosen on purpose (the settings log says so); one is off; one has no mode at all
    stored = {PSCOPE: {"mode": "approve", "frequency": "daily", "recipients": "all", "chosen": [], "optouts": [BOB]},
              XSCOPE: {"mode": "approve", "frequency": "weekly", "recipients": "all", "chosen": [], "optouts": [],
                       "updated_by": ADMIN, "updated_at": "2026-10-20T10:00:00Z"},
              "project:off-one": {"mode": "off"}, "project:no-mode": {"frequency": "daily"}}
    (root / "settings.json").write_text(json.dumps(stored), encoding="utf-8")
    DS.append_log({"event": "settings", "scope": XSCOPE, "by": ADMIN, "changed": {"mode": ["off", "approve"]}})
    assert DS.get_settings(PSCOPE)["mode"] == "auto" and DS.get_settings(PSCOPE)["optouts"] == [BOB]          # migrated, opt-out kept
    assert DS.get_settings(XSCOPE)["mode"] == "approve" and DS.get_settings(XSCOPE)["mode_chosen"] is True    # a deliberate approve
    assert DS.get_settings(XSCOPE)["frequency"] == "weekly"
    assert DS.get_settings("project:off-one")["mode"] == "off" and DS.get_settings("project:no-mode")["mode"] == "auto"
    mig = [r for r in DS.read_log() if r["event"] == "migrated"]
    assert len(mig) == 1 and mig[0]["scopes"] == [PSCOPE] and DS.MIGRATION_AUTO_DEFAULT in json.loads((root / "migrations.json").read_text())
    DS.get_settings(PSCOPE)                                                                                    # runs once only
    assert len([r for r in DS.read_log() if r["event"] == "migrated"]) == 1
    # after the migration a stored approve is authoritative and a later choice sticks
    DS.set_settings(PSCOPE, {"mode": "approve"}, by=ADMIN)
    assert DS.get_settings(PSCOPE)["mode"] == "approve" and DS.get_settings(PSCOPE)["mode_chosen"] is True
    DS.set_optout(PSCOPE, ANNA, True, by=ANNA)
    assert DS.get_settings(PSCOPE)["mode"] == "approve"


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
    rows = run(d, now=T0 + 600, scopes=[PSCOPE])                      # the timer fires again: nothing new -> the same draft, untouched
    assert rows[0]["action"] == "unchanged" and d["DS"].get_state(PSCOPE)["draft_id"] == first
    assert len([x for x in d["DS"].list_drafts() if x["scope"] == PSCOPE]) == 1
    res = go(d, first)
    assert res["sent"] == 2 and sorted(m["To"] for m in d["delivered"]) == sorted([ANNA, BOB])
    with pytest.raises(AP.ApprovalError) as e:                         # a second send of the same digest
        go(d, first)
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
            go(d, did, approved_by=who)
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


def test_only_the_approval_module_can_reach_smtp_and_only_the_daily_jobs_call_the_automatic_send():
    """Static guard: the package mails through ONE module; only the admin Send route and the CLI run import it."""
    delivery = {"deliver", "smtplib", "send_message", "sendmail", "SMTP", "SMTP_SSL", "send_draft", "auto_send_draft", "run_daily",
                "approval", "send"}
    reach = {py.name: _names(py) & delivery for py in sorted(SRC.glob("*.py"))}
    assert {k for k, v in reach.items() if v} == {"approval.py", "routes.py", "__main__.py"}, reach
    for scheduler in ("build.py", "collect.py", "store.py", "__init__.py"):
        assert not reach[scheduler], (scheduler, reach[scheduler])
    assert "mailing" not in _names(SRC / "__main__.py") and "mailing" not in _names(SRC / "store.py")
    assert reach["__main__.py"] == {"approval", "run_daily"}               # the CLI reaches the automatic send only through run_daily
    tree = ast.parse((SRC / "routes.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "send_draft"]
    assert len(calls) == 1                                                  # one call site: the admin "Send" route
    fn = next(f for f in ast.walk(tree) if isinstance(f, ast.AsyncFunctionDef) and f.name == "admin_send")
    assert any(c in ast.walk(fn) for c in calls)
    assert not {"auto_send_draft", "run_daily"} & _names(SRC / "routes.py")   # no route runs the automatic send
    run_route = next(f for f in ast.walk(tree) if isinstance(f, ast.AsyncFunctionDef) and f.name == "admin_run")
    assert any(isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "run" and getattr(c.func.value, "id", "") == "B"
               for c in ast.walk(run_route))                                 # "make drafts now" is build.run: drafts only
    routes_src = (SRC / "routes.py").read_text(encoding="utf-8")
    admin_send_src = routes_src.split("async def admin_send")[1].split("@admin_router")[0]
    assert 'body.get("approve") is not True' in routes_src and "Depends(auth.require_admin)" in routes_src.split("def admin_send")[0][-200:] + admin_send_src[:200]
    modes_line = (SRC / "store.py").read_text(encoding="utf-8").split("MODES = ")[1].splitlines()[0]
    assert all(m in modes_line for m in ("auto", "approve", "off"))


def test_the_cli_run_sends_auto_groups_only_and_drafts_only_does_not(d, capsys):
    from motor_ai_sim.digest.__main__ import main
    with pytest.raises(SystemExit):
        main(["run", "--send"])                                                           # no such option: the run decides by mode
    assert main(["run", "--dry-run", "--json"]) == 0
    assert d["delivered"] == [] and d["DS"].list_drafts() == []


def test_send_route_needs_admin_and_the_explicit_approve_flag(d):
    run(d, scopes=[PSCOPE])
    did = d["DS"].get_state(PSCOPE)["draft_id"]
    url = f"/api/admin/digests/{did}/send"
    from motor_ai_sim.digest import build as B
    h = B.content_hash(d["DS"].load_draft(did))
    ok = {"approve": True, "content_hash": h}
    assert client.post(url, json=ok).status_code in (401, 403)
    owner = _login(ANNA)
    assert client.post(url, json=ok, headers=owner).status_code == 403
    admin = _login(ADMIN)
    for body in ({}, {"approve": "yes", "content_hash": h}, {"approve": 1, "content_hash": h}, {"send": True}, {"approve": True},
                 {"approve": True, "content_hash": ""}, {"approve": True, "content_hash": 7}, None):
        r = client.post(url, json=body, headers=admin) if body is not None else client.post(url, headers=admin)
        assert r.status_code == 422, body
    assert d["delivered"] == [] and d["DS"].load_draft(did)["status"] == "pending"
    r = client.post(url, json=ok, headers=admin)
    assert r.status_code == 200 and r.json()["sent"] == 2
    assert d["DS"].load_draft(did)["sent"]["by"] == ADMIN
    assert client.post(url, json=ok, headers=admin).status_code == 409
    assert len(d["delivered"]) == 2


def test_send_rechecks_recipients_at_send_time(d):
    from motor_ai_sim.digest import approval as AP
    run(d, scopes=[XSCOPE])
    did = d["DS"].get_state(XSCOPE)["draft_id"]
    d["PR"].mutate_file(DIE, CFG, "production.json",
                        lambda p: p.update({"members": [m for m in p["members"] if m["email"] != LI] + [{"email": BOB, "role": "oem"}]}))
    res = go(d, did)
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
    res = go(d, draft["id"])
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
    assert r.status_code == 200 and r.json()["mode"] == "auto" and r.json()["frequency"] == "daily"
    assert {m["email"] for m in r.json()["members"]} == {ANNA, BOB}
    assert client.put(url, json={"mode": "automatic", "frequency": "hourly"}, headers=owner).status_code == 422
    assert client.put(url, json={"mode": "bogus"}, headers=owner).status_code == 422
    assert client.put(url, json={"frequency": "hourly"}, headers=owner).status_code == 422
    ok = client.put(url, json={"mode": "off", "frequency": "weekly", "recipients": "chosen", "chosen": [ANNA]}, headers=owner)
    assert ok.status_code == 200 and ok.json()["mode"] == "off" and ok.json()["updated_by"] == ANNA
    log = [x for x in d["DS"].read_log(PSCOPE) if x["event"] == "settings"]
    assert log[-1]["by"] == ANNA and log[-1]["changed"]["mode"] == ["auto", "off"] and log[-1]["ts"]
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


# -- review fixes (PR 438): approval covers the exact text, motor access, partial sends, header safety, results layers -----------------------------------------

def test_approval_carries_the_content_hash_and_a_changed_text_is_refused(d):
    from motor_ai_sim.digest import approval as AP
    from motor_ai_sim.digest import build as B
    run(d, scopes=[PSCOPE])
    did = d["DS"].get_state(PSCOPE)["draft_id"]
    h = B.content_hash(d["DS"].load_draft(did))
    for bad in (None, "", "0" * 64, h[:-1] + ("0" if h[-1] != "0" else "1")):
        with pytest.raises(AP.ApprovalError) as e:
            go(d, did, content_hash=bad)
        assert e.value.code == "content_changed", bad
    assert d["delivered"] == [] and d["DS"].load_draft(did)["status"] == "pending"
    # the hash covers subject, text, html AND the recipient list
    draft = d["DS"].load_draft(did)
    assert B.content_hash(dict(draft, recipients=draft["recipients"][:1])) != h
    assert B.content_hash(dict(draft, html_body=draft["html_body"] + " ")) != h
    assert B.content_hash(dict(draft, subject=draft["subject"] + "!")) != h
    # route: a stale hash is a 409, nothing is mailed
    admin = _login(ADMIN)
    r = client.post(f"/api/admin/digests/{did}/send", json={"approve": True, "content_hash": "f" * 64}, headers=admin)
    assert r.status_code == 409 and d["delivered"] == []
    pv = client.get(f"/api/admin/digests/{did}/preview", headers=admin).json()
    assert pv["content_hash"] == h
    assert client.post(f"/api/admin/digests/{did}/send", json={"approve": True, "content_hash": pv["content_hash"]}, headers=admin).status_code == 200


def test_an_opened_draft_is_never_rewritten_a_refresh_supersedes_it(d):
    admin = _login(ADMIN)
    run(d, scopes=[PSCOPE])
    a = d["DS"].get_state(PSCOPE)["draft_id"]
    # nobody looked at it yet: new activity refreshes the SAME draft in place
    d["PS"].append_jsonl(PID, "thread.jsonl", {"id": "m2", "author_email": BOB, "created_at": at(-900), "text": "second message"})
    assert run(d, now=T0 + 60, scopes=[PSCOPE])[0]["action"] == "refreshed" and d["DS"].get_state(PSCOPE)["draft_id"] == a
    assert "second message" in d["DS"].load_draft(a)["text_body"]
    # the owner previews it -> frozen
    pv = client.get(f"/api/admin/digests/{a}/preview", headers=admin).json()
    text_a = d["DS"].load_draft(a)["text_body"]
    assert d["DS"].load_draft(a)["opened"]["by"] == ADMIN
    d["PS"].append_jsonl(PID, "thread.jsonl", {"id": "m3", "author_email": BOB, "created_at": at(-800), "text": "third message"})
    rows = run(d, now=T0 + 120, scopes=[PSCOPE])
    assert rows[0]["action"] == "superseded"
    b = d["DS"].get_state(PSCOPE)["draft_id"]
    assert b != a
    old, new = d["DS"].load_draft(a), d["DS"].load_draft(b)
    assert old["text_body"] == text_a and "third message" not in old["text_body"] and old["status"] == "superseded" and old["superseded_by"] == b
    assert "third message" in new["text_body"] and new["status"] == "pending" and new["supersedes"] == a and new["revision"] == 1
    # what he previewed can no longer be approved, and the old hash cannot send the new text
    r = client.post(f"/api/admin/digests/{a}/send", json={"approve": True, "content_hash": pv["content_hash"]}, headers=admin)
    assert r.status_code == 409 and "newer draft" in r.json()["detail"]
    r = client.post(f"/api/admin/digests/{b}/send", json={"approve": True, "content_hash": pv["content_hash"]}, headers=admin)
    assert r.status_code == 409 and d["delivered"] == []
    assert len([x for x in d["DS"].list_drafts() if x["status"] == "pending"]) == 1
    # a second run with nothing new leaves revision 1 alone; the list shows one waiting draft, the old one in history
    assert run(d, now=T0 + 180, scopes=[PSCOPE])[0]["action"] == "unchanged"
    lst = client.get("/api/admin/digests", headers=admin).json()
    assert [x["id"] for x in lst["pending"]] == [b] and a in [x["id"] for x in lst["history"]]
    assert go(d, b)["sent"] == 2


def test_motor_lines_need_every_recipient_to_be_able_to_open_the_motor(d):
    from motor_ai_sim.digest import approval as AP
    d["U"].set_motor_grants(BOB, all_motors=False, dies=[], configs=[])               # Bob cannot open CIANO28 85 / L12 (or T9)
    run(d, scopes=[PSCOPE])
    draft = draft_of(d, PSCOPE)
    text = draft["text_body"]
    assert "New results" not in text and "Design step note" not in text and "12.3 N" not in text and "L12" not in text
    assert "New message from Bob" in text and "Specification version 2 submitted" in text          # the rest of the project still goes out
    assert not [i for i in draft["items"] if i.get("motor")]
    # everybody may open it: the lines are back, and carry the motor for the send-time re-check
    d["U"].set_motor_grants(BOB, all_motors=False, dies=[], configs=["CIANO28 85/L12"])
    run(d, now=T0 + 60, scopes=[PSCOPE])
    draft = draft_of(d, PSCOPE)
    assert "New results" in draft["text_body"] and any(i.get("motor") == ["CIANO28 85", "L12"] for i in draft["items"])
    # access is withdrawn after the draft was made: the send is refused (409) and nothing goes out
    d["U"].set_motor_grants(BOB, all_motors=False, dies=[], configs=[])
    with pytest.raises(AP.ApprovalError) as e:
        go(d, draft["id"])
    assert e.value.code == "access_changed" and d["delivered"] == []
    admin = _login(ADMIN)
    h = client.get(f"/api/admin/digests/{draft['id']}/preview", headers=admin).json()["content_hash"]
    r = client.post(f"/api/admin/digests/{draft['id']}/send", json={"approve": True, "content_hash": h}, headers=admin)
    assert r.status_code == 409 and "Make drafts now" in r.json()["detail"] and d["delivered"] == []
    # the run supersedes the opened draft with one without the motor lines; that text can be sent
    run(d, now=T0 + 120, scopes=[PSCOPE])
    cur = d["DS"].get_state(PSCOPE)["draft_id"]
    assert cur != draft["id"] and "New results" not in d["DS"].load_draft(cur)["text_body"] and go(d, cur)["sent"] == 2


def test_an_admin_recipient_and_unknown_accounts_in_the_motor_check(d):
    from motor_ai_sim.digest import build as B
    assert B.motor_visible(ADMIN, "Any Die", "any-config")                                  # admins open everything
    assert B.motor_visible(BOB, "CIANO28 85", "L12") and not B.motor_visible(BOB, "CIANO28 85", "Z99")
    assert not B.motor_visible("nobody@example.com", "CIANO28 85", "L12")                    # unknown account: closed, not "anonymous"


def test_a_partly_failed_send_is_partial_and_only_the_failed_are_retried(d):
    from motor_ai_sim.digest import approval as AP
    run(d, scopes=[PSCOPE])
    did = d["DS"].get_state(PSCOPE)["draft_id"]

    def flaky(msg):
        if msg["To"] == BOB:
            raise RuntimeError("mailbox full")
        d["delivered"].append(msg)
    res = go(d, did, deliver=flaky)
    assert res["sent"] == 1 and res["failed"] == 1 and res["outstanding"] == [BOB]
    cur = d["DS"].load_draft(did)
    assert cur["status"] == "partial" and cur["partial"]["outstanding"] == [BOB] and [m["To"] for m in d["delivered"]] == [ANNA]
    assert d["DS"].get_state(PSCOPE)["draft_id"] == did and "cursor" not in d["DS"].get_state(PSCOPE)          # the period is not closed
    # the scheduler leaves a partial draft alone, even with new activity
    d["PS"].append_jsonl(PID, "thread.jsonl", {"id": "m9", "author_email": BOB, "created_at": at(-300), "text": "late news"})
    assert run(d, now=T0 + 60, scopes=[PSCOPE])[0]["reason"] == "partial_send_open"
    assert "late news" not in d["DS"].load_draft(did)["text_body"]
    # the list still shows it as waiting; one more approval retries Bob only
    admin = _login(ADMIN)
    assert [x["status"] for x in client.get("/api/admin/digests", headers=admin).json()["pending"]] == ["partial"]
    res = go(d, did)
    assert res["sent"] == 1 and [m["To"] for m in d["delivered"]] == [ANNA, BOB]                    # Anna was not mailed twice
    done = d["DS"].load_draft(did)
    assert done["status"] == "sent" and d["DS"].get_state(PSCOPE)["cursor"] == done["period"]["end"]
    with pytest.raises(AP.ApprovalError):
        go(d, did)


def test_total_failure_stays_pending_and_a_discard_closes_a_partial_draft(d):
    run(d, scopes=[PSCOPE])
    did = d["DS"].get_state(PSCOPE)["draft_id"]

    def down(msg):
        raise RuntimeError("smtp down")

    def only_anna(msg):
        if msg["To"] != ANNA:
            raise RuntimeError("x")
        d["delivered"].append(msg)
    res = go(d, did, deliver=down)
    assert res["sent"] == 0 and d["DS"].load_draft(did)["status"] == "pending" and d["DS"].load_draft(did)["last_error"]
    go(d, did, deliver=only_anna)
    assert d["DS"].load_draft(did)["status"] == "partial"
    admin = _login(ADMIN)
    assert client.post(f"/api/admin/digests/{did}/discard", headers=admin).json()["status"] == "discarded"
    assert "cursor" in d["DS"].get_state(PSCOPE) and d["DS"].get_state(PSCOPE).get("draft_id") is None


def test_a_running_send_is_a_409_not_a_500(d):
    from motor_ai_sim.digest import build as B
    run(d, scopes=[PSCOPE])
    did = d["DS"].get_state(PSCOPE)["draft_id"]
    lock = Path(str(d["DS"].sendlog_file(did)) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("", encoding="utf-8")                                    # another send of this draft is running (or crashed)
    admin = _login(ADMIN)
    r = client.post(f"/api/admin/digests/{did}/send", json={"approve": True, "content_hash": B.content_hash(d["DS"].load_draft(did))},
                    headers=admin)
    assert r.status_code == 409 and "another send" in r.json()["detail"] and d["delivered"] == []
    lock.unlink()


def test_titles_cannot_inject_header_lines(d):
    from motor_ai_sim.digest import approval as AP
    from motor_ai_sim.digest import build as B
    evil = "Erium" + chr(13) + chr(10) + "Bcc: spy@evil.example" + chr(0) + chr(0x2028) + "X"
    d["PS"].mutate_project(PID, lambda p: p.update({"title": evil}))
    run(d, scopes=[PSCOPE])
    draft = draft_of(d, PSCOPE)
    assert not any(ord(c) < 32 or ord(c) in (127, 0x2028, 0x2029) for c in draft["subject"] + draft["title"])
    assert draft["title"] == "Erium Bcc: spy@evil.example X"                      # the line break became a space, no header can start
    msg = B.build_message(draft, ANNA, base="https://aerostator.com", sender="noreply@aerostator.com")
    assert msg["Bcc"] is None and msg["Subject"] == draft["subject"]
    assert B.header_safe("fine") and not B.header_safe("a" + chr(10) + "b") and not B.header_safe("")
    # defence in depth: a stored draft with an unsafe subject is refused at send time
    bad = dict(draft, subject="Hi" + chr(10) + "Bcc: x@y.z")
    d["DS"].save_draft(bad)
    with pytest.raises(AP.ApprovalError) as e:
        go(d, draft["id"], content_hash=B.content_hash(bad))
    assert e.value.code == "unsafe_subject" and d["delivered"] == []


def test_results_are_read_from_the_shared_layers_never_the_process_workspace(d, tmp_path):
    from motor_ai_sim import duty_results as DR
    from motor_ai_sim.digest import collect as C
    shared, ws = tmp_path / "shared" / ".duty_results.json", tmp_path / "ws" / ".duty_results.json"
    for p, duty in ((shared, "shared_duty"), (ws, "private_duty")):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"version": 1, "results": {"CIANO28 85": {"L12": {duty: {"em_run": {"computed_at": at(-60),
                                                                                                    "numbers": {"T_avg_Nm": 5.0}}}}}}}),
                     encoding="utf-8")
    d["mp"].setattr(DR, "shared_store_path", lambda: shared)
    d["mp"].setattr(DR, "_config_dir", lambda: ws.parent)
    assert DR.store_path() == ws                                         # the process workspace's own ledger exists...
    assert set(C.stored_results("CIANO28 85", "L12")) == {"shared_duty"}  # ...and the digest does not read it
    with DR.using_store(shared):
        assert set(C.stored_results("CIANO28 85", "L12")) == {"shared_duty"}           # whoever runs it, same answer


# -- link rules (owner 2026-10-28): a link only for something new, one per group of equal targets, never the same URL twice -----------------------------

def _linked_items():
    """The shape of a real project digest: two variants interleaved, notices in between (newest first)."""
    from motor_ai_sim.digest import collect as C

    def L(die, cfg):
        return f"/projects/{PID}/motors?die={die}&config={cfg}"

    def it(kind, en, link, **kw):
        return C.Item(kind, 0.0, en, en + " [zh]", link, **kw)

    def res(cfg, duty):
        return it("results", f"{duty}: 12.3 N·m", L("D", cfg), motor=("D", cfg),
                  group=(f"New results, D / {cfg}", f"新计算结果，D / {cfg}"))
    return [res("L110", "cruise"), res("L110", "peak"),                                  # one heading, two sub-lines
            res("L100", "cruise"),
            it("report_reviewed", "Report checked for D / L100", L("D", "L100")),
            it("motor_removed", "Design variant removed: D / L110", f"/projects/{PID}/motors"),
            it("recommended", "Recommended variant: D / L100", L("D", "L100")),
            it("motor_added", "Design variant added: D / L100", L("D", "L100")),
            it("member_joined", "A new engineer joined the project: Dan <dan@example.com>", f"/projects/{PID}"),
            it("spec_version", "Specification version 2 submitted", f"/projects/{PID}/specification")]


def _group(path=f"/projects/{PID}"):
    from motor_ai_sim.digest import build as B
    return B.Group("project:" + PID, "project", {}, "Demo X1", "Demo X1", [], True, path)


def _urls(text):
    return re.findall(r"https?://\S+", text)


def test_link_policy_per_kind_and_notices_have_no_target():
    from motor_ai_sim.digest import collect as C
    for kind in ("results", "motor_added", "spec_version", "report_reviewed", "revision", "document", "message"):
        assert C.Item(kind, 0.0, "x", "x", "/projects/p").target == "/projects/p", kind
    for kind in ("motor_removed", "member_joined", "motor_status", "notes", "project", "bom", "costs", "proposal_decision"):
        assert C.Item(kind, 0.0, "x", "x", "/projects/p").target is None, kind
    assert C.Item("parts", 0.0, "x", "x", "/p", link_policy="none").target is None            # one item may override its kind
    assert C.Item("unlisted_kind", 0.0, "x", "x", "/p").target == "/p"                       # an unknown kind keeps its link
    assert C.Item("results", 0.0, "x", "x", None).target is None


def test_compose_one_link_per_group_notices_none_and_no_url_twice(d):
    from motor_ai_sim.digest import build as B
    text, html_body = B.compose(_group(), _linked_items(), 0, T0 - 3600, T0, False)
    urls = _urls(text)
    assert len(urls) == len(set(urls)), urls                                              # no repeated URL
    lines = text.splitlines()
    # the heading group: both sub-lines first, its link once at the end (not on the heading)
    h = lines.index("- New results, D / L110:")
    assert lines[h + 1:h + 3] == ["    - cruise: 12.3 N·m", "    - peak: 12.3 N·m"] and "motors?die=D&config=L110" in lines[h + 3]
    # L100: results heading + report are ONE group (same target): one link, after the report line
    r = lines.index("- Report checked for D / L100")
    assert "config=L100" in lines[r + 1] and "config=L100" not in "".join(lines[:r])
    assert lines[r - 1].startswith("    - cruise")
    # notices: no link under them
    for notice in ("- Design variant removed: D / L110", "- A new engineer joined the project: Dan <dan@example.com>"):
        i = lines.index(notice)
        assert not lines[i + 1].startswith("  http"), notice
    # the later L100 group (recommended + added) repeats a URL already used: shown, without a second link
    i = lines.index("- Design variant added: D / L100")
    assert not lines[i + 1].startswith("  http")
    assert any(u.endswith("/specification") for u in urls)
    # HTML follows the same rules: every href once, none on a notice
    hrefs = re.findall(r'href="([^"]+)"', html_body)
    assert len(hrefs) == len(set(hrefs)) == len(urls)
    removed = next(x for x in html_body.splitlines() if "Design variant removed" in x)
    joined = next(x for x in html_body.splitlines() if "A new engineer joined" in x)
    assert "href" not in removed and "href" not in joined and "&lt;dan@example.com&gt;" in joined


def test_compose_zh_mirrors_en_and_each_language_section_has_its_own_links(d):
    from motor_ai_sim.digest import build as B
    text, html_body = B.compose(_group(), _linked_items(), 0, T0 - 3600, T0, True)
    en, zh = text.split("-" * 40)
    assert _urls(en) == _urls(zh) and len(_urls(en)) == len(set(_urls(en)))                 # same links, once per section
    assert "[zh]" in zh and "[zh]" not in en
    sections = html_body.split("<hr")
    assert len(sections) == 2
    for sec in sections:
        hrefs = re.findall(r'href="([^"]+)"', sec)
        assert len(hrefs) == len(set(hrefs)) == len(_urls(en))
    assert "打开" in sections[1] and ">Open<" in sections[0]


def test_consecutive_lines_with_the_same_target_share_one_link_at_the_end(d):
    from motor_ai_sim.digest import build as B, collect as C
    same = [C.Item("message", 0.0, f"New message {n}", f"留言 {n}", "/projects/p/messages") for n in (1, 2, 3)]
    other = C.Item("spec_version", 0.0, "Spec 2", "规格 2", "/projects/p/specification")
    text, _ = B.compose(_group(), same + [other], 0, T0 - 3600, T0, False)
    lines = text.splitlines()
    i = lines.index("- New message 3")
    assert lines[i + 1].endswith("/projects/p/messages") and "http" not in "".join(lines[:i])
    assert lines[i + 2] == "- Spec 2" and lines[i + 3].endswith("/projects/p/specification")


def test_more_link_is_dropped_when_the_page_was_already_linked(d):
    from motor_ai_sim.digest import build as B, collect as C
    items = [C.Item("message", 0.0, "New message", "留言", f"/projects/{PID}")]
    text, html_body = B.compose(_group(), items, 7, T0 - 3600, T0, False)
    assert "and 7 more" in text and len(_urls(text)) == 1 and html_body.count("<a ") == 1
    gone = [C.Item("motor_removed", 0.0, "gone", "移除", f"/projects/{PID}")]
    text, html_body = B.compose(_group(), gone, 7, T0 - 3600, T0, False)
    assert len(_urls(text)) == 1 and "and 7 more: " in text and html_body.count("<a ") == 1      # no earlier link: the more link stays


def test_member_joined_names_the_person_and_has_no_link(d):
    from motor_ai_sim.digest import collect as C
    d["PS"].append_jsonl(PID, "audit.jsonl", {"time": at(-600), "actor_email": "dan@example.com", "action": "invite_claimed",
                                              "entity": "member", "entity_id": "dan@example.com", "details": {"role": "engineer"}})
    d["PS"].append_jsonl(PID, "audit.jsonl", {"time": at(-500), "actor_email": ANNA, "action": "invite", "entity": "member",
                                              "entity_id": "nobody@example.com", "details": {"role": "customer", "pending": False}})
    items = C.collect_project(d["PS"].get_project(PID), T0 - 7200, T0, results=d["results"])
    joined = {i.key: i for i in items if i.kind == "member_joined"}
    assert joined["member:dan@example.com"].en == "A new engineer joined the project: Dan <dan@example.com>"
    assert "Dan <dan@example.com>" in joined["member:dan@example.com"].zh
    assert joined["member:nobody@example.com"].en.endswith(": nobody@example.com")           # no account name: the address alone
    assert all(i.target is None for i in joined.values())


def test_a_real_project_draft_has_no_repeated_url_and_no_link_on_notices(d):
    d["PS"].append_jsonl(PID, "audit.jsonl", {"time": at(-600), "actor_email": "dan@example.com", "action": "invite_claimed",
                                              "entity": "member", "entity_id": "dan@example.com", "details": {"role": "engineer"}})
    d["PS"].append_jsonl(PID, "audit.jsonl", {"time": at(-700), "actor_email": ANNA, "action": "motor.unlink", "entity": "motor",
                                              "entity_id": "CIANO28 85/L12", "details": {}})
    d["PS"].mutate_project(PID, lambda p: p.update({"members": [{"email": ANNA, "role": "owner"}]}))     # engineers only: sees every line
    run(d, scopes=[PSCOPE])
    draft = draft_of(d, PSCOPE)
    urls = [u for u in _urls(draft["text_body"]) if "/api/digest/" not in u]
    assert urls and len(urls) == len(set(urls))
    lines = draft["text_body"].splitlines()
    for notice in ("- Design variant removed: CIANO28 85 / L12", "- A new engineer joined the project: Dan <dan@example.com>"):
        assert not lines[lines.index(notice) + 1].startswith("  http"), notice
    stored = {i["kind"]: i["link_policy"] for i in draft["items"]}
    assert stored["motor_removed"] == "none" and stored["member_joined"] == "none" and stored["results"] == "new"


# -- the automatic daily send (owner decision 2026-10-28) ---------------------------------------------------------------------------------------------------------

def daily(d, now=T0, **kw):
    """The scheduled run (build the drafts, send the groups in auto mode) with the fake mailbox."""
    from motor_ai_sim.digest import approval as AP
    kw.setdefault("deliver", lambda m: d["delivered"].append(m))
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("delay_s", 0)
    kw.setdefault("results", d["results"])
    return AP.run_daily(now, **kw)


def test_auto_run_sends_each_new_draft_through_the_send_path_as_the_system_actor(d):
    from motor_ai_sim import mailing
    from motor_ai_sim.digest import approval as AP
    rows = {r["scope"]: r for r in daily(d)}
    assert {s: r["auto"]["status"] for s, r in rows.items() if "auto" in r} == {PSCOPE: "sent", XSCOPE: "sent"}
    assert sorted(m["To"] for m in d["delivered"]) == sorted([ANNA, BOB, ANNA, LI])
    for scope in (PSCOPE, XSCOPE):
        done = [x for x in d["DS"].list_drafts() if x["scope"] == scope]
        assert len(done) == 1 and done[0]["status"] == "sent" and done[0]["sent"]["by"] == AP.AUTO_ACTOR == "auto (daily 06:00)"
        assert done[0]["sent"]["sent"] == len(done[0]["recipients"]) and done[0]["sent"]["failed"] == 0
        assert d["DS"].get_state(scope)["cursor"] == done[0]["period"]["end"] and d["DS"].get_state(scope).get("draft_id") is None
        ev = [r for r in d["DS"].read_log(scope) if r["event"] == "sent"]
        assert len(ev) == 1 and ev[0]["by"] == AP.AUTO_ACTOR                                  # history shows who sent it
        assert {r["user"] for r in mailing.read_log(d["DS"].sendlog_file(done[0]["id"])) if r["status"] == "sent"}   # the send log
    # the timer fires again (or the report is forced): the period is closed, nobody is mailed twice
    again = daily(d, now=T0 + 600)
    assert not any("auto" in r for r in again) and len(d["delivered"]) == 4


def test_auto_send_rechecks_recipients_like_the_admin_send(d):
    from motor_ai_sim.digest import approval as AP
    run(d, scopes=[PSCOPE])                                         # a draft (Anna and Bob) exists ...
    did = d["DS"].get_state(PSCOPE)["draft_id"]
    d["CP"].unsubscribe_all(BOB, source="unsubscribe")              # ... then Bob unsubscribes from all e-mail before it goes out
    res = AP.auto_send_draft(did, deliver=lambda m: d["delivered"].append(m), delay_s=0, sleep=lambda s: None, now=lambda: T0)
    assert [m["To"] for m in d["delivered"]] == [ANNA]
    assert any(x["email"] == BOB for x in res["skipped"])
    assert d["DS"].load_draft(did)["sent"]["by"] == AP.AUTO_ACTOR


def test_an_idle_group_sends_nothing_in_auto_mode(d):
    rows = daily(d, now=T0 + 40 * 86400, results=lambda die, cfg: {})            # forty quiet days: no item in the period
    assert d["delivered"] == [] and d["DS"].list_drafts() == []
    assert all(r["action"] == "skipped" and "auto" not in r for r in rows)
    assert {r["reason"] for r in rows if r["scope"] in (PSCOPE, XSCOPE)} == {"no_activity"}


def test_approve_and_off_groups_are_never_sent_by_the_run_only_auto_ones(d):
    DS = d["DS"]
    DS.set_settings(PSCOPE, {"mode": "approve"}, by=ADMIN)                       # an explicit per-group override
    rows = {r["scope"]: r for r in daily(d)}
    assert "auto" not in rows[PSCOPE] and rows[XSCOPE]["auto"]["status"] == "sent"
    assert DS.load_draft(DS.get_state(PSCOPE)["draft_id"])["status"] == "pending"            # still waits for an admin
    assert sorted(m["To"] for m in d["delivered"]) == sorted([ANNA, LI])
    DS.set_settings(PSCOPE, {"mode": "off"}, by=ADMIN)
    daily(d, now=T0 + 600)
    assert len(d["delivered"]) == 2                                                          # off: nothing, and the waiting draft stays put
    # the admin still sends the waiting approve-mode draft by hand, through the same path
    DS.set_settings(PSCOPE, {"mode": "approve"}, by=ADMIN)
    did = DS.get_state(PSCOPE)["draft_id"]
    assert go(d, did)["sent"] == 2 and DS.load_draft(did)["sent"]["by"] == ADMIN


def test_drafts_only_and_dry_run_send_nothing_in_auto_mode(d):
    daily(d, send=False)
    assert d["delivered"] == [] and {x["status"] for x in d["DS"].list_drafts()} == {"pending"}
    daily(d, write=False)
    assert d["delivered"] == []
    rows = daily(d, now=T0 + 600)                                                            # the next real run sends the waiting drafts
    assert {r["auto"]["status"] for r in rows if "auto" in r} == {"sent"} and len(d["delivered"]) == 4


def test_a_failed_automatic_send_keeps_the_draft_and_the_next_run_retries_it(d):
    from motor_ai_sim.digest import approval as AP

    def down(msg):
        raise RuntimeError("smtp down")
    rows = {r["scope"]: r for r in daily(d, deliver=down, scopes=[PSCOPE])}
    assert rows[PSCOPE]["auto"]["status"] == "failed" and rows[PSCOPE]["auto"]["failed"] == 2
    cur = d["DS"].load_draft(d["DS"].get_state(PSCOPE)["draft_id"])
    assert cur["status"] == "pending" and cur["last_error"]["by"] == AP.AUTO_ACTOR
    rows = {r["scope"]: r for r in daily(d, now=T0 + 600, scopes=[PSCOPE])}
    assert rows[PSCOPE]["auto"]["status"] == "sent" and len(d["delivered"]) == 2


def test_a_partly_failed_automatic_send_retries_only_the_failed_ones(d):
    sent_to: list = []
    down = {"bob": True}

    def flaky(msg):
        if msg["To"] == BOB and down["bob"]:
            raise RuntimeError("mailbox full")
        sent_to.append(msg["To"])
    rows = {r["scope"]: r for r in daily(d, deliver=flaky, scopes=[PSCOPE])}
    assert rows[PSCOPE]["auto"]["status"] == "partial" and sent_to == [ANNA]
    down["bob"] = False
    rows = {r["scope"]: r for r in daily(d, now=T0 + 86400, deliver=flaky, scopes=[PSCOPE])}        # next day: Bob only
    assert rows[PSCOPE]["reason"] == "partial_send_open" and rows[PSCOPE]["auto"]["status"] == "sent"
    assert sent_to == [ANNA, BOB]


def test_a_blocked_automatic_send_is_recorded_not_raised(d):
    from motor_ai_sim.digest import approval as AP
    run(d, scopes=[PSCOPE])
    did = d["DS"].get_state(PSCOPE)["draft_id"]
    d["DS"].update_draft(did, lambda doc: doc.update(subject="Bad" + chr(13) + chr(10) + "Bcc: spy@evil.example"))   # refused by the send path
    out = AP._auto_one(did, PSCOPE, lambda m: d["delivered"].append(m), now=lambda: T0, sleep=lambda s: None, delay_s=0)
    assert out["status"] == "blocked" and out["code"] in ("unsafe_subject", "content_changed") and d["delivered"] == []
    assert d["DS"].load_draft(did)["last_error"]["by"] == AP.AUTO_ACTOR


def test_auto_send_refuses_a_group_that_is_not_in_auto_mode_and_smtp_must_be_configured(d):
    from motor_ai_sim.digest import approval as AP
    d["DS"].set_settings(PSCOPE, {"mode": "approve"}, by=ADMIN)
    run(d, scopes=[PSCOPE])
    did = d["DS"].get_state(PSCOPE)["draft_id"]
    with pytest.raises(AP.ApprovalError) as e:
        AP.auto_send_draft(did, deliver=lambda m: d["delivered"].append(m), delay_s=0, now=lambda: T0)
    assert e.value.code == "not_auto" and d["delivered"] == []
    d["DS"].set_settings(PSCOPE, {"mode": "auto"}, by=ADMIN)
    d["mp"].setattr(d["E"], "smtp_configured", lambda: False)
    rows = {r["scope"]: r for r in AP.run_daily(T0, results=d["results"], scopes=[PSCOPE])}
    assert rows[PSCOPE]["auto"]["code"] == "smtp_not_configured" and rows[PSCOPE]["auto"]["status"] == "blocked"
    assert d["delivered"] == [] and d["DS"].load_draft(did)["status"] == "pending"
