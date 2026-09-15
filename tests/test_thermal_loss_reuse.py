"""The Thermal tab TAKES the loss map from the Electromagnetic tab — or refuses.

User, 2026-09-07: *"а зачем считается каждый шаг? нам нужны средние потери
мотора за весь цикл"*, and later the same day: *"нужно как-то разделить тепловые
расчёты и электромагнитные; если вдруг тепловому расчёту нужно электромагнитное
моделирование, пусть оно делается во вкладке Simulation"* (the tab being renamed
Electromagnetic).

The thermal solve needs ONE number per element — the cycle-averaged loss density
— and it used to buy that number with its own 36-frame eddy transient every time
the operating point moved, then buy it again on every pass of the coupled
winding-temperature loop.  That solve is now GONE from this router: the map is an
electromagnetic result, it is computed on the Electromagnetic tab, and this
module pins what replaced the hidden solve.

Three promises, and they pull in opposite directions, which is the whole point:

  (1) an Electromagnetic run that matches EXACTLY is served, with no
      electromagnetic solve at all — asserted by making the solver itself explode
      if it is entered;
  (2) a run that differs in ANY of the things the map depends on — the current,
      the angle, the geometry, the material assignment, the frame count — is NOT
      served, and neither is a run that stored only component totals.  A near
      miss is a 422 raised BEFORE any solver is touched; nothing is approximated,
      and nothing is quietly solved instead;
  (3) a map once handed over is REMEMBERED under its physics identity (memory
      and disk), so the answer survives the user's next Electromagnetic run and
      an API restart.

And the loop on top of it:

  (4) ``solve_coupled`` obtains the map ONCE and moves the copper analytically
      (the ρ_Cu(T) / σ_Cu(T) split), landing on what a run solved AT the
      converged winding temperature reports — the audit ``verify_em`` used to
      run inside the loop, now made the only way the split allows: on the
      Electromagnetic side.

Everything runs on the 30 mm 12s/14p fixture through a per-request ``?geo=``
override, two frames over the machine's natural half-wedge — the cheapest run
that still produces a real cycle-averaged map (a single frame has no B(t)
history and is refused outright).
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import time

import pytest
import numpy as _np
from fastapi import HTTPException

from tests.test_thermal_routes import GEO_30MM, store_em_run

# TWO frames, not one: a single-angle request has no cycle to average, and the
# run store is deliberately not consulted for it (see `_em_loss_map`).  The
# current is high enough that copper is a real share of the loss — at 20 A the
# fixture's winding makes ~1 W against 2 W of iron and the coupled loop has
# nothing to converge.
FAST = {
    "n_steps_per_period": 2, "n_periods": 1.0,
    "mesh_size_mm": 2.5, "min_size_mm": 0.6, "n_sectors": 2,
    "I_phase_rms": 60.0, "rpm": 3000.0, "gamma_deg": 0.0,
    "coil_temp_c": 120.0,
}
#: 15 m/s, not the 5 m/s this module was written with (2026-09-10).  Nothing
#: about the machine changed; the BOUNDARY did.  A symmetry wedge's two radial
#: cut lines are adiabatic — nothing crosses a symmetry plane — and until
#: 2026-09-09 their outer ends carried the housing film, so a sectored solve was
#: cooled by ~10 % more surface than the machine has (the same bug that made the
#: G2's two edge slots run 3 K cool).  Correcting it moved this 60 A fixture,
#: which sat close to runaway on purpose, past RUNAWAY_C: one pass, no loop to
#: measure.  The cure is more air, not less current — the module's own note
#: above says the current has to stay high enough for copper to be a real share
#: of the loss.
AIR = {"cooling_mode": "air", "ambient_temp": 30.0, "air_speed_mps": 45.0}
RUN_ID = "2026-09-07T10:00:00"

#: PINNED materials (2026-09-09) — the sandbox-leak family once more.  The
#: geometry here is pinned to the 30 mm fixture, but the MATERIALS came from the
#: sandbox copy of the user's live config; the morning the live machine was the
#: 40 mm (F52SH_120C magnets, 20SW1200 steels) this module's 60 A point ran away
#: (one pass, no loop to test) and "an override is not reused" found nothing to
#: differ on — the config already said F52SH.  These are the cards the fixture
#: was written on; every request in this file and the stored run itself carry
#: them, so the run key and the requests agree whatever machine is loaded.
MATERIALS_30MM = {"magnet": "F52SH_120C", "stator_core": "B10AHV900M",
                  "rotor_core": "B10AHV900M", "shaft": "Aluminium_6061"}
#: …and the WINDING, for the same reason: neither the stored run nor the thermal
#: requests carry a connection (both read the shared config), and the G2's 4S
#: (all four coils in series, the whole 60 A through every one of them) made
#: four times the copper loss of the 2P this fixture was written under — a
#: one-pass runaway where the loop tests need two passes to say anything.
WINDING_30MM = {"n_coils_per_phase": 4, "connection": "2P",
                "n_parallel": 2, "n_series": 1, "layers": 1}


@pytest.fixture(scope="module")
def pinned_config_materials():
    """``MATERIALS_30MM`` and ``WINDING_30MM`` written into the SANDBOX config
    for the whole module, and the original blocks put back after.

    In the config rather than through the per-request override: the two
    route-driven tests below go through the router's own ``?mat=`` dependency,
    which sets that override per request (to nothing), and a ContextVar pinned
    from outside would not survive it — the stored run and the request would
    then be two different machines.  The sandbox copy is the test suite's own
    file (tests/conftest.py); ``config/`` is never touched.
    """
    import yaml

    from motor_ai_sim.config import DEFAULT_CONFIG_PATH, clear_config_cache

    path = pathlib.Path(DEFAULT_CONFIG_PATH)
    original = path.read_text(encoding="utf-8")
    doc = yaml.safe_load(original) or {}
    mats = dict(doc.get("materials") or {})
    mats.update(MATERIALS_30MM)
    doc["materials"] = mats
    wind = dict(doc.get("winding") or {})
    wind.update(WINDING_30MM)
    doc["winding"] = wind
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    clear_config_cache()
    yield dict(MATERIALS_30MM)
    path.write_text(original, encoding="utf-8")
    clear_config_cache()


def _geo() -> str:
    return json.dumps(GEO_30MM)


def _req(**over) -> dict:
    p = {**FAST, **AIR, "geo": _geo()}
    p.update(over)
    return p


# ---------------------------------------------------------------------------
# A Simulation run, parked where the Simulation tab parks one
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def run_snapshot(pinned_config_materials):
    """Solve ONE transient the way a Run does, and store its field snapshot.

    The solve + store is ``tests.test_thermal_routes.store_em_run`` — one copy of
    the handshake for all three thermal modules, because it IS the product's seam
    between the two tabs and a second spelling of it here would test this
    module's idea of a run rather than the run.

    The snapshot store and its pickle are redirected and restored around the
    module: ``config/.last_transient_field.pkl`` is the user's own last run and
    this suite promises not to touch it.
    """
    from motor_ai_sim.routes import simulation as sim

    mp = pytest.MonkeyPatch()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="thermal_loss_reuse_"))
    mp.setattr(sim, "_transient_field_store_path", lambda: str(tmp / ".snap.pkl"))
    saved = dict(sim._transient_field_snap)
    sim._transient_field_snap.clear()

    info = store_em_run(GEO_30MM, run_id=RUN_ID, phys=FAST)
    time.sleep(0.3)          # the persist is a daemon thread — let it land

    yield {"probe": info["probe"], "tmp": tmp,
           "solver_result": info["solver_result"]}

    sim._transient_field_snap.clear()
    sim._transient_field_snap.update(saved)
    mp.undo()


@pytest.fixture(autouse=True)
def _clear_thermal_caches(tmp_path, monkeypatch):
    """Every test asks the question fresh, and nothing lands in ``config/``.

    Only the THERMAL caches are dropped — the EM field cache holds replays of the
    module's one run, and re-doing those would buy nothing.

    The last-result store is redirected as well (same fixture as
    tests/test_thermal_routes.py): two tests below drive the real routes, which
    remember their answer as "what the Thermal tab was last showing" and persist
    it beside the user's own machine.
    """
    from motor_ai_sim.routes import thermal as th

    monkeypatch.setattr(th, "_LAST", {}, raising=True)
    monkeypatch.setattr(th, "_LAST_LOADED", True, raising=True)   # skip the disk
    monkeypatch.setattr(th, "_last_store_path",
                        lambda: str(tmp_path / ".last_thermal.pkl"))
    th._FIELD_CACHE.clear()
    th._COUPLED_CACHE.clear()
    # the thermal route's OWN map store (2026-09-07): redirected to tmp and
    # emptied, so a map one test solved is not "remembered" into the next
    monkeypatch.setattr(th, "_loss_maps_path", lambda: str(tmp_path / ".loss_maps.pkl"))
    th._LOSS_MAPS.clear()
    monkeypatch.setattr(th, "_LOSS_MAPS_LOADED", True, raising=True)
    yield
    th._FIELD_CACHE.clear()
    th._COUPLED_CACHE.clear()
    th._LOSS_MAPS.clear()


@pytest.fixture
def no_em_solve(monkeypatch):
    """Make the electromagnetic solver explode if anything enters it.

    The claim is an ABSENCE — "no electromagnetic solve ran for this temperature
    map" — and counting calls proves a number where this proves the absence.
    `_fem_field2d_impl` imports the solver from its module at call time, so
    patching the attribute is enough.

    Since 2026-09-07 it is armed on BOTH kinds of path, not just the reuse ones:
    a request nothing matches is now a 422 raised before any solver is reached,
    so "it refused" and "it did not secretly solve" are two separate assertions
    and both are made everywhere (see ``_refused``).  Before that day the second
    one could not even be asked — reaching the solver WAS the refusal.
    """
    from motor_ai_sim.simulation import fem_solver_2d as fs

    calls = []

    def _boom(*a, **kw):
        calls.append(kw)
        raise AssertionError("SENTINEL: the EM solver ran when it should not have")

    monkeypatch.setattr(fs, "fem_transient_sliding_band", _boom)
    return calls


# ---------------------------------------------------------------------------
# (1) the Electromagnetic run is served
# ---------------------------------------------------------------------------

def test_a_matching_simulation_run_is_reused_without_any_em_solve(
        run_snapshot, no_em_solve):
    """THE point of the change: the map the user already paid for.

    No electromagnetic solve runs at all — the solver would raise if it were
    entered — and the answer names the run it came from, because a map replayed
    from a run of ten minutes ago and a map solved just now are the same physics
    but not the same claim.
    """
    from motor_ai_sim.routes import thermal as th

    out = th.solve_thermal_field(**_req())
    assert out["ok"] is True
    src = out["loss_source"]
    assert src["kind"] == "simulation_run", src
    assert src["run_id"] == RUN_ID
    assert src["computed_at"] == RUN_ID
    assert "no electromagnetic solve" in src["note"]
    assert no_em_solve == [], "the EM solver was entered on a reuse"
    # …and it is a real temperature map, not a shell
    assert out["T_max"] >= out["ambient_temp"]
    assert out["components"]["winding"] is not None
    assert out["cooling"]["heat_budget"]["losses_W"] > 0


def test_the_reused_map_is_the_runs_own_per_element_map(run_snapshot,
                                                        no_em_solve):
    """Reuse means the run's ARRAY, not its totals redistributed.

    The heat budget closes on the elements that were solved, so if the map had
    been re-derived from the component watts the residual would not.
    """
    from motor_ai_sim.routes import thermal as th

    out = th.solve_thermal_field(**_req())
    b = out["cooling"]["heat_budget"]
    assert b["residual_pct"] < 2.0, b
    assert out["P_cu_W"] > 0 and out["P_fe_W"] is not None
    assert len(out["temperature_per_node"]) == out["n_vertices"]


def test_a_thermal_cache_hit_says_it_never_looked(run_snapshot, no_em_solve):
    """The thermal cache key does not carry the loss SOURCE — deliberately.

    Two requests that differ only in where their identical loss map came from
    are the same request, so they share a key; the answer then carries the
    provenance of the map it was built from, plus a flag saying this particular
    request did not get as far as looking for one.
    """
    from motor_ai_sim.routes import thermal as th

    first = th.solve_thermal_field(**_req())
    again = th.solve_thermal_field(**_req())
    assert again["cached"] is True
    assert again["T_max"] == pytest.approx(first["T_max"])
    assert again["loss_source"]["kind"] == "simulation_run"
    assert again["loss_source"]["thermal_cache_hit"] is True


# ---------------------------------------------------------------------------
# (2) what is NOT served — and is refused rather than solved
# ---------------------------------------------------------------------------

def _refused(th, no_em_solve, **over):
    """A request no Electromagnetic run covers: 422, and NOTHING was solved.

    Two assertions, and the second one is the 2026-09-07 half.  Before that day
    a near miss fell through to a solve, so "it was refused" could only be tested
    by catching the solver being entered.  Now the refusal comes FIRST — a 422
    naming the run to make — and the booby-trapped solver proves the route did
    not quietly do the work anyway on its way out.

    Returns the 422 detail so a caller can assert on the sentence.
    """
    with pytest.raises(HTTPException) as exc:
        th.solve_thermal_field(**_req(**over))
    assert exc.value.status_code == 422, exc.value.status_code
    d = exc.value.detail
    assert isinstance(d, dict), d
    assert d.get("error_code") == "no_electromagnetic_run", d
    # Nothing the user typed is wrong: the run simply has not been made.
    assert d.get("invalid_parameters") == [], d
    assert "Electromagnetic tab" in d["error"], d["error"]
    assert no_em_solve == [], "the EM solver was entered on a refusal"
    return d


def test_a_different_current_is_not_reused(run_snapshot, no_em_solve):
    """A loss map is an operating point, not a machine.

    ρJ² is quadratic in the current and the iron term is not linear in it
    either, so serving 60 A's map for a 61 A request would be wrong by more than
    any label could carry — and a temperature map, unlike the Loss view, has
    nowhere to print "solved at a different current".
    """
    from motor_ai_sim.routes import thermal as th

    _refused(th, no_em_solve, I_phase_rms=61.0)


def test_a_different_gamma_is_not_reused(run_snapshot, no_em_solve):
    from motor_ai_sim.routes import thermal as th

    _refused(th, no_em_solve, gamma_deg=15.0)


def test_a_different_frame_count_is_not_reused(run_snapshot, no_em_solve):
    """Steps per period are matched EXACTLY, not "close enough".

    The map is a cycle average, and how many samples that average was taken over
    is part of what the number is — the same solve at 2 and at 48 steps reports
    different iron loss.
    """
    from motor_ai_sim.routes import thermal as th

    _refused(th, no_em_solve, n_steps_per_period=3)


def test_a_different_mesh_or_demag_or_gap_layers_is_still_reused(run_snapshot, no_em_solve):
    """The run's mesh, sector count, gap layers and demag switch are ITS
    discretisation, not the physics: the map is replayed on the run's own
    elements.  Keying on them (the first cut did) meant the Thermal tab never
    matched a Simulation run — the user runs with demag and one gap layer,
    this route solves with neither ("опять расчёт на каждого фрейма",
    2026-09-07)."""
    from motor_ai_sim.routes import thermal as th

    out = th.solve_thermal_field(**_req(mesh_size_mm=2.2, min_size_mm=0.4, n_sectors=1))
    assert out["loss_source"]["kind"] == "simulation_run", out["loss_source"]
    assert "demag" in out["loss_source"]["note"]
    # the answer is on the RUN's mesh, not the request's
    n_run = int(_np.asarray(run_snapshot["solver_result"]["field"]["T"]).shape[1])
    # the thermal sub-mesh is the run's mesh minus the far air and the slip
    # band, never the request's (2.2 mm) mesh, which would be ~3x the elements
    n_out = len(out["domain_per_tri"])
    assert 0.8 * n_run <= n_out <= n_run, (n_out, n_run)


def test_the_route_remembers_the_map_it_was_handed(run_snapshot, monkeypatch):
    """A map once handed over outlives the run that produced it.

    The Electromagnetic run store holds the last run per key, and the user moves
    on — the next current, the next angle — so a Thermal tab that could only read
    that store would start refusing an operating point it answered five minutes
    ago.  The map is therefore remembered here too, keyed on the PHYSICS identity
    alone (so another cooling is the same map) and mirrored to disk, which is
    what makes it survive an API restart ("надо просто запоминать карту потерь",
    2026-09-07).

    It is remembered from an Electromagnetic RUN, never from a solve of its own:
    that is the difference between this and the version of this test that stood
    here this morning.
    """
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.routes import thermal as th

    first = th.solve_thermal_field(**_req())
    assert first["loss_source"]["kind"] == "simulation_run", first["loss_source"]
    assert th._LOSS_MAPS, "the map was not remembered"

    # The Electromagnetic tab moves to another point: this run is gone.  (The
    # lazy re-load from the pickle is stubbed too — it is how a benign cache
    # flush recovers, and here it would quietly undo what is being tested.)
    monkeypatch.setattr(sim, "_transient_field_snap", {})
    monkeypatch.setattr(sim, "_load_last_transient_field_snapshot",
                        lambda *a, **k: None)

    second = th.solve_thermal_field(**_req(cooling_mode="manual", h_conv=80.0))
    assert second["loss_source"]["kind"] == "thermal_store", second["loss_source"]
    assert second["P_loss_total_W"] == pytest.approx(first["P_loss_total_W"],
                                                     rel=1e-6)

    # A fresh process = an empty memory + the pickle on disk.
    time.sleep(0.4)          # the persist is a daemon thread
    th._LOSS_MAPS.clear()
    monkeypatch.setattr(th, "_LOSS_MAPS_LOADED", False, raising=True)
    third = th.solve_thermal_field(**_req(cooling_mode="manual", h_conv=120.0))
    assert third["loss_source"]["kind"] == "thermal_store", third["loss_source"]

    # ...and with the memory forgotten as well there is nothing left to serve:
    # the answer is the refusal, not a solve.
    th.clear_thermal_loss_maps()
    assert not th._LOSS_MAPS
    with pytest.raises(HTTPException) as exc:
        th.solve_thermal_field(**_req(cooling_mode="manual", h_conv=150.0))
    assert exc.value.detail["error_code"] == "no_electromagnetic_run"


def test_a_different_geometry_is_not_reused(run_snapshot, no_em_solve):
    """The hard one — a snapshot of another cross-section is another motor."""
    from motor_ai_sim.routes import thermal as th

    _refused(th, no_em_solve,
             geo=json.dumps({**GEO_30MM, "tooth_width": 2.4}))


def test_swapping_an_em_inert_material_keeps_the_run(run_snapshot, no_em_solve):
    """The insulation and the wire enamel are insulators the field never sees:
    swapping Nomex for Al2O3 (user 2026-09-07: "когда меняешь изоляцию,
    магнетизм не нужно пересчитывать, он никак не влияет") is a THERMAL change
    and must reuse the Electromagnetic run — while a different magnet is a
    different machine and must not (the test right below)."""
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.routes import thermal as th

    try:
        set_request_materials({"assignment": {"slot_insulation": "Al2O3"}})
        out = th.solve_thermal_field(**_req())
    finally:
        set_request_materials(None)
    assert out["loss_source"]["kind"] == "simulation_run", out["loss_source"]
    mu = out.get("materials_used") or {}
    assert mu.get("liner", {}).get("material") == "Al2O3", mu.get("liner")
    assert mu["liner"]["k"] > 10.0                     # alumina, not Nomex


def test_a_material_override_is_not_reused(run_snapshot, no_em_solve):
    """``?mat=`` is a per-REQUEST machine, and it changes the losses.

    A client evaluating another grade against the config's magnet on its own
    copy of the motor must not be handed the run's magnet losses; the override
    is part of the run key for exactly that reason.  (N52UH against the pinned
    F52SH — the grades were the other way round until 2026-09-09, when the
    fixture's materials were pinned to what the live config had carried.)
    """
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.routes import thermal as th

    try:
        set_request_materials({"assignment": {"magnet": "N52UH_150C"}})
        _refused(th, no_em_solve)
    finally:
        set_request_materials(None)


def test_a_single_frame_request_never_reads_the_run_store(run_snapshot,
                                                          no_em_solve):
    """One frame has no cycle to average and a run's map always is one.

    So there is nothing that could ever match it: the request is refused in words
    an engineer can act on ("run it with at least 2 steps per period"), rather
    than being handed a period average labelled as an instant — or, as it was
    until 2026-09-07, quietly given a single-frame solve of its own.
    """
    from motor_ai_sim.routes import thermal as th

    d = _refused(th, no_em_solve, n_steps_per_period=1)
    assert "single frame" in d["error"] and "no cycle to average" in d["error"]
    assert "at least 2 steps per period" in d["error"]


def test_a_run_that_stored_only_totals_is_refused_not_approximated(
        run_snapshot, no_em_solve, monkeypatch):
    """No per-element map = no answer, and the reason says which.

    Spreading component watts back over the mesh would need a distribution nobody
    measured, and solving one here is no longer this router's business — so the
    honest answer is to say what is missing.  This is the "never approximate
    silently" clause, and the reason reaches the PAYLOAD, not only the log.
    """
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.routes import thermal as th

    key = tuple(run_snapshot["probe"].values())
    entry = sim._transient_field_snap[key]
    stripped = {**entry, "field": {**entry["field"], "loss_dens": None}}
    monkeypatch.setitem(sim._transient_field_snap, key, stripped)

    _entry, _fields, why = th._snapshot_loss_entry(run_snapshot["probe"])
    assert _entry is None
    assert "per-element loss map" in why, why
    d = _refused(th, no_em_solve)
    assert "per-element loss map" in d["error"], d["error"]


# ---------------------------------------------------------------------------
# (4) the coupled loop: one map, analytic copper, zero EM solves
# ---------------------------------------------------------------------------

def test_the_copper_scaling_is_the_resistivity_ratio():
    """ρ(T)/ρ(T₀), NOT 1 + α(T − T₀).

    Both temperatures are referred to the 20 °C resistivity the material card
    quotes — the same model ``simulation.losses`` uses — and the linearised form
    is 5.7 % high from 120 °C to 180 °C, which is most of what the coupled loop
    is trying to resolve.
    """
    from motor_ai_sim.routes.thermal import (ALPHA_CU_PER_K, T_CU_REF_C,
                                             _cu_rho_ratio)

    a = float(ALPHA_CU_PER_K)
    assert a == pytest.approx(0.00393)
    exact = (1 + a * (180 - T_CU_REF_C)) / (1 + a * (120 - T_CU_REF_C))
    assert _cu_rho_ratio(180.0, 120.0) == pytest.approx(exact)
    assert _cu_rho_ratio(120.0, 120.0) == pytest.approx(1.0)
    # a COOLER winding is a lower resistivity, i.e. less DC loss
    assert _cu_rho_ratio(80.0, 120.0) < 1.0


def test_the_dc_and_ac_shares_of_the_copper_move_opposite_ways():
    """The correction that makes the analytic loop match the re-solved one.

    The DC loss goes as ρ_Cu(T) and the SOLVED proximity/skin loss as
    σ_Cu(T) = 1/ρ_Cu(T) — every conductor here is far thinner than the copper
    skin depth at its electrical frequency, which is the regime where
    P_eddy ∝ σ.  Scaling the whole copper by ρ alone made the coupled loop 2.7×
    too sensitive on the 30 mm fixture and settled it 1.4 K below the loop that
    re-solved the field on every pass.
    """
    from motor_ai_sim.routes.thermal import (ALPHA_CU_PER_K, T_CU_REF_C,
                                             _scaled_copper_map)

    a = float(ALPHA_CU_PER_K)
    r = (1 + a * (180 - T_CU_REF_C)) / (1 + a * (120 - T_CU_REF_C))
    # Tags: 1 stator, 2 coil, 4 magnet.  6 W DC + 2 W AC.
    em = {"domain_per_tri": [1, 2, 2, 4],
          "loss_density_per_tri": [10.0, 100.0, 200.0, 5.0],
          "P_cu_W": 8.0, "P_cu_exact_W": 8.0, "P_cu_ac_exact_W": 2.0,
          "P_fe_W": 3.0, "P_loss_total_W": 12.0, "P_loss_total_exact_W": 12.0}
    out, rep = _scaled_copper_map(em, t_ref_c=120.0, t_c=180.0)

    expect = 6.0 * r + 2.0 / r
    assert rep["rho_ratio"] == pytest.approx(r)
    assert rep["p_cu_dc_ref_W"] == pytest.approx(6.0)
    assert rep["p_cu_ac_ref_W"] == pytest.approx(2.0)
    assert rep["effective"] == pytest.approx(expect / 8.0)
    assert rep["effective"] < r, "the AC share must damp the ρ(T) rise"

    eff = rep["effective"]
    assert out["P_cu_exact_W"] == pytest.approx(expect)
    assert out["P_cu_ac_exact_W"] == pytest.approx(2.0 / r)
    assert out["loss_density_per_tri"] == pytest.approx(
        [10.0, 100.0 * eff, 200.0 * eff, 5.0])
    assert out["P_fe_W"] == 3.0                    # iron is HELD
    assert out["P_loss_total_exact_W"] == pytest.approx(12.0 + (expect - 8.0))
    assert em["P_cu_exact_W"] == 8.0               # the original is untouched

    # A map with no AC share at all is the pure ρ(T) case, unchanged.
    plain = {**em}
    plain.pop("P_cu_ac_exact_W")
    _out2, rep2 = _scaled_copper_map(plain, t_ref_c=120.0, t_c=180.0)
    assert rep2["effective"] == pytest.approx(r)


def test_the_coupled_loop_obtains_the_loss_map_exactly_once(run_snapshot):
    """Twelve passes, one map.

    Before 2026-09-07 every pass re-solved the electromagnetic transient, so a
    six-pass answer on a 200 mm machine was six times six minutes for a copper
    term that moves by a multiplication.  Counted at the field route, which is
    the only door to a loss map.
    """
    import motor_ai_sim.routes.simulation as sim
    from motor_ai_sim.routes import thermal as th

    real = sim.get_fem_field2d
    calls = []

    def _spy(*a, **kw):
        calls.append(kw)
        return real(*a, **kw)

    # Warm the THERMAL cache first: a cached answer carries no loss map, and a
    # loop handed one would have nothing to scale and would quietly go back to
    # solving the electromagnetics on every pass.
    th.solve_thermal_field(**_req())

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(sim, "get_fem_field2d", _spy)
        # 14 passes, not the 8 this was written with: the fixture now settles
        # at ~215 °C instead of ~150 °C (the adiabatic symmetry cut, see `AIR`),
        # and an under-relaxed loop takes more passes to close the last kelvin
        # from a bigger starting gap.  The claim being measured is one loss map
        # for the whole loop, and it is counted, not assumed.
        out = th.solve_coupled(max_iter=14, **_req())
    finally:
        mp.undo()

    assert out["ok"] is True
    assert len(calls) == 1, [c.get("coil_temp_c") for c in calls]
    assert calls[0].get("snapshot_only") is True     # …and it was the RUN's map
    assert out["loss_source"]["kind"] == "simulation_run"
    assert out["iterations"] >= 2, out["coil_temp_history_C"]

    cs = out["copper_scaling"]
    assert cs["alpha_per_k"] == pytest.approx(0.00393)
    assert cs["t_ref_c"] == pytest.approx(FAST["coil_temp_c"])
    assert cs["p_cu_ref_W"] > 0
    # the reported final copper IS the two shares moved by the stated law, at
    # the temperature the payload says the final map was scaled to
    from motor_ai_sim.routes.thermal import _cu_rho_ratio
    r = _cu_rho_ratio(cs["t_scaled_c"], cs["t_ref_c"])
    assert cs["rho_ratio"] == pytest.approx(r, rel=1e-3)
    assert cs["p_cu_dc_ref_W"] + cs["p_cu_ac_ref_W"] == pytest.approx(
        cs["p_cu_ref_W"], rel=0.01)
    assert cs["p_cu_final_W"] == pytest.approx(
        cs["p_cu_dc_ref_W"] * r + cs["p_cu_ac_ref_W"] / r, rel=0.01)
    # a converged loop scaled its last map to within tol_C of the answer
    assert abs(cs["t_scaled_c"] - out["coil_temp_converged_C"]) < out["tol_C"]
    assert "Iron, magnet, shaft and sleeve losses are" in cs["note"]


def test_the_analytic_copper_scaling_matches_a_run_solved_at_the_answer(
        run_snapshot):
    """The whole change has to be free of physics — audited the new way.

    Until this morning the claim was checked against the pre-2026-09-07 loop,
    reproduced literally: a full loss SOLVE inside every pass.  That check cannot
    be written any more, and its absence is the point — a map at another copper
    temperature is another Electromagnetic run, and this router does not make
    those.

    So the audit is done the way the product now does it, which is also what the
    dropped ``verify_em`` flag now tells the user to do: run the same point on
    the ELECTROMAGNETIC side at the converged winding temperature, and let the
    thermal route match it.  The copper the loop scaled to that temperature and
    the copper a run solved AT it report must agree to a few per cent, or the
    speed-up would be buying an answer — not a trade this project makes.
    """
    from motor_ai_sim.routes import thermal as th

    loop = th.solve_coupled(max_iter=14, **_req())
    assert loop["iterations"] >= 2, loop["coil_temp_history_C"]
    # not one electromagnetic solve for the whole fixed point
    assert loop["copper_scaling"]["em_solves"] == 0
    T = round(float(loop["coil_temp_converged_C"]), 1)

    # The run the user would make at the answer.  Rounded to 0.1 K because that
    # is the resolution `_field_snap_key_fields` keys `coil_temp_c` at: a run at
    # 148.37 °C and a request for 148.4 °C are the same run, and neither side may
    # guess which.
    store_em_run(GEO_30MM, run_id="2026-09-07T11:00:00",
                 phys={**FAST, "coil_temp_c": T})
    solved = th.solve_thermal_field(**_req(coil_temp_c=T))
    assert solved["loss_source"]["kind"] == "simulation_run", solved["loss_source"]

    p_scaled = float(loop["copper_scaling"]["p_cu_final_W"])
    p_solved = float(solved.get("P_cu_exact_W", solved["P_cu_W"]) or 0.0)
    assert p_solved > 0
    assert abs(p_scaled - p_solved) / p_solved < 0.05, (p_scaled, p_solved)
    # ...and the temperature the loop reports is the one that solved map gives
    T_solved = float((solved["components"]["winding"] or {})["avg"])
    assert abs(T_solved - float(loop["coil_temp_converged_C"])) < 1.0, (
        T_solved, loop["coil_temp_history_C"])


def test_verify_em_is_accepted_and_ignored_and_says_so(run_snapshot,
                                                       no_em_solve):
    """The audit flag is dead, and an old client is told rather than refused.

    ``verify_em=true`` re-solved the electromagnetic map at the converged
    temperature — exactly the solve this router may no longer start.  The route
    still accepts the parameter (a 422 over a flag that now does nothing would
    break a client for no benefit) and reports it under ``cooling.deprecated``;
    the loop function does not take it at all, and the sentinel proves no solve
    happened behind the word "verify".
    """
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    from motor_ai_sim.routes import thermal as th

    r = TestClient(app).get("/api/thermal/coupled",
                            params={**_req(), "max_iter": 3, "verify_em": True})
    assert r.status_code == 200, r.text[:600]
    out = r.json()
    assert "verification" not in out
    notes = " ".join(out["cooling"]["deprecated"])
    assert "verify_em is ignored" in notes, notes
    assert out["copper_scaling"]["em_solves"] == 0
    assert no_em_solve == []
    # the loop itself no longer has the parameter
    import inspect
    assert "verify_em" not in inspect.signature(th.solve_coupled).parameters


# ---------------------------------------------------------------------------
# the route and the capability keep their shape
# ---------------------------------------------------------------------------

def test_the_field_route_reports_the_loss_source(run_snapshot, no_em_solve):
    """The panel prints it, so it has to be on the wire."""
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app

    r = TestClient(app).get("/api/thermal/field", params=_req())
    assert r.status_code == 200, r.text[:600]
    src = r.json()["loss_source"]
    assert src["kind"] == "simulation_run" and src["run_id"] == RUN_ID


def test_the_module_capability_still_runs_the_same_loop(run_snapshot):
    """``solver.em_thermal`` must not grow a second copy of the fixed point."""
    from motor_ai_sim.modules.solvers import EmThermalCoupled

    res = EmThermalCoupled().run({**_req(), "max_iter": 3})
    assert res.raw is not None, res
    assert res.scalars.t_max_C is not None
    assert res.raw["copper_scaling"]["p_cu_ref_W"] > 0
    assert res.raw["loss_source"]["kind"] == "simulation_run"


def test_the_capabilities_refuse_with_the_route_s_own_sentence(run_snapshot,
                                                               no_em_solve):
    """``solver.thermal`` / ``solver.em_thermal`` never solve EM either.

    A module study that has not run the electromagnetics degrades — that is what
    ``ResultIR.failed`` is for — but it must degrade with the INSTRUCTION, not
    with ``HTTPException: 422: {'error': ...}``.  The route's own sentence names
    the operating point to run, and a study log that swallowed it into a dict
    repr would be a step back from the panel a user could have read.
    """
    from motor_ai_sim.modules.solvers import EmThermalCoupled, ThermalSolver

    payload = {**_req(I_phase_rms=FAST["I_phase_rms"] + 1.0), "max_iter": 3}
    for solver, physics in ((ThermalSolver(), "thermal"),
                            (EmThermalCoupled(), "em_thermal")):
        res = solver.run(payload)
        assert res.ok is False, res
        assert res.physics == physics
        assert res.error.startswith("no_electromagnetic_run: "), res.error
        assert "no Electromagnetic run of this machine at" in res.error
        assert "run it on the Electromagnetic tab first" in res.error
    assert no_em_solve == [], "a capability solved the electromagnetics"
