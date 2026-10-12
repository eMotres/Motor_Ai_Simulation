"""Flat-wire coil around one tooth (copper), one PartDesign body; instances by a Draft link polar array.

Built like the Fusion model (Coil component, owner decision 2026-10-12: "coils exactly like Fusion").
Layer k (k = 0 outermost, under the yoke) is a loop of wire_width x wire_height flat wire:
  legs      : two straight legs beside the tooth, inner side x = +-X1, X1 = tooth_width/2 + insulation_thickness +
              wire_spacing_x/2, length motor_length + coil_straight_extra (symmetric)             -> Pad   (Fusion Extrude1)
  end turn  : +z end = two 90-deg bends about axes parallel to Y at x = +-coil_bend_axis_dx (= X1 - wire_beng_r,
              0.05 mm), inner bend radius wire_beng_r (1.72), joined by a straight bridge 2*coil_bend_axis_dx wide
                                                                      -> Revolution 90 + Pad + Revolution 90 (Revolve1,
                                                                         Mirror2, Extrude3)
  transition: -z end, half turn from the right leg of layer k to the left leg of layer k+1 (one pitch lower)
              -> AdditiveLoft through 7 sections on the Fusion loft centre line (arc radius X1 about the tooth axis,
                 Fusion Loft1 / Sketch3 A0).  FreeCAD has no centre-line loft, so the sections sit on the centre line
                 every 30 deg, perpendicular to it; their drop follows the law of Fusion's Loft1 measured on the Fusion
                 STEP (zero slope at both legs): LOFT_LAW below.
Layers 0..n-2 = LinearPattern (num_wires_per_slot - 1) of [legs, end turn, transition]; the last layer (legs + end
turn) is added separately so the coil stays one connected solid for any num_wires_per_slot >= 3.
Leads (-z): layer 0 left leg and layer n-1 right leg extended by coil_lead_ext + coil_lead_len (Extrude6 + Extrude7).
"""
import FreeCAD as App
from .sketchkit import SK
from .params_sheet import pval
from .common import P, xy_sketch, origin_feature, pad_symmetric

X1 = P + "coil_x_in"
YTOP = P + "coil_top_y"
PITCH = P + "coil_layer_pitch"
LC = P + "coil_leg_length"
AX = P + "coil_bend_axis_dx"
# (angle on the centre line [deg], fraction of the layer pitch dropped) - Fusion Loft1 measured on the Fusion STEP
# (fusion_source/Coil_L20_fusion.step, symmetric mean of both halves); linear would be (30, 1/6), (60, 1/3) ...
LOFT_LAW = ((0, 0.0), (30, 0.119), (60, 0.3315), (90, 0.5), (120, 0.6685), (150, 0.881), (180, 1.0))


def _ytop(g, layer_val):
    return g("stator_up_r") - g("core_thickness") - g("insulation_thickness") - g("wire_spacing_y") / 2 \
        - layer_val * (g("wire_height") + g("wire_spacing_y"))


def _ytop_expr(layer_expr, extra=None):
    e = YTOP
    if layer_expr != "0":
        e += " - (" + layer_expr + ") * " + PITCH
    if extra:
        e += " - " + extra + " * " + PITCH
    return e


def _layer_rects(sk, g, layer_expr, layer_val, sides=("R", "L"), tag="", drop=0.0):
    """Wire cross-section rectangle of one leg of one layer with Params-driven constraints.  Every coil sketch holds ONE
    leg (the legs are separate features so the body stays one solid after every step), so there are no left / right
    dimension pairs: the one-sided distance from the axis is `wire_x_in`.
    drop = extra fraction of a pitch (loft sections).  Returns {side: geo ids}."""
    s = SK(sk)
    x1 = g("tooth_width") / 2 + g("insulation_thickness") + g("wire_spacing_x") / 2
    ww, wh = g("wire_width"), g("wire_height")
    ytop = _ytop(g, layer_val) - drop * (wh + g("wire_spacing_y"))
    ytop_expr = _ytop_expr(layer_expr, ("%.6g" % drop) if drop else None)
    out = {}
    for side in sides:
        if side == "R":
            gid = s.rect(x1, ytop - wh, x1 + ww, ytop)
            s.con("DistanceX", -1, 1, gid[3], 1, x1, name="wire_x_in" + tag, expr=X1)
        else:
            gid = s.rect(-x1 - ww, ytop - wh, -x1, ytop)
            s.con("DistanceX", gid[1], 1, -1, 1, x1, name="wire_x_in" + tag, expr=X1)
        s.con("Distance", gid[0], ww, name="wire_width" + tag, expr=P + "wire_width")
        s.con("DistanceY", -1, 1, gid[2], 1, ytop, name="layer_top_y" + tag, expr=ytop_expr)
        s.con("Distance", gid[1], wh, name="wire_height" + tag, expr=P + "wire_height")
        out[side] = gid
    return out


def _bend_axis(sk, g, rect, side, tag):
    """Vertical construction line at x = +-coil_bend_axis_dx spanning the wire rectangle (revolution axis 'Axis0')."""
    s = SK(sk)
    ax = g("coil_bend_axis_dx") * (1 if side == "R" else -1)
    y0 = sk.Geometry[rect[0]].StartPoint.y
    y1 = sk.Geometry[rect[2]].StartPoint.y
    ln = s.cline((ax, y0), (ax, y1))
    import Sketcher
    sk.addConstraint(Sketcher.Constraint("Vertical", ln))
    sk.addConstraint(Sketcher.Constraint("PointOnObject", ln, 1, rect[0]))
    sk.addConstraint(Sketcher.Constraint("PointOnObject", ln, 2, rect[2]))
    if side == "R":
        s.con("DistanceX", -1, 1, ln, 1, ax, name="bend_axis_x" + tag, expr=AX)
    else:
        s.con("DistanceX", ln, 1, -1, 1, -ax, name="bend_axis_x" + tag, expr=AX)
    return ln


def build_coil(doc, sh, body):
    g = lambda n: pval(sh, n)
    n = int(round(g("num_wires_per_slot")))
    feats = []
    zend = g("motor_length") / 2 + g("coil_straight_extra") / 2

    def leg(name, side, layer_expr, layer_val, tag):
        sk = xy_sketch(body, name + "Sketch")
        _layer_rects(sk, g, layer_expr, layer_val, sides=(side,), tag=tag)
        pad = body.newObject("PartDesign::Pad", name)
        pad.Profile = sk
        pad_symmetric(pad)
        pad.setExpression("Length", LC)
        return pad

    def bend(name, side, layer_expr, layer_val, tag):
        """90-deg bend at +z about x = +-coil_bend_axis_dx."""
        sk = xy_sketch(body, name + "Sketch", z_expr=LC + " / 2")
        r = _layer_rects(sk, g, layer_expr, layer_val, sides=(side,), tag=tag)[side]
        _bend_axis(sk, g, r, side, tag)
        rev = body.newObject("PartDesign::Revolution", name)
        rev.Profile = sk
        rev.ReferenceAxis = (sk, ["Axis0"])
        rev.Angle = 90
        doc.recompute()
        ax = g("coil_bend_axis_dx")
        for flip in (False, True):
            rev.Reversed = flip
            doc.recompute()
            bb = rev.AddSubShape.BoundBox
            if rev.isValid() and bb.ZMin > zend - 1e-6 and (bb.XMin > ax - 1e-6 if side == "R" else bb.XMax < -ax + 1e-6):
                return rev
        raise RuntimeError("bend %s orientation not found" % name)

    def bridge(name, layer_expr, layer_val, tag):
        """Straight piece between the two bends: YZ sketch (u = global y, v = global z), Pad 2*coil_bend_axis_dx."""
        sk = body.newObject("Sketcher::SketchObject", name + "Sketch")
        sk.AttachmentSupport = [(origin_feature(body, "YZ_Plane"), "")]
        sk.MapMode = "FlatFace"
        s = SK(sk)
        yt = _ytop(g, layer_val)
        z0 = zend + g("wire_beng_r")
        gid = s.rect(yt - g("wire_height"), z0, yt, z0 + g("wire_width"))
        s.con("DistanceX", -1, 1, gid[2], 1, yt, name="bridge_layer_top" + tag, expr=_ytop_expr(layer_expr))
        s.con("Distance", gid[0], g("wire_height"), name="bridge_wire_height" + tag, expr=P + "wire_height")
        s.con("DistanceY", -1, 1, gid[0], 1, z0, name="bridge_z_in" + tag, expr=LC + " / 2 + " + P + "wire_beng_r")
        s.con("Distance", gid[1], g("wire_width"), name="bridge_wire_width" + tag, expr=P + "wire_width")
        pad = body.newObject("PartDesign::Pad", name)
        pad.Profile = sk
        pad_symmetric(pad)
        pad.setExpression("Length", P + "coil_bridge_length")
        return pad

    def end_turn(prefix, layer_expr, layer_val, tag, first="R"):
        order = ("R", "B", "L") if first == "R" else ("L", "B", "R")
        out = []
        for part in order:
            if part == "B":
                out.append(bridge(prefix + "Bridge", layer_expr, layer_val, tag))
            else:
                out.append(bend(prefix + "Bend" + part, part, layer_expr, layer_val, tag))
            doc.recompute()
        return out

    def transition(name):
        """Loft from the right leg of layer 0 (theta 0) to the left leg of layer 1 (theta 180) at -z."""
        sks = []
        for i, (deg, drop) in enumerate(LOFT_LAW):
            sk = body.newObject("Sketcher::SketchObject", "%sSection%d" % (name, i))
            sk.AttachmentSupport = [(origin_feature(body, "XY_Plane"), "")]
            sk.MapMode = "FlatFace"
            sk.AttachmentOffset = App.Placement(App.Vector(0, 0, -zend), App.Rotation(App.Vector(0, 1, 0), deg))
            sk.setExpression(".AttachmentOffset.Base.z", "-" + LC + " / 2")
            _layer_rects(sk, g, "0", 0, sides=("R",), tag="_s%d" % i, drop=drop)
            sks.append(sk)
        lo = body.newObject("PartDesign::AdditiveLoft", name)
        lo.Profile = sks[0]
        lo.Sections = sks[1:]
        lo.Ruled = False
        lo.Closed = False
        return lo

    r0 = leg("CoilRightLeg", "R", "0", 0, "")
    et0 = end_turn("CoilEnd", "0", 0, "", first="R")
    l0 = leg("CoilLeftLeg", "L", "0", 0, "")
    tr = transition("CoilTransition")
    doc.recompute()
    feats += [r0] + et0 + [l0, tr]
    # layers 0..n-2
    lp = body.newObject("PartDesign::LinearPattern", "CoilLayers")
    lp.Originals = [r0] + et0 + [l0, tr]
    lp.Direction = (r0.Profile[0], ["V_Axis"])
    lp.Reversed = True
    lp.setExpression("Length", P + "coil_layer_span")
    lp.setExpression("Occurrences", P + "coil_layer_copies")
    body.Tip = lp
    doc.recompute()
    feats.append(lp)
    # last layer (n-1): left leg (lands on the last transition), end turn from the left, right leg
    last = P + "num_wires_per_slot - 1"
    ll = leg("CoilLastLeftLeg", "L", last, n - 1, "_last")
    etl = end_turn("CoilLastEnd", last, n - 1, "_last", first="L")
    lr = leg("CoilLastRightLeg", "R", last, n - 1, "_last")
    feats += [ll] + etl + [lr]
    # leads at -z: layer 0 left leg, last layer right leg
    lead_len = P + "coil_lead_total"
    sk_li = xy_sketch(body, "CoilLeadInSketch", z_expr="-" + LC + " / 2")
    _layer_rects(sk_li, g, "0", 0, sides=("L",), tag="_lead")
    li = body.newObject("PartDesign::Pad", "CoilLeadIn")
    li.Profile = sk_li
    li.Reversed = True
    li.setExpression("Length", lead_len)
    sk_lo = xy_sketch(body, "CoilLeadOutSketch", z_expr="-" + LC + " / 2")
    _layer_rects(sk_lo, g, last, n - 1, sides=("R",), tag="_lead")
    lo = body.newObject("PartDesign::Pad", "CoilLeadOut")
    lo.Profile = sk_lo
    lo.Reversed = True
    lo.setExpression("Length", lead_len)
    lo.Refine = True
    feats += [li, lo]
    body.Tip = lo
    for o in body.Group:
        if o.TypeId == "Sketcher::SketchObject":
            o.Visibility = False
    doc.recompute()
    sketches = [o for o in body.Group if o.TypeId == "Sketcher::SketchObject"]
    return dict(sketches=sketches, features=feats)
