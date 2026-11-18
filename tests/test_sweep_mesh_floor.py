"""The Sweep / Descent mesh-size floors (BUG_sweep_magnet_flat.md item 4): 1.0 / 0.1 mm without a preset, as always; a request that
names a preset takes the sizes it resolved for the machine (0.15 mm on the 12 mm die).  A size the bounds change is reported."""
from __future__ import annotations

import pytest

from motor_ai_sim.routes import optimization as O
from test_mesh_presets import _machine


@pytest.fixture
def small_machine(monkeypatch):
    g = _machine(12.0, 12, 10)
    monkeypatch.setattr(O, "get_config", lambda *a, **k: {"geometry": g})
    return g


def _scan(**kw):
    return O.ScanRequest(variables=[{"name": "tooth_width", "min": 1.0, "max": 1.2, "mode": "grid", "step": 0.1}], **kw)


def _descent(**kw):
    return O.DescentRequest(variables=[{"name": "tooth_width", "min": 0.0, "max": 0.0}], **kw)


def test_without_a_preset_nothing_changes_for_a_request_inside_the_bounds(small_machine):
    for plan in (O.prepare_scan(_scan()), O.prepare_descent(_descent())):
        assert "mesh_clamped" not in plan["response"]
    assert O.prepare_scan(_scan())["response"]["mesh_size_mm"] == 4.0
    assert O.prepare_descent(_descent())["init_state"]["eval_params"]["mesh_size_mm"] == 4.0


def test_without_a_preset_the_old_floors_hold_and_the_clamp_is_reported(small_machine):
    r = O.prepare_scan(_scan(mesh_size_mm=0.15, min_size_mm=0.015))["response"]
    assert r["mesh_size_mm"] == 1.0 and r["min_size_mm"] == 0.1
    assert r["mesh_clamped"] == {"mesh_size_mm": {"requested": 0.15, "used": 1.0},
                                 "min_size_mm": {"requested": 0.015, "used": 0.1}}
    d = O.prepare_descent(_descent(mesh_size_mm=0.15, min_size_mm=0.015))["response"]
    assert d["mesh_clamped"]["mesh_size_mm"]["used"] == 1.0
    assert O._mesh_sizes(_scan(mesh_size_mm=20.0), None)[:2] == (12.0, 0.3)       # the upper cap is unchanged


@pytest.mark.parametrize("preset", ["optimization", "simulation"])
def test_a_preset_takes_the_machine_sizes_below_the_old_floor(small_machine, preset):
    from motor_ai_sim import mesh_presets as MP
    want = MP.resolve(preset, small_machine)["values"]
    assert want["mesh_size_mm"] < 1.0                                           # the 12 mm die is below the old floor
    r = O.prepare_scan(_scan(preset=preset))["response"]
    assert r["mesh_size_mm"] == want["mesh_size_mm"] and r["min_size_mm"] == want["min_size_mm"]
    assert "mesh_clamped" not in r
    d = O.prepare_descent(_descent(preset=preset))
    assert d["init_state"]["eval_params"]["mesh_size_mm"] == want["mesh_size_mm"]
    assert d["init_state"]["eval_params"]["min_size_mm"] == want["min_size_mm"] and "mesh_clamped" not in d["response"]


def test_the_preset_sanity_bound_still_clamps_an_absurd_size(small_machine):
    ms, mn, clamped = O._mesh_sizes(_scan(mesh_size_mm=0.001, min_size_mm=0.0001), {"name": "optimization"})
    assert (ms, mn) == (0.02, 0.002) and set(clamped) == {"mesh_size_mm", "min_size_mm"}
