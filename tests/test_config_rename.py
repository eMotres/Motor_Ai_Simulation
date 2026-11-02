"""RENAME one configuration as a safe transaction (2026-11-02, ``config_move.py`` rename mode).

``PATCH /api/family/config/{die}/{cfg}`` used to move only the YAML file, ``runs/<cfg>/``, the duties and the
active context: everything else keyed by the PAIR "<die>/<cfg>" (account grants, project links / history /
grant ledger / index / snapshots, the result ledgers, production, the cost estimate's target motor, the revision
records ...) was orphaned.  These tests build one synthetic configuration referenced from every store that names
it, rename it through ``POST /api/family/config/{die}/{cfg}/rename`` (and the PATCH, which now runs the same
transaction) and check that everything resolves under the new name while the sibling keeps what it had; that a
dry run writes nothing and lists every store; that a failing step rolls everything back; and the refusals.

Everything lives in a throwaway tree under the pytest tmp area.  Synthetic names only.
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

DIE = "TESTDIE 400_90 v1"
CFG = "L90-2x3ph-Al2O3-15t"
NEW = "L90-2x3ph"
SIB = "L100"
DUTY = "rated"
CUSTOMER = "customer@example.com"      # holds the configuration grant a project made, default motor elsewhere
WHOLE = "whole@example.com"            # sees the die as a whole, default motor = this configuration
BYSTANDER = "bystander@example.com"
PROJECT = "rename-proj"
LINEAGE = "testdie-l90"
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


def _cfg(name: str) -> dict:
    return {"name": name, "die": DIE, "role": "motor",
            "geometry_overrides": {"motor_length": 90, "wire_width": 6.6, "wire_spacing_x": 0.2,
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
    from motor_ai_sim import motor_revisions as MR
    from motor_ai_sim import users as U
    from motor_ai_sim import workspace as W
    from motor_ai_sim.routes import catalog as CAT
    from motor_ai_sim.routes import presets as PRE
    from motor_ai_sim.development import store as PS
    from motor_ai_sim.production.store import _safe_slug

    t = tmp_path
    dies = t / "dies"
    (dies / DIE).mkdir(parents=True)
    (dies / DIE / "die.yaml").write_text(yaml.safe_dump(
        {"name": DIE, "locked": False, "geometry": dict(GEO)}, sort_keys=False), encoding="utf-8")
    for c in (CFG, SIB):
        (dies / DIE / f"{c}.yaml").write_text(yaml.safe_dump(_cfg(c), sort_keys=False), encoding="utf-8")
        _write_json(dies / DIE / "runs" / c / "rated" / "sine.json", {"cfg": c})
    fdir = dies / DIE / "runs" / CFG / DF._duty_stem(DUTY) / "fields"
    fdir.mkdir(parents=True)
    np.savez_compressed(fdir / "em.npz", meta=np.array(json.dumps(
        {"kind": "em", "die": DIE, "config": CFG, "duty": DUTY, "saved_at": "2026-11-01T10:00:00",
         "arrays": {"a": {"shape": [3], "dtype": "float32"}}})), a=np.arange(3, dtype="float32"))
    (dies / ".history" / DIE).mkdir(parents=True)
    (dies / ".history" / DIE / f"{CFG}.20261101-070300.yaml").write_text(f"name: {CFG}\n", encoding="utf-8")
    (dies / ".history" / DIE / f"{SIB}.20261101-070300.yaml").write_text(f"name: {SIB}\n", encoding="utf-8")
    _write_json(dies / DIE / ".duty_results.json", {"version": 1, "results": {
        DIE: {CFG: {DUTY: {"em": {"kind": "em", "T": 1}}}, SIB: {DUTY: {"em": {"kind": "em", "T": 2}}}}}})
    monkeypatch.setattr(fam, "_DIES_DIR", dies)
    ws = t / "ws"
    ctx = ws / ".family_context.json"
    monkeypatch.setattr(fam, "_CTX_FILE", ctx)
    _write_json(ctx, {"die": DIE, "config": CFG, "duty": DUTY, "at": "2026-11-01T10:00:00",
                      "released_from": DIE, "released_config": CFG})
    monkeypatch.setattr(fam, "may_see_die", lambda *_a, **_k: True)
    monkeypatch.setattr(fam, "_jobs_running_here", lambda: [])
    monkeypatch.setattr(W, "root", lambda: ws)

    for c, tmax in ((CFG, 90.0), (SIB, 70.0)):
        assert DR.record(DIE, c, DUTY, "thermal", {"computed_at": "2026-10-31T10:00:00", "t_max": tmax})
        assert DR.record(DIE, c, DUTY, "thermal", {"computed_at": "2026-11-01T10:00:00", "t_max": tmax + 5})

    monkeypatch.setattr(U, "_USERS_FILE", t / "users.json")
    _write_json(t / "users.json", {
        CUSTOMER: {"role": "user", "motors": {"all": False, "dies": [],
                                              "configs": [f"{DIE}/{CFG}", f"{DIE}/{SIB}"],
                                              "sources": {f"{DIE}/{CFG}": [f"project:{PROJECT}"]}}},
        WHOLE: {"role": "user", "motors": {"all": False, "dies": [DIE], "default": {"die": DIE, "config": CFG}}},
        BYSTANDER: {"role": "user", "motors": {"all": False, "dies": [], "configs": [f"{DIE}/{SIB}"]}},
    })
    monkeypatch.setattr(MA, "_DIE_ACCESS_FILE", t / "die_access.json")
    _write_json(t / "die_access.json", {DIE: {"visibility": "selected", "clients": [WHOLE]}})

    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(t / "projects"))
    (t / "projects").mkdir()
    PS.create_project({
        "id": PROJECT, "members": [{"email": CUSTOMER, "role": "customer"}],
        "linked_motors": [{"die": DIE, "config": CFG, "status": "current"},
                          {"die": DIE, "config": SIB, "status": "previous"}],
        "motor_history": [{"die": DIE, "config": CFG, "linked_at": "2026-10-31T10:00:00"}],
        "project_motor_grants": {_key(DIE, CFG): {"grant_key": f"{DIE}/{CFG}", "emails": [CUSTOMER]},
                                 _key(DIE, SIB): {"grant_key": f"{DIE}/{SIB}", "emails": [CUSTOMER]}},
    })
    PS.save_index({"motors": {_key(DIE, CFG): {"project_id": PROJECT, "die": DIE, "config": CFG},
                              _key(DIE, SIB): {"project_id": PROJECT, "die": DIE, "config": SIB}}})
    PS.save_snapshot(PROJECT, {"id": "snapshot-1", "version": 1, "created_at": "2026-10-31T10:00:00Z",
                               "machine": {"die_name": DIE, "config_name": CFG}, "results": [],
                               "access_scope": {"kind": "catalog", "die": DIE, "config": CFG,
                                                "grant_key": f"{DIE}/{CFG}"}})
    # the project's live cost estimate prices this configuration
    _write_json(PS.path_for(PROJECT) / "cost_estimate" / "estimate.json", {
        "version": 2, "schema_version": 1, "project_id": PROJECT, "revision": 3,
        "settings": {"complex": False, "iterations": 1, "currency": "EUR", "contingency_pct": 10.0,
                     "target_motor": {"die": DIE, "config": CFG}},
        "iterations": [], "migration": None, "updated_at": None, "updated_by": None})

    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(t / "production"))
    _write_json(t / "production" / _safe_slug(DIE) / _safe_slug(CFG) / "production.json",
                {"die": DIE, "config": CFG, "title": {"en": "Test motor"}})
    _write_json(t / "production" / _safe_slug(DIE) / _safe_slug(SIB) / "production.json",
                {"die": DIE, "config": SIB})
    monkeypatch.setenv("MOTRES_MOTOR_TESTS_ROOT", str(t / "motor_tests"))
    _write_json(t / "motor_tests" / _safe_slug(DIE) / _safe_slug(CFG) / "tests.json",
                {"die": DIE, "config": CFG, "tests": []})
    monkeypatch.setenv("MOTRES_CAD_ROOT", str(t / "cad"))
    _write_json(t / "cad" / DIE / CFG / "manifest.json", {"die": DIE, "config": CFG})
    from motor_ai_sim.cad import rebuild_queue as RQ
    RQ.request(t / "cad", DIE, CFG, requested_by=CUSTOMER)

    # the WORKING product revision (Rev A, not frozen) of this configuration, its snapshot intact
    monkeypatch.setenv("MOTRES_MOTOR_REVISIONS_ROOT", str(t / "revisions"))
    snap = t / "revisions" / "snapshots" / LINEAGE / "A"
    snap.mkdir(parents=True)
    (snap / "die.yaml").write_bytes((dies / DIE / "die.yaml").read_bytes())
    (snap / "config.yaml").write_bytes((dies / DIE / f"{CFG}.yaml").read_bytes())
    _write_json(t / "revisions" / "revisions.json", {"lineages": {LINEAGE: {
        "id": LINEAGE, "title": MR._title(DIE, CFG), "created_at": "2026-10-31T10:00:00+00:00",
        "created_by": "owner@example.com", "revisions": [{
            "rev": "A", "die": DIE, "config": CFG, "created_at": "2026-10-31T10:00:00+00:00",
            "created_by": "owner@example.com", "reason": "", "agreed_with_client": True, "project_id": PROJECT,
            "frozen": False, "frozen_at": None, "parent_rev": None, "new_die": False,
            "snapshot": {"taken_at": "2026-10-31T10:00:00+00:00", "die_sha256": MR._sha(snap / "die.yaml"),
                         "config_sha256": MR._sha(snap / "config.yaml")}}]}}})

    cat, pres = t / "catalog.json", t / "presets.json"
    _write_json(cat, {"tiers": [], "diameters_mm": [], "motors": [
        {"id": "c1", "name": f"{DIE} {CFG}", "die": DIE, "config": CFG},
        {"id": "c2", "name": f"{DIE} {SIB}", "die": DIE, "config": SIB}]})
    _write_json(pres, {"p1": {"id": "p1", "name": f"{DIE} {CFG}"}})
    monkeypatch.setattr(CAT, "_CATALOG_PATH", cat, raising=False)
    monkeypatch.setattr(PRE, "_PRESETS_PATH", pres, raising=False)
    _write_json(ws / ".panel_settings.json", {"thermal": {CUSTOMER: {
        "settings": {}, "motor": f"{DIE}|{CFG}",
        "by_motor": {f"{DIE}|{CFG}": {"settings": {"a": 1}}, f"{DIE}|{SIB}": {"settings": {}}}}}})
    _write_json(ws / "user_motors.json", {CUSTOMER: {"um_1": {"id": "um_1", "name": "mine",
                                                              "src": {"die": DIE, "config": CFG}}}})
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(t / "admin_audit.jsonl"))
    return t


def _rename(name: str = NEW, dry: bool = False, w: dict = ADMIN, cfg: str = CFG):
    return fam.rename_config_safe(DIE, cfg, fam.ConfigRename(name=name), dry_run=dry, _w=w)


def _stores(out) -> set:
    return {c["store"] for c in out["changes"]}


def test_dry_run_writes_nothing_and_lists_every_store(world):
    before = _digest(world)
    out = _rename(dry=True)
    assert out["dry_run"] is True and out["backup"] is None and out["mode"] == "rename"
    assert out["to"] == {"die": DIE, "config": NEW}
    assert _digest(world) == before
    assert {"catalog", "catalog_history", "duty_fields", "duty_results", "active_context", "panel_settings",
            "my_motors", "catalog_cards", "presets", "user_grants", "projects", "project_snapshots",
            "project_index", "cost_estimate", "production", "motor_tests", "motor_revisions", "cad_models",
            "cad_rebuild_queue"} <= _stores(out)
    ledgers = [c for c in out["changes"] if c["store"] == "duty_results"]
    assert any(c.get("rows", {}).get("results") == 1 and c["rows"]["history"] == 1 for c in ledgers)
    grants = {c["where"] for c in out["changes"] if c["store"] == "user_grants"}
    assert grants == {CUSTOMER, WHOLE}                       # the bystander only holds the sibling
    revs = [c for c in out["changes"] if c["store"] == "motor_revisions"]
    assert len(revs) == 1 and "snapshot re-taken" in revs[0]["change"] and "lineage title" in revs[0]["change"]
    assert out["manual"] == []


def test_rename_retargets_every_store_and_keeps_the_sibling(world):
    from motor_ai_sim import duty_fields as DF
    from motor_ai_sim import duty_results as DR
    from motor_ai_sim import motor_access as MA
    from motor_ai_sim import motor_revisions as MR
    from motor_ai_sim.cad import rebuild_queue as RQ
    from motor_ai_sim.development import cost_estimate_store as CS
    from motor_ai_sim.development import store as PS
    from motor_ai_sim.production.store import _safe_slug
    dies = world / "dies"
    out = _rename()
    assert out["ok"] and out["mode"] == "rename" and out["dry_run"] is False and out["config"] == NEW
    # catalog: the file, its name, the stored runs and their paths, the saved versions, the field meta
    assert not (dies / DIE / f"{CFG}.yaml").exists() and (dies / DIE / f"{SIB}.yaml").is_file()
    doc = yaml.safe_load((dies / DIE / f"{NEW}.yaml").read_text(encoding="utf-8"))
    assert doc["name"] == NEW and doc["die"] == DIE
    assert doc["duties"][0]["runs"]["sine"]["payload_file"] == f"runs/{NEW}/rated/sine.json"
    assert (dies / DIE / "runs" / NEW / "rated" / "sine.json").is_file()
    assert not (dies / DIE / "runs" / CFG).exists() and (dies / DIE / "runs" / SIB).is_dir()
    npz = next((dies / DIE / "runs" / NEW).rglob("*.npz"))
    assert DF.meta_field(npz, "config") == NEW and DF.meta_die(npz) == DIE
    assert (dies / ".history" / DIE / f"{NEW}.20261101-070300.yaml").is_file()
    assert not (dies / ".history" / DIE / f"{CFG}.20261101-070300.yaml").exists()
    assert (dies / ".history" / DIE / f"{SIB}.20261101-070300.yaml").is_file()
    # the result ledgers: results AND history follow, the sibling's stay
    assert DR.get(DIE, NEW)[DUTY]["thermal"]["t_max"] == 95.0
    assert DR.history(DIE, NEW, DUTY, "thermal")
    assert DR.get(DIE, CFG) == {} and DR.history(DIE, CFG, DUTY, "thermal") == []
    assert DR.get(DIE, SIB)[DUTY]["thermal"]["t_max"] == 75.0
    legacy = _read_json(dies / DIE / ".duty_results.json")["results"][DIE]
    assert CFG not in legacy and legacy[NEW][DUTY]["em"]["T"] == 1 and SIB in legacy
    # the active context
    c = _read_json(world / "ws" / ".family_context.json")
    assert (c["die"], c["config"], c["released_config"]) == (DIE, NEW, NEW)
    # accounts: the configuration grant, its source and the default motor follow
    reg = _read_json(world / "users.json")
    assert sorted(reg[CUSTOMER]["motors"]["configs"]) == sorted([f"{DIE}/{NEW}", f"{DIE}/{SIB}"])
    assert reg[CUSTOMER]["motors"]["sources"] == {f"{DIE}/{NEW}": [f"project:{PROJECT}"]}
    assert reg[WHOLE]["motors"]["default"] == {"die": DIE, "config": NEW} and "configs" not in reg[WHOLE]["motors"]
    assert reg[BYSTANDER]["motors"]["configs"] == [f"{DIE}/{SIB}"]
    assert f"{DIE}/{NEW}" in MA.access_for_email(CUSTOMER)["configs"]
    # the project: link, history, grant ledger, index, snapshot, audit
    p = PS.get_project(PROJECT)
    assert {(m["die"], m["config"]) for m in p["linked_motors"]} == {(DIE, NEW), (DIE, SIB)}
    assert p["motor_history"][0]["config"] == NEW
    assert set(p["project_motor_grants"]) == {_key(DIE, NEW), _key(DIE, SIB)}
    assert p["project_motor_grants"][_key(DIE, NEW)]["grant_key"] == f"{DIE}/{NEW}"
    idx = PS.load_index()["motors"]
    assert set(idx) == {_key(DIE, NEW), _key(DIE, SIB)} and idx[_key(DIE, NEW)]["config"] == NEW
    snap = _read_json(next((PS.path_for(PROJECT) / "snapshots").glob("*.json")))
    assert snap["machine"]["config_name"] == NEW and snap["access_scope"]["grant_key"] == f"{DIE}/{NEW}"
    assert '"motor.rename"' in (PS.path_for(PROJECT) / "audit.jsonl").read_text(encoding="utf-8")
    # the cost estimate's target motor, with a new revision (a stale editor gets a conflict) and its own audit line
    est = CS.load(PROJECT)
    assert est["settings"]["target_motor"] == {"die": DIE, "config": NEW} and est["revision"] == 4
    assert any(a["action"] == "target_motor.follow" for a in CS.read_audit(PROJECT))
    # production: folder and record keys
    prod = world / "production" / _safe_slug(DIE) / _safe_slug(NEW) / "production.json"
    assert _read_json(prod)["config"] == NEW and _read_json(prod)["title"] == {"en": "Test motor"}
    assert not (world / "production" / _safe_slug(DIE) / _safe_slug(CFG)).exists()
    assert _read_json(world / "production" / _safe_slug(DIE) / _safe_slug(SIB) / "production.json")["config"] == SIB
    assert "config.rename" in (prod.parent / "audit.jsonl").read_text(encoding="utf-8")
    # tests, CAD, the rebuild queue
    assert _read_json(world / "motor_tests" / _safe_slug(DIE) / _safe_slug(NEW) / "tests.json")["config"] == NEW
    assert _read_json(world / "cad" / DIE / NEW / "manifest.json")["config"] == NEW
    assert RQ.read(world / "cad", DIE, NEW)["config"] == NEW and not RQ.read(world / "cad", DIE, CFG)
    # the revision record follows; its snapshot was re-taken (only the name differs), so it still reads intact
    lin, rev = MR.find(DIE, NEW)
    assert rev["rev"] == "A" and lin["title"] == MR._title(DIE, NEW) and MR.find(DIE, CFG) is None
    assert rev["renamed"][0]["from"] == f"{DIE}/{CFG}" and MR._intact(rev) is True
    # cards, presets, workspace memories
    cards = {m["id"]: m for m in _read_json(world / "catalog.json")["motors"]}
    assert (cards["c1"]["config"], cards["c1"]["name"]) == (NEW, f"{DIE} {NEW}") and cards["c2"]["config"] == SIB
    assert _read_json(world / "presets.json")["p1"]["name"] == f"{DIE} {NEW}"
    th = _read_json(world / "ws" / ".panel_settings.json")["thermal"][CUSTOMER]
    assert th["motor"] == f"{DIE}|{NEW}" and set(th["by_motor"]) == {f"{DIE}|{NEW}", f"{DIE}|{SIB}"}
    assert _read_json(world / "ws" / "user_motors.json")[CUSTOMER]["um_1"]["src"] == {"die": DIE, "config": NEW}
    # the backup folder and the admin audit
    backup = Path(out["backup"])
    man = _read_json(backup / "manifest.json")
    assert (man["status"], man["mode"], man["from"], man["to"]) == ("done", "rename", f"{DIE}/{CFG}", f"{DIE}/{NEW}")
    assert (backup / "source" / f"{CFG}.yaml").is_file() and (backup / "source" / "runs").is_dir()
    assert not any("users.json" in b["of"] for b in man["backups"])
    audit = [json.loads(x) for x in (world / "admin_audit.jsonl").read_text(encoding="utf-8").splitlines() if x]
    assert any(a.get("action") == "config.rename" for a in audit)


def test_the_patch_runs_the_same_transaction(world):
    from motor_ai_sim.development import store as PS
    r = fam.rename_config(DIE, CFG, fam.ConfigRename(name=NEW), _w=ADMIN)
    assert r["ok"] and r["config"] == NEW and r["mode"] == "rename" and "user_grants" in _stores(r)
    assert {(m["die"], m["config"]) for m in PS.get_project(PROJECT)["linked_motors"]} == {(DIE, NEW), (DIE, SIB)}
    assert fam.rename_config(DIE, NEW, fam.ConfigRename(name=NEW), _w=ADMIN)["changes"] == []   # same name: no-op


def test_a_failing_step_rolls_everything_back(world, monkeypatch):
    before = _digest(world)

    def boom(ctx):
        raise RuntimeError("disk full")
    # fails after the grants, the projects, the cost estimate, production, tests and the revision records were rewritten
    steps = list(CM.GLOBAL_STEPS)
    steps.insert(steps.index(CM.step_motor_revisions) + 1, boom)
    monkeypatch.setattr(CM, "GLOBAL_STEPS", tuple(steps))
    with pytest.raises(HTTPException) as e:
        _rename()
    assert e.value.status_code == 500 and "rolled back" in e.value.detail
    assert _digest(world) == before
    man = _read_json(next((world / ".config_moves").glob("*/manifest.json")))
    assert man["status"] == "rolled back" and man["mode"] == "rename"
    assert not (world / "admin_audit.jsonl").exists()


def test_name_collisions_are_409(world):
    dies = world / "dies"
    before = _digest(world)
    with pytest.raises(HTTPException) as e:
        _rename(name=SIB)
    assert e.value.status_code == 409 and "already exists" in e.value.detail
    (dies / DIE / "l90-2X3PH.yaml").write_text(yaml.safe_dump(_cfg("l90-2X3PH")), encoding="utf-8")
    with pytest.raises(HTTPException) as e:                  # the stores match names case-insensitively
        _rename()
    assert e.value.status_code == 409 and "l90-2X3PH" in e.value.detail
    (dies / DIE / "l90-2X3PH.yaml").unlink()
    with pytest.raises(HTTPException) as e:                  # rows already under the new name: refused, nothing written
        from motor_ai_sim import duty_results as DR
        assert DR.record(DIE, NEW, DUTY, "thermal", {"computed_at": "2026-11-01T11:00:00", "t_max": 1.0})
        before = _digest(world)
        _rename()
    assert e.value.status_code == 409 and NEW in e.value.detail
    assert _digest(world) == before


def test_refusals(world, monkeypatch):
    with pytest.raises(HTTPException) as e:
        _rename(w={"is_admin": False, "user": {"email": CUSTOMER}})
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        _rename(name=CFG.upper())
    assert e.value.status_code == 422 and "letter case" in e.value.detail
    with pytest.raises(HTTPException) as e:
        _rename(name="die")
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        _rename(cfg="L999")
    assert e.value.status_code == 404
    # a FROZEN revision keeps its name
    rp = world / "revisions" / "revisions.json"
    doc = _read_json(rp)
    doc["lineages"][LINEAGE]["revisions"][0]["frozen"] = True
    doc["lineages"][LINEAGE]["revisions"].append({"rev": "B", "die": DIE, "config": SIB, "frozen": False})
    _write_json(rp, doc)
    with pytest.raises(HTTPException) as e:
        _rename()
    assert e.value.status_code == 409 and "keeps its name" in e.value.detail and "Rev B" in e.value.detail
    monkeypatch.setattr(fam, "_jobs_running_here", lambda: [{"id": "j1"}])
    doc["lineages"][LINEAGE]["revisions"][0]["frozen"] = False
    doc["lineages"][LINEAGE]["revisions"].pop()
    _write_json(rp, doc)
    with pytest.raises(HTTPException) as e:
        _rename()
    assert e.value.status_code == 409 and "job" in e.value.detail


def test_the_rename_is_a_worker_route():
    from motor_ai_sim import roles
    assert roles.is_worker_path(f"/api/family/config/{DIE}/{CFG}/rename")
    assert roles.is_worker_path(f"/api/family/config/{DIE}/{CFG}/move")


# ── with multi-user layering on (the server): a shared configuration, a customer's own workspace ──────────

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
    (shared / "dies" / DIE).mkdir()
    (shared / "dies" / DIE / "die.yaml").write_text(yaml.safe_dump(
        {"name": DIE, "locked": False, "geometry": dict(GEO)}, sort_keys=False), encoding="utf-8")
    for c in (CFG, SIB):
        (shared / "dies" / DIE / f"{c}.yaml").write_text(yaml.safe_dump(_cfg(c), sort_keys=False), encoding="utf-8")
        _write_json(shared / "dies" / DIE / "runs" / c / "rated" / "sine.json", {"cfg": c})
    shutil.copy2(Path(__file__).resolve().parents[1] / "config" / "motor_config.yaml", shared / "motor_config.yaml")
    _write_json(shared / ".duty_results.json", {"version": 1, "results": {
        DIE: {CFG: {DUTY: {"thermal": {"t_max": 90.0}}}, SIB: {DUTY: {"thermal": {"t_max": 70.0}}}}},
        "history": {DIE: {CFG: {DUTY: {"thermal": [{"t_max": 80.0}]}}}}})
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
    for env, sub in (("MOTRES_PROJECTS_ROOT", "projects"), ("MOTRES_PRODUCTION_ROOT", "production"),
                     ("MOTRES_MOTOR_TESTS_ROOT", "motor_tests"), ("MOTRES_CAD_ROOT", "cad"),
                     ("MOTRES_MOTOR_REVISIONS_ROOT", "revisions")):
        monkeypatch.setenv(env, str(tmp_path / sub))
    monkeypatch.setattr("motor_ai_sim.config.DEFAULT_CONFIG_PATH", str(tmp_path / "identity" / "motor_config.yaml"))
    (tmp_path / "identity").mkdir()
    U.create_user(OWNER, "password-owner", role="admin", name="Owner")
    U.create_user(ALICE, "password-alice", role="user", name="Alice")
    U.set_motor_grants(ALICE, all_motors=False, dies=[], configs=[f"{DIE}/{CFG}", f"{DIE}/{SIB}"])
    ws = works / W.workspace_id(ALICE)
    _write_json(ws / ".duty_results.json",
                {"version": 1, "results": {DIE: {CFG: {DUTY: {"coupled": {"t_max": 88.0}}}}}})
    _write_json(ws / ".family_context.json", {"die": DIE, "config": CFG, "duty": DUTY})
    yield {"client": TestClient(app), "shared": shared, "ws": ws,
           "admin": {"Authorization": f"Bearer {U.issue_token(OWNER)}"},
           "alice": {"Authorization": f"Bearer {U.issue_token(ALICE)}"}}
    fam._TREE_CACHE.clear()


def test_layered_rename_of_a_shared_configuration_follows_into_every_workspace(layered):
    from motor_ai_sim import users as U
    c, shared, ws = layered["client"], layered["shared"], layered["ws"]
    url = f"/api/family/config/{DIE}/{CFG}/rename"
    assert c.post(url, json={"name": NEW}, headers=layered["alice"]).status_code == 403
    dry = c.post(url + "?dry_run=1", json={"name": NEW}, headers=layered["admin"])
    assert dry.status_code == 200, dry.text
    d = dry.json()
    assert d["dry_run"] is True and d["scope"] == "shared catalog" and d["mode"] == "rename"
    assert ("user_grants", ALICE) in {(x["store"], x["where"]) for x in d["changes"]}
    assert {Path(x["where"]).parent for x in d["changes"] if x["store"] == "duty_results"} >= {shared, ws}
    assert (shared / "dies" / DIE / f"{CFG}.yaml").is_file()
    r = c.post(url, json={"name": NEW}, headers=layered["admin"])
    assert r.status_code == 200, r.text
    assert (shared / "dies" / DIE / f"{NEW}.yaml").is_file() and not (shared / "dies" / DIE / f"{CFG}.yaml").exists()
    sl = _read_json(shared / ".duty_results.json")
    assert sl["results"][DIE][NEW][DUTY]["thermal"]["t_max"] == 90.0 and CFG not in sl["results"][DIE]
    assert sl["history"][DIE][NEW][DUTY]["thermal"] == [{"t_max": 80.0}]
    assert _read_json(ws / ".duty_results.json")["results"] == {DIE: {NEW: {DUTY: {"coupled": {"t_max": 88.0}}}}}
    assert _read_json(ws / ".family_context.json")["config"] == NEW
    assert sorted(U.get_stored_motor_grants(ALICE)["configs"]) == sorted([f"{DIE}/{NEW}", f"{DIE}/{SIB}"])
    assert Path(r.json()["backup"]).parent == shared / ".config_moves"
    # the customer still opens it, under the new name
    t = c.get(f"/api/family/payload/{DIE}/{NEW}", headers=layered["alice"])
    assert t.status_code == 200, t.text


def test_layered_customer_patch_renames_only_her_workspace_copy(layered):
    from motor_ai_sim import users as U
    c, shared, ws = layered["client"], layered["shared"], layered["ws"]
    shared_before = _digest(shared)
    r = c.patch(f"/api/family/config/{DIE}/{CFG}", json={"name": NEW}, headers=layered["alice"])
    assert r.status_code == 200, r.text
    assert r.json()["scope"] == "workspace" and "user_grants" not in _stores(r.json())
    mine = ws / "dies" / DIE
    doc = yaml.safe_load((mine / f"{NEW}.yaml").read_text(encoding="utf-8"))
    assert doc["name"] == NEW and doc["duties"][0]["runs"]["sine"]["payload_file"] == f"runs/{NEW}/rated/sine.json"
    assert (mine / "runs" / NEW / "rated" / "sine.json").is_file()            # copied from the shared layer
    assert _read_json(ws / ".duty_results.json")["results"] == {DIE: {NEW: {DUTY: {"coupled": {"t_max": 88.0}}}}}
    assert _read_json(ws / ".family_context.json")["config"] == NEW
    # the shared catalog - its file, its runs, its ledger - and her grants are untouched
    assert _digest(shared) == shared_before
    assert sorted(U.get_stored_motor_grants(ALICE)["configs"]) == sorted([f"{DIE}/{CFG}", f"{DIE}/{SIB}"])
    # the plan (dry run) is an admin action
    assert c.post(f"/api/family/config/{DIE}/{SIB}/rename?dry_run=1", json={"name": "x"},
                  headers=layered["alice"]).status_code == 403
