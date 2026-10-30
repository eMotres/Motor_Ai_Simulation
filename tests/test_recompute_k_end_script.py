"""scripts/recompute_k_end.py: old -> new k_end of every stored INPUT; the cards (results, runs, catalog, frozen revisions) untouched."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from motor_ai_sim import coil_build
from motor_ai_sim.cad import store

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "recompute_k_end.py"


@pytest.fixture(scope="module")
def rk():
    spec = importlib.util.spec_from_file_location("recompute_k_end", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _template_rules(monkeypatch, tmp_path):
    """No private reference files: the built coil is the template's base rules (deterministic on every host)."""
    monkeypatch.setattr(store, "REFERENCE_DIR", str(tmp_path / "no_reference"))
    monkeypatch.setattr(store, "BASE_PROFILE", str(tmp_path / "no_reference" / "CIANO14_40_12.json"))
    coil_build.clear_cache()
    yield
    coil_build.clear_cache()


DIE_GEO = {"stator_diameter": 40, "slot_height": 6.1, "core_thickness": 2.2, "num_seg": 1, "num_slots_per_segment": 12,
           "num_poles_per_segment": 14, "air_gap": 0.4, "tooth_width": 3.3, "tooth2_width": 2.0, "cut_width": 2.0,
           "insulation_thickness": 0.07, "wire_width": 2.5, "wire_height": 0.6, "wire_spacing_x": 0.1, "wire_spacing_y": 0.1,
           "num_wires_per_slot": 7, "motor_length": 20}


def _duty(name: str, k: float) -> dict:
    return {
        "name": name, "rpm": 3000, "current_arms": 10.0,
        "mesh": {"sim.rpm": 3000, "sim.endWinding": k, "sim.endWindingGeo": k, "sim.coilTemp": 120},
        "summary": {"end_winding_factor": round(k, 2), "R_phase_ohm": 0.1, "T_em_avg_Nm": 0.4},
        "result": {"efficiency_pct": 90.0, "p_stranded_w": 50.0, "loss_w": 70.0, "mass_kg": 0.09},
        "runs": {"current": {"settings": {"sim.endWinding": k, "sim.endWindingGeo": k},
                             "summary": {"end_winding_factor": round(k, 2)}}},
    }


def _tree(root: Path) -> dict:
    files = {}
    for where, cfg, k_cfg in (("shared/dies/D40", "L20", 1.456), ("workspaces/ws1/dies/D40", "L20", None),
                              ("shared/dies/FROZEN", "L20", 1.456)):
        d = root / where
        d.mkdir(parents=True)
        (d / "die.yaml").write_text(yaml.safe_dump({"name": d.name, "geometry": DIE_GEO}, sort_keys=False), encoding="utf-8")
        doc = {"name": cfg, "die": d.name, "geometry_overrides": {"motor_length": 20}, "winding": {},
               "duties": [_duty("rated", 1.456), {"name": "bare", "rpm": 1000}]}
        if k_cfg is not None:
            doc["end_winding_factor"] = k_cfg
        p = d / f"{cfg}.yaml"
        p.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
        files[str(p)] = p.read_bytes()
    rev = root / "shared" / "motor_revisions"
    rev.mkdir(parents=True)
    (rev / "revisions.json").write_text(json.dumps({"lineages": {"x": {"revisions": [
        {"die": "FROZEN", "config": "L20", "frozen": True}]}}}), encoding="utf-8")
    cat = root / "shared" / "motor_catalog.json"
    cat.write_text(json.dumps({"motors": [{"id": "D40_L20", "end_winding_factor": 1.456}]}), encoding="utf-8")
    files[str(cat)] = cat.read_bytes()
    return files


def test_dry_run_reports_and_writes_nothing(rk, tmp_path):
    files = _tree(tmp_path)
    rep = rk.run(tmp_path, apply=False)
    for f, b in files.items():
        assert Path(f).read_bytes() == b, f
    assert not list(tmp_path.rglob("*.bak-kend-coil-*"))
    shared = next(e for e in rep["configs"] if "shared" in e["file"] and e["die"] == "D40")
    k = shared["coil"]["k_end"]
    assert k > 1.456                                       # the built coil is longer than the semicircle (1.456 at L20)
    assert "template base coil rules" in shared["coil"]["source"]
    cfg_row = shared["rows"][0]
    assert (cfg_row["old"], cfg_row["new"]) == (1.456, k) and cfg_row["ratio"] == pytest.approx(k / 1.456, abs=1e-4)
    duty = next(r for r in shared["rows"] if r["duty"] == "rated")
    assert duty["old"] == 1.456 and duty["new"] == k
    assert duty["est_dP_cu_w_max"] == pytest.approx(50.0 * (k / 1.456 - 1), abs=0.06)
    bare = next(r for r in shared["rows"] if r["duty"] == "bare")
    assert bare["new"] is None                              # no stored setting: nothing to write
    ws = next(e for e in rep["configs"] if "workspaces" in e["file"])
    assert ws["rows"][0]["new"] is None and "not stored" in ws["rows"][0]["note"]
    assert [e["die"] for e in rep["skipped_frozen"]] == ["FROZEN"]
    assert not rep["errors"]


def test_apply_changes_only_the_k_end_inputs(rk, tmp_path):
    files = _tree(tmp_path)
    shared = tmp_path / "shared" / "dies" / "D40" / "L20.yaml"
    before = yaml.safe_load(shared.read_text(encoding="utf-8"))
    rep = rk.run(tmp_path, apply=True)
    assert not rep["errors"]
    after = yaml.safe_load(shared.read_text(encoding="utf-8"))
    k = next(e for e in rep["configs"] if e["file"] == str(shared))["coil"]["k_end"]
    # the inputs moved ...
    assert after["end_winding_factor"] == k
    assert after["duties"][0]["mesh"]["sim.endWinding"] == k and after["duties"][0]["mesh"]["sim.endWindingGeo"] == k
    # ... and NOTHING else: results, summaries, stored runs and their settings are the cards / run records
    changed = rk._diff_paths(before, after)
    assert sorted(changed) == ["/duties[0]/mesh/sim.endWinding", "/duties[0]/mesh/sim.endWindingGeo", "/end_winding_factor"]
    assert after["duties"][0]["summary"] == before["duties"][0]["summary"]
    assert after["duties"][0]["result"] == before["duties"][0]["result"]
    assert after["duties"][0]["runs"] == before["duties"][0]["runs"]
    # backup of the old file, byte for byte
    baks = list(shared.parent.glob("L20.yaml.bak-kend-coil-*"))
    assert len(baks) == 1 and baks[0].read_bytes() == files[str(shared)]
    # the frozen revision and the catalog are untouched
    frozen = tmp_path / "shared" / "dies" / "FROZEN" / "L20.yaml"
    assert frozen.read_bytes() == files[str(frozen)]
    cat = tmp_path / "shared" / "motor_catalog.json"
    assert cat.read_bytes() == files[str(cat)]
    # a second run has nothing left to do
    rep2 = rk.run(tmp_path, apply=True)
    assert rep2["written"] == []


def test_the_write_guard_refuses_anything_outside_the_inputs(rk):
    assert rk.allowed_path("/end_winding_factor")
    assert rk.allowed_path("/duties[3]/mesh/sim.endWinding")
    assert rk.allowed_path("/duties[0]/mesh/sim.endWindingGeo")
    for bad in ("/duties[0]/summary/end_winding_factor", "/duties[0]/runs/current/settings/sim.endWinding",
                "/duties[0]/result/p_stranded_w", "/geometry_overrides/motor_length", "/duties[0]/mesh/sim.rpm"):
        assert not rk.allowed_path(bad), bad


def test_dry_run_on_a_plain_dies_directory(rk, tmp_path):
    _tree(tmp_path)
    rep = rk.run(tmp_path / "shared" / "dies", apply=False)
    assert {e["die"] for e in rep["configs"]} == {"D40"}
    assert [e["die"] for e in rep["skipped_frozen"]] == ["FROZEN"]     # motor_revisions beside shared/dies
    assert rep["errors"] == []
