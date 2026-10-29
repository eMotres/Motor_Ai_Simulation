"""Move / copy ONE configuration to another die (2026-10-30).

On 2026-10-29/30 the ceramic L100 variants of the Erium die had to be copied by hand
(vadim 3 -> vadim 4 / vadim 5): no route carried a configuration with its results, its
project links and its customers' access.  These tests build one synthetic configuration
referenced from every store that names "<die>/<cfg>", move it through
``POST /api/family/config/{die}/{cfg}/move`` and check that everything resolves under
the target die while the sibling configuration of the source die keeps everything it
had; that a dry run writes nothing and lists every store; that a copy leaves the source
alone; that a failure rolls back; and the refusals.

Everything lives in a throwaway tree under the pytest tmp area (layering off).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from motor_ai_sim import config_move as CM
from motor_ai_sim.routes import family as fam

SRC = "CIANO56 400_80 vadim 3"
DST = "CIANO56 400_80 vadim 4"
CFG = "L100"
SIB = "L110"
DUTY = "rated"
RODRIGO = "rodrigo@example.com"        # sees the die as a whole (die grant)
AGUSTIN = "agustin@example.com"        # holds the configuration grant a project made
SELECTED = "selected@example.com"      # on the source die's selected-client list
BYSTANDER = "bystander@example.com"
PROJECT = "erium"
ADMIN = {"is_admin": True, "user": {"email": "owner@example.com", "role": "admin"}}

GEO = {"stator_diameter": 400.0, "num_seg": 8, "num_slots_per_segment": 6,
       "num_poles_per_segment": 7, "magnet_height": 18.8, "slot_height": 19.3,
       "wire_width": 6.6, "wire_spacing_x": 0.2, "insulation_thickness": 0.3}


def _key(die, cfg):
    return f"{die.casefold()}\0{cfg.casefold()}"


def _write_json(p: Path, doc) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding="utf-8")


def _read_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _cfg(name: str, die: str = SRC) -> dict:
    return {"name": name, "die": die, "role": "motor",
            "geometry_overrides": {"motor_length": 100, "wire_width": 6.6, "wire_spacing_x": 0.2,
                                   "insulation_thickness": 0.3},
            "materials": {"magnet": "N52UH"},
            "duties": [{"name": DUTY, "mode": "motor", "current_arms": 100.0, "rpm": 3000.0,
                        "gamma_deg": 10.0, "note": "",
                        "runs": {"sine": {"payload_file": f"runs/{name}/rated/sine.json"}}}]}


def _digest(root: Path) -> dict:
    """Logical state of every file under ``root`` (the backup folder excluded)."""
    import io
    import zipfile
    out = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or ".config_moves" in p.parts or p.name.endswith(".lock"):
            continue
        rel = str(p.relative_to(root))
        raw = p.read_bytes()
        if p.suffix == ".json":
            doc = json.loads(raw.decode("utf-8"))
            if isinstance(doc, dict):
                doc.pop("updated_at", None)
            out[rel] = json.dumps(doc, sort_keys=True)
        elif p.suffix == ".yaml":
            out[rel] = json.dumps(yaml.safe_load(raw.decode("utf-8")), sort_keys=True, default=str)
        elif p.suffix == ".npz":
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                out[rel] = {n: hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist()}
        else:
            out[rel] = hashlib.sha256(raw).hexdigest()
    return out


@pytest.fixture
def world(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    from motor_ai_sim import duty_fields as DF
    from motor_ai_sim import duty_results as DR
    from motor_ai_sim import motor_access as MA
    from motor_ai_sim import users as U
    from motor_ai_sim import workspace as W
    from motor_ai_sim import cooling_options as CO
    from motor_ai_sim.routes import catalog as CAT
    from motor_ai_sim.routes import presets as PRE
    from motor_ai_sim.development import store as PS
    from motor_ai_sim.production.store import _safe_slug

    t = tmp_path
    dies = t / "dies"
    for d in (SRC, DST):
        (dies / d).mkdir(parents=True)
        (dies / d / "die.yaml").write_text(yaml.safe_dump(
            {"name": d, "locked": False, "geometry": dict(GEO)}, sort_keys=False), encoding="utf-8")
    for c in (CFG, SIB):
        (dies / SRC / f"{c}.yaml").write_text(yaml.safe_dump(_cfg(c), sort_keys=False), encoding="utf-8")
        _write_json(dies / SRC / "runs" / c / "rated" / "sine.json", {"cfg": c})
    fdir = dies / SRC / "runs" / CFG / DF._duty_stem(DUTY) / "fields"
    fdir.mkdir(parents=True)
    np.savez_compressed(fdir / "em.npz", meta=np.array(json.dumps(
        {"kind": "em", "die": SRC, "config": CFG, "duty": DUTY, "saved_at": "2026-10-29T10:00:00",
         "arrays": {"a": {"shape": [3], "dtype": "float32"}}})), a=np.arange(3, dtype="float32"))
    (dies / ".history" / SRC).mkdir(parents=True)
    (dies / ".history" / SRC / f"{CFG}.20261029-070300.yaml").write_text("name: L100\n", encoding="utf-8")
    (dies / ".history" / SRC / f"{SIB}.20261029-070300.yaml").write_text("name: L110\n", encoding="utf-8")
    # the legacy per-die ledger inside the die folder
    _write_json(dies / SRC / ".duty_results.json", {"version": 1, "results": {
        SRC: {CFG: {DUTY: {"em": {"kind": "em", "T": 1}}}, SIB: {DUTY: {"em": {"kind": "em", "T": 2}}}}}})
    monkeypatch.setattr(fam, "_DIES_DIR", dies)
    ctx = t / "ws" / ".family_context.json"
    monkeypatch.setattr(fam, "_CTX_FILE", ctx)
    _write_json(ctx, {"die": SRC, "config": CFG, "duty": DUTY, "at": "2026-10-29T10:00:00"})
    monkeypatch.setattr(fam, "may_see_die", lambda *_a, **_k: True)
    monkeypatch.setattr(fam, "_jobs_running_here", lambda: [])
    ws = t / "ws"
    monkeypatch.setattr(W, "root", lambda: ws)

    # results + history of both configurations, in the workspace ledger
    for c, tmax in ((CFG, 90.0), (SIB, 70.0)):
        assert DR.record(SRC, c, DUTY, "thermal", {"computed_at": "2026-10-28T10:00:00", "t_max": tmax})
        assert DR.record(SRC, c, DUTY, "thermal", {"computed_at": "2026-10-29T10:00:00", "t_max": tmax + 5})
    assert DR.history(SRC, CFG, DUTY, "thermal")

    monkeypatch.setattr(U, "_USERS_FILE", t / "users.json")
    _write_json(t / "users.json", {
        RODRIGO: {"role": "user", "motors": {"all": False, "dies": [SRC],
                                             "default": {"die": SRC, "config": CFG}}},
        AGUSTIN: {"role": "user", "motors": {"all": False, "dies": [],
                                             "configs": [f"{SRC}/{CFG}", f"{SRC}/{SIB}"],
                                             "sources": {f"{SRC}/{CFG}": [f"project:{PROJECT}"]}}},
        SELECTED: {"role": "user", "motors": {"all": False, "dies": []}},
        BYSTANDER: {"role": "user", "motors": {"all": False, "dies": [DST]}},
    })
    monkeypatch.setattr(MA, "_DIE_ACCESS_FILE", t / "die_access.json")
    _write_json(t / "die_access.json", {SRC: {"visibility": "selected", "clients": [SELECTED]}})

    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(t / "projects"))
    (t / "projects").mkdir()
    PS.create_project({
        "id": PROJECT, "members": [{"email": AGUSTIN, "role": "customer"}],
        "linked_motors": [{"die": SRC, "config": CFG, "status": "current"},
                          {"die": SRC, "config": SIB, "status": "previous"}],
        "motor_history": [{"die": SRC, "config": CFG, "linked_at": "2026-10-28T10:00:00"}],
        "project_motor_grants": {_key(SRC, CFG): {"grant_key": f"{SRC}/{CFG}", "emails": [AGUSTIN]},
                                 _key(SRC, SIB): {"grant_key": f"{SRC}/{SIB}", "emails": [AGUSTIN]}},
    })
    PS.save_index({"motors": {_key(SRC, CFG): {"project_id": PROJECT, "die": SRC, "config": CFG},
                              _key(SRC, SIB): {"project_id": PROJECT, "die": SRC, "config": SIB}}})
    PS.save_snapshot(PROJECT, {"id": "snapshot-1", "version": 1, "created_at": "2026-10-28T10:00:00Z",
                               "machine": {"die_name": SRC, "config_name": CFG}, "results": [],
                               "access_scope": {"kind": "catalog", "die": SRC, "config": CFG,
                                                "grant_key": f"{SRC}/{CFG}"}})
    PS.append_audit(PROJECT, AGUSTIN, "motor.link", "motor", f"{SRC}/{CFG}")

    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(t / "production"))
    _write_json(t / "production" / _safe_slug(SRC) / _safe_slug(CFG) / "production.json",
                {"die": SRC, "config": CFG})
    _write_json(t / "production" / _safe_slug(SRC) / _safe_slug(SIB) / "production.json",
                {"die": SRC, "config": SIB})
    monkeypatch.setenv("MOTRES_MOTOR_TESTS_ROOT", str(t / "motor_tests"))
    _write_json(t / "motor_tests" / _safe_slug(SRC) / _safe_slug(CFG) / "tests.json",
                {"die": SRC, "config": CFG, "tests": []})
    monkeypatch.setenv("MOTRES_CAD_ROOT", str(t / "cad"))
    _write_json(t / "cad" / SRC / CFG / "manifest.json", {"die": SRC, "config": CFG})
    from motor_ai_sim.cad import rebuild_queue as RQ
    RQ.request(t / "cad", SRC, CFG, requested_by=AGUSTIN)

    cat, pres = t / "catalog.json", t / "presets.json"
    _write_json(cat, {"tiers": [], "diameters_mm": [], "motors": [
        {"id": "c1", "name": f"{SRC} {CFG}", "die": SRC, "config": CFG},
        {"id": "c2", "name": f"{SRC} {SIB}", "die": SRC, "config": SIB}]})
    _write_json(pres, {"p1": {"id": "p1", "name": f"{SRC} {CFG}"}})
    monkeypatch.setattr(CAT, "_CATALOG_PATH", cat, raising=False)
    monkeypatch.setattr(PRE, "_PRESETS_PATH", pres, raising=False)
    _write_json(ws / ".panel_settings.json", {"thermal": {AGUSTIN: {
        "settings": {}, "motor": f"{SRC}|{CFG}",
        "by_motor": {f"{SRC}|{CFG}": {"settings": {"a": 1}}, f"{SRC}|{SIB}": {"settings": {}}}}}})
    _write_json(ws / "user_motors.json", {AGUSTIN: {"um_1": {"id": "um_1", "name": "mine",
                                                              "src": {"die": SRC, "config": CFG}}}})
    co = t / "cooling_options.yaml"
    co.write_text(yaml.safe_dump({"version": 1, "dies": {SRC: {"cooling_options": ["air"]}}}),
                  encoding="utf-8")
    monkeypatch.setattr(CO, "_FILE", co)
    linked = []
    monkeypatch.setattr(fam, "_autolink_new_config",
                        lambda die, cfg, w=None, copied_from=None: linked.append((die, cfg, copied_from)))
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(t / "admin_audit.jsonl"))
    world_linked.clear()
    world_linked.extend([linked])
    return t


world_linked: list = []


def _move(mode: str = "move", dry: bool = False, target: str = DST, w: dict = ADMIN):
    return fam.move_config(SRC, CFG, fam.ConfigMove(target_die=target, mode=mode, dry_run=dry), _w=w)


def _stores(out) -> set:
    return {c["store"] for c in out["changes"]}


def test_dry_run_writes_nothing_and_lists_every_store(world):
    before = _digest(world)
    out = _move(dry=True)
    assert out["dry_run"] is True and out["backup"] is None
    assert _digest(world) == before
    assert {"catalog", "catalog_history", "duty_fields", "duty_results", "active_context", "panel_settings",
            "my_motors", "catalog_cards", "presets", "user_grants", "projects", "project_snapshots",
            "project_index", "production", "motor_tests", "cad_models", "cad_rebuild_queue"} <= _stores(out)
    ledgers = [c for c in out["changes"] if c["store"] == "duty_results"]
    assert any(c.get("rows", {}).get("results") == 1 and c["rows"]["history"] == 1 for c in ledgers)
    grants = {c["where"]: c["change"] for c in out["changes"] if c["store"] == "user_grants"}
    assert set(grants) == {RODRIGO, AGUSTIN, SELECTED}
    assert [m["store"] for m in out["manual"]] == ["cooling_options"]
    assert out["build_check"]["target_vs_source"] == [] and out["build_check"]["stale_duties"] == {}


def test_move_retargets_every_store_and_keeps_the_sibling(world):
    from motor_ai_sim import duty_results as DR
    from motor_ai_sim import users as U
    from motor_ai_sim.development import store as PS
    from motor_ai_sim.production.store import _safe_slug
    dies = world / "dies"
    out = _move()
    assert out["ok"] and out["mode"] == "move" and out["dry_run"] is False
    # catalog
    assert not (dies / SRC / f"{CFG}.yaml").exists() and (dies / SRC / f"{SIB}.yaml").is_file()
    moved = yaml.safe_load((dies / DST / f"{CFG}.yaml").read_text(encoding="utf-8"))
    assert moved["die"] == DST and moved["duties"][0]["runs"]["sine"]["payload_file"] == f"runs/{CFG}/rated/sine.json"
    assert (dies / DST / "runs" / CFG / "rated" / "sine.json").is_file()
    assert not (dies / SRC / "runs" / CFG).exists() and (dies / SRC / "runs" / SIB).is_dir()
    from motor_ai_sim import duty_fields as DF
    npz = next((dies / DST / "runs" / CFG).rglob("*.npz"))
    assert DF.meta_die(npz) == DST
    assert (dies / ".history" / DST / f"{CFG}.20261029-070300.yaml").is_file()
    assert not (dies / ".history" / SRC / f"{CFG}.20261029-070300.yaml").exists()
    assert (dies / ".history" / SRC / f"{SIB}.20261029-070300.yaml").is_file()
    # ledgers: results AND history follow, the sibling's stay
    assert DR.get(DST, CFG)[DUTY]["thermal"]["t_max"] == 95.0
    assert DR.history(DST, CFG, DUTY, "thermal")
    assert DR.get(SRC, CFG) == {} and DR.history(SRC, CFG, DUTY, "thermal") == []
    assert DR.get(SRC, SIB)[DUTY]["thermal"]["t_max"] == 75.0
    legacy_src, legacy_dst = _read_json(dies / SRC / ".duty_results.json"), _read_json(dies / DST / ".duty_results.json")
    assert CFG not in legacy_src["results"][SRC] and SIB in legacy_src["results"][SRC]
    assert legacy_dst["results"][DST][CFG][DUTY]["em"]["T"] == 1
    # active context
    assert _read_json(world / "ws" / ".family_context.json")["die"] == DST
    # accounts: retargeted, access through the die kept as a configuration grant, defaults
    reg = _read_json(world / "users.json")
    assert sorted(reg[AGUSTIN]["motors"]["configs"]) == sorted([f"{DST}/{CFG}", f"{SRC}/{SIB}"])
    assert reg[AGUSTIN]["motors"]["sources"] == {f"{DST}/{CFG}": [f"project:{PROJECT}"]}
    assert reg[RODRIGO]["motors"]["configs"] == [f"{DST}/{CFG}"] and reg[RODRIGO]["motors"]["dies"] == [SRC]
    assert "default" not in reg[RODRIGO]["motors"]
    assert reg[SELECTED]["motors"]["configs"] == [f"{DST}/{CFG}"]
    assert "configs" not in reg[BYSTANDER]["motors"]            # sees the target die as a whole already
    from motor_ai_sim import motor_access as MA
    assert f"{DST}/{CFG}" in MA.access_for_email(RODRIGO)["configs"]
    # projects
    p = PS.get_project(PROJECT)
    assert {(m["die"], m["config"]) for m in p["linked_motors"]} == {(DST, CFG), (SRC, SIB)}
    assert p["motor_history"][0]["die"] == DST
    assert set(p["project_motor_grants"]) == {_key(DST, CFG), _key(SRC, SIB)}
    assert p["project_motor_grants"][_key(DST, CFG)]["grant_key"] == f"{DST}/{CFG}"
    assert set(PS.load_index()["motors"]) == {_key(DST, CFG), _key(SRC, SIB)}
    snap = _read_json(next((PS.path_for(PROJECT) / "snapshots").glob("*.json")))
    assert snap["machine"]["die_name"] == DST and snap["access_scope"]["grant_key"] == f"{DST}/{CFG}"
    audit = (PS.path_for(PROJECT) / "audit.jsonl").read_text(encoding="utf-8")
    assert '"motor.move"' in audit
    assert {(r["die"], r["config"]) for r in PS.motor_history(PROJECT)} >= {(DST, CFG)}
    # production / tests / CAD
    assert _read_json(world / "production" / _safe_slug(DST) / _safe_slug(CFG) / "production.json")["die"] == DST
    assert (world / "production" / _safe_slug(SRC) / _safe_slug(SIB) / "production.json").is_file()
    assert (world / "motor_tests" / _safe_slug(DST) / _safe_slug(CFG) / "tests.json").is_file()
    assert _read_json(world / "cad" / DST / CFG / "manifest.json")["die"] == DST
    from motor_ai_sim.cad import rebuild_queue as RQ
    assert RQ.read(world / "cad", DST, CFG) and not RQ.read(world / "cad", SRC, CFG)
    # cards / presets / workspace memories
    cards = {m["id"]: m for m in _read_json(world / "catalog.json")["motors"]}
    assert (cards["c1"]["die"], cards["c1"]["name"]) == (DST, f"{DST} {CFG}") and cards["c2"]["die"] == SRC
    assert _read_json(world / "presets.json")["p1"]["name"] == f"{DST} {CFG}"
    th = _read_json(world / "ws" / ".panel_settings.json")["thermal"][AGUSTIN]
    assert th["motor"] == f"{DST}|{CFG}" and set(th["by_motor"]) == {f"{DST}|{CFG}", f"{SRC}|{SIB}"}
    assert _read_json(world / "ws" / "user_motors.json")[AGUSTIN]["um_1"]["src"]["die"] == DST
    # the backup: every rewritten file and the source files, with a manifest
    backup = Path(out["backup"])
    man = _read_json(backup / "manifest.json")
    assert man["status"] == "done" and man["from"] == f"{SRC}/{CFG}" and man["to"] == f"{DST}/{CFG}"
    assert (backup / "source" / f"{CFG}.yaml").is_file() and (backup / "source" / "runs").is_dir()
    assert not any("users.json" in b["of"] for b in man["backups"])          # private: never copied
    assert U.get_stored_motor_grants(AGUSTIN)["configs"]


def test_copy_leaves_the_source_alone(world):
    from motor_ai_sim import duty_results as DR
    dies = world / "dies"
    users_before = (world / "users.json").read_bytes()
    out = _move(mode="copy")
    assert out["mode"] == "copy"
    assert (dies / SRC / f"{CFG}.yaml").is_file() and (dies / DST / f"{CFG}.yaml").is_file()
    assert (dies / SRC / "runs" / CFG).is_dir() and (dies / DST / "runs" / CFG).is_dir()
    assert DR.get(SRC, CFG)[DUTY]["thermal"]["t_max"] == 95.0
    assert DR.get(DST, CFG)[DUTY]["thermal"]["t_max"] == 95.0
    assert DR.history(DST, CFG, DUTY, "thermal") and DR.history(SRC, CFG, DUTY, "thermal")
    assert (world / "users.json").read_bytes() == users_before
    assert _read_json(world / "ws" / ".family_context.json")["die"] == SRC
    assert world_linked[0] == [(DST, CFG, SRC)]                               # projects pick it up via autolink
    assert {m["store"] for m in out["manual"]} >= {"production", "cad_models"}


def test_a_failed_step_rolls_everything_back(world, monkeypatch):
    before = _digest(world)

    def boom(ctx):
        raise RuntimeError("disk full")
    monkeypatch.setattr(CM, "GLOBAL_STEPS", CM.GLOBAL_STEPS[:-2] + (boom,))
    with pytest.raises(HTTPException) as e:
        _move()
    assert e.value.status_code == 500 and "rolled back" in e.value.detail
    assert _digest(world) == before
    man = next((world / ".config_moves").glob("*/manifest.json"))
    assert _read_json(man)["status"] == "rolled back"


def test_refusals(world, monkeypatch):
    dies = world / "dies"
    with pytest.raises(HTTPException) as e:
        _move(w={"is_admin": False, "user": {"email": AGUSTIN}})
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        _move(target=SRC)
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        _move(target="No such die")
    assert e.value.status_code == 404
    (dies / DST / f"{CFG}.yaml").write_text(yaml.safe_dump(_cfg(CFG, DST)), encoding="utf-8")
    with pytest.raises(HTTPException) as e:
        _move()
    assert e.value.status_code == 409 and "already has a configuration" in e.value.detail
    (dies / DST / f"{CFG}.yaml").unlink()
    from motor_ai_sim import motor_revisions as MR

    def held(die, cfg):
        raise HTTPException(409, detail=f"'{die} / {cfg}' is Rev A of Erium and keeps its name")
    monkeypatch.setattr(MR, "refuse_rename_config", held)
    with pytest.raises(HTTPException) as e:
        _move()
    assert e.value.status_code == 409 and "Rev A" in e.value.detail
    assert _move(mode="copy")["ok"]                                         # a copy leaves the revision alone
    monkeypatch.setattr(MR, "refuse_rename_config", lambda *_a: None)
    (dies / DST / f"{CFG}.yaml").unlink()
    import shutil
    shutil.rmtree(dies / DST / "runs")
    monkeypatch.setattr(fam, "_jobs_running_here", lambda: [{"id": "j1"}])
    with pytest.raises(HTTPException) as e:
        _move()
    assert e.value.status_code == 409 and "job" in e.value.detail


def test_build_check_names_a_different_lamination(world):
    d = yaml.safe_load((world / "dies" / DST / "die.yaml").read_text(encoding="utf-8"))
    d["geometry"]["magnet_height"] = 23.0
    (world / "dies" / DST / "die.yaml").write_text(yaml.safe_dump(d), encoding="utf-8")
    out = _move(dry=True)
    assert [x["key"] for x in out["build_check"]["target_vs_source"]] == ["magnet_height"]


def test_the_move_is_a_worker_route():
    from motor_ai_sim import roles
    assert roles.is_worker_path(f"/api/family/config/{SRC}/{CFG}/move")
    assert not roles.is_worker_path(f"/api/family/config/{SRC}/{CFG}/duplicate")


# ── with multi-user layering on (the server): shared dies, a customer's own workspace ──────────

ALICE = "alice@example.com"
OWNER = "owner@example.com"


@pytest.fixture
def layered(tmp_path, monkeypatch):
    import shutil

    from fastapi.testclient import TestClient
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    from motor_ai_sim import workspace as W
    from motor_ai_sim.api import app

    tree = tmp_path / "srv"
    shared, published, works = tree / "shared", tree / "published", tree / "workspaces"
    for d in (shared / "dies", published, works):
        d.mkdir(parents=True)
    for d in (SRC, DST):
        (shared / "dies" / d).mkdir()
        (shared / "dies" / d / "die.yaml").write_text(yaml.safe_dump(
            {"name": d, "locked": False, "geometry": dict(GEO)}, sort_keys=False), encoding="utf-8")
    for c in (CFG, SIB):
        (shared / "dies" / SRC / f"{c}.yaml").write_text(yaml.safe_dump(_cfg(c), sort_keys=False),
                                                         encoding="utf-8")
    shutil.copy2(Path(__file__).resolve().parents[1] / "config" / "motor_config.yaml",
                 shared / "motor_config.yaml")
    _write_json(shared / ".duty_results.json", {"version": 1, "results": {
        SRC: {CFG: {DUTY: {"thermal": {"t_max": 90.0}}}, SIB: {DUTY: {"thermal": {"t_max": 70.0}}}}},
        "history": {SRC: {CFG: {DUTY: {"thermal": [{"t_max": 80.0}]}}}}})
    monkeypatch.setenv("WORKSPACES_ROOT", str(works))
    monkeypatch.setenv("SHARED_ROOT", str(shared))
    monkeypatch.setenv("PUBLISHED_ROOT", str(published))
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.delitem(fam.__dict__, "_DIES_DIR", raising=False)
    monkeypatch.delitem(fam.__dict__, "_CTX_FILE", raising=False)
    fam._TREE_CACHE.clear()
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {OWNER})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "production"))
    monkeypatch.setenv("MOTRES_MOTOR_TESTS_ROOT", str(tmp_path / "motor_tests"))
    monkeypatch.setenv("MOTRES_CAD_ROOT", str(tmp_path / "cad"))
    monkeypatch.setattr("motor_ai_sim.config.DEFAULT_CONFIG_PATH",
                        str(tmp_path / "identity" / "motor_config.yaml"))
    (tmp_path / "identity").mkdir()
    U.create_user(OWNER, "password-owner", role="admin", name="Owner")
    U.create_user(ALICE, "password-alice", role="user", name="Alice")
    U.set_motor_grants(ALICE, all_motors=False, dies=[SRC])
    ws = works / W.workspace_id(ALICE)
    # the customer solved the shared L100 in her own workspace and has it loaded
    _write_json(ws / ".duty_results.json", {"version": 1, "results": {
        SRC: {CFG: {DUTY: {"coupled": {"t_max": 88.0}}}}}})
    _write_json(ws / ".family_context.json", {"die": SRC, "config": CFG, "duty": DUTY})
    yield {"client": TestClient(app), "shared": shared, "ws": ws,
           "admin": {"Authorization": f"Bearer {U.issue_token(OWNER)}"},
           "alice": {"Authorization": f"Bearer {U.issue_token(ALICE)}"}}
    fam._TREE_CACHE.clear()


def test_layered_move_of_a_shared_configuration_follows_into_every_workspace(layered):
    from motor_ai_sim import users as U
    c, shared, ws = layered["client"], layered["shared"], layered["ws"]
    url = f"/api/family/config/{SRC}/{CFG}/move"
    r = c.post(url, json={"target_die": DST}, headers=layered["alice"])
    assert r.status_code == 403, r.text
    dry = c.post(url + "?dry_run=1", json={"target_die": DST}, headers=layered["admin"])
    assert dry.status_code == 200, dry.text
    d = dry.json()
    assert d["dry_run"] is True and d["scope"] == "shared catalog"
    where = {(x["store"], Path(x["where"]).name if x["store"] == "duty_results" else x["where"])
             for x in d["changes"]}
    assert ("user_grants", ALICE) in where
    ledgers = [x for x in d["changes"] if x["store"] == "duty_results"]
    assert {Path(x["where"]).parent for x in ledgers} >= {shared, ws}
    assert (shared / "dies" / SRC / f"{CFG}.yaml").is_file()
    r = c.post(url, json={"target_die": DST}, headers=layered["admin"])
    assert r.status_code == 200, r.text
    assert (shared / "dies" / DST / f"{CFG}.yaml").is_file()
    assert not (shared / "dies" / SRC / f"{CFG}.yaml").exists()
    sl = _read_json(shared / ".duty_results.json")
    assert sl["results"][DST][CFG][DUTY]["thermal"]["t_max"] == 90.0
    assert sl["history"][DST][CFG][DUTY]["thermal"] == [{"t_max": 80.0}]
    assert CFG not in sl["results"][SRC] and SIB in sl["results"][SRC]
    assert _read_json(ws / ".duty_results.json")["results"] == {DST: {CFG: {DUTY: {"coupled": {"t_max": 88.0}}}}}
    assert _read_json(ws / ".family_context.json")["die"] == DST
    assert U.get_stored_motor_grants(ALICE)["configs"] == [f"{DST}/{CFG}"]
    assert Path(r.json()["backup"]).parent == shared / ".config_moves"
    # the customer still opens it, on the new die
    t = c.get(f"/api/family/payload/{DST}/{CFG}", headers=layered["alice"])
    assert t.status_code == 200, t.text
