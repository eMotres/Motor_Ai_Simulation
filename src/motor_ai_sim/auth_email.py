"""Transactional mail + rate limits for e-mail/password accounts.

Mail goes out over SMTP configured ONLY from the environment (on the server:
/etc/motres/api.env; never in the repo)::

    SMTP_HOST=smtp.example.com      # default smtp.gmail.com
    SMTP_PORT=587                   # 587 = STARTTLS, 465 = implicit TLS
    SMTP_USER=...
    SMTP_PASS=...                   # SMTP_PASSWORD (notify.py's name) also read
    MAIL_FROM=no-reply@...          # default NOTIFY_FROM, then SMTP_USER
    PUBLIC_APP_URL=https://app...   # base of the links in the mails

Unconfigured is a supported state, not an error: the link is written to the
server log at WARNING and the caller files the account in the admin "pending"
list, where the owner approves it by hand.

Link base URL is NEVER derived from the request's Host/Origin headers — that
is how reset links get pointed at an attacker's domain.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
import threading
import time
import urllib.parse
from email.message import EmailMessage

log = logging.getLogger(__name__)

SMTP_TIMEOUT_S = 10.0
DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 587
DEFAULT_APP_URL = "http://localhost:5173"


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _smtp_pass() -> str:
    return _env("SMTP_PASS") or _env("SMTP_PASSWORD")


def smtp_configured() -> bool:
    return bool(_env("SMTP_USER") and _smtp_pass())


def app_url() -> str:
    return (_env("PUBLIC_APP_URL") or DEFAULT_APP_URL).rstrip("/")


def link(kind: str, token: str) -> str:
    """`kind` is 'verify' or 'reset'; the web app reads the query param."""
    return f"{app_url()}/?{kind}={urllib.parse.quote(token, safe='')}"


def mail_from() -> str:
    return _env("MAIL_FROM") or _env("NOTIFY_FROM") or _env("SMTP_USER")


def deliver(msg: EmailMessage) -> None:
    """Hand one prepared message to the configured SMTP server.  RAISES on
    any failure (the newsletter queue classifies the exception as a bounce
    or a transient failure); `_send` below is the forgiving wrapper."""
    user, password = _env("SMTP_USER"), _smtp_pass()
    if not (user and password):
        raise RuntimeError("SMTP is not configured")
    if not msg.get("From"):
        msg["From"] = mail_from()
    host = _env("SMTP_HOST", DEFAULT_SMTP_HOST)
    try:
        port = int(_env("SMTP_PORT", str(DEFAULT_SMTP_PORT)))
    except ValueError:
        port = DEFAULT_SMTP_PORT
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT_S,
                              context=ctx) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_S) as s:
            s.ehlo()
            s.starttls(context=ctx)
            s.ehlo()
            s.login(user, password)
            s.send_message(msg)


def _send(to: str, subject: str, body: str) -> bool:
    user, password = _env("SMTP_USER"), _smtp_pass()
    if not (user and password):
        return False
    msg = EmailMessage()
    msg["From"] = mail_from()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    host = _env("SMTP_HOST", DEFAULT_SMTP_HOST)
    port = _env("SMTP_PORT", str(DEFAULT_SMTP_PORT))
    try:
        deliver(msg)
        log.info("auth-mail: sent %r to %s", subject, to)
        return True
    except Exception as e:                                   # noqa: BLE001
        log.warning("auth-mail: SMTP send to %s failed via %s:%s (%s: %s)",
                    to, host, port, type(e).__name__, str(e)[:160])
        return False


def send(to: str, subject: str, body: str, *, block: bool = False) -> bool:
    """Send in a daemon thread (the HTTP answer never waits on — or reveals
    anything through the timing of — a TLS handshake).  Returns False when
    SMTP is not configured, True when a send was started / succeeded."""
    if not smtp_configured():
        return False
    if block:
        return _send(to, subject, body)
    threading.Thread(target=_send, args=(to, subject, body), daemon=True,
                     name="auth-mail").start()
    return True


# ── message texts ────────────────────────────────────────────────────────────

def verify_mail(to: str, token: str) -> tuple[str, str]:
    url = link("verify", token)
    return ("Confirm your e-mail address",
            "Hello,\n\nconfirm this address to activate your account:\n\n"
            f"{url}\n\nThe link works once and expires in 24 hours. If you did "
            "not sign up, ignore this message.\n")


def exists_mail(to: str) -> tuple[str, str]:
    url = app_url()
    return ("Sign-up attempt for your account",
            "Hello,\n\nsomeone tried to register a new account with this "
            "address, but an account already exists. If it was you, sign in "
            f"at {url} — or use \"Forgot password\" there to set a new one.\n"
            "If it was not you, no action is needed.\n")


def reset_mail(to: str, token: str) -> tuple[str, str]:
    url = link("reset", token)
    return ("Reset your password",
            "Hello,\n\nuse this link to set a new password:\n\n"
            f"{url}\n\nThe link works once and expires in 24 hours; setting a "
            "new password signs out every other session. If you did not ask "
            "for this, ignore this message.\n")


# ── rate limiting ────────────────────────────────────────────────────────────

class Limiter:
    """Sliding-window failure counter: `max_hits` within `window_s` locks the
    key for `lock_s` after the last hit.  In-memory, per process."""

    def __init__(self, max_hits: int, window_s: float, lock_s: float):
        self.max_hits, self.window_s, self.lock_s = max_hits, window_s, lock_s
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            lst = [t for t in self._hits.get(key, [])
                   if now - t < max(self.window_s, self.lock_s)]
            if lst:
                self._hits[key] = lst
            else:
                self._hits.pop(key, None)
            recent = [t for t in lst if now - t < self.window_s]
            return len(recent) >= self.max_hits and now - lst[-1] < self.lock_s

    def hit(self, key: str) -> None:
        with self._lock:
            self._hits.setdefault(key, []).append(time.time())
            if len(self._hits) > 20000:                     # pragma: no cover
                self._hits.clear()

    def clear(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def reset_all(self) -> None:
        with self._lock:
            self._hits.clear()


#: Login failures: per account (any IP) and per IP (any account).
LOGIN_ACCOUNT = Limiter(max_hits=8, window_s=900.0, lock_s=900.0)
LOGIN_IP = Limiter(max_hits=30, window_s=900.0, lock_s=900.0)
#: Mail-sending requests (register, resend, reset request).
MAIL_IP = Limiter(max_hits=10, window_s=3600.0, lock_s=3600.0)
MAIL_ACCOUNT = Limiter(max_hits=3, window_s=3600.0, lock_s=3600.0)


def reset_limits() -> None:
    for lim in (LOGIN_ACCOUNT, LOGIN_IP, MAIL_IP, MAIL_ACCOUNT):
        lim.reset_all()
