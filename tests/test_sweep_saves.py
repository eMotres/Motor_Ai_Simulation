"""Saved Sweeps survive deploys, belong to a user, and name their machine.

Incident 2026-10-29: the owner's L100 sweep finished in a sandbox and could not
be put back into the app; the "saved" store was ``<repo>/optimization_saves`` -
inside the server image, so every deploy dropped it.  Saves now live in the
caller's workspace (a mounted volume), carry the machine stamp, can be reopened
as the scan on screen, and an admin can import a result file produced elsewhere.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

import motor_ai_sim.routes.optimization as opt
from motor_ai_sim import sweep_saves as ss
from motor_ai_sim import workspace as W


def _pt(g, o, **kw):
    return {"geom_id": g, "op_index": o, "feasible": True, "T_em_Nm": 1.0,
            "overrides": {"tooth_width": 3.0 + g}, "current_a": 10.0, **kw}


def _result(run_id="sweep_4_1", die="CIANO56", fp="fp-A", with_machine=True, points=None):
    r = {"points": points if points is not None else [_pt(0, 0), _pt(1, 0)],
         "run_id": run_id, "n_total_points": 2, "n_built": 2, "n_geometries": 2,
         "steps_per_period": 24,
         "variables": [{"name": "tooth_width", "min": 3.0, "max": 4.0, "step": 1.0}],
         "operating_points": [{"current_a": 10.0, "gamma_deg": 0.0, "rpm": 4000}]}
    if with_machine:
        r["machine"] = {"fingerprint": fp, "die": die, "config": "L100", "duty": "S1",
                        "swept": ["tooth_width"]}
    return r


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """One workspace root + an (initially absent) legacy folder, and an idle scan slot."""
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(W, "root", lambda: root)
    legacy = tmp_path / "legacy"
    monkeypatch.setenv(ss.ENV_LEGACY_DIR, str(legacy))
    monkeypatch.setattr(opt, "_save_last_scan", lambda r: None)
    keep = dict(opt._scan_state)                          # noqa: SLF001
    opt._scan_state.update(running=False, result=None, run_id="")   # noqa: SLF001
    yield root, legacy
    opt._scan_state.clear()                               # noqa: SLF001
    opt._scan_state.update(keep)                          # noqa: SLF001


# ── 1. where it lives ────────────────────────────────────────────────────────
def test_saves_live_in_the_workspace_not_the_repo(home):
    root, _ = home
    out = opt.save_result(opt.SaveRequest(name="a", result=_result()))
    f = root / "optimization_saves" / f"{out['id']}.json"
    assert f.is_file()
    repo_dir = Path(opt.__file__).resolve().parents[3] / "optimization_saves"
    assert not (repo_dir / f"{out['id']}.json").exists()


def test_two_workspaces_do_not_see_each_other(tmp_path, monkeypatch):
    monkeypatch.setenv(ss.ENV_LEGACY_DIR, str(tmp_path / "none"))
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir(); b.mkdir()
    monkeypatch.setattr(W, "root", lambda: a)
    sid = opt.save_result(opt.SaveRequest(name="mine", result=_result()))["id"]
    monkeypatch.setattr(W, "root", lambda: b)
    assert opt.list_saved()["saved"] == []
    with pytest.raises(HTTPException) as e:
        opt.get_saved(sid)
    assert e.value.status_code == 404
    monkeypatch.setattr(W, "root", lambda: a)
    assert [r["id"] for r in opt.list_saved()["saved"]] == [sid]


def test_a_bad_id_cannot_leave_the_folder(home):
    for bad in ("../x", "a/b", "..", "a.b"):
        with pytest.raises(HTTPException) as e:
            opt.get_saved(bad)
        assert e.value.status_code == 400


# ── 2. the old folder ────────────────────────────────────────────────────────
def _legacy_file(legacy: Path, sid="20260101_000000_000001"):
    legacy.mkdir(exist_ok=True)
    (legacy / f"{sid}.json").write_text(json.dumps(
        {"id": sid, "name": "old scan", "created_at": "2026-01-01T00:00:00",
         "config": {}, "result": _result(run_id="old")}), encoding="utf-8")
    return sid


def test_legacy_folder_is_imported_once_and_left_in_place(home):
    root, legacy = home
    sid = _legacy_file(legacy)
    rows = opt.list_saved()["saved"]
    assert [r["id"] for r in rows] == [sid]
    assert (root / "optimization_saves" / ss.LEGACY_MARKER).is_file()
    assert (legacy / f"{sid}.json").is_file(), "copy, not move"
    # delete it here: a second look must not bring it back
    opt.delete_saved(sid)
    assert opt.list_saved()["saved"] == []


def test_legacy_without_a_stamp_is_unattributed_never_the_loaded_machine(home):
    _, legacy = home
    sid = "20260101_000000_000002"
    legacy.mkdir()
    (legacy / f"{sid}.json").write_text(json.dumps(
        {"id": sid, "name": "old", "config": {}, "result": _result(with_machine=False)}),
        encoding="utf-8")
    row = opt.list_saved()["saved"][0]
    assert row["machine"].get("unattributed") is True
    assert row["matches_loaded_machine"] is False


def test_legacy_goes_to_an_admin_only_on_a_multi_user_server(home, monkeypatch):
    root, legacy = home
    _legacy_file(legacy)
    monkeypatch.setattr(W, "layering", lambda: True)
    monkeypatch.setattr(W, "is_admin", lambda *a, **k: False)
    assert opt.list_saved()["saved"] == []
    assert not (root / "optimization_saves" / ss.LEGACY_MARKER).exists()
    monkeypatch.setattr(W, "is_admin", lambda *a, **k: True)
    assert len(opt.list_saved()["saved"]) == 1


# ── 3. save the finished last scan, with its machine ─────────────────────────
def test_save_last_keeps_the_scans_own_machine_stamp(home):
    opt._scan_state["result"] = _result(fp="fp-A", die="L100-die")    # noqa: SLF001
    out = opt.save_last_scan_as_saved(opt.SaveLastRequest(name="L100 sweep"))
    assert out["already_saved"] is False
    assert out["machine"]["die"] == "L100-die" and out["machine"]["fingerprint"] == "fp-A"
    got = opt.get_saved(out["id"])
    assert got["machine"]["swept"] == ["tooth_width"]
    assert got["result"]["operating_points"] and got["result"]["variables"]
    assert got["config"]["steps_per_period"] == 24


def test_save_last_twice_is_one_save(home):
    opt._scan_state["result"] = _result()                             # noqa: SLF001
    a = opt.save_last_scan_as_saved(opt.SaveLastRequest())
    b = opt.save_last_scan_as_saved(opt.SaveLastRequest())
    assert b["already_saved"] is True and b["id"] == a["id"]
    assert len(opt.list_saved()["saved"]) == 1


def test_save_last_refuses_while_running_or_with_nothing(home):
    with pytest.raises(HTTPException) as e:
        opt.save_last_scan_as_saved(opt.SaveLastRequest())
    assert e.value.status_code == 404
    opt._scan_state.update(running=True, result=_result())            # noqa: SLF001
    with pytest.raises(HTTPException) as e:
        opt.save_last_scan_as_saved(opt.SaveLastRequest())
    assert e.value.status_code == 409


def test_a_scan_with_no_stamp_is_not_stamped_with_the_loaded_machine(home):
    opt._scan_state["result"] = _result(with_machine=False)           # noqa: SLF001
    out = opt.save_last_scan_as_saved(opt.SaveLastRequest())
    assert out["machine"].get("unattributed") is True


# ── 4. reopen ────────────────────────────────────────────────────────────────
def test_reopen_makes_it_the_scan_on_screen_with_its_machine(home):
    sid = opt.save_result(opt.SaveRequest(name="old", result=_result(run_id="r_old", die="D1", fp="fp-1")))["id"]
    opt._scan_state["result"] = _result(run_id="r_last", die="D2", fp="fp-2")   # noqa: SLF001
    out = opt.open_saved(sid)
    cur = opt._scan_state["result"]                                    # noqa: SLF001
    assert cur["run_id"] == "r_old" == opt._scan_state["run_id"]      # noqa: SLF001
    assert cur["machine"]["die"] == "D1" and cur["restored_from_disk"] is True
    assert cur["reopened_from_saved"] == sid
    # the unsaved last scan was kept, not lost
    assert out["archived_previous"]
    runs = {r["run_id"] for r in opt.list_saved()["saved"]}
    assert runs == {"r_old", "r_last"}


def test_reopen_refuses_while_a_scan_runs(home):
    sid = opt.save_result(opt.SaveRequest(result=_result()))["id"]
    opt._scan_state["running"] = True                                  # noqa: SLF001
    with pytest.raises(HTTPException) as e:
        opt.open_saved(sid)
    assert e.value.status_code == 409


def test_list_says_whether_a_save_is_for_the_loaded_machine(home, monkeypatch):
    monkeypatch.setattr(opt, "_machine_stamp", lambda swept=(): {"fingerprint": "fp-now"})
    opt.save_result(opt.SaveRequest(result=_result(run_id="mine", fp="fp-now")))
    opt.save_result(opt.SaveRequest(result=_result(run_id="other", fp="fp-else")))
    by_run = {r["run_id"]: r for r in opt.list_saved()["saved"]}
    assert by_run["mine"]["matches_loaded_machine"] is True
    assert by_run["other"]["matches_loaded_machine"] is False


# ── 5. import a result produced elsewhere ────────────────────────────────────
def test_import_keeps_the_files_stamp_and_gets_a_run_id_if_missing(home):
    res = _result(die="SANDBOX-L100", fp="fp-sbx")
    res.pop("run_id")
    out = opt.import_scan_result(opt.ImportRequest(result=res, name="L100 sandbox", source="sandbox run"))
    assert out["machine"]["die"] == "SANDBOX-L100"
    assert out["run_id"].startswith("imported_")
    row = opt.list_saved()["saved"][0]
    assert row["imported"] is True and row["run_id"] == out["run_id"]


def test_import_accepts_a_saved_document_and_a_declared_machine(home):
    doc = {"id": "x", "name": "wrapped", "result": _result(with_machine=False)}
    out = opt.import_scan_result(opt.ImportRequest(
        result=doc, machine={"die": "L100", "config": "A", "duty": "S1", "junk": "x"}))
    assert out["machine"] == {"die": "L100", "config": "A", "duty": "S1", "declared": True}


def test_import_without_a_machine_is_unattributed(home):
    out = opt.import_scan_result(opt.ImportRequest(result=_result(with_machine=False)))
    assert out["machine"].get("unattributed") is True


def test_import_refuses_junk_and_idless_results(home):
    with pytest.raises(HTTPException) as e:
        opt.import_scan_result(opt.ImportRequest(result={"points": []}))
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        opt.import_scan_result(opt.ImportRequest(result=_result(points=[{"T_em_Nm": 1.0}])))
    assert e.value.status_code == 422


def test_import_reports_points_that_lack_ids(home):
    pts = [_pt(0, 0), {"T_em_Nm": 2.0}]
    out = opt.import_scan_result(opt.ImportRequest(result=_result(points=pts)))
    assert out["n_points_without_id"] == 1


def test_import_make_current_opens_it(home):
    out = opt.import_scan_result(opt.ImportRequest(result=_result(run_id="sbx"), make_current=True))
    assert out["opened"]["run_id"] == "sbx"
    assert opt._scan_state["result"]["run_id"] == "sbx"                # noqa: SLF001


@pytest.mark.parametrize("method, path", [
    ("POST", "/api/optimization/saved"), ("POST", "/api/optimization/saved/last"),
    ("POST", "/api/optimization/saved/import"), ("POST", "/api/optimization/saved/abc/open"),
    ("DELETE", "/api/optimization/saved/abc"),
])
def test_writes_are_admin_routes(method, path):
    from motor_ai_sim import auth
    assert auth.required_role(method, path) == "admin"


# ── 6. the progress payload attributes a RUNNING sweep ───────────────────────
def test_a_running_sweeps_machine_is_in_progress_and_machine_now_blanks_its_swept_keys(home, monkeypatch):
    seen = {}

    def stamp(swept=()):
        seen["swept"] = tuple(swept)
        return {"fingerprint": "fp-now"}

    monkeypatch.setattr(opt, "_machine_stamp", stamp)
    opt._scan_state.update(running=True, result=None,                  # noqa: SLF001
                           machine={"fingerprint": "fp-0", "swept": ["tooth_width"]})
    out = opt.scan_progress()
    assert out["machine"]["fingerprint"] == "fp-0"
    assert seen["swept"] == ("tooth_width",), "machine_now must blank the keys the run was started with"
