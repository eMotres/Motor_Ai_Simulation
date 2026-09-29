"""THE CONTINUOUS RATING — what the machine may pull for ever, pinned five ways.

``coupled_continuous_rating`` is pure, so this suite solves nothing
electromagnetic and nothing two-dimensional.  It pins the feature where it can
be wrong:

  (a) ANALYTIC — on a machine with ONE thermal node and the copper feedback off,
      the steady state is ``T = T∞ + s²·P/G``, so the current scale that puts it
      exactly on a limit has a closed form and the bisection must find it;
  (b) WHICH PART BINDS — a magnet with a lower limit than the insulation class
      takes the answer over from the winding, and the rating drops accordingly;
  (c) THE REFUSALS — a cooling that cannot hold the losses which do NOT come
      from the current reports ``feasible: false`` with the temperature at
      ``s → 0``, never a number; a re-solved map that comes back COLDER with
      more copper in it stops the iteration and says so;
  (d) ρ(T) — the copper feedback makes the machine harder to cool the hotter it
      gets, so the rating with the feedback on must be BELOW the one with it
      off.  A sign error here would flatter every design;
  (e) THE FEM LOOP — with the 2-D solve monkeypatched to a linear map (hotter in
      proportion to the copper it is handed) the outer iteration must converge
      in two or three "solves" and must move ``s*`` in the direction the
      re-fitted network asks for.

The L13 (Ø85 / 13 mm robot joint) fixtures come from
``tests.test_thermal_duty_cycle`` — the same maps the duty-cycle and
time-to-limit suites are built on, so the three features cannot disagree about
the machine.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping

import pytest

import motor_ai_sim.coupled_continuous_rating as ccr
import motor_ai_sim.coupled_time_to_limit as ttl
import motor_ai_sim.thermal_duty_cycle as dc
from motor_ai_sim.thermal_capacities import NODES, part_capacities
from tests.test_thermal_capacities import (L13_MATERIALS, L13_PARTS,
                                           L13_SUMMARY)
from tests.test_thermal_duty_cycle import (D_HOUSING_M, L13_GEO, RATED_MAP,
                                           STACK_M)
from tests.test_thermal_duty_cycle import RATED_SUMMARY as _RATED_SUMMARY

#: The L13 rated run WITH the two numbers a rating is a multiple of.  The
#: duty-cycle fixtures never needed them (a cycle integrates watts, it does not
#: scale a current); this feature is defined against them, so they are stated
#: here rather than pushed into a fixture three suites share.
RATED_SUMMARY = dict(_RATED_SUMMARY, I_phase_rms_A=42.0, T_em_avg_Nm=3.4,
                     P_mech_W=356.0, gamma_deg=10.0)


@pytest.fixture(scope="module")
def caps():
    return part_capacities(L13_SUMMARY, L13_MATERIALS, L13_PARTS)


# ---------------------------------------------------------------------------
# A synthetic one-node machine, where the answer is a formula
# ---------------------------------------------------------------------------

def _one_node_network(g_w_per_k: float, t_amb: float = 25.0) -> dc.Network:
    """Every node merged into one lump with a single conductance to a sink."""
    return dc.Network(
        G={"w_s": 0.0, "r_s": 0.0, "m_r": 0.0, "s_mount": float(g_w_per_k),
           "r_bore": 0.0, "r_shaft": 0.0, "w_open": 0.0},
        areas={}, t_ambient_c=t_amb, t_mount_c=t_amb,
        node_of={n: "stator" for n in NODES},
        merged=(("winding", "stator"), ("rotor", "stator"),
                ("magnet", "stator")))


def _loss(p_cu: float, p_other: float = 0.0,
          coil_ref_c: float = 20.0) -> Dict[str, Any]:
    """A ``losses_by_node``-shaped dict, without a map to derive it from."""
    return {"winding": float(p_cu), "stator": float(p_other),
            "rotor": 0.0, "magnet": 0.0, "coil_ref_c": float(coil_ref_c),
            "rpm": 1000.0, "notes": [], "total_W": p_cu + p_other}


def _limit(part: str, node: str, limit_c: float,
           offset_k: float = 0.0) -> ttl.PartLimit:
    return ttl.PartLimit(part, node, float(limit_c), float(offset_k), 0.0,
                         "given by the test")


# ---------------------------------------------------------------------------
# (a) analytic — s* where the closed form puts it
# ---------------------------------------------------------------------------

def test_the_scale_is_where_the_closed_form_puts_it(monkeypatch):
    """One node, no copper feedback: T = T∞ + s²P/G, so s* = √(GΔT/P)."""
    monkeypatch.setattr(dc, "cu_rho_ratio", lambda *a, **k: 1.0)
    G, P, t_amb, t_lim = 2.0, 100.0, 25.0, 60.0
    net = _one_node_network(G, t_amb)
    caps = {n: 50.0 for n in NODES}
    exact = math.sqrt(G * (t_lim - t_amb) / P)         # 0.8366…

    got = ccr._search_scale(_loss(P), P, net, caps,
                            [_limit("winding", "winding", t_lim)], "test",
                            tol_k=0.01)
    assert got["feasible"] is True
    assert got["s"] == pytest.approx(exact, rel=2e-3), got
    assert got["limiting_part"] == "winding"
    assert abs(got["residual_K"]) <= 0.01


def test_the_hot_spot_offset_is_what_is_judged(monkeypatch):
    """A 10 K hot spot takes 10 K off the node the limit is reached at."""
    monkeypatch.setattr(dc, "cu_rho_ratio", lambda *a, **k: 1.0)
    G, P, t_amb, t_lim = 2.0, 100.0, 25.0, 60.0
    net = _one_node_network(G, t_amb)
    caps = {n: 50.0 for n in NODES}
    flat = ccr._search_scale(_loss(P), P, net, caps,
                             [_limit("winding", "winding", t_lim)], "t")["s"]
    hot = ccr._search_scale(_loss(P), P, net, caps,
                            [_limit("winding", "winding", t_lim, 10.0)],
                            "t")["s"]
    assert hot < flat
    assert hot == pytest.approx(math.sqrt(G * (t_lim - 10.0 - t_amb) / P),
                                rel=2e-3)


# ---------------------------------------------------------------------------
# (b) which part binds
# ---------------------------------------------------------------------------

def test_a_magnet_with_a_lower_limit_takes_the_answer_over(monkeypatch):
    monkeypatch.setattr(dc, "cu_rho_ratio", lambda *a, **k: 1.0)
    net = _one_node_network(2.0, 25.0)
    caps = {n: 50.0 for n in NODES}
    winding_only = ccr._search_scale(
        _loss(100.0), 100.0, net, caps,
        [_limit("winding", "winding", 200.0)], "t")
    with_magnet = ccr._search_scale(
        _loss(100.0), 100.0, net, caps,
        [_limit("winding", "winding", 200.0),
         _limit("magnet", "magnet", 120.0)], "t")
    assert winding_only["limiting_part"] == "winding"
    assert with_magnet["limiting_part"] == "magnet"
    assert with_magnet["s"] < winding_only["s"]


# ---------------------------------------------------------------------------
# (c) the refusals
# ---------------------------------------------------------------------------

def test_losses_that_do_not_come_from_the_current_can_make_it_infeasible(
        monkeypatch):
    """Iron alone over the limit → no rating, and the s→0 temperature with it."""
    monkeypatch.setattr(dc, "cu_rho_ratio", lambda *a, **k: 1.0)
    net = _one_node_network(1.0, 25.0)               # 1 W/K
    caps = {n: 50.0 for n in NODES}
    # 100 W of IRON on a 1 W/K machine settles at 125 °C, past a 60 °C limit
    # however small the current is.
    got = ccr._search_scale(_loss(10.0, p_other=100.0), 10.0, net, caps,
                            [_limit("winding", "winding", 60.0)], "t")
    assert got["feasible"] is False
    assert got["s"] is None
    assert got["limiting_part"] == "winding"
    assert got["parts"][0]["quantity_c"] == pytest.approx(125.0, abs=1.0)
    assert "at s → 0" in got["note"]


def test_no_part_with_a_limit_is_refused_by_name(caps):
    with pytest.raises(dc.DutyCycleError) as exc:
        ccr.rate(thermal_result=RATED_MAP, em_summary=RATED_SUMMARY, caps=caps,
                 limits=[], geometry=L13_GEO,
                 cooling={"ambient_temp": 40.0}, d_housing_m=D_HOUSING_M)
    assert exc.value.code == "no_part_limits"


# ---------------------------------------------------------------------------
# (d) ρ(T) — the feedback must make the rating SMALLER
# ---------------------------------------------------------------------------

def test_the_copper_feedback_lowers_the_rating():
    """Hotter copper is more copper loss, so s* with ρ(T) on is the smaller."""
    net = _one_node_network(2.0, 25.0)
    caps = {n: 50.0 for n in NODES}
    limits = [_limit("winding", "winding", 180.0)]
    with_feedback = ccr._search_scale(_loss(100.0, coil_ref_c=20.0), 100.0,
                                      net, caps, limits, "t")["s"]

    # …the same machine with the feedback removed, by pinning ρ to 1.
    real = dc.cu_rho_ratio
    try:
        dc.cu_rho_ratio = lambda *a, **k: 1.0            # noqa: E731
        without = ccr._search_scale(_loss(100.0, coil_ref_c=20.0), 100.0,
                                    net, caps, limits, "t")["s"]
    finally:
        dc.cu_rho_ratio = real
    assert with_feedback < without, (with_feedback, without)


# ---------------------------------------------------------------------------
# (e) the FEM loop, with the 2-D solve monkeypatched to a linear map
# ---------------------------------------------------------------------------

def _linear_map(p_cu_w: float, *, t_amb: float = 40.0,
                k_per_w: float = 1.0) -> Dict[str, Any]:
    """A 'solved map' whose temperatures are linear in the copper it carries.

    Not a physics model — a MONOTONE stand-in for the 2-D solve, so the outer
    iteration can be exercised without a mesh.  Everything the network fitter
    reads is present and self-consistent: the four node means, a housing that
    removes exactly what the machine makes, and the copper total.
    """
    p_other = 5.0
    total = float(p_cu_w) + p_other
    t_w = t_amb + k_per_w * total
    t_s = t_amb + 0.9 * k_per_w * total
    t_r = t_amb + 0.6 * k_per_w * total
    t_m = t_amb + 0.6 * k_per_w * total - 0.2
    return {
        "components": {"winding": {"avg": t_w, "max": t_w + 3.0},
                       "stator": {"avg": t_s, "max": t_s + 2.0},
                       "rotor": {"avg": t_r, "max": t_r + 1.0},
                       "magnet": {"avg": t_m, "max": t_m + 1.0}},
        "cooling": {
            "outer": {"mode": "manual", "h_conv": 300.0, "t_sink_c": t_amb,
                      "area_m2": 0.004, "heat_removed_W": total - 1.0},
            "heat_budget": {"losses_W": total, "housing_W": total - 1.0,
                            "bore_W": 1.0, "gap_W": 0.5, "shaft_ends_W": 0.0,
                            "coil_W": float(p_cu_w), "residual_pct": 0.0},
            "mech_losses": {"P_bearings_W": 0.0, "P_windage_gap_W": 0.0,
                            "shaft_ends_open": False}},
        "P_cu_exact_W": float(p_cu_w), "P_mag_eddy_W": 0.3,
        "ambient_temp": t_amb, "T_max": t_w + 3.0,
        "computed_at": "2026-09-20T00:00:00",
    }


def test_the_outer_loop_re_solves_the_map_and_converges(caps):
    """Two or three 'FEM solves', a converged s*, and the record says so."""
    p_cu_ref = float(RATED_MAP["P_cu_exact_W"])
    calls = []

    def resolve(factor: float) -> Mapping[str, Any]:
        calls.append(float(factor))
        return _linear_map(p_cu_ref * float(factor))

    out = ccr.rate(thermal_result=_linear_map(p_cu_ref),
                   em_summary=RATED_SUMMARY, caps=caps,
                   limits=[_limit("winding", "winding", 180.0, 3.0)],
                   geometry=L13_GEO, cooling={"ambient_temp": 40.0},
                   d_housing_m=D_HOUSING_M, resolve=resolve, duty="rated")
    assert out["feasible"] is True
    assert out["trustworthy"] is True
    assert out["converged"] is True, out["passes"]
    assert 2 <= out["n_thermal_fem_solves"] <= ccr.MAX_MAP_PASSES
    assert len(calls) == out["n_thermal_fem_solves"] - 1
    # the rating is a real current, and it is the reference current times s*
    assert out["I_cont_A_rms"] == pytest.approx(
        out["reference"]["I_phase_rms_A"] * out["s"], abs=1e-3)
    assert out["I_cont_A_peak"] == pytest.approx(
        out["I_cont_A_rms"] * math.sqrt(2.0), abs=1e-3)
    # …and the winding hot spot really is on the limit
    assert out["temperatures_c"]["winding"] == pytest.approx(180.0,
                                                             abs=ccr.TOL_K)
    assert out["power"]["T_em_Nm"] == pytest.approx(
        float(RATED_SUMMARY["T_em_avg_Nm"]) * out["s"], abs=1e-3)
    assert "LINEAR IN CURRENT" in out["power"]["torque_basis"]


def test_a_map_that_gets_colder_with_more_copper_stops_the_loop(caps):
    """The non-monotone guard: one pass, flagged, and NOT called a rating."""
    p_cu_ref = float(RATED_MAP["P_cu_exact_W"])

    def resolve(factor: float) -> Mapping[str, Any]:
        # colder the more copper it is handed — the robotics-mode pathology
        return _linear_map(p_cu_ref * float(factor),
                           k_per_w=1.0 / max(float(factor), 1e-6) ** 2)

    out = ccr.rate(thermal_result=_linear_map(p_cu_ref),
                   em_summary=RATED_SUMMARY, caps=caps,
                   limits=[_limit("winding", "winding", 180.0, 3.0)],
                   geometry=L13_GEO, cooling={"ambient_temp": 40.0},
                   d_housing_m=D_HOUSING_M, resolve=resolve, duty="rated")
    assert out["trustworthy"] is False
    assert out["n_thermal_fem_solves"] == 1
    assert any("NOT MONOTONE" in n for n in out["notes"])
    assert ccr.headline(out).startswith("NOT A RATING")


def test_without_a_resolve_callback_the_answer_says_so(caps):
    out = ccr.rate(thermal_result=_linear_map(float(RATED_MAP["P_cu_exact_W"])),
                   em_summary=RATED_SUMMARY, caps=caps,
                   limits=[_limit("winding", "winding", 180.0, 3.0)],
                   geometry=L13_GEO, cooling={"ambient_temp": 40.0},
                   d_housing_m=D_HOUSING_M, duty="rated")
    assert out["n_thermal_fem_solves"] == 1
    assert any("no loss map was re-solved" in n for n in out["notes"])


# ---------------------------------------------------------------------------
# The cooling conditions themselves
# ---------------------------------------------------------------------------

def test_a_condition_is_a_patch_over_the_duty_s_saved_setup():
    defaults = {"cooling_mode": "air", "ambient_temp": 30.0,
                "air_speed_mps": 40.0, "bore_mode": "air",
                "bore_air_speed_mps": 10.0, "frame": "open"}
    got = ccr.Condition("20 m/s", {"air_speed_mps": 20.0}).merged(defaults)
    assert got["air_speed_mps"] == 20.0
    assert got["bore_mode"] == "air" and got["bore_air_speed_mps"] == 10.0
    assert got["frame"] == "open"


def test_a_mode_only_gets_the_parameters_it_reads():
    """A liquid request carries no air speed, an air one carries no flow."""
    liquid = ccr.Condition("jacket", {
        "cooling_mode": "liquid", "fluid": "water", "flow_lpm": 2.0,
        "fluid_temp_in_c": 25.0}).merged(
            {"cooling_mode": "air", "air_speed_mps": 40.0,
             "ambient_temp": 30.0, "bore_mode": "none"})
    assert "air_speed_mps" not in liquid
    assert liquid["flow_lpm"] == 2.0
    air = ccr.Condition("10 m/s", {"cooling_mode": "air",
                                   "air_speed_mps": 10.0}).merged(
        {"cooling_mode": "liquid", "flow_lpm": 8.0, "ambient_temp": 30.0,
         "bore_mode": "none"})
    assert "flow_lpm" not in air and "fluid" not in air
    assert air["air_speed_mps"] == 10.0


def test_the_saved_cooling_is_read_back_out_of_a_duty_s_thermal_block():
    """The duty record keeps the ANSWER; the question is reconstructed from it."""
    block = {
        "point": {"cooling_mode": "air", "ambient_temp": 30.0, "h_conv": 50.0,
                  "flow_lpm": 0.0},
        "cooling": {
            "outer": {"mode": "air", "air_speed_mps": 40.0, "t_sink_c": 30.0},
            "inner": {"mode": "air", "air_speed_mps": 10.0},
            "end_windings": {"mode": "end turns in the airflow",
                             "air_speed_mps": 40.0},
            "shaft_ends": {"mode": "off", "length_each_side_mm": 0.0},
            "mount": {"mode": "off", "G_W_per_K": 0.0},
            "end_faces": {"mode": "off", "sides": 0}},
    }
    got = ccr.cooling_from_duty_thermal(block)
    assert got == {"cooling_mode": "air", "ambient_temp": 30.0,
                   "air_speed_mps": 40.0, "bore_mode": "air",
                   "bore_air_speed_mps": 10.0, "frame": "open"}


def test_a_robotics_setup_reads_back_its_mount_and_its_end_faces():
    block = {
        "point": {"cooling_mode": "robotics", "ambient_temp": 30.0},
        "cooling": {
            "outer": {"mode": "robotics", "emissivity": 0.9, "t_sink_c": 30.0},
            "inner": {"mode": "still"},
            "end_windings": {"mode": "housed"},
            "mount": {"mode": "conduction", "G_W_per_K": 1.0,
                      "t_sink_c": 30.0},
            "end_faces": {"mode": "still", "sides": 2, "emissivity": 0.9}},
    }
    got = ccr.cooling_from_duty_thermal(block)
    assert got["cooling_mode"] == "robotics"
    assert got["bore_mode"] == "still"
    assert got["mount_g_w_per_k"] == 1.0 and got["mount_temp_c"] == 30.0
    assert got["end_faces"] == "still" and got["end_face_sides"] == 2
    assert "frame" not in got


# ---------------------------------------------------------------------------
# The open frame's own paths, in the network (2026-09-20)
# ---------------------------------------------------------------------------

def _open_frame_map() -> Dict[str, Any]:
    """A map whose winding sends most of its heat STRAIGHT to the room."""
    m = _linear_map(100.0)
    m["cooling"]["end_windings"] = {"mode": "end turns in the airflow",
                                    "heat_removed_W": 70.0,
                                    "G_W_per_K": 0.7, "t_sink_c": 40.0}
    m["cooling"]["slot_channels"] = {"mode": "ventilated slot channels",
                                     "heat_removed_W": 15.0,
                                     "G_W_per_K": 0.15, "t_sink_c": 40.0}
    m["cooling"]["heat_budget"]["end_windings_W"] = 70.0
    m["cooling"]["heat_budget"]["slot_channels_W"] = 15.0
    m["cooling"]["heat_budget"]["housing_W"] = 20.0
    m["cooling"]["outer"]["heat_removed_W"] = 20.0
    return m


def test_the_open_frame_s_winding_path_is_carried_by_the_network():
    """85 of the 100 W of copper leave the winding directly; the network knows."""
    net = dc.network_from_steady(_open_frame_map(), t_ambient_c=40.0,
                                 d_housing_m=D_HOUSING_M, geometry=L13_GEO)
    assert net.G["w_open"] > 0.0
    means = {"winding": 145.0, "stator": 134.5, "rotor": 103.0,
             "magnet": 102.8}
    state = {net.rep(n): means[n] for n in NODES}
    flows = dc._flows(state, net)
    assert flows["winding_open"] == pytest.approx(85.0, rel=1e-6)
    # …and the winding→stator link is fitted to what actually crosses, not to
    # the whole copper loss.
    assert flows["w_s"] == pytest.approx(100.0 - 85.0, rel=1e-6)
    assert any("leave the winding DIRECTLY" in n for n in net.notes)


def test_the_surface_fit_is_the_default_and_can_still_be_asked_off():
    """ON since 2026-09-21 (owner's word), and the old fit is still reachable.

    The default carries the map's surfaces; ``surface_fit=False`` restores the
    pre-2026-09-21 network exactly, which is what a record written before that
    date has to be read back with.
    """
    on = dc.network_from_steady(_open_frame_map(), t_ambient_c=40.0,
                                d_housing_m=D_HOUSING_M, geometry=L13_GEO)
    assert on.G["w_open"] > 0.0 and on.G_fit

    off = dc.network_from_steady(_open_frame_map(), t_ambient_c=40.0,
                                 d_housing_m=D_HOUSING_M, geometry=L13_GEO,
                                 surface_fit=False)
    assert off.G.get("w_open", 0.0) == 0.0
    assert off.G_fit == {} and off.film_kind == {}
    means = {"winding": 145.0, "stator": 134.5, "rotor": 103.0,
             "magnet": 102.8}
    state = {off.rep(n): means[n] for n in NODES}
    flows = dc._flows(state, off)
    assert flows["winding_open"] == 0.0
    # …and the winding→stator link is the whole copper loss, as it always was
    assert flows["w_s"] == pytest.approx(100.0, rel=1e-6)


def test_a_housed_map_is_untouched_by_the_open_frame_paths():
    """Zero watts, zero conductance — nothing computed before this moves."""
    net = dc.network_from_steady(RATED_MAP, t_ambient_c=40.0,
                                 d_housing_m=D_HOUSING_M, geometry=L13_GEO,
                                 surface_fit=True)
    assert net.G.get("w_open", 0.0) == 0.0
    state = {net.rep(n): float(RATED_MAP["components"][n]["avg"])
             for n in NODES}
    assert dc._flows(state, net)["winding_open"] == 0.0


def test_a_forced_housing_film_is_held_and_a_natural_one_moves():
    """The map's own conductance is the level; only a natural film re-scales."""
    m = _linear_map(100.0)
    m["cooling"]["outer"]["regime"] = "forced"
    net = dc.network_from_steady(m, t_ambient_c=40.0, d_housing_m=D_HOUSING_M,
                                 geometry=L13_GEO, surface_fit=True)
    t_fit = net.t_fit_c["housing"]
    assert net.film_kind["housing"] == "forced"
    assert dc.still_air_G(t_fit + 100.0, net, "housing") == pytest.approx(
        dc.still_air_G(t_fit, net, "housing"))

    m2 = _linear_map(100.0)
    m2["cooling"]["outer"]["mode"] = "robotics"
    net2 = dc.network_from_steady(m2, t_ambient_c=40.0,
                                  d_housing_m=D_HOUSING_M, geometry=L13_GEO,
                                  surface_fit=True)
    assert net2.film_kind["housing"] == "natural"
    t0 = net2.t_fit_c["housing"]
    assert dc.still_air_G(t0 + 100.0, net2, "housing") > dc.still_air_G(
        t0, net2, "housing")


# ---------------------------------------------------------------------------
# The stored record must be ONE machine (2026-09-21)
# ---------------------------------------------------------------------------
# A `solve_to: limits` run files the machine AT the crossing.  Until today that
# record carried the LIMITED components beside the STEADY cooling block with
# nothing saying so, and the L13 peak on this disk is the measured case: a
# 183.5 °C winding beside a 297.3 °C housing wall and a 682.8 W heat budget,
# refitting to a 55-67 % residual and no time to any limit.  These pin the
# label and the calibration means that make it readable again.

def _synthetic_steady_map() -> Dict[str, Any]:
    """A solved steady map, in the shape `rescale_map_to_nodes` takes."""
    return {
        "ok": True,
        "state": "steady",
        "components": {"winding": {"avg": 392.4, "max": 408.9},
                       "stator": {"avg": 300.0, "max": 312.0},
                       "rotor": {"avg": 120.0, "max": 121.0},
                       "magnet": {"avg": 119.0, "max": 120.5}},
        "cooling": {"outer": {"mode": "robotics", "heat_removed_W": 26.86,
                              "t_sink_c": 40.0},
                    "mount": {"mode": "conduction", "G_W_per_K": 2.0,
                              "t_housing_mean_c": 297.3,
                              "heat_removed_W": 514.6, "t_sink_c": 40.0},
                    "heat_budget": {"losses_W": 682.8, "housing_W": 26.86,
                                    "bore_W": 8.49, "residual_pct": 0.0}},
        "P_cu_exact_W": 676.2, "P_mag_eddy_W": 1.0, "ambient_temp": 40.0,
        # the two arrays the shift needs
        "triangles": [[0, 1, 2], [1, 2, 3]],
        "domain_per_tri": [2, 2],
        "temperature_per_node": [390.0, 392.0, 394.0, 396.0],
        "T_max": 408.9, "T_min": 40.0,
    }


def test_a_limited_snapshot_says_which_state_it_is_and_what_it_came_from():
    from motor_ai_sim.routes.thermal import rescale_map_to_nodes

    steady = _synthetic_steady_map()
    at_limit = {"winding": 183.5, "stator": 125.06, "rotor": 45.68,
                "magnet": 43.7}
    snap = rescale_map_to_nodes(steady, at_limit)

    assert snap["state"] == "limited"
    assert snap["cooling"]["from_state"] == "steady"
    assert "NOT to the limited instant" in snap["cooling"]["state_note"]
    # …and the state the cooling block DOES belong to travels with it
    cal = snap["transient_snapshot"]["calibration_components_c"]
    assert cal["winding"]["avg"] == pytest.approx(392.4)
    assert cal["magnet"]["avg"] == pytest.approx(119.0)
    # the original is untouched — the snapshot is a copy
    assert steady["state"] == "steady"
    assert "from_state" not in steady["cooling"]


def test_the_duty_record_carries_the_state_and_stays_readable():
    from motor_ai_sim.duty_results import compact_thermal
    from motor_ai_sim.routes.thermal import rescale_map_to_nodes

    steady = _synthetic_steady_map()
    rec_steady = compact_thermal(steady, {}, "fp0", "2026-09-21T00:00:00")
    assert rec_steady["state"] == "steady"
    assert "from_state" not in rec_steady["cooling"]
    assert "calibration_components_c" not in rec_steady

    snap = rescale_map_to_nodes(steady, {"winding": 183.5, "stator": 125.06,
                                         "rotor": 45.68, "magnet": 43.7})
    rec = compact_thermal(snap, {}, "fp0", "2026-09-21T00:00:00")
    assert rec["state"] == "limited"
    assert rec["cooling"]["from_state"] == "steady"
    # the components are the LIMITED instant …
    assert rec["components"]["winding"]["avg"] == pytest.approx(183.5, abs=1.0)
    # … and the means the cooling block belongs to are beside them, so the
    # steady state can be refitted from the record alone
    assert rec["calibration_components_c"]["winding"]["avg"] == pytest.approx(
        392.4)
    assert rec["transient_snapshot"]["state"] == "limited"


def test_the_time_to_limit_record_carries_all_four_calibration_means(caps):
    """`map_means_c` — what makes a limited record refittable read-only."""
    m = _linear_map(float(RATED_MAP["P_cu_exact_W"]))
    net = dc.network_from_steady(m, t_ambient_c=40.0,
                                 d_housing_m=D_HOUSING_M, geometry=L13_GEO)
    seg = ttl._segment(RATED_SUMMARY, m, "rated")
    got = ttl._fit_residual(seg, net, caps, m)
    assert got["available"] is True
    assert set(got["map_means_c"]) == set(NODES)
    assert got["map_means_c"]["winding"] == pytest.approx(
        float(m["components"]["winding"]["avg"]), abs=0.01)
