"""The Thermal tab's contract — /api/thermal/{field,coupled,last,mesh}.

The steady thermal map used to be one route among sixty in the simulation
router (``GET /api/simulation/physics/thermal_field2d``): no last-result store,
no mesh preview, no timings, and no gate.  It moved to its own router on
2026-09-07, modelled 1:1 on ``routes.mechanical``, and these tests pin the
promises that move (and the two same-day passes after it) made — the ones a
panel is now allowed to rely on:

  (a) before anything is solved ``/last`` says so with a 200, so the tab draws
      the bare cross-section instead of a blank page and the console instead of
      a red 404;
  (b) ``/mesh`` returns the SOLID sub-mesh the solve runs on — air dropped, the
      sliding band welded — without solving anything;
  (c) ``/field`` answers a temperature map that obeys the first law (nothing is
      colder than its coolant), names its components, and reports the seconds it
      cost;
  (d) ``/coupled`` iterates the winding temperature rather than assuming it, and
      stops where it was told to;
  (e) a malformed ``?geo=`` and an impossible cooling spec are REFUSED by name
      (422), never solved as something else;
  (f) the insulation and the air the solve actually uses are named domains with
      temperatures and stated conductivities (2026-09-07), not white space;
  (g) THE TWO SOLVERS ARE SEPARATE (2026-09-07, same day, third pass — user:
      *"нужно как-то разделить тепловые расчёты и электромагнитные; если вдруг
      тепловому расчёту нужно электромагнитное моделирование, пусть оно делается
      во вкладке Simulation"*).  ``/field`` and ``/coupled`` never start an
      electromagnetic solve of any kind: the cycle-averaged loss map comes from
      an Electromagnetic RUN the user made, and with no matching run the answer
      is a 422 naming the point to run — not a six-minute solve behind a
      temperature request.

Everything runs on the 30 mm 12s/14p fixture through a per-request ``?geo=``
override, on a deliberately coarse mesh, over the machine's natural half-wedge:
this suite is about the CONTRACT, and a converged 200 mm machine would buy
nothing here at many times the wall clock.  What costs is the ELECTROMAGNETIC
RUN every temperature map is now answered from — two of them, one plain and one
sleeved, solved once for the whole module by the ``em_runs`` fixture; every
/field call below is then a snapshot replay plus a conduction solve.
"""
from __future__ import annotations

import json
import math
import pathlib
import tempfile
import time

import pytest

# The 30 mm 12s/14p spoke machine the physics-regression suite is pinned on
# (tests/test_daxis_calibration.py::GEO_30MM), passed as a per-request override
# so nothing on disk is touched.  Small on purpose — the Electromagnetic run
# every /field call below is answered from is a real transient.
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


# The 30 mm fixture with the gap opened to 1.0 mm so a 0.4 mm ring fits (see
# tests/test_sleeve.py: 0.2 mm is not a gap a sleeve can live in — the sliding
# band alone needs 0.12 mm of it).
GEO_SLEEVED = dict(GEO_30MM, air_gap=1.0, sleeve_thickness=0.4)


def _geo() -> str:
    import json
    return json.dumps(GEO_30MM)


# The cheapest honest CYCLE: four frames over one electrical period on a coarse
# mesh over the machine's natural half-wedge.  Four, not one — a single frame has
# no B(t) history, so it is not a cycle average and no Electromagnetic run can
# ever match it (the route refuses it by name; see the 422 tests below).  The
# physics is not the claim in this module; the response shape is, and a converged
# 24-frame map would buy nothing here at six times the wall clock.
FAST = {
    "n_steps_per_period": 4, "n_periods": 1.0,
    "mesh_size_mm": 2.5, "min_size_mm": 0.6, "n_sectors": 2,
    "I_phase_rms": 20.0, "rpm": 3000.0, "coil_temp_c": 120.0,
}

#: The Electromagnetic runs the maps below are built from.  Named (rather than
#: timestamped at import) so a failure says WHICH run the payload claims.
EM_RUN_ID = "2026-09-07T09:00:00"
EM_RUN_ID_SLEEVED = "2026-09-07T09:05:00"


def _field_params(**over) -> dict:
    p = {**FAST, "geo": _geo()}
    p.update(over)
    return p


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# The Electromagnetic run every temperature map below is answered from
# ---------------------------------------------------------------------------

def store_em_run(geo_dict: dict, *, run_id: str, phys: dict = None) -> dict:
    """Perform ONE Electromagnetic run and park it where a Run parks one.

    Shared with tests/test_thermal_loss_reuse.py and tests/test_progress_routes.py
    — all three modules now need a real run to have a temperature at all.

    Not a hand-built dict: the solve is the same ``fem_transient_sliding_band``
    call ``routes.simulation._fem_field2d_impl`` makes for a multi-frame view,
    and the entry goes in through ``_store_transient_field_snapshot`` under the
    key ``routes.thermal._loss_snapshot_probe`` builds.  That handshake IS the
    product's seam between the two tabs; a fake entry would test this module's
    idea of a run instead of the run.

    The caller must have redirected ``simulation._transient_field_snap`` and its
    pickle first (see ``em_runs``): ``config/.last_transient_field.pkl`` is the
    user's own last run and this suite promises not to touch it.
    """
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.routes import thermal as th
    from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

    p = dict(FAST if phys is None else phys)
    geo_json = json.dumps(geo_dict)
    geo_ov = sim._parse_geo_override(geo_json)
    # The thermal route auto-refines the COIL mesh before it does anything else,
    # so the run it will look for is the one solved on THAT mesh.
    cm = th._auto_coil_mesh("", p["mesh_size_mm"], geo_ov)
    ns_eff = th._snap_n_sectors(p["n_sectors"], geo_ov)

    d = fem_transient_sliding_band(
        n_steps_per_period=int(p["n_steps_per_period"]),
        n_periods=float(p["n_periods"]),
        gamma_deg=float(p.get("gamma_deg", 0.0)),
        I_phase_rms=float(p["I_phase_rms"]),
        mesh_size_mm=float(p["mesh_size_mm"]),
        min_size_mm=float(p["min_size_mm"]),
        outer_air_factor=1.3, n_sectors=int(ns_eff), stator_fillet_mm=0.0,
        coil_temp_c=float(p["coil_temp_c"]),
        eddy=True, rotor_eddy=True, demag=False,
        return_field=True, field_first=False, rotor_angle0_deg=0.0,
        pole_copy=False, iron_template=True, geo_mesh=True,
        structured_gap=True, airgap_macro=False, gap_layers=2.0,
        component_mesh_mm=sim._parse_component_mesh(cm),
        geo_override=geo_ov, element_order=2)
    d["computed_at"] = run_id

    probe = th._loss_snapshot_probe(
        gamma_deg=p.get("gamma_deg", 0.0), I_phase_rms=p["I_phase_rms"],
        mesh_size_mm=p["mesh_size_mm"], min_size_mm=p["min_size_mm"],
        outer_air_factor=1.3, n_sectors=p["n_sectors"],
        coil_temp_c=p["coil_temp_c"], component_mesh=cm,
        n_steps_per_period=p["n_steps_per_period"],
        n_periods=p["n_periods"], geo_ov=geo_ov)
    sim._store_transient_field_snapshot(
        tuple(probe.values()), d["field"], d, eddy=True,
        n_steps_per_period=int(p["n_steps_per_period"]),
        n_periods=float(p["n_periods"]), solve_time_s=1.0, key_fields=probe)
    return {"probe": probe, "run_id": run_id, "solver_result": d,
            "geo": geo_json}


@pytest.fixture(scope="module")
def em_runs():
    """Two Electromagnetic runs — plain and sleeved — solved ONCE per module.

    Module-scoped because these are the only genuine FEM solves left in the
    suite: since 2026-09-07 a temperature map is a snapshot replay plus a
    conduction solve, so paying for the runs once and answering thirty tests
    from them is the whole shape of this module.

    The snapshot store and its pickle are redirected and restored around the
    module, for the reason in ``store_em_run``.
    """
    from motor_ai_sim.routes import simulation as sim

    mp = pytest.MonkeyPatch()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="thermal_routes_em_"))
    mp.setattr(sim, "_transient_field_store_path", lambda: str(tmp / ".snap.pkl"))
    saved = dict(sim._transient_field_snap)
    sim._transient_field_snap.clear()

    runs = {"plain": store_em_run(GEO_30MM, run_id=EM_RUN_ID),
            "sleeved": store_em_run(GEO_SLEEVED, run_id=EM_RUN_ID_SLEEVED)}
    time.sleep(0.3)          # the persist is a daemon thread — let it land

    yield runs

    sim._transient_field_snap.clear()
    sim._transient_field_snap.update(saved)
    mp.undo()


@pytest.fixture(autouse=True)
def _isolate_last_store(tmp_path, monkeypatch):
    """Empty the last-result store and send its pickle to a tmp file.

    Both halves matter, exactly as in tests/test_mechanical_last.py.  The
    in-memory dict is module state a solve earlier in the session may have
    filled, and the pickle would otherwise land in ``config/`` — beside the
    machine the user has open — and be restored by the lazy loader, which would
    make "nothing solved yet" untestable and write to a directory this suite
    promises not to touch.

    The router's OWN loss-map store (2026-09-07) gets the same treatment and for
    the same reason: it is now written on every answer served from an
    Electromagnetic run, and its pickle lives beside the user's config too.
    Emptied per test as well as redirected, so a map one test was handed is not
    quietly "remembered" into the next one's claim about where its map came from.
    """
    from motor_ai_sim.routes import thermal as th

    monkeypatch.setattr(th, "_LAST", {}, raising=True)
    monkeypatch.setattr(th, "_LAST_LOADED", True, raising=True)   # skip the disk
    monkeypatch.setattr(th, "_last_store_path",
                        lambda: str(tmp_path / ".last_thermal.pkl"))
    monkeypatch.setattr(th, "_loss_maps_path",
                        lambda: str(tmp_path / ".loss_maps.pkl"))
    monkeypatch.setattr(th, "_LOSS_MAPS_LOADED", True, raising=True)
    th._LOSS_MAPS.clear()
    yield
    th._LOSS_MAPS.clear()


# ---------------------------------------------------------------------------
# (a) the unsolved tab
# ---------------------------------------------------------------------------

def test_last_is_empty_before_anything_is_solved(client):
    """200 + ``has_result: false``, never a 404.

    An unsolved Thermal tab is the NORMAL first state — it draws the bare mesh —
    and a console full of red 404s on every fresh mount is not an error report.
    """
    r = client.get("/api/thermal/last")
    assert r.status_code == 200, r.text[:400]
    out = r.json()
    assert out["has_result"] is False
    assert out["field"] is None and out["coupled"] is None
    # Always answered, even with nothing stored: it is what a later result will
    # be compared against.
    assert isinstance(out["live_geometry_fingerprint"], str)


# ---------------------------------------------------------------------------
# (b) the mesh the solve runs on
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mesh_payload(client):
    r = client.get("/api/thermal/mesh",
                   params={"mesh_size_mm": 2.5, "min_size_mm": 0.6,
                           "n_sectors": 2, "geo": _geo()})
    assert r.status_code == 200, r.text[:600]
    return r.json()


def test_mesh_returns_a_finite_solid_cross_section(mesh_payload):
    m = mesh_payload
    assert m["n_triangles"] > 50 and m["n_vertices"] > 50
    assert len(m["triangles"]) == m["n_triangles"]
    assert len(m["vertices"]) == m["n_vertices"]
    assert len(m["domain_per_tri"]) == m["n_triangles"]
    for x, y in m["vertices"]:
        assert math.isfinite(x) and math.isfinite(y)
    assert len(m["extent"]) == 4
    x0, x1, y0, y1 = m["extent"]
    assert x1 > x0 and y1 > y0
    assert m["mesh_s"] > 0 and m["elapsed_s"] > 0
    assert m["cached"] is False
    assert isinstance(m["geo_fingerprint"], str)


def test_mesh_carries_no_air_because_air_conducts_nothing(mesh_payload):
    """The whole point of the route: it is not the EM mesh.

    ``solve_steady_thermal`` drops the far-field ring, the slip band and the
    bore air so the mesh BOUNDARY is the cooled hardware — that is where
    convection acts — and the preview must show the same thing or it is a
    picture of a different problem.  Since 2026-09-07 the air that is INSIDE the
    machine is kept and named instead (the liner, the enamel, the wire coating, the
    gap and the rotor pockets); what may not survive is the anonymous "air" tag,
    which is what this asserts.
    """
    from motor_ai_sim.routes.thermal import DROP_TAGS, PART_NAMES

    m = mesh_payload
    tags = set(m["domain_per_tri"])
    assert tags, "an empty cross-section is not a preview"
    assert not (tags & set(DROP_TAGS)), sorted(tags & set(DROP_TAGS))
    for t in tags:
        assert str(t) in m["part_names"], t
        assert PART_NAMES[t] not in ("air", "airgap", "band", "outer_air")
    # index sanity — the viewer indexes straight into `vertices`
    n = m["n_vertices"]
    for tri in m["triangles"]:
        assert len(tri) == 3
        for i in tri:
            assert 0 <= i < n
    assert m["outlines"], "no outlines — the picture would have no part boundaries"


def test_a_second_mesh_at_the_same_size_is_a_cache_hit(client, mesh_payload):
    again = client.get("/api/thermal/mesh",
                       params={"mesh_size_mm": 2.5, "min_size_mm": 0.6,
                               "n_sectors": 2, "geo": _geo()}).json()
    assert again["cached"] is True
    assert again["n_triangles"] == mesh_payload["n_triangles"]
    # `mesh_s` stays what the build actually cost — a cache hit did not make
    # gmsh faster, it skipped it.
    assert again["mesh_s"] == pytest.approx(mesh_payload["mesh_s"])


def test_mesh_does_not_solve_anything(client):
    """The unsolved tab draws this — it must not fill the last-result store."""
    from motor_ai_sim.routes import thermal as th

    th._LAST.clear()
    assert client.get("/api/thermal/mesh",
                      params={"mesh_size_mm": 2.8, "min_size_mm": 0.6,
                              "n_sectors": 2, "geo": _geo()}).status_code == 200
    assert client.get("/api/thermal/last").json()["has_result"] is False


# ---------------------------------------------------------------------------
# (c) the temperature map
# ---------------------------------------------------------------------------

AIR = dict(cooling_mode="air", ambient_temp=30.0, air_speed_mps=5.0)


@pytest.fixture(scope="module")
def air_first(client, em_runs):
    """The very FIRST air-cooled solve of the module, kept as it came back.

    Module-scoped so ``cached is False`` and the seconds it reports are the ones
    a genuinely fresh solve produced — the claim the timing tests make.
    """
    r = client.get("/api/thermal/field", params=_field_params(**AIR))
    assert r.status_code == 200, r.text[:800]
    return r.json()


@pytest.fixture
def air_field(client, air_first):
    """One air-cooled map, re-requested per test.

    NOT module-scoped, on purpose: ``_isolate_last_store`` empties the
    last-result store before every test, so a solve that happened during another
    test's fixture would leave ``/last`` empty here.  The repeat costs nothing —
    the FIRST call is the only real solve, the rest are cache hits — and the tests
    that care about that fact assert it explicitly below.
    """
    r = client.get("/api/thermal/field", params=_field_params(**AIR))
    assert r.status_code == 200, r.text[:800]
    return r.json()


def test_air_cooled_field_is_a_physical_temperature_map(air_first):
    """Nothing in a heated machine is colder than the air cooling it.

    Weak on purpose — this suite pins the CONTRACT, not the physics — but it is
    the one assertion that catches a sign flip, a Kelvin/Celsius slip or a
    solve that quietly returned the ambient array.
    """
    f = air_first
    assert f["ok"] is True
    assert f["T_max"] >= f["ambient_temp"]
    assert f["T_min"] >= f["ambient_temp"] - 1.0     # 1 K of solver slack
    assert f["T_max"] >= f["T_min"]
    assert f["n_vertices"] > 0 and f["n_triangles"] > 0
    assert len(f["temperature_per_node"]) == f["n_vertices"]
    assert len(f["heat_flux_per_tri"]) == f["n_triangles"]
    assert f["cooling"]["outer"]["mode"] == "air"
    assert f["cooling"]["outer"]["regime"] in ("forced", "natural")
    # the bore is a first-class surface and is REPORTED even when it is off:
    # "not cooled" and "the key never got written" are different statements
    assert f["cooling"]["inner"]["mode"] == "none"


def test_the_field_names_its_components(air_first):
    """The table beside the picture: the metals, and since 2026-09-07 the air.

    The winding entry is the one the coupled loop reads, so a machine whose
    slot meshed to nothing would silently make /coupled a no-op — assert the
    keys exist and that the ones that are there carry both numbers.  The set is
    asserted as a SUPERSET of the four metals: the insulation and air rows
    (liner / enamel / wire coating / gap air / pocket air) are present on every
    machine but are ``None`` on a mesh too coarse to resolve them, and pinning
    the exact set would make adding a part a test edit rather than a feature.
    """
    comps = air_first["components"]
    assert {"winding", "magnet", "stator", "rotor"} <= set(comps)
    assert {"liner", "enamel", "slot_fill", "gap_air", "pocket_air"} <= set(comps)
    assert comps["winding"] is not None, "no winding elements — the hotspot is unmeasurable"
    for name, c in comps.items():
        if c is None:
            continue
        assert c["max"] >= c["avg"], name
        assert math.isfinite(c["max"]) and math.isfinite(c["avg"]), name


def test_every_solve_reports_the_seconds_it_took(air_first):
    """`elapsed_s` is the whole request, `solve_time_s` the conduction solve.

    A panel can time its own fetch, but that number includes the network and a
    payload carrying a temperature per node and a flux vector per triangle — so
    the honest figure is measured server-side.
    """
    assert air_first["cached"] is False
    assert air_first["elapsed_s"] > 0
    assert air_first["solve_time_s"] >= 0
    assert air_first["elapsed_s"] >= air_first["solve_time_s"]
    assert isinstance(air_first["geometry_fingerprint"], str)


def test_a_repeat_request_is_a_cache_hit_that_keeps_the_original_duration(
        client, air_first):
    """The panel prints "cached · solved in N s earlier" from these two fields.

    A hit that reset `solve_time_s` to its own microseconds would turn the
    estimate the timer shows next time into nonsense.
    """
    again = client.get("/api/thermal/field", params=_field_params(**AIR)).json()
    assert again["cached"] is True
    assert again["T_max"] == pytest.approx(air_first["T_max"])
    assert again["solve_time_s"] == pytest.approx(air_first["solve_time_s"])


def test_liquid_cooling_takes_a_flow_and_ANSWERS_with_the_outlet(client, em_runs):
    """The jacket model, the right way round (2026-09-07).

    It used to be INVERTED: the engineer typed the outlet temperature and the
    model answered with the flow rate it would take.  Nobody sizes a machine
    that way — the pump is a given and the outlet is what the machine does to
    the coolant — so ``flow_lpm`` + ``fluid_temp_in_c`` are now the inputs,
    ``t_out_c`` is the result, and the sink is the mean film temperature.
    """
    r = client.get("/api/thermal/field",
                   params=_field_params(cooling_mode="liquid", fluid="water",
                                        fluid_temp_in_c=40.0, flow_lpm=4.0))
    assert r.status_code == 200, r.text[:800]
    f = r.json()
    c = f["cooling"]["outer"]
    assert c["mode"] == "liquid" and c["fluid"] == "water"
    assert c["flow_lpm"] == pytest.approx(4.0)
    assert c["t_in_c"] == pytest.approx(40.0)
    assert c["t_out_c"] >= c["t_in_c"]                 # the coolant carries heat
    assert c["t_sink_c"] == pytest.approx(0.5 * (c["t_in_c"] + c["t_out_c"]),
                                          abs=0.2)
    assert c["re"] is not None and c["nu"] is not None
    # the sink the payload reports at the top level IS this surface's
    assert f["t_sink_c"] == pytest.approx(c["t_sink_c"], abs=0.5)
    assert f["T_max"] >= c["t_in_c"] - 1.0
    # the outlet loop is bounded and says how many passes it took
    assert 1 <= f["cooling"]["passes"] <= 4


def test_the_deprecated_outlet_and_gap_k_are_ignored_and_said_so(client, em_runs):
    """Old clients keep working — and are TOLD that two of their inputs are not.

    Silence would be the worse behaviour: a caller still sending
    ``fluid_temp_out_c`` believes it is choosing the outlet temperature, and the
    outlet temperature is now the answer.
    """
    r = client.get("/api/thermal/field",
                   params=_field_params(**AIR, fluid_temp_out_c=55.0,
                                        gap_k=0.5))
    assert r.status_code == 200, r.text[:800]
    notes = " ".join(r.json()["cooling"]["deprecated"])
    assert "fluid_temp_out_c is ignored" in notes
    assert "gap_k is ignored" in notes
    # …and gap_k really did NOT become the gap conductivity
    assert r.json()["gap_k"] != pytest.approx(0.5)


# ---------------------------------------------------------------------------
# (c2) the rotor's own heat path — the bore
# ---------------------------------------------------------------------------

def test_cooling_the_bore_cools_the_rotor(client, air_first):
    """User 2026-09-07: "Ротор придётся охлаждать в основном через вал".

    In a 2-D cross-section the rotor's only other route out is the air gap,
    whose effective conductivity is a few hundredths of a W/m·K — so blowing air
    down the shaft has to show up as a COLDER rotor, and as watts leaving through
    the bore that were not leaving before.
    """
    r = client.get("/api/thermal/field",
                   params=_field_params(**AIR, bore_mode="air",
                                        bore_air_speed_mps=40.0))
    assert r.status_code == 200, r.text[:800]
    f = r.json()
    inner = f["cooling"]["inner"]
    assert inner["mode"] == "air"
    assert inner["h_conv"] > 0 and inner["r_bore_mm"] > 0
    assert inner["heat_removed_W"] > 0
    assert f["n_bore_facets"] > 0
    assert air_first["cooling"]["inner"]["heat_removed_W"] == 0.0
    assert f["components"]["rotor"]["max"] < air_first["components"]["rotor"]["max"]


def test_liquid_through_the_shaft_cools_the_rotor_harder_still(client,
                                                               air_first):
    r = client.get("/api/thermal/field",
                   params=_field_params(**AIR, bore_mode="liquid",
                                        bore_fluid="water",
                                        bore_fluid_temp_in_c=30.0,
                                        bore_flow_lpm=2.0))
    assert r.status_code == 200, r.text[:800]
    f = r.json()
    inner = f["cooling"]["inner"]
    assert inner["mode"] == "liquid" and inner["fluid"] == "water"
    assert inner["flow_lpm"] == pytest.approx(2.0)
    assert inner["t_out_c"] >= inner["t_in_c"]
    assert inner["heat_removed_W"] > 0
    assert f["components"]["rotor"]["max"] < air_first["components"]["rotor"]["max"]


def test_the_heat_budget_closes(client, em_runs):
    """Watts in = watts out, to better than 2 %.

    Without this the temperature map is an assertion.  ``losses_W`` is ∫q dV over
    the sub-mesh the solve actually used; ``housing_W`` and ``bore_W`` are
    ∫h(T−T_sink)dA on their own facets; ``gap_W`` is what crosses the slip-line
    tie and is INTERNAL, so it is not part of the residual — it is the number
    that says how the rotor's heat splits between the gap and the shaft.

    ``abs=0.02`` beside the 2 % is the PAYLOAD's own resolution, not slack in
    the physics: this fixture is a 10 mm-long 30 mm machine making ~0.035 W in
    total, the budget is rounded to 0.01 W before it is serialised, and two
    rounded surfaces summed against one rounded total can differ by a whole
    rounding unit while ``residual_pct`` — computed before the rounding —
    reads 0.0.  That assertion above is the one that tests the closure.
    """
    r = client.get("/api/thermal/field",
                   params=_field_params(**AIR, bore_mode="air",
                                        bore_air_speed_mps=40.0))
    assert r.status_code == 200, r.text[:800]
    b = r.json()["cooling"]["heat_budget"]
    assert b["losses_W"] > 0
    assert b["residual_pct"] < 2.0, b
    # EVERY outflow, named.  The open-frame lines (2026-09-09) are 0 on a housed
    # machine and are written into the sum anyway: the invariant is "the sinks
    # add up to the losses", and a line left out of it is a line that can start
    # carrying watts without anything going red.
    assert b["end_windings_W"] == 0.0 and b["slot_channels_W"] == 0.0
    assert b["frame"] == "housed"
    assert (b["housing_W"] + b["bore_W"] + b["shaft_ends_W"]
            + b["end_windings_W"] + b["slot_channels_W"]
            == pytest.approx(b["losses_W"], rel=0.02, abs=0.02))
    assert b["bore_W"] > 0
    # the rotor's own split: what leaves across the gap is a real, signed number
    assert math.isfinite(b["gap_W"])
    assert b["symmetry_mult"] >= 1
    # 2026-09-07, second pass: the gap AIR is meshed now, so the rotor is not an
    # island any more and gap_W is what crosses the slip-line TIE — the pair of
    # coincident node sets the sliding band leaves at the slip radius — rather
    # than a lumped bridge across a hole.  (First pass: only ROTOR-side islands
    # counted as gap heat, because the coil blocks — islands once the slot air
    # was dropped — used to be bridged through gap air and summed into gap_W,
    # which then read 3.3 kW for a rotor making 0.77 kW.)
    assert b["n_slip_ties"] >= 1
    assert b["n_islands_rotor"] == 0
    # ...and the coils are not islands either: the wire coating (and, where the
    # mesh resolves them, the liner and the enamel) connects them to the tooth
    # walls with real elements, so the lumped liner bridge has nothing to do.
    assert b["n_islands_stator"] == 0
    assert b["slot_bridge_W"] == 0.0
    # `slot_liner_W` is now the winding's own boundary integral ∮q·n, which in
    # steady state must return the copper loss deposited inside it.
    assert math.isfinite(b["slot_liner_W"])
    assert b["coil_W"] == pytest.approx(b["slot_liner_W"])
    # Neither internal flow can exceed everything the machine makes.  The 0.02 W
    # allowance is the payload's rounding again (three numbers at 0.01 W on a
    # 0.035 W machine), not slack: on this fixture the bore blown at 40 m/s is
    # the stronger sink, so the gap runs stator→rotor and the two magnitudes
    # nearly add up to the whole loss.
    assert abs(b["gap_W"]) + abs(b["bore_W"]) <= b["losses_W"] + 0.02


# ---------------------------------------------------------------------------
# (c2') the rotor's THIRD heat path — the shaft ends outside the housing
# ---------------------------------------------------------------------------
# User 2026-09-07: *"торцы и лобовые части — только для вала, всё остальное
# вращается внутри мотора"*.  The rotor's end faces and the end windings spin
# inside a CLOSED housing and have nowhere else to send their heat — so nothing
# is modelled there, deliberately.  The SHAFT comes out through the bearings on
# both sides and those exposed stubs lose heat to the room, as a FIN.

SHAFT = dict(shaft_ext_length_mm=100.0, shaft_ext_sides=2)


def test_the_exposed_shaft_ends_cool_the_rotor(client, air_first):
    """A path off the rotor that does not cross the gap and is not the bore.

    Same shape of claim as the bore test above: turning it on has to show up as
    a COLDER rotor and as watts leaving somewhere they were not leaving before.
    """
    r = client.get("/api/thermal/field", params=_field_params(**AIR, **SHAFT))
    assert r.status_code == 200, r.text[:800]
    f = r.json()
    se = f["cooling"]["shaft_ends"]
    assert se["mode"] == "rotating shaft in air"
    assert se["sides"] == 2
    assert se["length_each_side_mm"] == pytest.approx(100.0)
    # Derived, because the request did not name a diameter: the shaft tube's OD
    # from the geometry (rotor_inner_radius = 9.0 − 4.5 − 0.8 = 3.7 mm).
    assert se["diameter_source"] in ("geometry", "measured on the shaft elements")
    assert se["diameter_mm"] == pytest.approx(7.4, abs=0.2)
    assert se["h_conv"] > 0 and se["re_omega"] > 0
    assert se["G_W_per_K"] > 0
    assert 0.0 < se["fin_efficiency"] <= 1.0
    assert se["heat_removed_W"] > 0
    assert se["n_elements"] > 0
    assert f["cooling"]["heat_budget"]["shaft_ends_W"] > 0
    # …and the rotor is colder than the same machine with nothing sticking out.
    assert (f["components"]["rotor"]["max"]
            < air_first["components"]["rotor"]["max"])
    assert air_first["cooling"]["shaft_ends"]["mode"] == "off"
    assert air_first["cooling"]["heat_budget"]["shaft_ends_W"] == 0.0


def test_the_shaft_ends_close_the_budget_and_report_their_own_conductance(
        client, em_runs):
    """The identity that makes the sink checkable: P = G·(T_shaft − T_ambient).

    A lumped sink is the one part of this model with no facets to integrate, so
    the only thing standing between it and a fudge factor is that both sides of
    that equation are in the payload — and that the whole budget still closes
    with the sink's watts counted as an OUTFLOW.
    """
    r = client.get("/api/thermal/field", params=_field_params(**AIR, **SHAFT))
    assert r.status_code == 200, r.text[:800]
    f = r.json()
    se = f["cooling"]["shaft_ends"]
    b = f["cooling"]["heat_budget"]

    assert b["residual_pct"] < 2.0, b
    assert (b["housing_W"] + b["bore_W"] + b["shaft_ends_W"]
            + b["end_windings_W"] + b["slot_channels_W"]
            == pytest.approx(b["losses_W"], rel=0.02, abs=0.02))
    # G·ΔT in MACHINE watts — the symmetry bookkeeping of the volume sink, which
    # is the one thing a wedge solve can silently get wrong by a factor of N.
    assert se["t_shaft_mean_c"] is not None
    assert b["shaft_ends_W"] == pytest.approx(
        se["G_W_per_K"] * (se["t_shaft_mean_c"] - se["t_sink_c"]),
        rel=0.02, abs=0.005)
    # The rotor's own split: everything it makes leaves across the gap, through
    # the bore, or down the shaft ends — and nowhere else.
    assert (b["gap_W"] + b["bore_W"] + b["shaft_ends_W"]
            == pytest.approx(b["rotor_W"], rel=0.05, abs=0.02))


def test_one_capped_end_is_half_the_path(client, em_runs):
    """``shaft_ext_sides`` is a real design choice, not decoration.

    A through-shaft has two stubs in the air; a machine whose non-drive end is
    capped has one, and the conductance is exactly half — the fin is the same
    fin twice.
    """
    two = client.get("/api/thermal/field",
                     params=_field_params(**AIR, **SHAFT)).json()
    one = client.get("/api/thermal/field",
                     params=_field_params(**AIR, shaft_ext_length_mm=100.0,
                                          shaft_ext_sides=1)).json()
    g2 = two["cooling"]["shaft_ends"]["G_W_per_K"]
    g1 = one["cooling"]["shaft_ends"]["G_W_per_K"]
    assert g1 == pytest.approx(0.5 * g2, rel=1e-3)
    assert one["components"]["rotor"]["max"] > two["components"]["rotor"]["max"]


def test_a_longer_stub_never_cools_less(client, em_runs):
    """Monotone in the exposed length, and saturating — it is a fin.

    The number that must not appear is a stub that cools WORSE as it grows,
    which is what a sign error in the fin's tanh would produce.
    """
    ws = []
    for mm in (10.0, 30.0, 100.0):
        f = client.get("/api/thermal/field",
                       params=_field_params(**AIR, shaft_ext_length_mm=mm,
                                            shaft_ext_sides=2)).json()
        ws.append(f["cooling"]["shaft_ends"]["G_W_per_K"])
    assert ws == sorted(ws), ws
    assert ws[0] < ws[-1]


def test_the_shaft_parameters_ride_back_through_last(client, em_runs):
    """Re-entering the tab restores the INPUT fields, this one included.

    An exposed-shaft length that is not on screen beside the answer is a
    boundary condition nobody can reproduce — the same rule the cooling modes
    already follow.
    """
    r = client.get("/api/thermal/field",
                   params=_field_params(**AIR, shaft_ext_length_mm=100.0,
                                        shaft_ext_sides=1))
    assert r.status_code == 200, r.text[:800]
    p = client.get("/api/thermal/last",
                   params={"field": False}).json()["field"]["params"]
    assert p["shaft_ext_length_mm"] == pytest.approx(100.0)
    assert p["shaft_ext_sides"] == 1
    assert p["shaft_ext_diameter_mm"] == pytest.approx(0.0)


def test_a_cross_section_with_no_shaft_refuses_the_shaft_path(client, em_runs,
                                                              monkeypatch):
    """NAMED, never silently dropped.

    A machine whose shaft is excluded from the model has no elements to attach
    the sink to.  Answering anyway would hand back a map that reads as if the
    stubs were cooling something — the exact "solve a machine nobody built"
    failure this router refuses everywhere else.
    """
    from motor_ai_sim.routes import thermal as th

    _real = th._retag_thermal_domains

    def _no_shaft(verts, tris, tags, polys, **kw):
        import numpy as _np
        out, rep = _real(verts, tris, tags, polys, **kw)
        out = _np.asarray(out).copy()
        out[out == th.DOM_SHAFT] = 5            # the shaft part, excluded
        return out, rep

    monkeypatch.setattr(th, "_retag_thermal_domains", _no_shaft)
    # A length no other test in this module used: the field cache is keyed on
    # the request, not on the mesh, so re-asking a question that was already
    # answered would be served the cached 200 and this would test nothing.
    r = client.get("/api/thermal/field",
                   params=_field_params(**AIR, shaft_ext_length_mm=63.0,
                                        shaft_ext_sides=2))
    d, fields = _detail(r)
    assert fields == ["shaft_ext_length_mm"]
    assert "no shaft elements" in d["error"]


@pytest.mark.parametrize("params,field", [
    ({"shaft_ext_length_mm": -5.0}, "shaft_ext_length_mm"),
    ({"shaft_ext_diameter_mm": -1.0}, "shaft_ext_diameter_mm"),
    ({"shaft_ext_length_mm": 50.0, "shaft_ext_sides": 3}, "shaft_ext_sides"),
])
def test_an_impossible_shaft_stub_is_refused_by_name(client, params, field):
    """A negative stub is not a shorter one, and there is no third shaft end."""
    from motor_ai_sim.routes.thermal import _validate_field_params

    with pytest.raises(Exception) as exc:
        _validate_field_params(cooling_mode="air", ambient_temp=30.0,
                               h_conv=0.0, air_speed_mps=5.0, fluid="water",
                               fluid_temp_in_c=25.0, flow_lpm=0.0, **params)
    detail = exc.value.detail
    assert [p["field"] for p in detail["invalid_parameters"]] == [field]


def test_the_gap_is_derived_and_states_what_it_was_derived_from(client,
                                                                air_first):
    """``gap_k`` stopped being an input; the payload has to say what replaced it."""
    g = air_first["cooling"]["gap"]
    assert g["regime"] in ("conduction", "transitional", "turbulent")
    assert g["k_eff"] >= g["k_air"] > 0
    assert g["delta_mm"] == pytest.approx(0.2, abs=0.01)   # the fixture's gap
    assert g["T_gap_c"] > 0                                 # STATED, not implied
    assert air_first["gap_k"] == pytest.approx(g["k_eff"])  # legacy key agrees


# ---------------------------------------------------------------------------
# (c3) the retaining sleeve — on GEO_SLEEVED, the module's second EM run
# ---------------------------------------------------------------------------

def test_the_sleeve_is_its_own_anisotropic_domain(client, em_runs):
    """A carbon retaining ring is a thermal blanket, and a very directional one.

    User 2026-09-07: "у него теплопроводность очень плохая в радиальном
    направлении".  Until then the thermal solve handed the ring's elements the
    DEFAULT element conductivity — the air-gap value — because nothing assigned
    it one: a sleeve modelled as air is a sleeve that is not there.  It now
    carries a tensor, radial ≪ hoop, from the assigned material's card.
    """
    import json

    r = client.get("/api/thermal/field",
                   params={**FAST, "geo": json.dumps(GEO_SLEEVED), **AIR})
    assert r.status_code == 200, r.text[:800]
    f = r.json()
    s = f["cooling"]["sleeve"]
    assert s is not None and s["present"] is True
    assert s["thickness_mm"] == pytest.approx(0.4)
    assert s["source"] in ("library", "default")
    assert 0.0 < s["k_radial"] < s["k_fibre"]
    assert "radial" in s["note"]
    # PREFERRED path: the ring keeps its own elements and gets the tensor.  The
    # lumped fallback exists for a mesh that never built the annulus, and if the
    # meshed path ever stops firing this assertion is how we find out rather
    # than silently losing a dimension.
    assert s["n_elements"] > 0, s
    assert s["model"].startswith("meshed domain"), s
    from motor_ai_sim.routes.thermal import PART_NAMES
    assert PART_NAMES[11] == "sleeve"
    assert 11 in set(f["domain_per_tri"]), "the sleeve was dropped from the mesh"
    # the gap it leaves is the MECHANICAL clearance, not the iron-to-bore
    # distance: air_gap 1.0 − sleeve 0.4 = 0.6 mm
    assert f["cooling"]["gap"]["delta_mm"] == pytest.approx(0.6, abs=0.02)


def test_a_machine_without_a_sleeve_reports_none(air_first):
    assert air_first["cooling"]["sleeve"] is None


def test_the_field_is_remembered_as_the_tabs_last_result(client, air_field):
    """Re-entering the tab restores the picture AND the inputs.

    A temperature map costs a full EM transient; re-solving it on every tab
    switch is the most expensive possible way to redraw something the process
    already has.
    """
    out = client.get("/api/thermal/last").json()
    assert out["has_result"] is True
    e = out["field"]
    assert e is not None
    assert e["result"]["T_max"] == pytest.approx(air_field["T_max"])
    # the request that produced it, so the panel can restore its input fields
    assert e["params"]["cooling_mode"] == "air"
    assert e["params"]["ambient_temp"] == pytest.approx(30.0)
    assert e["computed_at"]
    assert e["geometry_fingerprint"] == air_field["geometry_fingerprint"]


def test_last_without_the_field_drops_the_heavy_arrays(client, air_field):
    """``?field=false`` is how the panel asks "is there anything to come back to?"

    On a real machine the per-node / per-triangle arrays are tens of MB of JSON,
    and shipping them to answer a yes/no question is the reason that question
    used to be worth avoiding.
    """
    out = client.get("/api/thermal/last", params={"field": False}).json()
    res = out["field"]["result"]
    for k in ("temperature_per_node", "heat_flux_per_tri", "flux_mag_per_tri"):
        assert k not in res, k
    # the headline numbers survive — that is what "is there anything" means
    assert res["T_max"] == pytest.approx(air_field["T_max"])
    assert res["components"]["winding"] is not None


def test_a_result_solved_for_another_machine_comes_back_flagged(client,
                                                                air_field):
    """THE staleness badge.

    Everything above was solved through a ``?geo=`` override, i.e. for a 30 mm
    machine that is NOT the one loaded in the sandbox config.  Asking /last
    without that override must therefore say so — the frontend cannot work this
    out, it does not have the backend's fingerprint of the live machine.
    """
    out = client.get("/api/thermal/last", params={"field": False}).json()
    e = out["field"]
    assert e["stale_geometry"] is True
    assert e["geometry_fingerprint"] != out["live_geometry_fingerprint"]
    # …and asking WITH the override says it is current, same store, same entry.
    same = client.get("/api/thermal/last",
                      params={"field": False, "geo": _geo()}).json()
    assert same["field"]["stale_geometry"] is False


def test_stale_geometry_is_unknown_not_fine_when_a_fingerprint_is_missing(
        client, air_field):
    """No fingerprint on one side = UNKNOWN, reported as null.

    A staleness check that cannot prove a mismatch must never claim one — and
    must never claim the opposite either, which is why this is None, not False.
    """
    from motor_ai_sim.routes import thermal as th

    th._LAST["field"]["geometry_fingerprint"] = None
    out = client.get("/api/thermal/last", params={"field": False}).json()
    assert out["field"]["stale_geometry"] is None


# ---------------------------------------------------------------------------
# (d) the self-consistent operating point
# ---------------------------------------------------------------------------

def test_coupled_iterates_the_winding_temperature_and_stops_where_told(client, em_runs):
    """/coupled solves FOR the copper temperature instead of assuming it.

    ``max_iter=2`` is the cheapest run that is still a loop, and the assertion
    that matters is that it is bounded: an unbounded fixed point on a machine
    with no equilibrium would run until the request timed out.
    """
    r = client.get("/api/thermal/coupled",
                   params=_field_params(cooling_mode="air", ambient_temp=30.0,
                                        air_speed_mps=5.0, max_iter=2))
    assert r.status_code == 200, r.text[:800]
    out = r.json()
    assert out["max_iter"] == 2
    assert 0 < len(out["coil_temp_history_C"]) <= 2
    assert out["iterations"] == len(out["coil_temp_history_C"])
    assert isinstance(out["converged"], bool)
    assert isinstance(out["runaway"], bool)
    assert out["elapsed_s"] > 0 and out["cached"] is False
    # the final map rides along — the panel draws it, it does not re-solve
    f = out["field"]
    assert f and f["ok"] is True and f["T_max"] >= f["ambient_temp"]
    assert out["P_cu_W"] == pytest.approx(f["P_cu_W"])
    # …and it is remembered under its own kind, beside the plain field result
    e = client.get("/api/thermal/last", params={"field": False}).json()["coupled"]
    assert e is not None and e["params"]["max_iter"] == 2


def test_the_module_capability_runs_the_same_loop(client, em_runs):
    """``solver.em_thermal`` must not own a second copy of the fixed point.

    Two copies drift — one gains a runaway guard, the other does not — and then
    a module study and the Thermal tab report two different equilibrium
    temperatures for one motor.  The module is now an adapter over
    ``routes.thermal.solve_coupled``; this is that wiring, not the physics.
    """
    from motor_ai_sim.modules.solvers import EmThermalCoupled

    res = EmThermalCoupled().run({**FAST, "geo": _geo(), "cooling_mode": "air",
                                  "ambient_temp": 30.0, "air_speed_mps": 5.0,
                                  "max_iter": 1})
    assert res.raw is not None, res
    assert res.raw["iterations"] == 1
    assert res.scalars.t_max_C is not None


# ---------------------------------------------------------------------------
# (e) what the router refuses
# ---------------------------------------------------------------------------

def _detail(r):
    assert r.status_code == 422, f"{r.status_code}: {r.text[:400]}"
    d = r.json()["detail"]
    return d, [p["field"] for p in d.get("invalid_parameters", [])]


@pytest.mark.parametrize("route", ["/api/thermal/field", "/api/thermal/coupled",
                                   "/api/thermal/mesh", "/api/thermal/last"])
def test_a_malformed_geo_override_is_422_not_the_shared_machine(client, route):
    """A truncated override must never fall back to the config.

    That fallback is the multi-user bug this project fixed everywhere else: a
    client whose payload was truncated got somebody ELSE's design solved,
    labelled with its own name, with a 200 and no way to notice.
    """
    r = client.get(route, params={"geo": "{broken"})
    _d, fields = _detail(r)
    assert "geo" in fields


def test_an_unknown_cooling_mode_is_named(client):
    r = client.get("/api/thermal/field",
                   params=_field_params(cooling_mode="watercooled"))
    d, fields = _detail(r)
    assert fields == ["cooling_mode"]
    assert "watercooled" in d["error"]


def test_an_unknown_coolant_is_refused_rather_than_solved_as_water(client):
    """``_coolant_props`` falls back to WATER for anything it cannot resolve.

    That is right for a solve already running and wrong as an answer: a typo'd
    fluid would come back as a perfectly plausible water-cooled map.
    """
    r = client.get("/api/thermal/field",
                   params=_field_params(cooling_mode="liquid", fluid="unobtanium",
                                        fluid_temp_in_c=40.0, flow_lpm=4.0))
    d, fields = _detail(r)
    assert fields == ["fluid"]
    assert "unobtanium" in d["error"]


@pytest.mark.parametrize("params,field", [
    ({"cooling_mode": "liquid", "fluid": "water", "fluid_temp_in_c": 40.0},
     "flow_lpm"),
    ({"bore_mode": "liquid", "bore_fluid": "water",
      "bore_fluid_temp_in_c": 30.0}, "bore_flow_lpm"),
])
def test_liquid_cooling_without_a_flow_rate_is_refused(client, params, field):
    """ṁ = ρ·Q, so Q = 0 is a coolant that never leaves the machine.

    It is the single easiest way to get a plausible-looking wrong answer out of
    the new model — the outlet temperature would be infinite and the sink with
    it — so it is refused by name on BOTH surfaces rather than clamped.
    """
    r = client.get("/api/thermal/field", params=_field_params(**params))
    _d, fields = _detail(r)
    assert fields == [field]


def test_a_machine_with_nothing_cooled_is_refused(client):
    """Every boundary adiabatic = no steady temperature to solve for.

    The machine heats up without limit, so answering with a number would be
    answering a different question.
    """
    r = client.get("/api/thermal/field",
                   params=_field_params(cooling_mode="none", bore_mode="none"))
    d, fields = _detail(r)
    assert fields == ["cooling_mode"]
    assert "no cooled surface" in d["error"]


def test_an_unknown_bore_mode_is_named(client):
    r = client.get("/api/thermal/field",
                   params=_field_params(bore_mode="waterfall"))
    d, fields = _detail(r)
    assert fields == ["bore_mode"]
    assert "waterfall" in d["error"]


def test_a_mesh_floor_coarser_than_the_target_is_refused(client):
    for route in ("/api/thermal/field", "/api/thermal/mesh"):
        r = client.get(route, params={"geo": _geo(), "mesh_size_mm": 1.0,
                                      "min_size_mm": 4.0})
        _d, fields = _detail(r)
        assert fields == ["min_size_mm"], route


def test_manual_cooling_without_an_h_is_refused(client):
    """cooling_mode=manual IS the h — zero of it is not "no cooling", it is a
    boundary condition that does not exist."""
    r = client.get("/api/thermal/field",
                   params=_field_params(cooling_mode="manual", h_conv=0.0))
    _d, fields = _detail(r)
    assert fields == ["h_conv"]


# ---------------------------------------------------------------------------
# (f) the air: kept, named, and in the tree
# ---------------------------------------------------------------------------
# User 2026-09-07, looking at a thermal map whose slot was white around the wire
# bars and whose gap was white around the rotor: *"надо рисовать изоляцию и
# покрытие провода, а то пустое место, и воздух тоже показывать — он же входит в
# расчёт, и в дереве отображать их тоже нужно"*.
#
# The EM mesh calls five different materials "air" — the insulation, the wire
# enamel, the wire coating, the gap and the rotor's pocket air — and the thermal
# solve used to drop all five.  These tests pin the contract that replaced that:
# every one of them is a SOLVED domain with a tag, a name, a conductivity whose
# source is stated, and a temperature of its own; only the far field, the slip
# band and the bore air (the coolant side of the bore boundary condition) are
# still dropped.

AIR_DOMAIN_NAMES = ("insulation", "wire enamel", "wire coating", "air gap",
                    "pocket air")


def test_the_insulation_and_the_air_are_solved_domains(air_first):
    """Every one of the five is present, named, and carries a temperature."""
    from motor_ai_sim.routes.thermal import (DOM_GAP_AIR, DOM_POCKET_AIR,
                                             DOM_SLOT_FILL, DOM_SLOT_LINER,
                                             DOM_WIRE_ENAMEL,
                                             THERMAL_EXTRA_DOMAINS)

    f = air_first
    names = f["part_names"]
    for tag in (DOM_SLOT_LINER, DOM_WIRE_ENAMEL, DOM_SLOT_FILL, DOM_GAP_AIR,
                DOM_POCKET_AIR):
        assert names[str(tag)] == THERMAL_EXTRA_DOMAINS[tag]
    tags = set(f["domain_per_tri"])
    # The 30 mm fixture has a liner, an enamel film, slot air, a gap and rotor
    # pockets — all five must survive into the solved sub-mesh.
    for tag in (DOM_SLOT_LINER, DOM_WIRE_ENAMEL, DOM_SLOT_FILL, DOM_GAP_AIR,
                DOM_POCKET_AIR):
        assert tag in tags, THERMAL_EXTRA_DOMAINS[tag]
    for key in ("liner", "enamel", "slot_fill", "gap_air", "pocket_air"):
        c = f["components"][key]
        assert c is not None, key
        assert math.isfinite(c["max"]) and c["max"] >= c["avg"], key


def test_every_air_domain_says_which_k_it_was_solved_with(air_first):
    """A liner temperature nobody can trace to a k is not an engineering answer.

    The liner and the enamel come from the assignment (Nomex / polyimide on the
    fixture); the wire coating has no card in the materials library at all and must
    therefore say ``default``; the gap is the Taylor–Couette k_eff and the
    pocket is plain air at the same stated temperature — never the enhanced
    value, which is a rotating-clearance effect and not an enclosed-pocket one.
    """
    mu = air_first["materials_used"]
    assert set(mu) >= {"liner", "enamel", "slot_fill", "gap_air", "pocket_air",
                       "winding"}
    assert mu["liner"]["source"] == "library" and mu["liner"]["k"] > 0
    assert mu["enamel"]["source"] == "library" and mu["enamel"]["k"] > 0
    assert mu["slot_fill"]["source"] == "default"
    assert mu["slot_fill"]["k"] == pytest.approx(0.25)
    assert mu["slot_fill"]["note"]
    assert mu["gap_air"]["k_eff"] >= mu["gap_air"]["k_air"] > 0
    assert mu["pocket_air"]["k"] == pytest.approx(mu["gap_air"]["k_air"])
    for key in ("liner", "enamel", "slot_fill", "gap_air", "pocket_air"):
        assert mu[key]["n_elements"] > 0, key
    # The liner is a meshed domain here, so it must NOT also be lumped into the
    # winding's own k — that double count is the thing the split exists to avoid.
    # …and a wire-resolved mesh (enamel / fill meshed around each conductor)
    # gives the copper strips the COPPER k — a bulk winding k on them stacks
    # the insulation twice (860 °C copper under a 64 °C jacket, 2026-09-07).
    model = mu["winding"]["model"]
    if "individual conductors" in model:
        assert mu["winding"]["k"] > 100.0, mu["winding"]
        assert mu["winding"]["material"] == "copper"
    else:
        assert "NOT lumped in again" in model


def test_the_air_classifier_places_everything_it_finds(air_first):
    """``unclassified air`` is the classifier's own error bar.

    Every air element is either inside an insulation ring or inside one of four
    concentric annuli, so a machine with a meaningful count here is one whose
    geometry the classifier does not understand — and it is dropped, not
    guessed at, so the count is the only way to see it.
    """
    rep = air_first["air_domains"]
    assert rep["polygons"] is True, rep["note"]
    assert rep["n_air"] > 0
    assert rep["counts"]["insulation"] > 0 and rep["counts"]["wire enamel"] > 0
    # ~0, not 0: a handful of elements straddling the bore/pocket line on a
    # coarse mesh is a rounding of the geometry, not a misunderstanding of it.
    assert rep["counts"]["unclassified air"] <= 0.005 * rep["n_air"], rep["counts"]


def test_the_gap_carries_the_rotor_heat_when_the_bore_is_uncooled(air_first):
    """The slip tie, measured rather than asserted.

    With the bore adiabatic the rotor has exactly one way out — across the gap —
    so in steady state ``gap_W`` must equal every watt made inside the slip
    radius.  That is what makes the tie a measurement surface and not a fudge
    factor: it is checkable against a number computed from the loss map alone.
    """
    b = air_first["cooling"]["heat_budget"]
    assert b["n_slip_ties"] > 0
    assert b["bore_W"] == 0.0                      # AIR fixture: bore_mode=none
    assert b["rotor_W"] > 0
    assert b["gap_W"] == pytest.approx(b["rotor_W"], rel=0.05, abs=0.02)


def test_the_mesh_preview_tags_the_air_exactly_as_the_solve_does(
        mesh_payload, air_first):
    """/mesh is what the tab draws before Solve, so it must be the SAME machine.

    A preview that dropped the liner and then grew one on Solve would change the
    geometry under the user rather than filling it with temperatures.
    """
    assert set(air_first["domain_per_tri"]) == set(mesh_payload["domain_per_tri"])
    assert mesh_payload["part_names"] == air_first["part_names"]
    # The two meshes are built by the same builder with the same parameters but
    # not by the same CALL (the field route goes through the transient's own
    # sliding-band assembly), so the element COUNTS differ by a few per cent.
    # What must agree is the classification: the same domains present, and the
    # same ones empty.
    def _present(p):
        return {k for k, v in p["air_domains"]["counts"].items() if v}
    assert _present(mesh_payload) == _present(air_first)


def test_a_sleeved_rotor_keeps_both_its_sleeve_and_its_gap_air(client, em_runs):
    """The two things that share the clearance, and neither may eat the other.

    Before 2026-09-07 the whole gap was dropped and the sleeve was silently
    given the gap's conductivity.  Now both are domains: the sleeve carries its
    anisotropic tensor, the air on either side of it carries k_eff, and the
    rotor is still tied to the stator across the slip line.

    On GEO_SLEEVED, not a third geometry of its own: a temperature map is now
    only as available as the Electromagnetic run behind it, and a machine nobody
    ran is a 422 — so the sleeved claims are made on the one sleeved run this
    module pays for.
    """
    from motor_ai_sim.routes.thermal import DOM_GAP_AIR

    r = client.get("/api/thermal/field",
                   params={**FAST, "geo": json.dumps(GEO_SLEEVED), **AIR})
    assert r.status_code == 200, r.text[:800]
    f = r.json()
    sl = f["cooling"]["sleeve"]
    assert sl["present"] is True and sl["n_elements"] > 0
    assert sl["k_radial"] < sl["k_fibre"], "an isotropic sleeve is the old bug"
    assert "meshed domain" in sl["model"]
    assert f["components"]["sleeve"] is not None
    assert DOM_GAP_AIR in set(f["domain_per_tri"])
    b = f["cooling"]["heat_budget"]
    assert b["n_islands_rotor"] == 0 and b["n_slip_ties"] > 0
    assert b["residual_pct"] < 2.0, b


# ---------------------------------------------------------------------------
# (g) the two solvers are separate
# ---------------------------------------------------------------------------
# User, 2026-09-07: *"нужно как-то разделить тепловые расчёты и
# электромагнитные; если вдруг тепловому расчёту нужно электромагнитное
# моделирование, пусть оно делается во вкладке Simulation"* (renamed
# Electromagnetic).  Until that day a /field request whose operating point no
# run matched quietly started a 36-frame eddy transient of its own: minutes of
# FEM behind a temperature request, on a solver the user had just asked to keep
# apart from this one — and, once, a full-ring eddy field published into the
# warm-seed cache that killed 10 of 10 points of the next sweep.
#
# These four tests are the whole of the new contract: where the map comes from,
# what it says, what happens when there is none, and that a map once handed over
# outlives the run that produced it.


def test_the_temperature_map_names_the_electromagnetic_run_it_came_from(air_first):
    """``loss_source`` is the provenance the panel prints — and the proof.

    ``simulation_run`` is the ONLY kind a fresh answer can now carry besides
    ``thermal_store``: no electromagnetic solve ran for this temperature map, and
    the payload says which run's cycle-averaged loss density it is.
    """
    src = air_first["loss_source"]
    assert src["kind"] == "simulation_run", src
    assert src["run_id"] == EM_RUN_ID
    assert "no electromagnetic solve" in src["note"]


def test_a_point_no_electromagnetic_run_covers_is_refused_by_name(client, em_runs):
    """The refusal that replaced the hidden solve.

    422, a machine-readable ``error_code``, an EMPTY ``invalid_parameters`` (the
    user typed nothing wrong — the run simply does not exist yet), and a sentence
    that names the operating point to go and run.  A current 1 A away from the
    stored run is the honest case: ρJ² is quadratic in it, so the neighbouring
    map is not "close enough" for a number with no label on it.
    """
    r = client.get("/api/thermal/field",
                   params=_field_params(**AIR, I_phase_rms=FAST["I_phase_rms"] + 1.0))
    assert r.status_code == 422, r.text[:600]
    d = r.json()["detail"]
    assert d["error_code"] == "no_electromagnetic_run", d
    assert d["invalid_parameters"] == []
    msg = d["error"]
    assert "no Electromagnetic run of this machine at" in msg, msg
    assert "I = 21 A" in msg and "γ = 0°" in msg, msg
    assert " rpm" in msg and "coil 120 °C" in msg, msg
    assert "4 steps/period" in msg, msg
    assert "run it on the Electromagnetic tab first" in msg, msg
    assert "never computes electromagnetic losses itself" in msg, msg
    # ...and it names the speed the LOSSES are keyed on (the config's), not the
    # air-gap Taylor speed this request happened to send.
    from motor_ai_sim.routes.simulation import _effective_rpm
    assert format(int(round(_effective_rpm(None))), ",d").replace(",", " ") in msg


def test_a_single_frame_request_is_refused_because_it_is_not_a_cycle(client, em_runs):
    """One frame has no cycle to average, and a run's map always is one.

    So there is nothing that could ever match: refused by name rather than
    handed a period average labelled as an instant.  (This is also why the whole
    module now runs at four steps per period — the old single-frame FAST was
    exactly this request.)
    """
    r = client.get("/api/thermal/field",
                   params=_field_params(**AIR, n_steps_per_period=1))
    assert r.status_code == 422, r.text[:600]
    d = r.json()["detail"]
    assert d["error_code"] == "no_electromagnetic_run", d
    assert d["invalid_parameters"] == []
    assert "single frame" in d["error"] and "no cycle to average" in d["error"]
    assert "at least 2 steps per period" in d["error"]


def test_a_map_once_handed_over_outlives_the_run_that_produced_it(
        client, em_runs, monkeypatch):
    """The Electromagnetic tab moves on; the Thermal tab keeps its answer.

    The run store holds the last run per key, and the user's next Run at the next
    current replaces it.  A Thermal tab that only knew how to read that store
    would start refusing an operating point it answered five minutes ago — so the
    map is remembered here too, under its PHYSICS identity, and served with
    ``kind: thermal_store`` when the run is gone.  Still not a solve: it is the
    same array that run produced.
    """
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.routes import thermal as th

    first = client.get("/api/thermal/field",
                       params=_field_params(**{**AIR, "ambient_temp": 31.0}))
    assert first.status_code == 200, first.text[:600]
    assert first.json()["loss_source"]["kind"] == "simulation_run"
    assert th._LOSS_MAPS, "the map was not remembered"

    # The user runs the Electromagnetic tab at another point: this key is gone.
    # (`_load_last_transient_field_snapshot` is stubbed as well — an empty store
    # sends the lookup to the pickle, which is how a benign cache flush is meant
    # to recover, and here it would quietly undo the very thing being tested.)
    monkeypatch.setattr(sim, "_transient_field_snap", {})
    monkeypatch.setattr(sim, "_load_last_transient_field_snapshot", lambda *a, **k: None)

    again = client.get("/api/thermal/field",
                       params=_field_params(**{**AIR, "ambient_temp": 32.0}))
    assert again.status_code == 200, again.text[:600]
    src = again.json()["loss_source"]
    assert src["kind"] == "thermal_store", src
    assert "no electromagnetic solve ran" in src["note"]
    assert EM_RUN_ID in str(src["run_id"]), src

    # ...and with the memory cleared too there is nothing left to serve.
    th._LOSS_MAPS.clear()
    none_left = client.get("/api/thermal/field",
                           params=_field_params(**{**AIR, "ambient_temp": 33.0}))
    assert none_left.status_code == 422, none_left.text[:400]
    assert none_left.json()["detail"]["error_code"] == "no_electromagnetic_run"


# ---------------------------------------------------------------------------
# The MECHANICAL heat — bearing friction and windage as sources (2026-09-08)
# ---------------------------------------------------------------------------
# User: "when the coupled run runs, the WHOLE model must be solved, and ALL the
# losses must be carried".  The bearings and the rotor windage are ANALYTIC
# (motor_ai_sim.bearings), and they are heat like any other — so they enter this
# solve where they are MADE:
#
#   * bearing friction at the SHAFT, and only when the shaft-ends path is open:
#     the bearings sit on the stubs OUTSIDE this cross-section, so that
#     conductance is the only door the 2-D model has.  With the path closed the
#     watts are REPORTED as not modelled, never silently dropped — that silent
#     drop is the whole complaint this work started from;
#   * windage's GAP-SHEAR half as a volumetric source in the air-gap air, which
#     is literally where the shearing happens.  The END-FACE half is out along
#     the axis and is reported as not modelled.

#: A chosen mechanical split, injected at the seam.  The RESOLUTION (which
#: machine, which cards, which temperature) is pinned in tests/test_bearings.py;
#: what this module owns is what the conduction solve DOES with the watts, and a
#: fixture that has to run the 30 mm at a speed where its own windage is
#: measurable would test the correlation instead of the injection.
MECH = {"has_any": True, "has_bearings": True,
        "P_bearings_W": 40.0, "P_windage_gap_W": 6.0, "P_windage_faces_W": 3.0,
        "per_end_W": {"A": 20.0, "B": 20.0},
        "bearing_temp_c": 88.0, "bearing_temp_source": "assigned",
        "mech": {"model": "SKF frictional-moment model + analytic windage — "
                          "ANALYTIC, not FEM"},
        "windage": {"P_gap_W": 6.0, "P_faces_W": 3.0}, "note": ""}


@pytest.fixture
def mech_injected(monkeypatch):
    """``_resolve_mech_losses`` answering ``MECH`` for every request."""
    from motor_ai_sim.routes import thermal as th

    monkeypatch.setattr(th, "_resolve_mech_losses",
                        lambda geo_ov, *, rpm, bearing_temp_c=None: dict(MECH),
                        raising=True)
    return MECH


def test_bearing_friction_enters_at_the_shaft_ends(client, em_runs,
                                                   mech_injected):
    """With the shaft-ends path OPEN the friction is a source on the shaft.

    Three things have to be true at once, and only all three together mean the
    watts really went in: the budget NAMES them, the map's total generation grew
    by exactly that much over the electromagnetic loss, and the budget still
    closes with them counted as an outflow through the same surfaces as
    everything else.
    """
    r = client.get("/api/thermal/field", params=_field_params(**AIR, **SHAFT))
    assert r.status_code == 200, r.text[:800]
    f = r.json()
    b = f["cooling"]["heat_budget"]
    m = f["cooling"]["mech_losses"]

    assert b["bearing_friction_W"] == pytest.approx(40.0)
    assert b["bearing_friction_not_modelled_W"] == 0.0
    assert b["windage_W"] == pytest.approx(6.0)
    # the END FACES are out along the axis — named, with their watts on them
    assert b["windage_not_modelled_W"] == pytest.approx(3.0)
    assert b["mech_loss_in_map_W"] == pytest.approx(46.0)
    assert b["mech_loss_total_W"] == pytest.approx(49.0)

    # ∫q dV over the solved mesh = the EM map + exactly the injected watts.
    assert b["losses_W"] == pytest.approx(b["em_loss_total_W"] + 46.0,
                                          rel=0.02, abs=0.5)
    # …and it still leaves: the budget closes with the new sources in it.
    assert b["residual_pct"] < 2.0, b
    assert (b["housing_W"] + b["bore_W"] + b["shaft_ends_W"]
            == pytest.approx(b["losses_W"], rel=0.02, abs=0.05))

    assert m["bearings_modelled"] is True and m["windage_gap_modelled"] is True
    assert m["bearing_temp_c"] == pytest.approx(88.0)
    assert m["bearing_temp_source"] == "assigned"
    assert m["per_end_W"] == {"A": 20.0, "B": 20.0}
    assert "SKF" in m["model"]
    assert any("UPPER BOUND" in n for n in m["notes"]), m["notes"]


def test_the_friction_makes_the_shaft_hotter_and_leaves_down_its_own_ends(
        client, em_runs, mech_injected, monkeypatch):
    """A source is only a source if the temperature moved.

    40 W into a 30 mm machine's shaft is several times its own loss, so the
    shaft has to come back hotter AND the shaft-ends outflow has to grow —
    otherwise the watts were added to a budget line and to nothing else.
    """
    from motor_ai_sim.routes import thermal as th

    with_mech = client.get("/api/thermal/field",
                           params=_field_params(**AIR, **SHAFT)).json()
    # the same machine with no bearings at all
    monkeypatch.setattr(
        th, "_resolve_mech_losses",
        lambda geo_ov, *, rpm, bearing_temp_c=None: {
            "has_any": False, "has_bearings": False, "P_bearings_W": 0.0,
            "P_windage_gap_W": 0.0, "P_windage_faces_W": 0.0, "per_end_W": {},
            "mech": None, "windage": None, "bearing_temp_c": None,
            "bearing_temp_source": None, "note": "no bearings"},
        raising=True)
    without = client.get("/api/thermal/field",
                         params=_field_params(**AIR, **SHAFT)).json()

    assert (with_mech["components"]["shaft"]["max"]
            > without["components"]["shaft"]["max"] + 1.0)
    assert (with_mech["cooling"]["heat_budget"]["shaft_ends_W"]
            > without["cooling"]["heat_budget"]["shaft_ends_W"])
    # …and the machine with no bearings carries no mechanical watts, not zero
    # watts it invented: every line is 0 and the block says has_bearings false.
    b0 = without["cooling"]["heat_budget"]
    assert b0["mech_loss_total_W"] == 0.0
    assert without["cooling"]["mech_losses"]["has_bearings"] is False


def test_with_the_shaft_ends_closed_the_friction_is_reported_not_dropped(
        client, em_runs, mech_injected):
    """THE honesty case.

    A closed cross-section has nowhere to put bearing heat — the bearings are
    outside it.  What it must NOT do is quietly forget the watts: they are named
    in the budget with their number, and the note says which switch would let
    them in.  The gap windage still goes in, because the gap air IS in this
    cross-section.
    """
    r = client.get("/api/thermal/field",
                   params=_field_params(**AIR, shaft_ext_length_mm=0.0))
    assert r.status_code == 200, r.text[:800]
    f = r.json()
    b = f["cooling"]["heat_budget"]
    m = f["cooling"]["mech_losses"]

    assert b["bearing_friction_W"] == 0.0
    assert b["bearing_friction_not_modelled_W"] == pytest.approx(40.0)
    assert b["windage_W"] == pytest.approx(6.0)          # the gap is still here
    assert b["mech_loss_in_map_W"] == pytest.approx(6.0)
    assert b["mech_loss_total_W"] == pytest.approx(49.0)
    assert b["losses_W"] == pytest.approx(b["em_loss_total_W"] + 6.0,
                                          rel=0.02, abs=0.5)
    assert b["residual_pct"] < 2.0, b

    assert m["bearings_modelled"] is False
    assert m["shaft_ends_open"] is False
    assert m["P_bearings_not_modelled_W"] == pytest.approx(40.0)
    note = " ".join(m["notes"])
    assert "NOT modelled" in note and "shaft-ends heat path is closed" in note
    assert "40.0 W" in note


def test_windage_heats_the_gap_air_it_is_sheared_in(client, em_runs,
                                                    monkeypatch):
    """The gap-shear half is deposited where the shearing happens.

    The gap air has been a meshed, solved domain since 2026-09-07, so this needs
    no lumping — and with the source ten times larger the gap air has to come
    back hotter than the same machine without it.  (Ten times: at 3 000 rpm the
    30 mm fixture's own windage is microwatts, and a test that could not see its
    own effect would pass with the source wired to nothing.)
    """
    from motor_ai_sim.routes import thermal as th

    def _mk(gap_w):
        return lambda geo_ov, *, rpm, bearing_temp_c=None: {
            **MECH, "P_bearings_W": 0.0, "P_windage_gap_W": gap_w,
            "P_windage_faces_W": 0.0, "per_end_W": {},
            "has_bearings": False, "has_any": gap_w > 0.0,
            "windage": {"P_gap_W": gap_w, "P_faces_W": 0.0}}

    monkeypatch.setattr(th, "_resolve_mech_losses", _mk(60.0), raising=True)
    hot = client.get("/api/thermal/field", params=_field_params(**AIR)).json()
    monkeypatch.setattr(th, "_resolve_mech_losses", _mk(0.0), raising=True)
    cold = client.get("/api/thermal/field", params=_field_params(**AIR)).json()

    assert hot["cooling"]["heat_budget"]["windage_W"] == pytest.approx(60.0)
    assert cold["cooling"]["heat_budget"]["windage_W"] == 0.0
    assert (hot["components"]["gap_air"]["max"]
            > cold["components"]["gap_air"]["max"] + 1.0)
    assert (hot["cooling"]["heat_budget"]["losses_W"]
            > cold["cooling"]["heat_budget"]["losses_W"] + 50.0)
    assert hot["cooling"]["mech_losses"]["windage_gap_modelled"] is True


def test_the_real_machines_bearings_reach_this_solve(client, em_runs,
                                                     monkeypatch):
    """End to end, through the SHARED implementation and not through the seam.

    ``_resolve_mech_losses`` reads the same ``mech_losses.machine_mech_losses``
    the /losses route and the stored run's summary read, so a pair changed in
    the Mechanical tab moves this map too.  Only the die-file read and the
    fingerprint are faked here: this suite may not write ``config/dies``, and
    the fixture geometry is a per-request override that would otherwise be
    refused as a CANDIDATE (which is its own test, below).
    """
    from motor_ai_sim import mech_losses as ml
    from motor_ai_sim.routes import thermal as th

    assign = {"A": {"card": "61811-2RS1"}, "B": {"card": "61811-2RS1"},
              "lubrication": "grease", "preload_n": 0,
              "temp_source": "manual", "temp_c": 59}
    monkeypatch.setattr(ml, "machine_bearings",
                        lambda die=None, cfg=None: (dict(assign), "D", "C"),
                        raising=True)
    monkeypatch.setattr(th, "_live_fingerprint", lambda ov=None: "SAME-MACHINE",
                        raising=True)

    f = client.get("/api/thermal/field",
                   params=_field_params(**AIR, **SHAFT)).json()
    m = f["cooling"]["mech_losses"]
    b = f["cooling"]["heat_budget"]
    assert m["has_bearings"] is True
    # 2 x 61811-2RS1 at 3 000 rpm, 59 degC: the seal term dominates and the pair
    # is ~1.5x its 2 000 rpm figure of 84 W.
    assert 100.0 < m["P_bearings_W"] < 180.0, m["P_bearings_W"]
    assert m["bearing_temp_c"] == pytest.approx(59.0)
    assert m["bearing_temp_source"] == "assigned"
    assert b["bearing_friction_W"] == pytest.approx(m["P_bearings_W"])
    assert b["residual_pct"] < 2.0, b


def test_a_candidate_geometry_is_never_billed_for_this_machines_bearings(
        client, em_runs, monkeypatch):
    """A per-request geometry that is not the machine on screen is a CANDIDATE.

    Attributing the active machine's bearing pair to it would heat somebody
    else's rotor with this one's friction — the same resolved-fingerprint test
    ``routes.simulation`` uses for the field snapshot and the persisted last run.
    """
    from motor_ai_sim import mech_losses as ml

    monkeypatch.setattr(
        ml, "machine_bearings",
        lambda die=None, cfg=None: ({"A": {"card": "61811-2RS1"},
                                     "B": {"card": "61811-2RS1"},
                                     "lubrication": "grease", "preload_n": 0,
                                     "temp_source": "manual", "temp_c": 59},
                                    "D", "C"),
        raising=True)
    # no fingerprint patch: the fixture geometry IS a candidate here
    f = client.get("/api/thermal/field",
                   params=_field_params(**AIR, **SHAFT)).json()
    m = f["cooling"]["mech_losses"]
    assert m["has_bearings"] is False
    assert m["P_mech_total_W"] == 0.0
    assert f["cooling"]["heat_budget"]["mech_loss_total_W"] == 0.0
