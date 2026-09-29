"""Pricing data (motor_ai_sim.usage_stats): daily/monthly aggregation, cost
basis math, activity counters, storage sampling, monthly export, admin-only."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim import job_usage as U
from motor_ai_sim import usage_stats as US
from motor_ai_sim.api import app

client = TestClient(app)
T0 = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc).timestamp()


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setenv("CLUSTER_MONITOR_DIR", str(tmp_path / "cluster"))
    yield


def _job(rid, user, kind, cpu_s, t_end, wall=100.0, status="done", client="web", wait=0.0):
    U.record({"run_id": rid, "ts_start": t_end - wall, "ts_end": t_end, "user": user,
              "client": client, "kind": kind, "machine": "L155", "node": "eu1",
              "wall_s": wall, "cpu_s": cpu_s, "peak_rss": 1, "status": status,
              "cpu_method": "exclusive", "wait_s": wait})


def test_kind_category():
    assert US.kind_category("coupled") == "coupled"
    assert US.kind_category("duty_cycle") == "coupled"
    assert US.kind_category("em") == "em"
    assert US.kind_category("rotor_stress") == "mechanical"
    assert US.kind_category("optimizer") == "optimizer"
    assert US.kind_category("xyz") == "other"


def test_peak_concurrent():
    assert US.peak_concurrent([(0, 10), (5, 15), (9, 12), (15, 20)]) == 3
    assert US.peak_concurrent([(0, 5), (5, 10)]) == 1


def test_daily_aggregation():
    US._db().close()
    _job("a", "Alice@x.com", "em", 3600, T0, wall=1000, wait=30)
    _job("b", "alice@x.com", "coupled", 1800, T0 + 500, wall=1000, status="failed")
    _job("c", "alice@x.com", "thermal", 60, T0 + 5000, wall=10, status="stopped",
         client="claude")
    d = US.daily(T0 - 86400, T0 + 86400)
    assert len(d) == 1
    r = d[0]
    assert r["user"] == "alice@x.com" and r["day"] == "2026-09-10"
    assert r["jobs"] == 3 and r["failed"] == 1 and r["stopped"] == 1 and r["agent_runs"] == 1
    assert r["cpu_s"]["em"] == 3600 and r["cpu_s"]["coupled"] == 1800
    assert r["peak_concurrent"] == 2 and r["wait_s"] == 30


def test_cost_basis_math_and_defaults():
    b = US.get_basis()
    # eu1 = AX42-1: EUR 100 / (16 threads x 730 h)
    assert b["eur_per_server_month"] == 100.0 and b["cores_per_server"] == 16
    assert b["eur_per_gb_month"] == 0.0
    assert b["eur_per_cpu_hour"] == pytest.approx(100 / (16 * 730), abs=1e-6)
    b2 = US.set_basis({"eur_per_server_month": 146, "cores_per_server": 20,
                       "eur_per_gb_month": 0.1})
    assert b2["eur_per_cpu_hour"] == pytest.approx(0.01)
    assert US.get_basis()["eur_per_gb_month"] == 0.1
    with pytest.raises(ValueError):
        US.set_basis({"cores_per_server": 0})
    with pytest.raises(ValueError):
        US.set_basis({"eur_per_gb_month": -1})


def test_monthly_totals_and_cost(tmp_path):
    US._db().close()
    _job("a", "bob@acme.com", "em", 7300, T0)               # 2.0278 CPU-h
    _job("b", "bob@acme.com", "optimizer", 3600, T0 + 86400)
    _job("x", "bob@acme.com", "em", 99999, T0 + 40 * 86400)   # next month
    US.note("bob@acme.com", "report_pdf", ts=T0)
    US.note("bob@acme.com", "report_pdf", ts=T0)
    US.note("bob@acme.com", "mcp_call", "simulate", ts=T0)
    US.note("bob@acme.com", "mcp_429", ts=T0)
    US.note("owner@motres.si", "catalog_view", "L155", ts=T0 + 3 * 86400)
    ws = tmp_path / "ws"
    (ws / "w1" / "dies" / "d1" / "runs").mkdir(parents=True)
    (ws / "w1" / "dies" / "d1" / "die.yaml").write_bytes(b"x" * 1_000_000)
    (ws / "w1" / "dies" / "d1" / "runs" / "r.pkl").write_bytes(b"x" * 4_000_000)
    (ws / "w1" / "motor_config.yaml").write_bytes(b"x" * 500_000)
    (ws / "w1" / "report.pdf").write_bytes(b"x" * 200_000)
    US.sample_storage(ws, ts=T0, owners={"w1": "bob@acme.com"})
    basis = {"eur_per_server_month": 146, "cores_per_server": 20, "eur_per_gb_month": 1000}
    reg = {"bob@acme.com": {"email": "bob@acme.com", "tier": "pro", "created": "2026-09-01"},
           "owner@motres.si": {"email": "owner@motres.si", "tier": "admin"}}
    rep = US.monthly("2026-09", basis=basis, registry=reg, logins={"bob@acme.com": 4})
    by = {r["account"]: r for r in rep["accounts"]}
    bob = by["bob@acme.com"]
    assert bob["jobs"] == 2 and bob["cpu_h"] == pytest.approx(10900 / 3600, abs=1e-3)
    assert bob["cpu_h_optimizer"] == 1.0 and bob["active_days"] == 2
    assert bob["report_pdf"] == 2 and bob["mcp_calls"] == 1 and bob["mcp_429"] == 1
    assert bob["mcp_by_tool"] == {"simulate": 1} and bob["logins"] == 4
    assert bob["domain"] == "acme.com" and bob["plan"] == "free" and bob["created"] == "2026-09-01"
    assert bob["storage_results_gb"] == pytest.approx(0.004)
    assert bob["storage_dies_gb"] == pytest.approx(0.001)
    assert bob["storage_configs_gb"] == pytest.approx(0.0005)
    assert bob["storage_reports_gb"] == pytest.approx(0.0002)
    assert bob["cost_cpu_eur"] == pytest.approx(bob["cpu_h"] * 0.01, abs=1e-4)
    assert bob["cost_storage_eur"] == pytest.approx(5.7, abs=1e-4)
    assert by["owner@motres.si"]["plan"] == "internal"
    assert rep["totals"]["jobs"] == 2
    # CSV of the same table
    rows = list(csv.DictReader(io.StringIO(US.monthly_csv(rep))))
    assert {r["account"] for r in rows} == {"bob@acme.com", "owner@motres.si"}
    assert "simulate=1" in [r for r in rows if r["account"] == "bob@acme.com"][0]["mcp_by_tool"]


def test_event_mapping():
    assert US.event_for("GET", "/api/family/report/D/C") == ("report_pdf", "")
    assert US.event_for("GET", "/api/family/report/progress") is None
    assert US.event_for("GET", "/api/family/datasheet/D/C") == ("datasheet_export", "")
    assert US.event_for("POST", "/api/fusion/import") == ("fusion_import", "")
    assert US.event_for("GET", "/api/fusion/params.csv") == ("fusion_export", "")
    assert US.event_for("POST", "/api/catalog/m1/load") == ("catalog_view", "m1")
    assert US.event_for("GET", "/api/simulation/x") is None


def test_cli_monthly_json(capsys):
    US._db().close()
    _job("a", "c@x.com", "em", 3600, T0)
    assert US._main(["monthly", "2026-09"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["month"] == "2026-09" and rep["accounts"][0]["account"] == "c@x.com"
    assert US._main(["monthly", "2026-09", "--csv"]) == 0
    assert capsys.readouterr().out.startswith("account,")


@pytest.mark.parametrize("method,path", [
    ("get", "/api/admin/usage/monthly"), ("get", "/api/admin/usage/cost_basis"),
    ("put", "/api/admin/usage/cost_basis"), ("get", "/api/admin/usage/daily")])
def test_pricing_admin_only(monkeypatch, method, path):
    monkeypatch.setattr(auth, "_is_admin_caller",
                        lambda a: (False, {"uid": "u", "email": "u@x", "tier": "pro"}))
    assert getattr(client, method)(path).status_code == 403


def test_pricing_routes(monkeypatch):
    monkeypatch.setattr(auth, "_is_admin_caller",
                        lambda a: (True, {"uid": "a", "email": "a@x", "tier": "admin"}))
    _job("a", "c@x.com", "em", 3600, T0)
    assert client.put("/api/admin/usage/cost_basis",
                      json={"eur_per_server_month": 73, "cores_per_server": 10}).json()[
        "eur_per_cpu_hour"] == pytest.approx(0.01)
    assert client.put("/api/admin/usage/cost_basis", json={"cores_per_server": 0}).status_code == 400
    j = client.get("/api/admin/usage/monthly?month=2026-09").json()
    assert j["accounts"][0]["cost_cpu_eur"] == pytest.approx(0.01)
    c = client.get("/api/admin/usage/monthly?month=2026-09&format=csv")
    assert c.headers["content-type"].startswith("text/csv") and "c@x.com" in c.text
    assert client.get("/api/admin/usage/monthly?month=2026-13").status_code == 400
