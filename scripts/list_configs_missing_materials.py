"""List the catalog configurations whose ``materials:`` block does not name the
slot liner / the wire enamel (and, beside them, any other saved part).

Owner 2026-10-30: ``POST /api/family/config`` kept only the magnet and the two
steels, so a configuration created from a machine lined with Al2O3 stored no
liner, and its load filled Nomex.  The create path now keeps every part
(``routes.family._SAVED_MATERIAL_KEYS``); THIS script finds the files written
before that, so each can be corrected by someone who knows what the machine is
made of.  The project default is NOT written in: a missing liner is a question
about the machine, not a value to guess.

READ-ONLY.  There is no --apply.  It walks (the server tree, ``/srv/motres``)::

    shared/dies/<die>/<cfg>.yaml
    published/<ws>/<die>/<cfg>.yaml
    workspaces/<ws>/dies/<die>/<cfg>.yaml

and prints one row per configuration lacking a liner or an enamel: which parts
the file does not name, and what its duties' own ``materials:`` maps say for the
liner / enamel (a load of that duty reads them first — see ``payload``).

    python scripts/list_configs_missing_materials.py --root /srv/motres
    python scripts/list_configs_missing_materials.py --root /srv/motres --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import yaml

#: = routes.family._SAVED_MATERIAL_KEYS (kept as a literal so the script runs
#: without the app's dependencies; tests/test_config_materials_roundtrip.py
#: pins the two together).
SAVED_KEYS: Tuple[str, ...] = (
    "magnet", "stator_core", "rotor_core", "shaft", "sleeve",
    "slot", "slot_insulation", "wire_insulation",
)
INSULATION_KEYS: Tuple[str, ...] = ("slot_insulation", "wire_insulation")


def _config_files(root: Path) -> Iterator[Tuple[str, Path]]:
    """(layer, path) for every configuration yaml under the three trees."""
    trees = [("shared", root / "shared" / "dies")]
    pub = root / "published"
    if pub.is_dir():
        trees += [(f"published/{w.name}", w) for w in sorted(pub.iterdir()) if w.is_dir()]
    wss = root / "workspaces"
    if wss.is_dir():
        trees += [(f"workspace/{w.name}", w / "dies")
                  for w in sorted(wss.iterdir()) if (w / "dies").is_dir()]
    for layer, base in trees:
        if not base.is_dir():
            continue
        for p in sorted(base.glob("*/*.yaml")):
            if p.name != "die.yaml":
                yield layer, p


def scan_config(doc: Dict[str, Any]) -> Dict[str, Any]:
    """What one configuration document is missing.  Pure — the test drives it."""
    mats = doc.get("materials") or {}
    if not isinstance(mats, dict):
        mats = {}
    missing = [k for k in SAVED_KEYS if not mats.get(k)]
    try:
        sleeved = float((doc.get("geometry_overrides") or {}).get("sleeve_thickness") or 0) > 0
    except (TypeError, ValueError):
        sleeved = False
    if not sleeved and "sleeve" in missing:
        missing.remove("sleeve")      # no band, nothing to name
    duty_ins: Dict[str, Dict[str, str]] = {}
    for d in doc.get("duties") or []:
        if not isinstance(d, dict):
            continue
        dm = d.get("materials") or {}
        picks = {k: dm[k] for k in INSULATION_KEYS
                 if isinstance(dm, dict) and dm.get(k)}
        if picks:
            duty_ins[str(d.get("name") or "")] = picks
    return {"missing": missing,
            "lacks_insulation": [k for k in INSULATION_KEYS if k in missing],
            "named": {k: mats[k] for k in SAVED_KEYS if mats.get(k)},
            "duty_insulation": duty_ins}


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default="/srv/motres", help="data tree root")
    ap.add_argument("--json", help="also write the rows to this file")
    a = ap.parse_args(argv)
    root = Path(a.root)
    rows: List[Dict[str, Any]] = []
    n = 0
    for layer, p in _config_files(root):
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception as e:  # noqa: BLE001 - one bad file must not hide the rest
            print(f"UNREADABLE {p}: {e}", file=sys.stderr)
            continue
        if not isinstance(doc, dict):
            continue
        n += 1
        s = scan_config(doc)
        if s["lacks_insulation"]:
            rows.append({"layer": layer, "die": p.parent.name, "config": p.stem,
                         "file": str(p), **s})
    for r in rows:
        duty = "; ".join(f"{k}: " + ", ".join(f"{kk}={vv}" for kk, vv in v.items())
                         for k, v in r["duty_insulation"].items()) or "-"
        print(f"{r['layer']:<24} {r['die']} / {r['config']}\n"
              f"    missing: {', '.join(r['missing'])}\n"
              f"    named:   {', '.join(f'{k}={v}' for k, v in r['named'].items()) or '-'}\n"
              f"    duties:  {duty}")
    print(f"\n{len(rows)} of {n} configuration(s) lack a liner or an enamel "
          f"(read-only; nothing written)")
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1, ensure_ascii=False),
                                encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
