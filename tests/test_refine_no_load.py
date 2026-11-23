"""refine_proc.run_one(no_load=True): the unexcited eval behind the Sweep's opt-in "Cogging pp".

* fast (fake kernel): whatever excitation the config carries (block current, applied voltage, PWM
  with OPT_ALLOW_PWM), a no-load eval is sent as an unexcited sinusoidal CURRENT drive; a loaded eval
  and a plain eval are sent exactly as before; a non-zero current is refused;
* slow (real FEM, one small public case, run in a job container): the no-load eval equals a plain
  I = 0 run of the same geometry - same steps, mesh and rotor positions - within tolerance, and says
  what it really solved.
"""
from __future__ import annotations

import types

import pytest

from motor_ai_sim.optimization import refine_proc
from tests.test_optimizer_honesty import CFG, GEO, _raw


@pytest.fixture()
def captured(monkeypatch):
    import motor_ai_sim.config as _cfgmod
    seen = []
    cfg = {**CFG, "simulation": {"rpm": 3950.0, "demag": False, "drive": "bldc_current", "i_block": 15.0,
                                 "v_phase_peak": 12.0, "v_delta_deg": 5.0}}
    monkeypatch.setattr(_cfgmod, "get_config", lambda *a, **k: cfg)

    def run(cap, args):
        seen.append(dict(args))
        return {"ok": True, "result": types.SimpleNamespace(raw=dict(_raw(converged=True)))}

    monkeypatch.setattr(refine_proc, "_kernel", lambda: types.SimpleNamespace(run=run))
    return seen


def _one(**kw):
    return refine_proc.run_one({}, current_a=kw.pop("current_a", 0.0), steps=12, coil_temp_c=20.0, **kw)


def test_no_load_is_an_unexcited_current_drive_whatever_the_config_carries(captured):
    _one(no_load=True)
    a = captured[0]
    assert a["drive"] == "current" and a["I_phase_rms"] == 0.0
    assert a["v_phase_peak"] == 0.0 and a["v_delta_deg"] == 0.0
    assert "i_block" not in a and "v_bus" not in a and "f_switch" not in a


def test_no_load_under_a_pwm_config_with_the_pwm_switch_on_is_still_unexcited(captured, monkeypatch):
    monkeypatch.setenv("OPT_ALLOW_PWM", "1")
    import motor_ai_sim.config as _cfgmod
    _cfgmod.get_config().get("simulation").update(drive="pwm_voltage", v_bus=48.0, f_switch=20000.0)
    _one(no_load=True)
    a = captured[0]
    assert a["drive"] == "current" and "v_bus" not in a and "f_switch" not in a and a["v_phase_peak"] == 0.0


def test_without_the_flag_the_eval_is_sent_exactly_as_before(captured):
    _one()
    a = captured[0]
    assert a["drive"] == "bldc_current" and a["i_block"] == 15.0 and a["v_phase_peak"] == 12.0
    res = _one(current_a=0.0)
    assert "no_load_provenance" not in res            # a plain payload is unchanged


def test_a_no_load_eval_names_what_it_solved_and_refuses_a_current(captured):
    res = _one(no_load=True)
    prov = res["no_load_provenance"]
    assert prov["drive"] == "current" and prov["current_a"] == 0.0
    assert prov["steps_per_period_effective"] == 12 and prov["demag"] is False
    with pytest.raises(ValueError):
        _one(current_a=5.0, no_load=True)


def test_the_loaded_demag_loss_is_reported_only_when_asked(captured, monkeypatch):
    raw = dict(_raw(converged=True))
    raw["demag_summary"] = {"loss_pct": 0.7}
    monkeypatch.setattr(refine_proc, "_kernel", lambda: types.SimpleNamespace(
        run=lambda cap, args: {"ok": True, "result": types.SimpleNamespace(raw=raw)}))
    assert "demag_loss_pct" not in _one(current_a=20.0)                      # payload as before
    assert _one(current_a=20.0, report_demag_loss=True)["demag_loss_pct"] == 0.7


@pytest.mark.slow
def test_the_no_load_solve_equals_a_plain_no_load_run_of_the_same_geometry(monkeypatch):
    """REAL FEM on the 30 mm 12s14p fixture: same steps, mesh, sectors, rotor positions -> the same
    cogging peak-to-peak (the option adds a forced unexcited drive, nothing else)."""
    import motor_ai_sim.config as _cfgmod
    monkeypatch.setattr(_cfgmod, "get_config", lambda *a, **k: CFG)      # the 30 mm machine, not the live config
    kw = dict(steps=24, coil_temp_c=20.0, mesh_size_mm=1.4, min_size_mm=0.35, n_sectors=2, gap_layers=1.0,
              magnet_temp_c=20.0, rotor_eddy=False, demag=False, rpm=100.0, sampling_purpose="optimization",
              optimizer_candidate=True)
    plain = refine_proc.run_one(dict(GEO), current_a=0.0, **kw)
    nl = refine_proc.run_one(dict(GEO), current_a=0.0, no_load=True, **kw)
    assert nl["no_load_provenance"]["steps_per_period_effective"] == 24
    p0, p1 = float(plain["T_ripple_pp_Nm"]), float(nl["T_ripple_pp_Nm"])
    assert p0 > 0.0 and p1 == pytest.approx(p0, rel=1e-6)
    assert float(nl["T_em_Nm"]) == pytest.approx(float(plain["T_em_Nm"]), abs=1e-6)


def test_the_effective_mesh_uses_the_solvers_own_geometry_merge_not_a_shallow_one():
    """Astra's reproducer: slot_width stored 2.5 mm but wire_width 2.1 -> 0.7 mm; the solver derives the
    slot width from the primaries (smaller) before refining, so the recorded mesh must be ITS value, not
    the 1.25 mm a shallow merge gives."""
    base = {**GEO, "slot_width": 2.5, "wire_width": 2.1}
    got = refine_proc._eff_mesh_mm(base, {"wire_width": 0.7}, 4.0, 0.3, False)
    from motor_ai_sim.simulation.fem_solver_2d import mesh_feature_floor_mm
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override
    solver = min(4.0, mesh_feature_floor_mm(merge_geo_override(dict(base), {"wire_width": 0.7}), 0.3, False))
    shallow = min(4.0, mesh_feature_floor_mm({**base, "wire_width": 0.7}, 0.3, False))
    assert shallow == pytest.approx(1.25)
    assert got == pytest.approx(solver) and got < shallow - 0.1
