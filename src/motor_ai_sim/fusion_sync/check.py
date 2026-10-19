"""What the server checks about a Fusion export (docs/FUSION_SYNC.md sections 5.3, 8 Q5).

* param_diff   - EM parameters where the Fusion user parameters and the die/config disagree (same names and units as the
                 Import route reads).
* body_checks  - the Fusion bodies measured against the die/config: core / rotor outer radius and length, magnet length,
                 magnet and coil counts.  Read from the bundle manifest (bounding boxes, occurrences); no FreeCAD needed.
* status       - the version guard: latest export, the version the 3-D model was built from, the rebuild state, and whether
                 AeroStator changed the die/config after the last export.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from motor_ai_sim.fusion_sync import store as FS

TOL_MM = 1e-3


def effective(die_dir: str, config: str) -> Dict[str, Any]:
    """The die/config's effective machine (die geometry + the config's overrides) with the file hashes."""
    from motor_ai_sim.cad import store as CS
    return CS.load_effective(os.path.dirname(str(die_dir)), os.path.basename(str(die_dir)), config)


def param_rows(geometry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The die/config's primary EM parameters under their Fusion names (the rows of GET /api/fusion/params.csv)."""
    from motor_ai_sim.cad import store as CS
    return [dict(name=fname, key=key, value=v, unit=unit, comment=desc)
            for key, fname, v, unit, desc in CS.primary_rows(geometry, CS.load_mapping())]


def param_diff(bundle_params: List[Dict[str, Any]], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by = {p.get("name"): p for p in bundle_params}
    out = []
    for r in rows:
        f = by.get(r["name"])
        if f is None or f.get("value") is None:
            continue
        a, b = float(f["value"]), float(r["value"])
        if abs(a - b) > 1e-7 * max(1.0, abs(a), abs(b)):
            out.append(dict(name=r["name"], key=r["key"], unit=r["unit"], fusion=a, aerostator=b))
    return out


def _part(m: Dict[str, Any], *names) -> Optional[Dict[str, Any]]:
    parts = m.get("parts") or {}
    for n in names:
        if n in parts:
            return parts[n]
    return None


def _bbox(part: Dict[str, Any]) -> Optional[List[float]]:
    bbs = [b.get("bbox_mm") for b in part.get("bodies") or [] if b.get("bbox_mm")]
    if not bbs:
        return None
    return [min(b[0] for b in bbs), min(b[1] for b in bbs), min(b[2] for b in bbs),
            max(b[3] for b in bbs), max(b[4] for b in bbs), max(b[5] for b in bbs)]


def _check(name: str, fusion: Optional[float], aerostator: Optional[float], unit: str = "mm", tol: float = TOL_MM) -> Dict[str, Any]:
    ok = None if fusion is None or aerostator is None else abs(float(fusion) - float(aerostator)) <= tol
    return dict(name=name, fusion=None if fusion is None else round(float(fusion), 4),
                aerostator=None if aerostator is None else round(float(aerostator), 4), unit=unit, ok=ok)


def body_checks(m: Dict[str, Any], geo: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The Fusion bodies against the parameters (owner 2026-10-19: «сервер проверяет тела по параметрам»).  A check whose
    part or parameter is missing has ok = None (not judged) rather than a guess."""
    out = []
    core, rotor, magnet, coil = _part(m, "Core"), _part(m, "Rotor", "RotorCore"), _part(m, "Magnet"), _part(m, "Coil")
    L = geo.get("motor_length")
    if core and _bbox(core):
        b = _bbox(core)
        out.append(_check("core outer radius", max(abs(b[0]), abs(b[1]), abs(b[3]), abs(b[4])),
                          (geo.get("stator_diameter") or 0) / 2.0 if geo.get("stator_diameter") else None))
        out.append(_check("core length", b[5] - b[2], L))
    if rotor and _bbox(rotor):
        b = _bbox(rotor)
        out.append(_check("rotor outer radius", max(abs(b[0]), abs(b[1]), abs(b[3]), abs(b[4])), geo.get("rotor_outer_radius")))
        out.append(_check("rotor length", b[5] - b[2], L))
    if magnet and _bbox(magnet):
        b = _bbox(magnet)
        lam = float(geo.get("magnet_lamination") or 0.0)
        out.append(_check("magnet length", b[5] - b[2], L if lam <= 0 else lam))
        poles = geo.get("num_poles") or ((geo.get("num_seg") or 0) * (geo.get("num_poles_per_segment") or 0)) or None
        n = len(magnet.get("occurrences") or [])
        out.append(_check("magnet count", n * max(1, len(magnet.get("bodies") or [])) if lam <= 0 else n, poles, unit="", tol=0))
    if coil:
        sectors = (geo.get("num_seg") or 0) * 3 or None
        out.append(_check("coil count", len(coil.get("occurrences") or []), sectors, unit="", tol=0))
    return out


def model_fusion(manifest: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return (manifest or {}).get("fusion") or None


def status(die: str, config: str, *, die_dir: str, cad_manifest: Optional[Dict[str, Any]], rebuild: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The version guard of docs/FUSION_SYNC.md section 5.3, plus one short `warnings` list for the 3-D and Production tabs."""
    lt = FS.latest(die)
    built = model_fusion(cad_manifest)
    out: Dict[str, Any] = dict(die=die, config=config, enabled=die in FS.allowed_dies(), link=FS.link_of_die(die),
                               latest_export=lt, model_built_from=built, rebuild=rebuild, param_diff=[], body_checks=[],
                               aerostator_changed_since_export=None, warnings=[])
    if not lt:
        return out
    v = int(lt["version"])
    rec = FS.record(die, v) or {}
    try:
        eff = effective(die_dir, config)
    except (FileNotFoundError, ValueError) as e:
        out["warnings"].append("AeroStator die/config not readable: %s" % e)
        return out
    rows = param_rows(eff["geometry"])
    out["param_diff"] = param_diff(FS.bundle_parameters(die, v), rows)
    out["body_checks"] = body_checks(FS.bundle_manifest(die, v) or {}, eff["geometry"])
    then = rec.get("aerostator_files") or {}
    if then:
        out["aerostator_changed_since_export"] = then != file_hashes(eff)
    doc = (lt.get("document") or "Fusion")
    if built is None:
        out["warnings"].append("3-D model not built from Fusion yet (latest export: %s v%d)" % (doc, v))
    elif int(built.get("version") or 0) != v:
        out["warnings"].append("3-D model is from %s v%s; latest export is v%d" % (doc, built.get("version"), v))
    if rebuild and rebuild.get("state") == "failed" and rebuild.get("fusion_version") == v:
        out["warnings"].append("rebuild from v%d failed: %s" % (v, (rebuild.get("message") or "")[:160]))
    if out["param_diff"]:
        out["warnings"].append("Fusion v%d and AeroStator differ on %d parameter(s): %s" % (
            v, len(out["param_diff"]), ", ".join(d["name"] for d in out["param_diff"][:4])))
    bad = [c for c in out["body_checks"] if c["ok"] is False]
    if bad:
        out["warnings"].append("Fusion bodies do not match the parameters: %s" % ", ".join(
            "%s %g vs %g" % (c["name"], c["fusion"], c["aerostator"]) for c in bad[:3]))
    if out["aerostator_changed_since_export"]:
        out["warnings"].append("AeroStator parameters changed after the v%d export: Import into Fusion pending" % v)
    return out


def file_hashes(eff: Dict[str, Any]) -> Dict[str, str]:
    """sha256 of the die.yaml and the config file (the libraries do not count: they are not the die's parameters)."""
    return {k: (x or {}).get("sha256") for k, x in (eff.get("files") or {}).items()
            if k.endswith(".yaml") and k not in ("materials_library.yaml", "bearings_library.yaml")}
