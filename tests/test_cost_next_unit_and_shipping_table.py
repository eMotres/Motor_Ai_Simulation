"""Cost estimate: the price of the NEXT unit and the automatic shipping from the rate table (owner 2026-11-22).

Pinned: the rate table reproduces the one real quote (Shanghai -> Buenos Aires, 2 x 75 kg, CNY 20 169.14, 16 days) and says which
zones are only estimates; a lane (a real quote) always wins over the table; the table path converts, adds the margin, rounds, gives
the weeks; parcels / box / service settings change the chargeable weight (volumetric weight L x W x H / 5000); the next unit is one
motor's manufacturing + testing when ticked + shipping of ONE motor, overridable, never invented; the customer quote carries
``next_unit`` and ``excluded`` only when it is computable, so a quote shared before this change reads exactly as it did."""
from __future__ import annotations

import json

import pytest

from motor_ai_sim.development import cost_estimate as CE
from motor_ai_sim.development import cost_quote as Q
from motor_ai_sim.development import cost_shipping_rates as SR


@pytest.fixture
def schema():
    return CE.load_schema()


@pytest.fixture
def table():
    return SR.load_table()


def _est(schema, **settings):
    est = CE.default_estimate(schema, "alpha")
    est["settings"].update({"ship_to": {"country": "DE", "city": "Munich"}, **settings})
    est["fx"] = {"base": "EUR", "date": "2026-11-02", "source": "manual", "rates": {"CNY": 8.0, "USD": 1.25},
                 "ecb": None, "manual": {"CNY": 8.0, "USD": 1.25}, "manual_date": "2026-11-02"}
    est["dhl_lanes"] = []
    for it in est["iterations"]:
        it["motors"] = 2
    return CE.normalize_estimate(est, schema, "alpha")


def _fill(line, amount, weeks=None):
    line.update({"status": "filled", "amount": amount, "currency": "EUR", "source": "manual", "by": "o@example.test",
                 "date": "2026-11-22", "weeks": weeks})


# ── the table ────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_table_reproduces_the_one_real_quote_and_marks_the_calibrated_zone(table):
    q, why = SR.quote("AR", [{"weight_kg": 75, "box_cm": (60, 60, 40)}] * 2, table)
    assert why == "" and q["amount"] == pytest.approx(20169.14, abs=0.01) and q["transit_days"] == 16
    assert q["zone"] == "SA" and q["zone_basis"] == "calibrated" and q["chargeable_kg"] == 150.0 and q["volumetric_kg"] == 57.6
    de, _ = SR.quote("DE", [{"weight_kg": 40, "box_cm": SR.default_box_cm(40, table)}], table)
    assert de["zone"] == "EU" and de["zone_basis"] == "estimate"            # an estimate says so


def test_volumetric_weight_decides_for_a_light_big_box_and_there_is_a_minimum_charge(table):
    heavy, _ = SR.quote("DE", [{"weight_kg": 10, "box_cm": (50, 50, 50)}], table)         # 125000 / 5000 = 25 kg
    assert heavy["chargeable_kg"] == 25.0 and heavy["volumetric_kg"] == 25.0
    tiny, _ = SR.quote("DE", [{"weight_kg": 1, "box_cm": (10, 10, 10)}], table)
    assert tiny["amount"] == round(table["min_charge_cny"] * 1.15, 2)
    assert SR.quote("KP", [{"weight_kg": 5, "box_cm": (10, 10, 10)}], table)[0] is None
    assert "no zone for KP" in SR.quote("kp", [{"weight_kg": 5, "box_cm": (10, 10, 10)}], table)[1]
    assert SR.quote("DE", [{"weight_kg": 5, "box_cm": (10, 10, 10)}], table, "sea")[0] is None


def test_every_country_is_in_one_zone_only(table):
    seen = {}
    for zid, z in table["zones"].items():
        for c in z["countries"]:
            assert c not in seen, (c, zid, seen[c])
            seen[c] = zid
    assert len(seen) >= 75 and all(len(c) == 2 and c.isupper() for c in seen)


def test_the_default_box_is_the_crate_density(table):
    l, w, h = SR.default_box_cm(75, table)
    assert l * w * h / 1e6 == pytest.approx(75 / table["default_density_kg_m3"], rel=0.02)


# ── the shipping line ───────────────────────────────────────────────────────────────────────────────────────────────

def test_without_a_lane_the_line_comes_from_the_table_with_its_basis(schema):
    est = _est(schema)
    sug, why = CE.dhl_suggestion(est, est["iterations"][0], schema, 20.0, "complete")
    assert why == ""
    d = sug["source_ref"]["dhl"]
    assert d["basis"] == "rate_table" and d["zone"] == "EU" and d["zone_basis"] == "estimate" and d["table_date"] == "2026-11-22"
    assert d["parcels"] == 2 and d["weight_kg"] == 85.0 and d["margin_pct"] == 20.0 and d["service"] == "express"
    assert d["price"] == pytest.approx(d["lane_price"] * 1.2, abs=0.01) and d["rate"] == 8.0
    assert sug["amount"] % 100 == 0 and sug["amount"] >= d["price_eur_unrounded"]       # rounded UP to the step
    assert sug["weeks"] == 2.0                                                             # 6 days -> 1 week + the customs week


def test_a_lane_always_wins_over_the_table(schema):
    est = _est(schema)
    est["dhl_lanes"] = [{"from": "CN Shanghai", "to_country": "DE", "mode": "x", "quoted_on": "2026-11-20", "weight_kg": 100.0,
                         "price": 4000.0, "currency": "EUR", "transit_days": 5, "source": "s", "note": ""}]
    sug, _ = CE.dhl_suggestion(est, est["iterations"][0], schema, 20.0)
    assert sug["source_ref"]["dhl"]["basis"] == "lane quote" and sug["source_ref"]["dhl"]["lane_weight_kg"] == 100.0


def test_parcels_box_and_service_move_the_chargeable_weight(schema):
    base = _est(schema)
    one = _est(schema, ship_parcels=1)
    big = _est(schema, ship_box_cm=[100, 80, 60])
    d = lambda e: CE.dhl_suggestion(e, e["iterations"][0], schema, 20.0)[0]["source_ref"]["dhl"]       # noqa: E731
    assert d(base)["parcels"] == 2 and d(one)["parcels"] == 1
    assert d(big)["chargeable_kg"] > d(base)["chargeable_kg"]                                        # 2 x 96 kg volumetric
    assert d(big)["chargeable_kg"] == pytest.approx(2 * 100 * 80 * 60 / 5000, abs=0.01)
    assert _est(schema, ship_service="air_freight")["settings"]["ship_service"] == "air_freight"


def test_the_origin_must_be_the_tables_and_a_missing_rate_is_named(schema):
    est = _est(schema, ship_from="CN Ningbo")
    assert "the rate table is for CN Shanghai" in CE.dhl_suggestion(est, est["iterations"][0], schema, 20.0)[1]
    est = _est(schema)
    est["fx"] = None
    assert "no exchange rate" in CE.dhl_suggestion(est, est["iterations"][0], schema, 20.0)[1]


def test_the_new_settings_are_validated(schema):
    est = CE.default_estimate(schema, "alpha")
    for bad in ({"ship_parcels": 0}, {"ship_parcels": 51}, {"ship_box_cm": [10, 10]}, {"ship_box_cm": [10, 10, 0]},
                {"ship_service": "sea"}):
        with pytest.raises(CE.EstimateError):
            CE.normalize_estimate({**est, "settings": {**est["settings"], **bad}}, schema, "alpha")
    ok = CE.normalize_estimate({**est, "settings": {**est["settings"], "ship_parcels": 3, "ship_box_cm": [60, 50, 40],
                                                    "ship_to": {"country": "de", "city": "Munich", "postal": "80331"}}}, schema, "alpha")
    assert ok["settings"]["ship_parcels"] == 3 and ok["settings"]["ship_box_cm"] == [60.0, 50.0, 40.0]
    assert ok["settings"]["ship_to"] == {"country": "DE", "city": "Munich", "postal": "80331"}


# ── the next unit ────────────────────────────────────────────────────────────────────────────────────────────────────

def _priced(schema, bench="dyno"):
    est = _est(schema)
    for it in est["iterations"]:
        it["bench"] = bench
        _fill(it["lines"]["tooling"], 5000.0, 4)
        _fill(it["lines"]["manufacturing"], 3000.0, 6)
        _fill(it["lines"]["testing"], 800.0, 1)
        _fill(it["lines"]["shipping"], 1500.0, 2)
    return est


def test_the_next_unit_is_one_motor_without_tooling_and_testing_only_when_ticked(schema):
    est = _priced(schema)
    est["next_unit"]["shipping"].update({"status": "filled", "amount": 400.0, "currency": "EUR", "source": "manual", "weeks": 2.0})
    nu = CE.next_unit_of(est, schema)
    assert nu["available"] and nu["manufacturing"] == 3000.0 and nu["testing"] is None and nu["shipping"] == 400.0
    assert nu["total"] == 3400.0 and nu["weeks"] == 8.0 and nu["basis_iteration"] == 2 and nu["overridden"] is False
    est["next_unit"]["include_testing"] = True
    nu = CE.next_unit_of(est, schema)
    assert nu["testing"] == 800.0 and nu["total"] == 4200.0 and nu["weeks"] == 9.0
    est["iterations"][-1]["bench"] = "none"
    assert CE.next_unit_of(est, schema)["total"] == 3400.0                  # no bench: nothing to test
    assert CE.compute(est, schema)["next_unit"] == CE.next_unit_of(est, schema)


def test_the_admin_can_override_the_price_and_the_weeks_and_nothing_is_invented(schema):
    est = _priced(schema)
    nu = CE.next_unit_of(est, schema)
    assert nu["available"] is False and "shipping" in nu["reason"]            # no next-unit shipping yet: not guessed
    est["next_unit"]["price"] = 3900.0
    est["next_unit"]["weeks"] = 7.0
    nu = CE.next_unit_of(est, schema)
    assert nu["available"] and nu["total"] == 3900.0 and nu["overridden"] is True and nu["weeks"] == 7.0 and nu["derived"] is None
    empty = CE.default_estimate(schema, "alpha")
    assert CE.next_unit_of(empty, schema)["available"] is False


def test_the_next_unit_shipping_is_automatic_for_one_motor_and_follows_a_refresh(schema):
    est = _priced(schema)
    data = {"motor_mass_kg": 20.0, "motor_mass_basis": "complete", "target_mass_kg": None, "productions": [], "estimates": []}
    out, report = CE.suggest(est, schema, data, today="2026-11-22")
    line = out["next_unit"]["shipping"]
    assert line["status"] == "filled" and line["source"] == "dhl" and line["source_ref"]["dhl"]["parcels"] == 1
    assert line["source_ref"]["dhl"]["weight_kg"] == 45.0 and line["weeks"] == 2.0         # 1 x (26 + 15) = 41 -> 45 kg
    assert any(f["line"] == "next_unit.shipping" for f in report["filled"])
    assert out["iterations"][0]["lines"]["shipping"]["amount"] == 1500.0                    # the owner's number is untouched
    out["next_unit"]["shipping"]["amount"] = 1.0
    refreshed, changes = CE.refresh_dhl(out, schema, 30.0, "complete", today="2026-11-23")
    assert refreshed["next_unit"]["shipping"]["amount"] > 1.0 and refreshed["next_unit"]["shipping"]["by"] == "auto"
    assert "next_unit" in CE.auto_cells(out, refreshed)
    manual = json.loads(json.dumps(out))
    manual["next_unit"]["shipping"].update({"source": "manual", "amount": 777.0})
    assert CE.refresh_dhl(manual, schema, 30.0, "complete")[0]["next_unit"]["shipping"]["amount"] == 777.0   # a typed number stays


def test_the_next_unit_is_stored_and_normalised(schema):
    est = _priced(schema)
    est["next_unit"].update({"include_testing": True, "price": 3900.0, "weeks": 7.0, "note": "n"})
    again = CE.normalize_estimate(json.loads(json.dumps(est)), schema, "alpha")
    assert again["next_unit"]["include_testing"] is True and again["next_unit"]["price"] == 3900.0
    old = json.loads(json.dumps(est))
    old.pop("next_unit")
    assert CE.normalize_estimate(old, schema, "alpha")["next_unit"] == CE.default_next_unit("EUR")       # an older stored estimate
    with pytest.raises(CE.EstimateError):
        CE.normalize_estimate({**est, "next_unit": "x"}, schema, "alpha")
    assert any(c["path"] == "next_unit.price" for c in CE.diff(_priced(schema), est))


# ── the customer quote ───────────────────────────────────────────────────────────────────────────────────────────────

def test_the_quote_carries_the_next_unit_with_contingency_and_the_dap_note_only_when_computable(schema):
    est = _priced(schema)
    before = Q.build_quote(est, schema, "2026-11-22")
    assert before["next_unit"] is None and before["excluded"] is None and tuple(before) == Q.QUOTE_KEYS      # as it read before
    est["next_unit"]["shipping"].update({"status": "filled", "amount": 400.0, "currency": "EUR", "source": "manual", "weeks": 2.0})
    quote = Q.build_quote(est, schema, "2026-11-22")
    assert quote["next_unit"] == {"price": 3740, "weeks": 8}                  # 3400 x 1.10 (the contingency is folded in, never named)
    assert quote["excluded"] == "Duties and import VAT are not included."
    assert quote["total_price"] == before["total_price"] and quote["iterations"] == before["iterations"]
    assert "400" not in json.dumps(quote["next_unit"]) and set(quote["next_unit"]) == set(Q.NEXT_UNIT_KEYS)


def test_the_mcp_guard_refuses_the_next_unit_and_the_shipping_settings():
    from motor_ai_sim import mcp_tools as T
    for leak in ({"next_unit": {"price": 1}}, {"ship_parcels": 2}, {"ship_box_cm": [1, 2, 3]}, {"ship_service": "express"}):
        with pytest.raises(AssertionError, match="commercial field leaked"):
            T.assert_no_commercial(leak)
