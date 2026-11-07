"""Read-only Simulation view of a project motor's stored points (owner 2026-11-04; synthetic names only).

Pinned here:
  (a) WHO: any member of the project (customer, owner, project engineer) reads the points and one stored run; a stranger
      and a member of ANOTHER project get 404; a motor of another project is not reachable through this project.
  (b) WHAT: a test / validation point is not offered (and its run is a 404); build / assignment signatures, geometry blocks,
      fingerprints and per-element meshes never leave; stale is a flag, not a hash.
  (c) READ-ONLY: only GET exists on the two routes, nothing is solved or written by loading a point (the customer's
      read is audited once, on the points call).
  (d) THE SOLVER GATES: the tier table keeps every solve route behind a signed-in account and every optimisation / pipeline /
      static-3D / shared-config route behind the admin.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from motor_ai_sim.development import access, motors, sim_view, store

IDENTITIES = {
    "admin": {"email": "admin@example.test", "role": "admin"},
    "customer": {"email": "customer@example.test", "role": "user"},
    "owner": {"email": "owner@example.test", "role": "user"},
    "pengineer": {"email": "pe@example.test", "role": "user"},
    "stranger": {"email": "stranger@example.test", "role": "user"},
    "betamember": {"email": "beta@example.test", "role": "user"},
}

CFG_YAML = """
winding: {phases: 3}
duties:
  - name: rated
    mode: motor
    rpm: 3000
    current_arms: 40
    gamma_deg: 10
    torque_nm: 5.0
    power_kw: 1.5
    result: {efficiency_pct: 91.5, v_ll_peak_v: 48.0, build_sig: OLDSIG, secret_note: x}
    runs:
      current:
        recorded_at: "2026-11-01T10:00:00"
        build_sig: CURSIG
        payload_file: runs/opt1/rated.current.json.gz
        settings: {sim.stepsPP: 48, sim.vBus: 800}
        summary: {T_em_avg_Nm: 4.8, rpm: 3000, _geoSig: GEOHASH, geo_fingerprint: FP, geometry: {stator_diameter: 90}}
      pwm_voltage:
        recorded_at: "2026-11-01T11:00:00"
        build_sig: OLDSIG
        payload_file: runs/opt1/rated.pwm_voltage.json.gz
        settings: {sim.stepsPP: 96, sim.fSwitch: 16000}
        summary: {T_em_avg_Nm: 4.7}
  - name: peak
    rpm: 3500
    current_arms: 60
  - name: noload_3021
    rpm: 3000
    current_arms: 0.1
    kind: test
    runs:
      current: {recorded_at: "2026-11-01T10:00:00", payload_file: runs/opt1/noload.current.json.gz, summary: {a: 1}}
"""
PAYLOAD = {
    "time_s": [0.0, 0.001, 0.002], "T_em_Nm": [4.7, 4.8, 4.9], "I_A": [1.0, 2.0, 3.0],
    "_geoSig": "GEOHASH", "_matSig": "MATHASH", "geo_fingerprint": "FP", "stale_geometry": False,
    "demag_field": {"vertices": [[0, 0]], "triangles": [[0, 0, 0]]}, "frames": [1, 2],
    "summary": {"T_em_avg_Nm": 4.8, "_geoSig": "GEOHASH", "end3d": {"k_flux": 0.95}},
    "pwm": {"v_bus_V": 800.0, "wave_A": {"t_s": [0.0, 1.0], "v": [0.0, 1.0]}},
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from motor_ai_sim import duty_results
    from motor_ai_sim.routes import family
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    store.create_project({"id": "alpha", "members": [
        {"email": "customer@example.test", "role": "customer"},
        {"email": "owner@example.test", "role": "owner"},
        {"email": "pe@example.test", "role": "engineer"}],
        "linked_motors": [{"die": "die", "config": "opt1"}, {"die": "die", "config": "opt3", "status": "trial"}]})
    store.create_project({"id": "beta", "members": [{"email": "beta@example.test", "role": "customer"}],
                          "linked_motors": [{"die": "die", "config": "other"}]})
    store.save_index({"motors": {motors._key("die", "opt1"): {"project_id": "alpha"},
                                 motors._key("die", "opt3"): {"project_id": "alpha"},
                                 motors._key("die", "other"): {"project_id": "beta"}}})
    monkeypatch.setattr(access, "require_signed_in",
                        lambda request: IDENTITIES[request.headers.get("x-user")])
    die_dir = tmp_path / "catalog"
    die_dir.mkdir()
    for name in ("opt1", "opt3", "other"):
        (die_dir / f"{name}.yaml").write_text(CFG_YAML, encoding="utf-8")
    (die_dir / "die.yaml").write_text("geometry: {num_slots: 12, num_poles: 10}\n", encoding="utf-8")
    monkeypatch.setattr(family, "_iter_die_entries", lambda: [{"name": "die", "dir": die_dir, "layer": "shared"}])
    monkeypatch.setattr(family, "_die_dir", lambda die: die_dir)
    monkeypatch.setattr(family, "_die_file", lambda die: die_dir / "die.yaml")
    monkeypatch.setattr(family, "_cfg_file", lambda die, config: die_dir / f"{config}.yaml")
    monkeypatch.setattr(family, "_build_sig", lambda die_doc, cfg_doc: "CURSIG")
    monkeypatch.setattr(family, "_read_run_payload", lambda die, rel: json.loads(json.dumps(PAYLOAD)))
    monkeypatch.setattr(family, "_summary_with_full_mass", lambda summary, die, cfg: summary)
    monkeypatch.setattr(family, "_require_die_access", lambda die, authorization, cfg=None, *a, **k: None)
    monkeypatch.setattr(duty_results, "get", lambda die, cfg: {})
    monkeypatch.setattr(motors, "_identity", lambda request: IDENTITIES[request.headers.get("x-user")])
    app = FastAPI()
    app.include_router(motors.router)
    c = TestClient(app)
    c.tmp = tmp_path
    return c


def sim(client, who, project="alpha", config="opt1", **extra):
    q = "&".join(f"{k}={v}" for k, v in extra.items())
    return client.get(f"/api/projects/{project}/motors/sim?die=die&config={config}" + (f"&{q}" if q else ""),
                      headers={"x-user": who})


def run(client, who, duty="rated", drive="current", project="alpha", config="opt1"):
    return client.get(f"/api/projects/{project}/motors/sim/run?die=die&config={config}&duty={duty}&drive={drive}",
                      headers={"x-user": who})


# ---- (a) who ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("who,expected", [("admin", 200), ("customer", 200), ("owner", 200), ("pengineer", 200),
                                          ("stranger", 404), ("betamember", 404)])
def test_points_access_matrix(client, who, expected):
    assert sim(client, who).status_code == expected
    assert run(client, who).status_code == expected


def test_member_of_one_project_cannot_read_a_motor_of_another(client):
    # beta's motor through beta (not a member of alpha's) and through alpha's route: both closed
    assert sim(client, "customer", project="beta", config="other").status_code == 404
    assert run(client, "customer", project="beta", config="other").status_code == 404
    assert sim(client, "customer", project="alpha", config="other").status_code == 404
    assert run(client, "customer", project="alpha", config="other").status_code == 404
    # ... and beta's own member reads it
    assert sim(client, "betamember", project="beta", config="other").status_code == 200


def test_no_grant_on_the_motor_is_a_404_even_for_a_member(client, monkeypatch):
    from motor_ai_sim.routes import family

    def deny(die, authorization, cfg=None, *a, **k):
        raise HTTPException(404, detail="not found")
    monkeypatch.setattr(family, "_require_die_access", deny)
    assert sim(client, "customer").status_code == 404
    assert run(client, "customer").status_code == 404


def test_trial_variant_needs_show_all_like_the_project_list(client):
    assert sim(client, "customer", config="opt3").status_code == 404
    assert sim(client, "customer", config="opt3", show_all="true").status_code == 200


# ---- (b) what --------------------------------------------------------------------------------------------------

def test_points_are_shaped_like_a_tree_configuration_without_tests_or_signatures(client):
    body = sim(client, "customer").json()
    assert body["die"] == "die" and body["config"] == "opt1" and body["phases"] == 3
    assert body["default_duty"] == "rated"
    assert [d["name"] for d in body["duties"]] == ["rated", "peak"]          # the test point is not offered
    rated = body["duties"][0]
    assert rated["primary_drive"] == "current"
    assert [(r["drive"], r["stale"], r["steps"], r["f_switch_hz"]) for r in rated["runs"]] == [
        ("current", False, 48.0, None), ("pwm_voltage", True, 96.0, 16000.0)]
    assert rated["stale"] is True and rated["result"] == {"efficiency_pct": 91.5, "v_ll_peak_v": 48.0}
    text = json.dumps(body)
    for leak in ("CURSIG", "OLDSIG", "secret_note", "noload_3021", "sim.vBus", "geometry"):
        assert leak not in text, leak


def test_run_returns_summary_and_waveforms_scrubbed(client):
    body = run(client, "customer").json()
    assert body["duty"] == "rated" and body["drive"] == "current" and body["stale"] is False
    assert body["summary"] == {"T_em_avg_Nm": 4.8, "rpm": 3000}
    p = body["payload"]
    assert p["time_s"] == [0.0, 0.001, 0.002] and p["T_em_Nm"] == [4.7, 4.8, 4.9]
    assert p["summary"] == {"T_em_avg_Nm": 4.8, "end3d": {"k_flux": 0.95}}
    assert p["pwm"]["wave_A"]["v"] == [0.0, 1.0]
    text = json.dumps(body)
    for leak in ("GEOHASH", "MATHASH", "FP", "demag_field", "vertices", "triangles", "frames", "stator_diameter"):
        assert leak not in text, leak
    assert run(client, "customer", drive="pwm_voltage").json()["stale"] is True


def test_test_point_and_missing_runs_are_404s(client):
    assert run(client, "customer", duty="noload_3021").status_code == 404
    assert run(client, "customer", duty="peak").status_code == 404           # no stored run
    assert run(client, "customer", duty="nope").status_code == 404
    assert run(client, "customer", drive="voltage").status_code == 404       # known excitation, not stored
    assert run(client, "customer", drive="rocket").status_code == 422        # unknown excitation


def test_scrub_is_recursive_and_keeps_plain_numbers():
    out = sim_view.scrub({"a": [1.0, 2.0], "b": {"_geoSig": "x", "keep": 1, "deep": [{"build_sig": "y", "n": 2}]},
                          "thermal_path": {"x": 1}, "mesh": {"vertices": [], "triangles": []}})
    assert out == {"a": [1.0, 2.0], "b": {"keep": 1, "deep": [{"n": 2}]}, "mesh": None}


# ---- (c) read-only ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/sim", "/sim/run"])
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_only_get_exists_on_the_view_routes(client, path, method):
    r = getattr(client, method)("/api/projects/alpha/motors" + path + "?die=die&config=opt1&duty=rated&drive=current",
                                headers={"x-user": "admin"})
    assert r.status_code == 405


def _snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_loading_a_point_changes_nothing_and_starts_no_solve(client, monkeypatch):
    # every entry that could solve or write the shared catalog blows up if the view routes touch it
    from motor_ai_sim.modules.kernel import Kernel
    from motor_ai_sim.routes import family

    def boom(*a, **k):
        raise AssertionError("the read-only view must not solve or write")
    monkeypatch.setattr(Kernel, "run", boom)
    for name in ("_save_yaml", "activate", "_store_run", "record_duty_run"):
        if hasattr(family, name):
            monkeypatch.setattr(family, name, boom)
    before = _snapshot(client.tmp)
    for who in ("customer", "owner", "pengineer"):
        assert run(client, who).status_code == 200
        assert run(client, who, drive="pwm_voltage").status_code == 200
    assert _snapshot(client.tmp) == before


def test_customer_open_is_audited_once_and_engineer_open_is_not(client):
    sim(client, "pengineer")
    audit = store.path_for("alpha") / "audit.jsonl"
    assert not audit.exists()
    run(client, "customer")
    assert not audit.exists()                                                # a point load writes nothing
    sim(client, "customer")
    rows = [json.loads(x) for x in audit.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert [r["action"] for r in rows] == ["motor.sim.read"]


def test_answers_are_not_cached(client):
    assert sim(client, "customer").headers["cache-control"] == "no-store"
    assert run(client, "customer").headers["cache-control"] == "no-store"


# ---- (d) the solver gates --------------------------------------------------------------------------------------

#: (method, path) -> the tier the RoleGateMiddleware asks for.  "user" = any signed-in account; see the PR text: that tier
#: is what the existing solve routes have always had, and this PR does not widen or narrow it.
SOLVER_TIERS = {
    ("GET", "/api/simulation/physics/fem_transient"): "user",
    ("GET", "/api/simulation/physics/fem_field2d"): "user",
    ("GET", "/api/simulation/physics/harm_screening"): "user",
    ("POST", "/api/simulation/cogging/run"): "user",
    ("POST", "/api/kernel/run"): "user",
    ("POST", "/api/kernel/study"): "user",
    ("GET", "/api/mechanical/rotor_stress"): "user",
    ("GET", "/api/mechanical/modes"): "user",
    ("GET", "/api/mechanical/critical_speeds"): "user",
    ("POST", "/api/mechanical/limit_speed"): "user",
    ("GET", "/api/thermal/field"): "user",
    ("GET", "/api/thermal/coupled"): "user",
    ("POST", "/api/thermal/duty_cycle"): "user",
    ("POST", "/api/coupled/run"): "user",
    ("POST", "/api/coupled/continuous_rating"): "user",
    ("POST", "/api/coupled/constants_20c"): "user",
    ("POST", "/api/simulation/run"): "admin",
    ("POST", "/api/optimization/run"): "admin",
    ("POST", "/api/optimization/scan"): "admin",
    ("POST", "/api/optimization/doe/start"): "admin",
    ("POST", "/api/optimization/descent/start"): "admin",
    ("POST", "/api/optimization/auto"): "admin",
    ("POST", "/api/pipeline/generate"): "admin",
    ("POST", "/api/static3d/solve"): "admin",
    ("PATCH", "/api/simulation/config"): "admin",
}


@pytest.mark.parametrize("route,tier", sorted(SOLVER_TIERS.items()))
def test_solver_routes_are_never_open_to_an_anonymous_caller(route, tier):
    from motor_ai_sim import auth
    assert auth.required_role(*route) == tier


def test_the_view_routes_are_not_solver_routes():
    from motor_ai_sim import auth
    # they are plain reads behind the project gate, not in the tier table
    assert auth.required_role("GET", "/api/projects/alpha/motors/sim") is None
    assert auth.required_role("GET", "/api/projects/alpha/motors/sim/run") is None
