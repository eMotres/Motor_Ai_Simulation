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
    ch = CS.changes({}, {"pwm": {"values": {"model": {"value": 1}}}, "mcu": {"value": "x"},
                         "protections": {"value": {"stall": {"unit": "s", "values": {"measured": {"value": 2}}}}}})
    assert CS.change_scope([c for c in ch if c["field"] == "pwm"]) == {"results"}
    assert CS.change_scope([c for c in ch if c["field"] == "mcu"]) == {"requirements"}
    assert CS.change_scope([c for c in ch if c["field"] == "protections"]) == {"results"}
    assert CS.change_scope(CS.changes({}, {"pwm": {"values": {"requirement": {"value": 1}}}})) == {"requirements"}


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
