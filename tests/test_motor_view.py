"""One display system for motors: the read-only view of stored results (owner 2026-10-26; synthetic data only).

Pinned here, in order:

  (a) RECORD PATHS - every solve seam files its answer under die / configuration / duty with a solver stamp:
      the electromagnetic kernel run (``note_em_run`` + the ``em`` field), the thermal tail (field, coupled, duty
      cycle), the mechanical tail (rotor stress, modes, critical speeds); a ``record: false`` errand files nothing.
  (b) THE VIEW - ``motor_view.build_view`` shows the thermal block (temperatures, S1, S2) and the mechanical block
      (SF per part, retention, displacement, proof speed) with provenance, and decides full / points / empty.
  (c) FIELD PAYLOADS - the stored maps come back shaped like the tab payloads the adapters draw.
  (d) ROUTES - the project routes (``/motors/view`` and ``/motors/field``) keep the project-list access matrix; the
      generic ``/api/motor-view`` routes answer the same data for a motor loaded from the Motors tab.

No solver runs: the seams are fed small synthetic results, exactly what the solve routes hand them.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from motor_ai_sim import duty_fields as df
from motor_ai_sim import duty_results as dr
from motor_ai_sim.development import access, motor_view, motors, store

DIE, CFG, DUTY, DUTY2 = "TESTDIE 30", "T30", "rated", "peak"


# ---------------------------------------------------------------------------
# isolation: a throwaway workspace (duty_results) and catalogue root (duty_fields)
# ---------------------------------------------------------------------------

@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    from motor_ai_sim import config as cfgmod
    from motor_ai_sim import workspace as ws
    from motor_ai_sim.routes import family
    from motor_ai_sim.routes import mechanical as me
    from motor_ai_sim.routes import thermal as th

    monkeypatch.setattr(cfgmod, "DEFAULT_CONFIG_PATH", str(tmp_path / "motor_config.yaml"), raising=True)
    for var in ("WORKSPACES_ROOT", "SHARED_ROOT", "PUBLISHED_ROOT"):
        monkeypatch.delenv(var, raising=False)
    ws._PROC_CACHE = None
    dies = tmp_path / "dies"
    dies.mkdir()
    monkeypatch.setattr(family, "_DIES_DIR", dies)
    # no machine-level pickles, no lazy loads from the user's disk
    monkeypatch.setattr(th, "_persist_last", lambda: None)
    monkeypatch.setattr(me, "_persist_last", lambda: None)
    monkeypatch.setattr(th, "_load_last", lambda: None)
    monkeypatch.setattr(me, "_load_last", lambda: None)
    th._LAST.clear()
    me._LAST.clear()
    ctx = (DIE, CFG, DUTY)
    monkeypatch.setattr(dr, "active_context", lambda: ctx)
    monkeypatch.setattr(df, "active_context", lambda: ctx)
    assert dr.store_path() == tmp_path / ".duty_results.json"
    yield tmp_path
    th._LAST.clear()
    me._LAST.clear()
    ws._PROC_CACHE = None


# a 4-vertex, 2-triangle "mesh": enough for every packer, no solver
VERTS_MM = [[0.0, 0.0], [20.0, 0.0], [20.0, 20.0], [0.0, 20.0]]
TRIS = [[0, 1, 2], [0, 2, 3]]


def mech_result() -> dict:
    return {
        "primary_case": "proof", "rpm": 23000.0, "overspeed_factor": 1.2, "interference_mm": 0.05,
        "mesh": {"n_triangles": 2, "element_order": 2, "mesh_size_mm": 1.5},
        "cases": {"proof": {
            "rpm": 23000.0, "sf_min": 3.4, "sf_min_part": "sleeve", "sf_min_p05": 3.6,
            "max_displacement_um": 12.5, "rotor_od_growth_um": 9.1,
            "retention": {"status": "pass", "message": "retained by the iron"},
            "parts": {"sleeve": {"material": "GFRP", "safety_factor": 3.4, "von_mises_max_mpa": 700.0,
                                 "strength_mpa": 2400.0},
                      "rotor_core": {"material": "steel", "safety_factor": 5.2, "von_mises_max_mpa": 300.0}},
        }},
        "field": {"vertices": VERTS_MM, "triangles": TRIS, "domain_per_tri": [1, 2],
                  "part_names": {"1": "rotor_core", "2": "sleeve"},
                  "cases": {"proof": {"vm_per_tri": [100.0, 200.0], "s_p1_per_tri": [90.0, 150.0],
                                      "sf_per_tri": [5.0, 3.4], "u_per_node": [[0, 0], [1, 0], [1, 1], [0, 1]],
                                      "u_mag_per_node": [0.0, 1.0, 1.4, 1.0]}}},
    }


def thermal_result() -> dict:
    return {
        "vertices": [[x / 1000.0, y / 1000.0] for x, y in VERTS_MM], "triangles": TRIS,
        "domain_per_tri": [1, 2], "temperature_per_node": [60.0, 80.0, 120.0, 90.0],
        "heat_flux_per_tri": [[1000.0, 0.0], [0.0, 2000.0]], "part_names": {"1": "stator", "2": "winding"},
        "cooling": {"mount": {"t_housing_mean_c": 64.0}},
        "components": {"winding": {"avg": 110.0, "max": 120.0, "min": 80.0},
                       "magnet": {"avg": 70.0, "max": 75.0, "min": 65.0}},
        "T_max": 120.0, "T_min": 60.0, "P_loss_total_W": 321.0, "P_cu_W": 200.0, "P_fe_W": 100.0,
    }


def coupled_out() -> dict:
    return {"computed_at": "2026-10-26T10:00:00", "geometry_fingerprint": "fp1",
            "coupling": {"drive": "sine", "iterations": 3, "converged": True, "coil_temp_c": 118.0,
                         "magnet_temp_c": 72.0,
                         "continuous_rating": {"ok": True, "feasible": True, "I_cont_A_rms": 41.5,
                                               "limiting_part": "winding", "limits_c": {"winding": 180.0}},
                         "time_to_limit": {"within_limits": False, "limiting_part": "winding",
                                           "time_to_limit_s": 160.0, "limits_c": {"winding": 180.0}}}}


def em_run_result() -> dict:
    return {"computed_at": "2026-10-26T09:00:00", "geo_fingerprint": "fp1", "rpm": 3000.0,
            "summary": {"T_avg_Nm": 4.8, "P_loss_total_W": 140.0, "efficiency_pct": 91.5, "mode": "motor"}}


def em_snap() -> dict:
    # (2, n) mm vertices and (3, m) triangles: the solver's own orientation
    return {"field": {"P_mm": [[v[0] for v in VERTS_MM], [v[1] for v in VERTS_MM]],
                      "T": [[t[0] for t in TRIS], [t[1] for t in TRIS], [t[2] for t in TRIS]],
                      "tags": [3, 4], "Bx": [0.3, 0.4], "By": [0.4, 0.3], "loss_dens": [1.0e5, 2.0e5],
                      "A": [0.0, 0.1, 0.2, 0.3]},
            "scalars": {"demag_coef_per_tri": [1.0, 0.97], "rpm": 3000.0},
            "meta": {"computed_at": "2026-10-26T09:00:00", "eddy": True}}


def file_everything() -> None:
    """Drive the real tails with synthetic results - the same calls the solve routes make."""
    from motor_ai_sim.routes import mechanical as me
    from motor_ai_sim.routes import thermal as th
    dr.note_em_run(em_run_result())
    df.save_active("em", em_snap(), computed_at="2026-10-26T09:00:00")
    th._remember_last("field", thermal_result(), {"rpm": 3000.0, "mesh_size_mm": 2.0}, "fp1")
    dr.note_coupled(coupled_out())
    me._remember_last("rotor_stress", mech_result(), {"rpm": 23000.0, "mesh_size_mm": 1.5, "order": 2}, "fp1")


# ---------------------------------------------------------------------------
# (a) record paths
# ---------------------------------------------------------------------------

def test_a_every_seam_records_under_die_config_duty_with_a_solver_stamp(sandbox):
    file_everything()
    rows = dr.get(DIE, CFG)[DUTY]
    assert {"em_run", "thermal", "coupled", "rotor_stress"} <= set(rows)
    for kind in ("em_run", "thermal", "coupled", "rotor_stress"):
        assert rows[kind].get("solver", {}).get("code_version") or rows[kind].get("solver", {}).get("version"), kind
        assert rows[kind]["recorded_at"]
    assert rows["em_run"]["numbers"]["T_avg_Nm"] == 4.8
    assert rows["rotor_stress"]["sf_min"] == 3.4
    assert rows["thermal"]["components"]["winding"]["max"] == 120.0
    # the three field files exist and carry the stamp
    kinds = {r["kind"]: r for r in df.have(DIE, CFG)[DUTY]}
    assert set(kinds) == {"em", "thermal", "rotor_stress"}
    assert all(r["solver"] for r in kinds.values())
    assert kinds["rotor_stress"]["n_triangles"] == 2 and kinds["em"]["n_triangles"] == 2


def test_a_modes_and_critical_speeds_are_recorded_too(sandbox):
    from motor_ai_sim.routes import mechanical as me
    me._remember_last("critical_speeds", {"rated_rpm": 3000.0, "verdict": "ok",
                                          "critical_speeds": [{"mode": 1, "rpm": 9000.0}]}, {"rpm": 3000.0}, "fp1")
    me._remember_last("modes", {"modes": [{"f_hz": 1200.0}], "field": {"vertices": VERTS_MM, "triangles": TRIS,
                                                                       "modes": [[[0, 0], [1, 0], [1, 1], [0, 1]]]}},
                      {"rpm": 3000.0}, "fp1")
    rows = dr.get(DIE, CFG)[DUTY]
    assert rows["critical_speeds"]["solver"] and rows["modes"]["solver"]
    assert [r["kind"] for r in df.have(DIE, CFG)[DUTY]] == ["modes"]


def test_a_duty_cycle_is_filed_under_the_duty_it_names(sandbox):
    from motor_ai_sim.routes import thermal as th
    th._remember_last("duty_cycle", {"duty": DUTY2, "spec": {"kind": "S2", "cycle_s": 60.0},
                                     "cycle": {"t_s": [0, 1], "peak_c": {"winding": 150.0}}},
                      {"rpm": 3000.0}, "fp1", duty=DUTY2, die=DIE, cfg=CFG)
    assert dr.get(DIE, CFG)[DUTY2]["duty_cycle"]["solver"]
    assert DUTY not in dr.get(DIE, CFG)            # never under the editor's own duty


def test_a_a_record_false_errand_files_nothing(sandbox):
    from motor_ai_sim import run_recording as rr
    token = rr.suppress()
    try:
        file_everything()
    finally:
        rr.restore(token)
    assert dr.get(DIE, CFG) == {}
    assert df.have(DIE, CFG) == {}


def test_a_no_active_duty_files_nothing(sandbox, monkeypatch):
    monkeypatch.setattr(dr, "active_context", lambda: None)
    monkeypatch.setattr(df, "active_context", lambda: None)
    file_everything()
    assert dr.get(DIE, CFG) == {}


def test_a_the_kernel_run_tail_calls_the_em_recorders():
    """The transient route's live-machine tail files the field AND the numbers (static guard: no solve)."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim" / "routes" / "simulation.py").read_text(
        encoding="utf-8")
    tail = src[src.index("_ledger_write(_sb_key, _sbres, _sb_key_fields)"):]
    assert "note_em_run(_sbres)" in tail[:2500]
    assert '_df.save_active(\n                    "em"' in src


# ---------------------------------------------------------------------------
# (b) the view and the full / points / empty decision
# ---------------------------------------------------------------------------

def _rows(duties):
    return [{"duty": d, "has_result": True, "operating_point": {"rpm": 3000}, "electromagnetic": {"torque_nm": 4.8},
             "comparison": {"torque_nm": 4.8}} for d in duties]


def test_b_view_shows_thermal_and_mechanical_with_provenance(sandbox):
    file_everything()
    stored, fields = dr.get(DIE, CFG), df.have(DIE, CFG)
    view = motor_view.build_view(DIE, CFG, _rows([DUTY, DUTY2]), stored, fields, set())
    d = {x["duty"]: x for x in view["duties"]}
    th = d[DUTY]["thermal"]
    # the duty's temperatures come from the coupled loop (which says so), the map only supplies the housing
    assert th["duty_temps"]["kind"] == "coupled" and th["duty_temps"]["winding_c"] == 118.0
    assert th["duty_temps"]["magnet_c"] == 72.0 and th["duty_temps"]["housing_c"] == 64.0
    assert th["duty_temps"]["provenance"]["computed_at"]
    assert th["s1"]["I_cont_A_rms"] == 41.5 and th["s1"]["limiting_part"] == "winding"
    assert th["s2"]["time_to_limit_s"] == 160.0
    assert th["provenance"]["computed_at"] or th["provenance"]["recorded_at"]
    assert th["provenance"]["code_version"] or th["provenance"]["app_version"]
    mech = d[DUTY]["mechanical"]["rotor_stress"]
    assert mech["sf_min"] == 3.4 and mech["parts"]["sleeve"]["safety_factor"] == 3.4
    assert mech["retention"]["status"] == "pass" and mech["max_displacement_um"] == 12.5
    assert mech["proof_rpm"] == 23000.0
    assert mech["provenance"]["mesh_size_mm"] == 1.5 and mech["provenance"]["n_triangles"] == 2
    assert set(d[DUTY]["fields"]) == {"em", "thermal", "rotor_stress"}
    assert d[DUTY]["em_run"]["numbers"]["efficiency_pct"] == 91.5
    # a duty with nothing stored says so (None), never zeros; the motor's mechanical worst case shows on every point
    assert d[DUTY2]["thermal"] is None and d[DUTY2]["fields"] == {}
    assert d[DUTY2]["mechanical"]["rotor_stress"]["sf_min"] == 3.4
    assert d[DUTY2]["mechanical"]["rotor_stress"]["filed_under"] == DUTY
    assert d[DUTY2]["mechanical"]["rotor_stress"]["worst_case"]["rpm"] == 23000.0


def test_b_the_mode_decision(sandbox):
    assert motor_view.build_view(DIE, CFG, _rows([DUTY]), {}, {}, {DUTY})["mode"] == "full"
    assert motor_view.build_view(DIE, CFG, _rows([DUTY]), {}, {}, set())["mode"] == "points"
    empty = [{"duty": DUTY, "has_result": False, "operating_point": {}, "electromagnetic": {}, "comparison": {}}]
    assert motor_view.build_view(DIE, CFG, empty, {}, {}, set())["mode"] == "empty"
    # thermal/mechanical rows alone are points too (no yaml result needed)
    file_everything()
    only_stored = [{"duty": DUTY, "has_result": False, "operating_point": {}, "electromagnetic": {}, "comparison": {}}]
    assert motor_view.build_view(DIE, CFG, only_stored, dr.get(DIE, CFG), df.have(DIE, CFG), set())["mode"] == "points"


def test_b_plain_drops_anything_that_is_not_data():
    out = motor_view.plain({"a": 1, "nan": float("nan"), "obj": object(), "deep": {"x": {"y": {"z": {"w": 1}}}},
                            "s": "x" * 500})
    assert out["a"] == 1 and "nan" not in out and "obj" not in out and len(out["s"]) == 300
    assert "w" not in str(out)


# ---------------------------------------------------------------------------
# (c) field payloads
# ---------------------------------------------------------------------------

def test_c_field_payloads_match_the_tab_payload_shapes(sandbox):
    file_everything()
    em = motor_view.field_payload(DIE, CFG, DUTY, "em")["payload"]
    assert em["views"] == ["Bmag", "Loss", "Demag", "Az"]
    assert len(em["vertices"]) == 4 and em["vertices"][1][0] == pytest.approx(0.02)       # metres, (n, 2)
    assert len(em["triangles"]) == 2 and em["domain_per_tri"] == [3, 4]
    assert em["Bmag_per_tri"] == pytest.approx([0.5, 0.5], rel=1e-4)
    assert em["extent"][1] == pytest.approx(0.02)
    th = motor_view.field_payload(DIE, CFG, DUTY, "thermal")
    assert th["payload"]["views"] == ["temp", "flux"]
    assert th["payload"]["flux_mag_per_tri"] == pytest.approx([1000.0, 2000.0], rel=1e-3)
    assert th["payload"]["T_max"] == pytest.approx(120.0)
    assert th["provenance"]["code_version"] or th["provenance"]["app_version"]
    mech = motor_view.field_payload(DIE, CFG, DUTY, "rotor_stress")["payload"]
    assert mech["views"] == ["disp", "vm", "s1", "sf"] and mech["case"] == "proof"
    assert mech["cases"]["proof"]["sf_per_tri"] == pytest.approx([5.0, 3.4], rel=1e-4)


def test_c_missing_kinds_are_none_not_empty(sandbox):
    assert motor_view.field_payload(DIE, CFG, DUTY, "em") is None
    assert motor_view.field_payload(DIE, CFG, DUTY, "bogus") is None
    assert motor_view.field_kind("mech") == "rotor_stress" and motor_view.field_kind("Electromagnetic") == "em"
    assert motor_view.field_kind("nope") is None


# ---------------------------------------------------------------------------
# (d) routes
# ---------------------------------------------------------------------------

IDENTITIES = {
    "admin": {"email": "admin@example.test", "role": "admin"},
    "engineer": {"email": "engineer@example.test", "role": "engineer"},
    "customer": {"email": "customer@example.test", "role": "customer"},
    "stranger": {"email": "stranger@example.test", "role": "customer"},
}
DUTY_YAML = f"""
duties:
  - name: {DUTY}
    rpm: 3000
    current_arms: 40
    summary:
      T_em_avg_Nm: 4.8
      P_mech_W: 1500.0
      efficiency_pct: 91.5
  - name: {DUTY2}
    rpm: 3500
    current_arms: 60
"""


@pytest.fixture()
def client(sandbox, monkeypatch):
    from motor_ai_sim.routes import family
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(sandbox / "projects"))
    (sandbox / "projects").mkdir()
    store.create_project({"id": "alpha", "members": [{"email": "customer@example.test", "role": "customer"}],
                          "linked_motors": [{"die": DIE, "config": CFG}, {"die": DIE, "config": "nogrant"},
                                            {"die": DIE, "config": "trial1", "status": "trial"}]})
    store.save_index({"motors": {motors._key(DIE, c): {"project_id": "alpha"} for c in (CFG, "nogrant", "trial1")}})
    monkeypatch.setattr(access, "require_signed_in", lambda request: IDENTITIES[request.headers.get("x-user")])
    cat = sandbox / "catalog"
    cat.mkdir()
    for name in (CFG, "nogrant", "trial1"):
        (cat / f"{name}.yaml").write_text(DUTY_YAML, encoding="utf-8")
    monkeypatch.setattr(family, "_iter_die_entries", lambda: [{"name": DIE, "dir": cat, "layer": "shared"}])
    monkeypatch.setattr(family, "_die_dir", lambda die: cat)
    monkeypatch.setattr(family, "_cfg_file", lambda die, config: cat / f"{config}.yaml")

    def require(die, authorization, cfg=None, *args, **kwargs):
        if cfg == "nogrant" or die != DIE:
            raise HTTPException(404, detail="not found")
    monkeypatch.setattr(family, "_require_die_access", require)
    monkeypatch.setattr(motors, "_identity", lambda request: IDENTITIES[request.headers.get("x-user")])
    # no passport anywhere in this fixture -> points-limited
    monkeypatch.setattr(motors, "configure_duties_for_motor", lambda *a, **k: set())
    from motor_ai_sim.routes import motor_view as mv_routes
    app = FastAPI()
    app.include_router(motors.router)
    app.include_router(mv_routes.router)
    file_everything()
    return TestClient(app)


def pget(client, who, path, **q):
    return client.get(f"/api/projects/alpha/motors/{path}", params=q, headers={"x-user": who})


@pytest.mark.parametrize("who,expected", [("admin", 200), ("engineer", 200), ("customer", 200), ("stranger", 404)])
def test_d_project_view_access_matrix(client, who, expected):
    assert pget(client, who, "view", die=DIE, config=CFG).status_code == expected


def test_d_project_view_points_limited_and_complete(client):
    body = pget(client, "customer", "view", die=DIE, config=CFG).json()
    assert body["mode"] == "points" and body["configure_available"] is False
    duty = next(d for d in body["duties"] if d["duty"] == DUTY)
    assert duty["thermal"] and duty["mechanical"] and set(duty["fields"]) == {"em", "thermal", "rotor_stress"}
    assert duty["electromagnetic"]["t_em_avg_nm"] == 4.8


def test_d_project_view_hides_unlinked_trial_and_ungranted(client):
    assert pget(client, "customer", "view", die=DIE, config="nogrant").status_code == 404
    assert pget(client, "customer", "view", die=DIE, config="missing").status_code == 404
    assert pget(client, "customer", "view", die=DIE, config="trial1").status_code == 404
    assert pget(client, "customer", "view", die=DIE, config="trial1", show_all="true").status_code == 200


@pytest.mark.parametrize("who,expected", [("admin", 200), ("customer", 200), ("stranger", 404)])
def test_d_project_field_access_matrix(client, who, expected):
    assert pget(client, who, "field", die=DIE, config=CFG, duty=DUTY, kind="thermal").status_code == expected


def test_d_project_field_missing_unknown_and_bad_kind(client):
    assert pget(client, "customer", "field", die=DIE, config=CFG, duty=DUTY2, kind="thermal").status_code == 404
    assert pget(client, "customer", "field", die=DIE, config=CFG, duty="nope", kind="thermal").status_code == 404
    assert pget(client, "customer", "field", die=DIE, config=CFG, duty=DUTY, kind="bogus").status_code == 404
    ok = pget(client, "customer", "field", die=DIE, config=CFG, duty=DUTY, kind="rotor_stress").json()
    assert ok["kind"] == "rotor_stress" and ok["payload"]["views"][0] == "disp"


def test_d_customer_read_is_audited_but_engineer_is_not(client):
    pget(client, "engineer", "view", die=DIE, config=CFG)
    assert not (store.path_for("alpha") / "audit.jsonl").exists()
    pget(client, "customer", "view", die=DIE, config=CFG)
    assert "motor.view.read" in (store.path_for("alpha") / "audit.jsonl").read_text(encoding="utf-8")


def test_d_motors_tab_route_gives_the_same_view_as_the_project_route(client):
    """Loading from the Motors tab (no project) = the same data: ONE view."""
    via_project = pget(client, "customer", "view", die=DIE, config=CFG).json()
    via_motor = client.get(f"/api/motor-view/{DIE}/{CFG}", headers={"x-user": "customer"}).json()
    assert via_motor == via_project
    f1 = pget(client, "customer", "field", die=DIE, config=CFG, duty=DUTY, kind="em").json()
    f2 = client.get(f"/api/motor-view/{DIE}/{CFG}/{DUTY}/field/em").json()
    assert f1 == f2


def test_d_motors_tab_route_gates_like_the_ordinary_motor_gate(client):
    assert client.get(f"/api/motor-view/{DIE}/nogrant").status_code == 404
    assert client.get(f"/api/motor-view/{DIE}/{CFG}/{DUTY2}/field/em").status_code == 404      # not computed
    # an unknown kind is "not computed" (404), never a 400; the tab-style names are understood
    assert client.get(f"/api/motor-view/{DIE}/{CFG}/{DUTY}/field/bogus").status_code == 404
    assert client.get(f"/api/motor-view/{DIE}/{CFG}/{DUTY}/field/mech").json()["kind"] == "rotor_stress"


# ---------------------------------------------------------------------------
# (e) selection rules (owner review 2026-10-26)
# ---------------------------------------------------------------------------

def _cp(coil, magnet, at, **extra):
    return {"computed_at": at, "coil_temp_c": coil, "magnet_temp_c": magnet, "converged": True, "drive": "sine", **extra}


def test_e_coupled_wins_over_the_plain_map_and_is_labelled():
    stored = {"thermal": {"computed_at": "2026-10-24T10:00:00", "components": {"winding": {"max": 185.9}}},
              "coupled": _cp(128.5, 70.0, "2026-10-25T10:00:00")}
    t = motor_view.thermal_block(stored)
    assert t["duty_temps"]["kind"] == "coupled"
    assert (t["duty_temps"]["winding_c"], t["duty_temps"]["magnet_c"]) == (128.5, 70.0)
    assert t["duty_temps"]["provenance"]["computed_at"] == "2026-10-25T10:00:00"
    assert "185.9" not in str(t)                         # the map of another run is not offered as the duty's temperature


def test_e_a_limit_search_state_is_never_the_duty_temperature():
    for extra in ({"mode": "limited"}, {"solve_to": "continuous"}, {"solve_to": "limits"}):
        stored = {"thermal": {"computed_at": "2026-10-25T09:00:00", "state": "limited",
                              "components": {"winding": {"max": 179.6}}},
                  "coupled": _cp(179.6, 90.0, "2026-10-25T10:00:00", **extra)}
        t = motor_view.thermal_block(stored)
        assert "duty_temps" not in t, extra
        assert t["limit_search"]["winding_c"] == 179.6 and t["limit_search"]["kind"] == "coupled"


def test_e_without_a_coupled_row_the_steady_map_stands_in_a_limited_map_does_not():
    steady = motor_view.thermal_block({"thermal": {"computed_at": "2026-10-25T09:00:00",
                                                   "components": {"winding": {"max": 120.0}, "magnet": {"max": 75.0}}}})
    assert steady["duty_temps"]["kind"] == "thermal" and steady["duty_temps"]["winding_c"] == 120.0
    limited = motor_view.thermal_block({"thermal": {"state": "limited", "components": {"winding": {"max": 200.0}}}})
    assert limited is None or "duty_temps" not in limited


def test_e_s1_and_s2_are_their_own_lines_with_provenance():
    stored = {"coupled": _cp(128.5, 70.0, "2026-10-25T10:00:00",
                             continuous_rating={"ok": True, "I_cont_A_rms": 30.0, "limiting_part": "winding"},
                             time_to_limit={"time_to_limit_s": 90.0, "limiting_part": "winding"})}
    t = motor_view.thermal_block(stored)
    assert t["s1"]["I_cont_A_rms"] == 30.0 and t["s1"]["provenance"]["computed_at"]
    assert t["s2"]["time_to_limit_s"] == 90.0 and t["duty_temps"]["winding_c"] == 128.5


def _rs(at, sf, fp2=None, rpm=23000.0, torque=7.5, solver=None):
    row = {"computed_at": at, "sf_min": sf, "rpm": rpm, "point": {"rpm": rpm, "torque_nm": torque, "loads": "both"}}
    if fp2:
        row["geometry_fingerprint_v2"] = fp2
    if solver:
        row["solver"] = solver
    return row


def test_e_the_newest_rotor_stress_is_the_motors_worst_case_on_every_point():
    stored = {"cruise": {"rotor_stress": _rs("2026-10-25T08:00:00", 2.20)},
              "peak": {"rotor_stress": _rs("2026-10-24T08:00:00", 4.31)}}
    w = motor_view.worst_case_rotor_stress(stored)
    assert w["sf_min"] == 2.20 and w["filed_under"] == "cruise" and w["records"] == 2
    assert w["worst_case"] == {"rpm": 23000.0, "torque_nm": 7.5, "loads": "both"}
    assert w["others"][0]["duty"] == "peak"
    view = motor_view.build_view(DIE, CFG, _rows(["cruise", "peak", "idle"]), stored, {}, set())
    assert [d["mechanical"]["rotor_stress"]["sf_min"] for d in view["duties"]] == [2.20, 2.20, 2.20]
    assert view["mechanical_worst_case"]["sf_min"] == 2.20


def test_e_records_before_the_contact_repair_are_flagged_superseded():
    old = motor_view.worst_case_rotor_stress({"cruise": {"rotor_stress": _rs("2026-10-24T08:00:00", 4.31)}})
    assert old["solver_status"] == "superseded" and old["stale"] is True
    after = motor_view.worst_case_rotor_stress({"cruise": {"rotor_stress": _rs("2026-10-26T08:00:00", 3.1)}})
    assert after["solver_status"] == "current" and after["stale"] is False
    stamped = motor_view.worst_case_rotor_stress({"c": {"rotor_stress": _rs("2026-10-20T08:00:00", 3.1, solver={"code_version": "abc"})}})
    assert stamped["solver_status"] == "current"                      # a solver stamp exists only on builds with the repair
    undated = motor_view.worst_case_rotor_stress({"c": {"rotor_stress": {"sf_min": 3.0}}})
    assert undated["solver_status"] == "unknown" and undated["stale"] is False


def test_e_a_record_of_another_geometry_is_stale():
    stored = {"peak": {"rotor_stress": _rs("2026-10-26T08:00:00", 4.31, fp2="aaaa"),
                       "coupled": {**_cp(128.5, 70.0, "2026-10-26T09:00:00"), "geometry_fingerprint_v2": "bbbb"}}}
    w = motor_view.worst_case_rotor_stress(stored)
    assert w["geometry_status"] == "differs" and w["stale"] is True
    stored["peak"]["coupled"]["geometry_fingerprint_v2"] = "aaaa"
    w = motor_view.worst_case_rotor_stress(stored)
    assert w["geometry_status"] == "match" and w["stale"] is False
    del stored["peak"]["coupled"]["geometry_fingerprint_v2"]
    assert motor_view.worst_case_rotor_stress(stored)["geometry_status"] == "unknown"


# ---------------------------------------------------------------------------
# (f) the S1 limit search has its own kind; nothing is silently replaced
# ---------------------------------------------------------------------------

def _coupled_out(coil, at, solve_to=None, mode=None, **blocks):
    c = {"drive": "sine", "iterations": 3, "converged": True, "coil_temp_c": coil, "magnet_temp_c": coil / 2, **blocks}
    if solve_to:
        c["solve_to"] = solve_to
    if mode:
        c["mode"] = mode
    return {"computed_at": at, "geometry_fingerprint": "fp1", "coupling": c}


S1 = {"ok": True, "I_cont_A_rms": 30.0, "limiting_part": "winding"}
TTL = {"time_to_limit_s": 90.0, "limiting_part": "winding"}


def test_f_a_limit_search_never_overwrites_the_point_result(sandbox):
    dr.note_coupled(_coupled_out(128.5, "2026-10-24T10:00:00"))                                    # the cruise point
    dr.note_coupled(_coupled_out(179.6, "2026-10-25T10:00:00", solve_to="continuous", mode="limited",
                                 continuous_rating=S1, time_to_limit=TTL))                         # the S1 search
    rows = dr.get(DIE, CFG)[DUTY]
    assert rows["coupled"]["coil_temp_c"] == 128.5 and rows["coupled_limit"]["coil_temp_c"] == 179.6
    assert rows["coupled_limit"]["solve_to"] == "continuous"
    # the rating and the time ride on the point record for the readers that only know `coupled` - temperatures untouched
    assert rows["coupled"]["continuous_rating"]["I_cont_A_rms"] == 30.0 and rows["coupled"]["time_to_limit"]["time_to_limit_s"] == 90.0
    assert rows["coupled"]["limit_blocks_from"]["kind"] == "coupled_limit"
    assert "coupled_limit" in dr.kinds_present(rows)
    view = motor_view.thermal_block(rows)
    assert view["duty_temps"]["winding_c"] == 128.5 and view["duty_temps"]["kind"] == "coupled"
    assert view["limit_search"]["winding_c"] == 179.6 and view["limit_search"]["kind"] == "coupled_limit"
    assert view["s1"]["I_cont_A_rms"] == 30.0 and view["s2"]["time_to_limit_s"] == 90.0


def test_f_a_limit_search_alone_is_mirrored_for_old_readers_and_a_point_run_then_replaces_only_the_mirror(sandbox):
    dr.note_coupled(_coupled_out(179.6, "2026-10-25T10:00:00", solve_to="limits", mode="limited"))
    rows = dr.get(DIE, CFG)[DUTY]
    assert rows["coupled"]["coil_temp_c"] == 179.6 and rows["coupled_limit"]["coil_temp_c"] == 179.6
    dr.note_coupled(_coupled_out(128.5, "2026-10-26T10:00:00"))
    rows = dr.get(DIE, CFG)[DUTY]
    assert rows["coupled"]["coil_temp_c"] == 128.5 and rows["coupled_limit"]["coil_temp_c"] == 179.6
    assert [h["coil_temp_c"] for h in dr.history(DIE, CFG, DUTY, "coupled")] == [179.6]       # the replaced row is kept


def test_f_old_stores_read_compatibly(sandbox):
    """An old `coupled` row that IS a limit search (no coupled_limit kind yet) reads as the limit search, not as a temperature."""
    rows = {"coupled": {"computed_at": "2026-10-25T10:00:00", "coil_temp_c": 179.6, "solve_to": "continuous", "mode": "limited"}}
    t = motor_view.thermal_block(rows)
    assert "duty_temps" not in t and t["limit_search"]["kind"] == "coupled" and t["limit_search"]["winding_c"] == 179.6


def test_f_record_keeps_a_short_history_of_every_replaced_result(sandbox):
    for n in range(7):
        dr.record(DIE, CFG, DUTY, "thermal", {"computed_at": f"2026-10-2{n}T10:00:00", "T_max": 100.0 + n})
    dr.record(DIE, CFG, DUTY, "thermal", {"computed_at": "2026-10-26T10:00:00", "T_max": 106.0})   # the same run filed again
    assert dr.get(DIE, CFG)[DUTY]["thermal"]["T_max"] == 106.0
    h = dr.history(DIE, CFG, DUTY, "thermal")
    assert [x["T_max"] for x in h] == [105.0, 104.0, 103.0, 102.0] and len(h) == dr.HISTORY_MAX
    # a pointer kind rebuilt on every save keeps none
    dr.record(DIE, CFG, DUTY, "em", {"saved_at": "a"})
    dr.record(DIE, CFG, DUTY, "em", {"saved_at": "b"})
    assert dr.history(DIE, CFG, DUTY, "em") == []


def test_f_history_follows_a_renamed_duty_and_goes_with_a_forgotten_one(sandbox):
    dr.record(DIE, CFG, DUTY, "thermal", {"computed_at": "2026-10-24T10:00:00", "T_max": 1.0})
    dr.record(DIE, CFG, DUTY, "thermal", {"computed_at": "2026-10-25T10:00:00", "T_max": 2.0})
    assert dr.rename(DIE, CFG, DUTY, "renamed")
    assert dr.history(DIE, CFG, "renamed", "thermal")[0]["T_max"] == 1.0 and dr.history(DIE, CFG, DUTY, "thermal") == []
    assert dr.forget(DIE, CFG, "renamed")
    assert dr.history(DIE, CFG, "renamed", "thermal") == []


def test_f_a_suppressed_limit_search_files_nothing(sandbox):
    from motor_ai_sim import run_recording as rr
    token = rr.suppress()
    try:
        dr.note_coupled(_coupled_out(179.6, "2026-10-25T10:00:00", solve_to="continuous", mode="limited"))
    finally:
        rr.restore(token)
    assert dr.get(DIE, CFG) == {}
