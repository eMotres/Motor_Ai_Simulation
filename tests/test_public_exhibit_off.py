"""PUBLIC_EXHIBIT=0 — the internet-facing server shows an anonymous visitor nothing.

The public exhibit (``motor_access.MODE_ANONYMOUS`` plus the passport filter in
``routes/family.tree``) was written when this app only ever ran on the owner's
workstation: an anonymous caller could read the tree of "public" dies, their
full geometry and duty payloads, and could BUILD THE CONFIDENTIAL REPORT
(``GET /api/family/report/{die}/{cfg}``).  On a host reachable from the internet
that is the wrong default — the standing rule is that a new visitor sees NOTHING
until an account is granted something.

``PUBLIC_EXHIBIT`` is the switch, and it is ONE gate in
``auth.TierGateMiddleware``, not a rule repeated per route:

* unset / ``1`` — today's behaviour, byte for byte.  This workstation and every
  other test in the suite never set it, so nothing they assert can move.
* ``0`` — a caller with no valid credentials gets **401 on every /api route**
  except ``/api/health``, ``/api/me`` (which must still answer, in its anonymous
  shape, or the SPA cannot tell "not signed in" from "server down") and the
  sign-in endpoints ``/api/auth/login`` | ``/api/auth/google`` | ``/api/auth/logout``.
  A REGISTERED caller is unaffected: the tier table and the per-account motor
  grants decide exactly what they decided before.

Every path in ``CLOSED`` is checked against the app's real route table first —
with the door closed a typo would 401 just as convincingly as a real route, so
the list would rot into a test of nothing.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.auth import anonymous_allowed, public_exhibit

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"
_REAL_DIES = _ROOT / "config" / "dies"

ADMIN = "owner@example.com"
CLIENT = "client@example.com"
CLIENT_PW = "password-client"

client = TestClient(app)

# A representative slice of the surface: the family tree, the payload, the
# datasheet and THE REPORT (the four that leaked engineering data), plus one
# route from every other router — geometry, materials, config, simulation,
# thermal, mechanical, coupled, optimization, jobs, community/publish, presets,
# catalog, my-motors, saved sims, sweep, panel settings, bearings, modules,
# kernel, static3d, support, fusion, freecad, admin and the admin half of
# routes/auth_local.py.
CLOSED: list[tuple[str, str]] = [
    ("GET",  "/api/family/tree"),
    ("GET",  "/api/family/payload/CILN28/G2-L40"),
    ("GET",  "/api/family/datasheet/CILN28/G2-L40"),
    ("GET",  "/api/family/report/CILN28/G2-L40"),
    ("GET",  "/api/family/context"),
    ("GET",  "/api/family/community"),
    ("GET",  "/api/family/duty_results/CILN28/G2-L40"),
    ("POST", "/api/family/activate"),
    ("GET",  "/api/geometry"),
    ("GET",  "/api/geometry/summary"),
    ("GET",  "/api/config"),
    ("GET",  "/api/materials"),
    ("GET",  "/api/materials/library"),
    ("GET",  "/api/parts"),
    ("GET",  "/api/winding/config"),
    ("GET",  "/api/mesh/config"),
    ("GET",  "/api/simulation/status"),
    ("GET",  "/api/simulation/physics/fem_transient"),
    ("GET",  "/api/thermal/last"),
    ("GET",  "/api/mechanical/last"),
    ("GET",  "/api/coupled/last"),
    ("GET",  "/api/optimization/variables"),
    ("GET",  "/api/jobs"),
    ("GET",  "/api/presets"),
    ("GET",  "/api/catalog"),
    ("GET",  "/api/my_motors"),
    ("GET",  "/api/sims/saved"),
    ("GET",  "/api/sweep/config"),
    ("GET",  "/api/panel_settings/simulation"),
    ("GET",  "/api/bearings/library"),
    ("GET",  "/api/modules"),
    ("GET",  "/api/kernel/capabilities"),
    ("GET",  "/api/static3d/machine"),
    ("GET",  "/api/fusion/params.json"),
    ("GET",  "/api/freecad/export"),
    # NOT ``POST /api/support/chat`` since 2026-09-17: the landing page shows
    # the "Help & feedback" widget to a signed-out visitor, so that ONE route is
    # open (rate-limited per IP and per day in routes/support.py, and told to
    # describe the product and nothing else).  Its own door test lives in
    # tests/test_support_chat_limits.py, which also re-checks the five routes
    # below the way this file does — the hole must stay exactly one route wide.
    ("GET",  "/api/admin/users"),
    ("GET",  "/api/auth/users"),
]

# What stays open with the door closed, and what each must answer.
OPEN = ["/api/health", "/api/me", "/api/version"]


# ── isolation ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _real_files_untouched():
    before = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    yield
    after = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    assert before == after, "config/users.json was modified by a test"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A real registry (a throwaway copy) with an admin and an ungranted client.

    Only the file paths are faked — tokens, tier resolution, admin-ness and the
    middleware are the shipping code.
    """
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    from motor_ai_sim.routes import family as fam

    users_file = tmp_path / "users.json"
    dies_dir = tmp_path / "dies"
    shutil.copy2(_REAL_USERS, users_file)
    shutil.copytree(_REAL_DIES, dies_dir)

    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setattr(fam, "_DIES_DIR", dies_dir)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.delenv("PUBLIC_EXHIBIT", raising=False)
    # Name an admin, or with ADMIN_EMAILS empty and AUTH_ENFORCE off every
    # caller — anonymous included — is the local-dev admin and the door is
    # untestable.  AUTH_ENFORCE ON is what the server runs.
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)

    U.create_user(ADMIN, "password-admin", tier="admin", name="Owner")
    U.create_user(CLIENT, CLIENT_PW, tier="free", name="Client")
    return {
        "admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
        "client": {"Authorization": f"Bearer {U.issue_token(CLIENT)}"},
        "all_dies": sorted(p.name for p in dies_dir.iterdir()
                           if (p / "die.yaml").is_file()),
    }


@pytest.fixture()
def closed(env, monkeypatch):
    monkeypatch.setenv("PUBLIC_EXHIBIT", "0")
    return env


def _call(method: str, path: str, headers: dict | None = None):
    return client.request(method, path, headers=headers or {},
                          json={} if method in ("POST", "PUT", "PATCH") else None)


# ── the list is real ─────────────────────────────────────────────────────────

def test_every_listed_route_exists():
    """A path that matches no route would 401 exactly like a real one."""
    routes = [r for r in app.routes if hasattr(r, "path_regex")]
    missing = [
        (m, p) for m, p in CLOSED + [("GET", o) for o in OPEN]
        if not any(r.path_regex.match(p) and m in (r.methods or set())
                   for r in routes)
    ]
    assert missing == [], f"not routes of this app: {missing}"


# ── the switch itself ────────────────────────────────────────────────────────

def test_default_is_the_exhibit_open(monkeypatch):
    monkeypatch.delenv("PUBLIC_EXHIBIT", raising=False)
    assert public_exhibit() is True
    for off in ("0", "false", "no", "off", "OFF", " 0 "):
        monkeypatch.setenv("PUBLIC_EXHIBIT", off)
        assert public_exhibit() is False, off
    for on in ("1", "true", "yes", ""):
        monkeypatch.setenv("PUBLIC_EXHIBIT", on)
        assert public_exhibit() is True, on


def test_anonymous_allowlist_is_exactly_health_me_and_sign_in():
    for ok in ("/api/health", "/api/me", "/api/me/", "/api/version",
               # the visitor's chat (2026-09-17) — rate-limited, product-only
               "/api/support/chat",
               "/api/auth/login", "/api/auth/google", "/api/auth/logout",
               # everything outside /api is the SPA's own bundle
               "/", "/index.html", "/assets/index-abc.js"):
        assert anonymous_allowed(ok) is True, ok
    for no in ("/api/family/tree", "/api/family/report/x/y", "/api/geometry",
               "/api/auth/users", "/api/auth/sessions", "/api/auth/password",
               "/api/health/secret", "/api/mechanical/last", "/api",
               "/api/support", "/api/support/chat/x", "/api/admin/support"):
        assert anonymous_allowed(no) is False, no


# ── closed: the anonymous caller ─────────────────────────────────────────────

@pytest.mark.parametrize("method,path", CLOSED,
                         ids=[f"{m}:{p}" for m, p in CLOSED])
def test_anonymous_is_401_everywhere(closed, method, path):
    r = _call(method, path)
    assert r.status_code == 401, f"{method} {path} -> {r.status_code}"
    assert r.json()["detail"] == "Sign in to use this server."


def test_health_is_open(closed):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "healthy"


def test_version_is_open(closed):
    """The header badge and the frontend/backend skew check run before sign-in.

    It carries a version string, a git sha and a build time — nothing about a
    machine — and without it the SPA's login screen shows "v0.0.0 / Local Mode"
    as if the server were down (seen live on 2026-09-16).
    """
    r = client.get("/api/version")
    assert r.status_code == 200
    j = r.json()
    assert isinstance(j.get("version"), str) and j["version"]
    # and nothing engineering leaked in beside it
    assert set(j) <= {"version", "gitSha", "builtAt"}


def test_me_answers_anonymous(closed):
    """The SPA reads /api/me to decide between its login screen and the app."""
    r = client.get("/api/me")
    assert r.status_code == 200
    j = r.json()
    assert j["email"] is None
    assert j["tier"] == "anon"
    assert j["isAdmin"] is False
    assert j["enforced"] is True
    assert j["tokenPresented"] is False


def test_sign_in_still_works(closed, monkeypatch):
    from motor_ai_sim import auth
    r = client.post("/api/auth/login",
                    json={"email": CLIENT, "password": CLIENT_PW})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    # …and the token it hands back opens the door it was minted for.
    me = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["email"] == CLIENT
    assert client.get("/api/family/tree",
                      headers={"Authorization": f"Bearer {token}"}).status_code == 200
    # The Google exchange is reachable too — 503 "not configured" is the
    # endpoint's own answer, so it was not stopped at the door.
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "")
    g = client.post("/api/auth/google", json={"credential": "x.y.z"})
    assert g.status_code == 503, g.text


def test_a_bad_token_is_still_anonymous(closed):
    r = client.get("/api/family/tree",
                   headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Sign in to use this server."


# ── closed: the registered caller is unchanged ───────────────────────────────

def test_owner_sees_the_whole_catalog_with_the_door_closed(closed):
    r = client.get("/api/family/tree", headers=closed["admin"])
    assert r.status_code == 200
    assert sorted(d["name"] for d in r.json()["dies"]) == closed["all_dies"]
    assert r.json()["can_write"] is True


def test_report_is_reachable_for_the_owner(closed):
    """Not the report's content — only that the door is not what stops it."""
    r = client.get("/api/family/report/no-such-die/no-such-cfg",
                   headers=closed["admin"])
    assert r.status_code != 401


def test_a_granted_account_still_sees_only_its_grants(closed):
    grant = client.put(f"/api/admin/users/{CLIENT}/motors",
                       json={"all": False, "dies": [closed["all_dies"][0]]},
                       headers=closed["admin"])
    assert grant.status_code == 200, grant.text
    r = client.get("/api/family/tree", headers=closed["client"])
    assert r.status_code == 200
    assert [d["name"] for d in r.json()["dies"]] == [closed["all_dies"][0]]
    assert r.json()["can_write"] is False


def test_an_ungranted_account_sees_an_empty_catalog_not_a_401(closed):
    r = client.get("/api/family/tree", headers=closed["client"])
    assert r.status_code == 200
    assert r.json()["dies"] == []
    assert r.json()["note"] == "no motors granted yet — ask the vendor"


# ── unset: today's behaviour, unmoved ────────────────────────────────────────

def test_with_the_switch_unset_the_exhibit_is_still_there(env):
    """The workstation default: anonymous still gets the passport-filtered tree,
    the geometry and the config — this is the half that must not move."""
    r = client.get("/api/family/tree")
    assert r.status_code == 200
    names = [d["name"] for d in r.json()["dies"]]
    assert names, "the public exhibit came back empty — the default moved"
    assert set(names) <= set(env["all_dies"])
    assert client.get("/api/geometry").status_code == 200
    assert client.get("/api/config").status_code == 200
    assert client.get("/api/me").json()["tier"] == "anon"
