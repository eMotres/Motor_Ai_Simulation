"""CIANO14 50 (die "CIANO14 50 edited", L15; template A.7.5): its own reference profile, the replaceable phase board STEP, the even-mm screw rule.
No FreeCAD needed (the FreeCAD side is the local / server build of the die, see docs/CAD_GENERATOR.md section "CIANO14 50")."""
import json
import os
import sys

import pytest

from motor_ai_sim.cad import store

if store.CAD_DIR not in sys.path:
    sys.path.insert(0, store.CAD_DIR)

DIE = "CIANO14 50 edited"


@pytest.fixture(scope="module")
def ref():
    path, _ = store.reference_for(DIE)
    pj = json.load(open(path, encoding="utf-8"))
    rows = {r["name"]: r for sec in store.SECTIONS for r in pj.get(sec, [])}
    return path, pj, rows, store.evaluate({n: r["freecad_expression"] for n, r in rows.items()})


def test_the_die_has_its_own_reference(ref):
    path, pj, rows, vals = ref
    assert os.path.basename(path) == "CIANO14_50_edited.json" and path != store.BASE_PROFILE
    assert vals["bearing_d"] == 12 and vals["bearing_D"] == 24 and vals["bearing_B"] == 6          # SKF 61901-2Z
    assert vals["holder_flange_t"] == pytest.approx(1.8) and vals["bolt_head_d"] == pytest.approx(6)
    assert "phase_board_z" in rows and "deviations" in pj and "rotor_fill_r" in pj["deviations"]


def test_the_board_step_is_a_replaceable_named_file(ref):
    _, pj, _, _ = ref
    pb = pj["profile"]["phase_board"]
    assert pb["pins"] is False and pb["copper_volume"][0] < pb["copper_volume"][1]
    assert os.path.isfile(os.path.join(store.CAD_DIR, "reference", "phase_board", pb["step"]))      # the file electronics.find_step(name) opens
    assert os.path.isfile(os.path.join(store.CAD_DIR, "reference", "phase_board", "CIANO14_40_phase_board_revC_colour.step"))   # default board


def test_the_screw_stays_m2x20(ref):
    _, _, _, vals = ref
    assert vals["holder_screw_req"] == pytest.approx(19.9) and vals["holder_screw_l"] == pytest.approx(20.0)
    assert vals["holder_screw_l"] <= vals["holder_screw_max"]


def test_the_design_file_regenerates_the_reference():
    sys.path.insert(0, os.path.join(store.CAD_DIR, "reference"))
    import make_die_reference as m
    design = json.load(open(os.path.join(store.CAD_DIR, "reference", "designs", "CIANO14_50_edited_design.json"), encoding="utf-8"))
    pj = m.apply_design(json.load(open(store.BASE_PROFILE, encoding="utf-8")), design)
    mine = {r["name"]: r["freecad_expression"] for sec in store.SECTIONS for r in pj[sec]}
    saved = json.load(open(store.reference_for(DIE)[0], encoding="utf-8"))
    assert mine == {r["name"]: r["freecad_expression"] for sec in store.SECTIONS for r in saved[sec]}
