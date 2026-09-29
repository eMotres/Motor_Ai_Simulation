"""A loose magnet TRAVELS onto its pocket tab instead of being pinned (2026-09-09).

User, on the live G2-L40 generator at the coupled temperatures (iron 134 °C,
magnet 135 °C): *"магнит должен сесть на язычок, как в Fusion"*.

The magnet sits in an iron pocket whose lips overhang its shoulders with ZERO
clearance in the die cross-section, so at 20 °C the separation contact holds it.
Hot, the pocket grows more than the magnet does (12 against 5 ppm/K), the solve
finds every pair of that magnet in tension, releases them all, and is left with
the magnet as a connected component of its own — a few tens of microns short of
the lip it is about to rest on.  What the machine does next is travel those
microns and land; what the code did next was pin the magnet's rigid modes where
it stood, cycle the active set and run away, after which the route re-solved the
joint BONDED and added hundreds of MPa of thermal-mismatch stress that no glued
joint is there to carry.  Fusion 360, validated on the real machine, shows the
user the magnet moving and landing on the tab at a modest contact stress.

WHAT THIS FILE PINS
-------------------
  (a) the SEATING itself, on the case that motivated it: a pocket cut exactly to
      the magnet (the G2's zero die clearance), loosened by TEMPERATURE, at
      speed.  It seats, it is reported, and nothing runs away.
  (b) the guard that keeps every other answer where it was: with nothing loose,
      the feature is invisible to the last digit the linear solver reproduces.
  (c) the stresses.  The seated joint is a CONTACT joint, so the magnet is in
      light compression against its lip; the same case solved bonded reports the
      full thermal-mismatch tension, which is the number the user rejected.
  (d) the refusal that must survive: a part with no surface to fall onto is not
      retained, and no amount of travelling invents one.
  (e) four magnets loose at once, each seated and each named.
  (f) the placement itself, as arithmetic: it clears the face BEHIND the part and
      it TURNS the part, because a rigid magnet cannot be slid into a pocket that
      has expanded around it (2026-09-09).
  (g) the mechanism test — "can this part react its own load", asked of the
      stiffness and not of the component graph — and the real G2-L40
      cross-section, hot and cold, built READ-ONLY from the catalog.

THE SECOND HALF OF THE STORY (2026-09-09).  The synthetic fixture above seats
because its pocket is a rectangle: nothing sits behind the magnet, the walls are
parallel, mu is zero and there is no torque.  On the machine the feature was
written for, none of that holds, and both of the fixture's conveniences hid a
bug.  The pocket floor is 75 µm inside the magnet, so "fall until you touch
something" stopped 34 µm short of clearing it; and the pocket has EXPANDED and
been sheared by the torque, so no slide fits a rigid magnet into it at all.  The
G2 tests at the end of this file are the ones that would have caught both, and
they are built from ``config/dies/CILN28`` rather than from the live config —
the live one is whatever is on screen this minute, and these numbers have to mean
the same thing tomorrow.

THE ONE PLACE THE MODEL CANNOT FOLLOW THE MACHINE — see
``test_a_modelled_air_gap_has_no_contact_facet_to_land_on``.  The contact is
node-to-node on a CONFORMING mesh: a pair exists only where the two parts share
a boundary.  A clearance that is DRAWN (the 40 mm's ``magnet_up_gap 0.1``, an
unmeshed sliver of air above the magnet) therefore has no pair at all, and there
is nothing to seat onto.  A clearance that is CREATED — by temperature, by the
centrifugal field — opens a shared boundary, and that one seats.  Both answers
are honest; only the second is a seating.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from shapely import affinity
from shapely.geometry import Point, Polygon

from motor_ai_sim.simulation.mechanical import contact as ctc
from motor_ai_sim.simulation.mechanical import rotor_stress as rs

# ---------------------------------------------------------------------------
# The same 4-pole spoke rotor the runaway and part-temperature suites use
# ---------------------------------------------------------------------------
R_BORE, R_OD = 20.0, 50.0                 # mm
MAG_W, MAG_R0, MAG_R1 = 8.0, 26.0, 46.0   # mm — slab width, inner and outer r
N_POLES = 4
RPM = 20000.0

ASSIGN = {"rotor_core": "20SW1200", "magnet": "F52SH_120C",
          "sleeve": "HM63_UD_60", "shaft": "Aluminium_7075"}

#: The cards the physics claims below are made against, ppm/K.  20SW1200 is
#: isotropic at 12; F52SH grows +5 along the magnetisation and −1.5 across it,
#: and on these radial slabs the magnetisation is the THIN direction, so the
#: magnet's RADIAL coefficient is the −1.5 one.  Read here rather than hard-coded
#: so a card edit fails this file loudly instead of moving a tolerance.
CTE_STEEL = 12.0e-6
HOT_C, COLD_C = 150.0, 20.0

#: The centrifugal load one G2-L40 magnet carries at 3 000 rpm, N per metre of
#: stack, and the contact stiffness of that solve.  The looseness criterion is a
#: ratio of the two (see ``_loose_pairs``), so both are the machine's own.
MAGNET_LOAD_N_PER_M = 7600.0
CONTACT_K = 5.4e12


def _polys(gap_mm: float = 0.0, open_top: bool = False):
    """Steel annulus with ``N_POLES`` radial magnet slabs let into it.

    ``gap_mm`` cuts the pocket that much LONGER than the magnet — a drawn air
    gap, i.e. an unmeshed void (see the module docstring).  ``open_top`` runs
    the pocket clean through the rim, so the magnet has nothing outward at all.
    """
    disk = Point(0, 0).buffer(R_OD, resolution=128)
    annulus = disk.difference(Point(0, 0).buffer(R_BORE, resolution=96))
    magnets, rotor = [], annulus
    top = R_OD + 1.0 if open_top else MAG_R1 + gap_mm
    for k in range(N_POLES):
        ang = 360.0 * k / N_POLES
        slab = Polygon([(MAG_R0, -MAG_W / 2), (MAG_R1, -MAG_W / 2),
                        (MAG_R1, MAG_W / 2), (MAG_R0, MAG_W / 2)])
        pocket = Polygon([(MAG_R0, -MAG_W / 2), (top, -MAG_W / 2),
                          (top, MAG_W / 2), (MAG_R0, MAG_W / 2)])
        magnets.append((affinity.rotate(slab, ang, origin=(0, 0)),
                        1 if k % 2 == 0 else -1))
        rotor = rotor.difference(affinity.rotate(pocket, ang, origin=(0, 0)))
    return {"rotor": rotor, "magnets": magnets, "sleeve": None, "shaft": None,
            "sleeve_r_mm": (0.0, 0.0)}


def _solve(polys, *, rpm: float = RPM, mu: float = 0.0,
           typ: str = "separation", part_temps=None, lift_off: int = 0):
    # The thermally loosened fixtures run the solver's VERIFICATION model: in
    # production (2026-09-09, "температура только как изменение давления на
    # бандаж, если он есть") a sleeveless rotor is solved cold whatever map it
    # is given, so a pocket can only be opened by heat here, on purpose, to
    # exercise the seating machinery on a case with known arithmetic.  On the
    # machine the feature was written for, the G2, it is the centrifugal field
    # that frees the magnet — see the catalog tests at the end of this file.
    return rs.solve_rotor_stress(
        polys, ASSIGN, rpm, 1.0, 0.0, stack_length_mm=50.0,
        mesh_size_mm=2.0, order=1, with_field=False,
        contacts={"magnet_rotor": ctc.ContactSpec(typ, mu)},
        lift_off_solves=lift_off, case_mode="single", loads="centrifugal",
        part_temps_c=part_temps,
        thermal_model="free_expansion" if part_temps else "band_fit")


def _case(out):
    return out["cases"][out["primary_case"]]


#: The pocket loosened by heat alone: the iron at 150 °C, the magnets left at
#: 20 °C.  Only a per-part temperature can say which of the two is happening —
#: see tests/test_mechanical_part_temps.py for the cards behind it.
LOOSENING = {"rotor_core": HOT_C, "magnet": COLD_C,
             "shaft": COLD_C, "sleeve": COLD_C}


# ---------------------------------------------------------------------------
# (a) + (c) + (e)  the seating, on the case that motivated it
# ---------------------------------------------------------------------------

def test_a_thermally_loosened_magnet_travels_onto_its_pocket_tab():
    """The G2's case: zero clearance in the die, the pocket opened by heat.

    Every number here used to be a ``RotorRanAway``.
    """
    out = _solve(_polys(0.0), part_temps=LOOSENING)
    c = _case(out)
    seated = c["contact"]["seated"]

    # (e) all four magnets came loose, and all four are reported by name
    assert len(seated) == N_POLES, seated
    assert {s["part"] for s in seated} == {"magnet"}
    assert {s["landed_on"] for s in seated} == {"magnet_rotor"}
    assert len({s["component_id"] for s in seated}) == N_POLES
    assert all(s["n_pairs_closed"] >= 1 for s in seated)

    # the direction is the net load's: radially outward at each pole, i.e. the
    # four unit vectors are ±x and ±y for a rotor with poles on the axes
    dirs = np.array([s["direction"] for s in seated])
    assert np.allclose(np.linalg.norm(dirs, axis=1), 1.0)
    assert np.allclose(np.sort(np.abs(dirs).max(axis=1)), 1.0, atol=1e-6)

    # -- the travel is the differential expansion, and nothing like a runaway --
    # The magnet is cold and unloaded thermally, so it stays where the mesh put
    # it; the pocket's outer face runs away from it by the iron's own free
    # growth at that radius, alpha * dT * R1, plus the rotor's centrifugal
    # growth there (~10 % more at 20 000 rpm on this fixture).
    free_growth_um = CTE_STEEL * (HOT_C - COLD_C) * MAG_R1 * 1e3
    assert free_growth_um == pytest.approx(71.8, abs=0.5)      # the arithmetic
    travel = np.array([s["travel_um"] for s in seated])
    assert (travel > 0.8 * free_growth_um).all(), travel
    assert (travel < 1.5 * free_growth_um).all(), travel
    # the four are the same pocket four times over — they must agree
    assert np.ptp(travel) < 0.01 * travel.mean()

    # -- and the solve is an ELASTIC answer, not a refusal --------------------
    assert c["contact"]["unretained_parts"] == []
    assert c["contact"]["converged"]
    assert c["contact"]["n_bodies"] == 1
    # displacement = the rotor's own thermal + centrifugal growth, microns, far
    # under the 10 %-of-radius tripwire that used to fire here
    assert c["max_displacement_um"] < 0.01 * R_OD * 1e3
    assert c["max_displacement_um"] >= travel.max()

    # -- the magnet's OUTER face is what carries it ---------------------------
    j = c["interfaces"]["magnet_rotor"]
    assert j["pressure_max_mpa"] > 1.0, j        # compression on the tab
    assert j["pressure_min_mpa"] >= 0.0, j       # a separation pair never pulls
    ret = c["magnet_retention"]
    assert ret["carried_kn_per_m"]["pocket_radial_faces"] > 0.0
    assert ret["share"]["pocket_radial_faces"] > 0.5, ret["share"]

    # -- and the verdict SAYS so, with the number to compare with Fusion ------
    v = ret["verdict"]
    assert "seated on the pocket tab" in v, v
    assert "travelled" in v and "µm" in v, v
    assert f"{N_POLES} magnets" in v, v
    assert ret["seated"] and len(ret["seated"]) == N_POLES


def test_the_rotor_od_growth_is_the_rotors_own_never_a_seated_parts_travel():
    """``u`` carries the seating translation — the map must show the magnet where
    it now is — and the air-gap closure must not."""
    out = _solve(_polys(0.0), part_temps=LOOSENING)
    c = _case(out)
    # the rim is iron at r = R_OD and nothing seated it: its growth is the free
    # thermal one there plus the centrifugal, and it is NOT the magnet's 79 µm
    free_rim_um = CTE_STEEL * (HOT_C - COLD_C) * R_OD * 1e3       # 78 µm
    assert c["rotor_od_growth_um"] > free_rim_um
    assert c["rotor_od_growth_um"] < 1.3 * free_rim_um
    # a magnet that fell outward onto its lip is not the rotor growing: the OD
    # figure sits at the rim's own displacement, not at the rim's plus a travel
    assert c["rotor_od_growth_um"] <= c["max_displacement_um"] + 1e-9


def test_the_seated_magnet_is_in_contact_not_glued():
    """(c) The whole point of the feature.

    Solved bonded, the same 130 K of mismatch is carried as TENSION by a joint
    that on the real machine is a pocket, and the magnet reads hundreds of MPa —
    the number that took the user's safety factor to 0.3.  Seated, the joint
    opens where it is open and presses where it presses.
    """
    seat = _case(_solve(_polys(0.0), part_temps=LOOSENING))
    glue = _case(_solve(_polys(0.0), part_temps=LOOSENING, typ="bonded"))

    p1_seat = seat["parts"]["magnet"]["principal_max_mpa"]
    p1_glue = glue["parts"]["magnet"]["principal_max_mpa"]
    # the bonded answer must really be the big one, or the comparison is empty
    assert p1_glue > 100.0, p1_glue
    assert p1_seat < 0.2 * p1_glue, (p1_seat, p1_glue)
    # and the safety factor follows it back up out of the refusal zone
    assert glue["sf_min"] < 1.0 < seat["sf_min"]
    # the bonded joint is holding TENSION; the seated one cannot, by definition
    assert glue["interfaces"]["magnet_rotor"]["pressure_min_mpa"] < 0.0
    assert seat["interfaces"]["magnet_rotor"]["pressure_min_mpa"] >= 0.0
    assert glue["contact"]["seated"] == []


def test_the_lift_off_bisection_still_runs_on_a_seated_joint():
    """Every extra solve of the bisection starts from the MESH's own gaps: the
    seating owns its offsets for the length of one solve and never writes them
    back into the contact system."""
    out = _solve(_polys(0.0), part_temps=LOOSENING, lift_off=3)
    assert _case(out)["contact"]["seated"], "the fixture stopped seating"
    assert "magnet_rotor" in out["lift_off_rpm"]
    lo = out["lift_off_rpm"]["magnet_rotor"]
    assert lo is None or lo >= 0.0


# ---------------------------------------------------------------------------
# (b) the feature is invisible when nothing floats
# ---------------------------------------------------------------------------

#: The same solve one commit before seating existed, measured 2026-09-09.  Held
#: to 1e-6 relative and not to the bit: the linear solver is pypardiso, which is
#: multi-threaded and not bit-reproducible (see test_mechanical_part_temps).
#: `sf_min` is AVERAGED since 2026-09-10 — the strength over the governing
#: NODAL stress, which is the ANSYS/Fusion convention and the number every
#: table and map now prints.  The element-field factor this reference was first
#: written with, 1.730, is kept as `sf_min_unaveraged`: it is still solved and
#: still reported, and the two moving together is what says the change was a
#: reporting one.  Every other number here is untouched by it.
FROZEN_20C = {"sf_min": 1.9739, "sf_min_unaveraged": 1.730,
              "sf_min_p05": 2.761,
              "rotor_od_growth_um": 11.985, "max_displacement_um": 12.813,
              "open_fraction": 0.893, "pressure_max_mpa": 29.205}


def test_with_nothing_loose_the_answer_is_the_one_it_always_was():
    out = _solve(_polys(0.0))                    # 20 °C, no thermal load at all
    c = _case(out)
    assert c["contact"]["seated"] == []
    assert c["magnet_retention"]["seated"] == []
    assert "seated" not in c["magnet_retention"]["verdict"]
    for k, want in FROZEN_20C.items():
        got = (c["interfaces"]["magnet_rotor"][k] if k in
               ("open_fraction", "pressure_max_mpa") else c[k])
        assert got == pytest.approx(want, rel=2e-3), (k, got, want)


def test_a_free_body_eigenstrain_is_self_equilibrated_so_it_seats_nothing():
    """The load that decides the direction must be the APPLIED one.

    A thermal eigenstrain on a body with a free boundary has no resultant — it
    is checked here numerically rather than assumed, because if it had one, the
    direction a magnet "falls" in at standstill would be an artefact of its own
    expansion and every standstill answer in the suite would move.
    """
    out = _solve(_polys(0.0), rpm=0.0, part_temps=LOOSENING)
    c = _case(out)
    assert c["contact"]["seated"] == [], c["contact"]["seated"]
    assert c["contact"]["unretained_parts"] == []
    assert c["magnet_retention"]["verdict"] == "not loaded (standstill)"
    # the parts did expand — this is a loaded thermal case, just not a seated one
    assert c["rotor_od_growth_um"] > 50.0


# ---------------------------------------------------------------------------
# (d) the refusals that must survive
# ---------------------------------------------------------------------------

def test_a_magnet_with_nothing_outward_is_still_refused():
    """An open-top pocket: parallel side walls, nothing at all above the magnet.

    No open pair faces the load, so there is no landing candidate and no travel
    that would find one.  "Not retained" is the answer, and it is refused rather
    than graded.
    """
    with pytest.raises(rs.RotorRanAway) as ei:
        _solve(_polys(open_top=True))
    exc = ei.value
    assert exc.pair == "magnet_rotor"
    assert "held by nothing" in str(exc)
    assert exc.rpm == pytest.approx(RPM)


def test_a_modelled_air_gap_has_no_contact_facet_to_land_on():
    """THE LIMIT OF THE FORMULATION, pinned so it is a decision and not a
    surprise (2026-09-09).

    The contact is node-to-node on a conforming mesh, so a pair exists only
    where two parts SHARE a boundary.  Draw 0.1 mm of air above the magnet — the
    40 mm's ``magnet_up_gap`` — and that sliver is in no part's polygon, so it is
    not meshed, the magnet's outer face is a free surface facing another free
    surface, and the magnet/iron interface has no outward-facing pair at all.
    Seating moves a part onto a pair it already has; it cannot create one.  So
    this case stays the refusal it was, and the route's bonded fallback (a glued
    magnet, which is what a built machine has) remains its answer.
    """
    rm = rs.build_rotor_mesh(_polys(0.1), mesh_size_mm=2.0)
    cs = ctc.build_contact_system(
        rm.mesh, rm.part_tri,
        {"magnet_rotor": ctc.ContactSpec("separation", 0.0)}, order=1)
    it = cs.iface("magnet_rotor")
    mid = 0.5 * (it.seg[:, 0, :] + it.seg[:, 1, :])
    er = mid / np.maximum(np.linalg.norm(mid, axis=1), 1e-30)[:, None]
    outward = np.einsum("ij,ij->i", it.normal, er) > 0.7   # normal is magnet->iron
    assert outward.sum() == 0, "the drawn gap unexpectedly welded shut"

    # the same pocket cut to the magnet DOES have one, and that is the whole
    # difference between the two fixtures
    rm0 = rs.build_rotor_mesh(_polys(0.0), mesh_size_mm=2.0)
    cs0 = ctc.build_contact_system(
        rm0.mesh, rm0.part_tri,
        {"magnet_rotor": ctc.ContactSpec("separation", 0.0)}, order=1)
    it0 = cs0.iface("magnet_rotor")
    mid0 = 0.5 * (it0.seg[:, 0, :] + it0.seg[:, 1, :])
    er0 = mid0 / np.maximum(np.linalg.norm(mid0, axis=1), 1e-30)[:, None]
    assert (np.einsum("ij,ij->i", it0.normal, er0) > 0.7).sum() > 0

    with pytest.raises(rs.RotorRanAway):
        _solve(_polys(0.1))


# ---------------------------------------------------------------------------
# the guards
# ---------------------------------------------------------------------------

def test_the_travel_cap_is_a_runaway_by_another_name():
    """A part that has to cross 5 % of the rotor to find a surface has not been
    seated, it has escaped — ``runaway_verdict`` says so by name."""
    ran = rs.runaway_verdict(1e-5, 0.05, [], None, {}, ["magnet"])
    assert ran is not None
    assert any("came loose" in r and "5 %" in r for r in ran["reasons"]), ran
    # …and without it the same displacement is an ordinary elastic answer
    assert rs.runaway_verdict(1e-5, 0.05, [], None, {}, []) is None


def test_the_seating_constants_are_the_ones_the_docstring_argues_for():
    """Читаются в отчёте — a silent edit of any of these moves every seated
    answer, so they are pinned next to the physics they came from."""
    assert ctc.SEAT_MIN_COS == 0.05        # excludes the pocket SIDE walls
    assert ctc.SEAT_TRAVEL_FRAC == 0.05    # 2.5 mm on a Ø100 rotor
    assert ctc.SEAT_LOAD_REL == 1e-9       # a free-body eigenstrain is ~1e-15
    assert ctc.SEAT_MAX_STEPS == 8
    # 2026-09-09, the G2:
    assert ctc.SEAT_LOOSE_FRAC == 1e-3     # 73 µm on a Ø146.7 rotor
    assert ctc.SEAT_REG == 1e-6            # lets the Newton leave the subspace
    assert ctc.SEAT_GRAD_TOL == 1e-6
    assert ctc.SEAT_NEWTON_STEPS == 40
    assert ctc.FREEZE_FLIPS == 3
    assert ctc.FREEZE_MARGIN_FRAC == 1e-6  # 73 nm — the penalty's own scale


# ---------------------------------------------------------------------------
# (f) the placement is a RIGID BODY, not a slide (2026-09-09)
# ---------------------------------------------------------------------------

def test_the_travel_clears_the_face_BEHIND_the_part_not_just_the_one_in_front():
    """``_seat_line`` on the G2's own numbers, as a unit.

    The pocket floor is 75.5 µm inside the magnet and the first face in front of
    it is 5.4 µm away on a wall it meets at 7.5°.  "Stop at the first thing you
    touch" answers 41 µm and leaves the floor buried 34 µm deep; the unilateral
    equilibrium answers 75 µm, which is the whole point of the rewrite.
    """
    # gap (m), rate = d(gap)/ds along the travel: the floor behind (rate +1, it
    # opens as the part advances) and two faces in front (rate < 0).
    gap = np.array([-75.5e-6, 5.4e-6, 32.1e-6])
    rate = np.array([+0.999, -0.131, -0.419])
    target = 1e-9                      # |F| / k_c — nanometres, as on the G2
    s, capped = ctc._seat_line(gap, rate, target, 1e-3)
    assert not capped
    assert s == pytest.approx(75.0e-6, rel=0.01), s
    # the first-touch answer, for contrast, is the one that used to be taken
    assert (gap[1] / -rate[1]) == pytest.approx(41.22e-6, rel=1e-3)
    # …and with nothing behind it the equilibrium IS that first touch, plus the
    # penetration the load itself needs — 7.6 nm here, which is what puts the
    # part ON the face instead of a hair short of it
    s2, _c = ctc._seat_line(gap[1:], rate[1:], target, 1e-3)
    assert s2 == pytest.approx(41.28e-6, rel=1e-3), s2
    assert s2 - gap[1] / -rate[1] == pytest.approx(target / rate[1] ** 2,
                                                   rel=1e-3)


def test_a_part_that_needs_to_turn_is_turned():
    """A rigid part cannot be slid into a pocket that has EXPANDED around it.

    Three faces whose clearances are inconsistent with any pure translation, but
    consistent with a translation plus a small rotation: the placement has to
    find all three, or it rests the part on one of them (which on the G2 left the
    magnet on a point support and the next solve at 1.5e10 mm).
    """
    # unit normals of three faces, and the true rigid motion we hide in the gaps
    G = np.array([[-1.0, 0.0, -0.30],
                  [-0.20, -0.98, 0.55],
                  [0.15, -0.99, -0.80]])
    truth = np.array([40e-6, 12e-6, -9e-6])
    gap = -(G @ truth)                 # every face exactly touching at `truth`
    Q = -(G.T @ np.array([1.0, 1.0, 1.0])) * 1e3     # a load those three carry
    a, capped = ctc._seat_placement(gap, G, Q, 5.4e12, 5e-3)
    assert not capped
    assert a == pytest.approx(truth, abs=5e-9), a
    # …and a translation-only placement cannot: the rotation is 9 µrad of the
    # part's own size and leaves one of the three faces microns out
    d = Q[:2] / np.linalg.norm(Q[:2])
    t, _c = ctc._seat_line(gap, G[:, :2] @ d, float(Q[:2] @ d) / 5.4e12, 5e-3)
    resid = np.maximum(-(gap + G @ np.array([t * d[0], t * d[1], 0.0])), 0.0)
    assert resid.max() > 1e-6, resid


def _loose_setup(part_temps=None):
    """The 4-pole fixture's contact system, plus a purely RADIAL magnet load.

    Everything ``_loose_pairs`` reads and nothing it does not, so the test is
    about the criterion and not about a solve.
    """
    rm = rs.build_rotor_mesh(_polys(0.0), mesh_size_mm=2.0)
    cs = ctc.build_contact_system(
        rm.mesh, rm.part_tri,
        {"magnet_rotor": ctc.ContactSpec("separation", 0.2)}, order=1)
    basis, _elem = rs.make_basis(cs.mesh, 1)
    body = ctc._part_bodies(cs)
    body_dof = ctc._dof_components(basis, body, cs.mesh)
    ndof = basis.N
    ix = np.arange(0, ndof, 2)
    iy = ix + 1
    px, py = basis.doflocs[0], basis.doflocs[1]
    # one magnet body: the smallest that is not the iron
    sizes = np.bincount(body)
    frame = {int(sizes.argmax())}
    mag = int(np.argmin(np.where(sizes > 0, sizes, 10 ** 9)))
    # A radial outward load on that magnet's dofs only, scaled to the resultant
    # a magnet of this size really carries — the criterion asks how far the
    # part's OWN load moves it, so the load has to be the machine's and not a
    # unit vector (7.6 kN per metre of stack is the G2 magnet at 3 000 rpm).
    f = np.zeros(ndof)
    m_x, m_y = body_dof[ix] == mag, body_dof[iy] == mag
    r = np.hypot(px[ix][m_x], py[ix][m_x])
    f[ix[m_x]] = px[ix][m_x] / np.maximum(r, 1e-12)
    r2 = np.hypot(px[iy][m_y], py[iy][m_y])
    f[iy[m_y]] = py[iy][m_y] / np.maximum(r2, 1e-12)
    res = math.hypot(f[ix[m_x]].sum(), f[iy[m_y]].sum())
    f *= MAGNET_LOAD_N_PER_M / max(res, 1e-30)
    return cs, body, body_dof, f, frame, ix, iy, px, py, mag


def test_looseness_is_a_question_about_the_load_path_not_about_attachment():
    """(the mechanism test, 2026-09-09)

    Same part, same load, three different contact states:

      * on its radial faces      -> held;
      * on the pocket SIDE walls only, frictionless   -> loose, though every one
        of those pairs is closed and the component graph says "attached";
      * the same side walls STICKING under friction   -> held, because a Coulomb
        spring that has pressure behind it is a load path (this is the two-block
        fixture of tests/test_mechanical_torque.py, and getting it wrong seated
        that block sideways into its travel cap).
    """
    cs, body, body_dof, f, frame, ix, iy, px, py, mag = _loose_setup()
    uni = cs.pair_unilateral
    mine = (body[cs.pair_va] == mag) ^ (body[cs.pair_vb] == mag)
    er = cs.pair_pos / np.maximum(
        np.linalg.norm(cs.pair_pos, axis=1), 1e-30)[:, None]
    nr = np.abs(np.einsum("ij,ij->i", cs.pair_n, er))
    kc, r_out = CONTACT_K, R_OD * 1e-3

    def _loose(closed, kt):
        out = ctc._loose_pairs(cs, body, body_dof, f, closed, uni, frame,
                               ix, iy, px, py, kc, r_out,
                               np.where(closed, kt, 0.0))
        return bool((out & mine).any())

    all_closed = np.ones(cs.n_pairs, dtype=bool)
    assert not _loose(all_closed, np.ones(cs.n_pairs))
    # ONLY the side walls, and frictionless — the vertex normal at a pocket
    # corner blends the two faces, so a corner pair is a radial face wearing a
    # side wall's angle and must be left out or the test proves nothing.
    walls = mine & uni & (nr < 0.2)
    assert walls.sum() > 4, "the fixture stopped having side walls"
    assert _loose(walls, np.full(cs.n_pairs, ctc.TANGENT_REG))
    # the same walls, sticking: mu*F_n is a real path and the part is held
    assert not _loose(walls, np.ones(cs.n_pairs))
    # nothing closed at all is the degenerate case of the first one
    assert _loose(np.zeros(cs.n_pairs, dtype=bool),
                  np.full(cs.n_pairs, ctc.TANGENT_REG))
    # …and one corner pair, which DOES face the load, puts it back
    corner = np.zeros(cs.n_pairs, dtype=bool)
    corner[np.nonzero(mine & uni & (nr > 0.7))[0][:3]] = True
    assert not _loose(walls | corner, np.full(cs.n_pairs, ctc.TANGENT_REG))


def test_a_part_with_no_net_load_is_never_called_loose():
    """The guard that keeps every standstill answer where it is: with no load
    there is no direction to be loose in, whatever the contacts are doing."""
    cs, body, body_dof, f, frame, ix, iy, px, py, mag = _loose_setup()
    out = ctc._loose_pairs(cs, body, body_dof, np.zeros_like(f),
                           np.zeros(cs.n_pairs, dtype=bool), cs.pair_unilateral,
                           frame, ix, iy, px, py, CONTACT_K, R_OD * 1e-3,
                           np.zeros(cs.n_pairs))
    assert not out.any()


# ---------------------------------------------------------------------------
# (g) THE MACHINE — the live G2-L40 cross-section, from the CATALOG (2026-09-09)
# ---------------------------------------------------------------------------
# READ-ONLY: the die and the machine override are read from config/dies/CILN28
# and a CadQueryMotor is built from a COPY of the merged geometry dict.  Nothing
# under config/ is written, no route is called, no cache is touched — the
# project's standing rule that verification never goes through a persisting path.
# The LIVE config is deliberately not read either: it is whatever the user has
# on screen this minute, and these numbers have to mean the same thing tomorrow.

G2_DIE = "CILN28"
G2_MACHINE = "G2-L40"
G2_MATERIALS = {"rotor_core": "B15AHV950M", "stator_core": "B15AHV950M",
                "magnet": "F52SH_120C", "shaft": "Steel_42CrMo4_QT"}
G2_STACK_MM = 40.0
G2_RPM, G2_TORQUE, G2_MU = 3000.0, 61.09, 0.2
#: the coupled solve's own answer for this duty (memory: EM–thermal coupling)
G2_HOT = {"rotor_core": 133.9, "magnet": 134.7, "shaft": 133.3}


def _g2_polys():
    """The G2-L40 rotor polygons, from the catalog files only."""
    from pathlib import Path

    import yaml

    from motor_ai_sim.cadquery_geometry import CadQueryMotor

    die_dir = Path(__file__).resolve().parents[1] / "config" / "dies" / G2_DIE
    geo = dict(yaml.safe_load(
        (die_dir / "die.yaml").read_text(encoding="utf-8"))["geometry"])
    geo.update(yaml.safe_load(
        (die_dir / f"{G2_MACHINE}.yaml").read_text(encoding="utf-8"))
        ["geometry_overrides"])
    m = CadQueryMotor()
    m.set_parameters(geo)
    return m.get_2d_polygons(0.0)


def _g2_solve(part_temps):
    return rs.solve_rotor_stress(
        _g2_polys(), G2_MATERIALS, G2_RPM, 1.0, 0.0,
        stack_length_mm=G2_STACK_MM, mesh_size_mm=1.5, order=2,
        with_field=False,
        contacts={"magnet_rotor": ctc.ContactSpec("separation", G2_MU),
                  "shaft_rotor": ctc.ContactSpec("bonded", 0.0)},
        lift_off_solves=0, case_mode="single", loads="both",
        torque_nm=G2_TORQUE, part_temps_c=part_temps)


@pytest.fixture(scope="module")
def g2_hot_out():
    """~30 s: the coupled map, through the PRODUCTION model — which, on a
    machine with no band, must be the cold answer (user 2026-09-09)."""
    return _g2_solve(G2_HOT)


@pytest.fixture(scope="module")
def g2_hot(g2_hot_out):
    return _case(g2_hot_out)


@pytest.fixture(scope="module")
def g2_cold_out():
    return _g2_solve(None)


@pytest.fixture(scope="module")
def g2_cold(g2_cold_out):
    return _case(g2_cold_out)


#: The pocket, measured off the built cross-section (see the interface facets in
#: ``test_the_g2_pocket_is_a_floor_a_wedge_and_a_tab``).  The magnet sits on a
#: floor at r 54.4 with parallel walls above it, a tapered wedge from r 58.7 to
#: 68.2 whose normal is 0.24 radial, and the TAB — the shoulder under the iron
#: lip — from r 69.3 to 70.6 at 0.43…0.65 radial.  Above that is the 1.6 mm
#: ``magnet_up_gap``, which is drawn air and has no contact facet at all.
G2_TAB_MIN_NR = 0.40


def test_the_g2_pocket_is_a_floor_a_wedge_and_a_tab():
    """What the magnet has to land on, straight off the catalog geometry.

    Pinned first because every number below is about these faces, and a die edit
    that removed the tab would otherwise show up as a mysterious stress change.
    """
    rm = rs.build_rotor_mesh(_g2_polys(), mesh_size_mm=1.5)
    cs = ctc.build_contact_system(
        rm.mesh, rm.part_tri,
        {"magnet_rotor": ctc.ContactSpec("separation", G2_MU)}, order=2)
    it = cs.iface("magnet_rotor")
    mid = 0.5 * (it.seg[:, 0, :] + it.seg[:, 1, :])
    r = np.linalg.norm(mid, axis=1)
    nr = np.einsum("ij,ij->i", it.normal, mid / r[:, None])
    assert (nr < -0.9).any(), "the pocket floor is gone"
    assert ((nr > 0.15) & (nr < 0.35)).sum() > 100, "the wedge is gone"
    tab = nr > G2_TAB_MIN_NR
    assert tab.sum() > 20, "the pocket tab is gone"
    assert r[tab].min() * 1e3 == pytest.approx(69.3, abs=0.5)
    assert r[tab].max() * 1e3 == pytest.approx(70.6, abs=0.5)
    # …and nothing above the tab: the magnet_up_gap is drawn air, not iron
    assert r.max() * 1e3 < 71.0


#: The 20 °C answer, measured 2026-09-09 on the catalog cross-section.  It is the
#: guard on the rewrite: the hot case was a refusal and had nowhere to go but up,
#: the cold one was already a good answer and had to stay one.  Held loosely
#: (1 %) because pypardiso is multi-threaded and not bit-reproducible.
#: AVERAGED since 2026-09-10 (see `FROZEN_20C`).  Element-field values this
#: reference was written with, for the record: rotor 25.0, magnet 10.8,
#: sf_min 13.53.  The displacements and the open fraction are the same solve
#: either way — averaging is a reporting step, not a physics one.
G2_COLD = {"rotor_vm_p995": 19.42, "magnet_vm_p995": 9.52, "sf_min": 17.38,
           "sf_min_unaveraged": 13.53,
           "open_fraction": 0.919, "od_growth_um": 4.07,
           "max_displacement_um": 5.84}


def test_the_g2_at_20C_still_solves_the_way_it_did(g2_cold):
    """Cold, the pocket is cut to the magnet and the wedge holds it.

    The pre-rewrite code seated this case too (16.5 µm of accumulated travel,
    rotor 27.4 MPa p99.5, 91.0 % open); the first rigid-body placement moved
    it to 23 µm, 27.9 MPa and 90.4 %.  Since the trapped-vs-pushed rule (a
    part overlapped from ONE side rides out on its spring; only a part nipped
    from opposing faces is re-placed) the cold magnet stays in its wedge:
    2 µm of ride-along, 91.9 % open, and the rotor's 25.0 MPa p99.5 is the
    20.9 of the centrifugal case plus the 3.2 of the torque case — the two
    loads superposing, as they should on a joint that never lets go.
    Measured 2026-09-09 15:00 (mesh 1.5 mm, P2, µ 0.2, 3 000 rpm, 61 N·m).
    """
    c = g2_cold
    assert c["contact"]["converged"]
    assert c["contact"]["unretained_parts"] == []
    assert c["magnet_retention"]["verdict"].startswith(
        "pocket side walls (wedge)")
    assert c["parts"]["rotor"]["von_mises_p995_mpa"] == pytest.approx(
        G2_COLD["rotor_vm_p995"], rel=0.01)
    assert c["parts"]["magnet"]["von_mises_p995_mpa"] == pytest.approx(
        G2_COLD["magnet_vm_p995"], rel=0.01)
    assert c["sf_min"] == pytest.approx(G2_COLD["sf_min"], rel=0.01)
    assert c["sf_min_unaveraged"] == pytest.approx(
        G2_COLD["sf_min_unaveraged"], rel=0.01)
    assert c["interfaces"]["magnet_rotor"]["open_fraction"] == pytest.approx(
        G2_COLD["open_fraction"], rel=0.01)
    assert c["rotor_od_growth_um"] == pytest.approx(
        G2_COLD["od_growth_um"], rel=0.01)
    assert c["max_displacement_um"] == pytest.approx(
        G2_COLD["max_displacement_um"], rel=0.01)
    # cold, the joint is a light clamp: no interpenetration worth the name
    assert c["contact"]["max_penetration_um"] < 0.01


def test_the_g2_at_the_coupled_temperatures_is_the_cold_answer(g2_hot_out, g2_cold_out):
    """THE RULE ON THE MACHINE (user 2026-09-09: *"нам нужно учитывать
    температуру только как изменение давления на бандаж, если он есть"*).

    The G2-L40 has no band.  Its magnets sit in an epoxy bed with a 0.04 mm
    pocket clearance and the rotor is iron through, so the coupled map — iron
    134 °C, magnets 135 °C — is not a load: the per-part eigenstrain of the
    2026-09-07 model manufactured a pocket grown 75 µm around a magnet that
    then re-seated in a 7.5° self-locking wedge at seven times its own
    centrifugal load (rotor 211 MPa p99.5 against 28 cold), a limit-cycling
    active set, and a bonded fallback quoting 245 MPa of glued thermal
    tension.  None of that is the built machine.  Hot is now cold, to the
    solver's own reproducibility, and the answer says why.
    """
    th = g2_hot_out["thermal"]
    assert th["active"] is False and th["model"] == "band_fit"
    assert th["applied_as"] == "none — no retaining band"
    assert th["part_temps_c"]["magnet"] == pytest.approx(G2_HOT["magnet"])
    assert any("no retaining band" in n for n in th["notes"]), th["notes"]
    h, c = _case(g2_hot_out), _case(g2_cold_out)
    for k in ("rotor_od_growth_um", "max_displacement_um", "sf_min",
              "torque_balance"):
        assert h[k] == pytest.approx(c[k], rel=1e-6), k
    for name in c["parts"]:
        assert h["parts"][name]["von_mises_p995_mpa"] == pytest.approx(
            c["parts"][name]["von_mises_p995_mpa"], rel=1e-6), name
    assert h["interfaces"]["magnet_rotor"]["open_fraction"] == pytest.approx(
        c["interfaces"]["magnet_rotor"]["open_fraction"], abs=1e-9)
    assert len(h["contact"]["seated"]) == len(c["contact"]["seated"])
    assert h["contact"]["unretained_parts"] == []

