"""The one-click auto-optimization route.

The whole point of ``POST /api/optimization/auto`` is that the user supplies ONE
number and the server assembles a run that already obeys this project's standing
conventions.  That promise is only worth something if it is TESTED, because the
failure mode is silent: a run that quietly optimises the wrong objective, at the
wrong operating point, inside a box the user never asked for, still returns a
plausible-looking motor.

These tests exercise the ASSEMBLY (cheap, no FEM).  Launching is covered only to
the extent that garbage input is refused before any solve is paid for.
"""

import math

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.routes import optimization as O

client = TestClient(app)


@pytest.fixture
def no_rate_flush(monkeypatch):
    """Fake eval timings must never reach the PERSISTED rate file.

    _record_eval_seconds flushes to config/.eval_rate.json every 10 samples, so
    a test feeding it synthetic 2 s and 300 s evals would leave the real backend
    quoting a made-up cost to the real user — a test that lies to production."""
    monkeypatch.setattr(O, "_save_eval_rate", lambda: None)


def _plan(max_ripple=5.0, budget=0):
    r = client.post("/api/optimization/auto/plan",
                    json={"max_ripple_pct": max_ripple, "budget_evals": budget})
    assert r.status_code == 200, r.text
    return r.json()["plan"]


class TestAssemblyFromSimulationSettings:
    """The operating point comes from the Simulation tab, never from a default."""

    def test_operating_point_matches_the_simulation_config(self):
        from motor_ai_sim.config import get_config
        sim = get_config().get("simulation", {})
        op = _plan()["operating_point"]
        assert op["current_a"] == pytest.approx(float(sim["max_current"]))
        assert op["rpm"] == pytest.approx(float(sim["rpm"]))
        assert op["gamma_deg"] == pytest.approx(float(sim["phase_offset_deg"]))

    def test_coil_temperature_comes_from_the_simulation_config(self):
        from motor_ai_sim.config import get_config
        sim = get_config().get("simulation", {})
        assert _plan()["eval"]["coil_temp_c"] == pytest.approx(float(sim["coil_temp_c"]))

    def test_variables_are_exactly_the_sweep_whitelist(self):
        from motor_ai_sim.config import get_config
        cfg = get_config()
        wl = [n for n in cfg["sweep_whitelist"]
              if isinstance(cfg["geometry"].get(n), (int, float))]
        assert [v["name"] for v in _plan()["variables"]] == wl

    def test_rotor_hole_is_in_the_curated_set(self):
        # Named in the brief because it was the one that kept getting dropped.
        assert "rotor_hole" in {v["name"] for v in _plan()["variables"]}

    def test_every_variable_starts_at_the_current_design_value(self):
        from motor_ai_sim.config import get_config
        geo = get_config()["geometry"]
        for v in _plan()["variables"]:
            assert v["x0"] == pytest.approx(float(geo[v["name"]]))


class TestStandingObjective:
    """objective='baseline_line' is a rule, not a preference."""

    def test_objective_is_the_perpendicular_baseline_metric(self):
        assert _plan()["objective"] == "baseline_line"

    def test_the_objective_cannot_be_overridden_by_the_request(self):
        # An extra field must not steer the objective; pydantic ignores unknown
        # keys, and the assembled plan must still be the standing one.
        r = client.post("/api/optimization/auto/plan",
                        json={"max_ripple_pct": 5.0, "objective": "product",
                              "w_eff": 3, "w_td": 0})
        assert r.status_code == 200, r.text
        assert r.json()["plan"]["objective"] == "baseline_line"

    def test_the_ripple_gate_is_enforced_in_the_cost_not_only_on_the_chart(self):
        p = _plan(max_ripple=4.0)
        assert p["ripple_max_pct"] == pytest.approx(4.0)
        assert p["ripple_penalty_lambda"] > 0.0

    def test_the_eval_path_is_the_honest_p2_one(self):
        ev = _plan()["eval"]
        assert ev["element_order"] == 2          # P2 is the only basis
        assert ev["structured_gap"] is True      # belt gap mesh (honest ripple)
        assert ev["torque_filter"] is False      # RAW ripple, not band-limited
        assert ev["rotor_eddy"] is True          # same loss model as Simulation

    def test_ripple_is_not_sampled_below_the_aliasing_floor(self):
        # A run whose constraint IS ripple may not measure ripple on an aliased
        # window, whatever the Simulation tab's frame count happens to be.
        assert _plan()["eval"]["steps_per_period"] >= 48


class TestSearchRangeIsNotBoxed:
    """The whitelist says WHICH knobs move — the schema does not say how far."""

    def test_no_upper_bound_is_imposed(self):
        assert all(v["hi"] is None for v in _plan()["variables"])

    def test_lower_bound_is_physical_positivity_not_the_schema_minimum(self):
        from motor_ai_sim.config import get_config
        schema = get_config().get("geometry_schema", {})
        for v in _plan()["variables"]:
            assert v["lo"] == (1.0 if v["is_int"] else 0.0)
            s_min = (schema.get(v["name"]) or {}).get("min")
            if s_min is not None and float(s_min) > 0 and not v["is_int"]:
                # The schema floor is a UI convenience; the validator is the fence.
                assert v["lo"] < float(s_min)

    def test_a_variable_sitting_at_zero_still_gets_a_usable_step(self):
        zeros = [v for v in _plan()["variables"] if v["x0"] == 0.0]
        for v in zeros:
            assert v["sigma"] > 0.0, (
                f"{v['name']} starts at 0 and would be frozen by a "
                "percentage-of-value step")

    def test_millimetre_sigma_scales_with_the_motor_not_only_the_value(self):
        p = _plan()
        d = p["stator_diameter"]
        for v in p["variables"]:
            if v["unit"].strip().lower() == "mm":
                # 1e-4 slack: sigma is rounded to 4 decimals for the wire.
                assert v["sigma"] >= max(0.25 * abs(v["x0"]), 0.015 * d) - 1e-4

    def test_dimensionless_sigma_has_an_absolute_floor(self):
        for v in _plan()["variables"]:
            if v["unit"].strip() == "" and not v["is_int"]:
                assert v["sigma"] >= max(0.25 * abs(v["x0"]), 0.01) - 1e-4

    def test_integer_sigma_can_actually_move_the_integer(self):
        for v in _plan()["variables"]:
            if v["is_int"]:
                assert v["sigma"] >= 1.0


class TestSigmaUnit:
    """_auto_sigma in isolation — the rule, stated once, checked directly."""

    def test_mm_variable_at_zero_uses_the_machine_scale(self):
        assert O._auto_sigma(0.0, "mm", False, 40.0) == pytest.approx(0.6)

    def test_mm_variable_that_is_large_uses_its_own_scale(self):
        assert O._auto_sigma(20.0, "mm", False, 40.0) == pytest.approx(5.0)

    def test_dimensionless_at_zero_uses_the_absolute_floor(self):
        assert O._auto_sigma(0.0, "", False, 40.0) == pytest.approx(0.01)

    def test_integer_never_falls_below_one(self):
        assert O._auto_sigma(1.0, "", True, 40.0) == pytest.approx(1.0)


class TestGarbageRippleIsRefused:
    """Client-facing validation: loud, engineer-readable, before any FEM."""

    @pytest.mark.parametrize("bad", [0, -1, -0.001, 100.1, 1e9])
    def test_out_of_range_ripple_is_rejected(self, bad):
        r = client.post("/api/optimization/auto/plan", json={"max_ripple_pct": bad})
        assert r.status_code == 422, r.text
        assert "max_ripple_pct" in r.text

    @pytest.mark.parametrize("bad", ["five", None, [], {}])
    def test_non_numeric_ripple_is_rejected(self, bad):
        r = client.post("/api/optimization/auto/plan", json={"max_ripple_pct": bad})
        assert r.status_code == 422, r.text

    def test_nan_ripple_is_rejected(self):
        # NaN survives pydantic's float coercion and would compare False against
        # every gate — i.e. an unconstrained run wearing a constraint's name.
        with pytest.raises(Exception) as ei:
            O._auto_assemble(float("nan"))
        assert "finite" in str(ei.value).lower()

    def test_the_message_names_the_field_and_the_value(self):
        r = client.post("/api/optimization/auto/plan", json={"max_ripple_pct": -3})
        body = r.json()["detail"]
        assert "max_ripple_pct" in body and "-3" in body

    def test_a_rejected_request_never_starts_a_run(self):
        before = dict(O._descent_state)
        r = client.post("/api/optimization/auto", json={"max_ripple_pct": 0})
        assert r.status_code == 422
        assert O._descent_state.get("running") == before.get("running")


class TestCostHonesty:
    """A run says what it costs before it starts."""

    def test_the_plan_quotes_evals_and_seconds_per_eval(self):
        c = _plan(budget=60)["cost"]
        assert c["n_evals_max"] == 60
        assert c["s_per_eval"] > 0
        assert c["est_wall_seconds"] > 0

    def test_the_quote_says_whether_it_was_measured_or_estimated(self):
        assert _plan()["cost"]["s_per_eval_source"] in ("measured", "estimate")

    def test_the_budget_is_a_hard_cap_the_generations_respect(self):
        p = _plan(budget=60)
        assert 2 + p["generations"] * p["population"] <= p["budget_evals"]

    def test_measured_rate_is_used_once_evals_have_been_timed(self, no_rate_flush):
        saved = list(O._EVAL_SECS)
        try:
            O._EVAL_SECS.clear()
            assert O.measured_eval_seconds(48)["source"] == "estimate"
            for _ in range(5):
                O._record_eval_seconds(3.0)
            m = O.measured_eval_seconds(48)
            assert m["source"] == "measured"
            assert m["s_per_eval"] == pytest.approx(3.0)
            assert m["n_samples"] == 5
        finally:
            O._EVAL_SECS.clear()
            O._EVAL_SECS.extend(saved)

    def test_one_slow_outlier_does_not_inflate_the_quote(self, no_rate_flush):
        # Median, not mean: a single 300 s timeout must not triple the price.
        saved = list(O._EVAL_SECS)
        try:
            O._EVAL_SECS.clear()
            for _ in range(9):
                O._record_eval_seconds(2.0)
            O._record_eval_seconds(300.0)
            assert O.measured_eval_seconds(48)["s_per_eval"] == pytest.approx(2.0)
        finally:
            O._EVAL_SECS.clear()
            O._EVAL_SECS.extend(saved)


class TestRejectionAccounting:
    """A run that fences most of what it samples must SAY so."""

    @pytest.mark.parametrize("err,kind", [
        ("3 geometry violations — the cross-section is not buildable", "geometry"),
        ("infeasible winding: 40 turns cannot fit the slot even clamped", "geometry"),
        ("2 unconverged FEM frames in the window", "unconverged"),
        ("timeout", "timeout"),
        ("subprocess crashed", "other"),
        # The pre-mesh fence: a buildable candidate whose mesh would cascade is
        # rejected by geo_mesh.MeshBudgetExceeded in seconds — its OWN class,
        # not "timeout" (which it pre-empts) and not "other" (which hides it).
        ("MeshBudgetExceeded: mesh budget: quality meshing of this "
         "cross-section hit the 200000-point Steiner cap", "mesh"),
        ("mesh budget: 310000 stator + 120000 rotor triangles exceed the "
         "400000-triangle budget", "mesh"),
    ])
    def test_errors_are_classified_by_which_fence_stopped_them(self, err, kind):
        assert O._auto_classify_error(err) == kind

    def test_the_real_exception_message_maps_to_the_mesh_class(self):
        # The classifier keys on the message geo_mesh actually raises — if the
        # wording drifts apart, mesh rejects silently become "other".
        from motor_ai_sim.simulation.geo_mesh import MeshBudgetExceeded
        e = MeshBudgetExceeded(
            "mesh budget: quality meshing of this cross-section hit the "
            "1000-point Steiner cap (2400 triangles and still refining; "
            "budget 400000 triangles for the whole mesh).")
        assert O._auto_classify_error(str(e)) == "mesh"

    def test_the_reject_block_counts_every_fence_separately(self):
        counts = {"ok": 5, "geometry": 3, "unconverged": 2, "mesh": 4,
                  "timeout": 1, "other": 0}
        rj = O._auto_reject_block(counts)
        assert rj["evaluated"] == 15
        assert rj["ok"] == 5
        assert rj["rejected"] == 10
        assert rj["rejected_geometry"] == 3
        assert rj["rejected_unconverged"] == 2
        assert rj["rejected_mesh"] == 4
        assert rj["rejected_timeout"] == 1
        assert rj["rejected_other"] == 0
        assert rj["reject_pct"] == pytest.approx(66.7)

    def test_the_reject_block_is_sane_when_nothing_ran(self):
        counts = {"ok": 0, "geometry": 0, "unconverged": 0, "mesh": 0,
                  "timeout": 0, "other": 0}
        rj = O._auto_reject_block(counts)
        assert rj["evaluated"] == 0 and rj["reject_pct"] == 0.0


class TestMeshBudgetFence:
    """The cheap pre-mesh reject: Triangle itself, Steiner-capped.

    The fence must (a) stay OFF unless the eval subprocess arms it, (b) change
    NOTHING for a mesh that fits the budget, and (c) reject a cascading mesh
    with the "mesh budget" message the classifier keys on.  (a)+(b) are the
    no-false-reject half of the contract; (c) is the fence itself."""

    # A 10x10 mm square PSLG: with a fine max-area constraint the quality mesher
    # needs thousands of Steiner points — a deterministic stand-in for the
    # refinement cascade a pathological candidate causes.
    @staticmethod
    def _square():
        import numpy as np
        V = np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]])
        S = np.array([[0, 1], [1, 2], [2, 3], [3, 0]])
        return V, S

    @pytest.fixture(autouse=True)
    def _disarmed(self):
        # Every test starts and ends with the budget OFF, however it exits —
        # a leaked armed budget would fail unrelated meshing tests.
        from motor_ai_sim.simulation import geo_mesh as G
        G.set_tri_budget(None)
        yield
        G.set_tri_budget(None)

    def test_the_budget_is_off_by_default(self):
        from motor_ai_sim.simulation import geo_mesh as G
        assert G.tri_budget() is None

    def test_arm_and_disarm(self):
        from motor_ai_sim.simulation import geo_mesh as G
        G.set_tri_budget(400_000)
        assert G.tri_budget() == 400_000
        G.set_tri_budget(None)
        assert G.tri_budget() is None
        G.set_tri_budget(0)                      # 0 = off, not "reject all"
        assert G.tri_budget() is None

    def test_unarmed_meshing_is_never_rejected(self):
        from motor_ai_sim.simulation import geo_mesh as G
        V, S = self._square()
        _, T = G._triangulate(V, S, 0.01, quality=20, hole=False)
        assert len(T) > 2000                     # it really did refine

    def test_a_cascading_mesh_is_rejected_with_the_mesh_budget_message(self):
        from motor_ai_sim.simulation import geo_mesh as G
        V, S = self._square()
        G.set_tri_budget(2000)                   # ~10k tris needed >> budget
        with pytest.raises(G.MeshBudgetExceeded) as ei:
            G._triangulate(V, S, 0.01, quality=20, hole=False)
        assert str(ei.value).startswith("mesh budget:")

    def test_a_mesh_under_the_budget_is_bit_identical_to_the_unarmed_run(self):
        # The whole no-false-reject argument rests on this: an armed budget
        # that is NOT hit must not change the mesh the candidate is scored on.
        import numpy as np
        from motor_ai_sim.simulation import geo_mesh as G
        V, S = self._square()
        Vu, Tu = G._triangulate(V, S, 1.0, quality=20, hole=False)
        G.set_tri_budget(10_000_000)
        Va, Ta = G._triangulate(V, S, 1.0, quality=20, hole=False)
        assert np.array_equal(Vu, Va) and np.array_equal(Tu, Ta)

    def test_the_eval_subprocess_budget_clears_a_healthy_mesh_with_margin(self):
        # refine_proc's constant must sit far above anything a healthy design
        # meshes at (~25-60k tris/eval measured), else the fence would reject
        # designs that solve fine — the failure mode this feature must never
        # have.  10x the top of the healthy range is the floor of "far above".
        from motor_ai_sim.optimization.refine_proc import _MESH_TRI_BUDGET
        assert _MESH_TRI_BUDGET >= 10 * 40_000

    def test_area_only_meshing_is_exempt(self):
        # Area-only CDT (no quality flag) cannot cascade — the rotor fallback
        # path relies on it building unconditionally.  The cap must not apply.
        from motor_ai_sim.simulation import geo_mesh as G
        V, S = self._square()
        G.set_tri_budget(50)                     # absurdly small on purpose
        _, T = G._triangulate(V, S, 1.0, quality=None, hole=False)
        assert len(T) >= 2                       # built, not rejected


class TestGeometryStamp:
    """The Compare point carries a machine stamp the frontend can compare with."""

    def test_signature_matches_the_frontend_dialect(self):
        # geoSignature(): numeric fields only, sorted by key, `k:v` joined by '|',
        # numbers formatted the way a JS template literal formats them.
        sig = O._geo_signature({"b": 2.0, "a": 1.5, "z": "steel", "c": True})
        assert sig == "a:1.5|b:2"

    def test_integral_floats_lose_their_trailing_zero_like_javascript(self):
        assert O._js_number(12.0) == "12"
        assert O._js_number(5.6) == "5.6"


class TestNoSideEffects:
    """Assembling (and refusing) must never touch the user's design."""

    def test_planning_does_not_write_the_config(self):
        from pathlib import Path
        import hashlib
        p = Path(O.__file__).resolve().parents[3] / "config" / "motor_config.yaml"
        before = hashlib.sha256(p.read_bytes()).hexdigest()
        _plan(3.0)
        client.post("/api/optimization/auto/plan", json={"max_ripple_pct": -1})
        assert hashlib.sha256(p.read_bytes()).hexdigest() == before


class TestAutoStatusEndpoint:
    """/auto/status is the name anyone probing this API reaches for first."""

    def test_status_serves_the_finished_run(self):
        r = client.get("/api/optimization/auto/status")
        # 404 only when this server has never run an auto optimization; the
        # message must then say where progress DOES live, not just "Not Found".
        if r.status_code == 404:
            assert "descent/progress" in r.json()["detail"]
            return
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["objective"] == "baseline_line"
        assert b["progress_channel"] == "/api/optimization/descent/progress"

    def test_status_states_the_verdict_instead_of_leaving_it_to_the_reader(self):
        r = client.get("/api/optimization/auto/status")
        if r.status_code == 404:
            return
        b = r.json()
        if b.get("F") is None:
            return
        # F is the signed perpendicular distance above the current-only line.
        # A run that did NOT beat simply raising the current must say so.
        assert b["above_baseline_line"] is (b["F"] > 0)
        assert isinstance(b["verdict"], str) and b["verdict"]
        if b["F"] <= 0:
            assert "does NOT beat" in b["verdict"]

    def test_the_shared_progress_channel_still_serves(self):
        # The auto run deliberately reports on the optimizer's existing channel
        # so every chart and the Apply path keep working; if that 404s, the UI
        # goes blind mid-run.
        assert client.get("/api/optimization/descent/progress").status_code == 200


class TestBudgetScalesWithDimension:
    """A flat eval budget is a fiction in high dimensions.

    CMA-ES estimates an N x N covariance; with 18 variables that is 171 free
    parameters, and a 120-eval run that fences half of what it samples leaves
    fewer informative points than parameters.  The default budget therefore
    scales with the number of variables the whitelist actually opens."""

    def test_the_default_budget_scales_with_the_number_of_variables(self):
        p = _plan()
        n = len(p["variables"])
        assert p["budget_evals"] >= max(O._AUTO_DEFAULT_BUDGET,
                                        O._AUTO_BUDGET_PER_VAR * n)

    def test_the_default_budget_is_a_whole_number_of_generations(self):
        # CMA-ES only learns at a generation boundary, so a half-funded final
        # generation buys nothing - the quote pays for it or does not start it.
        p = _plan()
        assert 2 + p["generations"] * p["population"] == p["budget_evals"]

    def test_a_small_whitelist_still_gets_the_standing_floor(self):
        # 24/var must never REDUCE the budget below the 120 it has always been.
        assert (max(O._AUTO_DEFAULT_BUDGET, O._AUTO_BUDGET_PER_VAR * 2)
                == O._AUTO_DEFAULT_BUDGET)

    def test_an_explicit_budget_is_still_a_hard_cap(self):
        # The dimension rule applies to the DEFAULT only: a number the user typed
        # is a promise, and rounding it up would spend evals they did not agree to.
        p = _plan(budget=60)
        assert p["budget_evals"] == 60
        assert 2 + p["generations"] * p["population"] <= 60

    def test_the_quote_reflects_the_scaled_budget(self):
        p = _plan()
        c = p["cost"]
        assert c["n_evals_max"] == p["budget_evals"]
        assert c["est_cpu_seconds"] == int(round(p["budget_evals"] * c["s_per_eval"]))


class _FEMReached(Exception):
    """The eval subprocess got past every geometry gate to the FEM call."""


@pytest.fixture
def subprocess_geometry_verdict(monkeypatch):
    """The eval subprocess's OWN geometry verdict, with the FEM short-circuited.

    ``refine_proc.run_one`` is exactly what ``python -m ...refine_proc`` calls on
    the decoded stdin spec, and every geometry gate runs before the kernel call,
    so stubbing the kernel yields the subprocess's verdict for the price of the
    polygon build.  Returns None when the candidate would have reached the FEM,
    else the error string the subprocess would have reported."""
    from motor_ai_sim.optimization import refine_proc as RP
    from motor_ai_sim.simulation import geo_mesh as GM

    class _Stub:
        def run(self, *a, **k):
            raise _FEMReached()

    monkeypatch.setattr(RP, "_kernel", lambda: _Stub())

    def verdict(overrides):
        try:
            RP.run_one(dict(overrides), 10.0, 8, 120.0)
        except _FEMReached:
            return None
        except Exception as e:      # noqa: BLE001 - the subprocess catches this too
            return str(e)
        finally:
            GM.set_tri_budget(None)   # run_one's disarm is skipped when we raise
        return None

    try:
        yield verdict
    finally:
        GM.set_tri_budget(None)


def _random_candidates(n, seed=20260802):
    """n candidates drawn the way the CMA loop draws them: the current design
    perturbed by the run's own per-variable sigma, unbounded above, floored at
    the variable's physical lower bound."""
    import random
    rng = random.Random(seed)
    specs = _plan()["variables"]
    out = []
    for _ in range(n):
        d = {}
        for v in specs:
            # 2.5 sigma so the sample straddles the feasible wall - a screen that
            # only ever sees valid candidates proves nothing.
            x = float(v["x0"]) + 2.5 * float(v["sigma"]) * rng.gauss(0.0, 1.0)
            x = max(float(v["lo"]), x)
            if v["is_int"]:
                x = float(max(1, int(round(x))))
            elif v.get("quant"):
                x = round(x / float(v["quant"])) * float(v["quant"])
            d[v["name"]] = x
        out.append(d)
    return out


class TestGeometryPreFence:
    """The in-process screen must agree with the subprocess it replaces.

    A false 'valid' costs one FEM eval and is harmless.  A false 'invalid'
    silently deletes a reachable design from the search - the whole point of an
    unboxed range is that the validator, and only the validator, decides what is
    reachable.  So the screen is allowed to be incomplete, never wrong."""

    N_DRAWS = 24

    def test_the_current_design_passes(self):
        assert O._auto_prefence({}) is None

    def test_verdicts_agree_with_the_eval_subprocess(self, subprocess_geometry_verdict):
        disagreements = []
        n_invalid = 0
        for cand in _random_candidates(self.N_DRAWS):
            mine = O._auto_prefence(cand)
            theirs = subprocess_geometry_verdict(cand)
            n_invalid += int(theirs is not None)
            if (mine is None) != (theirs is None):
                disagreements.append((cand, mine, theirs))
        # A FALSE INVALID is the fatal one: the screen rejected something the
        # subprocess would have evaluated.
        false_invalid = [d for d in disagreements if d[1] is not None and d[2] is None]
        assert not false_invalid, (
            "pre-fence rejected {} candidate(s) the eval subprocess accepts - "
            "these designs would silently vanish from the search: {}".format(
                len(false_invalid), false_invalid[:1]))
        # And in practice the screen is exact, not merely conservative.
        assert not disagreements, disagreements[:1]
        # The draw has to actually exercise the fence, or this proves nothing.
        assert n_invalid > 0, ("no candidate in {} draws was invalid - widen the "
                               "draw, this test is asleep".format(self.N_DRAWS))

    def test_an_unbuildable_candidate_is_named_the_way_the_run_log_names_it(self):
        cfg = O.get_config()
        geo = dict(cfg.get("geometry", {}))
        why = O._auto_prefence({"magnet_height": float(geo["magnet_height"]) * 6.0})
        assert why, "a 6x magnet must not pass the screen"
        # The message has to land in the SAME class the reject chips count, or a
        # pre-fenced candidate would be filed under 'other'.
        assert O._auto_classify_error(why) == "geometry"

    def test_a_screen_that_cannot_decide_defers_instead_of_rejecting(self, monkeypatch):
        # If the validator itself blows up, the honest answer is "cannot tell",
        # and that must cost one FEM eval - not a silent deletion.
        import motor_ai_sim.geometry_validation as GV

        def _boom(*a, **k):
            raise RuntimeError("polygon build exploded")

        monkeypatch.setattr(GV, "validate_geometry", _boom)
        assert O._auto_prefence({}) is None

    def test_the_screen_judges_the_clamped_cross_section(self):
        # refine_proc clamps the winding knobs and scores the CLAMPED geometry,
        # so a candidate that is only invalid BEFORE clamping is not a reject.
        cfg = O.get_config()
        geo = dict(cfg.get("geometry", {}))
        from motor_ai_sim.geometry_constraints import clamp
        over = {"wire_height": float(geo["slot_height"])}   # absurd, but clampable
        clamped, applied = clamp({**geo, **over})
        assert applied, "this fixture must actually trigger the clamp"
        assert O._auto_prefence(over) == O._auto_prefence(
            {"wire_height": clamped["wire_height"]})


class TestResamplingUsesTheDocumentedPycmaPath:
    """Replacement draws must be first-class members of the generation.

    pycma's ask_geno docstring blesses ``X.append(es.ask(1)[0])`` before
    ``es.tell(X, ...)``; because the extra point came from ask() it sits in
    ``es.sent_solutions``, so tell() takes its genotype from there instead of
    treating it as a foreign point to repair."""

    def test_tell_accepts_points_that_came_from_ask_one(self):
        cma = pytest.importorskip("cma")
        es = cma.CMAEvolutionStrategy([1.0, 1.0, 1.0], 0.5,
                                      {"popsize": 6, "verbose": -9, "seed": 7})
        sols = es.ask()
        sols[2] = es.ask(1)[0]          # the resampling move, verbatim
        sols[5] = es.ask(1)[0]
        assert all(es.sent_solutions.get(s) is not None for s in sols), (
            "a resampled point is not in sent_solutions - tell() would repair it "
            "as a foreign point instead of using its true genotype")
        es.tell(sols, [float(sum(x * x for x in s)) for s in sols])
        assert es.countiter == 1

    def test_the_running_spread_is_settable_and_floored(self):
        cma = pytest.importorskip("cma")
        es = cma.CMAEvolutionStrategy([1.0, 1.0], 1.0,
                                      {"popsize": 4, "verbose": -9, "seed": 7})
        s0 = float(es.sigma)
        floor = O._AUTO_SIGMA_SHRINK_FLOOR * s0
        for _ in range(20):
            es.sigma = max(es.sigma * O._AUTO_SIGMA_SHRINK, floor)
        assert es.sigma == pytest.approx(floor)
        assert O._AUTO_SIGMA_SHRINK < 1.0 and 0.0 < O._AUTO_SIGMA_SHRINK_FLOOR < 1.0


def _fake_specs():
    return [{"name": "tooth_width", "x0": 5.0, "sigma": 0.5, "lo": 0.0,
             "is_int": False, "quant": 0.1},
            {"name": "slot_height", "x0": 18.0, "sigma": 0.5, "lo": 0.0,
             "is_int": False, "quant": 0.1},
            {"name": "magnet_height", "x0": 4.0, "sigma": 0.3, "lo": 0.0,
             "is_int": False, "quant": 0.1}]


def _fake_base():
    # A baseline line whose weights are 1 on td and 1 on eff, so F is a simple
    # (and hand-checkable) function of the two metrics.
    return {"torque_per_mass_Nm_kg": 10.0, "efficiency": 0.90,
            "_bline": {"td_a": 10.0, "eff_a": 0.90, "w_td": 1.0, "w_eff": 1.0,
                       "norm": 1.0}}


def _rec(fp="FP", I=100.0, gamma=0.0, ripple=3.0, td=11.0, eff=0.91, **ov):
    return {"cfg_fp": fp, "current_a": I, "gamma_deg": gamma, "ripple": ripple,
            "td": td, "eff": eff, "torque": 50.0,
            "overrides": ov or {"tooth_width": 5.5}}


class TestWarmStartSelection:
    """Seeding the search from evals already paid for - but only from evals that
    describe THIS machine at THIS operating point.  Everything else is the
    stale-machine failure wearing a helpful face."""

    ARGS = dict(cfg_fp="FP", current_a=100.0, gamma_deg=0.0, ripple_max=5.0)

    def _pick(self, recs, **kw):
        a = dict(self.ARGS)
        a.update(kw)
        return O._auto_warm_start(recs, _fake_specs(), base=_fake_base(), **a)

    def test_too_few_points_is_not_evidence(self):
        assert self._pick([_rec(), _rec()]) is None

    def test_three_matching_points_seed_the_run(self):
        s = self._pick([_rec(), _rec(), _rec()])
        assert s is not None and s["n"] == 3
        assert len(s["x"]) == 3

    def test_another_machines_evals_are_never_used(self):
        # Same schema, different cross-section: overrides are a DELTA on a
        # baseline geometry, so these numbers describe a different motor.
        assert self._pick([_rec(fp="OTHER") for _ in range(9)]) is None

    def test_rows_written_before_the_fingerprint_existed_are_skipped(self):
        old = _rec()
        old.pop("cfg_fp")
        assert self._pick([old, old, old, old]) is None

    def test_a_different_operating_point_is_a_different_problem(self):
        assert self._pick([_rec(I=140.0) for _ in range(5)]) is None
        assert self._pick([_rec(gamma=25.0) for _ in range(5)]) is None

    def test_points_over_the_ripple_gate_are_not_feasible(self):
        assert self._pick([_rec(ripple=9.0) for _ in range(5)]) is None

    def test_it_seeds_from_the_best_point_by_the_runs_own_objective(self):
        recs = [_rec(td=10.5, eff=0.905, tooth_width=5.1),
                _rec(td=12.0, eff=0.930, tooth_width=6.4),   # best F
                _rec(td=11.0, eff=0.910, tooth_width=5.8)]
        s = self._pick(recs)
        assert s["x"][0] == pytest.approx(6.4)
        # F = w_td*(td-td_a) + w_eff*(eff-eff_a) over norm=1
        assert s["F"] == pytest.approx(2.0 + 0.03)

    def test_variables_the_cached_point_does_not_carry_come_from_todays_design(self):
        s = self._pick([_rec(tooth_width=6.0) for _ in range(3)])
        assert s["x"][0] == pytest.approx(6.0)
        assert s["x"][1] == pytest.approx(18.0)   # spec x0
        assert s["x"][2] == pytest.approx(4.0)

    def test_the_seed_never_lands_below_a_variables_physical_floor(self):
        s = self._pick([_rec(tooth_width=-3.0) for _ in range(3)])
        assert s["x"][0] >= 0.0

    def test_an_unbuildable_seed_falls_through_to_the_next_best(self):
        recs = [_rec(td=12.0, eff=0.93, tooth_width=6.4),    # best, but rejected
                _rec(td=11.5, eff=0.92, tooth_width=5.9),
                _rec(td=11.0, eff=0.91, tooth_width=5.2)]
        assert self._pick(recs)["x"][0] == pytest.approx(6.4)
        s2 = O._auto_warm_start(recs, _fake_specs(), base=_fake_base(),
                                accept=lambda d: d["tooth_width"] < 6.0,
                                **self.ARGS)
        assert s2["x"][0] == pytest.approx(5.9)

    def test_no_acceptable_point_means_no_seed(self):
        recs = [_rec() for _ in range(5)]
        assert O._auto_warm_start(recs, _fake_specs(), base=_fake_base(),
                                  accept=lambda d: False, **self.ARGS) is None

    def test_garbage_rows_do_not_crash_the_selection(self):
        bad = [None, "nonsense", {}, {"cfg_fp": "FP"},
               {"cfg_fp": "FP", "overrides": {}, "current_a": "x"},
               {"cfg_fp": "FP", "overrides": {}, "current_a": 100.0,
                "gamma_deg": 0.0, "ripple": float("nan"), "td": 1.0, "eff": 1.0}]
        assert self._pick(bad) is None
        assert self._pick(bad + [_rec(), _rec(), _rec()])["n"] == 3

    def test_the_live_logger_stamps_the_fingerprint_the_selection_reads(self):
        # The selection is only as honest as the stamp; if _log_eval stopped
        # writing cfg_fp, every future warm start would silently be disabled.
        import inspect
        assert "cfg_fp" in inspect.getsource(O._log_eval)
        assert O._config_fingerprint()


class TestPreFenceAccounting:
    """Pre-fenced candidates cost no FEM eval - and the numbers must say so."""

    def test_resamples_are_reported_without_inflating_the_eval_count(self):
        counts = {"ok": 4, "geometry": 1, "unconverged": 0, "mesh": 0,
                  "timeout": 0, "other": 0, "resampled": 17, "prefenced": 3}
        rj = O._auto_reject_block(counts)
        assert rj["resampled_geometry"] == 17
        assert rj["prefenced_geometry"] == 3
        # 17 resamples + 3 fences were never submitted to a subprocess, so the
        # budget the user was quoted must not appear to have been spent on them.
        assert rj["evaluated"] == 5
        assert rj["rejected"] == 1

    def test_the_block_still_reads_a_counts_dict_without_the_new_keys(self):
        rj = O._auto_reject_block({"ok": 1, "geometry": 1, "unconverged": 0,
                                   "mesh": 0, "timeout": 0, "other": 0})
        assert rj["resampled_geometry"] == 0 and rj["prefenced_geometry"] == 0
