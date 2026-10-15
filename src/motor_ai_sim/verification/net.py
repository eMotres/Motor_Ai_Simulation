"""Network access of the verification, all of it behind two small replaceable objects (so tests never touch the network):

* :class:`Http` - ``get`` / ``post_json`` with short timeouts and a size cap.  ``public_only=True`` is for every URL that a person
  typed (the website, the pages they point to): the scheme must be http(s), the port 80 / 443, and every address the host
  resolves to - at the start and after each redirect - must be a global one (no loopback, private, link-local, reserved
  range).  The fetched text is never shown to anybody except as a yes/no finding.
* :func:`mx_records` - a stdlib DNS MX query over UDP (the same approach as :mod:`motor_ai_sim.email_domain`, no dependency).

Residual risk, documented in docs/APPLICANT_VERIFICATION.md: the address is checked before the request is made, so a hostile DNS
server could change its answer between the check and the connection (DNS rebinding); the response is never returned to the
caller, which limits what that could leak to a yes / no.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import random
import re
import socket
import struct
import urllib.parse
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable, Optional

from motor_ai_sim.verification import config as C

log = logging.getLogger(__name__)


@dataclass
class HttpResult:
    url: str
    status: int = 0                        # 0 = no HTTP answer (error holds why)
    text: str = ""
    headers: dict = field(default_factory=dict)
    error: str = ""
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self):
        try:
            return json.loads(self.text)
        except ValueError:
            return None


# -- SSRF guard ------------------------------------------------------------------------------------------------------

def _resolve_all(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({i[4][0] for i in infos})


def check_public_url(url: str, resolver: Optional[Callable[[str], list]] = None) -> Optional[str]:
    """None when the URL may be fetched, else the reason it may not (a short code)."""
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return "bad_url"
    if u.scheme not in ("http", "https") or not u.hostname:
        return "scheme"
    if u.username or u.password:
        return "credentials"
    try:
        port = u.port
    except ValueError:
        return "bad_port"
    if port not in (None, 80, 443):
        return "port"
    host = u.hostname
    try:
        ipaddress.ip_address(host)
        ips = [host]
    except ValueError:
        try:
            ips = (resolver or _resolve_all)(host)
        except (OSError, UnicodeError):
            return "unresolvable"
    if not ips:
        return "unresolvable"
    for ip in ips:
        try:
            if not ipaddress.ip_address(ip).is_global:
                return "not_public"
        except ValueError:
            return "bad_address"
    return None


class Http:
    """The default client (httpx).  Tests replace it with a fake that has the same two methods (see :func:`set_http`)."""

    def __init__(self, resolver: Optional[Callable[[str], list]] = None):
        self._resolver = resolver

    def _client(self, timeout: float):
        import httpx
        return httpx.Client(timeout=timeout, follow_redirects=False, headers={"User-Agent": C.USER_AGENT, "Accept": "*/*"})

    def get(self, url: str, *, headers: Optional[dict] = None, auth: Optional[tuple] = None, timeout: Optional[float] = None,
            max_bytes: Optional[int] = None, public_only: bool = False) -> HttpResult:
        timeout = timeout or C.HTTP_TIMEOUT_S
        cap = max_bytes or C.FETCH_MAX_BYTES
        cur = url
        try:
            with self._client(timeout) as cl:
                for _hop in range(C.FETCH_MAX_REDIRECTS + 1):
                    if public_only:
                        why = check_public_url(cur, self._resolver)
                        if why:
                            return HttpResult(cur, 0, error="blocked:" + why)
                    with cl.stream("GET", cur, headers=headers, auth=auth) as r:
                        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                            cur = urllib.parse.urljoin(cur, r.headers["location"])
                            continue
                        buf, trunc = b"", False
                        for chunk in r.iter_bytes():
                            buf += chunk
                            if len(buf) > cap:
                                buf, trunc = buf[:cap], True
                                break
                        enc = r.encoding or "utf-8"
                        return HttpResult(cur, r.status_code, buf.decode(enc, errors="replace"), dict(r.headers), truncated=trunc)
                return HttpResult(cur, 0, error="too_many_redirects")
        except Exception as e:                                   # noqa: BLE001 - any failure is "unknown", never a pass
            return HttpResult(cur, 0, error="%s: %s" % (type(e).__name__, str(e)[:160]))

    def post_json(self, url: str, body: dict, *, timeout: Optional[float] = None) -> HttpResult:
        try:
            with self._client(timeout or C.HTTP_TIMEOUT_S) as cl:
                r = cl.post(url, json=body)
                return HttpResult(url, r.status_code, r.text[: C.FETCH_MAX_BYTES], dict(r.headers))
        except Exception as e:                                   # noqa: BLE001
            return HttpResult(url, 0, error="%s: %s" % (type(e).__name__, str(e)[:160]))


_http: object = Http()


def get_http():
    return _http


def set_http(h) -> None:
    """Replace the HTTP client (tests, or an operator proxy)."""
    global _http
    _http = h


# -- HTML to text ----------------------------------------------------------------------------------------------------

class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self.meta: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            d = dict(attrs)
            if (d.get("property") in ("og:site_name", "og:title") or d.get("name") in ("author", "application-name", "description")) and d.get("content"):
                self.meta.append(d["content"])
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    try:
        p.feed(html or "")
        p.close()
    except Exception:                                            # noqa: BLE001 - broken markup: take what was parsed
        pass
    return re.sub(r"\s+", " ", " ".join([p.title, *p.meta, *p.parts])).strip()


# -- polite page fetch (robots.txt) ------------------------------------------------------------------------------------

@dataclass
class Page:
    url: str
    ok: bool
    status: int = 0
    text: str = ""
    error: str = ""                       # '' | 'robots' | 'blocked:...' | 'http_404' | 'ConnectError: ...'


def robots_allows(url: str, http=None, cache: Optional[dict] = None) -> bool:
    """robots.txt of the host for our user agent.  A missing / unreadable robots.txt allows (the usual convention).  ``cache``
    ({origin: parser or None}) keeps the file for the pages of one run."""
    from urllib.robotparser import RobotFileParser
    u = urllib.parse.urlsplit(url)
    origin = "%s://%s" % (u.scheme, u.netloc)
    if cache is not None and origin in cache:
        rp = cache[origin]
    else:
        r = (http or get_http()).get(origin + "/robots.txt", public_only=True, max_bytes=100_000)
        rp = None
        if r.ok:
            rp = RobotFileParser()
            rp.parse(r.text.splitlines())
        if cache is not None:
            cache[origin] = rp
    return True if rp is None else rp.can_fetch("AeroStatorVerification", url)


def fetch_page(url: str, http=None, robots: Optional[dict] = None) -> Page:
    http = http or get_http()
    if not robots_allows(url, http, robots):
        return Page(url, False, 0, error="robots")
    r = http.get(url, public_only=True)
    if r.error:
        return Page(url, False, 0, error=r.error)
    if not r.ok:
        return Page(url, False, r.status, error="http_%d" % r.status)
    return Page(r.url, True, r.status, html_to_text(r.text))


# -- DNS MX ----------------------------------------------------------------------------------------------------------

def _name_end(data: bytes, off: int) -> int:
    """Offset just after the (possibly compressed) name at ``off``."""
    while True:
        n = data[off]
        if n == 0:
            return off + 1
        if n & 0xC0 == 0xC0:
            return off + 2
        off += 1 + n


def _read_name(data: bytes, off: int, depth: int = 0) -> str:
    labels = []
    while depth < 20:
        n = data[off]
        if n == 0:
            break
        if n & 0xC0 == 0xC0:
            return ".".join(labels + [_read_name(data, ((n & 0x3F) << 8) | data[off + 1], depth + 1)]).strip(".")
        labels.append(data[off + 1: off + 1 + n].decode("ascii", "replace"))
        off += 1 + n
    return ".".join(labels)


def parse_mx(data: bytes) -> Optional[list]:
    """MX answers of a DNS response -> [(preference, host)], [] when the answer holds none, None when the response is unusable.
    NXDOMAIN (rcode 3) returns []."""
    if len(data) < 12:
        return None
    rcode = data[3] & 0x0F
    if rcode == 3:
        return []
    if rcode != 0:
        return None
    qd, an = struct.unpack(">HH", data[4:8])
    off = 12
    try:
        for _ in range(qd):
            off = _name_end(data, off) + 4
        out = []
        for _ in range(an):
            off = _name_end(data, off)
            rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", data[off: off + 10])
            off += 10
            if rtype == 15:
                pref = struct.unpack(">H", data[off: off + 2])[0]
                out.append((pref, _read_name(data, off + 2)))
            off += rdlen
        return out
    except (IndexError, struct.error):
        return None


def _query(domain: str, qtype: int, server: str, timeout: float) -> Optional[bytes]:
    qid = random.getrandbits(16)
    q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    for label in domain.split("."):
        q += bytes([len(label)]) + label.encode("ascii")
    q += b"\x00" + struct.pack(">HH", qtype, 1)
    fam = socket.AF_INET6 if ":" in server else socket.AF_INET
    try:
        with socket.socket(fam, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(q, (server, 53))
            data, _ = s.recvfrom(4096)
    except OSError:
        return None
    return data if len(data) >= 12 and struct.unpack(">H", data[:2])[0] == qid else None


FALLBACK_RESOLVERS = ("1.1.1.1", "9.9.9.9")


def mx_records(domain: str, timeout: float = 3.0) -> Optional[list]:
    """[(preference, host)] of the domain; [] = the domain has no MX (or does not exist); None = could not tell."""
    from motor_ai_sim import email_domain as ED
    try:
        d = domain.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    for server in (ED._resolvers() or list(FALLBACK_RESOLVERS)):
        data = _query(d, 15, server, timeout)
        if data is None:
            continue
        res = parse_mx(data)
        if res is not None:
            return res
    return None


_mx_impl: Callable[[str], Optional[list]] = mx_records


def set_mx(fn: Optional[Callable[[str], Optional[list]]]) -> None:
    """Replace the MX lookup (tests); None restores the real one."""
    global _mx_impl
    _mx_impl = fn or mx_records


def lookup_mx(domain: str) -> Optional[list]:
    return _mx_impl(domain)
