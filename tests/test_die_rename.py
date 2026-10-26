"""Safe rename of a die (owner decision 2026-10-26: names are editable, data must follow).

``PATCH /api/family/die/{die}`` used to move the folder and the configurations'
back-reference only.  Everything keyed by the die NAME was orphaned: the stored duty
results and history, the field files, the account grants, the access list, the
development projects that link the motor, the production records, the CAD models,
the catalog cards.  These tests build ONE synthetic die that is referenced from every
one of those stores, rename it, and check that everything resolves under the new name
and that nothing refers to the old one; then that a dry run changes nothing, that a
failure rolls everything back, and that a private copy in a workspace does not retarget
the shared catalog's stores.

Everything lives in a throwaway tree under the pytest tmp area.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from motor_ai_sim import die_rename as DRN
from motor_ai_sim.routes import family as fam

OLD = "TESTA 50 edited"
NEW = "TESTA 50 vadim"
CFG = "L15"
DUTY = "rated"
OTHER = "TESTB 40"
EMAIL = "client@example.com"
BYSTANDER = "bystander@example.com"
PROJECT = "alpha"

GEO = {"stator_diameter": 50, "num_seg": 2, "num_slots_per_segment": 6,
       "num_poles_per_segment": 7, "num_slots": 12, "num_poles": 14,
       "motor_length": 15, "magnet_height": 7.0, "tooth_width": 4.2}


def _key(die, cfg):
    return f"{die.casefold()}\0{cfg.casefold()}"


def _write_json(p: Path, doc) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding="utf-8")


def _tree_digest(root: Path) -> dict:
    """The LOGICAL state of every file under ``root``: parsed JSON / YAML (so a store that is
    written back with other whitespace compares equal), field files by meta and arrays, the rest
    by bytes.  ``updated_at`` stamps of the results ledger are volatile by design."""
    import io
    import zipfile
    out = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
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

    t = tmp_path
    # ── the catalog: the die, a configuration with a duty, a sibling derived from it ─────────
    dies = t / "dies"
    (dies / OLD).mkdir(parents=True)
    (dies / OLD / "die.yaml").write_text(yaml.safe_dump(
        {"name": OLD, "locked": False, "created": "2026-10-01T10:00:00", "geometry": dict(GEO)},
        sort_keys=False), encoding="utf-8")
    (dies / OLD / f"{CFG}.yaml").write_text(yaml.safe_dump({
        "name": CFG, "die": OLD, "role": "motor", "geometry_overrides": {"motor_length": 15},
        "winding": {"connection": "star"}, "materials": {"magnet": "N52UH"},
        "duties": [{"name": DUTY, "mode": "motor", "current_arms": 55.0, "rpm": 19000.0,
                    "gamma_deg": 8.0, "note": "",
                    "runs": {"sine": {"payload_file": f"runs/{CFG}/rated-1/sine.json"}}}],
    }, sort_keys=False), encoding="utf-8")
    _write_json(dies / OLD / "runs" / CFG / "rated-1" / "sine.json", {"x": 1})
    # a stored field file whose own meta names the die
    fdir = dies / OLD / "runs" / CFG / DF._duty_stem(DUTY) / "fields"
    fdir.mkdir(parents=True)
    np.savez_compressed(fdir / "em.npz", meta=np.array(json.dumps(
        {"kind": "em", "die": OLD, "config": CFG, "duty": DUTY, "saved_at": "2026-10-01T10:00:00",
         "arrays": {"a": {"shape": [3], "dtype": "float32"}}})), a=np.arange(3, dtype="float32"))
    # catalog history snapshots are filed under the die's name
    (dies / ".history" / OLD).mkdir(parents=True)
    (dies / ".history" / OLD / "die.20261001-100000.yaml").write_text("name: old\n", encoding="utf-8")
    (dies / OTHER).mkdir()
    (dies / OTHER / "die.yaml").write_text(yaml.safe_dump(
        {"name": OTHER, "locked": False, "geometry": dict(GEO),
         "derived_from": {"die": OLD, "config": CFG, "duty": DUTY}}, sort_keys=False),
        encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", dies)
    ctx = t / ".family_context.json"
    monkeypatch.setattr(fam, "_CTX_FILE", ctx)
    _write_json(ctx, {"die": OLD, "config": CFG, "duty": DUTY, "at": "2026-10-01T10:00:00"})
    monkeypatch.setattr(fam, "may_see_die", lambda *_a, **_k: True)
    ws = t / "ws"
    ws.mkdir()
    monkeypatch.setattr(W, "root", lambda: ws)

    # ── duty results + the per-kind history ───────────────────────────────────────────────────
    monkeypatch.setattr(DR, "store_path", lambda: t / ".duty_results.json")
    assert DR.record(OLD, CFG, DUTY, "thermal", {"computed_at": "2026-10-01T10:00:00", "t_max": 90.0})
    assert DR.record(OLD, CFG, DUTY, "thermal", {"computed_at": "2026-10-02T10:00:00", "t_max": 95.0})
    assert DR.history(OLD, CFG, DUTY, "thermal")             # the replaced row is kept
    assert DR.record(OTHER, "L1", DUTY, "thermal", {"computed_at": "2026-10-01T10:00:00", "t_max": 70.0})

    # ── accounts: grants, a default motor; access list ───────────────────────────────────────
    monkeypatch.setattr(U, "_USERS_FILE", t / "users.json")
    _write_json(t / "users.json", {
        EMAIL: {"role": "user", "motors": {"all": False, "dies": [OLD, OTHER],
                "configs": [f"{OLD}/{CFG}", f"{OTHER}/L1"], "default": {"die": OLD, "config": CFG}}},
        BYSTANDER: {"role": "user", "motors": {"all": False, "dies": [OTHER]}},
    })
    monkeypatch.setattr(MA, "_DIE_ACCESS_FILE", t / "die_access.json")
    _write_json(t / "die_access.json", {OLD: {"visibility": "selected", "clients": [EMAIL]}})

    # ── a development project that links the motor ───────────────────────────────────────────
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(t / "projects"))
    (t / "projects").mkdir()
    PS.create_project({
        "id": PROJECT, "members": [{"email": EMAIL, "role": "customer"}],
        "linked_motors": [{"die": OLD, "config": CFG, "status": "current", "note": "first try"}],
        "motor_history": [{"die": OLD, "config": CFG, "linked_at": "2026-10-01T10:00:00"}],
        "project_motor_grants": {_key(OLD, CFG): {"grant_key": f"{OLD}/{CFG}", "emails": [EMAIL]}},
        "grant_removal_candidates": [{"email": EMAIL, "die": OLD, "config": CFG, "reason": "x"}],
    })
    PS.save_index({"motors": {_key(OLD, CFG): {"project_id": PROJECT, "die": OLD, "config": CFG}}})
    PS.save_snapshot(PROJECT, {"id": "snapshot-1", "version": 1, "created_at": "2026-10-01T10:00:00Z",
                               "machine": {"die_name": OLD, "config_name": CFG, "die": {"name": OLD}},
                               "results": [],
                               "access_scope": {"kind": "catalog", "die": OLD, "config": CFG,
                                                "grant_key": f"{OLD}/{CFG}"}})
    PS.append_audit(PROJECT, EMAIL, "motor.link", "motor", f"{OLD}/{CFG}")

    # ── production + CAD ─────────────────────────────────────────────────────────────────────
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(t / "production"))
    prod = t / "production" / OLD.replace(" ", "_") / CFG
    _write_json(prod / "production.json", {"die": OLD, "config": CFG, "title": {"en": "x"}})
    _write_json(prod / "bom.json", {"rows": [{"part": "stator"}]})
    (prod / "files").mkdir()
    (prod / "files" / "photo.bin").write_bytes(b"\x00\x01die")
    # the Tests store (motor_tests), a sibling of production with the same folder naming
    monkeypatch.setenv("MOTRES_MOTOR_TESTS_ROOT", str(t / "motor_tests"))
    _write_json(t / "motor_tests" / OLD.replace(" ", "_") / CFG / "tests.json",
                {"die": OLD, "config": CFG, "settings": {"tests_visible_to_customer": False},
                 "tests": [{"id": "test_1", "kind": "parameters", "date": "2026-10-18"}]})
    monkeypatch.setenv("MOTRES_CAD_ROOT", str(t / "cad"))
    _write_json(t / "cad" / OLD / CFG / "manifest.json", {"die": OLD, "config": CFG, "motor_id": "m1",
                                                          "files": {"m1_full.glb": {"sha256": "ab"}}})
    from motor_ai_sim.cad import rebuild_queue as RQ
    RQ.request(t / "cad", OLD, CFG, requested_by=EMAIL)

    # ── catalog cards, presets, workspace memories, cooling options ──────────────────────────
    cat, pres = t / "catalog.json", t / "presets.json"
    _write_json(cat, {"tiers": [], "diameters_mm": [], "motors": [
        {"id": "c1", "name": OLD}, {"id": "c2", "name": f"{OLD} {CFG}"}, {"id": "c3", "name": OTHER}]})
    _write_json(pres, {"p1": {"id": "p1", "name": OLD}, "p2": {"id": "p2", "name": OTHER}})
    monkeypatch.setattr(CAT, "_CATALOG_PATH", cat, raising=False)
    monkeypatch.setattr(PRE, "_PRESETS_PATH", pres, raising=False)
    _write_json(ws / ".panel_settings.json", {"thermal": {EMAIL: {
        "settings": {}, "motor": f"{OLD}|{CFG}",
        "by_motor": {f"{OLD}|{CFG}": {"settings": {"a": 1}}, f"{OTHER}|L1": {"settings": {}}}}}})
    _write_json(ws / "user_motors.json", {EMAIL: {"um_1": {"id": "um_1", "name": "mine",
                                                            "src": {"die": OLD, "config": CFG}}}})
    co = t / "cooling_options.yaml"
    co.write_text(yaml.safe_dump({"version": 1, "dies": {OLD: {"cooling_options": ["robotics"]}}}),
                  encoding="utf-8")
    monkeypatch.setattr(CO, "_FILE", co)

    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(t / "admin_audit.jsonl"))
    return t


def _rename(world, new=NEW, dry_run=False, glob_scope=True):
    src = world / "dies" / OLD
    return DRN.run(fam, OLD, new, src_dir=src, dst_dir=src.parent / new, dry_run=dry_run,
                   glob_scope=glob_scope, actor="admin@example.com")


def _all_text(root: Path) -> str:
    """Every text file under ``root`` (the stores), concatenated; binary and field files skipped.
    The append-only audit logs are history, not references: a rename never rewrites them."""
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix in (".json", ".yaml", ".jsonl", ".yml") and p.name != "audit.jsonl":
            out.append(f"## {p.relative_to(root)}\n" + p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(out)


# ── the full rename ───────────────────────────────────────────────────────────────────────────

def test_rename_moves_everything_keyed_by_the_die_name(world):
    from motor_ai_sim import duty_fields as DF
    from motor_ai_sim import duty_results as DR
    from motor_ai_sim import motor_access as MA
    from motor_ai_sim import users as U
    from motor_ai_sim.development import store as PS
    from motor_ai_sim.production import store as PR

    out = _rename(world)
    assert out["ok"] and out["to"] == NEW and not out["dry_run"]
    stores = {c["store"] for c in out["changes"]}
    assert {"catalog", "catalog_history", "duty_fields", "duty_results", "active_context",
            "panel_settings", "my_motors", "catalog_cards", "presets", "user_grants", "die_access",
            "projects", "project_index", "project_snapshots", "production", "cad_models",
            "cad_rebuild_queue"} <= stores

    # the catalog: folder, name, back-references, derived_from of a sibling, history folder
    dies = world / "dies"
    assert not (dies / OLD).exists() and (dies / NEW / "die.yaml").is_file()
    assert yaml.safe_load((dies / NEW / "die.yaml").read_text(encoding="utf-8"))["name"] == NEW
    assert yaml.safe_load((dies / NEW / f"{CFG}.yaml").read_text(encoding="utf-8"))["die"] == NEW
    assert yaml.safe_load((dies / OTHER / "die.yaml").read_text(encoding="utf-8"))["derived_from"]["die"] == NEW
    assert (dies / ".history" / NEW).is_dir() and not (dies / ".history" / OLD).exists()
    # run sidecars travelled with the folder and the duty still points at them
    c = yaml.safe_load((dies / NEW / f"{CFG}.yaml").read_text(encoding="utf-8"))
    assert (dies / NEW / c["duties"][0]["runs"]["sine"]["payload_file"]).is_file()

    # duty results: the current rows AND the per-kind history, nothing left under the old name
    assert DR.get(NEW, CFG)[DUTY]["thermal"]["t_max"] == 95.0
    assert [h["t_max"] for h in DR.history(NEW, CFG, DUTY, "thermal")] == [90.0]
    assert DR.get(OLD, CFG) == {} and DR.history(OLD, CFG, DUTY, "thermal") == []
    assert DR.get(OTHER, "L1")[DUTY]["thermal"]["t_max"] == 70.0          # a neighbour is untouched

    # the stored field file names the new die in its own meta and is still listed
    assert DF.meta_die(dies / NEW / "runs" / CFG / DF._duty_stem(DUTY) / "fields" / "em.npz") == NEW
    assert list(DF.have(NEW, CFG)) == [DUTY]

    # the active context
    assert json.loads((world / ".family_context.json").read_text(encoding="utf-8"))["die"] == NEW

    # accounts: die grant, configuration grant, default motor; the bystander is untouched
    g = U.get_stored_motor_grants(EMAIL)
    assert sorted(g["dies"]) == sorted([NEW, OTHER])
    assert sorted(g["configs"]) == sorted([f"{NEW}/{CFG}", f"{OTHER}/L1"])
    assert g["default"] == {"die": NEW, "config": CFG}
    assert U.get_stored_motor_grants(BYSTANDER)["dies"] == [OTHER]
    # the per-die access list
    assert MA.get_die_access(NEW)["clients"] == [EMAIL] and MA.get_die_access(OLD)["visibility"] == "private"

    # the development project: link, history, grant ledger, removal candidates, index, snapshot, audit
    p = PS.get_project(PROJECT)
    assert p["linked_motors"] == [{"die": NEW, "config": CFG, "status": "current", "note": "first try"}]
    assert p["motor_history"][0]["die"] == NEW
    assert list(p["project_motor_grants"]) == [_key(NEW, CFG)]
    assert p["project_motor_grants"][_key(NEW, CFG)]["grant_key"] == f"{NEW}/{CFG}"
    assert p["grant_removal_candidates"][0]["die"] == NEW
    assert PS.load_index()["motors"] == {_key(NEW, CFG): {"project_id": PROJECT, "die": NEW, "config": CFG}}
    snap = PS.list_snapshots(PROJECT)[0]
    assert snap["machine"]["die_name"] == NEW and snap["access_scope"]["die"] == NEW
    assert snap["access_scope"]["grant_key"] == f"{NEW}/{CFG}"
    assert snap["machine"]["die"] == {"name": OLD}                       # the frozen content is history
    hist = PS.motor_history(PROJECT)
    assert all(r["die"] == NEW for r in hist)                             # audit fallback follows the rename

    # production: folder, the record's own die field, the uploaded file kept
    prod = world / "production" / NEW.replace(" ", "_") / CFG
    assert json.loads((prod / "production.json").read_text(encoding="utf-8"))["die"] == NEW
    assert (prod / "files" / "photo.bin").read_bytes() == b"\x00\x01die"
    assert PR.production_exists(NEW, CFG) and not PR.production_exists(OLD, CFG)
    assert not (world / "production" / OLD.replace(" ", "_")).exists()
    assert "die.rename" in (prod / "audit.jsonl").read_text(encoding="utf-8")

    # Tests: folder and the record's die field follow; the rename is in its audit
    mt = world / "motor_tests" / NEW.replace(" ", "_") / CFG
    assert json.loads((mt / "tests.json").read_text(encoding="utf-8"))["die"] == NEW
    assert not (world / "motor_tests" / OLD.replace(" ", "_")).exists()
    assert "die.rename" in (mt / "audit.jsonl").read_text(encoding="utf-8")
    assert "motor_tests" in stores

    # CAD model folder, manifest, rebuild request
    from motor_ai_sim import model3d
    from motor_ai_sim.cad import rebuild_queue as RQ
    assert model3d.find(NEW, CFG, world / "cad") and not model3d.find(OLD, CFG, world / "cad")
    assert RQ.read(world / "cad", NEW, CFG)["die"] == NEW and RQ.read(world / "cad", OLD, CFG) is None

    # catalog cards / presets named after the die; ids unchanged; the neighbour untouched
    cards = json.loads((world / "catalog.json").read_text(encoding="utf-8"))["motors"]
    assert [m["name"] for m in cards] == [NEW, f"{NEW} {CFG}", OTHER]
    assert [m["id"] for m in cards] == ["c1", "c2", "c3"]
    assert json.loads((world / "presets.json").read_text(encoding="utf-8"))["p1"]["name"] == NEW

    # workspace memories
    ps = json.loads((world / "ws" / ".panel_settings.json").read_text(encoding="utf-8"))["thermal"][EMAIL]
    assert ps["motor"] == f"{NEW}|{CFG}" and sorted(ps["by_motor"]) == sorted([f"{NEW}|{CFG}", f"{OTHER}|L1"])
    assert json.loads((world / "ws" / "user_motors.json").read_text(
        encoding="utf-8"))[EMAIL]["um_1"]["src"]["die"] == NEW

    # deploy-managed file the app cannot rewrite: reported, with the exact path
    assert [m["store"] for m in out["manual"]] == ["cooling_options"]
    assert str(world / "cooling_options.yaml") in out["manual"][0]["where"]

    # the audit entry
    audit = [json.loads(x) for x in (world / "admin_audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert audit[-1]["action"] == "die.rename" and audit[-1]["target"] == OLD
    assert audit[-1]["details"]["to"] == NEW

    # and NOTHING in any store (apart from the frozen content / audit history / the manual file)
    # still names the old die as a die reference
    text = _all_text(world)
    for needle in (f'"die": "{OLD}"', f'"{OLD}/{CFG}"', f"{OLD}|", f"die: {OLD}\n"):
        stale = [ln for ln in text.splitlines() if needle in ln]
        assert not stale, (needle, stale)


def test_dry_run_lists_every_place_and_changes_nothing(world):
    before = _tree_digest(world)
    out = _rename(world, dry_run=True)
    assert out["dry_run"] and out["die"] == OLD
    assert len(out["changes"]) >= 15
    assert {c["store"] for c in out["changes"]} >= {"catalog", "duty_results", "user_grants",
                                                   "projects", "production", "cad_models"}
    assert _tree_digest(world) == before                                # not one byte moved
    assert (world / "dies" / OLD / "die.yaml").is_file()


def test_failure_in_a_late_step_rolls_everything_back(world, monkeypatch):
    before = _tree_digest(world)

    def boom(ctx):
        raise RuntimeError("disk full")
    monkeypatch.setattr(DRN, "step_cad", boom)
    monkeypatch.setattr(DRN, "GLOBAL_STEPS", (DRN.step_grants, DRN.step_die_access, DRN.step_projects,
                                              DRN.step_production, DRN.step_motor_tests, boom,
                                              DRN.step_manual))
    with pytest.raises(RuntimeError, match="disk full"):
        _rename(world)
    assert _tree_digest(world) == before                                # every store is back as it was
    from motor_ai_sim import duty_results as DR
    from motor_ai_sim import users as U
    assert DR.get(OLD, CFG)[DUTY]["thermal"]["t_max"] == 95.0
    assert OLD in U.get_stored_motor_grants(EMAIL)["dies"]
    assert not (world / "admin_audit.jsonl").exists()                   # a failed rename is not "done"


def test_a_private_copy_does_not_retarget_the_shared_catalogs_stores(world):
    from motor_ai_sim import users as U
    from motor_ai_sim.development import store as PS
    out = _rename(world, glob_scope=False)
    assert out["scope"] == "workspace"
    # workspace-level stores followed ...
    from motor_ai_sim import duty_results as DR
    assert DR.get(NEW, CFG)
    # ... the shared catalog's grants / projects / production / CAD did not
    assert OLD in U.get_stored_motor_grants(EMAIL)["dies"]
    assert PS.get_project(PROJECT)["linked_motors"][0]["die"] == OLD
    assert (world / "production" / OLD.replace(" ", "_")).is_dir()
    assert (world / "cad" / OLD).is_dir()


def test_the_baseline_motor_cannot_be_renamed_away_from_every_account(world, monkeypatch):
    from motor_ai_sim import users as U
    monkeypatch.setattr(U, "_BASELINE_DIE", OLD)
    before = _tree_digest(world)
    with pytest.raises(DRN.RenameBlocked, match="baseline"):
        _rename(world)
    assert _tree_digest(world) == before


def test_an_existing_production_folder_blocks_the_rename_and_rolls_back(world):
    (world / "production" / NEW.replace(" ", "_")).mkdir(parents=True)
    (world / "production" / NEW.replace(" ", "_") / "x.json").write_text("{}", encoding="utf-8")
    before = _tree_digest(world)
    with pytest.raises(DRN.RenameBlocked, match="production folder"):
        _rename(world)
    assert _tree_digest(world) == before


def test_retag_meta_rewrites_only_the_named_fields_and_keeps_the_arrays(tmp_path):
    np = pytest.importorskip("numpy")
    from motor_ai_sim import duty_fields as DF
    f = tmp_path / "em.npz"
    np.savez_compressed(f, meta=np.array(json.dumps({"die": "A", "duty": "d1", "kind": "em"})),
                        a=np.arange(4, dtype="float32"))
    assert DF.retag_meta(f, die="B") is True
    assert DF.retag_meta(f, die="B") is False                          # already there: untouched
    DF._retag_duty(f, "d2")                                            # the duty-rename helper still works
    with np.load(f, allow_pickle=False) as z:
        meta = json.loads(str(z["meta"]))
        assert (meta["die"], meta["duty"], meta["kind"]) == ("B", "d2", "em")
        assert z["a"].tolist() == [0.0, 1.0, 2.0, 3.0]


# ── the route ─────────────────────────────────────────────────────────────────────────────────

def test_route_dry_run_then_real_rename(world):
    w = {"user": {"email": "admin@example.com"}, "is_admin": True, "authorization": None}
    dry = fam.rename_die(OLD, fam.DieRename(name=NEW), dry_run=True, _w=w)
    assert dry["dry_run"] and (world / "dies" / OLD).is_dir()
    out = fam.rename_die(OLD, fam.DieRename(name=NEW), dry_run=False, _w=w)
    assert out["die"] == NEW and out["from"] == OLD and not (world / "dies" / OLD).exists()
    assert out["changes"] and out["scope"] == "shared catalog"
    # the same name again is a no-op, a taken name is a 409, a missing die a 404
    assert fam.rename_die(NEW, fam.DieRename(name=NEW), dry_run=False, _w=w)["changes"] == []
    with pytest.raises(HTTPException) as e409:
        fam.rename_die(NEW, fam.DieRename(name=OTHER), dry_run=False, _w=w)
    assert e409.value.status_code == 409
    with pytest.raises(HTTPException) as e404:
        fam.rename_die("nope", fam.DieRename(name="x"), dry_run=False, _w=w)
    assert e404.value.status_code == 404


def test_route_maps_a_blocked_rename_to_409(world, monkeypatch):
    from motor_ai_sim import users as U
    monkeypatch.setattr(U, "_BASELINE_DIE", OLD)
    w = {"user": {"email": "admin@example.com"}, "is_admin": True, "authorization": None}
    with pytest.raises(HTTPException) as exc:
        fam.rename_die(OLD, fam.DieRename(name=NEW), dry_run=False, _w=w)
    assert exc.value.status_code == 409 and "baseline" in str(exc.value.detail)
    assert (world / "dies" / OLD).is_dir()


def test_route_turns_an_unexpected_failure_into_a_rolled_back_500(world, monkeypatch):
    def boom(ctx):
        raise OSError("disk full")
    monkeypatch.setattr(DRN, "GLOBAL_STEPS", (DRN.step_grants, boom))
    before = _tree_digest(world)
    w = {"user": {"email": "admin@example.com"}, "is_admin": True, "authorization": None}
    with pytest.raises(HTTPException) as exc:
        fam.rename_die(OLD, fam.DieRename(name=NEW), dry_run=False, _w=w)
    assert exc.value.status_code == 500 and "rolled back" in str(exc.value.detail)
    assert _tree_digest(world) == before


# ── with multi-user layering on (the server) ───────────────────────────────────────────────────

LAYER_SHARED_DIE = "SHAREDA 40"
LAYER_OWN_DIE = "OWNA 40"
ADMIN = "admin@example.com"
ALICE = "alice@example.com"


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

    def _die(root: Path, name: str):
        (root / name).mkdir(parents=True)
        (root / name / "die.yaml").write_text(yaml.safe_dump(
            {"name": name, "locked": False, "geometry": dict(GEO)}, sort_keys=False), encoding="utf-8")
        (root / name / f"{CFG}.yaml").write_text(yaml.safe_dump(
            {"name": CFG, "die": name, "role": "motor", "duties": []}, sort_keys=False), encoding="utf-8")

    _die(shared / "dies", LAYER_SHARED_DIE)
    shutil.copy2(Path(__file__).resolve().parents[1] / "config" / "motor_config.yaml",
                 shared / "motor_config.yaml")
    monkeypatch.setenv("WORKSPACES_ROOT", str(works))
    monkeypatch.setenv("SHARED_ROOT", str(shared))
    monkeypatch.setenv("PUBLISHED_ROOT", str(published))
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.delitem(fam.__dict__, "_DIES_DIR", raising=False)
    fam._TREE_CACHE.clear()
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr("motor_ai_sim.config.DEFAULT_CONFIG_PATH",
                        str(tmp_path / "identity" / "motor_config.yaml"))
    (tmp_path / "identity").mkdir()
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(ALICE, "password-alice", role="user", name="Alice")
    U.set_motor_grants(ALICE, all_motors=False, dies=[LAYER_SHARED_DIE],
                       configs=[f"{LAYER_SHARED_DIE}/{CFG}"])
    ws_id = W.workspace_id(ALICE)
    _die(works / ws_id / "dies", LAYER_OWN_DIE)
    # Alice's own results ledger names her private die
    _write_json(works / ws_id / ".duty_results.json", {"version": 1, "results": {
        LAYER_OWN_DIE: {CFG: {DUTY: {"thermal": {"t_max": 80.0}}}}}})
    yield {"client": TestClient(app), "shared": shared, "ws": works / ws_id,
           "admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
           "alice": {"Authorization": f"Bearer {U.issue_token(ALICE)}"}}
    fam._TREE_CACHE.clear()


def test_a_user_renames_her_private_die_and_her_results_follow(layered):
    from motor_ai_sim import users as U
    c = layered["client"]
    r = c.patch(f"/api/family/die/{LAYER_OWN_DIE}", json={"name": "OWNA 40 alice"},
                headers=layered["alice"])
    assert r.status_code == 200, r.text
    assert r.json()["scope"] == "workspace"
    assert (layered["ws"] / "dies" / "OWNA 40 alice" / "die.yaml").is_file()
    ledger = json.loads((layered["ws"] / ".duty_results.json").read_text(encoding="utf-8"))["results"]
    assert list(ledger) == ["OWNA 40 alice"]
    # the shared catalog's grants did not move
    assert U.get_stored_motor_grants(ALICE)["dies"] == [LAYER_SHARED_DIE]


def test_a_user_cannot_rename_a_shared_die(layered):
    r = layered["client"].patch(f"/api/family/die/{LAYER_SHARED_DIE}", json={"name": "SHAREDA 40 alice"},
                                headers=layered["alice"])
    assert r.status_code == 403, r.text
    assert (layered["shared"] / "dies" / LAYER_SHARED_DIE).is_dir()


def test_an_admin_renames_a_shared_die_and_the_grants_follow(layered):
    from motor_ai_sim import users as U
    c = layered["client"]
    dry = c.patch(f"/api/family/die/{LAYER_SHARED_DIE}?dry_run=1", json={"name": "SHAREDA 40 admin"},
                  headers=layered["admin"])
    assert dry.status_code == 200, dry.text
    assert dry.json()["dry_run"] is True and "user_grants" in {x["store"] for x in dry.json()["changes"]}
    assert (layered["shared"] / "dies" / LAYER_SHARED_DIE).is_dir()
    r = c.patch(f"/api/family/die/{LAYER_SHARED_DIE}", json={"name": "SHAREDA 40 admin"},
                headers=layered["admin"])
    assert r.status_code == 200, r.text
    assert r.json()["scope"] == "shared catalog"
    assert (layered["shared"] / "dies" / "SHAREDA 40 admin" / "die.yaml").is_file()
    g = U.get_stored_motor_grants(ALICE)
    assert g["dies"] == ["SHAREDA 40 admin"] and g["configs"] == [f"SHAREDA 40 admin/{CFG}"]


# ── the dry run names the duty_results store it would touch (path + rows) ───────────────────────

def _shared_ledger(layered):
    _write_json(layered["shared"] / ".duty_results.json", {"version": 1, "results": {
        LAYER_SHARED_DIE: {CFG: {DUTY: {"thermal": {"t_max": 90.0}, "coupled": {"t_max": 99.0}}}}},
        "history": {LAYER_SHARED_DIE: {CFG: {DUTY: {"thermal": [{"t_max": 80.0}]}}}}})


def test_dry_run_reports_the_shared_ledger_with_its_rows(layered):
    _shared_ledger(layered)
    r = layered["client"].patch(f"/api/family/die/{LAYER_SHARED_DIE}?dry_run=1",
                                json={"name": "SHAREDA 40 admin"}, headers=layered["admin"])
    assert r.status_code == 200, r.text
    rows = [c for c in r.json()["changes"] if c["store"] == "duty_results"]
    assert len(rows) == 1
    assert Path(rows[0]["where"]) == layered["shared"] / ".duty_results.json"
    assert rows[0]["exists"] is True and rows[0]["rows"] == {"results": 2, "history": 1}
    # nothing moved
    ledger = json.loads((layered["shared"] / ".duty_results.json").read_text(encoding="utf-8"))
    assert list(ledger["results"]) == [LAYER_SHARED_DIE]


def test_dry_run_makes_a_missing_ledger_visible(layered):
    r = layered["client"].patch(f"/api/family/die/{LAYER_SHARED_DIE}?dry_run=1",
                                json={"name": "SHAREDA 40 admin"}, headers=layered["admin"])
    assert r.status_code == 200, r.text
    rows = [c for c in r.json()["changes"] if c["store"] == "duty_results"]
    assert len(rows) == 1 and rows[0]["exists"] is False and rows[0]["rows"] == {"results": 0, "history": 0}
    assert Path(rows[0]["where"]) == layered["shared"] / ".duty_results.json"
    assert "does not exist" in rows[0]["change"]


def test_the_shared_ledger_is_found_without_the_admin_request_context(layered):
    """An in-process call (no verified-admin request bound) used to read the WORKSPACE ledger and miss the shared one."""
    from motor_ai_sim import duty_results as DR
    _shared_ledger(layered)
    src = layered["shared"] / "dies" / LAYER_SHARED_DIE
    ctx = DRN.Ctx(fam=fam, old=LAYER_SHARED_DIE, new="SHAREDA 40 admin", src_dir=src,
                  dst_dir=src.parent / "SHAREDA 40 admin", dry_run=True, glob_scope=True)
    assert not DR.has_die(LAYER_SHARED_DIE)                              # the identity-based store: not the shared one
    DRN.step_duty_results(ctx)
    entry = [c for c in ctx.changes if c["store"] == "duty_results"][0]
    assert Path(entry["where"]) == layered["shared"] / ".duty_results.json" and entry["rows"]["results"] == 2
    ctx.dry_run = False
    ctx.dst_dir = ctx.src_dir                                             # only this step runs; the folder stays
    DRN.step_duty_results(ctx)
    ledger = json.loads((layered["shared"] / ".duty_results.json").read_text(encoding="utf-8"))
    assert list(ledger["results"]) == ["SHAREDA 40 admin"] and list(ledger["history"]) == ["SHAREDA 40 admin"]
    for undo in reversed(ctx.undo):                                       # and it rolls back into the same store
        undo()
    ledger = json.loads((layered["shared"] / ".duty_results.json").read_text(encoding="utf-8"))
    assert list(ledger["results"]) == [LAYER_SHARED_DIE]
