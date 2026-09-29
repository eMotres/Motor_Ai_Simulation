"""The OPEN frame — a machine with no housing at all (``frame=open``).

User, 2026-09-09, on the 40 mm "CIANO14 40 new": *нет корпуса* — the stator
tooth blocks with their coils hang between two end plates on standoff pins, and
the coil END WINDINGS plus the axial CHANNELS between neighbouring coils sit
directly in the propeller wash (10-12 m/s).  Every thermal answer this router
gave before today assumes the opposite (2026-09-07: *"торцы и лобовые части —
только для вала, всё остальное вращается внутри мотора"* — a closed housing,
end turns with nowhere to send their heat), and on this machine that omission is
not conservative: k_end is 1.759 on the L12, i.e. 76 % of the copper LENGTH is
end turn, and all of its loss is currently deposited in the in-slot copper with
no way out except through the iron.

So the frame is a MODE, and this module pins the three things that makes it:

  (a) HOUSED IS UNCHANGED, bit for bit.  A request that does not ask for the
      open frame keys the cache on the tuple this router has always built and
      remembers the input fields it has always remembered — no new keys.  That
      is not politeness: a key that moved would miss every map already cached,
      and a `/last` entry that grew a field would show the tab a control it does
      not have;
  (b) OPEN adds two lumped conductances and both are REAL — G > 0 at rest (the
      still-air floor), larger in the wash, the reported A_ew is the hand
      formula on this fixture's own geometry, the budget still closes with both
      counted as outflows, and the winding comes out COLDER than the same
      machine housed at the same losses;
  (c) an open frame on a cross-section with no winding is REFUSED BY NAME, the
      same way the shaft path is refused when there is no shaft: a client that
      asked for the end turns and got a map without them would read the winding
      temperature as if the wash were cooling copper that is not in the model.

Runs on the 30 mm 12s/14p fixture through a per-request ``?geo=`` override — the
same machine, the same one Electromagnetic run and the same coarse mesh as
tests/test_thermal_routes.py, because what is claimed here is a CONTRACT and a
converged 200 mm machine would buy nothing at many times the wall clock.
"""
from __future__ import annotations

import json
import math
import pathlib
import tempfile
import time

import pytest
from fastapi import HTTPException

from tests.test_thermal_routes import GEO_30MM, store_em_run

# The same cheap honest cycle test_thermal_routes.py uses: four frames over one
# electrical period on the machine's natural half-wedge.  The current is what
# makes copper a real share of the loss — the whole point of the open frame is
# what happens to the WINDING.
FAST = {
    "n_steps_per_period": 4, "n_periods": 1.0,
    "mesh_size_mm": 2.5, "min_size_mm": 0.6, "n_sectors": 2,
    "I_phase_rms": 60.0, "rpm": 3000.0, "gamma_deg": 0.0,
    "coil_temp_c": 120.0,
}
AIR = {"cooling_mode": "air", "ambient_temp": 30.0, "air_speed_mps": 5.0}
RUN_ID = "2026-09-09T09:00:00"
#: The wash the user quotes for the propeller stream, m/s.
WASH = 10.0


def _geo() -> str:
    return json.dumps(GEO_30MM)


def _req(**over) -> dict:
    p = {**FAST, **AIR, "geo": _geo()}
    p.update(over)
    return p


# ---------------------------------------------------------------------------
# The Electromagnetic run every map below is answered from
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def em_run():
    """One transient, solved and parked the way a Run parks one.

    ``store_em_run`` is imported rather than re-spelled: that handshake IS the
    seam between the Electromagnetic and Thermal tabs, and a second copy here
    would test this module's idea of a run instead of the run.  The snapshot
    store and its pickle are redirected for the module —
    ``config/.last_transient_field.pkl`` is the user's own last run.
    """
    from motor_ai_sim.routes import simulation as sim

    mp = pytest.MonkeyPatch()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="thermal_open_frame_"))
    mp.setattr(sim, "_transient_field_store_path", lambda: str(tmp / ".snap.pkl"))
    saved = dict(sim._transient_field_snap)
    sim._transient_field_snap.clear()

    info = store_em_run(GEO_30MM, run_id=RUN_ID, phys=FAST)
    time.sleep(0.3)          # the persist is a daemon thread — let it land

    yield info

    sim._transient_field_snap.clear()
    sim._transient_field_snap.update(saved)
    mp.undo()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Nothing this module solves may land in ``config/``, and no test may be
    answered from another test's cache.

    Same fixture as tests/test_thermal_routes.py plus the field cache: half of
    what is asserted below is about the cache KEY, so a leftover entry would be
    the one thing that could make a broken key look right.
    """
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
# (a) housed is unchanged — no new keys anywhere
# ---------------------------------------------------------------------------

_KEY_KW = dict(
    ambient_temp=30.0, h_conv=50.0, slot_k=0.0, rpm=3000.0, gamma_deg=0.0,
    I_phase_rms=60.0, n_steps_per_period=4, n_periods=1.0, mesh_size_mm=2.5,
    min_size_mm=0.6, outer_air_factor=1.3, n_sectors=2, coil_temp_c=120.0,
    component_mesh="", cooling_mode="air", air_speed_mps=5.0, fluid="water",
    fluid_temp_in_c=25.0, flow_lpm=0.0)


def test_a_housed_request_keys_the_cache_exactly_as_it_always_did():
    """No ``frame``, no ``open_air_speed_mps``, no tuple growth.

    The rule ``thermal_settings`` states for the request — "a parameter the
    chosen mode does not use is not sent" — applied to the KEY.  A housed
    request must produce the tuple this function built before the frame existed,
    or the first request after this change misses every map in the cache and the
    user pays a solve for a field this process already has.
    """
    from motor_ai_sim.routes.thermal import _field_cache_key

    base = _field_cache_key(None, {}, **_KEY_KW)
    assert _field_cache_key(None, {}, frame="housed", **_KEY_KW) == base
    # …and an explicit 0 m/s beside `housed` changes nothing either: the value is
    # not read, so it must not split the cache.
    assert _field_cache_key(None, {}, frame="housed", open_air_speed_mps=12.0,
                            **_KEY_KW) == base
    assert "frame" not in base and "open" not in base


def test_an_open_request_is_a_different_answer_and_keys_as_one():
    """Two heat paths the housed map does not have — never one cache entry.

    And the SPEED is part of it: the two conductances are both proportional to
    a film computed from it, so a 0 m/s open machine and a 10 m/s one are two
    answers as surely as a jacket and a fan are.
    """
    from motor_ai_sim.routes.thermal import _field_cache_key

    base = _field_cache_key(None, {}, **_KEY_KW)
    op0 = _field_cache_key(None, {}, frame="open", open_air_speed_mps=0.0,
                           **_KEY_KW)
    op10 = _field_cache_key(None, {}, frame="open", open_air_speed_mps=10.0,
                            **_KEY_KW)
    assert op0 != base and op10 != base and op0 != op10
    # The spelling is normalised once, so a panel that sends "Open" cannot key
    # one answer and be solved another.
    assert _field_cache_key(None, {}, frame="OPEN", open_air_speed_mps=10.0,
                            **_KEY_KW) == op10


def test_a_housed_solve_remembers_exactly_the_fields_it_always_did(client, em_run):
    """``/last`` restores the tab's inputs — and a housed answer has no frame.

    An entry stored before this change and one stored after it have to read
    identically, or re-entering the Thermal tab after an upgrade would show a
    control that was never set.
    """
    from motor_ai_sim.routes import thermal as th

    _field(client)
    params = th._LAST["field"]["params"]
    assert "frame" not in params
    assert "open_air_speed_mps" not in params


def test_an_open_solve_remembers_the_frame_and_the_speed(client, em_run):
    f = _field(client, frame="open", open_air_speed_mps=WASH)
    assert f["cooling"]["frame"] == "open"

    from motor_ai_sim.routes import thermal as th
    params = th._LAST["field"]["params"]
    assert params["frame"] == "open"
    assert params["open_air_speed_mps"] == pytest.approx(WASH)


def test_a_housed_map_says_so_instead_of_leaving_the_blocks_out(client, em_run):
    """"There is a housing" is an ANSWER, not a missing key.

    Same rule the bore's ``mode: none`` and the shaft's ``mode: off`` follow: a
    client must be able to tell "this path is off" from "something went wrong
    and the block is gone".
    """
    c = _field(client)["cooling"]
    assert c["frame"] == "housed"
    assert c["end_windings"]["mode"] == "housed"
    assert c["slot_channels"]["mode"] == "housed"
    assert c["end_windings"]["G_W_per_K"] == 0.0
    assert c["slot_channels"]["G_W_per_K"] == 0.0
    assert c["heat_budget"]["end_windings_W"] == 0.0
    assert c["heat_budget"]["slot_channels_W"] == 0.0
    # …and the note says what to do about it rather than only what is missing.
    assert "frame=open" in c["end_windings"]["note"]


# ---------------------------------------------------------------------------
# (b) the open frame's two paths
# ---------------------------------------------------------------------------

def test_still_air_is_the_floor_not_zero(client, em_run):
    """0 m/s is a machine standing in a room, not a machine in a vacuum.

    An open frame with nothing blowing still loses heat off its end turns and
    out of its slots by natural convection — the same floor every other film in
    ``cooling_models`` has.  Zero here would say a stopped propeller insulates
    the winding, and it is also what a forced-convection correlation evaluated
    at v = 0 returns if nobody floors it.
    """
    from motor_ai_sim.simulation.cooling_models import NATURAL_CONVECTION_H

    c = _field(client, frame="open", open_air_speed_mps=0.0,
               cooling_mode="manual", h_conv=50.0)["cooling"]
    ew, ch = c["end_windings"], c["slot_channels"]
    # `cooling_mode=manual` on purpose: with the outer surface in air a 0 would
    # be filled in from the housing's own speed (which is the useful default and
    # is tested below), and that is not what is being pinned here.
    assert ew["air_speed_mps"] == 0.0 and ch["air_speed_mps"] == 0.0
    assert ew["h_conv"] == pytest.approx(NATURAL_CONVECTION_H)
    assert ch["h_conv"] == pytest.approx(NATURAL_CONVECTION_H)
    assert ew["regime"] == "at rest" and ch["regime"] == "at rest"
    assert ew["G_W_per_K"] > 0.0 and ch["G_W_per_K"] > 0.0
    # …and small: the floor is 7 W/m²K on a few square centimetres.
    assert ew["G_W_per_K"] < 0.05 and ch["G_W_per_K"] < 0.05


def test_the_wash_is_forced_convection_and_beats_still_air(client, em_run):
    """Both conductances rise with the air speed, and say which regime they are in."""
    calm = _field(client, frame="open", open_air_speed_mps=0.0,
                  cooling_mode="manual", h_conv=50.0)["cooling"]
    wash = _field(client, frame="open", open_air_speed_mps=WASH,
                  cooling_mode="manual", h_conv=50.0)["cooling"]

    assert wash["end_windings"]["G_W_per_K"] > calm["end_windings"]["G_W_per_K"]
    assert wash["slot_channels"]["G_W_per_K"] > calm["slot_channels"]["G_W_per_K"]
    assert wash["end_windings"]["regime"] == "forced"
    assert wash["end_windings"]["re"] > 1.0 and wash["end_windings"]["nu"] > 0.0
    assert wash["slot_channels"]["re"] > 0.0
    assert wash["slot_channels"]["regime"] in ("laminar", "transitional",
                                               "turbulent")
    # Monotone, not just bigger at one point: a sign error in either correlation
    # shows up as a non-monotone ladder long before it shows up as a wrong tile.
    gs = []
    for v in (0.0, 2.0, 5.0, 10.0, 25.0):
        c = _field(client, frame="open", open_air_speed_mps=v,
                   cooling_mode="manual", h_conv=50.0)["cooling"]
        gs.append((c["end_windings"]["G_W_per_K"],
                   c["slot_channels"]["G_W_per_K"]))
    assert [g[0] for g in gs] == sorted(g[0] for g in gs), gs
    assert [g[1] for g in gs] == sorted(g[1] for g in gs), gs


def test_a_zero_open_speed_falls_back_to_the_housing_air(client, em_run):
    """It is the SAME wash.

    On this machine the tooth backs and the end turns are millimetres apart in
    one airstream, so making the user type the number twice is how the two end
    up disagreeing.  ``open_air_speed_mps = 0`` therefore means "whatever is
    blowing on the housing" when the outer surface is in air — and plain still
    air when it is not, because a water jacket says nothing about the room.
    """
    c = _field(client, frame="open", open_air_speed_mps=0.0)["cooling"]
    assert c["end_windings"]["air_speed_mps"] == pytest.approx(AIR["air_speed_mps"])
    assert "housing" in c["end_windings"]["air_speed_source"]
    # …and an explicit speed always wins over the housing's.
    c2 = _field(client, frame="open", open_air_speed_mps=WASH)["cooling"]
    assert c2["end_windings"]["air_speed_mps"] == pytest.approx(WASH)
    assert c2["end_windings"]["air_speed_source"] == "given"


def test_the_end_winding_area_is_the_hand_formula(client, em_run):
    """A_ew, from the geometry, by hand.

    The whole path hangs off this number, so it is computed here from the SAME
    geometry dict the request carries rather than read back from the payload and
    admired:

        bundle thickness = num_wires_per_slot · (wire_height + wire_spacing_y)
        bundle width     = the wire COLUMN (winding_footprint_mm; = wire_width
                           on an unsplit machine)
        exposed perimeter= 2·thickness + width   (the tooth-facing face is not
                           in the wash — the documented judgement call)
        ℓ_end            = (k_end − 1)·L_stack/2   per side
        A_ew             = n_slots · 2 sides · perimeter · ℓ_end

    ``k_end`` is NOT recomputed here: it is read off the answer, and separately
    checked against the estimator the solver itself uses for ``auto`` — the
    point being that the thermal model bills the end turns at the SAME length
    the copper loss was billed at, whatever that was.
    """
    from motor_ai_sim.masses import end_winding_factor as _ewf
    from motor_ai_sim.simulation.geometry_2d import (
        merge_geo_override as _mgo, params_from_config as _pfc)
    from motor_ai_sim.config import get_config

    ew = _field(client, frame="open",
                open_air_speed_mps=WASH)["cooling"]["end_windings"]

    g = GEO_30MM
    n_slots = int(g["num_seg"] * g["num_slots_per_segment"])
    t_mm = g["num_wires_per_slot"] * (g["wire_height"] + g["wire_spacing_y"])
    w_mm = g["wire_width"]                       # wire_split = 1 on this fixture
    per_mm = 2.0 * t_mm + w_mm
    L_m = g["motor_length"] * 1e-3
    k_end = ew["k_end"]
    ell_m = (k_end - 1.0) * L_m / 2.0
    a_hand = n_slots * 2 * (per_mm * 1e-3) * ell_m

    assert ew["n_coils"] == n_slots
    assert ew["n_sides"] == 2
    assert ew["bundle_thickness_mm"] == pytest.approx(t_mm, rel=1e-6)
    assert ew["bundle_width_mm"] == pytest.approx(w_mm, rel=1e-6)
    assert ew["perimeter_mm"] == pytest.approx(per_mm, rel=1e-6)
    assert ew["end_turn_length_mm"] == pytest.approx(ell_m * 1e3, rel=1e-4)
    # `rel=1e-3` and not `1e-6`: the payload quotes `k_end` to four decimals,
    # so a formula rebuilt from the PRINTED k reproduces ℓ_end — and therefore
    # the area — to about 1e-4.  That is the payload's own resolution, not slack
    # in the model; tightening it would only pin the rounding.
    assert ew["area_m2"] == pytest.approx(a_hand, rel=1e-3)
    # G = h·A exactly — copper's fin efficiency is taken as 1 and SAYS so.
    assert ew["fin_efficiency"] == 1.0
    assert ew["G_W_per_K"] == pytest.approx(ew["h_conv"] * a_hand, rel=1e-3)

    # …and the k_end it used is the machine's own, not a constant: on this
    # fixture the run was made on auto, so it must equal the geometry estimator
    # the solver calls for `end_winding_factor = 0`.
    geo_ov = json.loads(_geo())
    k_auto = _ewf(_pfc(geo_override=geo_ov),
                  _mgo(dict(get_config().get("geometry", {})), geo_ov))
    assert k_end == pytest.approx(k_auto, rel=1e-3)
    assert k_end > 1.0
    assert ew["k_end_source"]


def test_the_slot_channel_is_measured_on_the_mesh(client, em_run):
    """The duct's free area is what is LEFT of the slot, so it is measured.

    Not derived from a geometry field: nobody types the area a slot has after
    the wires are in it.  What is pinned is that the measurement is sane —
    positive, a hydraulic diameter that is 4A/P, a wetted perimeter well inside
    the slot's own circumference, and elements actually carrying the sink.
    """
    ch = _field(client, frame="open",
                open_air_speed_mps=WASH)["cooling"]["slot_channels"]

    n_slots = int(GEO_30MM["num_seg"] * GEO_30MM["num_slots_per_segment"])
    assert ch["n_channels"] == n_slots
    assert ch["cross_section_mm2"] > 0.0
    assert ch["wetted_perimeter_mm"] > 0.0
    assert ch["hydraulic_diameter_mm"] == pytest.approx(
        4.0 * ch["cross_section_mm2"] / ch["wetted_perimeter_mm"], rel=1e-3)
    # Per slot, the wetted perimeter has to be the same order as the slot's own
    # outline — a few millimetres on a 30 mm machine.  An order out either way
    # would mean the edge walk counted the wedge cut, or counted nothing.
    assert 1.0 < ch["wetted_perimeter_per_slot_mm"] < 60.0
    # G = h · P_wet · L_stack, exactly.
    assert ch["area_m2"] == pytest.approx(
        ch["wetted_perimeter_mm"] * 1e-3 * GEO_30MM["motor_length"] * 1e-3,
        rel=1e-3)
    assert ch["G_W_per_K"] == pytest.approx(ch["h_conv"] * ch["area_m2"],
                                            rel=1e-3)
    assert ch["n_elements"] > 0


def test_the_budget_still_closes_with_both_new_sinks(client, em_run):
    """Every watt that leaves is on a named line, and they add up.

    The one thing a lumped sink can silently get wrong on a symmetry wedge is
    the factor of N, and the only defence is that the budget closes in MACHINE
    watts with the sink counted as an outflow — plus the identity
    ``P = G·(T_mean − T_sink)``, whose both sides are in the payload.
    """
    f = _field(client, frame="open", open_air_speed_mps=WASH)
    b = f["cooling"]["heat_budget"]
    ew = f["cooling"]["end_windings"]
    ch = f["cooling"]["slot_channels"]

    assert b["frame"] == "open"
    assert b["losses_W"] > 0
    assert b["residual_pct"] < 2.0, b
    assert b["end_windings_W"] > 0.0
    assert b["slot_channels_W"] > 0.0
    # Since 2026-09-21 the open frame has two more outflow lines — the rotor's
    # axial end faces in the wash and the ventilated gap — and they are part of
    # the same identity: EVERY watt that leaves is on a named line.
    assert (b["housing_W"] + b["bore_W"] + b["shaft_ends_W"]
            + b["end_windings_W"] + b["slot_channels_W"]
            + b["end_faces_W"] + b["gap_flow_W"]
            == pytest.approx(b["losses_W"], rel=0.02, abs=0.02))
    # G·ΔT in machine watts — the wedge bookkeeping, made checkable.
    assert ew["t_winding_mean_c"] is not None
    assert b["end_windings_W"] == pytest.approx(
        ew["G_W_per_K"] * (ew["t_winding_mean_c"] - ew["t_sink_c"]),
        rel=0.02, abs=0.005)
    assert ch["t_air_mean_c"] is not None
    assert b["slot_channels_W"] == pytest.approx(
        ch["G_W_per_K"] * (ch["t_air_mean_c"] - ch["t_sink_c"]),
        rel=0.02, abs=0.005)


def test_the_open_frame_cools_the_winding(client, em_run):
    """The claim the whole feature is for.

    Same machine, same losses, same outer film — the only difference is that the
    end turns and the slot channels are in the airflow.  The winding has to come
    out COLDER, and the watts have to leave through the two new paths rather
    than appear from nowhere.
    """
    housed = _field(client)
    openf = _field(client, frame="open", open_air_speed_mps=WASH)

    assert (openf["components"]["winding"]["max"]
            < housed["components"]["winding"]["max"])
    assert (openf["components"]["winding"]["avg"]
            < housed["components"]["winding"]["avg"])
    assert openf["T_max"] <= housed["T_max"]
    # …and the housing gives up exactly what the new paths took: the same losses
    # leave through more doors.
    bh, bo = housed["cooling"]["heat_budget"], openf["cooling"]["heat_budget"]
    assert bo["housing_W"] < bh["housing_W"]
    assert math.isclose(bh["losses_W"], bo["losses_W"], rel_tol=0.02, abs_tol=0.02)


# ---------------------------------------------------------------------------
# (c) refusals — by name, never solved as something else
# ---------------------------------------------------------------------------

def test_an_unknown_frame_is_refused_by_name(client, em_run):
    """"closed" is not "housed", and guessing which one it meant is how a typo
    becomes a result."""
    r = client.get("/api/thermal/field", params=_req(frame="closed"))
    assert r.status_code == 422, r.text[:400]
    d = r.json()["detail"]
    assert [p["field"] for p in d["invalid_parameters"]] == ["frame"]
    assert "housed" in d["invalid_parameters"][0]["message"]


def test_a_negative_open_air_speed_is_refused(client, em_run):
    """A negative speed is not a slower fan.  FastAPI's own ``ge=0`` catches it
    on the route; the by-name refusal below is what a direct caller
    (``modules.solvers``, the coupled orchestrator) gets."""
    from motor_ai_sim.routes.thermal import _validate_field_params

    r = client.get("/api/thermal/field", params=_req(open_air_speed_mps=-1.0))
    assert r.status_code == 422, r.text[:400]

    with pytest.raises(HTTPException) as exc:
        _validate_field_params(cooling_mode="air", ambient_temp=30.0,
                               h_conv=50.0, air_speed_mps=5.0, fluid="water",
                               fluid_temp_in_c=25.0, flow_lpm=0.0,
                               frame="open", open_air_speed_mps=-3.0)
    d = exc.value.detail
    assert [p["field"] for p in d["invalid_parameters"]] == ["open_air_speed_mps"]


def test_an_open_frame_without_a_winding_in_the_mesh_is_refused(client, em_run):
    """The end turns are the point — a cross-section with no copper cannot have
    them.

    Built by taking the run's OWN loss map and re-tagging the winding elements
    as stator iron, which is exactly the mesh a machine with the windings part
    excluded produces.  Injected through ``_em_map`` so nothing is solved
    electromagnetically and nothing touches the cache — the refusal has to come
    from the frame check and from nowhere else.
    """
    from motor_ai_sim.routes import thermal as th

    cap: dict = {}
    th.solve_thermal_field(_em_capture=cap, geo=_geo(), **{
        **{k: v for k, v in FAST.items()}, **AIR})
    em = dict(cap["em"])
    em["domain_per_tri"] = [1 if int(t) == 2 else int(t)
                            for t in em["domain_per_tri"]]

    with pytest.raises(HTTPException) as exc:
        th.solve_thermal_field(
            _em_map=em, _em_loss_source={"kind": "test", "note": "retagged"},
            geo=_geo(), frame="open", open_air_speed_mps=WASH,
            **{**{k: v for k, v in FAST.items()}, **AIR})
    assert exc.value.status_code == 422
    d = exc.value.detail
    assert [p["field"] for p in d["invalid_parameters"]] == ["frame"]
    assert "winding" in d["error"]


# ---------------------------------------------------------------------------
# the panel mirror — one translation of the tab's fields, not two
# ---------------------------------------------------------------------------

def test_the_frame_is_only_sent_when_the_machine_is_open():
    """``thermal_settings.cooling_fields`` mirrors ``thermalStore.coolingFields``.

    The rule that makes it more than a rename: a parameter the chosen mode does
    not use is not sent, because the solver keys its cache on it.  A ``frame``
    of ``housed`` beside an ``open_air_speed_mps`` nothing reads is exactly the
    cache-splitting parameter that rule is about.
    """
    from motor_ai_sim.thermal_settings import cooling_fields

    housed = cooling_fields({"coolMode": "air", "airSpeed": "10",
                             "boreMode": "none"})
    assert "frame" not in housed and "open_air_speed_mps" not in housed
    # …and an openAirSpeed left over from a machine the user switched back to
    # housed must not be sent either.
    still_housed = cooling_fields({"coolMode": "air", "airSpeed": "10",
                                   "boreMode": "none", "frame": "housed",
                                   "openAirSpeed": "12"})
    assert "frame" not in still_housed
    assert "open_air_speed_mps" not in still_housed

    op = cooling_fields({"coolMode": "air", "airSpeed": "10",
                         "boreMode": "none", "frame": "open",
                         "openAirSpeed": "11"})
    assert op["frame"] == "open"
    assert op["open_air_speed_mps"] == pytest.approx(11.0)
    # 0 IS meaningful here (take the housing's air), so it rides with the frame
    # rather than being gated on being > 0 the way the shaft length is.
    z = cooling_fields({"coolMode": "air", "airSpeed": "10", "boreMode": "none",
                        "frame": "open"})
    assert z["frame"] == "open" and z["open_air_speed_mps"] == 0.0
