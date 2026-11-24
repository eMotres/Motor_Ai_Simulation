"""Acknowledgement tracking and reminders for published revisions (owner 2026-11-15, "this is very important").

Each factory (``oem``) member acknowledges every revision.  The owner and the engineers see who has and who has not, with dates (the
Changes tab).  A revision that was published WITH an alert (``revision_alert``) and is still unacknowledged by a factory member after
24 h and again after 72 h sends that member ONE reminder mail each (the highest level due; a missed run does not send both); the hours count
from the daily digest that carried the revision to that member (urgent revisions: from the publication); the owner's
daily report lists every unacknowledged revision in one line per member (old revisions that never had an alert are listed, not mailed).

Mail goes through ``revision_alert.SEND`` (the existing mail module; a server without SMTP sends nothing).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from motor_ai_sim import auth_email
from motor_ai_sim.production import models, revision_alert, store

log = logging.getLogger(__name__)

LEVELS_H = (24, 72)
REQUIRED_ROLES = ("oem",)


def _ts(value: Any) -> Optional[float]:
    from motor_ai_sim.digest import store as DS
    return DS.parse_ts(value)


def required_members(production: dict) -> List[Dict[str, str]]:
    return [r for r in revision_alert.recipients(production) if r["role"] in REQUIRED_ROLES]


def ack_status(production: dict, revision: dict) -> List[Dict[str, Any]]:
    """Every member with whether an acknowledgement is required of them and when they acknowledged (None = not yet)."""
    acked = {str(a.get("email") or "").lower(): a.get("time") for a in revision.get("acknowledgements") or [] if isinstance(a, dict)}
    return [{"email": r["email"], "role": r["role"], "required": r["role"] in REQUIRED_ROLES, "acked_at": acked.get(r["email"])}
            for r in revision_alert.recipients(production)]


def due_level(hours: float, done: List[int]) -> Optional[int]:
    """The reminder level due now (72 once 72 h passed, 24 after 24 h) unless that level was already sent; None otherwise."""
    lvl = 72 if hours >= 72 else 24 if hours >= 24 else None
    return lvl if lvl is not None and lvl not in done else None


def pending(die: str, config: str, production: dict, revisions: List[dict], now: float) -> List[Dict[str, Any]]:
    """One row per (revision, factory member) that has not acknowledged: ``{number, email, hours, alerted, reminders}``."""
    req = {m["email"] for m in required_members(production)}
    rows = []
    for r in revisions:
        if not isinstance(r, dict):
            continue
        alert = r.get("alert") if isinstance(r.get("alert"), dict) else None
        since = _ts((alert or {}).get("sent_at") or r.get("date"))
        if since is None:
            continue
        acked = {str(a.get("email") or "").lower() for a in r.get("acknowledgements") or [] if isinstance(a, dict)}
        done: Dict[str, List[int]] = {}
        for x in r.get("reminders") or []:
            if isinstance(x, dict) and x.get("mail") != "not_configured":      # an unsent notice (no SMTP then) is not a reminder given
                done.setdefault(str(x.get("email")), []).append(int(x.get("level") or 0))
        # A revision that waits for the daily digest (owner 2026-11-19) counts from the digest THIS member got it in, not from the
        # publication: before that digest there is nothing to remind about (listed in the report, not mailed).
        urgent = bool(alert and alert.get("mode") == "urgent")         # urgent: the clock is the publication, a failed mail or not
        digest_mode = bool(alert and alert.get("mode") == "digest") or bool(r.get("digest_sent")) or (bool(r.get("digest_pending")) and not urgent)
        sent_in = {str(x.get("email") or "").lower(): x.get("sent_at") for x in r.get("digest_sent") or [] if isinstance(x, dict)}
        for em in sorted(req - acked):
            t0, alerted, stamp = since, alert is not None, (alert or {}).get("sent_at") or r.get("date")
            if digest_mode:
                got = _ts(sent_in.get(em))
                if got is None:
                    t0, alerted, stamp = since, False, r.get("date")
                else:
                    t0, alerted, stamp = got, True, sent_in.get(em)
            rows.append({"number": str(r.get("number")), "email": em, "hours": max(0.0, (now - t0) / 3600.0), "alerted": alerted,
                         "reminders": sorted(done.get(em, [])), "since": stamp})
    return rows


def reminder_mail(die: str, config: str, number: str, hours: float, level: int) -> Dict[str, str]:
    url = revision_alert.link(die, config, number)
    h = int(hours)
    subject = (f"[MOTRES] Reminder ({level} h): {die} {config} revision {number} - action required: acknowledge / "
               f"提醒（{level} 小时）：版本 {number} - 需要确认")
    en = (f"{die} {config}: revision {number} was published {h} hours ago and you have not acknowledged it yet.\n"
          f"ACTION REQUIRED: open the Changes tab, read what changed and acknowledge it.\n"
          f"{revision_alert.SUPERSEDED_SENTENCE_EN}\n\n{url}")
    zh = (f"{die} {config}：版本 {number} 已于 {h} 小时前发布，您尚未确认。\n"
          f"需要操作：请打开“变更”页，阅读变更内容并确认。\n{revision_alert.SUPERSEDED_SENTENCE_ZH}\n\n{url}")
    return {"subject": subject, "body": en + "\n\n" + "-" * 40 + "\n\n" + zh}


def _productions(root: Optional[Any] = None) -> List[tuple]:
    from pathlib import Path
    base = Path(root) if root is not None else store.production_root()
    out = []
    for pj in sorted(base.glob("*/*/production.json")):
        rec = store._read_json(pj, default={})
        if isinstance(rec, dict) and rec.get("die") and rec.get("config") and (rec.get("status") or "active") != "archived":
            out.append((str(rec["die"]), str(rec["config"]), rec))
    return out


#: a reservation older than this is taken as the trace of a crashed worker and may be taken over
CLAIM_TTL_S = 900.0


def _reserve(die: str, config: str, number: str, email: str, level: int, now: float) -> bool:
    """Reserve (revision, member, level) before the mail goes out, so two workers (or two overlapping runs) never send the same
    reminder.  False when somebody else holds a fresh reservation or the level is already recorded."""
    won: Dict[str, bool] = {"ok": False}

    def mut(d):
        for r in d.get("revisions", []):
            if not isinstance(r, dict) or str(r.get("number")) != number:
                continue
            if any(x.get("email") == email and int(x.get("level") or 0) == level and x.get("mail") != "not_configured"
                   for x in r.get("reminders") or [] if isinstance(x, dict)):
                return
            claims = [c for c in r.get("reminder_claims") or [] if isinstance(c, dict)]
            for c in claims:
                if c.get("email") == email and c.get("level") == level and now - float(c.get("at") or 0) < CLAIM_TTL_S:
                    return
            r["reminder_claims"] = [c for c in claims if not (c.get("email") == email and c.get("level") == level)] + [
                {"email": email, "level": level, "at": now}]
            won["ok"] = True

    store.mutate_file(die, config, "revisions.json", mut)
    return won["ok"]


def _release(die: str, config: str, number: str, email: str, level: int) -> None:
    def mut(d):
        for r in d.get("revisions", []):
            if isinstance(r, dict) and str(r.get("number")) == number:
                r["reminder_claims"] = [c for c in r.get("reminder_claims") or []
                                        if not (c.get("email") == email and c.get("level") == level)]

    try:
        store.mutate_file(die, config, "revisions.json", mut)
    except Exception as exc:                                              # noqa: BLE001
        log.warning("revision reminder claim not released: %s", exc)


def run_reminders(now: Optional[float] = None, *, root: Optional[Any] = None, send: Optional[Callable[[str, str, str], str]] = None) -> List[Dict[str, Any]]:
    """Send the reminders that are due; returns one row per mail (``{die, config, revision, email, level, mail}``).  Safe to run any number of
    times a day: a level is sent once per member and revision.  A mail that FAILED is not recorded and is tried again by the next run."""
    now = time.time() if now is None else now
    sender = send or (lambda to, s, b: revision_alert.SEND(to, s, b))
    sent_rows: List[Dict[str, Any]] = []
    for die, config, production in _productions(root):
        try:
            revisions = [r for r in store.load_file(die, config, "revisions.json").get("revisions") or [] if isinstance(r, dict)]
            for row in pending(die, config, production, revisions, now):
                if not row["alerted"]:
                    continue                                          # an old revision that never had an alert: listed in the report, not mailed
                lvl = due_level(row["hours"], row["reminders"])
                if lvl is None:
                    continue
                if not _reserve(die, config, row["number"], row["email"], lvl, now):
                    continue                                              # another worker is sending exactly this reminder
                mail = reminder_mail(die, config, row["number"], row["hours"], lvl)
                try:
                    status = sender(row["email"], mail["subject"], mail["body"])
                except Exception as exc:                                  # noqa: BLE001 - one bad address must not stop the others
                    log.warning("revision reminder to %s failed: %s", row["email"], type(exc).__name__)
                    status = "failed"
                sent_rows.append({"die": die, "config": config, "revision": row["number"], "email": row["email"], "level": lvl, "mail": status})
                if status != "sent":                                      # failed / not_configured: nothing was delivered, the level stays due
                    _release(die, config, row["number"], row["email"], lvl)
                    continue
                entry = {"email": row["email"], "level": lvl, "sent_at": models._now(), "mail": status}

                def attach(d, number=row["number"], entry=entry, lvl=lvl):
                    for r in d.get("revisions", []):
                        if isinstance(r, dict) and str(r.get("number")) == number:
                            r.setdefault("reminders", []).append(entry)
                            r["reminder_claims"] = [c for c in r.get("reminder_claims") or []
                                                    if not (c.get("email") == entry["email"] and c.get("level") == lvl)]

                store.mutate_file(die, config, "revisions.json", attach)
                store.append_audit(die, config, models.audit_entry("system/reminder", "revision_reminder", "revision", row["number"],
                                                                   {"email": row["email"], "level": lvl, "mail": status}))
        except Exception as exc:                                      # noqa: BLE001 - one production must not stop the others
            log.warning("revision reminders for %s/%s failed: %s", die, config, exc)
    return sent_rows


def report_rows(now: Optional[float] = None, *, root: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Every unacknowledged (revision, factory member) of every production, longest waiting first - the owner's daily report lines."""
    now = time.time() if now is None else now
    out = []
    for die, config, production in _productions(root):
        revisions = [r for r in store.load_file(die, config, "revisions.json").get("revisions") or [] if isinstance(r, dict)]
        for row in pending(die, config, production, revisions, now):
            out.append({**row, "die": die, "config": config})
    return sorted(out, key=lambda r: -r["hours"])


def main(argv: Optional[List[str]] = None) -> int:
    """``python -m motor_ai_sim.production.revision_ack [--send]``: without ``--send`` list who is waiting (and who WOULD be reminded)."""
    import argparse
    ap = argparse.ArgumentParser(prog="python -m motor_ai_sim.production.revision_ack", description=__doc__.split("\n\n")[0])
    ap.add_argument("--send", action="store_true", help="send the reminders that are due (each level once per member and revision)")
    a = ap.parse_args(argv)
    now = time.time()
    if a.send:
        rows = run_reminders(now)
        for r in rows:
            print("reminder %dh -> %s: %s %s revision %s (%s)" % (r["level"], r["email"], r["die"], r["config"], r["revision"], r["mail"]))
        print("%d reminder(s)" % len(rows))
        return 1 if any(r["mail"] == "failed" for r in rows) else 0
    for r in report_rows(now):
        print("%s %s revision %s: %s waiting %d h%s%s" % (r["die"], r["config"], r["number"], r["email"], int(r["hours"]),
                                                       "" if r["alerted"] else " (no alert was sent)",
                                                       " | due: %dh reminder" % due_level(r["hours"], r["reminders"]) if r["alerted"] and due_level(r["hours"], r["reminders"]) else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
