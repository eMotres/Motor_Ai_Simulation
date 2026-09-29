"""The visitor's IP address for per-IP limits, behind a TRUSTED-proxy boundary.

Forwarding headers are client-controlled unless a proxy WE run wrote them, so
they are honoured only when the TCP peer (``scope["client"]``) is a trusted
proxy.  Production chain (docs/MCP_DISCOVERY.md "Client IP"):

    visitor -> host nginx (TLS; X-Real-IP = $remote_addr, XFF appended)
            -> web container nginx on the docker bridge (realip from the host's
               X-Real-IP; X-Real-IP = that; XFF appended)
            -> api

Rules, for a request whose peer is trusted:

1. ``X-Real-IP`` (one valid address, overwritten by each of our proxies) when
   it is not itself a trusted proxy address;
2. else the RIGHTMOST hop of ``X-Forwarded-For`` that is not a trusted proxy
   (every hop right of it was appended by our own proxies; everything left of
   it is whatever the client sent and is never selected); a malformed hop met
   before an untrusted one ends the walk (nothing left of it can be trusted);
3. else the peer itself.

A request from an untrusted peer (someone reaching the API directly) is
counted by its peer address, whatever headers it carries.

``TRUSTED_PROXIES``: comma-separated addresses / CIDRs; default loopback plus
the docker bridge ranges (``127.0.0.0/8, ::1/128, 172.16.0.0/12``).  A peer
that is not an IP address at all (a unix-socket proxy, the in-process test
client) is a local hop and counts as trusted.
"""
from __future__ import annotations

import ipaddress
import os
from typing import Mapping, Optional

DEFAULT_TRUSTED = "127.0.0.0/8,::1/128,172.16.0.0/12"


def _nets():
    raw = os.environ.get("TRUSTED_PROXIES")
    spec = DEFAULT_TRUSTED if raw is None else raw
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return tuple(out)


def _ip(v: str) -> Optional[ipaddress._BaseAddress]:
    try:
        return ipaddress.ip_address((v or "").strip())
    except ValueError:
        return None


def is_trusted(addr: str, nets=None) -> bool:
    a = _ip(addr)
    if a is None:
        return False
    nets = _nets() if nets is None else nets
    return any(a.version == n.version and a in n for n in nets)


def resolve(peer: Optional[str], headers: Mapping[str, str]) -> str:
    """``peer``: the TCP peer host; ``headers``: lower-cased request headers."""
    peer = (peer or "").strip()
    nets = _nets()
    peer_is_ip = _ip(peer) is not None
    if peer_is_ip and not is_trusted(peer, nets):
        return peer                               # direct caller: headers ignored
    real = (headers.get("x-real-ip") or "").strip()
    if real and "," not in real and _ip(real) is not None and not is_trusted(real, nets):
        return str(_ip(real))
    xff = headers.get("x-forwarded-for") or ""
    for hop in reversed([h.strip() for h in xff.split(",")]):
        a = _ip(hop)
        if a is None:
            break                                 # malformed: stop, never go left of it
        if not is_trusted(hop, nets):
            return str(a)
    return peer or "?"


def from_scope(scope) -> str:
    """Resolve from an ASGI scope (headers are bytes pairs)."""
    client = scope.get("client") or None
    peer = str(client[0]) if client else ""
    hdrs = {}
    for k, v in scope.get("headers") or ():
        name = k.decode("latin-1").lower()
        if name in ("x-real-ip", "x-forwarded-for"):
            val = v.decode("latin-1")
            # repeated headers: join like one comma-separated list, in order
            hdrs[name] = f"{hdrs[name]},{val}" if name in hdrs else val
    return resolve(peer, hdrs)


def from_request(request) -> str:
    """Resolve from a Starlette/FastAPI ``Request``."""
    if request is None:
        return "?"
    try:
        return from_scope(request.scope)
    except Exception:                             # noqa: BLE001
        c = getattr(request, "client", None)
        return getattr(c, "host", "") or "?"
