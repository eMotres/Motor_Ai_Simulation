# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The GFRP coil rings: FULL model only (owner 2026-10-16, template A.7.4).

Template A.7.1 (#216) removed Ring / Ring_back from every construction; the owner only wanted them out of the SIMPLIFIED model - in the
full model the two rings fix the coils.  Pure checks (no FreeCAD):

* the full template builds Ring + its back link Ring_back (mechanics, health, view, palette, exports) for the profiles with ring rows
  (CIANO14 40, CIANO28 85); the BOM, the part tree and the full mass carry the "rings" part;
* the simplified export omits them (simplified.OMITTED_MECH, applied by fc_scripts/export_simplified.py);
* the CIANO28 150_35 port has no ring (no ring rows, no ring BOM line, no ring fit pair in the occupancy branch);
* the holders / bearings / shaft / screws keep their axial positions (the ring_h + ring_holder_gap chain never moved);
* the CIANO14 40 shaft follows the owner's Fusion body Shaft:1 (2026-10-16): the front rotor-stop flange is 2 mm above the rotor seat
  (Fusion d11885: 3 -> 2 mm, Ø13.9 at the as-built seat Ø9.9), the bore is Ø6 (d11692, r 3.0, no longer seat r - shaft_height) and the
  tip is ONE diameter Ø4.901 over 16 mm (the Ø4.95 / Ø4.9 step is gone); the 0.2 mm chamfers stay."""
from __future__ import annotations

import ast
import json
import math
import re
import sys
from pathlib import Path

import pytest

from motor_ai_sim.cad import store

CAD = Path(store.CAD_DIR)
if str(CAD) not in sys.path:
    sys.path.insert(0, str(CAD))
TEMPLATE = CAD / "motor_templates" / "segmented_flatwire"
FC = CAD / "fc_scripts"
REPO = CAD.parent
FIX = Path(__file__).parent / "fixtures"
RING_PROFILES = ("CIANO14_40_12.json", "CIANO28_85_20SW1200.json")
NO_RING_PROFILE = "CIANO28_150_35_new.json"


def _src(path):
    return Path(path).read_text(encoding="utf-8")


def _const(src, name):
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(name)


def _profile(name):
    pj = json.loads((CAD / "reference" / name).read_text(encoding="utf-8"))
    return pj, {p["name"]: p for sec in store.SECTIONS for p in pj.get(sec, [])}


def _values(name, length=None):
    _, rows = _profile(name)
    expr = {n: r["freecad_expression"] for n, r in rows.items()}
    if length is not None:
        expr["motor_length"] = "%g mm" % length
    return store.evaluate(expr)


# ------------------------------------------------------------------------------------------------- the full template


def test_the_full_template_builds_the_ring_and_its_back_link():
    mech = _src(TEMPLATE / "mechanics.py")
    assert "Ring" in _const(mech, "MECH_BODIES") and "Ring_back" in _const(mech, "MECH_LINKS")
    assert "def build_ring" in mech and 'body("Ring")' in mech and '"Ring_back", P + "ring_back_dz"' in mech
    assert 'out["Ring_front"]' in mech and 'out["Ring_back"]' in mech
    # solids() lists the rings only when the body exists: the 150 (occupancy variant) has none and must keep exporting
    assert re.search(r'if doc\.getObject\("Ring"\) is not None:\s*#[^\n]*\n\s*out\["Ring_front"\]', mech)


def test_health_gates_the_ring_bodies_links_extents_and_fits():
    h = _src(TEMPLATE / "health.py")
    assert "Ring" in _const(h, "MECH_BODIES") and "Ring" in _const(h, "MECH_BODIES_CARRIER")
    assert '("Bearing_618_8_back", "Ring_back")' in h                                  # the link is gated
    assert '("Ring_front z"' in h and '("Ring_back z"' in h                            # stale-geometry extents
    assert '"ring_inner_r"' in h                                                        # pre-check: ring clears the stator bore
    from motor_templates.segmented_flatwire import health
    pairs = {(a, b) for a, b, _ in health.FIT_PAIRS}
    assert {("Ring_front", "Core"), ("Ring_back", "Core"), ("Ring_front", "Coils"), ("Ring_back", "Coils"),
            ("Screws", "Ring_front"), ("Screws", "Ring_back")} <= pairs
    assert {("Shaft", "Ring_front"), ("Shaft_rear", "Ring_back")} <= {(a, b) for a, b, _ in health.FIT_PAIRS_CARRIER}
    assert not [p for p in health.FIT_PAIRS_OCCUPANCY if "Ring_front" in p[:2] or "Ring_back" in p[:2]]   # the 150: no ring


def test_view_palette_frameless_and_electronics_know_the_ring():
    v = _src(TEMPLATE / "view.py")
    assert "Ring" in _const(v, "BODIES") and "Ring_back" in _const(v, "LINKS")
    pal = json.loads((TEMPLATE / "palette.json").read_text(encoding="utf-8"))
    assert pal["hex"]["ring"] and pal["body_role"]["Ring"] == "ring"
    assert pal["group_role"]["Ring_front"] == pal["group_role"]["Ring_back"] == "ring"
    from motor_templates.segmented_flatwire import palette, frameless
    assert palette.BODY_ROLE["Ring"] == "ring" and palette.GROUP_ROLE["Ring_front"] == palette.GROUP_ROLE["Ring_back"] == "ring"
    assert palette.PALETTE["ring"] == pal["hex"]["ring"]
    assert {"Ring", "Ring_back"} <= set(frameless.OMITTED_OBJECTS)                         # the frameless kit omits it with the housing
    assert '"Rings": pick("Ring_front", "Ring_back")' in _src(TEMPLATE / "electronics.py")


def test_the_full_exports_carry_the_rings():
    step = _src(FC / "export_step.py")
    assert "Ring_front, Ring_back" in step and "rings=dict(" in step
    models = _src(FC / "export_models.py")
    for n in ("Ring_front", "Ring_back"):
        assert n in _const(models, "ORDER") and n in _const(models, "EXPLODE")
    assert 'name in ("Ring_front", "Ring_back")' in models
    assert '"Ring"' in _src(FC / "mechanics_vs_reference.py")
    assert (CAD / "reference" / "fusion_mechanics" / "Ring.json").exists()
    assert (CAD / "reference" / "fusion_mechanics" / "Ring.step").exists()


# ------------------------------------------------------------------------------------------------- the simplified model


def test_the_simplified_model_omits_the_rings():
    from motor_templates.segmented_flatwire import simplified as S
    assert set(S.OMITTED_MECH) == {"Ring_front", "Ring_back"}
    assert not S.keeps_mech("Ring_front") and not S.keeps_mech("Ring_back")
    for kept in ("Shaft", "Shaft_rear", "Stator_holder", "Stator_holder_back", "Prop_fix", "Screw_1"):
        assert S.keeps_mech(kept), kept
    src = _src(FC / "export_simplified.py")
    assert not [n for n in _const(src, "ORDER") if n.startswith("Ring")]
    assert not [n for n in _const(src, "EXPLODE") if n.startswith("Ring")]
    assert "S.keeps_mech(name)" in src and 'view.COLORS["Ring"]' not in src
    assert "omitted=sorted(" in src                                                    # the JSON says what it left out


# ------------------------------------------------------------------------------------------------- profiles, axial chain


@pytest.mark.parametrize("profile", RING_PROFILES)
def test_the_ring_profiles_have_the_ring_rows(profile):
    pj, rows = _profile(profile)
    for n in ("ring_t", "ring_h", "ring_holder_gap", "ring_inner_r", "ring_back_dz"):
        assert n in rows, n
    assert pj["profile"]["materials"]["Ring"] == "GFRP"
    assert rows["ring_inner_r"]["freecad_expression"] == "coil_corner_r - ring_t"
    assert rows["ring_back_dz"]["freecad_expression"] == "-(motor_length + ring_h)"
    v = _values(profile)
    assert v["ring_inner_r"] > v["stator_down_r"]
    from motor_templates.segmented_flatwire import health
    assert not [t for t in health.precheck(lambda n: v[n]) if "ring" in t.lower()]


def test_the_150_port_has_no_ring():
    pj, rows = _profile(NO_RING_PROFILE)
    assert not [n for n in rows if n in ("ring_t", "ring_inner_r", "ring_back_dz")]
    assert "Ring" not in pj["profile"]["materials"]
    assert "occupancy_variant" in rows                                                 # built by mechanics_occupancy, never build_ring
    occ = _src(TEMPLATE / "mechanics_occupancy.py")
    assert "build_ring" not in occ and "Ring_back" not in occ and "Ring_front" not in occ


@pytest.mark.parametrize("profile,length,face_z,flange_z,screw", [
    ("CIANO14_40_12.json", 12, 7.9, 9.1, 18.0), ("CIANO14_40_12.json", 20, 11.9, 13.1, 25.0),
    ("CIANO28_85_20SW1200.json", 13, 8.6, 9.6, 20.0), ("CIANO28_85_20SW1200.json", 15, 9.6, 10.6, 22.0)])
def test_holders_bearings_shaft_and_screws_keep_their_axial_positions(profile, length, face_z, flange_z, screw):
    v = _values(profile, length)
    _, rows = _profile(profile)
    assert rows["holder_face_z"]["freecad_expression"] == "motor_length / 2 + ring_h + ring_holder_gap"
    assert v["holder_face_z"] == pytest.approx(face_z) and v["holder_flange_z"] == pytest.approx(flange_z)
    assert v["holder_screw_l"] == pytest.approx(screw)
    assert v["bearing_out_z"] == pytest.approx(face_z + v["bearing_w"])
    # the ring fills stator face .. stator face + ring_h; the holder flange sits ring_holder_gap beyond it
    assert v["motor_length"] / 2 + v["ring_h"] + v["ring_holder_gap"] == pytest.approx(face_z)
    assert v["ring_back_dz"] == pytest.approx(-(length + v["ring_h"]))


# ------------------------------------------------------------------------------------------------- BOM, part tree, mass


def _bom(v):
    from tests.test_cad_bom import make                                                     # the CIANO14 40 L12 / L20 dumps
    return make(v)[0]


@pytest.mark.parametrize("v", ["L12", "L20"])
def test_the_full_bom_lists_two_glass_fibre_rings(v):
    k = {l["key"]: l for l in _bom(v)["lines"]}
    assert k["rings"]["qty"] == 2 and k["rings"]["part"] == "Ring (glass-fibre)" and k["rings"]["total_mass_g"] > 0


def test_the_150_bom_has_no_ring():
    from tests.test_cad_150_port import _bom as bom150
    assert "rings" not in {l["key"] for l in bom150()["lines"]}


def test_part_tree_part_rules_and_mass_carry_the_rings():
    from motor_ai_sim import part_materials as pm
    from motor_ai_sim import part_tree as PT
    from motor_ai_sim.modules import motor_mass as MM
    assert "rings" in pm.mechanical_parts() and "rings" in MM.ROTATING_KEYS
    assert PT.group_of("rings") == "housing" and PT.families_of("rings") == ["Ring_front", "Ring_back"]
    assert PT.names_of("rings")["en"] == "Glass-fibre rings"
    tree = PT.build([{"key": "stator_core"}, {"key": "rings"}, {"key": "holder_front"}])
    assert [p["id"] for p in tree["parts"]] == ["stator_core", "holder_front", "rings"]
    assert "rings" not in [p["id"] for p in PT.build([{"key": "stator_core"}, {"key": "rings"}], kit=True)["parts"]]
    assert not hasattr(MM, "_BOM_RETIRED")                                              # a ring BOM row is counted again


def test_the_ring_thickness_is_an_editable_parameter():
    from motor_ai_sim import cad_params as C
    assert "ring_t" in C.CATALOGUE


# ------------------------------------------------------------------------------------------------- the 40 shaft (Fusion Shaft:1)

#: FreeCAD-measured Shaft of the server-built L12 / L20 models BEFORE the 2026-10-16 change (flange step 3, bore r = seat 4.95 - 2):
#: the analytic model below must reproduce them, so the new numbers it gives are trustworthy without FreeCAD
_BUILT_BEFORE = {"L12": (1577.9821078617838, 1958.6081182188848, 6.775170399916014),
                 "L20": (1975.0794192755347, 2355.705429632635, 6.653502626485791)}
FUSION_SHAFT_L12_MM3 = 1485.53          # owner's Fusion CIANO14 40, body Shaft:1 (re-read 2026-10-16 11:45)


def _shaft_props(p, n=40000):
    """(volume, area, com z) of the CIANO14 40 shaft exactly as mechanics.build_shaft makes it: revolve of the ShaftSketch polygon
    (chamfers included) minus the two drive-flat pockets (shaft_flat_w across, shaft_flat_len from the journal end), integrated
    against the journal-end chamfer and the 90 deg bore tip cone."""
    rb, rj, rs, rsh = p["shaft_bore_r"], p["shaft_journal_r"], p["shaft_seat_r"], p["shaft_shoulder_r"]
    rf, rt, rtip, ch = rs + p["shaft_flange_step"], p["shaft_thread_r"], p["shaft_tip_r"], p["shaft_chamfer"]
    zb, zf, zr = p["bearing_out_z"], p["holder_face_z"], p["rotor_w"] / 2
    zsh, zj, zt, ze = zf - p["shaft_shoulder_len"], p["shaft_journal_end_z"], p["shaft_thread_end_z"], p["shaft_front_end_z"]
    ta = math.tan(math.radians(p["shaft_bore_tip_angle"]))
    zbore = zj - rb / ta
    pts = [(rb, -zb), (rj - ch, -zb), (rj, -zb + ch), (rj, -zf), (rs - ch, -zf), (rs, -zf + ch), (rs, zr), (rf, zr),
           (rf, zsh), (rsh, zsh), (rsh, zf), (rj, zf), (rj, zj - ch), (rj - ch, zj), (rt, zj), (rt, zt), (rtip, zt),
           (rtip, ze), (0, ze), (0, zj), (rb, zbore)]
    mr = mz = area = 0.0
    for i in range(len(pts)):                                    # Pappus / Green over the closed section
        (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % len(pts)]
        c = x1 * y2 - x2 * y1
        mr += c * (x1 + x2) / 6.0
        mz += c * (x1 * (2 * y1 + y2) + x2 * (y1 + 2 * y2)) / 24.0
        area += math.pi * (x1 + x2) * math.hypot(x2 - x1, y2 - y1)
    vol, mom = 2 * math.pi * abs(mr), 2 * math.pi * abs(mz)
    fx = p["shaft_flat_w"] / 2
    seg = lambda r: 0.0 if r <= fx else r * r * math.acos(fx / r) - fx * math.sqrt(r * r - fx * fx)
    arc = lambda r: 0.0 if r <= fx else 2 * r * math.acos(fx / r)
    chord = lambda r: 0.0 if r <= fx else 2 * math.sqrt(r * r - fx * fx)
    z0 = zj - p["shaft_flat_len"]
    dz = (zj - z0) / n
    for k in range(n):
        z = z0 + (k + 0.5) * dz
        cone_o, cone_i = z > zj - ch, z > zbore
        ro = rj - (z - (zj - ch)) if cone_o else rj
        ri = max(0.0, rb - (z - zbore) * ta) if cone_i else rb
        a = 2 * (seg(ro) - seg(ri))
        vol -= a * dz
        mom -= a * z * dz
        area += 2 * (chord(ro) - chord(ri)) * dz - 2 * arc(ro) * dz * (math.sqrt(2) if cone_o else 1.0)
        if ri > fx:
            area -= 2 * arc(ri) * dz * (math.hypot(1, ta) / ta if cone_i else 1.0)
    ri0 = rb - (z0 - zbore) * ta if z0 > zbore else rb
    area += 2 * (seg(rj) - seg(ri0)) - 2 * (seg(rj - ch) - seg(rt))   # pocket floor; the journal end face cut away
    return vol, area, mom / vol


@pytest.mark.parametrize("fx", ["L12", "L20"])
def test_the_analytic_shaft_reproduces_the_freecad_built_shaft(fx):
    p = dict(json.loads((FIX / ("cad_bom_dump_%s.json" % fx)).read_text(encoding="utf-8"))["params"])
    p.update(shaft_flange_step=3.0, shaft_bore_r=p["shaft_seat_r"] - p["shaft_height"],          # the parameters it was built with
             shaft_thread_r=2.475, shaft_tip_r=2.45)
    for got, want in zip(_shaft_props(p), _BUILT_BEFORE[fx]):
        assert got == pytest.approx(want, rel=1e-6)


def test_the_40_shaft_rows_follow_the_fusion_body():
    _, rows = _profile("CIANO14_40_12.json")
    r = rows["shaft_flange_step"]
    assert (r["value"], r["fusion_dimension_value"], r["freecad_expression"], r["fusion_expression"]) == (2.0, 2.0, "2 mm", "2 mm")
    b = rows["shaft_bore_r"]
    assert (b["value"], b["fusion_dimension_value"], b["freecad_expression"]) == (3.0, 3.0, "3 mm")    # Ø6, not seat r - shaft_height
    for n in ("shaft_thread_r", "shaft_tip_r"):                                         # ONE tip diameter Ø4.901
        assert (rows[n]["value"], rows[n]["fusion_dimension_value"], rows[n]["freecad_expression"]) == (2.4505, 2.4505, "4.901 mm / 2"), n
    assert rows["shaft_chamfer"]["value"] == 0.2                                          # the chamfers stay
    v = _values("CIANO14_40_12.json")
    assert v["shaft_front_end_z"] - v["shaft_journal_end_z"] == pytest.approx(16.0)
    assert v["shaft_shoulder_r"] < v["shaft_seat_r"] + v["shaft_flange_step"]           # the pre-check order still holds
    assert v["shaft_journal_end_z"] - v["shaft_bore_r"] / math.tan(math.radians(v["shaft_bore_tip_angle"])) == pytest.approx(17.7)
    from motor_templates.segmented_flatwire import health
    assert not [t for t in health.precheck(lambda n: v[n]) if "shaft" in t.lower()]
    assert _profile("CIANO28_85_20SW1200.json")[1]["shaft_flange_step"]["value"] == 2.0   # the 85 (carrier) keeps its own row


def test_one_tip_diameter_builds_without_a_zero_length_step_and_keeps_the_thread_at_the_end():
    mech = _src(TEMPLATE / "mechanics.py")
    assert "if abs(rtip - rt) < 1e-9:" in mech and "del pts[15:17], spec[15:17]" in mech
    assert "pl.g[cone_seg], 1, pl.g[13]" in mech and "pl.g[axis_seg], pl.g[cone_seg]" in mech
    models = _src(FC / "export_models.py")
    assert '"zband", (g("shaft_thread_end_z"), g("shaft_front_end_z"))' in models and 'mode == "zband"' in models


@pytest.mark.parametrize("fx,volume", [("L12", 1485.5290), ("L20", 1875.1493)])
def test_the_40_shaft_as_built_flange_d13_9_bore_d6_tip_d4_901_and_its_volume(fx, volume):
    d = json.loads((FIX / ("cad_bom_dump_%s.json" % fx)).read_text(encoding="utf-8"))
    p = d["params"]
    assert (p["shaft_flange_step"], p["shaft_bore_r"], p["shaft_thread_r"], p["shaft_tip_r"]) == (2.0, 3.0, 2.4505, 2.4505)
    assert 2 * (p["shaft_seat_r"] + p["shaft_flange_step"]) == pytest.approx(13.9)            # flange Ø13.9 at the as-built seat Ø9.9
    assert 2 * p["shaft_seat_r"] == pytest.approx(9.9)                                          # the seat does not move
    vol, area, cz = _shaft_props(p)
    assert vol == pytest.approx(volume, abs=1e-3)
    shaft = next(e for e in d["entries"] if e["name"] == "Shaft")                              # the pinned dump follows the model
    assert (shaft["volume"], shaft["area"], shaft["com"][2]) == pytest.approx((vol, area, cz), rel=1e-9)
    assert shaft["bbox"][3] == pytest.approx(6.95)
    if fx == "L12":
        assert vol == pytest.approx(FUSION_SHAFT_L12_MM3, abs=0.5)                             # = the owner's Fusion body Shaft:1
