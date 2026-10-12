"""Stator core (segmented, 20SW1200): one sector sketch -> Pad (symmetric motor_length) -> PolarPattern (num_seg*3).

Sector (angle 2*angle_wire, centred on +Y): wound tooth (tooth_width) from stator_down_r to the slot top
(stator_up_r - core_thickness), yoke up to stator_up_r, half legs (tooth2_width) at coil_w from the tooth with a foot
along stator_down_r to the sector boundary, foot height cut_width, round notch at the boundary (radius driven by
tangency to the leg, as in Fusion, where 0.82 mm is a driven dimension), fillets stator_fillet_r (yoke corners) and
stator_fillet_r1 (tooth-tip and foot corners; Fusion Fillet14 modelled in the sketch).
"""
import math
from .sketchkit import Chain, SK
from .params_sheet import pval
from .common import P, M, xy_sketch, pad_symmetric


def build_core(doc, sh, body):
    g = lambda n: pval(sh, n)
    R_in, R_out = g("stator_down_r"), g("stator_up_r")
    tw2, cw, ct, aw = g("tooth_width") / 2, g("coil_w"), g("core_thickness"), g("angle_wire")
    cut, t2, fr, f1 = g("cut_width"), g("tooth2_width"), g("stator_fillet_r"), g("stator_fillet_r1")
    y_s = R_out - ct
    x_l = tw2 + cw
    x_o = x_l + t2
    tb = math.radians(90 - aw)
    ub = (math.cos(tb), math.sin(tb))
    B1 = (R_in * ub[0], R_in * ub[1])
    B2 = ((R_in + cut) * ub[0], (R_in + cut) * ub[1])
    rn = ((R_in + cut) * ub[0] - x_o) / (1 - ub[0])
    N = ((R_in + cut + rn) * ub[0], (R_in + cut + rn) * ub[1])
    To = (x_o, N[1])
    cT = (tw2 - f1, math.sqrt((R_in + f1) ** 2 - (tw2 - f1) ** 2))
    tT_arc = (cT[0] * R_in / (R_in + f1), cT[1] * R_in / (R_in + f1))
    tT_line = (tw2, cT[1])
    cF = (x_l + f1, math.sqrt((R_in + f1) ** 2 - (x_l + f1) ** 2))
    tF_line = (x_l, cF[1])
    tF_arc = (cF[0] * R_in / (R_in + f1), cF[1] * R_in / (R_in + f1))
    cO = (x_o - fr, math.sqrt((R_out - fr) ** 2 - (x_o - fr) ** 2))
    tO_line = (x_o, cO[1])
    tO_arc = (cO[0] * R_out / (R_out - fr), cO[1] * R_out / (R_out - fr))

    # Closed loop (counter-clockwise around the material): tooth-tip arc, right side, outer arc, left side.
    # Each side carries its own constraints with the same Params expressions -> symmetric for any parameter set.
    sk = xy_sketch(body, "CoreSectorSketch")
    c = Chain(sk)
    tip = c.arc((0, 0), R_in, M(tT_arc), tT_arc, ccw=False, joint="T")
    R, L = {}, {}
    R["fT"] = c.arc(cT, f1, tT_arc, tT_line, ccw=True, joint="T")
    R["tooth"] = c.line(tT_line, (tw2, y_s), joint="C")
    R["slot"] = c.line((tw2, y_s), (x_l, y_s), joint="C")
    R["leg"] = c.line((x_l, y_s), tF_line, joint="T")
    R["fF"] = c.arc(cF, f1, tF_line, tF_arc, ccw=True, joint="T")
    R["foot"] = c.arc((0, 0), R_in, tF_arc, B1, ccw=False, joint="C")
    R["bnd"] = c.line(B1, B2, joint="C")
    R["notch"] = c.arc(N, rn, B2, To, ccw=False, joint="T")
    R["outleg"] = c.line(To, tO_line, joint="T")
    R["fO"] = c.arc(cO, fr, tO_line, tO_arc, ccw=True, joint="T")
    outer = c.arc((0, 0), R_out, tO_arc, M(tO_arc), ccw=True, joint="T")
    L["fO"] = c.arc(M(cO), fr, M(tO_arc), M(tO_line), ccw=True, joint="T")
    L["outleg"] = c.line(M(tO_line), M(To), joint="T")
    L["notch"] = c.arc(M(N), rn, M(To), M(B2), ccw=False, joint="C")
    L["bnd"] = c.line(M(B2), M(B1), joint="C")
    L["foot"] = c.arc((0, 0), R_in, M(B1), M(tF_arc), ccw=False, joint="T")
    L["fF"] = c.arc(M(cF), f1, M(tF_arc), M(tF_line), ccw=True, joint="T")
    L["leg"] = c.line(M(tF_line), (-x_l, y_s), joint="C")
    L["slot"] = c.line((-x_l, y_s), (-tw2, y_s), joint="C")
    L["tooth"] = c.line((-tw2, y_s), M(tT_line), joint="T")
    L["fT"] = c.arc(M(cT), f1, M(tT_line), M(tT_arc), ccw=True, joint="T")
    c.close()
    s = SK(sk)
    for a in (tip, outer):
        s.con("Coincident", a, 3, -1, 1)
    s.con("Radius", tip, R_in, name="stator_down_r", expr=P + "stator_down_r")
    s.con("Radius", outer, R_out, name="stator_up_r", expr=P + "stator_up_r")
    for side, G in (("R", R), ("L", L)):
        right = side == "R"
        s.con("Coincident", G["foot"], 3, -1, 1)
        s.con("Radius", G["foot"], R_in, name="foot_r_" + side, expr=P + "stator_down_r")
        s.con("Vertical", G["tooth"])
        if right:
            s.con("DistanceX", -1, 1, G["tooth"], 1, tw2, name="tooth_half_width_R", expr=P + "tooth_width / 2")
        else:
            s.con("DistanceX", G["tooth"], 1, -1, 1, tw2, name="tooth_half_width_L", expr=P + "tooth_width / 2")
        s.con("Horizontal", G["slot"])
        s.con("DistanceY", -1, 1, G["slot"], 1, y_s, name="slot_top_y_" + side,
              expr=P + "stator_up_r - " + P + "core_thickness")
        s.con("Vertical", G["leg"])
        s.con("Distance", G["slot"], cw, name="coil_w_" + side, expr=P + "coil_w")
        s.con("Radius", G["fT"], f1, name="tooth_tip_fillet_" + side, expr=P + "stator_fillet_r1")
        s.con("Radius", G["fF"], f1, name="foot_fillet_" + side, expr=P + "stator_fillet_r1")
        s.con("PointOnObject", -1, 1, G["bnd"])
        if right:
            s.con("Angle", G["bnd"], tb, name="sector_boundary_angle_R", expr="90 deg - " + P + "angle_wire")
        else:
            s.con("Angle", G["bnd"], 2 * math.pi - tb, name="sector_boundary_angle_L", expr="270 deg + " + P + "angle_wire")
        s.con("Distance", G["bnd"], cut, name="cut_width_" + side, expr=P + "cut_width")
        s.con("PointOnObject", G["notch"], 3, G["bnd"])
        s.con("Vertical", G["outleg"])
        if right:
            s.con("DistanceX", G["leg"], 1, G["outleg"], 1, t2, name="tooth2_width_R", expr=P + "tooth2_width")
        else:
            s.con("DistanceX", G["outleg"], 1, G["leg"], 1, t2, name="tooth2_width_L", expr=P + "tooth2_width")
        s.con("Radius", G["fO"], fr, name="stator_fillet_r_" + side, expr=P + "stator_fillet_r")
    doc.recompute()

    pad = body.newObject("PartDesign::Pad", "CorePad")
    pad.Profile = sk
    pad_symmetric(pad)
    pad.setExpression("Length", P + "motor_length")
    pp = body.newObject("PartDesign::PolarPattern", "CoreSectors")
    pp.Originals = [pad]
    pp.Axis = (sk, ["N_Axis"])
    pp.Angle = 360
    pp.setExpression("Occurrences", P + "num_seg * 3")
    pp.Refine = True
    body.Tip = pp
    sk.Visibility = False
    return dict(sketch=sk, features=[pad, pp])
