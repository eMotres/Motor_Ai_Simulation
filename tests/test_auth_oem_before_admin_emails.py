"""A stored factory (oem) identity is never elevated by ADMIN_EMAILS (Astra review 2026-11-26).  Real role resolution: the registry,
the local token path, the raw Google path, the agent owner and the permission helpers; only the file paths are redirected.

Only an account that is BOTH a stored oem record AND in ADMIN_EMAILS changes (admin -> oem); an allowlisted account of any other
role is still admin, a disabled allowlisted account is still refused, an unlisted account keeps its registry role."""
from __future__ import annotations

import pytest

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


# ── Astra review r1 (2026-11-26) ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_last_admin_guard_does_not_count_an_allowlisted_stored_oem_as_a_backup_admin(env, monkeypatch):
    """Two accounts: A = enabled stored admin, unlisted; B = enabled stored OEM, allowlisted.  Demoting A must be refused."""
    import pytest
    A, U = env["A"], env["U"]
    U.create_user("owner-a@example.com", "password-a", role="admin", name="A")
    U.create_user(FACTORY, "password-factory", role="oem", name="Factory")
    monkeypatch.setattr(A, "_ADMIN_EMAILS", {FACTORY})
    for other in [e for e, r in U._load().items() if r.get("role") == "admin" and e != "owner-a@example.com"]:
        U.update_user(other, disabled=True)                                       # the fixture's own admins must not be backups
    with pytest.raises(U.RoleProtected):
        U.update_user("owner-a@example.com", role="user")
    assert U.get_user("owner-a@example.com")["role"] == "admin"
    # a listed address that really is an admin (no oem record) still counts as a backup, a disabled one does not
    monkeypatch.setattr(A, "_ADMIN_EMAILS", {FACTORY, ADMIN})
    U.update_user(ADMIN, disabled=False)                                          # a listed, enabled, non-oem address is a backup
    U.update_user("owner-a@example.com", role="user")
    U.update_user("owner-a@example.com", role="admin")
    U.update_user(ADMIN, disabled=True)                                           # a disabled listed address is not
    with pytest.raises(U.RoleProtected):
        U.update_user("owner-a@example.com", role="user")


def test_an_unreadable_registry_never_elevates_through_the_allowlist(env, monkeypatch):
    A, U = _factory_in_allowlist(env, monkeypatch)

    def boom():
        raise OSError("registry locked")
    monkeypatch.setattr(U, "_load", boom)
    assert A.principal_is_admin(ADMIN) is False
    assert A._registry_role(ADMIN) == "user"                                       # the supplied fallback, never admin
    assert A._registry_role(ADMIN, fallback="engineer") == "engineer"
    assert A._registry_role(ADMIN, fail_closed=True) == "__unavailable__"
    assert A.agent_user_for(ADMIN) is None                                         # never an elevated principal
    assert A.allowlist_elevates(ADMIN) is False


def test_a_disabled_or_oem_record_is_refused_before_the_allowlist(env, monkeypatch):
    A, U = _factory_in_allowlist(env, monkeypatch)
    assert A.principal_is_admin(ADMIN) is True
    U.update_user(ADMIN, disabled=True)
    assert A.principal_is_admin(ADMIN) is False and A._registry_role(ADMIN) == "__disabled__"
    assert A.allowlist_elevates(ADMIN) is False and A.allowlist_elevates(FACTORY) is False
    assert A.agent_user_for(ADMIN) is None
    assert A.agent_user_for(FACTORY)["role"] == "oem"
    # a listed address without any record is the owner's own: still admin (unchanged)
    monkeypatch.setattr(A, "_ADMIN_EMAILS", {ADMIN, FACTORY, "nobody@example.com"})
    assert A._registry_role("nobody@example.com") == "admin" and A.principal_is_admin("nobody@example.com") is True


def test_the_other_allowlist_readers_do_not_treat_a_stored_oem_as_staff(env, monkeypatch):
    A, U = _factory_in_allowlist(env, monkeypatch)
    from motor_ai_sim import construction_privacy as CP
    from motor_ai_sim import usage_stats as UST
    from motor_ai_sim.owner_report import send as SEND, sources as SRC
    from motor_ai_sim import agent_designs as AD
    assert CP.subject_may_see_full_construction(FACTORY) is False                  # export scrubbed without an independent grant
    assert CP.subject_may_see_full_construction(ADMIN) is True
    assert SRC.side_of(FACTORY) == "factory" and SRC.side_of(ADMIN) == "staff"
    assert FACTORY not in SEND.allowed_addresses() and ADMIN in SEND.allowed_addresses()
    assert UST.account_attrs(FACTORY)["role"] != "admin" and UST.account_attrs(ADMIN)["role"] == "admin"
    class _P:  # a minimal principal: only .email is read
        def __init__(self, email): self.email = email
    assert AD.daily_limit(_P(ADMIN)) is None and AD.daily_limit(_P(FACTORY)) is not None


# ── Astra review r2 (2026-11-26): REAL corrupt-file outages (not a mocked reader) ───────────────────────────────────────────────

def _corrupt(U):
    good = U._USERS_FILE.read_bytes()
    U._USERS_FILE.write_text("{ this is not json", encoding="utf-8")
    return good


def _restore(U, good):
    U._USERS_FILE.write_bytes(good)


def test_agent_keys_and_oauth_grants_fail_closed_on_a_corrupt_registry_and_recover(env):
    from motor_ai_sim import agent_keys as K
    from motor_ai_sim import oauth as O
    U = env["U"]
    token, _rec = K.create_key(ADMIN, "outage test")
    gid = "g1"
    out = {}

    def seed(d):
        g = {"id": gid, "client_id": "c1", "owner": ADMIN, "scopes": list(K.DEFAULT_SCOPES)}
        d["grants"][gid] = g
        out["tok"] = O._mint(g)
    O._mutate(seed)
    access, refresh = out["tok"]["access_token"], out["tok"]["refresh_token"]
    assert K.verify("Bearer " + token)[1] == "ok" and O.verify_access(access)[1] == "ok"

    good = _corrupt(U)
    assert K._account_disabled(ADMIN) is True
    assert K.verify("Bearer " + token) == (None, "disabled")                        # key verification refuses during the outage
    assert O.verify_access(access) == (None, "disabled")                            # OAuth access refuses
    with pytest.raises(O.OAuthError):                                               # OAuth refresh refuses and mints nothing
        O._refresh({"client_id": "c1"}, {"refresh_token": refresh})
    _restore(U, good)                                                               # the same credentials recover after the restore
    assert K.verify("Bearer " + token)[1] == "ok" and O.verify_access(access)[1] == "ok"
    assert O._refresh({"client_id": "c1"}, {"refresh_token": refresh})["access_token"]


def test_the_construction_export_gate_fails_closed_on_a_corrupt_registry(env, monkeypatch):
    from motor_ai_sim import construction_privacy as CP
    A, U = _factory_in_allowlist(env, monkeypatch)
    assert CP.subject_may_see_full_construction(FACTORY) is False and CP.subject_may_see_full_construction(ADMIN) is True
    good = _corrupt(U)
    assert CP.subject_may_see_full_construction(FACTORY) is False                   # an allowlisted OEM is NOT elevated by the outage
    assert CP.subject_may_see_full_construction(ADMIN) is False                     # nobody is elevated blind
    _restore(U, good)
    assert CP.subject_may_see_full_construction(ADMIN) is True and CP.subject_may_see_full_construction(FACTORY) is False


def test_the_account_export_keeps_the_scrubbing_for_an_allowlisted_oem_during_an_outage(env, monkeypatch):
    """The consumer: account_lifecycle disables scrubbing only when the subject may see the full construction."""
    from motor_ai_sim import construction_privacy as CP
    A, U = _factory_in_allowlist(env, monkeypatch)
    good = _corrupt(U)
    assert CP.subject_may_see_full_construction(FACTORY) is False                   # => the export path scrubs (it keys on this gate)
    _restore(U, good)


def _owner_report(monkeypatch):
    from motor_ai_sim import nda as N
    from motor_ai_sim import notify
    from motor_ai_sim.owner_report import send as SEND
    monkeypatch.delenv("OWNER_REPORT_TO", raising=False)
    monkeypatch.setattr(notify, "recipients", lambda: [])
    return N, notify, SEND


def test_owner_report_recipients_exclude_a_stored_oem_from_every_source(env, monkeypatch):
    A, U = _factory_in_allowlist(env, monkeypatch)
    N, notify, SEND = _owner_report(monkeypatch)
    monkeypatch.setattr(N, "admin_address", lambda: ADMIN)
    assert SEND.recipients() == [ADMIN]
    # the default (NDA admin address, derived from the first ADMIN_EMAILS entry) is a stored OEM: never a recipient
    monkeypatch.setattr(N, "admin_address", lambda: FACTORY)
    assert SEND.recipients() == [] and FACTORY not in SEND.allowed_addresses()
    # a notification-address OEM explicitly selected in OWNER_REPORT_TO is refused, and the fallback does not bring it back
    monkeypatch.setattr(notify, "recipients", lambda: [FACTORY])
    monkeypatch.setenv("OWNER_REPORT_TO", FACTORY)
    assert SEND.recipients() == []
    # a rejected selection falls back to the default owner only when that owner is eligible
    monkeypatch.setenv("OWNER_REPORT_TO", FACTORY + ",stranger@example.com")
    monkeypatch.setattr(N, "admin_address", lambda: ADMIN)
    assert SEND.recipients() == [ADMIN]


def test_owner_report_recipients_exclude_a_disabled_admin_and_stop_before_delivery(env, monkeypatch):
    A, U = _factory_in_allowlist(env, monkeypatch)
    N, notify, SEND = _owner_report(monkeypatch)
    monkeypatch.setattr(N, "admin_address", lambda: ADMIN)
    U.update_user(ADMIN, disabled=True)
    assert SEND.recipients() == []
    sent = []
    res = SEND._send(type("R", (), {"text": "t", "html": "<p>t</p>", "subject": "s"})(), "m", lambda msg: sent.append(msg))
    assert res["sent"] is False and res["error"] == "no_eligible_recipient" and res["to"] == [] and not sent


def test_owner_report_has_no_recipient_when_the_registry_cannot_be_read(env, monkeypatch):
    A, U = _factory_in_allowlist(env, monkeypatch)
    N, notify, SEND = _owner_report(monkeypatch)
    monkeypatch.setattr(N, "admin_address", lambda: ADMIN)
    assert SEND.recipients() == [ADMIN]
    good = _corrupt(U)
    assert SEND.allowed_addresses() == set() and SEND.recipients() == []
    sent = []
    res = SEND._send(type("R", (), {"text": "t", "html": "<p>t</p>", "subject": "s"})(), "m", lambda msg: sent.append(msg))
    assert res["error"] == "no_eligible_recipient" and not sent
    _restore(U, good)
    assert SEND.recipients() == [ADMIN]
