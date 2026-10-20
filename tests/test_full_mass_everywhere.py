# SPDX-License-Identifier: Apache-2.0
"""THE MASS IS THE FULL MASS (owner 2026-10-19: «у нас почему-то везде указана активная масса ... лучше её использовать»).

1. A frameless-kit BOM whose simulation passport mass is 0 (T2 = 0, so its C row is the WHOLE BOM) is not "partial": 85 L15
   was flagged "only 27.9 g of the 443.5 g ... accounted for" and every surface fell back to the active mass.
2. The kit decision does not depend on which copy of the die a caller resolves: a configuration that does not NAME its
   cad_variant (an older copy read outside a workspace) follows the BOM's own "frameless kit" statement; one that says
   ``full`` is never overruled.
3. Configure's references: a passport whose stored ``mass0_kg`` is the full mass (40 L12 0.1229, L20 0.1807 kg) is served
   with the server's ACTIVE mass when its base build is the configuration's build; every preset carries the server's
   full / active mass.
Closed-form, no solver."""
from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest
import yaml

from motor_ai_sim.modules import motor_mass as MM

_ROOT = Path(__file__).resolve().parents[1]
DIE, CFG = "CIANO14 30_10", "L10"


def _kit_bom(path: Path, board_g: float = 5.0, enamel_g: float = 0.4) -> None:
    """A frameless-kit BOM as the CAD worker writes it for a configuration with no passport mass_components."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        ("1", "Stator lamination (stacked)", "20.0", "20SW1200", "density 7650"),
        ("2", "Rotor lamination, stacked", "9.0", "20SW1200", "density 7650"),
        ("3", "Permanent magnet (sintered NdFeB)", "8.0", "N52", "density 7500"),
        ("4", "Coil (edge-wound flat wire)", f"{6.0 + enamel_g}", "Copper",
         f"copper 6.0 g (rho 8933) + enamel {enamel_g} g (polyimide 1420 kg/m3)"),
        ("5", "Phase board 30 (coil connection)", f"{board_g}", "FR-4", "insulation + copper"),
        ("6", "Adhesive (magnet / coil / lamination bonding)", "", "to be specified", ""),
    ]
    total = sum(float(r[2]) for r in rows if r[2])
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["item", "part", "total mass g", "simulation mass g", "material / grade", "notes"])
        for it, part, g, mat, notes in rows:
            w.writerow([it, part, g, "", mat, notes])
        w.writerow(["T1", "TOTAL mass of the frameless kit (BOM lines, model)", f"{total}", "", "", ""])
        w.writerow(["T2", "Simulation passport mass (no mass_components)", "", "0.0", "", ""])
        w.writerow(["C", "parts the simulation mass does not contain", f"{total}", "", "", ""])
        w.writerow(["E", "FRAMELESS KIT (cad_variant = frameless): this bill of materials lists the kit only", "", "", "", ""])


@pytest.fixture()
def die(tmp_path, monkeypatch):
    dies = tmp_path / "dies"
    shutil.copytree(_ROOT / "config" / "dies" / DIE, dies / DIE)
    cad = tmp_path / "cad"
    _kit_bom(cad / DIE / CFG / "CIANO14_30_10_L10_BOM.csv")
    monkeypatch.setenv("MOTRES_DIES_DIR", str(dies))
    monkeypatch.setenv("MOTRES_CAD_DIR", str(cad))
    MM._FULL_CACHE.clear()                                                 # noqa: SLF001
    MM._CACHE.clear()                                                      # noqa: SLF001
    yield dies
    MM._FULL_CACHE.clear()                                                 # noqa: SLF001


def _set_variant(dies: Path, value) -> None:
    f = dies / DIE / f"{CFG}.yaml"
    doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    if value is None:
        doc.pop("cad_variant", None)
    else:
        doc["cad_variant"] = value
    f.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    MM._FULL_CACHE.clear()                                                 # noqa: SLF001


def test_kit_bom_with_no_passport_mass_is_complete_not_partial(die):
    _set_variant(die, None)                    # the configuration does not name a variant: the BOM's own statement decides
    bf = MM.read_bom_file(MM.find_bom_file(DIE, CFG))
    assert bf.kit_declared and bf.sim_g == 0.0
    fm = MM.full_mass(DIE, CFG)
    mm = MM.app_motor_mass(DIE, CFG)
    assert not fm.partial and fm.kit and fm.source == "cad_bom", fm.partial_reason
    shaft = {p.key: p for p in mm.parts}.get("shaft")
    want = mm.total_kg - (shaft.mass_kg if shaft else 0.0) + 0.005 + 0.0004        # kit: no shaft tube, + board + enamel
    assert fm.total_kg == pytest.approx(want, abs=1e-6)
    s = MM.mass_summary(DIE, CFG)
    assert s["full_kg"] == pytest.approx(fm.total_kg) and s["active_kg"] == pytest.approx(mm.total_kg) and not s["partial"]


def test_a_configuration_that_says_full_is_not_turned_into_a_kit(die):
    _set_variant(die, "full")
    fm = MM.full_mass(DIE, CFG)
    assert not fm.kit                           # a stale kit BOM never relabels a full motor
    assert not fm.partial, fm.partial_reason     # the C row of a T2 = 0 BOM is no reference: board + enamel are accounted for


def test_config_build_reads_the_configuration_geometry(die):
    assert MM.config_build(DIE, CFG) == {"L_mm": 10.0, "N": 6.0, "wireH_mm": 0.5}


def test_references_serve_the_server_active_mass_as_the_passport_base(monkeypatch):
    from motor_ai_sim.routes import catalog as cat
    summ = {"full_kg": 0.1810, "active_kg": 0.1506, "rotating_kg": 0.06, "partial": False, "scope": "full motor"}
    monkeypatch.setattr(MM, "mass_summary", lambda die, cfg, **k: dict(summ))
    monkeypatch.setattr(MM, "config_build", lambda die, cfg, **k: {"L_mm": 20.0, "N": 7.0, "wireH_mm": 0.6})
    monkeypatch.setattr(cat, "_passport_copper_mass", lambda card, inner: None)
    outer = {"passport": {"mass0_kg": 0.1807, "L0_mm": 20.0, "N0": 7, "wireH0_mm": 0.6}}
    card = {"die": "CIANO14 40 new", "config": "L20"}
    p = cat._with_full_mass(outer, card)["passport"]
    assert p["mass0_kg"] == pytest.approx(0.1506) and p["mass0_stored_kg"] == pytest.approx(0.1807)
    assert p["mass0_kg"] + p["mass_fixed_kg"] == pytest.approx(0.1810)       # at the base build: exactly the server's full mass
    # another base build than the configuration's: the stored number stays (the page anchors on the preset instead)
    outer2 = {"passport": {"mass0_kg": 0.1807, "L0_mm": 12.0, "N0": 7, "wireH0_mm": 0.6}}
    assert cat._with_full_mass(outer2, card)["passport"]["mass0_kg"] == pytest.approx(0.1807)


def test_every_preset_carries_the_server_masses(monkeypatch):
    from motor_ai_sim.routes import catalog as cat
    monkeypatch.setattr(MM, "mass_summary", lambda die, cfg, **k: {
        "full_kg": 0.12334, "active_kg": 0.09451, "rotating_kg": 0.03, "partial": False, "partial_reason": "",
        "scope": "full motor"})
    assert cat._preset_mass("CIANO14 40 new", "L12") == {"full_kg": 0.12334, "active_kg": 0.09451, "partial": False,
                                                          "partial_reason": "", "scope": "full motor"}
    monkeypatch.setattr(MM, "mass_summary", lambda die, cfg, **k: None)
    assert cat._preset_mass("CIANO14 40 new", "L12") is None
