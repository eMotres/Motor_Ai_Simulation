"""The daily run: make the digest drafts, build the report, send it once.

Order at 06:00 (owner 2026-10-27): (1) the digest package's own scheduler function writes the due DRAFTS for every project and
production - it never sends (``digest.build.run``); (2) the report is built, the drafts that wait for approval are in its last
section; (3) it is mailed to the owner and the date is marked as sent.  Nothing here sends to a group's recipients.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Callable, Optional

from motor_ai_sim.owner_report import build as B
from motor_ai_sim.owner_report import send as S
from motor_ai_sim.owner_report import sources as SRC

log = logging.getLogger(__name__)


def make_digest_drafts(now: float, *, write: bool = True) -> tuple[list, str]:
    """``(rows, error)``: the digest package's own run - DRAFTS only, nothing is sent.  A failure is reported, never raised."""
    try:
        from motor_ai_sim.digest import build as DB
        return DB.run(now, write=write), ""
    except Exception as exc:                                 # noqa: BLE001
        log.warning("owner_report: digest draft generation failed (%s: %s)", type(exc).__name__, exc)
        return [], f"digest drafts could not be made: {type(exc).__name__}: {str(exc)[:160]}"


def build_report(date: Optional[str] = None, *, now: Optional[float] = None, make_drafts: bool = False,
                 sources: Optional[B.Sources] = None) -> B.Report:
    from motor_ai_sim import auth_email as E
    now = time.time() if now is None else now
    date = date or B.report_date_for(now)
    errors: list = []
    if make_drafts:
        _rows, err = make_digest_drafts(now)
        if err:
            errors.append(err)
    return B.build(date, sources or SRC.default_sources(), base_url=E.app_url(), extra_errors=errors)


def run_daily(*, now: Optional[float] = None, date: Optional[str] = None, force: bool = False, dry_run: bool = False,
              deliver: Optional[Callable] = None, sources: Optional[B.Sources] = None, make_drafts: bool = True) -> dict:
    """One scheduled run.  Idempotent: a date that was already sent is skipped (``force`` sends again).

    Returns ``{status, date, ...}`` with status ``sent`` | ``already_sent`` | ``busy`` | ``dry_run`` | ``failed``."""
    now = time.time() if now is None else now
    date = date or B.report_date_for(now)
    if dry_run:
        rep = build_report(date, now=now, make_drafts=False, sources=sources)
        return {"status": "dry_run", "date": date, "report": rep}
    with S.RunLock(now) as lock:
        if not lock.held:
            return {"status": "busy", "date": date}
        st = S.get_state()
        if st.get("last_sent_date") == date and not force:
            return {"status": "already_sent", "date": date, "sent_at": st.get("sent_at")}
        rep = build_report(date, now=now, make_drafts=make_drafts, sources=sources)
        res = S.send_report(rep, deliver=deliver)
        if not res["sent"]:
            return {"status": "failed", "date": date, "error": res["error"], "to": res["to"], "report": rep}
        S.set_state(last_sent_date=date, sent_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time())), to=res["to"],
                    counts=rep.counts, problems=len(rep.errors))
        try:
            with open(S.root() / "history.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"date": date, "ts": round(time.time(), 1), "to": res["to"], "counts": rep.counts,
                                     "problems": rep.errors}, ensure_ascii=False) + "\n")
        except OSError:
            pass
        return {"status": "sent", "date": date, "to": res["to"], "report": rep}
