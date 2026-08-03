"""Physics regression: pin the numbers the solver produces today.

This suite does NOT judge whether a number is physically right — it judges
whether a code change moved it. That is the gate every refactoring step needs:
the solver is one very long function full of closures over mesh, slip pairing
and saturation state, and the only reason the ~35 % torque inflation on the
retired P1 path survived so long is that nothing was watching the numbers.

Design notes, in case a case starts failing for the wrong reason:

* **Config-independent.** Geometry goes in through ``geo_override``, the magnet
  through the per-request material override, and the SPEED through ``RPM`` below,
  so editing ``config/motor_config.yaml`` (which the user does constantly) cannot
  turn a red test into a code problem. Mutating the cached config to carry the
  geometry or the magnet was tried and is NOT equivalent: the cache follows the
  file now, so a live API server autosaving mid-run reloaded it and dropped the
  override — the suite passed alone and failed in a batch.

  Speed used to be the exception that had to go through the config, because
  ``fem_transient_sliding_band`` took no rpm argument and read
  ``simulation.rpm`` itself (docs/SOLVER_TRIALS_2026-07-30.md F2). It is an
  explicit ``rpm=`` argument now, so ``_run`` passes it like everything else.
  Keeping the story here because the leak was live and it bit: the pins were
  generated at 15000 rpm (this machine's own
  stored speed), the user's config moved to 13000 for the 40 mm design, and ALL
  SIX cases went red at once with nothing wrong in the code —
  ``p2_noload`` V_peak -13.33 % (= 13000/15000 exactly), its P_cu -24.89 %
  (= the same ratio squared, the AC term is proportional to f^2), P_fe -19.8 %,
  and the voltage cases +109 % in current because a fixed 7.0 V pk against a
  smaller back-EMF draws whatever the impedance allows. A gate that goes red
  when the user edits an unrelated field is not a gate.
* **Coarse on purpose.** 12 steps/period on a 1.4 mm mesh. These are not
  publication numbers; they only have to be *reproducible*. Keeping the suite
  near five minutes is what makes it get run.
* **One element order.** P2 is the only basis. P1 was deleted (its
  Maxwell-stress mean torque is radius-inconsistent under load, ~35 % high, and
  its ripple is a mesh staircase), so the p1_* cases went with it. What they
  were guarding did not: every capability they pinned — cogging, demag, voltage
  drive, the coupled sigma*dA/dt eddy solve, and eddy+voltage together — has a
  p2_* case here.

Regenerate the baseline deliberately, never casually — a diff here is the whole
point of the file::

    UPDATE_PHYSICS_BASELINE=1 python -m pytest tests/test_physics_regression.py

Then READ the printed diff and justify every line of it in the commit message.
"""
from __future__ import annotations

import io
import json
import math
import os
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pytest

from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

BASELINE = Path(__file__).with_name("physics_baseline.json")
UPDATE = os.environ.get("UPDATE_PHYSICS_BASELINE") == "1"

# Relative tolerance. 0.5 % is wide enough to absorb BLAS/threading jitter in
# the sparse solve and narrow enough that any real physics change trips it.
RTOL = 5e-3
# Quantities that legitimately sit near zero (cogging ripple, shaft loss) need an
# absolute floor or the relative test divides noise by noise.
ATOL = {"T_ripple_pct": 0.05, "P_shaft_W": 0.05, "P_solid_W": 0.05,
        "P_core_W": 0.05, "demag_br_min": 0.01, "demag_br_mean": 0.01,
        # Honest (frequency-domain) rotor eddy: the shaft term is sub-watt for
        # the same reason P_shaft_solve_W is, so it needs an absolute floor.
        "P_shaft_honest_W": 0.05,
        # Coupled-eddy shaft loss: the shaft sits under the magnets and the
        # back iron, so in the rotor frame it sees almost no AC field at all —
        # milliwatts, i.e. a relative test on it divides noise by noise.
        "P_shaft_solve_W": 0.05,
        # Voltage drive: these two are ~0 by construction (a settled orbit has
        # no DC current, a converged circuit has no residual), so a relative
        # test on them divides noise by noise.
        "v_dc_residual_A": 0.05, "v_circuit_resid_max_V": 1e-9}

# A 30 mm 12s14p spoke machine, pinned field by field so the suite does not
# inherit whatever is in the working config.
GEO_30MM: Dict[str, float] = {
    "stator_diameter": 30.0, "slot_height": 4.3, "core_thickness": 1.5,
    "num_seg": 2, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.2, "tooth_width": 2.6, "tooth2_width": 1.4, "cut_width": 1.5,
    "insulation_thickness": 0.05, "wire_width": 2.0, "wire_height": 0.5,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.1, "num_wires_per_slot": 6,
    "wire_split": 1, "slot_hs": 0.267, "magnet_height": 4.5,
    "rotor_house_height": 0.8, "shaft_height": 2.0, "magnet_fill_down": 0.9,
    "magnet_fill_up": 0.3, "magnet_fill_radius": 0.1, "magnet_up_gap": 0.1,
    "rotor_hole": 0.7, "magnet_down_height": 1.4, "magnet_lamination": 0,
    "stator_fillet_r": 1.2, "stator_fillet_r1": 0.0, "rotor_fill_r": 0.2,
    "motor_length": 10.0,
}

COMMON = dict(
    n_steps_per_period=12, n_periods=1.0, mesh_size_mm=1.4, min_size_mm=0.35,
    gap_layers=1.0, n_sectors=2, structured_gap=True, iron_template=True,
    geo_mesh=True, coil_temp_c=120.0, rotor_eddy=False,
)

CASES = {
    # The default basis: energy-consistent mean torque, mesh-convergent ripple.
    "p2_load": dict(COMMON, element_order=2, demag=False,
                    I_phase_rms=60.0, gamma_deg=0.0),
    # No-load cogging: the one number the flux-linkage torque cannot see. At
    # I=0 the energy torque is identically zero, so T_avg/T_ripple here come
    # from the raw Maxwell series alone — nothing else in the suite exercises
    # that path unloaded. Pinned BEFORE P1 was deleted (the retired p1_noload
    # used to be its only watcher), never after.
    "p2_noload": dict(COMMON, element_order=2, demag=False,
                      I_phase_rms=0.0, gamma_deg=0.0),
    # Irreversible demagnetisation — pins the load-line construction AND the
    # settling pass. P2 could not do it at all until the hook was moved to where
    # the frame actually converges (P2 solves by Newton; the hook sat in the
    # Picard fallback, which never runs). Pinned because THREE separate
    # improvements — iron loss, demag, the settling pass — were added to the P1
    # path and silently missed P2, each time because the P2 branch returned
    # before that code.
    # This case is slow (demag on P2 re-solves a frame until the magnet settles)
    # and coarsening it does NOT help — but not for the reason first suspected.
    # Measured: 1.4 mm and 2.2 mm both give 8195 triangles, while 0.9 mm gives
    # 8481, with the template path ON and OFF alike. mesh_size_mm IS honoured;
    # this geometry simply imposes a floor (0.2 mm air gap, fillets, a 2 mm wire)
    # that already demands finer than 1.4 mm, so asking for coarser is a no-op.
    # The cost is the demag outer loop, not the mesh.
    "p2_demag": dict(COMMON, element_order=2, demag=True,
                     I_phase_rms=60.0, gamma_deg=0.0),
    # Voltage drive. (7.0 V pk, +10 deg) puts this machine near i_d = 0 at
    # ~90 A pk, i.e. the same loaded, saturated, gamma~0 corner the current-drive
    # cases use. These are the slowest cases in the suite: the currents are
    # STATE, so each run marches 10 settling periods before the reported one.
    "p2_voltage": dict(COMMON, element_order=2, demag=False,
                       I_phase_rms=60.0, gamma_deg=0.0, drive="voltage",
                       v_phase_peak=7.0, v_delta_deg=10.0),
    # The coupled sigma*dA/dt solve on P2: solid copper wires with their net
    # current imposed per wire, magnets and shaft with zero net axial current,
    # all inside the field solve.  This is the case that pins the LOSS numbers
    # that stop being a model when it runs — P_cu_ac and P_mag come from
    # sigma*int(E^2) of the solved field, so a change in the constraint set, the
    # time discretisation or the conductor bookkeeping moves them immediately.
    # rotor_eddy is ON (unlike every other case): it is what puts the magnet and
    # shaft conductivity into the coupled system.
    "p2_eddy": dict(COMMON, element_order=2, demag=False, eddy=True,
                    rotor_eddy=True, I_phase_rms=60.0, gamma_deg=0.0),
    # demag AND the coupled eddy solve TOGETHER -- the combination the UI has
    # defaulted to since 3eae1ee, and the one nothing here watched: p2_demag runs
    # eddy off, p2_eddy runs demag off, so each half was pinned and the pair was
    # not. That gap hid a silent substitution for as long as it existed. The
    # demag re-assembly rebuilt the magnetostatic RHS only, while the bordered
    # eddy Newton kept building its own right-hand side from the source it was
    # handed before the frame loop -- the PRISTINE magnet. Measured on the 40 mm
    # Fe16N2 machine (docs/SOLVER_TRIALS_2026-07-30.md F1): demag on/off moved
    # the torque by 0.0 % with eddy on and by -69 % with eddy off, while the
    # demag map reported 98 % of the magnet de-rated either way.
    #
    # What makes this case a GUARD rather than a duplicate: its T_avg must stay
    # far below p2_eddy's (same current, same mesh, weakened magnet). If the
    # de-rating ever stops reaching the eddy system again, this pin snaps back
    # up toward p2_eddy and the suite says so. rotor_eddy is ON, as in p2_eddy,
    # so the magnet conductivity is inside the coupled system while its
    # remanence is being de-rated -- the two mechanisms that must not shadow
    # each other.
    "p2_demag_eddy": dict(COMMON, element_order=2, demag=True, eddy=True,
                          rotor_eddy=True, I_phase_rms=60.0, gamma_deg=0.0),
    # The two hardest features TOGETHER: the coupled sigma*dA/dt solve, whose
    # constraint rows IMPOSE each wire's current, driven by the voltage circuit,
    # for which those same currents are UNKNOWNS. They are solved as one
    # bordered (A, U, i_A, i_B) Newton. This case is the guard against the exact
    # regression the retired P1 path shipped for years -- an `if eddy: ... elif
    # vdrive:` chain that skips the circuit and reports a CURRENT-drive answer
    # as a voltage run. If that ever comes back, the pinned currents move to the
    # imposed 60 A rms sinusoid and the suite says so. The paired diagnostics are
    # what make it airtight: v_circuit_resid_max_V is ~0 only if the circuit was
    # actually solved on the converged field, and P_cu_ac_solve_W is nonzero only
    # if the eddy reaction was actually in it. rotor_eddy is off because the
    # voltage drive drops it (see the warning in the solver), so this pins the
    # copper constraint set specifically.
    "p2_voltage_eddy": dict(COMMON, element_order=2, demag=False, eddy=True,
                            I_phase_rms=60.0, gamma_deg=0.0, drive="voltage",
                            v_phase_peak=7.0, v_delta_deg=10.0),
}

MAGNET = "F45SH_120C"

# The speed every pin was generated at, and the 30 mm machine's OWN stored
# operating speed (config/motor_presets.json `my_motor`: 32 A, 15000 rpm).
# Passed as the solver's ``rpm=`` argument (F2).
RPM = 15000.0

# The winding connection every pin was generated at: 2 coils per phase in
# series, so ONE parallel path. Passed as the solver's ``connection=`` argument
# (F3) — it sets n_parallel (which divides the coil current) and the d-axis
# calibration key.
CONNECTION = "2S"


def _scalar(v: Any) -> float:
    """T_em_Nm comes back as a per-frame series; the pinned value is its mean."""
    if isinstance(v, (list, tuple)):
        a = np.asarray(v, float)
        return float(a.mean()) if a.size else 0.0
    return float(v)


def _metrics(d: Dict[str, Any]) -> Dict[str, float]:
    out = {
        "T_avg_Nm": _scalar(d.get("T_avg_Nm", d.get("T_em_Nm", 0.0))),
        "T_ripple_pct": float(d.get("T_ripple_pct", 0.0)),
        "P_cu_W": float(np.mean(d.get("P_cu_W", 0.0)) if isinstance(d.get("P_cu_W"), list)
                        else d.get("P_cu_W", 0.0)),
        "P_fe_W": float(np.mean(d.get("P_fe_W", 0.0)) if isinstance(d.get("P_fe_W"), list)
                        else d.get("P_fe_W", 0.0)),
        "V_peak": float(d.get("V_peak", 0.0)),
    }
    # The raw Maxwell mean is pinned separately: it is the diagnostic that makes
    # a regression in the hybrid distinguishable from one in the field solve.
    if d.get("T_avg_maxwell_Nm") is not None:
        out["T_avg_maxwell_Nm"] = float(d["T_avg_maxwell_Nm"])
    # Voltage drive: the currents are the ANSWER, not the input, so they are the
    # first thing to pin — a broken circuit shows up in the waveform long before
    # it moves the mean torque. v_dc_residual_A is the settling gauge (~0 A on a
    # converged periodic orbit); it has an absolute floor because it is a
    # near-zero quantity by construction.
    if d.get("drive") == "voltage":
        ia = np.asarray(d.get("I_A") or [0.0], float)
        F = np.abs(np.fft.rfft(ia - ia.mean())) * 2.0 / max(ia.size, 1)
        out["I_A_peak_A"] = float(np.max(np.abs(ia)))
        out["I_A_fund_A"] = float(F[1]) if F.size > 1 else 0.0
        out["I_A_thd_pct"] = (float(np.sqrt(np.sum(F[2:] ** 2)) / F[1] * 100.0)
                              if F.size > 2 and F[1] > 0 else 0.0)
        out["v_dc_residual_A"] = float(d.get("v_dc_residual_A") or 0.0)
        out["v_circuit_resid_max_V"] = float(
            max((d.get("v_drive_diag") or {}).get("resid") or [0.0]))
    # Coupled eddy solve: pin the four numbers that only exist because it ran.
    # Gated on the flag the P2 branch sets, so no other case grows a key (the
    # suite fails a case whose metric set changes, which is the point).
    if d.get("eddy_coupled"):
        out["P_cu_total_solve_W"] = float(d.get("P_cu_total_solve_W") or 0.0)
        out["P_cu_ac_solve_W"] = float(d.get("P_cu_ac_solve_W") or 0.0)
        out["P_mag_solve_W"] = float(d.get("P_mag_solve_W") or 0.0)
        out["P_shaft_solve_W"] = float(d.get("P_shaft_solve_W") or 0.0)
    # The frequency-domain "honest" rotor eddy solve (eddy_solver_2d.
    # honest_rotor_eddy). It runs whenever rotor_eddy is on and is where the
    # savgol prefilter and the k <= 16 harmonic ceiling live — and NOTHING here
    # watched it: p2_load has rotor_eddy off, and in p2_eddy the coupled solve
    # REPLACES the reported magnet/shaft numbers, so a change in that filter
    # chain moved no pin at all. It does now. Gated on the honest path's own
    # flag so no other case grows a key. (Its value is already computed in
    # p2_eddy, so this pins it at zero extra runtime.)
    if float(d.get("P_mag_honest_W") or 0.0) > 0.0:
        out["P_mag_honest_W"] = float(d["P_mag_honest_W"])
        out["P_shaft_honest_W"] = float(d.get("P_shaft_honest_W") or 0.0)
    f = d.get("demag_field")
    if f:
        br = np.asarray(f["demag_coef_per_tri"], float)
        dom = np.asarray(f["domain_per_tri"], int)
        m = np.isin(dom, f["mag_domains"])
        if m.any():
            out["demag_br_min"] = float(br[m].min())
            out["demag_br_mean"] = float(br[m].mean())
    return out


def _run(case: str) -> Dict[str, float]:
    # Force the magnet through the per-request override, NOT by mutating the
    # cached config. The cache now follows the file, so anything touching
    # motor_config.yaml mid-run — a live API server, an editor — reloads it and
    # silently drops an in-process mutation. That made this suite pass alone and
    # fail in a batch, which is worse than failing outright.
    set_request_materials({"assignment": {"magnet": MAGNET}, "materials": {}})
    # Speed and the winding connection are ARGUMENTS now (F2/F3). They used to
    # have to go through the cached config because the solver read
    # simulation.rpm and the winding block itself; the explicit parameters make
    # the pins independent of the user's file with no patching at all. Same
    # numbers, same physics — this is a channel change.
    kw = dict(CASES[case])
    try:
        # The winding CONNECTION is an ARGUMENT now (F3), like rpm. It is part
        # of the d-axis calibration topology key (fem_solver_2d.
        # _daxis_topology_key) AND it sets n_parallel, which divides the coil
        # current. The pins were generated at 2S (n_parallel 1); when the user
        # toggled the live config to 4S, the calibration re-ran on the new key
        # and the re-measured d-axis moved by its numerical noise — amplitude
        # metrics stayed inside tolerance but I_A_thd_pct drifted +1.1-1.2 % on
        # BOTH voltage cases with every other number byte-identical. It used to
        # be pinned by patching the cached config dict; passing it removes the
        # last shared-config read this suite depended on.
        d = fem_transient_sliding_band(geo_override=dict(GEO_30MM), rpm=RPM,
                                       connection=CONNECTION, **kw)
    finally:
        set_request_materials(None)
    return _metrics(d)


def test_winding_carries_exactly_n_wires_times_i():
    """The winding source integrates to n_wires·I_coil per slot. By construction.

    This is the invariant the pins above are only meaningful under, and it is
    cheap (polygons only, no mesh, no solve) so it runs in the fast suite.

    It was FALSE for as long as the source was J = dir·I·n_wires / (slot_width ·
    slot_height · fill_factor): that rectangle comes from the WIRE pitch and the
    fill factor was a MotorDomainParams dataclass default (0.6) that no config
    path ever set, while J was applied over the real copper. Every machine was
    solved at k·N·I with k = A_copper_per_slot / that rectangle ∈ 0.909…1.265,
    and T_maxwell/T_energy was exactly k (docs/SOLVER_TRIALS_2026-07-30.md,
    F4+F5). On this geometry k was 1.0111 — the whole 30 mm design line was
    optimised against a Maxwell torque 1.1 % too high.

    Note the trap this checks past: the CAD emits ONE POLYGON PER WIRE (72 for
    12 slots × 6 wires), so normalising each polygon by its OWN area would be
    wrong by n_wires. The divisor has to be the SLOT's copper.
    """
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.simulation.fem_solver_2d import (
        build_materials, coil_copper_areas, coil_slot_index, _params_from_geo_dict)
    from motor_ai_sim.simulation.geometry_2d import MotorDomains2D

    p = _params_from_geo_dict(dict(GEO_30MM))
    layout = MotorDomains2D(p).winding_layout
    n_slot = len(layout)
    motor = CadQueryMotor()
    motor.set_parameters(dict(GEO_30MM))
    polys = motor.get_2d_polygons(rotor_angle_deg=0.0)

    n_wires = int(GEO_30MM["num_wires_per_slot"])
    I_ph = {"A": 37.0, "B": -11.0, "C": -26.0}     # any unbalanced triple
    mats = build_materials(I_ph, layout, polys, 0.0,
                           p.slot_width_m * p.slot_height_m * p.fill_factor,
                           n_wires)
    areas = coil_copper_areas(polys, n_slot)
    assert areas, "no coil polygons — the invariant below would be vacuous"

    at = {}
    for i, cp in enumerate(polys.get("coils") or []):
        s = coil_slot_index(cp, n_slot)
        tag = 200 + i                                     # DOM_COIL_BASE + i
        if s is None or tag not in areas or tag not in mats:
            continue
        at[s] = at.get(s, 0.0) + mats[tag].J_z * areas[tag][0]

    assert len(at) == n_slot, f"only {len(at)} of {n_slot} slots carry copper"
    for s, got in sorted(at.items()):
        phase, direction = layout[s]
        want = direction * I_ph[phase] * n_wires
        assert math.isclose(got, want, rel_tol=1e-9, abs_tol=1e-9), (
            f"slot {s} ({'+' if direction > 0 else '-'}{phase}) is excited at "
            f"{got:.6f} At, asked for {want:.6f} At "
            f"(k = {got / want if want else float('nan'):.4f})")


@pytest.fixture(scope="module")
def baseline() -> Dict[str, Dict[str, float]]:
    if BASELINE.exists():
        return json.loads(BASELINE.read_text(encoding="utf-8"))
    if not UPDATE:
        pytest.skip("no baseline — run with UPDATE_PHYSICS_BASELINE=1 to create it")
    return {}


@pytest.mark.slow
@pytest.mark.parametrize("case", sorted(CASES))
def test_case_matches_baseline(case: str, baseline: Dict[str, Dict[str, float]]):
    got = _run(case)
    if UPDATE:
        pytest.skip("baseline update run — see regenerate_baseline")
    assert case in baseline, (
        f"case {case!r} has no baseline; regenerate with "
        f"UPDATE_PHYSICS_BASELINE=1")
    want = baseline[case]
    bad = []
    for k, wv in want.items():
        gv = got.get(k)
        if gv is None:
            bad.append(f"  {k}: MISSING (was {wv:.6g})")
            continue
        tol = max(abs(wv) * RTOL, ATOL.get(k, 0.0))
        if abs(gv - wv) > tol:
            drift = (gv - wv) / wv * 100 if wv else float("inf")
            bad.append(f"  {k}: {wv:.6g} -> {gv:.6g}  ({drift:+.2f} %)")
    for k in got:
        if k not in want:
            bad.append(f"  {k}: NEW ({got[k]:.6g})")
    assert not bad, (
        f"physics moved in case {case!r}:\n" + "\n".join(bad) +
        "\n\nIf the change is intended, regenerate with "
        "UPDATE_PHYSICS_BASELINE=1 and justify every line above in the commit.")


# ── coupled-eddy warm-up: settle until quiet ────────────────────────────────
# The reported window has to start on a machine that is already running. The
# sigma*dA/dt history starts from A_prev = 0 — a field the machine was never in
# — so the first frames carry a decaying start-up transient, and it is the SOLID
# conductors (magnet, shaft) that carry it longest: their diffusion time is
# sigma*mu*r^2, which on a 78 mm rotor assembly is ~1500x a 5 mm one's. With the
# warm-up pinned at 2 frames (tuned on the 40 mm), the 150 mm 24s28p machine
# reported a solid loss of 262 W whose settled value is 68 W — the average of a
# transient, not of a machine. The fix is to keep solving discarded frames until
# the transient is quiet; these tests pin that it IS quiet, and that the run says
# how it got there.

def _eddy_run(**over: Any) -> Dict[str, Any]:
    """One coupled-eddy transient on the 30 mm machine (raw solver dict)."""
    kw = dict(CASES["p2_eddy"]); kw.update(over)
    set_request_materials({"assignment": {"magnet": MAGNET}, "materials": {}})
    try:
        return fem_transient_sliding_band(geo_override=dict(GEO_30MM), rpm=RPM,
                                          connection=CONNECTION, **kw)
    finally:
        set_request_materials(None)


def _solid_series(d: Dict[str, Any]) -> np.ndarray:
    """Per-frame magnet + shaft sigma*E^2 of the REPORTED window [W]."""
    return (np.asarray(d.get("P_mag_eddy_W") or [], float)
            + np.asarray(d.get("P_shaft_eddy_W") or [], float))


def test_eddy_settle_resid_reports_the_tail_not_the_last_step():
    """The settling gauge measures what is LEFT, and ripple is not decay.

    The three sequences below are MEASURED cold-start solid-loss series (W) from
    the three machines this fix was developed on, run with SB_EDDY_WARM=0 so
    every frame is a warm-up frame. They are the whole reason the criterion is
    not the naive "|dP|/P between the last two frames":

    * the 30 mm drops 199 -> 1.5 W in ONE step, so the naive test reads 13000 %
      and calls a settled machine un-settled forever;
    * the settled 30 mm then RIPPLES 20 % frame to frame (a 3-phase machine's
      loss carries the 6th electrical harmonic), so no per-frame tolerance below
      that can ever be met;
    * the 150 mm decays with ratio ~0.9 in the tail, where a per-frame move of
      2 % means 18 % of transient is still to come.
    """
    from motor_ai_sim.simulation.sb_postproc import eddy_settle_resid

    # 30 mm 12s14p (12 steps/period): gone after one frame.  (Two warm-up
    # frames plus the first reported frame — the probe as the solver runs it.)
    r, _ = eddy_settle_resid([199.104, 1.501, 1.248], 12, 4.76e-5)
    assert r < 0.02, f"30 mm probe read as un-settled ({r:.4f})"
    # 40 mm 12s14p (36 steps/period): gone after two.
    r, _ = eddy_settle_resid([4487.788, 14.140, 2.494], 36, 1.83e-5)
    assert r < 0.02, f"40 mm probe read as un-settled ({r:.4f})"
    # 150 mm 24s28p (40 steps/period): 44 % of the solid loss is still transient.
    r, tau = eddy_settle_resid([2775.5, 1627.9, 1089.0], 40, 2.68e-5)
    assert r > 0.20, f"150 mm probe read as settled ({r:.4f})"
    assert tau is not None and tau > 0.0, "no time constant off a clean decay"
    # Pure rotor-position ripple, no decay: settled, whatever its amplitude.
    ripple = [1.24, 1.49, 1.24, 1.49, 1.24, 1.49, 1.24, 1.49, 1.24]
    r, _ = eddy_settle_resid(ripple, 12, 1e-5)
    assert r < 0.02, f"steady ripple read as a decay ({r:.4f})"
    # A SLOW decay whose per-frame move is small: not settled, because the tail
    # is not (0.98 per frame -> 2 % steps, ~100 % of the level still to come).
    slow = [100.0 * (0.98 ** i) for i in range(12)]
    r, _ = eddy_settle_resid(slow, 12, 1e-5)
    assert r > 0.10, f"a 0.98-per-frame decay read as settled ({r:.4f})"


@pytest.mark.slow
def test_eddy_warmup_leaves_no_start_up_transient_in_the_window(monkeypatch):
    """No reported frame may sit above the settled band, and the run says so.

    The assertion is "the first reported frame is inside the band the rest of the
    window occupies", NOT "within x % of the median": the solid loss legitimately
    ripples ~20 % frame to frame on this machine, so a tolerance tight enough to
    catch a start-up transient would be tripped by physics. A contaminated first
    frame is not near the band — it is 130x its top (measured below).
    """
    tol = 0.02

    # The bug, reproduced: no warm-up at all -> frame 0 IS the cold start.
    monkeypatch.setenv("SB_EDDY_WARM", "0")
    cold = _solid_series(_eddy_run())
    monkeypatch.delenv("SB_EDDY_WARM")
    assert cold.size > 3
    assert cold[0] > 10.0 * cold[1:].max(), (
        "the cold-start leg no longer shows a start-up transient, so the test "
        f"below cannot prove the warm-up removes one (first {cold[0]:.4g} W, "
        f"rest max {cold[1:].max():.4g} W)")

    d = _eddy_run()
    sol = _solid_series(d)
    assert sol.size > 3
    assert sol[0] <= sol[1:].max() * (1.0 + 2.0 * tol), (
        f"the reported window starts on a start-up transient: first frame "
        f"{sol[0]:.4g} W against a settled band of {sol[1:].min():.4g}.."
        f"{sol[1:].max():.4g} W")

    # ── the run carries HOW it got there ────────────────────────────────
    assert d["eddy_warmup_frames"] >= 2, d["eddy_warmup_frames"]
    assert d["eddy_warmup_tol"] == pytest.approx(tol)
    assert d["eddy_warmup_resid"] is not None
    assert d["eddy_warmup_resid"] <= d["eddy_warmup_tol"], (
        f"handed off with {d['eddy_warmup_resid']:.3g} of transient left "
        f"against a tolerance of {d['eddy_warmup_tol']}")
    # Solved frames = reported window + the warm-up it actually needed.
    assert (d["n_frames_solved"]
            == d["n_steps"] + d["eddy_warmup_frames"]), (
        f"{d['n_frames_solved']} solved vs {d['n_steps']} reported + "
        f"{d['eddy_warmup_frames']} warm-up")


def regenerate_baseline() -> None:
    """Recompute every case and write the baseline file, printing the diff."""
    old = (json.loads(BASELINE.read_text(encoding="utf-8"))
           if BASELINE.exists() else {})
    new: Dict[str, Dict[str, float]] = {}
    for case in sorted(CASES):
        print(f"running {case} ...", flush=True)
        new[case] = _run(case)
        for k, v in sorted(new[case].items()):
            ov = old.get(case, {}).get(k)
            if ov is None:
                print(f"    {k:22s} {v:12.6g}   (new)")
            elif abs(v - ov) > max(abs(ov) * RTOL, ATOL.get(k, 0.0)):
                print(f"    {k:22s} {ov:12.6g} -> {v:12.6g}  "
                      f"({(v - ov) / ov * 100 if ov else float('inf'):+.2f} %)")
            else:
                print(f"    {k:22s} {v:12.6g}")
    BASELINE.write_text(json.dumps(new, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(f"\nwrote {BASELINE}")


if __name__ == "__main__":
    regenerate_baseline()
