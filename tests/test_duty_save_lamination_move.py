"""A duty save from another lamination moves the configuration to the die it is (owner 2026-10-30).

2026-10-30 04:24-04:40 the owner changed wire_width 7 -> 7.5 (stack 90, 15 turns,
wire_height 1.0) on 'CIANO56 400_80 vadim 5 / L100-2x3ph-Al2O3-14t' and pressed
"Save to peak" three times.  The die holds only that configuration, so the geometry
PUT's shared-die guard did not move it; the die keeps its own wire keys, so its slot
stayed 7.74 while the live machine derives 8.24, and POST /duty answered 409 "build
differs" every time.  Pinned here:

* a save whose only differences are die-level and are a real lamination change
  (``die_shape_diffs``) moves the configuration to a die that IS the live lamination
  and saves the duty there; the source die is byte-for-byte untouched;
* an existing die of that lamination is reused, and when it already holds a
  configuration of this very build the duty goes into it (no duplicate copy);
* a save on the moved-to die does not move again;
* a locked configuration's own differences, or a stale derived copy in die.yaml,
  still refuse with 409 as before - nothing is written.

Throwaway catalog under the pytest tmp area; nothing reads or writes ``config/``.
"""
from __future__ import annotations

import json

import pytest
import yaml
from fastapi import HTTPException

from motor_ai_sim.routes import family as fam

DIE = "CIANO56 400_80 vadim 5"
CFG = "L100-2x3ph-Al2O3-14t"
WIRE = {"wire_width": 7.0, "wire_spacing_x": 0.2, "insulation_thickness": 0.17,
        "wire_height": 1.2, "num_wires_per_slot": 14, "motor_length": 100}
GEO = dict({"stator_diameter": 400.0, "num_seg": 8, "num_slots_per_segment": 6,
            "num_poles_per_segment": 7, "core_thickness": 6.5, "slot_height": 19.3,
            "air_gap": 1.0, "magnet_height": 18.8, "rotor_house_height": 4.0,
            "magnet_down": 2.0, "tooth_width": 9.0, "slot_width": 7.74}, **WIRE)
#: the owner's edit: wire 7 -> 7.5 (slot 7.74 -> 8.24), stack 90, 15 turns, wire height 1.0
EDIT = {"wire_width": 7.5, "motor_length": 90, "num_wires_per_slot": 15, "wire_height": 1.0}
LIVE_GEO = dict(GEO, slot_width=8.24, **EDIT)
MATS = {"magnet": "N52UH", "slot_insulation": "Al2O3"}


def _cfg_doc(name: str, die: str = DIE, locked: bool = False, **ov) -> dict:
    return {"name": name, "die": die, "role": "motor", "locked": locked,
            "geometry_overrides": dict(WIRE, **ov),
            "winding": {"connection": "8S"}, "materials": dict(MATS),
            "duties": [{"name": "peak", "mode": "motor", "current_arms": 400.0,
                        "rpm": 3000.0, "gamma_deg": 20.0, "note": ""}]}


def _write(path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


@pytest.fixture
def dies(tmp_path, monkeypatch):
    root = tmp_path / "dies"
    _write(root / DIE / "die.yaml", {"name": DIE, "locked": False, "geometry": dict(GEO)})
    _write(root / DIE / f"{CFG}.yaml", _cfg_doc(CFG))
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setattr(fam, "_CTX_FILE", tmp_path / ".family_context.json")
    monkeypatch.setattr(fam, "_acting_account", lambda *_a, **_k: ("Vadim", "vadim@example.com"))
    monkeypatch.setattr(fam, "_autolink_new_config", lambda *_a, **_k: None)
    monkeypatch.setattr(fam, "_live_parts", lambda: {})
    monkeypatch.setattr(fam, "may_see_die", lambda *_a, **_k: True)
    from motor_ai_sim.routes import presets as _presets
    monkeypatch.setattr(_presets, "_gen_thumb_svg", lambda *_a, **_k: None)
    live = {"geometry": dict(LIVE_GEO),
            "simulation": {"current_a": 420.0, "rpm": 3000.0, "gamma_deg": 22.0, "mode": "motor"},
            "winding": {"connection": "8S"}, "materials": dict(MATS)}
    monkeypatch.setattr(fam, "_live_cfg", lambda: live)
    (tmp_path / ".family_context.json").write_text(
        json.dumps({"die": DIE, "config": CFG, "duty": "peak"}), encoding="utf-8")
    return root


def _save(die: str = DIE, cfg: str = CFG) -> dict:
    spec = fam.DutySpec(name="peak", mode="motor", from_current=True)
    return fam.upsert_duty(fam.DutyCreate(die=die, config=cfg, duty=spec), _w={})


def _bytes(root, die: str = DIE) -> dict:
    return {p.name: p.read_bytes() for p in sorted((root / die).glob("*.yaml"))}


def _names(root) -> set:
    return {p.name for p in root.iterdir() if not p.name.startswith(".")}


def _yaml(path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _ctx() -> dict:
    return json.loads(fam._ctx_file().read_text(encoding="utf-8"))


def test_a_wider_slot_on_a_single_configuration_die_saves_on_its_own_die(dies):
    before = _bytes(dies)
    out = _save()
    assert _bytes(dies) == before                       # vadim 5 and its L100 untouched
    new = out["die"]
    assert new != DIE and _names(dies) == {DIE, new}
    tr = out["transitioned"]
    assert (tr["from_die"], tr["from_config"]) == (DIE, CFG)
    assert [x["key"] for x in tr["diffs"]] == ["slot_width"]
    assert tr["diffs"][0]["die"] == pytest.approx(7.74) and tr["diffs"][0]["live"] == pytest.approx(8.24)
    # the duty landed in the moved configuration (same name), with the live build adopted
    assert out["config"] == CFG
    c = _yaml(dies / new / f"{out['config']}.yaml")
    assert c["die"] == new
    assert c["geometry_overrides"]["wire_width"] == 7.5
    assert c["geometry_overrides"]["num_wires_per_slot"] == 15
    peak = next(x for x in c["duties"] if x["name"] == "peak")
    assert peak["current_arms"] == 420.0
    # the new die stamps the live lamination: its slot is 8.24
    nd = _yaml(dies / new / "die.yaml")
    assert fam.die_shape_diffs(nd["geometry"], LIVE_GEO) == []
    assert nd["derived_from"]["die"] == DIE
    ctx = _ctx()
    assert (ctx["die"], ctx["config"], ctx["duty"]) == (new, out["config"], "peak")
    assert ctx["transitioned_from"]["reason"] == "build_differs"


def test_the_next_save_stays_on_the_moved_die(dies):
    first = _save()
    again = _save(first["die"], first["config"])
    assert "transitioned" not in again
    assert again["die"] == first["die"] and _names(dies) == {DIE, first["die"]}


def test_an_existing_die_with_this_build_takes_the_duty(dies):
    """The orchestrator built 'vadim 6 / L90-2x3ph-Al2O3-15t' by hand for this machine."""
    six = "CIANO56 400_80 vadim 6"
    _write(dies / six / "die.yaml", {"name": six, "locked": False, "geometry": dict(LIVE_GEO)})
    _write(dies / six / "L90-2x3ph-Al2O3-15t.yaml",
           _cfg_doc("L90-2x3ph-Al2O3-15t", die=six, **EDIT))
    before = _bytes(dies)
    out = _save()
    assert (out["die"], out["config"]) == (six, "L90-2x3ph-Al2O3-15t")
    assert _names(dies) == {DIE, six}
    assert sorted(p.name for p in (dies / six).glob("*.yaml")) == ["L90-2x3ph-Al2O3-15t.yaml", "die.yaml"]
    assert _bytes(dies) == before                       # source untouched
    c = _yaml(dies / six / "L90-2x3ph-Al2O3-15t.yaml")
    assert next(x for x in c["duties"] if x["name"] == "peak")["current_arms"] == 420.0
    assert _ctx()["die"] == six and _ctx()["transitioned_from"]["die"] == DIE


def test_an_existing_die_of_this_lamination_gets_a_copy_when_no_configuration_matches(dies):
    six = "CIANO56 400_80 vadim 6"
    _write(dies / six / "die.yaml", {"name": six, "locked": False, "geometry": dict(LIVE_GEO)})
    _write(dies / six / "L90-other.yaml", _cfg_doc("L90-other", die=six, **dict(EDIT, num_wires_per_slot=12)))
    out = _save()
    assert out["die"] == six and out["config"] not in ("L90-other",)
    assert _names(dies) == {DIE, six}
    c = _yaml(dies / six / f"{out['config']}.yaml")
    assert c["geometry_overrides"]["num_wires_per_slot"] == 15


def test_a_locked_configuration_still_refuses(dies):
    _write(dies / DIE / f"{CFG}.yaml", _cfg_doc(CFG, locked=True))
    before = _bytes(dies)
    with pytest.raises(HTTPException) as e:
        _save()
    assert e.value.status_code == 409 and "die slot_width" in str(e.value.detail)
    assert _bytes(dies) == before and _names(dies) == {DIE}
    assert _ctx()["die"] == DIE


def test_a_stale_derived_copy_is_not_another_lamination(dies, monkeypatch):
    """die.yaml stores a stale slot_width; the wire stack is the die's own: refused as before, no move."""
    d = _yaml(dies / DIE / "die.yaml")
    d["geometry"]["slot_width"] = 9.99
    _write(dies / DIE / "die.yaml", d)
    live = {"geometry": dict(GEO), "simulation": {"current_a": 420.0, "rpm": 3000.0,
                                                  "gamma_deg": 22.0, "mode": "motor"},
            "winding": {"connection": "8S"}, "materials": dict(MATS)}
    monkeypatch.setattr(fam, "_live_cfg", lambda: live)
    with pytest.raises(HTTPException) as e:
        _save()
    assert e.value.status_code == 409
    assert _names(dies) == {DIE}
