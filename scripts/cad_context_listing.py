#!/usr/bin/env python3
"""Dry run of the CAD image build context: which files would `deploy/Dockerfile.cad` copy into the image?

No Docker needed.  It applies `deploy/Dockerfile.cad.dockerignore` to the tracked files of this checkout (the MAIN context) and
lists, for every `COPY` line of the Dockerfile, the files that come from the MAIN context and from the named `private` context
(`docker build --build-context private=DIR`; DIR = a checkout of eMotres/motor-ai-sim-private).

    python scripts/cad_context_listing.py --private C:/Users/me/Projects/motor-ai-sim-private [--all]

Exit 1 when the main context would carry a 3-D model file (it must come only from `private`) or when `private` lacks
`cad/reference/CIANO14_40_12.json` (the image build would fail).
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "deploy" / "Dockerfile.cad"
IGNORE = ROOT / "deploy" / "Dockerfile.cad.dockerignore"
THREE_D = (".step", ".stp", ".fcstd", ".glb", ".stl", ".brep", ".iges", ".igs")


def _tracked() -> list:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], check=True, capture_output=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def _to_re(pat: str):
    """Docker/.dockerignore glob -> regex (``**`` crosses directories, ``*`` and ``?`` do not)."""
    i, out = 0, ""
    while i < len(pat):
        c = pat[i]
        if pat.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pat.startswith("**", i):
            out += ".*"
            i += 2
        elif c == "*":
            out += "[^/]*"
            i += 1
        elif c == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(c)
            i += 1
    return re.compile("^" + out + "$")


def _read_ignore():
    rules = []
    for line in IGNORE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        neg = line.startswith("!")
        rules.append((neg, _to_re(line[1:] if neg else line)))
    return rules


def _excluded(path: str, rules) -> bool:
    """Last matching rule wins; a rule that matches a parent directory matches everything below it."""
    parts = PurePosixPath(path).parts
    prefixes = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    ex = False
    for neg, rx in rules:
        if any(rx.match(p) for p in prefixes):
            ex = not neg
    return ex


def _copy_lines():
    """[(from_context or None, [sources], dest)] of the COPY lines (line continuations joined)."""
    text = DOCKERFILE.read_text(encoding="utf-8").replace("\\\r\n", " ").replace("\\\n", " ")
    out = []
    for line in text.splitlines():
        if not line.startswith("COPY "):
            continue
        args = shlex.split(line[5:])
        frm = None
        while args and args[0].startswith("--"):
            flag = args.pop(0)
            if flag.startswith("--from="):
                frm = flag.split("=", 1)[1]
        out.append((frm, args[:-1], args[-1]))
    return out


def _match_src(files, src: str):
    src = src.rstrip("/")
    return [f for f in files if f == src or f.startswith(src + "/") or fnmatch.fnmatch(f, src)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--private", help="the `private` build context (a checkout of motor-ai-sim-private)")
    ap.add_argument("--all", action="store_true", help="list every file, not only the counts")
    args = ap.parse_args(argv)

    rules = _read_ignore()
    main_ctx = [f for f in _tracked() if not _excluded(f, rules)]
    priv_ctx = []
    if args.private:
        base = Path(args.private)
        for d, dirs, files in os.walk(base):
            dirs[:] = [x for x in dirs if x != ".git"]
            for f in files:
                priv_ctx.append(os.path.relpath(os.path.join(d, f), base).replace(os.sep, "/"))
    bad = []
    total = 0
    for frm, srcs, dest in _copy_lines():
        pool, label = (priv_ctx, "private") if frm == "private" else (main_ctx, "main")
        hit = sorted({f for s in srcs for f in _match_src(pool, s)})
        total += len(hit)
        print(f"COPY {('--from=' + frm + ' ') if frm else ''}{' '.join(srcs)} -> {dest}   [{label} context: {len(hit)} files]")
        if args.all:
            for f in hit:
                print("    " + f)
        if frm != "private":
            bad += [f for f in hit if f.lower().endswith(THREE_D)]
        elif not hit:
            bad.append("(private context has nothing for: " + " ".join(srcs) + ")")
    need = "cad/reference/CIANO14_40_12.json"
    if args.private and need not in priv_ctx:
        bad.append(f"private context lacks {need}: the image build would fail")
    if not args.private:
        print("(no --private given: the build would fail at `COPY --from=private`)")
    steps = [f for f in priv_ctx if f.lower().endswith(THREE_D) and f.startswith("cad/reference/")]
    print(f"\n{total} files in the image from the COPY lines; 3-D files from `private`: {len(steps)}")
    for b in bad:
        print("PROBLEM:", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
