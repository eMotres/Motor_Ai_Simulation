"""A stored factory (oem) identity is never elevated by ADMIN_EMAILS (Astra review 2026-11-26).  Real role resolution: the registry,
the local token path, the raw Google path, the agent owner and the permission helpers; only the file paths are redirected.

Only an account that is BOTH a stored oem record AND in ADMIN_EMAILS changes (admin -> oem); an allowlisted account of any other
role is still admin, a disabled allowlisted account is still refused, an unlisted account keeps its registry role."""
from __future__ import annotations

from tests.test_auth_sessions import ADMIN, CLIENT, _login, env  # noqa: F401  (fixture)

FACTORY = "factory@example.com"


def _factory_in_allowlist(env, monkeypatch):
    A, U = env["A"], env["U"]
    U.create_user(FACTORY, "password-factory", role="oem", name="Factory")
    monkeypatch.setattr(A, "_ADMIN_EMAILS", {ADMIN, FACTORY})
    return A, U


def test_an_oem_account_in_the_admin_emails_keeps_its_oem_role_on_every_path(env, monkeypatch):
    A, U = _factory_in_allowlist(env, monkeypatch)
    assert A._registry_role(FACTORY) == "oem"
    assert A._registry_role(FACTORY, fail_closed=True) == "oem"
    token = _login(FACTORY, "password-factory")["token"]                          # the local token path
    got = A.resolve_user_detail("Bearer " + token)
    assert got["reason"] == "ok" and got["user"]["role"] == "oem"
    monkeypatch.setattr(A, "_verify_google_token", lambda cred: {"email": FACTORY, "email_verified": True, "sub": "g-f"})
    google = A.resolve_user_detail("Bearer google-credential")                    # the raw Google path
    assert google["reason"] == "ok" and google["user"]["role"] == "oem"
    assert A.agent_user_for(FACTORY)["role"] == "oem"                              # an agent key owned by that account
    assert A.principal_is_admin(FACTORY) is False
    is_admin, user = A._is_admin_caller("Bearer " + token)
    assert is_admin is False and user["role"] == "oem"


def test_a_non_oem_allowlisted_account_is_still_admin(env, monkeypatch):
    A, U = _factory_in_allowlist(env, monkeypatch)
    assert A._registry_role(ADMIN) == "admin" and A.principal_is_admin(ADMIN) is True
    token = _login(ADMIN, "password-admin")["token"]
    assert A.resolve_user_detail("Bearer " + token)["user"]["role"] == "admin"
    assert A._is_admin_caller("Bearer " + token)[0] is True
    # an allowlisted account whose stored role is "user" or "engineer" is raised, exactly as before
    U.create_user("eng@example.com", "password-eng", role="engineer", name="Eng")
    monkeypatch.setattr(A, "_ADMIN_EMAILS", {ADMIN, FACTORY, "eng@example.com", CLIENT})
    assert A._registry_role("eng@example.com") == "admin" and A._registry_role(CLIENT) == "admin"
    assert A.principal_is_admin(CLIENT) is True


def test_an_unlisted_oem_account_and_a_disabled_allowlisted_account_are_unchanged(env, monkeypatch):
    A, U = _factory_in_allowlist(env, monkeypatch)
    monkeypatch.setattr(A, "_ADMIN_EMAILS", {ADMIN})                               # the factory is not listed
    assert A._registry_role(FACTORY) == "oem" and A.principal_is_admin(FACTORY) is False
    U.update_user(ADMIN, disabled=True)
    assert A._registry_role(ADMIN) == "__disabled__"
    monkeypatch.setattr(A, "_ADMIN_EMAILS", {ADMIN, FACTORY})
    U.update_user(FACTORY, disabled=True)
    assert A._registry_role(FACTORY) == "__disabled__"                             # disabled still wins for the factory account


def test_the_price_rule_sees_the_resolved_oem_role_of_an_allowlisted_factory_owner(env, monkeypatch):
    from motor_ai_sim.development import access
    A, U = _factory_in_allowlist(env, monkeypatch)
    token = _login(FACTORY, "password-factory")["token"]
    user = A.resolve_user("Bearer " + token)
    project = {"id": "alpha", "members": [{"email": FACTORY, "role": "owner"}]}
    assert user["role"] == "oem" and access.sees_prices(user, project) is False
    admin_user = A.resolve_user("Bearer " + _login(ADMIN, "password-admin")["token"])
    assert access.sees_prices(admin_user, project) is True
