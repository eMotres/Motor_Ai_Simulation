"""The Thermal tab takes THE LATEST Electromagnetic run of the loaded machine.

Owner, 2026-09-30 (CIANO14 40 new / L20 / peak): he re-ran the
Electromagnetic simulation, pressed Solve on the Thermal tab and was refused
"No Electromagnetic result for this point" — «I just re-ran the EM, it still
says this; it should simply take the values from there and compute».

ROOT CAUSE (live data, read-only): the stored run was keyed with the magnet
temperature it was solved at (45.2 °C, from the Electromagnetic tab), and the
Thermal panel sent its OWN copy of the operating point — without a magnet
temperature.  `_em_loss_map`'s exact physics identity therefore compared
``magnet_temp_c = None`` against ``45.2`` and refused a run of the same
machine one minute old.  (demag and gap_layers differed too; they are not in
that identity.)

The rule now, pinned here:

  (1) /field, /coupled (and the duty cycle) use the NEWEST stored run of the
      loaded machine and ADOPT its point and settings — current, γ, speed,
      coil and magnet temperatures, steps, mesh — whatever the request sent;
  (2) they refuse only when there is no run of this machine, or when the
      newest run is of a DIFFERENT machine: geometry INPUTS (a stale derived
      field is not a difference), EM materials or winding;
  (3) they never solve electromagnetics — asserted by making the solver
      explode if it is entered.

Runs on the 30 mm 12s/14p fixture of tests/test_thermal_routes.py, one real
two-frame run solved once for the module.
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import time

import pytest

from tests.test_thermal_routes import GEO_30MM

RUN_ID = "2026-09-30T09:50:45"
#: The run, spelled the way the owner's was: its own steps, mesh, coil and
#: MAGNET temperature — none of which the Thermal panel sends.
RUN = {
    "n_steps_per_period": 2, "n_periods": 1.0,
    "mesh_size_mm": 2.5, "min_size_mm": 0.6, "n_sectors": 2,
    "outer_air_factor": 1.3, "gap_layers": 2.0,
    "I_phase_rms": 30.0, "gamma_deg": 10.0, "coil_temp_c": 150.0,
    "magnet_temp_c": 45.2,
}
#: What a Thermal panel with its OWN copy of the EM settings would send: a
#: different step count, mesh, current, angle, speed and no magnet temperature.
PANEL_COPY = {
    "n_steps_per_period": 36, "n_periods": 1.0,
    "mesh_size_mm": 1.0, "min_size_mm": 0.3, "n_sectors": 2,
    "outer_air_factor": 1.2, "I_phase_rms": 80.61, "gamma_deg": 0.0,
    "rpm": 25000.0, "coil_temp_c": 200.0,
}
AIR = {"cooling_mode": "air", "ambient_temp": 30.0, "air_speed_mps": 30.0}


def _geo(**over) -> str:
    return json.dumps({**GEO_30MM, **over})


def _store_run(run_id: str = RUN_ID, geo: dict = None):
    """ONE real Electromagnetic run, parked the way a Run parks it — keyed by
    ``_field_snap_key_fields`` with the run's own settings (magnet temperature
    included) and stored through ``_store_transient_field_snapshot``, which
    also records the machine's inputs."""
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.routes import thermal as th
    from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

    geo_ov = sim._parse_geo_override(json.dumps(geo or GEO_30MM))
    ns_eff = th._snap_n_sectors(RUN["n_sectors"], geo_ov)
    d = fem_transient_sliding_band(
        n_steps_per_period=RUN["n_steps_per_period"],
        n_periods=RUN["n_periods"], gamma_deg=RUN["gamma_deg"],
        I_phase_rms=RUN["I_phase_rms"], mesh_size_mm=RUN["mesh_size_mm"],
        min_size_mm=RUN["min_size_mm"],
        outer_air_factor=RUN["outer_air_factor"], n_sectors=int(ns_eff),
        stator_fillet_mm=0.0, coil_temp_c=RUN["coil_temp_c"],
        magnet_temp_c=RUN["magnet_temp_c"],
        eddy=True, rotor_eddy=True, demag=False, return_field=True,
        field_first=False, rotor_angle0_deg=0.0, pole_copy=False,
        iron_template=True, geo_mesh=True, structured_gap=True,
        airgap_macro=False, gap_layers=RUN["gap_layers"],
        component_mesh_mm={}, geo_override=geo_ov, element_order=2)
    d["computed_at"] = run_id
    kf = sim._field_snap_key_fields(
        gamma_deg=RUN["gamma_deg"], I_phase_rms=RUN["I_phase_rms"],
        mesh_size_mm=RUN["mesh_size_mm"], min_size_mm=RUN["min_size_mm"],
        outer_air_factor=RUN["outer_air_factor"], n_sectors=ns_eff,
        stator_fillet_mm=0.0, gap_layers=RUN["gap_layers"],
        coil_temp_c=RUN["coil_temp_c"], comp_mesh={}, pole_copy=False,
        iron_template=True, geo_mesh=True, structured_gap=True,
        airgap_macro=False, n_steps_per_period=RUN["n_steps_per_period"],
        n_periods=RUN["n_periods"], eddy=True, rotor_eddy=True, demag=False,
        drive="current", element_order=2,
        cfg_fingerprint=sim._config_physics_fingerprint(
            with_request_materials=False),
        geo_ov=geo_ov, mat_ov=None, magnet_temp_c=RUN["magnet_temp_c"])
    sim._store_transient_field_snapshot(
        tuple(kf.values()), d["field"], d, eddy=True,
        n_steps_per_period=RUN["n_steps_per_period"],
        n_periods=RUN["n_periods"], solve_time_s=1.0, key_fields=kf)
    time.sleep(0.2)                  # the persist is a daemon thread
    return kf


@pytest.fixture(scope="module")
def run_store():
    """The snapshot store and its pickle redirected for the module, with the
    one run in it."""
    from motor_ai_sim.routes import simulation as sim

    mp = pytest.MonkeyPatch()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="thermal_latest_em_"))
    mp.setattr(sim, "_transient_field_store_path", lambda: str(tmp / ".snap.pkl"))
    saved = dict(sim._transient_field_snap)
    sim._transient_field_snap.clear()
    kf = _store_run()
    yield {"key_fields": kf, "tmp": tmp}
    sim._transient_field_snap.clear()
    sim._transient_field_snap.update(saved)
    mp.undo()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """The thermal router's own stores off the disk, and the EM solver made to
    EXPLODE: nothing in this module may start an electromagnetic solve."""
    from motor_ai_sim.routes import thermal as th
    from motor_ai_sim.simulation import fem_solver_2d as fs

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


@pytest.fixture
def no_em_solve(monkeypatch):
    from motor_ai_sim.simulation import fem_solver_2d as fs

    def _boom(*a, **k):
        raise AssertionError("the Thermal tab started an electromagnetic solve")
    monkeypatch.setattr(fs, "fem_transient_sliding_band", _boom)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


def _field(client, **over):
    p = {**PANEL_COPY, **AIR, "geo": _geo(), "fresh": True}
    p.update(over)
    return client.get("/api/thermal/field", params=p)


def _detail(r) -> dict:
    d = r.json().get("detail")
    return d if isinstance(d, dict) else {"error": d}


# ---------------------------------------------------------------------------
# (1) the latest run is used, and its point wins
# ---------------------------------------------------------------------------

def test_the_owners_case_is_refused_by_the_exact_point_lookup(client, run_store,
                                                              no_em_solve):
    """The root cause, reproduced: the panel's copy carries no magnet
    temperature, the exact-point lookup keys ``magnet_temp_c = None`` against
    the run's 45.2 °C and refuses — even at the run's own current, angle,
    steps and mesh."""
    r = _field(client, em_source="point",
               **{k: RUN[k] for k in ("n_steps_per_period", "mesh_size_mm",
                                      "min_size_mm", "outer_air_factor",
                                      "I_phase_rms", "gamma_deg",
                                      "coil_temp_c")})
    assert r.status_code == 422, r.text[:300]
    assert _detail(r).get("error_code") == "no_electromagnetic_run"


def test_field_uses_the_latest_run_whatever_the_panel_sent(client, run_store,
                                                           no_em_solve):
    r = _field(client)
    assert r.status_code == 200, r.text[:600]
    out = r.json()
    src = out["loss_source"]
    assert src["kind"] == "simulation_run"
    assert src["run_id"] == RUN_ID
    em = out["em_run"]
    # The RUN's point and settings, not the panel's copy.
    assert em["run_id"] == RUN_ID
    assert em["I_phase_rms"] == pytest.approx(RUN["I_phase_rms"])
    assert em["gamma_deg"] == pytest.approx(RUN["gamma_deg"])
    assert em["coil_temp_c"] == pytest.approx(RUN["coil_temp_c"])
    assert em["magnet_temp_c"] == pytest.approx(RUN["magnet_temp_c"])
    assert em["n_steps_per_period"] == RUN["n_steps_per_period"]
    assert em["mesh_size_mm"] == pytest.approx(RUN["mesh_size_mm"])
    assert em["computed_at"] and em["computed_at"].startswith("2026-09-30T")
    # The physics the map was solved at is the physics reported.
    assert out["P_loss_total_W"] and out["P_loss_total_W"] > 0
    assert out["T_max"] > AIR["ambient_temp"]


def test_the_remembered_params_are_the_runs(client, run_store, no_em_solve):
    """/last restores what the map IS of — the run's point, not the copy the
    request carried."""
    assert _field(client).status_code == 200
    last = client.get("/api/thermal/last").json()["field"]
    p = last["params"]
    assert p["I_phase_rms"] == pytest.approx(RUN["I_phase_rms"])
    assert p["n_steps_per_period"] == RUN["n_steps_per_period"]
    assert p["magnet_temp_c"] == pytest.approx(RUN["magnet_temp_c"])


def test_coupled_uses_the_latest_run(client, run_store, no_em_solve):
    p = {**PANEL_COPY, **AIR, "geo": _geo(), "max_iter": 2}
    r = client.get("/api/thermal/coupled", params=p)
    assert r.status_code == 200, r.text[:600]
    out = r.json()
    assert out["em_run"]["run_id"] == RUN_ID
    # The loop starts from the temperature the run's map was solved at.
    assert out["copper_scaling"]["t_ref_c"] == pytest.approx(RUN["coil_temp_c"])
    assert out["copper_scaling"]["em_solves"] == 0


def test_em_run_names_the_run_the_next_solve_uses(client, run_store):
    r = client.get("/api/thermal/em_run", params={"geo": _geo()})
    assert r.status_code == 200
    out = r.json()
    assert out["ok"] is True
    assert out["em_run"]["run_id"] == RUN_ID
    assert out["em_run"]["n_steps_per_period"] == RUN["n_steps_per_period"]
    assert out["em_run"]["inputs_recorded"] is True


# ---------------------------------------------------------------------------
# (2) refused only for a different machine
# ---------------------------------------------------------------------------

def test_a_stale_derived_field_is_not_a_different_machine(client, run_store,
                                                          no_em_solve):
    """PR #75's rule: derived radii are functions of the inputs.  A request
    whose geometry carries a STALE derived copy (bore radius 0.1 mm off) with
    identical inputs is the same machine and is answered."""
    r = _field(client, geo=_geo(stator_inner_radius=99.0, slot_width=9.9,
                                slot_hs=0.5))
    assert r.status_code == 200, r.text[:600]
    assert r.json()["em_run"]["run_id"] == RUN_ID


def test_a_geometry_input_difference_is_refused_by_name(client, run_store,
                                                         no_em_solve):
    r = _field(client, geo=_geo(slot_height=4.6))
    assert r.status_code == 422, r.text[:300]
    d = _detail(r)
    assert d["error_code"] == "no_electromagnetic_run"
    assert d["em_refusal"] == "different_machine"
    assert d["error"] == "Latest EM run is for a different geometry"
    assert "slot_height" in d["reason"]


def test_a_material_difference_is_refused_by_name(client, run_store,
                                                  no_em_solve):
    from motor_ai_sim.config import get_material_assignments

    cur = str((get_material_assignments() or {}).get("magnet") or "")
    other = "N52UH_150C" if cur != "N52UH_150C" else "F52SH_120C"
    r = _field(client, mat=json.dumps({"assignment": {"magnet": other}}))
    assert r.status_code == 422, r.text[:300]
    d = _detail(r)
    assert d["em_refusal"] == "different_machine"
    assert d["error"] == "Latest EM run used different materials"
    assert "magnet" in d["reason"]


def test_an_em_inert_material_is_not_a_different_machine(client, run_store,
                                                         no_em_solve):
    """The liner is a THERMAL choice (2026-09-07): swapping it never asks for
    a new Electromagnetic run."""
    from motor_ai_sim.config import get_material_assignments

    cur = dict(get_material_assignments() or {})
    liner = "Al2O3" if cur.get("slot_insulation") != "Al2O3" else "Nomex"
    r = _field(client, mat=json.dumps({"assignment": {"slot_insulation": liner}}))
    assert r.status_code == 200, r.text[:600]


def test_no_run_of_this_machine_is_refused_by_name(client, run_store,
                                                   no_em_solve):
    from motor_ai_sim.routes import simulation as sim

    saved = dict(sim._transient_field_snap)
    sim._transient_field_snap.clear()
    try:
        # …and nothing on disk either.
        p = pathlib.Path(sim._transient_field_store_path())
        moved = p.with_suffix(".hidden")
        if p.exists():
            p.replace(moved)
        try:
            r = _field(client)
        finally:
            if moved.exists():
                moved.replace(p)
    finally:
        sim._transient_field_snap.update(saved)
    assert r.status_code == 422, r.text[:300]
    d = _detail(r)
    assert d["em_refusal"] == "no_run"
    assert d["error"] == "No Electromagnetic run for this machine"


# ---------------------------------------------------------------------------
# the machine comparison itself (no solve)
# ---------------------------------------------------------------------------

def test_machine_diff_names_the_winding():
    from motor_ai_sim.routes import thermal as th

    rec = {"geometry": {"slot_height": 4.3, "stator_inner_radius": 9.0},
           "assignment": {"magnet": "F52SH_120C"}, "parts": {},
           "winding": {"connection": "2P", "n_parallel": 2}}
    cur = {"geometry": {"slot_height": 4.3, "stator_inner_radius": 9.3},
           "assignment": {"magnet": "F52SH_120C"}, "parts": {},
           "winding": {"connection": "4S", "n_parallel": 1}}
    d = th._machine_diff({}, rec, cur, None)
    assert list(d) == ["winding"]                   # derived radius skipped
    assert th._machine_headline(d) == "Latest EM run is for a different winding"
    cur_same = dict(cur, winding=dict(rec["winding"]))
    assert th._machine_diff({}, rec, cur_same, None) == {}


def test_the_newest_run_of_this_machine_wins(run_store, no_em_solve):
    """Two runs of the machine: the NEWER one is used, whatever its point."""
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.routes import thermal as th

    geo_ov = sim._parse_geo_override(_geo())
    first = th.latest_em_run(geo_ov)
    assert first["run_id"] == RUN_ID
    # Re-file the same entry under a newer stamp (a re-run) — the lookup reads
    # the stamps, not the insertion order.
    key = first["key"]
    entry = dict(sim._transient_field_snap[key])
    entry["meta"] = dict(entry["meta"], computed_at="2026-09-30T11:00:00")
    older = dict(entry, meta=dict(entry["meta"], computed_at="2026-09-29T08:00:00"))
    k_old = tuple(list(key[:-1]) + [("older",)])
    sim._transient_field_snap[k_old] = older
    sim._transient_field_snap[key] = entry
    try:
        assert th.latest_em_run(geo_ov)["run_id"] == "2026-09-30T11:00:00"
    finally:
        sim._transient_field_snap.pop(k_old, None)
        entry["meta"]["computed_at"] = RUN_ID
