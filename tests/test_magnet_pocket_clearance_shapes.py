"""`magnet_pocket_clearance` for EVERY magnet shape the builder draws (2026-11-18, opt-in).

Owner: every real magnet sits in its pocket with 0.035-0.04 mm per side; the e-coat and the epoxy inside it are
non-magnetic, so the model must carry that air ring.  Until now the key only worked with ``magnet_flat``.  Covered:

* the arc-topped spoke magnet (rotor_hole < 1, the CIANO14 40 rotor): pocket = magnet offset outward + the opening;
* the straight-sided pocket run to the OD (rotor_hole >= 1, the magnet on the rotor surface under a sleeve): the bottom
  edge (the layer between the magnet and the back iron) and both sides move out by the clearance;
* absent / 0 / blank = the polygons of today, bit for bit (WKB-identical);
* the 3-D Stage A section reads the same pocket.

The flat magnet with its clearance stays as tests/test_magnet_flat.py pins it to the Fusion sketch.
"""
import math

import pytest
from shapely import wkb
from shapely.geometry import Point
from shapely.ops import unary_union

from motor_ai_sim.cadquery_geometry import CadQueryMotor, _offset_magnet_local, _pocket_clearance

# CIANO14 40 new / L12, the stored geometry signature (12s14p, arc-topped spoke magnets, rotor_hole 0.918)
_SIG40 = ("air_gap:0.25|core_thickness:1.9|cut_width:1|insulation_thickness:0.05|k_end_by_turns:0|magnet_down_height:1|"
          "magnet_fill_down:0.82535391|magnet_fill_radius:0.2|magnet_fill_up:0.333429061|magnet_height:5.7|"
          "magnet_lamination:0|magnet_up_gap:0.1|motor_length:12|num_poles:14|num_poles_per_segment:7|num_seg:2|"
          "num_slots:12|num_slots_per_segment:6|num_wires_per_slot:7|rotor_fill_r:0|rotor_hole:0.917736442|"
          "rotor_house_height:1.1|shaft_height:2|sleeve_thickness:0|slot_height:6.1|slot_hs:0.267|stator_diameter:40|"
          "stator_fillet_r:1.1|stator_fillet_r1:0.15|tooth2_width:1.8|tooth_width:3.3|wire_height:0.6|wire_omit:0|"
          "wire_parallel:1|wire_spacing_x:0.14|wire_spacing_y:0.1|wire_split:1|wire_width:2.5")
G40 = {k: float(v) for k, v in (kv.split(":") for kv in _SIG40.split("|"))}
for _k in ("num_poles", "num_poles_per_segment", "num_seg", "num_slots", "num_slots_per_segment", "num_wires_per_slot",
           "wire_split", "wire_parallel"):
    G40[_k] = int(G40[_k])

# a surface-type rotor: the Ø200 12s10p die (magnets up to the OD under a 2.5 mm carbon sleeve, rotor_hole 1 = the
# straight-sided pocket run to the OD, magnet_up_gap 0), its stored die geometry
GSURF = dict(stator_diameter=200, slot_height=20.2, core_thickness=14, num_seg=2, num_slots_per_segment=6,
             num_poles_per_segment=5, air_gap=3.2, tooth_width=26, tooth2_width=12.1, cut_width=10,
             insulation_thickness=0.25, wire_width=9, wire_height=1, wire_spacing_x=0.1, wire_spacing_y=0.13,
             num_wires_per_slot=11, wire_split=1, slot_hs=0.2, magnet_height=31, rotor_house_height=6, shaft_height=5,
             magnet_fill_down=0.962426168, magnet_fill_up=0.44, magnet_fill_radius=4, magnet_up_gap=0, rotor_hole=1,
             magnet_down_height=8, magnet_lamination=5, stator_fillet_r=8, stator_fillet_r1=0.4, rotor_fill_r=0.2,
             motor_length=180, num_slots=12, num_poles=10, wire_parallel=1, sleeve_thickness=2.5)

CLR = 0.035


def _built(g, **ov):
    m = CadQueryMotor()
    m.set_parameters({**g, **ov})
    return m.get_2d_polygons(rotor_angle_deg=0.0)


def _mags(P):
    return [x[0] if isinstance(x, tuple) else x for x in P["magnets"]]


def _rotor(P):
    return P["rotor"][0] if isinstance(P["rotor"], tuple) else P["rotor"]


def _wkb(g):
    return wkb.dumps(g, hex=True)


@pytest.mark.parametrize("g", [G40, GSURF], ids=["arc_spoke", "surface_pocket_to_od"])
@pytest.mark.parametrize("off", [{"magnet_pocket_clearance": 0}, {"magnet_pocket_clearance": None},
                                 {"magnet_pocket_clearance": ""}, {"magnet_pocket_clearance": 0.0}],
                         ids=["zero", "none", "blank", "zero_float"])
def test_absent_or_zero_is_bit_identical(g, off):
    a, b = _built(g), _built(g, **off)
    assert _wkb(_rotor(a)) == _wkb(_rotor(b))
    assert [_wkb(m) for m in _mags(a)] == [_wkb(m) for m in _mags(b)]
    for k in ("in_band", "air_gap", "shaft", "sleeve"):
        if a.get(k) is not None and not isinstance(a.get(k), (list, tuple, dict, float, int)):
            assert _wkb(a[k]) == _wkb(b[k]), k


def test_offset_helper_zero_is_the_same_list_and_moves_edges_by_d():
    from motor_ai_sim.cadquery_geometry import _magnet_local_from_params
    m = CadQueryMotor()
    m.set_parameters(dict(G40))
    ml = _magnet_local_from_params(m.parameters)[0]
    assert _offset_magnet_local(ml, 0.0) is ml
    off = _offset_magnet_local(ml, CLR)

    def _line_dist(q, a, b):                                  # distance of q from the infinite line a-b
        (x0, y0), (x1, y1) = a, b
        return abs((x1 - x0) * (y0 - q[1]) - (x0 - q[0]) * (y1 - y0)) / math.hypot(x1 - x0, y1 - y0)

    for i in (0, 1, 4, 5):                                    # bottom corners and knees: on BOTH offset edges
        assert _line_dist(off[i], ml[i - 1], ml[i]) == pytest.approx(CLR, rel=1e-6)
        assert _line_dist(off[i], ml[i], ml[(i + 1) % 6]) == pytest.approx(CLR, rel=1e-6)
    assert _line_dist(off[2], ml[1], ml[2]) == pytest.approx(CLR, rel=1e-6)    # top corners: on the offset side
    assert _line_dist(off[3], ml[3], ml[4]) == pytest.approx(CLR, rel=1e-6)


@pytest.mark.parametrize("g", [G40, GSURF], ids=["arc_spoke", "surface_pocket_to_od"])
def test_clearance_opens_an_air_ring_and_leaves_the_magnet(g):
    a, b = _built(g), _built(g, magnet_pocket_clearance=CLR)
    # the magnet itself does not move
    assert [_wkb(m) for m in _mags(a)] == [_wkb(m) for m in _mags(b)]
    mags = unary_union(_mags(b))
    rot = _rotor(b)
    # iron keeps the clearance everywhere (the mitre offset of the faceted outline is never closer than d)
    assert rot.distance(mags) == pytest.approx(CLR, rel=0.02)
    assert _rotor(a).distance(mags) < 1e-6                     # without it the iron touches the magnet
    # the iron lost about (contact length) x clearance per magnet
    lost = _rotor(a).area - rot.area
    assert lost > 0.0
    contact = _rotor(a).buffer(1e-4).intersection(mags.boundary).length
    assert lost == pytest.approx(contact * CLR, rel=0.15)
    # the rotor stays one body and the ring is not iron and not magnet
    assert rot.geom_type == "Polygon" or len(rot.geoms) == 1
    ring = mags.buffer(0.9 * CLR, join_style=2).difference(mags)
    assert ring.intersection(rot).area < 1e-9


def test_surface_pocket_moves_the_back_iron_layer_and_the_sides():
    from shapely.geometry import LineString
    b = _built(GSURF, magnet_pocket_clearance=CLR)
    rot, m0 = _rotor(b), _mags(b)[0]
    c = m0.centroid
    u = (c.x / math.hypot(c.x, c.y), c.y / math.hypot(c.x, c.y))
    hit = LineString([(0, 0), (u[0] * 1e3, u[1] * 1e3)]).intersection(m0)
    r_bot = min(math.hypot(x, y) for x, y in (hit.coords if hasattr(hit, "coords") else
                                              [q for gg in hit.geoms for q in gg.coords]))
    # on the magnet axis: the layer between the magnet bottom and the back iron is air, the iron starts CLR below
    assert not rot.contains(Point((r_bot - 0.5 * CLR) * u[0], (r_bot - 0.5 * CLR) * u[1]))
    assert rot.contains(Point((r_bot - 1.5 * CLR) * u[0], (r_bot - 1.5 * CLR) * u[1]))
    # and without the clearance the iron starts right at the magnet
    a = _rotor(_built(GSURF))
    assert a.contains(Point((r_bot - 0.5 * CLR) * u[0], (r_bot - 0.5 * CLR) * u[1]))


def test_the_flat_magnet_clearance_is_unchanged():
    # the flat path computes exactly what it did: magnet.buffer(clearance, mitre) U opening (see test_magnet_flat.py)
    assert _pocket_clearance({"magnet_flat": 1, "magnet_pocket_clearance": 0.025}) == 0.025
    assert _pocket_clearance({"magnet_pocket_clearance": 0.035}) == 0.035          # no longer needs magnet_flat
    for v in (None, "", 0, -0.01, "x", float("nan")):
        assert _pocket_clearance({"magnet_pocket_clearance": v}) == 0.0


def test_validation_entry():
    from motor_ai_sim.routes._validation import SCHEMA_FALLBACK
    e = SCHEMA_FALLBACK["magnet_pocket_clearance"]
    assert e["min"] == 0.0 and e["max"] == 0.1 and e["unit"] == "mm" and e["unset_ok"] is True
    assert "per side" in e["description"]


def test_stage_a_section_uses_the_same_pocket():
    from motor_ai_sim.simulation.static3d.motor_geometry import load_motor_section
    s0 = load_motor_section(geo_override=dict(G40), n_sectors=1)
    s1 = load_motor_section(geo_override=dict(G40, magnet_pocket_clearance=CLR), n_sectors=1)
    assert s0.pocket_clearance_mm == 0.0 and s1.pocket_clearance_mm == CLR
    r0 = next(r for r in s0.regions if r.name == "rotor").polygon
    r1 = next(r for r in s1.regions if r.name == "rotor").polygon
    mags1 = unary_union([r.polygon for r in s1.magnet_regions()])
    assert r1.area < r0.area
    assert r1.distance(mags1) == pytest.approx(CLR, rel=0.02)
    assert "pocket clearance" in s1.summary() and "pocket clearance" not in s0.summary()
