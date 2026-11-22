"""The 3-D model of a development project: /api/projects/{id}/model3d (upload, list, archive / restore, view, gated download)."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim import model3d as M
from motor_ai_sim.development import project_model3d as PM
from motor_ai_sim.development import store
from motor_ai_sim.production import step_glb

OWNER = "owner@example.test"
CUSTOMER = "customer@example.test"
OUTSIDER = "outsider@example.test"
STAFF = "engineer@example.test"
ADMIN = "admin@example.test"
ROLES = {"t-owner": (OWNER, "user"), "t-customer": (CUSTOMER, "user"), "t-outsider": (OUTSIDER, "user"),
         "t-staff": (STAFF, "engineer"), "t-admin": (ADMIN, "admin")}
STEP = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
URL = "/api/projects/proj1/model3d"


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
    gate = {"open": False}
    monkeypatch.setattr(M, "can_download", lambda ident, rec=None: bool(ident.get("is_admin")) or gate["open"])
    built = []
    monkeypatch.setattr(step_glb, "glb_for_step", lambda path, file_id, cache_dir, timeout=0: built.append(file_id) or b"glTF-fake")
    store.create_project({"id": "proj1", "title": "Project", "customer_org": "Org", "status": "design", "created_at": "t",
                          "updated_at": "t", "owner_engineer": OWNER, "linked_motors": [], "notes": "", "pending_invites": [],
                          "members": [{"email": OWNER, "role": "owner"}, {"email": CUSTOMER, "role": "customer"}]})
    app = FastAPI()
    app.include_router(PM.router)
    c = TestClient(app)
    c.gate = gate
    c.built = built
    return c


def upload(client, who="t-staff", name="assembly.step", data=STEP, **fields):
    fields.setdefault("revision", "A")
    return client.post(URL, headers=hdr(who), files={"file": (name, data)}, data=fields)


def audit_actions(tmp_path):
    lines = (tmp_path / "projects" / "proj1" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["action"] for line in lines if line.strip()]


def test_staff_upload_is_internal_by_default_and_hidden_from_the_customer_side(client, tmp_path):
    r = upload(client, note="first assembly")
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["revision"] == "A" and row["note"] == "first assembly" and row["visible_to_customer"] is False
    assert len(row["sha256"]) == 64 and row["size"] == len(STEP)
    staff = client.get(URL, headers=hdr("t-staff")).json()
    assert [m["id"] for m in staff["models"]] == [row["id"]] and staff["latest"] == row["id"] and staff["can_upload"] is True
    for who in ("t-owner", "t-customer"):
        seen = client.get(URL, headers=hdr(who)).json()
        assert seen["models"] == [] and seen["latest"] is None and seen["can_upload"] is False
        assert client.get(f"{URL}/{row['id']}/model.glb", headers=hdr(who)).status_code == 404
    assert "model3d.upload" in audit_actions(tmp_path)


def test_a_visible_model_is_viewed_by_the_customer_side_without_staff_fields(client):
    row = upload(client, visible_to_customer="1").json()
    seen = client.get(URL, headers=hdr("t-customer")).json()
    assert [m["id"] for m in seen["models"]] == [row["id"]]
    assert "visible_to_customer" not in seen["models"][0] and "archived" not in seen["models"][0]
    assert "@" not in seen["models"][0]["uploader"]
    v = client.get(f"{URL}/{row['id']}/model.glb", headers=hdr("t-customer"))
    assert v.status_code == 200 and v.content == b"glTF-fake" and v.headers["content-type"] == "model/gltf-binary"


def test_customer_download_needs_the_gate_and_viewing_never_gives_the_file(client, tmp_path):
    row = upload(client, visible_to_customer="1").json()
    assert client.get(URL, headers=hdr("t-customer")).json()["can_download"] is False
    denied = client.get(f"{URL}/{row['id']}/download", headers=hdr("t-customer"))
    assert denied.status_code == 403 and denied.json()["detail"]["code"] == "nda_required"
    client.get(f"{URL}/{row['id']}/model.glb", headers=hdr("t-customer"))
    kinds = [json.loads(line)["kind"] for line in (tmp_path / "downloads.jsonl").read_text().splitlines()]
    assert kinds == ["project_model3d_view"]                      # the view is traced, the refused download wrote nothing
    client.gate["open"] = True
    ok = client.get(f"{URL}/{row['id']}/download", headers=hdr("t-customer"))
    assert ok.status_code == 200 and ok.content == STEP
    assert json.loads((tmp_path / "downloads.jsonl").read_text().splitlines()[-1])["kind"] == "project_model3d_step"
    assert "model3d.download" in audit_actions(tmp_path)


def test_staff_download_does_not_need_the_customer_gate(client):
    row = upload(client).json()
    r = client.get(f"{URL}/{row['id']}/download", headers=hdr("t-staff"))
    assert r.status_code == 200 and r.content == STEP


def test_only_staff_upload_and_change(client):
    assert upload(client, who="t-owner").status_code == 403
    assert upload(client, who="t-customer").status_code == 403
    assert upload(client, who="t-outsider").status_code == 404
    assert client.post(URL, files={"file": ("a.step", STEP)}, data={"revision": "A"}).status_code == 401
    row = upload(client, who="t-admin").json()
    assert client.patch(f"{URL}/{row['id']}", headers=hdr("t-owner"), json={"archived": True}).status_code == 403
    assert client.get(URL, headers=hdr("t-outsider")).status_code == 404


def test_upload_refuses_what_is_not_a_step_and_leaves_no_bytes(client, tmp_path):
    assert upload(client, name="drawing.pdf").status_code == 422
    assert upload(client, name="fake.step", data=b"%PDF-1.7 not a step").status_code == 422
    assert upload(client, revision="").status_code == 422
    assert upload(client, data=b"").status_code == 400
    files = tmp_path / "projects" / "proj1" / "model3d" / "files"
    assert not files.exists() or list(files.iterdir()) == []
    assert client.get(URL, headers=hdr("t-staff")).json()["models"] == []


def test_archive_and_restore_keep_the_row_and_the_file_and_latest_follows(client, tmp_path):
    a = upload(client, revision="A").json()
    b = upload(client, revision="B", note="bearing seat moved").json()
    assert client.get(URL, headers=hdr("t-staff")).json()["latest"] == b["id"]
    r = client.patch(f"{URL}/{b['id']}", headers=hdr("t-staff"), json={"archived": True})
    assert r.status_code == 200 and r.json()["archived"] is True and r.json()["archived_at"]
    listed = client.get(URL, headers=hdr("t-staff")).json()
    assert [m["id"] for m in listed["models"]] == [a["id"]] and listed["latest"] == a["id"]
    history = client.get(URL + "?archived=1", headers=hdr("t-staff")).json()["models"]
    assert [m["revision"] for m in history] == ["B", "A"]                     # newest first, nothing deleted
    assert (tmp_path / "projects" / "proj1" / "model3d" / "files" / b["id"]).is_file()
    r = client.patch(f"{URL}/{b['id']}", headers=hdr("t-staff"), json={"archived": False})
    assert r.json()["archived"] is False and r.json()["archived_at"] is None
    assert client.get(URL, headers=hdr("t-staff")).json()["latest"] == b["id"]
    actions = audit_actions(tmp_path)
    assert actions.count("model3d.upload") == 2 and "model3d.archive" in actions and "model3d.restore" in actions


def test_an_archived_model_is_gone_for_the_customer_side_and_archived_rows_are_staff_only(client):
    row = upload(client, visible_to_customer="true").json()
    client.patch(f"{URL}/{row['id']}", headers=hdr("t-staff"), json={"archived": True})
    assert client.get(URL + "?archived=1", headers=hdr("t-customer")).json()["models"] == []
    assert client.get(f"{URL}/{row['id']}/model.glb", headers=hdr("t-customer")).status_code == 404


def test_visibility_is_switched_by_staff_and_audited(client, tmp_path):
    row = upload(client).json()
    r = client.patch(f"{URL}/{row['id']}", headers=hdr("t-staff"), json={"visible_to_customer": True})
    assert r.status_code == 200 and r.json()["visible_to_customer"] is True
    assert [m["id"] for m in client.get(URL, headers=hdr("t-owner")).json()["models"]] == [row["id"]]
    assert "model3d.visibility" in audit_actions(tmp_path)


@pytest.mark.parametrize("body", [{}, {"archived": "yes"}, {"name": "x"}, {"visible_to_customer": 1}])
def test_patch_refuses_anything_but_the_two_flags(client, body):
    row = upload(client).json()
    assert client.patch(f"{URL}/{row['id']}", headers=hdr("t-staff"), json=body).status_code == 422


def test_unknown_or_malformed_ids_are_404(client):
    assert client.get(f"{URL}/nope/model.glb", headers=hdr("t-staff")).status_code == 404
    assert client.get(f"{URL}/..%2F..%2Fproject.json/download", headers=hdr("t-staff")).status_code == 404
    assert client.patch(f"{URL}/nope", headers=hdr("t-staff"), json={"archived": True}).status_code == 404


def test_the_view_of_a_too_large_step_is_refused_but_download_works(client, monkeypatch):
    row = upload(client).json()
    monkeypatch.setattr(step_glb, "MAX_STEP_BYTES", 10)
    assert client.get(f"{URL}/{row['id']}/model.glb", headers=hdr("t-staff")).status_code == 413
    assert client.get(f"{URL}/{row['id']}/download", headers=hdr("t-staff")).status_code == 200


def test_the_production_seam_reads_the_same_storage(client):
    hidden = upload(client, revision="A").json()
    shown = upload(client, revision="B", visible_to_customer="1").json()
    staff = PM.models_for_project("proj1", staff=True)
    assert [m["id"] for m in staff] == [shown["id"], hidden["id"]]
    assert all(m["path"].endswith("/model3d/files/" + m["id"]) for m in staff)
    assert [m["id"] for m in PM.models_for_project("proj1", staff=False)] == [shown["id"]]
