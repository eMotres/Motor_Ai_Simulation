"""The ONLY place where a digest can leave the house: :func:`send_draft` (an admin's approval) and :func:`run_daily` (the
scheduled automatic send).

Owner decision 2026-10-28 (it reverses the 2026-10-26 "no automatic mode" rule): the activity digests of ALL projects and
productions go out by themselves once a day, at 06:00 Europe/Ljubljana, and only for a group where something changed during the
period.  So there are two callers, both through the same private :func:`_send` (every re-check below, the send log, the
max-age and unsafe-subject guards, partial retry):

* the admin route (``POST /api/admin/digests/{id}/send``) -> :func:`send_draft`, the approving admin's address is the approver;
* the daily scheduled run (owner report step 1, ``python -m motor_ai_sim.digest run``) -> :func:`run_daily`, which builds the drafts
  (:func:`motor_ai_sim.digest.build.run`, draft-only) and then sends the draft of every group in mode ``auto`` through
  :func:`auto_send_draft`; the approver is the system actor :data:`AUTO_ACTOR`.  Groups in ``approve`` mode keep waiting in Admin,
  groups in ``off`` mode are not drafted.

Nothing else sends: page loads, the admin "make drafts now" route and ``build.run`` itself never reach SMTP.  ``tests/test_digest.py``
pins this.

At send time the recipients are re-checked against TODAY's state: a person who left the group, unsubscribed, was disabled or lost
the access the text needs is skipped (they are named in the result), and nobody who was not on the draft's list is added.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Callable, Optional

from motor_ai_sim import auth_email as E
from motor_ai_sim import mailing
from motor_ai_sim.digest import build as B
from motor_ai_sim.digest import collect as C
from motor_ai_sim.digest import store as DS

#: a draft that nobody looked at for this long is not sent as it is (the scheduler refreshes a pending draft every run)
MAX_AGE_S = 14 * 86400.0
#: the approver recorded on a digest the scheduled run sent (not an address: an admin's approval can never be mistaken for it)
AUTO_ACTOR = "auto (daily 06:00)"
#: pause between two messages of one send (seconds); the admin route has its own setting
AUTO_DELAY_S = float(os.environ.get("DIGEST_SEND_DELAY_S", "1.0") or 0)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s.]+$")
log = logging.getLogger(__name__)


class ApprovalError(Exception):
    """``code``: approval_required | content_changed | not_found | not_pending | stale | group_gone | mode_off | no_recipients |
    access_changed | unsafe_subject | send_in_progress | not_auto"""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _pending(draft_id: str) -> dict:
    """A draft that can still be sent: waiting (``pending``) or partly sent (``partial``, only the failed ones are retried)."""
    draft = DS.load_draft(draft_id)
    if draft is None:
        raise ApprovalError("not_found", "No such digest.")
    if draft.get("status") not in DS.OPEN_STATUSES:
        if draft.get("status") == "superseded":
            raise ApprovalError("not_pending", "A newer draft replaced this one - reload the list and review the new text.")
        raise ApprovalError("not_pending", f"This digest was already {draft.get('status')}.")
    return draft


def _close(draft: dict, status: str, by: str, extra: Optional[dict] = None, at: Optional[str] = None) -> None:
    now = at or DS.iso()
    draft["status"] = status
    draft[status] = dict({"at": now, "by": by}, **(extra or {}))
    draft["updated_at"] = now
    DS.save_draft(draft)
    # the next period starts where this one ended, whether it was sent or thrown away
    DS.set_state(draft["scope"], cursor=draft["period"]["end"], draft_id=None)


def discard_draft(draft_id: str, *, by: str) -> dict:
    draft = _pending(draft_id)
    _close(draft, "discarded", str(by or "").lower())
    DS.append_log({"event": "discarded", "scope": draft["scope"], "draft_id": draft_id, "by": str(by or "").lower()})
    return draft


def send_draft(draft_id: str, *, approved_by: str, content_hash: str, deliver: Callable, now: Callable[[], float] = time.time,
               sleep: Callable[[float], None] = time.sleep, delay_s: float = 3.0, base_url: Optional[str] = None,
               sender: str = "") -> dict:
    """Send the approved draft to its recipients (one message at a time, idempotent through the send log).

    ``approved_by`` is the admin's address and ``content_hash`` the hash of the text he previewed (``build.content_hash``); without
    either, or when the draft is no longer exactly that text, nothing is sent.  -> ``{sent, failed, skipped, aborted}``."""
    who = str(approved_by or "").strip().lower()
    if not _EMAIL_RE.match(who):
        raise ApprovalError("approval_required", "A digest is sent only on an explicit approval by a named admin.")
    return _send(draft_id, who, content_hash, deliver=deliver, now=now, sleep=sleep, delay_s=delay_s, base_url=base_url, sender=sender)


def auto_send_draft(draft_id: str, *, deliver: Callable, now: Callable[[], float] = time.time,
                    sleep: Callable[[float], None] = time.sleep, delay_s: float = AUTO_DELAY_S, base_url: Optional[str] = None,
                    sender: str = "") -> dict:
    """The scheduled send of a draft whose group is in mode ``auto`` (owner 2026-10-28).  The same path as the admin Send; the
    approver is :data:`AUTO_ACTOR` and the "previewed" text is the draft as it stands now.  Refuses a group that is not in ``auto``."""
    draft = _pending(draft_id)
    if DS.get_settings(draft["scope"])["mode"] != "auto":
        raise ApprovalError("not_auto", "This group is not set to send automatically.")
    return _send(draft_id, AUTO_ACTOR, B.content_hash(draft), deliver=deliver, now=now, sleep=sleep, delay_s=delay_s,
                 base_url=base_url, sender=sender)


def _send(draft_id: str, who: str, content_hash: str, *, deliver: Callable, now: Callable[[], float], sleep: Callable[[float], None],
          delay_s: float, base_url: Optional[str], sender: str) -> dict:
    """The one send path.  ``who`` is already checked by the caller (an admin's address, or :data:`AUTO_ACTOR`)."""
    draft = _pending(draft_id)
    if not isinstance(content_hash, str) or not content_hash or content_hash != B.content_hash(draft):
        raise ApprovalError("content_changed", "The text changed since you previewed it - review the current draft and approve that.")
    if not B.header_safe(str(draft.get("subject") or "")):
        raise ApprovalError("unsafe_subject", "The subject contains characters that are not allowed in a mail header.")
    age = now() - (DS.parse_ts(draft.get("updated_at")) or 0)
    if age > MAX_AGE_S:
        raise ApprovalError("stale", "This draft is too old to send as it is - refresh the drafts first.")
    group = B.get_group(draft["scope"])
    if group is None:
        raise ApprovalError("group_gone", "The project or production no longer exists.")
    settings = DS.get_settings(draft["scope"])
    if settings["mode"] == "off":
        raise ApprovalError("mode_off", "Updates by e-mail are switched off for this group.")
    current, left = B.resolve_recipients(group, settings)
    now_by_email = {r["email"]: r for r in current}
    needed = frozenset().union(*[frozenset(i.get("requires") or []) for i in draft["items"]]) if draft["items"] else frozenset()
    to_send: list[mailing.Recipient] = []
    skipped: list[dict] = [dict(s) for s in left if any(s["email"] == r["email"] for r in draft["recipients"])]
    for r in draft["recipients"]:
        cur = now_by_email.get(r["email"])
        if cur is None:
            if not any(s["email"] == r["email"] for s in skipped):
                skipped.append({"email": r["email"], "reason": "no_longer_a_recipient"})
        elif not needed <= group.caps(cur["role"]):
            skipped.append({"email": r["email"], "reason": "access_changed"})
        else:
            to_send.append(mailing.Recipient(cur["email"], cur["name"], cur["lang"]))
    if not to_send:
        raise ApprovalError("no_recipients", "Nobody on the list can receive this digest any more.")
    # the text names motors (results, notes, the recommended variant): every person it goes to must still be allowed to open them
    for item in draft["items"]:
        motor = item.get("motor")
        if motor and not all(B.motor_visible(r.email, motor[0], motor[1]) for r in to_send):
            raise ApprovalError("access_changed", "Somebody on the list can no longer open a motor this text mentions - "
                                "make new drafts (Admin, Digests, Make drafts now) and review them.")
    log_file = DS.sendlog_file(draft_id)
    plan = mailing.plan(draft_id, to_send, [], mailing.read_log(log_file), retry_failed=True)
    base = base_url or E.app_url()
    sender = sender or E.mail_from() or "noreply@aerostator.com"
    result = mailing.send(plan, deliver=deliver, log_file=log_file, base_url=base, api_base=base, sender=sender,
                          delay_s=delay_s, sleep=sleep, now=now,
                          build=lambda r: B.build_message(draft, r.email, base=base, sender=sender))
    result["skipped"] = skipped + [{"email": e, "reason": why} for e, why in plan.skipped]
    state = mailing.log_state(mailing.read_log(log_file), draft_id)
    delivered = [r.email for r in to_send if state.get(r.email) == "sent"]
    outstanding = [r.email for r in to_send if state.get(r.email) != "sent"]
    info = {"sent": len(delivered), "failed": len(outstanding), "skipped": result["skipped"], "aborted": result["aborted"]}
    stamp = DS.iso(now())                                  # the time of this send (the run's clock; the wall clock when it is time.time)
    if not outstanding and not result["aborted"]:
        _close(draft, "sent", who, info, at=stamp)
        DS.append_log({"event": "sent", "scope": draft["scope"], "draft_id": draft_id, "by": who, "sent": info["sent"],
                       "failed": 0, "skipped": len(result["skipped"])})
    elif delivered:
        # some went out, some did not: the draft stays open as ``partial`` and a new approval retries ONLY the others
        # (the send log skips everyone already sent); the next scheduler run leaves it alone
        draft["status"] = "partial"
        draft["partial"] = dict(info, at=stamp, by=who, outstanding=outstanding)
        draft["updated_at"] = stamp
        DS.save_draft(draft)
        DS.append_log({"event": "partial", "scope": draft["scope"], "draft_id": draft_id, "by": who, "sent": info["sent"],
                       "failed": info["failed"]})
    else:
        # nothing was delivered at all: the draft stays pending so the admin can try again
        draft["last_error"] = {"at": stamp, "by": who, "failed": result["failed"], "aborted": result["aborted"]}
        DS.save_draft(draft)
        DS.append_log({"event": "send_failed", "scope": draft["scope"], "draft_id": draft_id, "by": who, "failed": result["failed"]})
    result["sent_total"], result["outstanding"] = info["sent"], outstanding
    return result


# -- the scheduled run: build the drafts, send the ones whose group is in mode ``auto`` --------------------------------------------

#: report-row actions of ``build.run`` that leave an open draft behind (a quiet period has no ``draft_id``)
_OPEN_DRAFT_ACTIONS = ("drafted", "refreshed", "unchanged", "superseded")


def _note_problem(draft_id: str, code: str, message: str) -> None:
    """Remember on the open draft why the automatic send did not go out (the owner report and Admin show it)."""
    def mutate(doc):
        if isinstance(doc, dict) and doc.get("status") in DS.OPEN_STATUSES:
            doc["last_error"] = {"at": DS.iso(), "by": AUTO_ACTOR, "code": code, "message": message[:200]}

    DS.update_draft(draft_id, mutate)


def _auto_one(draft_id: str, scope: str, deliver: Callable, **kw) -> dict:
    """Send one draft in auto mode; never raises.  -> ``{status: sent|partial|failed|blocked|busy|error, sent, failed, skipped, ...}``."""
    try:
        res = auto_send_draft(draft_id, deliver=deliver, **kw)
    except mailing.SendAborted as exc:                 # another send of this draft is running (or crashed): try again tomorrow
        return {"status": "busy", "code": "send_in_progress", "message": str(exc)[:200]}
    except ApprovalError as exc:
        _note_problem(draft_id, exc.code, str(exc))
        DS.append_log({"event": "auto_blocked", "scope": scope, "draft_id": draft_id, "by": AUTO_ACTOR, "code": exc.code})
        return {"status": "blocked", "code": exc.code, "message": str(exc)[:200]}
    except Exception as exc:                           # noqa: BLE001 - one group's problem never stops the others
        log.warning("digest auto-send of %s failed (%s: %s)", draft_id, type(exc).__name__, str(exc)[:160])
        _note_problem(draft_id, "error", f"{type(exc).__name__}: {str(exc)[:160]}")
        return {"status": "error", "code": type(exc).__name__, "message": str(exc)[:200]}
    status = (DS.load_draft(draft_id) or {}).get("status")
    return {"status": {"sent": "sent", "partial": "partial"}.get(status, "failed"), "sent": res["sent_total"],
            "failed": len(res["outstanding"]), "skipped": res["skipped"]}


def run_daily(now: Optional[float] = None, *, scopes: Optional[list] = None, write: bool = True, send: bool = True,
              results: Callable[[str, str], dict] = C.stored_results, deliver: Optional[Callable] = None,
              sleep: Callable[[float], None] = time.sleep, delay_s: float = AUTO_DELAY_S, base_url: Optional[str] = None,
              sender: str = "") -> list[dict]:
    """THE scheduled run (owner 2026-10-28): make the due drafts, then send each one whose group is in mode ``auto``.

    Called only by the daily jobs - the owner report's step 1 and ``python -m motor_ai_sim.digest run`` - never by a page load or
    the admin "make drafts now" route (those call ``build.run``, which only writes drafts).  Groups in ``approve`` mode keep their
    draft waiting in Admin; ``off`` groups get none; a quiet group has no draft to send.  An open draft left from an earlier run
    (a failed or partial send) is retried.  ``write=False`` (dry run) or ``send=False`` sends nothing.

    Returns the rows of ``build.run``; a row whose draft was handled automatically carries ``auto`` = the result of that send."""
    now = time.time() if now is None else now
    rows = B.run(now, scopes=scopes, write=write, results=results)
    if not (write and send):
        return rows
    for row in rows:
        did, scope = row.get("draft_id"), row.get("scope")
        if not did or not (row.get("action") in _OPEN_DRAFT_ACTIONS or row.get("reason") == "partial_send_open"):
            continue
        if DS.get_settings(scope)["mode"] != "auto":
            continue
        if deliver is None and not E.smtp_configured():
            msg = "E-mail (SMTP) is not configured on this server."
            _note_problem(did, "smtp_not_configured", msg)
            row["auto"] = {"status": "blocked", "code": "smtp_not_configured", "message": msg}
            continue
        row["auto"] = _auto_one(did, scope, deliver or (lambda msg: E.deliver(msg)), now=lambda: now, sleep=sleep, delay_s=delay_s,
                                base_url=base_url, sender=sender)
    return rows
