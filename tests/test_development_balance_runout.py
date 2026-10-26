"""Owner 2026-10-27: field 4_8 is "Balance grade and runout", an object (balance_grade, runout_mm, note) instead of one
string.  A legacy string keeps loading and saving: it is converted when read or normalised, stored versions are never
rewritten.  All values are made up."""
import copy
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim.development import models, store
from motor_ai_sim.development.routes import router

LEGACY = "G2.5; runout ≤ 0.05 mm"


def _draft(value, status="filled", version=2):
    return {"schema_version": version, "fields": {"4_8": {"status": status, "value": value}},
            "mission_profile": [], "mission_profile_status": "missing", "production": {}, "provenance": {}}


def _field():
    return models._field_map(2)["4_8"][1]


def test_schema_field_is_a_two_property_object_with_zh_mirror():
    field = _field()
    assert field["type"] == "object" and field["required"] is False and field["allow_not_known_yet"] is True
    assert field["checklist_id"] == "4.8" and field["presentation_group"] == "4"
    props = {p["id"]: p for p in field["properties"]}
    assert list(props) == ["balance_grade", "runout_mm", "note"]
    assert props["balance_grade"]["type"] == "string" and not props["balance_grade"].get("unit")
    assert {"G2.5", "G6.3", "G1"} <= {item["value"] for item in props["balance_grade"]["suggestions"]}
    assert props["runout_mm"]["type"] == "number" and props["runout_mm"]["unit"] == "mm" and props["runout_mm"]["min"] == 0
    assert props["note"]["type"] == "string" and props["note"]["ui_hidden"] is True and not props["note"]["required"]
    assert field["label"]["en"] == "Balance grade and runout" == field["short_label"]["en"]
    assert props["balance_grade"]["label"]["en"] == "Balance grade (ISO 21940)" and props["runout_mm"]["label"]["en"] == "Runout"
    for item in (field, *props.values()):
        assert item["label"]["zh-CN"].strip()
    assert field["short_label"]["zh-CN"].strip() and field["help"]["en"].strip() and field["help"]["zh-CN"].strip()


def test_v1_schema_keeps_the_frozen_string_field():
    assert models._field_map(1)["4_8"][1]["type"] == "string"
    kept = models.normalize_draft({"schema_version": 1, "fields": {"4_8": {"status": "filled", "value": "G2.5; 0.05 mm"}}})
    assert kept["fields"]["4_8"]["value"] == "G2.5; 0.05 mm"


@pytest.mark.parametrize("text, expected", [
    (LEGACY, {"balance_grade": "G2.5", "runout_mm": 0.05}),
    ("G6.3, 0.02 mm", {"balance_grade": "G6.3", "runout_mm": 0.02}),
    ("balance G2.5, TIR 0.05 mm", {"balance_grade": "G2.5", "runout_mm": 0.05}),
    ("g2,5 / 0,02mm", {"balance_grade": "G2.5", "runout_mm": 0.02}),
    ("G 1", {"balance_grade": "G1"}),
    ("0.03 mm", {"runout_mm": 0.03}),
    ("runout 0.05", {"runout_mm": 0.05}),
    ("G2.5 runout 0.05 mm, check with supplier",
     {"balance_grade": "G2.5", "runout_mm": 0.05, "note": "G2.5 runout 0.05 mm, check with supplier"}),
    ("as per drawing 12", {"note": "as per drawing 12"}),
    ("Gx", {"note": "Gx"}),
])
def test_legacy_string_is_parsed(text, expected):
    assert models.legacy_balance_runout(text) == expected


def test_normalize_converts_a_legacy_string_and_validates_the_object():
    out = models.normalize_draft(_draft(LEGACY))
    assert out["fields"]["4_8"] == {"status": "filled", "value": {"balance_grade": "G2.5", "runout_mm": 0.05}}
    bare = models.normalize_draft({"schema_version": 2, "fields": {"4_8": "G6.3, 0.02 mm"}})  # an unwrapped string answer
    assert bare["fields"]["4_8"]["value"] == {"balance_grade": "G6.3", "runout_mm": 0.02}
    blank = models.normalize_draft(_draft("   "))
    assert blank["fields"]["4_8"] == {"value": None, "status": "missing"}
    unknown = models.normalize_draft(_draft("N/A", status="not_known_yet"))
    assert unknown["fields"]["4_8"]["status"] == "not_known_yet" and unknown["fields"]["4_8"]["value"] == {"note": "N/A"}
    for bad in ({"runout_mm": -0.1}, {"runout_mm": "x"}, {"balance_grade": 3}, {"other": 1}):
        with pytest.raises(ValueError):
            models.normalize_draft(_draft(bad))


def test_object_round_trip_is_stable_and_idempotent():
    value = {"balance_grade": "G1", "runout_mm": 0.01}
    once = models.normalize_draft(_draft(copy.deepcopy(value)))
    assert once["fields"]["4_8"]["value"] == value
    assert models.normalize_draft(copy.deepcopy(once)) == once
    cleared = models.normalize_draft(_draft({"balance_grade": "", "runout_mm": ""}))
    assert cleared["fields"]["4_8"]["value"] == {"balance_grade": None, "runout_mm": None}
    noted = models.normalize_draft(_draft({"note": "kept text"}))
    assert noted["fields"]["4_8"]["value"] == {"note": "kept text"}
    assert json.loads(json.dumps(once, allow_nan=False)) == once


def test_read_time_upgrade_does_not_touch_the_stored_draft_and_completeness_is_unaffected():
    stored = _draft("G6.3, 0.02 mm")
    snapshot = copy.deepcopy(stored)
    upgraded = models.upgrade_legacy_values(stored)
    assert stored == snapshot                                                   # stored record untouched
    assert upgraded["fields"]["4_8"]["value"] == {"balance_grade": "G6.3", "runout_mm": 0.02}
    assert models.upgrade_legacy_values(upgraded) == upgraded                   # idempotent
    assert models.upgrade_legacy_values(_draft("G2.5", version=1))["fields"]["4_8"]["value"] == "G2.5"   # v1 stays as stored
    assert models.with_defaults(stored)["fields"]["4_8"]["value"] == {"balance_grade": "G6.3", "runout_mm": 0.02}
    base = models.completeness(models.normalize_draft({"schema_version": 2, "fields": {}}))
    for variant in (_draft("G6.3, 0.02 mm"), _draft({"balance_grade": "G1"}), _draft("free text"), _draft(None, status="not_known_yet")):
        got = models.completeness(variant)
        assert got["total_required"] == base["total_required"]                 # 4_8 is optional: never required
        assert got["missing"] == base["missing"] and got["invalid"] == base["invalid"]
    assert not [e for e in models.validation_errors(_draft("G6.3, 0.02 mm")) if e["field"].startswith("4_8")]


def test_routes_show_legacy_strings_converted_and_leave_stored_versions_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    current = {"email": "eng@example.test", "role": "engineer"}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    store.create_project({"id": "bal", "title": "Bal", "customer_org": "Org", "status": "spec", "created_at": "t", "updated_at": "t",
                          "owner_engineer": "eng@example.test",
                          "members": [{"email": "eng@example.test", "role": "owner"}, {"email": "customer@example.test", "role": "customer"}],
                          "linked_motors": [], "notes": "", "pending_invites": []})
    legacy = _draft(LEGACY)
    store.save_json("bal", "draft.json", {"draft": legacy, "schema_version": 2, "revision": 3, "version": 1})
    store.save_json("bal", "spec/v1.json", models.version_record(1, current["email"], copy.deepcopy(legacy), source_revision=2))
    version_file = store._file("bal", "spec/v1.json")
    draft_file = store._file("bal", "draft.json")
    before = (version_file.read_bytes(), draft_file.read_bytes())
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    headers = {"Authorization": "Bearer t"}
    want = {"balance_grade": "G2.5", "runout_mm": 0.05}
    assert client.get("/api/projects/bal/spec", headers=headers).json()["draft"]["fields"]["4_8"]["value"] == want
    assert client.get("/api/projects/bal/spec/versions/1", headers=headers).json()["draft"]["fields"]["4_8"]["value"] == want
    current.update(email="customer@example.test", role="user")                  # the customer view converts too
    assert client.get("/api/projects/bal/spec", headers=headers).json()["draft"]["fields"]["4_8"]["value"] == want
    assert client.get("/api/projects/bal/spec/versions/1", headers=headers).json()["draft"]["fields"]["4_8"]["value"] == want
    assert (version_file.read_bytes(), draft_file.read_bytes()) == before       # reading rewrote nothing
    current.update(email="eng@example.test", role="engineer")
    saved = client.put("/api/projects/bal/spec", headers=headers, json={"draft": legacy, "revision": 3})   # saving the old string still works
    assert saved.status_code == 200
    assert saved.json()["draft"]["fields"]["4_8"]["value"] == want
    assert store.load_json("bal", "draft.json", {})["draft"]["fields"]["4_8"]["value"] == want
    assert version_file.read_bytes() == before[0]                               # the submitted version is still the original string
    assert json.loads(version_file.read_text(encoding="utf-8"))["draft"]["fields"]["4_8"]["value"] == LEGACY
