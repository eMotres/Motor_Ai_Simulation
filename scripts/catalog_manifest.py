"""sha256 manifest of a catalog tree, with byte-level names.

WHY
===
The catalog moves to Linux as bytes, and two things can silently change on the
way (migration plan §3.2):

* **Unicode normalisation.**  Real die folders are called
  ``100 mm · 24s-28p mid-torque`` — U+00B7 MIDDLE DOT, and ``°`` elsewhere.
  Copied with ``tar`` or ``rsync`` those names survive verbatim.  Round-tripped
  through a tool that NFD-normalises (macOS ``zip``, some SMB paths) the folder
  arrives spelled with a *decomposed* sequence: a different byte string, a
  different directory, and a catalog that has quietly lost a die while every
  listing still looks right.
* **Content.**  A text-mode transfer that rewrites CRLF inside a ``.npz``.

A manifest catches both, and catches them *at the transfer*, which is the only
moment they are cheap.  ``migrate_to_workspaces.py`` already carries this
function (``manifest()``, used by ``--manifest``); this script is the same
computation available on its own, so a manifest can be taken on the Windows box
*before* anything is installed on the server, and compared afterwards.

NAMES ARE RECORDED RAW.  No ``unicodedata.normalize`` anywhere: normalising
here would make exactly the failure mode this file exists to detect invisible.
A name is additionally reported with its NFC/NFD escape when the two differ, so
a mismatch reads as "this is an NFD copy" instead of as two identical lines.

USAGE
    python scripts/catalog_manifest.py config/dies -o before.json
    # ...transfer...
    python scripts/catalog_manifest.py /srv/motres/shared/dies -o after.json
    python scripts/catalog_manifest.py --compare before.json after.json

Exit code 0 = identical, 1 = a difference, 2 = a usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from pathlib import Path
from typing import Dict

#: Reproducible by construction, and megabytes of it.  A manifest that included
#: caches would differ on every comparison and teach the reader to ignore it.
SKIP_DIRS = {".mesh_cache", ".static3d_cache", ".run_ledger", "__pycache__",
             ".git"}
SKIP_NAMES = {".warm_cache.npz", ".daxis_cache.json", ".scan_cache.jsonl",
              ".DS_Store", "Thumbs.db"}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(root: Path) -> Dict[str, str]:
    """``{relative posix path: sha256}`` for every file under ``root``.

    Identical to ``migrate_to_workspaces.manifest`` plus the cache skips, so a
    manifest taken by either tool compares to one taken by the other.
    """
    root = Path(root)
    out: Dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        parts = set(p.relative_to(root).parts)
        if parts & SKIP_DIRS or p.name in SKIP_NAMES:
            continue
        out[p.relative_to(root).as_posix()] = sha256(p)
    return out


def _norm_note(name: str) -> str:
    """``"NFC"`` / ``"NFD"`` / ``""`` — only when it is load-bearing.

    A pure-ASCII name is never annotated: the note exists to explain a
    mismatch, not to decorate 200 lines that cannot have one.
    """
    if name.isascii():
        return ""
    nfc = unicodedata.normalize("NFC", name)
    nfd = unicodedata.normalize("NFD", name)
    if name == nfc and nfc != nfd:
        return "NFC"
    if name == nfd and nfc != nfd:
        return "NFD"
    return ""


def build(root: Path) -> dict:
    files = manifest(root)
    non_ascii = {k: _norm_note(k) for k in files if not k.isascii()}
    return {
        "root": str(root),
        "files": files,
        "count": len(files),
        # The whole manifest reduced to one line, so two trees can be compared
        # by eye across an ssh session.
        "digest": hashlib.sha256(
            "\n".join(f"{k}\t{v}" for k, v in sorted(files.items()))
            .encode("utf-8")).hexdigest(),
        "non_ascii_names": non_ascii,
    }


def compare(a: dict, b: dict) -> int:
    fa, fb = a.get("files", {}), b.get("files", {})
    only_a = sorted(set(fa) - set(fb))
    only_b = sorted(set(fb) - set(fa))
    changed = sorted(k for k in set(fa) & set(fb) if fa[k] != fb[k])

    # An NFC/NFD pair shows up as one "missing" and one "extra" whose names are
    # equal after normalising.  Naming that explicitly is the entire point.
    folded_a = {unicodedata.normalize("NFC", k): k for k in only_a}
    folded_b = {unicodedata.normalize("NFC", k): k for k in only_b}
    renormalised = sorted(set(folded_a) & set(folded_b))

    for k in renormalised:
        print(f"NORMALISED  {folded_a[k]!r}\n         -> {folded_b[k]!r}")
    for k in only_a:
        if unicodedata.normalize("NFC", k) not in renormalised:
            print(f"MISSING     {k}")
    for k in only_b:
        if unicodedata.normalize("NFC", k) not in renormalised:
            print(f"EXTRA       {k}")
    for k in changed:
        print(f"CHANGED     {k}")

    bad = len(only_a) + len(only_b) + len(changed)
    if not bad:
        print(f"identical: {len(fa)} files, digest {a.get('digest','')[:16]}")
        return 0
    print(f"\n{bad} difference(s): {len(only_a)} missing, {len(only_b)} extra, "
          f"{len(changed)} changed, {len(renormalised)} unicode-renormalised")
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", type=Path,
                    help="the tree to hash (e.g. config/dies)")
    ap.add_argument("-o", "--out", type=Path,
                    help="write the manifest here (default: stdout)")
    ap.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"),
                    help="compare two manifests instead of building one")
    args = ap.parse_args(argv)

    if args.compare:
        a, b = (json.loads(p.read_text(encoding="utf-8")) for p in args.compare)
        return compare(a, b)

    if args.root is None:
        ap.error("give a directory to hash, or --compare A B")
    if not args.root.is_dir():
        print(f"not a directory: {args.root}", file=sys.stderr)
        return 2

    man = build(args.root)
    text = json.dumps(man, indent=2, ensure_ascii=False, sort_keys=True)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"{man['count']} files, digest {man['digest'][:16]} -> {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
