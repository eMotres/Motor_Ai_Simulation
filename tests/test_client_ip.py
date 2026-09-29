"""motor_ai_sim.client_ip: forwarding headers are trusted only from a trusted
proxy peer, and never select a client-controlled hop (review 2026-09-29 #2)."""
from __future__ import annotations

import pytest

from motor_ai_sim import client_ip as C

REAL = "203.0.113.50"


@pytest.fixture(autouse=True)
def _default_trust(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)


def test_direct_peer_ignores_all_forwarding_headers():
    h = {"x-forwarded-for": "1.2.3.4", "x-real-ip": "5.6.7.8"}
    assert C.resolve("198.51.100.7", h) == "198.51.100.7"


def test_production_chain_before_and_after_the_realip_change():
    # container nginx without realip: X-Real-IP = bridge gateway (trusted)
    xff = f"6.6.6.6, {REAL}, 172.18.0.1"
    assert C.resolve("172.18.0.3", {"x-forwarded-for": xff, "x-real-ip": "172.18.0.1"}) == REAL
    # with realip: X-Real-IP is the visitor
    assert C.resolve("172.18.0.3", {"x-forwarded-for": f"6.6.6.6, {REAL}, {REAL}",
                                    "x-real-ip": REAL}) == REAL


def test_spoofed_left_hops_are_never_selected():
    for spoof in ("1.1.1.1", "10.0.0.1", "172.18.0.9", "garbage", "1.1.1.1, 2.2.2.2"):
        assert C.resolve("127.0.0.1", {"x-forwarded-for": f"{spoof}, {REAL}"}) == REAL


def test_malformed_hop_stops_the_walk():
    # the rightmost untrusted position is malformed: nothing left of it counts
    assert C.resolve("127.0.0.1", {"x-forwarded-for": f"{REAL}, not-an-ip"}) == "127.0.0.1"
    assert C.resolve("127.0.0.1", {"x-real-ip": "1.2.3.4, 5.6.7.8"}) == "127.0.0.1"


def test_trusted_proxies_env(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXIES", "192.0.2.10")
    assert C.resolve("192.0.2.10", {"x-forwarded-for": REAL}) == REAL
    assert C.resolve("127.0.0.1", {"x-forwarded-for": REAL}) == "127.0.0.1"   # no longer trusted
    monkeypatch.setenv("TRUSTED_PROXIES", "")
    assert C.resolve("172.18.0.3", {"x-forwarded-for": REAL}) == "172.18.0.3"


def test_from_scope_joins_repeated_headers():
    scope = {"client": ("172.18.0.3", 1), "headers": [
        (b"x-forwarded-for", b"9.9.9.9"), (b"x-forwarded-for", REAL.encode())]}
    assert C.from_scope(scope) == REAL
