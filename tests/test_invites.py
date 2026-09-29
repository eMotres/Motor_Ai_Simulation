"""Invites, first-sign-in provisioning and the door an external user comes in by.

Stage 10 of the migration.  The server runs with ``PUBLIC_EXHIBIT=0`` and
``CATALOG_GRANT_ALL_REGISTERED`` unset, so there are exactly two ways an outside
account can exist and see anything:

* it signs in with Google and is AUTO-REGISTERED at tier ``free`` with NOTHING
  granted (``auth._registry_tier``) — it can sign in, and it sees an empty
  catalog until a human grants it a motor.  That policy is unchanged here; what
  Stage 10 adds is that its WORKSPACE is created and seeded at sign-in, so the
  first request after the token is issued reads a working machine instead of
  racing a directory copy;
* an admin INVITES it (``POST /api/admin/invite``), which creates the same row
  with the tier and the motors already decided, and seeds the workspace before
  the person has ever knocked.  No e-mail is sent — the host blocks outbound
  25/465 — and the response says so out loud.

What is under test:

* the Google path end to end with a MOCKED JWKS (a real RS256 token, signed by a
  key generated here, verified by the shipping ``_verify_google_token``): the
  registry row appears at ``free``, the workspace is seeded from ``shared/``,
  the first ``/api/config`` after sign-in answers 200 from that seed, and
  ``/api/family/tree`` is EMPTY — grants and nothing else decide;
* the invite routes: row + tier + grants + workspace in one call, unknown die
  names refused and named, ``"all"``, re-invite, the list with ``accepted``,
  and revoke (row gone, sessions revoked, workspace directory NOT touched);
* the invite API is admin-only (403 for a registered account, 401 anonymous);
* an invited account sees exactly its die and 404s on every other — the same
  claim the server-side isolation check makes over https;
* per-tier queue admission (``jobs.priority_for``).

Everything runs in the pytest tmp area against copies; the real
``config/users.json`` and ``config/dies`` come out byte-identical.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"
_REAL_DIES = _ROOT / "config" / "dies"

DIE = "INVITEDIE 40"
OTHER = "VENDORONLY 30"
CFG = "L40"
DUTY = "rated"

ADMIN = "admin@example.com"
GUEST = "guest@example.com"            # invited
STRANGER = "stranger@example.com"      # arrives on its own through Google

client = TestClient(app)


# ── isolation ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _real_files_untouched():
    before_users = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    before_dies = {p: p.stat().st_mtime_ns
                   for p in sorted(_REAL_DIES.rglob("*")) if p.is_file()}
    yield
    after_users = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    after_dies = {p: p.stat().st_mtime_ns
                  for p in sorted(_REAL_DIES.rglob("*")) if p.is_file()}
    assert before_users == after_users, "config/users.json was modified by a test"
    assert before_dies == after_dies, "config/dies was modified by a test"


def _write_die(shared: Path, die: str) -> None:
    d = shared / "dies" / die
    d.mkdir(parents=True)
    (d / "die.yaml").write_text(yaml.safe_dump({
        "name": die, "locked": False, "created": "2026-09-16T10:00:00",
        "geometry": {"num_slots": 12, "num_poles": 14, "stator_diameter": 40.0,
                     "magnet_height": 3.0, "motor_length": 40.0},
    }, sort_keys=False, allow_unicode=True), encoding="utf-8")
    (d / f"{CFG}.yaml").write_text(yaml.safe_dump({
        "name": CFG, "die": die, "role": "motor",
        "geometry_overrides": {"motor_length": 40.0, "wire_height": 1.0},
        "winding": {"connection": "star"},
        "materials": {"magnet": "N42SH", "stator_core": "20SW1200"},
        "duties": [{"name": DUTY, "mode": "motor",
                    "saved_at": "2026-09-16T10:00:00",
                    "current_arms": 85.0, "rpm": 6000.0, "gamma_deg": 12.0,
                    "note": ""}],
    }, sort_keys=False, allow_unicode=True), encoding="utf-8")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A layered server with the production posture: the door closed, no blanket
    grant, one admin, two shared dies.  Only the PATHS are faked."""
    from motor_ai_sim import auth
    from motor_ai_sim import sessions as S
    from motor_ai_sim import users as U
    from motor_ai_sim.routes import family as fam

    tree = tmp_path / "srv"
    shared, published, works = (tree / "shared", tree / "published",
                                tree / "workspaces")
    for d in (shared / "dies", published, works):
        d.mkdir(parents=True)
    _write_die(shared, DIE)
    _write_die(shared, OTHER)
    shutil.copy2(_ROOT / "config" / "motor_config.yaml", shared / "motor_config.yaml")

    monkeypatch.setenv("WORKSPACES_ROOT", str(works))
    monkeypatch.setenv("SHARED_ROOT", str(shared))
    monkeypatch.setenv("PUBLISHED_ROOT", str(published))
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    # The server's posture: no anonymous audience at all.
    monkeypatch.setenv("PUBLIC_EXHIBIT", "0")
    monkeypatch.delitem(fam.__dict__, "_DIES_DIR", raising=False)
    fam._TREE_CACHE.clear()

    users_file = tmp_path / "users.json"
    sessions_file = tmp_path / "sessions.json"
    users_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setattr(S, "_SESSIONS_FILE", sessions_file)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    U.create_user(ADMIN, "password-admin", tier="admin", name="Admin")

    from motor_ai_sim import workspace as W
    yield {
        "shared": shared, "works": works, "users_file": users_file,
        "admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
        "ws_of": lambda e: works / W.workspace_id(e),
    }
    fam._TREE_CACHE.clear()


# ── a real RS256 Google token, with the JWKS mocked ──────────────────────────

@pytest.fixture()
def google(monkeypatch):
    """Sign GIS-shaped ID tokens with a throwaway RSA key and hand the public
    half to ``auth._google_keys`` — the ONLY thing faked.  Audience, issuer,
    expiry and signature are all checked by the shipping verifier."""
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    from motor_ai_sim import auth

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    kid = "test-kid"
    client_id = "test-client-id.apps.googleusercontent.com"
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", client_id)
    monkeypatch.setattr(auth, "_google_keys",
                        lambda force=False: {kid: key.public_key()})

    def _token(email: str, *, name: str = "", verified: bool = True) -> str:
        now = int(time.time())
        return jwt.encode({"iss": "https://accounts.google.com",
                           "aud": client_id, "sub": "g-" + email,
                           "email": email, "email_verified": verified,
                           "name": name or email.split("@")[0],
                           "iat": now, "exp": now + 3600},
                          key, algorithm="RS256", headers={"kid": kid})

    return _token


def _sign_in(google, email: str) -> dict:
    r = client.post("/api/auth/google", json={"credential": google(email)})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _invite(env, email=GUEST, tier="free", motors=None, note="", expect=200):
    r = client.post("/api/admin/invite", headers=env["admin"], json={
        "email": email, "tier": tier,
        "motors": [DIE] if motors is None else motors, "note": note})
    assert r.status_code == expect, r.text
    return r


def _tree(headers):
    r = client.get("/api/family/tree", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# ── 1. first sign-in provisioning ────────────────────────────────────────────

def test_unknown_google_account_is_registered_free_with_nothing_granted(env, google):
    from motor_ai_sim import users as U
    assert U.get_user(STRANGER) is None
    hdr = _sign_in(google, STRANGER)
    rec = U.get_user(STRANGER)
    assert rec is not None, "the sign-in did not create a registry row"
    assert rec["tier"] == "free"
    assert U.get_motor_grants(STRANGER) == {"all": False, "dies": []}
    # …and it is NOT an invite: nobody let this address in on purpose.
    assert U.invite_of(STRANGER) is None
    assert _tree(hdr)["dies"] == []
    assert _tree(hdr)["note"] == "no motors granted yet — ask the vendor"


def test_the_workspace_is_seeded_at_sign_in_not_at_the_first_solve(env, google):
    ws = env["ws_of"](STRANGER)
    assert not ws.exists()
    _sign_in(google, STRANGER)
    assert (ws / "motor_config.yaml").is_file(), (
        "the workspace was not seeded when the token was issued")
    seeded = yaml.safe_load((ws / "motor_config.yaml").read_text(encoding="utf-8"))
    shared = yaml.safe_load(
        (env["shared"] / "motor_config.yaml").read_text(encoding="utf-8"))
    assert seeded["geometry"] == shared["geometry"], "seeded from the wrong machine"


def test_the_first_request_after_sign_in_reads_that_workspace(env, google):
    hdr = _sign_in(google, STRANGER)
    r = client.get("/api/config", headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json()["geometry"].get("num_slots"), "the seeded machine is empty"
    # The door is closed to everyone else: the same request without a token.
    assert client.get("/api/config").status_code == 401


def test_a_grant_is_the_only_thing_that_opens_the_catalog(env, google):
    hdr = _sign_in(google, STRANGER)
    assert _tree(hdr)["dies"] == []
    r = client.put(f"/api/admin/users/{STRANGER}/motors", headers=env["admin"],
                   json={"all": False, "dies": [DIE]})
    assert r.status_code == 200, r.text
    assert [d["name"] for d in _tree(hdr)["dies"]] == [DIE]


def test_a_disabled_account_cannot_sign_in(env, google):
    from motor_ai_sim import users as U
    _sign_in(google, STRANGER)
    U.update_user(STRANGER, disabled=True)
    r = client.post("/api/auth/google", json={"credential": google(STRANGER)})
    assert r.status_code == 403


def test_an_unverified_google_email_is_refused(env, google):
    r = client.post("/api/auth/google",
                    json={"credential": google(STRANGER, verified=False)})
    assert r.status_code == 403
    from motor_ai_sim import users as U
    assert U.get_user(STRANGER) is None


# ── 2. the invite flow ───────────────────────────────────────────────────────

def test_invite_creates_the_row_the_grants_and_the_workspace(env):
    from motor_ai_sim import users as U
    r = _invite(env, note="Ø40 evaluation, 2 weeks")
    body = r.json()
    assert body["ok"] is True and body["emailed"] is False
    assert "Google" in body["next"]
    assert body["motors"] == {"all": False, "dies": [DIE]}
    assert U.get_user(GUEST)["tier"] == "free"
    inv = U.invite_of(GUEST)
    assert inv["by"] == ADMIN and inv["note"] == "Ø40 evaluation, 2 weeks"
    ws = env["ws_of"](GUEST)
    assert (ws / "motor_config.yaml").is_file(), (
        "the invited account has no workspace to sign in to")
    assert body["workspace"] == str(ws)


def test_the_invited_account_signs_in_and_sees_exactly_its_motor(env, google):
    _invite(env)
    hdr = _sign_in(google, GUEST)
    assert [d["name"] for d in _tree(hdr)["dies"]] == [DIE]
    # and the ungranted die is a 404 on every die-scoped route, never a 403
    for url in (f"/api/family/payload/{OTHER}/{CFG}",
                f"/api/family/datasheet/{OTHER}/{CFG}"):
        assert client.get(url, headers=hdr).status_code == 404, url
    assert client.get(f"/api/family/payload/{DIE}/{CFG}",
                      headers=hdr).status_code == 200


def test_invite_refuses_an_unknown_die_and_names_it(env):
    r = _invite(env, motors=["NO SUCH DIE"], expect=422)
    assert "NO SUCH DIE" in r.json()["detail"]
    from motor_ai_sim import users as U
    assert U.get_user(GUEST) is None, (
        "a refused invite still created the account")


def test_invite_refuses_an_unknown_tier_and_a_bad_address(env):
    assert _invite(env, tier="platinum", expect=422)
    assert _invite(env, email="not-an-address", expect=422)


def test_invite_all_grants_the_whole_catalog(env, google):
    _invite(env, motors="all")
    hdr = _sign_in(google, GUEST)
    assert sorted(d["name"] for d in _tree(hdr)["dies"]) == sorted([DIE, OTHER])


def test_reinvite_moves_the_tier_the_grants_and_un_disables(env):
    from motor_ai_sim import users as U
    _invite(env)
    U.update_user(GUEST, disabled=True)
    _invite(env, tier="pro", motors=[DIE, OTHER], note="upgraded")
    rec = U.get_user(GUEST)
    assert rec["tier"] == "pro" and rec["disabled"] is False
    assert U.get_motor_grants(GUEST)["dies"] == sorted([DIE, OTHER])
    assert U.invite_of(GUEST)["note"] == "upgraded"


def test_invites_list_says_who_has_actually_arrived(env, google):
    _invite(env, note="evaluation")
    r = client.get("/api/admin/invites", headers=env["admin"])
    assert r.status_code == 200
    row = next(x for x in r.json()["invites"] if x["email"] == GUEST)
    assert row["accepted"] is False and row["invited_by"] == ADMIN
    assert row["note"] == "evaluation" and row["motors"]["dies"] == [DIE]
    _sign_in(google, GUEST)
    row = next(x for x in client.get("/api/admin/invites", headers=env["admin"])
               .json()["invites"] if x["email"] == GUEST)
    assert row["accepted"] is True and row["last_seen"]


def test_a_self_registered_account_is_not_an_invite(env, google):
    _sign_in(google, STRANGER)
    rows = client.get("/api/admin/invites", headers=env["admin"]).json()["invites"]
    assert [r["email"] for r in rows] == []


def test_revoking_an_invite_removes_the_row_and_keeps_the_work(env, google):
    from motor_ai_sim import sessions as S
    from motor_ai_sim import users as U
    _invite(env)
    hdr = _sign_in(google, GUEST)
    ws = env["ws_of"](GUEST)
    assert ws.is_dir()
    r = client.delete(f"/api/admin/invites/{GUEST}", headers=env["admin"])
    assert r.status_code == 200, r.text
    assert r.json()["sessions_revoked"] >= 1
    assert r.json()["workspace"]["exists"] is True, (
        "revoking an invite deleted the person's saved work")
    assert ws.is_dir()
    assert U.get_user(GUEST) is None
    assert all(s.get("revoked") for s in S.list_for(GUEST))
    # the token it was holding stops working on the very next request
    assert client.get("/api/config", headers=hdr).status_code == 401


def test_revoke_refuses_an_account_that_was_never_invited(env):
    r = client.delete(f"/api/admin/invites/{ADMIN}", headers=env["admin"])
    assert r.status_code == 404
    assert "was not invited" in r.json()["detail"]
    from motor_ai_sim import users as U
    assert U.get_user(ADMIN) is not None
    assert client.delete("/api/admin/invites/nobody@example.com",
                         headers=env["admin"]).status_code == 404


def test_the_invite_api_is_admin_only(env, google):
    _invite(env)
    guest = _sign_in(google, GUEST)
    for call in (lambda h: client.post("/api/admin/invite", headers=h,
                                       json={"email": "x@example.com"}),
                 lambda h: client.get("/api/admin/invites", headers=h),
                 lambda h: client.delete(f"/api/admin/invites/{GUEST}", headers=h)):
        assert call(guest).status_code == 403
        assert call({}).status_code == 401


# ── 3. per-tier queue admission ──────────────────────────────────────────────

def test_priority_for_demotes_only_the_base_tiers():
    from motor_ai_sim import jobs as J
    P = J.Priority
    # a free account: campaigns for the optimizer, DUTY for everything else
    assert J.priority_for("optimizer.scan", "free", P.CAMPAIGN) is P.CAMPAIGN
    assert J.priority_for("optimizer.descent", "free", P.DUTY) is P.CAMPAIGN
    assert J.priority_for("transient", "free", P.INTERACTIVE) is P.DUTY
    assert J.priority_for("thermal.field", "free", P.FIELD) is P.DUTY
    assert J.priority_for("coupled.run", "free", P.DUTY) is P.DUTY
    assert J.priority_for("transient", "anon", P.INTERACTIVE) is P.DUTY
    # paying and owning accounts keep the class the route asked for
    for tier in ("pro", "team", "admin"):
        assert J.priority_for("transient", tier, P.INTERACTIVE) is P.INTERACTIVE
        assert J.priority_for("thermal.field", tier, P.FIELD) is P.FIELD
        assert J.priority_for("optimizer.scan", tier, P.CAMPAIGN) is P.CAMPAIGN
    # an unknown/absent tier (a direct call, a CLI run) is left alone
    assert J.priority_for("transient", "", P.INTERACTIVE) is P.INTERACTIVE
    assert J.priority_for("transient", None, P.INTERACTIVE) is P.INTERACTIVE


def test_the_record_carries_the_tier_class_not_the_route_s(env, google, monkeypatch):
    """The mapping is applied where every submission funnels: ``make_record``."""
    from motor_ai_sim import jobs as J
    from motor_ai_sim import workspace as W
    _invite(env, tier="free")
    with W.use_workspace(W.workspace_for_identity(GUEST)), \
            W.use_caller({"id": GUEST, "is_admin": False, "tier": "free"}):
        assert J.make_record("transient", priority=J.Priority.INTERACTIVE
                             ).priority == int(J.Priority.DUTY)
        assert J.make_record("optimizer.scan", priority=J.Priority.CAMPAIGN
                             ).priority == int(J.Priority.CAMPAIGN)
    with W.use_workspace(W.workspace_for_identity(ADMIN)), \
            W.use_caller({"id": ADMIN, "is_admin": True, "tier": "admin"}):
        assert J.make_record("transient", priority=J.Priority.INTERACTIVE
                             ).priority == int(J.Priority.INTERACTIVE)


def test_caller_identity_carries_the_tier(env, google):
    """The queue reads the tier the GATE already resolved — one verification."""
    from motor_ai_sim.auth import caller_identity
    _invite(env)
    guest = _sign_in(google, GUEST)
    assert caller_identity(guest["Authorization"]) == {
        "id": GUEST, "is_admin": False, "tier": "free"}
    assert caller_identity(env["admin"]["Authorization"])["tier"] == "admin"
    assert caller_identity(None)["tier"] in ("anon", "admin")
