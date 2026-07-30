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

  Speed is the exception that has to go through the config, because
  ``fem_transient_sliding_band`` takes no rpm argument and reads
  ``simulation.rpm`` itself (docs/SOLVER_TRIALS_2026-07-30.md F2). That leak was
  live and it bit: the pins were generated at 15000 rpm (this machine's own
  stored speed), the user's config moved to 13000 for the 40 mm design, and ALL
  SIX cases went red at once with nothing wrong in the code —
  ``p2_noload`` V_peak -13.33 % (= 13000/15000 exactly), its P_cu -24.89 %
  (= the same ratio squared, the AC term is proportional to f^2), P_fe -19.8 %,
  and the voltage cases +109 % in current because a fixed 7.0 V pk against a
  smaller back-EMF draws whatever the impedance allows. A gate that goes red
  when the user edits an unrelated field is not a gate, so ``_run`` pins the
  speed the same way the trials harness does: patch the in-memory config only,
  never the file. rpm is read ONCE per solve (fem_solver_2d.py:1661, before the
  frame loop), so the patch cannot be lost mid-solve.
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

from motor_ai_sim.config import get_config
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
# operating speed (config/motor_presets.json `my_motor`: 32 A, 15000 rpm). It has
# to be pinned here rather than passed as an argument because the solver has no
# rpm parameter — see the docstring.
RPM = 15000.0


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
    # Speed: the ONE input with no argument channel (see the docstring). Patch the
    # cached dict, never the YAML — the file is the user's, and a live backend is
    # often writing it. Read once per solve, so this cannot be dropped mid-solve.
    cfg = get_config()
    cfg["simulation"]["rpm"] = RPM
    # The winding CONNECTION is part of the d-axis calibration topology key
    # (fem_solver_2d._daxis_topology_key). The pins were generated at 2S; when
    # the user toggled the live config to 4S, the calibration re-ran on the new
    # key and the re-measured d-axis moved by its numerical noise — amplitude
    # metrics stayed inside tolerance but I_A_thd_pct drifted +1.1-1.2 % on
    # BOTH voltage cases with every other number byte-identical. Same leak
    # class as rpm: pin it on the cached dict, never touch the user's file.
    cfg.setdefault("winding", {})
    cfg["winding"]["connection"] = "2S"
    cfg["winding"]["n_series"] = 2
    kw = dict(CASES[case])
    try:
        d = fem_transient_sliding_band(geo_override=dict(GEO_30MM), **kw)
    finally:
        set_request_materials(None)
    return _metrics(d)


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
