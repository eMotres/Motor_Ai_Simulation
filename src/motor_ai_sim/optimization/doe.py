"""Screening DOE — UNBIASED global variable importance.

Latin-Hypercube samples the geometry search space (each whitelisted variable over
a band around the baseline, clamped to the schema), FEM-evaluates every sampled
design at a fixed representative current, and computes RandomForest variable
importance for ripple / torque / efficiency.

Unlike the importance from an optimization run — whose samples cluster near one
local optimum, so the importance is *local/biased* — the LHS samples span the
whole design box, so the importance is **global / unbiased**: it tells you which
levers genuinely move ripple vs torque vs efficiency before you optimize.

Fixed current (not target-torque) on purpose: it lets torque VARY across samples,
so torque importance is actually modelable (target-torque holds it ~constant).

Writes to ``config/.doe_dataset.jsonl`` — a SEPARATE file, and passes _log=False
to the evaluator so it does NOT pollute the optimizer's surrogate dataset.

CLI:  python -m motor_ai_sim.optimization.doe --n 60 --current 150 --band 0.35 [--sectors -1]
"""
from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

import numpy as np


def doe_path() -> str:
    here = os.path.dirname(__file__)
    return os.path.abspath(os.path.join(here, "..", "..", "..", "config", ".doe_dataset.jsonl"))


def sample_bounds(band: float) -> Dict[str, Any]:
    """{var: (lo, hi, is_int, is_mm)} — a ±band box around the baseline geometry,
    clamped to the schema min/max, for every numeric whitelisted variable."""
    from motor_ai_sim.config import get_config
    cfg = get_config()
    schema = cfg.get("geometry_schema", {})
    geo = cfg.get("geometry", {})
    wl = cfg.get("sweep_whitelist", []) or []
    out: Dict[str, Any] = {}
    for v in wl:
        m = schema.get(v, {})
        if m.get("min") is None or m.get("max") is None or str(m.get("type", "float")) == "string":
            continue
        s_lo, s_hi = float(m["min"]), float(m["max"])
        base = float(geo.get(v, (s_lo + s_hi) / 2))
        d = band * abs(base) if base else band * (s_hi - s_lo)
        lo, hi = max(s_lo, base - d), min(s_hi, base + d)
        if hi > lo:
            out[v] = (lo, hi, str(m.get("type", "float")) == "int",
                      str(m.get("unit", "")).strip().lower() == "mm")
    return out


def run_doe(n: int = 60, current_a: float = 150.0, band: float = 0.35, n_sectors: int = -1,
            steps: int = 18, gamma_deg: float = 0.0, coil_temp_c: float = 120.0,
            mesh_size_mm: float = 4.0, min_size_mm: float = 0.3, workers: int = 10,
            log=print) -> List[Dict[str, Any]]:
    from scipy.stats.qmc import LatinHypercube
    from motor_ai_sim.routes.optimization import _subprocess_eval

    bnd = sample_bounds(band)
    names = list(bnd.keys())
    lo = np.array([bnd[k][0] for k in names])
    hi = np.array([bnd[k][1] for k in names])
    is_int = [bnd[k][2] for k in names]
    is_mm = [bnd[k][3] for k in names]
    unit = LatinHypercube(d=len(names), seed=0).random(n)
    samples = lo + unit * (hi - lo)

    def _overrides(row):
        ov = {}
        for i, k in enumerate(names):
            v = float(row[i])
            ov[k] = float(round(v)) if is_int[i] else (round(v, 1) if is_mm[i] else v)
        return ov

    cand = [_overrides(samples[i]) for i in range(n)]

    def _ev(ov):
        # _log=False → DOE samples do NOT pollute the optimizer's surrogate dataset.
        return ov, _subprocess_eval(ov, current_a, steps, coil_temp_c, n_periods=1.0,
                                    gamma_deg=gamma_deg, mesh_size_mm=mesh_size_mm,
                                    min_size_mm=min_size_mm, n_sectors=n_sectors, _log=False)

    recs: List[Dict[str, Any]] = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_ev, ov) for ov in cand]
        for fut in as_completed(futs):
            ov, out = fut.result()
            done += 1
            if out and out.get("ok"):
                r = out["res"]
                recs.append({"overrides": ov, "current_a": current_a, "gamma_deg": gamma_deg,
                             "ripple": r.get("T_ripple_pct"), "torque": r.get("T_em_Nm"),
                             "eff": r.get("efficiency"), "td": r.get("torque_per_mass_Nm_kg"),
                             "mass": r.get("mass_total_kg"), "v_peak": r.get("V_peak")})
            log(f"  DOE {done}/{n}  (ok {len(recs)})")

    with open(doe_path(), "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, default=float) + "\n")
    log(f"wrote {len(recs)} evals -> {doe_path()}")
    return recs


def report(recs: List[Dict[str, Any]]) -> None:
    from motor_ai_sim.optimization import surrogate as S
    vi = S.variable_importance(recs, min_samples=15, filter_op=False)   # DOE: keep all samples
    if not vi.get("ok"):
        print(f"\nimportance: not enough successful evals ({vi.get('n')}/{vi.get('need')}).")
        return
    print(f"\n=== UNBIASED variable importance (n={vi['n']} LHS samples) ===")
    for t, info in vi["targets"].items():
        print(f"\n  {info['label']}  (surrogate R2={info['r2']:.2f})")
        for row in info["ranking"][:6]:
            print(f"    {row['var']:<22} {row['importance'] * 100:5.1f}%  {'#' * int(round(row['importance'] * 30))}")


def main(argv=None):
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Screening DOE for unbiased variable importance")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--current", type=float, default=150.0)
    ap.add_argument("--band", type=float, default=0.35, help="± fraction around baseline")
    ap.add_argument("--sectors", type=int, default=-1, help="-1 full disk (accurate ripple), 4 = ¼")
    ap.add_argument("--steps", type=int, default=18)
    args = ap.parse_args(argv)
    print(f"DOE: {args.n} LHS samples · current={args.current}A · band=±{args.band*100:.0f}% · sectors={args.sectors}")
    recs = run_doe(args.n, args.current, args.band, args.sectors, args.steps)
    report(recs)


if __name__ == "__main__":
    main()
