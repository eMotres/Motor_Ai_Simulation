"""The outer-diameter choice of the specification (4_1 ``od_mode``, owner 2026-11-25): completeness both ways, the public size list
(no non-public die, no name, no project data), reading old versions as "custom".  Synthetic data only; no solver, no network."""
from __future__ import annotations

import copy
import json

import pytest

from motor_ai_sim.development import models, spec_sizes as S


def _draft(env):
    return {"schema_version": 2, "fields": {"4_1": {"status": "filled", "value": env}}, "mission_profile": [], "production": {}}


def _missing_4_1(env):
    return [e for e in models.validation_errors(_draft(env)) if str(e["field"]).startswith("4_1")]


SIZE = {"od_mm": 80, "slots": 12, "poles": 14}


def test_old_version_reads_as_custom_and_stays_complete():
    assert models.od_mode_of({"outer_diameter_mm": 90}) == "custom"
    assert models.od_mode_of(None) == "custom"
    assert models.od_mode_of({"od_mode": "fixed"}) == "custom"
    assert _missing_4_1({"outer_diameter_mm": 90}) == []
    choice = models.od_choice(_draft({"outer_diameter_mm": 90}))
    assert choice == {"od_mode": "custom", "od_preferred_mm": None, "od_max_mm": 90, "accepted_sizes": []}


def test_custom_needs_a_diameter_or_length_and_ignores_stale_sizes():
    assert _missing_4_1({"od_mode": "custom"})
    assert _missing_4_1({"od_mode": "custom", "od_accepted_sizes": [SIZE]})
    assert _missing_4_1({"od_mode": "custom", "outer_diameter_mm": 90}) == []
    assert models.od_choice(_draft({"od_mode": "custom", "outer_diameter_mm": 90, "od_accepted_sizes": [SIZE]}))["accepted_sizes"] == []


def test_existing_needs_ticked_sizes_and_not_the_exact_diameter():
    assert _missing_4_1({"od_mode": "existing"})                                      # nothing ticked: refused
    assert _missing_4_1({"od_mode": "existing", "od_accepted_sizes": []})
    assert _missing_4_1({"od_mode": "existing", "od_accepted_sizes": [SIZE]}) == []   # no exact diameter needed
    assert _missing_4_1({"od_mode": "existing", "od_preferred_mm": {"minimum": 70, "maximum": 90}, "outer_diameter_mm": 100,
                         "od_accepted_sizes": [SIZE]}) == []
    choice = models.od_choice(_draft({"od_mode": "existing", "od_preferred_mm": 75, "outer_diameter_mm": 100,
                                      "od_accepted_sizes": [SIZE, {"od_mm": "x"}]}))
    assert choice == {"od_mode": "existing", "od_preferred_mm": 75, "od_max_mm": 100, "accepted_sizes": [SIZE]}


def test_the_choice_normalizes_and_refuses_unknown_values():
    ok = {"schema_version": 2, "fields": {"4_1": {"status": "filled", "value": {"od_mode": "existing", "od_accepted_sizes": [SIZE]}}}}
    assert models.normalize_draft(copy.deepcopy(ok))["fields"]["4_1"]["value"]["od_mode"] == "existing"
    bad = copy.deepcopy(ok)
    bad["fields"]["4_1"]["value"]["od_mode"] = "whatever"
    with pytest.raises(ValueError):
        models.normalize_draft(bad)
    bad = copy.deepcopy(ok)
    bad["fields"]["4_1"]["value"]["od_accepted_sizes"] = [{"od_mm": 80, "slots": 12, "poles": 14, "die": "X"}]
    with pytest.raises(ValueError):
        models.normalize_draft(bad)


# -- the public size list ---------------------------------------------------------------------------------------------

def _dies():
    return [
        {"name": "SECRETDIE A", "layer": "shared", "stator_diameter": 80, "slots": 12, "poles": 14, "owner": "a@example.com",
         "configs": [{"name": "SECRETCFG 1", "card_date": "2026-01-01", "stack_mm": 10}, {"name": "SECRETCFG 2", "card_date": "2026-01-02", "stack_mm": 15.04},
                     {"name": "NOCARD", "stack_mm": 99}, {"name": "ARCH", "card_date": "2026-01-03", "stack_mm": 77}]},
        {"name": "SECRETDIE B", "layer": "shared", "fields": {"stator_diameter": 120, "slots": 24, "poles": 20}, "configs": [{"name": "C", "card_date": "x"}]},
        {"name": "WORKSPACE DIE", "layer": "workspace", "stator_diameter": 50, "slots": 9, "poles": 6, "configs": [{"name": "W", "card_date": "x", "stack_mm": 5}]},
        {"name": "EMPTY DIE", "layer": "shared", "stator_diameter": 60, "slots": 9, "poles": 8, "configs": [{"name": "NOCARD"}]},
        {"name": "BROKEN", "layer": "shared", "stator_diameter": None, "slots": 9, "poles": 8, "configs": [{"name": "C", "card_date": "x"}]},
    ]


def test_build_sizes_is_public_only_and_leaks_no_name():
    rows = S.build_sizes(_dies(), archived=lambda d, c: c == "ARCH")
    assert rows == [{"od_mm": 80.0, "slots": 12, "poles": 14, "stack_mm": [10.0, 15.0]},
                    {"od_mm": 120.0, "slots": 24, "poles": 20, "stack_mm": []}]
    S.assert_allowlisted(rows)
    text = json.dumps(rows)
    for secret in ("SECRETDIE", "SECRETCFG", "NOCARD", "WORKSPACE", "a@example.com", "owner", "ARCH", "EMPTY"):
        assert secret not in text


def test_select_orders_by_closeness_and_keeps_the_hard_maximum():
    rows = [{"od_mm": float(o), "slots": 12, "poles": 14, "stack_mm": []} for o in (50, 80, 100, 150)]
    assert [r["od_mm"] for r in S.select(rows, preferred_min=95, preferred_max=95)] == [100, 80, 50, 150]
    assert [r["od_mm"] for r in S.select(rows, preferred_min=70, preferred_max=110)] == [80, 100, 50, 150]    # inside the range = 0
    assert [r["od_mm"] for r in S.select(rows, preferred_min=95, max_od=100)] == [100, 80, 50]                  # 150 is over the limit
    assert [r["od_mm"] for r in S.select(rows)] == [50, 80, 100, 150]
    assert rows[0]["od_mm"] == 50.0                                                                          # the input is not changed


def test_the_route_answers_members_only_and_never_with_a_project_field(monkeypatch):
    from starlette.requests import Request
    from fastapi import HTTPException
    from motor_ai_sim.development import access
    monkeypatch.setattr(S, "public_sizes", lambda: S.build_sizes(_dies(), archived=lambda d, c: False))
    seen = []
    monkeypatch.setattr(access, "require_project", lambda request, pid: seen.append(pid) or ({}, {"title": "PROJECT SECRET"}, None))
    monkeypatch.setattr(S.store, "valid_id", lambda pid: pid == "p1")
    req = Request({"type": "http", "method": "GET", "path": "/", "query_string": b"preferred_min_mm=118&max_mm=130", "headers": []})
    out = S.existing_sizes(req, "p1")
    assert seen == ["p1"] and out["available"] is True
    assert [r["od_mm"] for r in out["sizes"]] == [120.0, 80.0]
    assert "PROJECT SECRET" not in json.dumps(out)
    with pytest.raises(HTTPException) as e:
        S.existing_sizes(req, "nope")
    assert e.value.status_code == 404 and seen == ["p1"]


def test_an_unavailable_list_does_not_break_the_form(monkeypatch):
    from starlette.requests import Request
    from motor_ai_sim.development import access
    monkeypatch.setattr(S, "public_sizes", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(access, "require_project", lambda request, pid: ({}, {}, None))
    monkeypatch.setattr(S.store, "valid_id", lambda pid: True)
    req = Request({"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": []})
    assert S.existing_sizes(req, "p1") == {"sizes": [], "available": False}


def test_the_assistant_asks_early_without_promises():
    from motor_ai_sim import support_prompt as SP
    text = SP.SPEC_SECTION
    assert "Outer diameter choice" in text and "existing sizes" in text
    import re
    assert not re.search(r"\b(prices?|costs?|quotes?|budget|fees?)\b", text.split("Outer diameter choice")[1].split("- Answers are SHORT")[0], re.I)
