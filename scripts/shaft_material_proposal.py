# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""PROPOSAL ONLY - the shaft-material rule of 2026-10-14 applied to a die catalog, with what it would change.

Owner rule (2026-10-14): "only the SMALLEST motors have a stainless shaft", and "all the walls and the shafts are
aluminium 6061"; grade confirmed 2026-10-15: SS420 (AISI 420 martensitic, EN 1.4021 / 1.4028).  As a rule, not per-motor
guesses:

  * stator OD <= 40 mm (the Ø30 and Ø40 families) .... stainless steel AISI 420 - FERROMAGNETIC (hardened + tempered is the
                                                       library card ``Stainless_420``; ``Stainless_420_annealed`` is the
                                                       soft-condition twin - the owner's supplier answer decides which)
  * 85 mm <= stator OD < 200 mm (public dies) ......... aluminium 6061-T6
  * stator OD >= 200 mm ............................... NOT TOUCHED (private, high-speed: kept as is)
  * 40 mm < OD < 85 mm ................................ no rule yet: listed as "undecided"

The shaft is an ELECTROMAGNETIC part: its material is the configuration's ``materials.shaft`` (a part the configuration does
not name takes the shipped default of ``config/motor_config.yaml``), so the change is a PHYSICS change - it moves the shaft's
eddy loss and, for a ferromagnetic shaft, the rotor flux path - and the stored passports and duties must be re-solved.  This
script therefore NEVER writes a die or a configuration: it prints, per die and configuration, the current shaft material, the
proposed one, whether the change is magnetic -> non-magnetic or not, and the mass effect; ``--patch FILE`` writes a
``.patch.yaml`` in the format of ``cad/store.py`` proposals for the orchestrator to apply after the owner confirms.

    python scripts/shaft_material_proposal.py [--dies-dir DIR] [--cad-dir DIR] [--no-mass] [--out report.md] [--patch p.yaml]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

STAINLESS = "Stainless_420"          # owner 2026-10-15: SS420 (was provisional 316); hardened + tempered condition
ALUMINIUM = "Aluminium_6061"         # owner: "aluminium 6061 everywhere"


def classify(stator_od_mm: float) -> Dict[str, Any]:
    """The rule: ``{"class": ..., "proposed": material or None}``."""
    if stator_od_mm <= 40.0:
        return {"class": "smallest (OD <= 40 mm)", "proposed": STAINLESS}
    if stator_od_mm >= 200.0:
        return {"class": "untouched (OD >= 200 mm, private high-speed)", "proposed": None}
    if stator_od_mm >= 85.0:
        return {"class": "85 mm and larger public dies", "proposed": ALUMINIUM}
    return {"class": "undecided (40 mm < OD < 85 mm: no rule yet)", "proposed": None}


def _is_magnetic(material: str) -> Optional[bool]:
    """True for a library STEEL (it carries a B-H curve, the field sees it), False for a conductor/insulator card."""
    from motor_ai_sim import materials as mat
    for cat, magnetic in (("steel", True), ("conductor", False), ("insulator", False)):
        try:
            mat.get_material(cat, material)          # type: ignore[arg-type]
            return magnetic
        except Exception:                            # noqa: BLE001
            continue
    return None


def _density(material: str) -> Optional[float]:
    from motor_ai_sim import materials as mat
    for cat in ("steel", "conductor", "insulator"):
        try:
            return float(mat.get_material(cat, material).density)   # type: ignore[arg-type]
        except Exception:                            # noqa: BLE001
            continue
    return None


def meaning(cur: str, new: str) -> str:
    mc, mn = _is_magnetic(cur), _is_magnetic(new)
    if mc is None or mn is None:
        return "unknown material card"
    if mc and not mn:
        return ("MAGNETIC -> NON-MAGNETIC: the shaft leaves the rotor flux path (its B-H curve and solid-steel eddy treatment "
                "go); rotor field, torque, EMF and losses change - re-solve every passport and duty of the configuration")
    if not mc and mn:
        return ("NON-MAGNETIC -> MAGNETIC: the ferromagnetic shaft joins the rotor flux path (B-H curve + solid-steel eddy "
                "treatment appear); see docs/SHAFT_MATERIAL_PROPOSAL_2026-10-14.md for the measured effect; re-solve the "
                "passports and duties if the table says so")
    if mc and mn:
        return "magnetic -> magnetic: B-H curve and eddy loss of the shaft change; re-solve"
    sc, sn = _sigma(cur), _sigma(new)
    return (f"non-magnetic -> non-magnetic: the field is unchanged to first order; only the shaft's conductivity "
            f"({sc / 1e6:.1f} -> {sn / 1e6:.2f} MS/m: the shaft eddy loss moves with it, lower for the lower sigma) and its "
            "mass change; shaft eddy loss and the thermal rotor path should be re-solved") if sc and sn else \
        "non-magnetic -> non-magnetic: only conductivity and mass change"


def _sigma(material: str) -> Optional[float]:
    from motor_ai_sim import materials as mat
    for cat in ("conductor", "steel"):
        try:
            return float(mat.get_material(cat, material).sigma)       # type: ignore[arg-type]
        except Exception:                            # noqa: BLE001
            continue
    return None


def proposal(dies_dir: Path, *, cad_dir: Optional[Path] = None, with_mass: bool = True) -> List[Dict[str, Any]]:
    from motor_ai_sim.simulation.geometry_2d import _cfg_path
    default_shaft = (yaml.safe_load(Path(_cfg_path()).read_text(encoding="utf-8")).get("materials") or {}).get("shaft")
    rows: List[Dict[str, Any]] = []
    for ddir in sorted(p for p in dies_dir.iterdir() if (p / "die.yaml").is_file()):
        die = yaml.safe_load((ddir / "die.yaml").read_text(encoding="utf-8")) or {}
        od = float((die.get("geometry") or {}).get("stator_diameter") or 0.0)
        rule = classify(od)
        for cf in sorted(ddir.glob("*.yaml")):
            if cf.name == "die.yaml":
                continue
            cy = yaml.safe_load(cf.read_text(encoding="utf-8")) or {}
            named = (cy.get("materials") or {}).get("shaft")
            cur = str(named or default_shaft)
            row: Dict[str, Any] = {"die": ddir.name, "config": cf.stem, "stator_od_mm": od, "class": rule["class"],
                                   "current": cur, "current_source": "the configuration" if named else "the shipped default",
                                   "proposed": rule["proposed"], "file": str(cf)}
            if rule["proposed"] and rule["proposed"] != cur:
                row["meaning"] = meaning(cur, rule["proposed"])
                if with_mass:
                    try:
                        from motor_ai_sim.modules import motor_mass as MM
                        full = MM.full_mass(ddir.name, cf.stem, dies_dir=dies_dir, cad_dir=cad_dir)
                        sh = next((p for p in full.parts if p.key == "shaft" and p.counted), None) or \
                            next((p for p in MM.app_motor_mass(ddir.name, cf.stem, dies_dir=dies_dir).parts
                                  if p.key == "shaft"), None)
                        r0, r1 = _density(cur), _density(rule["proposed"])
                        if sh is not None and r0 and r1:
                            row["shaft_mass_g"] = (round(sh.mass_kg * 1e3, 2), round(sh.mass_kg * 1e3 * r1 / r0, 2))
                            row["shaft_mass_basis"] = ("CAD shaft" if not full.partial else "simulation shaft tube") + \
                                                      f", full mass {full.total_kg * 1e3:.1f} g" + \
                                                      (" (partial: no CAD model)" if full.partial else "")
                            row["motor_mass_g"] = (round(full.total_kg * 1e3, 2),
                                                   round((full.total_kg + sh.mass_kg * (r1 / r0 - 1)) * 1e3, 2))
                    except Exception as exc:         # noqa: BLE001
                        row["mass_error"] = str(exc)
            elif rule["proposed"] is None:
                row["meaning"] = "not changed by the rule"
            else:
                row["meaning"] = "already that material"
            rows.append(row)
    return rows


def to_markdown(rows: List[Dict[str, Any]]) -> str:
    out = ["# Shaft material proposal (PROPOSAL ONLY, nothing applied)", "",
           "| die | config | OD mm | rule | EM shaft today | proposed | shaft g (today -> proposed) | what it means |",
           "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        m = r.get("shaft_mass_g")
        out.append("| {die} | {config} | {od:g} | {cls} | {cur} ({src}) | {prop} | {mass} | {mean} |".format(
            die=r["die"], config=r["config"], od=r["stator_od_mm"], cls=r["class"], cur=r["current"], src=r["current_source"],
            prop=r["proposed"] or "-", mass=(f"{m[0]} -> {m[1]}" if m else "-"), mean=r.get("meaning", "")))
    return "\n".join(out) + "\n"


def to_patch(rows: List[Dict[str, Any]]) -> str:
    lines = ["# PROPOSAL ONLY - not applied. Generated by scripts/shaft_material_proposal.py from the owner's rule of 2026-10-14.",
             "# The orchestrator applies it to the configurations after the owner confirms the grade; nothing here writes a die.",
             "changes:"]
    for r in rows:
        if r.get("proposed") and r["proposed"] != r["current"]:
            lines += [f"  - file: {json.dumps(r['file'])}", "    section: materials",
                      f"    set: {{shaft: {r['proposed']}}}   # was {r['current']} ({r['current_source']}): {r.get('meaning', '')[:90]}"]
    return "\n".join(lines) + "\n"


def _tree_hash(root: Path) -> Dict[str, str]:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*.yaml"))}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dies-dir", default=os.environ.get("MOTRES_DIES_DIR") or str(ROOT / "config" / "dies"))
    ap.add_argument("--cad-dir", default=None)
    ap.add_argument("--no-mass", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--patch", default=None)
    a = ap.parse_args(argv)
    dies = Path(a.dies_dir)
    before = _tree_hash(dies)
    rows = proposal(dies, cad_dir=Path(a.cad_dir) if a.cad_dir else None, with_mass=not a.no_mass)
    md = to_markdown(rows)
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
    else:
        sys.stdout.buffer.write(md.encode("utf-8"))
    if a.patch:
        Path(a.patch).write_text(to_patch(rows), encoding="utf-8")
    assert _tree_hash(dies) == before, "the proposal must never write the die catalog"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
