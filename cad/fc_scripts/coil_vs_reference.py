"""FreeCAD 1.0.2 console script: exact coil comparison of a model against a reference STEP (Fusion export / owner STEP).

  freecadcmd cad/fc_scripts/coil_vs_reference.py   environment: FC_CAD_ROOT, FC_MODEL (FCStd), FC_REF (STEP),
         FC_REF_VOLUME (Fusion's own body volume sum, 641.4613 for Coil_L20_fusion.step), FC_OUT (JSON), FC_STATUS

The reference = all solids of REF whose bounding box lies inside the first coil's envelope (x within +-5, y 12.5..18.5,
|z| < 25 mm: coil 1 on +Y), fused.  Reported:
  * volume of the model coil vs the reference (STEP solids, and REF_VOLUME = Fusion's own body volumes if given),
  * two-sided surface deviation: points sampled on every face of one shape (16 x 16 per face, inside the face) ->
    distance to the other shape's boundary; max / 99th percentile, per region (+z end turn, straight legs,
    -z transitions, leads).  Distances are to the nearest face of the other shape (bbox-filtered, exact OCC distance).
"""
import json, math, os, sys, time, traceback

ROOT = os.environ["FC_CAD_ROOT"]
sys.path.insert(0, ROOT)
import FreeCAD as App  # noqa: E402
import Part  # noqa: E402
from motor_templates.segmented_flatwire.runstatus import finish  # noqa: E402
from motor_templates.segmented_flatwire import params_sheet as ps  # noqa: E402

V = App.Vector
K = int(os.environ.get("FC_K", "16"))


def samples(shape):
    pts = []
    for f in shape.Faces:
        u0, u1, v0, v1 = f.ParameterRange
        for i in range(K + 1):
            for j in range(K + 1):
                u, v = u0 + (u1 - u0) * i / K, v0 + (v1 - v0) * j / K
                if f.isPartOfDomain(u, v):
                    pts.append(f.valueAt(u, v))
    return pts


def dist_field(pts, other):
    faces = [(f, f.BoundBox) for f in other.Faces]
    out = []
    for p in pts:
        best = 1e9
        for grow in (0.06, 0.6, 6.0):
            for f, bb in faces:
                if bb.XMin - grow <= p.x <= bb.XMax + grow and bb.YMin - grow <= p.y <= bb.YMax + grow and \
                        bb.ZMin - grow <= p.z <= bb.ZMax + grow:
                    d = f.distToShape(Part.Vertex(p))[0]
                    best = min(best, d)
            if best <= grow:
                break
        out.append(best)
    return out


def region(p, zend):
    if p.z > zend + 1e-6:
        return "end_turn_+z"
    if p.z >= -zend - 1e-6:
        return "legs"
    if abs(p.x) > 1.0 and p.z < -zend - 4.0:
        return "leads"
    return "transitions_-z"


def stats(vals):
    if not vals:
        return None
    s = sorted(vals)
    return dict(n=len(s), max=round(s[-1], 5), p99=round(s[int(0.99 * (len(s) - 1))], 5), mean=round(sum(s) / len(s), 6))


def main():
    t0 = time.time()
    model = os.environ["FC_MODEL"]
    ref = os.environ["FC_REF"]
    out = os.environ["FC_OUT"]
    doc = App.openDocument(model)
    sh = doc.getObject("Params")
    zend = (ps.pval(sh, "motor_length") + ps.pval(sh, "coil_straight_extra")) / 2
    coil = doc.getObject("Coil").Shape.copy()
    params = {n: ps.pval(sh, n) for n in ("motor_length", "insulation_thickness", "wire_width", "wire_height",
                                          "num_wires_per_slot", "wire_beng_r", "coil_bend_axis_dx")}
    App.closeDocument(doc.Name)
    r = Part.Shape(); r.read(ref)
    sel = [s for s in r.Solids if s.BoundBox.XMin > -5 and s.BoundBox.XMax < 5 and s.BoundBox.YMin > 12.5
           and s.BoundBox.YMax < 18.5 and abs(s.BoundBox.ZMin) < 25 and abs(s.BoundBox.ZMax) < 25]
    if not sel:
        return finish(False, "no coil solids found in %s" % ref)
    rsum = sum(s.Volume for s in sel)
    rf = sel[0].multiFuse(sel[1:]) if len(sel) > 1 else sel[0]
    rf = rf.removeSplitter()
    res = dict(model=os.path.basename(model), reference=os.path.basename(ref), params=params,
               reference_solids=len(sel), model_volume=round(coil.Volume, 4),
               reference_step_volume_sum=round(rsum, 4), reference_step_volume_fused=round(rf.Volume, 4))
    rv = os.environ.get("FC_REF_VOLUME")
    if rv:
        res["reference_native_volume"] = float(rv)
        res["dV_vs_native_percent"] = round((coil.Volume - float(rv)) / float(rv) * 100, 4)
    res["dV_vs_step_fused_percent"] = round((coil.Volume - rf.Volume) / rf.Volume * 100, 4)
    m2r = samples(coil)
    d1 = dist_field(m2r, rf)
    r2m = samples(rf)
    d2 = dist_field(r2m, coil)
    regs = {}
    for p, d in list(zip(m2r, d1)) + list(zip(r2m, d2)):
        regs.setdefault(region(p, zend), []).append(d)
    worst = max(list(zip(d1, m2r)) + list(zip(d2, r2m)), key=lambda t: t[0])
    res["deviation_model_to_ref"] = stats(d1)
    res["deviation_ref_to_model"] = stats(d2)
    res["deviation_by_region"] = {k: stats(v) for k, v in sorted(regs.items())}
    res["max_surface_deviation_mm"] = round(max(max(d1), max(d2)), 5)
    res["worst_point"] = [round(worst[1].x, 4), round(worst[1].y, 4), round(worst[1].z, 4)]
    res["seconds"] = round(time.time() - t0, 1)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps(res, indent=1))
    finish(True, "coil max surface deviation %.4f mm, dV %s %%" % (res["max_surface_deviation_mm"],
                                                                   res.get("dV_vs_native_percent", res["dV_vs_step_fused_percent"])))


try:
    main()
except Exception:
    traceback.print_exc()
    finish(False, "exception: " + traceback.format_exc().splitlines()[-1])
