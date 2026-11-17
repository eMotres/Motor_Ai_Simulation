"""The two mesh / solve presets (owner 2026-11-17): the data file, the per-motor resolution, the fill rule, the record."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest
import yaml

from motor_ai_sim import mesh_presets as MP

CONFIG = Path(__file__).resolve().parents[1] / "config" / "motor_config.yaml"
LINEAR = ("stator_diameter", "tooth_width", "slot_width", "core_thickness", "magnet_height", "wire_width", "wire_height",
          "air_gap", "slot_height", "motor_length")


def _machine(od_mm: float, slots: int, poles: int) -> dict:
    """A synthetic machine of the public config's family at another size and slot / pole count (no catalog data)."""
    g = dict(yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["geometry"])
    s = od_mm / float(g["stator_diameter"])
    for k in LINEAR:
        if k in g and isinstance(g[k], (int, float)):
            g[k] = float(g[k]) * s
    g["num_slots"], g["num_poles"] = slots, poles
    return g


#: the size classes of the catalog: 12 mm to 400 mm, several slot / pole counts
FLEET = [(12, 12, 10), (40, 12, 14), (80, 24, 28), (150, 24, 20), (200, 24, 28), (250, 36, 42), (400, 48, 56)]


def test_the_data_file_validates_and_has_exactly_the_two_presets():
    doc = MP.load()
    assert MP.names() == ["optimization", "simulation"] and list(doc["presets"]) == ["optimization", "simulation"]
    assert MP.version() == doc["version"] >= 1
    cls = doc["size_classes"]
    assert cls[0]["from_mm"] <= 12 and cls[-1]["to_mm"] > 400 and all(a["to_mm"] == b["from_mm"] for a, b in zip(cls, cls[1:]))


def test_the_owner_fixed_numbers():
    doc = MP.load()["presets"]
    o, s = doc["optimization"], doc["simulation"]
    assert (o["steps_per_period"]["value"], o["steps_per_period"]["source"]) == (48, "owner")
    assert (o["gap_layers"]["value"], s["gap_layers"]["value"]) == (1, 2)
    assert s["steps_per_period"]["min"] == 96 and s["cogging_steps_per_period"]["min"] == 120
    assert o["element_order"]["value"] == s["element_order"]["value"] == 2


@pytest.mark.parametrize("edit,message", [
    (lambda d: d["presets"]["optimization"]["gap_layers"].update(value=3), "never above 2"),
    (lambda d: d["presets"]["optimization"]["gap_layers"].update(source="habit"), "source"),
    (lambda d: d["presets"]["simulation"]["element_order"].update(value=1), "element_order"),
    (lambda d: d["presets"]["simulation"]["global_edge_factor"].update(value=0), "global_edge_factor"),
    (lambda d: d["presets"]["optimization"]["coil_rel"].update(value=3), "coil_rel"),
    (lambda d: d["size_classes"][1].update(from_mm=120), "starts where the previous"),
    (lambda d: d["size_classes"][0].update(c_D=0), "c_D"),
    (lambda d: d["presets"].pop("simulation"), "exactly"),
    (lambda d: d.update(version=0), "version"),
])
def test_a_broken_data_file_is_refused(edit, message):
    doc = copy.deepcopy(MP.load())
    edit(doc)
    with pytest.raises(MP.PresetError, match=message):
        MP.validate(doc)


@pytest.mark.parametrize("od,slots,poles", FLEET)
def test_both_presets_resolve_for_every_size_without_falling_back(od, slots, poles):
    g = _machine(od, slots, poles)
    h = MP.h0_mm(g)
    cls = MP.size_class(od)
    assert 0 < h["h0"] <= cls["c_D"] * od + 1e-9                       # never coarser than the diameter term
    assert h["h0"] == pytest.approx(min(h["diameter_mm"], h["tooth_mm"], h["yoke_mm"], h["magnet_mm"], h["pole_pitch_mm"]))
    for name in MP.names():
        r = MP.resolve(name, g)
        v = r["values"]
        assert r["version"] == MP.version() and r["h0"]["mm"] == pytest.approx(h["h0"], abs=1e-4)
        assert v["mesh_size_mm"] == pytest.approx(h["h0"], abs=1e-4)             # g = 1
        assert v["min_size_mm"] == pytest.approx(cls["f_min"] * h["h0"], abs=1e-4) and 0 < v["min_size_mm"] < v["mesh_size_mm"]
        cm = v["component_mesh"]
        assert cm["stator"] == cm["rotor"] == cm["magnet"] == pytest.approx(h["h0"], abs=1e-4)
        assert cm["outer"] == pytest.approx(cls["air"] * h["h0"], abs=1e-3) and cm["coil_rel"] == 1.0
        assert v["n_sectors"] == math.gcd(slots, poles) >= 1 and v["element_order"] == 2
        assert all(k in v for k in ("n_steps_per_period", "gap_layers", "n_periods", "outer_air_factor", "eddy", "rotor_eddy", "demag"))
        assert all(x in MP.SOURCES for x in r["sources"].values())
    o, s = MP.resolve("optimization", g)["values"], MP.resolve("simulation", g)["values"]
    assert (o["n_steps_per_period"], o["gap_layers"]) == (48, 1.0)
    assert s["n_steps_per_period"] >= 96 and s["gap_layers"] == 2.0
    assert MP.resolve("simulation", g, cogging=True)["values"]["n_steps_per_period"] >= 120


def test_the_size_range_is_covered_and_outside_it_is_refused():
    assert MP.size_class(12)["from_mm"] == 12 and MP.size_class(100)["from_mm"] == 100 and MP.size_class(400)["to_mm"] > 400
    for bad in (11.9, 401):
        with pytest.raises(MP.PresetError, match="outside the preset range"):
            MP.size_class(bad)


def test_a_stale_stored_radius_does_not_change_the_resolution():
    """The config stores derived radii that go stale; the pole pitch must come from the primaries."""
    g = _machine(200, 24, 28)
    stale = dict(g, stator_inner_radius=g.get("stator_inner_radius", 1.0) * 7.0)
    assert MP.resolve("simulation", stale)["values"] == MP.resolve("simulation", g)["values"]


def test_a_machine_without_a_needed_feature_cannot_be_resolved():
    g = _machine(80, 24, 28)
    g.pop("tooth_width")
    with pytest.raises(MP.PresetError, match="tooth_width"):
        MP.resolve("optimization", g)
    with pytest.raises(MP.PresetError, match="unknown mesh preset"):
        MP.resolve("fast", _machine(80, 24, 28))


def test_the_steps_follow_the_cogging_rules():
    from motor_ai_sim.simulation.eddy_steps import cogging_cycles_per_period
    g = _machine(400, 48, 56)
    cycles = cogging_cycles_per_period(48, 56)
    assert MP.resolve("simulation", g)["values"]["n_steps_per_period"] == max(96, 9 * cycles)
    assert MP.resolve("simulation", g, cogging=True)["values"]["n_steps_per_period"] == 120      # 6 samples x 12 cycles = 72 < 120
    many = _machine(250, 120, 128)                                  # 30 cogging cycles per electrical period
    assert cogging_cycles_per_period(120, 128) == 30
    assert MP.resolve("simulation", many, cogging=True)["values"]["n_steps_per_period"] == 192   # 24 x ceil(6 x 30 / 24)


def test_fill_takes_the_preset_for_empty_fields_and_reports_what_the_request_changed():
    r = MP.resolve("optimization", _machine(80, 24, 28))
    empty = MP.fill(r, {})
    assert not empty["custom"] and empty["settings"]["n_steps_per_period"] == 48
    assert json.loads(empty["settings"]["component_mesh"]) == r["values"]["component_mesh"]
    same = MP.fill(r, {"n_steps_per_period": 48, "gap_layers": 1.0, "eddy": True})
    assert not same["custom"]
    custom = MP.fill(r, {"n_steps_per_period": 96, "gap_layers": 2, "demag": False, "component_mesh": '{"coil_rel": 2}'})
    assert custom["custom"] and custom["settings"]["n_steps_per_period"] == 96 and custom["settings"]["gap_layers"] == 2
    assert set(custom["overridden"]) == {"n_steps_per_period", "gap_layers", "demag", "component_mesh.coil_rel"}
    assert json.loads(custom["settings"]["component_mesh"])["coil_rel"] == 2 and "outer" in json.loads(custom["settings"]["component_mesh"])


def test_the_record_carries_name_version_effective_settings_and_what_a_clamp_changed():
    g = _machine(80, 24, 28)
    r = MP.resolve("simulation", g)
    f = MP.fill(r, {"mesh_size_mm": 3.9})                          # a request above the solver's ceiling
    eff = MP.effective_from_result(dict(f["settings"], n_periods=1.0, eddy=True, rotor_eddy=True, demag=True), g,
                                   {"n_steps_per_period": 108, "n_steps_per_period_requested": 110, "steps_snapped": True,
                                    "gap_layers_effective": 2.0, "gap_layers_requested": 2.0})
    rec = MP.record(r, f, eff)
    assert rec["name"] == "simulation" and rec["version"] == MP.version() and rec["custom"] is True
    assert rec["effective"]["n_steps_per_period"] == 108 and rec["effective"]["mesh_size_mm"] < 3.9
    assert set(rec["changed"]) == {"n_steps_per_period", "mesh_size_mm"}
    assert rec["resolved"]["gap_layers"] == 2.0 and rec["overridden"]["mesh_size_mm"]["requested"] == 3.9


def test_a_stored_em_run_keeps_the_preset_record_and_an_old_run_has_none():
    from motor_ai_sim import duty_results as DR
    base = {"summary": {"T_avg_Nm": 1.0}, "computed_at": "2026-11-17T00:00:00Z"}
    assert "preset" not in DR.compact_em_run(base)
    rec = {"name": "optimization", "version": 1, "custom": False, "overridden": {}, "effective": {"n_steps_per_period": 48},
           "changed": {}, "resolved": {"x": 1}}
    out = DR.compact_em_run(dict(base, preset=rec))
    assert out["preset"] == {k: rec[k] for k in ("name", "version", "custom", "overridden", "effective", "changed")}


def test_the_route_refuses_an_unknown_preset_before_any_solve():
    from fastapi import HTTPException
    from motor_ai_sim.routes import simulation as S
    with pytest.raises(HTTPException) as e:
        S.get_fem_transient(preset="fast")
    assert e.value.status_code == 422 and "unknown mesh preset" in str(e.value.detail)


def test_the_presets_route_answers_both_presets_resolved_for_the_open_machine(monkeypatch):
    from motor_ai_sim import api
    g = _machine(80.0, 12, 10)
    monkeypatch.setattr(api, "get_config", lambda: {"geometry": g})
    out = api.get_mesh_presets()
    assert out["version"] == MP.version()
    assert [p["name"] for p in out["presets"]] == ["optimization", "simulation"]
    opt, sim = out["presets"]
    assert opt["values"]["n_steps_per_period"] == 48 and opt["values"]["gap_layers"] == 1
    assert sim["values"]["n_steps_per_period"] >= 96 and sim["values"]["gap_layers"] == 2
    assert sim["cogging_steps_per_period"] >= 120
    assert all(p["values"]["mesh_size_mm"] > 0 and p["values"]["component_mesh"]["stator"] > 0 for p in out["presets"])


def test_the_presets_route_names_the_missing_feature_instead_of_inventing_a_default(monkeypatch):
    from motor_ai_sim import api
    g = _machine(80.0, 12, 10)
    g.pop("tooth_width", None)
    monkeypatch.setattr(api, "get_config", lambda: {"geometry": g})
    out = api.get_mesh_presets()
    assert all("values" not in p and p["error"] for p in out["presets"])


# ── production layout: the image has NO config/, the data file is at config-seed/ (PR #750 incident) ──────────────
def test_the_data_file_is_found_in_the_deployed_layout_with_only_config_seed(tmp_path):
    app = tmp_path / "app"
    (app / "config-seed").mkdir(parents=True)
    (app / "config-seed" / "mesh_presets.yaml").write_text((CONFIG.parent / "mesh_presets.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    assert not (app / "config").exists()
    p = MP.data_path(base=app)
    assert p == app / "config-seed" / "mesh_presets.yaml"
    assert MP.load(p)["version"] == MP.version()
    # a checkout (config/ present) still wins
    (app / "config").mkdir()
    (app / "config" / "mesh_presets.yaml").write_text("x: 1", encoding="utf-8")
    assert MP.data_path(base=app) == app / "config" / "mesh_presets.yaml"


def test_a_missing_data_file_is_a_plain_error_and_the_route_answers_503(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from motor_ai_sim import api
    gone = tmp_path / "app" / "config" / "mesh_presets.yaml"
    with pytest.raises(MP.PresetError) as e:
        MP.load(gone)
    assert str(gone) in str(e.value) and "not available" in str(e.value)
    monkeypatch.setattr(MP, "data_path", lambda base=None: gone)
    monkeypatch.setattr(api, "get_config", lambda: {"geometry": _machine(80.0, 12, 10)})
    with pytest.raises(HTTPException) as h:
        api.get_mesh_presets()
    assert h.value.status_code == 503 and str(gone) in str(h.value.detail)


# ── step (c): the optimiser, the headless runner and the final points use the presets ───────────────────────────────
def test_fill_body_completes_what_the_body_leaves_out_and_lists_what_it_states_otherwise():
    g = _machine(80.0, 12, 10)
    out = MP.fill_body("optimization", {"mesh_size_mm": 9.0, "x_other": 1}, g, steps_key="steps_per_period")
    body, rec = out["body"], out["record"]
    assert body["steps_per_period"] == 48 and body["gap_layers"] == 1.0 and body["x_other"] == 1
    assert body["mesh_size_mm"] == 9.0                                   # stated: stays
    assert rec["custom"] and set(rec["overridden"]) == {"mesh_size_mm"}
    assert json.loads(body["component_mesh"])["coil_rel"] == 1.0
    clean = MP.fill_body("optimization", {}, g)["record"]
    assert not clean["custom"] and clean["version"] == MP.version()


def test_the_simulation_preset_reads_96_or_more():
    g = _machine(80.0, 12, 10)
    base = MP.fill_body("simulation", {}, g)["body"]["n_steps_per_period"]
    assert base >= 96
    assert not MP.fill_body("simulation", {"n_steps_per_period": base + 24}, g)["record"]["custom"]
    assert MP.fill_body("simulation", {"n_steps_per_period": base - 24}, g)["record"]["custom"]
    assert MP.fill_body("optimization", {"steps_per_period": 72}, g, steps_key="steps_per_period")["record"]["custom"]


def _headless_req(**body):
    return {"mode": "descent", "descent": dict(body)}


def test_the_headless_runner_always_runs_the_optimization_preset():
    from motor_ai_sim import optimize_headless as H
    g = _machine(80.0, 12, 10)
    cfg = {"geometry": g, "mesh": {"gap_layers": 2, "mesh_size_mm": 4.0, "component_mesh": {"stator": 9}}}
    req = _headless_req(variables=[])
    rec = H.apply_optimization_preset(req, cfg, "descent")
    res = rec["resolved"]
    assert not rec["custom"] and rec["name"] == "optimization"
    assert req["descent"]["steps_per_period"] == 48 and req["descent"]["gap_layers"] == 1.0
    # the machine's own Mesh block is replaced by the preset (the evals read the sizes from there)
    assert cfg["mesh"]["gap_layers"] == 1.0 and cfg["mesh"]["mesh_size_mm"] == res["mesh_size_mm"]
    assert cfg["mesh"]["component_mesh"] == res["component_mesh"] and cfg["mesh"]["n_sectors"] == res["n_sectors"]


def test_the_headless_runner_refuses_other_settings_unless_a_reason_is_given():
    from motor_ai_sim import optimize_headless as H
    g = _machine(80.0, 12, 10)
    with pytest.raises(H.Refused) as e:
        H.apply_optimization_preset(_headless_req(steps_per_period=96, mesh_size_mm=0.5),
                                    {"geometry": g}, "descent")
    assert "steps_per_period 96" in str(e.value) and "mesh_size_mm" in str(e.value) and "preset" in str(e.value)
    req = dict(_headless_req(steps_per_period=96), preset_override_reason="owner asked for a 96-step check")
    rec = H.apply_optimization_preset(req, {"geometry": g}, "descent")
    assert rec["custom"] and rec["override_reason"].startswith("owner") and req["descent"]["steps_per_period"] == 96


def test_a_request_without_a_preset_is_unchanged_and_a_preset_fills_only_what_it_leaves_out():
    from motor_ai_sim.routes import optimization as O
    plain = O.DescentRequest()
    same, rec = O._with_preset(plain)
    assert same is plain and rec is None
    import motor_ai_sim.routes.optimization as mod
    g = _machine(80.0, 12, 10)
    orig = mod.get_config
    mod.get_config = lambda *a, **k: {"geometry": g}
    try:
        new, rec = O._with_preset(O.DescentRequest(preset="optimization", mesh_size_mm=3.0))
    finally:
        mod.get_config = orig
    assert new.steps_per_period == 48 and new.gap_layers == 1.0 and new.mesh_size_mm == 3.0
    assert rec["custom"] and "mesh_size_mm" in rec["overridden"] and "component_mesh" in rec["not_applied"]


def test_the_coupled_run_takes_the_simulation_preset_only_when_asked(monkeypatch):
    from motor_ai_sim.routes import coupled as C
    import motor_ai_sim.config as cfgmod
    g = _machine(80.0, 12, 10)
    monkeypatch.setattr(cfgmod, "get_config", lambda *a, **k: {"geometry": g})
    body = {"I_phase_rms": 10}
    same, rec = C._with_mesh_preset(body)
    assert same is body and rec is None
    out, rec = C._with_mesh_preset({"I_phase_rms": 10, "preset": "simulation"})
    assert out["n_steps_per_period"] >= 96 and out["gap_layers"] == 2.0 and "preset" not in out and not rec["custom"]
    with pytest.raises(Exception) as e:
        C._with_mesh_preset({"preset": "fast"})
    assert "unknown mesh preset" in str(e.value.detail)
