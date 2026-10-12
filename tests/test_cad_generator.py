"""Unit tests of the CAD generator (motor_ai_sim.cad): store -> profile mapping, fingerprints, manifest, camera,
CLI dry run.  No FreeCAD needed (the FreeCAD side is verified by cad/fc_scripts on the server, docs/CAD_GENERATOR.md)."""
import json
import math
import os
import sys

import pytest
import yaml

from motor_ai_sim.cad import build, manifest, runner, store

CAD_DIR = store.CAD_DIR
if CAD_DIR not in sys.path:
    sys.path.insert(0, CAD_DIR)


@pytest.fixture()
def dies(tmp_path):
    d = tmp_path / "dies" / "TEST die"
    d.mkdir(parents=True)
    (d / "die.yaml").write_text(yaml.safe_dump({"geometry": {
        "motor_length": 20.0, "air_gap": 0.2, "magnet_height": 5.7, "insulation_thickness": 0.07,
        "num_seg": 2, "stator_outer_radius": 99.0, "wire_split": 1, "wire_parallel": 1}}))
    (d / "L12.yaml").write_text(yaml.safe_dump({
        "geometry_overrides": {"motor_length": 12.0, "insulation_thickness": 0.06},
        "materials": {"magnet": "MAGNETX"}, "winding": {"connection": "2S"}}))
    lib = tmp_path / "materials_library.yaml"
    lib.write_text(yaml.safe_dump({"magnet": {"MAGNETX": {"Br": 1.2, "bh_curve": [[0, 0]]}}}))
    return str(tmp_path / "dies"), str(lib)


def test_effective_merges_overrides_and_hashes_files(dies):
    dd, lib = dies
    eff = store.load_effective(dd, "TEST die", "L12", lib)
    assert eff["geometry"]["motor_length"] == 12.0 and eff["geometry"]["air_gap"] == 0.2
    assert eff["variant"] == "L12" and eff["config"] == "L12.yaml"
    assert set(eff["files"]) == {"die.yaml", "L12.yaml", "materials_library.yaml"}
    assert eff["material_cards"]["MAGNETX"]["Br"] == 1.2 and "bh_curve" not in eff["material_cards"]["MAGNETX"]
    with pytest.raises(FileNotFoundError):
        store.load_effective(dd, "TEST die", "L99", lib)


def test_profile_takes_simulation_values_and_excludes_derived(dies):
    dd, lib = dies
    eff = store.load_effective(dd, "TEST die", "L12.yaml", lib)
    pj, rep = store.make_profile(eff, store.load_mapping(), stamp="t")
    p = {x["name"]: x for x in pj["user_parameters"]}
    assert p["motor_length"]["value"] == 12.0 and p["motor_length"]["freecad_expression"] == "12 mm"
    assert p["insulation_thickness"]["value"] == 0.06 and p["air_gap"]["value"] == 0.2
    assert pj["profile"]["motor_id"] == "TEST_die_L12"
    assert pj["profile"]["materials"]["Magnet"] == "MAGNETX"
    names = {r["sim_key"] for r in rep["parameters"]}
    assert "stator_outer_radius" not in names            # derived: never a CAD input
    assert "wire_split" in rep["summary"]["sim_only"]     # no CAD geometry: reported, not silently dropped
    assert rep["summary"]["freecad_equals_sim"]


def test_geo_sig_is_the_presets_dialect_and_fingerprint_is_stable(dies):
    from motor_ai_sim.routes.presets import _geo_sig
    geo = {"a": 3.0, "b": 0.25, "c": True, "d": "x", "e": 12}
    assert store.geo_sig(geo) == _geo_sig(geo)
    dd, lib = dies
    mp = store.load_mapping()
    f1 = store.parameter_fingerprint(store.load_effective(dd, "TEST die", "L12", lib), mp)
    f2 = store.parameter_fingerprint(store.load_effective(dd, "TEST die", "L12", lib), mp)
    assert f1 == f2 and len(f1["cad_param_hash"]) == 64
    eff = store.load_effective(dd, "TEST die", "L12", lib)
    eff["geometry"]["air_gap"] = 0.25
    assert store.parameter_fingerprint(eff, mp)["cad_param_hash"] != f1["cad_param_hash"]


def test_parsed_derived_sets_do_not_hide_cad_parameters():
    """The mapping parses _validation.py / fusion.py (no heavy imports in the FreeCAD image).  The runtime sets are
    larger (they add motor_geometry's names): the extras must not be names the CAD template takes as inputs."""
    from motor_ai_sim.routes import _validation, fusion
    mp = store.load_mapping()
    base = json.load(open(store.BASE_PROFILE, encoding="utf-8"))
    cad_inputs = {p["name"] for p in base["user_parameters"] if p.get("role") == "input"}
    assert set(mp["derived"]) <= set(_validation.DERIVED_GEOMETRY_NAMES)
    assert not ((set(_validation.DERIVED_GEOMETRY_NAMES) - set(mp["derived"])) & cad_inputs)
    assert set(mp["fusion_excluded"]) == set(fusion.FUSION_EXCLUDED_NAMES)


def test_evaluate_units_and_references():
    v = store.evaluate({"a": "2 mm", "b": "a * 3 + 1", "c": "sqrt(b)"})
    assert v["a"] == 2.0 and v["b"] == 7.0 and abs(v["c"] - math.sqrt(7)) < 1e-12
    with pytest.raises(ValueError):
        store.evaluate({"x": "y", "y": "x"})


def test_back_reports_differences_and_never_writes_the_store(dies, tmp_path):
    dd, lib = dies
    eff = store.load_effective(dd, "TEST die", "L12", lib)
    names = ("die.yaml", "L12.yaml")
    before = {p: store.sha256_file(os.path.join(dd, "TEST die", p)) for p in names}
    cells = {"motor_length": "14 mm", "air_gap": "0.2 mm", "insulation_thickness": "0.06 mm"}
    res = store.back(eff, store.load_mapping(), cells, str(tmp_path / "sync"), propose=True)
    d = {x["sim_key"]: x for x in res["differences"]}
    assert set(d) == {"motor_length"} and d["motor_length"]["target"].startswith("L12.yaml geometry_overrides")
    assert any(f.endswith(".patch.yaml") and os.path.exists(f) for f in res["files"])
    assert before == {p: store.sha256_file(os.path.join(dd, "TEST die", p)) for p in names}


def test_manifest_fields(monkeypatch, dies):
    dd, lib = dies
    eff = store.load_effective(dd, "TEST die", "L12", lib)
    pj, rep = store.make_profile(eff, store.load_mapping(), stamp="t")
    monkeypatch.setenv("MOTRES_GIT_SHA", "abc123")
    m = manifest.build_manifest(eff, pj, rep, {"gate": {"ok": True}, "volumes": {"Core": 1.0}, "sketches_total": 12,
                                               "sketches_fully_constrained": 12},
                                {"n_solid_objects": 22}, {}, "1.1.3", 1.5)
    assert m["die"] == "TEST die" and m["config"] == "L12" and m["git_sha"] == "abc123"
    assert m["freecad_version"] == "1.1.3" and m["param_hash"] == rep["fingerprint"]["cad_param_hash"]
    assert m["geo_sig_hash"] == rep["fingerprint"]["geo_sig_hash"] and m["template"]["name"] == "segmented_flatwire"
    assert m["template"]["version"] != "unknown" and len(m["template"]["sha256"]) == 64
    assert m["checks"]["gate_ok"] and m["checks"]["step_solids"] == 22


def test_cli_dry_run_writes_profile(dies, tmp_path):
    dd, lib = dies
    out = tmp_path / "out" / "L12"
    rc = build.main(["--die", "TEST die", "--config", "L12", "--dies-dir", dd, "--materials", lib,
                     "--out-dir", str(out), "--dry-run"])
    assert rc == 0                                     # a dry run leaves its work directory next to the target
    work = [p for p in (tmp_path / "out").iterdir() if p.name.startswith("L12.building-")]
    assert work and (work[0] / "params.json").exists() and (work[0] / "pull_report.json").exists()
    assert build.main(["--die", "TEST die", "--config", "L99", "--dies-dir", dd, "--out-dir", str(out)]) == 2


def test_camera_sits_on_the_iso_diagonal_at_a_fixed_distance():
    from motor_templates.segmented_flatwire import view
    bb = (-20, -20, -10, 20, 20, 10)
    cam = view.camera_string(bb)
    pos = [float(x) for x in cam.split("position")[1].split("\n")[0].split()]
    assert "OrthographicCamera" in cam and all(abs(c) > 1 for c in pos[:2])
    assert abs(math.sqrt(sum(c * c for c in pos)) - 2 * math.sqrt(40 ** 2 + 40 ** 2 + 20 ** 2)) < 1e-3
    assert abs(sum(c * c for c in view.ISO_Q) - 1) < 1e-4


def test_find_freecadcmd_env(monkeypatch, tmp_path):
    monkeypatch.setenv("FREECADCMD", str(tmp_path / "fc"))
    assert runner.find_freecadcmd() == str(tmp_path / "fc")


def _template_sources():
    d = os.path.join(CAD_DIR, "motor_templates", "segmented_flatwire")
    return {f: open(os.path.join(d, f), encoding="utf-8").read() for f in ("core.py", "coil.py", "rotor.py", "magnet.py")}


def test_no_left_right_constraint_pairs_in_sketch_builders():
    """Owner rule 2026-10-12: one half is dimensioned, the other half is a Symmetric mirror - no _L/_R dimension pairs."""
    import re
    for f, src in _template_sources().items():
        names = re.findall(r'name="([A-Za-z0-9_]+)"', src)
        assert names, f
        bad = [n for n in names if re.search(r"_(L|R)$", n)]
        assert not bad, (f, bad)
    for f in ("core.py", "rotor.py", "magnet.py"):
        assert 'mirror_chain' in _template_sources()[f]          # half + mirror pattern


def test_feature_counts_are_named_derived_rows():
    base = json.load(open(store.BASE_PROFILE, encoding="utf-8"))
    rows = {p["name"]: p for sec in ("user_parameters", "named_parameters_sketch3", "named_parameters_active_part")
            for p in base[sec]}
    want = ("core_sectors", "coil_count", "rotor_poles", "magnet_count", "coil_layer_copies", "coil_layer_span",
            "coil_layer_pitch", "coil_leg_length", "coil_x_in", "coil_top_y", "coil_bridge_length", "coil_lead_total")
    for n in want:
        assert rows[n]["role"] == "derived" and rows[n]["comment"], n
    vals = store.evaluate({n: p["freecad_expression"] for n, p in rows.items()})
    assert vals["core_sectors"] == 6 and vals["rotor_poles"] == 14 and vals["coil_layer_copies"] == 6
    src = "".join(_template_sources().values()) + open(os.path.join(CAD_DIR, "motor_templates", "segmented_flatwire", "build.py"),
                                                      encoding="utf-8").read()
    for inline in ('"num_seg * 3"', 'P + "num_seg * "', '"Params.num_seg * 3"'):
        assert inline not in src
    for n in ("core_sectors", "rotor_poles", "coil_count", "magnet_count", "coil_layer_copies"):
        assert n in src
