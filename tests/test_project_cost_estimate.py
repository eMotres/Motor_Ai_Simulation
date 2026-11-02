"""Project cost estimate (owner 2026-11-02, simplified the same day): admin-only, 4 lines per iteration (tooling, motor
manufacturing, testing, shipping).  Pure logic plus the admin routes on a temporary project store.  No solver, no network;
neutral fixture ids and made-up numbers only."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim.development import cost_estimate as CE
from motor_ai_sim.development import cost_estimate_routes as R
from motor_ai_sim.development import cost_estimate_sources as SRC
from motor_ai_sim.development import store


@pytest.fixture
def schema():
    return CE.load_schema()


def _est(schema, iterations=2, **settings):
    est = CE.default_estimate(schema, "alpha", linked_motors=[{"die": "DIE-A", "config": "big"}])
    est["settings"].update(settings)
    est["settings"]["iterations"] = iterations
    return CE.normalize_estimate(est, schema, "alpha")


def _fill(line, amount, currency="EUR", by="owner@example.test"):
    line.update({"status": "filled", "amount": amount, "pct": None, "currency": currency, "source": "manual", "by": by,
                 "date": "2026-11-02"})


# ── schema and settings ─────────────────────────────────────────────────────────────────────────────────────────────

def test_schema_has_exactly_the_four_lines_and_the_settings(schema):
    assert [ln["id"] for ln in schema["lines"]] == ["tooling", "manufacturing", "testing", "shipping"]
    assert [f["id"] for f in schema["settings"]] == ["complex", "iterations", "currency", "contingency_pct"]
    assert [f["id"] for f in schema["iteration_fields"]] == ["motors", "bench"]
    assert schema["currencies"] == ["EUR", "CNY", "USD"] and schema["default_currency"] == "EUR"
    assert schema["factor_limits"] == [0.25, 4.0]
    for entry in schema["lines"] + schema["settings"] + schema["iteration_fields"]:
        assert entry["label"]["en"] and entry["label"]["zh-CN"] and entry["help"]["en"] and entry["help"]["zh-CN"]
    est = CE.default_estimate(schema, "alpha")
    assert est["settings"] == {"complex": False, "iterations": 2, "currency": "EUR", "contingency_pct": 10.0,
                               "target_motor": None}


def test_schema_refuses_a_fifth_line(tmp_path, schema):
    bad = json.loads(json.dumps(schema))
    bad["lines"].append(dict(bad["lines"][0], id="travel"))
    p = tmp_path / "s.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="lines"):
        CE.load_schema(p)


def test_iteration_count_defaults_and_limits(schema):
    assert CE.default_iterations(False, schema) == 2 and CE.default_iterations(True, schema) == 3
    # complex ticked with no explicit count -> 3
    est = CE.normalize_estimate({"settings": {"complex": True}}, schema, "alpha")
    assert est["settings"]["iterations"] == 3 and len(est["iterations"]) == 3
    one = _est(schema, iterations=1)
    assert len(one["iterations"]) == 1
    with pytest.raises(CE.EstimateError):
        _est(schema, iterations=6)
    with pytest.raises(CE.EstimateError):
        _est(schema, iterations=0)
    # motors per iteration: default 2, editable per iteration
    est = _est(schema, iterations=3)
    assert [it["motors"] for it in est["iterations"]] == [2, 2, 2]
    raw = json.loads(json.dumps(est))
    raw["iterations"][1]["motors"] = 4
    raw["iterations"][2]["bench"] = "propeller"
    est = CE.normalize_estimate(raw, schema, "alpha")
    assert [it["motors"] for it in est["iterations"]] == [2, 4, 2] and est["iterations"][2]["bench"] == "propeller"
    # cut back to 2 iterations
    raw["settings"]["iterations"] = 2
    assert len(CE.normalize_estimate(raw, schema, "alpha")["iterations"]) == 2


def test_validation_refuses_unknown_lines_currencies_and_negative_amounts(schema):
    est = _est(schema)
    for mutate, match in ((lambda b: b["iterations"][0]["lines"].update({"magnets": {}}), "unknown lines"),
                          (lambda b: b["iterations"][0]["lines"]["shipping"].update({"amount": 5, "currency": "GBP"}), "currency"),
                          (lambda b: b["iterations"][0]["lines"]["shipping"].update({"amount": -5}), "out of range"),
                          (lambda b: b["iterations"][0]["lines"]["tooling"].update({"pct": 20}), "pct"),
                          (lambda b: b["iterations"][0].update({"bench": "wind tunnel"}), "bench")):
        bad = json.loads(json.dumps(est))
        mutate(bad)
        with pytest.raises(CE.EstimateError, match=match):
            CE.normalize_estimate(bad, schema, "alpha")


# ── later-iteration tooling ─────────────────────────────────────────────────────────────────────────────────────────

def test_later_iterations_tooling_is_a_percent_of_iteration_one_or_an_amount(schema):
    est = _est(schema, iterations=3)
    t1, t2, t3 = (it["lines"]["tooling"] for it in est["iterations"])
    assert t1["status"] == "missing" and t1["pct"] is None
    assert t2["pct"] == 20.0 and t2["source"] == "rule" and t2["status"] == "filled"
    out = CE.compute(est, schema)
    assert out["iterations"][1]["tooling"] is None
    assert {"iteration": 2, "line": "tooling", "reason": "iteration 1 tooling missing"} in out["missing"]

    _fill(t1, 10000.0)
    t3.update({"pct": 35.0, "source": "manual", "by": "owner@example.test", "date": "2026-11-02"})
    out = CE.compute(CE.normalize_estimate(est, schema, "alpha"), schema)
    assert out["iterations"][0]["tooling"] == {"EUR": 10000.0}
    assert out["iterations"][1]["tooling"] == {"EUR": 2000.0}
    assert out["iterations"][2]["tooling"] == {"EUR": 3500.0}
    assert out["iterations"][1]["lines"]["tooling"]["origin"] == "20 % of iteration 1 tooling"
    assert "35 % of iteration 1 tooling (set by owner@example.test" in out["iterations"][2]["lines"]["tooling"]["origin"]
    # a manual amount instead of the %: the amount wins, the % is gone
    raw = json.loads(json.dumps(est))
    raw["iterations"][1]["lines"]["tooling"] = {"amount": 1500, "currency": "CNY"}
    est2 = CE.normalize_estimate(raw, schema, "alpha")
    assert est2["iterations"][1]["lines"]["tooling"]["pct"] is None
    assert est2["iterations"][1]["lines"]["tooling"]["source"] == "manual"
    assert CE.compute(est2, schema)["iterations"][1]["tooling"] == {"CNY": 1500.0}


# ── totals ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_totals_per_iteration_and_currency_with_contingency(schema):
    est = _est(schema, iterations=2, contingency_pct=10)
    it1, it2 = est["iterations"]
    _fill(it1["lines"]["tooling"], 1000.0, "EUR")
    _fill(it1["lines"]["manufacturing"], 400.0, "CNY")          # per motor, 2 motors
    _fill(it1["lines"]["testing"], 500.0, "EUR")
    _fill(it1["lines"]["shipping"], 300.0, "USD")
    it2["motors"] = 3
    _fill(it2["lines"]["manufacturing"], 100.0, "EUR")
    it2["bench"] = "none"                                          # no testing: not counted, not missing
    _fill(it2["lines"]["testing"], 999.0, "EUR")
    out = CE.compute(est, schema)
    i1, i2 = out["iterations"]
    assert (i1["tooling"], i1["manufacturing"], i1["testing"], i1["shipping"]) == ({"EUR": 1000.0}, {"CNY": 800.0}, {"EUR": 500.0}, {"USD": 300.0})
    assert i1["subtotal"] == {"EUR": 1500.0, "CNY": 800.0, "USD": 300.0}
    assert i2["tooling"] == {"EUR": 200.0} and i2["manufacturing"] == {"EUR": 300.0} and i2["testing"] == {}
    assert i2["shipping"] is None
    assert out["subtotal"] == {"EUR": 2000.0, "CNY": 800.0, "USD": 300.0}
    assert out["contingency"] == {"EUR": 200.0, "CNY": 80.0, "USD": 30.0}
    assert out["total"] == {"EUR": 2200.0, "CNY": 880.0, "USD": 330.0}
    assert out["currencies"] == ["EUR", "CNY", "USD"]
    assert any("several currencies" in w for w in out["warnings"])
    assert out["missing"] == [{"iteration": 2, "line": "shipping", "reason": "not filled"}]
    assert i2["lines"]["testing"]["origin"] == "no test bench in this iteration"
    assert i1["lines"]["manufacturing"]["origin"] == "entered by owner@example.test on 2026-11-02"


# ── suggest ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def _data(target_mass=21.0):
    def prod(ref, mass, per_motor, tooling=None, updated="2026-10-01", missing=0):
        return {"ref": ref, "title": ref, "updated_at": updated, "active_mass_kg": mass, "cost_per_motor": per_motor,
                "tooling": tooling or {}, "missing_prices": missing}
    return {"target_ref": "DIE-A/big", "target_mass_kg": target_mass,
            "productions": [prod("SMALL/a", 5.0, {"CNY": 1000.0}, {"CNY": 3000.0}),
                            prod("MID/b", 14.0, {"CNY": 3000.0}, None, missing=2),
                            prod("NOMASS/c", None, {"CNY": 1.0}, {"CNY": 1.0})],
            "estimates": [
                {"project_id": "older", "title": "Older", "updated_at": "2026-09-01", "iterations": [
                    {"index": 1, "bench": "dyno", "lines": {"testing": {"status": "filled", "amount": 111.0, "currency": "EUR"},
                                                            "shipping": {"status": "filled", "amount": 222.0, "currency": "EUR"}}}]},
                {"project_id": "other", "title": "Other", "updated_at": "2026-10-20", "iterations": [
                    {"index": 1, "bench": "propeller", "lines": {"testing": {"status": "filled", "amount": 900.0, "currency": "EUR"},
                                                                 "shipping": {"status": "filled", "amount": 420.0, "currency": "USD"}}},
                    {"index": 2, "bench": "dyno", "lines": {"testing": {"status": "filled", "amount": 1200.0, "currency": "EUR"},
                                                            "shipping": {"status": "missing", "amount": None, "currency": "EUR"}}}]}]}


def test_suggest_scales_the_nearest_analogs_per_motor_cost_by_active_mass(schema):
    est = _est(schema, iterations=2)
    new, report = CE.suggest(est, schema, _data(), today="2026-11-02")
    m1 = new["iterations"][0]["lines"]["manufacturing"]
    # nearest by active mass: MID/b (21/14 = 1.5) beats SMALL/a (21/5 = 4.2); NOMASS/c has no mass
    assert m1["source"] == "production" and m1["source_ref"]["production"] == "MID/b"
    assert m1["factor"] == 1.5 and m1["amount"] == 4500.0 and m1["currency"] == "CNY"
    assert m1["source_ref"]["analog_mass_kg"] == 14.0 and m1["source_ref"]["target_mass_kg"] == 21.0
    assert m1["by"] == "suggest" and m1["date"] == "2026-11-02"
    assert CE.origin_text(m1) == "from production MID/b, x1.5 by active mass"
    assert new["iterations"][1]["lines"]["manufacturing"]["amount"] == 4500.0       # every iteration, per motor
    assert CE.compute(new, schema)["iterations"][0]["manufacturing"] == {"CNY": 9000.0}
    assert report["analogs"]["manufacturing"] == {"ref": "MID/b", "factor": 1.5}
    assert any("MID/b has 2 unpriced" in n for n in report["notes"])


def test_suggest_tooling_is_the_analogs_tooling_sum_by_the_same_rule(schema):
    est = _est(schema, iterations=2)
    data = _data(target_mass=6.0)
    new, report = CE.suggest(est, schema, data, today="2026-11-02")
    t1 = new["iterations"][0]["lines"]["tooling"]
    # only SMALL/a has tooling (and a mass): 3000 x 6/5
    assert t1["source_ref"]["production"] == "SMALL/a" and t1["factor"] == 1.2 and t1["amount"] == 3600.0
    assert new["iterations"][1]["lines"]["tooling"]["pct"] == 20.0                    # later: still "modifications"
    # no tooling data anywhere: tooling stays missing, with the reason
    for p in data["productions"]:
        p["tooling"] = {}
    new, report = CE.suggest(est, schema, data, today="2026-11-02")
    t1 = new["iterations"][0]["lines"]["tooling"]
    assert t1["status"] == "missing" and "no built motor has tooling data" in t1["hint"]
    assert report["analogs"]["tooling"]["ref"] is None


def test_suggest_refuses_a_factor_outside_quarter_to_four(schema):
    est = _est(schema, iterations=1)
    new, _report = CE.suggest(est, schema, _data(target_mass=70.0), today="2026-11-02")
    m1 = new["iterations"][0]["lines"]["manufacturing"]
    # nearest is MID/b, 70/14 = 5: outside 0.25..4 -> missing, the note names the analog and its factor
    assert m1["status"] == "missing" and m1["amount"] is None
    assert m1["hint"] == "nearest analog MID/b is x5 by active mass, outside 0.25..4: enter it"
    out = CE.compute(new, schema)
    assert {"iteration": 1, "line": "manufacturing", "reason": m1["hint"]} in out["missing"]
    # exactly 4 is allowed; 1.0 kg -> SMALL/a 0.2 is refused too
    assert CE.pick_analog(_data()["productions"][:1], "cost_per_motor", 20.0, schema)[0]["factor"] == 4.0
    pick, why = CE.pick_analog(_data()["productions"], "cost_per_motor", 1.0, schema)
    assert pick is None and "SMALL/a is x0.2" in why
    # no simulated mass of the target
    pick, why = CE.pick_analog(_data()["productions"], "cost_per_motor", None, schema)
    assert pick is None and "no simulated active mass" in why


def test_suggest_never_overwrites_manual_or_converted_values(schema):
    est = _est(schema, iterations=2)
    _fill(est["iterations"][0]["lines"]["manufacturing"], 777.0)
    est["iterations"][0]["lines"]["shipping"].update({"status": "filled", "amount": 55.0, "currency": "EUR", "source": "converted"})
    est["iterations"][1]["lines"]["tooling"].update({"pct": 50.0, "source": "manual"})
    new, _ = CE.suggest(est, schema, _data(), today="2026-11-02")
    assert new["iterations"][0]["lines"]["manufacturing"] == est["iterations"][0]["lines"]["manufacturing"]
    assert new["iterations"][0]["lines"]["shipping"] == est["iterations"][0]["lines"]["shipping"]
    assert new["iterations"][1]["lines"]["tooling"]["pct"] == 50.0
    again, _ = CE.suggest(new, schema, _data(target_mass=14.0), today="2026-11-03", refresh=True)
    assert again["iterations"][0]["lines"]["manufacturing"]["amount"] == 777.0
    # refresh re-suggests an earlier suggestion (iteration 2 manufacturing: now x1.0)
    assert again["iterations"][1]["lines"]["manufacturing"]["amount"] == 3000.0
    # without refresh a filled suggestion is left alone
    same, _ = CE.suggest(new, schema, _data(target_mass=14.0), today="2026-11-03")
    assert same["iterations"][1]["lines"]["manufacturing"]["amount"] == 4500.0


def test_suggest_testing_and_shipping_come_from_other_estimates_as_is(schema):
    est = _est(schema, iterations=2)
    est["iterations"][0]["bench"] = "propeller"
    new, _ = CE.suggest(est, schema, _data(), today="2026-11-02")
    it1, it2 = (it["lines"] for it in new["iterations"])
    # the newest estimate ("other"), same iteration first
    assert it1["testing"]["amount"] == 900.0 and it1["testing"]["source"] == "estimate"
    assert it1["testing"]["source_ref"] == {"project": "other", "title": "Other", "iteration": "1"}
    assert it2["testing"]["amount"] == 1200.0
    assert it1["shipping"]["amount"] == 420.0 and it1["shipping"]["currency"] == "USD"
    assert it2["shipping"]["amount"] == 420.0                                       # iteration 2 of "other" is empty
    assert CE.origin_text(it1["testing"]) == "from estimate of project other (iteration 1)"
    # the own project's estimate is never its own source; no other estimate -> missing with the reason
    data = _data()
    data["estimates"] = [dict(data["estimates"][1], project_id="alpha")]
    new, _ = CE.suggest(est, schema, data, today="2026-11-02")
    assert new["iterations"][0]["lines"]["shipping"]["status"] == "missing"
    assert "no other project's estimate has shipping" in new["iterations"][0]["lines"]["shipping"]["hint"]


def test_suggest_leaves_a_multi_currency_analog_to_the_owner(schema):
    data = _data(target_mass=14.0)
    data["productions"][1]["cost_per_motor"] = {"CNY": 3000.0, "EUR": 50.0}
    new, _ = CE.suggest(_est(schema, iterations=1), schema, data, today="2026-11-02")
    line = new["iterations"][0]["lines"]["manufacturing"]
    assert line["status"] == "missing" and "several currencies (CNY 3000 + EUR 50)" in line["hint"]


def test_sources_read_the_production_cost_summary_never_the_margin(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "prod"))
    from motor_ai_sim.production import store as PS
    PS.save_file("SYN", "a", "production.json", {"die": "SYN", "config": "a", "updated_at": "2026-10-01"})
    PS.save_file("SYN", "a", "parts.json", {"parts": [{"id": "p1", "name": {"en": "Housing"}, "kind": "made"},
                                                      {"id": "p2", "name": {"en": "Shaft"}, "kind": "made"},
                                                      {"id": "t1", "name": {"en": "Mould"}, "kind": "made"}]})
    PS.save_file("SYN", "a", "bom.json", {"rows": [{"part_id": "p1", "qty_per_motor": 1}, {"part_id": "p2", "qty_per_motor": 1}]})
    PS.save_file("SYN", "a", "costs.json", {"rows": [{"part_id": "p1", "unit_price": 120, "currency": "CNY"}],
                                            "extras": [{"id": "e1", "name": {"en": "Assembly"}, "amount": 30, "currency": "CNY"}],
                                            "margin_pct": 40})
    PS.save_file("SYN", "a", "bom_tooling.json", {"rows": [{"part_id": "t1", "qty_per_motor": 2}]})
    PS.save_file("SYN", "a", "costs_tooling.json", {"rows": [{"part_id": "t1", "unit_price": 1000, "currency": "CNY"}]})
    prods = SRC.productions(mass_of=lambda d, c: 3.0)
    assert prods == [{"ref": "SYN/a", "title": "SYN a", "updated_at": "2026-10-01", "active_mass_kg": 3.0,
                      "cost_per_motor": {"CNY": 150.0}, "tooling": {"CNY": 2000.0}, "missing_prices": 1}]


# ── the old 68-line shape ───────────────────────────────────────────────────────────────────────────────────────────

def _v1(lines1, lines2=None, project_lines=None, **settings):
    def line(qty=None, unit_cost=None, currency="EUR", source="manual", derive=None, status="filled"):
        return {"status": status, "qty": qty, "unit": "lot", "unit_cost": unit_cost, "currency": currency, "source": source,
                "source_ref": None, "scale": None, "derive": derive, "date": "2026-11-02", "note": "", "by": "o@example.test"}
    s = {"complex": False, "iterations": 2, "motors_per_iteration": 2, "currency": "EUR", "contingency_pct": 15,
         "liquid_cooling": False, "high_voltage": False, "target_motor": {"die": "DIE-A", "config": "big"},
         "analogs": [], "basis_overrides": {}}
    s.update(settings)
    rule = line(source="rule", derive={"pct": 20.0, "from_iteration": 1})
    return {"version": 1, "schema_version": 1, "project_id": "alpha", "revision": 7, "settings": s,
            "iterations": [{"index": 1, "settings": {"bench": "both", "milestone_pct": 60},
                            "lines": {k: line(**v) for k, v in lines1.items()}},
                           {"index": 2, "settings": {"bench": "dyno", "milestone_pct": None},
                            "lines": {"tool_main": rule, "tool_stack": rule, "mech_cooling": line(status="not_applicable"),
                                      **{k: line(**v) for k, v in (lines2 or {}).items()}}}],
            "project_lines": {k: line(**v) for k, v in (project_lines or {}).items()}, "basis": {},
            "updated_at": "2026-11-02T08:00:00+00:00", "updated_by": "o@example.test"}


def test_old_shape_is_summed_into_the_four_lines(schema):
    old = _v1({"tool_main": {"qty": 1, "unit_cost": 8000}, "tool_stack": {"qty": 1, "unit_cost": 2000},
               "mech_shaft": {"qty": 2, "unit_cost": 100}, "mech_housing": {"qty": 2, "unit_cost": 300},
               "scrap_allowance": {"qty": 5},
               "test_bench": {"qty": 10, "unit_cost": 50}, "test_report": {"qty": 1, "unit_cost": 500},
               "log_factory_test": {"qty": 1, "unit_cost": 200, "currency": "EUR"},
               "log_duties": {"qty": 1, "unit_cost": 300, "currency": "CNY"}},
              {"mech_shaft": {"qty": 2, "unit_cost": 100}},
              project_lines={"proj_engineering": {"qty": 10, "unit_cost": 80}})
    est = CE.from_stored(old, schema, "alpha", today="2026-11-02")
    assert est["version"] == 2 and est["revision"] == 7
    assert est["settings"] == {"complex": False, "iterations": 2, "currency": "EUR", "contingency_pct": 15.0,
                               "target_motor": {"die": "DIE-A", "config": "big"}}
    l1, l2 = (it["lines"] for it in est["iterations"])
    assert est["iterations"][0]["bench"] == "both" and est["iterations"][0]["motors"] == 2
    assert l1["tooling"]["amount"] == 10000.0 and l1["tooling"]["source"] == "converted"
    # manufacturing: (200 + 600) + 5 % scrap = 840 for 2 motors -> 420 per motor
    assert l1["manufacturing"]["amount"] == 420.0 and l1["manufacturing"]["source"] == "converted"
    assert "over 2 motor(s)" in l1["manufacturing"]["note"]
    assert l1["testing"]["amount"] == 1000.0
    # shipping lines in EUR and CNY: no exchange rate -> missing, the old amounts in the hint
    assert l1["shipping"]["status"] == "missing" and "EUR 200 + CNY 300" in l1["shipping"]["hint"]
    # iteration 2: the old default "20 % of iteration 1" tooling stays a 20 % rule
    assert l2["tooling"]["pct"] == 20.0 and l2["tooling"]["source"] == "rule"
    assert l2["manufacturing"]["amount"] == 100.0
    assert l2["testing"]["status"] == "missing" and l2["shipping"]["status"] == "missing"
    assert any("proj_engineering: EUR 800" in d for d in est["migration"]["dropped"])
    assert any("payment milestone of iteration 1: 60 %" in d for d in est["migration"]["dropped"])
    out = CE.compute(est, schema)
    assert out["iterations"][0]["subtotal"] == {"EUR": 10000.0 + 840.0 + 1000.0}
    assert out["iterations"][1]["tooling"] == {"EUR": 2000.0}
    assert "converted from the old detailed estimate" in out["iterations"][0]["lines"]["tooling"]["origin"]


def test_old_shape_with_nothing_filled_starts_blank_and_garbage_never_crashes(schema):
    est = CE.from_stored(_v1({}, iterations=3, complex=True), schema, "alpha")
    assert est["settings"]["iterations"] == 3 and est["revision"] == 7
    assert all(it["lines"][lid]["status"] == "missing" for it in est["iterations"] for lid in ("manufacturing", "testing", "shipping"))
    assert est["iterations"][1]["lines"]["tooling"]["pct"] == 20.0
    assert "started blank" in est["migration"]["note"]
    for junk in ({"version": 1, "revision": 3, "settings": "x", "iterations": "y"}, {"version": 2, "revision": 4, "settings": []},
                 {"version": 1, "revision": 5, "iterations": [{"index": "a"}, None, {"index": 1, "lines": {"tool_main": "x"}}]}):
        est = CE.from_stored(junk, schema, "alpha")
        assert est["version"] == 2 and est["revision"] == junk["revision"]
        CE.compute(est, schema)


# ── diff ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_diff_names_what_changed(schema):
    a = _est(schema)
    b = json.loads(json.dumps(a))
    _fill(b["iterations"][0]["lines"]["tooling"], 500.0)
    b["settings"]["contingency_pct"] = 15
    b["iterations"][1]["motors"] = 3
    paths = {c["path"] for c in CE.diff(a, b, schema)}
    assert {"settings.contingency_pct", "iteration 1.tooling.amount", "iteration 1.tooling.status", "iteration 2.motors",
            "total.EUR"} <= paths
    assert CE.diff(a, a) == []


# ── routes: admin only ──────────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "production"))
    monkeypatch.setenv("MOTRES_MOTOR_TESTS_ROOT", str(tmp_path / "tests"))
    store.create_project({"id": "alpha", "title": "Alpha", "customer_org": "Org", "status": "quote", "created_at": "t",
                          "updated_at": "t", "owner_engineer": "eng@example.test",
                          "members": [{"email": "customer@example.test", "role": "owner"},
                                      {"email": "eng@example.test", "role": "engineer"},
                                      {"email": "viewer@example.test", "role": "customer"}],
                          "linked_motors": [{"die": "DIE-A", "config": "big"}], "notes": "", "pending_invites": []})
    monkeypatch.setattr(SRC, "active_mass", lambda die, config: None)
    app = FastAPI()
    app.include_router(R.router)
    return TestClient(app)


def _as(monkeypatch, user):
    monkeypatch.setattr(auth, "_is_admin_caller", lambda token: (bool(user and user.get("role") == "admin"), user))


URLS = [("get", "/api/admin/projects/alpha/cost-estimate", None),
        ("put", "/api/admin/projects/alpha/cost-estimate", {"revision": 0}),
        ("post", "/api/admin/projects/alpha/cost-estimate/suggest", {"revision": 0}),
        ("post", "/api/admin/projects/alpha/cost-estimate/snapshot", {"note": "x"}),
        ("get", "/api/admin/projects/alpha/cost-estimate/snapshots", None)]


@pytest.mark.parametrize("who", [
    {"email": "customer@example.test", "role": "user"},     # the project owner on the customer side
    {"email": "viewer@example.test", "role": "user"},       # a project customer / production viewer
    {"email": "oem@example.test", "role": "user"},          # the factory
    {"email": "eng@example.test", "role": "engineer"},      # a MOTRES engineer who is not an admin
])
def test_only_admins_reach_the_cost_estimate(client, monkeypatch, who):
    _as(monkeypatch, who)
    for method, url, body in URLS:
        resp = getattr(client, method)(url, **({"json": body} if body is not None else {}))
        assert resp.status_code == 403, (who, url)
    _as(monkeypatch, None)
    assert client.get(URLS[0][1]).status_code == 401


def test_an_mcp_agent_acting_for_an_admin_is_refused():
    with auth.use_agent_user({"email": "admin@example.test", "role": "admin"}):
        with pytest.raises(HTTPException) as exc:
            R._admin({"email": "admin@example.test", "role": "admin"})
    assert exc.value.status_code == 403
    assert R._admin({"email": "admin@example.test", "role": "admin"})["role"] == "admin"


def test_the_mcp_guard_refuses_cost_estimate_keys():
    from motor_ai_sim import mcp_tools as T
    for leak in ({"cost_estimate": {}}, {"cost_per_motor": 1.0}, {"contingency_pct": 10}, {"computed": {"contingency": {}}}):
        with pytest.raises(AssertionError, match="commercial field leaked"):
            T.assert_no_commercial(leak)


def test_admin_round_trip_revision_suggest_snapshot_and_audit(client, monkeypatch, tmp_path):
    _as(monkeypatch, {"email": "admin@example.test", "role": "admin"})
    got = client.get("/api/admin/projects/alpha/cost-estimate").json()
    assert got["stored"] is False and got["estimate"]["revision"] == 0 and got["estimate"]["version"] == 2
    assert got["estimate"]["settings"]["target_motor"] == {"die": "DIE-A", "config": "big"}
    assert got["schema"]["schema_version"] == 2 and got["computed"]["missing_count"] == 8
    assert client.get("/api/admin/projects/nope/cost-estimate").status_code == 404

    est = got["estimate"]
    _fill(est["iterations"][0]["lines"]["tooling"], 1000.0)
    saved = client.put("/api/admin/projects/alpha/cost-estimate", json=est)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["estimate"]["revision"] == 1 and body["estimate"]["updated_by"] == "admin@example.test"
    assert any(c["path"] == "iteration 1.tooling.amount" for c in body["changes"])
    assert body["computed"]["iterations"][1]["tooling"] == {"EUR": 200.0}
    stale = client.put("/api/admin/projects/alpha/cost-estimate", json=est)
    assert stale.status_code == 409 and stale.json()["detail"]["revision"] == 1
    bad = dict(body["estimate"]); bad["settings"] = {**bad["settings"], "currency": "GBP"}
    assert client.put("/api/admin/projects/alpha/cost-estimate", json=bad).status_code == 422

    first = client.post("/api/admin/projects/alpha/cost-estimate/snapshot", json={"note": "first"}).json()["snapshot"]
    assert first["n"] == 1 and first["diff"] == [] and first["totals"]["total"]["EUR"] == 1320.0

    sug = client.post("/api/admin/projects/alpha/cost-estimate/suggest", json={"revision": 1})
    assert sug.status_code == 200, sug.text
    sbody = sug.json()
    assert sbody["estimate"]["revision"] == 2 and sbody["report"]["analogs"]["manufacturing"]["ref"] is None
    assert sbody["estimate"]["iterations"][0]["lines"]["tooling"]["amount"] == 1000.0     # manual kept
    assert "no built motor" in sbody["estimate"]["iterations"][0]["lines"]["manufacturing"]["hint"]

    est2 = sbody["estimate"]
    est2["settings"]["contingency_pct"] = 20
    assert client.put("/api/admin/projects/alpha/cost-estimate", json=est2).status_code == 200
    second = client.post("/api/admin/projects/alpha/cost-estimate/snapshot", json={"note": "second", "revision": 3}).json()["snapshot"]
    assert second["n"] == 2 and second["parent"] == "0001"
    assert any(c["path"] == "settings.contingency_pct" and c["was"] == 10 and c["now"] == 20 for c in second["diff"])
    listed = client.get("/api/admin/projects/alpha/cost-estimate/snapshots").json()["snapshots"]
    assert [s["n"] for s in listed] == [2, 1] and "estimate" not in listed[0]

    audit = [json.loads(line) for line in
             (tmp_path / "projects" / "alpha" / "cost_estimate" / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [a["action"] for a in audit] == ["cost_estimate.update", "cost_estimate.snapshot", "cost_estimate.suggest",
                                            "cost_estimate.update", "cost_estimate.snapshot"]
    project_audit = tmp_path / "projects" / "alpha" / "audit.jsonl"
    assert not project_audit.exists() or "cost_estimate" not in project_audit.read_text(encoding="utf-8")


def test_a_stored_old_shape_estimate_is_converted_on_read_and_saved_as_the_new_shape(client, monkeypatch, tmp_path):
    _as(monkeypatch, {"email": "admin@example.test", "role": "admin"})
    path = tmp_path / "projects" / "alpha" / "cost_estimate" / "estimate.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    old = _v1({"tool_main": {"qty": 1, "unit_cost": 5000}, "test_bench": {"qty": 4, "unit_cost": 100}})
    path.write_text(json.dumps(old), encoding="utf-8")
    # an old-shape snapshot too
    snap_dir = path.parent / "snapshots"
    snap_dir.mkdir()
    (snap_dir / "0001.json").write_text(json.dumps({"id": "0001", "n": 1, "note": "old", "created_at": "t", "created_by": "o",
                                                    "revision": 7, "parent": None, "estimate": old, "totals": {}, "diff": []}),
                                        encoding="utf-8")
    got = client.get("/api/admin/projects/alpha/cost-estimate")
    assert got.status_code == 200, got.text
    est = got.json()["estimate"]
    assert got.json()["stored"] is True and est["revision"] == 7 and est["migration"]["from_version"] == 1
    assert est["iterations"][0]["lines"]["tooling"]["amount"] == 5000.0
    assert est["iterations"][0]["lines"]["testing"]["amount"] == 400.0
    # the file is untouched until the admin saves; the save writes the new shape on the same revision line
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1
    saved = client.put("/api/admin/projects/alpha/cost-estimate", json=est)
    assert saved.status_code == 200, saved.text
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["version"] == 2 and on_disk["revision"] == 8 and on_disk["migration"]["from_version"] == 1
    snap = client.post("/api/admin/projects/alpha/cost-estimate/snapshot", json={"note": "new"})
    assert snap.status_code == 200, snap.text
    assert snap.json()["snapshot"]["n"] == 2
    # suggest on a converted estimate keeps the converted numbers
    sug = client.post("/api/admin/projects/alpha/cost-estimate/suggest", json={"revision": 8}).json()
    assert sug["estimate"]["iterations"][0]["lines"]["tooling"]["amount"] == 5000.0
    assert sug["estimate"]["iterations"][0]["lines"]["tooling"]["source"] == "converted"


def test_the_project_routes_never_carry_the_estimate(client, monkeypatch):
    """The customer-facing project payload has no cost-estimate field (it lives in its own file, admin routes only)."""
    from motor_ai_sim.development.routes import router as project_router
    _as(monkeypatch, {"email": "admin@example.test", "role": "admin"})
    est = client.get("/api/admin/projects/alpha/cost-estimate").json()["estimate"]
    _fill(est["iterations"][0]["lines"]["tooling"], 1000.0)
    assert client.put("/api/admin/projects/alpha/cost-estimate", json=est).status_code == 200
    app = FastAPI(); app.include_router(project_router)
    monkeypatch.setattr(auth, "resolve_user", lambda token: {"email": "customer@example.test", "role": "user"})
    resp = TestClient(app).get("/api/projects/alpha", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    assert "cost" not in resp.text.lower() and "1000" not in resp.text
