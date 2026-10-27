"""Queue position and expected finish of a run (owner 2026-10-27): the duration statistics, the scheduler replay and the
honest display rules.  Synthetic runs, a frozen clock (every function takes ``now``), no solver."""
from __future__ import annotations

import json
import threading

import pytest

from motor_ai_sim import job_eta as E
from motor_ai_sim import jobs as J
from motor_ai_sim import progress as P
from motor_ai_sim import workspace as WS

NOW = 1_800_000_000.0                      # frozen "now"


@pytest.fixture()
def st(tmp_path):
    return E.DurationStats(tmp_path / "stats.json")


def feed(st, kind, values, size=None):
    for v in values:
        st.record(kind, v, size)


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------

def test_percentile_is_interpolated():
    xs = list(range(1, 11))
    assert E.percentile(xs, 0.5) == 5.5
    assert E.percentile(xs, 0.8) == pytest.approx(8.2)
    assert E.percentile([7.0], 0.8) == 7.0


def test_median_and_p80_of_the_recorded_durations(st):
    feed(st, "transient", [100, 200, 300, 400, 500])
    got = st.lookup("transient")
    assert got["median"] == 300 and got["p80"] == pytest.approx(420) and got["n"] == 5
    assert st.lookup("never.seen") is None


def test_only_the_last_fifty_are_kept_and_the_file_survives_a_restart(tmp_path):
    path = tmp_path / "shared" / "compute" / "stats.json"          # the folder does not exist yet
    a = E.DurationStats(path)
    feed(a, "transient", [1000.0] * 30 + [10.0] * 50)               # the 30 old long ones fall out of the window
    assert a.lookup("transient")["n"] == 50 and a.lookup("transient")["median"] == 10.0
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert len(raw["keys"]["transient"]) == 50
    b = E.DurationStats(path)                                       # a new process
    assert b.lookup("transient")["median"] == 10.0


def test_bad_values_and_an_unwritable_or_corrupt_file_never_raise(tmp_path):
    path = tmp_path / "stats.json"
    path.write_text("not json", encoding="utf-8")
    s = E.DurationStats(path)
    for v in (0, -3, None, "x", float("inf")):
        s.record("transient", v)
    s.record("", 10)
    assert s.lookup("transient") is None
    s.record("transient", 12.0)
    assert s.lookup("transient")["n"] == 1
    blocked = tmp_path / "file"
    blocked.write_text("x", encoding="utf-8")
    E.DurationStats(blocked / "sub" / "s.json").record("transient", 5.0)         # parent is a file: swallowed


def test_a_size_class_is_used_once_it_has_enough_samples(st):
    feed(st, "transient", [100.0] * 6)
    feed(st, "transient", [900.0] * 3, size="s9")                   # these three also count in the plain kind
    assert E.size_class(400) == "s9" and E.size_class(0) is None and E.size_class("x") is None
    assert st.lookup("transient", "s9")["median"] == 900.0 and st.lookup("transient", "s9")["key"] == "transient|s9"
    assert st.lookup("transient", "s3")["key"] == "transient"          # a size without 3 samples falls back to the kind
    assert st.lookup("transient")["n"] == 9


def test_confidence_comes_from_the_sample_count_and_the_spread(st):
    feed(st, "a", [100.0] * 3)
    feed(st, "b", [100.0, 110.0, 105.0, 98.0, 102.0, 99.0])
    feed(st, "c", [100.0] * 25)
    feed(st, "d", [10.0] * 7 + [500.0] * 3)
    assert [st.lookup(k)["confidence"] for k in "abcd"] == ["low", "medium", "high", "low"]


# ---------------------------------------------------------------------------
# a running run
# ---------------------------------------------------------------------------

def test_a_running_run_with_meaningful_progress_uses_its_own_pace(st):
    rem, conf, basis = E.running_remaining("transient", 600.0, 0.25, 96, st)         # no history needed
    assert (rem, conf, basis) == (1800.0, "medium", "progress")
    assert E.running_remaining("transient", 600.0, 0.6, 96, st)[1] == "high"


def test_progress_before_the_mesh_is_built_is_not_progress(st):
    feed(st, "transient", [1000.0] * 6)
    assert E.running_remaining("transient", 10.0, 0.5, 96, st)[2] == "history"        # too early to trust the bar
    assert E.running_remaining("transient", 300.0, 0.01, 96, st)[2] == "history"      # 1 % is the mesh, not the solve
    assert E.running_remaining("transient", 300.0, None, 0, st)[0] == 700.0


def test_without_progress_the_remaining_is_median_minus_elapsed(st):
    feed(st, "coupled.run", [1000.0] * 6)
    assert E.running_remaining("coupled.run", 400.0, None, 0, st)[0] == 600.0
    assert E.running_remaining("coupled.run", 995.0, None, 0, st)[0] == 100.0         # never below 10 % of the median
    rem, conf, _ = E.running_remaining("coupled.run", 5000.0, None, 0, st)            # far past the p80: overdue
    assert rem == 250.0 and conf == "low"


def test_no_history_and_no_progress_is_unknown(st):
    assert E.running_remaining("coupled.run", 400.0, None, 0, st) == (None, "low", "no_history")


# ---------------------------------------------------------------------------
# the replayed scheduler
# ---------------------------------------------------------------------------

def run(rid, state, ws="a", prio=2, seq=0, kind="coupled.run", started=0.0, frac=None, total=0):
    return {"run_id": rid, "state": state, "priority": prio, "ws": ws, "seq": seq, "kind": kind,
            "started_at": started, "frac": frac, "total": total}


def env(workers=1, per_user=0, served=None, field_limit=1):
    return {"workers": workers, "field_limit": field_limit, "per_user": per_user, "field_priority": 1, "served": served or {}}


def test_a_queued_run_starts_when_the_running_one_is_expected_to_end(st):
    feed(st, "coupled.run", [1000.0] * 6)
    runs = [run("r", "running", started=NOW - 400), run("q", "queued", seq=1)]
    out = E.simulate(runs, env(), NOW, st)
    assert out["r"]["expected_finish"] == NOW + 600
    q = out["q"]
    assert (q["position"], q["ahead"]) == (1, 0)
    assert q["expected_start"] == NOW + 600 and q["expected_finish"] == NOW + 1600 and q["basis"] == "queue"


def test_workers_share_the_runs_ahead(st):
    feed(st, "coupled.run", [1000.0] * 6)
    runs = [run("r1", "running", started=NOW - 100), run("r2", "running", started=NOW - 700),
            run("q1", "queued", seq=1), run("q2", "queued", seq=2), run("q3", "queued", seq=3)]
    out = E.simulate(runs, env(workers=2), NOW, st)
    # r2 ends first (300 s), then r1 (900 s), then q1 (started at 300, ends 1300)
    assert out["q1"]["expected_start"] == NOW + 300
    assert out["q2"]["expected_start"] == NOW + 900
    assert out["q3"]["expected_start"] == NOW + 1300
    assert [out[k]["ahead"] for k in ("q1", "q2", "q3")] == [0, 1, 2]


def test_a_free_slot_means_the_run_starts_now(st):
    feed(st, "coupled.run", [1000.0] * 6)
    out = E.simulate([run("q", "queued", seq=1)], env(workers=2), NOW, st)
    assert out["q"]["expected_start"] == NOW and out["q"]["ahead"] == 0


def test_fairness_round_robin_among_accounts_not_arrival_order(st):
    feed(st, "coupled.run", [600.0] * 6)
    # account x queued three runs before account y queued one; one worker, both have started nothing
    runs = [run("busy", "running", ws="z", started=NOW - 100),
            run("x1", "queued", ws="x", seq=1), run("x2", "queued", ws="x", seq=2), run("x3", "queued", ws="x", seq=3),
            run("y1", "queued", ws="y", seq=4)]
    out = E.simulate(runs, env(), NOW, st)
    order = sorted(("x1", "x2", "x3", "y1"), key=lambda k: out[k]["position"])
    assert order == ["x1", "y1", "x2", "x3"]                       # y1 jumps x2 and x3, exactly as the scheduler would
    assert out["y1"]["ahead"] == 1
    assert out["y1"]["expected_start"] == NOW + 500 + 600          # busy ends in 500 s, x1 runs 600 s, then y1


def test_the_accounts_that_already_started_more_wait_behind(st):
    feed(st, "coupled.run", [600.0] * 6)
    runs = [run("busy", "running", ws="z", started=NOW - 100),
            run("x1", "queued", ws="x", seq=1), run("y1", "queued", ws="y", seq=2)]
    out = E.simulate(runs, env(served={"x": 5, "y": 0}), NOW, st)
    assert out["y1"]["position"] == 1 and out["x1"]["position"] == 2


def test_priority_beats_arrival_and_fairness(st):
    feed(st, "coupled.run", [600.0] * 6)
    feed(st, "transient", [60.0] * 6)
    runs = [run("busy", "running", started=NOW - 100),
            run("camp", "queued", prio=3, seq=1, kind="optimizer.scan"),
            run("duty", "queued", prio=2, seq=2),
            run("live", "queued", prio=0, seq=3, kind="transient")]
    out = E.simulate(runs, env(), NOW, st)
    assert [k for k in sorted(("camp", "duty", "live"), key=lambda k: out[k]["position"])] == ["live", "duty", "camp"]


def test_the_per_account_cap_holds_a_second_run_of_the_same_account_back(st):
    feed(st, "coupled.run", [600.0] * 6)
    # two workers, but each account may run one at a time: x's second run waits for x's first even with a free worker
    runs = [run("xr", "running", ws="x", started=NOW - 100), run("x2", "queued", ws="x", seq=1),
            run("y1", "queued", ws="y", seq=2)]
    out = E.simulate(runs, env(workers=2, per_user=1), NOW, st)
    assert out["y1"]["expected_start"] == NOW and out["y1"]["position"] == 1
    assert out["x2"]["expected_start"] == NOW + 500 and out["x2"]["position"] == 2


def test_field_views_have_their_own_slots(st):
    feed(st, "coupled.run", [600.0] * 6)
    feed(st, "field.view", [5.0] * 6)
    runs = [run("busy", "running", started=NOW - 100), run("f", "queued", prio=1, seq=1, kind="field.view")]
    out = E.simulate(runs, env(workers=1, field_limit=1), NOW, st)
    assert out["f"]["expected_start"] == NOW                        # a worker slot being busy does not stop a field view


def test_an_unknown_run_ahead_makes_the_estimate_unknown_but_not_the_position(st):
    feed(st, "transient", [100.0] * 6)
    runs = [run("busy", "running", kind="mystery.kind", started=NOW - 100), run("q", "queued", seq=1, kind="transient")]
    out = E.simulate(runs, env(), NOW, st)
    q = out["q"]
    assert q["position"] == 1 and q["ahead"] == 0
    assert q["expected_start"] is None and q["expected_finish"] is None and q["basis"] == "no_history"
    eta = E.public_eta(q, NOW)
    assert eta == {"expected_start": None, "expected_finish": None, "confidence": "low", "basis": "no_history"}


def test_a_queued_run_of_an_unseen_kind_has_a_start_but_no_finish(st):
    feed(st, "coupled.run", [1000.0] * 6)
    runs = [run("r", "running", started=NOW - 400), run("q", "queued", seq=1, kind="brand.new")]
    q = E.simulate(runs, env(), NOW, st)["q"]
    assert q["expected_start"] == NOW + 600 and q["expected_finish"] is None and q["basis"] == "no_history"


def test_a_run_ahead_with_progress_needs_no_history(st):
    feed(st, "coupled.run", [1000.0] * 6)
    runs = [run("r", "running", kind="unseen", started=NOW - 600, frac=0.5), run("q", "queued", seq=1)]
    q = E.simulate(runs, env(), NOW, st)["q"]
    assert q["expected_start"] == NOW + 600 and q["expected_finish"] == NOW + 1600


def test_queued_estimates_are_never_more_than_medium_confidence(st):
    feed(st, "coupled.run", [100.0] * 30)
    runs = [run("r", "running", started=NOW - 10), run("q", "queued", seq=1)]
    out = E.simulate(runs, env(), NOW, st)
    assert out["r"]["confidence"] == "high" and out["q"]["confidence"] == "medium"


def test_a_cap_that_lets_nothing_start_still_numbers_the_queue(st):
    out = E.simulate([run("a", "queued", seq=2), run("b", "queued", seq=1)], env(workers=1, per_user=0), NOW, st)
    assert out["b"]["position"] == 1 or out["a"]["position"] == 1          # free worker: both get numbers, no exception
    assert {out["a"]["position"], out["b"]["position"]} == {1, 2}


# ---------------------------------------------------------------------------
# honest display
# ---------------------------------------------------------------------------

def test_times_are_rounded_to_five_minutes_under_an_hour_and_fifteen_beyond():
    base = NOW - (NOW % 900)                                        # a quarter-hour boundary
    assert E.round_epoch(base + 11 * 60, base) == base + 10 * 60             # 11:00 -> :10
    assert E.round_epoch(base + 12 * 60 + 40, base) == base + 15 * 60
    assert E.round_epoch(base + 13 * 60, base) == base + 15 * 60
    assert E.round_epoch(base + 3600 + 6 * 60, base) == base + 3600 + 0     # beyond an hour: quarters
    assert E.round_epoch(base + 3600 + 8 * 60, base) == base + 3600 + 15 * 60
    assert E.round_epoch(base - 400, base) >= base - (base % 300)          # never into the past
    assert E.round_epoch(None, base) is None


def test_public_eta_is_rounded_iso_and_says_why_when_it_has_no_number():
    eta = E.public_eta({"expected_start": NOW + 100, "expected_finish": NOW + 4000, "confidence": "medium", "basis": "queue"}, NOW)
    assert eta["confidence"] == "medium" and eta["basis"] == "queue"
    assert eta["expected_finish"].endswith("+00:00") and eta["expected_start"].endswith("+00:00")
    from datetime import datetime
    f = datetime.fromisoformat(eta["expected_finish"]).timestamp()
    assert f % 900 == 0
    none = E.public_eta({"expected_start": NOW, "expected_finish": None, "confidence": "low", "basis": "no_history"}, NOW)
    assert none["expected_finish"] is None and none["basis"] == "no_history"


# ---------------------------------------------------------------------------
# the real queue: learns durations, replays its own state
# ---------------------------------------------------------------------------

@pytest.fixture()
def real_queue(monkeypatch, tmp_path):
    monkeypatch.setenv(WS.ENV_WORKSPACES_ROOT, str(tmp_path / "workspaces"))
    monkeypatch.delenv(J.ENV_ASYNC, raising=False)
    monkeypatch.delenv(J.ENV_PER_USER, raising=False)
    monkeypatch.setenv(E.ENV_STATS, str(tmp_path / "job_stats.json"))
    E.reset_stats(E.DurationStats(tmp_path / "job_stats.json"))
    J.reset_queue(J.InProcessQueue(workers=1, field_limit=1, persist=False))
    P.reset_registry()
    yield tmp_path
    J.reset_queue(J.InProcessQueue(workers=2, field_limit=2))
    P.reset_registry()
    E.reset_stats(E.DurationStats())


def _as(email):
    ws = WS.workspace_for_identity(email)
    return WS.use_workspace(ws), WS.use_caller({"id": email, "is_admin": False, "role": ""})


def test_a_finished_run_teaches_the_statistics_and_a_failed_one_does_not(real_queue):
    use_ws, use_who = _as("a@example.test")
    with use_ws, use_who:
        J.run_job("transient", lambda: __import__("time").sleep(0.05), target={"die": "D", "config": "C"})
        with pytest.raises(RuntimeError):
            J.run_job("coupled.run", lambda: (_ for _ in ()).throw(RuntimeError("boom")), target={"die": "D", "config": "C"})
    assert E.stats().lookup("transient")["n"] == 1
    assert E.stats().lookup("coupled.run") is None


def test_live_targets_carry_position_ahead_and_eta_and_no_identity(real_queue):
    gate, started = threading.Event(), threading.Event()
    E.stats().record("coupled.run", 1000.0)

    def first():
        use_ws, use_who = _as("a@example.test")
        with use_ws, use_who:
            def work():
                started.set()
                gate.wait(5.0)
            J.run_job("coupled.run", work, target={"die": "DieA", "config": "L1", "duty": "rated"})

    def second():
        use_ws, use_who = _as("b@example.test")
        with use_ws, use_who:
            J.run_job("coupled.run", lambda: None, target={"die": "DieB", "config": "L2"})

    t1 = threading.Thread(target=first, daemon=True)
    t1.start()
    assert started.wait(5.0)
    t2 = threading.Thread(target=second, daemon=True)
    t2.start()
    import time
    rows = J.live_targets()
    deadline = time.time() + 5
    while len(rows) < 2 and time.time() < deadline:
        time.sleep(0.02)
        rows = J.live_targets()
    load = J.server_load()
    gate.set()
    t1.join(5.0)
    t2.join(5.0)
    by = {r["state"]: r for r in rows}
    assert by["running"]["position"] == 0 and by["running"]["eta"]["expected_finish"]
    q = by["queued"]
    assert (q["position"], q["ahead"]) == (1, 0)
    assert q["eta"]["expected_start"] and q["eta"]["expected_finish"] and q["eta"]["basis"] == "queue"
    assert load == {"running": 1, "queued": 1, "workers": 1}
    assert "example.test" not in json.dumps(rows) and "run-" not in json.dumps(rows)


def test_recent_failures_are_listed_without_the_error(real_queue):
    use_ws, use_who = _as("a@example.test")
    with use_ws, use_who:
        with pytest.raises(RuntimeError):
            J.run_job("mechanical.modes", lambda: (_ for _ in ()).throw(RuntimeError("secret detail")),
                      target={"die": "D", "config": "C", "duty": "rated"})
    rows = J.recent_failures()
    assert [(r["kind"], r["die"], r["config"], r["duty"]) for r in rows] == [("mechanical.modes", "D", "C", "rated")]
    assert "secret" not in json.dumps(rows) and "example.test" not in json.dumps(rows)
    assert J.recent_failures(window_s=-10) == []
