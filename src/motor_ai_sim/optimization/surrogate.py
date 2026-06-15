"""Surrogate model + variable-importance + Bayesian-style candidate suggestion.

Trains on the accumulated FEM-evaluation dataset (``config/.opt_dataset.jsonl``,
written per-eval by ``routes.optimization._log_eval``) and answers the two
questions that make the *next* optimization much cheaper:

  1. **Which geometry variables most drive RIPPLE vs TORQUE vs EFFICIENCY?**
     RandomForest impurity + permutation importance per target → a ranked table.
  2. **Which candidate geometries are worth trying next?**
     A cheap surrogate-screened, Bayesian-style acquisition: sample many
     candidates, predict ripple/td/eff with the surrogates, and return the few
     that maximise torque-density × efficiency while staying under the ripple
     gate — so the expensive FEM solver only runs on promising designs.

Deliberately a **RandomForest, not a neural net**: with ~50-300 evaluations over
~16 variables an NN overfits; tree ensembles are data-efficient, need no scaling,
handle the small/biased sample well, and give variable importance for free.

CLI:
    python -m motor_ai_sim.optimization.surrogate \
        [--dataset PATH] [--suggest N] [--target-torque NM] [--ripple-max PCT]
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Targets we model.  key -> (human label, "maximise" | "minimise")
TARGETS: Dict[str, Tuple[str, str]] = {
    "ripple": ("Torque ripple %", "minimise"),
    "torque": ("Torque N·m", "—"),
    "eff":    ("Efficiency", "maximise"),
    "td":     ("Torque / mass (Nm/kg)", "maximise"),
}


def dataset_path() -> str:
    """Default location of the per-eval dataset (shared with the backend)."""
    here = os.path.dirname(__file__)
    return os.path.abspath(os.path.join(here, "..", "..", "..", "config", ".opt_dataset.jsonl"))


def load_dataset(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read the JSONL dataset; tolerant of partial/corrupt trailing lines."""
    path = path or dataset_path()
    recs: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        return recs
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except Exception:
                pass
    return recs


def filter_operating(recs: List[Dict[str, Any]], min_torque_frac: float = 0.6) -> List[Dict[str, Any]]:
    """Keep only evals near the target operating torque.  The MTPA γ-sweep probes
    at a fixed current → much lower torque (e.g. 35 vs 77.7 N·m); those rows would
    pollute a geometry→objective surrogate, so drop everything below
    ``min_torque_frac × max torque`` (the target-torque cluster)."""
    ts = [float(r["torque"]) for r in recs if r.get("torque") is not None]
    if not ts:
        return recs
    cut = min_torque_frac * max(ts)
    return [r for r in recs if r.get("torque") is not None and float(r["torque"]) >= cut]


def build_matrix(
    recs: List[Dict[str, Any]],
) -> Tuple[np.ndarray, List[str], Dict[str, np.ndarray]]:
    """Feature matrix X (geometry overrides + γ) and per-target y vectors.

    Only varied features are kept (constant columns carry no information for
    importance and break the surrogate's relative comparisons).  Missing keys
    in a record are imputed with that column's median.
    """
    # Union of override keys across all records (+ the load angle γ, an operating
    # variable that also affects ripple/torque).
    keys: set = set()
    for r in recs:
        keys.update((r.get("overrides") or {}).keys())
    feat_keys = sorted(keys) + ["gamma_deg"]

    rows, ys = [], {t: [] for t in TARGETS}
    for r in recs:
        if any(r.get(t) is None for t in TARGETS):
            continue
        ov = r.get("overrides") or {}
        row = [ov.get(k) for k in feat_keys[:-1]] + [r.get("gamma_deg")]
        rows.append([np.nan if v is None else float(v) for v in row])
        for t in TARGETS:
            ys[t].append(float(r[t]))

    X = np.asarray(rows, dtype=float)
    if X.size == 0:
        return X, feat_keys, {t: np.asarray(v) for t, v in ys.items()}

    # Impute NaNs with column medians.
    for j in range(X.shape[1]):
        col = X[:, j]
        med = np.nanmedian(col) if np.isfinite(col).any() else 0.0
        col[~np.isfinite(col)] = med
        X[:, j] = col

    # Drop constant (un-varied) columns.
    keep = [j for j in range(X.shape[1]) if np.ptp(X[:, j]) > 1e-12]
    X = X[:, keep]
    feat_keys = [feat_keys[j] for j in keep]
    return X, feat_keys, {t: np.asarray(v, dtype=float) for t, v in ys.items()}


def _fit_forest(X: np.ndarray, y: np.ndarray):
    from sklearn.ensemble import RandomForestRegressor
    n = len(y)
    rf = RandomForestRegressor(
        n_estimators=300,
        max_features="sqrt" if X.shape[1] > 4 else 1.0,
        min_samples_leaf=max(1, n // 30),
        random_state=0,
        n_jobs=-1,
    )
    rf.fit(X, y)
    return rf


def variable_importance(
    recs: List[Dict[str, Any]], min_samples: int = 20
) -> Dict[str, Any]:
    """Per-target ranked variable importance (permutation importance, which is
    robust to the correlated features a guided search produces)."""
    X, feat_keys, ys = build_matrix(filter_operating(recs))
    if len(X) < min_samples:
        return {"ok": False, "n": len(X), "need": min_samples, "features": feat_keys}

    from sklearn.inspection import permutation_importance

    out: Dict[str, Any] = {"ok": True, "n": len(X), "features": feat_keys, "targets": {}}
    for t, (label, goal) in TARGETS.items():
        y = ys[t]
        if np.ptp(y) < 1e-9:
            continue
        rf = _fit_forest(X, y)
        # cross-val-ish R^2 on the training set is optimistic; report OOB-style via
        # permutation importance + a simple train R^2 for context.
        r2 = float(rf.score(X, y))
        perm = permutation_importance(rf, X, y, n_repeats=20, random_state=0, n_jobs=-1)
        imp = perm.importances_mean
        imp = imp / (imp.sum() + 1e-12)  # normalise to fractions
        ranked = sorted(
            ({"var": feat_keys[i], "importance": float(imp[i])} for i in range(len(feat_keys))),
            key=lambda d: d["importance"], reverse=True,
        )
        out["targets"][t] = {"label": label, "goal": goal, "r2": r2, "ranking": ranked}
    return out


def suggest(
    recs: List[Dict[str, Any]],
    bounds: Optional[Dict[str, Tuple[float, float]]] = None,
    n: int = 5,
    ripple_max: float = 5.0,
    n_candidates: int = 4000,
    seed: int = 0,
) -> Dict[str, Any]:
    """Bayesian-style suggestion: fit surrogates for ripple/td/eff, sample many
    candidates within ``bounds`` (default = observed min/max per variable), predict,
    and return the top-``n`` by acquisition = td̂ × eff̂ among those with ripplê ≤
    gate.  Adds an exploration bonus from the RF prediction spread (uncertainty)."""
    X, feat_keys, ys = build_matrix(filter_operating(recs))
    if len(X) < 20:
        return {"ok": False, "n": len(X), "need": 20}

    rng = np.random.default_rng(seed)
    lo = X.min(axis=0); hi = X.max(axis=0)
    if bounds:
        for i, k in enumerate(feat_keys):
            if k in bounds:
                lo[i], hi[i] = bounds[k]
    span = np.where(hi > lo, hi - lo, 1.0)

    # The feasible region (ripple ≤ gate) is a THIN slice of a 16-D box → uniform
    # sampling almost never lands in it.  Sample mostly as local perturbations of
    # the best OBSERVED designs (trust-region, where feasibility lives) + some
    # uniform global exploration.
    obs_feas = ys["ripple"] <= ripple_max
    if obs_feas.any():
        seeds = X[obs_feas][np.argsort((ys["td"] * ys["eff"])[obs_feas])[::-1][:5]]
    else:
        seeds = X[np.argsort(ys["ripple"])[:5]]                 # the closest-to-feasible evals
    per_seed = max(1, n_candidates // (2 * len(seeds)))
    local = np.vstack([s + rng.normal(0, 0.06, (per_seed, len(feat_keys))) * span for s in seeds])
    glob = lo + (hi - lo) * rng.random((max(1, n_candidates - len(local)), len(feat_keys)))
    cand = np.clip(np.vstack([local, glob]), lo, hi)

    models = {t: _fit_forest(X, ys[t]) for t in ("ripple", "td", "eff")}
    pred = {t: m.predict(cand) for t, m in models.items()}
    # RF uncertainty = std across trees (exploration bonus on the ripple gate).
    rip_std = np.std([est.predict(cand) for est in models["ripple"].estimators_], axis=0)

    feasible = pred["ripple"] - 0.5 * rip_std <= ripple_max     # optimistic on the gate
    if not feasible.any():                                      # nothing predicted-feasible →
        feasible = pred["ripple"] <= np.percentile(pred["ripple"], 5)   # take the lowest-ripple 5%
    score = np.where(feasible, pred["td"] * pred["eff"], -np.inf)
    order = np.argsort(score)[::-1][:n]

    sugg = []
    for idx in order:
        if not np.isfinite(score[idx]):
            continue
        sugg.append({
            "overrides": {feat_keys[i]: round(float(cand[idx, i]), 4)
                          for i in range(len(feat_keys)) if feat_keys[i] != "gamma_deg"},
            "gamma_deg": round(float(cand[idx, feat_keys.index("gamma_deg")]), 2) if "gamma_deg" in feat_keys else None,
            "pred_ripple": round(float(pred["ripple"][idx]), 2),
            "pred_td": round(float(pred["td"][idx]), 3),
            "pred_eff": round(float(pred["eff"][idx]), 4),
        })
    return {"ok": True, "n_train": len(X), "ripple_max": ripple_max, "suggestions": sugg}


def _print_report(recs, n_suggest, ripple_max):
    vi = variable_importance(recs)
    print(f"\n=== Surrogate variable importance (n={vi.get('n')} evals) ===")
    if not vi.get("ok"):
        print(f"  not enough data yet — have {vi.get('n')}, need {vi.get('need')}.")
        return
    for t, info in vi["targets"].items():
        arrow = {"minimise": "↓ want low", "maximise": "↑ want high", "—": ""}[info["goal"]]
        print(f"\n  {info['label']} {arrow}   (surrogate R²={info['r2']:.2f})")
        for row in info["ranking"][:6]:
            bar = "█" * int(round(row["importance"] * 30))
            print(f"    {row['var']:<22} {row['importance']*100:5.1f}%  {bar}")

    print(f"\n=== Suggested next designs (maximise Nm/kg × eff, ripple ≤ {ripple_max}%) ===")
    sg = suggest(recs, n=n_suggest, ripple_max=ripple_max)
    if not sg.get("ok"):
        print(f"  not enough data — have {sg.get('n')}, need {sg.get('need')}.")
        return
    for i, s in enumerate(sg["suggestions"], 1):
        print(f"  #{i}: ripple≈{s['pred_ripple']}%  Nm/kg≈{s['pred_td']}  eff≈{s['pred_eff']*100:.2f}%")
        print(f"       {s['overrides']}")


def main(argv=None):
    import sys
    try:                                   # Windows console is cp1252 → force UTF-8 for ↓↑²×·
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Motor optimizer surrogate / variable importance")
    ap.add_argument("--dataset", default=None, help="path to .opt_dataset.jsonl")
    ap.add_argument("--suggest", type=int, default=5, help="number of next-design suggestions")
    ap.add_argument("--ripple-max", type=float, default=5.0, help="ripple gate %% for suggestions")
    args = ap.parse_args(argv)
    recs = load_dataset(args.dataset)
    print(f"loaded {len(recs)} evaluations from {args.dataset or dataset_path()}")
    _print_report(recs, args.suggest, args.ripple_max)


if __name__ == "__main__":
    main()
