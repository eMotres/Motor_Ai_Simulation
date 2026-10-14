"""Per-user working time: counting, caps, dedupe, rate limits, aggregation, retention, admin-only.

Temp store (CLUSTER_MONITOR_DIR); server time is injected, never the wall clock.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import activity as AC
from motor_ai_sim.api import app

ADMIN = "admin@example.com"
ALICE = "alice@example.com"
BOB = "bob@example.com"
SID = "sess-aaaaaaaa"
SID2 = "sess-bbbbbbbb"
T0 = 1_800_000_000.0          # a fixed UTC instant (2027-01-15)

client = TestClient(app)


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("CLUSTER_MONITOR_DIR", str(tmp_path / "cluster"))
    AC.reset_memory()
    yield
    AC.reset_memory()


def beats(user, sid, tab, offsets, t0=T0):
    return [AC.record_beat(user, sid, tab, t0 + o)["counted_s"] for o in offsets]


def test_credit_is_gap_capped_at_60_and_first_beat_is_zero():
    assert beats(ALICE, SID, "motors", [0, 60, 120, 125]) == [0.0, 60.0, 60.0, 5.0]
    d = AC.user_days(ALICE, 1, now=T0 + 200)[0]
    assert d["active_s"] == 125 and d["sessions"] == 1 and d["tabs"] == {"motors": 125}


def test_a_gap_over_90_s_starts_a_new_stretch_and_credits_nothing():
    got = beats(ALICE, SID, "compare", [0, 60, 400, 460])
    assert got == [0.0, 60.0, 0.0, 60.0]          # the 340 s hole (idle / closed lid) is not worked time


def test_credit_never_exceeds_elapsed_time_even_when_spammed():
    out = []
    for i in range(40):
        out.append(AC.record_beat(ALICE, SID, "motors", T0 + i * 0.5)["counted_s"])
    assert sum(out) <= 20.0 + 1e-6                # 40 beats in 20 s are worth at most 20 s


def test_rate_limits_per_session_and_per_user():
    res = [AC.record_beat(ALICE, SID, "motors", T0 + i)["counted_s"] for i in range(10)]
    assert res[4:] == [0.0] * 6        # more than 4 beats a minute from one session are dropped
    assert AC.record_beat(ALICE, SID, "motors", T0 + 30).get("dropped") == "rate"
    AC.reset_memory()
    dropped = 0
    for i in range(30):                           # many sessions, one user: capped at 12 beats a minute
        dropped += "dropped" in AC.record_beat(BOB, f"sess-{i:08d}", "motors", T0 + 1000 + i * 0.1)
    assert dropped == 30 - AC.USER_BEATS_PER_MIN


def test_two_tabs_open_at_once_do_not_double_count():
    for o in (0, 60, 120, 180):
        AC.record_beat(ALICE, SID, "motors", T0 + o)
        AC.record_beat(ALICE, SID2, "compare", T0 + o + 1)
    d = AC.user_days(ALICE, 1, now=T0 + 300)[0]
    assert d["sessions"] == 2
    assert d["active_s"] <= 181                   # union of the two intervals, not 2 x 180


def test_invalid_input_is_not_counted_and_tab_is_sanitised():
    assert AC.record_beat(ALICE, "x", "motors", T0)["dropped"] == "invalid"       # session id too short
    assert AC.record_beat("", SID, "motors", T0)["dropped"] == "invalid"
    AC.record_beat(ALICE, SID, "My Die: CIANO14 40 / a@b.c", T0)
    AC.record_beat(ALICE, SID, "My Die: CIANO14 40 / a@b.c", T0 + 30)
    d = AC.user_days(ALICE, 1, now=T0 + 60)[0]
    assert d["tabs"] == {"other": 30}             # no free text ever reaches the store


def test_days_are_utc_and_zero_filled():
    beats(ALICE, SID, "motors", [0, 60])
    beats(ALICE, SID, "motors", [86400 * 2, 86400 * 2 + 60])
    days = AC.user_days(ALICE, 4, now=T0 + 86400 * 2 + 100)
    assert [d["active_s"] for d in days] == [0, 60, 0, 60]


def test_summaries_today_7d_30d_sessions_tabs_and_runs():
    from motor_ai_sim import job_usage as JU
    now = T0 + 40 * 86400
    for day, tab in ((0, "motors"), (20, "compare"), (33, "compare"), (40, "compare")):
        base = T0 + day * 86400
        beats(ALICE, f"sess-d{day:05d}x", tab, [0, 60, 120], t0=base)      # 120 s each
    for i, end in enumerate((now - 3600, now - 2 * 86400, now - 20 * 86400)):
        JU.record({"run_id": f"r{i}", "ts_start": end - 5, "ts_end": end, "user": ALICE, "client": "web",
                   "kind": "fem", "machine": "m", "node": "n", "wall_s": 5, "cpu_s": 5, "peak_rss": 1,
                   "status": "done", "cpu_method": "exclusive", "wait_s": 0, "own_node": 0})
    JU.record({"run_id": "rb", "ts_start": now - 10, "ts_end": now - 5, "user": BOB, "client": "web",
               "kind": "fem", "machine": "m", "node": "n", "wall_s": 5, "cpu_s": 5, "peak_rss": 1,
               "status": "done", "cpu_method": "exclusive", "wait_s": 0, "own_node": 0})
    rows = {r["user"]: r for r in AC.summaries(now=now + 100)}
    a = rows[ALICE]
    assert a["today_s"] == 120 and a["d7_s"] == 120                   # day 40 only
    assert a["d30_s"] == 360                                          # days 20, 33, 40; day 0 is 40 d back
    assert a["sessions_30d"] == 3 and a["sessions_7d"] == 1
    assert a["top_tabs"] == [{"tab": "compare", "seconds": 360}]
    assert a["runs_7d"] == 2 and a["runs_30d"] == 3
    assert rows[BOB]["runs_30d"] == 1 and rows[BOB]["d30_s"] == 0     # ran a job, never sent a beat
    assert a["last_seen"] == T0 + 40 * 86400 + 120


def test_retention_folds_old_user_days_into_anonymous_totals_and_drops_session_rows():
    old = T0
    beats(ALICE, SID, "motors", [0, 60], t0=old)
    beats(BOB, SID2, "motors", [0, 30], t0=old)
    now = old + (AC.RETENTION_DAYS + 5) * 86400
    res = AC.prune(now)
    assert res["user_days_folded"] == 2 and res["sessions_dropped"] == 2
    con = AC._db()
    try:
        assert con.execute("SELECT COUNT(*) FROM activity_day").fetchone()[0] == 0
        tot = con.execute("SELECT day, users, active_s, sessions FROM activity_total").fetchall()
    finally:
        con.close()
    assert tot == [(AC.day_of(old), 2, 90.0, 2)]


# ── the routes ───────────────────────────────────────────────────────────────

@pytest.fixture()
def accounts(tmp_path, monkeypatch):
    from motor_ai_sim import auth, users as U
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(ALICE, "password-a", role="user", name="Alice")
    return {"admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
            "alice": {"Authorization": f"Bearer {U.issue_token(ALICE)}"}}


def test_heartbeat_needs_a_signed_in_user(accounts):
    r = client.post("/api/activity/heartbeat", json={"sid": SID, "tab": "motors"})
    assert r.status_code == 401


def test_heartbeat_counts_server_time_and_ignores_the_client_clock(accounts):
    h = accounts["alice"]
    r = client.post("/api/activity/heartbeat", json={"sid": SID, "tab": "motors", "ts": 1}, headers=h)
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["counted_s"] == 0.0
    days = AC.user_days(ALICE, 1)
    assert days[0]["sessions"] == 1 and abs(days[0]["first_ts"] - time.time()) < 60


def test_admin_endpoints_are_admin_only(accounts):
    client.post("/api/activity/heartbeat", json={"sid": SID, "tab": "motors"}, headers=accounts["alice"])
    assert client.get("/api/admin/activity", headers=accounts["alice"]).status_code == 403
    assert client.get(f"/api/admin/activity/user?email={ALICE}", headers=accounts["alice"]).status_code == 403
    r = client.get("/api/admin/activity", headers=accounts["admin"])
    assert r.status_code == 200 and [u["user"] for u in r.json()["users"]] == [ALICE]
    r = client.get(f"/api/admin/activity/user?email={ALICE}&days=7", headers=accounts["admin"])
    assert r.status_code == 200 and len(r.json()["days"]) == 7
