"""The simulation minimum (owner 2026-11-11): a specification is not submitted while the data needed to simulate the machine
is missing, and "not known yet" does not satisfy those fields.  The spec assistant is told what is missing.  tmp_path only."""
from __future__ import annotations

import copy

import pytest
from fastapi import HTTPException

from motor_ai_sim.development import models, routes, spec_assistant as A, store
from motor_ai_sim.development.spec_assistant_prompt import SPEC_PROMPT
from tests.test_development_core import _all_unknown_draft, simulation_minimum_fields

OWN = "own@example.test"
UNKNOWN = {"status": "not_known_yet", "value": None}


def _draft(**changes):
    """A draft that meets the minimum; `changes` replace answers (None removes one)."""
    draft = models.normalize_draft(_all_unknown_draft())
    for fid, answer in changes.items():
        if answer is None:
            draft["fields"].pop(fid, None)
        else:
            draft["fields"][fid] = answer
    return draft


def _fields(draft):
    return [m["field"] for m in models.simulation_missing(draft)]


def _filled(value):
    return {"status": "filled", "value": value}


def test_a_complete_minimum_is_ready_and_submittable():
    draft = _draft()
    assert models.simulation_minimum_errors(draft) == []
    stats = models.completeness(draft)
    assert stats["simulation_ready"] is True and stats["simulation_missing"] == []
    assert models.validation_errors(draft) == []


def test_everything_not_known_yet_is_complete_but_not_simulation_ready():
    draft = models.normalize_draft(_all_unknown_draft(simulation=False))
    stats = models.completeness(draft)
    assert stats["complete"] is True and stats["simulation_ready"] is False
    assert _fields(draft) == ["key_points", "1_6", "2_1", "3_1", "3_3"]
    errors = models.validation_errors(draft)
    sim = [e for e in errors if e.get("simulation")]
    assert {e["field"] for e in sim} == set(_fields(draft))
    assert all("needed for the simulation" in e["message"] and e["section"] in "123" for e in sim)


@pytest.mark.parametrize("field,answer,missing", [
    ("key_points", UNKNOWN, "key_points"),
    ("key_points", _filled([{"name": "A", "torque_Nm": 5, "speed_rpm": None, "duration_s": "continuous"}]), "key_points"),
    ("1_6", UNKNOWN, "1_6"),
    ("2_1", UNKNOWN, "2_1"),
    ("3_1", UNKNOWN, "3_1"),
    ("3_3", UNKNOWN, "3_3"),
])
def test_not_known_yet_does_not_satisfy_a_minimum_field(field, answer, missing):
    draft = _draft(**{field: answer})
    assert missing in _fields(draft)
    assert any(e["field"].startswith(missing) for e in models.validation_errors(draft))  # (an incomplete answer keeps its own exact errors)
    # the same answer on a field outside the minimum stays allowed
    assert models.validation_errors(_draft(**{"1_1": UNKNOWN})) == []


def test_legacy_s1_point_counts_when_no_key_points_are_stored():
    draft = _draft(key_points=None, **{"1_4": _filled({"speed_rpm": 3000, "power_W": 1200})})
    assert "key_points" not in _fields(draft)
    draft = _draft(key_points=None, **{"1_4": _filled({"speed_rpm": 3000})})
    assert "key_points" in _fields(draft)


def test_motor_needs_the_supply_a_generator_may_give_its_output_voltage_instead():
    assert _fields(_draft(**{"2_1": UNKNOWN, "1_2": _filled("Motor, one direction")})) == ["2_1"]
    output = _filled({"ac_min_V": 400, "basis": "line_line_rms"})
    assert _fields(_draft(**{"2_1": UNKNOWN, "1_2": _filled("Generator"), "2_13": output})) == []
    no_basis = _filled({"ac_min_V": 400})
    assert _fields(_draft(**{"2_1": UNKNOWN, "1_2": _filled("Generator"), "2_13": no_basis})) == ["2_13"]
    assert _fields(_draft(**{"2_1": UNKNOWN, "1_2": _filled("Generator"), "2_13": UNKNOWN})) == ["2_13"]
    # a motor with only an output voltage is still missing its supply
    assert _fields(_draft(**{"2_1": UNKNOWN, "1_2": _filled("Motor with regeneration"), "2_13": output})) == ["2_1"]
    # a generator that gives the DC bus needs nothing more
    assert _fields(_draft(**{"1_2": _filled("Generator")})) == []


@pytest.mark.parametrize("cooling,ambient,missing", [
    ({"cooling_medium": "air"}, _filled({"temperature_max_C": 40}), []),
    ({"cooling_medium": "air"}, UNKNOWN, ["3_2.temperature_max_C"]),
    ({"cooling_medium": "liquid"}, UNKNOWN, ["3_1.inlet_temperature_C"]),
    ({"cooling_medium": "liquid", "inlet_temperature_C": 30}, UNKNOWN, []),
    ({"cooling_medium": "submerged"}, UNKNOWN, ["3_1.water_temperature_C"]),
    ({"cooling_medium": "submerged", "water_temperature_C": 15}, UNKNOWN, []),
    ({"coolant": "Water-glycol 50/50", "flow_rate": 5}, UNKNOWN, ["3_1.inlet_temperature_C"]),  # medium inferred: liquid
])
def test_each_cooling_medium_needs_its_one_condition(cooling, ambient, missing):
    draft = _draft(**{"3_1": _filled(cooling), "3_2": ambient})
    assert _fields(draft) == missing


def test_submit_refuses_with_per_field_errors_and_accepts_when_filled(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr(routes.platform_agreement, "require_accepted", lambda user: None)
    monkeypatch.setattr(routes, "_require_data_agreement", lambda user: None)
    user = {"email": OWN, "role": "admin"}
    project = routes.create_project_for(user, {"title": "Pump", "customer_org": "Org"})
    pid = project["id"]
    store.save_json(pid, "draft.json", {"draft": _all_unknown_draft(simulation=False), "revision": 1, "schema_version": 2, "version": None})
    with pytest.raises(HTTPException) as refused:
        routes.submit_spec_for(user, pid, 1)
    detail = refused.value.detail
    assert refused.value.status_code == 422 and "simulation" in detail["message"]
    fields = {e["field"] for e in detail["errors"] if e.get("simulation")}
    assert fields == {"key_points", "1_6", "2_1", "3_1", "3_3"}
    assert all(set(e) >= {"field", "section", "message"} for e in detail["errors"])
    assert detail["completeness"]["simulation_ready"] is False and len(detail["completeness"]["simulation_missing"]) == 5
    store.save_json(pid, "draft.json", {"draft": _all_unknown_draft(), "revision": 2, "schema_version": 2, "version": None})
    assert routes.submit_spec_for(user, pid, 2)["version"]["version"] == 1


def test_mcp_submit_goes_through_the_same_check(tmp_path, monkeypatch):
    from motor_ai_sim.development import spec_order
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    draft = models.normalize_draft(_all_unknown_draft(simulation=False))
    needs = spec_order.needs_input(draft)
    sim = [n for n in needs if n["field"] in ("key_points", "1_6", "2_1", "3_1", "3_3")]
    assert len(sim) == 5 and all(n["accepts_not_known_yet"] is False for n in sim)
    view = spec_order.completeness_view(draft)
    assert view["simulation_ready"] is False and "3_3" in view["simulation_missing"]
    assert spec_order.needs_input(models.normalize_draft(_all_unknown_draft())) == []


def test_assistant_context_lists_the_missing_simulation_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    project = {"id": "alpha", "title": "Alpha", "customer_org": "Org", "status": "spec", "created_at": "t", "updated_at": "t",
               "owner_engineer": OWN, "members": [{"email": OWN, "role": "owner"}], "linked_motors": [], "notes": "",
               "pending_invites": []}
    store.create_project(project)
    draft = _all_unknown_draft(simulation=False)
    wrapper = {"draft": draft, "revision": 1, "schema_version": 2, "version": None}
    ctx = A.build_context(project, wrapper, [], lang="en", can_apply=True)
    block = ctx.split("SIMULATION MINIMUM still missing")[1].split("REQUIRED fields still missing")[0]
    for fid in ("key_points", "1_6", "2_1", "3_1", "3_3"):
        assert fid in block
    assert "FIRST" in ctx
    ready = copy.deepcopy(wrapper)
    ready["draft"] = _all_unknown_draft()
    done = A.build_context(project, ready, [], lang="en", can_apply=True)
    assert "(none - the minimum for the simulation is complete)" in done
    assert "SIMULATION MINIMUM" in SPEC_PROMPT and "FIRST" in SPEC_PROMPT and "never invent" in SPEC_PROMPT
