"""Calibration cache identity follows the resolved winding, without FEM solves."""
import copy
import json
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

from motor_ai_sim import config
from motor_ai_sim.simulation import fem_solver_2d as fem
from motor_ai_sim.simulation.geometry_2d import build_winding_layout, merge_geo_override


GEO = dict(num_slots=12, num_poles=14, stator_diameter=30., num_wires_per_slot=6,
           wire_parallel=1, wire_split=1)
PARAMS = SimpleNamespace(num_poles=14)
WIND = dict(layers=1, connection="2S", n_parallel=1)
LAYOUT = build_winding_layout(12, 7)


def spelling(layout, separator="|"):
    return separator.join(phase if sign == 1 else phase.lower() for phase, sign in layout)


def other_winding():
    # A phase relabeling changes the reference of phase-A flux while retaining
    # valid coil pairs, phase balance, slot/pole counts and sector admissibility.
    phase_map = {"A": "B", "B": "C", "C": "A"}
    return dict(WIND, layout=spelling([(phase_map[p], s) for p, s in LAYOUT]))


@pytest.fixture(autouse=True)
def isolated_memory_cache(monkeypatch):
    monkeypatch.setattr(fem, "_DAXIS_CACHE", {})


@pytest.mark.parametrize("layout", [
    [(p, -s) for p, s in LAYOUT], LAYOUT[2:]+LAYOUT[:2],
    [(dict(A="B", B="C", C="A")[p], s) for p, s in LAYOUT]])
def test_physically_changed_phase_or_direction_separates_both_keys(layout):
    changed = dict(WIND, layout=spelling(layout))
    assert fem._daxis_topology_key(PARAMS, GEO, WIND) != fem._daxis_topology_key(PARAMS, GEO, changed)
    assert fem.psipm_cache_key(GEO, WIND) != fem.psipm_cache_key(GEO, changed)


@pytest.mark.parametrize("separator", ["|", ",", " ", " | "])
def test_automatic_and_equivalent_explicit_spelling_share_identity(separator):
    explicit = dict(WIND, layout=spelling(LAYOUT, separator))
    assert fem._daxis_topology_key(PARAMS, GEO, WIND) == fem._daxis_topology_key(PARAMS, GEO, explicit)
    assert fem.psipm_cache_key(GEO, WIND) == fem.psipm_cache_key(GEO, explicit)


def test_wrong_length_uses_same_automatic_fallback_as_domain_builder():
    incomplete = dict(WIND, layout="A|b")
    assert fem._winding_cache_identity(GEO, WIND) == fem._winding_cache_identity(GEO, incomplete)


def test_counts_are_request_local_and_segment_form_is_supported(monkeypatch):
    geo, wind = copy.deepcopy(GEO), other_winding()
    before = copy.deepcopy((geo, wind))
    def forbidden(*args, **kwargs):
        pytest.fail("cache identity must not consult or mutate global configuration")
    monkeypatch.setattr(config, "get_config", forbidden)
    token = fem._winding_cache_identity(geo, wind)
    segmented = {k: v for k, v in geo.items() if k not in ("num_slots", "num_poles")}
    segmented.update(num_seg=2, num_slots_per_segment=6, num_poles_per_segment=7)
    assert fem._winding_cache_identity(segmented, wind) == token
    assert fem._winding_cache_identity(dict(geo, num_poles=28), wind, num_poles=14) == token
    fem._daxis_topology_key(PARAMS, geo, wind)
    fem.psipm_cache_key(geo, wind)
    assert (geo, wind) == before


def test_existing_geometry_layers_connection_and_scale_identity_is_preserved():
    daxis = fem._daxis_topology_key(PARAMS, GEO, WIND)
    pm = fem.psipm_cache_key(GEO, WIND)
    for changed in (dict(WIND, layers=2), dict(WIND, connection="2P")):
        assert fem._daxis_topology_key(PARAMS, GEO, changed) != daxis
        assert fem.psipm_cache_key(GEO, changed) != pm
    assert fem._daxis_topology_key(PARAMS, dict(GEO, air_gap=.4), WIND) != daxis
    assert fem.psipm_cache_key(dict(GEO, air_gap=.4), WIND) != pm
    assert fem._daxis_topology_key(PARAMS, dict(GEO, wire_parallel=3), WIND) == daxis
    assert fem.psipm_cache_key(dict(GEO, wire_parallel=3), WIND) != pm
    assert fem.psipm_cache_key(dict(GEO, wire_split=2), WIND) != pm
    assert fem.psipm_cache_key(GEO, WIND, connection="2P") != pm


def legacy_daxis_key(geo=GEO, wind=WIND):
    return (14, 12, wind["layers"], wind["connection"], fem._daxis_geo_fingerprint(geo))


def test_memory_misses_other_winding_and_legacy_then_reuses_same_actual_basis(monkeypatch):
    changed = other_winding()
    fem._DAXIS_CACHE[fem._daxis_topology_key(PARAMS, GEO, WIND)] = 20.
    fem._DAXIS_CACHE[legacy_daxis_key()] = 33.
    calls = []
    def calibrate(p, geo, wind, pole_pairs, override, sectors, key, fingerprint, progress_cb):
        calls.append(key)
        fem._DAXIS_CACHE[key] = 77.
        return 77.
    monkeypatch.setattr(fem, "_calibrate_daxis", calibrate)
    assert fem._resolve_daxis_shift(PARAMS, GEO, changed, 7, None, 2) == 77.
    equivalent = dict(changed, layout=changed["layout"].replace("|", ", "))
    assert fem._resolve_daxis_shift(PARAMS, GEO, equivalent, 7, None, 2) == 77.
    assert len(calls) == 1


class SolveIntercepted(BaseException):
    pass


@pytest.mark.parametrize("matching_entry", [False, True])
def test_daxis_disk_separates_winding_and_ignores_legacy_without_deletion(monkeypatch, tmp_path, matching_entry):
    changed = other_winding()
    disk = tmp_path/"daxis.json"
    entries = {"_".join(map(str, legacy_daxis_key())): 33.,
               "_".join(map(str, fem._daxis_topology_key(PARAMS, GEO, WIND))): 20.}
    if matching_entry:
        entries["_".join(map(str, fem._daxis_topology_key(PARAMS, GEO, changed)))] = 77.
    disk.write_text(json.dumps(entries), encoding="utf-8")
    before = disk.read_bytes()
    monkeypatch.setattr(fem, "_daxis_disk_path", lambda: str(disk))
    def intercept(**kwargs):
        raise SolveIntercepted
    monkeypatch.setattr(fem, "em_transient_eval", intercept)
    if matching_entry:
        assert fem._resolve_daxis_shift(PARAMS, GEO, changed, 7, None, 2) == 77.
    else:
        with pytest.raises(SolveIntercepted):
            fem._resolve_daxis_shift(PARAMS, GEO, changed, 7, None, 2)
    assert disk.read_bytes() == before


@pytest.mark.parametrize("matching_entry", [False, True])
def test_psi_pm_disk_separates_winding_and_ignores_legacy_without_deletion(monkeypatch, tmp_path, matching_entry):
    changed = other_winding()
    disk = tmp_path/"daxis.json"
    old = f"psipm_{fem._daxis_geo_fingerprint(GEO)}_L1_C2S_T6P1"
    entries = {old: [33., 34.], fem.psipm_cache_key(GEO, WIND): [20., 21.]}
    if matching_entry:
        entries[fem.psipm_cache_key(GEO, changed)] = [77., 78.]
    disk.write_text(json.dumps(entries), encoding="utf-8")
    before = disk.read_bytes()
    monkeypatch.setattr(fem, "_daxis_disk_path", lambda: str(disk))
    def intercept(**kwargs):
        raise SolveIntercepted
    monkeypatch.setattr(fem, "em_transient_eval", intercept)
    if matching_entry:
        assert fem.noload_psi_pm(GEO, changed, 7, 2, 60.) == (77., 78.)
    else:
        with pytest.raises(SolveIntercepted):
            fem.noload_psi_pm(GEO, changed, 7, 2, 60.)
    assert disk.read_bytes() == before


@pytest.mark.parametrize("storage", ["memory", "disk"])
@pytest.mark.parametrize("identity", ["same", "different", "legacy"])
def test_manual_pin_guard_only_uses_same_versioned_winding(monkeypatch, tmp_path, storage, identity):
    regression = runpy.run_path(str(Path(__file__).with_name("test_physics_regression.py")))
    geo_override = regression["GEO_30MM"]
    original_get = config.get_config
    wind = other_winding()
    def isolated_config(*args, **kwargs):
        value = copy.deepcopy(original_get(*args, **kwargs))
        value["winding"] = dict(value.get("winding", {}), **wind)
        return value
    monkeypatch.setattr(config, "get_config", isolated_config)
    geo = merge_geo_override(isolated_config()["geometry"], geo_override)
    cached_wind = wind if identity == "same" else WIND
    # Retain the prior policy: manual pins may be compared across cross-sections
    # but only within the same certified winding identity.
    cached_geo = dict(geo, stator_diameter=31.)
    key = fem._daxis_topology_key(PARAMS, cached_geo, cached_wind)
    if identity == "legacy":
        key = key[:4]+key[-1:]
    disk = tmp_path/"manual-daxis.json"
    if storage == "memory":
        fem._DAXIS_CACHE[key] = 120.
        monkeypatch.setattr(fem, "_daxis_disk_path", lambda: None)
    else:
        disk.write_text(json.dumps({"_".join(map(str, key)): 120.}), encoding="utf-8")
        monkeypatch.setattr(fem, "_daxis_disk_path", lambda: str(disk))
    def intercept(**kwargs):
        assert kwargs["daxis_deg"] == 60.
        raise SolveIntercepted
    monkeypatch.setattr(fem, "_Excitation", intercept)
    expected = pytest.raises(ValueError, match="d-axis pin") if identity == "same" else pytest.raises(SolveIntercepted)
    with expected:
        fem.fem_transient_sliding_band(geo_override=geo_override, n_sectors=1, daxis_deg=60.,
                                      rpm=15000., I_phase_rms=0., connection="2S", n_parallel=1,
                                      eddy=False, rotor_eddy=False)
