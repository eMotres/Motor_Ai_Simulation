"""The 3-D model of a development project: /api/projects/{id}/model3d (upload, list, archive / restore, view, gated download)."""
from __future__ import annotations

import json
from pathlib import Path

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
FACTORY = "factory@example.test"
ROLES = {"t-owner": (OWNER, "user"), "t-customer": (CUSTOMER, "user"), "t-outsider": (OUTSIDER, "user"),
         "t-staff": (STAFF, "engineer"), "t-admin": (ADMIN, "admin"), "t-oem": (FACTORY, "oem")}
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
    """The actions of the complete records (a torn line stays in the file and is skipped, like every reader does)."""
    out = []
    for line in (tmp_path / "projects" / "proj1" / "audit.jsonl").read_bytes().decode("utf-8", errors="replace").split("\n"):
        try:
            out.append(json.loads(line)["action"])
        except ValueError:
            continue
    return out


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
    assert all(Path(m["path"]).parts[-3:] == ("model3d", "files", m["id"]) for m in staff)
    assert [m["id"] for m in PM.models_for_project("proj1", staff=False)] == [shown["id"]]


@pytest.mark.parametrize("membership", [None, "customer", "owner"])
def test_a_factory_account_never_sees_the_project_model(client, membership):
    row = upload(client, visible_to_customer="1").json()
    if membership:
        store.mutate_project("proj1", lambda p: p["members"].append({"email": FACTORY, "role": membership}))
    for path in (URL, f"{URL}/{row['id']}/model.glb", f"{URL}/{row['id']}/download"):
        assert client.get(path, headers=hdr("t-oem")).status_code == 404, path
    assert upload(client, who="t-oem").status_code == 404
    assert client.patch(f"{URL}/{row['id']}", headers=hdr("t-oem"), json={"archived": True}).status_code == 404


def test_a_failed_audit_append_keeps_the_change_with_its_event_and_is_written_once_later(client, tmp_path, monkeypatch):
    real = store.append_jsonl

    def broken(project_id, name, entry):
        if name == "audit.jsonl":
            raise OSError("disk full")
        return real(project_id, name, entry)

    monkeypatch.setattr(store, "append_jsonl", broken)
    r = upload(client)
    assert r.status_code == 200                                         # the change is committed together with its event
    doc = json.loads((tmp_path / "projects" / "proj1" / "model3d" / "models.json").read_text())
    assert [e["action"] for e in doc["pending_audit"]] == ["model3d.upload"]
    assert upload(client).status_code == 503                            # no NEW change while the audit cannot be written
    monkeypatch.setattr(store, "append_jsonl", real)
    client.get(URL, headers=hdr("t-staff"))
    client.get(URL, headers=hdr("t-staff"))
    assert audit_actions(tmp_path).count("model3d.upload") == 1         # replayed exactly once
    doc = json.loads((tmp_path / "projects" / "proj1" / "model3d" / "models.json").read_text())
    assert "pending_audit" not in doc and len(doc["models"]) == 1


def test_the_byte_cap_holds_without_content_length_and_one_file_only(client, tmp_path, monkeypatch):
    monkeypatch.setattr(PM, "MAX_BYTES", 1000)
    big = STEP + b"x" * 200_000
    boundary = "b0undary"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"revision\"\r\n\r\nA\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.step\"\r\n\r\n").encode() + big + f"\r\n--{boundary}--\r\n".encode()

    def chunked():
        for i in range(0, len(body), 8192):
            yield body[i:i + 8192]

    r = client.post(URL, headers={**hdr("t-staff"), "Content-Type": f"multipart/form-data; boundary={boundary}"}, content=chunked())
    assert r.status_code == 413
    two = client.post(URL, headers=hdr("t-staff"), data={"revision": "A"},
                      files=[("file", ("a.step", STEP)), ("other", ("b.step", STEP))])
    assert two.status_code == 400
    files = tmp_path / "projects" / "proj1" / "model3d" / "files"
    assert not files.exists() or list(files.iterdir()) == []


def _pending_upload(client, tmp_path, monkeypatch, tail: bytes):
    """One committed upload whose audit append was cut short: ``tail`` is what reached the file."""
    real = store.append_jsonl
    audit = tmp_path / "projects" / "proj1" / "audit.jsonl"

    def torn(project_id, name, entry):
        if name == "audit.jsonl":
            with audit.open("ab") as fh:
                fh.write(tail.replace(b"EVENT", json.dumps(entry["details"].get("event_id", "")).encode()))
            raise OSError("cut short")
        return real(project_id, name, entry)

    monkeypatch.setattr(store, "append_jsonl", torn)
    assert upload(client, note="Ротор ✓").status_code == 200
    monkeypatch.setattr(store, "append_jsonl", real)
    return audit


def test_a_torn_audit_line_holding_the_event_id_is_not_taken_as_written(client, tmp_path, monkeypatch):
    audit = _pending_upload(client, tmp_path, monkeypatch, b'{"action": "model3d.upload", "details": {"event_id": EVENT, "na')
    assert client.get(URL, headers=hdr("t-staff")).status_code == 200         # the replay repairs the tail and writes the row
    lines = audit.read_bytes().splitlines()
    assert lines[0].startswith(b'{"action": "model3d.upload", "details": {"event_id": ')   # the torn line stays, untouched
    assert [json.loads(x)["action"] for x in lines[1:]] == ["model3d.upload"]               # the complete record follows it
    assert not list(audit.parent.glob("audit.jsonl.torn-*"))
    doc = json.loads((tmp_path / "projects" / "proj1" / "model3d" / "models.json").read_text())
    assert "pending_audit" not in doc
    assert upload(client).status_code == 200 and audit_actions(tmp_path).count("model3d.upload") == 2


def test_a_cut_utf8_tail_never_blocks_the_store(client, tmp_path, monkeypatch):
    _pending_upload(client, tmp_path, monkeypatch, '{"note": "Рот'.encode("utf-8")[:-1])
    assert upload(client).status_code == 200                                    # repaired, replayed, then the new change
    assert audit_actions(tmp_path).count("model3d.upload") == 2



def test_a_complete_last_record_without_its_newline_counts_and_nothing_is_cut(client, tmp_path, monkeypatch):
    real = store.append_jsonl
    audit = tmp_path / "projects" / "proj1" / "audit.jsonl"

    def no_newline(project_id, name, entry):
        if name == "audit.jsonl":
            with audit.open("ab") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True).encode())
            raise OSError("cut before the newline")
        return real(project_id, name, entry)

    monkeypatch.setattr(store, "append_jsonl", no_newline)
    assert upload(client).status_code == 200
    monkeypatch.setattr(store, "append_jsonl", real)
    before = audit.read_bytes()
    assert client.get(URL, headers=hdr("t-staff")).status_code == 200
    after = audit.read_bytes()
    assert after.startswith(before) and after[len(before):] == b"\r\n"          # append-only: one newline, the record kept
    assert audit_actions(tmp_path).count("model3d.upload") == 1                 # not written twice



def test_a_name_with_a_line_separator_is_recognised_once_written(client, tmp_path, monkeypatch):
    """U+2028 / U+2029 / U+0085 in a file name sit inside a JSON string: the record is still one line, written exactly once."""
    real = store.append_jsonl
    fails = {"n": 2}

    def flaky(project_id, name, entry):
        if name == "audit.jsonl" and fails["n"] > 0:
            fails["n"] -= 1
            raise OSError("disk full")
        return real(project_id, name, entry)

    monkeypatch.setattr(store, "append_jsonl", flaky)
    assert upload(client, name="assembly\u2028rev\u2029B\u0085.step").status_code == 200   # committed; the audit append fails
    client.get(URL, headers=hdr("t-staff"))                                       # the replay fails once more
    client.get(URL, headers=hdr("t-staff"))                                       # now it is written
    client.get(URL, headers=hdr("t-staff"))                                       # and never again
    client.get(URL, headers=hdr("t-staff"))
    assert audit_actions(tmp_path).count("model3d.upload") == 1
    doc = json.loads((tmp_path / "projects" / "proj1" / "model3d" / "models.json").read_text())
    assert "pending_audit" not in doc
