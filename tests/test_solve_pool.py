"""The process-level solve scheduler (motor_ai_sim.solve_pool).

Two layers:

* the scheduler rules on :class:`SolvePool` alone (no processes): dispatch,
  the load rule (N x 1 when busy, 1 x k when alone, even split between),
  fixed-width commands counted by their width, priority, per-owner fairness,
  the RAM cap, cancel while waiting;
* real worker processes driven through the diagnostic targets in
  ``solve_pool_child`` (no FEM import): the context a child sees, progress
  replayed on the caller's thread, re-threading when the load changes, cancel
  killing the whole tree, a crashed child failing its job with a clear error,
  command mode, and the per-child CPU attribution in ``job_usage``.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import pytest

from motor_ai_sim import job_usage as U
from motor_ai_sim import jobs as J
from motor_ai_sim import solve_pool as SP
from motor_ai_sim import workspace as WS

GB = 2 ** 30
CHILD = "motor_ai_sim.solve_pool_child:"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setenv("CLUSTER_MONITOR_DIR", str(tmp_path / "cluster"))
    monkeypatch.setattr(U, "_ensure_sampler", lambda: None)
    for k in (SP.ENV_ENABLE, SP.ENV_PROCS, SP.ENV_SOLO, SP.ENV_RSS,
              SP.ENV_RESERVE, SP.ENV_CHILD):
        monkeypatch.delenv(k, raising=False)
    U.reset()
    J.reset_queue(J.InProcessQueue(workers=4, field_limit=2, persist=False))
    yield
    U.reset()
    J.reset_queue(J.InProcessQueue(workers=4, field_limit=2, persist=False))
    SP.reset_pool()


def _pool(procs=6, solo=None, mem=64 * GB, rss_mb=1024, reserve_mb=0,
          floor_mb=0, **kw):
    return SP.SolvePool(procs=procs, solo=solo, rss_mb=rss_mb,
                        reserve_mb=reserve_mb, floor_mb=floor_mb,
                        mem_available=(mem if callable(mem) else (lambda: mem)),
                        child_rss=lambda t: 0, **kw)


def _t(owner="a", prio=int(J.Priority.DUTY), pinned=None, retunable=True, tag=""):
    return SP.Ticket(owner=owner, ws_id=owner, priority=prio, pinned=pinned,
                     retunable=retunable, tag=tag)


def _start(p, t):
    p.submit(t)
    assert p.try_start(t), "expected %r to start" % t.owner
    return t


def _ack(p, *ts):
    """The children confirm the width they were last told."""
    for t in ts:
        p.ack_threads(t, t.threads)


# ─────────────────────────────────────────────────────────────────────────────
#  Scheduler rules (no processes)
# ─────────────────────────────────────────────────────────────────────────────

def test_alone_gets_the_solo_width():
    p = _pool(procs=6, solo=6)
    a = _start(p, _t())
    assert a.threads == 6
    p.release(a)
    q = _pool(procs=6, solo=3)
    b = _start(q, _t())
    assert b.threads == 3


def test_default_solo_width_is_capped(monkeypatch):
    assert _pool(procs=8).solo == SP.DEFAULT_SOLO_MAX == 4   # wider measured slower
    assert _pool(procs=2).solo == 2
    monkeypatch.setenv(SP.ENV_SOLO, "6")
    assert _pool(procs=8).solo == 6


def test_busy_pool_runs_n_jobs_times_one_thread():
    p = _pool(procs=2)
    a = _start(p, _t("a"))
    assert a.threads == 2                       # alone: 1 x k
    b = _t("b")
    p.submit(b)
    assert a.threads == 1 and a.pending_threads == 1   # a told to narrow
    assert not p.try_start(b)                   # ...but still holds 2 cores
    _ack(p, a)                                  # a confirms 1 thread
    assert p.try_start(b) and b.threads == 1    # busy: N x 1
    c = _t("c")
    p.submit(c)
    assert not p.try_start(c)                   # both slots taken
    p.release(a)
    assert p.try_start(c) and c.threads == 1
    p.release(b)
    # c is alone now and nobody waits: it widens back (reserved at once)
    assert c.threads == 2 and c.pending_threads == 2
    assert p.snapshot()["threads_in_use"] == 2


def test_narrowing_reserves_the_old_width_until_acknowledged():
    p = _pool(procs=6, solo=4)
    a = _start(p, _t("a"))
    assert a.threads == 4 and a.acked_threads == 4
    b, c, d = _t("b"), _t("c"), _t("d")
    for t in (b, c, d):
        p.submit(t)
    assert a.threads == 1 and a.acked_threads == 4
    # 4 cores still held by a: only two more fit on 6
    assert p.try_start(b) and p.try_start(c) and not p.try_start(d)
    assert p.snapshot()["threads_in_use"] == 6
    _ack(p, a)
    assert p.try_start(d)
    assert p.overdue_rethreads(0.0) == []


def test_unacknowledged_rethread_is_reported_overdue():
    now = [1000.0]
    p = SP.SolvePool(procs=2, solo=2, rss_mb=1024, reserve_mb=0, floor_mb=0,
                     mem_available=lambda: 64 * GB, child_rss=lambda t: 0,
                     clock=lambda: now[0])
    a = _start(p, _t("a"))
    p.submit(_t("b"))
    assert p.overdue_rethreads(10.0) == []
    now[0] += 11.0
    assert p.overdue_rethreads(10.0) == [a]
    _ack(p, a)
    assert p.overdue_rethreads(10.0) == []


def test_even_split_between_alone_and_full():
    p = _pool(procs=6, solo=6)
    a = _start(p, _t("a"))
    assert a.threads == 6
    b = _t("b")
    p.submit(b)
    _ack(p, a)
    assert p.try_start(b)
    assert (a.threads, b.threads) == (3, 3)
    c = _t("c")
    p.submit(c)
    _ack(p, a, b)
    assert p.try_start(c)
    assert (a.threads, b.threads, c.threads) == (2, 2, 2)
    for o in "defg":
        p.submit(_t(o))
    # four more waiting (three can start): the queue is behind -> 1 each
    assert (a.threads, b.threads, c.threads) == (1, 1, 1)


def test_waiting_queue_counts_as_load_at_dispatch():
    p = _pool(procs=2)
    a = _start(p, _t("a"))
    b, c = _t("b"), _t("c")
    p.submit(b)
    p.submit(c)
    assert a.threads == 1                       # 3 active on 2 slots
    _ack(p, a)
    assert p.try_start(b) and b.threads == 1


def test_fixed_width_command_is_counted_by_its_width():
    p = _pool(procs=4)
    a = _start(p, _t("a", pinned=4, retunable=False))
    assert a.threads == 4                       # pinned 4, pool idle
    b = _t("b")
    p.submit(b)
    assert not p.try_start(b)                   # a really holds 4 cores
    assert a.threads == 4 and a.pending_threads is None   # never re-threaded
    p.release(a)
    assert p.try_start(b)


def test_pinned_single_thread_stays_single():
    p = _pool(procs=6)
    a = _start(p, _t("a", pinned=1, retunable=False))
    assert a.threads == 1
    # a pinned width above the share is cut to the share
    b = _start(p, _t("b", pinned=6, retunable=False))
    assert b.threads == 3                       # 6 // 2 active


def test_priority_first():
    p = _pool(procs=1)
    a = _start(p, _t("a"))
    camp = _t("x", prio=int(J.Priority.CAMPAIGN))
    inter = _t("y", prio=int(J.Priority.INTERACTIVE))
    p.submit(camp)
    p.submit(inter)
    p.release(a)
    assert not p.try_start(camp)
    assert p.try_start(inter)


def test_round_robin_among_owners():
    p = _pool(procs=1)
    x1 = _start(p, _t("x"))
    x2, x3, y1 = _t("x"), _t("x"), _t("y")
    for t in (x2, x3, y1):                      # x queued first
        p.submit(t)
    p.release(x1)
    assert not p.try_start(x2)
    assert p.try_start(y1)                      # y has been served less
    p.release(y1)
    assert p.try_start(x2)


def test_owner_with_fewer_running_goes_first():
    p = _pool(procs=3, solo=1)
    _start(p, _t("x"))
    _start(p, _t("x"))
    blocker = _start(p, _t("z"))
    x3, y1 = _t("x"), _t("y")
    p.submit(x3)
    p.submit(y1)
    p.release(blocker)
    assert p.try_start(y1) and not p.try_start(x3)


def test_ram_cap_limits_concurrency():
    p = _pool(procs=8, solo=1, mem=3 * GB + 1, rss_mb=1024, reserve_mb=0)
    started = []
    for o in "abcde":
        t = _t(o)
        p.submit(t)
        if p.try_start(t):
            started.append(t)
    assert len(started) == 3                    # 3 GB free / 1 GB per solve
    snap = p.snapshot()
    assert snap["running"] == 3 and snap["waiting"] == 2


def test_estimate_does_not_block_the_first_solve_above_the_floor():
    p = _pool(procs=4, mem=100 * 2 ** 20, rss_mb=1024, floor_mb=0)
    a = _start(p, _t("a"))
    assert a.threads == 4


def test_hard_ram_floor_blocks_even_the_first_solve(monkeypatch):
    free = [300 * 2 ** 20]
    p = _pool(procs=4, mem=lambda: free[0], rss_mb=1024, floor_mb=512)
    a = _t("a")
    p.submit(a)
    assert not p.try_start(a)                   # 300 MB < 512 MB floor
    free[0] = 600 * 2 ** 20
    assert p.try_start(a)
    # the default floor comes from the environment
    monkeypatch.setenv(SP.ENV_RAM_FLOOR, "2048")
    q = SP.SolvePool(procs=2, mem_available=lambda: GB, child_rss=lambda t: 0)
    b = _t("b")
    q.submit(b)
    assert q.floor() == 2 * GB and not q.try_start(b)


def test_strict_ram_runs_one_at_a_time_when_memory_is_unknown():
    p = _pool(procs=4, solo=1, mem=lambda: None, strict_ram=True)
    a, b = _t("a"), _t("b")
    for t in (a, b):
        p.submit(t)
    assert p.try_start(a) and not p.try_start(b)
    q = _pool(procs=4, solo=1, mem=lambda: None, strict_ram=False)
    c, d = _t("c"), _t("d")
    for t in (c, d):
        q.submit(t)
    assert q.try_start(c) and q.try_start(d)


def test_memory_events_are_read(tmp_path):
    (tmp_path / "memory.events").write_text("low 0\nhigh 3\nmax 7\noom 1\noom_kill 2\n")
    ev = SP._cgroup_memory_events(str(tmp_path))
    assert ev["oom_kill"] == 2 and ev["max"] == 7
    assert SP._cgroup_memory_events(str(tmp_path / "absent")) == {}


def test_aging_promotes_a_starved_campaign():
    now = [10000.0]
    p = _pool(procs=1, solo=1, aging_s=100.0, clock=lambda: now[0])
    blocker = _start(p, _t("z", prio=int(J.Priority.INTERACTIVE)))
    camp = _t("x", prio=int(J.Priority.CAMPAIGN))
    p.submit(camp)                              # queued at t=0
    now[0] += 350.0                             # waited 3.5 aging periods
    inter = _t("y", prio=int(J.Priority.INTERACTIVE))
    p.submit(inter)                             # a fresh interactive run
    assert p.effective_priority(camp) == 0
    p.release(blocker)
    # both at class 0 now; the campaign arrived first
    assert not p.try_start(inter) and p.try_start(camp)


def test_fairness_counter_is_a_rolling_window():
    now = [10000.0]
    p = _pool(procs=1, solo=1, fair_window_s=600.0, clock=lambda: now[0])
    for _ in range(5):                          # x was served a lot, long ago
        t = _start(p, _t("x"))
        p.release(t)
    y1 = _start(p, _t("y"))                     # y once, recently
    now[0] += 1000.0                            # x's history left the window
    x6, y2 = _t("x"), _t("y")
    p.submit(x6)                                # x arrives first
    p.submit(y2)
    p.release(y1)
    # A lifetime counter (x 5, y 1) would pick y; the rolling window has
    # forgotten both, so arrival order decides.
    assert not p.try_start(y2) and p.try_start(x6)
    # inside the window the counter still counts
    q = _pool(procs=1, solo=1, fair_window_s=600.0, clock=lambda: now[0])
    for _ in range(3):
        t = _start(q, _t("x"))
        q.release(t)
    blocker = _start(q, _t("z"))
    x, y = _t("x"), _t("y")
    q.submit(x)
    q.submit(y)
    q.release(blocker)
    assert q.try_start(y) and not q.try_start(x)


def test_rss_estimate_learns_from_finished_children():
    p = SP.SolvePool(procs=2, mem_available=lambda: 64 * GB)
    assert p.rss_estimate() == SP.DEFAULT_RSS_MB * 2 ** 20
    for rss in (300, 400, 500):
        t = _start(p, _t())
        t.peak_rss = rss * 2 ** 20
        p.release(t)
    assert p.rss_estimate() == int(500 * 2 ** 20 * 1.15)


def test_acquire_cancel_withdraws_the_ticket():
    p = _pool(procs=1)
    a = _start(p, _t("a"))
    stop = threading.Event()
    err = []

    def _check():
        if stop.is_set():
            raise J.JobCancelled("r")

    def _run():
        try:
            p.acquire(_t("b"), cancel_check=_check, poll=0.02)
        except J.JobCancelled as exc:
            err.append(exc)
    th = threading.Thread(target=_run)
    th.start()
    time.sleep(0.1)
    stop.set()
    th.join(2)
    assert err and p.snapshot()["waiting"] == 0
    p.release(a)


def test_cancel_tag_hits_waiting_and_running():
    p = _pool(procs=1)
    a = _start(p, _t("a", tag="ws:scan"))
    b = _t("b", tag="ws:scan")
    p.submit(b)
    running = p.cancel_tag("ws:scan")
    assert running == [a] and a.cancel_requested and b.cancel_requested


def test_enabled_flag_and_child_guard(monkeypatch):
    assert not SP.enabled()
    monkeypatch.setenv(SP.ENV_ENABLE, "1")
    assert SP.enabled()
    monkeypatch.setenv(SP.ENV_CHILD, "1")
    assert not SP.enabled()


def test_default_procs_and_queue_workers(monkeypatch):
    monkeypatch.setenv(SP.ENV_PROCS, "5")
    assert SP.default_procs() == 5
    monkeypatch.delenv(SP.ENV_PROCS)
    assert SP.default_procs() == max(1, SP.physical_cores() - 2)
    monkeypatch.delenv(J.ENV_WORKERS, raising=False)
    off = J.default_workers()
    monkeypatch.setenv(SP.ENV_ENABLE, "1")
    monkeypatch.setenv(SP.ENV_PROCS, "7")
    assert J.default_workers() == max(off, 7)
    monkeypatch.setenv(J.ENV_WORKERS, "2")      # explicit wins
    assert J.default_workers() == 2


def test_describe_exit_words():
    assert "137" in SP.describe_exit(137) and "memory" in SP.describe_exit(137)
    assert "access violation" in SP.describe_exit(0xC0000005)
    if os.name != "nt":
        assert "SIGKILL" in SP.describe_exit(-9)
        assert "SIGSEGV" in SP.describe_exit(-11)


# ─────────────────────────────────────────────────────────────────────────────
#  Real worker processes
# ─────────────────────────────────────────────────────────────────────────────

ID = ("a@x.com", "wsA", "", int(J.Priority.DUTY), "web")


def test_call_carries_context_and_lowers_priority(tmp_path):
    p = _pool(procs=2)
    ws = WS.workspace_for_identity("ctx@x.com")
    from motor_ai_sim import material_context as MC
    with WS.use_workspace(ws), WS.use_caller({"id": "ctx@x.com", "role": "user"}):
        tok = MC._OVERRIDE.set({"assignment": {"rotor": "M1"}, "materials": {}})
        try:
            out = SP.call(CHILD + "diag_echo", {"k": 1}, the_pool=p, identity=ID)
        finally:
            MC._OVERRIDE.reset(tok)
    assert out["payload"] == {"k": 1}
    assert out["child_flag"] == "1"
    assert out["mkl_threads"] == "2" and out["omp_threads"] == "2"   # alone
    assert out["ws_id"] == ws.id
    assert out["config_path"] == str(ws.config_file)
    assert out["caller"]["id"] == "ctx@x.com"
    assert out["materials"] == {"assignment": {"rotor": "M1"}, "materials": {}}
    assert out["pid"] != os.getpid()
    if os.name == "nt":
        import psutil
        assert out["nice"] == psutil.BELOW_NORMAL_PRIORITY_CLASS
    else:
        assert out["nice"] >= 10
    assert p.snapshot()["running"] == 0


def test_progress_is_replayed_on_the_calling_thread():
    p = _pool(procs=2)
    seen = []
    me = threading.get_ident()

    def cb(done, total, phase=None):
        seen.append((done, total, phase, threading.get_ident() == me))
    out = SP.call(CHILD + "diag_progress", {"n": 4, "dt": 0.01},
                  progress_cb=cb, the_pool=p, identity=ID)
    assert out["n"] == 4
    assert [s[:3] for s in seen] == [(i, 4, "diag") for i in range(1, 5)]
    assert all(s[3] for s in seen)


def test_running_child_is_rethreaded_when_load_changes():
    p = _pool(procs=2)
    res = {}

    def long():
        res["a"] = SP.call(CHILD + "diag_sleep", {"s": 9.0, "progress": True},
                           progress_cb=lambda *a: None, the_pool=p,
                           identity=("a", "a", "", 2, "web"))
    th = threading.Thread(target=long)
    th.start()
    deadline = time.time() + 20
    while p.snapshot()["running"] < 1 and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(1.5)                             # child is up and polling
    SP.call(CHILD + "diag_sleep", {"s": 1.0}, the_pool=p,
            identity=("b", "b", "", 2, "web"))
    th.join(30)
    hist = res["a"]["threads"]
    assert hist[0] == 2                         # alone at dispatch
    assert 1 in hist                            # narrowed while b ran
    assert hist[-1] == 2                        # widened after b left


def _alive(pid: int) -> bool:
    import psutil
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def test_job_cancel_kills_the_child_tree_promptly(tmp_path):
    p = SP.reset_pool(_pool(procs=2))
    pid_file = tmp_path / "pids.txt"
    err = []

    def work():
        return SP.call(CHILD + "diag_sleep", {"s": 120, "pid_file": str(pid_file)},
                       the_pool=p)

    def run():
        try:
            J.queue().submit(J.make_record("em", run_id="run-cancel-1"), work,
                             block=True)
        except BaseException as exc:            # noqa: BLE001
            err.append(exc)
    th = threading.Thread(target=run)
    th.start()
    deadline = time.time() + 30
    while not (pid_file.exists() and pid_file.read_text().strip()) \
            and time.time() < deadline:
        time.sleep(0.05)
    child_pid, grand_pid = (int(x) for x in pid_file.read_text().split())
    assert _alive(child_pid) and _alive(grand_pid)
    t0 = time.time()
    J.cancel_run("run-cancel-1", requester="", is_admin=True)
    th.join(10)
    assert time.time() - t0 < 3.0
    assert err and isinstance(err[0], J.JobCancelled)
    deadline = time.time() + 5
    while (_alive(child_pid) or _alive(grand_pid)) and time.time() < deadline:
        time.sleep(0.05)
    assert not _alive(child_pid) and not _alive(grand_pid)
    assert J.queue().status("run-cancel-1").state == J.JobState.CANCELLED
    assert p.snapshot()["running"] == 0


def test_crashed_child_fails_the_job_with_a_clear_error():
    p = SP.reset_pool(_pool(procs=2))
    rec = J.make_record("em", run_id="run-crash-1")
    with pytest.raises(SP.SolveWorkerDied) as ei:
        J.queue().submit(rec, lambda: SP.call(CHILD + "diag_crash", {"code": 137},
                                              the_pool=p), block=True)
    msg = str(ei.value)
    assert "died before returning a result" in msg and "137" in msg
    assert "diag_crash: exiting hard" in msg          # stderr tail
    st = J.queue().status("run-crash-1")
    assert st.state == J.JobState.FAILED and "SolveWorkerDied" in st.error
    assert p.snapshot()["running"] == 0
    # the pool is not stuck: the next solve runs
    assert SP.call(CHILD + "diag_echo", 7, the_pool=p, identity=ID)["payload"] == 7


def test_remote_exception_is_reraised_as_itself():
    p = _pool(procs=1)
    with pytest.raises(ValueError, match="bad point"):
        SP.call(CHILD + "diag_raise", {"msg": "bad point"}, the_pool=p, identity=ID)
    assert p.snapshot()["running"] == 0


def test_unpicklable_payload_is_not_poolable():
    p = _pool(procs=1)
    with pytest.raises(SP.NotPoolable):
        SP.call(CHILD + "diag_echo", {"f": lambda: 1}, the_pool=p, identity=ID)
    assert p.snapshot()["waiting"] == 0


def test_run_command_pins_one_thread_and_lowers_priority():
    p = _pool(procs=4)
    code = ("import os, sys\n"
            "print(os.environ['MKL_NUM_THREADS'], os.environ['SOLVE_POOL_CHILD'])\n"
            "print(sys.stdin.read().strip())\n")
    r = SP.run_command([sys.executable, "-c", code], input_text="spec-in",
                       the_pool=p, identity=ID)
    assert r.returncode == 0
    assert r.stdout.split() == ["1", "1", "spec-in"]
    r2 = SP.run_command([sys.executable, "-c", code], input_text="x",
                        threads=3, the_pool=p, identity=ID)
    assert r2.stdout.split()[0] == "3" and r2.threads == 3   # asked 3, pool idle


def test_run_command_timeout_kills_and_raises():
    p = _pool(procs=1)
    t0 = time.time()
    with pytest.raises(subprocess.TimeoutExpired):
        SP.run_command([sys.executable, "-c", "import time; time.sleep(60)"],
                       timeout=1.0, the_pool=p, identity=ID)
    assert time.time() - t0 < 10
    assert p.snapshot()["running"] == 0


def test_run_command_cancelled_while_waiting_never_starts():
    p = _pool(procs=1)
    blocker = _start(p, _t("z"))
    out = {}

    def run():
        out["r"] = SP.run_command([sys.executable, "-c", "print('ran')"],
                                  tag="wsA:scan", the_pool=p, identity=ID)
    th = threading.Thread(target=run)
    th.start()
    time.sleep(0.3)
    p.cancel_tag("wsA:scan")
    th.join(5)
    r = out["r"]
    assert r.cancelled and r.cancelled_while_waiting
    assert r.returncode is None and "ran" not in r.stdout
    p.release(blocker)


def _grand_code(pid_file):
    return ("import subprocess, sys, time\n"
            "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            "open(%r, 'w').write('%%d %%d' %% (__import__('os').getpid(), g.pid))\n"
            "time.sleep(float(sys.argv[1]))\n" % str(pid_file))


def _wait_dead(*pids, timeout=5.0):
    deadline = time.time() + timeout
    while any(_alive(x) for x in pids) and time.time() < deadline:
        time.sleep(0.05)
    return not any(_alive(x) for x in pids)


def test_command_exiting_normally_leaves_no_grandchild(tmp_path):
    """The review's race: the worker exits (or is killed from outside) before
    the owner saw a cancel.  Whatever way the leader ends, the pool reaps it
    and kills what is left of its tree (Job Object / process group)."""
    p = _pool(procs=1)
    pid_file = tmp_path / "g.txt"
    r = SP.run_command([sys.executable, "-c", _grand_code(pid_file), "0"],
                       the_pool=p, identity=ID)
    assert r.returncode == 0 and not r.cancelled
    child_pid, grand_pid = (int(x) for x in pid_file.read_text().split())
    assert _wait_dead(child_pid, grand_pid)
    assert p.snapshot()["running"] == 0


def test_cancel_tag_kills_a_running_command_tree(tmp_path):
    p = _pool(procs=1)
    pid_file = tmp_path / "g2.txt"
    out = {}

    def run():
        out["r"] = SP.run_command([sys.executable, "-c", _grand_code(pid_file), "120"],
                                  tag="wsA:scan", the_pool=p, identity=ID)
    th = threading.Thread(target=run)
    th.start()
    deadline = time.time() + 30
    while not (pid_file.exists() and pid_file.read_text().strip()) \
            and time.time() < deadline:
        time.sleep(0.05)
    child_pid, grand_pid = (int(x) for x in pid_file.read_text().split())
    t0 = time.time()
    assert len(p.cancel_tag("wsA:scan")) == 1
    th.join(10)
    assert time.time() - t0 < 3.0
    assert out["r"].cancelled and not out["r"].cancelled_while_waiting
    assert _wait_dead(child_pid, grand_pid)


def test_call_normal_exit_leaves_no_grandchild(tmp_path):
    p = _pool(procs=1)
    pid_file = tmp_path / "g3.txt"
    SP.call(CHILD + "diag_spawn_and_exit", {"pid_file": str(pid_file)},
            the_pool=p, identity=ID)
    child_pid, grand_pid = (int(x) for x in pid_file.read_text().split())
    assert _wait_dead(child_pid, grand_pid)


def test_width_change_is_forced_without_a_solver_callback(monkeypatch):
    """A long native call makes no progress callback: the child applies the
    narrowing from its control thread after SOLVE_POOL_RETHREAD_S and
    confirms it; the old width stays reserved until then."""
    monkeypatch.setenv(SP.ENV_RETHREAD, "1.0")
    p = _pool(procs=2)
    res = {}

    def long():
        res["a"] = SP.call(CHILD + "diag_busy_native", {"s": 6.0}, the_pool=p,
                           identity=("a", "a", "", 2, "web"))
    th = threading.Thread(target=long)
    th.start()
    deadline = time.time() + 20
    while p.snapshot()["running"] < 1 and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(1.0)
    b = _t("b")
    p.submit(b)                                 # a is told to narrow to 1
    assert not p.try_start(b)                   # a still holds 2
    deadline = time.time() + 15
    while not p.try_start(b) and time.time() < deadline:
        time.sleep(0.05)
    assert b.state == SP.RUNNING                # a confirmed 1 thread
    p.release(b)
    th.join(30)
    assert 1 in res["a"]["threads"] and res["a"]["forced"] >= 1


# ─────────────────────────────────────────────────────────────────────────────
#  CPU accounting per user and per process
# ─────────────────────────────────────────────────────────────────────────────

def _fake_tree(monkeypatch, values):
    it = iter(values)
    monkeypatch.setattr(U, "tree_cpu_s", lambda fast=False: next(it))
    monkeypatch.setattr(U, "tree_rss", lambda: 0)
    monkeypatch.setattr(U, "_thread_cpu", lambda nid: 0.0)


def test_child_cpu_goes_to_the_job_it_solves_for(monkeypatch):
    ra = J.JobRecord(run_id="ra", ws_id="A", owner="alice", kind="em")
    rb = J.JobRecord(run_id="rb", ws_id="B", owner="bob", kind="em")
    _fake_tree(monkeypatch, [0.0, 0.0, 0.0, 10.0])
    ma = U.start(ra, sampler=False)
    mb = U.start(rb, sampler=False)
    U._tick()                                   # baseline
    U.child_started(4242, "ra", "alice", "web", sampler=False)
    U.child_cpu(4242, 9.0)                      # 9 of the 10 s were alice's child
    U._tick()
    assert ma.cpu_s == pytest.approx(10.0)      # all weight on alice
    assert mb.cpu_s == pytest.approx(0.0)


def test_child_of_an_unmetered_job_is_credited_to_its_owner(monkeypatch):
    _fake_tree(monkeypatch, [0.0, 6.0, 9.0])
    rb = J.JobRecord(run_id="rb", ws_id="B", owner="bob", kind="em")
    mb = U.start(rb, sampler=False)
    U.child_started(777, "campaign-1", "carol", "web", sampler=False)
    U.child_cpu(777, 4.0)
    U.child_finished(777, 4.0)
    U._tick()
    acc = {k[2]: v for k, v in U._USER_ACC.items()}
    assert acc.get("carol") == pytest.approx(4.0)
    assert mb.cpu_s == pytest.approx(2.0)       # the rest of the tree's delta
    assert 777 not in U._CHILDREN               # finished child dropped


def test_child_cpu_sampled_ahead_of_the_tree_is_carried(monkeypatch):
    _fake_tree(monkeypatch, [0.0, 1.0, 5.0])
    U._tick()                                   # baseline (no meters at all)
    U.child_started(778, "campaign-2", "erin", "web", sampler=False)
    U.child_cpu(778, 3.0)                       # pool saw 3 s, tree only 1 s
    U._tick()
    acc = {k[2]: v for k, v in U._USER_ACC.items()}
    assert acc["erin"] == pytest.approx(1.0)
    U._tick()                                   # tree catches up (+4 s)
    acc = {k[2]: v for k, v in U._USER_ACC.items()}
    assert acc["erin"] == pytest.approx(3.0)


def test_metered_child_cpu_sampled_ahead_of_the_tree_is_carried(monkeypatch):
    ra = J.JobRecord(run_id="ra", ws_id="A", owner="alice", kind="em")
    rb = J.JobRecord(run_id="rb", ws_id="B", owner="bob", kind="em")
    # start a, start b (fast ticks), baseline, then the tree reads 1 s and 9 s
    _fake_tree(monkeypatch, [0.0, 0.0, 0.0, 1.0, 9.0])
    ma = U.start(ra, sampler=False)
    mb = U.start(rb, sampler=False)
    U._tick()
    U.child_started(4343, "ra", "alice", "web", sampler=False)
    U.child_cpu(4343, 9.0)                      # pool saw 9 s, the tree 1 s
    U._tick()
    assert ma.cpu_s == pytest.approx(1.0) and mb.cpu_s == pytest.approx(0.0)
    U._tick()                                   # tree catches up (+8 s)
    # The carried 8 s still weight alice's job: nothing goes to bob.
    assert ma.cpu_s == pytest.approx(9.0) and mb.cpu_s == pytest.approx(0.0)


def test_pool_child_reports_cpu_to_usage(monkeypatch):
    got = {}
    monkeypatch.setattr(U, "child_started",
                        lambda pid, rid, user, client="web", sampler=True:
                        got.setdefault("start", (pid, rid, user)))
    monkeypatch.setattr(U, "child_finished",
                        lambda pid, cpu=None: got.setdefault("finish", (pid, cpu)))
    p = _pool(procs=1)
    SP.call(CHILD + "diag_burn", {"s": 0.5}, the_pool=p,
            identity=("dave", "D", "run-d", 2, "web"))
    assert got["start"][1:] == ("run-d", "dave")
    assert got["finish"][0] == got["start"][0]
    assert got["finish"][1] >= 0.4             # the child's own CPU-seconds


# ─────────────────────────────────────────────────────────────────────────────
#  The em_transient_eval drop-in
# ─────────────────────────────────────────────────────────────────────────────

def test_em_transient_eval_is_the_inprocess_function_when_off(monkeypatch):
    from motor_ai_sim.simulation import fem_solver_2d as FS
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return {"T_avg_Nm": 1.0}
    monkeypatch.setattr(FS, "em_transient_eval", fake)
    cb = lambda *a: None                        # noqa: E731
    out = SP.em_transient_eval(gamma_deg=10.0, progress_cb=cb)
    assert out == {"T_avg_Nm": 1.0}
    assert seen == {"gamma_deg": 10.0, "progress_cb": cb}


def _two_users(tmp_path, monkeypatch):
    """Two accounts with DIFFERENT machines (config files and contents)."""
    import yaml
    from motor_ai_sim import config as CFG
    monkeypatch.setenv(WS.ENV_WORKSPACES_ROOT, str(tmp_path / "workspaces"))
    base = yaml.safe_load(open(str(CFG.DEFAULT_CONFIG_PATH), encoding="utf-8"))
    out = []
    for email, rpm in (("alice@x.com", 3000.0), ("bob@x.com", 9000.0)):
        ws = WS.workspace_for_identity(email)
        ws.root.mkdir(parents=True, exist_ok=True)
        cfg = dict(base)
        cfg["simulation"] = dict(cfg.get("simulation") or {}, rpm=rpm)
        ws.config_file.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        out.append(ws)
    return out


def test_worker_fingerprint_matches_its_own_user_only(tmp_path, monkeypatch):
    ws_a, ws_b = _two_users(tmp_path, monkeypatch)
    p = _pool(procs=1)
    with WS.use_workspace(ws_a):
        fp_a = SP.context_fingerprint()
        echo = SP.call(CHILD + "diag_fingerprint", {"nonce": "n1"}, the_pool=p,
                       identity=("alice@x.com", ws_a.id, "", 2, "web"))
    with WS.use_workspace(ws_b):
        fp_b = SP.context_fingerprint()
    assert echo["fingerprint"] == fp_a != fp_b  # the worker saw alice's machine


def test_fem_echo_is_scoped_to_its_request_user_and_machine(tmp_path, monkeypatch):
    from motor_ai_sim.simulation import fem_solver_2d as FS
    ws_a, ws_b = _two_users(tmp_path, monkeypatch)
    key = ("__solve_pool_test__", 1)
    FS._DAXIS_CACHE.pop(key, None)
    with WS.use_workspace(ws_a):
        fp_a = SP.context_fingerprint()
    echo = {"nonce": "n-a", "fingerprint": fp_a,
            "warm_last": {"marker": "alice"}, "daxis": {key: 12.5}}
    try:
        # bob's thread cannot install alice's update, and his seed is untouched
        with WS.use_workspace(ws_b):
            FS._SB_WARM_CACHE["last"] = {"marker": "bob"}
            assert not SP.apply_fem_echo(echo, nonce="n-a", expected=fp_a)
            assert FS._SB_WARM_CACHE.get("last") is None   # dropped: disk wins
        assert key not in FS._DAXIS_CACHE
        with WS.use_workspace(ws_a):
            # a foreign request (wrong nonce) is refused too
            assert not SP.apply_fem_echo(echo, nonce="other", expected=fp_a)
            # the machine changed while it solved: refused
            from motor_ai_sim import config as CFG
            import yaml
            cfg = yaml.safe_load(ws_a.config_file.read_text(encoding="utf-8"))
            cfg["simulation"]["rpm"] = 4000.0
            ws_a.config_file.write_text(yaml.safe_dump(cfg), encoding="utf-8")
            CFG.clear_config_cache()
            assert not SP.apply_fem_echo(echo, nonce="n-a", expected=fp_a)
            # ...and the right request on the right machine is installed
            fp_a2 = SP.context_fingerprint()
            echo2 = dict(echo, fingerprint=fp_a2)
            assert SP.apply_fem_echo(echo2, nonce="n-a", expected=fp_a2)
            assert FS._SB_WARM_CACHE.get("last") == {"marker": "alice"}
        with WS.use_workspace(ws_b):
            assert FS._SB_WARM_CACHE.get("last") is None   # never bob's
        assert FS._DAXIS_CACHE[key] == 12.5
        # an existing calibration is never overwritten by an echo
        with WS.use_workspace(ws_a):
            assert SP.apply_fem_echo(dict(echo2, daxis={key: 99.0}),
                                     nonce="n-a", expected=fp_a2)
        assert FS._DAXIS_CACHE[key] == 12.5
    finally:
        FS._DAXIS_CACHE.pop(key, None)
        for ws in (ws_a, ws_b):
            with WS.use_workspace(ws):
                FS._SB_WARM_CACHE.pop("last", None)


def test_queued_optimizer_eval_cancel_is_cancelled_not_failed(monkeypatch):
    """Stop while an optimizer eval WAITS for a slot: the eval never starts
    and comes back as cancelled — not a failed eval with an exit code, not
    timed, not logged to the surrogate dataset."""
    from motor_ai_sim.routes import optimization as O
    monkeypatch.setenv(SP.ENV_ENABLE, "1")
    p = SP.reset_pool(_pool(procs=1))
    blocker = _start(p, _t("z"))
    logged, timed = [], []
    monkeypatch.setattr(O, "_log_eval", lambda *a, **k: logged.append(a))
    monkeypatch.setattr(O, "_record_eval_seconds", lambda *a, **k: timed.append(a))
    out = {}

    def run():
        out["r"] = O._subprocess_eval({}, 10.0, 12, 80.0, owner="scan")
    th = threading.Thread(target=run)
    th.start()
    deadline = time.time() + 10
    while p.snapshot()["waiting"] < 1 and time.time() < deadline:
        time.sleep(0.05)
    assert p.snapshot()["waiting"] == 1
    O._kill_live_evals("scan")
    th.join(10)
    r = out["r"]
    assert r["ok"] is False and r["cancelled"] is True
    assert "exited with code" not in r["error"]
    assert not logged and not timed
    p.release(blocker)


def test_transient_lock_is_global_off_and_keyed_on(monkeypatch, tmp_path):
    from motor_ai_sim.routes import simulation as S
    monkeypatch.setenv(WS.ENV_WORKSPACES_ROOT, str(tmp_path / "workspaces"))
    assert S._transient_lock_for(("k", 1)) is S._fem_transient_lock
    monkeypatch.setenv(SP.ENV_ENABLE, "1")
    ws_a = WS.workspace_for_identity("la@x.com")
    ws_b = WS.workspace_for_identity("lb@x.com")
    with WS.use_workspace(ws_a):
        a1 = S._transient_lock_for(("k", 1))
        a2 = S._transient_lock_for(("k", 1))
        other_key = S._transient_lock_for(("k", 2))
    with WS.use_workspace(ws_b):
        b1 = S._transient_lock_for(("k", 1))
    assert a1 is a2                             # identical requests still wait
    assert a1 is not other_key and a1 is not b1
    assert a1 is not S._fem_transient_lock
    assert a1.acquire() and b1.acquire()        # different users side by side
    a1.release()
    b1.release()


def test_cgroup_memory_limit_is_honoured(tmp_path):
    (tmp_path / "memory.max").write_text("%d\n" % (10 * GB))
    (tmp_path / "memory.current").write_text("%d\n" % (7 * GB))
    (tmp_path / "memory.stat").write_text("anon 1\ninactive_file %d\n" % GB)
    assert SP._cgroup_mem_available(str(tmp_path)) == 4 * GB
    (tmp_path / "memory.max").write_text("max\n")
    assert SP._cgroup_mem_available(str(tmp_path)) is None
    assert SP._cgroup_mem_available(str(tmp_path / "absent")) is None
