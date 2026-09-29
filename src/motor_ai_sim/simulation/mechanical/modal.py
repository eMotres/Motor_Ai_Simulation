"""2-D in-plane modal analysis of the rotor and stator cross-sections.

Written 2026-09-05 for the user's request: "нам нужно сделать ещё модальный
анализ, чтобы понять все частоты — это очень важно для 20000 rpm".  At 20 000
rpm the rotor turns at 333 Hz, its magnetic force fundamental is 3.3 kHz, the
slot-passing order is 4 kHz and the PWM carrier is 24 kHz — the question this
module answers is whether any structural mode of the iron sits on top of one of
those.

WHAT IS SOLVED
--------------
The undamped generalised eigenproblem  K phi = omega^2 M phi  on the SAME
plane-stress finite-element model ``rotor_stress`` builds, for two bodies:

  * ``rotor``  — the rotor solids (core + magnets + sleeve + shaft tube),
  * ``stator`` — the stator core with its teeth (the ``stator`` polygon; the
    slots are holes in it and are NOT meshed).

These are PER UNIT LENGTH 2-D modes: no axial stiffness, no end effects, no
axial half-waves.  That is the right idealisation for the ring / ovalisation
modes of a long stack, which are the ones that whine, and it is the WRONG model
for anything that bends along the shaft — the shaft's own bending criticals are
``rotordynamics.py``, and they are the numbers that decide 20 000 rpm.

ASSUMPTIONS — read these before trusting a frequency
----------------------------------------------------
* BONDED interfaces.  A linear eigenproblem has no load and therefore no
  contact state: a Separation pair would be open in one half-cycle and closed in
  the other, which is not an eigenproblem at all.  Every interface here is
  welded, so the rotor is stiffer than the real one and these frequencies are an
  UPPER bound.  ``rotor_stress`` is where the contact question is answered.
* NO PRESTRESS.  The centrifugal stress state at rated speed stiffens the ring
  (and the spin softens it); neither is included — there is no geometric
  stiffness matrix in this build.  On a rotor whose OD stress is a few hundred
  MPa the shift is a few percent, in the stiffening direction.  Said out loud
  rather than quietly approximated.
* FREE-FREE for the rotor: nothing holds a rotor cross-section in its own
  plane, so the three rigid-body modes are found, checked and dropped.  For the
  stator the caller picks ``free`` (the bare core, as it would ring on a bench)
  or ``pinned`` — the outer surface held RADIALLY by a housing, tangential
  motion left free.  The pinned constraint is exact (the radial dof of every
  outer-boundary node is eliminated), not a penalty spring.
* The winding COPPER adds mass, not stiffness.  It is applied as
  NON-STRUCTURAL MASS lumped onto the slot-boundary nodes, weighted by the
  boundary length each node owns — the classical NSM treatment.  Smearing it
  into soft elements inside the slot would have invented a stiffness and filled
  the first twelve modes with slot-jelly artefacts; adding it to the tooth
  density would have put copper where there is iron.  The copper area is the
  UNION of the coil polygons the magnetic FEM meshes (``coil_copper_area_total_m2``),
  so it is the same copper the rest of the project weighs.
* Lamination is ignored in-plane (the standard 2-D assumption), the material is
  linear elastic, and there is no damping — these are undamped natural
  frequencies, not resonant amplitudes.

Units: geometry in mm on the way in, metres inside, hertz on the way out.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from motor_ai_sim.simulation.mechanical.contact import (PART_MAGNET, PART_NAMES,
                                                        PART_ROTOR, PART_SHAFT,
                                                        PART_SLEEVE)
from motor_ai_sim.simulation.mechanical.rotor_stress import (
    MissingMechanicalProperty, PartMech, build_rotor_mesh, part_C, part_mech,
    resolve_part_materials)

_log = logging.getLogger(__name__)

#: The stator gets its own part id so a modal field payload can be drawn by the
#: same map component as a stress one without the two tag spaces colliding.
PART_STATOR = 4
MODAL_PART_NAMES: Dict[int, str] = {**PART_NAMES, PART_STATOR: "stator"}

BODIES = ("rotor", "stator")
SUPPORTS = ("free", "pinned")


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _bases(mesh, order: int):
    """(stiffness basis, mass basis) — same dof numbering, different quadrature.

    The dof numbering follows the element and the mesh, not the quadrature, so
    the two matrices are assembled on the same unknowns.  The mass integrand of
    a P2 vector field is degree 4 and the stiffness integrand degree 2; using
    ``rotor_stress``'s intorder=3 basis for BOTH would under-integrate M, which
    on an eigenproblem shows up as frequencies that drift with mesh refinement
    for no physical reason.
    """
    from skfem import Basis, ElementTriP1, ElementTriP2, ElementVector

    elem = ElementVector(ElementTriP2() if order == 2 else ElementTriP1())
    kb = Basis(mesh, elem, intorder=3 if order == 2 else 2)
    mb = Basis(mesh, elem, intorder=4 if order == 2 else 2)
    return kb, mb


def assemble_K_M(mesh, C_elem: np.ndarray, rho_elem: np.ndarray,
                 order: int = 2):
    """(basis, K, M) for the plane-stress eigenproblem, both in SI.

    K is the same operator ``rotor_stress.assemble_plane_stress`` builds (it is
    re-assembled here only so it shares the mass basis's element object); M is
    the CONSISTENT mass matrix, ``integral rho u . v``, per unit length.
    """
    from skfem import BilinearForm, asm

    kb, mb = _bases(mesh, order)
    ne = mesh.t.shape[1]
    nq_k = kb.global_coordinates().value.shape[-1]
    nq_m = mb.global_coordinates().value.shape[-1]

    def per_elem(a, nq):
        return np.broadcast_to(np.asarray(a, dtype=float).reshape(ne, 1),
                               (ne, nq))

    def _voigt(g):
        return g[0][0], g[1][1], g[0][1] + g[1][0]

    kw = {f"C{i}{j}": per_elem(C_elem[:, i, j], nq_k)
          for i in range(3) for j in range(3)}

    @BilinearForm
    def stiffness(u, v, w):
        eu, ev = _voigt(u.grad), _voigt(v.grad)
        out = 0.0
        for i in range(3):
            si = (w[f"C{i}0"] * eu[0] + w[f"C{i}1"] * eu[1]
                  + w[f"C{i}2"] * eu[2])
            out = out + si * ev[i]
        return out

    @BilinearForm
    def mass(u, v, w):
        return w["rho"] * (u[0] * v[0] + u[1] * v[1])

    K = asm(stiffness, kb, **kw)
    M = asm(mass, mb, rho=per_elem(rho_elem, nq_m))
    return kb, K, M


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------

def boundary_loops(t: np.ndarray) -> List[np.ndarray]:
    """Node index sets of each connected piece of the mesh boundary.

    An edge that belongs to exactly one triangle is on the boundary; the
    boundary edges then split into loops (the outer silhouette, the bore, every
    hole).  Used for two things: which nodes the housing holds, and which nodes
    the circumferential mode order is counted on.
    """
    import scipy.sparse as sp
    from scipy.sparse.csgraph import connected_components

    e = np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    key = np.sort(e, axis=1)
    uniq, inv, cnt = np.unique(key, axis=0, return_inverse=True,
                               return_counts=True)
    bnd = uniq[cnt == 1]
    if bnd.size == 0:
        return []
    n = int(t.max()) + 1
    g = sp.coo_matrix((np.ones(bnd.shape[0]), (bnd[:, 0], bnd[:, 1])),
                      shape=(n, n))
    ncomp, lab = connected_components(g, directed=False)
    nodes = np.unique(bnd)
    out: List[np.ndarray] = []
    for c in range(ncomp):
        sel = nodes[lab[nodes] == c]
        if sel.size >= 3:
            out.append(sel)
    return out


def outer_boundary_facets(mesh) -> np.ndarray:
    """Indices of the mesh facets that lie on the OUTER silhouette.

    The housing holds a SURFACE, not a set of vertices, so the pinned support
    is built from facets and then asked for every dof sitting on them.  Picking
    the held dofs by "radius within 10 um of the maximum" instead looked
    equivalent and was not: on a P2 basis the edge-midpoint dof of a chord
    across the OD arc sits BELOW that radius (49.93 mm on a 50 mm bore), so
    half of the boundary dofs stayed free and the "pinned" stator came back
    with two near-zero translation modes — a housing made of nothing.
    """
    loops = boundary_loops(mesh.t.T)
    if not loops:
        return np.zeros(0, dtype=np.int64)
    r = np.hypot(mesh.p[0], mesh.p[1])
    best = set(int(i) for i in max(loops, key=lambda s: float(r[s].max())))
    fb = mesh.boundary_facets()
    f = mesh.facets[:, fb]
    keep = np.array([(int(a) in best) and (int(b) in best)
                     for a, b in zip(f[0], f[1])], dtype=bool)
    return np.asarray(fb)[keep]


def outer_boundary_nodes(pts: np.ndarray, t: np.ndarray) -> np.ndarray:
    """The nodes of the OUTER silhouette, sorted by angle.

    Picked as the boundary loop reaching the largest radius rather than by a
    radius threshold, because a rotor OD with notches (this machine has them)
    has boundary nodes well inside its own maximum radius and a threshold would
    keep only the tips.
    """
    loops = boundary_loops(t)
    if not loops:
        return np.zeros(0, dtype=np.int64)
    r = np.hypot(pts[:, 0], pts[:, 1])
    best = max(loops, key=lambda s: float(r[s].max()))
    th = np.arctan2(pts[best, 1], pts[best, 0])
    return best[np.argsort(th)]


def _encircles_axis(pts: np.ndarray, loop: np.ndarray) -> bool:
    """True when this boundary loop goes all the way round the axis.

    A stator has many boundary loops — the OD, the bore, and one per closed
    slot.  Only the first two enclose the origin, and the test is that their
    nodes leave no big angular gap; a slot spans a few degrees and fails it.
    """
    if loop.size < 8:
        return False
    th = np.sort(np.arctan2(pts[loop, 1], pts[loop, 0]))
    gaps = np.diff(np.concatenate([th, [th[0] + 2.0 * math.pi]]))
    return float(gaps.max()) < math.pi / 3.0


def bore_boundary_nodes(pts: np.ndarray, t: np.ndarray) -> np.ndarray:
    """The INNERMOST boundary loop that encircles the axis, sorted by angle.

    Needed because a radially pinned stator has, by construction, no radial
    motion on the surface the housing holds — counting the mode order there
    would read "no radial component" on every single mode.  The bore is where
    a pinned stator's shape actually shows, and it is also the surface the air
    gap cares about.
    """
    loops = boundary_loops(t)
    if not loops:
        return np.zeros(0, dtype=np.int64)
    r = np.hypot(pts[:, 0], pts[:, 1])
    cand = [s for s in loops if _encircles_axis(pts, s)]
    if not cand:
        return np.zeros(0, dtype=np.int64)
    best = min(cand, key=lambda s: float(r[s].mean()))
    th = np.arctan2(pts[best, 1], pts[best, 0])
    return best[np.argsort(th)]


def circumferential_order(pts: np.ndarray, u_node: np.ndarray,
                          ring: np.ndarray) -> Optional[int]:
    """The circumferential mode order n of one mode shape.

    Counted the way the user would count it off the picture: the sign changes
    of the RADIAL displacement going once round the outer boundary, divided by
    two.  n = 0 is a breathing mode, n = 1 a rigid translation of the ring,
    n = 2 the ovalisation that every ring machine hears first.

    Samples whose radial displacement is below 8 % of the peak are skipped
    before the signs are counted: near a node of the mode the value is numerical
    dust, and dust flips sign a dozen times per crossing.  A mode with no radial
    component at all (a pure in-plane shear/torsion of the ring) returns None
    rather than a made-up number.
    """
    if ring.size < 4:
        return None
    x, y = pts[ring, 0], pts[ring, 1]
    r = np.maximum(np.hypot(x, y), 1e-12)
    ur = (u_node[ring, 0] * x + u_node[ring, 1] * y) / r
    amp = float(np.abs(ur).max())
    if amp <= 1e-6 * float(max(np.abs(u_node).max(), 1e-30)):
        return None
    s = np.sign(ur[np.abs(ur) > 0.08 * amp])
    if s.size < 2:
        return None
    changes = int(np.count_nonzero(s != np.roll(s, -1)))
    return changes // 2


# ---------------------------------------------------------------------------
# The eigen solve
# ---------------------------------------------------------------------------

#: Below this fraction of the largest computed eigenvalue a mode is a RIGID-BODY
#: mode, not a structure.  A free-free plane body has exactly three (two
#: translations and a rotation) and their computed eigenvalues land 10-12 orders
#: below the first elastic one, so the threshold is nowhere near anything real.
RIGID_REL = 1e-6


def radial_constraint(pts_dof: np.ndarray, held: np.ndarray):
    """Sparse T with u_full = T u_reduced, holding the radial dof of ``held``.

    ``held`` is a boolean mask over NODE indices in the dof-location array (the
    vector element interleaves x and y at the same location, so dof 2k and 2k+1
    live at ``pts_dof[k]``).  A held node keeps exactly one unknown, its
    TANGENTIAL amplitude along t = (-sin, cos); every other node keeps both.

    Exact elimination rather than a stiff radial spring: a penalty large enough
    to be a housing is large enough to wreck the conditioning of a shift-invert
    factorisation, and one that is not is a housing made of rubber.
    """
    import scipy.sparse as sp

    nnode = pts_dof.shape[0]
    rows: List[int] = []
    cols: List[int] = []
    vals: List[float] = []
    col = 0
    for k in range(nnode):
        if held[k]:
            th = math.atan2(pts_dof[k, 1], pts_dof[k, 0])
            rows += [2 * k, 2 * k + 1]
            cols += [col, col]
            vals += [-math.sin(th), math.cos(th)]
            col += 1
        else:
            rows += [2 * k, 2 * k + 1]
            cols += [col, col + 1]
            vals += [1.0, 1.0]
            col += 2
    return sp.csr_matrix((vals, (rows, cols)), shape=(2 * nnode, col))


def eigen_modes(K, M, n_modes: int, n_rigid_expected: int = 3
                ) -> Tuple[np.ndarray, np.ndarray, int]:
    """(frequencies Hz, mode vectors, number of rigid modes found).

    ``n_rigid_expected`` is how many zero-energy modes the SUPPORT leaves —
    3 free-free (two translations and a rotation), and 1 for the radially
    pinned stator, which can still spin rigidly inside its housing because the
    constraint holds the radial dof and leaves the tangential one free.  It
    only sizes the extra modes ARPACK is asked for; how many are actually
    dropped is counted from the eigenvalues, so a support that turns out not to
    hold what it claims shows up as a number, not as a silent shift.

    ARPACK in shift-invert mode with a NEGATIVE shift.  ``eigsh`` returns the
    eigenvalues nearest sigma, and with sigma = -s (s > 0) the ordering by
    |lambda - sigma| is just the ordering by lambda, so the k smallest come back
    — while the matrix that is actually factorised, K + s M, is positive
    definite even when K is singular.  Shifting to a small POSITIVE sigma
    instead would ask SuperLU to factorise a nearly singular K on a free-free
    body, which is the classic way to get a rigid mode reported at 40 Hz.
    """
    import scipy.sparse.linalg as spla

    ndof = K.shape[0]
    n_exp = max(0, int(n_rigid_expected))
    k = int(n_modes) + n_exp
    k = max(1, min(k, ndof - 2))
    scale = float(K.diagonal().sum() / max(M.diagonal().sum(), 1e-300))
    vals, vecs = spla.eigsh(K.tocsc(), k=k, M=M.tocsc(),
                            sigma=-1e-3 * scale, which="LM")
    o = np.argsort(vals)
    vals, vecs = vals[o], vecs[:, o]
    n_rigid = 0
    if n_exp:
        top = float(max(vals[-1], 1e-300))
        n_rigid = int(np.count_nonzero(vals < RIGID_REL * top))
        vals, vecs = vals[n_rigid:], vecs[:, n_rigid:]
    f = np.sqrt(np.maximum(vals, 0.0)) / (2.0 * math.pi)
    return f, vecs, n_rigid


# ---------------------------------------------------------------------------
# Excitation orders
# ---------------------------------------------------------------------------

def excitation_orders(rpm: float, num_poles: int, num_slots: int,
                      f_switch: Optional[float] = None) -> List[Dict[str, Any]]:
    """Every line an engineer draws on a Campbell plot of THIS machine.

    Mechanical rotation and its second order (unbalance and its harmonic), the
    magnetic force fundamental at 2*f_e (a radial Maxwell force goes as B^2, so
    it beats at twice the electrical frequency, not at it), slot passing and its
    second order, and the inverter carrier with its second harmonic when the
    machine is driven by one.

    Each row carries its ``order`` — cycles per MECHANICAL revolution — beside
    the frequency, because a Campbell plot draws a ray f = order * rpm/60 and a
    frequency alone cannot say what its slope is.  The carrier's order is None
    on purpose: an inverter switches at its own fixed rate no matter how fast
    the shaft turns, so its line is horizontal, and inferring that from the
    row's NAME in the UI would be a guess where an answer exists.
    """
    f_rot = float(rpm) / 60.0
    f_e = f_rot * (int(num_poles) / 2.0)
    out: List[Dict[str, Any]] = [
        {"name": "rotation 1×", "hz": f_rot, "order": 1.0,
         "note": "unbalance — the order every rotor is excited at"},
        {"name": "rotation 2×", "hz": 2.0 * f_rot, "order": 2.0,
         "note": "misalignment / two-per-rev geometry"},
        {"name": "2·f_e", "hz": 2.0 * f_e, "order": float(int(num_poles)),
         "note": f"magnetic force fundamental (f_e = {f_e:,.0f} Hz); a Maxwell "
                 "stress goes as B², so it beats at twice the electrical "
                 "frequency"},
        {"name": "slot passing", "hz": float(num_slots) * f_rot,
         "order": float(int(num_slots)),
         "note": f"{int(num_slots)} slots × rotation — the permeance ripple the "
                 "rotor sees"},
        {"name": "slot passing 2×", "hz": 2.0 * float(num_slots) * f_rot,
         "order": 2.0 * float(int(num_slots)),
         "note": "second harmonic of the slot-passing force"},
    ]
    if f_switch and float(f_switch) > 0:
        fs = float(f_switch)
        out.append({"name": "PWM carrier", "hz": fs, "order": None,
                    "note": "inverter switching frequency — the loudest "
                            "high-frequency force on most drives; it does NOT "
                            "scale with speed"})
        out.append({"name": "PWM carrier 2×", "hz": 2.0 * fs, "order": None,
                    "note": "second carrier harmonic"})
    return out


#: A mode within this fraction of an excitation line is flagged.  10 % is the
#: usual first-pass separation-margin rule; it is not a standard, it is the
#: threshold at which an engineer stops and looks.
SEPARATION_FLAG = 0.10


def nearest_excitation(f_hz: float,
                       excitations: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Which excitation line this mode sits closest to, and by how much.

    The margin is relative to the EXCITATION, which is the number a separation
    requirement is written against ("keep every mode 10 % clear of 2·f_e").
    """
    best: Optional[Dict[str, Any]] = None
    for e in excitations:
        hz = float(e["hz"])
        if hz <= 0:
            continue
        margin = (f_hz - hz) / hz
        if best is None or abs(margin) < abs(best["margin"]):
            best = {"name": e["name"], "hz": hz, "margin": margin}
    if best is None:
        return {"name": None, "hz": None, "margin_pct": None, "flag": False}
    return {"name": best["name"], "hz": best["hz"],
            "margin_pct": 100.0 * best["margin"],
            "flag": bool(abs(best["margin"]) < SEPARATION_FLAG)}


# ---------------------------------------------------------------------------
# The stator mesh
# ---------------------------------------------------------------------------

def build_stator_mesh(polys: dict, mesh_size_mm: float = 2.5,
                      min_size_mm: float = 0.4):
    """(skfem MeshTri in METRES, outlines in mm) of the stator core.

    The ``stator`` polygon already has the slots as holes, so nothing is
    subtracted here — the copper does not carry load and is added as mass later.
    Same gmsh settings as the rotor mesher so the two bodies are comparable.
    """
    import gmsh
    from shapely.geometry import MultiPolygon
    from skfem import MeshTri

    from motor_ai_sim.simulation.sb_domains import _GMSH_LOCK

    stator = polys.get("stator")
    if stator is None:
        raise ValueError("this geometry has no stator polygon — nothing to "
                         "solve the stator modes on")
    geoms = list(stator.geoms) if isinstance(stator, MultiPolygon) else [stator]

    _GMSH_LOCK.acquire()
    try:
        try:
            gmsh.initialize([], interruptible=False)
        except TypeError:
            gmsh.initialize()
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.option.setNumber("Mesh.MeshSizeMin", min_size_mm)
            gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size_mm)
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 45)
            gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
            gmsh.option.setNumber("Mesh.Algorithm", 6)
            gmsh.option.setNumber("Geometry.Tolerance", 1e-5)
            gmsh.option.setNumber("Geometry.ToleranceBoolean", 1e-2)
            gmsh.model.add("stator_modal")
            occ = gmsh.model.occ

            def add_ring(coords) -> int:
                cs = list(coords)[:-1]
                a2 = 0.0
                for i in range(len(cs)):
                    x0, y0 = cs[i]
                    x1, y1 = cs[(i + 1) % len(cs)]
                    a2 += x0 * y1 - x1 * y0
                if a2 < 0:
                    cs = cs[::-1]
                pts_: List[int] = []
                prev = None
                for (x, y) in cs:
                    if prev is not None and abs(x - prev[0]) < 1e-4 \
                            and abs(y - prev[1]) < 1e-4:
                        continue
                    pts_.append(occ.addPoint(float(x), float(y), 0.0))
                    prev = (x, y)
                lines = [occ.addLine(pts_[i], pts_[(i + 1) % len(pts_)])
                         for i in range(len(pts_))]
                return occ.addCurveLoop(lines)

            surfs = []
            for g in geoms:
                loops = [add_ring(g.exterior.coords)]
                loops += [add_ring(r.coords) for r in g.interiors]
                surfs.append((2, occ.addPlaneSurface(loops)))
            occ.synchronize()
            if len(surfs) > 1:
                occ.fragment(surfs, [])
                occ.synchronize()
            gmsh.model.mesh.generate(2)

            ntags, ncoord, _ = gmsh.model.mesh.getNodes()
            etypes, _et, enodes = gmsh.model.mesh.getElements(2)
            coords = np.asarray(ncoord, dtype=float).reshape(-1, 3)[:, :2]
            o = np.argsort(np.asarray(ntags, dtype=np.int64))
            tag2idx = np.zeros(int(np.max(ntags)) + 1, dtype=np.int64)
            tag2idx[np.asarray(ntags, dtype=np.int64)[o]] = np.arange(len(ntags))
            pts = coords[o]
            tris = [tag2idx[np.asarray(en, dtype=np.int64)].reshape(-1, 3)
                    for et, en in zip(etypes, enodes) if int(et) == 2]
            if not tris:
                raise RuntimeError("gmsh produced no triangles for the stator")
            t = np.vstack(tris)
        finally:
            gmsh.finalize()
    finally:
        _GMSH_LOCK.release()

    used = np.unique(t)
    remap = -np.ones(pts.shape[0], dtype=np.int64)
    remap[used] = np.arange(used.size)
    pts, t = pts[used], remap[t]

    v0, v1, v2 = pts[t[:, 0]], pts[t[:, 1]], pts[t[:, 2]]
    a2 = ((v1[:, 0] - v0[:, 0]) * (v2[:, 1] - v0[:, 1])
          - (v2[:, 0] - v0[:, 0]) * (v1[:, 1] - v0[:, 1]))
    t[a2 < 0] = t[a2 < 0][:, [0, 2, 1]]

    outlines: List[List[List[float]]] = []
    for g in geoms:
        outlines.append([[float(x), float(y)] for x, y in g.exterior.coords])
        outlines += [[[float(x), float(y)] for x, y in r.coords]
                     for r in g.interiors]

    mesh = MeshTri(np.ascontiguousarray(pts.T * 1e-3),
                   np.ascontiguousarray(t.T.astype(np.int64)))
    return mesh, outlines


def winding_nsm(polys: dict, params: dict, pts_m: np.ndarray, t: np.ndarray,
                rho_cu: float) -> Tuple[np.ndarray, np.ndarray, float]:
    """(node indices, mass per node kg/m, total copper mass kg/m).

    The winding as NON-STRUCTURAL MASS on the slot walls.  A slot-wall node is a
    mesh boundary node whose radius is strictly inside the slot band
    (bore < r < bore + slot_height) — that picks the tooth flanks and the slot
    bottoms and leaves out the bore arc and the stator OD, which are not slot
    walls.  Each node takes the mass of half of each boundary edge it owns, so
    a deep slot gets more of the copper than a shallow one without any per-slot
    bookkeeping.
    """
    from motor_ai_sim.simulation.field_ops import coil_copper_area_total_m2

    a_cu_m2 = float(coil_copper_area_total_m2(polys) or 0.0)
    if a_cu_m2 <= 0:
        # No coil polygons (or shapely could not union them): fall back to the
        # nominal rectangle stack, the same fallback masses.py uses.
        nw = float(params.get("num_wires_per_slot") or 0)
        ww = float(params.get("wire_width") or 0)
        wh = float(params.get("wire_height") or 0)
        ns = float(params.get("num_slots") or 0)
        a_cu_m2 = nw * ww * wh * ns * 1e-6
    m_total = a_cu_m2 * float(rho_cu)          # kg per metre of stack
    if m_total <= 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0), 0.0

    r_bore = float(params.get("stator_inner_radius") or 0.0) * 1e-3
    slot_h = float(params.get("slot_height") or 0.0) * 1e-3
    if r_bore <= 0 or slot_h <= 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0), 0.0

    e = np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    key = np.sort(e, axis=1)
    uniq, cnt = np.unique(key, axis=0, return_counts=True)
    bnd = uniq[cnt == 1]
    if bnd.size == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0), 0.0

    r = np.hypot(pts_m[:, 0], pts_m[:, 1])
    tol = 1e-5                                   # 10 µm, well under any feature
    lo, hi = r_bore + tol, r_bore + slot_h - tol
    inband = (r > lo) & (r < hi)
    sel = bnd[inband[bnd[:, 0]] & inband[bnd[:, 1]]]
    if sel.size == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0), 0.0

    ln = np.hypot(pts_m[sel[:, 1], 0] - pts_m[sel[:, 0], 0],
                  pts_m[sel[:, 1], 1] - pts_m[sel[:, 0], 1])
    w = np.zeros(pts_m.shape[0])
    np.add.at(w, sel[:, 0], 0.5 * ln)
    np.add.at(w, sel[:, 1], 0.5 * ln)
    idx = np.nonzero(w > 0)[0]
    mass = m_total * w[idx] / w[idx].sum()
    return idx.astype(np.int64), mass, m_total


# ---------------------------------------------------------------------------
# Public solve
# ---------------------------------------------------------------------------

def _stator_material(assignments: Optional[dict],
                     overrides: Optional[dict]) -> PartMech:
    from motor_ai_sim.materials import DEFAULT_PART_MATERIAL

    name = (assignments or {}).get("stator_core") \
        or DEFAULT_PART_MATERIAL.get("stator_core")
    if not name:
        raise MissingMechanicalProperty(
            "stator", "(unassigned)", "material",
            "no materials.stator_core in the config")
    # part_mech's category hint is keyed on the assignment key, and 'stator_core'
    # is already one of them (PART_CATEGORIES) — pass it through unchanged.
    return part_mech("stator_core", str(name), overrides)


def _copper_density(overrides: Optional[dict]) -> float:
    from motor_ai_sim import materials as _mats

    try:
        raw = (_mats._load().get("conductor") or {}).get("copper") or {}  # noqa: SLF001
        d = float(raw.get("density") or 0.0)
        if d > 0:
            return d
    except Exception:  # noqa: BLE001
        pass
    return 8960.0     # pure copper — only reached if the library has no card


def solve_modes(polys: dict,
                params: dict,
                assignments: Optional[dict],
                body: str = "rotor",
                n_modes: int = 12,
                support: str = "free",
                mesh_size_mm: float = 2.5,
                order: int = 2,
                material_overrides: Optional[dict] = None,
                winding_mass: bool = True,
                with_shapes: bool = True,
                rpm: float = 0.0,
                f_switch: Optional[float] = None,
                progress=None) -> Dict[str, Any]:
    """The first ``n_modes`` elastic in-plane modes of one body.

    Returns frequencies, circumferential orders, the nearest excitation line for
    each, and (optionally) the mode shapes as per-vertex displacement fields for
    the map.  Everything is per unit length of stack — see the module docstring.

    ``progress`` (2026-09-07) is the shared live-progress callback (see
    ``motor_ai_sim.progress``).  The budget is honest rather than flattering:
    mesh + assembly + the eigensolve + one step per mode shaped and counted.  It
    is deliberately NOT one step per mode of solver time — ARPACK returns all of
    them at once — so the bar sits on "eigensolve" for the block that actually
    takes the seconds, and the phase says so instead of a counter creeping
    through modes that are already computed.
    """
    from motor_ai_sim.progress import StepLedger

    body = str(body).lower()
    if body not in BODIES:
        raise ValueError(f"body must be one of {BODIES}, got {body!r}")
    support = str(support).lower()
    if support not in SUPPORTS:
        raise ValueError(f"support must be one of {SUPPORTS}, got {support!r}")
    n_modes = int(n_modes)
    if n_modes < 1 or n_modes > 40:
        raise ValueError("n must be between 1 and 40")
    if body == "rotor" and support != "free":
        raise ValueError("the rotor is solved free-free: nothing holds a rotor "
                         "cross-section in its own plane, and pinning its OD "
                         "would model a rotor bolted to the housing")

    nsm_idx = np.zeros(0, dtype=np.int64)
    nsm_mass = np.zeros(0)
    m_cu = 0.0

    # What the MESH cost, reported separately from the solve seconds (user
    # 2026-09-06 asked to see and control the mesh, and "the mesh was 40 of
    # those 50 seconds" is what makes a mesh-size choice informed).  It is the
    # cost of BUILDING this mesh, which the rotor memo remembers across reuses —
    # `mesh_reused` on the stress result is the field that says whether this
    # particular call paid it.
    # mesh + assembly + eigensolve, then one step per mode reported.
    led = StepLedger(progress, total=3 + n_modes,
                     composition=(f"mesh + assembly + eigensolve + {n_modes} "
                                  f"mode{'s' if n_modes > 1 else ''}"))

    _t_mesh = time.perf_counter()
    if body == "rotor":
        rm = build_rotor_mesh(polys, mesh_size_mm=mesh_size_mm,
                              progress=lambda d, t, ph=None, c=None: led.at(d, ph))
        mesh, part_tri, outlines = rm.mesh, rm.part_tri, rm.outlines
        mech = resolve_part_materials(assignments, polys.get("sleeve") is not None,
                                      material_overrides)
        ne = mesh.t.shape[1]
        p = mesh.p.T
        cen = (p[mesh.t[0]] + p[mesh.t[1]] + p[mesh.t[2]]) / 3.0
        phi = np.arctan2(cen[:, 1], cen[:, 0])
        C_elem = np.zeros((ne, 3, 3))
        rho_elem = np.zeros(ne)
        for name, pid in (("rotor", PART_ROTOR), ("magnet", PART_MAGNET),
                          ("sleeve", PART_SLEEVE), ("shaft", PART_SHAFT)):
            m = part_tri == pid
            if not m.any():
                continue
            pm = mech.get(name)
            if pm is None:
                raise MissingMechanicalProperty(name, "(unassigned)", "material")
            C_elem[m] = part_C(pm, phi[m])
            rho_elem[m] = pm.density
        mats = {n: pm for n, pm in mech.items()}
    else:
        led.phase("mesh build (gmsh)")
        mesh, outlines = build_stator_mesh(polys, mesh_size_mm=mesh_size_mm)
        pm = _stator_material(assignments, material_overrides)
        ne = mesh.t.shape[1]
        part_tri = np.full(ne, PART_STATOR, dtype=np.int8)
        C_elem = part_C(pm, np.zeros(ne))
        rho_elem = np.full(ne, pm.density)
        mats = {"stator": pm}
        if winding_mass:
            nsm_idx, nsm_mass, m_cu = winding_nsm(
                polys, params, mesh.p.T, mesh.t.T, _copper_density(material_overrides))

    mesh_s = (float(rm.build_s) if body == "rotor"
              else round(time.perf_counter() - _t_mesh, 3))

    led.at(1, "assembly (stiffness, mass)")
    basis, K, M = assemble_K_M(mesh, C_elem, rho_elem, order=order)

    # ── the winding, as lumped non-structural mass on the slot walls ────────
    if nsm_idx.size:
        import scipy.sparse as sp
        nd = basis.nodal_dofs
        d = np.zeros(K.shape[0])
        d[nd[0][nsm_idx]] += nsm_mass
        d[nd[1][nsm_idx]] += nsm_mass
        M = (M + sp.diags(d)).tocsr()

    # ── the housing, as an exact radial constraint ──────────────────────────
    T = None
    # Zero-energy modes the support leaves: 3 free-free, and 1 pinned — a ring
    # held radially can still spin rigidly inside its housing.
    n_rigid_expected = 3
    n_held = 0
    if support == "pinned":
        ndof = K.shape[0]
        ix = np.arange(0, ndof, 2)
        loc = np.stack([basis.doflocs[0][ix], basis.doflocs[1][ix]], axis=1)
        facets = outer_boundary_facets(mesh)
        if facets.size == 0:
            raise ValueError("no outer-surface facets found to pin")
        # Every dof ON those facets — the vertex dofs AND the P2 edge dofs.
        # The vector element interleaves x/y at one location, so dof // 2 is
        # the index into ``loc`` above.
        D = basis.get_dofs(facets=facets)
        got = [np.asarray(v).ravel() for v in
               list(getattr(D, "nodal", {}).values())
               + list(getattr(D, "facet", {}).values())]
        idx = np.unique(np.concatenate(got)) // 2 if got else np.zeros(0, np.int64)
        held = np.zeros(loc.shape[0], dtype=bool)
        held[idx] = True
        if not held.any():
            raise ValueError("no outer-surface dofs found to pin")
        n_held = int(held.sum())
        T = radial_constraint(loc, held).tocsr()
        K = (T.T @ K @ T).tocsr()
        M = (T.T @ M @ T).tocsr()
        n_rigid_expected = 1

    led.at(2, f"eigensolve ({n_modes} elastic mode"
              f"{'s' if n_modes > 1 else ''})")
    f_hz, vecs, n_rigid = eigen_modes(K, M, n_modes, n_rigid_expected)
    led.at(3, "mode shapes")
    if T is not None:
        vecs = np.asarray(T @ vecs)

    # ── mode shapes on the vertices, and the order counter ──────────────────
    pts_mm = mesh.p.T * 1e3
    tri = mesh.t.T
    # WHICH boundary the order is counted on is part of the answer, so it is
    # reported alongside it: the free body is read on its outer silhouette, the
    # pinned one on its bore (its OD is held and cannot move radially).
    ring_on = "bore" if support == "pinned" else "outer"
    ring = (bore_boundary_nodes(pts_mm, tri) if ring_on == "bore"
            else outer_boundary_nodes(pts_mm, tri))
    if ring.size < 4:
        ring, ring_on = outer_boundary_nodes(pts_mm, tri), "outer"
    nd = basis.nodal_dofs
    shapes: List[List[List[float]]] = []
    modes: List[Dict[str, Any]] = []
    exc = excitation_orders(rpm, int(params.get("num_poles") or 0),
                            int(params.get("num_slots") or 0), f_switch) \
        if rpm and rpm > 0 else []

    _n_got = min(len(f_hz), n_modes)
    # Fewer modes than asked for is possible (a tiny mesh has fewer dofs than
    # requested modes); hand the unreported ones back so the bar ends where the
    # work does.
    led.give_back(n_modes - _n_got)
    for i in range(_n_got):
        led.at(4 + i, f"mode {i + 1}/{_n_got} (order, excitation, shape)")
        v = vecs[:, i]
        u = np.stack([v[nd[0]], v[nd[1]]], axis=1)
        peak = float(np.abs(u).max()) or 1.0
        u = u / peak                       # normalised: peak |component| = 1
        n_circ = circumferential_order(pts_mm, u, ring)
        row: Dict[str, Any] = {
            "index": i + 1,
            "f_hz": float(f_hz[i]),
            "order": (int(n_circ) if n_circ is not None else None),
        }
        if exc:
            row["nearest"] = nearest_excitation(float(f_hz[i]), exc)
        modes.append(row)
        if with_shapes:
            shapes.append(np.round(u, 4).tolist())

    out: Dict[str, Any] = {
        "body": body,
        "support": support,
        "n_modes": len(modes),
        "rpm": float(rpm or 0.0),
        "modes": modes,
        "excitations": exc,
        "mesh": {
            "n_nodes": int(mesh.p.shape[1]),
            "n_triangles": int(mesh.t.shape[1]),
            "n_dof": int(K.shape[0]),
            "element_order": int(order),
            "mesh_size_mm": float(mesh_size_mm),
            # seconds spent meshing, out of the solve's own elapsed_s
            "mesh_s": float(mesh_s),
            "n_rigid_modes_dropped": int(n_rigid),
            "n_rigid_modes_expected": int(n_rigid_expected),
            "n_pinned_locations": int(n_held),
            "boundary_nodes_counted": int(ring.size),
            "order_counted_on": ring_on,
        },
        "winding_mass_kg_per_m": float(m_cu),
        "materials": {n: {"material": pm.material, "density": pm.density,
                          "youngs_modulus_gpa": pm.E / 1e9,
                          "poisson_ratio": pm.nu,
                          "orthotropic": pm.orthotropic,
                          "note": pm.source_note}
                      for n, pm in mats.items()},
        "assumptions": (
            "2-D plane stress, per unit length — ring / ovalisation modes only, "
            "no axial half-waves. Every interface BONDED (a linear eigenproblem "
            "has no contact state). No centrifugal prestress. "
            + ("winding copper added as lumped non-structural mass on the slot "
               "walls." if body == "stator" and m_cu > 0 else
               "no winding mass on this body.")),
    }
    if with_shapes:
        ext = float(np.abs(pts_mm).max())
        out["field"] = {
            "vertices": np.round(pts_mm, 5).astype(np.float32).tolist(),
            "triangles": tri.astype(np.int32).tolist(),
            "domain_per_tri": np.asarray(part_tri, dtype=np.int8).tolist(),
            "part_names": MODAL_PART_NAMES,
            "outlines": outlines,
            "extent": ext,
            "modes": shapes,
        }
    return out


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_CACHE: Dict[Any, Dict[str, Any]] = {}
_CACHE_MAX = 8


def cache_get(key) -> Optional[Dict[str, Any]]:
    return _CACHE.get(key)


def cache_put(key, value: Dict[str, Any]) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)), None)
    _CACHE[key] = value


def clear_cache() -> int:
    n = len(_CACHE)
    _CACHE.clear()
    return n
