#!/usr/bin/env python3
"""Fail when the PUBLIC repository holds a 3-D model or the data of a CLOSED motor.

Owner decision 2026-10-19 (AeroStator launch day):
  * closed (private) motors are stored in the PRIVATE repository  eMotres/motor-ai-sim-private;
  * open motors are stored in the public repository;
  * ALL 3-D models of every motor are stored ONLY in the private repository.

This guard runs in CI (.github/workflows/no-private-data.yml) and as a test (tests/test_no_private_data_in_public.py).
It looks at the TRACKED files (``git ls-files``) and checks

  1. no 3-D / CAD / board file at all (STEP, FCStd, GLB, STL, BREP, IGES, KiCad, DXF, ...);
  2. no file under a path that only carries closed-motor data (passports, S1 models, passport specs, Fusion reference
     profiles, BOM fixtures, passport data dumps, study inputs of closed dies);
  3. ``config/dies/`` holds only the OPEN dies listed below (a new die is a deliberate owner decision: add its name here
     in the same pull request, after checking that it is public in the server catalogue);
  4. ``config/motor_catalog.json`` and ``config/motor_presets.json`` carry only open entries.

Usage:  python scripts/check_no_private_data.py [--root DIR]     exit 0 = clean, 1 = violations (listed).
Where the data went and how the app finds it: docs/OPEN_PRIVATE_DATA.md ("3-D models and closed-motor data").
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import PurePosixPath
from typing import Iterable, List

#: 3-D model / CAD / board file types.  None of them may be tracked in the public repository.
FORBIDDEN_EXTENSIONS = frozenset({
    ".step", ".stp", ".fcstd", ".fcstd1", ".glb", ".gltf", ".stl", ".brep", ".brp", ".iges", ".igs", ".obj", ".3mf",
    ".f3d", ".f3z", ".x_t", ".x_b", ".sldprt", ".sldasm", ".ipt", ".iam", ".sat", ".3dm", ".wrl", ".dxf", ".dwg",
    ".kicad_pcb", ".kicad_sch", ".kicad_pro", ".kicad_mod", ".kicad_sym", ".pcbdoc", ".schdoc", ".gbr",
})

#: Paths that carry ONLY closed-motor data / 3-D reference data.  A trailing ``/`` = a directory prefix.
FORBIDDEN_PREFIXES = (
    "cad/reference/",                       # Fusion reference profiles, parameter dumps, STEP exports (code *.py is allowed below)
    "config/passports/",                    # passports of the closed machines
    "config/propeller_s1/",                 # S1 models of the closed machines
    "config/passport_specs/",               # passport specs of the closed machines
    "tests/fixtures/server_bom/",           # BOMs with materials of the closed machines
    "tests/fixtures/cad_bom_dump",          # CAD BOM dumps of the closed machines
    "docs/data/passport_d85/",
    "docs/data/passport_pilot_d40/",
    "docs/data/propeller_s1_bench/",
    "docs/data/propeller_s1_stage_b/",
    "docs/data/propeller_s1_stage_c/",
    "docs/data/shaft_ss420_fem_2026-10-15/",
    "scripts/gap_layers_study/inputs/d40/",
    "scripts/gap_layers_study/inputs/l13/",
    "scripts/gap_layers_study/inputs/l155/",
    "web/public/motor-thumbs/cat_my_motor_40mm.svg",   # outline of a closed 40 mm build
    "config/motor_config.yaml.bak",                    # geometry of the retired 40 mm build
    "config/.last_descent.myverify.bak",
    "data/closed/",
)

#: Files under a forbidden prefix that are CODE or documentation of the public tooling, not data.
ALLOWED_UNDER_FORBIDDEN = frozenset({
    "cad/reference/add_mechanics_rows.py",
    "cad/reference/add_phase_board_rows.py",
    "cad/reference/make_die_reference.py",
})

#: The only dies allowed in ``config/dies/`` (public in the server catalogue, owner decision 2026-10-19).
OPEN_DIES = frozenset({
    "100 mm · 24s-28p mid-torque",
    "CIANO14 30_10",
    "CIANO20 150_35 new",
})

#: Entries allowed in the public catalogue / preset files.
OPEN_CATALOG_IDS = frozenset({"cat_motor_100mm", "cat_ciano14_30_10", "cat_ciano20_150_35_new"})
OPEN_PRESET_IDS = frozenset({"motor_100mm", "ciano14_30_10", "ciano20_150_35_new"})


def tracked_files(root: str) -> List[str]:
    """Tracked files of the git checkout at ``root`` (POSIX paths); the file tree itself when it is not a checkout."""
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "-z"], check=True, capture_output=True).stdout
        return [p for p in out.decode("utf-8").split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        found = []
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if x not in (".git", "node_modules", "__pycache__")]
            for f in files:
                found.append(os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/"))
        return found


def check_paths(paths: Iterable[str]) -> List[str]:
    problems: List[str] = []
    for p in paths:
        ext = PurePosixPath(p).suffix.lower()
        if ext in FORBIDDEN_EXTENSIONS:
            problems.append(f"{p}: 3-D / CAD / board file ({ext}); 3-D models live only in the private repository")
            continue
        if p not in ALLOWED_UNDER_FORBIDDEN and any(p.startswith(x) for x in FORBIDDEN_PREFIXES):
            problems.append(f"{p}: closed-motor / 3-D reference data; it lives only in the private repository")
            continue
        if p.startswith("config/dies/"):
            parts = p.split("/")
            if len(parts) >= 4 and parts[2] not in OPEN_DIES:
                problems.append(f"{p}: die {parts[2]!r} is not in OPEN_DIES (scripts/check_no_private_data.py); closed dies "
                                f"live in the private repository")
    return problems


def check_catalog_files(root: str) -> List[str]:
    problems: List[str] = []
    cat = os.path.join(root, "config", "motor_catalog.json")
    if os.path.isfile(cat):
        try:
            data = json.load(open(cat, encoding="utf-8"))
            for m in data.get("motors", []):
                if m.get("id") not in OPEN_CATALOG_IDS:
                    problems.append(f"config/motor_catalog.json: entry {m.get('id')!r} is not open (move it to the private repository)")
        except (OSError, ValueError) as e:
            problems.append(f"config/motor_catalog.json: unreadable ({e})")
    pre = os.path.join(root, "config", "motor_presets.json")
    if os.path.isfile(pre):
        try:
            data = json.load(open(pre, encoding="utf-8"))
            for k in data:
                if k not in OPEN_PRESET_IDS:
                    problems.append(f"config/motor_presets.json: preset {k!r} is not open (move it to the private repository)")
        except (OSError, ValueError) as e:
            problems.append(f"config/motor_presets.json: unreadable ({e})")
    return problems


def check(root: str) -> List[str]:
    return check_paths(tracked_files(root)) + check_catalog_files(root)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--paths-from-stdin", action="store_true",
                    help="check the paths on stdin (one per line, e.g. `git log --all --name-only --pretty=format:`) instead of the tree: "
                         "used to verify a history purge")
    args = ap.parse_args(argv)
    if args.paths_from_stdin:
        problems = check_paths(sorted({ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()}))
    else:
        problems = check(args.root)
    for p in problems:
        print("PRIVATE DATA IN PUBLIC REPO:", p)
    if problems:
        print(f"\n{len(problems)} violation(s). Move the file(s) to eMotres/motor-ai-sim-private (same relative path); "
              "see docs/OPEN_PRIVATE_DATA.md.")
        return 1
    print("ok: no 3-D model and no closed-motor data is tracked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
