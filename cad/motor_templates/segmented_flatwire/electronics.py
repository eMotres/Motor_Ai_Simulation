"""Electronics: the phase (coil-connection) board as an imported, non-parametric reference body.

App::Part `Electronics` -> Part::Feature `PhaseBoard` (the compound of the 110 solids of the coloured STEP of the PCB project
PCB_CIANO14_40, rev C: FR4, four 6 oz copper layers, mask, 3 phase pins, 6 notch walls).  The geometry is read once at build
time and stored in the model; only the PLACEMENT is parametric (pure translation, no rotation):

    x = phase_board_x, y = phase_board_y                      STEP frame offset (KiCad absolute board centre 100, -100)
    z = phase_board_z = -(coil_leg_length / 2 + coil_lead_total) + phase_board_n_offset - phase_board_n_z0
                      = coil lead end datum + N offset + 0.21 mm (N copper sits 0.21 mm below the STEP origin)

so the board follows `motor_length` (and every other edit of the coil leads); the N-side copper surface is always
`phase_board_n_offset` (2.525 mm) above the coil lead end datum, the board thickness (1.6 +-10 %) grows toward the motor.

Gate (health.gate): shape valid with the recorded solid count, global placement = the Params rows, N copper surface
measured on the solid = lead end datum + offset, pin J13 at (6.606, 12.964).  Fit (fit(), build_report.json `phase_board`):
board vs every motor group (overlap, minimum clearance, also for the pins alone) and the 12 coil lead ends against the board
(the lead stub passes the board plane: no overlap, clearance to the notch walls / pads / FR4).
"""
import hashlib
import math
import os

import FreeCAD as App
from .common import P

STEP_NAME = "CIANO14_40_phase_board_revC_colour.step"
PART, BODY = "Electronics", "PhaseBoard"
J13 = (6.606, 12.964)                    # pin J13 in the motor frame (r 14.55 at 63 deg), from the board README
J13_TOL = 0.01
PIN_MIN_Z_LENGTH = 3.0                   # the 3 phase pins are the only solids taller than the 1.6 mm board
COPPER_PLANE_VOLUME = (90.0, 110.0)      # the four full-board copper layers (100.13 / 101.06 mm3)
LEAD_PROBE_LEN = 40.0


def find_step(name=None):
    """STEP of the board: $FC_PHASE_BOARD_STEP, cad/reference/phase_board/ (motor_ai_sim) or fusion_source/phase_board/
    (owner repo), relative to the template's grand-parent directory.  `name` = the STEP file name of the profile
    (`profile.phase_board.step`; default: the CIANO14 40 board)."""
    name = name or STEP_NAME
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cands = [os.environ.get("FC_PHASE_BOARD_STEP"), os.path.join(root, "reference", "phase_board", name),
             os.path.join(root, "fusion_source", "phase_board", name)]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def has_board(doc):
    return doc.getObject(PART) is not None and doc.getObject(BODY) is not None


def build_electronics(doc, sh, cfg=None):
    """Adds the Electronics part with the PhaseBoard body.  Returns a small report.
    cfg = profile["phase_board"] (optional; the CIANO14 40 board needs none): step (STEP file name), pins (bool: the board STEP carries
    phase pins - the J13 position gate and the pin clearances), copper_volume ([lo, hi] mm3 of one full-board copper layer solid,
    for the N-side copper search)."""
    import Part
    from .params_sheet import has_param
    if has_param(sh, "board_outer_r"):
        return build_board_plate(doc, sh)
    cfg = cfg or {}
    step_name = cfg.get("step", STEP_NAME)
    path = find_step(step_name)
    if path is None:
        raise RuntimeError("phase board STEP %s not found (cad/reference/phase_board/ or $FC_PHASE_BOARD_STEP)" % step_name)
    shape = Part.read(path)
    part = doc.addObject("App::Part", PART)
    part.Label = PART
    f = doc.addObject("Part::Feature", BODY)
    f.Label = BODY
    f.Shape = shape
    part.addObject(f)
    for prop, typ, val, tip in (
            ("SourceFile", "App::PropertyString", step_name, "STEP the shape was read from (imported once, not parametric)"),
            ("HasPins", "App::PropertyBool", bool(cfg.get("pins", True)), "the board carries phase pins (J13 position gate, pin clearances)"),
            ("CopperVolume", "App::PropertyString", "%g,%g" % tuple(cfg.get("copper_volume", COPPER_PLANE_VOLUME)),
             "volume range (mm3) of a full-board copper layer solid"),
            ("SourceSHA256", "App::PropertyString", sha256_file(path), "SHA-256 of that STEP"),
            ("SolidCount", "App::PropertyInteger", len(shape.Solids), "solids of the imported board (gate)")):
        f.addProperty(typ, prop, "Motor", tip)
        setattr(f, prop, val)
    for axis in "xyz":
        part.setExpression(".Placement.Base.%s" % axis, "%sphase_board_%s" % (P, axis))
    doc.recompute()
    return dict(step=step_name, sha256=f.SourceSHA256, solids=len(shape.Solids), faces=len(shape.Faces))


# ---------------------------------------------------------------------------------------------------- plate board (CIANO28 85)
# "PCB 85 serial" of the Fusion model, reduced to what the fit needs (owner brief: outline, holes and pin positions only):
# a ring plate (PartDesign Body PhaseBoard: outer / inner radius, thickness board_t, a lead slot per coil lead, polar pattern) and
# the lead sleeves (Body PhaseBoardPins: a rectangular sleeve around every coil lead stub, same pattern).  Everything is a named
# Params row (board_*): the board follows the coil leads (board_top_z = lead end of the straight legs + board_gap).
PLATE_PINS = "PhaseBoardPinA"                  # base body of the lead A sleeve (B: PhaseBoardPinB); arrays BoardPinsA / BoardPinsB


def is_plate(doc):
    return doc.getObject(PLATE_PINS) is not None


def plate_pin_solids(doc):
    """The lead sleeves (Draft link arrays BoardPinsA / BoardPinsB) in the motor frame."""
    out = []
    for lbl in ("BoardPinsA", "BoardPinsB"):
        for o in doc.Objects:
            if o.Label == lbl:
                out += [x.copy() for x in o.Shape.Solids]
    return out


def build_board_plate(doc, sh):
    from .params_sheet import pval
    from .sketchkit import SK
    from .common import xy_sketch
    from .mechanics import polar, _pad, _pocket, ORIGIN
    from .build import _polar_array
    import Part
    g = lambda n: pval(sh, n)
    part = doc.addObject("App::Part", PART)
    part.Label = PART
    plate = doc.addObject("PartDesign::Body", BODY)
    plate.Label = BODY
    part.addObject(plate)
    V = App.Vector

    def lead_loops(sk, s, tags, with_inner):
        """Rectangles of board_pin_w x board_pin_t centred on the coil lead stubs (lead A on -x, lead B on +x) and, for the sleeves,
        the stub section wire_width x wire_height as the inner loop."""
        lx, ay, by = g("board_lead_x"), g("board_lead_a_y"), g("board_lead_b_y")
        pw, pt, ww, wh = g("board_pin_w"), g("board_pin_t"), g("wire_width"), g("wire_height")
        for tag, cx, cy, cxe, cye in (("a", -lx, ay, "-" + P + "board_lead_x", P + "board_lead_a_y"),
                                      ("b", lx, by, P + "board_lead_x", P + "board_lead_b_y")):
            if tag not in tags:
                continue
            loops = [("pin", pw, pt, P + "board_pin_w", P + "board_pin_t")]
            if with_inner:
                loops.append(("stub", ww, wh, P + "wire_width", P + "wire_height"))
            for kind, w, h, we, he in loops:
                gg = s.rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
                n = "%s_%s" % (kind, tag)
                s.con("DistanceX", ORIGIN, 1, gg[0], 1, cx - w / 2, name=n + "_x", expr="%s - (%s) / 2" % (cxe, we))
                s.con("DistanceY", ORIGIN, 1, gg[0], 1, cy - h / 2, name=n + "_y", expr="%s - (%s) / 2" % (cye, he))
                s.con("DistanceX", gg[0], 1, gg[0], 2, w, name=n + "_w", expr=we)
                s.con("DistanceY", gg[1], 1, gg[1], 2, h, name=n + "_h", expr=he)

    # plate: ring -> pad, lead slots -> pocket through, polar pattern
    rk = xy_sketch(plate, "BoardRingSketch", P + "board_bottom_z")
    s = SK(rk)
    co = rk.addGeometry(Part.Circle(V(0, 0, 0), V(0, 0, 1), g("board_outer_r")), False)
    ci = rk.addGeometry(Part.Circle(V(0, 0, 0), V(0, 0, 1), g("board_inner_r")), False)
    s.con("Coincident", co, 3, ORIGIN, 1)
    s.con("Coincident", ci, 3, ORIGIN, 1)
    s.con("Radius", co, g("board_outer_r"), name="board_outer_r", expr=P + "board_outer_r")
    s.con("Radius", ci, g("board_inner_r"), name="board_inner_r", expr=P + "board_inner_r")
    doc.recompute()
    ring = _pad(plate, rk, "BoardRing", P + "board_t")
    doc.recompute()
    sk1 = xy_sketch(plate, "BoardLeadSlotSketch", P + "board_top_z")
    lead_loops(sk1, SK(sk1), ("a", "b"), False)
    doc.recompute()
    slots = _pocket(plate, sk1, "BoardLeadSlots", P + "board_t")
    doc.recompute()
    pp1 = polar(plate, [slots], sk1, P + "coil_count", "BoardLeadSlotsPattern")
    pp1.Refine = True
    plate.Tip = pp1
    sketches = [rk, sk1]
    # lead sleeves: one body per lead (a PartDesign body keeps ONE solid), each patterned like the coils (Draft link array)
    for tag in ("a", "b"):
        pb = doc.addObject("PartDesign::Body", "PhaseBoardPin" + tag.upper())
        pb.Label = pb.Name
        part.addObject(pb)
        sk2 = xy_sketch(pb, "BoardPinSketch" + tag.upper(), P + "board_bottom_z")
        lead_loops(sk2, SK(sk2), (tag,), True)
        doc.recompute()
        pad = _pad(pb, sk2, "BoardPin" + tag.upper(), P + "board_t")
        pb.Tip = pad
        doc.recompute()
        arr = _polar_array(doc, pb, P + "coil_count", "BoardPins" + tag.upper())
        part.addObject(arr)
        sketches.append(sk2)
    doc.recompute()
    return dict(step=None, kind="plate", sketches=sketches, solids=len(plate.Shape.Solids) + len(plate_pin_solids(doc)))


def plate_gate(doc, g):
    errors = []
    plate = doc.getObject(BODY)
    n = int(round(g("coil_count")))
    if plate is None or plate.Shape.isNull() or not plate.Shape.isValid() or plate.Shape.Volume <= 0:
        return ["PhaseBoard: missing / null / invalid"]
    if len(plate.Shape.Solids) != 1:
        errors.append("PhaseBoard plate has %d solids, expected 1" % len(plate.Shape.Solids))
    sl = plate_pin_solids(doc)
    if len(sl) != 2 * n:
        errors.append("the board has %d lead sleeves, Params require %d (2 per coil)" % (len(sl), 2 * n))
    bb = plate.Shape.optimalBoundingBox(False, False)
    for name, got, want in (("board outer radius", bb.XMax, g("board_outer_r")), ("board top z", bb.ZMax, g("board_top_z")),
                            ("board bottom z", bb.ZMin, g("board_bottom_z"))):
        if abs(got - want) > 1e-4:
            errors.append("stale/incorrect geometry: %s: model %.5f, Params require %.5f" % (name, got, want))
    lead_end = -(g("coil_leg_length") / 2 + g("coil_lead_ext"))
    if abs(g("board_top_z") - (lead_end - g("board_gap"))) > 1e-6:
        errors.append("board_top_z is not the straight-leg end + lead extension + gap")
    return errors


def plate_fit(doc, motor_groups):
    import Part
    plate = doc.getObject(BODY).Shape
    sleeves = plate_pin_solids(doc)
    rows, errors = [], []
    for name, grp in motor_groups.items():
        d_pl, ov_pl = distance_overlap([plate], grp)
        d_pn, ov_pn = distance_overlap(sleeves, grp)
        ds = [x for x in (d_pl, d_pn) if x is not None]
        rows.append(dict(group=name, overlap_mm3=round(ov_pl + ov_pn, 6), min_distance_mm=_r(min(ds)) if ds else None,
                         board_overlap_mm3=round(ov_pl, 6), board_min_distance_mm=_r(d_pl),
                         pins_overlap_mm3=round(ov_pn, 6), pins_min_distance_mm=_r(d_pn),
                         status="CLASH" if (ov_pl + ov_pn) > 1e-6 else "clear"))
    leads = []
    coils = motor_groups.get("Coils", [])
    zt = plate.BoundBox.ZMax
    for ci, name, face in lead_ends(coils):
        z0 = face.CenterOfMass.z
        prism = face.extrude(App.Vector(0, 0, zt + 1.0 - z0))
        d_p, ov_p = distance_overlap([prism.Solids[0]], [plate])
        d_s, ov_s = distance_overlap([prism.Solids[0]], sleeves)
        c = face.CenterOfMass
        leads.append(dict(coil=ci, lead=name, centre=[round(c.x, 3), round(c.y, 3), round(c.z, 3)], r=round(math.hypot(c.x, c.y), 3),
                          stands_out_of_board_mm=round(zt - z0, 4), overlap_plate_mm3=round(ov_p, 6), overlap_sleeve_mm3=round(ov_s, 6),
                          overlap_mm3=round(ov_p + ov_s, 6),
                          clearance_to_board_mm=_r(min([x for x in (d_p, d_s) if x is not None] or [None]) if (d_p is not None or d_s is not None) else None),
                          clearance_to_plate_mm=_r(d_p), clearance_to_sleeve_mm=_r(d_s)))
        if ov_p > 1e-6 or ov_s > 1e-6:
            errors.append("coil %d lead '%s' runs into the board (plate %.4f, sleeve %.4f mm3)" % (ci, name, ov_p, ov_s))
    bb = plate.BoundBox
    return dict(kind="plate", groups=rows, leads=leads, pins=[[round(x.BoundBox.Center.x, 4), round(x.BoundBox.Center.y, 4)]
                                                              for x in sleeves],
                board_bbox=[round(v, 4) for v in (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)], errors=errors)


# ---------------------------------------------------------------------------------------------------- geometry in the motor frame


def placement(doc):
    return doc.getObject(PART).Placement.multiply(doc.getObject(BODY).Placement)


def board_shape(doc):
    """The imported compound (plate variant: plate + sleeves) in the motor frame."""
    if is_plate(doc):
        import Part
        return Part.makeCompound([doc.getObject(BODY).Shape.copy()] + plate_pin_solids(doc))
    s = doc.getObject(BODY).Shape.copy()
    s.Placement = doc.getObject(PART).Placement.multiply(s.Placement)
    return s


def board_cfg(doc):
    """(has_pins, copper volume range) of the board object (defaults = the CIANO14 40 board; a plate board has no such properties)."""
    f = doc.getObject(BODY)
    has_pins = bool(getattr(f, "HasPins", True)) if f is not None else True
    try:
        vol = tuple(float(x) for x in f.CopperVolume.split(","))
    except Exception:
        vol = COPPER_PLANE_VOLUME
    return has_pins, vol


def pins(shape, has_pins=True):
    """The phase pins / lead sleeves of the board shape.  `has_pins=False` (board STEP without pins, profile.phase_board.pins): none."""
    if not has_pins:
        return []
    tall = [s for s in shape.Solids if s.BoundBox.ZLength > PIN_MIN_Z_LENGTH]
    vmax = max([s.Volume for s in shape.Solids] or [0.0])
    if tall and not any(abs(s.Volume - vmax) < 1e-9 for s in tall):        # a thick plate (CIANO28 150, 6.1 mm) is not a pin
        return tall             # plate variant: the lead sleeves are the small solids
    return [s for s in shape.Solids if s.Volume < 0.05 * vmax] if len(shape.Solids) > 1 else []


def n_copper_z(shape, vol=COPPER_PLANE_VOLUME):
    """Z of the N-side copper surface: the lowest of the four full-board copper layers (volume ~100 mm3, < 0.25 mm thick)."""
    layers = [s for s in shape.Solids if s.BoundBox.XLength > 30 and s.BoundBox.ZLength < 0.25
              and vol[0] < s.Volume < vol[1]]
    return min(s.BoundBox.ZMin for s in layers) if layers else None


def pin_positions(shape, has_pins=True):
    return [(p.BoundBox.Center.x, p.BoundBox.Center.y) for p in pins(shape, has_pins)]


def j13_error(shape, has_pins=True):
    ps = pin_positions(shape, has_pins)
    return min(math.hypot(x - J13[0], y - J13[1]) for x, y in ps) if ps else None


def gate(doc, g):
    """Errors in words (empty = ok).  g(name) = Params value."""
    errors = []
    f, part = doc.getObject(BODY), doc.getObject(PART)
    if f is None or part is None:
        return ["Electronics / PhaseBoard missing"]
    if is_plate(doc):
        return plate_gate(doc, g)
    s = f.Shape
    if s.isNull() or not s.isValid() or s.Volume <= 0:
        return ["PhaseBoard: null / invalid / volume<=0"]
    if len(s.Solids) != int(f.SolidCount):
        errors.append("PhaseBoard has %d solids, the import recorded %d" % (len(s.Solids), f.SolidCount))
    pl = placement(doc)
    want = (g("phase_board_x"), g("phase_board_y"), g("phase_board_z"))
    got = (pl.Base.x, pl.Base.y, pl.Base.z)
    if max(abs(a - b) for a, b in zip(got, want)) > 1e-6 or abs(pl.Rotation.Angle) > 1e-9:
        errors.append("stale/incorrect geometry: PhaseBoard placement %s, rotation %.3g rad, Params require %s" % (
            tuple(round(v, 6) for v in got), pl.Rotation.Angle, tuple(round(v, 6) for v in want)))
    chain = -(g("coil_leg_length") / 2 + g("coil_lead_total")) + g("phase_board_n_offset") - g("phase_board_n_z0")
    if abs(g("phase_board_z") - chain) > 1e-6:
        errors.append("phase_board_z %.6f is not coil lead end + N offset (%.6f)" % (g("phase_board_z"), chain))
    bs = board_shape(doc)
    has_pins, vol = board_cfg(doc)
    zn = n_copper_z(bs, vol)
    lead_end = -(g("coil_leg_length") / 2 + g("coil_lead_total"))
    if zn is None:
        errors.append("PhaseBoard: N-side copper layer not found in the imported shape")
    elif abs(zn - (lead_end + g("phase_board_n_offset"))) > 1e-3:
        errors.append("stale/incorrect geometry: PhaseBoard N copper z %.4f, coil lead end %.4f + N offset %.4f require %.4f" % (
            zn, lead_end, g("phase_board_n_offset"), lead_end + g("phase_board_n_offset")))
    e = j13_error(bs) if has_pins else 0.0
    if has_pins and (e is None or e > J13_TOL):
        errors.append("stale/incorrect geometry: pin J13 is %s mm from (%.3f, %.3f), allowed %.2f" % (
            "?" if e is None else "%.4f" % e, J13[0], J13[1], J13_TOL))
    return errors


# ---------------------------------------------------------------------------------------------------- distances / fit


def _gap(a, b):
    dx = max(a.XMin - b.XMax, b.XMin - a.XMax, 0.0)
    dy = max(a.YMin - b.YMax, b.YMin - a.YMax, 0.0)
    dz = max(a.ZMin - b.ZMax, b.ZMin - a.ZMax, 0.0)
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _r(v, n=4):
    return None if v is None else round(v, n)


CUTOFF = 8.0     # mm: clearances larger than this are reported as None (">8"); keeps the face-level search small


def _near_faces(solid, box, margin):
    """Faces of `solid` whose bounding box comes within `margin` of the bounding box `box`."""
    out = []
    for f in solid.Faces:
        b = f.BoundBox
        if (b.XMin <= box.XMax + margin and b.XMax >= box.XMin - margin and b.YMin <= box.YMax + margin
                and b.YMax >= box.YMin - margin and b.ZMin <= box.ZMax + margin and b.ZMax >= box.ZMin - margin):
            out.append(f)
    return out


def distance_overlap(solids_a, solids_b, cutoff=CUTOFF):
    """(minimum distance or None beyond `cutoff`, overlap volume) of two lists of solids.  Solid pairs are visited by bounding
    box gap; only the faces that can lie within the best distance found so far enter the exact distance (a coil or a copper
    plane is far larger than the region where it comes near the other part), the overlap volume is the boolean `common` of
    the pairs that touch."""
    import Part
    pairs = sorted(((_gap(a.BoundBox, b.BoundBox), i, j) for i, a in enumerate(solids_a) for j, b in enumerate(solids_b)),
                   key=lambda t: t[0])
    best, ov = cutoff, 0.0
    found = False
    for gap, i, j in pairs:
        if gap >= best and not (found and best <= 1e-7 and gap <= 1e-7):
            break
        sa, sb = solids_a[i], solids_b[j]
        m = best if best > 1e-7 else 1e-3
        fa = _near_faces(sa, sb.BoundBox, m)
        fb = _near_faces(sb, sa.BoundBox, m)
        if not fa or not fb:
            continue
        d = Part.makeCompound(fa).distToShape(Part.makeCompound(fb))[0]
        if d < 1e-7:
            found, best = True, 0.0
            c = sa.common(sb)
            ov += c.Volume if not c.isNull() else 0.0
        elif d < best:
            found, best = True, d
    return (best if found else None), ov


def lead_ends(coils):
    """[(coil index, lead name, planar -Z end face)] of the two lead stubs of every coil solid (lowest z)."""
    out = []
    for i, s in enumerate(coils):
        zmin = s.BoundBox.ZMin
        fs = []
        for f in s.Faces:
            try:
                n = f.normalAt(*f.Surface.parameter(f.CenterOfMass))
            except Exception:
                continue
            if abs(f.CenterOfMass.z - zmin) < 1e-6 and n.z < -0.999:
                fs.append(f)
        fs.sort(key=lambda f: -math.hypot(f.CenterOfMass.x, f.CenterOfMass.y))       # outer (layer 0) first
        for name, f in zip(("start (layer 0, outer)", "end (last layer, inner)"), fs):
            out.append((i + 1, name, f))
    return out


def motor_groups(doc):
    """{group: [solids]} of the motor in its frame, for fit()."""
    from . import mechanics
    so = mechanics.solids(doc)
    lbl = lambda n: [o for o in doc.Objects if o.Label == n][0].Shape.Solids
    pick = lambda *names: [so[n] for n in names if n in so]
    groups = {"Coils": lbl("Coils"), "Core": doc.getObject("Core").Shape.Solids, "RotorCore": doc.getObject("RotorCore").Shape.Solids,
            "Magnets": lbl("MagnetArray"), "Shaft": pick("Shaft", "Shaft_rear"),
            "Stator_holder (front)": pick("Stator_holder"), "Stator_holder_back (A_H)": pick("Stator_holder_back"),
            "Rings": pick("Ring_front", "Ring_back"),
            "Bearings": [v for k, v in so.items() if k.startswith("Bearing_618_8")],
            "Screws": [v for k, v in so.items() if k.startswith("Screw_")], "Prop_fix": pick("Prop_fix")}
    from . import frameless
    if frameless.is_frameless(doc):                                  # the frameless kit has no housing groups
        groups = {k: v for k, v in groups.items() if v}
    return groups


def fit(doc, motor_groups):
    """Clearance table of the board against the motor and the 12 lead ends against the board.

    motor_groups: {name: [solids]} in the motor frame (coils, core, rotor, magnets, holders, shaft, rings, ...).
    Returns dict(groups, leads, pins, errors).  Errors only for structural problems (a lead stub running into the board);
    overlaps of the board with motor parts are REPORTED (status 'CLASH'), not refused: the pin tail is provisional."""
    import Part
    if is_plate(doc):
        return plate_fit(doc, motor_groups)
    bs = board_shape(doc)
    has_pins, vol = board_cfg(doc)
    sol = list(bs.Solids)
    pn = pins(bs, has_pins)
    body = [s for s in sol if s.BoundBox.ZLength <= PIN_MIN_Z_LENGTH]
    rows, errors = [], []
    for name, grp in motor_groups.items():
        d_all, ov = distance_overlap(sol, grp)
        d_pin, ov_pin = distance_overlap(pn, grp)
        d_body, ov_body = distance_overlap(body, grp)
        rows.append(dict(group=name, overlap_mm3=round(ov, 6), min_distance_mm=_r(d_all),
                         pins_overlap_mm3=round(ov_pin, 6), pins_min_distance_mm=_r(d_pin),
                         board_overlap_mm3=round(ov_body, 6), board_min_distance_mm=_r(d_body),
                         status="CLASH" if ov > 1e-6 else "clear"))
    leads = []
    zb0, zb1 = bs.BoundBox.ZMin, bs.BoundBox.ZMax
    coils = motor_groups.get("Coils", [])
    fr4 = [s for s in body if s.BoundBox.XLength > 30 and s.BoundBox.ZLength > 1.0]       # the FR4 body
    for ci, name, face in lead_ends(coils):
        bb = face.BoundBox
        z0 = face.CenterOfMass.z
        prism = face.extrude(App.Vector(0, 0, zb1 + 1.0 - z0))        # the lead stub from its end face up through the board
        d_b, ov = distance_overlap([prism.Solids[0]], sol)
        d_f, _ = distance_overlap([prism.Solids[0]], fr4) if fr4 else (None, 0.0)
        c = face.CenterOfMass
        leads.append(dict(coil=ci, lead=name, centre=[round(c.x, 3), round(c.y, 3), round(c.z, 3)],
                          r=round(math.hypot(c.x, c.y), 3), section_x=[round(bb.XMin, 3), round(bb.XMax, 3)],
                          section_y=[round(bb.YMin, 3), round(bb.YMax, 3)], stands_out_of_n_copper_mm=round(n_copper_z(bs, vol) - z0, 4),
                          overlap_mm3=round(ov, 6), clearance_to_board_mm=_r(d_b),
                          clearance_to_fr4_mm=_r(d_f)))
    return dict(groups=rows, leads=leads, pins=[[round(x, 4), round(y, 4)] for x, y in pin_positions(bs, has_pins)],
                j13_error_mm=(round(j13_error(bs), 5) if has_pins else None), board_bbox=[round(v, 4) for v in (
                    bs.BoundBox.XMin, bs.BoundBox.YMin, bs.BoundBox.ZMin, bs.BoundBox.XMax, bs.BoundBox.YMax, bs.BoundBox.ZMax)],
                errors=errors)


def datums(doc, g):
    """Block for the datums JSON (same frame as the STEP of the active part)."""
    if is_plate(doc):
        bs = board_shape(doc)
        pb = doc.getObject(BODY).Shape.BoundBox
        return dict(what="phase (coil-connection) board 85, reduced to outline, lead slots and lead sleeves (parametric plate)",
                    solids=len(bs.Solids), board_outer_r=g("board_outer_r"), board_inner_r=g("board_inner_r"),
                    board_top_z=round(pb.ZMax, 6), board_bottom_z=round(pb.ZMin, 6),
                    coil_lead_end_z=round(-(g("coil_leg_length") / 2 + g("coil_lead_total")), 6),
                    pins_xy=[[round(x, 4), round(y, 4)] for x, y in pin_positions(bs)],
                    note="board_top_z = end of the straight legs + coil_lead_ext + board_gap; the sleeves sit on the coil lead stubs")
    bs = board_shape(doc)
    pl = placement(doc)
    has_pins, vol = board_cfg(doc)
    return dict(
        what="phase (coil-connection) board rev C, imported reference body (not in the active-part STEP)",
        step=doc.getObject(BODY).SourceFile, sha256=doc.getObject(BODY).SourceSHA256, solids=len(bs.Solids),
        translation=[round(pl.Base.x, 6), round(pl.Base.y, 6), round(pl.Base.z, 6)], rotation="none",
        n_copper_z=round(n_copper_z(bs, vol), 6), a_side_top_z=round(bs.BoundBox.ZMax, 6),
        coil_lead_end_z=round(-(g("coil_leg_length") / 2 + g("coil_lead_total")), 6), n_offset=g("phase_board_n_offset"),
        pins_xy=[[round(x, 4), round(y, 4)] for x, y in pin_positions(bs, has_pins)], pin_j13_target=list(J13) if has_pins else None,
        note="STEP frame is KiCad absolute (centre 100, -100); +Z = layer A facing the motor; N-side copper at the lead end + "
             "n_offset; the board grows toward the motor when its thickness grows")

