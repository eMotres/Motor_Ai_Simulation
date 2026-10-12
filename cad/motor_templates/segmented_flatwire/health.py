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
    return v


def expected_counts(g):
    ns = int(round(_num(g, "num_seg")))
    return dict(core_sectors=ns * 3, coils=ns * 3, magnets=ns * int(round(_num(g, "num_poles_per_segment"))),
                layers=int(round(_num(g, "num_wires_per_slot"))))


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
            for k in ("Conflicting", "Redundant", "MalformedConstraints"):
                if list(getattr(o, k, [])):
                    errors.append("sketch %s: %s constraints %s" % (o.Label, k, list(getattr(o, k))))
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
