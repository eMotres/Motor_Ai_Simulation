"""Idle expiry of web sessions (owner 2026-10-20) - sessions.py / session_activity.py.

The rule under test: a web session is closed ONLY when, for ``MOTRES_SESSION_IDLE_S`` seconds (default
1800), there was no user activity AND none of the account's computations was queued or running.  The
idle clock restarts at the later of the last activity and the newest job's end; a job state that cannot
be read keeps the session open.  Agent API keys / MCP tokens are a separate system and never idle out.

Everything runs against throwaway files in tmp_path (users, sessions, event log, jobs file).
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

ADMIN = "owner@example.com"
CLIENT = "client@example.com"

client = TestClient(app)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth as A
    from motor_ai_sim import compute_nodes as CN
    from motor_ai_sim import jobs as J
    from motor_ai_sim import progress as P
    from motor_ai_sim import session_activity as SA
    from motor_ai_sim import sessions as S
    from motor_ai_sim import users as U

    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(S, "_SESSIONS_FILE", tmp_path / ".sessions.json")
    monkeypatch.setattr(S, "_EVENTS_FILE", tmp_path / "auth_events.jsonl")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.delenv(SA.ENV_IDLE, raising=False)
    monkeypatch.setattr(A, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(A, "AUTH_ENFORCE", False)
    monkeypatch.setattr(A, "_reject_seen", {})
    monkeypatch.setattr(S, "_last_touch", {})
    monkeypatch.setattr(S, "_last_sweep", 0.0)
    # job sources: a jobs file in tmp_path, no live queue, an empty progress registry, no BYO nodes
    jobs_file = tmp_path / "jobs.json"
    monkeypatch.setattr(SA, "_jobs_file", lambda email: jobs_file)
    monkeypatch.setattr(J, "_QUEUE", None)
    monkeypatch.setattr(P, "_REGISTRY", None, raising=False)
    P.registry().clear()
    monkeypatch.setattr(CN, "list_jobs", lambda owner, limit=50: [])

    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(CLIENT, "password-client", role="user", name="Client")
    yield {"tmp": tmp_path, "jobs": jobs_file, "events": tmp_path / "auth_events.jsonl",
           "S": S, "U": U, "A": A, "SA": SA, "P": P, "monkeypatch": monkeypatch}
    P.registry().clear()


def _login(email="client@example.com", password="password-client") -> dict:
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _sid(env, token) -> str:
    return env["U"].verify_token(token).sid


def _age(env, sid: str, *, activity_ago: float, seen_ago: float | None = None) -> None:
    """Move a session's clocks into the past (the store is the only state)."""
    S = env["S"]
    now = time.time()
    d = S._load()
    d[sid]["last_activity"] = now - activity_ago
    d[sid]["last_seen"] = now - (activity_ago if seen_ago is None else seen_ago)
    S._save(d)


def _rec(env, sid):
    return env["S"].get(sid)


def _events(env) -> list[dict]:
    p = env["events"]
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _write_jobs(env, rows) -> None:
    env["jobs"].write_text(json.dumps({"jobs": rows}), encoding="utf-8")


# ── the timeout itself ───────────────────────────────────────────────────────

def test_default_and_env_override_and_zero_disables(env, monkeypatch):
    SA = env["SA"]
    assert SA.idle_timeout_s() == 1800.0
    monkeypatch.setenv(SA.ENV_IDLE, "120")
    assert SA.idle_timeout_s() == 120.0
    monkeypatch.setenv(SA.ENV_IDLE, "nonsense")
    assert SA.idle_timeout_s() == 1800.0
    monkeypatch.setenv(SA.ENV_IDLE, "0")
    assert SA.idle_timeout_s() == 0.0


def test_idle_beyond_the_timeout_closes_the_session_with_a_distinct_401(env):
    j = _login()
    sid = _sid(env, j["token"])
    assert _rec(env, sid)["last_activity"] > 0
    _age(env, sid, activity_ago=31 * 60)

    r = client.post("/api/auth/heartbeat", headers=_bearer(j["token"]))
    assert r.status_code == 401
    assert r.json()["reason"] == "idle_timeout"
    assert r.headers.get("x-auth-reason") == "idle_timeout"
    rec = _rec(env, sid)
    assert rec["revoked"] is True and rec["revoked_reason"] == "idle_timeout"

    revokes = [e for e in _events(env) if e["event"] == "revoke" and e["sid"] == sid]
    assert len(revokes) == 1 and revokes[0]["reason"] == "idle_timeout"

    # every later request with that token keeps saying idle_timeout (not a generic "revoked")
    me = client.get("/api/me", headers=_bearer(j["token"])).json()
    assert me["tokenRejected"] is True and me["authError"] == "idle_timeout"
    assert client.post("/api/auth/heartbeat", headers=_bearer(j["token"])).json()["reason"] == "idle_timeout"
    # and still exactly one close event
    assert len([e for e in _events(env) if e["event"] == "revoke" and e["sid"] == sid]) == 1


def test_a_session_inside_the_timeout_stays_open(env):
    j = _login()
    sid = _sid(env, j["token"])
    _age(env, sid, activity_ago=20 * 60)
    assert client.get("/api/me", headers=_bearer(j["token"])).json()["email"] == CLIENT
    assert not _rec(env, sid)["revoked"]


def test_env_override_changes_the_limit_and_zero_disables_it(env, monkeypatch):
    SA = env["SA"]
    j = _login()
    sid = _sid(env, j["token"])
    _age(env, sid, activity_ago=5 * 3600)
    monkeypatch.setenv(SA.ENV_IDLE, "0")             # disabled: five idle hours change nothing
    assert client.get("/api/me", headers=_bearer(j["token"])).json()["email"] == CLIENT
    assert not _rec(env, sid)["revoked"]
    monkeypatch.setenv(SA.ENV_IDLE, str(6 * 3600))   # a 6 h limit: 5 h is still fine
    assert client.get("/api/me", headers=_bearer(j["token"])).json()["email"] == CLIENT
    monkeypatch.setenv(SA.ENV_IDLE, "60")            # a 1 min limit closes it
    assert client.get("/api/me", headers=_bearer(j["token"])).json()["authError"] == "idle_timeout"
    assert _rec(env, sid)["revoked_reason"] == "idle_timeout"


# ── what counts as activity ──────────────────────────────────────────────────

def test_the_heartbeat_extends_the_session_but_a_get_poll_does_not(env):
    j = _login()
    sid = _sid(env, j["token"])
    _age(env, sid, activity_ago=10 * 60)
    before = _rec(env, sid)["last_activity"]

    for _ in range(3):                                   # background polls
        assert client.get("/api/me", headers=_bearer(j["token"])).status_code == 200
        client.get("/api/jobs", headers=_bearer(j["token"]))
    assert _rec(env, sid)["last_activity"] == before

    r = client.post("/api/auth/heartbeat", headers=_bearer(j["token"]))
    assert r.status_code == 200 and r.json()["ok"] is True
    assert _rec(env, sid)["last_activity"] > before + 9 * 60


def test_polls_alone_never_keep_a_session_alive(env):
    j = _login()
    sid = _sid(env, j["token"])
    _age(env, sid, activity_ago=31 * 60, seen_ago=0)     # polled a second ago, but nobody touched it for 31 min
    me = client.get("/api/me", headers=_bearer(j["token"])).json()
    assert me["authError"] == "idle_timeout"
    assert _rec(env, sid)["revoked_reason"] == "idle_timeout"


def test_a_non_get_request_and_the_working_time_beat_count_as_activity_but_a_get_poll_does_not(env):
    j = _login()
    sid = _sid(env, j["token"])
    _age(env, sid, activity_ago=10 * 60)
    before = _rec(env, sid)["last_activity"]
    r = client.post("/api/activity/heartbeat", json={"sid": "abcdefgh1234", "tab": "motors"}, headers=_bearer(j["token"]))
    assert r.status_code == 200
    # the input-driven working-time beat of the bundle already in browsers counts as activity ...
    assert _rec(env, sid)["last_activity"] > before + 9 * 60

    # ... a GET poll still does not
    _age(env, sid, activity_ago=10 * 60)
    before = _rec(env, sid)["last_activity"]
    assert client.get("/api/me", headers=_bearer(j["token"])).status_code == 200
    assert _rec(env, sid)["last_activity"] == before

    # and any other non-GET is a user action (404 is fine)
    client.post("/api/auth/sessions/nonexistent/revoke", headers=_bearer(j["token"]))
    assert _rec(env, sid)["last_activity"] > before + 9 * 60


def test_an_old_tab_that_only_sends_the_working_time_beat_stays_signed_in(env):
    """The bundle sent before the idle rule has no session heartbeat, only the input-driven beat."""
    j = _login()
    sid = _sid(env, j["token"])
    _age(env, sid, activity_ago=29 * 60)                 # one minute from the limit
    r = client.post("/api/activity/heartbeat", json={"sid": "abcdefgh1234", "tab": "motors"}, headers=_bearer(j["token"]))
    assert r.status_code == 200
    assert _rec(env, sid)["last_activity"] > time.time() - 5     # the beat refreshed the clock
    assert client.get("/api/me", headers=_bearer(j["token"])).json()["email"] == CLIENT
    assert not _rec(env, sid)["revoked"]


def test_what_counts_as_activity():
    from motor_ai_sim import session_activity as SA
    assert SA.is_user_activity("POST", "/api/kernel/run")
    assert SA.is_user_activity("PUT", "/api/panel_settings")
    assert SA.is_user_activity("DELETE", "/api/account/deletion")
    assert SA.is_user_activity("POST", "/api/auth/heartbeat")
    assert not SA.is_user_activity("GET", "/api/auth/heartbeat")
    for p in ("/api/me", "/api/jobs", "/api/jobs/abc", "/api/thermal/progress", "/api/admin/live",
              "/api/admin/analytics/live", "/api/version"):
        assert not SA.is_user_activity("GET", p), p
    assert not SA.is_user_activity("OPTIONS", "/api/kernel/run")
    assert SA.is_user_activity("POST", "/api/activity/heartbeat")      # the input-driven working-time beat
    assert not SA.is_user_activity("POST", "/api/nodes/heartbeat")


def test_last_activity_is_written_at_most_once_a_minute(env, monkeypatch):
    S = env["S"]
    j = _login()
    sid = _sid(env, j["token"])
    client.post("/api/auth/heartbeat", headers=_bearer(j["token"]))
    writes = []
    real_save = S._save
    monkeypatch.setattr(S, "_save", lambda d: (writes.append(1), real_save(d)))
    for _ in range(5):
        client.post("/api/auth/heartbeat", headers=_bearer(j["token"]))
    assert writes == [], "user activity must not cost a store write per request"
    assert not _rec(env, sid)["revoked"]


# ── computations hold a session open ─────────────────────────────────────────

def _job(state, **kw):
    now = time.time()
    row = {"run_id": "r1", "ws_id": "w", "owner": CLIENT, "kind": "fem_transient", "state": state,
           "queued_at": now - 3 * 3600, "started_at": now - 3 * 3600, "finished_at": 0.0}
    row.update(kw)
    return row


def test_idle_two_hours_with_a_running_job_stays_open(env):
    j = _login()
    sid = _sid(env, j["token"])
    _age(env, sid, activity_ago=2 * 3600)
    _write_jobs(env, [_job("running")])
    assert client.get("/api/me", headers=_bearer(j["token"])).json()["email"] == CLIENT
    assert not _rec(env, sid)["revoked"]
    env["S"].sweep_idle()                                # the sweeper agrees
    assert not _rec(env, sid)["revoked"]


def test_a_queued_job_holds_the_session_too(env):
    j = _login()
    sid = _sid(env, j["token"])
    _age(env, sid, activity_ago=2 * 3600)
    _write_jobs(env, [_job("queued", started_at=0.0)])
    assert client.get("/api/me", headers=_bearer(j["token"])).json()["email"] == CLIENT


def test_after_the_job_ends_the_session_closes_30_minutes_after_the_job_not_the_last_click(env):
    j = _login()
    sid = _sid(env, j["token"])
    now = time.time()
    _age(env, sid, activity_ago=3 * 3600)                # last click three hours ago
    _write_jobs(env, [_job("done", finished_at=now - 10 * 60)])
    assert client.get("/api/me", headers=_bearer(j["token"])).json()["email"] == CLIENT   # job ended 10 min ago
    assert not _rec(env, sid)["revoked"]

    _write_jobs(env, [_job("done", finished_at=now - 31 * 60)])                           # 31 min after the job's end
    assert client.get("/api/me", headers=_bearer(j["token"])).json()["authError"] == "idle_timeout"
    assert _rec(env, sid)["revoked_reason"] == "idle_timeout"


def test_the_account_progress_registry_holds_the_session(env):
    """A report / CAD build / any run reporting progress under the owner's name is a computation too."""
    P = env["P"]
    j = _login()
    sid = _sid(env, j["token"])
    _age(env, sid, activity_ago=2 * 3600)
    tr = P.registry().tracker("report-1", route="/api/report", owner=CLIENT)
    tr.start(10, "building")
    assert client.get("/api/me", headers=_bearer(j["token"])).json()["email"] == CLIENT
    assert not _rec(env, sid)["revoked"]
    P.registry().clear()
    assert client.get("/api/me", headers=_bearer(j["token"])).json()["authError"] == "idle_timeout"


def test_a_job_of_another_account_does_not_hold_this_session(env):
    j = _login()
    sid = _sid(env, j["token"])
    _age(env, sid, activity_ago=2 * 3600)
    env["P"].registry().tracker("r-x", route="/api/report", owner="someone@else.com").start(5, "x")
    assert client.get("/api/me", headers=_bearer(j["token"])).json()["authError"] == "idle_timeout"


def test_an_unreadable_job_state_keeps_the_session_open(env):
    j = _login()
    sid = _sid(env, j["token"])
    _age(env, sid, activity_ago=2 * 3600)
    env["jobs"].write_text("{ this is not json", encoding="utf-8")
    assert client.get("/api/me", headers=_bearer(j["token"])).json()["email"] == CLIENT
    assert not _rec(env, sid)["revoked"]
    assert env["S"].sweep_idle() == 0
    assert not _rec(env, sid)["revoked"]


def test_a_crashed_running_leftover_does_not_hold_a_session_for_ever(env):
    j = _login()
    sid = _sid(env, j["token"])
    _age(env, sid, activity_ago=2 * 3600)
    stale = time.time() - 3 * 24 * 3600
    _write_jobs(env, [_job("running", queued_at=stale, started_at=stale)])
    assert client.get("/api/me", headers=_bearer(j["token"])).json()["authError"] == "idle_timeout"


# ── agent tokens are a separate system ───────────────────────────────────────

def test_agent_api_keys_are_unaffected_by_idle_sessions(env, monkeypatch):
    from motor_ai_sim import agent_keys as K
    monkeypatch.setenv("MCP_KEYS_DIR", str(env["tmp"]))
    token, _pub = K.create_key(CLIENT, "agent")
    j = _login()
    sid = _sid(env, j["token"])
    _age(env, sid, activity_ago=5 * 3600)
    monkeypatch.setenv(env["SA"].ENV_IDLE, "60")
    principal, why = K.verify(f"Bearer {token}")
    assert why == "ok" and principal is not None and principal.email == CLIENT
    # and an agent key presented as a web bearer never touches the session registry
    before = json.dumps(env["S"]._load(), sort_keys=True)
    client.get("/api/me", headers=_bearer(token))
    assert json.dumps(env["S"]._load(), sort_keys=True) == before


def test_legacy_token_without_a_sid_is_not_subject_to_the_idle_rule(env):
    token = env["U"].issue_token(CLIENT)                  # no sid: not in the registry
    assert client.get("/api/me", headers=_bearer(token)).json()["email"] == CLIENT


# ── the sweep and Admin's numbers ────────────────────────────────────────────

def test_sweep_closes_sessions_nobody_returns_to_and_admin_counts_are_exact(env):
    S = env["S"]
    now = time.time()
    a = _login()                                          # active a minute ago
    b = _login()                                          # active 3 min ago
    c = _login()                                          # idle 40 min
    d = _login()                                          # idle 2 h, but running a job
    sa, sb, sc, sd = (_sid(env, x["token"]) for x in (a, b, c, d))
    _age(env, sa, activity_ago=60)
    _age(env, sb, activity_ago=180)
    _age(env, sc, activity_ago=40 * 60)
    _age(env, sd, activity_ago=2 * 3600)
    _write_jobs(env, [_job("running")])                   # the registry has one account: the job holds its sessions

    # nobody has been swept yet: counts are computed from timestamps, so "open" is already exact
    counts = S.live_counts(now)
    assert counts["active_sessions"] == 2                 # a, b (5-minute window)
    assert counts["active_accounts"] == 1
    assert counts["open_sessions"] == 4                   # everything: the account has a job in flight
    assert counts["idle_timeout_s"] == 1800.0 and counts["window_s"] == 300.0

    _write_jobs(env, [])                                  # job gone, no end time known: c and d idle by their clocks
    counts = S.live_counts(now)
    assert counts["open_sessions"] == 2 and counts["active_sessions"] == 2
    assert S.sweep_idle(now) == 2
    assert _rec(env, sc)["revoked_reason"] == "idle_timeout" and _rec(env, sd)["revoked_reason"] == "idle_timeout"
    assert not _rec(env, sa)["revoked"] and not _rec(env, sb)["revoked"]
    closes = [e for e in _events(env) if e["event"] == "revoke" and e["reason"] == "idle_timeout"]
    assert {e["sid"] for e in closes} == {sc, sd}
    assert S.live_counts(now)["open_sessions"] == 2


def test_admin_live_card_carries_the_session_counts(env, monkeypatch):
    from motor_ai_sim.routes import admin_analytics as AA
    monkeypatch.setattr(AA, "_config", lambda: (_ for _ in ()).throw(AA.AnalyticsUnavailable("off")))
    monkeypatch.setattr(AA, "fetch_active_signed_in_users", lambda **kw: [])
    monkeypatch.setattr(AA, "fetch_live_events", lambda **kw: [])
    AA.reset_live_cache()
    _login()
    payload = AA.fetch_live_analytics()
    assert payload["sessions"]["open_sessions"] >= 1
    assert payload["sessions"]["active_sessions"] >= 1
    AA.reset_live_cache()


def test_sessions_from_before_the_rule_start_their_clock_at_first_sight(env):
    S = env["S"]
    j = _login()
    sid = _sid(env, j["token"])
    d = S._load()
    d[sid].pop("last_activity")
    d[sid]["last_seen"] = time.time() - 5                 # a polling tab of the old bundle
    S._save(d)
    assert client.get("/api/me", headers=_bearer(j["token"])).json()["email"] == CLIENT
    assert _rec(env, sid)["last_activity"] > time.time() - 30
    # one that nobody has seen for hours is simply idle
    j2 = _login()
    sid2 = _sid(env, j2["token"])
    d = S._load()
    d[sid2].pop("last_activity")
    d[sid2]["last_seen"] = time.time() - 3 * 3600
    S._save(d)
    assert client.get("/api/me", headers=_bearer(j2["token"])).json()["authError"] == "idle_timeout"
