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
    assert calls[:2] == [({"g": 0}, 10.0), ({"g": 0}, 11.0)]
    assert {tuple(x.items()) for x, _ in calls[2:]} == {
        (("g", 1),), (("g", 2),)}
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


def test_reference_or_shortlisted_quality_failure_cannot_publish(monkeypatch):
    def bad_reference(x, current):
        return _out(x, current, quality=not (x["g"] == 0 and current > 10))

    assert _finalize(bad_reference)["status"] == "failed"

    def bad_finalist(x, current):
        return _out(x, current, quality=x["g"] != 1)

    failed = _finalize(bad_finalist)
    assert failed["status"] == "failed"
    monkeypatch.setattr(O, "_descent_state", {"cancel": False})
    result = {"best": {"overrides": {"g": 1}}}
    assert not O._publish_standard_final(result, failed, [])
    assert result["best"] is None
    assert O._descent_state["apply_eligible"] is False


def test_standard_quality_requires_explicit_purpose_convergence_and_resolution():
    good = _out({"g": 1}, 10.0)
    assert O._standard_quality(good)[0]
    for field, value in (
        ("cogging_sampling_purpose", "optimization"),
        ("nonlinear_converged", False),
        ("cogging_sampling_final_quality_sufficient", False),
    ):
        bad = {"ok": True, "res": dict(good["res"], **{field: value})}
        assert not O._standard_quality(bad)[0]


def test_scan_points_are_preliminary_and_old_saved_points_cannot_apply():
    point = O._point_from_eval(_out({"g": 1}, 10.0), {}, 10.0, 0, 0, 100.0)
    assert point["feasible"] is True
    assert point["final_validation_status"] == "preliminary"
    assert point["apply_eligible"] is False

    panel = (Path(__file__).resolve().parents[1] / "web/src/components/sweep/SweepStudyPanel.tsx").read_text(encoding="utf-8")
    assert panel.count("apply_eligible: p.apply_eligible === true") == 2  # chart and table
    apply_body = panel.split("const applyPoint = async", 1)[1].split("// kU converts", 1)[0]
    assert apply_body.index("if (p.apply_eligible !== true)") < apply_body.index("updateGeometryViaApi")
    assert apply_body.index("if (p.apply_eligible !== true)") < apply_body.index("autoSaveAppliedDesign")
    assert "disabled={selected.apply_eligible !== true}" in panel
