#!/usr/bin/env python3
"""READ-ONLY: list stored 3-D end-effect factors where k_flux_self was put in the place of k_flux (the old silent substitution).

Before 2026-11-24 the passport writers applied k_flux_self instead of k_flux whenever the Stage A run's mid-plane check
said FAIL.  What they left behind (``end3d_verdict.substitution_of``):

* a catalog card ``end3d`` block: ``{k_flux, k_flux_vs_L: None, fidelity: "... (2-D check failed, raw k_flux not
  used)", k_T}`` - found by its text;
* a full passport ``stage2_3d``: ``k_psi.value`` differs from the own-stack row's real ``k_flux_raw`` (and the label
  "k_flux_self (2-D check failed)");
* a stored entry copied from such a passport: ``k_flux == k_flux_self`` exactly while its own-stack ``k_flux_vs_L`` row
  holds the real k_flux.  Equal scalars without such a row are what an INVALID / no-2-D run writes: they are listed
  apart under ``equal_not_substitution`` and are NOT hits.

    python scripts/end3d_substitution_list.py --store <shared>/end_effect_passports.json \\
        --root <catalog root: dies/<die>/<cfg>.yaml, or <die>/<cfg>.yaml> [--catalog <motor_catalog.json>] \\
        [--full <folder of full passports passport_L*.json>] [--snapshots <dir of project snapshot *.json>] \\
        > substitution_list.json

Every argument is explicit and the script writes nothing: it reads and prints one JSON report.  Per hit: source, key,
how it was found, machine, stack, the applied scalar, k_flux_self when stored, the real k_flux when recoverable, the
difference in % (applied / real - 1), the recorded verdict, and the old mid-plane rule's verdict from real / self.
The end-deficit rule is "not evaluated" here (it needs the run's own rows).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from motor_ai_sim.end3d_verdict import substitution_of  # noqa: E402


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _walk(o: Any, path: str = "", l0: Optional[float] = None) -> Iterator[Tuple[str, Dict[str, Any], Optional[float]]]:
    """Every block with k_flux / stage2_3d, with the nearest enclosing ``L0_mm`` (a card's passport carries its stack
    there, not inside ``end3d``)."""
    if isinstance(o, dict):
        l0 = _num(o.get("L0_mm")) or l0
        if "k_flux" in o or "stage2_3d" in o:
            yield path or "/", o, l0
        for k, v in o.items():
            yield from _walk(v, "%s/%s" % (path, k), l0)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _walk(v, "%s[%d]" % (path, i), l0)


def _real(e: Dict[str, Any], stack: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    """(stack, real k_flux) from a stored entry's own-stack table row, or from a full passport's k_psi_curve."""
    s2 = e.get("stage2_3d")
    if isinstance(s2, dict):
        kp = s2.get("k_psi") if isinstance(s2.get("k_psi"), dict) else {}
        L = _num(kp.get("L_mm")) or _num(s2.get("L_mm")) or stack
        curve = s2.get("k_psi_curve") if isinstance(s2.get("k_psi_curve"), list) else []
        own = next((r for r in curve
                    if isinstance(r, dict) and L is not None and _num(r.get("stack_mm")) is not None
                    and abs(float(r["stack_mm"]) - L) < 1e-6), {})
        return L, _num(own.get("k_flux_raw"))
    t = e.get("k_flux_vs_L") if isinstance(e.get("k_flux_vs_L"), dict) else {}
    rows = [(x, y) for x, y in ((_num(a), _num(b)) for a, b in t.items()) if x is not None and y is not None]
    st = _num(e.get("stack_mm")) or (None if e.get("inherited") else stack)
    if st is None and len(rows) == 1:
        st = rows[0][0]
    return st, next((y for x, y in rows if st is not None and abs(x - st) <= 1e-6 * max(1.0, st)), None)


def row(source: str, key: str, e: Dict[str, Any], stack: Optional[float], how: str) -> Dict[str, Any]:
    s2 = e.get("stage2_3d") if isinstance(e.get("stage2_3d"), dict) else None
    kp = (s2 or {}).get("k_psi") if isinstance((s2 or {}).get("k_psi"), dict) else {}
    kf = _num(kp.get("value")) if s2 else _num(e.get("k_flux"))
    ks = _num(e.get("k_flux_self"))
    st, real = _real(e, stack)
    if s2 and ks is None:
        curve = s2.get("k_psi_curve") if isinstance(s2.get("k_psi_curve"), list) else []
        own = next((r for r in curve
                    if isinstance(r, dict) and st is not None and _num(r.get("stack_mm")) is not None
                    and abs(float(r["stack_mm"]) - st) < 1e-6), {})
        ks = _num(own.get("k_flux_self"))
    old = None
    if real is not None and ks:
        old = "PASS" if abs(real / ks - 1.0) <= 0.02 else "FAIL"
    return {"source": source, "key": key, "found_by": how, "machine": e.get("machine"), "stack_mm": st,
            "fidelity": e.get("fidelity"), "generated_utc": e.get("generated_utc"),
            "applied_k_flux": kf, "k_flux_self": ks, "real_k_flux_own_stack": real,
            "applied_over_real_pct": (round(100.0 * (kf / real - 1.0), 3) if (real and kf is not None) else None),
            "mid_over_2d_at_own_stack": (round(real / ks, 5) if (real is not None and ks) else None),
            "verdict_recorded": e.get("k_flux_verdict") or ((e.get("two_d") or {}).get("verdict")
                                                            if isinstance(e.get("two_d"), dict) else None),
            "verdict_old_mid_plane_rule": old, "verdict_end_deficit_rule": "not evaluated"}


def _equal_only(e: Dict[str, Any]) -> bool:
    kf, ks = _num(e.get("k_flux")), _num(e.get("k_flux_self"))
    return kf is not None and ks is not None and kf == ks


def scan(store: Optional[Path], root: Optional[Path], catalog: Optional[Path], full: Optional[Path],
         snapshots: Optional[Path]) -> Dict[str, Any]:
    hits: List[Dict[str, Any]] = []
    equal: List[Dict[str, Any]] = []
    errors: List[str] = []
    seen = 0

    def judge(source: str, key: str, e: Dict[str, Any], stack: Optional[float]) -> None:
        nonlocal seen
        seen += 1
        try:                                   # one broken record never stops the listing
            how = substitution_of(e, stack)
            if how:
                hits.append(row(source, key, e, stack, how))
            elif _equal_only(e):
                equal.append({"source": source, "key": key, "k_flux": e.get("k_flux"),
                              "fidelity": e.get("fidelity"),
                              "why": "equal by an INVALID / no-2-D run or not recoverable: not a substitution"})
        except Exception as ex:                # noqa: BLE001 - reported, not raised
            errors.append("%s %s: %s: %s" % (source, key, type(ex).__name__, ex))

    if store:
        try:
            d = json.loads(store.read_text(encoding="utf-8"))
            for fp, e in (d.items() if isinstance(d, dict) else []):
                if isinstance(e, dict):
                    judge("store", str(fp), e, None)
        except (OSError, ValueError) as ex:
            errors.append("store %s: %s" % (store, ex))
    if root:
        import yaml
        base = root / "dies" if (root / "dies").is_dir() else root
        for f in sorted(base.glob("*/*.yaml")):
            if f.name == "die.yaml":
                continue
            try:
                doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except (OSError, ValueError, yaml.YAMLError) as ex:
                errors.append("%s: %s" % (f, ex))
                continue
            L = _num(((doc.get("geometry") or {}) if isinstance(doc, dict) else {}).get("motor_length"))
            for path, e, _l0 in _walk(doc):
                judge("catalog", "%s / %s %s" % (f.parent.name, f.stem, path), e, L)
    for label, src in (("motor_catalog", catalog), ("full_passport", full), ("snapshot", snapshots)):
        if not src:
            continue
        files = ([src] if src.is_file() else
                 sorted(src.rglob("passport_L*.json")) if label == "full_passport" else sorted(src.rglob("*.json")))
        for f in files:
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError) as ex:
                errors.append("%s: %s" % (f, ex))
                continue
            for path, e, l0 in _walk(doc):
                judge(label, "%s %s" % (f.name, path), e, l0)
    uniq: Dict[Tuple, List[str]] = {}
    for h in hits:
        uniq.setdefault((h["applied_k_flux"], h["k_flux_self"], h["real_k_flux_own_stack"]), []).append(h["key"])
    return {"read_only": True, "entries_seen": seen, "substituted": hits,
            "unique": [{"applied_k_flux": a, "k_flux_self": b, "real_k_flux_own_stack": c, "carried_by": keys}
                       for (a, b, c), keys in uniq.items()],
            "equal_not_substitution": equal, "errors": errors}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--store", type=Path, help="end_effect_passports.json (fingerprint -> entry)")
    ap.add_argument("--root", type=Path, help="catalog root: dies/<die>/<cfg>.yaml or <die>/<cfg>.yaml")
    ap.add_argument("--catalog", type=Path, help="motor_catalog.json")
    ap.add_argument("--full", type=Path, help="a full passport json or a folder of them")
    ap.add_argument("--snapshots", type=Path, help="a file or a folder of project snapshot *.json")
    a = ap.parse_args(argv)
    if not (a.store or a.root or a.catalog or a.full or a.snapshots):
        ap.error("name at least one of --store, --root, --catalog, --full, --snapshots")
    json.dump(scan(a.store, a.root, a.catalog, a.full, a.snapshots), sys.stdout, indent=1, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
