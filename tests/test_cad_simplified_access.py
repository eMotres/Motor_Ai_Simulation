# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The SIMPLIFIED model (owner 2026-10-15): an envelope-only copy of every motor that every SIGNED-IN user may download at once.

* ``simplified.step`` / ``.glb`` / ``.json``: no NDA, no applicant verification - only a signed-in account that may see the motor
  (anonymous 401, an ungranted or hidden motor the same 404 as the viewer, a motor whose simplified files are not built 404);
* the full STEP keeps its gate (403 without NDA);
* the STEP header says "SIMPLIFIED - envelope only" (+ the mass of the real model), the download id and the recipient;
* every simplified download is logged (user, file, version, sha256) in cad_downloads.jsonl, and a download whose log line cannot be
  written is refused;
* the list says whether the simplified model exists.
Throwaway registry and a fake CAD tree; no FreeCAD needed."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import model3d as M
from motor_ai_sim.api import app

ADMIN, USER, NDA_USER, OTHER = "admin@example.com", "client@example.com", "nda@example.com", "other@example.com"
DIE, CFG, MID = "CIANO14 40 new", "L12", "CIANO14_40_new_L12"
GLB, SGLB = b"glTF-FULL", b"glTF-SIMPLIFIED"
SJSON = json.dumps({"schema": "motres-cad-simplified/1", "motor_id": MID, "envelope": {"size": [1, 2, 3]},
                    "mass": {"total_g": 99.0, "scope": "full motor"}}).encode()
HEAD = ("ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('FreeCAD Model'),'2;1');\n"
        "FILE_NAME('x','2026-10-13T10:00:00',('A'),('O'),'OCC','FreeCAD','U');\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\nENDSEC;\n")
DATA = "DATA;\n#1=CARTESIAN_POINT('',(0.,0.,0.));\nENDSEC;\nEND-ISO-10303-21;\n"
STEP, SSTEP = (HEAD + DATA).encode("latin-1"), (HEAD + "DATA;\n#1=CARTESIAN_POINT('',(9.,9.,9.));\nENDSEC;\nEND-ISO-10303-21;\n").encode("latin-1")
client = TestClient(app)


def _build_tree(cad, with_simplified=True):
    d = cad / DIE / CFG
    d.mkdir(parents=True)
    files = {MID + "_full.glb": GLB, MID + "_active.step": STEP}
    if with_simplified:
        files |= {MID + "_simplified.step": SSTEP, MID + "_simplified.glb": SGLB, MID + "_simplified.json": SJSON}
    for n, b in files.items():
        (d / n).write_bytes(b)
    (d / "manifest.json").write_text(json.dumps({
        "motor_id": MID, "die": DIE, "config": CFG, "param_hash": "ab" * 32, "built_at": "2026-10-15T12:00:00+00:00",
        "template": {"version": "A.7.0"}, "cad_variant": "full",
        "files": {n: {"sha256": M.sha256_bytes(b), "bytes": len(b)} for n, b in files.items()}}))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    cad = tmp_path / "cad"
    _build_tree(cad)
    monkeypatch.setenv("MOTRES_CAD_ROOT", str(cad))
    monkeypatch.setenv("CAD_DOWNLOAD_LOG", str(tmp_path / "cad_downloads.jsonl"))
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(USER, "password-client", role="user", name="Ann Muller")
    U.create_user(NDA_USER, "password-nda", role="user", name="Bob")
    U.create_user(OTHER, "password-other", role="user", name="Other")
    for e in (USER, NDA_USER):
        U.set_motor_grants(e, all_motors=False, dies=[DIE])
    U.set_nda(NDA_USER, True, "2026-10-01", by=ADMIN)
    h = lambda e: {"Authorization": f"Bearer {U.issue_token(e)}"}                  # noqa: E731
    return dict(admin=h(ADMIN), user=h(USER), nda=h(NDA_USER), other=h(OTHER), log=tmp_path / "cad_downloads.jsonl",
                cad=cad, url=f"/api/model3d/{DIE}/{CFG}")


def _log(env):
    p = env["log"]
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_a_signed_in_user_without_nda_gets_the_simplified_model_but_not_the_full_step(env):
    assert client.get(env["url"] + "/model.step", headers=env["user"]).status_code == 403            # the full model keeps its gate
    r = client.get(env["url"] + "/simplified.step", headers=env["user"])
    assert r.status_code == 200 and r.content.endswith(b"#1=CARTESIAN_POINT('',(9.,9.,9.));\nENDSEC;\nEND-ISO-10303-21;\n")
    g = client.get(env["url"] + "/simplified.glb", headers=env["user"])
    assert g.status_code == 200 and g.content == SGLB
    j = client.get(env["url"] + "/simplified.json", headers=env["user"])
    assert j.status_code == 200 and j.json()["envelope"]["size"] == [1, 2, 3] and j.json()["download"]["id"].startswith("d")


def test_no_verification_is_needed(env, monkeypatch):
    from motor_ai_sim import verification as V
    monkeypatch.setattr(V, "access_ok", lambda *a, **k: False)                                        # nobody is verified
    assert client.get(env["url"] + "/model.step", headers=env["nda"]).status_code == 403
    assert client.get(env["url"] + "/simplified.step", headers=env["nda"]).status_code == 200
    assert client.get(env["url"] + "/simplified.step", headers=env["user"]).status_code == 200


def test_anonymous_ungranted_hidden_and_unbuilt_are_refused(env, monkeypatch, tmp_path):
    for k in ("step", "glb", "json"):
        assert client.get(env["url"] + "/simplified." + k).status_code == 401                          # anonymous
        assert client.get(env["url"] + "/simplified." + k, headers=env["other"]).status_code == 404     # not granted
        assert client.get(f"/api/model3d/{DIE}/NOSUCH/simplified." + k, headers=env["user"]).status_code == 404
    from motor_ai_sim import motor_access as MA
    monkeypatch.setattr(MA, "may_see_config", lambda access, die, cfg: cfg != CFG)                      # a hidden configuration
    assert client.get(env["url"] + "/simplified.step", headers=env["user"]).status_code == 404
    assert client.get(env["url"] + "/simplified.step", headers=env["admin"]).status_code == 404
    assert _log(env) == []
    monkeypatch.undo()


def test_a_motor_without_simplified_files_is_a_404_and_not_listed_as_available(tmp_path, monkeypatch, env):
    import shutil
    shutil.rmtree(env["cad"])
    _build_tree(env["cad"], with_simplified=False)
    assert client.get(env["url"] + "/simplified.step", headers=env["user"]).status_code == 404
    m = client.get("/api/model3d/models", headers=env["user"]).json()["models"][0]
    assert m["simplified_available"] is False and m["available"] is True


def test_the_list_says_the_simplified_model_exists(env):
    m = client.get("/api/model3d/models", headers=env["user"]).json()["models"][0]
    assert m["simplified_available"] is True and m["simplified_step_bytes"] == len(SSTEP) and m["simplified_glb_bytes"] == len(SGLB)
    assert m["assembly"] == "full"


def test_the_step_header_says_simplified_and_carries_id_recipient_and_mass(env, monkeypatch):
    from motor_ai_sim.modules import motor_mass as MM
    monkeypatch.setattr(MM, "mass_summary", lambda *a, **k: dict(full_kg=0.1234, scope="full motor", partial=False))
    r = client.get(env["url"] + "/simplified.step", headers=env["user"])
    head = r.content[:r.content.find(b"ENDSEC;")].decode("latin-1")
    assert "SIMPLIFIED - envelope only" in head and "CONFIDENTIAL" not in head
    st = M.stamp_of(r.content)
    assert st["download_id"].startswith("d") and "simplified" in st["filename"] and "Ann" in st["customer"]
    assert "Mass of the real model: 123.4 g (full motor)" in head and "no NDA required" in head
    assert r.headers["content-disposition"].startswith("attachment; filename=\"" + MID + "_simplified_")
    assert r.headers["cache-control"] == "no-store"


def test_every_simplified_download_is_logged_with_user_file_and_version(env):
    r = client.get(env["url"] + "/simplified.step", headers=env["user"])
    client.get(env["url"] + "/simplified.glb", headers=env["nda"])
    rows = _log(env)
    assert [x["kind"] for x in rows] == ["simplified_step", "simplified_glb"]
    a = rows[0]
    assert a["user"] == USER and a["file"].startswith(MID + "_simplified_") and a["nda"] is False and a["simplified"] is True
    assert a["sha256"] == M.sha256_bytes(r.content) and a["source_sha256"] == M.sha256_bytes(SSTEP)
    assert a["template"] == "A.7.0" and a["param_hash"] == "ab" * 32 and a["die"] == DIE and a["config"] == CFG
    assert M.read_log(10, kind="simplified_step")[0]["download_id"] == a["download_id"]
    assert [x["file"] for x in M.trace(a["download_id"])["matches"]] == [a["file"]]            # a leaked simplified file is traced like a full one
    admin = client.get("/api/model3d/downloads", headers=env["admin"]).json()["downloads"]
    assert {d["kind"] for d in admin} == {"simplified_step", "simplified_glb"}


def test_a_download_that_cannot_be_logged_is_refused(env, monkeypatch):
    def boom(rec):
        raise OSError("disk full")
    monkeypatch.setattr(M, "log_download", boom)
    assert client.get(env["url"] + "/simplified.step", headers=env["user"]).status_code == 503


def test_simplified_stamp_unit():
    import datetime
    s = M.build_simplified_stamp(download_id="d20261015-aaaaaaaaaaaa", motor_id="m", version="v", day=datetime.date(2026, 10, 15),
                                 recipient=dict(name="A", email="a@x", company=""), mass="1 g (full motor)")
    assert s["simplified"] and "SIMPLIFIED - envelope only" in s["notice"] and "no NDA required" in s["line"]
    out = M.stamp_step(STEP, filename="f.step", customer="A", day=datetime.date(2026, 10, 15), motor_id="m", version="v", stamp=s)
    assert out.endswith(DATA.encode("latin-1")) and b"SIMPLIFIED" in out and b"CONFIDENTIAL" not in out
