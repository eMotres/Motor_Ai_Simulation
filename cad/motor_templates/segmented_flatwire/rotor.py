"""Rotor (spoke type, steel): one pole-pitch sketch -> Pad (symmetric rotor_w) -> PolarPattern (num_seg*num_poles_per_segment).

Pole pitch (angle_mag, centred on +Y = magnet centre line): hub ring segment (rotor_down_r - rotor_house_height ..
rotor_down_r), two half poles up to rotor_up_r, magnet pocket open to the air gap between lips at
+-rotor_up_r*sin(magnet_fill_up*angle_mag*rotor_hole/2).  Pocket sides = nominal magnet sides offset outwards by
offset_rotor (Fusion OffsetFaces3); pocket bottom and lips are not offset (checked against the Fusion B-rep faces);
top fillet = magnet_fill_radius + offset_rotor about the nominal fillet centre.

Keys (template A.5.0, CIANO28 85: Fusion Extrude5 + Fillet7 + OffsetFaces6 + C-Pattern5): `rotor_key_count` > 0 adds keys in
the bore - one sketch (half profile + mirror: tip line, tip corner fillets, key side, base fillets into the bore circle,
bore arc) -> Pad (symmetric rotor_w) -> PolarPattern (rotor_key_count, first key on the +Y pole axis).  Key width
`rotor_key_w` and depth `rotor_key_h` are nominal; the three faces (sides, tip) are moved inwards by `rotor_key_offset`
(Fusion OffsetFaces6); all four fillets have the radius `rotor_fill_r` (one dimension + Equal).  Profiles without
`rotor_key_count` (CIANO14 40: Fusion Extrude5 suppressed, Fillet7 / OffsetFaces6 without effect) get no key.
"""
import math
from .sketchkit import Chain, SK, V_AXIS
from .params_sheet import pval, has_param
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
    rt = g("rotor_tip_fill_r") if has_param(sh, "rotor_tip_fill_r") else 0.0     # pole-tip rounding (Fusion Fillet8 = rotor_r; sim rotor_fill_r)
    if rt > 1e-12:
        Ct = (xl + rt, math.sqrt((n["Rup"] - rt) ** 2 - (xl + rt) ** 2))        # centre: tangent to the lip line (x = xl) inside the Rup circle
        T1 = (xl, Ct[1])                                                        # tangent point on the lip line
        kk = n["Rup"] / (n["Rup"] - rt)
        T2 = (Ct[0] * kk, Ct[1] * kk)                                           # tangent point on the outer arc
    else:
        T1 = T2 = Lt
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
    R["lip"] = c.line(Lb, T1, joint="T" if rt > 1e-12 else "C")
    if rt > 1e-12:
        R["tip"] = c.arc(Ct, rt, T1, T2, ccw=False, joint="T")
    R["outer"] = c.arc((0, 0), n["Rup"], T2, Pp, ccw=False, joint="C")
    R["pole"] = c.line(Pp, Ph, joint="C", rev=True)          # drawn hub -> outer so its angle to the pole axis is +half_pole_angle
    hub = c.arc((0, 0), n["Rhub"], Ph, M(Ph), ccw=True, joint="C")
    L["pole"] = c.line(M(Ph), M(Pp), joint="C")
    L["outer"] = c.arc((0, 0), n["Rup"], M(Pp), M(T2), ccw=False, joint="C")
    if rt > 1e-12:
        L["tip"] = c.arc(M(Ct), rt, M(T2), M(T1), ccw=False, joint="C")
    L["lip"] = c.line(M(T1), M(Lb), joint="C")
    L["fil"] = c.arc(M(cfil), rf, M(Lb), M(Fu), ccw=True, joint="C")
    L["pup"] = c.line(M(Fu), M(Kp), joint="C")
    L["plo"] = c.line(M(Kp), M(Bp), joint="C")
    c.close()
    s = SK(sk)
    # The right half carries the dimensions; the left half is its mirror image about the pole axis (Y): one Symmetric
    # constraint per node, Equal radius per arc.  The chord is symmetric about Y (its ends), not "horizontal".
    s.mirror_chain(c, R, {k: L[k] for k in R})
    s.con("Symmetric", chord, 1, chord, 2, V_AXIS)           # the chord crosses the axis: symmetric ends, no dimension
    s.con("Coincident", hub, 3, -1, 1)
    s.con("Radius", hub, n["Rhub"], name="rotor_hub_r", expr=P + "rotor_down_r - " + P + "rotor_house_height")
    ang_fd = P + "magnet_fill_down * " + P + "angle_mag / 2"       # half angles from the pole axis, as in the Fusion sketch
    ang_fu = P + "magnet_fill_up * " + P + "angle_mag / 2"
    lip_expr = P + "rotor_up_r * sin(" + P + "magnet_fill_up * " + P + "angle_mag * " + P + "rotor_hole / 2)"
    # construction: nominal magnet sides of the right half (radial lines to the lower and upper side end points)
    r4 = s.cline((0, 0), (P4[0], P4[1]))
    rU = s.cline((0, 0), (U[0], U[1]))
    mlo = s.cline((P4[0], P4[1]), (K[0], K[1]))
    mup = s.cline((K[0], K[1]), (U[0], U[1]))
    s.con("Coincident", r4, 1, -1, 1)
    s.con("Coincident", rU, 1, -1, 1)
    s.con("Coincident", mlo, 1, r4, 2)
    s.con("Coincident", mup, 1, mlo, 2)
    s.con("Coincident", mup, 2, rU, 2)
    s.con("Distance", r4, n["Rdn"], name="rotor_down_r", expr=P + "rotor_down_r")
    s.con("Distance", rU, n["Rup"], name="rotor_up_r_U", expr=P + "rotor_up_r")
    s.con("Angle", r4, V_AXIS, math.radians(n["fd"]), name="magnet_fill_down_angle", expr=ang_fd)
    s.con("Angle", rU, V_AXIS, math.radians(n["fu"]), name="magnet_fill_up_angle", expr=ang_fu)
    s.con("Parallel", mlo, R["pole"])
    s.con("Distance", mlo, n["mdh"], name="magnet_down_height", expr=P + "magnet_down_height")
    s.con("PointOnObject", r4, 2, chord)
    s.con("Parallel", R["plo"], mlo)
    s.con("Distance", R["plo"], 1, mlo, off, name="offset_rotor_lo", expr=P + "offset_rotor")
    s.con("Parallel", R["pup"], mup)
    s.con("Distance", R["pup"], 1, mup, off, name="offset_rotor_up", expr=P + "offset_rotor")
    s.con("Radius", R["fil"], rf, name="pocket_fillet_r", expr=P + "magnet_fill_radius + " + P + "offset_rotor")
    s.con("Distance", -1, 1, R["fil"], 3, n["Rtop"] - n["mfr"], name="fillet_centre_r",
          expr=P + "rotor_up_r - " + P + "magnet_up_gap - " + P + "magnet_fill_radius")
    if rt > 1e-12:
        s.con("Radius", R["tip"], rt, name="rotor_tip_fill_r", expr=P + "rotor_tip_fill_r")
    s.con("Vertical", R["lip"])
    s.con("DistanceX", -1, 1, R["lip"], 1, xl, name="lip_x", expr=lip_expr)
    s.con("Coincident", R["outer"], 3, -1, 1)
    s.con("Radius", R["outer"], n["Rup"], name="rotor_up_r", expr=P + "rotor_up_r")
    s.con("PointOnObject", -1, 1, R["pole"])
    s.con("PointOnObject", -1, 1, L["pole"])                  # the mirrored pole line also runs through the axis origin
    s.con("Angle", R["pole"], V_AXIS, math.radians(a_deg), name="half_pole_angle", expr=P + "angle_mag / 2")
    doc.recompute()

    pad = body.newObject("PartDesign::Pad", "RotorPad")
    pad.Profile = sk
    pad_symmetric(pad)
    pad.setExpression("Length", P + "rotor_w")
    pp = body.newObject("PartDesign::PolarPattern", "RotorPoles")
    pp.Originals = [pad]
    pp.Axis = (sk, ["N_Axis"])
    pp.Angle = 360
    pp.setExpression("Occurrences", P + "rotor_poles")
    pp.Refine = True
    body.Tip = pp
    sk.Visibility = False
    out = dict(sketch=sk, features=[pad, pp], extra_sketches=[])
    if has_param(sh, "rotor_key_count") and round(g("rotor_key_count")) > 0:
        ksk, kfeat = build_keys(doc, sh, body, g)
        out["extra_sketches"].append(ksk)
        out["features"] += kfeat
    return out


def build_keys(doc, sh, body, g):
    """Rotor keys: see the module docstring.  Right half is dimensioned, the left half is its mirror (Symmetric / Equal)."""
    Rb = g("rotor_down_r") - g("rotor_house_height")                  # bore radius (= hub inner radius)
    kfn = "rotor_key_fill_r" if has_param(sh, "rotor_key_fill_r") else "rotor_fill_r"      # G2: the key fillet is its own row (rotor_fill_r = pole tips)
    koff, kw, kh, fr = g("rotor_key_offset"), g("rotor_key_w"), g("rotor_key_h"), g(kfn)
    w2, yt = kw / 2 - koff, Rb - kh + koff                              # side x, tip y after the offset
    A = (w2 - fr, yt)                                                   # tip line end / tip fillet start
    C1 = (w2 - fr, yt + fr)
    B = (w2, yt + fr)                                                   # tip fillet end / side start
    C2y = math.sqrt((Rb - fr) ** 2 - (w2 + fr) ** 2)
    C2 = (w2 + fr, C2y)                                                 # base fillet centre (tangent to the side and the bore circle)
    S = (w2, C2y)                                                       # side end / base fillet start
    k = (Rb) / math.hypot(*C2)
    E = (C2[0] * k, C2[1] * k)                                          # base fillet end on the bore circle

    sk = xy_sketch(body, "RotorKeySketch")
    c = Chain(sk)
    tip = c.line(M(A), A, joint="T")
    R, L = {}, {}
    R["f1"] = c.arc(C1, fr, A, B, ccw=True, joint="T")
    R["side"] = c.line(B, S, joint="T")
    R["f2"] = c.arc(C2, fr, S, E, ccw=False, joint="T")
    bore = c.arc((0, 0), Rb, E, M(E), ccw=True, joint="T")
    L["f2"] = c.arc(M(C2), fr, M(E), M(S), ccw=False, joint="T")
    L["side"] = c.line(M(S), M(B), joint="T")
    L["f1"] = c.arc(M(C1), fr, M(B), M(A), ccw=True, joint="T")
    c.close()
    s = SK(sk)
    s.mirror_chain(c, R, {k_: L[k_] for k_ in R})
    # the left base fillet is fixed by its two symmetric end nodes and the bore tangency: its Equal would be redundant
    for i, cn in enumerate(sk.Constraints):
        if cn.Type == "Equal" and {cn.First, cn.Second} == {R["f2"], L["f2"]}:
            sk.delConstraint(i)
            break
    s.con("Coincident", bore, 3, -1, 1)          # (the tip line is horizontal through the mirrored, tangent tip fillets)
    s.con("Radius", bore, Rb, name="key_bore_r", expr=P + "rotor_down_r - " + P + "rotor_house_height")
    s.con("DistanceY", -1, 1, tip, 1, yt, name="key_tip_y",
          expr=P + "rotor_down_r - " + P + "rotor_house_height - " + P + "rotor_key_h + " + P + "rotor_key_offset")
    s.con("Vertical", R["side"])
    s.con("DistanceX", -1, 1, R["side"], 1, w2, name="key_side_x", expr=P + "rotor_key_w / 2 - " + P + "rotor_key_offset")
    s.con("Radius", R["f1"], fr, name="rotor_fill_r", expr=P + kfn)           # one fillet dimension
    s.con("Equal", R["f1"], R["f2"])
    doc.recompute()
    pad = body.newObject("PartDesign::Pad", "RotorKeyPad")
    pad.Profile = sk
    pad_symmetric(pad)
    pad.setExpression("Length", P + "rotor_w")
    pad.Refine = True
    pp = body.newObject("PartDesign::PolarPattern", "RotorKeys")
    pp.Originals = [pad]
    pp.Axis = (sk, ["N_Axis"])
    pp.Angle = 360
    pp.setExpression("Occurrences", P + "rotor_key_count")
    pp.Refine = True
    body.Tip = pp
    sk.Visibility = False
    return sk, [pad, pp]
