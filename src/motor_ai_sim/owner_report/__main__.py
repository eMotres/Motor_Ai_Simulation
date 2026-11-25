"""``python -m motor_ai_sim.owner_report [--date YYYY-MM-DD] [--dry-run] [--send] [--force]`` - the owner's daily report.

* default / ``--dry-run``   print the plain-text report (``--html`` prints the HTML); nothing is written or sent, no drafts are made.
* ``--send``                the daily run: make the digests (groups in auto mode are sent), build the report, mail it to the owner ONCE for the date
                            (a date already sent is skipped; ``--force`` sends again).  Used by the host timer at 06:00 Ljubljana.
Without ``--date`` the date is the report that is due now (today from 06:00 local, else yesterday).  Exit 1 when the mail failed.
"""
from __future__ import annotations

import argparse
import calendar
import functools
import sys
import time
from typing import Optional

from motor_ai_sim.owner_report import run as R


def format_queue_line(counts: dict, now: float) -> str:
    """``Decisions: 4 open, 9 messages, oldest 2 d 5 h`` from ``owner_queue.counts()`` (pure)."""
    line = f"Decisions: {int(counts.get('open_blocking', 0))} open, {int(counts.get('open_messages', 0))} messages"
    oldest = counts.get("oldest_open_at")
    if oldest:
        age = max(0, int(now - calendar.timegm(time.strptime(oldest, "%Y-%m-%dT%H:%M:%SZ"))))
        d, h = age // 86400, age % 86400 // 3600
        line += ", oldest " + (f"{d} d {h} h" if d else f"{h} h")
    return line


def queue_line(now: float) -> str:
    """The owner-queue line of the report; a failure is ``Decisions: unavailable`` and never breaks the report."""
    try:
        from motor_ai_sim import owner_queue as OQ
        return format_queue_line(OQ.counts(), now)
    except Exception:                                              # noqa: BLE001
        return "Decisions: unavailable"


def _with_queue_line(line: str):
    """run.build_report calls ``build.build`` without the line; wrap it for THIS process only (run.py is not ours to edit)."""
    orig = R.B.build

    @functools.wraps(orig)
    def build(*a, **kw):
        kw.setdefault("queue_line", line)
        return orig(*a, **kw)
    R.B.build = build
    return orig


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m motor_ai_sim.owner_report", description=__doc__.split("\n\n")[0])
    ap.add_argument("--date", help="the report of this date (YYYY-MM-DD): the 24 h before 06:00 Europe/Ljubljana")
    ap.add_argument("--dry-run", action="store_true", help="print the report, send and write nothing (the default)")
    ap.add_argument("--send", action="store_true", help="run the digests (auto groups are sent), then send the report to the owner (once per date)")
    ap.add_argument("--force", action="store_true", help="with --send: send even if this date was already sent")
    ap.add_argument("--html", action="store_true", help="print the HTML instead of the text")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if a.send and a.dry_run:
        ap.error("--send and --dry-run exclude each other")
    now = time.time()
    orig = _with_queue_line(queue_line(now))
    try:
        res = R.run_daily(now=now, date=a.date, force=a.force, dry_run=not a.send)
    finally:
        R.B.build = orig
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
