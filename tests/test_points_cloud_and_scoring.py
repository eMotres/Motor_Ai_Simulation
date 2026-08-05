"""Two defects found on the live 18-variable auto run (2026-08-05), and the
invariants that stop them coming back.

DEFECT 1 — the objective cloud was a partial subset.  Every optimizer worker
published its evals into ``_descent_state["points"]`` by hand, at nine separate
call sites, and one design was missing from ALL of them: point B of the baseline
line (the start geometry re-solved at I·1.1).  It is a fully paid-for FEM eval
with metrics, it defines the very line every candidate is measured against, and
it was the one design each run measured and never showed.  On top of that the
gradient path truncated to the last 1200 points — silently evicting the baseline
anchor — and the auto card's scatter plotted ``st.history`` (one row per
GENERATION, i.e. the incumbent) instead of ``st.points``, so a 62-eval run drew
six dots and read as "found nothing".

  Standing rule (Vadim): «надо выводить все точки я потом могу отфильтровать их
  по пульсации там же есть ползунок» — publish every scored eval; the chart's
  ripple slider does the filtering, visually.

DEFECT 2 — "best F = +0.00000 after 69 evals" while the eval log held a design
that beat the baseline on every axis.  The scoring path turned out to be
correct: the cited design is not from that run at all (it was solved at 78.49 A /
γ 10°, the run's operating point is 91.92 A / γ 16°), and scored against the
run's own baseline line it is POSITIVE — F = +0.00159 — so it would have been
accepted had the run ever evaluated it.  What the arithmetic does show is that
the perpendicular metric's normal is 99.9998 % along the EFFICIENCY axis: the
exchange rate is 1 percentage point of efficiency per 4.97 Nm/kg of torque
density, so +10.8 % torque density earns +0.0021 and a 0.062 pp efficiency loss
gives back 0.0006.  That is a property of the metric, not a bug, and the numbers
are pinned here so a future change to the metric is a deliberate act.

The live numbers below are taken verbatim from config/.last_descent.json and
config/.opt_dataset.jsonl of that run.
"""

import pytest

from motor_ai_sim.routes import optimization as O


# ═══════════════════════════════════════════════════════════════════════════
# The live run, as measured.  Baseline line A–B of run auto_ripple12_20260805.
# ═══════════════════════════════════════════════════════════════════════════
LIVE_BLINE = {
    "td_a": 10.1763, "eff_a": 0.96302,          # A: 91.92 A, T = 31.051 N·m, ripple 12.30 %
    "td_b": 11.1407, "eff_b": 0.96108,          # B: 101.12 A (+10 %)
    "w_td": 0.96302 - 0.96108,
    "w_eff": 11.1407 - 10.1763,
    "bump_pct": 10.0, "current_a": 91.92388155425117, "current_b": 101.11626970967629,
}
LIVE_BLINE["norm"] = (LIVE_BLINE["w_td"] ** 2 + LIVE_BLINE["w_eff"] ** 2) ** 0.5

# The candidate the user pointed at: T = 34.910 N·m, ripple 2.48 %, η 96.24 %.
CITED = {"torque_per_mass_Nm_kg": 11.275, "efficiency": 0.9624,
         "T_ripple_pct": 2.48, "T_em_Nm": 34.910, "V_peak": 90.0}
BASELINE_METRICS = {"torque_per_mass_Nm_kg": 10.1763, "efficiency": 0.96302,
                    "T_ripple_pct": 12.30, "T_em_Nm": 31.051, "V_peak": 71.0,
                    "_bline": LIVE_BLINE}
LIVE_RIPPLE_GATE = 12.3     # the run's own gate — the user asked for ≤ 12.3 %


def _ok(res):
    return {"ok": True, "res": dict(res), "overrides": {"tooth_width": 9.2}}


@pytest.fixture(autouse=True)
def _no_penalty():
    """_descent_cost reads the ripple λ from a module global; pin it per test."""
    before = dict(O._RIPPLE_PEN_LAM)
    yield
    O._RIPPLE_PEN_LAM.update(before)


# ═══════════════════════════════════════════════════════════════════════════
# 1.  _pub_pt — the ONE door into the cloud
# ═══════════════════════════════════════════════════════════════════════════
class TestEveryScoredEvalIsPublished:
    def test_an_eval_with_metrics_always_reaches_the_cloud(self):
        pts = []
        assert O._pub_pt(pts, _ok(CITED), "cmaes") is not None
        assert len(pts) == 1
        assert pts[0]["td"] == pytest.approx(11.275)
        assert pts[0]["ripple"] == pytest.approx(2.48)

    def test_it_does_not_filter_by_ripple(self):
        """The gate is a CHART control, not a publication rule.  A 68 % ripple
        design and a 2.5 % one are both measured, so both are plotted."""
        pts = []
        for rip in (2.48, 12.30, 68.35):
            O._pub_pt(pts, _ok({**CITED, "T_ripple_pct": rip}), "cmaes")
        assert [p["ripple"] for p in pts] == [2.48, 12.30, 68.35]

    def test_it_does_not_filter_by_the_objective(self):
        """Worse-than-baseline points are the majority of any honest cloud."""
        pts = []
        O._pub_pt(pts, _ok({**CITED, "efficiency": 0.5, "torque_per_mass_Nm_kg": 1.7}),
                  "cmaes")
        assert len(pts) == 1

    def test_the_shaft_torque_travels_with_the_point(self):
        """Without it the auto card's torque×ripple cloud has nothing to plot
        but the per-generation incumbent — which is how a 62-eval run came to
        show six dots."""
        pts = []
        O._pub_pt(pts, _ok(CITED), "cmaes")
        assert pts[0]["torque"] == pytest.approx(34.910)

    def test_a_failed_eval_carries_no_metrics_and_no_point(self):
        pts = []
        assert O._pub_pt(pts, {"ok": False, "error": "mesh failed"}, "cmaes") is None
        assert pts == []


class TestTheCloudIsBoundedButKeepsItsAnchors:
    def test_the_bound_is_the_documented_cap(self):
        assert O._POINTS_CAP == 4000

    def test_it_stops_growing_at_the_cap(self):
        pts = []
        for _ in range(25):
            O._pub_pt(pts, _ok(CITED), "cmaes", cap=10)
        assert len(pts) == 10

    def test_the_baseline_anchors_are_never_evicted(self):
        """A and B define the line every point is measured against; dropping
        them would leave the chart with no reference."""
        pts = []
        O._pub_pt(pts, _ok(BASELINE_METRICS), "baseline", cap=5)
        O._pub_pt(pts, _ok({**BASELINE_METRICS, "torque_per_mass_Nm_kg": 11.1407}),
                  "baseline_bump", cap=5)
        for _ in range(50):
            O._pub_pt(pts, _ok(CITED), "cmaes", cap=5)
        kinds = [p["kind"] for p in pts]
        assert kinds.count("baseline") == 1
        assert kinds.count("baseline_bump") == 1
        assert len(pts) == 5

    def test_the_oldest_ordinary_point_goes_first(self):
        pts = []
        for i in range(4):
            O._pub_pt(pts, _ok({**CITED, "T_ripple_pct": float(i)}), "cmaes", cap=3)
        assert [p["ripple"] for p in pts] == [1.0, 2.0, 3.0]


# ═══════════════════════════════════════════════════════════════════════════
# 2.  End-to-end: a whole run publishes every eval it paid for
# ═══════════════════════════════════════════════════════════════════════════
class _CountingBowl:
    """The synthetic FEM from test_screening_descent, plus a tally of how many
    evals actually returned metrics — the number the cloud must match."""

    TD0, EFF0 = 10.0, 0.96

    def __init__(self, movers, ripple=lambda ov: 3.0):
        self.movers = movers
        self.ripple = ripple
        self.n_ok = 0
        self.x0 = None
        self.I = None

    def __call__(self, overrides, current_a, *a, **kw):
        if self.x0 is None:
            self.x0 = dict(overrides)
        q = 0.0
        for nm, (curv, off) in self.movers.items():
            star = self.x0[nm] + off
            q -= curv * (float(overrides[nm]) - star) ** 2
        bumped = current_a > 1.05 * self.I
        self.n_ok += 1
        return {"ok": True, "res": {
            "T_em_Nm": 30.0 + q,
            "torque_per_mass_Nm_kg": self.TD0 + q + (0.5 if bumped else 0.0),
            "efficiency": self.EFF0 - (0.01 if bumped else 0.0),
            "T_ripple_pct": float(self.ripple(overrides)),
            "mass_total_kg": 3.0, "P_loss_total_W": 480.0, "V_peak": 90.0,
        }}


@pytest.fixture
def offline(monkeypatch):
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


class TestAWholeRunHidesNothing:
    def test_the_screening_run_publishes_every_eval_it_paid_for(self, offline):
        bowl = _CountingBowl({"tooth_width": (1.0, 0.6)})
        plan = O._auto_assemble(5.0, 60, "screen")
        bowl.I = plan["operating_point"]["current_a"]
        offline.setattr(O, "_subprocess_eval", bowl)
        O._screen_worker(plan, "test", "local", "test_point")
        assert not O._descent_state.get("error"), O._descent_state.get("error")
        pts = O._descent_state["points"]
        # Every successful eval, including baseline point B — nothing dropped.
        assert len(pts) == bowl.n_ok
        kinds = [p["kind"] for p in pts]
        assert kinds.count("baseline") == 1
        assert kinds.count("baseline_bump") == 1, (
            "point B of the baseline line is a measured design and must be plotted")

    def test_the_cloud_is_bigger_than_the_incumbent_history(self, offline):
        """The regression that made the auto card read 'found nothing': the
        chart was drawing st.history (one row per accepted step)."""
        bowl = _CountingBowl({"tooth_width": (1.0, 0.6)})
        plan = O._auto_assemble(5.0, 60, "screen")
        bowl.I = plan["operating_point"]["current_a"]
        offline.setattr(O, "_subprocess_eval", bowl)
        O._screen_worker(plan, "test", "local", "test_point")
        assert len(O._descent_state["points"]) > 3 * len(O._descent_state["history"])


# ═══════════════════════════════════════════════════════════════════════════
# 3.  The scoring path, on the real candidate's numbers
# ═══════════════════════════════════════════════════════════════════════════
class TestTheCitedCandidateScoresPositive:
    def test_F_is_positive_so_it_beats_the_baseline_line(self):
        O._RIPPLE_PEN_LAM.update(v=0.0, v0=0.0)
        cost, F = O._descent_cost(CITED, BASELINE_METRICS, LIVE_RIPPLE_GATE,
                                  1.0, 1.0, 1.0, 1e9)
        assert F == pytest.approx(0.00159, abs=2e-5)
        assert F > 0.0, "beats the baseline on torque, ripple and (nearly) efficiency"
        assert cost == pytest.approx(-F)

    def test_the_baseline_scores_exactly_zero(self):
        O._RIPPLE_PEN_LAM.update(v=0.0, v0=0.0)
        _cost, F0 = O._descent_cost(BASELINE_METRICS, BASELINE_METRICS,
                                    LIVE_RIPPLE_GATE, 1.0, 1.0, 1.0, 1e9)
        assert F0 == pytest.approx(0.0, abs=1e-12)

    def test_the_incumbent_test_would_have_accepted_it(self):
        """The run reported best F = +0.00000 not because the candidate was
        rejected but because it was never evaluated at THIS operating point.
        Had it been, this is the comparison the worker makes."""
        O._RIPPLE_PEN_LAM.update(v=O._AUTO_RIPPLE_LAMBDA, v0=O._AUTO_RIPPLE_LAMBDA)
        cost0, _ = O._descent_cost(BASELINE_METRICS, BASELINE_METRICS,
                                   LIVE_RIPPLE_GATE, 1.0, 1.0, 1.0, 1e9)
        cost, _ = O._descent_cost(CITED, BASELINE_METRICS, LIVE_RIPPLE_GATE,
                                  1.0, 1.0, 1.0, 1e9)
        assert cost < cost0 - 1e-9

    def test_a_design_measured_at_another_operating_point_is_another_problem(self):
        """Why the run never saw it: the warm start only seeds from cached evals
        solved at THIS current and THIS γ.  The cited row is 78.49 A / γ 10°,
        the run is 91.92 A / γ 16°."""
        rec = {"cfg_fp": "e5beb902165cc11e", "overrides": {"tooth_width": 9.2},
               "current_a": 78.49, "gamma_deg": 10.0,
               "ripple": 2.48, "td": 11.275, "eff": 0.9624}
        seed = O._auto_warm_start([rec] * 40, [{"name": "tooth_width", "x0": 9.2,
                                                "lo": 1.0}],
                                  "e5beb902165cc11e", 91.92388155425117, 16.0,
                                  LIVE_RIPPLE_GATE, BASELINE_METRICS)
        assert seed is None


class TestWhatThePerpendicularMetricActuallyWeighs:
    """Not a bug — a property.  Pinned so that changing it is deliberate.

    The baseline machine gives up only 0.194 pp of efficiency for +9.5 % torque
    density, so the line A–B is almost horizontal and its normal is almost
    vertical: the objective is, numerically, 'efficiency above the baseline's
    efficiency' with torque density worth 0.2 % as much per unit."""

    def test_the_normal_is_essentially_the_efficiency_axis(self):
        bl = LIVE_BLINE
        assert bl["w_eff"] / bl["norm"] == pytest.approx(1.0, abs=1e-5)
        assert bl["w_td"] / bl["norm"] == pytest.approx(0.00201, abs=1e-5)

    def test_one_point_of_efficiency_costs_five_units_of_torque_density(self):
        bl = LIVE_BLINE
        exchange = 0.01 * bl["w_eff"] / bl["w_td"]      # Nm/kg per 1 pp of η
        assert exchange == pytest.approx(4.97, abs=0.02)

    def test_the_cited_candidates_gain_is_mostly_cancelled_by_0_06_pp_of_eta(self):
        bl = LIVE_BLINE
        gain_td = bl["w_td"] * (CITED["torque_per_mass_Nm_kg"] - bl["td_a"])
        loss_eff = bl["w_eff"] * (bl["eff_a"] - CITED["efficiency"])
        assert gain_td == pytest.approx(0.002131, abs=2e-6)     # +1.099 Nm/kg
        assert loss_eff == pytest.approx(0.000598, abs=2e-6)    # −0.062 pp η
        # 28 % of a +10.8 % torque gain is eaten by six hundredths of a point of
        # efficiency.  Ripple — 12.30 % → 2.48 % — earns nothing at all here,
        # because the run's gate was 12.3 % and the metric only sees a penalty
        # for being OVER it.
        assert loss_eff / gain_td == pytest.approx(0.28, abs=0.02)

    def test_ripple_below_the_gate_earns_no_credit(self):
        O._RIPPLE_PEN_LAM.update(v=O._AUTO_RIPPLE_LAMBDA, v0=O._AUTO_RIPPLE_LAMBDA)
        _c1, f_quiet = O._descent_cost({**CITED, "T_ripple_pct": 0.1},
                                       BASELINE_METRICS, LIVE_RIPPLE_GATE,
                                       1.0, 1.0, 1.0, 1e9)
        _c2, f_loud = O._descent_cost({**CITED, "T_ripple_pct": 12.3},
                                      BASELINE_METRICS, LIVE_RIPPLE_GATE,
                                      1.0, 1.0, 1.0, 1e9)
        assert f_quiet == pytest.approx(f_loud)
