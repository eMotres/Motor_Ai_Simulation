"""The third run-settings input mode, ``preset: "tab"`` (owner 2026-11-24, one place for the settings): the request carries the
visible Mesh / Simulation values, the server labels them by MATCH against the two presets, and the legacy modes are unchanged."""
from __future__ import annotations

import copy

import pytest
from fastapi import HTTPException

from motor_ai_sim import mesh_presets as MP
from motor_ai_sim import run_settings as RS
from motor_ai_sim.routes import optimization as O
from tests.test_optimize_headless_prepare_scan import _req
from tests.test_sweep_optimization_preset import _cfg


def _numbers(name, cfg):
    return RS._preset_numbers(MP.resolve(name, dict(cfg.get("geometry") or {})))


def _tab(name, cfg, **kw):
    n = _numbers(name, cfg)
    n.update(kw)
    return n


def test_the_optimization_values_are_labelled_optimization():
    cfg = _cfg()
    r = RS.resolve("tab", _tab("optimization", cfg), cfg)
    assert r["label"] == "optimization" and not r["custom"] and r["differs"] == {} and r["matched"] == "optimization"
    assert r["preset"] == "tab" and r["record_name"] == "optimization"
    # the same numbers as the legacy Optimization button: same digest
    assert r["sig"] == RS.resolve("optimization", {}, cfg)["sig"]


def test_simulation_means_that_many_steps_or_more_decided_by_the_server():
    cfg = _cfg(coil_rel=1)
    n = _numbers("simulation", cfg)
    assert RS.resolve("tab", n, cfg)["label"] == "simulation"
    more = RS.resolve("tab", {**n, "steps_per_period": n["steps_per_period"] + 12}, cfg)
    assert more["label"] == "simulation" and more["matched"] == "simulation"
    less = RS.resolve("tab", {**n, "steps_per_period": n["steps_per_period"] - 12}, cfg)
    assert less["label"] == "custom" and "steps_per_period" in less["differs"]


def test_anything_else_is_custom_and_lists_the_differing_keys():
    cfg = _cfg()
    r = RS.resolve("tab", _tab("optimization", cfg, steps_per_period=108, coil_rel=1.0), cfg)
    assert r["label"] == "custom" and r["custom"] and r["matched"] is None and r["record_name"] == "custom"
    assert set(r["differs"]) == {"steps_per_period", "coil_rel"}
    assert r["differs"]["steps_per_period"] == {"preset": _numbers("optimization", cfg)["steps_per_period"], "current": 108}
    assert r["settings"]["steps_per_period"] == 108 and r["sources"]["steps_per_period"] == "tab"
    # a mesh size that is not the preset's is a difference too (the legacy label looked at the pair only)
    m = RS.resolve("tab", _tab("optimization", cfg, mesh_size_mm=3.3), cfg)
    assert m["label"] == "custom" and "mesh_size_mm" in m["differs"] and m["sig_scope"] == 3 and m["sig"] != RS.resolve("tab", _tab("optimization", cfg), cfg)["sig"]


def test_no_silent_default_a_missing_value_is_refused():
    cfg = _cfg()
    for k in RS.TAB_REQUIRED:
        n = _tab("optimization", cfg)
        del n[k]
        with pytest.raises(RS.RunSettingsError) as ei:
            RS.resolve("tab", n, cfg)
        assert k in str(ei.value)
    with pytest.raises(RS.RunSettingsError):
        RS.resolve("tab", {}, cfg)
    with pytest.raises(RS.RunSettingsError):          # a key the run cannot take
        RS.resolve("tab", {**_tab("optimization", cfg), "outer_air_factor": 1.5}, cfg)


def test_clamps_with_reasons_and_the_gap_row_limit():
    cfg = _cfg()
    r = RS.resolve("tab", _tab("optimization", cfg, gap_layers=2, steps_per_period=500), cfg)
    keys = {c["key"]: c for c in r["clamps"]}
    assert r["settings"]["steps_per_period"] == 180 and keys["steps_per_period"]["source"] == "override"
    assert "outside" in keys["steps_per_period"]["reason"]
    with pytest.raises(RS.RunSettingsError):
        RS.resolve("tab", _tab("optimization", cfg, gap_layers=1.5), cfg)      # a count, whole numbers only
    g = RS.resolve("tab", _tab("optimization", cfg, gap_layers=5), cfg)
    assert g["settings"]["gap_layers"] == 2.0 and "never above 2" in [c for c in g["clamps"] if c["key"] == "gap_layers"][0]["reason"]


def test_the_token_and_the_digest_are_stable_and_follow_the_values():
    cfg = _cfg()
    a, b = RS.resolve("tab", _tab("optimization", cfg), cfg), RS.resolve("tab", _tab("optimization", cfg), cfg)
    assert a["token"] == b["token"] and a["sig"] == b["sig"]
    c = RS.resolve("tab", _tab("optimization", cfg, steps_per_period=96), cfg)
    assert c["token"] != a["token"] and c["sig"] != a["sig"]
    # the finalist note comes from the same helper as before
    assert "finalist_note" in a


def test_a_stale_token_is_refused_with_409(monkeypatch):
    cfg = _cfg()
    monkeypatch.setattr(O, "get_config", lambda *a, **k: cfg)
    shown = RS.resolve("tab", _tab("optimization", cfg), cfg)
    ok = O._resolve_run_settings(O.RunSettingsIn(preset="tab", overrides=_tab("optimization", cfg), expect_sig=shown["sig"],
                                                 expect_token=shown["token"]), "sweep")
    assert ok["token"] == shown["token"]
    with pytest.raises(HTTPException) as ei:
        O._resolve_run_settings(O.RunSettingsIn(preset="tab", overrides=_tab("optimization", cfg, steps_per_period=96),
                                                expect_sig=shown["sig"], expect_token=shown["token"]), "sweep")
    assert ei.value.status_code == 409 and "run settings changed" in ei.value.detail
    with pytest.raises(HTTPException) as e2:
        O.run_settings_resolve(O.RunSettingsIn(preset="tab", overrides={}), "sweep")
    assert e2.value.status_code == 422


def test_a_tab_run_equals_the_resolve_answer_in_scan_descent_and_auto(monkeypatch):
    cfg = _cfg()
    monkeypatch.setattr(O, "get_config", lambda *a, **k: cfg)
    vals = _tab("optimization", cfg, steps_per_period=108, gap_layers=2, mesh_size_mm=3.3, rotor_eddy=False, demag=False)
    shown = RS.resolve("tab", vals, cfg)
    st = shown["settings"]
    plan = O.prepare_scan(_req(run_settings={"preset": "tab", "overrides": vals, "expect_sig": shown["sig"],
                                             "expect_token": shown["token"]}, steps_per_period=7, gap_layers=1, mesh_size_mm=9.0))
    a = plan["args"]
    assert (a[2], a[13], a[15], a[22], a[8]) == (st["steps_per_period"], st["gap_layers"], st["rotor_eddy"], st["demag"], st["mesh_size_mm"])
    mp = plan["response"]["mesh_preset"]
    assert mp["sig"] == shown["sig"] and mp["name"] == "custom" and mp["run_settings"] == {"preset": "tab", "overrides": vals}
    dvals = {k: v for k, v in vals.items() if k != "demag"}          # the Descent resolver refuses demag (the machine's)
    dshown = RS.resolve("tab", dvals, cfg, "descent")
    d = O.prepare_descent(O.DescentRequest(variables=[{"name": "tooth_width", "min": 0.0, "max": 0.0}],
                                           run_settings={"preset": "tab", "overrides": dvals, "expect_sig": dshown["sig"]},
                                           steps_per_period=24))
    ep = d["init_state"]["eval_params"]
    assert (ep["steps_per_period"], ep["gap_layers"], ep["mesh_size_mm"]) == (108, 2.0, 3.3)
    assert d["response"]["mesh_preset"]["sig"] == dshown["sig"]
    for mode in ("cmaes", "screen"):
        ap = O.auto_plan(O.AutoOptRequest(max_ripple_pct=5.0, mode=mode, run_settings={"preset": "tab", "overrides": vals}))["plan"]
        assert ap["run_settings"]["sig"] == shown["sig"] and ap["run_settings"]["name"] == "custom", mode
        assert ap["eval"]["steps_per_period"] == 108 and ap["eval"]["gap_layers"] == 2.0


def test_the_legacy_modes_are_unchanged():
    cfg = _cfg()
    before = copy.deepcopy(cfg)
    o = RS.resolve("optimization", {"steps_per_period": 108}, cfg)
    assert o["label"] == "custom" and o["record_name"] == "optimization" and o["matched"] is None
    assert set(o["differs"]) == {"steps_per_period"} and o["sources"]["steps_per_period"] == "override"
    s = RS.resolve("simulation", {}, cfg)
    assert s["preset"] == "simulation" and s["sources"]["steps_per_period"] == "preset"
    with pytest.raises(RS.RunSettingsError):
        RS.resolve("nope", {}, cfg)
    assert cfg == before


# ── the digest of a custom run covers every resolved setting (sig scope 3); a preset run keeps the scope-2 digest ──
from motor_ai_sim import sweep_saves as SS


def _stamp_of(resolved):
    """The settings the saved-sweeps reader gives for a run of this resolution whose solve verified every number."""
    st = O._run_settings_stamp(None, resolved)
    st.update({"effective": {"steps_per_period": resolved["settings"]["steps_per_period"],
                             "gap_layers": resolved["settings"]["gap_layers"], "coil_rel": resolved["coil_rel"]},
               "effective_source": {"steps_per_period": "solved", "gap_layers": "solved", "coil_rel": "solved"}})
    return SS.sweep_settings_of({"mesh_preset": st})


def test_a_preset_digest_is_byte_identical_to_before():
    assert RS.identity_sig(48, 1.0) == 29124574465193           # the pair digest of before, pinned
    cfg = _cfg()
    for name in ("optimization", "simulation"):
        r = RS.resolve(name, {}, cfg)
        assert r["sig_scope"] == 2
        assert r["sig"] == RS.identity_sig(r["settings"]["steps_per_period"], r["settings"]["gap_layers"], MP.version(), r["coil_rel"])
    assert RS.resolve("tab", _tab("optimization", cfg), cfg)["sig_scope"] == 2          # a tab run that equals a preset is a preset run
    assert RS.identity_sig(48, 1.0, 2, 2.0) == 122103711710627               # version 2, wire cell 2h, pinned


def test_two_tab_runs_differing_only_in_mesh_size_have_different_digests_and_are_not_ranked():
    cfg = _cfg()
    a = RS.resolve("tab", _tab("optimization", cfg, mesh_size_mm=3.3), cfg)
    b = RS.resolve("tab", _tab("optimization", cfg, mesh_size_mm=3.4), cfg)
    c = RS.resolve("tab", _tab("optimization", cfg, mesh_size_mm=3.3), cfg)
    assert a["custom"] and a["sig_scope"] == 3 and a["sig"] != b["sig"] and a["sig"] == c["sig"]
    sa, sb, sc = _stamp_of(a), _stamp_of(b), _stamp_of(c)
    assert sa["sig"] == sc["sig"] and sa["sig_scope"] == 3 and sa["sig"] is not None
    assert sa["sig"] != sb["sig"]


def test_every_resolved_setting_is_in_the_custom_digest():
    cfg = _cfg()
    base = RS.resolve("tab", _tab("optimization", cfg, steps_per_period=108), cfg)
    for k, v in (("min_size_mm", 0.9), ("n_sectors", _numbers("optimization", cfg)["n_sectors"] + 1), ("rotor_eddy", False), ("demag", False), ("gap_layers", 2),
                 ("coil_rel", 1.0), ("steps_per_period", 120)):
        other = RS.resolve("tab", _tab("optimization", cfg, **{"steps_per_period": 108, k: v}), cfg)
        assert other["sig"] != base["sig"], k
    # the machine-sourced settings (outer air, per-part sizes, eddy) are part of it too
    c2 = copy.deepcopy(cfg)
    c2["mesh"] = {**dict(c2.get("mesh") or {}), "outer_air_factor": 1.7}
    assert RS.resolve("tab", _tab("optimization", cfg, steps_per_period=108), c2)["sig"] != base["sig"]


def test_a_preset_run_and_a_custom_run_with_the_presets_pair_and_cell_but_another_mesh_are_not_ranked():
    cfg = _cfg()
    preset = RS.resolve("optimization", {}, cfg)
    custom = RS.resolve("tab", _tab("optimization", cfg, mesh_size_mm=3.3), cfg)
    assert preset["settings"]["steps_per_period"] == custom["settings"]["steps_per_period"] and preset["coil_rel"] == custom["coil_rel"]
    sp, sc = _stamp_of(preset), _stamp_of(custom)
    assert sp["sig_scope"] == 2 and sc["sig_scope"] == 3 and sp["sig"] != sc["sig"]


def test_an_unknown_scope_is_unknown_and_the_old_scope_keeps_its_meaning():
    cfg = _cfg()
    st = O._run_settings_stamp(None, RS.resolve("tab", _tab("optimization", cfg, mesh_size_mm=3.3), cfg))
    st.update({"effective": {"steps_per_period": 48, "gap_layers": 1.0, "coil_rel": 2.0},
               "effective_source": {"steps_per_period": "solved", "gap_layers": "solved", "coil_rel": "solved"}})
    assert SS.sweep_settings_of({"mesh_preset": {**st, "sig_scope": 4}})["sig"] is None       # newer than this reader: fail closed
    assert SS.sweep_settings_of({"mesh_preset": {**st, "sig_scope": None}})["sig"] is None
    old = SS.sweep_settings_of({"mesh_preset": {**st, "sig_scope": 2, "sig": 77}})            # a stored scope-2 row stays as it was
    assert old["sig"] == 77 and old["sig_scope"] == 2
