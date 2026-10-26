"""One-time announcement mailing to registered accounts (the launch e-mail) and its send log.

The tool is ``scripts/send_announcement.py``; this module holds everything testable:

* templates: ``config/mail/<campaign>.<lang>.md`` (``MAIL_TEMPLATE_DIR`` overrides) - first line ``Subject: ...``, then Markdown
  (headings, bullets, **bold**, links); a line ``=> [Label](url)`` is a button; text after a line ``---`` is the small footer;
  ``{{hello}} {{email}} {{open_url}} {{prefs_url}} {{unsubscribe_url}} {{privacy_url}}`` are filled per user.  Plain text plus
  simple table HTML with inline styles, no external images, fonts or trackers (renders in QQ / 163 / Gmail alike).
* audience: verified, enabled accounts that did not unsubscribe (communication preferences or the legacy newsletter).
* the send log (JSONL, one line per attempt) that makes a campaign idempotent: an account with a ``sent`` line is never mailed
  again for the same campaign id, and one with an unresolved ``attempt`` line (a crash between SMTP and the log) is skipped, not
  retried - a duplicate is worse than a miss.

Dry-run is the default everywhere; nothing here sends unless the caller passes ``send=True``.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Callable, Iterable, Optional

from motor_ai_sim import comm_prefs as CP

log = logging.getLogger(__name__)

DEFAULT_CAMPAIGN = "launch_2026-10-19"
DEFAULT_BASE_URL = "https://aerostator.com"
OPEN_URL = {"en": "https://aerostator.com", "zh-CN": "https://aerostator.com/zh-cn/"}
PRIVACY_URL = "https://emotres.com/privacy-policy"
BRAND = "AeroStator"
COMPANY_FOOTER = "MOTRES d.o.o., Kotnikova 34, 1000 Ljubljana, Slovenia"
_CAMPAIGN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
KNOWN_VARS = ("hello", "email", "open_url", "prefs_url", "unsubscribe_url", "privacy_url")
#: Stop a real run after this many failures in a row (a dead SMTP login must not be hammered).
MAX_CONSECUTIVE_FAILURES = 5


# -- templates ---------------------------------------------------------------------------

def template_dir() -> Path:
    env = (os.environ.get("MAIL_TEMPLATE_DIR") or "").strip()
    return Path(env) if env else Path(__file__).resolve().parents[2] / "config" / "mail"


def check_campaign(campaign: str) -> str:
    if not _CAMPAIGN_RE.match(campaign or ""):
        raise ValueError("campaign id must be letters, digits, '.', '_' or '-' (max 64)")
    return campaign


def load_template(campaign: str, lang: str) -> tuple[str, str, str]:
    """-> (subject, body markdown, footer markdown).  Falls back to English when the language file is missing."""
    check_campaign(campaign)
    lang = CP.norm_lang(lang)
    p = template_dir() / f"{campaign}.{lang}.md"
    if not p.is_file():
        p = template_dir() / f"{campaign}.en.md"
    raw = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    first, _, rest = raw.partition("\n")
    if not first.lower().startswith("subject:"):
        raise ValueError(f"{p.name}: the first line must be 'Subject: ...'")
    subject = first.split(":", 1)[1].strip()
    body, sep, footer = rest.strip("\n").partition("\n---\n")
    return subject, body.strip(), footer.strip() if sep else ""


def _fill(md: str, values: dict) -> str:
    def sub(m):
        k = m.group(1)
        if k not in KNOWN_VARS:
            raise ValueError("unknown template placeholder {{%s}}" % k)
        return values[k]
    return _PLACEHOLDER_RE.sub(sub, md)


# -- Markdown -> text / HTML (a small, safe subset) ----------------------------------------------

_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BUTTON_RE = re.compile(r"^=>\s*\[([^\]]+)\]\((https?://[^)\s]+)\)\s*$")


def _inline(s: str) -> str:
    s = html.escape(s, quote=True)
    s = _LINK_RE.sub(lambda m: f'<a href="{m.group(2)}" style="color:#2563eb;text-decoration:underline">{m.group(1)}</a>', s)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)


def _to_text(md: str) -> str:
    out = []
    for line in md.split("\n"):
        b = _BUTTON_RE.match(line.strip())
        if b:
            out.append(f"{b.group(1)}: {b.group(2)}")
            continue
        line = re.sub(r"^#{1,3}\s+", "", line)
        line = _LINK_RE.sub(lambda m: f"{m.group(1)} ({m.group(2)})", line)
        out.append(re.sub(r"\*\*(.+?)\*\*", r"\1", line))
    return "\n".join(out)


def _to_html(md: str, size: str = "15px") -> str:
    out: list[str] = []
    para: list[str] = []
    in_list = False

    def flush():
        nonlocal in_list
        if para:
            out.append(f'<p style="margin:0 0 14px;font-size:{size};line-height:1.55">' + "<br>".join(_inline(x) for x in para) + "</p>")
            para.clear()
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in md.split("\n"):
        line = raw.rstrip()
        bm = _BUTTON_RE.match(line.strip())
        hm = re.match(r"^(#{1,3})\s+(.*)$", line)
        lm = re.match(r"^\s*[-*]\s+(.*)$", line)
        if not line.strip():
            flush()
        elif bm:
            flush()
            out.append('<table role="presentation" cellpadding="0" cellspacing="0" style="margin:6px 0 20px"><tr>'
                       '<td bgcolor="#2563eb" style="background-color:#2563eb;border-radius:6px">'
                       f'<a href="{html.escape(bm.group(2), quote=True)}" style="display:inline-block;padding:12px 24px;'
                       'font-size:15px;font-weight:bold;color:#ffffff;text-decoration:none">'
                       f'{html.escape(bm.group(1))}</a></td></tr></table>')
        elif hm:
            flush()
            out.append(f'<h2 style="margin:22px 0 8px;font-size:17px;color:#0f172a">{_inline(hm.group(2))}</h2>')
        elif lm:
            if para:
                flush()
            if not in_list:
                out.append('<ul style="margin:0 0 14px;padding-left:22px;font-size:%s;line-height:1.55">' % size)
                in_list = True
            out.append(f'<li style="margin:0 0 6px">{_inline(lm.group(1))}</li>')
        else:
            if in_list:
                flush()
            para.append(line)
    flush()
    return "\n".join(out)


@dataclass
class Rendered:
    subject: str
    text: str
    html: str
    lang: str


def hello(name: str, lang: str) -> str:
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:80]
    if CP.norm_lang(lang) == "zh-CN":
        return f"{name}，您好" if name else "您好，"
    return f"Hello {name}," if name else "Hello,"


def render(campaign: str, lang: str, *, email: str, name: str, base_url: str = DEFAULT_BASE_URL) -> Rendered:
    """The subject, plain text and HTML of one e-mail for one account (personal preference and unsubscribe links inside)."""
    lang = CP.norm_lang(lang)
    subject, body, footer = load_template(campaign, lang)
    values = {"hello": hello(name, lang), "email": email, "open_url": OPEN_URL[lang],
              "prefs_url": CP.prefs_url(base_url, email, lang),
              "unsubscribe_url": CP.prefs_url(base_url, email, lang, action="unsubscribe"), "privacy_url": PRIVACY_URL}
    body, footer = _fill(body, values), _fill(footer, values)
    text = f"{_to_text(body)}\n\n--\n{_to_text(footer)}\n" if footer else _to_text(body) + "\n"
    html_doc = f"""<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>{html.escape(subject)}</title></head>
<body style="margin:0;padding:0;background:#f4f5f7;color:#1f2937;font-family:Arial,Helvetica,'Microsoft YaHei','PingFang SC',sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#f4f5f7"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" bgcolor="#ffffff" style="max-width:600px;width:100%;background:#ffffff;border-radius:8px">
<tr><td bgcolor="#0f172a" style="background:#0f172a;color:#ffffff;padding:16px 24px;font-size:18px;font-weight:bold;border-radius:8px 8px 0 0">{BRAND}</td></tr>
<tr><td style="padding:24px">
{_to_html(body)}
</td></tr>
<tr><td style="padding:16px 24px;border-top:1px solid #e5e7eb;color:#6b7280">
{_to_html(footer, "12px")}
</td></tr></table></td></tr></table></body></html>
"""
    return Rendered(subject, text, html_doc, lang)


def build_message(campaign: str, lang: str, *, email: str, name: str, base_url: str = DEFAULT_BASE_URL,
                  api_base: Optional[str] = None, sender: str = "", subject_prefix: str = "") -> EmailMessage:
    r = render(campaign, lang, email=email, name=name, base_url=base_url)
    sender = sender or _mail_from()
    # max_line_length 998 (RFC 5322 limit): the default 78 would turn the long List-Unsubscribe URL into an unreadable
    # RFC 2047 encoded word, which mailbox providers do not honour for one-click unsubscribe.
    msg = EmailMessage(policy=policy.default.clone(max_line_length=998))
    msg["From"] = sender
    msg["To"] = email
    msg["Subject"] = subject_prefix + r.subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=(sender.split("@")[-1].strip("> ") or "aerostator.com"))
    msg["List-Unsubscribe"] = f"<{CP.one_click_url(api_base or base_url, email)}>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg["List-Id"] = f"{BRAND} announcements <announce.{sender.split('@')[-1].strip('> ') or 'aerostator.com'}>"
    msg.set_content(r.text)
    msg.add_alternative(r.html, subtype="html")
    return msg


def _mail_from() -> str:
    from motor_ai_sim import auth_email as E
    return E.mail_from() or "noreply@aerostator.com"


# -- audience ---------------------------------------------------------------------------------

@dataclass
class Recipient:
    email: str
    name: str
    lang: str


def _valid_email(e: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s.]+", e or "")) and len(e) <= 254


def stored_locales() -> dict:
    """{email: UI locale} from the user-preference store (best effort)."""
    try:
        from motor_ai_sim.routes.account import _prefs_load
        return {str(k).strip().lower(): (v or {}).get("locale") for k, v in _prefs_load().items() if isinstance(v, dict)}
    except Exception:                                        # noqa: BLE001
        return {}


def load_users(users_file: Optional[Path] = None) -> dict:
    if users_file is None:
        from motor_ai_sim import users as U
        return U._load()                                     # raises when the registry is unreadable: never mail from a guess
    d = json.loads(Path(users_file).read_text(encoding="utf-8"))
    return d if isinstance(d, dict) else {}


def collect_audience(users: dict, *, locales: Optional[dict] = None, default_lang: str = "en") -> tuple[list[Recipient], list[tuple[str, str]]]:
    """-> (recipients sorted by address, [(address, reason it is skipped)])."""
    from motor_ai_sim import newsletter as N
    from motor_ai_sim import users as U
    locales = stored_locales() if locales is None else locales
    ok: list[Recipient] = []
    skipped: list[tuple[str, str]] = []
    for email in sorted(users):
        rec = users[email]
        e = str(email).strip().lower()
        if not isinstance(rec, dict):
            continue
        if not _valid_email(e):
            skipped.append((e, "invalid_address"))
        elif rec.get("disabled"):
            skipped.append((e, "disabled"))
        elif not U.is_verified(rec):
            skipped.append((e, "unverified"))
        elif CP.is_unsubscribed_all(e):
            skipped.append((e, "unsubscribed"))
        elif N.status(e)["status"] == "unsubscribed":
            skipped.append((e, "newsletter_unsubscribed"))
        else:
            lang = locales.get(e) or CP.last_lang(e) or default_lang
            ok.append(Recipient(e, str(rec.get("name") or ""), CP.norm_lang(lang)))
    return ok, skipped


# -- the send log -------------------------------------------------------------------------------

def log_dir() -> Path:
    env = (os.environ.get("MAILING_LOG_DIR") or "").strip()
    return Path(env) if env else CP._ROOT / "mailing"


def log_path(campaign: str) -> Path:
    return log_dir() / f"{check_campaign(campaign)}.sendlog.jsonl"


def read_log(path: Path) -> list[dict]:
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    rows.append(r)
    except OSError:
        return []
    return rows


def _append_log(path: Path, row: dict) -> None:
    from motor_ai_sim.private_files import ensure_private_dir, open_private
    ensure_private_dir(path.parent)
    with open_private(path, "a") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def log_state(rows: Iterable[dict], campaign: str) -> dict:
    """{address: last status} for a campaign: ``sent`` | ``attempt`` (unresolved) | ``failed``."""
    last: dict = {}
    for r in rows:
        if r.get("campaign") == campaign and r.get("status") in ("attempt", "sent", "failed"):
            last[str(r.get("user"))] = r["status"]
    return last


def last_campaign_status() -> Optional[dict]:
    """The most recently written campaign log, summarised for the Admin card; None when nothing was ever sent."""
    try:
        files = sorted(log_dir().glob("*.sendlog.jsonl"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
    if not files:
        return None
    rows = read_log(files[-1])
    campaign = files[-1].name[: -len(".sendlog.jsonl")]
    st = log_state(rows, campaign)
    stamps = [r.get("ts") for r in rows if r.get("ts")]
    return {"campaign": campaign, "sent": sum(1 for v in st.values() if v == "sent"),
            "failed": sum(1 for v in st.values() if v == "failed"),
            "unresolved": sum(1 for v in st.values() if v == "attempt"),
            "first": min(stamps) if stamps else None, "last": max(stamps) if stamps else None}


# -- planning and running -------------------------------------------------------------------------

@dataclass
class Plan:
    campaign: str
    to_send: list = field(default_factory=list)
    skipped: list = field(default_factory=list)          # [(address, reason)]


def plan(campaign: str, recipients: list, skipped: list, rows: list, *, retry_failed: bool = False, limit: Optional[int] = None) -> Plan:
    """Who this run would mail: the audience minus everyone the log says is done or unresolved."""
    check_campaign(campaign)
    state = log_state(rows, campaign)
    p = Plan(campaign, [], list(skipped))
    for r in recipients:
        s = state.get(r.email)
        if s == "sent":
            p.skipped.append((r.email, "already_sent"))
        elif s == "attempt":
            p.skipped.append((r.email, "unresolved_attempt_check_manually"))
        elif s == "failed" and not retry_failed:
            p.skipped.append((r.email, "failed_before_use_retry_failed"))
        else:
            p.to_send.append(r)
    if limit is not None and limit >= 0:
        for r in p.to_send[limit:]:
            p.skipped.append((r.email, "over_limit"))
        p.to_send = p.to_send[:limit]
    return p


def _safe_name(email: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", email)


def write_eml(p: Plan, out_dir: Path, *, base_url: str, api_base: Optional[str], sender: str) -> list[Path]:
    """DRY RUN: one .eml per recipient.  Sends nothing, touches no log."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, r in enumerate(p.to_send, 1):
        msg = build_message(p.campaign, r.lang, email=r.email, name=r.name, base_url=base_url, api_base=api_base, sender=sender)
        f = out_dir / f"{i:03d}_{_safe_name(r.email)}.{r.lang}.eml"
        f.write_bytes(msg.as_bytes())
        paths.append(f)
    return paths


class SendAborted(RuntimeError):
    pass


def send(p: Plan, *, deliver: Callable[[EmailMessage], None], log_file: Path, base_url: str, api_base: Optional[str], sender: str,
         delay_s: float = 3.0, sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.time,
         build: Optional[Callable[[Recipient], EmailMessage]] = None) -> dict:
    """REAL SENDING.  One message at a time, ``delay_s`` apart; an ``attempt`` line is flushed to disk BEFORE the SMTP call and
    a ``sent`` / ``failed`` line after it.  Returns {sent, failed, aborted}.  The caller has checked --confirm.
    ``build`` (digest approval, 2026-10-26) makes the message of one recipient instead of the template campaign."""
    lock = Path(str(log_file) + ".lock")
    from motor_ai_sim.private_files import ensure_private_dir
    ensure_private_dir(log_file.parent)
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SendAborted(f"another send of this campaign is running (or crashed): remove {lock} once you are sure it is not running")
    os.close(fd)
    sent = failed = streak = 0
    aborted = ""
    try:
        for i, r in enumerate(p.to_send):
            if i:
                sleep(delay_s)
            msg = build(r) if build is not None else build_message(p.campaign, r.lang, email=r.email, name=r.name, base_url=base_url,
                                                                    api_base=api_base, sender=sender)
            row = {"campaign": p.campaign, "user": r.email, "message_id": msg["Message-ID"], "lang": r.lang}
            _append_log(log_file, dict(row, status="attempt", ts=now()))
            try:
                deliver(msg)
            except Exception as e:                           # noqa: BLE001
                failed += 1
                streak += 1
                _append_log(log_file, dict(row, status="failed", ts=now(), error=f"{type(e).__name__}: {str(e)[:160]}"))
                if streak >= MAX_CONSECUTIVE_FAILURES:
                    aborted = f"{streak} failures in a row, stopped"
                    break
                continue
            streak = 0
            sent += 1
            _append_log(log_file, dict(row, status="sent", ts=now()))
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass
    return {"sent": sent, "failed": failed, "aborted": aborted}
