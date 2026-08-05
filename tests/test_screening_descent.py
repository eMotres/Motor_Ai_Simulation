"""The screening-descent optimizer mode (``POST /api/optimization/auto`` with
``mode='screen'``).

WHY THIS MODE EXISTS, and therefore what these tests have to protect:

On the CIANO20 150_35 the CMA-ES one-click run spent 434 evals and its best
candidate inside the ripple gate scored F = -0.0173 on the run's own
perpendicular-baseline metric — it never beat the design it started from.  The
user then beat it BY HAND at the same fixed operating point, reaching
F = +0.00221, by (1) deviating EVERY variable a little to see which way each one
pushes the machine, (2) descending along the most influential ones, (3) polishing
with the rest, never touching more than about four at a time.

So the things that must not silently rot are:
  • the screening arithmetic — a gradient measured wrong sends the whole descent
    the wrong way, and nothing downstream would notice;
  • the choice of WHICH variables are influential — if that collapses to "all of
    them" the method degenerates into the search that already failed;
  • the group cycling — the polish phase is where the user's last few tenths came
    from;
  • the ripple gate — a run that quietly relaxes its own constraint reports a
    design the user cannot build.

The screening math is tested on a QUADRATIC whose gradient is known in closed
form, so a wrong sign or a factor of two is a failure, not a judgement call.  The
loop is tested end-to-end against that same quadratic standing in for the FEM, so
no test here costs a solve.
"""

import math

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.routes import optimization as O

client = TestClient(app)


# ═══════════════════════════════════════════════════════════════════════════
# 1.  The screening deviations are the USER's numbers
# ═══════════════════════════════════════════════════════════════════════════
class TestScreeningDeviation:
    def test_lengths_move_two_tenths_of_a_millimetre(self):
        assert O._screen_delta("mm", False) == pytest.approx(0.2)

    def test_dimensionless_knobs_move_two_hundredths(self):
        assert O._screen_delta("", False) == pytest.approx(0.02)

    def test_integers_move_by_one(self):
        assert O._screen_delta("", True) == pytest.approx(1.0)

    def test_integers_do_not_shrink_below_their_own_quantum(self):
        # Half a turn is not a design; a shrunken integer step would round to
        # zero and the variable would silently stop being screened.
        assert O._screen_delta("", True, 0.25) == pytest.approx(1.0)

    def test_shrinking_scales_the_continuous_deviations(self):
        assert O._screen_delta("mm", False, 0.5) == pytest.approx(0.1)
        assert O._screen_delta("", False, 0.5) == pytest.approx(0.01)

    def test_the_shrink_floor_never_goes_under_the_manufacturing_grid(self):
        # mm knobs are quantised to 0.1 mm.  A 0.05 mm perturbation rounds
        # straight back onto the design it started from, so it measures nothing
        # — a screening that "found no sensitivity" there would be lying.
        assert O._SCREEN_DELTA_MM * O._SCREEN_MIN_SHRINK >= 0.1 - 1e-12
        assert O._SCREEN_DELTA_DIMLESS * O._SCREEN_MIN_SHRINK >= 0.01 - 1e-12


# ═══════════════════════════════════════════════════════════════════════════
# 2.  The screening math, on a quadratic whose gradient is known
# ═══════════════════════════════════════════════════════════════════════════
def _quadratic_screen(coeffs, x0, deltas, centre=None):
    """Screen c(x) = Σ a_i·(x_i − x*_i)² exactly, by evaluating it.

    Returns what _screen_rows would be given those evaluations.  The true
    gradient is dc/dx_i = 2·a_i·(x_i − x*_i), and a CENTRAL difference is exact
    for a quadratic — so any deviation the test sees is our arithmetic, not
    discretisation."""
    names = list(coeffs)
    star = centre or {n: 0.0 for n in names}

    def c(x):
        return sum(coeffs[n] * (x[n] - star[n]) ** 2 for n in names)

    c0 = c(x0)
    cp, cm = {}, {}
    for n in names:
        xp = dict(x0); xp[n] += deltas[n]
        xm = dict(x0); xm[n] -= deltas[n]
        cp[n] = c(xp)
        cm[n] = c(xm)
    rows = O._screen_rows(names, [deltas[n] for n in names], c0, cp, cm)
    return rows, {n: 2.0 * coeffs[n] * (x0[n] - star[n]) for n in names}


class TestScreeningRecoversTheKnownGradient:
    """Central differences on a quadratic are EXACT.  If these drift, the
    sensitivity table is fiction and the descent is walking blind."""

    def test_slope_equals_the_analytic_derivative(self):
        coeffs = {"a": 1.0, "b": 0.25, "c": 4.0}
        x0 = {"a": 3.0, "b": -2.0, "c": 0.5}
        deltas = {"a": 0.2, "b": 0.2, "c": 0.02}
        rows, truth = _quadratic_screen(coeffs, x0, deltas)
        for r in rows:
            assert r["slope"] == pytest.approx(truth[r["name"]], rel=1e-9, abs=1e-12)

    def test_influence_is_the_gradient_in_units_of_one_deviation(self):
        # The ranking key must be δ-scaled, or a millimetre knob and a
        # dimensionless one cannot be compared and the ranking is meaningless.
        coeffs = {"mm_knob": 1.0, "fraction": 100.0}
        x0 = {"mm_knob": 1.0, "fraction": 0.4}
        deltas = {"mm_knob": 0.2, "fraction": 0.02}
        rows, truth = _quadratic_screen(coeffs, x0, deltas)
        for r in rows:
            assert r["influence"] == pytest.approx(
                abs(truth[r["name"]]) * r["delta"], rel=1e-9)

    def test_direction_points_downhill(self):
        # x = +3 on a bowl centred at 0 must move NEGATIVE.
        rows, _ = _quadratic_screen({"a": 1.0}, {"a": 3.0}, {"a": 0.2})
        assert rows[0]["direction"] == -1.0
        rows, _ = _quadratic_screen({"a": 1.0}, {"a": -3.0}, {"a": 0.2})
        assert rows[0]["direction"] == +1.0

    def test_the_table_is_sorted_by_influence(self):
        coeffs = {"weak": 0.01, "strong": 10.0, "middling": 1.0}
        x0 = {n: 1.0 for n in coeffs}
        deltas = {n: 0.2 for n in coeffs}
        rows, _ = _quadratic_screen(coeffs, x0, deltas)
        assert [r["name"] for r in rows] == ["strong", "middling", "weak"]

    def test_a_flat_variable_is_reported_inert_not_influential(self):
        # At the bottom of its own bowl a variable has zero gradient.  It is not
        # broken and not unmeasured — it is inert, and must rank last.
        coeffs = {"live": 1.0, "at_its_optimum": 1.0}
        x0 = {"live": 2.0, "at_its_optimum": 0.0}
        deltas = {n: 0.2 for n in coeffs}
        rows, _ = _quadratic_screen(coeffs, x0, deltas)
        assert rows[-1]["name"] == "at_its_optimum"
        assert rows[-1]["influence"] == pytest.approx(0.0, abs=1e-12)
        assert rows[-1]["measured"] is True


class TestOneSidedScreening:
    """A variable whose −δ leaves the physical half-line (or whose cross-section
    will not build on one side) still has to be screened — from the side that
    exists.  Dropping it would silently delete a knob from the search."""

    def test_missing_minus_side_falls_back_to_a_forward_difference(self):
        rows = O._screen_rows(["a"], [0.2], 1.0, {"a": 1.4}, {})
        assert rows[0]["one_sided"] is True
        assert rows[0]["slope"] == pytest.approx((1.4 - 1.0) / 0.2)
        assert rows[0]["influence"] == pytest.approx(0.4)
        assert rows[0]["direction"] == -1.0     # +δ made it worse → go the other way

    def test_missing_plus_side_falls_back_to_a_backward_difference(self):
        rows = O._screen_rows(["a"], [0.2], 1.0, {}, {"a": 0.6})
        assert rows[0]["one_sided"] is True
        assert rows[0]["slope"] == pytest.approx((1.0 - 0.6) / 0.2)
        assert rows[0]["direction"] == -1.0     # cost rises with x → move down

    def test_a_variable_with_no_usable_side_is_unmeasured_not_inert(self):
        rows = O._screen_rows(["a"], [0.2], 1.0, {}, {})
        assert rows[0]["measured"] is False
        assert rows[0]["influence"] == 0.0


class TestNoiseFloor:
    """The floor below which an influence is not evidence.  Evals here are
    deterministic, so this is not statistical noise — it is the disagreement
    between the two one-sided slopes (curvature + the jump the objective takes
    when the mesh is rebuilt)."""

    def test_a_pure_linear_response_has_no_jitter(self):
        # c = 3x: both one-sided slopes agree exactly, so the floor is zero and
        # every non-zero influence counts.
        rows = O._screen_rows(["a", "b"], [0.2, 0.2], 0.0,
                              {"a": 0.6, "b": 0.2}, {"a": -0.6, "b": -0.2})
        assert O._screen_noise_floor(rows) == pytest.approx(0.0)

    def test_jitter_is_the_second_difference(self):
        # c₊ + c₋ − 2c₀ = 0.4 → jitter 0.2
        rows = O._screen_rows(["a"], [0.2], 1.0, {"a": 1.5}, {"a": 0.9})
        assert rows[0]["jitter"] == pytest.approx(0.2)
        assert O._screen_noise_floor(rows) == pytest.approx(0.2)

    def test_the_floor_is_the_median_not_the_worst_case(self):
        # One pathological variable must not raise the floor for all the others.
        rows = O._screen_rows(
            ["a", "b", "c"], [0.2, 0.2, 0.2], 0.0,
            {"a": 0.4, "b": 0.4, "c": 100.0}, {"a": 0.0, "b": 0.0, "c": 100.0})
        # jitters: a=0.2, b=0.2, c=100 → median 0.2
        assert O._screen_noise_floor(rows) == pytest.approx(0.2)


# ═══════════════════════════════════════════════════════════════════════════
# 3.  Choosing the influential set — by the GAP, not by a constant
# ═══════════════════════════════════════════════════════════════════════════
def _rows_from_influences(inf):
    rows = [{"name": n, "influence": float(v), "slope": -float(v) / 0.2,
             "direction": 1.0, "delta": 0.2, "jitter": 0.0,
             "one_sided": False, "measured": True} for n, v in inf.items()]
    rows.sort(key=lambda r: -r["influence"])
    return rows


class TestInfluentialSetSelection:
    def test_it_cuts_where_the_influence_falls_off_a_cliff(self):
        rows = _rows_from_influences({
            "big1": 1.0, "big2": 0.9, "big3": 0.8,
            "small1": 0.02, "small2": 0.015})
        pick = O._screen_pick_k(rows, noise=0.0)
        assert pick["names"] == ["big1", "big2", "big3"]
        assert pick["k"] == 3
        assert pick["gap"] > 1.0

    def test_variables_under_the_noise_floor_are_never_selected(self):
        rows = _rows_from_influences({"real": 1.0, "noise1": 0.001, "noise2": 0.0005})
        pick = O._screen_pick_k(rows, noise=0.01)
        assert pick["names"] == ["real"]
        assert pick["n_above_noise"] == 1

    def test_it_never_moves_more_than_the_cap(self):
        # "Screening then descending on the influential few" is the whole method;
        # a k that grows with N is just gradient descent on everything again.
        rows = _rows_from_influences({"v{}".format(i): 1.0 - i * 1e-6
                                      for i in range(30)})
        pick = O._screen_pick_k(rows, noise=0.0)
        assert pick["k"] <= O._SCREEN_TOPK_MAX

    def test_nothing_above_the_floor_selects_nothing_and_says_so(self):
        rows = _rows_from_influences({"a": 0.001, "b": 0.0005})
        pick = O._screen_pick_k(rows, noise=1.0)
        assert pick["names"] == []
        assert "noise floor" in pick["why"]

    def test_a_single_dominant_variable_is_descended_alone(self):
        # The user does this too: when one knob is worth 200x the next, moving
        # anything else with it only adds noise to the direction.
        rows = _rows_from_influences({"dominant": 1.0, "rest1": 0.005,
                                      "rest2": 0.004, "rest3": 0.003})
        pick = O._screen_pick_k(rows, noise=0.0)
        assert pick["names"] == ["dominant"]

    def test_a_smooth_ramp_has_no_honest_cut_and_takes_them_all(self):
        # No cliff anywhere -> inventing one would be a fiction.  Everything
        # above the noise floor is influential, up to the cap.
        rows = _rows_from_influences({"a": 1.0, "b": 0.95, "c": 0.9, "d": 0.85})
        pick = O._screen_pick_k(rows, noise=0.0)
        assert pick["k"] == 4
        assert "comparably influential" in pick["why"]

    def test_the_choice_carries_its_own_justification(self):
        rows = _rows_from_influences({"a": 1.0, "b": 0.9, "c": 0.01})
        pick = O._screen_pick_k(rows, noise=0.0)
        assert pick["why"]                       # reportable, not implicit
        assert str(pick["k"]) in pick["why"]


# ═══════════════════════════════════════════════════════════════════════════
# 4.  The descent direction
# ═══════════════════════════════════════════════════════════════════════════
class TestDescentStep:
    def test_the_most_influential_variable_moves_exactly_one_deviation(self):
        rows = _rows_from_influences({"strong": 1.0, "weak": 0.25})
        for r in rows:
            r["direction"] = -1.0
        step = O._screen_step(rows, ["strong", "weak"])
        assert step["strong"] == pytest.approx(-0.2)

    def test_the_others_move_in_proportion_to_their_influence(self):
        # This IS steepest descent in δ-scaled coordinates: du_i ∝ −dc/du_i.
        rows = _rows_from_influences({"strong": 1.0, "weak": 0.25})
        for r in rows:
            r["direction"] = -1.0
        step = O._screen_step(rows, ["strong", "weak"])
        assert step["weak"] == pytest.approx(-0.2 * 0.25)

    def test_signs_are_taken_from_the_screening_not_assumed(self):
        rows = _rows_from_influences({"up": 1.0, "down": 1.0})
        for r in rows:
            r["direction"] = +1.0 if r["name"] == "up" else -1.0
        step = O._screen_step(rows, ["up", "down"])
        assert step["up"] > 0 and step["down"] < 0

    def test_only_the_active_set_moves(self):
        rows = _rows_from_influences({"a": 1.0, "b": 1.0, "c": 1.0})
        step = O._screen_step(rows, ["a"])
        assert set(step) == {"a"}

    def test_a_step_on_a_quadratic_points_at_the_optimum(self):
        # End-to-end sanity for the two pure functions together: screen a bowl,
        # step along the result, and the cost must go DOWN.
        coeffs = {"a": 1.0, "b": 0.3}
        x0 = {"a": 2.0, "b": -1.5}
        deltas = {"a": 0.2, "b": 0.2}
        rows, _ = _quadratic_screen(coeffs, x0, deltas)
        step = O._screen_step(rows, ["a", "b"])
        c = lambda x: sum(coeffs[n] * x[n] ** 2 for n in coeffs)
        moved = {n: x0[n] + step[n] for n in x0}
        assert c(moved) < c(x0)


# ═══════════════════════════════════════════════════════════════════════════
# 5.  Group cycling — the user works ≤4 variables at a time
# ═══════════════════════════════════════════════════════════════════════════
class TestGroupCycling:
    def test_groups_never_exceed_the_users_working_set(self):
        names = ["v{}".format(i) for i in range(11)]
        groups = O._screen_groups(names, 4)
        assert all(len(g) <= 4 for g in groups)

    def test_every_variable_is_polished_exactly_once_per_round(self):
        names = ["v{}".format(i) for i in range(11)]
        flat = [n for g in O._screen_groups(names, 4) for n in g]
        assert flat == names                       # order preserved, none dropped

    def test_the_default_group_size_is_four(self):
        assert O._SCREEN_GROUP == 4

    def test_an_empty_set_produces_no_groups(self):
        assert O._screen_groups([], 4) == []


# ═══════════════════════════════════════════════════════════════════════════
# 6.  Plan assembly for the new mode
# ═══════════════════════════════════════════════════════════════════════════
def _plan(mode="screen", ripple=5.0, budget=0):
    r = client.post("/api/optimization/auto/plan",
                    json={"max_ripple_pct": ripple, "budget_evals": budget,
                          "mode": mode})
    assert r.status_code == 200, r.text
    return r.json()["plan"]


class TestPlanAssembly:
    def test_the_route_defaults_to_the_existing_search(self):
        # Adding a mode must not change what an old caller gets.
        r = client.post("/api/optimization/auto/plan", json={"max_ripple_pct": 5.0})
        assert r.json()["plan"]["mode"] == "cmaes"

    def test_screen_mode_publishes_the_deviation_of_every_variable(self):
        plan = _plan()
        assert plan["mode"] == "screen"
        for v in plan["variables"]:
            expected = 1.0 if v["is_int"] else (0.2 if v["unit"] == "mm" else 0.02)
            assert v["delta"] == pytest.approx(expected)

    def test_the_quoted_screen_costs_two_evals_per_variable(self):
        plan = _plan()
        assert plan["screen"]["n_screen_evals"] == 2 * len(plan["variables"])

    def test_the_objective_is_still_the_standing_one(self):
        # Standing rule: every optimization is judged by the perpendicular
        # distance above the current-only baseline line.  A new mode does not
        # get to bring its own objective.
        assert _plan()["objective"] == "baseline_line"

    def test_the_operating_point_still_comes_from_the_simulation_tab(self):
        from motor_ai_sim.config import get_config
        sim = get_config().get("simulation", {})
        op = _plan()["operating_point"]
        assert op["current_a"] == pytest.approx(float(sim["max_current"]))
        assert op["gamma_deg"] == pytest.approx(float(sim["phase_offset_deg"]))

    def test_both_modes_are_quoted_over_the_same_default_budget(self):
        # Otherwise "screen beat cmaes" would just mean "screen was given more".
        a, b = _plan("screen")["budget_evals"], _plan("cmaes")["budget_evals"]
        assert abs(a - b) <= _plan("cmaes")["population"]

    def test_the_eval_path_is_the_same_honest_one(self):
        ev = _plan()["eval"]
        assert ev["element_order"] == 2 and ev["geo_mesh"] and ev["iron_template"]
        assert ev["steps_per_period"] >= 48      # ripple must not alias

    def test_an_unknown_mode_is_refused_loudly(self):
        r = client.post("/api/optimization/auto/plan",
                        json={"max_ripple_pct": 5.0, "mode": "gradient-descent"})
        assert r.status_code == 422
        assert "mode must be one of" in r.json()["detail"]

    def test_the_point_name_says_which_search_produced_it(self):
        r = client.post("/api/optimization/auto/plan",
                        json={"max_ripple_pct": 5.0, "mode": "screen"})
        assert "screen" in r.json()["point_name"]


# ═══════════════════════════════════════════════════════════════════════════
# 7.  The whole loop, against a quadratic standing in for the FEM
# ═══════════════════════════════════════════════════════════════════════════
class _Bowl:
    """A synthetic motor whose objective is a quadratic with a KNOWN optimum.

    Only ``movers`` actually affect the machine; every other variable is inert,
    which is the situation the screening phase exists to discover.  Metrics are
    shaped so that _make_bline / _descent_cost produce
        F  ∝  −Σ a_i·(x_i − x*_i)²
    i.e. F is maximised exactly at x*.  Ripple can be made to depend on a
    variable so the gate has something to bite on."""

    TD0, EFF0 = 10.0, 0.96

    def __init__(self, movers, ripple=lambda ov: 3.0):
        self.movers = movers            # name -> (curvature, offset from x0)
        self.ripple = ripple
        self.calls = 0
        self.x0 = None

    def __call__(self, overrides, current_a, *a, **kw):
        self.calls += 1
        if self.x0 is None:
            self.x0 = dict(overrides)
        q = 0.0
        for nm, (curv, off) in self.movers.items():
            star = self.x0[nm] + off
            q -= curv * (float(overrides[nm]) - star) ** 2
        bumped = current_a > 1.05 * self._I()
        return {"ok": True, "res": {
            "T_em_Nm": 30.0 + q,
            # The bumped reference point buys torque density with efficiency —
            # that tilt is what defines the baseline line.
            "torque_per_mass_Nm_kg": self.TD0 + q + (0.5 if bumped else 0.0),
            "efficiency": self.EFF0 - (0.01 if bumped else 0.0),
            "T_ripple_pct": float(self.ripple(overrides)),
            "mass_total_kg": 3.0, "P_loss_total_W": 480.0, "V_peak": 90.0,
        }}

    _I_val = [None]

    def _I(self):
        return self._I_val[0]


@pytest.fixture
def synthetic_fem(monkeypatch):
    """Replace every path that touches the disk, the FEM or the geometry kernel,
    so the loop can be exercised for free."""
    monkeypatch.setattr(O, "_auto_prefence", lambda ov: None)
    monkeypatch.setattr(O, "_save_descent_state", lambda: None)
    monkeypatch.setattr(O, "_save_eval_rate", lambda: None)
    monkeypatch.setattr(O, "_store_eval", lambda k, v: None)
    monkeypatch.setattr(O, "_EVAL_CACHE", {})
    monkeypatch.setattr(O, "_auto_compare_point",
                        lambda bucket, name, plan, result: {"id": "x", "name": name})
    O._descent_state["cancel"] = False
    yield monkeypatch
    O._RIPPLE_PEN_LAM["v"] = 0.0
    O._RIPPLE_PEN_LAM["v0"] = 0.0


def _run_screen(monkeypatch, bowl, budget=200, ripple_max=5.0):
    plan = O._auto_assemble(ripple_max, budget, "screen")
    bowl._I_val[0] = plan["operating_point"]["current_a"]
    monkeypatch.setattr(O, "_subprocess_eval", bowl)
    O._screen_worker(plan, "test", "local", "test_point")
    assert not O._descent_state.get("error"), O._descent_state.get("error")
    return O._descent_state["result"], plan


class TestTheLoopFindsAKnownOptimum:
    def test_it_walks_downhill_to_the_planted_optimum(self, synthetic_fem):
        # tooth_width is 0.6 mm away from its optimum and is the only variable
        # that matters.  A working screening descent must find it.
        bowl = _Bowl({"tooth_width": (1.0, 0.6)})
        res, plan = _run_screen(synthetic_fem, bowl)
        x0 = {v["name"]: v["x0"] for v in plan["variables"]}
        got = res["best"]["x"]["tooth_width"]
        assert got == pytest.approx(x0["tooth_width"] + 0.6, abs=0.1)
        assert res["best"]["F"] > 0

    def test_it_improves_on_the_design_it_started_from(self, synthetic_fem):
        bowl = _Bowl({"tooth_width": (1.0, 0.6), "slot_height": (0.5, -0.4)})
        res, _ = _run_screen(synthetic_fem, bowl)
        assert res["best"]["F"] > 0
        assert res["best"]["cost"] < 0

    def test_the_screening_table_names_the_variables_that_matter(self,
                                                                 synthetic_fem):
        bowl = _Bowl({"tooth_width": (1.0, 0.6), "core_thickness": (0.8, -0.5)})
        res, _ = _run_screen(synthetic_fem, bowl)
        rows = {r["name"]: r for r in res["sensitivity"]["rows"]}
        # Inert variables must be measurably inert, not merely low-ranked.
        assert rows["wire_width"]["influence"] == pytest.approx(0.0, abs=1e-9)
        assert rows["magnet_height"]["influence"] == pytest.approx(0.0, abs=1e-9)

    def test_a_variable_that_does_nothing_is_never_moved(self, synthetic_fem):
        bowl = _Bowl({"tooth_width": (1.0, 0.6)})
        res, plan = _run_screen(synthetic_fem, bowl)
        x0 = {v["name"]: v["x0"] for v in plan["variables"]}
        for nm, v in res["best"]["x"].items():
            if nm != "tooth_width":
                assert v == pytest.approx(x0[nm]), nm

    def test_the_trajectory_is_reported_step_by_step(self, synthetic_fem):
        bowl = _Bowl({"tooth_width": (1.0, 0.6)})
        res, _ = _run_screen(synthetic_fem, bowl)
        traj = res["trajectory"]
        assert traj[0]["phase"] == "baseline"
        assert any(t["phase"] == "screening" for t in traj)
        assert any(t["phase"] in ("descent", "polish") for t in traj)
        # F must never go backwards along the accepted trajectory.
        costs = [t["cost"] for t in traj]
        assert costs == sorted(costs, reverse=True) or all(
            costs[i + 1] <= costs[i] + 1e-9 for i in range(len(costs) - 1))

    def test_it_stops_when_it_converges_rather_than_burning_the_budget(
            self, synthetic_fem):
        bowl = _Bowl({"tooth_width": (1.0, 0.6)})
        res, plan = _run_screen(synthetic_fem, bowl, budget=2000)
        assert res["stop_reason"] == "converged"
        assert res["n_evals"] < plan["budget_evals"]

    def test_the_budget_is_a_hard_cap(self, synthetic_fem):
        # A bowl far from its optimum in many variables would descend forever.
        bowl = _Bowl({n: (1.0, 5.0) for n in
                      ("tooth_width", "slot_height", "core_thickness",
                       "cut_width", "magnet_height")})
        res, plan = _run_screen(synthetic_fem, bowl, budget=90)
        assert res["n_evals"] <= plan["budget_evals"]


class TestPolishPhaseDoesRealWork:
    #: A machine built so the polish phase is the ONLY route to four of its
    #: optima.  One variable dominates the screening; four more sit far enough
    #: from their optima to be worth real torque density, but their influence is
    #: UNDER the noise floor, so the influential-set selection will never pick
    #: them.  The floor is set by nine stiff variables already sitting at their
    #: own optima — high curvature, zero slope: exactly the "inert but touchy"
    #: knobs a real cross-section is full of.
    WEAK = {"cut_width": 0.4, "core_thickness": -0.4,
            "slot_height": 0.4, "tooth2_width": -0.4}
    STIFF = ("stator_fillet_r", "stator_fillet_r1", "wire_width", "wire_height",
             "magnet_height", "magnet_fill_radius", "magnet_up_gap",
             "magnet_down_height", "rotor_fill_r")

    def _bowl(self):
        movers = {"tooth_width": (1.0, 1.2)}
        movers.update({n: (1.0, off) for n, off in self.WEAK.items()})
        movers.update({n: (10.0, 0.0) for n in self.STIFF})
        return _Bowl(movers)

    def test_the_weak_variables_are_below_the_floor_and_so_never_selected(
            self, synthetic_fem):
        # If this stops holding, the test below is no longer testing polish.
        res, _ = _run_screen(synthetic_fem, self._bowl(), budget=600)
        rows = {r["name"]: r for r in res["sensitivity"]["rows"]}
        floor = res["sensitivity"]["noise_floor"]
        assert floor > 0
        assert all(rows[n]["influence"] <= floor for n in self.WEAK)

    def test_the_polish_phase_moves_the_leftovers(self, synthetic_fem):
        res, _ = _run_screen(synthetic_fem, self._bowl(), budget=600)
        assert any(t["phase"] == "polish" for t in res["trajectory"]), \
            "no variable was ever moved by the polish phase"

    def test_the_leftovers_reach_their_optima(self, synthetic_fem):
        res, plan = _run_screen(synthetic_fem, self._bowl(), budget=600)
        x0 = {v["name"]: v["x0"] for v in plan["variables"]}
        best = res["best"]["x"]
        assert best["tooth_width"] == pytest.approx(x0["tooth_width"] + 1.2, abs=0.15)
        for nm, off in self.WEAK.items():
            assert best[nm] == pytest.approx(x0[nm] + off, abs=0.15), nm

    def test_polish_groups_never_exceed_four_variables(self, synthetic_fem):
        res, _ = _run_screen(synthetic_fem, self._bowl(), budget=600)
        n_polish = 0
        for t in res["trajectory"]:
            if t["phase"] == "polish":
                n_polish += 1
                grp = t["note"].split("|")[0].replace("polish", "").strip()
                assert len(grp.split("+")) <= O._SCREEN_GROUP, t["note"]
        assert n_polish > 0


class TestTheRippleGateIsHonoured:
    def test_a_design_over_the_gate_is_penalised_not_accepted_free(
            self, synthetic_fem):
        # Moving tooth_width toward its torque optimum ALSO drives ripple to
        # 20 %.  With the gate at 5 % the search must not take the free torque.
        def ripple(ov):
            return 3.0 + 40.0 * abs(float(ov["tooth_width"]) - bowl.x0["tooth_width"])
        bowl = _Bowl({"tooth_width": (1.0, 0.6)}, ripple=ripple)
        res, _ = _run_screen(synthetic_fem, bowl, budget=300, ripple_max=5.0)
        assert res["best"]["metrics"]["T_ripple_pct"] <= 5.0 + O._RIPPLE_OVER_TOL

    def test_the_penalty_machinery_is_the_shared_one(self, synthetic_fem):
        # Same globals the CMA route escalates, so a change to the constraint
        # handling cannot apply to one mode and not the other.
        bowl = _Bowl({"tooth_width": (1.0, 0.6)})
        _run_screen(synthetic_fem, bowl)
        assert O._RIPPLE_PEN_LAM["v0"] == O._AUTO_RIPPLE_LAMBDA


class TestGeometryFenceCostsNoFEM:
    def test_an_unbuildable_perturbation_is_screened_out_in_process(
            self, synthetic_fem):
        # The pre-fence exists so a candidate the geometry validator rejects
        # never reaches a 10-minute subprocess.  Reject everything that moves
        # tooth_width up, and no eval may be spent on those points.
        bowl = _Bowl({"tooth_width": (1.0, -0.6)})
        plan = O._auto_assemble(5.0, 120, "screen")
        bowl._I_val[0] = plan["operating_point"]["current_a"]
        x0_tw = {v["name"]: v["x0"] for v in plan["variables"]}["tooth_width"]
        seen = []

        def fence(ov):
            if float(ov["tooth_width"]) > x0_tw + 1e-9:
                return "geometry violation: synthetic fence"
            return None

        def stub(overrides, current_a, *a, **kw):
            seen.append(dict(overrides))
            return bowl(overrides, current_a, *a, **kw)

        synthetic_fem.setattr(O, "_auto_prefence", fence)
        synthetic_fem.setattr(O, "_subprocess_eval", stub)
        O._screen_worker(plan, "test", "local", "test_point")
        assert not O._descent_state.get("error")
        assert all(float(s["tooth_width"]) <= x0_tw + 1e-9 for s in seen)
        assert O._descent_state["result"]["rejects"]["prefenced_geometry"] > 0
        # …and the variable is still screened, from the side that exists.
        rows = {r["name"]: r
                for r in O._descent_state["result"]["sensitivity"]["rows"]}
        assert rows["tooth_width"]["measured"] is True


class TestTheReportedBestIsTheRealBest:
    """A screening pass evaluates 2N fully-paid-for designs and then throws the
    point away to build a table out of it.  If one of those perturbations is the
    best thing the run ever saw, reporting anything else means the user paid for
    a design and was not shown it."""

    def test_no_evaluated_design_beats_the_one_reported(self, synthetic_fem):
        seen = []
        bowl = _Bowl({"tooth_width": (1.0, 0.6), "core_thickness": (0.7, -0.4),
                      "magnet_up_gap": (0.3, 0.4)})

        def recording(overrides, current_a, *a, **kw):
            out = bowl(overrides, current_a, *a, **kw)
            if abs(current_a - bowl._I_val[0]) < 1e-6:      # the bumped point is
                seen.append((dict(overrides), out["res"]))  # not a candidate
            return out

        plan = O._auto_assemble(5.0, 400, "screen")
        bowl._I_val[0] = plan["operating_point"]["current_a"]
        synthetic_fem.setattr(O, "_subprocess_eval", recording)
        O._screen_worker(plan, "test", "local", "test_point")
        res = O._descent_state["result"]
        assert not O._descent_state.get("error")
        base = dict(res["baseline"])
        base["torque_per_mass_Nm_kg"] = base.pop("torque_per_mass")
        base["_bline"] = res["baseline_line"]
        best_seen = min(
            O._descent_cost(m, base, 5.0, 1.0, 1.0, 1.0, 1e9)[0] for _ov, m in seen)
        assert res["best"]["cost"] <= best_seen + 1e-9


class TestCacheAccounting:
    def test_a_cache_hit_is_not_charged_to_the_eval_budget(self, synthetic_fem):
        bowl = _Bowl({"tooth_width": (1.0, 0.6)})
        res, _ = _run_screen(synthetic_fem, bowl)
        # Every FEM eval counted must correspond to a real call into the stub.
        assert res["n_evals"] == bowl.calls
        assert res["rejects"]["fem_evals"] == bowl.calls

    def test_the_same_design_is_never_solved_twice(self, synthetic_fem):
        """A screening descent re-screens around a moving point, so it lands on
        cross-sections it has already solved constantly.  At ~10 min per eval
        that repetition is the difference between a 40-eval run and a 200-eval
        one, so the memo is not an optimisation — it is the budget."""
        seen = []
        bowl = _Bowl({"tooth_width": (1.0, 0.6), "slot_height": (0.4, -0.4)})

        def recording(overrides, current_a, *a, **kw):
            seen.append((tuple(sorted((k, round(float(v), 6))
                                      for k, v in overrides.items())),
                         round(float(current_a), 4)))
            return bowl(overrides, current_a, *a, **kw)

        plan = O._auto_assemble(5.0, 300, "screen")
        bowl._I_val[0] = plan["operating_point"]["current_a"]
        synthetic_fem.setattr(O, "_subprocess_eval", recording)
        O._screen_worker(plan, "test", "local", "test_point")
        assert not O._descent_state.get("error")
        assert len(seen) == len(set(seen)), "the same design was solved twice"


def test_screening_never_writes_the_users_config(synthetic_fem):
    """The optimizer is read-only w.r.t. the active machine — the run must not
    apply its own result behind the user's back."""
    from motor_ai_sim.config import get_config
    before = dict(get_config().get("geometry", {}))
    bowl = _Bowl({"tooth_width": (1.0, 0.6)})
    _run_screen(synthetic_fem, bowl)
    assert dict(get_config().get("geometry", {})) == before
