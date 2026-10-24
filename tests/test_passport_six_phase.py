"""Small passport metadata and solver-kwargs checks for two-set windings."""
import json

import pytest

from motor_ai_sim.passport_winding import (has_six_phase_override,
                                            passport_phase_metadata,
                                            reset_six_phase_override,
                                            resolved_six_phase,
                                            set_six_phase_override,
                                            six_phase_override)
from motor_ai_sim.passport_v1.jobs import base_kwargs, run_job, worker_init
from motor_ai_sim.passport_v1.fullcard import compare_html, machine_html
from motor_ai_sim.passport_v1.card import (loss_point_block,
                                            six_phase_peak_copper_per_set)


def _geometry():
    return {"num_slots": 48, "num_poles": 56}


def test_six_phase_metadata_keeps_equivalent_total_and_set_values():
    spec = resolved_six_phase({"phases": 6, "n_parallel": 2,
                               "set_neutrals": "isolated"}, _geometry(), "4S-2P")
    assert spec["shift_deg"] == 0.0
    assert spec["n_parallel"] == 2
    assert spec["paths_per_set"] == 1
    meta = passport_phase_metadata(spec, 120.0, 410.0)
    assert meta["phase_count"] == 6
    assert meta["phase_sets"] == 2
    assert meta["current_basis"] == "equivalent_three_phase_total"
    assert meta["six_phase"]["current_per_inverter_A"] == 60.0
    assert meta["six_phase"]["voltage_per_set_V"] == 410.0
    assert meta["six_phase"]["stage_capability"]["fault_cases"] == "not_computed"


def test_six_phase_jobs_force_full_ring_and_three_phase_kwargs_are_unchanged():
    base = {"mesh": {"mesh_size_mm": 1.0, "min_size_mm": 0.3,
                      "outer_air_factor": 1.2, "gap_layers": 1,
                      "n_sectors": 8, "iron_template": True, "geo_mesh": True,
                      "pole_copy": False, "component_mesh_mm": {}},
            "winding": {"connection": "4S-2P", "phases": 6,
                        "star_delta": "star"},
            "rated_duty": {"end_winding_factor": 1.1},
            "geometry": _geometry()}
    spec = resolved_six_phase(base["winding"], base["geometry"], "4S-2P")
    base["six_phase_spec"] = spec
    six = base_kwargs(base, daxis_deg=0.0)
    assert six["six_phase"] == spec
    assert six["n_sectors"] == 1
    base.pop("six_phase_spec")
    old_snapshot = base_kwargs(base, daxis_deg=0.0)
    assert old_snapshot["six_phase"] == spec
    assert old_snapshot["n_sectors"] == 1
    base["winding"]["phases"] = 3
    three = base_kwargs(base, daxis_deg=0.0)
    assert "six_phase" not in three
    assert three["n_sectors"] == 8


def test_passport_override_distinguishes_explicit_three_phase_and_resets():
    assert not has_six_phase_override()
    token = set_six_phase_override(None)
    try:
        assert has_six_phase_override()
        assert six_phase_override() is None
    finally:
        reset_six_phase_override(token)
    assert not has_six_phase_override()


def test_run_job_preserves_per_set_solver_values_and_raw_result(monkeypatch, tmp_path):
    from motor_ai_sim.simulation import fem_solver_2d

    spec = resolved_six_phase({"phases": 6, "n_parallel": 2},
                              _geometry(), "4S-2P")

    def solve(**kw):
        assert kw["six_phase"] == spec
        import math
        angles = [2 * math.pi * k / 12 for k in range(12)]
        return {"I_phase_rms_solved_A": None,
                "V_A": [405 * math.cos(a) for a in angles],
                "V_B": [405 * math.cos(a - 2 * math.pi / 3) for a in angles],
                "V_C": [405 * math.cos(a + 2 * math.pi / 3) for a in angles],
                "n_periods": 1.0, "P_cu_W": [20.0, 24.0],
                "P_fe_W": [6.0], "six_phase": {"represented_sets": [1, 2]}}

    monkeypatch.setattr(fem_solver_2d, "em_transient_eval", solve)
    worker_init({})
    rec = run_job({"id": "six", "kw": {"six_phase": spec,
                                          "I_phase_rms": 120.0}}, str(tmp_path))
    assert rec["ok"] is True
    six = rec["r"]["six_phase"]
    assert six["represented_sets"] == [1, 2]
    assert six["current_per_inverter_A"] == 60.0
    assert six["current_basis"] == "imposed equivalent machine phase current / 2"
    assert six["sets"][0]["V1_phase_peak_V"] == pytest.approx(405.0, abs=0.02)
    assert six["losses_per_set_W"]["copper_W"] == 11.0
    assert six["losses_per_set_W"]["iron_W"] is None
    import gzip
    with gzip.open(tmp_path / "raw" / "six.json.gz", "rt", encoding="utf-8") as fh:
        raw = json.load(fh)
    assert raw["six_phase"]["represented_sets"] == [1, 2]

    voltage = run_job({"id": "six_voltage", "kw": {"six_phase": spec,
                                                       "drive": "voltage",
                                                       "I_phase_rms": 120.0}},
                      str(tmp_path))
    assert voltage["r"]["six_phase"]["current_per_inverter_A"] is None
    assert voltage["r"]["six_phase"]["current_basis"] == \
        "solved RMS machine phase current / 2"


def test_current_drive_zero_and_missing_copper_remain_valid(monkeypatch, tmp_path):
    from motor_ai_sim.simulation import fem_solver_2d
    spec = resolved_six_phase({"phases": 6, "n_parallel": 2},
                              _geometry(), "4S-2P")
    monkeypatch.setattr(fem_solver_2d, "em_transient_eval",
                        lambda **kw: {"I_phase_rms_solved_A": None,
                                      "six_phase": {"represented_sets": [1, 2]}})
    rec = run_job({"id": "zero", "kw": {"six_phase": spec, "drive": "current",
                                           "I_phase_rms": 0.0}}, str(tmp_path))
    assert rec["ok"] is True
    six = rec["r"]["six_phase"]
    assert six["current_per_inverter_A"] == 0.0
    assert six["copper_loss_per_set_W"] is None


def test_loss_point_carries_per_set_block_into_card_grid():
    six = {"current_per_inverter_A": 60.0,
           "sets": [{"set": 1, "V1_phase_peak_V": 405.0}]}
    record = {"kw": {"rpm": 1000, "I_phase_rms": 120, "gamma_deg": 10},
              "r": {"P_loss_total_W": 80.0, "T_avg_Nm": 12.0,
                    "six_phase": six}}
    point = loss_point_block({"rated": record}, "rated", {"P_W": 2.0})
    assert point["six_phase"] == six
    assert point["P_loss_em_W"] == 80.0


def test_peak_copper_allocation_reads_interpolated_groups_and_fails_closed():
    assert six_phase_peak_copper_per_set(
        {"P_cu_dc_W": 18.0, "P_cu_ac_W": 6.0}) == 12.0
    assert six_phase_peak_copper_per_set({"P_cu_dc_W": 18.0}) is None
    assert six_phase_peak_copper_per_set({}) is None


def test_old_invalid_six_phase_snapshot_is_rejected_loudly():
    snap = {"mesh": {"mesh_size_mm": 1.0, "min_size_mm": 0.3,
                     "outer_air_factor": 1.2, "gap_layers": 1,
                     "n_sectors": 8, "iron_template": True, "geo_mesh": True,
                     "pole_copy": False, "component_mesh_mm": {}},
            "winding": {"connection": "4S-1P", "phases": 6},
            "rated_duty": {"end_winding_factor": 1.1},
            "geometry": _geometry()}
    with pytest.raises(ValueError, match="even number"):
        base_kwargs(snap, daxis_deg=0.0)


def test_stage1_only_fullcard_renders_without_unqualified_blocks():
    rec = {"full_card_status": "stage 1 only; six-phase full-card studies are unqualified",
           "machine": {"die": "public fixture"}, "phase_sets": 2,
           "current_basis": "equivalent_three_phase_total",
           "six_phase": {"set_connection": "star"},
           "card": {"rated_point": {"rpm": 1000, "I_rms": 120,
                                      "six_phase": {"current_per_inverter_A": 60},
                                      "T_operating_Nm": {"value": 12.0},
                                      "P_loss_em_W": {"value": 80.0}},
                    "peak_point": {"rpm": 1000, "I_rms": 180,
                                    "six_phase": {"current_per_inverter_A": 90},
                                    "T_operating_Nm": {"value": 16.0},
                                    "P_loss_em_W": {"value": 150.0}}}}
    html = machine_html(rec, "L80", "public")
    assert "2 x 3-phase" in html
    assert "single-set fault cases are not qualified" in html
    assert "Stage A 3-D" in html
    assert "90" in html
    comparison = compare_html({"L80": rec})
    assert "qualified stages" in comparison


def test_generate_passport_applies_target_six_phase_to_every_route_call(monkeypatch):
    from motor_ai_sim import passport
    from motor_ai_sim.config import get_config
    from motor_ai_sim.routes import simulation
    from tests.test_passport_loss_fixes import _StubSolver

    cfg = get_config()
    geo = dict(cfg.get("geometry") or {})
    winding = dict(cfg.get("winding") or {})
    winding.update({"phases": 6, "connection": "2S-2P"})
    stub = _StubSolver("star", float(geo.get("motor_length") or 12.0))
    seen = []

    def solve(**kw):
        assert has_six_phase_override()
        seen.append(six_phase_override())
        out = stub(**kw)
        out["six_phase"] = {"represented_sets": [1, 2]}
        return out

    monkeypatch.setattr(simulation, "get_fem_transient", solve)
    result = passport.generate_passport(
        machine={"geometry": geo, "connection": "2S-2P", "star_delta": "star",
                 "materials": {}, "end_winding_factor": 2.0, "winding": winding},
        I0=30.0, gamma_deg=10.0, rpm0=1000.0, rpms=[500.0, 1000.0],
        base_steps=6, sweep_steps=6, pwm="off")
    assert seen and all(s["phases"] == 6 and s["paths_per_set"] == 1 for s in seen)
    assert all(call["n_sectors"] == 1 for call in stub.calls)
    assert result["passport"]["phase_sets"] == 2
    assert result["passport"]["six_phase"]["current_per_inverter_A"] == 15.0
    assert result["passport"]["six_phase"]["copper_loss_per_set_W"] > 0.0
    assert not has_six_phase_override()
