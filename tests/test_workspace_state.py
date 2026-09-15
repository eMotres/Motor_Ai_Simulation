"""The in-memory stores, per workspace — migration Stage 3.

WHAT THIS FILE DEFENDS
======================

Stage 1 moved the FILES a caller reads and writes; Stage 2 split the catalog
into layers.  Both left the largest single-user object in the process
untouched: ~25 module-level dicts holding the geometry singleton, the four
``_fem_*`` caches, three ``_LAST`` stores, the transient reference, the field
snapshots, the thermal / mechanical / static-3D / family / freecad caches and
the optimizer's four campaign slots.  Two accounts on one box share every one
of them, so the disk isolation Stages 1-2 bought is undone the moment either
one answers from memory: A edits a slot, B's next solve is about A's motor.

Five promises, one per section below.

**(a) With ``WORKSPACES_ROOT`` unset, nothing moved.**  Every relocated name
still resolves to the PROCESS workspace's store, so this workstation, the CLI,
``refine_proc`` and the rest of the suite read and write exactly what they did.
This is the promise the other twenty test modules are the real proof of — they
all still monkeypatch and mutate these names — and the assertions here pin the
mechanism those modules rely on.

**(b) With it set, two workspaces cannot see each other's memory**, and B's
work cannot EVICT A's: the stores are per workspace, so a cap is spent on one
account's entries and never on the other's.

**(c) Every cache key produced under a workspace starts with that workspace's
id.**  Reflective, over every live store — the audit that a cache which had
somehow leaked would have to lie twice to pass.

**(d) Memory plateaus.**  Two workspaces × 200 alternating requests through the
real app with mocked solvers; RSS must not keep climbing and the registry must
not exceed its cap.

**(e) Eviction drops MEMORY, never DISK.**  An idle workspace's stores are
cleared and its ``.last_*`` files are still there, so its next request restores
exactly what it had.

Nothing here solves a field; every write lands in ``tmp_path``.
"""
from __future__ import annotations

import gc
import os
import pickle
import shutil
from pathlib import Path

import pytest
import yaml

from motor_ai_sim import workspace as W


_HERE = Path(__file__).resolve().parent
_REAL_CONFIG = _HERE.parent / "config"
_REAL_USERS = _REAL_CONFIG / "users.json"

A = "alice@example.com"
B = "bob@example.com"


# ─────────────────────────────────────────────────────────────────────────────
#  RSS, without adding a dependency
# ─────────────────────────────────────────────────────────────────────────────

def _rss_bytes():
    """Resident set size of THIS process, or None when it cannot be measured.

    ``psutil`` when it is installed (the migration plan's instrument), and
    otherwise the platform's own counter — Windows' ``GetProcessMemoryInfo``
    through ctypes, Linux's ``/proc/self/statm``.  The fall-backs exist so the
    soak below is a real assertion on this workstation rather than a skip:
    ``psutil`` is not in ``requirements.txt`` and adding it is Stage 6's call,
    not this stage's.
    """
    try:
        import psutil                                   # noqa: PLC0415
        return int(psutil.Process().memory_info().rss)
    except Exception:                                   # noqa: BLE001
        pass
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class _PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD),
                            ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]

            pmc = _PMC()
            pmc.cb = ctypes.sizeof(_PMC)
            # Spell the signature out.  Left to its defaults ctypes treats the
            # handle as a C int, and the current-process pseudo-handle is -1 as
            # a 64-bit value: either truncated (the call quietly returns 0) or
            # rejected outright.  ``c_void_p(-1)`` IS that pseudo-handle.
            gpmi = ctypes.windll.psapi.GetProcessMemoryInfo
            gpmi.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC),
                             wintypes.DWORD]
            gpmi.restype = wintypes.BOOL
            ok = gpmi(ctypes.c_void_p(-1), ctypes.byref(pmc), pmc.cb)
            return int(pmc.WorkingSetSize) if ok else None
        except Exception:                               # noqa: BLE001
            return None
    try:
        with open("/proc/self/statm", encoding="ascii") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except Exception:                                   # noqa: BLE001
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  The stores under test, by module-level NAME
# ─────────────────────────────────────────────────────────────────────────────

def _moved_stores():
    """``[(label, module, attribute)]`` — every global this stage relocated.

    Spelled out by NAME rather than discovered, because the point of the stage
    is that the names survived: a rename would be a silent break for the two
    dozen tests that monkeypatch them and for every reader outside the module.
    """
    from motor_ai_sim.routes import (coupled, family, freecad, geometry,
                                     mechanical, optimization, simulation,
                                     static3d, thermal)
    from motor_ai_sim.services import geometry_service
    from motor_ai_sim.simulation import fem_solver_2d
    return [
        ("geometry_service._mesh_store", geometry_service, "_mesh_store"),
        ("simulation._motor_geom_cache", simulation, "_motor_geom_cache"),
        ("simulation._motor_geom_ghash", simulation, "_motor_geom_ghash"),
        ("simulation._fem_mesh_cache", simulation, "_fem_mesh_cache"),
        ("simulation._fem_mesh_sb_cache", simulation, "_fem_mesh_sb_cache"),
        ("simulation._fem_field_cache", simulation, "_fem_field_cache"),
        ("simulation._fem_transient_cache", simulation, "_fem_transient_cache"),
        ("simulation._transient_field_snap", simulation, "_transient_field_snap"),
        ("simulation._last_transient_ref", simulation, "_last_transient_ref"),
        ("simulation._cache_state", simulation, "_cache_state"),
        ("thermal._LAST", thermal, "_LAST"),
        ("thermal._FIELD_CACHE", thermal, "_FIELD_CACHE"),
        ("thermal._COUPLED_CACHE", thermal, "_COUPLED_CACHE"),
        ("thermal._MESH_CACHE", thermal, "_MESH_CACHE"),
        ("thermal._LOSS_MAPS", thermal, "_LOSS_MAPS"),
        ("thermal._POLY_CACHE", thermal, "_POLY_CACHE"),
        ("mechanical._LAST", mechanical, "_LAST"),
        ("mechanical._MESH_CACHE", mechanical, "_MESH_CACHE"),
        ("coupled._LAST", coupled, "_LAST"),
        ("family._TREE_CACHE", family, "_TREE_CACHE"),
        ("freecad._BUNDLE_CACHE", freecad, "_BUNDLE_CACHE"),
        ("geometry._mesh_cache", geometry, "_mesh_cache"),
        ("geometry._mesh2d_cache", geometry, "_mesh2d_cache"),
        ("geometry._mesh_extruded_cache", geometry, "_mesh_extruded_cache"),
        ("static3d._SECTION_CACHE", static3d, "_SECTION_CACHE"),
        ("static3d._ENTRY_CACHE", static3d, "_ENTRY_CACHE"),
        ("static3d._ENTRY_ORDER", static3d, "_ENTRY_ORDER"),
        ("optimization._EVAL_CACHE", optimization, "_EVAL_CACHE"),
        ("optimization._warm_seed_memo", optimization, "_warm_seed_memo"),
        ("optimization._refine_state", optimization, "_refine_state"),
        ("optimization._scan_state", optimization, "_scan_state"),
        ("optimization._doe_state", optimization, "_doe_state"),
        ("optimization._descent_state", optimization, "_descent_state"),
        ("optimization._descent_disk_mtime", optimization, "_descent_disk_mtime"),
        ("optimization._descent_external", optimization, "_descent_external"),
        ("fem_solver_2d._SB_WARM_CACHE", fem_solver_2d, "_SB_WARM_CACHE"),
    ]


# ═════════════════════════════════════════════════════════════════════════════
#  (a)  WORKSPACES_ROOT unset -> the process workspace, exactly as before
# ═════════════════════════════════════════════════════════════════════════════

def test_every_moved_global_is_a_workspace_proxy(monkeypatch):
    """Each relocated name is still a module attribute, and a live proxy.

    A real attribute and not a ``__getattr__``: ``monkeypatch.setattr`` on
    these names is how two dozen existing tests work, and the module's own code
    reads the same module dict, so a patch has to reach BOTH.  See the next
    test for that half.
    """
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    for label, mod, attr in _moved_stores():
        assert attr in vars(mod), f"{label} is no longer a module attribute"
        obj = getattr(mod, attr)
        assert isinstance(obj, (W.StateMapping, W.StateList)), (
            f"{label} is a {type(obj).__name__}, not a per-workspace proxy")


def test_unset_resolves_to_the_process_workspace(monkeypatch):
    """With no ``WORKSPACES_ROOT`` every store IS the process workspace's."""
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    proc = W.process_workspace()
    assert W.workspace() is proc
    for label, mod, attr in _moved_stores():
        obj = getattr(mod, attr)
        target = obj.target
        assert proc.state.peek(obj._name) is target, (
            f"{label} did not resolve to the process workspace's slot")
        if isinstance(obj, W.StateMapping):
            assert target.workspace_id == W.PROCESS_WS_ID


def test_monkeypatching_a_store_still_reaches_the_module(monkeypatch):
    """``monkeypatch.setattr(th, "_LAST", {})`` must be seen by thermal itself.

    ``tests/test_coupled.py``, ``test_progress_routes.py``,
    ``test_thermal_*`` and four others do exactly this and then assert on what
    the ROUTE wrote.  A replacement in the module dict shadows the proxy for
    the module's own global lookups, which is why the proxy is a real attribute
    and not a ``__getattr__`` hook.
    """
    from motor_ai_sim.routes import thermal as th
    fake = {}
    monkeypatch.setattr(th, "_LAST", fake, raising=True)
    th._LAST["field"] = {"result": {"ok": True}}
    assert fake == {"field": {"result": {"ok": True}}}
    # …and the workspace store was NOT touched by the patched write.
    assert "field" not in W.process_workspace().state.peek("thermal.last", {})


def test_the_loaded_flags_survive_as_names(monkeypatch):
    """``_LAST_LOADED`` / ``_LOSS_MAPS_LOADED`` read and write by name."""
    from motor_ai_sim.routes import coupled as cp
    from motor_ai_sim.routes import mechanical as me
    from motor_ai_sim.routes import thermal as th
    assert isinstance(th._LAST_LOADED, bool)
    assert isinstance(th._LOSS_MAPS_LOADED, bool)
    assert isinstance(me._LAST_LOADED, bool)
    assert isinstance(cp._LAST_LOADED, bool)
    monkeypatch.setattr(th, "_LOSS_MAPS_LOADED", True, raising=True)
    assert th._loss_maps_loaded() is True


def test_bounded_store_behaves_like_the_dict_it_replaced():
    """The mapping surface the call sites actually use, one by one."""
    s = W.BoundedStore("ws-x", "demo", 3)
    assert s == {}
    s["a"], s["b"], s["c"] = 1, 2, 3
    assert dict(s) == {"a": 1, "b": 2, "c": 3}
    assert list(s) == ["a", "b", "c"] and list(reversed(s)) == ["c", "b", "a"]
    assert "a" in s and s.get("zz") is None and len(s) == 3
    s["d"] = 4                                   # the cap evicts oldest-first
    assert list(s) == ["b", "c", "d"] and s.evicted == 1
    s.move_to_end("b")
    assert list(s) == ["c", "d", "b"]
    assert s.popitem(last=False) == ("c", 3)
    assert s.pop("zz", "dflt") == "dflt"
    s.update({"e": 5})
    assert set(s) == {"d", "b", "e"}
    s.clear()
    assert s == {} and len(s) == 0
    # …and the REAL key carries the workspace, which is what (c) checks.
    s["k"] = 1
    assert s.raw_keys() == [("ws-x", "k")]


def test_reading_does_not_reorder_where_order_is_meaning():
    """``next(reversed(snap))`` means "the newest run", not "the last read"."""
    s = W.BoundedStore("ws-x", "snap", 3, lru_on_read=False)
    s["old"], s["new"] = 1, 2
    _ = s["old"]
    assert next(reversed(s)) == "new"
    c = W.BoundedStore("ws-x", "cache", 3, lru_on_read=True)
    c["old"], c["new"] = 1, 2
    _ = c["old"]
    assert next(reversed(c)) == "old"            # a pure cache DOES touch


# ═════════════════════════════════════════════════════════════════════════════
#  Two real workspaces
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def two(tmp_path, monkeypatch):
    """Two workspaces with their own config, through the real resolver."""
    root = tmp_path / "srv"
    works, shared = root / "workspaces", root / "shared"
    works.mkdir(parents=True)
    shared.mkdir(parents=True)
    src = _REAL_CONFIG / "motor_config.yaml"
    base = yaml.safe_load(src.read_text(encoding="utf-8"))
    shutil.copy2(src, shared / "motor_config.yaml")

    monkeypatch.setenv("WORKSPACES_ROOT", str(works))
    monkeypatch.setenv("SHARED_ROOT", str(shared))
    monkeypatch.setenv("PUBLISHED_ROOT", str(root / "published"))

    ws = {}
    for email, length in ((A, 40.0), (B, 41.0)):
        w = W.workspace_for_identity(email)
        cfg = dict(base)
        cfg["geometry"] = dict(base.get("geometry") or {})
        cfg["geometry"]["motor_length"] = length
        Path(str(w.config_file)).write_text(
            yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
            encoding="utf-8")
        w.state.evict()                       # nothing carried in from seeding
        ws[email] = w
    from motor_ai_sim.config import clear_config_cache
    clear_config_cache()
    yield ws
    for w in ws.values():
        w.state.evict()
    clear_config_cache()


# ── (b) isolation ────────────────────────────────────────────────────────────

def test_thermal_last_is_invisible_across_workspaces(two):
    from motor_ai_sim.routes import coupled as cp
    from motor_ai_sim.routes import mechanical as me
    from motor_ai_sim.routes import thermal as th
    with W.use_workspace(two[A]):
        th._LAST["field"] = {"result": {"T_max": 111.0}}
        me._LAST["rotor_stress"] = {"result": {"sf": 1.5}}
        cp._LAST.update({"ok": True, "coil_temp_c": 200.0})
    with W.use_workspace(two[B]):
        assert th._LAST == {} and me._LAST == {} and cp._LAST == {}
        th._LAST["field"] = {"result": {"T_max": 222.0}}
    with W.use_workspace(two[A]):
        assert th._LAST["field"]["result"]["T_max"] == 111.0
        assert cp._LAST["coil_temp_c"] == 200.0


def test_transient_ref_is_invisible_across_workspaces(two):
    from motor_ai_sim.routes import simulation as sim
    with W.use_workspace(two[A]):
        sim._last_transient_ref["key"] = ("A",)
        sim._last_transient_ref["result"] = {"summary": {"T_em_avg_Nm": 12.0}}
        sim._fem_transient_cache[("A",)] = {"ok": True}
    with W.use_workspace(two[B]):
        assert sim._last_transient_ref["key"] is None
        assert sim._last_transient_ref["result"] is None
        assert ("A",) not in sim._fem_transient_cache
    with W.use_workspace(two[A]):
        assert sim._last_transient_ref["key"] == ("A",)


def test_geometry_singleton_is_per_workspace(two):
    """The singleton that started this whole stage."""
    from motor_ai_sim.services import geometry_service as gs
    with W.use_workspace(two[A]):
        ga = gs.get_current_geometry(reload=True)
    with W.use_workspace(two[B]):
        gb = gs.get_current_geometry(reload=True)
        assert gs._geom_get() is gb
    with W.use_workspace(two[A]):
        assert gs._geom_get() is ga, "B's reload replaced A's geometry"
    assert ga.to_dict()["motor_length"] == 40.0
    assert gb.to_dict()["motor_length"] == 41.0


def test_b_solving_does_not_evict_a_snapshot(two):
    """A's field snapshots survive any amount of work by B.

    The snapshot store holds three entries — spend five in B and, with one
    shared store, A's would all be gone and its J⟳ view blank.
    """
    from motor_ai_sim.routes import simulation as sim
    with W.use_workspace(two[A]):
        for i in range(sim._TRANSIENT_SNAP_MAX):
            sim._transient_field_snap[("A", i)] = {"meta": {"computed_at": i}}
        mine = list(sim._transient_field_snap)
    with W.use_workspace(two[B]):
        for i in range(5):
            sim._transient_field_snap[("B", i)] = {"meta": {"computed_at": i}}
        assert len(sim._transient_field_snap) == sim._TRANSIENT_SNAP_MAX
    with W.use_workspace(two[A]):
        assert list(sim._transient_field_snap) == mine


def test_campaign_slots_are_per_workspace(two):
    """"A scan is already running" must be about the CALLER's scan."""
    from motor_ai_sim.routes import optimization as opt
    with W.use_workspace(two[A]):
        opt._scan_state["running"] = True
        opt._descent_state["run_id"] = "A-run"
    with W.use_workspace(two[B]):
        assert opt._scan_state["running"] is False
        assert opt._descent_state["run_id"] == ""
    with W.use_workspace(two[A]):
        assert opt._scan_state["running"] is True
        assert opt._descent_state["run_id"] == "A-run"


def test_eval_cache_and_locks_are_per_workspace(two):
    from motor_ai_sim.routes import optimization as opt
    with W.use_workspace(two[A]):
        opt._EVAL_CACHE["k"] = {"T_em_Nm": 1.0}
        lock_a = opt._eval_cache_lock.target
    with W.use_workspace(two[B]):
        assert "k" not in opt._EVAL_CACHE
        assert opt._eval_cache_lock.target is not lock_a
        with opt._eval_cache_lock:
            pass


def test_a_thread_carries_its_owner_workspace(two):
    """``workspace.bind`` — a persist thread must write its OWNER's folder."""
    import threading
    seen = {}

    def _who():
        seen["id"] = W.workspace().id

    with W.use_workspace(two[B]):
        t = threading.Thread(target=W.bind(_who))
    t.start()
    t.join(5)
    assert seen["id"] == two[B].id

    seen.clear()
    with W.use_workspace(two[B]):
        t = threading.Thread(target=_who)       # …and without it, it would not
    t.start()
    t.join(5)
    assert seen["id"] == W.PROCESS_WS_ID


# ── (c) the reflective audit ─────────────────────────────────────────────────

def test_every_live_cache_key_carries_its_workspace(two):
    """Walk every store both workspaces produced and check the stamp.

    A key is stored as ``(ws_id, key)``.  Nothing DEPENDS on that prefix for
    isolation — each workspace owns its own container — which is what makes it
    a usable audit: an entry that had somehow crossed over would carry the
    wrong stamp and be caught here rather than in a customer's answer.
    """
    from motor_ai_sim.routes import optimization as opt
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.routes import thermal as th
    from motor_ai_sim.services import geometry_service as gs

    for email in (A, B):
        with W.use_workspace(two[email]):
            th._LAST["field"] = {"result": {"T_max": 1.0}}
            th._FIELD_CACHE[("fp", email)] = {"T": [1.0]}
            th._LOSS_MAPS[f"id-{email}"] = {"em": {}}
            sim._fem_field_cache[("fp", email)] = {"A": [0.0]}
            sim._transient_field_snap[("snap", email)] = {"meta": {}}
            sim._last_transient_ref["key"] = (email,)
            opt._EVAL_CACHE[f"k-{email}"] = {"T_em_Nm": 1.0}
            opt._descent_state["run_id"] = email
            gs.get_current_geometry(reload=True)

    seen = 0
    for email in (A, B):
        ws = two[email]
        audit = W.audit_cache_keys(ws)
        assert audit, "no store was created for this workspace at all"
        for name, keys in audit.items():
            for raw in keys:
                assert isinstance(raw, tuple) and len(raw) == 2, (
                    f"{name}: {raw!r} is not a (ws_id, key) pair")
                assert raw[0] == ws.id, (
                    f"{name}: key {raw[1]!r} is stamped {raw[0]!r}, not "
                    f"{ws.id!r} — a store crossed workspaces")
                seen += 1
    assert seen > 20, "the audit walked almost nothing; it proves nothing"


# ── (e) eviction drops memory, never disk ────────────────────────────────────

def test_eviction_drops_memory_and_keeps_the_disk_stores(two):
    from motor_ai_sim.routes import thermal as th
    ws = two[A]
    entry = {"result": {"T_max": 99.0}, "params": {}, "geometry_fingerprint": "fp"}
    with W.use_workspace(ws):
        th._LAST["field"] = entry
        pkl = Path(th._last_store_path())
        pkl.write_bytes(pickle.dumps({"field": entry}))
        assert len(th._LAST) == 1

    dropped = ws.state.evict()
    assert dropped >= 1
    assert not ws.state.slots and not ws.state.flags

    assert pkl.is_file(), "eviction deleted a DISK store"
    with W.use_workspace(ws):
        assert th._LAST == {}, "memory was not actually dropped"
        th._load_last()                       # the next request restores it
        assert th._LAST["field"]["result"]["T_max"] == 99.0


def test_the_registry_is_an_lru_with_a_cap(tmp_path, monkeypatch):
    """The 33rd account does not make the server hold 33 sets of caches."""
    from motor_ai_sim.routes import thermal as th
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "works"))
    monkeypatch.setattr(W, "MAX_WORKSPACES", 4)
    W._REG.clear()
    try:
        first = W.workspace_for_identity("user0@example.com")
        with W.use_workspace(first):
            th._LAST["field"] = {"result": {"T_max": 1.0}}
        assert len(first.state.slots) >= 1
        for i in range(1, 8):
            W.workspace_for_identity(f"user{i}@example.com")
        assert W.registry_size() <= 4
        assert first.id not in W.registry_ids(), "the LRU kept the oldest"
        assert not first.state.slots, "an evicted workspace kept its memory"
    finally:
        W._REG.clear()


# ═════════════════════════════════════════════════════════════════════════════
#  (d)  The soak — two workspaces, 200 alternating requests, memory plateaus
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def soak_env(two, monkeypatch):
    """The real app, real auth, two real accounts — only the paths are faked."""
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    users_file = _REAL_CONFIG.parent / "config" / "users.json"
    tmp_users = Path(str(two[A].root)).parent.parent / "users.json"
    if users_file.exists():
        shutil.copy2(users_file, tmp_users)
    monkeypatch.setattr(U, "_USERS_FILE", tmp_users)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {A, B})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    U.create_user(A, "password-a", tier="admin", name="Alice")
    U.create_user(B, "password-b", tier="admin", name="Bob")
    return {A: {"Authorization": f"Bearer {U.issue_token(A)}"},
            B: {"Authorization": f"Bearer {U.issue_token(B)}"}}


def _mock_solve(sim, th, me, i: int) -> None:
    """A solve, without a solver: the payload shapes the caches really hold.

    ~400 kB of floats per call.  Under one shared store 400 alternating calls
    would be bounded too — the point of the measurement is that TWO workspaces
    are bounded SEPARATELY and neither grows without end.
    """
    field = {"T": [float(i)] * 12000, "flux": [float(i)] * 12000}
    sim._fem_field_cache[("soak", i)] = field
    sim._transient_field_snap[("soak", i)] = {"field": field, "meta": {},
                                              "scalars": {}}
    sim._fem_mesh_cache[("soak", i)] = {"vertices": [0.0] * 12000}
    th._FIELD_CACHE[("soak", i)] = {"temperature_per_node": [float(i)] * 12000}
    th._LAST["field"] = {"result": {"T_max": float(i)}, "params": {},
                         "geometry_fingerprint": "fp"}
    me._LAST["rotor_stress"] = {"result": {"sf": 1.0}, "params": {},
                                "geometry_fingerprint": "fp"}


def test_soak_two_workspaces_memory_plateaus(two, soak_env, capfd):
    import logging

    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    from motor_ai_sim.routes import mechanical as me
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.routes import thermal as th

    client = TestClient(app)
    paths = ("/api/thermal/last", "/api/mechanical/last", "/api/coupled/last",
             "/api/simulation/caches")

    def _cycle(n: int) -> None:
        for i in range(n):
            email = A if i % 2 == 0 else B
            with W.use_workspace(two[email]):
                _mock_solve(sim, th, me, i)
            r = client.get(paths[i % len(paths)], headers=soak_env[email])
            assert r.status_code < 500, (paths[i % len(paths)], r.text)
            # Collect periodically.  The question is whether LIVE memory
            # plateaus; Windows never shrinks a working set on free, so
            # cyclic garbage left lying between two collections reads as
            # unbounded growth no matter what the stores do.
            if i % 25 == 24:
                gc.collect()

    # THE CAPTURE IS NOT THE SUBJECT.  These routes log (and the geometry
    # sanitiser prints) on every request; pytest keeps every captured byte and
    # every LogRecord for the duration of the test, so a naive measurement here
    # reads 370 MB of pytest's own buffers as a leak in the code under test.
    # Silence both for the duration and measure the process, not the harness.
    with capfd.disabled():
        logging.disable(logging.ERROR)
        try:
            # Warm-up first, and generously: the first requests build polygons,
            # parse both configs, take the lazy restores and grow the
            # allocator's arenas.  Measured standalone, RSS is flat from ~50
            # requests in (230 -> 254 MB over 500), so 100 clears the ramp.
            _cycle(100)
            gc.collect()
            first = _rss_bytes()
            _cycle(200)
            gc.collect()
            before = _rss_bytes()
            _cycle(400)              # 2 workspaces x 200 requests each
            gc.collect()
            after = _rss_bytes()
            if None not in (first, before, after):
                print(f"[soak] RSS after 100 / 300 / 700 requests: "
                      f"{first/1e6:.1f} / {before/1e6:.1f} / {after/1e6:.1f} MB")
        finally:
            logging.disable(logging.NOTSET)

    # The stores are bounded, so both must be at their caps and no larger.
    for email in (A, B):
        with W.use_workspace(two[email]):
            assert len(sim._fem_field_cache) <= sim._FEM_FIELD_CACHE_MAX
            assert len(sim._transient_field_snap) <= sim._TRANSIENT_SNAP_MAX
            assert len(sim._fem_mesh_cache) <= sim._FEM_MESH_CACHE_MAX
            assert len(th._FIELD_CACHE) <= th._FIELD_CACHE_MAX
            assert len(th._LAST) <= th._LAST_MAX
    assert W.registry_size() <= W.MAX_WORKSPACES

    assert before is not None and after is not None, "RSS is not measurable here"
    growth_mb = (after - before) / 1e6
    assert growth_mb < 50.0, (
        f"RSS grew {growth_mb:.1f} MB over 400 requests across two workspaces "
        f"(warm-up {first/1e6:.1f} -> {before/1e6:.1f} -> {after/1e6:.1f} MB) "
        f"— a store is unbounded")
