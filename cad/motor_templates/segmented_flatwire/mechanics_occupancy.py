"""Volume-equivalent housing / carrier parts built from an OCCUPANCY table (CIANO28 150_35, template A.7.3).

The four big aluminium parts of the 150 (Rotor_holder1 / 2, Stator_holder, Stator_holder_back) are cast-like pieces with 10-20 Fusion
features each (pockets, ribs, bosses, holes).  Each is described here by rings of the cylindrical grid of its Fusion body:

    ring k : r0 .. r1, z0 .. z1, fill phi          (rows <prefix>_r0_k, _r1_k, _z0_k, _z1_k, _fill_k)

phi = 1: a full ring (annulus Pad).  phi < 1: ONE sector of angular width phi * 360 / n, centred on +Y and turned by <prefix>_rot,
patterned n times (<prefix>_pattern_n; sketch = half profile + Symmetric, angle = phi * 180 deg / n).  The volume of every ring equals
the volume of the Fusion body inside that ring (measured by tools/fc_scripts/occupancy.py), so the part's volume, its envelope, its
mass distribution along z / r and its axial stations match Fusion; the shape inside a ring (pocket outlines, holes) does not.
z rows are relative to the stack (motor_length / 2 + offset), so the part follows motor_length.

Rings are built full rings first, then sectors, each touching what is built (a PartDesign body keeps one solid); all sectors go into
one PolarPattern.

FUSION SHAPES (template A.7.6, 2026-10-19).  The volume-equivalent rings are NOT the owner's parts: a ring with fill < 1 is a set of sectors
with gaps between them (the "12 windows" of the holders, hairline slits of the rotor holders, notches in the rim), none of which exist in the
Fusion design.  When the profile has `profile.fusion_parts` the four aluminium parts are the REAL Fusion bodies instead: the read-only
Fusion STEP of each part (lightening pockets, ribs, screw holes as designed) is imported once, its largest solid is the part, and it is
the BaseFeature of the same PartDesign::Body (same names, same solids, same fit / BOM / STEP / GLB code).  Only the axial position follows
motor_length (a shift of +-(motor_length - ref_motor_length) / 2 at build time); the shape itself is not parametric.  A missing STEP refuses
the build - there is no silent fall-back to the gapped rings.
"""
import hashlib
import math
import os
import Part
import Sketcher
from .sketchkit import Chain, SK, V_AXIS
from .params_sheet import pval, has_param
from .common import P
from .mechanics import plane_sketch, polar, _pad, ORIGIN, V


def ring_table(sh, prefix):
    """Rings of a part: rows <prefix>_r0_k / _r1_k / _z0_k / _z1_k (k = 1, 2, ... until a row is missing) and _fill_k (a full ring has
    no fill row)."""
    g = lambda n: pval(sh, n)
    rows, k = [], 1
    while has_param(sh, "%s_r0_%d" % (prefix, k)):
        rows.append(dict(k=k, r0=g("%s_r0_%d" % (prefix, k)), r1=g("%s_r1_%d" % (prefix, k)), z0=g("%s_z0_%d" % (prefix, k)),
                         z1=g("%s_z1_%d" % (prefix, k)),
                         fill=g("%s_fill_%d" % (prefix, k)) if has_param(sh, "%s_fill_%d" % (prefix, k)) else 1.0))
        k += 1
    return rows


def _touch(a, b):
    """a and b share a face: radial or axial contact with positive overlap of the other extent."""
    eps = 1e-6
    ra = (max(a["r0"], b["r0"]), min(a["r1"], b["r1"]))
    za = (max(min(a["z0"], a["z1"]), min(b["z0"], b["z1"])), min(max(a["z0"], a["z1"]), max(b["z0"], b["z1"])))
    radial = (abs(a["r1"] - b["r0"]) < eps or abs(b["r1"] - a["r0"]) < eps) and za[1] - za[0] > eps
    axial = ((abs(max(a["z0"], a["z1"]) - min(b["z0"], b["z1"])) < eps or abs(max(b["z0"], b["z1"]) - min(a["z0"], a["z1"])) < eps)
             and ra[1] - ra[0] > eps)
    return radial or axial


def _vol(r):
    return r["fill"] * math.pi * (r["r1"] ** 2 - r["r0"] ** 2) * abs(r["z1"] - r["z0"])


def build_order(rings):
    """Largest ring first, then always a ring that touches what is built (a PartDesign body keeps ONE solid)."""
    pending = sorted(rings, key=lambda r: -_vol(r))
    out = [pending.pop(0)]
    while pending:
        for r in pending:
            if any(_touch(r, b) for b in out):
                out.append(r); pending.remove(r)
                break
        else:
            out.append(pending.pop(0))                       # touches nothing: the body check reports it
    return out


def _z_expr(prefix, which, k):
    return P + "%s_%s_%d" % (prefix, which, k)


def sector_sketch(body, name, prefix, k, rot_expr, r0, r1, fill, n):
    sk = plane_sketch(body, name, "XY_Plane", z_expr=P + "%s_z0_%d" % (prefix, k), rot_expr=rot_expr)
    th = math.radians(fill * 180.0 / n)
    a0 = (r0 * math.sin(th), r0 * math.cos(th))
    a1 = (r1 * math.sin(th), r1 * math.cos(th))
    M = lambda p: (-p[0], p[1])
    ch = Chain(sk)
    inner = ch.arc((0, 0), r0, M(a0), a0, ccw=False, joint="C")
    l1 = ch.line(a0, a1, joint="C")
    outer = ch.arc((0, 0), r1, a1, M(a1), ccw=True, joint="C")
    l2 = ch.line(M(a1), M(a0), joint="C")
    ch.close()
    s = SK(sk)
    s.con("Coincident", inner, 3, ORIGIN, 1)
    s.con("Radius", inner, r0, name="ring_r0", expr=P + "%s_r0_%d" % (prefix, k))
    s.con("Coincident", outer, 3, ORIGIN, 1)
    s.con("Radius", outer, r1, name="ring_r1", expr=P + "%s_r1_%d" % (prefix, k))
    ax = s.cline((0, 0), a1)                                     # the right side line lies on a construction ray from the axis
    s.con("Coincident", ax, 1, ORIGIN, 1)
    s.con("PointOnObject", l1, 1, ax)
    s.con("PointOnObject", l1, 2, ax)
    s.con("PointOnObject", ax, 2, outer)                          # the ray ends on the outer arc
    s.con("Angle", ax, V_AXIS, th, name="sector_half_angle", expr="%s%s_fill_%d * 180 deg / %s%s_pattern_n" % (P, prefix, k, P, prefix))
    ax2 = s.cline((0, 0), M(a1))                                 # the left side line lies on the mirrored ray
    s.con("Coincident", ax2, 1, ORIGIN, 1)
    s.con("Symmetric", ax, 2, ax2, 2, V_AXIS)
    s.con("PointOnObject", l2, 1, ax2)
    s.con("PointOnObject", l2, 2, ax2)
    return sk


def ring_sketch(body, name, prefix, k, r0, r1):
    sk = plane_sketch(body, name, "XY_Plane", z_expr=P + "%s_z0_%d" % (prefix, k))
    s = SK(sk)
    c1 = sk.addGeometry(Part.Circle(V(0, 0, 0), V(0, 0, 1), r1), False)
    c0 = sk.addGeometry(Part.Circle(V(0, 0, 0), V(0, 0, 1), r0), False)
    s.con("Coincident", c1, 3, ORIGIN, 1)
    s.con("Coincident", c0, 3, ORIGIN, 1)
    s.con("Radius", c1, r1, name="ring_r1", expr=P + "%s_r1_%d" % (prefix, k))
    s.con("Radius", c0, r0, name="ring_r0", expr=P + "%s_r0_%d" % (prefix, k))
    return sk


FUSION_SUFFIX = "_Fusion"


def find_fusion_part(file_name, subdir):
    """Fusion STEP of a part: $FC_FUSION_PARTS/<file>, cad/reference/fusion_parts/<subdir>/<file> or (owner repo)
    fusion_source/<subdir>/mechanics/<file>, relative to the template's grand-parent directory (cad/)."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cands = []
    if os.environ.get("FC_FUSION_PARTS"):
        cands.append(os.path.join(os.environ["FC_FUSION_PARTS"], file_name))
    cands += [os.path.join(root, "reference", "fusion_parts", subdir, file_name),
              os.path.join(root, "fusion_source", subdir, "mechanics", file_name)]
    for c in cands:
        if os.path.isfile(c):
            return c, cands
    return None, cands


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def import_fusion_part(doc, body, bn, file_name, cfg, shift_z, parent=None):
    """The Fusion body of part `bn` as the BaseFeature of `body` (see the module docstring).  Returns the report."""
    path, cands = find_fusion_part(file_name, cfg.get("dir", ""))
    if path is None:
        raise RuntimeError("Fusion STEP %s of %s not found; looked in: %s.  The CIANO28 150 holders are the real Fusion bodies (no gapped "
                           "volume-equivalent rings): put the file there or set $FC_FUSION_PARTS" % (file_name, bn, " | ".join(cands)))
    shape = Part.read(path)
    sol = max(shape.Solids, key=lambda x: x.Volume)             # the Stator_holder STEP also carries its 12 screws: the holder is the biggest solid
    valid, check = sol.isValid(), ""
    if not valid:                                               # Fusion's own B-rep: 12 pocket faces + 2 big faces of each stator holder trip BRepCheck
        try:                                                    # (tolerance-level 'self-intersecting / intersecting wires'); volume, booleans and meshing are fine
            sol.check(True)
        except Exception as e:
            check = str(e)[:200]
    if not sol.Shells or not all(x.isClosed() for x in sol.Shells):
        raise RuntimeError("Fusion STEP %s: the part is not a closed solid" % file_name)
    if abs(shift_z) > 1e-12:                                    # translated(): a moved location, the geometry (and its validity) is not copied
        import FreeCAD as App
        sol = sol.translated(App.Vector(0, 0, shift_z))
    f = doc.addObject("Part::Feature", bn + FUSION_SUFFIX)
    f.Label = bn + FUSION_SUFFIX
    f.Shape = sol
    for prop, typ, val, tip in (
            ("SourceFile", "App::PropertyString", file_name, "Fusion STEP the shape was read from (imported once, not parametric)"),
            ("SourceSHA256", "App::PropertyString", _sha256(path), "SHA-256 of that STEP"),
            ("SourceVolume", "App::PropertyFloat", sol.Volume, "volume (mm3) of the imported solid"),
            ("FusionBodyVolume", "App::PropertyFloat", float((cfg.get("volumes_mm3") or {}).get(bn, 0.0)),
             "volume (mm3) of the Fusion body read from the design (0 = not recorded); the gate compares it with the solid"),
            ("SourceValid", "App::PropertyBool", valid, "BRepCheck verdict of the imported solid; False = Fusion's B-rep carries tolerance-level wire errors (see SourceCheck), the gate accepts exactly that"),
            ("SourceCheck", "App::PropertyString", check, "message of Shape.check() when the imported solid is not valid"),
            ("SourceSolids", "App::PropertyInteger", len(shape.Solids), "solids in the STEP (the part is the biggest)"),
            ("AxialShift", "App::PropertyFloat", float(shift_z), "z shift (mm) applied for motor_length != ref_motor_length")):
        f.addProperty(typ, prop, "Motor", tip)
        setattr(f, prop, val)
    if parent is not None:                                       # the BaseFeature must live in the same App::Part as the body
        parent.addObject(f)
    body.BaseFeature = f
    doc.recompute()
    return dict(step=file_name, sha256=f.SourceSHA256, volume=round(sol.Volume, 3), solids_in_step=len(shape.Solids), faces=len(sol.Faces),
                valid=valid, check=check, shift_z=shift_z)


def build_occupancy_part(doc, sh, body, prefix):
    g = lambda n: pval(sh, n)
    rings = ring_table(sh, prefix)
    n = int(round(g(prefix + "_pattern_n")))
    rot = (P + prefix + "_rot") if has_param(sh, prefix + "_rot") else None
    sketches, feats, sect_pads = [], [], []
    for r in build_order(rings):
        k = r["k"]
        full = r["fill"] >= 0.999
        name = "%s_%s%d" % (prefix, "Ring" if full else "Sector", k)
        if full:
            sk = ring_sketch(body, name + "Sketch", prefix, k, r["r0"], r["r1"])
        else:
            sk = sector_sketch(body, name + "Sketch", prefix, k, rot, r["r0"], r["r1"], r["fill"], n)
        doc.recompute()
        pad = _pad(body, sk, name, "%s%s_z1_%d - %s%s_z0_%d" % (P, prefix, k, P, prefix, k))
        body.Tip = pad
        doc.recompute()
        import os
        if os.environ.get("OCC_DEBUG"):
            sol = body.Shape.Solids
            print("OCC", prefix, name, "solids", len(sol), "vol", round(body.Shape.Volume, 1), "state", list(pad.State))
        sketches.append(sk); feats.append(pad)
        if not full:
            sect_pads.append((sk, pad))
    if sect_pads:
        pp = polar(body, [p for _, p in sect_pads], sect_pads[0][0], P + prefix + "_pattern_n", prefix + "_Pattern")
        pp.Refine = True
        body.Tip = pp
        feats.append(pp)
    doc.recompute()
    return dict(sketches=sketches, features=feats)


# (row prefix, body, array label): the 91290A137 holder bolts (12), the M3 x 30 and M3 x 6 screws of the stator (6 each)
SCREW_FAMILIES = (("screwA", "Screw", "Screws"), ("screwB", "Screw_M3x30", "Screws_M3x30"), ("screwC", "Screw_M3x6", "Screws_M3x6"))
PART_BODIES = (("Shaft", "rotor_holder1"), ("Shaft_rear", "rotor_holder2"), ("Stator_holder", "stator_holder"),
               ("Stator_holder_back", "stator_holder_back"))


def build_mechanics_occupancy(doc, sh, polar_array, fusion_parts=None):
    """Mechanics of the CIANO28 150: four aluminium parts + the 61811-2RZ bearing (+ screws, see build_screws_occupancy).  The four parts
    are the Fusion STEP bodies when `fusion_parts` (profile.fusion_parts) is given, else volume-equivalent occupancy rings."""
    import FreeCAD as App
    from . import mechanics as M
    mech = doc.addObject("App::Part", "Mechanics"); mech.Label = "Mechanics"
    rep = {}

    def body(name):
        b = doc.addObject("PartDesign::Body", name); b.Label = name
        return b

    for bn, prefix in PART_BODIES:
        b = body(bn); mech.addObject(b)
        if fusion_parts:
            front = bn in ("Shaft", "Stator_holder")                     # z > 0 side: moves with +motor_length / 2
            shift = (pval(sh, "motor_length") - float(fusion_parts["ref_motor_length"])) / 2.0 * (1 if front else -1)
            rep[bn] = dict(sketches=[], features=[], fusion=import_fusion_part(doc, b, bn, fusion_parts["files"][bn], fusion_parts, shift, mech))
        else:
            rep[bn] = build_occupancy_part(doc, sh, b, prefix)
    brg = doc.addObject("App::Part", "Bearing_618_8"); brg.Label = "Bearing_618_8"; mech.addObject(brg)       # label kept: bom_dump / BOM find the back copy by it
    o, i, ba = body("Bearing_outer_ring"), body("Bearing_inner_ring"), body("Bearing_ball")
    for x in (o, i, ba):
        brg.addObject(x)
    rep["Bearing"] = M.build_bearing(doc, sh, o, i, ba)
    if has_param(sh, "bearing_seal_ri"):
        sl = body("Bearing_seal"); brg.addObject(sl)
        rep["Bearing_seal"] = M.build_bearing_seal(doc, sh, sl)
        lk = doc.addObject("App::Link", "Bearing_seal_2"); lk.Label = "Bearing_seal_2"; lk.LinkedObject = sl
        lk.Placement = App.Placement(V(0, 0, M.g_B(sh)), App.Rotation(V(1, 0, 0), 180))
        lk.setExpression(".Placement.Base.z", P + "bearing_B")
        brg.addObject(lk)
    brg.setExpression(".Placement.Base.z", P + "holder_face_z")
    arrays = []
    for fam, body_name, label in SCREW_FAMILIES:                  # three socket-head screw families (Fusion occurrences)
        if has_param(sh, fam + "_bolt_r"):
            sc = body(body_name); mech.addObject(sc)
            rep[body_name] = M.build_screw(doc, sh, sc, pre=fam)
            arrays.append((sc, fam, label))
    doc.recompute()
    balls = polar_array(doc, ba, P + "bearing_ball_count", "Balls"); brg.addObject(balls)
    for sc, fam, label in arrays:
        mech.addObject(polar_array(doc, sc, P + fam + "_count", label))
    M._link(doc, brg, "Bearing_618_8_back", P + "bearing_back_z0", mech)
    doc.recompute()
    return rep
