"""Per-die / per-configuration Fusion name map (2026-11-15): rename, scale, const, ignore.

The CIANO10 12_40 Fusion model calls the tangential strip size `wire_height` and the strip-column count `num_wires_per_slot`;
the global map cannot say that without breaking every other die.  A `dies:` section of `config/fusion_param_map.yaml` can.
No `dies` section / another die = the global behaviour, bit for bit.
"""
from __future__ import annotations

import asyncio
import csv
import io

import yaml
from fastapi import UploadFile

from motor_ai_sim.routes import fusion as F

HEADER = ["Name", "Unit", "Expression", "Value", "Comment", "Favorite"]
DIE, CFG = "CIANO10 12_40 test", "L40"

# the nine rows of the 12_40 Fusion model that differ from our names / values (the dump of Fusion v24)
FUSION = {"wire_height": "0.2 mm", "wire_width": "0.8 mm", "num_wires_per_slot": "3", "wire_spacing_x": "0.05 mm"}

DIE_MAP = {
    "wire_width": "wire_height",                                  # ours <- Fusion's wire_height (tangential 0.2)
    "wire_height": "wire_width",                                  # ours <- Fusion's wire_width (radial 0.8)
    "wire_split": "num_wires_per_slot",                           # strip COLUMNS
    "num_wires_per_slot": {"const": 2},                           # radial rows: no Fusion variable
    "wire_spacing_x": {"name": "wire_spacing_x", "scale": 0.6},   # N dx_F / (2N - 1) for N = 3
    "wire_omit": {"const": [[1, 0]]},
    "k_end_by_turns": {"const": 1},
    "wire_bore_margin": {"const": 0.04},
    "slot_hs": {"ignore": True},
}


def _install(monkeypatch, tmp_path, doc, target=(DIE, CFG)):
    f = tmp_path / "fusion_param_map.yaml"
    f.write_text(yaml.safe_dump(doc), encoding="utf-8")
    monkeypatch.setattr(F, "_MAP_FILE", f, raising=False)
    monkeypatch.setattr(F, "_coil_target", lambda: ({"die": target[0], "config": target[1]} if target else None))


def _csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(HEADER)
    for name, expr in rows.items():
        w.writerow([name, "mm" if "mm" in expr else "", expr, expr, "", "False"])
    return buf.getvalue()


def _dry(text):
    up = UploadFile(filename="p.csv", file=io.BytesIO(text.encode("utf-8-sig")))
    return asyncio.run(F.import_params(file=up, dry_run=1, _admin={"role": "admin"}))


def test_no_dies_section_is_the_global_map(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, {"map": {"magnet_height": "MagnetH", "wire_width": "wire_width"}})
    s = F._specs()
    assert s["magnet_height"] == {"name": "MagnetH", "scale": 1.0}
    assert {r[1]: r[0] for r in F._rows()}["magnet_height"] == "MagnetH"
    assert F._name_map()["magnet_height"] == "MagnetH"


def test_another_die_does_not_see_the_block(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, {"map": {"wire_width": "wire_width", "wire_height": "wire_height"},
                                     "dies": {DIE: {"map": DIE_MAP}}}, target=("CIANO14 40 new", "L12"))
    s = F._specs()
    assert s["wire_width"]["name"] == "wire_width" and "wire_omit" not in s
    names = {r[1]: r[0] for r in F._rows()}
    assert names["wire_width"] == "wire_width" and names["wire_height"] == "wire_height"
    assert "wire_omit" not in names                                  # optional keys are not in a die's geometry


def test_die_block_overrides_and_export_omits_const_keys(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, {"map": {"wire_width": "wire_width", "wire_height": "wire_height",
                                             "num_wires_per_slot": "num_wires_per_slot", "wire_split": "wire_split",
                                             "wire_spacing_x": "wire_spacing_x"}, "dies": {DIE: {"map": DIE_MAP}}})
    s = F._specs()
    assert s["wire_width"]["name"] == "wire_height" and s["wire_height"]["name"] == "wire_width"
    assert s["wire_split"]["name"] == "num_wires_per_slot"
    assert s["num_wires_per_slot"] == {"const": 2.0, "has_const": True}
    assert s["wire_omit"]["const"] == 256.0 and s["k_end_by_turns"]["const"] == 1.0
    assert s["wire_spacing_x"] == {"name": "wire_spacing_x", "scale": 0.6}
    rows = {r[1]: r for r in F._rows()}
    assert rows["wire_width"][0] == "wire_height" and rows["wire_height"][0] == "wire_width"
    assert rows["wire_split"][0] == "num_wires_per_slot"
    assert "num_wires_per_slot" not in rows and "slot_hs" not in rows           # no Fusion variable: not exported
    names = [r[0] for r in rows.values()]
    assert len(names) == len(set(names))                                        # no collision of Fusion names
    live = F.get_config()["geometry"]
    assert rows["wire_spacing_x"][2] == round(float(live["wire_spacing_x"]) / 0.6, 9)    # export divides by the scale


def test_import_of_the_fusion_names_lands_on_our_keys(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, {"dies": {DIE: {"map": DIE_MAP}}})
    out = _dry(_csv(FUSION))
    ap = out["applied"]
    assert ap["wire_width"] == 0.2 and ap["wire_height"] == 0.8 and ap["wire_split"] == 3
    assert abs(ap["wire_spacing_x"] - 0.03) < 1e-12
    assert ap["num_wires_per_slot"] == 2 and ap["wire_omit"] == 256 and ap["k_end_by_turns"] == 1
    assert ap["wire_bore_margin"] == 0.04
    assert out["map"]["constants"]["wire_omit"] == 256.0 and out["map"]["scaled"] == {"wire_spacing_x": 0.6}
    assert "num_wires_per_slot" not in out["unknown"]


def test_the_same_csv_on_another_die_is_the_identity_import(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, {"dies": {DIE: {"map": DIE_MAP}}}, target=("CIANO14 40 new", "L12"))
    out = _dry(_csv(FUSION))
    ap = out["applied"]
    assert ap.get("wire_height") == 0.2 and ap.get("wire_width") == 0.8 and ap.get("num_wires_per_slot") == 3
    assert "wire_omit" not in ap and "map" not in out


def test_configuration_layer_wins_over_the_die(monkeypatch, tmp_path):
    doc = {"dies": {DIE: {"map": DIE_MAP, "configs": {"L60": {"map": {"k_end_by_turns": {"const": 0},
                                                                         "wire_omit": {"ignore": True}}}}}}}
    _install(monkeypatch, tmp_path, doc, target=(DIE, "L60"))
    s = F._specs()
    assert s["k_end_by_turns"]["const"] == 0.0 and s["wire_omit"] == {"ignore": True}
    assert s["wire_split"]["name"] == "num_wires_per_slot"
    _install(monkeypatch, tmp_path, doc, target=(DIE, "L40"))
    assert F._specs()["k_end_by_turns"]["const"] == 1.0


def test_unusable_entries_are_skipped_not_fatal(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, {"map": {"wire_width": "wire_width"},
                                     "dies": {DIE: {"map": {"wire_width": {"name": "bad name"}, "wire_omit": {"const": "x"},
                                                           "wire_height": {"scale": 0}, "foo": 7}}}})
    s = F._specs()
    assert "wire_width" not in s and "wire_omit" not in s and "wire_height" not in s     # dropped, global name not left behind
    assert F._rows()                                                                      # export still works


def test_round_trip_of_the_mapped_die_changes_nothing(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, {"map": {"wire_width": "wire_width", "wire_height": "wire_height"},
                                     "dies": {DIE: {"map": {"wire_width": "wire_height", "wire_height": "wire_width",
                                                            "wire_spacing_x": {"name": "wire_spacing_x", "scale": 0.6}}}}})
    text = bytes(F.export_params_csv().body).decode("utf-8-sig")
    out = _dry(text)
    assert out["applied"] == {}, out["applied"]
