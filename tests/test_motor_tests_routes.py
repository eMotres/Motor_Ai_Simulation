"""Tests section routes: who may see / add / edit, the audit, and the read-only project view behind its flag."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim.motor_tests import model_source
from motor_ai_sim.motor_tests import routes as MTR
from motor_ai_sim.motor_tests import store as MTS

ADMIN = "admin@example.com"
ENGINEER = "engineer@example.com"
OEM_NDA = "oem_nda@example.com"
OEM_NO_NDA = "oem_nonda@example.com"
NON_MEMBER = "nonmember@example.com"
DIE, CONFIG = "Demo die", "L50"
BASE = f"/api/motor-tests/{DIE}/{CONFIG}"

PARAM_TEST = {"kind": "parameters", "date": "2026-10-26", "instrument": "TH2822D", "profile": "jiqiren",
              "conditions": {"ambient_c": 26.3, "rotor": "in"},
              "resistance": {"temp_c": 26.3, "pairs": [{"pair": "1-2", "value_mohm": 17.57},
                                                       {"pair": "2-3", "value_mohm": 17.47},
                                                       {"pair": "1-3", "value_mohm": 17.27}]}}


def _empty_model(die, config):
    return {"available": False, "duties": [], "noload": [], "r_ref": None, "l_ref": None, "l_stator_only": None,
            "magnet": {"points": []}, "mass_kg": None, "mech": None, "connection": "star", "has_bearings": False}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth, users as U
    from motor_ai_sim.api import app

    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "production"))
    monkeypatch.setenv("MOTRES_MOTOR_TESTS_ROOT", str(tmp_path / "motor_tests"))
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-motor-tests-1234")
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    monkeypatch.setattr(model_source, "collect", _empty_model)
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(ENGINEER, "password-eng", role="engineer", name="Engineer")
    U.create_user(OEM_NDA, "password-nda", role="oem", name="Factory")
    U.create_user(OEM_NO_NDA, "password-nonda", role="oem", name="Factory 2")
    U.create_user(NON_MEMBER, "password-none", role="oem", name="Stranger")
    U.set_nda(OEM_NDA, True, "2026-10-01", by=ADMIN)
    client = TestClient(app)
    h = {e: {"Authorization": f"Bearer {U.issue_token(e)}"} for e in (ADMIN, ENGINEER, OEM_NDA, OEM_NO_NDA, NON_MEMBER)}
    r = client.post(f"/api/admin/production/{DIE}/{CONFIG}", headers=h[ADMIN], json={
        "title": {"en": "t", "zh": "t", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
        "order_quantity": 1,
        "members": [{"email": ENGINEER, "role": "engineer"}, {"email": OEM_NDA, "role": "oem"},
                    {"email": OEM_NO_NDA, "role": "oem"}]})
    assert r.status_code == 200, r.text
    return client, h


def test_who_may_open_the_tests(env):
    client, h = env
    eng = client.get(BASE, headers=h[ENGINEER])
    assert eng.status_code == 200, eng.text
    assert eng.json()["can"] == {"add": True, "edit": True, "settings": True}
    oem = client.get(BASE, headers=h[OEM_NDA])
    assert oem.status_code == 200 and oem.json()["can"] == {"add": True, "edit": False, "settings": False}
    assert client.get(BASE, headers=h[OEM_NO_NDA]).status_code == 403          # the production's own NDA gate
    assert client.get(BASE, headers=h[NON_MEMBER]).status_code == 404          # indistinguishable from missing
    assert client.get(BASE).status_code == 401
    assert client.get(BASE, headers=h[ADMIN]).json()["settings"] == {"tests_visible_to_customer": False}


def test_factory_adds_engineer_edits_and_deletes_everything_audited(env):
    client, h = env
    added = client.post(BASE + "/tests", headers=h[OEM_NDA], json=PARAM_TEST)
    assert added.status_code == 200, added.text
    tid = added.json()["id"]
    assert added.json()["created_by"] == OEM_NDA
    # the factory measures, it does not rewrite or remove
    assert client.put(BASE + f"/tests/{tid}", headers=h[OEM_NDA], json=PARAM_TEST).status_code == 403
    assert client.delete(BASE + f"/tests/{tid}", headers=h[OEM_NDA]).status_code == 403
    edited = client.put(BASE + f"/tests/{tid}", headers=h[ENGINEER], json={**PARAM_TEST, "note": "after acid"})
    assert edited.status_code == 200 and edited.json()["note"] == "after acid"
    assert edited.json()["created_by"] == OEM_NDA and edited.json()["updated_by"] == ENGINEER
    listed = client.get(BASE, headers=h[OEM_NDA]).json()
    assert [t["id"] for t in listed["tests"]] == [tid] and tid in listed["compare"]
    assert client.delete(BASE + f"/tests/{tid}", headers=h[ENGINEER]).json() == {"ok": True}
    assert client.get(BASE, headers=h[ENGINEER]).json()["tests"] == []
    assert client.get(BASE + "/audit", headers=h[OEM_NDA]).status_code == 403
    audit = client.get(BASE + "/audit", headers=h[ENGINEER]).json()["audit"]
    assert [(a["action"], a["who"]) for a in audit] == [("create", OEM_NDA), ("update", ENGINEER), ("delete", ENGINEER)]
    assert audit[1]["snapshot"]["before"]["note"] == "" and audit[2]["snapshot"]["before"]["id"] == tid


def test_bad_input_and_unknown_documents_are_refused_and_change_nothing(env):
    client, h = env
    bad = client.post(BASE + "/tests", headers=h[ENGINEER], json={**PARAM_TEST, "date": "26.10.2026"})
    assert bad.status_code == 422 and "date" in bad.json()["detail"]
    doc = client.post(BASE + "/tests", headers=h[ENGINEER], json={**PARAM_TEST, "documents": ["doc_unknown123"]})
    assert doc.status_code == 422 and "documents" in doc.json()["detail"]
    assert client.put(BASE + "/tests/test_missing", headers=h[ENGINEER], json=PARAM_TEST).status_code == 404
    assert client.get(BASE, headers=h[ENGINEER]).json()["tests"] == []
    assert MTS.read_audit(DIE, CONFIG) == []


def test_a_test_names_production_documents_by_id(env):
    client, h = env
    up = client.post(f"/api/production/{DIE}/{CONFIG}/documents", headers=h[OEM_NDA],
                     files={"file": ("report.pdf", b"%PDF-1.4 x", "application/pdf")})
    assert up.status_code == 200, up.text
    doc_id = up.json()["id"]
    r = client.post(BASE + "/tests", headers=h[OEM_NDA], json={**PARAM_TEST, "documents": [doc_id]})
    assert r.status_code == 200 and r.json()["documents"] == [doc_id]
    assert {d["id"] for d in client.get(BASE, headers=h[OEM_NDA]).json()["documents"]} == {doc_id}


def test_customer_visibility_flag_is_set_by_engineers_only(env):
    client, h = env
    assert client.patch(BASE + "/settings", headers=h[OEM_NDA], json={"tests_visible_to_customer": True}).status_code == 403
    assert client.patch(BASE + "/settings", headers=h[ENGINEER], json={"tests_visible_to_customer": "yes"}).status_code == 422
    ok = client.patch(BASE + "/settings", headers=h[ENGINEER], json={"tests_visible_to_customer": True})
    assert ok.status_code == 200 and MTS.load(DIE, CONFIG)["settings"]["tests_visible_to_customer"] is True
    assert MTS.read_audit(DIE, CONFIG)[-1]["action"] == "settings"


def test_admin_may_keep_tests_of_a_configuration_without_production(env, tmp_path):
    client, h = env
    base = "/api/motor-tests/Other die/L1"
    assert client.post(base + "/tests", headers=h[ADMIN], json=PARAM_TEST).status_code == 200
    assert client.get(base, headers=h[ENGINEER]).status_code == 404            # not a member of a production there


# ── the project (Development) view ──────────────────────────────────────────────────────────

IDENTITIES = {
    "engineer": {"email": "engineer@example.test", "role": "engineer"},
    "customer": {"email": "customer@example.test", "role": "customer"},
    "owner": {"email": "owner@example.test", "role": "customer"},
    "stranger": {"email": "stranger@example.test", "role": "customer"},
}


@pytest.fixture()
def project(tmp_path, monkeypatch):
    from motor_ai_sim import duty_results
    from motor_ai_sim.development import access, motors, store
    from motor_ai_sim.routes import family
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MOTRES_MOTOR_TESTS_ROOT", str(tmp_path / "motor_tests"))
    (tmp_path / "projects").mkdir()
    store.create_project({"id": "alpha", "members": [{"email": "customer@example.test", "role": "customer"},
                                                     {"email": "owner@example.test", "role": "owner"}],
                          "linked_motors": [{"die": "die", "config": "opt1"}, {"die": "die", "config": "opt2"}]})
    store.save_index({"motors": {motors._key("die", c): {"project_id": "alpha"} for c in ("opt1", "opt2")}})
    monkeypatch.setattr(access, "require_signed_in", lambda request: IDENTITIES[request.headers.get("x-user")])
    die_dir = tmp_path / "catalog"
    die_dir.mkdir()
    (die_dir / "opt1.yaml").write_text(
        "winding: {star_delta: star}\nduties:\n  - name: rated\n    rpm: 3000\n    current_arms: 100\n"
        "    summary: {rpm: 3000, I_phase_rms_A: 100, R_phase_ohm: 0.008, coil_temp_C: 20.0}\n", encoding="utf-8")
    (die_dir / "opt2.yaml").write_text("duties: []\n", encoding="utf-8")
    monkeypatch.setattr(family, "_iter_die_entries", lambda: [{"name": "die", "dir": die_dir, "layer": "shared"}])
    monkeypatch.setattr(family, "_die_dir", lambda die: die_dir)
    monkeypatch.setattr(family, "_cfg_file", lambda die, config: die_dir / f"{config}.yaml")
    monkeypatch.setattr(family, "_require_die_access", lambda *a, **k: None)
    monkeypatch.setattr(motors, "_identity", lambda request: IDENTITIES[request.headers.get("x-user")])
    monkeypatch.setattr(duty_results, "get", lambda die, cfg: {})
    MTS.mutate("die", "opt1", lambda d: d["tests"].append(
        {"id": "test_a", "kind": "parameters", "date": "2026-10-26", "conditions": {},
         "resistance": {"temp_c": 20.0, "pairs": [{"pair": "1-2", "value_mohm": 17.0}]}}))
    app = FastAPI()
    app.include_router(MTR.project_router)
    app.include_router(motors.router)
    return TestClient(app), store


def _get(client, who, q="?die=die&config=opt1"):
    return client.get("/api/projects/alpha/motors/tests" + q, headers={"x-user": who})


def test_project_view_engineers_and_owner_always_customer_only_behind_the_flag(project):
    client, store = project
    for who in ("engineer", "owner"):
        r = _get(client, who)
        assert r.status_code == 200 and r.json()["can"] == {"add": False, "edit": False, "settings": False}
        row = r.json()["compare"]["test_a"]["groups"][0]["rows"][0]
        assert row["model"] == pytest.approx(16.0)                 # 2 x 8 mOhm at 20 C, read from the yaml summary
    assert _get(client, "customer").status_code == 404
    assert _get(client, "stranger").status_code == 404
    assert _get(client, "customer", "").json() == {"motors": []}
    MTS.mutate("die", "opt1", lambda d: d["settings"].update(tests_visible_to_customer=True))
    r = _get(client, "customer")
    assert r.status_code == 200 and [t["id"] for t in r.json()["tests"]] == ["test_a"]
    assert "documents" not in r.json()                              # production files stay in Production
    assert _get(client, "customer", "").json()["motors"] == [
        {"die": "die", "config": "opt1", "count": 1, "visible_to_customer": True}]
    audit = store._file("alpha", "audit.jsonl").read_text(encoding="utf-8")
    assert audit.count("motor.tests.read") == 1                      # the customer's read, not the engineers'
