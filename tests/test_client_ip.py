"""motor_ai_sim.client_ip: forwarding headers are trusted only from loopback
or the EXACT proxy endpoints (the compose service ``web``, resolved), never a
whole private range, and never select a client-controlled hop
(reviews 2026-09-29 #2 and re-review)."""
from __future__ import annotations

import time

import pytest

from motor_ai_sim import client_ip as C

REAL = "203.0.113.50"
WEB = "172.18.0.3"          # what the compose service `web` resolves to
GW = "172.18.0.1"           # the network gateway (host side)


@pytest.fixture(autouse=True)
def _web(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
    monkeypatch.delenv("TRUSTED_PROXY_HOSTS", raising=False)
    table = {"web": [WEB]}
    calls = []

    def fake(host):
        calls.append(host)
        return list(table.get(host, []))
    monkeypatch.setattr(C, "resolver", fake)
    C.reset()
    yield {"table": table, "calls": calls}
    C.reset()


def test_direct_peer_ignores_all_forwarding_headers():
    h = {"x-forwarded-for": "1.2.3.4", "x-real-ip": "5.6.7.8"}
    assert C.resolve("198.51.100.7", h) == "198.51.100.7"


def test_other_bridges_and_ranges_are_not_proxies():
    """172.17.x (compute sandboxes), 172.19.x (the ERP), even another address
    on the web's own network: counted by their own address."""
    h = {"x-real-ip": "9.9.9.9", "x-forwarded-for": "9.9.9.9"}
    for peer in ("172.17.0.4", "172.19.0.2", "172.18.0.7", GW, "10.0.0.5", "192.168.1.9"):
        assert C.resolve(peer, h) == peer, peer


def test_the_resolved_web_container_is_trusted():
    assert C.proxy_addrs() == (WEB,)
    # production chain with the realip include: X-Real-IP is the visitor
    assert C.resolve(WEB, {"x-real-ip": REAL,
                           "x-forwarded-for": f"6.6.6.6, {REAL}, {REAL}"}) == REAL
    # without it (fail-closed gateway detection): X-Real-IP = the gateway, which
    # is not a trusted proxy for the API -> one shared bucket, never spoofable
    assert C.resolve(WEB, {"x-real-ip": GW,
                           "x-forwarded-for": f"6.6.6.6, {REAL}, {GW}"}) == GW


def test_loopback_is_trusted_and_left_hops_never_selected():
    for spoof in ("1.1.1.1", "10.0.0.1", "172.18.0.9", "garbage", "1.1.1.1, 2.2.2.2"):
        assert C.resolve("127.0.0.1", {"x-forwarded-for": f"{spoof}, {REAL}"}) == REAL


def test_malformed_hop_stops_the_walk():
    assert C.resolve("127.0.0.1", {"x-forwarded-for": f"{REAL}, not-an-ip"}) == "127.0.0.1"
    assert C.resolve("127.0.0.1", {"x-real-ip": "1.2.3.4, 5.6.7.8"}) == "127.0.0.1"


def test_unresolvable_web_means_loopback_only(_web):
    _web["table"].clear()
    C.reset()
    assert C.proxy_addrs() == ()
    assert C.resolve(WEB, {"x-real-ip": REAL}) == WEB
    assert C.resolve("127.0.0.1", {"x-real-ip": REAL}) == REAL


def test_explicit_overrides(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXIES", "192.0.2.10")
    assert C.resolve("192.0.2.10", {"x-forwarded-for": REAL}) == REAL
    monkeypatch.setenv("TRUSTED_PROXY_HOSTS", "")
    C.reset()
    assert C.resolve(WEB, {"x-real-ip": REAL}) == WEB            # web no longer named


def test_reresolution_follows_a_recreated_network(_web, monkeypatch):
    assert C.resolve(WEB, {"x-real-ip": REAL}) == REAL
    # the network is recreated: web moves; the old address is someone else now
    _web["table"]["web"] = ["172.21.0.3"]
    monkeypatch.setattr(C, "RESOLVE_EVERY_S", 0.0)
    C.proxy_addrs()                                  # due: background refresh
    for _ in range(100):
        if C.proxy_addrs(refresh=False) == ("172.21.0.3",):
            break
        time.sleep(0.01)
    assert C.proxy_addrs(refresh=False) == ("172.21.0.3",)
    monkeypatch.setattr(C, "RESOLVE_EVERY_S", 3600.0)
    assert C.resolve(WEB, {"x-real-ip": REAL}) == WEB
    assert C.resolve("172.21.0.3", {"x-real-ip": REAL}) == REAL


def test_a_miss_triggers_a_rate_limited_reresolve(_web, monkeypatch):
    monkeypatch.setattr(C, "RESOLVE_EVERY_S", 3600.0)
    C.proxy_addrs()
    n0 = len(_web["calls"])
    _web["table"]["web"] = ["172.22.0.3"]
    C.resolve("172.22.0.3", {"x-real-ip": REAL})     # miss -> background lookup
    for _ in range(100):
        if C.proxy_addrs(refresh=False) == ("172.22.0.3",):
            break
        time.sleep(0.01)
    assert C.resolve("172.22.0.3", {"x-real-ip": REAL}) == REAL
    for _ in range(20):                              # further misses: rate limited
        C.resolve("172.17.0.9", {"x-real-ip": REAL})
    time.sleep(0.05)
    assert len(_web["calls"]) == n0 + 1


def test_warm_resolves_at_startup(_web):
    assert C.warm() == (WEB,)


def test_from_scope_joins_repeated_headers():
    scope = {"client": (WEB, 1), "headers": [
        (b"x-forwarded-for", b"9.9.9.9"), (b"x-forwarded-for", REAL.encode())]}
    assert C.from_scope(scope) == REAL
