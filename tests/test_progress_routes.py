"""The live-progress endpoints — /api/mechanical/progress and /api/thermal/progress.

Added 2026-09-07 with the bars themselves.  Until then both tabs ran a real FEM
solve behind a spinner that never moved, which on a minute-long rotor-stress run
or a twelve-pass coupled thermal run is indistinguishable from a hung server.
The Simulation tab has had a working strip for months; these two now poll the
SAME payload shape (``motor_ai_sim.progress``), and this module pins the three
promises a panel is allowed to build on:

  (a) idle answers with the full key set and ``running: false`` — a strip that
      mounts before anything is solved must render, not guard for missing keys;
  (b) a finished solve leaves ``step == total`` and ``running: false``.  Both
      halves matter: the flag is what stops the poll, and a bar frozen at 37/40
      because the last contact case converged early reads as a run that died;
  (c) a CACHE HIT does the same.  It is the failure mode this is most likely to
      have: an instant answer that never touches a solver would, without the
      start+finish, leave whatever ran before it reported as still running.

Everything runs on deliberately small work — a coarse mesh, one load case, no
lift-off search, and for the thermal side ONE small Electromagnetic run on the
30 mm fixture through ``?geo=`` (2026-09-07: the thermal routes no longer solve
electromagnetics at all, so a temperature map is a snapshot replay plus a
conduction solve, and its bar counts exactly those two steps) — because the
claim here is the CONTRACT, not the physics.
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import time

import pytest

#: The invariant response shape, as pinned in tests/test_progress_tracker.py.
KEYS = {"running", "step", "total", "elapsed_s", "eta_s", "per_step_s", "frac",
        "phase", "composition", "ts_start", "kind"}

# The 30 mm 12s/14p spoke machine the thermal suite runs on
# (tests/test_thermal_routes.py::GEO_30MM), passed per-request so nothing on
# disk is touched.
GEO_30MM = {
    "stator_diameter": 30.0, "slot_height": 4.3, "core_thickness": 1.5,
    "num_seg": 2, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.2, "tooth_width": 2.6, "tooth2_width": 1.4, "cut_width": 1.5,
    "insulation_thickness": 0.05, "wire_width": 2.0, "wire_height": 0.5,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.1, "num_wires_per_slot": 6,
    "wire_split": 1, "slot_hs": 0.267, "magnet_height": 4.5,
    "rotor_house_height": 0.8, "shaft_height": 2.0, "magnet_fill_down": 0.9,
    "magnet_fill_up": 0.3, "magnet_fill_radius": 0.1, "magnet_up_gap": 0.1,
    "rotor_hole": 0.7, "magnet_down_height": 1.4, "magnet_lamination": 0,
    "stator_fillet_r": 1.2, "stator_fillet_r1": 0.0, "rotor_fill_r": 0.2,
    "motor_length": 10.0,
}


def _geo() -> str:
    return json.dumps(GEO_30MM)


#: The thermal request the bar below is measured on, and the Electromagnetic run
#: it is answered from — the same four-frame cycle tests/test_thermal_routes.py
#: uses (its ``FAST``), because ``store_em_run`` keys the run on exactly these
#: numbers and a mismatch here would be a 422, not a progress bar.
THERMAL_RUN_ID = "2026-09-07T08:30:00"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


@pytest.fixture(scope="module")
def em_run():
    """One Electromagnetic run, so the thermal bar has something to measure.

    Since 2026-09-07 ``/api/thermal/field`` refuses (422) when no run covers its
    operating point — which is the right answer, and useless for testing a
    progress bar.  The store and its pickle are redirected around the module for
    the usual reason: ``config/.last_transient_field.pkl`` is the user's own last
    run.
    """
    from motor_ai_sim.routes import simulation as sim
    from tests.test_thermal_routes import GEO_30MM as _G, store_em_run

    mp = pytest.MonkeyPatch()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="progress_em_"))
    mp.setattr(sim, "_transient_field_store_path", lambda: str(tmp / ".snap.pkl"))
    saved = dict(sim._transient_field_snap)
    sim._transient_field_snap.clear()

    info = store_em_run(_G, run_id=THERMAL_RUN_ID)
    time.sleep(0.3)          # the persist is a daemon thread — let it land

    yield info

    sim._transient_field_snap.clear()
    sim._transient_field_snap.update(saved)
    mp.undo()


@pytest.fixture(autouse=True)
def _isolate_last_stores(tmp_path, monkeypatch):
    """Send both tabs' last-result pickles to a tmp file.

    The standing rule this suite runs on: verification never goes through a
    persisting route with the user's own store behind it.  ``/rotor_stress`` and
    ``/thermal/field`` both remember what they solved, and the pickle would
    otherwise land in ``config/`` beside the machine the user has open.
    """
    from motor_ai_sim.routes import mechanical as mech
    from motor_ai_sim.routes import thermal as th

    monkeypatch.setattr(mech, "_LAST", {}, raising=True)
    monkeypatch.setattr(mech, "_LAST_LOADED", True, raising=True)
    monkeypatch.setattr(mech, "_last_store_path",
                        lambda: str(tmp_path / ".last_mechanical.pkl"))
    monkeypatch.setattr(th, "_LAST", {}, raising=True)
    monkeypatch.setattr(th, "_LAST_LOADED", True, raising=True)
    monkeypatch.setattr(th, "_last_store_path",
                        lambda: str(tmp_path / ".last_thermal.pkl"))
    # ...and the thermal router's own loss-map store, which is written on every
    # answer served from an Electromagnetic run and lives in config/ too.
    monkeypatch.setattr(th, "_loss_maps_path",
                        lambda: str(tmp_path / ".loss_maps.pkl"))
    monkeypatch.setattr(th, "_LOSS_MAPS_LOADED", True, raising=True)
    th._LOSS_MAPS.clear()
    yield
    th._LOSS_MAPS.clear()


def _poll(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text[:400]
    out = r.json()
    assert set(out) == KEYS, sorted(set(out) ^ KEYS)
    return out


# ---------------------------------------------------------------------------
# (a) the idle contract
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/api/mechanical/progress",
                                  "/api/thermal/progress"])
def test_progress_is_answerable_before_anything_runs(client, path):
    """200 with the whole payload, whatever has or has not happened.

    This test runs FIRST in the module, so on a fresh process it also pins the
    genuinely untouched state; after a solve elsewhere in the session the flag
    is still the claim — nothing is running, and the strip may render.
    """
    out = _poll(client, path)
    assert out["running"] is False
    assert out["eta_s"] == 0.0
    assert isinstance(out["phase"], str)
    assert isinstance(out["composition"], str)
    assert isinstance(out["kind"], str)


def test_the_progress_route_is_not_gated():
    """It mirrors the transient's own progress endpoint, which is open.

    The SOLVE is gated (``/api/mechanical/rotor_stress`` is "pro"); its counter
    is a status read polled twice a second while that paid-for solve runs, and a
    bar that 401s over a running solve is the one moment the user most needs to
    see something.
    """
    from motor_ai_sim.auth import _GATED, _GATED_PREFIX

    for path in ("/api/mechanical/progress", "/api/thermal/progress"):
        assert ("GET", path) not in _GATED
        assert not [p for m, p, _t in _GATED_PREFIX
                    if m == "GET" and path.startswith(p)]
    # ... and the route it mirrors is open in exactly the same way.
    assert ("GET", "/api/simulation/physics/fem_transient/progress") not in _GATED


# ---------------------------------------------------------------------------
# (b) after a mechanical solve
# ---------------------------------------------------------------------------

def test_mechanical_progress_is_complete_and_stopped_after_a_solve(client):
    """One case, coarse mesh, no lift-off search — the cheapest honest solve.

    Two claims: the bar is FULL (``step == total``) and it is STOPPED.  The total
    is the solver's own revised count, not the route's pre-run guess: the
    contact loop converges in single figures out of a thirty-iteration budget
    and hands the rest back, which is exactly why the two must agree at the end.
    """
    r = client.get("/api/mechanical/rotor_stress",
                   params={"loads": "centrifugal", "rpm": 8000,
                           "cases": "single", "mesh_size_mm": 3.0, "order": 1,
                           "lift_off_solves": 0, "field": False})
    assert r.status_code == 200, r.text[:600]

    out = _poll(client, "/api/mechanical/progress")
    assert out["running"] is False
    assert out["total"] > 0
    assert out["step"] == out["total"]
    assert out["frac"] == 1.0
    assert out["eta_s"] == 0.0
    assert out["kind"] == "rotor_stress"
    # The breakdown is written by the SOLVER (the frontend used to derive its
    # own, which is how a bar ends up describing a schedule nobody ran).
    assert "contact iterations" in out["composition"], out["composition"]
    # It got past the mesh and through the cases — the phase is the last stage,
    # not the "geometry build" the route opened on.
    assert out["phase"].startswith("post-processing"), out["phase"]


def test_mechanical_progress_is_stopped_after_a_cache_hit(client):
    """The same request twice: the second is served from the cache in
    milliseconds and must STILL leave the bar stopped and full — an instant
    answer that skipped the start/finish would leave a stale ``running: true``
    for the next poll to find."""
    p = {"loads": "centrifugal", "rpm": 8000, "cases": "single",
         "mesh_size_mm": 3.0, "order": 1, "lift_off_solves": 0, "field": False}
    assert client.get("/api/mechanical/rotor_stress", params=p).status_code == 200
    second = client.get("/api/mechanical/rotor_stress", params=p)
    assert second.status_code == 200, second.text[:400]
    assert second.json()["cached"] is True

    out = _poll(client, "/api/mechanical/progress")
    assert out["running"] is False
    assert out["step"] == out["total"] > 0


def test_a_refused_request_does_not_leave_the_bar_running(client):
    """A 422 goes through the same ``finally``.

    ``cases=nonsense`` is rejected before the tracker is even started, and
    ``rpm=0`` after — either way the endpoint must not be left claiming a live
    solve, or the panel waits for a run that never began.
    """
    bad = client.get("/api/mechanical/rotor_stress",
                     params={"cases": "nonsense", "rpm": 8000})
    assert bad.status_code == 422
    assert _poll(client, "/api/mechanical/progress")["running"] is False


def test_mechanical_mesh_route_reports_its_own_kind(client):
    """/mesh is a mesher, not a solve — but it is gmsh, it is seconds, and the
    strip has to be able to NAME it (that is what ``kind`` is for)."""
    # order=1 keeps this off the (3.0 mm, P2) key tests/test_mechanical_last.py
    # asserts a fresh build on: one process, one four-entry mesh cache.
    r = client.get("/api/mechanical/mesh",
                   params={"mesh_size_mm": 3.0, "order": 1})
    assert r.status_code == 200, r.text[:400]
    out = _poll(client, "/api/mechanical/progress")
    assert out["running"] is False
    assert out["kind"] == "mesh"
    assert out["step"] == out["total"] == 1


# ---------------------------------------------------------------------------
# (c) after a thermal solve
# ---------------------------------------------------------------------------

def test_thermal_mesh_progress_is_complete_and_stopped(client):
    # 2.7 mm, not the 2.5 the thermal suite meshes at: these modules share one
    # process and one mesh cache, and warming a key another module asserts
    # ``cached is False`` on would break IT, not this.
    r = client.get("/api/thermal/mesh",
                   params={"mesh_size_mm": 2.7, "min_size_mm": 0.6,
                           "n_sectors": 2, "geo": _geo()})
    assert r.status_code == 200, r.text[:600]
    out = _poll(client, "/api/thermal/progress")
    assert out["running"] is False
    assert out["kind"] == "mesh"
    assert out["step"] == out["total"] == 1


def _thermal_params(**over) -> dict:
    from tests.test_thermal_routes import FAST

    p = {**FAST, "geo": _geo()}
    p.update(over)
    return p


def test_thermal_field_progress_counts_the_conduction_solve_and_the_post(
        client, em_run):
    """Conduction + post-processing, and NOTHING else (2026-09-07).

    The bar used to open at ``frames + 2`` because the first thing a /field
    request did was run its own multi-frame eddy transient.  That solve is gone —
    the loss map is taken from an Electromagnetic run — so the honest budget is
    the two steps that are still work: the conduction solve and the
    post-processing.  A bar that still counted EM frames would be describing a
    schedule nobody runs, which is the exact failure the composition string was
    added to prevent.
    """
    r = client.get("/api/thermal/field", params=_thermal_params())
    assert r.status_code == 200, r.text[:600]

    out = _poll(client, "/api/thermal/progress")
    assert out["running"] is False
    assert out["kind"] == "field"
    assert out["total"] == 2                     # conduction + post-processing
    assert out["step"] == out["total"]
    assert out["frac"] == 1.0
    assert out["composition"] == "conduction + post-processing", out["composition"]
    assert "EM frame" not in out["composition"]
    assert out["phase"].startswith("post-processing"), out["phase"]


def test_a_thermal_request_with_no_electromagnetic_run_does_not_leave_the_bar_running(
        client, em_run):
    """A 422 goes through the same ``finally``.

    The refusal is raised inside the solve function, well after the tracker
    started, and the panel would otherwise poll for ever a run that ended before
    it began.
    """
    r = client.get("/api/thermal/field",
                   params=_thermal_params(I_phase_rms=999.0))
    assert r.status_code == 422, r.text[:400]
    assert r.json()["detail"]["error_code"] == "no_electromagnetic_run"
    assert _poll(client, "/api/thermal/progress")["running"] is False


def test_thermal_field_progress_is_stopped_after_a_cache_hit(client, em_run):
    p = _thermal_params()
    first = client.get("/api/thermal/field", params=p)
    assert first.status_code == 200, first.text[:400]
    second = client.get("/api/thermal/field", params=p)
    assert second.status_code == 200, second.text[:400]
    assert second.json()["cached"] is True

    out = _poll(client, "/api/thermal/progress")
    assert out["running"] is False
    assert out["step"] == out["total"] > 0
