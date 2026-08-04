"""3D tet mesh of one symmetry sector of the REAL machine: 2D wedge x axial layers.

Why a tensor-product extrusion and not a 3D OCC solid
-----------------------------------------------------
The obvious construction — extrude the cross-section into OCC solids, add an end
air cap, fragment, tet-mesh — has three problems this build does not:

1. **The z = L/2 interface.**  The end effect IS the discontinuity at the end of
   the stack, so that plane has to be a mesh plane, exactly, for every stack
   length in the L-sweep.  Here the z levels are chosen, so it is.
2. **Periodicity.**  Rotational periodicity needs the two radial cut planes to
   carry IDENTICAL meshes.  In 3D that is ``gmsh.model.mesh.setPeriodic`` on a
   dozen fragmented faces and a lot of hope.  Here it is 1D periodicity on the
   two cut CURVES of a 2D mesh — the reliable case — and the extrusion copies it
   up every layer for free.
3. **The L-sweep cost.**  One 2D mesh serves every stack length: only the axial
   layer list and the per-element material change.  Meshing stops being the
   bottleneck, which is the difference between a 5-point k_flux(L) curve and a
   1-point claim.

The price is that the far air is discretised as a tensor product, so it cannot
coarsen in z and in (r, theta) independently.  Both directions are graded, and
the element count is reported, so the cost is visible rather than hidden.

Prism -> tet split
------------------
Extruding a triangle gives a prism; skfem has no prism element, so each prism is
cut into 3 tets.  The cut is only valid if neighbouring prisms agree on the
diagonal of every shared quad face.  The rule used is the standard one: on the
quad face over base edge (a, b), the diagonal runs through whichever of a, b has
the smaller GLOBAL KEY.  A total order cannot produce a cyclic (invalid)
configuration, and two prisms sharing a base edge see the same two keys, so the
mesh is conformal by construction.

The key is the 2D node index, EXCEPT that a node on the slave cut line is given
its master's key.  That makes the split rotation-equivariant, so the two cut
planes end up with identical triangulations and the anti-periodic constraint
below is an exact statement about matching basis functions rather than an
approximate one about nearby dofs.

Units: gmsh works in MILLIMETRES here (the project's OCC boolean tolerances are
tuned at that scale, and the air gap is 0.2 mm); the mesh handed to the solver
is in METRES.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .meshes import TaggedTetMesh, _GMSH_LOCK
from .motor_geometry import MM, MotorSection

_TOL_MM = 1e-6


# --------------------------------------------------------------------------
# 2D cross-section wedge
# --------------------------------------------------------------------------

@dataclass
class Section2D:
    """A conformal triangle mesh of the sector cross-section (mm)."""
    p: np.ndarray                    # (2, nnode) mm
    t: np.ndarray                    # (3, ntri)
    tri_region: np.ndarray           # (ntri,) region id
    names: Dict[str, int]            # region name -> id
    masters: np.ndarray              # node ids on theta = 0
    slaves: np.ndarray               # matching node ids on theta = sector
    r_box_mm: float
    meta: dict = field(default_factory=dict)

    @property
    def n_tri(self) -> int:
        return int(self.t.shape[1])

    def region_area_mm2(self, name: str) -> float:
        rid = self.names.get(name)
        if rid is None:
            return 0.0
        t = self.t[:, self.tri_region == rid]
        if t.shape[1] == 0:
            return 0.0
        a = self.p[:, t[0]]
        e1 = self.p[:, t[1]] - a
        e2 = self.p[:, t[2]] - a
        return float(np.abs(e1[0] * e2[1] - e1[1] * e2[0]).sum() / 2.0)


def _shapely_rings(poly) -> List[List[Tuple[float, float]]]:
    """[exterior, *interiors] as de-duplicated open coordinate lists."""
    out = []
    for ring in [poly.exterior] + list(poly.interiors):
        c = list(ring.coords)
        if len(c) > 1 and abs(c[0][0] - c[-1][0]) < 1e-12 and abs(c[0][1] - c[-1][1]) < 1e-12:
            c = c[:-1]
        ded = []
        for q in c:
            if not ded or math.hypot(q[0] - ded[-1][0], q[1] - ded[-1][1]) > 1e-6:
                ded.append((float(q[0]), float(q[1])))
        if len(ded) >= 3:
            out.append(ded)
    return out


def _poly_parts(poly) -> List[object]:
    if poly is None or poly.is_empty:
        return []
    return list(poly.geoms) if hasattr(poly, "geoms") else [poly]


def build_section_mesh_2d(section: MotorSection,
                          r_box_mm: Optional[float] = None,
                          box_factor: float = 4.0,
                          h_gap: float = 0.22,
                          h_solid: float = 0.70,
                          h_far: Optional[float] = None,
                          grade_far: float = 1.0,
                          verbose: bool = False) -> Section2D:
    """Mesh the sector cross-section out to an air disk of radius ``r_box_mm``.

    ``h_gap`` is the element size at the air-gap surfaces, ``h_solid`` inside and
    around the iron/magnets, ``h_far`` in the outer air (default: a sixth of the
    box radius).  Sizes are driven purely by Distance/Threshold fields, so
    growing the box changes only the far field and leaves the near-gap
    discretisation identical — which is what makes the truncation bracket a
    truncation measurement instead of a remesh artefact.
    """
    import gmsh

    r_box = float(r_box_mm if r_box_mm else box_factor * section.r_stator_out_mm)
    h_far = float(h_far if h_far else max(r_box / 6.0, h_solid))
    a_sec = math.radians(section.sector_deg)

    solids = [(r.name, part) for r in section.regions for part in _poly_parts(r.polygon)]

    with _GMSH_LOCK:
        t0 = time.perf_counter()
        try:
            gmsh.initialize([], interruptible=False)
        except TypeError:
            gmsh.initialize()
        try:
            gmsh.option.setNumber("General.Terminal", 1 if verbose else 0)
            gmsh.option.setNumber("Geometry.Tolerance", 1e-6)
            gmsh.option.setNumber("Geometry.ToleranceBoolean", 1e-3)
            gmsh.model.add("static3d_section")
            occ = gmsh.model.occ

            ptcache: Dict[Tuple[float, float], int] = {}

            def _pt(x, y):
                k = (round(x, 7), round(y, 7))
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
                rings = _shapely_rings(poly)
                return occ.addPlaneSurface([_loop(r) for r in rings])

            # ---- the air disk sector (the "box") --------------------------
            # The arc is discretised to the FAR element size, not finely: every
            # polygon segment forces at least one element, so a 1 deg arc would
            # pin ~360 elements onto a boundary where nothing happens.
            n_arc = max(16, int(math.ceil(a_sec * r_box / h_far)))
            arc = [(0.0, 0.0)] if section.n_sectors > 1 else []
            for i in range(n_arc + 1):
                th = a_sec * i / n_arc
                arc.append((r_box * math.cos(th), r_box * math.sin(th)))
            if section.n_sectors > 1:
                arc = arc[:-1] if abs(arc[-1][0]) < 1e-9 and abs(arc[-1][1]) < 1e-9 else arc

            box_s = occ.addPlaneSurface([_loop(arc)])
            tool_tags = [_surface(pl) for _nm, pl in solids]
            occ.fragment([(2, box_s)], [(2, t) for t in tool_tags])
            occ.synchronize()

            # Region membership is decided by the GEOMETRY, not by the fragment's
            # output map — see _classify_triangles.  Measured reason: on the FULL
            # ring OCC handed the first tool (the slotted stator) a map listing
            # all 19 solid faces, so the stator "claimed" every magnet.  The
            # sector build is clean, and the integrity checks below make sure a
            # future one that is not fails loudly instead of quietly.
            solid_curves = _curves_on_solid_boundaries(gmsh, section, r_box)
            # gap curves: everything whose centre of mass sits within the gap band
            r_lo = section.r_rotor_out_mm - 0.05
            r_hi = section.r_stator_in_mm + 0.05
            gap_curves = []
            for c in solid_curves:
                try:
                    cm = gmsh.model.occ.getCenterOfMass(1, c)
                except Exception:
                    continue
                rc = math.hypot(cm[0], cm[1])
                if r_lo <= rc <= r_hi:
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
            f.setNumber(2, "DistMax", max(2.0 * grade_far, 0.30 * r_box))

            f.add("Distance", 3)
            f.setNumbers(3, "CurvesList", [float(c) for c in gap_curves])
            f.setNumber(3, "Sampling", 400)
            f.add("Threshold", 4)
            f.setNumber(4, "InField", 3)
            # NOTE SizeMax here is h_far, NOT h_solid: this field is combined
            # with field 2 through a MIN, so a cap of h_solid would cap the
            # COMBINED size at h_solid and the far air would never coarsen — a
            # silent 4x element count with nothing to show for it.
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

            # ---- rotational periodicity on the two radial cuts ------------
            n_per = 0
            if section.n_sectors > 1:
                n_per = _set_curve_periodicity(gmsh, a_sec, r_box)

            gmsh.model.mesh.generate(2)
            sect = _extract_section(gmsh, section, a_sec, r_box)
            sect.r_box_mm = r_box
            sect.meta = dict(h_gap=h_gap, h_solid=h_solid, h_far=h_far,
                             r_box_mm=r_box, n_periodic_curves=n_per,
                             n_sectors=int(section.n_sectors),
                             sector_deg=float(section.sector_deg),
                             mesh_time_s=time.perf_counter() - t0)
        finally:
            try:
                gmsh.finalize()
            except Exception:
                pass
    return sect


def _set_curve_periodicity(gmsh, a_sec: float, r_box: float) -> int:
    """``setPeriodic`` every curve on the theta = 0 cut onto its partner on the
    theta = a_sec cut, so the two lines get IDENTICAL 1D meshes."""
    ca, sa = math.cos(a_sec), math.sin(a_sec)
    aff = [ca, -sa, 0.0, 0.0,
           sa, ca, 0.0, 0.0,
           0.0, 0.0, 1.0, 0.0,
           0.0, 0.0, 0.0, 1.0]

    masters: List[Tuple[float, int]] = []
    slaves: List[Tuple[float, int]] = []
    for (d, c) in gmsh.model.getEntities(1):
        cm = gmsh.model.occ.getCenterOfMass(1, c)
        x, y = cm[0], cm[1]
        r = math.hypot(x, y)
        if r < 1e-9 or r > r_box * 1.001:
            continue
        if abs(y) < 1e-6 and x > 0:
            masters.append((r, c))
        elif abs(x * sa - y * ca) < 1e-6 and (x * ca + y * sa) > 0:
            slaves.append((r, c))
    masters.sort()
    slaves.sort()
    if len(masters) != len(slaves):
        raise RuntimeError(
            f"sector cut curves do not correspond: {len(masters)} on theta=0 vs "
            f"{len(slaves)} on theta={math.degrees(a_sec):.1f} deg — the "
            "cross-section is not periodic at this angle")
    n = 0
    for (r1, cm_), (r2, cs) in zip(masters, slaves):
        # Deliberately tight.  A 0.8 um mismatch here was NOT round-off: it was
        # the two cut lines genuinely being split at different points, and
        # loosening the bound to accept it only moved the failure downstream to
        # the node pairing, with a worse message.
        if abs(r1 - r2) > 1e-4:
            raise RuntimeError(
                f"sector cut curve radii mismatch: {r1:.6f} vs {r2:.6f} mm")
        gmsh.model.mesh.setPeriodic(1, [cs], [cm_], aff)
        n += 1
    return n


def _curves_on_solid_boundaries(gmsh, section: MotorSection, r_box: float,
                                tol: float = 2e-4) -> List[int]:
    """Curve tags lying on a material interface, found geometrically.

    Same reason as the triangle classification: the fragment's own bookkeeping
    is not trustworthy on this cross-section, and the size field must key on the
    interfaces themselves.  Curves inside a solid (the sector cut, for instance)
    are correctly excluded — refining those buys nothing.
    """
    from shapely.geometry import Point
    from shapely.ops import unary_union

    bnd = unary_union([r.polygon.boundary for r in section.regions])
    out: List[int] = []
    for (d, c) in gmsh.model.getEntities(1):
        try:
            cm = gmsh.model.occ.getCenterOfMass(1, c)
        except Exception:
            continue
        if math.hypot(cm[0], cm[1]) > 0.999 * r_box:
            continue
        if bnd.distance(Point(cm[0], cm[1])) < tol:
            out.append(int(c))
    if not out:
        raise RuntimeError("no material-interface curves found — the fragment "
                           "did not produce the cross-section's interfaces")
    return sorted(set(out))


def _extract_section(gmsh, section: MotorSection,
                     a_sec: float, r_box: float) -> Section2D:
    node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
    node_tags = np.asarray(node_tags, dtype=np.int64)
    xyz = np.asarray(node_coords, dtype=float).reshape(-1, 3).T
    lut = np.zeros(int(node_tags.max()) + 1, dtype=np.int64)
    lut[node_tags] = np.arange(node_tags.size, dtype=np.int64)

    # every triangle in the model, exactly once
    etypes, _, enodes = gmsh.model.mesh.getElements(2)
    t_all = []
    for k, et in enumerate(etypes):
        if int(et) != 2:                          # 2 = 3-node triangle
            continue
        t_all.append(lut[np.asarray(enodes[k], dtype=np.int64)].reshape(-1, 3).T)
    if not t_all:
        raise RuntimeError("gmsh produced no triangles for the cross-section")
    t = np.hstack(t_all)
    if np.unique(np.sort(t, axis=0), axis=1).shape[1] != t.shape[1]:
        raise RuntimeError("duplicated triangles in the cross-section mesh")

    tri_region, names = _classify_triangles(xyz[:2], t, section)

    # Hard integrity gate: every region's MESHED area must equal its polygon's.
    # A boolean that quietly duplicated or dropped a face shows up here and
    # nowhere else until the field is already wrong.
    for r in section.regions:
        got = _tri_area(xyz[:2], t[:, tri_region == names[r.name]])
        if abs(got - r.area_mm2) > 1e-5 * max(r.area_mm2, 1.0):
            raise RuntimeError(
                f"region {r.name!r} meshed at {got:.6f} mm^2 but its polygon is "
                f"{r.area_mm2:.6f} mm^2 — the cross-section boolean lost or "
                "duplicated geometry")

    used = np.unique(t)
    remap = -np.ones(xyz.shape[1], dtype=np.int64)
    remap[used] = np.arange(used.size, dtype=np.int64)
    p = xyz[:2, used]
    t = remap[t]

    # positive orientation
    a = p[:, t[0]]
    e1 = p[:, t[1]] - a
    e2 = p[:, t[2]] - a
    det = e1[0] * e2[1] - e1[1] * e2[0]
    flip = det < 0
    if flip.any():
        t[1, flip], t[2, flip] = t[2, flip].copy(), t[1, flip].copy()

    if a_sec >= 2.0 * math.pi - 1e-9:
        # full ring: there is no cut, and "theta = 0" and "theta = 2 pi" are the
        # SAME half-line, so pairing would couple every node to itself
        masters = slaves = np.empty(0, dtype=np.int64)
    else:
        masters, slaves = _pair_cut_nodes(p, a_sec)
    return Section2D(p=np.ascontiguousarray(p), t=np.ascontiguousarray(t),
                     tri_region=tri_region, names=names,
                     masters=masters, slaves=slaves, r_box_mm=r_box)


def mirror_to_full_ring(sect: Section2D, full: MotorSection) -> Section2D:
    """A FULL-RING cross-section mesh, by rotating the sector mesh around and
    welding the seams.

    The direct 360 deg OCC build does not work on this cross-section: the
    fragment comes back with overlapping duplicate faces (measured: 22 faces
    totalling 4165 mm^2 where the disk is 3406 mm^2, the rotor at 178.7 mm^2
    against its polygon's 92.7).  That is the SAME failure the 2D pipeline
    documents in ``mesher._stitch_full_half`` — "the direct closed-360 OCC build
    double-meshes / mis-classifies (dead field)" — and it takes the same way out.

    The weld needs no search: the sector's own master/slave node pairing already
    says which node of copy k is which node of copy k-1.

    This exists for VALIDATION.  The full ring solved on it carries no
    periodicity constraint at all, so comparing it against the sector solve tests
    the anti-periodic sign, the dof elimination and the claim that the magnet
    pattern really is anti-periodic.  What it cannot test is mesh bias, since the
    two meshes are the same mesh.
    """
    if sect.masters.size == 0:
        raise ValueError("mirroring needs a sector mesh with a paired cut")
    if full.n_sectors != 1:
        raise ValueError("the target section must be the full ring")
    n_sec = int(round(360.0 / max(sect.meta.get("sector_deg", 0.0)
                                  or (360.0 / max(full.n_sectors, 1)), 1e-9))) \
        if sect.meta.get("sector_deg") else 0
    if not n_sec:
        n_sec = int(round(2.0 * math.pi
                          / _sector_angle_of(sect)))
    a = 2.0 * math.pi / n_sec

    n = sect.p.shape[1]
    copies = []
    for k in range(n_sec):
        c, s_ = math.cos(k * a), math.sin(k * a)
        copies.append(np.vstack([c * sect.p[0] - s_ * sect.p[1],
                                 s_ * sect.p[0] + c * sect.p[1]]))
    p = np.hstack(copies)
    t = np.hstack([sect.t + k * n for k in range(n_sec)])

    # Copy k's theta=0 line is copy k-1's theta=a line, all the way round.
    remap = np.arange(n_sec * n, dtype=np.int64)
    for k in range(1, n_sec):
        remap[k * n + sect.masters] = remap[(k - 1) * n + sect.slaves]
    remap[(n_sec - 1) * n + sect.slaves] = remap[sect.masters]
    t = remap[t]
    used = np.unique(t)
    back = -np.ones(n_sec * n, dtype=np.int64)
    back[used] = np.arange(used.size, dtype=np.int64)
    p = p[:, used]
    t = back[t]

    # A ROTATION-EQUIVARIANT split key for the extrusion.
    #
    # ``extrude_section`` picks each prism's three tets by ordering the base
    # triangle's nodes by a key, so two triangles that are rotations of each
    # other are split the same way only if their keys are.  Plain node ids are
    # not: on the ring, copy k's nodes are numbered after copy k-1's, so a
    # triangle and its 180 deg twin get opposite diagonals and the discrete
    # operator stops being rotation-symmetric even though the mesh is.  Measured
    # on the coarse validation ring, that alone breaks the anti-periodicity of
    # the solved field by 1.6 % with isotropic iron and 5.7 % with laminated
    # iron (an anisotropic mu weights the two diagonals differently) — i.e. the
    # sharpest test in this suite was measuring its own mesh.
    #
    # Giving every copy of a node the SAME key removes it, exactly as the
    # sector's own master/slave trick does inside a wedge.
    base_key = np.arange(n, dtype=np.int64)
    if sect.slaves.size:
        base_key[sect.slaves] = base_key[sect.masters]
    rot_key = np.tile(base_key, n_sec)[used]

    tri_region, names = _classify_triangles(p, t, full)
    a = p[:, t[0]]
    e1 = p[:, t[1]] - a
    e2 = p[:, t[2]] - a
    flip = (e1[0] * e2[1] - e1[1] * e2[0]) < 0
    if flip.any():
        t[1, flip], t[2, flip] = t[2, flip].copy(), t[1, flip].copy()
    return Section2D(p=np.ascontiguousarray(p), t=np.ascontiguousarray(t),
                     tri_region=tri_region, names=names,
                     masters=np.empty(0, dtype=np.int64),
                     slaves=np.empty(0, dtype=np.int64),
                     r_box_mm=sect.r_box_mm,
                     meta=dict(sect.meta, n_sectors=1,
                               mirrored_from_sector=n_sec,
                               rot_key=rot_key))


def _tri_area(p2: np.ndarray, t: np.ndarray) -> float:
    if t.shape[1] == 0:
        return 0.0
    a = p2[:, t[0]]
    e1 = p2[:, t[1]] - a
    e2 = p2[:, t[2]] - a
    return float(np.abs(e1[0] * e2[1] - e1[1] * e2[0]).sum() / 2.0)


def _classify_triangles(p2: np.ndarray, t: np.ndarray, section: MotorSection
                        ) -> Tuple[np.ndarray, Dict[str, int]]:
    """Region id per triangle, from its centroid, with 'air' as region 0.

    A triangle straddling two materials would make this ambiguous, which is
    exactly why the mesh is built conformal to every polygon boundary; if the
    total area assigned to a region ever disagrees with the polygon's area the
    ambiguity is real and the test suite catches it.
    """
    import shapely
    from shapely import STRtree
    from shapely.geometry import Point

    names: Dict[str, int] = {"air": 0}
    polys, ids = [], []
    for r in section.regions:
        rid = len(names)
        names[r.name] = rid
        for part in _poly_parts(r.polygon):
            polys.append(part)
            ids.append(rid)

    c = p2[:, t].mean(axis=1)                     # (2, ntri)
    reg = np.zeros(t.shape[1], dtype=np.int64)
    pts = shapely.points(c[0], c[1])
    tree = STRtree(polys)
    # NOTE "within", not "contains": shapely's STRtree evaluates
    # predicate(input_geometry, tree_geometry), so the point must be WITHIN the
    # indexed polygon.  Getting this backwards returns zero hits and puts the
    # whole machine in the air region.
    hits = tree.query(pts, predicate="within")    # (2, nhit): [pt_idx, poly_idx]
    if hits.size:
        reg[hits[0]] = np.asarray(ids, dtype=np.int64)[hits[1]]
    return reg, names


def _pair_cut_nodes(p: np.ndarray, a_sec: float, tol: float = 1e-5
                    ) -> Tuple[np.ndarray, np.ndarray]:
    """Node ids on theta=0 paired with node ids on theta=a_sec, by radius.

    ``setPeriodic`` guarantees the radii match to round-off; anything worse is a
    silent loss of the symmetry the whole sector model rests on, so it raises.
    """
    x, y = p[0], p[1]
    r = np.hypot(x, y)
    ca, sa = math.cos(a_sec), math.sin(a_sec)
    m = np.flatnonzero((np.abs(y) < tol) & (x > tol))
    s = np.flatnonzero((np.abs(x * sa - y * ca) < tol) & ((x * ca + y * sa) > tol))
    if m.size == 0 and s.size == 0:
        return m, s
    m = m[np.argsort(r[m])]
    s = s[np.argsort(r[s])]
    if m.size != s.size:
        raise RuntimeError(f"cut lines carry {m.size} vs {s.size} nodes — "
                           "periodic meshing did not take")
    dr = float(np.max(np.abs(r[m] - r[s]))) if m.size else 0.0
    if dr > 1e-6:
        raise RuntimeError(f"cut node radii differ by {dr:.3e} mm — "
                           "periodic meshing did not take")
    return m, s


# --------------------------------------------------------------------------
# axial layers
# --------------------------------------------------------------------------

def axial_levels(stack_half_mm: float, z_box_mm: float,
                 n_stack: int = 8, n_cap: int = 12,
                 end_bias: float = 3.0,
                 h_ew_mm: Optional[float] = None,
                 n_ew: int = 4) -> np.ndarray:
    """z levels from 0 (mirror plane) to ``z_box_mm``, with a node EXACTLY on
    ``stack_half_mm``.

    Inside the stack the spacing tightens toward the end face (that is where the
    axial gradient lives); outside it grows geometrically to the truncation
    radius (that is where nothing happens and dofs are wasted).

    ``h_ew_mm`` adds a resolved END-WINDING BAND immediately above the stack,
    from ``stack_half_mm`` to ``stack_half_mm + h_ew_mm``, in ``n_ew`` layers,
    with a node exactly on the far edge.  Under load that band is where the
    tangential end-turn current lives (``winding3d``); leaving it to the
    geometric cap would put the entire end winding inside one or two elements
    whose height is comparable to the whole bundle, and the end-winding leakage
    the 3D model exists to see would be a discretisation of nothing.  Omitted
    (``None``) the levels are exactly the Stage A ones, bit for bit.
    """
    s = np.linspace(0.0, 1.0, int(n_stack) + 1)
    inside = stack_half_mm * (1.0 - (1.0 - s) ** end_bias)
    inside[-1] = stack_half_mm
    if h_ew_mm and float(h_ew_mm) > 0.0 and int(n_ew) > 0:
        top = float(stack_half_mm) + float(h_ew_mm)
        if top < float(z_box_mm):
            ew = stack_half_mm + float(h_ew_mm) * (
                np.arange(1, int(n_ew) + 1) / float(int(n_ew)))
            ew[-1] = top
            inside = np.concatenate([inside, ew])
    span = float(z_box_mm) - float(inside[-1])
    if span <= 0:
        return np.unique(np.round(inside, 12))
    # geometric growth: first cap layer ~ the last stack layer
    h0 = max(inside[-1] - inside[-2], 1e-4)
    n = int(n_cap)
    # solve h0 * (q^n - 1)/(q - 1) = span for q by bisection
    lo, hi = 1.0000001, 4.0
    for _ in range(80):
        q = 0.5 * (lo + hi)
        tot = h0 * n if abs(q - 1) < 1e-9 else h0 * (q ** n - 1.0) / (q - 1.0)
        if tot < span:
            lo = q
        else:
            hi = q
    q = 0.5 * (lo + hi)
    outside, z, h = [], float(inside[-1]), h0
    for _ in range(n):
        h *= q
        z += h
        outside.append(z)
    outside[-1] = float(z_box_mm)
    return np.unique(np.round(np.concatenate([inside, outside]), 12))


# --------------------------------------------------------------------------
# extrusion
# --------------------------------------------------------------------------

def extrude_section(sect: Section2D, z_levels_mm: Sequence[float],
                    stack_half_mm: float,
                    section: MotorSection) -> TaggedTetMesh:
    """Tensor-product extrude the cross-section into a tet mesh (METRES).

    Elements above ``stack_half_mm`` are AIR whatever their cross-section region
    is — that single line is the whole end-cap construction, and it is exact
    because ``stack_half_mm`` is a mesh level.
    """
    from skfem import MeshTet

    z = np.asarray(z_levels_mm, dtype=float)
    if z[0] != 0.0:
        raise ValueError("z levels must start at the z=0 mirror plane")
    nz = z.size
    p2, t2 = sect.p, sect.t
    nn, ntri = p2.shape[1], t2.shape[1]

    # nodes: layer-major
    P = np.empty((3, nn * nz))
    P[0] = np.tile(p2[0], nz)
    P[1] = np.tile(p2[1], nz)
    P[2] = np.repeat(z, nn)

    # rotation-equivariant split key: a slave cut node borrows its master's key
    # (on a mirrored FULL RING the same job is done by the key the mirror left
    # behind — every copy of a node shares one key, see mirror_to_full_ring)
    key = np.arange(nn, dtype=np.int64)
    _rk = sect.meta.get("rot_key")
    if _rk is not None and len(_rk) == nn:
        key = np.asarray(_rk, dtype=np.int64)
    elif sect.slaves.size:
        key[sect.slaves] = key[sect.masters]

    # order each base triangle by ascending key -> (a, b, c) with ka < kb < kc
    tk = key[t2]                                   # (3, ntri)
    order = np.argsort(tk, axis=0)
    base = np.take_along_axis(t2, order, axis=0)   # (3, ntri)

    a, b, c = base[0], base[1], base[2]
    tets = np.empty((4, 3 * ntri * (nz - 1)), dtype=np.int64)
    reg = np.empty(3 * ntri * (nz - 1), dtype=np.int64)

    air_id = sect.names["air"]
    off = 0
    for L in range(nz - 1):
        lo, hi = L * nn, (L + 1) * nn
        A, B, C = a + lo, b + lo, c + lo
        A2, B2, C2 = a + hi, b + hi, c + hi
        blk = np.empty((4, 3 * ntri), dtype=np.int64)
        blk[:, 0::3] = np.vstack([A, B, C, C2])
        blk[:, 1::3] = np.vstack([A, B, C2, B2])
        blk[:, 2::3] = np.vstack([A, B2, C2, A2])
        tets[:, off:off + 3 * ntri] = blk
        zc = 0.5 * (z[L] + z[L + 1])
        r = sect.tri_region if zc <= stack_half_mm + 1e-9 else np.full(ntri, air_id)
        reg[off:off + 3 * ntri] = np.repeat(r, 3)
        off += 3 * ntri

    P = P * MM                                     # mm -> m
    # consistent positive orientation
    q = P[:, tets]
    e1 = q[:, 1] - q[:, 0]
    e2 = q[:, 2] - q[:, 0]
    e3 = q[:, 3] - q[:, 0]
    det = (e1[0] * (e2[1] * e3[2] - e2[2] * e3[1])
           - e1[1] * (e2[0] * e3[2] - e2[2] * e3[0])
           + e1[2] * (e2[0] * e3[1] - e2[1] * e3[0]))
    if np.any(np.abs(det) < 1e-20):
        raise RuntimeError("degenerate tet produced by the prism split")
    flip = det < 0
    if flip.any():
        t1 = tets[1, flip].copy()
        tets[1, flip] = tets[2, flip]
        tets[2, flip] = t1

    mesh = MeshTet(np.ascontiguousarray(P), np.ascontiguousarray(tets))
    tm = TaggedTetMesh(mesh=mesh, cell_region=reg, names=dict(sect.names))
    tm.meta = dict(kind="motor_sector", n_layers=nz - 1,
                   z_levels_mm=[float(v) for v in z],
                   stack_half_mm=float(stack_half_mm),
                   r_box_mm=sect.r_box_mm, z_box_mm=float(z[-1]),
                   n_tri_2d=ntri, n_nodes_2d=nn,
                   sector_deg=section.sector_deg,
                   antiperiodic=section.antiperiodic,
                   section_meta=dict(sect.meta))
    return tm


def build_motor_mesh(section: MotorSection,
                     stack_mm: Optional[float] = None,
                     box_factor: float = 4.0,
                     h_gap: float = 0.22,
                     h_solid: float = 0.70,
                     n_stack: int = 8,
                     n_cap: int = 12,
                     sect: Optional[Section2D] = None,
                     h_ew_mm: Optional[float] = None,
                     n_ew: int = 4,
                     verbose: bool = False) -> Tuple[TaggedTetMesh, Section2D]:
    """One call: cross-section (or a cached one) + axial levels + extrusion.

    ``h_ew_mm`` / ``n_ew`` resolve an END-WINDING BAND immediately above the
    stack — see :func:`axial_levels`.  Omitted, the levels are Stage A's."""
    if sect is None:
        sect = build_section_mesh_2d(section, box_factor=box_factor,
                                     h_gap=h_gap, h_solid=h_solid,
                                     verbose=verbose)
    L = float(stack_mm if stack_mm else section.stack_mm)
    z_box = sect.r_box_mm
    zl = axial_levels(0.5 * L, z_box, n_stack=n_stack, n_cap=n_cap,
                      h_ew_mm=h_ew_mm, n_ew=n_ew)
    tm = extrude_section(sect, zl, 0.5 * L, section)
    tm.meta["h_ew_mm"] = None if h_ew_mm is None else float(h_ew_mm)
    tm.meta["n_ew"] = int(n_ew) if h_ew_mm else 0
    return tm, sect
