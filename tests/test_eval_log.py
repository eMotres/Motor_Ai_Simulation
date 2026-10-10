"""The optimizer's evaluation log is COMPLETE (schema 2) and stays a log.

The dataset exporter found three holes in ``.opt_dataset.jsonl`` /
``.scan_cache.jsonl``: only successful evals reached the files, rows carried no
run id / timestamp / objective, and nothing identified the build.  These tests
pin the repair and its two safety properties:

* failures are rows (``status`` failed / infeasible) with their inputs, but a
  failed line is NEVER served as a scan-cache hit and never reaches a surrogate;
* old (schema-1) rows still read exactly as before.

No solve runs: ``_subprocess_eval`` is driven through a fake ``Popen``.
"""
from __future__ import annotations

import json
import subprocess
import threading

import pytest

from motor_ai_sim.optimization import eval_log as EL
from motor_ai_sim.optimization import surrogate as S
from motor_ai_sim.routes import optimization as O

RES = {"T_em_Nm": 31.05, "efficiency": 0.963, "torque_per_mass_Nm_kg": 10.18,
       "T_ripple_pct": 3.2, "mass_total_kg": 3.05, "V_peak": 400.0,
       "P_loss_total_W": 120.0, "THD_LL_pct": 2.0, "Kt_Nm_per_Arms": 0.4,
       "nonlinear_converged": True, "eddy_settled": True}
BLINE = {"td_a": 9.0, "eff_a": 0.95, "td_b": 10.0, "eff_b": 0.94,
         "w_td": 0.01, "w_eff": 1.0, "norm": 1.00005, "bump_pct": 10.0,
         "current_a": 80.0, "current_b": 88.0}


@pytest.fixture
def ds(tmp_path, monkeypatch):
    p = tmp_path / ".opt_dataset.jsonl"
    monkeypatch.setattr(O, "_dataset_path", lambda: str(p))
    monkeypatch.setattr(EL, "_nl_checked", set())
    return p


@pytest.fixture
def cache_file(tmp_path, monkeypatch):
    p = tmp_path / ".scan_cache.jsonl"
    monkeypatch.setattr(O, "_eval_cache_path", lambda: str(p))
    monkeypatch.setattr(EL, "_nl_checked", set())
    O._EVAL_CACHE.clear()
    yield p
    O._EVAL_CACHE.clear()


def rows(p):
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


# ── row schema ───────────────────────────────────────────────────────────────
def test_ok_row_keeps_legacy_keys_and_gains_schema_2_keys(ds):
    with EL.campaign("descent") as c:
        EL.set_objective("baseline_line")
        EL.set_baseline_line(BLINE)
        EL.set_limits(ripple_max_pct=5.0, v_peak_limit_v=500.0)
        O._log_eval({"tooth_width": 9.2}, 91.9, 16.0, {"ok": True, "res": RES},
                    solve={"steps": 18}, sampling_purpose="optimization",
                    elapsed_s=12.3456)
    (r,) = rows(ds)
    for k in ("overrides", "current_a", "gamma_deg", "ripple", "torque", "eff",
              "td", "mass", "v_peak", "p_loss", "thd_ll", "kt", "cfg_fp"):
        assert k in r, k                                  # schema-1 keys intact
    assert r["ripple"] == 3.2 and r["td"] == 10.18
    assert r["schema"] == EL.SCHEMA_VERSION == 2
    assert r["status"] == "ok"
    assert r["campaign_id"] == c.id and r["campaign_kind"] == "descent"
    assert r["stage"] == EL.STAGE_FREE or r["stage"] is None
    assert r["ts"].endswith("Z") and "T" in r["ts"]
    assert r["elapsed_s"] == pytest.approx(12.346, abs=1e-3)
    assert set(r["build"]) == {"git_sha", "version", "built_at"}
    assert r["solve"] == {"steps": 18}
    assert r["objective"] == "baseline_line"
    assert r["baseline_line"]["w_eff"] == 1.0
    cons = r["constraints"]
    assert cons["ripple_ok"] is True and cons["v_peak_ok"] is True
    assert cons["feasible"] is True and cons["nonlinear_converged"] is True


def test_objective_value_is_the_descent_cost_perpendicular_distance(ds):
    with EL.campaign("auto"):
        EL.set_objective("baseline_line")
        EL.set_baseline_line(BLINE)
        O._log_eval({}, 80.0, 0.0, {"ok": True, "res": RES})
    (r,) = rows(ds)
    _cost, F = O._descent_cost(RES, {"_bline": BLINE}, 5.0, 1.0, 1.0, 1.0, 1e9)
    assert r["objective_value"] == pytest.approx(F, rel=1e-12)


def test_constraint_verdicts_flag_violations(ds):
    bad = dict(RES, T_ripple_pct=9.0, V_peak=900.0, demag_warning="Br lost")
    with EL.campaign("descent"):
        EL.set_limits(ripple_max_pct=5.0, v_peak_limit_v=500.0)
        O._log_eval({}, 80.0, 0.0, {"ok": True, "res": bad})
    (r,) = rows(ds)
    c = r["constraints"]
    assert c["ripple_ok"] is False and c["v_peak_ok"] is False
    assert c["demag_ok"] is False and c["feasible"] is False
    assert r["status"] == "ok"            # solved; the verdicts say it is bad


def test_final_resolve_stage_is_inferred_from_the_sampling_purpose(ds):
    with EL.campaign("auto", stage=EL.STAGE_SEEDED):
        O._log_eval({}, 80.0, 0.0, {"ok": True, "res": RES},
                    sampling_purpose="cogging_quality")
        O._log_eval({}, 80.0, 0.0, {"ok": True, "res": RES},
                    sampling_purpose="optimization")
    a, b = rows(ds)
    assert a["stage"] == EL.STAGE_FINAL
    assert b["stage"] == EL.STAGE_SEEDED


def test_a_row_outside_any_campaign_has_null_campaign(ds):
    O._log_eval({}, 80.0, 0.0, {"ok": True, "res": RES})
    (r,) = rows(ds)
    assert r["campaign_id"] is None and r["status"] == "ok"


# ── failures are rows ────────────────────────────────────────────────────────
class _Popen:
    """Fake worker process: ``communicate`` returns / raises what the test set."""
    out = ""
    err = ""
    rc = 0
    raises = None
    seen_env = None

    def __init__(self, *a, **kw):
        self.pid = 4242
        self.returncode = type(self).rc

    def communicate(self, input=None, timeout=None):
        if type(self).raises is not None:
            raise type(self).raises
        return type(self).out, type(self).err

    def kill(self):
        pass


@pytest.fixture
def fake_worker(monkeypatch):
    monkeypatch.delenv("SOLVE_POOL", raising=False)
    from motor_ai_sim import solve_pool as SP
    monkeypatch.setattr(SP, "enabled", lambda: False)
    _Popen.out, _Popen.err, _Popen.rc, _Popen.raises = "", "", 0, None
    monkeypatch.setattr(subprocess, "Popen", _Popen)
    # A fake worker "takes" ~0 s: keep that out of the process-wide measured
    # eval-cost window (it would make later cost quotes read 0 s per eval).
    monkeypatch.setattr(O, "_record_eval_seconds", lambda *a, **k: None)
    return _Popen


def _eval(**kw):
    return O._subprocess_eval({"tooth_width": 9.4}, 80.0, 12, 120.0,
                              gamma_deg=5.0, **kw)


def test_an_exception_inside_an_eval_is_logged_as_failed(ds, fake_worker,
                                                         monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError(r"mesh exploded in C:\Users\vadim\x\mesh.py line 4")
    monkeypatch.setattr(subprocess, "Popen", boom)
    with EL.campaign("scan") as c:
        out = _eval()
    # the caller still sees exactly the legacy failure payload
    assert out == {"ok": False,
                   "error": r"mesh exploded in C:\Users\vadim\x\mesh.py line 4"}
    (r,) = rows(ds)
    assert r["status"] == "failed" and r["error_class"] == "RuntimeError"
    assert "vadim" not in r["error"] and "<path>" in r["error"]
    assert r["overrides"] == {"tooth_width": 9.4}      # inputs are kept
    assert r["current_a"] == 80.0 and r["gamma_deg"] == 5.0
    assert r["campaign_id"] == c.id and r["cfg_fp"]
    assert "eff" not in r and "torque" not in r        # no invented metrics
    assert r["solve"]["steps"] == 12 and r["elapsed_s"] >= 0


def test_timeout_is_a_failed_row(ds, fake_worker):
    fake_worker.raises = subprocess.TimeoutExpired("w", 1)
    out = _eval()
    assert out == {"ok": False, "error": "timeout"}
    (r,) = rows(ds)
    assert (r["status"], r["error_class"]) == ("failed", "timeout")


def test_worker_crash_without_result_is_a_failed_row(ds, fake_worker):
    fake_worker.rc = -11
    fake_worker.err = "Segmentation fault"
    out = _eval()
    assert not out["ok"]
    (r,) = rows(ds)
    assert (r["status"], r["error_class"]) == ("failed", "worker_crash")
    assert "Segmentation fault" in r["error"]


@pytest.mark.parametrize("msg, status, klass", [
    ("geometry violation: magnet cuts the iron", "infeasible", "geometry_invalid"),
    ("infeasible winding: 40 turns cannot fit the slot even clamped",
     "infeasible", "geometry_invalid"),
    ("mesh budget: 9e6 triangles", "infeasible", "mesh_budget"),
    ("unconverged FEM frames [3] of 12 (max nonlinear resid 1 vs tol 1e-6)"
     " - eval rejected", "failed", "non_convergence"),
    ("something odd", "failed", "exception"),
])
def test_worker_rejections_are_classified(ds, fake_worker, msg, status, klass):
    fake_worker.out = "@@RESULT@@" + json.dumps({"ok": False, "error": msg})
    out = _eval()
    assert out["ok"] is False
    (r,) = rows(ds)
    assert (r["status"], r["error_class"]) == (status, klass)
    assert r["error"].startswith(msg[:30])


def test_a_worker_exception_class_is_kept(ds, fake_worker):
    fake_worker.out = "@@RESULT@@" + json.dumps(
        {"ok": False, "error": "x", "error_class": "KeyError"})
    _eval()
    (r,) = rows(ds)
    assert r["error_class"] == "KeyError"


def test_a_nonphysical_ok_result_is_logged_as_failed(ds, fake_worker):
    bad = dict(RES, efficiency=0.0)
    fake_worker.out = "@@RESULT@@" + json.dumps({"ok": True, "res": bad})
    out = _eval()
    assert out["ok"] is False
    (r,) = rows(ds)
    assert (r["status"], r["error_class"]) == ("failed", "non_physical")


def test_ok_eval_through_the_worker_path(ds, fake_worker):
    fake_worker.out = "@@RESULT@@" + json.dumps({"ok": True, "res": RES})
    out = _eval()
    assert out["ok"]
    (r,) = rows(ds)
    assert r["status"] == "ok" and r["eff"] == 0.963
    assert r["sampling_purpose"] == "optimization"


def test_log_false_and_cancel_write_nothing(ds, fake_worker):
    fake_worker.raises = subprocess.TimeoutExpired("w", 1)
    O._subprocess_eval({}, 80.0, 12, 120.0, _log=False)
    O._log_eval({}, 80.0, 0.0, {"ok": False, "cancelled": True,
                                "error": "cancelled"})
    assert not ds.exists()


def test_prefence_reject_is_an_infeasible_pre_solve_row(ds):
    with EL.campaign("auto"):
        O._log_prefence_reject({"tooth_width": 99.0}, 80.0, 0.0,
                               "tooth_width 99 outside bound")
    (r,) = rows(ds)
    assert r["status"] == "infeasible" and r["pre_solve"] is True
    assert r["overrides"] == {"tooth_width": 99.0}


# ── the scan cache stays a cache ─────────────────────────────────────────────
def test_failed_lines_are_never_served_as_cache_hits(cache_file):
    ok_res = {"ok": True, "res": dict(RES, current_a=80.0)}
    cache_file.write_text("\n".join(json.dumps(x) for x in [
        {"k": "old", "v": ok_res},                                  # schema 1
        {"k": "new", "v": ok_res, "m": {"schema": 2, "status": "ok"}},
        {"k": "failed", "m": {"schema": 2, "status": "failed"},
         "inputs": {"overrides": {}}},                              # log line
        {"k": "tampered", "v": ok_res, "m": {"status": "failed"}},  # belt+braces
        {"k": "infeasible", "v": ok_res, "m": {"status": "infeasible"}},
    ]) + "\n", encoding="utf-8")
    O._load_eval_cache()
    assert sorted(O._EVAL_CACHE.keys()) == ["new", "old"]


def test_store_eval_adds_provenance_and_failure_logging_adds_a_log_line(
        cache_file):
    with EL.campaign("scan") as c:
        O._store_eval("k1", {"ok": True, "res": RES})
        O._log_eval_failure_cache("k2", {"ok": False, "error": "timeout"},
                                  overrides={"a": 1.0}, current_a=80.0,
                                  gamma_deg=0.0)
        O._log_eval_failure_cache("k3", {"ok": True, "res": RES})  # ignored
    ok, bad = rows(cache_file)
    assert ok["k"] == "k1" and ok["v"]["res"]["T_em_Nm"] == 31.05
    assert ok["m"]["campaign_id"] == c.id and ok["m"]["status"] == "ok"
    assert bad["k"] == "k2" and "v" not in bad
    assert bad["m"]["status"] == "failed" and bad["m"]["error_class"] == "timeout"
    assert bad["inputs"]["overrides"] == {"a": 1.0}
    # reload: only the ok line becomes a cache entry
    O._EVAL_CACHE.clear()
    O._load_eval_cache()
    assert list(O._EVAL_CACHE.keys()) == ["k1"]
    assert O._EVAL_CACHE.get("k2") is None


def test_servable_cache_record_rules():
    assert EL.servable_cache_record({"k": "a", "v": {"ok": True}})
    assert not EL.servable_cache_record({"k": "a"})
    assert not EL.servable_cache_record({"k": "a", "v": None})
    assert not EL.servable_cache_record({"v": {"ok": True}})
    assert not EL.servable_cache_record(
        {"k": "a", "v": {}, "m": {"status": "failed"}})


# ── campaign id across workers ───────────────────────────────────────────────
def test_campaign_id_reaches_every_pool_worker_and_differs_per_run(ds):
    from motor_ai_sim.workspace import (WorkspaceThreadPoolExecutor
                                        as TPE)

    def one_run():
        with EL.campaign("descent") as c:
            EL.set_stage(EL.STAGE_SEEDED)          # set by the run's thread…
            with TPE(max_workers=6) as ex:
                futs = [ex.submit(O._log_eval, {"i": float(i)}, 80.0, 0.0,
                                  {"ok": True, "res": RES}) for i in range(24)]
                for f in futs:
                    f.result()
            return c.id

    a, b = one_run(), one_run()
    rs = rows(ds)
    assert len(rs) == 48 and a != b
    assert {r["campaign_id"] for r in rs[:48]} == {a, b}
    assert sum(r["campaign_id"] == a for r in rs) == 24
    assert all(r["stage"] == EL.STAGE_SEEDED for r in rs)   # …seen by workers


def test_optimizer_job_decorator_opens_a_campaign():
    seen = []

    @O._optimizer_job("scan")
    def body():
        c = EL.current()
        seen.append((c.id, c.kind, c.stage))

    body()
    body()
    assert seen[0][1] == "scan" and seen[0][2] == EL.STAGE_SWEEP
    assert seen[0][0] != seen[1][0]
    assert EL.current() is None                      # closed afterwards


def test_a_nested_campaign_keeps_the_outer_id():
    with EL.campaign("auto") as outer:
        with EL.campaign("refine") as inner:
            assert inner is outer


# ── old rows still read ──────────────────────────────────────────────────────
def test_schema_1_rows_read_as_ok_and_feed_the_readers(ds):
    old = {"overrides": {"tooth_width": 9.2}, "current_a": 91.9, "gamma_deg": 16.0,
           "torque": 31.0, "mass": 3.0, "eff": 0.963, "td": 10.17,
           "ripple": 12.3}
    with EL.campaign("descent"):
        O._log_eval({"tooth_width": 9.4}, 91.9, 16.0, {"ok": True, "res": RES})
        O._log_eval({"tooth_width": 99.0}, 91.9, 16.0,
                    {"ok": False, "error": "timeout"})
    with open(ds, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(old) + "\n")
        fh.write('{"overrides": {"torn')                 # a torn last line
    recs = S.load_dataset(str(ds))
    assert len(recs) == 2                                # failed row excluded
    assert all(EL.is_ok_row(r) for r in recs)
    assert len(S.load_dataset(str(ds), include_failed=True)) == 3
    assert EL.row_status(old) == "ok"
    # the backfill reader (matches on overrides/current/metrics) still works
    pts = [{"td": 10.17, "eff": 0.963, "ripple": 12.3,
            "overrides": {"tooth_width": 9.2}, "current_a": 91.9,
            "gamma_deg": None}]
    assert O._backfill_point_metrics(pts) == 1
    assert pts[0]["torque"] == pytest.approx(31.0)


def test_warm_start_skips_failed_rows(ds):
    fp = O._config_fingerprint()
    with EL.campaign("descent"):
        for i in range(4):
            O._log_eval({"tooth_width": 9.0 + i / 10}, 80.0, 0.0,
                        {"ok": True, "res": dict(RES, T_ripple_pct=1.0)})
        for i in range(10):
            O._log_eval({"tooth_width": 20.0 + i}, 80.0, 0.0,
                        {"ok": False, "error": "timeout"})
    specs = [{"name": "tooth_width", "x0": 9.0, "lo": 5.0, "hi": 30.0}]
    base = {"_bline": BLINE}
    seed = O._auto_warm_start(S.load_dataset(str(ds)), specs, fp, 80.0, 0.0,
                              5.0, base)
    assert seed is not None and seed["n"] == 4


# ── the writer ───────────────────────────────────────────────────────────────
def test_append_line_is_whole_under_concurrent_writers(tmp_path):
    p = str(tmp_path / "x.jsonl")
    big = "x" * 4000

    def w(n):
        for i in range(100):
            EL.append_line(p, {"w": n, "i": i, "pad": big})

    ts = [threading.Thread(target=w, args=(n,)) for n in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    lines = open(p, encoding="utf-8").read().splitlines()
    assert len(lines) == 800
    assert all(json.loads(ln)["pad"] == big for ln in lines)


def test_append_line_does_not_glue_onto_a_torn_last_line(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"a": 1}\n{"torn', encoding="utf-8")
    EL.append_line(str(p), {"b": 2})
    good = [json.loads(ln) for ln in p.read_text().splitlines()
            if ln.endswith("}") and ln.startswith("{")]
    assert {"b": 2} in good


def test_clean_makes_rows_strict_json():
    out = EL.clean({"a": float("nan"), "b": [float("inf"), 1.5], "c": {"d": None}})
    assert out == {"a": None, "b": [None, 1.5], "c": {"d": None}}
    json.dumps(out, allow_nan=False)


def test_sanitize_error_is_short_pathless_and_stackless():
    tb = ("Traceback (most recent call last):\n  File \"/srv/motres/app/x.py\","
          " line 3, in f\n    g()\nValueError: bad value in /srv/motres/ws/u1/c.yaml")
    s = EL.sanitize_error(tb)
    assert s == "ValueError: bad value in <path>"
    assert len(EL.sanitize_error("e" * 5000)) == 300
    assert "Users" not in EL.sanitize_error(r"open C:\Users\someone\a.txt failed")
