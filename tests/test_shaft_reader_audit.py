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


def test_surrogate_refuses_mixed_conventions_before_training():
    from motor_ai_sim.optimization.surrogate import build_matrix, variable_importance, suggest
    row = {"overrides": {"length": 10}, "gamma_deg": 0, "torque": 10, "td": 5, "eff": .9, "ripple": 1}
    mixed = [row, {**row, "shaft_conv": 1}]
    with pytest.raises(ValueError, match="Mixed shaft conventions"):
        build_matrix(mixed)
    assert variable_importance(mixed)["reason"] == "mixed_shaft_conventions"
    assert suggest(mixed)["reason"] == "mixed_shaft_conventions"
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
    assert "earlier shaft basis" in _rated_lines(duty)[0]


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
