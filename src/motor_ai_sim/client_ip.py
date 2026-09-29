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

Who is trusted — EXACT proxy endpoints, never a whole private range (any
other container on a docker bridge must not be able to forge X-Real-IP):

* loopback (``127.0.0.0/8``, ``::1``);
* the addresses the proxy hostnames in ``TRUSTED_PROXY_HOSTS`` resolve to
  (default ``web``, the compose service of the web container).  Resolved at
  API startup (``warm``), re-resolved every ``RESOLVE_EVERY_S`` and, rate
  limited, when a peer misses — so a recreated network with a new subnet is
  followed without configuration.  A name that does not resolve (local dev
  without docker) adds nothing: loopback only;
* ``TRUSTED_PROXIES``: explicit extra addresses / CIDRs, empty by default.

A peer that is not an IP address at all (a unix-socket proxy, the in-process
test client) is a local hop and counts as trusted.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import threading
import time
from typing import Callable, List, Mapping, Optional

LOOPBACK = ("127.0.0.0/8", "::1/128")
DEFAULT_PROXY_HOSTS = "web"
RESOLVE_EVERY_S = 60.0
#: a peer that misses may trigger a re-resolution at most this often
MISS_RETRY_S = 10.0


def _lookup(host: str) -> List[str]:
    """All addresses ``host`` resolves to ([] when it does not)."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return []
    return sorted({str(i[4][0]).split("%", 1)[0] for i in infos})


#: replaceable in tests
resolver: Callable[[str], List[str]] = _lookup

_lock = threading.Lock()
_state = {"key": None, "addrs": (), "at": 0.0, "miss_at": 0.0, "busy": False, "gen": 0}


def _hosts() -> List[str]:
    raw = os.environ.get("TRUSTED_PROXY_HOSTS")
    spec = DEFAULT_PROXY_HOSTS if raw is None else raw
    return [h.strip() for h in spec.split(",") if h.strip()]


def _do_resolve(hosts: List[str]) -> None:
    with _lock:
        gen = _state["gen"]
    addrs: List[str] = []
    try:
        for h in hosts:
            addrs.extend(a for a in resolver(h) if a)
    except Exception:                             # noqa: BLE001 — fail closed
        addrs = []
    finally:
        with _lock:
            if _state["gen"] == gen:              # a reset() in between wins
                _state.update(key=tuple(hosts), addrs=tuple(sorted(set(addrs))),
                              at=time.monotonic(), busy=False)


def _refresh_async(hosts: List[str]) -> None:
    with _lock:
        if _state["busy"]:
            return
        _state["busy"] = True
    threading.Thread(target=_do_resolve, args=(hosts,), daemon=True,
                     name="trusted-proxy-resolve").start()


def warm(timeout_s: float = 2.0) -> tuple:
    """Resolve the proxy hostnames now (API startup); never blocks longer
    than ``timeout_s`` — a slow DNS just leaves loopback-only trust until the
    background lookup lands."""
    hosts = _hosts()
    with _lock:
        _state["busy"] = True
    t = threading.Thread(target=_do_resolve, args=(hosts,), daemon=True,
                         name="trusted-proxy-resolve")
    t.start()
    t.join(timeout_s)
    return proxy_addrs(refresh=False)


def reset() -> None:
    with _lock:
        _state.update(key=None, addrs=(), at=0.0, miss_at=0.0, busy=False,
                      gen=_state["gen"] + 1)


def proxy_addrs(refresh: bool = True) -> tuple:
    """The resolved proxy addresses (cached; refreshed in the background)."""
    hosts = _hosts()
    with _lock:
        key, addrs, at, busy = _state["key"], _state["addrs"], _state["at"], _state["busy"]
    if refresh and (key != tuple(hosts) or time.monotonic() - at >= RESOLVE_EVERY_S):
        if key != tuple(hosts) and not busy:
            with _lock:
                _state["busy"] = True
            _do_resolve(hosts)                     # first use / config changed
            with _lock:
                addrs = _state["addrs"]
        elif key != tuple(hosts):
            addrs = ()                             # being resolved: loopback only meanwhile
        else:
            _refresh_async(hosts)
    return addrs


def _note_miss() -> None:
    """A peer that looked like a proxy hop missed: maybe the network was
    recreated.  Re-resolve in the background, at most every MISS_RETRY_S."""
    now = time.monotonic()
    with _lock:
        if now - _state["miss_at"] < MISS_RETRY_S:
            return
        _state["miss_at"] = now
    _refresh_async(_hosts())


def _nets():
    specs = list(LOOPBACK)
    specs += [a for a in proxy_addrs()]
    specs += (os.environ.get("TRUSTED_PROXIES") or "").split(",")
    out = []
    for part in specs:
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
        if headers.get("x-real-ip") or headers.get("x-forwarded-for"):
            _note_miss()      # a proxy we no longer recognise? re-resolve (rate limited)
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
