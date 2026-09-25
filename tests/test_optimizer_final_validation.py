"""Final optimizer publication requires real standard-quality FEM metadata."""

from pathlib import Path

from motor_ai_sim.routes import optimization as O


def _out(x, current, *, quality=True):
    eff = {0: 0.50, 1: 0.70, 2: 0.95}[int(x["g"])]
    if int(x["g"]) == 0 and current > 10:
        eff = 0.45
    return {"ok": True, "res": {
        "T_em_Nm": 1.0, "efficiency": eff,
        "torque_per_mass_Nm_kg": 2.0 + x["g"],
        "T_ripple_pct": 4.0, "current_a": current,
        "nonlinear_converged": True, "cogging_sampling_purpose": "standard",
        "cogging_sampling_final_quality_sufficient": quality,
        "eddy_settled": True,
    }}


def _finalize(evaluate):
    points = [{"kind": "cmaes", "overrides": {"g": 2},
               "current_a": 10.0, "eff": 0.8, "td": 4.0,
               "ripple": 4.0, "v_peak": 2.0, "thd": 1.0}]
    coarse_base = {"efficiency": 0.5,
                   "_bline": {"w_td": 1.0, "w_eff": 1.0}}
    return O._finalize_standard_shortlist(
        points=points, best_x={"g": 1},
        best_metrics={"efficiency": 0.9, "current_a": 10.0},
        coarse_base=coarse_base, base_x={"g": 0},
        base_current=10.0, bump_pct=10.0, evaluate=evaluate,
        score=lambda m, b: (-float(m["efficiency"]), float(m["efficiency"])))


def test_standard_refs_and_shortlist_rerank_before_publication(monkeypatch):
    calls = []

    def evaluate(x, current):
        calls.append((dict(x), current))
        return _out(x, current)

    final = _finalize(evaluate)
    assert final["status"] == "certified"
    # Baseline A first and alone; then B and the finalists (fix D: in parallel,
    # so their order is not fixed).
    assert calls[0] == ({"g": 0}, 10.0)
    assert sorted((tuple(x.items()), c) for x, c in calls[1:]) == sorted([
        ((("g", 0),), 11.0), ((("g", 1),), 10.0), ((("g", 2),), 10.0)])
    assert final["baseline"]["_bline"]["eff_a"] == 0.50
    assert final["baseline"]["_bline"]["eff_b"] == 0.45
    assert final["winner"]["x"] == {"g": 2}  # coarse incumbent was g=1

    monkeypatch.setattr(O, "_descent_state", {"cancel": False})
    result = {"best": {"overrides": {"g": 1}}}
    points = []
    assert O._publish_standard_final(result, final, points)
    assert result["screening_best"]["overrides"] == {"g": 1}
    assert result["best"]["x"] == {"g": 2}
    assert result["best"]["metrics"]["current_a"] == 10.0
    assert O._descent_state["apply_eligible"] is True
    assert all(p["sampling_quality"] == "standard" for p in points)


def test_reference_failure_or_no_passing_finalist_cannot_publish(monkeypatch):
    def bad_reference(x, current):
        return _out(x, current, quality=not (x["g"] == 0 and current > 10))

    assert _finalize(bad_reference)["status"] == "failed"

    def bad_a(x, current):
        return _out(x, current, quality=not (x["g"] == 0 and current <= 10))

    assert _finalize(bad_a)["reason"].startswith("standard baseline A")

    def every_finalist_fails(x, current):
        return _out(x, current, quality=x["g"] == 0)

    failed = _finalize(every_finalist_fails)
    assert failed["status"] == "failed"
    assert {f["overrides"]["g"] for f in failed["failed"]} == {1, 2}
    monkeypatch.setattr(O, "_descent_state", {"cancel": False})
    result = {"best": {"overrides": {"g": 1}}}
    assert not O._publish_standard_final(result, failed, [])
    assert result["best"] is None
    assert O._descent_state["apply_eligible"] is False


def test_one_failing_finalist_drops_out_and_is_reported(monkeypatch):
    """Review item 5a: a finalist whose standard solve fails must not kill a
    run whose baselines and other finalists passed."""
    def bad_finalist(x, current):
        if x["g"] == 2:
            return {"ok": False, "error": "unconverged FEM frames [3] of 72"}
        return _out(x, current)

    final = _finalize(bad_finalist)
    assert final["status"] == "certified"
    assert final["winner"]["x"] == {"g": 1}
    assert final["dropped_count"] == 1
    assert final["failed"] == [{"overrides": {"g": 2}, "current_a": 10.0,
                                "reason": "unconverged FEM frames [3] of 72"}]
    monkeypatch.setattr(O, "_descent_state", {"cancel": False})
    result = {"best": {"overrides": {"g": 1}}}
    assert O._publish_standard_final(result, final, [])
    pub = result["final_validation"]
    assert pub["status"] == "certified" and pub["dropped_count"] == 1
    assert pub["failed"][0]["reason"].startswith("unconverged")
    assert O._descent_state["apply_eligible"] is True


def test_standard_quality_requires_explicit_purpose_convergence_and_resolution():
    good = _out({"g": 1}, 10.0)
    assert O._standard_quality(good)[0]
    for field, value in (
        ("cogging_sampling_purpose", "optimization"),
        ("nonlinear_converged", False),
        ("cogging_sampling_final_quality_sufficient", False),
        # owner 2026-09-24: an unsettled coupled-eddy result never certifies —
        # the same rule the Sweep Apply check and the re-check apply.
        ("eddy_settled", False),
        ("eddy_settled", None),
    ):
        bad = {"ok": True, "res": dict(good["res"], **{field: value})}
        assert not O._standard_quality(bad)[0]


def test_unsettled_baseline_a_fails_closed():
    """A capped (unsettled) cold baseline A must not be certified."""
    def unsettled_a(x, current):
        out = _out(x, current)
        if x["g"] == 0 and current <= 10:
            out["res"]["eddy_settled"] = False
        return out

    final = _finalize(unsettled_a)
    assert final["status"] == "failed"
    assert final["reason"].startswith("standard baseline A")
    assert "not settled" in final["reason"]


def test_scan_points_are_preliminary_and_apply_is_direct():
    point = O._point_from_eval(_out({"g": 1}, 10.0), {}, 10.0, 0, 0, 100.0)
    assert point["feasible"] is True
    assert point["final_validation_status"] == "preliminary"
    assert point["apply_eligible"] is False

    # 2026-09-25 (owner): Sweep Apply is direct — it reads the stored point
    # (machine-mismatch checked, see O._scan_point_for_apply) and writes it
    # into the machine immediately, with NO re-solve at standard/
    # cogging_quality resolution first (that stays a Descent/Auto-only path).
    panel = (Path(__file__).resolve().parents[1] / "web/src/components/sweep/SweepStudyPanel.tsx").read_text(encoding="utf-8")
    assert panel.count("apply_eligible: p.apply_eligible === true") == 2  # chart and table
    apply_body = panel.split("const applyPoint = async", 1)[1].split("// kU converts", 1)[0]
    assert apply_body.index("/api/optimization/scan/apply_point") < apply_body.index("updateGeometryViaApi")
    assert "/api/optimization/scan/validate_point" not in apply_body
    assert "v?.apply_eligible" not in apply_body
    assert "v?.cogging_sampling_final_quality_sufficient" not in apply_body
    assert "v?.nonlinear_converged" not in apply_body
    assert "disabled={applying || running}" in panel
