"""Reader regressions from the exhaustive PR900 inventory pass; synthetic, no FEM."""
from types import SimpleNamespace

import pytest

MARKER = "fe_on_shaft_k3d_v1"


def test_scalar_ir_keeps_standalone_basis_and_legacy_shape():
    from motor_ai_sim.contracts.adapters import result_ir_from_transient
    s = {"T_em_avg_Nm": 10, "P_mech_W": 870, "efficiency": .8969}
    old = result_ir_from_transient({"summary": s}).scalars.model_dump()
    marked = result_ir_from_transient({"summary": {**s, "shaft_convention": MARKER}})
    assert marked.scalars.p_mech_W == 870
    assert marked.scalars.torque_Nm == 10
    assert marked.scalars.torque_basis == "2-D field"
    assert marked.scalars.shaft_convention == MARKER
    assert "shaft_convention" not in old and "torque_basis" not in old


def test_pareto_does_not_derive_field_torque_from_shaft_density_or_rank_mixed():
    from motor_ai_sim.optimization.pareto import axes_of_point, axes_of_metrics, report
    old = {"td": 4.35, "mass": 2, "eff": .8969, "ripple": 1}
    assert axes_of_point(old)["torque"] == 8.7
    assert axes_of_point({**old, "shaft_conv": 1})["torque"] is None
    assert axes_of_point({"torque": 10, "mass": 2, "shaft_conv": 1})["td"] is None
    assert axes_of_metrics({"shaft_convention": MARKER, "T_em_Nm": 10, "mass_total_kg": 2})["td"] is None
    baseline = {"T_em_Nm": 10, "torque_per_mass": 4.35, "T_ripple_pct": 2, "efficiency": .8, "shaft_convention": MARKER}
    p = {**old, "torque": 11, "current_a": 10, "gamma_deg": 0, "kind": "candidate"}
    out = report([p, {**p, "shaft_conv": 1}], baseline, {"current_a": 10, "gamma_deg": 0})
    assert not out["points"][0]["dominates"] and not out["points"][0]["pareto_front"]
    assert out["points"][1]["dominates"]


def test_surrogate_matrix_guards_mixed_and_readers_select_newest_convention():
    from motor_ai_sim.optimization.surrogate import build_matrix, variable_importance, suggest
    row = {"overrides": {"length": 10}, "gamma_deg": 0, "torque": 10, "td": 5, "eff": .9, "ripple": 1}
    mixed = [row, {**row, "shaft_conv": 1}]
    with pytest.raises(ValueError, match="Mixed shaft conventions"):
        build_matrix(mixed)
    for result in (variable_importance(mixed), suggest(mixed)):
        assert result["ok"] is False and result["n"] == 1
        assert result["need"] == 20 and result["dropped_other_convention"] == 1
    assert len(build_matrix([row])[0]) == 1
    assert len(build_matrix([{**row, "shaft_conv": 1}])[0]) == 1


def test_rebuilt_pareto_is_grouped_by_convention():
    from motor_ai_sim.sweep_rebuild import build_result
    old = {"status": "ok", "current_a": 10, "torque": 10, "eff": .99, "mass": 2, "td": 5}
    new = {**old, "current_a": 11, "shaft_conv": 1, "eff": .89, "td": 4.35}
    out = build_result("test", [old, new])
    assert set(out["pareto_indices"]) == {0, 1}


def test_marked_terminal_budget_does_not_borrow_eta_or_turn_zero_into_positive(monkeypatch):
    from motor_ai_sim import terminals as T
    monkeypatch.setattr(T, "validate_terminals", lambda b: {"stated": True, "note": "synthetic"})
    monkeypatch.setattr(T, "terminal_loss_W", lambda *a, **k: {"P_leads_board_total_W": 10, "P_leads_W": 10, "P_board_W": 0})
    s = {"I_phase_rms_A": 10, "shaft_convention": MARKER, "P_mech_W": 870,
         "P_mech_extra_W": 20, "efficiency": 870 / 970, "efficiency_shaft": 850 / 970}
    out = T.duty_terminal_budget(s, {})
    assert out["efficiency_at_terminals"] == pytest.approx(850 / 980, abs=1e-6)
    s.pop("efficiency_shaft")
    assert "efficiency_at_terminals" not in T.duty_terminal_budget(s, {})
    s.update(P_mech_W=6, efficiency_shaft=0)
    assert T.duty_terminal_budget(s, {})["efficiency_at_terminals"] == 0
    s.update(op_mode="generator", P_mech_W=930, efficiency_shaft=830/950)
    assert T.duty_terminal_budget(s, {})["efficiency_at_terminals"] == pytest.approx(820/950, abs=1e-6)


def test_passport_cooling_card_reads_corrected_power_without_second_k(monkeypatch, tmp_path):
    from motor_ai_sim.passport_v1 import fullcard as F
    cp = {"continuous_rating": {"I_cont_A_rms": 10, "reference": {"shaft_convention": MARKER},
                              "power": {"T_em_Nm": 10, "P_shaft_W": 850, "eta_shaft": 850/970}}, "P_mech_extra_W": 20}
    monkeypatch.setattr(F, "_load", lambda p: {"coupling": cp})
    monkeypatch.setattr(F.S3, "motor_point", lambda *a, **k: {"P_mech_W": 20})
    hm = SimpleNamespace(operating_gamma=lambda *a: (None, "synthetic"))
    args = (tmp_path, "synthetic", [{"id": "one", "cooling": "air", "solve_to": "continuous", "rpm": 100*30/3.141592653589793}])
    kw = dict(rec={"loss_grid": {"plan": {"R_hot_ohm": 1, "v_phase_limit_V": 100}}}, snap={}, hm=hm, rows_grid=[], kT=.9)
    row = F.cooling_block(*args, **kw)["studies"][0]["s1"]
    assert row["card"]["P_shaft_W"] == 850 and row["card"]["T_Nm"] == pytest.approx(8.5)
    assert row["power_linear"]["shaft_convention"] == MARKER
    assert row["card"]["P_mech_W"] == 20
    assert "P_mech_W holds bearing / windage loss" in row["card"]["basis"]
    cp["continuous_rating"]["power"].pop("P_shaft_W")
    assert F.cooling_block(*args, **kw)["studies"][0]["s1"]["card"]["P_shaft_W"] is None


def test_public_and_support_efficiency_carry_basis():
    from motor_ai_sim.public_motors import build_row, assert_allowlisted
    from motor_ai_sim.support_catalog import _rated_lines
    duty = {"name": "rated", "rpm": 1000, "torque_nm": 10, "power_kw": 1,
            "result": {"efficiency_pct": 89.69, "shaft_conv": 1}}
    row = build_row({"name": "synthetic"}, {"name": "test", "duties": [duty]})
    assert row["rated"]["shaft_conv"] == 1
    assert_allowlisted([row])
    assert "core loss on the shaft" in _rated_lines(duty)[0]
    duty["result"].pop("shaft_conv")
    assert "shaft_conv" not in build_row({"name": "synthetic"}, {"name": "test", "duties": [duty]})["rated"]
    # an unmarked duty on its own keeps its earlier text; named only when listed with marked duties
    assert _rated_lines(duty)[0].endswith("efficiency 89.7 %") and "basis" not in _rated_lines(duty)[0]
    assert _rated_lines(duty, mixed=True)[0].endswith("efficiency 89.7 % (earlier shaft basis)")


def test_support_block_names_the_basis_only_when_bases_mix():
    from motor_ai_sim.support_catalog import render_block

    def cfg(name, marked):
        return {"name": name, "card_date": "2026-11-26",
                "duties": [{"name": "rated", "rpm": 1000, "torque_nm": 10, "current_arms": 10, "duty_class": "continuous",
                            "result": {"efficiency_pct": 89.69, **({"shaft_conv": 1} if marked else {})}}]}
    die = {"name": "synthetic", "stator_diameter": 50, "configs": [cfg("A", False)]}
    alone = render_block([die])
    assert "efficiency 89.7 %" in alone and "basis" not in alone and "core loss on the shaft" not in alone
    die["configs"].append(cfg("B", True))
    both = render_block([die])
    assert "efficiency 89.7 % (earlier shaft basis)" in both and "efficiency 89.7 % (core loss on the shaft)" in both
    die["configs"] = [cfg("B", True)]
    marked = render_block([die])
    assert "(core loss on the shaft)" in marked and "earlier shaft basis" not in marked


def test_bench_missing_marked_coupling_values_do_not_fall_back_to_field():
    from motor_ai_sim.motor_tests.model_source import from_docs
    s = {"shaft_convention": MARKER, "T_em_avg_Nm": 10, "efficiency": .9,
         "P_mech_extra_W": 20, "rpm": 1000}
    cfg = {"duties": [{"name": "rated", "rpm": 1000, "summary": s}]}
    row = from_docs({}, cfg, {}, {})["duties"][0]
    assert row["torque_nm"] is None and row["eff_pct"] is None
    s.pop("shaft_convention")
    row = from_docs({}, cfg, {}, {})["duties"][0]
    assert row["torque_nm"] == 10 and row["eff_pct"] == 90


def test_baseline_line_marker_addition_does_not_change_old_numbers():
    from motor_ai_sim.routes.optimization import _make_bline
    base = {"torque_per_mass_Nm_kg": 5, "efficiency": .9, "current_a": 10}
    bump = {"torque_per_mass_Nm_kg": 6, "efficiency": .8, "current_a": 11}
    old = _make_bline(base, bump, 10)
    new = _make_bline({**base, "shaft_convention": MARKER}, {**bump, "shaft_convention": MARKER}, 10)
    assert "shaft_conv" not in old
    assert new.pop("shaft_conv") == 1 and new == old


def test_declared_legacy_digest_omission_also_applies_to_two_unmarked_records():
    from motor_ai_sim.digest.readable import mechanical_numbers
    old = {"em_run": {"numbers": {"T_em_avg_Nm": 10, "P_mech_W": 1000}},
           "coupled": {"em": {"T_em_avg_Nm": 9, "P_mech_W": 900}, "efficiency_shaft": .876289}}
    assert mechanical_numbers(old) == {"T": 10, "P": 1000, "eta": None, "P_out": None, "basis": "earlier"}


@pytest.fixture
def tiny_surrogate(monkeypatch):
    """Exercise real training on tiny synthetic data: 2 trees, one CPU, 2 repeats."""
    from motor_ai_sim.optimization import surrogate as S
    from sklearn.ensemble import RandomForestRegressor
    import sklearn.inspection as inspection
    permutation = inspection.permutation_importance
    monkeypatch.setattr(S, "_fit_forest", lambda X, y: RandomForestRegressor(
        n_estimators=2, max_depth=2, random_state=0, n_jobs=1).fit(X, y))
    monkeypatch.setattr(inspection, "permutation_importance", lambda model, X, y, **kw:
        permutation(model, X, y, n_repeats=2, random_state=0, n_jobs=1))
    return S


def surrogate_rows(marked, n=40, torque=10):
    return [{"overrides": {"length": 10 + i / 10}, "gamma_deg": 0,
             "torque": torque + i / 100, "td": 4 + i / 100,
             "eff": .85 + i / 1000, "ripple": 1 + i / 100,
             **({"shaft_conv": 1} if marked else {})} for i in range(n)]


def test_surrogate_40_old_plus_one_new_refuses_only_for_current_sample_count(tiny_surrogate):
    S = tiny_surrogate
    rows = surrogate_rows(False, torque=100) + surrogate_rows(True, 1)
    for out in (S.variable_importance(rows), S.suggest(rows, n_candidates=8)):
        assert out["ok"] is False and out["n"] == 1 and out["need"] == 20
        assert out["dropped_other_convention"] == 40


@pytest.mark.parametrize("newest_marked", [False, True])
def test_surrogate_40_plus_40_trains_only_newest_basis(tiny_surrogate, newest_marked):
    S = tiny_surrogate
    selected = surrogate_rows(newest_marked)
    mixed = surrogate_rows(not newest_marked, torque=100) + selected
    before = [dict(row) for row in mixed]
    for reader, kw in ((S.variable_importance, {}), (S.suggest, {"n_candidates": 8})):
        out, expected = reader(mixed, **kw), reader(selected, **kw)
        assert out["ok"] and out.get("n", out.get("n_train")) == 40
        assert out.pop("dropped_other_convention") == 40
        assert expected.pop("dropped_other_convention") == 0
        assert out == expected  # same models, rankings and suggestions as homogeneous input
    assert mixed == before
    assert S.select_convention(mixed, shaft_conv=1)[0] == [r for r in mixed if r.get("shaft_conv") == 1]


def test_surrogate_all_unmarked_retains_full_training_matrix(tiny_surrogate):
    S = tiny_surrogate
    rows = surrogate_rows(False)
    assert S.select_convention(rows) == (rows, 0)
    X, keys, ys = S.build_matrix(rows)
    assert len(X) == 40 and keys == ["length"]
    assert ys["td"].tolist() == [r["td"] for r in rows]
    for out in (S.variable_importance(rows), S.suggest(rows, n_candidates=8)):
        assert out["ok"] and out["dropped_other_convention"] == 0


def test_surrogate_routes_select_before_operating_filter_and_report_drops(tiny_surrogate, monkeypatch, caplog):
    from motor_ai_sim.routes import optimization as O
    S = tiny_surrogate
    rows = surrogate_rows(False, torque=100) + surrogate_rows(True)
    monkeypatch.setattr(S, "load_dataset", lambda *a: rows)
    original = S.suggest
    monkeypatch.setattr(S, "suggest", lambda rows, **kw: original(rows, n_candidates=8, **kw))
    with caplog.at_level("INFO"):
        result = O._surrogate_seed_overrides([{"name": "length", "lo": 10, "hi": 14}], 5)
    assert result and 10 <= result["length"] <= 14
    assert "dropped_other_convention: 40" in caplog.text
    report = O.surrogate_report(suggest=1)
    assert report["n_total"] == 80 and report["dropped_other_convention"] == 40
    assert report["importance"]["n"] == report["suggest"]["n_train"] == 40
    assert report["importance"]["dropped_other_convention"] == report["suggest"]["dropped_other_convention"] == 40
    # A later imported old run must not change the current marked warm-start basis.
    rows[:] = surrogate_rows(True) + surrogate_rows(False, torque=100)
    seen = []
    def capture(rows, **kw):
        seen.extend(rows)
        return {"ok": True, "suggestions": [{"overrides": {"length": 12}}]}
    monkeypatch.setattr(S, "suggest", capture)
    assert O._surrogate_seed_overrides([{"name": "length", "lo": 10, "hi": 14}], 5)
    assert len(seen) == 40 and all(r.get("shaft_conv") == 1 for r in seen)
    rows[:] = surrogate_rows(False)
    seen.clear()
    assert O._surrogate_seed_overrides([{"name": "length", "lo": 10, "hi": 14}], 5)
    assert seen == rows



@pytest.mark.parametrize("baseline", [None, {}])
def test_pareto_empty_baseline_uses_each_points_convention(baseline):
    from motor_ai_sim.optimization.pareto import report
    old = {"torque": 20, "td": 10, "eff": .99, "ripple": 1, "current_a": 10, "gamma_deg": 0}
    new = {**old, "torque": 10, "td": 4.35, "eff": .89, "shaft_conv": 1}
    op = {"current_a": 10, "gamma_deg": 0}
    for points in ([old], [new], [old, new]):
        out = report(points, baseline, op)
        assert out["summary"]["n_candidates"] == len(points)
        assert all(p["pareto_front"] and p["pareto_front_td"] for p in out["points"])
        assert not any(p["dominates"] for p in out["points"])


def test_fit_producer_basis_reaches_design_agent_compactor(monkeypatch):
    from contextlib import nullcontext
    from motor_ai_sim import mcp_tools as M, duty_results as D
    from motor_ai_sim.design_agent import tools as A
    duty = {"duty": "rated", "torque_nm": 10, "speed_rpm": 1000,
            "line_voltage_peak_v": 10, "mass_kg": 2, "mode": "motor",
            "power_kw": 1, "efficiency_pct": 90}
    machine = {"die": "synthetic", "config": "test", "ownership": "own",
               "mass_kg": 2, "outer_diameter_mm": 50, "active_length_mm": 10}
    monkeypatch.setattr(M, "acting_as", lambda p: nullcontext())
    monkeypatch.setattr(D, "index", lambda *a: {})
    monkeypatch.setattr(M, "_checked", lambda x: x)
    monkeypatch.setattr(A, "_visible_configs", lambda: [{"die": {"name": "synthetic"}, "config": {"name": "test"}}])
    for marked in (False, True):
        monkeypatch.setattr(M, "_machines", lambda: [{**machine, "duties": [{**duty, "shaft_conv": int(marked)}]}])
        for target, key in ((9, "fits"), (11, "rejected")):
            produced = M.check_fit(None, target, 1000)
            compact = A._compact_fit(produced)
            if marked:
                assert compact[key][0]["shaft_convention"] == produced[key][0]["shaft_convention"]
                assert compact[key][0]["shaft_convention"] == "core loss on the shaft"
            else:                                       # earlier rows alone: their earlier shape, no basis key
                assert "shaft_convention" not in produced[key][0] and "shaft_convention" not in compact[key][0]
    # both kinds in one answer: the earlier row names its basis too, and sorts after the marked one
    other = {**machine, "config": "test2", "duties": [{**duty, "shaft_conv": 1}]}
    monkeypatch.setattr(M, "_machines", lambda: [{**machine, "duties": [{**duty, "shaft_conv": 0}]}, other])
    monkeypatch.setattr(A, "_visible_configs", lambda: [{"die": {"name": "synthetic"}, "config": {"name": c}}
                                                        for c in ("test", "test2")])
    produced = M.check_fit(None, 9, 1000)
    compact = A._compact_fit(produced)
    assert [f["shaft_convention"] for f in compact["fits"]] == ["core loss on the shaft",
                                                                "earlier run: every loss on the electrical side"]


def test_passport_cooling_table_names_the_basis_only_with_a_marked_row():
    from motor_ai_sim.passport_v1 import fullcard as F

    def study(cooling, marked):
        card = {"T_Nm": 8.5, "P_shaft_W": 850.0, "basis": "own S1 pass" if not marked else "S1 rating at the coupling"}
        if marked:
            card["shaft_convention"] = MARKER
        return {"cooling": cooling, "rpm": 1000.0, "solve_to": "continuous",
                "s1": {"I_cont_A_rms": 10, "card": card, "temperatures_c": {}, "verified": True}}
    old = F._cooling_html({"cooling": {"method": "synthetic", "studies": [study("air", False)]}})
    assert "power basis" not in old and "own S1 pass" not in old and "earlier shaft basis" not in old
    both = F._cooling_html({"cooling": {"method": "synthetic", "studies": [study("air", False), study("propeller", True)]}})
    assert "power basis" in both and "earlier shaft basis" in both and "S1 rating at the coupling" in both
    marked = F._cooling_html({"cooling": {"method": "synthetic", "studies": [study("propeller", True)]}})
    assert "power basis" in marked and "earlier shaft basis" not in marked
