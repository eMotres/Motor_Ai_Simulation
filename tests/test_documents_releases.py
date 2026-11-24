"""Releases of the Documents tab (owner 2026-11-24, after an assembler got every board revision in one archive): one revision group
is the CURRENT release; its ZIP holds exactly its visible documents; the other labelled groups are superseded - listed and readable,
but no ZIP for the customer side; the archive of every group is MOTRES staff's."""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim.development import documents, store
from motor_ai_sim.development.routes import router

USERS = {
    "owner": ("owner@example.test", "user"), "eng": ("eng@example.test", "user"), "cust": ("cust@example.test", "user"),
    "admin": ("admin@example.test", "admin"), "geng": ("geng@example.test", "engineer"), "out": ("out@example.test", "user"),
}


def hdr(who):
    return {"Authorization": "Bearer " + who}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("CAD_DOWNLOAD_LOG", str(tmp_path / "downloads.jsonl"))
    monkeypatch.setattr(auth, "resolve_user", lambda token: ({"email": USERS[token.split()[-1]][0], "role": USERS[token.split()[-1]][1]}
                                                              if token else None))
    store.create_project({"id": "p1", "title": "Board", "customer_org": "Org", "status": "spec", "created_at": "t", "updated_at": "t",
                          "owner_engineer": "owner@example.test", "linked_motors": [], "notes": "", "pending_invites": [],
                          "members": [{"email": USERS["owner"][0], "role": "owner"}, {"email": USERS["eng"][0], "role": "engineer"},
                                      {"email": USERS["cust"][0], "role": "customer"}]})
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def up(client, name, revision="", who="admin", at="2026-11-01T00:00:00", **fields):
    r = client.post("/api/projects/p1/documents", headers=hdr(who), files={"file": (name, b"data-" + name.encode())},
                    data={"revision": revision, **fields})
    assert r.status_code == 200, r.text
    doc = r.json()

    def stamp(rows):
        for row in rows:
            if row.get("id") == doc["id"]:
                row["uploaded_at"] = at
    store.mutate_documents("p1", stamp)                      # upload order made explicit (the clock is too fast for a test)
    return doc


@pytest.fixture
def board(client):
    """Revision A (October), B (November, newest), one unlabelled spec; B holds an internal and a price-restricted file."""
    return {
        "a_gerber": up(client, "gerber.zip", "A", at="2026-10-01T00:00:00"),
        "a_bom": up(client, "bom.csv", "A", at="2026-10-02T00:00:00"),
        "b_gerber": up(client, "gerber.zip", "B", at="2026-11-10T00:00:00"),
        "b_bom": up(client, "bom.csv", "B", at="2026-11-11T00:00:00"),
        "b_internal": up(client, "notes.step", "B", at="2026-11-12T00:00:00", internal="true"),
        "b_price": up(client, "quote.pdf", "B", at="2026-11-12T00:00:00", price_restricted="true"),
        "spec": up(client, "spec.pdf", "", at="2026-09-01T00:00:00"),
        "c_hidden": up(client, "draft.step", "C", at="2026-11-20T00:00:00", internal="true"),
    }


def listing(client, who):
    return client.get("/api/projects/p1/documents", headers=hdr(who)).json()


def zip_names(client, who, query):
    r = client.get("/api/projects/p1/documents.zip" + query, headers=hdr(who))
    assert r.status_code == 200, (r.status_code, r.text)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    return sorted(z.namelist()), z.read("MANIFEST.txt").decode(), r.headers["content-disposition"]


def test_default_current_is_the_newest_group_the_caller_sees(client, board):
    cust = listing(client, "cust")
    assert cust["current_release"] == "B" and cust["release_stored"] is False
    assert [(g["revision"], g["current"], g["superseded"], g["count"]) for g in cust["releases"]] == [
        ("B", True, False, 2), ("", False, False, 1), ("A", False, True, 2)]
    flags = {(d["revision"], d["name"]): d["superseded"] for d in cust["documents"]}
    assert flags == {("A", "gerber.zip"): True, ("A", "bom.csv"): True, ("B", "gerber.zip"): False, ("B", "bom.csv"): False,
                     ("", "spec.pdf"): False}
    # staff see the internal group C, which is newer: for staff it is the current one; the customer never sees C at all
    staff = listing(client, "admin")
    assert staff["current_release"] == "C"
    assert all(g["revision"] != "C" for g in cust["releases"])


def test_release_zip_is_exactly_the_visible_documents_of_the_group(client, board):
    names, manifest, disposition = zip_names(client, "cust", "?revision=B")
    assert sorted(n.rsplit("/", 1)[-1] for n in names) == ["MANIFEST.txt", "bom.csv", "gerber.zip"]   # the two visible files of B
    assert "Files included: 2" in manifest and "Release: B" in manifest
    for secret in ("notes.step", "quote.pdf", "spec.pdf", "draft.step"):
        assert secret not in manifest                        # no name, and the count is the visible count only
    assert "_B_documents.zip" in disposition
    # staff: the same group with the internal and price-restricted files
    names, manifest, _d = zip_names(client, "admin", "?revision=B")
    assert len(names) == 5 and "Files included: 4" in manifest


def test_a_label_of_hidden_documents_only_is_404_like_an_unknown_one(client, board):
    hidden = client.get("/api/projects/p1/documents.zip?revision=C", headers=hdr("cust"))
    unknown = client.get("/api/projects/p1/documents.zip?revision=Z", headers=hdr("cust"))
    assert hidden.status_code == unknown.status_code == 404 and hidden.json() == unknown.json()
    assert client.get("/api/projects/p1/documents.zip?revision=C", headers=hdr("admin")).status_code == 200


@pytest.mark.parametrize("who", ["owner", "eng", "cust"])
def test_customer_side_cannot_zip_a_superseded_release_or_every_group(client, board, who):
    assert client.get("/api/projects/p1/documents.zip?revision=A", headers=hdr(who)).status_code == 403
    assert client.get("/api/projects/p1/documents.zip", headers=hdr(who)).status_code == 403
    assert client.get("/api/projects/p1/documents.zip?revision=B", headers=hdr(who)).status_code == 200
    assert client.get("/api/projects/p1/documents.zip?revision=", headers=hdr(who)).status_code == 200      # general documents


@pytest.mark.parametrize("who", ["admin", "geng"])
def test_staff_zip_any_group_and_every_group(client, board, who):
    names, _m, _d = zip_names(client, who, "?revision=A")
    assert len(names) == 3
    names, manifest, _d = zip_names(client, who, "")
    assert "Release:" not in manifest and len(names) == 1 + 8   # every group, as before the releases


def test_outsider_and_anonymous(client, board):
    assert client.get("/api/projects/p1/documents.zip?revision=B", headers=hdr("out")).status_code == 404
    assert client.get("/api/projects/p1/documents.zip?revision=B").status_code == 401


def test_archived_documents_never_enter_a_release(client, board):
    client.delete("/api/projects/p1/documents/%s" % board["b_bom"]["id"], headers=hdr("owner"))
    for who in ("cust", "admin"):
        names, manifest, _d = zip_names(client, who, "?revision=B")
        assert "bom.csv" not in manifest
    assert [g["count"] for g in listing(client, "cust")["releases"] if g["revision"] == "B"] == [1]


def test_set_the_current_release(client, board, tmp_path):
    assert client.put("/api/projects/p1/documents/release", headers=hdr("cust"), json={"revision": "A"}).status_code == 403
    assert client.put("/api/projects/p1/documents/release", headers=hdr("owner"), json={"revision": "Z"}).status_code == 404
    assert client.put("/api/projects/p1/documents/release", headers=hdr("owner"), json={"revision": "C"}).status_code == 404   # hidden
    assert client.put("/api/projects/p1/documents/release", headers=hdr("owner"), json={"revision": ""}).status_code == 422
    r = client.put("/api/projects/p1/documents/release", headers=hdr("owner"), json={"revision": "A"})
    assert r.status_code == 200 and r.json()["current"] == "A"
    cust = listing(client, "cust")
    assert cust["current_release"] == "A" and cust["release_stored"] is True
    assert [g["revision"] for g in cust["releases"]] == ["A", "", "B"]
    assert client.get("/api/projects/p1/documents.zip?revision=A", headers=hdr("cust")).status_code == 200
    assert client.get("/api/projects/p1/documents.zip?revision=B", headers=hdr("cust")).status_code == 403
    assert (store.get_project("p1") or {})["documents_release"]["set_by"] == USERS["owner"][0]
    audit = [json.loads(x) for x in (tmp_path / "projects" / "p1" / "audit.jsonl").read_text().splitlines() if x.strip()]
    assert any(a["action"] == "documents.release" and a["details"]["revision"] == "A" for a in audit)
    # back to the derived default
    assert client.put("/api/projects/p1/documents/release", headers=hdr("eng"), json={"revision": None}).json()["current"] == "B"
    assert "documents_release" not in (store.get_project("p1") or {})


def test_a_stored_release_the_caller_cannot_see_falls_back_to_the_newest_visible(client, board):
    assert client.put("/api/projects/p1/documents/release", headers=hdr("admin"), json={"revision": "C"}).status_code == 200
    assert listing(client, "admin")["current_release"] == "C"
    assert listing(client, "cust")["current_release"] == "B"


def test_single_download_of_a_superseded_document(client, board, tmp_path, monkeypatch):
    doc = board["a_bom"]["id"]
    assert listing(client, "cust")["superseded_download"] is True
    assert client.get("/api/projects/p1/documents/%s/download" % doc, headers=hdr("cust")).status_code == 200
    audit = [json.loads(x) for x in (tmp_path / "projects" / "p1" / "audit.jsonl").read_text().splitlines() if x.strip()]
    assert [a["details"].get("superseded") for a in audit if a["action"] == "document.download"] == [True]
    monkeypatch.setattr(documents, "SUPERSEDED_SINGLE_DOWNLOAD", False)
    assert listing(client, "cust")["superseded_download"] is False
    assert client.get("/api/projects/p1/documents/%s/download" % doc, headers=hdr("cust")).status_code == 403
    assert client.get("/api/projects/p1/documents/%s/download" % board["b_bom"]["id"], headers=hdr("cust")).status_code == 200
    assert client.get("/api/projects/p1/documents/%s/download" % doc, headers=hdr("admin")).status_code == 200


def test_a_project_without_labels_has_one_current_release(client):
    up(client, "a.pdf")
    up(client, "b.pdf")
    cust = listing(client, "cust")
    assert cust["current_release"] == "" and [(g["revision"], g["superseded"]) for g in cust["releases"]] == [("", False)]
    names, _m, _d = zip_names(client, "cust", "?revision=")
    assert len(names) == 3


def test_a_label_whose_only_documents_are_archived_does_not_exist(client, board):
    """An archived document's label never makes a release exist: the ZIP's rows are the visible, non-archived ones."""
    old = up(client, "old.pdf", "R0", at="2026-08-01T00:00:00")
    client.delete("/api/projects/p1/documents/%s" % old["id"], headers=hdr("owner"))
    for who in ("cust", "owner", "admin"):
        r = client.get("/api/projects/p1/documents.zip?revision=R0", headers=hdr(who))
        assert r.status_code == 404 and r.json() == {"detail": "Release not found"}, who
    assert all(g["revision"] != "R0" for g in listing(client, "owner")["releases"])
    owner_view = client.get("/api/projects/p1/documents?archived=true", headers=hdr("owner")).json()
    assert all(d["name"] != "old.pdf" for d in owner_view["documents"])               # customer side: never listed (owner rule)
    staff_view = client.get("/api/projects/p1/documents?archived=true", headers=hdr("admin")).json()
    assert any(d["name"] == "old.pdf" for d in staff_view["documents"])               # staff list it in the archived view
    assert all(g["revision"] != "R0" for g in staff_view["releases"])
    assert client.put("/api/projects/p1/documents/release", headers=hdr("owner"), json={"revision": "R0"}).status_code == 404



@pytest.mark.parametrize("who", ["owner", "eng", "cust"])
def test_archived_documents_are_staff_only_whatever_the_project_role(client, board, who):
    """Owner 2026-11-24: never listed for, downloadable by or restorable by the customer side (before: a customer-side owner /
    engineer listed, downloaded and restored them)."""
    doc = board["a_bom"]["id"]
    assert client.delete("/api/projects/p1/documents/%s" % doc, headers=hdr("owner")).status_code == 200    # archiving stays theirs
    listed = client.get("/api/projects/p1/documents?archived=true", headers=hdr(who)).json()["documents"]
    assert all(d["id"] != doc for d in listed)
    assert client.get("/api/projects/p1/documents/%s/download" % doc, headers=hdr(who)).status_code == 404
    assert client.patch("/api/projects/p1/documents/%s" % doc, headers=hdr(who), json={"archived": False}).status_code == 404
    assert client.get("/api/projects/p1/documents/%s/download" % doc, headers=hdr("admin")).status_code == 200
    assert client.patch("/api/projects/p1/documents/%s" % doc, headers=hdr("admin"), json={"archived": False}).status_code == 200


def test_a_release_with_a_missing_file_is_refused_never_incomplete(client, board):
    import os
    row = [r for r in store.load_documents("p1") if r["id"] == board["b_gerber"]["id"]][0]
    os.remove(store.read_document_file("p1", row["file_id"]))
    for who in ("cust", "admin"):
        r = client.get("/api/projects/p1/documents.zip?revision=B", headers=hdr(who))
        assert r.status_code == 409 and "gerber.zip" in r.json()["detail"], who
    names, manifest, _d = zip_names(client, "admin", "")                    # the staff archive of everything: as before, noted
    assert "Not available on disk (1): gerber.zip" in manifest


def test_a_stored_release_that_loses_its_documents_is_cleared_and_editors_are_told(client, board, tmp_path):
    assert client.put("/api/projects/p1/documents/release", headers=hdr("owner"), json={"revision": "A"}).status_code == 200
    client.delete("/api/projects/p1/documents/%s" % board["a_gerber"]["id"], headers=hdr("owner"))
    assert (store.get_project("p1") or {})["documents_release"]["revision"] == "A"          # one A document is still live
    client.delete("/api/projects/p1/documents/%s" % board["a_bom"]["id"], headers=hdr("owner"))
    project = store.get_project("p1") or {}
    assert "documents_release" not in project and project["documents_release_cleared"]["revision"] == "A"
    assert listing(client, "owner")["release_cleared"] == "A" and listing(client, "cust")["release_cleared"] is None
    audit = [json.loads(x) for x in (tmp_path / "projects" / "p1" / "audit.jsonl").read_text().splitlines() if x.strip()]
    assert any(a["action"] == "documents.release_cleared" and a["details"]["revision"] == "A" for a in audit)
    # a restored A document does NOT re-activate the old choice
    client.patch("/api/projects/p1/documents/%s" % board["a_bom"]["id"], headers=hdr("admin"), json={"archived": False})
    assert listing(client, "cust")["current_release"] == "B"
    # a new choice answers the note
    client.put("/api/projects/p1/documents/release", headers=hdr("owner"), json={"revision": "B"})
    assert listing(client, "owner")["release_cleared"] is None


def test_relabelling_the_last_documents_of_the_stored_release_clears_it(client, board):
    client.put("/api/projects/p1/documents/release", headers=hdr("owner"), json={"revision": "A"})
    for key in ("a_gerber", "a_bom"):
        assert client.patch("/api/projects/p1/documents/%s" % board[key]["id"], headers=hdr("owner"), json={"revision": "A1"}).status_code == 200
    assert listing(client, "owner")["release_cleared"] == "A"
    assert "documents_release" not in (store.get_project("p1") or {})
