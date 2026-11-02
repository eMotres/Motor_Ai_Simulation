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
from motor_ai_sim.development import cost_estimate_fx as FX
from motor_ai_sim.development import cost_estimate_routes as R
from motor_ai_sim.development import cost_estimate_sources as SRC
from motor_ai_sim.development import store


@pytest.fixture(autouse=True)
def no_network_for_rates(tmp_path, monkeypatch):
    """The ECB is never reached from a test: the fetch fails unless a test supplies a fixture, and the rate cache lives in tmp."""
    monkeypatch.setenv("MOTRES_FX_CACHE_DIR", str(tmp_path / "fx"))

    def offline(*_a, **_k):
        raise OSError("offline in tests")
    monkeypatch.setattr(FX, "fetch_text", offline)


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
    assert [f["id"] for f in schema["settings"]] == ["complex", "iterations", "contingency_pct"]
    assert [f["id"] for f in schema["iteration_fields"]] == ["motors", "bench"]
    # every amount is in EUR; USD / CNY exist for the final table and the rates only
    assert schema["currencies"] == ["EUR", "CNY", "USD"] and schema["default_currency"] == "EUR" and schema["line_currency"] == "EUR"
    assert schema["factor_limits"] == [0.25, 4.0]
    for entry in schema["lines"] + schema["settings"] + schema["iteration_fields"]:
        assert entry["label"]["en"] and entry["label"]["zh-CN"] and entry["help"]["en"] and entry["help"]["zh-CN"]
    est = CE.default_estimate(schema, "alpha")
    assert est["settings"] == {"complex": False, "iterations": 2, "currency": "EUR", "contingency_pct": 10.0,
                               "display_currency": None, "target_motor": None,
                               "ship_from": "CN Shanghai", "ship_to": None, "ship_margin_pct": 20.0}


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


# ── EUR only ────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_new_saves_take_eur_lines_only_and_old_lines_are_converted_on_read(schema):
    est = _est(schema, iterations=2)
    for cur in ("CNY", "USD"):
        bad = json.loads(json.dumps(est))
        bad["iterations"][0]["lines"]["shipping"].update({"amount": 5, "currency": cur})
        with pytest.raises(CE.EstimateError, match="currency: EUR only"):
            CE.normalize_estimate(bad, schema, "alpha")
    bad = json.loads(json.dumps(est))
    bad["settings"]["currency"] = "CNY"
    with pytest.raises(CE.EstimateError, match="currency: EUR only"):
        CE.normalize_estimate(bad, schema, "alpha")

    # stored data with CNY / USD lines (before this rule): EUR on read at the stored rate (1 EUR = 8 CNY = 1.25 USD)
    old = json.loads(json.dumps(est))
    old["settings"]["currency"] = "CNY"
    old["fx"] = CE.build_fx({"date": "2026-11-02", "rates": {"CNY": 8.0, "USD": 1.25}}, {}, None)
    _fill(old["iterations"][0]["lines"]["manufacturing"], 400.0, "CNY")
    old["iterations"][0]["lines"]["manufacturing"]["note"] = "quote"
    _fill(old["iterations"][0]["lines"]["testing"], 125.0, "USD")
    _fill(old["iterations"][0]["lines"]["shipping"], 90.0)
    got = CE.from_stored(old, schema, "alpha")
    l1 = got["iterations"][0]["lines"]
    assert got["settings"]["currency"] == "EUR"
    assert (l1["manufacturing"]["amount"], l1["manufacturing"]["currency"], l1["manufacturing"]["source"]) == (50.0, "EUR", "converted")
    assert l1["manufacturing"]["note"] == "quote; was CNY 400.00" and l1["manufacturing"]["status"] == "filled"
    assert (l1["testing"]["amount"], l1["testing"]["note"]) == (100.0, "was USD 125.00")
    assert (l1["shipping"]["amount"], l1["shipping"]["source"]) == (90.0, "manual")                  # already EUR: untouched
    assert all(ln["currency"] == "EUR" for it in got["iterations"] for ln in it["lines"].values())
    assert CE.normalize_estimate(got, schema, "alpha")["iterations"][0]["lines"]["testing"]["amount"] == 100.0     # saves again as it is

    # no stored rate: the cached ECB rate is used; with neither, the line is missing and the hint gives the old amount
    old["fx"] = None
    assert CE.from_stored(old, schema, "alpha", fallback_rates={"CNY": 10.0})["iterations"][0]["lines"]["manufacturing"]["amount"] == 40.0
    none = CE.from_stored(old, schema, "alpha")["iterations"][0]["lines"]["manufacturing"]
    assert none["status"] == "missing" and none["amount"] is None and none["currency"] == "EUR"
    assert none["hint"] == "was CNY 400.00: no exchange rate known, enter it in EUR"


# ── later-iteration tooling ─────────────────────────────────────────────────────────────────────────────────────────

def test_later_iterations_tooling_is_an_amount_like_iteration_one(schema):
    est = _est(schema, iterations=3)
    for it in est["iterations"]:
        t = it["lines"]["tooling"]
        assert t["status"] == "missing" and t["pct"] is None and t["source"] is None
    out = CE.compute(est, schema)
    assert out["iterations"][1]["tooling"] is None
    assert {"iteration": 2, "line": "tooling", "reason": "not filled"} in out["missing"]
    _fill(est["iterations"][0]["lines"]["tooling"], 10000.0)
    _fill(est["iterations"][1]["lines"]["tooling"], 1500.0)
    out = CE.compute(CE.normalize_estimate(est, schema, "alpha"), schema)
    assert out["iterations"][1]["tooling"] == {"EUR": 1500.0} and out["iterations"][2]["tooling"] is None


def test_an_old_stored_percent_rule_becomes_its_amount_on_read(schema):
    raw = _est(schema, iterations=3)
    _fill(raw["iterations"][0]["lines"]["tooling"], 10000.0)
    raw["iterations"][1]["lines"]["tooling"].update({"status": "filled", "amount": None, "pct": 20.0, "source": "rule", "currency": "EUR"})
    raw["iterations"][2]["lines"]["tooling"].update({"status": "filled", "amount": None, "pct": 35.0, "source": "manual", "currency": "EUR",
                                                      "note": "my note", "by": "owner@example.test", "date": "2026-11-02"})
    est = CE.from_stored(raw, schema, "alpha")
    t2, t3 = (est["iterations"][i]["lines"]["tooling"] for i in (1, 2))
    # 20 % / 35 % of iteration 1's tooling, source "converted"; the line has no % any more
    assert (t2["amount"], t2["currency"], t2["source"], t2["pct"], t2["status"]) == (2000.0, "EUR", "converted", None, "filled")
    assert (t3["amount"], t3["currency"], t3["source"], t3["pct"], t3["note"]) == (3500.0, "EUR", "converted", None, "my note")
    assert CE.compute(est, schema)["iterations"][1]["tooling"] == {"EUR": 2000.0}
    assert "converted" in CE.compute(est, schema)["iterations"][1]["lines"]["tooling"]["origin"]
    # iteration 1's tooling missing: the converted line is missing too (and reads like any missing line)
    raw["iterations"][0]["lines"]["tooling"].update({"status": "missing", "amount": None, "source": None})
    est = CE.from_stored(raw, schema, "alpha")
    t2 = est["iterations"][1]["lines"]["tooling"]
    assert t2["status"] == "missing" and t2["amount"] is None and t2["pct"] is None and t2["hint"] == "iteration 1 tooling missing"
    assert {"iteration": 2, "line": "tooling", "reason": "iteration 1 tooling missing"} in CE.compute(est, schema)["missing"]
    # saving it again keeps the amount (no % comes back)
    assert CE.normalize_estimate(est, schema, "alpha")["iterations"][1]["lines"]["tooling"] == t2


# ── totals ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_totals_per_iteration_and_currency_with_contingency(schema):
    est = _est(schema, iterations=2, contingency_pct=10)
    it1, it2 = est["iterations"]
    _fill(it1["lines"]["tooling"], 1000.0, "EUR")
    _fill(it1["lines"]["manufacturing"], 400.0, "CNY")          # per motor, 2 motors
    _fill(it1["lines"]["testing"], 500.0, "EUR")
    _fill(it1["lines"]["shipping"], 300.0, "USD")
    it2["motors"] = 3
    _fill(it2["lines"]["tooling"], 200.0, "EUR")
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
                                                                 "shipping": {"status": "filled", "amount": 420.0, "currency": "EUR"}}},
                    {"index": 2, "bench": "dyno", "lines": {"testing": {"status": "filled", "amount": 1200.0, "currency": "EUR"},
                                                            "shipping": {"status": "missing", "amount": None, "currency": "EUR"}}}]}]}


def _est_fx(schema, **kw):
    est = _est(schema, **kw)
    est["fx"] = CE.build_fx({"date": "2026-11-02", "rates": {"CNY": 8.0, "USD": 1.25}}, {}, None)
    return est


def test_suggest_scales_the_nearest_analogs_per_motor_cost_by_active_mass(schema):
    est = _est_fx(schema, iterations=2)
    new, report = CE.suggest(est, schema, _data(), today="2026-11-02")
    m1 = new["iterations"][0]["lines"]["manufacturing"]
    # nearest by active mass: MID/b (21/14 = 1.5) beats SMALL/a (21/5 = 4.2); NOMASS/c has no mass
    assert m1["source"] == "production" and m1["source_ref"]["production"] == "MID/b"
    # CNY 3000 x 1.5 = CNY 4500, shown in EUR at 8 CNY per EUR (every amount is in EUR)
    assert m1["factor"] == 1.5 and m1["amount"] == 562.5 and m1["currency"] == "EUR"
    assert m1["source_ref"]["analog_mass_kg"] == 14.0 and m1["source_ref"]["target_mass_kg"] == 21.0
    assert m1["by"] == "suggest" and m1["date"] == "2026-11-02"
    assert CE.origin_text(m1) == "from production MID/b, x1.5 by active mass"
    assert new["iterations"][1]["lines"]["manufacturing"]["amount"] == 562.5        # every iteration, per motor
    assert CE.compute(new, schema)["iterations"][0]["manufacturing"] == {"EUR": 1125.0}
    assert report["analogs"]["manufacturing"] == {"ref": "MID/b", "factor": 1.5}
    assert any("MID/b has 2 unpriced" in n for n in report["notes"])


def test_suggest_tooling_is_the_analogs_tooling_sum_by_the_same_rule(schema):
    est = _est_fx(schema, iterations=2)
    data = _data(target_mass=6.0)
    new, report = CE.suggest(est, schema, data, today="2026-11-02")
    t1 = new["iterations"][0]["lines"]["tooling"]
    # only SMALL/a has tooling (and a mass): 3000 x 6/5
    assert t1["source_ref"]["production"] == "SMALL/a" and t1["factor"] == 1.2 and t1["amount"] == 450.0 and t1["currency"] == "EUR"       # CNY 3600 / 8
    # a later iteration's tooling is not suggested (no % rule any more): it stays missing for the owner to enter
    t2 = new["iterations"][1]["lines"]["tooling"]
    assert t2["status"] == "missing" and t2["pct"] is None and "enter it" in t2["hint"]
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
    est = _est_fx(schema, iterations=2)
    _fill(est["iterations"][0]["lines"]["manufacturing"], 777.0)
    est["iterations"][0]["lines"]["shipping"].update({"status": "filled", "amount": 55.0, "currency": "EUR", "source": "converted"})
    _fill(est["iterations"][1]["lines"]["tooling"], 500.0)
    new, _ = CE.suggest(est, schema, _data(), today="2026-11-02")
    assert new["iterations"][0]["lines"]["manufacturing"] == est["iterations"][0]["lines"]["manufacturing"]
    assert new["iterations"][0]["lines"]["shipping"] == est["iterations"][0]["lines"]["shipping"]
    assert new["iterations"][1]["lines"]["tooling"]["amount"] == 500.0
    again, _ = CE.suggest(new, schema, _data(target_mass=14.0), today="2026-11-03", refresh=True)
    assert again["iterations"][0]["lines"]["manufacturing"]["amount"] == 777.0
    # refresh re-suggests an earlier suggestion (iteration 2 manufacturing: now x1.0)
    assert again["iterations"][1]["lines"]["manufacturing"]["amount"] == 375.0
    # without refresh a filled suggestion is left alone
    same, _ = CE.suggest(new, schema, _data(target_mass=14.0), today="2026-11-03")
    assert same["iterations"][1]["lines"]["manufacturing"]["amount"] == 562.5


def test_suggest_testing_and_shipping_come_from_other_estimates_as_is(schema):
    est = _est(schema, iterations=2)
    est["iterations"][0]["bench"] = "propeller"
    new, _ = CE.suggest(est, schema, _data(), today="2026-11-02")
    it1, it2 = (it["lines"] for it in new["iterations"])
    # the newest estimate ("other"), same iteration first
    assert it1["testing"]["amount"] == 900.0 and it1["testing"]["source"] == "estimate"
    assert it1["testing"]["source_ref"] == {"project": "other", "title": "Other", "iteration": "1"}
    assert it2["testing"]["amount"] == 1200.0
    assert it1["shipping"]["amount"] == 420.0 and it1["shipping"]["currency"] == "EUR"
    assert it2["shipping"]["amount"] == 420.0                                       # iteration 2 of "other" is empty
    assert CE.origin_text(it1["testing"]) == "from estimate of project other (iteration 1)"
    # the own project's estimate is never its own source; no other estimate -> missing with the reason
    data = _data()
    data["estimates"] = [dict(data["estimates"][1], project_id="alpha")]
    new, _ = CE.suggest(est, schema, data, today="2026-11-02")
    assert new["iterations"][0]["lines"]["shipping"]["status"] == "missing"
    assert "no other project's estimate has shipping" in new["iterations"][0]["lines"]["shipping"]["hint"]


def test_suggest_without_a_rate_leaves_a_cny_analog_to_the_owner(schema):
    new, _ = CE.suggest(_est(schema, iterations=1), schema, _data(target_mass=14.0), today="2026-11-02")      # no fx in the estimate
    line = new["iterations"][0]["lines"]["manufacturing"]
    assert line["status"] == "missing" and line["amount"] is None
    assert "priced in CNY and no exchange rate is known: enter it in EUR" in line["hint"]


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
                               "display_currency": None, "target_motor": {"die": "DIE-A", "config": "big"},
                               "ship_from": "CN Shanghai", "ship_to": None, "ship_margin_pct": 20.0}
    l1, l2 = (it["lines"] for it in est["iterations"])
    assert est["iterations"][0]["bench"] == "both" and est["iterations"][0]["motors"] == 2
    assert l1["tooling"]["amount"] == 10000.0 and l1["tooling"]["source"] == "converted"
    # manufacturing: (200 + 600) + 5 % scrap = 840 for 2 motors -> 420 per motor
    assert l1["manufacturing"]["amount"] == 420.0 and l1["manufacturing"]["source"] == "converted"
    assert "over 2 motor(s)" in l1["manufacturing"]["note"]
    assert l1["testing"]["amount"] == 1000.0
    # shipping lines in EUR and CNY: no exchange rate -> missing, the old amounts in the hint
    assert l1["shipping"]["status"] == "missing" and "EUR 200 + CNY 300" in l1["shipping"]["hint"]
    # iteration 2: the old default "20 % of iteration 1" tooling is now its amount (20 % of 10000), source converted
    assert l2["tooling"]["pct"] is None and l2["tooling"]["amount"] == 2000.0 and l2["tooling"]["source"] == "converted"
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
    assert est["iterations"][1]["lines"]["tooling"]["pct"] is None and est["iterations"][1]["lines"]["tooling"]["status"] == "missing"
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
        ("get", "/api/admin/projects/alpha/cost-estimate/snapshots", None),
        ("post", "/api/admin/projects/alpha/cost-estimate/suggestions", {"revision": 0})]


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
    _fill(est["iterations"][1]["lines"]["tooling"], 200.0)
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


# ── exchange rates and the display currency (owner 2026-11-02) ──────────────────────────────────────────────────────
# Made-up rates: 1 EUR = 8 CNY = 1.25 USD, so 800 CNY = 100 EUR and 125 USD = 100 EUR.

ECB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
<gesmes:subject>Reference rates</gesmes:subject>
<gesmes:Sender><gesmes:name>European Central Bank</gesmes:name></gesmes:Sender>
<Cube><Cube time='2026-11-02'><Cube currency='USD' rate='1.2500'/><Cube currency='JPY' rate='160.00'/><Cube currency='CNY' rate='8.0000'/></Cube></Cube>
</gesmes:Envelope>"""
ECB_XML_LATER = ECB_XML.replace("2026-11-02", "2026-11-03").replace("8.0000", "8.5000")
ECB = {"date": "2026-11-02", "rates": {"CNY": 8.0, "USD": 1.25}}


def _mixed(schema, fx):
    """Iteration 1: tooling EUR 1000, manufacturing CNY 400 per motor (2 motors = 800 CNY = 100 EUR), testing USD 125
    (= 100 EUR), shipping missing; iteration 2: tooling EUR 200, the rest missing."""
    est = _est(schema, iterations=2)
    lines = est["iterations"][0]["lines"]
    _fill(lines["tooling"], 1000.0)
    _fill(lines["manufacturing"], 400.0, "CNY")
    _fill(lines["testing"], 125.0, "USD")
    _fill(est["iterations"][1]["lines"]["tooling"], 200.0)
    est["fx"] = fx
    return est


def test_the_ecb_xml_is_parsed(schema):
    doc = FX.parse_ecb_xml(ECB_XML)
    assert doc == {"date": "2026-11-02", "rates": {"CNY": 8.0, "USD": 1.25}}      # JPY is in the feed and ignored
    with pytest.raises(FX.FxError, match="DTD"):
        FX.parse_ecb_xml('<!DOCTYPE x [<!ENTITY a "b">]>' + ECB_XML)
    with pytest.raises(FX.FxError, match="CNY"):
        FX.parse_ecb_xml(ECB_XML.replace("<Cube currency='CNY' rate='8.0000'/>", ""))
    with pytest.raises(FX.FxError, match="date"):
        FX.parse_ecb_xml(ECB_XML.replace("time='2026-11-02'", ""))
    with pytest.raises(FX.FxError):
        FX.parse_ecb_xml("not xml")


def test_totals_are_converted_to_the_display_currency_at_the_given_rates(schema):
    est = _mixed(schema, CE.build_fx(ECB, {}, None))
    assert est["fx"]["source"] == "ECB" and est["fx"]["date"] == "2026-11-02" and est["fx"]["rates"] == {"CNY": 8.0, "USD": 1.25}
    c = CE.compute(est, schema, "EUR")
    cv = c["converted"]
    assert cv["currency"] == "EUR" and cv["available"] is True and cv["missing_rates"] == []
    assert cv["iterations"][0]["lines"] == {"tooling": 1000.0, "manufacturing": 100.0, "testing": 100.0, "shipping": None}
    assert cv["iterations"][0]["subtotal"] == 1200.0 and cv["iterations"][1]["subtotal"] == 200.0
    assert (cv["subtotal"], cv["contingency"], cv["total"]) == (1400.0, 140.0, 1540.0)
    # the entered amounts and the per-currency totals are untouched
    assert c["iterations"][0]["manufacturing"] == {"CNY": 800.0}
    assert c["total"] == {"EUR": 1320.0, "CNY": 880.0, "USD": 137.5}
    cny = CE.compute(est, schema, "CNY")["converted"]
    assert cny["currency"] == "CNY" and cny["total"] == 12320.0 and cny["iterations"][0]["lines"]["manufacturing"] == 800.0
    usd = CE.compute(est, schema, "USD")["converted"]
    assert usd["total"] == 1925.0 and usd["iterations"][0]["lines"]["testing"] == 125.0
    # the owner's own choice in the estimate wins over the UI language's; without either the schema default (EUR) is used
    assert CE.compute(est, schema)["converted"]["currency"] == "EUR"
    est["settings"]["display_currency"] = "USD"
    assert CE.compute(est, schema)["converted"]["currency"] == "USD"
    assert CE.compute(est, schema, "CNY")["converted"]["currency"] == "USD"
    est["settings"]["display_currency"] = None
    with pytest.raises(CE.EstimateError):
        CE.compute(est, schema, "GBP")


def test_without_rates_the_totals_stay_per_currency(schema):
    est = _mixed(schema, None)
    c = CE.compute(est, schema, "EUR")
    cv = c["converted"]
    assert cv["available"] is False and cv["missing_rates"] == ["CNY", "USD"]
    assert cv["total"] is None and cv["subtotal"] is None and cv["iterations"][0]["subtotal"] is None
    assert cv["iterations"][0]["lines"]["tooling"] == 1000.0 and cv["iterations"][0]["lines"]["manufacturing"] is None
    assert c["total"] == {"EUR": 1320.0, "CNY": 880.0, "USD": 137.5} and c["currencies"] == ["EUR", "CNY", "USD"]
    assert any("several currencies" in w for w in c["warnings"])
    # a single currency needs no rate at all, in its own currency
    one = _est(schema, iterations=1)
    _fill(one["iterations"][0]["lines"]["tooling"], 100.0, "CNY")
    assert CE.compute(one, schema, "CNY")["converted"]["total"] == 110.0
    assert CE.compute(one, schema, "EUR")["converted"]["available"] is False       # CNY -> EUR needs the CNY rate
    eur = _est(schema, iterations=1)
    _fill(eur["iterations"][0]["lines"]["tooling"], 100.0)
    assert CE.compute(eur, schema, "EUR")["converted"]["total"] == 110.0
    # a rate that is missing for ONE currency still converts the others and names the one it lacks
    part = _mixed(schema, CE.build_fx(None, {"CNY": 8.0}, "2026-11-02"))
    cvp = CE.compute(part, schema, "EUR")["converted"]
    assert cvp["available"] is False and cvp["missing_rates"] == ["USD"] and cvp["iterations"][0]["lines"]["manufacturing"] == 100.0


def test_a_manual_rate_wins_over_the_ecb_one_and_is_labelled(schema):
    fx = CE.merge_fx(None, {"CNY": 7.5}, ECB, schema, "2026-11-05")
    assert fx["source"] == "manual" and fx["date"] == "2026-11-05" and fx["manual"] == {"CNY": 7.5}
    assert fx["rates"] == {"CNY": 7.5, "USD": 1.25}                      # USD stays the ECB one
    assert fx["ecb"] == {"date": "2026-11-02", "rates": {"CNY": 8.0, "USD": 1.25}}      # the ECB rates are kept underneath
    est = _mixed(schema, fx)
    assert CE.compute(est, schema, "EUR")["converted"]["iterations"][0]["lines"]["manufacturing"] == round(800 / 7.5, 2)
    # a newer ECB reference does not touch the manual rate (and keeps its date); removing it falls back to the ECB rate
    newer = {"date": "2026-11-03", "rates": {"CNY": 8.5, "USD": 1.3}}
    again = CE.merge_fx(fx, {"CNY": 7.5}, newer, schema, "2026-11-09")
    assert again["rates"] == {"CNY": 7.5, "USD": 1.3} and again["manual_date"] == "2026-11-05" and again["ecb"]["date"] == "2026-11-03"
    gone = CE.merge_fx(again, {}, newer, schema, "2026-11-09")
    assert gone["source"] == "ECB" and gone["rates"] == {"CNY": 8.5, "USD": 1.3} and gone["manual"] == {} and gone["date"] == "2026-11-03"
    # the stored document round-trips through validation; a bad rate is refused
    assert CE.normalize_fx(fx, schema) == fx
    for bad in ({"manual": {"CNY": 0}}, {"manual": {"CNY": -1}}, {"manual": {"GBP": 1.1}}, {"manual": {"CNY": "x"}}, {"base": "USD"}):
        with pytest.raises(CE.EstimateError):
            CE.normalize_fx(bad, schema)
    # nothing known -> no fx (a rate is never invented)
    assert CE.merge_fx(None, {}, None, schema, "2026-11-05") is None
    # an older live answer never replaces a newer stored ECB rate
    assert CE.merge_fx(gone, {}, ECB, schema, "2026-11-09")["ecb"]["date"] == "2026-11-03"


def test_the_ecb_rates_are_cached_for_a_day_and_the_last_ones_are_kept_when_it_fails(monkeypatch):
    import datetime
    calls = []

    def fetch(*_a, **_k):
        calls.append(1)
        return ECB_XML
    monkeypatch.setattr(FX, "fetch_text", fetch)
    t0 = datetime.datetime(2026, 11, 2, 12, 0, tzinfo=datetime.timezone.utc)
    doc, status = FX.live_rates(t0)
    assert doc == ECB and status["state"] == "ok" and len(calls) == 1
    assert FX.cache_path().is_file()
    doc, status = FX.live_rates(t0 + datetime.timedelta(hours=5))
    assert doc == ECB and status["state"] == "ok" and len(calls) == 1             # cached: no second fetch

    # a day later the fetch is tried again; it fails -> the last rates are kept and the answer says so
    def broken(*_a, **_k):
        calls.append(1)
        raise OSError("network down")
    monkeypatch.setattr(FX, "fetch_text", broken)
    day2 = t0 + datetime.timedelta(hours=26)
    doc, status = FX.live_rates(day2)
    assert doc == ECB and status["state"] == "stale" and status["date"] == "2026-11-02" and "network down" in status["error"]
    assert len(calls) == 2
    FX.live_rates(day2 + datetime.timedelta(minutes=5))
    assert len(calls) == 2                                                         # a failed fetch is not retried at once
    monkeypatch.setattr(FX, "fetch_text", lambda *_a, **_k: ECB_XML_LATER)
    doc, status = FX.live_rates(day2 + datetime.timedelta(hours=1))
    assert doc["date"] == "2026-11-03" and doc["rates"]["CNY"] == 8.5 and status["state"] == "ok"


def test_with_nothing_cached_and_no_network_no_rate_is_known():
    doc, status = FX.live_rates()
    assert doc is None and status["state"] == "none" and "offline" in status["error"]


def _age_cache(days):
    import datetime
    cache = FX.cache_path()
    doc = json.loads(cache.read_text(encoding="utf-8"))
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat(timespec="seconds")
    doc["fetched_at"] = doc["last_attempt"] = old
    cache.write_text(json.dumps(doc), encoding="utf-8")


def test_routes_store_the_rates_in_the_estimate_and_a_snapshot_keeps_them(client, monkeypatch, tmp_path):
    _as(monkeypatch, {"email": "admin@example.test", "role": "admin"})
    url = "/api/admin/projects/alpha/cost-estimate"
    got = client.get(url + "?display_currency=EUR").json()                       # offline: no rate
    assert got["estimate"]["fx"] is None and got["fx_status"]["state"] == "none"
    assert got["computed"]["converted"]["currency"] == "EUR" and got["computed"]["converted"]["available"] is True   # all EUR

    assert FX.cache_path().is_file()                                             # the failed attempt is remembered (no retry for 15 min)
    FX.cache_path().unlink()
    monkeypatch.setattr(FX, "fetch_text", lambda *_a, **_k: ECB_XML)
    est = got["estimate"]
    _fill(est["iterations"][0]["lines"]["tooling"], 1000.0)
    _fill(est["iterations"][0]["lines"]["manufacturing"], 400.0)
    _fill(est["iterations"][1]["lines"]["tooling"], 200.0)
    cny_line = json.loads(json.dumps(est))
    cny_line["iterations"][0]["lines"]["shipping"].update({"amount": 5.0, "currency": "CNY"})
    refused = client.put(url + "?display_currency=EUR", json=cny_line)                 # a new save in another currency is refused
    assert refused.status_code == 422 and "EUR only" in refused.text
    body = client.put(url + "?display_currency=EUR", json=est).json()
    assert body["estimate"]["fx"]["source"] == "ECB" and body["estimate"]["fx"]["rates"] == {"CNY": 8.0, "USD": 1.25}
    assert body["fx_status"] == {"state": "ok", "date": "2026-11-02", "manual": [], "error": None}
    assert body["computed"]["converted"]["iterations"][0]["lines"]["manufacturing"] == 800.0
    assert body["computed"]["converted"]["total"] == 2200.0 and body["computed"]["total"] == {"EUR": 2200.0}
    cny = client.get(url + "?display_currency=CNY").json()["computed"]["converted"]
    assert cny["currency"] == "CNY" and cny["iterations"][0]["lines"]["tooling"] == 8000.0
    assert client.get(url + "?display_currency=GBP").status_code == 422

    # a client cannot push its own ECB numbers: only the manual rate is taken from the request
    forged = body["estimate"]
    forged["fx"] = {**forged["fx"], "rates": {"CNY": 99.0, "USD": 99.0}, "ecb": {"date": "2026-11-02", "rates": {"CNY": 99.0, "USD": 99.0}},
                    "manual": {"CNY": 7.5}}
    man = client.put(url + "?display_currency=EUR", json=forged).json()
    assert man["estimate"]["fx"]["rates"] == {"CNY": 7.5, "USD": 1.25} and man["estimate"]["fx"]["source"] == "manual"
    assert man["fx_status"]["manual"] == ["CNY"]
    assert client.get(url + "?display_currency=CNY").json()["computed"]["converted"]["iterations"][0]["lines"]["tooling"] == 7500.0

    # the snapshot freezes the numbers with the rates (and the display currency) they were computed with
    snap = client.post(url + "/snapshot", json={"note": "frozen", "display_currency": "EUR"}).json()["snapshot"]
    assert snap["estimate"]["fx"]["rates"] == {"CNY": 7.5, "USD": 1.25} and snap["estimate"]["fx"]["source"] == "manual"
    assert snap["totals"]["converted"]["currency"] == "EUR" and snap["totals"]["converted"]["total"] == man["computed"]["converted"]["total"]

    # the ECB moves on and the owner drops the manual rate: the live estimate follows, the snapshot does not
    monkeypatch.setattr(FX, "fetch_text", lambda *_a, **_k: ECB_XML_LATER)
    _age_cache(2)
    later = man["estimate"]
    later["fx"] = {**later["fx"], "manual": {}}
    moved = client.put(url + "?display_currency=EUR", json=later).json()
    assert moved["estimate"]["fx"]["rates"]["CNY"] == 8.5 and moved["estimate"]["fx"]["source"] == "ECB"
    assert moved["estimate"]["fx"]["date"] == "2026-11-03"
    snaps = client.get(url + "/snapshots").json()["snapshots"]
    stored_snap = json.loads((tmp_path / "projects" / "alpha" / "cost_estimate" / "snapshots" / "0001.json").read_text(encoding="utf-8"))
    assert stored_snap["estimate"]["fx"]["rates"]["CNY"] == 7.5
    assert stored_snap["totals"]["converted"]["total"] == snap["totals"]["converted"]["total"]
    assert "estimate" not in snaps[0]

    # the ECB breaks: the last stored rates stay, and the answer says so
    _age_cache(2)

    def down(*_a, **_k):
        raise OSError("ECB down")
    monkeypatch.setattr(FX, "fetch_text", down)
    stale = client.get(url + "?display_currency=EUR").json()
    assert stale["fx_status"]["state"] == "stale" and stale["fx_status"]["date"] == "2026-11-03"
    assert "ECB down" in stale["fx_status"]["error"]
    assert stale["estimate"]["fx"]["rates"]["CNY"] == 8.5 and stale["computed"]["converted"]["available"] is True


def test_the_mcp_guard_refuses_the_new_exchange_rate_keys():
    from motor_ai_sim import mcp_tools as T
    for leak in ({"fx": {}}, {"fx_status": "ok"}, {"display_currency": "EUR"}, {"converted": {}}, {"rates": {"CNY": 8}},
                 {"computed": {"converted": {"total": 1.0}}}, {"ecb_date": "2026-11-02"}, {"exchange_rate": 8.0}):
        with pytest.raises(AssertionError, match="commercial field leaked"):
            T.assert_no_commercial(leak)
    T.assert_no_commercial({"fx_N": 1.0, "force_x": 2.0, "heat_exchanger": 3})       # physics keys are not caught


def test_the_display_currency_setting_is_validated(schema):
    est = _est(schema, iterations=1)
    est["settings"]["display_currency"] = "cny"
    assert CE.normalize_estimate(est, schema, "alpha")["settings"]["display_currency"] == "CNY"
    est["settings"]["display_currency"] = ""
    assert CE.normalize_estimate(est, schema, "alpha")["settings"]["display_currency"] is None
    est["settings"]["display_currency"] = "GBP"
    with pytest.raises(CE.EstimateError):
        CE.normalize_estimate(est, schema, "alpha")
    assert schema["display_currency_by_locale"] == {"en": "EUR", "zh-CN": "EUR"}


# ── durations (owner 2026-11-02: «обязательно указывать время выполнения каждого этапа») ───────────────────────────────

def test_durations_run_in_sequence_the_owner_may_override_and_the_project_is_the_sum(schema):
    est = _est(schema, iterations=2)
    for lid, w in (("tooling", 6), ("manufacturing", 4), ("testing", 2), ("shipping", 3)):
        est["iterations"][0]["lines"][lid]["weeks"] = w
    est["iterations"][1]["lines"]["manufacturing"]["weeks"] = 5
    est["iterations"][1]["bench"] = "none"
    est["iterations"][1]["lines"]["testing"]["weeks"] = 9                    # no bench: testing does not count
    est = CE.normalize_estimate(est, schema, "alpha")
    out = CE.compute(est, schema)
    w1, w2 = (it["weeks"] for it in out["iterations"])
    assert (w1["sum"], w1["override"], w1["total"], w1["missing"]) == (15.0, None, 15.0, [])
    assert (w2["sum"], w2["total"], w2["testing"], w2["missing"]) == (5.0, 5.0, None, ["tooling", "shipping"])
    assert out["weeks_total"] == 20.0
    assert {"iteration": 2, "line": "tooling"} in out["weeks_missing"]
    # the owner overrides iteration 2 (e.g. stages overlap): the project follows
    est["iterations"][1]["weeks_override"] = 8
    out = CE.compute(CE.normalize_estimate(est, schema, "alpha"), schema)
    assert out["iterations"][1]["weeks"]["total"] == 8.0 and out["iterations"][1]["weeks"]["sum"] == 5.0
    assert out["weeks_total"] == 23.0
    # a change of a duration is named by the diff
    b = json.loads(json.dumps(est))
    b["iterations"][0]["lines"]["tooling"]["weeks"] = 7
    assert "iteration 1.tooling.weeks" in {c["path"] for c in CE.diff(est, b)}


def test_durations_are_validated_0_to_104_weeks_per_line(schema):
    est = _est(schema, iterations=1)
    for bad_value in (-1, 105, "soon", float("inf")):
        bad = json.loads(json.dumps(est))
        bad["iterations"][0]["lines"]["tooling"]["weeks"] = bad_value
        with pytest.raises(CE.EstimateError, match="weeks"):
            CE.normalize_estimate(bad, schema, "alpha")
    ok = json.loads(json.dumps(est))
    ok["iterations"][0]["lines"]["tooling"]["weeks"] = 104
    ok["iterations"][0]["lines"]["shipping"]["weeks"] = ""                  # cleared -> no duration
    ok["iterations"][0]["weeks_override"] = 0
    got = CE.normalize_estimate(ok, schema, "alpha")
    assert got["iterations"][0]["lines"]["tooling"]["weeks"] == 104.0 and got["iterations"][0]["lines"]["shipping"]["weeks"] is None
    assert got["iterations"][0]["weeks_override"] == 0.0
    bad = json.loads(json.dumps(est))
    bad["iterations"][0]["weeks_override"] = 500
    with pytest.raises(CE.EstimateError, match="weeks_override"):
        CE.normalize_estimate(bad, schema, "alpha")


def test_an_old_estimate_without_durations_or_lanes_still_loads(schema):
    old = _est(schema, iterations=2)
    for it in old["iterations"]:
        it.pop("weeks_override")
        for line in it["lines"].values():
            line.pop("weeks")
    old.pop("dhl_lanes")
    for k in ("ship_from", "ship_to", "ship_margin_pct"):
        old["settings"].pop(k)
    _fill(old["iterations"][0]["lines"]["tooling"], 1000.0)
    got = CE.from_stored(old, schema, "alpha")
    assert got["iterations"][0]["lines"]["tooling"]["amount"] == 1000.0
    assert got["iterations"][0]["lines"]["tooling"]["weeks"] is None and got["iterations"][0]["weeks_override"] is None
    assert got["settings"]["ship_from"] == "CN Shanghai" and got["settings"]["ship_to"] is None and got["settings"]["ship_margin_pct"] == 20.0
    assert [ln["to_country"] for ln in got["dhl_lanes"]] == ["AR"]          # the seeded quote
    out = CE.compute(got, schema)
    assert out["weeks_total"] == 0.0 and len(out["weeks_missing"]) == 8
    # the owner removed every lane: an empty table stays empty
    got["dhl_lanes"] = []
    assert CE.normalize_estimate(got, schema, "alpha")["dhl_lanes"] == []


# ── shipping (DHL), suggested by us ─────────────────────────────────────────────────────────────────────────────────

SEED = {"from": "CN Shanghai", "to_country": "AR", "mode": "DHL Air Freight (Global Forwarding)", "quoted_on": "2026-11-02",
        "weight_kg": 150.0, "price": 20169.14, "currency": "CNY", "transit_days": 16, "source": "dhl.com get-a-quote"}


def _ship(schema, iterations=1, motors=2, lanes=None, **settings):
    est = _est_fx(schema, iterations=iterations)
    est["settings"].update({"ship_to": {"country": "AR", "city": "Buenos Aires"}, **settings})
    for it in est["iterations"]:
        it["motors"] = motors
    if lanes is not None:
        est["dhl_lanes"] = lanes
    return CE.normalize_estimate(est, schema, "alpha")


def test_the_schema_seeds_the_dated_dhl_lane(schema):
    lanes = CE.default_estimate(schema, "alpha")["dhl_lanes"]
    assert len(lanes) == 1
    assert {k: lanes[0][k] for k in SEED} == SEED
    assert schema["shipping"]["housing_factor"] == 1.3 and schema["shipping"]["crate_kg"] == 15
    assert schema["shipping"]["round_to_kg"] == 5 and schema["shipping"]["customs_weeks"] == 1
    assert "VAT" in schema["shipping"]["excluded"]["en"]


def test_the_shipping_weight_is_motors_times_housed_motor_plus_crate_rounded_up_to_5_kg(schema):
    assert CE.shipping_weight(2, 20.0, schema) == 85.0                       # 2 x (26 + 15) = 82 -> 85
    assert CE.shipping_weight(1, 50.0, schema) == 80.0                       # 65 + 15 = 80 exactly
    assert CE.shipping_weight(3, 1.0, schema) == 50.0                        # 3 x 16.3 = 48.9 -> 50
    # the motor mass: the complete mass when known, else the active mass x 1.3
    assert CE.motor_mass_for_shipping(42.0, True, 20.0, schema) == (42.0, "complete")
    assert CE.motor_mass_for_shipping(20.0, False, 20.0, schema) == (26.0, "active x 1.3")
    assert CE.motor_mass_for_shipping(None, False, None, schema) == (None, "")


def test_dhl_scales_the_nearest_quote_per_kg_adds_the_margin_and_converts_to_eur(schema):
    est = _ship(schema)                                                     # seeded lane only, 1 EUR = 8 CNY
    sug, why = CE.dhl_suggestion(est, est["iterations"][0], schema, 20.0, "complete")
    assert why == ""
    d = sug["source_ref"]["dhl"]
    assert d["weight_kg"] == 85.0 and d["lane_weight_kg"] == 150.0 and d["margin_pct"] == 20.0
    cny = 20169.14 / 150 * 85 * 1.2
    assert d["price"] == round(cny, 2) and d["currency"] == "CNY"
    assert sug["amount"] == round(cny / 8.0, 2)
    assert sug["weeks"] == 4.0                                              # 16 days -> 3 weeks + 1 week customs
    line = {"status": "filled", "source": "dhl", "source_ref": sug["source_ref"]}
    assert CE.origin_text(line) == "DHL quote 2026-11-02, CN Shanghai→AR Buenos Aires, 85 kg, +20 %"
    # the margin is a setting; the nearest quoted weight wins (a 50 kg quote for a 65 kg shipment)
    small = dict(SEED, weight_kg=50.0, price=9000.0, transit_days=10, quoted_on="2026-11-01")
    est = _ship(schema, lanes=[SEED, small], ship_margin_pct=10)
    sug, _ = CE.dhl_suggestion(est, est["iterations"][0], schema, 13.0, "active x 1.3")   # 2 x (16.9 + 15) = 63.8 -> 65
    assert sug["source_ref"]["dhl"]["weight_kg"] == 65.0 and sug["source_ref"]["dhl"]["lane_weight_kg"] == 50.0
    assert sug["amount"] == round(9000.0 / 50 * 65 * 1.1 / 8.0, 2) and sug["weeks"] == 3.0
    # a EUR quote needs no rate
    eur = dict(SEED, currency="EUR", price=1500.0)
    est = _ship(schema, lanes=[eur])
    est["fx"] = None
    sug, _ = CE.dhl_suggestion(est, est["iterations"][0], schema, 20.0)
    assert sug["amount"] == round(1500.0 / 150 * 85 * 1.2, 2)


def test_dhl_without_a_lane_destination_mass_or_rate_is_missing_with_the_reason(schema):
    est = _ship(schema, ship_to={"country": "DE", "city": "Munich"})
    assert CE.dhl_suggestion(est, est["iterations"][0], schema, 20.0) == (None, "no DHL quote for DE; add one")
    est = _ship(schema, ship_from="CN Ningbo")                                # same country, another origin: no quote
    assert CE.dhl_suggestion(est, est["iterations"][0], schema, 20.0)[1] == "no DHL quote for AR; add one"
    est = _ship(schema, ship_to=None)
    assert "ship to is missing" in CE.dhl_suggestion(est, est["iterations"][0], schema, 20.0)[1]
    est = _ship(schema)
    assert "no known mass" in CE.dhl_suggestion(est, est["iterations"][0], schema, None)[1]
    est["fx"] = None
    assert "no exchange rate" in CE.dhl_suggestion(est, est["iterations"][0], schema, 20.0)[1]


def test_suggest_fills_shipping_from_dhl_and_an_empty_duration_but_never_the_owners_numbers(schema):
    est = _ship(schema, iterations=2)
    est["iterations"][1]["lines"]["shipping"]["weeks"] = 6                  # the owner's duration stays
    data = dict(_data(), motor_mass_kg=20.0, motor_mass_basis="complete")
    new, report = CE.suggest(est, schema, data, today="2026-11-02")
    s1, s2 = (it["lines"]["shipping"] for it in new["iterations"])
    assert s1["source"] == "dhl" and s1["amount"] == round(20169.14 / 150 * 85 * 1.2 / 8.0, 2) and s1["weeks"] == 4.0
    assert s1["by"] == "suggest" and s1["source_ref"]["dhl"]["weight_kg"] == 85.0
    assert s2["source"] == "dhl" and s2["weeks"] == 6.0
    assert report["weeks_filled"] == [{"iteration": 1, "line": "shipping", "weeks": 4.0}]
    assert CE.normalize_estimate(new, schema, "alpha")["iterations"][0]["lines"]["shipping"] == s1     # saves as it is
    # a manual shipping amount is kept; no lane -> missing with the hint
    _fill(est["iterations"][0]["lines"]["shipping"], 999.0)
    est["settings"]["ship_to"] = {"country": "BR", "city": ""}
    new, _ = CE.suggest(est, schema, data, today="2026-11-02")
    assert new["iterations"][0]["lines"]["shipping"]["amount"] == 999.0
    assert new["iterations"][1]["lines"]["shipping"]["hint"] == "no DHL quote for BR; add one"
    # with refresh an earlier DHL suggestion follows a new margin
    est = _ship(schema)
    first, _ = CE.suggest(est, schema, data, today="2026-11-02")
    first["settings"]["ship_margin_pct"] = 0
    again, _ = CE.suggest(first, schema, data, today="2026-11-03", refresh=True)
    assert again["iterations"][0]["lines"]["shipping"]["amount"] == round(20169.14 / 150 * 85 / 8.0, 2)


def test_suggestion_cells_are_only_for_empty_cells(schema):
    est = _ship(schema, iterations=1)
    _fill(est["iterations"][0]["lines"]["tooling"], 100.0)
    data = dict(_data(), motor_mass_kg=20.0, motor_mass_basis="complete")
    after, _ = CE.suggest(est, schema, data, today="2026-11-02")
    cells = CE.suggestion_cells(est, after)["1"]
    assert "tooling" not in cells                                           # filled by the owner: no placeholder
    assert cells["shipping"]["source"] == "dhl" and cells["shipping"]["weeks"] == 4.0
    assert cells["shipping"]["origin"].startswith("DHL quote 2026-11-02, CN Shanghai")
    assert cells["manufacturing"]["source"] == "production"
    assert est["iterations"][0]["lines"]["shipping"]["status"] == "missing"     # nothing is written


def test_the_lane_table_is_validated(schema):
    est = _ship(schema)
    good = dict(SEED, to_country="de", weight_kg="300", transit_days="")
    lanes = CE.normalize_estimate({**est, "dhl_lanes": [SEED, good]}, schema, "alpha")["dhl_lanes"]
    assert lanes[1]["to_country"] == "DE" and lanes[1]["weight_kg"] == 300.0 and lanes[1]["transit_days"] is None
    edited = CE.normalize_estimate({**est, "dhl_lanes": [dict(SEED, price=21000.0)]}, schema, "alpha")["dhl_lanes"]
    assert edited[0]["price"] == 21000.0
    for bad, match in ((dict(SEED, to_country="Argentina"), "to_country"), ({**SEED, "from": ""}, "from"),
                       (dict(SEED, quoted_on="02.11.2026"), "quoted_on"), (dict(SEED, weight_kg=0), "weight_kg"),
                       (dict(SEED, price=-1), "price"), (dict(SEED, currency="GBP"), "currency"),
                       (dict(SEED, transit_days=400), "transit_days"), ("lane", "object")):
        with pytest.raises(CE.EstimateError, match=match):
            CE.normalize_estimate({**est, "dhl_lanes": [bad]}, schema, "alpha")
    with pytest.raises(CE.EstimateError, match="at most"):
        CE.normalize_estimate({**est, "dhl_lanes": [SEED] * 51}, schema, "alpha")
    with pytest.raises(CE.EstimateError, match="ship_to"):
        CE.normalize_estimate({**est, "settings": {**est["settings"], "ship_to": {"country": "Argentina"}}}, schema, "alpha")
    with pytest.raises(CE.EstimateError, match="ship_margin_pct"):
        CE.normalize_estimate({**est, "settings": {**est["settings"], "ship_margin_pct": 150}}, schema, "alpha")
    assert CE.normalize_estimate({**est, "settings": {**est["settings"], "ship_to": {"country": "", "city": ""}}},
                                 schema, "alpha")["settings"]["ship_to"] is None
    # a lane change is named by the diff
    assert any(c["path"] == "dhl_lanes.1" for c in CE.diff(est, {**est, "dhl_lanes": [dict(SEED, price=21000.0)]}))


def test_sources_weigh_the_target_motor_for_shipping(schema):
    est = _est(schema)
    data = SRC.gather("alpha", est, schema, mass_of=lambda d, c: 10.0,
                      ship_mass_of=lambda d, c: {"full_kg": 30.0, "complete": True, "active_kg": 10.0})
    assert (data["motor_mass_kg"], data["motor_mass_basis"]) == (30.0, "complete")
    data = SRC.gather("alpha", est, schema, mass_of=lambda d, c: 10.0,
                      ship_mass_of=lambda d, c: {"full_kg": 10.0, "complete": False, "active_kg": 10.0})
    assert (data["motor_mass_kg"], data["motor_mass_basis"]) == (13.0, "active x 1.3")


def test_routes_preview_suggestions_and_a_snapshot_keeps_durations_and_lanes(client, monkeypatch):
    _as(monkeypatch, {"email": "admin@example.test", "role": "admin"})
    monkeypatch.setattr(SRC, "shipping_mass", lambda die, config: {"full_kg": 20.0, "complete": True, "active_kg": 10.0})
    url = "/api/admin/projects/alpha/cost-estimate"
    est = client.get(url).json()["estimate"]
    assert est["dhl_lanes"][0]["to_country"] == "AR" and est["settings"]["ship_to"] is None
    est["settings"]["ship_to"] = {"country": "AR", "city": "Buenos Aires"}
    est["fx"] = {"manual": {"CNY": 8.0}}
    est["iterations"][0]["lines"]["tooling"]["weeks"] = 6
    est["iterations"][1]["weeks_override"] = 10
    est["dhl_lanes"].append(dict(SEED, to_country="DE", quoted_on="2026-11-01"))
    preview = client.post(url + "/suggestions", json=est)
    assert preview.status_code == 200, preview.text
    p = preview.json()
    assert p["motor_mass_kg"] == 20.0 and p["cells"]["1"]["shipping"]["source"] == "dhl"
    assert p["cells"]["1"]["shipping"]["weeks"] == 4.0
    assert client.get(url).json()["stored"] is False                        # a preview saves nothing
    bad = json.loads(json.dumps(est))
    bad["iterations"][0]["lines"]["tooling"]["weeks"] = 200
    assert client.post(url + "/suggestions", json=bad).status_code == 422
    assert client.put(url, json=bad).status_code == 422

    saved = client.put(url, json=est)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["estimate"]["iterations"][0]["lines"]["tooling"]["weeks"] == 6.0
    assert body["computed"]["weeks_total"] == 16.0 and len(body["estimate"]["dhl_lanes"]) == 2
    snap = client.post(url + "/snapshot", json={"note": "with durations"}).json()["snapshot"]
    assert snap["estimate"]["iterations"][0]["lines"]["tooling"]["weeks"] == 6.0
    assert snap["estimate"]["iterations"][1]["weeks_override"] == 10.0
    assert [ln["to_country"] for ln in snap["estimate"]["dhl_lanes"]] == ["AR", "DE"]
    assert snap["totals"]["weeks_total"] == 16.0 and snap["iterations"][1]["weeks"]["total"] == 10.0


def test_the_mcp_guard_refuses_the_dhl_lane_and_shipping_margin_keys():
    from motor_ai_sim import mcp_tools as T
    for leak in ({"dhl_lanes": []}, {"ship_margin_pct": 20}, {"lane": {"price": 1.0}}, {"quoted_on": "2026-11-02"}):
        with pytest.raises(AssertionError, match="commercial field leaked"):
            T.assert_no_commercial(leak)
