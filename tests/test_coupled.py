"""The EM <-> thermal ORCHESTRATOR — /api/coupled.

Phase 2 of the coupling.  Phase 1 gave every magnet card two temperature
coefficients and every EM solve an optional ``magnet_temp_c``
(tests/test_magnet_temperature.py); this is the thing that decides what that
temperature should BE, by running the loop the two solvers are forbidden to run
for each other:

    EM run at (T_coil, T_magnet)  ->  thermal solve  ->  winding / magnet
    averages  ->  back into the EM run's temperatures  ->  until both settle.

User, 2026-09-08: *"не надо всё смешивать, нужен оркестратор"* and *"чтобы можно
было его включать и отключать"*.  Both halves of that sentence are testable, and
this file tests them:

  (a) SOLVER ISOLATION SURVIVES.  The orchestrator is a third module above the
      two routers; it calls ``get_fem_transient`` and ``solve_thermal_field``
      through their own public entry points, and neither router gains a call into
      the other.  Asserted structurally — a thermal module that imports the
      transient solver is the 2026-09-07 rule broken, whatever the numbers say.
  (b) OFF IS TODAY.  A run that did not go through this router carries no
      ``coupling`` block, and the summary builder cannot produce one: the block
      exists only because the orchestrator wrote it onto the run it belongs to.
  (c) THE LOOP ACTUALLY FEEDS BACK.  The second EM run is made at the FIRST
      thermal answer's temperatures, not at the ones the request came in with —
      which is the entire claim, and the one thing a loop that silently re-ran
      the same point would still pass every other assertion here.
  (d) THE RUN ON DISK IS THE RUN REPORTED.  The temperatures in the ``coupling``
      block are the temperatures the LAST electromagnetic run was solved at, so
      the cards beside them describe the machine those cards were computed for.
  (e) IT STOPS WHEN TOLD TO — a cancel between phases, by run-id, is a 499 and
      not a loop that finishes anyway.
  (f) THE COOLING IS THE USER'S.  The boundary conditions come from the Thermal
      tab's stored fields through a mapper that mirrors the browser's
      ``thermalStore.coolingFields`` field for field, pinned here against the
      real ``config/.panel_settings.json``.

COST.  The loop is the only thing in this suite that solves: two iterations of a
4-frame coupled-eddy transient on the 30 mm 12s/14p fixture over its half-wedge,
plus a conduction solve each.  Everything else — the mapper, the refusals, the
isolation check — is arithmetic and imports.

NOTHING THE USER OWNS IS TOUCHED.  Every store the loop writes is redirected to a
tmp directory first: the last transient (and its ledger and field snapshot), the
run journal, the thermal last-result and loss-map pickles, and this router's own
``/last``.  A test that ran the real Run path against ``config/`` would replace
the machine the user has on screen — the exact failure the no-silent-state rule
exists for.
"""
from __future__ import annotations

import json
import math
import pathlib
import tempfile

import pytest

from motor_ai_sim import jobs as _JOBS

# The 30 mm 12s/14p spoke machine every physics fixture in this repo is pinned
# on (tests/test_thermal_routes.GEO_30MM), passed as a per-request override.
from tests.test_thermal_routes import GEO_30MM

#: d-axis PINNED for 12s/14p (theta* = 30/7 mech = 60.000 deg el, measured on
#: three cross-sections — see DAXIS_SHIFT_DEG in fem_solver_2d).  Sent so the
#: loop does not pay for the 24-frame no-load calibration sweep on top of the
#: transients it is actually here to make.
DAXIS_12S14P_DEG = 60.0

#: The cheapest honest CYCLE, the same one tests/test_thermal_routes uses: four
#: frames over one electrical period on a coarse mesh over the machine's natural
#: half-wedge.  Four and not one because a single frame has no B(t) history, so
#: it is not a cycle average and no loss map can ever match it (the orchestrator
#: refuses it by name — see the refusal tests).
#:
#: `rpm` and `connection` are deliberately ABSENT: the thermal half resolves both
#: from the shared configuration (`_loss_snapshot_probe` does not take them), so
#: sending an explicit value that differs would make the two halves describe
#: different machines.  The orchestrator refuses that by name too, and omitting
#: them here is what a panel whose speed is saved to the machine does.
EM_BODY = {
    "n_steps_per_period": 4, "n_periods": 1.0,
    "gamma_deg": 0.0, "I_phase_rms": 20.0,
    "coil_temp_c": 120.0,
    "daxis_deg": DAXIS_12S14P_DEG,
    "mesh_size_mm": 2.5, "min_size_mm": 0.6, "outer_air_factor": 1.3,
    "n_sectors": 2, "gap_layers": 2.0, "stator_fillet_mm": 0.0,
    "structured_gap": True, "iron_template": True, "geo_mesh": True,
    "element_order": 2, "drive": "current",
    "eddy": True, "rotor_eddy": True, "demag": False,
    "include_frames": False,
    # PINNED (2026-09-08): an omitted mode follows the sandbox copy of the
    # user's config, and the day the Electromagnetic tab was switched to
    # Generator every run here became a generator run — the shaft-power
    # convention flipped (P_shaft_net = P_mech + friction) and the motor-side
    # assertions below failed on the sign alone.  The explicit value outranks
    # the config by design, like the geometry and the materials.
    "mode": "motor",
    "geo": json.dumps(GEO_30MM),
}

#: Air over the housing and air down the bore — the bore matters: without it the
#: rotor's only way out of a 2-D cross-section is the air gap, and the magnets
#: would sit at a temperature the loop cannot move.
COOLING = {"coolMode": "air", "ambientT": "30", "airSpeed": "10",
           "boreMode": "air", "boreAirSpeed": "40", "shaftExtMm": "0",
           "maxIter": "6"}


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


@pytest.fixture(scope="module")
def sandbox():
    """Every store the Run path writes, pointed at a tmp directory.

    MODULE-scoped (with its own ``pytest.MonkeyPatch``, the way
    tests/test_thermal_routes.em_runs does it) because the loop it protects is
    the only genuinely expensive thing in this file: paying for it once and
    answering eight assertions from it is the shape of this module.

    Module-level state is SAVED and restored, disk paths are REDIRECTED, and the
    two writers that have no path of their own — the run journal and the bench
    Ld/Lq probe — are stubbed: the journal is a record of runs the USER made
    (a test is not one), and the probe is three extra solves this file has no use
    for.

    ``_geometry_fingerprint`` is redirected so that "no override" fingerprints as
    the 30 mm fixture.  That is what makes ``_is_live_machine`` true inside
    ``get_fem_transient``, which is the gate on everything this test is about:
    the field snapshot the thermal half reads, the persisted last run the
    ``coupling`` block is written onto, and the restore store the panel reloads.
    Without it the loop would be solving a machine the backend considers a
    throw-away candidate, and would find no loss map at all.
    """
    from motor_ai_sim.routes import coupled as cp
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.routes import thermal as th

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="coupled_test_"))
    monkeypatch = pytest.MonkeyPatch()

    _real_fp = sim._geometry_fingerprint
    monkeypatch.setattr(sim, "_geometry_fingerprint",
                        lambda ov=None: _real_fp(ov if ov else dict(GEO_30MM)))
    monkeypatch.setattr(sim, "_transient_store_path",
                        lambda: str(tmp / ".last_transient.json"))
    monkeypatch.setattr(sim, "_transient_field_store_path",
                        lambda: str(tmp / ".last_transient_field.pkl"))
    monkeypatch.setattr(sim, "_append_run_journal", lambda *a, **k: None)
    monkeypatch.setattr(sim, "_bench_compute", lambda *a, **k: None)

    saved_cache = dict(sim._fem_transient_cache)
    saved_ref = dict(sim._last_transient_ref)
    saved_snap = dict(sim._transient_field_snap)
    sim._fem_transient_cache.clear()
    sim._transient_field_snap.clear()
    sim._last_transient_ref["key"] = None
    sim._last_transient_ref["result"] = None

    monkeypatch.setattr(th, "_LAST", {}, raising=True)
    monkeypatch.setattr(th, "_LAST_LOADED", True, raising=True)
    monkeypatch.setattr(th, "_last_store_path",
                        lambda: str(tmp / ".last_thermal.pkl"))
    monkeypatch.setattr(th, "_loss_maps_path", lambda: str(tmp / ".loss_maps.pkl"))
    monkeypatch.setattr(th, "_LOSS_MAPS_LOADED", True, raising=True)
    th._LOSS_MAPS.clear()

    monkeypatch.setattr(cp, "_last_store_path", lambda: str(tmp / ".last_coupled.json"))
    monkeypatch.setattr(cp, "_LAST", {}, raising=True)
    monkeypatch.setattr(cp, "_LAST_LOADED", True, raising=True)
    _JOBS.clear_cancelled()

    yield tmp

    sim._fem_transient_cache.clear()
    sim._fem_transient_cache.update(saved_cache)
    sim._transient_field_snap.clear()
    sim._transient_field_snap.update(saved_snap)
    sim._last_transient_ref.update(saved_ref)
    th._LOSS_MAPS.clear()
    _JOBS.clear_cancelled()
    monkeypatch.undo()


# ---------------------------------------------------------------------------
# (a) the isolation the orchestrator exists to preserve
# ---------------------------------------------------------------------------

def test_the_two_solvers_still_do_not_know_about_each_other():
    """The rule this router was built to avoid breaking (2026-09-07).

    The reason a coupled loop needed a THIRD module at all is that the two
    obvious places to put it are both forbidden: the Thermal tab may not start an
    electromagnetic solve, and the Electromagnetic tab has no business running a
    conduction solve.  A loop written into either would pass every numerical test
    in this file and still be the thing the user said no to.
    """
    import inspect

    from motor_ai_sim.routes import coupled as cp
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.routes import thermal as th

    th_src = inspect.getsource(th)
    assert "get_fem_transient" not in th_src, (
        "routes.thermal calls the transient solver — the Thermal tab may not "
        "start an electromagnetic run")
    sim_src = inspect.getsource(sim)
    for name in ("solve_thermal_field", "solve_coupled"):
        assert name not in sim_src, (
            f"routes.simulation calls {name} — the Electromagnetic tab may not "
            f"start a thermal solve")
    # …and the orchestrator calls BOTH, which is the whole point of it existing.
    cp_src = inspect.getsource(cp)
    assert "get_fem_transient" in cp_src and "solve_thermal_field" in cp_src


def test_the_thermal_solve_takes_a_magnet_temperature_end_to_end():
    """Phase 1 plumbed the EM side; the thermal side had to follow.

    A loss map solved with a 163 °C magnet is a different map — lower Br, a knee
    nearer the working point, so a different air-gap field, iron loss and magnet
    eddy loss.  ``_PHYSICS_ID_FIELDS`` already refused to reuse one for the other
    (test_magnet_temperature); this is the parameter that lets a caller ASK for
    the run at that temperature instead of only being refused the wrong one.
    """
    import inspect

    from motor_ai_sim.routes import thermal as th

    for fn in (th.solve_thermal_field, th._em_loss_map, th.field):
        p = inspect.signature(fn).parameters
        assert "magnet_temp_c" in p, f"{fn.__name__} does not take magnet_temp_c"
    assert inspect.signature(th.solve_thermal_field) \
        .parameters["magnet_temp_c"].default is None


# ---------------------------------------------------------------------------
# (f) the cooling settings mapper — the browser's rule, server-side
# ---------------------------------------------------------------------------

def _stored_thermal_settings() -> dict:
    """The Thermal panel's REAL stored fields — read from the repository's own
    ``config/.panel_settings.json``, not through the router.

    The suite runs against a SANDBOX config directory (tests/conftest.py), which
    is exactly right for everything that writes and exactly wrong here: the point
    of this pin is the shape the SHIPPING app actually persists, and the sandbox
    has never seen a Thermal tab.  Read-only, and it skips rather than fails when
    the file is absent — a fresh checkout is not a regression.
    """
    p = pathlib.Path(__file__).resolve().parents[1] / "config" / ".panel_settings.json"
    if not p.exists():
        pytest.skip("no config/.panel_settings.json in this checkout")
    try:
        users = (json.loads(p.read_text(encoding="utf-8")).get("thermal") or {})
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"config/.panel_settings.json is unreadable: {exc}")
    for entry in users.values():
        s = (entry or {}).get("settings")
        if isinstance(s, dict) and s:
            return s
    pytest.skip("config/.panel_settings.json holds no thermal settings")


def test_the_mapper_reads_the_panel_store_the_panel_actually_wrote():
    """Every key the browser persists is a key this mapper understands.

    The store holds STRINGS ("30", "10") because that is what a text field
    contains, and the two sides agreeing on the NAMES is what makes the
    orchestrator solve the boundary conditions the user set rather than a default
    invented in Python.  Pinned against the real file, so a rename on either side
    is caught here instead of by a coupled run that quietly cooled a machine with
    still air.
    """
    from motor_ai_sim.thermal_settings import (cooling_fields,
                                               coupled_iteration_settings)

    s = _stored_thermal_settings()
    out = cooling_fields(s)
    assert out["cooling_mode"] == str(s.get("coolMode", "air"))
    assert out["ambient_temp"] == pytest.approx(float(s.get("ambientT", 40)))
    assert out["bore_mode"] == str(s.get("boreMode", "none"))
    if out["cooling_mode"] == "liquid":
        assert out["fluid"] == s["fluid"]
        assert out["fluid_temp_in_c"] == pytest.approx(float(s["tIn"]))
        assert out["flow_lpm"] == pytest.approx(float(s["flowLpm"]))
    if out["bore_mode"] == "air":
        assert out["bore_air_speed_mps"] == pytest.approx(float(s["boreAirSpeed"]))
    it = coupled_iteration_settings(s)
    assert 1 <= it["max_iter"] <= 40
    # Every key it produces is one the thermal solve actually accepts — a mapper
    # that invented a name would be a TypeError inside a running loop.
    import inspect

    from motor_ai_sim.routes.thermal import solve_thermal_field
    accepted = set(inspect.signature(solve_thermal_field).parameters)
    assert set(out) <= accepted, sorted(set(out) - accepted)


@pytest.mark.parametrize("settings,present,absent", [
    ({"coolMode": "air", "airSpeed": "8", "boreMode": "none"},
     ["air_speed_mps"], ["fluid", "flow_lpm", "fluid_temp_in_c", "h_conv"]),
    ({"coolMode": "liquid", "fluid": "oil", "tIn": "50", "flowLpm": "6",
      "boreMode": "none"},
     ["fluid", "fluid_temp_in_c", "flow_lpm"], ["air_speed_mps", "h_conv"]),
    ({"coolMode": "manual", "hConv": "120", "boreMode": "none"},
     ["h_conv"], ["air_speed_mps", "fluid", "flow_lpm"]),
    ({"coolMode": "air", "airSpeed": "8", "boreMode": "liquid",
      "boreFluid": "water", "boreTIn": "30", "boreFlowLpm": "2"},
     ["bore_fluid", "bore_fluid_temp_in_c", "bore_flow_lpm"],
     ["bore_air_speed_mps"]),
    ({"coolMode": "air", "airSpeed": "8", "boreMode": "none", "shaftExtMm": "0",
      "shaftExtSides": "1"},
     [], ["shaft_ext_length_mm", "shaft_ext_sides"]),
    # THE FRAME (2026-09-09).  `housed` is the machine every request before
    # today described, so it is not sent — and neither is a leftover
    # `openAirSpeed` from a machine the user switched back to housed.
    ({"coolMode": "air", "airSpeed": "8", "boreMode": "none",
      "frame": "housed", "openAirSpeed": "12"},
     [], ["frame", "open_air_speed_mps"]),
    # …and an OPEN one sends both, 0 m/s included: 0 is meaningful there (the
    # solver takes the housing's own air speed, because it is the same wash),
    # unlike a 0 mm shaft stub, which means the path is off.
    ({"coolMode": "air", "airSpeed": "8", "boreMode": "none", "frame": "open"},
     ["frame", "open_air_speed_mps"], []),
    ({"coolMode": "air", "airSpeed": "8", "boreMode": "none", "frame": "open",
      "openAirSpeed": "11"},
     ["frame", "open_air_speed_mps"], []),
])
def test_a_parameter_the_mode_does_not_use_is_not_sent(settings, present, absent):
    """The rule that makes this more than a rename (mirrors ``coolingFields``).

    An ``air_speed_mps`` beside a water jacket is a value the solver keys its
    cache on and never reads: the same machine under the same cooling then has
    two cache entries and two answers to "was this already solved".  The exposed
    shaft is the same story at 0 mm — ``shaft_ext_sides`` beside a length of zero
    is a parameter nothing uses.
    """
    from motor_ai_sim.thermal_settings import cooling_fields

    out = cooling_fields(settings)
    for k in present:
        assert k in out, k
    for k in absent:
        assert k not in out, k


def test_an_uncoolable_machine_is_named_before_a_solve_is_spent():
    """The four refusals the browser makes, made here too.

    They all reach a 422 from the thermal router eventually — but eventually is
    on the far side of a full electromagnetic transient the user has already
    paid for.
    """
    from motor_ai_sim.thermal_settings import cooling_issue

    assert cooling_issue({"coolMode": "air", "airSpeed": "5",
                          "boreMode": "none"}) is None
    assert "flow" in (cooling_issue({"coolMode": "liquid", "flowLpm": "0",
                                     "boreMode": "none"}) or "")
    assert "h must" in (cooling_issue({"coolMode": "manual", "hConv": "",
                                       "boreMode": "none"}) or "")
    assert "bore coolant" in (cooling_issue({"coolMode": "air", "airSpeed": "5",
                                             "boreMode": "liquid",
                                             "boreFlowLpm": "0"}) or "")
    assert "no cooled surface" in (cooling_issue({"coolMode": "none",
                                                  "boreMode": "none"}) or "")


# ---------------------------------------------------------------------------
# what the orchestrator refuses BEFORE it solves anything
# ---------------------------------------------------------------------------

def _detail(r):
    assert r.status_code == 422, f"{r.status_code}: {r.text[:400]}"
    d = r.json()["detail"]
    return d, [p["field"] for p in d.get("invalid_parameters", [])]


@pytest.mark.parametrize("over,field", [
    ({"n_steps_per_period": 1}, "n_steps_per_period"),
    # An IMPOSED SINUSOIDAL VOLTAGE has no carrier to measure and no requested
    # phase current to look a loss map up by, so it is still refused by name.
    # `pwm` is NOT in this list any more: since 2026-09-14 the loop runs the
    # inverter (tests/test_coupled_pwm.py), and the map it solves on is the PWM
    # run's own — handed over rather than looked up.
    ({"drive": "voltage"}, "drive"),
    ({"rotor_eddy": False}, "eddy"),
    ({"damping": 0.0}, "damping"),
])
def test_a_loop_that_could_never_close_is_refused_by_name(client, over, field):
    """Every one of these ends in a 422 from the thermal half anyway — AFTER a
    transient.  Refusing here costs milliseconds and says which parameter.

    A single frame has no cycle to average, so no loss map can ever match it; an
    imposed-voltage run has no requested phase current to look one up by; without
    the conducting solve on both sides the rotor is heated by nothing, so the
    magnet temperature fed back would be the ambient; and a damping of zero is a
    loop that never moves.
    """
    body = {**EM_BODY, **over, "thermal_settings": COOLING, "max_iter": 2}
    _d, fields = _detail(client.post("/api/coupled/run", json=body))
    assert field in fields


def test_an_impossible_cooling_spec_is_refused_before_the_first_run(client):
    """The Thermal tab's own validation, applied to the settings the loop was
    handed — a machine with every boundary adiabatic has no steady temperature to
    solve for, and answering with a number would answer a different question."""
    body = {**EM_BODY, "max_iter": 2,
            "thermal_settings": {"coolMode": "none", "boreMode": "none"}}
    d, fields = _detail(client.post("/api/coupled/run", json=body))
    assert fields == ["thermal_settings"]
    assert d["error_code"] == "no_thermal_boundary"
    assert "no cooled surface" in d["error"]


# ---------------------------------------------------------------------------
# (e) stopping
# ---------------------------------------------------------------------------

@pytest.fixture
def fresh_cancel_registry():
    """The cancel registry emptied around a test, so a stopped run-id cannot
    leak into the next one — the very failure keying them by id prevents.

    ONE registry since migration Stage 4 (``motor_ai_sim.jobs``), where there
    used to be two one-slot dicts that had to be set in step: this module's,
    checked between phases, and the transient's, read by the frame march.  A
    single map keyed by run id is what makes "cancel THIS run" true for both
    halves at once — and what stops a second account's Stop from clearing the
    id the first one's march is checking.
    """
    _JOBS.clear_cancelled()
    yield
    _JOBS.clear_cancelled()


def test_cancel_stops_the_loop_between_phases_and_says_499(
        client, fresh_cancel_registry):
    """A cancel that lands before the loop's first phase must cost NO solve.

    Keyed by run-id exactly as the transient's Stop is, and since Stage 4 there
    is ONE registry for both: the loop's between-phase check and the frame march
    inside a running EM solve read the same map, so a cancel cannot be honoured
    by one half and missed by the other (setting only the loop's used to make
    Stop wait out a six-minute transient).
    """
    r = client.post("/api/coupled/cancel", params={"run_id": "cx-1"})
    assert r.status_code == 200 and r.json()["cancelled"] is True
    # ONE registry, and it answers for both halves: the loop's between-phase
    # check and the frame march inside a running EM solve read the same map.
    assert _JOBS.is_cancelled("cx-1")
    assert not _JOBS.is_cancelled("cx-2")

    body = {**EM_BODY, "thermal_settings": COOLING, "max_iter": 2,
            "run_id": "cx-1"}
    r = client.post("/api/coupled/run", json=body)
    assert r.status_code == 499, r.text[:400]
    assert "stopped" in r.text
    # …and the bar is not left claiming a live loop.
    assert client.get("/api/coupled/progress").json()["running"] is False


def test_progress_is_open_and_answers_before_anything_has_run(client):
    """Polled twice a second for the whole of a solve the user is ALREADY paying
    the gated tier for; a bar that 401s over a running loop is the one moment it
    is most needed.  Same shape as the transient's and the thermal router's."""
    p = client.get("/api/coupled/progress").json()
    assert p["kind"] in ("", "coupled")
    for k in ("running", "step", "total", "elapsed_s", "eta_s", "per_step_s",
              "frac", "phase", "composition"):
        assert k in p, k


def test_last_is_empty_before_the_loop_has_ever_run(client, monkeypatch):
    """200 with ``has_result: false``, never a 404 — the toggle being off (or
    never used) is the NORMAL state, and a red 404 on every mount is not an error
    report.  Same contract /api/thermal/last and /api/mechanical/last keep."""
    from motor_ai_sim.routes import coupled as cp

    monkeypatch.setattr(cp, "_LAST", {}, raising=True)
    monkeypatch.setattr(cp, "_LAST_LOADED", True, raising=True)
    out = client.get("/api/coupled/last").json()
    assert out["has_result"] is False and out["result"] is None
    assert "live_geometry_fingerprint" in out


# ---------------------------------------------------------------------------
# (b)+(c)+(d) the loop itself — the only solving test in this file
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def loop(client, sandbox):
    """TWO iterations of the real loop, solved ONCE for the whole module.

    ``max_iter=2`` is the cheapest run that is still a LOOP: one iteration would
    prove nothing about feedback, and the assertion that matters most (the second
    EM run is made at the FIRST thermal answer's temperatures) needs exactly two.
    ``tol_k`` is deliberately tiny so the loop does NOT converge early — this test
    is about the machinery, and a fixture that happened to settle on pass one
    would silently stop testing it.
    """
    body = {**EM_BODY, "thermal_settings": COOLING,
            "max_iter": 2, "tol_k": 0.01, "damping": 0.5}
    r = client.post("/api/coupled/run", json=body)
    assert r.status_code == 200, r.text[:1200]
    return r.json()


def test_the_loop_runs_em_then_thermal_and_records_every_iteration(loop):
    """The history IS the answer: what went in, what came out, at each pass.

    Without it a coupled temperature is a number with no provenance — and this
    loop's whole claim is that the number came from somewhere checkable.
    """
    c = loop["coupling"]
    assert loop["ok"] is True
    assert c["iterations"] == 2 and c["em_runs"] == 2
    assert len(c["history"]) == 2
    for i, row in enumerate(c["history"], start=1):
        assert row["iter"] == i
        for k in ("T_coil_in", "T_coil_out", "T_magnet_in", "T_magnet_out",
                  "T_magnet_max", "P_loss_W", "T_em_Nm"):
            assert k in row, k
        assert math.isfinite(float(row["T_coil_in"]))
        assert math.isfinite(float(row["T_coil_out"]))
        # A machine making watts is hotter than the 30 °C air cooling it.
        assert float(row["T_coil_out"]) >= 30.0 - 1.0
        assert float(row["P_loss_W"]) > 0.0
    # The magnet MAX is reported (the demagnetisation check) and is never below
    # the body average that IS fed back.
    m = c["history"][0]
    if m["T_magnet_max"] is not None and m["T_magnet_out"] is not None:
        assert float(m["T_magnet_max"]) >= float(m["T_magnet_out"])


def test_the_second_em_run_is_made_at_the_first_answers_temperatures(loop):
    """THE claim.  Everything else here would still pass if the loop re-ran the
    same operating point twice.

    The update is under-relaxed, so the second run's input is the first run's
    input plus ``damping`` times the gap the thermal solve found — checked
    against the arithmetic rather than against a stored number, so this test
    still means something when the physics moves.
    """
    c = loop["coupling"]
    a, b = c["history"][0], c["history"][1]
    d = float(a["T_coil_out"]) - float(a["T_coil_in"])
    assert abs(d) > 0.02, ("the thermal solve returned the coil temperature it "
                           "was handed — there is no feedback to test")
    assert float(b["T_coil_in"]) == pytest.approx(
        float(a["T_coil_in"]) + c["damping"] * d, abs=0.02)
    if a["T_magnet_out"] is not None and a["T_magnet_in"] is not None:
        dm = float(a["T_magnet_out"]) - float(a["T_magnet_in"])
        assert float(b["T_magnet_in"]) == pytest.approx(
            float(a["T_magnet_in"]) + c["damping"] * dm, abs=0.02)


def test_the_magnet_starts_at_its_own_cards_temperature(loop):
    """"No magnet temperature yet" means THE CARD AS QUOTED (phase 1).

    Starting the iteration anywhere else would make the loop's first EM run a
    different machine from the one a plain Run produces, and the first number the
    user sees would move for a reason nothing on screen explains.
    """
    from motor_ai_sim.materials import get_magnet
    from motor_ai_sim.routes.thermal import _assignments

    name = (_assignments() or {}).get("magnet")
    if not name:
        pytest.skip("no magnet assigned in this checkout's config")
    t_ref = get_magnet(str(name)).temperature_c
    if t_ref is None:
        pytest.skip(f"{name} carries no reference temperature")
    assert loop["coupling"]["history"][0]["T_magnet_in"] == pytest.approx(
        float(t_ref), abs=0.01)


def test_the_thermal_half_found_the_map_the_em_half_just_solved(loop):
    """No electromagnetic solve ran inside the thermal request — it REPLAYED the
    run the orchestrator had just made.

    That handshake is the seam between the two tabs (``loss_source``), and it is
    the thing that would silently break if the magnet temperature reached one
    side of the physics identity and not the other: the thermal half would find
    nothing and answer 422, or — worse — match a run solved at another magnet
    temperature.
    """
    th = loop["thermal"]
    assert th["ok"] is True
    src = th["loss_source"]
    assert src["kind"] in ("simulation_run", "thermal_store"), src
    assert th["T_max"] >= th["ambient_temp"]
    assert (th["components"] or {}).get("winding") is not None


def test_the_reported_temperatures_are_the_ones_the_last_run_was_solved_at(loop):
    """(d) The run left on disk IS the run being reported.

    The panel writes this coil temperature straight back into the
    Electromagnetic tab's own field, so a headline one step ahead of the cards
    underneath it is a number nobody can reproduce by pressing Run.  Every
    iteration BEGINS with the EM run and the damped update is applied only when a
    further iteration will consume it, so the reported pair is always the solved
    pair — and the gap that remains is REPORTED rather than quietly closed.
    """
    c = loop["coupling"]
    last_em = c["history"][-1]
    assert c["coil_temp_c"] == pytest.approx(float(last_em["T_coil_in"]), abs=0.01)
    if c["magnet_temp_c"] is not None:
        assert c["magnet_temp_c"] == pytest.approx(
            float(last_em["T_magnet_in"]), abs=0.01)
    # …and the transient in the payload is that run.
    s = loop["transient"]["summary"]
    assert s["coil_temp_C"] == pytest.approx(c["coil_temp_c"], abs=0.06)
    # The budget is a budget: max_iter bounds the ELECTROMAGNETIC runs, and a
    # loop that quietly made a third one would double the cost of a six-pass
    # request on a 200 mm machine.
    assert c["em_runs"] == c["max_iter"] == 2
    assert c["converged"] is False        # tol_k = 0.01 K, on purpose
    assert "max_iter" in (c["warning"] or "")
    # The residual is the honest half of that: how far the last thermal answer
    # sat from the temperature it was solved at.
    assert c["residual_coil_K"] == pytest.approx(
        abs(float(last_em["T_coil_out"]) - float(last_em["T_coil_in"])), abs=0.01)
    assert c["residual_coil_K"] > c["tol_K"]


def test_the_coupling_block_is_written_onto_the_last_run(client, loop):
    """The Electromagnetic tab reads its cards from the summary, and this block
    is a statement ABOUT those cards.  A restore (page reload, tab switch) must
    bring it back with them, or the tab would show coupled numbers with nothing
    saying they are coupled."""
    assert loop["written_to_last_run"] is True
    r = client.get("/api/simulation/physics/fem_transient",
                   params={"restore": "true", "geo": json.dumps(GEO_30MM)})
    assert r.status_code == 200, r.text[:400]
    c = (r.json().get("summary") or {}).get("coupling")
    assert c is not None, "the restored last run carries no coupling block"
    assert c["iterations"] == 2
    assert c["coil_temp_c"] == pytest.approx(loop["coupling"]["coil_temp_c"])


def test_a_run_that_was_not_coupled_carries_no_such_block(loop):
    """"Off = today's behaviour, bit for bit", expressed in the payload.

    The summary BUILDER cannot produce a ``coupling`` key: the block exists only
    because the orchestrator wrote it onto the run afterwards.  Rebuilding this
    very run's summary from its own recorded arguments — the path a cache hit
    takes when the summary shape moves — is therefore the sharpest available
    check that an ordinary Run has none.
    """
    from motor_ai_sim.routes.simulation import _build_transient_summary

    res = loop["transient"]
    args = res.get("_summary_args")
    assert isinstance(args, dict), "the run recorded no summary build args"
    rebuilt = _build_transient_summary(
        res, I_phase_rms=float(args["I_phase_rms"]),
        gamma_deg=float(args["gamma_deg"]),
        coil_temp_c=float(args["coil_temp_c"]),
        geo_override=args.get("geo_override"),
        mode_requested=args.get("mode_requested"))
    assert "coupling" not in rebuilt


def test_the_converged_map_becomes_the_thermal_tabs_last_result(client, loop):
    """The Thermal tab needs no change to show the coupled answer: it already
    restores its last ``field`` result on mount, and the loop's final map is now
    that result.  Two tabs, one temperature."""
    out = client.get("/api/thermal/last", params={"field": False}).json()
    assert out["has_result"] is True
    e = out["field"]
    assert e is not None
    assert e["result"]["T_max"] == pytest.approx(loop["thermal"]["T_max"])
    # the request that produced it, including the magnet temperature that
    # SELECTED the electromagnetic run behind it
    p = e["params"]
    assert p["coil_temp_c"] == pytest.approx(loop["coupling"]["coil_temp_c"])
    if loop["coupling"]["magnet_temp_c"] is not None:
        assert p["magnet_temp_c"] == pytest.approx(
            loop["coupling"]["magnet_temp_c"])


def test_last_answers_with_the_loop_but_not_with_its_payloads(client, loop):
    """``/last`` is a LOOKUP: it says what the loop concluded.  Shipping a whole
    transient plus a temperature-per-node map to answer that is how a lookup
    becomes a download — the same lesson ``/api/thermal/last?field=false``
    learned."""
    out = client.get("/api/coupled/last").json()
    assert out["has_result"] is True
    res = out["result"]
    assert res["coupling"]["iterations"] == 2
    assert "transient" not in res and "thermal" not in res
    # Migration Stage 4: a run that arrives WITHOUT an id is given one by the
    # server, and the answer carries it — otherwise a client that sent none has
    # no way to poll its own progress or stop its own run.  This fixture's body
    # sends none, so what is pinned here is that the id exists and is this
    # router's (it used to be the empty string the body carried).
    assert res["run_id"] and res["run_id"].startswith("coupled-"), res["run_id"]
    assert "stale_geometry" in out


def test_the_summary_builder_puts_the_mechanical_half_on_a_real_run(
        loop, monkeypatch):
    """The wiring, on a REAL solver payload rather than a synthetic one.

    ``tests/test_bearings.py`` pins the arithmetic of ``_mech_loss_fields``;
    what is unproven there is that ``_build_transient_summary`` actually splats
    it into the summary every card reads.  Rebuilt from the loop's own recorded
    arguments — the path a cache hit takes when the summary shape moves — so it
    costs no solve, and the only thing faked is the die-file read this suite may
    not do.
    """
    from motor_ai_sim import mech_losses as ml
    from motor_ai_sim.routes.simulation import _build_transient_summary

    res = loop["transient"]
    args = res["_summary_args"]

    def _rebuild():
        return _build_transient_summary(
            res, I_phase_rms=float(args["I_phase_rms"]),
            gamma_deg=float(args["gamma_deg"]),
            coil_temp_c=float(args["coil_temp_c"]),
            geo_override=args.get("geo_override"),
            mode_requested=args.get("mode_requested"))

    # …with bearings on the machine.  618/8-2Z: an 8 mm shielded pair, which is
    # what a 30 mm motor at 20 000 rpm would actually be built with.  A 55 mm
    # 61811 pair here eats more than the rotor makes and the shaft efficiency
    # clamps to zero — honest arithmetic on a machine nobody would build, and a
    # fixture that tests the clamp instead of the wiring.
    monkeypatch.setattr(
        ml, "machine_bearings",
        lambda die=None, cfg=None: ({"A": {"card": "618/8-2Z"},
                                     "B": {"card": "618/8-2Z"},
                                     "lubrication": "grease", "preload_n": 0,
                                     "temp_source": "manual", "temp_c": 59},
                                    "D", "C"),
        raising=True)
    s = _rebuild()
    assert s["P_bearings_W"] > 0.0
    assert s["P_mech_extra_W"] == pytest.approx(
        s["P_bearings_W"] + s["P_windage_W"], abs=0.02)
    assert s["bearing_temp_c"] == pytest.approx(59.0)
    assert s["P_loss_total_incl_mech_W"] == pytest.approx(
        s["P_loss_total_W"] + s["P_mech_extra_W"], abs=0.1)
    # the shaft efficiency is the EM one with the friction where it sits
    assert 0.0 < s["efficiency_shaft"] < s["efficiency"]
    assert s["P_shaft_net_W"] == pytest.approx(
        s["P_mech_W"] - s["P_mech_extra_W"], abs=0.1)
    assert s["mech_losses"]["cards"] == ["618/8-2Z", "618/8-2Z"]
    # …and the mass it billed the bearings at is the run's OWN rotating mass,
    # not a fresh CAD measurement that could disagree with the card beside it.
    rot = sum(float(c.get("mass_kg") or c.get("mass_modelled_kg") or 0.0)
              for c in s["mass_components"]
              if str(c.get("name") or "").startswith(
                  ("Rotor back-iron", "Magnets", "Shaft", "Sleeve")))
    assert s["mech_losses"]["rotor_mass_kg"] == pytest.approx(rot, rel=1e-9)

    # …and with none, the very same run grows none of them.
    monkeypatch.setattr(ml, "machine_bearings",
                        lambda die=None, cfg=None: (None, None, None),
                        raising=True)
    bare = _rebuild()
    for k in ("P_bearings_W", "P_windage_W", "P_mech_extra_W",
              "bearing_temp_c", "efficiency_shaft", "mech_losses"):
        assert k not in bare, k


def test_every_iteration_records_where_the_mechanical_half_stood(loop):
    """The MECHANICAL columns of the history exist on every run (2026-09-08).

    ``None`` on a machine that names no bearings, and that is the point: the
    row says "unknown", never "0 W".  What must never happen is the key being
    absent on some runs and present on others — a history a reader has to
    pattern-match is a history nobody reads.
    """
    for row in loop["coupling"]["history"]:
        for k in ("bearing_temp_c", "bearing_temp_source", "P_mech_extra_W"):
            assert k in row, k
    # The top-level mechanical numbers are present exactly when the run carried
    # them, and they are then the LAST run's — one answer, not two.
    c = loop["coupling"]
    s = loop["transient"]["summary"]
    for k in ("P_bearings_W", "P_windage_W", "P_mech_extra_W",
              "efficiency_shaft"):
        assert (k in c) == (s.get(k) is not None), k
        if k in c:
            assert c[k] == pytest.approx(s[k])


# ---------------------------------------------------------------------------
# The bearing temperature travels round the loop (2026-09-08)
# ---------------------------------------------------------------------------
# User: "when the coupled run runs, the WHOLE model must be solved, and ALL the
# losses must be carried into the electromagnetic calculation".  The bearings
# are ANALYTIC and temperature-dependent (M_rr goes as nu^0.6), so each pass has
# to take the seat temperature off the PREVIOUS pass's map and drive BOTH halves
# with it.  Tested with both solvers faked: the two halves each have their own
# expensive tests, and what is unproven here is the WIRING — which is exactly
# the thing a real 2-iteration solve would prove most slowly and least clearly.

@pytest.fixture
def faked_halves(monkeypatch):
    """``_em_run`` and ``_thermal_solve`` replaced by recorders.

    The maps hand back a rising shaft temperature so the feedback is visible,
    and the fake EM summary carries the mechanical fields a real run of a
    machine with bearings would carry.  ``_attach_coupling`` is stubbed because
    writing the block onto the last transient is a different claim with its own
    test above, and this one must not touch the run store at all.
    """
    from motor_ai_sim.routes import coupled as cp

    calls = {"em": [], "th": [], "ctx": []}
    shaft_c = [140.0, 150.0, 155.0, 158.0]

    def _em(body, *, coil_temp_c, magnet_temp_c, **_k):
        from motor_ai_sim import mech_losses as ml
        # The ContextVar is what carries it through `get_fem_transient` and
        # `_build_transient_summary`; if it is not visible HERE it is not
        # visible to the arithmetic that needs it either.  The LOOP sets it —
        # deliberately not this function, which is exactly why replacing this
        # function does not break the mechanism.
        t = ml.BEARING_TEMP_C.get()
        calls["em"].append(t)
        calls["ctx"].append(t)
        return {"summary": {
            "P_loss_total_W": 100.0, "T_em_avg_Nm": 5.0,
            "coil_temp_C": coil_temp_c,
            "bearing_temp_c": (None if t is None else round(float(t), 2)),
            "bearing_temp_source": ("coupled" if t is not None else "assigned"),
            "P_bearings_W": 40.0 + len(calls["em"]),
            "P_windage_W": 1.5,
            "P_mech_extra_W": 41.5 + len(calls["em"]),
            "P_loss_total_incl_mech_W": 141.5 + len(calls["em"]),
            "efficiency_shaft": 0.88,
        }}

    def _th(body, cooling, *, coil_temp_c, magnet_temp_c, rpm,
            bearing_temp_c=None, **_k):
        calls["th"].append(bearing_temp_c)
        i = len(calls["th"]) - 1
        return {"ok": True,
                "components": {"winding": {"avg": 120.0 + 10.0 * (i + 1),
                                           "max": 140.0},
                               "magnet": {"avg": 90.0, "max": 95.0},
                               "shaft": {"avg": 100.0, "max": 110.0}},
                "cooling": {"shaft_ends": {"t_shaft_mean_c": shaft_c[i]}}}

    monkeypatch.setattr(cp, "_em_run", _em, raising=True)
    monkeypatch.setattr(cp, "_thermal_solve", _th, raising=True)
    monkeypatch.setattr(cp, "_attach_coupling", lambda em, block: False,
                        raising=True)
    monkeypatch.setattr(cp, "_remember_last", lambda out, **k: None, raising=True)
    return calls


def test_the_bearing_temperature_is_fed_back_into_both_halves(
        client, faked_halves):
    """THE claim, for the third temperature.

    Pass 1 sends ``None`` — "resolve it the way any other run does", i.e. the
    machine's own ``bearings.temp_c`` or its last map.  Every pass after that
    sends the SHAFT of the previous map, to the electromagnetic half (through
    the ContextVar) and to the thermal half (through the argument) alike.  Both,
    or the friction the map carries would be a different bearing from the one
    the summary is billed at.
    """
    body = {**EM_BODY, "thermal_settings": COOLING, "max_iter": 3,
            "tol_k": 0.01, "damping": 0.5}
    r = client.post("/api/coupled/run", json=body)
    assert r.status_code == 200, r.text[:800]
    c = r.json()["coupling"]

    # the EM half saw it where the summary arithmetic lives (the ContextVar)…
    assert faked_halves["ctx"] == [None, 140.0, 150.0]
    # …and the thermal half got the very same number, through its argument.
    assert faked_halves["th"] == [None, 140.0, 150.0]

    rows = c["history"]
    assert [row["bearing_temp_c"] for row in rows] == [None, 140.0, 150.0]
    assert [row["bearing_temp_source"] for row in rows] == [
        "assigned", "coupled", "coupled"]
    assert [row["P_mech_extra_W"] for row in rows] == [42.5, 43.5, 44.5]


def test_the_reported_mechanical_numbers_are_the_last_runs(client,
                                                           faked_halves):
    """The block quotes the LAST electromagnetic run, like every other number in
    it — a mechanical loss one iteration out of step would contradict the cards
    it sits beside."""
    body = {**EM_BODY, "thermal_settings": COOLING, "max_iter": 2,
            "tol_k": 0.01, "damping": 0.5}
    c = client.post("/api/coupled/run", json=body).json()["coupling"]

    assert c["bearing_temp_c"] == 140.0
    assert c["bearing_temp_source"] == "coupled"
    assert c["P_mech_extra_W"] == pytest.approx(43.5)
    assert c["P_bearings_W"] == pytest.approx(42.0)
    assert c["efficiency_shaft"] == pytest.approx(0.88)
    assert "exposed ends" in c["bearing_temp_note"]
    # …and the convergence test is untouched: it is still the winding and the
    # magnet that decide, not the bearing seat.
    assert c["converged"] is False and c["tol_K"] == 0.01
    assert c["residual_coil_K"] is not None


def test_a_machine_with_no_bearings_grows_no_mechanical_keys(client,
                                                             monkeypatch):
    """ABSENT, not zero — at the loop's level too.

    A ``P_mech_extra_W: 0`` in the coupling block would put a bearing loss
    nobody measured on the Electromagnetic tab's card, which is the exact
    failure the house rule exists to prevent.
    """
    from motor_ai_sim.routes import coupled as cp

    monkeypatch.setattr(
        cp, "_em_run",
        lambda body, *, coil_temp_c, magnet_temp_c, **k: {
            "summary": {"P_loss_total_W": 100.0, "T_em_avg_Nm": 5.0}},
        raising=True)
    monkeypatch.setattr(
        cp, "_thermal_solve",
        lambda body, cooling, *, coil_temp_c, magnet_temp_c, rpm,
        bearing_temp_c=None, **k: {
            "ok": True,
            "components": {"winding": {"avg": 130.0, "max": 140.0},
                           "magnet": {"avg": 90.0, "max": 95.0}}},
        raising=True)
    monkeypatch.setattr(cp, "_attach_coupling", lambda em, block: False,
                        raising=True)
    monkeypatch.setattr(cp, "_remember_last", lambda out, **k: None, raising=True)

    body = {**EM_BODY, "thermal_settings": COOLING, "max_iter": 1}
    c = client.post("/api/coupled/run", json=body).json()["coupling"]
    for k in ("bearing_temp_c", "P_bearings_W", "P_windage_W",
              "P_mech_extra_W", "efficiency_shaft"):
        assert k not in c, k
    # the history row still HAS the column; it is None, which means unknown
    assert c["history"][0]["bearing_temp_c"] is None
    assert c["history"][0]["P_mech_extra_W"] is None


def test_a_map_with_no_shaft_leaves_the_bearing_temperature_alone(
        client, monkeypatch):
    """A cross-section whose shaft is excluded from the model has no seat to
    read, so the loop keeps what it had rather than inventing one.  ``None``
    all the way through means every pass resolved it the ordinary way — from
    the machine — which is the honest fallback."""
    from motor_ai_sim.routes import coupled as cp

    seen = []

    def _em(body, *, coil_temp_c, magnet_temp_c, **_k):
        from motor_ai_sim import mech_losses as ml
        seen.append(ml.BEARING_TEMP_C.get())
        return {"summary": {"P_loss_total_W": 100.0, "T_em_avg_Nm": 5.0}}

    monkeypatch.setattr(cp, "_em_run", _em, raising=True)
    monkeypatch.setattr(
        cp, "_thermal_solve",
        lambda body, cooling, *, coil_temp_c, magnet_temp_c, rpm,
        bearing_temp_c=None, **k: {
            "ok": True,
            # no shaft component and no shaft_ends block at all
            "components": {"winding": {"avg": 130.0 + len(seen), "max": 140.0},
                           "magnet": {"avg": 90.0, "max": 95.0}}},
        raising=True)
    monkeypatch.setattr(cp, "_attach_coupling", lambda em, block: False,
                        raising=True)
    monkeypatch.setattr(cp, "_remember_last", lambda out, **k: None, raising=True)

    body = {**EM_BODY, "thermal_settings": COOLING, "max_iter": 2,
            "tol_k": 0.01}
    r = client.post("/api/coupled/run", json=body)
    assert r.status_code == 200, r.text[:400]
    assert seen == [None, None]


def test_a_runaway_map_gets_no_rotor_stress_verdict(client, monkeypatch):
    """2026-09-09: the 40 mm at its peak in still air ran away to 658 °C and the
    mechanical step then solved the rotor at 658 °C — SF 0.05 on the magnets,
    which reads as "the magnets fail" when the answer is "there is no
    equilibrium".  A runaway map is not a state the machine can be in, so the
    step is recorded as a refusal and the solver is never called."""
    from motor_ai_sim.routes import coupled as cp
    from motor_ai_sim.routes import mechanical as mech

    hook_calls = []
    monkeypatch.setattr(mech, "run_rotor_stress_at",
                        lambda temps, **kw: hook_calls.append(temps) or {},
                        raising=True)
    monkeypatch.setattr(
        cp, "_em_run",
        lambda body, *, coil_temp_c, magnet_temp_c, **k: {
            "summary": {"P_loss_total_W": 189.2, "T_em_avg_Nm": 1.096}},
        raising=True)
    monkeypatch.setattr(
        cp, "_thermal_solve",
        lambda body, cooling, *, coil_temp_c, magnet_temp_c, rpm,
        bearing_temp_c=None, **k: {
            "ok": True,
            "components": {"winding": {"avg": 761.2, "max": 764.7},
                           "magnet": {"avg": 657.7, "max": 660.7},
                           "rotor": {"avg": 658.0, "max": 660.0},
                           "shaft": {"avg": 643.3, "max": 650.0}}},
        raising=True)
    monkeypatch.setattr(cp, "_attach_coupling", lambda em, block: False,
                        raising=True)
    monkeypatch.setattr(cp, "_remember_last", lambda out, **k: None, raising=True)

    body = {**EM_BODY, "thermal_settings": COOLING, "max_iter": 4,
            "mechanical": True}
    r = client.post("/api/coupled/run", json=body)
    assert r.status_code == 200, r.text[:400]
    c = r.json()["coupling"]
    assert c["runaway"] is True and c["iterations"] == 1
    assert hook_calls == []                       # never solved at 658 °C
    assert c["mechanical"]["ok"] is False
    assert "runaway" in c["mechanical"]["error"]


def test_a_later_pass_s_refusal_keeps_the_last_solved_pass(client, monkeypatch):
    """2026-09-09 00:12: the 40 mm L20/peak's first map put the magnets at
    243.6 °C; the second electromagnetic run refused (+124 K past the card's
    linear model) and the loop answered 500 — two minutes of solving and a
    valid first pass thrown away.  A refusal on a LATER pass now ends the loop
    like a runaway does: the last pass that solved is the answer, the refusal
    is the warning, verbatim, and the mechanics are solved at THAT map."""
    from fastapi import HTTPException
    from motor_ai_sim.routes import coupled as cp
    from motor_ai_sim.routes import mechanical as mech

    hook_calls = []
    monkeypatch.setattr(
        mech, "run_rotor_stress_at",
        lambda temps, **kw: hook_calls.append(dict(temps)) or {
            "primary_case": "25,000 rpm", "rpm": 25000.0,
            "cases": {"25,000 rpm": {"sf_min": 1.9, "sf_min_part": "magnet"}}},
        raising=True)
    em_calls = []

    def _em(body, *, coil_temp_c, magnet_temp_c, **_k):
        em_calls.append((coil_temp_c, magnet_temp_c))
        if len(em_calls) == 2:
            raise HTTPException(status_code=500, detail=(
                "sliding-band transient failed: magnet 'F52SH_120C': 243.6 °C "
                "is +124 K from the card's 120 °C reference"))
        return {"summary": {"P_loss_total_W": 189.2, "T_em_avg_Nm": 1.096}}

    monkeypatch.setattr(cp, "_em_run", _em, raising=True)
    monkeypatch.setattr(
        cp, "_thermal_solve",
        lambda body, cooling, *, coil_temp_c, magnet_temp_c, rpm,
        bearing_temp_c=None, **k: {
            "ok": True,
            "components": {"winding": {"avg": 231.0, "max": 240.0},
                           "magnet": {"avg": 243.6, "max": 245.0},
                           "rotor": {"avg": 243.0, "max": 244.0},
                           "shaft": {"avg": 230.0, "max": 235.0}}},
        raising=True)
    monkeypatch.setattr(cp, "_attach_coupling", lambda em, block: False,
                        raising=True)
    monkeypatch.setattr(cp, "_remember_last", lambda out, **k: None, raising=True)

    body = {**EM_BODY, "thermal_settings": COOLING, "max_iter": 4,
            "damping": 1.0, "mechanical": True}
    r = client.post("/api/coupled/run", json=body)
    assert r.status_code == 200, r.text[:400]
    c = r.json()["coupling"]
    assert len(em_calls) == 2                       # the refused pass was attempted
    assert c["iterations"] == 1 and c["converged"] is False
    assert c["runaway"] is False
    assert "refused" in c["warning"] and "+124 K" in c["warning"]
    # the reported pair is the one the LAST SOLVED run was made at…
    assert c["coil_temp_c"] == pytest.approx(EM_BODY.get("coil_temp_c", 120.0))
    # …and the mechanics were solved for that pass, not skipped, at THAT
    # map's numbers.  This map has no sleeve, so the solver treats them as no
    # load (user 2026-09-09: "температура только как изменение давления на
    # бандаж, если он есть") and the block says so in one line — the numbers
    # themselves still go through, because the user wants the coupling to
    # carry real ones everywhere.
    assert hook_calls and hook_calls[0]["magnet"] == pytest.approx(243.6)
    assert c["mechanical"]["ok"] is True
    assert "no retaining band" in c["mechanical"]["temps_note"]
    assert "244" in c["mechanical"]["temps_note"] or "243" in c["mechanical"]["temps_note"]


def test_a_first_pass_refusal_is_still_the_route_s_own_error(client, monkeypatch):
    """Nothing solved = nothing to keep: the transient's refusal passes through
    with its status, exactly as before."""
    from fastapi import HTTPException
    from motor_ai_sim.routes import coupled as cp

    def _em(body, *, coil_temp_c, magnet_temp_c, **_k):
        raise HTTPException(status_code=422, detail={"error": "no magnets"})

    monkeypatch.setattr(cp, "_em_run", _em, raising=True)
    body = {**EM_BODY, "thermal_settings": COOLING, "max_iter": 2}
    r = client.post("/api/coupled/run", json=body)
    assert r.status_code == 422
    assert client.get("/api/coupled/progress").json()["running"] is False
