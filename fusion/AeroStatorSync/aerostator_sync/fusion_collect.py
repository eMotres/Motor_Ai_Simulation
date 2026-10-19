"""Read a Fusion design into an export bundle (docs/FUSION_SYNC.md section 3.1).  Runs inside Fusion; changes nothing in the document.

Works on any open document, not only the active one (`collect(doc, ...)`), so the same code serves the add-in's button and a
test harness driving Fusion through its MCP server.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile

import adsk.core
import adsk.fusion

from . import bundle as B
from .params import internal_to_unit


class ExportRefused(RuntimeError):
    pass


def design_of(doc) -> "adsk.fusion.Design":
    des = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
    if des is None:
        raise ExportRefused("the document has no Fusion design")
    return des


def _mm(v: float) -> float:
    return round(v * 10.0, 6)


def _bbox(bb) -> list:
    return [_mm(bb.minPoint.x), _mm(bb.minPoint.y), _mm(bb.minPoint.z), _mm(bb.maxPoint.x), _mm(bb.maxPoint.y), _mm(bb.maxPoint.z)]


def _matrix_mm(m) -> list:
    """4x4 row-major world matrix with the translation in mm (Fusion gives cm)."""
    a = m.asArray()
    out = [round(x, 9) for x in a]
    for i in (3, 7, 11):
        out[i] = _mm(a[i])
    return out


def user_parameters(des) -> list:
    out = []
    for p in des.userParameters:
        out.append(dict(name=p.name, expression=p.expression, unit=p.unit, value=round(internal_to_unit(p.value, p.unit), 9),
                        comment=p.comment))
    return out


def _is_shown(comp, root) -> bool:
    """True when Fusion's own STEP export of `comp` would contain all its solids: some occurrence is visible (parents
    included) and no solid body is switched off.  Fusion silently leaves hidden geometry out of a STEP."""
    occs = list(root.allOccurrencesByComponent(comp))
    if occs and not any(o.isVisible for o in occs):
        return False
    return all(b.isLightBulbOn for b in comp.bRepBodies if b.isSolid)


class _HiddenExporter:
    """STEP of a hidden component without touching the source document: its solids are copied (TemporaryBRepManager) into a
    temporary design that is closed unsaved.  Switching light bulbs on and off would mark the source document modified.
    Fusion has no invisible documents, so the temporary design shows for a moment; the source document is activated again."""

    def __init__(self, src_doc):
        self.src, self.doc, self.des = src_doc, None, None

    def export(self, comp, path):
        app = adsk.core.Application.get()
        if self.doc is None:
            self.doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
            self.des = adsk.fusion.Design.cast(self.doc.products.itemByProductType("DesignProductType"))
            self.des.designType = adsk.fusion.DesignTypes.DirectDesignType
        tbm = adsk.fusion.TemporaryBRepManager.get()
        root = self.des.rootComponent
        added = []
        for b in comp.bRepBodies:
            if b.isSolid:
                nb = root.bRepBodies.add(tbm.copy(b))
                nb.name = b.name
                added.append(nb)
        em = self.des.exportManager
        ok = em.execute(em.createSTEPExportOptions(path))
        for nb in added:
            nb.deleteMe()
        return ok

    def close(self):
        if self.doc is not None:
            self.doc.close(False)
            self.doc = None
            self.src.activate()


def _solid_components(des):
    """Components that own solid bodies (the root's own bodies come with assembly.step)."""
    root = des.rootComponent
    for c in des.allComponents:
        if c == root:
            continue
        if any(b.isSolid for b in c.bRepBodies):
            yield c


def collect(doc, out_dir: str, *, sketches=(), link=None, allow_modified: bool = False, progress=None) -> dict:
    """Write the bundle files of `doc` under out_dir and return the manifest (also written as manifest.json).

    sketches: ["Component/Sketch", ...] to export as DXF; a missing one refuses the export."""
    if doc.isModified and not allow_modified:
        raise ExportRefused("the document has unsaved changes: save a version first, so the export is exactly version %d"
                            % (doc.dataFile.latestVersionNumber + 1 if doc.dataFile else 0))
    if doc.dataFile is None:
        raise ExportRefused("the document was never saved to the Fusion hub")
    des = design_of(doc)
    em = des.exportManager
    say = progress or (lambda msg: None)
    os.makedirs(os.path.join(out_dir, "parts"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "sketches"), exist_ok=True)

    with open(os.path.join(out_dir, "parameters.json"), "w", encoding="utf-8") as fh:
        json.dump(user_parameters(des), fh, indent=1, ensure_ascii=False)

    hidden = _HiddenExporter(doc)
    try:
        parts, sk_out = _export_geometry(des, em, out_dir, sketches, say, hidden)
    finally:
        hidden.close()
    app = adsk.core.Application.get()
    df = doc.dataFile
    manifest = B.make_manifest(document=doc.name, urn=df.id, version=df.versionNumber, link=link,
                               fusion_build=app.version, user=app.currentUser.displayName if app.currentUser else "",
                               parts=parts, sketches=sk_out, root_dir=out_dir)
    return manifest


def _export_geometry(des, em, out_dir, sketches, say, hidden_exporter):
    say("assembly STEP")
    if not em.execute(em.createSTEPExportOptions(os.path.join(out_dir, "assembly.step"))):
        raise ExportRefused("the assembly STEP export failed")

    parts, used = {}, set()
    root = des.rootComponent
    for comp in _solid_components(des):
        fname = B.unique_file(B.safe_name(comp.name), used, ".step")
        say("part " + comp.name)
        shown = _is_shown(comp, root)
        path = os.path.join(out_dir, "parts", fname)
        ok = em.execute(em.createSTEPExportOptions(path, comp)) if shown else hidden_exporter.export(comp, path)
        if not ok:
            raise ExportRefused("the STEP export of component %r failed" % comp.name)
        bodies = []
        for b in comp.bRepBodies:
            if not b.isSolid:
                continue
            bodies.append(dict(name=b.name, volume_mm3=round(b.volume * 1000.0, 6), area_mm2=round(b.area * 100.0, 6),
                               bbox_mm=_bbox(b.boundingBox), faces=b.faces.count, hidden=not b.isLightBulbOn,
                               material=b.material.name if b.material else None))
        threads = set()                                      # cosmetic threads: the server colours these cylinders (GLB, GUI)
        for tf in comp.features.threadFeatures:
            for f in tf.faces:
                if f.geometry.objectType.endswith("Cylinder"):
                    threads.add(round(f.geometry.radius * 10.0, 6))
        occs = [dict(path=o.fullPathName, hidden=not o.isVisible, matrix=_matrix_mm(o.transform2))
                for o in root.allOccurrencesByComponent(comp)]
        parts[comp.name] = dict(file="parts/" + fname, bodies=bodies, occurrences=occs, hidden_in_document=not shown,
                                thread_radii=sorted(threads), referenced=any(o.isReferencedComponent for o in root.allOccurrencesByComponent(comp)),
                                material=comp.material.name if comp.material else None)

    sk_out, used_dxf = {}, set()
    for key in sketches:
        cname, _, sname = key.partition("/")
        comp = des.allComponents.itemByName(cname)
        sk = comp.sketches.itemByName(sname) if comp else None
        if sk is None:
            raise ExportRefused("sketch %r (listed in the link) is not in the design" % key)
        fname = B.unique_file(B.safe_name(cname + "__" + sname), used_dxf, ".dxf")
        say("sketch " + key)
        ok = em.execute(em.createDXFSketchExportOptions(os.path.join(out_dir, "sketches", fname), sk))
        if not ok:
            raise ExportRefused("the DXF export of sketch %r failed" % key)
        sk_out[key] = dict(file="sketches/" + fname, curves=sk.sketchCurves.count, fully_constrained=sk.isFullyConstrained,
                           transform=_matrix_mm(sk.transform))
    return parts, sk_out


def export_zip(doc, *, sketches=(), link=None, allow_modified=False, progress=None, keep_dir=None) -> str:
    """collect() into a temporary folder and zip it; returns the zip path (in the temp folder, or keep_dir)."""
    work = tempfile.mkdtemp(prefix="aerostator_export_")
    files = os.path.join(work, "bundle")
    m = collect(doc, files, sketches=sketches, link=link, allow_modified=allow_modified, progress=progress)
    errs = B.check_manifest(m, lambda rel: _read(files, rel))
    if errs:
        raise ExportRefused("bundle self-check failed: " + "; ".join(errs[:5]))
    out = os.path.join(keep_dir or work, B.bundle_name(doc.name, m["version"]))
    B.write_zip(files, m, out)
    if keep_dir:
        shutil.rmtree(work, ignore_errors=True)
    return out


def _read(root, rel):
    p = os.path.join(root, *rel.split("/"))
    if not os.path.isfile(p):
        return None
    with open(p, "rb") as fh:
        return fh.read()


def apply_parameters(des, rows) -> dict:
    """Write the ticked Import rows: update by name, create the missing ones.  Never saves.  Returns {updated, created, failed}."""
    up = des.userParameters
    out = dict(updated=[], created=[], failed={})
    for r in rows:
        if not r.get("tick"):
            continue
        try:
            p = up.itemByName(r["name"])
            if p is None:
                up.add(r["name"], adsk.core.ValueInput.createByString(r["new_expression"]), r.get("unit") or "",
                       "AeroStator %s" % r.get("key", r["name"]))
                out["created"].append(r["name"])
            else:
                p.expression = r["new_expression"]
                out["updated"].append(r["name"])
        except Exception as e:      # noqa: BLE001 - one bad row must not hide the others; the dialog lists it
            out["failed"][r["name"]] = str(e)[:200]
    return out


def timeline_problems(des) -> list:
    """Unsuppressed timeline items that are not healthy after a recompute: [(index, name, message)]."""
    bad = []
    for i in range(des.timeline.count):
        it = des.timeline.item(i)
        if not it.isSuppressed and int(it.healthState) not in (0,):
            bad.append((i, it.name, (it.errorOrWarningMessage or "")[:200]))
    return bad
