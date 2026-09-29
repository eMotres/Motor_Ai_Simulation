"""Newsletter (marketing e-mail with consent) + in-app product notices.

GDPR / ePrivacy shape (MOTRES d.o.o. is an EU controller):

* Consent is opt-in only: the sign-up checkbox is unchecked by default, and an
  address is a subscriber only after it clicked a signed confirmation link
  (double opt-in).  Existing accounts are never subscribed by us.
* Every consent event is recorded per address: status, timestamp, source
  (signup / google / settings), the version of the consent text shown, and
  an HMAC of the client IP (never the IP itself).
* Unsubscribing is instant: a signed one-click link in every mail, the RFC 8058
  ``List-Unsubscribe`` / ``List-Unsubscribe-Post`` headers, and the account
  settings toggle.  Each is logged in the subscriber's history.
* Campaigns go ONLY to confirmed subscribers, re-checked right before every
  single send (an unsubscribe during a running campaign is honoured).

Sending is a throttled background queue persisted in the store file, so a
restart resumes where it stopped.  Limits come from the environment::

    NEWSLETTER_RATE_PER_MIN=20     # messages per rolling minute
    NEWSLETTER_DAILY_CAP=1500      # per rolling 24 h (Workspace: ~2000/day
                                   # TOTAL incl. transactional mail)
    NEWSLETTER_WORKER=0            # disable the worker in this process
    PUBLIC_API_URL=https://...     # base of the List-Unsubscribe URL
                                   # (default PUBLIC_APP_URL; /api must be
                                   # proxied there)

Run the API with ONE worker process: the queue thread lives in-process.
A message whose send was interrupted by a crash is marked failed
("interrupted"), never retried blindly — a duplicate is worse than a miss.

In-app notices are product notices (maintenance, new feature), not
marketing, so they do not depend on e-mail consent; each user has read state.

Nothing here logs a message body or a token.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import os
import re
import secrets
import smtplib
import threading
import time
import uuid
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Callable, Optional

import jwt

from motor_ai_sim import auth_email as E
from motor_ai_sim import users as U
from motor_ai_sim.config import DEFAULT_CONFIG_PATH

log = logging.getLogger(__name__)

_STORE_FILE = Path(DEFAULT_CONFIG_PATH).parent / "newsletter.json"
_LOCK = threading.RLock()

#: Shown next to the checkbox; bump the version whenever the wording changes.
CONSENT_TEXT_VERSION = "2026-09-29.1"
CONSENT_TEXT = ("Product news and updates, at most 2 per month; "
                "unsubscribe in one click.")
SOURCES = ("signup", "google", "settings", "admin")

CONFIRM_TTL_S = 7 * 24 * 3600
UNSUB_TTL_S = 400 * 24 * 3600
_ISS = "motor-ai-sim-newsletter"

COMPANY_FOOTER = "MOTRES d.o.o., Kotnikova 34, 1000 Ljubljana, Slovenia"

#: confirm / unsubscribe endpoints, per IP
LINK_IP = E.Limiter(max_hits=30, window_s=600.0, lock_s=600.0)


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


def rate_per_min() -> int:
    return _env_int("NEWSLETTER_RATE_PER_MIN", 20)


def daily_cap() -> int:
    return _env_int("NEWSLETTER_DAILY_CAP", 1500)


def brand() -> str:
    return (os.environ.get("NEWSLETTER_BRAND") or "Aerostator by MOTRES").strip()


def api_url() -> str:
    return ((os.environ.get("PUBLIC_API_URL") or "").strip().rstrip("/")
            or E.app_url())


# ── store ────────────────────────────────────────────────────────────────────

def _empty() -> dict:
    return {"subscribers": {}, "campaigns": {}, "send_log": [],
            "notices": {}, "notice_reads": {}}


def _load() -> dict:
    with _LOCK:
        try:
            with open(_STORE_FILE, encoding="utf-8") as f:
                d = json.load(f)
        except FileNotFoundError:
            return _empty()
        base = _empty()
        base.update(d if isinstance(d, dict) else {})
        return base


def _save(d: dict) -> None:
    with _LOCK:
        from motor_ai_sim.private_files import ensure_private_dir, open_private
        ensure_private_dir(_STORE_FILE.parent)
        tmp = _STORE_FILE.with_suffix(".tmp")
        with open_private(tmp, "w") as f:   # 0600 — audit 2026-09-29 #9
            json.dump(d, f, indent=1, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, _STORE_FILE)


def _key(label: bytes) -> bytes:
    return hmac.new(U._secret().encode("utf-8"), label, hashlib.sha256).digest()


def ip_hash(ip: str) -> str:
    if not ip or ip == "?":
        return ""
    return hmac.new(_key(b"newsletter-ip-v1"), ip.encode("utf-8"),
                    hashlib.sha256).hexdigest()[:32]


# ── tokens ───────────────────────────────────────────────────────────────────

def _token(email: str, purpose: str, ttl: int, **extra) -> str:
    now = int(time.time())
    return jwt.encode({"sub": email, "purpose": purpose, "iat": now,
                       "exp": now + ttl, "iss": _ISS, **extra},
                      _key(b"newsletter-link-v1"), algorithm="HS256")


def _claims(token: str, purpose: str) -> Optional[dict]:
    try:
        c = jwt.decode(token or "", _key(b"newsletter-link-v1"),
                       algorithms=["HS256"], issuer=_ISS,
                       options={"require": ["exp", "sub"]})
    except Exception:
        return None
    return c if c.get("purpose") == purpose else None


def unsubscribe_token(email: str, campaign: str = "") -> str:
    return _token(U._norm(email), "nl_unsub", UNSUB_TTL_S, c=campaign)


def unsubscribe_url(email: str, campaign: str = "") -> str:
    """The page link (the web app shows 'you are unsubscribed')."""
    return f"{E.app_url()}/?unsubscribe={unsubscribe_token(email, campaign)}"


def one_click_url(email: str, campaign: str = "") -> str:
    """RFC 8058 target: the mailbox provider POSTs here directly."""
    return (f"{api_url()}/api/newsletter/unsubscribe?t="
            f"{unsubscribe_token(email, campaign)}")


def confirm_url(token: str) -> str:
    return f"{E.app_url()}/?nl_confirm={token}"


# ── consent ──────────────────────────────────────────────────────────────────

def _hist(rec: dict, event: str, source: str, **kw) -> None:
    h = rec.setdefault("history", [])
    h.append({"ts": time.time(), "event": event, "source": source, **kw})
    del h[:-50]


def status(email: str) -> dict:
    rec = _load()["subscribers"].get(U._norm(email)) or {}
    return {"status": rec.get("status") or "none",
            "subscribed": rec.get("status") == "confirmed",
            "confirmed_at": rec.get("confirmed_at"),
            "consent_text": CONSENT_TEXT,
            "consent_version": CONSENT_TEXT_VERSION}


def record_pending(email: str, *, source: str, ip: str = "") -> None:
    """Remember a ticked checkbox WITHOUT sending anything yet (sign-up: the
    confirmation mail goes out once the account's own address is proven)."""
    email = U._norm(email)
    if source not in SOURCES:
        raise ValueError(source)
    with _LOCK:
        d = _load()
        rec = d["subscribers"].setdefault(email, {})
        if rec.get("status") == "confirmed":
            return
        rec.update(status="pending", source=source,
                   text_version=CONSENT_TEXT_VERSION, consent_at=time.time(),
                   ip_hash=ip_hash(ip), mail_sent=False)
        rec.pop("nonce", None)
        _hist(rec, "consent_given", source, version=CONSENT_TEXT_VERSION)
        _save(d)


def has_unsent_pending(email: str) -> bool:
    rec = _load()["subscribers"].get(U._norm(email)) or {}
    return rec.get("status") == "pending" and not rec.get("mail_sent")


def issue_confirmation(email: str) -> Optional[str]:
    """Mint the single-use confirmation token for a PENDING record."""
    email = U._norm(email)
    nonce = secrets.token_urlsafe(18)
    with _LOCK:
        d = _load()
        rec = d["subscribers"].get(email)
        if not rec or rec.get("status") != "pending":
            return None
        rec["nonce"] = hashlib.sha256(nonce.encode()).hexdigest()
        rec["mail_sent"] = True
        _save(d)
    return _token(email, "nl_confirm", CONFIRM_TTL_S, n=nonce)


def confirmation_mail(token: str) -> tuple[str, str]:
    url = confirm_url(token)
    return ("Confirm your subscription",
            "Hello,\n\nyou asked to receive product news and updates from "
            f"{brand()} ({CONSENT_TEXT})\n\nConfirm with this link:\n\n{url}\n\n"
            "The link expires in 7 days. If you did not ask for this, ignore "
            "this message — you will not be subscribed.\n\n"
            f"{COMPANY_FOOTER}\n")


def send_confirmation(email: str) -> bool:
    tok = issue_confirmation(email)
    if not tok:
        return False
    subj, body = confirmation_mail(tok)
    if not E.send(email, subj, body):
        log.warning("newsletter: SMTP not configured — confirmation mail for "
                    "%s not sent", email)
        return False
    return True


def request_subscribe(email: str, *, source: str, ip: str = "") -> str:
    """Record consent and mail the confirmation link.  'already' | 'sent'."""
    if status(email)["subscribed"]:
        return "already"
    record_pending(email, source=source, ip=ip)
    send_confirmation(email)
    return "sent"


def confirm(token: str) -> Optional[str]:
    c = _claims(token, "nl_confirm")
    if not c:
        return None
    email = U._norm(c.get("sub") or "")
    got = hashlib.sha256(str(c.get("n") or "").encode()).hexdigest()
    with _LOCK:
        d = _load()
        rec = d["subscribers"].get(email)
        if not rec or rec.get("status") != "pending" or \
                not hmac.compare_digest(rec.get("nonce") or "", got):
            return None
        rec.pop("nonce", None)
        rec.update(status="confirmed", confirmed_at=time.time())
        _hist(rec, "confirmed", rec.get("source") or "")
        _save(d)
    log.info("newsletter: subscription confirmed")
    return email


def unsubscribe(email: str, *, source: str, campaign: str = "") -> bool:
    """Instant.  Returns True when a subscription/pending consent ended."""
    email = U._norm(email)
    with _LOCK:
        d = _load()
        rec = d["subscribers"].get(email)
        if not rec or rec.get("status") not in ("confirmed", "pending"):
            return False
        rec.pop("nonce", None)
        rec.update(status="unsubscribed", unsubscribed_at=time.time())
        _hist(rec, "unsubscribed", source, campaign=campaign)
        camp = d["campaigns"].get(campaign) if campaign else None
        if camp is not None:
            camp["unsubscribes"] = int(camp.get("unsubscribes") or 0) + 1
        _save(d)
    log.info("newsletter: unsubscribed via %s", source)
    return True


def unsubscribe_by_token(token: str) -> bool:
    """False only for a bad token; an already-unsubscribed address is True
    (the link is idempotent and says nothing about the address)."""
    c = _claims(token, "nl_unsub")
    if not c:
        return False
    unsubscribe(c.get("sub") or "", source="link", campaign=str(c.get("c") or ""))
    return True


def subscribers(roles: Optional[list[str]] = None,
                only_confirmed: bool = False) -> list[dict]:
    out = []
    for email, rec in sorted(_load()["subscribers"].items()):
        st = rec.get("status") or "none"
        if only_confirmed and st != "confirmed":
            continue
        role = (U.get_user(email) or {}).get("role") or ""
        if roles and role not in roles:
            continue
        out.append({"email": email, "status": st, "role": role,
                    "source": rec.get("source") or "",
                    "text_version": rec.get("text_version") or "",
                    "consent_at": rec.get("consent_at"),
                    "confirmed_at": rec.get("confirmed_at"),
                    "unsubscribed_at": rec.get("unsubscribed_at")})
    return out


def is_confirmed(email: str) -> bool:
    rec = _load()["subscribers"].get(U._norm(email)) or {}
    return rec.get("status") == "confirmed"


# ── Markdown → HTML / text (a small, safe subset) ───────────────────────────

_LINK_RE = re.compile(r"\[([^\]]+)\]\(((?:https?://|mailto:)[^)\s]+)\)")


def _inline(s: str) -> str:
    s = html.escape(s, quote=True)
    s = _LINK_RE.sub(lambda m: f'<a href="{m.group(2)}" style="color:#2563eb">'
                     f'{m.group(1)}</a>', s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def markdown_to_html(md: str) -> str:
    out: list[str] = []
    para: list[str] = []
    lst: Optional[str] = None

    def flush_para():
        if para:
            out.append("<p style=\"margin:0 0 14px\">" + "<br>".join(
                _inline(x) for x in para) + "</p>")
            para.clear()

    def close_list():
        nonlocal lst
        if lst:
            out.append(f"</{lst}>")
            lst = None

    for raw in (md or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        m_h = re.match(r"^(#{1,3})\s+(.*)$", line)
        m_ul = re.match(r"^\s*[-*]\s+(.*)$", line)
        m_ol = re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        if not line.strip():
            flush_para(); close_list()
        elif m_h:
            flush_para(); close_list()
            n = len(m_h.group(1)) + 1
            out.append(f"<h{n} style=\"margin:18px 0 8px\">{_inline(m_h.group(2))}</h{n}>")
        elif m_ul or m_ol:
            flush_para()
            want = "ul" if m_ul else "ol"
            if lst != want:
                close_list(); out.append(f"<{want}>"); lst = want
            out.append(f"<li>{_inline((m_ul or m_ol).group(1))}</li>")
        else:
            close_list(); para.append(line)
    flush_para(); close_list()
    return "\n".join(out)


def markdown_to_text(md: str) -> str:
    t = _LINK_RE.sub(lambda m: f"{m.group(1)} ({m.group(2)})", md or "")
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    return t.strip() + "\n"


def render(subject: str, body_md: str, unsub_page: str) -> tuple[str, str]:
    """(html, text) with the branded shell and the footer."""
    b = html.escape(brand())
    html_doc = f"""<!doctype html><html><head><meta charset="utf-8">
<title>{html.escape(subject)}</title></head>
<body style="margin:0;background:#f4f5f7;font-family:Arial,Helvetica,sans-serif;color:#1f2937">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:8px">
<tr><td style="background:#0f172a;color:#ffffff;padding:16px 24px;font-size:18px;font-weight:bold;border-radius:8px 8px 0 0">{b}</td></tr>
<tr><td style="padding:24px;font-size:15px;line-height:1.55">
{markdown_to_html(body_md)}
</td></tr>
<tr><td style="padding:16px 24px;border-top:1px solid #e5e7eb;font-size:12px;color:#6b7280;line-height:1.5">
{html.escape(COMPANY_FOOTER)}<br>
You receive this because you subscribed to product news.
<a href="{html.escape(unsub_page)}" style="color:#6b7280">Unsubscribe</a> (one click).
</td></tr></table></td></tr></table></body></html>"""
    text = (f"{brand()}\n\n{markdown_to_text(body_md)}\n--\n{COMPANY_FOOTER}\n"
            "You receive this because you subscribed to product news.\n"
            f"Unsubscribe (one click): {unsub_page}\n")
    return html_doc, text


def build_message(email: str, subject: str, body_md: str,
                  campaign: str = "") -> EmailMessage:
    email = U._norm(email)
    h, t = render(subject, body_md, unsubscribe_url(email, campaign))
    msg = EmailMessage()
    msg["From"] = E.mail_from()
    msg["To"] = email
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=(E.mail_from().split("@")[-1] or None))
    msg["List-Unsubscribe"] = f"<{one_click_url(email, campaign)}>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg["List-Id"] = f"{brand()} news <news.{(E.mail_from().split('@')[-1] or 'local')}>"
    msg.set_content(t)
    msg.add_alternative(h, subtype="html")
    return msg


# ── campaigns ────────────────────────────────────────────────────────────────

def _clean_roles(roles) -> list[str]:
    return sorted({str(t) for t in (roles or []) if str(t).strip()})


def create_campaign(subject: str, body_md: str, roles=None, author: str = "") -> dict:
    subject = (subject or "").strip()
    if not subject or len(subject) > 200:
        raise ValueError("subject must be 1–200 characters")
    if not (body_md or "").strip():
        raise ValueError("body is empty")
    cid = uuid.uuid4().hex[:12]
    rec = {"id": cid, "subject": subject, "body_md": body_md,
           "roles": _clean_roles(roles), "author": author or "",
           "created": time.time(), "status": "draft", "scheduled_at": None,
           "started_at": None, "finished_at": None, "recipients": {},
           "unsubscribes": 0}
    with _LOCK:
        d = _load()
        d["campaigns"][cid] = rec
        _save(d)
    return public_campaign(rec)


def update_campaign(cid: str, subject=None, body_md=None, roles=None) -> dict:
    with _LOCK:
        d = _load()
        rec = d["campaigns"].get(cid)
        if rec is None:
            raise KeyError(cid)
        if rec["status"] != "draft":
            raise ValueError("only a draft can be edited")
        if subject is not None:
            if not subject.strip() or len(subject) > 200:
                raise ValueError("subject must be 1–200 characters")
            rec["subject"] = subject.strip()
        if body_md is not None:
            rec["body_md"] = body_md
        if roles is not None:
            rec["roles"] = _clean_roles(roles)
        _save(d)
    return public_campaign(rec)


def public_campaign(rec: dict) -> dict:
    counts = {"pending": 0, "sending": 0, "sent": 0, "failed": 0, "bounced": 0,
              "skipped": 0}
    for r in (rec.get("recipients") or {}).values():
        counts[r.get("status", "pending")] = counts.get(r.get("status", "pending"), 0) + 1
    return {k: rec.get(k) for k in ("id", "subject", "body_md", "roles", "author",
                                    "created", "status", "scheduled_at",
                                    "started_at", "finished_at")} | {
        "stats": {**counts, "total": len(rec.get("recipients") or {}),
                  "unsubscribes": int(rec.get("unsubscribes") or 0)}}


def list_campaigns() -> list[dict]:
    return sorted((public_campaign(c) for c in _load()["campaigns"].values()),
                  key=lambda c: -(c.get("created") or 0))


def get_campaign(cid: str) -> Optional[dict]:
    rec = _load()["campaigns"].get(cid)
    return public_campaign(rec) if rec else None


def schedule(cid: str, at: Optional[float] = None) -> dict:
    """Queue a draft: now (at=None) or at a UNIX time.  The audience is taken
    when sending starts, so a later confirmation is included."""
    with _LOCK:
        d = _load()
        rec = d["campaigns"].get(cid)
        if rec is None:
            raise KeyError(cid)
        if rec["status"] != "draft":
            raise ValueError(f"campaign is {rec['status']}")
        rec["status"] = "scheduled"
        rec["scheduled_at"] = float(at) if at else time.time()
        _save(d)
    return public_campaign(rec)


def cancel(cid: str) -> dict:
    with _LOCK:
        d = _load()
        rec = d["campaigns"].get(cid)
        if rec is None:
            raise KeyError(cid)
        if rec["status"] in ("scheduled", "sending"):
            rec["status"] = "cancelled"
            rec["finished_at"] = time.time()
            _save(d)
        elif rec["status"] == "draft":
            pass
        else:
            raise ValueError(f"campaign is {rec['status']}")
    return public_campaign(rec)


def audience(roles=None) -> list[str]:
    return [s["email"] for s in subscribers(_clean_roles(roles) or None,
                                            only_confirmed=True)]


def _classify(exc: BaseException) -> str:
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "bounced"
    code = getattr(exc, "smtp_code", None)
    if isinstance(code, int) and 500 <= code < 600:
        return "bounced"
    return "failed"


def recover_interrupted() -> int:
    """Called when the worker starts: a message claimed ('sending') by a
    process that died is marked failed, never re-sent."""
    n = 0
    with _LOCK:
        d = _load()
        for c in d["campaigns"].values():
            for r in (c.get("recipients") or {}).values():
                if r.get("status") == "sending":
                    r.update(status="failed", error="interrupted by restart")
                    n += 1
        if n:
            _save(d)
    return n


def pump(now: Optional[float] = None,
         sender: Optional[Callable[[EmailMessage], None]] = None,
         max_batch: int = 50) -> int:
    """Send as many queued messages as the rate limits allow right now.
    Returns the number of send attempts made.  `now`/`sender` are for tests."""
    sender = sender or E.deliver
    attempts = 0
    while attempts < max_batch:
        t = time.time() if now is None else now
        with _LOCK:
            d = _load()
            log_ts = [x for x in d.get("send_log") or [] if t - x < 86400]
            d["send_log"] = log_ts
            if len([x for x in log_ts if t - x < 60]) >= rate_per_min() \
                    or len(log_ts) >= daily_cap():
                _save(d)
                return attempts
            job = None
            for c in sorted(d["campaigns"].values(), key=lambda c: c.get("scheduled_at") or 0):
                if c["status"] == "scheduled" and (c.get("scheduled_at") or 0) <= t:
                    c["status"] = "sending"
                    c["started_at"] = t
                    c["recipients"] = {e: {"status": "pending"}
                                       for e in audience(c.get("roles"))}
                if c["status"] != "sending":
                    continue
                nxt = next((e for e, r in c["recipients"].items()
                            if r.get("status") == "pending"), None)
                if nxt is None:
                    c["status"] = "done"
                    c["finished_at"] = t
                    continue
                job = (c, nxt)
                break
            if job is None:
                _save(d)
                return attempts
            camp, email = job
            r = camp["recipients"][email]
            if not is_confirmed(email):          # unsubscribed meanwhile
                r.update(status="skipped", ts=t)
                _save(d)
                continue
            r.update(status="sending", ts=t)
            d["send_log"].append(t)
            _save(d)
            subject, body_md, cid = camp["subject"], camp["body_md"], camp["id"]
        attempts += 1
        try:
            sender(build_message(email, subject, body_md, cid))
            outcome, err = "sent", ""
        except Exception as e:                               # noqa: BLE001
            outcome, err = _classify(e), f"{type(e).__name__}: {str(e)[:160]}"
            log.warning("newsletter: campaign %s send failed (%s)", cid, outcome)
        with _LOCK:
            d = _load()
            c2 = d["campaigns"].get(cid)
            if c2 and email in c2.get("recipients", {}):
                c2["recipients"][email].update(status=outcome, error=err or None)
                _save(d)
    return attempts


def send_test(email: str, subject: str, body_md: str) -> None:
    E.deliver(build_message(email, "[TEST] " + subject, body_md))


def sent_last_24h(now: Optional[float] = None) -> int:
    t = time.time() if now is None else now
    return len([x for x in _load().get("send_log") or [] if t - x < 86400])


_worker: Optional[threading.Thread] = None
_stop = threading.Event()


def _loop() -> None:
    try:
        recover_interrupted()
    except Exception as e:                                   # pragma: no cover
        log.error("newsletter: recovery failed: %s", e)
    while not _stop.wait(3.0):
        if not E.smtp_configured():
            continue
        try:
            pump()
        except Exception as e:                               # pragma: no cover
            log.error("newsletter: queue error: %s", e)


def start_worker() -> None:
    global _worker
    if os.environ.get("NEWSLETTER_WORKER", "1").strip() == "0":
        return
    if _worker and _worker.is_alive():
        return
    _stop.clear()
    _worker = threading.Thread(target=_loop, daemon=True, name="newsletter-queue")
    _worker.start()


def stop_worker() -> None:
    _stop.set()


# ── in-app notices ───────────────────────────────────────────────────────────

LEVELS = ("info", "warning", "important")


def post_notice(title: str, body: str = "", *, level: str = "info",
                emails=None, roles=None, expires_at: Optional[float] = None,
                author: str = "") -> dict:
    title = (title or "").strip()
    if not title or len(title) > 140:
        raise ValueError("title must be 1–140 characters")
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    nid = uuid.uuid4().hex[:12]
    rec = {"id": nid, "title": title, "body": (body or "")[:2000], "level": level,
           "emails": sorted({U._norm(e) for e in (emails or []) if e}),
           "roles": _clean_roles(roles), "created": time.time(),
           "expires_at": expires_at, "active": True, "author": author or ""}
    with _LOCK:
        d = _load()
        d["notices"][nid] = rec
        _save(d)
    return rec


def withdraw_notice(nid: str) -> None:
    with _LOCK:
        d = _load()
        if nid not in d["notices"]:
            raise KeyError(nid)
        d["notices"][nid]["active"] = False
        _save(d)


def _targets(n: dict, email: str, role: str) -> bool:
    if n.get("emails") and email not in n["emails"]:
        return False
    if n.get("roles") and role not in n["roles"]:
        return False
    return True


def notices_for(email: str, role: str = "", now: Optional[float] = None) -> list[dict]:
    t = time.time() if now is None else now
    email = U._norm(email)
    d = _load()
    read = set(d["notice_reads"].get(email) or [])
    out = []
    for n in sorted(d["notices"].values(), key=lambda n: -n["created"]):
        if not n.get("active") or (n.get("expires_at") and n["expires_at"] < t):
            continue
        if not _targets(n, email, role):
            continue
        out.append({k: n[k] for k in ("id", "title", "body", "level", "created")}
                   | {"read": n["id"] in read})
    return out


def mark_read(email: str, nid: str) -> bool:
    email = U._norm(email)
    with _LOCK:
        d = _load()
        if nid not in d["notices"]:
            return False
        lst = d["notice_reads"].setdefault(email, [])
        if nid not in lst:
            lst.append(nid)
            _save(d)
    return True


def all_notices() -> list[dict]:
    d = _load()
    reads = d["notice_reads"]
    return [n | {"read_count": sum(1 for v in reads.values() if n["id"] in v)}
            for n in sorted(d["notices"].values(), key=lambda n: -n["created"])]
