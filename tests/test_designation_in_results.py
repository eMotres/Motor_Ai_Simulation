"""Every NEW stored result keeps the motor designation it was computed for (owner 2026-11-17; ~/jobs/TASK_designation_in_results.md).

Additive: stored results are never rewritten, a stamp never fails a save, the dedupe of a re-filed run still matches an
old entry without the key.  Temporary store, synthetic motor (round numbers), no solver."""
from __future__ import annotations

import json

import pytest

from motor_ai_sim import config as C
from motor_ai_sim import duty_results as DR
from motor_ai_sim import motor_designation as MD
from motor_ai_sim.optimization import run_store as RS


def live(rows=17, conn="4S", star="star"):
    return {"geometry": {"num_slots": 24, "num_poles": 28, "stator_diameter": 200, "rotor_outer_radius": 73.0,
                         "stator_inner_radius": 74.0, "motor_length": 65, "num_wires_per_slot": rows, "wire_split": 1,
                         "wire_parallel": 1, "wire_width": 7.0, "wire_height": 0.5},
            "winding": {"connection": conn, "star_delta": star, "phases": 3},
            "materials": {"stator_core": "NO20", "magnet": "NDFEB52", "slot": "copper"}}


LIB = {"steel": {"NO20": {"description": "non-oriented electrical steel", "form": "lamination"}},
       "magnet": {"NDFEB52": {"description": "sintered NdFeB"}}}


@pytest.fixture
def store(tmp_path, monkeypatch):
    state = {"cfg": live()}
    monkeypatch.setattr(C, "get_config", lambda *a, **k: state["cfg"])
    monkeypatch.setattr(MD, "_library", lambda: LIB)
    path = tmp_path / ".duty_results.json"
    with DR.using_store(path):
        yield state, path


def _rows(path):
    return json.loads(path.read_text())["results"]


def test_a_new_result_carries_the_name_of_the_machine_it_was_computed_on(store):
    state, path = store
    assert DR.record("CILN28", "G2", "rated", "em", {"computed_at": "t1", "T_avg_Nm": 1.0})
    assert _rows(path)["CILN28"]["G2"]["rated"]["em"]["designation"] == "CILN28 200_65 17F4SY"
    state["cfg"] = live(rows=16)                                            # the motor is edited during simulation
    assert DR.record("CILN28", "G2", "peak", "em", {"computed_at": "t2"})
    rows = _rows(path)["CILN28"]["G2"]
    assert rows["rated"]["em"]["designation"] == "CILN28 200_65 17F4SY"   # the earlier result keeps its name
    assert rows["peak"]["em"]["designation"] == "CILN28 200_65 16F4SY"
    assert MD.compare(rows["rated"]["em"]["designation"], rows["peak"]["em"]["designation"]) == {
        "computed_for": "CILN28 200_65 17F4SY", "now": "CILN28 200_65 16F4SY"}


@pytest.mark.parametrize("cfg, code", [(live(conn="2S-2P"), "17F2S2PY"), (live(star="delta"), "17F4SD")])
def test_connection_and_star_delta_changes(store, cfg, code):
    state, path = store
    state["cfg"] = cfg
    DR.record("CILN28", "G2", "rated", "em", {"computed_at": "t"})
    assert _rows(path)["CILN28"]["G2"]["rated"]["em"]["designation"].endswith(code)


def test_stored_results_are_never_rewritten(store):
    _state, path = store
    old = {"kind": "em", "computed_at": "t0", "recorded_at": "t0", "T_avg_Nm": 2.0}
    path.write_text(json.dumps({"results": {"CILN28": {"G2": {"old": {"em": old}}}}}))
    DR.record("CILN28", "G2", "new", "em", {"computed_at": "t1"})
    rows = _rows(path)["CILN28"]["G2"]
    assert rows["old"]["em"] == old and "designation" not in rows["old"]["em"]
    assert rows["new"]["em"]["designation"]


def test_a_caller_given_name_is_kept_and_a_failing_stamp_never_fails_the_save(store, monkeypatch):
    _state, path = store
    DR.record("CILN28", "G2", "a", "em", {"computed_at": "t", "designation": "GIVEN 1_1 1F1SY"})
    assert _rows(path)["CILN28"]["G2"]["a"]["em"]["designation"] == "GIVEN 1_1 1F1SY"
    monkeypatch.setattr(C, "get_config", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no live config")))
    assert DR.record("CILN28", "G2", "b", "em", {"computed_at": "t"}) is True
    assert _rows(path)["CILN28"]["G2"]["b"]["em"]["designation"] is None


def test_the_same_run_filed_again_still_matches_an_old_entry_without_the_key(store):
    """The one equality in the writer: a re-filed run (same computed_at) is not pushed into the history."""
    _state, path = store
    old = {"kind": "em", "computed_at": "t5", "recorded_at": "t5"}
    path.write_text(json.dumps({"results": {"CILN28": {"G2": {"rated": {"em": old}}}}}))
    DR.record("CILN28", "G2", "rated", "em", {"computed_at": "t5"})
    doc = json.loads(path.read_text())
    assert not (doc.get("history") or {})                                   # no duplicate row because of the new key


def test_alt_carrier_rows_are_stamped(store):
    _state, path = store
    DR.record("CILN28", "G2", "rated", "em", {"computed_at": "t", "inverter": {"f_carrier_hz": 20000.0}})
    assert DR.record_alt_carrier("CILN28", "G2", "rated", "em", {"computed_at": "t2", "inverter": {"f_carrier_hz": 40000.0}})
    alts = _rows(path)["CILN28"]["G2"]["rated"]["em"].get("alt_carriers") or []
    assert alts and all(a.get("designation") == "CILN28 200_65 17F4SY" for a in alts)


def test_optimisation_candidates_each_get_their_own_name(monkeypatch):
    die_doc = {"geometry": {k: v for k, v in live()["geometry"].items()}}
    cfg_doc = {"geometry_overrides": {}, "winding": live()["winding"], "materials": live()["materials"]}
    monkeypatch.setattr(MD, "_stored", lambda d, c: (die_doc, cfg_doc, None))
    monkeypatch.setattr(MD, "_library", lambda: LIB)
    state = {"variables": [{"name": "num_wires_per_slot"}], "history": [{"cost": 1}],
             "points": [{"overrides": {"num_wires_per_slot": n}} for n in (15, 16)] + [{"td": 1}]}
    rec = RS.build_record(state, "run-1", die="CILN28", config="G2")
    assert rec["designations"] == {"base": "CILN28 200_65 17F4SY",
                                   "points": ["CILN28 200_65 15F4SY", "CILN28 200_65 16F4SY", "CILN28 200_65 17F4SY"]}
    assert rec["state"] is state and "designation" not in state["points"][0]   # the run state is not touched
    assert "designations" not in RS.build_record(state, "run-2")             # an old caller: the record is as before
