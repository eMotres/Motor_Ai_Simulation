"""CILN28 / G2-L40 (the generator CILN28 200): EVERY part of the 3-D model and of the bill of materials is the Fusion body (template A.7.9, owner
2026-10-20).  The profile block profile.fusion_parts and the STEP files are private data (eMotres/motor-ai-sim-private, cad/reference/CILN28.json and
cad/reference/fusion_parts/CILN28_G2_L40/); the tests that read them skip cleanly without that checkout.  The BOM rules, the part rules and the full-mass
chain are tested here on a synthetic model dump, so they run everywhere."""
import copy
import csv
import json
import os

import pytest

from motor_ai_sim.cad import bom
from tests.private_fixtures import need_cad_reference, need_fixture

TPL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cad", "motor_templates", "segmented_flatwire")
ACTIVE = ("Core", "RotorCore", "Magnet", "Coil")
COMPONENT = {"Core": "Core", "RotorCore": "Rotor", "Magnet": "Magnet", "Coil": "Coil"}
#: Fusion component -> BOM key of the Fusion-only parts (profile.fusion_parts.extras)
EXTRA_KEYS = {"Rotor_holder1": "carrier_front", "Rotor_holder2": "carrier_rear", "Stator_holder_up": "holder_front", "Stator_holder_down ": "holder",
              "Stator_holder_back": "holder_back", "Housing": "housing", "Fitting": "fittings", "Fan 200 24s": "fan", "61814-2rz": "bearings",
              "Insulation": "insulation_ceramic", "PCB_Insulation": "board_insulation"}


def test_template_version_is_a79():
    src = open(os.path.join(TPL, "__init__.py"), encoding="utf-8").read()
    assert 'TEMPLATE_VERSION = "A.7.9"' in src and "A.7.9 (2026-10-20)" in src


# ------------------------------------------------------------------------------------------------ the private profile + STEP files
@pytest.fixture(scope="module")
def prof():
    return json.load(open(need_cad_reference("CILN28.json"), encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest():
    return json.load(open(need_cad_reference("fusion_parts", "CILN28_G2_L40", "fusion_export_manifest.json"), encoding="utf-8"))


def test_the_profile_is_the_fusion_document(prof, manifest):
    fp = prof["profile"]["fusion_parts"]
    assert fp["document"]["name"] == manifest["document"] == "CILN28 200"
    assert fp["document"]["version"] == manifest["document_version"] and fp["ref_motor_length"] == 40
    assert fp["bom_mode"] == "fusion_assembly" and prof["named_parameters_mechanics"] == []     # no template mechanics, no template phase board
    assert set(fp["files"]) == set(ACTIVE) == set(fp["volumes_mm3"]) == set(fp["occurrences"])
    for bn in ACTIVE:
        part = manifest["parts"][COMPONENT[bn]]
        assert fp["files"][bn] == part["file"]
        assert fp["occurrences"][bn] == [o["matrix"] for o in part["occurrences"]]                # every Fusion occurrence, as exported
        assert fp["placement"][bn] in fp["occurrences"][bn]
    assert len(manifest["parts"]["Magnet"]["bodies"]) == 8 and len(manifest["parts"]["Magnet"]["occurrences"]) == 28   # 8 slices of 5 mm on 28 poles
    assert len(manifest["parts"]["Coil"]["occurrences"]) == 12 and len(manifest["parts"]["Coil"]["bodies"]) == 65
    # the coil is the UNION of the 65 conductor bodies (Fusion's body sum counts the overlaps at the bends twice)
    assert fp["volumes_mm3"]["Coil"] < manifest["parts"]["Coil"]["volume_mm3"]
    assert fp["volumes_mm3"]["Coil"] == pytest.approx(manifest["parts"]["Coil"]["volume_mm3"], rel=6e-3)


def test_every_fusion_component_is_used_once(prof, manifest):
    fp = prof["profile"]["fusion_parts"]
    files = list(fp["files"].values()) + [e["file"] for e in fp["extras"]]
    assert sorted(files) == sorted(p["file"] for p in manifest["parts"].values())              # every exported component, exactly once
    by = {e["component"]: e for e in fp["extras"]}
    for comp, key in EXTRA_KEYS.items():
        if comp in by:
            assert by[comp]["bom"]["key"] == key, comp
    assert {e["bom"]["key"] for e in fp["extras"]} == set(EXTRA_KEYS.values()) | {"screws", "board_copper"}
    screws_in = [e for e in fp["extras"] if e["bom"]["key"] == "screws"]
    assert len(screws_in) == 4 and sum(len(e["occurrences"]) for e in screws_in) == 32
    assert sum(len(e["occurrences"]) for e in fp["extras"] if e["bom"]["key"] == "board_insulation") == 8
    for e in fp["extras"]:
        part = next(p for p in manifest["parts"].values() if p["file"] == e["file"])
        assert e["volume_mm3"] == pytest.approx(part["volume_mm3"], abs=1e-6)
        assert e["occurrences"] == [o["matrix"] for o in part["occurrences"]]
        assert e["density_kg_m3"] > 0 and e["density_src"] and e["simplified"] in ("keep", "ring", "board", "omit")
        if "select" in e:
            assert len(part["bodies"]) == 1 and e["select"]["match"]["volume"] == pytest.approx(part["bodies"][0]["volume_mm3"], abs=1e-6)
    # the parts whose component STEP also carries child screws must select their own solid
    assert all("select" in e for e in fp["extras"] if e["name"] in ("Rotor_holder_rear", "Stator_holder_front", "Stator_holder_bearing", "Fittings"))


def test_the_name_collision_of_the_two_insulation_components(manifest):
    """The Fusion document has TWO components called "Insulation": the 72 alumina slot pieces and the GFRP plate of the phase board (8 occurrences
    inside the referenced component "PCB 200 24s serial").  The first export took the alumina for both (the 9.13 kg of components_v35.json counts
    nine alumina masses of 33.6 g); the plate has its own export."""
    al, plate = manifest["parts"]["Insulation"], manifest["parts"]["PCB_Insulation"]
    assert len(al["bodies"]) == 72 and len(al["occurrences"]) == 1 and al["bodies"][0]["material"].startswith("Alumina")
    assert len(plate["bodies"]) == 1 and len(plate["occurrences"]) == 8 and plate["bodies"][0]["material"] == "GFRP"
    assert plate["volume_mm3"] == pytest.approx(6964.750056, abs=1e-6)


def test_fit_design_overlaps_are_declared(prof):
    fp = prof["profile"]["fusion_parts"]
    assert set(fp["fit_design"]) == {"Coils|Stator_holder_back", "Coils|PCB_PhaseA1", "Coils|PCB_Insulation", "Rotor_holder_front|Screws_91292A132",
                                     "Stator_holder_front|Screws_DIN7991_M4x8", "Stator_holder_bearing|Bearings", "Stator_holder_back|Bearings",
                                     "Stator_holder_back|Screws_91292A198", "Stator_holder_back|Screws_DIN912_M6x10"}
    assert all(v > 1.0 for v in fp["fit_design"].values())


def test_the_step_files_are_in_the_private_data(prof):
    fp = prof["profile"]["fusion_parts"]
    for f in sorted(set(fp["files"].values()) | {e["file"] for e in fp["extras"]}):
        p = need_cad_reference("fusion_parts", "CILN28_G2_L40", f)
        assert b"ISO-10303-21" in p.read_bytes()[:64], f


# ------------------------------------------------------------------------------------------------ part rules, tree, palette
def test_part_rules_and_tree_know_the_g2_parts():
    from motor_ai_sim import part_materials as pm
    from motor_ai_sim import part_tree as pt
    defs = pm.mechanical_parts()
    order = list(defs)
    assert order.index("board_copper") < order.index("board") and order.index("board_insulation") < order.index("board")   # the first matching rule wins
    for key, mat in (("housing", "Aluminium_6061"), ("fittings", "Aluminium_6061"), ("fan", "Aluminium_6061"), ("board_copper", "copper"),
                     ("board_insulation", "FR4_glass_epoxy")):
        assert defs[key]["material"] == mat
    import re
    hit = lambda title: next((k for k, d in defs.items() if re.search(d["match"], title, re.I)), None)
    assert hit("Phase board copper layers (Fusion PCB 200 24s serial: PhaseA1)") == "board_copper"
    assert hit("Phase board insulation plate (Fusion GFRP plate, 8 pcs)") == "board_insulation"
    assert hit("Phase board rev C (coil connection, 4 layers x 6 oz)") == "board"
    assert hit("Fan (Fusion Fan 200 24s, 1 mm aluminium sheet)") == "fan" and hit("Fan plate (Fusion component 'Fan 200 24s')") == "fan_plates"
    assert hit("Housing sleeve (Fusion Housing, the Fusion body)") == "housing" and hit("Coolant fitting (Fusion Fitting, 2 pcs)") == "fittings"
    assert hit("Stator holder, bearing seat (Fusion Stator_holder_down)") == "holder"
    assert hit("Rotor carrier, rear (Fusion Rotor_holder2, the Fusion body)") == "carrier_rear"
    assert pt.families_of("fan") == ["Fan"] and pt.families_of("housing") == ["Housing"] and "Rotor_holder_front" in pt.families_of("carrier_front")
    pal = json.load(open(os.path.join(TPL, "palette.json"), encoding="utf-8"))
    for g in ("Rotor_holder_front", "Rotor_holder_rear", "Holder_bearing", "Housing", "Fittings", "Fan"):
        assert pal["group_role"][g] == "aluminium"
    from motor_ai_sim.modules import motor_mass as mm
    assert "fan" in mm.ROTATING_KEYS


# ------------------------------------------------------------------------------------------------ the BOM on a synthetic dump (no FreeCAD, no private data)
def _entry(label, volume, n=1, bbox=(-1, -1, -1, 1, 1, 1), **kw):
    e = dict(name=label, label=label, type="Part::Feature", parent=None, motor={}, linked=None, base=None, state=[], n_solids=n, volume=volume,
             area=1.0, bbox=list(bbox), com=[0, 0, 0], solid_volumes=[])
    e.update(kw)
    return e


def _extra(label, volume, key, title, rho, occ=1, n=1, count_solids=False, mat="Aluminum 6061"):
    return _entry(label, volume, n=n, bbox=(-10, -10, -2, 10, 10, 2), motor=dict(
        MaterialName=mat, BomKey=key, BomTitle=title, BomType="manufactured", FusionComponent=label, FusionOccurrences=occ, FusionVolume=volume / occ,
        DensityKgM3=rho, DensitySource="test card", FusionMaterials=["Aluminum 6061"], SimplifiedRule="keep", CountSolids=count_solids))


def _dump():
    params = dict(motor_length=40.0, stator_up_r=100.0, stator_down_r=74.0, rotor_up_r=73.35, rotor_down_r=54.65, rotor_house_height=1.7, num_seg=4.0,
                  rotor_poles=28.0, magnet_slices=8.0, wire_width=7.0, wire_height=0.35, num_wires_per_slot=33.0)
    entries = [_entry("Core", 329555.0288, bbox=(-100, -100, -20, 100, 100, 20)), _entry("RotorCore", 120722.1408, bbox=(-73, -73, -20, 73, 73, 20)),
               _entry("Magnet", 849.3429, bbox=(-5.9, 54, -20, 5.9, 71.7, -15)), _entry("MagnetStack", 6794.74, n=8, base="Magnet"),
               _entry("MagnetArray", 190252.8, n=224, base="MagnetStack"), _entry("Coil", 11557.5, bbox=(-13, 78, -48, 13, 94, 34)),
               _entry("Coils", 138690.0, n=12, base="Coil")]
    entries += [_extra("Stator_holder_front", 164851.559, "holder_front", "Stator holder, front (Fusion Stator_holder_up, the Fusion body)", 2700.0),
                _extra("Housing", 48278.991, "housing", "Housing sleeve (Fusion Housing, the Fusion body)", 2700.0),
                _extra("Fan", 8162.366, "fan", "Fan (Fusion Fan 200 24s, 1 mm aluminium sheet)", 2700.0),
                _extra("Screws_A", 5082.754, "screws", "Screws (4 families)", 7930.0, occ=8, n=8, mat="18-8 Stainless Steel"),
                _extra("Screws_B", 2687.315, "screws", "Screws (4 families)", 7850.0, occ=4, n=4, mat="Steel 4.6"),
                _extra("Insulation_ceramic", 8620.8, "insulation_ceramic", "Ceramic insulation (72 alumina pieces)", 3800.0, occ=1, n=72, count_solids=True, mat="Al2O3")]
    return dict(model="x.FCStd", freecad_version="1.1.3", meta=dict(motor_bom_mode="fusion_assembly", motor_fusion_document="CILN28 200 v35"), params=params,
                entries=entries, fusion_bodies=[])


def _inputs():
    cards = {"B15AHV950M": dict(density=7600, stacking_factor=0.92, thickness_mm=0.15), "N52UH_150C": dict(density=7500), "copper": dict(density=8933)}
    return dict(die="CILN28", config="G2-L40", motor_id="CILN28_G2-L40", materials={}, cards=cards, wire_stock=[], winding={},
                sim_materials=dict(magnet="N52UH_150C", stator_core="B15AHV950M", rotor_core="B15AHV950M"), sim_components=[], sim_total_kg=None, sim_source="test")


def test_bom_mode_switch_and_masses():
    b = bom.build_bom(_dump(), _inputs())
    k = {ln["key"]: ln for ln in b["lines"]}
    assert not b["unclassified"] and not b["warnings"]
    assert k["holder_front"]["total_mass_g"] == pytest.approx(164851.559 * 2.7e-3, abs=2e-3)
    assert k["screws"]["qty"] == 12 and k["screws"]["total_mass_g"] == pytest.approx(5082.754 * 7.93e-3 + 2687.315 * 7.85e-3, abs=2e-3)   # one row, four families summed
    assert k["insulation_ceramic"]["qty"] == 72 and k["insulation_ceramic"]["total_mass_g"] == pytest.approx(8620.8 * 3.8e-3, abs=2e-3)
    assert k["magnets"]["qty"] == 224 and k["magnets"]["total_mass_g"] == pytest.approx(849.3429 * 224 * 7.5e-3, abs=2e-3)
    assert k["stator_lam"]["total_mass_g"] == pytest.approx(329555.0288 * 0.92 * 7.6e-3, rel=1e-3)         # sheets are whole: 245 x the sheet mass
    assert k["coils"]["total_mass_g"] == pytest.approx(11557.5 * 8.933e-3 * 12, abs=2e-3) and k["coils"]["qty"] == 12


def test_bom_density_in_the_notes_is_in_kg_per_m3():
    """motor_mass scales a CAD mass by (assigned density / the density the BOM states): the row's note must state kg/m3 (g / mm3 x 1e6), else the
    BOM mass is scaled by a factor of hundreds (found 2026-10-20 on the first G2 BOM)."""
    from motor_ai_sim.modules.motor_mass import BomLine
    b = bom.build_bom(_dump(), _inputs())
    for ln in b["lines"]:
        if ln["key"] in ("holder_front", "housing", "fan"):
            assert BomLine("1", ln["part"], ln["material"], ln["total_mass_g"], ln["notes"]).density == pytest.approx(2700.0, abs=1.0)
        if ln["key"] == "screws":
            assert BomLine("1", ln["part"], ln["material"], ln["total_mass_g"], ln["notes"]).density == pytest.approx(7900.0, abs=40.0)


def test_full_mass_chain_counts_the_fusion_parts(tmp_path):
    """The CSV the CAD build writes -> motor_mass.read_bom_file -> the part rules: every Fusion row is a known part (nothing 'other'), the carriers replace
    the simulation shaft tube, the mass is the file's."""
    from motor_ai_sim import part_materials as pm
    from motor_ai_sim.modules import motor_mass as mm
    b = bom.build_bom(_dump(), _inputs())
    path = tmp_path / "x_BOM.csv"
    bom.write_csv(b, str(path))
    bf = mm.read_bom_file(path)
    defs = pm.mechanical_parts()
    keys = {ln.part: mm._classify(defs, ln.part) for ln in bf.lines if not mm._BOM_ACTIVE.search(ln.part) and not mm._BOM_COIL_LINE.search(ln.part)}
    assert set(keys.values()) == {"holder_front", "housing", "fan", "screws", "insulation_ceramic"}
    assert bf.total_g == pytest.approx(b["totals"]["motor_mass_g"], rel=1e-9)
    assert bf.unmassed and all("Adhesive" in x or "varnish" in x or "leads" in x for x in bf.unmassed)


# ------------------------------------------------------------------------------------------------ the real dump of the 2026-10-20 build (private fixture)
def test_bom_of_the_g2_build():
    dump = json.load(open(str(need_fixture("cad_bom_dump_g2_fusion.json")), encoding="utf-8"))
    cards = {"B15AHV950M": dict(density=7600, stacking_factor=0.92, thickness_mm=0.15), "N52UH_150C": dict(density=7500), "copper": dict(density=8933)}
    inputs = dict(die="CILN28", config="G2-L40", motor_id="CILN28_G2-L40", materials={}, cards=cards, wire_stock=[], winding=dict(connection="4S"),
                  sim_materials=dict(magnet="N52UH_150C", stator_core="B15AHV950M", rotor_core="B15AHV950M"), sim_components=[], sim_total_kg=None,
                  sim_source="test")
    b = bom.build_bom(copy.deepcopy(dump), inputs)
    k = {ln["key"]: ln for ln in b["lines"]}
    assert not b["unclassified"] and not b["warnings"]
    want = dict(carrier_front=228.7748, carrier_rear=127.8146, holder_front=445.0992, holder=192.2288, holder_back=815.1176, housing=130.3533, fittings=49.28,
                fan=22.0384, bearings=285.836, screws=175.99, board_copper=84.131, board_insulation=103.078, insulation_ceramic=32.759)
    for key, g in want.items():
        assert k[key]["total_mass_g"] == pytest.approx(g, rel=2e-4), key
    assert (k["magnets"]["qty"], k["coils"]["qty"], k["bearings"]["qty"], k["screws"]["qty"], k["board_insulation"]["qty"]) == (224, 12, 2, 32, 8)
    assert k["stator_lam"]["total_mass_g"] == pytest.approx(2304.25, rel=1e-3) and k["rotor_lam"]["total_mass_g"] == pytest.approx(844.09, rel=1e-3)
    assert b["totals"]["motor_mass_g"] == pytest.approx(8506.7, abs=1.0)
