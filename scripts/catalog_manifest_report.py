#!/usr/bin/env python
"""Dry-run report of the catalog manifest that the US mirror would receive (docs/regions/DESIGN.md, sections 3 and 7).

    python scripts/catalog_manifest_report.py                 # reads the server's shared tree
    python scripts/catalog_manifest_report.py --root DIR      # another shared tree
    python scripts/catalog_manifest_report.py --json          # the full manifest summary as JSON

Prints the file count and bytes per top-level directory, the dies that are left out with the reason (open-only rule),
the files left out inside mirrored dies, and the files that could not be read.  Writes nothing.  Run it inside the API
container on the EU server (the shared tree is /srv/motres/shared there).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def summarize(manifest: dict) -> dict:
    per: dict = defaultdict(lambda: {"files": 0, "bytes": 0})
    for rel, v in (manifest.get("files") or {}).items():
        top = rel.split("/", 1)[0] if "/" in rel else "(root files)"
        per[top]["files"] += 1
        per[top]["bytes"] += int(v.get("bytes") or 0)
    return {"root_id": manifest.get("root_id"), "count": manifest.get("count"), "bytes": manifest.get("bytes"),
            "per_top_dir": dict(sorted(per.items())),
            "excluded_dies": manifest.get("excluded_dies") or [],
            "excluded_files": manifest.get("excluded_files") or [],
            "skipped_unreadable": manifest.get("skipped_unreadable", 0),
            "skipped_unreadable_paths": manifest.get("skipped_unreadable_paths") or []}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", help="shared tree to scan (default: the server's shared root)")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    a = ap.parse_args(argv)
    from motor_ai_sim.regions import catalog_manifest as CM
    rep = summarize(CM.build(Path(a.root) if a.root else None))
    if a.json:
        print(json.dumps(rep, indent=1, sort_keys=True))
        return 0
    print("manifest %s: %d files, %d bytes" % ((rep["root_id"] or "")[:12], rep["count"], rep["bytes"]))
    for top, v in rep["per_top_dir"].items():
        print("  %-18s %6d files %14d bytes" % (top, v["files"], v["bytes"]))
    print("excluded dies: %d" % len(rep["excluded_dies"]))
    for e in rep["excluded_dies"]:
        print("  %-40s %s" % (e["die"], e["why"]))
    print("excluded files inside mirrored dies / keyed dirs: %d (first %d shown)" % (
        len(rep["excluded_files"]), len(rep["excluded_files"])))
    for e in rep["excluded_files"]:
        print("  %-60s %s" % (e["path"], e["why"]))
    print("skipped (unreadable): %d" % rep["skipped_unreadable"])
    for p in rep["skipped_unreadable_paths"]:
        print("  " + p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
