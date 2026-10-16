"""FreeCAD console script: every mechanical part of a model against the Fusion reference (stage 3 verification).

  freecadcmd cad/fc_scripts/mechanics_vs_reference.py   environment: FC_CAD_ROOT, FC_MODEL (FCStd), FC_REF_DIR
         (cad/reference/fusion_mechanics: <component>.step exported read-only from Fusion in component coordinates +
         <component>.json with the Fusion body volumes and the occurrence matrices), FC_OUT (JSON), FC_STATUS, FC_K (samples)

Per part: volume (model, Fusion body volume, STEP solid volume), bounding box, the symmetric-difference volume
vol(A) + vol(B) - 2 vol(A & B) (0 = the same solid) and the two-sided surface deviation (points on every face of one
solid -> distance to the other solid's boundary).  Fusion occurrences (618_8:1/2, the six screws) are placed
with their Fusion matrices.
"""
import json, math, os, sys, time, traceback

ROOT = os.environ["FC_CAD_ROOT"]
sys.path.insert(0, ROOT)
import FreeCAD as App  # noqa: E402
import Part  # noqa: E402
import Import  # noqa: E402
from motor_templates.segmented_flatwire.runstatus import finish  # noqa: E402
from motor_templates.segmented_flatwire import mechanics  # noqa: E402

K = int(os.environ.get("FC_K", "8"))


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


def max_dev(a, b):
    """max over sampled points of a of the distance to b's boundary"""
    faces = [(f, f.BoundBox) for f in b.Faces]
    worst = 0.0
    for p in samples(a):
        best = 1e9
        for grow in (0.05, 0.5, 5.0):
            for f, bb in faces:
                if bb.XMin - grow <= p.x <= bb.XMax + grow and bb.YMin - grow <= p.y <= bb.YMax + grow and \
                        bb.ZMin - grow <= p.z <= bb.ZMax + grow:
                    best = min(best, f.distToShape(Part.Vertex(p))[0])
            if best <= grow:
                break
        worst = max(worst, best)
    return worst


def matrix(m):
    M = App.Matrix(*m[:16])
    return M


def ref_solids(ref_dir, comp):
    doc = App.newDocument("ref_" + comp)
    Import.insert(os.path.join(ref_dir, comp + ".step"), doc.Name)
    sols = [o.Shape.copy() for o in doc.Objects if o.TypeId == "Part::Feature" and not o.Shape.isNull() and o.Shape.Solids]
    App.closeDocument(doc.Name)
    meta = json.load(open(os.path.join(ref_dir, comp + ".json"), encoding="utf-8"))
    return sols, meta


def placed(shape, m):
    s = shape.copy()
    s.transformShape(matrix(m), True)
    return s


def bb(s):
    b = s.BoundBox
    return [round(x, 5) for x in (b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax)]


def compare(name, mine, ref, fusion_volume):
    t0 = time.time()
    # symmetric difference by a boolean; OCC booleans of near-identical solids (all faces coincident) sometimes return a
    # partial result, so it is reported only when the common part is consistent with both volumes - the two-sided surface
    # deviation below is the primary measure
    common = mine.common(ref, 1e-5)
    cv = 0.0 if common.isNull() else common.Volume
    sym = mine.Volume + ref.Volume - 2 * cv
    if sym > abs(mine.Volume - ref.Volume) + 0.05 * min(mine.Volume, ref.Volume):
        sym = None                                   # boolean failed (partial common)
    dev = max(max_dev(mine, ref), max_dev(ref, mine))
    return dict(part=name, volume_model=round(mine.Volume, 4), volume_fusion=round(fusion_volume, 4),
                volume_step=round(ref.Volume, 4), dV_rel_vs_fusion=round((mine.Volume - fusion_volume) / fusion_volume, 6),
                symmetric_difference_mm3=None if sym is None else round(sym, 5), max_surface_deviation_mm=round(dev, 5),
                bbox_model=bb(mine), bbox_fusion=bb(ref),
                bbox_max_diff_mm=round(max(abs(p - q) for p, q in zip(bb(mine), bb(ref))), 5), seconds=round(time.time() - t0, 1))


def main():
    model, ref_dir, out = os.environ["FC_MODEL"], os.environ["FC_REF_DIR"], os.environ["FC_OUT"]
    doc = App.openDocument(model)
    doc.recompute()
    my = mechanics.solids(doc)
    rows = []
    # single-occurrence parts (component frame = motor frame in Fusion)
    for comp, mine in (("Shaft", "Shaft"), ("Stator_holder", "Stator_holder"), ("Stator_holder_back", "Stator_holder_back"),
                       ("Prop_fix", "Prop_fix")):
        sols, meta = ref_solids(ref_dir, comp)
        m = meta["occurrences"][0]["matrix"]
        fv = meta["bodies"][0]["volume_mm3"]
        body = min(sols, key=lambda x: abs(x.Volume - fv))          # the Stator_holder STEP also carries its six screws
        rows.append(compare(mine, my[mine], placed(body, m), fv))
    sols, meta = ref_solids(ref_dir, "618_8")
    vols = {b["body"]: b["volume_mm3"] for b in meta["bodies"]}
    by_vol = sorted(sols, key=lambda s: -s.Volume)                    # outer ring, inner ring, balls
    names = ("outer_ring", "inner_ring", "balls")
    fus = sorted(vols.values(), reverse=True)
    for occ, tag in zip(meta["occurrences"], ("front", "back")):
        z = occ["matrix"][11]
        side = "front" if z > 0 else "back"
        for s, n, fv in zip(by_vol, names, fus):
            if n == "balls":
                mine = Part.makeCompound([v for k, v in my.items() if k.startswith("Bearing_618_8_%s_ball_" % side)])
            else:
                mine = my["Bearing_618_8_%s_%s" % (side, n)]
            rows.append(compare("Bearing_618_8_%s_%s" % (side, n), mine, placed(s, occ["matrix"]), fv))
    sols, meta = ref_solids(ref_dir, "Screw_91292A032")
    fv = meta["bodies"][0]["volume_mm3"]
    myscrews = [(k, v) for k, v in my.items() if k.startswith("Screw_")]
    for occ in meta["occurrences"]:
        r = placed(sols[0], occ["matrix"])
        c = r.BoundBox.Center
        k, v = min(myscrews, key=lambda kv: (kv[1].BoundBox.Center - c).Length)
        rows.append(compare(k, v, r, fv))
    res = dict(model=os.path.basename(model), reference=ref_dir, K=K, parts=rows,
               worst_dV_rel=max(abs(r["dV_rel_vs_fusion"]) for r in rows),
               worst_surface_deviation_mm=max(r["max_surface_deviation_mm"] for r in rows))
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    for r in rows:
        print("%-34s dV %+.4f %%  symdiff %s mm3  dev %.4f mm  dbbox %.4f mm" % (
            r["part"], 100 * r["dV_rel_vs_fusion"], r["symmetric_difference_mm3"], r["max_surface_deviation_mm"], r["bbox_max_diff_mm"]))
    finish(True, "%d parts compared, worst |dV| %.4f %%, worst deviation %.4f mm" % (
        len(rows), 100 * res["worst_dV_rel"], res["worst_surface_deviation_mm"]))


try:
    main()
except Exception:
    traceback.print_exc()
    finish(False, "exception: " + traceback.format_exc().splitlines()[-1])
