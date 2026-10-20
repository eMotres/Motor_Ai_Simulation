"""The separate TOOLING / FIXTURES order list next to the motor BOM (owner 2026-10-19): ``?list=tooling`` on bom / costs / procurement / parts.

Same rows, columns, prices and statuses as the motor BOM; its own files (bom_tooling.json / costs_tooling.json); quantities are totals (the order
quantity is 1, price x quantity per line); prices stay behind the cost-access rule; the motor BOM is untouched.
"""
from __future__ import annotations

import pytest

from tests.test_production_access import CONFIG, DIE, ENGINEER, OEM_NDA, prod_env  # noqa: F401  (fixture)

BASE = f"/api/production/{DIE}/{CONFIG}"


def _bi(en: str) -> dict:
    return {"en": en, "zh": en, "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}}


@pytest.fixture()
def prod(prod_env):
    c, admin, eng = prod_env["client"], prod_env["admin"], prod_env["engineer"]
    r = c.post(f"/api/admin/production/{DIE}/{CONFIG}", headers=admin, json={
        "title": _bi("Tooling BOM test"), "order_quantity": 100, "share_costs_with_oem": False,
        "members": [{"email": ENGINEER, "role": "engineer"}, {"email": OEM_NDA, "role": "oem"}]})
    assert r.status_code in (200, 201), r.text
    motor = c.post(f"{BASE}/parts", headers=eng, json={"name": _bi("Motor part"), "kind": "made", "qty_per_motor": 6}).json()
    t1 = c.post(f"{BASE}/parts?list=tooling", headers=eng, json={"name": _bi("Press plate"), "kind": "made", "qty_per_motor": 2}).json()
    t2 = c.post(f"{BASE}/parts?list=tooling", headers=eng, json={"name": _bi("Press base"), "kind": "made", "qty_per_motor": 1}).json()
    return {**prod_env, "motor": motor, "t1": t1, "t2": t2}


def test_tooling_parts_go_to_their_own_list(prod):
    c, eng = prod["client"], prod["engineer"]
    motor = c.get(f"{BASE}/bom", headers=eng).json()["rows"]
    tool = c.get(f"{BASE}/bom?list=tooling", headers=eng).json()["rows"]
    assert [r["part_id"] for r in motor] == [prod["motor"]["id"]]
    assert [r["part_id"] for r in tool] == [prod["t1"]["id"], prod["t2"]["id"]]
    assert [r["qty_per_motor"] for r in tool] == [2, 1]
    assert (prod["root"] / "CIANO14_40_new" / "L12" / "bom_tooling.json").exists() or any(prod["root"].rglob("bom_tooling.json"))


def test_tooling_prices_are_price_times_quantity_not_times_motors(prod):
    c, eng = prod["client"], prod["engineer"]
    r = c.put(f"{BASE}/costs?list=tooling", headers=eng, json={"rows": [
        {"part_id": prod["t1"]["id"], "unit_price": 50.0, "currency": "CNY"},
        {"part_id": prod["t2"]["id"], "unit_price": 200.0, "currency": "CNY"}], "extras": [], "margin_pct": 0.0})
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["parts_subtotal"] == 300.0          # 2 x 50 + 1 x 200
    rows = {x["part_id"]: x for x in c.get(f"{BASE}/bom?list=tooling", headers=eng).json()["rows"]}
    assert rows[prod["t1"]["id"]]["line_total"] == 100.0           # not multiplied by the 100 motors
    assert rows[prod["t2"]["id"]]["line_total"] == 200.0
    assert c.get(f"{BASE}/bom?list=tooling", headers=eng).json()["summary"]["order_cost_total"] == 300.0
    # the motor BOM and its costs are untouched
    assert c.get(f"{BASE}/costs", headers=eng).json()["rows"][0]["unit_price"] is None
    assert c.get(f"{BASE}/bom", headers=eng).json()["summary"]["parts_subtotal"] == 0.0


def test_prices_of_the_tooling_list_stay_hidden_from_the_factory(prod):
    c, eng, oem = prod["client"], prod["engineer"], prod["oem_nda"]
    c.put(f"{BASE}/costs?list=tooling", headers=eng, json={"rows": [{"part_id": prod["t1"]["id"], "unit_price": 50.0}], "extras": [], "margin_pct": 0})
    assert c.get(f"{BASE}/costs?list=tooling", headers=oem).status_code == 403
    rows = c.get(f"{BASE}/bom?list=tooling", headers=oem).json()["rows"]
    assert rows and all("unit_price" not in r and "line_total" not in r for r in rows)


def test_tooling_procurement_lists_the_tooling_parts_only(prod):
    c, eng = prod["client"], prod["engineer"]
    pid = prod["t1"]["id"]
    assert c.put(f"{BASE}/procurement/{pid}", headers=eng, json={"qty_ordered": 2, "lead_time_days": 14, "need_by": "2026-11-02"}).status_code == 200
    rows = c.get(f"{BASE}/procurement?list=tooling&today=2026-10-19", headers=eng).json()["rows"]
    assert {r["part_id"] for r in rows} == {prod["t1"]["id"], prod["t2"]["id"]}
    by = {r["part_id"]: r for r in rows}
    assert by[pid]["status"] == "to_order" and by[pid]["qty_needed"] == 2
    assert prod["t1"]["id"] not in {r["part_id"] for r in c.get(f"{BASE}/procurement", headers=eng).json()["rows"]}


def test_unknown_list_is_refused(prod):
    assert prod["client"].get(f"{BASE}/bom?list=nope", headers=prod["engineer"]).status_code == 422
