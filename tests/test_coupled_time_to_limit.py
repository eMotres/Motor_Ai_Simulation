"""HOW LONG UNTIL THE LIMIT — the coupled loop's new answer, pinned three ways.

``coupled_time_to_limit`` is pure, so this suite solves nothing electromagnetic
and nothing two-dimensional.  It pins the feature where it can be wrong:

  (a) ANALYTIC — on a machine with ONE thermal node the step response is
      ``T(t) = T∞ + ΔT∞(1 − e^{−t/τ})`` and the time to a limit is therefore
      ``t = τ·ln(ΔT∞ / (ΔT∞ − ΔT_lim))``.  An integrator that misses that closed
      form has nothing else worth checking;
  (b) THE RULES — a point inside every limit reports NOTHING (never a time to a
      limit it respects); a step response whose asymptote sits below the limit
      says so instead of inventing a number; the warm start is always the
      shorter of the two and is omitted, with a reason, when there is no rated
      duty to take it from;
  (c) THE RECORD — the block's shape, and that it survives ``compact_coupled``
      into the duty's stored answer, because a number the report cannot read is
      a number that does not exist.

The L13 (Ø85 / 13 mm robot joint) fixtures come from
``tests.test_thermal_duty_cycle`` — the same maps the duty-cycle suite is built
on, so the two features cannot disagree about the machine.
"""
from __future__ import annotations

import math

import pytest

import motor_ai_sim.coupled_time_to_limit as ttl
import motor_ai_sim.thermal_duty_cycle as dc
from motor_ai_sim.thermal_capacities import NODES, part_capacities
from tests.test_thermal_capacities import (L13_MATERIALS, L13_PARTS,
                                           L13_SUMMARY)
from tests.test_thermal_duty_cycle import (D_HOUSING_M, L13_GEO, PEAK_MAP,
                                           PEAK_SUMMARY, RATED_MAP,
                                           RATED_SUMMARY, STACK_M)


@pytest.fixture(scope="module")
def caps():
    return part_capacities(L13_SUMMARY, L13_MATERIALS, L13_PARTS)


@pytest.fixture(scope="module")
def areas():
    return dc.end_face_areas(RATED_SUMMARY, stack_m=STACK_M, geometry=L13_GEO)


# ---------------------------------------------------------------------------
# (a) analytic — the closed form, to the last digit the ODE claims
# ---------------------------------------------------------------------------

def _one_node_network(g_w_per_k: float, t_amb: float = 25.0) -> dc.Network:
    """Every node merged into one lump with a single conductance to a sink.

    The ONLY network with a closed-form step response, which is why the analytic
    check is made on it and not on a prettier one.
    """
    return dc.Network(
        G={"w_s": 0.0, "r_s": 0.0, "m_r": 0.0, "s_mount": float(g_w_per_k),
           "r_bore": 0.0, "r_shaft": 0.0},
        areas={}, t_ambient_c=t_amb, t_mount_c=t_amb,
        node_of={n: "stator" for n in NODES},
        merged=(("winding", "stator"), ("rotor", "stator"),
                ("magnet", "stator")))


def _const_segment(p_w: float) -> dc.Segment:
    return dc.Segment("point", 1.0,
                      {"stator": float(p_w), "winding": 0.0, "rotor": 0.0,
                       "magnet": 0.0}, rpm=1000.0, copper_feedback=False)


def test_the_time_to_a_limit_is_tau_ln_of_the_rise():
    """t = τ·ln(ΔT∞ / (ΔT∞ − ΔT_lim)), to a millisecond in 140 s."""
    C, G, P, t_amb, t_lim = 200.0, 2.0, 100.0, 25.0, 60.0
    net = _one_node_network(G, t_amb)
    caps = {n: C / 4.0 for n in NODES}
    tau = C / G                                  # 100 s
    d_inf = P / G                                # 50 K
    d_lim = t_lim - t_amb                        # 35 K
    exact = tau * math.log(d_inf / (d_inf - d_lim))

    rec = dc.time_to_limits(_const_segment(P), net, caps,
                            [("winding", "winding", t_lim, 0.0)],
                            t_max_s=10.0 * tau)
    got = rec["targets"]["winding"]
    assert got["reaches"] is True
    assert got["time_s"] == pytest.approx(exact, abs=1e-3), (got, exact)
    # …and it is a real rise, not a coincidence of the horizon.
    assert 100.0 < exact < 150.0


def test_an_offset_moves_the_crossing_by_exactly_the_offset():
    """The hot spot is the node plus a CONSTANT, so it crosses earlier — and by
    the amount the same closed form says for the lowered limit."""
    C, G, P, t_amb = 200.0, 2.0, 100.0, 25.0
    net = _one_node_network(G, t_amb)
    caps = {n: C / 4.0 for n in NODES}
    rec = dc.time_to_limits(_const_segment(P), net, caps,
                            [("plain", "winding", 60.0, 0.0),
                             ("hot", "winding", 60.0, 5.0)], t_max_s=1000.0)
    tau, d_inf = C / G, P / G
    assert rec["targets"]["hot"]["time_s"] == pytest.approx(
        tau * math.log(d_inf / (d_inf - (60.0 - 5.0 - t_amb))), abs=1e-3)
    assert rec["targets"]["hot"]["time_s"] < rec["targets"]["plain"]["time_s"]


def test_a_limit_above_the_asymptote_is_never_reached_and_says_the_asymptote():
    """The whole point of the honest branch: ΔT∞ = 50 K, so 100 °C above a
    25 °C ambient is not a temperature this point ever has."""
    C, G, P, t_amb = 200.0, 2.0, 100.0, 25.0
    net = _one_node_network(G, t_amb)
    caps = {n: C / 4.0 for n in NODES}
    rec = dc.time_to_limits(_const_segment(P), net, caps,
                            [("winding", "winding", 100.0, 0.0)],
                            t_max_s=40.0 * (C / G))
    got = rec["targets"]["winding"]
    assert got["reaches"] is False
    assert got["time_s"] is None
    assert rec["settled"] is True
    assert got["asymptote_c"] == pytest.approx(t_amb + P / G, abs=0.05)


def test_a_warmer_start_reaches_the_same_limit_sooner():
    """The rated pull is always the shorter one — and by the closed form."""
    C, G, P, t_amb, t_lim = 200.0, 2.0, 100.0, 25.0, 60.0
    net = _one_node_network(G, t_amb)
    caps = {n: C / 4.0 for n in NODES}
    seg = _const_segment(P)
    cold = dc.time_to_limits(seg, net, caps, [("w", "winding", t_lim, 0.0)],
                             T0={n: t_amb for n in NODES}, t_max_s=1000.0)
    warm = dc.time_to_limits(seg, net, caps, [("w", "winding", t_lim, 0.0)],
                             T0={n: 45.0 for n in NODES}, t_max_s=1000.0)
    tau, d_inf = C / G, P / G
    assert warm["targets"]["w"]["time_s"] == pytest.approx(
        tau * math.log((d_inf - (45.0 - t_amb)) / (d_inf - (t_lim - t_amb))),
        abs=1e-3)
    assert warm["targets"]["w"]["time_s"] < cold["targets"]["w"]["time_s"]


# ---------------------------------------------------------------------------
# (b) the rules
# ---------------------------------------------------------------------------

def _limits(*specs):
    return [ttl.PartLimit(p, n, lim, off, at, "test") for p, n, lim, off, at
            in specs]


def test_a_point_inside_every_limit_reports_no_time_at_all(caps, areas):
    """A machine that is not over anything has no time to a limit, and must not
    be handed one: a reader would plan around a number that is not a
    constraint."""
    out = ttl.solve(thermal_result=RATED_MAP, em_summary=RATED_SUMMARY,
                    limits=_limits(("winding", "winding", 200.0, 2.4, 106.1)),
                    caps=caps, geometry=L13_GEO, side_areas=areas,
                    d_housing_m=D_HOUSING_M,
                    cooling={"mount_g_w_per_k": 2.0, "mount_temp_c": 40.0},
                    duty="rated")
    assert out["within_limits"] is True
    assert out["time_to_limit_s"] is None
    assert out["limiting_part"] is None
    assert "starts" not in out
    assert ttl.headline(out) == ""
    assert ttl.panel_line(out) == ""


def test_half_a_kelvin_over_is_not_over():
    """The rounding of the number that produced it, not an answer."""
    p = ttl.PartLimit("winding", "winding", 200.0, 0.0, 200.3, "")
    assert p.over is False
    assert ttl.PartLimit("winding", "winding", 200.0, 0.0, 201.0, "").over


def test_the_peak_point_reports_a_time_from_cold_and_from_rated(caps, areas):
    """The L13 peak: 686 W in a Ø85 × 13 mm joint is a pulse, and the winding is
    what ends it.  The S2 study of 2026-09-15 put that pull at ~24 s from cold
    with the same network, so the answer has to be of that order — and the pull
    from the rated machine has to be shorter still."""
    out = ttl.solve(
        thermal_result=PEAK_MAP, em_summary=PEAK_SUMMARY,
        limits=_limits(("winding", "winding", 200.0, 27.5, 777.0),
                       ("magnet", "magnet", 180.0, 1.2, 630.7)),
        caps=caps, geometry=L13_GEO, side_areas=areas,
        d_housing_m=D_HOUSING_M,
        cooling={"mount_g_w_per_k": 2.0, "mount_temp_c": 40.0},
        duty="peak",
        rated_state_c={"winding": 103.7, "stator": 94.5, "rotor": 95.7,
                       "magnet": 95.6},
        rated_source="the rated duty's own stored thermal map")

    assert out["within_limits"] is False
    assert out["limiting_part"] == "winding"
    t_cold = out["time_to_limit_s"]
    t_rated = out["time_to_limit_from_rated_s"]
    assert 5.0 < t_cold < 120.0, t_cold
    assert 0.0 < t_rated < t_cold, (t_rated, t_cold)
    # The magnets are over their limit too and are reported beside the winding,
    # but they are not what the headline is about.
    parts = {r["part"]: r for r in out["parts"]}
    assert set(parts) == {"winding", "magnet"}
    assert parts["magnet"]["time_to_limit_s"] > t_cold
    assert parts["winding"]["quantity"] == "the winding hot spot"
    assert parts["winding"]["over_by_K"] == pytest.approx(577.0, abs=0.01)
    # …and the sentence the loop logs and the panel prints.
    assert "winding" in ttl.headline(out) and "from cold" in ttl.headline(out)
    assert ttl.panel_line(out).startswith("Runs ")
    assert "then winding reaches 200 °C" in ttl.panel_line(out)


def test_an_asymptote_below_the_limit_is_said_out_loud_not_extrapolated(caps,
                                                                        areas):
    """A map over the limit whose step response settles UNDER it: the coupling
    the loop closed on is not in these four nodes, and no time may be quoted."""
    out = ttl.solve(
        thermal_result=RATED_MAP, em_summary=RATED_SUMMARY,
        # The map says 106 °C; this limit claims the part is at 260 °C, which is
        # a state this network never reaches at the rated point's 62 W.
        limits=_limits(("winding", "winding", 200.0, 2.4, 260.0)),
        caps=caps, geometry=L13_GEO, side_areas=areas,
        d_housing_m=D_HOUSING_M,
        cooling={"mount_g_w_per_k": 2.0, "mount_temp_c": 40.0}, duty="rated")
    assert out["within_limits"] is False
    assert out["time_to_limit_s"] is None
    row = out["parts"][0]
    assert row["reaches"] is False
    assert row["asymptote_c"] is not None and row["asymptote_c"] < 200.0
    assert "never reaches" in row["note"] and "NO time is quoted" in row["note"]
    assert "never reaches" in ttl.headline(out)
    assert ttl.panel_line(out).startswith("No time to the winding limit")


def test_no_rated_duty_omits_the_warm_start_and_says_why(caps, areas):
    out = ttl.solve(
        thermal_result=PEAK_MAP, em_summary=PEAK_SUMMARY,
        limits=_limits(("winding", "winding", 200.0, 27.5, 777.0)),
        caps=caps, geometry=L13_GEO, side_areas=areas,
        d_housing_m=D_HOUSING_M,
        cooling={"mount_g_w_per_k": 2.0, "mount_temp_c": 40.0}, duty="peak",
        rated_source="this configuration has no rated duty with a coupled "
                     "record")
    assert "rated" not in out["starts"]
    assert out["time_to_limit_from_rated_s"] is None
    assert "no rated duty" in out["rated_start_note"]
    assert "from rated" not in ttl.headline(out)


def test_the_fit_quality_is_reported_beside_the_time(caps, areas):
    """A time is worth what the network behind it is worth, so the block carries
    the network's own residual at the map it was fitted to."""
    out = ttl.solve(
        thermal_result=PEAK_MAP, em_summary=PEAK_SUMMARY,
        limits=_limits(("winding", "winding", 200.0, 27.5, 777.0)),
        caps=caps, geometry=L13_GEO, side_areas=areas,
        d_housing_m=D_HOUSING_M,
        cooling={"mount_g_w_per_k": 2.0, "mount_temp_c": 40.0}, duty="peak")
    net = out["network"]
    assert net["available"] is True
    assert net["link_kinds"]["w_s"] == "calibrated"
    assert net["worst_residual_W"] >= 0.0
    assert "out of balance" in net["note"]


# ---------------------------------------------------------------------------
# the limits, off the machine's own cards
# ---------------------------------------------------------------------------

def test_the_winding_limit_is_the_projects_class_never_180():
    lim, why = ttl.winding_limit()
    assert lim == 200.0
    assert "200" in why and "ASSUMED" in why


def test_a_magnet_with_no_readable_grade_is_simply_not_judged():
    lim, why = ttl.magnet_card_limit("")
    assert lim is None and "not judged" in why


def test_part_limits_read_the_offsets_off_the_map():
    """Each part is judged on the quantity the coupled record reports: the
    winding MAX, the magnet MAX and the bearing SEAT — carried as a constant
    offset from the node the network integrates."""
    got = {p.part: p for p in ttl.part_limits(
        thermal_result=PEAK_MAP, em_summary=PEAK_SUMMARY,
        magnet_limit_c=180.0, bearing_limit_c=150.0, bearing_temp_c=640.0)}
    assert got["winding"].offset_k == pytest.approx(777.0 - 749.5, abs=1e-6)
    assert got["winding"].at_point_c == pytest.approx(777.0, abs=1e-6)
    assert got["magnet"].at_point_c == pytest.approx(630.7, abs=1e-6)
    assert got["bearing"].node == "rotor"
    assert got["bearing"].at_point_c == pytest.approx(640.0, abs=1e-6)
    assert got["bearing"].offset_k == pytest.approx(640.0 - 629.9, abs=1e-6)
    # A machine with no seat temperature grows no bearing row at all.
    assert "bearing" not in {p.part for p in ttl.part_limits(
        thermal_result=PEAK_MAP, bearing_limit_c=150.0)}


def test_the_robot_link_rides_the_stator_node_only_when_the_map_has_one():
    """mount_mode='link' (2026-09-26) leaves cooling.mount.link on the map;
    part_limits reads it as a fixed 70 degC touch limit offset from stator,
    the same way the bearing seat rides the rotor."""
    tr = dict(PEAK_MAP)
    tr["cooling"] = {"mount": {"link": {"t_link_c": 95.0,
                                        "touch_limit_c": 70.0}}}
    got = {p.part: p for p in ttl.part_limits(thermal_result=tr)}
    assert "link" in got
    assert got["link"].node == "stator"
    assert got["link"].limit_c == pytest.approx(70.0)
    assert got["link"].at_point_c == pytest.approx(95.0)
    assert got["link"].over is True
    assert "touch 70" in ttl.part_label("link")

    # A machine with no link block (mount_mode='sink', the default) grows no
    # link row at all — bit-identical to before this feature existed.
    assert "link" not in {p.part for p in ttl.part_limits(
        thermal_result=PEAK_MAP)}


def test_the_duration_words_are_the_ones_the_panel_prints():
    assert ttl.fmt_seconds(0.83) == "0.8 s"
    assert ttl.fmt_seconds(48.2) == "48 s"
    assert ttl.fmt_seconds(160.2) == "2 m 40 s"
    assert ttl.fmt_seconds(125.0) == "2 m 05 s"
    assert ttl.fmt_seconds(None) == "—"


# ---------------------------------------------------------------------------
# (c) the record, and its persistence
# ---------------------------------------------------------------------------

BLOCK = {
    "within_limits": False, "time_to_limit_s": 160.2,
    "time_to_limit_words": "2 m 40 s", "limiting_part": "winding",
    "time_to_limit_from_rated_s": 65.0,
    "limits_c": {"winding": 200.0}, "at_point_c": {"winding": 212.4},
    "over_by_K": {"winding": 12.4}, "over_parts": ["winding"],
    "judged": ["winding"],
    "parts": [{"part": "winding", "limit_c": 200.0, "reaches": True,
               "time_to_limit_s": 160.2}],
    "starts": {"cold": {"time_to_limit_s": 160.2, "limiting_part": "winding"},
               "rated": {"time_to_limit_s": 65.0, "limiting_part": "winding"}},
    "network": {"available": True}, "model": "…", "note": "…",
}


def test_the_headline_sentence_names_the_part_the_limit_and_both_starts():
    words = ttl.headline(BLOCK)
    assert words == ("over the winding limit by 12 K — reaches 200 °C after "
                     "2 m 40 s from cold, 1 m 05 s from rated")


def test_the_block_survives_compact_coupled_into_the_duty_record():
    """The report reads the DUTY's stored record, not the run — a time that does
    not make that crossing reaches no page."""
    from motor_ai_sim.duty_results import compact_coupled
    rec = compact_coupled({"coupling": {"coil_temp_c": 212.4,
                                        "time_to_limit": dict(BLOCK)},
                           "computed_at": "2026-09-17T00:00:00"})
    assert rec["time_to_limit"]["time_to_limit_s"] == 160.2
    assert rec["time_to_limit"]["limiting_part"] == "winding"
    assert rec["time_to_limit"]["starts"]["rated"]["time_to_limit_s"] == 65.0
    # …and a run with no block grows no key, rather than a null one.
    assert "time_to_limit" not in compact_coupled({"coupling": {}})


# ---------------------------------------------------------------------------
# (d) THE MACHINE AT THE CROSSING — the "limited" mode's raw material
# ---------------------------------------------------------------------------
# Owner, 2026-09-18: *«состояние мотора в работе 24 секунды при заданной
# мощности»*.  The time alone was the 2026-09-17 answer; what the loop now
# reports is the STATE at that instant, so the step response has to hand back
# every node at the crossing and not just when it happened.

def test_the_step_response_hands_back_every_node_at_the_crossing():
    """One lump, so the closed form fixes the answer: at the crossing the state
    IS the limit, to the integrator's own tolerance."""
    C, G, P, t_amb, t_lim = 200.0, 2.0, 100.0, 25.0, 60.0
    net = _one_node_network(G, t_amb)
    caps = {n: C / 4.0 for n in NODES}
    rec = dc.time_to_limits(_const_segment(P), net, caps,
                            [("winding", "winding", t_lim, 0.0)],
                            t_max_s=1000.0)
    got = rec["targets"]["winding"]
    assert set(got["state_c"]) == set(NODES)
    # Everything is one merged lump here, so every node reads the limit.
    for n in NODES:
        assert got["state_c"][n] == pytest.approx(t_lim, abs=0.05), n
    # …and a target that is NEVER reached has no state: there is no instant.
    none = dc.time_to_limits(_const_segment(P), net, caps,
                             [("w", "winding", 100.0, 0.0)], t_max_s=1000.0)
    assert "state_c" not in none["targets"]["w"]


def test_an_offset_puts_the_NODE_below_the_limit_by_exactly_the_offset():
    """The winding HOT SPOT is what is judged; the node the electromagnetic run
    is solved at is that limit MINUS the map's own offset, and confusing the two
    would run the final pass 27 K too hot."""
    C, G, P, t_amb = 200.0, 2.0, 100.0, 25.0
    net = _one_node_network(G, t_amb)
    caps = {n: C / 4.0 for n in NODES}
    rec = dc.time_to_limits(_const_segment(P), net, caps,
                            [("hot", "winding", 60.0, 5.0)], t_max_s=1000.0)
    assert rec["targets"]["hot"]["state_c"]["winding"] == pytest.approx(
        55.0, abs=0.05)


def test_limiting_is_the_first_crossing_with_the_whole_machine(caps, areas):
    """The L13 peak: the winding ends the pull, and what `limiting` hands the
    loop is the machine at THAT instant — the magnets are nowhere near their own
    card, which is the whole point of reporting the moment instead of the
    steady state."""
    out = ttl.solve(
        thermal_result=PEAK_MAP, em_summary=PEAK_SUMMARY,
        limits=_limits(("winding", "winding", 200.0, 27.5, 777.0),
                       ("magnet", "magnet", 180.0, 1.2, 630.7)),
        caps=caps, geometry=L13_GEO, side_areas=areas,
        d_housing_m=D_HOUSING_M,
        cooling={"mount_g_w_per_k": 2.0, "mount_temp_c": 40.0}, duty="peak",
        rated_state_c={"winding": 103.7, "stator": 94.5, "rotor": 95.7,
                       "magnet": 95.6},
        rated_source="the rated duty's own stored thermal map")
    lim = ttl.limiting(out)
    assert lim["part"] == "winding"
    assert lim["limit_c"] == 200.0
    assert lim["offset_K"] == pytest.approx(27.5, abs=1e-6)
    # The node the final electromagnetic pass is solved at = limit − offset.
    assert lim["temperatures_at_limit"]["winding"] == pytest.approx(
        200.0 - 27.5, abs=0.2)
    # …and the magnets are still cold there, far under their 180 °C card.
    assert lim["temperatures_at_limit"]["magnet"] < 150.0
    assert 0.0 < lim["t_rated_s"] < lim["t_cold_s"]


def test_the_limited_sentence_names_the_power_and_the_cooling(caps, areas):
    """One sentence (the no-walls rule), and it states the two things the answer
    is conditional on — the owner's addendum of 2026-09-18."""
    out = ttl.solve(
        thermal_result=PEAK_MAP, em_summary=PEAK_SUMMARY,
        limits=_limits(("winding", "winding", 200.0, 27.5, 777.0)),
        caps=caps, geometry=L13_GEO, side_areas=areas,
        d_housing_m=D_HOUSING_M,
        cooling={"mount_g_w_per_k": 2.0, "mount_temp_c": 40.0}, duty="peak")
    line = ttl.limited_line(out)
    assert line.startswith("Runs ")
    assert "from cold at this power and cooling" in line
    assert "then the winding reaches 200 °C" in line
    assert line.endswith("the numbers below are the machine at that moment")
    assert "\n" not in line


def test_a_point_inside_every_limit_has_no_moment_to_report(caps, areas):
    out = ttl.solve(thermal_result=RATED_MAP, em_summary=RATED_SUMMARY,
                    limits=_limits(("winding", "winding", 200.0, 2.4, 106.1)),
                    caps=caps, geometry=L13_GEO, side_areas=areas,
                    d_housing_m=D_HOUSING_M,
                    cooling={"mount_g_w_per_k": 2.0, "mount_temp_c": 40.0},
                    duty="rated")
    assert ttl.limiting(out) is None
    assert ttl.limited_line(out) == ""


def test_a_limit_that_is_never_reached_leaves_the_steady_state_the_answer(
        caps, areas):
    """The brief's own rule: no crossing, no limited state — a time
    extrapolated out of a curve that flattens first would be invented."""
    out = ttl.solve(
        thermal_result=RATED_MAP, em_summary=RATED_SUMMARY,
        limits=_limits(("winding", "winding", 200.0, 2.4, 260.0)),
        caps=caps, geometry=L13_GEO, side_areas=areas,
        d_housing_m=D_HOUSING_M,
        cooling={"mount_g_w_per_k": 2.0, "mount_temp_c": 40.0}, duty="rated")
    assert out["within_limits"] is False        # it IS over
    assert ttl.limiting(out) is None            # …but there is no moment
    assert ttl.limited_line(out) == ""
