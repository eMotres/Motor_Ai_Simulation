"""The CAD profile block of a die whose 3-D model is built from its latest Fusion export (template A.8, docs/FUSION_SYNC.md 4.1).

``fusion_parts_for(die, geometry)`` -> the ``profile.fusion_parts`` dict the FreeCAD template reads, or None when the die is not
synced or has no export yet.  Purchased parts (bearings, screws, the phase board) are not taken from Fusion (owner 2026-10-19).
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from motor_ai_sim.fusion_sync import store as FS

#: Fusion component -> template body.  A link may override it with its own "component_map".
COMPONENT_MAP = {"Core": "Core", "Rotor": "RotorCore", "Magnet": "Magnet", "Coil": "Coil", "Shaft": "Shaft",
                 "Stator_holder": "Stator_holder", "Stator_holder_back": "Stator_holder_back", "Ring": "Ring", "Prop_fix": "Prop_fix"}
#: The template bodies that must come from the export (no fall-back to template geometry: a missing one refuses the build).
REQUIRED = ("Core", "RotorCore", "Magnet", "Coil")
#: Parts made of several Fusion bodies, fused into one solid.
MULTI = {"Coil": "fuse"}


class MissingFusionPart(RuntimeError):
    pass


def fusion_parts_for(die: str, geometry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if die not in FS.allowed_dies():
        return None
    if not FS.root().is_dir():                                  # a synced die's geometry is Fusion's: never guess "no export yet"
        raise MissingFusionPart("die %r is synced with Fusion but the bundle store %s is not there (mount it / set "
                                "MOTRES_FUSION_SYNC_ROOT, docs/FUSION_SYNC.md section 9)" % (die, FS.root()))
    lt = FS.latest(die)
    if not lt:
        return None
    v = int(lt["version"])
    m = FS.bundle_manifest(die, v)
    if not m:
        return None
    link = FS.link_of_die(die) or {}
    cmap = dict(link.get("component_map") or COMPONENT_MAP)
    parts = m.get("parts") or {}
    files, vols = {}, {}
    for comp, body in cmap.items():
        p = parts.get(comp)
        if not p:
            continue
        files[body] = os.path.basename(p["file"])
        vols[body] = round(sum(float(b.get("volume_mm3") or 0.0) for b in p.get("bodies") or []), 6)
    missing = [b for b in REQUIRED if b not in files]
    if missing:
        raise MissingFusionPart("Fusion export %s v%d of die %r has no component for %s (component map %s); the 3-D model is built only "
                                "from Fusion bodies - export a version that has them" % (m.get("document"), v, die, ", ".join(missing), cmap))
    rec = FS.record(die, v) or {}
    return {
        "source": "bundle",
        "dies": [die],
        "dir": str(FS.bundle_dir(die, v) / "parts"),
        "ref_motor_length": float(geometry.get("motor_length") or 0.0),       # the export is at the real length: no shift / stretch
        "files": files,
        "volumes_mm3": vols,
        "multi": {k: x for k, x in MULTI.items() if k in files},
        "canonical": True,
        "bundle": {"document": m.get("document"), "urn": m.get("urn"), "version": v, "exported_at": m.get("exported_at"),
                   "exported_by": m.get("exported_by"), "zip_sha256": rec.get("zip_sha256")},
    }


def stamp_text(bundle: Dict[str, Any]) -> str:
    """The one line every derived file carries: "Fusion 'CIANO14 50' v3"."""
    return "Fusion '%s' v%s" % (bundle.get("document") or "?", bundle.get("version"))
