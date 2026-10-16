"""The winding as a CLOSED loop, expressed as a source field T with curl(T) = J.

Why not simply extrude the 2D current density
---------------------------------------------
A straight axial J_z running from one end of the stack to the other is not a
current: it is charge arriving at z = +L/2 and never leaving.  Magnetostatics
has no solution for that, and the A-formulation does not say so — the load
vector simply acquires a component outside the range of the curl-curl operator,
and whatever gauge is imposed (or, here, ``nedelec.project_source``) quietly
absorbs it.  The answer that comes back is the answer to a different problem,
and nothing in it is marked.

So the coil has to close.  A tooth-wound coil closes through END TURNS: up one
side of the tooth, over the top, down the other side, and back underneath.  This
model is the HALF machine z >= 0 with a mirror plane at z = 0, and the mirror
completes the loop exactly — the slot current is even in z, the end-turn current
is odd in z, and both statements together are precisely B_z = 0 on z = 0, which
is the Dirichlet condition ``nedelec.mirror_plane_dofs`` already imposes.  So
the modelled half is one half of a genuine closed loop, and the current that
crosses z = 0 leaves through a boundary whose nodes the discrete-gradient
projection has already contracted away.  Nothing is absorbed.

Why T and not J
---------------
Both are available in ``nedelec.solve_static3d_A``.  A load assembled as
``integral(J . v)`` is consistent only up to the quadrature's opinion of div J:
measured on this project's own coil benchmark, 9.3e-3 — small, but not zero, and
the projection that removes it is removing a real piece of the load.  A load
assembled as ``integral(T . curl v)`` is consistent to ROUND-OFF for ANY T
whatsoever, because curl(grad psi) is identically zero in the Nedelec space.
The physics is not approximated by this choice; only the bookkeeping is made
exact.  ``build_winding_T`` reports both numbers so the claim is measured rather
than asserted.

The construction
----------------
Take T radial::

    T(r, theta, z) = Psi(r, theta) * beta(z) * e_r

and use curl(psi e_r) = grad(psi) x e_r (e_r = grad(r) has zero curl):

    J_z     = -(1/r) d(Psi)/d(theta) * beta(z)
    J_theta =        Psi(r, theta)   * beta'(z)
    J_r     = 0

so with

    Psi(r, theta) = - r * integral_0^theta  J_z^2D(r, theta') d(theta')

the axial current inside the stack (beta = 1) is EXACTLY the 2D solver's own
piecewise-constant ``J_z = dir * I * n_wires / A_copper``, element for element,
and the end-turn band above the stack (where beta falls from 1 to 0) carries the
tangential return.  Each radius closes its own loop — up one side of the tooth
at radius r, across the top at the same r, down the other side — which is what a
tooth end-winding does, and it makes div J = 0 an identity rather than a
tolerance.

Two properties of that Psi are worth stating because they are what make the
sector model legal:

* Psi is compactly supported in r without any windowing: J_z vanishes for radii
  above and below the copper, so Psi does too.  A jump in the NORMAL component
  of T across a cylinder carries no surface current (n x [T] = 0 when [T] || n),
  so the support can end abruptly and does not have to be tapered.
* Psi(r, sector) = 0, i.e. T is anti-periodic on the 180 deg sector exactly when
  the winding is.  For the 12s/14p single-layer layout the net ampere-turns per
  half are ``I_A - I_A + I_C - I_C + I_B - I_B`` = 0 identically, for every
  instant of a balanced supply and at every radius.  It is CHECKED, not assumed:
  a layout that failed it would make the sector solve a different machine.

Units: SI (metres, A/m, A/m^2, tesla).  Angles in radians unless a name says deg.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .motor_geometry import MM, MotorSection

MU0 = 4.0e-7 * math.pi


# --------------------------------------------------------------------------
# the operating point, from the 2D solver's own excitation object
# --------------------------------------------------------------------------

def phase_currents(section: MotorSection, i_phase_rms: float,
                   gamma_deg: float, rotor_angle_deg: float = 0.0,
                   connection: Optional[str] = None,
                   n_parallel: Optional[int] = None,
                   daxis_deg: Optional[float] = None) -> Tuple[Dict[str, float], dict]:
    """(coil currents, provenance) at one rotor angle, from ``drive.Excitation``.

    Not re-derived here.  ``gamma`` is measured from the q-axis only after the
    per-topology ``daxis`` offset the 2D solver auto-calibrates, and a 3D model
    that re-implemented the phase algebra would sit at a different operating
    point than the 2D leg it is about to be divided by.  So the excitation
    object is the 2D solver's, imported read-only, and the d-axis offset is
    resolved by the 2D solver's own resolver unless a caller pins it.
    """
    from motor_ai_sim.simulation.drive import Excitation

    npar = int(n_parallel) if n_parallel else 1
    if n_parallel is None and connection:
        from motor_ai_sim.winding import parse_connection
        npar, _ns = parse_connection(str(connection))
    if daxis_deg is None:
        raise ValueError(
            "daxis_deg is required: gamma is measured from the q-axis, and the "
            "q-axis is a per-topology calibrated quantity "
            "(fem_solver_2d._resolve_daxis_shift).  Guessing it here would put "
            "the 3D solve at a different operating point than the 2D leg.")
    exc = Excitation(pole_pairs=int(section.pole_pairs),
                     daxis_deg=float(daxis_deg),
                     i_peak=float(i_phase_rms) / max(npar, 1) * math.sqrt(2.0),
                     gamma_deg=float(gamma_deg))
    prov = dict(i_phase_rms=float(i_phase_rms), n_parallel=int(npar),
                i_coil_peak=float(exc.i_peak), gamma_deg=float(gamma_deg),
                daxis_deg=float(daxis_deg),
                rotor_angle_deg=float(rotor_angle_deg),
                connection=(str(connection) if connection else None),
                source="simulation.drive.Excitation (read-only)")
    return exc.currents(float(rotor_angle_deg)), prov


def slot_current_densities(section: MotorSection, I_ph: Dict[str, float]
                           ) -> Tuple[List[object], np.ndarray, dict]:
    """(coil polygons, J_z per polygon, provenance), straight out of the 2D solver.

    ``fem_solver_2d.build_materials`` is called with the instantaneous phase
    currents and its ``DOM_COIL_BASE + i`` materials are read back.  That is the
    ONE place in this project that knows which slot a coil polygon belongs to
    (``field_ops.coil_slot_index`` and its half-pitch offset), which phase and
    sign the layout gives that slot, and which copper area the division is by.
    Re-deriving any of it here is how a 3D model comes to excite a different
    machine than the 2D model it is being compared with.
    """
    from motor_ai_sim.simulation.fem_solver_2d import DOM_COIL_BASE, build_materials
    from motor_ai_sim.simulation.geometry_2d import build_winding_layout

    polys = section.polys_full
    if polys is None:
        raise ValueError("MotorSection carries no full-ring polygons")
    coils = list(polys.get("coils", []))
    if not coils:
        raise ValueError(
            "the cross-section carries no coil polygons — there is no winding "
            "to close, and a torque computed without one would be cogging")
    layout = build_winding_layout(section.num_slots, section.num_poles // 2)
    n_wires = int(round(section.geo["num_wires_per_slot"]))
    mats = build_materials(dict(I_ph), layout, polys, 0.0, 1e-5, n_wires)

    Jz = np.zeros(len(coils))
    missing = 0
    for i in range(len(coils)):
        m = mats.get(DOM_COIL_BASE + i)
        if m is None:
            missing += 1
            continue
        Jz[i] = float(getattr(m, "J_z", 0.0) or 0.0)
    if missing:
        raise ValueError(
            f"{missing} of {len(coils)} coil polygons got no material from "
            "build_materials — the winding would be excited with holes in it")
    prov = dict(n_coil_polygons=len(coils), n_wires_per_slot=n_wires,
                layout=[f"{p}{'+' if d > 0 else '-'}" for p, d in layout],
                I_ph={k: float(v) for k, v in I_ph.items()},
                J_z_max=float(np.max(np.abs(Jz))),
                source="fem_solver_2d.build_materials (read-only)")
    return coils, Jz, prov


# --------------------------------------------------------------------------
# the source field
# --------------------------------------------------------------------------

@dataclass
class WindingT:
    """A closed winding as a sampled source field ``T = Psi(r,theta) beta(z) e_r``.

    ``Psi`` is stored on a polar grid and read back by bilinear interpolation,
    which is what makes the field cheap enough to evaluate at every quadrature
    point of every Picard sweep.  Interpolating T (rather than J) is also what
    keeps the load exactly consistent: whatever the interpolation does to Psi,
    ``integral(T . curl v)`` is still orthogonal to every discrete gradient.
    """
    r_grid: np.ndarray                 # (nr,) metres, ascending
    th_grid: np.ndarray                # (nt,) radians, ascending, [0, sector]
    Psi: np.ndarray                    # (nr, nt) A (i.e. A/m x m)
    stack_half_m: float
    h_ew_m: float
    ew_shape: str
    sector_rad: float
    ampere_turns_per_slot: Dict[int, float] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    # -- the axial profile of the end turn ---------------------------------
    def beta(self, z: np.ndarray) -> np.ndarray:
        """1 inside the stack, falling to 0 across the end-winding band.

        ``cosine`` gives a continuous beta' — the tangential end current then
        starts and stops smoothly instead of switching on at a plane, which is
        both closer to a wound bundle and kinder to a first-order element.
        ``linear`` spreads the same total ampere-turns uniformly over the band
        and exists so the answer's sensitivity to this shape can be MEASURED
        rather than argued (``build_winding_T`` records which was used)."""
        u = (np.abs(np.asarray(z, dtype=float)) - self.stack_half_m) / self.h_ew_m
        u = np.clip(u, 0.0, 1.0)
        if self.ew_shape == "linear":
            return 1.0 - u
        return 0.5 * (1.0 + np.cos(math.pi * u))

    def dbeta_dz(self, z: np.ndarray) -> np.ndarray:
        """beta'(z) — only used to report the end-turn current, never assembled."""
        z = np.asarray(z, dtype=float)
        u = (np.abs(z) - self.stack_half_m) / self.h_ew_m
        inside = (u > 0.0) & (u < 1.0)
        if self.ew_shape == "linear":
            d = np.where(inside, -1.0 / self.h_ew_m, 0.0)
        else:
            d = np.where(inside,
                         -0.5 * math.pi / self.h_ew_m
                         * np.sin(math.pi * np.clip(u, 0.0, 1.0)), 0.0)
        return d * np.sign(z)

    # -- Psi ---------------------------------------------------------------
    def psi_at(self, r: np.ndarray, th: np.ndarray) -> np.ndarray:
        """Bilinear read-back of Psi, zero outside the copper's radial band."""
        r = np.asarray(r, dtype=float)
        th = np.asarray(th, dtype=float)
        rg, tg = self.r_grid, self.th_grid
        out = np.zeros(r.shape)
        ok = (r >= rg[0]) & (r <= rg[-1])
        if not ok.any():
            return out
        rr, tt = r[ok], np.clip(th[ok], tg[0], tg[-1])
        i = np.clip(np.searchsorted(rg, rr) - 1, 0, rg.size - 2)
        j = np.clip(np.searchsorted(tg, tt) - 1, 0, tg.size - 2)
        fr = (rr - rg[i]) / (rg[i + 1] - rg[i])
        ft = (tt - tg[j]) / (tg[j + 1] - tg[j])
        P = self.Psi
        out[ok] = ((1 - fr) * (1 - ft) * P[i, j]
                   + fr * (1 - ft) * P[i + 1, j]
                   + (1 - fr) * ft * P[i, j + 1]
                   + fr * ft * P[i + 1, j + 1])
        return out

    # -- what the solver consumes -----------------------------------------
    def field(self) -> Callable[[np.ndarray], np.ndarray]:
        """``f(x) -> (3, nelem, nqp)`` for ``nedelec.solve_static3d_A(T=...)``."""
        def _T(x: np.ndarray) -> np.ndarray:
            x = np.asarray(x, dtype=float)
            r = np.hypot(x[0], x[1])
            safe = np.where(r > 0.0, r, 1.0)
            th = np.arctan2(x[1], x[0])
            th = np.where(th < 0.0, th + 2.0 * math.pi, th)
            amp = self.psi_at(r, th) * self.beta(x[2])
            out = np.zeros_like(x)
            out[0] = amp * x[0] / safe
            out[1] = amp * x[1] / safe
            return out
        return _T

    # NOTE deliberately no ``rotated()`` here.  The torque path turns the ROTOR,
    # not the stator, so a rotated winding is not what the energy method needs;
    # writing one now would ship an untested method that a later pass would
    # reasonably assume had been checked.  When the sliding band lands
    # (config/end_effect_3d.json::stage_b.blockers), the winding stays put.


def build_winding_T(section: MotorSection,
                    I_ph: Dict[str, float],
                    stack_mm: Optional[float] = None,
                    h_ew_mm: Optional[float] = None,
                    ew_shape: str = "cosine",
                    n_r: int = 121,
                    n_theta: int = 5401,
                    at_tol: float = 2e-3) -> WindingT:
    """Sample ``Psi`` for the machine's own winding at one instant.

    ``h_ew_mm`` defaults to the radius of the half-loop the project's END-WINDING
    LENGTH FACTOR already assumes — ``tooth_width/2 + wire_width/2`` — so the 3D
    end turn occupies the same axial space the 2D copper-loss model has been
    charging for.  It is a parameter because it is a modelling choice and the
    result's sensitivity to it has to be reported, not hidden.

    ``n_theta`` sets how sharply the copper's angular edges are resolved when
    Psi is integrated.  It does NOT affect the load's consistency (that is exact
    for any Psi); it only affects where the ampere-turns sit, and the delivered
    ampere-turns per slot are checked against the intended ones to ``at_tol``.
    """
    import shapely
    from shapely import STRtree

    from motor_ai_sim.simulation.field_ops import coil_slot_index

    coils, Jz, prov = slot_current_densities(section, I_ph)
    sector = math.radians(section.sector_deg)
    L = float(stack_mm if stack_mm else section.stack_mm)
    h_ew = float(h_ew_mm) if h_ew_mm is not None else (
        0.5 * float(section.geo["tooth_width"]) + 0.5 * float(section.geo["wire_width"]))
    if h_ew <= 0.0:
        raise ValueError(f"end-winding band height must be positive, got {h_ew}")
    if ew_shape not in ("cosine", "linear"):
        raise ValueError(f"ew_shape must be 'cosine' or 'linear', got {ew_shape!r}")

    # radial band of the copper, with one grid step of margin either side so the
    # interpolation has a zero to land on rather than a cliff.  Taken from the
    # polygons' own vertices (``shapely.get_coordinates`` handles holes and
    # MultiPolygons alike) rather than from bounding boxes, which would inflate
    # the band by up to a corner's worth and put grid rows where no copper is.
    xy = shapely.get_coordinates(np.asarray(coils, dtype=object))
    rad = np.hypot(xy[:, 0], xy[:, 1])
    r_lo, r_hi = float(rad.min()), float(rad.max())
    if not (r_hi > r_lo > 0.0):
        raise ValueError(f"coil polygons span r = {r_lo} .. {r_hi} mm — that is "
                         "not an annular winding")
    dr = (r_hi - r_lo) / max(n_r - 3, 1)
    r_grid = np.linspace(r_lo - dr, r_hi + dr, int(n_r)) * MM
    th_grid = np.linspace(0.0, sector, int(n_theta))

    # which coil polygon (if any) each polar sample falls in
    R, TH = np.meshgrid(r_grid / MM, th_grid, indexing="ij")
    pts = shapely.points((R * np.cos(TH)).ravel(), (R * np.sin(TH)).ravel())
    tree = STRtree(coils)
    hits = tree.query(pts, predicate="within")       # [pt_idx, poly_idx]
    Jmap = np.zeros(pts.size)
    if hits.size:
        # A point can only be inside one coil polygon; if the CAD ever overlaps
        # two, the later write would silently win, so it is caught here.
        uniq, counts = np.unique(hits[0], return_counts=True)
        if counts.max() > 1:
            raise ValueError(
                f"{int((counts > 1).sum())} polar samples fall inside more than "
                "one coil polygon — the winding polygons overlap and the "
                "ampere-turns they carry are not well defined")
        Jmap[hits[0]] = Jz[hits[1]]
    Jmap = Jmap.reshape(R.shape)                     # (nr, nt) A/m^2

    # Psi(r, theta) = - r * integral_0^theta J_z dtheta'   (trapezoid)
    dth = np.diff(th_grid)
    seg = 0.5 * (Jmap[:, 1:] + Jmap[:, :-1]) * dth[None]
    Psi = np.zeros_like(Jmap)
    Psi[:, 1:] = -np.cumsum(seg, axis=1)
    Psi *= (r_grid / MM * MM)[:, None]               # x r, in metres

    # ---- the two properties the sector model rests on --------------------
    # (1) T must be anti-periodic: Psi(r, sector) = 0.
    at_scale = max(float(np.max(np.abs(Psi))), 1e-30)
    close = float(np.max(np.abs(Psi[:, -1])))
    if close > 1e-6 * at_scale + 1e-12:
        raise ValueError(
            f"the winding does not close over one {section.sector_deg:.1f} deg "
            f"sector: Psi(r, sector) reaches {close:.4e} against a scale of "
            f"{at_scale:.4e}.  The net ampere-turns per sector are not zero, so "
            "the current pattern is not anti-periodic and a sector solve would "
            "excite a machine that does not exist")

    # (2) the ampere-turns actually delivered, per slot, against the intent
    n_slot = int(section.num_slots)
    want: Dict[int, float] = {}
    n_wires = int(round(section.geo["num_wires_per_slot"]))
    from motor_ai_sim.simulation.geometry_2d import build_winding_layout
    layout = build_winding_layout(n_slot, section.num_poles // 2)
    for k in range(n_slot):
        ph, d = layout[k]
        want[k] = float(d) * float(I_ph[ph]) * n_wires
    got: Dict[int, float] = {}
    slot_of = {}
    for i, c in enumerate(coils):
        si = coil_slot_index(c, n_slot)
        if si is not None:
            slot_of.setdefault(int(si), []).append(i)
    # delivered AT of a slot = integral over r of the JUMP in Psi across it
    edges = _slot_theta_edges(coils, slot_of, n_slot, sector)
    for si, (t0, t1) in edges.items():
        j0 = int(np.clip(np.searchsorted(th_grid, t0), 0, th_grid.size - 1))
        j1 = int(np.clip(np.searchsorted(th_grid, t1), 0, th_grid.size - 1))
        got[si] = float(np.trapezoid(Psi[:, j0] - Psi[:, j1], r_grid))
    scale = max(abs(v) for v in want.values()) if want else 1.0
    worst = 0.0
    for si, g in got.items():
        worst = max(worst, abs(g - want[si]) / max(scale, 1e-30))
    if scale > 1e-9 and worst > at_tol:
        raise ValueError(
            f"the sampled winding delivers ampere-turns {100*worst:.3f} % "
            f"(of the peak slot's {scale:.1f} At) away from what "
            "build_materials asked for — raise n_theta / n_r, or the copper "
            "polygons are not what coil_slot_index thinks they are")

    meta = dict(prov)
    meta.update(stack_mm=L, h_ew_mm=h_ew, ew_shape=ew_shape,
                n_r=int(n_r), n_theta=int(n_theta),
                r_grid_mm=[float(r_grid[0] / MM), float(r_grid[-1] / MM)],
                closure_Psi_max=close, closure_scale=at_scale,
                ampere_turn_error_pct=100.0 * worst,
                ampere_turns_intended={int(k): float(v) for k, v in want.items()},
                ampere_turns_delivered={int(k): float(v) for k, v in got.items()},
                h_ew_rule="tooth_width/2 + wire_width/2 (the k_end half-loop)")
    return WindingT(r_grid=r_grid, th_grid=th_grid, Psi=Psi,
                    stack_half_m=0.5 * L * MM, h_ew_m=h_ew * MM,
                    ew_shape=ew_shape, sector_rad=sector,
                    ampere_turns_per_slot={int(k): float(v)
                                           for k, v in got.items()},
                    meta=meta)


def _slot_theta_edges(coils, slot_of, n_slot: int, sector: float
                      ) -> Dict[int, Tuple[float, float]]:
    """(theta_start, theta_end) bracketing each slot's copper, inside the sector.

    Used only to AUDIT the delivered ampere-turns, never to build the field:
    Psi's jump between two angles that bracket a slot and touch no other slot is
    exactly that slot's current, whatever the copper's shape in between.
    """
    out: Dict[int, Tuple[float, float]] = {}
    pitch = 2.0 * math.pi / n_slot
    for si, idxs in slot_of.items():
        ths = []
        for i in idxs:
            xy = np.asarray(coils[i].exterior.coords, dtype=float)
            t = np.arctan2(xy[:, 1], xy[:, 0])
            t = np.where(t < 0.0, t + 2.0 * math.pi, t)
            ths.append(t)
        t = np.concatenate(ths)
        lo, hi = float(t.min()), float(t.max())
        if hi - lo > 0.9 * 2.0 * math.pi:            # straddles theta = 0
            continue
        mid = 0.5 * (lo + hi)
        a, b = mid - 0.5 * pitch, mid + 0.5 * pitch
        if a < 0.0 or b > sector:
            continue
        out[si] = (a, b)
    if not out:
        raise ValueError("no slot lies wholly inside the sector — the "
                         "ampere-turn audit has nothing to measure")
    return out


# --------------------------------------------------------------------------
# the exact-solution twin: a thick solenoid, as a T field
# --------------------------------------------------------------------------

def solenoid_T(J_phi: float, r_inner: float, r_outer: float, length: float
               ) -> Callable[[np.ndarray], np.ndarray]:
    """``T = tau(r) e_z`` whose curl is a uniform azimuthal J in the annulus.

        tau(r) = J (r_outer - clamp(r, r_inner, r_outer)) for |z| < L/2, else 0

    curl(tau e_z) = -d(tau)/dr e_phi = J e_phi exactly inside the winding and
    zero outside it.  The jump in tau at |z| = L/2 is a jump in the NORMAL
    component of T across that plane, which carries no surface current, so the
    solenoid really is finite and really does end there.

    This is the validation twin of :func:`build_winding_T`: the same code path
    in ``solve_static3d_A(T=...)``, driven by a source whose on-axis field is
    known in closed form (``exact.thick_solenoid_axis_Bz``).  A winding model
    that has never been put against an exact answer is a hypothesis.
    """
    a, b, h = float(r_inner), float(r_outer), 0.5 * float(length)
    if not (0.0 <= a < b):
        raise ValueError(f"need 0 <= r_inner < r_outer, got {a}, {b}")

    def _T(x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        r = np.hypot(x[0], x[1])
        tau = float(J_phi) * (b - np.clip(r, a, b))
        out = np.zeros_like(x)
        out[2] = np.where(np.abs(x[2]) < h, tau, 0.0)
        return out

    return _T
