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
    new, _ = CE.suggest(est, schema, _data(), today="2026-11-02")
    assert new["iterations"][0]["lines"]["mech_housing"]["source_ref"]["production"] == "SMALL/a"
    assert new["iterations"][0]["lines"]["mech_housing"]["unit_cost"] == 420.0


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
