"""i18n backend surface (docs/I18N.md): stable error codes beside the unchanged
English ``detail``, and the per-user interface-language preference."""
from __future__ import annotations

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


def test_classify_known_messages_and_params():
    from motor_ai_sim.api_errors import classify
    assert classify(401, "sign in first") == ("auth.sign_in_required", {})
    assert classify(400, "unknown material: M270-35A") == ("material.unknown", {"name": "M270-35A"})
    assert classify(418, "something new") == ("http.418", {})
    assert classify(422, {"error": "x"}) == ("http.422", {})


def test_error_body_keeps_detail_and_adds_code():
    from motor_ai_sim.api_errors import error_body
    b = error_body(429, "too many requests — try again later")
    assert b["detail"] == "too many requests — try again later"
    assert b["code"] == "rate_limited" and b["params"] == {}


def test_http_errors_carry_code_and_english_detail(client):
    r = client.get("/api/panel_settings/nope")
    assert r.status_code == 422
    j = r.json()
    assert j["detail"]["invalid_parameters"] == ["panel"]   # unchanged shape
    assert j["code"] == "http.422"


def test_locale_preference_round_trip(client):
    r = client.get("/api/me/preferences")
    assert r.status_code == 200, r.text
    assert r.json()["locale"] is None
    assert "zh-CN" in r.json()["supported_locales"]
    r = client.put("/api/me/preferences", json={"locale": "zh-CN"})
    assert r.status_code == 200, r.text
    assert client.get("/api/me/preferences").json()["locale"] == "zh-CN"


def test_unsupported_locale_is_a_coded_422(client):
    r = client.put("/api/me/preferences", json={"locale": "xx"})
    assert r.status_code == 422
    j = r.json()
    assert j["code"] == "prefs.locale_unsupported"
    assert j["params"]["locale"] == "xx"
    assert "locale must be one of" in j["detail"]
