"""The standard contract documents of every project (owner 2026-11-22: «универсальные договора должны появиться во всех проектах»):
``development.contract_templates`` + the template part of ``/api/projects/{id}/contract``."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim.development import contract_templates as CT
from motor_ai_sim.development import project_contract as PC
from motor_ai_sim.development import store
from test_project_contract import PDF, _docx, _pdf

OWNER = "owner@example.test"
MEMBER = "member@example.test"
OUTSIDER = "outsider@example.test"
STAFF = "engineer@example.test"
ADMIN = "admin@example.test"
FACTORY = "factory@example.test"
ROLES = {"t-owner": (OWNER, "user"), "t-member": (MEMBER, "user"), "t-outsider": (OUTSIDER, "user"), "t-staff": (STAFF, "engineer"),
         "t-admin": (ADMIN, "admin"), "t-oem": (FACTORY, "oem")}


def hdr(who: str) -> dict:
    return {"Authorization": "Bearer " + who}


def _project(pid: str) -> None:
    store.create_project({"id": pid, "title": pid, "customer_org": "Org", "status": "simulation", "created_at": "t", "updated_at": "t",
                          "owner_engineer": OWNER, "linked_motors": [], "notes": "", "pending_invites": [],
                          "members": [{"email": OWNER, "role": "owner"}, {"email": MEMBER, "role": "customer"},
                                      {"email": FACTORY, "role": "customer"}]})


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("CAD_DOWNLOAD_LOG", str(tmp_path / "downloads.jsonl"))
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    monkeypatch.setenv("MOTRES_CONTRACT_TEMPLATES_ROOT", str(tmp_path / "private" / "templates"))
    monkeypatch.delenv("MOTOR_AI_SIM_PRIVATE_DATA", raising=False)

    def resolve(token):
        if not token:
            return None
        email, role = ROLES[token.split()[-1]]
        return {"email": email, "role": role}

    monkeypatch.setattr(auth, "resolve_user", resolve)
    _project("proj1")
    _project("proj2")
    app = FastAPI()
    app.include_router(PC.router)
    app.include_router(CT.router)
    return TestClient(app)


def put_template(client, version="v0.5.1", kind="agreement", who="t-admin", name="customer_contract_TEMPLATE.pdf", data=PDF):
    return client.post("/api/contract-templates", headers=hdr(who), files={"file": (name, data)}, data={"kind": kind, "version": version})


def view(client, pid="proj1", who="t-owner"):
    return client.get(f"/api/projects/{pid}/contract", headers=hdr(who)).json()


def seen(client, pid="proj1") -> dict:
    body = view(client, pid, "t-staff")
    return {"revision": body["revision"], "terms_digest": body["terms_digest"]}


def test_one_upload_shows_the_standard_agreement_in_every_project(client):
    assert view(client)["templates"]["agreement"] is None and view(client)["template_only"] is False   # no template: nothing to show
    r = put_template(client)
    assert r.status_code == 200, r.text
    for pid in ("proj1", "proj2"):
        tpl = view(client, pid)["templates"]["agreement"]
        assert tpl["version"] == "v0.5.1" and len(tpl["sha256"]) == 64
    assert put_template(client, version="v0.6", data=_pdf("changed")).status_code == 200
    assert view(client, "proj2")["templates"]["agreement"]["version"] == "v0.6"
    listed = client.get("/api/contract-templates", headers=hdr("t-admin")).json()
    assert listed["configured"] is True and [v["version"] for v in listed["versions"]["agreement"]] == ["v0.6", "v0.5.1"]


def test_only_admins_manage_templates_and_every_version_is_kept(client):
    for who in ("t-staff", "t-owner", "t-oem"):
        assert put_template(client, who=who).status_code == 403
        assert client.get("/api/contract-templates", headers=hdr(who)).status_code == 403
    assert put_template(client).status_code == 200
    assert put_template(client).status_code == 409                                  # a version label is used once per kind
    assert put_template(client, version="v1", name="a.pdf", data=b"<html>not a pdf</html>").status_code == 422
    assert put_template(client, version="v1", kind="annex_z").status_code == 422
    assert put_template(client, version="v1", name="a.exe").status_code == 422


def test_without_any_store_there_are_no_templates_and_upload_says_why(client, monkeypatch):
    monkeypatch.setattr(CT, "root", lambda: None)
    assert view(client)["templates"] == {"agreement": None, "annex_a": None, "annex_b": None, "unavailable": False}
    assert client.get("/api/contract-templates", headers=hdr("t-admin")).json()["configured"] is False
    assert put_template(client).status_code == 503


def test_a_project_without_its_own_agreement_sends_the_template_by_its_version(client, tmp_path):
    put_template(client)
    r = client.post("/api/projects/proj1/contract/status", headers=hdr("t-staff"), json={"status": "sent", **seen(client)})
    assert r.status_code == 200, r.text
    sent = r.json()["history"][-1]["files"]
    assert sent[0]["template"] is True and sent[0]["version"] == "v0.5.1" and len(sent[0]["sha256"]) == 64
    put_template(client, version="v0.6", data=_pdf("newer"))                  # a later template never changes what was sent
    body = view(client)
    assert body["sent_files"][0]["version"] == "v0.5.1" and body["templates"]["agreement"]["version"] == "v0.6"
    old = client.get(f"/api/projects/proj1/contract/template/agreement/download?version=v0.5.1&sha256={sent[0]['sha256']}", headers=hdr("t-owner"))
    assert old.status_code == 200 and old.content == PDF
    log = [json.loads(x) for x in (tmp_path / "downloads.jsonl").read_text().splitlines()]
    assert log[-1]["kind"] == "project_contract_template"
    assert client.get("/api/projects/proj1/contract/template/agreement/download?version=v9", headers=hdr("t-owner")).status_code == 404


def test_the_template_has_the_section_visibility(client, monkeypatch):
    from motor_ai_sim.development import access
    monkeypatch.delattr(access, "sees_prices", raising=False)
    put_template(client)
    url = "/api/projects/proj1/contract/template/agreement/download"
    assert client.get(url, headers=hdr("t-owner")).status_code == 200
    for who in ("t-member", "t-outsider", "t-oem"):
        assert client.get(url, headers=hdr(who)).status_code == 404


def test_an_own_agreement_ends_template_only_and_is_what_is_sent(client):
    put_template(client)
    up = client.post("/api/projects/proj1/contract/files", headers=hdr("t-staff"), files={"file": ("own.pdf", PDF)},
                     data={"kind": "agreement", **{k: str(v) for k, v in seen(client).items()}})
    assert up.status_code == 200 and up.json()["template_only"] is False
    r = client.post("/api/projects/proj1/contract/status", headers=hdr("t-staff"), json={"status": "sent", **seen(client)})
    assert [f.get("template", False) for f in r.json()["history"][-1]["files"]] == [False]
    assert view(client, "proj2")["template_only"] is True                            # the other project still has the template only



def test_a_template_change_after_the_dialog_opened_is_caught(client):
    put_template(client)
    at = seen(client)                                                       # the page shows template v0.5.1
    put_template(client, version="v0.6", data=_pdf("newer"))
    r = client.post("/api/projects/proj1/contract/status", headers=hdr("t-staff"), json={"status": "sent", **at})
    assert r.status_code == 409
    assert view(client)["status"] == "draft"


def test_a_docx_template_is_accepted(client):
    assert put_template(client, name="customer_contract_TEMPLATE.docx", data=_docx()).status_code == 200


def _blob_dir(tmp_path):
    return tmp_path / "private" / "templates" / "files"


def test_files_are_immutable_blobs_named_by_their_hash(client, tmp_path):
    a = put_template(client).json()
    b = put_template(client, version="v0.5.2").json()                           # the same bytes under a new label
    assert [p.name for p in _blob_dir(tmp_path).iterdir()] == [a["sha256"] + ".pdf"] and a["sha256"] == b["sha256"]


def test_a_sent_version_is_served_from_its_frozen_blob_and_a_changed_file_is_refused(client, tmp_path):
    put_template(client)
    client.post("/api/projects/proj1/contract/status", headers=hdr("t-staff"), json={"status": "sent", **seen(client)})
    sent = view(client)["sent_files"][0]
    assert sent["blob"] == sent["sha256"] + ".pdf"
    blob = _blob_dir(tmp_path) / sent["blob"]
    blob.write_bytes(_pdf("tampered"))                                         # changed in place
    url = f"/api/projects/proj1/contract/template/agreement/download?version=v0.5.1&sha256={sent['sha256']}"
    assert client.get(url, headers=hdr("t-owner")).status_code == 409
    blob.unlink()                                                              # gone
    assert client.get(url, headers=hdr("t-owner")).status_code == 409


def test_send_refuses_a_template_whose_file_is_missing(client, tmp_path):
    put_template(client)
    for p in _blob_dir(tmp_path).iterdir():
        p.unlink()
    r = client.post("/api/projects/proj1/contract/status", headers=hdr("t-staff"), json={"status": "sent", **seen(client)})
    assert r.status_code == 409 and view(client)["status"] == "draft"


@pytest.mark.parametrize("damage", ["corrupt", "shape", "missing"])
def test_a_damaged_index_fails_closed_and_a_label_is_never_reused(client, tmp_path, damage):
    put_template(client)
    index = tmp_path / "private" / "templates" / "index.json"
    if damage == "corrupt":
        index.write_text("{ not json")
    elif damage == "shape":
        index.write_text(json.dumps({"versions": []}))
    else:
        index.unlink()
    assert put_template(client).status_code == 503                               # v0.5.1 cannot be re-uploaded
    assert client.get("/api/contract-templates", headers=hdr("t-admin")).status_code == 503
    body = view(client)
    assert body["templates"]["agreement"] is None and body["templates"]["unavailable"] is True and body["template_only"] is False
    r = client.post("/api/projects/proj1/contract/status", headers=hdr("t-staff"), json={"status": "sent", **seen(client)})
    assert r.status_code == 409                                                # nothing to send


def test_send_freezes_exactly_the_reference_set_it_checked(client, monkeypatch):
    put_template(client)
    at = seen(client)
    calls = []
    real = CT.current

    def publishing(kind):                                                      # a publication lands right after the first read
        row = real(kind)
        calls.append(row["version"] if row else None)
        if len(calls) == 1:                                                    # written straight into the index (no HTTP)
            index = CT._index_path(CT.root())
            doc = json.loads(index.read_text())
            doc["versions"]["agreement"].append({**doc["versions"]["agreement"][0], "version": "v0.6",
                                                 "uploaded_at": "2999-01-01T00:00:00+00:00"})
            index.write_text(json.dumps(doc))
        return row

    monkeypatch.setattr(CT, "current", publishing)
    r = client.post("/api/projects/proj1/contract/status", headers=hdr("t-staff"), json={"status": "sent", **at})
    monkeypatch.setattr(CT, "current", real)
    if r.status_code == 200:
        assert r.json()["sent_files"][0]["version"] == "v0.5.1"                 # what was checked is what was frozen
    else:
        assert r.status_code == 409


def test_the_section_downloads_the_version_it_shows(client):
    put_template(client)
    shown = view(client)["templates"]["agreement"]["version"]
    put_template(client, version="v0.6", data=_pdf("newer"))
    r = client.get(f"/api/projects/proj1/contract/template/agreement/download?version={shown}", headers=hdr("t-owner"))
    assert r.status_code == 200 and r.content == PDF



def test_the_default_store_is_the_shared_root(monkeypatch, tmp_path):
    monkeypatch.delenv("MOTRES_CONTRACT_TEMPLATES_ROOT", raising=False)
    monkeypatch.setenv("SHARED_ROOT", str(tmp_path / "srv" / "shared"))
    from motor_ai_sim import workspace as W
    monkeypatch.setattr(W, "shared_root", lambda: tmp_path / "srv" / "shared")
    assert CT.root() == tmp_path / "srv" / "shared" / "contract" / "templates"



def test_a_failed_index_write_changes_nothing(client, tmp_path, monkeypatch):
    put_template(client)
    index = tmp_path / "private" / "templates" / "index.json"
    before = index.read_bytes()
    import os
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("rename failed")) if str(b).endswith("index.json") else real(a, b))
    assert put_template(client, version="v0.6", data=_pdf("newer")).status_code == 503
    assert index.read_bytes() == before and not list(index.parent.glob(".*.tmp"))


def test_a_failed_first_upload_leaves_no_blob_and_no_damaged_store(client, tmp_path, monkeypatch):
    import os
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("rename failed")) if str(b).endswith("index.json") else real(a, b))
    assert put_template(client).status_code == 503
    monkeypatch.setattr(os, "replace", real)
    assert not [p for p in _blob_dir(tmp_path).iterdir() if not p.name.startswith(".")]
    assert put_template(client).status_code == 200                              # failed before anything was published: label free



def test_index_validation_is_strict(client, tmp_path):
    put_template(client)
    index = tmp_path / "private" / "templates" / "index.json"
    good = json.loads(index.read_text())
    for bad in ({"versions": {}},                                                     # blobs present, none listed
                {"versions": {"agreement": [{**good["versions"]["agreement"][0], "kind": "annex_a"}]}},
                {"versions": {"agreement": [{**good["versions"]["agreement"][0], "version": "  "}]}}):
        index.write_text(json.dumps(bad))
        assert client.get("/api/contract-templates", headers=hdr("t-admin")).status_code == 503


def test_a_damaged_store_is_repaired_from_the_publication_log(client, tmp_path):
    put_template(client)
    put_template(client, version="v0.6", data=_pdf("newer"))
    index = tmp_path / "private" / "templates" / "index.json"
    index.write_text("{ broken")
    assert put_template(client, version="v0.7", data=_pdf("v7")).status_code == 503
    assert client.post("/api/contract-templates/repair", headers=hdr("t-staff")).status_code == 403
    r = client.post("/api/contract-templates/repair", headers=hdr("t-admin"))
    assert r.status_code == 200 and r.json()["kept"] == 2
    assert [v["version"] for v in client.get("/api/contract-templates", headers=hdr("t-admin")).json()["versions"]["agreement"]] == ["v0.6", "v0.5.1"]
    assert list((tmp_path / "private" / "templates").glob("index.damaged-*.json"))
    assert put_template(client, version="v0.6", data=_pdf("again")).status_code == 409   # a label is never reused


def test_history_downloads_are_bound_to_the_frozen_identity(client, tmp_path):
    put_template(client)
    client.post("/api/projects/proj1/contract/status", headers=hdr("t-staff"), json={"status": "sent", **seen(client)})
    first = view(client)["sent_files"][0]
    r = client.get(f"/api/projects/proj1/contract/template/agreement/download?version=v0.5.1&sha256={first['sha256']}", headers=hdr("t-owner"))
    assert r.status_code == 200 and r.content == PDF
    other = "0" * 64
    assert client.get(f"/api/projects/proj1/contract/template/agreement/download?version=v0.5.1&sha256={other}",
                      headers=hdr("t-owner")).status_code == 404                     # an identity this project was never sent


def test_the_page_digest_and_its_templates_come_from_one_read(client, monkeypatch):
    put_template(client)
    calls = []
    real = CT.load
    monkeypatch.setattr(CT, "load", lambda: calls.append(1) or real())
    view(client)
    assert len(calls) == 1


def test_the_shared_store_is_used_only_when_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("MOTRES_CONTRACT_TEMPLATES_ROOT", raising=False)
    monkeypatch.delenv("SHARED_ROOT", raising=False)
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    private = tmp_path / "priv"
    private.mkdir()
    monkeypatch.setenv("MOTOR_AI_SIM_PRIVATE_DATA", str(private))
    assert CT.root() == private / "config" / "contract" / "templates"           # development: the private checkout
    monkeypatch.setenv("SHARED_ROOT", str(tmp_path / "srv" / "shared"))
    from motor_ai_sim import workspace as W
    monkeypatch.setattr(W, "shared_root", lambda: tmp_path / "srv" / "shared")
    assert CT.root() == tmp_path / "srv" / "shared" / "contract" / "templates"



def test_a_label_recorded_by_a_failed_publication_is_never_reused(client, tmp_path, monkeypatch):
    put_template(client)                                                        # the index exists
    import os
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("rename failed")) if str(b).endswith("index.json") else real(a, b))
    assert put_template(client, version="v0.6", data=_pdf("newer")).status_code == 503
    monkeypatch.setattr(os, "replace", real)
    assert put_template(client, version="v0.6", data=_pdf("other")).status_code == 409   # the label was used
    assert client.get("/api/contract-templates", headers=hdr("t-admin")).status_code == 200    # and the store is healthy
