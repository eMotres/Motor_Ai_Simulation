"""Magnet (spoke magnet, one body) -> Pad mag_step from z = -motor_length/2.  Instances: Draft link polar array.

Outline = nominal pocket shape of the rotor sketch (Fusion Magnet/Sketch1): bottom chord through the point of
rotor_down_r at magnet_fill_down*angle_mag/2, lower side parallel to the pole line (magnet_down_height), upper side to
the point of rotor_up_r at magnet_fill_up*angle_mag/2, top arc rotor_up_r - magnet_up_gap, all corners filleted with
magnet_fill_radius.
"""
import math
from .sketchkit import Chain, SK
from .params_sheet import pval
from .common import P, M, xy_sketch, fillet_center_circle_line, foot_point, corner_center, rotor_numbers


def build_magnet(doc, sh, body):
    g = lambda n: pval(sh, n)
    n = rotor_numbers(g)
    P4, K, U, r = n["P4"], n["K"], n["U"], n["mfr"]
    yb = P4[1]
    c_bot = corner_center((0, yb), P4, K, r)
    c_knee = corner_center(P4, K, U, r)
    c_top = fillet_center_circle_line(n["Rtop"] - r, K, U, r)
    b_bot = (c_bot[0], yb)
    b_lo1 = foot_point(c_bot, P4, K)
    b_lo2 = foot_point(c_knee, P4, K)
    b_up1 = foot_point(c_knee, K, U)
    b_up2 = foot_point(c_top, K, U)
    rr = math.hypot(*c_top)
    s_top = (c_top[0] * n["Rtop"] / rr, c_top[1] * n["Rtop"] / rr)

    sk = xy_sketch(body, "MagnetSketch", z_expr="-" + P + "motor_length / 2")
    c = Chain(sk)
    bot = c.line(M(b_bot), b_bot, joint="T")
    R, L = {}, {}
    R["f1"] = c.arc(c_bot, r, b_bot, b_lo1, ccw=True, joint="T")
    R["lo"] = c.line(b_lo1, b_lo2, joint="T")
    R["f2"] = c.arc(c_knee, r, b_lo2, b_up1, ccw=True, joint="T")
    R["up"] = c.line(b_up1, b_up2, joint="T")
    R["f3"] = c.arc(c_top, r, b_up2, s_top, ccw=True, joint="T")
    top = c.arc((0, 0), n["Rtop"], s_top, M(s_top), ccw=True, joint="T")
    L["f3"] = c.arc(M(c_top), r, M(s_top), M(b_up2), ccw=True, joint="T")
    L["up"] = c.line(M(b_up2), M(b_up1), joint="T")
    L["f2"] = c.arc(M(c_knee), r, M(b_up1), M(b_lo2), ccw=True, joint="T")
    L["lo"] = c.line(M(b_lo2), M(b_lo1), joint="T")
    L["f1"] = c.arc(M(c_bot), r, M(b_lo1), M(b_bot), ccw=True, joint="T")
    c.close()
    s = SK(sk)
    s.con("Horizontal", bot)
    s.con("Coincident", top, 3, -1, 1)
    s.con("Radius", top, n["Rtop"], name="magnet_top_r", expr=P + "rotor_up_r - " + P + "magnet_up_gap")
    ang_fd = P + "magnet_fill_down * " + P + "angle_mag / 2"
    ang_fu = P + "magnet_fill_up * " + P + "angle_mag / 2"
    for side, G in (("R", R), ("L", L)):
        right = side == "R"
        sg = 1 if right else -1
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
        s.con("Distance", rU, n["Rup"], name="rotor_up_r_" + side, expr=P + "rotor_up_r")
        if right:
            s.con("Angle", r4, math.radians(90 - n["fd"]), name="magnet_fill_down_angle_R", expr="90 deg - " + ang_fd)
            s.con("Angle", rU, math.radians(90 - n["fu"]), name="magnet_fill_up_angle_R", expr="90 deg - " + ang_fu)
            s.con("Angle", mlo, math.radians(90 - n["a"]), name="magnet_side_angle_R", expr="90 deg - " + P + "angle_mag / 2")
            s.con("PointOnObject", r4, 2, bot)
        else:
            s.con("Angle", r4, math.radians(90 + n["fd"]), name="magnet_fill_down_angle_L", expr="90 deg + " + ang_fd)
            s.con("Angle", rU, math.radians(90 + n["fu"]), name="magnet_fill_up_angle_L", expr="90 deg + " + ang_fu)
            s.con("Angle", mlo, math.radians(90 + n["a"]), name="magnet_side_angle_L", expr="90 deg + " + P + "angle_mag / 2")
        s.con("Distance", mlo, n["mdh"], name="magnet_down_height_" + side, expr=P + "magnet_down_height")
        s.con("PointOnObject", G["lo"], 1, mlo)
        s.con("PointOnObject", G["lo"], 2, mlo)
        s.con("PointOnObject", G["up"], 1, mup)
        s.con("PointOnObject", G["up"], 2, mup)
        for i, k in enumerate(("f1", "f2", "f3")):
            s.con("Radius", G[k], r, name="magnet_fillet_%d_%s" % (i + 1, side), expr=P + "magnet_fill_radius")
    doc.recompute()

    pad = body.newObject("PartDesign::Pad", "MagnetPad")
    pad.Profile = sk
    pad.setExpression("Length", P + "mag_step")
    pad.Refine = True
    body.Tip = pad
    sk.Visibility = False
    return dict(sketch=sk, features=[pad])
