"""The ONLY place where a digest can leave the house: an explicit approval by a named admin.

Owner rule 2026-10-26: no outside message without the owner's OK on the final text.  So there is no automatic mode: the
scheduler (:func:`motor_ai_sim.digest.build.run`) writes drafts, and this module sends one only when
:func:`send_draft` is called with the approving admin's address - which the admin route does after the admin previewed the text
and clicked Send.  ``tests/test_digest.py`` asserts that nothing else in the package reaches SMTP and that a run sends nothing.

At send time the recipients are re-checked against TODAY's state: a person who left the group, unsubscribed, was disabled or lost
the access the text needs is skipped (they are named in the result), and nobody who was not on the approved list is added.
"""
from __future__ import annotations

import re
import time
from typing import Callable, Optional

from motor_ai_sim import auth_email as E
from motor_ai_sim import mailing
from motor_ai_sim.digest import build as B
from motor_ai_sim.digest import store as DS

#: a draft that nobody looked at for this long is not sent as it is (the scheduler refreshes a pending draft every run)
MAX_AGE_S = 14 * 86400.0
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s.]+$")


class ApprovalError(Exception):
    """``code``: approval_required | content_changed | not_found | not_pending | stale | group_gone | mode_off | no_recipients |
    access_changed | unsafe_subject | send_in_progress"""

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


def _close(draft: dict, status: str, by: str, extra: Optional[dict] = None) -> None:
    now = DS.iso()
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
    if not outstanding and not result["aborted"]:
        _close(draft, "sent", who, info)
        DS.append_log({"event": "sent", "scope": draft["scope"], "draft_id": draft_id, "by": who, "sent": info["sent"],
                       "failed": 0, "skipped": len(result["skipped"])})
    elif delivered:
        # some went out, some did not: the draft stays open as ``partial`` and a new approval retries ONLY the others
        # (the send log skips everyone already sent); the next scheduler run leaves it alone
        draft["status"] = "partial"
        draft["partial"] = dict(info, at=DS.iso(), by=who, outstanding=outstanding)
        draft["updated_at"] = DS.iso()
        DS.save_draft(draft)
        DS.append_log({"event": "partial", "scope": draft["scope"], "draft_id": draft_id, "by": who, "sent": info["sent"],
                       "failed": info["failed"]})
    else:
        # nothing was delivered at all: the draft stays pending so the admin can try again
        draft["last_error"] = {"at": DS.iso(), "by": who, "failed": result["failed"], "aborted": result["aborted"]}
        DS.save_draft(draft)
        DS.append_log({"event": "send_failed", "scope": draft["scope"], "draft_id": draft_id, "by": who, "failed": result["failed"]})
    result["sent_total"], result["outstanding"] = info["sent"], outstanding
    return result
