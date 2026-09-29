"""Per-user motor access: the catalog a signed-in account sees is its GRANTS.

Until 2026-09-02 a signed-in non-admin got a marketing filter instead of an
access rule — a die was visible only when a passported catalog card described
the same machine — so the owner's own second account (vadshe@gmail.com) could
not see the two 85 mm dies at all.  Grants now live in the registry
(``config/users.json`` → ``motors: {all, dies}``) and decide visibility.

What is under test:

* an admin still sees every die;
* a signed-in account with NO grants sees an empty catalog and a one-line note
  (a brand-new account is granted nothing — the vendor assigns motors);
* a granted account sees exactly its dies, with all their configurations and
  duties, and NOTHING else;
* hiding is not cosmetic: payload / datasheet / context 404 (never 403 — an
  ungranted motor must not be distinguishable from a missing one) on a die the
  account was not granted, and serve it once granted;
* ``all: true`` and the ``CATALOG_GRANT_ALL_REGISTERED`` switch each open the
  whole catalog;
* the admin grant API validates die names and NAMES the unknown ones;
* the ANONYMOUS public exhibit is byte-for-byte the old passport-filtered set —
  the marketing landing was deliberately left alone.

Everything runs against COPIES of config/users.json and config/dies in the
scratchpad; the session asserts the real files were never touched.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.config import DEFAULT_CONFIG_PATH as _dcp

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"
_REAL_DIES = _ROOT / "config" / "dies"

ADMIN = "admin@example.com"
CLIENT = "client@example.com"

# The two dies the user could not see: no passported catalog card, so the old
# client filter hid them from every non-admin.
DIES_85 = ["CIANO28 85 20RSW175", "CIANO28 85 20SW1200"]

# What an ANONYMOUS visitor saw BEFORE this change, produced by running the
# pre-edit tree() against this same catalog (scratchpad/family_old.py,
# 2026-09-02).  The public exhibit must not have moved: these are the motors
# with a characterised passport card, and the 85 mm pair is NOT among them.
PUBLIC_EXHIBIT = [
    "40 mm · 12s-14p high-speed",
    "CIANO14 30_10",
    "CIANO14 40 new",
    "CIANO28 150_35",
    "CIANO28 150_35 new",
    "CILN28",
    "M1 850Nm",
    "My motor · refine 10-59",
]

client = TestClient(app)


# ── isolation ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _real_files_untouched():
    """The registry and the catalog on disk must come out of this module
    byte-identical — a test that writes the user's accounts or dies is a bug
    worse than the one it was chasing."""
    before_users = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    before_dies = {p: p.stat().st_mtime_ns
                   for p in sorted(_REAL_DIES.rglob("*")) if p.is_file()}
    yield
    after_users = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    after_dies = {p: p.stat().st_mtime_ns
                  for p in sorted(_REAL_DIES.rglob("*")) if p.is_file()}
    assert before_users == after_users, "config/users.json was modified by a test"
    assert before_dies == after_dies, "config/dies was modified by a test"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Registry + catalog redirected to throwaway copies, with a real admin
    account and a real client account.  Only the file paths are faked: tokens,
    tier resolution and admin-ness are the shipping code."""
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    from motor_ai_sim.routes import family as fam

    sandbox = tmp_path
    users_file = sandbox / "users.json"
    dies_dir = sandbox / "dies"
    shutil.copy2(_REAL_USERS, users_file)
    shutil.copytree(_REAL_DIES, dies_dir)

    # The anonymous exhibit filter reads motor_catalog.json NEXT TO the active
    # config, which conftest.py redirects to a sandbox holding only the machine
    # file.  Without the cards the filter falls back to "hide nothing" and the
    # public exhibit would not be under test at all — so put a copy of the real
    # catalog beside the sandbox config for the duration of this test.
    cat_dst = Path(_dcp).parent / "motor_catalog.json"
    borrowed = not cat_dst.exists()
    if borrowed:
        shutil.copy2(_ROOT / "config" / "motor_catalog.json", cat_dst)

    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setattr(fam, "_DIES_DIR", dies_dir)
    # Signing secret from the env, so config/.auth_secret is never read/created.
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    # With ADMIN_EMAILS empty and AUTH_ENFORCE off every caller is the local-dev
    # admin — that would make the whole feature untestable, so name an admin.
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)

    U.create_user(ADMIN, "password-admin", tier="admin", name="Admin")
    U.create_user(CLIENT, "password-client", tier="free", name="Client")
    yield {"dies": dies_dir, "users": users_file,
           "admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
           "client": {"Authorization": f"Bearer {U.issue_token(CLIENT)}"},
           "all_dies": sorted(p.name for p in dies_dir.iterdir()
                              if (p / "die.yaml").is_file())}
    if borrowed:
        cat_dst.unlink(missing_ok=True)


def _names(resp):
    return sorted(d["name"] for d in resp.json()["dies"])


def _grant(env, dies=None, all_motors=False, expect=200):
    r = client.put(f"/api/admin/users/{CLIENT}/motors",
                   json={"all": all_motors, "dies": dies or []},
                   headers=env["admin"])
    assert r.status_code == expect, r.text
    return r


# ── the tree ─────────────────────────────────────────────────────────────────

def test_admin_sees_every_die(env):
    r = client.get("/api/family/tree", headers=env["admin"])
    assert r.status_code == 200
    assert _names(r) == env["all_dies"]
    assert r.json()["can_write"] is True
    # never cached: a grant made a second ago must show on the next reload
    assert r.headers.get("cache-control") == "no-store"
    assert "Authorization" in (r.headers.get("vary") or "")


def test_new_account_sees_nothing_and_is_told_why(env):
    r = client.get("/api/family/tree", headers=env["client"])
    assert r.status_code == 200
    assert r.json()["dies"] == []
    assert r.json()["note"] == "no motors granted yet — ask the vendor"
    assert r.json()["can_write"] is False


def test_granted_dies_are_exactly_what_the_account_sees(env):
    _grant(env, DIES_85)
    r = client.get("/api/family/tree", headers=env["client"])
    assert _names(r) == sorted(DIES_85)
    # the WHOLE motor, not a teaser: configurations and their duties
    for die in r.json()["dies"]:
        assert die["configs"], f"{die['name']} came back without configurations"
        assert any(c["duties"] for c in die["configs"]), \
            f"{die['name']} came back without duties"
    assert r.json()["can_write"] is False


def test_all_true_opens_the_whole_catalog(env):
    _grant(env, all_motors=True)
    r = client.get("/api/family/tree", headers=env["client"])
    assert _names(r) == env["all_dies"]
    assert r.json()["can_write"] is False        # visibility is not write access


def test_env_switch_grants_every_registered_account(env, monkeypatch):
    monkeypatch.setenv("CATALOG_GRANT_ALL_REGISTERED", "1")
    r = client.get("/api/family/tree", headers=env["client"])
    assert _names(r) == env["all_dies"]
    assert "note" not in r.json()


def test_anonymous_still_gets_the_old_public_exhibit(env):
    r = client.get("/api/family/tree")
    # PUBLIC_EXHIBIT is a snapshot of a catalog the USER owns: dies come and go
    # in config/dies as designs are renamed or dropped (`My motor · refine
    # 10-59` was deleted on 2026-09-04), and a die that no longer exists cannot
    # be exhibited by any filter.  So the snapshot is judged against the dies
    # that are actually there — which still pins BOTH directions of the claim:
    # every surviving name on the list is public, and NOTHING outside the list
    # has become public (a new name would not be in `expect` and the equality
    # would fail).  Chasing the list by hand would only reopen the same break.
    expect = [n for n in PUBLIC_EXHIBIT if n in env["all_dies"]]
    assert _names(r) == expect
    for d in DIES_85:
        assert d not in _names(r)


# ── the hiding is not cosmetic ───────────────────────────────────────────────

def _cfg_of(env, die):
    return sorted(p.stem for p in (env["dies"] / die).glob("*.yaml")
                  if p.name != "die.yaml")[0]


def test_ungranted_payload_and_datasheet_are_404(env):
    die = DIES_85[0]
    cfg = _cfg_of(env, die)
    for url in (f"/api/family/payload/{die}/{cfg}",
                f"/api/family/datasheet/{die}/{cfg}"):
        r = client.get(url, headers=env["client"])
        assert r.status_code == 404, f"{url} -> {r.status_code}"
        assert "not found" in r.json()["detail"]


def test_granted_payload_serves_the_machine(env):
    die = DIES_85[0]
    cfg = _cfg_of(env, die)
    assert client.get(f"/api/family/payload/{die}/{cfg}",
                      headers=env["client"]).status_code == 404
    _grant(env, [die])
    r = client.get(f"/api/family/payload/{die}/{cfg}", headers=env["client"])
    assert r.status_code == 200
    assert r.json()["geometry"].get("num_slots")
    # the OTHER 85 die is still not theirs
    other = DIES_85[1]
    assert client.get(f"/api/family/payload/{other}/{_cfg_of(env, other)}",
                      headers=env["client"]).status_code == 404


def test_context_does_not_name_an_ungranted_die(env, monkeypatch):
    from motor_ai_sim.routes import family as fam
    die = DIES_85[0]
    monkeypatch.setattr(fam, "_read_ctx",
                        lambda: {"die": die, "config": _cfg_of(env, die),
                                 "duty": None})
    r = client.get("/api/family/context", headers=env["client"])
    assert r.json()["active"] is False
    assert die not in r.text
    _grant(env, [die])
    r = client.get("/api/family/context", headers=env["client"])
    assert r.json()["active"] is True and r.json()["die"] == die


# ── the admin grant API ──────────────────────────────────────────────────────

def test_admin_motor_list_is_admin_only(env):
    r = client.get("/api/admin/motors", headers=env["admin"])
    assert r.status_code == 200
    assert sorted(d["name"] for d in r.json()["dies"]) == env["all_dies"]
    for d in r.json()["dies"]:
        assert "stator_diameter" in d and "configs" in d and "duties" in d
    assert client.get("/api/admin/motors",
                      headers=env["client"]).status_code == 403
    assert client.get("/api/admin/motors").status_code in (401, 403)


def test_unknown_die_is_refused_and_named(env):
    r = _grant(env, ["CIANO28 85 20RSW175", "No Such Die"], expect=422)
    assert "No Such Die" in r.json()["detail"]
    assert "CIANO28 85 20RSW175" not in r.json()["detail"].split("—")[0]
    # nothing was written: the account still sees nothing
    assert client.get("/api/family/tree", headers=env["client"]).json()["dies"] == []


def test_grants_round_trip_through_the_registry(env):
    import json
    _grant(env, DIES_85)
    r = client.get(f"/api/admin/users/{CLIENT}/motors", headers=env["admin"])
    assert r.json()["motors"] == {"all": False, "dies": sorted(DIES_85)}
    stored = json.loads(env["users"].read_text(encoding="utf-8"))
    assert stored[CLIENT]["motors"] == {"all": False, "dies": sorted(DIES_85)}
    # the account keeps its tier / password / disabled flag
    assert stored[CLIENT]["tier"] == "free" and stored[CLIENT]["pw_hash"]
    # and the admin user table carries the grants for the count chip
    users = client.get("/api/auth/users", headers=env["admin"]).json()["users"]
    me = next(u for u in users if u["email"] == CLIENT)
    assert me["motors"] == {"all": False, "dies": sorted(DIES_85)}


def test_grants_for_an_unknown_account_are_404(env):
    r = client.put("/api/admin/users/nobody@example.com/motors",
                   json={"all": True}, headers=env["admin"])
    assert r.status_code == 404
