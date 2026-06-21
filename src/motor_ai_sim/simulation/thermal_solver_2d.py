"""Steady-state 2-D heat-conduction solver (thermal twin of the EM FEM).

Solves  -∇·(k ∇T) = q  on the SAME triangular mesh the electromagnetic solver
produces, with the EM losses as the volumetric heat source q [W/m³] and a Robin
(convection) boundary condition at the stator outer surface (the housing):

        -k ∂T/∂n = h · (T - T_ambient)            on the housing

It reuses scikit-fem (the project's FEM library) exactly like fem_solver_2d:
ElementTriP1 temperature, per-element k/q carried as an ElementTriP0 field, and
the convection term assembled on the outer FacetBasis.  The far-field outer-air
ring (DOM_OUTER) is dropped so the mesh boundary IS the housing.

PHYSICS NOTES
- Pure 2-D in-plane conduction → the temperature field is independent of stack
  length (both K and f scale with it).  Axial heat paths (end windings, end
  caps, shaft conduction) are lumped into the housing convection coefficient h.
- Steady state: gives the equilibrium temperature map for a constant loss set.
"""
from __future__ import annotations

from typing import Dict, Any, Iterable
import numpy as np


def solve_steady_thermal(
    P_m: np.ndarray,            # (2, n_nodes)  node coords [m]
    tri: np.ndarray,            # (3, n_elem)   node indices per triangle
    cell_tags: np.ndarray,      # (n_elem,)     domain tag per triangle
    k_elem: np.ndarray,         # (n_elem,)     thermal conductivity [W/m·K]
    q_elem: np.ndarray,         # (n_elem,)     volumetric heat source [W/m³]
    *,
    drop_tags: Iterable[int],   # domain tags to exclude (outer air + the gap/air)
    r_housing_m: float,         # stator outer radius [m] — convection surface
    rotor_outer_m: float,       # rotor OD [m]  — gap-bridge inner ring
    stator_inner_m: float,      # stator bore [m] — gap-bridge outer ring
    gap_k: float,               # effective air-gap conductivity [W/m·K]
    h_conv: float,              # convection coefficient [W/m²·K]
    t_ambient: float,           # ambient / coolant temperature [°C]
) -> Dict[str, Any]:
    """Return {vertices, triangles, cell_tags, T_node [°C], flux_elem (2,m),
    flux_mag_elem, T_min, T_max} for the SOLID sub-mesh (outer air removed)."""
    from skfem import (
        MeshTri, Basis, FacetBasis, ElementTriP1, ElementTriP0,
        BilinearForm, LinearForm,
    )
    from skfem.helpers import dot, grad
    from scipy.sparse.linalg import spsolve

    P_m = np.asarray(P_m, float)
    tri = np.asarray(tri, int)
    cell_tags = np.asarray(cell_tags, int)
    k_elem = np.asarray(k_elem, float)
    q_elem = np.asarray(q_elem, float)

    # ── 1. drop the far-field outer-air elements → solid sub-mesh ──────────────
    drop = np.isin(cell_tags, np.asarray(list(drop_tags), int))
    keep = ~drop
    t_keep = tri[:, keep]
    k_keep = k_elem[keep]
    q_keep = q_elem[keep]
    tags_keep = cell_tags[keep]

    # remap to the used nodes only
    used = np.unique(t_keep)
    remap = -np.ones(P_m.shape[1], int)
    remap[used] = np.arange(used.size)
    p_sub = P_m[:, used]                       # (2, n)
    t_sub = remap[t_keep]                      # (3, m)

    # WELD coincident nodes.  The EM mesh's sliding band is non-conforming
    # (rotor-side in_band and stator-side out_band carry duplicate nodes at the
    # gap interface); without welding the rotor is a thermally DISCONNECTED island
    # with no path to the cooled housing → singular system → NaN.  Merging nodes
    # that share a position (to ~1 µm) reconnects the gap so heat conducts
    # rotor → air-gap → stator.
    keyc = np.round(p_sub.T * 1e6).astype(np.int64)        # (n, 2) micron grid
    _, first, inv = np.unique(keyc, axis=0, return_index=True, return_inverse=True)
    p_weld = p_sub[:, first]                                # welded node coords
    t_weld = inv[t_sub]                                     # remap elements
    # drop any triangle that collapsed (two welded nodes coincide)
    nondegen = ((t_weld[0] != t_weld[1]) & (t_weld[1] != t_weld[2]) & (t_weld[0] != t_weld[2]))
    t_weld = t_weld[:, nondegen]
    k_keep = k_keep[nondegen]; q_keep = q_keep[nondegen]; tags_keep = tags_keep[nondegen]

    mesh = MeshTri(p_weld.copy(), t_weld.copy())
    p_sub = p_weld; t_sub = t_weld
    # MeshTri may reorder elements internally — realign per-element fields to it.
    order = _match_element_order(mesh, t_sub)
    k_ord = k_keep[order]; q_ord = q_keep[order]; tags_ord = tags_keep[order]

    basis = Basis(mesh, ElementTriP1())
    p0 = Basis(mesh, ElementTriP0())
    k_field = p0.interpolate(k_ord)
    q_field = p0.interpolate(q_ord)

    @BilinearForm
    def conduction(u, v, w):
        return w["k"] * dot(grad(u), grad(v))

    @LinearForm
    def heat_source(v, w):
        return w["q"] * v

    K = conduction.assemble(basis, k=k_field)
    f = heat_source.assemble(basis, q=q_field)

    # ── 2. Robin (convection) BC on the housing (outermost boundary facets) ────
    bnd = mesh.boundary_facets()
    fmid = mesh.p[:, mesh.facets[:, bnd]].mean(axis=1)   # (2, n_bnd)
    r_bnd = np.hypot(fmid[0], fmid[1])
    housing = bnd[r_bnd > 0.9 * float(r_housing_m)]      # outer ring only (not shaft bore)
    if housing.size == 0:                                # fallback: outermost facets
        housing = bnd[r_bnd > 0.9 * r_bnd.max()]
    fb = FacetBasis(mesh, ElementTriP1(), facets=housing)

    @BilinearForm
    def robin(u, v, w):
        return h_conv * u * v

    @LinearForm
    def robin_rhs(v, w):
        return h_conv * t_ambient * v

    K = (K + robin.assemble(fb)).tolil()
    f = f + robin_rhs.assemble(fb)

    # ── 2b. GAP BRIDGE: with the air dropped, the rotor (and any air-gapped
    # magnets) become DISCONNECTED islands with no path to the cooled housing.
    # Reconnect every island to the main (housing) component by lumped conduction
    # across the gap: link each island boundary node to the nearest main-component
    # node, the links of each island summing to ~ k_gap·(2π r_gap)/gap_thickness.
    from scipy.sparse import coo_matrix as _coo
    from scipy.sparse.csgraph import connected_components as _cc
    from scipy.spatial import cKDTree as _KDTree

    n_nodes = p_sub.shape[1]
    edges = np.hstack([t_sub[[0, 1]], t_sub[[1, 2]], t_sub[[2, 0]]])      # (2, 3m)
    adj = _coo((np.ones(edges.shape[1]), (edges[0], edges[1])), shape=(n_nodes, n_nodes))
    n_comp, labels = _cc(adj + adj.T, directed=False)
    bnodes = np.unique(mesh.facets[:, housing]) if housing.size else np.array([], int)
    main_lbl = int(np.bincount(labels[bnodes]).argmax()) if bnodes.size else int(np.bincount(labels).argmax())
    all_bnd = np.unique(mesh.facets[:, mesh.boundary_facets()])
    gap_d = max(float(stator_inner_m) - float(rotor_outer_m), 1e-5)
    n_bridge = 0
    if n_comp > 1:
        main_nodes = np.where(labels == main_lbl)[0]
        tree = _KDTree(p_sub[:, main_nodes].T)
        bset = set(all_bnd.tolist())
        for c in range(n_comp):
            if c == main_lbl:
                continue
            cn = np.where(labels == c)[0]
            cb = np.array([x for x in cn if x in bset], int)
            if cb.size == 0:
                cb = cn
            _, idx = tree.query(p_sub[:, cb].T)
            r_c = float(np.hypot(p_sub[0, cb], p_sub[1, cb]).mean())
            g_link = float(gap_k) * (2.0 * np.pi * max(r_c, 1e-4) / cb.size) / gap_d
            for j, a in enumerate(cb):
                a = int(a); b = int(main_nodes[int(idx[j])])
                K[a, a] += g_link; K[b, b] += g_link
                K[a, b] -= g_link; K[b, a] -= g_link
            n_bridge += int(cb.size)

    # ── 3. solve  K · T = f  (Robin BC makes K SPD, no Dirichlet needed) ───────
    T = np.asarray(spsolve(K.tocsr(), f), float)
    n_bad = int((~np.isfinite(T)).sum())          # >0 ⇒ a still-disconnected island
    if n_bad:
        T = np.nan_to_num(T, nan=float(t_ambient),
                          posinf=float(t_ambient), neginf=float(t_ambient))

    # ── 4. per-element heat flux  q = -k ∇T  ───────────────────────────────────
    gradT = basis.interpolate(T).grad            # (2, n_elem, n_qp)
    gT = gradT.mean(axis=2)                       # element-mean gradient (2, m)
    flux = np.nan_to_num(-k_ord[None, :] * gT)    # (2, m)  [W/m²]
    flux_mag = np.hypot(flux[0], flux[1])

    return {
        "vertices": p_sub.T.tolist(),             # (n, 2) metres
        "triangles": t_sub.T.tolist(),            # (m, 3)
        "cell_tags": tags_ord.tolist(),
        "T_node": T.tolist(),                     # (n,) °C
        "flux_elem": flux.T.tolist(),             # (m, 2) W/m²
        "flux_mag_elem": flux_mag.tolist(),
        "T_min": float(T.min()), "T_max": float(T.max()),
        "n_housing_facets": int(housing.size),
        "n_bridge_links": int(n_bridge),
        "n_nonfinite": int(n_bad),
    }


def _match_element_order(mesh, t_sub: np.ndarray) -> np.ndarray:
    """Index array ``order`` so that a per-element field in OUR ``t_sub`` order,
    indexed ``field[order]``, lines up with the mesh's internal element order
    (what ElementTriP0 expects).  MeshTri usually preserves order; we match by the
    sorted-node-triple key to be safe."""
    def keys(t):
        s = np.sort(np.asarray(t, np.int64), axis=0)
        return s[0] * 1_000_000_000 + s[1] * 1_000_000 + s[2]
    mine = keys(t_sub)
    theirs = keys(mesh.t)
    if np.array_equal(mine, theirs):
        return np.arange(t_sub.shape[1])
    sorter = np.argsort(mine)
    return sorter[np.searchsorted(mine, theirs, sorter=sorter)]
