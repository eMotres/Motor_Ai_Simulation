"""Rotor (spoke type, steel): one pole-pitch sketch -> Pad (symmetric rotor_w) -> PolarPattern (num_seg*num_poles_per_segment).

Pole pitch (angle_mag, centred on +Y = magnet centre line): hub ring segment (rotor_down_r - rotor_house_height ..
rotor_down_r), two half poles up to rotor_up_r, magnet pocket open to the air gap between lips at
+-rotor_up_r*sin(magnet_fill_up*angle_mag*rotor_hole/2).  Pocket sides = nominal magnet sides offset outwards by
offset_rotor (Fusion OffsetFaces3); pocket bottom and lips are not offset (checked against the Fusion B-rep faces);
top fillet = magnet_fill_radius + offset_rotor about the nominal fillet centre.  Fusion Fillet7 (0.1) and
OffsetFaces6 have no effect in the open model (error state, no faces) and are not modelled.
"""
import math
from .sketchkit import Chain, SK
from .params_sheet import pval
from .common import P, M, xy_sketch, line_offset, pad_symmetric, intersect, fillet_center_circle_line, rotor_numbers


def build_rotor(doc, sh, body):
    g = lambda n: pval(sh, n)
    n = rotor_numbers(g)
    P4, K, U, off = n["P4"], n["K"], n["U"], n["off"]

    def outward(p, q):
        a1, b1 = line_offset(p, q, off)
        a2, b2 = line_offset(p, q, -off)
        return (a1, b1) if a1[0] > p[0] else (a2, b2)
    lo_a, lo_b = outward(P4, K)
    up_a, up_b = outward(K, U)
    Kp = intersect(lo_a, lo_b, up_a, up_b)
    yb = P4[1]
    Bp = intersect(lo_a, lo_b, (0, yb), (1, yb))
    cfil = fillet_center_circle_line(n["Rtop"] - n["mfr"], K, U, n["mfr"])
    rf = n["mfr"] + off
    dx, dy = up_b[0] - up_a[0], up_b[1] - up_a[1]
    Ll = math.hypot(dx, dy); ux, uy = dx / Ll, dy / Ll
    t = (cfil[0] - up_a[0]) * ux + (cfil[1] - up_a[1]) * uy
    Fu = (up_a[0] + t * ux, up_a[1] + t * uy)
    xl = n["Rup"] * math.sin(math.radians(n["h"]))
    Lb = (xl, cfil[1] + math.sqrt(rf * rf - (xl - cfil[0]) ** 2))
    Lt = (xl, math.sqrt(n["Rup"] ** 2 - xl * xl))
    Pp = (n["Rup"] * n["sa"], n["Rup"] * n["ca"])
    Ph = (n["Rhub"] * n["sa"], n["Rhub"] * n["ca"])
    a_deg = n["a"]

    sk = xy_sketch(body, "RotorPoleSketch")
    c = Chain(sk)
    chord = c.line(M(Bp), Bp, joint="C")
    R, L = {}, {}
    R["plo"] = c.line(Bp, Kp, joint="C")
    R["pup"] = c.line(Kp, Fu, joint="T")
    R["fil"] = c.arc(cfil, rf, Fu, Lb, ccw=True, joint="C")
    R["lip"] = c.line(Lb, Lt, joint="C")
    R["outer"] = c.arc((0, 0), n["Rup"], Lt, Pp, ccw=False, joint="C")
    R["pole"] = c.line(Pp, Ph, joint="C")
    hub = c.arc((0, 0), n["Rhub"], Ph, M(Ph), ccw=True, joint="C")
    L["pole"] = c.line(M(Ph), M(Pp), joint="C")
    L["outer"] = c.arc((0, 0), n["Rup"], M(Pp), M(Lt), ccw=False, joint="C")
    L["lip"] = c.line(M(Lt), M(Lb), joint="C")
    L["fil"] = c.arc(M(cfil), rf, M(Lb), M(Fu), ccw=True, joint="T")
    L["pup"] = c.line(M(Fu), M(Kp), joint="C")
    L["plo"] = c.line(M(Kp), M(Bp), joint="C")
    c.close()
    s = SK(sk)
    s.con("Horizontal", chord)
    s.con("Coincident", hub, 3, -1, 1)
    s.con("Radius", hub, n["Rhub"], name="rotor_hub_r", expr=P + "rotor_down_r - " + P + "rotor_house_height")
    ang_fd = P + "magnet_fill_down * " + P + "angle_mag / 2"
    ang_fu = P + "magnet_fill_up * " + P + "angle_mag / 2"
    lip_expr = P + "rotor_up_r * sin(" + P + "magnet_fill_up * " + P + "angle_mag * " + P + "rotor_hole / 2)"
    for side, G in (("R", R), ("L", L)):
        right = side == "R"
        sg = 1 if right else -1
        # construction: nominal magnet sides (radial lines to the lower and upper side end points)
        r4 = s.cline((0, 0), (sg * P4[0], P4[1]))
        rU = s.cline((0, 0), (sg * U[0], U[1]))
        mlo = s.cline((sg * P4[0], P4[1]), (sg * K[0], K[1]))
        mup = s.cline((sg * K[0], K[1]), (sg * U[0], U[1]))
        s.con("Coincident", r4, 1, -1, 1)
        s.con("Coincident", rU, 1, -1, 1)
        s.con("Coincident", mlo, 1, r4, 2)
        s.con("Coincident", mup, 1, mlo, 2)
        s.con("Coincident", mup, 2, rU, 2)
        s.con("Distance", r4, n["Rdn"], name="rotor_down_r_" + side, expr=P + "rotor_down_r")
        s.con("Distance", rU, n["Rup"], name="rotor_up_r_U_" + side, expr=P + "rotor_up_r")
        if right:
            s.con("Angle", r4, math.radians(90 - n["fd"]), name="magnet_fill_down_angle_R", expr="90 deg - " + ang_fd)
            s.con("Angle", rU, math.radians(90 - n["fu"]), name="magnet_fill_up_angle_R", expr="90 deg - " + ang_fu)
        else:
            s.con("Angle", r4, math.radians(90 + n["fd"]), name="magnet_fill_down_angle_L", expr="90 deg + " + ang_fd)
            s.con("Angle", rU, math.radians(90 + n["fu"]), name="magnet_fill_up_angle_L", expr="90 deg + " + ang_fu)
        s.con("Parallel", mlo, G["pole"])
        s.con("Distance", mlo, n["mdh"], name="magnet_down_height_" + side, expr=P + "magnet_down_height")
        if right:
            s.con("PointOnObject", r4, 2, chord)
        s.con("Parallel", G["plo"], mlo)
        s.con("Distance", G["plo"], 1, mlo, off, name="offset_rotor_lo_" + side, expr=P + "offset_rotor")
        s.con("Parallel", G["pup"], mup)
        s.con("Distance", G["pup"], 1, mup, off, name="offset_rotor_up_" + side, expr=P + "offset_rotor")
        s.con("Radius", G["fil"], rf, name="pocket_fillet_r_" + side, expr=P + "magnet_fill_radius + " + P + "offset_rotor")
        s.con("Distance", -1, 1, G["fil"], 3, n["Rtop"] - n["mfr"], name="fillet_centre_r_" + side,
              expr=P + "rotor_up_r - " + P + "magnet_up_gap - " + P + "magnet_fill_radius")
        s.con("Vertical", G["lip"])
        if right:
            s.con("DistanceX", -1, 1, G["lip"], 1, xl, name="lip_x_R", expr=lip_expr)
        else:
            s.con("DistanceX", G["lip"], 1, -1, 1, xl, name="lip_x_L", expr=lip_expr)
        s.con("Coincident", G["outer"], 3, -1, 1)
        s.con("Radius", G["outer"], n["Rup"], name="rotor_up_r_" + side, expr=P + "rotor_up_r")
        s.con("PointOnObject", -1, 1, G["pole"])
        if right:   # right pole line runs outer -> hub
            s.con("Angle", G["pole"], math.radians(270 - a_deg), name="half_pole_angle_R", expr="270 deg - " + P + "angle_mag / 2")
        else:       # left pole line runs hub -> outer
            s.con("Angle", G["pole"], math.radians(90 + a_deg), name="half_pole_angle_L", expr="90 deg + " + P + "angle_mag / 2")
    doc.recompute()

    pad = body.newObject("PartDesign::Pad", "RotorPad")
    pad.Profile = sk
    pad_symmetric(pad)
    pad.setExpression("Length", P + "rotor_w")
    pp = body.newObject("PartDesign::PolarPattern", "RotorPoles")
    pp.Originals = [pad]
    pp.Axis = (sk, ["N_Axis"])
    pp.Angle = 360
    pp.setExpression("Occurrences", P + "num_seg * " + P + "num_poles_per_segment")
    pp.Refine = True
    body.Tip = pp
    sk.Visibility = False
    return dict(sketch=sk, features=[pad, pp])
