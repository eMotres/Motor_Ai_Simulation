"""Production revision alerts (owner 2026-11-15, "this is very important").

Publishing a revision SENDS: one e-mail to every member of the production (factory, engineers, owner), one message in the Discussion
(the thread of the part when the changes name exactly one part, else General) and an entry in the production audit with the recipients.
The text is English with a Chinese copy under it (the production's languages); the subject says the motor, the revision and
"action required: acknowledge".  No prices.  ``preview`` returns the exact subject, body and recipient list the publish dialog shows
BEFORE anything is sent; ``deliver`` sends exactly that.

Mail goes through ``auth_email._send`` (the existing SMTP/relay module, same logging).  A server without SMTP (every sandbox) sends
nothing: each recipient is recorded as ``not_configured``.  Tests replace ``SEND``.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from motor_ai_sim import auth_email
from motor_ai_sim import return_path as RP
from motor_ai_sim.production import models, store

log = logging.getLogger(__name__)

ROLE_ORDER = {"owner": 0, "engineer": 1, "oem": 2}
SUPERSEDED_SENTENCE_EN = "Parts must not be made to a superseded drawing."
SUPERSEDED_SENTENCE_ZH = "不得按已作废的图纸制造零件。"


def _send_default(to: str, subject: str, body: str) -> str:
    """``sent`` | ``failed`` | ``not_configured`` (no SMTP on this server: nothing leaves it)."""
    if not auth_email.smtp_configured():
        return "not_configured"
    return "sent" if auth_email._send(to, subject, body, kind="revision_alert") else "failed"


#: the one place mail leaves this module; tests replace it with a fake mailer
SEND: Callable[[str, str, str], str] = _send_default


def recipients(production: dict) -> List[Dict[str, str]]:
    """Every member of the production once (owner, engineers, the factory), the owner first.  Admins who are not members are not
    mailed: the alert goes to the people the production belongs to."""
    seen, out = set(), []
    for m in (production or {}).get("members") or []:
        em = str(m.get("email") or "").strip().lower()
        if em and em not in seen:
            seen.add(em)
            out.append({"email": em, "role": str(m.get("role") or "oem")})
    return sorted(out, key=lambda r: (ROLE_ORDER.get(r["role"], 9), r["email"]))


def _text(b: Any, lang: str) -> str:
    if isinstance(b, dict):
        t = str(b.get(lang) or "").strip()
        return t or str(b.get("en") or b.get("zh") or "").strip()
    return str(b or "").strip()


def _fv(row: dict) -> str:
    v = row.get("version") or models.file_version(row.get("name"))
    return "%s (v%s)" % (row.get("name"), v) if v else str(row.get("name"))


def link(die: str, config: str, number: str) -> str:
    path = RP.production_path(die, config, "changes", item=number) or RP.production_path(die, config) or ""
    return auth_email.app_url() + path


def build(die: str, config: str, production: dict, revision: dict) -> Dict[str, Any]:
    """The alert for ``revision`` (a normalized revision dict, with ``files`` when it has any): subject, body, recipients."""
    n = str(revision.get("number"))
    url = link(die, config, n)
    files = revision.get("files") or {}
    new_rows = [r for r in files.get("new") or [] if isinstance(r, dict)]
    old_rows = [r for r in files.get("superseded") or [] if isinstance(r, dict)]

    def part(lang: str) -> str:
        en = lang == "en"
        head = (f"{die} {config}: revision {n} was published. ACTION REQUIRED: open the Changes tab and acknowledge it."
                if en else f"{die} {config}：版本 {n} 已发布。需要操作：请打开“变更”页并确认。")
        lines = [head, "", ("Summary: " if en else "摘要：") + _text(revision.get("summary"), lang)]
        ch = [_text(c, lang) for c in revision.get("changes") or [] if _text(c, lang)]
        if ch:
            lines += ["", "What changed:" if en else "变更内容："] + ["- " + c for c in ch]
        if new_rows:
            lines += ["", "New files:" if en else "新文件："] + ["- " + _fv(r) for r in new_rows]
        if old_rows:
            lines += ["", "SUPERSEDED - do not use:" if en else "已作废 - 请勿使用："]
            for r in old_rows:
                by = r.get("replaced_by") or {}
                tail = ((" -> replaced by " if en else " -> 由下列文件取代：") + _fv(by)) if by.get("name") else ""
                lines.append("- " + _fv(r) + tail)
        lines += ["", SUPERSEDED_SENTENCE_EN if en else SUPERSEDED_SENTENCE_ZH, "", url]
        return "\n".join(lines)

    subject = (f"[MOTRES] {die} {config}: revision {n} - action required: acknowledge / "
               f"版本 {n} - 需要确认")
    body = part("en") + "\n\n" + "-" * 40 + "\n\n" + part("zh")
    return {"number": n, "subject": subject, "body": body, "link": url, "recipients": recipients(production),
            "files": {"new": new_rows, "superseded": old_rows}}


def preview(die: str, config: str, production: dict, revision: dict) -> Dict[str, Any]:
    a = build(die, config, production, revision)
    a["will_send"] = auth_email.smtp_configured()
    return a


def _thread_target(die: str, config: str, revision: dict) -> Optional[str]:
    ids = {str(c.get("part_id")) for c in revision.get("changes") or [] if isinstance(c, dict) and c.get("part_id")}
    return next(iter(ids)) if len(ids) == 1 else None


def _post_discussion(die: str, config: str, author: str, alert: dict, revision: dict) -> Dict[str, Any]:
    """The notice in the Discussion: the part's thread when the changes name exactly one part, else General.  Both languages, confirmed
    (the text is ours, not a machine translation)."""
    part_id = _thread_target(die, config, revision)
    n = alert["number"]
    en = f"Revision {n} was published - action required: acknowledge it in the Changes tab. {alert['link']}"
    zh = f"版本 {n} 已发布 - 需要操作：请在“变更”页确认。{alert['link']}"
    bil = models.normalize_bilingual({"en": en, "zh": zh, "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}})
    msg = models.normalize_message({"author_email": author, "src_lang": "en", "text": bil})
    made: Dict[str, Any] = {}

    def mut(d):
        rows = d.setdefault("threads", [])
        thread = next((t for t in rows if isinstance(t, dict) and (t.get("part_id") or None) == (part_id or None)), None)
        if thread is None:
            thread = models.normalize_thread({"part_id": part_id})
            rows.append(thread)
        thread.setdefault("messages", []).append(msg)
        made["thread"] = thread["id"]

    store.mutate_file(die, config, "threads.json", mut)
    return {"thread_id": made.get("thread"), "message_id": msg["id"], "part_id": part_id}


def deliver(die: str, config: str, production: dict, revision: dict, author: str) -> Dict[str, Any]:
    """Send the alert for a PUBLISHED revision: mail to every member, the Discussion message; returns the record stored on the revision."""
    a = build(die, config, production, revision)
    rows = []
    for r in a["recipients"]:
        try:
            status = SEND(r["email"], a["subject"], a["body"])
        except Exception as exc:                                   # noqa: BLE001 - one bad address must not stop the others
            log.warning("revision alert to %s failed: %s", r["email"], type(exc).__name__)
            status = "failed"
        rows.append({"email": r["email"], "role": r["role"], "mail": status})
    try:
        disc = _post_discussion(die, config, author, a, revision)
    except Exception as exc:                                       # noqa: BLE001
        log.warning("revision alert: Discussion message failed: %s", exc)
        disc = {"thread_id": None, "message_id": None, "part_id": None}
    return {"sent_at": models._now(), "subject": a["subject"], "recipients": rows, **disc}
