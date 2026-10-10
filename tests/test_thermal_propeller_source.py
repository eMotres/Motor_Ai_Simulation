"""Recovered focused tests for the Thermal propeller-source plumbing.

Recovered from the prior pytest node-id cache after an accidental replacement
of the owned test module. These tests use spies and pure functions only; they do
not launch FEM or contact a running API.
"""
from __future__ import annotations

import pytest


CONTEXT_KEY = "vadim@example.test|D85|L13|reference-85-l13"
PROP_SETTINGS = {
    "coolMode": "air",
    "airSpeedSource": "propeller",
    "propellerId": "tmotor_g32x11",
    "propellerPosition": "behind_hub",
    "propellerContextKey": CONTEXT_KEY,
}


def _context(monkeypatch, *, current=None, active=None, options=None):
    from motor_ai_sim.routes import account, family, propellers

    monkeypatch.setattr(account, "get_last_motor", lambda **_kw: current or {
        "user": "vadim@example.test",
        "selection": {"die": "D85", "config": "L13", "ref_id": "reference-85-l13"},
    })
    monkeypatch.setattr(family, "_read_ctx", lambda: active or {
        "die": "D85", "config": "L13",
    })
    monkeypatch.setattr(propellers, "get_cooling_options", lambda **_kw: options or {
        "cooling_options": ["robotics", "propeller_air"],
        "propellers": ["tmotor_g32x11"],
        "propeller_details": [{"id": "tmotor_g32x11", "selectable": True}],
    })


def test_propeller_context_guard_binds_account_motor_and_allowlist(monkeypatch):
    from motor_ai_sim.thermal_settings import propeller_cooling_context_issue

    _context(monkeypatch)
    assert propeller_cooling_context_issue(PROP_SETTINGS, "Bearer test") is None

    wrong_account = dict(PROP_SETTINGS, propellerContextKey=
                         "other@example.test|D85|L13|reference-85-l13")
    assert "different motor or account" in propeller_cooling_context_issue(
        wrong_account, "Bearer test")
    assert "this duty's die" in propeller_cooling_context_issue(
        PROP_SETTINGS, "Bearer test", expected_die="D40")
    assert "this duty's configuration" in propeller_cooling_context_issue(
        PROP_SETTINGS, "Bearer test", expected_config="L20")

    _context(monkeypatch, options={
        "cooling_options": ["robotics", "propeller_air"],
        "propellers": [], "propeller_details": [],
    })
    assert "not usable" in propeller_cooling_context_issue(PROP_SETTINGS, "Bearer test")


def test_propeller_context_guard_fails_closed_for_missing_account_ref_and_assignment(
        monkeypatch):
    from motor_ai_sim.thermal_settings import propeller_cooling_context_issue

    _context(monkeypatch, current=None)
    # An authenticated identity and current selection are required.
    from motor_ai_sim.routes import account
    monkeypatch.setattr(account, "get_last_motor", lambda **_kw: None)
    assert "sign in and load" in propeller_cooling_context_issue(PROP_SETTINGS)

    _context(monkeypatch, current={
        "user": "vadim@example.test",
        "selection": {"die": "D85", "config": "L13", "ref_id": ""},
    })
    assert "different motor or account" in propeller_cooling_context_issue(PROP_SETTINGS)

    no_id = dict(PROP_SETTINGS, propellerId="")
    assert "assigned propeller" in propeller_cooling_context_issue(no_id)

    # Manual airflow is independent of motor identity and never inherits the
    # selected propeller's identity/source checks.
    _context(monkeypatch, current=None, active=None, options={})
    assert propeller_cooling_context_issue({"airSpeedSource": "manual"}) is None


def test_propeller_context_guard_requires_active_family_to_match_current_motor(
        monkeypatch):
    from motor_ai_sim.thermal_settings import propeller_cooling_context_issue

    _context(monkeypatch, active={"die": "D40", "config": "L12"})
    assert "active motor family" in propeller_cooling_context_issue(PROP_SETTINGS)

    from motor_ai_sim.routes import family
    monkeypatch.setattr(family, "_read_ctx", lambda: None)
    assert "unavailable or released" in propeller_cooling_context_issue(PROP_SETTINGS)


def _cache_args():
    return dict(
        ambient_temp=25.0, h_conv=50.0, slot_k=0.0, rpm=3210.0,
        gamma_deg=0.0, I_phase_rms=8.0, n_steps_per_period=12,
        n_periods=2.0, mesh_size_mm=3.0, min_size_mm=0.3,
        outer_air_factor=1.3, n_sectors=4, coil_temp_c=100.0,
        component_mesh="", cooling_mode="air", air_speed_mps=0.0,
        fluid="water", fluid_temp_in_c=25.0, flow_lpm=0.0,
        air_speed_source="propeller", propeller_id="tmotor_g32x11",
        propeller_position="behind_hub",
    )


def test_propeller_id_separates_thermal_cache_and_duty_boundary_contract(monkeypatch):
    from motor_ai_sim.routes import thermal as th
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim import coupled_continuous_rating as ccr

    monkeypatch.setattr(sim, "_geometry_fingerprint", lambda _geo: "same-geo")
    monkeypatch.setattr(sim, "_config_physics_fingerprint", lambda **_kw: "same-config")
    monkeypatch.setattr(th, "_override_props", lambda: {})
    monkeypatch.setattr(th, "_thermal_k", lambda _cat, _name, default: default)
    monkeypatch.setattr(th, "_thermal_k_any", lambda _name, default: default)

    args = _cache_args()
    a = th._field_cache_key(None, {}, **args)
    b = th._field_cache_key(None, {}, **{**args, "propeller_id": "tmotor_g36x11_5"})
    assert a != b

    defaults = {
        "cooling_mode": "air", "ambient_temp": 27.0,
        "air_speed_mps": 0.0, "air_speed_source": "propeller",
        "propeller_id": "tmotor_g32x11", "propeller_position": "behind_hub",
        "bore_mode": "none",
    }
    got = ccr.Condition("assigned propeller", {}).merged(defaults)
    assert got == {k: v for k, v in defaults.items() if k != "air_speed_mps"}
    manual = ccr.Condition("manual airflow", {
        "air_speed_source": "manual", "air_speed_mps": 6.0,
    }).merged({**defaults, "air_speed_mps": 6.0})
    assert manual["air_speed_mps"] == 6.0
    assert "air_speed_source" not in manual and "propeller_id" not in manual


def test_thermal_settings_maps_propeller_source_without_manual_speed():
    from motor_ai_sim.thermal_settings import cooling_fields

    fields = cooling_fields({
        **PROP_SETTINGS, "ambientT": "27", "airSpeed": "99",
    })
    assert fields["cooling_mode"] == "air"
    assert fields["air_speed_source"] == "propeller"
    assert fields["propeller_id"] == "tmotor_g32x11"
    assert "air_speed_mps" not in fields


def test_route_reaches_solver_with_propeller_source_and_adopted_em_rpm(
        monkeypatch):
    """Both read routes forward source/id after adopting the saved EM RPM."""
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    from motor_ai_sim.routes import thermal as th
    from motor_ai_sim.routes import simulation as sim

    point = {
        "rpm": 3210.0, "gamma_deg": 0.0, "I_phase_rms": 8.0,
        "coil_temp_c": 100.0, "magnet_temp_c": 25.0,
        "n_steps_per_period": 12, "n_periods": 2.0,
        "mesh_size_mm": 3.0, "min_size_mm": 0.3,
        "outer_air_factor": 1.3, "n_sectors": 4,
        "component_mesh": "", "op_mode": "motor",
    }
    em = {"point": point, "run_id": "mock-3210", "key": ("mock",)}
    monkeypatch.setattr(th, "latest_em_run", lambda _geo: em)
    monkeypatch.setattr("motor_ai_sim.thermal_settings.propeller_cooling_context_issue",
                        lambda *_a, **_kw: None)
    monkeypatch.setattr(th, "_field_cache_key", lambda *_a, **_kw: ("spy",))
    monkeypatch.setattr(th, "_remember_last", lambda *_a, **_kw: None)
    monkeypatch.setattr(th, "_cache_put", lambda *_a, **_kw: None)
    monkeypatch.setattr(th, "_auto_coil_mesh", lambda component_mesh, *_a: component_mesh)
    monkeypatch.setattr(th, "_assignments", lambda: {})
    monkeypatch.setattr(th, "_validate_field_params",
                        lambda **kw: (kw["cooling_mode"], kw["bore_mode"]))
    monkeypatch.setattr(sim, "_parse_geo_override", lambda _geo: {})
    monkeypatch.setattr(sim, "_config_physics_fingerprint", lambda **_kw: "mock-config")
    monkeypatch.setattr(th._RH, "make_key", lambda *_a, **_kw: "mock-history-key")
    monkeypatch.setattr(th, "_thermal_field_summary", lambda *_a, **_kw: "mock-summary")
    class HistoryStub:
        def get(self, _key):
            return None
        def put(self, *_args, **_kwargs):
            return None
    monkeypatch.setattr(th, "_THERMAL_FIELD_HISTORY", HistoryStub())
    class ProgressStub:
        def start(self, **_kw):
            return None
        def finish(self):
            return None
        def callback(self):
            return None
    monkeypatch.setattr(th, "_progress", ProgressStub())
    monkeypatch.setattr(th, "_COUPLED_CACHE", {})

    calls = []
    def field_spy(**kw):
        calls.append(("field", kw))
        return {"ok": True, "T_max": 110.0, "T_min": 25.0,
                "components": {}, "geometry_fingerprint": "mock"}
    def coupled_spy(**kw):
        calls.append(("coupled", kw))
        return {"ok": True, "field": {"ok": True}, "geometry_fingerprint": "mock",
                "iterations": 1, "coil_temp_history_C": [110.0]}
    monkeypatch.setattr(th, "solve_thermal_field", field_spy)
    monkeypatch.setattr(th, "solve_coupled", coupled_spy)

    client = TestClient(app)
    query = {
        "air_speed_source": "propeller", "propeller_id": "tmotor_g32x11",
        "propeller_position": "behind_hub", "propeller_context_key": CONTEXT_KEY,
        "cooling_mode": "air", "em_source": "latest", "fresh": "true",
        "geo": "{}",
    }
    r_field = client.get("/api/thermal/field", params=query)
    assert r_field.status_code == 200, r_field.text[:800]
    r_coupled = client.get("/api/thermal/coupled", params={**query, "max_iter": 1})
    assert r_coupled.status_code == 200, r_coupled.text[:800]
    assert [kind for kind, _ in calls] == ["field", "coupled"]
    for _, kw in calls:
        assert kw["rpm"] == pytest.approx(3210.0)
        assert kw["air_speed_source"] == "propeller"
        assert kw["propeller_id"] == "tmotor_g32x11"
        assert kw["propeller_position"] == "behind_hub"


def test_coupled_iteration_forwards_propeller_to_each_field_pass(monkeypatch):
    from motor_ai_sim.routes import thermal as th

    seen = []
    def fake_field(**kwargs):
        seen.append(kwargs.copy())
        capture = kwargs.get("_em_capture")
        if capture is not None:
            capture["em"] = {"em_map": "mock"}
            capture["loss_source"] = {"kind": "mock", "run_id": "em-3210"}
        avg = 120.0 if len(seen) == 1 else 112.0
        return {"components": {"winding": {"avg": avg}}, "P_cu_W": 4.0,
                "loss_source": {"kind": "mock"}}

    monkeypatch.setattr(th, "solve_thermal_field", fake_field)
    monkeypatch.setattr(th, "_scaled_copper_map",
                        lambda em, **_kw: (em, {"rho_ratio": 1.0, "effective": 1.0,
                                                "p_cu_dc_ref_W": 4.0,
                                                "p_cu_ac_ref_W": 0.0}))
    th.solve_coupled(
        max_iter=3, coil_temp_c=100.0, ambient_temp=25.0,
        cooling_mode="air", air_speed_source="propeller",
        propeller_id="tmotor_g32x11", propeller_position="behind_hub",
        propeller_context_key=CONTEXT_KEY, rpm=3210.0,
    )
    assert len(seen) == 2
    for kwargs in seen:
        assert kwargs["air_speed_source"] == "propeller"
        assert kwargs["propeller_id"] == "tmotor_g32x11"
        assert kwargs["propeller_position"] == "behind_hub"
        assert kwargs["rpm"] == pytest.approx(3210.0)
