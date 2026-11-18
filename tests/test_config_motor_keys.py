"""A configuration's own MOTOR keys on top of a shared die (owner 2026-11-18: one die per size and pole count; the magnet
fill belongs to the motor, not to the lamination).

Pinned:
* ``MOTOR_GEO_KEYS`` is an explicit tuple (fill up / down / radius, lamination), disjoint from the FREE keys;
* ``PATCH /config/{die}/{cfg}/motor_keys``: admin only; a dry run writes nothing; null or the die's own value clears;
  any other key or a bad value is refused (422) and writes nothing; a locked configuration refuses (409); the build
  signature moves when an override is set, and a configuration WITHOUT such an override keeps its file and signature;
* the duty save: a live machine that matches die + the configuration's motor-key override saves into the configuration
  (no die is minted, die.yaml untouched) and keeps the override through a free-key adoption; a live machine that differs
  in a NON-motor die key is still a die difference; without an override, a different magnet fill is still a die
  difference exactly as before.
Throwaway catalog under the pytest tmp area; no FEM.
"""
from __future__ import annotations

import json

import pytest
import yaml
from fastapi import HTTPException

from motor_ai_sim.routes import family as fam

DIE = "TEST 200 shared die"
CFG, SIB = "L150", "G2-L65"
WIRE = {"wire_width": 7.0, "wire_spacing_x": 0.1, "insulation_thickness": 0.2,
        "wire_height": 0.43, "num_wires_per_slot": 33, "motor_length": 150}
GEO = dict({"stator_diameter": 200.0, "num_seg": 4, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
            "core_thickness": 5.6, "slot_height": 20.4, "air_gap": 0.65, "magnet_height": 18.7,
            "rotor_house_height": 2.8, "tooth_width": 11.3, "magnet_fill_up": 0.44, "magnet_fill_down": 0.9,
            "magnet_fill_radius": 4.0}, **WIRE)
MATS = {"magnet": "N52UH", "slot_insulation": "Nomex"}
ADMIN = {"is_admin": True, "user": {"email": "admin@example.com"}}
AUDIT: list = []


def _cfg_doc(name, **ov):
    return {"name": name, "die": DIE, "role": "generator", "locked": False,
            "geometry_overrides": dict(WIRE, **ov), "winding": {"connection": "4S"}, "materials": dict(MATS),
            "duties": [{"name": "rated", "mode": "motor", "current_arms": 9.4, "rpm": 120.0, "gamma_deg": 0.0,
                        "note": "", "summary": {"T_em_avg_Nm": 80.0}}]}


def _write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _bytes(root):
    return {p.name: p.read_bytes() for p in sorted((root / DIE).glob("*.yaml"))}


@pytest.fixture
def cat(tmp_path, monkeypatch):
    root = tmp_path / "dies"
    _write(root / DIE / "die.yaml", {"name": DIE, "locked": True, "geometry": dict(GEO)})
    _write(root / DIE / f"{CFG}.yaml", _cfg_doc(CFG))
    _write(root / DIE / f"{SIB}.yaml", _cfg_doc(SIB))
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setattr(fam, "_CTX_FILE", tmp_path / ".family_context.json")
    monkeypatch.setattr(fam, "_acting_account", lambda *_a, **_k: ("Tester", "t@example.com"))
    monkeypatch.setattr(fam, "_autolink_new_config", lambda *_a, **_k: None)
    monkeypatch.setattr(fam, "_live_parts", lambda: {})
    monkeypatch.setattr(fam, "may_see_die", lambda *_a, **_k: True)
    monkeypatch.setattr(fam, "_require_die_write", lambda *_a, **_k: None)
    from motor_ai_sim import admin_audit
    AUDIT.clear()
    monkeypatch.setattr(admin_audit, "record", lambda *a, **k: AUDIT.append((a, k)))
    from motor_ai_sim.routes import presets as _presets
    monkeypatch.setattr(_presets, "_gen_thumb_svg", lambda *_a, **_k: None)
    (tmp_path / ".family_context.json").write_text(
        json.dumps({"die": DIE, "config": CFG, "duty": "rated"}), encoding="utf-8")
    return root


def _patch(keys, dry=False, w=ADMIN):
    return fam.patch_config_motor_keys(DIE, CFG, fam.MotorKeysPatch(keys=keys, dry_run=dry), _w=w)


def _sig(root, cfg=CFG):
    return fam._build_sig(_yaml(root / DIE / "die.yaml"), _yaml(root / DIE / f"{cfg}.yaml"))


# ── the key set ────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_motor_keys_are_an_explicit_tuple_apart_from_the_free_keys():
    assert fam.MOTOR_GEO_KEYS == ("magnet_fill_up", "magnet_fill_down", "magnet_fill_radius", "magnet_lamination")
    assert not set(fam.MOTOR_GEO_KEYS) & set(fam.FREE_GEO_KEYS)
    assert fam._motor_key_overrides({"geometry_overrides": dict(WIRE)}) == {}
    assert fam._motor_key_overrides({"geometry_overrides": {"magnet_fill_up": 0.54, "magnet_fill_down": None,
                                                            "rotor_hole": 0.9, "magnet_lamination": True}}) \
        == {"magnet_fill_up": 0.54}


# ── the route ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_dry_run_writes_nothing_and_says_what_would_change(cat):
    before, sig = _bytes(cat), _sig(cat)
    out = _patch({"magnet_fill_up": 0.54}, dry=True)
    assert out["dry_run"] is True and out["changed"] == ["magnet_fill_up"]
    assert out["before"] == {"magnet_fill_up": None} and out["after"] == {"magnet_fill_up": 0.54}
    assert out["die_values"]["magnet_fill_up"] == 0.44
    assert out["build_sig_before"] == sig != out["build_sig_after"]
    assert out["results_stale"] is True
    assert _bytes(cat) == before and AUDIT == []


def test_set_clear_and_the_die_value_round_trip(cat):
    sib = (cat / DIE / f"{SIB}.yaml").read_bytes()
    sig0 = _sig(cat)
    out = _patch({"magnet_fill_up": 0.54})
    c = _yaml(cat / DIE / f"{CFG}.yaml")
    assert c["geometry_overrides"]["magnet_fill_up"] == 0.54
    assert {k: c["geometry_overrides"][k] for k in WIRE} == WIRE           # the free keys are untouched
    assert _sig(cat) == out["build_sig_after"] != sig0
    assert AUDIT and AUDIT[-1][0][1] == "config.motor_keys"
    assert AUDIT[-1][1]["details"] == {"from": {}, "to": {"magnet_fill_up": 0.54}}
    assert (cat / DIE / f"{SIB}.yaml").read_bytes() == sib                 # the sibling keeps the die's fill
    assert _yaml(cat / DIE / "die.yaml")["geometry"]["magnet_fill_up"] == 0.44
    # the die's own value clears the override, and so does null; the signature is the original again
    _patch({"magnet_fill_up": 0.44})
    assert "magnet_fill_up" not in _yaml(cat / DIE / f"{CFG}.yaml")["geometry_overrides"]
    assert _sig(cat) == sig0
    _patch({"magnet_lamination": 5})
    _patch({"magnet_lamination": None})
    assert "magnet_lamination" not in _yaml(cat / DIE / f"{CFG}.yaml")["geometry_overrides"]
    assert _sig(cat) == sig0


def test_a_no_change_patch_keeps_the_file_and_the_signature_bit_identical(cat):
    before, sig = _bytes(cat), _sig(cat)
    out = _patch({"magnet_fill_up": None, "magnet_fill_down": 0.9})        # nothing set; the die's value
    assert out["changed"] == [] and out["build_sig_after"] == sig
    assert _bytes(cat) == before and AUDIT == []


@pytest.mark.parametrize("keys", [{"rotor_hole": 0.9}, {"magnet_height": 18.0}, {"motor_length": 140},
                                  {"magnet_fill_up": 0.0}, {"magnet_fill_up": 1.2}, {"magnet_fill_up": "0.5"},
                                  {"magnet_fill_up": True}, {"magnet_fill_radius": -1}, {"magnet_lamination": 2.5}])
def test_other_keys_and_bad_values_are_refused_and_write_nothing(cat, keys):
    before = _bytes(cat)
    with pytest.raises(HTTPException) as e:
        _patch(keys)
    assert e.value.status_code == 422
    assert _bytes(cat) == before and AUDIT == []


def test_admin_only_and_a_locked_configuration_refuses(cat):
    before = _bytes(cat)
    with pytest.raises(HTTPException) as e:
        _patch({"magnet_fill_up": 0.54}, w={"is_admin": False})
    assert e.value.status_code == 403
    c = _yaml(cat / DIE / f"{CFG}.yaml")
    c["locked"] = True
    _write(cat / DIE / f"{CFG}.yaml", c)
    before = _bytes(cat)
    with pytest.raises(HTTPException) as e:
        _patch({"magnet_fill_up": 0.54})
    assert e.value.status_code == 409
    assert _bytes(cat) == before


# ── the duty save ──────────────────────────────────────────────────────────────────────────────────────────────────────
def _live(monkeypatch, geo):
    live = {"geometry": dict(geo), "simulation": {"current_a": 9.5, "rpm": 120.0, "gamma_deg": 0.0, "mode": "motor"},
            "winding": {"connection": "4S"}, "materials": dict(MATS)}
    monkeypatch.setattr(fam, "_live_cfg", lambda: live)


def _save():
    spec = fam.DutySpec(name="rated", mode="motor", from_current=True)
    return fam.upsert_duty(fam.DutyCreate(die=DIE, config=CFG, duty=spec), _w={})


def _dies(root):
    return {p.name for p in root.iterdir() if not p.name.startswith(".")}


def test_a_live_machine_on_die_plus_override_saves_into_the_configuration(cat, monkeypatch):
    _patch({"magnet_fill_up": 0.54})
    die_before = (cat / DIE / "die.yaml").read_bytes()
    _live(monkeypatch, dict(GEO, magnet_fill_up=0.54))
    out = _save()
    assert "transitioned" not in out and _dies(cat) == {DIE}
    assert (cat / DIE / "die.yaml").read_bytes() == die_before
    c = _yaml(cat / DIE / f"{CFG}.yaml")
    assert c["geometry_overrides"]["magnet_fill_up"] == 0.54
    assert next(x for x in c["duties"] if x["name"] == "rated")["current_arms"] == 9.5


def test_the_override_survives_a_free_key_adoption(cat, monkeypatch):
    _patch({"magnet_fill_up": 0.54})
    _live(monkeypatch, dict(GEO, magnet_fill_up=0.54, num_wires_per_slot=34))
    _save()
    ov = _yaml(cat / DIE / f"{CFG}.yaml")["geometry_overrides"]
    assert ov["num_wires_per_slot"] == 34 and ov["magnet_fill_up"] == 0.54


def test_a_non_motor_die_key_is_still_a_die_difference(cat, monkeypatch):
    _patch({"magnet_fill_up": 0.54})
    before, dies = _bytes(cat), _dies(cat)
    _live(monkeypatch, dict(GEO, magnet_fill_up=0.54, magnet_height=19.0))
    with pytest.raises(HTTPException) as e:
        _save()
    assert e.value.status_code == 409
    assert _bytes(cat) == before and _dies(cat) == dies


@pytest.mark.parametrize("fill", [0.54, 0.50])
def test_a_magnet_fill_other_than_die_or_override_is_still_a_die_difference(cat, monkeypatch, fill):
    if fill == 0.50:
        _patch({"magnet_fill_up": 0.54})        # with an override, a THIRD value is refused
    before, dies = _bytes(cat), _dies(cat)
    _live(monkeypatch, dict(GEO, magnet_fill_up=fill))   # 0.54 without an override: as before this PR
    with pytest.raises(HTTPException) as e:
        _save()
    assert e.value.status_code == 409
    assert _bytes(cat) == before and _dies(cat) == dies
