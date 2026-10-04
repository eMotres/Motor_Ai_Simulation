"""Time-periodic (TDM) steady state of the coupled P2 eddy march (2026-09-30).

WHAT IT SOLVES.  The coupled eddy march (``fem_solver_2d``, BDF2 on a uniform
step, the bordered (A, U) Newton of ``p2_drive.P2Drive.eddy_solve`` per frame)
is marched from a static start until the periodic steady state has settled:
on the L155 614 of 650 frames are warm-up.  This module solves for that
steady state directly.  The unknowns are the N frames of one (half) electrical
period at once, (A_j, U_j), j = 0..N-1, and the BDF2 history of the first two
frames is closed by the (anti)periodicity of the orbit:

    A_{-1} = H(A_{N-1}),   A_{-2} = H(A_{N-2}),

with H the exact map one period back (rotor dofs through the pole-pair image
map, stator dofs unchanged: ``rotor_window.period_shift_map``) or, where the
excitation is half-wave symmetric and the rotor mesh is pole periodic, the map
one HALF period back (rotor dofs one pole back, every value negated): N is then
half the steps.  Every frame's residual is EXACTLY the residual the march's own
Newton drives to 1e-7, so the orbit found here is a fixed point of the march:
marching one period from it reproduces it, which is how the caller reports.

METHOD (chosen from GPU_TDM_STUDY_2026-09-29 section 4): Newton on the whole
space-time system, the linear system solved iteratively in time.

  * Per frame (parallel over frames): the residual, the bordered Jacobian
    M_j = [[P'(K+T+sigma M/dte)P, -B], [-B', diag(S dte)]] and its Cholesky
    factor (one PARDISO handle per frame, the symbolic analysis kept across
    Newton iterations).  This is 85-90 % of the work of a march frame, and here
    the N frames of a period are independent.
  * The frames couple only through sigma*M and G' on the CONDUCTOR dofs (the
    BDF2 history), so the Newton system reduces EXACTLY to the wrap state
    w = (dA_{-2}, dA_{-1}) restricted to the conductor dofs:
        (I - T) w = g,   T = H o (one linear forward sweep of the period)
    (block forward substitution in time = back-solves with the stored
    factors; the sweep costs N back-solves, a few ms each).  This is the
    shooting / Schur-complement form of the cyclic block system; it is solved
    by right-preconditioned GMRES.  The sequential part is back-solves only.
  * COARSE CORRECTION (mandatory: without it the slow modes of T, eigenvalues
    e^{-mu T} near 1 for the L155 shaft wall, tau = 21 periods, cost O(tau/T)
    Krylov iterations).  The multiharmonic coarse space of Kulchytska-Ruchka and
    Schoeps (SIAM J. Sci. Comput. 43(1), 2021) truncated to its 0th harmonic:
    the rotor-frame DC of the solid ring conductors, the same operator as the
    TP-EEC correction of the march (``periodic_accel.dc_error_correction``,
    docs/EDDY_SHAFT_SETTLE_2026-09-29.md).  For a slow mode (I-T)^-1 = 1/x +
    1/2 + O(x), x = mu*T, while the coarse preconditioner applies 1 + 1/x with
    1/x = Jbar^-1 sigmaM / T from ONE static solve with the period-averaged
    Jacobian, projected on the H-invariant (pole-image mean) space and applied
    to the principal BDF2 temporal mode only.  The preconditioned eigenvalue of
    every real mode lies in [1, 1.3].
  * The Newton step is globalised by a backtracking line search on the whole
    space-time residual; converged when EVERY frame meets the march's own
    criterion (rrel < tol, default 1e-7).

WHAT IT IS NOT.  Not a single block direct solve (memory, study section 4.3).
Not PWM (hundreds of steps per period; the carrier is the physics there): the
caller refuses it.  Not the voltage drive yet (the circuit state would join
the wrap), not series strand paths (the bordered matrix is not definite):
refused by the caller, loudly.

The irreversible demag ratchet is not periodic; it stays a pre-pass that the
caller runs ON the orbit (``demag_march``), with the owner's shortcut
(``shortcut_window`` / ``map_br_from_reference``) as an option.
"""
from __future__ import annotations

import ctypes
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.sparse import bmat as _bmat, csr_matrix as _csr, diags as _diags

from motor_ai_sim.simulation.p2_nonlinear import SPD_SYM_RTOL, sym_pattern
from motor_ai_sim.simulation import periodic_accel as _pa

# BDF2 on a uniform step: dA/dt|_k = (A_k - (4 A_{k-1} - A_{k-2})/3) / dte
DTE_FACTOR = 2.0 / 3.0
C_M1 = 4.0 / 3.0
C_M2 = -1.0 / 3.0


EDDY_METHODS = ("tdm", "march")
#: The eddy method every steady-state eddy run uses unless told otherwise
#: (owner 2026-09-30).
DEFAULT_EDDY_METHOD = "tdm"

# BOUNDED INEXACT NEWTON (second Codex review, 2026-10-03, finding 8).  A wrap
# GMRES that stops at its iteration budget above its forcing term eta is still
# a valid inexact-Newton step as long as its TRUE linear residual
# ||g - (I - T) w|| / ||g|| stays below a forcing term < 1: the Newton then
# keeps converging, linearly at that rate (Dembo, Eisenstat & Steihaug, SIAM J.
# Numer. Anal. 19, 1982), and the backtracking line search checks that the
# step lowers the space-time merit.  The step is therefore taken when the true
# residual (recomputed independently of GMRES, see TimePeriodicEddy.solve) is
# <= GMRES_ACCEPT_FACTOR * eta = 0.1 at the default eta = 0.01, and refused
# above (the Newton stops unconverged and the caller marches).  Nothing is
# ACCEPTED on the strength of a GMRES: the orbit is accepted only by its
# nonlinear residual and by the caller's closure march and gates.  Tested in
# tests/test_time_periodic.py (test_bounded_inexact_newton_*).
GMRES_ACCEPT_FACTOR = 10.0

# ── acceptance thresholds (second Codex review, 2026-10-03) ─────────────────
# The owner's accuracy terms are: mean torque <= 1 %, ripple within
# max(0.5 pp, 10 % relative), TOTAL losses <= 5 %.  A TDM result is accepted only
# when a one-period march from the orbit closes on it (CLOSURE_GATE, frozen Br)
# and when the reported period, marched by the production frame loop, stays on
# it (REPORT_GATE, the demag ratchet active as in the march).
#
#  * CLOSURE (frozen Br; measured 3e-7 / 3e-5 pp / 1e-5 / 7e-6 on the 30 mm
#    fixture): mean torque 1e-4 (1/100 of the owner's 1 %), ripple 0.01 pp
#    (1/50 of 0.5 pp), every conductor group's loss 1e-3 of itself (1/50 of 5 %,
#    floor 1e-5 of the whole conductor loss), and the STATE: per conductor group
#    and for BOTH BDF2 history levels at the end of the period, the relative
#    sigma-norm distance between the marched state and the orbit's <= 1e-4.
#    A relative change e of the conductor flux moves the flux linkage, hence the
#    torque, by <= e, so 1e-4 is 1/100 of the torque term.
#  * REPORT (ratchet active, so the window also carries the Br the ratchet still
#    removes, <= the demag settle tolerances; measured <= 3.3e-4 per group on
#    the fixture's demag case): the owner's terms / 10 as before (torque 1e-3,
#    ripple 0.05 pp, group loss 5e-3, floor 1e-4) and the state per group, both
#    levels, at the end of EVERY reported period, <= 1e-3 (the torque term / 10).
# Distance to the true periodic orbit: a closure defect d of a mode that decays
# as exp(-T/tau) per period means a distance up to d / (1 - exp(-T/tau)); for
# the slowest body (the ring DC mode the coarse space carries: the L155 shaft,
# tau = 21 periods, 4 W of 3.8 kW) the 1e-4 bound is <= 2e-3 of that body's
# state, far inside the 5 % total-loss term.
CLOSURE_GATE = {"T_rel": 1e-4, "ripple_pp": 0.01, "P_rel": 1e-3,
                "P_floor_rel": 1e-5, "state_rel": 1e-4}
REPORT_GATE = {"T_rel": 1e-3, "ripple_pp": 0.05, "P_rel": 5e-3,
               "P_floor_rel": 1e-4, "state_rel": 1e-3}

# ── the period maps (second Codex review, 2026-10-03, findings 3 and 7) ─────
# The HALF period (opt-in) is taken only where the magnet source AND the
# operators are anti-symmetric under "one pole back, negated" to ROUND-OFF: the
# image map is a signed permutation of dofs matched on rotated coordinates, so an
# exactly symmetric mesh/source gives a mismatch of a few ulps of the assembled
# entries (measured < 1e-14 on the operators of the 30 mm fixture); 1e-12 leaves
# two decades for summation order.  Anything above is a discretisation
# asymmetry (the fixture's magnet source: 7.8e-6) that makes the anti-periodic
# orbit NOT a fixed point of the march — the run then takes the full period.
HALF_SYMMETRY_RTOL = 1e-12
# The map checks every TDM set-up runs (both periods): the back map is the
# exact inverse of the forward map, maps a constrained state (range of a frame
# projection: periodic BC signs, slip welds) onto a constrained state, maps
# every conductor body onto a whole body with the same conductance, and leaves
# the operators and the magnet source invariant.  A deviation above these
# refuses TDM at set-up (the run is marched, with a note).  Measured on the
# 30 mm fixture's full period: inverse 0, constrained 1.5e-19, bodies 9e-15,
# source 9.1e-6, operators 9.4e-4.  The operator number is the bilinear form on
# ROUGH random constrained vectors (dominated by the highest mesh modes), where
# the pole-pair images of the geometry-driven mesh match within the mesher's
# node tolerance, not to round-off; its effect on the solved field is what the
# source (smooth) number and the closure march measure.  A map DEFECT — a wrong
# BC sign, a wrong image, a body split — is O(1) on every one of these (tests).
MAP_CHECK_RTOL = {"inverse": 1e-12, "constrained": 1e-10, "bodies": 1e-10,
                  "operators": 1e-2, "source": 1e-4}

# ── demag fixed point (shared by the march and TDM) ─────────────────────────
#: Per magnet, area-weighted mean |dBr|/Br0 moved in one period (flux, hence
#: torque: the owner's term / 10).
DEMAG_SETTLE_TOL = 1e-3
#: …and at any single element (a local region still collapsing): 1 % of Br0 in
#: one period, ten times the area-mean bound.
DEMAG_SETTLE_ELEMENT_TOL = 1e-2


def demag_settled(chg: Dict[str, float], tol: float = DEMAG_SETTLE_TOL,
                  element_tol: float = DEMAG_SETTLE_ELEMENT_TOL) -> bool:
    """A period that moved Br by ``chg`` (:func:`br_change`) is settled when
    the worst magnet's area mean AND the worst single element are within
    their tolerances."""
    return bool(float(chg["per_magnet_mean_max"]) <= float(tol)
                and float(chg["element_max"]) <= float(element_tol))


def resolve_tdm_demag(requested: Optional[str],
                      environ: Optional[Dict[str, str]] = None
                      ) -> Tuple[str, Optional[str]]:
    """The demag mode of a TDM run and a note.

    ``"full"`` (the pre-pass to its fixed point) is the only QUALIFIED mode
    and the default.  The owner's 1/6-period shortcut is EXPERIMENTAL and is
    taken only through the explicit argument ``tdm_demag="shortcut"`` (no
    route, payload or workflow passes it — tests/test_tdm_entry_points.py);
    the environment can no longer select it (second Codex review, finding
    11): ``SB_TDM_DEMAG=shortcut`` is ignored with a note, so an ordinary
    workflow (EM tab, coupled loop, passport, MCP, optimizer) never gets it."""
    env = os.environ if environ is None else environ
    if requested is not None:
        m = str(requested).strip().lower()
        if m not in ("full", "shortcut"):
            raise ValueError("tdm_demag must be 'full' or 'shortcut', got %r"
                             % (requested,))
        return m, None
    e = str(env.get("SB_TDM_DEMAG") or "").strip().lower()
    if e == "shortcut":
        return "full", ("SB_TDM_DEMAG=shortcut ignored: the experimental demag "
                        "shortcut is taken only through the explicit "
                        "tdm_demag='shortcut' argument; full pre-pass used")
    return "full", None


def resolve_eddy_method(requested: Optional[str], sim_config: Optional[Dict] = None,
                        environ: Optional[Dict[str, str]] = None) -> str:
    """The eddy method a run uses: the argument, else ``SB_EDDY_METHOD``, else
    the config's ``simulation.eddy_method``, else :data:`DEFAULT_EDDY_METHOD`.
    Raises on a name that is neither."""
    env = os.environ if environ is None else environ
    m = str(requested or env.get("SB_EDDY_METHOD")
            or (sim_config or {}).get("eddy_method")
            or DEFAULT_EDDY_METHOD).strip().lower()
    if m not in EDDY_METHODS:
        raise ValueError("eddy_method must be 'march' or 'tdm', got %r" % (requested,))
    return m


def tdm_refusals(*, eddy: bool, voltage_drive: bool, series_paths: bool,
                 frozen_nu: bool, mixed_schedule: bool, bdf2: bool,
                 n_periods: float, full_ring: bool, source_name: Optional[str],
                 external_excitation: bool, six_phase: bool,
                 n_steps_per_period: int = 36) -> List[str]:
    """Why a run cannot be solved time-periodically (empty = it can).  Each
    reason sends the run to the march, with the reason in the result."""
    why: List[str] = []
    if not eddy:
        why.append("eddy is off (nothing to settle)")
    if int(n_steps_per_period) < 6:
        why.append("fewer than 6 steps per period (a view, not a steady state)")
    if external_excitation or source_name not in ("current", "custom_current",
                                                  "bldc_current"):
        why.append("an external or closed-loop excitation source")
    if six_phase:
        why.append("six-phase winding (not validated with TDM)")
    if voltage_drive:
        why.append("voltage / PWM drive: the circuit state is not in the "
                   "periodic wrap (PWM is out of scope)")
    if series_paths:
        why.append("series strand paths (the bordered matrix is a saddle point)")
    if frozen_nu:
        why.append("frozen_nu")
    if mixed_schedule:
        why.append("mixed-resolution schedule")
    if not bdf2:
        why.append("backward Euler (SB_EDDY_BE=1): TDM closes BDF2")
    if abs(float(n_periods) - round(float(n_periods))) > 1e-9:
        why.append("a fractional reported window")
    if full_ring:
        why.append("full-ring model (validated on sector models only)")
    return why


def bdf2_msd(Msig, dt: float):
    """sigma*M / dte of the uniform BDF2 step — the ONE place both the frames
    and the solver take it from."""
    return (Msig * (1.0 / (DTE_FACTOR * float(dt)))).tocsr()


def analytic_tangent(p2, MU0: float):
    """The Newton tangent of ``P2Nonlinear.Kpw`` with dν/dB² taken
    ANALYTICALLY from the same piecewise-linear H(B) table that
    ``field_ops._mu_r_from_bh_vec`` interpolates (ν = H/B; dν/dB =
    (B·dH/dB − H)/B²; zero where the table is clamped), instead of
    ``tangent2``'s one-sided difference over 1e-3·B.  Exact wherever B is not
    on a table knot, so the space-time Newton converges quadratically where
    the difference tangent made it linear.  TDM only (``SB_TDM_TANGENT=
    analytic``): the march keeps its own tangent."""
    cache: Dict[int, Any] = {}

    def tangent(info):
        T = None
        for _k2, _ids2, _c2, gA, Bm, nuq in info:
            if not _c2 or len(_c2) < 2:
                continue
            key = id(_c2)
            if key not in cache:
                cache[key] = _c2
            nup = dnu_dB2(_c2, Bm, MU0)
            Ti = p2._skel[_k2].tang(gA, 2.0 * nup)
            T = Ti if T is None else T + Ti
        return T
    return tangent


def dnu_dB2(curve, B: np.ndarray, MU0: float) -> np.ndarray:
    """dν/d(B²) of ν(B) = 1/(μ0·max(μ_r(B), 1)), μ_r from
    ``field_ops._mu_r_from_bh_vec`` (H(B) linear in the table, the implicit
    origin below the first sample, μ0 slope above the last), analytically."""
    hs = np.array([pt[0] for pt in curve], float)
    bs = np.array([pt[1] for pt in curve], float)
    sl = np.diff(hs) / np.maximum(np.diff(bs), 1e-300)
    B = np.asarray(B, float)
    H = np.interp(B, bs, hs)
    seg = np.clip(np.searchsorted(bs, B, side="right") - 1, 0, sl.size - 1)
    dH = np.where(B < bs[0], 0.0, sl[seg])           # np.interp clamps below
    if bs[0] > 0.0:
        below = B < bs[0]
        H = np.where(below, hs[0] * B / bs[0], H)
        dH = np.where(below, hs[0] / bs[0], dH)
    above = B >= bs[-1]
    H = np.where(above, hs[-1] + (B - bs[-1]) / MU0, H)
    dH = np.where(above, 1.0 / MU0, dH)
    clampH = H <= 1e-9
    H = np.maximum(H, 1e-9)
    mu = np.where(B <= 1e-12, 1.0, B / (MU0 * H))
    live = (mu > 1.0) & (B > 1e-12) & ~clampH
    dnu_dB = np.where(live, (dH * B - H) / np.maximum(B * B, 1e-300), 0.0)
    return dnu_dB / (2.0 * np.maximum(B, 1e-300))


# ════════════════════════════════════════════════════════════════════════
#  per-frame factorisation (one PARDISO handle per frame)
# ════════════════════════════════════════════════════════════════════════
def _mkl_local_threads(lib, n: Optional[int]) -> None:
    """Set the MKL thread count of the CALLING thread (mkl_set_num_threads_local).

    Several frames are factorised concurrently from a thread pool; each call
    gets ``n`` MKL threads so the total stays at workers x n.  ``n = 0``
    returns the thread to the process-wide setting; None leaves it."""
    if lib is None or n is None:
        return
    try:
        f = lib.MKL_Set_Num_Threads_Local
        f.argtypes = [ctypes.c_int]
        f.restype = ctypes.c_int
        f(int(n))
    except Exception:            # noqa: BLE001 — a missing symbol only costs speed
        pass


class FrameFactor:
    """Cholesky (PARDISO mtype 2) of one frame's bordered Jacobian, kept for
    back-solves; LU (mtype 11) when the run-time checks decline Cholesky or it
    fails — the same guards as ``P2Nonlinear._solve_spd``.  The symbolic
    analysis is reused while the pattern holds."""

    # live PARDISO handles of all FrameFactors (leak check, tests)
    _open_lock = threading.Lock()
    _open_count = 0

    @classmethod
    def open_handles(cls) -> int:
        return int(cls._open_count)

    def __init__(self, own: Optional[Callable] = None,
                 release: Optional[Callable] = None) -> None:
        self._own = own
        self._release = release
        self._h = None            # PyPardisoSolver
        self._lu = False
        self._pat = None          # sym_pattern of the analysed matrix (Cholesky)
        self._lu_key = None       # (indptr, indices) of the analysed LU matrix
        self._M = None            # the factorised matrix (upper triangle or full)
        self.n = 0
        self.analyses = 0
        self.factorizations = 0
        self.solves = 0
        self.lu_used = False

    @property
    def lib(self):
        return None if self._h is None else getattr(self._h, "libmkl", None)

    def _handle(self, mtype: int):
        if self._h is not None and int(self._h.mtype) == int(mtype):
            return self._h
        self.close()
        if "PYPARDISO_MKL_RT" not in os.environ:
            # PyPardisoSolver() otherwise globs sys.prefix for mkl_rt on every
            # construction (12.5 s on Windows, fem_solver_2d); reuse the
            # library the module-level solver already found
            try:
                from motor_ai_sim.simulation.pardiso_runtime import (
                    pardiso_subprocess_env)
                _hint = pardiso_subprocess_env({}).get("PYPARDISO_MKL_RT")
                if _hint:
                    os.environ["PYPARDISO_MKL_RT"] = _hint
            except Exception:    # noqa: BLE001 — only a start-up cost
                pass
        import pypardiso
        h = pypardiso.PyPardisoSolver(mtype=int(mtype))
        if self._own is not None:
            try:
                h = self._own(h)
            except Exception:    # noqa: BLE001 — no scope: released by close()
                pass
        self._h = h
        with FrameFactor._open_lock:
            FrameFactor._open_count += 1
        return h

    def factor(self, A, mkl_threads: Optional[int] = None) -> None:
        A = A.tocsr()
        if not A.has_canonical_format:
            A = A.copy()
            A.sum_duplicates()
        self.n = int(A.shape[0])
        if not self._lu:
            try:
                if self._factor_spd(A, mkl_threads):
                    return
            except Exception:    # noqa: BLE001 — not positive definite: LU
                pass
            self._lu = True
            self.lu_used = True
            self._pat = None
        self._factor_lu(A, mkl_threads)

    def _factor_spd(self, A, mkl_threads) -> bool:
        pat = self._pat
        new = not (pat is not None and pat.key == (int(A.shape[0]), int(A.nnz))
                   and np.array_equal(pat.indptr, A.indptr)
                   and np.array_equal(pat.indices, A.indices))
        if new:
            pat = sym_pattern(A.indptr, A.indices, A.shape[0])
            if not pat.ok:
                return False
        d = A.data
        dg = d[pat.diag]
        if not bool(np.all(dg > 0.0)):
            return False
        sc = 1.0 / np.sqrt(dg)
        dd = d[pat.up] - d[pat.lo]
        dd *= np.repeat(sc, pat.up_rep)
        dd *= sc[pat.up_c]
        if not float(np.max(np.abs(dd), initial=0.0)) <= SPD_SYM_RTOL:
            return False
        U = _csr((d[pat.tri], pat.u_indices, pat.u_indptr), shape=A.shape,
                 copy=False)
        h = self._handle(2)
        _mkl_local_threads(getattr(h, "libmkl", None), mkl_threads)
        h.iparm[11] = 0
        h.set_phase(12 if (new or self._pat is None) else 22)
        h._call_pardiso(U, np.zeros((U.shape[0], 1)))
        if new or self._pat is None:
            self.analyses += 1
        self._pat = pat
        self._M = U
        self.factorizations += 1
        return True

    def _factor_lu(self, A, mkl_threads) -> None:
        h = self._handle(11)
        _mkl_local_threads(getattr(h, "libmkl", None), mkl_threads)
        new = not (self._lu_key is not None
                   and np.array_equal(self._lu_key[0], A.indptr)
                   and np.array_equal(self._lu_key[1], A.indices))
        h.iparm[11] = 0
        h.set_phase(12 if new else 22)
        h._call_pardiso(A, np.zeros((A.shape[0], 1)))
        if new:
            self.analyses += 1
            self._lu_key = (A.indptr.copy(), A.indices.copy())
        self._M = A
        self.factorizations += 1

    def solve(self, b: np.ndarray, mkl_threads: Optional[int] = None) -> np.ndarray:
        h = self._h
        _mkl_local_threads(getattr(h, "libmkl", None), mkl_threads)
        bb = np.asfortranarray(np.asarray(b, float))
        h.set_phase(33)
        self.solves += 1
        return h._call_pardiso(self._M, bb)

    def close(self) -> None:
        h, self._h = self._h, None
        self._pat = None
        self._lu_key = None
        self._M = None
        if h is None:
            return
        with FrameFactor._open_lock:
            FrameFactor._open_count -= 1
        try:
            if self._release is not None:
                self._release(h)
            else:
                h.free_memory(everything=True)
        except Exception:        # noqa: BLE001 — cleanup
            pass


# ════════════════════════════════════════════════════════════════════════
#  frames
# ════════════════════════════════════════════════════════════════════════
class TdmFrame:
    """One time level of the period: its projection, free set, imposed body
    currents, and everything derived from them once (patterns are fixed)."""

    def __init__(self, k: int, Pro, free: np.ndarray, I_vec: np.ndarray, *,
                 Msd, G, cond: np.ndarray, factor: FrameFactor) -> None:
        self.k = int(k)
        self.Pro = Pro.tocsr()
        self.free = np.asarray(free, int)
        self.I_vec = np.asarray(I_vec, float)
        self.Pt = self.Pro.T.tocsr()
        Pf = self.Pro.tocsc()[:, self.free].tocsr()
        self.Pf = Pf
        self.PfT = Pf.T.tocsr()
        self.Msd_ff = (self.PfT @ (Msd @ Pf)).tocsr()
        self.Bf = (self.PfT @ G).tocsr()
        # history coupling: field rows see -P'·Msd·h, h on the conductor dofs
        self.Wc = (self.PfT @ Msd[:, cond]).tocsr()
        # reduced solution -> conductor dofs of the full vector
        self.Pc = Pf[cond, :].tocsr()
        self.fac = factor
        # the Newton state of this frame
        self.A = None
        self.U = None
        self.K = None
        self.info = None
        self.scale = None         # (bn, cden) frozen per Newton iteration

    @property
    def nfree(self) -> int:
        return int(self.free.size)

    def pad(self, xf: np.ndarray) -> np.ndarray:
        x = np.zeros(self.Pro.shape[1])
        x[self.free] = xf
        return self.Pro @ x


# ════════════════════════════════════════════════════════════════════════
#  GMRES (right preconditioned, restarted)
# ════════════════════════════════════════════════════════════════════════
def gmres_right(matvec: Callable[[np.ndarray], np.ndarray], b: np.ndarray, *,
                prec: Optional[Callable[[np.ndarray], np.ndarray]] = None,
                x0: Optional[np.ndarray] = None, rtol: float = 1e-8,
                restart: int = 40, maxiter: int = 200
                ) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Solve matvec(x) = b; x = prec(y) with GMRES on matvec∘prec.  Modified
    Gram-Schmidt with one re-orthogonalisation; ``maxiter`` counts matvecs.

    ``info["rel_resid"]`` is the TRUE relative residual ||b - matvec(x)|| / ||b||
    of the UNpreconditioned system (recomputed from x after every cycle, not
    the Arnoldi estimate); ``info["converged"]`` says whether it met ``rtol``
    (False when ``maxiter`` stopped it)."""
    P = prec if prec is not None else (lambda v: v)
    n = b.size
    x = np.zeros(n) if x0 is None else np.array(x0, float)
    bn = float(np.linalg.norm(b))
    info: Dict[str, Any] = {"iterations": 0, "restarts": 0, "resid": []}
    if bn == 0.0:
        info["rel_resid"] = 0.0
        info["converged"] = True
        return np.zeros(n), info
    r = b - (matvec(x) if x0 is not None else 0.0)
    it = 0
    while True:
        beta = float(np.linalg.norm(r))
        info["rel_resid"] = beta / bn
        if beta / bn <= rtol or it >= maxiter:
            break
        m = min(restart, maxiter - it)
        V = np.zeros((m + 1, n))
        Z = np.zeros((m, n))
        Hh = np.zeros((m + 1, m))
        V[0] = r / beta
        g = np.zeros(m + 1)
        g[0] = beta
        cs = np.zeros(m)
        sn = np.zeros(m)
        j_end = 0
        for j in range(m):
            Z[j] = P(V[j])
            w = matvec(Z[j])
            it += 1
            for _rep in range(2):
                for i in range(j + 1):
                    hij = float(w @ V[i])
                    Hh[i, j] += hij
                    w = w - hij * V[i]
            Hh[j + 1, j] = float(np.linalg.norm(w))
            if Hh[j + 1, j] > 0.0:
                V[j + 1] = w / Hh[j + 1, j]
            for i in range(j):
                t = cs[i] * Hh[i, j] + sn[i] * Hh[i + 1, j]
                Hh[i + 1, j] = -sn[i] * Hh[i, j] + cs[i] * Hh[i + 1, j]
                Hh[i, j] = t
            den = math.hypot(Hh[j, j], Hh[j + 1, j])
            cs[j] = Hh[j, j] / den if den > 0 else 1.0
            sn[j] = Hh[j + 1, j] / den if den > 0 else 0.0
            Hh[j, j] = den
            Hh[j + 1, j] = 0.0
            g[j + 1] = -sn[j] * g[j]
            g[j] = cs[j] * g[j]
            j_end = j + 1
            info["resid"].append(abs(g[j + 1]) / bn)
            if abs(g[j + 1]) / bn <= rtol or it >= maxiter:
                break
        y = np.linalg.solve(np.triu(Hh[:j_end, :j_end]), g[:j_end])
        x = x + Z[:j_end].T @ y
        r = b - matvec(x)
        info["restarts"] += 1
    info["iterations"] = it
    info["converged"] = bool(info["rel_resid"] <= rtol)
    return x, info


# ════════════════════════════════════════════════════════════════════════
#  the solver
# ════════════════════════════════════════════════════════════════════════
class TimePeriodicEddy:
    """Newton on the (anti)periodic space-time system of the BDF2 eddy march.

    ``kfun(A) -> (K, info)`` and ``tangent(info) -> T or None`` are the SAME
    nonlinearity the march uses (``P2Nonlinear.Kpw`` / ``tangent2``).
    ``wrap_back(v)`` maps a full dof vector one (half) period back.
    ``cond`` are the conductor dofs (support of sigma*M).
    ``coarse``: None, or a dict describing the DC coarse space (see
    :meth:`_build_coarse`): ``ring`` = ring-conductor dofs (subset of cond),
    ``Msig`` the full sigma-mass, ``image`` = (perm, sign, q, sigma) of the
    wrap map restricted to ``ring``.
    """

    def __init__(self, *, kfun, tangent, f_mag, G, Msig, S_raw, dt: float,
                 frames: Sequence[TdmFrame], wrap_back: Callable, cond: np.ndarray,
                 coarse: Optional[Dict[str, Any]] = None, tol: float = 1e-7,
                 max_newton: int = 25, workers: int = 1,
                 mkl_threads: Optional[int] = None, gmres_rtol: float = 1e-9,
                 gmres_max: int = 200, eta: Optional[float] = None,
                 monitor: Optional[Callable] = None,
                 stop_owner: Optional[Dict[str, float]] = None,
                 log=None) -> None:
        # monitor(As, Us, ev) -> {"T_mean", "ripple_pct", "P_joule", ...}: the
        # shipped torque / loss of the current iterate (the caller's own
        # post-processing functions); stop_owner: {"T_mean_rel", "ripple_pp",
        # "P_rel", "rrel_floor"} — stop when two iterates differ by less
        # (and the state residual is below the floor), or None (residual only)
        self.monitor = monitor
        self.stop_owner = stop_owner
        # eta: a FIXED Newton forcing term for the wrap GMRES (None = adaptive,
        # 1e-2 of the residual).  Where the Newton is linear anyway (the
        # march's difference tangent), solving tighter than its rate buys
        # nothing.
        self.eta = eta
        self.kfun = kfun
        self.tangent = tangent
        self.f_mag = f_mag
        self.G = G.tocsr()
        self.GT = self.G.T.tocsr()
        self.dt = float(dt)
        self.dte = DTE_FACTOR * self.dt
        self.Msig = Msig.tocsr()
        self.Msd = bdf2_msd(self.Msig, self.dt)
        self.S_raw = np.asarray(S_raw, float)
        self.Sdt = self.S_raw * self.dte
        self.frames = list(frames)
        self.N = len(self.frames)
        self.wrap_back = wrap_back
        self.cond = np.asarray(cond, int)
        self.nc = int(self.cond.size)
        self.GcT = self.G[self.cond, :].T.tocsr()      # G' restricted to cond
        self.coarse_spec = coarse
        self.tol = float(tol)
        self.max_newton = int(max_newton)
        self.workers = max(1, int(workers))
        self.mkl_threads = mkl_threads
        self.gmres_rtol = min(float(gmres_rtol), 1e-2 * float(tol))
        self.gmres_max = int(gmres_max)
        self.log = log
        self._pool = (ThreadPoolExecutor(max_workers=self.workers)
                      if self.workers > 1 else None)
        self._coarse = None
        self.stats: Dict[str, Any] = {
            "frames": self.N, "newton": [], "gmres_iterations": 0,
            "sweeps": 0, "back_solves": 0, "residual_evals": 0,
            "jacobian_factorizations": 0, "t": {}}

    # ── utilities ────────────────────────────────────────────────────────
    def _tick(self, key: str, t0: float) -> None:
        self.stats["t"][key] = self.stats["t"].get(key, 0.0) + (time.perf_counter() - t0)

    def _map(self, fn, items):
        if self._pool is None:
            return [fn(x) for x in items]
        return list(self._pool.map(fn, items))

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None
        for fr in self.frames:
            fr.fac.close()
        if self._coarse is not None:
            self._coarse["fac"].close()
            self._coarse = None

    def hist(self, j: int, As: Sequence[np.ndarray]) -> Tuple[np.ndarray, np.ndarray]:
        """(A_{j-1}, A_{j-2}) with the wrap closing the period."""
        N = self.N
        a1 = As[j - 1] if j >= 1 else self.wrap_back(As[N - 1])
        a2 = (As[j - 2] if j >= 2 else
              (self.wrap_back(As[N - 1]) if j == 1 else self.wrap_back(As[N - 2])))
        return a1, a2

    # ── per-frame residual (identical to P2Drive.eddy_solve) ─────────────
    def _residual(self, fr: TdmFrame, A, U, Ahist, scale=None):
        K, info = self.kfun(A)
        rhs_e = self.f_mag + self.Msd @ Ahist
        t = K @ A + self.Msd @ A - self.G @ U - rhs_e
        rf = np.asarray(fr.Pt @ t).ravel()[fr.free]
        GtA = np.asarray(self.GT @ A).ravel()
        cr = self.dte * fr.I_vec - np.asarray(self.GT @ Ahist).ravel()
        rc = self.Sdt * U - GtA - cr
        bn = max(float(np.linalg.norm(np.asarray(fr.Pt @ rhs_e).ravel()[fr.free])),
                 1e-30)
        cden = max(float(np.linalg.norm(cr)),
                   float(np.linalg.norm(self.dte * fr.I_vec)),
                   float(np.linalg.norm(GtA)),
                   float(np.linalg.norm(self.Sdt * U)), 1e-30)
        nf = float(np.linalg.norm(rf))
        nc = float(np.linalg.norm(rc))
        rrel = max(nf / bn, nc / cden)
        s = scale if scale is not None else (bn, cden)
        merit = (nf / s[0]) ** 2 + (nc / s[1]) ** 2
        return rf, rc, rrel, merit, (bn, cden), K, info

    def _eval_all(self, As, Us, scales=None):
        t0 = time.perf_counter()

        def one(j):
            fr = self.frames[j]
            a1, a2 = self.hist(j, As)
            Ah = C_M1 * a1 + C_M2 * a2
            return self._residual(fr, As[j], Us[j], Ah,
                                  None if scales is None else scales[j])
        out = self._map(one, range(self.N))
        self.stats["residual_evals"] += self.N
        self._tick("residual", t0)
        return out

    def _jac_all(self, keep_J: bool = False):
        t0 = time.perf_counter()

        def one(j):
            fr = self.frames[j]
            T = (self.tangent(fr.info)
                 if (fr.info is not None and len(fr.info) > 0) else None)
            J = fr.K if T is None else (fr.K + T)
            Jff = ((fr.PfT @ (J @ fr.Pf)) + fr.Msd_ff).tocsr()
            Mb = _bmat([[Jff, -fr.Bf], [-fr.Bf.T, _diags(self.Sdt)]]).tocsr()
            fr.fac.factor(Mb, self.mkl_threads)
            return J if keep_J else None
        Js = self._map(one, range(self.N))
        self.stats["jacobian_factorizations"] += self.N
        self._tick("jacobian", t0)
        return Js

    def joule(self, As: Sequence[np.ndarray], Us: Sequence[np.ndarray]) -> np.ndarray:
        """Per-frame σE² of all conductors [W per metre of the modelled
        sector], ∫σ(−∂A/∂t + U)² with the step's own BDF2 derivative — the
        march's integrand (sampled at t_k, i.e. SB_EDDY_LOSS_AT=step), used
        only to watch the loss between Newton iterates."""
        out = np.zeros(self.N)
        for j in range(self.N):
            a1, a2 = self.hist(j, As)
            dA = (As[j] - (C_M1 * a1 + C_M2 * a2)) / self.dte
            gd = np.asarray(self.GT @ dA).ravel()
            U = np.asarray(Us[j], float)
            out[j] = float(dA @ (self.Msig @ dA)) + float(
                np.sum(U * (U * self.S_raw - 2.0 * gd)))
        return out

    # ── the start: the static field of every frame, in parallel ──────────
    def static_start(self, A0: np.ndarray, tol: float = 1e-5,
                     maxit: int = 40, sequential: bool = False
                     ) -> Tuple[List[np.ndarray], Dict[str, Any]]:
        """The ∂A/∂t = 0 field of every frame (U_b = I_b/S_b, uniform current
        in every wire — ``P2Drive.eddy_static_state``'s problem), each with a
        factor of its own (released afterwards).  Parallel: every frame from
        ONE start ``A0`` (frame 0's static field) at once; ``sequential``:
        frame by frame, each from its neighbour (fewer iterations, no
        parallelism).  A START for the periodic Newton, so ``tol`` is loose:
        the eddy reaction the Newton adds is orders of magnitude larger."""
        t0 = time.perf_counter()
        its = [0] * self.N
        prev = [A0]

        def one(j):
            fr = self.frames[j]
            fac = FrameFactor(own=fr.fac._own, release=fr.fac._release)
            try:
                U = fr.I_vec / np.maximum(self.S_raw, 1e-300)
                f = self.f_mag + self.G @ U
                pd = np.asarray(fr.Pro.multiply(fr.Pro).sum(axis=0)).ravel()
                Ast = prev[0] if sequential else A0
                A = fr.Pro @ (np.asarray(fr.Pro.T @ Ast).ravel() / np.maximum(pd, 1.0))
                bn = max(float(np.linalg.norm(np.asarray(fr.Pt @ f).ravel()[fr.free])),
                         1e-30)
                for it in range(maxit):
                    K, info = self.kfun(A)
                    r = np.asarray(fr.Pt @ (K @ A - f)).ravel()[fr.free]
                    r0 = float(np.linalg.norm(r))
                    if r0 / bn < tol:
                        break
                    its[j] = it + 1
                    T = (self.tangent(info)
                         if (info is not None and len(info) > 0) else None)
                    J = K if T is None else (K + T)
                    fac.factor((fr.PfT @ (J @ fr.Pf)).tocsr(), self.mkl_threads)
                    dA = fr.pad(fac.solve(-r, self.mkl_threads))
                    lam = 1.0
                    for _ls in range(8):
                        At = A + lam * dA
                        Kt, _ = self.kfun(At)
                        if float(np.linalg.norm(np.asarray(
                                fr.Pt @ (Kt @ At - f)).ravel()[fr.free])) < r0:
                            A = At
                            break
                        lam *= 0.5
                    else:
                        break
                prev[0] = A
                return A
            finally:
                fac.close()
        out = ([one(j) for j in range(self.N)] if sequential
               else self._map(one, range(self.N)))
        self._tick("static_start", t0)
        return out, {"newton_iterations": list(its), "tol": float(tol)}

    # ── the linear forward sweep (block forward substitution in time) ───
    def _sweep(self, rhs: Optional[List[Tuple[np.ndarray, np.ndarray]]],
               w: Tuple[np.ndarray, np.ndarray], keep_x: bool = False):
        d_m2, d_m1 = w
        dc: List[Optional[np.ndarray]] = [None] * self.N
        xs: List[Optional[np.ndarray]] = [None] * self.N
        last_full = {}
        for j, fr in enumerate(self.frames):
            p1 = d_m1 if j == 0 else dc[j - 1]
            p2 = d_m2 if j == 0 else (d_m1 if j == 1 else dc[j - 2])
            h = C_M1 * p1 + C_M2 * p2
            bf = np.asarray(fr.Wc @ h).ravel()
            bc = -np.asarray(self.GcT @ h).ravel()
            if rhs is not None:
                bf = bf + rhs[j][0]
                bc = bc + rhs[j][1]
            # the sweep is sequential: every MKL thread of the process
            x = fr.fac.solve(np.concatenate([bf, bc]),
                             0 if self.mkl_threads else None)
            xa = x[:fr.nfree]
            dc[j] = np.asarray(fr.Pc @ xa).ravel()
            if keep_x:
                xs[j] = x
            if j >= self.N - 2:
                last_full[j] = fr.pad(xa)
        self.stats["sweeps"] += 1
        self.stats["back_solves"] += self.N
        wn2 = np.asarray(self.wrap_back(last_full[self.N - 2]))[self.cond]
        wn1 = np.asarray(self.wrap_back(last_full[self.N - 1]))[self.cond]
        return (wn2, wn1), xs

    # ── coarse space: the rotor-frame DC of the ring conductors ─────────
    def _build_coarse(self, Js: List) -> None:
        spec = self.coarse_spec
        if not spec or Js is None or any(J is None for J in Js):
            return
        t0 = time.perf_counter()
        ring = np.asarray(spec["ring"], int)
        pos = np.searchsorted(self.cond, ring)
        if ring.size == 0 or not np.array_equal(self.cond[pos], ring):
            return
        Jb = Js[0]
        for J in Js[1:]:
            Jb = Jb + J
        Jb = (Jb * (1.0 / len(Js))).tocsr()
        fr0 = self.frames[0]
        Jff = (fr0.PfT @ (Jb @ fr0.Pf)).tocsr()
        fac = FrameFactor(own=spec.get("own"), release=spec.get("release"))
        fac.factor(Jff, self.mkl_threads)
        Mring = spec["Msig"].tocsr()[:, ring].tocsr()
        perm, sign, q, sigma = spec["image"]
        self._coarse = {"fac": fac, "pos": pos, "ring": ring,
                        "PMr": (fr0.PfT @ Mring).tocsr(),
                        "Pring": fr0.Pf[ring, :].tocsr(),
                        "perm": perm, "sign": sign, "q": int(q),
                        "sigma": float(sigma), "T": float(spec["period_s"])}
        self._tick("coarse_build", t0)

    def _coarse_apply(self, r: np.ndarray) -> np.ndarray:
        c = self._coarse
        if c is None:
            return r
        nc = self.nc
        r2 = r[:nc]
        r1 = r[nc:]
        # principal BDF2 temporal mode of the wrap state (the z = 1 root)
        alpha = 0.5 * (3.0 * r1 - r2)
        a = _pa.pole_pair_image_mean(alpha[c["pos"]], c["perm"], c["sign"],
                                     c["q"], c["sigma"])
        x = c["fac"].solve(np.asarray(c["PMr"] @ a).ravel(),
                           0 if self.mkl_threads else None)
        v = np.asarray(c["Pring"] @ x).ravel()
        v = _pa.pole_pair_image_mean(v, c["perm"], c["sign"], c["q"],
                                     c["sigma"]) / c["T"]
        out = r.copy()
        out[c["pos"]] += v
        out[nc + c["pos"]] += v
        return out

    # ── certification: the period map's contraction and the orbit error ──
    # (third Codex review, 2026-10-04, finding 1).  The closure march measures
    # the one-period DEFECT d = Phi(a) - a of the orbit a.  The distance to the
    # true periodic orbit a* is e = a* - a with (I - T) e = d to first order
    # (T = the linearised period map, the operator the Newton reduces to): a
    # slow mode with eigenvalue lambda amplifies its share of d by
    # 1 / (1 - lambda).  So the bound needs T at the final orbit.
    def refresh_jacobian(self, As: Sequence[np.ndarray], Us: Sequence[np.ndarray]
                         ) -> None:
        """Factor every frame's Jacobian AT (As, Us), so that the sweeps below
        apply the linearised period map of THIS orbit (the factors the Newton
        left behind belong to its last iterate, or to an earlier Br)."""
        As = [np.asarray(a, float) for a in As]
        Us = [np.asarray(u, float) for u in Us]
        ev = self._eval_all(As, Us)
        for j, fr in enumerate(self.frames):
            fr.A, fr.U = As[j], Us[j]
            fr.K, fr.info = ev[j][5], ev[j][6]
        first = self._coarse is None and bool(self.coarse_spec)
        Js = self._jac_all(keep_J=first)
        if first:
            self._build_coarse(Js)

    def period_map(self, w: np.ndarray) -> np.ndarray:
        """T w: the linearised period map on the wrap state (conductor dofs of
        both BDF2 history levels)."""
        tw, _ = self._sweep(None, (w[:self.nc], w[self.nc:]))
        return np.concatenate(tw)

    def propagate(self, w: np.ndarray) -> Tuple[List[np.ndarray], List[np.ndarray]]:
        """The linear response of every frame (dA_j full vector, dU_j) to a
        perturbation w of the wrap state."""
        _, xs = self._sweep(None, (w[:self.nc], w[self.nc:]), keep_x=True)
        return ([fr.pad(x[:fr.nfree]) for fr, x in zip(self.frames, xs)],
                [np.asarray(x[fr.nfree:], float) for fr, x in zip(self.frames, xs)])

    def contraction(self, v0: np.ndarray, m: int = 15,
                    rng_seed: int = 0) -> Dict[str, Any]:
        """The dominant eigenvalues of T by Arnoldi (m steps) started from v0
        (the closure defect) plus a random component (so a mode the defect
        happens to miss is still in the Krylov space).  rho = the largest
        Ritz magnitude: outlying eigenvalues — the slow modes near 1 that
        matter here — are the ones Arnoldi finds first."""
        n = int(v0.size)
        rng = np.random.default_rng(rng_seed)
        v = np.asarray(v0, float)
        nv = float(np.linalg.norm(v))
        r = rng.standard_normal(n)
        r *= (nv if nv > 0.0 else 1.0) / max(float(np.linalg.norm(r)), 1e-300)
        v = (v + 0.1 * r) if nv > 0.0 else r
        m = int(max(1, min(m, n)))
        V = np.zeros((m + 1, n))
        Hh = np.zeros((m + 1, m))
        V[0] = v / float(np.linalg.norm(v))
        k_end = m
        for j in range(m):
            w = self.period_map(V[j])
            for _rep in range(2):
                for i in range(j + 1):
                    h = float(w @ V[i])
                    Hh[i, j] += h
                    w = w - h * V[i]
            Hh[j + 1, j] = float(np.linalg.norm(w))
            if Hh[j + 1, j] <= 1e-14 * max(abs(Hh[j, j]), 1e-300):
                k_end = j + 1
                break
            V[j + 1] = w / Hh[j + 1, j]
        ritz = np.linalg.eigvals(Hh[:k_end, :k_end])
        mags = np.sort(np.abs(ritz))[::-1]
        return {"rho": float(mags[0]) if mags.size else 0.0,
                "ritz_top": [float("%.6g" % x) for x in mags[:6]],
                "arnoldi_steps": int(k_end)}

    def orbit_error(self, d: np.ndarray, rtol: float = 1e-8,
                    maxiter: int = 400) -> Tuple[np.ndarray, Dict[str, Any]]:
        """e = (I - T)^-1 d, by the same right-preconditioned GMRES as the
        Newton (the DC coarse space carries the slow ring modes)."""
        dn = float(np.linalg.norm(d))
        if dn == 0.0:
            return np.zeros_like(d), {"rel_resid": 0.0, "iterations": 0,
                                      "converged": True}

        def mv(wv):
            return wv - self.period_map(wv)
        _prec = self._coarse_apply if self._coarse else None
        e, info = gmres_right(mv, d, prec=_prec, rtol=rtol, maxiter=maxiter)
        true = float(np.linalg.norm(d - mv(e))) / dn
        return e, {"rel_resid": true, "iterations": int(info["iterations"]),
                   "converged": bool(true <= max(rtol, 1e-6))}

    # ── the Newton ───────────────────────────────────────────────────────
    def solve(self, As: List[np.ndarray], Us: List[np.ndarray]) -> Dict[str, Any]:
        """Newton from the initial orbit (As, Us); returns the stats and
        leaves the converged states in ``self.frames[j].A/U``."""
        t_all = time.perf_counter()
        # every start ON its frame's constraint manifold range(Pro_j) (the
        # frame loop's least-squares projection): a component off it is not
        # seen by the projected residual and would stay frozen in the orbit
        As = [fr.Pro @ (np.asarray(fr.Pro.T @ np.asarray(a, float)).ravel()
                        / np.maximum(np.asarray(fr.Pro.multiply(fr.Pro).sum(axis=0))
                                     .ravel(), 1.0))
              for fr, a in zip(self.frames, As)]
        Us = [np.array(u, float, copy=True) for u in Us]
        # floors of the merit scales: the field and constraint terms the
        # imposed currents drive (a machine without magnets starts at 0 with
        # every right-hand side 0 at a current zero)
        _imax = np.max(np.abs(np.stack([fr.I_vec for fr in self.frames])), axis=0)
        _udc = _imax / np.maximum(self.S_raw, 1e-300)
        _gu = self.G @ _udc
        self._floor = (max([float(np.linalg.norm(np.asarray(fr.Pt @ _gu).ravel()[fr.free]))
                            for fr in self.frames] + [1e-30]),
                       max(float(np.linalg.norm(self.dte * _imax)), 1e-30))
        ev = self._eval_all(As, Us)
        converged = False
        self.stats["stopped_by"] = None
        mon_prev = None
        it = 0
        for it in range(self.max_newton + 1):
            rrel = [e[2] for e in ev]
            worst = float(max(rrel))
            rec = {"rrel_max": worst, "rrel_median": float(np.median(rrel))}
            mon = None
            if self.monitor is not None:
                t0m = time.perf_counter()
                mon = self.monitor(As, Us, ev)
                self._tick("monitor", t0m)
                rec["monitor"] = mon
            self.stats["newton"].append(rec)
            if self.log is not None:
                self.log.info("TDM Newton %d: max frame rrel %.3e (median %.3e)%s",
                              it, worst, rec["rrel_median"],
                              "" if mon is None else
                              "; T_mean %.7g, ripple %.5f %%, P_eddy %.6g W"
                              % (mon["T_mean"], mon["ripple_pct"], mon["P_joule"]))
            if worst < self.tol:
                converged = True
                self.stats["stopped_by"] = "state_residual"
                break
            # THE OWNER'S TERMS (coordinator 2026-09-30): the torque waveform
            # (mean, ripple p-p) and the loss stopped moving between two
            # iterates by a small fraction of the owner's tolerances, on an
            # iterate whose state residual is already small
            if (self.stop_owner is not None and mon is not None
                    and mon_prev is not None and worst < self.stop_owner["rrel_floor"]):
                dT = abs(mon["T_mean"] - mon_prev["T_mean"]) / max(
                    abs(mon["T_mean"]), 1e-300)
                dR = abs(mon["ripple_pct"] - mon_prev["ripple_pct"])
                dP = abs(mon["P_joule"] - mon_prev["P_joule"]) / max(
                    abs(mon["P_joule"]), 1e-300)
                rec["owner_changes"] = {"T_mean_rel": dT, "ripple_pp": dR,
                                        "P_eddy_rel": dP}
                if (dT < self.stop_owner["T_mean_rel"]
                        and dR < self.stop_owner["ripple_pp"]
                        and dP < self.stop_owner["P_rel"]):
                    converged = True
                    self.stats["stopped_by"] = "owner_terms"
                    break
            mon_prev = mon
            if it == self.max_newton:
                break
            for j, fr in enumerate(self.frames):
                fr.A, fr.U = As[j], Us[j]
                fr.K, fr.info = ev[j][5], ev[j][6]
            first = self._coarse is None and bool(self.coarse_spec)
            Js = self._jac_all(keep_J=first)
            if first:
                self._build_coarse(Js)
            Js = None
            # ── the linear system, reduced to the wrap state ──────────────
            t0 = time.perf_counter()
            rhs = [(-e[0], -e[1]) for e in ev]
            zero = (np.zeros(self.nc), np.zeros(self.nc))
            g_w, _ = self._sweep(rhs, zero)
            g = np.concatenate(g_w)

            def mv(wv):
                tw, _ = self._sweep(None, (wv[:self.nc], wv[self.nc:]))
                return wv - np.concatenate(tw)
            gn = float(np.linalg.norm(g))
            if gn > 0.0:
                # inexact Newton: the wrap solved to a forcing term that
                # follows the residual (1e-3 far away, 1e-2 of the residual
                # near the solution, never below gmres_rtol)
                _eta = (max(self.gmres_rtol, min(1e-3, 1e-2 * worst))
                        if self.eta is None else float(self.eta))
                rec["gmres_rtol"] = _eta
                # right preconditioning: the returned x is already P·y
                _prec = self._coarse_apply if self._coarse else None
                wsol, ginfo = gmres_right(mv, g, prec=_prec, rtol=_eta,
                                          maxiter=self.gmres_max)
                if not ginfo.get("converged", False):
                    # one more budget, restarted from where it stopped
                    _its0 = int(ginfo["iterations"])
                    wsol, ginfo = gmres_right(mv, g, prec=_prec, x0=wsol,
                                              rtol=_eta, maxiter=self.gmres_max)
                    ginfo["iterations"] = int(ginfo["iterations"]) + _its0
                    ginfo["retried"] = True
            else:
                wsol, ginfo = np.zeros(2 * self.nc), {"iterations": 0,
                                                     "rel_resid": 0.0,
                                                     "converged": True}
            self.stats["gmres_iterations"] += int(ginfo["iterations"])
            rec["gmres_iterations"] = int(ginfo["iterations"])
            # the TRUE residual of the UNpreconditioned wrap system, recomputed
            # here from the returned w with one more sweep — independent of
            # GMRES's own bookkeeping (second Codex review, finding 8); the
            # step is judged on THIS number
            rec["gmres_reported_rel_resid"] = float(ginfo.get("rel_resid", 0.0))
            if gn > 0.0:
                _t_true = float(np.linalg.norm(g - mv(wsol))) / gn
            else:
                _t_true = 0.0
            if not math.isfinite(_t_true):
                _t_true = float("inf")
            rec["gmres_rel_resid"] = _t_true
            rec["gmres_converged"] = bool(_t_true <= float(
                rec.get("gmres_rtol", self.gmres_rtol)) * (1.0 + 1e-9))
            rec["gmres_accepted_inexact"] = bool(
                not rec["gmres_converged"]
                and _t_true <= GMRES_ACCEPT_FACTOR * float(
                    rec.get("gmres_rtol", self.gmres_rtol)))
            self.stats["gmres_max_rel_resid"] = max(
                float(self.stats.get("gmres_max_rel_resid", 0.0)),
                rec["gmres_rel_resid"])
            if (not rec["gmres_converged"]
                    and rec["gmres_rel_resid"] > GMRES_ACCEPT_FACTOR * float(
                        rec.get("gmres_rtol", self.gmres_rtol))):
                # an inaccurate shooting correction is NOT taken (Codex review
                # 2026-09-30): the Newton stops unconverged, and the caller
                # marches instead
                self.stats["stopped_by"] = "gmres_not_converged"
                if self.log is not None:
                    self.log.warning(
                        "TDM Newton %d: wrap GMRES did not converge (true "
                        "relative residual %.3e after %d iterations, forcing "
                        "%.1e) — step rejected", it, rec["gmres_rel_resid"],
                        rec["gmres_iterations"], rec.get("gmres_rtol", 0.0))
                self._tick("linear", t0)
                break
            _, xs = self._sweep(rhs, (wsol[:self.nc], wsol[self.nc:]), keep_x=True)
            self._tick("linear", t0)
            dA = [fr.pad(x[:fr.nfree]) for fr, x in zip(self.frames, xs)]
            dU = [x[fr.nfree:] for fr, x in zip(self.frames, xs)]
            # ── line search on the whole space-time residual ──────────────
            # merit scales: the LARGEST field / constraint scale over the
            # period for every frame (a frame at a current zero has a
            # constraint scale of ~0 and would otherwise dominate the merit
            # with round-off)
            _bs = max([e[4][0] for e in ev] + [self._floor[0]])
            _cs = max([e[4][1] for e in ev] + [self._floor[1]])
            scales = [(_bs, _cs)] * self.N
            m0 = float(sum((np.linalg.norm(e[0]) / s[0]) ** 2
                           + (np.linalg.norm(e[1]) / s[1]) ** 2
                           for e, s in zip(ev, scales)))
            lam = 1.0
            acc = False
            for _ls in range(8):
                At = [a + lam * d for a, d in zip(As, dA)]
                Ut = [u + lam * d for u, d in zip(Us, dU)]
                evt = self._eval_all(At, Ut, scales)
                mt = float(sum(e[3] for e in evt))
                if mt < m0:
                    acc = True
                    break
                lam *= 0.5
            rec["step"] = lam
            if not acc:
                if self.log is not None:
                    self.log.warning("TDM Newton %d: line search failed "
                                     "(merit %.3e)", it, m0)
                break
            As, Us = At, Ut
            # the accepted trial: its rrel is judged on its own normalisation
            # (element 4), its K/info feed the next Jacobian
            ev = evt
        for j, fr in enumerate(self.frames):
            fr.A, fr.U = As[j], Us[j]
        self.stats["converged"] = bool(converged)
        self.stats["newton_iterations"] = int(it)
        self.stats["rrel_max"] = float(self.stats["newton"][-1]["rrel_max"])
        self.stats["coarse"] = (None if self._coarse is None else
                                {"ring_dofs": int(self._coarse["ring"].size),
                                 "q": int(self._coarse["q"]),
                                 "sigma": float(self._coarse["sigma"])})
        self._tick("solve_total", t_all)
        return self.stats


# ════════════════════════════════════════════════════════════════════════
#  demag on the orbit: the pre-pass march and the owner's shortcut
# ════════════════════════════════════════════════════════════════════════
def demag_march(ks: Sequence[int], hist: Tuple[np.ndarray, np.ndarray],
                start: Callable[[int], np.ndarray], solve_frame: Callable,
                ratchet: Callable[[np.ndarray], bool], max_passes: int = 11,
                log=None) -> Dict[str, Any]:
    """March the frames ``ks`` (consecutive) from the orbit history
    ``hist = (A_{k0-1}, A_{k0-2})`` with the Br ratchet ACTIVE, every frame
    re-entrant exactly like the march's demag pre-pass (solve, ratchet, and
    re-solve the frame on the weakened magnet, up to ``max_passes``).

    ``solve_frame(k, A_start, Ahist) -> A`` is the march's own bordered
    Newton; ``ratchet(A) -> moved`` applies ``MagnetDemag.update`` to the
    converged field and rebuilds the magnet source."""
    a1, a2 = hist
    passes = 0
    trips = 0
    for k in ks:
        Ah = C_M1 * a1 + C_M2 * a2
        A_st = start(k)
        for _p in range(max_passes + 1):
            A = solve_frame(k, A_st, Ah)
            passes += 1
            moved = ratchet(A)
            if not moved or _p == max_passes:
                break
            trips += 1
            A_st = A
        a2, a1 = a1, A
    return {"frames": len(list(ks)), "solves": passes, "ratchet_trips": trips}


def predicted_demag(mags: Sequence[Dict[str, Any]], Bx: np.ndarray,
                    By: np.ndarray, br: np.ndarray, area: np.ndarray,
                    MU0: float) -> np.ndarray:
    """Per magnet: the area-weighted Br the ratchet rule WOULD remove at this
    field (``MagnetDemag.update`` without the update), for locating the worst
    (magnet, instant) of an orbit."""
    from motor_ai_sim.simulation.demag import MagnetDemag
    out = np.zeros(len(mags))
    for i, d in enumerate(mags):
        ix = d["idx"]
        cur = br[ix]
        BdotM = Bx[ix] * d["Mx"] + By[ix] * d["My"]
        H = (BdotM / d["Mm"] - d["Br0"] * cur) / (MU0 * d["mu_r"])
        J_now = d["Br0"] * cur + MU0 * (d["mu_r"] - 1.0) * H
        kk = J_now / np.minimum(H, -1e-9)
        H_op, J_op = MagnetDemag._cross(d, kk)
        Br_new = J_op - (d["mu_rec_c"] - 1.0) * MU0 * H_op
        new = np.minimum(cur, np.clip(Br_new / max(d["Br0"], 1e-12), 0.0, 1.0))
        out[i] = float(np.sum((cur - new) * area[ix]) / max(np.sum(area[ix]), 1e-30))
    return out


def magnet_image_maps(mags: Sequence[Dict[str, Any]], centroids: np.ndarray,
                      areas: np.ndarray, n_sectors: int, bc_sign: int,
                      pole_pitch_rad: float, n_mag: int
                      ) -> Tuple[Optional[Dict[int, np.ndarray]], Dict[str, Any]]:
    """For every magnet element: its image in every other magnet under the
    pole rotations k·pole_pitch (k = 1..n_mag-1, folded into the sector).

    Returns ({k: image element index per magnet element}, info) over the
    concatenated magnet elements (order of ``mags``), or (None, info)."""
    from motor_ai_sim.simulation.rotor_window import MATCH_REL_TOL, _image_maps
    idx = np.concatenate([np.asarray(d["idx"], int) for d in mags])
    cen = np.asarray(centroids, float)[:, idx]
    h = float(np.sqrt(np.min(np.asarray(areas, float)[idx])))
    info: Dict[str, Any] = {"n_elements": int(idx.size)}
    maps, info = _image_maps(cen, MATCH_REL_TOL * max(h, 1e-12), n_sectors,
                             bc_sign, pole_pitch_rad, int(n_mag), info,
                             bijective=True)
    if maps is None:
        return None, info
    return {k + 1: idx[m[0]] for k, m in enumerate(maps)}, info


def map_br_from_reference(mags: Sequence[Dict[str, Any]], ref: int,
                          br: np.ndarray, maps: Dict[int, np.ndarray]
                          ) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Every magnet element takes the Br of its image in magnet ``ref``
    (the owner's shortcut: every pole repeats the reference magnet's
    history, shifted in time).  Returns the new Br array and info."""
    idx = np.concatenate([np.asarray(d["idx"], int) for d in mags])
    in_ref = np.zeros(br.size, bool)
    in_ref[np.asarray(mags[ref]["idx"], int)] = True
    new = br.copy()
    done = np.zeros(idx.size, bool)
    done[in_ref[idx]] = True
    for k in sorted(maps):
        img = maps[k]
        hit = (~done) & in_ref[img]
        new[idx[hit]] = br[img[hit]]
        done |= hit
    return new, {"mapped": int(np.sum(done)), "of": int(idx.size),
                 "complete": bool(np.all(done))}


def image_min_br(mags: Sequence[Dict[str, Any]], br: np.ndarray,
                 maps: Dict[int, np.ndarray]) -> np.ndarray:
    """Element-wise minimum of Br over the pole images: the asymptotic state
    of a fractional-slot rotor, where each magnet sees in one electrical
    period only 1/q of the rotor-frame history that all of them share."""
    idx = np.concatenate([np.asarray(d["idx"], int) for d in mags])
    out = br.copy()
    v = br[idx].copy()
    for k in sorted(maps):
        v = np.minimum(v, br[maps[k]])
    out[idx] = v
    return out


# ════════════════════════════════════════════════════════════════════════
#  acceptance: the owner's observables of a window of frames
# ════════════════════════════════════════════════════════════════════════
def group_joule(A: np.ndarray, U: np.ndarray, a1: np.ndarray, a2: np.ndarray, *,
                dte: float, Mg: Dict[str, Any], GT, S_raw: np.ndarray,
                body_group: Sequence[str]) -> Dict[str, float]:
    """σ∫E² of every conductor GROUP ("cu", "mag", "shaft", "sleeve", …) at one
    frame, with the step's own BDF2 derivative (a1 = A_{k-1}, a2 = A_{k-2}):
    dA'·M_g·dA plus, for each body b of the group, U_b·(U_b·S_b − 2·(G'dA)_b)
    — :meth:`TimePeriodicEddy.joule` split by group [W per metre of sector]."""
    dA = (np.asarray(A, float) - (C_M1 * np.asarray(a1, float)
                                  + C_M2 * np.asarray(a2, float))) / float(dte)
    gd = np.asarray(GT @ dA).ravel()
    U = np.asarray(U, float)
    out = {str(g): float(dA @ (M @ dA)) for g, M in Mg.items()}
    for b, g in enumerate(body_group):
        out[str(g)] = out.get(str(g), 0.0) + float(
            U[b] * (U[b] * float(S_raw[b]) - 2.0 * gd[b]))
    return out


def window_gate(ref: Dict[str, Any], got: Dict[str, Any], *,
                T_rel: float = 1e-3, ripple_pp: float = 0.05,
                P_rel: float = 5e-3, P_floor_rel: float = 1e-4
                ) -> Tuple[bool, Dict[str, Any]]:
    """Compare two windows' observables (``{"T_mean", "ripple_pct",
    "P": {group: W}}``) in the owner's terms: mean torque within ``T_rel``,
    ripple within ``ripple_pp`` percentage points, every conductor group's loss
    within ``P_rel`` of itself or ``P_floor_rel`` of the whole conductor loss
    (a group below that floor cannot move the total or the efficiency)."""
    dT = abs(got["T_mean"] - ref["T_mean"]) / max(abs(ref["T_mean"]), 1e-300)
    dR = abs(got["ripple_pct"] - ref["ripple_pct"])
    Ptot = max(sum(abs(v) for v in ref["P"].values()), 1e-300)
    dP = {}
    ok_P = True
    for g, v in ref["P"].items():
        d = abs(got["P"].get(g, 0.0) - v)
        tol = max(P_rel * abs(v), P_floor_rel * Ptot)
        dP[g] = {"rel": d / max(abs(v), 1e-300), "abs_W_per_m": d,
                 "tol_W_per_m": tol, "ok": bool(d <= tol)}
        ok_P = ok_P and d <= tol
    ok = bool(dT <= T_rel and dR <= ripple_pp and ok_P)
    return ok, {"ok": ok, "T_mean_rel": dT, "ripple_pp": dR, "P": dP,
                "tol": {"T_rel": T_rel, "ripple_pp": ripple_pp, "P_rel": P_rel,
                        "P_floor_rel": P_floor_rel}}


#: Certification safety fraction (third Codex review, 2026-10-04): a TDM result
#: is certified steady only when its BOUNDED error in mean torque, ripple and
#: TOTAL loss is below this fraction of the owner's limits (torque 1 %, ripple
#: max(0.5 pp, 10 % of the ripple), total loss 5 %).
CERT_SAFETY = 0.1


def owner_limits(ripple_pct: float, safety: float = CERT_SAFETY) -> Dict[str, float]:
    """The owner's accuracy terms times ``safety``."""
    return {"T_rel": safety * 0.01,
            "ripple_pp": safety * max(0.5, 0.1 * abs(float(ripple_pct))),
            "P_total_rel": safety * 0.05, "safety": float(safety)}


def certify_observables(cert: Dict[str, Any], *, P_fe_W: float, P_total_W: float,
                        report_windows: Optional[Dict[str, Any]] = None,
                        safety: float = CERT_SAFETY) -> Tuple[bool, Dict[str, Any]]:
    """The certification verdict of a TDM orbit.

    ``cert`` carries the orbit-error BOUND already mapped to the observables
    (the perturbation applied at the bound's size, see the solver):
    ``dT_rel``, ``dripple_pp``, ``dP_cond_W`` (conductor loss, every group),
    ``rB`` (relative change of the iron flux density, area-weighted L2, worst
    frame) and the orbit's ``ripple_pct``.  The iron loss moves by at most
    2·rB·P_fe to first order (P_fe ~ B^2 for the eddy and excess terms, below
    for hysteresis).  The reported period's own deviation from the orbit (the
    report gate's windows) is ADDED: the reported numbers are the orbit plus
    that deviation.  Certified when each total is below ``safety`` x the
    owner's term; a missing or non-finite bound is not certified."""
    lim = owner_limits(float(cert.get("ripple_pct") or 0.0), safety)
    rec: Dict[str, Any] = {"limits": lim}
    try:
        gT = gR = gP = 0.0
        for w in (report_windows or {}).values():
            gT = max(gT, float(w["T_mean_rel"]))
            gR = max(gR, float(w["ripple_pp"]))
            gP = max(gP, sum(float(v["abs_W_per_m"]) for v in w["P"].values()))
        bT = float(cert["dT_rel"]) + gT
        bR = float(cert["dripple_pp"]) + gR
        dP = (float(cert["dP_cond_W"]) + 2.0 * float(cert["rB"]) * max(float(P_fe_W), 0.0)
              + gP)
        bP = dP / max(abs(float(P_total_W)), 1e-300)
        vals = (bT, bR, bP)
        ok = bool(all(math.isfinite(v) for v in vals)
                  and bT <= lim["T_rel"] and bR <= lim["ripple_pp"]
                  and bP <= lim["P_total_rel"])
        rec.update({"bound_T_rel": bT, "bound_ripple_pp": bR,
                    "bound_P_total_rel": bP, "bound_P_total_W": dP,
                    "report_part": {"T_rel": gT, "ripple_pp": gR, "P_W": gP}})
    except (KeyError, TypeError, ValueError) as e:
        ok = False
        rec["error"] = "%s: %s" % (type(e).__name__, e)
    rec["ok"] = bool(ok)
    return bool(ok), rec


def state_closure(pairs: Sequence[Tuple[str, np.ndarray, np.ndarray]],
                  Mg: Dict[str, Any], state_rel: float
                  ) -> Tuple[bool, Dict[str, Any]]:
    """The STATE half of a gate: for every (label, marched, reference) pair —
    the two BDF2 history levels at the end of a period — and every conductor
    group g, the relative sigma-norm distance
    ||A - A_ref||_{M_g} / ||A_ref||_{M_g} <= ``state_rel``.  A group whose
    reference norm is exactly zero (no field in it) is judged on the absolute
    distance against the whole conductor norm.  Any non-finite number fails.

    Returns (ok, {"state_rel": tol, "levels": {label: {group: rel}},
    "worst": max rel, "worst_at": (label, group)})."""
    out: Dict[str, Dict[str, float]] = {}
    worst = 0.0
    worst_at = None
    ok = True
    for label, a, ref in pairs:
        a = np.asarray(a, float)
        ref = np.asarray(ref, float)
        d = a - ref
        tot = 0.0
        for M in Mg.values():
            tot += max(float(ref @ (M @ ref)), 0.0)
        tot = math.sqrt(tot)
        lev = {}
        for g, M in Mg.items():
            num = math.sqrt(max(float(d @ (M @ d)), 0.0))
            den = math.sqrt(max(float(ref @ (M @ ref)), 0.0))
            rel = num / den if den > 0.0 else (num / tot if tot > 0.0 else num)
            if not math.isfinite(rel):
                rel = float("inf")
            lev[str(g)] = rel
            if not rel <= state_rel:
                ok = False
            if rel > worst or worst_at is None:
                worst, worst_at = rel, (label, str(g))
        out[str(label)] = lev
    return bool(ok), {"ok": bool(ok), "state_rel": float(state_rel), "levels": out,
                      "worst": float(worst), "worst_at": worst_at}


def range_distance(P, w: np.ndarray) -> float:
    """||w - P y*|| / ||w||, y* the least-squares coefficients: how far w is
    from the constrained space range(P) of a frame."""
    from scipy.sparse.linalg import splu
    P = P.tocsr()
    w = np.asarray(w, float)
    nw = float(np.linalg.norm(w))
    if nw == 0.0:
        return 0.0
    PtP = (P.T @ P).tocsc()
    rhs = np.asarray(P.T @ w).ravel()
    off = PtP - _diags(PtP.diagonal())
    if off.count_nonzero() == 0:
        y = rhs / np.maximum(PtP.diagonal(), 1e-300)
    else:
        y = splu(PtP).solve(rhs)
    return float(np.linalg.norm(w - np.asarray(P @ y).ravel())) / nw


def period_map_checks(*, wrap: Callable, wrapf: Callable, P_from, P_to, P_start,
                      P_fwd, K, Msig, GT, S_raw: np.ndarray, f_mag: np.ndarray,
                      n_vec: int = 3, seed: int = 0,
                      tol: Optional[Dict[str, float]] = None
                      ) -> Tuple[bool, Dict[str, Any]]:
    """The checks of a period map H (``wrap``: one (half) period back, ``wrapf``:
    forward) that every TDM set-up runs (second Codex review, finding 7):

      * inverse: H^-1 H = I and H H^-1 = I, on arbitrary AND on constrained
        vectors (range of a frame projection);
      * constrained space: H maps range(P_from) (frame N-1) into range(P_to)
        (frame -1) and H^-1 maps range(P_start) (frame 0) into range(P_fwd)
        (frame N) — the periodic BC signs and the slip welds;
      * operators: the stiffness and the sigma-mass bilinear forms invariant on
        the constrained space;
      * conductor bodies: every body maps onto a whole body of equal
        conductance (:func:`body_map_deviation`);
      * magnet source: ||H f - f|| / ||f||.

    Returns (ok, record); ``ok`` is False when any deviation exceeds ``tol``
    (default :data:`MAP_CHECK_RTOL`), and the record names it."""
    tol = dict(MAP_CHECK_RTOL if tol is None else tol)
    rng = np.random.default_rng(seed)
    n = int(P_from.shape[0])
    us = [np.asarray(P_from @ rng.standard_normal(P_from.shape[1])).ravel()
          for _ in range(n_vec)]
    hus = [np.asarray(wrap(u), float) for u in us]
    u0 = [np.asarray(P_start @ rng.standard_normal(P_start.shape[1])).ravel()
          for _ in range(n_vec)]
    # H^-1 H = I on the constrained space, in the CONDUCTOR sigma-norm: the
    # wrapped state enters the march's equations only through the BDF2
    # history, i.e. through sigma*M and G' — the conductor dofs (the forward
    # map gives only Newton STARTS, which each frame re-projects and solves).
    # Measured on the 30 mm fixture: 0 there; over ALL dofs of constrained
    # vectors 0.017 and of arbitrary vectors 0.055 (the non-conducting rotor
    # dofs at the sector edge, which no equation reads through the map) —
    # both recorded, not judged.
    Ms = Msig.tocsr()

    def _snorm(x):
        return math.sqrt(max(float(x @ (Ms @ x)), 0.0))
    inv = 0.0
    inv_all = 0.0
    for v in us + u0:
        nv = max(_snorm(v), 1e-300)
        d1 = wrapf(wrap(v)) - v
        d2 = wrap(wrapf(v)) - v
        inv = max(inv, _snorm(d1) / nv, _snorm(d2) / nv)
        na = max(float(np.linalg.norm(v)), 1e-300)
        inv_all = max(inv_all, float(np.linalg.norm(d1)) / na,
                      float(np.linalg.norm(d2)) / na)
    inv_any = 0.0
    for v in [rng.standard_normal(n) for _ in range(n_vec)]:
        nv = max(float(np.linalg.norm(v)), 1e-300)
        inv_any = max(inv_any, float(np.linalg.norm(wrapf(wrap(v)) - v)) / nv)
    con = max([range_distance(P_to, hu) for hu in hus]
              + [range_distance(P_fwd, wrapf(u)) for u in u0])
    ops = max(bilinear_invariance(K, us, hus), bilinear_invariance(Msig, us, hus))
    bod, image = body_map_deviation(GT, S_raw, us, hus)
    f = np.asarray(f_mag, float)
    src = float(np.linalg.norm(np.asarray(wrap(f), float) - f)
                / max(float(np.linalg.norm(f)), 1e-300))
    dev = {"inverse": inv, "constrained": con, "operators": ops, "bodies": bod,
           "source": src}
    bad = {k: v for k, v in dev.items() if not (v <= tol[k])}
    rec = {"ok": not bad, "dev": dev, "tol": tol, "failed": sorted(bad),
           "inverse_all_dofs": inv_all, "inverse_unconstrained": inv_any,
           "bodies": len(image),
           "bodies_moved": int(sum(1 for b, j in enumerate(image) if b != j))}
    return (not bad), rec


def bilinear_invariance(A, us: Sequence[np.ndarray], hus: Sequence[np.ndarray]
                        ) -> float:
    """How far the bilinear form of the operator ``A`` is from invariant under
    a period map H, tested on CONSTRAINED vectors (``us`` in the range of a
    frame's projection, ``hus`` = H(us)): max over pairs (i, j) of
    |H(u_i)' A H(u_j) - u_i' A u_j| / sqrt(|u_i' A u_i| |u_j' A u_j|).
    Tested on the constrained space because that is where the march's
    equations live (periodic BC signs and slip welds tie the sector-edge and
    band dofs); 0 to round-off for an exactly periodic mesh and material map."""
    A = A.tocsr()
    Au = [np.asarray(A @ u).ravel() for u in us]
    Ahu = [np.asarray(A @ hu).ravel() for hu in hus]
    dev = 0.0
    for i in range(len(us)):
        for j in range(len(us)):
            a0 = float(np.asarray(us[i], float) @ Au[j])
            a1 = float(np.asarray(hus[i], float) @ Ahu[j])
            sc = math.sqrt(abs(float(np.asarray(us[i], float) @ Au[i]))
                           * abs(float(np.asarray(us[j], float) @ Au[j])))
            if sc > 0.0:
                dev = max(dev, abs(a1 - a0) / sc)
            elif a1 != a0:
                dev = float("inf")
    return float(dev)


def body_map_deviation(GT, S_raw: np.ndarray, us: Sequence[np.ndarray],
                       hus: Sequence[np.ndarray]) -> Tuple[float, List[int]]:
    """Every conductor body must map onto a WHOLE body with the same
    conductance.  On constrained test vectors u (and H u): the body fluxes
    c = G'u and c_h = G'(H u) must be a signed permutation of each other, body
    b of H u taking the flux of one body b' of u (with S_b = S_b').  Returns the
    worst relative mismatch over bodies (and conductances) and the image body
    per body; 0 to round-off for a consistent map."""
    S = np.asarray(S_raw, float)
    C = np.stack([np.asarray(GT @ u).ravel() for u in us], axis=1)     # (nb, m)
    Ch = np.stack([np.asarray(GT @ hu).ravel() for hu in hus], axis=1)
    nb = C.shape[0]
    scale = max(float(np.max(np.abs(C))), 1e-300)
    worst = 0.0
    image: List[int] = []
    for b in range(nb):
        dp = np.max(np.abs(C - Ch[b][None, :]), axis=1)
        dm = np.max(np.abs(C + Ch[b][None, :]), axis=1)
        d = np.minimum(dp, dm)
        j = int(np.argmin(d))
        dev = float(d[j]) / max(float(np.max(np.abs(Ch[b]))), 1e-12 * scale)
        ds = abs(S[b] - S[j]) / max(abs(S[b]), 1e-300)
        worst = max(worst, dev, ds)
        image.append(j)
    if len(set(image)) != nb:
        worst = float("inf")          # two bodies onto one: not a bijection
    return float(worst), image


# ════════════════════════════════════════════════════════════════════════
#  demag: how far Br moved, and the pole-pair relabelling of a continued pass
# ════════════════════════════════════════════════════════════════════════
def br_change(mags: Sequence[Dict[str, Any]], br0: np.ndarray, br1: np.ndarray,
              areas: np.ndarray) -> Dict[str, float]:
    """How far the Br map (per rotor element, fraction of Br0) moved between
    two instants: per magnet the area-weighted mean |ΔBr| (the magnet's flux,
    hence torque, change), its maximum over the magnets (the settle measure),
    the area mean over all magnets and the largest single element."""
    per = []
    num = 0.0
    den = 0.0
    emax = 0.0
    for d in mags:
        ix = np.asarray(d["idx"], int)
        a = np.asarray(areas, float)[ix]
        dd = np.abs(np.asarray(br1, float)[ix] - np.asarray(br0, float)[ix])
        s = float(np.sum(a))
        per.append(float(np.sum(dd * a)) / max(s, 1e-30))
        num += float(np.sum(dd * a))
        den += s
        if dd.size:
            emax = max(emax, float(np.max(dd)))
    return {"per_magnet_mean_max": float(max(per) if per else 0.0),
            "area_mean": num / max(den, 1e-30), "element_max": emax}


def relabel_br(br: np.ndarray, elems: np.ndarray, image: np.ndarray) -> np.ndarray:
    """Br carried one electrical period back, exactly as the eddy state is
    (``rotor_window.period_shift_map`` on the magnet-element centroids):
    element ``elems[i]`` takes the Br of element ``elems[image[i]]``.  A pass
    re-run at the SAME rotor angles after this relabelling is the NEXT period
    in time for every magnet (rotor pole-pair periodic), so repeated pre-pass
    periods are one continuous history — as the march's splice makes them for
    the eddy field."""
    out = np.array(br, float, copy=True)
    e = np.asarray(elems, int)
    out[e] = np.asarray(br, float)[e[np.asarray(image, int)]]
    return out
