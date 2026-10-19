"""Deploy stage 2: files and memory shared by several processes (file_lock.py, state_epoch.py)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from motor_ai_sim import file_lock as FL
from motor_ai_sim import roles
from motor_ai_sim import state_epoch as SE

ROOT = Path(__file__).resolve().parents[1]


class FakeFcntl:
    """flock() semantics on one machine: a lock belongs to an OPEN FILE DESCRIPTION, so two descriptors of the same file
    conflict even inside one process - which is what makes the real thing work between processes."""
    LOCK_EX, LOCK_NB, LOCK_UN = 2, 4, 8

    def __init__(self):
        self.held = {}                      # inode -> fd
        self.calls = []

    def flock(self, fd, op):
        ino = os.fstat(fd).st_ino
        self.calls.append(op)
        if op & self.LOCK_UN:
            if self.held.get(ino) == fd:
                del self.held[ino]
            return
        if ino in self.held and self.held[ino] != fd:
            raise BlockingIOError("held")
        self.held[ino] = fd


@pytest.fixture
def fake(monkeypatch):
    f = FakeFcntl()
    monkeypatch.setattr(FL, "_fcntl", f)
    return f


# ── file_lock ────────────────────────────────────────────────────────────────

def test_off_in_a_single_process_deployment_no_sidecar_no_flock(tmp_path, monkeypatch, fake):
    monkeypatch.delenv(roles.ENV_ROLE, raising=False)
    store = tmp_path / "users.json"
    with FL.store_lock(store):
        pass
    assert fake.calls == [] and not list(tmp_path.glob(".*.lock"))


def test_on_in_a_multi_process_deployment_with_a_sidecar_file(tmp_path, monkeypatch, fake):
    monkeypatch.setenv(roles.ENV_ROLE, "api")
    store = tmp_path / "users.json"
    lk = FL.store_lock(lambda: store)
    with lk:
        assert (tmp_path / ".users.json.lock").exists()
        with lk:                            # re-entrant like an RLock: one flock, not two
            assert sum(1 for c in fake.calls if c & fake.LOCK_EX) == 1
    assert fake.held == {}, "released on the way out"


def test_a_second_holder_waits_for_the_first(tmp_path, monkeypatch, fake):
    monkeypatch.setenv(roles.ENV_ROLE, "worker")
    store = tmp_path / "s.json"
    a, b = FL.store_lock(store, isolated=True), FL.store_lock(store, isolated=True)    # two processes' views of one file
    order = []

    def second():
        with b:
            order.append("b")
    with a:
        t = threading.Thread(target=second)
        t.start()
        time.sleep(0.15)
        order.append("a")
    t.join(5)
    assert order == ["a", "b"]


def test_a_stuck_holder_never_wedges_a_request(tmp_path, monkeypatch, fake, caplog):
    monkeypatch.setenv(roles.ENV_ROLE, "api")
    monkeypatch.setenv(FL.ENV_TIMEOUT, "0.1")
    store = tmp_path / "s.json"
    a, b = FL.store_lock(store, isolated=True), FL.store_lock(store, isolated=True)
    with a:
        t0 = time.monotonic()
        with b:                              # proceeds without the process lock after the timeout, and says so
            pass
        assert 0.09 <= time.monotonic() - t0 < 3
    assert "continuing WITHOUT it" in caplog.text


def test_two_lock_objects_for_one_path_are_one_lock_in_a_process(tmp_path, monkeypatch, fake, caplog):
    """A module-level lock and a json_store.lock_for one on the same file must not wait for each other (= for themselves)."""
    monkeypatch.setenv(roles.ENV_ROLE, "api")
    monkeypatch.setenv(FL.ENV_TIMEOUT, "5")
    store = tmp_path / "users.json"
    a, b = FL.store_lock(store), FL.store_lock(lambda: store)
    t0 = time.monotonic()
    with a:
        with b:                                # same thread, other object: re-entrant, no second flock, no 5 s wait
            assert sum(1 for c in fake.calls if c & fake.LOCK_EX) == 1
    assert time.monotonic() - t0 < 2
    assert "continuing WITHOUT it" not in caplog.text and fake.held == {}
    # and another THREAD is still excluded by either object
    order = []

    def other():
        with b:
            order.append("b")
    with a:
        t = threading.Thread(target=other)
        t.start()
        time.sleep(0.15)
        order.append("a")
    t.join(5)
    assert order == ["a", "b"]


def test_json_store_and_the_identity_stores_use_the_cross_process_lock(tmp_path):
    from motor_ai_sim import json_store, sessions, users, newsletter, comm_prefs, admin_audit, compute_nodes
    assert isinstance(json_store.lock_for(tmp_path / "a.json"), FL.StoreLock)
    for mod in (sessions, users, newsletter, comm_prefs, admin_audit, compute_nodes):
        assert isinstance(mod._LOCK, FL.StoreLock), mod.__name__
    # json_store keeps working under a multi-process role
    os.environ[roles.ENV_ROLE] = "api"
    try:
        json_store.mutate_json(tmp_path / "a.json", lambda d: d.__setitem__("k", 1), default={})
        assert json_store.read_json(tmp_path / "a.json") == {"k": 1}
    finally:
        os.environ.pop(roles.ENV_ROLE, None)


@pytest.mark.skipif(FL._fcntl is None, reason="needs fcntl (the server is Linux)")
def test_two_real_processes_do_not_lose_each_others_updates(tmp_path):
    """The bug this lock exists for: two read-modify-write cycles interleaving lose one of the two increments."""
    counter = tmp_path / "counter.txt"
    counter.write_text("0")
    code = (
        "import sys, time\n"
        "from motor_ai_sim.file_lock import store_lock\n"
        "p = sys.argv[1]\n"
        "lk = store_lock(p)\n"
        "for _ in range(150):\n"
        "    with lk:\n"
        "        n = int(open(p).read())\n"
        "        time.sleep(0.0005)\n"
        "        open(p, 'w').write(str(n + 1))\n")
    env = dict(os.environ, MOTRES_ROLE="worker", PYTHONPATH=str(ROOT / "src"))
    procs = [subprocess.Popen([sys.executable, "-c", code, str(counter)], env=env) for _ in range(3)]
    assert [p.wait(120) for p in procs] == [0, 0, 0]
    assert int(counter.read_text()) == 450


# ── state_epoch ──────────────────────────────────────────────────────────────

class _State:
    """The part of WorkspaceState the epoch uses.  evict() must NEVER be called by the epoch any more."""
    def __init__(self):
        self.flags, self.evicted = {}, 0

    def flag(self, name, default=None):
        return self.flags.get(name, default)

    def set_flag(self, name, value):
        self.flags[name] = value

    def evict(self):
        self.evicted += 1
        self.flags.clear()


def _ws(tmp_path, wid="w1"):
    return SimpleNamespace(id=wid, root=tmp_path, state=_State())


@pytest.fixture
def drops(monkeypatch):
    """Replace the cache-dropping funnels with a recorder; no job anywhere unless a test says so."""
    rec = []
    monkeypatch.setattr(SE, "_drop_caches", lambda ws: rec.append(ws.id))
    monkeypatch.setattr(SE, "_has_work", lambda ws: False)
    return rec


def test_epoch_is_a_no_op_in_a_single_process_deployment(tmp_path, monkeypatch):
    monkeypatch.delenv(roles.ENV_ROLE, raising=False)
    ws = _ws(tmp_path)
    assert SE.bump(ws) is None and SE.check(ws) is False
    assert not (tmp_path / SE.EPOCH_FILE).exists()


def test_a_change_made_by_another_process_drops_this_ones_cached_machine(tmp_path, monkeypatch, drops):
    monkeypatch.setenv(roles.ENV_ROLE, "worker")
    ws = _ws(tmp_path)
    assert SE.check(ws) is False                           # first look: nothing in memory to drop
    assert drops == []
    # the API process (another pid) applies a preset and bumps the epoch
    (tmp_path / SE.EPOCH_FILE).write_text("4242-aaaa", encoding="utf-8")
    assert SE.check(ws) is True and drops == ["w1"]
    assert ws.state.evicted == 0, "the epoch drops the CACHES through the funnels; WorkspaceState.evict() wipes campaign state"
    assert SE.check(ws) is False, "one change is one drop"


def test_our_own_bump_is_not_news_to_us(tmp_path, monkeypatch, drops):
    monkeypatch.setenv(roles.ENV_ROLE, "api")
    ws = _ws(tmp_path)
    SE.check(ws)
    token = SE.bump(ws, "geometry updated")
    assert token and (tmp_path / SE.EPOCH_FILE).read_text(encoding="utf-8") == token
    assert SE.check(ws) is False and drops == []
    other = _ws(tmp_path)                                    # the other process
    SE.check(other)
    SE.bump(ws, "preset applied")
    assert SE.check(other) is True and drops == ["w1"]


def test_a_workspace_with_a_running_job_is_not_touched_and_is_dropped_later(tmp_path, monkeypatch):
    """The bug: a preset applied through the API wiped the worker's scan / descent state and its ws locks mid-run."""
    monkeypatch.setenv(roles.ENV_ROLE, "worker")
    rec, busy = [], {"on": True}
    monkeypatch.setattr(SE, "_drop_caches", lambda ws: rec.append(ws.id))
    monkeypatch.setattr(SE, "_has_work", lambda ws: busy["on"])
    ws = _ws(tmp_path)
    SE.check(ws)
    (tmp_path / SE.EPOCH_FILE).write_text("4242-aaaa", encoding="utf-8")
    seen_before = ws.state.flag(SE._FLAG)
    assert SE.check(ws) is False and rec == [] and ws.state.evicted == 0
    assert ws.state.flag(SE._FLAG) == seen_before, "seen stays unchanged, so the drop happens once the job is over"
    assert SE.check(ws) is False and rec == []              # still running: still nothing
    busy["on"] = False
    assert SE.check(ws) is True and rec == ["w1"]
    assert ws.state.flag(SE._FLAG) == "4242-aaaa" and SE.check(ws) is False


def test_has_work_looks_at_this_workspaces_queued_and_running_jobs(tmp_path, monkeypatch):
    class Q:
        def __init__(self, items):
            self.items = items

        def snapshot(self):
            return {"items": self.items}
    ws = _ws(tmp_path, "mine")
    from motor_ai_sim import jobs as J
    monkeypatch.setattr(J, "queue", lambda: Q([{"ws_id": "other", "state": "running"}, {"ws_id": "mine", "state": "done"}]))
    assert SE._has_work(ws) is False
    monkeypatch.setattr(J, "queue", lambda: Q([{"ws_id": "mine", "state": "queued"}]))
    assert SE._has_work(ws) is True
    monkeypatch.setattr(J, "queue", lambda: Q([{"ws_id": "mine", "state": "running"}]))
    assert SE._has_work(ws) is True

    def boom():
        raise RuntimeError("boom")
    monkeypatch.setattr(J, "queue", boom)
    assert SE._has_work(ws) is True, "unknown counts as busy: a stale cache is cheap, a wiped campaign is not"


def test_a_running_scan_survives_a_preset_applied_from_another_process(tmp_path, monkeypatch):
    """End to end on a REAL workspace: the campaign slot, the ws locks and the cached machine of a running job."""
    from motor_ai_sim import jobs as J
    from motor_ai_sim import workspace as W
    from motor_ai_sim.routes import simulation as SIM
    from motor_ai_sim.services import geometry_service as GEO
    monkeypatch.setenv(roles.ENV_ROLE, "worker")
    ws = W.Workspace(id="wsepoch1", email="a@x", root=tmp_path / "w", shared_root=tmp_path / "shared")
    ws.root.mkdir()
    scan = {"running": True, "done": 3, "total": 10}
    lock_obj = object()
    ws.state.slots["optimization.scan_state"] = scan                # what a running campaign keeps in the workspace
    ws.state.slots["optimization.scan_lock"] = lock_obj
    with W.use_workspace(ws):
        SIM._fem_field_cache["k"] = {"v": 1}
        assert SE.check(ws) is False                                # first look
    SE._file(ws).write_text("4242-aaaa", encoding="utf-8")          # the API process applies a preset
    running = {"items": [{"ws_id": "wsepoch1", "state": "running"}]}
    monkeypatch.setattr(J, "queue", lambda: SimpleNamespace(snapshot=lambda: running))
    assert SE.check(ws) is False
    assert ws.state.slots["optimization.scan_state"] is scan and scan["running"] is True and scan["done"] == 3
    assert ws.state.slots["optimization.scan_lock"] is lock_obj
    # the job ends: the cached machine goes (through the funnels), the campaign slots are STILL not wiped
    running["items"] = []
    assert SE.check(ws) is True
    with W.use_workspace(ws):
        assert "k" not in SIM._fem_field_cache
        assert GEO._geom_get() is None
    assert ws.state.slots["optimization.scan_state"] is scan and ws.state.slots["optimization.scan_lock"] is lock_obj


def test_two_concurrent_bumps_never_make_a_process_evict_itself(tmp_path, monkeypatch, drops):
    monkeypatch.setenv(roles.ENV_ROLE, "api")
    ws = _ws(tmp_path)
    SE.check(ws)
    start = threading.Barrier(8)
    tokens, errors = [], []

    def bump():
        try:
            start.wait(5)
            for _ in range(25):
                tokens.append(SE.bump(ws, "x"))
                SE.check(ws)                   # a request thread looking in between
        except Exception as exc:               # noqa: BLE001
            errors.append(exc)
    ts = [threading.Thread(target=bump) for _ in range(8)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert not errors and None not in tokens, "every bump got its own temp file and went through"
    assert drops == [], "our own bumps are never news to us"
    assert (tmp_path / SE.EPOCH_FILE).read_text(encoding="utf-8") == ws.state.flag(SE._FLAG)
    assert SE.check(ws) is False
    assert not list(tmp_path.glob("*.tmp")), "no temp file is left behind"


def test_bump_temp_names_are_unique_per_call(tmp_path, monkeypatch):
    monkeypatch.setenv(roles.ENV_ROLE, "api")
    ws = _ws(tmp_path)
    names = []
    real = os.replace

    def spy(src, dst):
        names.append(Path(src).name)
        return real(src, dst)
    monkeypatch.setattr(os, "replace", spy)
    SE.bump(ws)
    SE.bump(ws)
    assert len(names) == 2 and names[0] != names[1]
    assert all(str(os.getpid()) in n and n.endswith(".tmp") for n in names)


def test_clear_simulation_caches_announces_the_change(tmp_path, monkeypatch):
    """The funnel every config write goes through also bumps the epoch (checked without importing the solvers)."""
    src = (ROOT / "src" / "motor_ai_sim" / "routes" / "simulation.py").read_text(encoding="utf-8")
    body = src.split("def clear_simulation_caches", 1)[1].split("\ndef _cache_stats", 1)[0]
    assert "_EPOCH.bump(" in body and "announce: bool = True" in src
    geo = (ROOT / "src" / "motor_ai_sim" / "services" / "geometry_service.py").read_text(encoding="utf-8")
    assert geo.count("_announce_change(") >= 3             # defined once, called by update + reset
    ws = (ROOT / "src" / "motor_ai_sim" / "workspace.py").read_text(encoding="utf-8")
    assert "_EPOCH.check(resolved)" in ws


def test_the_drop_does_not_announce_again(tmp_path, monkeypatch):
    """The reaction to somebody else's bump must not bump (two processes would answer each other for ever)."""
    from motor_ai_sim import workspace as W
    monkeypatch.setenv(roles.ENV_ROLE, "worker")
    ws = W.Workspace(id="wsepoch2", email="a@x", root=tmp_path / "w", shared_root=tmp_path / "shared")
    ws.root.mkdir()
    bumps = []
    monkeypatch.setattr(SE, "bump", lambda *a, **k: bumps.append(1))
    SE._drop_caches(ws)
    assert bumps == []


# ── users: the secret is read once, the temp file is unique ─────────────────

def test_the_signing_secret_file_is_read_once_per_process(tmp_path, monkeypatch):
    from motor_ai_sim import users as U
    f = tmp_path / ".auth_secret"
    monkeypatch.delenv("AUTH_SECRET", raising=False)       # NOT required in the environment
    monkeypatch.setattr(U, "_SECRET_FILE", f)
    monkeypatch.setattr(U, "_SECRET_CACHE", None)
    first = U._secret()                                    # created on first use
    assert f.read_text(encoding="utf-8").strip() == first

    class Boom:
        def __enter__(self):
            raise AssertionError("the users.json lock was taken for an already cached secret")

        def __exit__(self, *a):
            return None
    monkeypatch.setattr(U, "_LOCK", Boom())
    for _ in range(3):
        assert U._secret() == first
    # the environment still wins and is not cached over
    monkeypatch.setenv("AUTH_SECRET", "from-env")
    assert U._secret() == "from-env"


def test_users_save_uses_a_unique_temp_file(tmp_path, monkeypatch):
    from motor_ai_sim import users as U
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    names = []
    real = Path.replace

    def spy(self, target):
        names.append(self.name)
        return real(self, target)
    monkeypatch.setattr(Path, "replace", spy)
    U._save({"a": {"x": 1}})
    U._save({"a": {"x": 2}})
    assert len(names) == 2 and names[0] != names[1] and all(n.startswith("users.json.") and n.endswith(".tmp") for n in names)
    assert not list(tmp_path.glob("*.tmp")) and json.loads((tmp_path / "users.json").read_text(encoding="utf-8")) == {"a": {"x": 2}}


# ── job records: the cutover's recovery rules ───────────────────────────────

def test_recovery_at_worker_start_is_postponed_while_the_cutover_marker_exists(tmp_path, monkeypatch):
    from motor_ai_sim import jobs as J
    ws = tmp_path / "w1"
    ws.mkdir()
    (ws / J.JOBS_FILE).write_text(json.dumps({"jobs": [{"run_id": "a", "state": "running", "queued_at": 100.0}]}), encoding="utf-8")
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path))
    marker = tmp_path / J.RECOVERY_DEFER_FILE
    marker.write_text("")
    assert J.recover_stale_records() == 0, "the old API may still be computing this record"
    assert json.loads((ws / J.JOBS_FILE).read_text(encoding="utf-8"))["jobs"][0]["state"] == "running"
    old = time.time() - J.RECOVERY_DEFER_MAX_S - 60                    # a cutover that died: the marker expires
    os.utime(marker, (old, old))
    assert J.recover_stale_records() == 1


def test_recovery_after_the_switch_touches_only_records_older_than_it(tmp_path):
    from motor_ai_sim import jobs as J
    ws = tmp_path / "w1"
    ws.mkdir()
    (ws / J.JOBS_FILE).write_text(json.dumps({"jobs": [
        {"run_id": "old", "state": "running", "queued_at": 100.0},
        {"run_id": "new", "state": "running", "queued_at": 5000.0}]}), encoding="utf-8")
    assert J.recover_stale_records(tmp_path, before=1000.0) == 1
    rows = {r["run_id"]: r["state"] for r in json.loads((ws / J.JOBS_FILE).read_text(encoding="utf-8"))["jobs"]}
    assert rows == {"old": "interrupted", "new": "running"}, "the worker's own job stays as it is"
