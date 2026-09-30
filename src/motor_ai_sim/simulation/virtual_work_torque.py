"""Coulomb's virtual-work torque (local Jacobian derivative, J.L. Coulomb 1983).

The torque on the rotor is the derivative of the magnetic co-energy with
respect to the rotor angle at constant current,

    T = dW'/dθ |_i .

In the finite-element A-formulation the discrete solution A minimises the
functional  F(A, θ) = W(A, θ) − fᵀA  (W: stored energy, f: current and magnet
sources), and  min_A F = −W'.  By the envelope theorem

    dW'/dθ = −∂F/∂θ |_A          (nodal A held fixed),

so ONE solve gives the torque: virtually displace the nodes of a layer of
elements and differentiate the element integrals with the nodal potentials
frozen.  Only elements whose shape changes contribute.  If the displaced
elements are all AIR (constant ν = ν0, no current, no magnetisation, no
conductivity), the source term fᵀA does not depend on θ and the energy density
is exactly quadratic, so no ∂ν/∂B term is ever needed — which is why the
layer must be pure air.  Every element outside the layer either does not move
(stator side) or moves rigidly with the rotor (its energy, its magnet source
and its current source are then rotation-invariant), and contributes nothing.

Formula (derived in docs/COULOMB_TORQUE_2026-09-30.md).  Let the virtual
displacement per mechanical radian be u(x) = φ(x)·(−y, x), with φ = 1 on the
rotor side of the layer and φ = 0 on its stator side, interpolated with the
vertex (P1) shape functions.  With the element Jacobian J = ∂x/∂ξ:

    ∂J/∂s = D·J,   D = ∇u = Σ_k u_k ⊗ ∇N_k       (constant per element),
    ∂(∇A)/∂s = −Dᵀ ∇A,     ∂|J|/∂s = |J| tr D,

hence, for one element with ν = ν0 and stack length L,

    T_e = L ν0 ∫_e [ ∇A·(D ∇A) − ½ |∇A|² tr D ] dΩ
        = L ν0 ∫_e [ a (A_x² − A_y²) + 2 b A_x A_y ] dΩ,
    a = (D_xx − D_yy)/2,   b = (D_xy + D_yx)/2 .

(Only the symmetric, trace-free part of D survives: a rigid rotation has
a = b = 0 and contributes exactly zero.)  In polar form with φ = φ(r) this is
L ∫ r B_r B_θ (−dφ/dr)/μ0 dΩ — the Arkkio integral is the continuous special
case with a linear φ(r); the difference is that here the weight is the P1
interpolant of φ on the ACTUAL mesh, so the result is the exact derivative of
the discrete co-energy along a mesh deformation.

Element types.  The solver's meshes are straight-sided (``MeshTri``): the
geometry map is AFFINE for both P1 and P2 fields, so J (and D) are constant per
element and a P2 element's mid-side nodes stay at their edge midpoints — they
move by the average of their two vertices, i.e. by half when only one vertex
moves.  ∇A is taken from the supplied field's own basis (linear per element
for P2, constant for P1) and integrated with that basis' quadrature, which is
exact for the quadratic (P2) and constant (P1) integrands.

Layer independence.  In the continuum the torque does not depend on φ; on a
mesh the choices differ by discretisation error only.  ``sliding_band_layers``
therefore returns two disjoint layers — the rotor-side air ring (rotor metal to
the slip circle) and the stator-side air ring (slip circle to the stator bore)
— whose difference is the built-in self-check.  Their average is the Coulomb
torque of the averaged field φ̄ = (φ_r + φ_s)/2, which spans the whole gap.
Neither layer is the sliding interface itself: the slip circle is a boundary
of both, never inside one.

Pure numpy + scikit-fem.  No solver state.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, Optional, Sequence

import numpy as np

MU0 = 4e-7 * math.pi
NU0 = 1.0 / MU0


#: Reported-torque methods.  "coulomb" is the default since 2026-09-30 (owner,
#: after docs/COULOMB_TORQUE_2026-09-30.md); "hybrid_maxwell_ac" is the earlier
#: energy / terminal-work mean + raw Maxwell AC, still selectable.
TORQUE_METHODS = ("coulomb", "hybrid_maxwell_ac")
DEFAULT_TORQUE_METHOD = "coulomb"


def resolve_torque_method(requested: Optional[str] = None,
                          sim_cfg: Optional[Dict[str, object]] = None) -> str:
    """The reported-torque method of a run: the request, else the config's
    ``simulation.torque_method``, else :data:`DEFAULT_TORQUE_METHOD`.
    Raises ``ValueError`` on an unknown name (never a silent substitute)."""
    m = str(requested or (sim_cfg or {}).get("torque_method")
            or DEFAULT_TORQUE_METHOD)
    if m not in TORQUE_METHODS:
        raise ValueError("torque_method must be one of %s; got %r"
                         % (", ".join(repr(x) for x in TORQUE_METHODS), m))
    return m


class CoulombLayerError(ValueError):
    """The requested layer would deform an element that is not pure air."""


@dataclass
class CoulombLayer:
    """One virtual-displacement layer.

    ``elements``  indices (into the mesh's element list) of the DEFORMED
                  elements, i.e. those whose vertex φ values differ;
    ``phi``       φ at every mesh vertex (1 rotor side, 0 stator side);
    ``a``, ``b``  the trace-free symmetric part of D = ∇u per layer element
                  [1/rad], see the module docstring;
    ``name``      a label for the record.
    """

    elements: np.ndarray
    phi: np.ndarray
    a: np.ndarray
    b: np.ndarray
    name: str = "layer"
    info: Dict[str, object] = field(default_factory=dict)


def _p1_gradients(p: np.ndarray, t: np.ndarray):
    """∇N_k of the three vertex shape functions per element, and 2·area.

    ``p`` (2, n_nodes), ``t`` (3, n_elem).  Returns gx, gy of shape (3, n_elem)
    and the signed doubled area (n_elem,).
    """
    x = p[0][t]; y = p[1][t]                     # (3, ne)
    two_area = ((x[1] - x[0]) * (y[2] - y[0])
                - (x[2] - x[0]) * (y[1] - y[0]))
    if np.any(two_area == 0.0):
        raise ValueError("degenerate (zero-area) element in the layer")
    gx = np.stack((y[1] - y[2], y[2] - y[0], y[0] - y[1])) / two_area
    gy = np.stack((x[2] - x[1], x[0] - x[2], x[1] - x[0])) / two_area
    return gx, gy, two_area


def rotation_displacement_gradient(p: np.ndarray, t: np.ndarray,
                                   phi: np.ndarray) -> np.ndarray:
    """D = ∇u per element for u = φ·(−y, x) interpolated with P1 [1/rad].

    Returns an array (n_elem, 2, 2) with D[e, i, j] = ∂u_i/∂x_j.
    """
    p = np.asarray(p, float); t = np.asarray(t, int)
    phi = np.asarray(phi, float)
    gx, gy, _ = _p1_gradients(p, t)
    ux = -phi[t] * p[1][t]                       # (3, ne) nodal u_x
    uy = phi[t] * p[0][t]                        # (3, ne) nodal u_y
    D = np.empty((t.shape[1], 2, 2))
    D[:, 0, 0] = np.sum(ux * gx, axis=0)
    D[:, 0, 1] = np.sum(ux * gy, axis=0)
    D[:, 1, 0] = np.sum(uy * gx, axis=0)
    D[:, 1, 1] = np.sum(uy * gy, axis=0)
    return D


def deformed_elements(t: np.ndarray, phi: np.ndarray) -> np.ndarray:
    """Elements whose vertices do not all carry the same φ (they change shape)."""
    v = np.asarray(phi, float)[np.asarray(t, int)]
    return np.where(np.ptp(v, axis=0) > 0.0)[0]


def build_layer(p: np.ndarray, t: np.ndarray, phi: np.ndarray,
                air_mask: np.ndarray, name: str = "layer",
                info: Optional[Dict[str, object]] = None) -> CoulombLayer:
    """Validate a virtual-displacement field and freeze its layer.

    ``air_mask`` (n_elem,) is True for pure-air elements (ν0, no source, no σ).
    Raises :class:`CoulombLayerError` if any element the field deforms is not
    air, or if no element is deformed at all.
    """
    p = np.asarray(p, float); t = np.asarray(t, int)
    phi = np.asarray(phi, float)
    air_mask = np.asarray(air_mask, bool)
    if phi.shape != (p.shape[1],):
        raise ValueError("phi must have one value per mesh vertex")
    if air_mask.shape != (t.shape[1],):
        raise ValueError("air_mask must have one value per element")
    if not np.all(np.isfinite(phi)):
        raise ValueError("phi must be finite")
    elems = deformed_elements(t, phi)
    if elems.size == 0:
        raise CoulombLayerError("%s: the displacement field deforms no element" % name)
    bad = elems[~air_mask[elems]]
    if bad.size:
        raise CoulombLayerError(
            "%s: the displacement field deforms %d non-air element(s) "
            "(first: %s); the Coulomb layer must be pure air"
            % (name, int(bad.size), bad[:5].tolist()))
    D = rotation_displacement_gradient(p, t[:, elems], phi)
    a = 0.5 * (D[:, 0, 0] - D[:, 1, 1])
    b = 0.5 * (D[:, 0, 1] + D[:, 1, 0])
    out_info = {"n_elements": int(elems.size)}
    out_info.update(info or {})
    return CoulombLayer(elements=elems, phi=phi, a=a, b=b, name=name,
                        info=out_info)


def _grad_at_quad(basis, w):
    """∇w at the quadrature points of ``basis`` (2, n_elem, n_qp).

    Same arithmetic as ``field_ops._grad_at_quad`` (skfem's linear
    combination), repeated here so this module has no solver dependency.
    """
    edofs = basis.element_dofs
    g0 = basis.basis[0][0].get(1)
    out = np.zeros(np.shape(g0), dtype=np.result_type(g0, w))
    for i in range(basis.Nbfun):
        out += np.einsum('...,...j->...j', w[edofs[i]],
                         basis.basis[i][0].get(1))
    return out


def prepare_coulomb_torque(mesh, element, layer: CoulombLayer,
                           stack_length_m: float, sector_count: int = 1,
                           nu: float = NU0) -> Callable[[np.ndarray], float]:
    """Return ``torque(A) -> T [N·m]`` for one fixed mesh, element and layer.

    ``A`` is the global DOF vector of a field on ``Basis(mesh, element)``.
    The result is multiplied by the stack length and by ``sector_count`` (the
    same symmetry multiplier the Maxwell path applies to a sector model).
    Sign: positive = counter-clockwise torque on the rotor (the side where
    φ = 1), the convention of the Arkkio evaluator.
    """
    from skfem import Basis
    gb = Basis(mesh, element, elements=layer.elements)
    dx = np.asarray(gb.dx)                        # (ne, nq) quadrature weights
    wa = dx * layer.a[:, None]
    wb = dx * (2.0 * layer.b)[:, None]
    scale = float(nu) * float(stack_length_m) * float(sector_count)

    def torque(A_vec) -> float:
        g = _grad_at_quad(gb, np.asarray(A_vec))
        gx, gy = g[0], g[1]
        return scale * float(np.sum(wa * (gx * gx - gy * gy) + wb * (gx * gy)))

    return torque


def coulomb_torque(mesh, element, A_vec, layer: CoulombLayer,
                   stack_length_m: float, sector_count: int = 1,
                   nu: float = NU0) -> float:
    """One-shot convenience wrapper around :func:`prepare_coulomb_torque`."""
    return prepare_coulomb_torque(mesh, element, layer, stack_length_m,
                                  sector_count, nu)(A_vec)


def radial_phi(r: np.ndarray, r_one: float, r_zero: float) -> np.ndarray:
    """φ(r) = 1 for r ≤ r_one, 0 for r ≥ r_zero, linear in between."""
    r = np.asarray(r, float)
    if not r_zero > r_one:
        raise ValueError("r_zero must exceed r_one")
    return np.clip((r_zero - r) / (r_zero - r_one), 0.0, 1.0)


def sliding_band_layers(p: np.ndarray, t: np.ndarray, n_stator_nodes: int,
                        air_mask: np.ndarray, r_rotor_metal: float,
                        r_slip: float, r_stator_metal: float,
                        rel_tol: float = 1e-9,
                        slip_nodes_rotor: Optional[np.ndarray] = None,
                        slip_nodes_stator: Optional[np.ndarray] = None
                        ) -> Dict[str, CoulombLayer]:
    """The two self-check layers of a two-half sliding-band mesh.

    ``p``/``t`` are the stitched mesh (stator-half nodes first, then the
    rotor-half nodes, which live in the ROTOR frame — a rotation of the whole
    rotor half is invisible to these radius-only fields).  The slip circle is
    duplicated: one copy per half.

    * ``rotor_side``: φ = 1 on every rotor-half vertex at r ≤ r_rotor_metal,
      linear in r to 0 at the rotor-half copy of the slip circle; 0 on the
      whole stator half.  Deforms the rotor-half gap air only.
    * ``stator_side``: φ = 1 on the whole rotor half and on the stator-half
      copy of the slip circle, linear in r to 0 at r_stator_metal; 0 beyond.
      Deforms the stator-half gap air only.

    ``r_rotor_metal`` is the outermost radius of any rotating non-air node
    (iron, magnet, sleeve); ``r_stator_metal`` the innermost stator iron
    radius.  Both layers are validated against ``air_mask``.

    ``slip_nodes_rotor`` / ``slip_nodes_stator`` (global node ids of the two
    copies of the slip ring, optional) pin φ EXACTLY on the interface — 0 on
    the rotor copy for the rotor-side layer, 1 on the stator copy for the
    stator-side layer — so the two copies of every welded node always move
    together and the virtual displacement never opens the slip cut.
    """
    p = np.asarray(p, float)
    ns = int(n_stator_nodes)
    r = np.hypot(p[0], p[1])
    if not (r_stator_metal > r_slip > r_rotor_metal > 0.0):
        raise CoulombLayerError(
            "sliding-band layers need r_rotor_metal < r_slip < r_stator_metal "
            "(got %.9g, %.9g, %.9g)" % (r_rotor_metal, r_slip, r_stator_metal))
    tol = rel_tol * r_slip
    is_stator = np.zeros(p.shape[1], bool); is_stator[:ns] = True
    # rotor side: slip radius pulled in by the tolerance so the rotor-half
    # slip-ring copy lands exactly on 0 despite round-off in its radius
    phi_r = np.where(is_stator, 0.0,
                     radial_phi(r, r_rotor_metal + tol, r_slip - tol))
    phi_s = np.where(is_stator,
                     radial_phi(r, r_slip + tol, r_stator_metal - tol), 1.0)
    if slip_nodes_rotor is not None:
        phi_r[np.asarray(slip_nodes_rotor, int)] = 0.0
    if slip_nodes_stator is not None:
        phi_s[np.asarray(slip_nodes_stator, int)] = 1.0
    return {
        "rotor_side": build_layer(
            p, t, phi_r, air_mask, "rotor_side",
            {"r_one_m": float(r_rotor_metal), "r_zero_m": float(r_slip)}),
        "stator_side": build_layer(
            p, t, phi_s, air_mask, "stator_side",
            {"r_one_m": float(r_slip), "r_zero_m": float(r_stator_metal)}),
    }


#: Layer self-check gate (owner 2026-09-30): the two-ring difference may be
#: at most this fraction of the RIPPLE SCALE, max(p-p, 0.5 % of |mean|) — the
#: owner's ripple tolerance is max(0.5 pp, 10 % relative), and on the measured
#: machines the ripple error was 0.3-0.55x the self-check (Ø40 static: 19.4 %
#: -> ripple +10.6 %, 3.0 % -> +0.9 %), so a 5 % gate keeps it near 3 %.
SELF_CHECK_GATE = 0.05
#: Absolute ripple floor of the gate's denominator, as a fraction of |mean|.
RIPPLE_SCALE_FLOOR_REL_MEAN = 0.005
#: Target the automatic gap refinement aims at (margin under the gate), the
#: convergence order it assumes and the most layers per side it will choose.
#: The order is MEASURED, not P2's nominal 2: on the Ø40 static mesh the
#: self-check fell with order 1.39 (rated) / 1.14 (no-load) from 1 to 2 layers
#: per side, 2.2 from 2 to 3 and 1.7 / 1.5 from 1 to 3 — 1.5 sends both
#: 1-layer cases to 3 layers per side, where both pass (3.0 % / 2.2 %).
SELF_CHECK_TARGET = 0.04
SELF_CHECK_ORDER = 1.5
GAP_LAYERS_AUTO_MAX = 6.0
# kept for callers of the first version
SELF_CHECK_MAX_REL_TO_PP = SELF_CHECK_GATE


def ripple_pp(series: Sequence[float]) -> Optional[float]:
    """Raw peak-to-peak of a series [same unit], ``None`` when empty."""
    x = np.asarray(list(series), float)
    return float(np.ptp(x)) if x.size else None


def layer_self_check(t_rotor_side: Iterable[float],
                     t_stator_side: Iterable[float]) -> Dict[str, Optional[float]]:
    """Summary of the two-layer difference over a series of frames [N·m].

    ``max_abs_diff_Nm``   max_k |T_r − T_s|;
    ``mean_diff_Nm``      mean_k (T_r − T_s);
    ``rel_to_mean``       max |T_r − T_s| / |mean of the two-layer average|
                          (``None`` if that mean is zero);
    ``rel_to_pp``         max |T_r − T_s| / p-p of the average (``None`` if
                          the waveform is flat);
    ``rel_to_ripple_scale``  max |T_r − T_s| / max(p-p, 0.5 % of |mean|) —
                          the gated number;
    ``ripple_mesh_limited``  rel_to_ripple_scale > ``SELF_CHECK_GATE``: the gap
                          mesh, not the machine, sets the ripple — refine the
                          gap (gap layers per side).
    """
    a = np.asarray(list(t_rotor_side), float)
    b = np.asarray(list(t_stator_side), float)
    base = {"threshold_rel_to_ripple_scale": SELF_CHECK_GATE,
            "ripple_scale_floor_rel_mean": RIPPLE_SCALE_FLOOR_REL_MEAN}
    if a.size == 0 or a.shape != b.shape:
        return {"max_abs_diff_Nm": None, "mean_diff_Nm": None,
                "rel_to_mean": None, "rel_to_pp": None,
                "rel_to_ripple_scale": None, "ripple_mesh_limited": None, **base}
    d = a - b
    avg = 0.5 * (a + b)
    m = float(np.mean(avg)); pp = float(np.ptp(avg))
    mx = float(np.max(np.abs(d)))
    scale = max(pp, RIPPLE_SCALE_FLOOR_REL_MEAN * abs(m))
    rel_scale = (mx / scale if scale > 0.0 else None)
    return {"max_abs_diff_Nm": mx, "mean_diff_Nm": float(np.mean(d)),
            "rel_to_mean": (mx / abs(m) if m != 0.0 else None),
            "rel_to_pp": (mx / pp if pp > 0.0 else None),
            "rel_to_ripple_scale": rel_scale,
            "ripple_mesh_limited": (None if rel_scale is None
                                    else bool(rel_scale > SELF_CHECK_GATE)),
            **base}


def gap_layers_for_self_check(gap_layers: float, rel_to_ripple_scale: Optional[float],
                              target: float = SELF_CHECK_TARGET,
                              order: float = SELF_CHECK_ORDER,
                              gl_max: float = GAP_LAYERS_AUTO_MAX) -> Optional[float]:
    """Gap layers per side predicted to bring the self-check to ``target``.

    ``None`` when the check passes the gate (or is unknown) or the mesh is
    already at ``gl_max``.  Otherwise
    ``min(gl_max, max(gl + 1, ceil(gl · (ε / target) ** (1 / order))))``.
    """
    if rel_to_ripple_scale is None or not math.isfinite(rel_to_ripple_scale):
        return None
    if rel_to_ripple_scale <= SELF_CHECK_GATE or gap_layers >= gl_max:
        return None
    want = math.ceil(float(gap_layers) * (rel_to_ripple_scale / target) ** (1.0 / order))
    return float(min(gl_max, max(float(gap_layers) + 1.0, want)))


# ── shared per-frame torque post-processing ──────────────────────────────────
#: Domain tags that are air in the sliding-band meshes (sb_domains):
#: DOM_AIR, DOM_AIRGAP, DOM_BAND, DOM_OUTER.  A tag alone is not enough — an
#: element is air for the layer only if its base ν is also exactly ν0 and it
#: is not saturable (see ``air_mask_from``).
AIR_TAGS = (0, 3, 7, 8)


def air_mask_from(elem_tags: np.ndarray, nu_const: np.ndarray,
                  air_tags: Sequence[int] = AIR_TAGS,
                  nu_air: float = NU0) -> np.ndarray:
    """True for elements that are pure air: an air tag AND ν exactly ν0.

    ``nu_const`` is the per-element constant reluctivity with saturable
    elements set to 0 (the solver's ``_nu_const2``), so iron never passes.
    """
    tags = np.asarray(elem_tags, int)
    nu = np.asarray(nu_const, float)
    return np.isin(tags, np.asarray(air_tags, int)) & (nu == float(nu_air))


@dataclass
class FrameTorqueEvaluator:
    """Everything a frame needs to report its torques, prepared ONCE per mesh.

    ``maxwell_sector``  the Arkkio evaluator (SECTOR torque, the solver's
                        ``_prepare_arkkio_torque_p2``), or None;
    ``coulomb``         layer name -> prepared Coulomb evaluator (already
                        scaled by stack length and sector count), or empty;
    ``sector_count``    the Maxwell symmetry multiplier;
    ``unavailable_reason``  why Coulomb is absent (None when present);
    ``layers_info``     per-layer description for the record.
    """

    maxwell_sector: Optional[Callable[[np.ndarray], float]]
    coulomb: Dict[str, Callable[[np.ndarray], float]]
    sector_count: int = 1
    unavailable_reason: Optional[str] = None
    layers_info: Dict[str, Dict[str, object]] = field(default_factory=dict)


def prepare_sliding_band_frame_torques(
        mesh, element, *, stack_length_m: float, sector_count: int,
        maxwell_sector: Optional[Callable[[np.ndarray], float]],
        n_stator_nodes: int, air_mask: np.ndarray, r_rotor_metal: float,
        r_slip: float, r_stator_metal: float,
        slip_nodes_rotor: Optional[np.ndarray] = None,
        slip_nodes_stator: Optional[np.ndarray] = None,
        log_warning: Optional[Callable[..., None]] = None,
) -> FrameTorqueEvaluator:
    """Build the per-frame torque evaluator of a two-half sliding-band mesh.

    Never raises on a Coulomb set-up problem: the Maxwell torque must keep
    working, so the reason is recorded in ``unavailable_reason`` (and logged
    through ``log_warning`` when given) and Coulomb is reported as None.
    """
    coul: Dict[str, Callable[[np.ndarray], float]] = {}
    info: Dict[str, Dict[str, object]] = {}
    reason = None
    try:
        layers = sliding_band_layers(
            mesh.p, mesh.t, n_stator_nodes, air_mask, r_rotor_metal, r_slip,
            r_stator_metal, slip_nodes_rotor=slip_nodes_rotor,
            slip_nodes_stator=slip_nodes_stator)
        for name, lay in layers.items():
            coul[name] = prepare_coulomb_torque(mesh, element, lay,
                                                stack_length_m, sector_count)
            info[name] = dict(lay.info)
    except Exception as exc:          # noqa: BLE001 — recorded, never silent
        coul = {}
        reason = "%s: %s" % (type(exc).__name__, exc)
        if log_warning is not None:
            log_warning("Coulomb virtual-work torque unavailable (%s)", reason)
    return FrameTorqueEvaluator(maxwell_sector=maxwell_sector, coulomb=coul,
                                sector_count=int(sector_count),
                                unavailable_reason=reason, layers_info=info)


def frame_torques(evaluator: FrameTorqueEvaluator, A_vec) -> Dict[str, Optional[float]]:
    """Torques of ONE solved frame — the shared per-frame post-processing.

    Pure function of the prepared evaluator (mesh, element, layers) and the
    frame's solved field ``A_vec``; eddy currents, demagnetisation and the
    drive type need nothing else (the layers are source-free air).  Any frame
    producer — the marching transient or a time-periodic solve — calls this.

    Returns (N·m, full machine, full precision):
      ``maxwell_Nm``               Arkkio × sector count (bit-identical to the
                                   solver's historical ``_torque2(A) * NS``);
      ``coulomb_rotor_side_Nm``    Coulomb on the rotor-side gap air ring;
      ``coulomb_stator_side_Nm``   Coulomb on the stator-side gap air ring;
      ``coulomb_Nm``               their mean = Coulomb over the whole gap;
      ``coulomb_layer_diff_Nm``    rotor side − stator side (self-check).
    Coulomb entries are None when the evaluator has no layers.
    """
    out: Dict[str, Optional[float]] = {
        "maxwell_Nm": (evaluator.maxwell_sector(A_vec) * evaluator.sector_count
                       if evaluator.maxwell_sector is not None else None),
        "coulomb_rotor_side_Nm": None, "coulomb_stator_side_Nm": None,
        "coulomb_Nm": None, "coulomb_layer_diff_Nm": None,
    }
    if "rotor_side" in evaluator.coulomb and "stator_side" in evaluator.coulomb:
        tr = float(evaluator.coulomb["rotor_side"](A_vec))
        ts = float(evaluator.coulomb["stator_side"](A_vec))
        out.update({"coulomb_rotor_side_Nm": tr, "coulomb_stator_side_Nm": ts,
                    "coulomb_Nm": 0.5 * (tr + ts),
                    "coulomb_layer_diff_Nm": tr - ts})
    return out


def coulomb_series_summary(t_coulomb: Sequence[Optional[float]],
                           t_layers: Sequence[Sequence[Optional[float]]],
                           unavailable_reason: Optional[str] = None,
                           n_steps_per_period: Optional[int] = None,
                           step_periods: Optional[float] = None
                           ) -> Dict[str, object]:
    """Retained-window summary of the Coulomb series, full precision.

    ``t_coulomb`` one value per reported frame (None where unavailable);
    ``t_layers``  (rotor_side, stator_side) per reported frame.
    With ``n_steps_per_period`` the raw spectrum is added
    (``sb_postproc.torque_harmonics``, same bins as the Maxwell one).
    """
    vals = list(t_coulomb)
    ok = bool(vals) and all(v is not None and math.isfinite(v) for v in vals)
    res: Dict[str, object] = {
        "available": ok,
        "unavailable_reason": (None if ok else (
            unavailable_reason or ("no reported frames" if not vals
                                   else "one or more frames lack a Coulomb value"))),
        "T_coulomb_series": [None if v is None else float(v) for v in vals],
        "T_avg_coulomb_Nm": None, "T_ripple_pp_coulomb": None,
        "T_ripple_pct_coulomb": None,
        "T_coulomb_rotor_side_series": [None if a is None else float(a) for a, _ in t_layers],
        "T_coulomb_stator_side_series": [None if b is None else float(b) for _, b in t_layers],
        "layer_self_check": layer_self_check([], []),
        "method": "coulomb_virtual_work",
        "T_harm_order_coulomb": [], "T_harm_amp_coulomb": [],
    }
    if ok:
        x = np.asarray(vals, float)
        m = float(x.mean()); pp = float(np.ptp(x))
        res.update({"T_avg_coulomb_Nm": m, "T_ripple_pp_coulomb": pp,
                    "T_ripple_pct_coulomb": (100.0 * pp / abs(m) if abs(m) > 1e-9 else None),
                    "layer_self_check": layer_self_check(
                        [a for a, _ in t_layers], [b for _, b in t_layers])})
        if n_steps_per_period:
            from motor_ai_sim.simulation.sb_postproc import torque_harmonics
            res["T_harm_order_coulomb"], res["T_harm_amp_coulomb"] = torque_harmonics(
                vals, int(n_steps_per_period), step_periods=step_periods)
    return res
