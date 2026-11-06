"""New customer path (owner 2026-11-05): the Platform Agreement reaches a customer before the project starts and before the specification goes
to MOTRES, because MOTRES runs the simulations for now and the gate at a job start alone would never reach them.  One internal mail tells staff
about a new customer project and a submitted specification.  Everything runs on tmp_path; no mail leaves."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from motor_ai_sim import auth_email as E, nda
from motor_ai_sim.development import routes, store
from tests.test_development_core import _all_unknown_draft
from tests.test_legal_texts import AGREE, ANNA, client, env  # noqa: F401  (fixture)

FLAG = "PLATFORM_AGREEMENT_AT_SIMULATION"
USER = {"email": ANNA, "role": "user"}
STAFF = {"email": "eng@motres-staff.test", "role": "engineer"}


@pytest.fixture()
def path(env, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    mails: list = []
    monkeypatch.setattr(E, "send", lambda to, subject, body, **kw: mails.append((to, subject, body)) or True)
    monkeypatch.setattr(nda, "admin_address", lambda: "owner@motres-staff.test")
    return {"env": env, "mails": mails}


def _accept(path):
    r = client.post("/api/legal/accept-agreement", json=AGREE, headers=path["env"]["h"](ANNA))
    assert r.status_code == 200, r.text


def _project(user=USER):
    return routes.create_project_for(user, {"title": "Pump motor", "customer_org": "Some company"})


def _fill(project_id):
    store.save_json(project_id, "draft.json", {"draft": _all_unknown_draft(), "revision": 1, "version": None})


def test_without_the_agreement_a_customer_cannot_open_a_project(path, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    with pytest.raises(HTTPException) as e:
        _project()
    assert e.value.status_code == 428 and e.value.detail["code"] == "agreement_required" and e.value.detail["at"] == "simulation"
    assert store.list_projects() == [] and path["mails"] == []


def test_with_the_agreement_the_project_opens_and_staff_get_one_mail(path, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    _accept(path)
    project = _project()
    assert project["status"] == "spec"
    (to, subject, body), = path["mails"]
    assert to == "owner@motres-staff.test" and "New customer project" in subject and "Pump motor" in subject
    assert ANNA in body and f"/projects/{project['id']}/specification" in body


def test_submit_is_gated_too_and_tells_staff(path, monkeypatch):
    project = _project()                     # opened while the switch was off: an older project of the customer
    _fill(project["id"])
    path["mails"].clear()
    monkeypatch.setenv(FLAG, "1")
    with pytest.raises(HTTPException) as e:
        routes.submit_spec_for(USER, project["id"], 1)
    assert e.value.status_code == 428 and path["mails"] == []
    _accept(path)
    done = routes.submit_spec_for(USER, project["id"], 1)
    assert done["version"]["version"] == 1
    (_to, subject, _body), = path["mails"]
    assert "Specification v1 submitted" in subject


def test_staff_are_not_gated_and_send_no_mail(path, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    assert _project(STAFF)["id"] and path["mails"] == []


def test_switch_off_means_no_gate(path, monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    assert _project()["id"]


def test_mail_trouble_never_blocks_the_customer(path, monkeypatch):
    def boom(*a, **k):
        raise OSError("smtp down")
    monkeypatch.setattr(E, "send", boom)
    assert _project()["id"]


def _save(project_id, draft, revision, *, tolerant=True):
    return routes.save_spec_for(USER, project_id, {"draft": draft, "revision": revision, "schema_version": 2}, tolerant=tolerant)


def test_one_bad_value_is_set_aside_and_every_other_field_still_saves(path):
    pid = _project()["id"]
    draft = {"schema_version": 2, "mission_profile": [], "mission_profile_status": "missing", "production": {},
             "fields": {"load_inertia_kg_m2": {"status": "filled", "value": -5}, "startup_mode": {"status": "filled", "value": "loaded"}}}
    saved = _save(pid, draft, 0)
    assert saved["rejected"] == [{"field": "load_inertia_kg_m2", "path": "load_inertia_kg_m2", "message": "value outside allowed range"}]
    stored = store.load_json(pid, "draft.json", None)
    assert "load_inertia_kg_m2" not in stored["draft"]["fields"]                       # the bad value is not stored ...
    assert stored["draft"]["fields"]["startup_mode"]["value"] == "loaded"                # ... and the rest is
    assert "rejected" not in stored                                                      # the refusal is an answer, not data
    # the next autosave carries the bad value again: it is refused again, nothing typed elsewhere is lost
    draft["fields"]["startup_mode"] = {"status": "filled", "value": "unloaded"}
    again = _save(pid, draft, saved["revision"])
    assert [r["field"] for r in again["rejected"]] == ["load_inertia_kg_m2"] and again["draft"]["fields"]["startup_mode"]["value"] == "unloaded"
    # a corrected value saves; a stored good value stays when a later bad one is refused
    draft["fields"]["load_inertia_kg_m2"] = {"status": "filled", "value": 0.35}
    fixed = _save(pid, draft, again["revision"])
    assert fixed["rejected"] == [] and fixed["draft"]["fields"]["load_inertia_kg_m2"]["value"] == 0.35
    draft["fields"]["load_inertia_kg_m2"] = {"status": "filled", "value": -1}
    kept = _save(pid, draft, fixed["revision"])
    assert kept["draft"]["fields"]["load_inertia_kg_m2"]["value"] == 0.35 and kept["rejected"][0]["field"] == "load_inertia_kg_m2"


def test_agents_through_the_order_tools_still_get_a_strict_422(path):
    pid = _project()["id"]
    body = {"draft": {"schema_version": 2, "mission_profile": [], "mission_profile_status": "missing", "production": {},
                      "fields": {"load_inertia_kg_m2": {"status": "filled", "value": -5}}}, "revision": 0, "schema_version": 2}
    with pytest.raises(HTTPException) as strict:
        routes.save_spec_for(USER, pid, body)
    assert strict.value.status_code == 422 and "load_inertia_kg_m2" in strict.value.detail


def test_submitting_the_same_specification_twice_makes_no_second_version_and_no_second_mail(path):
    import copy
    pid = _project()["id"]
    _fill(pid)
    path["mails"].clear()
    first = routes.submit_spec_for(USER, pid, 1)
    assert first["version"]["version"] == 1 and "unchanged" not in first and len(path["mails"]) == 1
    wrapper = store.load_json(pid, "draft.json", None)
    again = routes.submit_spec_for(USER, pid, wrapper["revision"])
    assert again["unchanged"] is True and again["version"]["version"] == 1
    assert len(path["mails"]) == 1                                                       # staff are not told twice
    assert [v["version"] for v in routes._versions(pid)] == [1]
    state = routes._submitted_state(pid, store.load_json(pid, "draft.json", None))
    assert state["version"] == 1 and state["unchanged"] is True and state["submitted_at"]
    # a changed specification can be submitted again, as version 2, with its own mail
    current = store.load_json(pid, "draft.json", None)
    changed = copy.deepcopy(current["draft"])
    changed["fields"]["startup_mode"] = {"value": "loaded", "status": "filled"}
    saved = routes.save_spec_for(USER, pid, {"draft": changed, "revision": current["revision"], "schema_version": 2})
    assert routes._submitted_state(pid, store.load_json(pid, "draft.json", None))["unchanged"] is False
    second = routes.submit_spec_for(USER, pid, saved["revision"])
    assert second["version"]["version"] == 2 and "unchanged" not in second and len(path["mails"]) == 2
