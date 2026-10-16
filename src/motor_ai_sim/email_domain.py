"""Does the domain of an e-mail address exist?  Checked BEFORE a verification or reset mail is sent.

Why: a sign-up with a mistyped domain (2026-10-15: ``d.xu@movigor.tech``, a domain that does not exist) bounces both the
confirmation and the password-reset mail, and the person is locked out with no way to know why.

Stdlib only (no new dependency), short timeout, fail OPEN:

1. ``socket.getaddrinfo(domain)`` (A / AAAA) in a worker thread with a 2 s limit.  It resolves -> the domain exists.
2. It does not resolve with a TEMPORARY error or the limit expires -> do not block, send.
3. It does not resolve with "name unknown": ``getaddrinfo`` cannot tell a domain that does not exist (NXDOMAIN) from one that has
   only MX records and no address (valid for mail), so the answer is confirmed with one minimal DNS query (type A) over UDP to the
   system resolver (``/etc/resolv.conf``); only the response code is read: 3 = NXDOMAIN -> the domain does not exist, 0 -> it exists
   (an MX-only domain), anything else, a timeout, or no resolver configured (a Windows workstation) -> do not block.

Results are cached per domain for ten minutes.  No MX lookup, no mailbox probing, and no guessing of company domains: only obvious
typos of big mail providers get a "did you mean" suggestion.
"""
from __future__ import annotations

import random
import re
import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

TIMEOUT_S = 2.0
CACHE_S = 600.0

#: obvious typos of big providers -> the right domain.  Never a guess about a company domain.
TYPOS = {
    "gmial.com": "gmail.com", "gmai.com": "gmail.com", "gmail.con": "gmail.com", "gmail.co": "gmail.com", "gmaill.com": "gmail.com",
    "gnail.com": "gmail.com", "gmil.com": "gmail.com", "gmail.cm": "gmail.com",
    "qq.con": "qq.com", "qq.cm": "qq.com", "qqq.com": "qq.com", "qq.co": "qq.com",
    "163.con": "163.com", "163.cm": "163.com", "163.co": "163.com", "126.con": "126.com", "126.cm": "126.com",
    "outlok.com": "outlook.com", "outloo.com": "outlook.com", "outlook.con": "outlook.com",
    "hotmial.com": "hotmail.com", "hotmai.com": "hotmail.com", "hotmail.con": "hotmail.com",
    "yaho.com": "yahoo.com", "yahooo.com": "yahoo.com", "yahoo.con": "yahoo.com",
    "icloud.con": "icloud.com", "iclod.com": "icloud.com", "sina.con": "sina.com", "foxmail.con": "foxmail.com",
}

_LABEL_RE = re.compile(r"^[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?$")
_cache: dict[str, tuple[float, str]] = {}
_lock = threading.Lock()


@dataclass
class DomainCheck:
    ok: bool                      # False only when the domain is known not to exist
    domain: str
    reason: str = ""              # "exists" | "nxdomain" | "unknown" (could not tell: not blocked) | "invalid"
    suggestion: str = ""          # a corrected domain for an obvious provider typo, else ""


def domain_of(email: str) -> str:
    return (email or "").rpartition("@")[2].strip().lower().rstrip(".")


def suggest(domain: str) -> str:
    return TYPOS.get(domain.lower(), "")


def _ascii(domain: str) -> Optional[str]:
    try:
        d = domain.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    labels = d.split(".")
    if len(d) > 253 or len(labels) < 2 or not all(_LABEL_RE.match(x) for x in labels):
        return None
    return d


def _getaddrinfo(domain: str, timeout: float) -> str:
    """'yes' | 'no' (name unknown) | 'unknown' (temporary failure or timeout)."""
    out: list[str] = []

    def work() -> None:
        try:
            socket.getaddrinfo(domain, None)
            out.append("yes")
        except socket.gaierror as e:
            out.append("no" if e.errno in _NONAME else "unknown")
        except Exception:                                    # noqa: BLE001
            out.append("unknown")

    t = threading.Thread(target=work, daemon=True, name="email-domain-check")
    t.start()
    t.join(timeout)
    return out[0] if out else "unknown"


_NONAME = {getattr(socket, n) for n in ("EAI_NONAME", "EAI_NODATA") if hasattr(socket, n)}


def _resolvers() -> list[str]:
    try:
        with open("/etc/resolv.conf", encoding="utf-8", errors="replace") as f:
            return [p[1] for p in (ln.split() for ln in f if ln.startswith("nameserver")) if len(p) > 1][:2]
    except OSError:
        return []


def _dns_rcode(domain: str, server: str, timeout: float, port: int = 53) -> Optional[int]:
    """Response code of one UDP query (type A, class IN) for ``domain``; None on any failure."""
    qid = random.getrandbits(16)
    q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    for label in domain.split("."):
        q += bytes([len(label)]) + label.encode("ascii")
    q += b"\x00" + struct.pack(">HH", 1, 1)
    fam = socket.AF_INET6 if ":" in server else socket.AF_INET
    try:
        with socket.socket(fam, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(q, (server, port))
            data, _addr = s.recvfrom(1500)
    except OSError:
        return None
    if len(data) < 12 or struct.unpack(">H", data[:2])[0] != qid:
        return None
    return data[3] & 0x0F


def _exists(domain: str, timeout: float) -> str:
    """'yes' | 'no' | 'unknown'."""
    first = _getaddrinfo(domain, timeout)
    if first != "no":
        return first
    for server in _resolvers():
        rc = _dns_rcode(domain, server, timeout)
        if rc == 3:
            return "no"
        if rc == 0:
            return "yes"
    return "unknown"


def check(email: str, *, resolver: Optional[Callable[[str, float], str]] = None, timeout: float = TIMEOUT_S) -> DomainCheck:
    """The verdict for the domain of ``email``.  ``resolver(domain, timeout) -> 'yes'|'no'|'unknown'`` is for tests."""
    raw = domain_of(email)
    d = _ascii(raw)
    if d is None:
        return DomainCheck(True, raw, "invalid")                 # not this module's job to refuse a format: _valid_email does
    now = time.time()
    with _lock:
        hit = _cache.get(d)
        if hit and now - hit[0] < CACHE_S and resolver is None:
            verdict = hit[1]
        else:
            verdict = None
    if verdict is None:
        verdict = (resolver or _exists)(d, timeout)
        if resolver is None and verdict in ("yes", "no"):
            with _lock:
                _cache[d] = (now, verdict)
                if len(_cache) > 5000:
                    _cache.clear()
    if verdict == "no":
        return DomainCheck(False, raw, "nxdomain", suggest(raw))
    return DomainCheck(True, raw, "exists" if verdict == "yes" else "unknown")


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def message(c: DomainCheck) -> str:
    """The English sentence for a domain that does not exist (the web translates it by code ``auth.email_domain_unknown``)."""
    tail = f" (did you mean {c.suggestion}?)" if c.suggestion else ""
    return f"The domain {c.domain} does not exist - please check the address{tail}."
