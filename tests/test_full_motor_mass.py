# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""THE MOTOR MASS IS THE FULL ASSEMBLED MASS (owner 2026-10-14, "the full mass must now be used everywhere").

The full mass = the app's active parts + the CAD bill of materials' shaft/carrier, holders, rings, bearings, screws, board,
pins and enamel, with the die's Full material assignment.  It is what the catalog rows, the datasheet and the report, the
component card, Configure and the API show; the ACTIVE mass stays a separate labelled number that the electromagnetic
model and the optimizer use (and that nothing here changes).  A motor with no built CAD BOM shows the active mass flagged
"partial mass (no CAD model yet)" - never silently mixed.  Closed-form, no solver, no FEM."""
from __future__ import annotations

import csv
import re
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.modules import motor_mass as MM

DIE, CFG = "CIANO14 40 new", "L12"
_ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


@pytest.fixture()
def catalog(tmp_path, monkeypatch):
    from motor_ai_sim.cad import bom
    from motor_ai_sim.modules import component_library as L
    from motor_ai_sim.routes import family as fam
    from tests._component_dies import make_dies
    from tests.test_cad_bom import make
    dies = make_dies(tmp_path)
    # give the configurations a stored duty, the way a saved catalog has it
    for v, kg in (("L12", 0.0917), ("L20", 0.1414)):
        f = dies / DIE / f"{v}.yaml"
        d = yaml.safe_load(f.read_text())
        d["duties"] = [{"name": "rated", "mode": "motor", "power_kw": 1.0, "torque_nm": 0.66, "rpm": 14400,
                        "result": {"mass_kg": kg, "efficiency_pct": 90.0}}]
        f.write_text(yaml.safe_dump(d), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", dies)
    monkeypatch.setenv("MOTRES_DIES_DIR", str(dies))
    cad = tmp_path / "cad"
    for v in ("L12",):                                         # L20 deliberately has NO CAD model
        (cad / DIE / v).mkdir(parents=True)
        b, _ = make(v)
        bom.write_csv(b, str(cad / DIE / v / f"CIANO14_40_new_{v}_BOM.csv"))
    monkeypatch.setenv("MOTRES_CAD_DIR", str(cad))
    L._LIB = None                                                         # noqa: SLF001
    MM._FULL_CACHE.clear()                                                # noqa: SLF001
    yield dies
    L._LIB = None                                                         # noqa: SLF001


def _bom_csv(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["item", "part", "total mass g", "material / grade", "notes"])
        for r in rows:
            w.writerow(r)


# -- the arithmetic -------------------------------------------------------------------------------------

def test_full_mass_is_active_plus_the_cad_parts_and_names_what_it_replaced(catalog):
    fm = MM.full_mass(DIE, CFG)
    mm = MM.app_motor_mass(DIE, CFG)
    assert not fm.partial and fm.source == "cad_bom" and fm.active_kg == pytest.approx(mm.total_kg)
    by = {p.key: p for p in fm.parts}
    # the active parts are the app's, untouched
    for k in ("stator_core", "rotor_core", "magnet", "slot"):
        assert by[k].mass_kg == pytest.approx({p.key: p.mass_kg for p in mm.parts}[k])
    # the shaft is the CAD's real stepped shaft in the EM shaft material, not the simulation tube
    assert by["shaft"].mass_kg != pytest.approx({p.key: p.mass_kg for p in mm.parts}["shaft"])
    assert "CAD bill of materials" in by["shaft"].basis
    assert by["wire_insulation"].mass_kg == pytest.approx(0.00122, abs=2e-5)            # the coil enamel of the BOM
    extras = {k: p for k, p in by.items() if not p.em}
    assert {"holder_front", "holder_back", "bearings", "screws", "board", "pins", "prop_fix"} <= set(extras)
    assert "rings" not in by                                     # the GFRP ring is gone from every construction (owner 2026-10-15)
    assert fm.total_kg == pytest.approx(sum(p.mass_kg for p in fm.parts if p.counted))
    assert fm.total_kg > fm.active_kg * 1.15
    # rotating mass = the rotating parts of the list (rotor, magnets, shaft, prop plate) and nothing else
    rot = sum(p.mass_kg for p in fm.parts if p.counted and p.key in MM.ROTATING_KEYS)
    assert fm.rotating_kg == pytest.approx(rot) and 0 < rot < fm.total_kg
    assert by["bearings"].key not in MM.ROTATING_KEYS and by["stator_core"].key not in MM.ROTATING_KEYS


def test_no_built_cad_model_keeps_the_active_mass_and_says_partial(catalog):
    fm = MM.full_mass(DIE, "L20")
    mm = MM.app_motor_mass(DIE, "L20")
    assert fm.partial and fm.source == "active_only" and fm.total_kg == pytest.approx(mm.total_kg)
    assert fm.total_kg == fm.active_kg and "partial mass (no CAD model yet)" in fm.basis
    assert all("partial mass (no CAD model yet)" in p.basis for p in fm.parts)
    assert MM.mass_summary(DIE, "L20")["partial"] is True and MM.mass_summary("NO SUCH DIE", "L20") is None


def test_a_rotor_carrier_replaces_the_simulation_shaft_and_follows_the_material(catalog, tmp_path):
    cad = tmp_path / "carrier_cad"
    _bom_csv(cad / DIE / CFG / "x_BOM.csv", [
        ("1", "Rotor carrier, front (hub)", "45.852", "Steel_42CrMo4_QT", "shaft density 7720 (simulation library card)"),
        ("2", "Rotor carrier, rear", "7.464", "Aluminum 6061", "shaft_rear density 2700"),
        ("3", "Stator holder, front (propeller side)", "6.0", "Aluminum 6061", "density 2700"),
    ])
    fm = MM.full_mass(DIE, CFG, cad_dir=cad)
    by = {p.key: p for p in fm.parts}
    assert by["shaft"].counted is False and "stand-in" in by["shaft"].basis        # the tube is not counted twice
    # owner rule: every wall and shaft is aluminium 6061; the CAD mass (computed at STEEL density) is rescaled
    assert by["carrier_front"].material_id == "Aluminium_6061"
    assert by["carrier_front"].mass_kg == pytest.approx(0.045852 * 2700 / 7720)
    assert "mass scaled" in by["carrier_front"].basis
    assert by["carrier_rear"].mass_kg == pytest.approx(0.007464)
    assert {"carrier_front", "carrier_rear"} <= set(MM.ROTATING_KEYS)
    assert fm.rotating_kg == pytest.approx(sum(p.mass_kg for p in fm.parts if p.counted and p.key in MM.ROTATING_KEYS))


# -- every surface ------------------------------------------------------------------------------------------

def test_the_tree_stamps_the_full_mass_and_keeps_the_active_one(catalog):
    from motor_ai_sim.routes.family import _overlay_mass
    fm = MM.full_mass(DIE, CFG)
    base = {"name": CFG, "duties": [{"name": "rated", "result": {"mass_kg": 0.0917}}]}
    cfg = _overlay_mass(DIE, base)
    r = cfg["duties"][0]["result"]
    assert r["mass_kg"] == pytest.approx(fm.total_kg, abs=1e-4) and r["mass_active_kg"] == 0.0917 and r["mass_partial"] is False
    assert cfg["mass"]["full_kg"] == pytest.approx(fm.total_kg) and cfg["mass"]["partial"] is False
    assert base["duties"][0]["result"]["mass_kg"] == 0.0917                      # the cached tree is never mutated
    # no CAD model: the stored (active) mass stays, flagged partial
    part = _overlay_mass(DIE, {"name": "L20", "duties": [{"name": "rated", "result": {"mass_kg": 0.1414}}]})
    assert part["duties"][0]["result"]["mass_kg"] == 0.1414 and part["duties"][0]["result"]["mass_partial"] is True
    # a die the catalog cannot weigh: the stored mass stays, flagged partial, nothing breaks
    gone = _overlay_mass("NO SUCH DIE", {"name": "L12", "duties": [{"name": "r", "result": {"mass_kg": 1.0}}]})
    assert gone["duties"][0]["result"]["mass_kg"] == 1.0 and gone["mass"]["partial"] is True


def test_the_tree_route_and_the_mcp_headline_carry_it(catalog):
    from fastapi import Response
    from motor_ai_sim import mcp_tools as T
    from motor_ai_sim.routes import family as fam
    res = fam.tree(Response(), authorization=None)
    die = next(d for d in res["dies"] if d["name"] == DIE)
    c12 = next(c for c in die["configs"] if c["name"] == "L12")
    c20 = next(c for c in die["configs"] if c["name"] == "L20")
    fm = MM.full_mass(DIE, "L12")
    assert c12["duties"][0]["result"]["mass_kg"] == pytest.approx(fm.total_kg, abs=1e-4)
    assert c20["mass"]["partial"] is True and c20["duties"][0]["result"]["mass_partial"] is True
    h = T._duty_headline(c12["duties"][0])
    assert h["mass_kg"] == pytest.approx(fm.total_kg, abs=1e-3) and h["mass_active_kg"] == pytest.approx(0.0917, abs=1e-3) and h["mass_partial"] is False
    h20 = T._duty_headline(c20["duties"][0])
    assert h20["mass_partial"] is True


def test_the_datasheet_quotes_the_full_mass_and_labels_the_active_one(catalog):
    from openpyxl import load_workbook
    import io
    from motor_ai_sim.datasheet import build_datasheet
    fm = MM.full_mass(DIE, CFG)
    d = yaml.safe_load((catalog / DIE / "die.yaml").read_text())
    c = yaml.safe_load((catalog / DIE / f"{CFG}.yaml").read_text())
    c["duties"][0]["summary"] = {"mass_total_kg": 0.0917,
                                 "mass_components": [{"name": "Stator core (20SW1200)", "mass_kg": 0.0346}]}
    info = {"full_kg": fm.total_kg, "active_kg": fm.active_kg, "partial": False, "rotating_kg": fm.rotating_kg,
            "parts": [(p.name, p.material_id, p.mass_kg) for p in fm.parts if p.counted]}
    wb = load_workbook(io.BytesIO(build_datasheet(die=DIE, cfg=CFG, die_doc=d, cfg_doc=c, mass_info=info)))

    def cells(ws):
        return [[x.value for x in row] for row in ws.iter_rows()]
    rows = [r for ws in wb.worksheets for r in cells(ws)]
    full = next(r for r in rows if r[0] == "Motor mass (kg)")
    act = next(r for r in rows if r[0] == "Active mass, EM parts (kg)")
    nums = lambda r: [x for x in r[1:] if isinstance(x, (int, float))]            # noqa: E731
    assert nums(full)[0] == pytest.approx(fm.total_kg, abs=1e-3) and nums(act)[0] == pytest.approx(0.0917, abs=1e-3)
    ptk = next(r for r in rows if r[0] == "Torque per mass (N·m/kg)")
    assert nums(ptk)[0] == pytest.approx(0.66 / fm.total_kg, abs=0.01)             # per-kg divides by the FULL mass
    assert any(r[0] == "Full motor mass breakdown (kg)" for r in rows)
    # partial: the active mass, labelled
    wb2 = load_workbook(io.BytesIO(build_datasheet(die=DIE, cfg=CFG, die_doc=d, cfg_doc=c,
                                                   mass_info={**info, "partial": True})))
    rows2 = [r for ws in wb2.worksheets for r in cells(ws)]
    assert any(isinstance(r[0], str) and r[0].startswith("Motor mass (kg) - partial") for r in rows2)
    assert next(r for r in rows2 if isinstance(r[0], str) and r[0].startswith("Motor mass (kg) - partial"))[1] is not None


def test_the_report_cover_and_table_use_the_full_mass(catalog):
    from motor_ai_sim import report as R
    fm = MM.full_mass(DIE, CFG)
    em = {"mass_total_kg": 0.0917, "mass_active_kg": 0.0902,
          "mass_components": [{"name": "Stator core (20SW1200)", "mass_kg": 0.0346, "material": "steel"}]}
    R._inject_full_mass(em, DIE, CFG)
    assert em["mass_partial"] is False and em["mass_total_kg"] == pytest.approx(fm.total_kg)
    assert em["mass_active_total_kg"] == 0.0917 and em["rotating_mass_kg"] == pytest.approx(fm.rotating_kg)
    assert {c["name"] for c in em["mass_components"]} >= {"Bearings (Bearing_steel_100Cr6)", "Phase board (FR4_glass_epoxy)"}
    rows = R.mass_rows(em, {})
    tot = next(r for r in rows if r[0].startswith("TOTAL"))
    assert float(re.sub(r"[^0-9.]", "", tot[3].split()[0])) == pytest.approx(fm.total_kg, abs=1e-3)
    assert any(r[0].startswith("Active mass (electromagnetic parts only)") for r in rows)
    assert R._rotating_mass_kg(em) == pytest.approx(fm.rotating_kg)
    # no CAD model: nothing replaced, flagged
    em2 = {"mass_total_kg": 0.1414, "mass_components": [{"name": "Stator core (20SW1200)", "mass_kg": 0.0577, "material": "x"}]}
    R._inject_full_mass(em2, DIE, "L20")
    assert em2["mass_partial"] is True and em2["mass_total_kg"] == 0.1414
    assert any(r[0].startswith("Partial mass") for r in R.mass_rows(em2, {}))


def test_the_configure_passport_gets_the_fixed_parts_only_with_a_cad_model(catalog):
    from motor_ai_sim.routes.catalog import _with_full_mass
    fm = MM.full_mass(DIE, CFG)
    outer = {"passport": {"mass0_kg": 0.0917, "L0_mm": 12.0}, "geo": {}}
    got = _with_full_mass(outer, {"die": DIE, "config": CFG, "date": "x"})
    p = got["passport"]
    assert p["mass_partial"] is False and p["mass_fixed_kg"] == pytest.approx(fm.total_kg - fm.active_kg, abs=1e-5)
    assert "mass_fixed_kg" not in outer["passport"]                                # the cached card is never mutated
    for card in ({"die": DIE, "config": "L20", "date": "x"}, None, {"die": "NO SUCH DIE", "config": "L12"}):
        q = _with_full_mass(outer, card)["passport"]
        assert q["mass_partial"] is True and "mass_fixed_kg" not in q


# -- the electromagnetic model and the optimizer do not move ------------------------------------------------------

def test_active_mass_and_the_solver_inputs_are_identical_with_and_without_the_full_mass(catalog, tmp_path):
    from motor_ai_sim.masses import compute_masses
    from motor_ai_sim.simulation.geometry_2d import _cfg_path, merge_geo_override, params_from_config
    d = catalog / DIE
    geo = dict(yaml.safe_load((d / "die.yaml").read_text())["geometry"])
    cy = yaml.safe_load((d / f"{CFG}.yaml").read_text())
    geo.update(cy["geometry_overrides"])
    base = yaml.safe_load(Path(_cfg_path()).read_text())
    mats = {k: v for k, v in base["materials"].items() if k in ("stator_core", "rotor_core", "magnet", "slot", "shaft")}
    mats.update(cy["materials"])

    def solver_mass():
        return compute_masses(params_from_config(geo_override=geo), merge_geo_override(dict(base["geometry"]), geo),
                              k_end=0.0, materials=mats)
    before = solver_mass()
    MM.full_mass(DIE, CFG)                                                         # compute the full mass, with a BOM
    MM._FULL_CACHE.clear()                                                         # noqa: SLF001
    (d / "die.yaml").write_text(yaml.safe_dump({**yaml.safe_load((d / "die.yaml").read_text()),
                                                "part_materials": {"holder_front": "copper", "bearings": "copper"}}),
                                encoding="utf-8")
    fm = MM.full_mass(DIE, CFG)
    after = solver_mass()
    assert {k: before[k] for k in before if isinstance(before[k], (int, float))} == \
           {k: after[k] for k in after if isinstance(after[k], (int, float))}      # bit for bit
    assert fm.active_kg == pytest.approx(before["total"], rel=1e-12)                # the active mass is the solver's own
    # and no solver / optimizer / sweep module even imports the full-mass code
    src = _ROOT / "src" / "motor_ai_sim"
    pat = re.compile(r"motor_mass|full_mass|mass_summary")
    for pkg in ("optimization", "simulation"):
        for f in (src / pkg).rglob("*.py"):
            assert not pat.search(f.read_text(encoding="utf-8")), f
    for f in ("masses.py", "fem_solver_2d.py", "passport.py", "design_eval.py"):
        for g in src.rglob(f):
            assert not pat.search(g.read_text(encoding="utf-8")), g


def test_rotor_inertia_keeps_its_meaning_documented():
    """masses.rotor_inertia_kg_m2 is the ACTIVE rotor (rotor iron + magnets + the simulation shaft tube + sleeve): the BOM has
    no polar moments, so the full list's rotating mass is a MASS, not an inertia - and the docstring says so."""
    text = (_ROOT / "src" / "motor_ai_sim" / "masses.py").read_text(encoding="utf-8")
    assert "FULL assembled" in text and "rotating_kg" in text
