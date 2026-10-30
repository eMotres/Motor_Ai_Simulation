"""A sweep is never lost to the next one.

Owner 2026-10-30: he started a new sweep, cancelled it at 0/12, and the previous
one (``sweep_8``, 7/8 points) was gone from the Sweep tab - ``.last_scan.json``
holds one run and "Save" was a button he had not pressed.  Now the server files
every sweep that ends with a computed point (finished or Stopped), a new Run files
what the tab still holds, a run that computed nothing never replaces the last
sweep on disk, and an admin can rebuild the sweeps of the past from the eval logs
(``.opt_dataset.jsonl`` / ``.scan_cache.jsonl``).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

import motor_ai_sim.routes.optimization as opt
from motor_ai_sim import sweep_rebuild as rb
from motor_ai_sim import sweep_saves as ss
from motor_ai_sim import workspace as W

MACHINE = {"fingerprint": "fp-A", "die": "CIANO56", "config": "L100-2x3ph", "duty": "S1",
           "swept": ["tooth_width"]}
WHEN = datetime(2026, 10, 29, 14, 5, tzinfo=timezone.utc)


def _pt(g, o, ok=True, **kw):
    p = {"geom_id": g, "op_index": o, "feasible": ok, "fem": True,
         "overrides": {"tooth_width": 3.0 + g}, "current_a": 10.0}
    if ok:
        p.update(T_em_Nm=1.0 + g, efficiency=0.9, torque_per_mass_Nm_kg=5.0 + g)
    return {**p, **kw}


def _result(run_id="sweep_8", points=None, machine=MACHINE, **kw):
    r = {"points": points if points is not None else [_pt(0, 0), _pt(1, 0)], "run_id": run_id,
         "n_total_points": 2, "n_built": 2, "n_geometries": 2, "steps_per_period": 24,
         "variables": [{"name": "tooth_width", "min": 3.0, "max": 4.0, "step": 1.0}],
         "operating_points": [{"current_a": 10.0, "gamma_deg": 0.0, "rpm": 4000}]}
    if machine is not None:
        r["machine"] = dict(machine)
    return {**r, **kw}


@pytest.fixture()
def home(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(W, "root", lambda: root)
    monkeypatch.setenv(ss.ENV_LEGACY_DIR, str(tmp_path / "no-legacy"))
    monkeypatch.setattr(opt, "_save_last_scan", lambda r: None)
    monkeypatch.setattr(opt, "_scan_store_path", lambda: str(root / ".last_scan.json"))
    keep = dict(opt._scan_state)                          # noqa: SLF001
    opt._scan_state.update(running=False, result=None, run_id="")   # noqa: SLF001
    yield root
    opt._scan_state.clear()                               # noqa: SLF001
    opt._scan_state.update(keep)                          # noqa: SLF001


# ── 1. every sweep that computed a point is filed ────────────────────────────
def test_a_finished_sweep_is_filed_with_name_machine_and_progress(home):
    row = ss.autosave(_result(campaign_id="scan-1"), state="finished", n_planned=2, when=WHEN)
    assert row["name"] == "L100-2x3ph S1 2026-10-29 14:05 UTC · tooth_width"
    assert row["auto"] is True and row["state"] == "finished"
    assert (row["n_done"], row["n_planned"]) == (2, 2)
    assert row["machine"]["fingerprint"] == "fp-A" and row["machine"]["die"] == "CIANO56"
    assert row["campaign_id"] == "scan-1"
    assert [r["id"] for r in ss.list_entries()] == [row["id"]]
    # and it reopens with its machine, like any save
    out = opt.open_saved(row["id"])
    assert out["machine"]["fingerprint"] == "fp-A"
    assert opt._scan_state["result"]["run_id"] == "sweep_8"           # noqa: SLF001


def test_a_stopped_sweep_with_some_points_is_filed_as_cancelled(home):
    pts = [_pt(0, 0), _pt(1, 0, ok=False, error="stopped"), _pt(2, 0)]
    row = ss.autosave(_result(points=pts, n_total_points=3), state="cancelled", n_planned=8)
    assert row["state"] == "cancelled"
    assert (row["n_done"], row["n_planned"]) == (2, 8)     # failed points are not "done"


def test_a_sweep_that_computed_nothing_is_not_filed(home):
    assert ss.autosave(_result(points=[]), state="cancelled", n_planned=12) is None
    assert ss.autosave(_result(points=[_pt(0, 0, ok=False)]), state="cancelled") is None
    assert ss.list_entries() == []


def test_one_run_is_one_file_and_a_manual_save_does_not_duplicate_it(home):
    ss.autosave(_result(), state="finished", n_planned=2)
    assert ss.autosave(_result(), state="finished", n_planned=2) is None
    opt._scan_state["result"] = _result()                                # noqa: SLF001
    out = opt.save_last_scan_as_saved(opt.SaveLastRequest())
    assert out["already_saved"] is True
    assert len(ss.list_entries()) == 1


def test_the_list_is_newest_first_across_auto_and_manual_saves(home):
    opt.save_result(opt.SaveRequest(name="manual", result=_result("r_old")))
    a = ss.autosave(_result("r_new"), state="finished",
                    when=datetime(2999, 1, 1, tzinfo=timezone.utc))
    assert ss.list_entries()[0]["id"] == a["id"]


# ── 2. a new Run files what the tab holds ────────────────────────────────────
def test_a_new_run_files_the_previous_unsaved_sweep(home):
    (home / ".last_scan.json").write_text("{}")
    opt._scan_state["result"] = _result("sweep_8", restored_from_disk=True)   # noqa: SLF001
    opt._archive_previous_scan()                                         # noqa: SLF001
    (row,) = ss.list_entries()
    assert row["run_id"] == "sweep_8" and row["state"] == "previous" and row["auto"]
    opt._archive_previous_scan()                       # twice -> still one      # noqa: SLF001
    assert len(ss.list_entries()) == 1


def test_a_new_run_does_not_refile_a_sweep_already_saved_by_hand(home):
    opt.save_result(opt.SaveRequest(name="mine", result=_result("sweep_8")))
    opt._scan_state["result"] = _result("sweep_8")                       # noqa: SLF001
    opt._archive_previous_scan()                                         # noqa: SLF001
    assert len(ss.list_entries()) == 1


def test_an_empty_previous_result_is_ignored(home):
    opt._scan_state["result"] = _result("empty", points=[])              # noqa: SLF001
    opt._archive_previous_scan()                                         # noqa: SLF001
    assert ss.list_entries() == []


# ── 3. the list is cheap and survives a damaged sidecar ──────────────────────
def test_listing_reads_the_sidecar_not_the_points(home, monkeypatch):
    row = ss.autosave(_result(), state="finished")
    d = ss.saves_dir()
    assert (d / f"{row['id']}.meta").is_file()
    seen = []
    real = ss.json.loads
    monkeypatch.setattr(ss.json, "loads", lambda t, *a, **k: (seen.append(len(t)), real(t, *a, **k))[1])
    ss.list_entries()
    assert seen and max(seen) < 2000                       # only the small sidecar was parsed


def test_a_damaged_or_missing_sidecar_is_rebuilt_from_the_save(home):
    row = ss.autosave(_result(), state="finished")
    meta = ss.saves_dir() / f"{row['id']}.meta"
    meta.write_text("{not json")
    assert ss.list_entries()[0]["run_id"] == "sweep_8"
    meta.unlink()
    assert ss.list_entries()[0]["state"] == "finished"
    assert meta.is_file()
    opt.delete_saved(row["id"])
    assert not meta.exists() and ss.list_entries() == []


# ── 4. the rebuild ───────────────────────────────────────────────────────────
CID = "scan-20261029T140500Z-aaaaaa"


def _row(ca, tw, mg, *, status="ok", cid=CID, kind="scan", fp="FP-A", ts="2026-10-29T14:05:01Z",
         torque=None, **kw):
    r = {"overrides": {"tooth_width": tw, "magnet_height": mg}, "current_a": ca, "gamma_deg": 0.0,
         "cfg_fp": fp, "campaign_id": cid, "campaign_kind": kind, "status": status, "ts": ts}
    if status == "ok":
        r.update(ripple=3.0, torque=torque if torque is not None else tw * 10.0 + mg + ca / 100, eff=0.9 + tw / 100,
                 td=tw, mass=2.0, v_peak=300.0, p_loss=50.0, thd_ll=1.0, kt=0.2)
    else:
        r["error"] = "solver failed"
    return {**r, **kw}


@pytest.fixture()
def logs(home, monkeypatch):
    ds, cache = home / ".opt_dataset.jsonl", home / ".scan_cache.jsonl"
    monkeypatch.setattr(opt, "_dataset_path", lambda: str(ds))
    monkeypatch.setattr(opt, "_eval_cache_path", lambda: str(cache))
    monkeypatch.setattr(opt, "_config_fingerprint", lambda keys=(): "FP-A")
    monkeypatch.setattr(opt, "_machine_stamp",
                        lambda keys=(): {"fingerprint": "STAMP-A", "swept": sorted(keys),
                                         "die": "CIANO56", "config": "L100-2x3ph", "duty": "S1"})

    def put(rows, cache_rows=()):
        ds.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        cache.write_text("".join(json.dumps(r) + "\n" for r in cache_rows), encoding="utf-8")
    return put


def _sweep_rows():
    return [
        _row(10.0, 3.0, 2.0), _row(10.0, 4.0, 2.0), _row(10.0, 5.0, 2.0),
        _row(10.0, 3.0, 3.0), _row(10.0, 4.0, 3.0, status="failed"),
        _row(20.0, 3.0, 2.0),                                   # a second operating point
        _row(10.0, 9.0, 9.0, status="rejected"),                # a rejected row
        _row(10.0, 8.0, 8.0, pre_solve=True, status="infeasible"),   # refused before a FEM
        _row(10.0, 7.0, 7.0, kind="descent", cid="descent-1"),  # not a sweep
        {"overrides": {"tooth_width": 6.0}, "current_a": 10.0, "status": "ok"},   # pre-schema-2
    ]


def test_rebuild_files_a_past_sweep_with_points_variables_operating_points_machine(logs):
    cache_line = {"k": "h1", "m": {"campaign_id": CID, "status": "ok"},
                  "v": {"ok": True, "res": {"T_em_Nm": 32.1, "efficiency": 0.93, "mass_total_kg": 2.0,
                                            "P_loss_total_W": 50.0, "P_cu_W": 11.0, "T_ripple_pct": 3.0,
                                            "torque_per_mass_Nm_kg": 3.0}}}
    logs(_sweep_rows(), [cache_line])
    out = opt.rebuild_saved(opt.RebuildRequest())
    assert out["created"] == 1
    (c,) = [c for c in out["campaigns"] if c["saved_id"]]
    assert c["campaign_id"] == CID and c["machine_verified"] is True
    (row,) = ss.list_entries()
    assert row["rebuilt"] and row["state"] == "rebuilt" and row["campaign_id"] == CID
    assert row["name"].startswith("L100-2x3ph S1 2026-10-29 14:05 UTC")
    assert row["machine"]["fingerprint"] == "STAMP-A"      # same machine as loaded -> real stamp
    res = ss.read_entry(row["id"])["result"]
    pts = res["points"]
    assert len(pts) == 6 and res["n_built"] == 5 and res["n_failed"] == 1
    assert all(isinstance(p["geom_id"], int) and isinstance(p["op_index"], int) for p in pts)
    assert {(o["current_a"], o["gamma_deg"]) for o in res["operating_points"]} == {(10.0, 0.0), (20.0, 0.0)}
    v = {x["name"]: x for x in res["variables"]}
    assert (v["tooth_width"]["min"], v["tooth_width"]["max"], v["tooth_width"]["step"]) == (3.0, 5.0, 1.0)
    assert (v["magnet_height"]["min"], v["magnet_height"]["max"]) == (2.0, 3.0)
    full = [p for p in pts if p.get("rebuilt_from") == "cache"]
    assert len(full) == 1 and full[0]["P_cu_W"] == 11.0    # matched to the cache by its numbers
    assert res["run_id"] == CID and res["pareto_indices"]
    assert ss.read_entry(row["id"])["result"]["rebuilt"]["rows_other_machine"] == 0


def test_rebuild_skips_rejected_rows_other_kinds_and_rows_without_a_campaign(logs):
    logs(_sweep_rows())
    groups = rb.group_campaigns(__import__("motor_ai_sim.optimization.eval_log", fromlist=["x"])
                                .iter_rows(opt._dataset_path()))                 # noqa: SLF001
    assert list(groups) == [CID] and len(groups[CID]) == 6


def test_rebuild_does_not_refile_a_sweep_saved_by_campaign_id_or_by_content(logs):
    logs(_sweep_rows())
    ss.autosave(_result("sweep_8", campaign_id=CID), state="finished")
    out = opt.rebuild_saved(opt.RebuildRequest())
    assert out["created"] == 0
    assert out["campaigns"][0]["skipped"] == "already saved (campaign id)"
    # content: a hand-saved sweep that holds every one of its designs
    for r in ss.list_entries():
        ss.delete_entry(r["id"])
    held = [{**_pt(i, 0), "overrides": {"tooth_width": tw, "magnet_height": mg}, "current_a": ca}
            for i, (ca, tw, mg) in enumerate([(10.0, 3.0, 2.0), (10.0, 4.0, 2.0), (10.0, 5.0, 2.0),
                                              (10.0, 3.0, 3.0), (10.0, 4.0, 3.0), (20.0, 3.0, 2.0)])]
    opt.save_result(opt.SaveRequest(name="by hand", result=_result("hand", points=held)))
    out = opt.rebuild_saved(opt.RebuildRequest(force=True))
    assert out["created"] == 0
    assert "holds all its designs" in out["campaigns"][0]["skipped"]


def test_rebuild_runs_once_per_workspace_unless_forced_and_never_duplicates(logs):
    logs(_sweep_rows())
    assert opt.rebuild_saved(opt.RebuildRequest())["created"] == 1
    with pytest.raises(HTTPException) as e:
        opt.rebuild_saved(opt.RebuildRequest())
    assert e.value.status_code == 409
    assert opt.rebuild_saved(opt.RebuildRequest(force=True))["created"] == 0
    assert len(ss.list_entries()) == 1


def test_a_dry_run_writes_nothing(logs):
    logs(_sweep_rows())
    out = opt.rebuild_saved(opt.RebuildRequest(dry_run=True))
    assert out["created"] == 0 and out["campaigns"][0]["n_points"] == 6
    assert ss.list_entries() == [] and not (ss.saves_dir() / ss.REBUILD_MARKER).exists()
    assert opt.rebuild_saved(opt.RebuildRequest())["created"] == 1     # not blocked by the dry run


def test_a_sweep_of_another_machine_is_unattributed_unless_the_admin_names_it(logs):
    rows = [_row(10.0, 3.0, 2.0, fp="FP-OLD"), _row(10.0, 4.0, 2.0, fp="FP-OLD")]
    logs(rows)
    opt.rebuild_saved(opt.RebuildRequest())
    (row,) = ss.list_entries()
    assert row["machine"]["unattributed"] is True and row["machine"]["source_fp"] == "FP-OLD"
    assert "machine unknown" in row["name"]
    for r in ss.list_entries():
        ss.delete_entry(r["id"])
    opt.rebuild_saved(opt.RebuildRequest(force=True, machine={"die": "G2", "config": "L40"}))
    (row,) = ss.list_entries()
    assert row["machine"]["die"] == "G2" and row["machine"]["declared"] is True
    assert row["machine"].get("fingerprint") is None        # a name, never an invented fingerprint
    assert row["machine"]["source_fp"] == "FP-OLD"
    assert row["name"].startswith("L40 ")


def test_rows_of_another_machine_inside_one_campaign_are_left_out_and_counted(logs):
    rows = [_row(10.0, 3.0, 2.0), _row(10.0, 4.0, 2.0), _row(10.0, 5.0, 2.0, fp="FP-ELSE")]
    logs(rows)
    opt.rebuild_saved(opt.RebuildRequest())
    res = ss.read_entry(ss.list_entries()[0]["id"])["result"]
    assert len(res["points"]) == 2 and res["rebuilt"]["rows_other_machine"] == 1


def test_a_campaign_with_no_computed_point_is_not_filed(logs):
    logs([_row(10.0, 3.0, 2.0, status="failed")])
    out = opt.rebuild_saved(opt.RebuildRequest())
    assert out["created"] == 0 and out["campaigns"][0]["skipped"] == "no computed point"


def test_the_rebuild_is_an_admin_route():
    from motor_ai_sim import auth
    assert auth.required_role("POST", "/api/optimization/saved/rebuild") == "admin"
