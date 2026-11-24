"""The magnet temperature of a Sweep / Descent / Auto run is visible and recorded (owner 2026-11-24: real magnet temperature everywhere).

What the run uses is NOT changed: a blank field still means the assigned card as the library writes it.  Pinned here (payload only, the
solver is a stand-in): the typed number is forwarded to the worker, a blank one adds nothing and keeps every key and digest, the resolver
answers with the used value / source / card, a typed value is part of the run identity (digest, token, eval-cache pins) so two runs at
different magnet temperatures are never ranked together or share cached points, scan params and every point say what was used, and a
record from before the field is read as the card."""
from __future__ import annotations

import hashlib
import json

import pytest
from fastapi import HTTPException

from motor_ai_sim import run_settings as RS
from motor_ai_sim.config import get_config
from motor_ai_sim.routes import optimization as O

PRE_PR_SIG = 229510273252169          # run_settings.resolve("tab", TAB, ...)["sig"] computed with the BASE code (custom run, scope 3)
PRE_PR_TOKEN = "39fcecfcc149d6322934d3c802279fc9"   # its resolution token, same source
GEO_VARS = [{"name": "tooth_width", "min": 2.0, "max": 3.0, "mode": "sweep", "step": 0.5}]
OPS = [{"gamma_deg": 0.0, "current_a": 20.0, "rpm": 1000.0}]
TAB = {"steps_per_period": 48, "gap_layers": 1, "mesh_size_mm": 1.8, "min_size_mm": 0.2, "n_sectors": 2, "coil_rel": 2.0,
       "rotor_eddy": True, "demag": False}


def _rs(mag=None, mode="sweep", **kw):
    ov = {k: v for k, v in TAB.items() if not (mode == "descent" and k == "demag")}      # a Descent run reads the machine's demag
    return O.RunSettingsIn(preset="tab", overrides=ov, magnet_temp_c=mag, **kw)


# ── the resolver answers with the used value, the source and the card ─────────────────────────────────────────────
def test_resolve_reports_the_card_when_blank_and_the_typed_value_when_typed():
    blank = RS.resolve("tab", dict(TAB), get_config(), "sweep")
    m = blank["magnet"]
    assert m["requested"] is None and m["source"] == "card" and m["used_c"] == m["card_c"]
    assert m["card"]                                                   # the assigned card's name
    typed = RS.resolve("tab", dict(TAB), get_config(), "sweep", magnet_temp_c=60.0)["magnet"]
    assert typed["requested"] == 60.0 and typed["used_c"] == 60.0 and typed["source"] == "typed" and typed["card"] == m["card"]


# ── identity: blank keeps today's digest and token, typed changes them ────────────────────────────────────────────
def test_blank_keeps_the_old_digest_and_token_and_a_typed_value_changes_them():
    old_pair = int(hashlib.md5(json.dumps([48, 1.0]).encode()).hexdigest()[:12], 16)
    assert RS.identity_sig(48, 1) == old_pair                           # the pair digest of every stored sweep
    assert RS.identity_sig(48, 1, None, None, None) == old_pair
    blank = RS.resolve("tab", dict(TAB), get_config(), "sweep")
    # the digest and token a blank run had BEFORE this change (computed with the base code, pinned as literals)
    assert blank["sig"] == PRE_PR_SIG and blank["token"] == PRE_PR_TOKEN
    same = RS.resolve("tab", dict(TAB), get_config(), "sweep", magnet_temp_c=None)
    a = RS.resolve("tab", dict(TAB), get_config(), "sweep", magnet_temp_c=60.0)
    b = RS.resolve("tab", dict(TAB), get_config(), "sweep", magnet_temp_c=90.0)
    assert blank["sig"] == same["sig"] and blank["token"] == same["token"]
    assert len({blank["sig"], a["sig"], b["sig"]}) == 3                # three runs, three identities: never ranked together
    assert len({blank["token"], a["token"], b["token"]}) == 3
    assert a["sig"] == RS.resolve("tab", dict(TAB), get_config(), "sweep", magnet_temp_c=60.0)["sig"]   # stable
    assert RS.full_sig({"x": 1}, 2) == RS.full_sig({"x": 1}, 2, None) != RS.full_sig({"x": 1}, 2, 60.0)  # the custom-run digest too


def test_a_run_that_carries_the_digest_shown_without_the_magnet_is_refused_when_one_is_typed():
    shown = O._resolve_run_settings(_rs(None), "sweep")
    with pytest.raises(HTTPException) as e:
        O._resolve_run_settings(_rs(60.0, expect_sig=shown["sig"]), "sweep")
    assert e.value.status_code == 409


def test_the_eval_cache_pins_and_key_carry_a_typed_temperature_and_nothing_when_blank():
    assert O._scan_pins(1000.0, None) == {"rpm": 1000.0}                # unchanged: the old key
    assert O._scan_pins(1000.0, 60.0) == {"rpm": 1000.0, "magnet_temp_c": 60.0}
    def key(pins):
        return O._eval_cache_key({"tooth_width": 2.0}, 20.0, 48, 120.0, 1.0, 0.0, 1.8, 0.2, 2, False, False, "fp", pins=pins)
    assert key(O._scan_pins(1000.0, None)) == key({"rpm": 1000.0})
    assert len({key(O._scan_pins(1000.0, None)), key(O._scan_pins(1000.0, 60.0)), key(O._scan_pins(1000.0, 90.0))}) == 3


# ── the typed number is forwarded; blank adds nothing ─────────────────────────────────────────────────────────────
def test_the_body_and_the_run_settings_must_agree_and_the_grade_limit_is_kept():
    assert O._effective_magnet_temp(None, None) is None
    assert O._effective_magnet_temp(None, {"magnet": {"requested": 60.0}}) == 60.0
    assert O._effective_magnet_temp(60.0, {"magnet": {"requested": 60.0}}) == 60.0
    with pytest.raises(HTTPException) as e:
        O._effective_magnet_temp(60.0, {"magnet": {"requested": 90.0}})
    assert e.value.status_code == 422
    with pytest.raises(HTTPException):
        O._effective_magnet_temp(900.0, None)                           # above the grade's maximum working temperature


def _scan(**kw):
    body = {"variables": GEO_VARS, "operating_points": OPS, "steps_per_period": 48, "gap_layers": 1, "run_id": "mt-scan"}
    body.update(kw)
    return O.ScanRequest(**body)


def test_scan_forwards_a_typed_temperature_from_the_run_settings_and_blank_adds_nothing():
    off = O.prepare_scan(_scan(run_settings=_rs(None)))
    assert "magnet_temp_c" not in off["kwargs"]
    on = O.prepare_scan(_scan(run_settings=_rs(60.0)))
    assert on["kwargs"]["magnet_temp_c"] == 60.0
    assert len(on["args"]) == len(off["args"])
    assert O.prepare_scan(_scan(run_settings=_rs(60.0), magnet_temp_c=60.0))["kwargs"]["magnet_temp_c"] == 60.0


def _descent(**kw):
    body = {"variables": [{"name": "tooth_width", "min": 0.0, "max": 0.0}],
            "operating_point": {"current_a": 10.0, "gamma_deg": 5.0, "rpm": 3000.0},
            "max_iters": 3, "gap_layers": 2, "run_id": "mt-descent"}
    body.update(kw)
    return O.DescentRequest(**body)


def test_descent_forwards_the_temperature_to_the_job_and_records_it():
    off = O.prepare_descent(_descent(run_settings=_rs(None, "descent")))
    assert "run_magnet_temp_c" not in off["kwargs"]
    assert off["init_state"]["magnet"]["source"] == "card" and off["init_state"]["magnet"]["requested"] is None
    on = O.prepare_descent(_descent(run_settings=_rs(60.0, "descent")))
    assert on["kwargs"]["run_magnet_temp_c"] == 60.0
    m = on["init_state"]["magnet"]
    assert (m["requested"], m["used_c"], m["source"]) == (60.0, 60.0, "typed")
    assert on["init_state"]["eval_params"]["magnet"] == m               # restored with the other eval parameters


def test_the_job_scope_hands_the_temperature_to_every_eval_and_nothing_else_changes():
    assert O._RUN_MAGNET_T.get() is None
    seen = {}

    @O._optimizer_job("descent")
    def worker(x):
        seen["t"] = O._RUN_MAGNET_T.get()
        return x
    assert worker(1, run_magnet_temp_c=60.0) == 1 and seen["t"] == 60.0
    assert O._RUN_MAGNET_T.get() is None                                # the scope ends with the job
    worker(2)
    assert seen["t"] is None


def test_an_eval_without_its_own_temperature_takes_the_runs(monkeypatch):
    from types import SimpleNamespace
    from motor_ai_sim import solve_pool as SP
    got = []
    monkeypatch.setattr(SP, "enabled", lambda: True)
    monkeypatch.setattr(SP, "run_command", lambda cmd, **k: got.append(json.loads(k["input_text"])) or SimpleNamespace(
        cancelled=False, cancelled_while_waiting=False, stdout="", stderr="", returncode=1, wall_s=0.0))
    monkeypatch.setattr(O, "_log_eval", lambda *a, **k: None)
    O._subprocess_eval({}, 10.0, 24, 120.0, _log=False)                  # outside a run: the request names no magnet temperature
    tok = O._RUN_MAGNET_T.set(60.0)
    try:
        O._subprocess_eval({}, 10.0, 24, 120.0, _log=False)              # inside a typed Descent / Auto run: the run's
        O._subprocess_eval({}, 10.0, 24, 120.0, _log=False, magnet_temp_c=75.0)   # an explicit argument wins
    finally:
        O._RUN_MAGNET_T.reset(tok)
    assert len(got) == 3
    assert "magnet_temp_c" not in got[0] and got[1]["magnet_temp_c"] == 60.0 and got[2]["magnet_temp_c"] == 75.0


# ── scan_params and every point say what was used ─────────────────────────────────────────────────────────────────
@pytest.fixture()
def sweep(monkeypatch):
    cache = {}
    monkeypatch.setattr(O, "_EVAL_CACHE", cache)
    monkeypatch.setattr(O, "_store_eval", lambda k, v: cache.setdefault(k, v))
    calls, saved = [], {}

    def fake_eval(overrides, current_a, steps, coil_temp_c, **kw):
        calls.append(dict(kw))
        tw = float(overrides.get("tooth_width", 2.0))
        return {"ok": True, "res": {"T_em_Nm": current_a * tw, "efficiency": 0.9, "torque_per_mass_Nm_kg": 1.0, "T_ripple_pct": 3.0,
                                    "mass_total_kg": 1.0, "P_loss_total_W": 10.0, "V_peak": 10.0}}
    monkeypatch.setattr(O, "_subprocess_eval", fake_eval)
    monkeypatch.setattr(O, "_geometry_reject", lambda ov: None)
    monkeypatch.setattr(O, "_save_last_scan", lambda r: None)
    monkeypatch.setattr(O._sweep_saves, "autosave", lambda r, **k: saved.setdefault("result", r))
    monkeypatch.setattr(O, "_job_workers", lambda: 2)

    def run(**kw):
        calls.clear()
        saved.clear()
        with O._scan_lock:
            O._scan_state.update({"running": True, "done": 0, "total": 0, "result": None, "points": [], "run_id": "mt-run",
                                  "error": None, "cancel": False, "cached": 0})
        O._scan_worker(GEO_VARS, OPS, 48, 120.0, 100.0, 24, 1, "mt-run", mesh_size_mm=4.0, min_size_mm=0.3, n_sectors=-1,
                       gap_layers=1.0, element_order=2, **kw)
        with O._scan_lock:
            assert not O._scan_state.get("error"), O._scan_state.get("error")
        return saved["result"], list(calls)
    return run


def test_a_sweep_stores_requested_used_and_source_in_scan_params_and_on_every_point(sweep):
    res, calls = sweep(magnet_temp_c=60.0)
    m = res["scan_params"]["magnet"]
    assert (m["requested"], m["used_c"], m["source"]) == (60.0, 60.0, "typed") and m["card"]
    assert res["points"] and all(p["magnet_temp_used_c"] == 60.0 for p in res["points"])
    assert all(c.get("magnet_temp_c") == 60.0 for c in calls)           # forwarded to every eval
    res, calls = sweep()                                                # blank: the card as written, solved exactly as before
    m = res["scan_params"]["magnet"]
    assert m["requested"] is None and m["source"] == "card" and m["used_c"] == m["card_c"]
    assert all("magnet_temp_c" not in c or c["magnet_temp_c"] is None for c in calls)
    assert m["card_c"] is not None and m["used_c"] == m["card_c"]
    assert res["points"] and all(p["magnet_temp_used_c"] == m["card_c"] for p in res["points"])


def test_two_sweeps_at_different_magnet_temperatures_do_not_share_cached_points(sweep):
    a, ca = sweep(magnet_temp_c=60.0)
    b, cb = sweep(magnet_temp_c=90.0)
    assert len(cb) > 0                                                  # the second sweep solved its own points, no hit on the first's
    assert {p["magnet_temp_used_c"] for p in b["points"]} == {90.0}
    c, cc = sweep(magnet_temp_c=60.0)
    assert len(cc) == 0                                                 # the same temperature IS served from the cache


# ── review round: caches, re-checks, seeding, body-only value, limits ────────────────────────────────────────────────
def test_the_screen_cache_pins_carry_a_typed_temperature_only_when_typed():
    assert O._run_cache_pins(1000.0, "4S") == {"rpm": 1000.0, "connection": "4S"}           # the key an empty field always had
    assert O._run_cache_pins(None, None, None) == {"rpm": None, "connection": None}
    assert O._run_cache_pins(1000.0, "4S", 60.0) == {"rpm": 1000.0, "connection": "4S", "magnet_temp_c": 60.0}
    def key(pins):
        return O._eval_cache_key({"tooth_width": 2.0}, 20.0, 48, 120.0, 1.0, 0.0, 1.8, 0.2, 2, False, False, "fp", pins=pins)
    assert len({key(O._run_cache_pins(1000.0, "4S")), key(O._run_cache_pins(1000.0, "4S", 60.0)),
                key(O._run_cache_pins(1000.0, "4S", 90.0))}) == 3


def _stored_run(magnet=None):
    ep = {"steps_per_period": 48, "coil_temp_c": 120.0, "n_sectors": 2, "gap_layers": 1}
    if magnet is not None:
        ep["magnet"] = magnet
    return {"running": False, "run_id": "r1", "eval_params": ep, "operating_point": {"current_a": 10.0, "gamma_deg": 0.0, "rpm": 1000.0},
            "best": {"overrides": {"tooth_width": 2.5}, "metrics": {"current_a": 10.0}}}


def test_the_best_point_recheck_solves_at_the_runs_typed_magnet_temperature():
    req = O.DescentValidateRequest(run_id="r1", target="best")
    _p, args, _prov = O._descent_recheck_inputs(_stored_run({"requested": 60.0, "used_c": 60.0, "source": "typed"}), req)
    assert args["magnet_temp_c"] == 60.0
    _p, args, _prov = O._descent_recheck_inputs(_stored_run({"requested": None, "used_c": 120.0, "source": "card"}), req)
    assert "magnet_temp_c" not in args                                  # a card run: re-checked as before
    _p, args, _prov = O._descent_recheck_inputs(_stored_run(None), req)
    assert "magnet_temp_c" not in args                                  # a run from before the field


def test_a_typed_sweep_is_validated_at_its_own_temperature_and_not_seeded(monkeypatch):
    full = {"n_sectors": 2, "gap_layers": 1, "end_winding": 0.0, "rotor_eddy": False, "hi_fidelity": False, "structured_gap": False,
            "airgap_macro": False, "iron_template": True, "geo_mesh": True, "element_order": 2, "demag": False, "cfg_fp": "fp",
            "sampling_purpose": "optimization", "coil_temp_c": 120.0}
    result = {"points": [{"feasible": True, "current_a": 10.0, "gamma_deg": 0.0, "overrides": {"tooth_width": 2.0}, "T_em_Nm": 1.0}],
              "steps_per_period": 48, "scan_params": {**full, "magnet": {"requested": 60.0, "used_c": 60.0, "source": "typed"}}}
    cache = {}
    monkeypatch.setattr(O, "_EVAL_CACHE", cache)
    with O._scan_lock:
        saved = O._scan_state.get("result")
        O._scan_state["result"] = result
    try:
        out = O.seed_cache(O.SeedCacheRequest())
        assert out["seeded"] == 0 and "typed magnet temperature" in out["error"] and cache == {}
        result["scan_params"]["magnet"] = {"requested": None, "used_c": 120.0, "source": "card"}
        assert O.seed_cache(O.SeedCacheRequest())["seeded"] == 1       # a card sweep seeds exactly as before
    finally:
        with O._scan_lock:
            O._scan_state["result"] = saved


def test_a_value_typed_only_in_the_body_is_part_of_the_identity():
    blank = O._resolve_run_settings(_rs(None), "sweep")
    via_rs = O._resolve_run_settings(_rs(60.0), "sweep")
    via_body = O._resolve_run_settings(_rs(None), "sweep", 60.0)
    assert via_body["sig"] == via_rs["sig"] != blank["sig"] and via_body["token"] == via_rs["token"]
    assert via_body["magnet"]["requested"] == 60.0 and via_body["magnet"]["source"] == "typed"
    with pytest.raises(HTTPException) as e:
        O._resolve_run_settings(_rs(90.0), "sweep", 60.0)               # two different numbers: refused
    assert e.value.status_code == 422
    plan = O.prepare_scan(_scan(run_settings=_rs(None), magnet_temp_c=60.0))
    assert plan["kwargs"]["magnet_temp_c"] == 60.0
    assert plan["kwargs"]["mesh_preset"]["sig"] == via_rs["sig"]         # the stamp carries the identity WITH the value
    assert plan["kwargs"]["mesh_preset"]["magnet"]["requested"] == 60.0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -61.0, 351.0])
def test_a_magnet_temperature_that_is_not_finite_or_out_of_range_is_refused(bad):
    from pydantic import ValidationError
    for make in (lambda: O.RunSettingsIn(preset="tab", overrides=dict(TAB), magnet_temp_c=bad),
                 lambda: _scan(magnet_temp_c=bad), lambda: _descent(magnet_temp_c=bad),
                 lambda: O.BaselineRequest(operating_point={"current_a": 10.0, "gamma_deg": 0.0, "rpm": 1000.0}, magnet_temp_c=bad),
                 lambda: O.AutoOptRequest(max_ripple_pct=5.0, magnet_temp_c=bad)):
        with pytest.raises(ValidationError):
            make()
    assert RS.magnet_block(bad)["source"] == "card"                      # and never reported as typed
    for ok in (-60.0, 0.0, 350.0):
        assert O.RunSettingsIn(preset="tab", overrides=dict(TAB), magnet_temp_c=ok).magnet_temp_c == ok


def test_used_value_comes_from_the_solvers_own_reader(monkeypatch):
    import motor_ai_sim.simulation.fem_solver_2d as fs
    monkeypatch.setattr(fs, "_run_magnet_temp_c", lambda t: 77.0 if t is None else t)
    m = RS.magnet_block(None)
    assert m["card_c"] == 77.0 and m["used_c"] == 77.0 and m["source"] == "card"
    assert RS.magnet_block(60.0)["used_c"] == 60.0


def test_a_descent_body_with_a_magnet_temperature_is_accepted_headless_too():
    from motor_ai_sim import optimize_headless as H
    body = {"variables": [{"name": "tooth_width", "min": 0.0, "max": 0.0}],
            "operating_point": {"current_a": 10.0, "gamma_deg": 5.0, "rpm": 3000.0}, "max_iters": 3, "gap_layers": 2,
            "run_id": "hl", "magnet_temp_c": 60.0}
    req = O.DescentRequest(**body)
    plan = O.prepare_descent(req)
    assert plan["kwargs"]["run_magnet_temp_c"] == 60.0 and plan["init_state"]["magnet"]["source"] == "typed"
    # the headless guard: a machine temperature different from the card is refused only when the body carries no value
    H.refuse_magnet_temperature(O, body, {"magnet_temp_c": 60.0}, "descent")                     # typed in the body: accepted
    with pytest.raises(H.Refused) as e:
        H.refuse_magnet_temperature(O, {k: v for k, v in body.items() if k != "magnet_temp_c"}, {"magnet_temp_c": 5.0}, "descent")
    assert "set 'magnet_temp_c' in the descent body" in str(e.value)


def test_auto_plan_and_thread_carry_the_typed_temperature(monkeypatch):
    plan = O._auto_assemble(5.0, 0, "cmaes", _rs(60.0, "auto"))
    assert plan["eval"]["magnet_temp_c"] == 60.0 and plan["eval"]["magnet"]["source"] == "typed"
    assert O._auto_assemble(5.0, 0, "cmaes", _rs(None, "auto"))["eval"]["magnet_temp_c"] is None
    assert O._auto_assemble(5.0, 0, "screen", None, 60.0)["eval"]["magnet_temp_c"] == 60.0       # a body-only value
    seen = []

    class _Thread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            seen.append({"target": target, "args": args, "kwargs": kwargs})

        def start(self):
            pass
    monkeypatch.setattr(O.threading, "Thread", _Thread)
    saved = dict(O._descent_state)
    O._descent_state["running"] = False
    try:
        O.auto_start(O.AutoOptRequest(max_ripple_pct=5.0, run_settings=_rs(60.0, "auto"), run_id="mt-auto"), None)
        O._descent_state["running"] = False
        O.auto_start(O.AutoOptRequest(max_ripple_pct=5.0, run_settings=_rs(None, "auto"), run_id="mt-auto2"), None)
    finally:
        O._descent_state.clear()
        O._descent_state.update(saved)
    typed, blank = seen
    assert typed["kwargs"] == {"run_magnet_temp_c": 60.0} and not blank["kwargs"]
    # the thread's call goes through the job decorator: an eval inside it takes the run's temperature
    got = {}

    @O._optimizer_job("auto")
    def worker(plan, run_id, bucket, name):
        got["t"] = O._RUN_MAGNET_T.get()
    worker(None, "r", "b", "n", **typed["kwargs"])
    assert got["t"] == 60.0 and O._RUN_MAGNET_T.get() is None


def test_two_concurrent_jobs_at_two_temperatures_do_not_see_each_other():
    import threading
    barrier = threading.Barrier(2)
    seen = {}

    def run(t, name):
        @O._optimizer_job("descent")
        def worker():
            barrier.wait(timeout=10)                                    # both jobs are inside their scope at the same moment
            seen[name] = O._RUN_MAGNET_T.get()
            barrier.wait(timeout=10)
        worker(run_magnet_temp_c=t)
    a = threading.Thread(target=run, args=(60.0, "a"))
    b = threading.Thread(target=run, args=(90.0, "b"))
    a.start(); b.start(); a.join(15); b.join(15)
    assert seen == {"a": 60.0, "b": 90.0} and O._RUN_MAGNET_T.get() is None


def test_the_server_side_baseline_with_run_settings_solves_at_the_typed_temperature(monkeypatch):
    got = []

    def fake(overrides, current_a, steps, coil_temp_c, **kw):
        got.append(dict(kw))
        return {"ok": True, "res": {"torque_per_mass_Nm_kg": 1.0 + 0.1 * len(got), "efficiency": 0.9 - 0.01 * len(got),
                                    "T_em_Nm": 1.0, "T_ripple_pct": 2.0}}
    monkeypatch.setattr(O, "_subprocess_eval", fake)
    body = {"operating_point": {"current_a": 10.0, "gamma_deg": 0.0, "rpm": 1000.0}, "current_bump_pct": 10.0,
            "run_settings": _rs(60.0, "descent")}
    O.descent_baseline(O.BaselineRequest(**body))
    assert len(got) == 2 and all(g.get("magnet_temp_c") == 60.0 for g in got)
    got.clear()
    body["run_settings"] = _rs(None, "descent")
    O.descent_baseline(O.BaselineRequest(**body))
    assert len(got) == 2 and all(g.get("magnet_temp_c") is None for g in got)
