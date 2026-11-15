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
                                 writes nothing — but it DOES run the same
                                 schema/value validation the real write would
                                 (`check_geometry_submission`), so a CSV that
                                 describes a machine that cannot be built
                                 (e.g. a stator too small for its own slot +
                                 core + gap + magnet stack) is refused —
                                 `ok: false` and `invalid_parameters` — before
                                 the confirm dialog ever tells the user
                                 anything is safe to apply.

NAMES.  Fusion user parameters must be alphanumeric/underscore and not start
with a digit — every geometry key qualifies, so the default map is identity:
`magnet_height` here is `magnet_height` there.  A model built on the user's
own names is handled by `config/fusion_param_map.yaml` (our_key: FusionName);
the export writes the Fusion name, the import reads it back.  Names that map
to nothing are exported as-is and reported as `unknown` on import — the
user's housing dimensions live in the same parameter list and are none of
our business (same rule as the FreeCAD sheet).

COIL (2026-11-02).  ``wire_beng_r`` and ``coil_straight_extra`` (or
``coil_straight_len``, minus the stack) are read into the LOADED
CONFIGURATION's ``coil_overrides`` (``family.set_config_coil``), not into the
live geometry: machines on one die can have different Fusion coils.  They
are imported only, never exported (Fusion keeps them as its own formulas).

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
from motor_ai_sim.routes._validation import reject
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


# ── per-die / per-configuration map (2026-11-15) ─────────────────────────────────────────────────────────────────────
# The global ``map:`` above is one file for every machine.  A model whose Fusion variables mean something else (the CIANO10
# 12_40: Fusion's ``wire_height`` is OUR ``wire_width``, Fusion's ``num_wires_per_slot`` counts the strip COLUMNS = our
# ``wire_split``) needs its own entries, WITHOUT touching any other die.  Same file, optional sections:
#
#   dies:
#     "<die name>":
#       map:                       # overrides the global map for this die
#         wire_width: wire_height                          # rename (a plain string, as in the global map)
#         wire_spacing_x: {name: wire_spacing_x, scale: 0.6}   # ours = Fusion x scale  (export: Fusion = ours / scale)
#         num_wires_per_slot: {const: 2}                   # no Fusion variable: this value on every import, not exported
#         wire_omit: {const: [[1, 0]]}                     # a list of [row, col] becomes the wire_omit integer
#         slot_hs: {ignore: true}                          # a Fusion variable we do not take / do not export
#       configs:
#         "<configuration>":
#           map: {...}                                     # overrides the die's map for this configuration
#
# No ``dies`` section / a die that is not named = exactly the global behaviour (bit for bit).
_SPEC_KEYS = {"name", "scale", "const", "ignore"}
#: optional geometry keys a die may not carry yet (the server owns their schema): importable although absent from the live geometry
_OPTIONAL_KEYS = frozenset({"wire_bore_margin", "wire_omit", "k_end_by_turns"})


def _map_doc() -> Dict[str, Any]:
    try:
        if _map_file().is_file():
            d = yaml.safe_load(_map_file().read_text(encoding="utf-8")) or {}
            return d if isinstance(d, dict) else {}
    except Exception:      # noqa: BLE001
        log.warning("fusion_param_map.yaml unreadable — using identity names", exc_info=True)
    return {}


def _spec_of(key: str, raw: Any) -> Optional[Dict[str, Any]]:
    """One map entry -> ``{name, scale, const(+has_const), ignore}`` or None when unusable (skipped, never a 500)."""
    if isinstance(raw, str):
        return {"name": raw, "scale": 1.0} if _NAME_RE.match(raw) else None
    if not isinstance(raw, dict) or not (set(raw) <= _SPEC_KEYS):
        return None
    if raw.get("ignore"):
        return {"ignore": True}
    if "const" in raw:
        c = raw["const"]
        if key == "wire_omit" and isinstance(c, (list, tuple)):
            from motor_ai_sim.winding import wire_omit_mask
            try:
                c = wire_omit_mask([(int(a), int(b)) for a, b in c])
            except (TypeError, ValueError):
                return None
        if isinstance(c, bool) or not isinstance(c, (int, float)):
            return None
        return {"const": float(c), "has_const": True}
    name = raw.get("name", key)
    try:
        scale = float(raw.get("scale", 1.0))
    except (TypeError, ValueError):
        return None
    if not (isinstance(name, str) and _NAME_RE.match(name)) or not scale or scale != scale:
        return None
    return {"name": name, "scale": scale}


def _specs(die: Optional[str] = None, config: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """our key -> spec, the global map first, then the die's, then the configuration's (later wins).  ``die`` / ``config``
    default to the loaded configuration."""
    doc = _map_doc()
    base = doc.get("map") if isinstance(doc.get("map"), dict) else {k: v for k, v in doc.items() if k not in ("dies", "map")}
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in (base or {}).items():
        s = _spec_of(str(k), v) if v else None
        if s:
            out[str(k)] = s
    dies = doc.get("dies") if isinstance(doc.get("dies"), dict) else {}
    if dies:
        if die is None and config is None:
            tgt = _coil_target()
            die, config = ((tgt or {}).get("die"), (tgt or {}).get("config"))
        blk = dies.get(die) if isinstance(dies.get(die), dict) else None
        layers = []
        if blk:
            layers.append(blk.get("map"))
            cfgs = blk.get("configs") if isinstance(blk.get("configs"), dict) else {}
            if isinstance(cfgs.get(config), dict):
                layers.append(cfgs[config].get("map"))
        for layer in layers:
            for k, v in (layer or {}).items() if isinstance(layer, dict) else []:
                s = _spec_of(str(k), v)
                if s:
                    out[str(k)] = s
                else:
                    out.pop(str(k), None)          # an unusable override must not leave the global name behind
    return out


#: Primary geometry keys kept OUT of the Fusion export on purpose.
#
#: WHY.  `slot_hs` (user 2026-09-14: "throw out slot_hs, we don't use it") is
#: a primary parameter by type, but the geometry schema itself calls it hidden
#: — "yields non-manufacturable fractional wire thicknesses, so hidden from the
#: UI for now (kept in the file; not yet wired into the CadQuery geometry)".
#: A user parameter the model does not drive and nobody may sensibly edit is
#: noise in Fusion's list, so it is neither exported nor read back: the import
#: treats such a row exactly like a derived one — ignored, and reported.
#: `stator_outer_notches` (2026-11-03) is a lamination FLAG, not a length:
#: Fusion's parameter table holds expressions with units, and the notch is a
#: feature of the Fusion sketch itself (suppress it there), so the flag is
#: set in the Geometry tab and never travels through the CSV.
FUSION_EXCLUDED_NAMES: frozenset = frozenset({"slot_hs", "stator_outer_notches"})


def _rows():
    """(fusion_name, our_key, value, unit, comment) for every PRIMARY geometry
    parameter.  The derived ones (radii, counts, pitches, slot_width) are NOT
    exported (user 2026-09-13: "throw the DERIVED variables out of the export"): Fusion
    rebuilds them from the primaries the same way the geometry does, and a
    user parameter nobody may edit only clutters the list.  Same for the
    explicit exclusions in FUSION_EXCLUDED_NAMES.  The import still accepts and
    ignores both kinds of row, so an older CSV round-trips unchanged."""
    from motor_ai_sim.routes.freecad import _params_rows
    from motor_ai_sim.routes._validation import DERIVED_GEOMETRY_NAMES
    geo = dict(get_config().get("geometry") or {})
    specs = _specs()
    out = []
    for k, v, unit, desc in _params_rows(geo):
        if k in DERIVED_GEOMETRY_NAMES or k in FUSION_EXCLUDED_NAMES:
            continue
        s = specs.get(k)
        if s is not None and (s.get("ignore") or s.get("has_const")):
            continue                                # no Fusion variable for this key on this machine
        name = (s or {}).get("name", k)
        scale = (s or {}).get("scale", 1.0)
        out.append((name, k, round(v / scale, 9) if scale != 1.0 else v, ("mm" if unit == "mm" else ""), desc))
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

    Before anything is applied — dry_run=1 as much as dry_run=0 — every
    changed value is checked TOGETHER through
    ``routes.geometry.check_geometry_submission``: the same unknown-field /
    schema-bounds / impossible-derived-value guard ``PUT /api/geometry``
    refuses a write with.  A CSV that describes a machine that cannot exist
    (e.g. stator_diameter too small for its own slot_height + core_thickness
    + air_gap + magnet_height + rotor_house_height, so the rotor bore comes
    out negative) is refused on BOTH calls, with the same
    `invalid_parameters` list either way: `ok: false` and nothing applied on
    dry_run=1 (so the confirm dialog can show the errors and disable OK), a
    422 on dry_run=0 (so a client that skips the dry run cannot write it
    either).

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
    specs = _specs()
    inv = {s["name"]: (k, s["scale"]) for k, s in specs.items() if s.get("name")}   # Fusion name -> (our key, scale)
    ignored_names = {k for k, s in specs.items() if s.get("ignore")}
    geo = dict(get_config().get("geometry") or {})
    from motor_ai_sim.routes._validation import DERIVED_GEOMETRY_NAMES
    vals: Dict[str, float] = {}
    unknown, refused, derived, excluded = [], {}, [], []
    # The coil of the loaded CONFIGURATION (owner 2026-11-02: «все бери из
    # Fusion»): its inner bend radius and straight overhang are not live
    # geometry keys but the configuration's own ``coil_overrides``.  Fusion
    # usually keeps them as formulas, so the add-in's evaluated Value is read
    # when the Expression is not a plain number; ``coil_straight_extra`` is
    # also derived from ``coil_straight_len - motor_length`` (the template's
    # own definition) when the CSV carries the length only.
    from motor_ai_sim.coil_build import CONFIG_COIL_KEYS, validate_coil_override
    coil_vals: Dict[str, float] = {}
    coil_straight_len: Optional[float] = None
    for r in rows:
        fname = str(r.get(cols["name"]) or "").strip()
        if not fname:
            continue
        key, _scale = inv.get(fname, (fname, 1.0))
        if key in ignored_names:
            continue
        if key in CONFIG_COIL_KEYS or key == "coil_straight_len":
            unit = r.get(cols.get("unit", ""), "")
            v, why = _parse_expression(r.get(cols["expression"]), unit)
            if v is None and "value" in cols:
                v, why = _parse_expression(r.get(cols["value"]), unit)
            if v is None:
                refused[fname] = why; continue
            if key == "coil_straight_len":
                coil_straight_len = v
            else:
                coil_vals[key] = v
            continue
        if key not in geo and key not in _OPTIONAL_KEYS:
            unknown.append(fname); continue
        if key in DERIVED_GEOMETRY_NAMES:
            derived.append(key); continue
        if key in FUSION_EXCLUDED_NAMES:
            excluded.append(key); continue
        v, why = _parse_expression(r.get(cols["expression"]), r.get(cols.get("unit", ""), ""))
        if v is None:
            refused[fname] = why; continue
        vals[key] = v * _scale if _scale != 1.0 else v
    # keys with NO Fusion variable on this machine (``const``): this value on every import
    consts = {k: s["const"] for k, s in specs.items() if s.get("has_const")}
    for k, c in consts.items():
        vals[k] = c
    if coil_straight_len is not None and "coil_straight_extra" not in coil_vals:
        _L = vals.get("motor_length", geo.get("motor_length"))
        try:
            coil_vals["coil_straight_extra"] = round(float(coil_straight_len) - float(_L), 6)
        except (TypeError, ValueError):
            refused["coil_straight_len"] = "no motor_length to subtract it from"
    for _k in list(coil_vals):
        _v, _why = validate_coil_override(_k, coil_vals[_k])
        if _why:
            refused[_k] = _why
            coil_vals.pop(_k)
    changed = {k: v for k, v in vals.items()
               if geo.get(k) is None or abs(float(geo[k]) - float(v)) > 1e-5}
    out: Dict[str, Any] = {
        "ok": True, "dry_run": bool(dry_run),
        "applied": {k: changed[k] for k in sorted(changed)},
        "before": {k: geo.get(k) for k in sorted(changed)},
        "unchanged": sorted(set(vals) - set(changed)),
        "derived_ignored": sorted(set(derived)),
        "excluded_ignored": sorted(set(excluded)),
        "unknown": sorted(unknown),
        "refused": refused,
    }
    if consts or any(s.get("scale", 1.0) != 1.0 for s in specs.values()):
        out["map"] = {"constants": {k: consts[k] for k in sorted(consts)},
                      "scaled": {k: s["scale"] for k, s in sorted(specs.items()) if s.get("scale", 1.0) != 1.0}}
    coil_target = _coil_target() if coil_vals else None
    coil_changed: Dict[str, float] = {}
    if coil_vals:
        before = coil_target.get("overrides", {}) if coil_target else {}
        coil_changed = {k: v for k, v in coil_vals.items()
                        if before.get(k) is None or abs(float(before[k]) - float(v)) > 1e-6}
        why_not = ("no configuration is loaded - the coil belongs to a configuration" if coil_target is None else
                   "configuration '%s' is locked - unlock it to take its coil from Fusion" % coil_target["config"]
                   if coil_target.get("locked") and coil_changed else None)
        if why_not:
            coil_target, coil_changed = None, {}
        # additive bucket: the loaded configuration's coil (never the live geometry)
        out["coil"] = {
            "die": (coil_target or {}).get("die"), "config": (coil_target or {}).get("config"),
            "values": {k: coil_vals[k] for k in sorted(coil_vals)},
            "applied": {k: coil_changed[k] for k in sorted(coil_changed)},
            "before": {k: before.get(k) for k in sorted(coil_changed)},
            **({"not_applied": why_not} if why_not else {}),
        }
    # ── Validate the RESULTING machine — every imported value APPLIED
    # TOGETHER — before dry_run=1 (the confirm dialog) tells the user
    # anything is fine to apply.  Before this, the dry run only diffed
    # values against the live machine: it never asked whether the numbers it
    # was about to show describe a machine that can exist, so a CSV whose
    # stator_diameter is too small for its own slot_height + core_thickness +
    # air_gap + magnet_height + rotor_house_height (a negative rotor bore)
    # produced a clean "apply 30 parameters?" dialog, and only the SECOND
    # click — the real write, through update_geometry's own guard — was
    # refused.  ``check_geometry_submission`` is the exact same three checks
    # that write refuses with (unknown fields / schema bounds / impossible
    # derived values), so dry_run and the real write always agree, and dry_run
    # now reports it too — the confirm dialog must see this before the user
    # ever gets to click OK.
    from motor_ai_sim.routes.geometry import check_geometry_submission
    _checks = check_geometry_submission(changed) if changed else {}
    _invalid: list = []
    for _records in _checks.values():
        _invalid.extend(_records)
    if _invalid:
        _error = next(iter(_checks))
        out["ok"] = False
        out["error"] = _error
        out["invalid_parameters"] = _invalid
        out["note"] = ("refused — applying every imported parameter together "
                       "describes a machine that cannot be built; see "
                       "invalid_parameters (nothing was written)")
        if dry_run:
            return out
        raise reject(_error, _invalid)

    _coil_todo = bool(coil_changed) and coil_target is not None
    if (not changed and not _coil_todo) or dry_run:
        out["note"] = ("nothing to apply — every recognised parameter already has this value"
                       if not (changed or _coil_todo) else "dry run — nothing written")
        return out
    if changed:
        # THE geometry write path, never a private copy of it.
        from motor_ai_sim.routes.geometry import GeometryUpdateModel, update_geometry
        result = update_geometry(GeometryUpdateModel(**changed))
        log.info("Fusion import: %d parameter(s) applied (%s), %d unknown, %d refused",
                 len(changed), ", ".join(sorted(changed)), len(unknown), len(refused))
        out["geometry_validation"] = (result or {}).get("geometry_validation")
        out["constraints_applied"] = (result or {}).get("constraints_applied")
    if _coil_todo:
        # THE configuration coil writer (validation, lock, cache, build signature)
        from motor_ai_sim.routes.family import set_config_coil
        res = set_config_coil(coil_target["die"], coil_target["config"], coil_changed, source="fusion import")
        out["coil"]["k_end_before"] = res.get("k_end_before")
        out["coil"]["k_end_after"] = res.get("k_end_after")
        log.info("Fusion import: coil of '%s/%s' set (%s)", coil_target["die"], coil_target["config"],
                 ", ".join("%s=%g" % kv for kv in sorted(coil_changed.items())))
    return out


def _coil_target() -> Optional[Dict[str, Any]]:
    """``{die, config, overrides}`` of the loaded configuration whose coil a Fusion import sets, or None."""
    try:
        from motor_ai_sim import coil_build
        from motor_ai_sim.routes import family as fam
        ctx = fam._read_ctx() or {}
        die, cfg = str(ctx.get("die") or ""), str(ctx.get("config") or "")
        if not die or not cfg:
            return None
        doc = fam.config_doc(die, cfg)
        if doc is None:
            return None
        return {"die": die, "config": cfg, "locked": doc.get("locked") is True,
                "overrides": coil_build.clean_coil_overrides(doc.get(coil_build.CONFIG_COIL_BLOCK))}
    except Exception:      # noqa: BLE001 — no target = reported, never a 500
        return None
