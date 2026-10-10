"""Shared passport references remain available in layered user workspaces."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from motor_ai_sim.routes import catalog as cat


@pytest.fixture()
def catalog_layers(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    shared = tmp_path / "shared"
    workspace.mkdir()
    shared.mkdir()
    workspace_file = workspace / "motor_catalog.json"
    shared_file = shared / "motor_catalog.json"

    # Other catalog tests may explicitly pin this legacy module override.
    # These tests exercise the layered resolver unless a case opts in below.
    monkeypatch.delitem(cat.__dict__, "_CATALOG_PATH", raising=False)
    monkeypatch.setattr(cat, "_catalog_path", lambda: workspace_file)
    import motor_ai_sim.workspace as ws
    monkeypatch.setattr(ws, "layering", lambda: True)
    monkeypatch.setattr(ws, "shared_root", lambda: shared)
    cat._REF_CACHE.update({"key": None, "cards": []})
    yield workspace_file, shared_file
    cat._REF_CACHE.update({"key": None, "cards": []})


def _write(path: Path, motors: list[dict]) -> None:
    path.write_text(json.dumps({"motors": motors}), encoding="utf-8")


def _reference(motor_id: str, name: str, *, torque: float = 10.0, **extra) -> dict:
    return {
        "id": motor_id,
        "name": name,
        "diameter_mm": 40,
        "passport": {
            "passport": {"L0_mm": 40.0, "T0_Nm": torque},
            "fit": {"slotHeight_mm": 1},
            "geo": {"numSlots": 12, "numPoles": 14, "statorOR_mm": 20,
                    "magnetHeight_mm": 5.7},
        },
        **extra,
    }


def test_personal_catalog_without_passport_does_not_hide_shared_reference(catalog_layers):
    workspace_file, shared_file = catalog_layers
    _write(workspace_file, [{"id": "cat_my_motor", "name": "My motor", "passport": None}])
    _write(shared_file, [_reference("cat_ciano14_40_new", "CIANO14 40 new")])

    assert [m["id"] for m in cat._passport_cards()] == ["cat_ciano14_40_new"]


def test_workspace_card_wins_duplicate_stable_id_without_geometry_dedup(catalog_layers):
    workspace_file, shared_file = catalog_layers
    _write(workspace_file, [_reference("same-id", "Workspace build", torque=21)])
    _write(shared_file, [
        _reference("same-id", "Shared build", torque=10),
        _reference("another-build", "Shared longer build", torque=12),
    ])

    cards = cat._passport_cards()
    assert [(m["id"], m["name"], m["passport"]["passport"]["T0_Nm"])
            for m in cards] == [
                ("same-id", "Workspace build", 21),
                ("another-build", "Shared longer build", 12),
            ]


def test_shared_catalog_change_invalidates_two_source_cache(catalog_layers):
    workspace_file, shared_file = catalog_layers
    _write(workspace_file, [{"id": "cat_my_motor", "name": "My motor"}])
    _write(shared_file, [_reference("first", "Shared first")])
    assert [m["id"] for m in cat._passport_cards()] == ["first"]

    before = shared_file.stat()
    _write(shared_file, [
        _reference("first", "Shared first updated", torque=11),
        _reference("second", "Shared second reference with longer name"),
    ])
    os.utime(shared_file, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))

    assert [m["id"] for m in cat._passport_cards()] == ["first", "second"]


def test_shared_source_creation_and_removal_invalidate_cache(catalog_layers):
    workspace_file, shared_file = catalog_layers
    _write(workspace_file, [{"id": "cat_my_motor", "name": "My motor"}])
    assert cat._passport_cards() == []

    _write(shared_file, [_reference("shared-created", "Created shared reference")])
    assert [m["id"] for m in cat._passport_cards()] == ["shared-created"]

    shared_file.unlink()
    assert cat._passport_cards() == []


def test_explicit_catalog_path_override_uses_only_that_source(monkeypatch, catalog_layers, tmp_path):
    _workspace_file, shared_file = catalog_layers
    override = tmp_path / "override.json"
    _write(override, [_reference("override", "Explicit override")])
    _write(shared_file, [_reference("shared", "Shared reference")])
    # Patch the module dictionary directly so teardown removes the override
    # instead of materializing the module's lazy __getattr__ value as a global.
    monkeypatch.setitem(cat.__dict__, "_CATALOG_PATH", override)

    assert [m["id"] for m in cat._passport_cards()] == ["override"]


def test_union_keeps_grants_and_private_card_visibility(monkeypatch, catalog_layers):
    workspace_file, shared_file = catalog_layers
    _write(workspace_file, [
        _reference("foreign-private", "Other user's private motor",
                   visibility="private", owner="other@example.com"),
        _reference("own-private", "My private motor",
                   visibility="private", owner="user@example.com"),
        _reference("ungranted", "Unrelated public motor"),
    ])
    _write(shared_file, [_reference("granted", "CIANO14 40 new")])

    import motor_ai_sim.auth as auth
    import motor_ai_sim.motor_access as access
    import motor_ai_sim.passport_store as passport_store
    import motor_ai_sim.routes.presets as presets

    identity = {"id": "user@example.com", "is_admin": False}
    monkeypatch.setattr(auth, "caller_identity", lambda _auth=None: identity)
    monkeypatch.setattr(cat, "_caller_identity", lambda _auth=None: identity)
    monkeypatch.setattr(cat, "_machine_die_index", lambda: {
        "ciano14 40 new": "CIANO14 40 new",
    })
    monkeypatch.setattr(cat, "_machine_names", lambda: {"ciano14 40 new"})
    monkeypatch.setattr(presets, "_owner_of", lambda card: card.get("owner"))
    monkeypatch.setattr(access, "catalog_access", lambda _auth=None: {
        "mode": access.MODE_GRANTED,
        "dies": frozenset({"CIANO14 40 new"}),
        "is_admin": False,
    })
    monkeypatch.setattr(access, "may_see_die",
                        lambda grant, die: die in grant["dies"])
    monkeypatch.setattr(passport_store, "attach", lambda _motors: None)

    response = cat.get_references("Bearer fake")
    assert [m["id"] for m in response["motors"]] == ["own-private", "granted"]


def test_shared_reference_opens_current_family_pack_context_without_widening_access(
    monkeypatch, catalog_layers, tmp_path,
):
    workspace_file, shared_file = catalog_layers
    die = "CIANO14 40 new"
    workspace_dir = tmp_path / "shared-dies" / die
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "die.yaml").write_text(yaml.safe_dump({
        "name": die,
        "geometry": {"num_slots": 12, "num_poles": 14, "stator_diameter": 40.0,
                     "magnet_height": 5.7},
    }), encoding="utf-8")
    battery = {"cells": 6, "chemistry": "NMC", "v_min": 18.0,
               "v_nom": 22.2, "v_max": 25.2,
               "v_cell_min": 3.0, "v_cell_nom": 3.7, "v_cell_max": 4.2}
    for config, length, pack in (("L12", 12.0, battery), ("L20", 20.0, None)):
        doc = {"die": die, "name": config,
               "geometry_overrides": {"motor_length": length},
               "battery": pack, "winding": {}, "duties": []}
        (workspace_dir / f"{config}.yaml").write_text(
            yaml.safe_dump(doc), encoding="utf-8")

    shared_card = _reference("cat_ciano14_40_new", die)
    shared_card["passport"]["passport"]["L0_mm"] = 12.0
    _write(workspace_file, [{"id": "cat_my_motor", "name": "My motor"}])
    _write(shared_file, [
        shared_card,
        _reference("ungranted", "Another machine"),
        _reference("foreign-private", die, visibility="private", owner="other@example.com"),
    ])

    import motor_ai_sim.auth as auth
    import motor_ai_sim.motor_access as access
    import motor_ai_sim.passport_store as passport_store
    import motor_ai_sim.routes.presets as presets
    import motor_ai_sim.workspace as ws

    identity = {"id": "user@example.com", "is_admin": False}
    monkeypatch.setattr(auth, "caller_identity", lambda _auth=None: identity)
    monkeypatch.setattr(cat, "_caller_identity", lambda _auth=None: identity)
    monkeypatch.setattr(ws, "iter_dies", lambda: [{"die": die, "name": die,
                                                   "dir": workspace_dir}])
    monkeypatch.setattr(cat, "_machine_die_index", lambda: {
        die.casefold(): die,
    })
    monkeypatch.setattr(cat, "_machine_names", lambda: {die.casefold()})
    monkeypatch.setattr(presets, "_owner_of", lambda card: card.get("owner"))
    monkeypatch.setattr(access, "catalog_access", lambda _auth=None: {
        "mode": access.MODE_GRANTED, "dies": frozenset({die}), "is_admin": False,
    })
    monkeypatch.setattr(access, "may_see_die",
                        lambda grant, name: name in grant["dies"])
    monkeypatch.setattr(passport_store, "card_of", lambda *_args: None)
    monkeypatch.setattr(passport_store, "attach", lambda _motors: None)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(cat.router)
    client = TestClient(app)

    refs = client.get("/api/catalog/references", headers={"Authorization": "Bearer fake"})
    assert refs.status_code == 200
    assert [m["id"] for m in refs.json()["motors"]] == ["cat_ciano14_40_new"]

    context = client.get("/api/catalog/cat_ciano14_40_new/configure_context",
                         headers={"Authorization": "Bearer fake"})
    assert context.status_code == 200, context.text
    body = context.json()
    assert body["has_family_doc"] is True
    assert body["battery"]["cells"] == 6
    assert body["battery"]["v_nom"] == 22.2
    l12 = next(p for p in body["presets"] if p["config"] == "L12")
    assert l12["battery"]["v_max"] == 25.2
    assert client.get("/api/catalog/ungranted/configure_context",
                      headers={"Authorization": "Bearer fake"}).status_code == 404
    assert client.get("/api/catalog/foreign-private/configure_context",
                      headers={"Authorization": "Bearer fake"}).status_code == 404

    # An account-local record with the same stable ID remains authoritative.
    local_duplicate = _reference("cat_ciano14_40_new", die)
    local_duplicate["passport"]["passport"]["L0_mm"] = 12.0
    local_duplicate["configure_limits"] = {"L_max_mm": 99.0}
    _write(workspace_file, [local_duplicate])
    duplicate = client.get("/api/catalog/cat_ciano14_40_new/configure_context",
                          headers={"Authorization": "Bearer fake"})
    assert duplicate.status_code == 200
    assert duplicate.json()["limits"]["L_max_mm"] == 99.0

