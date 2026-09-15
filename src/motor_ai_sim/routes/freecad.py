"""FreeCAD round-trip endpoints.

GET  /api/freecad/export  → ZIP bundle: <motor>.FCStd (solids + Parameters
                            spreadsheet), <motor>.step, rebuild macro, README.
                            Where App Control blocks OCP the bundle is instead
                            DXF sections + parameters.csv + a macro that builds
                            the solids inside FreeCAD; `X-Bundle-Kind` and the
                            filename say which of the two you got.
POST /api/freecad/import  → upload an edited .FCStd; parameter values are read
                            from the spreadsheet by alias and applied through
                            THE SAME PUT-geometry path every other edit uses —
                            schema guard, loud validator, family locks, clamp
                            report and die-snapshot sync all included for free.

The heavy lifting lives in motor_ai_sim.freecad_io; this layer is HTTP + the
"what exactly changed" bookkeeping the user sees in the response.
"""
from __future__ import annotations

import io
import logging
import re
import threading
import zipfile
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from motor_ai_sim.auth import require_admin
from motor_ai_sim.config import get_config

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/freecad", tags=["freecad"])


def _motor_label() -> str:
    try:
        from motor_ai_sim.routes.family import _read_ctx
        ctx = _read_ctx()
        if ctx and ctx.get("die"):
            return str(ctx["die"])
    except Exception:  # noqa: BLE001
        pass
    return "motor"


def _params_rows(geo: Dict[str, Any]):
    """(name, value, unit, description) rows — schema-driven so labels/units
    stay in one place; parameters absent from the schema still export (bare)."""
    sch = dict(get_config().get("geometry_schema") or {})
    from motor_ai_sim.routes._validation import DERIVED_GEOMETRY_NAMES
    rows = []
    for k in sorted(geo):
        v = geo[k]
        if not isinstance(v, (int, float)):
            continue
        e = sch.get(k) or {}
        desc = str(e.get("description", "") or "")
        if k in DERIVED_GEOMETRY_NAMES:
            # exported for reference; the import path skips them (recomputed
            # from primaries on every save) — say so where the user will look
            desc = ("DERIVED - recomputed from the primary parameters; edits "
                    "here are ignored on import. " + desc).strip()
        rows.append((k, round(float(v), 6), str(e.get("unit", "") or ""), desc))
    return rows


# ── one build per machine, however many clicks ───────────────────────────────
# The bundle is a pure function of (geometry, label) and costs a few CPU-seconds
# (polygon rebuild + DXF/BREP writing).  Without this, N simultaneous
# downloads — a double-click, a browser retrying a slow download, two tabs —
# each took a worker thread of the SHARED anyio pool for those seconds, and the
# pool has 40 tokens for the whole API.  Single-flight + memo turns N×3 s into
# 3 s: the first caller builds, the rest wait on this lock and get the bytes.
_BUNDLE_LOCK = threading.Lock()
_BUNDLE_CACHE: Dict[str, Any] = {"key": None, "blob": None, "headers": None}


def _bundle_key(geo: Dict[str, Any], label: str, kind: str) -> str:
    import hashlib
    import json as _json
    return hashlib.md5(
        (_json.dumps(geo, sort_keys=True, default=str) + "|" + label + "|" + kind)
        .encode("utf-8")).hexdigest()


def reset_bundle_cache() -> None:
    """Forget the memoised bundle (tests)."""
    _BUNDLE_CACHE.update(key=None, blob=None, headers=None)


@router.get("/export")
def export_freecad():
    """Build the bundle for the ACTIVE machine.  Takes a few seconds (solid
    extrusion + BREP/STEP writing) — a download, not a poll job.

    Serialised and memoised per machine: see ``_BUNDLE_LOCK``."""
    from motor_ai_sim import freecad_io as F
    geo = dict(get_config().get("geometry") or {})
    if not geo:
        raise HTTPException(500, detail="live config has no geometry block")
    label = _motor_label()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label) or "motor"
    # The probe is memoised, so asking here costs nothing — and it belongs in
    # the key: the two bundle kinds are different files for the same machine.
    ok, why = F.ocp_available()
    key = _bundle_key(geo, label, "ocp" if ok else "macro")
    if _BUNDLE_CACHE["key"] == key and _BUNDLE_CACHE["blob"] is not None:
        return Response(content=_BUNDLE_CACHE["blob"],
                        media_type="application/zip",
                        headers=dict(_BUNDLE_CACHE["headers"]))

    def _store(blob: bytes, headers: Dict[str, str]) -> Response:
        _BUNDLE_CACHE.update(key=key, blob=blob, headers=dict(headers))
        return Response(content=blob, media_type="application/zip",
                        headers=headers)

    # App Control blocks OCP on the user's PC (2026-09-14): no solids can be
    # written here, so ship the DXF sections + a macro that extrudes them in
    # FreeCAD (whose own OpenCASCADE is not blocked).  Same fallback pattern as
    # mapbox_earcut -> triangle in the 3-D viewer.
    def _macro_bundle(why: str) -> Response:
        log.warning("FreeCAD export: OCP unavailable (%s) — macro bundle", why)
        try:
            blob = F.build_macro_bundle(geo, label, safe, why)
        except Exception as e:  # noqa: BLE001
            log.exception("FreeCAD macro-bundle export failed")
            raise HTTPException(500, detail=f"FreeCAD export failed: {e}")
        return _store(blob, {
            "Content-Disposition":
                f'attachment; filename="{safe}_freecad_macro.zip"',
            "X-Bundle-Kind": "macro-dxf",
            # latin-1 only: header encoding is what a response is allowed to
            # fail on, and an OS-localised loader message is not worth a 500
            # on a download.
            "X-Bundle-Reason":
                why[:200].encode("ascii", "replace").decode("ascii")})

    with _BUNDLE_LOCK:
        # Another caller may have built this exact machine while we queued.
        if _BUNDLE_CACHE["key"] == key and _BUNDLE_CACHE["blob"] is not None:
            return Response(content=_BUNDLE_CACHE["blob"],
                            media_type="application/zip",
                            headers=dict(_BUNDLE_CACHE["headers"]))
        return _build_bundle(F, geo, label, safe, ok, why,
                             _macro_bundle, _store)


def _build_bundle(F, geo: Dict[str, Any], label: str, safe: str,
                  ok: bool, why: str, _macro_bundle, _store) -> Response:
    """The actual build — always called with ``_BUNDLE_LOCK`` held."""
    if not ok:
        return _macro_bundle(why)

    try:
        solids = F.build_solids(geo)
        rows = _params_rows(geo)
        fcstd = F.write_fcstd(solids, rows, label)
        step = F.write_step(solids)
        macro = F.write_macro(rows, f"{safe}.step")
    except HTTPException:
        raise
    except ImportError as e:
        # The kernel said yes and died anyway — a blocked DLL surfacing deeper
        # inside OCP.  The user asked for CAD, not for a 500: ship the macro
        # bundle, which needs no kernel here.  ONLY ImportError: anything else
        # out of the solid builder is a real bug and must stay loud.
        log.warning("FreeCAD export: solids failed after a positive probe (%s)"
                    " — falling back to the macro bundle", e)
        return _macro_bundle(str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("FreeCAD export failed")
        raise HTTPException(500, detail=f"FreeCAD export failed: {e}")
    readme = (
        "motor_ai_sim FreeCAD export — %s (%s)\n"
        "\n"
        "%s.FCStd   open directly in FreeCAD: motor solids + the 'Parameters'\n"
        "           spreadsheet (every geometry parameter, cell alias = its name).\n"
        "           Design housings around the solids; edit values in column B.\n"
        "%s.step    the same solids for any other CAD.\n"
        "%s.FCMacro fallback: if a FreeCAD version refuses the FCStd, run this\n"
        "           macro (Macro menu) with the STEP in the same folder — it\n"
        "           rebuilds the document through FreeCAD's own API.\n"
        "\n"
        "Round-trip: change values in the Parameters sheet, save, and upload the\n"
        ".FCStd in the Geometry tab ('Import FreeCAD').  The values are validated\n"
        "by the same geometry checks as any manual edit; the solids in YOUR file\n"
        "are not read back — the system regenerates its own from the parameters\n"
        "(single master model).\n"
        % (label, datetime.now().isoformat(timespec="seconds"),
           safe, safe, safe))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{safe}.FCStd", fcstd)
        z.writestr(f"{safe}.step", step)
        z.writestr(f"{safe}.FCMacro", macro)
        z.writestr("README.txt", readme)
    return _store(out.getvalue(), {
        "Content-Disposition": f'attachment; filename="{safe}_freecad.zip"',
        "X-Bundle-Kind": "ocp-fcstd"})


@router.post("/import")
async def import_freecad(file: UploadFile = File(...),
                         _admin: dict = Depends(require_admin)):
    """Apply the Parameters spreadsheet of an uploaded .FCStd.

    Reports every alias in three buckets: applied (value differed and was
    accepted), unchanged (already at that value), unknown (no such geometry
    parameter — the user's own housing dimensions live in the same sheet and
    are legitimately none of our business).  A refusal (validator, family
    lock) surfaces as the SAME 422/423 the manual editor gets."""
    from motor_ai_sim import freecad_io as F
    data = await file.read()
    if not data:
        raise HTTPException(422, detail="empty upload")
    try:
        vals = F.read_fcstd_params(data)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, detail=f"could not read the FreeCAD file: {e}")
    if not vals:
        raise HTTPException(422, detail="no aliased numeric cells found — is "
                            "the Parameters spreadsheet still in the document?")
    geo = dict(get_config().get("geometry") or {})
    # DERIVED parameters are exported for reference but never imported: they
    # are recomputed from the primaries on every save, so "applying" one is at
    # best a no-op and at worst a stale value fighting its own primaries.
    # (num_poles/num_slots are in the derived set as segment products but ARE
    # accepted primaries elsewhere — still safer to take them from the
    # segment counts, which are importable.)
    from motor_ai_sim.routes._validation import DERIVED_GEOMETRY_NAMES
    known = {k: v for k, v in vals.items()
             if k in geo and k not in DERIVED_GEOMETRY_NAMES}
    derived = sorted(k for k in vals if k in DERIVED_GEOMETRY_NAMES)
    unknown = sorted(k for k in vals if k not in geo)
    # Tolerance = the export's own rounding (6 decimals), not float epsilon:
    # re-importing an UNTOUCHED file must report zero changes.
    changed = {k: v for k, v in known.items()
               if abs(float(geo[k]) - float(v)) > 1e-5}
    if not changed:
        return {"ok": True, "applied": {}, "unchanged": sorted(known),
                "derived_ignored": derived, "unknown": unknown,
                "note": "every recognised parameter already has this value"}
    # THE geometry write path — not a private copy of it: schema guard, region
    # validator, family locks, clamp reporting and the active-die snapshot sync
    # all fire exactly as if the user typed the values into the form.
    from motor_ai_sim.routes.geometry import GeometryUpdateModel, update_geometry
    result = update_geometry(GeometryUpdateModel(**changed))
    log.info("FreeCAD import: %d parameter(s) applied (%s), %d unknown alias(es)",
             len(changed), ", ".join(sorted(changed)), len(unknown))
    return {"ok": True, "applied": {k: changed[k] for k in sorted(changed)},
            "unchanged": sorted(set(known) - set(changed)),
            "derived_ignored": derived, "unknown": unknown,
            "geometry_validation": (result or {}).get("geometry_validation"),
            "constraints_applied": (result or {}).get("constraints_applied")}
