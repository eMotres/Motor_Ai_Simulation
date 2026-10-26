"""``python -m motor_ai_sim.digest run [--dry-run]`` - make the due digest DRAFTS.

The server's systemd timer calls this once a day.  It never sends anything (owner 2026-10-26: no outside message without the
owner's OK on the final text): the drafts wait in Admin -> Digests, where the owner previews and sends or discards them.
There is deliberately no ``--send`` option.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from motor_ai_sim.digest import build as B
from motor_ai_sim.digest import store as DS


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m motor_ai_sim.digest", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="make the due drafts (nothing is sent)")
    run.add_argument("--dry-run", action="store_true", help="show what would be drafted, write nothing")
    run.add_argument("--scope", action="append", help="only this group (project:<id> or production:<die>/<config>); repeatable")
    run.add_argument("--json", action="store_true", help="machine-readable output")
    sub.add_parser("list", help="the drafts that wait for approval")
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(a == "--send" or a.startswith("--send=") for a in argv):
        print("There is no --send: digests are sent only from Admin -> Digests after the owner's approval.", file=sys.stderr)
        return 2
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for d in DS.list_drafts():
            if d.get("status") == "pending":
                print(f"{d['id']}  {d['scope']}  {d['period']['start']} .. {d['period']['end']}  {len(d['items']) + d['more']} items  "
                      f"{len(d['recipients'])} recipients")
        return 0
    rows = B.run(time.time(), scopes=a.scope, write=not a.dry_run)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        for r in rows:
            tail = f"{r.get('items', 0)} items, {r.get('recipients', 0)} recipients" if r["action"] != "skipped" else r.get("reason", "")
            print(f"{r['action']:9} {r['scope']}  {tail}")
        print(("DRY RUN - nothing written. " if a.dry_run else "") + "Nothing was sent; drafts wait in Admin -> Digests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
