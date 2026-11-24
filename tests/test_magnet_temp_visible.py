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
    assert all(p.get("magnet_temp_used_c") == m["card_c"] for p in res["points"] if m["card_c"] is not None)


def test_two_sweeps_at_different_magnet_temperatures_do_not_share_cached_points(sweep):
    a, ca = sweep(magnet_temp_c=60.0)
    b, cb = sweep(magnet_temp_c=90.0)
    assert len(cb) > 0                                                  # the second sweep solved its own points, no hit on the first's
    assert {p["magnet_temp_used_c"] for p in b["points"]} == {90.0}
    c, cc = sweep(magnet_temp_c=60.0)
    assert len(cc) == 0                                                 # the same temperature IS served from the cache
