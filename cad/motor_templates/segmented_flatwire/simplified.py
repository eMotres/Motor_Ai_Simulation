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
  * the phase board  ->  ONE plain solid piece: the board outline extruded to the board thickness, with its through holes (mounting
    holes, the holes the coil terminals pass through, windows, notches of the outline).  No pins, components, copper, pads,
    silkscreen, mask or imported board body; the terminal positions go into the JSON.
  * what a customer integrates stays as built: shaft / rotor carrier, stator holders (outer shape and mounting holes), rings,
    Prop_fix, screws (the full variant).

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


#: an imported board draws its round windows, holes and notch radii as polygons of hundreds of short lines; a closed wire of at least this
#: many straight edges is re-drawn as the few arcs and lines it really is (every vertex within ARC_TOL of its arc / line), so the board
#: has one cylinder face per round feature instead of one plane per segment
POLYGON_MIN_EDGES = 24
ARC_TOL = 0.01
ARC_MIN_POINTS = 5
ARC_MAX_STEP = 0.25                      # longest step of an arc, in radii


def _circle_through(a, b, c):
    """(centre x, y, radius) of the circle through three points, or None when they are (nearly) collinear."""
    ax, ay, bx, by, cx, cy = a.x, a.y, b.x, b.y, c.x, c.y
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    return ux, uy, math.hypot(ax - ux, ay - uy)


def _fits_line(pts, i, j):
    a, b = pts[i], pts[j]
    ln = math.hypot(b.x - a.x, b.y - a.y)
    if ln < 1e-12:
        return False
    return all(abs((b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x)) / ln <= ARC_TOL for p in pts[i + 1:j])


def _fits_arc(pts, i, j):
    if j - i + 1 < ARC_MIN_POINTS:
        return False
    if math.hypot(pts[i].x - pts[j].x, pts[i].y - pts[j].y) < 1e-6:                  # a closed loop is not an arc between two points
        return False
    c = _circle_through(pts[i], pts[(i + j) // 2], pts[j])
    if c is None or c[2] > 1e4:
        return False
    if not all(abs(math.hypot(p.x - c[0], p.y - c[1]) - c[2]) <= ARC_TOL for p in pts[i:j + 1]):
        return False
    # an arc is drawn as MANY short steps: one long edge between two points that happen to lie on the circle is a chord, not an arc
    if any(math.hypot(pts[k + 1].x - pts[k].x, pts[k + 1].y - pts[k].y) > ARC_MAX_STEP * c[2] for k in range(i, j)):
        return False
    sweep, prev = 0.0, math.atan2(pts[i].y - c[1], pts[i].x - c[0])
    for p in pts[i + 1:j + 1]:                                                        # the arc must not wrap more than a full turn
        a = math.atan2(p.y - c[1], p.x - c[0])
        sweep += abs((a - prev + math.pi) % (2 * math.pi) - math.pi)
        prev = a
    return sweep < 2 * math.pi * 0.97


def _simplify_polygon(wire):
    """A long all-straight closed wire as the arcs and lines it approximates (greedy fit from a sharp corner), else the wire itself."""
    import Part
    if len(wire.Edges) < POLYGON_MIN_EDGES or any(e.Curve.__class__.__name__ != "Line" for e in wire.Edges):
        return wire
    pts = [v.Point for v in Part.Wire(Part.__sortEdges__(wire.Edges)).OrderedVertexes]
    n = len(pts)
    turn = []
    for k in range(n):
        p0, p1, p2 = pts[k - 1], pts[k], pts[(k + 1) % n]
        a1 = math.atan2(p1.y - p0.y, p1.x - p0.x)
        a2 = math.atan2(p2.y - p1.y, p2.x - p1.x)
        turn.append(abs((a2 - a1 + math.pi) % (2 * math.pi) - math.pi))
    s = max(range(n), key=lambda k: turn[k])
    if turn[s] < math.radians(5.0):                                                  # smooth all round: one circle
        cx, cy = sum(p.x for p in pts) / n, sum(p.y for p in pts) / n
        r = sum(math.hypot(p.x - cx, p.y - cy) for p in pts) / n
        if max(abs(math.hypot(p.x - cx, p.y - cy) - r) for p in pts) <= ARC_TOL:
            return Part.Wire(Part.makeCircle(r, _V(cx, cy, pts[0].z)))
        return wire
    pts = pts[s:] + pts[:s] + [pts[s]]
    edges, i = [], 0
    while i < len(pts) - 1:
        best_arc = None
        j = i + ARC_MIN_POINTS - 1
        while j < len(pts) and _fits_arc(pts, i, j):
            best_arc, j = j, j + 1
        best_line = None
        j = i + 1
        while j < len(pts) and _fits_line(pts, i, j):
            best_line, j = j, j + 1
        if best_arc is not None and best_arc >= (best_line or i + 1):
            edges.append(Part.Arc(pts[i], pts[(i + best_arc) // 2], pts[best_arc]).toShape())
            i = best_arc
        else:
            edges.append(Part.makeLine(pts[i], pts[best_line or i + 1]))
            i = best_line or i + 1
    out = Part.Wire(edges)
    return out if out.isClosed() else wire


def board_plate(body, thickness_solids):
    """(solid, report): ONE plain board.  The outline and the through holes are the closed wires of ``body`` sliced at mid thickness
    (the biggest is the outline, every other one a hole); the piece is extruded over the whole board thickness (the z extent of
    ``thickness_solids``: body, mask, copper ... - but not the pins).  Nothing else of the board survives."""
    import Part
    bb = body.BoundBox
    zmid = (bb.ZMin + bb.ZMax) / 2.0
    z0 = min(s.BoundBox.ZMin for s in thickness_solids)
    z1 = max(s.BoundBox.ZMax for s in thickness_solids)
    raw = [w for w in body.slice(_V(0, 0, 1), zmid) if w.isClosed()]
    if not raw:
        raise ValueError("the board has no outline at mid thickness")
    areas = sorted((Part.Face(w).Area for w in raw), reverse=True)
    area0 = areas[0] - sum(areas[1:])                                        # the net area as the body draws it (polygons and all)
    wires = [_simplify_polygon(w) for w in raw]
    faces = sorted([Part.Face(w) for w in wires], key=lambda f: -f.Area)
    plate = outer = faces[0]
    for h in faces[1:]:
        if abs(outer.common(h).Area - h.Area) > 1e-6 * max(h.Area, 1.0):
            raise ValueError("a wire of the board lies outside its outline")
        plate = plate.cut(h)
    plate = plate.copy()
    net = plate.Area
    if abs(net - area0) > 1e-3 * area0:
        raise ValueError("re-drawing the board outline as arcs and lines changed its area (%.4f -> %.4f mm2)" % (area0, net))
    plate.translate(_V(0, 0, z0 - zmid))
    sol = plate.extrude(_V(0, 0, z1 - z0))
    if len(sol.Solids) != 1:
        raise ValueError("the board is %d solids" % len(sol.Solids))
    return sol.Solids[0], dict(z=[round(z0, 6), round(z1, 6)], area_mm2=round(net, 6), holes=len(faces) - 1,
                               edges=sum(len(w.Edges) for w in wires))


def check_board(board, brep):
    """[] when the board is ONE plain solid piece with holes: one solid, one shell, planes and cylinders only, a face per outline /
    hole edge plus the two sides, and a volume of net area x thickness."""
    bad = []
    if len(board.Solids) != 1 or len(board.Shells) != 1:
        bad.append("the board is %d solids / %d shells (one piece, no components)" % (len(board.Solids), len(board.Shells)))
    for f in board.Faces:
        if f.Surface.__class__.__name__ not in ("Plane", "Cylinder"):
            bad.append("board face of type %s (only planes and cylinders)" % f.Surface.__class__.__name__)
            break
    if len(board.Faces) > 2 + brep["edges"]:
        bad.append("board has %d faces (outline + holes allow %d): detail is left" % (len(board.Faces), 2 + brep["edges"]))
    want = brep["area_mm2"] * (brep["z"][1] - brep["z"][0])
    if abs(board.Volume - want) > VOLUME_TOL * want:
        bad.append("board volume %.4f differs from outline area x thickness %.4f" % (board.Volume, want))
    return bad


def check(monolith, rep, coil_blocks, board=None, board_rep=None):
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
    if board is not None:
        bad += check_board(board, board_rep)
    return bad
