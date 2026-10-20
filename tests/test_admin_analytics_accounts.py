"""Admin account and signed NDA analytics, independent of Umami."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.routes import admin_analytics_accounts as AA

ADMIN = "admin@example.test"
ALICE = "alice@example.test"
BOB = "bob@example.test"
CAROL = "carol@example.test"
OLD = "old@example.test"
NOW = datetime(2026, 10, 20, 12, 30, tzinfo=timezone.utc).timestamp()
client = TestClient(app)


@pytest.fixture
def stores(tmp_path, monkeypatch):
    from motor_ai_sim import auth, users

    monkeypatch.setenv("AUTH_SECRET", "test-analytics-secret")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("MOTRES_LEGAL_ROOT", str(tmp_path / "legal"))
    users._USERS_FILE.write_text(json.dumps({
        ADMIN: {"role": "admin", "created": "2026-09-01T00:00:00Z"},
        ALICE: {"role": "user", "created": "2026-10-20T11:30:00Z",
                "nda": {"signed": True, "by": "online", "date": "2026-10-20", "record_id": "n1"}},
        BOB: {"role": "user", "created": "2026-10-17T12:30:00Z",
              "nda": {"signed": True, "by": ADMIN, "date": "2026-10-01"}},
        CAROL: {"role": "user", "created": "2026-10-01T12:30:00Z",
                "nda": {"signed": True, "by": ADMIN, "date": "2026-10-20"}},
        OLD: {"role": "user", "created": "2026-09-19T12:30:00Z",
              "nda": {"signed": False, "by": ADMIN, "date": ""}},
    }), encoding="utf-8")
    legal = tmp_path / "legal"
    legal.mkdir()
    rows = [
        {"id": "n1", "user": ALICE, "email": ALICE, "ts": NOW - 3600, "iso": "2026-10-20T11:30:00Z"},
        # Repeated records and a changed e-mail field still resolve to one account by record.user.
        {"id": "n2", "user": ALICE, "email": "alias@example.test", "ts": NOW - 1800, "iso": "2026-10-20T12:00:00Z"},
        {"id": "n3", "user": BOB, "email": BOB, "ts": NOW - 3 * 86400, "iso": "2026-10-17T12:30:00Z"},
        {"id": "n4", "user": BOB, "email": BOB, "ts": NOW - 2 * 86400, "iso": "2026-10-18T12:30:00Z"},
        # A signature remains evidence of signing even if the account access flag was later revoked.
        {"id": "n5", "user": OLD, "email": OLD, "ts": NOW - 31 * 86400, "iso": "2026-09-19T12:30:00Z"},
    ]
    (legal / "nda_signatures.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    from motor_ai_sim import nda
    assert len(nda.read_records(0)) == 5
    return {"admin": {"Authorization": f"Bearer {users.issue_token(ADMIN)}"},
            "alice": {"Authorization": f"Bearer {users.issue_token(ALICE)}"},
            "legal": legal}


@pytest.mark.parametrize("range_key,expected_accounts,expected_nda", [
    ("24h", 1, 2),
    ("7d", 2, 3),
    ("30d", 3, 3),
])
def test_counts_per_range_and_deduplication(stores, range_key, expected_accounts, expected_nda):
    result = AA.build_accounts_analytics(range_key, now=NOW)
    assert result["range"] == range_key
    assert result["accounts"] == {"total": 5, "new": expected_accounts}
    assert result["nda"] == {"total": 4, "new": expected_nda,
                              "online_total": 3, "admin_flag_total": 2}
    assert len(result["series"]) == (25 if range_key == "24h" else (8 if range_key == "7d" else 31))
    assert sum(point["accounts"] for point in result["series"]) == expected_accounts
    assert sum(point["nda"] for point in result["series"]) == expected_nda
    assert all(point["t"].endswith(":00:00") for point in result["series"])


def test_endpoint_has_counts_only_and_does_not_need_umami(stores, monkeypatch):
    monkeypatch.setattr(AA.time, "time", lambda: NOW)
    # The append-only reader's zero limit deliberately means all records, even beyond its default 200.
    with (stores["legal"] / "nda_signatures.jsonl").open("a", encoding="utf-8") as fh:
        for index in range(205):
            fh.write(json.dumps({"id": f"repeat-{index}", "user": ALICE,
                                 "ts": NOW - 3600, "iso": "2026-10-20T11:30:00Z"}) + "\n")
    for key in ("UMAMI_API_URL", "UMAMI_WEBSITE_ID", "UMAMI_USERNAME", "UMAMI_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    response = client.get("/api/admin/analytics/accounts?range=7d", headers=stores["admin"])
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["accounts"] == {"total": 5, "new": 2}
    assert payload["nda"] == {"total": 4, "new": 3, "online_total": 3, "admin_flag_total": 2}
    for private_value in (ADMIN, ALICE, BOB, CAROL, OLD, "alias@example.test"):
        assert private_value not in response.text


def test_endpoint_requires_admin(stores):
    assert client.get("/api/admin/analytics/accounts").status_code == 401
    assert client.get("/api/admin/analytics/accounts", headers=stores["alice"]).status_code == 403


def test_bad_range_is_rejected(stores):
    response = client.get("/api/admin/analytics/accounts?range=1y", headers=stores["admin"])
    assert response.status_code == 400


def test_exact_window_edges_epoch_iso_offset_and_invalid_time(stores, monkeypatch):
    from motor_ai_sim import nda, users

    start = NOW - 7 * 86400
    monkeypatch.setattr(users, "_load", lambda: {
        "start@example.test": {"created": start},
        "before@example.test": {"created": start - 0.001},
        "now@example.test": {"created": "2026-10-20T14:30:00+02:00"},
        "future@example.test": {"created": NOW + 0.001},
        "invalid@example.test": {"created": "not-a-date",
                                 "nda": {"signed": True, "by": ADMIN, "date": "2026-10-20"}},
    })
    monkeypatch.setattr(nda, "read_records", lambda _limit: [
        {"user": "start@example.test", "ts": start},
        {"user": "before@example.test", "ts": start - 0.001},
        {"user": "now@example.test", "iso": "2026-10-20T14:30:00+02:00"},
        {"user": "future@example.test", "ts": NOW + 0.001},
        {"user": "invalid@example.test", "iso": "not-a-date"},
    ])
    result = AA.build_accounts_analytics("7d", now=NOW)
    assert result["accounts"] == {"total": 5, "new": 2}
    assert result["nda"] == {"total": 5, "new": 2, "online_total": 5, "admin_flag_total": 1}
    assert sum(point["accounts"] for point in result["series"]) == 2
    assert sum(point["nda"] for point in result["series"]) == 2
