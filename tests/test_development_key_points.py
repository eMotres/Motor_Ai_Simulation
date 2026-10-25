"""Key operating points (owner 2026-10-26): any number of named points (takeoff, cruise, landing, peak ...), each with
torque, speed and duration (continuous or seconds > 0) and an optional DC voltage.  They count toward completeness,
validate loudly, and the S1 point (1_4) / peak point (1_5) the rest of the system reads are DERIVED from them.  A project
that holds only 1_4 / 1_5 stays valid and reads as two points.  All values are made up."""
import copy
import math

import pytest

from motor_ai_sim.development import models


def _all_unknown_draft():
    fields, production = {}, {}
    for section in models.schema(2)["sections"]:
        for field in section["fields"]:
            if field["id"] == "mission_profile":
                continue
            (production if section["id"] == "production" else fields)[field["id"]] = {"value": None, "status": "not_known_yet"}
    return {"schema_version": 2, "fields": fields, "mission_profile": [], "mission_profile_status": "not_known_yet",
            "production": production}


def _point(name, torque, speed, duration, voltage=None):
    return {"name": name, "torque_Nm": torque, "speed_rpm": speed, "duration_s": duration, "voltage_V": voltage}


FOUR = [_point("Takeoff", 80, 2400, 60, 700), _point("Cruise", 40, 2800, "continuous"),
        _point("Landing", 55, 2000, 30), _point("Peak", 110, 2200, 5, 640)]


def _draft_with(points, status="filled"):
    draft = _all_unknown_draft()
    draft["fields"].pop("1_4")
    draft["fields"].pop("1_5")
    draft["fields"]["key_points"] = {"status": status, "value": points}
    return models.normalize_draft(draft)


def _errors(draft):
    return [e for e in models.validation_errors(draft) if e["field"].startswith("key_points")]


def test_schema_has_the_key_points_slot_in_place_of_the_required_s1():
    by_id = models._field_map(2)
    assert by_id["key_points"][1]["required"] is True and by_id["1_4"][1]["required"] is False
    # the swap keeps the number of required fields; the old v1 schema is untouched
    assert models.completeness(models.normalize_draft({"schema_version": 2, "fields": {}}))["total_required"] == 15
    assert "key_points" not in models._field_map(1)


def test_four_named_points_are_complete_and_derive_the_s1_and_peak_point():
    draft = _draft_with(copy.deepcopy(FOUR))
    rows = draft["fields"]["key_points"]["value"]
    assert [r["name"] for r in rows] == ["Takeoff", "Cruise", "Landing", "Peak"]
    assert all(r["row_id"] for r in rows) and len({r["row_id"] for r in rows}) == 4
    assert models.completeness(draft)["complete"] is True and models.validation_errors(draft) == []
    s1, peak = draft["fields"]["1_4"], draft["fields"]["1_5"]
    # continuous = the point whose duration is "continuous"; peak = the highest-torque point
    assert s1["status"] == "filled" and s1["value"]["torque_Nm"] == 40 and s1["value"]["speed_rpm"] == 2800
    assert s1["value"]["shaft_power_W"] == pytest.approx(40 * 2 * math.pi * 2800 / 60)
    assert peak["status"] == "filled" and peak["value"]["torque_Nm"] == 110 and peak["value"]["duration_s"] == 5
    # the second normalisation (the submit path) changes nothing
    assert models.normalize_draft(copy.deepcopy(draft)) == draft


def test_derivation_is_deterministic():
    longest = [_point("A", 30, 1000, 10), _point("B", 20, 1000, 90), _point("C", 50, 1000, 90)]
    cont, peak = models.derive_continuous_peak(longest)
    assert cont["name"] == "C" and peak is None  # no "continuous" point: the longest wins, the higher torque breaks the tie
    several = [_point("Hover", 30, 1000, "continuous"), _point("Cruise", 20, 3000, "continuous"), _point("Boost", 45, 900, 8)]
    cont, peak = models.derive_continuous_peak(several)
    assert cont["name"] == "Hover" and peak["name"] == "Boost"
    # the highest-torque point is the continuous one: there is no separate peak
    assert models.derive_continuous_peak([_point("Only", 10, 500, "continuous")]) == ({**_point("Only", 10, 500, "continuous")}, None)
    # an incomplete row never takes part
    assert models.derive_continuous_peak([_point("x", None, 500, "continuous")]) == (None, None)


def _shared_cases():
    import json
    from pathlib import Path
    return json.loads((Path(__file__).parent / "fixtures" / "development_key_points_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _shared_cases(), ids=lambda c: c["id"])
def test_derivation_matches_the_shared_cases_the_web_also_runs(case):
    """tests/fixtures/development_key_points_cases.json is read by this test and by the web's keyPoints.test.mjs."""
    rows = [_point(*r) for r in case["rows"]]
    cont, peak = models.derive_continuous_peak(rows)
    assert (cont["name"] if cont else None) == case["continuous"]
    assert (peak["name"] if peak else None) == case["peak"]


def test_a_point_without_peak_clears_the_peak_but_keeps_its_period():
    draft = _all_unknown_draft()
    draft["fields"]["1_5"] = {"status": "filled", "value": {"torque_Nm": 99, "speed_rpm": 1, "frequency": "twice a flight", "period_s": 120}}
    draft["fields"]["key_points"] = {"status": "filled", "value": [_point("Cruise", 40, 2800, "continuous")]}
    out = models.normalize_draft(draft)
    assert out["fields"]["1_5"] == {"value": {"frequency": "twice a flight", "period_s": 120}, "status": "missing"}


@pytest.mark.parametrize("row,field", [
    (_point("", 5, 100, 10), "key_points[0].name"),
    (_point("x", None, 100, 10), "key_points[0].torque_Nm"),
    (_point("x", 5, None, 10), "key_points[0].speed_rpm"),
    (_point("x", 5, 100, None), "key_points[0].duration_s"),
    (_point("x", 5, 100, 0), "key_points[0].duration_s"),
])
def test_an_incomplete_point_is_a_loud_error_and_blocks_completeness(row, field):
    draft = _draft_with([row])
    assert [e["field"] for e in _errors(draft)] == [field]
    assert models.completeness(draft)["complete"] is False
    assert models.completeness(draft)["invalid"] == 1


def test_empty_list_and_unknown_marker():
    empty = _draft_with([])
    assert empty["fields"]["key_points"]["status"] == "missing"
    assert [e["field"] for e in _errors(empty)] == ["key_points"] and models.completeness(empty)["complete"] is False
    unknown = _draft_with(copy.deepcopy(FOUR), status="not_known_yet")
    assert _errors(unknown) == [] and models.completeness(unknown)["complete"] is True
    assert unknown["fields"]["1_4"]["status"] == "not_known_yet"


@pytest.mark.parametrize("bad", [
    [{"name": "x", "torque_Nm": "5", "speed_rpm": 1, "duration_s": 1}],
    [{"name": "x", "torque_Nm": float("nan"), "speed_rpm": 1, "duration_s": 1}],
    [{"name": "x", "torque_Nm": 1, "speed_rpm": -1, "duration_s": 1}],
    [{"name": "x", "torque_Nm": 1, "speed_rpm": 1, "duration_s": -3}],
    [{"name": "x", "torque_Nm": 1, "speed_rpm": 1, "duration_s": "sometimes"}],
    [{"name": "x", "torque_Nm": 1, "speed_rpm": 1, "duration_s": 1, "extra": 1}],
    [{"name": "x", "row_id": "a"}, {"name": "y", "row_id": "a"}],
    "not a list",
])
def test_bad_shapes_are_rejected_on_save(bad):
    draft = _all_unknown_draft()
    draft["fields"]["key_points"] = {"status": "filled", "value": bad}
    with pytest.raises(ValueError):
        models.normalize_draft(draft)


def test_key_points_are_not_a_v1_field():
    with pytest.raises(ValueError):
        models.normalize_draft({"schema_version": 1, "fields": {"key_points": {"status": "filled", "value": []}}})


def test_legacy_s1_and_peak_read_as_two_points_and_stay_valid_untouched():
    draft = _all_unknown_draft()
    draft["fields"]["1_4"] = {"status": "filled", "value": {"torque_Nm": 12.5, "speed_rpm": 3000}}
    draft["fields"]["1_5"] = {"status": "filled", "value": {"torque_Nm": {"minimum": 20, "maximum": 25}, "speed_rpm": 2800,
                                                            "duration_s": {"minimum": 10, "maximum": 30}, "period_s": 600}}
    draft["fields"].pop("key_points")
    legacy = models.normalize_draft(draft)
    assert "key_points" not in legacy["fields"]  # migration on read: nothing is written
    rows = models.key_points_effective(legacy)
    assert [(r["name"], r["torque_Nm"], r["speed_rpm"], r["duration_s"]) for r in rows] == [
        ("Continuous", 12.5, 3000, "continuous"), ("Peak", 25, 2800, 30)]
    assert models.completeness(legacy)["complete"] is True and models.validation_errors(legacy) == []
    # a legacy project whose S1 is incomplete is still judged by the old S1 rules
    broken = copy.deepcopy(legacy)
    broken["fields"]["1_4"] = {"status": "filled", "value": {"speed_rpm": 3000}}
    assert models.completeness(broken)["complete"] is False
    assert any(e["field"].startswith("1_4.") for e in models.validation_errors(broken))


def test_saving_the_migrated_rows_loses_nothing():
    draft = _all_unknown_draft()
    draft["fields"].pop("key_points")
    draft["fields"]["1_4"] = {"status": "filled", "value": {"torque_Nm": 12.5, "speed_rpm": 3000}}
    draft["fields"]["1_5"] = {"status": "filled", "value": {"torque_Nm": 25, "speed_rpm": 2800, "duration_s": 30, "frequency": "every takeoff", "period_s": 600}}
    legacy = models.normalize_draft(draft)
    migrated = copy.deepcopy(legacy)
    migrated["fields"]["key_points"] = {"status": "filled", "value": models.key_points_effective(legacy)}
    saved = models.normalize_draft(migrated)
    assert saved["fields"]["1_4"]["value"]["torque_Nm"] == 12.5 and saved["fields"]["1_4"]["value"]["speed_rpm"] == 3000
    peak = saved["fields"]["1_5"]["value"]
    assert (peak["torque_Nm"], peak["speed_rpm"], peak["duration_s"]) == (25, 2800, 30)
    assert peak["frequency"] == "every takeoff" and peak["period_s"] == 600  # the extras of the old peak survive
    assert models.completeness(saved)["complete"] is True and models.validation_errors(saved) == []


def test_a_legacy_power_only_s1_is_not_wiped_by_half_typed_points():
    draft = _all_unknown_draft()
    draft["fields"]["1_4"] = {"status": "filled", "value": {"power_W": 5000, "speed_rpm": 3000}}
    draft["fields"]["key_points"] = {"status": "missing", "value": [_point("Cruise", None, 3000, "continuous")]}
    out = models.normalize_draft(draft)
    assert out["fields"]["1_4"]["value"]["power_W"] == 5000
    assert models.completeness(out)["complete"] is False  # but the slot is judged by the stored key points, loudly
    assert _errors(out)


def test_voltage_is_optional_and_never_derived_into_the_slot():
    rows = [_point("Cruise", 40, 2800, "continuous"), _point("Peak", 90, 2500, 10, 640)]
    draft = _draft_with(rows)
    assert draft["fields"]["key_points"]["value"][0]["voltage_V"] is None
    assert draft["fields"]["key_points"]["value"][1]["voltage_V"] == 640
    assert models.validation_errors(draft) == []


def test_key_points_round_trip_through_the_project_api(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from motor_ai_sim import auth, users
    from motor_ai_sim.development import store
    from motor_ai_sim.development.routes import router

    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr(auth, "resolve_user", lambda token: {"email": "eng@example.test", "role": "user"} if token else None)
    monkeypatch.setattr(users, "get_user", lambda email: {"email_verified": True})
    store.create_project({"id": "alpha", "title": "Alpha", "customer_org": "Org", "status": "spec", "created_at": "t", "updated_at": "t",
                          "owner_engineer": "eng@example.test", "linked_motors": [], "notes": "", "pending_invites": [],
                          "members": [{"email": "eng@example.test", "role": "owner"}, {"email": "customer@example.test", "role": "customer"}]})
    store.save_json("alpha", "draft.json", {"draft": {"schema_version": 2}, "schema_version": 2, "revision": 0, "version": None})
    app = FastAPI()
    app.include_router(router)
    client, headers = TestClient(app), {"Authorization": "Bearer dev"}
    draft = _all_unknown_draft()
    draft["fields"]["key_points"] = {"status": "filled", "value": copy.deepcopy(FOUR)}
    assert client.put("/api/projects/alpha/spec", headers=headers, json={"draft": draft, "revision": 0}).status_code == 200
    got = client.get("/api/projects/alpha/spec", headers=headers).json()
    assert [r["name"] for r in got["draft"]["fields"]["key_points"]["value"]] == ["Takeoff", "Cruise", "Landing", "Peak"]
    assert got["draft"]["fields"]["1_4"]["value"]["torque_Nm"] == 40 and got["completeness"]["complete"] is True
    assert any(f["id"] == "key_points" for s in got["schema"]["sections"] for f in s["fields"])
    assert client.post("/api/projects/alpha/spec/submit", headers=headers, json={"revision": 1}).status_code == 200
    # a half-typed point autosaves, but blocks the submit loudly
    draft["fields"]["key_points"]["value"][0]["torque_Nm"] = None
    assert client.put("/api/projects/alpha/spec", headers=headers, json={"draft": draft, "revision": 2}).status_code == 200
    rejected = client.post("/api/projects/alpha/spec/submit", headers=headers, json={"revision": 3})
    assert rejected.status_code == 422
    assert [e["field"] for e in rejected.json()["detail"]["errors"]] == ["key_points[0].torque_Nm"]


def test_only_the_owner_and_admins_change_the_specification(tmp_path, monkeypatch):
    """Owner rule 2026-10-26: engineers and customers view the specification (and may post in the thread)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from motor_ai_sim import auth, users
    from motor_ai_sim.development import store
    from motor_ai_sim.development.routes import router

    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    actor = {"email": "pe@example.test", "role": "user"}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(actor) if token else None)
    monkeypatch.setattr(users, "get_user", lambda email: {"email_verified": True})
    store.create_project({"id": "alpha", "title": "Alpha", "customer_org": "Org", "status": "spec", "created_at": "t", "updated_at": "t",
                          "owner_engineer": "eng@example.test", "linked_motors": [], "notes": "", "pending_invites": [],
                          "members": [{"email": "eng@example.test", "role": "owner"}, {"email": "pe@example.test", "role": "engineer"}]})
    store.save_json("alpha", "draft.json", {"draft": {"schema_version": 2}, "schema_version": 2, "revision": 0, "version": None})
    app = FastAPI()
    app.include_router(router)
    client, headers = TestClient(app), {"Authorization": "Bearer dev"}
    body = {"draft": _all_unknown_draft(), "revision": 0}
    seen = client.get("/api/projects/alpha", headers=headers).json()
    assert seen["can_edit_spec"] is False
    for who in ({"email": "pe@example.test", "role": "user"}, {"email": "gl@example.test", "role": "engineer"}):
        actor.update(who)
        assert client.put("/api/projects/alpha/spec", headers=headers, json=body).status_code == 403
        assert client.post("/api/projects/alpha/spec/submit", headers=headers, json={"revision": 0}).status_code == 403
        assert client.post("/api/projects/alpha/spec/migration/preview", headers=headers).status_code == 403
    actor.update(email="adm@example.test", role="admin")
    assert client.get("/api/projects/alpha", headers=headers).json()["can_edit_spec"] is True
    assert client.put("/api/projects/alpha/spec", headers=headers, json=body).status_code == 200
    actor.update(email="eng@example.test", role="user")
    assert client.get("/api/projects/alpha", headers=headers).json()["can_edit_spec"] is True
