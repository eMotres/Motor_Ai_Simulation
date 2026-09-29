"""Keep credentials and raw IP addresses out of every log line.

Audit 2026-09-29 (#9, #16): ``logs/api.log`` holds e-mails, RAW IPs and user
agents; auth warnings print the client IP; the "SMTP not configured" fallback
printed a live password-reset LINK (a bearer credential for the account) into
the log.  Logs are copied to backups and read by more people than the identity
store, so they must never be the easiest place to find a secret.

What :func:`redact` does to a message, in this order:

* ``Authorization: Bearer <x>`` / ``Bearer <x>``          -> ``Bearer [REDACTED]``
* any JWT (``eyJ….….…``)                                   -> ``[JWT]``
* our own credentials: agent keys ``emk_…``, OAuth ``emo_…`` / ``emr_…``
                                                           -> ``emk_[REDACTED]``
* ``password=… / token=… / secret=… / api_key=… / code=…`` in query strings,
  ``k=v`` pairs and JSON (``"password": "…"``)             -> ``[REDACTED]``
* every IPv4 / IPv6 address (validated with :mod:`ipaddress`, so a clock time
  like ``12:34:56`` is not mistaken for one) except loopback / unspecified
                                                           -> ``ip:<12 hex>``

The IP hash is keyed (HMAC-SHA256) with a per-deployment salt, so the same
visitor hashes the same way across days (forensics still works: "is this the
same client?") but the address cannot be recovered by brute-forcing 2^32
IPv4s without the salt.  E-mail addresses are NOT redacted: they are the
account identifier the operator needs to answer a support request, and they
are pseudonymised at account deletion / retention instead.

:class:`RedactingFilter` applies it to a ``LogRecord``; :func:`install`
attaches it to every handler of the root logger and of uvicorn's loggers.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import os
import re
from pathlib import Path
from typing import Optional

_REDACTED = "[REDACTED]"

_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{6,}")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
_OWN_TOKEN_RE = re.compile(r"\b(em[kor]_)[A-Za-z0-9_-]{6,}")
_SECRET_KEYS = (r"password|passwd|pwd|new_password|old_password|secret|client_secret|"
                r"token|access_token|refresh_token|id_token|credential|api[_-]?key|"
                r"apikey|authorization|code_verifier|code|nl_confirm|"
                r"smtp_password|auth_secret")
# key=value (query strings, k=v log fragments).  Stops at & , ; whitespace quote.
_KV_RE = re.compile(r"(?i)\b(" + _SECRET_KEYS + r")=([^&\s,;'\"]+)")
# "key": "value"  /  'key': 'value'  (JSON / repr of a dict)
_JSON_RE = re.compile(r"(?i)([\"'](?:" + _SECRET_KEYS + r")[\"']\s*:\s*)([\"'])(.*?)(\2)")
# Candidates only; each is validated by ipaddress before it is replaced.
_IPV4_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_IPV6_RE = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])")

_SALT_CACHE: Optional[bytes] = None


def _salt() -> bytes:
    """Per-deployment key for :func:`hash_ip`.

    ``LOG_HASH_SALT`` wins; else ``AUTH_SECRET``; else the ``.auth_secret`` file
    if it already exists (read, NEVER created here — a log filter must not
    mint the deployment's signing secret as a side effect); else a constant
    (still a hash, just not a keyed one).
    """
    global _SALT_CACHE
    if _SALT_CACHE is not None:
        return _SALT_CACHE
    raw = (os.environ.get("LOG_HASH_SALT") or os.environ.get("AUTH_SECRET") or "").strip()
    if not raw:
        try:
            from motor_ai_sim import users as _U
            p = Path(_U._SECRET_FILE)
            if p.is_file():
                raw = p.read_text(encoding="utf-8").strip()
        except Exception:                                   # noqa: BLE001
            raw = ""
    _SALT_CACHE = hmac.new((raw or "motres-log-salt").encode("utf-8"),
                           b"ip-hash-v1", hashlib.sha256).digest()
    return _SALT_CACHE


def reset_salt_cache() -> None:
    """Tests (and a secret rotation) drop the cached key."""
    global _SALT_CACHE
    _SALT_CACHE = None


def hash_ip(ip: str) -> str:
    """``ip:<12 hex>`` for an address; ``""`` for nothing.  Idempotent: an
    already-hashed value passes through unchanged."""
    s = (ip or "").strip()
    if not s:
        return ""
    if s.startswith("ip:"):
        return s
    d = hmac.new(_salt(), s.lower().encode("utf-8"), hashlib.sha256).hexdigest()
    return "ip:" + d[:12]


def _keep_raw(addr) -> bool:
    return bool(addr.is_loopback or addr.is_unspecified)


def _ip_sub(m: "re.Match") -> str:
    txt = m.group(0)
    try:
        addr = ipaddress.ip_address(txt)
    except ValueError:
        return txt
    return txt if _keep_raw(addr) else hash_ip(txt)


def redact(text: str) -> str:
    """The message with credentials removed and IPs hashed.  Never raises."""
    if not text:
        return text
    try:
        s = _BEARER_RE.sub(lambda m: f"{m.group(1)} {_REDACTED}", text)
        s = _JWT_RE.sub("[JWT]", s)
        s = _OWN_TOKEN_RE.sub(lambda m: m.group(1) + _REDACTED, s)
        s = _KV_RE.sub(lambda m: f"{m.group(1)}={_REDACTED}", s)
        s = _JSON_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}{m.group(4)}", s)
        s = _IPV4_RE.sub(_ip_sub, s)
        s = _IPV6_RE.sub(_ip_sub, s)
        return s
    except Exception:                                       # noqa: BLE001
        return text


class RedactingFilter(logging.Filter):
    """Rewrite ``record.msg`` with :func:`redact` applied to the formatted
    message.  Always returns True — it edits, it never drops."""

    def filter(self, record: logging.LogRecord) -> bool:   # noqa: A003
        try:
            msg = record.getMessage()
        except Exception:                                   # noqa: BLE001
            return True
        red = redact(msg)
        if red != msg:
            record.msg = red
            record.args = None
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


_FILTER = RedactingFilter()


def _attach(handler: logging.Handler) -> None:
    if not any(isinstance(f, RedactingFilter) for f in handler.filters):
        handler.addFilter(_FILTER)


def install() -> None:
    """Attach the filter to every handler of the root logger and of uvicorn's
    loggers (uvicorn's access logger does not propagate by default and prints
    the client address on every line).  Idempotent; call again after a new
    handler is added."""
    names = ("", "uvicorn", "uvicorn.access", "uvicorn.error")
    for name in names:
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            _attach(h)
        if name and not any(isinstance(f, RedactingFilter) for f in lg.filters):
            lg.addFilter(_FILTER)
