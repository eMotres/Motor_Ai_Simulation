"""The cooling correlations, on their own — no app, no FEM, no config.

``simulation.cooling_models`` is the half of the thermal model that decides how
many watts a surface can take away.  Until 2026-09-07 it was four branches of an
``if`` inside a FastAPI route, which meant that checking "does more flow cool
better?" cost a full EM transient — so nobody checked, and the liquid branch was
INVERTED (the engineer typed the outlet temperature and the model answered with
the flow rate) for as long as it existed.

These are the properties an engineer would put a thumb on before believing any
of it, and every one of them runs in milliseconds:

  (a) MONOTONICITY.  More flow, more speed → more cooling.  A correlation ladder
      with a discontinuity at a regime seam fails this, and a discontinuity at a
      seam is a cooling system that gets worse when you turn the pump up.
  (b) THE LAMINAR FLOOR.  A duct's heat transfer does not fall to zero as the
      flow slows; it asymptotes to Nu = 3.66.  A turbulent correlation
      extrapolated downwards says it does.
  (c) THE FIRST LAW.  T_out > T_in whenever heat is being carried, and
      P = ṁ·cp·(T_out − T_in) exactly.
  (d) THE GAP.  δ is the MECHANICAL clearance (sleeve included) and Ta ∝ δ³, so
      a sleeve that is not subtracted is a gap conductivity that is hundreds of
      times too large.
  (e) THE SHAFT ENDS.  A fin is not a wetted area: past m·L ≈ 2.5 another
      millimetre of exposed shaft removes nothing, and a model that did not know
      that would sell a 300 mm stub as three times a 100 mm one.
"""
from __future__ import annotations

import math

import pytest

from motor_ai_sim.simulation import cooling_models as cm

WATER = cm.FluidProps(rho=1000.0, cp=4186.0, k=0.60, nu=1.0e-6, pr=7.0)


# ---------------------------------------------------------------------------
# Fluid properties
# ---------------------------------------------------------------------------

def test_air_properties_reproduce_the_materials_library_at_300_k():
    """The power laws are the library's own air card, extrapolated.

    If they were a second opinion about air, the gap conductivity and the
    library-driven coolant table would disagree at the one temperature both are
    quoted at — and every air number in the payload would depend on which code
    path produced it.
    """
    p = cm.air_properties(26.85)                 # 300.0 K
    assert p.k == pytest.approx(0.0263, rel=0.02)
    assert p.nu == pytest.approx(1.56e-5, rel=0.02)
    assert p.rho == pytest.approx(1.16, rel=0.03)


def test_hot_air_conducts_better_and_is_thinner():
    cold, hot = cm.air_properties(20.0), cm.air_properties(120.0)
    assert hot.k > cold.k
    assert hot.nu > cold.nu                      # kinematic viscosity rises
    assert hot.rho < cold.rho


# ---------------------------------------------------------------------------
# (a) + (b) the correlation ladder
# ---------------------------------------------------------------------------

def test_pipe_flow_has_a_laminar_floor_and_never_falls_below_it():
    """Nu = 3.66 is a FLOOR, not a correlation.

    The bore of a stationary machine with a trickle of coolant in it still
    conducts; a Dittus–Boelter extrapolated to Re = 50 would return 0.06 and
    report the rotor as uncooled.
    """
    for re in (0.0, 1.0, 50.0, 500.0, 2299.0):
        nu, regime = cm.pipe_nusselt(re, 7.0)
        assert regime == "laminar"
        assert nu == pytest.approx(3.66)
    for re in (2301.0, 5000.0, 9999.0, 1.0e4, 1.0e5, 1.0e6):
        nu, _ = cm.pipe_nusselt(re, 7.0)
        assert nu >= 3.66, re


def test_pipe_flow_nusselt_is_monotone_across_both_regime_seams():
    """A step at 2300 or at 10⁴ is a pump curve with a cliff in it.

    Both seams are checked because they are joined by DIFFERENT correlations
    (Gnielinski below 10⁴, Dittus–Boelter above) and nothing forces two
    independent fits to meet.
    """
    res = [2.0e3, 2.31e3, 2.6e3, 2.99e3, 3.1e3, 5.0e3, 9.5e3, 1.05e4, 2.0e4,
           1.0e5]
    nus = [cm.pipe_nusselt(r, 7.0)[0] for r in res]
    assert nus == sorted(nus), list(zip(res, nus))
    # …and neither seam JUMPS.  2300 is bridged by a blend (Gnielinski lands ~4x
    # above the laminar value there); 10⁴ needs no bridge — the two correlations
    # already agree to a fraction of a percent.
    for lo, hi in ((2299.0, 2301.0), (2999.0, 3001.0), (9999.0, 1.0001e4)):
        a = cm.pipe_nusselt(lo, 7.0)[0]
        b = cm.pipe_nusselt(hi, 7.0)[0]
        assert abs(b - a) <= 0.05 * max(a, b), (lo, hi, a, b)


def test_the_three_regimes_are_named_where_they_are_expected():
    assert cm.pipe_nusselt(1.0e3, 7.0)[1] == "laminar"
    assert cm.pipe_nusselt(5.0e3, 7.0)[1] == "transitional"
    assert cm.pipe_nusselt(5.0e4, 7.0)[1] == "turbulent"


def test_cross_flow_over_a_cylinder_rises_with_the_wind():
    """Churchill–Bernstein over the whole practical range, in one expression.

    The piecewise Hilpert tables it replaces have steps at the band edges, which
    a fan cannot produce.
    """
    nus = [cm.churchill_bernstein_nu(re, 0.707)
           for re in (1e2, 1e3, 1e4, 1e5, 1e6)]
    assert nus == sorted(nus)
    assert cm.churchill_bernstein_nu(0.0, 0.707) == pytest.approx(0.3)


def test_the_hydraulic_diameter_of_a_wide_slot_tends_to_twice_its_height():
    assert cm.hydraulic_diameter_slot(10.0, 0.005) == pytest.approx(0.01, rel=0.02)
    # …but a SMALL machine's jacket is not a wide slot: a 30 mm housing is 94 mm
    # round and 5 mm deep, and the 2·h approximation is 5 % out there.
    d = cm.hydraulic_diameter_slot(2.0 * math.pi * 0.015, 0.005)
    assert 0.0090 < d < 0.0100
    assert d < 0.01


# ---------------------------------------------------------------------------
# (c) the energy balance
# ---------------------------------------------------------------------------

def test_the_outlet_is_the_inlet_plus_the_heat_over_the_thermal_mass_flow():
    assert cm.outlet_temperature(40.0, 4186.0, 1.0, 4186.0) == pytest.approx(41.0)
    # no flow → no heating term, not an infinity
    assert cm.outlet_temperature(40.0, 5000.0, 0.0, 4186.0) == pytest.approx(40.0)


def test_a_liquid_jacket_carries_its_heat_and_says_where_it_went():
    """T_out > T_in, and the balance P = ṁ·cp·ΔT closes exactly.

    This is the model that replaced the inverted one: the flow is the INPUT and
    the outlet is the RESULT.
    """
    s = cm.outer_liquid(props=WATER, fluid="water", t_in_c=40.0, flow_lpm=6.0,
                        r_housing_m=0.100, length_m=0.10, heat_w=2000.0)
    assert s["mode"] == "liquid" and s["fluid"] == "water"
    assert s["t_in_c"] == pytest.approx(40.0)
    assert s["t_out_c"] > s["t_in_c"]
    assert s["t_sink_c"] == pytest.approx(0.5 * (s["t_in_c"] + s["t_out_c"]),
                                          abs=0.02)
    m_dot = s["m_dot_kg_s"]
    assert m_dot == pytest.approx(1000.0 * 6.0 / 60000.0, rel=1e-6)
    assert (s["t_out_c"] - s["t_in_c"]) == pytest.approx(
        2000.0 / (m_dot * WATER.cp), abs=0.02)
    assert s["h_conv"] > 0 and s["re"] > 0 and s["nu"] > 0


def test_more_jacket_flow_means_a_colder_outlet_and_never_a_worse_film():
    """Both halves of "turn the pump up": never less h, AND less coolant heating.

    Never LESS rather than always MORE, deliberately.  The jacket is modelled as
    one annular slot the full way round the housing, and on a 200 mm machine that
    cross-section is so large that even 20 L/min of water is still laminar —
    where h depends on the hydraulic diameter and not on the flow at all.  That
    is not a modelling failure, it is the reason real jackets are helical
    grooves, and both the velocity and Re are reported so a jacket drawing can be
    checked against it.  What must never happen is h going DOWN.
    """
    kw = dict(props=WATER, fluid="water", t_in_c=40.0, r_housing_m=0.100,
              length_m=0.10, heat_w=2000.0)
    flows = [0.5, 1.0, 5.0, 20.0, 100.0]
    hs = [cm.outer_liquid(flow_lpm=q, **kw)["h_conv"] for q in flows]
    outs = [cm.outer_liquid(flow_lpm=q, **kw)["t_out_c"] for q in flows]
    assert hs == sorted(hs), list(zip(flows, hs))
    assert outs == sorted(outs, reverse=True), list(zip(flows, outs))
    slow, fast = (cm.outer_liquid(flow_lpm=q, **kw) for q in (0.5, 100.0))
    assert fast["t_sink_c"] < slow["t_sink_c"]

    # …and on a small housing, where the same L/min is a real velocity, h rises
    # strictly: 30 mm housing, 2 → 60 L/min crosses laminar → turbulent.
    small = dict(props=WATER, fluid="water", t_in_c=40.0, r_housing_m=0.030,
                 length_m=0.05, heat_w=500.0)
    a = cm.outer_liquid(flow_lpm=2.0, **small)
    b = cm.outer_liquid(flow_lpm=60.0, **small)
    assert b["re"] > a["re"]
    assert b["h_conv"] > a["h_conv"]
    assert b["regime"] == "turbulent"


def test_a_windy_housing_beats_a_still_one_and_still_air_has_a_floor():
    kw = dict(t_ambient_c=30.0, d_housing_m=0.20, heat_w=500.0)
    still = cm.outer_air(air_speed_mps=0.0, **kw)
    breeze = cm.outer_air(air_speed_mps=5.0, **kw)
    gale = cm.outer_air(air_speed_mps=40.0, **kw)
    assert still["h_conv"] == pytest.approx(cm.NATURAL_CONVECTION_H, abs=0.01)
    assert still["regime"] == "natural"
    assert breeze["h_conv"] > still["h_conv"]
    assert gale["h_conv"] > breeze["h_conv"]
    assert gale["regime"] == "forced"
    # the outside air is an unbounded reservoir: the sink stays at ambient
    assert gale["t_sink_c"] == pytest.approx(30.0)


def test_bore_air_heats_up_along_the_bore_and_swirl_helps():
    """The rotor's own cooling stream is BOUNDED — unlike the housing's.

    So its sink is the mean of inlet and outlet, and a stationary rotor and a
    spinning one at the same axial speed are not the same h.
    """
    kw = dict(t_ambient_c=25.0, r_bore_m=0.010, heat_w=300.0)
    still = cm.bore_air(air_speed_mps=20.0, rpm=0.0, **kw)
    spun = cm.bore_air(air_speed_mps=20.0, rpm=20000.0, **kw)
    assert spun["h_conv"] > still["h_conv"]        # swirl raises Re
    assert still["t_out_c"] > still["t_in_c"]      # the air comes out hotter
    assert still["t_sink_c"] == pytest.approx(
        0.5 * (still["t_in_c"] + still["t_out_c"]), abs=0.02)
    faster = cm.bore_air(air_speed_mps=60.0, rpm=0.0, **kw)
    assert faster["h_conv"] > still["h_conv"]
    assert faster["t_out_c"] < still["t_out_c"]    # more mass, less heating


def test_a_spinning_but_unblown_bore_removes_no_heat_and_says_so():
    """Stirred is not renewed.

    Rotation raises h — the air in the bore really is churning — but with no
    axial flow there is no mass leaving the machine, so there is no heat leaving
    with it.  Reporting a cooled rotor here would be the most flattering possible
    lie about a design.
    """
    s = cm.bore_air(air_speed_mps=0.0, rpm=20000.0, t_ambient_c=25.0,
                    r_bore_m=0.010, heat_w=300.0)
    assert s["m_dot_kg_s"] == pytest.approx(0.0)
    assert s["t_out_c"] == pytest.approx(25.0)
    assert "not renewed" in s["note"]


def test_bore_liquid_is_the_same_pipe_flow_with_the_coolant_in_it():
    kw = dict(props=WATER, fluid="water", t_in_c=30.0, r_bore_m=0.008,
              heat_w=400.0)
    slow = cm.bore_liquid(flow_lpm=0.5, **kw)
    fast = cm.bore_liquid(flow_lpm=8.0, **kw)
    assert fast["h_conv"] > slow["h_conv"]
    assert fast["t_out_c"] < slow["t_out_c"]
    assert slow["t_out_c"] > slow["t_in_c"]
    assert (slow["t_out_c"] - slow["t_in_c"]) == pytest.approx(
        400.0 / (slow["m_dot_kg_s"] * WATER.cp), abs=0.02)


def test_every_surface_reports_the_same_documented_keys():
    """One shape per surface, so one panel component renders both.

    A surface that is switched off must be a REPORT, not a missing key: "the
    bore is not cooled" and "the bore key never got written" are different
    statements.
    """
    keys = set(cm._SURFACE_KEYS)
    for s in (cm.surface_off("the rotor bore"),
              cm.outer_manual(h_conv=50.0, t_ambient_c=25.0),
              cm.outer_air(air_speed_mps=3.0, t_ambient_c=25.0,
                           d_housing_m=0.2, heat_w=100.0),
              cm.outer_still(t_wall_c=90.0, t_ambient_c=25.0,
                             d_housing_m=0.085, heat_w=3.0),
              cm.bore_still(t_wall_c=90.0, t_ambient_c=25.0, r_bore_m=0.025,
                            heat_w=1.0),
              cm.outer_liquid(props=WATER, fluid="water", t_in_c=25.0,
                              flow_lpm=5.0, r_housing_m=0.1, length_m=0.1,
                              heat_w=100.0),
              cm.bore_air(air_speed_mps=10.0, rpm=1000.0, t_ambient_c=25.0,
                          r_bore_m=0.01, heat_w=100.0),
              cm.bore_liquid(props=WATER, fluid="water", t_in_c=25.0,
                             flow_lpm=2.0, r_bore_m=0.01, heat_w=100.0)):
        assert keys <= set(s), (s["mode"], sorted(keys - set(s)))
        assert isinstance(s["note"], str) and s["note"]


# ---------------------------------------------------------------------------
# (d) the air gap
# ---------------------------------------------------------------------------

def test_a_stationary_gap_is_still_air_conduction():
    g = cm.taylor_couette_gap(rpm=0.0, r_rotor_m=0.0994, r_bore_m=0.100,
                              t_gap_c=75.0)
    assert g["regime"] == "conduction"
    assert g["Nu"] == pytest.approx(1.0)
    assert g["k_eff"] == pytest.approx(g["k_air"])
    assert g["k_air"] == pytest.approx(0.0296, rel=0.05)     # air at 75 °C


def test_spinning_the_rotor_stirs_the_gap_and_raises_k_eff():
    kw = dict(r_rotor_m=0.0994, r_bore_m=0.100, t_gap_c=75.0)
    slow = cm.taylor_couette_gap(rpm=1000.0, **kw)
    fast = cm.taylor_couette_gap(rpm=23000.0, **kw)
    assert fast["k_eff"] > slow["k_eff"] >= slow["k_air"]
    assert fast["regime"] in ("transitional", "turbulent")
    assert fast["Ta"] > slow["Ta"]


def test_the_gap_is_the_mechanical_clearance_and_ta_goes_as_delta_cubed():
    """THE sleeve bug, in one assertion.

    ``air_gap`` in this project's geometry is measured to the rotor IRON, and a
    retaining sleeve eats most of it.  Reading δ off the iron rather than off the
    sleeve OD over-states the clearance — on the 200 mm machine 3.6 mm instead of
    0.6 mm — and Ta ∝ δ³, so the gap came back hundreds of times more
    conductive than it is.
    """
    iron = cm.taylor_couette_gap(rpm=23000.0, r_rotor_m=0.0964, r_bore_m=0.100,
                                 t_gap_c=75.0)          # δ = 3.6 mm (WRONG)
    sleeved = cm.taylor_couette_gap(rpm=23000.0, r_rotor_m=0.0994,
                                    r_bore_m=0.100, t_gap_c=75.0)  # δ = 0.6 mm
    assert iron["delta_mm"] == pytest.approx(3.6, abs=0.01)
    assert sleeved["delta_mm"] == pytest.approx(0.6, abs=0.01)
    assert iron["Ta"] / max(sleeved["Ta"], 1.0) == pytest.approx(6.0 ** 3,
                                                                 rel=0.05)
    assert iron["k_eff"] > sleeved["k_eff"]


# ---------------------------------------------------------------------------
# The sleeve's two conductivities
# ---------------------------------------------------------------------------

def test_the_sleeve_reads_both_conductivities_from_the_materials_library():
    """The library already carries them; code defaults must be the LAST resort.

    ``T800_UD_60`` (and the three high-modulus sleeves beside it) list
    ``thermal_conductivity`` = through-thickness and
    ``thermal_conductivity_axial`` = along the fibres.  A model that averaged
    them, or that quietly used its own numbers while the catalogue said
    something else, would make the sleeve card unfalsifiable.
    """
    from motor_ai_sim.routes.thermal import _sleeve_k

    kr, kf, src = _sleeve_k("T800_UD_60")
    assert src == "library"
    assert kr == pytest.approx(0.8)
    assert kf == pytest.approx(7.0)
    assert kr < kf


def test_an_uncatalogued_sleeve_falls_back_to_documented_defaults_and_says_so():
    """A catalogue gap must never silently become an ISOTROPIC sleeve.

    That is the failure mode worth guarding: an isotropic sleeve conducts
    radially at the fibre value and the rotor's blanket disappears.
    """
    from motor_ai_sim.routes.thermal import _sleeve_k

    for name in (None, "", "not_a_material"):
        kr, kf, src = _sleeve_k(name)
        assert src == "default"
        assert kr == pytest.approx(cm.SLEEVE_K_RADIAL_DEFAULT)
        assert kf == pytest.approx(cm.SLEEVE_K_FIBRE_DEFAULT)
        assert kr < kf


# ---------------------------------------------------------------------------
# (e) the shaft that sticks out of the housing
# ---------------------------------------------------------------------------
# User 2026-09-07: *"торцы и лобовые части — только для вала, всё остальное
# вращается внутри мотора"*.  The rotor's end faces and the end windings are in a
# CLOSED housing and have nowhere else to send their heat; the shaft comes out
# through the bearings and its exposed length does.  Two functions: the film on a
# cylinder spinning in still air, and the fin conductance that follows.

def test_a_stationary_shaft_still_loses_heat_by_natural_convection():
    """The floor, and the reason it exists.

    ``Nu ∝ Re_ω^(2/3)`` goes to zero at rest, which would report a stopped
    machine's shaft as perfectly insulated — and make every temperature above it
    a different answer for a machine that is merely parked.
    """
    s = cm.rotating_cylinder_h(rpm=0.0, d_m=0.020, t_air_c=25.0)
    assert s["h"] == pytest.approx(cm.NATURAL_CONVECTION_H)
    assert s["re_omega"] == pytest.approx(0.0)
    assert s["regime"] == "at rest"


def test_a_spinning_shaft_cools_better_the_faster_it_turns():
    """Monotone in rpm, and well past the floor at any real speed.

    Monotone NON-DECREASING rather than strictly rising, because below the
    crossover the floor is the answer and a floor is flat by construction — what
    must never happen is h going DOWN when the machine speeds up.
    """
    kw = dict(d_m=0.020, t_air_c=25.0)
    rpms = [0.0, 100.0, 1000.0, 5000.0, 23000.0, 60000.0]
    hs = [cm.rotating_cylinder_h(rpm=r, **kw)["h"] for r in rpms]
    assert hs == sorted(hs), list(zip(rpms, hs))
    assert hs[0] == pytest.approx(cm.NATURAL_CONVECTION_H)
    assert hs[-1] > 5.0 * hs[0]
    fast = cm.rotating_cylinder_h(rpm=23000.0, **kw)
    assert fast["regime"] == "rotating"
    assert fast["re_omega"] > cm.ROTATING_RE_OMEGA_MIN
    # Re_ω = ω·d²/(2ν) — spelled out, because getting the factor of two wrong
    # here is a 59 % error on h that nothing else in the payload would show.
    p = cm.air_properties(25.0)
    assert fast["re_omega"] == pytest.approx(
        (23000.0 * 2.0 * math.pi / 60.0) * 0.020 ** 2 / (2.0 * p.nu), rel=1e-9)
    assert fast["nu"] == pytest.approx(
        0.133 * fast["re_omega"] ** (2.0 / 3.0) * p.pr ** (1.0 / 3.0), rel=1e-9)


def test_the_rotating_film_meets_the_floor_without_a_cliff():
    """No step at the correlation's lower validity bound.

    A hard branch at Re_ω = 10⁴ would put one there — on a 20 mm shaft the
    correlation is already ~70 W/m²K at that Re_ω, i.e. ten times the floor — and
    a discontinuity in h(rpm) is a machine that gets better when you nudge it.
    """
    kw = dict(d_m=0.020, t_air_c=25.0)
    # Sampling-independent: a 1 % nudge in speed may not move h by more than
    # 2 %.  Below the crossover h is the flat floor (ratio 1), above it
    # h ∝ ω^(2/3) (ratio 1.0067), and a branch at Re_ω = 10⁴ would read ~10 here.
    rpm = 1.0
    while rpm < 1.0e5:
        a = cm.rotating_cylinder_h(rpm=rpm, **kw)["h"]
        b = cm.rotating_cylinder_h(rpm=rpm * 1.01, **kw)["h"]
        assert b >= a - 1e-12, rpm
        assert b <= a * 1.02, (rpm, a, b)
        rpm *= 1.01


def test_a_short_fin_is_its_wetted_area_and_a_long_one_is_not():
    """The two limits the fin model exists to sit between.

      * m·L → 0: G → h·P·L, i.e. the isothermal wetted-area answer;
      * m·L → ∞: G → √(h·P·k·A), and the length stops mattering entirely.

    Everything in between is what a real shaft does, and it is the reason a
    wetted-area model over-reads a 100 mm steel stub by ~40 %.
    """
    kw = dict(h=30.0, d_out_m=0.020, d_in_m=0.0, k_shaft=45.0)
    per = math.pi * 0.020
    area = math.pi * 0.020 ** 2 / 4.0
    root = math.sqrt(30.0 * per * 45.0 * area)

    short = cm.shaft_fin_conductance(length_m=0.0005, **kw)
    assert short["G_W_per_K"] == pytest.approx(30.0 * per * 0.0005, rel=0.02)
    assert short["efficiency"] == pytest.approx(1.0, abs=0.01)

    long = cm.shaft_fin_conductance(length_m=2.0, **kw)
    assert long["G_W_per_K"] == pytest.approx(root, rel=1e-6)
    assert long["efficiency"] < 0.05           # nearly all of it is doing nothing
    assert long["mL"] > 5.0


def test_fin_conductance_is_monotone_in_length_and_saturates():
    lengths = [0.0, 0.01, 0.05, 0.10, 0.20, 0.50, 1.0, 3.0]
    gs = [cm.shaft_fin_conductance(h=30.0, d_out_m=0.020, d_in_m=0.0,
                                   k_shaft=45.0, length_m=L)["G_W_per_K"]
          for L in lengths]
    assert gs == sorted(gs), list(zip(lengths, gs))
    assert gs[0] == 0.0                          # nothing sticking out, no path
    # Tripling a stub that is already long buys ~nothing — the property a wetted
    # area model does not have.
    assert gs[-1] == pytest.approx(gs[-2], rel=0.01)


def test_a_hollow_shaft_conducts_less_along_itself_but_is_no_less_wetted():
    """``d_in`` comes off the CROSS-SECTION, never off the perimeter.

    Less steel to conduct along = a worse fin.  The bore's own inner surface is
    not the room's air (it is either still or already the ``bore`` cooled
    surface), so counting it here would claim the same watts on two paths.
    """
    solid = cm.shaft_fin_conductance(h=30.0, d_out_m=0.020, d_in_m=0.0,
                                     k_shaft=45.0, length_m=0.10)
    tube = cm.shaft_fin_conductance(h=30.0, d_out_m=0.020, d_in_m=0.014,
                                    k_shaft=45.0, length_m=0.10)
    assert tube["G_W_per_K"] < solid["G_W_per_K"]
    assert tube["perimeter_m"] == pytest.approx(solid["perimeter_m"])
    assert tube["m_per_m"] > solid["m_per_m"]      # thinner wall, steeper decay


def test_the_shaft_ends_path_is_two_fins_and_says_what_it_assumed():
    """The assembled path: film × fin × sides, with the whole story reported."""
    kw = dict(rpm=23000.0, t_ambient_c=25.0, d_out_m=0.020, d_in_m=0.0,
              k_shaft=45.0, length_each_side_m=0.10)
    two = cm.shaft_ends_path(n_sides=2, **kw)
    one = cm.shaft_ends_path(n_sides=1, **kw)
    assert two["G_W_per_K"] == pytest.approx(2.0 * one["G_W_per_K"])
    assert two["G_W_per_K"] == pytest.approx(2.0 * two["per_side"]["G_W_per_K"])
    assert two["h"] > cm.NATURAL_CONVECTION_H
    assert two["re_omega"] > 0.0
    assert two["n_sides"] == 2 and two["t_sink_c"] == pytest.approx(25.0)
    assert "fin" in two["note"] and "mm of Ø" in two["note"]
    # A colder shaft material is a worse path — the conductance is √(h·P·k·A).
    poor = cm.shaft_ends_path(n_sides=2, **{**kw, "k_shaft": 15.0})
    assert poor["G_W_per_K"] < two["G_W_per_K"]


def test_no_exposed_length_is_a_statement_not_a_zero():
    """"Nothing sticks out" and "the stub removes nothing" are different claims.

    The whole point of the user's ruling is that the OTHER axial paths (the end
    faces, the end windings) are inside a closed housing and must NOT be
    modelled — so a machine with no exposed shaft has to say so rather than come
    back with an unexplained zero.
    """
    off = cm.shaft_ends_path(rpm=23000.0, t_ambient_c=25.0, d_out_m=0.020,
                             d_in_m=0.0, k_shaft=45.0, length_each_side_m=0.0)
    assert off["G_W_per_K"] == 0.0
    assert "this path is off" in off["note"]
    assert off["h"] > 0.0            # the film is still real, there is no area


# ---------------------------------------------------------------------------
# (f) the machine that is not cooled at all — a robot joint in a room
# ---------------------------------------------------------------------------
# User 2026-09-14: no fan, no jacket, no slipstream.  Until this day the nearest
# mode was `outer_air` at v = 0, i.e. the flat 7 W/m²·K floor — one number for
# every machine, every ΔT and every surface finish.  The pinned case throughout
# is the Ø85 × 13 mm joint (CIANO28 85 20SW1200 / L13) at ΔT 60 K over 40 °C air,
# because that is the machine the mode was written for and its two films come out
# COMPARABLE: h_conv ≈ 6, h_rad ≈ 8.3.

#: The L13's housing and its room — the numbers every assertion below is quoted
#: against.  π·0.085·0.013 = 3.47e-3 m² of cylinder, which at these coefficients
#: removes ~3 W of the machine's 63.7 W: the point of the whole exercise is that
#: the air is NOT the cooling system on this machine, the mount is.
L13_D_HOUSING_M = 0.085
L13_T_AMBIENT_C = 40.0
L13_T_WALL_C = 100.0


def test_the_housing_in_still_air_is_churchill_chu_plus_radiation():
    """The pinned Ø85 case, term by term.

    Ra, h_conv and h_rad are asserted SEPARATELY rather than through their sum,
    because the sum is the one number a wrong β, a wrong film temperature or a
    forgotten ε would still land near by accident.
    """
    s = cm.outer_still(t_wall_c=L13_T_WALL_C, t_ambient_c=L13_T_AMBIENT_C,
                       d_housing_m=L13_D_HOUSING_M, emissivity=0.9,
                       area_m2=math.pi * L13_D_HOUSING_M * 0.013, heat_w=3.0)
    assert s["mode"] == "still"
    assert s["ra"] == pytest.approx(1.9e6, rel=0.05)
    assert s["h_conv"] == pytest.approx(6.0, rel=0.15)
    assert s["h_rad"] == pytest.approx(8.3, rel=0.05)
    assert s["h_total"] == pytest.approx(s["h_conv"] + s["h_rad"], abs=0.01)
    # the sink is the room; nothing flows, so there is no outlet to iterate
    assert s["t_sink_c"] == pytest.approx(L13_T_AMBIENT_C)
    assert s["t_in_c"] == s["t_out_c"] == pytest.approx(L13_T_AMBIENT_C)
    # film properties at ½(T_wall + T_∞), and it is SAID which temperature
    assert s["t_film_c"] == pytest.approx(70.0)
    assert s["t_wall_c"] == pytest.approx(L13_T_WALL_C)
    # the watts are split in the ratio of the two films — exact, same area, same ΔT
    assert s["convection_W"] + s["radiation_W"] == pytest.approx(3.0, abs=0.02)
    assert s["radiation_W"] > s["convection_W"]      # more than half leaves as light


def test_radiation_is_an_identity_and_a_polished_housing_radiates_nothing():
    """h_rad·(T_w − T_∞) ≡ εσ(T_w⁴ − T_∞⁴), at any ΔT.

    The linearisation is what lets a Robin condition carry radiation at all (a
    conduction solve takes h·(T − T_sink), not T⁴), and it costs NOTHING in
    accuracy — it is algebra.  What must also hold is the far end: ε = 0 is a
    real design (bare polished aluminium, ε ≈ 0.05) and it has to come back as
    exactly zero, or the emissivity input cannot be falsified.
    """
    sig = cm.STEFAN_BOLTZMANN
    for tw, te in ((100.0, 40.0), (200.0, 25.0), (30.0, 25.0), (0.0, -20.0)):
        h = cm.radiation_h(tw, te, 1.0)
        exact = sig * ((tw + 273.15) ** 4 - (te + 273.15) ** 4) / (tw - te)
        assert h == pytest.approx(exact, rel=1e-12), (tw, te)
    assert cm.radiation_h(100.0, 40.0, 0.0) == 0.0
    # linear in ε, so "half the emissivity, half the radiation" is exactly true
    assert cm.radiation_h(100.0, 40.0, 0.45) == pytest.approx(
        0.5 * cm.radiation_h(100.0, 40.0, 0.9), rel=1e-12)
    # at ΔT = 0 it is the 4εσT³ tangent, not zero: a wall AT ambient still
    # exchanges radiation, it just exchanges it both ways
    assert cm.radiation_h(40.0, 40.0, 0.9) == pytest.approx(
        4.0 * 0.9 * sig * 313.15 ** 3, rel=1e-9)


def test_a_polished_housing_removes_exactly_the_convective_half():
    """ε = 0 must leave ``h_conv`` untouched — the separability the payload needs.

    The heat budget reports convection and radiation as two lines, and the panel
    lets the user set ε; if switching radiation off moved the convective number
    as well (a floor on the TOTAL would do exactly that), neither line would mean
    anything and "what does the finish buy me" could not be answered.
    """
    kw = dict(t_wall_c=L13_T_WALL_C, t_ambient_c=L13_T_AMBIENT_C,
              d_housing_m=L13_D_HOUSING_M)
    bright = cm.outer_still(emissivity=0.9, **kw)
    polished = cm.outer_still(emissivity=0.0, **kw)
    assert polished["h_conv"] == pytest.approx(bright["h_conv"], abs=1e-9)
    assert polished["h_rad"] == 0.0
    assert polished["h_total"] == pytest.approx(polished["h_conv"], abs=0.01)
    assert bright["h_total"] - polished["h_total"] == pytest.approx(
        bright["h_rad"], abs=0.01)


def test_still_air_is_not_one_number_it_rises_with_the_wall():
    """h(ΔT), which is the whole reason this mode has to be ITERATED.

    The flat ``NATURAL_CONVECTION_H`` the still case used to get is the same at
    ΔT 5 K and at ΔT 150 K.  The real pair roughly doubles across that range —
    convection as ΔT^(1/4), radiation as the tangent to T⁴ — so a machine solved
    with a guessed wall temperature is solved with the wrong boundary condition.
    """
    kw = dict(t_ambient_c=L13_T_AMBIENT_C, d_housing_m=L13_D_HOUSING_M,
              emissivity=0.9)
    walls = [40.0, 45.0, 60.0, 80.0, 100.0, 150.0, 200.0]
    tot = [cm.outer_still(t_wall_c=w, **kw)["h_total"] for w in walls]
    conv = [cm.outer_still(t_wall_c=w, **kw)["h_conv"] for w in walls]
    assert tot == sorted(tot), list(zip(walls, tot))
    assert conv == sorted(conv), list(zip(walls, conv))
    assert tot[-1] > 2.0 * tot[0]
    # the convective half goes as ΔT^(1/4): tripling ΔT buys 3^0.25 = 1.32×
    h20 = cm.outer_still(t_wall_c=L13_T_AMBIENT_C + 20.0, **kw)["h_conv"]
    h60 = cm.outer_still(t_wall_c=L13_T_AMBIENT_C + 60.0, **kw)["h_conv"]
    assert 1.15 < h60 / h20 < 1.5, (h20, h60)


def test_at_ambient_the_two_films_land_on_the_old_flat_constant():
    """The check that the new model and the constant it replaces mean the same
    housing — and the reason ``NATURAL_CONVECTION_H`` is NOT applied here.

    Every other film in this module is a FORCED correlation, which at v = 0
    collapses to zero and needs the floor to stop it claiming a perfectly
    insulated machine.  Churchill–Chu is the still-air correlation itself: at
    ΔT → 0 it tends to Nu = 0.36 (conduction into the surrounding air) and, with
    the 4εσT³ radiation tangent beside it, the pair lands just UNDER the flat
    7 W/m²·K — from below, which is the safe direction for a cooling claim.
    Bolting the floor on top would double-count the mechanism the constant stands
    for and would break the ε = 0 separability above.
    """
    s = cm.outer_still(t_wall_c=L13_T_AMBIENT_C, t_ambient_c=L13_T_AMBIENT_C,
                       d_housing_m=L13_D_HOUSING_M, emissivity=0.9)
    assert s["h_total"] == pytest.approx(cm.NATURAL_CONVECTION_H, rel=0.20)
    assert s["h_total"] < cm.NATURAL_CONVECTION_H          # under-read, not over
    assert s["h_conv"] > 0.0                               # never zero, ever
    # the quiescent limit of the correlation itself: Nu → 0.60² = 0.36
    assert cm.churchill_chu_nu(0.0, 0.707) == pytest.approx(0.36, rel=1e-9)


def test_churchill_chu_is_monotone_in_rayleigh():
    nus = [cm.churchill_chu_nu(ra, 0.707)
           for ra in (0.0, 1e2, 1e4, 1e6, 1.9e6, 1e9, 1e12)]
    assert nus == sorted(nus), nus
    # the pinned housing: Ra 1.9e6 → Nu ≈ 17.4 → h = Nu·k/D ≈ 6 W/m²·K
    assert cm.churchill_chu_nu(1.894e6, 0.707) == pytest.approx(17.4, rel=0.02)


def test_the_still_housing_removes_about_three_of_the_machines_sixty_four_watts():
    """THE number the robot-joint work exists because of.

    3.47e-3 m² of Ø85 × 13 mm cylinder at 14.3 W/m²·K over 60 K is ~3 W, against
    63.7 W of loss at the L13's rated point — under 5 %.  Still air is not this
    machine's cooling system; the MOUNT is, and a model that quietly reported the
    housing as adequate would be the most flattering possible lie about the
    design.
    """
    area = math.pi * L13_D_HOUSING_M * 0.013
    s = cm.outer_still(t_wall_c=L13_T_WALL_C, t_ambient_c=L13_T_AMBIENT_C,
                       d_housing_m=L13_D_HOUSING_M, emissivity=0.9,
                       area_m2=area)
    watts = s["h_total"] * area * (L13_T_WALL_C - L13_T_AMBIENT_C)
    assert watts == pytest.approx(3.0, abs=0.5)
    assert watts < 0.05 * 63.7


def test_an_unventilated_bore_cannot_exchange_less_than_conduction_across_it():
    """Nu = 1 is the bore's floor — and it is a DIFFERENT floor from the housing's.

    A cavity's lower limit is conduction straight across it.  Churchill–Chu's own
    quiescent limit (0.36) is the unbounded-medium answer and sits BELOW that,
    i.e. it would report a hole in a hot rotor exchanging less than the still air
    inside it conducts.
    """
    cold = cm.bore_still(t_wall_c=40.0, t_ambient_c=40.0, r_bore_m=0.025)
    assert cold["nu"] == pytest.approx(1.0)
    assert cold["regime"] == "conduction across the bore"
    p = cm.air_properties(cold["t_film_c"])
    assert cold["h_conv"] == pytest.approx(p.k / 0.050, abs=0.01)
    # …and with a real ΔT the buoyant loop turns over and beats conduction
    hot = cm.bore_still(t_wall_c=100.0, t_ambient_c=40.0, r_bore_m=0.025)
    assert hot["nu"] > 1.0 and hot["h_conv"] > cold["h_conv"]
    assert hot["regime"] == "still air in the bore"
    # nothing is metered through it: no mass flow, so no outlet to iterate
    assert hot["m_dot_kg_s"] == 0.0
    assert hot["t_out_c"] == pytest.approx(40.0)
    # the bore radiates out of its ENDS; ε = 0 (a sealed or cable-filled bore)
    # removes that term exactly
    sealed = cm.bore_still(t_wall_c=100.0, t_ambient_c=40.0, r_bore_m=0.025,
                           emissivity=0.0)
    assert sealed["h_rad"] == 0.0
    assert sealed["h_total"] == pytest.approx(hot["h_conv"], abs=0.01)


# ---------------------------------------------------------------------------
# (g) the mount — the path this machine's heat actually takes
# ---------------------------------------------------------------------------

def test_the_mount_is_an_input_and_its_absence_is_a_statement():
    """A conductance nobody typed is not a machine bolted to a perfect heatsink,
    and it is not one bolted to nothing either — the model has to SAY which."""
    on = cm.mount_path(g_w_per_k=2.0, t_mount_c=40.0)
    assert on["mode"] == "conduction"
    assert on["G_W_per_K"] == pytest.approx(2.0)
    assert on["t_sink_c"] == pytest.approx(40.0)
    assert "INPUT" in on["note"] and "infinite sink" in on["note"]

    for bad in (0.0, -1.0, float("nan")):
        off = cm.mount_path(g_w_per_k=bad, t_mount_c=40.0)
        assert off["mode"] == "off"
        assert off["G_W_per_K"] == 0.0
        assert "bolted to NOTHING" in off["note"]


# ---------------------------------------------------------------------------
# (g-2) the robot link — the arm itself heats up (2026-09-26)
# ---------------------------------------------------------------------------

def test_a_link_carrying_no_heat_sits_at_ambient_and_never_binds():
    r = cm.robot_link_path(q_w=0.0, t_ambient_c=40.0, preset="wrist",
                           material="aluminium")
    assert r["t_link_c"] == pytest.approx(40.0)
    assert r["G_W_per_K"] == 0.0
    assert r["binds_touch_limit"] is False


def test_a_small_link_under_the_mount_heat_of_a_finger_motor_blows_past_touch():
    # The owner's Ø12 case: ~200 W into the mount, a finger-sized link.  The
    # whole point of this feature — a link this small cannot shed that much.
    r = cm.robot_link_path(q_w=200.0, t_ambient_c=40.0, preset="finger",
                           material="aluminium")
    assert r["t_link_c"] > cm.LINK_TOUCH_LIMIT_C
    assert r["binds_touch_limit"] is True
    assert r["G_W_per_K"] > 0.0


def test_a_bigger_link_of_the_same_material_runs_cooler():
    small = cm.robot_link_path(q_w=20.0, t_ambient_c=40.0, preset="finger",
                               material="aluminium")
    big = cm.robot_link_path(q_w=20.0, t_ambient_c=40.0, preset="arm",
                             material="aluminium")
    assert big["t_link_c"] < small["t_link_c"]
    assert big["area_m2"] > small["area_m2"]
    assert big["mass_kg"] > small["mass_kg"]


def test_a_steel_link_has_more_capacity_than_aluminium_of_the_same_size():
    al = cm.robot_link_path(q_w=10.0, t_ambient_c=40.0, preset="wrist",
                            material="aluminium")
    steel = cm.robot_link_path(q_w=10.0, t_ambient_c=40.0, preset="wrist",
                               material="steel")
    assert steel["mass_kg"] > al["mass_kg"]
    assert steel["C_J_per_K"] > al["C_J_per_K"]
    # Same shape, same film — the steady temperature should match closely.
    assert steel["t_link_c"] == pytest.approx(al["t_link_c"], abs=0.5)


def test_an_unknown_preset_or_material_falls_back_rather_than_raising():
    r = cm.robot_link_path(q_w=10.0, t_ambient_c=40.0, preset="huge",
                           material="unobtainium")
    assert r["preset"] == "wrist"
    assert r["material"] == "aluminium"


# ---------------------------------------------------------------------------
# (h) the axial faces — the end turns and the core ends of an open joint
# ---------------------------------------------------------------------------
# User 2026-09-14: the 24 coils stand PROUD of the core on both sides and the
# core's end faces are largely uncovered.  That is the 2026-09-07 ruling (*"торцы
# и лобовые части — только для вала"*) not holding for this machine, so these
# faces need the same natural-convection + radiation pair the housing gets — as
# lumped conductances, because a 2-D cross-section has no facets out along the
# axis.

def test_a_vertical_end_face_in_still_air_is_a_conductance_not_a_film():
    """G = h_total·A·n, and the watts that conductance carries at the stated wall.

    The shape ``volume_sinks`` needs (the same shape ``shaft_ends_path`` returns),
    because the face is out along the axis and the mesh has no facet there.
    """
    f = cm.end_face_still(t_wall_c=100.0, t_ambient_c=40.0, area_m2=0.0020,
                          char_len_m=0.060, emissivity=0.9, n_faces=2,
                          name="core end face")
    assert f["mode"] == "still" and f["regime"] == "vertical plate"
    # a 60 mm vertical face at ΔT 60 K: Churchill–Chu's plate form
    assert 5.5 < f["h_conv"] < 8.5, f["h_conv"]
    assert f["h_rad"] == pytest.approx(
        cm.radiation_h(100.0, 40.0, 0.9), rel=1e-12)
    assert f["h_total"] == pytest.approx(f["h_conv"] + f["h_rad"], rel=1e-12)
    assert f["G_W_per_K"] == pytest.approx(f["h_total"] * 0.0020 * 2, rel=1e-12)
    assert f["heat_removed_W"] == pytest.approx(f["G_W_per_K"] * 60.0, rel=1e-12)
    assert f["convection_W"] + f["radiation_W"] == pytest.approx(
        f["heat_removed_W"], rel=1e-9)
    # …and a face that is covered is a REPORT, not a zero nobody can read
    off = cm.end_face_still(t_wall_c=100.0, t_ambient_c=40.0, area_m2=0.0,
                            char_len_m=0.060)
    assert off["mode"] == "off" and off["G_W_per_K"] == 0.0
    assert "this path is off" in off["note"]


def test_which_way_the_face_looks_is_worth_a_factor_of_two():
    """Hot face UP lets the plume leave; hot face DOWN traps it against the face.

    0.54 vs 0.27 Ra^(1/4) — exactly half — which is why the orientation is an
    input and a typo in it is refused rather than defaulted.
    """
    kw = dict(t_wall_c=100.0, t_ambient_c=40.0, area_m2=0.0020,
              char_len_m=0.060)
    up = cm.end_face_still(orientation="horizontal_up", **kw)
    down = cm.end_face_still(orientation="horizontal_down", **kw)
    assert up["h_conv"] == pytest.approx(2.0 * down["h_conv"], rel=0.02)
    assert "hot face up" in up["regime"] and "hot face down" in down["regime"]
    with pytest.raises(ValueError):
        cm.end_face_still(orientation="sideways", **kw)
    # the McAdams power laws are FITS, not limits: at ΔT → 0 they go to zero and
    # would report an adiabatic face, so every branch keeps the vertical form's
    # own quiescent value
    for o in ("vertical", "horizontal_up", "horizontal_down"):
        assert cm.flat_plate_nu(0.0, 0.707, o)[0] == pytest.approx(0.825 ** 2)


def test_the_end_turns_have_one_geometry_and_two_films():
    """``end_winding_area`` is the OPEN frame's derivation, exposed.

    The propeller motor blows air over the end turns and the robot joint does
    not, but they are the same copper standing out of the same core — so the area
    is computed once.  Two derivations would drift, and the one that drifted
    would be the one nobody was looking at.
    """
    kw = dict(n_coils=24, bar_thickness_m=0.0066, bar_width_m=0.0035,
              end_turn_length_m=0.00668)
    geom = cm.end_winding_area(**kw)
    # P_exposed = 2t + w — the tooth-facing face is not in the room either
    assert geom["perimeter_m"] == pytest.approx(2 * 0.0066 + 0.0035)
    assert geom["area_m2"] == pytest.approx(24 * 2 * geom["perimeter_m"] * 0.00668)
    assert geom["area_per_side_m2"] == pytest.approx(0.5 * geom["area_m2"])
    # the forced path measures the same copper
    blown = cm.end_windings_path(air_speed_mps=12.0, t_ambient_c=25.0, **kw)
    assert blown["area_m2"] == pytest.approx(geom["area_m2"], rel=1e-12)
    assert blown["d_equiv_m"] == pytest.approx(geom["d_equiv_m"], rel=1e-12)
    # …and the still-air joint puts its own film on it
    still = cm.end_face_still(t_wall_c=120.0, t_ambient_c=40.0,
                              area_m2=geom["area_per_side_m2"], n_faces=2,
                              char_len_m=0.0066, name="end windings")
    assert still["area_total_m2"] == pytest.approx(geom["area_m2"], rel=1e-12)
    assert still["G_W_per_K"] < blown["G_W_per_K"]     # no wash, less cooling


def test_the_gap_states_the_temperature_its_air_was_evaluated_at():
    cold = cm.taylor_couette_gap(rpm=5000.0, r_rotor_m=0.0994, r_bore_m=0.100,
                                 t_gap_c=25.0)
    hot = cm.taylor_couette_gap(rpm=5000.0, r_rotor_m=0.0994, r_bore_m=0.100,
                                t_gap_c=150.0)
    assert cold["T_gap_c"] == pytest.approx(25.0)
    assert hot["T_gap_c"] == pytest.approx(150.0)
    assert hot["k_air"] > cold["k_air"]           # k rises ~13 %/50 K
