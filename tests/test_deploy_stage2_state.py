"""Deploy stage 2: files and memory shared by several processes (file_lock.py, state_epoch.py)."""
from __future__ import annotations

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
    a, b = FL.store_lock(store), FL.store_lock(store)          # two instances = two processes' view of one file
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
    a, b = FL.store_lock(store), FL.store_lock(store)
    with a:
        t0 = time.monotonic()
        with b:                              # proceeds without the process lock after the timeout, and says so
            pass
        assert 0.09 <= time.monotonic() - t0 < 3
    assert "continuing WITHOUT it" in caplog.text


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
    def __init__(self):
        self.flags, self.evicted = {}, 0

    def flag(self, name, default=None):
        return self.flags.get(name, default)

    def set_flag(self, name, value):
        self.flags[name] = value

    def evict(self):
        self.evicted += 1
        self.flags.clear()


def _ws(tmp_path):
    return SimpleNamespace(id="w1", root=tmp_path, state=_State())


def test_epoch_is_a_no_op_in_a_single_process_deployment(tmp_path, monkeypatch):
    monkeypatch.delenv(roles.ENV_ROLE, raising=False)
    ws = _ws(tmp_path)
    assert SE.bump(ws) is None and SE.check(ws) is False
    assert not (tmp_path / SE.EPOCH_FILE).exists()


def test_a_change_made_by_another_process_evicts_this_ones_memory(tmp_path, monkeypatch):
    monkeypatch.setenv(roles.ENV_ROLE, "worker")
    ws = _ws(tmp_path)
    assert SE.check(ws) is False                           # first look: nothing in memory to drop
    assert ws.state.evicted == 0
    # the API process (another pid) applies a preset and bumps the epoch
    (tmp_path / SE.EPOCH_FILE).write_text("4242-aaaa", encoding="utf-8")
    assert SE.check(ws) is True and ws.state.evicted == 1
    assert SE.check(ws) is False, "one change is one eviction"


def test_our_own_bump_is_not_news_to_us(tmp_path, monkeypatch):
    monkeypatch.setenv(roles.ENV_ROLE, "api")
    ws = _ws(tmp_path)
    SE.check(ws)
    token = SE.bump(ws, "geometry updated")
    assert token and (tmp_path / SE.EPOCH_FILE).read_text(encoding="utf-8") == token
    assert SE.check(ws) is False and ws.state.evicted == 0
    other = _ws(tmp_path)                                    # the other process
    SE.check(other)
    SE.bump(ws, "preset applied")
    assert SE.check(other) is True


def test_clear_simulation_caches_announces_the_change(tmp_path, monkeypatch):
    """The funnel every config write goes through also bumps the epoch (checked without importing the solvers)."""
    src = (ROOT / "src" / "motor_ai_sim" / "routes" / "simulation.py").read_text(encoding="utf-8")
    body = src.split("def clear_simulation_caches", 1)[1].split("\ndef _cache_stats", 1)[0]
    assert "_EPOCH.bump(" in body
    geo = (ROOT / "src" / "motor_ai_sim" / "services" / "geometry_service.py").read_text(encoding="utf-8")
    assert geo.count("_announce_change(") >= 3             # defined once, called by update + reset
    ws = (ROOT / "src" / "motor_ai_sim" / "workspace.py").read_text(encoding="utf-8")
    assert "_EPOCH.check(resolved)" in ws
