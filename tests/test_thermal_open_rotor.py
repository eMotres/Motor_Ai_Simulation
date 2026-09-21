"""The OPEN frame, part two: the ROTOR is in the wash as well (2026-09-21).

User, with one thermal photograph of an open drone motor on a propeller stand
in front of him: *«по термофотографиям катушки греются всегда значительно больше
магнитов; конструкция полностью открыта, магниты обдуваются со всех сторон, и
воздух ещё продувает зазор — надо это как-то учесть, когда мы задаём no
housing»*.  The 2026-09-09 open frame put the STATOR side in the propeller
stream (end turns, slot channels) and left the rotor exactly where the housed
model had it, with the mechanical clearance and the bore as its only doors — and
on the CIANO14 50 edited / L15 record that reads the magnets at 240 °C against a
251 °C winding, i.e. the rotor all but welded to the stator through 0.2 mm of
air.  The photograph says the rotor's rise is about HALF the winding's.

What this module pins is the CONTRACT of the two paths that were added, not a
temperature:

  (a) HOUSED IS UNTOUCHED — a machine with a housing reports both new blocks as
      ``mode: off`` with no watts on them, which is a statement and not a
      missing key, and its map is the one it always was;
  (b) OPEN puts a FORCED film on the rotor's own end faces — the rotating disc
      and the flat plate, whichever is larger, with no radiation — and the four
      end-face blocks say which film each of them is on (``film_kind``), because
      the lumped network downstream has to know;
  (c) OPEN turns the clearance into a VENTILATED DUCT whose through-velocity is
      SOLVED from a pressure balance (so it is a fraction of the wash, not the
      wash), whose mass flow is a real number and whose energy balance closes:
      ``Q = G·(T_air − T_∞)`` and ``ΔT_air = Q/(ṁ·cp)``;
  (d) the open magnet RECESS is part of that duct when the pockets run out to
      the rotor OD, and the block says whether it found one;
  (e) the whole budget still closes with both new outflows counted, and the
      lumped network SEES them — ``r_gap_flow`` / ``s_gap_flow`` fitted, the
      rotor faces held as a forced film rather than walked down a Rayleigh
      number.

Runs on the same 30 mm fixture and the same one Electromagnetic run as
tests/test_thermal_open_frame.py, for the same reason: what is claimed here is a
contract, and a converged 200 mm machine would buy nothing at many times the
wall clock.
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import time

import pytest

from tests.test_thermal_routes import GEO_30MM, store_em_run

FAST = {
    "n_steps_per_period": 4, "n_periods": 1.0,
    "mesh_size_mm": 2.5, "min_size_mm": 0.6, "n_sectors": 2,
    "I_phase_rms": 60.0, "rpm": 3000.0, "gamma_deg": 0.0,
    "coil_temp_c": 120.0,
}
AIR = {"cooling_mode": "air", "ambient_temp": 30.0, "air_speed_mps": 5.0}
RUN_ID = "2026-09-21T09:00:00"
#: The wash, m/s — the propeller stream the user quotes for these machines.
WASH = 10.0
#: J/kg·K — `cooling_models._AIR_CP`, spelled here so the energy balance below
#: is checked against a number and not against the module that produced it.
AIR_CP = 1007.0


def _req(**over) -> dict:
    p = {**FAST, **AIR, "geo": json.dumps(GEO_30MM)}
    p.update(over)
    return p


@pytest.fixture(scope="module")
def em_run():
    from motor_ai_sim.routes import simulation as sim

    mp = pytest.MonkeyPatch()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="thermal_open_rotor_"))
    mp.setattr(sim, "_transient_field_store_path", lambda: str(tmp / ".snap.pkl"))
    saved = dict(sim._transient_field_snap)
    sim._transient_field_snap.clear()

    info = store_em_run(GEO_30MM, run_id=RUN_ID, phys=FAST)
    time.sleep(0.3)

    yield info

    sim._transient_field_snap.clear()
    sim._transient_field_snap.update(saved)
    mp.undo()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from motor_ai_sim.routes import thermal as th

    monkeypatch.setattr(th, "_LAST", {}, raising=True)
    monkeypatch.setattr(th, "_LAST_LOADED", True, raising=True)
    monkeypatch.setattr(th, "_last_store_path",
                        lambda: str(tmp_path / ".last_thermal.pkl"))
    monkeypatch.setattr(th, "_loss_maps_path",
                        lambda: str(tmp_path / ".loss_maps.pkl"))
    monkeypatch.setattr(th, "_LOSS_MAPS_LOADED", True, raising=True)
    th._LOSS_MAPS.clear()
    th._FIELD_CACHE.clear()
    th._COUPLED_CACHE.clear()
    yield
    th._LOSS_MAPS.clear()
    th._FIELD_CACHE.clear()
    th._COUPLED_CACHE.clear()


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


def _field(client, **over):
    r = client.get("/api/thermal/field", params=_req(**over))
    assert r.status_code == 200, r.text[:900]
    return r.json()


# ---------------------------------------------------------------------------
# (a) housed is untouched
# ---------------------------------------------------------------------------

def test_a_housed_machine_says_its_gap_is_closed_instead_of_leaving_it_out(
        client, em_run):
    """``mode: off`` is an ANSWER — the same rule the bore's ``none`` follows."""
    c = _field(client, frame="housed")["cooling"]
    gf = c["gap_flow"]
    assert gf["mode"] == "off"
    assert gf["G_W_per_K"] == 0.0
    assert gf["heat_removed_W"] == 0.0
    assert "Taylor" in gf["note"] or "closed" in gf["note"]
    # …and no rotor face was put in any airflow either.
    ef = c["end_faces"]
    assert ef["mode"] == "off"
    for node in ("winding", "stator", "rotor", "magnet"):
        assert ef[node]["heat_removed_W"] == 0.0


def test_a_housed_budget_carries_the_new_lines_at_zero(client, em_run):
    """Every line is always there; a housed machine's two new ones read 0.

    A missing key would make a client's budget arithmetic depend on the frame.
    """
    b = _field(client, frame="housed")["cooling"]["heat_budget"]
    assert b["gap_flow_W"] == 0.0
    assert b["gap_flow_rotor_W"] == 0.0
    assert b["gap_flow_stator_W"] == 0.0
    assert b["end_faces_W"] == 0.0
    assert b["residual_pct"] < 2.0, b


# ---------------------------------------------------------------------------
# (b) the rotor's end faces, in the wash
# ---------------------------------------------------------------------------

def test_the_rotor_end_faces_are_forced_convection_not_still_air(client, em_run):
    """The claim: an open rotor face is in the propeller stream.

    Forced, at the wash speed, with NO radiation term — and well above the
    7 W/m²·K natural floor, which is what "still air" would have given it.
    """
    from motor_ai_sim.simulation.cooling_models import NATURAL_CONVECTION_H

    ef = _field(client, frame="open",
                open_air_speed_mps=WASH)["cooling"]["end_faces"]
    assert ef["mode"] == "forced"
    for node in ("rotor", "magnet"):
        blk = ef[node]
        assert blk["mode"] == "forced", blk
        assert blk["film_kind"] == "forced"
        assert blk["air_speed_mps"] == pytest.approx(WASH, rel=1e-6)
        assert blk["h_rad"] == 0.0            # a face sees the end plate
        assert blk["h_conv"] > NATURAL_CONVECTION_H
        assert blk["G_W_per_K"] > 0.0
        assert blk["area_m2"] > 0.0
        assert blk["n_faces"] == 2
    # The WINDING's and the STATOR's ends are deliberately NOT added on an open
    # frame: the end turns already have `cooling.end_windings` in this same
    # wash, and counting them twice would cool the copper twice.
    assert ef["winding"]["heat_removed_W"] == 0.0
    assert ef["stator"]["heat_removed_W"] == 0.0


def test_the_rotor_faces_remove_what_their_conductance_says(client, em_run):
    """``P = G·(T_mean − T_sink)`` in MACHINE watts — the wedge bookkeeping."""
    f = _field(client, frame="open", open_air_speed_mps=WASH)
    ef = f["cooling"]["end_faces"]
    for node in ("rotor", "magnet"):
        blk = ef[node]
        assert blk["t_mean_c"] is not None
        assert blk["heat_removed_W"] == pytest.approx(
            blk["G_W_per_K"] * (blk["t_mean_c"] - blk["t_sink_c"]),
            rel=0.05, abs=0.01)


def test_a_faster_wash_cools_the_rotor_faces_harder(client, em_run):
    """Monotone in the one input it has — and the rotating disc is the floor.

    At zero wash the faces are NOT off: the rotor is still spinning, and the
    disc film is there whatever the aircraft is doing.
    """
    slow = _field(client, frame="open", open_air_speed_mps=1.0,
                  )["cooling"]["end_faces"]["magnet"]
    fast = _field(client, frame="open", open_air_speed_mps=4.0 * WASH,
                  )["cooling"]["end_faces"]["magnet"]
    assert fast["h_conv"] > slow["h_conv"]
    assert slow["h_conv"] > 0.0


# ---------------------------------------------------------------------------
# (c) the ventilated gap
# ---------------------------------------------------------------------------

def test_the_gap_through_velocity_is_a_fraction_of_the_wash(client, em_run):
    """It is SOLVED, not assumed: a 0.2 mm slot 10 mm long is not a free jet.

    The whole reason the velocity is a pressure balance rather than a typed
    fraction is that a client must be able to see WHY it is what it is — so the
    losses that produced it are in the payload beside it.
    """
    gf = _field(client, frame="open",
                open_air_speed_mps=WASH)["cooling"]["gap_flow"]
    assert gf["mode"] == "through-flow"
    assert gf["air_speed_mps"] == pytest.approx(WASH, rel=1e-6)
    assert 0.0 < gf["gap_speed_mps"] < WASH
    assert 0.0 < gf["speed_fraction"] < 1.0
    assert gf["entrance_k"] > 0.0 and gf["exit_k"] > 0.0
    assert gf["L_over_Dh"] > 1.0
    assert gf["m_dot_g_s"] > 0.0
    assert gf["G_W_per_K"] > 0.0
    # The calibration hook is reported and is the physics-derived default.
    assert gf["calibration"] == pytest.approx(1.0)


def test_the_gap_stream_energy_balance_closes(client, em_run):
    """``Q = G·(T_air − T_∞)`` and ``ΔT_air = Q/(ṁ·cp)`` — both, from the payload.

    The second is the one that makes the model falsifiable: a channel cannot
    carry more heat than its own mass flow will hold, and the outlet temperature
    the block prints is that statement.
    """
    gf = _field(client, frame="open",
                open_air_speed_mps=WASH)["cooling"]["gap_flow"]
    q = gf["heat_removed_W"]
    assert q > 0.0
    assert gf["rotor_side_W"] + gf["stator_side_W"] == pytest.approx(q, abs=1e-3)
    assert gf["t_air_rotor_c"] is not None and gf["t_air_stator_c"] is not None
    # the two halves, each against its own conductance
    assert gf["rotor_side_W"] == pytest.approx(
        gf["G_rotor_W_per_K"] * (gf["t_air_rotor_c"] - gf["t_sink_c"]),
        rel=0.05, abs=0.01)
    assert gf["stator_side_W"] == pytest.approx(
        gf["G_stator_W_per_K"] * (gf["t_air_stator_c"] - gf["t_sink_c"]),
        rel=0.05, abs=0.01)
    # …and the stream's own enthalpy rise
    m_dot = gf["m_dot_g_s"] * 1e-3
    assert gf["dT_air_K"] == pytest.approx(q / (m_dot * AIR_CP), rel=0.02)
    assert gf["t_air_out_c"] == pytest.approx(
        gf["t_sink_c"] + gf["dT_air_K"], rel=1e-6, abs=1e-6)


def test_the_open_magnet_recess_is_part_of_the_channel(client, em_run):
    """This fixture's pockets run out to the OD (rotor_hole 0.7, up_gap 0.1 mm).

    So the air above the magnet is the gap's air, the magnet's top face is
    washed by it, and the recess's free area — MEASURED on the mesh, the same
    rule the slot channels follow — is part of the mass flow.
    """
    gf = _field(client, frame="open",
                open_air_speed_mps=WASH)["cooling"]["gap_flow"]
    assert gf["recess_open"] is True
    assert gf["recess_area_mm2"] > 0.0
    assert gf["cross_section_mm2"] > gf["recess_area_mm2"]
    assert any("recess" in n for n in gf.get("notes") or []), gf.get("notes")


def test_no_wash_leaves_the_gap_the_closed_conductor_it_always_was(client, em_run):
    """An open machine standing still: the clearance is not ventilated.

    ``frame=open`` with the outer surface in 5 m/s of air DOES ventilate it (the
    wash falls back to the housing's own air, the 2026-09-09 rule), so the honest
    "nothing is blowing" case is the one where neither speed is set.
    """
    gf = _field(client, frame="open", cooling_mode="manual", h_conv=20.0,
                air_speed_mps=0.0, open_air_speed_mps=0.0,
                )["cooling"]["gap_flow"]
    assert gf["mode"] == "off"
    assert gf["heat_removed_W"] == 0.0
    assert any("no wash" in n or "not ventilated" in n
               for n in gf.get("notes") or []), gf.get("notes")


def test_the_budget_closes_with_the_rotor_paths_counted(client, em_run):
    """Every watt that leaves is on a named line, rotor side included."""
    f = _field(client, frame="open", open_air_speed_mps=WASH)
    b = f["cooling"]["heat_budget"]
    assert b["residual_pct"] < 2.0, b
    assert b["gap_flow_W"] > 0.0
    assert (b["housing_W"] + b["bore_W"] + b["shaft_ends_W"]
            + b["end_windings_W"] + b["slot_channels_W"]
            + b["end_faces_W"] + b["gap_flow_W"] + b["mount_W"]
            == pytest.approx(b["losses_W"], rel=0.02, abs=0.02))
    # …and each SIDE closes on its own half — which is the whole reason the gap
    # stream is split at the slip radius instead of being one sink.
    rs = b["rotor_heat_split"]
    assert rs["gap_flow_W"] == pytest.approx(b["gap_flow_rotor_W"], abs=1e-3)
    assert abs(rs["closure_W"]) < max(0.02 * abs(rs["rotor_W"]), 0.05)
    ss = b["stator_heat_split"]
    assert ss["gap_flow_W"] == pytest.approx(b["gap_flow_stator_W"], abs=1e-3)
    assert abs(ss["closure_W"]) < max(0.02 * abs(ss["total_in_W"]), 0.05)


def test_the_open_rotor_runs_colder_than_the_same_machine_housed(client, em_run):
    """The claim the feature is for, stated as the user stated it.

    Same machine, same losses, same outer film: putting the rotor in the wash
    has to move its temperature DOWN, and it has to move it further than it
    moves the winding's — which is exactly what the thermal photographs say (the
    coils are always much hotter than the magnets).  The ratio itself is not
    asserted to a number here: that is a calibration, and the calibration
    constants are still at their physics-derived 1.0.
    """
    housed = _field(client, frame="housed")
    openf = _field(client, frame="open", open_air_speed_mps=WASH)
    amb = 30.0

    def rise(f, node):
        return float(f["components"][node]["avg"]) - amb

    assert rise(openf, "magnet") < rise(housed, "magnet")
    assert rise(openf, "rotor") < rise(housed, "rotor")
    # the rotor's rise falls FURTHER, relative to the winding's, than it was
    r_housed = rise(housed, "magnet") / max(rise(housed, "winding"), 1e-9)
    r_open = rise(openf, "magnet") / max(rise(openf, "winding"), 1e-9)
    assert r_open < r_housed


# ---------------------------------------------------------------------------
# (e) the lumped network has to SEE all of it
# ---------------------------------------------------------------------------

def test_the_network_carries_the_two_gap_streams_and_holds_the_forced_faces(
        client, em_run):
    """A path the map takes 40 % of the machine's heat through, and the network
    does not know about it, is how every transient on such a machine ran away
    (2026-09-20, the end faces).  So: fitted conductances for both gap streams,
    and the rotor's faces HELD as a forced film instead of being walked down a
    Rayleigh number to a tenth of what the map used.
    """
    from motor_ai_sim import thermal_duty_cycle as tdc

    f = _field(client, frame="open", open_air_speed_mps=WASH)
    net = tdc.network_from_steady(f, t_ambient_c=30.0, geometry=GEO_30MM,
                                  surface_fit=True)
    assert net.G.get("r_gap_flow", 0.0) > 0.0
    assert net.G.get("s_gap_flow", 0.0) > 0.0
    assert net.film_kind.get("rotor_ends") == "forced"
    assert net.film_kind.get("magnet_ends") == "forced"
    # a forced film is HELD, not re-evaluated: same G at any wall temperature
    g_cold = tdc.still_air_G(50.0, net, "magnet_ends")
    g_hot = tdc.still_air_G(250.0, net, "magnet_ends")
    assert g_cold == pytest.approx(g_hot, rel=1e-9)

    # …and the flows the integrator builds carry them on the right nodes
    state = {n: 100.0 for n in net.active_nodes}
    flows = tdc._flows(state, net)
    assert flows["rotor_gap_flow"] > 0.0
    assert flows["stator_gap_flow"] > 0.0
    assert tdc._FLOW_NODE["rotor_gap_flow"] == "rotor"
    assert tdc._FLOW_NODE["stator_gap_flow"] == "stator"
    assert "rotor_gap_flow" in tdc.ROTOR_SIDE_FLOWS
    assert "stator_gap_flow" in tdc.STATOR_SIDE_FLOWS


def test_a_housed_map_gives_the_network_no_gap_stream(client, em_run):
    """…and a machine with a housing keeps the network it always had."""
    from motor_ai_sim import thermal_duty_cycle as tdc

    f = _field(client, frame="housed")
    net = tdc.network_from_steady(f, t_ambient_c=30.0, geometry=GEO_30MM,
                                  surface_fit=True)
    assert net.G.get("r_gap_flow", 0.0) == 0.0
    assert net.G.get("s_gap_flow", 0.0) == 0.0


# ---------------------------------------------------------------------------
# the correlations themselves — no FEM, no app
# ---------------------------------------------------------------------------

def test_the_two_rotor_films_are_the_larger_of_the_two_mechanisms():
    """A spinning rotor in still air is a disc; a still rotor in a stream is a
    plate; the model takes whichever is larger and never their sum."""
    from motor_ai_sim.simulation import cooling_models as cm

    spin = cm.rotor_end_faces_open(
        air_speed_mps=0.0, rpm=20000.0, t_wall_c=150.0, t_ambient_c=30.0,
        area_m2=5e-4, radius_m=0.015, n_faces=2)
    blow = cm.rotor_end_faces_open(
        air_speed_mps=40.0, rpm=0.0, t_wall_c=150.0, t_ambient_c=30.0,
        area_m2=5e-4, radius_m=0.015, n_faces=2)
    both = cm.rotor_end_faces_open(
        air_speed_mps=40.0, rpm=20000.0, t_wall_c=150.0, t_ambient_c=30.0,
        area_m2=5e-4, radius_m=0.015, n_faces=2)
    assert spin["h_conv"] > cm.NATURAL_CONVECTION_H
    assert blow["h_conv"] > cm.NATURAL_CONVECTION_H
    assert both["h_conv"] == pytest.approx(max(spin["h_conv"], blow["h_conv"]))
    assert both["h_conv"] < spin["h_conv"] + blow["h_conv"]
    assert both["h_rad"] == 0.0
    # a machine that is neither spinning nor in a stream is at the floor, not 0
    dead = cm.rotor_end_faces_open(
        air_speed_mps=0.0, rpm=0.0, t_wall_c=150.0, t_ambient_c=30.0,
        area_m2=5e-4, radius_m=0.015, n_faces=2)
    assert dead["h_conv"] == pytest.approx(cm.NATURAL_CONVECTION_H)


def test_a_longer_gap_passes_less_air():
    """The pressure balance, in one number: friction goes as L/D_h, so the same
    wash on a longer stack gets less air through the same clearance."""
    from motor_ai_sim.simulation import cooling_models as cm

    short = cm.gap_axial_flow(air_speed_mps=20.0, r_rotor_m=0.0119,
                              r_bore_m=0.0121, length_m=0.012,
                              t_air_c=80.0, t_ambient_c=30.0)
    long = cm.gap_axial_flow(air_speed_mps=20.0, r_rotor_m=0.0119,
                             r_bore_m=0.0121, length_m=0.060,
                             t_air_c=80.0, t_ambient_c=30.0)
    assert short["gap_speed_mps"] > long["gap_speed_mps"]
    assert short["m_dot_kg_s"] > long["m_dot_kg_s"]
    # and both are well under the free stream — a slot is not a free jet
    assert short["speed_fraction"] < 1.0
    # a wider clearance passes more, for the same reason
    wide = cm.gap_axial_flow(air_speed_mps=20.0, r_rotor_m=0.0115,
                             r_bore_m=0.0121, length_m=0.012,
                             t_air_c=80.0, t_ambient_c=30.0)
    assert wide["gap_speed_mps"] > short["gap_speed_mps"]
    # no wash, no duct
    still = cm.gap_axial_flow(air_speed_mps=0.0, r_rotor_m=0.0119,
                              r_bore_m=0.0121, length_m=0.012,
                              t_air_c=80.0, t_ambient_c=30.0)
    assert still["G_W_per_K"] == 0.0
    assert still["mode"] == "off"


def test_the_gap_conductance_is_twice_the_enthalpy_flow():
    """``G = 2·ṁ·cp`` — the linear-rise lumping, stated as an identity so a
    later refactor cannot quietly halve the machine's gap cooling."""
    from motor_ai_sim.simulation import cooling_models as cm

    r = cm.gap_axial_flow(air_speed_mps=20.0, r_rotor_m=0.0119,
                          r_bore_m=0.0121, length_m=0.012,
                          t_air_c=80.0, t_ambient_c=30.0)
    props = cm.air_properties(80.0)
    assert r["G_W_per_K"] == pytest.approx(2.0 * r["m_dot_kg_s"] * props.cp,
                                           rel=1e-9)


def test_the_calibration_constants_are_the_physics_and_say_so():
    """They are 1.0 and they are NOT measurements (2026-09-21: the owner has
    thermal photographs and has given no numbers yet)."""
    from motor_ai_sim.simulation import cooling_models as cm

    assert cm.OPEN_GAP_FLOW_CALIBRATION == 1.0
    assert cm.OPEN_ROTOR_FACE_H_CALIBRATION == 1.0
    for doc in (cm.__doc__ or "", ):
        pass
    import inspect
    src = inspect.getsource(cm)
    assert "AWAITS CALIBRATION" in src or "AWAITING CALIBRATION" in src
