"""CIANO28 150_35 port (template A.7.3): the Fusion reference profile of the 150 evaluates, passes the template pre-check, and its
mechanics rows reproduce the Fusion bodies.  No FreeCAD needed (the FreeCAD side is verified by the server build of the die, see
docs/CAD_GENERATOR.md section "CIANO28 150")."""
import json
import math
import os
import sys

import pytest

from motor_ai_sim.cad import store

if store.CAD_DIR not in sys.path:
    sys.path.insert(0, store.CAD_DIR)

REF = os.path.join(store.CAD_DIR, "reference", "CIANO28_150_35_new.json")
TPL = os.path.join(store.CAD_DIR, "motor_templates", "segmented_flatwire")


@pytest.fixture(scope="module")
def ref():
    pj = json.load(open(REF, encoding="utf-8"))
    rows = {r["name"]: r for sec in store.SECTIONS for r in pj.get(sec, [])}
    vals = store.evaluate({n: r["freecad_expression"] for n, r in rows.items()})
    return pj, rows, vals


def test_template_version_is_a73():
    src = open(os.path.join(TPL, "__init__.py"), encoding="utf-8").read()
    assert 'TEMPLATE_VERSION = "A.7.3"' in src


def test_reference_is_found_for_the_die():
    own, fv = store.reference_for("CIANO28 150_35 new")
    assert own == REF and os.path.exists(fv)


def test_precheck_passes_on_the_reference(ref):
    from motor_templates.segmented_flatwire import health
    _, _, vals = ref
    assert health.precheck(lambda n: vals[n]) == []


def test_the_active_part_matches_fusion_values(ref):
    _, _, v = ref
    assert v["motor_length"] == 35 and v["num_seg"] == 4 and v["num_poles_per_segment"] == 7
    assert v["magnet_slices"] == 7 and v["mag_step"] == 5               # 7 axial magnet slices of 5 mm (Fusion R-Pattern1)
    assert v["rotor_key_count"] == 6 and v["wire_beng_r"] == 2.5
    assert v["coil_straight_len"] == 36 and v["coil_lead_total"] == 20


@pytest.mark.parametrize("prefix,fusion_mm3", [("rotor_holder1", 48825.856), ("rotor_holder2", 27786.865),
                                               ("stator_holder", 46239.733), ("stator_holder_back", 79820.163)])
def test_occupancy_rings_reproduce_the_fusion_body_volume(ref, prefix, fusion_mm3):
    _, rows, v = ref
    vol, k = 0.0, 1
    while prefix + "_r0_%d" % k in v:
        r0, r1, z0, z1 = (v["%s_%s_%d" % (prefix, x, k)] for x in ("r0", "r1", "z0", "z1"))
        fill = v.get("%s_fill_%d" % (prefix, k), 1.0)                  # a full ring has no fill row
        assert 0 <= r0 < r1 and z1 > z0 and 0 < fill <= 1
        vol += fill * math.pi * (r1 ** 2 - r0 ** 2) * (z1 - z0)
        k += 1
    assert k > 1
    assert vol == pytest.approx(fusion_mm3, rel=2e-3)


@pytest.mark.parametrize("pre,count,flip,vol", [("screwA", 12, 0, 427.865), ("screwB", 6, 0, 292.207), ("screwC", 6, 1, 103.801)])
def test_screw_families(ref, pre, count, flip, vol):
    _, rows, v = ref
    assert v[pre + "_count"] == count and v[pre + "_flip"] == flip
    rs, rh = v[pre + "_shank_d"] / 2, v[pre + "_head_d"] / 2
    approx = math.pi * rs ** 2 * v[pre + "_length"] + math.pi * rh ** 2 * v[pre + "_head_h"]
    assert approx == pytest.approx(vol, rel=0.08)                      # plain cylinders, before chamfers / socket (the build measures the exact solid)
    assert v[pre + "_head_top_z"] == pytest.approx(v[pre + "_flange_z"] + v[pre + "_head_h"])


def test_shaft_and_walls_are_aluminium(ref):
    pj, _, _ = ref
    m = pj["profile"]["materials"]
    assert all(m[b] == "Aluminum 6061" for b in ("Shaft", "Shaft_rear", "Stator_holder", "Stator_holder_back"))


def test_every_row_has_a_source(ref):
    pj, rows, _ = ref
    assert all(r.get("source") or r["role"] == "derived" for r in rows.values())     # a derived row is its own formula


def test_the_occupancy_rows_are_declared_info_only_where_python_reads_them(ref):
    _, rows, _ = ref
    for n in ("occupancy_variant", "bearing_out_z", "screwA_flip", "screwB_flip", "screwC_flip", "screwC_angle"):
        assert rows[n]["role"] == "info"                                # no expression uses them: the role check must not count them live


def _bom():
    from motor_ai_sim.cad import bom
    fix = os.path.join(os.path.dirname(__file__), "fixtures", "cad_bom_dump_150_L35.json")
    dump = json.load(open(fix, encoding="utf-8"))
    mats = {"Core": "20SW1200", "Coil": "Copper", "RotorCore": "20SW1200", "Magnet": "F52SH_120C", "Shaft": "Aluminum 6061",
            "Shaft_rear": "Aluminum 6061", "Stator_holder": "Aluminum 6061", "Stator_holder_back": "Aluminum 6061",
            "Bearing_outer_ring": "Steel (bearing)", "Bearing_inner_ring": "Steel (bearing)", "Bearing_ball": "Steel (bearing)",
            "Bearing_seal": "Steel (bearing)", "Screw": "Steel", "Screw_M3x30": "Steel", "Screw_M3x6": "Steel"}
    cards = {"20SW1200": dict(density=7650, stacking_factor=0.95, description="JFE 20SW1200, 0.20 mm"), "F52SH_120C": dict(density=7500),
             "copper": dict(density=8933), "polyimide": dict(density=1420), "Aluminium_6061": dict(density=2700),
             "Steel_42CrMo4_QT": dict(density=7850)}
    inputs = dict(die="CIANO28 150_35 new", config="L35", motor_id="CIANO28_150_35_new_L35", materials=mats, cards=cards, wire_stock=[],
                  winding=dict(connection="2S", n_coils_per_phase=6),
                  sim_materials=dict(magnet="F52SH_120C", stator_core="20SW1200", rotor_core="20SW1200", shaft="Aluminium_6061"),
                  sim_components=[], sim_total_kg=None, sim_source="test")
    return bom.build_bom(dump, inputs)


def test_bom_of_the_150_has_every_part_and_nothing_unclassified():
    b = _bom()
    k = {l["key"]: l for l in b["lines"]}
    assert not b["unclassified"]
    assert (k["magnets"]["qty"], k["coils"]["qty"], k["bearings"]["qty"]) == (196, 12, 2)     # 28 poles x 7 slices, 2 bearings
    assert (k["screws"]["qty"], k["screws_m3_front"]["qty"], k["screws_m3_back"]["qty"]) == (12, 6, 6)
    assert {"holder_front", "holder_back", "shaft", "shaft_rear", "board"} <= set(k)
    assert any("34" in (t or "") for t in b["to_be_specified"])                               # the M3 x 30 with a 34 mm shank is flagged


def test_bom_masses_follow_the_fusion_volumes():
    k = {l["key"]: l for l in _bom()["lines"]}
    assert k["screws"]["unit_mass_g"] == pytest.approx(427.8361 * 7850e-6, rel=1e-3)
    assert k["holder_back"]["unit_mass_g"] == pytest.approx(79821.6017 * 2700e-6, rel=1e-3)
