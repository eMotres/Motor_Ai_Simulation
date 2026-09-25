"""The P2 coupled solvers: voltage drive, the bordered eddy-current solve, and
the two together as one Newton.

Three Newtons that look like three features but are one system taken in
increasing generality — (A, i), (A, U), (A, U, i) — and they are here together
for the reason the third one exists at all: the eddy solve imposes each wire's
current as an integral CONSTRAINT while the circuit needs those same currents
as UNKNOWNS, and a code layout that lets the two live apart is exactly what
produced the `if eddy: ... elif vdrive:` chain that answered a voltage question
with a current-drive number.  One class, one ψ, one circuit residual.

Everything nonlinear is delegated to :class:`~motor_ai_sim.simulation.
p2_nonlinear.P2Nonlinear` — the same pointwise ν(|B|) secant stiffness and
differential-reluctivity tangent the magnetostatic frame uses, so no solve path
here can converge on iron the others never saw.

Extracted verbatim from ``fem_transient_sliding_band``, where these were six
closures over the coil source columns, the flux functional, the phase
resistance and the eddy constraint blocks.  Every expression and every comment
recording why an expression is written the way it is moves unchanged: the
LINE-TO-LINE circuit equations, the merit function each line search is measured
on, the DC seed for the conductor voltages and the Δt_k rescale of the eddy
history term were each paid for with a measured wrong answer.

The state is per-run.  ``Pro``/``free`` — the only per-frame quantities — are
arguments on every call.
"""
from __future__ import annotations

import math
import os as _os

import numpy as np
from scipy.sparse import bmat as _bmat, diags as _diags

# Fixed-pattern linear algebra in the bordered Newton: nothing whose
# sparsity pattern holds for the frame is re-merged or re-converted inside
# the iteration loop.  Algebraically identical (same matrices, same solves);
# floating-point sums re-associate at the eps level — measured on the
# validated take-off point (12 steps, eddy+demag, 27 frames): x1.62 wall
# (680.8 s -> 421.2 s), T_avg rel 7.7e-14, ripple 6.3e-13, V_peak 7.8e-14,
# P_cu identical — the same class of noise as MKL's own threaded
# nondeterminism (~1e-14 run to run).  SB_NO_FAST_LA=1 is the escape hatch,
# mirroring SB_NO_PARDISO_REUSE.
_SB_FAST_LA = _os.environ.get("SB_NO_FAST_LA") != "1"

from motor_ai_sim.simulation.drive import (
    circuit_residual_ll, circuit_jacobian_ll,
)
from motor_ai_sim.simulation.field_ops import MU0, _mu_r_from_bh_vec


class P2Drive:
    """Voltage-drive and eddy-current Newtons on one P2 mesh.

    ``psi`` is the flux-linkage functional ψ(A) -> (ψ_A, ψ_B, ψ_C); ``Pa``/
    ``Pb`` are the unit-current source columns with i_C folded in.  The eddy
    arguments are only needed when ``eddy=True`` — ``ed_con`` is the list of
    constrained conductor bodies, from which the per-body terminal-current
    sensitivities c_a / c_b are derived below.
    """

    def __init__(self, *, p2, psi, f_mag, Pa, Pb, R_phase, v_phase_peak,
                 n_dof: int, pic_tol: float, dt: float, log,
                 ed_con=None, G=None, Msig=None, Msd=None, Sdt=None,
                 paths=None) -> None:
        self.p2 = p2
        self.psi = psi
        self.f_mag = f_mag
        self.Pa = Pa
        self.Pb = Pb
        self.R_phase = R_phase
        self.v_phase_peak = v_phase_peak
        self.N = int(n_dof)
        self.pic_tol = float(pic_tol)
        self.dt = float(dt)
        self.log = log
        self.G = G
        self.Msig = Msig
        self.Msd = Msd
        self.Sdt = Sdt
        self._ops_cache = None           # (dte, Msig/dte, S·dte) — see eddy_ops
        self.S_raw = None
        self.ed_ca = self.ed_cb = None
        self.pT = self.pQ = self.p_rep = self.p_mask = None
        if ed_con is not None:
            self._init_eddy_currents(ed_con)
            if paths:
                self._init_strand_paths(ed_con, paths)

    # ═══════════════════════════════════════════════════════════════════
    #  SERIES STRAND PATHS — the soldered-ends winding
    # ═══════════════════════════════════════════════════════════════════
    # Without this, every conductor body carries a KNOWN current: its share of
    # the phase current, imposed row by row.  That is a perfectly transposed
    # winding, and no current can circulate between the strands because none
    # of them is free to differ.
    #
    # A real k-in-hand coil is joined only at its two ends.  So the unknown is
    # not a per-body current but a per-PATH one: strand j of coil g carries the
    # same i_(g,j) through every turn, and the k paths of a coil hold a common
    # terminal voltage W_g while their currents sum to the coil current.  Write
    # T[b, p] = ±1 for "body b lies on path p, traversed this way" and
    # Q[p, g] = 1 for "path p belongs to coil g", and the bordered system
    # becomes, with the SAME field and constraint blocks it already had:
    #
    #   [ K+Msig/dt   −G      0       0   ] [A]   [ f_mag + (Msig/dt)·A_prev ]
    #   [   −Gᵀ      S·dt   −dt·T     0   ] [U]   [      −Gᵀ·A_prev          ]
    #   [    0      −dt·Tᵀ    0     dt·Q  ] [i] = [           0              ]
    #   [    0        0     dt·Qᵀ     0   ] [W]   [        dt·I_coil         ]
    #
    # Row 2 is the old constraint row with its right-hand side turned into an
    # unknown (dt·T·i instead of dt·I_b).  Row 3 says every path of a coil
    # drops the same voltage — Σ_b ±U_b is the path's volts per metre, and U is
    # per unit length, which is why no stack length appears.  Row 4 is
    # Kirchhoff at the solder joint.  The matrix stays SYMMETRIC, which is why
    # rows 3 and 4 carry the dt scaling: it makes block (2,3) = −dt·T the
    # transpose of block (3,2), and the factorisation keeps the same character
    # it has on the transposed winding.
    #
    # The bound this closes: per-strand rows forbid circulation (lower bound),
    # merging a turn's strands into one row lets every turn circulate on its
    # own (upper bound).  The truth is one current per strand per COIL, and
    # that is exactly what the i unknowns are.
    def _init_strand_paths(self, ed_con, paths):
        from scipy.sparse import coo_matrix as _coo
        _pl = paths["paths"]; _pg = paths["group"]
        nb, npth, ng = len(ed_con), len(_pl), int(paths["n_group"])
        _r, _c, _v = [], [], []
        for _p, _mem in enumerate(_pl):
            for _b, _sg in _mem:
                _r.append(int(_b)); _c.append(_p); _v.append(float(_sg))
        self.pT = _coo((_v, (_r, _c)), shape=(nb, npth)).tocsr()
        self.pQ = _coo((np.ones(npth), (np.arange(npth), np.asarray(_pg, int))),
                       shape=(npth, ng)).tocsr()
        self.p_rep = np.asarray(paths["rep"], int)
        _m = np.zeros(nb, bool); _m[np.asarray(_r, int)] = True
        self.p_mask = _m

    def _check_path_current_conservation(self, currents, imposed):
        """Check each solder joint in amperes, including zero-current coils."""
        if not np.all(np.isfinite(currents)) or not np.all(np.isfinite(imposed)):
            raise RuntimeError(
                "series strand paths: non-finite path or imposed coil current")
        with np.errstate(over="ignore", invalid="ignore"):
            total = np.asarray(self.pQ.T @ currents).ravel()
            magnitude = np.asarray(abs(self.pQ).T @ np.abs(currents)).ravel()
            count = np.asarray(abs(self.pQ).sum(axis=0)).ravel()
            error = total - imposed
            # Preserve the loaded-current criterion, but use a 1 pA absolute
            # floor at a zero crossing. Circulating paths can be large while
            # their sum is zero: n*eps*sum(abs(i)) is the summation-roundoff
            # scale, with a 64-fold allowance for the bordered solve. Using
            # 1e-8 times circulation would hide real leakage. All scales are
            # LOCAL to this coil, never borrowed from a heavily loaded one.
            tolerance = (1e-12 + 1e-8 * np.abs(imposed)
                         + 64 * np.finfo(float).eps * np.maximum(count, 1.0)
                         * magnitude)
        bad = (~np.isfinite(total) | ~np.isfinite(magnitude)
               | ~np.isfinite(error) | ~np.isfinite(tolerance)
               | (np.abs(error) > tolerance))
        if np.any(bad):
            group = int(np.flatnonzero(bad)[0])
            raise RuntimeError(
                "series strand paths: coil current not conserved "
                "(coil index %d: sum %.12g A, imposed %.12g A, error %.3e A, "
                "tolerance %.3e A, sum(abs(paths)) %.3e A) — check the "
                "incidence map and bordered linear solve"
                % (group, total[group], imposed[group], error[group],
                   tolerance[group], magnitude[group]))

    # Line-to-line Crank–Nicolson circuit residual + its 2×2 Jacobian —
    # simulation/drive.py.  R_phase is the only run-dependent term, so it is
    # bound here rather than captured.
    def circ_r(self, psi, iA, iB, iv_prev, psi_prev, Vt, dtk):
        return circuit_residual_ll(psi, iA, iB, iv_prev, psi_prev, Vt, dtk,
                                   self.R_phase)

    def circ_M(self, qa, qb, dtk):
        # The LAST dpsi/di columns any solve on this object used.  They are the
        # differential inductance at THIS frame's saturation state and step —
        # with the eddy reaction in them on the coupled path — which is what a
        # correction to psi_prev meets one step later.  The period-mean DC
        # anchor reads them; the phasor initialiser's columns are the fallback
        # and over-state the gain (1.8x, measured on the 30 mm fixture).
        # B5 / PWM study 2026-09-13.
        self.last_qa, self.last_qb = qa, qb
        return circuit_jacobian_ll(qa, qb, dtk, self.R_phase)

    def v_newton(self, Pro, free, A_start, i_start, Vt, dtk, iv_prev,
                 psi_prev, maxit):
        """Coupled (A, i_A, i_B) Newton.  One Jacobian factorization per
        iteration, three back-solves: the field correction and the two
        ∂A/∂i columns.  Returns (ok, A2, iA, iB, rrel, nit, rc)."""
        iA = float(i_start[0]); iB = float(i_start[1])
        A2 = A_start.copy()
        _PtPa = np.asarray(Pro.T @ self.Pa).ravel()[free]
        _PtPb = np.asarray(Pro.T @ self.Pb).ravel()[free]
        # voltage scale for the circuit residual test: the driving line-to-
        # line amplitude (never the instantaneous value, which passes 0).
        _vsc = max(math.sqrt(3.0) * abs(float(self.v_phase_peak)), 1e-3)
        nit = 0; rrel = 1.0
        rc = np.array([np.inf, np.inf])

        def _state(Av, ia, ib):
            fv = self.f_mag + ia * self.Pa + ib * self.Pb
            Kv, iv = self.p2.Kpw(Av)
            rf = np.asarray(Pro.T @ (Kv @ Av - fv)).ravel()[free]
            bn = max(float(np.linalg.norm(
                np.asarray(Pro.T @ fv).ravel()[free])), 1e-30)
            rcv = self.circ_r(self.psi(Av), ia, ib, iv_prev, psi_prev, Vt, dtk)
            return Kv, iv, rf, float(np.linalg.norm(rf)) / bn, rcv

        for it in range(maxit):
            nit = it + 1
            K, info, r_free, rrel, rc = _state(A2, iA, iB)
            if rrel < 1e-7 and float(np.max(np.abs(rc))) < 1e-6 * _vsc:
                return True, A2, iA, iB, rrel, nit, rc
            T = self.p2.tangent2(info)
            J = (K + T).tocsr() if T is not None else K
            Jff = (Pro.T @ J @ Pro).tocsr()[free][:, free].tocsc()
            try:
                X = self.p2.solve_ff(Jff, np.column_stack([-r_free, _PtPa, _PtPb]))
            except Exception as _je:
                self.log.info("P2 vdrive Newton solve failed (%s)", _je)
                return False, A2, iA, iB, rrel, nit, rc
            dA0 = self.p2.pad2(Pro, free, X[:, 0])
            dAa = self.p2.pad2(Pro, free, X[:, 1])
            dAb = self.p2.pad2(Pro, free, X[:, 2])
            # ψ is a LINEAR functional of A, so the linearised flux of the
            # trial step is exact: ψ(A+δ) = ψ(A) + ψ(δ).
            q0 = self.psi(dA0); qa = self.psi(dAa); qb = self.psi(dAb)
            try:
                di = np.linalg.solve(
                    self.circ_M(qa, qb, dtk),
                    np.array([rc[0] - (q0[0] - q0[1]) / dtk,
                              rc[1] - (q0[1] - q0[2]) / dtk]))
            except np.linalg.LinAlgError:
                return False, A2, iA, iB, rrel, nit, rc
            # backtracking line-search on the COMBINED merit (field residual
            # + circuit residual) — the BH knee needs globalising and a step
            # that fixes the field while wrecking the circuit is no step.
            _m0 = rrel + float(np.max(np.abs(rc))) / _vsc
            _lam = 1.0; _acc = False
            for _ls in range(8):
                _At = A2 + _lam * (dA0 + di[0] * dAa + di[1] * dAb)
                _ia = iA + _lam * di[0]; _ib = iB + _lam * di[1]
                _, _, _, _rr, _rcv = _state(_At, _ia, _ib)
                if _rr + float(np.max(np.abs(_rcv))) / _vsc < _m0:
                    A2 = _At; iA = _ia; iB = _ib; _acc = True
                    break
                _lam *= 0.5
            if not _acc:
                return False, A2, iA, iB, rrel, nit, rc
        return False, A2, iA, iB, rrel, nit, rc

    def v_picard(self, Pro, free, nu_start, Vt, dtk, iv_prev, psi_prev, npic,
                 frozen_frame):
        """Damped-Picard fallback — this IS the P1 recipe: ν frozen inside a
        sweep makes A = A_pm + i_A·xa + i_B·xb exact, so the 2×2 circuit is
        solved directly on the apparent inductance.  Returns
        (A2, iA, iB, nu, res, nit)."""
        nu = nu_start.copy()
        _Pt = lambda v: np.asarray(Pro.T @ v).ravel()[free]      # noqa: E731
        A2 = np.zeros(self.N); iA = iB = 0.0; res = 0.0; nit = 0
        _ok = 0; _rp = None; _om = 0.5
        for it in range(max(1, npic)):
            nit = it + 1
            K = self.p2.asmK(nu)
            Kff = (Pro.T @ K @ Pro).tocsr()[free][:, free].tocsc()
            X = self.p2.solve_ff(Kff, np.column_stack(
                [_Pt(self.f_mag), _Pt(self.Pa), _Pt(self.Pb)]))
            A_pm = self.p2.pad2(Pro, free, X[:, 0])
            xa = self.p2.pad2(Pro, free, X[:, 1])
            xb = self.p2.pad2(Pro, free, X[:, 2])
            pm = self.psi(A_pm); qa = self.psi(xa); qb = self.psi(xb)
            _bcv = np.array([
                (Vt['A'] - Vt['B'])
                - ((pm[0] - pm[1]) - (psi_prev['A'] - psi_prev['B'])) / dtk
                - 0.5 * self.R_phase * (iv_prev['A'] - iv_prev['B']),
                (Vt['B'] - Vt['C'])
                - ((pm[1] - pm[2]) - (psi_prev['B'] - psi_prev['C'])) / dtk
                - 0.5 * self.R_phase * (iv_prev['B'] - iv_prev['C'])])
            _iab = np.linalg.solve(self.circ_M(qa, qb, dtk), _bcv)
            iA = float(_iab[0]); iB = float(_iab[1])
            A2 = A_pm + iA * xa + iB * xb
            if frozen_frame or not self.p2.sat:
                break
            _Bm = self.p2.elemB(A2)
            _vo = np.concatenate([nu[_ids] for _ids, _ in self.p2.sat])
            _vn = np.concatenate([
                1.0 / (MU0 * np.maximum(_mu_r_from_bh_vec(_c, _Bm[_ids]), 1.0))
                for _ids, _c in self.p2.sat])
            _rr = _vn - _vo
            res = float(np.linalg.norm(_rr) / max(np.linalg.norm(_vo), 1e-30))
            if _rp is not None:
                _dr = _rr - _rp; _den = float(_dr @ _dr)
                if _den > 0.0:
                    _om = float(np.clip(-_om * float(_rp @ _dr) / _den,
                                        0.05, 1.0))
            _rp = _rr
            _vu = _vo + _om * _rr
            _p0 = 0
            for _ids, _c in self.p2.sat:
                nu[_ids] = _vu[_p0:_p0 + _ids.size]; _p0 += _ids.size
            if res < self.pic_tol:
                _ok += 1
                if _ok >= 2:
                    break
            else:
                _ok = 0
        return A2, iA, iB, nu, res, nit

    # ═══════════════════════════════════════════════════════════════════
    #  COUPLED EDDY-CURRENT SOLVE (σ·∂A/∂t) — BORDERED NEWTON, P2
    # ═══════════════════════════════════════════════════════════════════
    # The magnetodynamic system on one time step, bordered by ONE integral
    # constraint per current-carrying body (dt = the step's EFFECTIVE Δt and
    # A_prev its history A_hist — backward Euler or BDF2, see bdf2_history):
    #
    #   [ K(A) + Msig/dt      −G  ] [A]   [ f_mag + (Msig/dt)·A_prev ]
    #   [      −Gᵀ          S·dt  ] [U] = [ dt·I − Gᵀ·A_prev         ]
    #
    # Row 1 is  ∇·(ν∇A) = −σ(−∂A/∂t + U_b), row 2 is ∫σ(−∂A/∂t + U_b)dΩ = I_b
    # (both scaled by dt so the block is symmetric).  The coil current is NOT
    # a source term any more — it is the constraint, which is the whole point:
    # J redistributes freely inside the conductor and only its NET value is
    # imposed.  σ = 0 everywhere else, so air and laminated iron see exactly
    # the magnetostatic operator they saw before.
    #
    # TIME: A_prev is the previous frame's field PER DOF.  The rotor block of
    # mesh_all is the rotor's MATERIAL frame (the rotation lives entirely in
    # the slip pairing Pro), so a dof tracks a material point on both halves
    # and ∂A/∂t needs no convective term and no re-projection.  Only Pro
    # changes per frame, and it is re-applied to G every frame below.
    #
    # NONLINEARITY: the SAME pointwise ν(|B|) residual + differential-
    # reluctivity tangent the magnetostatic Newton uses (p2_nonlinear
    # Kpw/tangent2), so
    # the eddy solve converges on identical iron physics.  With ν frozen
    # (frozen_nu / no saturable iron) the system is linear and one bordered
    # solve is exact — there is no Picard variant, by design: this branch
    # solves by Newton and code parked in the Picard fallback never runs.
    def eddy_static_state(self, Pro, free, A_start, I_vec, nu_fix, maxit):
        """The ∂A/∂t = 0 limit of the bordered system — the field the eddy
        march STARTS from on a cold run (no-filter pass 2026-09-24, item 7).

        With A_prev = A the constraint rows give U_b = I_b/S_b (uniform current
        in every wire, no current in a floating magnet or shaft) and the field
        rows the magnetostatic problem K(A)·A = f_mag + G·U.  A cold march used
        to start from A_prev = 0 instead: its first step switched the whole
        field on in one Δt, and the rotor-frame DC field then had to DIFFUSE
        into a conducting, magnetic shaft — on the L155 (steel shaft) a mode of
        seconds, i.e. hundreds of electrical periods, and the shaft loss read
        29 W, 15 W, 8.4 W after 2, 5 and 16 periods.  Starting from the static
        field puts that DC in place, as it is on the machine's periodic orbit,
        and leaves only the AC reaction to settle.  Returns (ok, A).
        """
        _Iv = np.asarray(I_vec, float)
        S = np.asarray(self.Sdt, float) / max(float(self.dt), 1e-300)
        U = np.where(np.abs(S) > 0.0, _Iv / np.where(S != 0.0, S, 1.0), 0.0)
        f = self.f_mag + np.asarray(self.G @ U).ravel()
        _bf = np.asarray(Pro.T @ f).ravel()[free]
        _bn = max(float(np.linalg.norm(_bf)), 1e-30)
        A = np.array(A_start, float, copy=True)
        for _it in range(max(int(maxit), 2)):
            if nu_fix is not None:
                K = self.p2.asmK(nu_fix); info = None
            else:
                K, info = self.p2.Kpw(A)
            r = np.asarray(Pro.T @ (K @ A - f)).ravel()[free]
            if float(np.linalg.norm(r)) / _bn < 1e-7:
                return True, A
            J = K
            if info is not None:
                T = self.p2.tangent2(info)
                if T is not None:
                    J = K + T
            Jff = (Pro.T @ J @ Pro).tocsr()[free][:, free].tocsc()
            dA = self.p2.pad2(Pro, free, self.p2.solve_ff(Jff, -r))
            if nu_fix is not None:
                A = A + dA
                continue
            _r0 = float(np.linalg.norm(r)); lam = 1.0; acc = False
            for _ls in range(8):
                At = A + lam * dA
                _Kt = self.p2.Kpw(At)[0]
                if float(np.linalg.norm(np.asarray(
                        Pro.T @ (_Kt @ At - f)).ravel()[free])) < _r0:
                    A = At; acc = True
                    break
                lam *= 0.5
            if not acc:
                return False, A
        return False, A

    # ═══════════════════════════════════════════════════════════════════
    #  TIME DISCRETISATION OF σ·∂A/∂t — ONE EFFECTIVE STEP, ONE HISTORY
    # ═══════════════════════════════════════════════════════════════════
    # Every scheme used here writes the step's derivative as
    #
    #     ∂A/∂t |_k  ≈  (A_k − A_hist) / Δt_eff
    #
    # Backward Euler:  Δt_eff = Δt_k,   A_hist = A_{k−1}          (1st order)
    # BDF2 (variable step, ω = Δt_k/Δt_{k−1}):
    #     Δt_eff = Δt_k·(1+ω)/(1+2ω)
    #     A_hist = [(1+ω)²·A_{k−1} − ω²·A_{k−2}] / (1+2ω)          (2nd order)
    # (ω = 1: Δt_eff = 2Δt/3, A_hist = (4A_{k−1} − A_{k−2})/3.)
    #
    # So the bordered system keeps its exact shape — the σ-mass is divided by
    # Δt_eff, the constraint rows are scaled by Δt_eff, and A_prev becomes
    # A_hist — and the Joule loss ∫σ(−∂A/∂t + U)² is evaluated with the SAME
    # derivative the step solved.  The caller (fem_solver_2d) owns the two
    # history levels and the step ratio; this object only needs Δt_eff.
    # docs/EDDY_TIME_INTEGRATION_2026-09-25.md.
    @staticmethod
    def bdf2_history(h, h_prev, A1, A2):
        """(Δt_eff, A_hist) of the variable-step BDF2 derivative at a step h
        whose predecessor was h_prev, with A1 = A_{k−1}, A2 = A_{k−2}."""
        w = float(h) / float(h_prev)
        dte = float(h) * (1.0 + w) / (1.0 + 2.0 * w)
        Ah = ((1.0 + w) ** 2 * np.asarray(A1, float)
              - w * w * np.asarray(A2, float)) / (1.0 + 2.0 * w)
        return dte, Ah

    def eddy_ops(self, dte):
        """(Msig/Δt_eff, S·Δt_eff, Δt_eff) for an effective step.  ``None`` or
        the construction step returns the matrices built by the caller, so a
        backward-Euler run uses the identical objects it always did."""
        if dte is None or float(dte) == float(self.dt):
            return self.Msd, self.Sdt, self.dt
        dte = float(dte)
        c = self._ops_cache
        if c is None or c[0] != dte:
            _S = (self.S_raw if self.S_raw is not None
                  else np.asarray(self.Sdt, float) / float(self.dt))
            c = (dte, (self.Msig * (1.0 / dte)).tocsr(), _S * dte)
            self._ops_cache = c
        return c[1], c[2], dte

    def eddy_solve(self, Pro, free, A_start, U_start, I_vec, Aprev, nu_fix,
                    maxit, dte=None):
        """Bordered (A, U) Newton.  Returns (ok, A, U, rrel, nit).

        ``Aprev`` is the step's history A_hist and ``dte`` its effective step
        Δt_eff (see ``bdf2_history``); ``dte=None`` is backward Euler on the
        construction step with ``Aprev`` = A_{k−1}.

        With series strand paths bound (``strand_bonding="series"``) the system
        is the augmented (A, U, i, W) one described at ``_init_strand_paths``:
        the bodies on a path no longer carry an imposed current, they carry
        their path's unknown one.
        """
        _Msd, _Sdt, _dt = self.eddy_ops(dte)
        Ae = A_start.copy(); Ue = U_start.copy()
        _Iv = np.asarray(I_vec, float)
        cr = _dt * _Iv - np.asarray(self.G.T @ Aprev).ravel()
        _sp = self.pT is not None
        if _sp:
            # A path body's current is an UNKNOWN: strip the imposed term from
            # its constraint row and leave only the flux history.  Every other
            # body (magnets, shaft, any conductor outside a coil) keeps the row
            # it always had.
            cr = np.where(self.p_mask,
                          -np.asarray(self.G.T @ Aprev).ravel(), cr)
            _npth = self.pT.shape[1]; _ngr = self.pQ.shape[1]
            _dtT = (self.pT * _dt).tocsr()
            _dtQ = (self.pQ * _dt).tocsr()
            # coil current = Σ of its paths' TRANSPOSED currents: the same
            # ampere-turns the per-strand rows imposed, redistributed instead
            # of removed.  (Kirchhoff at the joint cannot change the total.)
            _Ig = np.asarray(self.pQ.T @ _Iv[self.p_rep]).ravel()
            _ip = _Iv[self.p_rep].copy()          # transposed start for i
            _Wg = np.zeros(_ngr)
        rhs_e = self.f_mag + _Msd @ Aprev
        _rf0 = np.asarray(Pro.T @ rhs_e).ravel()[free]
        _bn = max(float(np.linalg.norm(_rf0)), 1e-30)
        # The constraint residual is judged RELATIVE to the size of the
        # constraint equation, not to `cr` alone: at I = 0 on a cold frame
        # cr = dt·0 − Gᵀ·0 is exactly zero, so a machine-zero residual divided
        # by 1e-30 read as rrel ≈ 1e15 and the no-load run was refused twice
        # (user 2026-09-05: "запускаю с 0 A и не могу получить результата").
        # The magnet flux linked by the eddy bodies (GᵀA) is the natural scale
        # of the equation when the drive term vanishes; it is folded in per
        # iterate below, so the criterion means the same thing at 0 A and at
        # 600 A.
        _cn = max(float(np.linalg.norm(cr)),
                  float(np.linalg.norm(_dt * _Iv)),
                  float(np.linalg.norm(np.asarray(self.G.T @ A_start).ravel())),
                  1e-30)
        Bf = (Pro.T @ self.G).tocsr()[free, :]
        nit = 0; rrel = 1.0
        # ── SB_FAST_LA per-FRAME precompute ──────────────────────────────
        # Everything here has a pattern fixed for the whole frame: the
        # projection restricted to the free set, and Msd in that basis.
        # Doing it once kills the per-iteration (J+Msd) N-sized merge, the
        # [free][:, free] slicing (a CSR→CSC conversion each) and one of the
        # two big projection products.
        if _SB_FAST_LA:
            _Pt = Pro.T.tocsr()
            _Pf = Pro.tocsc()[:, free].tocsr()
            _PfT = _Pf.T.tocsr()
            _Msd_ff = (_PfT @ (_Msd @ _Pf)).tocsr()

        def _res_e(Av, Uv, Km, iv=None, Wv=None):
            if _SB_FAST_LA:
                _t = Km @ Av + _Msd @ Av - self.G @ Uv - rhs_e
                rf = np.asarray(_Pt @ _t).ravel()[free]
            else:
                rf = np.asarray(Pro.T @ ((Km + _Msd) @ Av - self.G @ Uv
                                         - rhs_e)).ravel()[free]
            _GtA = np.asarray(self.G.T @ Av).ravel()
            rc = _Sdt * Uv - _GtA - cr
            # scale of the constraint equation at THIS iterate (see _cn)
            _cden = max(_cn, float(np.linalg.norm(_GtA)),
                        float(np.linalg.norm(_Sdt * Uv)))
            if not _sp:
                return rf, rc, max(float(np.linalg.norm(rf)) / _bn,
                                   float(np.linalg.norm(rc)) / _cden)
            rc = rc - np.asarray(_dtT @ iv).ravel()
            rp = (-np.asarray(_dtT.T @ Uv).ravel()
                  + np.asarray(_dtQ @ Wv).ravel())
            rg = np.asarray(_dtQ.T @ iv).ravel() - _dt * _Ig
            return rf, np.concatenate([rc, rp, rg]), max(
                float(np.linalg.norm(rf)) / _bn,
                float(np.linalg.norm(np.concatenate([rc, rp, rg]))) / _cden)

        # DC seed for the conductor voltages: at ∂A/∂t = 0 the constraint
        # gives U_b = I_b/S_b, which is the bulk of the answer (the eddy
        # reaction is a correction to it).  Starting from U = 0 instead puts
        # the whole ampere-turn drive in the first Newton step, and at 60 A
        # that step lands past the BH knee from an unsaturated start.
        if not np.any(Ue):
            Ue = (np.asarray(I_vec, float) * _dt
                  / np.maximum(_Sdt, 1e-30))

        for it in range(max(int(maxit), 2)):
            nit = it + 1
            if nu_fix is not None:
                Km = self.p2.asmK(nu_fix); info = None
            else:
                Km, info = self.p2.Kpw(Ae)
            rf, rc, rrel = _res_e(Ae, Ue, Km, (_ip if _sp else None),
                                  (_Wg if _sp else None))
            if rrel < 1e-7:
                if _sp:
                    self.path_currents = _ip.copy()
                    # INVARIANT, checked every frame rather than argued: the
                    # solder joint cannot create or destroy current, so the
                    # paths of a coil must still sum to the ampere-turns the
                    # transposed rows imposed.  If this ever drifts, the field
                    # is being driven by a winding the caller did not ask for.
                    self._check_path_current_conservation(_ip, _Ig)
                    # the circulating part: how far each path sits from the
                    # equal split its coil would have if it were transposed.
                    _kg = np.asarray(self.pQ.sum(axis=0)).ravel()
                    _eq = np.asarray(
                        self.pQ @ (np.asarray(self.pQ.T @ _ip).ravel()
                                   / np.maximum(_kg, 1.0))).ravel()
                    self.path_circ = float(np.max(np.abs(_ip - _eq)))
                    self.path_share = _ip.copy()
                return True, Ae, Ue, rrel, nit
            J = Km
            if info is not None:
                T = self.p2.tangent2(info)
                if T is not None:
                    J = Km + T
            if _SB_FAST_LA:
                Jff = ((_PfT @ (J @ _Pf)) + _Msd_ff).tocsr()
            else:
                Jff = (Pro.T @ (J + _Msd) @ Pro).tocsr()[free][:, free]
            if _sp:
                Mb = _bmat([[Jff,   -Bf,               None,     None],
                            [-Bf.T, _diags(_Sdt),  -_dtT,    None],
                            [None,  -_dtT.T,           None,     _dtQ],
                            [None,  None,              _dtQ.T,   None]]).tocsc()
            else:
                Mb = _bmat([[Jff, -Bf], [-Bf.T, _diags(_Sdt)]]).tocsc()
            try:
                sol = self.p2.solve_ff(Mb, -np.concatenate([rf, rc]))
            except Exception as _je:
                self.log.info("P2 eddy bordered solve failed (%s)", _je)
                return False, Ae, Ue, rrel, nit
            dA = self.p2.pad2(Pro, free, sol[:free.size])
            _nU = _Sdt.size
            dU = sol[free.size:free.size + _nU]
            if _sp:
                _di = sol[free.size + _nU:free.size + _nU + _npth]
                _dW = sol[free.size + _nU + _npth:]
            if nu_fix is not None:        # linear system: the step is exact
                Ae = Ae + dA; Ue = Ue + dU
                if _sp:
                    _ip = _ip + _di; _Wg = _Wg + _dW
                continue
            # Backtracking line-search on the FIELD residual — the same test
            # the magnetostatic Newton uses, and the only one that means
            # anything here: the constraint block is LINEAR, so a damped step
            # scales its residual by exactly (1−λ) and it can never be what
            # blocks progress.  Testing the two together (max of the relative
            # norms) made the constraint residual, which is ~1 by
            # construction on the first sweep, veto every field-reducing step
            # and the solve stalled at rrel≈0.97 at 60 A.
            _f0 = float(np.linalg.norm(rf))
            lam = 1.0; acc = False
            for _ls in range(8):
                At = Ae + lam * dA; Ut = Ue + lam * dU
                _it_ = (_ip + lam * _di) if _sp else None
                _Wt_ = (_Wg + lam * _dW) if _sp else None
                if float(np.linalg.norm(_res_e(At, Ut, self.p2.Kpw(At)[0],
                                               _it_, _Wt_)[0])) < _f0:
                    Ae = At; Ue = Ut; acc = True
                    if _sp:
                        _ip = _it_; _Wg = _Wt_
                    break
                lam *= 0.5
            if not acc:
                return False, Ae, Ue, rrel, nit
        return False, Ae, Ue, rrel, nit

    # ═══════════════════════════════════════════════════════════════════
    #  COUPLED EDDY **AND** VOLTAGE DRIVE — ONE (A, U, i_A, i_B) NEWTON
    # ═══════════════════════════════════════════════════════════════════
    # The two features look mutually exclusive — the eddy solve imposes each
    # wire's current as an integral CONSTRAINT, the circuit needs those same
    # currents as UNKNOWNS — but they are not.  The constraint VALUE simply
    # becomes a function of the circuit state:
    #
    #     I_b(i) = Iunit_b · i_phase(b),     i_C = −i_A − i_B
    #
    # so the winding current still never appears as a source term (it must
    # not: under eddy the ampere-turns enter through the constraint row and
    # putting them in f as well drives the machine twice), and the bordered
    # system keeps its exact structure.  Only the constraint RHS moves:
    #
    #   [ K(A)+Msig/dt   −G  ] [A]   [ f_mag + (Msig/dt)·A_prev            ]
    #   [    −Gᵀ        S·dt ] [U] = [ dt·(i_A·c_a + i_B·c_b) − Gᵀ·A_prev  ]
    #        └──────── M_b ───────┘
    #   plus the two LINE-TO-LINE circuit equations on ψ(A), i.
    #
    # with c_a = ∂I_vec/∂i_A, c_b = ∂I_vec/∂i_B (zero on the ∫J=0 rotor
    # bodies — magnets and shaft carry no terminal current).
    #
    # The Newton step is therefore the SAME shape as the magnetostatic
    # voltage drive (_v_newton): one factorization of M_b per iteration and
    # three back-solves — the field/voltage correction and the two ∂(A,U)/∂i
    # columns.  Those columns are back-solves of the BORDERED matrix with a
    # pure CONSTRAINT rhs [0; dt·c], i.e. "inject one more ampere into that
    # wire and let the eddy reaction redistribute it", which is exactly the
    # differential inductance the circuit Jacobian needs — now including the
    # eddy reaction, which is the whole point of running the two together.
    #
    # ψ is a linear functional of A, so the linearised flux of a trial step
    # is exact and the 2×2 circuit reduction is identical to _v_newton's.
    #
    # NOTHING here is a fallback path: if this does not converge the frame
    # RAISES (see the call site).  Reporting a current-drive answer as a
    # voltage run — what the P1 `if eddy: … elif _vdrive:` chain does — is
    # the failure mode this whole function exists to avoid.
    #
    # TIME STEP: the eddy history term is discretised on the SAME Δt_k the
    # circuit uses — the ACTUAL (slip-node-snapped) rotor time, not the
    # nominal dt.  Under current drive the two are interchangeable because
    # nothing else in the frame carries a time scale; here the circuit
    # already divides Δψ by Δt_k, and feeding σ·∂A/∂t a different Δt would
    # reintroduce exactly the node-quantisation sawtooth the rotor-time step
    # exists to remove — into the eddy loss this time instead of the current.
    # (On the pinned geometries Δt_k == dt to the last bit: a frame spans a
    # whole number of slip nodes.  The rescale is a scalar on a fixed matrix,
    # so it costs nothing and it stays right when it stops being exact.)
    def _init_eddy_currents(self, ed_con):
        self.S_raw = np.array([c["S"] for c in ed_con], float)   # S_b = ∫σ dΩ
        self.ed_ca = np.zeros(len(ed_con)); self.ed_cb = np.zeros(len(ed_con))
        for _ci, _c in enumerate(ed_con):
            if _c["key"] != "cu":
                continue                   # ∫J = 0 body: no terminal current
            _iu = float(_c["Iunit"])
            if _c["phase"] == 'A':
                self.ed_ca[_ci] = _iu
            elif _c["phase"] == 'B':
                self.ed_cb[_ci] = _iu
            else:                          # i_C = −i_A − i_B
                self.ed_ca[_ci] = -_iu; self.ed_cb[_ci] = -_iu

    def ve_newton(self, Pro, free, A_start, U_start, i_start, Aprev, Vt, dtk,
                   iv_prev, psi_prev, nu_fix, maxit, dte=None):
        """Bordered (A, U, i_A, i_B) Newton: coupled σ·∂A/∂t eddy solve WITH
        the line-to-line voltage circuit.  Returns
        (ok, A, U, iA, iB, rrel, nit, rc_circ).

        TWO STEPS, ONE TIME LEVEL.  ``dtk`` is the rotor-time step the circuit
        integrates its volt-seconds over (Crank–Nicolson, drive.
        circuit_residual_ll — second order, and exact for the piecewise-
        constant inverter voltage because it integrates the step's MEAN
        voltage).  ``dte`` is the eddy term's effective step and ``Aprev`` its
        history (``bdf2_history``; ``None`` = backward Euler, dte = dtk,
        Aprev = A_{k−1}).  Both close at t_k: the constraint rows impose the
        wire currents i_k the circuit solves for, so the two second-order
        discretisations meet at the same instant."""
        if self.pT is not None:
            raise RuntimeError(
                "strand_bonding='series' is implemented on the current-drive "
                "eddy Newton only: the path currents and the terminal currents "
                "would have to be solved as one circuit, and silently dropping "
                "the strand paths would report a transposed winding as a "
                "soldered one")
        Ae = A_start.copy(); Ue = U_start.copy()
        iA = float(i_start[0]); iB = float(i_start[1])
        # eddy term on its effective step (backward Euler: dte = Δt_k)
        dte = float(dtk) if dte is None else float(dte)
        Msd_k = (self.Msig * (1.0 / dte)).tocsr()
        Sdt_k = self.S_raw * dte
        rhs_e = self.f_mag + Msd_k @ Aprev
        _GtAp = np.asarray(self.G.T @ Aprev).ravel()
        _rf0 = np.asarray(Pro.T @ rhs_e).ravel()[free]
        _bn = max(float(np.linalg.norm(_rf0)), 1e-30)
        Bf = (Pro.T @ self.G).tocsr()[free, :]
        # constraint-residual scale: the ampere-seconds the terminal current
        # imposes at the DRIVING amplitude, never the instantaneous value.
        _cn = max(float(np.linalg.norm(dte * (self.ed_ca + self.ed_cb))),
                  float(np.linalg.norm(_GtAp)), 1e-30)
        # voltage scale for the circuit residual test — the driving line-to-
        # line amplitude (the instantaneous value passes through 0).
        _vsc = max(math.sqrt(3.0) * abs(float(self.v_phase_peak)), 1e-3)
        nit = 0; rrel = 1.0
        rcc = np.array([np.inf, np.inf])

        # Same fixed-projection optimization as eddy_solve, scoped to THIS
        # frame: both the slip projection and the snapped timestep can change
        # on the next call. Keep the timestep-scaled mass matrix out of the
        # per-Newton sparse merge/projection and line-search matrix additions.
        if _SB_FAST_LA:
            _Pt = Pro.T.tocsr()
            _Pf = Pro.tocsc()[:, free].tocsr()
            _PfT = _Pf.T.tocsr()
            _Msd_ff = (_PfT @ (Msd_k @ _Pf)).tocsr()

        def _res_ve(Av, Uv, ia, ib, Km):
            if _SB_FAST_LA:
                _t = Km @ Av + Msd_k @ Av - self.G @ Uv - rhs_e
                rf = np.asarray(_Pt @ _t).ravel()[free]
            else:
                rf = np.asarray(Pro.T @ ((Km + Msd_k) @ Av - self.G @ Uv
                                         - rhs_e)).ravel()[free]
            rc = (Sdt_k * Uv - np.asarray(self.G.T @ Av).ravel()
                  - (dte * (ia * self.ed_ca + ib * self.ed_cb) - _GtAp))
            rcv = self.circ_r(self.psi(Av), ia, ib, iv_prev, psi_prev, Vt, dtk)
            return rf, rc, rcv, max(float(np.linalg.norm(rf)) / _bn,
                                    float(np.linalg.norm(rc)) / _cn)

        # DC seed for the conductor voltages (same as the current-drive eddy
        # solve): at ∂A/∂t = 0 the constraint gives U_b = I_b/S_b.
        if not np.any(Ue):
            Ue = ((iA * self.ed_ca + iB * self.ed_cb) / np.maximum(self.S_raw, 1e-30))

        for it in range(max(int(maxit), 2)):
            nit = it + 1
            if nu_fix is not None:
                Km = self.p2.asmK(nu_fix); info = None
            else:
                Km, info = self.p2.Kpw(Ae)
            rf, rc, rcc, rrel = _res_ve(Ae, Ue, iA, iB, Km)
            if rrel < 1e-7 and float(np.max(np.abs(rcc))) < 1e-6 * _vsc:
                return True, Ae, Ue, iA, iB, rrel, nit, rcc
            J = Km
            if info is not None:
                T = self.p2.tangent2(info)
                if T is not None:
                    J = Km + T
            if _SB_FAST_LA:
                Jff = ((_PfT @ (J @ _Pf)) + _Msd_ff).tocsr()
            else:
                Jff = (Pro.T @ (J + Msd_k) @ Pro).tocsr()[free][:, free]
            Mb = _bmat([[Jff, -Bf], [-Bf.T, _diags(Sdt_k)]]).tocsc()
            # column 0: the (A, U) correction at frozen current
            # columns 1,2: ∂(A, U)/∂i_A and ∂(A, U)/∂i_B — a pure CONSTRAINT
            #              rhs, the eddy-reaction-included differential
            #              inductance the circuit Jacobian needs.
            _z = np.zeros(free.size)
            try:
                X = self.p2.solve_ff(Mb, np.column_stack([
                    -np.concatenate([rf, rc]),
                    np.concatenate([_z, dte * self.ed_ca]),
                    np.concatenate([_z, dte * self.ed_cb])]))
            except Exception as _je:
                self.log.info("P2 eddy+vdrive bordered solve failed (%s)", _je)
                return False, Ae, Ue, iA, iB, rrel, nit, rcc
            dA0 = self.p2.pad2(Pro, free, X[:free.size, 0])
            dAa = self.p2.pad2(Pro, free, X[:free.size, 1])
            dAb = self.p2.pad2(Pro, free, X[:free.size, 2])
            dU0 = X[free.size:, 0]
            dUa = X[free.size:, 1]; dUb = X[free.size:, 2]
            q0 = self.psi(dA0); qa = self.psi(dAa); qb = self.psi(dAb)
            try:
                di = np.linalg.solve(
                    self.circ_M(qa, qb, dtk),
                    np.array([rcc[0] - (q0[0] - q0[1]) / dtk,
                              rcc[1] - (q0[1] - q0[2]) / dtk]))
            except np.linalg.LinAlgError:
                return False, Ae, Ue, iA, iB, rrel, nit, rcc
            dA = dA0 + di[0] * dAa + di[1] * dAb
            dU = dU0 + di[0] * dUa + di[1] * dUb
            if nu_fix is not None:        # linear system: the step is exact
                Ae = Ae + dA; Ue = Ue + dU
                iA += float(di[0]); iB += float(di[1])
                continue
            # Backtracking line-search on the FIELD residual only — for the
            # same reason the current-drive eddy solve uses it: the ONLY
            # nonlinearity in this system is K(A).  Both the constraint rows
            # and the circuit rows are exactly LINEAR in (A, U, i), so a
            # damped step scales their residuals by exactly (1−λ) and they
            # can never be what blocks progress; including them in the merit
            # instead lets a residual that is ~1 by construction on the first
            # sweep veto every field-reducing step (measured: stall at
            # rrel≈0.97 on the current-drive path at 60 A).
            _f0 = float(np.linalg.norm(rf))
            lam = 1.0; acc = False
            for _ls in range(8):
                At = Ae + lam * dA; Ut = Ue + lam * dU
                _ia = iA + lam * float(di[0]); _ib = iB + lam * float(di[1])
                if float(np.linalg.norm(
                        _res_ve(At, Ut, _ia, _ib, self.p2.Kpw(At)[0])[0])) < _f0:
                    Ae = At; Ue = Ut; iA = _ia; iB = _ib
                    acc = True; break
                lam *= 0.5
            if not acc:
                return False, Ae, Ue, iA, iB, rrel, nit, rcc
        return False, Ae, Ue, iA, iB, rrel, nit, rcc
