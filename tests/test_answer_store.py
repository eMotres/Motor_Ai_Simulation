"""The load is ready, not rebuilt (owner 2026-10-19: «зачем её каждый раз собирать, она уже должна быть готова»).

* ``fscache``: a file is parsed once per version; every reader gets its own copy; an edit is seen at once;
  a missing optional file does not change a stamp;
* ``answer_store``: an answer is computed once per stamp (single flight), served from memory and from its
  file after a restart, recomputed when the stamp moves; ``persist=False`` never touches the disk;
* ``/s1`` names a drive the model does not carry and lists the ones it does (a client falls back);
* a Configure context never opens a preset on a drive its S1 model does not carry;
* a stored ``configure_load`` answer is stripped for a caller who may not see its signed-in parts.
Synthetic data only, no FEM.
"""
from __future__ import annotations

import json
import os
import threading
import time

import pytest
import yaml

from motor_ai_sim import answer_store as st
from motor_ai_sim import fscache


def test_a_file_is_parsed_once_per_version_and_every_reader_gets_a_copy(tmp_path):
    p = tmp_path / "a.yaml"
    p.write_text(yaml.safe_dump({"x": [1, 2]}), encoding="utf-8")
    a = fscache.read_yaml(p)
    a["x"].append(99)                                          # a caller that mutates what it read ...
    assert fscache.read_yaml(p) == {"x": [1, 2]}               # ... poisons nobody
    p.write_text(yaml.safe_dump({"x": [3]}), encoding="utf-8")
    os.utime(p, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
    assert fscache.read_yaml(p) == {"x": [3]}                  # an edit is seen
    with pytest.raises(FileNotFoundError):
        fscache.read_yaml(tmp_path / "missing.yaml")
    s1 = fscache.stamp_of([p, tmp_path / "nope-1.json"])
    assert s1 == fscache.stamp_of([p, tmp_path / "nope-2.json"])   # absent optional files do not split stamps
    os.utime(p, ns=(time.time_ns() + 3 * 10**9, time.time_ns() + 3 * 10**9))
    assert fscache.stamp_of([p]) != fscache.stamp_of([tmp_path / "x"]) and fscache.stamp_of([p]) != s1


def test_an_answer_is_computed_once_per_stamp_and_survives_a_restart(tmp_path, monkeypatch):
    monkeypatch.setenv(st.ENV_ROOT, str(tmp_path / "store"))
    st._ROOT_CHECKED.clear(); st.clear_memory()
    n = {"c": 0}

    def compute():
        n["c"] += 1
        time.sleep(0.05)
        return {"v": n["c"]}
    key = {"motor": "m1", "config": "L1"}
    outs = []
    th = [threading.Thread(target=lambda: outs.append(st.get_or_compute("t", key, "s1", compute))) for _ in range(4)]
    [t.start() for t in th]; [t.join() for t in th]
    assert n["c"] == 1 and all(o == {"v": 1} for o in outs)    # single flight
    st.clear_memory()                                          # "restart": the file answers
    assert st.get_or_compute("t", key, "s1", compute) == {"v": 1} and n["c"] == 1
    assert st.get_or_compute("t", key, "s2", compute) == {"v": 2}   # the inputs moved: recomputed
    st.get_or_compute("t", {"motor": "private"}, "s1", compute, persist=False)
    files = [p.name for p in (tmp_path / "store" / "t").glob("*.json")]
    assert len(files) == 1                                     # the private answer never reached the disk


def test_no_store_off_a_layered_install(monkeypatch):
    monkeypatch.delenv(st.ENV_ROOT, raising=False)
    monkeypatch.delenv("SHARED_ROOT", raising=False)
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    st._ROOT_CHECKED.clear()
    assert st.store_root() is None


def test_s1_names_the_missing_drive(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from motor_ai_sim import cooling_options as co
    from motor_ai_sim import propeller as pp
    from motor_ai_sim import propeller_s1 as s1
    from motor_ai_sim.routes import propellers as R
    from tests.test_propeller_s1 import _model, _prop
    prop = _prop(tmp_path)
    (tmp_path / "props" / "v").mkdir(parents=True)
    (tmp_path / "props" / "v" / "syn_s1.yaml").write_text(yaml.safe_dump(prop.raw), encoding="utf-8")
    monkeypatch.setattr(pp, "_DIR", tmp_path / "props")
    (tmp_path / "s1" / "TEST DIE").mkdir(parents=True)
    (tmp_path / "s1" / "TEST DIE" / "L1.json").write_text(json.dumps(_model()), encoding="utf-8")
    monkeypatch.setattr(s1, "_DIR", tmp_path / "s1")
    f = tmp_path / "co.yaml"
    f.write_text(yaml.safe_dump({"version": 1, "dies": {"TEST DIE": {
        "cooling_options": ["propeller_air"], "propellers": ["syn_s1"]}}}), encoding="utf-8")
    monkeypatch.setattr(co, "_FILE", f)
    st.clear_memory()
    with pytest.raises(HTTPException) as e:
        R.s1_answer("syn_s1", "TEST DIE", "L1", drive="si_48k")
    assert e.value.status_code == 422
    d = e.value.detail
    assert d["code"] == "drive_not_available" and d["drive"] == "si_48k" and d["drives_available"] == ["sine"]
    a = R.s1_answer("syn_s1", "TEST DIE", "L1", drive="sine", n=11)
    assert R.s1_answer("syn_s1", "TEST DIE", "L1", drive="sine", n=11) is a      # the stored answer


def test_a_preset_never_opens_on_a_drive_its_s1_model_lacks(monkeypatch):
    from motor_ai_sim import propeller_s1 as s1
    from motor_ai_sim.routes import catalog as cat
    monkeypatch.setattr(s1, "load_model", lambda die, cfg: {"drives": {"si_48k": {}}} if cfg == "L12"
                        else {"drives": {}} if cfg == "L15" else (_ for _ in ()).throw(s1.S1ModelNotFound("x")))
    a = {"config": "L12", "drive_variant": "si_48k"}
    b = {"config": "L15", "drive_variant": "si_48k"}
    c = {"config": "L99", "drive_variant": "si_48k"}
    for pr in (a, b, c):
        cat._drive_the_s1_model_carries(pr, "D")
    assert a["drive_variant"] == "si_48k"                                 # carried: unchanged
    assert b["drive_variant"] is None and b["drive_variant_unmodelled"] == "si_48k"   # not carried: Sine
    assert c["drive_variant"] == "si_48k"                                 # no S1 model: nothing to agree with


def test_the_stored_answer_is_stripped_for_a_caller_who_may_not_see_it(monkeypatch):
    from motor_ai_sim import motor_access as ma
    from motor_ai_sim.routes import catalog as cat
    full = {"config": "L1", "thermal_model": {"x": 1}, "thermal_anchor": {"y": 2},
            "propeller": {"s1": {"z": 3}, "s1_error": None}, "_gate": {"s1_die": "D", "anchor_die": "D"}}
    monkeypatch.setattr(ma, "catalog_access", lambda a=None: {"mode": ma.MODE_ANONYMOUS, "dies": frozenset()})
    out = cat._gate_configure_load(full, None)
    assert out["thermal_model"] is None and out["thermal_anchor"] is None and out["propeller"]["s1"] is None
    assert "_gate" not in out and full["propeller"]["s1"] == {"z": 3}       # the stored answer itself untouched
    monkeypatch.setattr(ma, "catalog_access", lambda a=None: {"mode": ma.MODE_ALL, "dies": frozenset()})
    monkeypatch.setattr(ma, "may_see_config", lambda acc, die, cfg: True)
    out = cat._gate_configure_load(full, "Bearer x")
    assert out["thermal_model"] == {"x": 1} and out["propeller"]["s1"] == {"z": 3}


def test_the_warm_up_stores_every_allowed_propellers_s1(tmp_path, monkeypatch):
    """Every propeller a public configuration may drive, for Sine and the preset's own drive when its S1 model carries
    it - so picking another propeller in the selector or the load chart reads a stored answer (owner 2026-10-19)."""
    from motor_ai_sim import configure_warm as cw
    from motor_ai_sim import cooling_options as co
    from motor_ai_sim import propeller as pp
    from motor_ai_sim import propeller_s1 as s1
    from motor_ai_sim.routes import catalog as cat
    from tests.test_propeller_s1 import _model, _prop
    for name in ("syn_a", "syn_b"):
        pr = _prop(tmp_path, name=name)
        (tmp_path / "props" / "v").mkdir(parents=True, exist_ok=True)
        (tmp_path / "props" / "v" / f"{name}.yaml").write_text(yaml.safe_dump(pr.raw), encoding="utf-8")
    monkeypatch.setattr(pp, "_DIR", tmp_path / "props")
    (tmp_path / "s1" / "TEST DIE").mkdir(parents=True)
    (tmp_path / "s1" / "TEST DIE" / "L1.json").write_text(json.dumps(_model()), encoding="utf-8")
    monkeypatch.setattr(s1, "_DIR", tmp_path / "s1")
    f = tmp_path / "co.yaml"
    f.write_text(yaml.safe_dump({"version": 1, "dies": {"TEST DIE": {
        "cooling_options": ["propeller_air"], "propellers": ["syn_a", "syn_b", "unknown_prop"]}}}), encoding="utf-8")
    monkeypatch.setattr(co, "_FILE", f)
    monkeypatch.setenv(st.ENV_ROOT, str(tmp_path / "store"))
    st._ROOT_CHECKED.clear(); st.clear_memory()
    monkeypatch.setattr(cw, "_shared_cards", lambda: [{"id": "cat_t"}])
    monkeypatch.setattr(cat, "configure_context_answer", lambda mid, auth=None: {
        "cooling": {"die": "TEST DIE"},
        "presets": [{"config": "L1", "drive_variant": "si_48k",            # a drive the synthetic model does not carry
                     "battery": {"cells": 6, "v_cell_min": 3.0, "v_cell_max": 4.2}}]})
    out = cw.warm_propellers(limit_s=120)
    assert out["s1_answers"] == 2 and out["failed"] == 0          # two computable propellers x Sine only
    assert len(list((tmp_path / "store" / "s1").glob("*.json"))) == 2
    st.clear_memory()
    again = cw.warm_propellers(limit_s=120)                        # a restart: read back, not recomputed
    assert again["s1_answers"] == 2 and again["seconds"] <= out["seconds"] + 0.5
