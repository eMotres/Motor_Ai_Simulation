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
  4. ``config/motor_catalog.json`` and ``config/motor_presets.json`` carry only open entries;
  5. (re-audit R4, continued) no tracked text file (docs/*.md, coordination/, web/src, scripts, configs ...) names a
     token on the PRIVATE_LITERAL_DIGESTS list (closed card id, candidate pin, source-record hash, also truncated to 16 hex);
  6. (re-audit R4, 2026-10-20) no Python LITERAL of closed-motor data in ``src/`` (``tests/`` for the digest list): a 64-hex
     (sha256) string, a closed card id (``candidate_<die>_...``), a basis-shaped dict (``sourceSha256`` + ``sourceRecord``, or
     ``windingC`` + ``magnetC`` + ``stackLengthMm``), or any literal on the PRIVATE_LITERAL_DIGESTS list.  A legitimate public
     literal is allowed with ``# private-data-ok: <reason>`` on its line or an entry in ALLOWED_LITERALS.

Usage:  python scripts/check_no_private_data.py [--root DIR]     exit 0 = clean, 1 = violations (listed).
Where the data went and how the app finds it: docs/OPEN_PRIVATE_DATA.md ("3-D models and closed-motor data").
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
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
    # review F4 (2026-10-20): only the thumbnail of an OPEN catalogue card may sit in the public web folder;
    # the server serves every other card's thumb_svg through the API (visibility-checked).
    "web/public/motor-thumbs/cat_150_baseline.svg",
    "web/public/motor-thumbs/cat_150_opt.svg",
    "web/public/motor-thumbs/cat_my_baseline.svg",
    "web/public/motor-thumbs/cat_motor_40mm.svg",
    "web/public/last_loadline.json",                   # load-line sweep of an old closed build
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


#: SHA-256 digests of lower-cased string literals that must never appear in public Python (src/ or tests/).  Digests,
#: not the values: the guard itself is public.  Add the digest of a closed card id / record hash here when one is found.
PRIVATE_LITERAL_DIGESTS = frozenset({
    "4d79db9394d5c6d51b6ba800d0523a2936cc9477b662a15bde9f4d8bc38d6acd",   # closed reference card id
    "be7d7e9afada0c082b2ae453786b94688b0e8f51b5642d3707af6b0ae0e49ab8",   # its candidate pin
    "494a5e9ec197081a1fe83bb687a583e812e7ae5e05f6ec24a283a419d3110ad5",   # its source-record hash
    "7ed32ad2136389ff8cce7cc9ddafbbf472c23752223d1f6b24311bdd74cc003d",   # first 16 hex characters of the pin
    "b7a7db0e459b724ed9e35c9dc4462082763acec5979ebab66f3009cf3faa1ee0",   # first 16 hex characters of the record hash
})

#: Python literals that are allowed although they look like closed data: ``(posix path, literal)``.
ALLOWED_LITERALS: frozenset = frozenset()

#: Where Python literals are scanned: all rules under src/, only the digest list under tests/.
SRC_PREFIX = "src/"
TEST_PREFIX = "tests/"
ALLOW_MARK = "private-data-ok"

_HEX64 = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")
_CARD_ID = re.compile(r"^candidate_[a-z]+\d+_[a-z0-9_]+$")
#: Key sets of a dict literal that is an audited-basis record.
_BASIS_KEYS = (frozenset({"sourceSha256", "sourceRecord"}), frozenset({"windingC", "magnetC", "stackLengthMm"}))


#: Text files scanned token by token against the digest list (docs, notes, web sources, scripts, configs).
TEXT_SUFFIXES = frozenset({".md", ".txt", ".rst", ".mjs", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
                           ".py", ".html", ".csv", ".toml", ".sh", ".ps1"})
MAX_TEXT_BYTES = 8 * 1024 * 1024
_TOKEN = re.compile(r"[a-z0-9_]+")


def check_text_tokens(path: str, text: str) -> List[str]:
    """Closed-motor identifiers in a text file: every token (and the first 16 characters of a long hex token) is
    hashed and looked up in PRIVATE_LITERAL_DIGESTS.  The digests live in this guard; the values never do."""
    if PurePosixPath(path).suffix.lower() not in TEXT_SUFFIXES:
        return []
    found: List[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARK in line or (path, line.strip()) in ALLOWED_LITERALS:
            continue
        for tok in _TOKEN.findall(line.lower()):
            if len(tok) < 12:
                continue
            hit = hashlib.sha256(tok.encode("utf-8")).hexdigest() in PRIVATE_LITERAL_DIGESTS
            if not hit and len(tok) >= 16 and re.fullmatch(r"[0-9a-f]+", tok):
                hit = hashlib.sha256(tok[:16].encode("utf-8")).hexdigest() in PRIVATE_LITERAL_DIGESTS
            if hit:
                found.append(f"{path}:{lineno}: names a closed reference card / pin / record hash (PRIVATE_LITERAL_DIGESTS); "
                             f"such notes live in the private repository (docs/d85_reference_basis_moved/ style)")
                break
    return found


def check_texts(root: str, paths: Iterable[str]) -> List[str]:
    problems: List[str] = []
    for p in paths:
        if PurePosixPath(p).suffix.lower() not in TEXT_SUFFIXES:
            continue
        full = os.path.join(root, p)
        try:
            if os.path.getsize(full) > MAX_TEXT_BYTES:
                continue
            with open(full, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        problems += check_text_tokens(p, text)
    return problems


def check_python_literals(path: str, text: str) -> List[str]:
    """Closed-motor literals in one Python file (``path`` is the repo-relative POSIX path)."""
    in_src = path.startswith(SRC_PREFIX)
    if not (in_src or path.startswith(TEST_PREFIX)) or not path.endswith(".py"):
        return []
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    lines = text.splitlines()

    def allowed(lineno: int, literal: str = "") -> bool:
        line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
        return ALLOW_MARK in line or (path, literal) in ALLOWED_LITERALS

    problems: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            why = None
            if hashlib.sha256(val.strip().lower().encode("utf-8")).hexdigest() in PRIVATE_LITERAL_DIGESTS:
                why = "is a closed-motor card id / hash (PRIVATE_LITERAL_DIGESTS)"
            elif in_src and _HEX64.search(val):
                why = "holds a 64-hex (sha256) literal"
            elif in_src and _CARD_ID.match(val):
                why = "looks like a closed reference card id (candidate_<die>_...)"
            if why and not allowed(node.lineno, val):
                shown = val if len(val) <= 14 else val[:12] + ".."
                problems.append(f"{path}:{node.lineno}: string literal {shown!r} {why}; closed-motor data lives in the private "
                                f"repository / shared data store (mark a public one with '# {ALLOW_MARK}: <reason>')")
        elif in_src and isinstance(node, ast.Dict):
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if any(ks <= keys for ks in _BASIS_KEYS) and not allowed(node.lineno):
                problems.append(f"{path}:{node.lineno}: dict literal shaped like an audited reference basis; it belongs in "
                                f"<shared>/references/basis (private repo config/references/basis), not in public source")
    return problems


def check_literals(root: str, paths: Iterable[str]) -> List[str]:
    problems: List[str] = []
    for p in paths:
        if not p.endswith(".py") or not (p.startswith(SRC_PREFIX) or p.startswith(TEST_PREFIX)):
            continue
        try:
            with open(os.path.join(root, p), encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        problems += check_python_literals(p, text)
    return problems


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
    files = tracked_files(root)
    return check_paths(files) + check_catalog_files(root) + check_literals(root, files) + check_texts(root, files)


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
    print("ok: no 3-D model, no closed-motor data file and no closed-motor Python literal is tracked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
