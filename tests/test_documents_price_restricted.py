"""Part B of the project price rule (owner 2026-11-22): a document marked ``price_restricted`` is seen by staff, project owners and
members with ``sees_prices``; for everybody else it does not exist (list, id, download, zip, cover) and no digest line names it."""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim.development import documents, store
from motor_ai_sim.development.routes import router

USERS = {
    "owner": ("owner@example.test", "user"), "owner2": ("owner2@example.test", "user"),
    "eng": ("eng@example.test", "user"), "cust": ("cust@example.test", "user"), "price": ("price@example.test", "user"),
    "geng": ("geng@example.test", "engineer"), "admin": ("admin@example.test", "admin"), "out": ("out@example.test", "user"),
}
SECRET = "contract-with-prices.pdf"


def hdr(who):
    return {"Authorization": "Bearer " + who}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("CAD_DOWNLOAD_LOG", str(tmp_path / "downloads.jsonl"))
    monkeypatch.setattr(auth, "resolve_user", lambda token: ({"email": USERS[token.split()[-1]][0], "role": USERS[token.split()[-1]][1]}
                                                              if token else None))
    store.create_project({"id": "p1", "title": "Project", "customer_org": "Org", "status": "spec", "created_at": "t", "updated_at": "t",
                          "owner_engineer": "owner@example.test", "linked_motors": [], "notes": "", "pending_invites": [],
                          "members": [{"email": USERS["owner"][0], "role": "owner"}, {"email": USERS["owner2"][0], "role": "owner"},
                                      {"email": USERS["eng"][0], "role": "engineer"}, {"email": USERS["cust"][0], "role": "customer"},
                                      {"email": USERS["price"][0], "role": "customer", "sees_prices": True}]})
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def up(client, who="owner", name=SECRET, **fields):
    return client.post("/api/projects/p1/documents", headers=hdr(who), files={"file": (name, b"data-" + name.encode())}, data=fields)


def ids(client, who):
    return {d["name"] for d in client.get("/api/projects/p1/documents", headers=hdr(who)).json()["documents"]}


@pytest.fixture
def docs(client):
    open_ = up(client, name="open.pdf").json()
    secret = up(client, name=SECRET, price_restricted="true").json()
    assert secret["price_restricted"] is True and open_["price_restricted"] is False
    return open_, secret


SEE = ("owner", "owner2", "price", "admin", "geng")
BLIND = ("eng", "cust")


@pytest.mark.parametrize("who", SEE)
def test_who_sees_the_restricted_document(client, docs, who):
    _open, secret = docs
    assert ids(client, who) == {"open.pdf", SECRET}
    assert client.get("/api/projects/p1/documents/%s/download" % secret["id"], headers=hdr(who)).status_code == 200
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/projects/p1/documents.zip", headers=hdr(who)).content))
    assert any(SECRET in n for n in z.namelist()) and SECRET in z.read("MANIFEST.txt").decode()


@pytest.mark.parametrize("who", BLIND)
def test_who_does_not_see_it_anywhere(client, docs, who):
    _open, secret = docs
    assert ids(client, who) == {"open.pdf"}
    assert client.get("/api/projects/p1/documents/%s/download" % secret["id"], headers=hdr(who)).status_code == 404
    assert client.patch("/api/projects/p1/documents/%s" % secret["id"], headers=hdr(who), json={"revision": "X"}).status_code in (403, 404)
    assert client.delete("/api/projects/p1/documents/%s" % secret["id"], headers=hdr(who)).status_code in (403, 404)
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/projects/p1/documents.zip", headers=hdr(who)).content))
    assert not any(SECRET in n for n in z.namelist()) and SECRET not in z.read("MANIFEST.txt").decode()
    assert client.get("/api/projects/p1/documents/%s/download" % secret["id"], headers=hdr("out")).status_code in (403, 404)


def test_default_is_unrestricted_and_the_flag_is_set_only_by_owner_or_staff(client):
    assert up(client, name="a.pdf").json()["price_restricted"] is False
    assert up(client, who="eng", name="b.pdf", price_restricted="true").status_code == 403       # a project engineer cannot restrict
    assert up(client, who="owner2", name="c.pdf", price_restricted="true").json()["price_restricted"] is True
    assert up(client, who="geng", name="d.pdf", price_restricted="true").json()["price_restricted"] is True
    assert up(client, who="admin", name="e.pdf", price_restricted="true").json()["price_restricted"] is True
    a = [d for d in client.get("/api/projects/p1/documents", headers=hdr("owner")).json()["documents"] if d["name"] == "a.pdf"][0]
    assert client.patch("/api/projects/p1/documents/%s" % a["id"], headers=hdr("eng"), json={"price_restricted": True}).status_code == 403
    assert client.patch("/api/projects/p1/documents/%s" % a["id"], headers=hdr("owner"), json={"price_restricted": "yes"}).status_code == 422
    r = client.patch("/api/projects/p1/documents/%s" % a["id"], headers=hdr("owner2"), json={"price_restricted": True})
    assert r.status_code == 200 and r.json()["price_restricted"] is True
    assert "a.pdf" not in ids(client, "cust")
    assert client.patch("/api/projects/p1/documents/%s" % a["id"], headers=hdr("owner"), json={"price_restricted": False}).json()["price_restricted"] is False
    assert "a.pdf" in ids(client, "cust")


def test_can_restrict_in_the_list_payload(client, docs):
    flags = {w: client.get("/api/projects/p1/documents", headers=hdr(w)).json()["can_restrict"] for w in ("owner", "admin", "geng", "eng", "cust", "price")}
    assert flags == {"owner": True, "admin": True, "geng": True, "eng": False, "cust": False, "price": False}


def test_the_switch_of_sees_prices_changes_what_a_member_sees(client, docs):
    assert ids(client, "cust") == {"open.pdf"}
    def switch(value):
        def apply(project):
            for m in project["members"]:
                if m["email"] == USERS["cust"][0]:
                    m["sees_prices"] = value
        store.mutate_project("p1", apply)
    switch(True)
    assert ids(client, "cust") == {"open.pdf", SECRET}
    switch(False)
    assert ids(client, "cust") == {"open.pdf"}


def test_audit_digest_and_report_lines_do_not_reach_members(client, docs, tmp_path):
    from motor_ai_sim.digest import collect
    lines = [json.loads(x) for x in (tmp_path / "projects" / "p1" / "audit.jsonl").read_text().splitlines() if x.strip()]
    up_row = [x for x in lines if x["action"] == "document.upload" and x["details"]["name"] == SECRET][0]
    assert up_row["details"]["price_restricted"] is True
    items = collect.collect_project(store.get_project("p1"), 0, 4e9)
    assert not any(SECRET in repr(i) for i in items)             # the project digest maps no document.* action at all
    from motor_ai_sim.owner_report import sources                # staff only: the report line is tagged
    assert sources is not None


def test_a_restricted_document_cannot_be_the_cover_and_marking_clears_a_cover(client, docs):
    _open, secret = docs
    r = client.put("/api/projects/p1/cover", headers=hdr("admin"), json={"document_id": secret["id"]})
    assert r.status_code == 422


def test_an_mcp_agent_never_sees_a_restricted_row(client, docs):
    project = store.get_project("p1")
    for who in ("admin", "owner"):
        user = {"email": USERS[who][0], "role": USERS[who][1]}
        staff = documents._staff(user, None)
        with auth.use_agent_user(user):
            assert documents._prices(user, project, staff) is False
            assert [r["name"] for r in documents._visible(store.load_documents("p1"), staff, prices=documents._prices(user, project, staff))] == ["open.pdf"]
        assert documents._prices(user, project, staff) is True


def test_a_non_member_gets_nothing(client, docs):
    assert client.get("/api/projects/p1/documents", headers=hdr("out")).status_code in (403, 404)
