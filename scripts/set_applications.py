#!/usr/bin/env python
"""Set the ``applications`` list on motor configurations (owner decision 2026-10-13).

    python scripts/set_applications.py <dies_root> [<dies_root> ...]            # dry run
    python scripts/set_applications.py <dies_root> [<dies_root> ...] --apply    # write

``<dies_root>`` is a directory of ``<die>/<config>.yaml`` documents: the shared tree
(``/srv/motres/shared/dies``) and each owner workspace copy (``/srv/motres/workspaces/*/dies``).
The table below is the owner's assignment.  Behaviour:

* dry run by default; ``--apply`` writes;
* idempotent: a configuration that already has the wanted value is left alone;
* a configuration that already has a DIFFERENT ``applications`` value is never overwritten
  (reported as ``keeps``) - a later data edit by the owner wins over this table;
* every touched file is backed up first, next to it, as ``<config>.yaml.bak-applications-<stamp>``;
* the new line is inserted as text, so the rest of the file is byte-identical (line endings
  kept), and the result is re-parsed and compared before it is accepted;
* dies and configurations the table does not name are listed as ``no rule`` and not touched
  (nothing is left undecided).
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from motor_ai_sim.applications import APPLICATIONS, validate_applications  # noqa: E402

#: die -> (applications, configurations).  ``None`` configurations = every configuration of the die.
RULES: Dict[str, Tuple[List[str], Optional[List[str]]]] = {
    "CIANO14 30_10": (["propeller"], None),
    "CIANO14 40 new": (["propeller"], ["L12", "L20"]),
    "CIANO28 150_35": (["propeller"], None),
    "CIANO28 150_35 new": (["propeller"], None),
    "CIANO20 150_35 new": (["propeller"], None),
    "CIANO28 85 20SW1200": (["robot"], ["L13"]),
    "CIANO28 85 20RSW175": (["robot"], ["L13"]),
    "100 mm · 24s-28p mid-torque": (["robot"], None),
    "CIANO10 200 opt": (["aviation"], ["L155 motor", "L180 gen"]),
    "CIANO10 200": (["aviation"], None),
    "CILN28": (["marine"], None),
    "M1 850Nm": (["marine"], None),            # boats (owner update 2026-10-13)
}

for _die, (_apps, _cfgs) in RULES.items():          # the table itself must be valid
    validate_applications(_apps)


def _insert_line(text: str, apps: List[str]) -> str:
    """``text`` with a top-level ``applications: [...]`` line added after ``die:`` / ``name:``."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    line = "applications: [" + ", ".join(apps) + "]"
    at = 0
    for key in ("role:", "die:", "name:"):
        hit = next((i for i, ln in enumerate(lines) if ln.startswith(key)), None)
        if hit is not None:
            at = hit + 1
            break
    lines.insert(at, line)
    return nl.join(lines)


def plan_file(path: Path, want: List[str]) -> Tuple[str, Optional[str]]:
    """``(status, new_text)``; status is ``set`` | ``ok`` | ``keeps`` | ``error: ...``."""
    raw = path.read_bytes().decode("utf-8")
    try:
        doc = yaml.safe_load(raw) or {}
    except Exception as exc:                                  # noqa: BLE001
        return f"error: unreadable yaml ({exc})", None
    if not isinstance(doc, dict):
        return "error: not a mapping", None
    if "applications" in doc:
        try:
            have = validate_applications(doc["applications"])
        except ValueError as exc:
            return f"error: existing value invalid ({exc})", None
        return ("ok" if have == want else "keeps"), None
    new = _insert_line(raw, want)
    after = yaml.safe_load(new)
    expected = dict(doc)
    expected["applications"] = want
    if after != expected:
        return "error: the inserted line changed the document", None
    return "set", new


def run(roots: List[Path], apply: bool) -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    errors = 0
    for root in roots:
        print(f"== {root}")
        if not root.is_dir():
            print("   not a directory")
            errors += 1
            continue
        for die_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
            rule = RULES.get(die_dir.name)
            cfgs = sorted(p for p in die_dir.glob("*.yaml") if p.name != "die.yaml")
            for cf in cfgs:
                if rule is None:
                    print(f"   no rule   {die_dir.name} / {cf.stem}")
                    continue
                apps, only = rule
                if only is not None and cf.stem not in only:
                    print(f"   no rule   {die_dir.name} / {cf.stem}")
                    continue
                status, new = plan_file(cf, apps)
                print(f"   {status:<9} {die_dir.name} / {cf.stem}  -> {apps}")
                if status.startswith("error"):
                    errors += 1
                elif status == "set" and apply:
                    shutil.copy2(cf, cf.with_name(f"{cf.name}.bak-applications-{stamp}"))
                    cf.write_bytes(new.encode("utf-8"))
    if not apply:
        print("dry run - nothing written; add --apply to write")
    return 1 if errors else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("roots", nargs="+", type=Path, help="dies root(s)")
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("allowed values: " + ", ".join(APPLICATIONS))
    return run(a.roots, a.apply)


if __name__ == "__main__":
    raise SystemExit(main())
