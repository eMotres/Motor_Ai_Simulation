"""Two regions, stage 3 - the fixes after the independent review of PR #630 (docs/regions/DESIGN.md sections 5, 8, 13).

Writers the guard missed (raw Google tokens, MCP keys / OAuth tokens, a crashing check), the freeze on the password and
reset routes, enumeration (every 421 counts per IP; reset relayed, never 421), the hand-off store (single use across
processes, fail closed), the refusal reasons of a ticket, and a peer that really times out at 2 s.  Same two-server
simulation as tests/test_regions_stage3_auth.py; nothing leaves tmp_path.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jwt
import pytest

from motor_ai_sim.regions import guard as G
from motor_ai_sim.regions import handoff as H
from motor_ai_sim.regions import peer as P
from motor_ai_sim.regions import store as RS
from tests.test_regions_stage3_auth import (EU_USER, PW, SECRET, STAFF, US_USER, _as, _call, _directory_has, _login,
                                            _migrate_record, client, env)  # noqa: F401 - env is the fixture

ROOT = Path(__file__).resolve().parents[1]


def _session_token(env, email: str) -> str:
    """A token with a real server-side session (sid), as a sign-in issues it."""
    sid = env["S"].create(email, expires=time.time() + 3600, login_method="password", ip="203.0.113.1",
                          user_agent="pytest")
    return env["U"].issue_token(email, sid=sid)


def _forge(payload: dict) -> str:
    body = H._b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return f"{H.PREFIX}.{body}.{H._sig(SECRET, body)}"


def _us_record(env, email: str, **fields) -> None:
    U = env["U"]
    _as(env, "us")
    with U._LOCK:
        users = U._load()
        users[email] = {"role": "user", "name": "x", "disabled": False, "created": "2026", "home_region": "us", **fields}
        U._save(users)


# ── HIGH 1: every writer the guard must see ─────────────────────────────────

def test_guard_with_real_session_tokens(env):
    tok = _session_token(env, EU_USER)
    RS.set_region_freeze(EU_USER, True, by="test")
    assert _call("/api/projects", tok, "POST")[0] == 423
    assert _call("/api/projects", tok, "GET") == "passed"
    _migrate_record(env)
    status, body = _call("/api/projects", _session_token(env, US_USER), "GET")
    assert status == 421
    _as(env, "us")
    assert client.post("/api/auth/region-handoff", json={"ticket": body["detail"]["handoff"]}).status_code == 200


def test_a_raw_google_token_cannot_bypass_the_freeze_or_the_guard(env):
    from motor_ai_sim import auth as A
    google_like = jwt.encode({"email": EU_USER, "sub": "g-1"}, "google-signs-this-not-us", algorithm="HS256")
    RS.set_region_freeze(EU_USER, True, by="test")
    env["mp"].setattr(A, "_verify_google_token", lambda t: None)          # Google says no: the role gate answers it
    assert _call("/api/projects", google_like, "POST") == "passed"
    env["mp"].setattr(A, "_verify_google_token", lambda t: {"email": EU_USER, "email_verified": True})
    assert _call("/api/projects", google_like, "POST")[0] == 423
    _migrate_record(env)
    us_google = jwt.encode({"email": US_USER, "sub": "g-2"}, "google-signs-this-not-us", algorithm="HS256")
    env["mp"].setattr(A, "_verify_google_token", lambda t: {"email": US_USER, "email_verified": True})
    status, body = _call("/api/projects", us_google, "GET")
    assert status == 421 and json.loads(H._unb64(body["detail"]["handoff"].split(".")[1]))["m"] == "google"


def test_mcp_keys_and_oauth_respect_the_freeze_and_the_guard(env):
    from motor_ai_sim import agent_keys as K
    from motor_ai_sim import mcp_app as M
    tok, _ = K.create_key(EU_USER, name="laptop")
    principal, _why = M.verify_any(f"Bearer {tok}")
    assert principal is not None and principal.email == EU_USER
    RS.set_region_freeze(EU_USER, True, by="test")
    assert M.verify_any(f"Bearer {tok}") == (None, "account_migrating")
    RS.set_region_freeze(EU_USER, False)
    us_tok, _ = K.create_key(US_USER, name="agent")
    _migrate_record(env)
    assert M.verify_any(f"Bearer {us_tok}") == (None, "wrong_region")


def test_a_crashing_check_keeps_a_flagged_write_out(env):
    tok = env["U"].issue_token(EU_USER)
    RS.set_region_freeze(EU_USER, True, by="test")

    def boom(*a, **k):
        raise OSError("registry unreadable")
    env["mp"].setattr(env["U"], "verify_token", boom)
    assert _call("/api/projects", tok, "POST")[0] == 423                  # fail closed for a flagged account's write
    assert _call("/api/projects", tok, "GET") == "passed"
    unflagged = jwt.encode({"email": US_USER}, "x", algorithm="HS256")
    assert _call("/api/projects", unflagged, "POST") == "passed"           # not flagged: the role gate's business


# ── HIGH 2: password and reset routes of a frozen / moved account ───────────

def test_frozen_account_cannot_change_or_reset_its_password(env):
    E = env["E"]
    mails = []
    env["mp"].setattr(E, "send", lambda to, subject, body, **kw: mails.append(to) or True)
    tok = _login(EU_USER).json()["token"]
    sid_r = env["U"].issue_short_link(EU_USER, "reset")
    RS.set_region_freeze(EU_USER, True, by="test")
    h = {"Authorization": f"Bearer {tok}"}
    r = client.post("/api/auth/password", json={"old_password": PW, "new_password": "a new password 12"}, headers=h)
    assert r.status_code == 423 and r.json()["detail"]["code"] == "account_migrating"
    assert client.post("/api/auth/reset/request", json={"email": EU_USER}).status_code == 202 and mails == []
    r = client.post("/api/auth/reset/confirm", json={"token": sid_r, "password": "a new password 12"})
    assert r.status_code == 423
    page = client.post("/api/auth/link/reset", data={"id": sid_r, "password": "a new password 12",
                                                     "password2": "a new password 12"})
    assert "being moved" in page.text
    assert client.patch(f"/api/auth/users/{EU_USER}", json={"password": "a new password 12"}).status_code == 423
    assert client.patch(f"/api/auth/users/{EU_USER}", json={"disabled": True}).status_code == 423
    assert env["U"].authenticate(EU_USER, PW)[0] == "ok"                   # nothing changed
    RS.set_region_freeze(EU_USER, False)
    assert env["U"].find_short_link(sid_r, "reset")["state"] == "valid"    # the link survives the freeze


def test_a_moved_account_changes_its_password_at_home(env):
    _migrate_record(env)
    h = {"Authorization": f"Bearer {env['U'].issue_token(US_USER)}"}
    r = client.post("/api/auth/password", json={"old_password": PW, "new_password": "a new password 12"}, headers=h)
    assert r.status_code == 421 and r.json()["detail"]["region"] == "us"


# ── MED 4: enumeration ──────────────────────────────────────────────────────

def test_every_421_counts_against_the_ip_limit(env):
    _directory_has("only.on.us@example.com")
    codes = [_login("only.on.us@example.com", "whatever").status_code for _ in range(32)]
    assert codes[0] == 421 and 429 in codes and codes.index(429) <= 30
    env["mp"].setenv("TERMS_ACCEPTANCE_DISABLED", "1")
    env["E"].reset_limits()
    env["mp"].setattr(P, "get", lambda path, **kw: None)
    for _ in range(30):
        env["E"].LOGIN_IP.hit("203.0.113.10")
    r = client.post("/api/auth/register", json={"email": "only.on.us@example.com", "password": PW, "name": "X"},
                    headers={"X-Forwarded-For": "203.0.113.10"})
    assert r.status_code == 429                                           # the sign-up 421 is limited the same way


def test_resend_and_reset_never_answer_421(env):
    _directory_has("only.on.us@example.com")
    env["mp"].setattr(P, "post", lambda *a, **k: {"ok": True})
    for path in ("/api/auth/register/resend", "/api/auth/reset/request"):
        a = client.post(path, json={"email": "only.on.us@example.com"})
        b = client.post(path, json={"email": "nobody.at.all@example.com"})
        assert a.status_code == b.status_code == 202 and a.json() == b.json(), path


def test_peer_reset_mail_route_mails_only_its_own_accounts(env):
    _us_record(env, US_USER, pw_hash="x")
    mails = []
    env["mp"].setattr(env["E"], "send", lambda to, subject, body, **kw: mails.append(to) or True)
    path = P.PREFIX + "/auth/reset-mail"
    for em in (US_USER, "nobody@example.com"):
        raw = json.dumps({"email": em}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        h = {**P.sign_headers("POST", path, raw, secret=SECRET, region="eu"), "Content-Type": "application/json"}
        r = client.post(path, content=raw, headers=h)
        assert r.status_code == 200 and r.json() == {"ok": True}
    assert mails == [US_USER]
    assert client.post(path, json={"email": US_USER}).status_code == 404    # unsigned


def test_change_email_to_an_address_the_peer_hosts_changes_nothing(env):
    U = env["U"]
    assert U.register_self("typo@example.com", PW, "Typo") == "created"
    _directory_has("taken.on.us@example.com")
    env["mp"].setattr(env["E"], "send", lambda *a, **k: True)
    r = client.post("/api/auth/register/change-email", json={"email": "typo@example.com", "password": PW,
                                                             "new_email": "taken.on.us@example.com"})
    assert r.status_code == 202
    assert U.get_user("typo@example.com") is not None and U.get_user("taken.on.us@example.com") is None


# ── the hand-off ticket ─────────────────────────────────────────────────────

def test_ticket_refusal_reasons(env):
    now = time.time()
    base = {"e": US_USER, "to": "us", "exp": int(now) + 30, "m": "google", "ph": ""}
    _us_record(env, US_USER)
    with pytest.raises(H.HandoffError) as e:
        H.consume(_forge({**base, "from": "us", "n": "n" * 20}), now=now)
    assert e.value.reason == "wrong_source"
    _us_record(env, STAFF, role="engineer")
    with pytest.raises(H.HandoffError) as e:
        H.consume(_forge({**base, "e": STAFF, "from": "eu", "n": "s" * 20}), now=now)
    assert e.value.reason == "not_allowed"
    _us_record(env, "off@example.com", disabled=True)
    with pytest.raises(H.HandoffError) as e:
        H.consume(_forge({**base, "e": "off@example.com", "from": "eu", "n": "d" * 20}), now=now)
    assert e.value.reason == "not_allowed"
    _us_record(env, "euhome@example.com", home_region="eu")
    with pytest.raises(H.HandoffError) as e:
        H.consume(_forge({**base, "e": "euhome@example.com", "from": "eu", "n": "h" * 20}), now=now)
    assert e.value.reason == "not_home"
    assert H.consume(_forge({**base, "from": "eu", "n": "g" * 20}), now=now)["email"] == US_USER


def _spend_in_other_process(env, nonce: str, exp: int) -> int:
    code = ("import sys, time\n"
            "from motor_ai_sim.regions import handoff as H\n"
            "try:\n"
            f"    H._spend({nonce!r}, {exp}, time.time())\n"
            "except H.HandoffError as e:\n"
            "    print(e.reason); sys.exit(3)\n")
    envv = {**os.environ, "PYTHONPATH": str(ROOT / "src"),
            "MOTRES_REGION_HANDOFF_FILE": os.environ["MOTRES_REGION_HANDOFF_FILE"]}
    return subprocess.run([sys.executable, "-c", code], env=envv, capture_output=True, timeout=120).returncode


def test_a_ticket_is_single_use_across_processes(env):
    _as(env, "us")
    exp = int(time.time()) + 60
    H._spend("a" * 24, exp, time.time())                                   # this API worker burns it ...
    assert _spend_in_other_process(env, "a" * 24, exp) == 3                # ... another worker refuses it
    assert _spend_in_other_process(env, "b" * 24, exp) == 0                # a worker burns a new one ...
    with pytest.raises(H.HandoffError) as e:
        H._spend("b" * 24, exp, time.time())                               # ... and this one refuses it
    assert e.value.reason == "replay"


def test_a_corrupt_used_store_refuses_instead_of_forgetting(env):
    _as(env, "us")
    Path(os.environ["MOTRES_REGION_HANDOFF_FILE"]).write_text("{not json", encoding="utf-8")
    with pytest.raises(H.HandoffError) as e:
        H._spend("c" * 24, int(time.time()) + 60, time.time())
    assert e.value.reason == "store_unavailable"


# ── sign-up against a slow peer ─────────────────────────────────────────────

class _Slow(BaseHTTPRequestHandler):
    def do_GET(self):                                                       # noqa: N802
        time.sleep(3.0)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"region": "us", "entries": {}}')
        except OSError:
            pass

    def log_message(self, *a):
        pass


def test_sign_up_does_not_wait_longer_than_2_s_for_the_peer(env):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Slow)
    srv.daemon_threads = True
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        env["mp"].setenv("MOTRES_PEER_URL", f"http://127.0.0.1:{srv.server_address[1]}")
        env["mp"].setenv("TERMS_ACCEPTANCE_DISABLED", "1")
        t0 = time.monotonic()
        assert RS.peer_lookup("slow.case@example.com") is None
        dt = time.monotonic() - t0
        assert 1.8 <= dt < 2.9, dt                                          # the 2 s timeout, not the peer's 3 s
        t0 = time.monotonic()
        r = client.post("/api/auth/register", json={"email": "slow.case2@example.com", "password": PW, "name": "Slow"},
                        headers={"X-Forwarded-For": "203.0.113.77"})
        assert r.status_code == 202 and time.monotonic() - t0 < 2.9          # proceeds locally
        assert env["U"].get_user("slow.case2@example.com") is not None
    finally:
        srv.shutdown()
        srv.server_close()
