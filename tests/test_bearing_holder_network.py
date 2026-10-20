"""The rotor's axial heat path: shaft → bearing → holder → air / stator.

Owner, 2026-10-19: «подшипники не греются так же как обмотка».  The coupled
loop used to report the bearing at the rotor's own temperature; these tests pin
the pieces that replace it, all fast and all without the FEM:

  * the conduction solver's lumped NETWORK (node links + node sources): energy
    closes, a chain with no film anchors through the mesh, an un-anchored node
    is refused, and the 2026-09-26 single-node case is unchanged;
  * the two LIMIT CASES the owner asked for: a path that conducts nothing gives
    the old answer (the shaft temperature, untouched), and an infinite holder
    film puts the holder at ambient;
  * the bearing / holder resistances (``simulation.bearing_holder``): every one
    has a source, the 618/8 lands in the range the physics says, the CAD block
    wins over the defaults;
  * the rotor's axial channels (``cooling_models.rotor_axial_channels``) and
    their detection on a mesh;
  * ``mech_losses.bearing_temp_from_map`` reads the SOLVED bearing first.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from motor_ai_sim.simulation import bearing_holder as bh
from motor_ai_sim.simulation import cooling_models as cm
from motor_ai_sim.simulation.thermal_solver_2d import solve_steady_thermal

T_AMB = 40.0
L_STACK = 0.012
DOM_SHAFT, DOM_STATOR, DOM_ROTOR = 6, 1, 5


def _annulus(r0=0.003, r_split=0.006, r1=0.012, nr=8, nt=48):
    """A structured annulus: shaft (tag 6) inside ``r_split``, stator outside."""
    rs = np.linspace(r0, r1, nr + 1)
    ts = np.linspace(0.0, 2.0 * math.pi, nt, endpoint=False)
    pts = np.array([[r * math.cos(t), r * math.sin(t)] for r in rs for t in ts]).T
    tris, tags = [], []
    for i in range(nr):
        for j in range(nt):
            a = i * nt + j
            b = i * nt + (j + 1) % nt
            c = (i + 1) * nt + j
            d = (i + 1) * nt + (j + 1) % nt
            tag = DOM_SHAFT if rs[i + 1] <= r_split + 1e-12 else DOM_STATOR
            tris += [[a, b, d], [a, d, c]]
            tags += [tag, tag]
    return pts, np.array(tris).T, np.array(tags)


def _solve(q_shaft=2.0e6, sinks=None, nodes=None, links=None, h_outer=20.0):
    P, T, tags = _annulus()
    k = np.where(tags == DOM_SHAFT, 40.0, 25.0)
    q = np.where(tags == DOM_SHAFT, q_shaft, 0.0)
    return solve_steady_thermal(
        P, T, tags, k, q, drop_tags=[], r_housing_m=0.012,
        rotor_outer_m=0.006, stator_inner_m=0.006, gap_k=25.0,
        length_m=L_STACK,
        surfaces=[{"name": "outer", "h": h_outer, "t_sink": T_AMB}],
        volume_sinks=sinks, lumped_nodes=nodes, node_links=links)


# ---------------------------------------------------------------------------
# The solver's network
# ---------------------------------------------------------------------------

def test_a_chain_of_nodes_closes_its_energy_and_carries_its_source():
    sinks = [{"name": "brg_shaft", "tags": [DOM_SHAFT], "G_W_per_K": 0.2,
              "t_sink_c": T_AMB, "node": "inner"}]
    nodes = [{"name": "inner", "G_W_per_K": 0.0, "q_W": 0.5},
             {"name": "holder", "G_W_per_K": 0.3, "t_sink_c": T_AMB}]
    links = [{"name": "ring", "a": "inner", "b": "holder", "G_W_per_K": 0.1}]
    r = _solve(sinks=sinks, nodes=nodes, links=links)
    nd = {n["name"]: n for n in r["nodes"]}
    lk = r["node_links"][0]
    sk = r["sinks"][0]
    # the inner node: what the shaft sends + its own source = what the link carries
    assert sk["heat_removed_W"] + 0.5 == pytest.approx(lk["heat_W"], rel=1e-9, abs=1e-9)
    # the holder: what the link brings = its film
    assert lk["heat_W"] == pytest.approx(nd["holder"]["heat_removed_W"], rel=1e-9)
    # the link obeys G·ΔT, and the film G·(T − T_sink)
    assert lk["heat_W"] == pytest.approx(
        0.1 * (nd["inner"]["t_c"] - nd["holder"]["t_c"]), rel=1e-9)
    # the mesh's own closure is untouched by a node source
    assert abs(r["heat_residual_W"]) < 1e-6 * max(r["q_generated_W"], 1.0)
    # everything that leaves the machine = everything made in it
    out = r["surfaces"][0]["heat_removed_W"] + nd["holder"]["heat_removed_W"]
    assert out == pytest.approx(r["q_generated_W"] + 0.5, rel=1e-6)


def test_no_links_and_no_source_is_the_old_single_node_case():
    sinks = [{"name": "brg", "tags": [DOM_SHAFT], "G_W_per_K": 0.2,
              "t_sink_c": T_AMB, "node": "body"}]
    a = _solve(sinks=sinks, nodes=[{"name": "body", "G_W_per_K": 0.3,
                                    "t_sink_c": T_AMB}])
    b = _solve(sinks=sinks, nodes=[{"name": "body", "G_W_per_K": 0.3,
                                    "t_sink_c": T_AMB, "q_W": 0.0}], links=[])
    assert a["nodes"][0]["t_c"] == pytest.approx(b["nodes"][0]["t_c"], abs=1e-12)
    assert np.allclose(a["T_node"], b["T_node"])


def test_a_node_that_reaches_nothing_is_refused():
    nodes = [{"name": "a", "G_W_per_K": 0.0}, {"name": "b", "G_W_per_K": 0.0}]
    links = [{"a": "a", "b": "b", "G_W_per_K": 0.1}]
    with pytest.raises(ValueError, match="neither a film nor the mesh"):
        _solve(nodes=nodes, links=links)
    with pytest.raises(ValueError, match="no film"):
        _solve(nodes=[{"name": "lonely", "G_W_per_K": 0.0}])


# ---------------------------------------------------------------------------
# The owner's two limit cases
# ---------------------------------------------------------------------------

def _network_on_annulus(*, g_shaft, g_film, g_contact):
    """The real bearing network's shape on the test annulus."""
    sinks = [{"name": "bearing_A_shaft", "tags": [DOM_SHAFT],
              "G_W_per_K": g_shaft, "t_sink_c": T_AMB, "node": "inner"}]
    if g_contact > 0:
        sinks.append({"name": "bearing_A_holder_stator", "tags": [DOM_STATOR],
                      "G_W_per_K": g_contact, "t_sink_c": T_AMB,
                      "node": "holder"})
    nodes = [{"name": "inner", "G_W_per_K": 0.0},
             {"name": "balls", "G_W_per_K": 0.0},
             {"name": "outer", "G_W_per_K": 0.0},
             {"name": "holder", "G_W_per_K": g_film, "t_sink_c": T_AMB}]
    links = [{"a": "inner", "b": "balls", "G_W_per_K": 0.27},
             {"a": "balls", "b": "outer", "G_W_per_K": 0.29},
             {"a": "outer", "b": "holder", "G_W_per_K": 0.73}]
    return _solve(sinks=sinks, nodes=nodes, links=links)


def test_a_path_that_conducts_nothing_gives_the_old_answer():
    old = _solve()
    new = _network_on_annulus(g_shaft=1e-12, g_film=0.0, g_contact=0.0)
    # the machine is the old machine…
    assert np.allclose(old["T_node"], new["T_node"], atol=1e-6)
    # …and the bearing sits at the shaft it is pressed onto (the old 0 K drop)
    t_shaft = new["sinks"][0]["t_mean_c"]
    for n in new["nodes"]:
        assert n["t_c"] == pytest.approx(t_shaft, abs=0.05)


def test_an_infinite_holder_film_puts_the_holder_at_ambient():
    r = _network_on_annulus(g_shaft=0.3, g_film=1e9, g_contact=0.2)
    nd = {n["name"]: n for n in r["nodes"]}
    assert nd["holder"]["t_c"] == pytest.approx(T_AMB, abs=1e-3)
    # and the path really cools the shaft against the no-path machine
    assert r["sinks"][0]["t_mean_c"] < _solve()["T_max"]
    assert nd["inner"]["t_c"] > nd["outer"]["t_c"] > nd["holder"]["t_c"]


# ---------------------------------------------------------------------------
# The resistances
# ---------------------------------------------------------------------------

CARD_618_8 = {"name": "618/8-2Z", "d": 8, "D": 16, "B": 4, "d_m": 12,
              "n_balls": 10, "ball_d": 2.0}


def test_the_618_8_ring_to_ring_resistance_is_in_the_physical_range():
    b = bh.bearing_internal(CARD_618_8, rpm=13000, f_r_n=0.18, t_c=100)
    r = b["R_ring_to_ring_K_per_W"]
    # a lightly loaded miniature bearing: the oil wedge, not the Hertz spot,
    # carries the heat — several K/W, not a fraction of one, not hundreds
    assert 3.0 < r < 30.0
    assert b["G_hertz_inner_W_per_K"] < 0.1 * b["G_wedge_inner_W_per_K"]
    assert b["ball_set_source"] == "bearing card"


def test_more_load_and_a_wider_meniscus_both_conduct_better(monkeypatch):
    r_light = bh.bearing_internal(CARD_618_8, rpm=13000, f_r_n=0.2)["R_ring_to_ring_K_per_W"]
    r_heavy = bh.bearing_internal(CARD_618_8, rpm=13000, f_r_n=300.0)["R_ring_to_ring_K_per_W"]
    assert r_heavy < r_light
    monkeypatch.setattr(bh, "WEDGE_MENISCUS_M", 200e-6)
    r_wide = bh.bearing_internal(CARD_618_8, rpm=13000, f_r_n=0.2)["R_ring_to_ring_K_per_W"]
    assert r_wide < r_light


def test_a_card_without_a_ball_set_says_its_estimate():
    b = bh.bearing_internal({"name": "x", "d": 12, "D": 24, "B": 6},
                            rpm=10000, f_r_n=0.3)
    assert "ESTIMATE" in b["ball_set_source"]
    assert b["n_balls"] >= 6


def test_every_resistance_names_its_source_and_the_cad_block_wins():
    kw = dict(card=CARD_618_8, stack_m=0.012, shaft_area_stack_m2=49.64e-6,
              shaft_bore_d_m=5.9e-3, stator_od_m=0.040, stator_bore_m=0.024)
    g_def = bh.end_geometry(end="A", cad=None, **kw)
    cad = {"shaft_segments_mm": [[1.8, 49.64], [2.1, 22.93]],
           "holder": {"contact_core_mm2": 25.8, "film_area_mm2": 2086,
                      "char_len_mm": 33.7,
                      "arm_segments_mm": [[2.0, 165.7], [2.0, 108.5], [2.85, 28.9]]}}
    g_cad = bh.end_geometry(end="B", cad=cad, **kw)
    assert g_def["sources"]["holder"].startswith("DEFAULT")
    assert g_cad["sources"]["holder"].startswith("CAD")
    assert g_cad["sources"]["shaft"].startswith("CAD")
    # the CAD shaft: stack/6 at the stack section + the two measured segments
    r = bh.series_conduction(g_cad["shaft_segments_m"], 42.0)
    expect = (0.002 / (42 * 49.64e-6) + 0.0018 / (42 * 49.64e-6)
              + 0.0021 / (42 * 22.93e-6))
    assert r == pytest.approx(expect, rel=1e-9)
    net = bh.network(
        ends=[{"end": "B", "card": CARD_618_8, "geom": g_cad, "f_r_n": 0.18,
               "p_w": 0.03}], k_shaft=42.0, rpm=13000, t_ambient_c=T_AMB,
        air_mode="wash", air_speed_mps=12.9, holder_wall_c={},
        emissivity=0.9, symmetry_mult=2, shaft_tags=[6], stator_tags=[1])
    rows = net["ends"][0]["resistances"]
    assert len(rows) == 8
    assert all(r_["source"] for r_ in rows)
    # the friction goes in at the bearing: 25 / 50 / 25
    q = {n["name"]: n.get("q_W", 0.0) for n in net["nodes"]}
    assert q["brgB_balls"] == pytest.approx(0.015)
    assert q["brgB_inner"] == pytest.approx(0.0075)
    assert sum(q.values()) == pytest.approx(0.03)
    # the delta of the holder star is positive and smaller than each arm
    d = net["ends"][0]["delta"]
    assert 0 < d["G_rotor_air_W_per_K"] < d["G_rotor_holder_W_per_K"]


def test_the_holder_film_in_the_wash_beats_still_air():
    w = bh.holder_film(mode="wash", air_speed_mps=12.9, t_wall_c=80,
                       t_ambient_c=40, area_m2=2e-3, char_len_m=0.034)
    s = bh.holder_film(mode="still", air_speed_mps=0.0, t_wall_c=80,
                       t_ambient_c=40, area_m2=2e-3, char_len_m=0.034)
    assert w["G_W_per_K"] > s["G_W_per_K"] > 0.0
    assert "flat plate" in w["source"]


# ---------------------------------------------------------------------------
# The rotor's own axial channels
# ---------------------------------------------------------------------------

CH = {"area_m2": 60e-6, "perimeter_m": 0.035, "perimeter_magnet_m": 0.012,
      "perimeter_iron_m": 0.023, "r_m": 0.05, "count": 28}


def test_no_wash_no_through_flow_no_heat_removed():
    r = cm.rotor_axial_channels(channels=[CH], length_m=0.035,
                                face_speed_mps=0.0, rpm=4000,
                                t_air_c=40, t_wall_c=120)
    assert r["G_W_per_K"] == 0.0


def test_more_wash_cools_more_and_never_more_than_the_air_can_carry():
    g = []
    for v in (2.0, 8.0, 20.0):
        r = cm.rotor_axial_channels(channels=[CH], length_m=0.035,
                                    face_speed_mps=v, rpm=4000,
                                    t_air_c=40, t_wall_c=120)
        row = r["channels"][0]
        assert row["G_W_per_K"] <= row["C_W_per_K"] + 1e-12
        g.append(r["G_W_per_K"])
        assert r["G_magnet_W_per_K"] + r["G_iron_W_per_K"] == pytest.approx(
            28 * row["G_W_per_K"])
    assert g[0] < g[1] < g[2]


def test_an_adhesive_clearance_is_not_a_channel():
    thin = dict(CH, area_m2=0.1e-3 * 5e-3, perimeter_m=2 * 5.1e-3)
    r = cm.rotor_axial_channels(channels=[thin], length_m=0.012,
                                face_speed_mps=10.0, rpm=13000,
                                t_air_c=40, t_wall_c=110)
    assert r["n_open"] == 0 and r["G_W_per_K"] == 0.0


def test_the_channels_are_found_on_the_mesh():
    from motor_ai_sim.routes.thermal import (DOM_GAP_AIR, DOM_POCKET_AIR,
                                             _rotor_air_channels)
    # a 10 x 10 grid of 1 mm squares: rotor iron, one enclosed 2 x 2 pocket
    # lined by a magnet on its left, one pocket opening onto gap air at the top
    n = 10
    xs = np.linspace(0.0, 0.01, n + 1)
    pts = np.array([[x, y + 0.02] for y in xs for x in xs])
    tris, tags = [], []
    for j in range(n):
        for i in range(n):
            a, b = j * (n + 1) + i, j * (n + 1) + i + 1
            c, d = a + (n + 1), b + (n + 1)
            if 3 <= i <= 4 and 3 <= j <= 4:
                tag = DOM_POCKET_AIR
            elif i == 2 and 3 <= j <= 4:
                tag = 4                          # magnet N
            elif 7 <= i <= 8 and j == 8:
                tag = DOM_POCKET_AIR
            elif 7 <= i <= 8 and j == 9:
                tag = DOM_GAP_AIR
            else:
                tag = DOM_ROTOR
            tris += [[a, b, d], [a, d, c]]
            tags += [tag, tag]
    tris = np.array(tris)
    tags = np.array(tags)
    found = _rotor_air_channels(pts, tris, tags, tags == DOM_POCKET_AIR, 4,
                                mag_tags=(4, 44))
    assert len(found) == 2
    closed = next(c for c in found if not c["opens_to_gap"])
    opened = next(c for c in found if c["opens_to_gap"])
    assert closed["area_m2"] == pytest.approx(4e-6)
    assert closed["perimeter_m"] == pytest.approx(8e-3)
    assert closed["perimeter_magnet_m"] == pytest.approx(2e-3)
    assert closed["count"] == 4
    assert opened["area_m2"] == pytest.approx(2e-6)


# ---------------------------------------------------------------------------
# The bearing temperature the rest of the app reads
# ---------------------------------------------------------------------------

def test_the_bearing_temperature_is_the_solved_bearing_first():
    from motor_ai_sim.mech_losses import bearing_temp_from_map
    res = {"cooling": {"bearing_node": {"mode": "solved",
                                        "t_bearing_max_c": 87.5}},
           "components": {"shaft": {"avg": 113.0}}}
    t, where = bearing_temp_from_map(res)
    assert t == pytest.approx(87.5) and "bearing itself" in where
    res["cooling"]["bearing_node"]["mode"] = "off"
    t, where = bearing_temp_from_map(res)
    assert t == pytest.approx(113.0)


def test_the_real_network_on_a_mesh_closes_and_reports_signed_flows():
    kw = dict(card=CARD_618_8, stack_m=L_STACK, shaft_area_stack_m2=49.64e-6,
              shaft_bore_d_m=5.9e-3, stator_od_m=0.024, stator_bore_m=0.012)
    ends = [{"end": e, "card": CARD_618_8, "f_r_n": 0.18, "p_w": 0.05,
             "geom": bh.end_geometry(end=e, cad=None, **kw)} for e in "AB"]
    net = bh.network(ends=ends, k_shaft=42.0, rpm=13000, t_ambient_c=T_AMB,
                     air_mode="wash", air_speed_mps=12.0, holder_wall_c={},
                     emissivity=0.9, symmetry_mult=1, shaft_tags=[DOM_SHAFT],
                     stator_tags=[DOM_STATOR])
    r = _solve(sinks=net["sinks"], nodes=net["nodes"], links=net["links"])
    s = bh.summarize(r, net, t_ambient_c=T_AMB)
    for e in s["ends"]:
        # what reaches the holder (shaft side + stator side + friction) leaves by its film
        assert (e["shaft_to_bearing_W"] + e["stator_to_holder_W"] + e["friction_W"]
                == pytest.approx(e["holder_to_air_W"], rel=1e-6, abs=1e-9))
        # the stator here is the hotter side: heat LEAVES it into the holder
        assert e["stator_to_holder_W"] > 0.0
    # the reported bearing temperature is the hottest ring / ball node
    assert s["t_bearing_max_c"] == pytest.approx(max(
        max(e["inner_ring_c"], e["balls_c"], e["outer_ring_c"])
        for e in s["ends"]), abs=0.01)


# ---------------------------------------------------------------------------
# The holder rim in the coolant jacket (2026-10-20, G2-L40)
# ---------------------------------------------------------------------------

_RIM_CAD = {"holder": {"contact_core_mm2": 60.0, "film_area_mm2": 600.0,
                       "char_len_mm": 24.0, "coolant_area_mm2": 150.0,
                       "arm_segments_mm": [[4.0, 40.0], [4.0, 30.0]]}}


def _rim_net(coolant, cad=_RIM_CAD):
    kw = dict(card=CARD_618_8, stack_m=L_STACK, shaft_area_stack_m2=49.64e-6,
              shaft_bore_d_m=5.9e-3, stator_od_m=0.024, stator_bore_m=0.012)
    ends = [{"end": e, "card": CARD_618_8, "f_r_n": 0.18, "p_w": 0.05,
             "geom": bh.end_geometry(end=e, cad=cad, **kw)} for e in "AB"]
    return bh.network(ends=ends, k_shaft=42.0, rpm=13000, t_ambient_c=T_AMB,
                      air_mode="still", air_speed_mps=0.0, holder_wall_c={},
                      emissivity=0.9, symmetry_mult=1, shaft_tags=[DOM_SHAFT],
                      stator_tags=[DOM_STATOR], coolant=coolant)


def test_a_wetted_holder_rim_closes_its_energy_and_cools_the_bearing():
    dry = _rim_net(None)
    assert not any(n["name"].endswith("_rim") for n in dry["nodes"])
    # no wetted area: the jacket changes nothing at all
    cad0 = {"holder": dict(_RIM_CAD["holder"], coolant_area_mm2=0.0)}
    assert _rim_net({"h_W_per_m2K": 1060.0, "t_sink_c": T_AMB}, cad0) == _rim_net(None, cad0)
    wet = _rim_net({"h_W_per_m2K": 1060.0, "t_sink_c": T_AMB, "source": "test"})
    rim = {n["name"]: n for n in wet["nodes"]}["brgA_rim"]
    assert rim["G_W_per_K"] == pytest.approx(1060.0 * 150e-6)
    # the pads leave the RIM, not the holder node
    pad = {s_["name"]: s_ for s_ in wet["sinks"]}["bearing_A_holder_stator"]
    assert pad["node"] == "brgA_rim"
    assert any(r_["path"] == "holder rim → coolant jacket"
               for r_ in wet["ends"][0]["resistances"])
    r_d = _solve(sinks=dry["sinks"], nodes=dry["nodes"], links=dry["links"])
    r_w = _solve(sinks=wet["sinks"], nodes=wet["nodes"], links=wet["links"])
    s_d = bh.summarize(r_d, dry, t_ambient_c=T_AMB)
    s_w = bh.summarize(r_w, wet, t_ambient_c=T_AMB)
    for e in s_w["ends"]:
        # shaft side + stator side + friction leave by the air film AND the jacket
        assert (e["shaft_to_bearing_W"] + e["stator_to_holder_W"] + e["friction_W"]
                == pytest.approx(e["holder_to_air_W"] + e["holder_to_coolant_W"],
                                 abs=5e-4))
        assert e["holder_to_coolant_W"] > 0.0
        assert T_AMB < e["holder_rim_c"] < e["holder_c"]
    assert s_w["t_bearing_max_c"] < s_d["t_bearing_max_c"]
    assert s_w["holders_to_coolant_W"] > 0.0 and s_d["holders_to_coolant_W"] == 0.0
    # the delta gains the jacket on its air side, and the rim's stator leg
    d_d, d_w = dry["ends"][0]["delta"], wet["ends"][0]["delta"]
    assert d_w["G_rotor_air_W_per_K"] > d_d["G_rotor_air_W_per_K"]
    assert d_w["G_stator_air_W_per_K"] > d_d["G_stator_air_W_per_K"]
    assert d_w["coolant_sink_c"] == pytest.approx(T_AMB)


def test_an_infinite_jacket_film_puts_the_rim_at_the_coolant():
    t_c = 25.0
    wet = _rim_net({"h_W_per_m2K": 1.0e9, "t_sink_c": t_c})
    r = _solve(sinks=wet["sinks"], nodes=wet["nodes"], links=wet["links"])
    s_ = bh.summarize(r, wet, t_ambient_c=T_AMB)
    for e in s_["ends"]:
        assert e["holder_rim_c"] == pytest.approx(t_c, abs=0.01)
