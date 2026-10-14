"""Ring carrier (profile `mechanics = ring_carrier`, CIANO28 85/L13): the rotor carrier of the hollow-shaft motor, built like the
Fusion components Shaft1 (steel, front, hub with the M3 end-face holes) and Shaft2 (aluminium, rear, thin hub tube).

Both are a revolve of ONE rectilinear half section (Poly: every edge is one named dimension of a Params row) with the rotor
bore as the rim seat (shaft_seat_r), a flange behind the rotor face, the bearing shoulder and the bearing journal; the rear
part is the front one mirrored in z with the hub tube instead of the thick hub.  Fusion adds, per rotor key
(rotor_key_count, first on +Y), a rounded tab inside the rim (cap r = carrier_tab_cap_r, concave flanks r =
carrier_tab_fillet_r) and cuts the key slot (width rotor_key_w, round bottom, through the rim) - one sketch each, patterned.
The fillet on the slot edges (Fusion Fillet4, r 0.15) is not modelled (volume effect < 0.2 mm3).
"""
import math
import Part
import Sketcher
from .sketchkit import Chain, SK, V_AXIS
from .params_sheet import pval
from .common import P
from .mechanics import plane_sketch, Poly, revolve, polar, _pad, _pocket, ORIGIN, V


def _g(sh):
    return lambda n: pval(sh, n)


def _hub_holes(doc, body, sh, g, rv):
    """M3 holes in the front hub end face (Fusion Extrude15 + Thread10 + C-Pattern10)."""
    hk = plane_sketch(body, "CarrierHoleSketch", "XY_Plane", z_expr=P + "shaft_journal_end_z")
    s = SK(hk)
    cc = s.ccircle((0, g("carrier_hole_pcd_r")), g("carrier_hole_d") / 2)
    hk.setConstruction(cc, False)
    s.con("PointOnObject", cc, 3, V_AXIS)
    s.con("DistanceY", ORIGIN, 1, cc, 3, g("carrier_hole_pcd_r"), name="carrier_hole_pcd_r", expr=P + "carrier_hole_pcd_r")
    s.con("Diameter", cc, g("carrier_hole_d"), name="carrier_hole_d", expr=P + "carrier_hole_d")
    doc.recompute()
    pk = _pocket(body, hk, "CarrierHole", P + "shaft_journal_end_z - " + P + "carrier_flange_front_z")
    doc.recompute()
    pp = polar(body, [pk], hk, P + "carrier_hole_count", "CarrierHoles")
    pp.Refine = True
    body.Tip = pp            # the next features (key tab / slot) build on the patterned holes
    return hk, pk, pp


def _tab_points(g):
    """Initial positions of the tab sketch (the solver finds the same point from the constraints): the flank circle (radius
    rf) is tangent inside to the rim circle (radius Rin) and tangent outside to the cap circle (radius rc, centre (0, Rin))."""
    Rin, rc, rf = g("carrier_rim_in_r"), g("carrier_tab_cap_r"), g("carrier_tab_fillet_r")
    a, b = Rin - rf, rc + rf
    y = (a * a - b * b + Rin * Rin) / (2 * Rin)
    Cf = (math.sqrt(max(a * a - y * y, 0.0)), y)
    k = rc / b
    J = (Cf[0] * k, Rin + (Cf[1] - Rin) * k)             # on the segment cap centre -> flank centre
    E = (Cf[0] * Rin / a, Cf[1] * Rin / a)                 # tangent point on the rim circle
    return Cf, J, E


def build_carrier(doc, sh, body, rear=False):
    g = _g(sh)
    sg = -1.0 if rear else 1.0
    bore, fl_r, floor, rin = g("carrier_bore_r"), g("carrier_flange_r"), g("carrier_floor_r"), g("carrier_rim_in_r")
    seat, jr, shr = g("shaft_seat_r"), g("shaft_journal_r"), g("shaft_shoulder_r")
    z_fl, z_web, z_rim = g("carrier_flange_front_z"), g("carrier_web_z"), g("carrier_rim_end_z")
    zf, zb, zj, zr = g("holder_face_z"), g("bearing_out_z"), g("shaft_journal_end_z"), g("rotor_w") / 2
    sk = plane_sketch(body, "ShaftRearSketch" if rear else "ShaftSketch", "XZ_Plane")
    m = lambda e: ("-" + P + e) if rear else (P + e)
    if not rear:
        pts = [(bore, z_fl), (floor, z_fl), (floor, z_web), (rin, z_web), (rin, z_rim), (seat, z_rim), (seat, zr), (fl_r, zr),
               (fl_r, z_fl), (shr, z_fl), (shr, zf), (jr, zf), (jr, zj), (bore, zj)]
        spec = [("y", P + "carrier_flange_front_z", "carrier_flange_front_z"), ("x", P + "carrier_floor_r", "carrier_floor_r"),
                ("y", P + "carrier_web_z", "carrier_web_z"), ("x", P + "carrier_rim_in_r", "carrier_rim_in_r"),
                ("y", P + "carrier_rim_end_z", "carrier_rim_end_z"), ("x", P + "shaft_seat_r", "shaft_seat_r"),
                ("y", P + "rotor_w / 2", "rotor_face_z"), ("x", P + "carrier_flange_r", "carrier_flange_r"),
                ("y", P + "carrier_flange_front_z", "carrier_flange_front_z_2"), ("x", P + "shaft_shoulder_r", "shaft_shoulder_r"),
                ("y", P + "holder_face_z", "holder_face_z"), ("x", P + "shaft_journal_r", "shaft_journal_r"),
                ("y", P + "shaft_journal_end_z", "shaft_journal_end_z"), ("x", P + "carrier_bore_r", "carrier_bore_r")]
    else:
        pts = [(floor, -zb), (jr, -zb), (jr, -zf), (shr, -zf), (shr, -z_fl), (fl_r, -z_fl), (fl_r, -zr), (seat, -zr),
               (seat, -z_rim), (rin, -z_rim), (rin, -z_web), (floor, -z_web)]
        spec = [("y", m("bearing_out_z"), "shaft_back_end_z"), ("x", P + "shaft_journal_r", "shaft_journal_r"),
                ("y", m("holder_face_z"), "back_step_z"), ("x", P + "shaft_shoulder_r", "shaft_shoulder_r"),
                ("y", m("carrier_flange_front_z"), "carrier_flange_front_z"), ("x", P + "carrier_flange_r", "carrier_flange_r"),
                ("y", m("rotor_w / 2"), "rotor_face_z"), ("x", P + "shaft_seat_r", "shaft_seat_r"),
                ("y", m("carrier_rim_end_z"), "carrier_rim_end_z"), ("x", P + "carrier_rim_in_r", "carrier_rim_in_r"),
                ("y", m("carrier_web_z"), "carrier_web_z"), ("x", P + "carrier_floor_r", "carrier_floor_r")]
    Poly(sk, pts, spec)
    doc.recompute()
    rv = revolve(body, sk, "ShaftRearRevolve" if rear else "ShaftRevolve")
    sketches, feats = [sk], [rv]
    if not rear:
        hk, hpk, hpp = _hub_holes(doc, body, sh, g, rv)
        sketches.append(hk); feats += [hpk, hpp]

    # ---- rim key tab (Pad) -------------------------------------------------------------------------------------------
    rc, rf = g("carrier_tab_cap_r"), g("carrier_tab_fillet_r")
    Cf, J, E = _tab_points(g)
    tk = plane_sketch(body, "CarrierTabSketch", "XY_Plane", z_expr=("-" + P + "carrier_web_z") if rear else (P + "carrier_rim_end_z"))
    s = SK(tk)
    c = Chain(tk)
    M = lambda p: (-p[0], p[1])
    top = c.arc((0, 0), rin, M(E), E, ccw=False, joint="T")
    flR = c.arc(Cf, rf, E, J, ccw=True, joint="T")
    cap = c.arc((0, rin), rc, J, M(J), ccw=False, joint="T")
    flL = c.arc(M(Cf), rf, M(J), M(E), ccw=True, joint="T")
    c.close()
    s.con("Coincident", top, 3, -1, 1)
    s.con("Radius", top, rin, name="carrier_rim_in_r", expr=P + "carrier_rim_in_r")
    s.con("PointOnObject", cap, 3, V_AXIS)
    s.con("DistanceY", ORIGIN, 1, cap, 3, rin, name="carrier_tab_centre_y", expr=P + "carrier_rim_in_r")
    s.con("Radius", cap, rc, name="carrier_tab_cap_r", expr=P + "carrier_tab_cap_r")
    s.con("Radius", flR, rf, name="carrier_tab_fillet_r", expr=P + "carrier_tab_fillet_r")
    s.con("Equal", flR, flL)
    doc.recompute()
    tab = _pad(body, tk, "CarrierTab", P + "carrier_rim_h")
    doc.recompute()

    # ---- key slot (Pocket): half width rotor_key_w / 2, round bottom on the rim inner circle, up to the flange radius ------
    kw = g("rotor_key_w") / 2
    yt = fl_r
    pk = plane_sketch(body, "CarrierSlotSketch", "XY_Plane", z_expr=m("rotor_w / 2"))
    s2 = SK(pk)
    A, B = (kw, yt), (kw, rin)
    gl = pk.addGeometry(Part.LineSegment(V(*A, 0), V(*B, 0)), False)           # right side, down
    ga = pk.addGeometry(Part.ArcOfCircle(Part.Circle(V(0, rin, 0), V(0, 0, 1), kw), math.pi, 2 * math.pi), False)   # bottom, right to left
    gr = pk.addGeometry(Part.LineSegment(V(-kw, rin, 0), V(-kw, yt, 0)), False)  # left side, up
    gt = pk.addGeometry(Part.LineSegment(V(-kw, yt, 0), V(kw, yt, 0)), False)    # top
    s2.con("Tangent", gl, 2, ga, 2); s2.con("Tangent", ga, 1, gr, 1)          # side lines run tangent into the round bottom
    s2.con("Coincident", gr, 2, gt, 1); s2.con("Coincident", gt, 2, gl, 1)
    s2.con("Vertical", gl)
    s2.con("Symmetric", gl, 1, gt, 1, V_AXIS)                  # the two top corners are mirror images (top line horizontal)
    s2.con("PointOnObject", ga, 3, V_AXIS)
    s2.con("DistanceY", ORIGIN, 1, ga, 3, rin, name="carrier_slot_bottom_y", expr=P + "carrier_rim_in_r")
    s2.con("DistanceX", ORIGIN, 1, gl, 1, kw, name="carrier_slot_half_w", expr=P + "rotor_key_w / 2")
    s2.con("DistanceY", ORIGIN, 1, gl, 1, yt, name="carrier_slot_top_y", expr=P + "carrier_flange_r")
    doc.recompute()
    slot = _pocket(body, pk, "CarrierSlot", P + "rotor_w / 2 - " + P + "carrier_rim_end_z", reversed_=rear)
    doc.recompute()
    kp = polar(body, [tab, slot], tk, P + "rotor_key_count", "CarrierKeys")
    kp.Refine = True
    body.Tip = kp
    doc.recompute()
    return dict(sketches=sketches + [tk, pk], features=feats + [tab, slot, kp])
