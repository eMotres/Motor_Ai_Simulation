"""A die shared by several configurations never adopts one configuration's edit (owner 2026-10-30).

2026-10-29 07:03-10:09 UTC the owner edited his live L100 on the unlocked die
'CIANO56 400_80 vadim 3' (magnet_height 18.8 -> 23, slot_height 19.3 -> 22, ...).
Every save rewrote die.yaml, so the die's other configurations started building
the new lamination under results computed on the old one.  Pinned here:

* an edit of a die-level key on a die that also carries other configurations
  moves the ACTIVE configuration to its own die ("<family base> <user> <n>"),
  the source die and its other configurations are byte-for-byte untouched, the
  context says why (``transitioned_from.reason == "shared_die"``);
* a configuration whose DERIVED slot width (wire + spacing + liner) is not its
  die's moves the same way;
* a die holding only the active configuration keeps adopting the edit;
* a die that already is the new lamination on every die key is reused (loading
  the same configuration twice never mints a second die); one that matches only
  the diameter / topology is not;
* a stale derived copy in die.yaml is not a different lamination.

Throwaway catalog under the pytest tmp area; nothing reads or writes ``config/``.
"""
from __future__ import annotations

import json

import pytest
import yaml

from motor_ai_sim.routes import family as fam

DIE = "CIANO56 400_80 vadim 3"
GEO = {"stator_diameter": 400.0, "num_seg": 8, "num_slots_per_segment": 6,
       "num_poles_per_segment": 7, "core_thickness": 6.5, "slot_height": 19.3,
       "air_gap": 1.0, "magnet_height": 18.8, "rotor_house_height": 4.0,
       "magnet_down": 2.0, "tooth_width": 9.0,
       "wire_width": 6.6, "wire_spacing_x": 0.2, "insulation_thickness": 0.3,
       "wire_height": 1.2, "num_wires_per_slot": 10, "motor_length": 100}


def _cfg_doc(name: str, **overrides) -> dict:
    ov = {"motor_length": 100, "wire_width": 6.6, "wire_spacing_x": 0.2,
          "insulation_thickness": 0.3, "wire_height": 1.2, "num_wires_per_slot": 10}
    ov.update(overrides)
    return {"name": name, "die": DIE, "role": "motor", "geometry_overrides": ov,
            "materials": {"magnet": "N52UH"},
            "duties": [{"name": "rated", "mode": "motor", "current_arms": 100.0,
                        "rpm": 3000.0, "gamma_deg": 10.0, "note": ""}]}


@pytest.fixture
def dies(tmp_path, monkeypatch):
    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump(
        {"name": DIE, "locked": False, "geometry": dict(GEO)}, sort_keys=False), encoding="utf-8")
    for c in ("L100", "L110"):
        (root / DIE / f"{c}.yaml").write_text(yaml.safe_dump(_cfg_doc(c), sort_keys=False),
                                              encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setattr(fam, "_CTX_FILE", tmp_path / ".family_context.json")
    monkeypatch.setattr(fam, "_acting_account", lambda *_a, **_k: ("Vadim", "vadim@example.com"))
    monkeypatch.setattr(fam, "_autolink_new_config", lambda *_a, **_k: None)
    from motor_ai_sim.routes import presets as _presets
    monkeypatch.setattr(_presets, "_gen_thumb_svg", lambda *_a, **_k: None)
    return root


def _activate(cfg: str = "L100", die: str = DIE) -> None:
    fam._ctx_file().write_text(json.dumps({"die": die, "config": cfg, "duty": "rated"}),
                               encoding="utf-8")


def _ctx() -> dict:
    return json.loads(fam._ctx_file().read_text(encoding="utf-8"))


def _bytes(root, die: str = DIE) -> dict:
    return {p.name: p.read_bytes() for p in sorted((root / die).glob("*.yaml"))}


def _names(root) -> set:
    return {p.name for p in root.iterdir() if not p.name.startswith(".")}


def _die(root, die: str = DIE) -> dict:
    return yaml.safe_load((root / die / "die.yaml").read_text(encoding="utf-8"))


def test_edit_on_a_shared_die_moves_the_active_configuration(dies):
    _activate("L100")
    before = _bytes(dies)
    live = dict(GEO, magnet_height=23.0, slot_height=22.0, magnet_down=2.5)
    assert fam.sync_active_die_geometry(live, prev_geo=dict(GEO)) == "CIANO56 400_80 vadim"
    assert _bytes(dies) == before                           # source die + L100 + L110 untouched
    ctx = _ctx()
    assert (ctx["die"], ctx["config"], ctx["duty"]) == ("CIANO56 400_80 vadim", "L100", "rated")
    tf = ctx["transitioned_from"]
    assert tf["die"] == DIE and tf["reason"] == "shared_die" and tf["others"] == ["L110"]
    assert {x["key"] for x in tf["diffs"]} == {"magnet_height", "slot_height", "magnet_down"}
    new = dies / "CIANO56 400_80 vadim"
    assert sorted(p.name for p in new.glob("*.yaml")) == ["L100.yaml", "die.yaml"]
    nd = _die(dies, "CIANO56 400_80 vadim")
    assert nd["locked"] is False
    assert nd["geometry"]["magnet_height"] == 23.0 and nd["geometry"]["slot_height"] == 22.0
    assert nd["derived_from"]["die"] == DIE and nd["derived_from"]["config"] == "L100"
    nc = yaml.safe_load((new / "L100.yaml").read_text(encoding="utf-8"))
    assert nc["die"] == "CIANO56 400_80 vadim" and nc["duties"][0]["current_arms"] == 100.0


def test_next_edits_on_the_own_die_are_adopted(dies):
    _activate("L100")
    live = dict(GEO, magnet_height=23.0)
    new = fam.sync_active_die_geometry(live, prev_geo=dict(GEO))
    live2 = dict(live, slot_height=22.0)
    assert fam.sync_active_die_geometry(live2, prev_geo=live) == new
    assert _die(dies, new)["geometry"]["slot_height"] == 22.0
    assert _ctx()["die"] == new
    assert _names(dies) == {DIE, new}                     # no third die


def test_a_die_with_only_the_active_configuration_still_adopts(dies):
    (dies / DIE / "L110.yaml").unlink()
    _activate("L100")
    live = dict(GEO, magnet_height=23.0)
    assert fam.sync_active_die_geometry(live, prev_geo=dict(GEO)) == DIE
    assert _die(dies)["geometry"]["magnet_height"] == 23.0
    assert "transitioned_from" not in _ctx()
    assert _names(dies) == {DIE}


def test_a_wider_derived_slot_moves_the_configuration(dies):
    """L110 with a ceramic liner: insulation 0.3 -> 0.37 gives slot 7.74 on a die stamped 7.6."""
    (dies / DIE / "L110.yaml").write_text(yaml.safe_dump(
        _cfg_doc("L110", insulation_thickness=0.37), sort_keys=False), encoding="utf-8")
    _activate("L110")
    before = _bytes(dies)
    live = dict(GEO, insulation_thickness=0.37)
    target = fam.sync_active_die_geometry(live, prev_geo=dict(GEO))
    assert target == "CIANO56 400_80 vadim"
    assert _bytes(dies) == before
    tf = _ctx()["transitioned_from"]
    assert [x["key"] for x in tf["diffs"]] == ["slot_width"]
    assert tf["diffs"][0]["die"] == pytest.approx(7.6) and tf["diffs"][0]["live"] == pytest.approx(7.74)
    assert tf["others"] == ["L100"]
    # the new die stamps the configuration's slot: a duty save there no longer reads "build differs"
    ng = _die(dies, target)["geometry"]
    assert fam.die_shape_diffs(ng, live) == []


def test_loading_the_same_configuration_twice_reuses_its_die(dies):
    (dies / DIE / "L110.yaml").write_text(yaml.safe_dump(
        _cfg_doc("L110", insulation_thickness=0.37), sort_keys=False), encoding="utf-8")
    live = dict(GEO, insulation_thickness=0.37)
    _activate("L110")
    first = fam.sync_active_die_geometry(live, prev_geo=dict(GEO))
    _activate("L110")                                       # loaded again from the source die
    assert fam.sync_active_die_geometry(live, prev_geo=dict(GEO)) == first
    assert _names(dies) == {DIE, first}


def test_a_die_matching_only_the_diameter_is_not_reused(dies):
    other = dies / "CIANO56 400_80 vadim"
    other.mkdir()
    (other / "die.yaml").write_text(yaml.safe_dump(
        {"name": other.name, "locked": False, "geometry": dict(GEO, magnet_height=30.0)},
        sort_keys=False), encoding="utf-8")
    _activate("L100")
    assert fam.sync_active_die_geometry(dict(GEO, magnet_height=23.0),
                                        prev_geo=dict(GEO)) == "CIANO56 400_80 vadim 2"
    assert _die(dies, other.name)["geometry"]["magnet_height"] == 30.0


def test_a_fully_matching_die_is_reused(dies):
    other = dies / "Ready lamination"
    other.mkdir()
    (other / "die.yaml").write_text(yaml.safe_dump(
        {"name": other.name, "locked": False, "geometry": dict(GEO, magnet_height=23.0)},
        sort_keys=False), encoding="utf-8")
    _activate("L100")
    assert fam.sync_active_die_geometry(dict(GEO, magnet_height=23.0),
                                        prev_geo=dict(GEO)) == "Ready lamination"
    assert (other / "L100.yaml").is_file()
    assert not (dies / "CIANO56 400_80 vadim").exists()


def test_a_locked_shared_die_is_left_alone(dies):
    d = _die(dies)
    d["locked"] = True
    (dies / DIE / "die.yaml").write_text(yaml.safe_dump(d, sort_keys=False), encoding="utf-8")
    _activate("L100")
    before = _bytes(dies)
    assert fam.sync_active_die_geometry(dict(GEO, magnet_height=23.0), prev_geo=dict(GEO)) is None
    assert _bytes(dies) == before and _ctx()["die"] == DIE


def test_a_stale_derived_copy_is_not_another_lamination(dies):
    """die.yaml stores a stale stator_inner_radius; the live save carries the fresh one."""
    d = _die(dies)
    d["geometry"]["stator_inner_radius"] = 999.0
    (dies / DIE / "die.yaml").write_text(yaml.safe_dump(d, sort_keys=False), encoding="utf-8")
    _activate("L100")
    fam.sync_active_die_geometry(dict(GEO, stator_inner_radius=174.2), prev_geo=dict(GEO))
    assert _ctx()["die"] == DIE and "transitioned_from" not in _ctx()


def test_shape_diffs_cover_identity_and_ignore_configuration_keys():
    assert fam.die_shape_diffs(GEO, dict(GEO, motor_length=110, num_wires_per_slot=12)) == []
    keys = [x["key"] for x in fam.die_shape_diffs(GEO, dict(GEO, stator_diameter=410.0))]
    assert keys == ["stator_diameter"]
