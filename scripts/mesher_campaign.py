# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Optimizer-style mini-campaign: netgen vs gmsh CDT (Triangle, the third
backend of the original campaign, was removed on 2026-10-03) on
cusp/fillet-heavy candidates (docs/MESHER_TRANSITION.md, "Required before S4"
item 4; netgen: docs/MESHER_NETGEN_2026-09-30.md).

Each candidate is a perturbation of the 40 mm 12s/14p preset biased towards
the geometry that broke meshers before: sharp or tiny rotor fillets
(rotor_fill_r -> 0), a magnet fillet tangent to the pocket wall
(rotor_hole = 1), sharp stator slot fillets, a thin slot opening, a thin
bridge.  Every candidate is meshed with the optimizer's budget armed and,
when it meshes, solved (short transient); each (candidate, backend) runs in
its own child process so its peak RSS and any crash are isolated.

    python scripts/mesher_campaign.py plan --workdir W [--n 24] [--budget 400000]
                                           [--solve] [--threads 6]
                                           [--backends netgen,gmsh]
                                           [--only c00,c04,...]
    python scripts/mesher_campaign.py report --workdir W

Recorded per run: mesh build time, triangle count, minimum angle, aspect
ratio p50/p99/p99.9/max, budget verdict, error class + message, solve wall
time, peak RSS, torque, ripple, losses, EMF-related voltage.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
import sys
import time

_BASE = "my_40mm_last"
# element size of the campaign meshes/solves (CAMPAIGN_MESH_MM for refinement checks)
_MESH_MM = float(os.environ.get("CAMPAIGN_MESH_MM", "1.0") or 1.0)
# perturbations: key -> candidate values (absolute), chosen on the cusp side
_KNOBS = {
    "rotor_fill_r": [0.0, 0.02, 0.05, 0.1, 0.2],
    "rotor_hole": [0.6, 1.0],
    "magnet_fill_radius": None,        # x {0.2, 0.5, 1.0, 1.5} of the base
    "stator_fillet_r1": [0.0, 0.1, 0.3],
    "slot_hs": None,                   # x {0.25, 0.5, 1.0}
    "magnet_up_gap": None,             # x {0.1, 0.5, 1.0}
}
_SCALE = {"magnet_fill_radius": [0.2, 0.5, 1.0, 1.5], "slot_hs": [0.25, 0.5, 1.0],
          "magnet_up_gap": [0.1, 0.5, 1.0]}


def _repo():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def candidates(n: int, seed: int = 20260929):
    base = json.load(open(os.path.join(_repo(), "config", "motor_presets.json"),
                          encoding="utf-8"))[_BASE]["geometry"]
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        g = dict(base)
        pert = {}
        for k, vals in _KNOBS.items():
            if k not in g:
                continue
            if vals is None:
                v = float(g[k]) * rnd.choice(_SCALE[k])
            else:
                v = rnd.choice(vals)
            g[k] = v
            pert[k] = v
        out.append({"id": f"c{i:02d}", "geometry": g, "perturbation": pert})
    return out


def _quality(V, T):
    import numpy as np
    p = V[T]
    e = np.stack([np.linalg.norm(p[:, 1] - p[:, 0], axis=1),
                  np.linalg.norm(p[:, 2] - p[:, 1], axis=1),
                  np.linalg.norm(p[:, 0] - p[:, 2], axis=1)], axis=1)
    a2 = np.abs((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
                - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1]))
    # aspect = longest edge / (2 sqrt(3) inradius) (1 = equilateral)
    s = e.sum(axis=1) / 2.0
    r_in = np.where(s > 0, 0.5 * a2 / np.maximum(s, 1e-300), 0.0)
    ar = e.max(axis=1) / np.maximum(2.0 * math.sqrt(3.0) * r_in, 1e-300)
    # minimum angle (law of cosines)
    ang = []
    for i, j, k in ((0, 1, 2), (1, 2, 0), (2, 0, 1)):
        c = (e[:, i] ** 2 + e[:, k] ** 2 - e[:, j] ** 2) / np.maximum(
            2 * e[:, i] * e[:, k], 1e-300)
        ang.append(np.degrees(np.arccos(np.clip(c, -1, 1))))
    amin = np.min(np.stack(ang, axis=1), axis=1)
    return {"n_tri": int(len(T)), "min_angle_deg": float(amin.min()),
            "min_angle_p0_1_deg": float(np.percentile(amin, 0.1)),
            "ar_p50": float(np.percentile(ar, 50)), "ar_p99": float(np.percentile(ar, 99)),
            "ar_p99_9": float(np.percentile(ar, 99.9)), "ar_max": float(ar.max()),
            "n_ar_over_20": int((ar > 20).sum())}


def one(cand_path, backend, out_path, budget, solve):
    try:
        os.nice(19)
    except (AttributeError, OSError):
        pass
    import logging
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    import numpy as np
    cand = json.load(open(cand_path, encoding="utf-8"))
    res = {"id": cand["id"], "backend": backend, "perturbation": cand["perturbation"]}
    from motor_ai_sim.simulation import geo_mesh as gm
    gm.set_cdt_backend(backend)
    try:  # metadata only: gmsh is never imported in a solve process
        from importlib.metadata import version as _pkg_version
        res["gmsh_version"] = _pkg_version("gmsh")
    except Exception:  # noqa: BLE001
        res["gmsh_version"] = None
    res["netgen_version"] = __import__(
        "motor_ai_sim.simulation.geo_mesh_netgen", fromlist=["x"]).netgen_version()
    t0 = time.time()
    try:
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        m = CadQueryMotor()
        m.set_parameters(dict(cand["geometry"]))
        p = m.parameters
        polys = m.get_2d_polygons(rotor_angle_deg=0.0)
    except Exception as e:  # noqa: BLE001 — a geometry verdict, not a mesher one
        res.update(stage="geometry", error=type(e).__name__, message=str(e)[:400])
        json.dump(res, open(out_path, "w"), indent=1)
        return
    gm.set_tri_budget(budget or None)
    t0 = time.time()
    try:
        ms, ts, _, mr, tr, _ = gm.geo_mesh_halves(
            p, polys, r_si=float(p["stator_inner_radius"]),
            r_ro=float(p["rotor_outer_radius"]), n_slip=1008, n_sectors=1,
            mesh_edge_mm=_MESH_MM)
        res["mesh_s"] = time.time() - t0
        q_s = _quality(ms.p.T * 1e3, ms.t.T)
        q_r = _quality(mr.p.T * 1e3, mr.t.T)
        res["quality"] = {"stator": q_s, "rotor": q_r}
        res["stage"] = "meshed"
    except Exception as e:  # noqa: BLE001
        res.update(stage="mesh", mesh_s=time.time() - t0, error=type(e).__name__,
                   message=str(e)[:400],
                   budget_reject=isinstance(e, gm.MeshBudgetExceeded))
    finally:
        gm.set_tri_budget(None)
    if solve and res.get("stage") == "meshed":
        from motor_ai_sim.simulation.fem_solver_2d import em_transient_eval
        t1 = time.time()
        try:
            r = em_transient_eval(
                n_steps_per_period=24, n_periods=1.0, gamma_deg=0.0,
                I_phase_rms=10.0, rpm=3000.0, mesh_size_mm=_MESH_MM, min_size_mm=0.3,
                outer_air_factor=1.2, gap_layers=1, n_sectors=1, rotor_eddy=True,
                iron_template=True, geo_mesh=True, structured_gap=True,
                geo_override=dict(cand["geometry"]), eddy=True)

            def mean(k):
                v = r.get(k)
                try:
                    return None if v is None else float(np.mean(v))
                except (TypeError, ValueError):
                    return None
            t_c, pp_c = mean("T_avg_coulomb_Nm"), mean("T_ripple_pp_coulomb")
            res["solve"] = {"wall_s": time.time() - t1,
                            "T_avg_Nm": mean("T_avg_Nm"),
                            "T_ripple_pct": mean("T_ripple_pct"),
                            # the owner's criteria (Coulomb torque, ripple, total loss)
                            "torque_method": r.get("torque_method"),
                            "T_avg_coulomb_Nm": t_c,
                            "T_ripple_pct_coulomb": (100.0 * pp_c / abs(t_c)
                                                     if pp_c is not None and t_c else None),
                            "P_loss_total_W": mean("P_loss_total_W"),
                            "P_cu_W": mean("P_cu_W"),
                            "P_fe_W": mean("P_fe_W"), "P_mag_W": mean("P_mag_eddy_W"),
                            "P_shaft_W": mean("P_shaft_eddy_W"),
                            "V_peak": mean("V_peak"),
                            "notes": r.get("mesh_build_notes"),
                            "events": r.get("mesh_build_events")}
            res["stage"] = "solved"
        except Exception as e:  # noqa: BLE001
            res.update(stage="solve", error=type(e).__name__, message=str(e)[:400])
    try:
        import resource
        res["maxrss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except ImportError:
        res["maxrss_mb"] = None
    json.dump(res, open(out_path, "w"), indent=1, default=str)


def plan(a):
    os.makedirs(a.workdir, exist_ok=True)
    thr = str(a.threads)
    env = dict(os.environ, OMP_NUM_THREADS=thr, MKL_NUM_THREADS=thr,
               OPENBLAS_NUM_THREADS=thr, SB_NO_WARM_CACHE="1")
    prog = os.path.join(a.workdir, "progress.txt")
    only = set(a.only.split(",")) if a.only else None
    for c in candidates(a.n):
        if only and c["id"] not in only:
            continue
        cp = os.path.join(a.workdir, c["id"] + ".cand.json")
        json.dump(c, open(cp, "w"), indent=1)
        for be in a.backends.split(","):
            out = os.path.join(a.workdir, f"{c['id']}_{be}.json")
            if os.path.exists(out):
                continue
            t0 = time.time()
            with open(os.path.join(a.workdir, f"{c['id']}_{be}.err"), "w") as fe:
                rc = subprocess.call([sys.executable, os.path.abspath(__file__), "one",
                                      cp, be, out, str(a.budget),
                                      "1" if a.solve else "0"],
                                     env=env, stdout=fe, stderr=fe, timeout=3600)
            if not os.path.exists(out):     # crashed hard (segfault / OOM)
                json.dump({"id": c["id"], "backend": be, "stage": "crash",
                           "returncode": rc}, open(out, "w"))
            with open(prog, "a") as pf:
                pf.write(f"{time.strftime('%H:%M:%S')} {c['id']} {be} rc={rc} "
                         f"{time.time() - t0:.0f}s\n")
    with open(prog, "a") as pf:
        pf.write("CAMPAIGN DONE\n")


def report(a):
    import numpy as np
    rows = {}
    for fn in sorted(os.listdir(a.workdir)):
        if fn.endswith(("_triangle.json", "_gmsh.json", "_netgen.json")):
            d = json.load(open(os.path.join(a.workdir, fn)))
            rows.setdefault(d["id"], {})[d["backend"]] = d
    summ = {}
    backends = a.backends.split(",")
    for be in backends:
        ds = [r[be] for r in rows.values() if be in r]
        st = {}
        for d in ds:
            st[d.get("stage")] = st.get(d.get("stage"), 0) + 1
        q = [d["quality"][h] for d in ds if d.get("quality") for h in ("stator", "rotor")]
        s = [d["solve"] for d in ds if d.get("solve")]

        def pct(vals, k):
            v = np.array([x[k] for x in vals], float)
            return None if not v.size else [float(v.min()), float(np.median(v)), float(v.max())]
        summ[be] = {"runs": len(ds), "stages": st,
                    "min_angle_deg[min,med,max]": pct(q, "min_angle_deg"),
                    "ar_p99[min,med,max]": pct(q, "ar_p99"),
                    "ar_p99_9[min,med,max]": pct(q, "ar_p99_9"),
                    "ar_max[min,med,max]": pct(q, "ar_max"),
                    "n_tri_half[min,med,max]": pct(q, "n_tri"),
                    "mesh_s[min,med,max]": pct([d for d in ds if "mesh_s" in d], "mesh_s"),
                    "solve_s[min,med,max]": pct(s, "wall_s"),
                    "maxrss_mb[min,med,max]": pct([d for d in ds if d.get("maxrss_mb")],
                                                  "maxrss_mb"),
                    "errors": sorted({f"{d.get('error')}: {str(d.get('message'))[:90]}"
                                      for d in ds if d.get("error")})}
    pair = []
    ref = backends[0]
    for cid, r in sorted(rows.items()):
        t = r.get(ref, {})
        ts = t.get("solve") or {}
        row = {"id": cid, "stage_" + ref: t.get("stage"),
               "ripple_" + ref: ts.get("T_ripple_pct")}
        for be in backends[1:]:
            g = r.get(be, {})
            gs = g.get("solve") or {}

            def dl(k):
                a_, b_ = ts.get(k), gs.get(k)
                return (None if a_ in (None, 0) or b_ is None
                        else 100.0 * (b_ - a_) / abs(a_))
            # owner's criteria vs the reference backend (2026-09-30)
            crit = {}
            if dl("T_avg_coulomb_Nm") is not None:
                crit["torque"] = abs(dl("T_avg_coulomb_Nm")) <= 1.0
            r0, r1 = ts.get("T_ripple_pct_coulomb"), gs.get("T_ripple_pct_coulomb")
            if r0 is not None and r1 is not None:
                crit["ripple"] = abs(r1 - r0) <= max(0.5, 0.10 * abs(r0))
            if dl("P_loss_total_W") is not None:
                crit["total_loss"] = abs(dl("P_loss_total_W")) <= 5.0
            qg = [g["quality"][h] for h in ("stator", "rotor")] if g.get("quality") else []
            row.update({"stage_" + be: g.get("stage"),
                        "dTc_pct_" + be: dl("T_avg_coulomb_Nm"),
                        "dRipc_pp_" + be: (r1 - r0 if r0 is not None and r1 is not None
                                           else None),
                        "dLoss_pct_" + be: dl("P_loss_total_W"),
                        "criteria_" + be: crit,
                        "quality_gate_" + be: (all(q["min_angle_deg"] >= 1.5
                                                   and q["ar_p99_9"] <= 30 for q in qg)
                                               if qg else None),
                        "dT_pct_" + be: dl("T_avg_Nm"), "dV_pct_" + be: dl("V_peak"),
                        "dFe_pct_" + be: dl("P_fe_W"), "dMag_pct_" + be: dl("P_mag_W"),
                        "ripple_" + be: gs.get("T_ripple_pct"),
                        "mesh_s_" + be: g.get("mesh_s"),
                        "solve_ratio_" + be: (gs.get("wall_s") / ts["wall_s"]
                                              if gs.get("wall_s") and ts.get("wall_s") else None),
                        "rss_ratio_" + be: (g.get("maxrss_mb") / t["maxrss_mb"]
                                            if g.get("maxrss_mb") and t.get("maxrss_mb") else None)})
        pair.append(row)
    out = {"summary": summ, "pairs": pair}
    json.dump(out, open(os.path.join(a.workdir, "campaign_report.json"), "w"),
              indent=1, default=str)
    print(json.dumps(out, indent=1, default=str))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("one")
    o.add_argument("cand"); o.add_argument("backend"); o.add_argument("out")
    o.add_argument("budget", type=int); o.add_argument("solve", type=int)
    p = sub.add_parser("plan")
    p.add_argument("--workdir", required=True)
    p.add_argument("--n", type=int, default=24)
    p.add_argument("--budget", type=int, default=400_000)
    p.add_argument("--solve", action="store_true")
    p.add_argument("--threads", type=int, default=6)
    p.add_argument("--backends", default="netgen,gmsh",
                   help="comma list of CDT backends (netgen,gmsh)")
    p.add_argument("--only", default="", help="comma list of candidate ids")
    r = sub.add_parser("report")
    r.add_argument("--workdir", required=True)
    r.add_argument("--backends", default="triangle,gmsh",
                   help="comma list; the first is the reference")
    a = ap.parse_args()
    if a.cmd == "one":
        one(a.cand, a.backend, a.out, a.budget, bool(a.solve))
    elif a.cmd == "plan":
        if a.threads > 6:
            raise SystemExit("threads <= 6 (the server is shared)")
        plan(a)
    else:
        report(a)


if __name__ == "__main__":
    main()
