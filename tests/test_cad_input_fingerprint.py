"""CAD freshness tracks build inputs without reacting to duty-only saves."""
from __future__ import annotations

import json
from pathlib import Path

import yaml


def _sandbox(tmp_path, monkeypatch):
    from motor_ai_sim.cad import store

    root = tmp_path / "repo"
    cad = root / "cad"
    dies = tmp_path / "dies"
    die_dir = dies / "Motor"
    (cad / "reference" / "phase_board").mkdir(parents=True)
    template = cad / "motor_templates" / "segmented_flatwire"
    template.mkdir(parents=True)
    (template / "__init__.py").write_text('TEMPLATE_VERSION = "test"\n', encoding="utf-8")
    (template / "part.py").write_text("VALUE = 1\n", encoding="utf-8")
    (cad / "reference" / "Motor.json").write_text("{}", encoding="utf-8")
    (cad / "reference" / "Motor_fusion_values.json").write_text("[]", encoding="utf-8")
    (cad / "reference" / "phase_board" / "board.step").write_bytes(b"board-a")
    (root / "config").mkdir()
    (root / "config" / "fusion_param_map.yaml").write_text("map: {}\n", encoding="utf-8")
    (root / "config" / "motor_config.yaml").write_text("geometry_schema: {}\n", encoding="utf-8")
    for path, data in (("src/motor_ai_sim/routes/_validation.py", "DERIVED_GEOMETRY_NAMES = set()\n"),
                       ("src/motor_ai_sim/routes/fusion.py", "FUSION_EXCLUDED_NAMES = set()\n"),
                       ("src/motor_ai_sim/cad/build.py", "VALUE = 1\n"),
                       ("src/motor_ai_sim/cad/store.py", "VALUE = 1\n"),
                       ("src/motor_ai_sim/cad/manifest.py", "VALUE = 1\n"),
                       ("src/motor_ai_sim/cad/bom.py", "VALUE = 1\n"),
                       ("src/motor_ai_sim/cad/bom_store.py", "VALUE = 1\n")):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data, encoding="utf-8")

    die_dir.mkdir(parents=True)
    (die_dir / "die.yaml").write_text(yaml.safe_dump({
        "geometry": {"slot_width": 2.0},
        "part_materials": {"housing": "aluminum"},
    }), encoding="utf-8")
    (die_dir / "L12.yaml").write_text(yaml.safe_dump({
        "geometry_overrides": {"motor_length": 12.0},
        "materials": {"core": "steel"},
        "winding": {"turns": 3},
        "bearings": {"front": {"card": "B1"}},
        "cad_overrides": {"holder_lip_t": 0.9},
        "duty": {"rpm": 1000},
    }), encoding="utf-8")
    materials = tmp_path / "materials.yaml"
    materials.write_text(yaml.safe_dump({"steel_cards": {
        "steel": {"density": 7.8}, "steel2": {"density": 8.0},
        "aluminum": {"density": 2.7},
    }}), encoding="utf-8")
    bearings = tmp_path / "bearings.yaml"
    bearings.write_text(yaml.safe_dump({"bearings": {
        "B1": {"d": 8, "D": 16, "B": 4}, "B2": {"d": 8, "D": 18, "B": 4},
    }}), encoding="utf-8")
    monkeypatch.setattr(store, "ROOT", str(root))
    monkeypatch.setattr(store, "CAD_DIR", str(cad))
    return root, cad, dies, die_dir, materials, bearings


def _hash(dies, materials, bearings):
    from motor_ai_sim.cad.input_fingerprint import current_build_inputs
    return current_build_inputs(str(dies), "Motor", "L12", materials_library=str(materials),
                                bearings_library=str(bearings))["build_inputs_hash"]


def test_fingerprint_covers_each_saved_input_class_and_ignores_duty(tmp_path, monkeypatch):
    root, cad, dies, die_dir, materials, bearings = _sandbox(tmp_path, monkeypatch)
    base = _hash(dies, materials, bearings)

    # Each value mutation gets its own fresh baseline and is restored afterward.
    die_file, cfg_file = die_dir / "die.yaml", die_dir / "L12.yaml"
    cases = [
        (die_file, lambda d: d["geometry"].update(slot_width=2.1)),
        (cfg_file, lambda d: d["geometry_overrides"].update(motor_length=12.1)),
        (cfg_file, lambda d: d["winding"].update(turns=4)),
        (cfg_file, lambda d: d["materials"].update(core="steel2")),
        (cfg_file, lambda d: d["bearings"]["front"].update(card="B2")),
        (cfg_file, lambda d: d["cad_overrides"].update(holder_lip_t=1.0)),
        (die_file, lambda d: d["part_materials"].update(housing="steel")),
    ]
    for path, edit in cases:
        before = path.read_text("utf-8")
        doc = yaml.safe_load(before)
        edit(doc)
        path.write_text(yaml.safe_dump(doc), encoding="utf-8")
        assert _hash(dies, materials, bearings) != base
        path.write_text(before, encoding="utf-8")
        assert _hash(dies, materials, bearings) == base

    doc = yaml.safe_load(cfg_file.read_text("utf-8"))
    doc["duty"]["rpm"] = 1200
    cfg_file.write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert _hash(dies, materials, bearings) == base

    # The complete content of a selected card participates, while an unrelated
    # card would not.
    material_doc = yaml.safe_load(materials.read_text("utf-8"))
    material_doc["steel_cards"]["steel"]["density"] = 7.9
    materials.write_text(yaml.safe_dump(material_doc), encoding="utf-8")
    assert _hash(dies, materials, bearings) != base
    material_doc["steel_cards"]["steel"]["density"] = 7.8
    material_doc["steel_cards"]["unassigned"] = {"density": 4.0}
    materials.write_text(yaml.safe_dump(material_doc), encoding="utf-8")
    assert _hash(dies, materials, bearings) == base
    materials.write_text(yaml.safe_dump({"steel_cards": {
        "steel": {"density": 7.8}, "steel2": {"density": 8.0}, "aluminum": {"density": 2.7},
    }}), encoding="utf-8")
    bearing_doc = yaml.safe_load(bearings.read_text("utf-8"))
    bearing_doc["bearings"]["B1"]["D"] = 17
    bearings.write_text(yaml.safe_dump(bearing_doc), encoding="utf-8")
    assert _hash(dies, materials, bearings) != base


def test_build_fingerprint_uses_the_loaded_input_snapshot(tmp_path, monkeypatch):
    from motor_ai_sim.cad import store
    from motor_ai_sim.cad.input_fingerprint import fingerprint_effective

    _root, _cad, dies, die_dir, materials, bearings = _sandbox(tmp_path, monkeypatch)
    eff = store.load_effective(str(dies), "Motor", "L12", materials_library=str(materials),
                               bearings_library=str(bearings))
    before = fingerprint_effective(eff)["build_inputs_hash"]
    material_doc = yaml.safe_load(materials.read_text("utf-8"))
    material_doc["steel_cards"]["steel"]["density"] = 9.0
    materials.write_text(yaml.safe_dump(material_doc), encoding="utf-8")
    die_file = die_dir / "die.yaml"
    die_doc = yaml.safe_load(die_file.read_text("utf-8"))
    die_doc["geometry"]["slot_width"] = 2.5
    die_file.write_text(yaml.safe_dump(die_doc), encoding="utf-8")
    assert fingerprint_effective(eff)["build_inputs_hash"] == before
    new_eff = store.load_effective(str(dies), "Motor", "L12", materials_library=str(materials),
                                   bearings_library=str(bearings))
    assert fingerprint_effective(new_eff)["build_inputs_hash"] != before
def test_fingerprint_covers_template_reference_mapping_and_exporter_content(tmp_path, monkeypatch):
    root, cad, dies, die_dir, materials, bearings = _sandbox(tmp_path, monkeypatch)
    from motor_ai_sim.cad.input_fingerprint import current_build_inputs

    def fingerprint():
        return current_build_inputs(str(dies), "Motor", "L12", materials_library=str(materials),
                                    bearings_library=str(bearings))["build_inputs_hash"]

    base = fingerprint()
    changed_files = [
        cad / "motor_templates" / "segmented_flatwire" / "part.py",
        cad / "reference" / "Motor.json",
        cad / "reference" / "Motor_fusion_values.json",
        cad / "reference" / "phase_board" / "board.step",
        root / "config" / "fusion_param_map.yaml",
        root / "src/motor_ai_sim/cad/build.py",
        root / "src/motor_ai_sim/cad/bom.py",
    ]
    for path in changed_files:
        before = path.read_bytes()
        path.write_bytes(before + b"\n# changed\n")
        assert fingerprint() != base, path
        path.write_bytes(before)
        assert fingerprint() == base, path

    # motor_config is live simulation state: duty points do not affect CAD,
    # while the geometry schema used by the mapper does.
    motor_cfg = root / "config" / "motor_config.yaml"
    motor_cfg.write_text("simulation:\n  rpm: 1000\ngeometry_schema:\n  slot_width:\n    unit: mm\n    description: slot\n", encoding="utf-8")
    mapped = fingerprint()
    motor_cfg.write_text("simulation:\n  rpm: 1400\ngeometry_schema:\n  slot_width:\n    unit: mm\n    description: slot\n", encoding="utf-8")
    assert fingerprint() == mapped
    motor_cfg.write_text("simulation:\n  rpm: 1400\ngeometry_schema:\n  slot_width:\n    unit: cm\n    description: slot\n", encoding="utf-8")
    assert fingerprint() != mapped


def test_queue_dedupes_and_preserves_new_request_arriving_during_build(tmp_path, monkeypatch):
    from motor_ai_sim.cad import rebuild_queue as queue

    root = tmp_path / "cad"
    first = queue.request(root, "Motor", "L12", build_inputs_hash="a" * 64)
    again = queue.request(root, "Motor", "L12", build_inputs_hash="a" * 64)
    assert first["requested_at"] == again["requested_at"]

    calls = []

    class Result:
        returncode = 0
        stdout = "built"

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "--preview" in cmd:
            queue.request(root, "Motor", "L12", build_inputs_hash="b" * 64)
        return Result()

    monkeypatch.setattr(queue.subprocess, "run", fake_run)
    done = queue.run_one(root, first)
    assert "--preview" in calls[0]
    assert "--preview" not in calls[1]
    assert done["state"] == "pending"
    assert done["build_inputs_hash"] == "b" * 64
    assert queue.pending(root)[0]["build_inputs_hash"] == "b" * 64


def test_legacy_manifest_is_stale_and_status_exposes_rebuilding(tmp_path):
    from motor_ai_sim.cad import rebuild_queue as queue

    legacy = queue.status(tmp_path, "Motor", "L12", "new", manifest={"cad_overrides_hash": "none"})
    assert legacy["stale"] is True and legacy["rebuilding"] is False
    rec = queue.request(tmp_path, "Motor", "L12", build_inputs_hash="new")
    rec.update(state="building", active_build_inputs_hash="old")
    queue._write(tmp_path, rec)
    status = queue.status(tmp_path, "Motor", "L12", "new", manifest={"build_inputs_hash": "old"})
    assert status["stale"] and status["rebuilding"] and status["state"] == "building"


def test_active_material_sync_refuses_mismatched_live_machine(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from motor_ai_sim.routes import family

    cfg_file = tmp_path / "L12.yaml"
    cfg_doc = {"materials": {"core": "old"}, "winding": {"turns": 3}}
    die_doc = {"geometry": {"stator_diameter": 40.0, "num_slots": 12, "num_poles": 14}}
    monkeypatch.setattr(family, "_read_ctx", lambda: {"die": "Motor", "config": "L12"})
    monkeypatch.setattr(family, "_cfg_file", lambda die, cfg: cfg_file)
    monkeypatch.setattr(family, "_die_file", lambda die: tmp_path / "die.yaml")
    monkeypatch.setattr(family, "_load_yaml", lambda p, what: cfg_doc if str(p) == str(cfg_file) else die_doc)
    live = {"geometry": {"stator_diameter": 85.0, "num_slots": 12, "num_poles": 14},
            "winding": {"turns": 3}}
    monkeypatch.setattr("motor_ai_sim.config.get_config", lambda: live)
    saves = []
    monkeypatch.setattr(family, "_save_yaml", lambda *a: saves.append(a))

    try:
        family.sync_active_die_build_inputs("materials", {"core": "new"})
    except HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("foreign live geometry must not be mirrored into the active die")
    assert cfg_doc["materials"] == {"core": "old"}
    assert saves == []
