"""Validity domain (pre-check) and the mandatory health gate of the segmented_flatwire template.

precheck(g)      - pure Python, g(name) -> value (mm / deg / unitless).  Returns a list of violations in words.
                   Run BEFORE building and from the owner macro (macros/CheckMotorParams.FCMacro).
gate(doc)        - run AFTER every recompute and BEFORE any save-as-result / STEP / datum export.  Fails on:
                   invalid / error / not-recomputed objects, sketch solver failure or loss of full constraint,
                   wrong body / instance counts, null / invalid / multi-solid / zero-volume bodies, pre-check
                   violations, and stale geometry (a failed feature keeps its OLD valid shape in FreeCAD, so the
                   shapes are compared with what the current Params demand).
"""
import math

TOL = 1e-4   # mm


def _num(g, name):
    return float(g(name))


def precheck(g):
    """Necessary conditions for this template; each violation is a sentence naming the parameters."""
    v = []

    def need(cond, text):
        if not cond:
            v.append(text)

    # integer counts and fixed-topology parameters
    for n in ("num_seg", "num_poles_per_segment", "num_wires_per_slot", "num_slots_per_segment"):
        x = _num(g, n)
        need(abs(x - round(x)) < 1e-9 and x >= 1, "%s = %g must be a positive integer" % (n, x))
    need(round(_num(g, "num_wires_per_slot")) >= 3,
         "num_wires_per_slot = %g: the coil builder needs at least 3 layers" % _num(g, "num_wires_per_slot"))
    need(round(_num(g, "num_slots_per_segment")) == 6,
         "num_slots_per_segment = %g is not supported: the template (like the Fusion model, angle_wire = 360/(num_seg*6)) "
         "has 6 slots = 3 wound teeth per segment" % _num(g, "num_slots_per_segment"))
    for n in ("magnet_lamination", "sleeve_thickness", "rotor_fill_r"):
        need(abs(_num(g, n)) < 1e-12, "%s = %g is not supported by the template (must be 0)" % (n, _num(g, n)))

    # stator core sector
    R_in, R_out = _num(g, "stator_down_r"), _num(g, "stator_up_r")
    tw2, cw, ct = _num(g, "tooth_width") / 2, _num(g, "coil_w"), _num(g, "core_thickness")
    aw, cut, t2 = _num(g, "angle_wire"), _num(g, "cut_width"), _num(g, "tooth2_width")
    fr, f1 = _num(g, "stator_fillet_r"), _num(g, "stator_fillet_r1")
    s = math.sin(math.radians(aw))
    x_l, x_o = tw2 + cw, tw2 + cw + t2
    y_s = R_out - ct
    need(R_in > 0 and R_out > R_in, "stator_down_r (%.4g) must be > 0 and < stator_up_r (%.4g)" % (R_in, R_out))
    rn = ((R_in + cut) * s - x_o) / (1 - s) if s < 1 else -1
    need(rn > 0.01, "core notch radius = ((stator_down_r + cut_width)*sin(angle_wire) - (tooth_width/2 + coil_w + "
                    "tooth2_width)) / (1 - sin(angle_wire)) = %.3f mm must be > 0: the half leg (outer side x = %.3f mm) "
                    "does not fit in the sector (half width %.3f mm at r = stator_down_r + cut_width); reduce "
                    "tooth_width / coil_w / tooth2_width or increase stator_diameter / reduce num_seg"
                    % (rn, x_o, (R_in + cut) * s))
    need(x_l < R_in * s, "slot outer side x = tooth_width/2 + coil_w = %.3f mm must be inside the sector at "
                         "r = stator_down_r (%.3f mm)" % (x_l, R_in * s))
    need(tw2 > f1 > 0, "stator_fillet_r1 (%.3g) must be > 0 and < tooth_width/2 (%.3g)" % (f1, tw2))
    y_tip = math.sqrt(max(R_in ** 2 - tw2 ** 2, 0))
    need(y_s > y_tip + f1, "slot top (stator_up_r - core_thickness = %.3f) must be above the tooth tip (%.3f)" % (y_s, y_tip + f1))
    need(x_o - fr > 0 and x_o < R_out * s, "outer leg x = %.3f mm must be inside the sector at stator_up_r and > stator_fillet_r" % x_o)
    if rn > 0:
        Ny = (R_in + cut + rn) * math.cos(math.radians(aw))
        need(Ny + rn < y_s, "core notch (top y = %.3f) must stay below the slot top y = %.3f" % (Ny + rn, y_s))

    # winding
    ins, sx, sy = _num(g, "insulation_thickness"), _num(g, "wire_spacing_x"), _num(g, "wire_spacing_y")
    ww, wh, n = _num(g, "wire_width"), _num(g, "wire_height"), round(_num(g, "num_wires_per_slot"))
    x1 = tw2 + ins + sx / 2
    need(x1 + ww <= x_l + 1e-9, "wire outer side x = %.3f mm exceeds the slot (tooth_width/2 + coil_w = %.3f mm)" % (x1 + ww, x_l))
    stack = ins + sy / 2 + n * (wh + sy) - sy
    need(y_s - stack > y_tip, "winding stack (%d layers, %.3f mm) reaches below the tooth tip: inner layer y = %.3f, tooth "
                              "tip y = %.3f" % (n, stack, y_s - stack, y_tip))
    need(wh > 0 and ww > 0, "wire_width and wire_height must be > 0")
    ax = _num(g, "coil_bend_axis_dx")
    need(0 < ax < x1 - 1e-3, "end-turn bend axis x = coil_bend_axis_dx = tooth_width/2 + insulation_thickness + "
                             "wire_spacing_x/2 - wire_beng_r = %.3f mm must be > 0 (bridge 2x wide) and < the leg inner "
                             "side x = %.3f mm (wire_beng_r > 0)" % (ax, x1))

    # rotor / magnets
    Rup, Rdn = _num(g, "rotor_up_r"), _num(g, "rotor_down_r")
    Rhub = Rdn - _num(g, "rotor_house_height")
    a = _num(g, "angle_mag") / 2
    fd = _num(g, "magnet_fill_down") * a
    fu = _num(g, "magnet_fill_up") * a
    h = fu * _num(g, "rotor_hole")
    need(Rup < R_in, "rotor_up_r (%.4g) must be < stator_down_r (%.4g) (air gap)" % (Rup, R_in))
    need(0 < Rhub < Rdn < Rup, "need 0 < rotor_down_r - rotor_house_height (%.4g) < rotor_down_r (%.4g) < rotor_up_r (%.4g)"
                               % (Rhub, Rdn, Rup))
    need(0 < h < fu < fd < a, "magnet angles must satisfy 0 < lip (%.3f) < magnet top (%.3f) < magnet bottom (%.3f) < half "
                              "pole pitch (%.3f) deg (magnet_fill_up, rotor_hole, magnet_fill_down)" % (h, fu, fd, a))
    off, mfr, mdh = _num(g, "offset_rotor"), _num(g, "magnet_fill_radius"), _num(g, "magnet_down_height")
    need(off >= 0 and mfr > 0 and mdh > 0, "offset_rotor >= 0, magnet_fill_radius > 0, magnet_down_height > 0 required")
    need(Rdn + mdh < Rup - _num(g, "magnet_up_gap") - 2 * mfr,
         "magnet_down_height (%.3g) too large for the magnet height (rotor_up_r - rotor_down_r = %.3g)" % (mdh, Rup - Rdn))
    need(_num(g, "mag_step") > 0 and _num(g, "rotor_w") > 0 and _num(g, "motor_length") > 0,
         "motor_length, rotor_w and mag_step must be > 0")
    try:
        _num(g, "holder_face_z")
    except Exception:
        return v                                   # profile without the mechanical parts
    v += precheck_mechanics(g)
    return v


def precheck_mechanics(g):
    """Validity domain of the mechanical parts (stage 3): the topology the sketches assume, in words."""
    v = []

    def need(cond, text):
        if not cond:
            v.append(text)

    rb, rj, rs, rsh = _num(g, "shaft_bore_r"), _num(g, "shaft_journal_r"), _num(g, "shaft_seat_r"), _num(g, "shaft_shoulder_r")
    ch, rt, rtip = _num(g, "shaft_chamfer"), _num(g, "shaft_thread_r"), _num(g, "shaft_tip_r")
    rfl = rs + _num(g, "shaft_flange_step")
    need(0 < rb < rj - ch, "shaft bore radius (shaft_seat_r - shaft_height = %.3f) must be > 0 and < journal radius - chamfer "
                           "(%.3f): the shaft wall vanishes" % (rb, rj - ch))
    need(rj < rsh < rfl, "shaft shoulder radius %.3f must lie between the journal (%.3f) and the flange (%.3f)" % (rsh, rj, rfl))
    need(rj + ch < rs, "bearing bore / 2 (%.3f) + chamfer must be < the rotor seat radius (rotor_down_r - rotor_house_height = "
                       "%.3f)" % (rj, rs))
    need(rtip <= rt < rj - ch, "thread radius %.3f must be >= tip radius %.3f and < journal radius - chamfer" % (rt, rtip))
    need(_num(g, "shaft_flat_w") / 2 < rj, "drive flats (shaft_flat_w / 2 = %.3f) must cut the journal (r %.3f)"
         % (_num(g, "shaft_flat_w") / 2, rj))
    zj = _num(g, "shaft_journal_end_z")
    zbore = zj - rb / math.tan(math.radians(_num(g, "shaft_bore_tip_angle")))
    need(zbore > _num(g, "holder_face_z"), "the bore tip cone (bore ends at z %.3f) must end in front of the front bearing (z %.3f)"
         % (zbore, _num(g, "holder_face_z")))
    need(_num(g, "shaft_flat_len") < zj - _num(g, "front_holder_end_z"),
         "drive flats (%.3f long) must stay in front of the front holder (journal end %.3f, holder end %.3f)"
         % (_num(g, "shaft_flat_len"), zj, _num(g, "front_holder_end_z")))
    d, D, B = _num(g, "bearing_d"), _num(g, "bearing_D"), _num(g, "bearing_B")
    li, lo, bd = _num(g, "bearing_inner_land_d") / 2, _num(g, "bearing_outer_land_d") / 2, _num(g, "bearing_ball_d") / 2
    pr, e = (d + D) / 4, _num(g, "bearing_edge_r")
    need(d / 2 + e < li < pr < lo < D / 2 - e, "bearing radii out of order: bore %.3f < inner land %.3f < pitch %.3f < outer "
                                               "land %.3f < outer %.3f" % (d / 2, li, pr, lo, D / 2))
    need(pr - li < bd and lo - pr < bd and bd < B / 2, "ball (d %.3f) must cut both lands and fit the width B %.3f" % (2 * bd, B))
    n = _num(g, "bearing_ball_count")
    need(abs(n - round(n)) < 1e-9 and n >= 3 and 2 * pr * math.sin(math.pi / max(n, 1)) > 2 * bd,
         "bearing_ball_count = %g: must be an integer >= 3 and the balls must not overlap on the pitch circle" % n)
    Ri, Rh, R8 = _num(g, "holder_inner_r"), _num(g, "holder_hub_r"), _num(g, "bearing_od_r")
    aw = _num(g, "angle_wire")
    w, bolt_r, hb = _num(g, "bolt_head_d") / 2, _num(g, "holder_bolt_r"), _num(g, "bolt_d") / 2
    rf = _num(g, "holder_window_r")
    need(R8 < Rh < Ri, "holder radii: bearing seat %.3f < hub %.3f < flange %.3f" % (R8, Rh, Ri))
    need(_num(g, "holder_lip_r") < R8, "holder lip radius must be < the bearing seat radius")
    need((Rh + rf) * math.sin(math.radians(aw)) > w + rf,
         "the window fillets do not fit between the spokes: spokes too wide (bolt_head_d) or angle_wire too small")
    need(bolt_r - w < Ri < bolt_r, "bolt circle %.3f: the boss (head circle radius %.3f) must straddle the flange radius %.3f"
         % (bolt_r, w, Ri))
    leg = bolt_r * math.sin(math.radians(aw)) - _num(g, "core_leg_outer_x")
    need(max(hb, _num(g, "holder_tap_d") / 2) < leg < w,
         "seat: the bolt axis is %.3f mm from the core leg face; it must be more than the hole radius and less than "
         "bolt_head_d / 2 (%.3f) - change holder_bolt_r or the core leg (tooth_width / coil_w / tooth2_width)" % (leg, w))
    need(_num(g, "holder_seat_depth") > _num(g, "holder_chamfer") > 0, "holder_seat_depth must exceed holder_chamfer > 0")
    Ro, ff = _num(g, "back_holder_outer_r"), _num(g, "back_holder_fillet_r")
    need(R8 < Ro < Ri and 0 < ff < w, "back holder: seat %.3f < outer %.3f < flange %.3f and 0 < fillet %.3f < bolt_head_d/2"
         % (R8, Ro, Ri, ff))
    rm, dm = _num(g, "back_screw_r"), _num(g, "back_screw_tap_d") / 2
    need(R8 < rm - dm and rm + dm < Ro and dm < w, "M3 holes (r %.3f, d %.3f) must lie in the back holder spokes (between the "
                                                   "bearing seat r %.3f and the outer r %.3f)" % (rm, 2 * dm, R8, Ro))
    need(_num(g, "ring_inner_r") > _num(g, "stator_down_r"), "GFRP ring inner radius %.3f must be > the stator bore %.3f"
         % (_num(g, "ring_inner_r"), _num(g, "stator_down_r")))
    need(_num(g, "holder_screw_l") > _num(g, "holder_flange_z") + _num(g, "motor_length") / 2,
         "holder screw (%.3f) must reach the back bosses" % _num(g, "holder_screw_l"))
    need(_num(g, "prop_fix_d") / 2 > rj + _num(g, "prop_fix_relief_r"), "Prop_fix diameter too small for the hole")
    return v


def expected_counts(g):
    ns = int(round(_num(g, "num_seg")))
    out = dict(core_sectors=ns * 3, coils=ns * 3, magnets=ns * int(round(_num(g, "num_poles_per_segment"))),
               layers=int(round(_num(g, "num_wires_per_slot"))))
    try:
        out.update(screws=ns * 3, balls=int(round(_num(g, "bearing_ball_count"))))
    except Exception:
        pass
    return out


def _bb(shape):
    try:
        return shape.optimalBoundingBox(False, False)   # exact: never the display mesh (GUI)
    except Exception:
        return shape.BoundBox


def gate(doc, sheet_name="Params"):
    """Mandatory health gate.  Returns dict(ok, errors, counts, volumes)."""
    import FreeCAD as App
    from .params_sheet import pval
    sh = doc.getObject(sheet_name)
    g = lambda n: pval(sh, n)
    errors = []
    pre = precheck(g)
    errors += ["precheck: " + t for t in pre]
    for o in doc.Objects:
        st = list(getattr(o, "State", []))
        if "Invalid" in st or "Error" in st or (hasattr(o, "isValid") and not o.isValid()):
            errors.append("object %s (%s) failed: state %s" % (o.Label, o.TypeId, ",".join(st)))
        elif "Touched" in st:
            errors.append("object %s not recomputed (Touched)" % o.Label)
        if o.TypeId == "Sketcher::SketchObject":
            rc = o.solve()
            if rc != 0:
                errors.append("sketch %s: solver failed (rc=%d) - check its constraints / the Params they use" % (o.Label, rc))
            if not o.FullyConstrained:
                errors.append("sketch %s lost full constraint" % o.Label)
            for k in ("ConflictingConstraints", "RedundantConstraints", "PartiallyRedundantConstraints", "MalformedConstraints"):
                if list(getattr(o, k, None) or []):
                    errors.append("sketch %s: %s %s" % (o.Label, k, list(getattr(o, k))))
    vols = {}
    shapes = {}
    for bn in ("Core", "Coil", "RotorCore", "Magnet"):
        b = doc.getObject(bn)
        if b is None:
            errors.append("body %s missing" % bn)
            continue
        s = b.Shape
        if s.isNull() or not s.isValid() or len(s.Solids) != 1 or s.Volume <= 0:
            errors.append("body %s: null/invalid/%d solids/volume<=0" % (bn, 0 if s.isNull() else len(s.Solids)))
            continue
        vols[bn] = s.Volume
        shapes[bn] = s
    exp = expected_counts(g)
    counts = {}
    for label, key in (("Coils", "coils"), ("MagnetArray", "magnets")):
        arr = [o for o in doc.Objects if o.Label == label]
        n = len(arr[0].Shape.Solids) if arr else 0
        counts[key] = n
        if n != exp[key]:
            errors.append("array %s has %d solids, Params require %d" % (label, n, exp[key]))
        if arr:
            vols[label] = arr[0].Shape.Volume
    if doc.getObject("Mechanics") is not None:
        errors += _gate_mechanics(doc, g, exp, counts, vols)
    # stale-geometry invariants (a failed feature keeps its previous shape) - always evaluated
    try:
        L = g("motor_length")
        Lc = L + g("coil_straight_extra")
        inv = []
        if "Core" in shapes:
            b = _bb(shapes["Core"])
            inv += [("Core z length = motor_length", b.ZLength, L), ("Core outer radius = stator_up_r", b.YMax, g("stator_up_r"))]
            sk = doc.getObject("CoreSectorSketch")
            try:
                import Part
                area = Part.Face(Part.Wire(Part.__sortEdges__([e for e in sk.Shape.Edges]))).Area
                inv.append(("Core volume = sector area x motor_length x num_seg*3", shapes["Core"].Volume,
                            area * L * exp["core_sectors"]))
            except Exception as e:
                errors.append("Core sector sketch area not computable: %s" % e)
        if "RotorCore" in shapes:
            s = shapes["RotorCore"]
            inv.append(("Rotor z length = rotor_w", _bb(s).ZLength, g("rotor_w")))
            a = math.radians(90 - g("angle_mag") / 2)
            Rup, Rhub = g("rotor_up_r"), g("rotor_down_r") - g("rotor_house_height")
            p_in = App.Vector((Rup - 0.01) * math.cos(a), (Rup - 0.01) * math.sin(a), 0)
            p_out = App.Vector((Rup + 0.01) * math.cos(a), (Rup + 0.01) * math.sin(a), 0)
            h_in = App.Vector(0, Rhub + 0.01, 0); h_out = App.Vector(0, Rhub - 0.01, 0)
            if not (s.isInside(p_in, 1e-6, True) and not s.isInside(p_out, 1e-6, True)):
                errors.append("stale/incorrect Rotor: outer radius is not rotor_up_r = %.4f" % Rup)
            if not (s.isInside(h_in, 1e-6, True) and not s.isInside(h_out, 1e-6, True)):
                errors.append("stale/incorrect Rotor: bore radius is not rotor_down_r - rotor_house_height = %.4f" % Rhub)
        if "Magnet" in shapes:
            b = _bb(shapes["Magnet"])
            inv += [("Magnet z length = mag_step", b.ZLength, g("mag_step")),
                    ("Magnet top = rotor_up_r - magnet_up_gap", b.YMax, g("rotor_up_r") - g("magnet_up_gap")),
                    ("Magnet bottom = rotor_down_r*cos(fill_down)", b.YMin,
                     g("rotor_down_r") * math.cos(math.radians(g("magnet_fill_down") * g("angle_mag") / 2)))]
        if "Coil" in shapes:
            b = _bb(shapes["Coil"])
            pitch = g("wire_height") + g("wire_spacing_y")
            ytop = g("stator_up_r") - g("core_thickness") - g("insulation_thickness") - g("wire_spacing_y") / 2
            inv += [("Coil top y", b.YMax, ytop),
                    ("Coil bottom y (num_wires_per_slot layers)", b.YMin, ytop - g("wire_height") - (exp["layers"] - 1) * pitch),
                    ("Coil lead end z", b.ZMin, -(Lc / 2 + g("coil_lead_ext") + g("coil_lead_len"))),
                    ("Coil end-turn top z = (L+extra)/2 + wire_beng_r + wire_width", b.ZMax,
                     Lc / 2 + g("wire_beng_r") + g("wire_width")),
                    ("Coil half width x", b.XMax, g("tooth_width") / 2 + g("insulation_thickness") + g("wire_spacing_x") / 2 + g("wire_width"))]
        for name, got, want in inv:
            tol = max(TOL, 1e-7 * abs(want)) if "volume" not in name else 1e-6 * abs(want)
            if name.startswith("Coil") and (" y" in name or " x" in name):
                tol = 1e-3   # the -z transition loft (B-spline through 7 sections) overshoots its layer by < 1 um
            if abs(got - want) > tol:
                errors.append("stale/incorrect geometry: %s: model %.6f, Params require %.6f" % (name, got, want))
    except Exception as e:
        errors.append("geometry invariants not evaluable: %s" % e)
    return dict(ok=not errors, errors=errors, counts=dict(counts, core_sectors=exp["core_sectors"]), volumes=vols,
                precheck_violations=pre)


MECH_BODIES = ("Shaft", "Stator_holder", "Stator_holder_back", "Ring", "Prop_fix", "Bearing_outer_ring", "Bearing_inner_ring",
               "Bearing_ball", "Screw")


def _gate_mechanics(doc, g, exp, counts, vols):
    """Mechanical parts: valid single solids, array counts, links, and stale-geometry invariants (extents that the
    current Params demand)."""
    from . import mechanics
    errors = []
    for bn in MECH_BODIES:
        b = doc.getObject(bn)
        if b is None:
            errors.append("body %s missing" % bn)
            continue
        s = b.Shape
        if s.isNull() or not s.isValid() or len(s.Solids) != 1 or s.Volume <= 0:
            errors.append("body %s: null/invalid/%d solids/volume<=0" % (bn, 0 if s.isNull() else len(s.Solids)))
            continue
        vols[bn] = s.Volume
    for label, key in (("Screws", "screws"), ("Balls", "balls")):
        arr = [o for o in doc.Objects if o.Label == label]
        n = len(arr[0].Shape.Solids) if arr else 0
        counts[key] = n
        if n != exp.get(key):
            errors.append("array %s has %d solids, Params require %s" % (label, n, exp.get(key)))
    for ln in ("Bearing_618_8_back", "Ring_back"):
        lk = doc.getObject(ln)
        if lk is None or lk.LinkedObject is None:
            errors.append("link %s missing or empty" % ln)
    if errors:
        return errors
    so = mechanics.solids(doc)
    zf, zfl, zb = g("holder_face_z"), g("holder_flange_z"), g("bearing_out_z")
    inv = [("Shaft back end z = -bearing_out_z", _bb(so["Shaft"]).ZMin, -zb),
           ("Shaft front end z = shaft_front_end_z", _bb(so["Shaft"]).ZMax, g("shaft_front_end_z")),
           ("Shaft flange radius", _bb(so["Shaft"]).XMax, g("shaft_seat_r") + g("shaft_flange_step")),
           ("front bearing inner face z", _bb(so["Bearing_618_8_front_outer_ring"]).ZMin, zf),
           ("front bearing outer face z", _bb(so["Bearing_618_8_front_outer_ring"]).ZMax, zb),
           ("back bearing inner face z", _bb(so["Bearing_618_8_back_outer_ring"]).ZMax, -zf),
           ("bearing outer radius", _bb(so["Bearing_618_8_front_outer_ring"]).XMax, g("bearing_D") / 2),
           ("Stator_holder z min = seat bottom", _bb(so["Stator_holder"]).ZMin, g("holder_seat_z")),
           ("Stator_holder z max = front_holder_end_z", _bb(so["Stator_holder"]).ZMax, g("front_holder_end_z")),
           ("Stator_holder_back z min = A_H = -back_holder_end_z", _bb(so["Stator_holder_back"]).ZMin, -g("back_holder_end_z")),
           ("Stator_holder_back z max = -seat bottom", _bb(so["Stator_holder_back"]).ZMax, -g("holder_seat_z")),
           ("Ring_front z", _bb(so["Ring_front"]).ZMin, g("motor_length") / 2),
           ("Ring_back z", _bb(so["Ring_back"]).ZMax, -g("motor_length") / 2),
           ("Prop_fix z min", _bb(so["Prop_fix"]).ZMin, g("shaft_journal_end_z") - g("prop_fix_hub_t")),
           ("Prop_fix z max", _bb(so["Prop_fix"]).ZMax, g("shaft_journal_end_z") + g("prop_fix_plate_t")),
           ("Screw head top z", _bb(so["Screw_1"]).ZMax, g("screw_head_top_z")),
           ("Screw tip z", _bb(so["Screw_1"]).ZMin, zfl - g("holder_screw_l"))]
    for name, got, want in inv:
        if abs(got - want) > max(TOL, 1e-7 * abs(want)):
            errors.append("stale/incorrect geometry: %s: model %.6f, Params require %.6f" % (name, got, want))
    # every patterned feature present (a tool that stops cutting after an edit keeps the body valid but wrong)
    n = int(round(g("holder_bolt_count")))
    want = {("Stator_holder", "chamfer45"): 2 * n, ("Stator_holder", g("holder_window_r")): 2 * n,
            ("Stator_holder_back", "chamfer45"): 2 * n, ("Stator_holder_back", g("holder_window_r")): 2 * n,
            ("Stator_holder_back", g("back_holder_fillet_r")): 2 * n, ("Stator_holder_back", g("back_screw_tap_d") / 2): n,
            ("Stator_holder", g("bolt_d") / 2): n, ("Stator_holder_back", g("holder_tap_d") / 2): n}
    for (body, kind), cnt in want.items():
        got = _feature_count(so[body], kind)
        if got != cnt:
            errors.append("incomplete %s: %d %s faces, Params require %d" % (
                body, got, "45-deg chamfer" if kind == "chamfer45" else "r = %.4g cylinder" % kind, cnt))
    return errors


def _feature_count(shape, kind):
    """Faces of a feature kind: 'chamfer45' = planar faces at 45 deg to the axis; a number = cylinders of that radius."""
    k = 0
    for f in shape.Faces:
        sf = f.Surface
        if kind == "chamfer45":
            if sf.__class__.__name__ == "Plane" and abs(abs(sf.Axis.z) - math.sqrt(0.5)) < 1e-6:
                k += 1
        elif sf.__class__.__name__ == "Cylinder" and abs(sf.Radius - kind) < 1e-6:
            k += 1
    return k


# pairs checked by fit(): (a, b, kind) - kind "contact" (must touch, must not overlap), "clear" (must not overlap),
# "thread" (screw in its tapped hole: the overlap is the engaged thread, reported, not refused)
FIT_PAIRS = [
    ("Shaft", "RotorCore", "contact"), ("Shaft", "Bearing_618_8_front_inner_ring", "contact"),
    ("Shaft", "Bearing_618_8_back_inner_ring", "contact"), ("Bearing_618_8_front_outer_ring", "Stator_holder", "contact"),
    ("Bearing_618_8_back_outer_ring", "Stator_holder_back", "contact"), ("Stator_holder", "Core", "contact"),
    ("Stator_holder_back", "Core", "contact"), ("Ring_front", "Core", "contact"), ("Ring_back", "Core", "contact"),
    ("Prop_fix", "Shaft", "contact"), ("Stator_holder", "Coils", "clear"), ("Stator_holder_back", "Coils", "clear"),
    ("Ring_front", "Coils", "clear"), ("Ring_back", "Coils", "clear"), ("Stator_holder", "Ring_front", "clear"),
    ("Stator_holder_back", "Ring_back", "clear"), ("RotorCore", "Stator_holder", "clear"),
    ("RotorCore", "Stator_holder_back", "clear"), ("MagnetArray", "Stator_holder", "clear"),
    ("MagnetArray", "Stator_holder_back", "clear"), ("RotorCore", "Ring_front", "clear"), ("RotorCore", "Ring_back", "clear"),
    ("Shaft", "Stator_holder", "clear"), ("Shaft", "Stator_holder_back", "clear"), ("Screws", "Core", "clear"),
    ("Screws", "Coils", "clear"), ("Screws", "Stator_holder", "contact"), ("Screws", "Stator_holder_back", "thread"),
]
FIT_REFUSE_MM3 = 0.5        # an overlap above this (outside "thread") refuses the build: a part runs into another one


def fit(doc):
    """Interference / contact table of the assembled motor (stage 3 fit check).  Returns dict(pairs, errors)."""
    import Part
    from . import mechanics
    so = mechanics.solids(doc)
    act = {"Core": doc.getObject("Core").Shape, "RotorCore": doc.getObject("RotorCore").Shape}
    for lbl in ("Coils", "MagnetArray"):
        act[lbl] = [o for o in doc.Objects if o.Label == lbl][0].Shape
    act["Screws"] = Part.makeCompound([v for k, v in so.items() if k.startswith("Screw_")])
    rows, errors = [], []
    for a, b, kind in FIT_PAIRS:
        sa, sb = so.get(a, act.get(a)), so.get(b, act.get(b))
        if sa is None or sb is None:
            continue
        common = sa.common(sb)
        ov = common.Volume if not common.isNull() else 0.0
        dist = sa.distToShape(sb)[0] if ov < 1e-9 else 0.0
        row = dict(a=a, b=b, kind=kind, overlap_mm3=round(ov, 6), min_distance_mm=round(dist, 6))
        if kind == "contact":
            row["ok"] = ov < 1e-3 and dist < 1e-6
        elif kind == "clear":
            row["ok"] = ov < 1e-3
        else:
            row["ok"] = True
            row["note"] = "screw thread in the tapped hole (overlap = engaged thread)"
        rows.append(row)
        if kind != "thread" and ov > FIT_REFUSE_MM3:
            errors.append("fit: %s runs into %s (overlap %.3f mm3)" % (a, b, ov))
    return dict(pairs=rows, errors=errors)
