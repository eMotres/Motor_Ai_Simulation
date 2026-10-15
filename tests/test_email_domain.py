"""The e-mail domain must exist before a verification or reset mail is sent (email_domain.py), the "wrong address" fix for an
unconfirmed account (change-email / resend routes), the admin's edit of an unverified address, and the last-mail record.

Everything runs against tmp_path; the resolver is mocked (tests/conftest.py turns every domain into "exists", the tests below
set their own verdicts) - no test waits for a real DNS server or sends a real e-mail.
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time

import pytest

from motor_ai_sim import email_domain as ED
from tests.test_email_password_auth import ADMIN, NEW, PW, _admin_h, _login, _register, _smtp_on, _token_from, client, env  # noqa: F401

ORIG_EXISTS = ED._exists          # captured at import, before the autouse fixture replaces it


def verdicts(mp, table, default="yes"):
    """Resolver verdict per domain; records the domains asked."""
    asked: list[str] = []

    def fake(domain, timeout):
        asked.append(domain)
        return table.get(domain, default)
    mp.setattr(ED, "_exists", fake)
    ED.clear_cache()
    return asked


@pytest.fixture()
def cp(env):
    from motor_ai_sim import comm_prefs as CP
    env["mp"].setattr(CP, "_STATE_FILE", env["tmp"] / "comm_prefs.json")
    env["mp"].setattr(CP, "_HISTORY_FILE", env["tmp"] / "comm_prefs_history.jsonl")
    env["mp"].setattr(CP, "_ROOT", env["tmp"])
    env["CP"] = CP
    return env


# -- the checker -------------------------------------------------------------------------------------------

def test_check_verdicts_and_fail_open():
    assert ED.check("a@movigor.com", resolver=lambda d, t: "yes").ok
    c = ED.check("d.xu@movigor.tech", resolver=lambda d, t: "no")
    assert not c.ok and c.reason == "nxdomain" and c.domain == "movigor.tech" and c.suggestion == ""
    assert ED.message(c) == "The domain movigor.tech does not exist - please check the address."
    # cannot tell (timeout, no network): never blocks
    u = ED.check("a@movigor.tech", resolver=lambda d, t: "unknown")
    assert u.ok and u.reason == "unknown"
    # not this module's job to refuse a format
    assert ED.check("a@bad_domain!", resolver=lambda d, t: "no").ok
    assert ED.check("a@localhost", resolver=lambda d, t: "no").ok


def test_suggestions_only_for_obvious_provider_typos():
    for typo, right in (("gmial.com", "gmail.com"), ("qq.con", "qq.com"), ("163.con", "163.com"), ("hotmial.com", "hotmail.com")):
        c = ED.check(f"x@{typo}", resolver=lambda d, t: "no")
        assert not c.ok and c.suggestion == right
        assert f"(did you mean {right}?)" in ED.message(c)
    # no guessing company domains
    assert ED.check("x@movigor.tech", resolver=lambda d, t: "no").suggestion == ""
    assert ED.check("x@acme-motors.cm", resolver=lambda d, t: "no").suggestion == ""


def test_getaddrinfo_layer(monkeypatch):
    def raising(code):
        def f(*a, **k):
            raise socket.gaierror(code, "x")
        return f
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [("x",)])
    assert ED._getaddrinfo("example.com", 1.0) == "yes"
    monkeypatch.setattr(socket, "getaddrinfo", raising(socket.EAI_NONAME))
    assert ED._getaddrinfo("nope.invalid", 1.0) == "no"
    monkeypatch.setattr(socket, "getaddrinfo", raising(getattr(socket, "EAI_AGAIN", -3)))
    assert ED._getaddrinfo("slow.example", 1.0) == "unknown"          # temporary failure: do not block
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: time.sleep(3))
    t0 = time.time()
    assert ED._getaddrinfo("hang.example", 0.1) == "unknown"          # the 2 s limit (here 0.1 s) never blocks the request
    assert time.time() - t0 < 1.5


def test_nxdomain_is_confirmed_by_the_resolver_so_an_mx_only_domain_is_not_refused(monkeypatch):
    monkeypatch.setattr(ED, "_getaddrinfo", lambda d, t: "no")
    monkeypatch.setattr(ED, "_resolvers", lambda: ["192.0.2.53"])
    rc = {}
    monkeypatch.setattr(ED, "_dns_rcode", lambda d, server, t, port=53: rc.get("v"))
    rc["v"] = 3
    assert ORIG_EXISTS("movigor.tech", 1.0) == "no"                  # NXDOMAIN
    rc["v"] = 0
    assert ORIG_EXISTS("mx-only.example", 1.0) == "yes"              # the name exists, only no A record
    rc["v"] = 2                                                      # SERVFAIL
    assert ORIG_EXISTS("x.example", 1.0) == "unknown"
    rc["v"] = None                                                   # timeout
    assert ORIG_EXISTS("x.example", 1.0) == "unknown"
    monkeypatch.setattr(ED, "_resolvers", lambda: [])                # a workstation with no resolv.conf
    assert ORIG_EXISTS("movigor.tech", 1.0) == "unknown"
    monkeypatch.setattr(ED, "_getaddrinfo", lambda d, t: "yes")
    assert ORIG_EXISTS("example.com", 1.0) == "yes"


def test_dns_rcode_reads_the_response_code_of_a_real_udp_exchange():
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    modes = iter(["nx", "ok", "wrong_id"])

    def serve():
        for mode in ("nx", "ok", "wrong_id"):
            data, addr = srv.recvfrom(512)
            qid = struct.unpack(">H", data[:2])[0]
            if mode == "wrong_id":
                qid ^= 0xFFFF
            rcode = 3 if mode == "nx" else 0
            srv.sendto(struct.pack(">HHHHHH", qid, 0x8180 | rcode, 1, 0, 0, 0) + data[12:], addr)
    threading.Thread(target=serve, daemon=True).start()
    assert ED._dns_rcode("movigor.tech", "127.0.0.1", 1.0, port) == 3
    assert ED._dns_rcode("movigor.com", "127.0.0.1", 1.0, port) == 0
    assert ED._dns_rcode("spoof.example", "127.0.0.1", 0.5, port) is None      # a response for another query is ignored
    srv.close()
    assert ED._dns_rcode("x.example", "127.0.0.1", 0.2, port) is None          # nobody listening: no verdict


def test_verdicts_are_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(ED, "_exists", lambda d, t: calls.append(d) or "no")
    ED.clear_cache()
    assert not ED.check("a@movigor.tech").ok and not ED.check("b@movigor.tech").ok
    assert calls == ["movigor.tech"]
    monkeypatch.setattr(ED, "_exists", lambda d, t: calls.append(d) or "unknown")
    ED.clear_cache()
    ED.check("a@slow.example")
    ED.check("a@slow.example")
    assert calls.count("slow.example") == 2                           # "could not tell" is not remembered


# -- register / reset ---------------------------------------------------------------------------------------

def test_register_with_a_nonexistent_domain_is_refused_before_anything_is_created_or_sent(env):
    _smtp_on(env)
    asked = verdicts(env["mp"], {"movigor.tech": "no"})
    r = _register(email="d.xu@movigor.tech")
    assert r.status_code == 422, r.text
    j = r.json()
    assert j["code"] == "auth.email_domain_unknown" and j["params"] == {"domain": "movigor.tech", "suggestion": ""}
    assert "The domain movigor.tech does not exist" in j["detail"]
    assert env["outbox"] == [] and env["U"].get_user("d.xu@movigor.tech") is None
    assert "movigor.tech" in asked
    # a provider typo carries the suggestion and its own code
    verdicts(env["mp"], {"gmial.com": "no"})
    r = _register(email="x@gmial.com")
    assert r.status_code == 422 and r.json()["code"] == "auth.email_domain_unknown_suggest"
    assert r.json()["params"]["suggestion"] == "gmail.com" and "did you mean gmail.com?" in r.json()["detail"]


def test_register_goes_ahead_when_the_resolver_cannot_tell_or_the_domain_exists(env):
    _smtp_on(env)
    verdicts(env["mp"], {"movigor.com": "yes", "slow.example": "unknown"})
    assert _register(email="a@movigor.com").status_code == 202
    assert _register(email="b@slow.example", ip="203.0.113.6").status_code == 202
    assert [m["to"] for m in env["outbox"]] == ["a@movigor.com", "b@slow.example"]


def test_reset_request_checks_the_domain_too(env):
    _smtp_on(env)
    verdicts(env["mp"], {"movigor.tech": "no"})
    r = client.post("/api/auth/reset/request", json={"email": "d.xu@movigor.tech"})
    assert r.status_code == 422 and r.json()["code"] == "auth.email_domain_unknown" and env["outbox"] == []
    verdicts(env["mp"], {})
    assert client.post("/api/auth/reset/request", json={"email": NEW}).status_code == 202      # unknown account: same answer as ever


# -- resend ------------------------------------------------------------------------------------------------------

def test_resend_mails_an_unconfirmed_account_and_is_rate_limited(env):
    _smtp_on(env)
    _register()
    assert len(env["outbox"]) == 1
    for _ in range(2):
        assert client.post("/api/auth/register/resend", json={"email": NEW}).status_code == 202
    assert len(env["outbox"]) == 3
    assert all(m["subject"] == "Confirm your e-mail address" and m["to"] == NEW for m in env["outbox"])
    # the fourth in the hour is silently not mailed (a 429 here would reveal that the address was asked about)
    r = client.post("/api/auth/register/resend", json={"email": NEW})
    assert r.status_code == 202 and len(env["outbox"]) == 3


def test_resend_says_nothing_about_other_addresses(env):
    _smtp_on(env)
    env["U"].create_user("done@example.com", "password-done")            # a confirmed account
    a = client.post("/api/auth/register/resend", json={"email": "done@example.com"})
    b = client.post("/api/auth/register/resend", json={"email": "nobody@example.com"})
    assert a.status_code == b.status_code == 202 and a.json() == b.json() and env["outbox"] == []
    assert client.post("/api/auth/register/resend", json={"email": "nope"}).status_code == 422


# -- change-email -----------------------------------------------------------------------------------------------

def _change(old=NEW, pw=PW, new="fixed@movigor.com", ip="203.0.113.5"):
    return client.post("/api/auth/register/change-email", json={"email": old, "password": pw, "new_email": new},
                       headers={"X-Forwarded-For": ip})


def test_change_email_moves_the_account_and_mails_the_new_address(cp):
    env = cp
    _smtp_on(env)
    r = client.post("/api/auth/register", json={"email": "d.xu@movigor.tech", "password": PW, "name": "D Xu",
                                                "comm_prefs": {"new_motors": True}, "lang": "zh-CN"},
                    headers={"X-Forwarded-For": "203.0.113.5"})
    assert r.status_code == 202                                              # (the resolver says "exists" here: the typo got through)
    old_tok = _token_from(env["outbox"][-1], "verify")
    assert _change(old="d.xu@movigor.tech", new="d.xu@movigor.com").status_code == 202
    assert env["U"].get_user("d.xu@movigor.tech") is None and env["U"].get_user("d.xu@movigor.com")["name"] == "D Xu"
    mail = env["outbox"][-1]
    assert mail["to"] == "d.xu@movigor.com" and mail["subject"] == "Confirm your e-mail address"
    # the old link names the old address: dead; the new one works and the account can sign in
    assert client.post("/api/auth/verify", json={"token": old_tok}).status_code == 400
    assert client.post("/api/auth/verify", json={"token": _token_from(mail, "verify")}).status_code == 200
    assert _login("d.xu@movigor.com").status_code == 200
    assert _login("d.xu@movigor.tech").status_code == 401
    # what was ticked at sign-up follows the account (fresh email_change line under the new address)
    CP = env["CP"]
    assert CP.get("d.xu@movigor.com")["topics"]["new_motors"] is True and CP.get("d.xu@movigor.tech")["topics"]["new_motors"] is False
    assert [(r["user"], r["source"], r["lang"]) for r in CP.history("d.xu@movigor.com")] == [("d.xu@movigor.com", "email_change", "zh-CN")]
    assert len(CP.history("d.xu@movigor.tech")) == 1                          # the old address keeps its own record


def test_change_email_needs_the_password_and_counts_failures(env):
    _smtp_on(env)
    _register()
    n = len(env["outbox"])
    assert _change(pw="wrong password here").status_code == 400
    assert _change(old="nobody@example.com").status_code == 400                # same answer for an unknown account
    assert env["U"].get_user(NEW) is not None and len(env["outbox"]) == n
    codes = [_change(pw="wrong password here", ip="198.51.100.77").status_code for _ in range(12)]
    assert 429 in codes                                                       # locked like a sign-in
    assert _change().status_code == 429                                       # even the right password, while locked


def test_change_email_is_for_unconfirmed_accounts_only(env):
    _smtp_on(env)
    env["U"].create_user("done@example.com", "password-done")
    assert _change(old="done@example.com", pw="password-done").status_code == 400
    assert env["U"].get_user("done@example.com") is not None


def test_change_email_refuses_a_nonexistent_new_domain_and_never_reveals_an_existing_account(env):
    _smtp_on(env)
    _register()
    verdicts(env["mp"], {"movigor.tech": "no"})
    r = _change(new="d.xu@movigor.tech")
    assert r.status_code == 422 and r.json()["code"] == "auth.email_domain_unknown" and env["U"].get_user(NEW) is not None
    verdicts(env["mp"], {})
    env["U"].create_user("taken@example.com", "password-taken")
    n = len(env["outbox"])
    r = _change(new="taken@example.com", ip="203.0.113.9")
    assert r.status_code == 202 and r.json() == client.post("/api/auth/reset/request", json={"email": "x@example.com"}).json()
    assert env["U"].get_user(NEW) is not None                                 # nothing moved
    assert env["outbox"][n]["to"] == "taken@example.com" and "tried to register" in env["outbox"][n]["body"]
    # the same address again just resends
    n = len(env["outbox"])
    assert _change(new=NEW, ip="203.0.113.10").status_code == 202 and env["outbox"][n]["to"] == NEW


# -- admin ----------------------------------------------------------------------------------------------------------

def test_admin_edits_an_unverified_address_and_the_new_one_is_mailed(env):
    _smtp_on(env)
    _register(email="d.xu@movigor.tech")
    h = _admin_h()
    r = client.patch("/api/auth/users/d.xu@movigor.tech", json={"new_email": "d.xu@movigor.com", "name": "D Xu"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["email"] == "d.xu@movigor.com" and r.json()["user"]["name"] == "D Xu"
    assert env["U"].get_user("d.xu@movigor.tech") is None and env["outbox"][-1]["to"] == "d.xu@movigor.com"
    # a confirmed account's address is never edited here; a dead domain is refused; a taken address is refused
    env["U"].create_user("done@example.com", "password-done")
    assert client.patch("/api/auth/users/done@example.com", json={"new_email": "x@movigor.com"}, headers=h).status_code == 409
    verdicts(env["mp"], {"movigor.tech": "no"})
    assert client.patch("/api/auth/users/d.xu@movigor.com", json={"new_email": "d.xu@movigor.tech"}, headers=h).status_code == 422
    verdicts(env["mp"], {})
    assert client.patch("/api/auth/users/d.xu@movigor.com", json={"new_email": "done@example.com"}, headers=h).status_code == 409
    assert client.patch("/api/auth/users/nobody@example.com", json={"new_email": "x@movigor.com"}, headers=h).status_code == 404
    assert client.patch("/api/auth/users/d.xu@movigor.com", json={"new_email": "x@movigor.com"}).status_code in (401, 403)


def test_last_mail_is_recorded_per_account_and_listed_for_the_admin(env):
    from motor_ai_sim import auth_email as E
    env["mp"].setenv("SMTP_USER", "noreply@aerostator.com")
    env["mp"].setenv("SMTP_PASS", "x")
    sent = []
    env["mp"].setattr(E, "deliver", lambda msg: sent.append(msg))
    _register()                                                              # real E.send path, delivery mocked (daemon thread)
    for _ in range(50):
        if (env["U"].get_user(NEW) or {}).get("last_mail"):
            break
        time.sleep(0.05)
    lm = env["U"].get_user(NEW)["last_mail"]
    assert lm["kind"] == "verify" and lm["ok"] is True and lm["ts"] and sent and sent[0]["To"] == NEW
    rows = {u["email"]: u for u in client.get("/api/auth/users", headers=_admin_h()).json()["users"]}
    assert rows[NEW]["email_verified"] is False and rows[NEW]["last_mail"]["kind"] == "verify"
    assert rows[ADMIN]["last_mail"] is None
    # a refused message is recorded as failed
    env["mp"].setattr(E, "deliver", lambda msg: (_ for _ in ()).throw(OSError("refused")))
    assert E.send(NEW, "s", "b", block=True, kind="reset") is False
    lm = env["U"].get_user(NEW)["last_mail"]
    assert lm["kind"] == "reset" and lm["ok"] is False and lm["error"] == "OSError"
    # nothing is recorded without a kind, nor for an unknown address
    E.send("ghost@example.com", "s", "b", block=True, kind="verify")
    assert env["U"].get_user("ghost@example.com") is None
