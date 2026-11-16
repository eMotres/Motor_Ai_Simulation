"""Sidecar ids and the link registry (unified object model, steps 1-2): idempotent, nothing written into the sources,
ambiguous links go to the review list, renames keep the ids.  Temporary stores, neutral names, no solver."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

from motor_ai_sim import object_registry as R


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def stores(tmp_path):
    cat, prj, prd = tmp_path / "dies", tmp_path / "projects", tmp_path / "production"
    _write(cat / "DIE A" / "die.yaml", "name: DIE A\n")
    _write(cat / "DIE A" / "L12.yaml", "controller:\n  catalog_id: ctl-60v\n")
    _write(cat / "DIE A" / "L20.yaml", "name: L20\n")
    _write(cat / "DIE B" / "die.yaml", "name: DIE B\n")
    _write(cat / "DIE B" / "L40.yaml", "name: L40\n")
    _write(cat / "DIE B" / "l40.yaml", "name: l40\n")                       # differs only by case
    _write(cat / ".hidden" / "die.yaml", "x: 1\n")
    projects = {
        "alpha": {"id": "alpha", "title": "Alpha", "linked_motors": [{"die": "DIE A", "config": "L12", "status": "current"},
                                                                     {"die": "DIE A", "config": "L20", "status": "trial"}]},
        "beta": {"id": "beta", "title": "Beta", "linked_motors": [{"die": "DIE A", "config": "L12", "status": "current"},
                                                                  {"die": "GONE", "config": "L1", "status": "current"}],
                 "motor_history": [{"die": "DIE B", "config": "L40", "linked_at": "t1", "unlinked_at": "t2"}]},
        "gamma": {"id": "gamma", "title": "Gamma", "linked_motors": [{"die": "CTRL 60V", "config": "R07", "status": "current"}]},
    }
    for pid, p in projects.items():
        _write(prj / pid / "project.json", json.dumps(p))
    for die, cfg, kind in (("DIE A", "L12", "motor"), ("DIE C", "L9", "motor"), ("CTRL 60V", "R07", "electronics")):
        _write(prd / die.replace(" ", "_") / cfg / "production.json",
               json.dumps({"die": die, "config": cfg, "status": "active", "product_type": kind}))
    return {"catalog_root": cat, "projects_root": prj, "production_root": prd, "registry": tmp_path / "registry"}


def _hashes(*roots: Path) -> dict:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for r in roots for p in sorted(r.rglob("*")) if p.is_file()}


def test_backfill_maps_everything_and_writes_nothing_into_the_sources(stores):
    before = _hashes(stores["catalog_root"], stores["projects_root"], stores["production_root"])
    rep = R.backfill(**stores)
    assert _hashes(stores["catalog_root"], stores["projects_root"], stores["production_root"]) == before
    assert rep["minted"] == {"family": 3, "item": 5, "project": 3, "production": 3}
    idx = R.index(stores["registry"])
    a12 = idx.resolve("item", "catalog", "DIE A/L12")
    assert idx.obj(a12)["family"] == idx.resolve("family", "catalog", "DIE A")
    board = idx.resolve("item", "production", "CTRL 60V/R07")
    assert idx.obj(board)["item_kind"] == "board"
    assert idx.obj(idx.obj(board)["family"])["family_kind"] == "platform"
    # one motor serves two projects; the trial link and the unlinked history stay visible as such
    proj_links = {(idx.obj(l["from"])["locator"]["key"], l["status"]) for l in idx.links_to[a12] if l["kind"] == "project_item"}
    assert proj_links == {("alpha", "current"), ("beta", "current")}
    b40 = idx.resolve("item", "catalog", "DIE B/L40")
    assert [l["status"] for l in idx.links_to[b40]] == ["unlinked"]
    assert sorted(l["kind"] for l in idx.links_to[board]) == ["production_item", "project_item"]
    ctl = [l for l in idx.links_from[a12] if l["kind"] == "analysis_controller"]
    assert ctl and ctl[0]["to"] == "controller_catalog:ctl-60v" and ctl[0]["confirmed"] is False


def test_ambiguous_links_go_to_the_review_list_not_guessed(stores):
    rep = R.backfill(**stores)
    codes = sorted((r["code"], r["where"]) for r in rep["review_items"])
    assert ("link_to_missing_motor", "beta -> GONE/L1") in codes
    assert ("production_without_item", "DIE C/L9") in codes
    assert ("case_collision", "catalog/DIE B") in codes
    idx = R.index(stores["registry"])
    assert idx.resolve("item", "catalog", "GONE/L1") is None                 # nothing invented for a missing motor


def test_a_second_run_mints_nothing_and_changes_nothing(stores):
    R.backfill(**stores)
    first = json.loads(R.store_path(stores["registry"]).read_text())
    rep = R.backfill(**stores)
    second = json.loads(R.store_path(stores["registry"]).read_text())
    assert sum(rep["minted"].values()) == 0 and rep["links_added"] == 0 and rep["links_updated"] == 0
    assert first["objects"] == second["objects"] and first["links"] == second["links"] and first["review"] == second["review"]


def test_dry_run_writes_nothing(stores):
    rep = R.backfill(**stores, dry_run=True)
    assert rep["dry_run"] and rep["objects"] == 14
    assert not R.store_path(stores["registry"]).exists()


def test_a_vanished_source_is_marked_never_deleted(stores):
    R.backfill(**stores)
    (stores["catalog_root"] / "DIE A" / "L20.yaml").unlink()
    rep = R.backfill(**stores)
    idx = R.index(stores["registry"])
    a20 = idx.resolve("item", "catalog", "DIE A/L20")
    assert a20 and idx.obj(a20)["missing_since"] and rep["missing"] == 1
    assert ("link_to_missing_motor", "alpha -> DIE A/L20") in {(r["code"], r["where"]) for r in rep["review_items"]}


def test_a_rename_keeps_the_ids_and_the_old_names_resolve(stores):
    R.backfill(**stores)
    doc = R.load(stores["registry"])
    idx0 = R.Index(doc)
    fam, a12 = idx0.resolve("family", "catalog", "DIE A"), idx0.resolve("item", "catalog", "DIE A/L12")
    prd = idx0.resolve("production", "production", "DIE A/L12")
    assert R.rename_die_in(doc, "DIE A", "DIE A2") == 4                       # family, L12, L20, its production
    assert R.move_config_in(doc, "DIE A2", "L12", "DIE B", "L13") == 2        # the item and its production
    idx = R.Index(doc)
    assert idx.resolve("family", "catalog", "DIE A2") == fam
    assert idx.resolve("item", "catalog", "DIE B/L13") == a12 == idx.resolve("item", "catalog", "DIE A/L12")
    assert idx.obj(a12)["family"] == idx.resolve("family", "catalog", "DIE B")
    assert idx.resolve("production", "production", "DIE B/L13") == prd


def test_the_rename_steps_are_registered():
    from motor_ai_sim import config_move as CM
    from motor_ai_sim import die_rename as DRN
    assert DRN.step_object_registry in DRN.GLOBAL_STEPS and CM.step_object_registry in CM.GLOBAL_STEPS


def test_parallel_backfills_do_not_mint_twice(stores):
    threads = [threading.Thread(target=R.backfill, kwargs=stores) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    doc = R.load(stores["registry"])
    assert len(doc["objects"]) == 14 and len(doc["backfills"]) == 4


def test_cli_dry_run_report(stores, tmp_path, capsys):
    report = tmp_path / "report.json"
    assert R.main(["backfill", "--catalog", str(stores["catalog_root"]), "--projects", str(stores["projects_root"]),
                   "--production", str(stores["production_root"]), "--registry", str(stores["registry"]),
                   "--dry-run", "--report", str(report)]) == 0
    assert json.loads(report.read_text())["review_items"]
    assert '"dry_run": true' in capsys.readouterr().out
    assert not R.store_path(stores["registry"]).exists()


def test_history_of_a_removed_configuration_is_history_not_review(stores):
    """Coordinator dry run 2026-11-17: 54 of 55 review items were motor_history rows of configurations deleted long ago."""
    p = stores["projects_root"] / "beta" / "project.json"
    doc = json.loads(p.read_text())
    doc["motor_history"].append({"die": "DIE A", "config": "OLD-17t", "linked_at": "t0", "unlinked_at": "t1"})
    doc["motor_history"].append({"die": "GONE DIE", "config": "X", "linked_at": "t0", "unlinked_at": "t1"})
    p.write_text(json.dumps(doc))
    rep = R.backfill(**stores)
    wheres = {r["where"] for r in rep["review_items"]}
    assert "beta -> DIE A/OLD-17t" not in wheres and "beta -> GONE DIE/X" not in wheres
    assert "beta -> GONE/L1" in wheres                                       # a CURRENT link to a missing motor stays
    idx = R.index(stores["registry"])
    old = idx.resolve("item", "catalog", "DIE A/OLD-17t")
    assert idx.obj(old)["state"] == "removed" and idx.obj(old)["family"] == idx.resolve("family", "catalog", "DIE A")
    gone = idx.resolve("item", "catalog", "GONE DIE/X")
    assert idx.obj(gone)["state"] == "removed" and idx.obj(gone)["family"] is None
    assert [l["status"] for l in idx.links_to[old]] == ["unlinked"]
    assert R.backfill(**stores)["minted"]["item"] == 0                         # idempotent for removed items too


def test_workspace_layers_give_workspace_only_dies_an_id(stores, tmp_path):
    """Coordinator dry run 2026-11-17: a production whose die lives only in the owner's workspace layer."""
    ws = tmp_path / "ws-owner"
    _write(ws / "DIE C" / "die.yaml", "name: DIE C\n")
    _write(ws / "DIE C" / "L9.yaml", "name: L9\n")
    _write(ws / "DIE A" / "L12.yaml", "name: L12\n")
    _write(ws / "DIE A" / "die.yaml", "name: DIE A\n")
    args = dict(stores, catalog_root=[("shared", stores["catalog_root"]), f"ws-owner={ws}"])
    rep = R.backfill(**args)
    assert "production_without_item" not in {r["code"] for r in rep["review_items"]}
    idx = R.index(stores["registry"])
    c9 = idx.resolve("item", "catalog", "DIE C/L9")
    assert idx.obj(c9)["layers"] == ["ws-owner"]
    assert idx.obj(idx.resolve("item", "catalog", "DIE A/L12"))["layers"] == ["shared", "ws-owner"]
    assert idx.obj(idx.resolve("family", "catalog", "DIE A"))["layers"] == ["shared", "ws-owner"]
    prd = idx.resolve("production", "production", "DIE C/L9")
    assert [l["to"] for l in idx.links_from[prd]] == [c9]
