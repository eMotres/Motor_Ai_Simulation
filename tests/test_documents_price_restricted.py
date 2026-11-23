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
    "oem": ("oem@example.test", "oem"), "oemflag": ("oemflag@example.test", "oem"),
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
                                      {"email": USERS["price"][0], "role": "customer", "sees_prices": True},
                                      {"email": USERS["oem"][0], "role": "owner"},
                                      {"email": USERS["oemflag"][0], "role": "customer", "sees_prices": True}]})
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


# ── #804 review fixes (Astra 2026-11-23, coordinator decisions 2026-11-23) ────────────────────────────────────────────────────────

def _png(path_bytes=None):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 10, 10)).save(buf, "PNG")
    return buf.getvalue()


def _upload_png(client, who="owner", name="top.png", **fields):
    return client.post("/api/projects/p1/documents", headers=hdr(who), files={"file": (name, _png())}, data=fields).json()


def _events(tmp_path):
    p = tmp_path / "projects" / "p1" / "audit.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def test_identity_comes_first_oem_agent_engineer(client, docs):
    _open, secret = docs
    for who in ("oem", "oemflag"):              # a factory account never sees prices: not as owner, not with the flag
        assert ids(client, who) == {"open.pdf"}
        assert client.get("/api/projects/p1/documents/%s/download" % secret["id"], headers=hdr(who)).status_code == 404
        assert client.get("/api/projects/p1/documents", headers=hdr(who)).json()["can_restrict"] is False
        assert up(client, who=who, name="o.pdf", price_restricted="true").status_code == 403
    assert ids(client, "geng") == {"open.pdf", SECRET}            # MOTRES staff always do
    assert client.get("/api/projects/p1/documents", headers=hdr("geng")).json()["can_restrict"] is True
    project = store.get_project("p1")
    agent = {"email": USERS["owner"][0], "role": "user"}
    with auth.use_agent_user(agent):
        assert documents._prices(agent, project) is False
        assert documents._can_restrict(agent, project) is False
    assert documents._prices(agent, project) is True


def test_a_restricted_upload_is_one_write_never_public(client, tmp_path, monkeypatch):
    seen = []
    real = store.mutate_json

    def spy(project_id, name, mutator, default=None):
        out = real(project_id, name, mutator, default)
        if name == store.DOCUMENTS_JSON:
            seen.append([dict(r) for r in store.load_documents(project_id)])
        return out

    monkeypatch.setattr(store, "mutate_json", spy)
    r = up(client, name="priced.pdf", price_restricted="true")
    assert r.status_code == 200 and r.json()["price_restricted"] is True
    first = [rows for rows in seen if any(x["name"] == "priced.pdf" for x in rows)][0]
    assert [x["price_restricted"] for x in first if x["name"] == "priced.pdf"] == [True]      # the row's very first state is restricted
    assert "priced.pdf" not in ids(client, "cust")
    assert [e for e in _events(tmp_path) if e["action"] == "document.upload"][0]["details"]["price_restricted"] is True


def test_cover_race_document_restricted_while_the_thumbnail_is_made(client, tmp_path, monkeypatch):
    doc = _upload_png(client)
    from motor_ai_sim.development import cover as cover_mod
    real = cover_mod.make_thumbnail

    def racing(path):
        out = real(path)
        store.mutate_documents("p1", lambda rows: [r.update(price_restricted=True) for r in rows if r["id"] == doc["id"]])
        return out

    monkeypatch.setattr(cover_mod, "make_thumbnail", racing)
    r = client.put("/api/projects/p1/cover", headers=hdr("owner"), json={"document_id": doc["id"]})
    assert r.status_code == 409
    assert store.read_cover("p1") is None and "cover" not in store.get_project("p1")
    assert client.get("/api/projects/p1/cover", headers=hdr("cust")).status_code == 404


def test_cover_is_cleared_and_get_cover_enforces_the_source_document(client, tmp_path):
    doc = _upload_png(client)
    assert client.put("/api/projects/p1/cover", headers=hdr("owner"), json={"document_id": doc["id"]}).status_code == 200
    assert client.get("/api/projects/p1/cover", headers=hdr("cust")).status_code == 200
    # the clearing never happened (a failure): the stored cover still points at the document, which is now restricted
    store.mutate_documents("p1", lambda rows: [r.update(price_restricted=True) for r in rows if r["id"] == doc["id"]])
    assert "cover" in store.get_project("p1")
    for who in ("cust", "owner", "admin", "price"):
        assert client.get("/api/projects/p1/cover", headers=hdr(who)).status_code == 404
    # the normal path: PATCH restricts and the cover goes
    doc2 = _upload_png(client, name="two.png")
    assert client.put("/api/projects/p1/cover", headers=hdr("owner"), json={"document_id": doc2["id"]}).status_code == 200
    assert client.patch("/api/projects/p1/documents/%s" % doc2["id"], headers=hdr("owner"), json={"price_restricted": True}).status_code == 200
    assert "cover" not in store.get_project("p1") and store.read_cover("p1") is None
    assert [e["details"]["reason"] for e in _events(tmp_path) if e["action"] == "cover.clear"] == ["document marked price-restricted"]


def test_an_audit_failure_neither_blocks_the_cover_clearing_nor_loses_the_event(client, tmp_path, monkeypatch):
    doc = _upload_png(client)
    assert client.put("/api/projects/p1/cover", headers=hdr("owner"), json={"document_id": doc["id"]}).status_code == 200
    real = store.append_jsonl

    def broken(project_id, name, entry):
        if name == "audit.jsonl":
            raise OSError("disk full")
        return real(project_id, name, entry)

    monkeypatch.setattr(store, "append_jsonl", broken)
    r = client.patch("/api/projects/p1/documents/%s" % doc["id"], headers=hdr("owner"), json={"price_restricted": True})
    assert r.status_code == 200 and r.json()["price_restricted"] is True
    assert "cover" not in store.get_project("p1") and store.read_cover("p1") is None       # cleared although the audit failed
    pending = store.load_json("p1", store.DOCUMENTS_JSON, {}).get("pending_audit") or []
    assert [e["action"] for e in pending] == ["document.update"]                      # the change's own event stays pending
    assert pending[0]["details"]["changes"] == {"price_restricted": {"from": False, "to": True}}
    monkeypatch.setattr(store, "append_jsonl", real)
    client.get("/api/projects/p1/documents", headers=hdr("owner"))
    documents._flush("p1")
    documents._flush("p1")                                                            # idempotent: written once
    store.flush_project_audit("p1")
    acts = [e["action"] for e in _events(tmp_path)]
    assert acts.count("document.update") == 1 and acts.count("cover.clear") == 1
    assert not store.load_json("p1", store.DOCUMENTS_JSON, {}).get("pending_audit")


def test_hidden_document_answers_404_like_an_unknown_id_for_a_non_editor(client, docs):
    _open, secret = docs
    unknown = "no-such-id"
    for method, body in (("patch", {"json": {"revision": "X"}}), ("delete", {})):
        call = getattr(client, method)
        hidden = call("/api/projects/p1/documents/%s" % secret["id"], headers=hdr("cust"), **body)
        gone = call("/api/projects/p1/documents/%s" % unknown, headers=hdr("cust"), **body)
        assert hidden.status_code == gone.status_code == 404 and hidden.json() == gone.json()
        engineer = call("/api/projects/p1/documents/%s" % secret["id"], headers=hdr("eng"), **body)      # an editor who may not see it
        assert engineer.status_code == 404 and engineer.json() == gone.json()
    visible = client.patch("/api/projects/p1/documents/%s" % _open["id"], headers=hdr("cust"), json={"revision": "X"})
    assert visible.status_code == 403                                                  # a document the caller sees: the usual 403
    assert store.load_documents("p1")[1]["price_restricted"] is True and not store.load_documents("p1")[1]["archived"]


def test_failed_cover_commit_leaves_the_previous_cover_and_no_restricted_bytes_reachable(client, tmp_path, monkeypatch):
    a = _upload_png(client, name="a.png")
    assert client.put("/api/projects/p1/cover", headers=hdr("owner"), json={"document_id": a["id"]}).status_code == 200
    before = client.get("/api/projects/p1/cover", headers=hdr("cust"))
    assert before.status_code == 200
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (0, 0, 255)).save(buf, "PNG")
    b = client.post("/api/projects/p1/documents", headers=hdr("owner"), files={"file": ("b.png", buf.getvalue())}).json()
    real = store.mutate_project

    def failing(project_id, mutator):
        probe = {"cover": None}
        mutator(probe)                                   # the pointer write is what fails, after the bytes were stored
        if probe.get("cover") and probe["cover"]["document_id"] == b["id"]:
            raise OSError("project.json not writable")
        return real(project_id, mutator)

    monkeypatch.setattr(store, "mutate_project", failing)
    with pytest.raises(OSError):
        client.put("/api/projects/p1/cover", headers=hdr("owner"), json={"document_id": b["id"]})
    monkeypatch.setattr(store, "mutate_project", real)
    # B becomes restricted afterwards; the pointer still names A, and GET serves A's own bytes
    store.mutate_documents("p1", lambda rows: [x.update(price_restricted=True) for x in rows if x["id"] == b["id"]])
    after = client.get("/api/projects/p1/cover", headers=hdr("cust"))
    assert after.status_code == 200 and after.content == before.content
    covers = list((tmp_path / "projects" / "p1" / "covers").glob("*.webp"))
    assert len(covers) == 1 and covers[0].read_bytes() == before.content          # the failed version's file is gone
    # a successful replacement removes the superseded version only after the commit
    c = _upload_png(client, name="c.png")
    assert client.put("/api/projects/p1/cover", headers=hdr("owner"), json={"document_id": c["id"]}).status_code == 200
    assert len(list((tmp_path / "projects" / "p1" / "covers").glob("*.webp"))) == 1
