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

# ── mesh budget for ONE candidate eval (triangles) ───────────────────────────
# The optimizer samples the whitelist without a range box, so a candidate can be
# buildable (passes validate_geometry) yet carry a DERIVED thin feature — a slot
# opening squeezed to um, a tooth pared to a knife edge — that makes the stator
# cell's q20 refinement cascade to 0.5-1 M triangles.  Measured on a real 230-
# eval auto run: 38 candidates burned the full 300 s _subprocess_eval cap and
# returned nothing.  Arming geo_mesh's process-scoped budget makes Triangle
# terminate promptly at a Steiner cap instead, so the same candidate is rejected
# in seconds with an error the run log and the reject chips can name ("mesh
# budget: ...", classified as "mesh").
#
# The value is chosen so a rejected candidate PROVABLY could not have solved
# inside the 300 s cap — a false reject silently removes a design from the
# search, which is worse than a slow eval.  The measured healthy P2 eval on the
# active design is ~25-60 k triangles in ~40 s for a 48-frame window, i.e.
# ~0.8 s per frame at ~30 k tris; at 400 k tris the frames alone extrapolate
# (linearly — FEM assembly+solve is superlinear, so this UNDERestimates) to
# ~500 s before the mesh build is even paid.  Every refine_proc caller shares
# the 300 s subprocess timeout, so the budget applies to all of them.
_MESH_TRI_BUDGET = 400_000


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
            pole_copy=None, torque_filter: bool = False,
            gap_layers: float = 3.0, end_winding_factor: float = 0.0,
            rotor_eddy: bool = False, hi_fidelity: bool = False,
            structured_gap: bool = False, airgap_macro: bool = False,
            iron_template: bool = True, geo_mesh: bool = True,
            element_order: int = 2, rpm: float | None = None,
            n_parallel: int | None = None,
            connection: str | None = None,
            demag: bool | None = None) -> Dict[str, Any]:
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

    # REAL geometry check — the clamp above bounds one scalar at a time and
    # _coil_fit only counts wires; neither can see whether the finished regions
    # overlap.  A candidate whose magnets cut into the rotor iron, or whose two
    # coil sides share the same copper, still meshes and still returns a torque
    # — and that torque would be ranked against honest ones and could WIN the
    # descent.  Reject it here, before the subprocess pays for a mesh + N FEM
    # solves: the eval comes back {"ok": False, "error": ...} and the candidate
    # is dropped, exactly like an unconverged frame.  (The route-side gate in
    # get_fem_transient would also catch it; this is the cheap early exit and it
    # names the violations in the run log.)
    from motor_ai_sim.geometry_validation import validate_geometry as _vgeo
    _vres = _vgeo(geo)
    if not _vres.ok:
        raise ValueError(_vres.summary())

    # A parameter SWEEP / optimization explores GEOMETRY, and k_end is a FUNCTION of the
    # geometry (tooth_width, slot_width, …).  Pinning ONE k_end across changing geometry
    # would freeze the end-winding copper loss — yet a wider tooth has a longer end-turn
    # and MUST cost more DC I²R copper.  So always recompute k_end PER-POINT from each
    # candidate's geometry (auto), ignoring any single pinned value the Simulation tab
    # sent.  This keeps the copper LOSS and the copper MASS (mass uses auto k_end via
    # _masses below) on the SAME per-point k_end — consistent and tooth-dependent.
    end_winding_factor = 0.0
    # SPEED — one number for this eval, used BOTH for our own omega and by the
    # solver.  It used to be read here for omega while the solver read the same
    # global key for itself; correct only as long as nothing moved the config
    # between the two reads, and silently a different operating point when the
    # caller meant a candidate's own speed (docs/SOLVER_TRIALS_2026-07-30.md F2).
    # None = the active config's speed, so an omitted argument changes nothing.
    rpm = float(cfg.get("simulation", {}).get("rpm", 3950.0)) if rpm is None else float(rpm)
    omega = 2 * math.pi * rpm / 60.0

    # n_total ≈ n_steps_per_period · n_periods → pick n_steps_per_period so the
    # window holds exactly ``steps`` frames.
    nper = max(1e-3, float(n_periods))
    nspp = max(4, int(round(int(steps) / nper)))
    # P2 "high-fidelity ripple" self-consistency — MIRRORS the Simulation route
    # (routes/simulation.py get_fem_transient): element_order=2 needs the merged
    # structured belt and current-drive magnetostatics, so coerce the incompatible
    # options (force structured_gap; disable airgap_macro + demag; keep rotor_eddy
    # — P2 supports post-processed losses).  And since a P2 sector solve == the
    # full motor for ripple at ~S× lower cost, auto-use the natural symmetry
    # sector gcd(slots, poles) = num_seg when the caller left the full motor.
    _eo = int(element_order)
    _sg = bool(structured_gap); _am = bool(airgap_macro)
    # DEMAG — SINGLE SOURCE: the caller (the Simulation tab's toggle rides in
    # the sweep/optimize request, like the winding connection).  None = fall
    # back to the active config, which is what a bare in-process call means.
    # It used to read ONLY the config, and the checkbox never writes there: the
    # Simulation tab solved a de-rated machine while every sweep point solved a
    # full-strength one, and the two were compared as if they were the same.
    _sim = dict(cfg.get("simulation", {}) or {})
    _demag = bool(_sim.get("demag", False)) if demag is None else bool(demag)
    _ns = int(n_sectors)
    if _eo == 2:
        # NOTE: this used to also force _demag = False — stale since P2 demag
        # landed (a1aedad); it silently dropped demagnetisation from every P2
        # sweep, same bug class as routes/simulation e16c3ad. Only the two
        # genuinely-required coercions remain.
        _sg = True; _am = False
        if _ns <= 1:
            try:
                _sym = int(geo.get("num_seg")
                           or math.gcd(int(geo.get("num_slots", 1)),
                                       int(geo.get("num_poles", 1))) or 1)
                if _sym >= 2:
                    _ns = _sym
            except Exception:
                pass
    # Run the candidate THROUGH the kernel module solver.em_transient (the SAME
    # module the Simulation tab uses), inside this isolated subprocess. The module
    # -> get_fem_transient -> em_transient_eval; the geo override means it won't
    # touch the user's saved simulation. result.raw is the sliding-band dict.
    # Arm the mesh budget for THIS candidate (process == candidate here; the
    # __main__ path below runs one eval per subprocess).  The kernel seam
    # catches every exception and returns ok=False (ResultIR.failed), so the
    # call below cannot raise past the disarm — an in-process caller (tests)
    # gets the budget disarmed again either way.
    from motor_ai_sim.simulation import geo_mesh as _geo_mesh_mod
    _geo_mesh_mod.set_tri_budget(_MESH_TRI_BUDGET)
    _out = _kernel().run("solver.em_transient", {
        "n_steps_per_period": nspp, "n_periods": nper, "gamma_deg": float(gamma_deg),
        "I_phase_rms": float(current_a), "rpm": float(rpm),
        # WINDING: omitted (None) = the active config's connection, exactly as
        # before.  Passed, the candidate is driven at ITS parallel paths — the
        # FEM only ever sees I_coil = I_phase / n_parallel (F3).
        **({} if n_parallel is None else {"n_parallel": int(n_parallel)}),
        **({} if connection is None else {"connection": str(connection)}),
        "mesh_size_mm": float(mesh_size_mm),
        "min_size_mm": float(min_size_mm), "n_sectors": int(_ns),
        "coil_temp_c": float(coil_temp_c), "gap_layers": float(gap_layers),
        "end_winding_factor": float(end_winding_factor), "rotor_eddy": bool(rotor_eddy),
        "pole_copy": pole_copy, "torque_filter": bool(torque_filter),
        "hi_fidelity": bool(hi_fidelity),   # 2× slip nodes + finer mesh + gap layers → smoother raw torque
        "structured_gap": bool(_sg),   # belt gap mesh (Mesh tab "Structured") → honest ripple, ¼==full
        "airgap_macro": bool(_am),
        "iron_template": bool(iron_template),   # harmonic gap coupling (full ring): step-independent RAW ripple
        "geo_mesh": bool(geo_mesh),   # geometry-driven CDT mesh (Mesh tab) — same build as Simulation
        "element_order": int(_eo),   # 2 = P2 quadratic — the only basis

        # Ambient mesh/sim params the Simulation tab passes but the optimizer used to
        # omit (so get_fem_transient fell back to ITS defaults — e.g. outer_air 1.3 vs
        # Sim's 1.2). Pull them from the active design's config so the optimizer's
        # em_transient_eval call is IDENTICAL to Simulation's → a picked design
        # reproduces exactly when re-run in the Simulation tab.
        "outer_air_factor": float(cfg.get("mesh", {}).get("outer_air_factor", 1.2)),
        "stator_fillet_mm": 0.0,   # Simulation hardcodes 0 (native geometry, no tooth-tip smoothing)

        # ── the remaining PHYSICS switches, from the shared config ───────────
        # These have route defaults (eddy=False, drive="current"), and a
        # candidate that takes the default while the Simulation tab runs the
        # coupled eddy solve is a DIFFERENT machine: the coupled solve reports
        # the SOLVED copper loss (proximity crowding) instead of the analytic
        # one, which moves the efficiency the optimizer is ranking on.  Read
        # them from the config, which is where the Simulation tab now persists
        # every one of its switches.
        "eddy": bool(_sim.get("eddy", False)),
        "drive": str(_sim.get("drive", "current") or "current"),
        "v_phase_peak": float(_sim.get("v_phase_peak", 0.0) or 0.0),
        "v_delta_deg": float(_sim.get("v_delta_deg", 0.0) or 0.0),
        "demag": bool(_demag),   # per-element irreversible demag (doubles the frames)
        "component_mesh": json.dumps(cfg.get("mesh", {}).get("component_mesh") or {}),
        "sliding_band": True, "fresh": True, "geo": json.dumps(overrides),
    })
    _geo_mesh_mod.set_tri_budget(None)
    # TWO failure carriers, and both must be read.  The kernel ENVELOPE's ok is
    # False only when the module itself threw (Kernel.run's fault isolation);
    # when the module caught the solver's exception and degraded gracefully it
    # returns envelope ok=True carrying a ResultIR with ok=False + error — the
    # contract's documented "graceful failure so a crashed module degrades the
    # study instead of breaking it".  Reading only the envelope treated that as
    # a SUCCESS with an empty payload: `raw` is None, `d` fell back to {}, and
    # the first field access below died as a bare `KeyError: 'T_avg_Nm'`, which
    # is what the whole run then reported ("10 of 10 designs couldn't be built
    # ('T_avg_Nm')") — the real reason (a rejected mesh, an unbuildable
    # cross-section, a d-axis calibration failure) was thrown away at this line.
    if not _out.get("ok"):
        raise RuntimeError(_out.get("error") or "solver.em_transient failed")
    _res = _out.get("result")
    if _res is not None and not getattr(_res, "ok", True):
        raise RuntimeError(getattr(_res, "error", None)
                           or "solver.em_transient returned a failed result")
    d = getattr(_res, "raw", None) or {}
    # Nothing below can produce a meaningful number without the sliding-band
    # payload, and naming the missing key alone tells an engineer nothing about
    # WHY it is missing.  Say what happened.
    if not d:
        raise RuntimeError(
            "solver.em_transient returned no sliding-band payload (result.raw "
            "is empty) — the transient produced no frames for this candidate")

    # ── nonlinear-solve honesty ──────────────────────────────────────────────
    # The Simulation card already refuses to present a window containing a frame
    # whose field never met its solver's convergence test (routes/simulation.py
    # "nonlinear_converged").  The optimizer read the SAME dict and ignored the
    # flag, so a candidate scored on an unconverged field could win a sweep, a
    # Pareto front or a descent step — the numbers below are averages over that
    # frame.  Treat it exactly like an infeasible build: raise, so the eval comes
    # back {"ok": False, "error": ...} and the candidate is DROPPED, never ranked.
    # The offending frame indices travel in the message so the run log names them
    # instead of saying "eval failed".
    if not bool(d.get("picard_converged", True)):
        _bad = list(d.get("picard_unconverged_frames") or [])
        raise RuntimeError(
            "unconverged FEM frames {} of {} (max nonlinear resid {:.3g} vs tol "
            "{:.1e}) — eval rejected".format(
                _bad, int(d.get("n_steps", 0) or 0),
                float(d.get("picard_resid_max") or 0.0),
                float(d.get("picard_tol") or 0.0)))

    # The four fields every scored metric below is built from.  Checking them
    # together, by name, means a payload that is short of one says so in words
    # instead of surfacing as a bare `KeyError: 'T_avg_Nm'` in the run log — the
    # difference between "10 designs couldn't be built ('T_avg_Nm')" and a
    # sentence an engineer can act on.
    _need = ("T_avg_Nm", "P_cu_W", "P_fe_W", "P_mag_eddy_W")
    _miss = [k for k in _need if k not in d]
    if _miss:
        raise RuntimeError(
            "solver.em_transient returned a payload without {} — the transient "
            "produced {} frame(s) but no {} for them; the candidate cannot be "
            "scored.".format(", ".join(_miss), int(d.get("n_steps", 0) or 0),
                             "torque" if "T_avg_Nm" in _miss else "losses"))

    Tavg = float(d["T_avg_Nm"])
    cu = float(np.mean(d["P_cu_W"])); fe = float(np.mean(d["P_fe_W"]))
    mg = float(np.mean(d["P_mag_eddy_W"]))
    sh = float(np.mean(d.get("P_shaft_eddy_W", [0.0]) or [0.0]))   # solid-steel shaft eddy
    cu_ac = float(np.mean(d.get("P_cu_ac_W", [0.0]) or [0.0]))     # winding AC eddy/proximity
    cu_dc = float(d.get("P_cu_dc_W", cu - cu_ac))
    ploss = cu + fe + mg + sh
    pmech = Tavg * omega
    # Efficiency EXACTLY as the Simulation card reports it (e08f4af, 2026-08-04):
    # shaft out over shaft out + EVERY loss reported, with shaft out = T_avg·ω so
    # the row's own torque and power tiles reproduce its η by hand.  It used to
    # divide P_mech_avg_W by the solved terminal power P_elec_in_W, and that is
    # optimistic by construction: the 2-D circuit carries only the losses that
    # live INSIDE the field solve (active-length copper + solved eddy), while the
    # analytic iron loss and the end-winding copper are post-processed and never
    # cross its terminals — measured 295 W of the 150 mm card's 500 W, i.e. η
    # read 97.81 % where the same numbers say 96.34 %.  Compare rows and the
    # Simulation card are read against each other; two conventions is one too
    # many.  The solver's energy-balanced numbers stay below as diagnostics.
    _pe = float(d.get("P_elec_in_W", 0.0) or 0.0)
    _pm = float(d.get("P_mech_avg_W", 0.0) or 0.0)
    eff = (pmech / (pmech + ploss)) if (pmech > 0 and (pmech + ploss) > 0) else 0.0
    mass = float(_masses(build_params(geo), geo)["total"])
    # Voltage waveform quality — SAME helper as the Simulation summary, so the
    # optimizer's THD is byte-identical to the Simulation tab's.
    from motor_ai_sim.simulation.postproc import voltage_harmonics
    vh = voltage_harmonics(d)
    # Thermal loading and the speed constant, computed EXACTLY as the Simulation
    # summary does (routes/simulation.py): J = I_rms / n_parallel over ONE strand's
    # section, KV from the fundamental line voltage phasor.  Same formula → an
    # optimizer design and a Simulation run of it report the same number.
    # The SAME parallel-path count the solve used — argument first, then the
    # connection label, then the config.  Reading the config here while the
    # solver honoured an argument would report a J the run never had.
    if n_parallel is not None:
        _npar = max(1, int(n_parallel))
    elif connection is not None:
        from motor_ai_sim.winding import parse_connection as _pc
        _npar = max(1, _pc(connection)[0])
    else:
        _npar = max(1, int((cfg.get("winding", {}) or {}).get("n_parallel", 1) or 1))
    _a_cond_mm2 = float(geo.get("wire_width", 0.0)) * float(geo.get("wire_height", 0.0))
    j_coil = (float(current_a) / _npar / _a_cond_mm2) if _a_cond_mm2 > 1e-9 else 0.0
    # Peak LINE voltage — the DC-bus sizing number.  Taken from the actual
    # line-to-line waveforms like the Simulation summary does, falling back to the
    # sqrt(3)*V_phase_peak identity only when the phase waveforms aren't returned.
    _vs = [d.get(k) for k in ("V_A", "V_B", "V_C")]
    if all(isinstance(v, (list, tuple)) and len(v) >= 4 for v in _vs) \
            and len({len(v) for v in _vs}) == 1:
        _va, _vb, _vc = (np.nan_to_num(np.asarray(v, float), nan=0.0, posinf=0.0, neginf=0.0)
                         for v in _vs)
        v_line_peak = float(max(np.max(np.abs(p)) for p in (_va - _vb, _vb - _vc, _vc - _va)))
    else:
        v_line_peak = float(d["V_peak"]) * math.sqrt(3)
    # KV = rpm / V_line_peak — the max/max convention the Simulation tile and the
    # user's Ansys table use (bef2ed2, 2026-08-04).  It divided by the
    # FUNDAMENTAL line-to-line rms, which reads ~sqrt(2) higher and contradicted
    # a by-hand rpm / V_LINE PEAK check off the cell beside it (49.3 vs 34.7 on
    # the 150 mm at 4000 rpm).  Computed HERE, below v_line_peak, because that is
    # the number it now needs — it used to sit above the block that measures it.
    kv_line = (rpm / v_line_peak) if v_line_peak > 1.0 else 0.0
    return {
        "T_em_Nm": round(Tavg, 3), "efficiency": round(eff, 5),
        "torque_per_mass_Nm_kg": round(Tavg / mass, 4) if mass > 0 else 0.0,
        "T_ripple_pct": round(float(d["T_ripple_pct"]), 2),
        "P_loss_total_W": round(ploss, 1), "P_cu_W": round(cu, 1),
        "P_cu_dc_W": round(cu_dc, 1), "P_cu_ac_W": round(cu_ac, 1),
        "P_fe_W": round(fe, 1), "P_mag_W": round(mg, 1), "P_shaft_W": round(sh, 1),
        # Same loss split under the Simulation's OWN names, so a design carries one
        # vocabulary from the optimizer through the Compare table.
        "P_core_W": round(fe, 1),                 # laminated iron
        "P_stranded_W": round(cu, 1),             # copper
        "P_solid_W": round(mg + sh, 1),           # magnet + shaft eddy
        # MECHANICAL power = T_avg·ω, the same convention the Simulation card
        # adopted in e08f4af — the row's power tile IS its torque tile times
        # speed, so the two agree by inspection and η above is reproducible from
        # the row.  It used to report the solver's energy-balanced
        # P_mech_avg_W (P_elec_in − all losses); that number stays, below, as the
        # diagnostic it is.  The pair differ by the energy-balance residual
        # (~1 % on the 150 mm), which is exactly the gap that made a Compare row
        # contradict itself once η stopped dividing by the terminal power.
        "P_mech_W": round(pmech, 1),
        "P_mech_balance_W": round(_pm, 1),
        "P_elec_in_solved_W": round(_pe, 1),
        "J_coil_A_per_mm2": round(j_coil, 1),
        "KV_rpm_per_V_line": round(kv_line, 2),
        "power_per_mass_W_kg": round(pmech / mass, 1) if mass > 0 else 0.0,
        "loss_density_W_kg": round(ploss / mass, 1) if mass > 0 else 0.0,
        "mass_total_kg": round(mass, 3), "V_peak": round(float(d["V_peak"]), 1),
        "V_line_peak_V": round(v_line_peak, 1),
        "I_phase_rms_A": round(float(current_a), 2),
        "V1_phase_V": vh["V1_phase_V"], "THD_pct": vh["THD_pct"],
        "THD_LL_pct": vh["THD_LL_pct"], "V1_LL_V": vh.get("V1_LL_V", 0.0),
        "Kt_Nm_per_Arms": (round(Tavg / float(current_a), 4)
                           if float(current_a or 0.0) > 1e-9 else 0.0),
        # Convergence stamp, under the SAME names the Simulation summary uses.
        # Always True here (an unconverged window raised above) — it is carried
        # so a cached eval can be told apart from a pre-gate one, and so anything
        # downstream can assert on it rather than assume.
        "nonlinear_converged": True,
        "nonlinear_resid_max": float(d.get("picard_resid_max") or 0.0),
        "nonlinear_tol": float(d.get("picard_tol") or 0.0),
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
                      iron_template=spec.get("iron_template", True),
                      geo_mesh=spec.get("geo_mesh", True),
                      element_order=spec.get("element_order", 2),
                      rpm=spec.get("rpm"),
                      n_parallel=spec.get("n_parallel"),
                      connection=spec.get("connection"),
                      demag=spec.get("demag"))
        sys.stdout.write("@@RESULT@@" + json.dumps({"ok": True, "res": res}))
    except Exception as e:  # noqa: BLE001
        sys.stdout.write("@@RESULT@@" + json.dumps({"ok": False, "error": str(e)}))
