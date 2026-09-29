"""/api/panel_settings — the server-side memory of a tab's input fields.

User 2026-09-07: "запоминай все последние настройки механических и термических
моделирований … всё одинаково для всех моделирований".  The store is a dot-file
beside the config; tests redirect it to tmp_path so the user's own memory is
never touched.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    from motor_ai_sim.routes import panel_settings as ps
    monkeypatch.setattr(ps, "_store_path", lambda: str(tmp_path / ".panel_settings.json"))
    yield


def test_an_unused_panel_answers_empty_not_404(client):
    r = client.get("/api/panel_settings/mechanical")
    assert r.status_code == 200, r.text
    assert r.json()["settings"] == {}
    assert r.json()["updated_at"] is None


def test_round_trip_keeps_the_panel_s_own_shapes(client):
    body = {"settings": {"coolMode": "liquid", "flowLpm": "12", "eqTemp": True,
                         "contacts": {"sleeve_magnet": {"type": "separation", "mu": 0.2}}}}
    r = client.put("/api/panel_settings/thermal", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["updated_at"]
    back = client.get("/api/panel_settings/thermal").json()
    assert back["settings"] == body["settings"]          # strings stay strings, objects objects
    # panels do not leak into one another
    assert client.get("/api/panel_settings/mechanical").json()["settings"] == {}


def test_a_second_put_replaces_not_merges(client):
    client.put("/api/panel_settings/mechanical", json={"settings": {"rpm": "20000", "osf": "1.15"}})
    client.put("/api/panel_settings/mechanical", json={"settings": {"rpm": "23000"}})
    assert client.get("/api/panel_settings/mechanical").json()["settings"] == {"rpm": "23000"}


def test_the_store_survives_a_process_restart(client, tmp_path):
    client.put("/api/panel_settings/mechanical", json={"settings": {"rpm": "21000"}})
    from motor_ai_sim.routes import panel_settings as ps
    d = json.load(open(tmp_path / ".panel_settings.json", encoding="utf-8"))
    assert d["mechanical"]["shared"]["settings"]["rpm"] == "21000"
    assert ps._load() == d


@pytest.mark.parametrize("bad", [
    {"settings": "not a dict"},
    {"nope": {}},
    {"settings": {str(i): i for i in range(500)}},
])
def test_malformed_bodies_are_422_with_a_sentence(client, bad):
    r = client.put("/api/panel_settings/thermal", json=bad)
    assert r.status_code == 422, r.text
    assert "error" in r.json()["detail"]


def test_an_unknown_panel_is_422(client):
    r = client.get("/api/panel_settings/kitchen")
    assert r.status_code == 422
    assert "kitchen" in r.json()["detail"]["error"]
