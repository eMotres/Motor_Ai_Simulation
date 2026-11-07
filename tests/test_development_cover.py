"""Project cover image: PUT/DELETE/GET /api/projects/{id}/cover, thumbnail, summary cache key, auto-clear."""
from __future__ import annotations

import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from motor_ai_sim import auth
from motor_ai_sim.development import cover as cover_module, store
from motor_ai_sim.development.routes import router

OWNER = "owner@example.test"
CUSTOMER = "customer@example.test"
OUTSIDER = "outsider@example.test"
ROLES = {"t-owner": (OWNER, "user"), "t-customer": (CUSTOMER, "user"), "t-outsider": (OUTSIDER, "user"), "t-admin": ("admin@example.test", "admin")}


def hdr(who: str) -> dict:
    return {"Authorization": "Bearer " + who}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("CAD_DOWNLOAD_LOG", str(tmp_path / "downloads.jsonl"))
    monkeypatch.setattr(auth, "resolve_user", lambda token: ({"email": ROLES[token.split()[-1]][0], "role": ROLES[token.split()[-1]][1]} if token else None))
    store.create_project({"id": "ciano", "title": "Controller", "customer_org": "Org", "status": "spec", "created_at": "t",
                          "updated_at": "t", "owner_engineer": OWNER, "linked_motors": [], "notes": "", "pending_invites": [],
                          "members": [{"email": OWNER, "role": "owner"}, {"email": CUSTOMER, "role": "customer"}]})
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def png(width=1600, height=1000, mode="RGB", fmt="PNG") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (width, height), (20, 120, 60) if mode == "RGB" else (20, 120, 60, 128)).save(buf, fmt)
    return buf.getvalue()


def upload(client, name, data, who="t-owner", **fields):
    r = client.post("/api/projects/ciano/documents", headers=hdr(who), files={"file": (name, data)}, data=fields)
    assert r.status_code == 200, r.text
    return r.json()


def set_cover(client, doc_id, who="t-owner"):
    return client.put("/api/projects/ciano/cover", headers=hdr(who), json={"document_id": doc_id})


def audit_actions(tmp_path):
    lines = (tmp_path / "projects" / "ciano" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["action"] for line in lines if line.strip()]


def summary(client, who="t-customer"):
    return next(p for p in client.get("/api/projects", headers=hdr(who)).json()["projects"] if p["id"] == "ciano")


def test_set_cover_makes_a_small_webp_thumbnail(client):
    assert summary(client)["cover_version"] is None
    assert client.get("/api/projects/ciano/cover", headers=hdr("t-owner")).status_code == 404
    doc = upload(client, "top-view.png", png(1600, 1000))
    r = set_cover(client, doc["id"])
    assert r.status_code == 200 and r.json()["document_id"] == doc["id"]
    got = client.get("/api/projects/ciano/cover", headers=hdr("t-customer"))
    assert got.status_code == 200 and got.headers["content-type"] == "image/webp" and "private" in got.headers["cache-control"]
    thumb = Image.open(io.BytesIO(got.content))
    assert thumb.format == "WEBP" and max(thumb.size) == cover_module.COVER_WIDTH and thumb.size == (480, 300)
    assert len(got.content) < 20_000
    assert summary(client)["cover_version"] == r.json()["cover_version"]
    assert "cover" not in summary(client)                                   # only the cache key reaches the list
    listed = client.get("/api/projects/ciano/documents", headers=hdr("t-owner")).json()
    assert [d["is_cover"] for d in listed["documents"]] == [True] and listed["cover_version"] == r.json()["cover_version"]


def test_a_small_image_is_not_enlarged_and_jpeg_and_webp_work(client):
    for name, data in (("a.jpg", png(100, 60, fmt="JPEG")), ("b.webp", png(100, 60, fmt="WEBP"))):
        assert set_cover(client, upload(client, name, data)["id"]).status_code == 200
        assert Image.open(io.BytesIO(client.get("/api/projects/ciano/cover", headers=hdr("t-owner")).content)).size == (100, 60)


def test_a_new_cover_changes_the_version(client):
    first = set_cover(client, upload(client, "a.png", png(300, 200))["id"]).json()["cover_version"]
    second = set_cover(client, upload(client, "b.png", png(300, 200))["id"]).json()["cover_version"]
    assert first != second and summary(client)["cover_version"] == second


def test_unset_removes_the_image_and_is_idempotent(client, tmp_path):
    set_cover(client, upload(client, "a.png", png(300, 200))["id"])
    assert client.delete("/api/projects/ciano/cover", headers=hdr("t-owner")).json() == {"cover_version": None}
    assert client.delete("/api/projects/ciano/cover", headers=hdr("t-owner")).status_code == 200
    assert client.get("/api/projects/ciano/cover", headers=hdr("t-owner")).status_code == 404
    assert not (tmp_path / "projects" / "ciano" / "cover.webp").exists()
    assert summary(client)["cover_version"] is None
    assert audit_actions(tmp_path).count("cover.clear") == 1 and "cover.set" in audit_actions(tmp_path)


def test_only_write_members_set_it_and_only_members_read_it(client):
    doc = upload(client, "a.png", png(300, 200))
    assert set_cover(client, doc["id"], "t-customer").status_code == 403
    assert set_cover(client, doc["id"], "t-outsider").status_code == 404
    assert client.delete("/api/projects/ciano/cover", headers=hdr("t-customer")).status_code == 403
    assert client.get("/api/projects/ciano/cover").status_code == 401
    set_cover(client, doc["id"])
    assert client.get("/api/projects/ciano/cover", headers=hdr("t-outsider")).status_code == 404


def test_only_a_readable_public_image_can_be_the_cover(client):
    assert set_cover(client, upload(client, "notes.pdf", b"%PDF-1.4 not an image")["id"]).status_code == 422
    assert set_cover(client, upload(client, "fake.png", b"this is text")["id"]).status_code == 422
    assert set_cover(client, upload(client, "secret.png", png(100, 100), who="t-admin", internal="true")["id"], who="t-admin").status_code == 422
    assert set_cover(client, "doc-missing").status_code == 404
    assert client.put("/api/projects/ciano/cover", headers=hdr("t-owner"), json={}).status_code == 422
    assert summary(client)["cover_version"] is None


def test_oversize_pixel_count_is_refused(client, monkeypatch):
    monkeypatch.setattr(cover_module, "MAX_PIXELS", 1000)
    assert set_cover(client, upload(client, "big.png", png(100, 100))["id"]).status_code == 422


def test_archiving_or_hiding_the_cover_document_clears_the_cover(client, tmp_path):
    doc = upload(client, "a.png", png(300, 200))
    set_cover(client, doc["id"])
    client.patch("/api/projects/ciano/documents/%s" % doc["id"], headers=hdr("t-admin"), json={"internal": True})
    assert summary(client)["cover_version"] is None
    other = upload(client, "b.png", png(300, 200))
    set_cover(client, other["id"])
    assert client.delete("/api/projects/ciano/documents/%s" % other["id"], headers=hdr("t-owner")).status_code == 200
    assert summary(client)["cover_version"] is None
    assert client.get("/api/projects/ciano/cover", headers=hdr("t-owner")).status_code == 404
    assert audit_actions(tmp_path).count("cover.clear") == 2


def test_owner_report_lists_cover_changes():
    from motor_ai_sim.owner_report import sources
    text = lambda action, **det: sources._project_audit_text({"action": action, "entity": "project", "entity_id": "ciano", "details": det})
    assert text("cover.set", name="top.png")[0] == "project cover image set (top.png)"
    assert text("cover.clear")[0] == "project cover image removed"
