"""Controller sim (controller PR3, owner 2026-11-23): the model / measured results are entered by MOTRES staff, the requirements by
the project owner and admins - one PUT, refused whole when it touches a side the caller may not change; the board revisions and test
results records; the schema's limit keys."""
from __future__ import annotations

import json

import pytest

from motor_ai_sim.development import controller_sim as SIM
from motor_ai_sim.development import controller_spec as CS
from motor_ai_sim.development import store
from test_controller_spec import H, env  # noqa: F401 - the shared fixture

URL = "/api/projects/ctl/controller-spec"
SIM_URL = "/api/projects/ctl/controller-sim"
REQ = {"builds": {"value": ["6S", "12S"]},
       "i_cont": {"builds": {"6S": {"values": {"requirement": {"value": 33}}}, "12S": {"values": {"requirement": {"value": 21}}}}},
       "mcu": {"value": "M0"}}


def _owner_member(env):
    p = store.get_project("ctl")
    p["members"].append({"email": "own@example.test", "role": "owner"})
    store.save_json("ctl", "project.json", p)


def _with(fields, **kw):
    out = json.loads(json.dumps(fields))
    out.update(kw)
    return out


def test_staff_enter_results_owners_enter_requirements_and_neither_crosses(env):
    c = env["c"]
    _owner_member(env)
    assert c.put(URL, headers=H, json={"fields": REQ, "revision": 0}).status_code == 200           # admin: both sides
    model = _with(REQ, i_cont={"builds": {"6S": {"values": {"requirement": {"value": 33}, "model": {"value": 31.5, "ref": "sim 11-23"}}},
                                          "12S": {"values": {"requirement": {"value": 21}}}}})
    env["who"].update(email="eng@example.test", role="engineer")                                 # staff, not the project owner
    g = c.get(URL, headers=H).json()
    assert g["can_edit"] is False and g["can_edit_results"] is True
    r = c.put(URL, headers=H, json={"fields": model, "revision": 1})
    assert r.status_code == 200 and r.json()["revision"] == 2
    assert c.put(URL, headers=H, json={"fields": _with(model, mcu={"value": "M1"}), "revision": 2}).status_code == 403
    req_change = _with(model, i_cont={"builds": {"6S": {"values": {"requirement": {"value": 35}, "model": {"value": 31.5, "ref": "sim 11-23"}}},
                                                 "12S": {"values": {"requirement": {"value": 21}}}}})
    assert c.put(URL, headers=H, json={"fields": req_change, "revision": 2}).status_code == 403
    env["who"].update(email="own@example.test", role="user")                                     # the project owner, a customer
    g = c.get(URL, headers=H).json()
    assert g["can_edit"] is True and g["can_edit_results"] is False
    assert c.put(URL, headers=H, json={"fields": req_change, "revision": 2}).status_code == 200
    measured = _with(req_change, i_cont={"builds": {"6S": {"values": {"requirement": {"value": 35}, "model": {"value": 31.5, "ref": "sim 11-23"},
                                                                       "measured": {"value": 30}}}, "12S": {"values": {"requirement": {"value": 21}}}}})
    assert c.put(URL, headers=H, json={"fields": measured, "revision": 3}).status_code == 403
    saved = json.loads((env["root"] / "ctl" / CS.FILE).read_text())
    assert saved["revision"] == 3 and "measured" not in saved["fields"]["i_cont"]["builds"]["6S"]["values"]
    env["who"].update(email="cust@example.test", role="user")                                    # a member who is neither
    assert c.put(URL, headers=H, json={"fields": measured, "revision": 3}).status_code == 403


def test_the_scope_of_a_change():
    after = CS.normalize({"pwm": {"values": {"model": {"value": 1}}}, "mcu": {"value": "x"},
                          "protections": {"value": {"stall": {"unit": "s", "values": {"measured": {"value": 2}}}}}})
    pick = lambda *ids: {k: v for k, v in after.items() if k in ids}  # noqa: E731
    assert CS.change_scope({}, pick("pwm")) == {"results"}
    assert CS.change_scope({}, pick("mcu")) == {"requirements"}
    assert CS.change_scope({}, pick("protections")) == {"requirements", "results"}       # ticking it is a requirement
    assert CS.change_scope({}, CS.normalize({"pwm": {"values": {"requirement": {"value": 1}}}})) == {"requirements"}


def test_a_build_named_like_a_column_or_with_a_dot_or_empty_is_refused(env):
    """Astra on #836 (1): build names are user text and never decide the side of a change."""
    for bad in (["6S", "model"], ["Requirement"], ["6S.1"], ["6S", " "], ["measured"]):
        r = env["c"].put(URL, headers=H, json={"fields": {"builds": {"value": bad}}, "revision": 0})
        assert r.status_code == 422, (bad, r.text)


def test_the_side_of_a_change_comes_from_the_column_whatever_the_build_is_called(env):
    """Both directions, clearing, and an earlier single answer of a per-build field."""
    c = env["c"]
    base = {"builds": {"value": ["A1", "B2"]},
            "i_cont": {"builds": {"A1": {"values": {"requirement": {"value": 30}, "model": {"value": 29}}}, "B2": {"values": {"requirement": {"value": 20}}}}}}
    assert c.put(URL, headers=H, json={"fields": base, "revision": 0}).status_code == 200
    env["who"].update(email="eng@example.test", role="engineer")                       # staff: results only
    req = _with(base, i_cont={"builds": {"A1": {"values": {"requirement": {"value": 31}, "model": {"value": 29}}}, "B2": {"values": {"requirement": {"value": 20}}}}})
    assert c.put(URL, headers=H, json={"fields": req, "revision": 1}).status_code == 403
    clear_req = _with(base, i_cont={"builds": {"A1": {"values": {"model": {"value": 29}}}, "B2": {"values": {"requirement": {"value": 20}}}}})
    assert c.put(URL, headers=H, json={"fields": clear_req, "revision": 1}).status_code == 403        # clearing a requirement
    clear_model = _with(base, i_cont={"builds": {"A1": {"values": {"requirement": {"value": 30}}}, "B2": {"values": {"requirement": {"value": 20}}}}})
    assert c.put(URL, headers=H, json={"fields": clear_model, "revision": 1}).status_code == 200      # clearing a result
    _owner_member(env)
    env["who"].update(email="own@example.test", role="user")                          # the owner: requirements only
    model = _with(clear_model, i_cont={"builds": {"A1": {"values": {"requirement": {"value": 30}, "model": {"value": 28}}}, "B2": {"values": {"requirement": {"value": 20}}}}})
    assert c.put(URL, headers=H, json={"fields": model, "revision": 2}).status_code == 403
    assert c.put(URL, headers=H, json={"fields": _with(clear_model, builds={"value": ["A1", "B2", "C3"]}), "revision": 2}).status_code == 200


def test_an_earlier_single_answer_spread_over_the_builds_is_no_change_of_either_side(env):
    store.save_json("ctl", CS.FILE, {**CS.empty(), "revision": 1, "fields": {
        "builds": {"value": ["6S", "12S"]}, "i_cont": {"value": 33, "status": "model", "ref": "sim"}, "method": {"value": "foc_sensor"}}})
    env["who"].update(email="eng@example.test", role="engineer")
    got = env["c"].get(URL, headers=H).json()["fields"]
    r = env["c"].put(URL, headers=H, json={"fields": got, "revision": 1})
    assert r.status_code == 200 and r.json()["changed"] == []
    more = _with(got, i_cont={"builds": {b: {"values": {"model": {"value": 33, "ref": "sim"}, "measured": {"value": 32}}} for b in ("6S", "12S")}})
    assert env["c"].put(URL, headers=H, json={"fields": more, "revision": 1}).status_code == 200


def test_ticking_a_protection_or_changing_its_unit_is_a_requirement(env):
    """Astra on #836 (2): a protection with no value in any column is still the requirement side."""
    c = env["c"]
    base = {"protections": {"value": {"stall": {"unit": "s", "values": {"requirement": {"value": 2}}}}}}
    assert c.put(URL, headers=H, json={"fields": base, "revision": 0}).status_code == 200
    env["who"].update(email="eng@example.test", role="engineer")
    for bad in ({"protections": {"value": {"stall": {"unit": "s", "values": {"requirement": {"value": 2}}}, "over_current": {"unit": "", "values": {}}}}},
                {"protections": {"value": {"stall": {"unit": "ms", "values": {"requirement": {"value": 2}}}}}},
                {"protections": {"value": {}}}):
        r = c.put(URL, headers=H, json={"fields": bad, "revision": 1})
        assert r.status_code == 403, (bad, r.text)                              # refused whole: nothing of it is saved
    ok = {"protections": {"value": {"stall": {"unit": "s", "values": {"requirement": {"value": 2}, "model": {"value": 1.8}}}}}}
    assert c.put(URL, headers=H, json={"fields": ok, "revision": 1}).status_code == 200
    saved = json.loads((env["root"] / "ctl" / CS.FILE).read_text())
    assert list(saved["fields"]["protections"]["value"]) == ["stall"] and saved["fields"]["protections"]["value"]["stall"]["unit"] == "s"


def test_the_limits_are_on_plain_numbers_only():
    limited = {f["id"]: f["limit"] for s in CS.schema()["sections"] for f in s["fields"] if "limit" in f}
    assert limited["i_cont"] == "min" and limited["ripple"] == "max" and limited["eff_target"] == "min"
    kinds = {f["id"]: f["kind"] for s in CS.schema()["sections"] for f in s["fields"]}
    assert all(kinds[k] == "number" and v in ("min", "max") for k, v in limited.items())
    assert "bus_voltage" not in limited and "abs_max_voltage" not in limited


RECORDS = {"revisions": [{"rev": "R07", "status": "released", "date": "2026-11-13", "documents": "INFA_R07", "note": ""},
                         {"rev": "R08", "status": "draft", "date": "", "documents": "", "note": "board changes"}, {}],
           "tests": [{"date": "2026-11-25", "revision": "R07", "name": "double-pulse", "value": "66 V peak", "result": "pass", "ref": "scope 1"}]}


def test_staff_keep_the_revisions_and_tests_members_read_them(env):
    c = env["c"]
    assert c.get(SIM_URL, headers=H).json()["revisions"] == []
    r = c.put(SIM_URL, headers=H, json={**RECORDS, "revision": 0})
    assert r.status_code == 200 and r.json()["revision"] == 1 and r.json()["changed"] == ["revisions", "tests"]
    assert [x["rev"] for x in r.json()["revisions"]] == ["R07", "R08"]                     # the empty row is dropped
    assert c.put(SIM_URL, headers=H, json={**RECORDS, "revision": 0}).status_code == 409
    same = c.put(SIM_URL, headers=H, json={**RECORDS, "revision": 1})
    assert same.json()["revision"] == 1 and same.json()["changed"] == []
    for email, role in (("eng@example.test", "engineer"),):
        env["who"].update(email=email, role=role)
        assert c.put(SIM_URL, headers=H, json={**RECORDS, "tests": [], "revision": 1}).status_code == 200
    env["who"].update(email="cust@example.test", role="user")
    g = c.get(SIM_URL, headers=H)
    assert g.status_code == 200 and g.json()["can_edit"] is False and "eng@example.test" not in g.text   # author name, not address
    assert c.put(SIM_URL, headers=H, json={**RECORDS, "revision": 2}).status_code == 403


@pytest.mark.parametrize("bad", [{"revisions": [{"rev": "R1", "status": "gone"}]}, {"revisions": [{"date": "13.11.2026"}]},
                                 {"tests": [{"result": "maybe"}]}, {"tests": [{"name": ["x"]}]}, {"tests": "x"}, {"other": []}])
def test_wrong_records_are_refused(env, bad):
    r = env["c"].put(SIM_URL, headers=H, json={**bad, "revision": 0})
    assert r.status_code == 422, r.text
    assert not (env["root"] / "ctl" / SIM.FILE).exists()


def test_a_motor_project_has_no_controller_sim(env):
    c = env["c"]
    assert c.get("/api/projects/mot/controller-sim", headers=H).status_code == 404
    assert c.put("/api/projects/mot/controller-sim", headers=H, json={**RECORDS, "revision": 0}).status_code == 404
    assert not (env["root"] / "mot" / SIM.FILE).exists()
