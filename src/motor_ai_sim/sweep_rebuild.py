"""Rebuild saved sweeps from the eval logs - for the sweeps that were never saved.

Owner 2026-10-30: a 7/8-point sweep (``sweep_8``) was replaced by the next Run
and its cancel; ``.last_scan.json`` holds one run.  The numbers are not gone,
though: every FEM eval a sweep makes is logged twice, per workspace -

* ``.opt_dataset.jsonl`` - one row per eval: ``overrides``, ``current_a``,
  ``gamma_deg``, the headline metrics, ``cfg_fp`` (the machine's full
  fingerprint), and (schema 2) ``campaign_id`` / ``campaign_kind`` / ``status``;
* ``.scan_cache.jsonl`` - the full result of every eval that was computed, under
  the same ``campaign_id`` (``"m"``), keyed by a hash - which is why it cannot be
  walked back to a geometry by itself, but CAN be matched to a dataset row by its
  numbers.

This module turns those rows into scan results, one per campaign.  Pure: it takes
the rows (iterables of dicts) and returns dicts; ``routes/optimization.py`` reads
the files, decides which campaigns are already saved, and writes the saves.

What a rebuilt sweep is, honestly
---------------------------------
It is reconstructed, not recorded: ``geom_id`` / ``op_index`` are assigned here,
the operating point's rpm is not in the log (``None``), the variable steps are
estimated from the values seen, and a point whose full result is no longer in the
cache carries only the headline metrics of its dataset row.  Every rebuilt point
says so (``rebuilt``), and so does the save (``rebuilt``).  Re-sweeping is not
needed to *look* at it; applying a point still re-solves it on the machine as it
is now, exactly like any older sweep.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

__all__ = ["group_campaigns", "build_result", "points_key_set", "REBUILD_KINDS"]

#: Campaign kinds that are sweeps (``eval_log.Campaign.kind`` of the scan job).
REBUILD_KINDS = ("scan",)

#: Dataset-row metric -> scan-point key (the subset ``_log_eval`` writes).
_ROW_TO_POINT = (("ripple", "T_ripple_pct"), ("torque", "T_em_Nm"),
                 ("eff", "efficiency"), ("td", "torque_per_mass_Nm_kg"),
                 ("mass", "mass_total_kg"), ("v_peak", "V_peak"),
                 ("p_loss", "P_loss_total_W"), ("thd_ll", "THD_LL_pct"),
                 ("kt", "Kt_Nm_per_Arms"))
_NOT_VARIABLES = ("current_a", "rpm")


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _ov(row: Dict[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for k, v in (row.get("overrides") or {}).items():
        f = _num(v)
        if f is not None:
            out[str(k)] = f
    return out


def _gamma(row: Dict[str, Any], ov: Dict[str, float]) -> float:
    g = _num(row.get("gamma_deg"))
    if g is None:
        g = ov.get("gamma_deg", 0.0)
    return round(g, 4)


def _geom_key(ov: Dict[str, float]) -> Tuple[Tuple[str, float], ...]:
    return tuple(sorted((k, round(v, 6)) for k, v in ov.items() if k != "gamma_deg"))


def points_key_set(points: Iterable[Any]) -> frozenset:
    """``(overrides, current_a)`` of each point - what says two sweeps hold the
    same designs.  Used to see that a campaign is already in a saved sweep."""
    out = set()
    for p in points or ():
        if not isinstance(p, dict):
            continue
        ov = _ov(p)
        ca = _num(p.get("current_a"))
        out.add((_geom_key(ov), None if ca is None else round(ca, 4)))
    return frozenset(out)


def group_campaigns(dataset_rows: Iterable[Dict[str, Any]],
                    kinds: Tuple[str, ...] = REBUILD_KINDS
                    ) -> Dict[str, List[Dict[str, Any]]]:
    """Dataset rows of the wanted campaign kinds, grouped by ``campaign_id``, in
    file order.  Rows without a campaign id (written before schema 2) cannot be
    attributed to a run and are left out; so is anything ``rejected`` and any row
    refused before a FEM started (``pre_solve``) - those are not computed designs."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in dataset_rows:
        cid = r.get("campaign_id")
        if not cid or r.get("campaign_kind") not in kinds:
            continue
        if str(r.get("status") or "") == "rejected" or r.get("pre_solve"):
            continue
        out.setdefault(str(cid), []).append(r)
    return out


def _index_cache(cache_rows: Iterable[Dict[str, Any]], wanted: set) -> Dict[str, Dict[tuple, dict]]:
    """``campaign_id -> {(torque, eff, mass): full result}`` from the scan cache
    (only the campaigns asked for; ok lines only)."""
    idx: Dict[str, Dict[tuple, dict]] = {}
    for rec in cache_rows:
        m = rec.get("m") or {}
        cid = m.get("campaign_id")
        v = rec.get("v")
        if cid not in wanted or not isinstance(v, dict):
            continue
        res = v.get("res") if isinstance(v.get("res"), dict) else v
        key = _metric_key(res.get("T_em_Nm"), res.get("efficiency"), res.get("mass_total_kg"),
                          res.get("P_loss_total_W"))
        if key is not None:
            idx.setdefault(cid, {})[key] = res
    return idx


def _metric_key(t: Any, e: Any, m: Any, pl: Any = None) -> Optional[tuple]:
    """What a dataset row and its cache line share: the numbers they were both
    written from (same floats, so rounding is only a guard)."""
    t, e, m, pl = _num(t), _num(e), _num(m), _num(pl)
    if t is None or e is None:
        return None
    return (round(t, 9), round(e, 9), None if m is None else round(m, 6),
            None if pl is None else round(pl, 6))


def _step_of(values: List[float]) -> float:
    vs = sorted(set(round(v, 6) for v in values))
    gaps = [b - a for a, b in zip(vs, vs[1:]) if b - a > 1e-9]
    return round(min(gaps), 6) if gaps else 0.0


def build_result(campaign_id: str, rows: List[Dict[str, Any]],
                 cache_by_key: Optional[Dict[tuple, dict]] = None) -> Optional[Dict[str, Any]]:
    """One scan result from one campaign's dataset rows, or ``None`` when the
    campaign computed nothing.  ``info`` (dropped rows, how many points have
    their full result) rides in ``result["rebuilt"]``."""
    cache_by_key = cache_by_key or {}
    # One machine per sweep: the fingerprint most rows agree on (ok rows first).
    fps = Counter(r.get("cfg_fp") for r in rows if r.get("status") == "ok" and r.get("cfg_fp"))
    if not fps:
        fps = Counter(r.get("cfg_fp") for r in rows if r.get("cfg_fp"))
    fp = fps.most_common(1)[0][0] if fps else ""
    kept = [r for r in rows if (not fp or r.get("cfg_fp") in (fp, None))]
    dropped_other_machine = len(rows) - len(kept)

    # A design computed twice (re-run) counts once: the last row wins.
    latest: Dict[tuple, Dict[str, Any]] = {}
    for r in kept:
        ov = _ov(r)
        ca = _num(r.get("current_a"))
        if ca is None:
            continue
        latest[(_geom_key(ov), round(ca, 4), _gamma(r, ov))] = r
    rows_u = list(latest.values())
    if not any(r.get("status") == "ok" for r in rows_u):
        return None

    geoms = sorted({k[0] for k in latest})
    gid = {g: i for i, g in enumerate(geoms)}
    ops = sorted({(k[1], k[2]) for k in latest})
    oid = {o: i for i, o in enumerate(ops)}

    # Two designs that share every headline number cannot be told apart in the
    # cache: neither gets the other's full result.
    seen_keys = Counter(_metric_key(r.get("torque"), r.get("eff"), r.get("mass"), r.get("p_loss"))
                        for r in rows_u if r.get("status") == "ok")
    points: List[Dict[str, Any]] = []
    n_full = 0
    for (g, ca, gam), r in sorted(latest.items(), key=lambda kv: (gid[kv[0][0]], oid[(kv[0][1], kv[0][2])])):
        ov = _ov(r)
        base = {"overrides": ov, "current_a": ca, "gamma_deg": gam,
                "geom_id": gid[g], "op_index": oid[(ca, gam)], "fem": True,
                "rebuilt": True}
        if r.get("status") != "ok":
            points.append({**base, "feasible": False, "eligible": False,
                           "final_validation_status": "failed", "apply_eligible": False,
                           "error": str(r.get("error") or r.get("status") or "eval failed")})
            continue
        mk = _metric_key(r.get("torque"), r.get("eff"), r.get("mass"), r.get("p_loss"))
        full = cache_by_key.get(mk) if seen_keys[mk] == 1 else None
        if full is not None:
            n_full += 1
            res = dict(full)
        else:
            res = {pk: r[rk] for rk, pk in _ROW_TO_POINT if r.get(rk) is not None}
        ripple = _num(res.get("T_ripple_pct"))
        points.append({**res, **base, "feasible": True,
                       "final_validation_status": "preliminary", "apply_eligible": False,
                       "eligible": ripple is not None,
                       "rebuilt_from": "cache" if full is not None else "dataset"})

    built = [i for i, p in enumerate(points) if p.get("feasible")]
    pool = [(i, points[i]) for i in built
            if _num(points[i].get("torque_per_mass_Nm_kg")) is not None
            and _num(points[i].get("efficiency")) is not None]
    from motor_ai_sim.optimization.optimizer import _pareto_front
    front = _pareto_front([(points[i]["torque_per_mass_Nm_kg"], points[i]["efficiency"])
                           for i, _ in pool]) if pool else []
    pareto = sorted((pool[k][0] for k in front),
                    key=lambda i: points[i]["torque_per_mass_Nm_kg"])

    by_geom: Dict[int, List[int]] = {}
    for i, p in enumerate(points):
        by_geom.setdefault(p["geom_id"], []).append(i)

    names = sorted({k for g in geoms for k, _ in g})
    variables = []
    for n in names:
        vals = [v for g in geoms for k, v in g if k == n]
        if len(set(vals)) > 1 and n not in _NOT_VARIABLES:
            variables.append({"name": n, "min": min(vals), "max": max(vals),
                              "step": _step_of(vals)})
    ts = sorted(str(r.get("ts")) for r in rows if r.get("ts"))
    return {
        "points": points, "segments": [v for v in by_geom.values() if len(v) == 2],
        "pareto_indices": pareto,
        "baseline": {"feasible": False, "fem": True, "overrides": {}},
        "n_total_points": len(points), "n_built": len(built),
        "n_failed": len(points) - len(built),
        "n_eligible_points": sum(1 for p in points if p.get("eligible")),
        "n_geometries": len(geoms), "variables": variables,
        "operating_points": [{"current_a": ca, "gamma_deg": gam, "rpm": None}
                             for ca, gam in ops],
        "ripple_max_pct": None, "steps_per_period": None, "fem": True,
        "objective": "pareto_torque_density_vs_efficiency_FEM",
        "final_validation_status": "preliminary",
        "run_id": campaign_id, "campaign_id": campaign_id,
        "rebuilt": {"source": "opt_dataset+scan_cache", "cfg_fp": fp,
                    "points_with_full_result": n_full,
                    "rows_other_machine": dropped_other_machine,
                    "started": ts[0] if ts else "", "ended": ts[-1] if ts else ""},
    }


def parse_ts(s: str) -> Optional[datetime]:
    """An ``eval_log`` timestamp (UTC ISO, usually ``...Z``) as an aware datetime."""
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
