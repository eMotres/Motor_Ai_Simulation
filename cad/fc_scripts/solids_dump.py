"""freecadcmd script (FreeCAD 1.0.2 / 1.1): dump every solid of a motor model (FCStd or STEP) as JSON, for the
cross-version comparison (motor_ai_sim.cad.compare).

  environment: FC_CAD_ROOT, FC_MODEL (.FCStd or .step), FC_OUT (JSON), FC_STATUS

FCStd: the document is fully recomputed first.  Solids are named like the export: Core, RotorCore, Coil_<n>, Magnet_<nn>
(from the bodies Core, RotorCore, Coils, MagnetArray).  STEP: the imported solid objects by label.
Per solid: volume, area, bounding box, centre of mass (mm, mm3, mm2).
"""
import json, os, sys, traceback

sys.path.insert(0, os.environ["FC_CAD_ROOT"])
import FreeCAD as App  # noqa: E402
from motor_templates.segmented_flatwire.runstatus import finish  # noqa: E402


def row(s):
    """volume, area, bbox, centre of mass (volume-weighted over the solids of a compound)."""
    sols = s.Solids or [s]
    vol = sum(x.Volume for x in sols)
    com = [sum(x.Volume * x.CenterOfMass[i] for x in sols) / vol for i in range(3)]
    b = s.BoundBox
    return dict(volume=s.Volume, area=s.Area, bbox=[b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax],
                com=com, faces=len(s.Faces))


def main():
    model, out = os.environ["FC_MODEL"], os.environ["FC_OUT"]
    solids = {}
    if model.lower().endswith(".fcstd"):
        doc = App.openDocument(model)
        for o in doc.Objects:
            o.touch()
        doc.recompute()
        solids["Core"] = row(doc.getObject("Core").Shape)
        solids["RotorCore"] = row(doc.getObject("RotorCore").Shape)
        for i, s in enumerate([o for o in doc.Objects if o.Label == "Coils"][0].Shape.Solids):
            solids["Coil_%d" % (i + 1)] = row(s)
        for i, s in enumerate([o for o in doc.Objects if o.Label == "MagnetArray"][0].Shape.Solids):
            solids["Magnet_%02d" % (i + 1)] = row(s)
    else:
        import Import
        doc = App.newDocument("dump")
        Import.insert(model, doc.Name)
        for o in doc.Objects:
            if o.TypeId == "Part::Feature" and not o.Shape.isNull() and o.Shape.Solids:
                solids[o.Label] = row(o.Shape)
    res = dict(model=os.path.basename(model), freecad_version=".".join(str(x) for x in App.Version()[:3]),
               n_solids=len(solids), total_volume=sum(v["volume"] for v in solids.values()), solids=solids)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    finish(True, "%d solids, total volume %.6f mm3 (FreeCAD %s)" % (len(solids), res["total_volume"], res["freecad_version"]))


try:
    main()
except Exception:
    traceback.print_exc()
    finish(False, "exception: " + traceback.format_exc().splitlines()[-1])
