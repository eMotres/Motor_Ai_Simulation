"""SIMPLIFIED model of every motor (owner 2026-10-15): an envelope-only copy that can be downloaded at once; the real model stays
behind the NDA and the applicant verification.

Built by ``cad/fc_scripts/export_simplified.py`` from the built, gated model (full AND frameless alike):

  * stator + rotor + magnets  ->  ONE monolithic solid: the outer envelope (stator outer circle, stator / rotor end faces) and the
    rotor bore with its keys (the shaft interface) exactly; everything inside is FILLED - slots, teeth, yoke cut-outs, magnet
    pockets, magnet shapes, the air gap.  Nothing of the electromagnetic construction can be read back from it.
  * every coil  ->  ONE thick turn: the winding's U-shaped outline (inner to outer turn, end turn included) extruded over the
    whole winding height, plus the two terminal stubs where the first and the last turn leave it; no single turns, no wire.
    (The coil blocks lie inside the filled slots: the solids overlap by design - this is an envelope model, not an assembly
    for interference checks.)
  * bearings  ->  one solid ring per bearing, d x D x B: no balls, cage or seals.
  * what a customer integrates stays as built: shaft / rotor carrier, stator holders (outer shape and mounting holes), rings,
    Prop_fix, screws (the full variant); the phase board outline and its terminals (the pins and the board body; the copper
    layers of an imported board are dropped).

``check()`` is the patent-hygiene gate the export runs before it writes a file: the monolith is a single solid whose faces are
only the envelope (no face between the bore and the outer circle except the planar ends), its volume equals the envelope
volume, and every coil block is one solid with few faces.  A failed check refuses the simplified files (nothing is written).
"""
import math

#: the monolith may have at most this many faces: outer cylinder + 2 end planes + one face per edge of the bore profile (the bore
#: and its keys) - and a few more when the stator and rotor lengths / radii differ (a step).  More means internal detail leaked.
MONOLITH_BASE_FACES = 3
MONOLITH_STEP_FACES = 4
#: a coil block: U outline (its edges) + 2 caps + the stubs; a hard ceiling that no single-turn construction can stay under
COIL_MAX_FACES = 60
VOLUME_TOL = 1e-6          # relative: monolith volume vs envelope volume


def _V(*a):
    import FreeCAD as App
    return App.Vector(*a)


def bore_face(rotor, zmid=None):
    """Planar face of the rotor bore (with its keys) at z = zmid: of the closed slice wires that enclose the axis, the smallest."""
    import Part
    bb = rotor.BoundBox
    zmid = (bb.ZMin + bb.ZMax) / 2.0 if zmid is None else zmid
    best = None
    for w in rotor.slice(_V(0, 0, 1), zmid):
        if not w.isClosed():
            continue
        f = Part.Face(w)
        if f.isInside(_V(0, 0, zmid), 1e-6, True) and (best is None or f.Area < best.Area):
            best = f
    if best is None:
        raise ValueError("no bore found in the rotor slice")
    return best, zmid


def build_monolith(core, rotor, magnets, r_stator_out, r_rotor_out):
    """(solid, report): stator + rotor + magnets as ONE annulus-like solid.  ``magnets`` is a list of solids."""
    import Part
    zs = [core.BoundBox.ZMin, core.BoundBox.ZMax]
    zr = [min([rotor.BoundBox.ZMin] + [m.BoundBox.ZMin for m in magnets]), max([rotor.BoundBox.ZMax] + [m.BoundBox.ZMax for m in magnets])]
    face, zmid = bore_face(rotor)
    stator = Part.makeCylinder(r_stator_out, zs[1] - zs[0], _V(0, 0, zs[0]))
    rot = Part.makeCylinder(r_rotor_out, zr[1] - zr[0], _V(0, 0, zr[0]))
    z0, z1 = min(zs[0], zr[0]), max(zs[1], zr[1])
    f = face.copy()
    f.translate(_V(0, 0, z0 - zmid))
    hole = f.extrude(_V(0, 0, z1 - z0))
    body = stator.fuse(rot).removeSplitter().cut(hole).removeSplitter()
    sol = body.Solids
    if len(sol) != 1:
        raise ValueError("the monolith is %d solids" % len(sol))
    rep = dict(z=[round(z0, 6), round(z1, 6)], stator_outer_r=round(r_stator_out, 6), rotor_outer_r=round(r_rotor_out, 6),
               bore_area_mm2=round(face.Area, 6), bore_edges=len(face.OuterWire.Edges),
               stepped=not (zr[0] >= zs[0] - 1e-6 and zr[1] <= zs[1] + 1e-6 and r_rotor_out <= r_stator_out + 1e-6))   # rotor sticks out of the stator
    return sol[0], rep


def coil_block(coil, ytop, wire_h, pitch, n_layers):
    """ONE thick turn of the base coil (local frame, centred on +Y): the outline of the winding in the plane of a middle turn,
    extruded from the bottom turn to the top turn, plus the terminal stubs (first and last turn) at their own place."""
    import Part
    y_hi = ytop
    y_lo = ytop - wire_h - (n_layers - 1) * pitch

    def outline(k):
        y = ytop - wire_h / 2.0 - k * pitch
        wires = [w for w in coil.slice(_V(0, 1, 0), y) if w.isClosed()]
        if not wires:
            raise ValueError("turn %d has no section" % k)
        w = max(wires, key=lambda w: Part.Face(w).Area)
        return Part.Face(w), y

    def slab(face, y, lo, hi):
        f = face.copy()
        f.translate(_V(0, lo - y, 0))
        return f.extrude(_V(0, hi - lo, 0))

    mid, ym = outline(min(1, n_layers - 1))
    body = slab(mid, ym, y_lo, y_hi)
    for k in (0, n_layers - 1):                                   # the terminals: the first and the last turn leave the block
        fk, yk = outline(k)
        body = body.fuse(slab(fk, yk, yk - wire_h / 2.0, yk + wire_h / 2.0))
    body = body.removeSplitter()
    if len(body.Solids) != 1:
        raise ValueError("the coil block is %d solids" % len(body.Solids))
    return body.Solids[0]


def bearing_ring(outer_ring, d, z_range=None):
    """One solid ring d x D x B from an outer ring solid (its radius and axial extent) and the bore diameter d."""
    import Part
    bb = outer_ring.BoundBox
    z0, z1 = (bb.ZMin, bb.ZMax) if z_range is None else z_range
    R = max(abs(bb.XMax), abs(bb.XMin))
    big = Part.makeCylinder(R, z1 - z0, _V(0, 0, z0))
    small = Part.makeCylinder(d / 2.0, z1 - z0, _V(0, 0, z0))
    return big.cut(small).removeSplitter().Solids[0]


def check(monolith, rep, coil_blocks):
    """The patent-hygiene gate: [] when the simplified solids are envelope only, else the reasons."""
    import math as _m
    bad = []
    faces = monolith.Faces
    limit = MONOLITH_BASE_FACES + rep["bore_edges"] + (MONOLITH_STEP_FACES if rep["stepped"] else 0)
    if len(faces) > limit:
        bad.append("monolith has %d faces (envelope + a bore of %d edges allow %d): internal detail is left" % (len(faces), rep["bore_edges"], limit))
    z0, z1 = rep["z"]
    lz = z1 - z0
    env = _m.pi * rep["stator_outer_r"] ** 2 * lz - rep["bore_area_mm2"] * lz
    if rep["stepped"]:
        env = None                                   # a stepped envelope: the volume is the sum of its two cylinders, checked below
    if env is not None and abs(monolith.Volume - env) > VOLUME_TOL * env:
        bad.append("monolith volume %.4f differs from the envelope volume %.4f: cavities or detail inside" % (monolith.Volume, env))
    if len(monolith.Solids) != 1 or monolith.Shells and len(monolith.Shells) != 1:
        bad.append("monolith is not one solid with one shell (an internal cavity would be a second shell)")
    ro = rep["stator_outer_r"]
    for f in faces:                                  # every curved face is the outer circle, the rotor outer circle, the bore or a key face
        if f.Surface.__class__.__name__ == "Cylinder" and f.Surface.Radius > rep["rotor_outer_r"] + 1e-6 and abs(f.Surface.Radius - ro) > 1e-6:
            bad.append("a cylindrical face of radius %.3f is neither the envelope nor the bore" % f.Surface.Radius)
        if f.Surface.__class__.__name__ not in ("Cylinder", "Plane"):
            bad.append("face of type %s in the monolith" % f.Surface.__class__.__name__)
    for i, c in enumerate(coil_blocks):
        if len(c.Solids) != 1 or len(c.Faces) > COIL_MAX_FACES:
            bad.append("coil block %d: %d solids, %d faces (a single thick turn has at most %d)" % (i + 1, len(c.Solids), len(c.Faces), COIL_MAX_FACES))
            break
    return bad
