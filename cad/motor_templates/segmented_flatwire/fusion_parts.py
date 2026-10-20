"""Mechanical parts taken from Fusion STEP files instead of the parametric template (template A.7.6 / A.7.7).

Owner rule 2026-10-19: everything in production comes ONLY from Fusion.  `profile.fusion_parts` names the parts that are the REAL Fusion bodies:

    "fusion_parts": {
        "dir":              "CIANO14_40",                    sub-folder of <reference>/fusion_parts/ that holds the STEP files
        "ref_motor_length": 12,                              motor_length the STEP files were exported at (mm)
        "files":            {"Shaft": "Shaft.step", ...},    body name -> STEP file (read-only export of the Fusion component)
        "volumes_mm3":      {"Shaft": 1527.938979, ...},     Fusion body volumes at ref_motor_length (the gate compares the imported solid)
        "shift":            {"Stator_holder": 1, ...},       optional, rigid parts: z shift = shift * (motor_length - ref_motor_length) / 2
        "stretch":          {"Shaft": {"z": 0.0}},           optional, one piece that spans both sides: cut (or lengthen) the constant
                                                             cross-section at z, the part behind moves -d/2 and the part in front +d/2
        "dies":             ["CIANO14 40 new"],              optional, only these dies take the Fusion bodies (store.make_profile)
        "thread_radii":     {"Shaft": [2.4505]}              optional, radii of the cosmetic-thread cylinders (view.thread_face_indices, glTF thread nodes)
        "param_overrides":  {"holder_bolt_r": "14.8431907 mm"}   optional, Params rows of the template parts that must follow the Fusion body
                                                             (store.apply_fusion_param_overrides; the screws sit on the Fusion bolt circle)
        "canonical":        true | [names]                   optional, write the imported solid to a STEP and read it back (see import_fusion_part): the shape then
                                                             has FreeCAD's own tolerance (1e-7) instead of the 2.5e-3 .. 4.2e-3 mm of Fusion's STEP and its
                                                             volume and area are Fusion's to 1e-9 (a plain read is off by 1e-5, which the STEP round-trip check of
                                                             the build would see as a volume change)
        "replaced_rows":    ["shaft_seat_r", ...]            optional, Params rows that only the replaced template geometry reads: dead by design,
                                                             build_model.py lists them in roles_check.replaced_by_fusion instead of refusing the build
    }

Without `shift` the occupancy variant (CIANO28 150) keeps its own rule (front parts +d/2, rear parts -d/2).

The STEP is imported ONCE (its largest solid is the part; a component STEP also carries the screws of the holder) and becomes the BaseFeature of
the same PartDesign::Body, so names, solids, gate, fit, BOM, STEP tree, GLB code are unchanged.  The shape is not parametric: the Params rows of the
part are not read any more.  A missing STEP refuses the build; there is no silent fall-back to the template geometry.

Stretch (a cut-and-shift, exactly the production drawing rule "L20 = L12 + 8 mm in the rotor-seat section" of the 40 mm shaft): the section of the part
at `z` must be the same at both cut planes (checked), the part is split, the rear half moves -d/2, the front half +d/2 and, for d > 0, the extruded
section fills the gap (for d < 0 a slab of -d mm is removed).  The volume then equals the Fusion volume + d x section area.
"""
import hashlib
import json
import os

import Part

FUSION_SUFFIX = "_Fusion"


def find_fusion_part(file_name, subdir):
    """Fusion STEP of a part, first hit of: $FC_FUSION_PARTS/<file>, $FC_FUSION_PARTS/<subdir>/<file>,
    $FC_CAD_REFERENCE/fusion_parts/<subdir>/<file> (the private data repository, store.find_reference_dir), cad/reference/fusion_parts/<subdir>/<file>,
    (owner repo) fusion_source/<subdir>/mechanics/<file> relative to cad/.  Returns (path or None, candidates)."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))        # cad/
    cands = []
    if os.environ.get("FC_FUSION_PARTS"):
        cands += [os.path.join(os.environ["FC_FUSION_PARTS"], file_name), os.path.join(os.environ["FC_FUSION_PARTS"], subdir, file_name)]
    if os.environ.get("FC_CAD_REFERENCE"):
        cands.append(os.path.join(os.environ["FC_CAD_REFERENCE"], "fusion_parts", subdir, file_name))
    cands += [os.path.join(root, "reference", "fusion_parts", subdir, file_name),
              os.path.join(root, "fusion_source", subdir, "mechanics", file_name)]
    for c in cands:
        if os.path.isfile(c):
            return c, cands
    return None, cands


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _moved(sol, dz):
    """`sol` moved by dz along z with the movement BAKED INTO the geometry.  Shape.translated() only changes the shape's location; a PartDesign::Body
    ignores the placement of its BaseFeature (the body then sits at the unshifted height - the first Fusion-parts build of a length other than
    ref_motor_length showed it), transformShape(copy=True) keeps surfaces (cylinders stay cylinders) and the validity."""
    import FreeCAD as App
    m = App.Matrix()
    m.move(_v(0, 0, dz))
    out = sol.copy()
    out.transformShape(m, True)
    return out


def _box(z0, z1, big=1000.0):
    return Part.makeBox(2 * big, 2 * big, z1 - z0, _v(-big, -big, z0))


def _v(x, y, z):
    import FreeCAD as App
    return App.Vector(x, y, z)


def _section_face(sol, z):
    """The planar cross-section of `sol` at height z as ONE face (outer wire + holes)."""
    wires = sol.slice(_v(0, 0, 1), z)
    if not wires:
        raise RuntimeError("no cross-section of the Fusion part at z = %.4f" % z)
    return Part.makeFace(wires, "Part::FaceMakerBullseye")


def stretch_solid(sol, z, dz, tol=1e-5):
    """Lengthen (dz > 0) or shorten (dz < 0) `sol` at height z by cut-and-shift; see the module docstring.  Returns (solid, filler volume)."""
    bb = sol.BoundBox
    a0 = _section_face(sol, z).Area
    za, zb = (z, z) if dz >= 0 else (z + dz / 2.0, z - dz / 2.0)           # the removed slab is [za, zb]
    n = 5
    for zz in [za + (zb - za) * i / n for i in range(n + 1)] if zb > za else [za]:         # the whole removed slab (or the cut plane) has the constant section
        if not (bb.ZMin + 1e-6 < zz < bb.ZMax - 1e-6) or abs(_section_face(sol, zz).Area - a0) > tol * max(a0, 1.0):
            raise RuntimeError("stretch of the Fusion part at z = %.3f by %+.3f mm: the cross-section at z = %.3f is not the constant section "
                               "(area %.5f mm2 vs %.5f mm2); the part cannot be stretched there" % (z, dz, zz, _section_face(sol, zz).Area, a0))
    rear = sol.common(_box(bb.ZMin - 1.0, za))
    front = sol.common(_box(zb, bb.ZMax + 1.0))
    rear = _moved(rear, -dz / 2.0)
    front = _moved(front, dz / 2.0)
    parts = [rear, front]
    fill = 0.0
    if dz > 0:
        f = _section_face(sol, z)
        f.translate(_v(0, 0, -dz / 2.0))                                    # the section lies at z; move it to the start of the gap, z - dz/2
        filler = f.extrude(_v(0, 0, dz))
        parts.append(filler)
        fill = filler.Volume
    out = parts[0].fuse(parts[1:])
    out = out.removeSplitter()
    sols = out.Solids
    if len(sols) != 1:
        raise RuntimeError("stretch of the Fusion part gave %d solids" % len(sols))
    return sols[0], fill


def _canonical(sol):
    """The same solid after a STEP write + read: Fusion's STEP carries shape tolerances of 2.5e-3 .. 4.2e-3 mm, so a plain read integrates volume and
    area about 1e-5 off Fusion's own numbers; the re-read shape has the written geometry's tolerance and reproduces them (Shaft 1527.9390 vs Fusion
    1527.938979, was 1527.9501), which is also what the STEP round-trip check of the build compares against."""
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".step")
    os.close(fd)
    try:
        sol.exportStep(tmp)
        back = Part.read(tmp).Solids
    finally:
        os.remove(tmp)
    if len(back) != 1 or len(back[0].Faces) != len(sol.Faces):
        raise RuntimeError("canonical STEP round trip of the Fusion part changed it (%d solids, %d -> %d faces)" % (
            len(back), len(sol.Faces), len(back[0].Faces) if back else 0))
    return back[0]


def is_canonical(cfg, name):
    """profile.fusion_parts.canonical: true (every part) or a list of the parts whose solid is written to a STEP and read back (_canonical)."""
    c = cfg.get("canonical")
    return bool(c) if not isinstance(c, list) else name in c


def import_fusion_part(doc, body, bn, file_name, cfg, shift_z, parent=None, stretch=None, frame=None):
    """The Fusion body of part `bn` as the BaseFeature of `body` (see the module docstring).  stretch = dict(z=..., dz=...) or None.
    frame = App.Placement of the body's App::Part in the motor frame (None = identity).  Returns the report."""
    path, cands = find_fusion_part(file_name, cfg.get("dir", ""))
    if path is None:
        raise RuntimeError("Fusion STEP %s of %s not found; looked in: %s.  The mechanical parts of this profile are the real Fusion bodies (no "
                           "template geometry as fall-back): put the file there or set $FC_FUSION_PARTS" % (file_name, bn, " | ".join(cands)))
    shape = read_step(path)
    rule = (cfg.get("select") or {}).get(bn)
    if rule:                                                    # A.7.8: which solid(s) of the component STEP make the part (select_solids)
        sol = seed_solid(select_solids(shape, rule, file_name), rule, file_name)
    else:
        sol = max(shape.Solids, key=lambda x: x.Volume)         # a component STEP also carries the screws of the holder: the part is the biggest solid
    if is_canonical(cfg, bn):
        sol = _canonical(sol)
    src_valid, check = sol.isValid(), ""
    if not src_valid:                                           # Fusion's own B-rep: tolerance-level wire errors; volume, booleans and meshing are fine
        try:
            sol.check(True)
        except Exception as e:
            check = str(e)[:200]
    if not sol.Shells or not all(x.isClosed() for x in sol.Shells):
        raise RuntimeError("Fusion STEP %s: the part is not a closed solid" % file_name)
    expected = float((cfg.get("volumes_mm3") or {}).get(bn, 0.0))
    filler = 0.0
    if stretch and abs(stretch.get("dz", 0.0)) > 1e-12:
        sol, filler = stretch_solid(sol, float(stretch["z"]), float(stretch["dz"]))
        if expected > 0:
            expected += float(stretch["dz"]) * _section_face(sol, float(stretch["z"])).Area
    m12 = (cfg.get("placement") or {}).get(bn)
    if m12:                                                     # A.7.8: component frame -> motor frame (the Fusion occurrence the template body stands for)
        sol = transformed(sol, matrix(m12))
    if abs(shift_z) > 1e-12:
        sol = _moved(sol, shift_z)
    if frame is not None and not frame.isIdentity():           # the body lives in a placed App::Part (bearing): its BaseFeature is in that frame
        sol = transformed(sol, frame.inverse().toMatrix())
    f = doc.addObject("Part::Feature", bn + FUSION_SUFFIX)
    f.Label = bn + FUSION_SUFFIX
    f.Shape = sol
    radii = (cfg.get("thread_radii") or {}).get(bn, [])
    for prop, typ, val, tip in (
            ("SourceFile", "App::PropertyString", file_name, "Fusion STEP the shape was read from (imported once, not parametric)"),
            ("SourceSHA256", "App::PropertyString", _sha256(path), "SHA-256 of that STEP"),
            ("SourceVolume", "App::PropertyFloat", sol.Volume, "volume (mm3) of the imported solid"),
            ("FusionBodyVolume", "App::PropertyFloat", expected,
             "volume (mm3) the Fusion body must have at this motor_length (Fusion volume + stretch slab; 0 = not recorded); the gate compares it with the solid"),
            ("SourceValid", "App::PropertyBool", bool(src_valid), "BRepCheck verdict of the Fusion STEP solid; False = Fusion's B-rep carries tolerance-level wire errors (see SourceCheck), the gate accepts exactly that"),
            ("SourceCheck", "App::PropertyString", check, "message of Shape.check() when the Fusion solid is not valid"),
            ("SourceSolids", "App::PropertyInteger", len(shape.Solids), "solids in the STEP (the part is the biggest unless profile.fusion_parts.select says otherwise)"),
            ("SourcePlacement", "App::PropertyString", json.dumps(m12 or []), "Fusion occurrence matrix (component -> motor frame, 3 x 4, mm) applied to the solid; [] = identity"),
            ("AxialShift", "App::PropertyFloat", float(shift_z), "z shift (mm) applied for motor_length != ref_motor_length"),
            ("StretchDz", "App::PropertyFloat", float((stretch or {}).get("dz", 0.0)), "mm added (+) or removed (-) in the constant section of the part (cut-and-shift)"),
            ("ThreadRadii", "App::PropertyString", json.dumps(radii), "radii (mm) of the cylinders that carry a cosmetic thread (JSON list)")):
        f.addProperty(typ, prop, "Motor", tip)
        setattr(f, prop, val)
    if parent is not None:                                       # the BaseFeature must live in the same App::Part as the body
        parent.addObject(f)
    body.BaseFeature = f
    doc.recompute()
    return dict(step=file_name, sha256=f.SourceSHA256, volume=round(sol.Volume, 3), solids_in_step=len(shape.Solids), faces=len(sol.Faces),
                placement=bool(m12),
                valid=bool(src_valid), check=check, shift_z=shift_z, stretch_dz=float((stretch or {}).get("dz", 0.0)),
                stretch_filler_mm3=round(filler, 3), expected_volume=round(expected, 3))


# ---------------------------------------------------------------------------------------------- A.7.8: every part from Fusion
# CIANO28 150_35 (owner 2026-10-20: the 3-D model of the 150_35 comes from Fusion; Fusion sync decision Q5 2026-10-19: core, rotor, magnets
# and coils of the 3-D model come from the Fusion bodies, the EM solver keeps its own 2-D geometry).  Three more profile keys:
#
#   "select":      {"Magnet": {"match": "all", "seed": {"near": [0, 48.7, -15]}}, "Coil": {"match": "fuse"},
#                   "Bearing_seal": {"match": {"volume": 883.401302}, "seed": {"near": [...]}}, "Bearing_ball": {"match": {"volume": ...}, "split": "shells", ...}}
#                  which solids of the component STEP make the part (match: "largest" (default), "all", "fuse" = all solids fused into one,
#                  {"volume": v} = the solids of that volume, 1e-4 relative; split "shells" = every closed shell of them is its own solid)
#                  and which ONE of them is the template body (seed: the solid whose centre of mass is nearest, component coordinates)
#   "placement":   {"Magnet": [3 x 4 matrix, mm]}  the Fusion occurrence (component -> motor frame) the template body stands for; the
#                  template's arrays / links place the other copies (instance_check proves they land on every Fusion occurrence)
#   "occurrences": {"Magnet": [[3 x 4], ...]}  EVERY Fusion occurrence of the component, for instance_check
#   "instance_bbox_tol_mm": {"Screw_M3x30": 0.15}  optional: the template's polar array turns each copy about the motor axis, a Fusion copy
#                  may keep the seed's own orientation (a hex socket / head detail then sits a little differently in the bounding box)
#
# The template keeps its bodies, arrays and links (names, solid counts, BOM, STEP tree, GLB groups unchanged); only the shape of each seed
# body is the Fusion solid.  A missing STEP or a selection that does not resolve refuses the build (no fall-back to template geometry).

_STEP_CACHE = {}


def read_step(path):
    """Part.read with a per-process cache (one component STEP serves several bodies: the bearing rings, seal and balls)."""
    key = (path, os.path.getmtime(path))
    if key not in _STEP_CACHE:
        _STEP_CACHE[key] = Part.read(path)
    return _STEP_CACHE[key]


def matrix(m12):
    """App.Matrix of a 3 x 4 row-major matrix (Fusion transform2, translation in mm)."""
    import FreeCAD as App
    return App.Matrix(*([float(x) for x in m12] + [0.0, 0.0, 0.0, 1.0]))


def transformed(sol, m):
    """`sol` transformed by the App.Matrix m, baked into the geometry (see _moved)."""
    out = sol.copy()
    out.transformShape(m, True)
    return out


def _shell_solids(sol):
    out = []
    for sh in sol.Shells:
        if not sh.isClosed():
            raise RuntimeError("an open shell in a Fusion solid that is split into shells")
        s = Part.Solid(sh)
        if s.Volume < 0:
            s.reverse()
        out.append(s)
    return out


def select_solids(shape, rule, file_name="?"):
    """The solids of the component STEP `shape` that make the part (rule["match"], see above)."""
    m = (rule or {}).get("match", "largest")
    sols = list(shape.Solids)
    if not sols:
        raise RuntimeError("Fusion STEP %s has no solid" % file_name)
    if m == "largest":
        out = [max(sols, key=lambda x: x.Volume)]
    elif m == "all":
        out = sols
    elif m == "fuse":
        fused = sols[0].fuse(sols[1:]).removeSplitter() if len(sols) > 1 else sols[0]
        if len(fused.Solids) != 1:
            raise RuntimeError("Fusion STEP %s: the %d bodies do not fuse into one solid (%d)" % (file_name, len(sols), len(fused.Solids)))
        out = [fused.Solids[0]]
    elif isinstance(m, dict) and "volume" in m:
        v = float(m["volume"])
        out = [s for s in sols if abs(s.Volume - v) <= 1e-4 * v]
    else:
        raise RuntimeError("profile.fusion_parts.select: unknown match %r" % (m,))
    if (rule or {}).get("split") == "shells":
        out = [x for s in out for x in _shell_solids(s)]
    if not out:
        raise RuntimeError("Fusion STEP %s: no solid matches %r (volumes %s)" % (file_name, m, sorted(round(s.Volume, 4) for s in sols)))
    return out


def seed_solid(sols, rule, file_name="?"):
    """The ONE solid of `sols` that is the template body: the only one, or the one nearest to rule["seed"]["near"]."""
    if len(sols) == 1:
        return sols[0]
    near = ((rule or {}).get("seed") or {}).get("near")
    if near is None:
        raise RuntimeError("Fusion STEP %s: %d solids match and the profile names no seed (select.seed.near)" % (file_name, len(sols)))
    import FreeCAD as App
    p = App.Vector(*near)
    return min(sols, key=lambda s: (s.CenterOfMass - p).Length)


def fusion_instances(cfg, bn):
    """Every solid of part `bn` in the motor frame as Fusion places it: the matched solids x every occurrence (profile.fusion_parts)."""
    path, cands = find_fusion_part(cfg["files"][bn], cfg.get("dir", ""))
    if path is None:
        raise RuntimeError("Fusion STEP %s of %s not found; looked in: %s" % (cfg["files"][bn], bn, " | ".join(cands)))
    rule = (cfg.get("select") or {}).get(bn)
    sols = select_solids(read_step(path), rule, cfg["files"][bn])
    occ = (cfg.get("occurrences") or {}).get(bn) or [(cfg.get("placement") or {}).get(bn)]
    out = []
    for m12 in occ:
        for s in sols:
            out.append(transformed(s, matrix(m12)) if m12 else s.copy())
    return out


def model_instances(doc, bn):
    """The solids of the built model (motor frame) that stand for part `bn`: the body itself or every copy of its arrays / links."""
    from . import mechanics
    arr = {"Magnet": "MagnetArray", "Coil": "Coils"}
    if bn in arr:
        return [s.copy() for o in doc.Objects if o.Label == arr[bn] for s in o.Shape.Solids]
    if bn in ("Core", "RotorCore"):
        return [x.copy() for x in doc.getObject(bn).Shape.Solids]
    so = mechanics.solids(doc)
    key = {"Bearing_outer_ring": "_outer_ring", "Bearing_inner_ring": "_inner_ring", "Bearing_seal": "_seal_", "Bearing_ball": "_ball_"}
    if bn in key:
        return [x for k, v in so.items() if k.startswith("Bearing_618_8_") and key[bn] in k for x in v.Solids]
    if bn in ("Screw", "Screw_M3x30", "Screw_M3x6"):
        pre = {"Screw": "Screw_", "Screw_M3x30": "Screw_M3x30_", "Screw_M3x6": "Screw_M3x6_"}[bn]
        return [x for k, v in so.items() if k.startswith(pre) and k[len(pre):].isdigit() for x in v.Solids]
    return [x for x in so[bn].Solids] if bn in so else []


INSTANCE_TOL_MM = 2e-3          # centre of mass / bounding box of a model copy vs the Fusion occurrence
INSTANCE_TOL_VOL = 1e-4         # relative volume


def instance_check(doc, cfg):
    """Every Fusion occurrence of every listed part has exactly one model solid on it (same volume, centre of mass and bounding box):
    the template's arrays and links reproduce the Fusion assembly.  Returns (rows, errors)."""
    rows, errors = [], []
    for bn in sorted(cfg.get("occurrences") or {}):
        fus = fusion_instances(cfg, bn)
        mod = model_instances(doc, bn)
        free = list(range(len(mod)))
        worst_c = worst_b = worst_v = 0.0
        missing = 0
        for f in fus:
            fc, fb = f.CenterOfMass, f.BoundBox
            best = None
            for j in free:
                d = (mod[j].CenterOfMass - fc).Length
                if best is None or d < best[0]:
                    best = (d, j)
            if best is None or best[0] > INSTANCE_TOL_MM:
                missing += 1
                continue
            j = best[1]
            free.remove(j)
            mb = mod[j].BoundBox
            db = max(abs(getattr(mb, a) - getattr(fb, a)) for a in ("XMin", "YMin", "ZMin", "XMax", "YMax", "ZMax"))
            dv = abs(mod[j].Volume - f.Volume) / max(f.Volume, 1e-12)
            worst_c, worst_b, worst_v = max(worst_c, best[0]), max(worst_b, db), max(worst_v, dv)
        tol_b = max(INSTANCE_TOL_MM, float((cfg.get("instance_bbox_tol_mm") or {}).get(bn, 0.0)))   # a copy turned about its own axis
        ok = missing == 0 and not free and worst_b <= tol_b and worst_v <= INSTANCE_TOL_VOL
        rows.append(dict(part=bn, fusion=len(fus), model=len(mod), unmatched_fusion=missing, unmatched_model=len(free),
                         max_centre_mm=round(worst_c, 6), max_bbox_mm=round(worst_b, 6), bbox_tol_mm=tol_b, max_rel_volume=float("%.3g" % worst_v),
                         ok=ok))
        if not ok:
            errors.append("Fusion occurrences of %s: %d in Fusion, %d in the model, %d / %d unmatched, max centre %.4f mm, bbox %.4f mm, "
                          "volume %.2g (the template copies do not land on the Fusion assembly)" % (
                              bn, len(fus), len(mod), missing, len(free), worst_c, worst_b, worst_v))
    return rows, errors
