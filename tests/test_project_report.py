"""The project variant report and the staff flags (owner 2026-10-26; synthetic data only).

  (a) ``PATCH /motors/report_reviewed`` and ``PATCH /motors/recommended`` are staff only (a global admin / engineer); a project
      engineer who is not staff, a customer and a stranger are refused; the recommended flag is exclusive.
  (b) ``GET /motors/report``: staff always; a project member (customer or project engineer) only for a variant marked reviewed,
      otherwise 403 ``report_not_reviewed``; a non-member gets 404; every non-staff download is audited.
  (c) the generic ``GET /api/family/report/{die}/{cfg}`` cannot be used to go round the review gate for a project motor.
  (d) ``report_missing.still_to_compute`` lists what a partly computed variant still needs and flags stale mechanics.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from motor_ai_sim import duty_results as dr
from motor_ai_sim import report_missing
from motor_ai_sim.development import access, motors, store

DIE, CFG, CFG2 = "TESTDIE 30", "T30", "T31"
IDENTITIES = {
    "admin": {"email": "admin@example.test", "role": "admin"},
    "engineer": {"email": "engineer@example.test", "role": "engineer"},
    "customer": {"email": "customer@example.test", "role": "customer"},
    "pengineer": {"email": "pe@example.test", "role": "customer"},      # project role engineer, NOT global staff
    "stranger": {"email": "stranger@example.test", "role": "customer"},
}
YAML = "duties:\n  - name: cruise\n    rpm: 3000\n    current_arms: 40\n    summary:\n      T_em_avg_Nm: 4.8\n      P_mech_W: 1500.0\n  - name: peak\n    rpm: 3500\n    current_arms: 60\n"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from motor_ai_sim import auth as auth_mod
    from motor_ai_sim import config as cfgmod
    from motor_ai_sim import workspace as ws
    from motor_ai_sim.routes import family
    from motor_ai_sim.routes import report as report_routes

    monkeypatch.setattr(cfgmod, "DEFAULT_CONFIG_PATH", str(tmp_path / "motor_config.yaml"), raising=True)
    for var in ("WORKSPACES_ROOT", "SHARED_ROOT", "PUBLISHED_ROOT"):
        monkeypatch.delenv(var, raising=False)
    ws._PROC_CACHE = None
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    store.create_project({"id": "alpha", "title": "Alpha", "members": [
        {"email": "customer@example.test", "role": "customer"}, {"email": "pe@example.test", "role": "engineer"}],
        "linked_motors": [{"die": DIE, "config": CFG}, {"die": DIE, "config": CFG2}]})
    store.save_index({"motors": {motors._key(DIE, c): {"project_id": "alpha"} for c in (CFG, CFG2)}})
    cat = tmp_path / "catalog"
    cat.mkdir()
    for name in (CFG, CFG2):
        (cat / f"{name}.yaml").write_text(YAML, encoding="utf-8")
    monkeypatch.setattr(access, "require_signed_in", lambda request: IDENTITIES[request.headers.get("x-user")])
    monkeypatch.setattr(motors, "_identity", lambda request: IDENTITIES[request.headers.get("x-user")])
    monkeypatch.setattr(family, "_iter_die_entries", lambda: [{"name": DIE, "dir": cat, "layer": "shared"}])
    monkeypatch.setattr(family, "_die_dir", lambda die: cat)
    monkeypatch.setattr(family, "_cfg_file", lambda die, config: cat / f"{config}.yaml")
    monkeypatch.setattr(family, "_require_die_access", lambda die, authorization, cfg=None, *a, **k: None)
    monkeypatch.setattr(motors, "configure_duties_for_motor", lambda *a, **k: set())
    # the generic route resolves the caller from the bearer header: "Bearer <who>"
    monkeypatch.setattr(auth_mod, "caller_identity", lambda authorization=None: {
        "id": IDENTITIES[(authorization or "").replace("Bearer ", "")]["email"],
        "is_admin": (authorization or "").endswith("admin"),
        "role": IDENTITIES[(authorization or "").replace("Bearer ", "")]["role"]})
    built = []

    def fake_build(die, cfg, fmt, authorization, **kw):
        built.append((die, cfg, fmt))
        return b"%PDF-fake", f"{die} {cfg} report.{fmt}", "rid"
    monkeypatch.setattr(report_routes, "build_for", fake_build)
    app = FastAPI()
    app.include_router(motors.router)
    app.include_router(report_routes.router)
    c = TestClient(app)
    c.built = built
    return c


def patch(client, who, what, config=CFG, value=True):
    return client.patch(f"/api/projects/alpha/motors/{what}", json={"die": DIE, "config": config, "value": value},
                        headers={"x-user": who})


def get_report(client, who, config=CFG, fmt="pdf"):
    return client.get("/api/projects/alpha/motors/report", params={"die": DIE, "config": config, "format": fmt},
                      headers={"x-user": who})


def audit_text():
    p = store.path_for("alpha") / "audit.jsonl"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ── (a) the staff flags ──────────────────────────────────────────────────────

@pytest.mark.parametrize("who,expected", [("admin", 200), ("engineer", 200), ("pengineer", 403), ("customer", 403), ("stranger", 404)])
@pytest.mark.parametrize("what", ["report_reviewed", "recommended"])
def test_a_only_staff_sets_the_flags(client, who, expected, what):
    assert patch(client, who, what).status_code == expected


def test_a_the_recommended_variant_is_exclusive_and_clearable(client):
    assert patch(client, "admin", "recommended", CFG).json()["recommended"]["by"] == "admin@example.test"
    assert patch(client, "admin", "recommended", CFG2).status_code == 200
    rows = {m["config"]: m for m in client.get("/api/projects/alpha/motors", headers={"x-user": "customer"}).json()["motors"]}
    assert rows[CFG]["recommended"] is None and rows[CFG2]["recommended"]["at"]            # one at a time
    patch(client, "admin", "recommended", CFG2, value=False)
    rows = {m["config"]: m for m in client.get("/api/projects/alpha/motors", headers={"x-user": "customer"}).json()["motors"]}
    assert rows[CFG2]["recommended"] is None                                                # default: none
    assert "motor.recommended" in audit_text()


def test_a_the_review_stamp_names_who_when_and_which_code(client):
    stamp = patch(client, "engineer", "report_reviewed").json()["report_reviewed"]
    assert stamp["by"] == "engineer@example.test" and stamp["at"] and stamp["report_commit"]
    rows = {m["config"]: m for m in client.get("/api/projects/alpha/motors", headers={"x-user": "customer"}).json()["motors"]}
    assert rows[CFG]["report_reviewed"]["by"] == "engineer@example.test" and rows[CFG2]["report_reviewed"] is None


# ── (b) the project report route ─────────────────────────────────────────────

def test_b_staff_always_build_a_member_only_after_review(client):
    assert get_report(client, "admin").status_code == 200                       # staff, not reviewed
    assert get_report(client, "engineer", fmt="docx").status_code == 200
    for who in ("customer", "pengineer"):
        r = get_report(client, who)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "report_not_reviewed", who
    assert client.built == [(DIE, CFG, "pdf"), (DIE, CFG, "docx")]              # nothing was built for the refused callers
    assert patch(client, "admin", "report_reviewed").status_code == 200
    for who in ("customer", "pengineer"):
        assert get_report(client, who).status_code == 200, who
    assert get_report(client, "customer", config=CFG2).status_code == 403          # another variant stays closed
    assert get_report(client, "stranger").status_code == 404                       # a non-member does not even learn it exists


def test_b_every_member_download_is_audited_staff_ones_are_not(client):
    patch(client, "admin", "report_reviewed")
    get_report(client, "admin")
    assert "motor.report.download" not in audit_text()
    get_report(client, "customer")
    text = audit_text()
    assert "motor.report.download" in text and "customer@example.test" in text and f"{DIE}/{CFG}" in text


def test_b_un_marking_closes_the_report_again(client):
    patch(client, "admin", "report_reviewed")
    assert get_report(client, "customer").status_code == 200
    patch(client, "admin", "report_reviewed", value=False)
    assert get_report(client, "customer").status_code == 403


# ── (c) no way round the gate ────────────────────────────────────────────────

def test_c_the_generic_report_route_keeps_the_review_gate_for_project_motors(client):
    url = f"/api/family/report/{DIE}/{CFG}"
    assert client.get(url, params={"format": "pdf"}, headers={"authorization": "Bearer customer"}).status_code == 403
    assert client.get(url, params={"format": "pdf"}, headers={"authorization": "Bearer admin"}).status_code == 200
    assert client.get(url, params={"format": "pdf"}, headers={"authorization": "Bearer engineer"}).status_code == 200
    patch(client, "admin", "report_reviewed")
    assert client.get(url, params={"format": "pdf"}, headers={"authorization": "Bearer customer"}).status_code == 200


def test_c_a_motor_outside_every_project_is_not_gated(client):
    store.save_index({"motors": {}})
    r = client.get(f"/api/family/report/{DIE}/{CFG}", params={"format": "pdf"}, headers={"authorization": "Bearer customer"})
    assert r.status_code == 200


# ── (d) what is still to compute ─────────────────────────────────────────────

@pytest.fixture()
def results(tmp_path, monkeypatch):
    from motor_ai_sim import config as cfgmod
    from motor_ai_sim import workspace as ws
    monkeypatch.setattr(cfgmod, "DEFAULT_CONFIG_PATH", str(tmp_path / "motor_config.yaml"), raising=True)
    ws._PROC_CACHE = None
    yield tmp_path
    ws._PROC_CACHE = None


CFG_DOC = {"duties": [{"name": "takeoff", "summary": {"T_em_avg_Nm": 5.0}}, {"name": "cruise", "summary": {"T_em_avg_Nm": 3.0}},
                      {"name": "peak"}]}


def test_d_a_variant_with_only_em_results_lists_everything_else(results):
    rows = report_missing.still_to_compute(DIE, CFG, CFG_DOC)
    items = {(r["duty"], r["item"]) for r in rows}
    assert ("takeoff", report_missing.ITEM_COUPLED) in items and ("cruise", report_missing.ITEM_S1) in items
    assert ("peak", report_missing.ITEM_EM) in items                       # no electromagnetic run at all for this duty
    assert (report_missing.MOTOR_LEVEL, report_missing.ITEM_MECH) in items
    assert all(r["reason"] == report_missing.REASON_MISSING for r in rows)
    assert report_missing.as_lines(rows)[0].startswith("takeoff: ")


def test_d_stale_mechanics_is_listed_not_printed_as_current(results):
    dr.record(DIE, CFG, "cruise", "rotor_stress", {"computed_at": "2026-10-24T08:00:00", "sf_min": 4.31, "solver": {}})   # a row from before the solver stamps existed
    dr.record(DIE, CFG, "takeoff", "coupled", {"computed_at": "2026-10-26T08:00:00", "coil_temp_c": 130.0})
    rows = report_missing.still_to_compute(DIE, CFG, CFG_DOC)
    mech = [r for r in rows if r["item"] == report_missing.ITEM_MECH]
    assert [r["reason"] for r in mech] == [report_missing.REASON_SUPERSEDED]
    assert not any(r["duty"] == "takeoff" and r["item"] == report_missing.ITEM_COUPLED for r in rows)   # the point result exists
    # a limit search is not the duty's own point result
    dr.record(DIE, CFG, "cruise", "coupled", {"computed_at": "2026-10-26T08:00:00", "coil_temp_c": 180.0, "solve_to": "continuous", "mode": "limited"})
    assert ("cruise", report_missing.ITEM_COUPLED) in {(r["duty"], r["item"]) for r in report_missing.still_to_compute(DIE, CFG, CFG_DOC)}
