"""Tests of the BOM (motor_ai_sim.cad.bom / bom_store): built from saved model dumps (cad/fc_scripts/bom_dump.py output of the
server-built CIANO14 40 L12 / L20 models), no FreeCAD needed."""
import copy
import csv
import json
import os

import pytest
import yaml

from motor_ai_sim.cad import bom, bom_store

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
MATS = {"Core": "20SW1200", "Coil": "Copper", "RotorCore": "20SW1200", "Magnet": "F52SH_120C", "Shaft": "Steel",
        "Stator_holder": "Aluminum 6061", "Stator_holder_back": "Aluminum 6061", "Ring": "GFRP", "Prop_fix": "Aluminum 6061",
        "Bearing_outer_ring": "Steel (bearing)", "Bearing_inner_ring": "Steel (bearing)", "Bearing_ball": "Steel (bearing)",
        "Screw": "18-8 Stainless Steel"}
CARDS = {"20SW1200": dict(density=7650, stacking_factor=0.95, description="JFE Steel 20SW1200, non-oriented electrical steel, 0.20 mm"),
         "F52SH_120C": dict(density=7500), "copper": dict(density=8933), "polyimide": dict(density=1420),
         "Aluminium_6061": dict(density=2700), "Steel_42CrMo4_QT": dict(density=7850)}
COMPS = [dict(name="Stator core (20SW1200)", mass_kg=0.035), dict(name="Copper windings (Cu)", mass_kg=0.024),
         dict(name="Magnets (F52SH_120C)", mass_kg=0.016), dict(name="Rotor back-iron (20SW1200)", mass_kg=0.015),
         dict(name="Shaft (Steel_42CrMo4_QT)", mass_kg=0.005)]
COMPS_L20 = [dict(name="Stator core (20SW1200)", mass_kg=0.058), dict(name="Copper windings (Cu)", mass_kg=0.033),
             dict(name="Magnets (F52SH_120C)", mass_kg=0.027), dict(name="Rotor back-iron (20SW1200)", mass_kg=0.025),
             dict(name="Shaft (Steel_42CrMo4_QT)", mass_kg=0.008)]
WIRES = [dict(code="010119", spec="SFT-AIW 0.6*2.5", thickness_mm=0.6, width_mm=2.5, insulation="AIW", stock_kg=5.6)]


def make(v, dump=None, **over):
    dump = dump or json.load(open(os.path.join(FIX, "cad_bom_dump_%s.json" % v), encoding="utf-8"))
    inputs = dict(die="CIANO14 40 new", config=v, motor_id="CIANO14_40_new_" + v, materials=MATS, cards=CARDS, wire_stock=WIRES,
                  winding=dict(connection="2S", n_coils_per_phase=4), sim_materials=dict(magnet="F52SH_120C", stator_core="20SW1200", rotor_core="20SW1200"),
                  sim_components=COMPS if v == "L12" else COMPS_L20, sim_total_kg=0.095 if v == "L12" else 0.151, sim_source="test")
    inputs.update(over)
    return bom.build_bom(dump, inputs), dump


@pytest.mark.parametrize("v,sheets,screw", [("L12", 57, 17.0), ("L20", 95, 25.0)])
def test_counts_masses_and_stock_check(v, sheets, screw):
    b, dump = make(v)
    k = {l["key"]: l for l in b["lines"]}
    assert k["stator_lam"]["qty"] == k["rotor_lam"]["qty"] == sheets            # round(L x k_f / 0.20 mm)
    assert (k["magnets"]["qty"], k["coils"]["qty"], k["bearings"]["qty"], k["rings"]["qty"], k["screws"]["qty"]) == (14, 6, 2, 2, 6)
    assert k["pins"]["qty"] == 3 and k["board"]["qty"] == 1 and not b["unclassified"]
    mag = next(e for e in dump["entries"] if e["label"] == "Magnet")
    assert k["magnets"]["total_mass_g"] == pytest.approx(14 * mag["volume"] * 7500e-6, abs=1e-3)
    coil = next(e for e in dump["entries"] if e["label"] == "Coil")
    assert k["coils"]["_length_mm"] == pytest.approx(coil["volume"] / (2.5 * 0.6))
    assert "AIW" in k["wire"]["source"] or "010119" in k["wire"]["source"]
    for r in b["comparison"]["rows"]:
        if r["component"] != "Shaft":
            assert abs(r["diff_pct"]) < 8.0
    assert any("not a stock length" in w for w in b["warnings"]) == (v == "L12")  # 17 mm is not an M2 stock length, 25 is
    assert bom.nearest_m2_stock(17.0) == 16 and bom.nearest_m2_stock(25.0) == 25


def test_unknown_model_object_is_listed_not_dropped():
    _, dump = make("L12")
    d2 = copy.deepcopy(dump)
    d2["entries"].append(dict(name="X", label="Mystery", type="Part::Feature", parent=None, linked=None, base=None, state=[], n_solids=1,
                              volume=3.0, area=1.0, bbox=[0, 0, 0, 1, 1, 1], com=[0, 0, 0], solid_volumes=[3.0]))
    b, _ = make("L12", d2)
    assert b["unclassified"] == ["Mystery"] and any("UNCLASSIFIED" in l["part"] for l in b["lines"])


def test_files_carry_no_commercial_data(tmp_path):
    b, _ = make("L12")
    bom.write_csv(b, str(tmp_path / "a.csv"))
    txt = (tmp_path / "a.csv").read_text(encoding="utf-8-sig")
    assert "CNY" not in txt and "price" not in txt.lower()
    rows = list(csv.DictReader(open(tmp_path / "a.csv", encoding="utf-8-sig")))
    assert rows[0]["part"].startswith("Stator lamination") and len(rows) > len(b["lines"])
    pytest.importorskip("openpyxl")
    bom.write_xlsx(b, str(tmp_path / "a.xlsx"), banner="TEST")
    import openpyxl
    assert openpyxl.load_workbook(tmp_path / "a.xlsx").sheetnames == ["L12"]


def test_sim_components_from_config_takes_the_last_saved_summary(tmp_path):
    p = tmp_path / "L12.yaml"
    p.write_text(yaml.safe_dump({"duties": [
        {"summary": {"mass_total_kg": 0.09, "mass_components": [{"name": "Stator core (x)", "mass_kg": 0.03}]}},
        {"summary": {"mass_total_kg": 0.095, "mass_components": COMPS}}]}))
    comps, total, src = bom.sim_components_from_config(str(p))
    assert total == 0.095 and comps[0]["mass_kg"] == 0.035 and "duties[1]" in src


def test_store_assemble_reads_cards_and_wire_stock(tmp_path):
    lib = tmp_path / "materials_library.yaml"
    lib.write_text(yaml.safe_dump({"conductor": {"copper": dict(density=8933, bh_curve=[[0, 0]]), "Aluminium_6061": dict(density=2700)}}))
    ws = tmp_path / "wire_stock.yaml"
    ws.write_text(yaml.safe_dump({"wires": WIRES}))
    dd = tmp_path / "dies" / "D"
    dd.mkdir(parents=True)
    (dd / "L12.yaml").write_text(yaml.safe_dump({"duties": [{"summary": {"mass_total_kg": 0.095, "mass_components": COMPS}}]}))
    eff = dict(die="D", config="L12.yaml", variant="L12", dies_dir=str(tmp_path / "dies"), material_cards={"20SW1200": CARDS["20SW1200"]},
               bearings={}, materials={"magnet": "F52SH_120C"}, winding={}, files={})
    inp = bom_store.assemble(eff, {"profile": {"motor_id": "D_L12", "materials": MATS}}, materials_library=str(lib), wire_stock=str(ws))
    assert inp["cards"]["copper"]["density"] == 8933 and "bh_curve" not in inp["cards"]["copper"] and inp["cards"]["Aluminium_6061"]
    assert inp["wire_stock"][0]["code"] == "010119" and inp["sim_total_kg"] == 0.095
