"""Behavioral regressions for the five final PR900 convention patches; no solves."""
import copy
import math

import pytest

from motor_ai_sim import shaft_convention as SC


def test_digest_two_duties_detect_the_effective_held_result_in_both_languages(tmp_path, monkeypatch):
    from motor_ai_sim.digest import collect as C, readable as RD
    from motor_ai_sim.development import store as PS
    monkeypatch.setattr(PS, "path_for", lambda pid: tmp_path / str(pid))
    stamp = "2026-11-26T10:00:00Z"
    old = {"computed_at": stamp, "numbers": {"T_avg_Nm": 10, "P_mech_W": 1000}}
    held = {"computed_at": stamp, "em": {
        "shaft_convention": SC.SHAFT_CONVENTION, "op_mode": "motor",
        "rpm": 100 * 60 / (2 * math.pi), "P_mech_W": 870, "T_shaft_Nm": 8.7,
        "T_em_avg_Nm": 10, "efficiency": 870 / 970},
        "P_mech_extra_W": 20, "efficiency_shaft": 850 / 970,
        "coil_temp_c": 60, "magnet_temp_c": 50, "converged": True,
        "hold_torque": {"basis": "held_torque", "torque_nm": 8.5, "target_nm": 8.5, "held": True}}
    duties = {"A": {"em_run": old, "coupled": held}, "B": {"em_run": old}}
    before = copy.deepcopy(duties)
    project = {"id": "synthetic_r8", "linked_motors": [{"die": "D", "config": "C"}]}
    items = C.collect_project(project, 0, 4e9, results=lambda *_: duties)
    rows = sorted((i.en, i.zh) for i in items if i.kind == "results")
    assert len(rows) == 2
    en, zh = rows[0]
    assert en.startswith("A: shaft torque 8.5 N·m at 850 W, efficiency 87.6 %, core loss on the shaft")
    assert zh.startswith("A：轴转矩 8.5 N·m，850 W，效率 87.6 %，铁损计入轴侧")
    assert rows[1] == ("B: 10 N·m at 1 kW, earlier shaft basis", "B：10 N·m，1 kW，较早轴功率口径")
    # Owner headlines still choose their original mechanical source (no held override).
    assert RD.line_basis(duties["A"]) == "earlier"
    assert RD.line_basis(duties["A"], result=True) == "shaft"
    assert RD.headline(duties["A"]).startswith("T 10 N·m, P 1 kW")
    assert "core loss" not in RD.headline(duties["A"])
    assert duties == before


@pytest.mark.parametrize("field", ["T", "P_out"])
@pytest.mark.parametrize("basis", ["shaft", "earlier"])
def test_digest_basis_guards_include_torque_and_generator_output(field, basis, monkeypatch):
    from motor_ai_sim.digest import readable as RD
    # Exercise both guards with an isolated mechanical selection, independent of source parsing.
    mech = dict(T=None, P=None, P_out=None, eta=None, basis=basis)
    mech[field] = 8.5 if field == "T" else 830
    monkeypatch.setattr(RD, "mechanical_numbers", lambda _: mech.copy())
    assert RD.line_basis({}) == basis
    assert RD.line_basis({}, result=True) == basis
    en, zh = RD.result_line("A", {}, mixed=True)
    assert ("core loss on the shaft" if basis == "shaft" else "earlier shaft basis") in en
    assert ("铁损计入轴侧" if basis == "shaft" else "较早轴功率口径") in zh
    if basis == "earlier":
        assert "earlier shaft basis" not in RD.result_line("A", {})[0]


def warm_fixture():
    old = {"cfg_fp": "f", "overrides": {"a": 1.0}, "current_a": 10., "gamma_deg": 0.,
           "ripple": 1., "td": 5., "eff": 1000 / 1100}
    marked = {**old, "shaft_conv": 1, "overrides": {"a": 2.0}, "td": 4.35, "eff": 870 / 970}
    base = {"torque_per_mass_Nm_kg": old["td"], "efficiency": old["eff"],
            "_bline": {"td_a": old["td"], "eff_a": old["eff"], "w_td": .01, "w_eff": .5,
                       "norm": math.hypot(.01, .5)}}
    return old, marked, base


def test_warm_start_refuses_a_real_unmarked_line_before_scoring(monkeypatch, caplog):
    from motor_ai_sim.routes import optimization as O
    old, marked, base = warm_fixture()
    def forbidden(*args, **kwargs):
        pytest.fail("cross-convention rows reached scoring")
    monkeypatch.setattr(O, "_descent_cost", forbidden)
    assert O._auto_warm_start([old] * 5 + [marked] * 3, [{"name": "a", "x0": 1.}],
                              "f", 10., 0., 10., base) is None
    assert "auto warm start: selected rows and baseline use different shaft conventions" in caplog.text


@pytest.mark.parametrize("marker", ["full", "compact", "line"])
@pytest.mark.parametrize("mixed", [False, True])
def test_warm_start_scores_marked_rows_against_a_matching_baseline(marker, mixed):
    from motor_ai_sim.routes import optimization as O
    old, marked, base = warm_fixture()
    base.update(torque_per_mass_Nm_kg=marked["td"], efficiency=marked["eff"])
    base["_bline"].update(td_a=marked["td"], eff_a=marked["eff"])
    if marker == "line":
        base["_bline"]["shaft_conv"] = 1
    else:
        base.update({"shaft_convention": SC.SHAFT_CONVENTION} if marker == "full" else {"shaft_conv": 1})
    rows = ([old] * 5 if mixed else []) + [marked] * 3
    out = O._auto_warm_start(rows, [{"name": "a", "x0": 1.}], "f", 10., 0., 10., base)
    assert out == {"x": [2.], "n": 3, "F": 0., "overrides": {"a": 2.}}
    assert O._auto_warm_start([old] * 5, [{"name": "a", "x0": 1.}], "f", 10., 0., 10., base) is None


def test_unmarked_warm_start_keeps_order_filters_scoring_and_seed():
    from motor_ai_sim.routes import optimization as O
    old, marked, base = warm_fixture()
    rows = [{**old, "overrides": {"a": a}, "td": td} for a, td in [(2., 5.2), (3., 5.2), (4., 5.1)]]
    # Every rejected row has a tempting higher score; a marked row of another machine must not switch this one.
    for patch in ({"cfg_fp": "other"}, {"current_a": 11.}, {"gamma_deg": 1.}, {"ripple": 11.}, {"eff": float("nan")}):
        rows.append({**old, "td": 10., **patch})
    rows.append({**marked, "cfg_fp": "other"})
    before = copy.deepcopy(rows[:-2])
    out = O._auto_warm_start(rows, [{"name": "a", "x0": 1.}], "f", 10., 0., 10., base)
    assert out["n"] == 3 and out["x"] == [2.] and out["overrides"] == {"a": 2.}
    assert out["F"] == pytest.approx(.01 * .2 / math.hypot(.01, .5))
    fallback = O._auto_warm_start(rows, [{"name": "a", "x0": 1.}], "f", 10., 0., 10., base,
                                  accept=lambda ov: ov["a"] != 2.)
    assert fallback["n"] == 3 and fallback["x"] == [3.] and fallback["F"] == out["F"]
    assert rows[:-2] == before
