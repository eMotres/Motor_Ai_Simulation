"""Stale DERIVED geometry fields in stored dies (2026-09-30).

die.yaml stores the derived radii (stator_inner_radius, rotor_outer_radius,
rotor_inner_radius, …) next to the inputs they are computed from, and on most
dies the copies were stale: CIANO14 40 new stored 12.1 / 11.9 / 4.8 for inputs
that give 12.0 / 11.8 / 5.0.  The duty-load guard compared the duty's stamp
(fresh values, from the live geometry object) with those stale copies and
answered 409 "the duty was saved on a different geometry" for an unchanged
machine.

Pinned here:
  * a refresh gives the radii of the inputs, and is byte-identical on a
    consistent document (so no hash taken over one moves);
  * the guard compares INPUTS: no false 409, a real input change still 409s,
    and slot_hs (never read by the builder) is not a difference;
  * the lock check does not refuse a derived key;
  * the payload / save refresh is ON by default (MOTOR_AI_SIM_FRESH_DERIVED=0
    turns it off, and then nothing the fingerprints hash changes);
  * build_sig and _geometry_fingerprint are unchanged for a consistent die;
  * the migration script: dry-run writes nothing, --apply fixes the file
    (CRLF kept, .bak written), a second run is a no-op.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from motor_ai_sim.geometry import motor_geometry as mg
from motor_ai_sim.routes import family as fam

REPO = Path(__file__).resolve().parents[1]

DIE = "CIANO14 40 T"
CFG = "L12"

#: The CIANO14 40 new die as stored on 2026-09-30: inputs + STALE derived.
INPUTS = {
    "stator_diameter": 40, "slot_height": 6.1, "core_thickness": 1.9,
    "num_seg": 2, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.2, "tooth_width": 3.3, "tooth2_width": 1.7, "cut_width": 1,
    "insulation_thickness": 0.06, "wire_width": 2.5, "wire_height": 0.6,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.1, "num_wires_per_slot": 7,
    "wire_split": 1, "slot_hs": 0.267, "magnet_height": 5.7,
    "rotor_house_height": 1.1, "shaft_height": 2, "motor_length": 12,
}
STALE = {
    **INPUTS,
    "stator_outer_radius": 20, "stator_inner_radius": 12.100000000000001,
    "num_slots": 12, "num_poles": 14, "angle_slot": 30.0,
    "angle_pole": 25.714285714285715, "slot_pitch": 0.5235987755982988,
    "pole_pitch": 0.4487989505128276, "rotor_outer_radius": 11.900000000000002,
    "rotor_inner_radius": 4.800000000000002, "slot_width": 2.8200000000000003,
}


def _sig(g: dict) -> str:
    """The frontend's geoSignature: numeric fields, sorted, `k:v|…`."""
    from motor_ai_sim.routes.presets import _geo_sig
    return _geo_sig(g)


# ── the refresh itself ───────────────────────────────────────────────────────

def test_refresh_gives_the_radii_of_the_inputs():
    g = mg.refresh_derived_geometry(STALE)
    assert g["stator_inner_radius"] == pytest.approx(12.0)
    assert g["rotor_outer_radius"] == pytest.approx(11.8)
    assert g["rotor_inner_radius"] == pytest.approx(5.0)
    assert set(mg.stale_derived_fields(STALE)) == {
        "stator_inner_radius", "rotor_outer_radius", "rotor_inner_radius"}
    assert mg.stale_derived_fields(g) == {}
    assert STALE["stator_inner_radius"] == 12.100000000000001   # input untouched


def test_refresh_of_a_consistent_document_is_byte_identical():
    good = mg.refresh_derived_geometry(STALE)
    again = mg.refresh_derived_geometry(good)
    assert json.dumps(again, sort_keys=True) == json.dumps(good, sort_keys=True)
    # `20` stays the int it was written as — a re-spelling would move every
    # hash taken over the document
    assert type(again["stator_outer_radius"]) is int
    assert yaml.safe_dump(again, sort_keys=False) == yaml.safe_dump(good, sort_keys=False)


def test_refresh_never_adds_a_key():
    bare = {k: STALE[k] for k in ("stator_diameter", "core_thickness",
                                  "slot_height", "stator_inner_radius")}
    out = mg.refresh_derived_geometry(bare)
    assert set(out) == set(bare)
    assert out["stator_inner_radius"] == pytest.approx(12.0)


def test_stale_counts_follow_the_segment_form():
    g = dict(STALE, num_slots=24, num_poles=28, angle_slot=15.0)
    out = mg.refresh_derived_geometry(g)
    assert (out["num_slots"], out["num_poles"]) == (12, 14)
    assert out["angle_slot"] == pytest.approx(30.0)


# ── the guard ────────────────────────────────────────────────────────────────

@pytest.fixture
def dies(monkeypatch, tmp_path):
    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump(
        {"name": DIE, "locked": True, "geometry": dict(STALE)},
        sort_keys=False), encoding="utf-8")
    live = mg.refresh_derived_geometry(STALE)          # what the browser stamps
    moved = dict(live, slot_height=6.3)
    moved.update(mg.fresh_derived_values(moved))
    (root / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump({
        "name": CFG, "die": DIE, "geometry_overrides": {"motor_length": 12},
        "duties": [
            {"name": "peak", "mode": "motor", "summary": {"_geoSig": _sig(live)}},
            {"name": "hs", "mode": "motor",
             "summary": {"_geoSig": _sig(dict(live, slot_hs=0.3))}},
            {"name": "moved", "mode": "motor", "summary": {"_geoSig": _sig(moved)}},
        ]}, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setattr(fam, "_CTX_FILE", tmp_path / ".family_context.json")
    monkeypatch.setattr(fam, "_sync_mesh_config_from_duty", lambda c, n: False)
    monkeypatch.setenv(mg.FRESH_DERIVED_ENV, "0")
    return root


def test_equal_inputs_stale_die_copies_no_409(dies):
    before = (dies / DIE / "die.yaml").read_bytes()
    out = fam.activate(fam.Activate(die=DIE, config=CFG, duty="peak"), _w={})
    assert out["ok"] and out["geometry_diffs"] == []
    assert (dies / DIE / "die.yaml").read_bytes() == before


def test_slot_hs_is_never_a_difference(dies):
    out = fam.activate(fam.Activate(die=DIE, config=CFG, duty="hs"), _w={})
    assert out["ok"] and out["geometry_diffs"] == []


def test_a_real_input_difference_still_409s(dies):
    with pytest.raises(HTTPException) as ei:
        fam.activate(fam.Activate(die=DIE, config=CFG, duty="moved"), _w={})
    assert ei.value.status_code == 409
    keys = {x["key"]: x for x in ei.value.detail["diffs"]}
    assert set(keys) == {"slot_height"}        # its derived radii are not listed
    assert keys["slot_height"]["scope"] == "die"
    assert keys["slot_height"]["live"] == 6.1 and keys["slot_height"]["duty"] == 6.3


def test_lock_check_ignores_derived_keys_but_not_inputs(dies, tmp_path):
    (tmp_path / ".family_context.json").write_text(
        json.dumps({"die": DIE, "config": CFG}), encoding="utf-8")
    fresh = mg.refresh_derived_geometry(STALE)
    assert fam.geometry_lock_check(fresh) is None          # same machine
    ref = fam.geometry_lock_check(dict(fresh, slot_height=6.3))
    assert ref and [x["field"] for x in ref["invalid_parameters"]] == ["slot_height"]


# ── the write-side switch, and the prints ────────────────────────────────────

def test_payload_is_unchanged_with_the_switch_off(dies):
    p = fam.payload(DIE, CFG)                     # fixture: switch = 0
    assert p["geometry"]["stator_inner_radius"] == 12.100000000000001


def test_payload_and_save_refresh_with_the_switch_on(dies, monkeypatch):
    monkeypatch.delenv(mg.FRESH_DERIVED_ENV)      # unset = the default: ON
    assert mg.fresh_derived_on_write()
    p = fam.payload(DIE, CFG)
    assert p["geometry"]["stator_inner_radius"] == pytest.approx(12.0)
    d = fam._load_yaml(fam._die_file(DIE), "die")
    fam._save_yaml(fam._die_file(DIE), d)
    saved = yaml.safe_load((dies / DIE / "die.yaml").read_text(encoding="utf-8"))
    assert saved["geometry"]["rotor_inner_radius"] == pytest.approx(5.0)


def test_build_sig_unchanged_for_a_consistent_die():
    good = {"geometry": mg.refresh_derived_geometry(STALE)}
    cfg = {"geometry_overrides": {"motor_length": 12}, "winding": {}, "materials": {}}
    again = {"geometry": mg.refresh_derived_geometry(good["geometry"])}
    assert fam._build_sig(good, cfg) == fam._build_sig(again, cfg)


def test_geometry_fingerprint_unchanged_for_a_consistent_block(monkeypatch, tmp_path):
    """`_geometry_fingerprint` (the passport / bench Ld/Lq key) hashes the raw
    motor_config.yaml block: refreshing a CONSISTENT block must leave it — and
    so the print — exactly as it was."""
    import motor_ai_sim.config as C
    from motor_ai_sim.routes.simulation import _geometry_fingerprint
    from motor_ai_sim.services import geometry_service as gs

    base = yaml.safe_load(Path(C.config_path()).read_text(encoding="utf-8"))
    base["geometry"] = mg.refresh_derived_geometry(base.get("geometry") or {})
    cfg_file = tmp_path / "motor_config.yaml"

    def _fp(cfg: dict) -> str:
        cfg_file.write_text(yaml.dump(cfg, allow_unicode=True,
                                      default_flow_style=False, sort_keys=False),
                            encoding="utf-8")
        C.clear_config_cache()
        gs.get_current_geometry(reload=True)
        return _geometry_fingerprint(None)

    monkeypatch.setattr(C, "_active_config_path", lambda: cfg_file)
    try:
        a = _fp(base)
        b = _fp(dict(base, geometry=mg.refresh_derived_geometry(base["geometry"])))
        assert a == b and a != "nofp"
    finally:
        monkeypatch.undo()
        C.clear_config_cache()
        gs.get_current_geometry(reload=True)


# ── the migration script ─────────────────────────────────────────────────────

def _run(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO / "scripts" / "migrate_derived_geometry.py"), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO), timeout=300)


def test_migration_dry_run_apply_idempotent(tmp_path):
    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    text = yaml.safe_dump({"name": DIE, "locked": True, "geometry": dict(STALE),
                           "thumb_svg": "<svg/>"}, sort_keys=False)
    die = root / DIE / "die.yaml"
    die.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))    # CRLF on disk
    good = mg.refresh_derived_geometry(STALE)
    ok_die = root / "OK" / "die.yaml"
    ok_die.parent.mkdir()
    ok_bytes = yaml.safe_dump({"name": "OK", "geometry": good},
                              sort_keys=False).encode("utf-8")
    ok_die.write_bytes(ok_bytes)
    before = die.read_bytes()

    r = _run(str(root))
    assert r.returncode == 0, r.stderr
    assert "stator_inner_radius: 12.100000000000001 -> 12.000000000000002" in r.stdout
    assert "OK" + ("\\" if sys.platform == "win32" else "/") + "die.yaml" not in r.stdout
    assert die.read_bytes() == before                      # dry-run: nothing written
    assert not list(root.rglob("*.bak-*"))

    r = _run(str(root), "--apply", "--date", "20300101")
    assert r.returncode == 0, r.stderr
    after = die.read_bytes()
    assert b"\r\n" in after and b"\n" not in after.replace(b"\r\n", b"")
    doc = yaml.safe_load(after.decode("utf-8"))
    assert doc["geometry"]["stator_inner_radius"] == pytest.approx(12.0)
    assert doc["geometry"]["rotor_inner_radius"] == pytest.approx(5.0)
    want = copy.deepcopy(yaml.safe_load(text))
    for k in ("stator_inner_radius", "rotor_outer_radius", "rotor_inner_radius"):
        want["geometry"][k] = good[k]
    assert doc == want                                     # nothing else moved
    assert (root / DIE / "die.yaml.bak-20300101").read_bytes() == before
    assert ok_die.read_bytes() == ok_bytes                 # consistent: untouched

    r = _run(str(root))
    assert r.returncode == 0 and "0 file(s), 0 field(s) stale" in r.stdout
