"""/api/catalog/cards and the device-card writer: users read, ADMINS edit.

Owner decision 2026-09-28 (unified catalogues stage 1): editing a reference
catalogue is admin only.  Before this, ``POST /api/controller/devices`` wrote
``config/devices/<part>.yaml`` for ANY caller — a card the loss model quotes.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim import bearings as brg
from motor_ai_sim.api import app
from motor_ai_sim.inverter import devices as dv

ROOT = Path(__file__).resolve().parents[1]
REAL_USERS = ROOT / "config" / "users.json"
REAL_DEVICES = ROOT / "config" / "devices"
ADMIN = "admin@example.com"
USER = "engineer@example.com"

client = TestClient(app)


@pytest.fixture()
def devdir(tmp_path, monkeypatch):
    """A COPY of the device folder — a write must never reach config/."""
    d = tmp_path / "devices"
    d.mkdir()
    for p in REAL_DEVICES.glob("*.yaml"):
        shutil.copy2(p, d / p.name)
    monkeypatch.setattr(dv, "_DIR", d)
    monkeypatch.setattr(brg, "_LIB_PATH", ROOT / "config" / "bearings_library.yaml")
    monkeypatch.setattr(brg, "_library", None)
    return d


@pytest.fixture()
def accounts(tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    users_file = tmp_path / "users.json"
    if REAL_USERS.exists():
        shutil.copy2(REAL_USERS, users_file)
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    U.create_user(ADMIN, "password-admin", tier="admin", name="Admin")
    U.create_user(USER, "password-user", tier="pro", name="Engineer")
    return {"admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
            "user": {"Authorization": f"Bearer {U.issue_token(USER)}"},
            "anon": {}}


def _card_yaml(devdir: Path) -> str:
    doc = yaml.safe_load((devdir / "WCMS900B170E53.yaml").read_text(encoding="utf-8"))
    doc["part"] = "TESTPART-1"
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# writes: admin only, on BOTH doors
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", ["/api/controller/devices",
                                 "/api/catalog/cards/device"])
def test_non_admin_cannot_write_a_device_card(url, devdir, accounts):
    body = {"card_yaml": _card_yaml(devdir), "overwrite": True}
    r = client.post(url, json=body, headers=accounts["user"])
    assert r.status_code == 403, r.text
    r = client.post(url, json=body, headers=accounts["anon"])
    assert r.status_code == 401, r.text
    assert not (devdir / "TESTPART-1.yaml").exists()


@pytest.mark.parametrize("url", ["/api/controller/devices",
                                 "/api/catalog/cards/device"])
def test_admin_writes_a_device_card(url, devdir, accounts):
    r = client.post(url, json={"card_yaml": _card_yaml(devdir), "overwrite": True},
                    headers=accounts["admin"])
    assert r.status_code == 200, r.text
    assert (devdir / "TESTPART-1.yaml").is_file()
    # an invalid card is still refused for the admin — the validator did not move
    bad = client.post(url, json={"card": {"part": "HALF"}}, headers=accounts["admin"])
    assert bad.status_code == 422


# ---------------------------------------------------------------------------
# reads: everybody
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("who", ["user", "anon", "admin"])
def test_everybody_reads_the_catalogue(who, devdir, accounts):
    h = accounts[who]
    kinds = client.get("/api/catalog/cards", headers=h)
    assert kinds.status_code == 200
    assert {k["kind"] for k in kinds.json()["kinds"]} == {"bearing", "lubricant", "device"}
    lst = client.get("/api/catalog/cards/bearing", headers=h).json()["cards"]
    ids = {c["id"] for c in lst}
    assert "61811-2RS1" in ids and "71910 CE/HCP4A" in ids
    one = client.get("/api/catalog/cards/bearing/card",
                     params={"id": "618/8-2Z"}, headers=h)
    assert one.status_code == 200
    assert one.json()["body"]["C_kn"] == 1.33
    assert one.json()["prov"]["C_kn"]["verify"] is True
    dev = client.get("/api/catalog/cards/device", headers=h).json()["cards"]
    assert any(c["id"] == "WCMS900B170E53" for c in dev)


def test_unknown_kind_and_card_are_404(devdir):
    assert client.get("/api/catalog/cards/steel").status_code == 404
    assert client.get("/api/catalog/cards/bearing/card",
                      params={"id": "NOPE"}).status_code == 404


def test_the_motor_catalog_still_answers():
    assert client.get("/api/catalog").status_code == 200


# ---------------------------------------------------------------------------
# used by
# ---------------------------------------------------------------------------

def test_used_by_names_the_machines(tmp_path, monkeypatch, devdir):
    from motor_ai_sim.routes import family as fam
    root = tmp_path / "dies"
    (root / "DIE A").mkdir(parents=True)
    (root / "DIE A" / "die.yaml").write_text("name: DIE A\n", encoding="utf-8")
    (root / "DIE A" / "L40.yaml").write_text(yaml.safe_dump({
        "name": "L40", "die": "DIE A",
        "bearings": {"A": {"card": "61811-2RS1", "grease": "LGHP_2"},
                     "B": {"card": "61811-2RS1"}},
        "controller": {"device": "IMCQ120R004M2H"}}), encoding="utf-8")
    (root / "DIE A" / "L60.yaml").write_text(yaml.safe_dump({
        "name": "L60", "die": "DIE A"}), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    b = client.get("/api/catalog/cards/bearing/used_by",
                   params={"id": "61811-2RS1"}).json()["machines"]
    assert b == [{"die": "DIE A", "config": "L40", "count": 2, "where": "bearings"}]
    lub = client.get("/api/catalog/cards/lubricant/used_by",
                     params={"id": "LGHP_2"}).json()["machines"]
    assert [m["config"] for m in lub] == ["L40"]
    d = client.get("/api/catalog/cards/device/used_by",
                   params={"id": "IMCQ120R004M2H"}).json()["machines"]
    assert d == [{"die": "DIE A", "config": "L40", "count": 1, "where": "controller"}]
    none = client.get("/api/catalog/cards/device/used_by",
                      params={"id": "WCMS900B170E53"}).json()["machines"]
    assert none == []
