"""Solve pool bench: equality and throughput of independent sweep points.

Runs a saved duty (the same composition as ``profile_fem_run.py`` on the
profiling branch) through ``em_transient_eval`` either in-process or through
``motor_ai_sim.solve_pool``.  Sandbox only: point ``MOTOR_AI_SIM_CONFIG`` at a
throw-away copy (this script rewrites it with the duty's machine) and never run
it against the live API or a live config.

  # engineering equality, in-process vs pool, both at one thread
  python solve_pool_bench.py --dies DIES --case d40_rated --mode static \
      --run equality --out eq.json

  # 12 sweep points, today's layout: one in-process solve at a time, MKL default
  python solve_pool_bench.py --dies DIES --case d40_rated --mode static \
      --run inprocess --points 12 --out inproc.json

  # the same 12 points through the pool with N processes (+ one solo latency run)
  python solve_pool_bench.py --dies DIES --case d40_rated --mode static \
      --run pool --procs 6 --points 12 --out pool6.json

A thread-count variable must be set BEFORE numpy is imported, which is why the
in-process thread count comes from the environment of this process
(``MKL_NUM_THREADS`` etc.), and the pool's from ``QUEUE_PROCS`` /
``SOLVE_POOL_SOLO_THREADS``.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time

import yaml

CASES = {
    "l155_rated": dict(die="CIANO10 200 opt", cfg="L155 motor", duty="rated 1x9 mm"),
    "l13_rated": dict(die="CIANO28 85 20SW1200", cfg="L13", duty="rated"),
    "d40_rated": dict(die="CIANO14 40 new", cfg="L12", duty="rated"),
}


def compose(dies, case):
    from motor_ai_sim.routes.family import _ABSENT_MEANS, _ABSENT_MATERIALS
    c = CASES[case]
    d = yaml.safe_load(open(os.path.join(dies, c["die"], "die.yaml"), encoding="utf-8"))
    y = yaml.safe_load(open(os.path.join(dies, c["die"], c["cfg"] + ".yaml"), encoding="utf-8"))
    geo = dict(d.get("geometry") or {})
    geo.update({k: v for k, v in (y.get("geometry_overrides") or {}).items() if v is not None})
    for k, v in _ABSENT_MEANS.items():
        if geo.get(k) is None:
            geo[k] = v
    ns = float(geo["num_seg"])
    pps, sps = float(geo["num_poles_per_segment"]), float(geo["num_slots_per_segment"])
    geo["num_poles"] = int(round(ns * pps)); geo["angle_pole"] = 360.0 / (ns * pps)
    geo["num_slots"] = int(round(ns * sps)); geo["angle_slot"] = 360.0 / (ns * sps)
    duty = next(x for x in y["duties"] if x["name"] == c["duty"])
    mats = {k: v for k, v in (y.get("materials") or {}).items() if v}
    for k, v in (duty.get("materials") or {}).items():
        if v and not mats.get(k):
            mats[k] = v
    for k, v in _ABSENT_MATERIALS.items():
        if not mats.get(k):
            mats[k] = v
    runs = duty.get("runs") or {}
    st = {}
    st.update(((runs.get("current") or {}).get("settings")) or duty.get("mesh") or {})
    return c, d, y, geo, duty, mats, st


def build_kwargs(mode, d, y, geo, duty, st, sd):
    wnd = y.get("winding") or {}
    I_term = float(duty["current_arms"])
    I_wind = I_term / (math.sqrt(3.0) if sd == "delta" else 1.0)
    cm = {}
    for k, v in dict(st.get("mesh.componentMesh") or {}).items():
        try:
            cm[k] = float(v)
        except (TypeError, ValueError):
            cm[k] = v
    mt = st.get("sim.magnetTempC")
    eddy = mode != "static"
    return dict(
        n_steps_per_period=int(st.get("sim.stepsPP", 36)), n_periods=1.0,
        gamma_deg=float(duty["gamma_deg"]), I_phase_rms=I_wind, rpm=float(duty["rpm"]),
        n_parallel=int(wnd.get("n_parallel") or 1), connection=wnd.get("connection"),
        star_delta=sd,
        daxis_deg=(float(d["daxis_deg"]) if d.get("daxis_deg") is not None else None),
        mesh_size_mm=float(st.get("mesh.meshSize", 4)),
        min_size_mm=float(st.get("mesh.minSize", 0.3)),
        outer_air_factor=float(st.get("mesh.outerAir", 1.2)),
        gap_layers=float(st.get("mesh.gapLayers", 1)),
        n_sectors=int(st.get("mesh.nSectors", 2)),
        coil_temp_c=float(st.get("sim.coilTemp", 120)),
        magnet_temp_c=(float(mt) if mt not in (None, "") else None),
        end_winding_factor=float(st.get("sim.endWinding", 0) or 0),
        rotor_eddy=eddy, demag=bool(st.get("sim.demag", False)),
        pole_copy=bool(st.get("mesh.poleCopy", False)),
        iron_template=bool(st.get("mesh.ironTemplate", True)),
        geo_mesh=bool(st.get("mesh.geoMesh", True)),
        structured_gap=bool(st.get("mesh.structuredGap", True)),
        component_mesh_mm=cm, geo_override=dict(geo),
        drive="current", inc_ldq=True, eddy=eddy)


def prepare(args):
    c, d, y, geo, duty, mats, st = compose(args.dies, args.case)
    cfg_path = os.environ["MOTOR_AI_SIM_CONFIG"]
    base = yaml.safe_load(open(cfg_path, encoding="utf-8"))
    base["geometry"].update(geo)
    base["materials"].update(mats)
    base["winding"] = dict(y.get("winding") or {})
    base["parts"] = dict(y.get("parts") or base.get("parts") or {})
    wnd = y.get("winding") or {}
    sd = str(wnd.get("star_delta") or duty.get("star_delta") or st.get("sim.starDelta") or "star")
    base["simulation"].update({"rpm": float(duty["rpm"]), "gamma_deg": float(duty["gamma_deg"]),
                               "star_delta": sd})
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(base, f, allow_unicode=True, sort_keys=False)
    from motor_ai_sim.config import clear_config_cache
    clear_config_cache()
    return build_kwargs(args.mode, d, y, geo, duty, st, sd)


def points(kw, n):
    """``n`` independent sweep points: the duty's current from 50 % to 105 %."""
    out = []
    for i in range(n):
        f = 0.5 + 0.55 * i / max(1, n - 1)
        k = dict(kw)
        k["I_phase_rms"] = kw["I_phase_rms"] * f
        out.append(k)
    return out


# ── measuring ────────────────────────────────────────────────────────────────
KEYS = ("T_avg_Nm", "T_ripple_pct", "V_peak", "V_line_rms_solved_V", "P_cu_W",
        "P_fe_W", "P_mag_eddy_W", "P_loss_total_W", "P_in_W", "P_mech_avg_W")


def summary(r):
    return {k: r.get(k) for k in KEYS if k in r}


def rel_diff(a, b, path="", out=None):
    """Worst relative difference per key path between two result trees."""
    import numpy as np
    if out is None:
        out = {}
    if isinstance(a, dict) and isinstance(b, dict):
        for k in set(a) | set(b):
            if k not in a or k not in b:
                out[path + "/" + str(k)] = "missing on one side"
                continue
            rel_diff(a[k], b[k], path + "/" + str(k), out)
        return out
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        try:
            a = np.asarray(a, float)
            b = np.asarray(b, float)
        except (TypeError, ValueError):
            if len(a) != len(b):
                out[path] = "length differs"
            else:
                for i, (x, y) in enumerate(zip(a, b)):
                    rel_diff(x, y, "%s[%d]" % (path, i), out)
            return out
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        try:
            a = np.asarray(a, float)
            b = np.asarray(b, float)
        except (TypeError, ValueError):
            if not np.array_equal(np.asarray(a), np.asarray(b)):
                out[path] = "non-numeric array differs"
            return out
        if a.shape != b.shape:
            out[path] = "shape %s vs %s" % (a.shape, b.shape)
            return out
        if a.size == 0:
            return out
        fin = np.isfinite(a) & np.isfinite(b)
        if not np.array_equal(np.isfinite(a), np.isfinite(b)):
            out[path] = "nan pattern differs"
            return out
        den = max(float(np.max(np.abs(a[fin]), initial=0.0)),
                  float(np.max(np.abs(b[fin]), initial=0.0)), 1e-300)
        out[path] = float(np.max(np.abs(a[fin] - b[fin]), initial=0.0)) / den
        return out
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None \
            or isinstance(a, str) or isinstance(b, str):
        if a != b:
            out[path] = "value differs: %r vs %r" % (a, b)
        return out
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        if a != b:
            out[path] = "value differs"
        return out
    if math.isnan(fa) and math.isnan(fb):
        return out
    out[path] = abs(fa - fb) / max(abs(fa), abs(fb), 1e-300)
    return out


class RssSampler:
    """Peak RSS of this process, and of this process + all descendants."""

    def __init__(self, dt=0.5):
        import psutil
        self.p = psutil.Process()
        self.dt = dt
        self.peak_self = 0
        self.peak_tree = 0
        self.peak_children = 0
        self._stop = threading.Event()
        self._th = threading.Thread(target=self._run, daemon=True)
        self._th.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                me = self.p.memory_info().rss
                kids = 0
                for c in self.p.children(recursive=True):
                    try:
                        kids += c.memory_info().rss
                    except Exception:           # noqa: BLE001
                        pass
                self.peak_self = max(self.peak_self, me)
                self.peak_children = max(self.peak_children, kids)
                self.peak_tree = max(self.peak_tree, me + kids)
            except Exception:                   # noqa: BLE001
                pass
            self._stop.wait(self.dt)

    def stop(self):
        self._stop.set()
        self._th.join(2)
        mb = 2 ** 20
        return {"peak_self_mb": round(self.peak_self / mb, 1),
                "peak_children_mb": round(self.peak_children / mb, 1),
                "peak_tree_mb": round(self.peak_tree / mb, 1)}


# ── runs ─────────────────────────────────────────────────────────────────────
def run_inprocess(pts):
    from motor_ai_sim.simulation import fem_solver_2d as fs
    rows = []
    rss = RssSampler()
    t0 = time.perf_counter()
    for i, k in enumerate(pts):
        t1 = time.perf_counter()
        r = fs.em_transient_eval(**k)
        rows.append({"i": i, "wall_s": time.perf_counter() - t1, **summary(r)})
        print("inprocess point %d: %.1f s  T=%.6g" % (i, rows[-1]["wall_s"],
                                                     r.get("T_avg_Nm", float("nan"))),
              flush=True)
    wall = time.perf_counter() - t0
    return {"wall_s": wall, "points": rows, "rss": rss.stop(),
            "points_per_h": 3600.0 * len(pts) / wall,
            "mkl_threads": os.environ.get("MKL_NUM_THREADS", "default")}


def run_pool(pts, procs):
    os.environ["SOLVE_POOL"] = "1"
    os.environ["QUEUE_PROCS"] = str(procs)
    from motor_ai_sim import solve_pool as SP
    p = SP.reset_pool(SP.SolvePool(procs=procs))
    out = {"procs": procs, "solo_threads": p.solo}
    # 1) single-solve latency: one point, alone in the pool (solo width)
    rss = RssSampler()
    t1 = time.perf_counter()
    r = SP.em_transient_eval(**pts[-1])
    out["single"] = {"wall_s": time.perf_counter() - t1, **summary(r),
                     "rss": rss.stop()}
    print("pool %d single: %.1f s" % (procs, out["single"]["wall_s"]), flush=True)
    # 2) throughput: every point submitted at once, the pool schedules them
    rows = [None] * len(pts)
    errs = []

    def one(i, k):
        t = time.perf_counter()
        try:
            res = SP.em_transient_eval(**k)
            rows[i] = {"i": i, "wall_s": time.perf_counter() - t, **summary(res)}
        except BaseException as exc:            # noqa: BLE001
            errs.append("%d: %r" % (i, exc))
    rss = RssSampler()
    t0 = time.perf_counter()
    ths = [threading.Thread(target=one, args=(i, k)) for i, k in enumerate(pts)]
    for th in ths:
        th.start()
        time.sleep(0.05)                        # arrival order = point order
    peak_threads = 0
    while any(th.is_alive() for th in ths):
        peak_threads = max(peak_threads, p.snapshot()["threads_in_use"])
        time.sleep(0.5)
    wall = time.perf_counter() - t0
    out["throughput"] = {"wall_s": wall, "points": rows, "errors": errs,
                         "points_per_h": 3600.0 * len(pts) / wall,
                         "rss": rss.stop(),
                         "child_peak_rss_mb": [round(x / 2 ** 20, 1) for x in p._peaks],
                         "peak_threads_in_use": peak_threads}
    print("pool %d: 12 points in %.1f s, errors %d" % (procs, wall, len(errs)), flush=True)
    return out


def run_equality(kw):
    """The same point in-process and through the pool, both single-threaded."""
    from motor_ai_sim.simulation import fem_solver_2d as fs
    os.environ["SOLVE_POOL"] = "1"
    from motor_ai_sim import solve_pool as SP
    SP.reset_pool(SP.SolvePool(procs=1, solo=1))
    progress = []
    t = time.perf_counter()
    a = fs.em_transient_eval(**kw)
    t_in = time.perf_counter() - t
    t = time.perf_counter()
    b = SP.em_transient_eval(progress_cb=lambda *x: progress.append(x), **kw)
    t_pool = time.perf_counter() - t
    diffs = rel_diff(a, b)
    num = {k: v for k, v in diffs.items() if isinstance(v, float)}
    other = {k: v for k, v in diffs.items() if not isinstance(v, float)}
    worst = sorted(num.items(), key=lambda kv: -kv[1])[:15]
    named = {k: num.get("/" + k) for k in KEYS if ("/" + k) in num}
    return {"inprocess_s": t_in, "pool_s": t_pool, "progress_events": len(progress),
            "n_compared": len(num), "max_rel": max(num.values()) if num else 0.0,
            "named_rel": named, "worst": worst, "non_numeric": other,
            "inprocess": summary(a), "pool": summary(b)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dies", required=True)
    ap.add_argument("--case", default="d40_rated", choices=sorted(CASES))
    ap.add_argument("--mode", default="static", choices=("static", "eddy"))
    ap.add_argument("--run", required=True, choices=("equality", "inprocess", "pool"))
    ap.add_argument("--points", type=int, default=12)
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import logging
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    kw = prepare(a)
    res = {"case": a.case, "mode": a.mode, "run": a.run, "host": os.uname().nodename
           if hasattr(os, "uname") else "", "started": time.time()}
    if a.run == "equality":
        res["equality"] = run_equality(kw)
    elif a.run == "inprocess":
        res["inprocess"] = run_inprocess(points(kw, a.points))
    else:
        res["pool"] = run_pool(points(kw, a.points), a.procs)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, default=str)
    print(json.dumps(res, default=str)[:4000])


if __name__ == "__main__":
    main()
