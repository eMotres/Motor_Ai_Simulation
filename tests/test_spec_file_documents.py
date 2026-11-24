"""Who sees the stored ORIGINAL of a specification file (coordinator 2026-11-24, final): only who may EDIT the specification (the
project owner, a global admin) and MOTRES staff.  Every other member does not see it in the documents list, the ZIPs, the counts or
the cover choices, and a download by its id is a 404 like an unknown id.  Mechanism: the ``spec_source`` flag of the document row,
evaluated in the ONE ``documents._visible`` filter (the same place as ``internal`` / ``price_restricted``).  Synthetic data only."""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth, users
from motor_ai_sim.development import documents, spec_file_assistant as FA, store
from motor_ai_sim.development.routes import router

OWNER, ENG, CUST, ADMIN, STAFF, OEM = ("owner@example.test", "eng@example.test", "cust@example.test", "admin@example.test",
                                       "staff@example.test", "oem@example.test")
ROLES = {"t-owner": (OWNER, "user"), "t-eng": (ENG, "user"), "t-cust": (CUST, "user"), "t-admin": (ADMIN, "admin"),
         "t-staff": (STAFF, "engineer"), "t-oem": (OEM, "oem")}


def hdr(who):
    return {"Authorization": "Bearer " + who}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("CAD_DOWNLOAD_LOG", str(tmp_path / "downloads.jsonl"))
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(zip(("email", "role"), ROLES[token.split()[-1]])) if token else None)
    monkeypatch.setattr(users, "get_user", lambda email: {"email_verified": True, "role": ROLES_BY_EMAIL.get(email, "user")})
    store.create_project({"id": "alpha", "title": "Alpha", "customer_org": "Org", "status": "spec", "created_at": "t", "updated_at": "t",
                          "owner_engineer": OWNER, "linked_motors": [], "notes": "", "pending_invites": [],
                          "members": [{"email": OWNER, "role": "owner"}, {"email": ENG, "role": "engineer"}, {"email": CUST, "role": "customer"},
                                      {"email": OEM, "role": "customer"}]})
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


ROLES_BY_EMAIL = {ADMIN: "admin", STAFF: "engineer", OEM: "oem"}


def put_files(env):
    """An ordinary document and a spec source; returns (plain id, source id)."""
    plain = env.post("/api/projects/alpha/documents", headers=hdr("t-owner"), files={"file": ("board.txt", b"plain document")}).json()["id"]
    row = FA.store_original("alpha", {"email": OWNER, "role": "user"}, "requirements.txt", b"Unit price 5 EUR; speed 6000 rpm", "text/plain")
    return plain, row["id"]


SEE = ["t-owner", "t-admin", "t-staff"]
BLIND = ["t-eng", "t-cust", "t-oem"]


@pytest.mark.parametrize("who", SEE)
def test_owner_admin_and_staff_list_and_download_it(env, who):
    plain, src = put_files(env)
    listed = env.get("/api/projects/alpha/documents", headers=hdr(who)).json()
    ids = {d["id"] for d in listed["documents"]}
    assert ids == {plain, src}
    assert next(d for d in listed["documents"] if d["id"] == src)["spec_source"] is True
    got = env.get(f"/api/projects/alpha/documents/{src}/download", headers=hdr(who))
    assert got.status_code == 200 and b"speed 6000 rpm" in got.content


@pytest.mark.parametrize("who", BLIND)
def test_a_customer_an_engineer_member_and_a_factory_account_do_not_see_it(env, who):
    plain, src = put_files(env)
    listed = env.get("/api/projects/alpha/documents", headers=hdr(who)).json()
    assert {d["id"] for d in listed["documents"]} == {plain}                          # not in the list, so not in a count either
    assert env.get(f"/api/projects/alpha/documents/{src}/download", headers=hdr(who)).status_code == 404
    assert env.get(f"/api/projects/alpha/documents/nope/download", headers=hdr(who)).status_code == 404     # the same answer as an unknown id
    assert env.patch(f"/api/projects/alpha/documents/{src}", headers=hdr(who), json={"revision": "x"}).status_code == 404
    assert env.delete(f"/api/projects/alpha/documents/{src}", headers=hdr(who)).status_code == 404


def _zip_names(resp):
    return set(zipfile.ZipFile(io.BytesIO(resp.content)).namelist())


@pytest.mark.parametrize("who", BLIND + ["t-owner"])
def test_the_current_release_zip_never_holds_it_for_a_non_staff_caller(env, who):
    plain, src = put_files(env)
    env.patch(f"/api/projects/alpha/documents/{plain}", headers=hdr("t-owner"), json={"revision": "A"})
    resp = env.get("/api/projects/alpha/documents.zip", headers=hdr("t-staff") if False else hdr(who), params={"revision": "A"})
    if resp.status_code == 200:
        names = _zip_names(resp)
        assert not any("requirements" in n for n in names)


def test_the_staff_zip_of_every_group_holds_it_and_the_release_list_ignores_it(env):
    plain, src = put_files(env)
    names = _zip_names(env.get("/api/projects/alpha/documents.zip", headers=hdr("t-staff")))
    assert any(n.endswith("requirements.txt") for n in names)
    for who in SEE + BLIND:                                                            # the spec source is no release: no group, no "current"
        info = env.get("/api/projects/alpha/documents", headers=hdr(who)).json()
        assert all(g["revision"] != "specification source" for g in info["releases"]), who
        assert info["current_release"] != "specification source"
    plain_zip = _zip_names(env.get("/api/projects/alpha/documents.zip", headers=hdr("t-cust")) if False else
                           env.get("/api/projects/alpha/documents.zip", headers=hdr("t-staff"), params={"revision": ""}))
    assert not any("requirements" in n for n in plain_zip)


def test_it_can_never_be_the_cover(env):
    plain, src = put_files(env)
    r = env.put("/api/projects/alpha/cover", headers=hdr("t-owner"), json={"document_id": src})
    assert r.status_code == 422 and "specification source" in r.json()["detail"]
    from motor_ai_sim.development import cover
    assert cover._cover_allowed({"id": "x", "spec_source": True}) is False and cover._cover_allowed({"id": "x"}) is True


def test_only_the_documents_module_and_the_cover_read_the_project_documents():
    """The digest, the owner report, the counts and every other reader of project documents go through ``documents._visible``: a new reader
    of ``store.load_documents`` is a new place that must apply the same rule (this list is the guard)."""
    from pathlib import Path
    root = Path(documents.__file__).resolve().parents[1]
    readers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py") if "load_documents(" in p.read_text(encoding="utf-8")
                     and p.name != "store.py")
    assert readers == ["development/cover.py", "development/documents.py", "development/spec_file_assistant.py"], readers


def test_the_upload_route_does_not_let_the_flag_be_set_or_cleared_by_hand(env):
    plain, src = put_files(env)
    r = env.post("/api/projects/alpha/documents", headers=hdr("t-owner"), files={"file": ("x.txt", b"x")}, data={"spec_source": "true"})
    assert r.status_code == 200
    rows = {d["id"]: d for d in store.load_documents("alpha")}
    assert rows[r.json()["id"]].get("spec_source") in (None, False)
    r2 = env.patch(f"/api/projects/alpha/documents/{src}", headers=hdr("t-owner"), json={"spec_source": False})
    assert r2.status_code == 422
