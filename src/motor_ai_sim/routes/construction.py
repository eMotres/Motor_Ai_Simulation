# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Geometry tab "Full": the part tree and the CAD-only construction parameters of a die/configuration (owner 2026-10-15).

    GET  /api/family/die/{die}/part_tree?config=            THE part tree (groups, parts with ids / names EN+zh / materials / masses)
    GET  /api/family/config/{die}/{cfg}/cad_params          the editable construction table + rebuild status
    PUT  /api/family/config/{die}/{cfg}/cad_params          save CAD-only edits (validated with the template pre-check)
    POST /api/family/config/{die}/{cfg}/cad_params/import   the same edits from a Fusion Parameter I/O CSV or a FreeCAD .FCStd
    POST /api/family/config/{die}/{cfg}/cad_rebuild         request a server rebuild of the CAD model (also made by every save)

Permission = the rule of every other die/configuration edit (``family.require_catalog_write`` + ``_require_die_write``: an admin on
a workstation, any registered account in its own workspace copy under the multi-user layering).  A save is audited
(``cad.params``).  The electromagnetic simulation never reads ``cad_overrides``: a Full edit cannot change the geometry, its
``_geoSig`` or any run input (tests/test_construction_params.py pins it).
"""
from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from motor_ai_sim import admin_audit as _AA
from motor_ai_sim import cad_params as CP
from motor_ai_sim import part_materials as _pm
from motor_ai_sim import part_tree as PT
from motor_ai_sim.cad import rebuild_queue as RQ
from motor_ai_sim.cad import store as CS
from motor_ai_sim.routes import family as fam

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/family", tags=["construction"])


def _resolve_cfg(die: str, config: Optional[str], who: dict) -> str:
    ddir = fam._die_dir(die)
    if config:
        cfg = fam._check_name(config, "configuration")
        # a PRIVATE configuration reads exactly like a missing one (config-visibility rule)
        if not fam.config_allowed(who, die, cfg) or not (ddir / f"{cfg}.yaml").is_file():
            raise HTTPException(404, detail=f"configuration not found: {cfg}.yaml")
        return cfg
    names = sorted(p.stem for p in ddir.glob("*.yaml") if p.name != "die.yaml" and fam.may_see_config_file(who, die, p.stem, p))
    if not names:
        raise HTTPException(404, detail=f"die '{die}' has no configuration")
    return names[0]


# ---------------------------------------------------------------------------
# the part tree
# ---------------------------------------------------------------------------

@router.get("/die/{die}/part_tree")
def get_part_tree(die: str, config: Optional[str] = None, authorization: str = Header(default=None)):
    """THE part tree of a motor.  Same ids, names, groups and order as ``part_materials``, the 3-D model, the component card and
    the BOM export (they all call :func:`motor_ai_sim.part_tree.part_tree`)."""
    from motor_ai_sim.modules import motor_mass as _mm
    die = fam._check_name(die, "die")
    who = fam._require_die_access(die, authorization)
    cfg = _resolve_cfg(die, config, who)
    # the part tree IS the full construction (owner 2026-10-15): the same gate as the full model / STEP; the 404 for a hidden configuration came first
    from motor_ai_sim import model3d as _m3
    from motor_ai_sim.auth import caller_identity as _ci
    _who = _ci(authorization)
    _rec = {}
    if not _who.get("is_admin") and _who.get("id"):
        from motor_ai_sim import users as _U
        _rec = _U.get_user(_who["id"]) or {}
    if not _m3.can_download(_who, _rec):
        raise HTTPException(status_code=403, detail=dict(code="nda_required", message="The part tree of the full construction needs the signed NDA and a verified account."))
    out: Dict[str, Any] = {"die": die, "config": cfg, "can_write": fam._can_write_catalog(who)}
    try:
        out.update(PT.part_tree(die, cfg, dies_dir=fam._die_dir(die).parent))
    except _mm.MotorMassError as exc:
        out.update({"available": False, "reason": str(exc), "groups": [], "parts": []})
        return out
    out["available"] = True
    return out


# ---------------------------------------------------------------------------
# CAD-only parameters
# ---------------------------------------------------------------------------

def _layer_of(die: str) -> str:
    if not fam._ws_layering():
        return "shared"
    return str(fam._die_layer(die))


def _bearing_choices() -> List[Dict[str, Any]]:
    from motor_ai_sim import bearings as _b
    out = []
    for name in _b.list_bearings():
        try:
            c = _b.get_bearing(name)
        except Exception:                                                  # noqa: BLE001 - a broken card is not offered
            continue
        if c.d and c.D and c.B:
            out.append({"id": name, "d": c.d, "D": c.D, "B": c.B, "description": c.description or ""})
    return out


def _bearing_dims(card: str) -> Dict[str, float]:
    from motor_ai_sim import bearings as _b
    try:
        c = _b.get_bearing(card)
    except KeyError:
        raise HTTPException(422, detail=f"unknown bearing card '{card}'")
    if not (c.d and c.D and c.B):
        raise HTTPException(422, detail=f"bearing card '{card}' has no d / D / B")
    return {"d": float(c.d), "D": float(c.D), "B": float(c.B)}


def _status(die: str, cfg: str, overrides: Dict[str, Any]) -> Dict[str, Any]:
    root = CP.cad_root()
    st = RQ.status(root, die, cfg, CP.overrides_hash(overrides), manifest=CP.read_manifest(die, cfg))
    st["layer"] = _layer_of(die)
    return st


def _table(die: str, cfg: str, who: dict) -> Dict[str, Any]:
    cfile = fam._cfg_file(die, cfg)
    c = fam._load_yaml(cfile, "configuration")
    ov = CP.clean_overrides(c.get(CP.OVERRIDES_KEY))
    from motor_ai_sim.modules import motor_mass as _mm
    kit = _mm.config_cad_variant(fam._die_dir(die), cfg) == "frameless"
    out: Dict[str, Any] = {"die": die, "config": cfg, "can_write": fam._can_write_catalog(who), "overrides": ov,
                           "assembly": "frameless" if kit else "full", "kit": kit,
                           "cad_only_label": CP.CAD_ONLY_LABEL, "read_only_note": CP.READ_ONLY_NOTE}
    built = CP.read_built_params(die, cfg)
    if built is None:
        out.update(available=False, reason="no CAD model has been built for this motor yet; there is nothing to edit",
                   rows=[], groups=[], rebuild=_status(die, cfg, ov))
        return out
    rows = CP.rows(built, ov, kit)
    present = {r["group"] for r in rows}
    out["groups"] = [{"id": g, "name": {"en": en, "zh": zh}} for g, en, zh in PT.GROUPS if g in present]
    man = CP.read_manifest(die, cfg) or {}
    cards = _bearing_choices()
    eff_card = ov.get(CP.BEARING_CARD_KEY)
    cfg_card = None
    br = c.get("bearings") or {}
    if isinstance(br, dict):
        cfg_card = next((str(v.get("card")) for _k, v in sorted(br.items()) if isinstance(v, dict) and v.get("card")), None)
    bear_vals = {k: built[n].get("value") for k, n in (("d", "bearing_d"), ("D", "bearing_D"), ("B", "bearing_B")) if n in built}
    out.update(available=True, rows=rows, rebuild=_status(die, cfg, ov),
               bearing=None if kit else {"override": eff_card, "config_card": cfg_card, "model": bear_vals, "choices": cards,
                        "group": "bearings", "cad_only": True},
               model={"motor_id": man.get("motor_id"), "built_at": man.get("built_at"),
                      "template": (man.get("template") or {}).get("version")})
    return out


@router.get("/config/{die}/{cfg}/cad_params")
def get_cad_params(die: str, cfg: str, authorization: str = Header(default=None)):
    die = fam._check_name(die, "die")
    cfg = fam._check_name(cfg, "configuration")
    who = fam._require_die_access(die, authorization, cfg)
    if not (fam._die_dir(die) / f"{cfg}.yaml").is_file():          # same text as the config-path gate gives a hidden one
        raise HTTPException(404, detail=f"configuration '{die}/{cfg}' not found")
    return _table(die, cfg, who)


class CadParamsPut(BaseModel):
    values: Dict[str, Any] = Field(default_factory=dict, description="{name: number}; null / '' = back to the default")
    bearing_card: Optional[str] = Field(default=None, description="CAD bearing card (bearings library); null resets")
    dry_run: bool = False


def _change(die: str, cfg: str, who: dict, values: Dict[str, Any], *, bearing_set: bool, bearing_card: Optional[str],
            dry_run: bool, source: str) -> Dict[str, Any]:
    cfile = fam._cfg_file(die, cfg)
    c = fam._load_yaml(cfile, "configuration")
    cur = CP.clean_overrides(c.get(CP.OVERRIDES_KEY))
    built = CP.read_built_params(die, cfg)
    if built is None:
        raise HTTPException(409, detail="no CAD model has been built for this motor yet; there is nothing to edit")
    from motor_ai_sim.modules import motor_mass as _mm
    kit = _mm.config_cad_variant(fam._die_dir(die), cfg) == "frameless"
    defaults = {r["name"]: r["default"] for r in CP.rows(built, {}, kit)}
    new = dict(cur)
    nums: Dict[str, float] = {}
    for k, v in values.items():
        if v is None or (isinstance(v, str) and not v.strip()):
            new.pop(str(k), None)
        else:
            nums[str(k)] = v
    coerced, bad = CP.coerce(nums)
    if bad:
        raise HTTPException(422, detail={"error": "invalid values", "violations": bad})
    for k, x in coerced.items():
        if k in defaults and abs(x - defaults[k]) <= 1e-9:
            new.pop(k, None)                                  # equal to the default: no override
        else:
            new[k] = x
    dims = None
    if bearing_set and kit:
        raise HTTPException(422, detail={"error": "the frameless kit has no bearings", "violations": ["bearing_card: the frameless kit has no bearings"]})
    if bearing_set:
        if bearing_card:
            dims = _bearing_dims(bearing_card)
            new[CP.BEARING_CARD_KEY] = bearing_card
        else:
            new.pop(CP.BEARING_CARD_KEY, None)
    elif new.get(CP.BEARING_CARD_KEY):
        dims = _bearing_dims(new[CP.BEARING_CARD_KEY])
    try:
        problems = CP.validate(built, new, bearing=dims, kit=kit)
    except CP.CadParamError as exc:
        raise HTTPException(503, detail={"error": str(exc), "violations": exc.violations})
    if problems:
        raise HTTPException(422, detail={"error": "the CAD model would not build with these values", "violations": problems})
    changed = sorted(k for k in set(cur) | set(new) if cur.get(k) != new.get(k))
    res: Dict[str, Any] = {"ok": True, "dry_run": dry_run, "die": die, "config": cfg, "changed": changed,
                           "before": {k: cur.get(k) for k in changed}, "after": {k: new.get(k) for k in changed}}
    if dry_run:
        return res
    if changed:
        if new:
            c[CP.OVERRIDES_KEY] = new
        else:
            c.pop(CP.OVERRIDES_KEY, None)
        fam._save_yaml(cfile, c)
        actor = _AA.actor_of(who.get("user"))
        _AA.record(actor, "cad.params", f"{die}/{cfg}", details={"changed": changed, "source": source, "hash": CP.overrides_hash(new)})
        RQ.request(CP.cad_root(), die, cfg, requested_by=actor, overrides_hash=CP.overrides_hash(new), layer=_layer_of(die))
        log.info("construction: %s/%s CAD-only edit by %s: %s", die, cfg, actor, changed)
    res["table"] = _table(die, cfg, who)
    return res


@router.put("/config/{die}/{cfg}/cad_params")
def put_cad_params(die: str, cfg: str, req: CadParamsPut, _w: dict = Depends(fam.require_catalog_write)):
    """Save CAD-only construction edits.  Refused with the template's own sentences when the model would not build."""
    die = fam._check_name(die, "die")
    cfg = fam._check_name(cfg, "configuration")
    fam._require_die_write(die, _w, cfg)
    return _change(die, cfg, _w, req.values, bearing_set="bearing_card" in req.model_fields_set,
                   bearing_card=req.bearing_card, dry_run=req.dry_run, source="form")


_NUM = re.compile(r"^\s*([-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?)\s*([A-Za-z]*)\s*$")


def _plain(expr: str, unit: str) -> Optional[float]:
    m = _NUM.match(str(expr or ""))
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    u = (m.group(2) or unit or "").strip().lower()
    return v * {"": 1.0, "mm": 1.0, "deg": 1.0, "cm": 10.0, "m": 1000.0}.get(u, float("nan"))


def values_from_csv(data: bytes) -> Dict[str, float]:
    """CAD parameters of a Fusion Parameter I/O CSV (Name, Unit, Expression, Value ...): plain numbers only."""
    text = data.decode("utf-8-sig", errors="replace")
    out: Dict[str, float] = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3 or row[0].strip().lower() == "name":
            continue
        name = row[0].strip()
        if name not in CP.CATALOGUE:
            continue
        v = _plain(row[2], row[1])
        if v is None or v != v:
            v = _plain(row[3] if len(row) > 3 else "", row[1])
        if v is not None and v == v:
            out[name] = v
    return out


def values_from_fcstd(data: bytes) -> Dict[str, float]:
    """CAD parameters of the ``Params`` spreadsheet of a FreeCAD file (evaluated; formulas of other cells are followed)."""
    try:
        cells = CS.read_fcstd_params(io.BytesIO(data))
        vals = CS.evaluate(cells)
    except (zipfile.BadZipFile, KeyError, ValueError, SyntaxError, NameError, ZeroDivisionError) as exc:
        raise HTTPException(422, detail=f"not a FreeCAD file with a Params sheet: {exc}")
    return {n: float(vals[n]) for n in CP.CATALOGUE if n in vals}


@router.post("/config/{die}/{cfg}/cad_params/import")
async def import_cad_params(die: str, cfg: str, dry_run: int = Query(1), file: UploadFile = File(...),
                            _w: dict = Depends(fam.require_catalog_write)):
    """Apply the CAD-only parameters found in an uploaded Fusion CSV or FreeCAD file (electromagnetic rows are ignored: they are
    edited in Active).  ``dry_run=1`` (default) validates and reports; ``dry_run=0`` saves."""
    die = fam._check_name(die, "die")
    cfg = fam._check_name(cfg, "configuration")
    fam._require_die_write(die, _w, cfg)
    saved = CP.clean_overrides(fam._load_yaml(fam._cfg_file(die, cfg), "configuration").get(CP.OVERRIDES_KEY))   # 404 first, like a missing one
    data = await file.read()
    if len(data) > 20_000_000:
        raise HTTPException(413, detail="file too large")
    name = (file.filename or "").lower()
    found = values_from_fcstd(data) if name.endswith((".fcstd", ".zip")) else values_from_csv(data)
    built = CP.read_built_params(die, cfg)
    if built is None:
        raise HTTPException(409, detail="no CAD model has been built for this motor yet; there is nothing to edit")
    from motor_ai_sim.modules import motor_mass as _mm
    kit = _mm.config_cad_variant(fam._die_dir(die), cfg) == "frameless"
    cur = {r["name"]: r["value"] for r in CP.rows(built, saved, kit) if r["editable"]}       # bearing internals are read-only
    todo = {k: v for k, v in found.items() if k in cur and abs(v - cur[k]) > 1e-9}
    res = _change(die, cfg, _w, todo, bearing_set=False, bearing_card=None, dry_run=bool(dry_run), source="import:" + name[-6:])
    res.update(found=len(found), unchanged=sorted(k for k in found if k not in todo))
    return res


@router.post("/config/{die}/{cfg}/cad_rebuild")
def request_cad_rebuild(die: str, cfg: str, _w: dict = Depends(fam.require_catalog_write)):
    """Ask the CAD runtime for a new model of this configuration (the old one stays in use until the new one passed every check)."""
    die = fam._check_name(die, "die")
    cfg = fam._check_name(cfg, "configuration")
    fam._require_die_write(die, _w, cfg)
    cfile = fam._cfg_file(die, cfg)
    ov = CP.clean_overrides(fam._load_yaml(cfile, "configuration").get(CP.OVERRIDES_KEY))
    if CP.read_manifest(die, cfg) is None:
        raise HTTPException(409, detail="no CAD model has been built for this motor yet")
    actor = _AA.actor_of(_w.get("user"))
    RQ.request(CP.cad_root(), die, cfg, requested_by=actor, overrides_hash=CP.overrides_hash(ov), layer=_layer_of(die))
    _AA.record(actor, "cad.rebuild", f"{die}/{cfg}", details={"hash": CP.overrides_hash(ov)})
    return {"ok": True, "rebuild": _status(die, cfg, ov)}
