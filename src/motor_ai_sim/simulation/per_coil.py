"""Per-coil current excitation for the six-coil study (Controller Stage 3).

An H-bridge per coil drives every coil of a single-layer concentrated winding
on its own.  This module lets the unchanged sliding-band transient
(``fem_solver_2d.fem_transient_sliding_band``) run such a drive, as a LIBRARY:
nothing in the solver is edited, the excitation is an
:class:`~motor_ai_sim.simulation.excitation.ExcitationSource`.

What the solver can and cannot express, and why that is enough
----------------------------------------------------------------
The solver has three current channels, ``A``/``B``/``C``; every slot of the
resolved winding layout is ``(phase, ±1)``.  A single-layer 12-slot/10-pole
winding has six coils; coil ``k`` and coil ``k+3`` belong to the same phase
with opposite signs and sit 180° mechanical = 900° = 180° electrical apart.
With the coil currents written in each coil's OWN orientation (the sign of its
first slot side), the standard three-phase drive gives coil ``k`` the current
``f(θe + φ_k)`` with ``φ_k = 60°·k`` — the 60° el. phasing.

A per-coil waveform ``f`` that is rotor-synchronous and contains only ODD
harmonics satisfies ``-f(x + 180°) = f(x)``, so coil ``k+3`` carries exactly
what the series/parallel partner of coil ``k`` would, and the six coil
currents collapse onto three channels WITHOUT loss of generality:
``channel_X(θe) = f(θe + δ_X)``, δ = 0/−120/+120°.  That covers the reference
three-phase sine, the per-coil sine and any odd-harmonic injection — the
triplen ones included, which is exactly the freedom a three-wire machine does
not have (they are zero-sequence: a star cannot carry them, a delta only
circulates the one its EMF drives).  :meth:`PerCoilCurrentSource.currents`
CHECKS the pairing at every call and raises when a waveform breaks it.

Even harmonics or unequal coils (a fault) break the pairing.  The one such
case the study needs — a coil OPEN — is expressed by zeroing that coil's slot
sources in the resolved layout (:func:`open_coils_layout`), so the remaining
five coils still map onto three channels.  Two consequences, both stated
wherever the numbers are used: the solver's phase flux linkage of the faulted
phase still sums the open coil's sides (its ψ-map reads the sign from the
material name), so the energy torque and phase voltage of that run are not
used — torque comes from the Maxwell stress of the air-gap field
(:func:`gap_forces`) scaled against the healthy run on the same mesh; and the
coupled strand-eddy solve must be off (its per-conductor constraint would
re-impose a current on the open coil).

UMP.  The net radial force is integrated from the per-frame air-gap field the
solver already returns for its animation (``return_frames``) — Maxwell stress
over a pure-air annulus of the stator-side gap, the force analogue of Arkkio's
torque.  A sector model cannot carry a net force (its images cancel it), so
UMP is computed on full-ring runs; on any pair-symmetric drive it is zero by
the same symmetry that makes the sector valid.
"""
from __future__ import annotations

import contextlib
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from motor_ai_sim.simulation.excitation import ExcitationError, SettlePolicy

MU0 = 4e-7 * math.pi
#: Electrical offset of each phase channel, the same as drive.Excitation.
PHASE_DELTA_DEG = {"A": 0.0, "B": -120.0, "C": 120.0}

__all__ = [
    "Coil", "coil_map", "CoilWaveform", "PerCoilCurrentSource",
    "open_coils_layout", "gap_forces", "coil_rms", "PairingError",
]


class PairingError(ExcitationError):
    """The requested coil currents cannot be expressed on three channels."""


# ═══════════════════════════════════════════════════════════════════════════
#  THE COILS
# ═══════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Coil:
    index: int              # 0-based coil number (coil 1 of the docs = 0)
    slots: Tuple[int, int]  # 0-based slot indices of its two sides
    phase: str              # the solver channel it belongs to
    sign: int               # sign of its FIRST side in the resolved layout
    phi_deg: float          # its own electrical phasing: channel δ + (0 | 180)


def coil_map(layout: Sequence[Tuple[str, int]]) -> List[Coil]:
    """Coils of a resolved SINGLE-LAYER layout (sides in slots 2j, 2j+1).

    Refuses anything else: a double-layer winding has two coils per slot and
    the one-coil-per-slot-pair reading below would be wrong for it.
    """
    n = len(layout)
    if n % 2:
        raise ValueError("per-coil study needs an even slot count (single layer)")
    coils: List[Coil] = []
    for j in range(n // 2):
        (p0, s0), (p1, s1) = layout[2 * j], layout[2 * j + 1]
        if p0 != p1 or s0 != -s1 or s0 == 0:
            raise ValueError(
                f"slots {2 * j + 1}/{2 * j + 2} are not one single-layer coil "
                f"({p0}{s0:+d}, {p1}{s1:+d})")
        phi = PHASE_DELTA_DEG[p0] + (0.0 if s0 > 0 else 180.0)
        coils.append(Coil(j, (2 * j, 2 * j + 1), p0, int(s0), phi % 360.0))
    return coils


# ═══════════════════════════════════════════════════════════════════════════
#  THE WAVEFORM
# ═══════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class CoilWaveform:
    """One coil's current as a function of its OWN electrical angle x [deg].

    ``f(x) = I1_peak · Σ_h a_h · cos(h·x + ψ_h)`` with ``a_1 = 1, ψ_1 = 0``.
    x = θ_rotor·p + daxis + γ + φ_k — so γ and the d-axis mean exactly what
    they mean on the stock current drive, and a harmonic's phase ψ_h is
    measured in ITS own cycles against the fundamental's q-axis crossing.
    """

    i1_peak: float
    harmonics: Tuple[Tuple[int, float, float], ...] = ()   # (h, a_h, ψ_h deg)

    def __post_init__(self):
        for h, _a, _p in self.harmonics:
            if int(h) < 2:
                raise ValueError("harmonics are orders >= 2; the fundamental is i1_peak")

    @property
    def odd_only(self) -> bool:
        return all(int(h) % 2 == 1 for h, a, _ in self.harmonics if abs(a) > 0)

    def __call__(self, x_deg: float) -> float:
        x = math.radians(x_deg)
        v = math.cos(x)
        for h, a, ps in self.harmonics:
            if a:
                v += float(a) * math.cos(int(h) * x + math.radians(ps))
        return self.i1_peak * v

    def rms(self) -> float:
        """RMS of the waveform (orthogonal harmonics)."""
        s = 1.0 + sum(float(a) ** 2 for _, a, _ in self.harmonics)
        return abs(self.i1_peak) * math.sqrt(0.5 * s)

    def scaled_to_rms(self, i_rms: float) -> "CoilWaveform":
        """Same shape, fundamental rescaled so the total rms is ``i_rms``.

        This is the EQUAL-COPPER-LOSS normalisation of the study: DC copper
        loss is R·Σ I_rms², so equal rms per coil is equal I²R.
        """
        s = math.sqrt(1.0 + sum(float(a) ** 2 for _, a, _ in self.harmonics))
        return CoilWaveform(i1_peak=float(i_rms) * math.sqrt(2.0) / s,
                            harmonics=tuple(self.harmonics))

    def as_dict(self) -> Dict[str, Any]:
        return {"i1_peak_A": self.i1_peak, "i_rms_A": self.rms(),
                "harmonics": [{"h": int(h), "a": float(a), "psi_deg": float(p)}
                              for h, a, p in self.harmonics]}


# ═══════════════════════════════════════════════════════════════════════════
#  THE SOURCE
# ═══════════════════════════════════════════════════════════════════════════
class PerCoilCurrentSource:
    """Imposed per-coil currents, folded onto the solver's three channels.

    ``coil_scale`` multiplies each coil's waveform (1 = healthy, 0 = open).
    An open coil must ALSO be removed from the layout
    (:func:`open_coils_layout`) — the channel then carries only its partner,
    and the pairing check skips it.
    """

    kind = "I"
    name = "per_coil_current"
    rms_from_series = True
    carriers = 0

    def __init__(self, coils: Sequence[Coil], waveform: CoilWaveform, *,
                 pole_pairs: int, daxis_deg: float, gamma_deg: float,
                 coil_scale: Optional[Sequence[float]] = None,
                 pair_tol: float = 1e-9):
        self.coils = list(coils)
        self.wave = waveform
        self.pole_pairs = int(pole_pairs)
        self.daxis_deg = float(daxis_deg)
        self.gamma_deg = float(gamma_deg)
        self.scale = ([1.0] * len(self.coils) if coil_scale is None
                      else [float(s) for s in coil_scale])
        if len(self.scale) != len(self.coils):
            raise ValueError("coil_scale needs one entry per coil")
        self.pair_tol = float(pair_tol)
        self.open = {c.index for c, s in zip(self.coils, self.scale) if s == 0.0}
        live = [s for s in self.scale if s != 0.0]
        if not live:
            raise ValueError("every coil is open")
        if not waveform.odd_only:
            raise PairingError(
                "an even current harmonic makes coil k and coil k+3 differ; "
                "three channels cannot carry it (and it makes no mean torque "
                "against an odd-symmetric EMF — it only adds loss and UMP)")

    # -- the physics --------------------------------------------------------
    def electrical_angle(self, theta_mech_deg: float) -> float:
        return (float(theta_mech_deg) * self.pole_pairs + self.gamma_deg
                + self.daxis_deg)

    def coil_currents(self, theta_mech_deg: float) -> Dict[int, float]:
        """Every coil's current in its OWN orientation [A per coil]."""
        te = self.electrical_angle(theta_mech_deg)
        return {c.index: s * self.wave(te + c.phi_deg)
                for c, s in zip(self.coils, self.scale)}

    def currents(self, theta_mech_deg: float) -> Dict[str, float]:
        ic = self.coil_currents(theta_mech_deg)
        out: Dict[str, Optional[float]] = {"A": None, "B": None, "C": None}
        tol = self.pair_tol * max(abs(self.wave.i1_peak), 1.0)
        for c in self.coils:
            if c.index in self.open:
                continue
            v = c.sign * ic[c.index]          # the channel's own orientation
            if out[c.phase] is None:
                out[c.phase] = v
            elif abs(out[c.phase] - v) > tol:
                raise PairingError(
                    f"coil {c.index + 1} needs {v:.6g} A on channel {c.phase} "
                    f"but a partner already set {out[c.phase]:.6g} A")
        return {k: (0.0 if v is None else float(v)) for k, v in out.items()}

    # -- the ExcitationSource interface -------------------------------------
    def mean_over(self, fb) -> Dict[str, float]:
        return self.currents(fb.theta_deg)

    def fundamental(self, theta_deg: float) -> Dict[str, float]:
        return self.currents(theta_deg)

    def nominal_currents(self, theta_deg: float) -> Dict[str, float]:
        return self.currents(theta_deg)

    def settle_policy(self) -> SettlePolicy:
        return SettlePolicy(periods_static=0)

    def on_steps_snapped(self, n_steps_per_period: int):
        return self

    def describe(self, ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {"name": self.name, "series": "I",
                "quantity": "imposed per-coil current [A per coil]",
                "v_phase_peak_V": None, "v_delta_deg": None,
                "pwm": None, "custom_current": None, "bldc": None,
                "per_coil": {"waveform": self.wave.as_dict(),
                             "coil_scale": list(self.scale),
                             "open_coils": sorted(i + 1 for i in self.open),
                             "phasing_deg": [c.phi_deg for c in self.coils]}}


def coil_rms(src: PerCoilCurrentSource, n: int = 720) -> Dict[int, float]:
    """RMS of every coil's current over one electrical period [A]."""
    p = max(1, src.pole_pairs)
    th = np.linspace(0.0, 360.0 / p, n, endpoint=False)
    acc = {c.index: 0.0 for c in src.coils}
    for t in th:
        for k, v in src.coil_currents(float(t)).items():
            acc[k] += v * v
    return {k: math.sqrt(v / n) for k, v in acc.items()}


# ═══════════════════════════════════════════════════════════════════════════
#  AN OPEN COIL
# ═══════════════════════════════════════════════════════════════════════════
@contextlib.contextmanager
def open_coils_layout(open_coils: Iterable[int]):
    """Resolve every winding layout with the given coils' sources removed.

    ``open_coils`` are 0-based coil indices (slots 2j, 2j+1).  Patches
    ``geometry_2d.build_winding_layout`` for the duration — the solver imports
    it at call time, so nothing else needs to know.  Direction 0 gives the
    open coil's slots J = 0 (build_materials multiplies by it).  Only a FULL
    RING may be solved this way: a sector's symmetry check would refuse the
    unbalanced layout, which is the right answer.
    """
    from motor_ai_sim.simulation import geometry_2d as g2
    dead = {int(j) for j in open_coils}
    orig = g2.build_winding_layout

    def _patched(num_slots, num_pole_pairs, single_layer=True, layout_str=None):
        lay = list(orig(num_slots, num_pole_pairs, single_layer=single_layer,
                        layout_str=layout_str))
        for j in dead:
            for s in (2 * j, 2 * j + 1):
                if 0 <= s < len(lay):
                    lay[s] = (lay[s][0], 0)
        return lay

    g2.build_winding_layout = _patched
    try:
        yield
    finally:
        g2.build_winding_layout = orig


# ═══════════════════════════════════════════════════════════════════════════
#  AIR-GAP FORCES (UMP) FROM THE SOLVER'S OWN FRAMES
# ═══════════════════════════════════════════════════════════════════════════
def _gap_annulus(P: np.ndarray, T: np.ndarray, tags: np.ndarray,
                 stator_el: np.ndarray) -> Tuple[np.ndarray, float, float]:
    """Stator-side elements lying entirely between the band edge and the bore.

    The stator half of the sliding-band mesh starts at the band's outer ring;
    everything of it below the first stator-iron node is the stator-side gap
    ring (tagged as outer/gap air by the mesher, which is why no tag is
    trusted here — only radii and "no iron, no copper").  It does not rotate,
    so its field needs no frame change.
    """
    r = np.hypot(P[0], P[1])
    st = np.where(stator_el)[0]
    solid = st[(tags[st] == 1) | (tags[st] >= 100)]
    if not solid.size:
        raise ValueError("no stator iron/copper in the frame mesh")
    r_bore = float(r[T[:, solid]].min())
    r_band = float(r[T[:, st]].min())
    # node radii on "the bore circle" scatter by mesher round-off: allow 2 %
    # of the ring's own thickness, far below one element layer
    tol = 0.02 * max(r_bore - r_band, 1e-12)
    rmax = r[T[:, st]].max(axis=0)
    ok = rmax <= r_bore + tol
    sel = st[ok & (tags[st] != 1) & (tags[st] < 100)]
    if not sel.size:
        raise ValueError("no stator-side gap air below the bore")
    return sel, r_band, float(r[T[:, sel]].max())


def gap_forces(result: Dict[str, Any], stack_length_m: float) -> Dict[str, Any]:
    """Net radial force (UMP) and Maxwell torque per returned frame.

    ``result`` is a transient payload solved with ``return_frames`` = the step
    count on a FULL RING.  Uses the stator half of the mesh (it does not move),
    the per-element B the solver already averaged, and the Arkkio volume form

        F = L/(r_o-r_i) ∫_S [ (B·r̂) B − ½|B|² r̂ ] / μ0 dS
        T = L/(r_o-r_i) ∫_S r (B·r̂)(B·θ̂) / μ0 dS

    over the pure-air annulus between the stator-side band edge and the bore.
    The torque is returned as the method's own cross-check against the
    solver's Maxwell series.
    """
    frames = result.get("frames") or []
    fm = result.get("frames_mesh") or {}
    if not frames or not fm:
        raise ValueError("the run carries no frames (return_frames=0?)")
    T = np.asarray(fm["T"], int)
    tags = np.asarray(fm["tags"], int)
    nsn = int(fm["nsn"])
    # stator elements are those whose nodes are all stator nodes (the rotor
    # block of P_mm is rotated per frame for display; the stator is not)
    stator_el = np.all(T < nsn, axis=0)
    P0 = np.asarray(frames[0]["P_mm"], float) * 1e-3
    sel, r_i, r_o = _gap_annulus(P0, T, tags, stator_el)
    tri = P0[:, T[:, sel]]                       # (2, 3, E)
    area = 0.5 * np.abs((tri[0, 1] - tri[0, 0]) * (tri[1, 2] - tri[1, 0])
                        - (tri[0, 2] - tri[0, 0]) * (tri[1, 1] - tri[1, 0]))
    cx, cy = tri[0].mean(axis=0), tri[1].mean(axis=0)
    rc = np.hypot(cx, cy)
    ux, uy = cx / rc, cy / rc                    # r̂
    k = float(stack_length_m) / (MU0 * (r_o - r_i))
    out = {"r_in_mm": r_i * 1e3, "r_out_mm": r_o * 1e3, "n_el": int(sel.size),
           "area_mm2": float(area.sum()) * 1e6,
           "area_annulus_mm2": math.pi * (r_o ** 2 - r_i ** 2) * 1e6,
           "Fx_N": [], "Fy_N": [], "T_Nm": [], "step_idx": []}
    for f in frames:
        bx = np.asarray(f["Bx"], float)[sel]
        by = np.asarray(f["By"], float)[sel]
        br = bx * ux + by * uy
        bt = -bx * uy + by * ux
        b2 = bx * bx + by * by
        fx = k * float(np.sum((br * bx - 0.5 * b2 * ux) * area))
        fy = k * float(np.sum((br * by - 0.5 * b2 * uy) * area))
        tq = k * float(np.sum(rc * br * bt * area))
        out["Fx_N"].append(fx); out["Fy_N"].append(fy); out["T_Nm"].append(tq)
        out["step_idx"].append(int(f.get("step_idx", len(out["step_idx"]))))
    F = np.hypot(out["Fx_N"], out["Fy_N"])
    out["F_mag_mean_N"] = float(F.mean())
    out["F_mag_max_N"] = float(F.max())
    out["F_vec_mean_N"] = float(math.hypot(np.mean(out["Fx_N"]), np.mean(out["Fy_N"])))
    out["T_mean_Nm"] = float(np.mean(out["T_Nm"]))
    return out
