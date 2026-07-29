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

import numpy as np
from scipy.sparse import bmat as _bmat, diags as _diags

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
                 ed_con=None, G=None, Msig=None, Msd=None, Sdt=None) -> None:
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
        self.S_raw = None
        self.ed_ca = self.ed_cb = None
        if ed_con is not None:
            self._init_eddy_currents(ed_con)

    # Line-to-line Crank–Nicolson circuit residual + its 2×2 Jacobian —
    # simulation/drive.py.  R_phase is the only run-dependent term, so it is
    # bound here rather than captured.
    def circ_r(self, psi, iA, iB, iv_prev, psi_prev, Vt, dtk):
        return circuit_residual_ll(psi, iA, iB, iv_prev, psi_prev, Vt, dtk,
                                   self.R_phase)

    def circ_M(self, qa, qb, dtk):
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
    # Backward Euler on the magnetodynamic system, bordered by ONE integral
    # constraint per current-carrying body:
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
    def eddy_solve(self, Pro, free, A_start, U_start, I_vec, Aprev, nu_fix,
                    maxit):
        """Bordered (A, U) Newton.  Returns (ok, A, U, rrel, nit)."""
        Ae = A_start.copy(); Ue = U_start.copy()
        cr = self.dt * np.asarray(I_vec, float) - np.asarray(self.G.T @ Aprev).ravel()
        rhs_e = self.f_mag + self.Msd @ Aprev
        _rf0 = np.asarray(Pro.T @ rhs_e).ravel()[free]
        _bn = max(float(np.linalg.norm(_rf0)), 1e-30)
        _cn = max(float(np.linalg.norm(cr)), 1e-30)
        Bf = (Pro.T @ self.G).tocsr()[free, :]
        nit = 0; rrel = 1.0

        def _res_e(Av, Uv, Km):
            rf = np.asarray(Pro.T @ ((Km + self.Msd) @ Av - self.G @ Uv
                                     - rhs_e)).ravel()[free]
            rc = self.Sdt * Uv - np.asarray(self.G.T @ Av).ravel() - cr
            return rf, rc, max(float(np.linalg.norm(rf)) / _bn,
                               float(np.linalg.norm(rc)) / _cn)

        # DC seed for the conductor voltages: at ∂A/∂t = 0 the constraint
        # gives U_b = I_b/S_b, which is the bulk of the answer (the eddy
        # reaction is a correction to it).  Starting from U = 0 instead puts
        # the whole ampere-turn drive in the first Newton step, and at 60 A
        # that step lands past the BH knee from an unsaturated start.
        if not np.any(Ue):
            Ue = (np.asarray(I_vec, float) * self.dt
                  / np.maximum(self.Sdt, 1e-30))

        for it in range(max(int(maxit), 2)):
            nit = it + 1
            if nu_fix is not None:
                Km = self.p2.asmK(nu_fix); info = None
            else:
                Km, info = self.p2.Kpw(Ae)
            rf, rc, rrel = _res_e(Ae, Ue, Km)
            if rrel < 1e-7:
                return True, Ae, Ue, rrel, nit
            J = Km
            if info is not None:
                T = self.p2.tangent2(info)
                if T is not None:
                    J = Km + T
            Jff = (Pro.T @ (J + self.Msd) @ Pro).tocsr()[free][:, free]
            Mb = _bmat([[Jff, -Bf], [-Bf.T, _diags(self.Sdt)]]).tocsc()
            try:
                sol = self.p2.solve_ff(Mb, -np.concatenate([rf, rc]))
            except Exception as _je:
                self.log.info("P2 eddy bordered solve failed (%s)", _je)
                return False, Ae, Ue, rrel, nit
            dA = self.p2.pad2(Pro, free, sol[:free.size]); dU = sol[free.size:]
            if nu_fix is not None:        # linear system: the step is exact
                Ae = Ae + dA; Ue = Ue + dU
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
                if float(np.linalg.norm(_res_e(At, Ut, self.p2.Kpw(At)[0])[0])) < _f0:
                    Ae = At; Ue = Ut; acc = True; break
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
                   iv_prev, psi_prev, nu_fix, maxit):
        """Bordered (A, U, i_A, i_B) Newton: coupled σ·∂A/∂t eddy solve WITH
        the line-to-line voltage circuit.  Returns
        (ok, A, U, iA, iB, rrel, nit, rc_circ)."""
        Ae = A_start.copy(); Ue = U_start.copy()
        iA = float(i_start[0]); iB = float(i_start[1])
        Msd_k = (self.Msig * (1.0 / dtk)).tocsr()   # backward Euler on Δt_k
        Sdt_k = self.S_raw * dtk
        rhs_e = self.f_mag + Msd_k @ Aprev
        _GtAp = np.asarray(self.G.T @ Aprev).ravel()
        _rf0 = np.asarray(Pro.T @ rhs_e).ravel()[free]
        _bn = max(float(np.linalg.norm(_rf0)), 1e-30)
        Bf = (Pro.T @ self.G).tocsr()[free, :]
        # constraint-residual scale: the ampere-seconds the terminal current
        # imposes at the DRIVING amplitude, never the instantaneous value.
        _cn = max(float(np.linalg.norm(dtk * (self.ed_ca + self.ed_cb))),
                  float(np.linalg.norm(_GtAp)), 1e-30)
        # voltage scale for the circuit residual test — the driving line-to-
        # line amplitude (the instantaneous value passes through 0).
        _vsc = max(math.sqrt(3.0) * abs(float(self.v_phase_peak)), 1e-3)
        nit = 0; rrel = 1.0
        rcc = np.array([np.inf, np.inf])

        def _res_ve(Av, Uv, ia, ib, Km):
            rf = np.asarray(Pro.T @ ((Km + Msd_k) @ Av - self.G @ Uv
                                     - rhs_e)).ravel()[free]
            rc = (Sdt_k * Uv - np.asarray(self.G.T @ Av).ravel()
                  - (dtk * (ia * self.ed_ca + ib * self.ed_cb) - _GtAp))
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
                    np.concatenate([_z, dtk * self.ed_ca]),
                    np.concatenate([_z, dtk * self.ed_cb])]))
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
