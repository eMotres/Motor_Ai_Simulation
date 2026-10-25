from __future__ import annotations

import copy
import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim.development import models, store, migrations
from motor_ai_sim.development.routes import router


def _project():
    return {"id": "migration-test", "title": "Migration", "customer_org": "Org", "status": "spec",
            "created_at": "t", "updated_at": "t", "owner_engineer": "eng@example.test",
            "members": [{"email": "eng@example.test", "role": "owner"}], "linked_motors": [], "notes": ""}


def test_v2_s1_scalar_derives_shaft_power_and_rejects_ranges():
    answer = models.normalize_draft({"fields": {"1_4": {"status": "filled", "value": {"torque_Nm": 1.8, "speed_rpm": 6000}}}}, schema_version=2)
    point = answer["fields"]["1_4"]["value"]
    assert point["shaft_power_W"] == pytest.approx(1.8 * 2 * 3.141592653589793 * 6000 / 60)
    with pytest.raises(ValueError):
        models.normalize_draft({"fields": {"1_4": {"status": "filled", "value": {"torque_Nm": {"minimum": 1, "maximum": 2}, "speed_rpm": 6000}}}}, schema_version=2)
    with pytest.raises(ValueError, match="does not match"):
        models.normalize_draft({"fields": {"1_4": {"status": "filled", "value": {"torque_Nm": 1.8, "speed_rpm": 6000, "shaft_power_W": 100}}}}, schema_version=2)


def test_typed_load_curve_and_mission_metadata_are_allowlisted():
    samples = [{"rpm": 1000, "torque_Nm": 0.2, "shaft_power_W": 0.2 * 2 * 3.141592653589793 * 1000 / 60, "quality": "estimated", "extrapolated": False},
               {"rpm": 2000, "torque_Nm": 0.4, "shaft_power_W": 0.4 * 2 * 3.141592653589793 * 2000 / 60, "quality": "estimated", "extrapolated": False}]
    characteristic = {"catalog_id": "air-demo", "catalog_type": "air_propeller", "medium": "air", "content_digest": "abc123",
                      "calculation_model": "server_propeller_series", "conditions": {"temperature_C": 25, "altitude_m": 0},
                      "units": "rpm,N·m,W", "shaft_side": "load", "valid_domain": {"min_rpm": 0, "max_rpm": 2000},
                      "samples": samples, "source_snapshot": {"catalog_id": "air-demo", "vendor": "fixture", "model": "synthetic", "data_quality": "estimated", "power_data": "estimated",
                      "source_urls": ["https://example.test/prop-A"],
                      "fit_ct": {"c_ref": 0.12, "rpm_ref": 1000, "k": 0.1, "rpm_min": 500, "rpm_max": 2500, "rms_rel": 0.03, "n_used": 5},
                      "fit_cp": {"c_ref": 0.04, "rpm_ref": 1000, "k": 0.2, "rpm_min": 500, "rpm_max": 2500, "rms_rel": 0.05, "n_used": 4}}}
    draft = {"schema_version": 2, "fields": {"load_type": {"status": "filled", "value": "air_propeller"},
             "load_source": {"status": "filled", "value": "catalog"}, "load_characteristic": {"status": "filled", "value": characteristic}}}
    normalized = models.normalize_draft(draft, schema_version=2)
    assert normalized["fields"]["load_characteristic"]["value"]["content_digest"] == "abc123"
    snapshot = normalized["fields"]["load_characteristic"]["value"]["source_snapshot"]
    assert snapshot["fit_ct"]["k"] == 0.1 and snapshot["source_urls"] == ["https://example.test/prop-A"]
    bad_units = copy.deepcopy(draft); bad_units["fields"]["load_characteristic"]["value"]["units"] = "rpm,kW"
    with pytest.raises(ValueError, match="units"):
        models.normalize_draft(bad_units, schema_version=2)
    bad_type = copy.deepcopy(draft); bad_type["fields"]["load_type"]["value"] = "marine_propeller"
    with pytest.raises(ValueError, match="compatible"):
        models.normalize_draft(bad_type, schema_version=2)
    bad_snapshot = copy.deepcopy(draft); bad_snapshot["fields"]["load_characteristic"]["value"]["source_snapshot"]["digest"] = {"private": "blob"}
    with pytest.raises(ValueError, match="unknown properties"):
        models.normalize_draft(bad_snapshot, schema_version=2)
    bad_fit = copy.deepcopy(draft); bad_fit["fields"]["load_characteristic"]["value"]["source_snapshot"]["fit_ct"]["raw_points"] = [[500, 0.1]]
    with pytest.raises(ValueError, match="unknown properties"):
        models.normalize_draft(bad_fit, schema_version=2)
    bad_fit_value = copy.deepcopy(draft); bad_fit_value["fields"]["load_characteristic"]["value"]["source_snapshot"]["fit_cp"]["k"] = True
    with pytest.raises(ValueError, match="finite number"):
        models.normalize_draft(bad_fit_value, schema_version=2)
    bad_urls = copy.deepcopy(draft); bad_urls["fields"]["load_characteristic"]["value"]["source_snapshot"]["source_urls"] = ["", 3]
    with pytest.raises(ValueError):
        models.normalize_draft(bad_urls, schema_version=2)
    mission = [{"row_id": "row-one", "phase": "cruise", "rpm": 1000, "torque_Nm": 0.2, "duration_s": 10,
                "voltage_V": 48, "source": "catalog", "shaft_side": "load", "direct_drive_confirmed": True,
                "source_snapshot": {"catalog_id": "air-demo", "digest": "sha256"}}]
    normalized_rows = models.normalize_draft({"schema_version": 2, "mission_profile": mission}, schema_version=2)["mission_profile"]
    assert normalized_rows[0]["speed_rpm"] == 1000 and normalized_rows[0]["battery_voltage_V"] == 48
    assert normalized_rows[0]["direct_drive_confirmed"] is True
    unconfirmed = copy.deepcopy(mission)
    unconfirmed[0]["direct_drive_confirmed"] = False
    draft_with_unconfirmed_import = models.normalize_draft({"schema_version": 2, "mission_profile": unconfirmed}, schema_version=2)
    assert any(error["field"].endswith("direct_drive_confirmed") for error in models.validation_errors(draft_with_unconfirmed_import))
    duplicate = copy.deepcopy(mission) * 2
    with pytest.raises(ValueError, match="unique"):
        models.normalize_draft({"schema_version": 2, "mission_profile": duplicate}, schema_version=2)
    bad_metadata = copy.deepcopy(mission); bad_metadata[0]["source_snapshot"]["content_digest"] = {"secret": True}
    with pytest.raises(ValueError, match="invalid type"):
        models.normalize_draft({"schema_version": 2, "mission_profile": bad_metadata}, schema_version=2)


def test_schema_v2_has_localized_short_labels_and_rejects_unknown_choices_and_duplicate_rows():
    spec = models.schema(2)
    fields = [field for section in spec["sections"] for field in section["fields"]]
    assert all(set(field["label"]) >= {"en", "zh-CN"} for field in fields)
    assert all(set(field["short_label"]) >= {"en", "zh-CN"} for field in fields)
    assert not any("???" in label for field in fields for label in field["label"].values())
    assert all(max(map(len, field["short_label"].values())) <= 42 for field in fields)  # 42: "Max torque ripple at continuous operation" (owner 2026-10-26)
    with pytest.raises(ValueError, match="unsupported choice"):
        models.normalize_draft({"fields": {"load_type": {"status": "filled", "value": "invented_load"}}}, schema_version=2)
    row = {"row_id": "stable-1", "phase": "cruise", "torque_Nm": 0, "speed_rpm": 0,
           "duration_s": 1, "battery_voltage_V": 48}
    assert models.normalize_draft({"mission_profile": [row]}, schema_version=2)["mission_profile"][0]["row_id"] == "stable-1"
    with pytest.raises(ValueError, match="row_id must be unique"):
        models.normalize_draft({"mission_profile": [row, dict(row)]}, schema_version=2)


def test_catalog_load_curve_requires_reviewed_air_type_and_consistent_power():
    curve = {"catalog_id": "synthetic", "catalog_type": "air_propeller", "medium": "air",
             "content_digest": "sha256:synthetic", "calculation_model": "fixture",
             "units": "rpm,N\u00b7m,W", "shaft_side": "load", "valid_domain": {"min_rpm": 900, "max_rpm": 1200}, "samples": [
                 {"rpm": 1000, "torque_Nm": 0.2, "shaft_power_W": 20.943951, "quality": "measured", "extrapolated": False}]}
    fields = {"load_source": {"status": "filled", "value": "catalog"},
              "load_type": {"status": "filled", "value": "air_propeller"}}
    models._validate_load_characteristic(curve, fields)
    with pytest.raises(ValueError, match="incompatible catalog record"):
        models._validate_load_characteristic({**curve, "catalog_type": None}, fields)
    with pytest.raises(ValueError, match="inconsistent with torque"):
        models._validate_load_characteristic({**curve, "samples": [{"rpm": 1000, "torque_Nm": 0.2, "shaft_power_W": 12, "quality": "measured", "extrapolated": False}]}, fields)
    with pytest.raises(ValueError, match="finite number"):
        models._validate_load_characteristic({**curve, "samples": [{"rpm": float("nan"), "torque_Nm": 0.2, "shaft_power_W": 12, "quality": "measured", "extrapolated": False}]}, fields)


def test_catalog_mission_provenance_rejects_negative_or_boolean_ranges_and_direct_drive_is_invalid_until_reviewed():
    row = {"phase": "cruise", "torque_Nm": 0.2, "speed_rpm": 1000, "duration_s": 10,
           "battery_voltage_V": 48, "source": "catalog", "shaft_side": "load",
           "source_snapshot": {"catalog_id": "prop-A", "rpm_range_tested": [-100, 2000]}}
    with pytest.raises(ValueError, match="source_snapshot.rpm_range_tested"):
        models.normalize_draft({"mission_profile": [row]}, schema_version=2)
    row["source_snapshot"]["rpm_range_tested"] = [True, 2000]
    with pytest.raises(ValueError, match="source_snapshot.rpm_range_tested"):
        models.normalize_draft({"mission_profile": [row]}, schema_version=2)
    row["source_snapshot"]["rpm_range_tested"] = [100, 2000]
    row["source_snapshot"]["pressure_Pa"] = -1
    with pytest.raises(ValueError, match="source_snapshot.pressure_Pa"):
        models.normalize_draft({"mission_profile": [row]}, schema_version=2)
    row["source_snapshot"].pop("pressure_Pa")
    row["conditions"] = {"pressure_Pa": True}
    with pytest.raises(ValueError, match="pressure positive"):
        models.normalize_draft({"mission_profile": [row]}, schema_version=2)
    row.pop("conditions")
    draft = models.normalize_draft({"mission_profile": [row]}, schema_version=2)
    assert models.completeness(draft)["invalid"] == 1
    assert models.completeness(draft)["answered"] == 1  # only the pre-filled prototypes (default 1); the mission stays invalid
    assert any(error["field"].endswith("direct_drive_confirmed") for error in models.validation_errors(draft))
    row["direct_drive_confirmed"] = True
    reviewed = models.normalize_draft({"mission_profile": [row]}, schema_version=2)
    assert models.completeness(reviewed)["answered"] == 2  # the mission + the pre-filled prototypes
    assert not any(error["field"].endswith("direct_drive_confirmed") for error in models.validation_errors(reviewed))


def test_migration_preserves_full_s1_and_assigns_stable_mission_ids():
    original = {"fields": {"1_4": {"status": "filled", "value": {
        "torque_Nm": {"minimum": 1, "maximum": 2}, "speed_rpm": {"minimum": 1000, "maximum": 3000},
        "input_power_W": 100, "output_power_W": 200}}},
        "mission_profile": [{"phase": "cruise", "torque_Nm": 1, "speed_rpm": 1000, "duration_s": 10, "battery_voltage_V": 48}]}
    source = copy.deepcopy(original)
    preview = migrations.preview_v1_to_v2(original, source_revision=7)
    repeated = migrations.preview_v1_to_v2(original, source_revision=7)
    assert original == source
    assert preview["source_sha256"] == hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert preview["draft"]["fields"]["1_4"]["value"] == {"torque_Nm": 2, "speed_rpm": 3000, "shaft_power_W": pytest.approx(2 * 2 * 3.141592653589793 * 3000 / 60)}
    assert preview["draft"]["mission_profile"][0]["row_id"] == repeated["draft"]["mission_profile"][0]["row_id"]
    assert preview["requires_acknowledgement"] is True
    assert preview["draft"]["provenance"]["schema_migrations"][0]["legacy_1_4"] == source["fields"]["1_4"]
    assert preview["draft"]["provenance"]["schema_migrations"][0]["legacy_draft_snapshot"] == source
    assert any(issue["code"] == "legacy_power_semantics_preserved" for issue in preview["issues"])
    with pytest.raises(ValueError, match="acknowledgement"):
        migrations.apply_acknowledgement(preview, False)
    assert migrations.apply_acknowledgement(preview, True)["provenance"]["schema_migrations"][0]["acknowledged"]


def test_migration_missing_max_unknown_and_scalar_have_no_guessed_values():
    old = {"fields": {"1_4": {"status": "filled", "value": {
        "torque_Nm": {"minimum": 4, "maximum": None}, "speed_rpm": {"minimum": 1200, "maximum": 0},
        "input_power_W": 60, "output_power_W": 50}}}}
    preview = migrations.preview_v1_to_v2(old, source_revision=2)
    point = preview["draft"]["fields"]["1_4"]["value"]
    assert point["torque_Nm"] is None and point["speed_rpm"] == 0
    assert preview["draft"]["fields"]["1_4"]["status"] == "missing"
    assert "shaft_power_W" not in point
    assert preview["requires_acknowledgement"] is True
    unknown = {"fields": {"1_4": {"status": "not_known_yet", "value": {
        "torque_Nm": {"minimum": 1, "maximum": 3}, "speed_rpm": {"minimum": 1000, "maximum": 2000},
        "output_power_W": 75}}}}
    unknown_preview = migrations.preview_v1_to_v2(unknown, source_revision=3)
    assert unknown_preview["draft"]["fields"]["1_4"]["status"] == "not_known_yet"
    assert "shaft_power_W" not in unknown_preview["draft"]["fields"]["1_4"]["value"]
    assert unknown_preview["draft"]["provenance"]["schema_migrations"][0]["legacy_1_4"] == unknown["fields"]["1_4"]
    scalar = {"schema_version": 1, "fields": {"1_4": {"status": "filled", "value": {
        "torque_Nm": 1.5, "speed_rpm": 2400, "output_power_W": 400}}}}
    scalar_preview = migrations.preview_v1_to_v2(scalar, source_revision=4)
    assert scalar_preview["draft"]["fields"]["1_4"]["value"]["torque_Nm"] == 1.5
    assert scalar_preview["draft"]["fields"]["1_4"]["value"]["speed_rpm"] == 2400
    assert scalar_preview["requires_acknowledgement"] is True
    first_apply = migrations.apply_acknowledgement(scalar_preview, True)
    second_apply = migrations.apply_acknowledgement(scalar_preview, True)
    assert first_apply == second_apply
    repeated_preview = migrations.preview_v1_to_v2(first_apply, source_revision=5)
    assert repeated_preview["already_migrated"] is True
    assert repeated_preview["draft"] == first_apply


def test_migration_overflow_keeps_inputs_and_returns_reviewable_missing_point():
    legacy = {"fields": {"1_4": {"status": "filled", "value": {
        "torque_Nm": 1e300, "speed_rpm": 1e300, "output_power_W": 1e299}}}}
    preview = migrations.preview_v1_to_v2(legacy, source_revision=12)
    answer = preview["draft"]["fields"]["1_4"]
    assert answer["status"] == "missing"
    assert answer["value"] == {"torque_Nm": 1e300, "speed_rpm": 1e300}
    assert preview["requires_acknowledgement"] is True
    assert any(issue["code"] == "derived_power_out_of_range" for issue in preview["issues"])
    assert json.loads(json.dumps(preview, allow_nan=False))["draft"]["fields"]["1_4"]["value"]["torque_Nm"] == 1e300
    migrated = migrations.apply_acknowledgement(preview, True)
    normalized = models.normalize_draft(migrated, schema_version=2)
    assert normalized["fields"]["1_4"]["status"] == "missing"


def test_migration_route_is_explicit_revision_checked_and_acknowledged(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    current = {"email": "eng@example.test", "role": "engineer"}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    store.create_project(_project())
    legacy = {"fields": {"1_4": {"status": "filled", "value": {"torque_Nm": {"minimum": 1, "maximum": 2}, "speed_rpm": {"minimum": 1000, "maximum": 3000}}}}, "mission_profile": []}
    store.save_json("migration-test", "draft.json", {"draft": legacy, "schema_version": 1, "revision": 4, "version": None})
    snapshot = models.version_record(1, current["email"], {"schema_version": 1, **legacy}, source_revision=1)
    store.save_json("migration-test", "spec/v1.json", snapshot)
    snapshot_bytes = store._file("migration-test", "spec/v1.json").read_bytes()
    draft_path = store._file("migration-test", "draft.json")
    draft_bytes = draft_path.read_bytes()
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    headers = {"Authorization": "Bearer test"}
    current_spec = client.get("/api/projects/migration-test/spec", headers=headers)
    assert current_spec.status_code == 200 and current_spec.json()["schema_version"] == 1
    historical = client.get("/api/projects/migration-test/spec/versions/1", headers=headers)
    assert historical.status_code == 200 and historical.json()["schema_version"] == 1
    assert historical.json()["schema"]["schema_version"] == 1
    assert draft_path.read_bytes() == draft_bytes
    preview_response = client.post("/api/projects/migration-test/spec/migration/preview", headers=headers)
    assert preview_response.status_code == 200
    preview = preview_response.json()["preview"]
    assert store.load_json("migration-test", "draft.json", {})["revision"] == 4
    assert draft_path.read_bytes() == draft_bytes
    missing_ack = client.post("/api/projects/migration-test/spec/migration/apply", headers=headers,
                              json={"revision": 4, "source_sha256": preview["source_sha256"], "acknowledge_point": False})
    assert missing_ack.status_code == 422
    applied = client.post("/api/projects/migration-test/spec/migration/apply", headers=headers,
                          json={"revision": 4, "source_sha256": preview["source_sha256"], "acknowledge_point": True})
    assert applied.status_code == 200
    assert applied.json()["schema_version"] == 2
    assert applied.json()["revision"] == 5
    assert store._file("migration-test", "spec/v1.json").read_bytes() == snapshot_bytes
    stale = client.post("/api/projects/migration-test/spec/migration/apply", headers=headers,
                        json={"revision": 4, "source_sha256": preview["source_sha256"], "acknowledge_point": True})
    assert stale.status_code == 200
    assert stale.json()["revision"] == 5
    edited = client.put("/api/projects/migration-test/spec", headers=headers,
                        json={"draft": applied.json()["draft"], "schema_version": 2, "revision": 5})
    assert edited.status_code == 200 and edited.json()["revision"] == 6
    stale_after_edit = client.post("/api/projects/migration-test/spec/migration/apply", headers=headers,
                                   json={"revision": 4, "source_sha256": preview["source_sha256"], "acknowledge_point": True})
    assert stale_after_edit.status_code == 409
    current.update(email="stranger@example.test", role="user")
    assert client.post("/api/projects/migration-test/spec/migration/preview", headers=headers).status_code == 404


def test_migration_returns_conflict_if_draft_changes_after_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    current = {"email": "eng@example.test", "role": "engineer"}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    store.create_project(_project())
    legacy = {"fields": {"1_4": {"status": "filled", "value": {"torque_Nm": {"minimum": 1, "maximum": 2}, "speed_rpm": {"minimum": 1000, "maximum": 3000}}}}}
    store.save_json("migration-test", "draft.json", {"draft": legacy, "schema_version": 1, "revision": 4, "version": None})
    app = FastAPI(); app.include_router(router)
    client = TestClient(app); headers = {"Authorization": "Bearer test"}
    preview = client.post("/api/projects/migration-test/spec/migration/preview", headers=headers).json()["preview"]
    current_wrapper = store.load_json("migration-test", "draft.json", {})
    current_wrapper["revision"] = 5
    store.save_json("migration-test", "draft.json", current_wrapper)
    response = client.post("/api/projects/migration-test/spec/migration/apply", headers=headers,
                           json={"revision": 4, "source_sha256": preview["source_sha256"], "acknowledge_point": True})
    assert response.status_code == 409
    assert store.load_json("migration-test", "draft.json", {})["schema_version"] == 1


def test_legacy_draft_flush_keeps_v1_ranges_and_rejects_schema_bypass(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    current = {"email": "eng@example.test", "role": "engineer"}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    store.create_project(_project())
    legacy = {"fields": {"1_4": {"status": "filled", "value": {"torque_Nm": {"minimum": 1, "maximum": 2}, "speed_rpm": {"minimum": 1000, "maximum": 3000}}}}}
    store.save_json("migration-test", "draft.json", {"draft": legacy, "schema_version": 1, "revision": 0})
    app = FastAPI(); app.include_router(router)
    client = TestClient(app); headers = {"Authorization": "Bearer test"}
    save = client.put("/api/projects/migration-test/spec", headers=headers,
                      json={"draft": legacy, "schema_version": 1, "revision": 0})
    assert save.status_code == 200 and save.json()["schema_version"] == 1
    assert store.load_json("migration-test", "draft.json", {})["draft"]["fields"]["1_4"]["value"]["torque_Nm"] == {"minimum": 1, "maximum": 2}
    bypass = client.put("/api/projects/migration-test/spec", headers=headers,
                        json={"draft": {"schema_version": 2, "fields": {}}, "schema_version": 2, "revision": 1})
    assert bypass.status_code == 409


def test_customer_roundtrip_preserves_private_migration_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    current = {"email": "eng@example.test", "role": "user"}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    import motor_ai_sim.users as users
    monkeypatch.setattr(users, "get_user", lambda email: {"email_verified": True})
    project = _project()
    project["members"].append({"email": "customer@example.test", "role": "customer"})
    store.create_project(project)
    private = {"schema_migrations": [{"from_schema_version": 1, "to_schema_version": 2,
        "source_revision": 3, "source_sha256": "a" * 64,
        "mission_row_index_map": [{"legacy_index": 0, "row_id": "public-row-1"}],
        "legacy_1_4": {"status": "filled", "value": {"torque_Nm": {"minimum": 1, "maximum": 2, "private_note": "engineer-only"},
                          "speed_rpm": {"minimum": 1000, "maximum": 3000}, "internal_note": "engineer-only"}},
        "legacy_draft_snapshot": {"private_audit": "engineer-only"}, "acknowledged": True,
        "issues": [{"code": "s1_range_collapsed_to_maxima", "field": "1_4", "message": "engineer-only"},
                   {"code": "private_code", "field": "1_4", "message": "engineer-only"}]}]}
    draft = {"schema_version": 2, "fields": {}, "mission_profile": [], "mission_profile_status": "missing", "production": {}, "provenance": private}
    store.save_json("migration-test", "draft.json", {"draft": draft, "schema_version": 2, "revision": 1, "version": None})
    app = FastAPI(); app.include_router(router)
    client = TestClient(app); headers = {"Authorization": "Bearer test"}
    viewed = client.get("/api/projects/migration-test/spec", headers=headers)
    assert viewed.status_code == 200
    public_note = viewed.json()["draft"]["provenance"]["schema_migrations"][0]
    assert public_note["legacy_1_4"]["value"]["torque_Nm"] == {"minimum": 1, "maximum": 2}
    assert "internal_note" not in public_note["legacy_1_4"]["value"]
    assert "legacy_draft_snapshot" not in public_note and "source_sha256" not in public_note
    assert public_note["mission_row_index_map"] == [{"legacy_index": 0, "row_id": "public-row-1"}]
    assert public_note["issues"] == [{"code": "s1_range_collapsed_to_maxima", "field": "1_4"}]
    saved = client.put("/api/projects/migration-test/spec", headers=headers,
                       json={"draft": viewed.json()["draft"], "schema_version": 2, "revision": 1})
    assert saved.status_code == 200
    assert store.load_json("migration-test", "draft.json", {})["draft"]["provenance"] == private


def test_migration_fails_closed_when_store_is_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    current = {"email": "eng@example.test", "role": "engineer"}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    store.create_project(_project())
    legacy = {"fields": {"1_4": {"status": "filled", "value": {
        "torque_Nm": {"minimum": 1, "maximum": 2}, "speed_rpm": {"minimum": 1000, "maximum": 3000}}}}}
    store.save_json("migration-test", "draft.json", {"draft": legacy, "schema_version": 1, "revision": 4})
    path = store._file("migration-test", "draft.json")
    before = path.read_bytes()
    monkeypatch.setattr(store, "ensure_writable", lambda: (_ for _ in ()).throw(OSError("read-only")))
    app = FastAPI(); app.include_router(router)
    response = TestClient(app).post("/api/projects/migration-test/spec/migration/apply",
        headers={"Authorization": "Bearer test"},
        json={"revision": 4, "source_sha256": "unused", "acknowledge_point": True})
    assert response.status_code == 503
    assert path.read_bytes() == before


def test_thread_references_resolution_and_reopen_are_retained(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    current = {"email": "eng@example.test", "role": "engineer"}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    store.create_project(_project())
    app = FastAPI(); app.include_router(router)
    client = TestClient(app); headers = {"Authorization": "Bearer test"}
    created = client.post("/api/projects/migration-test/thread", headers=headers,
                          json={"text": "Review this phase", "field_ref": "4_6.axial_N", "row_id": "row-1", "version": 2, "revision": 9})
    assert created.status_code == 200
    message_id = created.json()["id"]
    endpoint = f"/api/projects/migration-test/thread/{message_id}/resolve"
    assert client.post(endpoint, headers=headers, json={"resolved": True}).status_code == 200
    assert client.post(endpoint, headers=headers, json={"resolved": False}).json()["resolved"] is False
    messages = client.get("/api/projects/migration-test/thread", headers=headers).json()["messages"]
    assert len(messages) == 1
    assert messages[0]["field_ref"] == "4_6.axial_N" and messages[0]["row_id"] == "row-1"
    assert messages[0]["version"] == 2 and messages[0]["revision"] == 9
    assert messages[0]["resolved"] is False
