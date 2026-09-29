"""The duty-cycle CONTRACT — POST /api/thermal/duty_cycle, GET /duty_cycle/last.

Every thermal answer this router gave before 2026-09-14 was a STEADY one: "how
hot does it get if this point never ends".  The question the user has about the
Ø85 robot joint is a different one — "how long may it pull 46 A, and at what duty
may it repeat that for ever" — and this route answers it by fitting a lumped
network to ONE converged 2-D map and integrating the cycle on it.

What is claimed here:

  (a) THE REFUSALS, all of them by NAME.  A duty cycle that quietly became a
      different cycle, or an answer of "186 °C" for a machine heading to 600 °C,
      is the whole class of bug this feature could ship; so an unknown duty, a
      duty with no run, a locked rotor, a mixed-speed cycle, a machine with no
      door for its heat and a cycle with no periodic state are each a 422 with a
      stable ``error_code`` and an English sentence saying what to change.  None
      of these costs a solve: the calibration map is mocked;
  (b) THE CALIBRATION MAP IS REUSED when the Thermal tab already has this duty's
      map at these boundary conditions, and re-solved when anything about either
      moved — the ``cached`` flag says which;
  (c) THE HAPPY PATH on the machine the feature was written for
      (``CIANO28 85 20SW1200 / L13``): the rated duty as calibration, the peak
      duty on an S3 25 % / 60 s cycle, in the robotics boundary conditions the
      user named (still air at 40 °C, ε 0.9, an open bore, the axial end faces,
      bolted to a 40 °C arm through 2 W/K).  ONE real Electromagnetic run and
      ONE real conduction solve for the module; everything after them is
      arithmetic.

NOTHING HERE TOUCHES ``config/``.  The Electromagnetic snapshot store, the
thermal loss-map pickle, the last-result pickle, the per-duty result store and
the DIE DIRECTORY are all redirected into the module's own tmp dir — those five
files are the user's own catalogue and last solves — and the materials are pinned
per request through ``?mat=`` so the answer does not move with the shared
config's assignment.
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import time

import pytest

from tests.test_thermal_capacities import L13_MASS_ROWS
from tests.test_thermal_routes import store_em_run

DIE = "TESTDIE 85"
CFG = "L13"
RATED = "rated 120C wire 80C NdFeB"
PEAK = "peak 200C wire 120C NdFeB"
UNSOLVED = "draft, never run"
FAST_DUTY = "fast 3000 rpm"
HOLD = "hold 0 rpm"

#: ``config/dies/CIANO28 85 20SW1200/{die,L13}.yaml``'s overrides — the machine
#: the user named, as a per-request ``?geo=`` override so nothing on disk is read.
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
GEO_JSON = json.dumps(L13_GEO)

MATERIALS = {"assignment": {"magnet": "F52SH_120C", "stator_core": "20SW1200",
                            "rotor_core": "20SW1200"}}
MAT_JSON = json.dumps(MATERIALS)

#: The L13's two solved duties, verbatim from the die file (2026-09-14).
RATED_SUMMARY = {
    "mass_components": L13_MASS_ROWS, "part_states": {"shaft": "reference"},
    "P_stranded_W": 59.8, "P_core_W": 2.6, "P_solid_W": 1.3,
    "P_loss_total_W": 63.7, "coil_temp_C": 120, "rpm": 1000,
    "I_phase_rms_A": 14.71, "gamma_deg": 2, "op_mode": "motor",
    "end_winding_factor": 2.027, "A_copper_slotted_mm2": 453.6,
    "P_core_terms": {
        "stator": {"hysteresis_W": 1.544, "eddy_W": 0.327, "excess_W": 0.568},
        "rotor": {"hysteresis_W": 0.078, "eddy_W": 0.056, "excess_W": 0.041}}}

PEAK_SUMMARY = dict(RATED_SUMMARY, **{
    "P_stranded_W": 676.1, "P_core_W": 2.5, "P_solid_W": 8.2,
    "P_loss_total_W": 686.9, "coil_temp_C": 200, "I_phase_rms_A": 45.96,
    "P_core_terms": {
        "stator": {"hysteresis_W": 1.466, "eddy_W": 0.310, "excess_W": 0.549},
        "rotor": {"hysteresis_W": 0.088, "eddy_W": 0.057, "excess_W": 0.050}}})

#: The cycle the user asked about: 25 % of a 60 s cycle at the peak, resting
#: unpowered, from a 40 °C machine.
S3 = {"kind": "S3", "duty": PEAK, "ed_pct": 25.0, "cycle_s": 60.0,
      "rest_duty": None, "t_start_c": 40.0, "calibration_duty": RATED}

#: The room the joint stands in and where its heat is conducted, in the Thermal
#: panel's own field names (this is what `cooling_fields` maps).  The heat path
#: replaced the 2 W/K mount on 2026-09-26: the stator OD sits in a housing that
#: sheds its heat by still air + radiation.
ROBOT_SETTINGS = {"coolMode": "robotics", "ambientT": "40", "boreMode": "still",
                  "emissivity": "0.9", "endFaces": "still", "endFaceSides": "2",
                  "heatPath": "housing", "maxIter": "6"}

#: The L13's rated point, on the cheapest honest cycle — four frames over one
#: electrical period.  The same settings tests/test_thermal_robotics.py uses.
EM = {"n_steps_per_period": 4, "n_periods": 1.0, "mesh_size_mm": 2.0,
      "min_size_mm": 0.35, "n_sectors": 4, "outer_air_factor": 1.3}
RUN_ID = "2026-09-14T11:30:00"


# ---------------------------------------------------------------------------
# A canned steady map — the refusal tests must not cost a solve
# ---------------------------------------------------------------------------
# The L13's rated point as the robotics mode answers it (2026-09-14): ~62 W out,
# most of it through the bolts, a couple of watts off the housing and the rest
# off the four exposed AXIAL faces.  The numbers are the real machine's order and
# they are never ASSERTED here — what these tests claim is the contract.

def _canned_map(**over) -> dict:
    m = {
        "components": {"winding": {"max": 106.1, "avg": 103.7},
                       "stator": {"max": 97.4, "avg": 94.5},
                       "rotor": {"max": 95.7, "avg": 95.7},
                       "magnet": {"max": 95.7, "avg": 95.6}},
        "cooling": {
            # A heat_path='housing' map: the OD's film is the contact into the
            # housing, so the housing line carries what the 2 W/K mount did.
            "outer": {"mode": "robotics", "h_conv": 6.1, "h_rad": 8.3,
                      "h_total": 14.4, "t_sink_c": 40.0, "area_m2": 0.003471,
                      "emissivity": 0.9, "heat_removed_W": 41.1},
            "heat_path": {"option": "housing", "body": "housing",
                          "rides_node": "stator", "t_body_c": 62.0,
                          "touch_limit_c": 70.0, "binds_touch_limit": False,
                          "heat_to_room_W": 41.1,
                          "contact": {"heat_W": 41.1}, "bearings": None},
            "end_faces": {
                "mode": "still", "sides": 2, "k_end": 2.027,
                "winding": {"area_m2": 0.00540, "char_len_mm": 1.1,
                            "heat_removed_W": 9.5},
                "stator": {"area_m2": 0.00254, "char_len_mm": 50.4,
                           "heat_removed_W": 4.1},
                "rotor": {"area_m2": 0.00189, "char_len_mm": 43.5,
                          "heat_removed_W": 3.1},
                "magnet": {"area_m2": 0.00143, "char_len_mm": 37.8,
                           "heat_removed_W": 2.3}},
            "heat_budget": {"losses_W": 62.0, "housing_W": 41.1, "bore_W": 1.69,
                            "gap_W": -0.47, "shaft_ends_W": 0.0,
                            "mount_W": 0.0, "bearings_W": 0.0,
                            "end_faces_W": 19.0,
                            "coil_W": 55.33, "residual_pct": 0.0},
            "mech_losses": {"P_bearings_W": 0.0, "P_windage_gap_W": 0.0,
                            "shaft_ends_open": False}},
        "P_cu_exact_W": 58.5735522, "P_mag_eddy_W": 0.3,
        "ambient_temp": 40.0, "T_max": 106.1, "P_loss_total_W": 62.0,
        "loss_source": {"kind": "canned"},
        "geometry_fingerprint": "fp-test",
    }
    m.update(over)
    return m


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sandbox():
    """Every store this module could write to, redirected into a tmp dir.

    Module-scoped (and therefore not ``tmp_path``): the Electromagnetic run of
    the happy path is solved once for the whole file and the snapshot it is
    answered from has to outlive the first test.
    """
    import yaml

    from motor_ai_sim import duty_results as dr
    from motor_ai_sim import thermal_settings as ts
    from motor_ai_sim.routes import family as fam
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.routes import thermal as th

    mp = pytest.MonkeyPatch()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="thermal_duty_cycle_"))
    mp.setattr(sim, "_transient_field_store_path", lambda: str(tmp / ".snap.pkl"))
    mp.setattr(th, "_last_store_path", lambda: str(tmp / ".last_thermal.pkl"))
    mp.setattr(th, "_loss_maps_path", lambda: str(tmp / ".loss_maps.pkl"))
    mp.setattr(dr, "store_path", lambda: tmp / ".duty_results.json")
    mp.setattr(th, "_LAST", {}, raising=True)
    mp.setattr(th, "_LAST_LOADED", True, raising=True)
    mp.setattr(th, "_LOSS_MAPS_LOADED", True, raising=True)
    # The panel store is the USER's remembered Thermal tab; every request here
    # states its own `thermal_settings`, and this makes the one that does not
    # deterministic instead of dependent on what the user last typed.
    mp.setattr(ts, "thermal_panel_settings", lambda auth=None: {})

    root = tmp / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump({
        "name": DIE, "locked": False,
        "geometry": {"num_slots": 24, "num_poles": 28, "stator_diameter": 85.0},
    }, sort_keys=False, allow_unicode=True), encoding="utf-8")
    (root / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump({
        "name": CFG, "die": DIE, "role": "motor",
        "materials": {"magnet": "F52SH_120C", "stator_core": "20SW1200",
                      "rotor_core": "20SW1200"},
        "parts": {"shaft": "reference"},
        "duties": [
            {"name": RATED, "mode": "motor", "current_arms": 14.708,
             "rpm": 1000.0, "gamma_deg": 2.0, "summary": RATED_SUMMARY},
            {"name": PEAK, "mode": "motor", "current_arms": 45.962,
             "rpm": 1000.0, "gamma_deg": 2.0, "summary": PEAK_SUMMARY,
             "duty_cycle": dict(S3)},
            {"name": UNSOLVED, "mode": "motor", "current_arms": 20.0,
             "rpm": 1000.0, "gamma_deg": 2.0},
            {"name": FAST_DUTY, "mode": "motor", "current_arms": 14.708,
             "rpm": 3000.0, "gamma_deg": 2.0,
             "summary": dict(RATED_SUMMARY, rpm=3000)},
            {"name": HOLD, "mode": "motor", "current_arms": 45.962,
             "rpm": 0.0, "gamma_deg": 2.0,
             "summary": dict(PEAK_SUMMARY, rpm=0)},
        ],
    }, sort_keys=False, allow_unicode=True), encoding="utf-8")
    mp.setattr(fam, "_DIES_DIR", root)

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
def client(sandbox):
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


@pytest.fixture()
def no_context(sandbox, monkeypatch):
    """No machine loaded in the catalogue — the bare-request case."""
    from motor_ai_sim import duty_results as dr
    from motor_ai_sim.routes import thermal as th
    monkeypatch.setattr(dr, "active_context", lambda: None)
    monkeypatch.setattr(th, "active_context", lambda: None, raising=False)


@pytest.fixture()
def mock_map(sandbox, monkeypatch):
    """The calibration solve, replaced by a canned map.

    Returns the call log, so a test can say not only what the answer was but
    whether a solve was paid for at all.
    """
    from motor_ai_sim.routes import thermal as th

    calls = []

    def _fake(**kw):
        calls.append(kw)
        return _canned_map()

    monkeypatch.setattr(th, "solve_thermal_field", _fake)
    monkeypatch.setattr(th, "_LAST", {}, raising=False)
    return calls


def _post(client, **body) -> "object":
    """One request, with the coarse integration every canned test wants.

    ``ed_curve_step_pct`` 25 rather than the route's own 5: the ED curve is 20
    periodic solves at the default, and what these tests claim is the CONTRACT.
    ``ed_cycle_lengths_s: []`` for the same reason — the ED-vs-cycle-length
    curve is a whole allowable-ED search PER PERIOD, and the section that
    claims it asks for its own two periods.  A body that sends ``None`` (or
    nothing) gets the route's own span.
    """
    return client.post("/api/thermal/duty_cycle",
                       params={"mat": MAT_JSON},
                       json={"die": DIE, "config": CFG, "geo": GEO_JSON,
                             "thermal_settings": dict(ROBOT_SETTINGS),
                             "ed_curve_step_pct": 25.0,
                             "ed_cycle_lengths_s": [],
                             "samples_per_segment": 24,
                             **EM, **body})


def _refusal(r, code: str) -> dict:
    """The 422, checked for its shape and handed back with ``text``.

    ``error`` is the toast line and each ``invalid_parameters`` entry carries the
    whole sentence plus the REMEDY — this router's own convention (``_bad``) —
    so ``text`` is what an engineer actually reads.
    """
    assert r.status_code == 422, r.text[:600]
    d = r.json()["detail"]
    assert d["error_code"] == code, d
    assert d["invalid_parameters"], d
    d["text"] = " ".join([d["error"]] + [str(p.get("message") or "")
                                         for p in d["invalid_parameters"]])
    # an English SENTENCE with a remedy in it, never a bare token: this is what
    # an engineer reads and acts on
    assert len(d["text"]) > 60 and d["text"].rstrip().endswith("."), d["text"]
    return d


# ---------------------------------------------------------------------------
# (a) the refusals — none of these costs a solve
# ---------------------------------------------------------------------------

def test_no_catalogue_context_and_no_names_is_refused(client, mock_map,
                                                      no_context):
    r = client.post("/api/thermal/duty_cycle",
                    json={"thermal_settings": dict(ROBOT_SETTINGS)})
    d = _refusal(r, "duty_cycle_no_context")
    assert "catalogue" in d["text"] or "catalog" in d["text"]
    assert not mock_map, "a refusal must not cost a calibration solve"


def test_an_unknown_duty_is_refused_and_names_what_there_is(client, mock_map):
    d = _refusal(_post(client, duty="no such duty"), "duty_cycle_unknown_duty")
    assert PEAK in d["text"] and RATED in d["text"]
    assert not mock_map


def test_a_duty_with_no_cycle_block_is_refused_by_name(client, mock_map):
    d = _refusal(_post(client, duty=RATED), "duty_cycle_missing")
    assert "S3" in d["text"] and "duty_cycle" in d["text"]
    assert not mock_map


def test_a_calibration_duty_that_was_never_run_is_no_electromagnetic_run(
        client, mock_map):
    d = _refusal(_post(client, duty=PEAK,
                       duty_cycle=dict(S3, calibration_duty=UNSOLVED)),
                 "no_electromagnetic_run")
    assert UNSOLVED in d["text"] and "Electromagnetic" in d["text"]
    assert not mock_map


def test_a_machine_with_no_door_for_its_heat_is_refused(client, mock_map):
    d = _refusal(_post(client, duty=PEAK,
                       thermal_settings={"coolMode": "none", "boreMode": "none",
                                         "mountG": "0", "ambientT": "40"}),
                 "no_thermal_boundary")
    assert "nowhere" in d["text"]
    assert not mock_map


def test_a_locked_rotor_is_refused_by_name(client, mock_map):
    """0 rpm is out of scope for v1: the gap and bore films, the bearings and
    the windage are all fitted at a turning speed."""
    d = _refusal(_post(client, duty=PEAK,
                       duty_cycle={"kind": "S2", "duty": HOLD, "t_on_s": 5.0,
                                   "calibration_duty": RATED}),
                 "duty_cycle_locked_rotor")
    assert "0 rpm" in d["text"] and "Thermal tab" in d["text"]
    assert len(mock_map) == 1, "the map is fitted before the cycle is read"


def test_a_mixed_speed_cycle_is_refused_by_name(client, mock_map):
    d = _refusal(_post(client, duty=PEAK,
                       duty_cycle={"kind": "segments", "calibration_duty": RATED,
                                   "segments": [{"duty": PEAK, "t_s": 5.0},
                                                {"duty": FAST_DUTY, "t_s": 5.0}]}),
                 "duty_cycle_mixed_speed")
    assert "1000" in d["text"] and "3000" in d["text"]


def test_a_malformed_block_is_refused_by_its_own_name(client, mock_map):
    for blk, code in (({"kind": "S4", "duty": PEAK}, "duty_cycle_bad_kind"),
                      ({"kind": "S3", "duty": PEAK, "ed_pct": 0,
                        "cycle_s": 60}, "duty_cycle_bad_ed"),
                      ({"kind": "S2", "duty": PEAK}, "duty_cycle_bad_s2")):
        _refusal(_post(client, duty=PEAK,
                       duty_cycle=dict(blk, calibration_duty=RATED)), code)


def test_a_cycle_with_no_periodic_state_is_refused_with_a_remedy(client,
                                                                 mock_map):
    """100 % duty at the peak point: there is no equilibrium below 400 °C,
    and "186 °C" would be the worst possible answer."""
    d = _refusal(_post(client, duty=PEAK,
                       duty_cycle=dict(S3, ed_pct=100.0),
                       thermal_settings=dict(ROBOT_SETTINGS, heatPath="none")),
                 "duty_cycle_no_periodic_state")
    assert "400" in d["text"] or "rising" in d["text"]
    assert "heat path" in d["text"]                  # what to change


def test_a_map_with_no_components_is_no_steady_thermal_map(client, sandbox,
                                                           monkeypatch):
    from motor_ai_sim.routes import thermal as th
    monkeypatch.setattr(th, "_LAST", {}, raising=False)
    monkeypatch.setattr(th, "solve_thermal_field",
                        lambda **kw: {"T_max": 106.1, "cooling": {}})
    d = _refusal(_post(client, duty=PEAK), "no_steady_thermal_map")
    assert "Thermal tab" in d["text"]


# ---------------------------------------------------------------------------
# (b) the calibration map is reused, or it is not — and the answer says which
# ---------------------------------------------------------------------------

def test_the_remembered_map_is_reused_when_it_is_this_duty_at_these_BCs(
        client, sandbox, monkeypatch):
    """The Thermal tab already solved the rated duty in these boundary
    conditions — the cycle must not pay for it twice."""
    from motor_ai_sim.routes import thermal as th
    from motor_ai_sim.thermal_settings import cooling_fields

    calls = []
    monkeypatch.setattr(th, "solve_thermal_field",
                        lambda **kw: (calls.append(kw), _canned_map())[1])
    params = {"rpm": 1000.0, "I_phase_rms": 14.708, "gamma_deg": 2.0,
              "coil_temp_c": 120.0, **cooling_fields(ROBOT_SETTINGS)}
    monkeypatch.setattr(th, "_LAST",
                        {"field": {"result": _canned_map(), "params": params,
                                   "geometry_fingerprint": None,
                                   "computed_at": "2026-09-14T11:00:00"}},
                        raising=False)
    out = _post(client, duty=PEAK).json()
    assert out["cached"] is True
    assert out["calibration_map"]["reused"] is True
    assert not calls, "a remembered map at these BCs must not be re-solved"

    # …and a map solved with a DIFFERENT heat path is not this machine.
    out2 = _post(client, duty=PEAK,
                 thermal_settings=dict(ROBOT_SETTINGS, heatPath="shaft")).json()
    assert out2["cached"] is False and len(calls) == 1


def test_the_calibration_solve_is_at_the_calibration_dutys_own_point(
        client, mock_map):
    """Not the duty the CYCLE is about: the network is fitted at the rated
    point, and the peak is then an interpolation of a real solve."""
    out = _post(client, duty=PEAK).json()
    assert len(mock_map) == 1
    kw = mock_map[0]
    assert kw["I_phase_rms"] == pytest.approx(14.708)     # rated, not peak
    assert kw["coil_temp_c"] == pytest.approx(120.0)
    assert kw["rpm"] == pytest.approx(1000.0)
    assert kw["cooling_mode"] == "robotics" and kw["bore_mode"] == "still"
    assert kw["heat_path"] == "housing"
    assert "mount_g_w_per_k" not in kw
    assert out["spec"]["calibration_duty"] == RATED
    assert out["network"]["calibration"]["duty"] == RATED


# ---------------------------------------------------------------------------
# (c) the record, the store and /last — on the canned map (no solve)
# ---------------------------------------------------------------------------

def test_the_record_has_the_shape_B3_promises(client, mock_map):
    out = _post(client, duty=PEAK).json()
    for block in ("spec", "network", "cycle", "split", "limits", "point"):
        assert isinstance(out[block], dict) and out[block], block
    for k in ("elapsed_s", "solve_time_s", "cached", "geometry_fingerprint"):
        assert k in out, k

    assert out["spec"]["kind"] == "S3" and out["spec"]["ed_pct"] == 25.0
    assert [s["duty"] for s in out["spec"]["segments"]] == [PEAK, None]
    assert out["spec"]["segments"][0]["t_s"] == 15.0
    assert out["spec"]["segments"][0]["total_W"] > 600.0

    net = out["network"]
    assert net["C_J_per_K"]["winding"] == pytest.approx(41.2, abs=0.1)
    assert net["cp_sources"]["stator"] == "default"      # 20SW1200 has no c_p
    assert net["G_W_per_K"]["s_mount"] == 0.0           # no mount: a housing
    assert net["hot_spot_offset_K"] == pytest.approx(2.4, abs=0.01)
    assert net["areas_m2"]["winding_ends"] == pytest.approx(0.00540)
    assert "measured end-face areas" in net["side_area_basis"]["source"]

    cyc = out["cycle"]
    assert cyc["converged"] is True
    assert len(cyc["t_s"]) <= 400 and len(cyc["T_c"]["winding"]) == len(cyc["t_s"])
    assert cyc["peak_c"]["winding"] > cyc["min_c"]["winding"]
    assert cyc["winding_hot_peak_c"] > cyc["peak_c"]["winding"]
    assert abs(cyc["closure_pct"]) < 0.5

    assert out["limits"]["winding_limit_c"] == 200.0     # the project's class
    assert out["limits"]["magnet_limit_c"] is None       # no card maximum
    assert out["limits"]["s2_time_to_limit_s"] is not None
    assert out["limits"]["ed_requested_pct"] == 25.0


def test_the_network_block_says_how_each_link_was_obtained(client, mock_map):
    """WHICH conductance is a fit and which is a formula (user 2026-09-15).

    The canned L13 map is degenerate on both rotor links — the rotor sits a
    kelvin from the stator with the gap watt pointing the wrong way, and the
    magnets are a tenth of a kelvin from the rotor — so neither can be divided
    out of it.  Neither is merged either: the route hands the network the
    geometry and both links keep the interface's own conductance, with the
    formula and the numbers in the record.
    """
    net = _post(client, duty=PEAK).json()["network"]

    assert net["link_kinds"] == {"w_s": "calibrated", "r_s": "physical",
                                 "m_r": "physical"}
    assert net["merged"] == []
    assert net["active_nodes"] == ["winding", "stator", "rotor", "magnet"]

    r_s, m_r = net["links"]["r_s"], net["links"]["m_r"]
    assert r_s["nodes"] == ["rotor", "stator"] and r_s["G_W_per_K"] > 0.0
    assert net["G_W_per_K"]["r_s"] == pytest.approx(r_s["G_W_per_K"], rel=1e-5)
    assert "k_eff·A/δ + h_rad·A" in r_s["basis"]
    assert "wrong sign" in r_s["why_not_calibrated"]
    assert m_r["G_W_per_K"] == pytest.approx(4.5, abs=0.5)
    assert "k·A_root/(h_mag/2)" in m_r["basis"]
    assert "0.50 K" in m_r["why_not_calibrated"]
    # …and the four nodes really are integrated apart: the magnets are not
    # nailed to the stator's peak any more
    cyc = _post(client, duty=PEAK).json()["cycle"]
    assert cyc["peak_c"]["stator"] > cyc["peak_c"]["magnet"] + 5.0


def test_the_record_is_filed_under_the_duty_and_nothing_else_is(client,
                                                                mock_map,
                                                                sandbox):
    """The route may persist exactly two things: the per-duty record and the
    remembered last.  Not the catalogue yaml, not the duty's thermal row."""
    from motor_ai_sim import duty_results as dr

    before = (sandbox / "dies" / DIE / f"{CFG}.yaml")
    stamp = (before.stat().st_mtime if before.exists() else None)
    _post(client, duty=PEAK)

    entry = dr.get(DIE, CFG)[PEAK]
    assert "duty_cycle" in entry and "thermal" not in entry
    rec = entry["duty_cycle"]
    assert rec["kind"] == "duty_cycle"
    assert rec["cycle"]["converged"] is True
    assert len(rec["cycle"]["t_s"]) <= dr.DUTY_CYCLE_MAX_SAMPLES
    assert rec["point"]["cooling_mode"] == "robotics"
    # the catalogue yaml is the user's and changes only on an explicit Save
    assert (before.stat().st_mtime if before.exists() else None) == stamp


def test_last_answers_before_and_after(client, mock_map, sandbox):
    from motor_ai_sim.routes import thermal as th

    th._LAST.pop("duty_cycle", None)
    out = client.get("/api/thermal/duty_cycle/last").json()
    assert out["has_result"] is False and out["duty_cycle"] is None

    _post(client, duty=PEAK)
    out = client.get("/api/thermal/duty_cycle/last").json()
    assert out["has_result"] is True
    e = out["duty_cycle"]
    assert e["result"]["duty"] == PEAK
    assert e["params"]["duty"] == PEAK and e["params"]["cooling_mode"] == "robotics"
    assert e["computed_at"]
    # …and the steady /last is untouched: a solved CYCLE is not a temperature map
    assert client.get("/api/thermal/last").json()["field"] is None


def test_an_S2_pull_says_there_is_no_periodic_state_and_times_the_class(
        client, mock_map):
    out = _post(client, duty=PEAK,
                duty_cycle={"kind": "S2", "duty": PEAK, "t_on_s": 600.0,
                            "t_start_c": 40.0, "calibration_duty": RATED}).json()
    assert out["cycle"]["converged"] is None
    assert "no periodic state" in out["cycle"]["note"]
    # the two hand bounds: adiabatic copper 9.7 s, perfectly mixed 40 s
    t = out["limits"]["s2_time_to_limit_s"]
    assert 9.7 < t < 40.0, t
    assert out["limits"]["s2_limiting_part"] == "winding"
    assert out["limits"]["ed_allowable_pct"] is None
    assert "S3" in out["limits"]["ed_note"]


# ---------------------------------------------------------------------------
# (c2) THE TOOL FINDS THE REGIME — the contract, on the canned map
# ---------------------------------------------------------------------------
# User 2026-09-15: «с помощью Duty cycle мы можем подобрать такой режим работы
# мотора, чтобы он смог уложиться в температурные лимиты».  So `ed_pct` became
# OPTIONAL on an S3 and the answer carries the regime that was FOUND: the
# allowable ratio at this cycle length, the same over a span of cycle lengths,
# the temperatures at that point, and how long one pull lasts from cold, from
# the rated state and out of the settled cycle.  The request contract did not
# move: a block that states an ED is still graded exactly as before.

#: The same 60 s cycle, with the duty ratio LEFT TO THE TOOL.
S3_FIND = {"kind": "S3", "duty": PEAK, "cycle_s": 60.0, "rest_duty": None,
           "t_start_c": 40.0, "calibration_duty": RATED}


def test_an_S3_with_no_ED_is_answered_with_the_ratio_the_machine_holds(
        client, mock_map):
    """`ed_pct` optional — and the cycle that comes back is the FOUND one."""
    out = _post(client, duty=PEAK, duty_cycle=dict(S3_FIND)).json()
    lim, spec = out["limits"], out["spec"]

    ed = lim["ed_allowable_pct"]
    assert ed is not None and 0.0 < ed < 100.0
    assert lim["ed_found"] is True
    assert lim["ed_requested_pct"] is None      # nothing was asked for
    assert lim["ed_limiting_part"] == "winding"
    # the integrated, drawn and STORED cycle is the one at that ratio — the
    # 50 % seed the profile was built at may appear nowhere
    assert spec["ed_given"] is False
    assert spec["ed_pct"] == pytest.approx(ed, abs=0.01)
    assert spec["segments"][0]["t_s"] == pytest.approx(60.0 * ed / 100.0,
                                                       abs=0.01)
    assert out["cycle"]["winding_hot_peak_c"] <= lim["winding_limit_c"] + 1.0

    at = lim["at_allowable"]
    assert at["winding_hot_peak_c"] == pytest.approx(lim["winding_limit_c"],
                                                     abs=2.0)
    assert set(at["peak_c"]) == {"winding", "stator", "rotor", "magnet"}
    assert at["magnet_peak_c"] == at["peak_c"]["magnet"]


def test_a_stated_ED_is_still_graded_the_way_it_always_was(client, mock_map):
    """BACKWARD COMPATIBLE: the request contract did not move."""
    out = _post(client, duty=PEAK).json()          # the saved 25 % block
    lim = out["limits"]
    assert lim["ed_requested_pct"] == 25.0
    assert lim["ed_found"] is False
    assert out["spec"]["ed_pct"] == 25.0 and out["spec"]["ed_given"] is True
    assert out["spec"]["segments"][0]["t_s"] == 15.0
    assert lim["ed_allowable_pct"] is not None     # …and found beside it


def test_the_allowable_ED_comes_back_over_a_SPAN_of_cycle_lengths(
        client, mock_map, monkeypatch):
    """One ratio at one period hides the trade; the curve is the answer.

    The span is the module's own (`thermal_duty_cycle.ED_CYCLE_LENGTHS_S`), and
    it is monkeypatched to two periods here for the wall clock — each row is a
    whole bisection of its own.
    """
    from motor_ai_sim import thermal_duty_cycle as tdc

    assert tdc.ED_CYCLE_LENGTHS_S == (10.0, 30.0, 60.0, 120.0, 300.0)
    monkeypatch.setattr(tdc, "ED_CYCLE_LENGTHS_S", (30.0, 60.0))
    out = _post(client, duty=PEAK, duty_cycle=dict(S3_FIND),
                ed_cycle_lengths_s=None).json()

    rows = out["limits"]["ed_vs_cycle"]
    assert [r["cycle_s"] for r in rows] == [30.0, 60.0]
    assert rows[0]["ed_allowable_pct"] > rows[1]["ed_allowable_pct"]
    for r in rows:
        assert r["t_on_s"] == pytest.approx(
            r["cycle_s"] * r["ed_allowable_pct"] / 100.0, abs=0.01)
        assert r["limiting_part"] == "winding"
        assert r["winding_hot_peak_c"] == pytest.approx(200.0, abs=3.0)
        assert r["magnet_peak_c"] is not None
    assert out["limits"]["ed_cycle_s"] == 60.0


def test_the_same_pull_is_timed_from_cold_from_rated_and_out_of_the_cycle(
        client, mock_map):
    """Three start states, three answers — and cold is the longest of them."""
    out = _post(client, duty=PEAK, duty_cycle=dict(S3_FIND)).json()
    lim = out["limits"]

    cold = lim["s2_time_to_limit_s"]
    rated = lim["s2_from_rated_s"]
    in_cycle = lim["s2_from_cycle_mean_s"]
    assert cold and rated and in_cycle
    assert cold > rated > in_cycle, (cold, rated, in_cycle)
    assert lim["s2_from_rated_part"] == lim["s2_limiting_part"] == "winding"
    # the rated start state IS the calibration map's own means, not a guess
    assert lim["s2_from_rated_start_c"]["winding"] == pytest.approx(103.7,
                                                                    abs=0.01)
    assert RATED in lim["s2_from_rated_note"]
    # …and the in-cycle start state IS the settled cycle's own mean
    assert lim["s2_from_cycle_mean_start_c"]["winding"] == pytest.approx(
        out["cycle"]["mean_c"]["winding"], abs=0.05)


def test_an_S2_has_no_settled_cycle_to_pull_out_of(client, mock_map):
    """…and says so, rather than reporting a number from another kind."""
    lim = _post(client, duty=PEAK,
                duty_cycle={"kind": "S2", "duty": PEAK, "t_on_s": 600.0,
                            "t_start_c": 40.0,
                            "calibration_duty": RATED}).json()["limits"]
    assert lim["s2_from_rated_s"] is not None      # a warm start still applies
    assert lim["s2_from_cycle_mean_s"] is None
    assert "S2" in lim["s2_from_cycle_mean_note"]
    assert lim["ed_found"] is False and lim["ed_vs_cycle"] == []


def test_no_ED_and_no_search_is_refused_by_name(client, mock_map):
    """Switching the search off on a cycle that states no ratio leaves nothing
    to integrate — and that is a refusal, never a silent 50 % seed."""
    d = _refusal(_post(client, duty=PEAK, duty_cycle=dict(S3_FIND),
                       ed_search=False), "duty_cycle_bad_ed")
    assert "ed_pct" in d["text"] and "ed_search" in d["text"]


def test_the_found_regime_is_STORED_with_the_flag_that_says_it_was_found(
        client, mock_map, sandbox):
    """What the report reads: the ratio, the flag, the curve and the three
    pull times all survive `duty_results`' whitelist."""
    from motor_ai_sim import duty_results as dr

    _post(client, duty=PEAK, duty_cycle=dict(S3_FIND),
          ed_cycle_lengths_s=[60.0])
    lim = dr.get(DIE, CFG)[PEAK]["duty_cycle"]["limits"]
    for k in ("ed_allowable_pct", "ed_found", "ed_found_note", "at_allowable",
              "ed_vs_cycle", "ed_cycle_s", "ed_limiting_part",
              "s2_time_to_limit_s", "s2_from_rated_s", "s2_from_rated_note",
              "s2_from_cycle_mean_s"):
        assert k in lim, k
    assert lim["ed_found"] is True
    assert lim["at_allowable"]["magnet_peak_c"] is not None
    assert len(lim["ed_vs_cycle"]) == 1
    spec = dr.get(DIE, CFG)[PEAK]["duty_cycle"]["spec"]
    assert spec["ed_given"] is False and spec["ed_pct"] > 0.0


# ---------------------------------------------------------------------------
# (d) THE HAPPY PATH — one real Electromagnetic run, one real conduction solve
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def em_run(sandbox):
    """ONE Electromagnetic run of the L13's rated point, parked as a Run parks it.

    ``store_em_run`` is imported rather than re-spelled: that handshake IS the
    seam between the Electromagnetic and the Thermal tab, and a second copy here
    would test this module's idea of a run instead of the run.
    """
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.routes._validation import parse_mat_override

    set_request_materials(parse_mat_override(MAT_JSON))
    try:
        info = store_em_run(L13_GEO, run_id=RUN_ID,
                            phys=dict(EM, I_phase_rms=14.708, gamma_deg=2.0,
                                      coil_temp_c=120.0))
    finally:
        set_request_materials(None)
    time.sleep(0.3)          # the persist is a daemon thread — let it land
    return info


@pytest.fixture(scope="module")
def solved(client, em_run):
    """The whole thing, once: the L13's rated map in the robotics boundary
    conditions the user named, and the peak duty's S3 cycle on it."""
    r = client.post("/api/thermal/duty_cycle", params={"mat": MAT_JSON},
                    json={"die": DIE, "config": CFG, "duty": PEAK,
                          "geo": GEO_JSON,
                          "thermal_settings": dict(ROBOT_SETTINGS),
                          # the ED search on a coarse grid: the claim is the
                          # ANSWER, and a 5 % curve would buy nothing here at
                          # five times the wall clock
                          "ed_curve_step_pct": 25.0, "samples_per_segment": 24,
                          **EM})
    assert r.status_code == 200, r.text[:800]
    return r.json()


def test_the_L13_robot_joint_cycle(solved):
    """25 % of 60 s at 46 A, in a 40 °C room, the stator OD in a housing
    (heat_path='housing', 2026-09-26 — it replaced the 2 W/K mount)."""
    cyc, split, lim = solved["cycle"], solved["split"], solved["limits"]
    # Printed (visible under -s, and on any failure): the numbers this whole
    # feature exists to produce, so a regression is read rather than guessed at.
    print("\nL13 S3 25 %%/60 s, robotics 40 C, heat path housing: winding hot peak "
          "%.1f C (mean %.1f), stator peak %.1f C, %d cycles, ED allowable "
          "%.1f %%, S2 to 200 C %.1f s, stator side %.1f %% (mount %.1f W, "
          "housing %.1f W, coil ends %.1f W, bore %.1f W)"
          % (cyc["winding_hot_peak_c"], cyc["winding_hot_mean_c"],
             cyc["peak_c"]["stator"], cyc["n_cycles"],
             lim["ed_allowable_pct"] or -1.0,
             lim["s2_time_to_limit_s"] or -1.0, split["stator_pct"],
             split["mount_W"], split["housing_W"],
             split["winding_end_faces_W"], split["bore_W"]))

    assert solved["cached"] is False        # a real calibration solve
    assert solved["calibration_map"]["loss_source"]     # from a real EM run
    assert cyc["converged"] is True and cyc["n_cycles"] <= 60
    assert cyc["residual_K"] < 0.05
    assert abs(cyc["closure_pct"]) < 0.5

    # THE HOUSING AND THE END FACES ARE THE MACHINE's cooling: the stator OD
    # hands its heat to the housing (the network's fitted housing path), the
    # exposed coil ends take most of the rest, and there is no mount.
    assert split["stator_pct"] > 90.0, split
    assert split["mount_W"] == 0.0
    assert split["housing_W"] > split["winding_end_faces_W"] > 0.0
    assert abs(split["closure_pct"]) < 0.5

    # THE ANSWER the feature exists for.  It MOVED on 2026-09-26: a still-air
    # housing (~1.2 W/K to the room at this point) is a weaker door than the
    # 2 W/K mount held at 40 °C it replaced — 13.1 % where the mount allowed
    # 20–40 %.
    assert 8.0 < lim["ed_allowable_pct"] < 20.0, lim
    assert lim["ed_limiting_part"] == "winding"
    assert lim["winding_limit_c"] == 200.0
    peaks = [p for _ed, p in lim["ed_curve"] if p is not None]
    assert peaks == sorted(peaks), lim["ed_curve"]


def test_the_L13_numbers_are_the_machines_own(solved):
    """The capacities are the die's masses, and the conductances are the map's."""
    net = solved["network"]
    assert net["C_J_per_K"]["winding"] == pytest.approx(41.2, abs=0.1)
    assert net["C_total_J_per_K"] == pytest.approx(171.7, abs=0.5)
    assert net["t_ambient_c"] == 40.0 and net["t_mount_c"] == 40.0
    assert net["emissivity"] == 0.9
    assert net["G_W_per_K"]["s_mount"] == 0.0          # a housing, no mount
    assert net["d_housing_m"] == pytest.approx(0.085, abs=1e-6)
    # the end-face areas are the SOLVED map's own, not a second derivation
    assert net["areas_m2"]["winding_ends"] * 1e4 == pytest.approx(54.0, abs=8.0)
    assert "measured end-face areas" in net["side_area_basis"]["source"]
    # every still-air film names a real correlation, never a provisional constant
    for key, src in net["h_sources"].items():
        assert "provisional" not in src, (key, src)


def test_the_peak_segment_carries_the_peak_dutys_own_losses(solved):
    on = solved["spec"]["segments"][0]
    assert on["duty"] == PEAK and on["rpm"] == 1000.0
    assert on["losses_W"]["winding"] == pytest.approx(676.1, abs=1.0)
    assert on["total_W"] == pytest.approx(686.9, abs=1.5)
    assert solved["spec"]["segments"][1]["duty"] is None     # unpowered rest
    assert solved["spec"]["segments"][1]["total_W"] == 0.0
