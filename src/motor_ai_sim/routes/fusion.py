"""Fusion 360 parameter round-trip — the geometry as USER PARAMETERS.

GET  /api/fusion/params.csv   → CSV in the format of Autodesk's own
                                 "Parameter I/O" add-in: Name,Unit,Expression,
                                 Value,Comment,Favorite — one row per PRIMARY
                                 geometry parameter (derived radii / counts /
                                 pitches are not exported; Fusion derives its
                                 own).  All six columns are mandatory: the
                                 add-in refuses a shorter file with "The file
                                 should contain at least six columns".
                                 Import it in Fusion (Parameter I/O → Import):
                                 existing user parameters are UPDATED, missing
                                 ones are CREATED, the model rebuilds.
GET  /api/fusion/params.json  → the same rows for a script running INSIDE
                                 Fusion (scripts/fusion360_sync_params.py).
POST /api/fusion/import       → a CSV exported by Parameter I/O, applied by
                                 NAME through the geometry write path (schema
                                 guard, validator, family locks, die snapshot
                                 sync — exactly as if typed into the form).
                                 `?dry_run=1` reports what WOULD change and
                                 writes nothing.

NAMES.  Fusion user parameters must be alphanumeric/underscore and not start
with a digit — every geometry key qualifies, so the default map is identity:
`magnet_height` here is `magnet_height` there.  A model built on the user's
own names is handled by `config/fusion_param_map.yaml` (our_key: FusionName);
the export writes the Fusion name, the import reads it back.  Names that map
to nothing are exported as-is and reported as `unknown` on import — the
user's housing dimensions live in the same parameter list and are none of
our business (same rule as the FreeCAD sheet).

Fusion has no external API (scripts run in-app only), which is why the
channel is a FILE and not a socket: this route works with Fusion closed, the
CSV is what Parameter I/O speaks natively, and the Fusion-side script is an
optional one-click on top.  Replaces `fusion360_controller.py` (2026-02),
whose eleven names no longer exist in the geometry.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from motor_ai_sim.auth import require_admin
from motor_ai_sim.config import get_config
from motor_ai_sim.workspace import shared_root as _ws_shared

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/fusion", tags=["fusion"])

# A SHARED read-only library (§2.1 of the migration plan), so it resolves
# through ``workspace.shared_root()`` rather than the per-user root.  With
# ``SHARED_ROOT`` unset that is the process config directory — the folder this
# constant has always named.
def _map_file() -> Path:
    _ov = globals().get("_MAP_FILE")
    if _ov is not None:
        return Path(str(_ov))
    return _ws_shared() / "fusion_param_map.yaml"


def __getattr__(name):
    if name == "_MAP_FILE":
        return _map_file()
    raise AttributeError(name)


_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _name_map() -> Dict[str, str]:
    """our geometry key -> Fusion user-parameter name (identity when unmapped)."""
    try:
        if _map_file().is_file():
            d = yaml.safe_load(_map_file().read_text(encoding="utf-8")) or {}
            m = d.get("map") if isinstance(d.get("map"), dict) else d
            return {str(k): str(v) for k, v in (m or {}).items()
                    if v and _NAME_RE.match(str(v))}
    except Exception:      # noqa: BLE001 — a broken map file must not break the export
        log.warning("fusion_param_map.yaml unreadable — using identity names", exc_info=True)
    return {}


#: Primary geometry keys kept OUT of the Fusion export on purpose.
#
#: WHY.  `slot_hs` (user 2026-09-14: "выкинь slot_hs, мы его не используем") is
#: a primary parameter by type, but the geometry schema itself calls it hidden
#: — "yields non-manufacturable fractional wire thicknesses, so hidden from the
#: UI for now (kept in the file; not yet wired into the CadQuery geometry)".
#: A user parameter the model does not drive and nobody may sensibly edit is
#: noise in Fusion's list, so it is neither exported nor read back: the import
#: treats such a row exactly like a derived one — ignored, and reported.
FUSION_EXCLUDED_NAMES: frozenset = frozenset({"slot_hs"})


def _rows():
    """(fusion_name, our_key, value, unit, comment) for every PRIMARY geometry
    parameter.  The derived ones (radii, counts, pitches, slot_width) are NOT
    exported (user 2026-09-13: "выкинь DERIVED переменные из экспорта"): Fusion
    rebuilds them from the primaries the same way the geometry does, and a
    user parameter nobody may edit only clutters the list.  Same for the
    explicit exclusions in FUSION_EXCLUDED_NAMES.  The import still accepts and
    ignores both kinds of row, so an older CSV round-trips unchanged."""
    from motor_ai_sim.routes.freecad import _params_rows
    from motor_ai_sim.routes._validation import DERIVED_GEOMETRY_NAMES
    geo = dict(get_config().get("geometry") or {})
    m = _name_map()
    out = []
    for k, v, unit, desc in _params_rows(geo):
        if k in DERIVED_GEOMETRY_NAMES or k in FUSION_EXCLUDED_NAMES:
            continue
        out.append((m.get(k, k), k, v, ("mm" if unit == "mm" else ""), desc))
    return out


@router.get("/params.csv")
def export_params_csv():
    """Parameter I/O CSV — the add-in's own six columns, in its own order:
    Name, Unit, Expression, Value, Comment, Favorite.  Fewer columns and the
    importer refuses the whole file ("The file should contain at least six
    columns"), which is what a four-column export used to hit.

    Expression is the bare number; Value is what Parameter I/O writes there —
    the EVALUATED expression, the same number again for a plain constant (and
    for a unitless row likewise).  Unit carries mm or nothing (counts, ratios).
    Comment = our key when it was renamed + the schema description, so a row
    can always be traced back.  Favorite is the add-in's "True"/"False"
    casing; nothing of ours is a favourite, so every row is False.

    QUOTING.  Several schema descriptions contain commas, so the Comment field
    is quoted — RFC 4180, csv.writer's QUOTE_MINIMAL, exactly what Parameter
    I/O's own export writes and its importer reads.  Such a row is still six
    FIELDS; only `line.split(",")` says otherwise, and that is the wrong way
    to count a CSV."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(["Name", "Unit", "Expression", "Value", "Comment", "Favorite"])
    for fname, key, v, unit, desc in _rows():
        comment = (("motor_ai_sim:" + key + " ") if fname != key else "") + desc
        num = "%g" % v
        w.writerow([fname, unit, num, num, comment.strip(), "False"])
    stem = _stem()
    return Response(
        content=buf.getvalue().encode("utf-8-sig"),      # BOM: Excel/Fusion read it as UTF-8
        media_type="text/csv",
        headers={"Content-Disposition":
                 'attachment; filename="%s_fusion_params_%s.csv"'
                 % (stem, datetime.now().strftime("%Y%m%d-%H%M"))})


@router.get("/params.json")
def export_params_json():
    return {"machine": _stem(), "map_file": str(_map_file()),
            "parameters": [{"name": f, "key": k, "value": v, "unit": u, "comment": d}
                           for f, k, v, u, d in _rows()]}


def _stem() -> str:
    try:
        from motor_ai_sim.routes.freecad import _motor_label
        return _motor_label()
    except Exception:      # noqa: BLE001
        return "motor"


_UNIT_RE = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*([A-Za-z]*)\s*$")


def _parse_expression(expr: str, unit: str) -> Tuple[Optional[float], str]:
    """A Parameter I/O expression -> value in OUR units (mm / count / ratio).

    Accepts "36", "36 mm", "3.6 cm", "0.05 m"; refuses anything else (a
    formula referencing other parameters, an angle, an inch value) and says
    why — a silently mis-scaled dimension is the one outcome worse than a
    refused row."""
    m = _UNIT_RE.match(str(expr or ""))
    if not m:
        return None, "not a plain number (formula or unsupported form)"
    val = float(m.group(1)); u = (m.group(2) or unit or "").strip().lower()
    if u in ("", "mm"):
        return val, ""
    if u == "cm":
        return val * 10.0, ""
    if u == "m":
        return val * 1000.0, ""
    return None, "unit %r not supported (mm / cm / m or unitless only)" % u


@router.post("/import")
async def import_params(file: UploadFile = File(...), dry_run: int = 0,
                        _admin: dict = Depends(require_admin)):
    """Apply a Parameter I/O CSV by name.  Buckets, like the FreeCAD import:
    applied / unchanged / unknown / derived_ignored / excluded_ignored /
    refused (unparseable).  `excluded_ignored` is the additive bucket for
    FUSION_EXCLUDED_NAMES (slot_hs) — a row we no longer export but that an
    older CSV still carries; every pre-existing field keeps its meaning.

    Columns are matched by HEADER NAME, not by position, so both shapes of the
    file work: the six-column one Parameter I/O writes (Name, Unit, Expression,
    Value, Comment, Favorite) and our older four-column one.  Only Name,
    Expression and (optionally) Unit are read — Value is the add-in's own
    evaluation of Expression and never overrides it, Favorite is a UI flag.

    Parsing is csv.DictReader — a csv.reader underneath — so a quoted field
    carrying commas or embedded quotes (which any real Comment does) stays ONE
    field and Expression keeps its place."""
    data = await file.read()
    if not data:
        raise HTTPException(422, detail="empty upload")
    text = data.decode("utf-8-sig", errors="replace")
    try:
        rdr = csv.DictReader(io.StringIO(text))
        rows = list(rdr)
    except Exception as e:      # noqa: BLE001
        raise HTTPException(422, detail="could not read the CSV: %s" % e)
    cols = {c.strip().lower(): c for c in (rdr.fieldnames or [])}
    if "name" not in cols or "expression" not in cols:
        raise HTTPException(422, detail="expected the Parameter I/O columns "
                            "Name, Unit, Expression, Value, Comment, Favorite "
                            "(Name and Expression are the ones actually read) "
                            "— got %s" % (rdr.fieldnames,))
    inv = {v: k for k, v in _name_map().items()}       # Fusion name -> our key
    geo = dict(get_config().get("geometry") or {})
    from motor_ai_sim.routes._validation import DERIVED_GEOMETRY_NAMES
    vals: Dict[str, float] = {}
    unknown, refused, derived, excluded = [], {}, [], []
    for r in rows:
        fname = str(r.get(cols["name"]) or "").strip()
        if not fname:
            continue
        key = inv.get(fname, fname)
        if key not in geo:
            unknown.append(fname); continue
        if key in DERIVED_GEOMETRY_NAMES:
            derived.append(key); continue
        if key in FUSION_EXCLUDED_NAMES:
            excluded.append(key); continue
        v, why = _parse_expression(r.get(cols["expression"]), r.get(cols.get("unit", ""), ""))
        if v is None:
            refused[fname] = why; continue
        vals[key] = v
    changed = {k: v for k, v in vals.items() if abs(float(geo[k]) - float(v)) > 1e-5}
    out: Dict[str, Any] = {
        "ok": True, "dry_run": bool(dry_run),
        "applied": {k: changed[k] for k in sorted(changed)},
        "before": {k: geo[k] for k in sorted(changed)},
        "unchanged": sorted(set(vals) - set(changed)),
        "derived_ignored": sorted(set(derived)),
        "excluded_ignored": sorted(set(excluded)),
        "unknown": sorted(unknown),
        "refused": refused,
    }
    if not changed or dry_run:
        out["note"] = ("nothing to apply — every recognised parameter already has this value"
                       if not changed else "dry run — nothing written")
        return out
    # THE geometry write path, never a private copy of it.
    from motor_ai_sim.routes.geometry import GeometryUpdateModel, update_geometry
    result = update_geometry(GeometryUpdateModel(**changed))
    log.info("Fusion import: %d parameter(s) applied (%s), %d unknown, %d refused",
             len(changed), ", ".join(sorted(changed)), len(unknown), len(refused))
    out["geometry_validation"] = (result or {}).get("geometry_validation")
    out["constraints_applied"] = (result or {}).get("constraints_applied")
    return out
