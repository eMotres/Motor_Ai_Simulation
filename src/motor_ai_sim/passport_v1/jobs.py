"""Solver-direct jobs of the passport pilot and the worker that runs them.

Every job is ONE call of ``simulation.fem_solver_2d.em_transient_eval`` — the
canonical sliding-band invocation the Simulation route, the optimizer and the
kernel module all funnel through (its docstring: "the optimizer's physics can
NEVER drift from what the Simulation tab shows").  The arguments mirror what
``routes.simulation.get_fem_transient`` hands it for a web Run of the same
duty (P2 + structured belt, Coulomb torque, ``inc_ldq=True``, the duty's Mesh
block, the per-request geometry), except that every value is explicit and
comes from the frozen snapshot (M0) — no panel context, no route caches, no
ledger, no warm seed (``SB_NO_WARM_CACHE=1``), the d-axis pinned to the value
measured once per machine.

The solver is NOT forked: this module only builds keyword dicts and prunes
the result for storage (full precision kept; only per-element field arrays
are dropped).
"""
from __future__ import annotations

import gzip
import json
import math
import os
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

#: Static-point window (spec 3.2): 60° electrical, re-based on the shipped
#: >= 9 samples per cogging cycle rule — 144 steps/period = 12 samples per
#: 30° cogging cycle on 12s/14p, 24 rotor positions in the 60° window.
STATIC_STEPS_PER_PERIOD = 144
STATIC_WINDOW_PERIODS = 1.0 / 6.0

#: Keys whose payload is per-element / per-node field data (megabytes) —
#: never needed by the passport and never stored.
_HEAVY_KEYS = {"field", "frames", "frames_mesh", "demag_coef_per_tri",
               "demag_field", "P2_transient_sample_history"}
_MAX_LIST = 1200


def prune(v: Any, depth: int = 0) -> Any:
    """JSON-safe copy of a solver result: full-precision scalars and series,
    per-element field arrays dropped (their length recorded)."""
    try:
        import numpy as _np
        if isinstance(v, _np.ndarray):
            v = v.tolist()
        elif isinstance(v, _np.generic):
            v = v.item()
    except Exception:                        # noqa: BLE001
        pass
    if isinstance(v, float):
        return v if math.isfinite(v) else repr(v)
    if isinstance(v, (int, str, bool)) or v is None:
        return v
    if isinstance(v, Mapping):
        out = {}
        for k, x in v.items():
            if str(k) in _HEAVY_KEYS:
                out[str(k)] = {"_dropped": True}
                continue
            out[str(k)] = prune(x, depth + 1)
        return out
    if isinstance(v, (list, tuple)):
        if len(v) > _MAX_LIST:
            return {"_dropped_len": len(v)}
        return [prune(x, depth + 1) for x in v]
    return str(v)


def _mean(series: Any) -> Optional[float]:
    if series is None:
        return None
    if isinstance(series, (int, float)):
        return float(series)
    try:
        vals = [float(x) for x in series]
    except (TypeError, ValueError):
        return None
    return (sum(vals) / len(vals)) if vals else None


def scalars(res: Mapping[str, Any]) -> Dict[str, Any]:
    """The passport's per-point quantities, full precision (spec 3.3 / §4)."""
    g = res.get
    terms = g("P_fe_terms") or {}

    def _half(h):
        t = terms.get(h) or {}
        return sum(float(t.get(k) or 0.0) for k in ("hysteresis_W", "eddy_W", "excess_W"))
    fe = _mean(g("P_fe_W")) or 0.0
    hs, hr = _half("stator"), _half("rotor")
    fe_s = fe * hs / (hs + hr) if (hs + hr) > 0 else None
    pcu = _mean(g("P_cu_W")) or 0.0
    pcu_dc = _mean(g("P_cu_dc_W"))
    pmag = _mean(g("P_mag_eddy_W")) or 0.0
    pshaft = _mean(g("P_shaft_eddy_W")) or 0.0
    psleeve = _mean(g("P_sleeve_eddy_W")) or 0.0
    coul = g("coulomb_torque") or {}
    diag = g("torque_method_diagnostics") or {}
    tdm = g("tdm") or {}
    out = {
        "T_avg_Nm": g("T_avg_Nm"),
        "T_avg_coulomb_Nm": g("T_avg_coulomb_Nm"),
        "T_avg_maxwell_Nm": g("T_avg_maxwell_Nm"),
        "T_ripple_pp_Nm": g("T_ripple_pp_Nm"),
        "T_ripple_pct": g("T_ripple_pct"),
        "torque_method": g("torque_method"),
        "T_mean_method": g("T_mean_method"),
        "terminal_work_mean_candidate_Nm": diag.get("terminal_work_mean_candidate_Nm"),
        "terminal_work_eligible": diag.get("terminal_work_method_eligible"),
        "terminal_work_eligibility_reason": diag.get("terminal_work_eligibility_reason"),
        "space_vector_mean_candidate_Nm": diag.get("space_vector_mean_candidate_Nm"),
        "coulomb_self_check": coul.get("layer_self_check"),
        "psi_d_Wb": g("psi_d_Wb"), "psi_q_Wb": g("psi_q_Wb"),
        "i_d_A": g("i_d_A"), "i_q_A": g("i_q_A"),
        "T_dq_Nm": g("T_dq_Nm"), "dq_torque_check_pct": g("dq_torque_check_pct"),
        "inc_ldq": g("inc_ldq"),
        "daxis_deg": g("daxis_deg"), "daxis_source": g("daxis_source"),
        "gamma_effective_deg": g("gamma_effective_deg"),
        "R_phase_ohm": g("R_phase_ohm"),
        "coil_temp_C": g("coil_temp_C"),
        "end_winding_factor": g("end_winding_factor"),
        "V_peak": g("V_peak"),
        "V_line_peak_solved_V": g("V_line_peak_solved_V"),
        "f_elec_Hz": g("f_elec_Hz"), "rpm": g("rpm"),
        "P_cu_W": pcu, "P_cu_dc_W": pcu_dc,
        "P_cu_ac_W": (pcu - pcu_dc) if pcu_dc is not None else None,
        "P_cu_circulating_W": g("P_cu_circulating_W"),
        "P_fe_W": fe, "P_fe_stator_W": fe_s,
        "P_fe_rotor_W": (fe - fe_s) if fe_s is not None else None,
        "P_fe_terms": terms,
        "P_mag_W": pmag, "P_shaft_W": pshaft, "P_sleeve_W": psleeve,
        "P_loss_total_W": pcu + fe + pmag + pshaft + psleeve,
        "P_elec_in_W": g("P_elec_in_W"), "P_airgap_W": g("P_airgap_W"),
        "demag_summary": g("demag_summary"),
        "demag_settled": g("demag_settled"), "steady_state": g("steady_state"),
        "steady_state_note": g("steady_state_note"),
        "demag_warning": g("demag_warning"),
        "eddy_coupled": g("eddy_coupled"), "eddy_settled": g("eddy_settled"),
        "eddy_capped": g("eddy_capped"), "eddy_method": g("eddy_method"),
        "eddy_method_note": g("eddy_method_note"),
        "eddy_settle_residual": g("eddy_settle_residual"),
        "qualified": g("qualified"), "tdm_experimental": g("tdm_experimental"),
        "tdm_stop": tdm.get("stop") if isinstance(tdm, Mapping) else None,
        "tdm_orbit_error_estimate": (tdm.get("orbit_error_estimate")
                                     if isinstance(tdm, Mapping) else None),
        "gap_layers_effective": g("gap_layers_effective"),
        "gap_refinement": g("gap_refinement"),
        "n_steps_per_period": g("n_steps_per_period"),
        "steps_snapped": g("steps_snapped"),
        "slip_nodes_per_period": g("slip_nodes_per_period"),
        "n_frames_solved": g("n_frames_solved"),
        "solve_wall_s": g("solve_wall_s"),
        "picard_converged": g("picard_converged"),
        "picard_resid_max": g("picard_resid_max"),
        "cogging_raw_samples_per_cycle": g("cogging_raw_samples_per_cycle"),
        "cogging_sampling_sufficient": g("cogging_sampling_sufficient"),
        "mesher": g("mesher"), "mesh_build_events": g("mesh_build_events"),
        "structured_gap_effective": g("structured_gap_effective"),
        "magnet_segmentation": g("magnet_segmentation"),
    }
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  Job builders
# ─────────────────────────────────────────────────────────────────────────────

def base_kwargs(snap: Mapping[str, Any], *, daxis_deg: Optional[float]) -> Dict[str, Any]:
    """The machine-fixed half of every job's arguments, from the snapshot."""
    m = snap["mesh"]
    w = snap["winding"]
    rd = snap["rated_duty"]
    return dict(
        connection=str(w["connection"]),
        star_delta=str(w.get("star_delta") or "star"),
        strand_bonding=None,                  # the geometry decides (route rule)
        daxis_deg=(None if daxis_deg is None else float(daxis_deg)),
        mesh_size_mm=float(m["mesh_size_mm"]), min_size_mm=float(m["min_size_mm"]),
        outer_air_factor=float(m["outer_air_factor"]),
        gap_layers=float(m["gap_layers"]), n_sectors=int(m["n_sectors"]),
        stator_fillet_mm=0.0,
        component_mesh_mm=dict(m.get("component_mesh_mm") or {}),
        iron_template=bool(m["iron_template"]), geo_mesh=bool(m["geo_mesh"]),
        pole_copy=bool(m["pole_copy"]), structured_gap=True, airgap_macro=False,
        hi_fidelity=False, element_order=2, torque_method="coulomb",
        end_winding_factor=float(rd["end_winding_factor"]),
        geo_override=dict(snap["geometry"]),
        inc_ldq=True, drive="current",
    )


def static_job(jid: str, base: Mapping[str, Any], *, I_rms: float, gamma_deg: float,
               magnet_temp_c: Optional[float], coil_temp_c: float, rpm: float,
               n_periods: float = STATIC_WINDOW_PERIODS,
               steps: int = STATIC_STEPS_PER_PERIOD,
               purpose: str = "standard", meta: Optional[dict] = None) -> Dict[str, Any]:
    """Magnetostatic (id, iq) point: imposed sine current, no eddy, no rotor
    eddy, no demag — the VIRGIN-magnet fixed retention state of the map (B7:
    damage is a boundary, never interpolated across)."""
    kw = dict(base)
    kw.update(n_steps_per_period=int(steps), n_periods=float(n_periods),
              sampling_purpose=purpose, gamma_deg=float(gamma_deg),
              I_phase_rms=float(I_rms), rpm=float(rpm),
              coil_temp_c=float(coil_temp_c),
              magnet_temp_c=(None if magnet_temp_c is None else float(magnet_temp_c)),
              rotor_eddy=False, demag=False, eddy=False)
    return {"id": jid, "kind": "fem", "kw": kw, "meta": dict(meta or {})}


def demag_probe_job(jid: str, base: Mapping[str, Any], *, I_rms: float, gamma_deg: float,
                    magnet_temp_c: Optional[float], coil_temp_c: float, rpm: float,
                    steps: int, meta: Optional[dict] = None) -> Dict[str, Any]:
    """Retention probe of one grid point: one full electrical period with the
    irreversible demag model on (the solver prepends its one-period ratchet
    pre-pass), eddy off — the steady damaged state reached from virgin
    magnets at this point.  Defines the (id, iq, T) safe surface (B7)."""
    kw = dict(base)
    kw.update(n_steps_per_period=int(steps), n_periods=1.0,
              sampling_purpose="standard", gamma_deg=float(gamma_deg),
              I_phase_rms=float(I_rms), rpm=float(rpm),
              coil_temp_c=float(coil_temp_c),
              magnet_temp_c=(None if magnet_temp_c is None else float(magnet_temp_c)),
              rotor_eddy=False, demag=True, eddy=False)
    return {"id": jid, "kind": "fem", "kw": kw, "meta": dict(meta or {})}


def loss_job(jid: str, base: Mapping[str, Any], *, I_rms: float, gamma_deg: float,
             rpm: float, magnet_temp_c: Optional[float], coil_temp_c: float,
             steps: int, demag: bool = True, meta: Optional[dict] = None,
             component_mesh_mm: Optional[dict] = None,
             end_winding_factor: Optional[float] = None) -> Dict[str, Any]:
    """Settled coupled-eddy transient (§4): strands resolved, magnets + shaft
    (+ sleeve) in the solve, TDM periodic orbit (solver default), full demag
    pre-pass (owner decision), one reported electrical period."""
    kw = dict(base)
    kw.update(n_steps_per_period=int(steps), n_periods=1.0,
              sampling_purpose="standard", gamma_deg=float(gamma_deg),
              I_phase_rms=float(I_rms), rpm=float(rpm),
              coil_temp_c=float(coil_temp_c),
              magnet_temp_c=(None if magnet_temp_c is None else float(magnet_temp_c)),
              rotor_eddy=True, demag=bool(demag), eddy=True)
    if component_mesh_mm is not None:
        kw["component_mesh_mm"] = dict(component_mesh_mm)
    if end_winding_factor is not None:
        kw["end_winding_factor"] = float(end_winding_factor)
    return {"id": jid, "kind": "fem", "kw": kw, "meta": dict(meta or {})}


# ─────────────────────────────────────────────────────────────────────────────
#  Worker
# ─────────────────────────────────────────────────────────────────────────────

_WORKER_STATE: Dict[str, Any] = {}


def worker_init(materials_assignment: Mapping[str, str]) -> None:
    """Per-process set-up (spawned workers): the request materials context.
    Threads / warm-cache / config env vars are inherited from the parent."""
    _WORKER_STATE["materials"] = dict(materials_assignment)


def wait_while_paused(poll_s: float = 20.0) -> None:
    """Hold before a FEM solve while the pause flag exists.  On the shared
    server a host-side watcher creates ``$PASSPORT_PAUSE_FLAG`` whenever a user
    job is running or queued on the live API (owner rule: sandbox runs yield
    to the user's own jobs); a solve already under way is not interrupted."""
    flag = os.environ.get("PASSPORT_PAUSE_FLAG")
    if not flag:
        return
    while os.path.exists(flag):
        time.sleep(poll_s)


def run_job(job: Mapping[str, Any], out_dir: str) -> Dict[str, Any]:
    """Solve one job; write the pruned raw result (gz JSON) and return the
    compact record.  Exceptions are captured — a failed run is a record too
    (B9: failed runs are kept)."""
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.simulation.fem_solver_2d import em_transient_eval
    set_request_materials({"assignment": dict(_WORKER_STATE.get("materials") or {}),
                           "materials": {}})
    wait_while_paused()
    t0 = time.time()
    rec: Dict[str, Any] = {"id": job["id"], "meta": dict(job.get("meta") or {}),
                           "pid": os.getpid(),
                           "kw": {k: v for k, v in job["kw"].items()
                                  if k != "geo_override"}}
    try:
        res = em_transient_eval(**job["kw"])
        rec["ok"] = True
        rec["r"] = prune(scalars(res))
        raw = prune(res)
        p = Path(out_dir) / "raw" / (job["id"] + ".json.gz")
        p.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(p, "wt", encoding="utf-8") as fh:
            json.dump(raw, fh)
    except Exception as e:                   # noqa: BLE001
        rec["ok"] = False
        rec["error"] = f"{type(e).__name__}: {e}"
        rec["tb"] = traceback.format_exc()[-4000:]
    rec["wall_s"] = time.time() - t0
    return rec
