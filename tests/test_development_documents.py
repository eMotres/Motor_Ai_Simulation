"""Documents of a development project: /api/projects/{id}/documents (upload, list, patch, archive, download, zip)."""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim.development import documents, models, store
from motor_ai_sim.development.routes import router

OWNER = "owner@example.test"
CUSTOMER = "customer@example.test"
OUTSIDER = "outsider@example.test"
ADMIN = "admin@example.test"
ROLES = {"t-owner": (OWNER, "user"), "t-customer": (CUSTOMER, "user"), "t-outsider": (OUTSIDER, "user"), "t-admin": (ADMIN, "admin")}


def hdr(who: str) -> dict:
    return {"Authorization": "Bearer " + who}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("CAD_DOWNLOAD_LOG", str(tmp_path / "downloads.jsonl"))

    def resolve(token):
        if not token:
            return None
        email, role = ROLES[token.split()[-1]]
        return {"email": email, "role": role}

    monkeypatch.setattr(auth, "resolve_user", resolve)
    store.create_project({"id": "ciano", "title": "Controller", "customer_org": "Org", "status": "spec", "created_at": "t",
                          "updated_at": "t", "owner_engineer": OWNER, "linked_motors": [], "notes": "", "pending_invites": [],
                          "members": [{"email": OWNER, "role": "owner"}, {"email": CUSTOMER, "role": "customer"}]})
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def upload(client, who="t-owner", name="board.zip", data=b"PK-data", **fields):
    return client.post("/api/projects/ciano/documents", headers=hdr(who), files={"file": (name, data)}, data=fields)


def audit_actions(tmp_path):
    lines = (tmp_path / "projects" / "ciano" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["action"] for line in lines if line.strip()]


def test_upload_streams_to_disk_and_lists(client, tmp_path):
    r = upload(client, name="CIANO-gerber.zip", data=b"x" * 5000, revision="B")
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["folder"] == "fabrication" and row["revision"] == "B" and row["size"] == 5000 and row["internal"] is False
    assert len(row["sha256"]) == 64
    stored = list((tmp_path / "projects" / "ciano" / "documents" / "files").iterdir())
    assert len(stored) == 1 and stored[0].stat().st_size == 5000
    listed = client.get("/api/projects/ciano/documents", headers=hdr("t-customer")).json()
    assert [d["id"] for d in listed["documents"]] == [row["id"]]
    assert listed["can_edit"] is False and listed["folders"] == list(models.DOCUMENT_FOLDERS)
    assert "document.upload" in audit_actions(tmp_path)


@pytest.mark.parametrize("name,folder", [("a.step", "3d"), ("bom.csv", "bom"), ("board.kicad_pro", "kicad"), ("kicad-project.zip", "kicad"),
                                        ("gerbers_v2.zip", "fabrication"), ("schematic.pdf", "docs"), ("photo.png", "docs")])
def test_folder_is_guessed_from_the_name(client, name, folder):
    assert upload(client, name=name).json()["folder"] == folder


def test_explicit_folder_wins_and_is_validated(client):
    assert upload(client, name="x.pdf", folder="kicad").json()["folder"] == "kicad"
    assert upload(client, name="x.pdf", folder="secrets").status_code == 422


def test_upload_needs_write_rights(client):
    assert upload(client, who="t-customer").status_code == 403
    assert upload(client, who="t-outsider").status_code == 404
    assert client.post("/api/projects/ciano/documents", files={"file": ("a.pdf", b"x")}).status_code == 401
    assert upload(client, who="t-admin").status_code == 200       # global admin: staff of every project


def test_upload_rejects_empty_and_oversize_and_leaves_nothing(client, tmp_path, monkeypatch):
    assert upload(client, data=b"").status_code == 400
    monkeypatch.setattr(models, "DOCUMENT_MAX_BYTES", 1000)
    assert upload(client, data=b"x" * 1001).status_code == 413
    files = tmp_path / "projects" / "ciano" / "documents" / "files"
    assert not files.exists() or list(files.iterdir()) == []
    assert client.get("/api/projects/ciano/documents", headers=hdr("t-owner")).json()["documents"] == []


def test_declared_oversize_body_is_refused_before_reading(client, monkeypatch):
    monkeypatch.setattr(models, "DOCUMENT_MAX_BYTES", 10)
    r = client.post("/api/projects/ciano/documents", headers={**hdr("t-owner"), "Content-Length": str(10 + 3 * 1024 * 1024)},
                    files={"file": ("a.pdf", b"x")})
    assert r.status_code in (413, 400)


def test_internal_documents_are_hidden_from_customers(client):
    public = upload(client, name="public.pdf").json()
    secret = upload(client, name="internal.step", internal="true").json()
    assert secret["internal"] is True
    staff = client.get("/api/projects/ciano/documents", headers=hdr("t-owner")).json()["documents"]
    assert {d["id"] for d in staff} == {public["id"], secret["id"]}
    cust = client.get("/api/projects/ciano/documents", headers=hdr("t-customer")).json()["documents"]
    assert [d["id"] for d in cust] == [public["id"]]
    assert client.get("/api/projects/ciano/documents/%s/download" % secret["id"], headers=hdr("t-customer")).status_code == 404
    assert client.get("/api/projects/ciano/documents/%s/download" % secret["id"], headers=hdr("t-owner")).status_code == 200


def test_patch_internal_folder_revision_with_audit(client, tmp_path):
    doc = upload(client, name="notes.pdf").json()
    url = "/api/projects/ciano/documents/%s" % doc["id"]
    r = client.patch(url, headers=hdr("t-owner"), json={"internal": True, "folder": "bom", "revision": "C"})
    assert r.status_code == 200 and (r.json()["internal"], r.json()["folder"], r.json()["revision"]) == (True, "bom", "C")
    assert client.get("/api/projects/ciano/documents", headers=hdr("t-customer")).json()["documents"] == []
    assert client.patch(url, headers=hdr("t-owner"), json={"folder": "nope"}).status_code == 422
    assert client.patch(url, headers=hdr("t-owner"), json={"internal": "yes"}).status_code == 422
    assert client.patch(url, headers=hdr("t-owner"), json={"revision": "r" * 41}).status_code == 422
    assert client.patch(url, headers=hdr("t-owner"), json={}).status_code == 422
    assert client.patch(url, headers=hdr("t-customer"), json={"internal": False}).status_code == 403
    assert client.patch("/api/projects/ciano/documents/doc-missing", headers=hdr("t-owner"), json={"internal": False}).status_code == 404
    assert "document.update" in audit_actions(tmp_path)


def test_delete_archives_keeps_file_and_audit_and_restore(client, tmp_path):
    doc = upload(client, name="old.pdf", data=b"keep me").json()
    url = "/api/projects/ciano/documents/%s" % doc["id"]
    assert client.delete(url, headers=hdr("t-customer")).status_code == 403
    r = client.delete(url, headers=hdr("t-owner"))
    assert r.status_code == 200 and r.json()["archived"] is True
    assert client.delete(url, headers=hdr("t-owner")).status_code == 200              # idempotent: one audit row
    files = list((tmp_path / "projects" / "ciano" / "documents" / "files").iterdir())
    assert len(files) == 1 and files[0].read_bytes() == b"keep me"
    assert client.get("/api/projects/ciano/documents", headers=hdr("t-owner")).json()["documents"] == []
    assert len(client.get("/api/projects/ciano/documents?archived=true", headers=hdr("t-owner")).json()["documents"]) == 1
    assert client.get("/api/projects/ciano/documents?archived=true", headers=hdr("t-customer")).json()["documents"] == []
    assert client.get(url + "/download", headers=hdr("t-customer")).status_code == 404
    assert client.get(url + "/download", headers=hdr("t-owner")).status_code == 200    # staff can still fetch it
    assert audit_actions(tmp_path).count("document.archive") == 1
    assert client.patch(url, headers=hdr("t-owner"), json={"archived": True}).status_code == 422
    assert client.patch(url, headers=hdr("t-owner"), json={"archived": False}).json()["archived"] is False
    assert len(client.get("/api/projects/ciano/documents", headers=hdr("t-customer")).json()["documents"]) == 1
    assert "document.restore" in audit_actions(tmp_path)


def test_download_is_members_only_and_traced(client, tmp_path):
    payload = bytes(range(256)) * 100
    doc = upload(client, name="Model v1.step", data=payload).json()
    url = "/api/projects/ciano/documents/%s/download" % doc["id"]
    assert client.get(url).status_code == 401
    assert client.get(url, headers=hdr("t-outsider")).status_code == 404
    r = client.get(url, headers=hdr("t-customer"))
    assert r.status_code == 200 and r.content == payload
    assert "attachment" in r.headers["content-disposition"] and r.headers["cache-control"] == "no-store"
    log = [json.loads(x) for x in (tmp_path / "downloads.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(log) == 1
    assert log[0]["kind"] == "project_document" and log[0]["project"] == "ciano" and log[0]["sha256"] == doc["sha256"]
    assert log[0]["email"] == CUSTOMER and log[0]["bytes"] == len(payload) and log[0]["file"] == "Model v1.step"
    assert "document.download" in audit_actions(tmp_path)


def test_download_refused_when_the_log_is_not_writable(client, monkeypatch):
    from motor_ai_sim import model3d
    doc = upload(client, name="a.pdf").json()
    monkeypatch.setattr(model3d, "log_download", lambda rec: (_ for _ in ()).throw(OSError("disk full")))
    assert client.get("/api/projects/ciano/documents/%s/download" % doc["id"], headers=hdr("t-owner")).status_code == 503


def test_zip_holds_only_visible_files_in_folders(client, tmp_path):
    a = upload(client, name="board.kicad_pro", data=b"kicad").json()
    upload(client, name="bom.csv", data=b"ref,qty\nR1,2\n", revision="A")
    upload(client, name="bom.csv", data=b"ref,qty\nR1,3\n")                              # same name twice
    upload(client, name="hidden.step", data=b"secret", internal="true")
    gone = upload(client, name="archived.pdf", data=b"old").json()
    client.delete("/api/projects/ciano/documents/%s" % gone["id"], headers=hdr("t-owner"))
    assert client.get("/api/projects/ciano/documents.zip").status_code == 401
    assert client.get("/api/projects/ciano/documents.zip", headers=hdr("t-outsider")).status_code == 404

    def names(who):
        r = client.get("/api/projects/ciano/documents.zip", headers=hdr(who))
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        z = zipfile.ZipFile(io.BytesIO(r.content))
        assert z.testzip() is None
        return z, sorted(z.namelist())

    z, listed = names("t-customer")
    assert listed == ["MANIFEST.txt", "bom/bom (2).csv", "bom/bom.csv", "kicad/board.kicad_pro"]
    assert z.read("kicad/board.kicad_pro") == b"kicad" and a["sha256"] in z.read("MANIFEST.txt").decode()
    assert "@" not in z.read("MANIFEST.txt").decode()
    _z, staff = names("t-owner")
    assert "3d/hidden.step" in staff and "docs/archived.pdf" not in staff
    log = [json.loads(x) for x in (tmp_path / "downloads.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["kind"] for row in log] == ["project_documents_zip", "project_documents_zip"]
    assert "documents.zip" in audit_actions(tmp_path)


def test_zip_over_the_limit_is_refused(client, monkeypatch):
    upload(client, name="a.pdf", data=b"x" * 50)
    monkeypatch.setattr(documents, "MAX_ZIP_BYTES", 10)
    assert client.get("/api/projects/ciano/documents.zip", headers=hdr("t-owner")).status_code == 413


def test_project_storage_cap(client, monkeypatch):
    monkeypatch.setattr(documents, "PROJECT_MAX_BYTES", 100)
    assert upload(client, data=b"x" * 100).status_code == 200
    assert upload(client, data=b"x").status_code == 413


def test_unknown_project_and_traversal_ids(client):
    assert client.get("/api/projects/nope/documents", headers=hdr("t-owner")).status_code == 404
    assert client.get("/api/projects/ciano/documents/doc-x/download", headers=hdr("t-owner")).status_code == 404
    assert store.read_document_file("ciano", "../project.json") is None


def test_owner_report_lists_document_changes():
    from motor_ai_sim.owner_report import sources
    text = lambda action, **det: sources._project_audit_text({"action": action, "entity": "document", "entity_id": "doc-1", "details": det})
    assert text("document.upload", name="a.step", folder="3d", revision="B", internal=True) == ("document uploaded: a.step [3d] rev B (internal)", "documents")
    assert text("document.archive", name="a.step", folder="3d")[0] == "document archived: a.step [3d]"
    assert text("document.update", name="a.step", changes={"folder": {}})[0] == "document updated: a.step (folder)"
    assert text("document.download", name="a.step")[1] == "documents"
