"""Who sees the stored ORIGINAL of a specification file (coordinator 2026-11-24, final): only who may EDIT the specification (the
project owner, a global admin) and MOTRES staff.  Every other member does not see it in the documents list, the ZIPs, the counts or
the cover choices, and a download by its id is a 404 like an unknown id.  Mechanism: the ``spec_source`` flag of the document row,
evaluated in the ONE ``documents._visible`` filter (the same place as ``internal`` / ``price_restricted``).  Synthetic data only."""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth, users
from motor_ai_sim.development import documents, spec_file_assistant as FA, store
from motor_ai_sim.development.routes import router

OWNER, ENG, CUST, ADMIN, STAFF, OEM = ("owner@example.test", "eng@example.test", "cust@example.test", "admin@example.test",
                                       "staff@example.test", "oem@example.test")
ROLES = {"t-owner": (OWNER, "user"), "t-eng": (ENG, "user"), "t-cust": (CUST, "user"), "t-admin": (ADMIN, "admin"),
         "t-staff": (STAFF, "engineer"), "t-oem": (OEM, "oem")}


def hdr(who):
    return {"Authorization": "Bearer " + who}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("CAD_DOWNLOAD_LOG", str(tmp_path / "downloads.jsonl"))
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(zip(("email", "role"), ROLES[token.split()[-1]])) if token else None)
    monkeypatch.setattr(users, "get_user", lambda email: {"email_verified": True, "role": ROLES_BY_EMAIL.get(email, "user")})
    store.create_project({"id": "alpha", "title": "Alpha", "customer_org": "Org", "status": "spec", "created_at": "t", "updated_at": "t",
                          "owner_engineer": OWNER, "linked_motors": [], "notes": "", "pending_invites": [],
                          "members": [{"email": OWNER, "role": "owner"}, {"email": ENG, "role": "engineer"}, {"email": CUST, "role": "customer"},
                                      {"email": OEM, "role": "customer"}]})
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


ROLES_BY_EMAIL = {ADMIN: "admin", STAFF: "engineer", OEM: "oem"}


def put_files(env):
    """An ordinary document and a spec source; returns (plain id, source id)."""
    plain = env.post("/api/projects/alpha/documents", headers=hdr("t-owner"), files={"file": ("board.txt", b"plain document")}).json()["id"]
    row = FA.store_original("alpha", {"email": OWNER, "role": "user"}, "requirements.txt", b"Unit price 5 EUR; speed 6000 rpm", "text/plain")
    return plain, row["id"]


SEE = ["t-owner", "t-admin", "t-staff"]
BLIND = ["t-eng", "t-cust", "t-oem"]


@pytest.mark.parametrize("who", SEE)
def test_owner_admin_and_staff_list_and_download_it(env, who):
    plain, src = put_files(env)
    listed = env.get("/api/projects/alpha/documents", headers=hdr(who)).json()
    ids = {d["id"] for d in listed["documents"]}
    assert ids == {plain, src}
    assert next(d for d in listed["documents"] if d["id"] == src)["spec_source"] is True
    got = env.get(f"/api/projects/alpha/documents/{src}/download", headers=hdr(who))
    assert got.status_code == 200 and b"speed 6000 rpm" in got.content


@pytest.mark.parametrize("who", BLIND)
def test_a_customer_an_engineer_member_and_a_factory_account_do_not_see_it(env, who):
    plain, src = put_files(env)
    listed = env.get("/api/projects/alpha/documents", headers=hdr(who)).json()
    assert {d["id"] for d in listed["documents"]} == {plain}                          # not in the list, so not in a count either
    assert env.get(f"/api/projects/alpha/documents/{src}/download", headers=hdr(who)).status_code == 404
    assert env.get(f"/api/projects/alpha/documents/nope/download", headers=hdr(who)).status_code == 404     # the same answer as an unknown id
    assert env.patch(f"/api/projects/alpha/documents/{src}", headers=hdr(who), json={"revision": "x"}).status_code == 404
    assert env.delete(f"/api/projects/alpha/documents/{src}", headers=hdr(who)).status_code == 404


def _zip_names(resp):
    return set(zipfile.ZipFile(io.BytesIO(resp.content)).namelist())


def _plain_and_labelled(env):
    plain, src = put_files(env)
    env.patch(f"/api/projects/alpha/documents/{plain}", headers=hdr("t-owner"), json={"revision": "A"})
    return plain, src


@pytest.mark.parametrize("who", BLIND)
def test_the_zips_never_hold_the_original_for_a_member_who_may_not_edit_the_specification(env, who):
    _plain_and_labelled(env)
    resp = env.get("/api/projects/alpha/documents.zip", headers=hdr(who))
    assert resp.status_code in (200, 403)                                                        # a customer-side member: the current release only
    if resp.status_code == 200:
        assert "requirements.txt" not in " ".join(_zip_names(resp))
    cur = env.get("/api/projects/alpha/documents.zip", headers=hdr(who), params={"revision": "A"})
    assert cur.status_code == 200 and not any("requirements" in n for n in _zip_names(cur)) and any("board.txt" in n for n in _zip_names(cur))
    other = env.get("/api/projects/alpha/documents.zip", headers=hdr(who), params={"revision": "specification source"})
    assert other.status_code in (403, 404, 409) or not any("requirements" in n for n in _zip_names(other))


def test_the_zips_hold_the_original_for_staff_and_the_owner_by_its_label(env):
    _plain_and_labelled(env)
    every = env.get("/api/projects/alpha/documents.zip", headers=hdr("t-staff"))
    assert every.status_code == 200 and any(n.endswith("requirements.txt") for n in _zip_names(every))
    for who in ("t-owner", "t-staff", "t-admin"):
        got = env.get("/api/projects/alpha/documents.zip", headers=hdr(who), params={"revision": "specification source"})
        assert got.status_code == 200 and any(n.endswith("requirements.txt") for n in _zip_names(got)), who
        assert not any("requirements" in n for n in _zip_names(env.get("/api/projects/alpha/documents.zip", headers=hdr(who), params={"revision": "A"})))


def test_the_spec_source_is_no_release_for_anybody(env):
    put_files(env)
    for who in SEE + BLIND:
        info = env.get("/api/projects/alpha/documents", headers=hdr(who)).json()
        assert all(g["revision"] != "specification source" for g in info["releases"]), who
        assert info["current_release"] != "specification source"


def test_the_visibility_filter_defaults_to_nobody():
    rows = [{"id": "s", "spec_source": True}, {"id": "p"}]
    assert [r["id"] for r in documents._visible(rows, True)] == ["p"]                              # even a staff-context caller that says nothing
    assert [r["id"] for r in documents._visible(rows, True, spec=True)] == ["s", "p"]
    assert [r["id"] for r in documents._visible(rows, False)] == ["p"]


def test_it_can_never_be_the_cover(env):
    plain, src = put_files(env)
    r = env.put("/api/projects/alpha/cover", headers=hdr("t-owner"), json={"document_id": src})
    assert r.status_code == 422 and "specification source" in r.json()["detail"]
    from motor_ai_sim.development import cover
    assert cover._cover_allowed({"id": "x", "spec_source": True}) is False and cover._cover_allowed({"id": "x"}) is True


def test_readers_of_the_project_documents_are_a_known_short_list():
    """Every reader of a project's documents (rows or bytes) in the application, the scripts and the deploy files.  A new one is a new
    place that must apply ``documents._visible`` / ``documents._spec``: this list is the guard.  Backups and a mirror copy folders as
    they are, on the staff side (not an API for members); the digest, the owner report and MCP are tested below."""
    from pathlib import Path
    repo = Path(documents.__file__).resolve().parents[3]
    needles = ("load_documents(", "DOCUMENTS_JSON", "read_document_file(", "documents_file_dir(", "documents/documents.json", "documents/files")
    found = set()
    for base in ("src", "scripts", "deploy"):
        if not (repo / base).is_dir():
            continue
        for p in (repo / base).rglob("*"):
            if p.suffix not in (".py", ".sh", ".yml", ".yaml", ".service", ".timer", ".cfg", ".ini", ".toml") or not p.is_file():
                continue
            if any(n in p.read_text(encoding="utf-8", errors="replace") for n in needles):
                found.add(str(p.relative_to(repo)))
    assert found == {"src/motor_ai_sim/development/cover.py", "src/motor_ai_sim/development/documents.py",
                     "src/motor_ai_sim/development/models.py", "src/motor_ai_sim/development/spec_file_assistant.py",
                     "src/motor_ai_sim/development/store.py"}, sorted(found)


def test_the_digest_the_owner_report_and_mcp_do_not_expose_it(env):
    from pathlib import Path
    root = Path(documents.__file__).resolve().parents[1]
    for rel in ("digest", "mcp_app.py", "mcp_tools.py", "mcp_discovery.py", "owner_report"):
        p = root / rel
        for f in ([p] if p.is_file() else sorted(p.rglob("*.py"))):
            text = f.read_text(encoding="utf-8")
            assert "load_documents" not in text and "development.documents" not in text and "read_document_file" not in text, f.name
    from motor_ai_sim.owner_report import sources as OR
    plain, src = put_files(env)
    env.get(f"/api/projects/alpha/documents/{src}/download", headers=hdr("t-owner"))
    audit = [json.loads(x) for x in (store.path_for("alpha") / "audit.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    texts = [OR._project_audit_text(ev) for ev in audit]
    assert all("requirements.txt" not in json.dumps(t) for t in texts)                            # no line names the original (upload, attach, download)
    assert any(t and "board.txt" in t[0] for t in texts)                                          # an ordinary upload is still reported


def test_the_upload_route_does_not_let_the_flag_be_set_or_cleared_by_hand(env):
    plain, src = put_files(env)
    r = env.post("/api/projects/alpha/documents", headers=hdr("t-owner"), files={"file": ("x.txt", b"x")}, data={"spec_source": "true"})
    assert r.status_code == 200
    rows = {d["id"]: d for d in store.load_documents("alpha")}
    assert rows[r.json()["id"]].get("spec_source") in (None, False)
    r2 = env.patch(f"/api/projects/alpha/documents/{src}", headers=hdr("t-owner"), json={"spec_source": False})
    assert r2.status_code == 422
