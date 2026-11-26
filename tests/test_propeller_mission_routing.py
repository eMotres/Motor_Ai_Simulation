"""The Mission tab's routes: compute-worker routing and the central auth gate (Astra review of PR #915, findings 1 and 3).

Real role resolution (registry, signed token, middleware); only the file paths are redirected.  The change is additive: the
full gate table and the worker prefix list are compared with a literal expectation."""
from __future__ import annotations

import re

import pytest

from motor_ai_sim import roles
from tests.test_auth_sessions import ADMIN, CLIENT, _bearer, _login, client, env  # noqa: F401  (fixture)

MISSION = "/api/propellers/mission"
ENG = "engineer@example.com"
OEM = "factory@example.com"


@pytest.fixture()
def accounts(env, monkeypatch):
    U = env["U"]
    U.create_user(ENG, "password-eng", role="engineer", name="Eng")
    U.create_user(OEM, "password-oem", role="oem", name="Factory")
    monkeypatch.setattr(env["A"], "AUTH_ENFORCE", True)
    return env


def _code(method, path, who=None):
    headers = {}
    if who:
        pw = {ADMIN: "password-admin", CLIENT: "password-client", ENG: "password-eng", OEM: "password-oem"}[who]
        headers = _bearer(_login(who, pw)["token"])
    r = client.request(method, path, headers=headers, json={} if method == "POST" else None)
    return r.status_code


PATHS = [("GET", MISSION + "/options"), ("GET", MISSION + "/runs"), ("POST", MISSION + "/run"), ("POST", MISSION),
         ("DELETE", MISSION + "/runs/abc")]


def test_anonymous_is_refused_401(accounts):
    for m, p in PATHS:
        assert _code(m, p) == 401, (m, p)


def test_user_and_oem_are_refused_403_in_both_solve_modes(accounts, monkeypatch):
    for flag in ("1", "0"):
        monkeypatch.setenv("MOTRES_SOLVE_ADMIN_ONLY", flag)
        for who in (CLIENT, OEM):
            for m, p in PATHS:
                assert _code(m, p, who) == 403, (flag, who, m, p)


def test_engineer_is_refused_while_solves_are_admin_only_and_allowed_when_off(accounts, monkeypatch):
    monkeypatch.setenv("MOTRES_SOLVE_ADMIN_ONLY", "1")
    for m, p in PATHS:
        assert _code(m, p, ENG) == 403, (m, p)
    monkeypatch.setenv("MOTRES_SOLVE_ADMIN_ONLY", "0")
    for m, p in PATHS:
        assert _code(m, p, ENG) not in (401, 403), (m, p)     # past the gate (the route itself is a later PR: 404/405/422 is fine)


def test_admin_is_allowed_in_both_modes(accounts, monkeypatch):
    for flag in ("1", "0"):
        monkeypatch.setenv("MOTRES_SOLVE_ADMIN_ONLY", flag)
        for m, p in PATHS:
            assert _code(m, p, ADMIN) not in (401, 403), (flag, m, p)


def test_local_mode_has_no_gate(env, monkeypatch):
    monkeypatch.setattr(env["A"], "AUTH_ENFORCE", False)
    monkeypatch.setattr(env["A"], "_ADMIN_EMAILS", set())
    monkeypatch.setenv("MOTRES_SOLVE_ADMIN_ONLY", "1")
    for m, p in PATHS:
        assert _code(m, p) not in (401, 403), (m, p)


def test_the_tier_table_knows_the_mission_routes(env, monkeypatch):
    A = env["A"]
    for m, p in PATHS:
        assert A.raw_tier(m, p) == "solve:engineer" and A.is_solve_route(m, p), (m, p)
    monkeypatch.setenv("MOTRES_SOLVE_ADMIN_ONLY", "1")
    assert A.required_role("GET", MISSION + "/runs") == "admin"
    monkeypatch.setenv("MOTRES_SOLVE_ADMIN_ONLY", "0")
    assert A.required_role("GET", MISSION + "/runs") == "engineer"
    # the neighbours keep their tier: the catalogue stays open, the designer stays a solve
    for p in ("/api/propellers", "/api/propellers/x", "/api/propellers/x/point", "/api/propellers/cooling-options"):
        assert A.raw_tier("GET", p) is None, p
    assert A.raw_tier("GET", "/api/propellers/design/family") == "solve:engineer"


def test_only_the_mission_prefix_is_routed_to_the_worker():
    for p in (MISSION, MISSION + "/run", MISSION + "/runs/abc"):
        assert roles.is_worker_path(p) and re.search(roles.nginx_regex(), p), p
    for p in ("/api/propellers", "/api/propellers/x", "/api/propellers/design/evaluate", "/api/propellers/cooling-options",
              "/api/propellers/missions"):
        assert not roles.is_worker_path(p), p
        assert not re.search(roles.nginx_regex(), p), p


_BASE_WORKER_PREFIXES = (
    "/api/simulation", "/api/static3d", "/api/mechanical", "/api/thermal", "/api/coupled", "/api/optimization",
    "/api/controller", "/api/system", "/api/kernel",
    "/api/jobs", "/api/history", "/api/sims",
    "/api/geometry", "/api/config", "/api/winding", "/api/mesh", "/api/parts",
    "/api/family/report",
    "/mcp",
)


def test_worker_prefixes_differ_from_the_base_only_by_the_mission_entry():
    assert set(roles.WORKER_PREFIXES) == set(_BASE_WORKER_PREFIXES) | {MISSION}
    assert len(roles.WORKER_PREFIXES) == len(set(roles.WORKER_PREFIXES)) == len(_BASE_WORKER_PREFIXES) + 1


def test_gated_prefix_table_differs_from_the_base_only_by_the_mission_entries(env):
    A = env["A"]
    new = {(m, MISSION, "solve:engineer") for m in ("GET", "POST", "DELETE")}
    got = list(A._GATED_PREFIX)
    assert new <= set(got) and len(got) == len(set(got))
    rest = [e for e in got if e not in new]
    # the designer entries sit directly before the new ones and are untouched
    assert [e for e in rest if e[1] == "/api/propellers/design"] == [
        (m, "/api/propellers/design", "solve:engineer") for m in ("GET", "POST", "PUT", "DELETE")]
    assert got.index(("GET", MISSION, "solve:engineer")) > got.index(("DELETE", "/api/propellers/design", "solve:engineer"))
    assert len(rest) == len(got) - 3 == 55      # 55 base entries on pre-migration-freeze-2026-09-15
