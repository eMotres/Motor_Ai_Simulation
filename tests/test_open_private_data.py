"""OPEN / PRIVATE data sets: the loader, the admin move-by-PR flow, guard rails.

Everything runs against throw-away trees in tmp_path: two local BARE git
repositories stand in for GitHub (the fake remotes), a clone of each is the
server checkout, and the PR opener is swapped for a recorder.  No network.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

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
               runs=False) -> Path:
    d = root / name
    d.mkdir(parents=True)
    doc = {"name": name, "geometry": {"stator_diameter": 50, "num_slots": 12,
                                      "num_poles": 14}}
    if tags:
        doc["tags"] = tags
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
        (d / "runs" / "L10" / "rated.motor.json.gz").write_bytes(b"x")
    return d


def _repo(tmp: Path, name: str, seed) -> tuple:
    """(bare remote, server checkout) with ``seed(worktree)`` committed on main."""
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


@pytest.fixture()
def sources(tmp_path, monkeypatch):
    """Open repo (demo dies + public libraries) and private repo, both git."""
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
        _write_die(dies, "Real B", materials={"magnet": "SECRET_MAG"},
                   device="SECRETDEV")
        _write_die(dies, "Real C", tags=["customer"])

    open_bare, open_co = _repo(tmp_path, "open", seed_open)
    priv_bare, priv_co = _repo(tmp_path, "private", seed_private)
    monkeypatch.setenv("OPEN_DATA_REPO_DIR", str(open_co))
    monkeypatch.setenv("MOTOR_AI_SIM_PRIVATE_DATA", str(priv_co))
    for k in ("OPEN_DATA_GITHUB_TOKEN", "PRIVATE_DATA_GITHUB_TOKEN",
              "OPEN_DATA_DEPLOY_KEY", "PRIVATE_DATA_DEPLOY_KEY",
              "PRIVATE_DATA_REPO_DIR", "WORKSPACES_ROOT"):
        monkeypatch.delenv(k, raising=False)
    return {"open_bare": open_bare, "open_co": open_co,
            "priv_bare": priv_bare, "priv_co": priv_co}


@pytest.fixture()
def api(sources, tmp_path, monkeypatch):
    """Admin API wired to a sandbox config dir whose own catalog is EMPTY, so
    the catalog is exactly open + private."""
    from motor_ai_sim import auth, config, sessions
    from motor_ai_sim import motor_access as MA
    from motor_ai_sim import users as U

    cfg_dir = tmp_path / "cfg"
    (cfg_dir / "dies").mkdir(parents=True)
    (cfg_dir / "motor_config.yaml").write_text("{}\n")
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", str(cfg_dir / "motor_config.yaml"))
    users_file = tmp_path / "users.json"
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setattr(MA, "_DIE_ACCESS_FILE", tmp_path / "die_access.json")
    monkeypatch.setattr(sessions, "_EVENTS_FILE", tmp_path / "auth_events.jsonl")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    U.create_user(ADMIN, "password-admin", tier="admin", name="Admin")
    U.create_user(USER, "password-user", tier="free", name="User")

    opened = []

    def fake_pr(spec, branch, title, body):
        opened.append({"repo": spec.slug, "branch": branch, "title": title})
        return {"repo": spec.slug, "branch": branch, "number": len(opened),
                "url": f"https://example.invalid/{spec.slug}/pull/{len(opened)}",
                "opened": True}

    monkeypatch.setattr(DP, "PR_OPENER", fake_pr)
    return {**sources, "cfg_dir": cfg_dir, "opened": opened,
            "admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
            "user": {"Authorization": f"Bearer {U.issue_token(USER)}"}}


# ── loader ───────────────────────────────────────────────────────────────────

def test_repo_ships_an_open_demo_set():
    names = sorted(p.name for p in _DEMO.iterdir() if (p / "die.yaml").is_file())
    assert len(names) >= 3
    diam = set()
    for n in names:
        d = _DEMO / n
        assert DS.publish_blockers(d) == [], n     # public materials/devices only
        diam.add(yaml.safe_load((d / "die.yaml").read_text(encoding="utf-8"))
                 ["geometry"]["stator_diameter"])
    assert len(diam) >= 3                          # different sizes


def test_loader_open_only(monkeypatch, tmp_path):
    monkeypatch.delenv("OPEN_DATA_REPO_DIR", raising=False)
    monkeypatch.setenv("MOTOR_AI_SIM_PRIVATE_DATA", str(tmp_path / "missing"))
    s = DS.scan()
    assert s and all(e["source"] == DS.SOURCE_OPEN for e in s.values())
    assert DS.private_dies_dir() is None
    assert DS.die_source("Not A Die") == DS.SOURCE_PRIVATE   # default


def test_loader_open_plus_private(sources):
    s = DS.scan()
    assert s["Real A"]["source"] == DS.SOURCE_PRIVATE
    assert any(e["source"] == DS.SOURCE_OPEN for e in s.values())
    assert DS.die_source("Real A") == DS.SOURCE_PRIVATE


def test_clash_private_wins_with_warning(sources, caplog):
    name = sorted(p.name for p in _DEMO.iterdir())[0]
    _write_die(sources["priv_co"] / "config" / "dies", name)
    DS._WARNED.clear()
    with caplog.at_level(logging.WARNING, logger="motor_ai_sim.data_sources"):
        s = DS.scan()
    assert s[name]["source"] == DS.SOURCE_PRIVATE
    assert s[name]["shadowed"] == [DS.SOURCE_OPEN]
    assert any("BOTH" in r.getMessage() and name in r.getMessage() for r in caplog.records)
    assert name in DS.clashes()


def test_catalog_merges_both_sources_when_process_catalog_is_empty(api):
    from motor_ai_sim.routes.family import die_names
    names = set(die_names())
    assert {"Real A", "Real B"} <= names
    assert any(n.startswith("Demo") for n in names)


def test_workspace_layers_include_sources(sources, tmp_path, monkeypatch):
    from motor_ai_sim import workspace as W
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "ws"))
    monkeypatch.delenv(DS.ENV_SOURCES, raising=False)
    assert not [l for l in W.layers() if l.source]      # off unless switched on
    monkeypatch.setenv(DS.ENV_SOURCES, "1")
    lays = W.layers()
    srcs = [l.source for l in lays if l.source]
    assert srcs == [DS.SOURCE_PRIVATE, DS.SOURCE_OPEN]
    assert all(l.name == W.LAYER_SHARED for l in lays if l.source)


# ── admin list + preview ────────────────────────────────────────────────────

def test_admin_list_has_source_column(api):
    r = client.get("/api/admin/dies", headers=api["admin"])
    assert r.status_code == 200, r.text
    rows = {d["name"]: d for d in r.json()["dies"]}
    assert rows["Real A"]["source"] == "private"
    assert rows["Real A"]["source_pending"] is None
    demo = next(n for n in rows if n.startswith("Demo"))
    assert rows[demo]["source"] == "open"


def test_preview_lists_exactly_what_is_published(api):
    r = client.get("/api/admin/dies/Real A/source/preview",
                   params={"target": "open"}, headers=api["admin"])
    assert r.status_code == 200, r.text
    j = r.json()
    paths = {f["path"] for f in j["manifest"]["files"]}
    assert paths == {"die.yaml", "L10.yaml", "runs/L10/rated.motor.json.gz"}
    assert j["manifest"]["geometry"]["stator_diameter"] == 50
    assert j["manifest"]["materials"] == ["B15AHV950M", "N52UH_150C"]
    assert j["manifest"]["results"]
    assert "irreversible" in j["warning"]
    assert j["blockers"] == []


# ── guard rails ──────────────────────────────────────────────────────────────

def test_private_materials_and_devices_block_publication(api):
    r = client.get("/api/admin/dies/Real B/source/preview",
                   params={"target": "open"}, headers=api["admin"])
    kinds = {(b["kind"], b["name"]) for b in r.json()["blockers"]}
    assert kinds == {("material", "SECRET_MAG"), ("device", "SECRETDEV")}
    r = client.post("/api/admin/dies/Real B/source",
                    json={"target": "open", "confirm": True}, headers=api["admin"])
    assert r.status_code == 409
    assert {b["name"] for b in r.json()["detail"]["blockers"]} == {"SECRET_MAG", "SECRETDEV"}
    assert api["opened"] == []
    assert DP.read_audit("Real B")[-1]["event"] == "move_refused"


def test_customer_die_can_never_go_open(api):
    r = client.post("/api/admin/dies/Real C/source",
                    json={"target": "open", "confirm": True}, headers=api["admin"])
    assert r.status_code == 409
    assert r.json()["detail"]["blockers"][0]["kind"] == "confidential"


def test_move_needs_confirmation(api):
    r = client.post("/api/admin/dies/Real A/source", json={"target": "open"},
                    headers=api["admin"])
    assert r.status_code == 422


def test_non_admin_cannot_move_or_preview(api):
    r = client.post("/api/admin/dies/Real A/source",
                    json={"target": "open", "confirm": True}, headers=api["user"])
    assert r.status_code == 403
    r = client.get("/api/admin/dies/Real A/source/preview",
                   params={"target": "open"}, headers=api["user"])
    assert r.status_code == 403


# ── the move, end to end against the fake remotes ───────────────────────────

def _branches(bare: Path) -> list:
    out = _git(bare, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return sorted(out.split())


def test_publish_flow_by_pull_request(api):
    r = client.post("/api/admin/dies/Real A/source",
                    json={"target": "open", "confirm": True}, headers=api["admin"])
    assert r.status_code == 200, r.text
    rec = r.json()
    br = rec["branch"]
    assert rec["status"] == "pending" and rec["from"] == "private" and rec["to"] == "open"

    # A branch in EACH repository, main untouched in both.
    assert br in _branches(api["open_bare"]) and br in _branches(api["priv_bare"])
    ls_main = _git(api["open_bare"], "ls-tree", "-r", "--name-only", "main")
    assert "data/open/dies/Real A/die.yaml" not in ls_main
    ls_br = _git(api["open_bare"], "ls-tree", "-r", "--name-only", br)
    assert "data/open/dies/Real A/runs/L10/rated.motor.json.gz" in ls_br
    ls_pbr = _git(api["priv_bare"], "ls-tree", "-r", "--name-only", br)
    assert "config/dies/Real A/die.yaml" not in ls_pbr
    # Signed off.
    assert "Signed-off-by:" in _git(api["open_bare"], "log", "-1", "--format=%B", br)
    assert "Signed-off-by:" in _git(api["priv_bare"], "log", "-1", "--format=%B", br)
    # Two PRs opened, pending shown in the list, and the server checkout the
    # loader reads is unchanged until merge.
    assert {o["repo"] for o in api["opened"]} == {"eMotres/Motor_Ai_Simulation",
                                                  "eMotres/motor-ai-sim-private"}
    rows = {d["name"]: d for d in client.get("/api/admin/dies", headers=api["admin"]).json()["dies"]}
    assert rows["Real A"]["source_pending"]["to"] == "open"
    assert rows["Real A"]["source"] == "private"
    # A second move while pending is refused.
    r2 = client.post("/api/admin/dies/Real A/source",
                     json={"target": "open", "confirm": True}, headers=api["admin"])
    assert r2.status_code == 409

    # Nothing merged yet → reconcile changes nothing.
    assert client.post("/api/admin/dies/source/reconcile", headers=api["admin"]).json()["completed"] == []

    # A human merges both PRs (fast-forward main to the branch on each remote).
    for bare in (api["open_bare"], api["priv_bare"]):
        _git(bare, "update-ref", "refs/heads/main", f"refs/heads/{br}")
    done = client.post("/api/admin/dies/source/reconcile", headers=api["admin"]).json()["completed"]
    assert [d["die"] for d in done] == ["Real A"]
    assert DS.die_source("Real A") == DS.SOURCE_OPEN
    assert (api["open_co"] / "data" / "open" / "dies" / "Real A" / "die.yaml").is_file()
    assert not (api["priv_co"] / "config" / "dies" / "Real A").exists()

    events = [e["event"] for e in DP.read_audit("Real A")]
    # the refused second attempt is audited too
    assert events == ["move_requested", "move_failed", "move_completed"]
    audit_log = (api["cfg_dir"].parent / "auth_events.jsonl").read_text(encoding="utf-8")
    assert "die_source_move" in audit_log


def test_withdraw_flow_open_to_private(api):
    demo = sorted(p.name for p in (api["open_co"] / "data" / "open" / "dies").iterdir())[0]
    pv = client.get(f"/api/admin/dies/{demo}/source/preview",
                    params={"target": "private"}, headers=api["admin"]).json()
    assert "cannot be unpublished" in pv["warning"]
    r = client.post(f"/api/admin/dies/{demo}/source",
                    json={"target": "private", "confirm": True}, headers=api["admin"])
    assert r.status_code == 200, r.text
    br = r.json()["branch"]
    assert br.startswith("data/withdraw-")
    ls = _git(api["priv_bare"], "ls-tree", "-r", "--name-only", br)
    assert f"config/dies/{demo}/die.yaml" in ls
