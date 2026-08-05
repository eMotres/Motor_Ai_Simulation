"""A ROTATABLE rotor with fixed mesh topology: the 3D sliding band.

The blocker this closes
----------------------
Torque by the energy method is ``dW'/dtheta``, so it needs the machine at two or
three rotor positions.  Re-meshing between them is not an option: the mesh
difference is orders of magnitude larger than the torque signal, and the
resulting number would be a property of gmsh's Delaunay seed.  What is needed is
a rotation that is a RE-LABELLING — element geometry bit-identical at every
angle, and only the coupling across one cylinder changed.

The 2D solver already does this (``mesher._weld_belt_into_half`` +
``p2_projection.SlipProjection``).  This module is the 3D port, and the three
things that are genuinely different in 3D are stated up front:

1.  **The cross-section is built in TWO pieces, not one.**  Stage A meshes the
    whole wedge — iron, magnets, gap and far air — as one gmsh mesh with a free
    Delaunay air gap.  Here the wedge is cut at the MID-GAP radius into a rotor
    piece (``r <= r_mid``) and a stator piece (``r >= r_mid``), and both carry
    the SAME uniform ring of ``N`` vertices on that circle.  The ring is a
    geometric boundary polygon of each piece, so gmsh is obliged to put a mesh
    node on every one of them, and the check that it did (and put no others
    there) is a hard gate, not a hope: :func:`_ring_nodes`.

    The two pieces share no nodes.  The mesh is deliberately TORN at ``r_mid``
    and the constraint below stitches it, which is exactly why rotating costs
    nothing: neither piece ever moves.

2.  **The weld is on NEDELEC EDGE dofs.**  The interface is a prismatic surface
    (ring index ``k`` x axial level ``l``), and every edge lying in it is one of
    three families — axial ``(k,l)-(k,l+1)``, angular ``(k,l)-(k+1,l)``, and the
    DIAGONAL of each quad face.  All three have to be welded or the tangential
    trace is not continuous and the gap is torn rather than coarsely coupled.

    The diagonal is the delicate one.  ``motor_mesh.extrude_section`` splits the
    prism over a base edge ``(a, b)`` through whichever of ``a``, ``b`` has the
    smaller split KEY, so the two pieces only agree on where the diagonals run if
    their ring keys are monotone in the ring index — on BOTH sides, and under the
    shift.  :func:`ring_split_key` builds exactly that key, and it has to do it
    without breaking the OTHER thing the key is for (making the two radial cut
    planes carry identical triangulations).  The two requirements are compatible
    for one reason and it is worth writing down: the ring node is the outermost
    cut node of the rotor piece and the innermost of the stator piece, so giving
    every ring node a key ABOVE every other key puts it on the same side of its
    radial neighbour on both cut planes.

3.  **Two signs, not one.**  A welded edge pair carries the anti-periodic WRAP
    sign (a rotor ring node pushed past ``theta = sector`` re-enters at
    ``theta = 0`` with ``bc_sign``) and its own ORIENTATION sign (scikit-fem
    orients every edge from the lower vertex index to the higher, and vertex
    numbering knows nothing about the rotation).  Get either wrong and the solve
    still converges — to a machine with a few reversed edges in its air gap.

    The sector's anti-periodic cut constraint and the slip constraint are
    composed in ONE SIGNED UNION-FIND rather than applied in sequence, because a
    ring node on a cut plane belongs to both.  :class:`SlipWeld` does that, and
    then VERIFIES every constraint it was given against the classes it produced
    — a signed union-find silently drops a contradictory union, and a dropped
    constraint is indistinguishable downstream from a physics error.

Legal angles
------------
Rotation is by integer multiples of the ring pitch ``2 pi / n_ring``.  That is
not a limitation of the method but its definition: at any other angle the two
rings do not correspond and something would have to be interpolated, which is
where 2D sliding bands lose their conservation properties.  ``n_ring`` is chosen
so the pitch is small enough to central-difference: at ``n_ring = 360`` the arc
is 0.21 mm against a 0.20 mm gap.

Units: mm in the cross-section (gmsh's OCC tolerances in this project are tuned
there), metres everywhere the solver sees.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .meshes import _GMSH_LOCK
from .motor_geometry import MM, MotorSection
from .motor_mesh import Section2D, _poly_parts, _shapely_rings, _tri_area

_TOL_MM = 1e-6


# ==========================================================================
# the three-piece cross-section
# ==========================================================================

@dataclass
class BandedSection:
    """A cross-section torn at the mid-gap radius, plus the ring bookkeeping."""
    sect: Section2D
    n_ring: int                 # nodes on the FULL 360 deg ring
    rring: np.ndarray           # (n_sec_ring,) 2D node ids, rotor side
    sring: np.ndarray           # (n_sec_ring,) 2D node ids, stator side
    n_rotor_nodes: int          # 2D nodes belonging to the rotor piece
    r_mid_mm: float
    sector_rad: float
    bc_sign: float
    meta: dict = field(default_factory=dict)

    @property
    def n_sec_ring(self) -> int:
        """Ring nodes in the OPEN sector wedge (both cut columns included)."""
        return int(self.rring.size)

    @property
    def pitch_rad(self) -> float:
        return 2.0 * math.pi / float(self.n_ring)

    @property
    def pitch_deg(self) -> float:
        return 360.0 / float(self.n_ring)

    def shift_angle_deg(self, m: int) -> float:
        return float(m) * self.pitch_deg


def _uniform_ring_xy(r_mm: float, n_ring: int, a_sec: float) -> np.ndarray:
    """(2, n) vertices of the mid-gap ring inside ``[0, a_sec]``, both ends kept."""
    n_cell = int(round(a_sec / (2.0 * math.pi) * n_ring))
    if abs(n_cell * (2.0 * math.pi / n_ring) - a_sec) > 1e-12:
        raise ValueError(
            f"n_ring={n_ring} does not divide the sector: {a_sec*180/math.pi:.4f} "
            "deg is not an integer number of ring pitches, so a rotor node would "
            "never land on a stator node and the weld would have to interpolate")
    th = np.arange(n_cell + 1) * (2.0 * math.pi / n_ring)
    return np.vstack([r_mm * np.cos(th), r_mm * np.sin(th)])


def _classify(p2: np.ndarray, t: np.ndarray, regions, names: Dict[str, int]
              ) -> np.ndarray:
    """Region id per triangle from its centroid, against a GIVEN name map."""
    import shapely
    from shapely import STRtree

    polys, ids = [], []
    for r in regions:
        for part in _poly_parts(r.polygon):
            polys.append(part)
            ids.append(names[r.name])
    reg = np.zeros(t.shape[1], dtype=np.int64)
    if not polys:
        return reg
    c = p2[:, t].mean(axis=1)
    pts = shapely.points(c[0], c[1])
    hits = STRtree(polys).query(pts, predicate="within")
    if hits.size:
        reg[hits[0]] = np.asarray(ids, dtype=np.int64)[hits[1]]
    return reg


def _pair_cut(p: np.ndarray, a_sec: float, tol: float = 1e-5
              ) -> Tuple[np.ndarray, np.ndarray]:
    """theta = 0 node ids paired with theta = a_sec ones, by radius."""
    x, y = p[0], p[1]
    r = np.hypot(x, y)
    ca, sa = math.cos(a_sec), math.sin(a_sec)
    m = np.flatnonzero((np.abs(y) < tol) & (x > tol))
    s = np.flatnonzero((np.abs(x * sa - y * ca) < tol) & ((x * ca + y * sa) > tol))
    if m.size != s.size:
        raise RuntimeError(f"cut lines carry {m.size} vs {s.size} nodes — "
                           "periodic meshing did not take on this piece")
    if m.size == 0:
        return m, s
    m = m[np.argsort(r[m])]
    s = s[np.argsort(r[s])]
    dr = float(np.max(np.abs(r[m] - r[s])))
    if dr > 1e-6:
        raise RuntimeError(f"cut node radii differ by {dr:.3e} mm — periodic "
                           "meshing did not take on this piece")
    return m, s


def _mesh_piece(regions, domain_xy: np.ndarray, a_sec: float,
                r_out_mm: float, names: Dict[str, int],
                h_gap: float, h_solid: float, h_far: float,
                grade_far: float, gap_r_lo: float, gap_r_hi: float,
                ring_xy: np.ndarray, verbose: bool = False):
    """Mesh ONE piece of the cross-section (rotor side or stator side).

    ``domain_xy`` is the piece's outline in mm, walked once, open (no repeated
    first point).  Its vertices become gmsh geometry POINTS, which is what makes
    the mid-gap ring exactly reproducible on both pieces: a geometry point is
    always a mesh node.
    """
    import gmsh

    solids = [(r.name, part) for r in regions for part in _poly_parts(r.polygon)]

    with _GMSH_LOCK:
        try:
            gmsh.initialize([], interruptible=False)
        except TypeError:
            gmsh.initialize()
        try:
            gmsh.option.setNumber("General.Terminal", 1 if verbose else 0)
            gmsh.option.setNumber("Geometry.Tolerance", 1e-7)
            gmsh.option.setNumber("Geometry.ToleranceBoolean", 1e-4)
            gmsh.model.add("static3d_band_piece")
            occ = gmsh.model.occ

            ptcache: Dict[Tuple[float, float], int] = {}

            def _pt(x, y):
                k = (round(x, 8), round(y, 8))
                tag = ptcache.get(k)
                if tag is None:
                    tag = occ.addPoint(float(x), float(y), 0.0)
                    ptcache[k] = tag
                return tag

            def _loop(coords):
                pts = [_pt(*c) for c in coords]
                lines = []
                for i in range(len(pts)):
                    a, b = pts[i], pts[(i + 1) % len(pts)]
                    if a != b:
                        lines.append(occ.addLine(a, b))
                return occ.addCurveLoop(lines)

            def _surface(poly):
                return occ.addPlaneSurface(
                    [_loop(r) for r in _shapely_rings(poly)])

            dom = occ.addPlaneSurface([_loop([tuple(c) for c in domain_xy.T])])
            tools = [_surface(pl) for _nm, pl in solids]
            if tools:
                occ.fragment([(2, dom)], [(2, t) for t in tools])
            occ.synchronize()

            # size fields, exactly Stage A's two-threshold construction
            solid_curves = _interface_curves(gmsh, regions, r_out_mm)
            gap_curves = []
            for c in solid_curves:
                try:
                    cm = gmsh.model.occ.getCenterOfMass(1, c)
                except Exception:
                    continue
                rc = math.hypot(cm[0], cm[1])
                if gap_r_lo <= rc <= gap_r_hi:
                    gap_curves.append(c)
            if not gap_curves:
                gap_curves = solid_curves

            f = gmsh.model.mesh.field
            f.add("Distance", 1)
            f.setNumbers(1, "CurvesList", [float(c) for c in solid_curves])
            f.setNumber(1, "Sampling", 200)
            f.add("Threshold", 2)
            f.setNumber(2, "InField", 1)
            f.setNumber(2, "SizeMin", h_solid)
            f.setNumber(2, "SizeMax", h_far)
            f.setNumber(2, "DistMin", grade_far)
            f.setNumber(2, "DistMax", max(2.0 * grade_far, 0.30 * r_out_mm))

            f.add("Distance", 3)
            f.setNumbers(3, "CurvesList", [float(c) for c in gap_curves])
            f.setNumber(3, "Sampling", 400)
            f.add("Threshold", 4)
            f.setNumber(4, "InField", 3)
            f.setNumber(4, "SizeMin", h_gap)
            f.setNumber(4, "SizeMax", h_far)
            f.setNumber(4, "DistMin", 0.30)
            f.setNumber(4, "DistMax", max(2.5, 6.0 * h_solid))

            f.add("Min", 5)
            f.setNumbers(5, "FieldsList", [2.0, 4.0])
            f.setAsBackgroundMesh(5)
            gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
            gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
            gmsh.option.setNumber("Mesh.MeshSizeMin", 0.25 * h_gap)
            gmsh.option.setNumber("Mesh.MeshSizeMax", h_far)
            gmsh.option.setNumber("Mesh.Algorithm", 6)

            _set_cut_periodicity(gmsh, a_sec, r_out_mm)
            gmsh.model.mesh.generate(2)

            node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
            node_tags = np.asarray(node_tags, dtype=np.int64)
            xyz = np.asarray(node_coords, dtype=float).reshape(-1, 3).T
            lut = np.zeros(int(node_tags.max()) + 1, dtype=np.int64)
            lut[node_tags] = np.arange(node_tags.size, dtype=np.int64)
            etypes, _, enodes = gmsh.model.mesh.getElements(2)
            tt = [lut[np.asarray(enodes[k], dtype=np.int64)].reshape(-1, 3).T
                  for k, et in enumerate(etypes) if int(et) == 2]
            if not tt:
                raise RuntimeError("gmsh produced no triangles for this piece")
            t = np.hstack(tt)
        finally:
            try:
                gmsh.finalize()
            except Exception:
                pass

    if np.unique(np.sort(t, axis=0), axis=1).shape[1] != t.shape[1]:
        raise RuntimeError("duplicated triangles in a band piece")
    p2 = xyz[:2]
    reg = _classify(p2, t, regions, names)
    for r in regions:
        got = _tri_area(p2, t[:, reg == names[r.name]])
        if abs(got - r.area_mm2) > 1e-5 * max(r.area_mm2, 1.0):
            raise RuntimeError(
                f"region {r.name!r} meshed at {got:.6f} mm^2 but its polygon is "
                f"{r.area_mm2:.6f} mm^2 — the piece's boolean lost geometry")

    used = np.unique(t)
    remap = -np.ones(p2.shape[1], dtype=np.int64)
    remap[used] = np.arange(used.size, dtype=np.int64)
    p2 = p2[:, used]
    t = remap[t]
    a = p2[:, t[0]]
    e1 = p2[:, t[1]] - a
    e2 = p2[:, t[2]] - a
    flip = (e1[0] * e2[1] - e1[1] * e2[0]) < 0
    if flip.any():
        t[1, flip], t[2, flip] = t[2, flip].copy(), t[1, flip].copy()

    masters, slaves = _pair_cut(p2, a_sec)
    ring = _ring_nodes(p2, ring_xy)
    return p2, t, reg, masters, slaves, ring


def _interface_curves(gmsh, regions, r_out: float, tol: float = 2e-4
                      ) -> List[int]:
    """Curve tags on a material interface OR on the piece's own outline."""
    from shapely.geometry import Point
    from shapely.ops import unary_union

    out: List[int] = []
    bnd = unary_union([r.polygon.boundary for r in regions]) if regions else None
    for (d, c) in gmsh.model.getEntities(1):
        try:
            cm = gmsh.model.occ.getCenterOfMass(1, c)
        except Exception:
            continue
        if math.hypot(cm[0], cm[1]) > 0.999 * r_out:
            continue
        if bnd is not None and bnd.distance(Point(cm[0], cm[1])) < tol:
            out.append(int(c))
    if not out:
        raise RuntimeError("no material-interface curves found in this piece")
    return sorted(set(out))


def _set_cut_periodicity(gmsh, a_sec: float, r_out: float) -> int:
    """``setPeriodic`` the theta = 0 cut curves onto the theta = a_sec ones."""
    ca, sa = math.cos(a_sec), math.sin(a_sec)
    aff = [ca, -sa, 0.0, 0.0, sa, ca, 0.0, 0.0,
           0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    masters: List[Tuple[float, int]] = []
    slaves: List[Tuple[float, int]] = []
    for (d, c) in gmsh.model.getEntities(1):
        cm = gmsh.model.occ.getCenterOfMass(1, c)
        x, y = cm[0], cm[1]
        r = math.hypot(x, y)
        if r < 1e-9 or r > r_out * 1.001:
            continue
        if abs(y) < 1e-6 and x > 0:
            masters.append((r, c))
        elif abs(x * sa - y * ca) < 1e-6 and (x * ca + y * sa) > 0:
            slaves.append((r, c))
    masters.sort()
    slaves.sort()
    if len(masters) != len(slaves):
        raise RuntimeError(
            f"piece cut curves do not correspond: {len(masters)} vs "
            f"{len(slaves)} — the piece is not periodic at this angle")
    for (r1, cm_), (r2, cs) in zip(masters, slaves):
        if abs(r1 - r2) > 1e-4:
            raise RuntimeError(
                f"piece cut curve radii mismatch: {r1:.6f} vs {r2:.6f} mm")
        gmsh.model.mesh.setPeriodic(1, [cs], [cm_], aff)
    return len(masters)


def _ring_nodes(p2: np.ndarray, ring_xy: np.ndarray,
                tol_mm: float = 2e-6) -> np.ndarray:
    """The piece's node ids on the mid-gap ring, in ring-index order.

    A hard gate rather than a search: the ring vertices were handed to gmsh as
    geometry points, so EVERY one of them must be a node and NOTHING else may
    sit on that circle.  gmsh subdividing a ring segment (because a size field
    asked for something smaller than the pitch) would silently give the two
    pieces different rings and the weld would be between edges that are not each
    other's images.
    """
    from scipy.spatial import cKDTree

    r_ring = float(np.hypot(ring_xy[0, 0], ring_xy[1, 0]))
    r = np.hypot(p2[0], p2[1])
    on = np.flatnonzero(np.abs(r - r_ring) < 1e-4)
    if on.size != ring_xy.shape[1]:
        raise RuntimeError(
            f"{on.size} mesh nodes sit on the mid-gap circle but the ring has "
            f"{ring_xy.shape[1]} vertices — gmsh subdivided (or merged) the ring "
            "segments, so the two pieces no longer carry the same ring.  Raise "
            "h_gap above the ring pitch, or lower n_ring.")
    d, idx = cKDTree(p2[:, on].T).query(ring_xy.T)
    if float(np.max(d)) > tol_mm:
        raise RuntimeError(
            f"a ring vertex is {float(np.max(d)):.3e} mm from the nearest mesh "
            "node — the ring is not exact")
    out = on[idx]
    if np.unique(out).size != out.size:
        raise RuntimeError("two ring vertices matched the same mesh node")
    return out.astype(np.int64)


def ring_split_key(nn: int, masters: np.ndarray, slaves: np.ndarray,
                   rring: np.ndarray, sring: np.ndarray) -> np.ndarray:
    """The prism-split key that makes the interface diagonals correspond.

    Two requirements, and they are compatible for one specific reason:

    * **the radial cuts** need the two cut planes to carry identical
      triangulations, which ``motor_mesh`` gets by giving a slave cut node its
      master's key;
    * **the mid-gap ring** needs its key MONOTONE in the ring index on both
      pieces, or the quad-face diagonals run one way on the rotor and the other
      way on the stator and the two surfaces are not the same triangulation at
      all.

    A ring node is itself a cut node (the ring's first and last vertex sit on
    the two cut planes), so the second rule overrides the first there.  That is
    safe because the ring node is the OUTERMOST cut node of the rotor piece and
    the INNERMOST of the stator piece: giving every ring node a key above every
    other key puts it on the same side of its radial cut neighbour on both
    planes, which is all the cut-plane triangulation actually depends on.
    """
    key = np.arange(nn, dtype=np.int64)
    if slaves.size:
        key[slaves] = key[masters]
    key[np.asarray(rring, dtype=np.int64)] = nn + 2 * np.arange(rring.size)
    key[np.asarray(sring, dtype=np.int64)] = nn + 2 * np.arange(sring.size) + 1
    return key


def build_banded_section(section: MotorSection,
                         n_ring: int = 360,
                         r_box_mm: Optional[float] = None,
                         box_factor: float = 4.0,
                         h_gap: float = 0.35,
                         h_solid: float = 1.2,
                         h_far: Optional[float] = None,
                         grade_far: float = 1.0,
                         verbose: bool = False) -> BandedSection:
    """The THREE-PIECE cross-section: rotor piece | mid-gap ring | stator piece.

    The pieces are meshed separately and concatenated into one
    :class:`~.motor_mesh.Section2D` that is deliberately DISCONNECTED at the
    mid-gap radius.  Nothing downstream has to know: ``extrude_section`` builds
    the tets exactly as it does for Stage A, and the two halves are stitched by
    :class:`SlipWeld` at solve time.
    """
    if int(section.n_sectors) < 2:
        raise ValueError("the banded build is a SECTOR model; a full ring has "
                         "no anti-periodic wrap to test the weld against")
    a_sec = math.radians(section.sector_deg)
    r_box = float(r_box_mm if r_box_mm else box_factor * section.r_stator_out_mm)
    h_far = float(h_far if h_far else max(r_box / 6.0, h_solid))
    r_mid = float(section.mid_r_mm)
    t0 = time.perf_counter()

    ring_xy = _uniform_ring_xy(r_mid, int(n_ring), a_sec)
    pitch_mm = 2.0 * math.pi * r_mid / float(n_ring)
    # The ring pitch and the gap element size have to be within a factor of a few
    # of each other, and BOTH bounds are real:
    #   too fine an element size and gmsh subdivides the ring segments, so the
    #     two pieces stop sharing a ring and the weld has nothing to pair;
    #   too coarse and gmsh is asked to fill a region from a boundary whose
    #     points are many times finer than the target size, which it does not do
    #     in reasonable time — measured: n_ring = 672 (0.112 mm of arc) against
    #     h_gap = 0.9 mm did not return in 15 minutes.
    if h_gap < 1.05 * pitch_mm:
        raise ValueError(
            f"h_gap = {h_gap:.3f} mm is not above the ring pitch "
            f"{pitch_mm:.3f} mm — gmsh would subdivide the ring segments and the "
            "two pieces would stop sharing a ring")
    if h_gap > 4.0 * pitch_mm:
        raise ValueError(
            f"h_gap = {h_gap:.3f} mm is more than 4x the ring pitch "
            f"{pitch_mm:.3f} mm.  gmsh will grind for minutes filling the gap "
            "from a boundary that much finer than its target size, and the "
            "elements it does produce next to the ring are slivers.  Lower "
            "n_ring or lower h_gap.")

    names: Dict[str, int] = {"air": 0}
    for r in section.regions:
        names[r.name] = len(names)

    rotor_regs = [r for r in section.regions
                  if r.kind in ("magnet", "shaft") or r.name == "rotor"]
    stator_regs = [r for r in section.regions if r.name == "stator"]
    if not rotor_regs or not stator_regs:
        raise ValueError("the cross-section has no rotor or no stator region")
    for r in rotor_regs:
        rr = np.hypot(*np.asarray(r.polygon.exterior.coords
                                  if hasattr(r.polygon, "exterior")
                                  else r.polygon.geoms[0].exterior.coords).T)
        if float(rr.max()) > r_mid - 1e-6:
            raise ValueError(f"rotor-side region {r.name!r} reaches r = "
                             f"{float(rr.max()):.4f} mm, past the mid-gap ring")

    # --- rotor piece: the half disk out to the mid-gap ring ----------------
    # origin + the WHOLE ring (both cut vertices kept): the polygon closes along
    # the theta = sector radial cut, which is what makes the piece periodic.
    dom_r = np.hstack([np.zeros((2, 1)), ring_xy])
    pr, tr, regr, mr, sr, ringr = _mesh_piece(
        rotor_regs, dom_r, a_sec, r_mid, names, h_gap, h_solid, h_far,
        grade_far, gap_r_lo=section.r_rotor_out_mm - 0.05, gap_r_hi=r_mid + 0.05,
        ring_xy=ring_xy, verbose=verbose)

    # --- stator piece: the annulus from the ring out to the air box --------
    n_arc = max(24, int(math.ceil(a_sec * r_box / h_far)))
    th = a_sec * np.arange(n_arc + 1) / n_arc
    outer = np.vstack([r_box * np.cos(th), r_box * np.sin(th)])
    dom_s = np.hstack([outer, ring_xy[:, ::-1]])
    ps, ts, regs_, ms, ss, rings = _mesh_piece(
        stator_regs, dom_s, a_sec, r_box, names, h_gap, h_solid, h_far,
        grade_far, gap_r_lo=r_mid - 0.05, gap_r_hi=section.r_stator_in_mm + 0.05,
        ring_xy=ring_xy, verbose=verbose)

    # --- concatenate (no node is shared: the section is torn at r_mid) -----
    nr = pr.shape[1]
    p = np.hstack([pr, ps])
    t = np.hstack([tr, ts + nr])
    reg = np.concatenate([regr, regs_])
    masters = np.concatenate([mr, ms + nr])
    slaves = np.concatenate([sr, ss + nr])
    rring = ringr
    sring = rings + nr
    nn = p.shape[1]
    key = ring_split_key(nn, masters, slaves, rring, sring)

    sect = Section2D(p=np.ascontiguousarray(p), t=np.ascontiguousarray(t),
                     tri_region=reg, names=names,
                     masters=masters, slaves=slaves, r_box_mm=r_box,
                     meta=dict(h_gap=h_gap, h_solid=h_solid, h_far=h_far,
                               r_box_mm=r_box, n_sectors=int(section.n_sectors),
                               sector_deg=float(section.sector_deg),
                               rot_key=key, banded=True,
                               n_ring=int(n_ring), r_mid_mm=r_mid,
                               n_rotor_nodes=int(nr),
                               n_tri_rotor=int(tr.shape[1]),
                               n_tri_stator=int(ts.shape[1]),
                               ring_pitch_mm=pitch_mm,
                               mesh_time_s=time.perf_counter() - t0))
    return BandedSection(sect=sect, n_ring=int(n_ring), rring=rring, sring=sring,
                         n_rotor_nodes=int(nr), r_mid_mm=r_mid,
                         sector_rad=a_sec,
                         bc_sign=-1.0 if section.antiperiodic else 1.0,
                         meta=dict(sect.meta))


def merged_section(banded: BandedSection) -> Section2D:
    """The SAME cross-section with the two rings identified — one conformal mesh.

    This exists for one purpose: it is the proof.  At zero rotor shift the weld
    is supposed to be doing nothing more than what a mesh that shared those
    nodes would do for free, so a solve on this section and a welded solve at
    ``m = 0`` must agree to round-off.  Anything less and the weld is coupling
    the gap approximately, which is a torn field with a plausible-looking
    picture.

    Merging cannot change the tets: ``extrude_section`` splits each prism by the
    ORDER of its nodes' keys, and identifying a stator ring node with the rotor
    ring node at the same angle leaves every key comparison in the mesh
    unchanged.
    """
    s = banded.sect
    nn = s.p.shape[1]
    remap = np.arange(nn, dtype=np.int64)
    remap[np.asarray(banded.sring, dtype=np.int64)] = np.asarray(
        banded.rring, dtype=np.int64)
    t = remap[s.t]
    used = np.unique(t)
    back = -np.ones(nn, dtype=np.int64)
    back[used] = np.arange(used.size, dtype=np.int64)
    p = s.p[:, used]
    t = back[t]
    key_old = np.asarray(s.meta["rot_key"], dtype=np.int64)
    # the two pieces both carried the ring's cut node, so after merging the
    # pairing lists it twice; keep one of each
    pr = np.unique(np.stack([back[remap[s.masters]], back[remap[s.slaves]]]).T,
                   axis=0)
    masters, slaves = pr[:, 0], pr[:, 1]
    meta = dict(s.meta)
    meta["rot_key"] = key_old[used]
    meta["merged_ring"] = True
    return Section2D(p=np.ascontiguousarray(p), t=np.ascontiguousarray(t),
                     tri_region=s.tri_region.copy(), names=dict(s.names),
                     masters=masters, slaves=slaves,
                     r_box_mm=s.r_box_mm, meta=meta)


# ==========================================================================
# the signed union-find
# ==========================================================================

class SignedUF:
    """dof_a == s * dof_b, composed.  ``find`` returns (root, sign).

    Iterative rather than recursive (the 2D twin in ``p2_projection`` recurses,
    which is fine at 2D dof counts and would be a stack overflow at 3D ones), and
    on plain Python lists rather than numpy arrays because every access here is
    scalar and numpy scalar indexing is an order of magnitude slower.

    ``union`` RETURNS whether the constraint was consistent.  The 2D twin simply
    drops a union whose two dofs are already related; here a dropped constraint
    would be a torn air gap that still solves, so the caller is told.
    """
    __slots__ = ("par", "sgn")

    def __init__(self, n: int):
        self.par = list(range(n))
        self.sgn = [1] * n

    def find(self, x: int) -> Tuple[int, int]:
        par, sgn = self.par, self.sgn
        root = x
        s = 1
        while par[root] != root:
            s *= sgn[root]
            root = par[root]
        cur, cs = x, s
        while par[cur] != root:            # path compression, carrying the sign
            nxt, ns = par[cur], sgn[cur]
            par[cur] = root
            sgn[cur] = cs
            cur, cs = nxt, cs * ns
        return root, s

    def union(self, a: int, b: int, sign: int) -> bool:
        ra, sa = self.find(a)
        rb, sb = self.find(b)
        if ra == rb:
            return (sa == sign * sb)          # False = CONTRADICTION
        self.par[ra] = rb
        self.sgn[ra] = sign * sa * sb
        return True

    def classes(self) -> Tuple[np.ndarray, np.ndarray]:
        n = len(self.par)
        root = [0] * n
        sg = [0] * n
        find = self.find
        for i in range(n):
            r, s = find(i)
            root[i] = r
            sg[i] = s
        _uniq, inv = np.unique(np.asarray(root, dtype=np.int64),
                               return_inverse=True)
        return inv.astype(np.int64).ravel(), np.asarray(sg, dtype=float)


# ==========================================================================
# the 3D edge weld
# ==========================================================================

def _edge_lookup(mesh):
    """(find, nP): map an unordered node pair to its global edge index."""
    e = np.asarray(mesh.edges, dtype=np.int64)
    nP = int(mesh.p.shape[1])
    lo = np.minimum(e[0], e[1])
    hi = np.maximum(e[0], e[1])
    key = lo * nP + hi
    order = np.argsort(key, kind="stable")
    skey = key[order]

    def find(a, b):
        a = np.asarray(a, dtype=np.int64)
        b = np.asarray(b, dtype=np.int64)
        k = np.minimum(a, b) * nP + np.maximum(a, b)
        pos = np.searchsorted(skey, k)
        bad = (pos >= skey.size)
        pos = np.where(bad, 0, pos)
        idx = order[pos]
        ok = (~bad) & (skey[np.minimum(pos, skey.size - 1)] == k)
        if not np.all(ok):
            raise RuntimeError(
                f"{int((~ok).sum())} of {k.size} interface edges do not exist in "
                "the mesh — the prism split did not produce the diagonals the "
                "weld expects")
        return idx

    return find, nP


class SlipWeld:
    """The composed constraint: anti-periodic cuts + the mid-gap slip, at shift m.

    ``build(m)`` returns ``(P, col_of_dof)`` with ``A = P @ x``, ``P`` carrying
    exactly one +-1 per row.  The reduced system is ``P^T K P x = P^T f``, which
    is the same algebra ``periodic.elimination`` produces for the plain
    anti-periodic case — this is that construction with the slip folded in.
    """

    def __init__(self, basis, banded: BandedSection, tm, section: MotorSection,
                 verify: bool = True):
        mesh = basis.mesh
        if int(basis.N) != int(mesh.edges.shape[1]):
            raise RuntimeError(
                "SlipWeld is for the lowest-order Nedelec element (one dof per "
                f"edge): {basis.N} dofs for {mesh.edges.shape[1]} edges")
        self.N = int(basis.N)
        self.mesh = mesh
        self.banded = banded
        self.bc_sign = int(round(banded.bc_sign))
        self.verify = bool(verify)

        nn = int(tm.meta["n_nodes_2d"])
        nz = len(tm.meta["z_levels_mm"])
        self.nz = nz
        Nr = banded.n_sec_ring
        self.Nring = Nr
        self.n_cell = Nr - 1                      # ring edges in the open wedge

        find, _nP = _edge_lookup(mesh)
        e = np.asarray(mesh.edges, dtype=np.int64)

        rr = np.asarray(banded.rring, dtype=np.int64)
        sr = np.asarray(banded.sring, dtype=np.int64)

        # -- family 1: AXIAL edges (k, l) -> (k, l+1) ----------------------
        k_ax = np.repeat(np.arange(Nr), nz - 1)
        l_ax = np.tile(np.arange(nz - 1), Nr)
        ax_rot_a = l_ax * nn + rr[k_ax]
        ax_rot_b = (l_ax + 1) * nn + rr[k_ax]
        self._ax_rot = find(ax_rot_a, ax_rot_b)
        self._ax_rot_fwd = self._forward(e, self._ax_rot, ax_rot_a)
        self._ax_k, self._ax_l = k_ax, l_ax

        # -- family 2: ANGULAR edges (k, l) -> (k+1, l) --------------------
        e_an = np.repeat(np.arange(self.n_cell), nz)
        l_an = np.tile(np.arange(nz), self.n_cell)
        an_rot_a = l_an * nn + rr[e_an]
        an_rot_b = l_an * nn + rr[e_an + 1]
        self._an_rot = find(an_rot_a, an_rot_b)
        self._an_rot_fwd = self._forward(e, self._an_rot, an_rot_a)
        self._an_e, self._an_l = e_an, l_an

        # -- family 3: the quad DIAGONAL (k, l) -> (k+1, l+1) --------------
        e_dg = np.repeat(np.arange(self.n_cell), nz - 1)
        l_dg = np.tile(np.arange(nz - 1), self.n_cell)
        dg_rot_a = l_dg * nn + rr[e_dg]
        dg_rot_b = (l_dg + 1) * nn + rr[e_dg + 1]
        self._dg_rot = find(dg_rot_a, dg_rot_b)
        self._dg_rot_fwd = self._forward(e, self._dg_rot, dg_rot_a)
        self._dg_e, self._dg_l = e_dg, l_dg

        self._find = find
        self._e = e
        self._nn = nn
        self._rr, self._sr = rr, sr

        # -- the two pieces' own anti-periodic cut pairs -------------------
        self._cut = _blocked_edge_cut_pairs(
            basis, banded.sector_rad, banded.bc_sign,
            block_of_node=(np.arange(mesh.p.shape[1]) % nn)
            >= banded.n_rotor_nodes)

        self.meta = dict(n_ring_nodes=Nr, n_z_levels=nz,
                         n_axial=int(self._ax_rot.size),
                         n_angular=int(self._an_rot.size),
                         n_diagonal=int(self._dg_rot.size),
                         n_cut_pairs=int(self._cut[0].size),
                         n_dofs=self.N)

    @staticmethod
    def _forward(e: np.ndarray, eid: np.ndarray, logical_first: np.ndarray
                 ) -> np.ndarray:
        """True where the stored edge orientation runs from ``logical_first``."""
        return e[0][eid] == logical_first

    # -- the ring map ------------------------------------------------------
    def ring_map(self, m: int):
        """rotor ring node k -> (stator ring node j, wrap sign).

        The open wedge holds ``Nring`` nodes with wrap period ``Nring - 1``: node
        ``Nring - 1`` IS node 0 of the next sector, carrying ``bc_sign``.
        """
        Nr = self.Nring
        per = Nr - 1
        k = np.arange(Nr)
        q, j = np.divmod(k + int(m), per)
        sg = np.power(float(self.bc_sign), q)
        return j.astype(np.int64), sg

    def edge_map(self, m: int):
        """ring CELL e -> (stator cell, wrap sign).  Cells reduce mod Nring - 1.

        Reducing the CELL rather than its two endpoints is what makes the wrap
        exact: the cell that straddles the cut has one endpoint on each side, so
        an endpoint-by-endpoint reduction would give it two different signs.
        """
        per = self.n_cell
        e = np.arange(per)
        q, j = np.divmod(e + int(m), per)
        return j.astype(np.int64), np.power(float(self.bc_sign), q)

    # -- the projection ----------------------------------------------------
    def build(self, m: int, dirichlet_dofs: Optional[np.ndarray] = None):
        from scipy.sparse import coo_matrix

        nn, nz = self._nn, self.nz
        e, find, sr = self._e, self._find, self._sr
        jnode, sgnode = self.ring_map(m)
        jcell, sgcell = self.edge_map(m)

        pairs_a: List[np.ndarray] = []
        pairs_b: List[np.ndarray] = []
        pairs_s: List[np.ndarray] = []

        # axial
        k = self._ax_k
        l = self._ax_l
        sa = l * nn + sr[jnode[k]]
        sb = (l + 1) * nn + sr[jnode[k]]
        sid = find(sa, sb)
        sgn = sgnode[k] * np.where(
            self._ax_rot_fwd == self._forward(e, sid, sa), 1.0, -1.0)
        pairs_a.append(self._ax_rot); pairs_b.append(sid); pairs_s.append(sgn)

        # angular
        ec, l = self._an_e, self._an_l
        sa = l * nn + sr[jcell[ec]]
        sb = l * nn + sr[jcell[ec] + 1]
        sid = find(sa, sb)
        sgn = sgcell[ec] * np.where(
            self._an_rot_fwd == self._forward(e, sid, sa), 1.0, -1.0)
        pairs_a.append(self._an_rot); pairs_b.append(sid); pairs_s.append(sgn)

        # diagonal
        ec, l = self._dg_e, self._dg_l
        sa = l * nn + sr[jcell[ec]]
        sb = (l + 1) * nn + sr[jcell[ec] + 1]
        sid = find(sa, sb)
        sgn = sgcell[ec] * np.where(
            self._dg_rot_fwd == self._forward(e, sid, sa), 1.0, -1.0)
        pairs_a.append(self._dg_rot); pairs_b.append(sid); pairs_s.append(sgn)

        # the two pieces' anti-periodic cuts (m-independent)
        cm, cs, csg = self._cut
        pairs_a.append(cs); pairs_b.append(cm); pairs_s.append(csg)

        A = np.concatenate(pairs_a)
        B = np.concatenate(pairs_b)
        S = np.concatenate(pairs_s)

        uf = SignedUF(self.N)
        bad = 0
        for i in range(A.size):
            if not uf.union(int(A[i]), int(B[i]), int(round(S[i]))):
                bad += 1
        if bad:
            raise RuntimeError(
                f"{bad} of {A.size} weld constraints CONTRADICT the classes they "
                "landed in — the anti-periodic wrap sign and the edge "
                "orientation sign do not compose. A silently dropped constraint "
                "is a torn air gap that still solves.")
        inv, sgn = uf.classes()

        if self.verify:
            err = np.abs(sgn[A] - S * sgn[B])
            if float(err.max()) > 0.0 or not np.array_equal(inv[A], inv[B]):
                raise RuntimeError("the signed union-find did not satisfy every "
                                   "constraint it was given")

        P = coo_matrix((sgn, (np.arange(self.N), inv)),
                       shape=(self.N, int(inv.max()) + 1)).tocsr()
        Dcols = (np.unique(inv[np.asarray(dirichlet_dofs, dtype=np.int64)])
                 if dirichlet_dofs is not None and len(dirichlet_dofs)
                 else np.empty(0, dtype=np.int64))
        return P, inv, Dcols


def _blocked_edge_cut_pairs(basis, sector_rad: float, sign: float,
                            block_of_node: np.ndarray,
                            tol: float = 1e-9, atol: float = 1e-10):
    """``periodic.edge_dof_pairs`` restricted to pair WITHIN each piece.

    The plain version keys the pairing on (r, z) alone.  On a banded section
    that is ambiguous at exactly one radius — the mid-gap ring, where the rotor
    piece and the stator piece both have a node at the same (r, z) on each cut
    plane — and the ambiguity is resolved the wrong way half the time, welding a
    rotor cut edge to a stator one.  Adding the piece id to the sort key removes
    it, and nothing else changes.
    """
    m = basis.mesh
    e = np.asarray(m.edges, dtype=np.int64)
    p = np.asarray(m.p)
    mid = 0.5 * (p[:, e[0]] + p[:, e[1]])
    tan = p[:, e[1]] - p[:, e[0]]
    x, y, z = mid[0], mid[1], mid[2]
    r = np.hypot(x, y)
    ca, sa = math.cos(sector_rad), math.sin(sector_rad)
    scale = float(np.max(r)) if r.size else 1.0
    t = max(tol, 1e-12 * scale)

    on_m0 = (np.abs(p[1]) <= t) & (p[0] > -t)
    on_s0 = (np.abs(p[0] * sa - p[1] * ca) <= t) & ((p[0] * ca + p[1] * sa) > -t)
    blk = np.asarray(block_of_node).astype(np.int64)
    ebl = blk[e[0]]
    if not np.array_equal(ebl, blk[e[1]]):
        # an edge spanning the two pieces would mean they are not torn
        bad = int((ebl != blk[e[1]]).sum())
        raise RuntimeError(f"{bad} edges join the rotor piece to the stator "
                           "piece — the cross-section is not torn at r_mid")
    mm = np.flatnonzero(on_m0[e[0]] & on_m0[e[1]] & (r > t))
    ss = np.flatnonzero(on_s0[e[0]] & on_s0[e[1]] & (r > t))
    if mm.size != ss.size:
        raise RuntimeError(
            f"cut planes hold {mm.size} vs {ss.size} EDGE dofs — the banded "
            "section is not rotationally periodic")
    if mm.size == 0:
        return (np.empty(0, np.int64), np.empty(0, np.int64),
                np.empty(0, np.float64))
    om = np.lexsort((z[mm], r[mm], ebl[mm]))
    os_ = np.lexsort((z[ss], r[ss], ebl[ss]))
    mm, ss = mm[om], ss[os_]
    err = float(np.max(np.abs(np.stack([r[mm] - r[ss], z[mm] - z[ss]]))))
    if not np.array_equal(ebl[mm], ebl[ss]):
        raise RuntimeError("a cut edge was paired across the two pieces")
    a = max(atol, 1e-9 * scale)
    if err > a:
        raise RuntimeError(
            f"periodic EDGE pairing is off by {err:.3e} m (tol {a:.1e}) — the "
            "two cut planes do not carry the same mesh")
    tm_ = tan[:, mm]
    rot = np.vstack([tm_[0] * ca - tm_[1] * sa, tm_[0] * sa + tm_[1] * ca, tm_[2]])
    ts = tan[:, ss]
    num = (rot * ts).sum(axis=0)
    den = np.linalg.norm(rot, axis=0) * np.linalg.norm(ts, axis=0)
    cosang = num / np.maximum(den, 1e-300)
    worst = float(np.max(np.abs(np.abs(cosang) - 1.0)))
    if worst > 1e-7:
        raise RuntimeError(
            f"a paired edge is not the rotation of its master (worst |cos| off "
            f"by {worst:.2e}) — the cut planes share midpoints but not edges")
    signs = float(sign) * np.where(cosang > 0.0, 1.0, -1.0)
    return mm.astype(np.int64), ss.astype(np.int64), signs
