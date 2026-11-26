# -*- coding: utf-8 -*-
"""The canonical machine fingerprint (2026-11-25): a geometry key added after the 2026-10-20 baseline, at its default value, does
not change the canonical print (``routes.simulation._geometry_fingerprints()[0]``); the legacy49 print (every key) stays available as an alias that every reader
of a stored fingerprint accepts; a non-default value and a real mismatch still move it."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from motor_ai_sim import geo_fingerprint as GF
from motor_ai_sim.routes import simulation as S

DEFAULTS = {"wire_omit": 0.0, "k_end_by_turns": 0.0, "magnet_flat": 0.0, "magnet_pocket_clearance": 0.0}


def _canonical(ov=None) -> str:
    """The canonical print (what writers store from stage 2 on, ``geo_fingerprint.WRITE_CANONICAL``)."""
    return S._geometry_fingerprints(ov)[0]


def _md5(d) -> str:
    return hashlib.md5(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]


class _Geo:
    def __init__(self, d):
        self._d = dict(d)

    def to_dict(self):
        return dict(self._d)


@pytest.fixture()
def machine(monkeypatch):
    """The repo machine as the 2026-10-20 code saw it (no post-baseline key) and a setter for the live object / config block."""
    from motor_ai_sim import config as C
    from motor_ai_sim.services import geometry_service as GS
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override
    base = dict(C.get_config()["geometry"])
    for k in DEFAULTS:
        base.pop(k, None)
    base = merge_geo_override(dict(base), dict(base))        # a self-consistent machine (the stored derived copies may lag the primaries)
    state = {"g": dict(base), "glive": dict(base)}
    real = C.get_config()
    monkeypatch.setattr(C, "get_config", lambda *a, **k: {**real, "geometry": state["g"]})
    monkeypatch.setattr(GS, "get_current_geometry", lambda *a, **k: _Geo(state["glive"]))
    return base, state


def test_a_machine_at_defaults_has_the_fingerprint_it_had_before_the_four_keys(machine):
    base, state = machine
    old = _md5({"g": base, "glive": base})                                   # the 2026-10-20 function, from the pre-change key set
    assert len(base) >= 44 and S._geometry_fingerprint_impl(None, False) == old
    state["g"] = {**base, **DEFAULTS}
    state["glive"] = {**base, **DEFAULTS}                                    # what the 2026-11-15 code hashes (49 keys on a catalogue machine)
    legacy49 = _md5({"g": state["g"], "glive": state["glive"]})
    assert S._geometry_fingerprint_legacy(None) == legacy49 and legacy49 != old
    assert _canonical(None) == old                              # canonical == the old fingerprint
    # defaults spelt differently (None, "", 0, False) are the same machine
    state["glive"] = {**base, "wire_omit": None, "k_end_by_turns": 0, "magnet_flat": False, "magnet_pocket_clearance": ""}
    assert _canonical(None) == old


def test_the_alias_is_accepted_both_ways(machine):
    base, state = machine
    state["g"], state["glive"] = {**base, **DEFAULTS}, {**base, **DEFAULTS}
    canon, legacy = S._geometry_fingerprints(None)
    assert canon != legacy
    assert GF.same(legacy, canon) and GF.same(canon, legacy) and GF.same(canon, canon)
    assert GF.accepted(canon)[0] == canon and legacy in GF.accepted(canon)    # other spellings of the defaults may add more aliases
    assert not GF.same("0123456789abcdef", canon) and not GF.same(None, canon) and not GF.same(canon, None)
    GF._BOOK.clear()                                                           # a process that has not fingerprinted yet resolves the live machine
    assert GF.same(legacy, canon)


def test_wire_bore_margin_unset_is_dropped_and_any_value_counts(machine):
    base, state = machine
    old = _canonical(None)
    state["glive"] = {**base, "wire_bore_margin": None}
    assert _canonical(None) == old
    state["glive"] = {**base, "wire_bore_margin": 0.0}                       # a stated zero margin is a statement, not "unset"
    assert _canonical(None) != old


def test_a_non_default_new_key_changes_the_canonical_fingerprint(machine):
    base, state = machine
    old = _canonical(None)
    for k, v in (("magnet_pocket_clearance", 0.035), ("magnet_flat", 1), ("wire_omit", 2), ("k_end_by_turns", 1)):
        state["glive"] = {**base, **DEFAULTS, k: v}
        state["g"] = {**base, **DEFAULTS, k: v}
        assert _canonical(None) != old, k
        assert GF.canonical_geometry(state["glive"])[k] == v


def test_a_real_value_mismatch_still_mismatches(machine):
    base, state = machine
    a = _canonical(None)
    state["glive"] = {**base, "stator_diameter": float(base["stator_diameter"]) + 1.0}
    assert _canonical(None) != a
    canon, legacy = S._geometry_fingerprints(None)
    assert not GF.same(a, canon) and not GF.same(a, legacy)


def test_the_geo_override_path_is_consistent(machine):
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override
    base, state = machine
    state["g"], state["glive"] = {**base, **DEFAULTS}, {**base, **DEFAULTS}
    ov = {"tooth_width": float(base["tooth_width"]) + 0.2}
    expected_old = _md5({"g": base, "glive": merge_geo_override(dict(base), dict(ov))})   # the 2026-10-20 function on the override
    assert _canonical(ov) == expected_old
    assert S._geometry_fingerprint_legacy(ov) != expected_old
    # ov == live resolves to the no-override fingerprint (canonical AND legacy)
    full = {**base, **DEFAULTS}
    assert _canonical(full) == _canonical(None)
    assert S._geometry_fingerprint_legacy(full) == S._geometry_fingerprint_legacy(None)
    # an override carrying a default key resolves like one without it; a non-default one is another machine
    assert _canonical({**ov, "magnet_flat": 0}) == expected_old
    assert _canonical({**ov, "magnet_pocket_clearance": 0.035}) != expected_old
    assert GF.same(S._geometry_fingerprint_legacy(ov), _canonical(ov), ov)


def test_a_new_geometry_key_must_be_registered_on_purpose():
    """The tripwire: the live geometry object (repo configuration) and every schema / free key may carry only the baseline keys
    and the registered post-baseline keys.  A new key fails here until it is added to ``geo_fingerprint`` (default-dropped, with an
    ``ABSENT_MEANS`` default) or to ``KNOWN_POST_BASELINE`` (hashed whenever present)."""
    from motor_ai_sim.config import get_config
    from motor_ai_sim.routes._validation import SCHEMA_FALLBACK
    from motor_ai_sim.routes.family import FREE_GEO_KEYS
    from motor_ai_sim.services.geometry_service import get_current_geometry
    known = set(GF.BASELINE_KEYS) | set(GF.POST_BASELINE_KEYS) | set(GF.KNOWN_POST_BASELINE)
    live = set(get_current_geometry().to_dict())
    assert live <= known, sorted(live - known)
    assert set(get_config()["geometry"]) <= known, sorted(set(get_config()["geometry"]) - known)
    assert set(FREE_GEO_KEYS) <= known, sorted(set(FREE_GEO_KEYS) - known)
    assert set(GF.POST_BASELINE_KEYS) <= set(GF.DEFAULT_OF)
    from motor_ai_sim.routes import family
    assert family._ABSENT_MEANS is GF.ABSENT_MEANS                           # ONE table
    unknown_schema = {k for k in SCHEMA_FALLBACK if k not in known}
    assert not unknown_schema, sorted(unknown_schema)


def test_the_end_effect_passport_stored_under_the_legacy_print_is_found(machine, monkeypatch):
    from motor_ai_sim import end3d_store
    base, state = machine
    state["g"], state["glive"] = {**base, **DEFAULTS}, {**base, **DEFAULTS}
    canon, legacy = S._geometry_fingerprints(None)
    rec = {"k_flux": 0.93, "machine": "x", "stack_mm": 40.0}
    monkeypatch.setattr(end3d_store, "load", lambda: {legacy: dict(rec)})
    got = S._end3d_lookup(canon, None, exact=True)
    assert got is not None and got.get("k_flux") == 0.93
    monkeypatch.setattr(end3d_store, "load", lambda: {"0123456789abcdef": dict(rec)})
    assert S._end3d_lookup(canon, None, exact=True) is None


def test_the_bench_ldq_stored_under_the_legacy_print_is_found(machine, monkeypatch, tmp_path):
    base, state = machine
    state["g"], state["glive"] = {**base, **DEFAULTS}, {**base, **DEFAULTS}
    canon, legacy = S._geometry_fingerprints(None)
    p = tmp_path / ".bench_ldq.json"
    p.write_text(json.dumps({S._bench_key(legacy, "4P"): {"Ld_mH": 1.0, "Lq_mH": 2.0}}), encoding="utf-8")
    monkeypatch.setattr(S, "_bench_cache_path", lambda: p)
    assert S._bench_read(canon, "4P") == {"Ld_mH": 1.0, "Lq_mH": 2.0}
    assert S._bench_read("0123456789abcdef", "4P") is None


def test_the_stored_comparisons_of_the_readers_accept_the_alias(machine):
    from motor_ai_sim.routes.static3d import _stale_block
    base, state = machine
    state["g"], state["glive"] = {**base, **DEFAULTS}, {**base, **DEFAULTS}
    canon, legacy = S._geometry_fingerprints(None)
    assert _stale_block(canon, legacy)["stale_geometry"] is False
    assert _stale_block(canon, canon)["stale_geometry"] is False
    assert _stale_block(canon, "0123456789abcdef")["stale_geometry"] is True
    assert _stale_block(canon, None)["stale_geometry"] is None


# ── the read-only list script ───────────────────────────────────────────────
def test_the_match_list_script_counts_and_writes_nothing(tmp_path, machine, capsys):
    import importlib.util
    import yaml
    base, state = machine
    root = tmp_path / "dies"
    (root / "D1").mkdir(parents=True)
    die_geo = {**base, **DEFAULTS}
    (root / "D1" / "die.yaml").write_text(yaml.safe_dump({"name": "D1", "geometry": die_geo}), encoding="utf-8")
    geo = {**die_geo, "motor_length": 55.0}
    canon, legacy = S._geometry_fingerprints(geo)
    assert canon != legacy
    (root / "D1" / "L55.yaml").write_text(yaml.safe_dump({
        "name": "L55", "geometry_overrides": {"motor_length": 55.0},
        "duties": [{"name": "a", "result": {"geometry_fingerprint": canon}},
                   {"name": "b", "result": {"geo_fingerprint": legacy}},
                   {"name": "c", "result": {"geometry_fingerprint": "0123456789abcdef"}}]}), encoding="utf-8")
    store = tmp_path / "passports.json"
    store.write_text(json.dumps({canon: {}, legacy: {}, "fedcba9876543210": {}}), encoding="utf-8")
    snap = {p: p.read_bytes() for p in sorted(tmp_path.rglob("*")) if p.is_file()}
    dirs = sorted(p for p in tmp_path.rglob("*") if p.is_dir())
    spec = importlib.util.spec_from_file_location("gfml", Path(__file__).resolve().parents[1] / "scripts" / "geo_fingerprint_match_list.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["--root", str(root), "--store", str(store)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["machines"] == 1 and out["records"] == {"canonical": 1, "alias": 1, "neither": 1}
    assert out["stores"][str(store)] == {"keys": 3, "canonical": 1, "alias": 1, "neither": 1}
    assert {p: p.read_bytes() for p in sorted(tmp_path.rglob("*")) if p.is_file()} == snap     # byte-identical, nothing created
    assert sorted(p for p in tmp_path.rglob("*") if p.is_dir()) == dirs
    assert mod.main(["--root", str(tmp_path / "nope")]) == 2


# ── review of #891 (2026-11-26): one snapshot, staged writer, symmetric readers, the measured k_T reader ─────────────────────
def test_stage_1_writers_keep_the_print_of_the_build_before(machine, monkeypatch):
    """Stage 1: ``_geometry_fingerprint`` (what every writer stores) is bit-identical to the function before this PR (every key
    hashed), so a process of the previous build reads what this one writes; stage 2 switches to the canonical print."""
    base, state = machine
    state["g"], state["glive"] = {**base, **DEFAULTS}, {**base, **DEFAULTS}
    before = _md5({"g": state["g"], "glive": state["glive"]})
    assert GF.WRITE_CANONICAL is False
    assert S._geometry_fingerprint(None) == before == S._geometry_fingerprint_legacy(None)
    ov = {"tooth_width": float(base["tooth_width"]) + 0.2}
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override
    assert S._geometry_fingerprint(ov) == _md5({"g": state["g"], "glive": merge_geo_override(dict(state["glive"]), dict(ov))})
    monkeypatch.setattr(GF, "WRITE_CANONICAL", True)
    assert S._geometry_fingerprint(None) == _canonical(None) != before


def test_both_prints_come_from_one_snapshot(machine, monkeypatch):
    """A geometry change between the two hashes must not link canonical(A) to legacy(B) (review P1 #1)."""
    from motor_ai_sim.services import geometry_service as GS
    base, state = machine
    a = {**base, **DEFAULTS, "stator_diameter": 80.0}
    b = {**base, **DEFAULTS, "stator_diameter": 120.0}
    state["g"], state["glive"] = dict(a), dict(a)
    canon_a, legacy_a = S._geometry_fingerprints(None)
    state["glive"] = dict(b)
    canon_b, legacy_b = S._geometry_fingerprints(None)
    GF._BOOK.clear()
    reads = iter([a, b, b, b, b, b])
    monkeypatch.setattr(GS, "get_current_geometry", lambda *x, **k: _Geo(next(reads)))
    got = S._geometry_fingerprints(None)                                   # ONE read of the live object: machine A
    assert got == (canon_a, legacy_a)
    assert not GF.same(legacy_b, canon_a) and not GF.same(canon_a, legacy_b)
    assert legacy_b not in GF.accepted(canon_a) and canon_b != canon_a


def test_the_alias_book_is_symmetric(machine):
    base, state = machine
    state["g"], state["glive"] = {**base, **DEFAULTS}, {**base, **DEFAULTS}
    canon, legacy = S._geometry_fingerprints(None)
    assert canon in GF.accepted(legacy) and legacy in GF.accepted(canon)
    assert canon in GF.aliases(legacy) and legacy in GF.aliases(canon)
    GF._BOOK.clear()
    assert canon in GF.accepted(legacy)                                    # resolved from the live machine in a fresh process


def _kT_record(fp):
    return {"fingerprint": fp, "k_T": 0.83, "slots": 12, "poles": 14, "od_mm": 80.0, "stack_mm": 40.0, "source": "synthetic"}


@pytest.mark.parametrize("stored_as", ["legacy", "canonical"])
def test_a_measured_k_T_keyed_by_either_print_is_an_exact_match(machine, monkeypatch, stored_as):
    """Review P1 #2: ``end3d_factors.measured_k_T`` matches a record keyed by the equivalent print, without geometry."""
    from motor_ai_sim import end3d_factors as F
    base, state = machine
    state["g"], state["glive"] = {**base, **DEFAULTS}, {**base, **DEFAULTS}
    canon, legacy = S._geometry_fingerprints(None)
    stored, asked = (legacy, canon) if stored_as == "legacy" else (canon, legacy)
    monkeypatch.setattr(F, "_records", lambda: [_kT_record(stored)])
    for book_known in (True, False):
        if not book_known:
            GF._BOOK.clear()
        m = F.measured_k_T(asked)
        assert m and m["k_T"] == 0.83 and m["inherited"] is False, (stored_as, book_known)
    assert F.measured_k_T("0123456789abcdef") is None


def test_the_enrich_path_keeps_the_measured_k_T_of_a_legacy_record(machine, monkeypatch):
    """``_end3d_lookup`` finds the legacy passport AND ``enrich`` keeps the exact measured k_T recorded under the legacy print."""
    from motor_ai_sim import end3d_factors as F
    from motor_ai_sim import end3d_store
    base, state = machine
    state["g"], state["glive"] = {**base, **DEFAULTS}, {**base, **DEFAULTS}
    canon, legacy = S._geometry_fingerprints(None)
    monkeypatch.setattr(F, "_records", lambda: [_kT_record(legacy)])
    monkeypatch.setattr(end3d_store, "load", lambda: {legacy: {"k_flux": 0.93, "machine": "x", "stack_mm": 40.0}})
    for live in (canon, legacy):
        GF._BOOK.clear()
        got = S._end3d_lookup(live, None, exact=True)
        assert got is not None and got.get("k_flux") == 0.93
        assert got.get("k_T") == 0.83 and not got.get("k_T_inherited"), live
        assert F.torque_factor(got) == (0.83, F.BASIS_MEASURED)


# ── review r2 of #891: a stable winner between equivalent records ───────────────────────────────────────────────────────
@pytest.mark.parametrize("order", ["canonical_first", "legacy_first"])
def test_measured_k_T_keeps_literal_priority_between_equivalent_records(machine, monkeypatch, order):
    """Records under BOTH prints (canonical 0.71, legacy 0.83): the queried print's own record wins (the base answer), in
    either file order; the equivalent one is used only when no literal record exists."""
    from motor_ai_sim import end3d_factors as F
    base, state = machine
    state["g"], state["glive"] = {**base, **DEFAULTS}, {**base, **DEFAULTS}
    canon, legacy = S._geometry_fingerprints(None)
    rc, rl = dict(_kT_record(canon), k_T=0.71), dict(_kT_record(legacy), k_T=0.83)
    monkeypatch.setattr(F, "_records", lambda: [rc, rl] if order == "canonical_first" else [rl, rc])
    assert F.measured_k_T(legacy)["k_T"] == 0.83
    assert F.measured_k_T(canon)["k_T"] == 0.71
    monkeypatch.setattr(F, "_records", lambda: [rc])
    assert F.measured_k_T(legacy)["k_T"] == 0.71                            # no literal record: the equivalent one


def test_the_passport_answer_does_not_depend_on_which_print_is_asked(machine, monkeypatch, tmp_path):
    """The store's rule (workspace over shared) holds across the alias group, so the answer stays the same when writers later
    switch from legacy49 to canonical; inside one layer the literal print wins."""
    from motor_ai_sim import end3d_factors as F
    from motor_ai_sim import end3d_store
    base, state = machine
    state["g"], state["glive"] = {**base, **DEFAULTS}, {**base, **DEFAULTS}
    canon, legacy = S._geometry_fingerprints(None)
    monkeypatch.setattr(F, "_records", lambda: [])
    sh, ws = tmp_path / "shared.json", tmp_path / "ws.json"
    monkeypatch.setattr(end3d_store, "shared_path", lambda: sh)
    monkeypatch.setattr(end3d_store, "workspace_path", lambda: ws)
    sh.write_text(json.dumps({canon: {"k_flux": 0.75, "machine": "x", "stack_mm": 40.0}}), encoding="utf-8")
    ws.write_text(json.dumps({legacy: {"k_flux": 0.93, "machine": "x", "stack_mm": 40.0}}), encoding="utf-8")
    for live in (legacy, canon):
        for book_known in (True, False):
            if not book_known:
                GF._BOOK.clear()
            got = S._end3d_lookup(live, None, exact=True)
            assert got is not None and got["k_flux"] == 0.93, (live, book_known)
    # the same pair in ONE layer: the literal print wins (the base answer for each print)
    ws.write_text("{}", encoding="utf-8")
    sh.write_text(json.dumps({canon: {"k_flux": 0.75, "machine": "x", "stack_mm": 40.0},
                              legacy: {"k_flux": 0.93, "machine": "x", "stack_mm": 40.0}}), encoding="utf-8")
    assert S._end3d_lookup(legacy, None, exact=True)["k_flux"] == 0.93
    assert S._end3d_lookup(canon, None, exact=True)["k_flux"] == 0.75
    # shared only, under the other print: found
    sh.write_text(json.dumps({canon: {"k_flux": 0.75, "machine": "x", "stack_mm": 40.0}}), encoding="utf-8")
    assert S._end3d_lookup(legacy, None, exact=True)["k_flux"] == 0.75
