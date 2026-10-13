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


def find_step():
    """STEP of the board: $FC_PHASE_BOARD_STEP, cad/reference/phase_board/ (motor_ai_sim) or fusion_source/phase_board/
    (owner repo), relative to the template's grand-parent directory."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cands = [os.environ.get("FC_PHASE_BOARD_STEP"), os.path.join(root, "reference", "phase_board", STEP_NAME),
             os.path.join(root, "fusion_source", "phase_board", STEP_NAME)]
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


def build_electronics(doc, sh):
    """Adds the Electronics part with the PhaseBoard body.  Returns a small report."""
    import Part
    path = find_step()
    if path is None:
        raise RuntimeError("phase board STEP %s not found (cad/reference/phase_board/ or $FC_PHASE_BOARD_STEP)" % STEP_NAME)
    shape = Part.read(path)
    part = doc.addObject("App::Part", PART)
    part.Label = PART
    f = doc.addObject("Part::Feature", BODY)
    f.Label = BODY
    f.Shape = shape
    part.addObject(f)
    for prop, typ, val, tip in (
            ("SourceFile", "App::PropertyString", STEP_NAME, "STEP the shape was read from (imported once, not parametric)"),
            ("SourceSHA256", "App::PropertyString", sha256_file(path), "SHA-256 of that STEP"),
            ("SolidCount", "App::PropertyInteger", len(shape.Solids), "solids of the imported board (gate)")):
        f.addProperty(typ, prop, "Motor", tip)
        setattr(f, prop, val)
    for axis in "xyz":
        part.setExpression(".Placement.Base.%s" % axis, "%sphase_board_%s" % (P, axis))
    doc.recompute()
    return dict(step=STEP_NAME, sha256=f.SourceSHA256, solids=len(shape.Solids), faces=len(shape.Faces))


# ---------------------------------------------------------------------------------------------------- geometry in the motor frame


def placement(doc):
    return doc.getObject(PART).Placement.multiply(doc.getObject(BODY).Placement)


def board_shape(doc):
    """The imported compound in the motor frame."""
    s = doc.getObject(BODY).Shape.copy()
    s.Placement = doc.getObject(PART).Placement.multiply(s.Placement)
    return s


def pins(shape):
    return [s for s in shape.Solids if s.BoundBox.ZLength > PIN_MIN_Z_LENGTH]


def n_copper_z(shape):
    """Z of the N-side copper surface: the lowest of the four full-board copper layers (volume ~100 mm3, < 0.25 mm thick)."""
    layers = [s for s in shape.Solids if s.BoundBox.XLength > 30 and s.BoundBox.ZLength < 0.25
              and COPPER_PLANE_VOLUME[0] < s.Volume < COPPER_PLANE_VOLUME[1]]
    return min(s.BoundBox.ZMin for s in layers) if layers else None


def pin_positions(shape):
    return [(p.BoundBox.Center.x, p.BoundBox.Center.y) for p in pins(shape)]


def j13_error(shape):
    ps = pin_positions(shape)
    return min(math.hypot(x - J13[0], y - J13[1]) for x, y in ps) if ps else None


def gate(doc, g):
    """Errors in words (empty = ok).  g(name) = Params value."""
    errors = []
    f, part = doc.getObject(BODY), doc.getObject(PART)
    if f is None or part is None:
        return ["Electronics / PhaseBoard missing"]
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
    zn = n_copper_z(bs)
    lead_end = -(g("coil_leg_length") / 2 + g("coil_lead_total"))
    if zn is None:
        errors.append("PhaseBoard: N-side copper layer not found in the imported shape")
    elif abs(zn - (lead_end + g("phase_board_n_offset"))) > 1e-3:
        errors.append("stale/incorrect geometry: PhaseBoard N copper z %.4f, coil lead end %.4f + N offset %.4f require %.4f" % (
            zn, lead_end, g("phase_board_n_offset"), lead_end + g("phase_board_n_offset")))
    e = j13_error(bs)
    if e is None or e > J13_TOL:
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
    return {"Coils": lbl("Coils"), "Core": doc.getObject("Core").Shape.Solids, "RotorCore": doc.getObject("RotorCore").Shape.Solids,
            "Magnets": lbl("MagnetArray"), "Shaft": pick("Shaft"),
            "Stator_holder (front)": pick("Stator_holder"), "Stator_holder_back (A_H)": pick("Stator_holder_back"),
            "Rings": pick("Ring_front", "Ring_back"),
            "Bearings": [v for k, v in so.items() if k.startswith("Bearing_618_8")],
            "Screws": [v for k, v in so.items() if k.startswith("Screw_")], "Prop_fix": pick("Prop_fix")}


def fit(doc, motor_groups):
    """Clearance table of the board against the motor and the 12 lead ends against the board.

    motor_groups: {name: [solids]} in the motor frame (coils, core, rotor, magnets, holders, shaft, rings, ...).
    Returns dict(groups, leads, pins, errors).  Errors only for structural problems (a lead stub running into the board);
    overlaps of the board with motor parts are REPORTED (status 'CLASH'), not refused: the pin tail is provisional."""
    import Part
    bs = board_shape(doc)
    sol = list(bs.Solids)
    pn = pins(bs)
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
                          section_y=[round(bb.YMin, 3), round(bb.YMax, 3)], stands_out_of_n_copper_mm=round(n_copper_z(bs) - z0, 4),
                          overlap_mm3=round(ov, 6), clearance_to_board_mm=_r(d_b),
                          clearance_to_fr4_mm=_r(d_f)))
    return dict(groups=rows, leads=leads, pins=[[round(x, 4), round(y, 4)] for x, y in pin_positions(bs)],
                j13_error_mm=round(j13_error(bs), 5), board_bbox=[round(v, 4) for v in (
                    bs.BoundBox.XMin, bs.BoundBox.YMin, bs.BoundBox.ZMin, bs.BoundBox.XMax, bs.BoundBox.YMax, bs.BoundBox.ZMax)],
                errors=errors)


def datums(doc, g):
    """Block for the datums JSON (same frame as the STEP of the active part)."""
    bs = board_shape(doc)
    pl = placement(doc)
    return dict(
        what="phase (coil-connection) board rev C, imported reference body (not in the active-part STEP)",
        step=STEP_NAME, sha256=doc.getObject(BODY).SourceSHA256, solids=len(bs.Solids),
        translation=[round(pl.Base.x, 6), round(pl.Base.y, 6), round(pl.Base.z, 6)], rotation="none",
        n_copper_z=round(n_copper_z(bs), 6), a_side_top_z=round(bs.BoundBox.ZMax, 6),
        coil_lead_end_z=round(-(g("coil_leg_length") / 2 + g("coil_lead_total")), 6), n_offset=g("phase_board_n_offset"),
        pins_xy=[[round(x, 4), round(y, 4)] for x, y in pin_positions(bs)], pin_j13_target=list(J13),
        note="STEP frame is KiCad absolute (centre 100, -100); +Z = layer A facing the motor; N-side copper at the lead end + "
             "n_offset; the board grows toward the motor when its thickness grows")

