#!/usr/bin/env python3
"""READ-ONLY: how many stored geometry fingerprints match the machine they belong to, by the canonical fingerprint, by its legacy49
alias (written 2026-11-15 .. 2026-11-25) or by neither.  Writes nothing (no file under --root / --store is opened for writing,
nothing is created); the result is JSON on stdout.

    python scripts/geo_fingerprint_match_list.py --root <dies root> [--store end_effect_passports.json ...]

* ``--root``: a dies root (``<root>/<die>/die.yaml`` + ``<root>/<die>/<config>.yaml``): every ``geometry_fingerprint`` /
  ``geo_fingerprint`` field of every yaml / json file of a die is judged against the machines of THAT die (die geometry + the
  configuration's ``geometry_overrides``).
* ``--store``: a passport store (a json object keyed by fingerprint): each key is judged against the machines of the whole root.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Set, Tuple

FP_KEYS = ("geometry_fingerprint", "geo_fingerprint")


def _load(p: Path) -> Any:
    import yaml
    text = p.read_text(encoding="utf-8")
    return json.loads(text) if p.suffix == ".json" else yaml.safe_load(text)


def _fps_of(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in FP_KEYS and isinstance(v, str) and v:
                yield v
            else:
                yield from _fps_of(v)
    elif isinstance(node, list):
        for v in node:
            yield from _fps_of(v)


def machine_fps(root: Path) -> Dict[str, Dict[str, Tuple[str, str]]]:
    """``{die: {config: (canonical, legacy49)}}`` of every machine of the dies root."""
    from motor_ai_sim.routes.simulation import _geometry_fingerprints
    out: Dict[str, Dict[str, Tuple[str, str]]] = {}
    for d in sorted(p for p in root.iterdir() if p.is_dir() and (p / "die.yaml").is_file()):
        die_geo = dict((_load(d / "die.yaml") or {}).get("geometry") or {})
        for c in sorted(d.glob("*.yaml")):
            if c.name == "die.yaml":
                continue
            doc = _load(c) or {}
            geo = {**die_geo, **dict(doc.get("geometry_overrides") or {})}
            out.setdefault(d.name, {})[c.stem] = tuple(_geometry_fingerprints(geo))     # type: ignore[assignment]
    return out


def classify(fp: str, machines: Dict[str, Tuple[str, str]]) -> str:
    if any(fp == c for c, _l in machines.values()):
        return "canonical"
    if any(fp == l for _c, l in machines.values()):
        return "alias"
    return "neither"


def run(root: Path, stores: List[Path]) -> Dict[str, Any]:
    machines = machine_fps(root)
    every: Dict[str, Tuple[str, str]] = {f"{d}/{c}": v for d, cs in machines.items() for c, v in cs.items()}
    counts = {"canonical": 0, "alias": 0, "neither": 0}
    per_die: Dict[str, Dict[str, int]] = {}
    for d in sorted(machines):
        pd = per_die.setdefault(d, {"canonical": 0, "alias": 0, "neither": 0})
        for f in sorted((root / d).rglob("*")):
            if f.suffix not in (".yaml", ".json") or not f.is_file():
                continue
            try:
                doc = _load(f)
            except Exception:                                                    # noqa: BLE001 - an unreadable file is not a record
                continue
            for fp in _fps_of(doc):
                k = classify(fp, machines[d])
                pd[k] += 1
                counts[k] += 1
    stores_out = {}
    for s in stores:
        keys = [k for k in (_load(s) or {}) if isinstance(k, str)]
        c = {"canonical": 0, "alias": 0, "neither": 0}
        for k in keys:
            c[classify(k, every)] += 1
        stores_out[str(s)] = {"keys": len(keys), **c}
    return {"root": str(root), "machines": len(every), "records": counts, "per_die": per_die, "stores": stores_out}


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, type=Path, help="dies root (explicit; nothing is guessed)")
    ap.add_argument("--store", action="append", default=[], type=Path, help="a passport store (json), may be repeated")
    a = ap.parse_args(argv)
    if not a.root.is_dir():
        print("--root is not a directory: %s" % a.root, file=sys.stderr)
        return 2
    json.dump(run(a.root, a.store), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
