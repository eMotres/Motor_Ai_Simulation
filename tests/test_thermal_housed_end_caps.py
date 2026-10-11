"""The HOUSED frame's end caps in the propeller slipstream (2026-10-11).

A housed drone motor cooled by its own propeller has its two end caps in the
same stream as the housing side.  The thermal map cools them only when the
propeller IS the air source of a housed machine; a typed air speed, robotics,
and the open frame (which has its own wash paths) are unchanged.

Pinned here:
  (a) the film model — the housing side's own correlation at each face's speed
      (front = the housing air speed, rear = HOUSING_REAR_FACE_SPEED_FACTOR × it)
      on the cap annulus π/4·(D² − d_shaft²);
  (b) the cache key moves only for propeller air on a housed machine;
  (c) on the 30 mm fixture: the caps take real watts, the budget closes with
      them, the winding is colder than the same machine with the same typed
      speed, and every other mode reports the block as off with 0 W.
"""
from __future__ import annotations

import json
import math
import pathlib
import tempfile
import time

import pytest

from tests.test_thermal_routes import GEO_30MM, store_em_run

FAST = {
    "n_steps_per_period": 4, "n_periods": 1.0,
    "mesh_size_mm": 2.5, "min_size_mm": 0.6, "n_sectors": 2,
    "I_phase_rms": 60.0, "rpm": 3000.0, "gamma_deg": 0.0,
    "coil_temp_c": 120.0,
}
AMB = 30.0
V_PROP = 8.0
RUN_ID = "2026-10-11T09:00:00"


def _geo() -> str:
    return json.dumps(GEO_30MM)


# ---------------------------------------------------------------------------
# (a) the film model
# ---------------------------------------------------------------------------

def test_end_cap_film_is_the_housing_film_at_each_face_speed():
    from motor_ai_sim.simulation import cooling_models as cm

    D, d, L = 0.085, 0.012, 0.013
    r = cm.housing_end_faces_slipstream(air_speed_mps=V_PROP, t_ambient_c=AMB,
                                        d_housing_m=D, d_shaft_m=d, length_m=L)
    a = math.pi / 4.0 * (D * D - d * d)
    hf = cm.outer_air(air_speed_mps=V_PROP, t_ambient_c=AMB, d_housing_m=D)["h_conv"]
    hr = cm.outer_air(air_speed_mps=cm.HOUSING_REAR_FACE_SPEED_FACTOR * V_PROP,
                      t_ambient_c=AMB, d_housing_m=D)["h_conv"]
    assert r["area_per_face_m2"] == pytest.approx(a)
    assert r["front"]["h_conv"] == pytest.approx(hf)
    assert r["rear"]["h_conv"] == pytest.approx(hr)
    assert r["rear"]["air_speed_mps"] == pytest.approx(0.5 * V_PROP)
    assert hr < hf
    assert r["G_W_per_K"] == pytest.approx((hf + hr) * a)
    assert r["h_equiv_on_od"] * math.pi * D * L == pytest.approx(r["G_W_per_K"])


def test_end_caps_in_still_air_fall_to_the_natural_floor():
    from motor_ai_sim.simulation import cooling_models as cm

    r = cm.housing_end_faces_slipstream(air_speed_mps=0.0, t_ambient_c=AMB,
                                        d_housing_m=0.085, d_shaft_m=0.0,
                                        length_m=0.013)
    assert r["front"]["h_conv"] == pytest.approx(cm.NATURAL_CONVECTION_H)
    assert r["rear"]["h_conv"] == pytest.approx(cm.NATURAL_CONVECTION_H)


# ---------------------------------------------------------------------------
# (b) the cache key
# ---------------------------------------------------------------------------

_KEY_KW = dict(
    ambient_temp=AMB, h_conv=50.0, slot_k=0.0, rpm=3000.0, gamma_deg=0.0,
    I_phase_rms=60.0, n_steps_per_period=4, n_periods=1.0, mesh_size_mm=2.5,
    min_size_mm=0.6, outer_air_factor=1.3, n_sectors=2, coil_temp_c=120.0,
    component_mesh="", cooling_mode="air", air_speed_mps=V_PROP, fluid="water",
    fluid_temp_in_c=25.0, flow_lpm=0.0)


def _key(**kw):
    from motor_ai_sim.routes import thermal as th
    return th._field_cache_key(None, {}, **{**_KEY_KW, **kw})


def test_cache_key_marks_only_propeller_air_on_a_housed_machine():
    manual = _key()
    prop = _key(air_speed_source="propeller", propeller_id="p1")
    prop_open = _key(air_speed_source="propeller", propeller_id="p1", frame="open")
    assert "housing_end_faces" not in manual
    assert "housing_end_faces" in prop
    assert "housing_end_faces" not in prop_open


# ---------------------------------------------------------------------------
# (c) the solve
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def em_run():
    from motor_ai_sim.routes import simulation as sim

    mp = pytest.MonkeyPatch()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="thermal_end_caps_"))
    mp.setattr(sim, "_transient_field_store_path", lambda: str(tmp / ".snap.pkl"))
    saved = dict(sim._transient_field_snap)
    sim._transient_field_snap.clear()
    info = store_em_run(GEO_30MM, run_id=RUN_ID, phys=FAST)
    time.sleep(0.3)
    yield info
    sim._transient_field_snap.clear()
    sim._transient_field_snap.update(saved)
    mp.undo()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from motor_ai_sim import propeller as pp
    from motor_ai_sim.routes import thermal as th

    monkeypatch.setattr(th, "_LAST", {}, raising=True)
    monkeypatch.setattr(th, "_LAST_LOADED", True, raising=True)
    monkeypatch.setattr(th, "_last_store_path",
                        lambda: str(tmp_path / ".last_thermal.pkl"))
    monkeypatch.setattr(th, "_loss_maps_path",
                        lambda: str(tmp_path / ".loss_maps.pkl"))
    monkeypatch.setattr(th, "_LOSS_MAPS_LOADED", True, raising=True)
    # the propeller's slipstream, fixed: this module tests the thermal side
    monkeypatch.setattr(pp, "air_speed_for_thermal",
                        lambda *_a, **_k: {"air_speed_mps": V_PROP,
                                           "air_speed_ms": V_PROP})
    th._LOSS_MAPS.clear()
    th._FIELD_CACHE.clear()
    yield
    th._LOSS_MAPS.clear()
    th._FIELD_CACHE.clear()


def _solve(**over):
    from motor_ai_sim.routes import thermal as th
    kw = {**FAST, "cooling_mode": "air", "ambient_temp": AMB,
          "air_speed_mps": V_PROP, "geo": _geo()}
    kw.update(over)
    return th.solve_thermal_field(**kw)


def test_propeller_air_cools_the_housed_end_caps(em_run):
    manual = _solve()
    prop = _solve(air_speed_source="propeller", propeller_id="p1")
    blk = prop["cooling"]["housing_end_faces"]
    b = prop["cooling"]["heat_budget"]

    assert blk["mode"] == "slipstream"
    assert blk["front"]["air_speed_mps"] == pytest.approx(V_PROP)
    assert blk["heat_removed_W"] > 0.0
    assert b["housing_end_faces_W"] == pytest.approx(blk["heat_removed_W"], abs=1e-3)
    # G·ΔT on the housing wall — the caps are at the housing temperature
    assert blk["heat_removed_W"] == pytest.approx(
        blk["G_W_per_K"] * (blk["t_wall_c"] - AMB), rel=0.02)
    # every watt on a named line
    assert b["residual_pct"] < 2.0, b
    assert (b["housing_W"] + b["housing_end_faces_W"] + b["bore_W"]
            == pytest.approx(b["losses_W"], rel=0.02, abs=0.02))
    assert abs(b["stator_heat_split"]["closure_W"]) < 0.02 * b["losses_W"] + 0.02
    # same machine, same speed typed by hand: no caps, hotter winding
    assert manual["cooling"]["housing_end_faces"]["mode"] == "off"
    assert manual["cooling"]["heat_budget"]["housing_end_faces_W"] == 0.0
    assert (prop["components"]["winding"]["max"]
            < manual["components"]["winding"]["max"])


def test_other_modes_keep_the_end_caps_off(em_run):
    openf = _solve(air_speed_source="propeller", propeller_id="p1", frame="open")
    robot = _solve(cooling_mode="robotics", end_faces="none")
    for f in (openf, robot):
        assert f["cooling"]["housing_end_faces"]["mode"] == "off"
        assert f["cooling"]["heat_budget"]["housing_end_faces_W"] == 0.0
