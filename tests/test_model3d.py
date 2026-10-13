"""3D model menu (routes/model3d.py, model3d.py): who gets which model, the STEP stamp, the download log, the NDA flag.

* a signed-in account without NDA sees and downloads the simplified ENVELOPE only, whatever the URL asks for;
* an account flagged "NDA signed" (Admin) and the owner/admin get the FULL model and the full STEP;
* an ungranted motor is a 404, anonymous is a 401;
* every STEP download is stamped (customer name + date in the header and the file name) and logged, and a download whose
  log line cannot be written is refused;
* the NDA flag is admin-only, is validated, and shows in the user record.

Runs against a throwaway registry and a fake CAD tree; no FreeCAD needed.
"""
from __future__ import annotations

import datetime
import json

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import model3d as M
from motor_ai_sim.api import app

ADMIN = "admin@example.com"
USER = "client@example.com"
NDA_USER = "nda@example.com"
OTHER = "other@example.com"
DIE, CFG, MID = "CIANO14 40 new", "L12", "CIANO14_40_new_L12"

FULL_GLB = b"glTF-FULL-MODEL-BYTES"
ENV_GLB = b"glTF-ENVELOPE-BYTES"
STEP_HEAD = ("ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('FreeCAD Model'),'2;1');\n"
             "FILE_NAME('Open CASCADE Shape Model','2026-10-13T10:00:00',('Author'),('Org'),'OCC','FreeCAD','Unknown');\n"
             "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\nENDSEC;\n")
DATA = "DATA;\n#1=CARTESIAN_POINT('',(0.,0.,0.));\nENDSEC;\nEND-ISO-10303-21;\n"
FULL_STEP = (STEP_HEAD + DATA.replace("#1", "#1 /*FULL*/")).encode("latin-1")
ENV_STEP = (STEP_HEAD + DATA.replace("#1", "#1 /*ENV*/")).encode("latin-1")

client = TestClient(app)


def _sha(b):
    return M.sha256_bytes(b)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U

    cad = tmp_path / "cad"
    d = cad / DIE / CFG
    d.mkdir(parents=True)
    files = {MID + "_full.glb": FULL_GLB, MID + "_envelope.glb": ENV_GLB, MID + "_active.step": FULL_STEP,
             MID + "_envelope.step": ENV_STEP}
    for n, b in files.items():
        (d / n).write_bytes(b)
    (d / "manifest.json").write_text(json.dumps({
        "motor_id": MID, "die": DIE, "config": CFG, "param_hash": "ab" * 32, "built_at": "2026-10-13T12:00:00+00:00",
        "template": {"version": "A.4.0"}, "files": {n: {"sha256": _sha(b), "bytes": len(b)} for n, b in files.items()}}))
    # a second motor nobody was granted, and a half-built directory that must be ignored
    o = cad / "Other die" / "L1"
    o.mkdir(parents=True)
    (o / "manifest.json").write_text(json.dumps({"motor_id": "Other_die_L1", "files": {
        "Other_die_L1_envelope.glb": {"sha256": "0" * 64}, "Other_die_L1_full.glb": {"sha256": "0" * 64}}}))
    (o / "Other_die_L1_envelope.glb").write_bytes(b"x")
    (o / "Other_die_L1_full.glb").write_bytes(b"x")
    (cad / DIE / "L12.building-123").mkdir()

    monkeypatch.setenv("MOTRES_CAD_ROOT", str(cad))
    monkeypatch.setenv("CAD_DOWNLOAD_LOG", str(tmp_path / "cad_downloads.jsonl"))
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(USER, "password-client", role="user", name="Ann Müller")
    U.create_user(NDA_USER, "password-nda", role="user", name="Bob O'Neil")
    U.create_user(OTHER, "password-other", role="user", name="Other")
    for e in (USER, NDA_USER):
        U.set_motor_grants(e, all_motors=False, dies=[DIE])
    U.set_nda(NDA_USER, True, "2026-10-01", by=ADMIN)
    h = lambda e: {"Authorization": f"Bearer {U.issue_token(e)}"}
    return dict(admin=h(ADMIN), user=h(USER), nda=h(NDA_USER), other=h(OTHER), log=tmp_path / "cad_downloads.jsonl",
                url=f"/api/model3d/{DIE}/{CFG}")


def _log(env):
    p = env["log"]
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


# -- who gets which model -----------------------------------------------------

def test_user_without_nda_gets_the_envelope_only(env):
    r = client.get("/api/model3d/models", headers=env["user"])
    assert r.status_code == 200 and r.json()["variant"] == "envelope"
    assert [m["motor_id"] for m in r.json()["models"]] == [MID]
    assert r.json()["models"][0]["glb_sha256"] == _sha(ENV_GLB)
    g = client.get(env["url"] + "/model.glb", headers=env["user"])
    assert g.status_code == 200 and g.content == ENV_GLB and g.headers["x-model-variant"] == "envelope"
    s = client.get(env["url"] + "/model.step", headers=env["user"])
    assert s.status_code == 200 and b"/*ENV*/" in s.content and b"/*FULL*/" not in s.content


def test_nda_account_and_owner_get_the_full_model(env):
    for who in ("nda", "admin"):
        r = client.get("/api/model3d/models", headers=env[who])
        assert r.json()["variant"] == "full"
        g = client.get(env["url"] + "/model.glb", headers=env[who])
        assert g.content == FULL_GLB and g.headers["x-model-variant"] == "full"
        s = client.get(env["url"] + "/model.step", headers=env[who])
        assert b"/*FULL*/" in s.content


def test_the_url_cannot_ask_for_the_other_variant(env):
    # no variant or file name in the route: the server decides; a crafted name is just another (missing) model
    for bad in ("/model_full.glb", "/..%2FL12/model.glb", f"/{MID}_full.glb"):
        r = client.get(env["url"] + bad, headers=env["user"])
        assert r.status_code in (400, 404)
    r = client.get(f"/api/model3d/{DIE}/L12.building-123/model.glb", headers=env["admin"])
    assert r.status_code == 404


def test_ungranted_motor_is_404_and_not_listed(env):
    r = client.get("/api/model3d/models", headers=env["other"])
    assert r.status_code == 200 and r.json()["models"] == []
    assert client.get(env["url"] + "/model.glb", headers=env["other"]).status_code == 404
    assert client.get(env["url"] + "/model.step", headers=env["other"]).status_code == 404
    assert _log(env) == []                       # a refused request is not a download
    a = client.get("/api/model3d/models", headers=env["admin"]).json()["models"]
    assert sorted(m["motor_id"] for m in a) == sorted([MID, "Other_die_L1"])


def test_anonymous_is_refused(env, monkeypatch):
    from motor_ai_sim import auth
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    assert client.get("/api/model3d/models").status_code == 401
    assert client.get(env["url"] + "/model.glb").status_code == 401
    assert client.get(env["url"] + "/model.step").status_code == 401


def test_list_carries_no_bom_or_prices(env):
    txt = json.dumps(client.get("/api/model3d/models", headers=env["admin"]).json()).lower()
    for word in ("price", "cost", "bom", "eur", "usd"):
        assert word not in txt


# -- the stamp and the log ----------------------------------------------------

def test_step_is_stamped_and_logged(env):
    r = client.get(env["url"] + "/model.step", headers=env["user"])
    day = datetime.datetime.now(datetime.timezone.utc).date()
    name = f"{MID}_envelope_ann-muller_{day:%Y%m%d}.step"
    assert name in r.headers["content-disposition"]
    st = M.stamp_of(r.content)
    assert st["filename"] == name and st["customer"] == "Ann Muller"      # ASCII in the header
    head = r.content[:r.content.find(b"ENDSEC;")].decode("latin-1")
    assert "Licensed to Ann Muller on %s" % day.isoformat() in head and "envelope" in head
    assert r.content.endswith(DATA.replace("#1", "#1 /*ENV*/").encode("latin-1"))   # DATA copied byte for byte
    (line,) = _log(env)
    assert line["user"] == USER and line["file"] == name and line["variant"] == "envelope" and line["kind"] == "step"
    assert line["motor_id"] == MID and line["die"] == DIE and line["config"] == CFG
    assert line["source_sha256"] == _sha(ENV_STEP) and line["sha256"] == _sha(r.content) and line["bytes"] == len(r.content)
    assert line["template"] == "A.4.0" and line["built_at"] == "2026-10-13T12:00:00+00:00" and line["iso"].endswith("Z")


def test_apostrophe_in_the_customer_name_is_escaped(env):
    r = client.get(env["url"] + "/model.step", headers=env["nda"])
    assert "Bob O''Neil" in r.content.decode("latin-1")
    assert M.stamp_of(r.content)["filename"].startswith(f"{MID}_full_bob-o-neil_")
    assert _log(env)[0]["variant"] == "full" and _log(env)[0]["nda"] is True


def test_every_download_adds_a_line_and_glb_views_do_not(env):
    client.get(env["url"] + "/model.glb", headers=env["user"])
    assert _log(env) == []
    client.get(env["url"] + "/model.step", headers=env["user"])
    client.get(env["url"] + "/model.step", headers=env["admin"])
    assert [l["user"] for l in _log(env)] == [USER, ADMIN]
    rows = client.get("/api/model3d/downloads", headers=env["admin"]).json()["downloads"]
    assert [r["user"] for r in rows] == [ADMIN, USER]                    # newest first
    assert client.get("/api/model3d/downloads", headers=env["user"]).status_code == 403


def test_unwritable_log_refuses_the_download(env, monkeypatch, tmp_path):
    bad = tmp_path / "file-not-dir"
    bad.write_text("x")
    monkeypatch.setenv("CAD_DOWNLOAD_LOG", str(bad / "sub" / "log.jsonl"))
    r = client.get(env["url"] + "/model.step", headers=env["user"])
    assert r.status_code == 503


def test_stamp_step_rejects_non_step_and_keeps_schema():
    day = datetime.date(2026, 10, 13)
    with pytest.raises(ValueError):
        M.stamp_step(b"not a step file", filename="a.step", customer="x", day=day, variant="envelope", motor_id="m")
    out = M.stamp_step(FULL_STEP, filename="a.step", customer="Zoë */ Ltd", day=day, variant="full", motor_id="m")
    assert b"FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));" in out
    assert b"/* MOTRES" in out and b"Zoe" in out and "Zoë".encode("latin-1") not in out
    head = out[:out.find(b"ENDSEC;")]
    assert head.count(b"*/") == 1                     # the customer cannot close the header comment early
    assert M.download_name("m", "full", "Zoë */ Ltd", day) == "m_full_zoe-ltd_20261013.step"


# -- the NDA flag -------------------------------------------------------------

def test_nda_flag_is_admin_only_and_validated(env):
    u = f"/api/auth/users/{USER}"
    assert client.patch(u, json={"nda_signed": True}, headers=env["user"]).status_code == 403
    assert client.get("/api/model3d/models", headers=env["user"]).json()["variant"] == "envelope"
    bad = client.patch(u, json={"nda_signed": True, "nda_date": "13.10.2026"}, headers=env["admin"])
    assert bad.status_code == 422
    ok = client.patch(u, json={"nda_signed": True, "nda_date": "2026-10-13"}, headers=env["admin"])
    assert ok.status_code == 200 and ok.json()["user"]["nda"] == {"signed": True, "date": "2026-10-13", "by": ADMIN}
    assert client.get("/api/model3d/models", headers=env["user"]).json()["variant"] == "full"
    off = client.patch(u, json={"nda_signed": False}, headers=env["admin"])
    assert off.json()["user"]["nda"] == {"signed": False, "date": "", "by": ""}
    assert client.get("/api/model3d/models", headers=env["user"]).json()["variant"] == "envelope"
    users = {x["email"]: x for x in client.get("/api/auth/users", headers=env["admin"]).json()["users"]}
    assert users[NDA_USER]["nda"]["signed"] is True and users[ADMIN]["nda"]["signed"] is False


def test_nda_default_date_is_today(env):
    from motor_ai_sim import users as U
    got = U.set_nda(OTHER, True)
    assert got["signed"] and got["date"] == datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
