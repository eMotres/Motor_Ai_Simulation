#!/usr/bin/env python3
"""READ-ONLY: list stored 3-D end-effect entries whose scalar k_flux is k_flux_self (the old silent substitution).

Before 2026-11-24 the passport writers put k_flux_self in the place of k_flux whenever the Stage A run's mid-plane
check said FAIL; the stored scalar carried no mark of it, except that ``k_flux == k_flux_self`` exactly.  This walks
the stores it is given, finds every such entry (``end3d_verdict.is_silent_substitution``) and prints one JSON report:

    python scripts/end3d_substitution_list.py --store <shared>/end_effect_passports.json \\
        --root <shared catalog root with dies/<die>/<cfg>.yaml> [--catalog <motor_catalog.json>] \\
        [--snapshots <dir with project snapshot *.json>] > substitution_list.json

Every argument is explicit (nothing is guessed from the environment) and the script writes nothing: it only reads
the files and prints.  Per entry: source, key (fingerprint / die + configuration / file + path), machine, stack,
the applied scalar, the real k_flux from the entry's own-stack table row when there is one, the difference in %
(applied / real - 1), and the verdict: the recorded one when the entry has it, else derived - the old mid-plane rule
(|real / k_flux_self - 1| <= 2 %) and the end-deficit rule (NOT CHECKED with fewer than two table rows at or above the
own stack; such stale rows cannot be fitted either, so it says so).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from motor_ai_sim.end3d_verdict import is_silent_substitution  # noqa: E402


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _walk(o: Any, path: str = "") -> Iterator[Tuple[str, Dict[str, Any]]]:
    if isinstance(o, dict):
        if "k_flux" in o and "k_flux_self" in o:
            yield path or "/", o
        for k, v in o.items():
            yield from _walk(v, "%s/%s" % (path, k))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _walk(v, "%s[%d]" % (path, i))


def _table(e: Dict[str, Any]) -> List[Tuple[float, float]]:
    t = e.get("k_flux_vs_L")
    out = []
    if isinstance(t, dict):
        for a, b in t.items():
            x, y = _num(a), _num(b)
            if x is not None and y is not None:
                out.append((x, y))
    return sorted(out)


def row(source: str, key: str, e: Dict[str, Any], stack: Optional[float]) -> Dict[str, Any]:
    kf = float(e["k_flux"])
    ks = float(e["k_flux_self"])
    tab = _table(e)
    st = _num(e.get("stack_mm")) or stack
    if st is None and len(tab) == 1:
        st = tab[0][0]
    real = None
    if st is not None:
        near = [y for x, y in tab if abs(x - st) <= 1e-6 * max(1.0, st)]
        real = near[0] if near else None
    rec = e.get("k_flux_verdict") or ((e.get("two_d") or {}).get("verdict") if isinstance(e.get("two_d"), dict) else None)
    old = None
    if real is not None and ks:
        old = "PASS" if abs(real / ks - 1.0) <= 0.02 else "FAIL"
    at_or_above = [x for x, _ in tab if st is not None and x >= st * (1 - 1e-9)]
    return {"source": source, "key": key, "machine": e.get("machine"), "stack_mm": st,
            "fidelity": e.get("fidelity"), "generated_utc": e.get("generated_utc"),
            "applied_k_flux": kf, "k_flux_self": ks, "real_k_flux_own_stack": real,
            "applied_over_real_pct": (round(100.0 * (kf / real - 1.0), 3) if real else None),
            "mid_over_2d_at_own_stack": (round(real / ks, 5) if (real is not None and ks) else None),
            "verdict_recorded": rec, "verdict_old_mid_plane_rule": old,
            "verdict_end_deficit_rule": ("NOT CHECKED" if len(at_or_above) < 2 else
                                          "NOT CHECKED (pre-fix table rows, recompute)"),
            "table_rows": len(tab)}


def scan(store: Optional[Path], root: Optional[Path], catalog: Optional[Path],
         snapshots: Optional[Path]) -> Dict[str, Any]:
    hits: List[Dict[str, Any]] = []
    errors: List[str] = []
    seen = 0
    if store:
        try:
            d = json.loads(store.read_text(encoding="utf-8"))
            for fp, e in (d.items() if isinstance(d, dict) else []):
                if isinstance(e, dict):
                    seen += 1
                    if is_silent_substitution(e):
                        hits.append(row("store", str(fp), e, None))
        except (OSError, ValueError) as ex:
            errors.append("store %s: %s" % (store, ex))
    if root:
        import yaml
        for f in sorted((root / "dies").glob("*/*.yaml")):
            if f.name == "die.yaml":
                continue
            try:
                doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except (OSError, ValueError, yaml.YAMLError) as ex:
                errors.append("%s: %s" % (f, ex))
                continue
            L = _num(((doc.get("geometry") or {}) if isinstance(doc, dict) else {}).get("motor_length"))
            for path, e in _walk(doc):
                seen += 1
                if is_silent_substitution(e):
                    hits.append(row("catalog", "%s / %s %s" % (f.parent.name, f.stem, path), e, L))
    for label, base in (("motor_catalog", catalog), ("snapshot", snapshots)):
        if not base:
            continue
        files = [base] if base.is_file() else sorted(base.rglob("*.json"))
        for f in files:
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError) as ex:
                errors.append("%s: %s" % (f, ex))
                continue
            for path, e in _walk(doc):
                seen += 1
                if is_silent_substitution(e):
                    hits.append(row(label, "%s %s" % (f.name, path), e, _num((e.get("L0_mm")))))
    uniq = {}
    for h in hits:
        uniq.setdefault((h["applied_k_flux"], h["k_flux_self"], h["real_k_flux_own_stack"]), []).append(h["key"])
    return {"read_only": True, "entries_seen": seen, "substituted": hits,
            "unique": [{"applied_k_flux": a, "k_flux_self": b, "real_k_flux_own_stack": c, "carried_by": keys}
                       for (a, b, c), keys in uniq.items()],
            "errors": errors}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--store", type=Path, help="end_effect_passports.json (fingerprint -> entry)")
    ap.add_argument("--root", type=Path, help="catalog root holding dies/<die>/<cfg>.yaml")
    ap.add_argument("--catalog", type=Path, help="motor_catalog.json")
    ap.add_argument("--snapshots", type=Path, help="a file or a folder of project snapshot *.json")
    a = ap.parse_args(argv)
    if not (a.store or a.root or a.catalog or a.snapshots):
        ap.error("name at least one of --store, --root, --catalog, --snapshots")
    json.dump(scan(a.store, a.root, a.catalog, a.snapshots), sys.stdout, indent=1, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
