"""Lateral rotordynamics of the shaft — whirl, Campbell, critical speeds.

Written 2026-09-05 alongside ``modal.py`` for the user's request: "нам нужно
сделать ещё модальный анализ, чтобы понять все частоты — это очень важно для
20000 rpm".  The 2-D modal solve answers "does the iron ring at an excitation
frequency"; THIS module answers the question that actually decides whether a
machine may be run at 20 000 rpm: where are the shaft's bending critical
speeds, and is the rated speed clear of them.

WHAT IS SOLVED
--------------
A 1-D TIMOSHENKO beam of the shaft, four dofs per node — two lateral
displacements (x, y) and the two slopes (dx/dz, dy/dz) — carrying:

  * the shaft tube itself (mass, bending stiffness, shear flexibility, rotary
    inertia),
  * the rotor stack over its own length as ADDED mass, diametral inertia and
    polar inertia per metre, taken from the same 2-D cross-section the stress
    and modal solves mesh (so the magnets, the sleeve and every pocket are in
    the number, not a cylinder approximation),
  * two isotropic bearings as radial springs, and
  * the GYROSCOPIC coupling  M q'' + Omega G q' + K q = 0, which is what splits
    every mode into a forward and a backward whirl and makes the answer a
    Campbell diagram rather than a list of frequencies.

The eigenproblem is solved as a first-order pencil at each speed; there is no
damping, so the eigenvalues are purely imaginary and the whirl frequencies are
their imaginary parts.

WHAT IS ASSUMED — and none of it comes from the motor config
------------------------------------------------------------
The cross-section is the machine's; the SHAFT LINE is not.  Nothing in
``motor_config.yaml`` says how far apart the bearings sit, how much shaft hangs
past them, or how stiff the bearings are — those are the drawing, not the
electromagnetics.  Every one of them is an INPUT with a flagged default:

  * bearing span, overhangs, stack position along the span,
  * shaft OD / ID outside the stack (defaulting to the tube the 2-D geometry
    already has: OD = 2*rotor_inner_radius, ID = 2*shaft_inner_radius),
  * bearing radial stiffness (default 2e8 N/m — a mid-size preloaded angular
    contact pair; a real bearing is 1e8...1e9 and it is speed and load
    dependent),
  * how much of the lamination stack's own bending stiffness counts
    (default ZERO — laminations are not bonded axially, so the conservative
    and standard first pass gives the stack mass but no stiffness; raising the
    fraction toward 1 shows how much the answer depends on it).

Also assumed: isotropic supports (so backward whirl is not excited by
unbalance), no bearing damping, no foundation flexibility, no torsional or
axial modes, and a shaft that is straight and axisymmetric.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

_log = logging.getLogger(__name__)

#: Default bearing radial stiffness [N/m].  Flagged "assumed" everywhere it is
#: shown: a critical speed moves as sqrt(k) below the shaft's own stiffness, so
#: this number is the single biggest lever on the answer.
DEFAULT_BEARING_K = 2.0e8


# ---------------------------------------------------------------------------
# Section properties
# ---------------------------------------------------------------------------

@dataclass
class BeamSection:
    """One uniform stretch of shaft, in SI, per metre of length."""
    m_per_l: float       # kg/m   translational mass
    EI: float            # N·m²   bending stiffness
    kGA: float           # N      shear rigidity kappa*G*A; <= 0 means Euler
    Id_per_l: float      # kg·m   diametral rotary inertia per metre
    Ip_per_l: float      # kg·m   polar rotary inertia per metre


def cowper_kappa(nu: float, id_over_od: float) -> float:
    """Cowper's shear correction factor for a hollow circular section.

    Collapses to the familiar 6(1+nu)/(7+6nu) = 0.886 (nu = 0.3) for a solid
    shaft.  Only matters on a stubby rotor; on a slender one Phi is ~1e-4 and
    the Timoshenko element becomes the Euler one.
    """
    m2 = float(id_over_od) ** 2
    a = (1.0 + m2) ** 2
    return 6.0 * (1.0 + nu) * a / ((7.0 + 6.0 * nu) * a + (20.0 + 12.0 * nu) * m2)


def tube_section(od_m: float, id_m: float, E: float, nu: float,
                 rho: float) -> BeamSection:
    """The beam properties of a plain circular tube."""
    if od_m <= 0 or id_m < 0 or id_m >= od_m:
        raise ValueError(f"shaft OD must exceed ID (got OD {od_m*1e3:.2f} mm, "
                         f"ID {id_m*1e3:.2f} mm)")
    A = math.pi / 4.0 * (od_m ** 2 - id_m ** 2)
    I = math.pi / 64.0 * (od_m ** 4 - id_m ** 4)
    G = E / (2.0 * (1.0 + nu))
    kappa = cowper_kappa(nu, id_m / od_m)
    return BeamSection(m_per_l=rho * A, EI=E * I, kGA=kappa * G * A,
                       Id_per_l=rho * I, Ip_per_l=2.0 * rho * I)


# ---------------------------------------------------------------------------
# Element matrices  (one plane: dofs w1, theta1, w2, theta2)
# ---------------------------------------------------------------------------

def _phi(sec: BeamSection, L: float) -> float:
    """The Timoshenko shear parameter 12EI/(kGA L^2); 0 = Euler-Bernoulli."""
    if sec.kGA <= 0 or not np.isfinite(sec.kGA):
        return 0.0
    return 12.0 * sec.EI / (sec.kGA * L * L)


def _k_plane(sec: BeamSection, L: float) -> np.ndarray:
    p = _phi(sec, L)
    c = sec.EI / ((1.0 + p) * L ** 3)
    return c * np.array([
        [12.0, 6 * L, -12.0, 6 * L],
        [6 * L, (4 + p) * L * L, -6 * L, (2 - p) * L * L],
        [-12.0, -6 * L, 12.0, -6 * L],
        [6 * L, (2 - p) * L * L, -6 * L, (4 + p) * L * L]])


def _mt_plane(m_per_l: float, sec: BeamSection, L: float) -> np.ndarray:
    """Translational consistent mass (Przemieniecki), Timoshenko-corrected."""
    p = _phi(sec, L)
    a1 = 13 / 35 + 7 * p / 10 + p * p / 3
    a2 = 11 / 210 + 11 * p / 120 + p * p / 24
    a3 = 9 / 70 + 3 * p / 10 + p * p / 6
    a4 = 13 / 420 + 3 * p / 40 + p * p / 24
    a5 = 1 / 105 + p / 60 + p * p / 120
    a6 = 1 / 140 + p / 60 + p * p / 120
    c = m_per_l * L / (1.0 + p) ** 2
    return c * np.array([
        [a1, a2 * L, a3, -a4 * L],
        [a2 * L, a5 * L * L, a4 * L, -a6 * L * L],
        [a3, a4 * L, a1, -a2 * L],
        [-a4 * L, -a6 * L * L, -a2 * L, a5 * L * L]])


def _mr_plane(I_per_l: float, sec: BeamSection, L: float) -> np.ndarray:
    """Rotary inertia matrix for a given inertia per unit length.

    Called with the DIAMETRAL inertia to build the mass matrix and with the
    POLAR one to build the gyroscopic matrix — they are the same shape function
    integral, which is exactly why the two are assembled from one routine.
    """
    p = _phi(sec, L)
    b1 = 6 / 5
    b2 = 1 / 10 - p / 2
    b3 = 2 / 15 + p / 6 + p * p / 3
    b4 = 1 / 30 + p / 6 - p * p / 6
    c = I_per_l / ((1.0 + p) ** 2 * L)
    return c * np.array([
        [b1, b2 * L, -b1, b2 * L],
        [b2 * L, b3 * L * L, -b2 * L, -b4 * L * L],
        [-b1, -b2 * L, b1, -b2 * L],
        [b2 * L, -b4 * L * L, -b2 * L, b3 * L * L]])


# dof layout per node: 0 = x, 1 = y, 2 = dx/dz, 3 = dy/dz
_XPLANE = (0, 2)      # (translation, slope) of the x-z plane
_YPLANE = (1, 3)


def _scatter(dst: np.ndarray, blk: np.ndarray, dofs: Sequence[int]) -> None:
    ix = np.asarray(dofs, dtype=int)
    dst[np.ix_(ix, ix)] += blk


def assemble_beam(z: np.ndarray, sections: Sequence[BeamSection],
                  bearings: Sequence[Tuple[int, float]]
                  ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(M, K, G) of the whole shaft line, 4 dofs per node.

    ``z`` are the node stations in metres (ascending), ``sections`` one entry
    per element, ``bearings`` a list of (node index, radial stiffness N/m).
    ``G`` is the SKEW gyroscopic matrix that multiplies Omega (rad/s), not
    Omega*G itself — the Campbell sweep re-scales it at every speed instead of
    re-assembling.
    """
    z = np.asarray(z, dtype=float)
    n = z.size
    if n < 2 or len(sections) != n - 1:
        raise ValueError("one section per element is required")
    nd = 4 * n
    M = np.zeros((nd, nd))
    K = np.zeros((nd, nd))
    G = np.zeros((nd, nd))

    for e, sec in enumerate(sections):
        L = float(z[e + 1] - z[e])
        if L <= 0:
            raise ValueError("shaft stations must strictly increase")
        kp = _k_plane(sec, L)
        mt = _mt_plane(sec.m_per_l, sec, L)
        mrd = _mr_plane(sec.Id_per_l, sec, L)
        mrp = _mr_plane(sec.Ip_per_l, sec, L)
        for (w, th) in (_XPLANE, _YPLANE):
            d = [4 * e + w, 4 * e + th, 4 * (e + 1) + w, 4 * (e + 1) + th]
            _scatter(K, kp, d)
            _scatter(M, mt + mrd, d)
        # Gyroscopic coupling: the polar-inertia term links the SLOPE of one
        # plane to the slope rate of the other.  Signs are opposite across the
        # two planes, which is what makes G skew — and skewness is asserted
        # below rather than trusted, because a sign slip here silently turns
        # forward whirl into backward whirl and moves every critical speed.
        dx = [4 * e + _XPLANE[0], 4 * e + _XPLANE[1],
              4 * (e + 1) + _XPLANE[0], 4 * (e + 1) + _XPLANE[1]]
        dy = [4 * e + _YPLANE[0], 4 * e + _YPLANE[1],
              4 * (e + 1) + _YPLANE[0], 4 * (e + 1) + _YPLANE[1]]
        G[np.ix_(dx, dy)] += mrp
        G[np.ix_(dy, dx)] -= mrp

    for (node, k) in bearings:
        if k <= 0:
            continue
        K[4 * int(node) + 0, 4 * int(node) + 0] += float(k)
        K[4 * int(node) + 1, 4 * int(node) + 1] += float(k)

    assert np.allclose(G, -G.T, atol=1e-9 * max(np.abs(G).max(), 1.0)), \
        "the gyroscopic matrix must be skew-symmetric"
    return M, K, G


# ---------------------------------------------------------------------------
# Whirl
# ---------------------------------------------------------------------------

#: Sign of  sum_j Im(conj(q_xj) q_yj)  for a FORWARD whirl orbit, with the
#: gyroscopic matrix ``assemble_beam`` builds.  x = cos(wt), y = sin(wt) is a
#: counter-clockwise (forward) orbit and gives q = (1, -i), whose invariant is
#: negative — so forward is the NEGATIVE sign here.  Pinned by
#: ``test_gyroscopic_coupling_splits_the_whirl_at_speed``: if this convention
#: were wrong the two branches would swap and every critical speed with them.
_FORWARD_SIGN = -1.0


class WhirlSolver:
    """Whirl frequencies of one shaft line, at any speed.

    The pencil  [[0, I], [-K, -Omega G]] z = lambda [[I, 0], [0, M]] z  is
    reduced ONCE to the standard form  z' = [[0, I], [-M^-1 K, -Omega M^-1 G]] z
    by factorising M up front.  A Campbell sweep is a hundred of these solves
    and the standard eigenvalue problem is twice the speed of the generalised
    one; M is inverted once instead of being re-factorised at every speed.

    Forward and backward are told apart from the ORBIT of each eigenvector, not
    from where the frequency lands in the sorted list.  Sorting and pairing by
    adjacency reads correctly until two branches cross — and they do cross: on
    the live machine the first forward branch climbs past the second backward
    one well inside the search range, after which every "forward" curve is part
    of one mode and part of another, and the critical-speed hunt brackets a
    crossing that does not exist.
    """

    def __init__(self, M: np.ndarray, K: np.ndarray, G: np.ndarray):
        self.n = M.shape[0]
        self.MK = np.linalg.solve(M, K)
        self.MG = np.linalg.solve(M, G)
        self._I = np.eye(self.n)
        self._Z = np.zeros((self.n, self.n))
        self._cache: Dict[float, Tuple[np.ndarray, np.ndarray]] = {}

    def _solve(self, omega: float) -> Tuple[np.ndarray, np.ndarray]:
        """(frequencies Hz ascending, whirl sense +1 forward / -1 backward).

        The model is undamped, so every eigenvalue is a conjugate pair on the
        imaginary axis and one of each pair carries all the information: the
        half-plane Im lambda > 0 is taken, which leaves exactly one entry per
        whirl mode.  The sense is the sign of the orbit invariant
        ``sum_j Im(conj(q_x) q_y)`` over the lateral dofs of the displacement
        half of the state vector — positive area one way round, negative the
        other.
        """
        key = round(float(omega), 9)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        A = np.block([[self._Z, self._I],
                      [-self.MK, -float(omega) * self.MG]])
        lam, vec = np.linalg.eig(A)
        ok = np.isfinite(lam) & (np.imag(lam) > 0)
        lam, vec = lam[ok], vec[:, ok]
        f = np.imag(lam) / (2.0 * math.pi)
        q = vec[:self.n, :]
        inv = np.imag(np.conj(q[0::4, :]) * q[1::4, :]).sum(axis=0)
        o = np.argsort(f)
        out = (f[o], np.where(inv[o] * _FORWARD_SIGN > 0, 1.0, -1.0))
        if len(self._cache) > 512:
            self._cache.clear()
        self._cache[key] = out
        return out

    def freqs(self, omega: float, n_modes: int = 6) -> np.ndarray:
        """The lowest ``n_modes`` whirl frequencies [Hz] at spin speed omega."""
        return self._solve(omega)[0][:n_modes]

    def branches(self, rpm: float, n_modes: int = 6
                 ) -> Tuple[np.ndarray, np.ndarray]:
        """(backward, forward) — ``n_modes // 2`` of each, ascending.

        At standstill the two senses are degenerate (the same frequency twice)
        and the eigenvectors of a degenerate pair are an arbitrary combination,
        so the orbit invariant there is numerical dust.  It costs nothing to be
        right anyway: the sorted list is paired by adjacency, which at zero
        speed is exact because the pair IS one frequency, and within a pair the
        gyroscopic term lifts the forward branch and drops the backward one, so
        the lower of the two is the backward one.

        The adjacency fallback used to fire only when the sense split came out
        UNEVEN, and that missed the case this rotor actually produces: at 0 rpm
        the dust came out 3 forward / 1 backward over the first four pairs, i.e.
        even enough to pass, with mode 2's frequency handed to the forward
        branch twice and mode 3's to the backward branch — a Campbell plot whose
        backward curve started at 1200 Hz and dropped to 643 Hz one step later.
        So the fallback is now triggered by the SYMPTOM instead: on an isotropic
        rotor the k-th forward frequency can never sit below the k-th backward
        one, and a split that says otherwise has scrambled the modes.  Away from
        the degenerate region the check never fires and the sense split stands.
        (Found 2026-09-06 while making the Mechanical tab keep its results.)
        """
        omega = float(rpm) * 2.0 * math.pi / 60.0
        f, sense = self._solve(omega)
        k = max(1, int(n_modes) // 2)
        g = f[:2 * k].reshape(-1, 2)
        adj_bw, adj_fw = g[:, 0], g[:, 1]
        # AT STANDSTILL the pairing is by adjacency, always (2026-09-09).  With
        # no gyroscopic term the eigenproblem is exactly degenerate and the
        # sense of each pair is dust — and dust can come out EVEN: on the G2-L40
        # (Ø106 mm tube shaft, 40 mm stack; modes at 1161, 1161, 2290, 2290 Hz)
        # it labelled both copies of the first mode "backward" and both copies
        # of the second "forward", a split the check below cannot see because
        # every "forward" frequency was above every "backward" one.  The
        # Campbell plot then started with a forward branch at 2290 Hz that
        # "fell" to 1165 Hz at speed (it was the second mode handed to the first
        # branch) and no critical was found.  Adjacency is exact here by
        # construction: each pair IS one frequency.
        if omega == 0.0:
            return adj_bw.copy(), adj_fw.copy()
        fw, bw = f[sense > 0], f[sense < 0]
        if fw.size < k or bw.size < k:
            return adj_bw, adj_fw
        bw, fw = bw[:k], fw[:k]
        if np.any(fw < bw - 1e-9 * np.maximum(np.abs(fw), 1.0)):
            return adj_bw, adj_fw
        # The same scramble just OFF standstill: while every pair's gyroscopic
        # split is still below the eigensolver's noise the sense is still dust,
        # and adjacency is still exact.  Only then — once the branches have
        # separated, adjacency is the WRONG pairing (two forward branches that
        # cross are sorted neighbours of the wrong partners), so the sense
        # split, with the check above, must stand.
        split = adj_fw - adj_bw
        if np.all(split <= 1e-6 * np.maximum(np.abs(adj_fw), 1.0)):
            return adj_bw.copy(), adj_fw.copy()
        return bw, fw


def whirl_frequencies(M: np.ndarray, K: np.ndarray, G: np.ndarray,
                      omega: float, n_modes: int = 6) -> np.ndarray:
    """One-shot convenience wrapper around :class:`WhirlSolver`."""
    return WhirlSolver(M, K, G).freqs(omega, n_modes)


# ---------------------------------------------------------------------------
# Campbell + critical speeds
# ---------------------------------------------------------------------------

#: Speeds the Campbell sweep is evaluated at.  Module-level because the
#: progress budget of a /critical_speeds request is built from it in
#: ``routes/mechanical.py`` before the solve starts — the bar's total and the
#: sweep's point count must be the SAME number, not two literals that agree
#: today.
N_CAMPBELL_POINTS = 41


def campbell_and_criticals(ws: "WhirlSolver", rpm_max: float,
                           n_points: int = 41, n_modes: int = 6,
                           want: int = 3, progress=None
                           ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """One sweep, two answers: the Campbell curves and the critical speeds.

    Unbalance is a synchronous, FORWARD-rotating force, so on an isotropic
    rotor it excites the forward branches and not the backward ones: a critical
    speed is a crossing of a forward curve with the 1x line, f = rpm/60.  The
    backward crossings are reported too — flagged as not excited by unbalance —
    because an anisotropic mount or a bent shaft will find them.

    The sweep that draws the plot is the same sweep that brackets the crossings;
    solving it twice would double the cost of the slowest thing this module
    does for no extra information.

    ``progress`` (optional, the shared contract in ``motor_ai_sim.progress``) is
    called ``progress(done, total, phase)`` once per swept speed.  This sweep IS
    the wall clock of a critical-speed request — ``n_points`` dense eigenvalue
    solves, plus twelve more per crossing found — so it is the one place worth
    reporting from; the bisections that follow are reported as a single phase
    because their count is not knowable until the crossings are.
    """
    rpms = np.linspace(0.0, float(rpm_max), int(n_points))
    bw: List[List[float]] = []
    fw: List[List[float]] = []
    _np_tot = int(rpms.size)
    for _i, r in enumerate(rpms):
        if progress is not None:
            progress(_i, _np_tot,
                     f"Campbell sweep — speed {_i + 1}/{_np_tot}")
        b, f_ = ws.branches(float(r), n_modes)
        bw.append([float(x) for x in b])
        fw.append([float(x) for x in f_])
    if progress is not None:
        progress(_np_tot, _np_tot, "critical-speed bisection")
    nb = min(len(c) for c in fw) if fw else 0

    def _cross(forward: bool, k: int) -> List[float]:
        cur = fw if forward else bw
        g = np.array([c[k] - r / 60.0 for c, r in zip(cur, rpms)])
        hits: List[float] = []
        for i in range(g.size - 1):
            if g[i] * g[i + 1] >= 0:
                continue
            lo, hi = float(rpms[i]), float(rpms[i + 1])
            # 12 bisections on one sweep step is well under 1 rpm on a
            # 20 000 rpm machine; the curves are smooth, and every extra step
            # is another dense eigenvalue solve for digits nobody reads.
            for _ in range(12):
                mid = 0.5 * (lo + hi)
                b_, f_ = ws.branches(mid, n_modes)
                v = (f_ if forward else b_)[k] - mid / 60.0
                if v * g[i] > 0:
                    lo = mid
                else:
                    hi = mid
            hits.append(0.5 * (lo + hi))
        return hits

    fwd: List[Dict[str, Any]] = []
    bwd: List[Dict[str, Any]] = []
    for k in range(nb):
        for r in _cross(True, k):
            fwd.append({"rpm": float(r), "hz": float(r) / 60.0,
                        "whirl": "forward", "mode": k + 1,
                        "excited_by_unbalance": True})
        for r in _cross(False, k):
            bwd.append({"rpm": float(r), "hz": float(r) / 60.0,
                        "whirl": "backward", "mode": k + 1,
                        "excited_by_unbalance": False})
    fwd.sort(key=lambda d: d["rpm"])
    bwd.sort(key=lambda d: d["rpm"])
    keep = fwd[:want] + bwd[:want]
    keep.sort(key=lambda d: d["rpm"])
    cam = {"rpm": [float(r) for r in rpms], "backward": bw, "forward": fw}
    return cam, keep


# ---------------------------------------------------------------------------
# Building the shaft line from the machine
# ---------------------------------------------------------------------------

@dataclass
class ShaftInputs:
    """The drawing dimensions the motor config does not carry.  All mm / SI."""
    bearing_span_mm: float = 250.0
    stack_length_mm: float = 0.0          # 0 -> the config's motor_length
    stack_offset_mm: float = 0.0          # + moves the stack toward bearing B
    overhang_a_mm: float = 30.0
    overhang_b_mm: float = 30.0
    shaft_od_mm: float = 0.0              # 0 -> 2*rotor_inner_radius
    shaft_id_mm: float = -1.0             # <0 -> 2*shaft_inner_radius
    bearing_k_n_per_m: float = DEFAULT_BEARING_K
    stack_stiffness_fraction: float = 0.0
    #: Element count of the beam.  28 puts the first three criticals within a
    #: fraction of a percent of a converged mesh and keeps the dense 2N pencil
    #: that every Campbell point solves down to ~15 ms.
    elements: int = 28


@dataclass
class StackProps:
    """What the 2-D rotor cross-section adds to the beam, per metre of stack."""
    m_per_l: float = 0.0        # kg/m, EXCLUDING the shaft tube (the beam has it)
    Ip_per_l: float = 0.0       # kg·m
    Id_per_l: float = 0.0       # kg·m
    EI_core: float = 0.0        # N·m², the lamination's own bending stiffness
    m_shaft_per_l: float = 0.0  # kg/m of the tube, reported for the audit
    parts: Dict[str, float] = field(default_factory=dict)


def stack_properties(polys: dict, assignments: Optional[dict],
                     material_overrides: Optional[dict] = None,
                     mesh_size_mm: float = 3.0) -> StackProps:
    """Mass and inertia of the rotor cross-section, from the SAME mesh.

    The shaft tube is excluded from the added mass because the beam already
    carries a tube of its own; double-counting it would drop the criticals by
    the square root of the error and nobody would see why.
    """
    from motor_ai_sim.simulation.mechanical.contact import PART_ROTOR, PART_SHAFT
    from motor_ai_sim.simulation.mechanical.rotor_stress import (
        PART_NAMES, build_rotor_mesh, resolve_part_materials)

    rm = build_rotor_mesh(polys, mesh_size_mm=mesh_size_mm)
    mesh, part_tri = rm.mesh, rm.part_tri
    mech = resolve_part_materials(assignments, polys.get("sleeve") is not None,
                                  material_overrides)
    p = mesh.p.T
    v0, v1, v2 = p[mesh.t[0]], p[mesh.t[1]], p[mesh.t[2]]
    area = 0.5 * np.abs((v1[:, 0] - v0[:, 0]) * (v2[:, 1] - v0[:, 1])
                        - (v2[:, 0] - v0[:, 0]) * (v1[:, 1] - v0[:, 1]))
    cen = (v0 + v1 + v2) / 3.0
    r2 = cen[:, 0] ** 2 + cen[:, 1] ** 2

    out = StackProps()
    for pid, name in PART_NAMES.items():
        m = part_tri == pid
        if not m.any():
            continue
        pm = mech.get(name)
        if pm is None:
            continue
        mass = float((area[m] * pm.density).sum())
        out.parts[name] = mass
        if pid == PART_SHAFT:
            out.m_shaft_per_l = mass
            continue
        out.m_per_l += mass
        out.Ip_per_l += float((area[m] * pm.density * r2[m]).sum())
        if pid == PART_ROTOR:
            # Only the lamination is a candidate bending member; magnets sit in
            # pockets and the sleeve is a hoop, neither carries axial bending.
            out.EI_core += pm.E * float((area[m] * cen[m, 1] ** 2).sum())
    # For an axisymmetric section the diametral inertia of the cross-section is
    # half the polar one; the axial spread of the stack is carried by the beam
    # kinematics, not by this term.
    out.Id_per_l = 0.5 * out.Ip_per_l
    return out


def build_shaft_line(sp: StackProps, shaft: BeamSection, inp: ShaftInputs,
                     stack_len_m: float) -> Tuple[np.ndarray,
                                                  List[BeamSection],
                                                  List[Tuple[int, float]],
                                                  Dict[str, Any]]:
    """(node stations, sections, bearings, layout) — with loud validation.

    The user is an engineer typing a drawing into a form; a shaft line that
    cannot exist must say which number is impossible, not solve a machine that
    was never built.
    """
    span = float(inp.bearing_span_mm) * 1e-3
    oa = float(inp.overhang_a_mm) * 1e-3
    ob = float(inp.overhang_b_mm) * 1e-3
    if span <= 0:
        raise ValueError("bearing span must be greater than zero")
    if oa < 0 or ob < 0:
        raise ValueError("an overhang cannot be negative")
    if stack_len_m <= 0:
        raise ValueError("the rotor stack length is zero — set motor_length, "
                         "or pass stack_length_mm")

    z_b1, z_b2 = oa, oa + span
    total = oa + span + ob
    z_c = 0.5 * (z_b1 + z_b2) + float(inp.stack_offset_mm) * 1e-3
    z_s0, z_s1 = z_c - 0.5 * stack_len_m, z_c + 0.5 * stack_len_m
    if z_s0 < -1e-9 or z_s1 > total + 1e-9:
        raise ValueError(
            f"the {stack_len_m*1e3:.0f} mm stack centred at "
            f"{z_c*1e3:.0f} mm runs off the {total*1e3:.0f} mm shaft "
            f"({z_s0*1e3:.0f} … {z_s1*1e3:.0f} mm) — lengthen the span or the "
            "overhangs, or move the stack")

    breaks = sorted({0.0, z_b1, z_b2, total,
                     max(0.0, z_s0), min(total, z_s1)})
    n_el = max(8, int(inp.elements))
    # Subdivide every stretch in proportion to its length, so the stack and the
    # overhangs are both resolved and no element straddles a property change.
    z: List[float] = [breaks[0]]
    lens = [breaks[i + 1] - breaks[i] for i in range(len(breaks) - 1)]
    tot = sum(lens) or 1.0
    for i, L in enumerate(lens):
        k = max(1, int(round(n_el * L / tot)))
        z += list(np.linspace(breaks[i], breaks[i + 1], k + 1)[1:])
    zz = np.asarray(z, dtype=float)

    sections: List[BeamSection] = []
    for e in range(zz.size - 1):
        mid = 0.5 * (zz[e] + zz[e + 1])
        if z_s0 - 1e-12 <= mid <= z_s1 + 1e-12:
            sections.append(BeamSection(
                m_per_l=shaft.m_per_l + sp.m_per_l,
                EI=shaft.EI + float(inp.stack_stiffness_fraction) * sp.EI_core,
                kGA=shaft.kGA,
                Id_per_l=shaft.Id_per_l + sp.Id_per_l,
                Ip_per_l=shaft.Ip_per_l + sp.Ip_per_l))
        else:
            sections.append(shaft)

    def _near(zt: float) -> int:
        return int(np.argmin(np.abs(zz - zt)))

    bearings = [(_near(z_b1), float(inp.bearing_k_n_per_m)),
                (_near(z_b2), float(inp.bearing_k_n_per_m))]
    layout = {
        "total_length_mm": total * 1e3,
        "bearing_a_mm": z_b1 * 1e3, "bearing_b_mm": z_b2 * 1e3,
        "stack_from_mm": z_s0 * 1e3, "stack_to_mm": z_s1 * 1e3,
        "n_nodes": int(zz.size), "n_elements": int(zz.size - 1),
    }
    return zz, sections, bearings, layout


def solve_rotordynamics(polys: dict, params: dict,
                        assignments: Optional[dict],
                        inp: ShaftInputs,
                        rpm_rated: float,
                        material_overrides: Optional[dict] = None,
                        n_modes: int = 6,
                        rpm_max_factor: float = 1.3,
                        mesh_size_mm: float = 3.0,
                        f_switch: Optional[float] = None,
                        progress=None) -> Dict[str, Any]:
    """The full shaft report: Campbell, critical speeds and the margins.

    ``progress`` (2026-09-07) is the shared live-progress callback (see
    ``motor_ai_sim.progress``).  The budget is the honest shape of the work: one
    step for the shaft line (which meshes the cross-section to weigh the stack),
    one for the beam assembly, one per swept speed, and one for the bisection
    pass — the Campbell sweep is nearly all of the wall clock, so that is where
    the bar spends its length.
    """
    from motor_ai_sim.progress import StepLedger
    from motor_ai_sim.simulation.mechanical.modal import excitation_orders
    from motor_ai_sim.simulation.mechanical.rotor_stress import part_mech

    from motor_ai_sim.materials import DEFAULT_PART_MATERIAL

    shaft_name = (assignments or {}).get("shaft") \
        or DEFAULT_PART_MATERIAL.get("shaft")
    if not shaft_name:
        raise ValueError("no materials.shaft in the config — the beam has no "
                         "modulus or density to run on")
    pm = part_mech("shaft", str(shaft_name), material_overrides)

    od = float(inp.shaft_od_mm) if inp.shaft_od_mm > 0 else \
        2.0 * float(params.get("rotor_inner_radius") or 0.0)
    idm = float(inp.shaft_id_mm) if inp.shaft_id_mm >= 0 else \
        2.0 * float(params.get("shaft_inner_radius") or 0.0)
    shaft = tube_section(od * 1e-3, idm * 1e-3, pm.E, pm.nu, pm.density)

    stack_len_mm = float(inp.stack_length_mm) if inp.stack_length_mm > 0 else \
        float(params.get("motor_length") or 0.0)
    _N_SWEEP = N_CAMPBELL_POINTS
    led = StepLedger(progress, total=3 + _N_SWEEP,
                     composition=(f"shaft line + beam assembly + {_N_SWEEP} "
                                  f"Campbell speeds x {int(n_modes)} modes + "
                                  "bisection"))
    led.phase("shaft line (stack properties, section build)")
    sp = stack_properties(polys, assignments, material_overrides,
                          mesh_size_mm=mesh_size_mm)
    zz, sections, bearings, layout = build_shaft_line(
        sp, shaft, inp, stack_len_mm * 1e-3)
    led.at(1, "beam assembly (M, K, G)")
    M, K, G = assemble_beam(zz, sections, bearings)
    led.at(2, "beam assembly (M, K, G)")

    rated = float(rpm_rated or 0.0)
    rpm_plot = max(rated * float(rpm_max_factor), 1.0)
    # The sweep runs well past the plot band: a machine whose third critical
    # sits at 2.4x rated still needs to know where it is, and a curve that
    # stopped at 1.3x would report "none found" instead of a number.  The plot
    # band is returned as `rpm_plot_max` so the panel can shade it.
    rpm_hunt = max(rpm_plot, rated * 3.0, 1.0)
    ws = WhirlSolver(M, K, G)
    cam, crits = campbell_and_criticals(
        ws, rpm_hunt, n_points=_N_SWEEP, n_modes=n_modes, want=3,
        # The sweep counts from 0; it lives at steps 2 … 2 + n_points on the bar.
        progress=lambda d, t, ph=None, c=None: led.at(2 + int(d), ph))

    led.at(3 + _N_SWEEP, "post-processing (margins, verdict)")
    f0 = ws.freqs(0.0, n_modes)
    exc = excitation_orders(rated, int(params.get("num_poles") or 0),
                            int(params.get("num_slots") or 0), f_switch) \
        if rated > 0 else []

    for c in crits:
        c["margin_vs_rated_pct"] = (100.0 * (c["rpm"] - rated) / rated
                                    if rated > 0 else None)
        c["beyond_plot"] = bool(c["rpm"] > rpm_plot)

    fwd = [c for c in crits if c["whirl"] == "forward"]
    first_above = next((c["rpm"] for c in fwd if c["rpm"] > rated), None)
    last_below = max((c["rpm"] for c in fwd if c["rpm"] <= rated), default=None)
    if rated <= 0:
        verdict = "no rated speed given"
    elif last_below is None:
        verdict = ("subcritical — the machine runs below its first forward "
                   "critical speed" if first_above else
                   "no forward critical found in the search range")
    else:
        n_below = sum(1 for c in fwd if c["rpm"] <= rated)
        verdict = (f"supercritical — {n_below} forward critical speed"
                   f"{'s lie' if n_below > 1 else ' lies'} below rated, so the "
                   f"machine must run THROUGH {'them' if n_below > 1 else 'it'} "
                   "on every start")

    return {
        "rated_rpm": rated,
        "overspeed_rpm": rated * 1.2,
        "rpm_plot_max": rpm_plot,
        "rpm_hunt_max": rpm_hunt,
        "natural_hz_at_rest": [float(x) for x in f0],
        "critical_speeds": crits,
        "verdict": verdict,
        "campbell": cam,
        "excitations": exc,
        "layout": layout,
        "shaft": {
            "material": pm.material, "od_mm": od, "id_mm": idm,
            "youngs_modulus_gpa": pm.E / 1e9, "poisson_ratio": pm.nu,
            "density": pm.density,
            "mass_kg_per_m": shaft.m_per_l, "EI_n_m2": shaft.EI,
            "kGA_n": shaft.kGA,
        },
        "stack": {
            "length_mm": stack_len_mm,
            "added_mass_kg_per_m": sp.m_per_l,
            "added_mass_kg": sp.m_per_l * stack_len_mm * 1e-3,
            "polar_inertia_kg_m2": sp.Ip_per_l * stack_len_mm * 1e-3,
            "diametral_inertia_kg_m2": sp.Id_per_l * stack_len_mm * 1e-3,
            "shaft_tube_mass_kg_per_m": sp.m_shaft_per_l,
            "lamination_EI_n_m2": sp.EI_core,
            "stiffness_fraction_used": float(inp.stack_stiffness_fraction),
            "part_mass_kg_per_m": sp.parts,
        },
        "inputs": {
            "bearing_span_mm": inp.bearing_span_mm,
            "overhang_a_mm": inp.overhang_a_mm,
            "overhang_b_mm": inp.overhang_b_mm,
            "stack_offset_mm": inp.stack_offset_mm,
            "shaft_od_mm": od, "shaft_id_mm": idm,
            "bearing_k_n_per_m": inp.bearing_k_n_per_m,
            "stack_stiffness_fraction": inp.stack_stiffness_fraction,
            "assumed": ["bearing_span_mm", "overhang_a_mm", "overhang_b_mm",
                        "stack_offset_mm", "bearing_k_n_per_m",
                        "stack_stiffness_fraction"],
        },
        "assumptions": (
            "Timoshenko beam, isotropic bearings as radial springs, no bearing "
            "damping, no foundation flexibility; lateral modes only (no "
            "torsion, no axial). The stack adds mass and inertia; its own "
            "bending stiffness is counted at the fraction shown (0 = the "
            "conservative default, laminations are not bonded axially). "
            "Every shaft-line dimension is an ASSUMPTION — nothing in the motor "
            "config carries it."),
    }


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
