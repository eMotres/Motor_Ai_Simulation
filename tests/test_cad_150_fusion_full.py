"""CIANO28 150_35: every part of the FULL 3-D model is the Fusion body (template A.7.8, owner 2026-10-20: the 3-D model of the 150_35 comes from
Fusion; the simplified model stays as it is).  The profile block profile.fusion_parts and the STEP files are private data
(eMotres/motor-ai-sim-private, cad/reference/fusion_parts/CIANO28_150_35/); the tests skip cleanly without that checkout.  The FreeCAD side
(import, placement, instance check, gate, fit) runs in the build; the selection rules are run here inside freecadcmd when it is installed."""
import json
import os
import sys

import pytest

from motor_ai_sim.cad import runner, store
from tests.private_fixtures import need_cad_reference, need_fixture

if store.CAD_DIR not in sys.path:
    sys.path.insert(0, store.CAD_DIR)

TPL = os.path.join(store.CAD_DIR, "motor_templates", "segmented_flatwire")
HERE = os.path.dirname(os.path.abspath(__file__))
ACTIVE = ("Core", "RotorCore", "Magnet", "Coil")
MECH = ("Shaft", "Shaft_rear", "Stator_holder", "Stator_holder_back", "Bearing_outer_ring", "Bearing_inner_ring", "Bearing_seal",
        "Screw", "Screw_M3x30", "Screw_M3x6")
COMPONENT = {"Core": "Core", "RotorCore": "Rotor", "Magnet": "Magnet", "Coil": "Coil", "Shaft": "Rotor_holder1", "Shaft_rear": "Rotor_holder2",
             "Stator_holder": "Stator_holder", "Stator_holder_back": "Stator_holder_back", "Bearing_outer_ring": "61811-2rz",
             "Bearing_inner_ring": "61811-2rz", "Bearing_seal": "61811-2rz",
             "Screw": "91290A137_NO THREADS_Black-Oxide Alloy Steel Socket Head Screw",
             "Screw_M3x30": "Hexagon Socket Head Cap Screw DIN 912 - M3 x 0.5 x 30 Steel 4.6 Plain",
             "Screw_M3x6": "Hexagon Socket Head Cap Screw DIN 912 - M3 x 0.5 x 6 Steel 4.6 Plain"}


@pytest.fixture(scope="module")
def fp():
    p = need_cad_reference("CIANO28_150_35_new.json")
    return json.load(open(p, encoding="utf-8"))["profile"]["fusion_parts"]


@pytest.fixture(scope="module")
def manifest():
    p = need_cad_reference("fusion_parts", "CIANO28_150_35", "fusion_export_manifest.json")
    return json.load(open(p, encoding="utf-8"))


def test_template_version_is_a78():
    src = open(os.path.join(TPL, "__init__.py"), encoding="utf-8").read()
    assert 'TEMPLATE_VERSION = "A.7.8"' in src and "A.7.8 (2026-10-20)" in src


def test_every_part_of_the_full_model_is_a_fusion_body(fp, manifest):
    assert set(fp["files"]) == set(ACTIVE + MECH) == set(fp["volumes_mm3"]) == set(fp["occurrences"])
    assert "Bearing_ball" not in fp["files"]                      # Fusion draws the 22 balls as one body: the template spheres stay
    assert fp["document"]["name"] == manifest["document"] == "CIANO28 150"
    assert fp["document"]["version"] == manifest["document_version"]
    assert fp["ref_motor_length"] == 35
    for bn in ACTIVE + MECH:
        part = manifest["parts"][COMPONENT[bn]]
        assert fp["files"][bn] == part["file"], bn
        assert fp["occurrences"][bn] == [o["matrix"] for o in part["occurrences"]], bn        # every Fusion occurrence, as exported
        if bn in fp.get("placement", {}):
            assert fp["placement"][bn] in fp["occurrences"][bn], bn                          # the template body stands for one of them
        vols = [b["volume_mm3"] for b in part["bodies"]]
        want = part["volume_mm3"] if bn == "Coil" else min(vols, key=lambda v: abs(v - fp["volumes_mm3"][bn]))
        assert fp["volumes_mm3"][bn] == pytest.approx(want, abs=1e-6), bn


def test_magnets_are_seven_slices_on_28_poles(fp, manifest):
    part = manifest["parts"]["Magnet"]
    assert len(part["bodies"]) == 7 and len(part["occurrences"]) == 28
    assert {round(b["bbox"][5] - b["bbox"][2], 6) for b in part["bodies"]} == {5.0}            # mag_step = 5 mm (Fusion)
    assert manifest["user_parameters"]["mag_step"]["expression"] == "5 mm"
    assert fp["select"]["Magnet"]["match"] == "all" and fp["select"]["Coil"]["match"] == "fuse"


def test_the_board_and_the_fusion_only_parts(fp, manifest):
    board = {b["component"]: b for b in fp["board"]}
    assert set(board) == {"Insulation", "PhaseA1", "PhaseA2", "PhaseB1", "PhaseB2", "PhaseC1", "PhaseC2", "Null"}
    assert len(board["Insulation"]["occurrences"]) == 8 and board["Insulation"]["material"] == "GFRP"
    assert all(b["material"] == "Copper" for c, b in board.items() if c != "Insulation")
    extras = {e["name"]: e for e in fp["extras"]}
    assert set(extras) == {"Fan_plates", "Insulation_ceramic"}
    assert len(extras["Fan_plates"]["occurrences"]) == 2 and extras["Fan_plates"]["split_sides"]
    for e in list(board.values()) + list(extras.values()):
        part = manifest["parts"][e["component"]]
        assert e["file"] == part["file"] and e["volume_mm3"] == pytest.approx(part["volume_mm3"], abs=1e-6)
        assert e["occurrences"] == [o["matrix"] for o in part["occurrences"]]


def test_the_step_files_are_in_the_private_data(fp):
    names = set(fp["files"].values()) | {b["file"] for b in fp["board"]} | {e["file"] for e in fp["extras"]}
    for f in sorted(names):
        p = need_cad_reference("fusion_parts", "CIANO28_150_35", f)
        assert b"ISO-10303-21" in p.read_bytes()[:64], f


def test_replaced_rows_are_declared_rows(fp):
    p = need_cad_reference("CIANO28_150_35_new.json")
    pj = json.load(open(p, encoding="utf-8"))
    rows = {r["name"] for sec in store.SECTIONS for r in pj.get(sec, [])}
    assert fp["replaced_rows"] and set(fp["replaced_rows"]) <= rows


def test_the_selection_and_the_instances_in_freecad(fp, tmp_path):
    """Magnet: 7 x 28 = 196 placed slices; Coil: the 29 bodies fuse into ONE solid of the Fusion volume; seal: 2 per bearing (freecadcmd)."""
    need_cad_reference("fusion_parts", "CIANO28_150_35", fp["files"]["Magnet"])
    try:
        fc = runner.find_freecadcmd()
    except FileNotFoundError:
        pytest.skip("freecadcmd not installed")
    prof = tmp_path / "fp.json"
    prof.write_text(json.dumps(fp), encoding="utf-8")
    code, log, st = runner.run_script(os.path.join(HERE, "_fusion_150_check.py"),
                                      dict(FC_CAD_ROOT=store.CAD_DIR, FC_CAD_REFERENCE=str(need_cad_reference()), FC_FP=str(prof),
                                           FC_REPORT=str(tmp_path / "r.json")), freecadcmd=fc, timeout=900)
    assert code == 0, log
    r = json.load(open(tmp_path / "r.json", encoding="utf-8"))
    assert r["Magnet"]["instances"] == 196 and r["Coil"]["instances"] == 12 and r["Bearing_seal"]["instances"] == 4
    assert r["Coil"]["seed_solids"] == 1
    assert r["Coil"]["seed_volume"] == pytest.approx(fp["volumes_mm3"]["Coil"], rel=1e-3)
    assert r["Magnet"]["seed_volume"] == pytest.approx(fp["volumes_mm3"]["Magnet"], rel=1e-6)
    assert r["Magnet"]["seed_zmin"] == pytest.approx(-17.5, abs=1e-6)                      # the lowest slice is the template body


def test_part_rules_and_tree_know_the_fusion_only_parts():
    from motor_ai_sim import part_materials as pm
    from motor_ai_sim import part_tree as pt
    defs = pm.mechanical_parts()
    assert defs["fan_plates"]["material"] == "Aluminium_6061" and defs["insulation_ceramic"]["material"] == "Al2O3"
    assert pt.families_of("fan_plates") == ["Fan_front", "Fan_back"] and pt.families_of("insulation_ceramic") == ["Insulation_ceramic"]
    pal = json.load(open(os.path.join(TPL, "palette.json"), encoding="utf-8"))
    assert pal["group_role"]["Insulation_ceramic"] == "ceramic" and pal["group_role"]["Fan_front"] == "aluminium"


def _bom(name):
    from motor_ai_sim.cad import bom
    dump = json.load(open(str(need_fixture(name)), encoding="utf-8"))
    mats = {"Core": "20SW1200", "Coil": "Copper", "RotorCore": "20SW1200", "Magnet": "F52SH_120C", "Shaft": "Aluminum 6061",
            "Shaft_rear": "Aluminum 6061", "Stator_holder": "Aluminum 6061", "Stator_holder_back": "Aluminum 6061",
            "Bearing_outer_ring": "Steel (bearing)", "Bearing_inner_ring": "Steel (bearing)", "Bearing_ball": "Steel (bearing)",
            "Bearing_seal": "Steel (bearing)", "Screw": "Steel", "Screw_M3x30": "Steel", "Screw_M3x6": "Steel"}
    cards = {"20SW1200": dict(density=7650, stacking_factor=0.95, description="JFE 20SW1200, 0.20 mm"), "F52SH_120C": dict(density=7500),
             "copper": dict(density=8933), "polyimide": dict(density=1420), "Aluminium_6061": dict(density=2700),
             "Steel_42CrMo4_QT": dict(density=7850), "Al2O3": dict(density=3800)}
    inputs = dict(die="CIANO28 150_35 new", config="L35", motor_id="CIANO28_150_35_new_L35", materials=mats, cards=cards, wire_stock=[],
                  winding=dict(connection="2S", n_coils_per_phase=6),
                  sim_materials=dict(magnet="F52SH_120C", stator_core="20SW1200", rotor_core="20SW1200", shaft="Aluminium_6061"),
                  sim_components=[], sim_total_kg=None, sim_source="test")
    return bom.build_bom(dump, inputs)


def test_bom_of_the_fusion_model():
    b = _bom("cad_bom_dump_150_L35_fusion.json")
    k = {ln["key"]: ln for ln in b["lines"]}
    assert not b["unclassified"]
    assert (k["magnets"]["qty"], k["coils"]["qty"], k["bearings"]["qty"]) == (196, 12, 2)
    assert (k["screws"]["qty"], k["screws_m3_front"]["qty"], k["screws_m3_back"]["qty"]) == (12, 6, 6)
    assert "the Fusion body" in k["holder_front"]["part"] and k["shaft"]["part"].startswith("Rotor carrier, front (Fusion Rotor_holder1")
    assert k["shaft_rear"]["part"].startswith("Rotor carrier, rear")        # motor_mass: carriers, the simulation shaft tube is not counted twice
    assert "Fusion phase board" in k["board"]["notes"]
    ins, cu = 8 * 3751.64283, 1833.405132 + 1121.40274 + 1777.808518 + 1218.497331 + 1777.808518 + 1248.55766 + 914.358782
    assert k["board"]["unit_mass_g"] == pytest.approx(ins * 1.85e-3 + cu * 8.933e-3, rel=1e-4)
    assert k["fan_plates"]["qty"] == 2 and k["fan_plates"]["unit_mass_g"] == pytest.approx(3560.979685 * 2.7e-3, rel=1e-4)
    assert k["insulation_ceramic"]["qty"] == 1 and k["insulation_ceramic"]["unit_mass_g"] == pytest.approx(3792.6 * 3.8e-3, rel=1e-4)
    assert k["rotor_lam"]["unit_mass_g"] * k["rotor_lam"]["qty"] == pytest.approx(70661.958676 * 0.95 * 7650e-6, rel=1e-4)
