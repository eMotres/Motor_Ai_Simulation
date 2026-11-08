#!/usr/bin/env python
"""Report the proposed home region of every account, with the reason (docs/regions/DESIGN.md, "Home region").

    python scripts/classify_regions.py              # DRY RUN (default): prints the report, writes nothing
    python scripts/classify_regions.py --json       # the same as JSON
    python scripts/classify_regions.py --apply      # stores the changed proposals (never over an admin's choice)

Run it inside the API container on the server (the identity store is /srv/motres/identity there).  ``--apply`` moves NO
data: it only sets ``home_region`` on the accounts.  The migration of a US account's data is stage 3.
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="store the changed proposals (default: dry run)")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    a = ap.parse_args(argv)
    from motor_ai_sim.regions import classifier as CL
    rep = CL.classify_all(apply=a.apply, actor="classify_regions.py")
    if a.json:
        print(json.dumps(rep, indent=1, sort_keys=True))
        return 0
    print("%s: %d accounts, proposed eu %d / us %d, %d change(s)%s" % (
        "DRY RUN" if rep["dry_run"] else "APPLIED", rep["accounts"], rep["proposed"]["eu"], rep["proposed"]["us"],
        rep["changes"], "" if rep["dry_run"] else ", %d stored" % rep["applied"]))
    print("by reason: " + ", ".join("%s %d" % kv for kv in sorted(rep["by_reason"].items())))
    for r in rep["rows"]:
        print("%-3s %-40s %s -> %s  %s%s" % ("*" if r["change"] else "", r["email"], r["current"], r["proposed"],
                                            r["reason"], (" " + r["country"]) if r["country"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
