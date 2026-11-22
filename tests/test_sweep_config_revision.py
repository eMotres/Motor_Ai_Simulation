"""The sweep config carries a server-issued revision and a save states the one it saw (Astra review of #772, 2026-11-21):
two tabs that read the same base cannot both win, and a refused save leaves the stored copy untouched."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim.routes import sweep_config as SC


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "_STORE", tmp_path / "sweep_config.json", raising=False)
    app = FastAPI()
    app.include_router(SC.router)
    return TestClient(app)


def _cfg(tag, at):
    return {"variations": {"tooth_width": {"mode": "optimize", "min": 1, "max": 2, "tag": tag}}, "updatedAt": at}


def test_nothing_stored_is_revision_zero(client):
    r = client.get("/api/sweep/config").json()
    assert r == {"config": None, "revision": 0}


def test_every_save_advances_the_revision_and_get_returns_it(client):
    assert client.put("/api/sweep/config?expected_revision=0", json=_cfg("a", 1)).json() == {"status": "ok", "revision": 1}
    assert client.put("/api/sweep/config?expected_revision=1", json=_cfg("b", 2)).json() == {"status": "ok", "revision": 2}
    got = client.get("/api/sweep/config").json()
    assert got["revision"] == 2 and got["config"]["variations"]["tooth_width"]["tag"] == "b"


def test_two_tabs_on_the_same_base_cannot_both_win(client):
    client.put("/api/sweep/config?expected_revision=0", json=_cfg("base", 1))
    base = client.get("/api/sweep/config").json()["revision"]
    first = client.put(f"/api/sweep/config?expected_revision={base}", json=_cfg("tab1", 2))
    second = client.put(f"/api/sweep/config?expected_revision={base}", json=_cfg("tab2", 3))
    assert first.status_code == 200
    assert second.status_code == 409
    body = second.json()
    assert body["status"] == "conflict" and body["revision"] == first.json()["revision"]
    assert body["config"]["variations"]["tooth_width"]["tag"] == "tab1"          # both copies stay recoverable
    stored = client.get("/api/sweep/config").json()
    assert stored["config"]["variations"]["tooth_width"]["tag"] == "tab1"        # the refused save wrote nothing


def test_a_stale_tab_with_a_newer_local_timestamp_is_still_refused(client):
    """Sequential case of the review: base stamp 100, server moved to 200, the stale tab edited at 300."""
    client.put("/api/sweep/config?expected_revision=0", json=_cfg("base", 100))
    client.put("/api/sweep/config?expected_revision=1", json=_cfg("elsewhere", 200))
    stale = client.put("/api/sweep/config?expected_revision=1", json=_cfg("stale-but-later-stamp", 300))
    assert stale.status_code == 409
    assert client.get("/api/sweep/config").json()["config"]["variations"]["tooth_width"]["tag"] == "elsewhere"


def test_a_client_without_expected_revision_still_saves_and_advances(client):
    assert client.put("/api/sweep/config", json=_cfg("old-client", 1)).json()["revision"] == 1
    assert client.put("/api/sweep/config", json=_cfg("old-client-2", 2)).json()["revision"] == 2


def test_a_client_cannot_forge_the_revision_in_the_body(client):
    body = _cfg("x", 1)
    body["_rev"] = 99
    assert client.put("/api/sweep/config", json=body).json()["revision"] == 1
    assert client.get("/api/sweep/config").json()["revision"] == 1


def test_a_config_stored_before_revisions_existed_is_revision_zero(client, tmp_path):
    import json
    SC._store().write_text(json.dumps({"local": _cfg("legacy", 5)}), encoding="utf-8")
    assert client.get("/api/sweep/config").json()["revision"] == 0
    assert client.put("/api/sweep/config?expected_revision=0", json=_cfg("next", 6)).json()["revision"] == 1
