"""Working groups, recipients, the ONE text of a digest and the run that makes drafts.

Owner rules 2026-10-26 (they override the first brief):

* **Nothing is sent from here.**  :func:`run` only writes DRAFTS that wait in Admin; the one function that hands mail to SMTP is
  :func:`motor_ai_sim.digest.approval.send_draft`, reached only from the admin "Send" route.  There is no automatic mode.
* **One text per group per period.**  The draft holds a single subject and body for the whole group.  The body keeps only the
  items EVERY recipient may see (lowest common access: no costs unless shared with the factory, no internal notes, no
  engineers' trial variants).  A group with Chinese-speaking members (the factory) gets both languages in that one e-mail,
  English first.  Links are the same for everybody; the app enforces each person's own access after sign-in.
* The only per-person part is the footer (their own unsubscribe and preferences links) - the text above it is identical.
* A period with nothing to report makes NO draft.  One draft per (group, period start): running twice, or after a send, never makes
  a second digest for the same period.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import time
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any, Callable, Optional

from motor_ai_sim import auth_email as E
from motor_ai_sim import comm_prefs as CP
from motor_ai_sim import return_path as RP
from motor_ai_sim.digest import collect as C
from motor_ai_sim.digest import store as DS

MAX_ITEMS = 20
DAY = 86400.0
#: how long a group's period must be before the next digest (a little under the nominal length: the timer drifts)
GATE_S = {"daily": 20 * 3600.0, "weekly": 6.5 * DAY}
WINDOW_S = {"daily": DAY, "weekly": 7 * DAY}


# -- groups ----------------------------------------------------------------------------------------------------------------------

@dataclass
class Group:
    scope: str
    kind: str                       # "project" | "production"
    record: dict
    title_en: str
    title_zh: str
    members: list = field(default_factory=list)       # [(email, role)]
    active: bool = True
    path: Optional[str] = None      # the app path of the group's own page

    def caps(self, role: str) -> frozenset:
        """What a member of this role may see (see collect.INTERNAL / COSTS)."""
        if self.kind == "project":
            return frozenset({C.INTERNAL}) if role in ("owner", "engineer") else frozenset()
        if role in ("owner", "engineer"):
            return frozenset({C.INTERNAL, C.COSTS})
        return frozenset({C.COSTS}) if self.record.get("share_costs_with_oem") else frozenset()


_UNSAFE_RE = re.compile("[" + chr(0) + "-" + chr(31) + chr(127) + chr(0x2028) + chr(0x2029) + "]+")


def safe_text(value: Any, fallback: str = "", limit: int = 120) -> str:
    """A title that is safe in an e-mail header: control characters and line breaks (header injection) become one space."""
    s = _UNSAFE_RE.sub(" ", str(value or "")).strip()
    s = re.sub(r"\s+", " ", s)[:limit].strip()
    return s or fallback


def header_safe(value: str) -> bool:
    return bool(value) and not _UNSAFE_RE.search(value)


def _members(record: dict) -> list:
    out = []
    for m in record.get("members") or []:
        if isinstance(m, dict) and str(m.get("email") or "").strip():
            out.append((str(m["email"]).strip().lower(), str(m.get("role") or "")))
    return out


def project_group(project: dict) -> Group:
    pid = str(project.get("id") or "")
    title = safe_text(project.get("title"), pid)
    return Group(DS.project_scope(pid), "project", project, title, title, _members(project),
                 str(project.get("status") or "spec") != "closed", RP.project_path(pid))


def production_group(production: dict) -> Group:
    die, config = str(production.get("die") or ""), str(production.get("config") or "")
    t_en, t_zh = C.bilingual(production.get("title"))
    t_en = safe_text(t_en, safe_text(f"{die} / {config}", "production"))
    t_zh = safe_text(t_zh, t_en)
    return Group(DS.production_scope(die, config), "production", production, t_en, t_zh, _members(production),
                 str(production.get("status") or "active") == "active", RP.production_path(die, config))


def list_groups() -> list[Group]:
    """Every development project and every production."""
    out: list[Group] = []
    try:
        from motor_ai_sim.development import store as PS
        out += [project_group(p) for p in PS.list_projects()]
    except Exception:                                        # noqa: BLE001
        pass
    try:
        from motor_ai_sim.production import store as PR
        root = PR.production_root()
        if root.is_dir():
            for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
                for c in sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith(".")):
                    rec = PR._read_json(c / "production.json", default={}) if (c / "production.json").is_file() else {}
                    if isinstance(rec, dict) and rec.get("die"):
                        out.append(production_group(rec))
    except Exception:                                        # noqa: BLE001
        pass
    return out


def get_group(scope: str) -> Optional[Group]:
    parsed = DS.parse_scope(scope)
    if parsed is None:
        return None
    kind, parts = parsed
    if kind == "project":
        from motor_ai_sim.development import store as PS
        rec = PS.get_project(parts[0])
        return project_group(rec) if rec else None
    from motor_ai_sim.production import access as PA
    rec = PA.load_production(parts[0], parts[1])
    return production_group(rec) if rec else None


# -- recipients ---------------------------------------------------------------------------------------------------------------------

def _locales() -> dict:
    from motor_ai_sim import mailing
    return mailing.stored_locales()


def resolve_recipients(group: Group, settings: dict, *, locales: Optional[dict] = None) -> tuple[list[dict], list[dict]]:
    """``(recipients, skipped)``.  A recipient is a verified, enabled member who did not leave this digest, did not unsubscribe from
    all e-mail and (factory role) has signed the NDA that opens the production.  ``recipients: chosen`` narrows to the listed
    members."""
    from motor_ai_sim import users as U
    locales = _locales() if locales is None else locales
    chosen = set(settings.get("chosen") or []) if settings.get("recipients") == "chosen" else None
    optouts = set(settings.get("optouts") or [])
    ok: list[dict] = []
    skipped: list[dict] = []
    seen: set = set()
    for email, role in group.members:
        if email in seen:
            continue
        seen.add(email)
        rec = U.get_user(email)
        reason = ""
        if chosen is not None and email not in chosen:
            reason = "not_chosen"
        elif email in optouts:
            reason = "left_this_digest"
        elif not rec:
            reason = "no_account"
        elif rec.get("disabled"):
            reason = "disabled"
        elif not U.is_verified(rec):
            reason = "unverified"
        elif CP.is_unsubscribed_all(email):
            reason = "unsubscribed_all"
        elif group.kind == "production" and role == "oem" and not _has_nda(email, role, rec):
            reason = "nda_missing"
        if reason:
            skipped.append({"email": email, "reason": reason})
            continue
        lang = "zh-CN" if role == "oem" and group.kind == "production" else CP.norm_lang(locales.get(email) or CP.last_lang(email) or "en")
        ok.append({"email": email, "name": str((rec or {}).get("name") or ""), "role": role, "lang": lang})
    return ok, skipped


def _has_nda(email: str, role: str, rec: Optional[dict]) -> bool:
    """The production page itself is closed to a factory member without a signed NDA, so is its digest."""
    try:
        from motor_ai_sim import model3d as M3
        return bool(M3.can_download({"id": email, "is_admin": False, "role": role}, rec))
    except Exception:                                        # noqa: BLE001 - when in doubt, do not mail
        return False


def common_caps(group: Group, recipients: list[dict]) -> frozenset:
    """The capabilities EVERY recipient has - the text may contain only what these allow."""
    caps: Optional[frozenset] = None
    for r in recipients:
        c = group.caps(r["role"])
        caps = c if caps is None else caps & c
    return caps if caps is not None else frozenset()


# -- the text -----------------------------------------------------------------------------------------------------------------------

def _day(ts: float) -> str:
    return DS.iso(ts)[:10]


def _url(path: Optional[str]) -> str:
    return E.app_url() + path if path else E.app_url()


def _subject(group: Group, end: float, bilingual: bool) -> str:
    s = f"{group.title_en} - what changed {_day(end)}"
    return s + f" / {group.title_zh} - 更新摘要 {_day(end)}" if bilingual else s


def compose(group: Group, items: list, more: int, start: float, end: float, bilingual: bool) -> tuple[str, str]:
    """``(text, html fragment)`` of the one body.  Plain lines, a link per line, no images, no attachments."""
    t_lines: list[str] = []
    h_parts: list[str] = []
    langs = [("en", group.title_en, f"What changed since {_day(start)}", "Open", f"and {more} more", "en")]
    if bilingual:
        langs.append(("zh", group.title_zh, f"{_day(start)} 以来的更新", "打开", f"另有 {more} 条", "zh"))
    for i, (lang, title, head, open_label, more_label, attr) in enumerate(langs):
        if i:
            t_lines += ["", "-" * 40, ""]
            h_parts.append('<hr style="border:0;border-top:1px solid #e5e7eb;margin:18px 0">')
        t_lines += [title, head + ":", ""]
        h_parts.append(f'<h2 style="margin:0 0 4px;font-size:17px;color:#0f172a" lang="{attr}">{html.escape(title)}</h2>'
                       f'<p style="margin:0 0 12px;font-size:14px;color:#475569" lang="{attr}">{html.escape(head)}:</p>'
                       '<ul style="margin:0 0 12px;padding-left:20px;font-size:15px;line-height:1.5">')
        for it in items:
            line = it.zh if lang == "zh" else it.en
            url = _url(it.link)
            t_lines += [f"- {line}", f"  {url}"]
            h_parts.append(f'<li style="margin:0 0 8px">{html.escape(line)} '
                           f'<a href="{html.escape(url, quote=True)}" style="color:#2563eb;text-decoration:underline">{html.escape(open_label)}</a></li>')
        h_parts.append("</ul>")
        if more:
            url = _url(group.path)
            t_lines += ["", f"{more_label}: {url}"]
            h_parts.append(f'<p style="margin:0 0 12px;font-size:14px" lang="{attr}">'
                           f'<a href="{html.escape(url, quote=True)}" style="color:#2563eb">{html.escape(more_label)}</a></p>')
    return "\n".join(t_lines).rstrip() + "\n", "\n".join(h_parts)


def footer(draft: dict, email: str, *, base: Optional[str] = None) -> tuple[str, str]:
    """The ONLY per-person part: this person's own unsubscribe-from-this-digest and preferences links."""
    from motor_ai_sim import mailing
    base = base or E.app_url()
    scope, title = draft["scope"], draft["title"]
    unsub = unsub_url(email, scope, base)
    # the preferences page opens in the recipient's own language (the text above is the same for everybody)
    lang = next((r.get("lang") for r in draft.get("recipients") or [] if r.get("email") == email), None) or "en"
    prefs = CP.prefs_url(base, email, lang)
    kind_en = "project" if draft["kind"] == "project" else "production"
    kind_zh = "项目" if draft["kind"] == "project" else "生产"
    t = [f"You get this because you are a member of the {kind_en} \"{title}\".",
         f"Stop updates for this {kind_en}: {unsub}", f"All e-mail preferences: {prefs}"]
    h = [f'You get this because you are a member of the {kind_en} "{html.escape(title)}". '
         f'<a href="{html.escape(unsub, quote=True)}" style="color:#6b7280">Stop updates for this {kind_en}</a> · '
         f'<a href="{html.escape(prefs, quote=True)}" style="color:#6b7280">All e-mail preferences</a>']
    if draft.get("bilingual"):
        t += ["", f"您收到此邮件是因为您是{kind_zh}“{draft.get('title_zh') or title}”的成员。",
              f"不再接收此{kind_zh}的更新：{unsub}", f"邮件偏好设置：{prefs}"]
        h.append(f'您收到此邮件是因为您是{kind_zh}“{html.escape(str(draft.get("title_zh") or title))}”的成员。 '
                 f'<a href="{html.escape(unsub, quote=True)}" style="color:#6b7280">不再接收此{kind_zh}的更新</a> · '
                 f'<a href="{html.escape(prefs, quote=True)}" style="color:#6b7280">邮件偏好设置</a>')
    t += ["", mailing.COMPANY_FOOTER]
    h.append(html.escape(mailing.COMPANY_FOOTER))
    return "\n".join(t) + "\n", "<br>".join(h)


def shell(subject: str, body_html: str, footer_html: str, lang: str = "en") -> str:
    from motor_ai_sim import mailing
    return f"""<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>{html.escape(subject)}</title></head>
<body style="margin:0;padding:0;background:#f4f5f7;color:#1f2937;font-family:Arial,Helvetica,'Microsoft YaHei','PingFang SC',sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#f4f5f7"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" bgcolor="#ffffff" style="max-width:600px;width:100%;background:#ffffff;border-radius:8px">
<tr><td bgcolor="#0f172a" style="background:#0f172a;color:#ffffff;padding:16px 24px;font-size:18px;font-weight:bold;border-radius:8px 8px 0 0">{mailing.BRAND}</td></tr>
<tr><td style="padding:24px">
{body_html}
</td></tr>
<tr><td style="padding:16px 24px;border-top:1px solid #e5e7eb;color:#6b7280;font-size:12px;line-height:1.5">
{footer_html}
</td></tr></table></td></tr></table></body></html>
"""


def render_for(draft: dict, email: str, *, base: Optional[str] = None) -> dict:
    """The e-mail exactly as this recipient would get it: ``{subject, text, html}``.  The body is the draft's single text."""
    f_text, f_html = footer(draft, email, base=base)
    return {"subject": draft["subject"], "text": draft["text_body"] + "\n--\n" + f_text,
            "html": shell(draft["subject"], draft["html_body"], f_html, "zh-CN" if draft.get("bilingual") else "en")}


def build_message(draft: dict, email: str, *, base: Optional[str] = None, sender: str = "") -> EmailMessage:
    r = render_for(draft, email, base=base)
    base = base or E.app_url()
    sender = sender or E.mail_from() or "noreply@aerostator.com"
    msg = EmailMessage(policy=policy.default.clone(max_line_length=998))
    msg["From"] = sender
    msg["To"] = email
    msg["Subject"] = r["subject"]
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=(sender.split("@")[-1].strip("> ") or "aerostator.com"))
    msg["List-Unsubscribe"] = f"<{unsub_url(email, draft['scope'], base)}>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg["List-Id"] = f"AeroStator project updates <digest.{sender.split('@')[-1].strip('> ') or 'aerostator.com'}>"
    msg.set_content(r["text"])
    msg.add_alternative(r["html"], subtype="html")      # text + HTML only: no images, no attachments
    return msg


# -- unsubscribe from one digest -----------------------------------------------------------------------------------------------------

def unsub_purpose(scope: str) -> str:
    return "digest:" + scope


def unsub_url(email: str, scope: str, base: str) -> str:
    return (base.rstrip("/") + "/api/digest/unsubscribe/" + DS.scope_token(scope) + "/"
            + CP.make_token(email, purpose=unsub_purpose(scope)))


def unsub_email(scope: str, token: str) -> Optional[str]:
    """The member behind a genuine unsubscribe token of this scope; None otherwise."""
    uid = CP.verify_token(token, unsub_purpose(scope))
    return CP.resolve(uid) if uid else None


# -- what the approval covers --------------------------------------------------------------------------------------------------------

def content_hash(draft: dict) -> str:
    """SHA-256 of exactly what the owner approves: subject, plain text, HTML and the recipient list.

    The preview shows this hash and the send request must carry it back: if the draft changed in between (a refresh, a different
    recipient list) the hashes differ and nothing is sent."""
    blob = json.dumps({"subject": draft.get("subject"), "text_body": draft.get("text_body"), "html_body": draft.get("html_body"),
                       "recipients": sorted(str(r.get("email")) for r in draft.get("recipients") or [])},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def motor_visible(email: str, die: str, config: str) -> bool:
    """May this person open the motor ``die`` / ``config`` in the app?  The catalog rule (``motor_access``) evaluated for the named
    account; a published die ("<die> . by <owner>") is open to every registered account except private configurations.  Any doubt
    or error = no."""
    try:
        from motor_ai_sim import motor_access as MA
        from motor_ai_sim import workspace as WS
        acc = MA.access_for_email(email)
        if acc.get("is_admin"):
            return True
        published = bool(WS.split_published_label(die)[1])
        if not published and not MA.may_see_die(acc, die):
            return False
        return bool(MA.config_visible(acc, die, config, layer="published" if published else "shared"))
    except Exception:                                        # noqa: BLE001 - fail closed
        return False


def motors_visible_to_all(items, emails) -> dict:
    """``{(die, config): bool}`` for every motor the items mention: true only when EVERY listed person may open it."""
    out: dict = {}
    for it in items:
        m = getattr(it, "motor", None)
        if m and tuple(m) not in out:
            out[tuple(m)] = all(motor_visible(e, m[0], m[1]) for e in emails)
    return out


# -- the run: drafts, never mail ---------------------------------------------------------------------------------------------------------

def build_draft(group: Group, settings: dict, items: list, recipients: list[dict], skipped: list[dict],
                start: float, end: float, existing: Optional[dict] = None, *, revision: int = 0) -> Optional[dict]:
    """The draft for one period, or None when there is nothing every recipient may see (role-wise AND motor-wise)."""
    if not recipients:
        return None
    caps = common_caps(group, recipients)
    seen_motor = motors_visible_to_all(items, [r["email"] for r in recipients])
    visible = sorted((i for i in items if i.requires <= caps and (not i.motor or seen_motor.get(tuple(i.motor), False))),
                     key=lambda i: i.ts, reverse=True)
    if not visible:
        return None
    shown, more = visible[:MAX_ITEMS], max(0, len(visible) - MAX_ITEMS)
    bilingual = any(r["lang"] == "zh-CN" for r in recipients)
    text, body_html = compose(group, shown, more, start, end, bilingual)
    now = DS.iso()
    subject = _subject(group, end, bilingual)
    if not header_safe(subject):
        return None                      # cannot happen after safe_text; never build a header from an unsafe value
    draft = {
        "id": DS.draft_id_for(group.scope, DS.iso(start), revision), "revision": revision, "scope": group.scope, "kind": group.kind,
        "title": group.title_en, "title_zh": group.title_zh, "status": "pending",
        "period": {"start": DS.iso(start), "end": DS.iso(end)},
        "created_at": (existing or {}).get("created_at") or now, "updated_at": now,
        "bilingual": bilingual, "caps": sorted(caps), "frequency": settings.get("frequency", "daily"),
        "subject": subject, "text_body": text, "html_body": body_html,
        "items": [{"kind": i.kind, "ts": DS.iso(i.ts), "en": i.en, "zh": i.zh, "link": i.link,
                   "requires": sorted(i.requires), **({"motor": list(i.motor)} if i.motor else {})} for i in shown],
        "more": more, "recipients": recipients, "skipped": skipped,
    }
    draft["content_hash"] = content_hash(draft)
    return draft


def run(now: Optional[float] = None, *, scopes: Optional[list] = None, write: bool = True,
        results: Callable[[str, str], dict] = C.stored_results) -> list[dict]:
    """Make the due drafts.  Returns one report row per group looked at.  NEVER sends anything.

    A pending draft nobody has opened is refreshed in place.  A draft the owner already opened or previewed is NEVER rewritten
    (what he read is what he may approve): a refresh with different content makes a new draft (next revision) and marks the old
    one ``superseded``.  A draft whose send partly failed (``partial``) is left alone until the owner finishes or discards it.

    ``write=False`` (dry run) computes the same rows and touches no file."""
    now = time.time() if now is None else now
    report: list[dict] = []
    for group in list_groups():
        if scopes is not None and group.scope not in scopes:
            continue
        settings = DS.get_settings(group.scope)
        row = {"scope": group.scope, "title": group.title_en}
        report.append(row)
        if settings["mode"] == "off":
            row["action"], row["reason"] = "skipped", "mode_off"
            continue
        if not group.active:
            row["action"], row["reason"] = "skipped", "not_active"
            continue
        freq = settings["frequency"]
        state = DS.get_state(group.scope)
        current = DS.load_draft(state["draft_id"]) if state.get("draft_id") else None
        if current is not None and current.get("status") == "partial":
            row["action"], row["reason"] = "skipped", "partial_send_open"
            continue
        pending = current if current is not None and current.get("status") == "pending" else None
        cursor = DS.parse_ts(state.get("cursor"))
        if pending is not None:
            start = DS.parse_ts(pending["period"]["start"]) or now - WINDOW_S[freq]
        else:
            start = cursor if cursor is not None else now - WINDOW_S[freq]
            if now - start < GATE_S[freq]:
                row["action"], row["reason"] = "skipped", "not_due"
                continue
        if group.kind == "project":
            from motor_ai_sim.development import store as PS
            record = PS.get_project(group.record.get("id")) or group.record
            items = C.collect_project(record, start, now, results=results)
        else:
            items = C.collect_production(group.record, start, now)
        recipients, skipped = resolve_recipients(group, settings)
        revision = int((pending or {}).get("revision") or 0)
        draft = build_draft(group, settings, items, recipients, skipped, start, now, existing=pending, revision=revision)
        if draft is None:
            row["action"], row["reason"] = "skipped", "no_activity" if not items else "nothing_for_everyone" if recipients else "no_recipients"
            if pending is None and write:
                # a quiet period: keep a trailing window so the next digest never reports "since three months ago"
                DS.set_state(group.scope, cursor=DS.iso(max(cursor or 0.0, now - WINDOW_S[freq])))
            continue
        if pending is None:
            done = DS.load_draft(draft["id"])
            if done is not None and done.get("status") != "pending":
                row["action"], row["reason"] = "skipped", "period_already_" + str(done.get("status"))
                continue
        row.update(draft_id=draft["id"], items=len(draft["items"]) + draft["more"], recipients=len(recipients))
        if pending is not None and pending.get("content_hash") == draft["content_hash"] and content_hash(pending) == draft["content_hash"]:
            row["action"] = "unchanged"                  # nothing new since the last run: the text is left alone
            if write:
                DS.update_skipped(pending["id"], skipped)
            continue
        if pending is None:
            row["action"] = "drafted"
            if write:
                DS.save_draft(draft)
                DS.set_state(group.scope, draft_id=draft["id"])
                DS.append_log({"event": "drafted", "scope": group.scope, "draft_id": draft["id"], "items": row["items"],
                               "recipients": len(recipients)})
        elif not pending.get("opened"):
            row["action"] = "refreshed"
            if write:
                if not DS.rewrite_unopened(draft):                 # opened a moment ago: fall through to superseding
                    row["action"] = _supersede(group, settings, items, recipients, skipped, start, now, pending, row)
                else:
                    DS.set_state(group.scope, draft_id=draft["id"])
        else:
            row["action"] = "superseded"
            if write:
                row["action"] = _supersede(group, settings, items, recipients, skipped, start, now, pending, row)
    return report


def _supersede(group: Group, settings: dict, items: list, recipients: list, skipped: list, start: float, end: float,
               old: dict, row: dict) -> str:
    """A new draft (next revision) replaces an opened one; the old text stays on disk, status ``superseded``."""
    nxt = int(old.get("revision") or 0) + 1
    new = build_draft(group, settings, items, recipients, skipped, start, end, existing=old, revision=nxt)
    if new is None:
        return "skipped"
    new["supersedes"] = old["id"]
    DS.save_draft(new)
    old = dict(old, status="superseded", superseded_by=new["id"], updated_at=DS.iso())
    DS.save_draft(old)
    DS.set_state(group.scope, draft_id=new["id"])
    DS.append_log({"event": "superseded", "scope": group.scope, "draft_id": old["id"], "by_draft": new["id"]})
    row["draft_id"] = new["id"]
    return "superseded"
