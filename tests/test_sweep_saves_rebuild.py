"""Sidecar rebuild of saved sweeps: unique temporary names and a bounded one-time migration."""
from __future__ import annotations

import json
import threading

import pytest

from motor_ai_sim import sweep_saves as ss


def _mk(d, n, with_old_sidecar=True):
    for i in range(n):
        sid = f"s{i:03d}"
        entry = {"id": sid, "name": f"n{i}", "created_at": f"2026-11-01T10:{i % 60:02d}:00",
                 "config": {"steps_per_period": 108},
                 "result": {"steps_per_period": 108, "points": [], "scan_params": {"gap_layers": 1.0}}, "auto": True}
        f = d / f"{sid}.json"
        f.write_text(json.dumps(entry))
        if with_old_sidecar:
            ss._meta_path(f).write_text(json.dumps({"id": sid, "auto": True, "name": f"n{i}",
                                                    "settings": {"sig": "OLD", "v": 1}}))


@pytest.fixture()
def saves(tmp_path, monkeypatch):
    d = tmp_path / "saves"
    d.mkdir()
    monkeypatch.setattr(ss, "saves_dir", lambda create=False: d)
    monkeypatch.setattr(ss, "import_legacy_once", lambda: 0)
    return d


def test_temporary_names_are_unique_and_leave_nothing_behind(tmp_path):
    names = []
    real = ss.os.replace

    def spy(a, b):
        names.append(str(a))
        return real(a, b)

    target = tmp_path / "x.meta"
    ss.os.replace = spy
    try:
        ss._atomic_write(target, "1")
        ss._atomic_write(target, "2")
    finally:
        ss.os.replace = real
    assert len(set(names)) == 2 and all(n.endswith(".tmp") for n in names)
    assert target.read_text() == "2"
    assert [p.name for p in tmp_path.iterdir()] == ["x.meta"]


def test_concurrent_rebuilds_never_expose_a_partial_sidecar(saves):
    _mk(saves, 1, with_old_sidecar=False)
    f = saves / "s000.json"
    errors = []

    def work():
        for _ in range(40):
            ss._meta_path(f).unlink(missing_ok=True)
            row = ss._read_meta_row(f)
            if row is None or row["id"] != "s000":
                errors.append(row)
            try:
                json.loads(ss._meta_path(f).read_text())
            except FileNotFoundError:
                pass
            except ValueError as e:
                errors.append(e)

    ts = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    assert not [p for p in saves.iterdir() if p.suffix == ".tmp"]


def test_migration_is_bounded_per_listing_and_finishes_on_later_calls(saves, monkeypatch):
    _mk(saves, 7)
    monkeypatch.setattr(ss, "REBUILD_MAX_PER_LISTING", 3)
    stored = {p.name: p.read_text() for p in saves.glob("*.json")}
    rows = ss.list_entries()
    assert len(rows) == 7
    pending = [r for r in rows if r["settings"]["source"] == "pending_rebuild"]
    assert len(pending) == 4
    assert all(r["settings"]["sig"] is None and r["settings"]["steps_per_period"] is None for r in pending)
    assert all("OLD" not in json.dumps(r["settings"]) for r in rows)
    assert len([r for r in rows if r["settings"]["v"] == ss.SETTINGS_VERSION]) == 3
    assert len(ss.list_entries()) == 7
    ss.list_entries()
    final = ss.list_entries()
    assert all(r["settings"]["v"] == ss.SETTINGS_VERSION for r in final)
    assert {p.name: p.read_text() for p in saves.glob("*.json")} == stored


def test_time_budget_stops_the_rebuild(saves, monkeypatch):
    _mk(saves, 5, with_old_sidecar=False)
    monkeypatch.setattr(ss, "REBUILD_SECONDS_PER_LISTING", -1.0)
    rows = ss.list_entries()
    assert len(rows) == 5 and {r["id"] for r in rows} == {f"s{i:03d}" for i in range(5)}
    # the first stale one is always admitted, the rest wait
    assert len([r for r in rows if r["settings"]["source"] == "pending_rebuild"]) == 4


def test_a_slow_scan_of_current_sidecars_cannot_starve_the_stale_tail(saves, monkeypatch):
    _mk(saves, 6)
    for i in range(3):                                   # current prefix: sidecars already in the new format
        f = saves / f"s{i:03d}.json"
        ss._write_meta(f, json.loads(f.read_text()))
    clock = {"t": 0.0}
    monkeypatch.setattr(ss.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(ss, "REBUILD_SECONDS_PER_LISTING", 2.0)
    real = ss._read_meta_row

    def slow(f, budget=None):
        if ss._meta_path(f).exists() and (json.loads(ss._meta_path(f).read_text()).get("settings") or {}).get("v") == ss.SETTINGS_VERSION:
            clock["t"] += 5.0                            # scanning a current sidecar is slow
        return real(f, budget)

    monkeypatch.setattr(ss, "_read_meta_row", slow)
    last = None
    for _ in range(8):
        rows = ss.list_entries()
        n = len([r for r in rows if r["settings"]["source"] == "pending_rebuild"])
        assert last is None or n < last or n == 0
        last = n
        if n == 0:
            break
    assert last == 0


@pytest.mark.skipif(not hasattr(__import__("os"), "chmod") or __import__("os").name != "posix", reason="POSIX modes")
def test_atomic_write_keeps_the_target_mode_and_new_files_get_the_plain_write_mode(tmp_path):
    import os
    t = tmp_path / "a.json"
    t.write_text("x")
    os.chmod(t, 0o644)
    ss._atomic_write(t, "y")
    assert t.read_text() == "y" and (t.stat().st_mode & 0o777) == 0o644
    os.chmod(t, 0o640)
    ss._atomic_write(t, "z")
    assert (t.stat().st_mode & 0o777) == 0o640
    plain = tmp_path / "plain.txt"
    plain.write_text("p")                                # what the old code produced
    new = tmp_path / "b.json"
    ss._atomic_write(new, "n")
    assert (new.stat().st_mode & 0o777) == (plain.stat().st_mode & 0o777)
