"""Project cost estimate (owner 2026-11-02): admin-only questionnaire, estimated from other projects.  Pure logic plus the
admin routes on a temporary project store.  No solver, no network; neutral fixture ids only."""
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


def _fill(line, qty, unit_cost, currency="EUR", source="manual", by="owner@example.test"):
    line.update({"status": "filled", "qty": qty, "unit_cost": unit_cost, "currency": currency, "source": source,
                 "derive": None, "by": by, "date": "2026-11-02"})


# ── schema ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_schema_loads_with_every_section_the_owner_listed(schema):
    cat = CE.catalogue(schema)
    assert len(cat) >= 60
    sections = {s["id"] for g in schema["groups"] for s in g["sections"]}
    assert {"tooling", "laminations", "magnets", "winding", "electrical", "mechanical", "assembly", "scrap", "redesign",
            "testing", "logistics", "project"} <= sections
    assert [g["id"] for g in schema["groups"]] == ["manufacturing", "testing", "logistics", "project"]
    for _g, _s, item in cat.values():
        assert item["label"]["en"] and item["label"]["zh-CN"] and item["help"]["en"] and item["help"]["zh-CN"]
        assert item["unit"] in schema["units"]
    assert schema["currencies"] == ["EUR", "CNY", "USD"] and schema["default_currency"] == "EUR"
    assert set(CE.SOURCES) >= {"analog", "test_history", "model", "manual"}


def test_schema_refuses_a_duplicate_item(tmp_path, schema):
    bad = json.loads(json.dumps(schema))
    bad["groups"][0]["sections"][0]["items"].append(dict(bad["groups"][0]["sections"][0]["items"][0]))
    p = tmp_path / "s.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        CE.load_schema(p)


def test_default_iterations_follow_the_complex_flag(schema):
    assert CE.default_settings(schema)["iterations"] == 2
    s = json.loads(json.dumps(schema))
    next(f for f in s["settings"] if f["id"] == "complex")["default"] = True
    assert CE.default_settings(s)["iterations"] == 3
    with pytest.raises(CE.EstimateError):
        _est(schema, iterations=6)


# ── iteration 1 vs later tooling ────────────────────────────────────────────────────────────────────────────────────

def test_iteration_one_carries_the_tooling_later_iterations_default_to_modifications(schema):
    est = _est(schema, iterations=3)
    first, second, third = (it["lines"] for it in est["iterations"])
    for tool in ("tool_main", "tool_assembly", "tool_winding", "tool_stack", "tool_magnet"):
        assert first[tool]["status"] == "missing" and first[tool]["derive"] is None
        assert second[tool]["status"] == "filled" and second[tool]["source"] == "rule"
        assert second[tool]["derive"] == {"pct": 20.0, "from_iteration": 1}
    assert first["redesign_changes"]["status"] == "not_applicable"
    assert second["redesign_changes"]["status"] == "missing"
    assert first["mech_cooling"]["status"] == "not_applicable"           # air-cooled by default
    assert first["test_pd"]["status"] == "not_applicable"                # not HV by default
    assert first["mech_shaft"]["qty"] == 2.0                             # motors per iteration

    _fill(first["tool_main"], 1, 10000.0)
    out = CE.compute(est, schema)
    assert out["iterations"][0]["lines"]["tool_main"]["amount"] == {"EUR": 10000.0}
    assert out["iterations"][1]["lines"]["tool_main"]["amount"] == {"EUR": 2000.0}
    assert out["iterations"][2]["lines"]["tool_main"]["amount"] == {"EUR": 2000.0}
    assert "20 % of iteration 1" in out["iterations"][1]["lines"]["tool_main"]["origin"]
    # an unfilled iteration-1 tool leaves its later modifications missing, with the reason
    reasons = {(m["iteration"], m["item"]): m["reason"] for m in out["missing"]}
    assert reasons[(2, "tool_assembly")] == "iteration 1 line not filled"


def test_liquid_cooling_and_hv_switch_their_lines_on(schema):
    est = _est(schema, liquid_cooling=True, high_voltage=True)
    lines = est["iterations"][0]["lines"]
    est2 = CE.default_estimate(schema, "alpha")
    est2["settings"].update({"liquid_cooling": True, "high_voltage": True})
    est2["iterations"] = [CE.default_iteration(schema, est2["settings"], 1)]
    est2["settings"]["iterations"] = 1
    lines2 = est2["iterations"][0]["lines"]
    assert lines2["mech_cooling"]["status"] == "missing" and lines2["test_pd"]["status"] == "missing"
    assert lines2["test_cooling"]["status"] == "missing"
    assert lines["mech_cooling"]["status"] == "not_applicable"     # an existing line keeps the owner's earlier state


# ── totals ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_totals_are_per_currency_with_contingency_and_never_added_across_currencies(schema):
    est = _est(schema, iterations=2, contingency_pct=10)
    it1, it2 = est["iterations"][0]["lines"], est["iterations"][1]["lines"]
    _fill(it1["tool_main"], 1, 1000.0, "EUR")
    _fill(it1["mag_material"], 3.0, 400.0, "CNY")                       # 1200 CNY
    _fill(it1["test_bench"], 10, 50.0, "EUR")                          # 500 EUR testing
    _fill(it1["log_factory_test"], 1, 300.0, "USD")
    _fill(it2["mech_shaft"], 2, 100.0, "EUR")
    it1["scrap_allowance"].update({"status": "filled", "qty": 5.0, "source": "manual"})
    est["project_lines"]["proj_engineering"].update({"status": "filled", "qty": 10, "unit_cost": 80.0, "currency": "EUR",
                                                     "source": "manual"})
    est["project_lines"]["proj_warranty"].update({"status": "filled", "qty": 2.0, "source": "manual"})
    out = CE.compute(est, schema)
    i1, i2 = out["iterations"]
    # scrap 5 % of iteration-1 manufacturing without tooling: 5 % of 1200 CNY
    assert i1["lines"]["scrap_allowance"]["amount"] == {"CNY": 60.0}
    assert i1["manufacturing"] == {"EUR": 1000.0, "CNY": 1260.0}
    assert i1["testing"] == {"EUR": 500.0} and i1["logistics"] == {"USD": 300.0}
    assert i1["subtotal"] == {"EUR": 1500.0, "CNY": 1260.0, "USD": 300.0}
    assert i2["lines"]["tool_main"]["amount"] == {"EUR": 200.0}
    assert i2["manufacturing"] == {"EUR": 400.0}
    # engineering 10 h x 80 x 2 iterations; warranty 2 % of all manufacturing (EUR 1400, CNY 1260)
    assert out["project"]["lines"]["proj_engineering"]["amount"] == {"EUR": 1600.0}
    assert out["project"]["lines"]["proj_warranty"]["amount"] == {"EUR": 28.0, "CNY": 25.2}
    assert out["subtotal"] == {"EUR": 1500.0 + 400.0 + 1600.0 + 28.0, "CNY": 1285.2, "USD": 300.0}
    assert out["contingency"] == {"EUR": 352.8, "CNY": 128.52, "USD": 30.0}
    assert out["total"] == {"EUR": 3880.8, "CNY": 1413.72, "USD": 330.0}
    assert out["currencies"] == ["EUR", "CNY", "USD"]
    assert any("several currencies" in w for w in out["warnings"])
    assert out["missing_count"] == len(out["missing"]) > 0
    assert all(m["item"] != "tool_main" for m in out["missing"])


def test_milestones_suggest_shares_and_warn_when_not_100(schema):
    est = _est(schema, iterations=2, contingency_pct=0)
    _fill(est["iterations"][0]["lines"]["mech_shaft"], 1, 300.0)
    _fill(est["iterations"][1]["lines"]["mech_shaft"], 1, 100.0)
    out = CE.compute(est, schema)
    assert [m["suggested_pct"] for m in out["milestones"]] == [75.0, 25.0]
    est["iterations"][0]["settings"]["milestone_pct"] = 50
    est["iterations"][1]["settings"]["milestone_pct"] = 40
    out = CE.compute(est, schema)
    assert out["milestones"][0]["amount"] == {"EUR": 200.0}
    assert any("90.0 %" in w for w in out["warnings"])


def test_validation_refuses_unknown_lines_currencies_and_negative_numbers(schema):
    est = _est(schema)
    bad = json.loads(json.dumps(est))
    bad["iterations"][0]["lines"]["no_such_line"] = {}
    with pytest.raises(CE.EstimateError, match="unknown lines"):
        CE.normalize_estimate(bad, schema, "alpha")
    bad = json.loads(json.dumps(est))
    bad["iterations"][0]["lines"]["tool_main"]["currency"] = "GBP"
    with pytest.raises(CE.EstimateError, match="currency"):
        CE.normalize_estimate(bad, schema, "alpha")
    bad = json.loads(json.dumps(est))
    bad["iterations"][0]["lines"]["tool_main"]["unit_cost"] = -5
    with pytest.raises(CE.EstimateError, match="out of range"):
        CE.normalize_estimate(bad, schema, "alpha")


# ── scale rule and suggest ──────────────────────────────────────────────────────────────────────────────────────────

def test_scale_rule(schema):
    assert CE.scale_factor(schema, "active_mass", {"active_mass_kg": 21.0}, {"active_mass_kg": 5.0})[0] == 4.2
    assert CE.scale_factor(schema, "outer_diameter", {"outer_diameter_mm": 200}, {"outer_diameter_mm": 100}, 2.0)[0] == 4.0
    assert CE.scale_factor(schema, "one_to_one", {}, {})[0] == 1.0
    factor, why, _t, _a = CE.scale_factor(schema, "power", {"power_kw": 100}, {})
    assert factor is None and "analog" in why
    factor, why, _t, _a = CE.scale_factor(schema, "stack_length", {}, {"stack_length_mm": 40})
    assert factor is None and "target" in why


def test_rows_go_to_one_item_by_the_longest_keyword(schema):
    rows = [{"list": "motor", "row_id": "a", "name_en": "Bearing 6205", "name_zh": "", "material": ""},
            {"list": "motor", "row_id": "b", "name_en": "Magnetising", "name_zh": "", "material": ""},
            {"list": "motor", "row_id": "c", "name_en": "Magnet N52UH", "name_zh": "", "material": ""},
            {"list": "motor", "row_id": "d", "name_en": "", "name_zh": "轴承", "material": ""},
            {"list": "tooling", "row_id": "e", "name_en": "Stator mould", "name_zh": "", "material": ""},
            {"list": "extras", "row_id": "f", "name_en": "Assembly work", "name_zh": "", "material": ""},
            {"list": "tooling", "row_id": "g", "name_en": "Balancing arbor", "name_zh": "", "material": ""}]
    out = {k: [r["row_id"] for r in v] for k, v in CE.assign_rows(schema, rows).items()}
    assert out["mech_bearings"] == ["a", "d"]
    assert out["mag_magnetising"] == ["b"] and out["mag_material"] == ["c"]
    assert out["tool_main"] == ["e"] and out["asm_labour"] == ["f"]
    assert "g" not in sum(out.values(), [])         # a tool is never a per-motor balancing cost
    assert "mech_holders" not in out


def _data():
    def prod(ref, mass, od, rows, updated="2026-10-01"):
        return {"ref": ref, "title": ref, "updated_at": updated,
                "basis": {"active_mass_kg": mass, "magnet_mass_kg": mass / 5, "outer_diameter_mm": od, "stack_length_mm": 40},
                "rows": rows}
    r = lambda lst, rid, name, amount, cur="CNY": {"list": lst, "row_id": rid, "name_en": name, "name_zh": "", "material": "",
                                                    "amount": amount, "currency": cur}
    return {"target_ref": "DIE-A/big",
            "target_basis": {"active_mass_kg": 21.0, "magnet_mass_kg": 4.2, "outer_diameter_mm": 200, "stack_length_mm": 80},
            "productions": [prod("SMALL/a", 5.0, 100, [r("motor", "p1", "Housing", 100.0), r("motor", "p2", "Magnet", 500.0),
                                                       r("tooling", "t1", "Stator mould", 3000.0)]),
                            prod("MID/b", 14.0, 150, [r("motor", "q1", "Housing", 300.0)])],
            "estimates": [{"project_id": "other", "title": "Other", "basis": {"active_mass_kg": 21.0},
                           "iterations": [{"index": 1, "lines": {
                               "test_bench": {"status": "filled", "qty": 12, "unit": "h", "unit_cost": 90.0, "currency": "EUR",
                                              "source": "manual"},
                               "log_factory_test": {"status": "filled", "qty": 1, "unit": "shipment", "unit_cost": 420.0,
                                                    "currency": "EUR", "source": "manual"}}}],
                           "project_lines": {}}],
            "tests": {"SMALL/a": {"days": 3, "kinds": {"load": 2}}}}


def test_suggest_never_overwrites_manual_lines_and_fills_from_the_nearest_analog(schema):
    est = _est(schema, iterations=2)
    it1 = est["iterations"][0]["lines"]
    _fill(it1["mech_shaft"], 2, 777.0)
    it1["mech_bearings"]["status"] = "not_applicable"
    it1["mech_bearings"]["source"] = "manual"
    new, report = CE.suggest(est, schema, _data(), today="2026-11-02")
    n1 = new["iterations"][0]["lines"]
    assert n1["mech_shaft"] == it1["mech_shaft"]                     # manual: untouched
    assert n1["mech_bearings"]["status"] == "not_applicable"          # the owner's decision: untouched
    housing = n1["mech_housing"]
    # nearest by active mass: MID/b (21/14 = 1.5) beats SMALL/a (21/5 = 4.2)
    assert housing["source"] == "analog" and housing["source_ref"]["production"] == "MID/b"
    assert housing["scale"]["rule"] == "active_mass" and housing["scale"]["factor"] == 1.5
    assert housing["unit_cost"] == 450.0 and housing["qty"] == 2.0 and housing["currency"] == "CNY"
    assert "from production MID/b" in CE.origin_text(housing, schema) and "x1.5" in CE.origin_text(housing, schema)
    # tooling list line: the whole line, scaled by the outer diameter 200/100
    tool = n1["tool_main"]
    assert tool["unit_cost"] == 6000.0 and tool["qty"] == 1.0 and tool["source_ref"]["production"] == "SMALL/a"
    # later iteration tooling stays "modifications" of iteration 1
    assert new["iterations"][1]["lines"]["tool_main"]["derive"] == {"pct": 20.0, "from_iteration": 1}
    # model line: simulation magnet mass x price per kg of the analog (500 CNY / 1 kg)
    mag = n1["mag_material"]
    assert mag["source"] == "model" and mag["qty"] == round(4.2 * 1.0 * 2, 3) and mag["unit_cost"] == 500.0
    # testing from another project's estimate = test_history; logistics = analog of that project
    assert n1["test_bench"]["source"] == "test_history" and n1["test_bench"]["unit_cost"] == 90.0
    assert n1["test_bench"]["source_ref"]["project"] == "other"
    assert n1["log_factory_test"]["source"] == "analog" and n1["log_factory_test"]["unit_cost"] == 420.0
    assert report["filled_count"] > 0
    assert any(s["reason"].startswith("nothing to base") for s in report["skipped"])

    # a second suggest with refresh keeps the manual line too
    again, _ = CE.suggest(new, schema, _data(), today="2026-11-03", refresh=True)
    assert again["iterations"][0]["lines"]["mech_shaft"] == it1["mech_shaft"]


def test_suggest_keeps_a_line_missing_when_the_basis_is_unknown(schema):
    est = _est(schema, iterations=1)
    data = _data()
    data["target_basis"] = {}
    new, report = CE.suggest(est, schema, data, today="2026-11-02")
    assert new["iterations"][0]["lines"]["mech_housing"]["status"] == "missing"
    assert any("unknown for the target" in s["reason"] for s in report["skipped"])
    # an owner-typed basis override makes it scalable
    est["settings"]["basis_overrides"] = {"active_mass_kg": 14.0}
    new, _ = CE.suggest(est, schema, data, today="2026-11-02")
    assert new["iterations"][0]["lines"]["mech_housing"]["unit_cost"] == 300.0


def test_suggest_only_uses_the_chosen_analogs(schema):
    est = _est(schema, iterations=1)
    est["settings"]["analogs"] = ["SMALL/a"]
    est["settings"]["basis_overrides"] = {"active_mass_kg": 18.0}      # 18 / 5 = 3.6: inside the scale limits
    new, _ = CE.suggest(est, schema, _data(), today="2026-11-02")
    assert new["iterations"][0]["lines"]["mech_housing"]["source_ref"]["production"] == "SMALL/a"
    assert new["iterations"][0]["lines"]["mech_housing"]["unit_cost"] == 360.0


def test_production_rows_read_motor_tooling_and_work_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "prod"))
    from motor_ai_sim.production import store as PS
    PS.save_file("SYN", "a", "production.json", {"die": "SYN", "config": "a", "updated_at": "2026-10-01"})
    PS.save_file("SYN", "a", "parts.json", {"parts": [{"id": "p1", "name": {"en": "Housing", "zh": "机壳"}, "kind": "made"},
                                                      {"id": "t1", "name": {"en": "Mould", "zh": ""}, "kind": "made"}]})
    PS.save_file("SYN", "a", "bom.json", {"rows": [{"part_id": "p1", "qty_per_motor": 1}]})
    PS.save_file("SYN", "a", "costs.json", {"rows": [{"part_id": "p1", "unit_price": 120, "currency": "CNY"}],
                                            "extras": [{"id": "e1", "name": {"en": "Assembly"}, "amount": 50, "currency": "EUR"}]})
    PS.save_file("SYN", "a", "bom_tooling.json", {"rows": [{"part_id": "t1", "qty_per_motor": 2}]})
    PS.save_file("SYN", "a", "costs_tooling.json", {"rows": [{"part_id": "t1", "unit_price": 1000, "currency": "CNY"}]})
    prods = SRC.productions(basis_of=lambda d, c: {"active_mass_kg": 3.0})
    assert [p["ref"] for p in prods] == ["SYN/a"]
    rows = {r["row_id"]: (r["list"], r["amount"], r["currency"]) for r in prods[0]["rows"]}
    assert rows == {"p1": ("motor", 120.0, "CNY"), "e1": ("extras", 50.0, "EUR"), "t1": ("tooling", 2000.0, "CNY")}


# ── suggest quality (first real run, 2026-11-02): price per kg, scale limits, lamination match, later tooling, report ──
# made-up numbers only; the bands are set in a schema copy so no test depends on the configured prices

def _qschema(schema, **bands):
    s = json.loads(json.dumps(schema))
    for iid, band in bands.items():
        s["price_per_kg_bands"]["items"][iid] = {"CNY": list(band)}
    return s


def _row(lst, rid, name, amount, cur="CNY", material="", **extra):
    return {"list": lst, "row_id": rid, "name_en": name, "name_zh": "", "material": material, "amount": amount,
            "currency": cur, **extra}


def _prod(ref, basis, rows, updated="2026-10-01"):
    return {"ref": ref, "title": ref, "updated_at": updated, "basis": basis, "rows": rows}


def _qdata(target, prods):
    return {"target_ref": "DIE-A/big", "target_basis": target, "productions": prods, "estimates": [], "tests": {}}


def test_magnet_price_per_kg_from_a_row_priced_per_piece_per_motor_per_kg_or_per_order(schema, tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "prod"))
    from motor_ai_sim.production import store as PS
    # the same magnet cost written four ways: 8 pieces x 30 = 240 per motor = 600 per kg of 0.4 kg = 2400 for 10 motors
    sheets = {"PCS": (30.0, "per piece"), "MOT": (240.0, None), "KG": (600.0, "price per kg"), "ORD": (2400.0, "whole order")}
    for die, (price, note) in sheets.items():
        PS.save_file(die, "a", "production.json", {"die": die, "config": "a", "order_quantity": 10, "updated_at": "2026-10-01"})
        PS.save_file(die, "a", "parts.json", {"parts": [{"id": "m1", "name": {"en": "Magnet N52UH", "zh": ""}, "kind": "purchased"}]})
        PS.save_file(die, "a", "bom.json", {"rows": [{"part_id": "m1", "qty_per_motor": 8}]})
        PS.save_file(die, "a", "costs.json", {"rows": [{"part_id": "m1", "unit_price": price, "currency": "CNY", "note": note}]})
    basis = {"magnet_mass_kg": 0.4, "active_mass_kg": 3.0, "outer_diameter_mm": 100}
    prods = {p["ref"]: p for p in SRC.productions(basis_of=lambda d, c: dict(basis))}
    got = {ref: (p["rows"][0]["price_per"], p["rows"][0]["amount"], p["rows"][0]["qty_per_motor"]) for ref, p in prods.items()}
    assert got == {"PCS/a": ("piece", 240.0, 8), "MOT/a": ("motor", 240.0, 8), "KG/a": ("kg", None, 8),
                   "ORD/a": ("order", 240.0, 8)}
    assert prods["ORD/a"]["motors"] == 10
    s = _qschema(schema, mag_material=(100, 5000))
    target = {"magnet_mass_kg": 2.0, "active_mass_kg": 6.0, "outer_diameter_mm": 150}
    for ref, prod in prods.items():
        est = _est(schema, iterations=1)
        new, report = CE.suggest(est, s, _qdata(target, [prod]), today="2026-11-02")
        mag = new["iterations"][0]["lines"]["mag_material"]
        # the price per kg is the analog's cost per motor / its OWN simulated magnet mass - whatever the row's unit
        assert mag["status"] == "filled" and mag["source"] == "model", ref
        assert mag["unit_cost"] == 600.0 and mag["qty"] == 4.0, ref           # 2 kg x 1.0 buy x 2 motors
        assert mag["source_ref"]["production"] == ref
        entry = next(f for f in report["filled"] if f["item"] == "mag_material")
        assert entry["analog"] == ref and entry["price_per_kg"] == 600.0 and entry["factor"] is None


def test_the_plausibility_band_refuses_a_price_per_kg_and_never_fills_it(schema):
    s = _qschema(schema, mag_material=(100, 5000))
    target = {"magnet_mass_kg": 2.0, "active_mass_kg": 6.0, "outer_diameter_mm": 150}
    # 40 per motor for 1 kg of magnets = 40 per kg: below the band (a per-piece price taken as per motor)
    cheap = _prod("CHEAP/a", {"magnet_mass_kg": 1.0, "active_mass_kg": 5.0, "outer_diameter_mm": 140},
                  [_row("motor", "m1", "Magnet", 40.0, unit_price=40.0, qty_per_motor=12, price_per="motor")])
    est = _est(schema, iterations=1)
    new, report = CE.suggest(est, s, _qdata(target, [cheap]), today="2026-11-02")
    mag = new["iterations"][0]["lines"]["mag_material"]
    assert mag["status"] == "missing" and mag["unit_cost"] is None and mag["source"] is None
    assert mag["qty"] == 4.0                                                # the simulated mass is still shown
    assert "implausible price per kg from CHEAP/a: 40 CNY/kg" in mag["note"]
    assert any(w["item"] == "mag_material" and "implausible price per kg from CHEAP/a: 40" in w["text"]
               for w in report["warnings"])
    assert all(f["item"] != "mag_material" for f in report["filled"])
    assert CE.compute(new, s)["iterations"][0]["lines"]["mag_material"]["amount"] is None
    # a second, plausible analog is used instead; the implausible one is still reported
    fair = _prod("FAIR/b", {"magnet_mass_kg": 1.0, "active_mass_kg": 3.0, "outer_diameter_mm": 100},
                 [_row("motor", "m1", "Magnet", 700.0)])
    new, report = CE.suggest(est, s, _qdata(target, [cheap, fair]), today="2026-11-02")
    mag = new["iterations"][0]["lines"]["mag_material"]
    assert mag["status"] == "filled" and mag["unit_cost"] == 700.0 and mag["source_ref"]["production"] == "FAIR/b"
    assert any("CHEAP/a" in w["text"] for w in report["warnings"])


def _big_motor():
    return {"active_mass_kg": 36.0, "magnet_mass_kg": 10.0, "copper_mass_kg": 6.0, "steel_mass_kg": 20.0,
            "outer_diameter_mm": 400, "stack_length_mm": 90}


def test_the_scale_limit_refuses_a_far_analog_and_falls_back_to_a_nearer_one(schema):
    tiny = _prod("TINY/a", {"active_mass_kg": 0.15, "outer_diameter_mm": 40},
                 [_row("motor", "s", "Shaft", 20.0), _row("motor", "h", "Housing", 50.0),
                  _row("motor", "b", "Bolt M3 zinc-plated", 3.0)], updated="2026-10-30")
    mid = _prod("MID/b", {"active_mass_kg": 10.0, "outer_diameter_mm": 200},
                [_row("motor", "s", "Shaft", 300.0), _row("motor", "b", "Bolt M5 zinc-plated", 9.0)], updated="2026-09-01")
    est = _est(schema, iterations=1)
    new, report = CE.suggest(est, schema, _qdata(_big_motor(), [tiny, mid]), today="2026-11-02")
    lines = new["iterations"][0]["lines"]
    # shaft: TINY would be x240 (36 / 0.15) - refused; MID x3.6 is inside 0.25..4
    shaft = lines["mech_shaft"]
    assert shaft["source_ref"]["production"] == "MID/b" and shaft["scale"]["factor"] == 3.6
    assert shaft["unit_cost"] == 1080.0
    # housing: only TINY has one - the line stays missing, the warning names the nearest analog and its factor
    housing = lines["mech_housing"]
    assert housing["status"] == "missing" and housing["unit_cost"] is None
    text = "no analog within the scale limits 0.25..4: nearest production TINY/a, scaled by active_mass x240"
    assert text in housing["note"]
    assert any(w["item"] == "mech_housing" and w["text"] == text for w in report["warnings"])
    # fasteners scale 1:1 (factor 1 for both): the analog closest in diameter / active mass wins, not the newest
    assert lines["mech_fasteners"]["source_ref"]["production"] == "MID/b" and lines["mech_fasteners"]["unit_cost"] == 9.0
    # the bolts are fasteners, never "mechanical materials" ("zinc-plated" is not "plate")
    assert lines["mech_materials"]["status"] == "missing"
    # the limit comes from the schema
    wide = json.loads(json.dumps(schema))
    wide["scale_limits"] = {"min": 0.001, "max": 1000}
    new, _ = CE.suggest(est, wide, _qdata(_big_motor(), [tiny]), today="2026-11-02")
    assert new["iterations"][0]["lines"]["mech_housing"]["scale"]["factor"] == 240.0
    bad = json.loads(json.dumps(schema))
    bad["scale_limits"] = {"min": 2, "max": 4}
    with pytest.raises(ValueError, match="scale_limits"):
        CE._check_schema(bad)


def test_lamination_sheet_matches_cores_and_grades_and_warns_when_nothing_matches(schema):
    rows = [_row("motor", "c1", "Stator core", 200.0, material="B20AT1500"),
            _row("motor", "c2", "Rotor core", 100.0),
            _row("motor", "c3", "Rotor yoke", 80.0, material="10JNEX900"),
            _row("motor", "m1", "Magnet segment N52UH", 500.0),
            _row("motor", "m2", "Magnet holder", 40.0),
            _row("motor", "b1", "Bolt M5 zinc-plated", 2.0),
            _row("motor", "x1", "Stator core laser cutting", 60.0)]
    out = {k: [r["row_id"] for r in v] for k, v in CE.assign_rows(schema, rows).items()}
    assert out["lam_sheet"] == ["c1", "c2", "c3"]
    assert out["mag_material"] == ["m1"] and "mag_segmentation" not in out   # the segment is the magnet itself
    assert out["mech_holders"] == ["m2"] and out["mech_fasteners"] == ["b1"] and "mech_materials" not in out
    assert out["lam_cut"] == ["x1"]
    s = _qschema(schema, lam_sheet=(10, 1000))
    analog = _prod("LAM/a", {"steel_mass_kg": 2.0, "active_mass_kg": 4.0, "outer_diameter_mm": 120}, rows)
    est = _est(schema, iterations=1)
    new, _ = CE.suggest(est, s, _qdata(_big_motor(), [analog]), today="2026-11-02")
    sheet = new["iterations"][0]["lines"]["lam_sheet"]
    # (200 + 100 + 80) per motor / (2 kg x 2.2 buy factor) = 86.3636 per bought kg; 20 kg x 2.2 x 2 motors = 88 kg
    assert sheet["status"] == "filled" and sheet["source"] == "model"
    assert sheet["unit_cost"] == 86.3636 and sheet["qty"] == 88.0
    # an analog without any core / sheet row: the line stays missing with a clear warning
    bare = _prod("BARE/a", {"steel_mass_kg": 2.0, "active_mass_kg": 4.0, "outer_diameter_mm": 120},
                 [_row("motor", "s", "Shaft", 30.0)])
    new, report = CE.suggest(est, s, _qdata(_big_motor(), [bare]), today="2026-11-02")
    sheet = new["iterations"][0]["lines"]["lam_sheet"]
    assert sheet["status"] == "missing" and sheet["qty"] == 88.0
    assert "no analog production has a row matching lam_sheet" in sheet["note"]
    assert any(w["item"] == "lam_sheet" and "looked for: lamination" in w["text"] for w in report["warnings"])


def test_later_iterations_take_the_modifications_rule_once_iteration_one_tooling_is_filled(schema):
    analog = _prod("TOOL/a", {"active_mass_kg": 12.0, "outer_diameter_mm": 200, "stack_length_mm": 60},
                   [_row("tooling", "t1", "Stator mould", 3000.0), _row("motor", "s", "Shaft", 100.0)])
    est = _est(schema, iterations=3)
    est["iterations"][1]["lines"]["tool_main"].update({"status": "missing", "source": None, "derive": None})  # reset
    new, report = CE.suggest(est, schema, _qdata(_big_motor(), [analog]), today="2026-11-02")
    it1, it2, it3 = (it["lines"] for it in new["iterations"])
    assert it1["tool_main"]["source"] == "analog" and it1["tool_main"]["unit_cost"] == 6000.0     # x2 by diameter
    for later in (it2, it3):                     # never an analog of its own, always "modifications" of iteration 1
        assert later["tool_main"]["source"] == "rule" and later["tool_main"]["derive"] == {"pct": 20.0, "from_iteration": 1}
    assert {"item": "tool_main", "where": "iteration 2", "source": "rule", "status": "filled", "analog": None, "factor": None,
            "rule": None, "check": False, "pct_of_iteration_1": 20.0} in report["filled"]
    out = CE.compute(new, schema)
    amounts = [it["lines"]["tool_main"]["amount"] for it in out["iterations"]]
    assert amounts == [{"CNY": 6000.0}, {"CNY": 1200.0}, {"CNY": 1200.0}]
    m1, m2, m3 = (it["manufacturing"]["CNY"] for it in out["iterations"])
    assert m1 - m2 == pytest.approx(4800.0) and m2 == m3       # the later iterations are not a copy of iteration 1
    again, _ = CE.suggest(new, schema, _qdata(_big_motor(), [analog]), today="2026-11-03", refresh=True)
    assert again["iterations"][2]["lines"]["tool_main"]["derive"] == {"pct": 20.0, "from_iteration": 1}


def test_the_report_carries_the_analog_and_factor_of_every_filled_line_and_marks_check(schema):
    assert not CE.needs_check(schema, 2.0) and not CE.needs_check(schema, 0.5) and not CE.needs_check(schema, None)
    assert CE.needs_check(schema, 2.01) and CE.needs_check(schema, 0.49)
    target = {"active_mass_kg": 30.0, "outer_diameter_mm": 80, "stack_length_mm": 60}
    analog = _prod("REF/a", {"active_mass_kg": 10.0, "outer_diameter_mm": 200, "stack_length_mm": 40},
                   [_row("tooling", "t1", "Stator mould", 1000.0), _row("motor", "h", "Housing", 100.0),
                    _row("tooling", "w1", "Winding mandrel", 500.0), _row("motor", "n", "PT1000", 7.0)])
    est = _est(schema, iterations=1)
    new, report = CE.suggest(est, schema, _qdata(target, [analog]), today="2026-11-02")
    by_item = {f["item"]: f for f in report["filled"]}
    assert by_item["tool_main"] == {"item": "tool_main", "where": "iteration 1", "source": "analog", "status": "filled",
                                         "analog": "REF/a", "factor": 0.4, "rule": "outer_diameter", "check": True}
    assert by_item["mech_housing"]["factor"] == 3.0 and by_item["mech_housing"]["check"] is True
    assert by_item["tool_winding"]["factor"] == 1.5 and by_item["tool_winding"]["check"] is False
    assert by_item["el_temp_sensors"]["factor"] == 1.0 and by_item["el_temp_sensors"]["rule"] == "one_to_one"
    for f in report["filled"]:
        assert {"analog", "factor", "rule", "check"} <= set(f)
        assert f["status"] == "filled"
    assert report["check_count"] == sum(1 for f in report["filled"] if f["check"]) == 2
    assert report["scale_limits"] == [0.25, 4.0]
    # the line itself keeps the analog and the factor (the Review step reads them from the estimate)
    assert new["iterations"][0]["lines"]["tool_main"]["scale"]["factor"] == 0.4
    assert new["iterations"][0]["lines"]["tool_main"]["source_ref"]["production"] == "REF/a"


# ── diff ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_diff_names_what_changed(schema):
    a = _est(schema)
    b = json.loads(json.dumps(a))
    _fill(b["iterations"][0]["lines"]["tool_main"], 1, 500.0)
    b["settings"]["contingency_pct"] = 15
    changes = CE.diff(a, b, schema)
    paths = {c["path"] for c in changes}
    assert {"settings.contingency_pct", "iteration 1.tool_main.unit_cost", "iteration 1.tool_main.status", "total.EUR"} <= paths
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
    monkeypatch.setattr(SRC, "motor_basis", lambda die, config: {})
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
    for leak in ({"cost_estimate": {}}, {"unit_cost": 1.0}, {"contingency_pct": 10}, {"computed": {"contingency": {}}}):
        with pytest.raises(AssertionError, match="commercial field leaked"):
            T.assert_no_commercial(leak)


def test_admin_round_trip_revision_suggest_snapshot_and_audit(client, monkeypatch, tmp_path):
    _as(monkeypatch, {"email": "admin@example.test", "role": "admin"})
    got = client.get("/api/admin/projects/alpha/cost-estimate").json()
    assert got["stored"] is False and got["estimate"]["revision"] == 0
    assert got["estimate"]["settings"]["target_motor"] == {"die": "DIE-A", "config": "big"}
    assert got["schema"]["schema_version"] == 1 and got["computed"]["missing_count"] > 0
    assert client.get("/api/admin/projects/nope/cost-estimate").status_code == 404

    est = got["estimate"]
    _fill(est["iterations"][0]["lines"]["tool_main"], 1, 1000.0)
    saved = client.put("/api/admin/projects/alpha/cost-estimate", json=est)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["estimate"]["revision"] == 1 and body["estimate"]["updated_by"] == "admin@example.test"
    assert any(c["path"] == "iteration 1.tool_main.unit_cost" for c in body["changes"])
    assert body["computed"]["iterations"][1]["lines"]["tool_main"]["amount"] == {"EUR": 200.0}
    # stale revision -> 409, nothing written
    stale = client.put("/api/admin/projects/alpha/cost-estimate", json=est)
    assert stale.status_code == 409 and stale.json()["detail"]["revision"] == 1
    bad = dict(body["estimate"]); bad["settings"] = {**bad["settings"], "currency": "GBP"}
    assert client.put("/api/admin/projects/alpha/cost-estimate", json=bad).status_code == 422

    first = client.post("/api/admin/projects/alpha/cost-estimate/snapshot", json={"note": "first"}).json()["snapshot"]
    assert first["n"] == 1 and first["diff"] == [] and first["totals"]["total"]["EUR"] > 0

    sug = client.post("/api/admin/projects/alpha/cost-estimate/suggest", json={"revision": 1})
    assert sug.status_code == 200, sug.text
    sbody = sug.json()
    assert sbody["estimate"]["revision"] == 2 and "report" in sbody
    assert sbody["estimate"]["iterations"][0]["lines"]["tool_main"]["unit_cost"] == 1000.0   # manual kept

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
    # nothing commercial in the project's own audit log
    project_audit = tmp_path / "projects" / "alpha" / "audit.jsonl"
    assert not project_audit.exists() or "cost_estimate" not in project_audit.read_text(encoding="utf-8")


def test_the_project_routes_never_carry_the_estimate(client, monkeypatch):
    """The customer-facing project payload has no cost-estimate field (it lives in its own file, admin routes only)."""
    from motor_ai_sim.development.routes import router as project_router
    _as(monkeypatch, {"email": "admin@example.test", "role": "admin"})
    est = client.get("/api/admin/projects/alpha/cost-estimate").json()["estimate"]
    _fill(est["iterations"][0]["lines"]["tool_main"], 1, 1000.0)
    assert client.put("/api/admin/projects/alpha/cost-estimate", json=est).status_code == 200
    app = FastAPI(); app.include_router(project_router)
    monkeypatch.setattr(auth, "resolve_user", lambda token: {"email": "customer@example.test", "role": "user"})
    resp = TestClient(app).get("/api/projects/alpha", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    assert "cost" not in resp.text.lower() and "1000" not in resp.text
