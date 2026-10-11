"""The coupled loop at a request rpm that is not the saved one (2026-10-11).

Before: ``routes.coupled._preflight`` refused any body ``rpm`` different from the
shared configuration's, because the thermal half looked its loss map up at the
configuration's speed.  Now one rpm reaches BOTH halves: the EM run solves at the
body's rpm, and the thermal half looks the loss map up at that rpm
(``loss_map_rpm``) while the gap and the propeller slipstream use the same rpm.
The saved configuration is not touched.  Fakes only — nothing here solves.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from motor_ai_sim.routes import coupled as cp
from motor_ai_sim.routes import simulation as sim
from motor_ai_sim.routes import thermal as th

BODY = {"n_steps_per_period": 4, "drive": "current", "eddy": True, "rotor_eddy": True}
CFG_RPM = 1000.0
REQ_RPM = 2345.0


@pytest.fixture()
def config_rpm(monkeypatch):
    """The shared configuration says 1000 rpm, whatever is on disk."""
    real = sim._effective_rpm
    monkeypatch.setattr(sim, "_effective_rpm", lambda rpm=None: CFG_RPM if rpm is None else real(rpm))


class _Stop(Exception):
    pass


def test_preflight_no_longer_refuses_another_speed(config_rpm):
    cp._preflight({**BODY, "rpm": REQ_RPM}, max_iter=6)          # must not raise
    cp._preflight({**BODY}, max_iter=6)


def test_loss_snapshot_probe_keys_on_the_given_rpm(config_rpm, monkeypatch):
    monkeypatch.setattr(sim, "_field_snap_key_fields",
                        lambda **kw: {"rpm": round(sim._effective_rpm(None), 3), "gamma_deg": kw["gamma_deg"]})
    monkeypatch.setattr(sim, "_config_physics_fingerprint", lambda **k: "fp")
    monkeypatch.setattr(sim, "_get_request_materials_safe", lambda: None)
    monkeypatch.setattr(sim, "_parse_component_mesh", lambda s: {})
    monkeypatch.setattr(th, "_snap_n_sectors", lambda n, g: n)
    monkeypatch.setattr(th, "_machine_gap_layers", lambda: 1.0)
    kw = dict(gamma_deg=5.0, I_phase_rms=10.0, mesh_size_mm=1.0, min_size_mm=0.3,
              outer_air_factor=1.2, n_sectors=1, coil_temp_c=100.0, component_mesh="",
              n_steps_per_period=12, n_periods=1.0, geo_ov=None)
    assert th._loss_snapshot_probe(**kw)["rpm"] == CFG_RPM               # the Thermal tab: unchanged
    assert th._loss_snapshot_probe(**kw, rpm=REQ_RPM)["rpm"] == REQ_RPM  # the coupled loop's speed


def test_em_loss_map_forwards_its_rpm_to_the_probe(monkeypatch):
    seen = {}

    def probe(**kw):
        seen.update(kw)
        raise _Stop()
    monkeypatch.setattr(th, "_loss_snapshot_probe", probe)
    with pytest.raises(HTTPException):
        th._em_loss_map(gamma_deg=0.0, I_phase_rms=10.0, n_steps_per_period=12, n_periods=1.0,
                        mesh_size_mm=1.0, min_size_mm=0.3, outer_air_factor=1.2, n_sectors=1,
                        coil_temp_c=100.0, component_mesh="", geo=None, geo_ov=None, rpm=REQ_RPM)
    assert seen["rpm"] == REQ_RPM


def test_solve_thermal_field_uses_one_rpm_for_air_and_loss_map(monkeypatch):
    seen = {}

    def air(src, pid, pos, *, rpm, ambient_temp, cooling_mode, air_speed_mps):
        seen["air_rpm"] = rpm
        return 7.5, {"air_speed_mps": 7.5, "rpm": rpm}

    def loss_map(**kw):
        seen["loss_map_rpm"] = kw.get("rpm")
        raise _Stop()
    monkeypatch.setattr(th, "_apply_air_speed_source", air)
    monkeypatch.setattr(th, "_validate_field_params", lambda **kw: (kw["cooling_mode"], kw["bore_mode"]))
    monkeypatch.setattr(th, "_auto_coil_mesh", lambda c, m, g: c)
    monkeypatch.setattr(th, "_assignments", lambda: {})
    monkeypatch.setattr(th, "_resolve_mech_losses", lambda g, rpm=None, bearing_temp_c=None: {"has_any": False})
    monkeypatch.setattr(th, "_em_loss_map", loss_map)
    with pytest.raises(_Stop):
        th.solve_thermal_field(rpm=REQ_RPM, loss_map_rpm=REQ_RPM, cooling_mode="air",
                               air_speed_source="propeller", propeller_id="tmotor_p13x4_4")
    assert seen == {"air_rpm": REQ_RPM, "loss_map_rpm": REQ_RPM}
    # the Thermal tab's own call: no loss-map speed, the lookup keeps the config's
    seen.clear()
    with pytest.raises(_Stop):
        th.solve_thermal_field(rpm=REQ_RPM, cooling_mode="air", air_speed_source="propeller",
                               propeller_id="tmotor_p13x4_4", mesh_size_mm=1.1)
    assert seen == {"air_rpm": REQ_RPM, "loss_map_rpm": None}


def test_coupled_thermal_half_gets_the_em_rpm(monkeypatch):
    seen = {}

    def fake(progress=None, **kw):
        seen.update(kw)
        return {"geometry_fingerprint": None}
    monkeypatch.setattr(th, "solve_thermal_field", fake)
    monkeypatch.setattr(th, "_remember_last", lambda *a, **k: None)
    monkeypatch.setattr(cp, "_mesh_kw", lambda body, keys: {k: 1 for k in keys})
    cooling = {"cooling_mode": "air", "air_speed_source": "propeller", "propeller_id": "tmotor_p13x4_4",
               "ambient_temp": 25.0}
    params: dict = {}
    cp._thermal_solve({**BODY, "rpm": REQ_RPM}, cooling, coil_temp_c=90.0, magnet_temp_c=70.0,
                      rpm=REQ_RPM, params_out=params)
    assert seen["rpm"] == REQ_RPM and seen["loss_map_rpm"] == REQ_RPM
    assert seen["air_speed_source"] == "propeller" and seen["propeller_id"] == "tmotor_p13x4_4"
    assert "loss_map_rpm" not in params          # not a Thermal-tab input field
    # a body without an rpm runs at the configuration's speed: the lookup is the old one
    seen.clear()
    cp._thermal_solve({**BODY}, cooling, coil_temp_c=90.0, magnet_temp_c=70.0, rpm=CFG_RPM)
    assert seen["rpm"] == CFG_RPM and "loss_map_rpm" not in seen


def test_answer_pass_replays_the_map_at_the_body_rpm(monkeypatch):
    seen = {}
    monkeypatch.setattr(cp, "_mesh_kw", lambda body, keys: {k: 1 for k in keys})
    monkeypatch.setattr(th, "coupled_pass_probe", lambda **kw: seen.setdefault("probe_rpm", kw.get("rpm")) and {})

    def loss_map(**kw):
        seen["map_rpm"] = kw.get("rpm")
        return {"loss_density_per_tri": [1.0], "n_triangles": 1}, {}
    monkeypatch.setattr(th, "_em_loss_map", loss_map)
    monkeypatch.setattr(th, "register_coupled_pass", lambda **kw: {"ok": True})
    rec = cp._register_answer_pass("answer pass", {**BODY, "rpm": REQ_RPM}, {"computed_at": "x"},
                                   coil_temp_c=90.0, magnet_temp_c=70.0)
    assert rec["registered"], rec
    assert seen == {"probe_rpm": REQ_RPM, "map_rpm": REQ_RPM}
