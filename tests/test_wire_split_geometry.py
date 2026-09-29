"""``wire_split`` is GEOMETRY, and its strips are SERIES TURNS.

The rule this file pins (user, 2026-09-08: *"сделай ширину провода 4,5 мм, слот
станет чуть больше, я бы гап между проводами сделал 2·Wire Spacing X"*, then
*"wire_split_series можно убрать — нам всегда будет нужно только
последовательное подключение этих двух катушек; при параллельном подключении
возникнут компенсационные токи между ними"*):

  * ``wire_width`` is ONE STRIP.  ``wire_split`` = N lays N of them SIDE BY SIDE
    across the slot, ``2 × wire_spacing_x`` apart, so the wire column — and the
    pocket the CAD cuts for it — is ``N·wire_width + (N−1)·2·wire_spacing_x``.
    The user splits a 9 mm bar by typing ``wire_width 4.5, wire_split 2``.
  * every strip is a DRAWN, MESHED, SEPARATELY-EXCITED conductor: N polygons
    where the wire had one, N bodies in the coupled σ·∂A/∂t solve, N blocks in
    the thermal map, N counted in the mass.
  * the N strips of a row are CONSECUTIVE SERIES TURNS — always, there is no
    other wiring: turns ×N, ψ ×N, KV ÷N, R ×N², every strip at the full branch
    current, and the same torque back at I ÷ N.

What is deliberately NOT here: the retired reading in which ``wire_split`` cut a
bar into strips of ``wire_width/N`` with no CAD behind them and assumed ideal
transposition (``cu_ac_solved_ignores_wire_split`` was its flag and it is gone,
case (e)), and the ``wire_split_series`` knob that offered a PARALLEL wiring for
a few hours the same day (case (f) pins that it is gone, not that it works).

Cost: two 4-frame transients and one thermal map on the 30 mm 12s/14p fixture
the physics-regression suite is pinned on — the smallest honest cycle in the
repo (tests/test_thermal_routes.py::FAST).  The physics is not the claim; the
BOOKKEEPING is, and a converged 24-frame run would buy nothing here.
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import time
from typing import Any, Dict

import numpy as np
import pytest

from motor_ai_sim.cadquery_geometry import CadQueryMotor
from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band
from motor_ai_sim.winding import (
    STRIP_GAP_FACTOR,
    conductors_per_slot,
    n_parallel_effective,
    strip_gap_mm,
    strip_width_mm,
    turns_per_coil,
    winding_footprint_mm,
    wire_split_from_geo,
)

from tests.test_physics_regression import GEO_30MM, OVERRIDE, RPM, CONNECTION

# ─────────────────────────────────────────────────────────────────────────────
# The fixture
# ─────────────────────────────────────────────────────────────────────────────
#
# ``wire_spacing_x`` is tightened from the regression fixture's 0.1 mm to
# 0.02 mm ON BOTH MEMBERS of every pair below.  The reason is that the split's
# gaps are REAL PLANE: splitting a 2.0 mm wire into 2 × 1.0 mm on this machine
# widens the slot by 2·wire_spacing_x, and at 0.1 mm that is +0.2 mm on a 2.0 mm
# column — a 10 % wider winding window on a 30 mm machine, worth −2.5 % of ψ and
# +1.8 % of k_end all by itself.  That is honest physics and not what these
# cases are about: they measure the ELECTRICAL bookkeeping, so the fixture keeps
# the geometric perturbation small (+0.04 mm, ≈0.6 % on ψ) instead of pretending
# it is zero.  Every tolerance below is comfortably wider than that residue and
# far narrower than the factor the bookkeeping would move by if it were wrong.
BASE: Dict[str, Any] = dict(GEO_30MM, wire_spacing_x=0.02)

#: The same machine with the wire halved and split in two — the user's own
#: transformation (9 → 4.5 mm), scaled to this fixture (2.0 → 1.0 mm).
SPLIT: Dict[str, Any] = dict(BASE, wire_width=1.0, wire_split=2)

#: What the unsplit fixture runs at, and TWICE what the split one runs at.
#: Low on purpose: the split machine needs I/N to sit at the same MMF, and
#: comparing a saturated run with an unsaturated one would move the ratio for
#: iron reasons.
I_PROBE = 20.0

#: This 12s/14p family's calibrated d-axis (config/.daxis_cache.json holds
#: 59.87…59.95 across every cross-section measured).  PASSED, not measured, so
#: the pair shares one frame and no case starts a 24-frame calibration.
DAXIS_DEG = 59.9

#: The cheapest honest cycle: 4 frames over one electrical period, coarse mesh,
#: the machine's natural half-wedge.  `eddy=True` because case (c) is about the
#: SOLVED AC copper — the number the split exists to move.
RUN = dict(n_steps_per_period=4, n_periods=1.0, mesh_size_mm=2.5,
           min_size_mm=0.6, gap_layers=1.0, n_sectors=2, structured_gap=True,
           iron_template=True, geo_mesh=True, coil_temp_c=120.0, eddy=True,
           rotor_eddy=False, element_order=2, demag=False, daxis_deg=DAXIS_DEG)


def _build(geo: Dict[str, Any], angle: float = 0.0):
    motor = CadQueryMotor()
    motor.set_parameters(dict(geo))
    return motor, motor.get_2d_polygons(rotor_angle_deg=angle)


def _solve(geo: Dict[str, Any], current_a: float) -> Dict[str, Any]:
    """One cold 4-frame transient.  Cold because the coupled eddy solve
    warm-starts from the previous run's settled state, and a pair compared with
    one leg warm is a pair compared at two different tolerances."""
    from motor_ai_sim.simulation import fem_solver_2d as _F
    _F._SB_WARM_CACHE.clear()
    try:
        _p = _F._warm_cache_path()
        if _p.exists():
            _p.unlink()
    except Exception:       # noqa: BLE001 — a missing file is the goal
        pass
    set_request_materials(OVERRIDE)
    try:
        return fem_transient_sliding_band(
            geo_override=dict(geo), rpm=RPM, connection=CONNECTION,
            I_phase_rms=float(current_a), gamma_deg=0.0, **RUN)
    finally:
        set_request_materials(None)


def _mean(v) -> float:
    return float(np.mean(np.asarray(v, float))) if isinstance(v, list) else float(v)


def _amp(series) -> float:
    """Peak-to-peak/2 — the amplitude a ratio can be taken of."""
    a = np.asarray(series, float)
    return float((a.max() - a.min()) / 2.0) if a.size else 0.0


def _cu_ac(d: Dict[str, Any]) -> float:
    """The SOLVED ∫σE² copper AC of this run (eddy=True, so it is solved)."""
    return _mean(d.get("P_cu_ac_W") or 0.0)


def _coil_area_mm2(polys) -> float:
    return float(sum(float(c.area) for c in polys["coils"]))


# ─────────────────────────────────────────────────────────────────────────────
# (a) wire_split = 1 is the machine that has no split at all
# ─────────────────────────────────────────────────────────────────────────────

def _build_without_split_keys(geo: Dict[str, Any]):
    """Build the machine with the split knobs genuinely ABSENT.

    Deleting them from the dict is not enough: ``CadQueryMotor`` fills every
    unlisted parameter from the LOADED config (the ``_map_api_to_cadquery``
    gap-fill the physics-regression docstring tells the F2 story about), and the
    user's live machine carries ``wire_split: 2``.  So the keys are removed
    AFTER the fill — which is exactly the state ``_wire_split`` must read as
    "one solid wire".
    """
    motor = CadQueryMotor()
    motor.set_parameters(dict(geo))
    motor.parameters.pop("wire_split", None)
    return motor, motor.get_2d_polygons(rotor_angle_deg=0.0)


def test_split_of_one_is_bit_identical_to_the_knob_being_absent():
    """Nothing about an unsplit machine may move because the knob exists.

    Polygons, conductor count, the slot the CAD cut, the CAD masses and the
    whole winding bookkeeping — all of it compared between the knob ABSENT and
    ``wire_split: 1``.
    """
    from motor_ai_sim.masses import cad_areas_m2

    absent = {k: v for k, v in BASE.items() if k != "wire_split"}
    one = dict(BASE, wire_split=1)

    # the pure bookkeeping: an absent knob is a knob set to 1
    for geo in (absent, one):
        assert wire_split_from_geo(geo) == 1
        assert conductors_per_slot(geo) == geo["num_wires_per_slot"]
        assert turns_per_coil(geo) == geo["num_wires_per_slot"]
        assert n_parallel_effective(2, geo) == 2
        assert strip_width_mm(geo) == geo["wire_width"]
        assert strip_gap_mm(geo) == 0.0
        assert winding_footprint_mm(geo) == geo["wire_width"]

    # …and the drawn machine, ring by ring
    _ma, ref = _build_without_split_keys(BASE)
    _m, polys = _build(one)
    assert polys["wire_split"] == 1
    assert polys["strip_width_mm"] == one["wire_width"]
    assert polys["wire_column_mm"] == one["wire_width"]
    assert len(polys["coils"]) == len(ref["coils"])
    for i, (a, b) in enumerate(zip(polys["coils"], ref["coils"])):
        assert a.equals_exact(b, 0.0), f"coil {i} moved"
    assert polys["stator"].equals_exact(ref["stator"], 0.0)
    assert polys["slot_cut_x_mm"] == ref["slot_cut_x_mm"]

    # the masses built on it
    a1 = cad_areas_m2(one)
    assert a1
    ns = int(BASE["num_seg"]) * int(BASE["num_slots_per_segment"])
    assert a1["copper"] * 1e6 == pytest.approx(
        ns * BASE["num_wires_per_slot"] * BASE["wire_width"]
        * BASE["wire_height"], rel=1e-9)


@pytest.mark.slow
def test_split_of_one_meshes_to_the_same_triangles():
    """The mesher is handed the same polygons, so it must build the same mesh —
    the check `test_split_of_one_is_bit_identical…` cannot make, because a
    conductor count that survived the CAD could still change the mesh."""
    from motor_ai_sim.simulation.mesher import build_mesh_from_polygons

    counts = []
    for motor, polys in (_build_without_split_keys(BASE),
                         _build(dict(BASE, wire_split=1))):
        mesh, tags = build_mesh_from_polygons(
            polys, rotor_angle_deg=0.0, mesh_size_mm=2.5, min_size_mm=0.6,
            geo_cfg=motor.parameters, outer_air_factor=1.2, gap_layers=1.0,
            n_sectors=2)[:2]
        counts.append((int(mesh.t.shape[1]), int(mesh.p.shape[1]),
                       int(np.asarray(tags, int).sum())))
    assert counts[0] == counts[1], counts


# ─────────────────────────────────────────────────────────────────────────────
# (b) the geometry the split builds
# ─────────────────────────────────────────────────────────────────────────────

def test_the_users_own_numbers_come_out_of_the_rule():
    """9 mm bar → wire_width 4.5, wire_split 2, gap 2·wire_spacing_x = 0.2 mm.

    The machine on the user's screen (12s/10p, 27 wires/slot, 3 in hand), so a
    change to the arithmetic shows up as HIS numbers, not as a fixture's.
    """
    g = {"wire_width": 4.5, "wire_height": 0.5, "wire_spacing_x": 0.1,
         "wire_spacing_y": 0.13, "num_wires_per_slot": 27, "wire_parallel": 3,
         "wire_split": 2}
    assert STRIP_GAP_FACTOR == 2.0
    assert strip_width_mm(g) == 4.5              # wire_width IS the strip
    assert strip_gap_mm(g) == pytest.approx(0.2)  # 2 × wire_spacing_x
    assert winding_footprint_mm(g) == pytest.approx(2 * 4.5 + 0.2)   # 9.2 mm
    assert conductors_per_slot(g) == 27 * 2
    # 27 rows / 3 in hand = 9 rows of turns, each row TWO series strips = 18
    # turns; the strips add no parallel path, so the divider stays the 3
    # strands in hand.
    assert turns_per_coil(g) == 18
    assert n_parallel_effective(1, g) == 3
    # …and the same rows unsplit are half the turns, on the same divider
    assert turns_per_coil(dict(g, wire_split=1)) == 9
    assert n_parallel_effective(1, dict(g, wire_split=1)) == 3


def test_the_strips_are_drawn_side_by_side_and_the_slot_grows():
    """Two strips of wire_width, one 2·wire_spacing_x gap, twice the conductors,
    twice the copper, and a pocket wide enough to hold them."""
    _m1, p1 = _build(BASE)
    _m2, p2 = _build(SPLIT)
    dx = BASE["wire_spacing_x"]
    w = SPLIT["wire_width"]

    # the column grew by exactly the formula
    assert p1["wire_column_mm"] == pytest.approx(BASE["wire_width"])
    assert p2["wire_column_mm"] == pytest.approx(2 * w + STRIP_GAP_FACTOR * dx)
    assert p2["wire_split"] == 2 and p2["strip_width_mm"] == pytest.approx(w)
    # …and the slot wall moved with it, by the column's growth and nothing else
    assert (p2["slot_cut_x_mm"] - p1["slot_cut_x_mm"]) == pytest.approx(
        p2["wire_column_mm"] - p1["wire_column_mm"])

    # twice the conductors, ONE polygon each
    assert len(p2["coils"]) == 2 * len(p1["coils"])
    # copper per turn = 2 × wire_width × wire_height (here: the same copper the
    # 2.0 mm wire had, because the user halved the width — that is the point)
    a_strip = w * SPLIT["wire_height"]
    assert _coil_area_mm2(p2) == pytest.approx(len(p2["coils"]) * a_strip,
                                               rel=1e-9)
    assert _coil_area_mm2(p2) == pytest.approx(_coil_area_mm2(p1), rel=1e-9)

    # The strips of ONE row are SEPARATE bodies, exactly one gap apart.  Slot 0
    # is built unrotated and its polygons come out strip-by-strip within a row
    # (see get_2d_polygons), so [0] and [1] are that row's two strips.
    s0, s1 = p2["coils"][0], p2["coils"][1]
    assert float(s0.centroid.y) == pytest.approx(float(s1.centroid.y), abs=1e-12)
    assert (float(s1.centroid.x) - float(s0.centroid.x)) == pytest.approx(
        w + STRIP_GAP_FACTOR * dx, abs=1e-9)
    assert float(s0.area) == pytest.approx(a_strip, rel=1e-12)
    assert s0.intersection(s1).area == pytest.approx(0.0, abs=1e-12)


def test_the_new_gaps_are_wire_coating_not_slot_air():
    """The enamel is the envelope MINUS the copper, so the (N−1) gaps of a split
    row come out as coating — which is what routes/thermal's domains 61-63 have
    to see, or a split machine's slot would draw a stripe of air between two
    conductors that are touching each other's insulation."""
    from shapely.geometry import box
    from shapely.ops import unary_union

    _m1, p1 = _build(BASE)
    _m2, p2 = _build(SPLIT)
    a1 = sum(float(g.area) for g in p1["wire_insulation"])
    a2 = sum(float(g.area) for g in p2["wire_insulation"])
    assert a2 > a1

    # the gap between the first row's two strips, taken off the strips
    s0, s1 = p2["coils"][0], p2["coils"][1]
    x0, y0, x1, y1 = s0.bounds
    x2, _y2, _x3, _y3 = s1.bounds
    gap = box(x1, y0, x2, y1)
    assert gap.area == pytest.approx(
        STRIP_GAP_FACTOR * BASE["wire_spacing_x"] * SPLIT["wire_height"],
        rel=1e-9)
    enamel = unary_union(list(p2["wire_insulation"]))
    assert gap.difference(enamel).area == pytest.approx(0.0, abs=1e-9)
    # …and it is not copper: no conductor reaches into it
    assert unary_union(list(p2["coils"])).intersection(gap).area == \
        pytest.approx(0.0, abs=1e-12)


# ─────────────────────────────────────────────────────────────────────────────
# (c) + (d) the split, solved
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def runs() -> Dict[str, Dict[str, Any]]:
    """Two 4-frame transients: the unsplit bar at I, and the same wire split in
    two at I/2 — the current that restores the MMF, since the strips are series
    turns.  Module-scoped — they are the only FEM solves here."""
    return {
        "base": _solve(BASE, I_PROBE),
        "split": _solve(SPLIT, I_PROBE / 2.0),
    }


@pytest.mark.slow
def test_series_strips_double_the_turns_and_quadruple_the_resistance(runs):
    """N strips are N times the turns.  At I/N the MMF — and therefore the
    torque and the copper watts — come back, while ψ, the voltage and the
    resistance carry the ×N and ×N² the user is trading current for.

    The AC copper is measured on the STRIP: same MMF, same field, half the
    conductor width, so the solved ∫σE² falls.  The strip and its current here
    are exactly what they were under the retired PARALLEL wiring at the full
    phase current (I/(paths·k·N) either way), which is why this one number
    survives the flag's removal unchanged.
    """
    b, s = runs["base"], runs["split"]

    assert s["wire_split"] == 2
    assert "wire_split_series" not in s, "the removed flag is back in the result"
    assert s["turns_per_coil"] == 2 * b["turns_per_coil"]
    # the strips add no parallel path: each carries the full branch current
    assert s["n_parallel_eff"] == b["n_parallel_eff"]

    t_b, t_s = _mean(b["T_avg_Nm"]), _mean(s["T_avg_Nm"])
    assert abs(t_b) > 1e-3, "probe torque is noise — the ratio would be too"
    assert t_s == pytest.approx(t_b, rel=0.02), (t_b, t_s)
    assert _amp(s["psi_A_Wb"]) == pytest.approx(2 * _amp(b["psi_A_Wb"]), rel=0.02)
    assert s["R_phase_ohm"] == pytest.approx(4 * b["R_phase_ohm"], rel=0.02)
    assert _mean(s["P_cu_dc_W"]) == pytest.approx(_mean(b["P_cu_dc_W"]), rel=0.02)

    # d_r halves and the width-direction proximity term goes as d_r², so the
    # honest expectation is ~4x; 1.3x is the floor a wrong sign or a lost
    # factor could not clear.
    assert _cu_ac(b) > 0.0 and _cu_ac(s) > 0.0
    assert _cu_ac(b) / _cu_ac(s) >= 1.3, (_cu_ac(b), _cu_ac(s))


@pytest.mark.slow
def test_the_summary_no_longer_says_the_solve_ignores_the_split(runs):
    """(e) The flag was the honest report of a solve that was handed the whole
    bar while the loss model described strips.  The mesher gets the strips now,
    so there is nothing left to ignore — and the card must not keep warning
    about it."""
    from motor_ai_sim.routes.simulation import _build_transient_summary

    s = _build_transient_summary(
        runs["split"], I_phase_rms=float(runs["split"].get("I_phase_rms_A")
                                         or I_PROBE / 2.0),
        gamma_deg=0.0, coil_temp_c=120.0, geo_override=dict(SPLIT))
    assert s["wire_split"] == 2
    assert s["cu_ac_solved_ignores_wire_split"] is False
    # the numbers that make the split visible on the card
    assert s["turns_per_coil"] == runs["split"]["turns_per_coil"]
    assert "wire_split_series" not in s, "the removed flag is back on the card"
    assert s["R_phase_ohm"] > 0.0 and s["KV_rpm_per_V_line"] > 0.0


# ─────────────────────────────────────────────────────────────────────────────
# (f) a split that does not fit
# ─────────────────────────────────────────────────────────────────────────────

def test_a_split_that_does_not_fit_the_slot_is_refused_by_name():
    """Three 1 mm strips need 3.4 mm of column on a machine whose tooth pitch
    leaves room for 2.24 — the slot cutter would meet its neighbour and the
    tooth between them would be gone.  Refused on ``wire_split``, with the
    numbers and the largest N that does fit."""
    from motor_ai_sim.geometry_validation import (split_width_error,
                                                  validate_parameter_values)

    too_many = dict(BASE, wire_width=1.0, wire_split=6)
    err = split_width_error(too_many)
    assert err is not None
    field, msg, bound = err
    assert field == "wire_split"
    assert "wire_split = 6" in msg
    assert "wire_spacing_x" in msg and "tooth" in msg
    assert 1 <= bound < 6
    # …and the bound it names actually fits, while one more does not
    assert split_width_error(dict(too_many, wire_split=int(bound))) is None
    assert split_width_error(dict(too_many, wire_split=int(bound) + 1)) is not None

    bad = validate_parameter_values(too_many)
    assert [b["field"] for b in bad] == ["wire_split"]
    assert bad[0]["kind"] == "derived" and bad[0]["max"] == bound

    # the fixture's own split fits, and says so by staying silent
    assert split_width_error(SPLIT) is None


def test_the_series_flag_is_gone_and_a_machine_carrying_it_still_loads():
    """``wire_split_series`` is REMOVED, not defaulted (user 2026-09-08).

    Two halves, and both matter: no code path may still read it (a lingering
    reader would be a second, silent definition of the winding), and a die or
    sweep config saved during the hours it existed must still go through the
    PUT — it is a RETIRED key, accepted and dropped, exactly like `magnet_top`.
    """
    import motor_ai_sim.winding as _w
    from motor_ai_sim.geometry_validation import validate_parameter_values
    from motor_ai_sim.routes._validation import (RETIRED_GEOMETRY_KEYS,
                                                 SCHEMA_FALLBACK,
                                                 check_unknown_geometry_keys,
                                                 geometry_schema_meta)

    assert not hasattr(_w, "wire_split_series_from_geo")
    assert "wire_split_series_from_geo" not in _w.__all__
    assert "wire_split_series" not in SCHEMA_FALLBACK
    assert "wire_split_series" not in geometry_schema_meta()

    # the stale key is tolerated, not resurrected: no 422, no field error
    assert "wire_split_series" in RETIRED_GEOMETRY_KEYS
    assert check_unknown_geometry_keys({"wire_split_series": 1}) == []
    assert validate_parameter_values({"wire_split_series": 1}) == []
    # …and it changes nothing about the machine it rides on
    assert turns_per_coil(dict(SPLIT, wire_split_series=0)) == turns_per_coil(SPLIT)
    assert n_parallel_effective(1, dict(SPLIT, wire_split_series=0)) == \
        n_parallel_effective(1, SPLIT)


def test_the_knob_reaches_the_geometry_tab_and_the_old_text_does_not():
    """A knob the solver honours and the UI cannot show does not exist for the
    user — and the stored per-machine schema still explains the RETIRED split
    (strips of wire_width/N, transposed, no CAD), so the server owns that
    sentence now.  It must also say the strips are SERIES turns: that is the
    whole electrical meaning of the knob and there is no second knob to carry
    it any more."""
    from motor_ai_sim.routes._validation import (SCHEMA_DESCRIPTION_OVERRIDE,
                                                 geometry_schema_meta)

    meta = geometry_schema_meta()
    ws = meta["wire_split"]["description"]
    assert ws == SCHEMA_DESCRIPTION_OVERRIDE["wire_split"]
    assert "wire_width/N" not in ws and "transposed" not in ws.lower()
    assert "2 x wire_spacing_x" in ws
    assert "SERIES" in ws and "wire_split_series" not in ws


# ─────────────────────────────────────────────────────────────────────────────
# (h) the passport carries the split, so Configure scales the right turns
# ─────────────────────────────────────────────────────────────────────────────

def test_the_passport_records_the_split_and_the_scaling_uses_the_real_turns():
    """A passport's ``N0`` is wire ROWS, and rows stopped being turns.

    Configure picks a reference passport by CROSS-SECTION, not by build, so a
    passport measured unsplit can legitimately be scaled onto a machine that is
    split — and the turns ratio would then be short by exactly N.  The fix is
    two halves: the passport records ``wire_split0``, and the scaling multiplies
    by the split on BOTH sides instead of comparing rows to rows.

    A source contract, not a solve: generating a passport is a dozen FEM runs,
    and what is at stake here is bookkeeping that a reader can break silently.
    """
    import inspect
    import pathlib as _pl

    from motor_ai_sim import datasheet as _ds
    from motor_ai_sim import passport as _pp

    # 1 — the passport records it, from the SAME reader the solver uses
    src = inspect.getsource(_pp)
    assert '"wire_split0": wireSplit0' in src
    assert "wireSplit0 = float(_ws_geo(g))" in src
    assert "wire_split_from_geo as _ws_geo" in src
    # …and says out loud that an old passport without it means 1
    assert "ABSENT MEANS 1" in src

    # 2 — the datasheet reads it with that default and puts it IN the turns
    dsrc = inspect.getsource(_ds)
    assert 'ws0 = max(1.0, float(pp.get("wire_split0") or 1))' in dsrc
    assert "turns0 = (N0 / wp0) * ws0" in dsrc

    # 3 — the web scaler declares it and multiplies BOTH sides by a split
    ts = (_pl.Path(__file__).resolve().parents[1] / "web" / "src" / "lib"
          / "motorScaling.ts").read_text(encoding="utf-8")
    assert "wire_split0?: number;" in ts
    i = ts.index("export function turnsFactor")
    body = ts[i:i + 700]
    assert "p.wire_split0 ?? 1" in body and "k.split ?? s0" in body
    assert "((k.N / kPar) * s) / ((p.N0 / kPar0) * s0)" in body


# ─────────────────────────────────────────────────────────────────────────────
# (g) the thermal map sees every strip
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.slow
def test_the_thermal_map_covers_every_strip():
    """The steady thermal map runs on the STRUCTURED template mesh, whose slot
    column is built from the geometry parameters rather than from the CAD
    polygons — so a split the template did not know about would put half the
    copper in the map and heat a machine with one strip per row.

    Measured against the CAD's own conductor area: the map's copper must be the
    drawn copper, to 1 %.
    """
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.routes import thermal as th
    from tests.test_thermal_routes import FAST, store_em_run

    geo = dict(SPLIT)
    mp = pytest.MonkeyPatch()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="wire_split_thermal_"))
    mp.setattr(sim, "_transient_field_store_path", lambda: str(tmp / ".snap.pkl"))
    mp.setattr(th, "_last_store_path", lambda: str(tmp / ".last_thermal.pkl"))
    mp.setattr(th, "_loss_maps_path", lambda: str(tmp / ".loss_maps.pkl"))
    mp.setattr(th, "_LOSS_MAPS_LOADED", True, raising=True)
    saved = dict(sim._transient_field_snap)
    sim._transient_field_snap.clear()
    th._LOSS_MAPS.clear()
    try:
        store_em_run(geo, run_id="2026-09-08T09:00:00")
        time.sleep(0.3)          # the persist is a daemon thread — let it land
        r = TestClient(app).get(
            "/api/thermal/field",
            params={**FAST, "geo": json.dumps(geo), "cooling_mode": "air",
                    "air_speed_mps": 10.0, "ambient_c": 40.0})
        assert r.status_code == 200, r.text[:800]
        j = r.json()
    finally:
        sim._transient_field_snap.clear()
        sim._transient_field_snap.update(saved)
        th._LOSS_MAPS.clear()
        mp.undo()

    V = np.asarray(j["vertices"], float)          # metres
    T = np.asarray(j["triangles"], int)
    D = np.asarray(j["domain_per_tri"], int)

    def _area_mm2(mask) -> float:
        t = T[mask]
        if not len(t):
            return 0.0
        a, b, c = V[t[:, 0]], V[t[:, 1]], V[t[:, 2]]
        return float(np.abs(np.cross(b - a, c - a)).sum() / 2.0) * 1e6

    budget = j["cooling"]["heat_budget"]
    sym = float(budget["symmetry_mult"])
    coil_mm2 = _area_mm2((D == 2) | (D >= 200)) * sym
    _m, polys = _build(geo)
    assert coil_mm2 == pytest.approx(_coil_area_mm2(polys), rel=0.01), \
        (coil_mm2, _coil_area_mm2(polys))

    # …and the WATTS put into that copper are the run's, not N times them.  The
    # copper heat density is Pcu/V_cu and V_cu was built on the wire ROWS: with
    # the strips all present in the mesh but only half of them counted in the
    # volume, the map deposited 2×Pcu into the winding (measured 13.15 W on a
    # 10.72 W machine, 2026-09-08).
    assert budget["losses_W"] == pytest.approx(budget["em_loss_total_W"], rel=0.01)
    assert budget["residual_pct"] < 2.0, budget

    # …and the enamel between the strips is meshed as coating, not left as a
    # hole: the split's own gaps are the reason this area is not zero.
    assert _area_mm2((D == 10) | (D == 62)) > 0.0
