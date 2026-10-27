"""The daily run: make the digests (and send the automatic ones), build the report, send it once.

Order at 06:00 (owner 2026-10-27, changed by the owner decision 2026-10-28): (1) the digest package's scheduler function
(``digest.approval.run_daily``) writes the due DRAFTS for every project and production and SENDS the ones of groups in mode ``auto``
- the default - to the group's own recipients; groups in ``approve`` mode keep their draft waiting; (2) the report is built: the
digests sent automatically today and the drafts that still wait are in its last section; (3) it is mailed to the owner and the date
is marked as sent; (4) when the run made NEW drafts that wait for approval, a second short mail to the owner says "N digests ready
to check" with a deep link per draft (not for the digests that went out by themselves).
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


def make_digest_drafts(now: float, *, write: bool = True, send: bool = True) -> tuple[list, str]:
    """``(rows, error)``: the digest package's daily run - the due drafts, and the automatic send of the groups in mode ``auto``
    (``send=False`` = drafts only).  A failure is reported, never raised."""
    try:
        from motor_ai_sim.digest import approval as AP
        return AP.run_daily(now, write=write, send=send), ""
    except Exception as exc:                                 # noqa: BLE001
        log.warning("owner_report: digest run failed (%s: %s)", type(exc).__name__, exc)
        return [], f"digests could not be made: {type(exc).__name__}: {str(exc)[:160]}"


def build_report(date: Optional[str] = None, *, now: Optional[float] = None, make_drafts: bool = False,
                 sources: Optional[B.Sources] = None, extra_errors: Optional[list] = None) -> B.Report:
    from motor_ai_sim import auth_email as E
    now = time.time() if now is None else now
    date = date or B.report_date_for(now)
    errors: list = list(extra_errors or [])
    if make_drafts:
        _rows, err = make_digest_drafts(now)
        if err:
            errors.append(err)
    return B.build(date, sources or SRC.default_sources(), base_url=E.app_url(), extra_errors=errors)


# -- the "digests ready to check" notice ------------------------------------------------------------------------------------------------

NOTICE_KEEP = 300                                            # how many already-announced draft ids the state remembers


def new_draft_ids(rows: list, *, sent_by_run: bool = True) -> list:
    """The drafts this run CREATED (a fresh draft, or a new revision that replaced an opened one) and left WAITING for the owner - not
    the ones refreshed in place, and not the ones the run handled by itself (``auto``: sent, or failed - the report names those).  ``sent_by_run=False`` (a run that
    sends nothing, the admin "send now" route) also leaves out the drafts of groups in ``auto`` mode: the next scheduled run sends them."""
    from motor_ai_sim.digest import store as DS
    return [str(r["draft_id"]) for r in rows if r.get("action") in ("drafted", "superseded") and r.get("draft_id") and not r.get("auto")
            and (sent_by_run or DS.get_settings(str(r.get("scope")))["mode"] != "auto")]


def notice_rows(ids: list) -> list:
    """``{id, kind, title, items, recipients}`` of the listed drafts that still wait for the owner (a draft he already sent or
    discarded is not announced)."""
    from motor_ai_sim.digest import store as DS
    out = []
    for did in ids:
        d = DS.load_draft(did)
        if isinstance(d, dict) and d.get("status") in DS.OPEN_STATUSES:
            out.append({"id": did, "kind": d.get("kind"), "title": d.get("title"),
                        "items": len(d.get("items") or []) + int(d.get("more") or 0), "recipients": len(d.get("recipients") or [])})
    return out


def queue_notice(ids: list) -> None:
    """Remember the new drafts until the notice is out (a failed mail is retried by the next run, never duplicated)."""
    if not ids:
        return
    st = S.get_state()
    done = set(st.get("notified") or [])
    pending = list(st.get("notice_pending") or [])
    pending += [i for i in ids if i not in done and i not in pending]
    S.set_state(notice_pending=pending)


def send_pending_notice(*, deliver: Optional[Callable] = None) -> dict:
    """Mail the owner ONE short notice for every queued new draft; ``{status: none|sent|failed, count, ...}``.  Never raises."""
    try:
        from motor_ai_sim import auth_email as E
        st = S.get_state()
        pending = [str(i) for i in st.get("notice_pending") or []]
        if not pending:
            return {"status": "none", "count": 0}
        rows = notice_rows(pending)
        notice = B.build_notice(rows, E.app_url())
        if notice is None:                                   # all of them were handled in the meantime
            S.set_state(notice_pending=[])
            return {"status": "none", "count": 0}
        res = S.send_notice(notice, deliver=deliver)
        if not res["sent"]:
            return {"status": "failed", "count": notice.count, "error": res["error"], "to": res["to"]}
        done = (list(st.get("notified") or []) + pending)[-NOTICE_KEEP:]
        S.set_state(notice_pending=[], notified=done)
        return {"status": "sent", "count": notice.count, "to": res["to"], "subject": notice.subject}
    except Exception as exc:                                 # noqa: BLE001 - a notice problem never stops the report
        log.warning("owner_report: digest notice failed (%s: %s)", type(exc).__name__, str(exc)[:160])
        return {"status": "failed", "count": 0, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}


def run_daily(*, now: Optional[float] = None, date: Optional[str] = None, force: bool = False, dry_run: bool = False,
              deliver: Optional[Callable] = None, sources: Optional[B.Sources] = None, make_drafts: bool = True,
              send_digests: bool = True) -> dict:
    """One scheduled run.  Idempotent: a date that was already sent is skipped (``force`` sends again).

    ``send_digests=False`` makes the drafts but sends none (the admin "send the owner report now" route: the automatic digest send
    belongs to the scheduled run alone).

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
        rows, err = (make_digest_drafts(now, send=send_digests) if make_drafts else ([], ""))
        queue_notice(new_draft_ids(rows, sent_by_run=send_digests))
        rep = build_report(date, now=now, make_drafts=False, sources=sources, extra_errors=[err] if err else None)
        res = S.send_report(rep, deliver=deliver)
        if not res["sent"]:
            return {"status": "failed", "date": date, "error": res["error"], "to": res["to"], "report": rep}
        notice = send_pending_notice(deliver=deliver)
        S.set_state(last_sent_date=date, sent_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time())), to=res["to"],
                    counts=rep.counts, problems=len(rep.errors))
        try:
            with open(S.root() / "history.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"date": date, "ts": round(time.time(), 1), "to": res["to"], "counts": rep.counts,
                                     "problems": rep.errors, "notice": notice.get("status")}, ensure_ascii=False) + "\n")
        except OSError:
            pass
        return {"status": "sent", "date": date, "to": res["to"], "report": rep, "notice": notice}
