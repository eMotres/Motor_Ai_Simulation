# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The two VIEWER routes serve the compressed copies (owner 2026-10-20: the models took 8-16 s to load) without changing who sees what.

* ``model.glb`` (full-model gate) and ``simplified_view.glb`` (every signed-in user who may see the motor) send the
  ``*.meshopt.glb`` copy when the manifest lists it and the file is there, else the canonical GLB; an unlisted file on disk is never sent;
* strong ETag = the served file's sha256, ``Cache-Control: private, no-cache``: every view is revalidated, and the 304 comes only
  AFTER the same checks as the 200 (anonymous 401, ungranted 404, no NDA 403 - whatever If-None-Match says);
* the logged, stamped ``simplified.glb`` DOWNLOAD stays the canonical, uncompressed file.
Throwaway registry and a fake CAD tree; no FreeCAD needed."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import model3d as M
from motor_ai_sim.api import app
from motor_ai_sim.routes import model3d as R

ADMIN, USER, OTHER = "admin@example.com", "client@example.com", "other@example.com"
DIE, CFG, MID = "CIANO14 40 new", "L12", "CIANO14_40_new_L12"
GLB, SGLB = b"glTF-FULL-CANONICAL", b"glTF-SIMPLIFIED-CANONICAL"
VGLB, VSGLB = b"glTF-FULL-MESHOPT", b"glTF-SIMPLIFIED-MESHOPT"
HEAD = ("ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('FreeCAD Model'),'2;1');\n"
        "FILE_NAME('x','2026-10-13T10:00:00',('A'),('O'),'OCC','FreeCAD','U');\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\nENDSEC;\n")
STEP = (HEAD + "DATA;\n#1=CARTESIAN_POINT('',(0.,0.,0.));\nENDSEC;\nEND-ISO-10303-21;\n").encode("latin-1")
client = TestClient(app)


def _tree(cad, *, packed=True, list_packed=True):
    d = cad / DIE / CFG
    d.mkdir(parents=True, exist_ok=True)
    files = {MID + "_full.glb": GLB, MID + "_active.step": STEP, MID + "_simplified.step": STEP, MID + "_simplified.glb": SGLB,
             MID + "_simplified.json": b"{}"}
    if packed:
        files |= {MID + "_full.meshopt.glb": VGLB, MID + "_simplified.meshopt.glb": VSGLB}
    for n, b in files.items():
        (d / n).write_bytes(b)
    listed = {n: b for n, b in files.items() if list_packed or not n.endswith(".meshopt.glb")}
    (d / "manifest.json").write_text(json.dumps({
        "motor_id": MID, "die": DIE, "config": CFG, "param_hash": "ab" * 32, "built_at": "2026-10-20T08:00:00+00:00",
        "template": {"version": "A.8.0"}, "cad_variant": "full",
        "files": {n: {"sha256": M.sha256_bytes(b), "bytes": len(b)} for n, b in listed.items()}}))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    cad = tmp_path / "cad"
    _tree(cad)
    monkeypatch.setenv("MOTRES_CAD_ROOT", str(cad))
    monkeypatch.setenv("CAD_DOWNLOAD_LOG", str(tmp_path / "cad_downloads.jsonl"))
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(USER, "password-client", role="user", name="Ann")
    U.create_user(OTHER, "password-other", role="user", name="Other")
    U.set_motor_grants(USER, all_motors=False, dies=[DIE])
    h = lambda e: {"Authorization": f"Bearer {U.issue_token(e)}"}                  # noqa: E731
    return dict(admin=h(ADMIN), user=h(USER), other=h(OTHER), cad=cad, url=f"/api/model3d/{DIE}/{CFG}")


def _inm(sha, weak=False):
    return {"If-None-Match": ('W/"%s"' if weak else '"%s"') % sha}


def test_the_viewer_routes_send_the_compressed_copy_with_a_strong_etag(env):
    r = client.get(env["url"] + "/model.glb", headers=env["admin"])
    assert r.status_code == 200 and r.content == VGLB and r.headers["content-type"] == "model/gltf-binary"
    assert r.headers["etag"] == '"%s"' % M.sha256_bytes(VGLB) and r.headers["cache-control"] == "private, no-cache"
    s = client.get(env["url"] + "/simplified_view.glb", headers=env["user"])
    assert s.status_code == 200 and s.content == VSGLB and s.headers["etag"] == '"%s"' % M.sha256_bytes(VSGLB)
    assert s.headers["cache-control"] == "private, no-cache"


def test_a_current_copy_gets_a_304_after_the_checks(env):
    for weak in (False, True):                                                      # nginx's gzip makes the ETag weak
        r = client.get(env["url"] + "/model.glb", headers=env["admin"] | _inm(M.sha256_bytes(VGLB), weak))
        assert r.status_code == 304 and r.content == b"" and r.headers["etag"] == '"%s"' % M.sha256_bytes(VGLB)
        s = client.get(env["url"] + "/simplified_view.glb", headers=env["user"] | _inm(M.sha256_bytes(VSGLB), weak))
        assert s.status_code == 304 and s.content == b""
    stale = client.get(env["url"] + "/model.glb", headers=env["admin"] | _inm(M.sha256_bytes(GLB)))   # an older model: the new bytes
    assert stale.status_code == 200 and stale.content == VGLB


def test_no_304_without_access(env):
    full, simp = _inm(M.sha256_bytes(VGLB)), _inm(M.sha256_bytes(VSGLB))
    assert client.get(env["url"] + "/model.glb", headers=full).status_code == 401                         # anonymous
    assert client.get(env["url"] + "/model.glb", headers=env["user"] | full).status_code == 403            # no NDA: the full gate
    assert client.get(env["url"] + "/model.glb", headers=env["other"] | full).status_code == 404           # not granted
    assert client.get(env["url"] + "/simplified_view.glb", headers=simp).status_code == 401
    assert client.get(env["url"] + "/simplified_view.glb", headers=env["other"] | simp).status_code == 404
    assert client.get(env["url"] + "/model.glb", headers=env["user"] | {"If-None-Match": "*"}).status_code == 403


def test_without_a_listed_copy_the_canonical_glb_is_served(env):
    import shutil
    shutil.rmtree(env["cad"] / DIE)
    _tree(env["cad"], packed=True, list_packed=False)                               # on disk but not in the manifest: never sent
    assert client.get(env["url"] + "/model.glb", headers=env["admin"]).content == GLB
    assert client.get(env["url"] + "/simplified_view.glb", headers=env["user"]).content == SGLB
    shutil.rmtree(env["cad"] / DIE)
    _tree(env["cad"], packed=False)
    r = client.get(env["url"] + "/model.glb", headers=env["admin"])
    assert r.content == GLB and r.headers["etag"] == '"%s"' % M.sha256_bytes(GLB)
    (env["cad"] / DIE / CFG / (MID + "_full.glb")).unlink()                         # listed but gone: not built
    assert client.get(env["url"] + "/model.glb", headers=env["admin"]).status_code == 404


def test_the_logged_download_stays_the_canonical_glb(env):
    g = client.get(env["url"] + "/simplified.glb", headers=env["user"])
    assert g.status_code == 200 and g.content == SGLB


def test_etag_matching():
    sha = "ab" * 32
    assert R.etag_matches('"%s"' % sha, sha) and R.etag_matches('W/"%s"' % sha, sha) and R.etag_matches('"x", W/"%s"' % sha, sha)
    assert R.etag_matches("*", sha)
    assert not R.etag_matches('"%s"' % ("cd" * 32), sha) and not R.etag_matches(None, sha) and not R.etag_matches('"x"', None)


def test_the_web_nginx_gzips_the_viewer_glbs():
    from pathlib import Path
    conf = (Path(__file__).resolve().parents[1] / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    types = conf.split("gzip_types", 1)[1].split(";", 1)[0].split()
    assert "model/gltf-binary" in types and "gzip_proxied any;" in conf
