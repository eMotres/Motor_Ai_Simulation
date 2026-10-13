"""freecadcmd script (FreeCAD 1.0.2 / 1.1): dump what a motor model contains, for the BOM (motor_ai_sim.cad.bom).

  environment: FC_CAD_ROOT (or FC_REPO), FC_MODEL (.FCStd), FC_OUT (JSON), FC_STATUS

The stored shapes are read as saved (no recompute: a model built by FreeCAD 1.1.3 on the server is not re-evaluated by
1.0.2 identically - coils split into fragments); FC_RECOMPUTE=1 recomputes first.  One entry per BOM-relevant object, found GENERICALLY (not from a fixed name list),
so parts added later (phase board, pins, threads, further screws) show up without editing this script:
  * every PartDesign::Body that is not a feature of another body,
  * every App::Link (placed copy: back bearing, back ring),
  * every Draft array / Part feature with solids that is not inside a body (Coils, MagnetArray, Balls, Screws, imports),
and the Params sheet (alias -> value, mm / deg).  Per entry: label, type, parent App::Part, linked object, solid count,
base object (arrays), total volume, area, bounding box, centre of mass and the volume of every solid (mm, mm2, mm3).
"""
import json, os, sys, traceback

ROOT = os.environ.get("FC_CAD_ROOT") or os.environ["FC_REPO"]
sys.path.insert(0, ROOT)
import FreeCAD as App  # noqa: E402
from motor_templates.segmented_flatwire.runstatus import finish  # noqa: E402
from motor_templates.segmented_flatwire import params_sheet as ps  # noqa: E402

SKIP = ("Sketcher::", "Spreadsheet::", "App::Origin", "App::Plane", "App::Line", "App::Point", "App::Part",
        "App::DocumentObjectGroup", "App::FeaturePython")


def in_body(o):
    return any(p.TypeId == "PartDesign::Body" and o in getattr(p, "Group", []) for p in o.InList)


def parent_part(o):
    try:
        p = o.getParentGeoFeatureGroup()
        return p.Label if p is not None else None
    except Exception:
        return None


def entry(o):
    sh = o.Shape
    sols = sh.Solids
    if not sols:
        return None
    vol = sum(s.Volume for s in sols)
    b = sh.BoundBox
    com = [sum(s.Volume * s.CenterOfMass[i] for s in sols) / vol for i in range(3)]
    linked = getattr(o, "LinkedObject", None)
    base = getattr(o, "Base", None)
    return dict(name=o.Name, label=o.Label, type=o.TypeId, parent=parent_part(o),
                linked=linked.Label if linked is not None else None,
                base=base.Label if base is not None and hasattr(base, "Label") else None,
                state=[s for s in o.State if s in ("Invalid", "Error")], n_solids=len(sols), volume=vol, area=sh.Area,
                bbox=[b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax], com=com,
                solid_volumes=[round(s.Volume, 5) for s in sols[:600]])


def main():
    model, out = os.environ["FC_MODEL"], os.environ["FC_OUT"]
    doc = App.openDocument(model)
    if os.environ.get("FC_RECOMPUTE") == "1":
        for o in doc.Objects:
            o.touch()
        doc.recompute()
    entries = []
    for o in doc.Objects:
        if o.TypeId.startswith(SKIP) or not hasattr(o, "Shape"):
            continue
        if o.TypeId == "PartDesign::Body" or o.TypeId == "App::Link" or not in_body(o):
            if o.TypeId.startswith("PartDesign::") and o.TypeId != "PartDesign::Body":
                continue
            try:
                e = entry(o)
            except Exception:
                e = None
            if e:
                entries.append(e)
    sheet = doc.getObject("Params")
    params = {k: v["value"] for k, v in ps.read_sheet(sheet).items()} if sheet is not None else {}
    res = dict(model=os.path.basename(model), freecad_version=".".join(str(x) for x in App.Version()[:3]),
               meta=dict(doc.Meta or {}), params=params, entries=entries)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    finish(True, "%d BOM entries, %d Params rows (FreeCAD %s)" % (len(entries), len(params), res["freecad_version"]))


try:
    main()
except Exception:
    traceback.print_exc()
    finish(False, "exception: " + traceback.format_exc().splitlines()[-1])
