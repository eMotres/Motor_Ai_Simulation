"""``python -m motor_ai_sim.digest run [--dry-run] [--drafts-only]`` - the daily digest run.

Owner decision 2026-10-28 (it reverses the 2026-10-26 "never sends" rule): the run makes the due DRAFTS and then SENDS the ones of
every group in mode ``auto`` (the default), through the same path as the admin Send; groups in ``approve`` mode keep their draft
waiting in Admin -> Digests, groups in ``off`` mode get none, a group with no changes gets none.  ``--dry-run`` writes and sends
nothing; ``--drafts-only`` writes the drafts and sends nothing.  The server's owner-report timer (06:00 Europe/Ljubljana) does the
same run as its first step, so this CLI is needed only for a manual run.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from motor_ai_sim.digest import approval as AP
from motor_ai_sim.digest import store as DS


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m motor_ai_sim.digest", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="make the due drafts and send those of groups in auto mode")
    run.add_argument("--dry-run", action="store_true", help="show what would be drafted, write nothing")
    run.add_argument("--drafts-only", action="store_true", help="make the drafts, send nothing (even for groups in auto mode)")
    run.add_argument("--scope", action="append", help="only this group (project:<id> or production:<die>/<config>); repeatable")
    run.add_argument("--json", action="store_true", help="machine-readable output")
    sub.add_parser("list", help="the drafts that wait for approval")
    argv = list(sys.argv[1:] if argv is None else argv)
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for d in DS.list_drafts():
            if d.get("status") == "pending":
                print(f"{d['id']}  {d['scope']}  {d['period']['start']} .. {d['period']['end']}  {len(d['items']) + d['more']} items  "
                      f"{len(d['recipients'])} recipients")
        return 0
    rows = AP.run_daily(time.time(), scopes=a.scope, write=not a.dry_run, send=not a.drafts_only)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        for r in rows:
            tail = f"{r.get('items', 0)} items, {r.get('recipients', 0)} recipients" if r["action"] != "skipped" else r.get("reason", "")
            auto = r.get("auto")
            if auto:
                tail += f"  -> auto: {auto['status']}" + (f" ({auto['code']})" if auto.get("code") else "")
            print(f"{r['action']:9} {r['scope']}  {tail}")
        sent = sum(1 for r in rows if (r.get("auto") or {}).get("status") == "sent")
        print("DRY RUN - nothing written or sent." if a.dry_run else f"Sent automatically: {sent}. Other drafts wait in Admin -> Digests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
