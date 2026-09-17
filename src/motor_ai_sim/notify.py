"""Tell the team when a visitor asks for access — by e-mail, over port 587.

The standing note in this codebase was "the host blocks outbound SMTP", and it
is half true: 25 and 465 are blocked on the Hetzner box, **587 (submission,
STARTTLS) is open** — verified against smtp.gmail.com and smtp.office365.com on
2026-09-17.  motresres.com is on Google Workspace, so the notifier is ten lines
of ``smtplib``: STARTTLS on 587, an app password, one message per request.  No
API, no dependency, no service in the middle.

It is OPTIONAL by construction.  With ``SMTP_USER`` / ``SMTP_PASSWORD`` unset —
this workstation, and the server until the owner adds them — every call logs one
INFO line and returns.  The admin inbox (``support_store``) is the record; the
mail is only the faster way to hear about it.  Nothing here may delay or break
the visitor's reply, so the send runs on a daemon thread with a 10 s socket
timeout and swallows its own failures.

``Reply-To`` is the VISITOR's address, so answering the notification answers the
person — which is the whole point of collecting the address in the first place.

Setup (docs/VISITOR_REQUESTS.md says the same in three lines):
  1. Google account for vadim@motresres.com (or a dedicated mailbox) with
     2-step verification ON → https://myaccount.google.com/apppasswords →
     create an app password.
  2. Put it in /etc/motres/api.env::

        SMTP_USER=vadim@motresres.com
        SMTP_PASSWORD=<the 16-character app password>
        # optional, these are the defaults:
        # SMTP_HOST=smtp.gmail.com
        # SMTP_PORT=587
        # NOTIFY_FROM=<SMTP_USER>
        # NOTIFY_TO=vadim@motresres.com

  3. Restart the API.  Unset again = no mail, the inbox still fills.

A Telegram push is kept as a second, equally optional channel
(``TELEGRAM_BOT_TOKEN`` + ``TELEGRAM_CHAT_ID``): it costs nothing when unset and
it is the one channel that survives the mailbox the alerts are about.
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import threading
import time
import urllib.request
from email.message import EmailMessage
from typing import Optional

log = logging.getLogger(__name__)

#: A socket timeout, not a deadline for the thread: a submission handshake over
#: TLS to Gmail is three round trips, and the visitor is not waiting on any of
#: them (the send runs off the request thread).
SMTP_TIMEOUT_S = 10.0
TELEGRAM_TIMEOUT_S = 5.0
_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_TELEGRAM_MAX_CHARS = 3500

DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 587
DEFAULT_NOTIFY_TO = "vadim@motresres.com"

_lock = threading.Lock()
#: The local day whose visitor-chat digest has already gone out.
_digest_done: str = ""
_unconfigured_logged = False


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


# ── is anything configured? ──────────────────────────────────────────────────

def smtp_configured() -> bool:
    """Both credentials set?  Read per call — the owner adds them to
    /etc/motres/api.env without this code changing."""
    return bool(_env("SMTP_USER") and _env("SMTP_PASSWORD"))


def telegram_configured() -> bool:
    return bool(_env("TELEGRAM_BOT_TOKEN") and _env("TELEGRAM_CHAT_ID"))


def configured() -> bool:
    return smtp_configured() or telegram_configured()


def recipients() -> list[str]:
    raw = _env("NOTIFY_TO", DEFAULT_NOTIFY_TO)
    return [a.strip() for a in raw.split(",") if a.strip()]


# ── the channels ─────────────────────────────────────────────────────────────

def _send_email(subject: str, body: str, reply_to: str = "") -> bool:
    """One submission over STARTTLS.  Returns True when it was accepted.

    Never raises: a mail server having a bad minute is not a reason for a
    visitor's question to fail, and the request is already in the inbox.
    """
    user, password = _env("SMTP_USER"), _env("SMTP_PASSWORD")
    if not (user and password):
        return False
    to = recipients()
    if not to:
        return False
    msg = EmailMessage()
    msg["From"] = _env("NOTIFY_FROM", user)
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    if reply_to:
        # Answering the alert answers the person who asked.
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    host = _env("SMTP_HOST", DEFAULT_SMTP_HOST)
    try:
        port = int(_env("SMTP_PORT", str(DEFAULT_SMTP_PORT)))
    except ValueError:
        port = DEFAULT_SMTP_PORT
    try:
        with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_S) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(user, password)
            s.send_message(msg)
        log.info("notify: e-mailed %s — %s", ", ".join(to), subject)
        return True
    except Exception as e:                                   # noqa: BLE001
        log.warning("notify: SMTP send failed via %s:%s (%s: %s) — the request "
                    "is still in the admin inbox", host, port,
                    type(e).__name__, str(e)[:160])
        return False


def _send_telegram(text: str) -> bool:
    """One sendMessage call.  Returns True on a 2xx.  Never raises."""
    token, chat = _env("TELEGRAM_BOT_TOKEN"), _env("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    body = json.dumps({"chat_id": chat, "text": text[:_TELEGRAM_MAX_CHARS],
                       "disable_web_page_preview": True}).encode("utf-8")
    req = urllib.request.Request(
        _TELEGRAM_API.format(token=token), data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TELEGRAM_TIMEOUT_S) as resp:
            ok = 200 <= int(getattr(resp, "status", 200) or 200) < 300
        if not ok:
            log.warning("notify: telegram did not accept the message")
        return ok
    except Exception as e:                                   # noqa: BLE001
        log.warning("notify: telegram sendMessage failed (%s: %s)",
                    type(e).__name__, str(e)[:160])
        return False


def send(subject: str, body: str, *, reply_to: str = "",
         block: bool = False) -> bool:
    """Push one notification down every configured channel.

    ``block=False`` (the default, and what a request handler uses) hands the
    work to a daemon thread and returns immediately: the visitor's reply must
    never wait on a TLS handshake, and a request that files an access request is
    a request a person is watching.  The return value then says only that the
    attempt was STARTED.
    """
    global _unconfigured_logged
    if not configured():
        if not _unconfigured_logged:
            _unconfigured_logged = True
            log.info("notify: smtp not configured (SMTP_USER / SMTP_PASSWORD "
                     "unset) — requests are still recorded in the admin inbox")
        else:
            log.info("notify: smtp not configured")
        return False

    def _run() -> bool:
        ok = False
        if smtp_configured():
            ok = _send_email(subject, body, reply_to) or ok
        if telegram_configured():
            ok = _send_telegram(f"{subject}\n\n{body}") or ok
        return ok

    if block:
        return _run()
    threading.Thread(target=_run, daemon=True, name="notify-send").start()
    return True


# ── what we say ──────────────────────────────────────────────────────────────

def _line(label: str, value: str) -> str:
    value = (value or "").strip()
    return f"{label}: {value}\n" if value else ""


def format_access_request(rec: dict) -> tuple[str, str]:
    """(subject, body) for one access request — everything the owner needs to
    decide, so the mail is an answer and not a prompt to go and look."""
    rec = rec or {}
    email = str(rec.get("email") or "").strip()
    who = (str(rec.get("name") or "").strip()
           or str(rec.get("company") or "").strip() or email or "a visitor")
    company = str(rec.get("company") or "").strip()
    if company and company.lower() not in who.lower():
        who = f"{who} ({company})"
    merged = int(rec.get("merged") or 1)
    subject = f"AeroStator Core: access request from {who}"
    if merged > 1:
        subject += f" [updated x{merged}]"

    head = ("A visitor asked for access on the landing page."
            if merged <= 1 else
            f"A visitor updated their access request (message {merged}).")
    body = (
        f"{head}\n\n"
        + _line("Name", str(rec.get("name") or ""))
        + _line("Company", company)
        + _line("E-mail", email)
        + _line("Wants", str(rec.get("note") or ""))
        + _line("Received", time.strftime(
            "%Y-%m-%d %H:%M", time.localtime(float(rec.get("updated") or 0) or None)))
    )
    turns = [t for t in (rec.get("transcript") or []) if isinstance(t, dict)]
    if turns:
        body += "\n--- the conversation ---\n"
        for t in turns[-20:]:
            speaker = "Visitor" if t.get("role") == "user" else "Assistant"
            body += f"\n{speaker}: {str(t.get('content') or '').strip()[:1500]}\n"
    body += ("\n\nAdmin tab -> Visitor requests. The Invite button there opens "
             "the invite dialog already addressed to them.\n"
             "Reply to this mail to answer the visitor directly.\n")
    return subject, body


def access_request(rec: dict, *, block: bool = False) -> bool:
    """One notification per filed access request."""
    subject, body = format_access_request(rec or {})
    return send(subject, body, reply_to=str((rec or {}).get("email") or ""),
                block=block)


def visitor_chat_digest(day: str, turns: int, conversations: int = 0,
                        *, block: bool = False) -> bool:
    """One notification per day: how many visitors talked to the assistant."""
    if turns <= 0:
        return False
    convo = (f" across {conversations} conversation"
             f"{'s' if conversations != 1 else ''}" if conversations else "")
    return send(
        f"AeroStator Core: {turns} visitor message"
        f"{'s' if turns != 1 else ''} on {day}",
        f"Visitors talked to the in-app assistant {turns} time"
        f"{'s' if turns != 1 else ''}{convo} on {day}, and none of them left "
        "their contact details.\n\n"
        "Admin tab -> Visitor chats to read what they asked.\n",
        block=block)


def maybe_daily_digest(*, block: bool = False) -> bool:
    """Send YESTERDAY's visitor-chat count, once, the first time this process
    handles a visitor on a new day.

    No scheduler: this backend has no cron of its own, and a digest that needed
    one would simply never arrive.  A day with no visitors sends nothing — and
    neither does a day whose visitors already produced access requests, because
    each of those was mailed the moment it arrived and a summary of mail the
    owner has already read is exactly the kind of noise that trains a person to
    filter the alert away.
    """
    global _digest_done
    try:
        from motor_ai_sim import support_store as S
        today = time.strftime("%Y-%m-%d", time.localtime())
        with _lock:
            if _digest_done == today:
                return False
            _digest_done = today
        yesterday = time.strftime("%Y-%m-%d",
                                  time.localtime(time.time() - 86400))
        turns = S.read_day(yesterday)
        if not turns:
            return False
        for r in S.list_access_requests():
            when = time.strftime("%Y-%m-%d",
                                 time.localtime(float(r.get("ts") or 0.0)))
            if when == yesterday:
                return False
        convs = len({str(t.get("conv") or "") for t in turns})
        return visitor_chat_digest(yesterday, len(turns), convs, block=block)
    except Exception as e:                                   # noqa: BLE001
        log.warning("notify: daily digest failed (%s: %s)", type(e).__name__, e)
        return False


def reset_digest_state(day: Optional[str] = None) -> None:
    """Tests: forget which day's digest has gone out."""
    global _digest_done, _unconfigured_logged
    with _lock:
        _digest_done = day or ""
    _unconfigured_logged = False
