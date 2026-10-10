"""OPEN / PRIVATE data sets: loader, precedence rule, fail-closed validation,
the journaled move, resume/rollback, and the customer-data boundary.

Everything runs against throw-away trees in tmp_path: two local BARE git
repositories stand in for GitHub (the fake remotes), a clone of each is the
server checkout, and the PR opener/closer are swapped for recorders.  No network.
"""
from __future__ import annotations

import gzip
import json
import logging
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim import catalog_sources as CS
from motor_ai_sim import data_publish as DP
from motor_ai_sim import data_sources as DS
from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_DEMO = _ROOT / "data" / "open" / "dies"

ADMIN = "admin@example.com"
USER = "user@example.com"

client = TestClient(app)


# ── helpers ──────────────────────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com",
                        "-c", "commit.gpgsign=false", "-c", "core.quotepath=off", *args],
                       cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr
    return r.stdout


def _write_die(root: Path, name: str, *, materials=None, device=None, tags=None,
               runs=False, owner_org="MOTRES", extra=None) -> Path:
    d = root / name
    d.mkdir(parents=True)
    doc = {"name": name, "geometry": {"stator_diameter": 50, "num_slots": 12,
                                      "num_poles": 14}}
    if owner_org:
        doc["owner_org"] = owner_org
    if tags:
        doc["tags"] = tags
    doc.update(extra or {})
    (d / "die.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    cfg = {"name": "L10", "die": name, "role": "motor",
           "duties": [{"name": "rated", "current_arms": 10.0, "rpm": 1000.0}],
           "materials": materials or {"magnet": "N52UH_150C",
                                      "stator_core": "B15AHV950M"}}
    if device:
        cfg["controller"] = {"device": device}
    (d / "L10.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    if runs:
        (d / "runs" / "L10").mkdir(parents=True)
        with gzip.open(d / "runs" / "L10" / "rated.motor.json.gz", "wb") as fh:
            fh.write(json.dumps({"torque_Nm": 1.23}).encode())
    return d


def _repo(tmp: Path, name: str, seed) -> tuple:
    bare = tmp / f"{name}.git"
    _git(tmp, "init", "--bare", "-b", "main", str(bare))
    co = tmp / f"{name}-checkout"
    _git(tmp, "clone", str(bare), str(co))
    _git(co, "checkout", "-b", "main")
    seed(co)
    _git(co, "add", "-A")
    _git(co, "commit", "-m", "seed")
    _git(co, "push", "origin", "main")
    return bare, co


@pytest.fixture(autouse=True)
def _clean_hooks(monkeypatch, tmp_path):
    monkeypatch.setattr(DP, "BEFORE_STEP", lambda step, rec: None)
    monkeypatch.setenv("MOTOR_AI_SIM_CATALOG_OVERRIDES", str(tmp_path / "overrides.yaml"))
    DS._WARNED.clear()


@pytest.fixture()
def sources(tmp_path, monkeypatch):
    def seed_open(co: Path):
        shutil.copytree(_DEMO, co / "data" / "open" / "dies")
        (co / "config").mkdir()
        shutil.copy2(_ROOT / "config" / "materials_library.yaml",
                     co / "config" / "materials_library.yaml")
        (co / "config" / "devices").mkdir()
        (co / "config" / "devices" / "PUBDEV1.yaml").write_text("part: PUBDEV1\n")

    def seed_private(co: Path):
        dies = co / "config" / "dies"
        _write_die(dies, "Real A", runs=True)
        _write_die(dies, "Real B", materials={"magnet": "SECRET_MAG"}, device="SECRETDEV")
        _write_die(dies, "Real C", tags=["customer"])
        _write_die(dies, "Real D", owner_org=None)
        _write_die(dies, "Real E", owner_org="ACME Motors")
        _write_die(dies, "Real N", extra={"nda": True})

    open_bare, open_co = _repo(tmp_path, "open", seed_open)
    priv_bare, priv_co = _repo(tmp_path, "private", seed_private)
    monkeypatch.setenv("OPEN_DATA_REPO_DIR", str(open_co))
    monkeypatch.setenv("MOTOR_AI_SIM_PRIVATE_DATA", str(priv_co))
    for k in ("OPEN_DATA_GITHUB_TOKEN", "PRIVATE_DATA_GITHUB_TOKEN",
              "OPEN_DATA_DEPLOY_KEY", "PRIVATE_DATA_DEPLOY_KEY",
              "PRIVATE_DATA_REPO_DIR", "WORKSPACES_ROOT", DS.ENV_REFERENCE_ORGS):
        monkeypatch.delenv(k, raising=False)
    return {"open_bare": open_bare, "open_co": open_co,
            "priv_bare": priv_bare, "priv_co": priv_co}


@pytest.fixture()
def api(sources, tmp_path, monkeypatch):
    from motor_ai_sim import auth, config, sessions
    from motor_ai_sim import motor_access as MA
    from motor_ai_sim import users as U

    cfg_dir = tmp_path / "cfg"
    (cfg_dir / "dies").mkdir(parents=True)
    (cfg_dir / "motor_config.yaml").write_text("{}\n")
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", str(cfg_dir / "motor_config.yaml"))
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(MA, "_DIE_ACCESS_FILE", tmp_path / "die_access.json")
    monkeypatch.setattr(sessions, "_EVENTS_FILE", tmp_path / "auth_events.jsonl")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(USER, "password-user", role="user", name="User")

    opened, closed = [], []

    def fake_pr(spec, branch, title, body):
        opened.append({"repo": spec.slug, "branch": branch, "title": title, "body": body})
        return {"repo": spec.slug, "branch": branch, "number": len(opened),
                "url": f"https://example.invalid/{spec.slug}/pull/{len(opened)}",
                "opened": True}

    def fake_close(spec, number):
        closed.append((spec.slug, number))
        return {"closed": True}

    monkeypatch.setattr(DP, "PR_OPENER", fake_pr)
    monkeypatch.setattr(DP, "PR_CLOSER", fake_close)
    return {**sources, "cfg_dir": cfg_dir, "opened": opened, "closed": closed,
            "admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
            "user": {"Authorization": f"Bearer {U.issue_token(USER)}"}}


def _preview(api, die, target="open"):
    r = client.get(f"/api/admin/dies/{die}/source/preview", params={"target": target},
                   headers=api["admin"])
    assert r.status_code == 200, r.text
    return r.json()


def _move(api, die, target="open", snapshot=None):
    snap = snapshot if snapshot is not None else _preview(api, die, target)["snapshot"]
    return client.post(f"/api/admin/dies/{die}/source",
                       json={"target": target, "confirm": True, "snapshot": snap},
                       headers=api["admin"])


def _branches(bare: Path) -> list:
    return sorted(_git(bare, "for-each-ref", "--format=%(refname:short)", "refs/heads").split())


def _merge_all(api, branch):
    for bare in (api["open_bare"], api["priv_bare"]):
        _git(bare, "update-ref", "refs/heads/main", f"refs/heads/{branch}")


def _reconcile(api):
    r = client.post("/api/admin/dies/source/reconcile", headers=api["admin"])
    assert r.status_code == 200, r.text
    return r.json()["completed"]


# ── loader + precedence rule ────────────────────────────────────────────────

def test_repo_ships_an_open_demo_set(monkeypatch):
    monkeypatch.delenv("OPEN_DATA_REPO_DIR", raising=False)
    names = sorted(p.name for p in _DEMO.iterdir() if (p / "die.yaml").is_file())
    assert len(names) >= 3
    diam = set()
    for n in names:
        d = _DEMO / n
        assert DS.move_blockers(d, DS.SOURCE_OPEN) == [], n
        diam.add(yaml.safe_load((d / "die.yaml").read_text(encoding="utf-8"))
                 ["geometry"]["stator_diameter"])
    assert len(diam) >= 3


def test_loader_open_only(monkeypatch, tmp_path):
    monkeypatch.delenv("OPEN_DATA_REPO_DIR", raising=False)
    monkeypatch.setenv("MOTOR_AI_SIM_PRIVATE_DATA", str(tmp_path / "missing"))
    s = DS.scan()
    assert s and all(e["source"] == DS.SOURCE_OPEN for e in s.values())
    assert DS.die_source("Not A Die") == DS.SOURCE_PRIVATE


def test_loader_open_plus_private(sources):
    s = DS.scan()
    assert s["Real A"]["source"] == DS.SOURCE_PRIVATE
    assert any(e["source"] == DS.SOURCE_OPEN for e in s.values())


def test_die_clash_is_an_error_not_a_silent_winner(sources, caplog):
    name = sorted(p.name for p in _DEMO.iterdir())[0]
    _write_die(sources["priv_co"] / "config" / "dies", name)
    with caplog.at_level(logging.ERROR, logger="motor_ai_sim.data_sources"):
        s = DS.scan()
    assert s[name]["clash"] and s[name]["source"] is None and s[name]["dir"] is None
    assert DS.locate(name) is None
    assert name in DS.clashes()
    assert any(name in r.getMessage() and "override" in r.getMessage() for r in caplog.records)
    with pytest.raises(DP.MoveError):
        DP.preview(name, DS.SOURCE_PRIVATE)


def test_die_clash_resolved_by_explicit_override(sources, tmp_path):
    name = sorted(p.name for p in _DEMO.iterdir())[0]
    _write_die(sources["priv_co"] / "config" / "dies", name)
    (tmp_path / "overrides.yaml").write_text(yaml.safe_dump(
        {"overrides": [{"kind": "dies", "id": name, "source": "open", "by": "t"}]}))
    e = DS.scan()[name]
    assert e["source"] == DS.SOURCE_OPEN and e["override"] and not e["clash"]
    ref = CS.die_ref(name)
    assert ref["source"] == "open" and ref["override"] and ref["revision"]


def test_corrupt_override_file_keeps_the_clash(sources, tmp_path):
    name = sorted(p.name for p in _DEMO.iterdir())[0]
    _write_die(sources["priv_co"] / "config" / "dies", name)
    (tmp_path / "overrides.yaml").write_text("overrides: [broken")
    assert DS.scan()[name]["clash"]


@pytest.fixture()
def device_sources(tmp_path, monkeypatch):
    from motor_ai_sim import workspace as W
    from motor_ai_sim.inverter import devices as D
    pub = tmp_path / "pubdev"
    pub.mkdir()
    card = sorted((_ROOT / "config" / "devices").glob("*.yaml"))[0]
    shutil.copy2(card, pub / card.name)
    priv = tmp_path / "privdata"
    (priv / "config" / "devices").mkdir(parents=True)
    shutil.copy2(card, priv / "config" / "devices" / card.name)
    monkeypatch.setattr(D, "_DIR", pub)
    monkeypatch.setattr(D, "_DEFAULT_DIR", pub)
    monkeypatch.setattr(W, "shared_root", lambda: tmp_path / "no-shared")
    monkeypatch.setenv("MOTOR_AI_SIM_PRIVATE_DATA", str(priv))
    return {"part": card.stem, "pub": pub / card.name,
            "priv": priv / "config" / "devices" / card.name}


def test_device_clash_is_loud_and_override_resolves(device_sources, tmp_path):
    from motor_ai_sim.inverter import devices as D
    part = device_sources["part"]
    assert part not in D.card_paths()
    assert part in D.card_clashes()
    with pytest.raises(D.CardError, match="more than one source"):
        D.get_device(part)
    assert any(r.get("clash") and r["part"] == part for r in D.list_devices())
    (tmp_path / "overrides.yaml").write_text(yaml.safe_dump(
        {"overrides": [{"kind": "devices", "id": part, "source": "private"}]}))
    assert D.card_paths()[part] == device_sources["priv"]
    ref = CS.device_ref(part)
    assert ref["source"] == "private" and ref["override"] is True
    assert D.get_device(part).provenance()["catalog_ref"]["source"] == "private"


def test_catalog_refs_record_id_source_and_revision(device_sources, tmp_path):
    part = device_sources["part"]
    device_sources["priv"].unlink()                       # one source only now
    cfg = {"materials": {"magnet": "N52UH_150C"}, "controller": {"device": part}}
    refs = {r["kind"]: r for r in CS.refs_for_config(cfg)}
    assert refs["devices"]["source"] == "public" and refs["devices"]["revision"]
    assert refs["materials"]["id"] == "magnet/N52UH_150C" and refs["materials"]["revision"]
    before = refs["devices"]["revision"]
    with device_sources["pub"].open("a", encoding="utf-8") as fh:
        fh.write("\n# edited\n")
    after = {r["kind"]: r for r in CS.refs_for_config(cfg)}["devices"]["revision"]
    assert after != before                                # a swap cannot hide


def test_catalog_merges_both_sources_when_process_catalog_is_empty(api):
    from motor_ai_sim.routes.family import die_names
    names = set(die_names())
    assert {"Real A", "Real B"} <= names
    assert any(n.startswith("Demo") for n in names)


def test_workspace_layers_include_sources(sources, tmp_path, monkeypatch):
    from motor_ai_sim import workspace as W
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "ws"))
    monkeypatch.delenv(DS.ENV_SOURCES, raising=False)
    assert not [l for l in W.layers() if l.source]
    monkeypatch.setenv(DS.ENV_SOURCES, "1")
    lays = W.layers()
    assert [l.source for l in lays if l.source] == [DS.SOURCE_PRIVATE, DS.SOURCE_OPEN]
    assert all(l.name == W.LAYER_SHARED for l in lays if l.source)


def test_workspace_clash_between_shared_and_private_resolves_to_none(sources, tmp_path, monkeypatch):
    from motor_ai_sim import workspace as W
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv(DS.ENV_SOURCES, "1")
    shared = tmp_path / "shared" / "dies"
    _write_die(shared, "Real A")
    monkeypatch.setattr(DS, "_shared_dies_dir", lambda: shared)
    # layers() takes prefer_shared since admin saves go to the shared catalogue
    monkeypatch.setattr(W, "layers",
                        lambda prefer_shared=None: [W.Layer(W.LAYER_SHARED, shared)]
                        + W.source_layers())
    assert DS.scan()["Real A"]["clash"]
    assert W.resolve_die_dir("Real A") is None
    assert "Real A" not in {e["name"] for e in W.iter_dies()}
    assert W.resolve_die_dir("Real B") is not None


# ── fail-closed validation + whitelist ──────────────────────────────────────

def _kinds(blockers):
    return {b["kind"] for b in blockers}


def test_corrupt_die_yaml_blocks(sources):
    d = sources["priv_co"] / "config" / "dies" / "Real A"
    (d / "die.yaml").write_text("confidential: [broken\n", encoding="utf-8")
    bl = DS.move_blockers(d, DS.SOURCE_OPEN)
    assert bl and any("die.yaml" in b["name"] or "die.yaml" in b["reason"] for b in bl)
    assert DS.move_blockers(d, DS.SOURCE_PRIVATE)       # both directions


def test_corrupt_config_and_result_block(sources):
    d = sources["priv_co"] / "config" / "dies" / "Real A"
    (d / "L10.yaml").write_text("duties: [ {name: x\n", encoding="utf-8")
    (d / "runs" / "L10" / "bad.json.gz").write_bytes(b"not gzip")
    names = {b["name"] for b in DS.move_blockers(d, DS.SOURCE_OPEN)}
    assert {"L10.yaml", "runs/L10/bad.json.gz"} <= names


def test_wrong_schema_blocks(sources):
    d = sources["priv_co"] / "config" / "dies" / "Real A"
    (d / "L10.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    assert "L10.yaml" in {b["name"] for b in DS.move_blockers(d, DS.SOURCE_PRIVATE)}


@pytest.mark.parametrize("rel,content", [
    ("notes.txt", "hello"),
    ("L10.yaml.bak-123", "x: 1"),
    ("attachments/drawing.pdf", "%PDF"),
])
def test_files_outside_the_whitelist_block(sources, rel, content):
    d = sources["priv_co"] / "config" / "dies" / "Real A"
    (d / rel).parent.mkdir(parents=True, exist_ok=True)
    (d / rel).write_text(content, encoding="utf-8")
    bl = DS.move_blockers(d, DS.SOURCE_OPEN)
    assert any(b["name"] == rel and "whitelist" in b["reason"] for b in bl)
    assert rel not in {f["path"] for f in DS.manifest(d)["files"]}


@pytest.mark.parametrize("text,what", [
    ("note: contact jane.doe@customer.example\n", "e-mail"),
    ("token: ghp_abcdefghijklmnopqrstuvwxyz0123456789\n", "GitHub token"),
    ("key: |\n  -----BEGIN RSA PRIVATE KEY-----\n", "private key"),
])
def test_secrets_and_emails_block(sources, text, what):
    d = sources["priv_co"] / "config" / "dies" / "Real A"
    (d / "L20.yaml").write_text(text, encoding="utf-8")
    bl = DS.move_blockers(d, DS.SOURCE_PRIVATE)
    assert any(b["name"] == "L20.yaml" and what in b["reason"] for b in bl), bl


def test_size_limit_blocks(sources, monkeypatch):
    d = sources["priv_co"] / "config" / "dies" / "Real A"
    monkeypatch.setattr(DS, "WHITELIST", tuple((rx, 10 if kind == "yaml" else lim, kind)
                                               for rx, lim, kind in DS.WHITELIST))
    assert any("limit" in b["reason"] for b in DS.move_blockers(d, DS.SOURCE_OPEN))


def test_symlink_blocks(sources, tmp_path):
    d = sources["priv_co"] / "config" / "dies" / "Real A"
    outside = tmp_path / "outside.yaml"
    outside.write_text("a: 1\n")
    try:
        (d / "L99.yaml").symlink_to(outside)
    except OSError:
        pytest.skip("no symlink privilege on this machine")
    assert any(b["name"] == "L99.yaml" for b in DS.move_blockers(d, DS.SOURCE_OPEN))


def test_manifest_carries_hashes_and_snapshot_binds_content(api):
    j = _preview(api, "Real A")
    files = {f["path"]: f for f in j["manifest"]["files"]}
    assert set(files) == {"die.yaml", "L10.yaml", "runs/L10/rated.motor.json.gz"}
    assert all(len(f["sha256"]) == 64 for f in files.values())
    assert j["blockers"] == [] and j["public_push"] is True
    assert "IMMEDIATELY" in j["notice"] and "not a confidentiality gate" in j["notice"]
    d = api["priv_co"] / "config" / "dies" / "Real A"
    with (d / "L10.yaml").open("a", encoding="utf-8") as fh:
        fh.write("# changed\n")
    assert _preview(api, "Real A")["snapshot"] != j["snapshot"]


# ── the boundary: MOTRES reference data only ─────────────────────────────────

@pytest.mark.parametrize("die,kind", [
    ("Real C", "customer"),            # tagged customer
    ("Real D", "ownership"),           # no owner_org declared
    ("Real E", "ownership"),           # owned by somebody else
])
@pytest.mark.parametrize("target", ["open", "private"])
def test_customer_and_foreign_dies_go_to_neither_repo(api, die, kind, target):
    if target == "private":
        # put it in the OPEN set so "private" is a real move target
        src = api["priv_co"] / "config" / "dies" / die
        shutil.copytree(src, api["open_co"] / "data" / "open" / "dies" / die)
        shutil.rmtree(src)
    pv = _preview(api, die, target)
    assert any(b["kind"] == kind or kind in b["reason"] for b in pv["blockers"]), pv["blockers"]
    r = _move(api, die, target, snapshot=pv["snapshot"])
    assert r.status_code == 409
    assert api["opened"] == []
    assert _branches(api["open_bare"]) == ["main"] and _branches(api["priv_bare"]) == ["main"]


def test_customer_tag_inside_a_configuration_blocks(sources):
    d = sources["priv_co"] / "config" / "dies" / "Real A"
    cfg = yaml.safe_load((d / "L10.yaml").read_text(encoding="utf-8"))
    cfg["duties"][0]["customer"] = "ACME"
    (d / "L10.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    assert any("customer" in b["reason"] for b in DS.move_blockers(d, DS.SOURCE_PRIVATE))


def test_nda_die_may_stay_private_but_never_go_open(api):
    assert "confidential" in _kinds(_preview(api, "Real N")["blockers"])


def test_workspace_die_cannot_be_moved(api):
    _write_die(api["cfg_dir"] / "dies", "Customer Job")
    r = client.get("/api/admin/dies/Customer Job/source/preview",
                   params={"target": "open"}, headers=api["admin"])
    assert r.status_code == 409 and "application storage" in r.json()["detail"]


def test_private_materials_and_devices_block_publication(api):
    pv = _preview(api, "Real B")
    kinds = {(b["kind"], b["name"]) for b in pv["blockers"]}
    assert {("material", "SECRET_MAG"), ("device", "SECRETDEV")} <= kinds
    r = _move(api, "Real B", snapshot=pv["snapshot"])
    assert r.status_code == 409
    assert api["opened"] == []
    assert DP.read_audit("Real B")[-1]["event"] == "move_refused"


# ── confirmation gate ────────────────────────────────────────────────────────

def test_move_needs_confirmation_and_snapshot(api):
    r = client.post("/api/admin/dies/Real A/source", json={"target": "open"},
                    headers=api["admin"])
    assert r.status_code == 422
    r = client.post("/api/admin/dies/Real A/source", json={"target": "open", "confirm": True},
                    headers=api["admin"])
    assert r.status_code == 422
    assert _move(api, "Real A", snapshot="0" * 64).status_code == 409
    assert _branches(api["open_bare"]) == ["main"]          # nothing pushed


def test_content_changed_after_preview_is_refused(api):
    snap = _preview(api, "Real A")["snapshot"]
    d = api["priv_co"] / "config" / "dies" / "Real A"
    with (d / "L10.yaml").open("a", encoding="utf-8") as fh:
        fh.write("# edited after the preview\n")
    r = _move(api, "Real A", snapshot=snap)
    assert r.status_code == 409 and "snapshot" in r.json()["detail"]["message"]
    assert _branches(api["open_bare"]) == ["main"]


def test_non_admin_cannot_move_or_preview(api):
    r = client.post("/api/admin/dies/Real A/source",
                    json={"target": "open", "confirm": True, "snapshot": "x"},
                    headers=api["user"])
    assert r.status_code == 403
    r = client.get("/api/admin/dies/Real A/source/preview",
                   params={"target": "open"}, headers=api["user"])
    assert r.status_code == 403


# ── the move, end to end against the fake remotes ───────────────────────────

def test_publish_flow_journal_and_order(api, monkeypatch):
    seen = []
    monkeypatch.setattr(DP, "BEFORE_STEP", lambda step, rec: seen.append(step))
    r = _move(api, "Real A")
    assert r.status_code == 200, r.text
    rec = r.json()
    br = rec["branch"]
    assert seen == list(DP.PLAN)                  # private first, public push last-but-PRs
    assert rec["status"] == "pending" and rec["from"] == "private" and rec["to"] == "open"
    assert all(rec["steps"][s]["status"] == "done" for s in DP.PLAN)
    assert {f["path"] for f in rec["files"]} == {"die.yaml", "L10.yaml",
                                                  "runs/L10/rated.motor.json.gz"}
    assert br in _branches(api["open_bare"]) and br in _branches(api["priv_bare"])
    assert "data/open/dies/Real A/die.yaml" not in _git(api["open_bare"], "ls-tree", "-r", "--name-only", "main")
    assert "data/open/dies/Real A/runs/L10/rated.motor.json.gz" in _git(
        api["open_bare"], "ls-tree", "-r", "--name-only", br)
    assert "config/dies/Real A/die.yaml" not in _git(api["priv_bare"], "ls-tree", "-r", "--name-only", br)
    assert "Signed-off-by:" in _git(api["open_bare"], "log", "-1", "--format=%B", br)
    assert "Signed-off-by:" in _git(api["priv_bare"], "log", "-1", "--format=%B", br)
    assert "not a confidentiality gate" in api["opened"][-1]["body"]
    rows = {d["name"]: d for d in client.get("/api/admin/dies", headers=api["admin"]).json()["dies"]}
    assert rows["Real A"]["source_pending"]["status"] == "pending"
    assert _move(api, "Real A").status_code == 409               # one active move per die
    assert _reconcile(api) == []                                 # not merged yet

    _merge_all(api, br)
    done = _reconcile(api)
    assert [d["die"] for d in done] == ["Real A"] and done[0]["status"] == "done"
    assert DS.die_source("Real A") == DS.SOURCE_OPEN
    assert (api["open_co"] / "data" / "open" / "dies" / "Real A" / "die.yaml").is_file()
    assert not (api["priv_co"] / "config" / "dies" / "Real A").exists()
    events = [e["event"] for e in DP.read_audit("Real A")]
    assert events[0] == "move_intent" and "move_requested" in events and events[-1] == "move_completed"
    assert "die_source_move" in (api["cfg_dir"].parent / "auth_events.jsonl").read_text(encoding="utf-8")


def test_withdraw_flow_open_to_private(api):
    demo = sorted(p.name for p in (api["open_co"] / "data" / "open" / "dies").iterdir())[0]
    pv = _preview(api, demo, "private")
    assert "cannot be unpublished" in pv["warning"] and pv["public_push"] is False
    r = _move(api, demo, "private", snapshot=pv["snapshot"])
    assert r.status_code == 200, r.text
    br = r.json()["branch"]
    assert br.startswith("data/withdraw-")
    assert f"config/dies/{demo}/die.yaml" in _git(api["priv_bare"], "ls-tree", "-r", "--name-only", br)


class _Boom(RuntimeError):
    pass


def _interrupt_at(monkeypatch, at):
    fired = []

    def hook(step, rec):
        if step == at and not fired:
            fired.append(step)
            raise _Boom(f"simulated crash at {step}")
    monkeypatch.setattr(DP, "BEFORE_STEP", hook)


@pytest.mark.parametrize("step", DP.PLAN)
def test_interrupted_move_resumes(api, monkeypatch, step):
    _interrupt_at(monkeypatch, step)
    r = _move(api, "Real A")
    assert r.status_code == 409
    mv = r.json()["detail"]["move"]
    assert mv["status"] == "incomplete" and mv["failed_step"] == step
    rows = {d["name"]: d for d in client.get("/api/admin/dies", headers=api["admin"]).json()["dies"]}
    assert rows["Real A"]["source_pending"]["status"] == "incomplete"
    # Before the open push step ran, nothing is in the public repository.
    if DP.PLAN.index(step) <= DP.PLAN.index("push:open"):
        assert _branches(api["open_bare"]) == ["main"]
    r = client.post(f"/api/admin/dies/source/moves/{mv['id']}/resume", headers=api["admin"])
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"
    _merge_all(api, mv["branch"])
    assert [d["die"] for d in _reconcile(api)] == ["Real A"]


@pytest.mark.parametrize("step", DP.PLAN)
def test_interrupted_move_rolls_back(api, monkeypatch, step):
    _interrupt_at(monkeypatch, step)
    mv = _move(api, "Real A").json()["detail"]["move"]
    r = client.post(f"/api/admin/dies/source/moves/{mv['id']}/rollback",
                    json={"confirm": True}, headers=api["admin"])
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rolled_back"
    assert _branches(api["open_bare"]) == ["main"] and _branches(api["priv_bare"]) == ["main"]
    for co in (api["open_co"], api["priv_co"]):
        assert mv["branch"] not in _git(co, "branch", "--list")
    # the die can be moved again afterwards
    assert _move(api, "Real A").status_code == 200


def test_resume_is_idempotent_after_a_crash_between_push_and_journal(api, monkeypatch):
    _interrupt_at(monkeypatch, "pr:private")
    mv = _move(api, "Real A").json()["detail"]["move"]
    # pretend the process died right after pushing the open branch, before the
    # journal recorded it: the step is "running" again, the branch is on the remote
    moves = DP.load_moves()
    moves[mv["id"]]["steps"]["push:open"]["status"] = "running"
    moves[mv["id"]]["status"] = "running"
    DP._save_moves(moves)
    r = client.post(f"/api/admin/dies/source/moves/{mv['id']}/resume", headers=api["admin"])
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"
    assert len([o for o in api["opened"]]) == 2


def test_failed_checkout_update_is_not_complete(api):
    rec = _move(api, "Real A").json()
    _merge_all(api, rec["branch"])
    _git(api["open_co"], "checkout", "-b", "somebody-elses-work")
    assert _reconcile(api) == []
    mv = DP.get_move(rec["id"])
    assert mv["status"] == "incomplete" and mv["failed_step"] == "ff:open"
    rows = {d["name"]: d for d in client.get("/api/admin/dies", headers=api["admin"]).json()["dies"]}
    assert rows["Real A"]["source_pending"]["status"] == "incomplete"
    # a merged move cannot be rolled back
    r = client.post(f"/api/admin/dies/source/moves/{rec['id']}/rollback",
                    json={"confirm": True}, headers=api["admin"])
    assert r.status_code == 409
    _git(api["open_co"], "checkout", "main")
    r = client.post(f"/api/admin/dies/source/moves/{rec['id']}/resume", headers=api["admin"])
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done"


def test_unreadable_journal_refuses_moves(api):
    DP.moves_file().parent.mkdir(parents=True, exist_ok=True)
    DP.moves_file().write_text("{not json", encoding="utf-8")
    assert _move(api, "Real A", snapshot="f" * 64).status_code == 409
    r = client.get("/api/admin/dies/Real A/source/preview", params={"target": "open"},
                   headers=api["admin"])
    assert r.status_code == 409 and "journal" in r.json()["detail"]
    assert _branches(api["open_bare"]) == ["main"]


def test_intent_is_written_before_any_git_action(api, monkeypatch):
    seen = {}

    def hook(step, rec):
        if step == DP.PLAN[0]:
            seen["journal"] = DP.get_move(rec["id"])
            raise _Boom("stop")
    monkeypatch.setattr(DP, "BEFORE_STEP", hook)
    _move(api, "Real A")
    j = seen["journal"]
    assert j["files"] and j["snapshot"] and j["plan"] == list(DP.PLAN)
    assert j["steps"][DP.PLAN[0]]["status"] == "running"
