"""gmsh → skfem tetrahedral meshes for the 3D magnetostatic spike.

Every builder here follows the SAME three-step shape, because Stage A (the
motor extrusion) will be a fourth builder of exactly this shape:

  1. build the solid regions with the OCC kernel,
  2. ``fragment`` them into the surrounding air box so the mesh is CONFORMAL
     across every material interface (no hanging nodes, no duplicated nodes on
     the magnet surface — the weak form integrates ``M·∇v`` over the magnet
     volume and that only telescopes into the right surface charge if the two
     sides share nodes),
  3. drive the element size from a Distance→Threshold field anchored on the
     magnet surfaces, NOT from the global MeshSizeMax.

Step 3 is what makes the truncation study in ``benchmarks.py`` mean anything:
growing the air box then changes only the far field, and leaves the mesh around
the magnet essentially untouched, so the difference between two solves is
truncation error and not a remesh artefact.

Units are METERS throughout this package.  The 2D pipeline works in mm; the
conversion belongs at the Stage-A boundary, not scattered through the physics.

Nothing here imports from — or touches — the 2D mesher.  gmsh is process-global
and not thread safe, so every build serialises on one module lock, the same
discipline ``simulation/mesher.py`` uses.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

_GMSH_LOCK = threading.RLock()


@dataclass
class TaggedTetMesh:
    """A ``MeshTet`` plus the per-element region tag that came out of gmsh.

    ``regions`` maps a human name to the element indices carrying that tag, so
    the solver never has to know a gmsh physical-group number.
    """
    mesh: object                      # skfem.MeshTet
    cell_region: np.ndarray           # (nelem,) int region id
    names: Dict[str, int]             # region name -> id
    meta: dict = field(default_factory=dict)

    def elements(self, name: str) -> np.ndarray:
        """Element indices of one named region (empty array if absent)."""
        rid = self.names.get(name)
        if rid is None:
            return np.empty(0, dtype=np.int64)
        return np.flatnonzero(self.cell_region == rid)

    @property
    def n_elements(self) -> int:
        return int(self.mesh.t.shape[1])

    @property
    def n_vertices(self) -> int:
        return int(self.mesh.p.shape[1])

    def region_volume(self, name: str) -> float:
        """Exact (affine) volume of a named region — a cheap mesh sanity check."""
        p = np.asarray(self.mesh.p)
        t = np.asarray(self.mesh.t)[:, self.elements(name)]
        if t.shape[1] == 0:
            return 0.0
        a = p[:, t[0]]
        e1 = p[:, t[1]] - a
        e2 = p[:, t[2]] - a
        e3 = p[:, t[3]] - a
        det = (e1[0] * (e2[1] * e3[2] - e2[2] * e3[1])
               - e1[1] * (e2[0] * e3[2] - e2[2] * e3[0])
               + e1[2] * (e2[0] * e3[1] - e2[1] * e3[0]))
        return float(np.abs(det).sum() / 6.0)


# --------------------------------------------------------------------------
# gmsh plumbing shared by every builder
# --------------------------------------------------------------------------

def _gmsh_start(name: str, verbose: bool = False):
    import gmsh
    try:
        gmsh.initialize([], interruptible=False)
    except TypeError:                                  # older binding
        gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 1 if verbose else 0)
    gmsh.model.add(name)
    return gmsh


def _size_field(gmsh, surfaces: Sequence[int], h_fine: float, h_coarse: float,
                dist_min: float, dist_max: float) -> None:
    """Element size = h_fine within dist_min of ``surfaces``, ramping to
    h_coarse by dist_max.  The point-, curvature- and boundary-driven sizing is
    switched OFF so this field is the ONLY thing setting the size — that is what
    keeps the near-magnet mesh reproducible when the outer box moves."""
    f = gmsh.model.mesh.field
    f.add("Distance", 1)
    f.setNumbers(1, "SurfacesList", list(surfaces))
    f.setNumber(1, "Sampling", 60)
    f.add("Threshold", 2)
    f.setNumber(2, "InField", 1)
    f.setNumber(2, "SizeMin", h_fine)
    f.setNumber(2, "SizeMax", h_coarse)
    f.setNumber(2, "DistMin", dist_min)
    f.setNumber(2, "DistMax", dist_max)
    f.setAsBackgroundMesh(2)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
    gmsh.option.setNumber("Mesh.MeshSizeMin", h_fine * 0.25)
    gmsh.option.setNumber("Mesh.MeshSizeMax", h_coarse)


def _extract(gmsh, vol_groups: List[Tuple[str, List[int]]], optimize: bool
             ) -> TaggedTetMesh:
    """Pull the generated 3D mesh out of gmsh into a skfem ``MeshTet``.

    ``vol_groups`` is an ordered list of (name, [occ volume tags]); region ids
    are the positions in that list.  Nodes are taken globally ONCE (so the mesh
    stays conformal) and re-indexed; tets are collected per volume so each one
    carries its region.
    """
    from skfem import MeshTet

    gmsh.model.mesh.generate(3)
    if optimize:
        try:
            gmsh.model.mesh.optimize("Netgen")
        except Exception:                              # optional dependency
            pass

    node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
    node_tags = np.asarray(node_tags, dtype=np.int64)
    coords = np.asarray(node_coords, dtype=float).reshape(-1, 3).T   # (3, N)

    # gmsh node tags are 1-based but NOT guaranteed contiguous
    lut = np.zeros(int(node_tags.max()) + 1, dtype=np.int64)
    lut[node_tags] = np.arange(node_tags.size, dtype=np.int64)

    t_all: List[np.ndarray] = []
    reg_all: List[np.ndarray] = []
    names: Dict[str, int] = {}
    for rid, (nm, vols) in enumerate(vol_groups):
        names[nm] = rid
        for v in vols:
            etypes, _, enodes = gmsh.model.mesh.getElements(3, int(v))
            for k, et in enumerate(etypes):
                if int(et) != 4:                       # 4 = 4-node tetrahedron
                    continue
                conn = lut[np.asarray(enodes[k], dtype=np.int64)].reshape(-1, 4).T
                t_all.append(conn)
                reg_all.append(np.full(conn.shape[1], rid, dtype=np.int64))

    if not t_all:
        raise RuntimeError("gmsh produced no tetrahedra")
    t = np.hstack(t_all)
    cell_region = np.hstack(reg_all)

    # Drop nodes no tet references (gmsh keeps the surface-only nodes around).
    used = np.unique(t)
    remap = -np.ones(coords.shape[1], dtype=np.int64)
    remap[used] = np.arange(used.size, dtype=np.int64)
    p = coords[:, used]
    t = remap[t]

    # Positive-orientation tets.  skfem integrates with |det| so this is not
    # strictly required, but a consistently oriented mesh means a sign error in
    # any later flux integral is a real error and not an orientation artefact.
    a = p[:, t[0]]
    e1 = p[:, t[1]] - a
    e2 = p[:, t[2]] - a
    e3 = p[:, t[3]] - a
    det = (e1[0] * (e2[1] * e3[2] - e2[2] * e3[1])
           - e1[1] * (e2[0] * e3[2] - e2[2] * e3[0])
           + e1[2] * (e2[0] * e3[1] - e2[1] * e3[0]))
    flip = det < 0.0
    if flip.any():
        n1, n2 = t[1, flip].copy(), t[2, flip].copy()
        t[1, flip], t[2, flip] = n2, n1

    mesh = MeshTet(np.ascontiguousarray(p), np.ascontiguousarray(t.astype(np.int64)))
    return TaggedTetMesh(mesh=mesh, cell_region=cell_region, names=names)


def _split_fragment(out_map, n_objects: int) -> Tuple[List[int], List[List[int]]]:
    """From ``occ.fragment`` output: (air volumes, [tool volumes per tool]).

    ``out_map[i]`` are the pieces input ``i`` became.  The tools' pieces also
    appear in the object's list (they are the same physical volumes after the
    fragment), so the air is the object's pieces MINUS every tool piece."""
    obj_vols: List[int] = []
    for i in range(n_objects):
        obj_vols += [tag for (dim, tag) in out_map[i] if dim == 3]
    tools: List[List[int]] = []
    for j in range(n_objects, len(out_map)):
        tools.append([tag for (dim, tag) in out_map[j] if dim == 3])
    claimed = {tag for tl in tools for tag in tl}
    air = [v for v in obj_vols if v not in claimed]
    return air, tools


# --------------------------------------------------------------------------
# benchmark geometries
# --------------------------------------------------------------------------

def sphere_in_box(radius: float = 0.01,
                  box_factor: float = 6.0,
                  h_magnet: float = 0.0025,
                  h_far: Optional[float] = None,
                  grade_dist: Optional[float] = None,
                  optimize: bool = True,
                  verbose: bool = False) -> TaggedTetMesh:
    """Sphere of ``radius`` centred at the origin inside a cube of half-width
    ``box_factor * radius``.  Regions: ``air``, ``magnet``.

    ``h_magnet`` is the element size on and inside the sphere; it ramps to
    ``h_far`` (default: a quarter of the box half-width) over ``grade_dist``
    (default: two radii).  Those defaults are deliberate — they hold the
    near-field discretisation fixed while ``box_factor`` sweeps.
    """
    L = box_factor * radius
    h_far = h_far if h_far is not None else max(L / 4.0, h_magnet)
    grade = grade_dist if grade_dist is not None else 2.0 * radius

    with _GMSH_LOCK:
        gmsh = _gmsh_start("sphere_in_box", verbose)
        try:
            occ = gmsh.model.occ
            sph = occ.addSphere(0.0, 0.0, 0.0, radius)
            box = occ.addBox(-L, -L, -L, 2 * L, 2 * L, 2 * L)
            _, out_map = occ.fragment([(3, box)], [(3, sph)])
            occ.synchronize()
            air, tools = _split_fragment(out_map, 1)
            mag = tools[0]
            surf = [tag for (d, tag) in gmsh.model.getBoundary(
                [(3, v) for v in mag], oriented=False, combined=True) if d == 2]
            _size_field(gmsh, surf, h_magnet, h_far, grade, grade + 3.0 * radius)
            tm = _extract(gmsh, [("air", air), ("magnet", mag)], optimize)
        finally:
            try:
                gmsh.finalize()
            except Exception:
                pass
    tm.meta = dict(kind="sphere_in_box", radius=radius, box_half=L,
                   box_factor=box_factor, h_magnet=h_magnet, h_far=h_far)
    return tm


def cylinder_in_box(radius: float = 0.005,
                    length: float = 0.02,
                    box_factor: float = 6.0,
                    h_magnet: float = 0.00125,
                    h_far: Optional[float] = None,
                    grade_dist: Optional[float] = None,
                    optimize: bool = True,
                    verbose: bool = False) -> TaggedTetMesh:
    """Finite cylinder (axis = z, centred on the origin) inside a cube of
    half-width ``box_factor * max(radius, length/2)``.  Regions: ``air``,
    ``magnet``.

    This is the direct stand-in for the motor's axial end effect: the flat end
    faces are where the flux spills out, and the on-axis field there is known in
    closed form (see ``exact.cylinder_axis_Bz``).
    """
    scale = max(radius, 0.5 * length)
    L = box_factor * scale
    h_far = h_far if h_far is not None else max(L / 4.0, h_magnet)
    grade = grade_dist if grade_dist is not None else 2.0 * scale

    with _GMSH_LOCK:
        gmsh = _gmsh_start("cylinder_in_box", verbose)
        try:
            occ = gmsh.model.occ
            cyl = occ.addCylinder(0.0, 0.0, -0.5 * length,
                                  0.0, 0.0, length, radius)
            box = occ.addBox(-L, -L, -L, 2 * L, 2 * L, 2 * L)
            _, out_map = occ.fragment([(3, box)], [(3, cyl)])
            occ.synchronize()
            air, tools = _split_fragment(out_map, 1)
            mag = tools[0]
            surf = [tag for (d, tag) in gmsh.model.getBoundary(
                [(3, v) for v in mag], oriented=False, combined=True) if d == 2]
            _size_field(gmsh, surf, h_magnet, h_far, grade, grade + 3.0 * scale)
            tm = _extract(gmsh, [("air", air), ("magnet", mag)], optimize)
        finally:
            try:
                gmsh.finalize()
            except Exception:
                pass
    tm.meta = dict(kind="cylinder_in_box", radius=radius, length=length,
                   box_half=L, box_factor=box_factor, h_magnet=h_magnet,
                   h_far=h_far)
    return tm


def cylinder_with_iron_ring(radius: float = 0.005,
                            length: float = 0.02,
                            ring_inner: float = 0.0058,
                            ring_outer: float = 0.0064,
                            ring_length: float = 0.014,
                            box_factor: float = 3.0,
                            h_magnet: float = 0.0022,
                            h_far: Optional[float] = None,
                            optimize: bool = True,
                            verbose: bool = False) -> TaggedTetMesh:
    """Axially magnetised cylinder inside a coaxial iron ring, in an air box.
    Regions: ``air``, ``magnet``, ``iron``.

    The default ring is deliberately UNDERSIZED — 0.6 mm wall, hugging the
    magnet — so the return flux drives it to ~1.9 T and mu_r collapses from
    ~7000 to ~100.  The nonlinear smoke test needs the B-H curve to actually
    bite; a comfortable ring sits on its initial slope and would pass the same
    test with the nonlinearity switched off.
    """
    scale = max(ring_outer, 0.5 * length)
    L = box_factor * scale
    h_far = h_far if h_far is not None else max(L / 4.0, h_magnet)

    with _GMSH_LOCK:
        gmsh = _gmsh_start("cylinder_iron_ring", verbose)
        try:
            occ = gmsh.model.occ
            cyl = occ.addCylinder(0.0, 0.0, -0.5 * length,
                                  0.0, 0.0, length, radius)
            ro = occ.addCylinder(0.0, 0.0, -0.5 * ring_length,
                                 0.0, 0.0, ring_length, ring_outer)
            ri = occ.addCylinder(0.0, 0.0, -0.5 * ring_length,
                                 0.0, 0.0, ring_length, ring_inner)
            ring, _ = occ.cut([(3, ro)], [(3, ri)])
            ring_tags = [tag for (d, tag) in ring if d == 3]
            box = occ.addBox(-L, -L, -L, 2 * L, 2 * L, 2 * L)
            _, out_map = occ.fragment(
                [(3, box)], [(3, cyl)] + [(3, r) for r in ring_tags])
            occ.synchronize()
            air, tools = _split_fragment(out_map, 1)
            mag = tools[0]
            iron: List[int] = []
            for tl in tools[1:]:
                iron += tl
            surf = [tag for (d, tag) in gmsh.model.getBoundary(
                [(3, v) for v in mag + iron], oriented=False, combined=True)
                if d == 2]
            _size_field(gmsh, surf, h_magnet, h_far,
                        1.5 * scale, 1.5 * scale + 3.0 * scale)
            tm = _extract(gmsh, [("air", air), ("magnet", mag), ("iron", iron)],
                          optimize)
        finally:
            try:
                gmsh.finalize()
            except Exception:
                pass
    tm.meta = dict(kind="cylinder_with_iron_ring", radius=radius, length=length,
                   ring_inner=ring_inner, ring_outer=ring_outer,
                   ring_length=ring_length, box_half=L, h_magnet=h_magnet)
    return tm
