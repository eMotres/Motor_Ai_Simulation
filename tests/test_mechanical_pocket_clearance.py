"""Mechanics of a magnet pocket DRAWN with a clearance (2026-11-25).

``geometry.magnet_pocket_clearance`` cuts the pocket as the magnet offset
outward by the clearance.  Above the rotor mesher's node weld (0.02 mm) the
magnet used to mesh as an island and rotor stress / modes / critical speeds were
refused ("the rotor solids mesh in N disconnected pieces").  The model now
(``simulation.mechanical.pocket_clearance``): the iron keeps the real pocket,
the magnet is meshed as the body that fills the clearance ring with its density
scaled to the drawn mass, and every separation pair across the ring starts open
with the drawn clearance as its stress-free gap, so the magnet seats against the
pocket under load.  Modes and critical speeds: the magnet bonded at that seated
position.

What this file pins, on a synthetic 4-pole spoke rotor (no catalog data):
  * clearance 0 / unset / at the weld tolerance: the very same polygons object,
    and every hook inert — nothing about an existing machine can move;
  * clearance 0.035 mm: the rotor solves, the four magnets seat across the
    clearance, the iron area is the drawn pocket's, the magnet mass is the
    drawn magnet's, and the answer is close to the zero-clearance one;
  * a magnet with nothing outward to seat on is still refused;
  * a pocket that is not the magnet offset by the clearance is refused rather
    than filled with iron;
  * modes and the stack properties behind the critical speeds solve, with the
    drawn masses;
  * a coupled run's mechanical failures are listed at its top level.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from shapely import affinity
from shapely.geometry import Point, Polygon

from motor_ai_sim.simulation.mechanical import contact as ctc
from motor_ai_sim.simulation.mechanical import pocket_clearance as pkc
from motor_ai_sim.simulation.mechanical import rotor_stress as rs

R_BORE, R_OD = 20.0, 50.0                 # mm
MAG_W, MAG_R0, MAG_R1 = 8.0, 26.0, 46.0   # mm
N_POLES = 4
RPM = 20000.0
CLR = 0.035                               # mm per side, the real Fusion value
STACK_MM = 50.0

ASSIGN = {"rotor_core": "20SW1200", "magnet": "F52SH_120C",
          "sleeve": "HM63_UD_60", "shaft": "Aluminium_7075"}


def _slab(ang: float) -> Polygon:
    s = Polygon([(MAG_R0, -MAG_W / 2), (MAG_R1, -MAG_W / 2),
                 (MAG_R1, MAG_W / 2), (MAG_R0, MAG_W / 2)])
    return affinity.rotate(s, ang, origin=(0, 0))


def _polys(clearance: float = 0.0, open_top: bool = False,
           pocket_clearance: float | None = None, up_gap: float = 0.0) -> dict:
    """Steel annulus with ``N_POLES`` radial magnet slabs in pockets.

    The pocket is the slab offset outward by ``pocket_clearance`` (default:
    ``clearance``) with mitre joins — the builder's cut.  ``open_top`` runs the
    pocket clean through the rim, so the magnet has nothing outward at all.
    ``up_gap`` draws that much more air above the magnet (``magnet_up_gap``):
    the pocket's outer wall then stands ``up_gap + clearance`` above it.
    """
    pc = clearance if pocket_clearance is None else pocket_clearance
    disk = Point(0, 0).buffer(R_OD, quad_segs=128)
    rotor = disk.difference(Point(0, 0).buffer(R_BORE, quad_segs=96))
    magnets = []
    for k in range(N_POLES):
        ang = 360.0 * k / N_POLES
        slab = _slab(ang)
        pocket = slab.buffer(pc, join_style=2) if pc > 0 else slab
        if up_gap > 0:
            w = MAG_W / 2 + pc
            pocket = pocket.union(affinity.rotate(
                Polygon([(MAG_R1, -w), (MAG_R1 + pc + up_gap, -w),
                         (MAG_R1 + pc + up_gap, w), (MAG_R1, w)]), ang, origin=(0, 0)))
        if open_top:
            w = MAG_W / 2 + pc
            chimney = affinity.rotate(
                Polygon([(MAG_R0 + 1.0, -w), (R_OD + 1.0, -w),
                         (R_OD + 1.0, w), (MAG_R0 + 1.0, w)]), ang, origin=(0, 0))
            pocket = pocket.union(chimney)
        magnets.append((slab, 1 if k % 2 == 0 else -1))
        rotor = rotor.difference(pocket)
    return {"rotor": rotor, "magnets": magnets, "sleeve": None, "shaft": None,
            "sleeve_r_mm": (0.0, 0.0)}


def _solve(polys, *, rpm: float = RPM, typ: str = "separation",
           cases: str = "single"):
    return rs.solve_rotor_stress(
        polys, ASSIGN, rpm, 1.2, 0.0, stack_length_mm=STACK_MM,
        mesh_size_mm=2.0, order=1, with_field=False,
        contacts={"magnet_rotor": ctc.ContactSpec(typ, 0.0)},
        lift_off_solves=0, case_mode=cases, loads="centrifugal")


def _case(out):
    return out["cases"][out["primary_case"]]


def _mech(c: float) -> dict:
    return pkc.mechanical_polys(_polys(c), {"magnet_pocket_clearance": c})


# ---------------------------------------------------------------------------
# opt-in: nothing changes below the weld
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("params", [
    None, {}, {"magnet_pocket_clearance": 0.0},
    {"magnet_pocket_clearance": None}, {"magnet_pocket_clearance": ""},
    {"magnet_pocket_clearance": "x"}, {"magnet_pocket_clearance": -0.1},
    {"magnet_pocket_clearance": float("nan")},
    {"magnet_pocket_clearance": 0.01},
    {"magnet_pocket_clearance": pkc.WELD_TOL_MM},
])
def test_below_the_weld_the_polygons_are_the_same_object(params):
    polys = _polys(0.0)
    assert pkc.mechanical_polys(polys, params) is polys
    assert pkc.POCKET_KEY not in polys


def test_without_the_marker_every_hook_is_inert():
    polys = _polys(0.0)
    mech = rs.resolve_part_materials(ASSIGN, False, None)
    assert pkc.scaled_mechs(mech, polys) is mech
    assert pkc.summary(polys, pkc.RULE_STRESS) is None
    rm = rs.build_rotor_mesh(polys, mesh_size_mm=2.0)
    cs = ctc.build_contact_system(rm.mesh, rm.part_tri, dict(ctc.DEFAULT_CONTACTS),
                                  order=1)
    assert pkc.pair_gaps(cs, polys) is None


def test_clearance_zero_solve_carries_no_clearance_block():
    out = _solve(_polys(0.0))
    assert "pocket_clearance" not in out
    assert "density_effective" not in out["materials"]["magnet"]


# ---------------------------------------------------------------------------
# the fill: iron keeps the real pocket, the magnet keeps its mass
# ---------------------------------------------------------------------------

def test_the_fill_is_the_pocket_and_the_iron_is_untouched():
    raw = _polys(CLR)
    mp = pkc.mechanical_polys(raw, {"magnet_pocket_clearance": CLR})
    assert mp is not raw and mp["rotor"] is raw["rotor"]
    pk = mp[pkc.POCKET_KEY]
    assert pk["clearance_mm"] == CLR
    for (fill, _p), (drawn, _q) in zip(mp["magnets"], raw["magnets"]):
        assert fill.contains(drawn)
        # the fill is the pocket: it overlaps no iron and leaves no ring of air
        assert fill.intersection(raw["rotor"]).area < 1e-6
        pocket = drawn.buffer(CLR, join_style=2)
        assert fill.symmetric_difference(pocket).area < 1e-6
    ring = 2.0 * (MAG_W + (MAG_R1 - MAG_R0)) * CLR + 4.0 * CLR * CLR
    assert pk["magnet_area_meshed_mm2"] - pk["magnet_area_drawn_mm2"] == \
        pytest.approx(N_POLES * ring, rel=1e-6)
    assert pk["density_scale"] == pytest.approx(
        pk["magnet_area_drawn_mm2"] / pk["magnet_area_meshed_mm2"])


def test_a_pocket_that_is_not_the_offset_is_refused_not_filled_with_iron():
    tight = _polys(0.0)                   # pocket cut to the magnet
    with pytest.raises(ValueError, match="not the magnet offset"):
        pkc.mechanical_polys(tight, {"magnet_pocket_clearance": CLR})


# ---------------------------------------------------------------------------
# rotor stress across the clearance
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def clr_out():
    return _solve(_mech(CLR), cases="three")


@pytest.fixture(scope="module")
def zero_out():
    return _solve(_polys(0.0), cases="three")


def test_the_clearance_rotor_solves_and_the_magnets_seat(clr_out):
    c = clr_out["cases"]["rated"]
    con = c["contact"]
    assert con["unretained_parts"] == []
    assert con["n_bodies"] >= 1
    seated = con["seated"]
    assert len(seated) == N_POLES, seated
    assert {s["part"] for s in seated} == {"magnet"}
    # each magnet travelled across the drawn clearance (plus the pocket's own
    # elastic growth), radially outward — not a runaway
    for s in seated:
        assert 0.8 * CLR * 1e3 < s["travel_um"] < 10.0 * CLR * 1e3, s
    assert c["sf_min"] is not None and c["sf_min"] > 0.0
    assert clr_out["retention"]["status"] == "pass"
    blk = clr_out["pocket_clearance"]
    assert blk["clearance_mm"] == CLR and blk["rule"] == pkc.RULE_STRESS


def test_standstill_with_the_clearance_is_unloaded_not_refused(clr_out):
    c = clr_out["cases"]["standstill"]
    assert c["contact"]["unretained_parts"] == []
    assert c["magnet_retention"]["verdict"] == "not loaded (standstill)"


def test_masses_are_the_drawn_ones(clr_out):
    raw = _polys(CLR)
    L = STACK_MM * 1e-3
    mech = rs.resolve_part_materials(ASSIGN, False, None)
    parts = clr_out["cases"]["rated"]["parts"]
    m_mag = sum(m.area for m, _ in raw["magnets"]) * 1e-6 * mech["magnet"].density * L
    m_fe = raw["rotor"].area * 1e-6 * mech["rotor"].density * L
    assert parts["magnet"]["mass_kg"] == pytest.approx(m_mag, rel=5e-3)
    assert parts["rotor"]["mass_kg"] == pytest.approx(m_fe, rel=5e-3)
    mats = clr_out["materials"]["magnet"]
    assert mats["density"] == mech["magnet"].density        # the card
    assert mats["density_effective"] == pytest.approx(
        mech["magnet"].density * clr_out["pocket_clearance"]["density_scale"])


def test_the_answer_is_close_to_the_zero_clearance_one(clr_out, zero_out):
    """35 µm of clearance on a 50 mm rotor: the magnet seats on the same lip it
    rests on at zero clearance, so the iron sees nearly the same load."""
    for case in ("rated", "overspeed"):
        a, b = clr_out["cases"][case], zero_out["cases"][case]
        assert a["sf_min"] == pytest.approx(b["sf_min"], rel=0.15), case
        assert a["rotor_od_growth_um"] == pytest.approx(
            b["rotor_od_growth_um"], rel=0.15), case


def test_the_magnet_seats_across_air_drawn_above_it():
    """A drawn up gap (0.1 mm) over the clearance: the magnet's outer face has a
    pair with the pocket wall at 0.135 mm, and it travels across to land there.
    A fill limited to the clearance ring left that wall with no pair at all."""
    up = 0.1
    raw = _polys(CLR, up_gap=up)
    mp = pkc.mechanical_polys(raw, {"magnet_pocket_clearance": CLR,
                                    "magnet_up_gap": up})
    for (fill, _p), (drawn, _q) in zip(mp["magnets"], raw["magnets"]):
        assert fill.intersection(raw["rotor"]).area < 1e-6
        assert fill.area - drawn.area == pytest.approx(
            2.0 * (MAG_W + (MAG_R1 - MAG_R0)) * CLR + 4.0 * CLR * CLR
            + (MAG_W + 2.0 * CLR) * up, rel=1e-6)
    c = _case(_solve(mp))
    con = c["contact"]
    assert con["unretained_parts"] == []
    assert len(con["seated"]) == N_POLES
    for s in con["seated"]:
        assert (CLR + up) * 1e3 * 0.9 < s["travel_um"] < (CLR + up) * 1e3 * 3.0, s
    assert c["sf_min"] is not None


@pytest.mark.parametrize("rpm,loads", [(RPM, "both"), (0.0, "torque")])
def test_torque_goes_through_the_iron_and_the_rotor_solves(rpm, loads):
    """With a torque the gap shear is put on the iron pole tops only, so at
    standstill (torque alone) a loose magnet is simply unloaded, and at speed it
    seats under its spin first — neither is a runaway."""
    out = rs.solve_rotor_stress(
        _mech(CLR), ASSIGN, rpm, 1.0, 0.0, stack_length_mm=STACK_MM,
        mesh_size_mm=2.0, order=1, with_field=False,
        contacts={"magnet_rotor": ctc.ContactSpec("separation", 0.0)},
        lift_off_solves=0, case_mode="single", loads=loads, torque_nm=50.0)
    c = _case(out)
    assert c["contact"]["unretained_parts"] == []
    assert c["sf_min"] is not None
    if rpm > 0:
        assert len(c["contact"]["seated"]) == N_POLES


def test_bonded_with_a_clearance_is_the_glued_fill():
    out = _solve(_mech(CLR), typ="bonded")
    assert out["pocket_clearance"]["fill"] == "bonded"
    c = _case(out)
    assert c["contact"]["seated"] == []
    assert c["contact"]["unretained_parts"] == []
    assert c["sf_min"] is not None


def test_a_magnet_with_nothing_outward_is_still_refused():
    raw = _polys(CLR, open_top=True)
    mp = pkc.mechanical_polys(raw, {"magnet_pocket_clearance": CLR})
    with pytest.raises(rs.RotorRanAway) as ei:
        _solve(mp)
    assert ei.value.pair == "magnet_rotor"
    assert "held by nothing" in str(ei.value)


def test_pair_gaps_are_the_drawn_clearance():
    mp = _mech(CLR)
    rm = rs.build_rotor_mesh(mp, mesh_size_mm=2.0)
    cs = ctc.build_contact_system(rm.mesh, rm.part_tri, dict(ctc.DEFAULT_CONTACTS),
                                  order=1)
    g0 = pkc.pair_gaps(cs, mp)
    mag = np.array([ctc.PART_MAGNET in (it.part_a, it.part_b)
                    for it in cs.interfaces])[cs.pair_iface]
    assert mag.any()
    # flat faces at the clearance; at a mitre corner the gap is measured along
    # the corner node's (averaged) normal, a little more than the clearance
    gm = g0[mag] * 1e3
    assert np.median(gm) == pytest.approx(CLR, rel=1e-3)
    assert gm.max() <= 2.0 * CLR
    assert gm.min() >= CLR * 0.999
    assert not g0[~mag].any()


# ---------------------------------------------------------------------------
# modes and the critical speeds' stack properties
# ---------------------------------------------------------------------------

def test_modes_solve_with_the_magnet_bonded_at_the_seat():
    from motor_ai_sim.simulation.mechanical import modal
    a = modal.solve_modes(_mech(CLR), {}, ASSIGN, body="rotor", n_modes=4,
                          mesh_size_mm=3.0, order=1, with_shapes=False)
    b = modal.solve_modes(_polys(0.0), {}, ASSIGN, body="rotor", n_modes=4,
                          mesh_size_mm=3.0, order=1, with_shapes=False)
    assert a["pocket_clearance"]["rule"] == pkc.RULE_MODAL
    assert "pocket_clearance" not in b
    fa = sorted(m["f_hz"] for m in a["modes"])
    fb = sorted(m["f_hz"] for m in b["modes"])
    assert len(fa) == 4
    for x, y in zip(fa, fb):
        assert x == pytest.approx(y, rel=0.05)


def test_stack_properties_weigh_the_drawn_parts():
    from motor_ai_sim.simulation.mechanical import rotordynamics as rdm
    raw = _polys(CLR)
    sp = rdm.stack_properties(_mech(CLR), ASSIGN, mesh_size_mm=3.0)
    mech = rs.resolve_part_materials(ASSIGN, False, None)
    m_mag = sum(m.area for m, _ in raw["magnets"]) * 1e-6 * mech["magnet"].density
    m_fe = raw["rotor"].area * 1e-6 * mech["rotor"].density
    assert sp.parts["magnet"] == pytest.approx(m_mag, rel=5e-3)
    assert sp.parts["rotor"] == pytest.approx(m_fe, rel=5e-3)


# ---------------------------------------------------------------------------
# the coupled run says so at its top level
# ---------------------------------------------------------------------------

def test_coupled_mechanical_failures_are_listed():
    from motor_ai_sim.routes.coupled import _mechanical_failures
    assert _mechanical_failures(None) == []
    assert _mechanical_failures({"ok": True, "modes": {"ok": True},
                                 "critical_speeds": {"ok": True}}) == []
    got = _mechanical_failures({
        "ok": False, "error": "the rotor solids mesh in 15 disconnected pieces",
        "modes": {"ok": False, "error": "x"}, "critical_speeds": {"ok": True}})
    assert [g["step"] for g in got] == ["rotor_stress", "modes"]
    assert "disconnected" in got[0]["error"]
    # a retention FAIL is a solved step with a verdict, not a failed step
    assert _mechanical_failures({"ok": True, "retention": {"status": "fail"}}) == []


# ---------------------------------------------------------------------------
# a pocket OPEN to the air gap (lips over the magnet corners)
# ---------------------------------------------------------------------------

OPEN_R1, OPEN_UP, OPEN_W = 49.6, 0.4, 0.8 * MAG_W   # magnet top 0.4 below the OD


def _open_polys(clearance: float) -> dict:
    """The 4-pole rotor with each magnet run up to 0.4 mm below the OD and its
    pocket opened to the gap over the middle 80 % of the magnet — two lips over
    the magnet corners, each belonging to a different pole piece."""
    disk = Point(0, 0).buffer(R_OD, quad_segs=128)
    rotor = disk.difference(Point(0, 0).buffer(R_BORE, quad_segs=96))
    magnets = []
    for k in range(N_POLES):
        ang = 360.0 * k / N_POLES
        slab = affinity.rotate(Polygon([(MAG_R0, -MAG_W / 2), (OPEN_R1, -MAG_W / 2),
                                        (OPEN_R1, MAG_W / 2), (MAG_R0, MAG_W / 2)]),
                               ang, origin=(0, 0))
        pocket = slab.buffer(clearance, join_style=2) if clearance > 0 else slab
        mouth = affinity.rotate(Polygon([(OPEN_R1, -OPEN_W / 2), (R_OD + 1, -OPEN_W / 2),
                                         (R_OD + 1, OPEN_W / 2), (OPEN_R1, OPEN_W / 2)]),
                                ang, origin=(0, 0))
        rotor = rotor.difference(pocket.union(mouth))
        magnets.append((slab, 1 if k % 2 == 0 else -1))
    return {"rotor": rotor, "magnets": magnets, "sleeve": None, "shaft": None,
            "sleeve_r_mm": (0.0, 0.0)}


def _open_mech(c: float = CLR) -> dict:
    return pkc.mechanical_polys(_open_polys(c), {"magnet_pocket_clearance": c,
                                                 "magnet_up_gap": OPEN_UP})


def _rmax(g) -> float:
    xy = np.asarray(g.exterior.coords)
    return float(np.hypot(xy[:, 0], xy[:, 1]).max())


def _xmax_pole(g, k: int) -> float:
    """The largest radial coordinate in pole ``k``'s own frame."""
    loc = affinity.rotate(g, -360.0 * k / N_POLES, origin=(0, 0))
    return float(np.asarray(loc.exterior.coords)[:, 0].max())


def test_open_pocket_fills_stop_at_the_od_and_the_bonded_fill_is_the_ring():
    mp = _open_mech()
    pk = mp[pkc.POCKET_KEY]
    for k, ((fs, _), (fb, _q), (d, _r)) in enumerate(zip(
            mp["magnets"], pk["magnets_bonded"], pk["magnets_drawn"])):
        assert _rmax(fs) <= R_OD + 1e-9            # never into the air gap
        assert _rmax(fs) > R_OD - 2e-3             # …but up the opening
        # the bonded fill is the clearance ring: no plug in the opening
        assert _xmax_pole(fb, k) <= OPEN_R1 + CLR + 1e-6
        assert fb.symmetric_difference(d.buffer(CLR, join_style=2)).area < 1e-6
    assert pkc.bonded_variant(mp)["magnets"] == pk["magnets_bonded"]
    p0 = _polys(0.0)
    assert pkc.bonded_variant(p0) is p0


def test_open_pocket_walls_of_the_opening_never_close():
    mp = _open_mech()
    rm = rs.build_rotor_mesh(mp, mesh_size_mm=0.5)
    cs = ctc.build_contact_system(rm.mesh, rm.part_tri, dict(ctc.DEFAULT_CONTACTS),
                                  order=1)
    g0 = pkc.pair_gaps(cs, mp) * 1e3
    x, y = cs.pair_pos[:, 0] * 1e3, np.abs(cs.pair_pos[:, 1] * 1e3)
    mag = np.array([ctc.PART_MAGNET in (it.part_a, it.part_b)
                    for it in cs.interfaces])[cs.pair_iface] & (x > 0) & (y < 6.0)
    wall = mag & (x > OPEN_R1 + CLR + 0.01)        # up the opening, above the lips
    assert wall.any()
    assert (g0[wall] > 0.05 * R_OD).all()          # beyond the seating cap
    # the lips' undersides: shut by the clearance (corners a little more)
    lip = (mag & (x > OPEN_R1 + 0.5 * CLR) & (x < OPEN_R1 + CLR + 1e-3)
           & (y > OPEN_W / 2) & (y < MAG_W / 2 + CLR + 1e-6))
    assert lip.any()
    assert (g0[lip] >= CLR * (1 - 1e-3)).all() and (g0[lip] <= 2.0 * CLR).all()
    assert g0[lip].min() == pytest.approx(CLR, rel=1e-3)    # the flat underside


def test_open_pocket_seats_on_the_lips():
    c = _case(_solve(_open_mech()))
    con = c["contact"]
    assert con["unretained_parts"] == [] and con["converged"]
    assert len(con["seated"]) == N_POLES
    assert c["sf_min"] is not None


def test_open_pocket_modes_do_not_tie_the_lips():
    """Modes on the bonded fill (the ring) agree with the zero-clearance rotor
    of the same drawing — a plug of magnet in the opening would stiffen the
    lips and raise them."""
    from motor_ai_sim.simulation.mechanical import modal
    a = modal.solve_modes(_open_mech(), {}, ASSIGN, body="rotor", n_modes=4,
                          mesh_size_mm=3.0, order=1, with_shapes=False)
    b = modal.solve_modes(_open_polys(0.0), {}, ASSIGN, body="rotor", n_modes=4,
                          mesh_size_mm=3.0, order=1, with_shapes=False)
    assert a["pocket_clearance"]["fill"] == "bonded"
    for x, y in zip(sorted(m["f_hz"] for m in a["modes"]),
                    sorted(m["f_hz"] for m in b["modes"])):
        assert x == pytest.approx(y, rel=0.05)


# ---------------------------------------------------------------------------
# the refusals of the fill
# ---------------------------------------------------------------------------

def test_two_magnets_sharing_pocket_air_are_refused():
    raw = _polys(0.0)
    a = Polygon([(MAG_R0, -4.0), (MAG_R1, -4.0), (MAG_R1, -0.05), (MAG_R0, -0.05)])
    b = Polygon([(MAG_R0, 0.05), (MAG_R1, 0.05), (MAG_R1, 4.0), (MAG_R0, 4.0)])
    pocket = Polygon([(MAG_R0, -4.0), (MAG_R1, -4.0), (MAG_R1, 4.0),
                      (MAG_R0, 4.0)]).buffer(CLR, join_style=2)
    disk = Point(0, 0).buffer(R_OD, quad_segs=128)
    rotor = disk.difference(Point(0, 0).buffer(R_BORE, quad_segs=96)).difference(pocket)
    polys = dict(raw, rotor=rotor, magnets=[(a, 1), (b, 1)])
    with pytest.raises(ValueError, match="share pocket air"):
        pkc.mechanical_polys(polys, {"magnet_pocket_clearance": CLR})


def test_magnets_with_different_mass_scales_are_refused():
    raw = _polys(CLR)
    small = affinity.scale(raw["magnets"][1][0], 0.5, 0.5)
    disk = Point(0, 0).buffer(R_OD, quad_segs=128)
    rotor = disk.difference(Point(0, 0).buffer(R_BORE, quad_segs=96))
    mags = [m for m, _ in raw["magnets"]]
    mags[1] = small
    for m in mags:
        rotor = rotor.difference(m.buffer(CLR, join_style=2))
    polys = dict(raw, rotor=rotor, magnets=[(m, 1) for m in mags])
    with pytest.raises(ValueError, match="pockets differ"):
        pkc.mechanical_polys(polys, {"magnet_pocket_clearance": CLR})


def test_more_air_than_the_clearance_and_up_gap_explain_is_refused():
    disk = Point(0, 0).buffer(R_OD, quad_segs=128)
    rotor = disk.difference(Point(0, 0).buffer(R_BORE, quad_segs=96))
    mags = []
    for k in range(N_POLES):
        slab = _slab(360.0 * k / N_POLES)
        rotor = rotor.difference(slab.buffer(CLR + 0.1, join_style=2))
        mags.append((slab, 1))
    polys = {"rotor": rotor, "magnets": mags, "sleeve": None, "shaft": None,
             "sleeve_r_mm": (0.0, 0.0)}
    with pytest.raises(ValueError, match="more than the"):
        pkc.mechanical_polys(polys, {"magnet_pocket_clearance": CLR,
                                     "magnet_up_gap": 0.1})


def test_pair_gaps_fail_closed_on_a_mismatched_pair_table():
    from types import SimpleNamespace
    cs = SimpleNamespace(n_pairs=3, pair_unilateral=np.ones(2, bool),
                         interfaces=[], pair_iface=np.zeros(3, int))
    with pytest.raises(ValueError, match="unilateral"):
        pkc.pair_gaps(cs, _mech(CLR))


# ---------------------------------------------------------------------------
# what the answer reports
# ---------------------------------------------------------------------------

def test_lift_off_of_a_drawn_clearance_is_not_applicable(clr_out):
    assert clr_out["lift_off_na"] == {"magnet_rotor": "n/a: drawn clearance"}
    assert clr_out["lift_off_rpm"]["magnet_rotor"] is None


def test_the_magnet_stresses_are_read_inside_the_drawn_magnet():
    mp = _open_mech()                    # the plug in the opening is fill only
    rm = rs.build_rotor_mesh(mp, mesh_size_mm=1.0)
    mask = pkc.drawn_mask(rm.mesh, rm.part_tri, mp, ctc.PART_MAGNET)
    mag = rm.part_tri == ctc.PART_MAGNET
    assert (mag & ~mask).any() and (mag & mask).any()
    assert mask[~mag].all()
    assert pkc.drawn_mask(rm.mesh, rm.part_tri, _polys(0.0), ctc.PART_MAGNET) is None
    out = rs.solve_rotor_stress(
        mp, ASSIGN, RPM, 1.0, 0.0, stack_length_mm=STACK_MM, mesh_size_mm=1.0,
        order=1, with_field=True, lift_off_solves=0, case_mode="single",
        loads="centrifugal")
    fld = out["field"]["cases"][out["primary_case"]]
    assert sum(fld["pocket_fill_per_tri"]) > 0


@pytest.mark.parametrize("which", ["closed", "open"])
def test_every_seated_magnet_is_in_equilibrium(which, clr_out):
    """Per magnet: |load + contact forces on it| / |load| < 1e-3 — each magnet
    in a drawn clearance is held by its pairs alone."""
    out = clr_out if which == "closed" else _solve(_open_mech(), cases="three")
    for case in ("rated", "overspeed"):
        eq = out["cases"][case]["contact"]["magnet_equilibrium"]
        assert len(eq["per_magnet_rel"]) == N_POLES
        assert eq["max_rel"] < 1e-3, (case, eq)
    assert "magnet_equilibrium" not in out["cases"]["standstill"]["contact"]
