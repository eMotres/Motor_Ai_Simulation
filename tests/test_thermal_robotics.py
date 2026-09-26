"""The ROBOTICS mode — a joint bolted to an arm, standing in a room.

User, 2026-09-14: the Ø85 / 13 mm machine (``CIANO28 85 20SW1200 / L13``) is a
robot joint.  It has no fan, no jacket and no slipstream; its bore is OPEN; its
24 coils stand PROUD of the core on both sides and the core and magnet end faces
are uncovered; and it is bolted to an arm.  Until today the nearest thing this
router could say was ``cooling_mode='air'`` at v = 0 — the flat 7 W/m²·K
natural-convection floor, one number for every machine, every ΔT and every
finish, with no radiation, no end faces and nowhere for the heat to go but the
housing.

``cooling_mode='robotics'`` is ONE mode and not four fields (the user's
decision), and this module pins the five things that makes it:

  (a) THE HOUSING FILM is Churchill–Chu PLUS radiation, the Robin coefficient is
      ``h_total`` and the reported ``h_conv`` is the convective half — so
      ``emissivity = 0`` removes exactly the radiation and nothing else.  On this
      machine radiation carries MORE than the convection, so that is not a
      detail: a polished housing is a different machine;
  (b) THE MOUNT IS THE PATH.  The housing hands the room a couple of watts of the
      ~60 W the joint makes; the bolts take the rest.  ``mount_W`` is checkable
      against ``G·(t_housing_mean_c − t_mount)`` from the payload alone, and with
      2 W/K at 40 °C the three outflows plus the bore add back up to the losses;
  (c) THE AXIAL END FACES are real and they are the biggest exposed area on this
      machine — the end turns alone are larger than the whole housing cylinder.
      Each of the four carries its own film, its own measured area and its own
      watts, and ``G·(T̄ − T_∞)`` closes on each;
  (d) THE FILMS ARE ITERATED against the wall temperature (h ∝ ΔT^(1/4) and the
      radiation re-linearises about the wall), and the answer is the FIXED POINT:
      the reported coefficient, area and wall reproduce the reported watts;
  (e) EVERY OTHER MODE IS UNCHANGED — the new blocks are present and say "off",
      the new budget lines are zero, and a machine with no film and no mount at
      all is still refused BY NAME.

LIBRARY CALLS ONLY, and nothing here may touch ``config/``: the Electromagnetic
snapshot store, the thermal loss-map pickle and the last-result pickle are all
redirected into the test's own tmp_path, exactly as
tests/test_thermal_open_frame.py does it — ``config/.last_thermal.pkl`` and
``config/.last_transient_field.pkl`` are the user's own last solves.  The
materials are pinned per request (``material_context.set_request_materials``) to
the die's own cards, so the answer does not move with the shared config's
assignment.

The Electromagnetic run is REAL (this router never solves one itself) but cheap:
four frames over one electrical period on a coarse mesh over the machine's
natural quarter.  What is claimed here is a CONTRACT and a set of identities, and
a converged 40-frame run would buy neither at ten times the wall clock.  The
loss level it produces is the rated duty's own (14.708 A, 1000 rpm, coil 120 °C →
~60 W, of which ~56 W is copper), which is what makes the watts below readable
beside the die's stored numbers.
"""
from __future__ import annotations

import json
import math
import pathlib
import tempfile
import time

import pytest
from fastapi import HTTPException

from tests.test_thermal_routes import store_em_run

#: ``config/dies/CIANO28 85 20SW1200/die.yaml`` + ``L13.yaml``'s overrides — the
#: machine the user named, as a per-request ``?geo=`` override so nothing on disk
#: is read or written.
L13_GEO = {
    "stator_diameter": 85.0, "slot_height": 7.4, "core_thickness": 2.4,
    "num_seg": 4, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.3, "tooth_width": 5.0, "tooth2_width": 2.2, "cut_width": 1.5,
    "insulation_thickness": 0.05, "wire_width": 3.5, "wire_height": 0.3,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.07, "num_wires_per_slot": 18,
    "wire_split": 1, "slot_hs": 0.13, "magnet_height": 7.0,
    "rotor_house_height": 0.8, "shaft_height": 2.0, "magnet_fill_down": 0.92,
    "magnet_fill_up": 0.22, "magnet_fill_radius": 0.4, "magnet_up_gap": 0.1,
    "rotor_hole": 0.5, "magnet_down_height": 0.6, "magnet_lamination": 0,
    "stator_fillet_r": 1.2, "stator_fillet_r1": 0.1, "rotor_fill_r": 0.2,
    "motor_length": 13.0,
}

#: The die's own material cards, pinned per request so the shared config's
#: assignment cannot move this module's answer.
MATERIALS = {"assignment": {"magnet": "F52SH_120C", "stator_core": "20SW1200",
                            "rotor_core": "20SW1200"},
             "materials": {}}

#: The L13's RATED duty (die file: 14.708 A, 1000 rpm, γ 2°, coil 120 °C), on the
#: cheapest honest cycle — four frames over one electrical period.
FAST = {"n_steps_per_period": 4, "n_periods": 1.0, "mesh_size_mm": 2.0,
        "min_size_mm": 0.35, "n_sectors": 4, "I_phase_rms": 14.708,
        "gamma_deg": 2.0, "coil_temp_c": 120.0}
RUN_ID = "2026-09-14T09:00:00"

#: The room the joint stands in, and the arm it is bolted to.
AMBIENT_C = 40.0
#: A machined flange with thermal compound on a machine this size (the user has
#: not measured one yet — this is the plan's documented stand-in).
MOUNT_G = 2.0

ROBOT = dict(cooling_mode="robotics", ambient_temp=AMBIENT_C, bore_mode="still",
             emissivity=0.9, end_faces="still", end_face_sides=2)


# ---------------------------------------------------------------------------
# Fixtures — one Electromagnetic run and four conduction solves for the module
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sandbox():
    """Every store this module could write to, redirected into a tmp dir.

    Module-scoped (and therefore not ``tmp_path``, which is function-scoped)
    because the Electromagnetic run is solved once for the whole file and the
    snapshot it is answered from has to outlive the first test.
    """
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.routes import thermal as th

    mp = pytest.MonkeyPatch()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="thermal_robotics_"))
    mp.setattr(sim, "_transient_field_store_path", lambda: str(tmp / ".snap.pkl"))
    mp.setattr(th, "_last_store_path", lambda: str(tmp / ".last_thermal.pkl"))
    mp.setattr(th, "_loss_maps_path", lambda: str(tmp / ".loss_maps.pkl"))
    mp.setattr(th, "_LAST", {}, raising=True)
    mp.setattr(th, "_LAST_LOADED", True, raising=True)
    mp.setattr(th, "_LOSS_MAPS_LOADED", True, raising=True)
    saved = dict(sim._transient_field_snap)
    sim._transient_field_snap.clear()
    th._LOSS_MAPS.clear()
    th._FIELD_CACHE.clear()

    yield tmp

    sim._transient_field_snap.clear()
    sim._transient_field_snap.update(saved)
    th._LOSS_MAPS.clear()
    th._FIELD_CACHE.clear()
    mp.undo()


@pytest.fixture(scope="module")
def em_run(sandbox):
    """ONE Electromagnetic run of the L13's rated point, parked as a Run parks it.

    ``store_em_run`` is imported rather than re-spelled: that handshake IS the
    seam between the Electromagnetic and the Thermal tab, and a second copy here
    would test this module's idea of a run instead of the run.
    """
    from motor_ai_sim.material_context import set_request_materials

    set_request_materials(MATERIALS)
    try:
        info = store_em_run(L13_GEO, run_id=RUN_ID, phys=FAST)
    finally:
        set_request_materials(None)
    time.sleep(0.3)          # the persist is a daemon thread — let it land
    return info


def _solve(**over):
    """``solve_thermal_field`` on the L13, with this module's materials pinned."""
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.routes import thermal as th

    kw = dict(FAST, geo=json.dumps(L13_GEO), rpm=1000.0)
    kw.update(over)
    set_request_materials(MATERIALS)
    try:
        return th.solve_thermal_field(**kw)
    finally:
        set_request_materials(None)


@pytest.fixture(scope="module")
def robot(em_run):
    """The headline machine: still air at 40 °C, ε 0.9, open bore, end faces on,
    bolted to a 40 °C arm through 2 W/K."""
    return _solve(**ROBOT, mount_g_w_per_k=MOUNT_G, mount_temp_c=AMBIENT_C)


@pytest.fixture(scope="module")
def unbolted(em_run):
    """The same joint bolted to NOTHING — everything has to leave through the
    air.  It is the comparison that says how much the flange is worth."""
    return _solve(**ROBOT)


@pytest.fixture(scope="module")
def polished(em_run):
    """The same joint with a bare polished housing: ε = 0, i.e. no radiation."""
    return _solve(**{**ROBOT, "emissivity": 0.0},
                  mount_g_w_per_k=MOUNT_G, mount_temp_c=AMBIENT_C)


@pytest.fixture(scope="module")
def housed(em_run):
    """The SAME machine solved the way it always was — a manual film, no mount,
    no end faces.  The regression guard for every other mode."""
    return _solve(cooling_mode="manual", ambient_temp=AMBIENT_C, h_conv=300.0,
                  bore_mode="none")


# ---------------------------------------------------------------------------
# (a) the housing is a still-air film WITH radiation
# ---------------------------------------------------------------------------

def test_a_the_housing_film_is_convection_plus_radiation(robot):
    """Two mechanisms, both reported, and the Robin coefficient is their sum.

    ``h_conv`` keeps its plain meaning — the convective half — so the panel, the
    report and ``emissivity = 0`` all mean what they say.  What the conduction
    solve was actually given is ``h_total``, and the identity that proves it is
    the surface's own ∫h(T − T_sink)dA against the area and the wall temperature
    in the payload.
    """
    o = robot["cooling"]["outer"]
    b = robot["cooling"]["heat_budget"]

    assert o["mode"] == "robotics"          # the mode that was ASKED for
    assert o["h_conv"] > 0.0 and o["h_rad"] > 0.0
    # `abs=0.01` and not `rel=1e-9`: the payload quotes h_conv to two decimals
    # and the other two to three, so a sum rebuilt from the PRINTED numbers
    # reproduces the printed total to the payload's own resolution.
    assert o["h_total"] == pytest.approx(o["h_conv"] + o["h_rad"], abs=0.01)
    assert o["emissivity"] == pytest.approx(0.9)
    assert o["t_wall_c"] > AMBIENT_C
    assert o["ra"] > 0.0 and o["nu"] > 0.0
    # The film the SOLVE used is the total — this is the whole of the
    # "h_conv vs h_total" trap, stated as watts.
    assert b["housing_W"] == pytest.approx(
        o["h_total"] * o["area_m2"] * (o["t_wall_c"] - o["t_sink_c"]),
        rel=0.02), (o, b["housing_W"])
    # …and the housing is the Ø85 machine's own outer boundary — the area the
    # SOLVER walked, not a cylinder assumed here.  It is a little larger than the
    # plain πDL (3.47e-3 m²) because this stator has outer cuts, and those walls
    # are open to the room exactly like the rest of the boundary; what would be
    # wrong is an area of a different ORDER, which is what a wedge counted once
    # or four times over looks like.
    _cyl = math.pi * 0.085 * 0.013
    assert _cyl <= o["area_m2"] <= 1.3 * _cyl, o["area_m2"]
    # On a small machine in still air radiation is not a correction: it carries
    # MORE than the convection does.
    assert o["h_rad"] > o["h_conv"]
    assert b["housing_convection_W"] + b["housing_radiation_W"] == pytest.approx(
        b["housing_W"], abs=0.01)


def test_a2_the_bore_is_open_and_unventilated(robot):
    """The user's decision: the bore is OPEN, not sealed.

    ``bore_still`` and not ``bore_air`` at v = 0 — the second says "the air is
    stirred but not renewed, no mass flow, no heat removal", which is the right
    answer for a DUCT and no answer at all for a standing hole.
    """
    i = robot["cooling"]["inner"]
    assert i["mode"] == "still"
    assert i["h_total"] == pytest.approx(i["h_conv"] + i["h_rad"], abs=0.01)
    assert i["m_dot_kg_s"] == 0.0            # nothing flows, nothing to iterate
    assert i["nu"] >= 1.0                    # floored at conduction across it
    assert robot["cooling"]["heat_budget"]["bore_W"] == pytest.approx(
        i["h_total"] * i["area_m2"] * (i["t_wall_c"] - i["t_sink_c"]),
        rel=0.05, abs=0.05)


# ---------------------------------------------------------------------------
# (b) the mount is the path
# ---------------------------------------------------------------------------

def test_b_the_mount_carries_the_machine(robot):
    """2 W/K at 40 °C, and the identity that makes the number checkable.

    ``mount_W == G·(t_housing_mean_c − t_mount)`` — both sides are in the
    payload, which is the only defence a lumped out-of-plane conductance has
    against a silent factor of N on a symmetry wedge.
    """
    m = robot["cooling"]["mount"]
    b = robot["cooling"]["heat_budget"]

    assert m["mode"] == "conduction"
    assert m["G_W_per_K"] == pytest.approx(MOUNT_G)
    assert m["t_sink_c"] == pytest.approx(AMBIENT_C)
    assert m["t_housing_mean_c"] is not None and m["n_elements"] > 0
    assert b["mount_W"] == pytest.approx(
        MOUNT_G * (m["t_housing_mean_c"] - AMBIENT_C), rel=0.01), (m, b)
    assert b["mount_W"] == pytest.approx(m["heat_removed_W"], rel=1e-6)
    # THE POINT: the air is not the cooling system on this machine.
    assert b["mount_W"] > b["housing_W"], b
    assert b["housing_W"] < 0.15 * b["losses_W"], b


# ---------------------------------------------------------------------------
# (b1b) THE HEAT PATH — one choice, four options (2026-09-26)
# ---------------------------------------------------------------------------
# Owner: «давай упростим».  The five mount / robot-link fields became ONE
# select; each option must change the heat paths exactly as its name says, and
# 'none' must have no conduction path at all.

@pytest.fixture(scope="module")
def hp_housing(em_run):
    return _solve(**ROBOT, heat_path="housing")


@pytest.fixture(scope="module")
def hp_shaft(em_run):
    return _solve(**ROBOT, heat_path="shaft")


@pytest.fixture(scope="module")
def hp_both(em_run):
    return _solve(**ROBOT, heat_path="both")


def _body_film_closes(hp):
    """The body's own film carries what reaches it: G_film·(T − T_∞)."""
    assert hp["heat_to_room_W"] == pytest.approx(
        hp["G_film_W_per_K"] * (hp["t_body_c"] - AMBIENT_C), rel=0.05, abs=0.02)


def test_b1b_heat_path_none_has_no_conduction_path(unbolted):
    """'none' is the robotics mode with nothing conducted: no contact, no
    bearings, no mount — the OD keeps its still-air film and nothing else."""
    c = unbolted["cooling"]
    b = c["heat_budget"]
    assert c["heat_path"]["option"] == "none"
    assert c["heat_path"]["body"] is None
    assert b["bearings_W"] == 0.0 and b["mount_W"] == 0.0
    assert c["mount"]["mode"] == "off"
    assert "contact" not in str(c["outer"].get("model") or "")
    assert c["outer"]["h_rad"] > 0.0        # the OD sees the room directly
    # …and no touch limit is judged: there is no housing / structure node.
    from motor_ai_sim import coupled_time_to_limit as ttl
    assert not ({"housing", "structure"}
                & {p.part for p in ttl.part_limits(thermal_result=unbolted)})


def test_b1c_heat_path_housing_puts_the_od_into_the_housing(hp_housing, unbolted):
    c = hp_housing["cooling"]
    hp, b = c["heat_path"], c["heat_budget"]
    assert hp["option"] == "housing" and hp["body"] == "housing"
    assert hp["rides_node"] == "stator"
    # The OD's film IS the contact now, and every watt through it reaches the
    # housing, which is what sheds it — nothing goes through the bearings.
    assert "contact" in c["outer"]["model"]
    assert hp["contact"]["heat_W"] == pytest.approx(b["housing_W"], abs=0.01)
    assert hp["bearings"] is None and b["bearings_W"] == 0.0
    assert hp["heat_to_room_W"] == pytest.approx(b["housing_W"], rel=1e-3,
                                                 abs=1e-3)
    _body_film_closes(hp)
    assert AMBIENT_C < hp["t_body_c"]
    # The housing is a BIGGER skin than the bare OD it covers: the stator
    # side runs cooler than with no contact at all.
    assert (hp_housing["components"]["stator"]["avg"]
            < unbolted["components"]["stator"]["avg"])
    assert b["residual_pct"] < 1.0, b


def test_b1d_heat_path_shaft_goes_rotor_shaft_bearings_structure(hp_shaft,
                                                                 unbolted):
    c = hp_shaft["cooling"]
    hp, b = c["heat_path"], c["heat_budget"]
    assert hp["option"] == "shaft" and hp["body"] == "structure"
    assert hp["rides_node"] == "rotor"
    assert hp["contact"] is None
    assert hp["bearings"]["heat_W"] == pytest.approx(b["bearings_W"], abs=1e-3)
    assert b["bearings_W"] > 0.0
    assert hp["heat_to_room_W"] == pytest.approx(b["bearings_W"], rel=1e-3,
                                                 abs=1e-3)
    _body_film_closes(hp)
    # The stator OD keeps its own still-air film (no housing on it).
    assert "contact" not in str(c["outer"].get("model") or "")
    assert c["outer"]["h_rad"] > 0.0
    # It is a ROTOR path: on the rotor's own balance, and the magnets cooler.
    rs = b["rotor_heat_split"]
    assert rs["axial_bearings_W"] == pytest.approx(b["bearings_W"], abs=1e-3)
    assert abs(rs["closure_W"]) < 0.02 * max(rs["rotor_W"], 1.0) + 0.05, rs
    assert (hp_shaft["components"]["magnet"]["avg"]
            < unbolted["components"]["magnet"]["avg"])
    assert b["residual_pct"] < 1.0, b


def test_b1e_heat_path_both_shares_one_housing(hp_both, hp_housing):
    c = hp_both["cooling"]
    hp, b = c["heat_path"], c["heat_budget"]
    assert hp["option"] == "both" and hp["body"] == "housing"
    assert hp["contact"]["heat_W"] > 0.0 and hp["bearings"]["heat_W"] > 0.0
    # ONE body: what reaches it through the OD and through the bearings is
    # what it hands the room.
    assert hp["heat_to_room_W"] == pytest.approx(
        hp["contact"]["heat_W"] + hp["bearings"]["heat_W"], rel=1e-3, abs=1e-3)
    _body_film_closes(hp)
    assert b["residual_pct"] < 1.0, b
    # A second way in for the rotor's heat: the magnets run cooler than with
    # the housing alone.
    assert (hp_both["components"]["magnet"]["avg"]
            < hp_housing["components"]["magnet"]["avg"])


def test_b1f_the_body_is_judged_against_the_70_c_touch_limit(hp_housing):
    from motor_ai_sim import coupled_time_to_limit as ttl
    hp = hp_housing["cooling"]["heat_path"]
    assert hp["touch_limit_c"] == 70.0
    assert hp["binds_touch_limit"] is (hp["t_body_c"] > 70.0)
    got = {p.part: p for p in ttl.part_limits(thermal_result=hp_housing)}
    assert got["housing"].limit_c == 70.0
    assert got["housing"].at_point_c == pytest.approx(hp["t_body_c"])
    assert ttl.part_label("housing") == "housing (touch 70 °C)"


def test_b2_the_budget_closes_on_the_four_paths(robot):
    """Every watt that leaves is on a named line, and they add up.

    housing + bore + mount + end faces = the losses, in MACHINE watts, with the
    solver's own closure error as the only difference.
    """
    b = robot["cooling"]["heat_budget"]
    assert b["losses_W"] > 0.0
    assert b["residual_pct"] < 1.0, b
    assert (b["housing_W"] + b["bore_W"] + b["mount_W"] + b["end_faces_W"]
            + b["shaft_ends_W"]) == pytest.approx(b["losses_W"], rel=0.01,
                                                  abs=0.05), b


def test_b3_a_mount_only_machine_is_solvable(em_run):
    """No film anywhere and a flange: that machine IS cooled, and used to be
    refused.

    The widened refusal is the point — with ``cooling_mode='none'`` and
    ``bore_mode='none'`` every boundary is adiabatic, but a conductance to a HELD
    temperature is not a boundary of this cross-section and the steady problem
    has a solution.  All of it then leaves through the bolts.
    """
    f = _solve(cooling_mode="none", ambient_temp=AMBIENT_C, bore_mode="none",
               mount_g_w_per_k=MOUNT_G, mount_temp_c=AMBIENT_C)
    b = f["cooling"]["heat_budget"]
    assert b["housing_W"] == 0.0 and b["bore_W"] == 0.0
    assert b["mount_W"] == pytest.approx(b["losses_W"], rel=0.01, abs=0.05), b
    assert f["cooling"]["mount"]["mode"] == "conduction"
    # …and the end faces are NOT on: they belong to the robotics mode, and this
    # request did not ask for it.
    assert b["end_faces_W"] == 0.0
    assert f["cooling"]["end_faces"]["mode"] == "off"


def test_b4_the_flange_is_worth_the_difference(robot, unbolted):
    """Bolted to an arm vs bolted to nothing, same machine, same losses."""
    assert (robot["components"]["winding"]["max"]
            < unbolted["components"]["winding"]["max"])
    assert unbolted["cooling"]["mount"]["mode"] == "off"
    assert unbolted["cooling"]["heat_budget"]["mount_W"] == 0.0
    assert "bolted to NOTHING" in unbolted["cooling"]["mount"]["note"]
    # With no flange every watt leaves through the air, so the air-side paths
    # have to carry MORE than they did with one.
    bu = unbolted["cooling"]["heat_budget"]
    br = robot["cooling"]["heat_budget"]
    assert bu["end_faces_W"] > br["end_faces_W"]
    assert bu["housing_W"] > br["housing_W"]


# ---------------------------------------------------------------------------
# (c) the axial end faces
# ---------------------------------------------------------------------------

def test_c_the_end_faces_are_four_real_paths(robot):
    """One block per node: area, film, conductance, wall and watts — each
    closing on ``G·(T̄ − T_∞)``."""
    ef = robot["cooling"]["end_faces"]
    b = robot["cooling"]["heat_budget"]

    assert ef["mode"] == "still" and ef["sides"] == 2
    total = 0.0
    for node in ("winding", "stator", "rotor", "magnet"):
        e = ef[node]
        assert e["mode"] == "still", (node, e)
        assert e["area_m2"] > 0.0 and e["n_faces"] == 2
        assert e["h_total"] == pytest.approx(e["h_conv"] + e["h_rad"], abs=0.01)
        # G = h_total · A_total (the area is BOTH faces; no fin, and it says so)
        assert e["G_W_per_K"] == pytest.approx(
            e["h_total"] * e["area_m2"], rel=1e-3), (node, e)
        assert e["t_mean_c"] is not None and e["n_elements"] > 0
        assert e["heat_removed_W"] == pytest.approx(
            e["G_W_per_K"] * (e["t_mean_c"] - e["t_sink_c"]),
            rel=0.03, abs=0.02), (node, e)
        total += e["heat_removed_W"]
    assert b["end_faces_W"] == pytest.approx(total, rel=1e-3, abs=1e-3)
    # The end turns are the biggest exposed area on this machine — bigger than
    # the whole housing cylinder, which is why leaving them out was not
    # conservative.
    assert ef["winding"]["area_m2"] > robot["cooling"]["outer"]["area_m2"]


def test_c2_the_end_winding_area_is_the_hand_formula(robot):
    """A_ew from the geometry, by hand, on the run's OWN k_end.

        bundle thickness = num_wires_per_slot · (wire_height + wire_spacing_y)
        bundle width     = the wire column (= wire_width on an unsplit machine)
        exposed perimeter= 2·thickness + width  (the tooth-facing face is shielded)
        ℓ_end            = (k_end − 1)·L_stack/2  per side
        A_ew             = n_slots · 2 sides · perimeter · ℓ_end

    The same derivation the OPEN frame's forced-air path uses — one geometry,
    two films, so the two machines cannot disagree about how much copper is
    standing out of the core.  ``k_end`` is read off the answer, not recomputed:
    what is claimed is that the thermal model bills the end turns at the length
    the COPPER LOSS was billed at, whatever that was.
    """
    ef = robot["cooling"]["end_faces"]
    k_end = ef["k_end"]
    g = L13_GEO
    n_slots = int(g["num_seg"] * g["num_slots_per_segment"])
    t_mm = g["num_wires_per_slot"] * (g["wire_height"] + g["wire_spacing_y"])
    per_mm = 2.0 * t_mm + g["wire_width"]
    ell_m = (k_end - 1.0) * (g["motor_length"] * 1e-3) / 2.0
    a_hand = n_slots * 2 * (per_mm * 1e-3) * ell_m

    assert k_end > 1.0 and ef["k_end_source"]
    assert ef["winding"]["area_m2"] == pytest.approx(a_hand, rel=1e-3)
    assert ef["winding"]["area_per_face_m2"] == pytest.approx(a_hand / 2.0,
                                                              rel=1e-3)


def test_c3_the_core_end_faces_are_the_section_area(robot):
    """The stator's end face is its own SECTION, measured on this mesh.

    Not a number typed anywhere: the annulus a core end face has is exactly the
    area its polygons enclose, and the die's mass row quotes the same section
    (1335 mm² of stator core).  Two faces, so twice it.
    """
    ef = robot["cooling"]["end_faces"]
    # The die file's own CAD section for the stator core, ±15 % for the mesh's
    # discretisation of the fillets and the slot openings.
    assert ef["stator"]["area_per_face_m2"] == pytest.approx(1335e-6, rel=0.15)
    assert ef["stator"]["area_m2"] == pytest.approx(
        2.0 * ef["stator"]["area_per_face_m2"], rel=1e-6)
    for node in ("rotor", "magnet"):
        assert 1e-5 < ef[node]["area_per_face_m2"] < 6e-3, ef[node]
        assert "section area" in ef[node]["area_source"]


# ---------------------------------------------------------------------------
# (d) the films are iterated, and the answer is the fixed point
# ---------------------------------------------------------------------------

def test_d_the_wall_iteration_converges(robot):
    """More than one pass, and the answer is self-consistent.

    The seed is the flat 7 W/m²·K floor this mode replaces, so pass 1 is the old
    model; every pass after it re-evaluates every film at the wall the previous
    solve MEASURED (each surface's own ``t_mean_c``, never the global maximum).
    Convergence is on the wall to half a kelvin, which is a tenth of a per cent
    of h_total — so the fixed point can be checked by rebuilding the wall from
    the reported watts and the reported coefficient.
    """
    c = robot["cooling"]
    assert 1 < c["passes"] <= 4, c["passes"]

    o = c["outer"]
    t_implied = o["t_sink_c"] + (c["heat_budget"]["housing_W"]
                                 / (o["h_total"] * o["area_m2"]))
    assert t_implied == pytest.approx(o["t_wall_c"], abs=0.5), (t_implied, o)

    # …and the same for every end face: the reported G is the film at the
    # reported wall, and the reported wall is the one the solve came back with.
    for node in ("winding", "stator", "rotor", "magnet"):
        e = c["end_faces"][node]
        assert e["t_wall_c"] == pytest.approx(e["t_mean_c"], abs=0.5), (node, e)


def test_d2_the_film_is_evaluated_at_the_wall_the_solve_measured(robot):
    """``cooling_models.outer_still`` at the payload's own wall reproduces the
    payload's own coefficients.

    Not a tautology: it pins that the number in the answer came from the
    correlation at THAT temperature — a mode that quietly evaluated its film at
    ambient (the cheap way to avoid an iteration) would return a different
    h_conv here and the same everything else.
    """
    from motor_ai_sim.simulation.cooling_models import outer_still

    o = robot["cooling"]["outer"]
    hand = outer_still(t_wall_c=o["t_wall_c"], t_ambient_c=AMBIENT_C,
                       d_housing_m=0.085, emissivity=0.9,
                       area_m2=o["area_m2"], heat_w=0.0)
    # `abs=0.01`: the payload rounds h_conv to two decimals and the wall to two,
    # so a hand rebuild lands within the printed resolution — which is the claim.
    assert hand["h_conv"] == pytest.approx(o["h_conv"], abs=0.01)
    assert hand["h_rad"] == pytest.approx(o["h_rad"], abs=0.01)
    assert hand["h_total"] == pytest.approx(o["h_total"], abs=0.02)
    # An ambient-evaluated film would be a DIFFERENT number — i.e. the check
    # above has something to fail on.
    cold = outer_still(t_wall_c=AMBIENT_C, t_ambient_c=AMBIENT_C,
                       d_housing_m=0.085, emissivity=0.9)
    assert cold["h_total"] < o["h_total"]


def test_d3_both_splits_close(robot):
    """The rotor's balance and the stator's, each to under a per cent.

    Both gained an out-of-plane term today (the rotor's and the magnets' end
    faces on one side, the mount and the stator-side end faces on the other), and
    a closure that ignored one would not close on this machine.
    """
    b = robot["cooling"]["heat_budget"]
    r, s = b["rotor_heat_split"], b["stator_heat_split"]

    assert abs(r["closure_W"]) < 0.01 * max(abs(r["rotor_W"]), 1e-9) + 0.02, r
    assert abs(s["closure_W"]) < 0.01 * abs(s["total_in_W"]) + 0.02, s
    # The stator side's outflows are named, and on this machine the mount is
    # most of them.
    assert s["mount_W"] > s["housing_W"]
    assert s["housing_pct"] is not None and s["mount_pct"] is not None
    assert s["total_in_W"] == pytest.approx(s["stator_W"] + s["gap_in_W"],
                                            rel=1e-6, abs=1e-6)


# ---------------------------------------------------------------------------
# (e) emissivity, and every other mode unchanged
# ---------------------------------------------------------------------------

def test_e_zero_emissivity_removes_exactly_the_radiation(robot, polished):
    """A bare polished housing radiates nothing worth counting, and the model has
    to be able to SAY so rather than approach it.

    Exactly the radiation and nothing else: every film in this mode loses its
    ``h_rad`` and keeps its ``h_conv`` (re-evaluated at the new, hotter wall —
    the machine is worse cooled, so it is hotter, and that is the answer, not a
    discrepancy).
    """
    o0 = polished["cooling"]["outer"]
    b0 = polished["cooling"]["heat_budget"]

    assert o0["emissivity"] == 0.0
    assert o0["h_rad"] == 0.0
    assert o0["h_total"] == pytest.approx(o0["h_conv"], abs=0.01)
    assert b0["housing_radiation_W"] == 0.0
    assert b0["housing_convection_W"] == pytest.approx(b0["housing_W"],
                                                       abs=0.01)
    assert polished["cooling"]["inner"]["h_rad"] == 0.0
    for node in ("winding", "stator", "rotor", "magnet"):
        assert polished["cooling"]["end_faces"][node]["h_rad"] == 0.0
    # …and the machine that cannot radiate runs hotter.
    assert (polished["components"]["winding"]["max"]
            > robot["components"]["winding"]["max"])
    # The ε 0.9 machine's radiation is a real share of the air-side cooling, so
    # removing it is a real change and not a rounding.
    assert robot["cooling"]["heat_budget"]["housing_radiation_W"] > 0.0


def test_f_every_other_mode_still_answers_the_way_it_did(housed):
    """The new blocks are present and say "off"; the new budget lines are 0.

    "There is no mount" and "the mount block is missing because something went
    wrong" are different statements, and only a real payload can tell them
    apart — the same rule the bore's ``mode: none`` and the shaft's ``mode: off``
    have followed since 2026-09-07.
    """
    c = housed["cooling"]
    b = c["heat_budget"]

    assert c["outer"]["mode"] == "manual"
    assert c["mount"]["mode"] == "off" and c["mount"]["G_W_per_K"] == 0.0
    assert c["end_faces"]["mode"] == "off"
    assert b["mount_W"] == 0.0 and b["end_faces_W"] == 0.0
    # A film with one mechanism reports all of its watts as convection and no
    # radiation — which is what those models say (they omit it deliberately).
    assert b["housing_radiation_W"] == 0.0
    # `abs=0.01`: the budget quotes housing_W to two decimals and the two
    # mechanism lines to three, so they agree to the printed resolution — which
    # is the claim ("all of it is convection"), not a claim about rounding.
    assert b["housing_convection_W"] == pytest.approx(b["housing_W"], abs=0.01)
    assert b["residual_pct"] < 1.0, b
    # …and the manual mode still solves in ONE pass: nothing in it depends on
    # the wall temperature, so there is nothing to iterate.
    assert c["passes"] == 1
    assert "mount" in b["stator_heat_split"]["note"]


# ---------------------------------------------------------------------------
# refusals — by name, never solved as something else
# ---------------------------------------------------------------------------

def _validate(**over):
    from motor_ai_sim.routes.thermal import _validate_field_params

    kw = dict(cooling_mode="robotics", ambient_temp=AMBIENT_C, h_conv=50.0,
              air_speed_mps=0.0, fluid="water", fluid_temp_in_c=25.0,
              flow_lpm=0.0, bore_mode="still")
    kw.update(over)
    return _validate_field_params(**kw)


def test_g_nothing_cooled_and_no_mount_is_refused():
    """The widened refusal, and the sentence that says what to do about it."""
    with pytest.raises(HTTPException) as exc:
        _validate(cooling_mode="none", bore_mode="none", mount_g_w_per_k=0.0)
    d = exc.value.detail
    assert exc.value.status_code == 422
    assert d["error"] == "no cooled surface and no mount conductance"
    msg = d["invalid_parameters"][0]["message"]
    assert "mount_g_w_per_k > 0" in msg
    # …and with a flange it is a legal request (the solve is test_b3).
    assert _validate(cooling_mode="none", bore_mode="none",
                     mount_g_w_per_k=2.0) == ("none", "none")


def test_g2_a_still_bore_outside_the_robotics_mode_is_refused():
    """It is evaluated with an emissivity only that mode sends."""
    with pytest.raises(HTTPException) as exc:
        _validate(cooling_mode="air", bore_mode="still")
    d = exc.value.detail
    assert [p["field"] for p in d["invalid_parameters"]] == ["bore_mode"]
    assert "robotics" in d["invalid_parameters"][0]["message"]


@pytest.mark.parametrize("over,field", [
    ({"emissivity": 1.4}, "emissivity"),
    ({"emissivity": -0.1}, "emissivity"),
    ({"mount_g_w_per_k": -1.0}, "mount_g_w_per_k"),
    ({"mount_temp_c": float("nan")}, "mount_temp_c"),
    ({"end_faces": "open"}, "end_faces"),
    ({"end_face_sides": 3}, "end_face_sides"),
    ({"cooling_mode": "robot"}, "cooling_mode"),
    ({"heat_path": "arm"}, "heat_path"),
    ({"heat_path": "housing", "cooling_mode": "air", "bore_mode": "none"},
     "heat_path"),
])
def test_g3_every_new_input_is_refused_by_name(over, field):
    """Never clamped: an ε of 1.4 is a typo, and a typo that solves is a result
    nobody can reproduce."""
    with pytest.raises(HTTPException) as exc:
        _validate(**over)
    d = exc.value.detail
    assert exc.value.status_code == 422
    assert [p["field"] for p in d["invalid_parameters"]] == [field], d


def test_g4_zero_emissivity_is_legal():
    """0 is a machine (bare polished aluminium), not a missing input."""
    assert _validate(emissivity=0.0) == ("robotics", "still")


# ---------------------------------------------------------------------------
# the cache key and the panel mirror — a parameter a mode does not use is not sent
# ---------------------------------------------------------------------------

_KEY_KW = dict(
    ambient_temp=40.0, h_conv=50.0, slot_k=0.0, rpm=1000.0, gamma_deg=2.0,
    I_phase_rms=14.708, n_steps_per_period=4, n_periods=1.0, mesh_size_mm=2.0,
    min_size_mm=0.35, outer_air_factor=1.3, n_sectors=4, coil_temp_c=120.0,
    component_mesh="", fluid="water", fluid_temp_in_c=25.0, flow_lpm=0.0)


def test_h_a_request_that_is_not_robotics_keys_exactly_as_it_always_did():
    """No emissivity, no end faces, no mount: the tuple this function built
    before today, or the first request after this change misses every map the
    process already has."""
    from motor_ai_sim.routes.thermal import _field_cache_key

    base = _field_cache_key(None, {}, cooling_mode="air", air_speed_mps=5.0,
                            **_KEY_KW)
    # Values nothing reads must not split the cache.
    assert _field_cache_key(None, {}, cooling_mode="air", air_speed_mps=5.0,
                            emissivity=0.3, end_faces="none", end_face_sides=1,
                            **_KEY_KW) == base
    assert "robotics" not in base and "mount" not in base


def test_h2_the_robotics_inputs_and_the_mount_key_two_answers():
    """A polished housing, a buried end face and a bolted flange are three
    different machines — and the mount keys in EVERY mode, because a jacketed
    machine is bolted to something too."""
    from motor_ai_sim.routes.thermal import _field_cache_key

    r = _field_cache_key(None, {}, cooling_mode="robotics", air_speed_mps=0.0,
                         **_KEY_KW)
    assert r != _field_cache_key(None, {}, cooling_mode="robotics",
                                 air_speed_mps=0.0, emissivity=0.0, **_KEY_KW)
    assert r != _field_cache_key(None, {}, cooling_mode="robotics",
                                 air_speed_mps=0.0, end_faces="none", **_KEY_KW)
    assert r != _field_cache_key(None, {}, cooling_mode="robotics",
                                 air_speed_mps=0.0, end_face_sides=1, **_KEY_KW)
    assert r != _field_cache_key(None, {}, cooling_mode="robotics",
                                 air_speed_mps=0.0, mount_g_w_per_k=2.0,
                                 **_KEY_KW)
    air = _field_cache_key(None, {}, cooling_mode="air", air_speed_mps=5.0,
                           **_KEY_KW)
    assert air != _field_cache_key(None, {}, cooling_mode="air",
                                   air_speed_mps=5.0, mount_g_w_per_k=2.0,
                                   **_KEY_KW)
    # …and the HEAT PATH (2026-09-26): every option but 'none' is its own
    # answer, and 'none' keys exactly like a request from before it existed.
    hps = {_field_cache_key(None, {}, cooling_mode="robotics",
                            air_speed_mps=0.0, heat_path=hp, **_KEY_KW)
           for hp in ("housing", "shaft", "both")}
    assert len(hps) == 3 and r not in hps
    assert _field_cache_key(None, {}, cooling_mode="robotics",
                            air_speed_mps=0.0, heat_path="none",
                            **_KEY_KW) == r
    # …and the mount TEMPERATURE is part of it: the same flange onto a 20 °C arm
    # and onto a 60 °C one are two answers.
    assert (_field_cache_key(None, {}, cooling_mode="robotics",
                             air_speed_mps=0.0, mount_g_w_per_k=2.0,
                             mount_temp_c=20.0, **_KEY_KW)
            != _field_cache_key(None, {}, cooling_mode="robotics",
                                air_speed_mps=0.0, mount_g_w_per_k=2.0,
                                mount_temp_c=60.0, **_KEY_KW))


def test_h3_the_panel_mirror_sends_the_robotics_fields_only_in_robotics():
    """``thermal_settings.cooling_fields`` is the server's copy of the tab's own
    request builder, and the rule that makes it more than a rename: a parameter
    the chosen mode does not use is NOT sent, because the solver keys its cache
    on it."""
    from motor_ai_sim.thermal_settings import cooling_fields, cooling_issue

    air = cooling_fields({"coolMode": "air", "airSpeed": "10",
                          "boreMode": "none", "emissivity": "0.5",
                          "mountG": "2", "endFaces": "none"})
    assert set(air) == {"cooling_mode", "ambient_temp", "bore_mode",
                        "air_speed_mps"}

    rob = cooling_fields({"coolMode": "robotics", "boreMode": "still",
                          "ambientT": "40", "emissivity": "0.9",
                          "heatPath": "shaft"})
    assert rob == {"cooling_mode": "robotics", "ambient_temp": 40.0,
                   "bore_mode": "still", "emissivity": 0.9,
                   "end_faces": "still", "end_face_sides": 2,
                   "heat_path": "shaft"}

    # OLD SAVED SETTINGS (2026-09-26): a store written before the heat path
    # carries the mount fields — any mount, the robot link included, maps to
    # the housing; a mount of 0 to no contact.  No mount key is ever sent.
    old = cooling_fields({"coolMode": "robotics", "boreMode": "still",
                          "ambientT": "40", "emissivity": "0.9",
                          "mountG": "2", "mountT": "40"})
    assert old["heat_path"] == "housing"
    assert "mount_g_w_per_k" not in old and "mount_temp_c" not in old
    link = cooling_fields({"coolMode": "robotics", "mountG": "2",
                           "mountMode": "link", "linkPreset": "finger",
                           "linkMaterial": "steel"})
    assert link["heat_path"] == "housing"
    assert not {"mount_mode", "link_preset", "link_material"} & set(link)

    # 'none' is not sent (the router's default); a side count beside
    # `end_faces: none` is the unused cache-splitting parameter the rule is
    # about.
    bare = cooling_fields({"coolMode": "robotics", "boreMode": "still",
                           "ambientT": "40", "endFaces": "none",
                           "endFaceSides": "1", "mountG": "0"})
    assert bare == {"cooling_mode": "robotics", "ambient_temp": 40.0,
                    "bore_mode": "still", "emissivity": 0.9,
                    "end_faces": "none"}

    # …and the orchestrator refuses the impossible before it spends an
    # electromagnetic run on it.  The panel offers no mount any more, so a
    # saved mountG no longer rescues a machine with no film and no bore.
    assert cooling_issue({"coolMode": "none", "boreMode": "none"}) == (
        "no cooled surface — the heat has nowhere to leave")
    assert cooling_issue({"coolMode": "none", "boreMode": "none",
                          "mountG": "2"}) is not None
    assert "robotics" in (cooling_issue({"coolMode": "air",
                                         "boreMode": "still"}) or "")
    assert cooling_issue({"coolMode": "robotics", "boreMode": "still",
                          "emissivity": "1.4"}) == (
        "emissivity must be between 0 and 1")
