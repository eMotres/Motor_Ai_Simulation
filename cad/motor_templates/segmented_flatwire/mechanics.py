"""Mechanical parts of the CIANO14 40 motor, built like the Fusion model (stage 3, read-only Fusion dump 2026-10-12).

Fusion builds every turned part from ONE axial sketch (CIANO14 40/Sketch3, YZ plane).  A FreeCAD PartDesign body owns its
sketches, so each body here has its own half-section sketch; all of them are dimensioned from the SAME named Params rows
(the Sketch3 dimension names + named derived rows of the axial chain: holder_face_z, bearing_out_z, ...), so one Params
edit moves every part together, as in Fusion.

  Shaft                 revolve of the Sketch3 profile 5 (journals, rotor seat, flange, shoulder, thread, tip, bore with the
                        45 deg tip) with the three Chamfer7 chamfers in the sketch; drive flats (Sketch10 / Extrude15)
  Bearing_618_8         outer ring, inner ring (revolves with the edge radii and the ball groove) + Balls (array of one ball)
                        - the Fusion 618_8 import rebuilt from the bearing card (d, D, B) and the import's own sizes;
                        front copy = the Part, back copy = App::Link (Fusion occurrences 618_8:1 / 618_8:2)
  Stator_holder         revolve of Sketch3 profile 0, window between the spokes (Sketch2 profile 16), boss to the stator face
                        and seat into the pocket between the core legs (Sketch2 profiles 9/10/11), clearance holes; x6;
                        Chamfer23 on the seat edges along the core legs
  Stator_holder_back    revolve of Sketch3 profile 6, the same window (through the whole part), bosses / seats with the M2
                        tap holes, the six M3 holes of the A_H face (Sketch7), Fillet27 at the spoke ends
  Prop_fix              double-D hub on the flats + plate (Sketch2, Extrude1/2), hole faces offset by the clearance
                        (OffsetFaces1 modelled in the sketch)
  Screw / Screws        M2 socket head screw (McMaster 91292A032 in Fusion) + polar array, one per sector boundary

Owner rules (2026-10-12): symmetric profiles = one half dimensioned + Symmetric constraints; one radius / chamfer = one
dimension + Equal; computed lengths and counts = named derived rows; every sketch fully constrained, no redundant
constraints (health gate).  Frame: Z = motor axis, +Z = propeller side, first coil / magnet / window centred on +Y.
"""
import math
import FreeCAD as App
import Part
import Sketcher
from .sketchkit import Chain, SK, V_AXIS, H_AXIS
from .params_sheet import pval, has_param
from .common import P, origin_feature, pad_symmetric

V = App.Vector
ORIGIN = -1          # root point geo id (pos 1)

# --------------------------------------------------------------------------------------------- sketch helpers


def plane_sketch(body, name, plane="XY_Plane", z_expr=None, rot_expr=None):
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = [(origin_feature(body, plane), "")]
    sk.MapMode = "FlatFace"
    sk.AttachmentOffset = App.Placement(V(0, 0, 0), App.Rotation(V(0, 0, 1), 0))
    if z_expr:
        sk.setExpression(".AttachmentOffset.Base.z", z_expr)
    if rot_expr:
        sk.setExpression(".AttachmentOffset.Rotation.Angle", rot_expr)
    return sk


class Poly:
    """Closed polyline of a half section (x = radius, y = z) with named dimensions.

    pts: list of (x, y) vertices in loop order.  Segment i runs pts[i] -> pts[i+1] (the last closes the loop).
    spec[i]: ("x", expr, name) vertical segment at x = expr (DistanceX from the origin), ("y", expr, name) horizontal at
    y = expr, ("axis",) vertical on the V axis, ("free",) no own constraint (chamfer / cone: constrained by the caller),
    ("xon", seg) vertical, collinear with segment seg (PointOnObject), ("yrel", j, expr, name) horizontal, DistanceY
    from the start of segment j to this segment's start = expr, ("xrel", j, expr, name) vertical, DistanceX likewise.
    """

    def __init__(self, sk, pts, spec):
        self.sk, self.s = sk, SK(sk)
        n = len(pts)
        self.g = [sk.addGeometry(Part.LineSegment(V(*pts[i], 0), V(*pts[(i + 1) % n], 0)), False) for i in range(n)]
        for i in range(n):
            sk.addConstraint(Sketcher.Constraint("Coincident", self.g[i], 2, self.g[(i + 1) % n], 1))
        for i, sp in enumerate(spec):
            k, gi = sp[0], self.g[i]
            if k == "x":
                self.s.con("Vertical", gi)
                self.s.con("DistanceX", ORIGIN, 1, gi, 1, pts[i][0], name=sp[2], expr=sp[1])
            elif k == "y":
                self.s.con("Horizontal", gi)
                self.s.con("DistanceY", ORIGIN, 1, gi, 1, pts[i][1], name=sp[2], expr=sp[1])
            elif k == "axis":
                self.s.con("Vertical", gi)
                self.s.con("PointOnObject", gi, 1, V_AXIS)
            elif k == "haxis":
                self.s.con("Horizontal", gi)
                self.s.con("PointOnObject", gi, 1, H_AXIS)
            elif k == "xon":
                self.s.con("Vertical", gi)
                self.s.con("PointOnObject", gi, 1, self.g[sp[1]])
            elif k == "yrel":
                self.s.con("Horizontal", gi)
                self.s.con("DistanceY", self.g[sp[1]], 1, gi, 1, pts[i][1] - pts[sp[1]][1], name=sp[3], expr=sp[2])
            elif k == "xrel":
                self.s.con("Vertical", gi)
                self.s.con("DistanceX", self.g[sp[1]], 1, gi, 1, pts[i][0] - pts[sp[1]][0], name=sp[3], expr=sp[2])
            elif k == "free":
                pass
            else:
                raise ValueError(sp)

    def chamfer45(self, i, first=None, name=None, expr=None):
        """Segment i is a 45 deg chamfer: the first one carries the size (DistanceY between its ends), the others are Equal."""
        gi = self.g[i]
        a, b = self.sk.Geometry[gi].StartPoint, self.sk.Geometry[gi].EndPoint
        ang = math.atan2(b.y - a.y, b.x - a.x)
        self.s.con("Angle", gi, ang)
        if first is None:
            self.s.con("DistanceY", gi, 1, gi, 2, b.y - a.y, name=name, expr=expr)
        else:
            self.s.con("Equal", self.g[first], gi)


def rounded_corner(sk, s, l1, l2, r, name=None, expr=None, equal_to=None):
    """Fillet arc between the end of line l1 and the start of line l2 (axis-parallel lines meeting at a corner).
    Replaces the corner: shortens both lines by r, adds a tangent arc.  Returns the arc geo id."""
    p1a, p1b = sk.Geometry[l1].StartPoint, sk.Geometry[l1].EndPoint
    p2a, p2b = sk.Geometry[l2].StartPoint, sk.Geometry[l2].EndPoint
    corner = p1b
    d1 = (p1a - p1b); d1.normalize()
    d2 = (p2b - p2a); d2.normalize()
    t1 = corner + d1 * r
    t2 = corner + d2 * r
    c = corner + d1 * r + d2 * r
    # remove the coincidence at the corner (it is the last constraint between l1 end and l2 start)
    for i, cs in enumerate(sk.Constraints):
        if cs.Type == "Coincident" and {(cs.First, cs.FirstPos), (cs.Second, cs.SecondPos)} == {(l1, 2), (l2, 1)}:
            sk.delConstraint(i)
            break
    move = getattr(sk, "movePoint", None) or getattr(sk, "moveGeometry")     # FreeCAD 1.0 / 1.1 name
    move(l1, 2, t1)
    move(l2, 1, t2)
    a1 = math.atan2(t1.y - c.y, t1.x - c.x)
    a2 = math.atan2(t2.y - c.y, t2.x - c.x)
    # the arc goes the short way between the two tangent points
    da = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
    lo, hi = (a1, a1 + da) if da > 0 else (a1 + da, a1)
    arc = sk.addGeometry(Part.ArcOfCircle(Part.Circle(c, V(0, 0, 1), r), lo, hi), False)
    ps, pe = (1, 2) if da > 0 else (2, 1)
    sk.addConstraint(Sketcher.Constraint("Tangent", l1, 2, arc, ps))
    sk.addConstraint(Sketcher.Constraint("Tangent", arc, pe, l2, 1))
    if equal_to is None:
        s.con("Radius", arc, r, name=name, expr=expr)
    else:
        s.con("Equal", equal_to, arc)
    return arc


def revolve(body, sk, name, axis="V_Axis"):
    rv = body.newObject("PartDesign::Revolution", name)
    rv.Profile = sk
    rv.ReferenceAxis = (sk, [axis])
    rv.Angle = 360
    sk.Visibility = False
    return rv


def polar(body, feats, axis_sketch, count_expr, name):
    pp = body.newObject("PartDesign::PolarPattern", name)
    pp.Originals = feats
    pp.Axis = (axis_sketch, ["N_Axis"])
    pp.Angle = 360
    pp.setExpression("Occurrences", count_expr)
    return pp


def _g(sh):
    return lambda n: pval(sh, n)


# --------------------------------------------------------------------------------------------- shaft


def build_shaft(doc, sh, body):
    g = _g(sh)
    rb, rj, rs, rsh = g("shaft_bore_r"), g("shaft_journal_r"), g("shaft_seat_r"), g("shaft_shoulder_r")
    rf = rs + g("shaft_flange_step")
    rt, rtip, ch = g("shaft_thread_r"), g("shaft_tip_r"), g("shaft_chamfer")
    zb, zf = g("bearing_out_z"), g("holder_face_z")
    zr = g("rotor_w") / 2
    zsh = zf - g("shaft_shoulder_len")
    zj, zt, ze = g("shaft_journal_end_z"), g("shaft_thread_end_z"), g("shaft_front_end_z")
    zbore = zj - rb / math.tan(math.radians(g("shaft_bore_tip_angle")))
    sk = plane_sketch(body, "ShaftSketch", "XZ_Plane")
    pts = [(rb, -zb), (rj - ch, -zb), (rj, -zb + ch), (rj, -zf), (rs - ch, -zf), (rs, -zf + ch), (rs, zr), (rf, zr),
           (rf, zsh), (rsh, zsh), (rsh, zf), (rj, zf), (rj, zj - ch), (rj - ch, zj), (rt, zj), (rt, zt), (rtip, zt),
           (rtip, ze), (0, ze), (0, zj), (rb, zbore)]
    spec = [("y", "-" + P + "bearing_out_z", "shaft_back_end_z"),          # 0  back end face
            ("free",),                                                      # 1  chamfer
            ("x", P + "shaft_journal_r", "shaft_journal_r"),                # 2  back journal
            ("y", "-" + P + "holder_face_z", "back_journal_step_z"),        # 3  10 -> 8 step = back holder face
            ("free",),                                                      # 4  chamfer
            ("x", P + "shaft_seat_r", "shaft_seat_r"),                      # 5  rotor seat
            ("y", P + "rotor_w / 2", "rotor_face_z"),                       # 6  front rotor face
            ("xrel", 5, P + "shaft_flange_step", "shaft_flange_step"),      # 7  flange (Fusion d11885: 3 mm from the seat)
            ("yrel", 10, "-" + P + "shaft_shoulder_len", "shaft_shoulder_len"),  # 8  flange front face (0.5 below the shoulder face)
            ("x", P + "shaft_shoulder_r", "shaft_shoulder_r"),              # 9  shoulder
            ("y", P + "holder_face_z", "holder_face_z"),                    # 10 shoulder face = front bearing inner face
            ("xon", 2),                                                     # 11 front journal, collinear with the back one
            ("free",),                                                      # 12 chamfer
            ("y", P + "shaft_journal_end_z", "shaft_journal_end_z"),        # 13 journal end (Prop_fix seat)
            ("x", P + "shaft_thread_r", "shaft_thread_r"),                  # 14 thread section
            ("y", P + "shaft_thread_end_z", "shaft_thread_end_z"),          # 15
            ("x", P + "shaft_tip_r", "shaft_tip_r"),                        # 16 tip
            ("y", P + "shaft_front_end_z", "shaft_front_end_z"),            # 17 front end
            ("axis",),                                                      # 18 on the axis
            ("free",),                                                      # 19 bore tip cone
            ("x", P + "shaft_bore_r", "shaft_bore_r")]                      # 20 bore
    # spec 8 measures from the start of segment 10 (shoulder face) to the start of segment 8 -> negative
    pl = Poly(sk, pts, spec)
    s = pl.s
    pl.chamfer45(1, name="shaft_chamfer", expr=P + "shaft_chamfer")
    pl.chamfer45(4, first=1)
    pl.chamfer45(12, first=1)
    s.con("PointOnObject", pl.g[19], 1, pl.g[13])                           # cone apex on the journal end plane (Fusion P32 on L17)
    s.con("Angle", pl.g[18], pl.g[19], math.radians(g("shaft_bore_tip_angle")),
          name="shaft_bore_tip_angle", expr=P + "shaft_bore_tip_angle")
    doc.recompute()
    rv = revolve(body, sk, "ShaftRevolve")
    # drive flats: two circular segments of the journal outside |x| < shaft_flat_w / 2, pocket shaft_flat_len
    fk = plane_sketch(body, "ShaftFlatsSketch", "XY_Plane", z_expr=P + "shaft_journal_end_z")
    fx = g("shaft_flat_w") / 2
    fy = math.sqrt(rj * rj - fx * fx)
    s2 = SK(fk)
    lr = fk.addGeometry(Part.LineSegment(V(fx, -fy, 0), V(fx, fy, 0)), False)
    ar = fk.addGeometry(Part.ArcOfCircle(Part.Circle(V(0, 0, 0), V(0, 0, 1), rj), -math.atan2(fy, fx), math.atan2(fy, fx)), False)
    ll = fk.addGeometry(Part.LineSegment(V(-fx, fy, 0), V(-fx, -fy, 0)), False)
    al = fk.addGeometry(Part.ArcOfCircle(Part.Circle(V(0, 0, 0), V(0, 0, 1), rj), math.pi - math.atan2(fy, fx),
                                         math.pi + math.atan2(fy, fx)), False)
    s2.con("Coincident", lr, 2, ar, 2); s2.con("Coincident", ar, 1, lr, 1)
    s2.con("Coincident", ll, 2, al, 2); s2.con("Coincident", al, 1, ll, 1)
    s2.con("Coincident", ar, 3, ORIGIN, 1)
    s2.con("Radius", ar, rj, name="shaft_journal_r", expr=P + "shaft_journal_r")
    s2.con("Vertical", lr)
    s2.con("DistanceX", ORIGIN, 1, lr, 1, fx, name="shaft_flat_half_w", expr=P + "shaft_flat_w / 2")
    s2.con("Symmetric", lr, 1, ll, 2, V_AXIS)
    s2.con("Symmetric", lr, 2, ll, 1, V_AXIS)
    s2.con("Equal", ar, al)
    doc.recompute()
    pk = body.newObject("PartDesign::Pocket", "ShaftFlats")
    pk.Profile = fk
    pk.setExpression("Length", P + "shaft_flat_len")
    pk.Refine = True
    fk.Visibility = False
    body.Tip = pk
    return dict(sketches=[sk, fk], features=[rv, pk])


# --------------------------------------------------------------------------------------------- bearing 618/8


def build_bearing(doc, sh, outer, inner, ball):
    """Bodies in bearing-local coordinates: axis Z, faces z = 0 and z = bearing_B (the Part / Link places them)."""
    g = _g(sh)
    d2, D2, B = g("bearing_d") / 2, g("bearing_D") / 2, g("bearing_B")
    lo, li = g("bearing_outer_land_d") / 2, g("bearing_inner_land_d") / 2
    pr, rb, e = g("bearing_pitch_r"), g("bearing_ball_d") / 2, g("bearing_edge_r")
    dz = math.sqrt(max(rb * rb - (lo - pr) ** 2, 1e-9))
    dzi = math.sqrt(max(rb * rb - (pr - li) ** 2, 1e-9))
    out = {}

    def groove(sk, s, p_from, p_to, ccw, name_r):
        """ball groove arc between two points on a land line, centre (pitch_r, B/2), radius ball_d/2"""
        a1 = math.atan2(p_from[1] - B / 2, p_from[0] - pr)
        a2 = math.atan2(p_to[1] - B / 2, p_to[0] - pr)
        lo_, hi_ = (a1, a2) if ccw else (a2, a1)
        while hi_ <= lo_:
            hi_ += 2 * math.pi
        arc = sk.addGeometry(Part.ArcOfCircle(Part.Circle(V(pr, B / 2, 0), V(0, 0, 1), rb), lo_, hi_), False)
        s.con("DistanceX", ORIGIN, 1, arc, 3, pr, name="bearing_pitch_r", expr=P + "bearing_pitch_r")
        s.con("DistanceY", ORIGIN, 1, arc, 3, B / 2, name="groove_centre_z", expr=P + "bearing_B / 2")
        s.con("Radius", arc, rb, name=name_r, expr=P + "bearing_ball_d / 2")
        return arc, (1, 2) if ccw else (2, 1)

    # outer ring: land (lo) with the groove, OD corners rounded (bearing_edge_r)
    sk = plane_sketch(outer, "OuterRingSketch", "XZ_Plane")
    s = SK(sk)
    pts = [(lo, 0), (D2, 0), (D2, B), (lo, B), (lo, B / 2 + dz), (lo, B / 2 - dz)]
    gl = [sk.addGeometry(Part.LineSegment(V(*pts[i], 0), V(*pts[i + 1], 0)), False) for i in range(4)]
    lb = sk.addGeometry(Part.LineSegment(V(*pts[5], 0), V(*pts[0], 0)), False)
    for i in range(3):
        sk.addConstraint(Sketcher.Constraint("Coincident", gl[i], 2, gl[i + 1], 1))
    arc, (ps, pe) = groove(sk, s, pts[4], pts[5], ccw=False, name_r="ball_groove_r")
    sk.addConstraint(Sketcher.Constraint("Coincident", gl[3], 2, arc, ps))
    sk.addConstraint(Sketcher.Constraint("Coincident", arc, pe, lb, 1))
    sk.addConstraint(Sketcher.Constraint("Coincident", lb, 2, gl[0], 1))
    s.con("Horizontal", gl[0]); s.con("PointOnObject", gl[0], 1, H_AXIS)
    s.con("Vertical", gl[1]); s.con("DistanceX", ORIGIN, 1, gl[1], 1, D2, name="bearing_D_r", expr=P + "bearing_D / 2")
    s.con("Horizontal", gl[2]); s.con("DistanceY", ORIGIN, 1, gl[2], 1, B, name="bearing_B", expr=P + "bearing_B")
    s.con("Vertical", gl[3]); s.con("DistanceX", ORIGIN, 1, gl[3], 1, lo, name="outer_land_r", expr=P + "bearing_outer_land_d / 2")
    s.con("Vertical", lb); s.con("PointOnObject", lb, 1, gl[3])
    f1 = rounded_corner(sk, s, gl[0], gl[1], e, name="bearing_edge_r", expr=P + "bearing_edge_r")
    rounded_corner(sk, s, gl[1], gl[2], e, equal_to=f1)
    doc.recompute()
    out["outer"] = (sk, revolve(outer, sk, "OuterRingRevolve"))
    outer.Tip = out["outer"][1]
    seal_sketches, seal_feats = [], []
    if has_param(sh, "bearing_seal_notch_r"):                    # 2RS1: seal notch + lip groove + cone at both ends
        for top in (False, True):
            gk, gf = _seal_groove(doc, sh, outer, top)
            seal_sketches.append(gk); seal_feats.append(gf)

    # inner ring: bore (d/2) with rounded corners, land (li) with the groove
    sk = plane_sketch(inner, "InnerRingSketch", "XZ_Plane")
    s = SK(sk)
    pts = [(d2, 0), (li, 0), (li, B / 2 - dzi), (li, B / 2 + dzi), (li, B), (d2, B)]
    l0 = sk.addGeometry(Part.LineSegment(V(*pts[0], 0), V(*pts[1], 0)), False)
    l1 = sk.addGeometry(Part.LineSegment(V(*pts[1], 0), V(*pts[2], 0)), False)
    l2 = sk.addGeometry(Part.LineSegment(V(*pts[3], 0), V(*pts[4], 0)), False)
    l3 = sk.addGeometry(Part.LineSegment(V(*pts[4], 0), V(*pts[5], 0)), False)
    l4 = sk.addGeometry(Part.LineSegment(V(*pts[5], 0), V(*pts[0], 0)), False)
    arc, (ps, pe) = groove(sk, s, pts[2], pts[3], ccw=False, name_r="ball_groove_r")
    sk.addConstraint(Sketcher.Constraint("Coincident", l0, 2, l1, 1))
    sk.addConstraint(Sketcher.Constraint("Coincident", l1, 2, arc, ps))
    sk.addConstraint(Sketcher.Constraint("Coincident", arc, pe, l2, 1))
    sk.addConstraint(Sketcher.Constraint("Coincident", l2, 2, l3, 1))
    sk.addConstraint(Sketcher.Constraint("Coincident", l3, 2, l4, 1))
    sk.addConstraint(Sketcher.Constraint("Coincident", l4, 2, l0, 1))
    s.con("Horizontal", l0); s.con("PointOnObject", l0, 1, H_AXIS)
    s.con("Vertical", l1); s.con("DistanceX", ORIGIN, 1, l1, 1, li, name="inner_land_r", expr=P + "bearing_inner_land_d / 2")
    s.con("Vertical", l2); s.con("PointOnObject", l2, 1, l1)
    s.con("Horizontal", l3); s.con("DistanceY", ORIGIN, 1, l3, 1, B, name="bearing_B", expr=P + "bearing_B")
    s.con("Vertical", l4); s.con("DistanceX", ORIGIN, 1, l4, 1, d2, name="bearing_d_r", expr=P + "bearing_d / 2")
    f1 = rounded_corner(sk, s, l3, l4, e, name="bearing_edge_r", expr=P + "bearing_edge_r")
    rounded_corner(sk, s, l4, l0, e, equal_to=f1)
    doc.recompute()
    out["inner"] = (sk, revolve(inner, sk, "InnerRingRevolve"))
    inner.Tip = out["inner"][1]

    # one ball on +Y (as the Fusion import: balls at +-Y), the Balls array makes bearing_ball_count of them
    sk = plane_sketch(ball, "BallSketch", "YZ_Plane")
    s = SK(sk)
    a = sk.addGeometry(Part.ArcOfCircle(Part.Circle(V(pr, B / 2, 0), V(0, 0, 1), rb), -math.pi / 2, math.pi / 2), False)
    ax = sk.addGeometry(Part.LineSegment(V(pr, B / 2 + rb, 0), V(pr, B / 2 - rb, 0)), False)
    sk.addConstraint(Sketcher.Constraint("Coincident", a, 2, ax, 1))
    sk.addConstraint(Sketcher.Constraint("Coincident", ax, 2, a, 1))
    s.con("PointOnObject", a, 3, ax)
    s.con("Vertical", ax)
    s.con("DistanceX", ORIGIN, 1, a, 3, pr, name="bearing_pitch_r", expr=P + "bearing_pitch_r")
    s.con("DistanceY", ORIGIN, 1, a, 3, B / 2, name="ball_centre_z", expr=P + "bearing_B / 2")
    s.con("Radius", a, rb, name="ball_r", expr=P + "bearing_ball_d / 2")
    doc.recompute()
    rv = revolve(ball, sk, "BallRevolve", axis="Edge2")
    ball.Tip = rv
    out["ball"] = (sk, rv)
    return dict(sketches=[out["outer"][0], out["inner"][0], sk] + seal_sketches,
                features=[out["outer"][1], out["inner"][1], rv] + seal_feats)


def _seal_groove(doc, sh, outer, top):
    """2RS1 outer ring end detail (Fusion w_61707-2rs1 Body1): from the end face the bore steps out to bearing_seal_notch_r for
    bearing_seal_notch_t, a half-round lip groove (radius bearing_seal_groove_r) and a short cone back to the land.  One revolved
    cut per end (sketch of the removed region; the top one is the bottom one mirrored about z = B / 2)."""
    g = _g(sh)
    B, lo, rn, t, gr, dz = g("bearing_B"), g("bearing_outer_land_d") / 2, g("bearing_seal_notch_r"), g("bearing_seal_notch_t"),         g("bearing_seal_groove_r"), g("bearing_seal_cone_dz")
    sg = -1.0 if top else 1.0
    E = B if top else 0.0
    nm = "Top" if top else "Bottom"
    sk = plane_sketch(outer, "SealGroove%sSketch" % nm, "XZ_Plane")
    s = SK(sk)
    z1, z2, z3, z4 = E + sg * t, E + sg * (t + gr), E + sg * (t + 2 * gr), E + sg * (t + 2 * gr + dz)
    la = sk.addGeometry(Part.LineSegment(V(lo, E, 0), V(rn, E, 0)), False)
    lb = sk.addGeometry(Part.LineSegment(V(rn, E, 0), V(rn, z1, 0)), False)
    ar = sk.addGeometry(Part.ArcOfCircle(Part.Circle(V(rn, z2, 0), V(0, 0, 1), gr), -math.pi / 2, math.pi / 2), False)
    lc = sk.addGeometry(Part.LineSegment(V(rn, z3, 0), V(lo, z4, 0)), False)
    ld = sk.addGeometry(Part.LineSegment(V(lo, z4, 0), V(lo, E, 0)), False)
    lower, upper = (1, 2)                                              # arc ends: pos 1 = lower z, pos 2 = upper z
    b_end, c_start = (lower, upper) if not top else (upper, lower)
    for a, ap, b, bp in ((la, 2, lb, 1), (lb, 2, ar, b_end), (ar, c_start, lc, 1), (lc, 2, ld, 1), (ld, 2, la, 1)):
        s.con("Coincident", a, ap, b, bp)
    s.con("Horizontal", la)
    s.con("DistanceX", ORIGIN, 1, la, 1, lo, name="seal_groove_land_r", expr=P + "bearing_outer_land_d / 2")
    if top:
        s.con("DistanceY", ORIGIN, 1, la, 1, B, name="seal_groove_end_z", expr=P + "bearing_B")
    else:
        s.con("PointOnObject", la, 1, H_AXIS)
    s.con("DistanceX", ORIGIN, 1, la, 2, rn, name="seal_notch_r", expr=P + "bearing_seal_notch_r")
    s.con("Vertical", lb)
    s.con("DistanceY", lb, 1, lb, 2, sg * t, name="seal_notch_t", expr=("-" if top else "") + P + "bearing_seal_notch_t")
    s.con("Radius", ar, gr, name="seal_groove_r", expr=P + "bearing_seal_groove_r")
    s.con("Vertical", ar, 3, ar, 1)                                    # half circle: both ends vertically in line with the centre
    s.con("Vertical", ar, 3, ar, 2)
    s.con("DistanceY", lc, 1, lc, 2, sg * dz, name="seal_cone_dz", expr=("-" if top else "") + P + "bearing_seal_cone_dz")
    s.con("Vertical", ld)
    doc.recompute()
    gv = outer.newObject("PartDesign::Groove", "SealGroove" + nm)
    gv.Profile = sk
    gv.ReferenceAxis = (sk, ["V_Axis"])
    gv.Angle = 360
    gv.Refine = True
    sk.Visibility = False
    outer.Tip = gv
    return sk, gv


def build_bearing_seal(doc, sh, body):
    """Seal of the 2RS1 bearing (Fusion Body3 / Body4): the bearing-local seal at z = bearing_seal_z0 .. z1; the second seal is the
    same body turned over (App::Link, 180 deg about X).  Fusion's profile is a stack of 45 deg cones: here an L section with the
    same radial / axial envelope and the same volume (outer flange thickness bearing_seal_t is the volume-matching value)."""
    g = _g(sh)
    ri, rm, ro, z0, z1, t = (g(n) for n in ("bearing_seal_ri", "bearing_seal_rm", "bearing_seal_ro", "bearing_seal_z0",
                                           "bearing_seal_z1", "bearing_seal_t"))
    sk = plane_sketch(body, "SealSection", "XZ_Plane")
    pts = [(ri, z0), (ro, z0), (ro, z0 + t), (rm, z0 + t), (rm, z1), (ri, z1)]
    spec = [("y", P + "bearing_seal_z0", "seal_z0"), ("x", P + "bearing_seal_ro", "seal_outer_r"),
            ("yrel", 0, P + "bearing_seal_t", "seal_flange_t"), ("x", P + "bearing_seal_rm", "seal_lip_start_r"),
            ("y", P + "bearing_seal_z1", "seal_z1"), ("x", P + "bearing_seal_ri", "seal_inner_r")]
    Poly(sk, pts, spec)
    doc.recompute()
    rv = revolve(body, sk, "SealRevolve")
    body.Tip = rv
    return dict(sketches=[sk], features=[rv])


# --------------------------------------------------------------------------------------------- holders


def _window_sketch(body, sh, name, z_expr):
    """Window between two spokes, centred on +Y (sector axis), symmetric: hub arc, fillet, spoke edge, outer arc."""
    g = _g(sh)
    # window axis -> spoke axis angle: half the spoke pitch.  CIANO14 40: = angle_wire (6 spokes on 6 sectors, window on the sector
    # axis).  CIANO28 85: 6 spokes on 12 sectors: holder_spoke_angle = 180 deg / holder_bolt_count, the window axis is turned by
    # holder_window_rot = holder_spoke_angle - angle_wire (counter-clockwise) so that the spokes stay on the bolt axes (angle_wire from the sector axis)
    sa_row = has_param(sh, "holder_spoke_angle")
    sa_name = "holder_spoke_angle" if sa_row else "angle_wire"
    aw = math.radians(g(sa_name))
    Rh, rf, w, Ri = g("holder_hub_r"), g("holder_window_r"), g("bolt_head_d") / 2, g("holder_inner_r")
    u = (math.sin(aw), math.cos(aw)); n = (-math.cos(aw), math.sin(aw))
    sc = math.sqrt((Rh + rf) ** 2 - (w + rf) ** 2)
    c = ((w + rf) * n[0] + sc * u[0], (w + rf) * n[1] + sc * u[1])
    k = Rh / (Rh + rf)
    TH = (c[0] * k, c[1] * k)
    TE = (c[0] - rf * n[0], c[1] - rf * n[1])
    te = math.sqrt(Ri * Ri - w * w)
    E = (w * n[0] + te * u[0], w * n[1] + te * u[1])
    M = lambda p: (-p[0], p[1])
    sk = plane_sketch(body, name, "XY_Plane", z_expr=z_expr, rot_expr=(P + "holder_window_rot") if has_param(sh, "holder_window_rot") else None)
    ch = Chain(sk)
    hub = ch.arc((0, 0), Rh, M(TH), TH, ccw=False, joint="T")
    fr = ch.arc(c, rf, TH, TE, ccw=True, joint="T")
    er = ch.line(TE, E, joint="C")
    out = ch.arc((0, 0), Ri, E, M(E), ccw=True, joint="C")
    el = ch.line(M(E), M(TE), joint="T")
    fl = ch.arc(M(c), rf, M(TE), M(TH), ccw=True, joint="T")
    ch.close()
    s = SK(sk)
    s.con("Coincident", hub, 3, ORIGIN, 1)
    s.con("Radius", hub, Rh, name="holder_hub_r", expr=P + "holder_hub_r")
    s.con("Coincident", out, 3, ORIGIN, 1)
    s.con("Radius", out, Ri, name="holder_inner_r", expr=P + "holder_inner_r")
    s.con("Radius", fr, rf, name="holder_window_r", expr=P + "holder_window_r")
    s.con("Angle", er, V_AXIS, aw, name="spoke_angle", expr=P + sa_name)
    s.con("Distance", ORIGIN, 1, er, w, name="spoke_half_width", expr=P + "bolt_head_d / 2")
    s.con("Symmetric", fr, ch.by_geo[fr]["exit"], fl, ch.by_geo[fl]["entry"], V_AXIS)
    s.con("Equal", fr, fl)
    return sk


def _boss_sketch(body, sh, name, z_expr, hole, hole_expr, hole_name):
    """Boss outline in a frame rotated by angle_wire (bolt on +Y): stator-side arc holder_inner_r, two spoke-edge lines
    tangent to the head circle (bolt_head_d) around the bolt, the head circle over the top, the screw hole."""
    g = _g(sh)
    b, h, Ri = g("holder_bolt_r"), g("bolt_head_d") / 2, g("holder_inner_r")
    sk = plane_sketch(body, name, "XY_Plane", z_expr=z_expr, rot_expr="-" + P + "angle_wire")
    ya = math.sqrt(Ri * Ri - h * h)
    ch = Chain(sk)
    a0 = ch.arc((0, 0), Ri, (-h, ya), (h, ya), ccw=False, joint="C")
    r = ch.line((h, ya), (h, b), joint="T")
    c1 = ch.arc((0, b), h, (h, b), (-h, b), ccw=True, joint="T")
    l = ch.line((-h, b), (-h, ya), joint="C")
    ch.close()
    s = SK(sk)
    s.con("Coincident", a0, 3, ORIGIN, 1)
    s.con("Radius", a0, Ri, name="holder_inner_r", expr=P + "holder_inner_r")
    s.con("Vertical", r)
    s.con("DistanceY", ORIGIN, 1, c1, 3, b, name="holder_bolt_r", expr=P + "holder_bolt_r")
    s.con("Radius", c1, h, name="bolt_head_r", expr=P + "bolt_head_d / 2")
    s.con("Symmetric", r, 2, l, 1, V_AXIS)
    hc = sk.addGeometry(Part.Circle(V(0, b, 0), V(0, 0, 1), hole), False)
    s.con("Coincident", hc, 3, c1, 3)
    s.con("Radius", hc, hole, name=hole_name, expr=hole_expr)
    return sk


def _seat_sketch(body, sh, name, z_expr, hole, hole_expr, hole_name):
    """Seat outline (bolt on +Y): stator-side arc, the two core-leg lines (distance core_leg_outer_x from the axis,
    parallel to the neighbouring sector axes), the head circle over the top, the screw hole."""
    g = _g(sh)
    aw = math.radians(g("angle_wire"))
    b, h, Ri, d = g("holder_bolt_r"), g("bolt_head_d") / 2, g("holder_inner_r"), g("core_leg_outer_x")
    u = (math.sin(aw), math.cos(aw)); n = (-math.cos(aw), math.sin(aw))     # right leg: n.p = d
    base = (d * n[0], d * n[1])
    t1 = math.sqrt(Ri * Ri - d * d)
    P1 = (base[0] + t1 * u[0], base[1] + t1 * u[1])
    # leg / head circle: |base + t u - (0, b)| = h, upper root
    bx, by = base[0], base[1] - b
    bb = 2 * (bx * u[0] + by * u[1]); cc = bx * bx + by * by - h * h
    t2 = (-bb + math.sqrt(bb * bb - 4 * cc)) / 2
    P2 = (base[0] + t2 * u[0], base[1] + t2 * u[1])
    M = lambda p: (-p[0], p[1])
    sk = plane_sketch(body, name, "XY_Plane", z_expr=z_expr, rot_expr="-" + P + "angle_wire")
    ch = Chain(sk)
    a0 = ch.arc((0, 0), Ri, M(P1), P1, ccw=False, joint="C")
    r = ch.line(P1, P2, joint="C")
    c1 = ch.arc((0, b), h, P2, M(P2), ccw=True, joint="C")
    l = ch.line(M(P2), M(P1), joint="C")
    ch.close()
    s = SK(sk)
    s.con("Coincident", a0, 3, ORIGIN, 1)
    s.con("Radius", a0, Ri, name="holder_inner_r", expr=P + "holder_inner_r")
    s.con("Angle", r, V_AXIS, aw, name="core_leg_angle", expr=P + "angle_wire")
    s.con("Distance", ORIGIN, 1, r, d, name="core_leg_outer_x", expr=P + "core_leg_outer_x")
    s.con("DistanceY", ORIGIN, 1, c1, 3, b, name="holder_bolt_r", expr=P + "holder_bolt_r")
    s.con("Radius", c1, h, name="bolt_head_r", expr=P + "bolt_head_d / 2")
    s.con("Symmetric", r, 2, l, 1, V_AXIS)
    s.con("Equal", r, l)
    hc = sk.addGeometry(Part.Circle(V(0, b, 0), V(0, 0, 1), hole), False)
    s.con("Coincident", hc, 3, c1, 3)
    s.con("Radius", hc, hole, name=hole_name, expr=hole_expr)
    return sk


def _pad(body, sk, name, length_expr, reversed_=False):
    pd = body.newObject("PartDesign::Pad", name)
    pd.Profile = sk
    pd.setExpression("Length", length_expr)
    pd.Reversed = reversed_
    pd.Refine = True
    sk.Visibility = False
    return pd


def _symmetric_pocket(body, sk, name, length_expr):
    """Pocket of a fixed length, symmetric about the sketch plane (no ThroughAll: its length follows the support's
    bounding box and a Params edit that moves the part could leave a tool short - seen on FreeCAD 1.0.2)."""
    pk = body.newObject("PartDesign::Pocket", name)
    pk.Profile = sk
    pk.setExpression("Length", length_expr)
    pad_symmetric(pk)
    pk.Refine = True
    sk.Visibility = False
    return pk


def _seat_chamfer_sketch(body, sh, name, back=False):
    """Chamfer23 as a pocket: two 45 deg triangles at the seat bottom corners on the core-leg faces x = +-core_leg_outer_x
    (XZ plane, pocketed through all along Y; the polar pattern repeats them at every sector boundary)."""
    g = _g(sh)
    d, c, zb = g("core_leg_outer_x"), g("holder_chamfer"), g("holder_seat_z")
    sg = -1 if back else 1
    z0 = sg * zb
    sk = plane_sketch(body, name, "XZ_Plane")
    s = SK(sk)
    A, B, C = (d, z0), (d + c, z0), (d, z0 + sg * c)
    M = lambda p: (-p[0], p[1])
    tri = []
    for pts in ((A, B, C), (M(A), M(B), M(C))):
        ls = [sk.addGeometry(Part.LineSegment(V(*pts[i], 0), V(*pts[(i + 1) % 3], 0)), False) for i in range(3)]
        for i in range(3):
            s.con("Coincident", ls[i], 2, ls[(i + 1) % 3], 1)
        tri.append(ls)
    r, l = tri
    s.con("Horizontal", r[0])
    s.con("DistanceY", ORIGIN, 1, r[0], 1, z0, name="holder_seat_z", expr=("-" if back else "") + P + "holder_seat_z")
    s.con("Vertical", r[2])
    s.con("DistanceX", ORIGIN, 1, r[0], 1, d, name="core_leg_outer_x", expr=P + "core_leg_outer_x")
    s.con("DistanceX", r[0], 1, r[0], 2, c, name="holder_chamfer", expr=P + "holder_chamfer")
    # 45 deg hypotenuse with its direction (an Equal of the two legs would let a large Params jump flip the triangle)
    s.con("Angle", r[1], math.atan2(C[1] - B[1], C[0] - B[0]))
    for i in range(3):
        s.con("Symmetric", r[i], 1, l[i], 1, V_AXIS)
    return sk


def _spoke_end_fillet_sketch(body, sh, name, z_expr=None):
    """Fillet27 as a pocket: the two cusps between a spoke side (bolt_head_d / 2 from the spoke axis), the back holder
    outer radius and the fillet arc tangent to both (frame rotated by angle_wire: spoke axis on +Y)."""
    g = _g(sh)
    w, R, rf = g("bolt_head_d") / 2, g("back_holder_outer_r"), g("back_holder_fillet_r")
    c = (w - rf, math.sqrt((R - rf) ** 2 - (w - rf) ** 2))
    Tl = (w, c[1])
    K = (w, math.sqrt(R * R - w * w))
    Tc = (c[0] * R / (R - rf), c[1] * R / (R - rf))
    M = lambda p: (-p[0], p[1])
    sk = plane_sketch(body, name, "XY_Plane", z_expr=z_expr or ("-" + P + "back_holder_end_z"), rot_expr="-" + P + "angle_wire")
    s = SK(sk)
    cr = Chain(sk)
    lr = cr.line(Tl, K, joint="C")
    br = cr.arc((0, 0), R, K, Tc, ccw=True, joint="T")
    fr = cr.arc(c, rf, Tc, Tl, ccw=False, joint="T")
    cr.close()
    cl = Chain(sk)
    ll = cl.line(M(Tl), M(K), joint="C")
    bl = cl.arc((0, 0), R, M(K), M(Tc), ccw=False, joint="T")
    fl = cl.arc(M(c), rf, M(Tc), M(Tl), ccw=True, joint="C")      # (C here, T above: the other split is degenerate)
    cl.close()
    s.con("Vertical", lr)
    s.con("DistanceX", ORIGIN, 1, lr, 1, w, name="spoke_half_width", expr=P + "bolt_head_d / 2")
    s.con("Coincident", br, 3, ORIGIN, 1)
    s.con("Radius", br, R, name="back_holder_outer_r", expr=P + "back_holder_outer_r")
    s.con("Radius", fr, rf, name="back_holder_fillet_r", expr=P + "back_holder_fillet_r")
    s.con("Symmetric", lr, 1, ll, 1, V_AXIS)
    s.con("Symmetric", lr, 2, ll, 2, V_AXIS)
    s.con("Coincident", bl, 3, ORIGIN, 1)
    s.con("Equal", fr, fl)
    return sk


def _pocket(body, sk, name, length_expr, reversed_=False):
    pk = body.newObject("PartDesign::Pocket", name)
    pk.Profile = sk
    pk.setExpression("Length", length_expr)
    pk.Reversed = reversed_
    pk.Refine = True
    sk.Visibility = False
    return pk


def _rib_sketch(body, sh, name, z_expr):
    """Spoke rib (Fusion Extrude50 / Extrude49), bolt axis on +Y: a strip of half width holder_rib_half_w from the hub circle
    (concave fillets of radius holder_window_r, one dimension + Equal) up to the head circle around the bolt."""
    g = _g(sh)
    a, rf, h, b, Rh = g("holder_rib_half_w"), g("holder_window_r"), g("bolt_head_d") / 2, g("holder_bolt_r"), g("holder_hub_r")
    vc = math.sqrt((Rh + rf) ** 2 - (a + rf) ** 2)
    k = Rh / (Rh + rf)
    Th = ((a + rf) * k, vc * k)
    ye = b - math.sqrt(h * h - a * a)
    M = lambda p: (-p[0], p[1])
    sk = plane_sketch(body, name, "XY_Plane", z_expr=z_expr, rot_expr="-" + P + "angle_wire")
    ch = Chain(sk)
    rl = ch.line((a, ye), (a, vc), joint="T")
    rfil = ch.arc((a + rf, vc), rf, (a, vc), Th, ccw=True, joint="T")
    hub = ch.arc((0, 0), Rh, Th, M(Th), ccw=True, joint="T")
    lfil = ch.arc((-a - rf, vc), rf, M(Th), (-a, vc), ccw=True, joint="T")
    ll = ch.line((-a, vc), (-a, ye), joint="C")
    top = ch.arc((0, b), h, (-a, ye), (a, ye), ccw=True, joint="C")
    ch.close()
    s = SK(sk)
    s.con("Coincident", hub, 3, ORIGIN, 1)
    s.con("Radius", hub, Rh, name="holder_hub_r", expr=P + "holder_hub_r")
    s.con("DistanceY", ORIGIN, 1, top, 3, b, name="holder_bolt_r", expr=P + "holder_bolt_r")
    s.con("Radius", top, h, name="bolt_head_r", expr=P + "bolt_head_d / 2")
    s.con("Vertical", rl)
    s.con("DistanceX", ORIGIN, 1, rl, 1, a, name="rib_half_width", expr=P + "holder_rib_half_w")
    s.con("Radius", rfil, rf, name="holder_window_r", expr=P + "holder_window_r")
    s.con("Symmetric", rl, 1, ll, 2, V_AXIS)           # (the left fillet radius follows: tangent to the mirrored line and the hub circle)
    s.con("Symmetric", rl, 2, ll, 1, V_AXIS)
    return sk


def build_holder_front(doc, sh, body):
    g = _g(sh)
    zf, zfl, zb, ze = g("holder_face_z"), g("holder_flange_z"), g("bearing_out_z"), g("front_holder_end_z")
    R8, Ri, Rh, Rl = g("bearing_od_r"), g("holder_inner_r"), g("holder_hub_r"), g("holder_lip_r")
    sk = plane_sketch(body, "HolderSection", "XZ_Plane")
    pts = [(R8, zf), (Ri, zf), (Ri, zfl), (Rh, zfl), (Rh, ze), (Rl, ze), (Rl, zb), (R8, zb)]
    spec = [("y", P + "holder_face_z", "holder_face_z"), ("x", P + "holder_inner_r", "holder_inner_r"),
            ("y", P + "holder_flange_z", "holder_flange_z"), ("x", P + "holder_hub_r", "holder_hub_r"),
            ("y", P + "front_holder_end_z", "front_holder_end_z"), ("x", P + "holder_lip_r", "holder_lip_r"),
            ("y", P + "bearing_out_z", "bearing_out_z"), ("x", P + "bearing_od_r", "bearing_od_r")]
    Poly(sk, pts, spec)
    doc.recompute()
    rv = revolve(body, sk, "HolderRevolve")
    wk = _window_sketch(body, sh, "HolderWindowSketch", P + "holder_flange_z")
    doc.recompute()
    win = _pocket(body, wk, "HolderWindow", P + "holder_flange_z")          # flange face down to z = 0
    bk = _boss_sketch(body, sh, "HolderBossSketch", P + "holder_flange_z", g("bolt_d") / 2, P + "bolt_d / 2", "bolt_hole_r")
    doc.recompute()
    boss = _pad(body, bk, "HolderBoss", P + "holder_boss_len", reversed_=True)
    tk = _seat_sketch(body, sh, "HolderSeatSketch", P + "motor_length / 2", g("bolt_d") / 2, P + "bolt_d / 2", "bolt_hole_r")
    doc.recompute()
    seat = _pad(body, tk, "HolderSeat", P + "holder_seat_depth", reversed_=True)
    ck = _seat_chamfer_sketch(body, sh, "HolderSeatChamferSketch")
    doc.recompute()
    chf = _symmetric_pocket(body, ck, "HolderSeatChamfer", P + "seat_chamfer_tool_len")
    doc.recompute()
    extra_s, extra_f = [], []
    if has_param(sh, "holder_rib_h"):                                   # CIANO28 85: ribs on the spokes (z flange face .. + holder_rib_h)
        rk = _rib_sketch(body, sh, "HolderRibSketch", P + "holder_flange_z")
        doc.recompute()
        rib = _pad(body, rk, "HolderRib", P + "holder_rib_h")
        extra_s.append(rk); extra_f.append(rib)
    pp = polar(body, [win, boss, seat, chf] + extra_f, wk, P + "holder_bolt_count", "HolderSpokes")
    pp.Refine = True
    body.Tip = pp
    doc.recompute()
    return dict(sketches=[sk, wk, bk, tk, ck] + extra_s, features=[rv, win, boss, seat, chf] + extra_f + [pp])


def build_holder_back(doc, sh, body):
    g = _g(sh)
    zf, zfl, zb, ze = g("holder_face_z"), g("holder_flange_z"), g("bearing_out_z"), g("back_holder_end_z")
    R8, Ri, Ro, Rl = g("bearing_od_r"), g("holder_inner_r"), g("back_holder_outer_r"), g("holder_lip_r")
    sk = plane_sketch(body, "HolderBackSection", "XZ_Plane")
    pts = [(R8, -zf), (R8, -zb), (Rl, -zb), (Rl, -ze), (Ro, -ze), (Ro, -zfl), (Ri, -zfl), (Ri, -zf)]
    spec = [("x", P + "bearing_od_r", "bearing_od_r"), ("y", "-" + P + "bearing_out_z", "bearing_out_z"),
            ("x", P + "holder_lip_r", "holder_lip_r"), ("y", "-" + P + "back_holder_end_z", "back_holder_end_z"),
            ("x", P + "back_holder_outer_r", "back_holder_outer_r"), ("y", "-" + P + "holder_flange_z", "holder_flange_z"),
            ("x", P + "holder_inner_r", "holder_inner_r"), ("y", "-" + P + "holder_face_z", "holder_face_z")]
    Poly(sk, pts, spec)
    doc.recompute()
    rv = revolve(body, sk, "HolderBackRevolve")
    wk = _window_sketch(body, sh, "HolderBackWindowSketch", "-" + P + "back_holder_end_z")
    doc.recompute()
    win = _pocket(body, wk, "HolderBackWindow", P + "back_holder_end_z", reversed_=True)   # A_H face up to z = 0
    bk = _boss_sketch(body, sh, "HolderBackBossSketch", "-" + P + "holder_flange_z", g("holder_tap_d") / 2, P + "holder_tap_d / 2",
                      "tap_hole_r")
    doc.recompute()
    boss = _pad(body, bk, "HolderBackBoss", P + "holder_boss_len")
    tk = _seat_sketch(body, sh, "HolderBackSeatSketch", "-" + P + "motor_length / 2", g("holder_tap_d") / 2,
                      P + "holder_tap_d / 2", "tap_hole_r")
    doc.recompute()
    seat = _pad(body, tk, "HolderBackSeat", P + "holder_seat_depth")
    # six M3 holes in the A_H face (Sketch7): on the spokes at back_screw_r
    mk = plane_sketch(body, "HolderBackM3Sketch", "XY_Plane", z_expr="-" + P + "back_holder_end_z",
                      rot_expr="-" + P + "back_screw_angle")
    s = SK(mk)
    rr, dd = g("back_screw_r"), g("back_screw_tap_d") / 2
    c = mk.addGeometry(Part.Circle(V(0, rr, 0), V(0, 0, 1), dd), False)
    s.con("PointOnObject", c, 3, V_AXIS)
    s.con("DistanceY", ORIGIN, 1, c, 3, rr, name="back_screw_r", expr=P + "back_screw_r")
    s.con("Radius", c, dd, name="back_screw_tap_r", expr=P + "back_screw_tap_d / 2")
    doc.recompute()
    m3 = _pocket(body, mk, "HolderBackM3", P + "back_holder_end_z", reversed_=True)
    ck = _seat_chamfer_sketch(body, sh, "HolderBackSeatChamferSketch", back=True)
    doc.recompute()
    chf = _symmetric_pocket(body, ck, "HolderBackSeatChamfer", P + "seat_chamfer_tool_len")
    if has_param(sh, "holder_rib_h"):         # CIANO28 85: the spoke-end fillets (Fusion Fillet27, r 2) exist in the rib zone only
        fk = _spoke_end_fillet_sketch(body, sh, "HolderBackSpokeFilletSketch", z_expr="-" + P + "holder_flange_z")
        doc.recompute()
        fil = _pocket(body, fk, "HolderBackSpokeFillet", P + "holder_rib_h")
    else:
        fk = _spoke_end_fillet_sketch(body, sh, "HolderBackSpokeFilletSketch")
        doc.recompute()
        fil = _pocket(body, fk, "HolderBackSpokeFillet", P + "back_holder_len", reversed_=True)
    doc.recompute()
    extra_s, extra_f = [], []
    if has_param(sh, "holder_rib_h"):
        rk = _rib_sketch(body, sh, "HolderBackRibSketch", "-" + P + "holder_flange_z")
        doc.recompute()
        rib = _pad(body, rk, "HolderBackRib", P + "holder_rib_h", reversed_=True)
        extra_s.append(rk); extra_f.append(rib)
    pp = polar(body, [win, boss, seat, m3, chf, fil] + extra_f, wk, P + "holder_bolt_count", "HolderBackSpokes")
    pp.Refine = True
    body.Tip = pp
    doc.recompute()
    return dict(sketches=[sk, wk, bk, tk, mk, ck, fk] + extra_s, features=[rv, win, boss, seat, m3, chf, fil] + extra_f + [pp])


# --------------------------------------------------------------------------------------------- Prop_fix, screw
# (the GFRP ring and its back copy were removed from every construction, owner 2026-10-15; template A.7.1)


def build_prop_fix(doc, sh, body):
    g = _g(sh)
    clr = g("prop_fix_clearance")
    Rj, fw2, rr0 = g("shaft_journal_r"), g("shaft_flat_w") / 2, g("prop_fix_relief_r")
    Rb, fx, rr = Rj + clr, fw2 + clr, rr0 + clr
    K = (fw2, math.sqrt(Rj * Rj - fw2 * fw2))                     # shaft corner (flat / journal), Fusion P3
    kk = (Rj - rr0) / Rj
    c = (K[0] * kk, K[1] * kk)                                   # relief centre: tangent to the journal, through the corner
    T = (c[0] * Rb / (Rj - rr0), c[1] * Rb / (Rj - rr0))         # tangent point relief / big arc (offset geometry)
    Fy = c[1] - math.sqrt(rr * rr - (fx - c[0]) ** 2)            # relief meets the flat
    F = (fx, Fy)
    mx = lambda p: (-p[0], p[1]); my = lambda p: (p[0], -p[1]); mxy = lambda p: (-p[0], -p[1])
    z = P + "shaft_journal_end_z"
    sk = plane_sketch(body, "PropFixHubSketch", "XY_Plane", z_expr=z)
    s = SK(sk)
    oc = sk.addGeometry(Part.Circle(V(0, 0, 0), V(0, 0, 1), g("prop_fix_d") / 2), False)
    s.con("Coincident", oc, 3, ORIGIN, 1)
    s.con("Diameter", oc, g("prop_fix_d"), name="prop_fix_d", expr=P + "prop_fix_d")
    ch = Chain(sk)
    flr = ch.line(my(F), F, joint="C")
    rtr = ch.arc(c, rr, F, T, ccw=True, joint="T")
    bt = ch.arc((0, 0), Rb, T, mx(T), ccw=True, joint="T")
    rtl = ch.arc(mx(c), rr, mx(T), mx(F), ccw=True, joint="C")
    fll = ch.line(mx(F), mxy(F), joint="C")
    rbl = ch.arc(mxy(c), rr, mxy(F), mxy(T), ccw=True, joint="T")
    bb = ch.arc((0, 0), Rb, mxy(T), my(T), ccw=True, joint="T")
    rbr = ch.arc(my(c), rr, my(T), my(F), ccw=True, joint="C")
    ch.close()
    s.con("Symmetric", flr, 1, flr, 2, H_AXIS)
    s.con("DistanceX", ORIGIN, 1, flr, 1, fx, name="prop_fix_flat_x", expr=P + "shaft_flat_w / 2 + " + P + "prop_fix_clearance")
    s.con("Symmetric", flr, 1, fll, 2, V_AXIS)
    s.con("Symmetric", flr, 2, fll, 1, V_AXIS)
    s.con("Coincident", bt, 3, ORIGIN, 1)
    s.con("Radius", bt, Rb, name="prop_fix_bore_r", expr=P + "shaft_journal_r + " + P + "prop_fix_clearance")
    s.con("Coincident", bb, 3, ORIGIN, 1)
    s.con("Equal", bt, bb)
    s.con("Radius", rtr, rr, name="prop_fix_relief_r", expr=P + "prop_fix_relief_r + " + P + "prop_fix_clearance")
    for a in (rtl, rbl, rbr):
        s.con("Equal", rtr, a)
    # Fusion: the relief circle (prop_fix_relief_r) passes through the shaft corner (flat x journal); construction geometry
    kc = s.ccircle((0, 0), Rj)
    s.con("Coincident", kc, 3, ORIGIN, 1)
    s.con("Radius", kc, Rj, name="shaft_journal_r", expr=P + "shaft_journal_r")
    kp = sk.addGeometry(Part.Point(V(K[0], K[1], 0)), True)
    s.con("PointOnObject", kp, 1, kc)
    s.con("DistanceX", ORIGIN, 1, kp, 1, K[0], name="shaft_flat_half_w", expr=P + "shaft_flat_w / 2")
    # the relief is tangent to the journal AT the corner: its tangent point lies on the ray origin -> corner (a distance
    # |corner - centre| = relief_r would be stationary there and the solver would call it redundant)
    ray = s.cline((0, 0), T)
    s.con("Coincident", ray, 1, ORIGIN, 1)
    s.con("Coincident", ray, 2, rtr, ch.by_geo[rtr]["exit"])
    s.con("PointOnObject", kp, 1, ray)
    doc.recompute()
    hub = _pad(body, sk, "PropFixHub", P + "prop_fix_hub_t", reversed_=True)
    pk = plane_sketch(body, "PropFixPlateSketch", "XY_Plane", z_expr=z)
    s = SK(pk)
    oc = pk.addGeometry(Part.Circle(V(0, 0, 0), V(0, 0, 1), g("prop_fix_d") / 2), False)
    s.con("Coincident", oc, 3, ORIGIN, 1)
    s.con("Diameter", oc, g("prop_fix_d"), name="prop_fix_d", expr=P + "prop_fix_d")
    ic = pk.addGeometry(Part.Circle(V(0, 0, 0), V(0, 0, 1), g("shaft_thread_r") + clr), False)
    s.con("Coincident", ic, 3, ORIGIN, 1)
    s.con("Radius", ic, g("shaft_thread_r") + clr, name="prop_fix_hole_r", expr=P + "shaft_thread_r + " + P + "prop_fix_clearance")
    doc.recompute()
    plate = _pad(body, pk, "PropFixPlate", P + "prop_fix_plate_t")
    body.Tip = plate
    return dict(sketches=[sk, pk], features=[hub, plate])


def build_screw(doc, sh, body):
    """Socket head screw on the bolt circle at angle_wire (body rotated by angle_wire), head on the front flange face."""
    g = _g(sh)
    shank_row = has_param(sh, "screw_shank_d")                 # CIANO28 85: the screw shank differs from the hole (bolt_d)
    shank_expr = P + ("screw_shank_d" if shank_row else "bolt_d") + " / 2"
    a, rs, rh, hh = g("holder_bolt_r"), g("screw_shank_d" if shank_row else "bolt_d") / 2, g("screw_head_d") / 2, g("screw_head_h")
    f, ct, af2, dep = g("screw_head_fillet"), g("screw_tip_chamfer"), g("screw_socket_af") / 2, g("screw_socket_depth")
    zf = g("holder_flange_z")
    zt, zh = zf - g("holder_screw_l"), zf + hh
    pa = math.radians(g("screw_socket_point") / 2)
    zc = zh - dep - af2 / math.tan(pa)
    sk = plane_sketch(body, "ScrewSection", "YZ_Plane")
    s = SK(sk)
    pts = [(a, zc), (a, zt), (a + rs - ct, zt), (a + rs, zt + ct), (a + rs, zf), (a + rh, zf), (a + rh, zh),
           (a + af2, zh), (a + af2, zh - dep)]
    spec = [("free",), ("free",), ("free",), ("free",), ("free",), ("free",), ("free",), ("free",), ("free",)]
    pl = Poly(sk, pts, spec)
    G = pl.g
    s.con("Vertical", G[0]); s.con("DistanceX", ORIGIN, 1, G[0], 1, a, name="holder_bolt_r", expr=P + "holder_bolt_r")
    s.con("Horizontal", G[1])
    s.con("Vertical", G[3]); s.con("DistanceX", G[0], 1, G[3], 1, rs, name="bolt_r", expr=shank_expr)
    s.con("Horizontal", G[4]); s.con("DistanceY", ORIGIN, 1, G[4], 1, zf, name="holder_flange_z", expr=P + "holder_flange_z")
    s.con("DistanceY", G[1], 1, G[4], 1, zf - zt, name="holder_screw_l", expr=P + "holder_screw_l")
    s.con("Vertical", G[5]); s.con("DistanceX", G[0], 1, G[5], 1, rh, name="screw_head_r", expr=P + "screw_head_d / 2")
    s.con("Horizontal", G[6]); s.con("DistanceY", G[4], 1, G[6], 1, hh, name="screw_head_h", expr=P + "screw_head_h")
    s.con("Vertical", G[7]); s.con("DistanceX", G[0], 1, G[7], 1, af2, name="socket_half_af", expr=P + "screw_socket_af / 2")
    s.con("DistanceY", G[7], 2, G[6], 2, dep, name="screw_socket_depth", expr=P + "screw_socket_depth")
    s.con("Angle", G[8], G[0], pa, name="socket_point_half_angle", expr=P + "screw_socket_point / 2")
    pl.chamfer45(2, name="screw_tip_chamfer", expr=P + "screw_tip_chamfer")
    f1 = rounded_corner(sk, s, G[4], G[5], f, name="screw_head_fillet", expr=P + "screw_head_fillet")
    rounded_corner(sk, s, G[5], G[6], f, equal_to=f1)
    doc.recompute()
    rv = revolve(body, sk, "ScrewRevolve", axis="Edge1")
    # hex socket (flats parallel to the screw's radial line), depth screw_socket_depth from the head top
    hk = plane_sketch(body, "ScrewSocketSketch", "XY_Plane", z_expr=P + "screw_head_top_z")
    s = SK(hk)
    R = af2 / math.cos(math.radians(30))
    vs = [(R * math.cos(math.radians(60 * i)), a + R * math.sin(math.radians(60 * i))) for i in range(6)]
    ls = [hk.addGeometry(Part.LineSegment(V(*vs[i], 0), V(*vs[(i + 1) % 6], 0)), False) for i in range(6)]
    for i in range(6):
        s.con("Coincident", ls[i], 2, ls[(i + 1) % 6], 1)
    cc = s.ccircle((0, a), R)
    for i in range(6):
        s.con("PointOnObject", ls[i], 1, cc)
    for i in range(1, 6):
        s.con("Equal", ls[0], ls[i])
    s.con("PointOnObject", cc, 3, V_AXIS)
    s.con("DistanceY", ORIGIN, 1, cc, 3, a, name="holder_bolt_r", expr=P + "holder_bolt_r")
    s.con("Horizontal", ls[1])
    s.con("Distance", cc, 3, ls[1], af2, name="socket_half_af", expr=P + "screw_socket_af / 2")
    doc.recompute()
    pk = body.newObject("PartDesign::Pocket", "ScrewSocket")
    pk.Profile = hk
    pk.setExpression("Length", P + "screw_socket_depth")
    pk.Refine = True
    hk.Visibility = False
    body.Tip = pk
    body.Placement = App.Placement(V(0, 0, 0), App.Rotation(V(0, 0, 1), -g("angle_wire")))
    body.setExpression(".Placement.Rotation.Angle", "-" + P + "angle_wire")
    return dict(sketches=[sk, hk], features=[rv, pk])


# --------------------------------------------------------------------------------------------- assembly


MECH_BODIES = ("Shaft", "Shaft_rear", "Bearing_seal", "Stator_holder", "Stator_holder_back", "Prop_fix", "Bearing_outer_ring", "Bearing_inner_ring")
ARRAY_BASES = ("Bearing_ball", "Screw")
MECH_PARTS = ("Mechanics", "Bearing_618_8")
MECH_LINKS = ("Bearing_618_8_back", "Bearing_seal_2")
MECH_ARRAYS = ("Balls", "Screws")


def _link(doc, target, name, z_expr, parent):
    lk = doc.addObject("App::Link", name)
    lk.Label = name
    lk.LinkedObject = target
    lk.setExpression(".Placement.Base.z", z_expr)
    parent.addObject(lk)
    return lk


def g_B(sh):
    return pval(sh, "bearing_B")


def build_mechanics(doc, sh, polar_array):
    """Adds the Mechanics part with every mechanical body.  polar_array(doc, base, count_expr, name) -> Draft link array.
    Returns the per-body reports (sketches, chamfer / fillet edge counts)."""
    mech = doc.addObject("App::Part", "Mechanics"); mech.Label = "Mechanics"
    rep = {}

    def body(name):
        b = doc.addObject("PartDesign::Body", name); b.Label = name
        return b

    carrier = has_param(sh, "carrier_bore_r")                  # profile mechanics = ring_carrier (CIANO28 85)
    b = body("Shaft"); mech.addObject(b)
    if carrier:
        from .mechanics_carrier import build_carrier
        rep["Shaft"] = build_carrier(doc, sh, b)
        b = body("Shaft_rear"); mech.addObject(b); rep["Shaft_rear"] = build_carrier(doc, sh, b, rear=True)
    else:
        rep["Shaft"] = build_shaft(doc, sh, b)
    brg = doc.addObject("App::Part", "Bearing_618_8"); brg.Label = "Bearing_618_8"; mech.addObject(brg)
    o, i, ba = body("Bearing_outer_ring"), body("Bearing_inner_ring"), body("Bearing_ball")
    for x in (o, i, ba):
        brg.addObject(x)
    rep["Bearing"] = build_bearing(doc, sh, o, i, ba)
    if has_param(sh, "bearing_seal_ri"):
        sl = body("Bearing_seal"); brg.addObject(sl)
        rep["Bearing_seal"] = build_bearing_seal(doc, sh, sl)
        lk = doc.addObject("App::Link", "Bearing_seal_2"); lk.Label = "Bearing_seal_2"; lk.LinkedObject = sl
        lk.Placement = App.Placement(V(0, 0, g_B(sh)), App.Rotation(V(1, 0, 0), 180))
        lk.setExpression(".Placement.Base.z", P + "bearing_B")
        brg.addObject(lk)
    brg.setExpression(".Placement.Base.z", P + "holder_face_z")
    b = body("Stator_holder"); mech.addObject(b); rep["Stator_holder"] = build_holder_front(doc, sh, b)
    b = body("Stator_holder_back"); mech.addObject(b); rep["Stator_holder_back"] = build_holder_back(doc, sh, b)
    if not carrier:
        b = body("Prop_fix"); mech.addObject(b); rep["Prop_fix"] = build_prop_fix(doc, sh, b)
    sc = body("Screw"); mech.addObject(sc); rep["Screw"] = build_screw(doc, sh, sc)
    doc.recompute()
    balls = polar_array(doc, ba, P + "bearing_ball_count", "Balls"); brg.addObject(balls)
    screws = polar_array(doc, sc, P + "holder_bolt_count", "Screws"); mech.addObject(screws)
    _link(doc, brg, "Bearing_618_8_back", "-" + P + "bearing_out_z", mech)
    doc.recompute()
    return rep


# --------------------------------------------------------------------------------------------- solids (export / checks)


def has_mechanics(doc):
    return doc.getObject("Mechanics") is not None


def _placed(shape, *placements):
    s = shape.copy()
    pl = App.Placement()
    for p in placements:
        pl = pl.multiply(p)
    s.Placement = pl.multiply(s.Placement)
    return s


def solids(doc):
    """{name: solid in the motor frame} of every mechanical part, in a fixed order (STEP tree, dumps, fit checks).
    Copies are placed explicitly: Bearing_618_8 Part placement for the front bearing, the App::Link placements for the
    back bearing and the back ring (Fusion occurrences)."""
    out = {}
    if not has_mechanics(doc):
        return out
    gs = lambda n: doc.getObject(n).Shape
    out["Shaft"] = gs("Shaft").copy()
    if doc.getObject("Shaft_rear") is not None:
        out["Shaft_rear"] = gs("Shaft_rear").copy()
    brg = doc.getObject("Bearing_618_8")
    back = doc.getObject("Bearing_618_8_back")
    balls = [o for o in doc.Objects if o.Label == "Balls"][0].Shape.Solids
    for tag, pl in (("front", brg.Placement), ("back", back.Placement)):
        out["Bearing_618_8_%s_outer_ring" % tag] = _placed(gs("Bearing_outer_ring"), pl)
        out["Bearing_618_8_%s_inner_ring" % tag] = _placed(gs("Bearing_inner_ring"), pl)
        for i, b in enumerate(balls):
            out["Bearing_618_8_%s_ball_%02d" % (tag, i + 1)] = _placed(b, pl)
        if doc.getObject("Bearing_seal") is not None:
            out["Bearing_618_8_%s_seal_1" % tag] = _placed(gs("Bearing_seal"), pl)
            out["Bearing_618_8_%s_seal_2" % tag] = _placed(gs("Bearing_seal"), pl, doc.getObject("Bearing_seal_2").Placement)
    out["Stator_holder"] = gs("Stator_holder").copy()
    out["Stator_holder_back"] = gs("Stator_holder_back").copy()
    if doc.getObject("Prop_fix") is not None:
        out["Prop_fix"] = gs("Prop_fix").copy()
    for i, s in enumerate([o for o in doc.Objects if o.Label == "Screws"][0].Shape.Solids):
        out["Screw_%d" % (i + 1)] = s.copy()
    return out


def groups(names):
    """STEP sub-assembly of a solid name."""
    def grp(n):
        if n.startswith("Bearing_618_8_front"):
            return "Bearing_618_8_front"
        if n.startswith("Bearing_618_8_back"):
            return "Bearing_618_8_back"
        if n.startswith("Screw_"):
            return "Screws"
        return None
    return {n: grp(n) for n in names}
