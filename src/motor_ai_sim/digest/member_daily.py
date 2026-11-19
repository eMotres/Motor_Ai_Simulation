"""ONE production e-mail per member per day (owner 2026-11-19: "e-mails once a day, with the changes, to everybody").

Everything that happens in a production stays in the production at once (the revision, the Changes tab, the Discussion, files, parts,
tests, members).  None of it mails on its own.  Once a day each member gets ONE e-mail with what was recorded since the last digest
over ALL productions he is a member of: per production the lines of the activity collector (``digest.collect.collect_production``,
the same lines the per-production digest used), English then Chinese, and one "open" link per production (to the Changes tab when a
revision is in it, where the acknowledgement is).  Nothing new = no mail.

* recipients: the production digest rules, unchanged - ``build.resolve_recipients`` (members, verified, enabled, not unsubscribed
  from all e-mail, factory members only with a signed NDA, the per-production opt-outs and ``off`` mode honoured);
* each line is shown only when THIS member may see it (``Group.caps`` of his role, ``motor_visible`` for motor lines);
* the hour: ``PRODUCTION_DIGEST_HOUR`` (default 8) in ``PRODUCTION_DIGEST_TZ`` (default ``Asia/Shanghai``); one hour for all;
* idempotent through the mailing machinery's send log (``mailing.send``: an ``attempt`` line before SMTP, ``sent`` / ``failed`` after;
  a member with a ``sent`` line for the day's campaign is never mailed again, one with an unresolved ``attempt`` is skipped, not
  retried).  The member's watermark (what the digest covered) is written as ``pending`` BEFORE the send and committed from the log at
  the next run, so a crash between SMTP and the state file neither repeats a day nor loses one; a missed day is sent at the next run
  with everything since the watermark;
* a revision that was published ``urgent`` ("urgent: mail now") was mailed at once and is listed here marked as already sent;
* the revisions a digest carried are stamped ``digest_sent`` {email, sent_at} - the 24 h / 72 h acknowledgement reminders count from it.

``python -m motor_ai_sim.digest.member_daily [--send]`` (dry run by default); the deploy timer runs it hourly: it sends only when the
local hour is past the send hour and the day's campaign has not gone out.  ``PRODUCTION_MEMBER_DIGEST=0`` switches the whole thing off
and the old per-production digests run again.
"""
from __future__ import annotations

import dataclasses
import html
import json
import logging
import os
import time
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from motor_ai_sim import auth_email as E
from motor_ai_sim import comm_prefs as CP
from motor_ai_sim import mailing
from motor_ai_sim.digest import build as B
from motor_ai_sim.digest import collect as C
from motor_ai_sim.digest import store as DS

log = logging.getLogger(__name__)

ENV_ON = "PRODUCTION_MEMBER_DIGEST"
ENV_HOUR = "PRODUCTION_DIGEST_HOUR"
ENV_TZ = "PRODUCTION_DIGEST_TZ"
DEFAULT_HOUR = 8
DEFAULT_TZ = "Asia/Shanghai"
#: the first digest of a member (no watermark yet) covers this much, never the whole history
FIRST_WINDOW_S = 24 * 3600.0
#: a member who missed many days gets at most this much history
MAX_WINDOW_S = 14 * 24 * 3600.0
#: lines per production before "and N more"
MAX_LINES = 20
SENDER_DELAY_S = 1.0


def enabled() -> bool:
    """Is the member digest in force (default yes)?  When it is, the production groups are not sent by the per-production digests."""
    return (os.environ.get(ENV_ON) or "1").strip().lower() not in ("0", "off", "false", "no")


def send_hour() -> int:
    try:
        h = int((os.environ.get(ENV_HOUR) or "").strip() or DEFAULT_HOUR)
    except ValueError:
        h = DEFAULT_HOUR
    return min(23, max(0, h))


def _tz():
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo((os.environ.get(ENV_TZ) or "").strip() or DEFAULT_TZ)
    except Exception:                                      # noqa: BLE001 - an unknown zone name falls back to the default
        return ZoneInfo(DEFAULT_TZ)


def local(now: float) -> datetime:
    return datetime.fromtimestamp(now, _tz())


def due(now: float) -> bool:
    """Is it past the send hour of the local day?"""
    return local(now).hour >= send_hour()


def campaign_id(now: float) -> str:
    return "production_daily_" + local(now).strftime("%Y-%m-%d")


# -- the member watermark ---------------------------------------------------------------------------------------------------------

def _state_file() -> Path:
    return DS.root() / "member_daily.json"


def load_state() -> dict:
    try:
        d = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d = d if isinstance(d, dict) else {}
    d.setdefault("through", {})
    d.setdefault("pending", {})
    return d


def save_state(st: dict) -> None:
    p = _state_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp-%d" % os.getpid())
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(str(tmp), str(p))


def reconcile(st: dict) -> dict:
    """Commit the watermark of every pending digest the send log says went out; drop the others (they are covered again by the next
    one).  Called at the start of every run, so a crash between SMTP and the state file loses nothing and repeats nothing."""
    for email, pend in list((st.get("pending") or {}).items()):
        camp = str((pend or {}).get("campaign") or "")
        try:
            done = mailing.log_state(mailing.read_log(mailing.log_path(camp)), camp).get(email) == "sent"
        except ValueError:
            done = False
        if done:
            st["through"][email] = max(float(st["through"].get(email) or 0.0), float(pend.get("through") or 0.0))
        st["pending"].pop(email, None)
    return st


# -- what a member gets ------------------------------------------------------------------------------------------------------------

def _urgent_numbers(group: B.Group) -> set:
    """The revisions of this production that were published urgent (mailed at once)."""
    from motor_ai_sim.production import store as PR
    try:
        rows = PR.load_file(str(group.record.get("die")), str(group.record.get("config")), "revisions.json").get("revisions") or []
    except Exception:                                      # noqa: BLE001
        return set()
    return {str(r.get("number")) for r in rows
            if isinstance(r, dict) and isinstance(r.get("alert"), dict) and r["alert"].get("mode") == "urgent"}


def _mark_urgent(items: list, urgent: set) -> list:
    out = []
    for it in items:
        if it.kind == "revision" and it.key.startswith("rev:") and it.key[4:] in urgent:
            it = dataclasses.replace(it, en=it.en + " (already e-mailed when it was published)",
                                     zh=it.zh + "（发布时已邮件通知）")
        out.append(it)
    return out


def _link(group: B.Group, items: list, role: str) -> str:
    """The one link of the production: the exact revision in the Changes tab when a revision is in it (acknowledge there), else the page."""
    from motor_ai_sim.production import revision_alert as RA
    die, config = str(group.record.get("die")), str(group.record.get("config"))
    revs = [it.key[4:] for it in items if it.kind == "revision" and it.key.startswith("rev:")]
    if revs:
        return RA.link(die, config, revs[-1])
    return E.app_url() + (group.path or "")


def sections_for(member: dict, groups: List[dict], start: float, end: float,
                 cache: Optional[dict] = None) -> List[Dict[str, Any]]:
    """The production sections ``member`` gets for ``(start, end)``: ``[{group, items, more, link, revisions, text, html}]``, empty
    productions left out.  ``groups`` = the member's groups as ``{"group": Group, "role": str}``."""
    cache = {} if cache is None else cache
    out = []
    for g in groups:
        group, role = g["group"], g["role"]
        key = (group.scope, start)
        if key not in cache:
            cache[key] = _mark_urgent(C.collect_production(group.record, start, end), _urgent_numbers(group))
        caps = group.caps(role)
        items = [it for it in cache[key]
                 if frozenset(it.requires or ()) <= caps and (not it.motor or B.motor_visible(member["email"], it.motor[0], it.motor[1]))]
        if not items:
            continue
        # ONE link per production (below): the lines themselves carry none
        items = [dataclasses.replace(it, link_policy="none") for it in sorted(items, key=lambda it: it.ts)]
        more = max(0, len(items) - MAX_LINES)
        shown = items[:MAX_LINES]
        text, body = B.compose(group, shown, more, start, end, True)
        out.append({"group": group, "role": role, "items": items, "more": more, "link": _link(group, items, role),
                    "revisions": [it.key[4:] for it in items if it.kind == "revision" and it.key.startswith("rev:")
                                  and it.key[4:] not in _urgent_numbers(group)],
                    "text": text, "html": body})
    return out


def subject_for(sections: list, end: float) -> str:
    day = local(end).strftime("%Y-%m-%d")
    n = len(sections)
    if n == 1:
        g = sections[0]["group"]
        return f"[MOTRES] {g.title_en} - what changed {day} / {g.title_zh} - 更新摘要 {day}"
    return f"[MOTRES] Production updates {day}: {n} productions / 生产更新 {day}：{n} 个生产"


def compose_member(member: dict, sections: list, start: float, end: float, *, base: Optional[str] = None) -> Dict[str, str]:
    """``{subject, text, html}`` of the one mail: a short head, each production's section, its open link, the personal footer."""
    base = base or E.app_url()
    day0 = local(start).strftime("%Y-%m-%d")
    t: List[str] = [f"Changes in your productions since {day0}.", f"自 {day0} 以来您所在生产的变更。", ""]
    h: List[str] = [f'<p style="margin:0 0 14px;font-size:15px">Changes in your productions since {day0}.<br>'
                    f'自 {day0} 以来您所在生产的变更。</p>']
    for s in sections:
        t.append("=" * 40)
        t.append(s["text"].rstrip())
        has_rev = bool(s["revisions"])
        t.append("")
        t.append(("Open and acknowledge: " if has_rev else "Open: ") + s["link"])
        t.append(("打开并确认：" if has_rev else "打开：") + s["link"])
        t.append("")
        h.append('<hr style="border:0;border-top:2px solid #cbd5e1;margin:20px 0">' + s["html"])
        lab_en, lab_zh = ("Open and acknowledge", "打开并确认") if has_rev else ("Open", "打开")
        u = html.escape(s["link"], quote=True)
        h.append(f'<p style="margin:0 0 12px;font-size:15px"><a href="{u}" style="color:#2563eb;font-weight:bold">{lab_en}</a>'
                 f' · <a href="{u}" style="color:#2563eb;font-weight:bold">{lab_zh}</a></p>')
    prefs = CP.prefs_url(base, member["email"], member.get("lang") or "en")
    f_t = [f"You get this once a day because you are a member of {len(sections)} production(s) with changes.",
           f"All e-mail preferences: {prefs}", "",
           f"您每天收到一封此邮件，因为您是有变更的 {len(sections)} 个生产的成员。", f"邮件偏好设置：{prefs}", "", mailing.COMPANY_FOOTER]
    f_h = [f"You get this once a day because you are a member of {len(sections)} production(s) with changes. "
           f'<a href="{html.escape(prefs, quote=True)}" style="color:#6b7280">All e-mail preferences</a>',
           f"您每天收到一封此邮件，因为您是有变更的 {len(sections)} 个生产的成员。 "
           f'<a href="{html.escape(prefs, quote=True)}" style="color:#6b7280">邮件偏好设置</a>',
           html.escape(mailing.COMPANY_FOOTER)]
    subject = subject_for(sections, end)
    return {"subject": subject, "text": "\n".join(t).rstrip() + "\n--\n" + "\n".join(f_t) + "\n",
            "html": B.shell(subject, "\n".join(h), "<br>".join(f_h), "zh-CN"), "prefs": prefs}


def build_message(member: dict, mail: Dict[str, str], *, sender: str = "") -> EmailMessage:
    sender = sender or E.mail_from() or "noreply@aerostator.com"
    msg = EmailMessage(policy=policy.default.clone(max_line_length=998))
    msg["From"] = sender
    msg["To"] = member["email"]
    msg["Subject"] = mail["subject"]
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=(sender.split("@")[-1].strip("> ") or "aerostator.com"))
    msg["List-Unsubscribe"] = f"<{mail['prefs']}>"
    msg.set_content(mail["text"])
    msg.add_alternative(mail["html"], subtype="html")     # text + HTML only: no images, no attachments
    return msg


# -- the run -----------------------------------------------------------------------------------------------------------------------

def _member_groups(groups: Optional[List[B.Group]] = None) -> Dict[str, dict]:
    """``{email: {"email", "name", "lang", "groups": [{"group", "role"}]}}`` over every active production in a mode other than ``off``."""
    members: Dict[str, dict] = {}
    for g in (B.list_groups() if groups is None else groups):
        if g.kind != "production" or not g.active:
            continue
        settings = DS.get_settings(g.scope)
        if settings["mode"] == "off":
            continue
        recipients, _skipped = B.resolve_recipients(g, settings)
        for r in recipients:
            m = members.setdefault(r["email"], {"email": r["email"], "name": r["name"], "lang": r["lang"], "groups": []})
            m["groups"].append({"group": g, "role": r["role"]})
            if r["lang"] == "zh-CN":
                m["lang"] = "zh-CN"
    return members


def _stamp_revisions(member: str, sections: list, sent_at: str) -> None:
    """Remember on each revision that this member's digest carried it (the reminders count from here)."""
    from motor_ai_sim.production import store as PR
    for s in sections:
        die, config = str(s["group"].record.get("die")), str(s["group"].record.get("config"))
        nums = set(s["revisions"])
        if not nums:
            continue

        def mut(d, nums=nums):
            for r in d.get("revisions", []):
                if isinstance(r, dict) and str(r.get("number")) in nums:
                    rows = [x for x in r.get("digest_sent") or [] if isinstance(x, dict) and x.get("email") != member]
                    rows.append({"email": member, "sent_at": sent_at})
                    r["digest_sent"] = rows

        try:
            PR.mutate_file(die, config, "revisions.json", mut)
        except Exception as exc:                           # noqa: BLE001 - a stamp must never undo a sent mail
            log.warning("digest stamp for %s/%s failed: %s", die, config, exc)


def run(now: Optional[float] = None, *, send: bool = True, deliver: Optional[Callable] = None, force: bool = False,
        groups: Optional[List[B.Group]] = None, base_url: Optional[str] = None, sender: str = "",
        sleep: Callable[[float], None] = time.sleep, delay_s: float = SENDER_DELAY_S) -> List[dict]:
    """The scheduled run.  Returns one row per member who has something: ``{email, productions, lines, subject, status}`` where status
    is ``planned`` (dry run), ``sent``, ``failed``, ``already_sent`` or ``not_configured``.  Nothing is written or sent unless
    ``send``; nothing happens before the send hour unless ``force``."""
    now = time.time() if now is None else now
    if not enabled():
        return []
    if not (force or due(now)):
        return []
    end = now
    campaign = campaign_id(now)
    st = reconcile(load_state()) if send else reconcile(json.loads(json.dumps(load_state())))
    members = _member_groups(groups)
    if send:                                                # a member is first seen NOW: his watermark exists from here on, so a first
        for email in members:                               # send that fails is covered again (with the day's changes) by the next run
            st["through"].setdefault(email, end - FIRST_WINDOW_S)
    cache: dict = {}
    rows: List[dict] = []
    todo: List[tuple] = []
    for email in sorted(members):
        m = members[email]
        start = max(float(st["through"].get(email) or 0.0) or (end - FIRST_WINDOW_S), end - MAX_WINDOW_S)
        secs = sections_for(m, m["groups"], start, end, cache)
        if not secs:
            if send:
                st["through"][email] = end                 # nothing new: the watermark moves, no mail
            continue
        mail = compose_member(m, secs, start, end, base=base_url)
        todo.append((m, secs, mail, start))
        rows.append({"email": email, "productions": [s["group"].scope for s in secs], "lines": sum(len(s["items"]) for s in secs),
                     "subject": mail["subject"], "status": "planned"})
    if not send:
        return rows
    if deliver is None and not E.smtp_configured():
        for r in rows:
            r["status"] = "not_configured"
        return rows
    deliver = deliver or (lambda msg: E.deliver(msg))
    log_file = mailing.log_path(campaign)
    base = base_url or E.app_url()
    sender = sender or E.mail_from() or "noreply@aerostator.com"
    by_email = {m["email"]: (m, secs, mail) for m, secs, mail, _s in todo}
    plan = mailing.plan(campaign, [mailing.Recipient(m["email"], m["name"], m["lang"]) for m, _s, _m, _t in todo], [],
                        mailing.read_log(log_file), retry_failed=True)
    for em, why in plan.skipped:
        for r in rows:
            if r["email"] == em:
                r["status"] = "already_sent" if why == "already_sent" else why
    for r in plan.to_send:                                  # the watermark is pending BEFORE the send, committed from the log
        st["pending"][r.email] = {"campaign": campaign, "through": end}
    save_state(st)
    if plan.to_send:
        mailing.send(plan, deliver=deliver, log_file=log_file, base_url=base, api_base=base, sender=sender, delay_s=delay_s,
                     sleep=sleep, now=lambda: now,
                     build=lambda r: build_message(by_email[r.email][0], by_email[r.email][2], sender=sender))
    state = mailing.log_state(mailing.read_log(log_file), campaign)
    for r in rows:
        if r["status"] == "planned":
            r["status"] = "sent" if state.get(r["email"]) == "sent" else "failed"
    st = reconcile(st)
    save_state(st)
    stamp = DS.iso(now)
    for r in rows:
        if r["status"] == "sent":
            _stamp_revisions(r["email"], by_email[r["email"]][1], stamp)
    return rows


def main(argv: Optional[list] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m motor_ai_sim.digest.member_daily", description=__doc__.split("\n\n")[0])
    ap.add_argument("--send", action="store_true", help="really send (default: show what would go out)")
    ap.add_argument("--force", action="store_true", help="ignore the send hour")
    a = ap.parse_args(argv)
    rows = run(send=a.send, force=a.force)
    for r in rows:
        print(f"{r['status']:14} {r['email']}  {len(r['productions'])} production(s), {r['lines']} line(s)  {r['subject']}")
    print(("%d member(s)." % len(rows)) if a.send else "DRY RUN - nothing sent. %d member(s) would get a digest." % len(rows))
    return 1 if any(r["status"] == "failed" for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
