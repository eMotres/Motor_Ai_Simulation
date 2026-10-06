"""The remembered Configure selection is isolated to a verified account."""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.routes import account, family


@pytest.fixture()
def last_motor_api(tmp_path, monkeypatch):
    path = tmp_path / ".user_prefs.json"
    monkeypatch.setattr(account, "_prefs_path", lambda: str(path))
    users = {
        "Bearer account-a": {"uid": "uid-a", "email": "a@example.com", "role": "user"},
        "Bearer account-b": {"uid": "uid-b", "email": "b@example.com", "role": "user"},
    }

    def resolve(token, **_kwargs):
        user = users.get(token)
        return {"user": user, "reason": "ok" if user else ("no_token" if not token else "malformed")}

    import motor_ai_sim.auth as auth
    monkeypatch.setattr(auth, "resolve_user_detail", resolve)
    denied = {"value": False}

    def guard(die, _authorization):
        if denied["value"] or die == "private":
            raise HTTPException(404, detail="not found")
        return {}

    monkeypatch.setattr(family, "_require_die_access", guard)
    monkeypatch.setattr(family, "_cfg_file", lambda die, config: f"{die}/{config}.yaml")
    monkeypatch.setattr(family, "_load_yaml", lambda _path, _what: {
        "duties": [{"name": "rated"}, {"name": "peak"}],
    })
    import motor_ai_sim.routes.catalog as catalog
    monkeypatch.setattr(catalog, "get_references", lambda _authorization: {"motors": [
        {"id": "cat_ciano14", "card": {"die": "CIANO14 40 new", "config": "L12"}},
        {"id": "cat_other", "card": {"die": "OTHER", "config": "L20"}},
    ]})
    with TestClient(app) as client:
        yield client, path, denied


def _headers(token: str | None):
    return {"Authorization": token} if token else {}


def test_last_motor_is_owned_by_verified_account_and_preserves_locale(last_motor_api):
    client, path, _ = last_motor_api
    path.write_text(json.dumps({"a@example.com": {"locale": "zh-CN"}}), encoding="utf-8")
    selection_a = {"ref_id": "cat_ciano14", "die": "CIANO14 40 new",
                   "config": "L12", "duty": "peak"}
    selection_b = {"ref_id": "cat_other", "die": "OTHER", "config": "L20", "duty": "rated"}

    saved = client.put("/api/me/last_motor", headers=_headers("Bearer account-a"),
                       json={"selection": selection_a})
    assert saved.status_code == 200
    assert client.get("/api/me/last_motor", headers=_headers("Bearer account-a")).json()["selection"] == selection_a
    assert client.get("/api/me/last_motor", headers=_headers("Bearer account-b")).json()["selection"] is None

    assert client.put("/api/me/last_motor", headers=_headers("Bearer account-b"),
                      json={"selection": selection_b}).status_code == 200
    store = json.loads(path.read_text(encoding="utf-8"))
    assert store["a@example.com"]["locale"] == "zh-CN"
    assert store["a@example.com"]["last_motor"] == selection_a
    assert store["b@example.com"]["last_motor"] == selection_b


@pytest.mark.parametrize("token", [None, "Bearer invalid"])
def test_last_motor_rejects_anonymous_and_invalid_auth(last_motor_api, token):
    client, _path, _denied = last_motor_api
    assert client.get("/api/me/last_motor", headers=_headers(token)).status_code == 401
    response = client.put("/api/me/last_motor", headers=_headers(token), json={"selection": {}})
    assert response.status_code == 401


def test_put_rejects_missing_config_or_duty(last_motor_api):
    client, _path, _denied = last_motor_api
    selection = {"ref_id": "cat_ciano14", "die": "CIANO14 40 new",
                 "config": "L12", "duty": "not-saved"}
    response = client.put("/api/me/last_motor", headers=_headers("Bearer account-a"),
                          json={"selection": selection})
    assert response.status_code == 404


def test_put_rejects_reference_that_points_to_another_family_card(last_motor_api):
    client, _path, _denied = last_motor_api
    selection = {"ref_id": "cat_other", "die": "CIANO14 40 new",
                 "config": "L12", "duty": "peak"}
    response = client.put("/api/me/last_motor", headers=_headers("Bearer account-a"),
                          json={"selection": selection})
    assert response.status_code == 404


def test_put_accepts_l20_build_under_its_same_die_l12_passport_reference(last_motor_api):
    client, _path, _denied = last_motor_api
    selection = {"ref_id": "cat_ciano14", "die": "CIANO14 40 new",
                 "config": "L20", "duty": "peak"}
    response = client.put("/api/me/last_motor", headers=_headers("Bearer account-a"),
                          json={"selection": selection})
    assert response.status_code == 200
    assert client.get("/api/me/last_motor", headers=_headers("Bearer account-a")).json()["selection"] == selection


def test_put_and_get_allow_valid_nonpassport_motor_with_null_reference(last_motor_api):
    client, _path, _denied = last_motor_api
    selection = {"ref_id": None, "die": "personal motor", "config": "L12", "duty": None}
    response = client.put("/api/me/last_motor", headers=_headers("Bearer account-a"),
                          json={"selection": selection})
    assert response.status_code == 200
    assert client.get("/api/me/last_motor", headers=_headers("Bearer account-a")).json()["selection"] == selection


def test_get_hides_a_selection_when_access_was_revoked(last_motor_api):
    client, _path, denied = last_motor_api
    selection = {"ref_id": "cat_ciano14", "die": "CIANO14 40 new",
                 "config": "L12", "duty": "peak"}
    assert client.put("/api/me/last_motor", headers=_headers("Bearer account-a"),
                      json={"selection": selection}).status_code == 200
    denied["value"] = True
    response = client.get("/api/me/last_motor", headers=_headers("Bearer account-a"))
    assert response.status_code == 200
    assert response.json()["selection"] is None
    assert response.json()["unavailable"] is True
