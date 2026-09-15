"""The duty-cycle network: analytic where it can be, the L13 where it matters.

``thermal_duty_cycle`` is a pure module, so this suite solves nothing
electromagnetic and nothing two-dimensional.  It pins the model in three layers:

  (a) ANALYTIC — a one-node machine has to reproduce
      ``T(t) = T∞ + (P/G)(1 − e^{−t/τ})`` to 1e-4, and the first law has to
      close over a whole cycle.  An integrator that cannot do the exponential
      cannot do anything else either;
  (b) THE L13 — the Ø85 / 13 mm robot joint, with the real steady maps this
      network is fitted to (solved 2026-09-14 through
      ``routes.thermal.solve_thermal_field``, 12 steps/period, 1.5 mm mesh,
      4 sectors, ambient 40 °C, bore open in still air, and a manual 300 W/m²·K
      film standing in for the mount so the calibration map sits at a realistic
      temperature).  The network fitted at the RATED point is asked about the
      PEAK point and has to land on the peak's own 2-D answer; the S2 pull has
      to sit inside the two bounds anybody can compute by hand (adiabatic copper
      below, perfectly-mixed machine above);
  (c) REFUSALS — a cycle with no periodic state, a locked rotor, a mixed-speed
      cycle and an unknown duty are all refused BY NAME, because an answer of
      "185 °C" for a machine heading to 600 °C is the worst output this feature
      could have.

The winding limit is 200 °C everywhere (the project's class,
``report.PROJECT_INSULATION_C``), judged on the HOT SPOT.
"""
from __future__ import annotations

import math

import pytest

import motor_ai_sim.thermal_duty_cycle as dc
from motor_ai_sim.thermal_capacities import NODES, part_capacities
from tests.test_thermal_capacities import (L13_MATERIALS, L13_PARTS,
                                           L13_SUMMARY)

# ---------------------------------------------------------------------------
# The L13, as solved on 2026-09-14 — see the module docstring for the settings
# ---------------------------------------------------------------------------

RATED_SUMMARY = dict(L13_SUMMARY, **{
    "P_stranded_W": 59.8, "P_core_W": 2.6, "P_solid_W": 1.3,
    "P_loss_total_W": 63.7, "coil_temp_C": 120, "rpm": 1000,
    "end_winding_factor": 2.03, "A_copper_slotted_mm2": 453.6,
    "P_core_terms": {
        "stator": {"hysteresis_W": 1.544, "eddy_W": 0.327, "excess_W": 0.568},
        "rotor": {"hysteresis_W": 0.078, "eddy_W": 0.056, "excess_W": 0.041}}})

PEAK_SUMMARY = dict(RATED_SUMMARY, **{
    "P_stranded_W": 676.1, "P_core_W": 2.5, "P_solid_W": 8.2,
    "P_loss_total_W": 686.9, "coil_temp_C": 200,
    "P_core_terms": {
        "stator": {"hysteresis_W": 1.466, "eddy_W": 0.310, "excess_W": 0.549},
        "rotor": {"hysteresis_W": 0.088, "eddy_W": 0.057, "excess_W": 0.050}}})

RATED_MAP = {
    "components": {"winding": {"max": 106.1, "avg": 103.7},
                   "stator": {"max": 97.4, "avg": 94.5},
                   "rotor": {"max": 95.7, "avg": 95.7},
                   "magnet": {"max": 95.7, "avg": 95.6}},
    "cooling": {
        "outer": {"mode": "manual", "h_conv": 300.0, "t_sink_c": 40.0,
                  "area_m2": 0.003994, "heat_removed_W": 60.31},
        "heat_budget": {"losses_W": 62.0, "housing_W": 60.31, "bore_W": 1.69,
                        "gap_W": -0.47, "shaft_ends_W": 0.0, "coil_W": 55.33,
                        "residual_pct": 0.0},
        "mech_losses": {"P_bearings_W": 0.0, "P_windage_gap_W": 0.0,
                        "shaft_ends_open": False}},
    "P_cu_exact_W": 58.5735522, "P_mag_eddy_W": 0.3,
    "ambient_temp": 40.0, "T_max": 106.1,
}

PEAK_MAP = {
    "components": {"winding": {"max": 777.0, "avg": 749.5},
                   "stator": {"max": 674.8, "avg": 642.4},
                   "rotor": {"max": 630.7, "avg": 629.9},
                   "magnet": {"max": 630.7, "avg": 629.5}},
    "cooling": {
        "outer": {"mode": "manual", "h_conv": 300.0, "t_sink_c": 40.0,
                  "area_m2": 0.003994, "heat_removed_W": 667.23},
        "heat_budget": {"losses_W": 685.15, "housing_W": 667.23,
                        "bore_W": 17.92, "gap_W": -11.78, "shaft_ends_W": 0.0,
                        "coil_W": 638.25, "residual_pct": 0.0},
        "mech_losses": {"P_bearings_W": 0.0, "P_windage_gap_W": 0.0,
                        "shaft_ends_open": False}},
    "P_cu_exact_W": 676.5750472, "P_mag_eddy_W": 1.0,
    "ambient_temp": 40.0, "T_max": 777.0,
}

#: The L13's geometry, as the die + the L13 configuration merge it (the fields
#: the end-face areas need, plus the radii the PHYSICAL gap and magnet
#: conductances are built from — ``config/dies/CIANO28 85 20SW1200/die.yaml``).
L13_GEO = {"stator_diameter": 85.0, "motor_length": 13.0, "num_seg": 4,
           "num_slots_per_segment": 6, "num_wires_per_slot": 18,
           "wire_width": 3.5, "wire_height": 0.3, "wire_spacing_y": 0.07,
           "wire_split": 1,
           "rotor_outer_radius": 32.8, "stator_inner_radius": 33.1,
           "air_gap": 0.3, "sleeve_thickness": 0.0, "magnet_height": 7.0}
STACK_M = L13_GEO["motor_length"] * 1e-3
D_HOUSING_M = L13_GEO["stator_diameter"] * 1e-3

DUTIES = {
    "rated": {"name": "rated", "rpm": 1000, "summary": RATED_SUMMARY},
    "peak": {"name": "peak", "rpm": 1000, "summary": PEAK_SUMMARY},
}
THERMAL_BY_DUTY = {"rated": RATED_MAP, "peak": PEAK_MAP}


@pytest.fixture(scope="module")
def caps():
    return part_capacities(L13_SUMMARY, L13_MATERIALS, L13_PARTS)


@pytest.fixture(scope="module")
def areas():
    return dc.end_face_areas(RATED_SUMMARY, stack_m=STACK_M, geometry=L13_GEO)


@pytest.fixture(scope="module")
def net_robot(areas):
    """The robot-joint network: mount 2 W/K at 40 °C, everything else still air."""
    return dc.network_from_steady(RATED_MAP, mount_g_w_per_k=2.0,
                                  mount_temp_c=40.0, side_areas=areas,
                                  d_housing_m=D_HOUSING_M, geometry=L13_GEO,
                                  calibration_duty="rated")


@pytest.fixture(scope="module")
def net_cal():
    """The network under the CALIBRATION map's own boundary condition.

    No mount, no end faces, the housing film held at the map's own conductance —
    the only network that can be compared with a 2-D solve like for like.
    """
    return dc.network_from_steady(RATED_MAP, geometry=L13_GEO,
                                  calibration_duty="rated")


# ---------------------------------------------------------------------------
# (a) analytic
# ---------------------------------------------------------------------------

def _one_node_network(g_w_per_k: float, t_amb: float = 25.0) -> dc.Network:
    """Every node merged into one, a single conductance to a sink."""
    return dc.Network(
        G={"w_s": 0.0, "r_s": 0.0, "m_r": 0.0, "s_mount": float(g_w_per_k),
           "r_bore": 0.0, "r_shaft": 0.0},
        areas={}, t_ambient_c=t_amb, t_mount_c=t_amb,
        node_of={n: "stator" for n in NODES},
        merged=(("winding", "stator"), ("rotor", "stator"),
                ("magnet", "stator")))


def test_one_node_reproduces_the_exponential():
    """T(t) = T∞ + (P/G)(1 − e^{−t/τ}) to 1e-4 of the rise."""
    C, G, P, t_amb = 200.0, 2.0, 100.0, 25.0
    net = _one_node_network(G, t_amb)
    caps = {n: C / 4.0 for n in NODES}
    seg = dc.Segment("const", 400.0, {"stator": P, "winding": 0.0,
                                      "rotor": 0.0, "magnet": 0.0},
                     rpm=1000.0, copper_feedback=False)
    prof = dc.Profile("S1", (seg,), cycle_s=400.0, t_start_c=t_amb)
    tr = dc.integrate_profile(prof, net, caps, samples_per_segment=200)

    tau = C / G
    worst = 0.0
    for t, T in zip(tr.t_s, tr.T_c["winding"]):
        exact = t_amb + (P / G) * (1.0 - math.exp(-t / tau))
        worst = max(worst, abs(T - exact))
    assert worst < 1e-4 * (P / G), "worst deviation %.3e K" % worst
    # …and left alone it settles on P/G above ambient (here 4τ, i.e. 98.2 % of
    # the way there — the number the exponential itself says, not a round one).
    assert tr.T_c["stator"][-1] == pytest.approx(
        t_amb + (P / G) * (1.0 - math.exp(-4.0)), abs=1e-3)
    assert dc.steady_state(seg, net, caps)["stator"] == pytest.approx(
        t_amb + P / G, abs=0.02)


def test_the_first_law_closes_over_the_integration():
    """E_in − E_out = ΔU, to 0.1 %."""
    net = _one_node_network(2.0, 25.0)
    caps = {n: 50.0 for n in NODES}
    on = dc.Segment("on", 30.0, {"stator": 200.0, "winding": 0.0,
                                 "rotor": 0.0, "magnet": 0.0}, rpm=1000.0,
                    copper_feedback=False)
    off = dc.Segment(None, 30.0, {n: 0.0 for n in NODES}, rpm=0.0)
    prof = dc.Profile("S3", (on, off), cycle_s=60.0, ed_pct=50.0,
                      t_start_c=25.0)
    tr = dc.integrate_profile(prof, net, caps, n_cycles=3,
                              samples_per_segment=120)
    assert tr.closure_pct < 0.1, (tr.energy_in_J, tr.energy_out_J, tr.stored_J)


def test_the_copper_feedback_is_the_solvers_own_rho_law():
    """P_cu(T) rides ρ(T)/ρ(T_ref), both referred to 20 °C."""
    assert dc.cu_rho_ratio(120.0, 120.0) == pytest.approx(1.0)
    # 120 → 180 °C is the step the coupled loop quotes: 1.169, not 1.236.
    assert (dc.cu_rho_ratio(180.0) / dc.cu_rho_ratio(120.0)
            == pytest.approx(1.169, abs=0.002))


# ---------------------------------------------------------------------------
# (b) the L13
# ---------------------------------------------------------------------------

def test_the_network_is_fitted_where_it_can_be_and_physical_where_it_cannot(
        net_robot):
    """G_ws is the copper over the winding-to-core drop; the two links the map
    cannot divide out keep the interface's OWN conductance and say so."""
    assert net_robot.G["w_s"] == pytest.approx(58.5735 / (103.7 - 94.5),
                                               rel=1e-3)
    # The rotor makes 1 W and sits 1.2 K from the stator MEAN with the heat
    # crossing the other way; the magnets are 0.1 K from the rotor.  Neither is
    # a fit — and neither is merged any more: four nodes, three conductances.
    assert net_robot.active_nodes == ("winding", "stator", "rotor", "magnet")
    assert net_robot.merged == ()
    assert net_robot.link_kinds() == {"w_s": "calibrated", "r_s": "physical",
                                      "m_r": "physical"}
    assert net_robot.G["r_s"] > 0.0 and net_robot.G["m_r"] > 0.0
    assert any("wrong sign" in n for n in net_robot.notes)
    assert any("PHYSICAL" in n and "k_eff·A/δ" in n for n in net_robot.notes)
    # The bore is a real film and stays: 1.69 W over (95.7 − 40) K.
    assert net_robot.G["r_bore"] == pytest.approx(1.69 / 55.7, rel=1e-3)
    assert net_robot.G["s_mount"] == 2.0
    assert net_robot.hot_spot_offset_k == pytest.approx(2.4, abs=0.01)


def test_a_resolved_delta_T_keeps_its_CALIBRATED_conductance(degenerate_map):
    """The rule that did NOT change: a link the map resolves is still divided
    out of the map, geometry or no geometry.

    The rotor is 5.1 K above the stator with 1 W crossing and the magnets 1.0 K
    above the rotor with 0.5 W: both are fits, and handing the network the
    geometry must not replace them with a formula.
    """
    for kwargs in ({}, {"geometry": L13_GEO}):
        net = dc.network_from_steady(degenerate_map, **kwargs)
        assert net.G["r_s"] == pytest.approx(1.0 / (105.0 - 99.9), rel=1e-6)
        assert net.G["m_r"] == pytest.approx(0.5 / (106.0 - 105.0), rel=1e-6)
        assert net.links["r_s"]["kind"] == "calibrated"
        assert net.links["m_r"]["kind"] == "calibrated"
        assert "fitted to the calibration map" in net.links["r_s"]["basis"]
        assert ("rotor", "stator") not in net.merged


def test_two_nodes_0_2_K_apart_with_no_geometry_are_merged_with_a_note(
        degenerate_map):
    """The last-resort rule: winding↔stator has no closed form to fall back on,
    so a map that cannot resolve it still merges — loudly."""
    net = dc.network_from_steady(degenerate_map, geometry=L13_GEO)
    assert ("winding", "stator") in net.merged        # 0.2 K apart
    assert net.G["w_s"] == 0.0
    assert net.links["w_s"]["kind"] == "merged"
    note = [n for n in net.notes if n.startswith("winding and stator")][0]
    assert "0.50 K" in note and "MERGED" in note
    assert "NOT conservative for the winding" in note
    # winding folded into the stator; the rotor and the magnets stand alone
    assert net.active_nodes == ("stator", "rotor", "magnet")


@pytest.fixture(scope="module")
def degenerate_map():
    """A map built so that ONE pair is unresolvable and two are fits."""
    return {
        "components": {"winding": {"max": 100.2, "avg": 100.1},
                       "stator": {"max": 100.0, "avg": 99.9},
                       "rotor": {"max": 105.2, "avg": 105.0},
                       "magnet": {"max": 106.1, "avg": 106.0}},
        "cooling": {"outer": {"area_m2": 0.004, "t_sink_c": 40.0},
                    "heat_budget": {"losses_W": 60.0, "housing_W": 58.0,
                                    "bore_W": 2.0, "gap_W": 1.0,
                                    "shaft_ends_W": 0.0}},
        "P_cu_exact_W": 55.0, "P_mag_eddy_W": 0.5, "ambient_temp": 40.0}


def test_the_end_face_areas_are_the_geometrys_own(areas):
    """54 cm² of end turns against 40 cm² of housing — the reason the axial
    paths are in this model at all."""
    ell = (2.03 - 1.0) * STACK_M / 2.0
    per = 2.0 * (18 * (0.3 + 0.07)) * 1e-3 + 3.5e-3      # 2t + w, shielded
    assert areas["winding_ends"] == pytest.approx(24 * 2 * per * ell, rel=1e-6)
    assert areas["winding_ends"] * 1e4 == pytest.approx(54.0, abs=0.5)
    # the cores: section area × 2 sides, off the mass rows
    assert areas["stator_ends"] * 1e4 == pytest.approx(25.4, abs=0.3)
    assert areas["rotor_ends"] * 1e4 == pytest.approx(18.9, abs=0.3)
    assert areas["magnet_ends"] * 1e4 == pytest.approx(14.3, abs=0.3)
    assert "end_winding_area" in areas["basis"]["winding_ends"]


def test_the_end_faces_carry_more_than_the_housing(net_robot):
    """The user's correction, as a number.

    At ΔT 60 K over 40 °C air the machine's exposed AXIAL faces are worth
    0.24 W/K against the housing cylinder's 0.06 W/K — four times as much — and
    the coil ends alone are 2.5× the housing.  A model without them would put
    every one of those watts through the mount.
    """
    t_wall = 100.0
    g_house = dc.still_air_G(t_wall, net_robot, "housing")
    g_coils = dc.still_air_G(t_wall, net_robot, "winding_ends")
    g_sides = sum(dc.still_air_G(t_wall, net_robot, k)
                  for k in ("winding_ends", "stator_ends", "rotor_ends",
                            "magnet_ends"))
    assert g_house == pytest.approx(0.057, abs=0.008)
    assert g_coils > 2.0 * g_house
    assert g_sides > 3.5 * g_house
    # h rises with the wall temperature (radiation and Rayleigh both do)
    assert (dc.still_air_G(140.0, net_robot, "housing")
            > dc.still_air_G(60.0, net_robot, "housing"))


def test_S1_on_the_network_reproduces_the_calibration_map(net_cal, caps):
    """The point of fitting to a map: put the map's own losses back in and the
    map's own temperatures come out.

    The residual is 0.5 K and it is bookkeeping, not physics: the map integrates
    62.0 W of loss density over its mesh while the summary's terms add to
    62.5 W, and 0.5 W over the 1.14 W/K this machine has is 0.4 K.
    """
    loss = dc.losses_by_node(RATED_SUMMARY, RATED_MAP)
    seg = dc.Segment("rated", 1.0, dc.node_watts(loss),
                     coil_ref_c=loss["coil_ref_c"], rpm=1000.0,
                     copper_feedback=False)
    st = dc.steady_state(seg, net_cal, caps)
    assert st["winding"] == pytest.approx(103.7, abs=1.0)
    assert st["stator"] == pytest.approx(94.5, abs=1.0)
    assert abs(st["winding"] - st["stator"]) == pytest.approx(9.2, abs=0.3)


def test_the_rated_network_predicts_the_peak_points_own_2D_solve(net_cal, caps):
    """THE cross-point test: eleven times the copper loss, same network.

    The network is fitted at 14.7 A and asked about 46 A — a winding 646 K above
    the point it was calibrated at — and it has to land on the temperature the
    peak point's OWN 2-D solve produced.  Residual on the winding: 1.3 K.
    """
    loss = dc.losses_by_node(PEAK_SUMMARY, PEAK_MAP)
    seg = dc.Segment("peak", 1.0, dc.node_watts(loss),
                     coil_ref_c=loss["coil_ref_c"], rpm=1000.0,
                     copper_feedback=False)
    st = dc.steady_state(seg, net_cal, caps)
    residual = st["winding"] - PEAK_MAP["components"]["winding"]["avg"]
    assert abs(residual) < 10.0, "winding residual %.2f K" % residual
    assert abs(st["stator"] - PEAK_MAP["components"]["stator"]["avg"]) < 10.0


def test_the_S2_pull_sits_between_the_two_hand_bounds(net_robot, caps):
    """40 °C → 200 °C at the peak point: 26 s, and it MUST be 10 s < t < 40 s.

    The bracket is the whole regression, because both ends are arithmetic:

      * adiabatic copper — the winding alone absorbs its own loss,
        41.2 J/K × 160 K / 676.6 W = 9.7 s.  Nothing can be faster;
      * perfectly mixed — the whole machine's 171.7 J/K rises together,
        171.7 × 160 / 687 = 40.0 s.  With any real conductance and any real
        cooling, nothing can be slower.
    """
    prof = dc.normalise_spec({"kind": "S2", "duty": "peak", "t_on_s": 600.0,
                              "t_start_c": 40.0}, DUTIES,
                             thermal_by_duty=THERMAL_BY_DUTY)
    lim = dc.default_limits()
    assert lim["winding"] == 200.0            # the project's class, not 180
    out = dc.time_to_limit(prof, net_robot, caps, limits=lim)

    adiabatic = 41.195 * 160.0 / 676.575
    mixed = 171.655 * 160.0 / 687.3
    assert adiabatic == pytest.approx(9.7, abs=0.2)
    assert mixed == pytest.approx(40.0, abs=0.3)
    assert adiabatic < out["s2_time_to_limit_s"] < mixed
    # 25.8 s with the rotor and the magnets on their own conductances.  Merging
    # them into the stator lump (the pre-2026-09-15 network) reads 33 s here —
    # the coil borrowing 71 J/K it is not in contact with.
    assert out["s2_time_to_limit_s"] == pytest.approx(25.8, abs=1.5)
    assert out["s2_limiting_part"] == "winding"
    assert "200 °C" in out["note"]


def test_a_point_that_never_reaches_the_limit_says_this_is_S1(net_robot, caps):
    """The other S2 answer — and it is an answer, not a null."""
    prof = dc.normalise_spec({"kind": "S2", "duty": "rated", "t_on_s": 60.0,
                              "t_start_c": 40.0}, DUTIES,
                             thermal_by_duty=THERMAL_BY_DUTY)
    out = dc.time_to_limit(prof, net_robot, caps)
    assert out["s2_time_to_limit_s"] is None
    assert out["s2_limiting_part"] is None
    assert "S1" in out["note"]


def test_the_S3_cycle_reaches_a_periodic_state_and_the_budget_closes(net_robot,
                                                                    caps):
    """25 % of 60 s at the peak, resting unpowered, mount 2 W/K at 40 °C."""
    prof = dc.normalise_spec({"kind": "S3", "duty": "peak", "ed_pct": 25.0,
                              "cycle_s": 60.0, "rest_duty": None,
                              "t_start_c": 40.0}, DUTIES,
                             thermal_by_duty=THERMAL_BY_DUTY)
    rec = dc.periodic_steady_state(prof, net_robot, caps,
                                   samples_per_segment=40)
    assert rec["converged"] and rec["n_cycles"] <= 20
    assert rec["residual_K"] < dc.PERIODIC_TOL_K
    assert rec["closure_pct"] < 0.1
    assert 150.0 < rec["winding_hot_peak_c"] < 220.0
    # the peak is the END of the pull and the minimum the end of the rest
    assert rec["peak_c"]["winding"] > rec["min_c"]["winding"]

    split = dc.average_split(rec["trace"], net_robot)
    assert abs(split["closure_pct"]) < 0.1
    # every path is its own line, and the axial ones are inside the two sides
    assert split["mount_W"] > split["housing_W"] > 0.0
    assert split["winding_end_faces_W"] > split["housing_W"]
    assert split["stator_side_W"] == pytest.approx(
        split["housing_W"] + split["mount_W"] + split["winding_end_faces_W"]
        + split["stator_end_faces_W"], abs=0.02)
    assert split["rotor_side_W"] == pytest.approx(
        split["bore_W"] + split["shaft_ends_W"] + split["rotor_end_faces_W"]
        + split["magnet_end_faces_W"], abs=0.02)
    assert split["stator_pct"] + split["rotor_pct"] == pytest.approx(100.0,
                                                                     abs=0.2)


def test_the_allowable_ED_is_monotone_and_lands_on_the_limit(net_robot, caps):
    """Hotter at every step up, and the answer is the ED that sits ON 200 °C."""
    prof = dc.normalise_spec({"kind": "S3", "duty": "peak", "ed_pct": 25.0,
                              "cycle_s": 60.0, "rest_duty": None,
                              "t_start_c": 40.0}, DUTIES,
                             thermal_by_duty=THERMAL_BY_DUTY)
    out = dc.allowable_ed(prof, net_robot, caps, curve_step_pct=25.0,
                          samples_per_segment=24)
    peaks = [p for _ed, p in out["ed_curve"] if p is not None]
    assert peaks == sorted(peaks), out["ed_curve"]
    assert out["limiting_part"] == "winding"
    assert 15.0 < out["ed_allowable_pct"] < 40.0
    assert out["ed_requested_pct"] == 25.0

    ed = out["ed_allowable_pct"]
    below = dc.periodic_steady_state(prof.with_ed(ed), net_robot, caps,
                                     samples_per_segment=24)
    above = dc.periodic_steady_state(prof.with_ed(ed + 0.5), net_robot, caps,
                                     samples_per_segment=24)
    assert below["winding_hot_peak_c"] <= 200.0
    assert above["winding_hot_peak_c"] > 200.0
    # the bisection is tight: half a percent of ED is under a kelvin here
    assert above["winding_hot_peak_c"] - below["winding_hot_peak_c"] < 5.0


# ---------------------------------------------------------------------------
# (b2) the PHYSICAL links, against the L13 what-if study of 2026-09-15
# ---------------------------------------------------------------------------
# The basis is the L13's own stored calibration map — the rated duty solved
# 2026-09-14 19:38 in the robotics mode at 40 °C with a 2 W/K mount — which is
# the map the user quotes and the one the study rebuilt everything on.  It is
# the interesting case because it is DEGENERATE: stator 64.5, rotor 65.5 and
# magnet 65.5 °C, so neither internal rotor link can be divided out of it.

CAL40_MAP = {
    "components": {"winding": {"max": 73.6, "avg": 72.3},
                   "stator": {"max": 65.1, "avg": 64.5},
                   "rotor": {"max": 65.6, "avg": 65.5},
                   "magnet": {"max": 65.6, "avg": 65.5}},
    "cooling": {
        "outer": {"mode": "robotics", "area_m2": 0.003994, "t_sink_c": 40.0,
                  "emissivity": 0.9},
        # the compacted record's gap block: k_eff and nothing else, so the
        # clearance and the mean diameter have to come from the geometry
        "gap": {"k_eff": 0.0283, "Ta": 28.0, "Nu": 1.0},
        "heat_budget": {"losses_W": 56.18, "housing_W": 1.11, "bore_W": 0.589,
                        "gap_W": -0.128, "shaft_ends_W": 0.0, "coil_W": 52.3},
        "mech_losses": {"P_bearings_W": 0.0, "P_windage_gap_W": 0.0,
                        "shaft_ends_open": False}},
    "P_cu_W": 52.3, "P_mag_eddy_W": 1.3, "ambient_temp": 40.0, "T_max": 73.6,
}

#: The map's own measured end-face areas, with each face's characteristic
#: length inverted from the h_conv the map reports for it (the study's rule —
#: the transient films then reproduce the map's films at the calibration point).
CAL40_SIDES = {"winding_ends": 0.00538953, "stator_ends": 0.0026691,
               "rotor_ends": 0.00136992, "magnet_ends": 0.00142695,
               "char_len_m": {"winding_ends": 0.0046, "stator_ends": 0.0365,
                              "rotor_ends": 0.0262, "magnet_ends": 0.0267}}

CAL40_CAPS = {"winding": 41.195, "stator": 59.22, "rotor": 39.04,
              "magnet": 32.20}
#: Peak (200 °C coil reference) and rated-hold (72.5 °C) watts per node.
PEAK40_W = {"winding": 676.10, "stator": 2.277, "rotor": 0.190, "magnet": 8.200}
REST40_W = {"winding": 52.30, "stator": 2.405, "rotor": 0.183, "magnet": 1.300}


def _net40(**kw) -> dc.Network:
    return dc.network_from_steady(
        CAL40_MAP, mount_g_w_per_k=2.0, mount_temp_c=40.0,
        side_areas=CAL40_SIDES, d_housing_m=D_HOUSING_M, t_ambient_c=40.0,
        emissivity=0.9, geometry=L13_GEO, calibration_duty="rated", **kw)


def _s3_40(net, ed_pct: float = 25.0):
    on = dc.Segment("peak", 60.0 * ed_pct / 100.0, PEAK40_W, coil_ref_c=200.0,
                    rpm=1000.0)
    rest = dc.Segment("rated", 60.0 * (100.0 - ed_pct) / 100.0, REST40_W,
                      coil_ref_c=72.5, rpm=1000.0)
    prof = dc.Profile("S3", (on, rest), cycle_s=60.0, ed_pct=ed_pct,
                      t_start_c=40.0)
    return dc.periodic_steady_state(prof, net, CAL40_CAPS,
                                    samples_per_segment=60)


def test_a_degenerate_L13_map_gets_the_studys_own_conductances():
    """The two numbers §4 of the what-if computed by hand, from the module.

    G_rs 0.291 W/K (0.254 conduction + 0.037 radiation) and G_mr 4.58 W/K, to
    10 %.  The conduction half and G_mr are reproduced exactly; the radiation
    half here is the two-surface grey formula 1/(1/ε_r + 1/ε_s − 1), which is
    about half of what the study put in its table, and it moves the total by
    6 % — inside the band the study itself calls physically defensible
    (0.25-0.29 W/K, worth 1 K on the magnets).
    """
    net = _net40()
    # NOT merged: four nodes, and the magnets keep their own 32 J/K
    assert net.active_nodes == ("winding", "stator", "rotor", "magnet")
    assert net.merged == ()
    assert net.link_kinds() == {"w_s": "calibrated", "r_s": "physical",
                                "m_r": "physical"}

    assert net.G["w_s"] == pytest.approx(52.3 / (72.3 - 64.5), rel=1e-4)
    assert net.G["r_s"] == pytest.approx(0.291, rel=0.10)
    assert net.G["m_r"] == pytest.approx(4.58, rel=0.10)

    r_s = net.links["r_s"]
    assert r_s["G_conduction_W_per_K"] == pytest.approx(0.254, rel=0.02)
    assert r_s["G_radiation_W_per_K"] > 0.0
    assert r_s["area_m2"] * 1e4 == pytest.approx(26.91, abs=0.05)   # π·D·L
    assert r_s["delta_m"] == pytest.approx(0.0003, rel=1e-6)        # gap − sleeve
    assert r_s["k_eff_W_per_mK"] == 0.0283 and "gap block" in r_s["k_source"]
    m_r = net.links["m_r"]
    assert m_r["area_m2"] * 1e4 == pytest.approx(21.07, abs=0.05)
    assert m_r["k_W_per_mK"] == dc.MAGNET_K_DEFAULT == 7.6
    # the formula is in the record, not only the number
    assert "k_eff·A/δ + h_rad·A" in net.as_record()["links"]["r_s"]["basis"]
    assert "k·A_root/(h_mag/2)" in net.as_record()["links"]["m_r"]["basis"]


def test_the_physical_links_are_winding_conservative_and_magnet_honest():
    """§4 of the study, as the two numbers a datasheet would quote.

    S2 from cold is 26.6 s with the rotor on its own conductance against 35.6 s
    merged — the merge lends the coil 71 J/K it is not in contact with, which is
    a quarter of the pull — and the 25 %/60 s magnet peak is 120 °C against the
    merged 144 °C, which is 23 K of false alarm against a 150 °C grade.
    """
    net, merged = _net40(), _net40(allow_merge=True)

    prof = dc.Profile("S2", (dc.Segment("peak", 600.0, PEAK40_W,
                                        coil_ref_c=200.0, rpm=1000.0),),
                      cycle_s=600.0, t_on_s=600.0, t_start_c=40.0)
    s2 = dc.time_to_limit(prof, net, CAL40_CAPS, limits={"winding": 200.0})
    s2_merged = dc.time_to_limit(prof, merged, CAL40_CAPS,
                                 limits={"winding": 200.0})
    assert s2["s2_limiting_part"] == "winding"
    assert s2["s2_time_to_limit_s"] == pytest.approx(26.6, abs=0.5)
    assert s2_merged["s2_time_to_limit_s"] == pytest.approx(35.6, abs=0.5)

    rec, rec_merged = _s3_40(net), _s3_40(merged)
    assert rec["peak_c"]["magnet"] == pytest.approx(120.0, abs=2.0)
    assert rec_merged["peak_c"]["magnet"] == pytest.approx(143.8, abs=2.0)
    # the magnets float at the stator's MEAN rather than being dragged to its
    # peak, and the winding peak itself barely moves (the periodic state is set
    # by the steady balance, not by the capacity)
    assert rec["peak_c"]["stator"] > rec["peak_c"]["rotor"] > rec["peak_c"]["magnet"]
    assert rec["winding_hot_peak_c"] == pytest.approx(
        rec_merged["winding_hot_peak_c"], abs=3.0)


# ---------------------------------------------------------------------------
# (b3) THE TOOL FINDS THE REGIME — user 2026-09-15
# ---------------------------------------------------------------------------
# «с помощью Duty cycle мы можем подобрать такой режим работы мотора, чтобы он
# смог уложиться в температурные лимиты — то есть мы сами находим это время /
# S3 ED, при котором всё нормально».  The duty cycle does not grade a duty ratio
# somebody typed; it ANSWERS with the one this machine holds.  On the same L13
# basis as (b2) — the degenerate 40 °C calibration map with the physical links —
# those answers are: 21.6 % of a 60 s cycle, one pull of 26.6 s from cold and of
# 20.7 s from the rated state.


#: The only limit this machine is judged against in these tests — the project's
#: insulation class.  The magnets are not judged (no card maximum), which is
#: what makes the winding the limiting part in every assertion below.
LIM40 = {"winding": 200.0}


def _s3_40_profile(ed_pct=None, cycle_s: float = 60.0) -> dc.Profile:
    """The 60 s peak/rated cycle, with the duty ratio STATED or left to be found.

    ``ed_pct=None`` is the reframed request: the block carries no ratio, the
    segments are built at the seed and ``ed_given`` is False, so nothing may
    report the seed as something the user chose.
    """
    given = ed_pct is not None
    ed = dc.ED_SEED_PCT if ed_pct is None else float(ed_pct)
    on = dc.Segment("peak", cycle_s * ed / 100.0, PEAK40_W, coil_ref_c=200.0,
                    rpm=1000.0)
    rest = dc.Segment("rated", cycle_s * (100.0 - ed) / 100.0, REST40_W,
                      coil_ref_c=72.5, rpm=1000.0)
    return dc.Profile("S3", (on, rest), cycle_s=cycle_s, ed_pct=ed,
                      t_start_c=40.0, ed_given=given)


def _s2_40_profile() -> dc.Profile:
    return dc.Profile("S2", (dc.Segment("peak", 600.0, PEAK40_W,
                                        coil_ref_c=200.0, rpm=1000.0),),
                      cycle_s=600.0, t_on_s=600.0, t_start_c=40.0)


def test_an_S3_with_no_ED_is_a_request_to_FIND_one():
    """A blank duty ratio is normalised, a typed 0 is still refused.

    The two cannot be read the same way: "find me a regime" and "run this point
    for no time at all" are different requests, and a block that quietly became
    the other one is the whole class of bug this module refuses to ship.
    """
    found = dc.normalise_spec({"kind": "S3", "duty": "peak", "cycle_s": 60.0,
                               "rest_duty": None, "t_start_c": 40.0},
                              DUTIES, thermal_by_duty=THERMAL_BY_DUTY)
    assert found.ed_given is False
    assert found.ed_pct == dc.ED_SEED_PCT      # the SEED, and it says so
    assert "seed" in found.note and "found" in found.note

    asked = dc.normalise_spec({"kind": "S3", "duty": "peak", "ed_pct": 25.0,
                               "cycle_s": 60.0, "rest_duty": None,
                               "t_start_c": 40.0},
                              DUTIES, thermal_by_duty=THERMAL_BY_DUTY)
    assert asked.ed_given is True and asked.ed_pct == 25.0

    for bad in (0, 0.0, -5, 140):
        with pytest.raises(dc.DutyCycleError) as e:
            dc.normalise_spec({"kind": "S3", "duty": "peak", "ed_pct": bad,
                               "cycle_s": 60.0}, DUTIES,
                              thermal_by_duty=THERMAL_BY_DUTY)
        assert e.value.code == "duty_cycle_bad_ed"
    # …and the blank one keeps its identity through the search's own rewrite
    assert _s3_40_profile().with_ed(21.6).ed_given is False


def test_the_found_regime_is_the_L13s_own_21_6_pct_at_60_s():
    """The headline the panel prints, from the module: 21.6 % at 60 s.

    The ED is not compared against a request — there was none — and the
    temperatures AT the found point come back with it, because "and how hot is
    it there?" is the next question every time.
    """
    ed = dc.allowable_ed(_s3_40_profile(), _net40(), CAL40_CAPS, limits=LIM40,
                         curve_step_pct=25.0, samples_per_segment=40)
    assert ed["ed_allowable_pct"] == pytest.approx(21.6, abs=0.5)
    assert ed["limiting_part"] == "winding"
    # nothing was asked for, so nothing is reported as asked for
    assert ed["ed_requested_pct"] is None

    at = ed["at_allowable"]
    # the FEASIBLE side of the bracket: the found cycle sits ON the class, never
    # a bisection step over it
    assert at["winding_hot_peak_c"] == pytest.approx(200.0, abs=1.0)
    assert at["winding_hot_peak_c"] <= 200.0
    assert at["magnet_peak_c"] == pytest.approx(111.0, abs=5.0)
    assert set(at["peak_c"]) == set(dc.NODES) and set(at["mean_c"]) == set(dc.NODES)
    # …and the magnets are nowhere near the winding, which is the whole point of
    # keeping them on their own conductance (b2)
    assert at["peak_c"]["stator"] > at["peak_c"]["rotor"] >= at["peak_c"]["magnet"]

    # a STATED ratio is still graded, and the found one is reported beside it
    graded = dc.allowable_ed(_s3_40_profile(25.0), _net40(), CAL40_CAPS,
                             limits=LIM40, curve_step_pct=50.0,
                             samples_per_segment=40)
    assert graded["ed_requested_pct"] == 25.0
    assert graded["ed_allowable_pct"] == pytest.approx(21.6, abs=0.5)


def test_the_allowable_ED_falls_as_the_cycle_lengthens():
    """The curve that makes the single number mean something.

    A short period is ridden out on the machine's own heat capacity; a long one
    has to be in thermal balance, so the allowable ratio falls towards the
    continuous answer — 32 % of 10 s down to 7 % of 300 s on this joint.  Every
    row sits on the class, so the curve is the SAME answer at five periods and
    not five different judgements.
    """
    net = _net40()
    rows = dc.ed_vs_cycle(_s3_40_profile(), net, CAL40_CAPS, limits=LIM40,
                          samples_per_segment=24)
    assert [r["cycle_s"] for r in rows] == list(dc.ED_CYCLE_LENGTHS_S)

    eds = [r["ed_allowable_pct"] for r in rows]
    assert all(e is not None for e in eds)
    assert eds == sorted(eds, reverse=True), eds        # monotone, strictly:
    assert all(a > b for a, b in zip(eds, eds[1:])), eds
    at60 = next(r for r in rows if r["cycle_s"] == 60.0)
    assert at60["ed_allowable_pct"] == pytest.approx(21.6, abs=1.0)

    for r in rows:
        # the ON time is the ratio of THIS period, spelled out so a panel never
        # has to multiply it back out
        assert r["t_on_s"] == pytest.approx(
            r["cycle_s"] * r["ed_allowable_pct"] / 100.0, abs=0.01)
        assert r["limiting_part"] == "winding"
        assert r["winding_hot_peak_c"] == pytest.approx(200.0, abs=2.0)
    # the longer the cycle, the longer the single pull and the COOLER the
    # magnets — the machine is trading peak duty for thermal balance
    assert rows[0]["t_on_s"] < rows[-1]["t_on_s"]
    assert rows[0]["magnet_peak_c"] > rows[-1]["magnet_peak_c"]


def test_the_same_pull_is_shorter_out_of_a_warm_machine():
    """S2 from cold, from the rated state and out of the settled cycle.

    26.6 s is the number a datasheet would quote and it is the LONGEST of the
    three: it starts from a machine that has been standing in the room.  The
    joint that has been holding its rated point all morning has 20.7 s, and the
    one already living in its own 25 % cycle has less again.
    """
    net = _net40()
    cold = dc.time_to_limit(_s2_40_profile(), net, CAL40_CAPS, limits=LIM40)
    assert cold["s2_time_to_limit_s"] == pytest.approx(26.6, abs=0.5)

    # the calibration map IS the rated state — no second solve, only a read
    rated_state = dc.map_state_c(CAL40_MAP)
    assert rated_state == {"winding": 72.3, "stator": 64.5, "rotor": 65.5,
                           "magnet": 65.5}
    from_rated = dc.time_to_limit(_s2_40_profile(), net, CAL40_CAPS,
                                  limits=LIM40, T0=rated_state)
    assert from_rated["s2_time_to_limit_s"] == pytest.approx(20.7, abs=0.5)
    assert from_rated["s2_limiting_part"] == "winding"

    cyc = _s3_40(net, 25.0)
    from_cycle = dc.time_to_limit(_s2_40_profile(), net, CAL40_CAPS,
                                  limits=LIM40,
                                  T0={n: cyc["mean_c"][n] for n in dc.NODES})
    assert from_cycle["s2_time_to_limit_s"] < from_rated["s2_time_to_limit_s"]
    assert from_cycle["s2_time_to_limit_s"] < cold["s2_time_to_limit_s"]

    # a payload with no component means is an answer of "no state", never a
    # crash and never a silent ambient
    assert dc.map_state_c({"components": {"winding": {"avg": 70.0}}}) == {}
    assert dc.map_state_c({}) == {}


# ---------------------------------------------------------------------------
# (c) refusals, by name
# ---------------------------------------------------------------------------

def test_a_cycle_with_no_periodic_state_is_refused(caps, areas):
    """100 % duty at the peak with a 0.1 W/K mount: there is no equilibrium
    below 400 °C, and the refusal says what to change."""
    net = dc.network_from_steady(RATED_MAP, mount_g_w_per_k=0.1,
                                 mount_temp_c=40.0, side_areas=areas,
                                 d_housing_m=D_HOUSING_M)
    prof = dc.normalise_spec({"kind": "S3", "duty": "peak", "ed_pct": 100.0,
                              "cycle_s": 60.0, "t_start_c": 40.0}, DUTIES,
                             thermal_by_duty=THERMAL_BY_DUTY)
    with pytest.raises(dc.DutyCycleError) as exc:
        dc.periodic_steady_state(prof, net, caps, samples_per_segment=24)
    assert exc.value.code == "duty_cycle_no_periodic_state"
    assert "mount conductance" in str(exc.value)


def test_a_locked_rotor_is_refused_by_name():
    duties = {"hold": {"name": "hold", "rpm": 0,
                       "summary": dict(PEAK_SUMMARY, rpm=0)}}
    with pytest.raises(dc.DutyCycleError) as exc:
        dc.normalise_spec({"kind": "S2", "duty": "hold", "t_on_s": 5.0},
                          duties)
    assert exc.value.code == "duty_cycle_locked_rotor"


def test_a_mixed_speed_cycle_is_refused_by_name():
    duties = dict(DUTIES)
    duties["fast"] = {"name": "fast", "rpm": 3000,
                      "summary": dict(RATED_SUMMARY, rpm=3000)}
    with pytest.raises(dc.DutyCycleError) as exc:
        dc.normalise_spec({"kind": "segments",
                           "segments": [{"duty": "peak", "t_s": 5.0},
                                        {"duty": "fast", "t_s": 5.0}]},
                          duties, thermal_by_duty=THERMAL_BY_DUTY)
    assert exc.value.code == "duty_cycle_mixed_speed"


def test_an_unknown_duty_and_a_bad_ED_are_refused():
    with pytest.raises(dc.DutyCycleError) as exc:
        dc.normalise_spec({"kind": "S2", "duty": "nope", "t_on_s": 5.0}, DUTIES)
    assert exc.value.code == "duty_cycle_unknown_duty"
    assert "peak" in str(exc.value)        # names what it DOES have

    for bad in ({"kind": "S3", "duty": "peak", "ed_pct": 0, "cycle_s": 60},
                {"kind": "S3", "duty": "peak", "ed_pct": 120, "cycle_s": 60}):
        with pytest.raises(dc.DutyCycleError) as exc:
            dc.normalise_spec(bad, DUTIES, thermal_by_duty=THERMAL_BY_DUTY)
        assert exc.value.code == "duty_cycle_bad_ed"

    with pytest.raises(dc.DutyCycleError) as exc:
        dc.normalise_spec({"kind": "S4", "duty": "peak"}, DUTIES)
    assert exc.value.code == "duty_cycle_bad_kind"


def test_the_losses_split_says_where_every_watt_went():
    """Copper from the map, iron from the Bertotti terms, the solid loss split
    between the magnets and the shaft — with a note for each judgement."""
    loss = dc.losses_by_node(RATED_SUMMARY, RATED_MAP)
    assert loss["winding"] == pytest.approx(58.574, abs=0.01)
    assert loss["stator"] == pytest.approx(1.544 + 0.327 + 0.568, abs=1e-3)
    # rotor iron + the 1.0 W of solid loss that is not in the magnets
    assert loss["rotor"] == pytest.approx(0.078 + 0.056 + 0.041 + 1.0,
                                          abs=1e-3)
    assert loss["magnet"] == pytest.approx(0.3, abs=1e-6)
    assert any("shaft" in n for n in loss["notes"])

    # With no thermal map the whole solid loss goes on the MAGNETS, and says so.
    bare = dc.losses_by_node(RATED_SUMMARY)
    assert bare["magnet"] == pytest.approx(1.3, abs=1e-6)
    assert any("conservative" in n for n in bare["notes"])
    assert bare["basis"]["P_cu_source"].startswith("summary")


def test_the_still_air_films_name_their_source(net_robot):
    """A provisional constant must never be quotable as a correlation."""
    for key, src in net_robot.h_sources.items():
        assert src, key
        if "provisional" in src:
            pytest.fail("%s is still on the provisional constant: %s"
                        % (key, src))
