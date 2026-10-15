# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The GFRP ring is removed from EVERY motor construction (owner 2026-10-15, template A.7.1).

Pure checks (no FreeCAD): the template builds no Ring / Ring_back, nothing in the profiles, the BOM, the part tree, the editable parameters,
the palette or the web names mentions it, and NO other part moved: the holders keep their axial position (the axial chain still carries the space the
ring filled in ``ring_h`` + ``ring_holder_gap``), so the screw clamp length and every holder / bearing / shaft position are what they were."""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest

from motor_ai_sim.cad import store

CAD = Path(store.CAD_DIR)
if str(CAD) not in sys.path:
    sys.path.insert(0, str(CAD))
TEMPLATE = CAD / "motor_templates" / "segmented_flatwire"
REPO = CAD.parent


def _src(name):
    return (TEMPLATE / name).read_text(encoding="utf-8")


def _tuple(src, name):
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(name)


def test_the_template_builds_no_ring():
    mech = _src("mechanics.py")
    for n in ("MECH_BODIES", "MECH_LINKS"):
        assert not [x for x in _tuple(mech, n) if x.startswith("Ring")], n
    assert "def build_ring" not in mech and 'body("Ring")' not in mech and "Ring_back" not in mech and "Ring_front" not in mech
    health = _src("health.py")
    assert "Ring" not in _tuple(health, "MECH_BODIES") and "Ring" not in _tuple(health, "MECH_BODIES_CARRIER")
    assert "Ring_front" not in health and "Ring_back" not in health and not re.search(r"(?<![A-Za-z])ring_inner_r", health)
    view = _src("view.py")
    assert "Ring" not in _tuple(view, "BODIES") and "Ring_back" not in _tuple(view, "LINKS")
    from motor_templates.segmented_flatwire import frameless
    assert "Ring" not in frameless.OMITTED_OBJECTS and "Ring_back" not in frameless.OMITTED_OBJECTS
    assert not any("ring" in x.lower() and "bearing" not in x.lower() for x in frameless.KIT_OMITS)
    assert "Rings" not in _src("electronics.py")
    pal = json.loads((TEMPLATE / "palette.json").read_text(encoding="utf-8"))
    assert "ring" not in pal["hex"] and "Ring" not in pal["body_role"]
    assert not [k for k in json.dumps(pal).split('"') if k.startswith("Ring")]
    for script in ("export_step.py", "export_models.py", "export_simplified.py", "mechanics_vs_reference.py"):
        s = (CAD / "fc_scripts" / script).read_text(encoding="utf-8")
        assert "Ring_front" not in s and "Ring_back" not in s and '"Ring"' not in s and not re.search(r"(?<![A-Za-z])ring_inner_r", s), script
    assert not (CAD / "reference" / "fusion_mechanics" / "Ring.json").exists()


@pytest.mark.parametrize("profile", ["CIANO14_40_12.json", "CIANO28_85_20SW1200.json"])
def test_the_profiles_lose_what_only_the_ring_used_and_keep_the_holder_chain(profile):
    pj = json.loads((CAD / "reference" / profile).read_text(encoding="utf-8"))
    rows = {p["name"]: p for sec in store.SECTIONS for p in pj.get(sec, [])}
    for gone in ("ring_t", "ring_inner_r", "ring_back_dz"):
        assert gone not in rows, gone
    assert "Ring" not in pj["profile"]["materials"]
    # the rows that place the holders stay, with an honest comment
    assert rows["holder_face_z"]["freecad_expression"] == "motor_length / 2 + ring_h + ring_holder_gap"
    assert "removed" in rows["ring_h"]["comment"] or "no longer" in rows["ring_h"]["comment"]
    assert not [n for n, r in rows.items() if re.search(r"ring_inner_r|ring_t\b|ring_back_dz", r["freecad_expression"])]


@pytest.mark.parametrize("profile,length,face_z,flange_z,screw", [
    ("CIANO14_40_12.json", 12, 7.9, 9.1, 18.0), ("CIANO14_40_12.json", 20, 11.9, 13.1, 25.0),
    ("CIANO28_85_20SW1200.json", 13, 8.6, 9.6, 20.0), ("CIANO28_85_20SW1200.json", 15, 9.6, 10.6, 22.0)])
def test_no_other_part_moved_the_holders_the_bearings_and_the_screws_are_where_they_were(profile, length, face_z, flange_z, screw):
    pj = json.loads((CAD / "reference" / profile).read_text(encoding="utf-8"))
    expr = {p["name"]: p["freecad_expression"] for sec in store.SECTIONS for p in pj.get(sec, [])}
    expr["motor_length"] = "%g mm" % length
    v = store.evaluate(expr)
    assert v["holder_face_z"] == pytest.approx(face_z) and v["holder_flange_z"] == pytest.approx(flange_z)       # axial chain, as before
    assert v["holder_screw_l"] == pytest.approx(screw)                                                          # clamp length and stock screw
    assert v["bearing_out_z"] == pytest.approx(face_z + v["bearing_w"])


def test_the_ring_is_not_an_editable_parameter_and_has_no_label():
    from motor_ai_sim import cad_params as C
    assert "ring_t" not in C.CATALOGUE
    for k in ("ring_h", "ring_holder_gap"):                                       # the holder offset keeps its (Fusion) name
        label = C.CATALOGUE[k]["label"]
        assert "ring" not in label["en"].lower() and "glass" not in label["en"].lower() and "玻璃纤维环" not in label["zh"]


def test_the_part_tree_the_part_rules_and_the_mass_have_no_ring():
    from motor_ai_sim import part_materials as pm
    from motor_ai_sim import part_tree as PT
    from motor_ai_sim.modules import motor_mass as MM
    assert "rings" not in pm.mechanical_parts()
    assert "rings" not in MM.ROTATING_KEYS
    src = Path(PT.__file__).read_text(encoding="utf-8")
    assert "Ring_front" not in src and "Glass-fibre rings" not in src


@pytest.mark.parametrize("name,retired", [("Ring (glass-fibre)", True), ("Ring", True), ("Rotor carrier, front", False),
                                          ("Stator holder, front", False), ("Ball bearing 618/8-2Z", False), ("Ringing", False)])
def test_a_bom_built_before_the_removal_has_its_ring_row_ignored_by_the_mass(name, retired):
    from motor_ai_sim.modules import motor_mass as MM
    assert bool(MM._BOM_RETIRED.search(name)) is retired                          # noqa: SLF001


def test_the_web_names_and_the_docs_say_nothing_of_a_ring():
    web = REPO / "web" / "src"
    for loc in ("en", "zh-CN"):
        for f in ("model3d.json", "geometry.json", "motors.json"):
            flat = (web / "locales" / loc / f).read_text(encoding="utf-8")
            assert not re.search(r"materialsPart_rings|assembly_ring_|\"Ring_front\"|\"Ring_back\"", flat), (loc, f)
    assert "(Ring|Holder)" not in (web / "lib" / "assemblyTree.ts").read_text(encoding="utf-8")
