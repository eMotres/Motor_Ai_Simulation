"""Ablation of the numerical filters in the P2 transient — do they still earn it?

Each of these exists to hide a slip-band artifact that may or may not still be
there.  A filter that is no longer necessary is a bias nobody is watching, so
this script MEASURES each one on the pinned p2_load / p2_voltage recipes instead
of arguing about it:

  A  savgol pre-filter + the k <= 16 harmonic ceiling in
     eddy_solver_2d.honest_rotor_eddy  (sized for a 24s20p machine)
  B  slip-ring density: _slip_base = 1008 in fem_solver_2d
  C  the Aitken flux-anchor guards in drive.py (5e-4 flux_scale, corr > 5*drift)
  D  _snap_steps_to_nodes silently changing the requested step count

Run::

    python scripts/_filter_ablation.py [A|B|C|D ...]      # default: all

Each case prints one line per variant.  A variant that moves a reported number
by more than the regression suite's 0.5 % tolerance is the filter doing real
work; one that does not is a filter to delete.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np                                                    # noqa: E402

from motor_ai_sim.material_context import set_request_materials       # noqa: E402

GEO = {
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
COMMON = dict(n_steps_per_period=12, n_periods=1.0, mesh_size_mm=1.4,
              min_size_mm=0.35, gap_layers=1.0, n_sectors=2, structured_gap=True,
              iron_template=True, geo_mesh=True, coil_temp_c=120.0,
              rotor_eddy=False, element_order=2, demag=False,
              I_phase_rms=60.0, gamma_deg=0.0)
MAGNET = "F45SH_120C"
RESULTS: dict = {}


def run(**kw) -> dict:
    from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band
    set_request_materials({"assignment": {"magnet": MAGNET}, "materials": {}})
    try:
        return fem_transient_sliding_band(geo_override=dict(GEO), **kw)
    finally:
        set_request_materials(None)


def _m(d: dict) -> dict:
    def avg(k):
        v = d.get(k, 0.0)
        return float(np.mean(v)) if isinstance(v, list) else float(v or 0.0)
    return {"T_avg": float(d.get("T_avg_Nm", 0.0)),
            "T_ripple": float(d.get("T_ripple_pct", 0.0)),
            "P_fe": avg("P_fe_W"), "P_mag": avg("P_mag_eddy_W"),
            "P_shaft": avg("P_shaft_eddy_W"),
            "n_steps": int(d.get("n_steps", 0)),
            "n_slip": int(d.get("n_slip_nodes", 0)),
            "I_pk": float(np.max(np.abs(np.asarray(d.get("I_A") or [0.0], float)))),
            "v_dc_res": float(d.get("v_dc_residual_A") or 0.0)}


def _line(tag: str, m: dict, ref: dict | None, keys) -> None:
    parts = []
    for k in keys:
        v = m[k]
        if ref is None or not ref.get(k):
            parts.append(f"{k}={v:.5g}")
        else:
            parts.append(f"{k}={v:.5g} ({(v - ref[k]) / ref[k] * 100:+.2f}%)")
    print(f"    {tag:38s} " + "  ".join(parts), flush=True)


# ── A: rotor-eddy savgol + harmonic ceiling ─────────────────────────────────
def case_A() -> None:
    print("\nA  eddy_solver_2d.honest_rotor_eddy: savgol prefilter + n_harm<=17")
    print("   recipe = p2_load + rotor_eddy=True (eddy off, so the MODELLED "
          "magnet/shaft numbers are the ones reported)")
    import scipy.signal as _sps
    from motor_ai_sim.simulation import eddy_solver_2d as _es
    real_savgol = _sps.savgol_filter
    real_hre = _es.honest_rotor_eddy

    def variants(steps: int):
        out = {}
        for tag, no_savgol, n_harm in (("baseline (savgol, k<=16)", False, None),
                                       ("savgol OFF", True, None),
                                       ("n_harm=80 (no ceiling)", False, 80),
                                       ("savgol OFF + n_harm=80", True, 80)):
            if no_savgol:
                _sps.savgol_filter = lambda x, *a, **k: x           # noqa: E731
            if n_harm:
                def _patched(*a, n_harm=n_harm, **k):
                    k["n_harm"] = n_harm
                    return real_hre(*a, **k)
                _es.honest_rotor_eddy = _patched
            try:
                t0 = time.time()
                d = run(**{**COMMON, "rotor_eddy": True,
                           "n_steps_per_period": steps})
                out[tag] = _m(d)
                out[tag]["_s"] = time.time() - t0
            finally:
                _sps.savgol_filter = real_savgol
                _es.honest_rotor_eddy = real_hre
        return out

    for steps in (12, 36):
        print(f"  --- {steps} steps/period ---")
        v = variants(steps)
        ref = v.get("baseline (savgol, k<=16)")
        for tag, m in v.items():
            _line(tag, m, ref, ("P_mag", "P_shaft", "T_avg"))
        RESULTS[f"A_{steps}"] = v


# ── B: slip-ring density ─────────────────────────────────────────────────────
def case_B() -> None:
    print("\nB  fem_solver_2d slip density (_slip_base = 1008 -> 144 nodes/period "
          "at gap_layers=1)")
    import motor_ai_sim.simulation.fem_solver_2d as _fs
    real = _fs._SLIP_PER_PERIOD_OVERRIDE
    out = {}
    try:
        for spp in (0, 216, 288, 432):
            # The override is a module global read inside the solve — set the
            # attribute rather than reloading the module (a reload would leave
            # every other module holding stale references to the old one).
            _fs._SLIP_PER_PERIOD_OVERRIDE = int(spp)
            d = run(**COMMON)
            tag = "default (1008 calibration)" if spp == 0 else f"forced {spp}/period"
            out[tag] = _m(d)
            _line(tag, out[tag], out.get("default (1008 calibration)"),
                  ("T_avg", "T_ripple", "P_fe", "n_slip"))
    finally:
        _fs._SLIP_PER_PERIOD_OVERRIDE = real
    RESULTS["B"] = out


# ── C: Aitken flux-anchor guards ─────────────────────────────────────────────
def case_C() -> None:
    print("\nC  drive.py aitken_flux_anchor guards (skip when drift < 5e-4*flux "
          "or |corr| > 5*drift) — voltage drive only")
    import motor_ai_sim.simulation.fem_solver_2d as _fs
    from motor_ai_sim.simulation import drive as _dr
    real = _fs._aitken_flux_anchor
    vkw = {**COMMON, "drive": "voltage", "v_phase_peak": 7.0, "v_delta_deg": 10.0}

    def unguarded(samples):
        """The same Delta^2 extrapolation with the two guards removed."""
        new, corr, drift = _dr.aitken_flux_anchor(samples)
        if new is not None:
            return new, corr, drift
        q0, q1, q2 = samples[-3:]
        out = {}
        for ci, ky in enumerate(("A", "B")):
            x0, x1, x2 = q0[ci], q1[ci], q2[ci]
            d1, d2 = x1 - x0, x2 - x1
            dd = d2 - d1
            out[ky] = (x2 - d2 * d2 / dd) if abs(dd) > 1e-15 else x2
        out["C"] = -(out["A"] + out["B"])
        return out, corr, drift

    out = {}
    for tag, fn in (("baseline (guards on)", real),
                    ("guards REMOVED (always anchor)", unguarded),
                    ("anchor DISABLED (never)", lambda s: (None, 0.0, 0.0))):
        _fs._aitken_flux_anchor = fn
        try:
            d = run(**vkw)
            out[tag] = _m(d)
        finally:
            _fs._aitken_flux_anchor = real
        _line(tag, out[tag], out.get("baseline (guards on)"),
              ("I_pk", "T_avg", "T_ripple", "v_dc_res"))
    RESULTS["C"] = out


# ── D: silent step snapping ──────────────────────────────────────────────────
def case_D() -> None:
    print("\nD  _snap_steps_to_nodes — does the response say what it actually ran?")
    for req in (12, 40, 48, 50):
        d = run(**{**COMMON, "n_steps_per_period": req})
        print(f"    requested {req:3d} -> n_steps_per_period={d.get('n_steps_per_period')} "
              f"n_steps={d.get('n_steps')} "
              f"requested_key={d.get('n_steps_per_period_requested')} "
              f"snapped_key={d.get('steps_snapped')}", flush=True)
        RESULTS.setdefault("D", {})[str(req)] = {
            "ran": d.get("n_steps_per_period"),
            "requested": d.get("n_steps_per_period_requested"),
            "snapped": d.get("steps_snapped")}


if __name__ == "__main__":
    which = [a.upper() for a in sys.argv[1:]] or ["A", "B", "C", "D"]
    for c in which:
        {"A": case_A, "B": case_B, "C": case_C, "D": case_D}[c]()
    Path(__file__).with_name("_filter_ablation_result.json").write_text(
        json.dumps(RESULTS, indent=1, default=float), encoding="utf-8")
    print("\nwrote scripts/_filter_ablation_result.json")
