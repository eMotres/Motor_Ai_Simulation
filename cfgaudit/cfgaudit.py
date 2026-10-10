"""Configure-audit sandbox driver (2026-09-30).  NOT committed.

Runs a list of solver-direct jobs sequentially in ONE process, appending one
JSON line per finished job to the results file.  Resumable: a job whose id is
already in the results file is skipped.  `touch <out>/stop` stops after the
current job.  Never touches a live API or live config: MOTOR_AI_SIM_CONFIG
points at a sandbox copy.

usage: python cfgaudit.py JOBS.json RESULTS.jsonl
"""
import json
import math
import os
import sys
import time
import traceback


def _mean(v):
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return float(sum(v) / len(v)) if v else None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pick(d, s):
    out = {}
    for k in ("T_avg_Nm", "V_peak", "R_phase_ohm", "rpm", "end_winding_factor",
              "daxis_deg", "psi_d_Wb", "psi_q_Wb", "i_d_A", "i_q_A"):
        out[k] = _mean(d.get(k))
    for k in ("P_cu_W", "P_fe_W", "P_mag_eddy_W", "P_shaft_eddy_W",
              "P_cu_total_solve_W", "P_cu_ac_solve_W"):
        out[k] = _mean(d.get(k))
    keep = ("T_em_avg_Nm", "T_ripple_pct", "V_phase_peak_V", "V_line_peak_V",
            "P_core_W", "P_core_stator_W", "P_core_rotor_W", "P_solid_W",
            "P_stranded_W", "P_mag_W", "P_cu_W", "P_loss_total_W",
            "efficiency", "R_phase_ohm", "R_phase_eq_star_ohm",
            "I_phase_rms_A", "I_line_rms_A", "I_winding_rms_A",
            "psi_pm_Wb", "Ld_mH", "Lq_mH", "end_winding_factor",
            "n_steps_per_period", "n_frames", "frames", "slot_fill_pct")
    for k in keep:
        if k in s:
            out["s_" + k] = s.get(k)
    dm = s.get("demag") or {}
    out["demag_loss_pct"] = dm.get("loss_pct") if isinstance(dm, dict) else None
    return out


def main():
    jobs = json.load(open(sys.argv[1], encoding="utf-8"))
    res_path = sys.argv[2]
    stop = os.path.join(os.path.dirname(res_path), "stop")
    done = set()
    if os.path.exists(res_path):
        for ln in open(res_path, encoding="utf-8"):
            try:
                done.add(json.loads(ln)["id"])
            except Exception:
                pass
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.routes.simulation import get_fem_transient, _BACKGROUND_RUN
    _BACKGROUND_RUN.set(True)
    for job in jobs:
        if os.path.exists(stop):
            print("stop file present — exiting", flush=True)
            return
        jid = job["id"]
        if jid in done:
            continue
        mats = job.get("materials") or {}
        set_request_materials({"assignment": mats, "materials": {}})
        t0 = time.time()
        rec = {"id": jid, "kind": job["kind"], "meta": job.get("meta", {})}
        try:
            if job["kind"] == "solve":
                kw = dict(job["kw"])
                if "geo" in kw and isinstance(kw["geo"], dict):
                    kw["geo"] = json.dumps(kw["geo"])
                d = get_fem_transient(**kw)
                s = d.get("summary", {}) or {}
                rec["r"] = _pick(d, s)
                rec["summary_keys"] = sorted(s.keys())[:400] if job.get("dump_keys") else None
            elif job["kind"] == "passport":
                from motor_ai_sim.passport import generate_passport
                kw = dict(job["kw"])
                r = generate_passport(**kw)
                rec["passport"] = r
            elif job["kind"] == "evaluate":
                # raw em_transient_eval (magnetostatic dq timing probe)
                from motor_ai_sim.simulation.fem_solver_2d import em_transient_eval
                kw = dict(job["kw"])
                d = em_transient_eval(**kw)
                rec["r"] = _pick(d, d.get("summary", {}) or {})
                rec["n_frames"] = len(d.get("T_Nm") or d.get("torque") or [])
            rec["ok"] = True
        except Exception as e:  # noqa: BLE001
            rec["ok"] = False
            rec["error"] = f"{type(e).__name__}: {e}"
            rec["tb"] = traceback.format_exc()[-3000:]
        rec["wall_s"] = round(time.time() - t0, 1)
        with open(res_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=float) + "\n")
        print(f"{jid}: ok={rec['ok']} {rec['wall_s']} s", flush=True)


if __name__ == "__main__":
    main()
