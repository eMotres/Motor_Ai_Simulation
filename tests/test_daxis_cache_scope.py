"""Whose d-axis calibration is this, and for which material cards?

Codex review of PR #68 (docs/SOLVE_POOL_2026-09-29.md, "Review 2026-09-30"):
``fem_solver_2d._DAXIS_CACHE`` was one module-global dict for the whole API
process, so one account's calibration answered for every other account's
machine with the same key.  It is now a per-workspace map, its disk mirror sits
in the workspace root, and its lock is per workspace.

The d-axis KEY deliberately carries no material fingerprint (θ* is a symmetry
property; measured material-invariant to mesh noise, see the comment above
``_DAXIS_CACHE``), while the ψ_PM / catalogue-Ld/Lq key, which shares the disk
file and DOES scale with the cards, now carries one.

No FEM here: ``em_transient_eval`` is stubbed with a four-frame ψ_A curve.
"""
from __future__ import annotations

import json

import pytest

from motor_ai_sim import workspace as WS
from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.simulation import fem_solver_2d as F


class _P:
    num_poles = 14


GEO = {"num_slots": 12, "stator_diameter": 40.0}
WIND = {"layers": 1, "connection": "2S"}

#: ψ_A peaks at frame 1 (vertex +1/6 frame) -> θ* = 7/6 mech deg -> DAXIS 81.8333.
PSI_A = [1.0, 3.0, 2.0, 1.0]
#: ψ_A peaks at frame 2 (vertex -1/6 frame) -> θ* = 11/6 mech deg -> DAXIS 77.1667.
PSI_B = [1.0, 2.0, 3.0, 1.0]
DAX_A = (90.0 - 7.0 / 6.0 * 7.0) % 360.0
DAX_B = (90.0 - 11.0 / 6.0 * 7.0) % 360.0


def _cal(psi):
    return {"psi_A_Wb": list(psi), "rotor_angle_deg": [0.0, 1.0, 2.0, 3.0],
            "picard_converged": True, "picard_unconverged_frames": [],
            "picard_fallback_frames": [], "picard_tol": 1e-3,
            "picard_resid_max": 1e-8}


def _ws(tmp_path, name):
    ws = WS.Workspace(id="test-" + name, email=name + "@x.test",
                      root=tmp_path / name, shared_root=tmp_path / "shared")
    ws.root.mkdir(parents=True, exist_ok=True)
    return ws


@pytest.fixture()
def solver(monkeypatch):
    """A stub no-load solve that answers per WORKSPACE (alice's machine peaks
    one frame earlier than bob's) and counts its calls."""
    calls = []
    curves = {"test-alice": PSI_A, "test-bob": PSI_B}

    def _stub(**kw):
        ws_id = WS.workspace().id
        calls.append(ws_id)
        return _cal(curves.get(ws_id, PSI_A))

    monkeypatch.setattr(F, "em_transient_eval", _stub)
    return calls


def _resolve():
    return F._resolve_daxis_shift(_P, GEO, WIND, 7, None, 2)


# ── per workspace ────────────────────────────────────────────────────────────

def test_two_workspaces_do_not_share_a_calibration(tmp_path, solver):
    alice, bob = _ws(tmp_path, "alice"), _ws(tmp_path, "bob")
    key = F._daxis_topology_key(_P, GEO, WIND)

    with WS.use_workspace(alice):
        assert _resolve() == pytest.approx(DAX_A)
    # Bob builds the SAME key: before this fix he was handed alice's angle.
    with WS.use_workspace(bob):
        assert key not in F._DAXIS_CACHE
        assert _resolve() == pytest.approx(DAX_B)
    assert solver == ["test-alice", "test-bob"]

    # each keeps its own, and a repeat is a memory hit, not a solve
    with WS.use_workspace(alice):
        assert _resolve() == pytest.approx(DAX_A)
        assert F._DAXIS_CACHE[key] == pytest.approx(DAX_A)
        assert all(rk[0] == alice.id for rk in F._DAXIS_CACHE.raw_keys())
    with WS.use_workspace(bob):
        assert _resolve() == pytest.approx(DAX_B)
    assert len(solver) == 2
    # and neither leaked into the process workspace
    with WS.use_workspace(WS.process_workspace()):
        assert key not in F._DAXIS_CACHE


def test_the_disk_mirror_is_per_workspace(tmp_path, solver):
    alice, bob = _ws(tmp_path, "alice"), _ws(tmp_path, "bob")
    skey = "_".join(str(x) for x in F._daxis_topology_key(_P, GEO, WIND))

    with WS.use_workspace(alice):
        assert F._daxis_disk_path() == str(alice.root / ".daxis_cache.json")
        _resolve()
    with WS.use_workspace(bob):
        assert F._daxis_disk_path() == str(bob.root / ".daxis_cache.json")
        _resolve()
    disk_a = json.loads((alice.root / ".daxis_cache.json").read_text())
    disk_b = json.loads((bob.root / ".daxis_cache.json").read_text())
    assert disk_a[skey] == pytest.approx(DAX_A)
    assert disk_b[skey] == pytest.approx(DAX_B)

    # memory gone (eviction, restart): each workspace reads ITS file, no solve
    alice.state.evict()
    bob.state.evict()
    with WS.use_workspace(alice):
        assert _resolve() == pytest.approx(DAX_A)
    with WS.use_workspace(bob):
        assert _resolve() == pytest.approx(DAX_B)
    assert solver == ["test-alice", "test-bob"]


def test_the_lock_is_per_workspace(tmp_path):
    alice, bob = _ws(tmp_path, "alice"), _ws(tmp_path, "bob")
    with WS.use_workspace(alice):
        la = F._DAXIS_LOCK.target
        with F._DAXIS_LOCK:          # re-entrant: a nested calibration walks in
            with F._DAXIS_LOCK:
                pass
    with WS.use_workspace(bob):
        lb = F._DAXIS_LOCK.target
    assert la is not lb


def test_single_user_default_is_the_process_workspace(monkeypatch, solver):
    """No WORKSPACES_ROOT, no bound workspace: the process workspace answers,
    exactly as the module-global dict did (disk off here)."""
    monkeypatch.setattr(F, "_daxis_disk_path", lambda: None)
    F._DAXIS_CACHE.clear()
    try:
        assert WS.workspace().is_process
        assert _resolve() == pytest.approx(DAX_A)
        assert _resolve() == pytest.approx(DAX_A)
        assert solver == ["process"]
    finally:
        F._DAXIS_CACHE.clear()


# ── the disk file is read as untrusted ───────────────────────────────────────

@pytest.mark.parametrize("junk", ["sixty", None, [60.0, 1.0], {"daxis": 60.0},
                                  True, float("nan"), float("inf")])
def test_an_unusable_disk_entry_is_a_miss_and_is_replaced(tmp_path, solver, junk):
    alice = _ws(tmp_path, "alice")
    skey = "_".join(str(x) for x in F._daxis_topology_key(_P, GEO, WIND))
    disk = alice.root / ".daxis_cache.json"
    other = "psipm_v3_fp_L1_C2S_w2-x_T6P1_m0"      # a ψ_PM entry sharing the file
    disk.write_text(json.dumps({skey: junk, other: [1.0, 2.0]}))
    with WS.use_workspace(alice):
        assert _resolve() == pytest.approx(DAX_A)
    assert solver == ["test-alice"]
    after = json.loads(disk.read_text())
    assert after[skey] == pytest.approx(DAX_A)
    assert after[other] == [1.0, 2.0]               # other entries survive


@pytest.mark.parametrize("content", ["[1, 2, 3]", "\"text\"", "{not json"])
def test_a_disk_file_that_is_not_a_json_object_is_a_miss(tmp_path, solver, content):
    alice = _ws(tmp_path, "alice")
    disk = alice.root / ".daxis_cache.json"
    disk.write_text(content)
    skey = "_".join(str(x) for x in F._daxis_topology_key(_P, GEO, WIND))
    with WS.use_workspace(alice):
        assert _resolve() == pytest.approx(DAX_A)
    assert solver == ["test-alice"]
    assert json.loads(disk.read_text())[skey] == pytest.approx(DAX_A)


def test_old_format_entries_are_never_served(tmp_path, solver):
    """A pre-fingerprint (4-field) and a pre-winding-identity (5-field) entry
    for the same counts are ignored: the lookup builds the current key."""
    alice = _ws(tmp_path, "alice")
    key = F._daxis_topology_key(_P, GEO, WIND)
    old4 = "_".join(str(x) for x in key[:4])
    old5 = "_".join(str(x) for x in key[:4] + key[-1:])
    (alice.root / ".daxis_cache.json").write_text(json.dumps({old4: 108.0,
                                                              old5: 33.0}))
    with WS.use_workspace(alice):
        assert _resolve() == pytest.approx(DAX_A)
    assert solver == ["test-alice"]


def test_a_valid_disk_entry_is_served_without_a_solve(tmp_path, solver):
    alice = _ws(tmp_path, "alice")
    skey = "_".join(str(x) for x in F._daxis_topology_key(_P, GEO, WIND))
    (alice.root / ".daxis_cache.json").write_text(json.dumps({skey: 60.0125}))
    with WS.use_workspace(alice):
        assert _resolve() == pytest.approx(60.0125)
    assert solver == []


# ── materials ────────────────────────────────────────────────────────────────

_MAT_GEO = dict(num_slots=12, num_poles=14, stator_diameter=30.0,
                num_wires_per_slot=6, wire_parallel=1, wire_split=1)
_MAT_WIND = dict(layers=1, connection="2S", n_parallel=1)


def _keys(override):
    set_request_materials(override)
    try:
        return (F._daxis_topology_key(_P, _MAT_GEO, _MAT_WIND),
                F.psipm_cache_key(_MAT_GEO, _MAT_WIND,
                                  materials=F._noload_material_fingerprint()))
    finally:
        set_request_materials(None)


def _ov(**assignment):
    return {"assignment": dict(assignment), "materials": {}}


def test_psi_pm_key_follows_the_magnet_and_steel_cards_daxis_key_does_not():
    base = _keys(_ov(magnet="N52UH_150C", stator_core="B10AHV900M",
                     rotor_core="B10AHV900M"))
    magnet = _keys(_ov(magnet="F52SH_120C", stator_core="B10AHV900M",
                       rotor_core="B10AHV900M"))
    steel = _keys(_ov(magnet="N52UH_150C", stator_core="20SW1200",
                      rotor_core="20SW1200"))
    # θ* is material-invariant: one calibration serves every card set
    assert base[0] == magnet[0] == steel[0]
    # ψ_PM scales with Br and with the iron: each card set has its own entry
    assert len({base[1], magnet[1], steel[1]}) == 3
    # stable for the same cards
    assert _keys(_ov(magnet="N52UH_150C", stator_core="B10AHV900M",
                     rotor_core="B10AHV900M")) == base


def test_psi_pm_key_ignores_parts_the_no_load_field_cannot_see():
    a = _keys(_ov(magnet="N52UH_150C", slot_insulation="Nomex"))
    b = _keys(_ov(magnet="N52UH_150C", slot_insulation="Al2O3"))
    assert a == b


def test_psi_pm_key_follows_the_props_not_just_the_name():
    """A user's own card keeps its name when its B-H curve is edited."""
    card = {"category": "magnet", "Br": 1.20, "Hc": 900e3, "mu_r": 1.05}
    a = _keys({"assignment": {"magnet": "my_magnet"},
               "materials": {"my_magnet": card}})
    b = _keys({"assignment": {"magnet": "my_magnet"},
               "materials": {"my_magnet": dict(card, Br=1.30)}})
    assert a[0] == b[0]
    assert a[1] != b[1]


def test_psi_pm_disk_entry_of_other_cards_or_old_version_is_not_served(
        tmp_path, monkeypatch):
    class _Intercepted(BaseException):
        pass

    def _intercept(**kw):
        raise _Intercepted

    disk = tmp_path / "daxis.json"
    monkeypatch.setattr(F, "_daxis_disk_path", lambda: str(disk))
    monkeypatch.setattr(F, "em_transient_eval", _intercept)
    n52 = _ov(magnet="N52UH_150C")
    set_request_materials(n52)
    try:
        k_n52 = F.psipm_cache_key(_MAT_GEO, _MAT_WIND,
                                  materials=F._noload_material_fingerprint())
        v2 = k_n52.replace("psipm_v3_", "psipm_v2_").rsplit("_", 1)[0]
    finally:
        set_request_materials(None)
    ldq = {"Ld_mH": 0.1, "Lq_mH": 0.2}
    disk.write_text(json.dumps({k_n52: [2.0e-4, 1e-9], v2: [9.0, 9.0],
                                "ldq0_v1_%s_M20" % k_n52: ldq}))

    set_request_materials(n52)
    try:                      # the same cards: served, no solve
        assert F.noload_psi_pm(_MAT_GEO, _MAT_WIND, 7, 2, 60.) == (2.0e-4, 1e-9)
        assert F.noload_incremental_ldq(_MAT_GEO, _MAT_WIND, 7, 60.) == ldq
    finally:
        set_request_materials(None)
    set_request_materials(_ov(magnet="F52SH_120C"))
    try:                      # another magnet: a miss, it solves its own
        with pytest.raises(_Intercepted):
            F.noload_psi_pm(_MAT_GEO, _MAT_WIND, 7, 2, 60.)
        with pytest.raises(_Intercepted):   # and so does the catalogue Ld/Lq
            F.noload_incremental_ldq(_MAT_GEO, _MAT_WIND, 7, 60.)
    finally:
        set_request_materials(None)


def test_material_fingerprint_never_raises(monkeypatch):
    import motor_ai_sim.materials as ML

    def _boom(*a, **k):
        raise RuntimeError("library unavailable")

    monkeypatch.setattr(ML, "resolve_assigned", _boom)
    a = _keys(_ov(magnet="N52UH_150C"))[1]
    b = _keys(_ov(magnet="F52SH_120C"))[1]
    assert a != b                 # the names alone still separate them
