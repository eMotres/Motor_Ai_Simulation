# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The FRAMELESS assembly variant of the CAD generator (owner decision 2026-10-15): CIANO28 85 L13 becomes a kit for robots -
stator with coils and the phase board + rotor with magnets.  Housing, holders, rotor carriers / hub, bearings, shaft, screws,
housing rings and the propeller plate are not in it.  ONE parametric model with parts omitted, never a second model.

No FreeCAD needed here: the store side (``cad_variant`` key, override, hash), the template rules that are pure Python
(pre-check, the omitted-object list), the BOM of a kit built from model dumps, and the mass of the kit on the real server BOM file
of the 85.  The FreeCAD build itself is covered by the local build evidence in docs/CAD_GENERATOR.md."""
from __future__ import annotations

import ast
import copy
import csv
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from motor_ai_sim.cad import bom, bom_store, build, manifest, store
from motor_ai_sim.modules import motor_mass as MM

CAD_DIR = store.CAD_DIR
if CAD_DIR not in sys.path:
    sys.path.insert(0, CAD_DIR)

FIX = Path(__file__).resolve().parent / "fixtures"
TEMPLATE = Path(CAD_DIR) / "motor_templates" / "segmented_flatwire"


@pytest.fixture()
def dies(tmp_path):
    d = tmp_path / "dies" / "TEST die"
    d.mkdir(parents=True)
    (d / "die.yaml").write_text(yaml.safe_dump({"geometry": {
        "motor_length": 20.0, "air_gap": 0.2, "magnet_height": 5.7, "insulation_thickness": 0.07,
        "num_seg": 2, "stator_outer_radius": 99.0, "wire_split": 1, "wire_parallel": 1}}))
    (d / "L12.yaml").write_text(yaml.safe_dump({
        "geometry_overrides": {"motor_length": 12.0}, "materials": {"magnet": "MAGNETX"}, "winding": {"connection": "2S"}}))
    (d / "L13.yaml").write_text(yaml.safe_dump({
        "cad_variant": "frameless", "geometry_overrides": {"motor_length": 12.0}, "materials": {"magnet": "MAGNETX"},
        "winding": {"connection": "2S"}}))
    (d / "L14.yaml").write_text(yaml.safe_dump({"cad_variant": "bare", "geometry_overrides": {"motor_length": 12.0}}))
    lib = tmp_path / "materials_library.yaml"
    lib.write_text(yaml.safe_dump({"magnet": {"MAGNETX": {"Br": 1.2, "bh_curve": [[0, 0]]}}}))
    return str(tmp_path / "dies"), str(lib)


# -- the option: configuration key, build override, hash ------------------------------------------------------------------

def test_cad_variant_defaults_to_full_and_is_read_from_the_configuration(dies):
    dd, lib = dies
    assert store.load_effective(dd, "TEST die", "L12", lib)["cad_variant"] == "full"
    eff = store.load_effective(dd, "TEST die", "L13", lib)
    assert eff["cad_variant"] == "frameless" and eff["cad_variant_from"] == "L13.yaml"


def test_the_build_call_overrides_the_configuration(dies):
    dd, lib = dies
    assert store.load_effective(dd, "TEST die", "L13", lib, cad_variant="full")["cad_variant"] == "full"
    eff = store.load_effective(dd, "TEST die", "L12", lib, cad_variant="frameless")
    assert eff["cad_variant"] == "frameless" and eff["cad_variant_from"] == "build option"


def test_an_unknown_variant_is_refused_in_words(dies, tmp_path):
    dd, lib = dies
    with pytest.raises(ValueError, match="not one of full | frameless"):
        store.load_effective(dd, "TEST die", "L14", lib)
    assert build.main(["--die", "TEST die", "--config", "L14", "--dies-dir", dd, "--materials", lib,
                       "--out-dir", str(tmp_path / "o"), "--dry-run"]) == 2


def test_a_full_profile_and_hash_are_unchanged_and_a_frameless_one_differs(dies):
    dd, lib = dies
    mp = store.load_mapping()
    full = store.load_effective(dd, "TEST die", "L12", lib)
    kit = store.load_effective(dd, "TEST die", "L12", lib, cad_variant="frameless")
    pf, rf = store.make_profile(full, mp, stamp="t")
    pk, rk = store.make_profile(kit, mp, stamp="t")
    assert "cad_variant" not in pf["profile"] and pk["profile"]["cad_variant"] == "frameless"
    assert "frameless kit" in pk["profile"]["variant"] and "frameless" not in pf["profile"]["variant"]
    # a full build keeps the hash it always had (no rebuild of the full models); the kit has its own
    base = store.canonical_hash(dict(rows={k: v for k, _f, v, _u, _d in store.primary_rows(full["geometry"], mp)},
                                     materials=full["materials"], winding=full["winding"]))
    assert rf["fingerprint"]["cad_param_hash"] == base != rk["fingerprint"]["cad_param_hash"]
    assert rf["fingerprint"]["cad_variant"] == "full" and rk["fingerprint"]["cad_variant"] == "frameless"


def test_manifest_and_cli_carry_the_variant(dies, tmp_path, monkeypatch):
    dd, lib = dies
    eff = store.load_effective(dd, "TEST die", "L13", lib)
    pj, rep = store.make_profile(eff, store.load_mapping(), stamp="t")
    monkeypatch.setenv("MOTRES_GIT_SHA", "abc123")
    m = manifest.build_manifest(eff, pj, rep, {"gate": {"ok": True}}, {}, {}, "1.1.3", 1.0)
    assert m["cad_variant"] == "frameless"
    pj2, rep2 = store.make_profile(store.load_effective(dd, "TEST die", "L12", lib), store.load_mapping(), stamp="t")
    assert manifest.build_manifest(store.load_effective(dd, "TEST die", "L12", lib), pj2, rep2, {"gate": {"ok": True}}, {}, {},
                                   "1.1.3", 1.0)["cad_variant"] == "full"
    out = tmp_path / "out" / "L12"
    assert build.main(["--die", "TEST die", "--config", "L12", "--dies-dir", dd, "--materials", lib, "--variant", "frameless",
                       "--out-dir", str(out), "--dry-run"]) == 0
    work = next(p for p in (tmp_path / "out").iterdir() if p.name.startswith("L12.building-"))
    assert json.load(open(work / "params.json", encoding="utf-8"))["profile"]["cad_variant"] == "frameless"


# -- the template: what is omitted, what the gate and the pre-check do -------------------------------------------------------

def _tuple_in(source: str, name: str):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(name)


def test_the_omitted_objects_cover_every_mechanical_body_part_link_and_array():
    from motor_templates.segmented_flatwire import frameless
    mech = (TEMPLATE / "mechanics.py").read_text(encoding="utf-8")
    needed = set()
    for n in ("MECH_BODIES", "ARRAY_BASES", "MECH_PARTS", "MECH_LINKS"):
        needed |= set(_tuple_in(mech, n))
    assert needed <= set(frameless.OMITTED_OBJECTS), needed - set(frameless.OMITTED_OBJECTS)
    assert set(_tuple_in(mech, "MECH_ARRAYS")) == set(frameless.OMITTED_LABELS)
    # the kept parts are never in the omitted list
    for kept in ("Core", "Coil", "RotorCore", "Magnet", "Coils", "MagnetArray", "PhaseBoard", "Electronics"):
        assert kept not in frameless.OMITTED_OBJECTS


def test_the_builder_skips_mechanics_and_adds_the_datums_only_for_the_kit():
    src = (TEMPLATE / "build.py").read_text(encoding="utf-8")
    assert 'if pj.get("named_parameters_mechanics") and not kit:' in src
    assert "frameless.build_mounting_datums(doc, sh)" in src and 'motor_cad_variant' in src
    assert "if gt[\"ok\"] and doc.getObject(\"Mechanics\") is not None and not kit" in (
        Path(CAD_DIR) / "fc_scripts" / "build_model.py").read_text(encoding="utf-8")


def test_variant_of_profile():
    from motor_templates.segmented_flatwire import frameless
    assert frameless.variant_of_profile({"profile": {}}) == "full"
    assert frameless.variant_of_profile({"profile": {"cad_variant": "frameless"}}) == "frameless"
    with pytest.raises(ValueError):
        frameless.variant_of_profile({"profile": {"cad_variant": "nude"}})


def _values_85():
    pj = json.load(open(Path(CAD_DIR) / "reference" / "CIANO28_85_20SW1200.json", encoding="utf-8"))
    rows = {p["name"]: p["freecad_expression"] for sec in ("user_parameters", "named_parameters_sketch3", "named_parameters_active_part",
                                                           "named_parameters_mechanics") for p in pj.get(sec, [])}
    return store.evaluate(rows), rows


def test_the_precheck_does_not_judge_the_housing_of_a_kit():
    from motor_templates.segmented_flatwire import health
    vals, rows = _values_85()
    assert health.precheck(lambda n: vals[n]) == []                      # the 85 as it is: valid in both variants
    assert health.precheck(lambda n: vals[n], frameless=True) == []
    bad = dict(vals, holder_bolt_r=vals["holder_bolt_r"] + 30.0)         # a bolt circle the holder cannot have
    full_v = health.precheck(lambda n: bad[n])
    assert full_v and "bolt" in " ".join(full_v)
    assert health.precheck(lambda n: bad[n], frameless=True) == []       # the kit has no holder: nothing to refuse
    core = dict(vals, tooth_width=vals["tooth_width"] * 4)               # but the stator is still judged
    assert health.precheck(lambda n: core[n], frameless=True)


def test_the_mounting_datums_are_bound_to_rows_the_85_has():
    from motor_templates.segmented_flatwire import frameless
    vals, rows = _values_85()
    import re
    for name, expr, need, text in frameless._PLANES:                                   # noqa: SLF001
        if expr:
            assert set(re.findall(r"Params\.(\w+)", expr)) <= set(need), name
    for name, typ, expr, need, text in frameless._PROPS:                               # noqa: SLF001
        assert set(re.findall(r"Params\.(\w+)", expr)) <= set(need), name
    have = [n for n, _e, need, _t in frameless._PLANES if all(x in vals for x in need)]   # noqa: SLF001
    assert {"Datum_Stator_front_face", "Datum_Stator_back_face", "Datum_Rotor_front_face", "Datum_Rotor_back_face",
            "Datum_Coil_lead_end", "Datum_Phase_board_top", "Datum_Phase_board_bottom"} <= set(have)
    g = lambda n: vals[n]                                                              # noqa: E731
    assert frameless._eval_expr("Params.motor_length / 2", g) == 6.5                   # noqa: SLF001
    assert frameless._eval_expr("(Params.rotor_down_r - Params.rotor_house_height) * 2", g) == pytest.approx(  # noqa: SLF001
        2 * (vals["rotor_down_r"] - vals["rotor_house_height"]))
    assert vals["rotor_down_r"] - vals["rotor_house_height"] == pytest.approx(vals["shaft_seat_r"])     # the rotor bore of the 85 = 24.6


# -- the BOM of a kit ---------------------------------------------------------------------------------------------------------

KIT_LABELS = {"Core", "Coil", "RotorCore", "Magnet", "Coils", "MagnetArray", "PhaseBoard"}


def _kit_dump():
    dump = json.load(open(FIX / "cad_bom_dump_L20.json", encoding="utf-8"))
    d = copy.deepcopy(dump)
    d["entries"] = [e for e in dump["entries"] if e["label"] in KIT_LABELS]
    d["meta"] = dict(d.get("meta") or {}, motor_cad_variant="frameless")
    return dump, d


def _bom(dump, **over):
    from tests.test_cad_bom import CARDS, COMPS_L20, MATS, WIRES
    inputs = dict(die="CIANO14 40 new", config="L20", motor_id="CIANO14_40_new_L20", materials=MATS, cards=CARDS, wire_stock=WIRES,
                  winding=dict(connection="2S", n_coils_per_phase=4),
                  sim_materials=dict(magnet="F52SH_120C", stator_core="20SW1200", rotor_core="20SW1200"),
                  sim_components=COMPS_L20, sim_total_kg=0.151, sim_source="test")
    inputs.update(over)
    return bom.build_bom(dump, inputs)


def test_the_kit_bom_lists_the_kit_and_no_housing():
    full_dump, kit_dump = _kit_dump()
    full, kit = _bom(full_dump), _bom(kit_dump)
    keys = [l["key"] for l in kit["lines"]]
    for k in ("stator_lam", "rotor_lam", "magnets", "coils", "wire", "board", "pins"):
        assert k in keys, k
    for k in ("shaft", "holder_front", "holder_back", "rings", "prop_fix", "bearings", "screws"):
        assert k not in keys, k
    assert not kit["unclassified"] and kit["header"]["cad_variant"] == "frameless" and full["header"]["cad_variant"] == "full"
    assert kit["totals"]["scope"] == "frameless kit" and "scope" not in full["totals"]
    # the kit is the full BOM minus exactly the housing / mechanics lines
    fk = {l["key"]: l for l in full["lines"]}
    gone = {"shaft", "holder_front", "holder_back", "rings", "prop_fix", "bearings", "screws"}
    assert kit["totals"]["motor_mass_g"] == pytest.approx(
        full["totals"]["motor_mass_g"] - sum(fk[k]["total_mass_g"] for k in gone), abs=0.01)
    assert kit["comparison"]["explain"][0].startswith("FRAMELESS KIT")
    assert not any("Shaft" in e for e in kit["comparison"]["explain"][1:])


def test_the_kit_csv_says_what_it_is_and_the_totals_row_keeps_its_name(tmp_path):
    _, kit_dump = _kit_dump()
    path = tmp_path / "x_BOM.csv"
    bom.write_csv(_bom(kit_dump), str(path))
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig", newline="")))
    t1 = next(r for r in rows if r["item"] == "T1")
    assert "frameless kit" in t1["part"] and float(t1["total mass g"]) > 0
    assert any(r["item"] == "E" and r["part"].startswith("FRAMELESS KIT") for r in rows)
    bf = MM.read_bom_file(path)                                         # the mass reader still finds the totals row
    assert bf.total_g == pytest.approx(float(t1["total mass g"]))
    assert not any(ln.part.lower().startswith(("shaft", "stator holder", "ball bearing", "screw")) for ln in bf.lines)


def test_a_full_bom_is_written_exactly_as_before(tmp_path):
    full_dump, _ = _kit_dump()
    path = tmp_path / "f_BOM.csv"
    bom.write_csv(_bom(full_dump), str(path))
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig", newline="")))
    assert next(r for r in rows if r["item"] == "T1")["part"] == "TOTAL mass of the BOM lines (model)"
    assert not any("FRAMELESS" in r["part"] for r in rows)


def test_the_store_side_passes_the_variant_to_the_bom(dies):
    dd, lib = dies
    eff = store.load_effective(dd, "TEST die", "L13", lib)
    pj, _ = store.make_profile(eff, store.load_mapping(), stamp="t")
    assert bom_store.assemble(eff, pj, materials_library=lib)["cad_variant"] == "frameless"
    eff2 = store.load_effective(dd, "TEST die", "L12", lib)
    pj2, _ = store.make_profile(eff2, store.load_mapping(), stamp="t")
    assert bom_store.assemble(eff2, pj2, materials_library=lib)["cad_variant"] == "full"


# -- the mass of the kit ----------------------------------------------------------------------------------------------------------

DIE, CFG = "CIANO28 85 20SW1200", "L13"
ACTIVE = {"stator_core": 126.09, "rotor_core": 64.13, "magnet": 70.49, "slot": 106.26, "shaft": 29.76}


def _setup(tmp_path, monkeypatch, *, kit, with_bom=True):
    """A stand-in 85 die whose L13 is (or is not) a kit, the REAL server BOM of the full L13 in a CAD folder, and the app's active
    parts given by that BOM's own simulation numbers (the real dies are private)."""
    from tests._component_dies import make_dies
    dies = make_dies(tmp_path)
    shutil.copytree(dies / "CIANO14 40 new", dies / DIE)
    cfg = yaml.safe_load((dies / DIE / "L12.yaml").read_text())
    cfg.update(name="L13", die=DIE)
    if kit:
        cfg["cad_variant"] = "frameless"
    (dies / DIE / "L13.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    cad = tmp_path / "cad" / DIE / CFG
    cad.mkdir(parents=True)
    if with_bom:
        shutil.copy(FIX / "server_bom" / "CIANO28_85_20SW1200_L13_BOM.csv", cad / "CIANO28_85_20SW1200_L13_BOM.csv")
    names = {"stator_core": ("Stator core", "20SW1200"), "rotor_core": ("Rotor core", "20SW1200"),
             "magnet": ("Magnets", "F52SH_120C"), "slot": ("Winding copper", "copper"), "shaft": ("Shaft", "Aluminium_6061")}
    parts = [MM.MotorPart(names[k][0], names[k][1], g / 1000.0, "magnetic", True, "computed", "", key=k) for k, g in ACTIVE.items()]
    fake = MM.MotorMass(DIE, CFG, sum(ACTIVE.values()) / 1000.0, parts)
    monkeypatch.setattr(MM, "app_motor_mass", lambda *a, **k: fake)
    MM._FULL_CACHE.clear()                                                                # noqa: SLF001
    return dies, tmp_path / "cad"


def test_a_full_configuration_keeps_its_scope_and_mass(tmp_path, monkeypatch):
    dies, cad = _setup(tmp_path, monkeypatch, kit=False)
    fm = MM.full_mass(DIE, CFG, dies_dir=dies, cad_dir=cad)
    assert fm.scope == "full motor" and not fm.kit and not fm.partial
    assert 465 < fm.total_kg * 1000 < 480                                                 # the full 85, as before
    assert {p.key for p in fm.parts if p.counted} >= {"carrier_front", "holder_front", "bearings", "screws", "board"}
    assert MM.mass_summary(DIE, CFG, dies_dir=dies, cad_dir=cad)["scope"] == "full motor"


def test_the_kit_mass_is_the_active_parts_without_the_shaft_plus_board_and_enamel(tmp_path, monkeypatch):
    dies, cad = _setup(tmp_path, monkeypatch, kit=True)
    fm = MM.full_mass(DIE, CFG, dies_dir=dies, cad_dir=cad)
    by = {p.key: p for p in fm.parts}
    assert fm.scope == "frameless kit" and fm.kit and not fm.partial and fm.source == "cad_bom"
    assert "FRAMELESS KIT" in fm.basis and "Housing" in fm.basis
    counted = {k for k, p in by.items() if p.counted}
    assert counted == {"stator_core", "rotor_core", "magnet", "slot", "wire_insulation", "board"}      # nothing of the housing
    assert by["shaft"].counted is False and "no shaft" in by["shaft"].basis
    bf = MM.read_bom_file(FIX / "server_bom" / "CIANO28_85_20SW1200_L13_BOM.csv")
    board_g = next(ln.grams for ln in bf.lines if ln.part.startswith("Phase board"))
    expect = 126.09 + 64.13 + 70.49 + 106.26 + 9.45 + board_g
    assert fm.total_kg * 1000 == pytest.approx(expect, rel=0.002)
    assert 390 < fm.total_kg * 1000 < 400
    full = 474.0
    assert fm.total_kg * 1000 < full - 75                                                      # clearly lighter than the full motor
    assert fm.rotating_kg == pytest.approx(0.06413 + 0.07049)                                  # rotor iron + magnets, nothing else
    assert fm.active_kg == pytest.approx(sum(ACTIVE.values()) / 1000.0)                      # the EM mass is untouched
    s = MM.mass_summary(DIE, CFG, dies_dir=dies, cad_dir=cad)
    assert s["scope"] == "frameless kit" and s["kit"] is True and s["full_kg"] == pytest.approx(fm.total_kg)


def test_a_kit_without_a_built_bom_is_partial_and_still_has_no_shaft(tmp_path, monkeypatch):
    dies, cad = _setup(tmp_path, monkeypatch, kit=True, with_bom=False)
    fm = MM.full_mass(DIE, CFG, dies_dir=dies, cad_dir=cad)
    assert fm.partial and fm.kit and fm.source == "active_only" and "partial mass (no CAD model yet)" in fm.basis
    assert fm.total_kg * 1000 == pytest.approx(126.09 + 64.13 + 70.49 + 106.26)
    assert not next(p for p in fm.parts if p.key == "shaft").counted


def test_the_materials_full_list_of_a_kit_has_no_housing_part(tmp_path, monkeypatch):
    dies, cad = _setup(tmp_path, monkeypatch, kit=True)
    out = MM.full_part_list(DIE, CFG, dies_dir=dies, cad_dir=cad)
    assert out["scope"] == "frameless kit" and out["kit"] is True
    keys = {p["key"] for p in out["parts"] if p["counted"]}
    assert not keys & {"carrier_front", "carrier_rear", "holder_front", "holder_back", "holder", "rings", "prop_fix", "bearings",
                       "screws", "shaft"}
    assert {"stator_core", "rotor_core", "magnet", "slot", "board"} <= keys
    dies2, cad2 = _setup(tmp_path / "again", monkeypatch, kit=True, with_bom=False)
    out2 = MM.full_part_list(DIE, CFG, dies_dir=dies2, cad_dir=cad2)
    assert "bearings" not in {p["key"] for p in out2["parts"]}                              # a kit names no bearing card either


def test_the_3d_model_list_says_which_variant_a_model_is():
    from motor_ai_sim import model3d
    entry = dict(die="d", config="L13", motor_id="m", dir=Path("."), manifest={"cad_variant": "frameless", "files": {}})
    assert model3d.model_info(entry)["assembly"] == "frameless"
    entry["manifest"].pop("cad_variant")
    assert model3d.model_info(entry)["assembly"] == "full"


def test_the_catalog_refuses_an_unknown_variant_and_keeps_a_good_one(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from motor_ai_sim.routes import family as fam
    monkeypatch.setattr(fam, "_write_target", lambda p: p)
    with pytest.raises(HTTPException) as e:
        fam._save_yaml(tmp_path / "L13.yaml", {"name": "L13", "cad_variant": "bare"})            # noqa: SLF001
    assert e.value.status_code == 422 and "full | frameless" in str(e.value.detail) and not (tmp_path / "L13.yaml").exists()
    fam._save_yaml(tmp_path / "L13.yaml", {"name": "L13", "cad_variant": "frameless"})            # noqa: SLF001
    assert yaml.safe_load((tmp_path / "L13.yaml").read_text())["cad_variant"] == "frameless"


def test_the_report_and_the_catalog_carry_the_kit_scope(tmp_path, monkeypatch):
    from motor_ai_sim.report import _inject_full_mass
    from motor_ai_sim.routes import family as fam
    dies, cad = _setup(tmp_path, monkeypatch, kit=True)
    monkeypatch.setenv("MOTRES_DIES_DIR", str(dies))
    monkeypatch.setenv("MOTRES_CAD_DIR", str(cad))
    monkeypatch.setattr(fam, "_DIES_DIR", dies)
    em = {"mass_total_kg": 0.3968}
    _inject_full_mass(em, DIE, CFG)
    assert em["mass_scope"] == "frameless kit" and em["mass_full_kg"] == pytest.approx(em["mass_total_kg"])
    assert 0.390 < em["mass_total_kg"] < 0.400 and em["mass_active_total_kg"] == 0.3968
    names = " ".join(r["name"] for r in em["mass_components"]).lower()
    assert "phase board" in names and not any(w in names for w in ("holder", "carrier", "bearing", "screw", "ring"))
    out = fam._overlay_mass(DIE, {"name": CFG, "duties": [{"name": "rated", "result": {"mass_kg": 0.3968}}]})
    assert out["mass"]["scope"] == "frameless kit" and out["duties"][0]["result"]["mass_kg"] == pytest.approx(em["mass_total_kg"], abs=1e-4)
