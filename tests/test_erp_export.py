"""MOTRES ERP export (2026-10-18): companies catalogue + production motor prices, read-only, service token or admin."""
from __future__ import annotations

import json

from tests.test_production_access import ADMIN, ENGINEER, prod_env  # noqa: F401  (fixture)

TOKEN = "erp-sync-test-token-0123456789"


def _seed(env, tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_DIRECTORY_ROOT", str(tmp_path / "directory"))
    monkeypatch.setenv("ERP_SYNC_TOKEN", TOKEN)
    from motor_ai_sim.directory import service as D
    c = D.create_company({"legal_name": "NINGBO ITO MOTOR", "country": "cn", "roles": ["supplier"],
                          "website": "https://ito.example"})
    p = D.create_person({"name": "Chen", "emails": ["chen@ito.example"]})
    D.create_affiliation(c["id"], p["id"], title="Sales", status="confirmed", source="manual",
                         confirmed_by=ADMIN, actor=ADMIN)
    root = tmp_path / "production" / "D" / "L1"
    root.mkdir(parents=True)
    (root / "production.json").write_text(json.dumps({"die": "D", "config": "L1", "title": {"en": "Pilot"},
                                                      "order_quantity": 100, "status": "active"}))
    (root / "bom.json").write_text(json.dumps({"rows": [{"part_id": "p1", "qty_per_motor": 1},
                                                        {"part_id": "p2", "qty_per_motor": 6}]}))
    (root / "costs.json").write_text(json.dumps({"rows": [{"part_id": "p1", "unit_price": 100, "currency": "CNY"},
                                                          {"part_id": "p2", "unit_price": 40, "currency": "CNY"}],
                                                 "extras": [{"label": "assembly", "amount": 35}], "margin_pct": 30}))
    return c


def test_token_or_admin_only(prod_env, tmp_path, monkeypatch):
    _seed(prod_env, tmp_path, monkeypatch)
    c = prod_env["client"]
    for url in ("/api/erp-export/directory", "/api/erp-export/motor-prices"):
        assert c.get(url).status_code == 401
        assert c.get(url, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get(url, headers=prod_env["engineer"]).status_code == 403
        assert c.get(url, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
        assert c.get(url, headers=prod_env["admin"]).status_code == 200


def test_no_token_configured_means_admin_only(prod_env, tmp_path, monkeypatch):
    _seed(prod_env, tmp_path, monkeypatch)
    monkeypatch.delenv("ERP_SYNC_TOKEN")
    r = prod_env["client"].get("/api/erp-export/directory", headers={"Authorization": "Bearer "})
    assert r.status_code == 401


def test_directory_and_prices_content(prod_env, tmp_path, monkeypatch):
    comp = _seed(prod_env, tmp_path, monkeypatch)
    h = {"Authorization": f"Bearer {TOKEN}"}
    d = prod_env["client"].get("/api/erp-export/directory", headers=h).json()
    row = next(x for x in d["companies"] if x["id"] == comp["id"])
    assert row["legal_name"] == "NINGBO ITO MOTOR" and row["country"] == "CN" and row["roles"] == ["supplier"]
    assert [p["name"] for p in row["people"]] == ["Chen"] and row["people"][0]["title"] == "Sales"
    assert "iban" not in json.dumps(d).lower()
    m = prod_env["client"].get("/api/erp-export/motor-prices", headers=h).json()
    pr = next(x for x in m["productions"] if x["id"] == "D/L1")
    # unit_price is the price per motor of the BOM line: 100 + 40 + extras 35 = 175; / (1 - 0.30) = 250
    assert pr["unit_cost_per_motor"] == 175 and pr["factory_price_per_motor"] == 250
    assert pr["currency"] == "CNY" and pr["order_quantity"] == 100 and pr["missing_prices_count"] == 0
    assert pr["title"] == "Pilot"
