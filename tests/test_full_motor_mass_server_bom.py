# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The full mass on the REAL server BOM files (tests/fixtures/server_bom: CIANO14 40 L12 / L20, CIANO28 85 L13).

Incident 2026-10-14: on the server the full mass added only ~9 g to the active mass although the BOM lists holders, carriers,
bearings, screws, the board and the enamel.  Cause: ``part_materials.yaml`` was not where the API image looks for it (the image keeps
``config/`` as ``config-seed/``), the loader answered an EMPTY document, and every mechanical part silently fell out of the list.
Fixed: the file is searched where it can be (shared root, repo, config-seed), a missing file is LOUD (and the mass is flagged
partial with the reason), a BOM row no rule recognises is COUNTED as "other", and a consistency guard compares the counted
non-active parts with the BOM's own "parts the simulation mass does not contain" row.  Closed-form, no solver."""
from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest

from motor_ai_sim import part_materials as pm
from motor_ai_sim.modules import motor_mass as MM

FIX = Path(__file__).resolve().parent / "fixtures" / "server_bom"
FILES = {"L12": "CIANO14_40_new_L12_BOM.csv", "L20": "CIANO14_40_new_L20_BOM.csv", "L13": "CIANO28_85_20SW1200_L13_BOM.csv"}
DIE_OF = {"L12": "CIANO14 40 new", "L20": "CIANO14 40 new", "L13": "CIANO28 85 20SW1200"}


def _rows(name):
    return list(csv.DictReader(open(FIX / FILES[name], encoding="utf-8-sig", newline="")))


def _g(row):
    return float(row["total mass g"])


# -- the parser on the real format ---------------------------------------------------------------------------------

@pytest.mark.parametrize("name,n_massed,n_unmassed,total,non_sim", [
    ("L12", 13, 4, 123.84, 21.31), ("L20", 13, 5, 180.74, 22.93), ("L13", 12, 5, 503.62, 86.59)])
def test_the_parser_reads_every_numbered_row_and_skips_the_summary_rows(name, n_massed, n_unmassed, total, non_sim):
    bf = MM.read_bom_file(FIX / FILES[name])
    assert len(bf.lines) == n_massed and len(bf.unmassed) == n_unmassed
    assert not any(ln.item in ("T1", "T2", "C", "E") for ln in bf.lines)              # T / C / E rows are never parts
    if total is not None:
        assert bf.total_g == pytest.approx(total) and bf.non_sim_g == pytest.approx(non_sim)
    # "total mass g" is unit mass x qty, as the file defines it (and the T1 row is the sum of the massed lines + the enamel is inside the coil line)
    assert all(ln.mass_consistent for ln in bf.lines)
    assert sum(ln.grams for ln in bf.lines) == pytest.approx(bf.total_g, abs=0.02)
    # rows without a mass are LISTED, never lost
    assert any("Adhesive" in u for u in bf.unmassed) and any("Slot insulation" in u for u in bf.unmassed)
    assert any("Power leads" in u for u in bf.unmassed)
    # the BOM-encoded header and a name that may be a multi-line cell
    assert MM.BomLine("1", "Rotor carrier, front (hub\nwith 4 holes)", "x", 1.0, "").name == "Rotor carrier, front (hub"


def test_a_multi_line_quoted_part_name_survives(tmp_path):
    f = tmp_path / "x_BOM.csv"
    with open(f, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["item", "part", "type", "qty", "unit", "material / grade", "key dimensions", "unit mass g", "total mass g",
                    "simulation mass g", "source / standard", "notes", "to be specified"])
        w.writerow(["6", "Rotor carrier, front (hub with 4 M3 end-face holes\nsecond line)", "manufactured", "1", "pcs", "Al",
                    "dims", "2.0", "2.0", "", "model", "density 2700 (x)", ""])
        w.writerow(["T1", "TOTAL mass of the BOM lines (model)", "total", "", "g", "", "", "", "2.0", "", "", "", ""])
    bf = MM.read_bom_file(f)
    assert len(bf.lines) == 1 and bf.lines[0].name.startswith("Rotor carrier, front")
    assert MM._classify(pm.mechanical_parts(), bf.lines[0].part) == "carrier_front"        # noqa: SLF001


# -- the full mass on the real files -------------------------------------------------------------------------------

def _setup(tmp_path, monkeypatch, name, active):
    """A stand-in die named like the real one, the REAL BOM file in a CAD folder, and the app's active parts given by the BOM's own
    simulation numbers (the real dies are private): ``active`` = {key: grams}."""
    from tests._component_dies import make_dies
    die = DIE_OF[name]
    dies = make_dies(tmp_path)
    if die != "CIANO14 40 new":
        shutil.copytree(dies / "CIANO14 40 new", dies / die)
        shutil.copy(dies / die / "L12.yaml", dies / die / f"{name}.yaml")
    cad = tmp_path / "cad" / die / name
    cad.mkdir(parents=True)
    shutil.copy(FIX / FILES[name], cad / FILES[name])
    names = {"stator_core": ("Stator core", "20SW1200"), "rotor_core": ("Rotor core", "20SW1200"),
             "magnet": ("Magnets", "F52SH_120C"), "slot": ("Winding copper", "copper"),
             "shaft": ("Shaft", "Aluminium_6061")}
    parts = [MM.MotorPart(names[k][0], names[k][1], g / 1000.0, "magnetic", True, "computed", "", key=k)
             for k, g in active.items()]
    fake = MM.MotorMass(die, name, sum(active.values()) / 1000.0, parts)
    monkeypatch.setattr(MM, "app_motor_mass", lambda *a, **k: fake)
    MM._FULL_CACHE.clear()                                                                # noqa: SLF001
    return dies, tmp_path / "cad", die


def test_l13_full_mass_is_the_bom_with_the_front_carrier_rescaled_to_aluminium_6061(tmp_path, monkeypatch):
    rows = {r["item"]: r for r in _rows("L13")}
    active = {"stator_core": 126.09, "rotor_core": 64.13, "magnet": 70.49, "slot": 106.26, "shaft": 29.76}
    dies, cad, die = _setup(tmp_path, monkeypatch, "L13", active)
    fm = MM.full_mass(die, "L13", dies_dir=dies, cad_dir=cad)
    assert not fm.partial and fm.source == "cad_bom" and fm.partial_reason == ""
    by = {p.key: p for p in fm.parts}
    # every non-active row of the BOM is in, none dropped
    for key, item in (("carrier_rear", "7"), ("holder_front", "8"), ("holder_back", "9"), ("bearings", "11"), ("screws", "12"),
                      ("board", "13")):
        assert by[key].mass_kg * 1000 == pytest.approx(_g(rows[item]), rel=0.01), key
    # the real file is a BOM built BEFORE the GFRP ring was removed (owner 2026-10-15): its Ring row is ignored - not counted, not "other"
    assert "rings" not in by and fm.others == [] and not any("ring (glass" in p.name.lower() for p in fm.parts)
    assert by["wire_insulation"].mass_kg == pytest.approx(0.00945, abs=1e-5)                    # the coil enamel of the notes
    # the front carrier: CAD at steel density 7850, owner rule aluminium 6061 -> x 2700 / 7850
    front = by["carrier_front"]
    assert front.material_id == "Aluminium_6061" and front.mass_kg * 1000 == pytest.approx(46.624 * 2700 / 7850, rel=1e-3)
    assert 15.9 < front.mass_kg * 1000 < 16.2
    # the simulation shaft tube is a stand-in: not counted twice
    assert by["shaft"].counted is False
    # expected total by hand: the app's four active parts + the BOM's non-active rows with the carrier rescaled
    expect = (126.09 + 64.13 + 70.49 + 106.26 + 9.45 + 46.624 * 2700 / 7850 + _g(rows["7"]) + _g(rows["8"]) + _g(rows["9"])
              + _g(rows["11"]) + _g(rows["12"]) + _g(rows["13"]))
    assert fm.total_kg * 1000 == pytest.approx(expect, rel=0.002)
    assert 464 < fm.total_kg * 1000 < 479                                                    # ~473 g without the ring (474 g with it)
    assert fm.total_kg * 1000 == pytest.approx(503.62 - _g(rows["10"]) - (46.624 - front.mass_kg * 1000) - (122.607 - 106.26 - 9.45) -
                                               (124.177 - 126.09) - (62.879 - 64.13) - (70.19 - 70.49), abs=2.5)


@pytest.mark.parametrize("name,active", [
    ("L12", {"stator_core": 34.63, "rotor_core": 15.24, "magnet": 16.59, "slot": 23.65, "shaft": 1.63}),
    ("L20", {"stator_core": 57.71, "rotor_core": 25.39, "magnet": 27.64, "slot": 27.95, "shaft": 2.71})])
def test_l12_and_l20_count_every_non_active_row(tmp_path, monkeypatch, name, active):
    rows = {r["item"]: r for r in _rows(name)}
    dies, cad, die = _setup(tmp_path, monkeypatch, name, active)
    fm = MM.full_mass(die, name, dies_dir=dies, cad_dir=cad)
    assert not fm.partial and fm.others == []                   # every row of the real file is recognised by a part rule
    non_active = sum(p.mass_kg for p in fm.parts if p.counted and (not p.em or p.key == "wire_insulation")) * 1000
    bf = MM.read_bom_file(FIX / FILES[name])
    ring_g = sum(_g(r) for r in rows.values() if r["part"].startswith("Ring"))               # a pre-removal BOM: its Ring row is ignored
    assert ring_g > 0 and non_active == pytest.approx(bf.non_sim_g - ring_g, abs=0.2)         # = the BOM's own "not in the simulation" minus the ring
    by = {p.key: p for p in fm.parts}
    assert by["shaft"].counted and by["shaft"].material_id == "Aluminium_6061"
    shaft_row = next(r for r in rows.values() if r["part"].startswith("Shaft"))
    assert by["shaft"].mass_kg * 1000 == pytest.approx(_g(shaft_row) * 2700 / 7850, rel=1e-3)    # CAD steel -> EM shaft material
    assert fm.total_kg * 1000 == pytest.approx(sum(active.values()) - active["shaft"] + by["shaft"].mass_kg * 1000 + non_active,
                                               rel=1e-6)
    assert fm.total_kg > 1.15 * fm.active_kg
    assert any("Adhesive" in u for u in fm.unmassed)


def test_a_row_no_rule_recognises_is_counted_as_other_and_listed(tmp_path, monkeypatch):
    active = {"stator_core": 34.63, "rotor_core": 15.24, "magnet": 16.59, "slot": 23.65, "shaft": 1.63}
    dies, cad, die = _setup(tmp_path, monkeypatch, "L12", active)
    f = cad / die / "L12" / FILES["L12"]
    text = f.read_text(encoding="utf-8-sig")
    extra = '19,"Heat-shrink sleeve (NEW PART)",purchased,2,pcs,PTFE,dims,1.5,3.0,,x,notes,\n'
    f.write_text(text.replace("T1,TOTAL", extra + "T1,TOTAL", 1), encoding="utf-8-sig")
    MM._FULL_CACHE.clear()                                                                # noqa: SLF001
    fm = MM.full_mass(die, "L12", dies_dir=dies, cad_dir=cad)
    other = [p for p in fm.parts if p.key.startswith("other_")]
    assert [p.name for p in other] == ["Heat-shrink sleeve (NEW PART)"] and other[0].counted
    assert other[0].mass_kg == pytest.approx(0.003) and other[0].role == "other" and other[0].material_id == "Unspecified_BOM_part"
    assert fm.others == ["Heat-shrink sleeve (NEW PART)"]
    # a second row of a known part is not dropped either
    spare = '20,"Ball bearing spare",purchased,1,pcs,x,d,2.0,2.0,,x,n,\n'
    f.write_text(f.read_text(encoding="utf-8-sig").replace("T1,TOTAL", spare + "T1,TOTAL", 1), encoding="utf-8-sig")
    MM._FULL_CACHE.clear()                                                                # noqa: SLF001
    fm2 = MM.full_mass(die, "L12", dies_dir=dies, cad_dir=cad)
    assert "Ball bearing spare" in fm2.others and fm2.total_kg == pytest.approx(fm.total_kg + 0.002)


def test_the_consistency_guard_flags_a_mass_it_cannot_account_for(tmp_path, monkeypatch):
    active = {"stator_core": 34.63, "rotor_core": 15.24, "magnet": 16.59, "slot": 23.65, "shaft": 1.63}
    dies, cad, die = _setup(tmp_path, monkeypatch, "L12", active)
    f = cad / die / "L12" / FILES["L12"]
    # the BOM's own "parts the simulation mass does not contain" row claims 100 g, the rows add up to ~21 g: not accountable
    out = []
    for r in csv.reader(open(f, encoding="utf-8-sig", newline="")):
        if r and r[0] == "C" and r[1].startswith("parts the simulation mass does not contain"):
            r[8] = "100.0"
        out.append(r)
    with open(f, "w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(out)
    MM._FULL_CACHE.clear()                                                                # noqa: SLF001
    fm = MM.full_mass(die, "L12", dies_dir=dies, cad_dir=cad)
    assert fm.partial and fm.source == "active_only" and "could be accounted for" in fm.partial_reason
    assert fm.total_kg == fm.active_kg and all("partial mass" in p.basis for p in fm.parts)
    assert MM.mass_summary(die, "L12", dies_dir=dies, cad_dir=cad)["partial_reason"] == fm.partial_reason


def test_missing_part_rules_are_loud_and_the_mass_is_partial_with_the_reason(tmp_path, monkeypatch):
    active = {"stator_core": 34.63, "rotor_core": 15.24, "magnet": 16.59, "slot": 23.65, "shaft": 1.63}
    dies, cad, die = _setup(tmp_path, monkeypatch, "L12", active)
    monkeypatch.setenv("MOTRES_PART_MATERIALS", str(tmp_path / "nowhere" / "part_materials.yaml"))
    pm._CACHE.clear()                                                                     # noqa: SLF001
    with pytest.raises(pm.PartMaterialsUnavailable, match="not found"):
        pm.mechanical_parts()                      # never an empty answer
    fm = MM.full_mass(die, "L12", dies_dir=dies, cad_dir=cad)
    assert fm.partial and "part-material rules are unavailable" in fm.partial_reason
    assert fm.total_kg == fm.active_kg
    monkeypatch.delenv("MOTRES_PART_MATERIALS")
    pm._CACHE.clear()                                                                     # noqa: SLF001
    MM._FULL_CACHE.clear()                                                                # noqa: SLF001
    assert not MM.full_mass(die, "L12", dies_dir=dies, cad_dir=cad).partial


def test_part_rules_are_found_where_the_api_image_keeps_them(monkeypatch):
    """The image keeps config/ as <app>/config-seed (never <app>/config); the shared catalog root wins like the materials library."""
    monkeypatch.delenv("MOTRES_PART_MATERIALS", raising=False)
    cands = [str(c).replace("\\", "/") for c in pm.part_file_candidates()]
    assert any(c.endswith("/config-seed/part_materials.yaml") for c in cands)
    assert any(c.endswith("/config/part_materials.yaml") for c in cands)
    assert len(cands) >= 3 and all(c.endswith("part_materials.yaml") for c in cands)
    assert pm.mechanical_parts() and pm.exists("20SW1200")


def test_the_unspecified_card_resolves_but_cannot_be_picked():
    card = pm.resolve("Unspecified_BOM_part")
    assert card.props == {} and set(card.missing) == set(pm.PROPERTIES)
    assert "Unspecified_BOM_part" not in {c["id"] for c in pm.choices()}


def test_the_api_image_layout_finds_the_rules_in_config_seed(tmp_path, monkeypatch):
    """The image has no <app>/config, only <app>/config-seed: with the repo path missing the seed copy is used (the 2026-10-14
    server answered an empty document here and weighed no mechanical part at all)."""
    seed = tmp_path / "app" / "config-seed"
    seed.mkdir(parents=True)
    shutil.copy(Path(__file__).resolve().parents[1] / "config" / "part_materials.yaml", seed / "part_materials.yaml")
    monkeypatch.delenv("MOTRES_PART_MATERIALS", raising=False)
    monkeypatch.setattr(pm, "_PART_FILE", tmp_path / "app" / "config" / "part_materials.yaml")      # absent, as in the image
    monkeypatch.setattr(pm, "_SEED_FILE", seed / "part_materials.yaml")
    monkeypatch.setattr(pm, "part_file_candidates", pm.part_file_candidates)
    # the shared catalog has no copy either
    from motor_ai_sim import workspace as W
    monkeypatch.setattr(W, "shared_root", lambda: tmp_path / "no_shared")
    pm._CACHE.clear()                                                                     # noqa: SLF001
    assert pm._part_file() == seed / "part_materials.yaml"                                # noqa: SLF001
    assert "holder_front" in pm.mechanical_parts() and "bearings" in pm.mechanical_parts()
    pm._CACHE.clear()                                                                     # noqa: SLF001
