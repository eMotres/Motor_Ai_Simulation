"""Steady-state 2-D heat-conduction solver (thermal twin of the EM FEM).

Solves  -∇·(K ∇T) = q  on the SAME triangular mesh the electromagnetic solver
produces, with the EM losses as the volumetric heat source q [W/m³] and one Robin
(convection) boundary condition per COOLED SURFACE:

        -K ∂T/∂n = h · (T - T_sink)            on each cooled surface

It reuses scikit-fem (the project's FEM library) exactly like fem_solver_2d:
ElementTriP1 temperature, per-element material data carried as ElementTriP0
fields, and the convection terms assembled on per-surface FacetBases.  The
far-field outer-air ring (DOM_OUTER) and the gap air are dropped so the mesh
boundary IS the cooled hardware.

WHAT CHANGED 2026-09-07 (and why)
=================================
1. **K is a TENSOR, not a scalar.**  A hoop-wound carbon retaining sleeve is the
   most anisotropic thing in the machine: ~0.8 W/m·K through its thickness and
   ~7 W/m·K along the fibres.  Averaging those into one number is not a small
   error — the radial value is what stands between the rotor and the only heat
   path it has, and the hoop value is what smears the magnet hotspots round the
   circumference.  Each element therefore carries (k_radial, k_tangential) and
   the bilinear form builds the 2×2 tensor

        K = k_r · r̂ r̂ᵀ  +  k_t · (I − r̂ r̂ᵀ)

   in the LOCAL cylindrical frame at each quadrature point.  Isotropic elements
   pass k_r = k_t and the form collapses to k·∇u·∇v exactly, so there is one code
   path and no branch to get wrong.

2. **A LIST of Robin surfaces, not one.**  The user's rotor is cooled through the
   shaft ("Ротор придётся охлаждать в основном через вал"), so the innermost
   closed boundary — the shaft bore when the shaft is a tube, the rotor inner
   radius when the shaft is excluded — is a first-class cooled surface with its
   own h and its own sink.

3. **The heat budget is REPORTED.**  Every surface comes back with its
   facet-integrated ∫h(T−T_sink)dA in watts, the gap bridge comes back with the
   watts crossing it rotor→stator, and the volume integral of q comes back as the
   heat that went in.  Without those three numbers "the rotor runs at 180 °C" is
   an assertion; with them it is an answer that closes.

4. **A LUMPED VOLUME SINK, for the one AXIAL path that is real.**  The user's
   ruling (2026-09-07): *"торцы и лобовые части — только для вала, всё остальное
   вращается внутри мотора"* — the rotor's end faces and the end windings turn
   inside a CLOSED housing and have nowhere to send their heat, so there is
   nothing to model there; the SHAFT sticks out through the bearings and its
   exposed length loses heat to the room.  ``volume_sinks`` is how a 2-D
   cross-section carries that: one conductance in W/K, spread over the elements
   of the named domains, reported as watts of its own.

PHYSICS NOTES
- Pure 2-D in-plane conduction → the temperature field is independent of stack
  length (both K and f scale with it).  Axial heat paths that stay INSIDE the
  housing are lumped into the surface convection coefficients; the shaft's
  exposed stubs, which do not, are a ``volume_sinks`` entry.  Heat FLOWS, which
  do not cancel, are per unit depth internally and multiplied by ``length_m``
  once, at the end.
- Steady state: gives the equilibrium temperature map for a constant loss set.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

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
    rotor_outer_m: float,       # rotor OD [m] INCLUDING any sleeve — bridge inner ring
    stator_inner_m: float,      # stator bore [m] — gap-bridge outer ring
    gap_k: float,               # effective air-gap conductivity [W/m·K]
    h_conv: float = 0.0,        # legacy single-surface convection coefficient
    t_ambient: float = 25.0,    # legacy single-surface sink [°C]
    surfaces: Optional[Sequence[Dict[str, Any]]] = None,
    k_radial_elem: Optional[np.ndarray] = None,   # (n_elem,) radial k, or None
    length_m: float = 1.0,      # stack length [m] — scales the reported WATTS
    slot_ins_k: float = 0.14,   # slot-liner conductivity [W/m·K] — stator-side islands
    slot_ins_d_m: float = 2.5e-4,  # slot-liner thickness [m]
    slip_r_m: float = 0.0,      # sliding-band radius [m] — 0 = no slip-line tie
    coil_mask: Optional[np.ndarray] = None,   # (n_elem,) bool — the winding
    volume_sinks: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Return the solved sub-mesh, its temperature field and its heat budget.

    ``surfaces`` is the cooled-surface list; each entry is
    ``{"name": "outer"|"bore", "h": W/m²·K, "t_sink": °C}``.  Omitting it falls
    back to the legacy single outer surface built from ``h_conv`` / ``t_ambient``,
    so every caller that predates the bore surface keeps working unchanged.

    ``k_radial_elem`` is the per-element RADIAL conductivity; ``k_elem`` is then
    read as the CIRCUMFERENTIAL one.  Pass ``None`` (the default) for a fully
    isotropic machine and ``k_elem`` is used for both.

    ``slip_r_m`` (2026-09-07) is the sliding-band radius.  Since the gap AIR is
    now a meshed domain of its own (``routes.thermal`` keeps and re-tags it
    instead of dropping it), the rotor is no longer an island floating in a
    hole — it is connected to the stator through real elements carrying the
    Taylor–Couette k_eff.  What is left at the slip radius is a NON-CONFORMING
    interface: the rotor half and the stator half of the sliding band carry
    duplicate nodes there.  Welding them by coordinate would fuse the two sides
    into one body and leave no place to MEASURE the heat crossing the gap, so
    the weld is made side-aware at that radius and the two node sets are joined
    by an explicit TIE instead — a contact conductance of ``k_gap/1e-4 m`` per
    unit length, three orders above the gap's own ~k/0.6 mm, so the tie is
    thermally transparent and the resistance stays where the physics is (in the
    gap elements).  ``gap_heat_W`` is then literally the watts crossing that
    tie.  Pass 0 to keep the old behaviour (weld everything, bridge the islands).

    ``coil_mask`` marks the winding elements.  When given, ``coil_boundary_W``
    comes back: the net conductive heat leaving the coil domain, integrated
    facet by facet over its boundary.  With the liner and the wire coating meshed
    there are no coil ISLANDS left to bridge, so the old ``slot_bridge_W`` goes
    to zero and this integral is what replaces it — and, unlike the bridge, it
    is a CHECK rather than a model: in steady state it must equal the copper
    loss put into those same elements.

    ``volume_sinks`` (2026-09-07) are LUMPED linear sinks spread over the
    elements of the named tags — the way a 2-D cross-section models a heat path
    that leaves along the THIRD dimension.  The one that motivated it is the
    shaft: the rotor's end faces and the end windings spin inside a closed
    housing and have nowhere to send their heat, but the shaft comes out through
    the bearings on both sides and the exposed stubs lose heat to the room
    (user 2026-09-07: *"торцы и лобовые части — только для вала, всё остальное
    вращается внутри мотора"*).  Each entry is

        ``{"tags": [...], "G_W_per_K": float, "t_sink_c": float,
           "name": str, "symmetry_mult": int}``

    and adds ``∫ g·u·v dV`` to K and ``∫ g·T_sink·v dV`` to f, with

        ``g = G / (V_tags · L · symmetry_mult)``   [W/(K·m³)]

    where ``V_tags`` is the tagged elements' AREA in this (possibly wedge) mesh.
    THE BOOKKEEPING IS THE POINT: ``G_W_per_K`` is a WHOLE-MACHINE conductance —
    it comes from the real shaft's real diameter and its real exposed length, and
    knows nothing about symmetry — while the mesh may be a 1/N wedge carrying
    1/N of the shaft.  Dividing by ``symmetry_mult`` puts the wedge's share on
    the wedge, so ``sinks[i]["heat_removed_W"] × symmetry_mult`` is exactly
    ``G·(t_mean_c − t_sink_c)`` in machine watts and the caller's budget closes
    with the same ``× symmetry_mult`` it applies to every other flow here.  A
    sink whose tags match no element is reported with zero watts rather than
    silently doing nothing.

    Result keys, beyond the field itself: ``surfaces`` (per-surface h, sink,
    area, facet count, ``heat_removed_W`` and ``t_mean_c``), ``sinks`` (per-sink
    ``name``, ``G_W_per_K``, ``t_sink_c``, ``heat_removed_W``, ``t_mean_c``),
    ``gap_heat_W`` (rotor → stator across the slip tie, or across the bridge
    when there is none; positive when the rotor is the hotter side),
    ``q_generated_W`` (∫q dV over the SOLVED sub-mesh) and ``heat_residual_W``.
    ``heat_removed_W`` is the sum of the surfaces AND the sinks — everything
    that actually leaves the model — so the residual stays the solve's own
    closure error.

    ``surfaces[i]["t_mean_c"]`` (2026-09-14) is the AREA-MEAN WALL TEMPERATURE of
    that surface, ∫T dA / A over the facets the film acts on — the surface-side
    twin of the sinks' ``t_mean_c``, and it makes the same identity checkable::

        heat_removed_W == h_conv · area_m2 · (t_mean_c − t_sink_c)

    exactly (both sides come out of the SAME facet integral, so it holds to
    round-off, not to a tolerance), and it is ``None`` on a surface with no
    facets.  It is what a temperature-dependent film has to iterate on: still-air
    natural convection goes as ΔT^(1/4) and the linearised radiation coefficient
    re-linearises about the wall, so the caller re-evaluates h from this number
    and re-solves.  Every mode that predates it keeps a constant h and simply
    ignores it.
    """
    from scipy.sparse.linalg import spsolve
    from skfem import (Basis, BilinearForm, ElementTriP0, ElementTriP1,
                       FacetBasis, Functional, LinearForm, MeshTri)
    from skfem.helpers import dot, grad

    P_m = np.asarray(P_m, float)
    tri = np.asarray(tri, int)
    cell_tags = np.asarray(cell_tags, int)
    k_elem = np.asarray(k_elem, float)
    q_elem = np.asarray(q_elem, float)
    k_rad = (k_elem if k_radial_elem is None
             else np.asarray(k_radial_elem, float))
    if k_rad.shape != k_elem.shape:
        raise ValueError(
            "k_radial_elem must have one value per element (%d), got %d"
            % (k_elem.size, k_rad.size))
    L = max(float(length_m), 1e-9)

    # ── 1. drop the far-field outer-air elements → solid sub-mesh ──────────────
    drop = np.isin(cell_tags, np.asarray(list(drop_tags), int))
    keep = ~drop
    t_keep = tri[:, keep]
    if t_keep.size == 0:
        raise ValueError("no solid elements left after dropping the air domains "
                         "— there is nothing to conduct heat through")
    k_keep = k_elem[keep]
    kr_keep = k_rad[keep]
    q_keep = q_elem[keep]
    tags_keep = cell_tags[keep]
    cm_all = (np.zeros(cell_tags.size, bool) if coil_mask is None
              else np.asarray(coil_mask, bool))
    if cm_all.shape != cell_tags.shape:
        raise ValueError(
            "coil_mask must have one flag per element (%d), got %d"
            % (cell_tags.size, cm_all.size))
    cm_keep = cm_all[keep]

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
    #
    # SIDE-AWARE at the slip radius (2026-09-07).  A blind weld also fuses the
    # two halves of the sliding band into one body, and then there is no surface
    # left at which to ask how much heat crossed the gap — the number the whole
    # rotor-cooling question turns on.  So a node ON the slip circle carries its
    # side (1 = rotor half, 2 = stator half) in the weld key and only welds to
    # its own side; the two sides are joined below by an explicit tie whose flow
    # IS `gap_heat_W`.  Every other node is unaffected (side 0).
    keyc = np.round(p_sub.T * 1e6).astype(np.int64)        # (n, 2) micron grid
    slip_r = max(float(slip_r_m or 0.0), 0.0)
    side = np.zeros(p_sub.shape[1], np.int64)
    if slip_r > 0.0:
        _ec = p_sub[:, t_sub].mean(axis=1)                  # (2, m) centroids
        _rot_el = np.hypot(_ec[0], _ec[1]) < slip_r
        _rn = np.hypot(p_sub[0], p_sub[1])
        _on_slip = np.abs(_rn - slip_r) <= max(1e-9, 1e-4 * slip_r)
        _has_rot = np.zeros(p_sub.shape[1], bool)
        _has_sta = np.zeros(p_sub.shape[1], bool)
        for _i in range(3):
            _has_rot[t_sub[_i][_rot_el]] = True
            _has_sta[t_sub[_i][~_rot_el]] = True
        # A node used by BOTH sides is already conforming there — leave it at 0
        # so it welds normally and the tie simply finds nothing to do.
        side = np.where(_on_slip & _has_rot & ~_has_sta, 1,
                        np.where(_on_slip & _has_sta & ~_has_rot, 2,
                                 0)).astype(np.int64)
    keyc = np.hstack([keyc, side[:, None]])
    _, first, inv = np.unique(keyc, axis=0, return_index=True, return_inverse=True)
    p_weld = p_sub[:, first]                                # welded node coords
    side_w = side[first]                                    # side per welded node
    t_weld = inv[t_sub]                                     # remap elements
    # drop any triangle that collapsed (two welded nodes coincide)
    nondegen = ((t_weld[0] != t_weld[1]) & (t_weld[1] != t_weld[2]) & (t_weld[0] != t_weld[2]))
    t_weld = t_weld[:, nondegen]
    k_keep = k_keep[nondegen]; kr_keep = kr_keep[nondegen]
    q_keep = q_keep[nondegen]; tags_keep = tags_keep[nondegen]
    cm_keep = cm_keep[nondegen]

    mesh = MeshTri(p_weld.copy(), t_weld.copy())
    p_sub = p_weld; t_sub = t_weld
    # MeshTri may reorder elements internally — realign per-element fields to it.
    order = _match_element_order(mesh, t_sub)
    k_ord = k_keep[order]; kr_ord = kr_keep[order]
    q_ord = q_keep[order]; tags_ord = tags_keep[order]
    cm_ord = cm_keep[order]

    basis = Basis(mesh, ElementTriP1())
    p0 = Basis(mesh, ElementTriP0())
    # Element areas, in the MESH's element order — computed once here because
    # three different readers want them: the volume sinks below (to spread a
    # lumped conductance over a domain), ∫q dV at the end, and the rotor's share
    # of it.  Three copies of the same cross product is three chances to have
    # one of them stale after a re-order.
    _pe = mesh.p[:, mesh.t]
    _v1 = _pe[:, 1, :] - _pe[:, 0, :]
    _v2 = _pe[:, 2, :] - _pe[:, 0, :]
    a_elem = 0.5 * np.abs(_v1[0] * _v2[1] - _v1[1] * _v2[0])
    kt_field = p0.interpolate(k_ord)            # circumferential / isotropic
    kr_field = p0.interpolate(kr_ord)           # radial (through-thickness)
    q_field = p0.interpolate(q_ord)

    @BilinearForm
    def conduction(u, v, w):
        """K = k_r·r̂r̂ᵀ + k_t·(I − r̂r̂ᵀ), evaluated in the local (r, θ) frame.

        Written as ``k_t·∇u·∇v + (k_r − k_t)·(∇u·r̂)(∇v·r̂)`` so an isotropic
        element (k_r = k_t) reduces to the scalar form EXACTLY — same code, no
        branch, no chance of the anisotropic path silently drifting.
        """
        x, y = w.x[0], w.x[1]
        r = np.sqrt(np.maximum(x * x + y * y, 1e-24))
        rx, ry = x / r, y / r
        gu, gv = grad(u), grad(v)
        gur = gu[0] * rx + gu[1] * ry
        gvr = gv[0] * rx + gv[1] * ry
        return w["kt"] * dot(gu, gv) + (w["kr"] - w["kt"]) * gur * gvr

    @LinearForm
    def heat_source(v, w):
        return w["q"] * v

    K = conduction.assemble(basis, kt=kt_field, kr=kr_field)
    f = heat_source.assemble(basis, q=q_field)

    # ── 2. Robin (convection) BCs, one FacetBasis per cooled surface ───────────
    bnd = mesh.boundary_facets()
    cut_sel = np.array([], int)
    fnodes = mesh.facets[:, bnd]                          # (2, n_bnd)
    r_node = np.hypot(mesh.p[0], mesh.p[1])
    r_fmax = r_node[fnodes].max(axis=0)                   # outer end of each facet
    r_fmin = r_node[fnodes].min(axis=0)

    # OUTER: the housing ring.  Same rule as before (r > 0.9·r_housing on the
    # facet midpoint), with the same fallback to "whatever is outermost" for a
    # cross-section whose housing radius the caller could not name.
    fmid_r = 0.5 * (r_fmax + r_fmin)
    outer_sel = bnd[fmid_r > 0.9 * float(r_housing_m)]
    if outer_sel.size == 0:
        outer_sel = bnd[fmid_r > 0.9 * fmid_r.max()] if bnd.size else bnd
    # …MINUS the two radial CUT lines of a symmetry wedge (2026-09-09).  Their
    # outer 10 % passed the midpoint rule, so on the G2's 90° quarter the yoke
    # at both cuts was pinned at the coolant temperature and the two edge slots
    # ran 3 K cooler than the four between them — a boundary condition, not a
    # machine (user: "меня пугает неравномерность, проверь граничные условия").
    # A cut is a symmetry plane: adiabatic, nothing crosses it.  The housing's
    # own groove walls are radial too but sit INSIDE the angular span, so they
    # keep their film — see ``wedge_cut_facets``.
    cut_sel = wedge_cut_facets(mesh, bnd)
    if cut_sel.size:
        outer_sel = np.setdiff1d(outer_sel, cut_sel)

    # BORE: the innermost CLOSED boundary of the solid mesh.  Selected by node
    # radius rather than by tag on purpose — it is the shaft bore when the shaft
    # is a tube, the rotor inner radius when the shaft is excluded from the
    # model, and neither of those is a domain the mesher labels.  Only facets
    # whose BOTH ends sit on that radius qualify, which is what keeps the two
    # radial cut lines of a symmetry wedge out of the set.
    r_inner = float(r_fmax.min()) if bnd.size else 0.0
    bore_sel = np.array([], int)
    if r_inner > max(1e-5, 0.02 * float(r_housing_m)):
        bore_mask = (r_fmax <= r_inner * 1.02 + 1e-6)
        bore_sel = bnd[bore_mask]
        bore_sel = np.setdiff1d(bore_sel, outer_sel)
        # The 2 % band is 1 mm on a 50 mm bore — one mesh cell — so the FIRST
        # radial facet of each cut line sat inside it and carried the bore film
        # too: ~0.5 W per cut on the G2, which is exactly the 2 K hump with
        # cool edges the rotor showed across its quarter after the housing cut
        # was fixed (user 2026-09-09: "в роторе та же неравномерность и
        # осталась").  A cut is adiabatic on every film.
        if cut_sel.size:
            bore_sel = np.setdiff1d(bore_sel, cut_sel)
    else:
        r_inner = 0.0                       # solid to the axis: there is no bore

    spec: List[Dict[str, Any]] = list(surfaces or [
        {"name": "outer", "h": float(h_conv), "t_sink": float(t_ambient)}])
    facet_sets = {"outer": outer_sel, "bore": bore_sel}

    active: List[Dict[str, Any]] = []
    for s in spec:
        name = str(s.get("name", "outer"))
        h = float(s.get("h", 0.0) or 0.0)
        ts = float(s.get("t_sink", t_ambient))
        fset = facet_sets.get(name, np.array([], int))
        if h <= 0.0 or fset.size == 0:
            active.append({"name": name, "h": h, "t_sink": ts, "facets": fset,
                           "basis": None})
            continue
        fb = FacetBasis(mesh, ElementTriP1(), facets=fset)
        robin, robin_rhs = _robin_forms(h, ts)
        K = K + robin.assemble(fb)
        f = f + robin_rhs.assemble(fb)
        active.append({"name": name, "h": h, "t_sink": ts, "facets": fset,
                       "basis": fb})

    # ── 2a'. VOLUME SINKS: a heat path that leaves along the third dimension ──
    # See the docstring.  A lumped G [W/K] between the mean temperature of a set
    # of domains and a sink, spread UNIFORMLY over those elements as a mass-type
    # form — which is what "lumped" means: the model has no information about
    # WHERE inside the shaft the axial heat leaves from, and inventing a
    # distribution would be exactly the guesswork this codebase refuses
    # elsewhere.  The sink is linear in T, so it goes into K (not into f alone)
    # and the fixed point is solved, not iterated.
    sinks_active: List[Dict[str, Any]] = []
    for s in list(volume_sinks or ()):
        name = str(s.get("name", "sink"))
        g_tot = float(s.get("G_W_per_K", 0.0) or 0.0)
        ts = float(s.get("t_sink_c", t_ambient))
        sym_s = max(int(s.get("symmetry_mult", 1) or 1), 1)
        want = np.asarray(list(s.get("tags") or ()), int)
        mask = np.isin(tags_ord, want) if want.size else np.zeros(tags_ord.size, bool)
        # ``r_range_m`` (2026-09-21) narrows the sink to the elements whose
        # CENTROID radius lies in [r_min, r_max).  One thing needed it: the open
        # frame's gap through-flow, where the same air-gap domain carries two
        # streams — the rotor half of the clearance and the stator half, split
        # at the slip radius — and each has to be reported on its own side of
        # the machine's heat budget or neither the rotor nor the stator split
        # closes.  Tags alone cannot say that: the two halves are one domain.
        _rr = s.get("r_range_m")
        if _rr is not None and mask.any():
            _r0 = float(_rr[0] if _rr[0] is not None else 0.0)
            _r1 = float(_rr[1] if _rr[1] is not None else np.inf)
            _rc = np.hypot(p_sub[0][t_sub].mean(axis=0),
                           p_sub[1][t_sub].mean(axis=0))
            mask = mask & (_rc >= _r0) & (_rc < _r1)
        a_tags = float(a_elem[mask].sum()) if mask.any() else 0.0
        if g_tot <= 0.0 or a_tags <= 0.0:
            sinks_active.append({"name": name, "G_W_per_K": g_tot, "t_sink": ts,
                                 "mask": mask, "g": 0.0, "area": a_tags,
                                 "symmetry_mult": sym_s})
            continue
        # g·(A_tags·L·sym) = G  →  the WEDGE carries G/sym, which is its share.
        g_vol = g_tot / (a_tags * L * sym_s)
        g_elem = np.where(mask, g_vol, 0.0)
        mass, mass_rhs = _volume_sink_forms(ts)
        g_field = p0.interpolate(g_elem)
        K = K + mass.assemble(basis, g=g_field)
        f = f + mass_rhs.assemble(basis, g=g_field)
        sinks_active.append({"name": name, "G_W_per_K": g_tot, "t_sink": ts,
                             "mask": mask, "g": g_vol, "area": a_tags,
                             "symmetry_mult": sym_s})
    K = K.tolil()

    if not (any(a["basis"] is not None for a in active)
            or any(s["g"] > 0.0 for s in sinks_active)):
        raise ValueError(
            "no cooled surface reached the conduction solve: with every "
            "boundary adiabatic the steady problem has no solution (the machine "
            "would heat up for ever).  Cool the housing (cooling_mode) or the "
            "bore (bore_mode).")

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

    # ── 2a. the SLIP-LINE TIE ────────────────────────────────────────────────
    # The one interface the mesh genuinely does not close: the rotor half and
    # the stator half of the sliding band meet at `slip_r` with duplicate nodes.
    # Tie them pairwise with a CONTACT conductance k_gap/D_TIE per unit length —
    # D_TIE = 0.1 mm against a mechanical clearance of 0.6 mm on the 200 mm
    # machine, i.e. six times the gap's own conductance, so the tie adds ~15 %
    # of nothing to a path whose resistance now lives in the meshed gap air.  It
    # is a measurement surface first and a connection second: `gap_heat_W` is
    # the sum of what flows through these links, which is what the rotor's heat
    # split (gap vs bore) is read off.
    D_TIE_M = 1.0e-4
    tie_a: List[int] = []
    tie_b: List[int] = []
    tie_g: List[float] = []
    if slip_r > 0.0:
        rot_n = np.where(side_w == 1)[0]
        sta_n = np.where(side_w == 2)[0]
        if rot_n.size and sta_n.size:
            _tt = _KDTree(p_sub[:, sta_n].T)
            _, _j = _tt.query(p_sub[:, rot_n].T)
            g_tie = (max(float(gap_k), 1e-9) * (2.0 * np.pi * slip_r / rot_n.size)
                     / D_TIE_M)
            for _i, _a in enumerate(rot_n):
                _b = int(sta_n[int(_j[_i])])
                tie_a.append(int(_a)); tie_b.append(_b); tie_g.append(g_tie)
    if tie_a:
        # Scalar loop, not fancy indexing: several rotor nodes can share one
        # nearest stator node, and `K[rows, cols] += v` on a duplicated index
        # applies the value ONCE — which would silently drop most of the tie.
        for _a, _b, _g in zip(tie_a, tie_b, tie_g):
            K[_a, _a] += _g; K[_b, _b] += _g
            K[_a, _b] -= _g; K[_b, _a] -= _g
        _ta = np.asarray(tie_a, int); _tb = np.asarray(tie_b, int)
        # The tie is a real connection, so the island search must see it — or
        # the rotor is reported as a floating island and bridged a SECOND time.
        edges = np.hstack([edges, np.vstack([_ta, _tb])])

    adj = _coo((np.ones(edges.shape[1]), (edges[0], edges[1])), shape=(n_nodes, n_nodes))
    n_comp, labels = _cc(adj + adj.T, directed=False)
    bnodes = np.unique(mesh.facets[:, outer_sel]) if outer_sel.size else np.array([], int)
    main_lbl = int(np.bincount(labels[bnodes]).argmax()) if bnodes.size else int(np.bincount(labels).argmax())
    all_bnd = np.unique(mesh.facets[:, mesh.boundary_facets()])
    # The gap the bridge spans is the MECHANICAL clearance: stator bore minus the
    # rotor's true outside diameter, sleeve included.  Reading it off the iron OD
    # (what this used to get) over-reads the gap by the sleeve thickness, and the
    # bridge conductance is inversely proportional to it.
    gap_d = max(float(stator_inner_m) - float(rotor_outer_m), 1e-5)
    n_bridge = 0
    link_a: List[int] = []
    link_b: List[int] = []
    link_g: List[float] = []
    link_rotor: List[bool] = []          # True = a rotor-side (gap) link
    n_isl_rotor = n_isl_stator = 0
    if n_comp > 1:
        main_nodes = np.where(labels == main_lbl)[0]
        tree = _KDTree(p_sub[:, main_nodes].T)
        bset = set(all_bnd.tolist())
        # Boundary facet lengths, for the perimeter of a stator-side island.
        _bf = mesh.facets[:, mesh.boundary_facets()]
        _bl = np.hypot(p_sub[0, _bf[0]] - p_sub[0, _bf[1]],
                       p_sub[1, _bf[0]] - p_sub[1, _bf[1]])
        for c in range(n_comp):
            if c == main_lbl:
                continue
            cn = np.where(labels == c)[0]
            cb = np.array([x for x in cn if x in bset], int)
            if cb.size == 0:
                cb = cn
            _, idx = tree.query(p_sub[:, cb].T)
            r_c = float(np.hypot(p_sub[0, cb], p_sub[1, cb]).mean())
            # WHICH side of the gap is this island on?  Dropping the air leaves
            # TWO kinds of island: the rotor (with its magnets, sleeve and shaft)
            # — separated from the stator by the mechanical gap — and every COIL
            # block, separated from the tooth walls by the slot air and the
            # liner the EM mesh does not draw.  They used to get the same bridge
            # (gap air over the gap width), so a winding sat on a 0.6 mm film of
            # air instead of its 0.25 mm liner, and the coils' 3 kW were summed
            # into "heat crossing the gap" (measured 2026-09-07: 3.3 kW reported
            # for a rotor that makes 0.77 kW).  A stator-side island is bridged
            # through the LINER over its own perimeter; only rotor-side links
            # count as gap heat.
            rotor_side = r_c < float(stator_inner_m)
            if rotor_side:
                n_isl_rotor += 1
                g_total = float(gap_k) * (2.0 * np.pi * max(r_c, 1e-4)) / gap_d
            else:
                n_isl_stator += 1
                _cs = set(cn.tolist())
                _in = np.array([(int(a) in _cs) and (int(b) in _cs)
                                for a, b in zip(_bf[0], _bf[1])], bool)
                perim = float(_bl[_in].sum()) if _in.any() else 2.0 * np.pi * r_c
                g_total = float(slot_ins_k) * max(perim, 1e-6) / max(float(slot_ins_d_m), 1e-6)
            g_link = g_total / cb.size
            for j, a in enumerate(cb):
                a = int(a); b = int(main_nodes[int(idx[j])])
                K[a, a] += g_link; K[b, b] += g_link
                K[a, b] -= g_link; K[b, a] -= g_link
                link_a.append(a); link_b.append(b); link_g.append(g_link)
                link_rotor.append(rotor_side)
            n_bridge += int(cb.size)

    # ── 3. solve  K · T = f  (Robin BC makes K SPD, no Dirichlet needed) ───────
    T = np.asarray(spsolve(K.tocsr(), f), float)
    n_bad = int((~np.isfinite(T)).sum())          # >0 ⇒ a still-disconnected island
    if n_bad:
        T = np.nan_to_num(T, nan=float(t_ambient),
                          posinf=float(t_ambient), neginf=float(t_ambient))

    # ── 4. per-element heat flux  q = -K ∇T  (the same tensor as the form) ─────
    gradT = basis.interpolate(T).grad            # (2, n_elem, n_qp)
    gT = gradT.mean(axis=2)                       # element-mean gradient (2, m)
    cx = mesh.p[0][mesh.t].mean(axis=0)
    cy = mesh.p[1][mesh.t].mean(axis=0)
    cr = np.sqrt(np.maximum(cx * cx + cy * cy, 1e-24))
    rx, ry = cx / cr, cy / cr
    g_r = gT[0] * rx + gT[1] * ry                 # radial component of ∇T
    flux = -(k_ord[None, :] * gT
             + (kr_ord - k_ord)[None, :] * g_r[None, :] * np.vstack([rx, ry]))
    flux = np.nan_to_num(flux)                    # (2, m)  [W/m²]
    flux_mag = np.hypot(flux[0], flux[1])
    # |∇T| per element [K/m] — WHERE the temperature drops fastest is where the
    # heat path is worst (user 2026-09-07: "график градиента температуры, чтобы
    # понять, где самые плохие места для теплопередачи").  The flux says how
    # much heat passes; the gradient says how much it costs in kelvin per metre
    # to pass it — the liner, the enamel, the gap and the sleeve light up here.
    grad_mag = np.nan_to_num(np.hypot(gT[0], gT[1]))

    # ── 5. the heat budget ────────────────────────────────────────────────────
    # Everything above is per METRE of stack (both K and f scale with the depth),
    # so the flows are multiplied by `length_m` exactly once, here.
    @Functional
    def _facet_area(w):
        return 1.0 + 0.0 * w.x[0]

    @Functional
    def _facet_temp(w):
        return w["T"]

    surf_out: List[Dict[str, Any]] = []
    total_removed = 0.0
    for a in active:
        fb = a["basis"]
        if fb is None:
            surf_out.append({"name": a["name"], "h_conv": a["h"],
                             "t_sink_c": a["t_sink"], "area_m2": 0.0,
                             "heat_removed_W": 0.0, "t_mean_c": None,
                             "n_facets": 0})
            continue
        area_pm = float(_facet_area.assemble(fb))                  # m²/m
        t_int = float(_facet_temp.assemble(fb, T=fb.interpolate(T)))
        p_surf = a["h"] * (t_int - a["t_sink"] * area_pm) * L      # W
        total_removed += p_surf
        # The surface's AREA-MEAN WALL TEMPERATURE — ∫T dA / A over the same
        # facets the film acts on, from the same integral the watts come from, so
        # `heat_removed_W == h · area_m2 · (t_mean_c − t_sink_c)` is an identity
        # and not a reconciliation.  Two things need it (2026-09-14): the payload
        # can be CHECKED rather than trusted, the way the volume sinks below have
        # been checkable since they were written; and a film whose coefficient
        # depends on the wall temperature — still air, where h ∝ ΔT^(1/4) and
        # radiation re-linearises about the wall — has something to iterate on.
        # Before this the caller could only guess the wall from the global T_max,
        # which on a machine whose heat leaves through its mount is tens of
        # kelvin wrong and biased the expensive way (too hot, so too cooled).
        surf_out.append({"name": a["name"], "h_conv": a["h"],
                         "t_sink_c": a["t_sink"],
                         "area_m2": area_pm * L,
                         "heat_removed_W": p_surf,
                         "t_mean_c": (t_int / area_pm if area_pm > 0.0
                                      else None),
                         "n_facets": int(a["facets"].size)})

    # The volume sinks, measured the same way and in the same (wedge) watts.
    # ∫T dA over a P1 field is exact as area × the vertex mean, so the sink's
    # heat needs no quadrature of its own:  P = g·(∫T dA − T_sink·A)·L, and with
    # g = G/(A·L·sym) that is G·(T_mean − T_sink)/sym exactly.  Reporting
    # `t_mean_c` beside it is what makes that identity CHECKABLE from the
    # payload instead of trusted.
    sink_out: List[Dict[str, Any]] = []
    for s in sinks_active:
        m = s["mask"]
        if not m.any() or s["area"] <= 0.0:
            sink_out.append({"name": s["name"], "G_W_per_K": s["G_W_per_K"],
                             "t_sink_c": s["t_sink"], "heat_removed_W": 0.0,
                             "t_mean_c": None, "n_elements": 0,
                             "symmetry_mult": s["symmetry_mult"]})
            continue
        t_int = float((a_elem[m] * T[mesh.t[:, m]].mean(axis=0)).sum())
        t_mean = t_int / s["area"]
        p_sink = s["g"] * (t_int - s["t_sink"] * s["area"]) * L
        total_removed += p_sink
        sink_out.append({"name": s["name"], "G_W_per_K": s["G_W_per_K"],
                         "t_sink_c": s["t_sink"], "heat_removed_W": p_sink,
                         "t_mean_c": t_mean, "n_elements": int(m.sum()),
                         "symmetry_mult": s["symmetry_mult"]})

    # Heat crossing the gap bridge, rotor(island) → stator(main).  Internal to
    # the system, so it does NOT appear in the global balance — it is the number
    # that says how the rotor's own heat splits between the gap and the bore.
    gap_w = 0.0
    slot_w = 0.0
    if link_g:
        la = np.asarray(link_a, int); lb = np.asarray(link_b, int)
        lg = np.asarray(link_g, float); lr = np.asarray(link_rotor, bool)
        _flow = lg * (T[la] - T[lb]) * L
        gap_w = float(_flow[lr].sum())            # rotor → stator across the gap
        slot_w = float(_flow[~lr].sum())          # coil → tooth through the liner
    # With the gap air meshed there are no rotor islands to bridge, and the heat
    # crossing the gap is what crosses the slip tie.  The tie WINS over the
    # bridge number rather than being added to it — they are two models of the
    # same interface and only one of them ran.
    tie_w = 0.0
    if tie_a:
        _tg = np.asarray(tie_g, float)
        tie_w = float((_tg * (T[_ta] - T[_tb])).sum() * L)
        gap_w = tie_w

    # COIL → SLOT: the net conductive heat leaving the winding elements, walked
    # facet by facet round their boundary.  It replaces `slot_bridge_W`, which
    # measured a lumped link that no longer exists once the liner and the slot
    # fill are meshed — and it is a CHECK, not a model: in steady state it must
    # come back equal to the copper loss deposited in those same elements.
    coil_w = (_domain_boundary_flux(mesh.p, mesh.t, flux, cm_ord) * L
              if cm_ord.any() else 0.0)

    # ∫q dV over the SOLVED sub-mesh — the heat that actually went in, measured
    # on the same elements the solve used rather than trusted from upstream.
    # (`a_elem` was built up beside the bases — see the note there.)
    q_gen = float(np.sum(q_ord * a_elem)) * L
    # ...and the ROTOR SIDE's share of it.  Steady state fixes the rest: every
    # watt made inside the slip radius leaves either across the gap or through
    # the bore, so `q_rotor_W` is what `gap_heat_W + bore` has to add up to and
    # the only way to check the tie is measuring something and not just
    # reporting it.
    q_rotor = 0.0
    if slip_r > 0.0:
        _cr = np.hypot(mesh.p[0][mesh.t].mean(axis=0),
                       mesh.p[1][mesh.t].mean(axis=0))
        q_rotor = float(np.sum((q_ord * a_elem)[_cr < slip_r])) * L

    return {
        "vertices": p_sub.T.tolist(),             # (n, 2) metres
        "triangles": t_sub.T.tolist(),            # (m, 3)
        "cell_tags": tags_ord.tolist(),
        "T_node": T.tolist(),                     # (n,) °C
        "flux_elem": flux.T.tolist(),             # (m, 2) W/m²
        "flux_mag_elem": flux_mag.tolist(),
        "grad_mag_elem": grad_mag.tolist(),       # (m,) K/m
        "T_min": float(T.min()), "T_max": float(T.max()),
        "n_housing_facets": int(outer_sel.size),
        "n_bore_facets": int(bore_sel.size),
        # the wedge's cut facets, kept OUT of every film (adiabatic) — 0 on a
        # full disk (2026-09-09)
        "n_cut_facets": int(cut_sel.size),
        "r_bore_m": float(r_inner),
        "n_bridge_links": int(n_bridge),
        "n_islands_rotor": int(n_isl_rotor),
        "n_islands_stator": int(n_isl_stator),
        "slot_bridge_W": slot_w,
        "n_slip_ties": int(len(tie_a)),
        "slip_tie_W": float(tie_w),
        "coil_boundary_W": float(coil_w),
        "n_nonfinite": int(n_bad),
        "surfaces": surf_out,
        "sinks": sink_out,
        "gap_heat_W": gap_w,
        "q_generated_W": q_gen,
        "q_rotor_W": q_rotor,
        "heat_removed_W": total_removed,
        "heat_residual_W": q_gen - total_removed,
        "length_m": L,
    }


def wedge_cut_facets(mesh, bnd: np.ndarray, *, tol_rad: float = 2e-3) -> np.ndarray:
    """The boundary facets lying on the two radial CUT lines of a symmetry wedge.

    2026-09-09.  A sector mesh (a quarter, a half) has four kinds of boundary:
    the housing arc, the bore arc, and the two rays it was cut along.  The rays
    are symmetry planes — adiabatic, nothing crosses them — and the films must
    never be applied to them.  The housing rule selects facets by radius, so the
    outer 10 % of each ray used to be swept into the coolant film; on the G2's
    quarter that pinned the yoke at both cuts and left the two edge slots 3 K
    cooler than the four between them, which the user read as a machine that
    heats unevenly (user: "меня пугает неравномерность, проверь граничные
    условия").

    Recognised on the MESH, not from a parameter the solver does not have: the
    nodes' angular span is measured about their mean direction (so a wedge
    straddling −180° is handled), a span short of the full circle says "wedge",
    and a facet whose BOTH ends sit on the span's extreme angle is a cut facet.
    A groove wall in the housing is radial too, but it lies inside the span and
    is left alone.  A full disk (span ≈ 2π) returns nothing, so every full-disk
    answer is bit for bit what it was.
    """
    if bnd.size == 0:
        return np.array([], int)
    p = mesh.p
    r = np.hypot(p[0], p[1])
    th = np.arctan2(p[1], p[0])
    ok = r > 1e-9                       # the axis has no angle
    if not ok.any():
        return np.array([], int)
    c = np.exp(1j * th[ok]).sum()
    mean = float(np.angle(c)) if abs(c) > 1e-9 else 0.0
    d = np.angle(np.exp(1j * (th - mean)))          # wrapped to (-pi, pi]
    lo, hi = float(d[ok].min()), float(d[ok].max())
    if hi - lo > 2.0 * np.pi - 0.05:
        return np.array([], int)                    # a full disk: no cut
    fn = mesh.facets[:, bnd]
    d0, d1 = d[fn[0]], d[fn[1]]
    r0, r1 = r[fn[0]], r[fn[1]]
    on_lo = (np.abs(d0 - lo) < tol_rad) & (np.abs(d1 - lo) < tol_rad)
    on_hi = (np.abs(d0 - hi) < tol_rad) & (np.abs(d1 - hi) < tol_rad)
    # …and radial: the two ends at different radii (an arc facet that happens to
    # end on the cut angle is the housing's own, and it keeps its film)
    radial = np.abs(r0 - r1) > 0.25 * np.hypot(p[0][fn[0]] - p[0][fn[1]],
                                               p[1][fn[0]] - p[1][fn[1]])
    return bnd[(on_lo | on_hi) & radial]


def _domain_boundary_flux(p: np.ndarray, t: np.ndarray, flux: np.ndarray,
                          mask: np.ndarray) -> float:
    """∮ q·n dl over the boundary of the elements in ``mask`` [W per metre of stack].

    Positive = heat LEAVING the region.  Written as an explicit facet walk
    rather than a scikit-fem ``Functional`` because the surface wanted here is
    an INTERIOR one (the copper/insulation interface), and the element-constant
    flux is discontinuous across it: the honest reading is the one taken on the
    region's OWN side, which an interior-facet basis would have to be told
    anyway.

    Why it exists: until the slot air was meshed, the winding was a set of
    islands lumped onto the tooth wall through a liner conductance, and the
    watts on that link were the answer to "does the copper's heat actually get
    out through its insulation".  With the liner and the fill meshed the link is
    gone, and this integral is the same question asked of the real elements —
    and it is falsifiable, because in steady state it must equal the copper loss
    deposited inside the region.
    """
    m = int(t.shape[1])
    if m == 0 or not mask.any():
        return 0.0
    ends = np.hstack([t[[0, 1]], t[[1, 2]], t[[2, 0]]])       # (2, 3m)
    owner = np.tile(np.arange(m), 3)
    key = np.sort(ends, axis=0)
    _, inv = np.unique(key, axis=1, return_inverse=True)
    inv = np.asarray(inv).ravel()
    he_in = mask[owner]
    # An edge is on the region's boundary when exactly ONE of its (one or two)
    # half-edges belongs to the region: the mesh boundary counts, an interior
    # edge between two masked elements does not.
    n_in = np.bincount(inv, weights=he_in.astype(float), minlength=inv.max() + 1)
    sel = he_in & (n_in[inv] == 1.0)
    if not sel.any():
        return 0.0
    a = ends[0][sel]; b = ends[1][sel]; o = owner[sel]
    dx = p[0, b] - p[0, a]; dy = p[1, b] - p[1, a]
    ln = np.hypot(dx, dy)
    ln = np.where(ln > 1e-15, ln, 1e-15)
    nx, ny = dy / ln, -dx / ln                    # one of the two unit normals
    cx = p[0, t[:, o]].mean(axis=0); cy = p[1, t[:, o]].mean(axis=0)
    mx = 0.5 * (p[0, a] + p[0, b]); my = 0.5 * (p[1, a] + p[1, b])
    sgn = np.sign((mx - cx) * nx + (my - cy) * ny)   # make it point OUTWARD
    sgn = np.where(sgn == 0.0, 1.0, sgn)
    return float(np.sum((flux[0, o] * nx + flux[1, o] * ny) * sgn * ln))


def _robin_forms(h: float, t_sink: float):
    """(bilinear, linear) convection forms for ONE surface, closed over its own
    h and sink.

    A closure rather than default arguments on the form body: scikit-fem
    inspects the decorated function's signature, so an extra ``_h=h`` parameter
    is a change to the form's contract and not a private detail.  Building the
    pair per surface is what lets the outer housing and the rotor bore carry
    different coefficients into ONE stiffness matrix.
    """
    from skfem import BilinearForm, LinearForm

    @BilinearForm
    def robin(u, v, w):
        return h * u * v

    @LinearForm
    def robin_rhs(v, w):
        return h * t_sink * v

    return robin, robin_rhs


def _volume_sink_forms(t_sink: float):
    """(bilinear, linear) forms for ONE lumped volume sink, closed over its sink.

    The mass-type pair ``∫ g·u·v dV`` / ``∫ g·T_sink·v dV``: the same shape as
    the Robin pair one dimension up, and for the same reason — the sink is
    LINEAR in T, so it belongs in the stiffness matrix and the equilibrium is
    solved rather than iterated.  ``g`` arrives as a per-element field so one
    form serves any set of tags; the sink temperature is a closure for the same
    reason ``_robin_forms`` uses one (scikit-fem reads the decorated function's
    signature, so an extra parameter would be a change to the form's contract).
    """
    from skfem import BilinearForm, LinearForm

    @BilinearForm
    def mass(u, v, w):
        return w["g"] * u * v

    @LinearForm
    def mass_rhs(v, w):
        return w["g"] * t_sink * v

    return mass, mass_rhs


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
