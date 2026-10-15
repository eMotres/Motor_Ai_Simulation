"""3D model menu routes: list the models a caller may see, serve the GLB, download the stamped STEP.

ONE model for everybody (owner 2026-10-13): every signed-in user who may see a motor (``motor_access``: admin / granted dies /
per-die visibility) can view it - the GLB is the full model, copper board included.  Downloading the STEP is the NDA gate:
only accounts flagged "NDA signed" and the owner / admin get it; everybody else gets 403.  The URL names the motor, never a
file.  Every download carries a download id and the NDA it rests on (STEP header + the append-only log); the admin can list
the downloads and trace a file by id or sha256.  Nothing priced is served here.

The SIMPLIFIED model (owner 2026-10-15, ``simplified.step`` / ``.glb`` / ``.json``) is open to every signed-in user who may see the motor:
no NDA, no verification - envelope only (stator + rotor + magnets one monolith, coils one thick turn, bearings one ring), stamped
"SIMPLIFIED - envelope only" and logged like every download.

Geometry tab "Full" (owner 2026-10-15) adds the full-construction files next to the STEP, all under the SAME NDA flag and the SAME
stamped, logged delivery: ``/parts`` (THE part tree, names only), ``/bom.csv`` and ``/bom.xlsx`` (the part list with materials and
masses - no prices, no suppliers), ``/params.csv`` (the full parameter set, electromagnetic + CAD-only, Fusion Parameter I/O
dialect) and ``/model.fcstd`` (the parametric FreeCAD model; engineer and admin accounts only - a plain ``user`` never gets the
FreeCAD file, owner 2026-10-13).
"""
from __future__ import annotations

import datetime
import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response

from motor_ai_sim import model3d as M
from motor_ai_sim.auth import ANON_OWNER, caller_identity, require_admin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/model3d", tags=["model3d"])


def _caller(authorization: Optional[str]) -> tuple[dict, dict]:
    """(identity, user record) of a signed-in caller; 401 for anonymous."""
    who = caller_identity(authorization)
    if not who.get("is_admin") and (not who.get("id") or who["id"] == ANON_OWNER):
        raise HTTPException(status_code=401, detail="Sign in to open the 3D models.")
    from motor_ai_sim import users as U
    rec = U.get_user(who["id"]) or {}
    return who, rec


def _entry_for(authorization: Optional[str], die: str, config: str) -> dict:
    from motor_ai_sim import motor_access as MA
    e = M.find(die, config)
    # 404 for "not granted" and "not built" alike: the list does not reveal motors the caller may not see.
    # A PRIVATE configuration of a visible die is "not granted" too (motor_access.may_see_config).
    if e is None or not MA.may_see_config(MA.catalog_access(authorization), die, config):
        raise HTTPException(status_code=404, detail="No 3D model for this motor.")
    return e


@router.get("/models")
def list_models(authorization: Optional[str] = Header(default=None)):
    """The built models of the motors this caller may open, and whether this caller may download the STEP."""
    who, rec = _caller(authorization)
    from motor_ai_sim import motor_access as MA
    access = MA.catalog_access(authorization)
    models = [M.model_info(e) for e in M.scan() if MA.may_see_config(access, e["die"], e["config"])]
    from motor_ai_sim import verification as V
    return {"can_download": M.can_download(who, rec), "access": V.public_access(who, rec), "models": [m for m in models if m["available"]]}


@router.get("/{die}/{config}/model.glb")
def get_glb(die: str, config: str, authorization: Optional[str] = Header(default=None)):
    _caller(authorization)
    e = _entry_for(authorization, die, config)
    row = M.file_row(e, "glb")
    if row is None:
        raise HTTPException(status_code=404, detail="This model has not been built yet.")
    return FileResponse(row["path"], media_type="model/gltf-binary",
                        headers={"ETag": '"%s"' % row["sha256"], "Cache-Control": "private, max-age=0, must-revalidate"})


@router.get("/{die}/{config}/model.step")
def get_step(die: str, config: str, request: Request, authorization: Optional[str] = Header(default=None)):
    """The STEP with the customer name and the date in its header and file name.  NDA accounts and the owner only; logged."""
    who, rec = _caller(authorization)
    e = _entry_for(authorization, die, config)
    if not M.can_download(who, rec):
        raise HTTPException(status_code=403, detail=M.denial_detail(who, rec))
    row = M.file_row(e, "step")
    if row is None:
        raise HTTPException(status_code=404, detail="This model has not been built yet.")
    customer = M.customer_name(who, rec)
    now = datetime.datetime.now(datetime.timezone.utc)
    day = now.date()
    name = M.download_name(e["motor_id"], customer, day)
    m = e["manifest"]
    version = "template %s, built %s, param %s" % ((m.get("template") or {}).get("version", "?"), m.get("built_at", "?"),
                                                  (m.get("param_hash") or "")[:12])
    basis = M.nda_basis(who, rec)
    who_for = M.recipient_of(who, rec, basis)
    download_id = M.new_download_id(now)
    stamp = M.build_stamp(download_id=download_id, motor_id=e["motor_id"], version=version, day=day, recipient=who_for, basis=basis)
    with open(row["path"], "rb") as fh:
        src = fh.read()
    data = M.stamp_step(src, filename=name, customer=customer, day=day, motor_id=e["motor_id"], version=version, stamp=stamp)
    try:
        from motor_ai_sim.client_ip import from_request
        ip = from_request(request)
    except Exception:                                       # noqa: BLE001
        ip = "?"
    try:
        M.log_download(dict(download_id=download_id, user=who["id"], email=who_for["email"], name=customer, role=who.get("role"),
                            company=who_for["company"], nda=basis["type"] != "internal", nda_basis=basis,
                            die=die, config=config, motor_id=e["motor_id"], kind="step", file=name,
                            source_file=row["name"], source_sha256=row["sha256"], sha256=M.sha256_bytes(data), bytes=len(data),
                            template=(m.get("template") or {}).get("version"), built_at=m.get("built_at"),
                            param_hash=m.get("param_hash"), ip=ip, user_agent=request.headers.get("user-agent", "")[:300]))
    except OSError as ex:
        log.error("model3d: download log not writable (%s) - download refused", ex)
        raise HTTPException(status_code=503, detail="Download log unavailable; try again later.")
    return Response(content=data, media_type="model/step",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % name, "Cache-Control": "no-store"})


# ── the SIMPLIFIED model (owner 2026-10-15): envelope only, open to every signed-in user ─────────────────────────────

def _simplified_row(e: dict, kind: str) -> dict:
    row = M.file_row(e, kind)
    if row is None:
        raise HTTPException(status_code=404, detail="The simplified model of this motor has not been built yet.")
    return row


def _mass_text(die: str, config: str, info: Optional[dict] = None) -> tuple[str, Optional[dict]]:
    """(text for the STEP header, dict for the JSON): the total mass of the REAL model as the app shows it (the full mass, or the
    frameless kit's with its label); falls back to the generator's own number in the simplified JSON."""
    try:
        from motor_ai_sim.modules import motor_mass as MMASS
        from motor_ai_sim.routes import family as fam
        s = MMASS.mass_summary(die, config, dies_dir=fam._die_dir(die).parent)
    except Exception:                                       # noqa: BLE001 - a mass hiccup never blocks the download
        s = None
    if s:
        g = round(s["full_kg"] * 1000.0, 1)
        scope = s.get("scope") or "full motor"
        return ("%s g (%s%s)" % (g, scope, ", partial: no CAD bill of materials yet" if s.get("partial") else ""),
                dict(total_g=g, scope=scope, partial=bool(s.get("partial"))))
    m = (info or {}).get("mass") or {}
    if m.get("total_g") is not None:
        return ("%s g (%s, bill of materials)" % (m["total_g"], m.get("scope") or "full motor"), dict(m))
    return "", None


def _deliver_simplified(request: Request, who: dict, rec: dict, e: dict, die: str, config: str, *, kind: str, ext: str, media: str,
                        row: dict, body) -> Response:
    """Stamp ("SIMPLIFIED - envelope only"), LOG (user, file, version) and return one simplified file.  No NDA, no verification: the
    log line is the accountability.  ``body`` is ``(stamp, name) -> bytes``."""
    customer = M.customer_name(who, rec)
    now = datetime.datetime.now(datetime.timezone.utc)
    day = now.date()
    m = e["manifest"]
    version = "template %s, built %s, param %s" % ((m.get("template") or {}).get("version", "?"), m.get("built_at", "?"),
                                                  (m.get("param_hash") or "")[:12])
    basis = dict(type="none", ref="none (simplified model)", label="none")
    who_for = M.recipient_of(who, rec, basis)
    download_id = M.new_download_id(now)
    mass_txt, _mass = _mass_text(die, config)
    stamp = M.build_simplified_stamp(download_id=download_id, motor_id=e["motor_id"], version=version, day=day, recipient=who_for, mass=mass_txt)
    name = M.download_name(e["motor_id"] + "_simplified", customer, day, ext)
    data = body(stamp, name)
    try:
        from motor_ai_sim.client_ip import from_request
        ip = from_request(request)
    except Exception:                                       # noqa: BLE001
        ip = "?"
    try:
        M.log_download(dict(download_id=download_id, user=who["id"], email=who_for["email"], name=customer, role=who.get("role"),
                            company=who_for["company"], nda=False, nda_basis=basis, simplified=True,
                            die=die, config=config, motor_id=e["motor_id"], kind=kind, file=name,
                            source_file=row["name"], source_sha256=row["sha256"], sha256=M.sha256_bytes(data), bytes=len(data),
                            template=(m.get("template") or {}).get("version"), built_at=m.get("built_at"),
                            param_hash=m.get("param_hash"), ip=ip, user_agent=request.headers.get("user-agent", "")[:300]))
    except OSError as ex:
        log.error("model3d: download log not writable (%s) - download refused", ex)
        raise HTTPException(status_code=503, detail="Download log unavailable; try again later.")
    return Response(content=data, media_type=media,
                    headers={"Content-Disposition": 'attachment; filename="%s"' % name, "Cache-Control": "no-store"})


@router.get("/{die}/{config}/simplified.step")
def get_simplified_step(die: str, config: str, request: Request, authorization: Optional[str] = Header(default=None)):
    """The SIMPLIFIED STEP (stator + rotor + magnets one monolith, every coil one thick turn, every bearing one ring; shaft, holders,
    board outline as built).  Every signed-in user who may see the motor - no NDA, no verification; stamped "SIMPLIFIED - envelope
    only" with the download id and the mass of the real model; logged."""
    who, rec = _caller(authorization)
    e = _entry_for(authorization, die, config)
    row = _simplified_row(e, "simplified_step")

    def body(stamp, name):
        src = open(row["path"], "rb").read()
        return M.stamp_step(src, filename=name, customer=M.customer_name(who, rec), day=datetime.date.fromisoformat(stamp["date"]),
                            motor_id=e["motor_id"], version=stamp["version"], stamp=stamp)
    return _deliver_simplified(request, who, rec, e, die, config, kind="simplified_step", ext="step", media="model/step", row=row, body=body)


@router.get("/{die}/{config}/simplified.glb")
def get_simplified_glb(die: str, config: str, request: Request, authorization: Optional[str] = Header(default=None)):
    """The simplified model for the web tools (same access, logged)."""
    who, rec = _caller(authorization)
    e = _entry_for(authorization, die, config)
    row = _simplified_row(e, "simplified_glb")
    return _deliver_simplified(request, who, rec, e, die, config, kind="simplified_glb", ext="glb", media="model/gltf-binary", row=row,
                               body=lambda stamp, name: open(row["path"], "rb").read())


@router.get("/{die}/{config}/simplified.json")
def get_simplified_json(die: str, config: str, request: Request, authorization: Optional[str] = Header(default=None)):
    """The tiny JSON of the simplified model: envelope, mounting interface, board terminals and the total mass of the real model."""
    who, rec = _caller(authorization)
    e = _entry_for(authorization, die, config)
    row = _simplified_row(e, "simplified_json")

    def body(stamp, name):
        import json
        info = json.loads(open(row["path"], encoding="utf-8").read())
        _txt, mass = _mass_text(die, config, info)
        if mass:
            info["mass"] = mass
        info["download"] = dict(id=stamp["download_id"], notice=stamp["notice"], date=stamp["date"])
        return json.dumps(info, indent=1, ensure_ascii=False).encode("utf-8")
    return _deliver_simplified(request, who, rec, e, die, config, kind="simplified_json", ext="json", media="application/json", row=row, body=body)


@router.get("/downloads")
def downloads(limit: int = 200, user: str = "", company: str = "", motor: str = "", nda_version: str = "",
              _admin: dict = Depends(require_admin)):
    """Admin: the most recent downloads, newest first; optional case-insensitive filters (user, company, motor, NDA version)."""
    return {"downloads": M.read_log(max(1, min(int(limit), 2000)), user=user, company=company, motor=motor,
                                    nda_version=nda_version)}


@router.post("/trace")
def trace(body: dict = Body(default={}), _admin: dict = Depends(require_admin)):
    """Admin: which download is this file?  ``{"text": ...}`` is a STEP header line, a download id or a sha256 (the page hashes an
    uploaded file in the browser and sends its sha256 and the id found in its header; the file itself never leaves the browser)."""
    return M.trace(str(body.get("text") or "")[:20000])


# ── Geometry "Full": the full-construction files (owner 2026-10-15) ───────────────────────────────────────────────

def _tree_of(die: str, config: str, *, brief: bool = False) -> dict:
    from motor_ai_sim import part_tree as PT
    from motor_ai_sim.modules import motor_mass as MMASS
    from motor_ai_sim.routes import family as fam
    try:
        return PT.part_tree(die, config, dies_dir=fam._die_dir(die).parent, brief=brief)
    except MMASS.MotorMassError as ex:
        raise HTTPException(status_code=404, detail=str(ex))


@router.get("/{die}/{config}/parts")
def get_parts(die: str, config: str, authorization: Optional[str] = Header(default=None)):
    """THE part tree for the 3-D viewer's parts list: ids, names (EN + zh-CN), groups, order and the viewer nodes each part draws.
    After the NDA, like the viewer's other tools; no material and no mass (a viewer is not a bill of materials)."""
    who, rec = _caller(authorization)
    _entry_for(authorization, die, config)
    if not M.can_download(who, rec):
        raise HTTPException(status_code=403, detail=M.denial_detail(who, rec))
    return _tree_of(die, config, brief=True)


def _deliver(request: Request, who: dict, rec: dict, e: dict, die: str, config: str, *, kind: str, ext: str, media: str,
             body, source_name: str = "", source_sha: str = "") -> Response:
    """Stamp, log and return one non-STEP delivery.  ``body`` is a function ``(stamp, name) -> bytes`` so the stamp (download id,
    recipient, NDA reference) can be written INTO the file; the log line carries the sha256 of the delivered bytes."""
    customer = M.customer_name(who, rec)
    now = datetime.datetime.now(datetime.timezone.utc)
    day = now.date()
    m = e["manifest"]
    version = "template %s, built %s, param %s" % ((m.get("template") or {}).get("version", "?"), m.get("built_at", "?"),
                                                  (m.get("param_hash") or "")[:12])
    basis = M.nda_basis(who, rec)
    who_for = M.recipient_of(who, rec, basis)
    download_id = M.new_download_id(now)
    stamp = M.build_stamp(download_id=download_id, motor_id=e["motor_id"], version=version, day=day, recipient=who_for, basis=basis)
    name = M.download_name(e["motor_id"] + "_" + kind.replace("_", "-"), customer, day, ext)
    data = body(stamp, name)
    try:
        from motor_ai_sim.client_ip import from_request
        ip = from_request(request)
    except Exception:                                       # noqa: BLE001
        ip = "?"
    try:
        M.log_download(dict(download_id=download_id, user=who["id"], email=who_for["email"], name=customer, role=who.get("role"),
                            company=who_for["company"], nda=basis["type"] != "internal", nda_basis=basis,
                            die=die, config=config, motor_id=e["motor_id"], kind=kind, file=name,
                            source_file=source_name, source_sha256=source_sha, sha256=M.sha256_bytes(data), bytes=len(data),
                            template=(m.get("template") or {}).get("version"), built_at=m.get("built_at"),
                            param_hash=m.get("param_hash"), ip=ip, user_agent=request.headers.get("user-agent", "")[:300]))
    except OSError as ex:
        log.error("model3d: download log not writable (%s) - download refused", ex)
        raise HTTPException(status_code=503, detail="Download log unavailable; try again later.")
    return Response(content=data, media_type=media,
                    headers={"Content-Disposition": 'attachment; filename="%s"' % name, "Cache-Control": "no-store"})


def _nda_gate(authorization: Optional[str], die: str, config: str, need_engineer: bool = False):
    who, rec = _caller(authorization)
    e = _entry_for(authorization, die, config)
    if not M.can_download(who, rec):
        raise HTTPException(status_code=403, detail=M.denial_detail(who, rec))
    if need_engineer and not who.get("is_admin") and str(who.get("role") or rec.get("role") or "user") not in ("engineer", "admin"):
        raise HTTPException(status_code=403, detail="The FreeCAD model is not available for this account type; the STEP is.")
    return who, rec, e


BOM_HEADER = ["item", "part id", "group", "part", "part (zh-CN)", "material", "role", "mass g", "counted in motor mass",
              "used by EM simulation", "status"]


def _bom_rows(tree: dict) -> list:
    rows = []
    for i, p in enumerate(tree["parts"], 1):
        rows.append([i, p["id"], p["group"], p["name"], p["names"]["zh"], p.get("material_id") or "", p.get("role") or "",
                     "" if p.get("mass_kg") is None else round(p["mass_kg"] * 1000.0, 3), "yes" if p.get("counted") else "no",
                     "yes" if p.get("em") else "no", p.get("status") or ""])
    return rows


def _bom_total(tree: dict) -> list:
    return ["", "", "", "total (counted)", "", "", "", round((tree.get("total_kg") or 0) * 1000.0, 3), "", "",
            "partial mass (no CAD model yet)" if tree.get("partial") else (tree.get("scope") if tree.get("kit") else "")]


@router.get("/{die}/{config}/bom.csv")
def get_bom_csv(die: str, config: str, request: Request, authorization: Optional[str] = Header(default=None)):
    """The part list with materials and masses (THE part tree, same ids and order as everywhere), CSV.  NDA accounts and the owner;
    stamped and logged.  No prices, no suppliers."""
    who, rec, e = _nda_gate(authorization, die, config)
    tree = _tree_of(die, config)

    def body(stamp, name):
        import csv
        import io
        buf = io.StringIO()
        buf.write("# " + stamp["line"] + "\n")                  # a comment line, not a quoted cell
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(BOM_HEADER)
        w.writerows(_bom_rows(tree))
        w.writerow(_bom_total(tree))
        return buf.getvalue().encode("utf-8-sig")
    return _deliver(request, who, rec, e, die, config, kind="bom_csv", ext="csv", media="text/csv", body=body)


@router.get("/{die}/{config}/bom.xlsx")
def get_bom_xlsx(die: str, config: str, request: Request, authorization: Optional[str] = Header(default=None)):
    """The same part list as an Excel workbook (sheet "BOM", the notice on sheet "Notice")."""
    who, rec, e = _nda_gate(authorization, die, config)
    tree = _tree_of(die, config)

    def body(stamp, name):
        import io
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "BOM"
        ws.append(BOM_HEADER)
        for r in _bom_rows(tree):
            ws.append(r)
        ws.append(_bom_total(tree))
        n = wb.create_sheet("Notice")
        n.append([stamp["notice"]])
        n.append([stamp["line"]])
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()
    return _deliver(request, who, rec, e, die, config, kind="bom_xlsx", ext="xlsx",
                    media="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", body=body)


def full_param_rows(die: str, config: str) -> list:
    """(name, unit, value, comment) of the FULL parameter set of a motor: the electromagnetic primary parameters (Active) and the
    CAD-only construction parameters (Full), each CAD row marked "CAD only, not simulated"."""
    import yaml
    from motor_ai_sim import cad_params as CP
    from motor_ai_sim.routes import family as fam
    from motor_ai_sim.routes import freecad as FC
    from motor_ai_sim.routes import fusion as FU
    from motor_ai_sim.routes._validation import DERIVED_GEOMETRY_NAMES
    ddir = fam._die_dir(die)
    dy = yaml.safe_load((ddir / "die.yaml").read_text(encoding="utf-8")) or {}
    cy = yaml.safe_load((ddir / ("%s.yaml" % config)).read_text(encoding="utf-8")) or {}
    geo = dict(dy.get("geometry") or {})
    geo.update(cy.get("geometry_overrides") or {})
    names = FU._name_map()
    out = []
    for k, v, unit, desc in FC._params_rows(geo):
        if k in DERIVED_GEOMETRY_NAMES or k in FU.FUSION_EXCLUDED_NAMES:
            continue
        out.append((names.get(k, k), "mm" if unit == "mm" else "", v, desc))
    built = CP.read_built_params(die, config)
    if built is not None:
        from motor_ai_sim.modules import motor_mass as _mm
        kit = _mm.config_cad_variant(ddir, config) == "frameless"
        for r in CP.rows(built, CP.clean_overrides(cy.get(CP.OVERRIDES_KEY)), kit):
            out.append((r["name"], r["unit"], round(r["value"], 6), "%s. %s" % (CP.CAD_ONLY_LABEL["en"], r["tip"]["en"])))
    return out


@router.get("/{die}/{config}/params.csv")
def get_full_params_csv(die: str, config: str, request: Request, authorization: Optional[str] = Header(default=None)):
    """The full parameter set (EM + CAD-only) as a Fusion Parameter I/O CSV (six columns).  The download id is written into every
    Comment; NDA accounts and the owner; logged."""
    who, rec, e = _nda_gate(authorization, die, config)
    rows = full_param_rows(die, config)

    def body(stamp, name):
        import csv
        import io
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["Name", "Unit", "Expression", "Value", "Comment", "Favorite"])
        for n, unit, v, desc in rows:
            num = "%g" % v
            w.writerow([n, unit, num, num, ("%s [%s]" % (desc, stamp["download_id"])).strip(), "False"])
        return buf.getvalue().encode("utf-8-sig")
    return _deliver(request, who, rec, e, die, config, kind="params_csv", ext="csv", media="text/csv", body=body)


@router.get("/{die}/{config}/model.fcstd")
def get_fcstd(die: str, config: str, request: Request, authorization: Optional[str] = Header(default=None)):
    """The parametric FreeCAD model of the build (solids + the Params sheet).  NDA + engineer / admin role only; logged."""
    who, rec, e = _nda_gate(authorization, die, config, need_engineer=True)
    from pathlib import Path
    p = Path(e["dir"]) / (e["motor_id"] + ".FCStd")
    row = (e["manifest"].get("files") or {}).get(p.name)
    if not row or not p.is_file():
        raise HTTPException(status_code=404, detail="This model has no FreeCAD file.")
    return _deliver(request, who, rec, e, die, config, kind="fcstd", ext="FCStd", media="application/octet-stream",
                    body=lambda stamp, name: p.read_bytes(), source_name=p.name, source_sha=row.get("sha256") or "")
