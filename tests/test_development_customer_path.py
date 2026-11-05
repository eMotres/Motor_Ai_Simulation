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
