"""Solver guards added 2026-09-23 (docs/solver-guards-2026-09-23.md).

1. ``check_eddy_conductor_bodies`` — the eddy solve imposes one branch current
   per meshed conductor body (Iunit = ±1), so a slot must hold exactly
   ``n_wires`` bodies; a sector must hold whole slots; a body under half its
   slot's median area is refused, a clipped stack is warned about.
2. ``snapshot_scalar_history`` accepts numpy scalars / 0-d arrays and skips a
   genuinely vector channel with a recorded reason instead of raising.
3. Every per-frame series the P2 frame loop appends is in the settle-trim
   tuple ``_v2_lists`` (the sleeve loss and the air-gap |B| were not).

No FEM, no live config.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

import numpy as np
import pytest

from motor_ai_sim.simulation.fem_solver_2d import check_eddy_conductor_bodies
from motor_ai_sim.simulation.sb_postproc import (
    drop_settling_frames,
    retained_window_metadata,
    snapshot_scalar_history,
)

SOLVER = Path(__file__).resolve().parents[1] / "src/motor_ai_sim/simulation/fem_solver_2d.py"


# ── 1. conductor-body guard ──────────────────────────────────────────────────
def _bodies(n_slots, n_wires, area=9e-6, drop=(), areas=None):
    """Synthetic ``_coil_con`` rows + meshed areas: slot s holds tags
    200 + s*n_wires + i."""
    con, area_of = [], {}
    for s in range(n_slots):
        for i in range(n_wires):
            tag = 200 + s * n_wires + i
            if tag in drop:
                continue
            con.append({"tag": tag, "slot": s, "phase": "A", "Iunit": 1.0})
            area_of[tag] = (areas or {}).get(tag, area)
    return con, area_of


def test_whole_slots_with_equal_areas_pass_and_summarise():
    con, areas = _bodies(6, 7)          # the Ø40 two-sector mesh: 6 slots x 7
    out = check_eddy_conductor_bodies(con, areas, 7, n_slots_expected=6,
                                      log=logging.getLogger("t"))
    assert out["n_bodies"] == 42 and out["n_slots_meshed"] == 6
    assert out["area_spread_max_rel"] == 0.0 and out["area_warnings"] == []


def test_wire_split_counts_strips_not_rows():
    # 10 rows x 2 strips = 20 bodies per slot; n_wires is the strip count.
    con, areas = _bodies(2, 20)
    assert check_eddy_conductor_bodies(con, areas, 20)["n_wires_per_slot"] == 20
    with pytest.raises(RuntimeError, match=r"slot 0: expected 10 .* found 20"):
        check_eddy_conductor_bodies(con, areas, 10)


def test_dropped_body_is_refused_naming_slot_expected_found():
    con, areas = _bodies(3, 6, drop=(200 + 6 + 2,))      # slot 1 lost one
    with pytest.raises(RuntimeError) as e:
        check_eddy_conductor_bodies(con, areas, 6, n_slots_expected=3)
    msg = str(e.value)
    assert "slot 1: expected 6 conductor bodies" in msg and "found 5" in msg
    assert "slot 0" not in msg and "slot 2" not in msg


def test_merged_bodies_are_refused_even_when_total_copper_is_right():
    # Two strips fused into one polygon: the old area-weighted Iunit summed
    # to the right ampere-turns; Iunit = ±1 loses one branch current.
    con, areas = _bodies(2, 4, drop=(203,))
    areas[202] = 2 * 9e-6
    with pytest.raises(RuntimeError, match=r"slot 0: expected 4 .* found 3"):
        check_eddy_conductor_bodies(con, areas, 4)


def test_unreadable_slot_is_refused():
    con, areas = _bodies(1, 3)
    con[1]["slot"] = -1
    with pytest.raises(RuntimeError, match="slot cannot be read"):
        check_eddy_conductor_bodies(con, areas, 3)


def test_sector_must_hold_whole_slot_count():
    con, areas = _bodies(5, 7)
    with pytest.raises(RuntimeError, match=r"holds 5 slot\(s\).*expected 6"):
        check_eddy_conductor_bodies(con, areas, 7, n_slots_expected=6)
    assert check_eddy_conductor_bodies(con, areas, 7, n_slots_expected=None)


def test_small_area_spread_is_clean_and_reported():
    # The saved 30 mm fixture spread: 0.17 % min/max.
    con, areas = _bodies(1, 6, areas={200: 9.0e-6, 201: 9.00315e-6,
                                      202: 9.00837e-6, 203: 9.0009e-6,
                                      204: 8.9991e-6, 205: 8.9928e-6})
    out = check_eddy_conductor_bodies(con, areas, 6)
    assert 0.0 < out["area_spread_max_rel"] < 0.002
    assert out["area_warnings"] == []


def test_clipped_stack_is_a_recorded_warning_not_a_refusal(caplog):
    con, areas = _bodies(2, 5)
    areas[204] = 9e-6 * 0.80                     # top wire clipped to 80 %
    with caplog.at_level(logging.WARNING, logger="t"):
        out = check_eddy_conductor_bodies(con, areas, 5,
                                          log=logging.getLogger("t"))
    assert len(out["area_warnings"]) == 1
    assert "slot 0" in out["area_warnings"][0] and "tag 204" in out["area_warnings"][0]
    assert abs(out["area_spread_max_rel"] - 0.2) < 1e-9
    assert any("clipped" in r.message for r in caplog.records)


def test_half_body_is_refused():
    con, areas = _bodies(1, 4)
    areas[203] = 9e-6 * 0.4                      # cut by the sector boundary
    with pytest.raises(RuntimeError, match=r"slot 0, tag 203 .*60 % off"):
        check_eddy_conductor_bodies(con, areas, 4)


def test_guard_is_called_where_the_bodies_are_built():
    tree = ast.parse(SOLVER.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "check_eddy_conductor_bodies"]
    assert len(calls) == 1
    args = [ast.unparse(a) for a in calls[0].args]
    assert args == ["_coil_con", "_coil_area_meshed", "n_wires"]


# ── 2. scalar-history snapshot never fails a finished solve ─────────────────
def test_snapshot_accepts_numpy_scalars_and_zero_d_arrays():
    raw = snapshot_scalar_history({
        "torque_em_Nm": [np.float64(1.5), np.array(2.5), np.array([3.5]),
                         np.int32(4), 5, None],
    })
    assert raw["samples"]["torque_em_Nm"] == [1.5, 2.5, 3.5, 4, 5, None]
    assert all(type(v) in (float, int, type(None))
               for v in raw["samples"]["torque_em_Nm"])
    assert raw["skipped_series"] == {}
    assert raw["all_series_aligned"]


def test_snapshot_skips_a_vector_channel_with_a_reason():
    raw = snapshot_scalar_history({
        "torque_em_Nm": [1.0, 2.0, 3.0],
        "field_B": [np.zeros(100), np.zeros(100)],
        "time_s_absolute": [0.0, 1.0, 2.0],
    })
    assert set(raw["samples"]) == {"torque_em_Nm", "time_s_absolute"}
    assert "field_B" in raw["skipped_series"]
    assert "index 0" in raw["skipped_series"]["field_B"]
    assert "(100,)" in raw["skipped_series"]["field_B"]
    assert raw["all_series_aligned"]          # judged on the kept channels only


def test_skipped_channel_travels_into_the_retained_window_metadata():
    live = {"time_s_absolute": [10.0, 11.0, 12.0], "mechanical_angle_rad": [0.0, 0.1, 0.2],
            "torque_em_Nm": [1.0, 2.0, 3.0], "psi_A_Wb": [1.0, 2.0, 3.0],
            "psi_B_Wb": [1.0, 2.0, 3.0], "psi_C_Wb": [1.0, 2.0, 3.0],
            "current_A_A": [1.0, 2.0, 3.0], "current_B_A": [1.0, 2.0, 3.0],
            "current_C_A": [1.0, 2.0, 3.0],
            "eddy_loss_density": [np.zeros(7), np.zeros(7), np.zeros(7)]}
    raw = snapshot_scalar_history(live)
    drop_settling_frames(live.values(), 1, live["time_s_absolute"])
    meta = retained_window_metadata(
        raw, live, core_series=("time_s_absolute", "mechanical_angle_rad",
                                "torque_em_Nm", "psi_A_Wb", "psi_B_Wb", "psi_C_Wb",
                                "current_A_A", "current_B_A", "current_C_A"),
        trim_operations=({"kind": "voltage_settling", "requested_frames": 1},),
        nominal_retained_frames=2, retained_periods=1.0)
    assert meta["core_waveform_aligned"]
    assert meta["retained_start_raw_sample_index"] == 1
    assert list(meta["skipped_series"]) == ["eddy_loss_density"]
    assert "eddy_loss_density" not in meta["sample_count_by_series_before_trim"]


def test_solver_snapshot_call_is_guarded():
    src = SOLVER.read_text(encoding="utf-8")
    i = src.index("_p2_raw_scalar_history = _snapshot_scalar_history(")
    block = src[src.rfind("\n", 0, i - 400):i]
    assert "try:" in block


# ── 3. every per-frame series is settle-trimmed ─────────────────────────────
# Lists the frame loop appends to that are NOT one-entry-per-reported-frame
# series (keyframes, sampled rows, frame-index lists, warm-up-only samples,
# anchor events) — everything else the loop appends must be in _v2_lists.
NOT_PER_FRAME = {
    "_frames2",       # animation keyframes, picked beyond the settling prefix
    "_inc_rows",      # incremental Ldq rows at sampled frames only
    "_pic_fallback", "_pic_unconv",    # frame INDEX lists (diagnostics)
    "_v_bpsi",        # period-boundary flux samples (Aitken anchor)
    "_cs_hist",       # one row per settling PERIOD (converged sine settle)
    "_warm_ks", "_warm_solid",         # eddy warm-up (k < 0) samples only
    "_warm_grp.setdefault(_gk, [])",   # …per conductor group (period gauge)
    "_v_diag.setdefault('dc_anchor_A', [])",   # one entry per anchor event
}


def _loop_appends_and_v2():
    tree = ast.parse(SOLVER.read_text(encoding="utf-8"))
    loops = [n for n in ast.walk(tree)
             if isinstance(n, ast.While) and "_fseq" in ast.dump(n.test)]
    assert len(loops) == 1, "the P2 frame loop is `while _fi < len(_fseq)`"
    appended = set()
    for n in ast.walk(loops[0]):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "append"):
            appended.add(ast.unparse(n.func.value))
    v2 = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
          and any(isinstance(t, ast.Name) and t.id == "_v2_lists" for t in n.targets)]
    assert len(v2) == 1
    return appended, {ast.unparse(e) for e in v2[0].value.elts}


def test_every_per_frame_series_is_in_the_settle_trim_tuple():
    appended, trimmed = _loop_appends_and_v2()
    missing = appended - trimmed - NOT_PER_FRAME
    assert not missing, (
        "per-frame series appended in the P2 frame loop but not trimmed with "
        "the settling frames (add to _v2_lists, or to NOT_PER_FRAME here with "
        "a reason): %s" % sorted(missing))
    unknown = NOT_PER_FRAME - appended
    assert not unknown, "NOT_PER_FRAME names nothing the loop appends: %s" % sorted(unknown)


def test_sleeve_loss_and_gap_field_are_settle_trimmed():
    _, trimmed = _loop_appends_and_v2()
    assert {"_ed_sl", "_bgap2", "_ed_cu", "_ed_mag", "_ed_sh"} <= trimmed


def test_sleeve_and_gap_series_are_in_the_raw_scalar_snapshot():
    src = SOLVER.read_text(encoding="utf-8")
    i = src.index("_p2_scalar_series = {")
    block = src[i:src.index("}", i)]
    assert '"eddy_sleeve_power_W": _ed_sl' in block
    assert '"air_gap_B_mean_T": _bgap2' in block
