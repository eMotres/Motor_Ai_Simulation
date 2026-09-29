"""``run_history`` — the persistent, capped "don't recompute this" store.

Owner, 2026-09-22: *"если я запускаю те же параметры каплинга, он не
считается, а подгружает уже рассчитанный вариант; ... нужна проверка и
хранить небольшую историю, 10 вычислений"*.  This file tests the module in
isolation (no solver, no FastAPI) — route-level "a repeat launch does not
solve" tests live beside each route (``tests/test_mechanical_history.py``).

Every test runs inside its own ``tmp_path`` workspace
(``motor_ai_sim.workspace.use_workspace``), so nothing here ever touches the
real ``config/.run_history``.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from motor_ai_sim import run_history as RH
from motor_ai_sim import workspace as W


@pytest.fixture(autouse=True)
def _fresh_code_version_cache():
    """``code_version()`` caches on first use; each test may want a different
    monkeypatched answer, so drop the cache before and after."""
    RH._reset_code_version_cache_for_tests()
    yield
    RH._reset_code_version_cache_for_tests()


def _ws(tmp_path: Path, ws_id: str = "ws1") -> W.Workspace:
    root = tmp_path / ws_id
    root.mkdir(parents=True, exist_ok=True)
    return W.Workspace(id=ws_id, email=f"{ws_id}@example.com", root=root,
                       shared_root=root)


def test_make_key_stable_under_dict_reordering():
    a = {"rpm": 23000.0, "mesh_mm": 1.5, "assign": {"rotor": "steel", "sleeve": "cf"}}
    b = {"mesh_mm": 1.5, "assign": {"sleeve": "cf", "rotor": "steel"}, "rpm": 23000.0}
    assert RH.make_key("mechanical.rotor_stress", a) == \
        RH.make_key("mechanical.rotor_stress", b)


def test_make_key_changes_with_a_field():
    base = {"rpm": 23000.0, "mesh_mm": 1.5}
    changed = {"rpm": 23001.0, "mesh_mm": 1.5}
    assert RH.make_key("k", base) != RH.make_key("k", changed)


def test_make_key_is_scoped_by_kind():
    p = {"rpm": 23000.0}
    assert RH.make_key("mechanical.rotor_stress", p) != RH.make_key("thermal.steady", p)


def test_put_then_get_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(RH, "_from_deployed_commit_marker", lambda: "v1")
    with W.use_workspace(_ws(tmp_path)):
        h = RH.history_for("mechanical.rotor_stress")
        key = RH.make_key("mechanical.rotor_stress", {"rpm": 23000.0})
        entry = h.put(key, params={"rpm": 23000.0}, summary="23000 rpm, sector",
                      payload={"max_vm_mpa": 123.4})
        assert entry["key"] == key
        assert entry["code_version"] == "v1"

        hit = h.get(key)
        assert hit is not None
        assert hit["payload"] == {"max_vm_mpa": 123.4}
        assert hit["entry"]["summary"] == "23000 rpm, sector"


def test_get_miss_on_unknown_key(tmp_path):
    with W.use_workspace(_ws(tmp_path)):
        h = RH.history_for("mechanical.rotor_stress")
        assert h.get("doesnotexist12345678") is None


def test_code_version_mismatch_is_a_miss(tmp_path, monkeypatch):
    with W.use_workspace(_ws(tmp_path)):
        h = RH.history_for("thermal.steady")
        key = RH.make_key("thermal.steady", {"ambient_c": 25.0})
        monkeypatch.setattr(RH, "_from_deployed_commit_marker", lambda: "old-sha")
        RH._reset_code_version_cache_for_tests()
        h.put(key, params={}, summary="25 C", payload={"t_max_c": 90.0})

        # A newer build: the same key must not be served.
        monkeypatch.setattr(RH, "_from_deployed_commit_marker", lambda: "new-sha")
        RH._reset_code_version_cache_for_tests()
        assert h.get(key) is None
        # ...but it is still LISTED (the owner can see what is stale there).
        rows = h.list()
        assert any(r["key"] == key for r in rows)


def test_lru_cap_evicts_oldest_and_removes_its_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(RH, "_from_deployed_commit_marker", lambda: "v1")
    with W.use_workspace(_ws(tmp_path)):
        h = RH.history_for("mechanical.rotor_stress", cap=10)
        keys = []
        for i in range(12):
            k = RH.make_key("mechanical.rotor_stress", {"rpm": float(i)})
            keys.append(k)
            h.put(k, params={"rpm": float(i)}, summary=str(i), payload=i)
        assert len(h) == 10
        # The two oldest (rpm=0, rpm=1) were evicted...
        assert h.get(keys[0]) is None
        assert h.get(keys[1]) is None
        assert not h._payload_path(keys[0]).is_file()
        # ...the newest ten remain, most-recent last in the raw index but
        # newest-first from list().
        assert h.get(keys[-1]) is not None
        rows = h.list()
        assert [r["key"] for r in rows[:3]] == [keys[11], keys[10], keys[9]]


def test_reassigning_an_existing_key_does_not_grow_or_reorder_wrongly(tmp_path, monkeypatch):
    monkeypatch.setattr(RH, "_from_deployed_commit_marker", lambda: "v1")
    with W.use_workspace(_ws(tmp_path)):
        h = RH.history_for("mechanical.rotor_stress", cap=3)
        k1 = RH.make_key("k", {"a": 1})
        k2 = RH.make_key("k", {"a": 2})
        k3 = RH.make_key("k", {"a": 3})
        h.put(k1, params={}, summary="1", payload=1)
        h.put(k2, params={}, summary="2", payload=2)
        h.put(k3, params={}, summary="3", payload=3)
        # Recompute k1 (fresh=true path): moves to newest, still 3 entries.
        h.put(k1, params={}, summary="1b", payload="1b")
        assert len(h) == 3
        rows = h.list()
        assert rows[0]["key"] == k1
        assert rows[0]["summary"] == "1b"


def test_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(RH, "_from_deployed_commit_marker", lambda: "v1")
    with W.use_workspace(_ws(tmp_path)):
        h = RH.history_for("mechanical.rotor_stress")
        k = RH.make_key("k", {})
        h.put(k, params={}, summary="x", payload=1)
        assert h.delete(k) is True
        assert h.get(k) is None
        assert h.delete(k) is False


def test_corrupt_index_starts_fresh_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(RH, "_from_deployed_commit_marker", lambda: "v1")
    ws = _ws(tmp_path)
    with W.use_workspace(ws):
        h = RH.history_for("mechanical.rotor_stress")
        h.put(RH.make_key("k", {}), params={}, summary="x", payload=1)
        idx = h._index_path()
        idx.write_text("{not json", encoding="utf-8")
        assert h.list() == []
        # And a fresh write still works (index self-heals).
        k2 = RH.make_key("k", {"a": 2})
        h.put(k2, params={}, summary="y", payload=2)
        assert h.get(k2) is not None


def test_workspace_isolation(tmp_path, monkeypatch):
    """Two workspaces never see or evict each other's history — the same
    guarantee ``tests/test_workspace_state.py`` proves for the in-memory
    stores, here for the on-disk one."""
    monkeypatch.setattr(RH, "_from_deployed_commit_marker", lambda: "v1")
    ws_a = _ws(tmp_path, "alice")
    ws_b = _ws(tmp_path, "bob")
    key = RH.make_key("mechanical.rotor_stress", {"rpm": 23000.0})

    with W.use_workspace(ws_a):
        RH.history_for("mechanical.rotor_stress").put(
            key, params={}, summary="alice's rotor", payload="A")

    with W.use_workspace(ws_b):
        # Bob's history is empty even though the key is identical.
        assert RH.history_for("mechanical.rotor_stress").get(key) is None
        RH.history_for("mechanical.rotor_stress").put(
            key, params={}, summary="bob's rotor", payload="B")

    with W.use_workspace(ws_a):
        hit = RH.history_for("mechanical.rotor_stress").get(key)
        assert hit["payload"] == "A"
    with W.use_workspace(ws_b):
        hit = RH.history_for("mechanical.rotor_stress").get(key)
        assert hit["payload"] == "B"

    # And nothing crossed on disk either.
    assert (ws_a.root / RH._DIRNAME / "mechanical.rotor_stress").is_dir()
    assert (ws_b.root / RH._DIRNAME / "mechanical.rotor_stress").is_dir()
    assert ws_a.root != ws_b.root


def test_code_version_fallback_order(monkeypatch, tmp_path):
    # 1) deploy marker wins when present
    monkeypatch.setattr(RH, "_from_deployed_commit_marker", lambda: "deployed-sha")
    monkeypatch.setattr(RH, "_from_git_head", lambda: "git-sha")
    RH._reset_code_version_cache_for_tests()
    assert RH.code_version() == "deployed-sha"

    # 2) no marker -> git HEAD
    monkeypatch.setattr(RH, "_from_deployed_commit_marker", lambda: None)
    RH._reset_code_version_cache_for_tests()
    assert RH.code_version() == "git-sha"

    # 3) neither -> "unknown"
    monkeypatch.setattr(RH, "_from_git_head", lambda: None)
    RH._reset_code_version_cache_for_tests()
    assert RH.code_version() == "unknown"
