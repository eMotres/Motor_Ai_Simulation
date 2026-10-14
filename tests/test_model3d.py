"""3D model menu (routes/model3d.py, model3d.py): who may view, who may download, the STEP stamp, the download log, the NDA flag.

One model for everybody (owner 2026-10-13: "look and rotate for all, download under NDA"):
* every signed-in account that may see the motor gets the full GLB; an ungranted motor is a 404, anonymous is a 401;
* the STEP download is 403 for an account without NDA; an account flagged "NDA signed" and the owner/admin get it;
* every STEP download is stamped (customer name + date in the header and the file name) and logged, and a download whose
  log line cannot be written is refused;
* the NDA flag is admin-only, is validated, and switches the download on and off.

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

GLB = b"glTF-FULL-MODEL-BYTES"
STEP_HEAD = ("ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('FreeCAD Model'),'2;1');\n"
             "FILE_NAME('Open CASCADE Shape Model','2026-10-13T10:00:00',('Author'),('Org'),'OCC','FreeCAD','Unknown');\n"
             "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\nENDSEC;\n")
DATA = "DATA;\n#1=CARTESIAN_POINT('',(0.,0.,0.));\nENDSEC;\nEND-ISO-10303-21;\n"
STEP = (STEP_HEAD + DATA).encode("latin-1")

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
    files = {MID + "_full.glb": GLB, MID + "_active.step": STEP}
    for n, b in files.items():
        (d / n).write_bytes(b)
    # a stale envelope file from an older build must never be served
    (d / (MID + "_envelope.step")).write_bytes(b"STALE-ENVELOPE")
    (d / "manifest.json").write_text(json.dumps({
        "motor_id": MID, "die": DIE, "config": CFG, "param_hash": "ab" * 32, "built_at": "2026-10-13T12:00:00+00:00",
        "template": {"version": "A.4.0"},
        "files": {n: {"sha256": _sha(b), "bytes": len(b)} for n, b in files.items()}
        | {MID + "_envelope.step": {"sha256": _sha(b"STALE-ENVELOPE"), "bytes": 14}}}))
    # a second motor nobody was granted, and a half-built directory that must be ignored
    o = cad / "Other die" / "L1"
    o.mkdir(parents=True)
    (o / "manifest.json").write_text(json.dumps({"motor_id": "Other_die_L1", "files": {"Other_die_L1_full.glb": {"sha256": "0" * 64}}}))
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


# -- view for all, download under NDA -----------------------------------------

def test_user_without_nda_views_but_cannot_download(env):
    r = client.get("/api/model3d/models", headers=env["user"])
    assert r.status_code == 200 and r.json()["can_download"] is False
    assert [m["motor_id"] for m in r.json()["models"]] == [MID]
    assert r.json()["models"][0]["glb_sha256"] == _sha(GLB)
    g = client.get(env["url"] + "/model.glb", headers=env["user"])
    assert g.status_code == 200 and g.content == GLB
    s = client.get(env["url"] + "/model.step", headers=env["user"])
    assert s.status_code == 403 and "NDA" in s.json()["detail"]
    assert _log(env) == []                       # a refused download is not logged as a download


def test_nda_user_gets_the_glb_and_the_step(env):
    r = client.get("/api/model3d/models", headers=env["nda"])
    assert r.json()["can_download"] is True
    assert client.get(env["url"] + "/model.glb", headers=env["nda"]).content == GLB
    s = client.get(env["url"] + "/model.step", headers=env["nda"])
    assert s.status_code == 200 and s.content.endswith(DATA.encode("latin-1"))


def test_owner_gets_the_glb_and_the_step(env):
    assert client.get("/api/model3d/models", headers=env["admin"]).json()["can_download"] is True
    assert client.get(env["url"] + "/model.glb", headers=env["admin"]).content == GLB
    assert client.get(env["url"] + "/model.step", headers=env["admin"]).status_code == 200


def test_one_model_only_no_variant_and_no_envelope_served(env):
    body = client.get("/api/model3d/models", headers=env["admin"]).json()
    assert "variant" not in json.dumps(body).lower()
    for bad in ("/model_full.glb", "/..%2FL12/model.glb", f"/{MID}_envelope.step", "/model.envelope.step"):
        r = client.get(env["url"] + bad, headers=env["nda"])
        assert r.status_code in (400, 404)
    r = client.get(f"/api/model3d/{DIE}/L12.building-123/model.glb", headers=env["admin"])
    assert r.status_code == 404
    # the stale envelope file on disk is never what a download returns
    assert b"STALE-ENVELOPE" not in client.get(env["url"] + "/model.step", headers=env["nda"]).content


def test_ungranted_motor_is_404_and_not_listed(env):
    r = client.get("/api/model3d/models", headers=env["other"])
    assert r.status_code == 200 and r.json()["models"] == []
    assert client.get(env["url"] + "/model.glb", headers=env["other"]).status_code == 404
    assert client.get(env["url"] + "/model.step", headers=env["other"]).status_code == 404
    assert _log(env) == []
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
    r = client.get(env["url"] + "/model.step", headers=env["nda"])
    day = datetime.datetime.now(datetime.timezone.utc).date()
    name = f"{MID}_bob-o-neil_{day:%Y%m%d}.step"
    assert name in r.headers["content-disposition"]
    st = M.stamp_of(r.content)
    assert st["filename"] == name and st["customer"] == "Bob O''Neil"
    head = r.content[:r.content.find(b"ENDSEC;")].decode("latin-1")
    assert "Licensed to Bob O''Neil on %s" % day.isoformat() in head
    assert r.content.endswith(DATA.encode("latin-1"))                    # DATA copied byte for byte
    (line,) = _log(env)
    assert line["user"] == NDA_USER and line["file"] == name and line["kind"] == "step" and line["nda"] is True
    assert line["motor_id"] == MID and line["die"] == DIE and line["config"] == CFG and "variant" not in line
    assert line["source_file"] == MID + "_active.step"
    assert line["source_sha256"] == _sha(STEP) and line["sha256"] == _sha(r.content) and line["bytes"] == len(r.content)
    assert line["template"] == "A.4.0" and line["built_at"] == "2026-10-13T12:00:00+00:00" and line["iso"].endswith("Z")


def test_every_download_adds_a_line_and_glb_views_do_not(env):
    client.get(env["url"] + "/model.glb", headers=env["user"])
    client.get(env["url"] + "/model.step", headers=env["user"])              # 403
    assert _log(env) == []
    client.get(env["url"] + "/model.step", headers=env["nda"])
    client.get(env["url"] + "/model.step", headers=env["admin"])
    assert [l["user"] for l in _log(env)] == [NDA_USER, ADMIN]
    rows = client.get("/api/model3d/downloads", headers=env["admin"]).json()["downloads"]
    assert [r["user"] for r in rows] == [ADMIN, NDA_USER]                    # newest first
    assert client.get("/api/model3d/downloads", headers=env["user"]).status_code == 403


def test_unwritable_log_refuses_the_download(env, monkeypatch, tmp_path):
    bad = tmp_path / "file-not-dir"
    bad.write_text("x")
    monkeypatch.setenv("CAD_DOWNLOAD_LOG", str(bad / "sub" / "log.jsonl"))
    assert client.get(env["url"] + "/model.step", headers=env["nda"]).status_code == 503


def test_stamp_step_rejects_non_step_and_keeps_schema():
    day = datetime.date(2026, 10, 13)
    with pytest.raises(ValueError):
        M.stamp_step(b"not a step file", filename="a.step", customer="x", day=day, motor_id="m")
    out = M.stamp_step(STEP, filename="a.step", customer="Zoë */ Ltd", day=day, motor_id="m")
    assert b"FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));" in out
    assert b"/* MOTRES" in out and b"Zoe" in out and "Zoë".encode("latin-1") not in out
    head = out[:out.find(b"ENDSEC;")]
    assert head.count(b"*/") == 1                     # the customer cannot close the header comment early
    assert M.download_name("m", "Zoë */ Ltd", day) == "m_zoe-ltd_20261013.step"


# -- the NDA flag -------------------------------------------------------------

def test_nda_flag_is_admin_only_validated_and_switches_the_download(env):
    u = f"/api/auth/users/{USER}"
    assert client.patch(u, json={"nda_signed": True}, headers=env["user"]).status_code == 403
    assert client.get(env["url"] + "/model.step", headers=env["user"]).status_code == 403
    bad = client.patch(u, json={"nda_signed": True, "nda_date": "13.10.2026"}, headers=env["admin"])
    assert bad.status_code == 422
    ok = client.patch(u, json={"nda_signed": True, "nda_date": "2026-10-13"}, headers=env["admin"])
    assert ok.status_code == 200 and ok.json()["user"]["nda"] == {"signed": True, "date": "2026-10-13", "by": ADMIN}
    assert client.get("/api/model3d/models", headers=env["user"]).json()["can_download"] is True
    assert client.get(env["url"] + "/model.step", headers=env["user"]).status_code == 200
    off = client.patch(u, json={"nda_signed": False}, headers=env["admin"])
    assert off.json()["user"]["nda"] == {"signed": False, "date": "", "by": ""}
    assert client.get(env["url"] + "/model.step", headers=env["user"]).status_code == 403
    assert client.get(env["url"] + "/model.glb", headers=env["user"]).status_code == 200      # viewing stays open
    users = {x["email"]: x for x in client.get("/api/auth/users", headers=env["admin"]).json()["users"]}
    assert users[NDA_USER]["nda"]["signed"] is True and users[ADMIN]["nda"]["signed"] is False


def test_nda_default_date_is_today(env):
    from motor_ai_sim import users as U
    got = U.set_nda(OTHER, True)
    assert got["signed"] and got["date"] == datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


# -- NDA basis in the file and in the log, admin table, trace ------------------

REC_ID = "n20261012-0123456789ab"


def _online(env, monkeypatch, tmp_path, email=USER):
    """USER signs the NDA online: a record in the (append-only) signature file and the NDA state on the account."""
    from motor_ai_sim import users as U
    legal = tmp_path / "legal"
    legal.mkdir()
    rec = dict(id=REC_ID, iso="2026-10-12T09:30:00Z", user=email, email=email, company="Acme GmbH", signatory="Ann Mueller",
               version="v1.0", text_sha256="ab" * 32)
    (legal / "nda_signatures.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    monkeypatch.setenv("MOTRES_LEGAL_ROOT", str(legal))
    U.set_nda(email, True, "2026-10-12", by="online", version="v1.0", company="Acme GmbH", record_id=REC_ID)


def _head(content):
    return content[:content.find(b"ENDSEC;")].decode("latin-1")


def test_online_nda_download_row_and_header(env, monkeypatch, tmp_path):
    _online(env, monkeypatch, tmp_path)
    r = client.get(env["url"] + "/model.step", headers=env["user"])
    assert r.status_code == 200
    (row,) = _log(env)
    assert row["user"] == USER and row["email"] == USER and row["role"] == "user" and row["company"] == "Acme GmbH"
    b = row["nda_basis"]
    assert (b["type"], b["version"], b["record_id"], b["text_sha256"], b["company"], b["signatory"]) == (
        "online", "v1.0", REC_ID, "ab" * 32, "Acme GmbH", "Ann Mueller")
    assert row["download_id"].startswith("d") and row["sha256"] == _sha(r.content) and row["kind"] == "step"
    assert row["ip"] and "user_agent" in row and row["iso"].endswith("Z")
    head = _head(r.content)
    assert "Download ID: " + row["download_id"] in head
    assert "NDA reference: v1.0 record " + REC_ID in head
    assert "Acme GmbH" in head and "CONFIDENTIAL - MOTRES Model Access and NDA v1.0" in head
    assert M.stamp_of(r.content)["download_id"] == row["download_id"]


def test_admin_flag_and_internal_rows(env):
    r = client.get(env["url"] + "/model.step", headers=env["nda"])               # flag set by ADMIN on 2026-10-01
    a = _log(env)[-1]
    assert a["nda_basis"] == dict(type="admin", ref="admin-flag 2026-10-01", label="admin-flag 2026-10-01",
                                  set_by=ADMIN, date="2026-10-01")
    assert "NDA reference: admin-flag 2026-10-01" in _head(r.content)
    assert "NDA admin-flag 2026-10-01" in _head(r.content)
    r2 = client.get(env["url"] + "/model.step", headers=env["admin"])
    i = _log(env)[-1]
    assert i["nda_basis"]["type"] == "internal" and i["nda"] is False and i["role"] == "admin"
    assert "NDA reference: internal" in _head(r2.content) and "MOTRES Model Access and NDA internal" in _head(r2.content)
    assert len({x["download_id"] for x in _log(env)}) == 2                      # unique ids


def test_stamped_step_is_valid_and_geometry_unchanged(env):
    r = client.get(env["url"] + "/model.step", headers=env["nda"])
    c = r.content
    assert c.startswith(b"ISO-10303-21;\nHEADER;\n") and c.endswith(b"END-ISO-10303-21;\n")
    assert c[c.find(b"ENDSEC;"):] == STEP[STEP.find(b"ENDSEC;"):]                 # DATA section byte for byte
    head = _head(c)
    for ent in ("FILE_DESCRIPTION(", "FILE_NAME(", "FILE_SCHEMA("):
        assert head.count(ent) == 1
    assert head.count("'") % 2 == 0                                              # quotes balanced (apostrophes doubled)
    assert head.count("(") == head.count(")")


def test_log_is_append_only(env):
    client.get(env["url"] + "/model.step", headers=env["nda"])
    first = env["log"].read_bytes()
    client.get(env["url"] + "/model.step", headers=env["admin"])
    assert env["log"].read_bytes().startswith(first)                             # the earlier line is untouched


def test_downloads_filters(env, monkeypatch, tmp_path):
    _online(env, monkeypatch, tmp_path)
    for h in ("user", "nda", "admin"):
        client.get(env["url"] + "/model.step", headers=env[h])

    def ids(**q):
        return [r["user"] for r in client.get("/api/model3d/downloads", params=q, headers=env["admin"]).json()["downloads"]]
    assert ids() == [ADMIN, NDA_USER, USER]                                      # newest first
    assert ids(user="client") == [USER]
    assert ids(company="acme") == [USER]
    assert ids(motor="ciano14") == [ADMIN, NDA_USER, USER]
    assert ids(motor="nothing") == []
    assert ids(nda_version="v1.0") == [USER]
    assert ids(nda_version="admin-flag") == [NDA_USER]
    assert ids(nda_version="internal") == [ADMIN]


def test_trace_by_id_by_hash_and_pasted_header(env):
    r = client.get(env["url"] + "/model.step", headers=env["nda"])
    client.get(env["url"] + "/model.step", headers=env["admin"])
    row = _log(env)[0]

    def tr(text, who="admin"):
        return client.post("/api/model3d/trace", json={"text": text}, headers=env[who])
    by_id = tr("Download ID: " + row["download_id"]).json()
    assert [m["download_id"] for m in by_id["matches"]] == [row["download_id"]] and by_id["ids"] == [row["download_id"]]
    by_hash = tr(_sha(r.content)).json()
    assert [m["user"] for m in by_hash["matches"]] == [NDA_USER] and by_hash["hashes"] == [_sha(r.content)]
    pasted = tr("FILE_DESCRIPTION(('x','" + _head(r.content).split("Download ID: ")[1].split("'")[0] + "'),'2;1');").json()
    assert [m["user"] for m in pasted["matches"]] == [NDA_USER]
    assert tr("nothing to find here").json()["matches"] == []
    assert tr("d20990101-ffffffffffff " + "0" * 64).json()["matches"] == []
    assert tr(row["download_id"], "user").status_code == 403
