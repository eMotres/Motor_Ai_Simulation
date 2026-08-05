"""Pareto-dominance reporting next to the objective F.

WHAT THIS PINS
──────────────
The perpendicular objective is, on this machine, ~pure efficiency: 1 pp of η is
worth 4.97 Nm/kg (half the machine's whole torque density) and ripple below the
gate earns exactly zero (pinned in tests/test_points_cloud_and_scoring.py).  So
"F ≤ 0" answers "did we beat raising the current", not "did we find anything
better".  The Pareto report answers the second question — and the single most
important thing about it is what it REFUSES to compare.

THE RETRACTION IT GUARDS AGAINST (2026-08-05)
─────────────────────────────────────────────
A "31 better designs" report was read off config/.opt_dataset.jsonl, the
CROSS-RUN accumulator: it holds evals at many currents and gammas, and the
"better" designs were solved at 78.49 A / γ 10° while the run's operating point
was 91.92 A / γ 16°.  Different machine problem, meaningless comparison, report
withdrawn.  TestOperatingPointIsolation below is the regression guard: a
candidate from another operating point can never enter EITHER set.
"""

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.optimization import pareto as P
from motor_ai_sim.routes import optimization as O


# The live run auto_ripple12_20260805 — 110 evals at 91.92 A / γ 16°, gate 12.3 %.
BASE = {"T_em_Nm": 31.051, "efficiency": 0.96302, "torque_per_mass": 10.1763,
        "T_ripple_pct": 12.30, "mass_total_kg": 3.051,
        "current_a": 91.92388155425117}
OP = {"current_a": 91.92388155425117, "gamma_deg": 16.0}


def pt(torque=31.051, ripple=12.30, eff=0.96302, mass=3.051, *, kind="cmaes",
       current=91.92388155425117, gamma=16.0, **kw):
    """One cloud point in the shape `_pt` publishes."""
    p = {"torque": torque, "ripple": ripple, "eff": eff, "mass": mass,
         "td": torque / mass, "kind": kind, "current_a": current,
         "gamma_deg": gamma, "overrides": {"tooth_width": 9.2}}
    p.update(kw)
    return p


AX = P.axes_of_point
BASE_AX = P.axes_of_metrics(BASE)


# ═══════════════════════════════════════════════════════════════════════════
# 1.  The predicate — ties, tolerance, and "better on at least one"
# ═══════════════════════════════════════════════════════════════════════════
class TestTheDominationPredicate:
    def test_better_on_all_three_dominates(self):
        c = AX(pt(torque=32.0, ripple=8.0, eff=0.9700))
        assert P.dominates(c, BASE_AX)

    def test_better_on_one_and_equal_on_the_rest_dominates(self):
        c = AX(pt(ripple=8.0))                     # same T, same η, quieter
        assert P.dominates(c, BASE_AX)

    def test_worse_on_a_single_axis_does_not(self):
        """The point of the metric: no axis may be traded away silently."""
        for worse in ({"torque": 31.0}, {"ripple": 12.4}, {"eff": 0.9629}):
            good = {"torque": 32.0, "ripple": 8.0, "eff": 0.97}
            good.update(worse)
            assert not P.dominates(AX(pt(**good)), BASE_AX), worse

    def test_an_exact_tie_is_not_an_improvement(self):
        """A re-eval of the starting design must not be reported as 'better' —
        the number is answering 'did we find anything BETTER'."""
        assert not P.dominates(AX(pt()), BASE_AX)

    def test_the_baseline_never_dominates_itself(self):
        assert not P.dominates(BASE_AX, BASE_AX)


class TestTheTolerance:
    def test_it_is_half_the_stored_digit(self):
        """The solver is bit-reproducible, so the only slack needed is the
        rounding applied when the metric was stored."""
        assert P.TOL == {"torque": 5e-4, "td": 5e-5, "ripple": 5e-3, "eff": 5e-6}

    def test_a_difference_inside_the_tolerance_is_a_tie_not_a_loss(self):
        c = AX(pt(torque=31.051 - 4e-4, ripple=12.30 + 4e-3, eff=0.96302 - 4e-6))
        assert not P.dominates(c, BASE_AX)          # nothing gained anywhere
        better = AX(pt(torque=31.051 - 4e-4, ripple=8.0, eff=0.96302 - 4e-6))
        assert P.dominates(better, BASE_AX)         # ties on T/η, wins on ripple

    def test_a_difference_outside_the_tolerance_counts(self):
        c = AX(pt(torque=31.051 - 6e-4, ripple=8.0))
        assert not P.dominates(c, BASE_AX)          # measurably less torque

    def test_torque_density_is_the_same_judgement_on_the_other_axis(self):
        """T and T/mass differ only by mass: a design that is heavier can gain
        torque and still lose torque density, and both answers are reported."""
        c = AX(pt(torque=32.0, ripple=8.0, eff=0.97, mass=3.5))
        assert P.dominates(c, BASE_AX, "torque")
        assert not P.dominates(c, BASE_AX, "td")


# ═══════════════════════════════════════════════════════════════════════════
# 2.  The front, on a hand-built set
# ═══════════════════════════════════════════════════════════════════════════
class TestTheFront:
    def test_a_hand_built_set(self):
        rows = [
            AX(pt(torque=32.0, ripple=6.0, eff=0.960)),   # 0 — non-dominated
            AX(pt(torque=31.5, ripple=7.0, eff=0.958)),   # 1 — dominated by 0
            AX(pt(torque=30.0, ripple=2.0, eff=0.955)),   # 2 — quietest
            AX(pt(torque=29.0, ripple=9.0, eff=0.970)),   # 3 — most efficient
            AX(pt(torque=28.0, ripple=9.5, eff=0.965)),   # 4 — dominated by 3
        ]
        assert P.front_mask(rows) == [True, False, True, True, False]

    def test_duplicates_are_both_on_the_front(self):
        """Two identical designs cannot dominate each other (neither is better
        anywhere), so neither may be dropped."""
        rows = [AX(pt(torque=32.0, ripple=6.0)), AX(pt(torque=32.0, ripple=6.0))]
        assert P.front_mask(rows) == [True, True]

    def test_a_point_missing_an_axis_is_off_the_front(self):
        rows = [AX(pt(torque=32.0, ripple=6.0)), AX({"td": None, "mass": None,
                                                     "torque": None, "ripple": 1.0,
                                                     "eff": 0.99})]
        assert P.front_mask(rows) == [True, False]

    def test_the_front_never_shrinks_below_the_single_best_of_each_axis(self):
        rows = [AX(pt(torque=t, ripple=r, eff=e)) for t, r, e in
                ((33.0, 20.0, 0.90), (25.0, 1.0, 0.90), (25.0, 20.0, 0.99),
                 (24.0, 21.0, 0.89))]
        assert P.front_mask(rows) == [True, True, True, False]


# ═══════════════════════════════════════════════════════════════════════════
# 3.  OPERATING-POINT ISOLATION — the regression guard for the retraction
# ═══════════════════════════════════════════════════════════════════════════
class TestOperatingPointIsolation:
    # The design that produced the false "31 better designs": 78.49 A / γ 10°,
    # T = 34.910 N·m, ripple 2.48 %, η 96.24 % — better than the run's baseline
    # on torque and ripple, and it would look like a find on any axis-blind read.
    FOREIGN = pt(torque=34.910, ripple=2.48, eff=0.9624, mass=3.096,
                 current=78.49, gamma=10.0)

    def test_a_candidate_from_another_current_can_never_dominate(self):
        rep = P.report([pt(), self.FOREIGN], BASE, OP)
        foreign = rep["points"][1]
        assert foreign["op_match"] is False
        assert foreign["dominates"] is False
        assert foreign["dominates_td"] is False
        assert foreign["pareto_front"] is False
        assert foreign["pareto_front_td"] is False
        assert rep["summary"]["n_other_op"] == 1
        assert rep["summary"]["n_dominating"] == 0

    def test_a_candidate_from_another_gamma_can_never_dominate(self):
        alien = pt(torque=40.0, ripple=1.0, eff=0.99, gamma=10.0)
        rep = P.report([alien], BASE, OP)
        assert rep["points"][0]["dominates"] is False
        assert rep["summary"]["n_candidates"] == 0

    def test_it_is_not_a_dominator_even_at_the_right_operating_point(self):
        """Worth pinning: the cited design is +3.86 N·m and −9.8 pp of ripple,
        but it is 0.062 pp DOWN on efficiency — so it does not beat the current
        design on all three axes.  It is on the front, not in the dominating set,
        and the two numbers are reported separately for exactly this reason."""
        here = dict(self.FOREIGN, current_a=OP["current_a"], gamma_deg=16.0)
        rep = P.report([here], BASE, OP)
        assert rep["points"][0]["op_match"] is True
        assert rep["points"][0]["dominates"] is False
        assert rep["points"][0]["pareto_front"] is True
        assert rep["summary"]["n_dominating"] == 0
        assert rep["summary"]["n_front"] == 1

    def test_a_genuine_dominator_at_the_right_operating_point_is_reported(self):
        """Not a filter that hides good designs — the same geometry judged at the
        run's own operating point is counted, loudly."""
        here = pt(torque=34.910, ripple=2.48, eff=0.9631, mass=3.096)
        rep = P.report([here], BASE, OP)
        assert rep["points"][0]["dominates"] is True
        assert rep["summary"]["n_dominating"] == 1

    def test_baseline_point_B_is_a_different_operating_point_and_an_anchor(self):
        """B is the start geometry at I·1.1 — it beats A on torque by paying
        efficiency, which is the very trade the objective measures against.  It
        must never be reported as a find."""
        b = pt(torque=34.16, ripple=12.4, eff=0.96108, kind="baseline_bump",
               current=101.11626970967629)
        rep = P.report([b], BASE, OP)
        assert rep["points"][0]["dominates"] is False
        assert rep["summary"]["n_candidates"] == 0
        assert rep["summary"]["n_other_op"] == 0      # anchors are not "elsewhere"

    def test_a_point_with_no_current_is_not_assumed_to_belong(self):
        orphan = pt(torque=40.0, ripple=1.0, eff=0.99)
        orphan["current_a"] = None
        rep = P.report([orphan], BASE, OP)
        assert rep["points"][0]["op_match"] is False
        assert rep["summary"]["n_dominating"] == 0

    def test_a_point_without_gamma_belongs_to_the_run(self):
        """γ is the run's operating point, not a geometry override, so it is not
        stamped on every point (the live 110-eval run's cloud has gamma_deg =
        null on all 87).  Excluding those would report every run as empty."""
        p = pt(torque=32.0, ripple=8.0, eff=0.97)
        p["gamma_deg"] = None
        rep = P.report([p], BASE, OP)
        assert rep["points"][0]["op_match"] is True
        assert rep["summary"]["n_dominating"] == 1

    def test_resolve_op_prefers_the_runs_own_plan(self):
        st = {"auto": {"operating_point": {"current_a": 91.92, "gamma_deg": 16.0}},
              "baseline": {"current_a": 12.0}, "points": []}
        assert P.resolve_op(st) == {"current_a": 91.92, "gamma_deg": 16.0}

    def test_resolve_op_falls_back_to_the_baseline_and_the_mtpa_gamma(self):
        st = {"baseline": {"current_a": 91.92}, "mtpa_gamma_deg": 16.0,
              "points": []}
        assert P.resolve_op(st) == {"current_a": 91.92, "gamma_deg": 16.0}


# ═══════════════════════════════════════════════════════════════════════════
# 4.  The summary, and the API payload it must reach
# ═══════════════════════════════════════════════════════════════════════════
class TestTheSummary:
    def test_counts_are_over_the_runs_own_candidates_only(self):
        pts = [pt(kind="baseline"),                                  # anchor
               pt(torque=32.0, ripple=8.0, eff=0.97),                # dominates
               pt(torque=33.0, ripple=7.0, eff=0.98),                # dominates
               pt(torque=20.0, ripple=30.0, eff=0.90),               # nothing
               pt(torque=99.0, ripple=0.1, eff=0.99, current=78.49)] # other op
        s = P.report(pts, BASE, OP)["summary"]
        assert s["n_points"] == 5
        assert s["n_candidates"] == 3
        assert s["n_other_op"] == 1
        assert s["n_dominating"] == 2
        assert s["n_front"] == 1          # 33/7/0.98 dominates 32/8/0.97
        assert s["operating_point"] == {"current_a": OP["current_a"],
                                        "gamma_deg": 16.0}

    def test_it_states_the_tolerance_it_used(self):
        assert P.report([pt()], BASE, OP)["summary"]["tolerance"] == P.TOL

    def test_a_point_whose_torque_was_never_stored_is_counted_not_hidden(self):
        p = pt(torque=32.0, ripple=8.0, eff=0.97)
        p["torque"] = None
        p["mass"] = None                    # td alone survives
        s = P.report([p], BASE, OP)["summary"]
        assert s["n_no_torque"] == 1
        assert s["n_dominating"] == 0       # cannot judge the torque axis
        assert s["n_dominating_td"] == 1    # the density axis still can


class TestTheApiPublishesIt:
    """The counts must arrive on the two channels the UI reads, or the panel is
    back to showing F alone."""

    @pytest.fixture
    def loaded(self, monkeypatch):
        state = {
            "running": False, "phase": "done", "iter": 10, "n_evals": 110,
            "baseline": dict(BASE),
            "best": {"metrics": dict(BASE), "F": 0.0, "cost": 0.0, "x": {}},
            "history": [], "points": [
                pt(kind="baseline"),
                pt(torque=32.0, ripple=8.0, eff=0.97),
                pt(torque=99.0, ripple=0.1, eff=0.99, current=78.49),
            ],
            "auto": {"objective": "baseline_line", "mode": "cmaes",
                     "max_ripple_pct": 12.3, "generations": 36,
                     "operating_point": {"current_a": OP["current_a"],
                                         "rpm": 4000.0, "gamma_deg": 16.0}},
            "cancel": False, "error": None,
        }
        monkeypatch.setattr(O, "_descent_state", state)
        monkeypatch.setattr(O, "_refresh_descent_state_from_disk", lambda: None)
        return TestClient(app)

    def test_descent_progress_carries_the_flags_and_the_summary(self, loaded):
        body = loaded.get("/api/optimization/descent/progress").json()
        assert body["pareto"]["n_dominating"] == 1
        assert body["pareto"]["n_candidates"] == 1
        assert body["pareto"]["n_other_op"] == 1
        flags = [(p["kind"], p["dominates"], p["pareto_front"]) for p in body["points"]]
        assert flags == [("baseline", False, False), ("cmaes", True, True),
                         ("cmaes", False, False)]

    def test_auto_status_carries_the_summary_and_says_it_in_words(self, loaded):
        body = loaded.get("/api/optimization/auto/status").json()
        assert body["pareto"]["n_dominating"] == 1
        assert body["above_baseline_line"] is False
        assert "all three axes" in body["verdict"]

    def test_the_progress_payload_is_unchanged_when_there_is_no_cloud(self, monkeypatch):
        monkeypatch.setattr(O, "_descent_state",
                            {"running": False, "points": [], "baseline": None})
        monkeypatch.setattr(O, "_refresh_descent_state_from_disk", lambda: None)
        body = TestClient(app).get("/api/optimization/descent/progress").json()
        assert "pareto" not in body


class TestLegacyPointsGetTheirTorqueBack:
    """Measured on the live card: the finished 110-eval run served 87 points and
    the panel printed "0 designs measured" over an empty cloud — every point was
    published before `_pt` carried T_em_Nm, and the chart keys on torque.  The
    eval log has those numbers; the repair takes ONLY the two missing fields, and
    only for a point already in this run's own array, matched exactly."""

    ROW = {"overrides": {"tooth_width": 9.2}, "current_a": 91.92388155425117,
           "gamma_deg": 16.0, "torque": 31.051, "mass": 3.051, "eff": 0.96302,
           "td": 10.1763, "ripple": 12.3}

    @pytest.fixture
    def log(self, tmp_path, monkeypatch):
        import json
        p = tmp_path / ".opt_dataset.jsonl"
        p.write_text(json.dumps(self.ROW) + "\n", encoding="utf-8")
        monkeypatch.setattr(O, "_dataset_path", lambda: str(p))
        return p

    def _legacy(self, **kw):
        q = {"td": 10.1763, "eff": 0.96302, "ripple": 12.3, "kind": "cmaes",
             "overrides": {"tooth_width": 9.2}, "current_a": 91.92388155425117,
             "gamma_deg": None}
        q.update(kw)
        return q

    def test_torque_and_mass_are_restored(self, log):
        pts = [self._legacy()]
        assert O._backfill_point_metrics(pts) == 1
        assert pts[0]["torque"] == pytest.approx(31.051)
        assert pts[0]["mass"] == pytest.approx(3.051)

    def test_a_point_whose_metrics_disagree_is_left_alone(self, log):
        """Same geometry, same current, different measured efficiency = not the
        same eval.  The log is a cross-run accumulator; a loose match here would
        import another run's numbers into this run's cloud."""
        pts = [self._legacy(eff=0.9500)]
        assert O._backfill_point_metrics(pts) == 0
        assert pts[0].get("torque") is None

    def test_a_point_from_another_current_is_left_alone(self, log):
        pts = [self._legacy(current_a=78.49)]
        assert O._backfill_point_metrics(pts) == 0

    def test_it_never_adds_points(self, log):
        pts = [self._legacy()]
        O._backfill_point_metrics(pts)
        assert len(pts) == 1

    def test_a_point_that_already_has_torque_is_untouched(self, log):
        pts = [self._legacy(torque=99.0, mass=1.0)]
        assert O._backfill_point_metrics(pts) == 0
        assert pts[0]["torque"] == 99.0


class TestThePointCarriesWhatTheReportNeeds:
    def test_mass_travels_with_every_published_point(self):
        """torque, torque density and mass are one measurement in three forms;
        without mass the card cannot derive the torque axis for older runs."""
        p = O._pt({"ok": True, "overrides": {}, "res": {
            "T_em_Nm": 31.051, "torque_per_mass_Nm_kg": 10.1763,
            "efficiency": 0.96302, "T_ripple_pct": 12.3,
            "mass_total_kg": 3.051}}, "cmaes")
        assert p["torque"] == pytest.approx(31.051)
        assert p["mass"] == pytest.approx(3.051)
        assert P.axes_of_point(p)["td"] == pytest.approx(10.1763)

    def test_a_point_with_only_td_and_mass_still_yields_torque(self):
        a = P.axes_of_point({"td": 10.1763, "mass": 3.051, "ripple": 12.3,
                             "eff": 0.96302})
        assert a["torque"] == pytest.approx(31.048, abs=5e-3)
