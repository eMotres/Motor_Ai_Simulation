"""``python -m motor_ai_sim.owner_report [--date YYYY-MM-DD] [--dry-run] [--send] [--force]`` - the owner's daily report.

* default / ``--dry-run``   print the plain-text report (``--html`` prints the HTML); nothing is written or sent, no drafts are made.
* ``--send``                the daily run: make the digest DRAFTS, build the report, mail it to the owner ONCE for the date
                            (a date already sent is skipped; ``--force`` sends again).  Used by the host timer at 06:00 Ljubljana.
Without ``--date`` the date is the report that is due now (today from 06:00 local, else yesterday).  Exit 1 when the mail failed.
"""
from __future__ import annotations

import argparse
import sys
import time

from motor_ai_sim.owner_report import run as R


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m motor_ai_sim.owner_report", description=__doc__.split("\n\n")[0])
    ap.add_argument("--date", help="the report of this date (YYYY-MM-DD): the 24 h before 06:00 Europe/Ljubljana")
    ap.add_argument("--dry-run", action="store_true", help="print the report, send and write nothing (the default)")
    ap.add_argument("--send", action="store_true", help="make the digest drafts and send the report to the owner (once per date)")
    ap.add_argument("--force", action="store_true", help="with --send: send even if this date was already sent")
    ap.add_argument("--html", action="store_true", help="print the HTML instead of the text")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if a.send and a.dry_run:
        ap.error("--send and --dry-run exclude each other")
    now = time.time()
    res = R.run_daily(now=now, date=a.date, force=a.force, dry_run=not a.send)
    rep = res.get("report")
    if res["status"] == "dry_run":
        print(rep.html if a.html else rep.text)
        return 0
    line = f"owner report {res['date']}: {res['status']}"
    if rep is not None:
        line += " | " + rep.summary
    if res.get("error"):
        line += f" | {res['error']}"
    print(line)
    return 1 if res["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
