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


def _pool(procs=6, solo=None, mem=64 * GB, rss_mb=1024, reserve_mb=0):
    return SP.SolvePool(procs=procs, solo=solo, rss_mb=rss_mb,
                        reserve_mb=reserve_mb, mem_available=lambda: mem,
                        child_rss=lambda t: 0)


def _t(owner="a", prio=int(J.Priority.DUTY), pinned=None, retunable=True, tag=""):
    return SP.Ticket(owner=owner, ws_id=owner, priority=prio, pinned=pinned,
                     retunable=retunable, tag=tag)


def _start(p, t):
    p.submit(t)
    assert p.try_start(t), "expected %r to start" % t.owner
    return t


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
    b = _start(p, _t("b"))
    assert b.threads == 1                       # busy: N x 1
    assert a.threads == 1 and a.pending_threads == 1   # a re-threaded
    c = _t("c")
    p.submit(c)
    assert not p.try_start(c)                   # both slots taken
    p.release(a)
    assert p.try_start(c) and c.threads == 1
    p.release(b)
    # c is alone now and nobody waits: it widens back
    assert c.threads == 2 and c.pending_threads == 2


def test_even_split_between_alone_and_full():
    p = _pool(procs=6, solo=6)
    a = _start(p, _t("a"))
    assert a.threads == 6
    b = _start(p, _t("b"))
    assert (a.threads, b.threads) == (3, 3)
    c = _start(p, _t("c"))
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
    p = _pool(procs=3)
    _start(p, _t("x"))
    _start(p, _t("x"))
    blocker = _start(p, _t("z"))
    x3, y1 = _t("x"), _t("y")
    p.submit(x3)
    p.submit(y1)
    p.release(blocker)
    assert p.try_start(y1) and not p.try_start(x3)


def test_ram_cap_limits_concurrency():
    p = _pool(procs=8, mem=3 * GB + 1, rss_mb=1024, reserve_mb=0)
    started = []
    for o in "abcde":
        t = _t(o)
        p.submit(t)
        if p.try_start(t):
            started.append(t)
    assert len(started) == 3                    # 3 GB free / 1 GB per solve
    snap = p.snapshot()
    assert snap["running"] == 3 and snap["waiting"] == 2


def test_ram_cap_never_blocks_the_first_solve():
    p = _pool(procs=4, mem=100 * 2 ** 20, rss_mb=1024)
    a = _start(p, _t("a"))
    assert a.threads == 4


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
    assert out["r"].returncode == -15 and "ran" not in out["r"].stdout
    p.release(blocker)


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


def test_fem_echo_installs_the_child_caches():
    from motor_ai_sim.simulation import fem_solver_2d as FS
    key = ("__solve_pool_test__", 1)
    ws = WS.workspace_for_identity("echo@x.com")
    with WS.use_workspace(ws):
        SP.apply_fem_echo({"warm_last": {"marker": 1}, "daxis": {key: 12.5}})
        assert FS._SB_WARM_CACHE.get("last") == {"marker": 1}
    assert FS._DAXIS_CACHE.pop(key) == 12.5


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
