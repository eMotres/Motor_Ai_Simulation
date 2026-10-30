"""Stored inputs naming a former per-temperature magnet card -> grade + magnet temperature.

Owner 2026-10-30: one 20 °C magnet card per grade, the temperature set in
Simulation.  The library change ships with the code; THIS script rewrites the
data tree's stored INPUTS (``motor_ai_sim.magnet_migration`` says exactly what
and why).  Old names keep resolving after the code change
(``materials.LEGACY_MAGNET_CARDS``), so the tree may be migrated at any time
after the deploy — nothing breaks in between.

WHAT IT WALKS (the server tree, ``/srv/motres``)::

    shared/dies/<die>/<cfg>.yaml               catalog configurations
    published/<ws>/<die>/<cfg>.yaml            published copies
    workspaces/<ws>/dies/<die>/<cfg>.yaml      workspace copies
    workspaces/<ws>/user_motors.json           "my motors" payloads

and REPORTS, without changing:

    shared/materials_library.yaml              still holding former cards? (the
                                               shared copy WINS over the image's
                                               repo copy - see --library)
    workspaces/<ws>/motor_config.yaml          the live machine: its browser
                                               holds the magnet temperature, so
                                               a rename here could not carry it.
                                               The former name resolves exactly;
                                               the next duty load migrates it.
    frozen product revisions                   read-only for good; their former
                                               names resolve exactly
    names like <grade>_<T>C that are NOT a former library card (typos, cards
                                               of another library) - left alone

Stored run RECORDS (summaries, run sidecars, ledgers) are not touched: they
name the card a past run was solved with, and that name still resolves to it.

DRY RUN BY DEFAULT.  ``--apply`` writes, each changed file first copied to
``<file>.bak-magnets20c-<UTC stamp>``.  ``--library <repo yaml>`` also replaces
``shared/materials_library.yaml`` (backed up the same way) with the given file.

    python scripts/migrate_magnet_cards_20c.py --root /srv/motres
    python scripts/migrate_magnet_cards_20c.py --root /srv/motres --apply \\
        --library /opt/motres/app/config/materials_library.yaml
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set, Tuple

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from motor_ai_sim.magnet_migration import (migrate_config_doc,  # noqa: E402
                                           migrate_motor_payload)
from motor_ai_sim.materials import LEGACY_MAGNET_CARDS, legacy_magnet  # noqa: E402

_PER_T = re.compile(r"^[A-Za-z0-9]+_\d{2,3}C$")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _frozen(root: Path) -> Set[Tuple[str, str]]:
    """(die, config) of every FROZEN product revision (motor_revisions)."""
    p = root / "shared" / "motor_revisions" / "revisions.json"
    out: Set[Tuple[str, str]] = set()
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
        return out
    for lin in (doc.get("lineages") or {}).values():
        for rev in (lin or {}).get("revisions") or []:
            if (rev or {}).get("frozen"):
                out.add((str(rev.get("die")), str(rev.get("config"))))
    return out


def _config_files(root: Path):
    pats = ["shared/dies/*/*.yaml", "published/*/*/*.yaml", "workspaces/*/dies/*/*.yaml"]
    for pat in pats:
        for p in sorted(root.glob(pat)):
            if p.name != "die.yaml" and not p.name.startswith("."):
                yield p


def _unknown_names(obj, found: Set[str]) -> None:
    """Every `<grade>_<T>C` string under a materials map that is NOT a former card."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "magnet" and isinstance(v, str) and _PER_T.match(v) and legacy_magnet(v) is None:
                found.add(v)
            else:
                _unknown_names(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _unknown_names(v, found)


def _backup(p: Path, stamp: str) -> None:
    shutil.copy2(p, p.with_name(f"{p.name}.bak-magnets20c-{stamp}"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, help="data tree root (/srv/motres)")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--library", help="repo materials_library.yaml to install as shared/")
    ap.add_argument("--json", help="also write the full report as JSON here")
    a = ap.parse_args(argv)
    root = Path(a.root)
    stamp = _stamp()
    frozen = _frozen(root)
    report: Dict[str, List] = {"changed": [], "skipped_frozen": [], "live_machines": [],
                               "unknown_names": [], "errors": []}
    unknown: Set[str] = set()

    # THE ORDER THAT MATTERS: the shared library WINS over the image's repo copy
    # (materials._lib_path).  A configuration renamed to a grade the shared copy
    # does not hold would stop resolving, so without the grade cards there (or
    # --library to install them in the same run) nothing is written.
    grades = sorted({g for g, _t in LEGACY_MAGNET_CARDS.values()})
    lib = root / "shared" / "materials_library.yaml"
    missing: List[str] = []
    if lib.is_file() and not a.library:
        try:
            have = (yaml.safe_load(lib.read_text(encoding="utf-8")) or {}).get("magnet") or {}
        except Exception:                                   # noqa: BLE001
            have = {}
        missing = [g for g in grades if g not in have]
    if missing and a.apply:
        print(f"REFUSED: {lib} holds no card for {missing}; configurations renamed to them "
              f"would not resolve. Re-run with --library <repo materials_library.yaml>.")
        return 2

    for p in _config_files(root):
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception as exc:                            # noqa: BLE001
            report["errors"].append(f"{p}: unreadable ({exc})")
            continue
        if not isinstance(doc, dict):
            continue
        _unknown_names(doc, unknown)
        die = p.parent.name
        die_doc = None
        try:
            die_doc = yaml.safe_load((p.parent / "die.yaml").read_text(encoding="utf-8")) or {}
        except Exception:                                   # noqa: BLE001
            pass
        new, notes = migrate_config_doc(doc, die_doc, where=f"{die}/{p.stem}")
        if not notes:
            continue
        if (die, p.stem) in frozen:
            report["skipped_frozen"].append({"file": str(p), "would": notes})
            continue
        report["changed"].append({"file": str(p), "notes": notes})
        if a.apply:
            _backup(p, stamp)
            p.write_text(yaml.safe_dump(new, sort_keys=False, allow_unicode=True),
                         encoding="utf-8")

    for p in sorted(root.glob("workspaces/*/user_motors.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8")) or {}
        except Exception as exc:                            # noqa: BLE001
            report["errors"].append(f"{p}: unreadable ({exc})")
            continue
        _unknown_names(doc, unknown)
        all_notes: List[str] = []
        for owner, motors in (doc or {}).items():
            for mid, entry in (motors or {}).items() if isinstance(motors, dict) else []:
                pl = (entry or {}).get("payload") if isinstance(entry, dict) else None
                if isinstance(pl, dict):
                    new_pl, notes = migrate_motor_payload(pl, where=f"motor {mid}")
                    if notes:
                        entry["payload"] = new_pl
                        all_notes += notes
        if all_notes:
            report["changed"].append({"file": str(p), "notes": all_notes})
            if a.apply:
                _backup(p, stamp)
                p.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    for p in sorted(root.glob("workspaces/*/motor_config.yaml")):
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:                                   # noqa: BLE001
            continue
        name = ((doc or {}).get("materials") or {}).get("magnet")
        hit = legacy_magnet(name) if isinstance(name, str) else None
        if hit:
            report["live_machines"].append(
                f"{p}: live magnet {name} left as is (resolves to {hit[0]} at {hit[1]:g} °C; "
                f"the next duty load replaces it)")

    if lib.is_file():
        try:
            mags = (yaml.safe_load(lib.read_text(encoding="utf-8")) or {}).get("magnet") or {}
        except Exception:                                   # noqa: BLE001
            mags = {}
        old = sorted(n for n in mags if n in LEGACY_MAGNET_CARDS)
        report["library"] = [f"{lib}: magnet cards {sorted(mags)}"
                             + (f"; FORMER per-temperature cards still present: {old}" if old else "")]
        if missing:
            report["library"].append(f"no grade card for {missing}: --apply needs --library")
        if a.library:
            src = Path(a.library)
            if a.apply:
                _backup(lib, stamp)
                shutil.copy2(src, lib)
            report["library"].append(f"{lib} {'replaced' if a.apply else 'would be replaced'} by {src}")
    report["unknown_names"] = sorted(unknown)

    mode = "APPLIED" if a.apply else "DRY RUN (nothing written; --apply to write)"
    print(f"== migrate_magnet_cards_20c: {mode}, root {root}")
    for item in report["changed"]:
        print(f"\n{item['file']}")
        for n in item["notes"]:
            print(f"   {n}")
    for key, title in (("skipped_frozen", "frozen revisions (left as is)"),
                       ("live_machines", "live machines (left as is)"),
                       ("library", "shared materials library"),
                       ("unknown_names", "<grade>_<T>C names that are not former cards (left as is)"),
                       ("errors", "errors")):
        if report.get(key):
            print(f"\n-- {title}:")
            for x in report[key]:
                print(f"   {x if not isinstance(x, dict) else x['file']}")
    print(f"\n{len(report['changed'])} file(s) {'changed' if a.apply else 'to change'}")
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
