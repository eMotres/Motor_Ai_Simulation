"""Two regions, stage 3 (docs/regions/DESIGN.md section 13): the per-account migration tool, end to end on two tmp
servers (EU = source, US = target) with a fake peer channel.  Nothing leaves tmp_path; no server is touched.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.regions import handoff as H
from motor_ai_sim.regions import migrate as MG
from motor_ai_sim.regions import peer as P
from motor_ai_sim.regions import store as RS

SECRET = "m" * 40
PW = "correct horse battery"
BUYER = "buyer@example.com"
OTHER = "other.eu@example.com"
STAFF = "staff@example.com"
client = TestClient(app)
ROOT = Path(__file__).resolve().parents[1]

_ENV = ("MOTRES_REGION", "MOTRES_PEER_URL", "MOTRES_PEER_REGION", "MOTRES_PEER_HMAC_SECRET",
        "MOTRES_PEER_HMAC_SECRET_FILE", "MOTRES_REGION_GUARD", "MOTRES_REGION_BASE_URL_EU", "MOTRES_REGION_BASE_URL_US",
        "MOTRES_PROJECTS_ROOT", "MOTRES_PRODUCTION_ROOT", "MOTRES_MOTOR_TESTS_ROOT", "MOTRES_CAD_ROOT",
        "MOTRES_VERIFICATION_ROOT", "MOTRES_LEGAL_ROOT", "MOTRES_DIRECTORY_ROOT", "PUBLISHED_ROOT")


def _server(env, region: str) -> Path:
    """Point every store at ``tmp/<region>/`` and make this process that region's server."""
    from motor_ai_sim import comm_prefs as CP
    from motor_ai_sim import motor_access as MA
    mp, d = env["mp"], env["tmp"] / region
    ident, shared = d / "identity", d / "shared"
    for p in (ident, shared, d / "workspaces"):
        p.mkdir(parents=True, exist_ok=True)
    mp.setattr(env["U"], "_USERS_FILE", ident / "users.json")
    mp.setattr(env["S"], "_SESSIONS_FILE", ident / ".sessions.json")
    mp.setattr(env["S"], "_EVENTS_FILE", d / "logs" / "auth_events.jsonl")
    mp.setattr(CP, "_ROOT", ident)
    mp.setattr(CP, "_STATE_FILE", ident / "comm_prefs.json")
    mp.setattr(CP, "_HISTORY_FILE", ident / "comm_prefs_history.jsonl")
    mp.setattr(MA, "_DIE_ACCESS_FILE", ident / "die_access.json")
    mp.setenv("SHARED_ROOT", str(shared))
    mp.setenv("WORKSPACES_ROOT", str(d / "workspaces"))
    mp.setenv("AUTH_SECRET", f"auth-secret-{region}-not-real")
    mp.setenv("ADMIN_AUDIT_FILE", str(ident / "admin_audit.jsonl"))
    mp.setenv("MOTRES_REGION_DIRECTORY_FILE", str(ident / "region_directory.json"))
    mp.setenv("MOTRES_REGION_HANDOFF_FILE", str(ident / "region_handoff_used.json"))
    mp.setenv("MOTRES_REGION_MIGRATIONS_DIR", str(ident / "region_migrations"))
    mp.delenv("MOTRES_REGION_BUNDLES_DIR", raising=False)
    mp.delenv("MCP_KEYS_DIR", raising=False)
    mp.setenv("MOTRES_REGION", region)
    mp.setenv("MOTRES_PEER_URL", "http://peer.invalid:8001")
    mp.setenv("MOTRES_PEER_HMAC_SECRET", SECRET)
    return d


def _w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _jl(p: Path, *rows: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth as A
    from motor_ai_sim import auth_email as E
    from motor_ai_sim import sessions as S
    from motor_ai_sim import users as U
    from motor_ai_sim.workspace import workspace_id
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(A, "AUTH_ENFORCE", False)
    monkeypatch.setattr(A, "_ADMIN_EMAILS", set())
    monkeypatch.setattr(A, "_reject_seen", {})
    monkeypatch.setattr(S, "_last_touch", {})
    E.reset_limits()
    P.reset_nonces()
    e = {"U": U, "S": S, "E": E, "tmp": tmp_path, "mp": monkeypatch}
    _server(e, "us")
    eu = _server(e, "eu")
    for em, role in ((BUYER, "user"), (OTHER, "user"), (STAFF, "engineer")):
        U.create_user(em, PW, role=role, name=em.split("@")[0])
    RS.set_home_region(BUYER, "us", by="admin", reason="test")
    sh, ws = eu / "shared", workspace_id(BUYER)
    e["ws"] = ws
    _w(eu / "workspaces" / ws / "motor_config.yaml", "rpm: 3000\n")
    _w(eu / "workspaces" / ws / "results" / "r1.json", '{"T": 1.5}')
    _w(eu / "workspaces" / workspace_id(OTHER) / "motor_config.yaml", "rpm: 1\n")
    _w(eu / "published" / ws / "pub.json", '{"p": 1}')
    _w(sh / "projects" / "p-one" / "project.json", json.dumps({
        "id": "p-one", "title": "One", "members": [{"email": BUYER, "role": "owner"}],
        "linked_motors": [{"die": "DIEA", "config": "c1"}]}))
    _w(sh / "projects" / "p-two" / "project.json", json.dumps({
        "id": "p-two", "title": "Two", "members": [{"email": OTHER, "role": "owner"}, {"email": BUYER, "role": "viewer"}],
        "linked_motors": [{"die": "DIEB", "config": "c1"}]}))
    _w(sh / "dies" / "DIEA" / "die.yaml", "# the die\nname: DIEA\n")
    _w(sh / "dies" / "DIEA" / "c1.yaml", "die: DIEA\n")
    _w(sh / "dies" / "DIEA buyer" / "die.yaml", "name: DIEA buyer\nderived_from:\n  die: DIEA\n  config: c1\n")
    _w(sh / "dies" / "DIEB" / "die.yaml", "name: DIEB\n")
    _w(sh / "production" / "DIEA" / "c1" / "production.json", '{"die": "DIEA"}')
    _w(sh / "motor_tests" / "DIEA_buyer" / "c1" / "tests.json", '{"die": "DIEA buyer"}')
    _w(sh / "cad" / "DIEA" / "c1" / "manifest.json", '{"die": "DIEA"}')
    _w(sh / ".duty_results.json", json.dumps({"version": 2, "results": {"DIEA": {"c1": {"S1": {}}}, "DIEB": {"c1": {}}},
                                              "history": {}}))
    _w(eu / "identity" / "die_access.json", json.dumps({"DIEA": {"visibility": "private", "clients": [BUYER]},
                                                       "DIEB": {"visibility": "private", "clients": []}}))
    from motor_ai_sim.verification import store as VS
    cid = VS.new_case_id()
    VS.append(cid, "case_created", {"user": BUYER, "application": {"country": "US", "legal_name": "Buyer Inc"}})
    _w(VS.documents_dir() / cid / "doc1.pdf", "%PDF-1.4 test")
    e["case"] = cid
    _jl(sh / "legal" / "nda_signatures.jsonl", {"id": "nda-1", "user": BUYER, "email": BUYER},
        {"id": "nda-2", "user": OTHER, "email": OTHER})
    _w(sh / "legal" / "nda_signed" / "nda-1.pdf", "%PDF nda")
    _jl(eu / "identity" / "terms_acceptances.jsonl", {"id": "t1", "user": BUYER}, {"id": "t2", "user": OTHER})
    _w(sh / "directory" / "people.json", json.dumps({"people": [{"id": "pe1", "emails": [BUYER]},
                                                               {"id": "pe2", "emails": [OTHER]}]}))
    _w(sh / "directory" / "companies.json", json.dumps({"companies": [{"id": "co1", "legal_name": "Buyer Inc",
                                                                       "country": "US"}]}))
    _w(sh / "directory" / "affiliations.json", json.dumps({"affiliations": [
        {"id": "af1", "person_id": "pe1", "company_id": "co1", "status": "confirmed"}]}))
    _jl(eu / "identity" / "admin_audit.jsonl", {"actor": "admin", "action": "user.update", "target": BUYER,
                                               "subject": BUYER}, {"actor": "admin", "action": "x", "target": OTHER})
    e["eu"] = eu
    yield e
    E.reset_limits()


def _bundle(env) -> Path:
    rep = MG.export(BUYER, env["tmp"] / "out", apply=True, actor="test")
    return Path(rep["bundle_dir"])


def _to_us(env, b: Path, name: str = "") -> Path:
    """What the host's rsync does: a copy of the bundle folder on the US host."""
    import shutil
    dst = env["tmp"] / "us_bundles" / (name or b.name)
    shutil.copytree(b, dst)
    return dst


def _fake_peer(env):
    """The US server's answers over the peer channel: its receipt and its directory entry (read from US's own files)."""
    def handler(req: httpx.Request):
        recdir = env["tmp"] / "us" / "identity" / "region_migrations"
        if req.url.path.endswith("/migration/receipt"):
            bid = req.url.params.get("bundle")
            p = recdir / f"{bid}.json"
            if not p.is_file():
                return httpx.Response(404, json={"detail": "Not Found"})
            return httpx.Response(200, json={"region": "us", **MG.public_receipt(json.loads(p.read_text()))})
        if req.url.path.endswith("/directory"):
            em = req.url.params.get("email")
            users = json.loads((env["tmp"] / "us" / "identity" / "users.json").read_text()) \
                if (env["tmp"] / "us" / "identity" / "users.json").is_file() else {}
            ent = {em: {"id": "x", "region": "us", "tier": "user", "created": None}} if em in users else {}
            return httpx.Response(200, json={"region": "us", "entries": ent})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


# ── the plan ─────────────────────────────────────────────────────────────────

def test_plan_takes_exactly_what_belongs_to_the_account_and_writes_nothing(env):
    before = sorted(str(p) for p in env["tmp"].rglob("*"))
    p = MG.plan(BUYER)
    assert p["blockers"] == [] and p["target_region"] == "us"
    assert p["projects"] == ["p-one"]                                      # a project it only views stays
    assert p["dies"] == ["DIEA", "DIEA buyer"]                             # linked die + the die derived from it
    rels = {f["rel"] for f in p["files"]}
    ws = env["ws"]
    assert {f"workspaces/{ws}/motor_config.yaml", f"workspaces/{ws}/results/r1.json", f"published/{ws}/pub.json",
            "projects/p-one/project.json", "dies/DIEA/die.yaml", "dies/DIEA/c1.yaml", "dies/DIEA buyer/die.yaml",
            "production/DIEA/c1/production.json", "motor_tests/DIEA_buyer/c1/tests.json", "cad/DIEA/c1/manifest.json",
            f"verification/cases/{env['case']}.json", f"verification/documents/{env['case']}/doc1.pdf",
            "legal/nda_signed/nda-1.pdf"} <= rels
    assert not any("DIEB" in r or "p-two" in r for r in rels)
    assert sorted(str(p) for p in env["tmp"].rglob("*")) == before
    assert not RS.is_frozen(env["U"].get_user(BUYER))


def test_plan_blocks(env):
    assert any("staff" in b for b in MG.plan(STAFF)["blockers"])
    assert any("home region is 'eu'" in b for b in MG.plan(OTHER)["blockers"])
    shared = env["eu"] / "shared" / "projects" / "p-two" / "project.json"
    doc = json.loads(shared.read_text())
    doc["linked_motors"].append({"die": "DIEA", "config": "c1"})
    shared.write_text(json.dumps(doc))
    assert any("also linked by project(s) that stay: p-two" in b for b in MG.plan(BUYER)["blockers"])
    assert MG.plan(BUYER, allow_shared_dies=True)["blockers"] == []
    with pytest.raises(MG.MigrationError):
        MG.export(BUYER, env["tmp"] / "out", apply=True)
    assert not RS.is_frozen(env["U"].get_user(BUYER))


def test_plan_blocks_other_writers_of_what_moves(env):
    """Other non-staff members of a moved project, and other accounts in a moved die's access list, would keep writing
    to the source copy: both block (staff members do not: they work on both servers)."""
    one = env["eu"] / "shared" / "projects" / "p-one" / "project.json"
    doc = json.loads(one.read_text())
    doc["members"].append({"email": STAFF, "role": "engineer"})
    one.write_text(json.dumps(doc))
    assert MG.plan(BUYER)["blockers"] == []                                # a staff member is fine
    doc["members"].append({"email": OTHER, "role": "editor"})
    one.write_text(json.dumps(doc))
    assert any("p-one: 1 other non-staff member" in b for b in MG.plan(BUYER)["blockers"])
    assert MG.plan(BUYER, allow_project_members=True)["blockers"] == []
    doc["members"] = doc["members"][:-1]
    one.write_text(json.dumps(doc))
    acc = env["eu"] / "identity" / "die_access.json"
    a = json.loads(acc.read_text())
    a["DIEA"]["clients"].append(OTHER)
    acc.write_text(json.dumps(a))
    assert any("die 'DIEA' is used by 1 other account" in b for b in MG.plan(BUYER)["blockers"])


def test_bundle_goes_to_the_mounted_identity_folder_by_default(env):
    rep = MG.export(BUYER, apply=True, actor="test")
    b = Path(rep["bundle_dir"])
    assert b.parent == env["eu"] / "identity" / "region_bundles"
    m = MG.load_manifest(b)
    assert m["root_hmac"] == MG.root_hmac(m["root_sha256"]) and MG.verify_bundle(b)["signature_ok"]


def test_extra_die_and_its_descendants(env):
    _w(env["eu"] / "shared" / "dies" / "DIEB v2" / "die.yaml", "name: DIEB v2\nderived_from:\n  die: DIEB\n")
    assert MG.plan(BUYER, extra_dies=["DIEB"], allow_shared_dies=True)["dies"] == ["DIEA", "DIEB", "DIEA buyer", "DIEB v2"]


# ── export / verify / import / finish / delete ──────────────────────────────

def test_export_freezes_and_the_bundle_is_hashed(env):
    b = _bundle(env)
    assert RS.is_frozen(env["U"].get_user(BUYER))
    v = MG.verify_bundle(b)
    assert v["ok"], v
    m = MG.load_manifest(b)
    f = b / "files" / "projects" / "p-one" / "project.json"
    assert m["files"]["projects/p-one/project.json"]["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
    rec = json.loads((b / "records.json").read_text())
    assert rec["user"]["pw_hash"] == env["U"].get_user(BUYER)["pw_hash"] and "region_freeze" not in rec["user"]
    assert [json.loads(x)["id"] for x in rec["terms_acceptances"]] == ["t1"]
    assert [json.loads(x)["id"] for x in rec["nda_signatures"]] == ["nda-1"]
    assert len(rec["admin_audit"]) == 1 and rec["directory"]["companies"][0]["id"] == "co1"
    assert set(rec["die_access"]) == {"DIEA"} and set(rec["duty_results"]["results"]) == {"DIEA"}
    f.write_text("tampered")
    assert not MG.verify_bundle(b)["ok"] and MG.verify_bundle(b)["bad"] == ["projects/p-one/project.json"]


def test_transfer_builds_a_checksummed_rsync(env, monkeypatch):
    b = _bundle(env)
    ran = []
    rep = MG.transfer(b, "10.77.0.2:/srv/motres/identity/region_bundles", runner=lambda c: ran.append(c) or 0)
    assert rep["dry_run"] and "--dry-run" in ran[0] and "--checksum" in ran[0]
    assert ran[0][-1] == f"10.77.0.2:/srv/motres/identity/region_bundles/{b.name}/"
    # root on both hosts: ids kept numerically, the copy owned by the container user, the target folder created 0700
    assert "--numeric-ids" in ran[0] and "--chown=10001:10001" in ran[0]
    assert "--rsync-path=install -d -m 0700 -o 10001 -g 10001 /srv/motres/identity/region_bundles && rsync" in ran[0]
    monkeypatch.setattr(MG.shutil, "which", lambda name: None)              # the API container: no rsync
    with pytest.raises(MG.MigrationError) as e:
        MG.transfer(b, "10.77.0.2:/srv/motres/identity/region_bundles", apply=True)
    assert "run on the host: rsync -a --checksum" in str(e.value)


def test_full_migration_eu_to_us(env):
    U = env["U"]
    b = _bundle(env)
    eu_rec = U.get_user(BUYER)
    _server(env, "us")
    bu = _to_us(env, b)
    dry = MG.import_bundle(bu)
    assert dry["blockers"] == [] and dry["to_install"] == len(MG.load_manifest(bu)["files"])
    assert U.get_user(BUYER) is None                                       # a dry run writes nothing
    rep = MG.import_bundle(bu, apply=True, actor="test")
    assert rep["bundle_removed"] and not bu.exists()                       # the US copy of the bundle is gone
    us = env["tmp"] / "us"
    rec = U.get_user(BUYER)
    assert rec["pw_hash"] == eu_rec["pw_hash"] and rec["home_region"] == "us"
    assert "region_guard" not in rec and "region_freeze" not in rec
    assert (us / "workspaces" / env["ws"] / "results" / "r1.json").read_text() == '{"T": 1.5}'
    assert "home_region: us" in (us / "shared" / "dies" / "DIEA" / "die.yaml").read_text()
    assert (us / "shared" / "dies" / "DIEA" / "die.yaml").read_text().startswith("# the die")   # layout kept
    assert '"t1"' in (us / "identity" / "terms_acceptances.jsonl").read_text()
    assert json.loads((us / "identity" / "die_access.json").read_text())["DIEA"]["clients"] == [BUYER]
    assert "DIEA" in json.loads((us / "shared" / ".duty_results.json").read_text())["results"]
    from motor_ai_sim.verification import store as VS
    assert [c["id"] for c in VS.cases_of(BUYER)] == [env["case"]]
    assert rep["receipt"]["root_sha256"] == MG.load_manifest(b)["root_sha256"] and rep["receipt"]["state"] == "done"
    # back on the source: an agent key of the account exists; finish needs the target's receipt
    _server(env, "eu")
    from motor_ai_sim import agent_keys as K
    K.create_key(BUYER, name="laptop")
    fin = MG.finish(BUYER, b, transport=_fake_peer(env))
    assert fin["blockers"] == [] and fin["dry_run"]
    done = MG.finish(BUYER, b, apply=True, actor="test", transport=_fake_peer(env))
    assert done["revoked"]["agent_keys"] == 1 and not any(k["active"] for k in K.list_keys(BUYER))
    rec = U.get_user(BUYER)
    assert RS.is_guarded(rec) and not RS.is_frozen(rec) and RS.effective_region(rec) == "us"
    assert "home_region: us" in (env["eu"] / "shared" / "dies" / "DIEA" / "die.yaml").read_text()
    assert BUYER in RS.load_remote_directory()["entries"]
    # the person signs in on EU and is handed to US without a second password
    r = client.post("/api/auth/login", json={"email": BUYER, "password": PW})
    assert r.status_code == 421, r.text
    ticket = r.json()["detail"]["handoff"]
    _server(env, "us")
    assert client.post("/api/auth/region-handoff", json={"ticket": ticket}).status_code == 200
    # LATER: delete the EU copy
    _server(env, "eu")
    with pytest.raises(MG.MigrationError):
        MG.delete_source(BUYER, b, apply=True, transport=_fake_peer(env))      # backup not confirmed
    # a write that reached the EU copy after the export: outside a die it blocks, inside a die that die stays whole
    ws_file = env["eu"] / "workspaces" / env["ws"] / "motor_config.yaml"
    ws_file.write_text("rpm: 4000\n")
    dry = MG.delete_source(BUYER, b, target_backup_confirmed=True, transport=_fake_peer(env))
    assert dry["changed"] == [f"workspaces/{env['ws']}/motor_config.yaml"] and dry["blockers"]
    with pytest.raises(MG.MigrationError):
        MG.delete_source(BUYER, b, apply=True, target_backup_confirmed=True, transport=_fake_peer(env))
    ws_file.write_text("rpm: 3000\n")
    (env["eu"] / "shared" / "production" / "DIEA" / "c1" / "production.json").write_text('{"die": "DIEA", "x": 2}')
    _w(env["eu"] / "shared" / "projects" / "p-three" / "project.json", json.dumps({
        "id": "p-three", "members": [{"email": OTHER, "role": "owner"}], "linked_motors": [{"die": "DIEA buyer"}]}))
    d = MG.delete_source(BUYER, b, apply=True, target_backup_confirmed=True, actor="test", transport=_fake_peer(env))
    assert set(d["kept_dies"]) == {"DIEA", "DIEA buyer"}                   # a changed file / a project that stays
    assert (env["eu"] / "shared" / "dies" / "DIEA" / "c1.yaml").is_file()
    assert (env["eu"] / "shared" / "dies" / "DIEA buyer" / "die.yaml").is_file()
    assert "DIEA" in json.loads((env["eu"] / "identity" / "die_access.json").read_text())
    assert not (env["eu"] / "workspaces" / env["ws"]).exists()
    assert not (env["eu"] / "shared" / "projects" / "p-one").exists()
    assert (env["eu"] / "shared" / "projects" / "p-two" / "project.json").is_file()
    assert (env["eu"] / "shared" / "dies" / "DIEB" / "die.yaml").is_file()
    assert U.get_user(BUYER) is None and U.get_user(OTHER) is not None
    assert d["bundle_removed"] and not b.exists()                          # the EU bundle went with the copy
    r = client.post("/api/auth/login", json={"email": BUYER, "password": PW})
    assert r.status_code == 421 and r.json()["detail"]["region"] == "us"  # the directory still sends the person on


def _imported(env) -> Path:
    b = _bundle(env)
    _server(env, "us")
    MG.import_bundle(_to_us(env, b), apply=True, actor="test")
    _server(env, "eu")
    return b


def test_finish_refuses_a_source_change_between_export_and_finish(env):
    """A write that slipped past the freeze (a staff edit of a moved project, here) makes the bundle stale."""
    b = _imported(env)
    (env["eu"] / "shared" / "projects" / "p-one" / "project.json").write_text('{"id": "p-one", "edited": true}')
    rep = MG.finish(BUYER, b, transport=_fake_peer(env))
    assert any("1 file(s) changed" in x for x in rep["blockers"]) and rep["changed"] == ["projects/p-one/project.json"]
    with pytest.raises(MG.MigrationError):
        MG.finish(BUYER, b, apply=True, transport=_fake_peer(env))
    assert not RS.is_guarded(env["U"].get_user(BUYER)) and RS.is_frozen(env["U"].get_user(BUYER))


def test_finish_refuses_a_password_or_disabled_change_during_the_freeze(env):
    b = _imported(env)
    env["U"].set_password(BUYER, "a different password 9")
    assert any("password changed" in x for x in MG.finish(BUYER, b, transport=_fake_peer(env))["blockers"])
    b2 = _imported_again(env)
    env["U"].update_user(BUYER, disabled=True)
    assert any("enabled / disabled" in x for x in MG.finish(BUYER, b2, transport=_fake_peer(env))["blockers"])


def _imported_again(env) -> Path:
    """Abort, then a fresh export of the (changed) account."""
    MG.unfreeze(BUYER, apply=True)
    return _bundle(env)


def test_import_checks_the_signature_and_refuses_staff(env):
    b = _bundle(env)
    _server(env, "us")
    bu = _to_us(env, b)
    m = json.loads((bu / "manifest.json").read_text())
    m["root_hmac"] = "0" * 64
    (bu / "manifest.json").write_text(json.dumps(m))
    rep = MG.import_bundle(bu)
    assert rep["verify"]["signature_ok"] is False and rep["blockers"]
    # a staff record in a bundle that is otherwise well formed and signed
    bu2 = _to_us(env, b, name="staff-copy")
    rec = json.loads((bu2 / "records.json").read_text())
    rec["user"]["role"] = "admin"
    raw = json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    (bu2 / "records.json").write_bytes(raw.encode("utf-8"))
    m2 = json.loads((bu2 / "manifest.json").read_text())
    m2["records_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    m2["root_sha256"] = MG.root_hash(m2)
    m2["root_hmac"] = MG.root_hmac(m2["root_sha256"])
    (bu2 / "manifest.json").write_text(json.dumps(m2))
    assert any("staff account" in x for x in MG.import_bundle(bu2)["blockers"])


def test_interrupted_import_can_run_again(env, monkeypatch):
    b = _bundle(env)
    _server(env, "us")
    bu = _to_us(env, b)

    def boom():
        raise RuntimeError("power cut")
    monkeypatch.setattr(MG, "_rebuild_indexes", boom)
    with pytest.raises(RuntimeError):
        MG.import_bundle(bu, apply=True)
    assert MG.load_receipt(b.name)["state"] == "pending" and env["U"].get_user(BUYER) is not None
    monkeypatch.undo()
    _server(env, "us")
    rep = MG.import_bundle(bu, apply=True)                                 # the account here equals the bundle's
    assert rep["receipt"]["state"] == "done" and rep["merged"]["account"] == 0
    # a DIFFERENT account record under the same address is never merged
    _server(env, "eu")
    b2 = _imported_again(env)
    _server(env, "us")
    env["U"].update_user(BUYER, name="someone else")
    assert any("already exists" in x for x in MG.import_bundle(_to_us(env, b2))["blockers"])


def test_import_refuses_conflicts_and_wrong_region(env):
    b = _bundle(env)
    with pytest.raises(MG.MigrationError):
        MG.import_bundle(b, apply=True)                                   # this is the EU server
    _server(env, "us")
    bu = _to_us(env, b)
    _w(env["tmp"] / "us" / "shared" / "dies" / "DIEA" / "die.yaml", "name: something else\n")
    rep = MG.import_bundle(bu)
    assert rep["conflicts"] == ["dies/DIEA/die.yaml"] and rep["blockers"]
    with pytest.raises(MG.MigrationError):
        MG.import_bundle(bu, apply=True)
    assert env["U"].get_user(BUYER) is None


def test_finish_refuses_without_a_matching_receipt_and_unfreeze_aborts(env):
    b = _bundle(env)
    rep = MG.finish(BUYER, b, transport=_fake_peer(env))
    assert any("no receipt" in x for x in rep["blockers"])
    with pytest.raises(MG.MigrationError):
        MG.finish(BUYER, b, apply=True, transport=_fake_peer(env))
    assert not RS.is_guarded(env["U"].get_user(BUYER))
    MG.unfreeze(BUYER, apply=True)
    assert not RS.is_frozen(env["U"].get_user(BUYER))


def test_peer_receipt_route_shows_hashes_only(env):
    b = _bundle(env)
    _server(env, "us")
    MG.import_bundle(_to_us(env, b), apply=True)
    bid = b.name
    path = f"/api/peer/v1/migration/receipt?bundle={bid}"
    h = P.sign_headers("GET", path, secret=SECRET, region="eu")
    j = client.get(path, headers=h).json()
    assert j["root_sha256"] == MG.load_manifest(b)["root_sha256"] and BUYER not in json.dumps(j)
    assert j["state"] == "done" and j["verified"] is True
    assert client.get(path).status_code == 404                            # unsigned
    h = P.sign_headers("GET", "/api/peer/v1/migration/receipt?bundle=mig-nope", secret=SECRET, region="eu")
    assert client.get("/api/peer/v1/migration/receipt?bundle=mig-nope", headers=h).status_code == 404


def test_cli_dry_run_is_the_default(env, capsys):
    spec = importlib.util.spec_from_file_location("migrate_cli", ROOT / "scripts" / "migrate_account_region.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    assert cli.main(["export", "--email", BUYER]) == 0
    out = capsys.readouterr().out
    assert "EXPORT (DRY RUN)" in out and "p-one" in out
    assert not RS.is_frozen(env["U"].get_user(BUYER))
    assert cli.main(["export", "--email", STAFF]) == 1


# ── re-review of #632: files added after the export, other accounts' jobs on the moved dies ──

def test_finish_and_delete_source_block_on_files_added_after_the_export(env):
    """An engineer's run files a new result into the workspace or a die folder after the export: it is not in the
    bundle, so finish (and later delete-source) must stop instead of orphaning or purging it."""
    b = _imported(env)
    new_ws = _w(env["eu"] / "workspaces" / env["ws"] / "results" / "r2.json", '{"T": 2.0}')
    rep = MG.finish(BUYER, b, transport=_fake_peer(env))
    assert rep["new"] == [f"workspaces/{env['ws']}/results/r2.json"] and any("1 new" in x for x in rep["blockers"])
    new_ws.unlink()
    new_die = _w(env["eu"] / "shared" / "dies" / "DIEA" / "runs" / "c1" / "late.json", "{}")
    assert MG.finish(BUYER, b, transport=_fake_peer(env))["new"] == ["dies/DIEA/runs/c1/late.json"]
    new_die.unlink()
    MG.finish(BUYER, b, apply=True, actor="test", transport=_fake_peer(env))
    _w(env["eu"] / "shared" / "production" / "DIEA" / "c1" / "late_record.json", "{}")
    d = MG.delete_source(BUYER, b, target_backup_confirmed=True, transport=_fake_peer(env))
    assert d["new"] == ["production/DIEA/c1/late_record.json"] and any("not in the bundle" in x for x in d["blockers"])
    with pytest.raises(MG.MigrationError):
        MG.delete_source(BUYER, b, apply=True, target_backup_confirmed=True, transport=_fake_peer(env))
    assert env["U"].get_user(BUYER) is not None                            # nothing purged


def test_a_job_of_any_account_on_a_moved_die_blocks(env, monkeypatch):
    """A staff engineer's run on a moved die writes into the source copy: the plan and finish count it too."""
    from motor_ai_sim import cluster_monitor as CM
    from motor_ai_sim.development import activity as ACT
    monkeypatch.setattr(CM, "jobs_view", lambda *a, **k: {"items": []})
    monkeypatch.setattr(ACT, "app_rows", lambda: [{"kind": "coupled", "state": "running", "die": "DIEA buyer",
                                                   "config": "c1"}])
    p = MG.plan(BUYER)
    assert any("on its dies (any account, staff included)" in x for x in p["blockers"])
    assert p["active_jobs"][0]["why"] == "on die DIEA buyer"
    monkeypatch.setattr(ACT, "app_rows", lambda: [{"kind": "coupled", "state": "running", "die": "DIEB"}])
    assert MG.plan(BUYER)["blockers"] == []                                 # a die that stays is not ours
    b = _imported(env)
    monkeypatch.setattr(ACT, "app_rows", lambda: [{"kind": "thermal", "state": "queued", "die": "DIEA"}])
    assert any("job(s) of the account or on its dies" in x for x in MG.finish(BUYER, b, transport=_fake_peer(env))["blockers"])
