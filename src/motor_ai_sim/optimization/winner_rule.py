"""The owner's winner rule for comparing designs (2026-11-18, ~/jobs/RULE_compare_configurations.md), as a post-ranking.

Every design is computed at TWO (or more) currents; its points go on the chart efficiency (y) against torque per mass in N*m/kg (x) and
form a LINE.  The design whose line lies higher is the better one, read at EQUAL N*m/kg (never at equal current).  Then:

1. the group of designs whose lines lie highest (within ``tie_band`` of the best line, as an absolute efficiency difference), and
2. inside that group the design with the LOWEST torque ripple (the largest ripple of its points); a design whose ripple exceeds the
   requirement (``ripple_max_pct``, 5 % by default) is MARKED, and ranks behind the designs that meet it within its group.

Pure and dependency-free (``rank`` takes plain dicts): used by ``POST /api/optimization/winner_rank`` and by a Sweep that was asked to
(``rank_winner``).  A design with fewer than two usable points has no line: it is listed as ``incomplete`` and never ranked.
If the lines have no common N*m/kg range the heights are read over the union with each line held at its end values, and the answer says
``common_range: false``: the reader must not treat that as a like-for-like comparison.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

RIPPLE_MAX_PCT = 5.0
TIE_BAND = 0.002            # absolute efficiency (fraction): 0.2 percentage points
N_SAMPLES = 41


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _line(points: Sequence[dict]) -> List[Tuple[float, float]]:
    """The design's line: ``[(x, y)]`` sorted by x (x = N*m/kg, y = efficiency as a fraction), duplicates of x averaged."""
    pts: Dict[float, List[float]] = {}
    for p in points:
        x, y = _num(p.get("td")), _num(p.get("eff"))
        if x is None or y is None:
            continue
        pts.setdefault(round(x, 9), []).append(y)
    return sorted((x, sum(ys) / len(ys)) for x, ys in pts.items())


def _at(line: Sequence[Tuple[float, float]], x: float) -> float:
    """The line's efficiency at ``x`` (linear between its points; held at the end values outside them)."""
    if x <= line[0][0]:
        return line[0][1]
    if x >= line[-1][0]:
        return line[-1][1]
    for (x0, y0), (x1, y1) in zip(line, line[1:]):
        if x0 <= x <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return line[-1][1]


def _xs(lo: float, hi: float) -> List[float]:
    return [lo + (hi - lo) * i / (N_SAMPLES - 1) for i in range(N_SAMPLES)] if hi > lo else [lo]


def _wins(a: Sequence[Tuple[float, float]], b: Sequence[Tuple[float, float]], lo: float, hi: float) -> List[dict]:
    """Where each of two lines is higher over ``[lo, hi]``: ``[{x0, x1, winner: "a"|"b"|"tie"}]`` (sampled)."""
    out: List[dict] = []
    for x in _xs(lo, hi):
        d = _at(a, x) - _at(b, x)
        w = "tie" if abs(d) < 1e-9 else ("a" if d > 0 else "b")
        if out and out[-1]["winner"] == w:
            out[-1]["x1"] = x
        else:
            out.append({"x0": x, "x1": x, "winner": w})
    return out


def rank(points: Sequence[dict], ripple_max_pct: float = RIPPLE_MAX_PCT, tie_band: float = TIE_BAND) -> Dict[str, Any]:
    """Rank designs.  ``points``: dicts with ``geom`` (the design's id), ``td`` (N*m/kg), ``eff`` (efficiency, a fraction) and
    ``ripple`` (percent); optional ``current_a``.  Returns ``{ranking, winner, common_range, x_range, crossings, rule}``."""
    by: Dict[str, List[dict]] = {}
    for p in points or []:
        by.setdefault(str(p.get("geom")), []).append(p)
    designs, incomplete = [], []
    for g, pts in by.items():
        line = _line(pts)
        rips = [r for r in (_num(p.get("ripple")) for p in pts) if r is not None]
        if len(line) < 2:
            incomplete.append({"geom": g, "reason": "fewer than two points with torque per mass and efficiency"})
            continue
        designs.append({"geom": g, "line": line, "ripple_max": max(rips) if rips else None, "n_points": len(line)})
    out: Dict[str, Any] = {"ranking": [], "winner": None, "incomplete": incomplete, "tie_band": tie_band,
                           "ripple_max_pct": ripple_max_pct,
                           "rule": "highest efficiency line at equal N*m/kg first; inside that group the lowest ripple"}
    if not designs:
        out.update(common_range=None, x_range=None, crossings=[])
        return out
    lo = max(d["line"][0][0] for d in designs)
    hi = min(d["line"][-1][0] for d in designs)
    common = lo < hi
    if not common:
        lo = min(d["line"][0][0] for d in designs)
        hi = max(d["line"][-1][0] for d in designs)
    xs = _xs(lo, hi)
    for d in designs:
        d["height"] = sum(_at(d["line"], x) for x in xs) / len(xs)
        r = d["ripple_max"]
        d["ripple_fail"] = (r is None) or (r > ripple_max_pct)         # an unknown ripple never passes
    designs.sort(key=lambda d: -d["height"])
    group, top = 1, designs[0]["height"]
    for d in designs:
        if top - d["height"] > tie_band:
            group += 1
            top = d["height"]
        d["group"] = group
    ordered = sorted(designs, key=lambda d: (d["group"], d["ripple_fail"], d["ripple_max"] if d["ripple_max"] is not None else 1e9,
                                              -d["height"]))
    for i, d in enumerate(ordered, 1):
        d["rank"] = i
    out["ranking"] = [{"rank": d["rank"], "geom": d["geom"], "group": d["group"], "height": round(d["height"], 6),
                       "ripple_max_pct": d["ripple_max"], "ripple_fail": d["ripple_fail"], "n_points": d["n_points"],
                       "line": [{"td": x, "eff": y} for x, y in d["line"]]} for d in ordered]
    out["winner"] = ordered[0]["geom"]
    out["common_range"] = common
    out["x_range"] = [lo, hi]
    win = ordered[0]
    out["crossings"] = [{"winner": win["geom"], "other": d["geom"],
                         "ranges": [{"x0": r["x0"], "x1": r["x1"], "higher": (win["geom"] if r["winner"] == "a" else
                                                                              d["geom"] if r["winner"] == "b" else "tie")}
                                    for r in _wins(win["line"], d["line"], lo, hi)]}
                        for d in ordered[1:]
                        if any(r["winner"] == "b" for r in _wins(win["line"], d["line"], lo, hi))]
    return out
