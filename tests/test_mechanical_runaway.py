"""A rotor whose piece is held by nothing is not a solved rotor (2026-09-09).

The 40 mm spoke rotor (fourteen magnets between pole pieces, a 0.1 mm gap above
each magnet, parallel side walls) was solved with the Ø200's ``separation``
magnet–rotor contact.  The magnets slid outward, the active set cycled, the
maximum displacement came out at 1.6e11 µm — and the route still returned
SF 0.08 on the magnets and a stress map with one 2 GPa corner element, which
the user read as a real result ("напряжения должны быть распределены
равномерно").  The solver had already written "a piece of the rotor is held by
nothing and ran away" in ``torque_path`` beside it.  Such a solve is now
refused by name, at the solver and at the route.

The geometry here is the spoke rotor of ``test_mechanical_part_temps`` with the
pocket cut ``GAP`` longer than the magnet: the magnet's outer face floats
``GAP`` short of the pocket bottom, exactly the 40 mm's ``magnet_up_gap``.

Later the same day the solver learned to SEAT a loose part — to travel it onto
the surface that retains it rather than pin it where it floated (see
tests/test_mechanical_seating.py, "магнит должен сесть на язычок, как в Fusion").
That does NOT soften anything here.  Seating moves a part onto a contact pair it
already has, and a DRAWN gap has none: the 0.1 mm sliver is in no part's polygon,
so it is not meshed and the two faces across it are both free surfaces.  A
clearance the machine CREATES — the same pocket opened by temperature — is on a
shared boundary and does seat; both cases are pinned below, side by side, because
the difference between them is the whole rule.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from shapely import affinity
from shapely.geometry import Point, Polygon

from motor_ai_sim.simulation.mechanical import contact as ctc
from motor_ai_sim.simulation.mechanical import rotor_stress as rs

R_BORE, R_OD = 20.0, 50.0                 # mm
MAG_W, MAG_R0, MAG_R1 = 8.0, 26.0, 46.0   # mm — slab width, inner and outer r
N_POLES = 4
GAP = 0.1                                  # mm, the pocket past the magnet's top
RPM = 20000.0

ASSIGN = {"rotor_core": "20SW1200", "magnet": "F52SH_120C",
          "sleeve": "HM63_UD_60", "shaft": "Aluminium_7075"}


def _polys(gap_mm: float):
    disk = Point(0, 0).buffer(R_OD, resolution=128)
    annulus = disk.difference(Point(0, 0).buffer(R_BORE, resolution=96))
    magnets, rotor = [], annulus
    for k in range(N_POLES):
        ang = 360.0 * k / N_POLES
        slab = Polygon([(MAG_R0, -MAG_W / 2), (MAG_R1, -MAG_W / 2),
                        (MAG_R1, MAG_W / 2), (MAG_R0, MAG_W / 2)])
        pocket = Polygon([(MAG_R0, -MAG_W / 2), (MAG_R1 + gap_mm, -MAG_W / 2),
                          (MAG_R1 + gap_mm, MAG_W / 2), (MAG_R0, MAG_W / 2)])
        magnets.append((affinity.rotate(slab, ang, origin=(0, 0)),
                        1 if k % 2 == 0 else -1))
        rotor = rotor.difference(affinity.rotate(pocket, ang, origin=(0, 0)))
    return {"rotor": rotor, "magnets": magnets, "sleeve": None, "shaft": None,
            "sleeve_r_mm": (0.0, 0.0)}


def _solve(gap_mm: float, contact: str = "separation", mu: float = 0.0,
           part_temps=None):
    # A map is only a load here under the solver's verification model: the
    # production rule (2026-09-09, "температура только как изменение давления
    # на бандаж") solves a sleeveless rotor cold whatever it is given.
    return rs.solve_rotor_stress(
        _polys(gap_mm), ASSIGN, RPM, 1.0, 0.0, stack_length_mm=50.0,
        mesh_size_mm=2.0, order=1, with_field=False,
        contacts={"magnet_rotor": ctc.ContactSpec(contact, mu)},
        lift_off_solves=0, case_mode="single", loads="centrifugal",
        part_temps_c=part_temps,
        thermal_model="free_expansion" if part_temps else "band_fit")


def test_the_verdict_rule():
    ok = rs.runaway_verdict(0.6e-3, 0.1, [], 1.0, {})          # the Ø200 at speed
    assert ok is None
    ran = rs.runaway_verdict(1.6e5, 0.0118, [], None,
                             {"magnet_rotor": {"type": "separation", "n_facets": 252,
                                               "open_fraction": 0.966},
                              "shaft_rotor": {"type": "bonded", "n_facets": 256,
                                              "open_fraction": 0.0}})
    assert ran["pair"] == "magnet_rotor" and ran["open_fraction"] == pytest.approx(0.966)
    assert any("displacement" in r for r in ran["reasons"])
    # the torque identity alone is enough…
    assert rs.runaway_verdict(1e-5, 0.1, [], 0.5, {}) is not None
    # …and so is a part with no load path
    assert rs.runaway_verdict(1e-5, 0.1, ["magnet"], None, {}) is not None
    # a contact loop that did not converge is not, by itself, a runaway
    assert rs.runaway_verdict(1e-5, 0.1, [], 0.9999, {}) is None


def test_a_magnet_floating_short_of_its_pocket_bottom_is_refused():
    """A DRAWN 0.1 mm of air above the magnet is still the refusal (2026-09-09).

    SEATING (tests/test_mechanical_seating.py) travels a loose part onto the
    surface that retains it — but only onto a contact pair it already has, and
    this fixture has none facing outward: the 0.1 mm sliver belongs to no part's
    polygon, so it is not meshed and the magnet's outer face is a free surface
    looking at another free surface.  "Not retained" therefore stays the answer
    here, and the route's bonded fallback (a glued magnet, which is what the
    built 40 mm has) stays what it does about it.
    """
    with pytest.raises(rs.RotorRanAway) as ei:
        _solve(GAP)
    exc = ei.value
    assert exc.pair == "magnet_rotor"
    assert "held by nothing" in str(exc) and "bonded" in str(exc)
    assert exc.rpm == pytest.approx(RPM)


def test_the_same_pocket_loosened_by_HEAT_seats_instead_of_running_away():
    """The G2-L40's case, and the counterpart of the test above (2026-09-09).

    Cut the pocket to the magnet — the zero die clearance the CILN28 has — and
    open it with TEMPERATURE instead of with a pencil: the iron grows 12 ppm/K
    against the magnet's 5, every pair goes tensile, and the magnet is loose by
    tens of microns.  That clearance is on a SHARED boundary, so the pair the
    magnet has to land on exists, and it travels onto it instead of running
    away.  User: "магнит должен сесть на язычок, как в Fusion".
    """
    out = _solve(0.0, part_temps={"rotor_core": 150.0, "magnet": 20.0,
                                  "shaft": 20.0, "sleeve": 20.0})
    case = out["cases"][out["primary_case"]]
    seated = case["contact"]["seated"]
    assert len(seated) == N_POLES and {s["part"] for s in seated} == {"magnet"}
    assert all(10.0 < s["travel_um"] < 200.0 for s in seated), seated
    assert case["contact"]["unretained_parts"] == []
    assert case["max_displacement_um"] < 0.01 * R_OD * 1e3
    assert "seated on the pocket tab" in case["magnet_retention"]["verdict"]


def test_the_same_pocket_glued_is_an_elastic_answer():
    out = _solve(GAP, contact="bonded")
    case = out["cases"][out["primary_case"]]
    # a real rotor grows ~1e-3 of its radius; the tripwire sits at 1e-1
    assert case["max_displacement_um"] < 0.01 * R_OD * 1e3
    assert case["contact"]["unretained_parts"] == []
    assert case["sf_min_p05"] > 0.0


def test_the_same_pocket_closed_holds_the_magnet_with_separation():
    out = _solve(0.0)
    case = out["cases"][out["primary_case"]]
    assert case["max_displacement_um"] < 0.01 * R_OD * 1e3
    assert case["interfaces"]["magnet_rotor"]["open_fraction"] < 0.9


def test_the_route_solves_an_unretained_separation_joint_bonded_and_says_so(monkeypatch):
    """2026-09-09 morning, the G2: "rotor stress at 3,000 rpm did not solve — a
    piece of the rotor is held by nothing".  On a built machine an unretained
    magnet is a glued magnet, so the route re-solves that joint bonded and
    reports the substitution instead of refusing a machine that works."""
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app

    calls = []

    def fake_solve(polys, assign, rpm, osf, interf, **kw):
        specs = {k: v.type for k, v in kw["contacts"].items()}
        calls.append(specs)
        if specs.get("magnet_rotor") == "separation":
            raise rs.RotorRanAway("3,000 rpm", 3000.0, "magnet_rotor", 0.93,
                                  ["maximum displacement 4.09e+05 mm on a Ø146.7 mm rotor"])
        return {"primary_case": "3,000 rpm", "rpm": 3000.0, "cases": {
            "3,000 rpm": {"sf_min": 2.4, "sf_min_part": "rotor", "sf_min_p05": 6.1,
                          "rotor_od_growth_um": 95.0}},
            "interference_effective_mm": 0.0, "contacts": {
                k: {"type": t, "mu": 0.0, "n_facets": 1} for k, t in specs.items()},
            "mesh": {"n_nodes": 1, "n_triangles": 1, "mesh_size_mm": 1.5}}
    monkeypatch.setattr(rs, "solve_rotor_stress", fake_solve, raising=True)
    monkeypatch.setattr(rs, "cache_get", lambda key: None, raising=True)
    monkeypatch.setattr(rs, "cache_put", lambda key, out: None, raising=True)
    r = TestClient(app).get("/api/mechanical/rotor_stress",
                            params={"cases": "single", "rpm": 3000, "loads": "centrifugal",
                                    "field": "false"})
    assert r.status_code == 200, r.text[:400]
    d = r.json()
    assert [c["magnet_rotor"] for c in calls] == ["separation", "bonded"]
    fb = d["contact_fallback"]
    assert fb["pair"] == "magnet_rotor" and fb["to"] == "bonded"
    assert "glued" in fb["reason"] and "93%" in fb["reason"]
    assert d["cases"]["3,000 rpm"]["sf_min"] == pytest.approx(2.4)


def test_every_floating_separation_joint_is_bonded_in_turn(monkeypatch):
    """User 2026-09-09 ("я везде сделал separation"): magnet AND shaft joints
    separation, µ = 0 on the shaft — the magnet floats first, then the hub
    opens off the fit-less shaft.  Each runaway bonds the joint it names; the
    record lists both, in order."""
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app

    calls = []

    def fake_solve(polys, assign, rpm, osf, interf, **kw):
        specs = {k: v.type for k, v in kw["contacts"].items()}
        calls.append(dict(specs))
        for pair, frac in (("magnet_rotor", 0.93), ("shaft_rotor", 0.71)):
            if specs.get(pair) == "separation":
                raise rs.RotorRanAway("3,000 rpm", 3000.0, pair, frac, ["x"])
        return {"primary_case": "3,000 rpm", "rpm": 3000.0, "cases": {
            "3,000 rpm": {"sf_min": 1.7, "sf_min_part": "rotor"}},
            "interference_effective_mm": 0.0, "contacts": {
                k: {"type": t, "mu": 0.0, "n_facets": 1} for k, t in specs.items()},
            "mesh": {"n_nodes": 1, "n_triangles": 1, "mesh_size_mm": 1.5}}
    monkeypatch.setattr(rs, "solve_rotor_stress", fake_solve, raising=True)
    monkeypatch.setattr(rs, "cache_get", lambda key: None, raising=True)
    monkeypatch.setattr(rs, "cache_put", lambda key, out: None, raising=True)
    r = TestClient(app).get("/api/mechanical/rotor_stress", params={
        "cases": "single", "rpm": 3000, "loads": "centrifugal", "field": "false",
        "contacts": '{"magnet_rotor": {"type": "separation", "mu": 0.2}, '
                    '"shaft_rotor": {"type": "separation", "mu": 0}}'})
    assert r.status_code == 200, r.text[:400]
    assert [(c["magnet_rotor"], c["shaft_rotor"]) for c in calls] == [
        ("separation", "separation"), ("bonded", "separation"), ("bonded", "bonded")]
    fb = r.json()["contact_fallback"]
    assert fb["pairs"] == ["magnet_rotor", "shaft_rotor"]
    assert fb["pair"] == "magnet_rotor" and len(fb["entries"]) == 2
    assert r.json()["contacts"]["shaft_rotor"]["type"] == "bonded"


def test_a_second_runaway_is_still_the_refusal(monkeypatch):
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app

    def always(*a, **k):
        raise rs.RotorRanAway("3,000 rpm", 3000.0, "magnet_rotor", 0.5, ["x"])
    monkeypatch.setattr(rs, "solve_rotor_stress", always, raising=True)
    monkeypatch.setattr(rs, "cache_get", lambda key: None, raising=True)
    r = TestClient(app).get("/api/mechanical/rotor_stress",
                            params={"cases": "single", "rpm": 3000, "loads": "centrifugal"})
    assert r.status_code == 422
    assert r.json()["detail"]["invalid_parameters"][0]["field"] == "contacts.magnet_rotor"


def test_the_route_turns_it_into_a_422_naming_the_pair(monkeypatch):
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app

    def boom(*a, **k):
        raise rs.RotorRanAway("23,000 rpm", 23000.0, "magnet_rotor", 0.966,
                              ["maximum displacement 1.6e+05 mm on a Ø23.6 mm rotor"])
    # The route imports the solver module inside the handler, so the module's
    # own attributes are what it calls.
    monkeypatch.setattr(rs, "solve_rotor_stress", boom, raising=True)
    monkeypatch.setattr(rs, "cache_get", lambda key: None, raising=True)
    r = TestClient(app).get("/api/mechanical/rotor_stress",
                            params={"cases": "single", "rpm": 23000, "loads": "centrifugal"})
    assert r.status_code == 422, r.text[:400]
    d = r.json()["detail"]
    assert "held by nothing" in d["error"]
    assert d["invalid_parameters"][0]["field"] == "contacts.magnet_rotor"
    assert d["invalid_parameters"][0]["kind"] == "unretained_part"


# ---------------------------------------------------------------------------
# WHICH joint the refusal blames (2026-09-09)
# ---------------------------------------------------------------------------
# The route bonds the joint this verdict names and solves again, so naming the
# wrong one glues a part that was never the problem.  On the live Ø200 the SHAFT
# floated — its hub contact was `separation` with no interference, which holds
# nothing — while the widest-open joint on the rotor was magnet↔iron; the
# fallback glued the MAGNETS, handed their centrifugal load to the iron, and the
# band's stress fell from 1717 MPa to 220 while the displacement fell from
# 421 µm to 13.  A design read as safe because the wrong joint was glued is the
# one failure this fallback must never produce (user: "так у нас всё раздельно").

def _ifaces(**open_by_pair):
    return {lb: {"type": "separation", "n_facets": 200, "open_fraction": of}
            for lb, of in open_by_pair.items()}


def test_the_blamed_joint_is_the_floating_part_s_own():
    v = rs.runaway_verdict(
        1e-3, 0.1, ["shaft"], None,
        _ifaces(magnet_rotor=0.93, shaft_rotor=0.20, sleeve_rotor=0.05))
    assert v is not None
    assert v["pair"] == "shaft_rotor", "the shaft floated, not the magnets"
    assert v["open_fraction"] == pytest.approx(0.20)


def test_a_floating_magnet_still_blames_the_magnet_joint():
    v = rs.runaway_verdict(
        1e-3, 0.1, ["magnet"], None,
        _ifaces(magnet_rotor=0.60, shaft_rotor=0.99))
    assert v["pair"] == "magnet_rotor"


def test_a_part_that_seating_gave_up_on_is_blamed_the_same_way():
    v = rs.runaway_verdict(
        1e-5, 0.1, [], None, _ifaces(magnet_rotor=0.30, shaft_rotor=0.95),
        seating_capped=["magnet"])
    assert v["pair"] == "magnet_rotor"


def test_with_no_part_named_the_widest_open_joint_is_still_blamed():
    """The displacement tripwire alone says nothing about WHICH part — there the
    open fraction is the only evidence there is, and the rule is unchanged."""
    # 20 mm on a 100 mm radius: past the displacement tripwire, which is the
    # only reason that names no part at all.
    v = rs.runaway_verdict(
        2e-2, 0.1, [], None, _ifaces(magnet_rotor=0.93, shaft_rotor=0.20))
    assert v is not None
    assert v["pair"] == "magnet_rotor"


def test_a_free_part_with_no_separation_joint_falls_back_to_the_open_rule():
    """The rotor itself has no joint named after it here: nothing to bond, so
    the refusal stands on the widest-open joint and the route re-raises."""
    v = rs.runaway_verdict(
        2e-2, 0.1, ["rotor"], None, _ifaces(magnet_rotor=0.93, shaft_rotor=0.20))
    assert v["pair"] in ("magnet_rotor", "rotor_shaft", "shaft_rotor")


# ─────────────────────────────────────────────────────────────────────────────
# 2026-09-21.  The owner hit this refusal on production with a machine that was
# not a runaway at all: CIANO14 50 edited / L15 — Ø50, 14 poles, rotor_hole 1,
# magnet_up_gap 0.1, i.e. the straight-sided pocket with the magnets RECESSED
# 0.1 mm below the rotor surface («мне нужно сделать запас magnet_up_gap = 0.1,
# чтобы магниты не выскочили наружу, я должен проверить деформации»).  The
# magnets are retained by the wedge of the pocket's side walls, exactly as at
# magnet_up_gap = 0.
#
# The geometry was right and the MESH was wrong.  The magnet's corner fillet
# arrived at the straight pocket wall tangentially, and the few-µm cusp that
# leaves is finer than gmsh's boolean tolerance: the mechanical mesh came out
# with 22 triangles of 6e-19 mm² at 0.00°.  A zero-area element has a singular
# element stiffness matrix, and with those in the assembly every contact pair
# closed to 2e-16 µm, the rotor deformed 0.05 µm instead of 6.2, the bore
# reacted 0.5 % of the applied torque — and `runaway_verdict`, correctly, would
# not grade that.  Three fixes, all in the mesh path:
#   * `cadquery_geometry._open_fillet_at_top`'s "on the top circle" band is
#     1.2 × the ring sanitiser's weld instead of a flat 0.06 mm, so the cusp is
#     opened on small machines too (the Ø200 is unchanged to the bit);
#   * `_build_rotor_mesh`'s node weld buckets with `floor` and scans all eight
#     neighbours, so a 15 µm pair straddling a bucket edge welds;
#   * whatever collinear triple still survives is dropped as an element.
# The geometry tests are in tests/test_pocket_straight_sides.py section (h).
# ─────────────────────────────────────────────────────────────────────────────

CIANO50 = {
    "stator_diameter": 50.0, "slot_height": 7.5, "core_thickness": 2.4,
    "num_seg": 2, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.25, "tooth_width": 4.2, "tooth2_width": 2.1, "cut_width": 1.2,
    "insulation_thickness": 0.06, "wire_width": 3.0, "wire_height": 0.5,
    "wire_spacing_x": 0.07, "wire_spacing_y": 0.1, "num_wires_per_slot": 11,
    "wire_split": 1, "wire_parallel": 1, "slot_hs": 0.267,
    "magnet_height": 7.0, "rotor_house_height": 1.3, "shaft_height": 2.0,
    "magnet_fill_down": 0.87, "magnet_fill_up": 0.34, "magnet_fill_radius": 0.2,
    "magnet_up_gap": 0.1, "rotor_hole": 1.0, "magnet_down_height": 1.5,
    "magnet_lamination": 0, "stator_fillet_r": 1.5, "stator_fillet_r1": 0.1,
    "rotor_fill_r": 0.2, "motor_length": 15.0, "sleeve_thickness": 0.0,
    "rotor_outer_radius": 14.85, "rotor_inner_radius": 6.55,
}

CIANO50_ASSIGN = {"rotor_core": "20SW1200", "magnet": "N52UH_150C",
                  "shaft": "Aluminium_7075"}


def _ciano50_polys(hole: float, gap: float):
    from motor_ai_sim.cadquery_geometry import CadQueryMotor

    motor = CadQueryMotor()
    motor.set_parameters(dict(CIANO50, rotor_hole=hole, magnet_up_gap=gap))
    return motor.get_2d_polygons(0.0)


def _solve_ciano50(hole: float, gap: float, rpm: float = 20000.0):
    """Coarse mesh, P1, spin only — this asks whether the machine SOLVES, not
    what its safety factor is (that is the owner's own run)."""
    return rs.solve_rotor_stress(
        _ciano50_polys(hole, gap), CIANO50_ASSIGN, rpm, 1.0, 0.0,
        stack_length_mm=float(CIANO50["motor_length"]),
        mesh_size_mm=3.0, order=1, with_field=False,
        contacts={"magnet_rotor": ctc.ContactSpec("separation", 0.2)},
        lift_off_solves=0, case_mode="single", loads="centrifugal")


@pytest.mark.parametrize("hole", [0.9, 1.0])
@pytest.mark.parametrize("gap", [0.0, 0.05, 0.1, 0.3])
def test_the_recessed_magnet_solves_at_every_gap(hole, gap):
    """The pair the owner needs: any magnet_up_gap on either pocket.

    Solved rather than refused; no part left unretained; and the displacements
    an elastic answer instead of the 0.05 µm frozen state the degenerate mesh
    produced.
    """
    out = _solve_ciano50(hole, gap)
    case = out["cases"][out["primary_case"]]
    assert case["contact"]["unretained_parts"] == []
    assert case["contact"]["n_bodies"] == 1
    u = case["max_displacement_um"]
    assert 0.3 < u < 0.1 * CIANO50["rotor_outer_radius"] * 1e3, (
        f"rotor_hole {hole}, magnet_up_gap {gap}: |u|max = {u} µm is not an "
        f"elastic answer for a Ø29.7 mm rotor at 20 000 rpm")
    assert case["parts"]["magnet"]["safety_factor"] > 1.0, (
        "the magnet is crushed in its own pocket — the jam the degenerate "
        "elements produced")
    iface = case["interfaces"]["magnet_rotor"]
    # a joint that is 100 % closed with a 2e-16 µm gap is the frozen state, not
    # a contact: on a spinning rotor part of it opens and the rest carries.
    assert iface["gap_max_um"] > 1e-3, iface


@pytest.mark.parametrize("gap", [0.05, 0.1, 0.3])
def test_the_recessed_magnets_are_held_by_the_pocket_walls(gap):
    """Retention is the WEDGE, at every recess — the same answer as at gap 0.

    The recess takes no load path away: the magnet is held by the converging
    side walls either way, and the verdict line has to say so, because it is the
    sentence the owner reads in the report.
    """
    verdicts = {}
    for g in (0.0, gap):
        case = _solve_ciano50(1.0, g)["cases"]["20,000 rpm"]
        rep = case["magnet_retention"]
        verdicts[g] = rep["verdict"]
        assert "wedge" in rep["verdict"], rep["verdict"]
        assert max(rep["share"].values()) > 0.5, rep["share"]
    assert verdicts[0.0].split("—")[0] == verdicts[gap].split("—")[0]
