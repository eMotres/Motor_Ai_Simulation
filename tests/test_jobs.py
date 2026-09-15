"""The job queue — priority, fairness, ownership, persistence (migration Stage 4).

Every claim here is about SCHEDULING, so every "solve" is a sleep.  That is not
a shortcut: what is under test is who gets a core and in what order, and a real
transient would only make the same assertions take four minutes each and add
FEM flakiness to a queue bug report.  The one test that does measure wall clock
(``test_four_concurrent_are_within_one_and_a_half_times_solo``) is the gate the
migration plan names — 4 concurrent solves each within ~1.5x of solo — and it
measures the queue's own overhead with the physics mocked out, which is the only
part of that number this stage can be held responsible for.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from motor_ai_sim import jobs as J
from motor_ai_sim import progress as P
from motor_ai_sim import workspace as WS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fresh_queue(monkeypatch, tmp_path):
    """A queue of our own, and a workspace tree of our own.

    ``WORKSPACES_ROOT`` is set for the whole module because the per-user
    fairness rule is deliberately OFF without it (see ``jobs._per_user_limit``):
    a rule among users is no rule when there is one user, and this module is
    entirely about several.
    """
    monkeypatch.setenv(WS.ENV_WORKSPACES_ROOT, str(tmp_path / "workspaces"))
    monkeypatch.delenv(J.ENV_ASYNC, raising=False)
    monkeypatch.delenv(J.ENV_PER_USER, raising=False)
    J.reset_queue(J.InProcessQueue(workers=2, field_limit=2))
    P.reset_registry()
    yield
    J.reset_queue(J.InProcessQueue(workers=2, field_limit=2))
    P.reset_registry()


def _ws(email: str):
    return WS.workspace_for_identity(email)


class Recorder:
    """Who ran, when, and in what order — the only instrument these need."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.started: list = []
        self.finished: list = []

    def work(self, tag: str, seconds: float = 0.05, gate=None):
        def _run():
            with self.lock:
                self.started.append(tag)
            if gate is not None:
                gate.wait(5.0)
            else:
                time.sleep(seconds)
            with self.lock:
                self.finished.append(tag)
            return {"tag": tag}
        return _run


def _submit(email: str, kind: str, priority: J.Priority, work,
            run_id: str = "", block: bool = True):
    """Submit AS ``email`` — workspace and caller identity both, as a request."""
    ws = _ws(email)
    with WS.use_workspace(ws), WS.use_caller({"id": email, "is_admin": False}):
        rec = J.make_record(kind, priority=priority, run_id=run_id)
        return J.queue().submit(rec, work, block=block), rec


def _thread(fn, *a, **kw) -> threading.Thread:
    t = threading.Thread(target=fn, args=a, kwargs=kw, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# 1. Priority
# ---------------------------------------------------------------------------

def test_priority_beats_arrival_order():
    """A campaign queued FIRST must not overtake a transient queued second.

    The whole reason the queue is not ``_FifoGate``: pure arrival order gives
    the machine to whoever pressed the button first, and the person watching a
    progress bar loses to a sweep that will still be running at lunch.
    """
    q = J.reset_queue(J.InProcessQueue(workers=1, field_limit=1))
    rec = Recorder()
    hold = threading.Event()

    # One job in flight, so everything else has to queue behind it.
    blocker = _thread(lambda: _submit("a@x.com", "blocker",
                                      J.Priority.INTERACTIVE,
                                      rec.work("blocker", gate=hold)))
    while not rec.started:
        time.sleep(0.01)

    ts = []
    for tag, prio in (("campaign", J.Priority.CAMPAIGN),
                      ("duty", J.Priority.DUTY),
                      ("transient", J.Priority.INTERACTIVE)):
        ts.append(_thread(lambda t=tag, p=prio:
                          _submit("a@x.com", t, p, rec.work(t, 0.01))))
        time.sleep(0.05)            # a clear arrival order: campaign first

    assert [w.record.kind for w in sorted(q._waiting, key=q._order_key)] == \
        ["transient", "duty", "campaign"], "the queue is not ordered by priority"

    hold.set()
    blocker.join(5)
    for t in ts:
        t.join(5)
    assert rec.finished[0] == "blocker"
    assert rec.finished[1:] == ["transient", "duty", "campaign"]


def test_field_views_keep_their_own_limit():
    """``SB_FIELD_MAX_CONCURRENT`` survives as a PRIORITY CLASS.

    Field views have had their own machine-wide cap since the 2026-09-03
    incident (``field_jobs``), and it is not the worker count: a field solve
    fans out internally, and two of them is the measured right number whatever
    else the server is doing.  So a field view is admitted against its own
    limit and against nothing else — which also means a transient holding the
    only worker slot does not stop one.
    """
    J.reset_queue(J.InProcessQueue(workers=1, field_limit=2))
    rec = Recorder()
    hold = threading.Event()
    ts = [_thread(lambda: _submit("a@x.com", "transient", J.Priority.INTERACTIVE,
                                  rec.work("transient", gate=hold)))]
    while "transient" not in rec.started:
        time.sleep(0.01)
    for i in (1, 2):
        ts.append(_thread(lambda i=i: _submit("a@x.com", "field",
                                              J.Priority.FIELD,
                                              rec.work("field%d" % i, gate=hold))))
    deadline = time.time() + 5
    while len(rec.started) < 3 and time.time() < deadline:
        time.sleep(0.01)
    assert sorted(rec.started) == ["field1", "field2", "transient"]
    hold.set()
    for t in ts:
        t.join(5)


# ---------------------------------------------------------------------------
# 2. Per-user fairness
# ---------------------------------------------------------------------------

def test_three_users_two_workers_the_third_queues():
    """The migration plan's own acceptance test, minus the physics.

    Three accounts start a transient at once with N=2.  Two run, the third
    waits — and the one that waits is the third ARRIVAL, not the account whose
    request happened to hit a lock first.
    """
    rec = Recorder()
    hold = threading.Event()
    ts = []
    for who in ("a@x.com", "b@x.com", "c@x.com"):
        ts.append(_thread(lambda w=who: _submit(w, "transient",
                                                J.Priority.INTERACTIVE,
                                                rec.work(w, gate=hold))))
        time.sleep(0.08)

    time.sleep(0.2)
    assert sorted(rec.started) == ["a@x.com", "b@x.com"], rec.started
    snap = J.queue().snapshot()
    assert snap["running"] == 2 and snap["queued"] == 1
    assert [i["position"] for i in snap["items"] if i["state"] == "queued"] == [1]

    hold.set()
    for t in ts:
        t.join(5)
    assert sorted(rec.finished) == ["a@x.com", "b@x.com", "c@x.com"]


def test_one_running_job_per_user():
    """One account may hold ONE worker, however many jobs it submits.

    ``QUEUE_WORKERS=2`` and one user with three jobs: one runs.  Without this
    the first account to open four tabs owns the server.
    """
    rec = Recorder()
    hold = threading.Event()
    ts = [_thread(lambda i=i: _submit("a@x.com", "duty", J.Priority.DUTY,
                                      rec.work("a%d" % i, gate=hold)))
          for i in range(3)]
    time.sleep(0.3)
    assert len(rec.started) == 1, rec.started
    hold.set()
    for t in ts:
        t.join(5)
    assert len(rec.finished) == 3


def test_round_robin_among_waiting_users():
    """Two accounts, one slot: they alternate rather than A taking the lot.

    A submits five jobs before B submits one.  Pure FIFO would run all five of
    A's first; the round-robin term (fewest jobs STARTED so far) puts B's single
    job ahead of A's second.
    """
    J.reset_queue(J.InProcessQueue(workers=1, field_limit=1))
    rec = Recorder()
    hold = threading.Event()
    ts = [_thread(lambda: _submit("a@x.com", "duty", J.Priority.DUTY,
                                  rec.work("a0", gate=hold)))]
    while not rec.started:
        time.sleep(0.01)
    for i in range(1, 5):
        ts.append(_thread(lambda i=i: _submit("a@x.com", "duty", J.Priority.DUTY,
                                              rec.work("a%d" % i, 0.01))))
        time.sleep(0.03)
    ts.append(_thread(lambda: _submit("b@x.com", "duty", J.Priority.DUTY,
                                      rec.work("b0", 0.01))))
    time.sleep(0.1)
    hold.set()
    for t in ts:
        t.join(5)
    assert rec.finished[0] == "a0"
    assert rec.finished[1] == "b0", (
        "B's only job queued behind four of A's: the round-robin term is not "
        "in the ordering (%r)" % (rec.finished,))


def test_each_user_sees_only_their_own_jobs():
    rec = Recorder()
    for who in ("a@x.com", "b@x.com"):
        _submit(who, "duty", J.Priority.DUTY, rec.work(who, 0.0))
    for who, other in (("a@x.com", "b@x.com"), ("b@x.com", "a@x.com")):
        with WS.use_workspace(_ws(who)), WS.use_caller({"id": who}):
            mine = J.queue().list_for_owner(who)
        assert [r.owner for r in mine] == [who]
        assert other not in [r.owner for r in mine]


# ---------------------------------------------------------------------------
# 3. Cancellation and ownership
# ---------------------------------------------------------------------------

def test_a_cancel_does_not_stop_the_other_users_run():
    """A's Stop must not touch B.  The one-slot registries could not say this.

    Before Stage 4 the cancel registry was ``{"id": <the last id cancelled>}``
    and every march compared ITS id against that one slot — so the check was
    "was the most recent cancel about me", and a second account's Stop cleared
    the answer for the first.
    """
    rec = Recorder()
    hold = threading.Event()
    stopped = {}

    def work(tag, run_id):
        def _run():
            with rec.lock:
                rec.started.append(tag)
            for _ in range(200):
                if J.is_cancelled(run_id):
                    stopped[tag] = True
                    return {"cancelled": True}
                time.sleep(0.01)
            with rec.lock:
                rec.finished.append(tag)
            return {"tag": tag}
        return _run

    ts = []
    for who, rid in (("a@x.com", "run-a"), ("b@x.com", "run-b")):
        ts.append(_thread(lambda w=who, r=rid:
                          _submit(w, "transient", J.Priority.INTERACTIVE,
                                  work(w, r), run_id=r)))
    deadline = time.time() + 5
    while len(rec.started) < 2 and time.time() < deadline:
        time.sleep(0.01)

    with WS.use_workspace(_ws("a@x.com")), WS.use_caller({"id": "a@x.com"}):
        J.cancel_run("run-a")
    for t in ts:
        t.join(6)

    assert stopped.get("a@x.com") is True
    assert "b@x.com" in rec.finished and "b@x.com" not in stopped
    hold.set()


def test_cancelling_someone_elses_run_is_refused():
    _submit("a@x.com", "duty", J.Priority.DUTY, lambda: None, run_id="run-a")
    with WS.use_workspace(_ws("b@x.com")), WS.use_caller({"id": "b@x.com",
                                                          "is_admin": False}):
        with pytest.raises(J.NotOwner):
            J.cancel_run("run-a")
    # ...and the owner may.
    with WS.use_workspace(_ws("a@x.com")), WS.use_caller({"id": "a@x.com"}):
        assert J.cancel_run("run-a")["cancelled"] is True


def test_an_admin_may_cancel_anyones_run():
    _submit("a@x.com", "duty", J.Priority.DUTY, lambda: None, run_id="run-a")
    with WS.use_workspace(_ws("b@x.com")), WS.use_caller({"id": "b@x.com",
                                                          "is_admin": True}):
        assert J.cancel_run("run-a")["cancelled"] is True


def test_a_queued_job_cancelled_before_it_starts_never_runs():
    rec = Recorder()
    hold = threading.Event()
    blocker = _thread(lambda: _submit("a@x.com", "duty", J.Priority.DUTY,
                                      rec.work("blocker", gate=hold)))
    while not rec.started:
        time.sleep(0.01)

    out = {}

    def _late():
        try:
            _submit("a@x.com", "duty", J.Priority.DUTY, rec.work("late", 0.01),
                    run_id="run-late")
        except J.JobCancelled:
            out["cancelled"] = True

    t = _thread(_late)
    time.sleep(0.15)
    with WS.use_workspace(_ws("a@x.com")), WS.use_caller({"id": "a@x.com"}):
        J.cancel_run("run-late")
    t.join(5)
    hold.set()
    blocker.join(5)
    assert out.get("cancelled") is True
    assert "late" not in rec.started


# ---------------------------------------------------------------------------
# 4. Re-entrancy — the deadlock this queue would otherwise have
# ---------------------------------------------------------------------------

def test_a_nested_job_runs_inline_and_does_not_queue():
    """A coupled run calls the transient twelve times.  With one job per user,
    an inner call that queued would wait for a slot its own caller is holding —
    a deadlock the first time anybody ran a duty cycle."""
    J.reset_queue(J.InProcessQueue(workers=1, field_limit=1))
    seen = []

    def inner():
        seen.append(("inner", J.current_run_id()))
        return 42

    def outer():
        seen.append(("outer", J.current_run_id()))
        return J.run_job("transient", inner, priority=J.Priority.INTERACTIVE)

    ws = _ws("a@x.com")
    with WS.use_workspace(ws), WS.use_caller({"id": "a@x.com"}):
        assert J.run_job("coupled.run", outer, priority=J.Priority.DUTY) == 42
    # The inner call kept the OUTER run's id: one bar, one Stop button.
    assert seen[0][1] == seen[1][1] and seen[0][1]


# ---------------------------------------------------------------------------
# 5. Persistence
# ---------------------------------------------------------------------------

def test_records_survive_a_restart_and_a_running_one_reads_as_interrupted():
    """``<ws>/.jobs.json`` is the answer to "what was it doing when it died".

    A record that was RUNNING comes back as ``interrupted``: nobody knows how it
    ended, and the one answer that is certainly wrong is "still running", which
    is what a restored ``running`` flag would say for ever.
    """
    ws = _ws("a@x.com")
    with WS.use_workspace(ws), WS.use_caller({"id": "a@x.com"}):
        _submit("a@x.com", "duty", J.Priority.DUTY, lambda: None, run_id="done-1")
        path = J.store_path()
        assert path.is_file(), "no .jobs.json was written"
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert [r["run_id"] for r in raw["jobs"]] == ["done-1"]
        assert raw["jobs"][0]["state"] == J.JobState.DONE

        # Forge a record that was running when the process stopped.
        raw["jobs"].append({**raw["jobs"][0], "run_id": "run-2",
                            "state": J.JobState.RUNNING})
        path.write_text(json.dumps({"jobs": raw["jobs"]}), encoding="utf-8")

        # THE RESTART: a brand-new queue with no memory at all.
        J.reset_queue(J.InProcessQueue(workers=2))
        back = {r.run_id: r for r in J.queue().list_for_owner("a@x.com")}
        assert set(back) == {"done-1", "run-2"}
        assert back["done-1"].state == J.JobState.DONE
        assert back["run-2"].state == J.JobState.INTERRUPTED


def test_one_workspaces_records_never_appear_in_anothers_file():
    for who in ("a@x.com", "b@x.com"):
        _submit(who, "duty", J.Priority.DUTY, lambda: None, run_id="r-" + who[0])
    for who, mine in (("a@x.com", "r-a"), ("b@x.com", "r-b")):
        with WS.use_workspace(_ws(who)):
            rows = json.loads(J.store_path().read_text(encoding="utf-8"))["jobs"]
        assert [r["run_id"] for r in rows] == [mine]


# ---------------------------------------------------------------------------
# 6. The 202 mode
# ---------------------------------------------------------------------------

def test_async_mode_returns_a_position_and_a_worker_runs_the_job(monkeypatch):
    """``QUEUE_ASYNC=1``: the call does not block, the answer names the run.

    This is the mode a Redis-backed deployment needs — there is no thread on the
    API host to block — and the reason every admitted route is submitted with
    its body as a callable rather than wrapped in a context manager.
    """
    monkeypatch.setenv(J.ENV_ASYNC, "1")
    rec = Recorder()
    ws = _ws("a@x.com")
    with WS.use_workspace(ws), WS.use_caller({"id": "a@x.com"}):
        with pytest.raises(J.JobAccepted) as got:
            J.run_job("duty", rec.work("async", 0.05), priority=J.Priority.DUTY)
    payload = got.value.payload()
    assert payload["queued"] is True and payload["position"] >= 1
    assert payload["poll"].endswith(payload["run_id"])

    deadline = time.time() + 5
    while "async" not in rec.finished and time.time() < deadline:
        time.sleep(0.01)
    assert rec.finished == ["async"]
    assert J.queue().status(payload["run_id"]).state == J.JobState.DONE


# ---------------------------------------------------------------------------
# 7. Wall clock — the migration plan's gate
# ---------------------------------------------------------------------------

def test_four_concurrent_are_within_one_or_a_half_times_solo():
    """4 concurrent jobs, 4 slots, each within 1.5x of the same job run alone.

    With the physics mocked out this measures exactly one thing — what the QUEUE
    costs — and that is the only part of the plan's "within ~1.5x" the queue can
    be held to.  The rest of that number is the machine's, and belongs to the
    soak test on the real server.
    """
    J.reset_queue(J.InProcessQueue(workers=4, field_limit=4))
    rec = Recorder()
    SOLO = 0.25

    t0 = time.perf_counter()
    _submit("solo@x.com", "duty", J.Priority.DUTY, rec.work("solo", SOLO))
    solo = time.perf_counter() - t0

    took = {}

    def _one(who):
        t = time.perf_counter()
        _submit(who, "duty", J.Priority.DUTY, rec.work(who, SOLO))
        took[who] = time.perf_counter() - t

    ts = [_thread(_one, "u%d@x.com" % i) for i in range(4)]
    for t in ts:
        t.join(20)

    assert len(took) == 4, took
    worst = max(took.values())
    assert worst < solo * 1.5, (
        "four concurrent jobs cost %.3f s each against %.3f s solo (%.2fx) — "
        "the queue is serialising work it has slots for" % (worst, solo,
                                                            worst / solo))


# ---------------------------------------------------------------------------
# 8. The Redis seam
# ---------------------------------------------------------------------------

def test_the_redis_queue_is_a_declared_seam_and_nothing_more():
    """Every method raises, and the contract is in the docstring.

    A half-working Redis queue that silently degraded to in-process would be a
    two-user server pretending to be a cluster; an unimplemented one is a
    promise that the in-process queue's API cannot quietly stop being portable.
    """
    q = J.RedisQueue(url="redis://nowhere")
    assert isinstance(q, J.JobQueue)
    rec = J.JobRecord(run_id="r", ws_id="w", owner="o", kind="duty")
    for call in (lambda: q.submit(rec, lambda: None),
                 lambda: q.status("r"),
                 lambda: q.cancel("r", requester="o"),
                 lambda: q.list_for_owner("o"),
                 lambda: q.worker_loop()):
        with pytest.raises(NotImplementedError):
            call()
    for word in ("workspace", "HANDLERS", "DISK", "cancel"):
        assert word in J.RedisQueue.__doc__


def test_a_job_record_is_json_all_the_way_down():
    """The record IS the wire format: a worker on another host has nothing else.

    If anything in it stopped being JSON — a Path, a Popen, a numpy array —
    ``RedisQueue`` could not be written at all, and the seam would be a comment.
    """
    rec = J.make_record("transient", priority=J.Priority.INTERACTIVE,
                        body={"current_a": 120.0, "rpm": 3000})
    round_tripped = json.loads(json.dumps(rec.to_json()))
    assert set(round_tripped) >= {"run_id", "ws_id", "owner", "kind",
                                  "priority", "body", "state"}
    assert J.JobRecord(**round_tripped).run_id == rec.run_id


def test_the_handler_table_is_how_a_remote_worker_finds_the_work():
    J.register_handler("unit.test", lambda body: {"echo": body})
    assert J.HANDLERS["unit.test"]({"a": 1}) == {"echo": {"a": 1}}
    J.HANDLERS.pop("unit.test", None)


# ---------------------------------------------------------------------------
# 9. The single-user promise
# ---------------------------------------------------------------------------

def test_fairness_is_off_when_multi_user_is_off(monkeypatch):
    """With ``WORKSPACES_ROOT`` unset the one-job-per-user rule does not apply.

    It is a rule AMONG users; on this workstation it would only make the owner's
    own second solve wait for their first, which is a behaviour change nobody
    asked for.  ``QUEUE_WORKERS`` alone bounds concurrency there.
    """
    monkeypatch.delenv(WS.ENV_WORKSPACES_ROOT, raising=False)
    assert J._per_user_limit() == 0
    monkeypatch.setenv(WS.ENV_WORKSPACES_ROOT, "/tmp/ws")
    assert J._per_user_limit() == 1
    monkeypatch.setenv(J.ENV_PER_USER, "3")
    assert J._per_user_limit() == 3


def test_the_worker_count_follows_the_cores(monkeypatch):
    monkeypatch.delenv(J.ENV_WORKERS, raising=False)
    monkeypatch.setattr("os.cpu_count", lambda: 16)
    assert J.default_workers() == 4                  # the AX102's number
    monkeypatch.setattr("os.cpu_count", lambda: 2)
    assert J.default_workers() == 1                  # never zero
    monkeypatch.setenv(J.ENV_WORKERS, "7")
    assert J.default_workers() == 7
