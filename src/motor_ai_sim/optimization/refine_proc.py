"""Isolated FEM-transient evaluation of ONE candidate design.

Run as a subprocess (``python -m motor_ai_sim.optimization.refine_proc``) reading
a JSON spec on stdin and writing a JSON result on stdout.  Running each FEM
transient in its own process isolates the occasional LLVM/JIT crash in the FEM
stack from the API server — a crashed eval just yields a failed design, not a
dead backend.  It also evaluates the candidate via geo_override, so the global
config / Simulation state is never touched.
"""
from __future__ import annotations

import math
from typing import Dict, Any

_KERNEL = None


def _kernel():
    """Lazy singleton Kernel for this subprocess — build the module registry once."""
    global _KERNEL
    if _KERNEL is None:
        from motor_ai_sim.modules.kernel import Kernel
        _KERNEL = Kernel()
    return _KERNEL


def _coil_fit(geo: Dict[str, float]) -> tuple:
    """How many of the requested wires actually fit in the slot before the stack
    would cross the stator inner radius into the air gap.  Returns (n_fit,
    n_requested).  Mirrors the placement in cadquery_geometry.get_2d_polygons."""
    outer_r = float(geo.get("stator_diameter", 200.0)) / 2.0
    core_h  = float(geo.get("core_thickness", 5.6))
    slot_h  = float(geo.get("slot_height", 18.0))
    ins_w   = float(geo.get("insulation_thickness", 0.2))
    wire_dy = float(geo.get("wire_spacing_y", 0.13))
    wire_h  = float(geo.get("wire_height", 0.8))
    nw      = int(geo.get("num_wires_per_slot", 18))
    inner_r = outer_r - core_h - slot_h
    top_y_c = (outer_r - core_h) - ins_w - wire_dy / 2.0
    min_wire_r = inner_r + ins_w
    n_fit = 0
    for step in range(max(0, nw)):
        if top_y_c - step * (wire_h + wire_dy) - wire_h < min_wire_r:
            break
        n_fit += 1
    return n_fit, nw


def run_one(overrides: Dict[str, float], current_a: float, steps: int,
            coil_temp_c: float, n_periods: float = 1.0,
            gamma_deg: float = 0.0, mesh_size_mm: float = 4.0,
            min_size_mm: float = 0.3, n_sectors: int = -1,
            pole_copy=None, torque_filter: bool = True,
            gap_layers: float = 3.0, end_winding_factor: float = 0.0,
            rotor_eddy: bool = False, hi_fidelity: bool = False,
            structured_gap: bool = False, airgap_macro: bool = False,
            iron_template: bool = True) -> Dict[str, Any]:
    """Run the sliding-band transient for one candidate and return mean
    performance metrics (torque, efficiency, ripple, losses, mass).

    ``steps`` is the number of FEM frames actually computed; ``n_periods`` is the
    fraction of the electrical period swept.  For a cheap ripple-amplitude probe
    the scan uses n_periods=1/6 (one 6·k ripple cycle) with ~6 frames; a full
    refine uses n_periods=1 with more frames."""
    import numpy as np, json
    from motor_ai_sim.optimization.design_eval import build_params, _masses
    from motor_ai_sim.config import get_config

    cfg = get_config()
    geo = {**dict(cfg.get("geometry", {})), **overrides}

    # Enforce geometry feasibility (winding fits the slot, …) by CLAMPING the
    # violating knobs — so the optimizer always evaluates a VALID cross-section
    # (no coils overflowing the air gap).  Both the mass calc (build_params(geo))
    # and the FEM build (geo_override) use the same clamped values.
    from motor_ai_sim.geometry_constraints import clamp as _clamp_geo
    geo, _applied = _clamp_geo(geo)
    overrides = dict(overrides)
    for _a in _applied:
        overrides[_a["target"]] = geo[_a["target"]]

    # Final safety: if the winding STILL can't fit even after clamping (e.g. an
    # absurd turn count at minimum wire height), the design is truly infeasible.
    n_fit, n_req = _coil_fit(geo)
    if n_fit < n_req:
        raise ValueError(
            f"infeasible winding: {n_req} turns cannot fit the slot even clamped")

    # A parameter SWEEP / optimization explores GEOMETRY, and k_end is a FUNCTION of the
    # geometry (tooth_width, slot_width, …).  Pinning ONE k_end across changing geometry
    # would freeze the end-winding copper loss — yet a wider tooth has a longer end-turn
    # and MUST cost more DC I²R copper.  So always recompute k_end PER-POINT from each
    # candidate's geometry (auto), ignoring any single pinned value the Simulation tab
    # sent.  This keeps the copper LOSS and the copper MASS (mass uses auto k_end via
    # _masses below) on the SAME per-point k_end — consistent and tooth-dependent.
    end_winding_factor = 0.0
    rpm = float(cfg.get("simulation", {}).get("rpm", 3950.0))
    omega = 2 * math.pi * rpm / 60.0

    # n_total ≈ n_steps_per_period · n_periods → pick n_steps_per_period so the
    # window holds exactly ``steps`` frames.
    nper = max(1e-3, float(n_periods))
    nspp = max(4, int(round(int(steps) / nper)))
    # Run the candidate THROUGH the kernel module solver.em_transient (the SAME
    # module the Simulation tab uses), inside this isolated subprocess. The module
    # -> get_fem_transient -> em_transient_eval; the geo override means it won't
    # touch the user's saved simulation. result.raw is the sliding-band dict.
    _out = _kernel().run("solver.em_transient", {
        "n_steps_per_period": nspp, "n_periods": nper, "gamma_deg": float(gamma_deg),
        "I_phase_rms": float(current_a), "mesh_size_mm": float(mesh_size_mm),
        "min_size_mm": float(min_size_mm), "n_sectors": int(n_sectors),
        "coil_temp_c": float(coil_temp_c), "gap_layers": float(gap_layers),
        "end_winding_factor": float(end_winding_factor), "rotor_eddy": bool(rotor_eddy),
        "pole_copy": pole_copy, "torque_filter": bool(torque_filter),
        "hi_fidelity": bool(hi_fidelity),   # 2× slip nodes + finer mesh + gap layers → smoother raw torque
        "structured_gap": bool(structured_gap),   # belt gap mesh (Mesh tab "Structured") → honest ripple, ¼==full
        "airgap_macro": bool(airgap_macro),
        "iron_template": bool(iron_template),   # harmonic gap coupling (full ring): step-independent RAW ripple

        # Ambient mesh/sim params the Simulation tab passes but the optimizer used to
        # omit (so get_fem_transient fell back to ITS defaults — e.g. outer_air 1.3 vs
        # Sim's 1.2). Pull them from the active design's config so the optimizer's
        # em_transient_eval call is IDENTICAL to Simulation's → a picked design
        # reproduces exactly when re-run in the Simulation tab.
        "outer_air_factor": float(cfg.get("mesh", {}).get("outer_air_factor", 1.2)),
        "stator_fillet_mm": 0.0,   # Simulation hardcodes 0 (native geometry, no tooth-tip smoothing)
        "demag": bool(cfg.get("simulation", {}).get("demag", False)),
        "component_mesh": json.dumps(cfg.get("mesh", {}).get("component_mesh") or {}),
        "sliding_band": True, "fresh": True, "geo": json.dumps(overrides),
    })
    if not _out.get("ok"):
        raise RuntimeError(_out.get("error") or "solver.em_transient failed")
    d = getattr(_out.get("result"), "raw", None) or {}

    Tavg = float(d["T_avg_Nm"])
    cu = float(np.mean(d["P_cu_W"])); fe = float(np.mean(d["P_fe_W"]))
    mg = float(np.mean(d["P_mag_eddy_W"]))
    sh = float(np.mean(d.get("P_shaft_eddy_W", [0.0]) or [0.0]))   # solid-steel shaft eddy
    cu_ac = float(np.mean(d.get("P_cu_ac_W", [0.0]) or [0.0]))     # winding AC eddy/proximity
    cu_dc = float(d.get("P_cu_dc_W", cu - cu_ac))
    ploss = cu + fe + mg + sh
    pmech = Tavg * omega
    # Efficiency EXACTLY as the Simulation reports it (P_mech_avg / P_elec_in) when the
    # solver gives the power split, so the optimizer's η matches the Simulation tab to
    # the digit; fall back to P_mech / (P_mech + P_loss) only if those aren't present.
    _pe = float(d.get("P_elec_in_W", 0.0) or 0.0)
    _pm = float(d.get("P_mech_avg_W", 0.0) or 0.0)
    eff = (_pm / _pe) if (_pe > 0.0 and _pm > 0.0) else (pmech / (pmech + ploss) if pmech > 0 else 0.0)
    mass = float(_masses(build_params(geo), geo)["total"])
    # Voltage waveform quality — SAME helper as the Simulation summary, so the
    # optimizer's THD is byte-identical to the Simulation tab's.
    from motor_ai_sim.simulation.postproc import voltage_harmonics
    vh = voltage_harmonics(d)
    return {
        "T_em_Nm": round(Tavg, 3), "efficiency": round(eff, 5),
        "torque_per_mass_Nm_kg": round(Tavg / mass, 4) if mass > 0 else 0.0,
        "T_ripple_pct": round(float(d["T_ripple_pct"]), 2),
        "P_loss_total_W": round(ploss, 1), "P_cu_W": round(cu, 1),
        "P_cu_dc_W": round(cu_dc, 1), "P_cu_ac_W": round(cu_ac, 1),
        "P_fe_W": round(fe, 1), "P_mag_W": round(mg, 1), "P_shaft_W": round(sh, 1),
        "mass_total_kg": round(mass, 3), "V_peak": round(float(d["V_peak"]), 1),
        "V1_phase_V": vh["V1_phase_V"], "THD_pct": vh["THD_pct"],
        "THD_LL_pct": vh["THD_LL_pct"],
        "Kt_Nm_per_Arms": (round(Tavg / float(current_a), 4)
                           if float(current_a or 0.0) > 1e-9 else 0.0),
    }


if __name__ == "__main__":
    import sys, json
    spec = json.loads(sys.stdin.read())
    try:
        res = run_one(spec["overrides"], spec["current_a"],
                      spec.get("steps", 40), spec.get("coil_temp_c", 120.0),
                      n_periods=spec.get("n_periods", 1.0),
                      gamma_deg=spec.get("gamma_deg", 0.0),
                      mesh_size_mm=spec.get("mesh_size_mm", 4.0),
                      min_size_mm=spec.get("min_size_mm", 0.3),
                      n_sectors=spec.get("n_sectors", -1),
                      pole_copy=spec.get("pole_copy"),
                      torque_filter=spec.get("torque_filter", True),
                      gap_layers=spec.get("gap_layers", 3.0),
                      end_winding_factor=spec.get("end_winding_factor", 0.0),
                      rotor_eddy=spec.get("rotor_eddy", False),
                      hi_fidelity=spec.get("hi_fidelity", False),
                      structured_gap=spec.get("structured_gap", False),
                      airgap_macro=spec.get("airgap_macro", False),
                      iron_template=spec.get("iron_template", True))
        sys.stdout.write("@@RESULT@@" + json.dumps({"ok": True, "res": res}))
    except Exception as e:  # noqa: BLE001
        sys.stdout.write("@@RESULT@@" + json.dumps({"ok": False, "error": str(e)}))
