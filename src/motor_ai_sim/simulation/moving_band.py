"""The air-gap coupling between the rotor and stator halves: slip-ring node
selection, the closed-form moving-band strip, and the harmonic macroelement.

This is the part of the sliding-band transient that knows how the two half
meshes are joined across the gap, and nothing else. Both couplings work on the
SAME pair of uniform N-gon rings and both answer the same two questions per
frame — "what is the stiffness at rotor shift m" and "what is the torque of this
field at rotor shift m" — so they belong behind one interface instead of a pair
of if/else expressions repeated at four call sites in the frame loops.

Extracted verbatim from ``fem_transient_sliding_band``, where it was ten
closures over ``Nring``, ``spacing``, ``_bc_sign``, the ring DOF arrays and the
global DOF count. Every expression, and every comment that records why an
expression is written the way it is, is unchanged: the signed-order phase, the
per-unit-length normalisation and the ``N_full`` (not ``Nw``) DFT scaling were
each paid for with a measured wrong answer, and the notes below are the receipt.
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

import numpy as np
from scipy.sparse import coo_matrix as _coo

from motor_ai_sim.simulation.field_ops import MU0


def slip_ring_nodes(P: np.ndarray, r_at: float, n_slip: int) -> np.ndarray:
    """Node ids of the SEEDED uniform ring at radius ``r_at``, sorted by angle.

    Select the SEEDED ring nodes only: radius window + snap-to-grid in
    angle.  A bare radius window also sweeps in foreign free-mesh nodes
    that happen to sit within microns of the ring radius (the stator
    free row is only ~0.13 mm thick) — those polluted the pairing.
    """
    r = np.hypot(P[0], P[1])
    idx = np.where(np.abs(r - r_at) < 1e-6)[0]
    ang = np.degrees(np.arctan2(P[1, idx], P[0, idx])) % 360.0
    step = 360.0 / n_slip
    kg = np.round(ang / step)
    on_grid = np.abs(ang - kg * step) < (0.05 * step)
    idx, ang, kg = idx[on_grid], ang[on_grid], kg[on_grid].astype(int) % n_slip
    # one node per grid slot (keep the angularly-closest if duplicated)
    if kg.size:
        order = np.lexsort((np.abs(ang - np.round(ang / step) * step), kg))
        idx, kg = idx[order], kg[order]
        keep = np.concatenate([[True], np.diff(kg) != 0])
        idx, kg = idx[keep], kg[keep]
        o = np.argsort(kg)
        idx = idx[o]
    return idx


def _tri_template(P3) -> Tuple[np.ndarray, np.ndarray, np.ndarray, float, float]:
    (x1, y1), (x2, y2), (x3, y3) = P3
    bb = np.array([y2 - y3, y3 - y1, y1 - y2])
    cc = np.array([x3 - x2, x1 - x3, x2 - x1])
    area = 0.5 * abs(cc[2] * bb[1] - cc[1] * bb[2])
    Kl = (np.outer(bb, bb) + np.outer(cc, cc)) / (4.0 * area * MU0)
    cxl = (x1 + x2 + x3) / 3.0; cyl = (y1 + y2 + y3) / 3.0
    rcl = math.hypot(cxl, cyl); cp, sp = cxl / rcl, cyl / rcl
    # B = (∂A/∂y, −∂A/∂x);  Br = u·A,  Bφ = v·A  (template frame —
    # rotationally invariant, so valid for every quad of the ring)
    u = ( cc * cp - bb * sp) / (2.0 * area)
    v = (-cc * sp - bb * cp) / (2.0 * area)
    return Kl, u, v, area, rcl


def _pol(r_: float, a_: float) -> Tuple[float, float]:
    return (r_ * math.cos(a_), r_ * math.sin(a_))


class MovingBand:
    """Rotor↔stator gap coupling on two uniform rings at r1 (rotor) and r2.

    The annulus R1..R2 is re-stitched EVERY frame; see _simplify_polys: each
    ring is a UNIFORM N-gon, so the stitch pattern is IDENTICAL at every
    shift m — two congruent triangle shapes whose local stiffness (air) and
    torque vectors are computed ONCE; per frame only the index mapping
    (rotor k ↔ stator k+m, anti-periodic sign on wrap) changes.  This
    replaces the node-merge slip coupling whose frozen irregular fans
    produced the order-6 parasitic cogging.

    ``use_macro`` swaps the triangle strip for the harmonic macroelement; the
    two are interchangeable through :meth:`K` and :meth:`torque`.
    """

    def __init__(self, *, n_dof: int, rring: np.ndarray, sring: np.ndarray,
                 nsn: int, n_ring: int, spacing_deg: float, full_ring: bool,
                 bc_sign: float, r1_m: float, r2_m: float,
                 stack_length_m: float, use_macro: bool) -> None:
        self.n = int(n_dof)
        self.Nring = int(n_ring)
        self.spacing = float(spacing_deg)
        self.full_ring = bool(full_ring)
        self.bc_sign = bc_sign
        self.r1_m = float(r1_m)
        self.r2_m = float(r2_m)
        self.stack_length = float(stack_length_m)
        self.use_macro = bool(use_macro)

        _gR1 = rring.astype(int) + nsn          # rotor-ring DOFs (global ids)
        _gR2 = sring.astype(int)                # stator-ring DOFs
        self._gR1 = _gR1
        self._gR2 = _gR2
        _dphi_b = math.radians(self.spacing)

        Nring = self.Nring
        _bc_sign = self.bc_sign
        _full_ring = self.full_ring
        _r1_m, _r2_m = self.r1_m, self.r2_m

        # ── Harmonic air-gap macroelement (Davat) — analytic gap, smooth torque ──
        # Couple the two uniform rings by the EXACT Laplace solution of the gap
        # annulus instead of the single-layer triangle strip.  Both rings are
        # uniform N-gons ⇒ the coupling is block-circulant and DFT-diagonalises into
        # 2×2 per-harmonic blocks; rotor rotation by m nodes is a smooth phase
        # e^{i·k·φ} (no node re-pairing) → the broadband sliding-band ripple is gone
        # at the source.  Per-harmonic stiffness + nodal assembly validated standalone
        # (energy == analytic == FEM annulus; m-shift == circulant shift).  Full-ring
        # only for now (sector anti-periodic harmonics deferred).
        if self.use_macro:
            # SECTOR generalisation: a wedge of 1/S of the machine carries the
            # (anti-)periodic harmonic ladder k = S·(m + moff), moff = 1/2 when
            # the wedge field is ANTI-periodic (odd pole count per wedge, i.e.
            # _bc_sign = −1) and 0 when periodic.  The half-integer ladder makes
            # the circulant a SKEW-circulant automatically (col(d−Nw) = −col(d)),
            # so the anti-periodic wrap sign needs no special-casing.  Full ring
            # is the S=1, moff=0 member of the same family.
            if _full_ring:
                _NwM = int(Nring)                  # independent ring nodes in model
                _SfacM = 1
                _moffM = 0.0
            else:
                _NwM = int(Nring) - 1              # open wedge: last node == first via cut
                _SfacM = max(1, int(round(360.0 / (_NwM * float(self.spacing)))))
                _moffM = 0.5 if float(_bc_sign) < 0 else 0.0
            _NfullM = _NwM * _SfacM                # full-circle node count (order base)
            _stkM = self.stack_length
            self._NwM, self._SfacM, self._moffM = _NwM, _SfacM, _moffM
            self._NfullM, self._stkM = _NfullM, _stkM

            # PER-UNIT-LENGTH normalisation: the half-mesh K_const carries no stack
            # length (2D solve; L is applied later in the torque/loss post-processing,
            # exactly like _T_band).  So the gap coupling must also be per-unit — the
            # stack length _stkM is applied only in _T_macro below.  (Baking L in here
            # made the gap ~1/L weaker than the iron → decoupled, garbage field.)
            # Normalise by N_FULL, not Nw: the Qk annulus form integrates the WHOLE
            # 2π gap, so the ladder with G=2π/(MU0·Nfull) carries the machine energy;
            # the wedge's 1/S share then comes out of the Nw-point DFT automatically.
            # The old G=2π/(MU0·Nw) ("wedge = 1/S ⇒ S·G" reasoning) double-counted S:
            # measured K_wedge == S × the energy restriction (1/S)·UᵀK_full·U on EVERY
            # mode — a uniformly 4×-stiff gap on the 1/4.  That barely moves the
            # fundamental (ψ −3%, T_avg −1% — why mean checks passed) but skews the
            # high-harmonic field balance → spurious torque orders that grow with
            # steps/period (sector 22→30% vs full 15%).  Full ring: Nfull==Nw, no-op.
            _Gm = 2.0 * math.pi / (MU0 * _NfullM)         # DFT energy normalisation (per-unit)
            _kphysM = _SfacM * (np.arange(_NwM) + _moffM)  # physical order per bin
            _kfoldM = np.minimum(_kphysM, _NfullM - _kphysM)   # fold to 0..Nfull/2
            _mu_rr = np.empty(_NwM); _mu_rs = np.empty(_NwM); _mu_ss = np.empty(_NwM)
            for _j in range(_NwM):
                _q11, _q12, _q22 = self._Qk_gap(float(_kfoldM[_j]))
                _mu_rr[_j], _mu_rs[_j], _mu_ss[_j] = _Gm*_q11, _Gm*_q12, _Gm*_q22
            for _j in range(_NwM):                         # unpaired bins counted once
                if _kphysM[_j] == 0 or 2 * _kphysM[_j] == _NfullM:
                    _mu_rr[_j] *= 0.5; _mu_rs[_j] *= 0.5; _mu_ss[_j] *= 0.5
            _jfreq = _kphysM.copy()                        # signed PHYSICAL order (for ∂/∂φ)
            _jfreq[_jfreq > _NfullM/2] -= _NfullM
            _ii_m = (np.arange(_NwM)[:, None] - np.arange(_NwM)[None, :]) % _NwM
            # Half-integer twist: FFT bins live at (m+moff)/Nw cycles per node.
            _twn = np.exp(-1j * 2.0 * np.pi * _moffM * np.arange(_NwM) / _NwM)
            _twd = np.conj(_twn)                           # e^{+i·2π·moff·d/Nw}
            _gR1M = _gR1[:_NwM]; _gR2M = _gR2[:_NwM]
            self._mu_rs = _mu_rs; self._jfreq = _jfreq; self._ii_m = _ii_m
            self._twn = _twn; self._twd = _twd
            self._gR1M = _gR1M; self._gR2M = _gR2M

            _Krr_blk = self._circ_of(_mu_rr)               # rotor-rotor  (m-independent)
            _Kss_blk = self._circ_of(_mu_ss)               # stator-stator(m-independent)
            _Rg1, _Cg1 = np.meshgrid(_gR1M, _gR1M, indexing="ij")
            _Rg2, _Cg2 = np.meshgrid(_gR2M, _gR2M, indexing="ij")
            _Rg12, _Cg12 = np.meshgrid(_gR1M, _gR2M, indexing="ij")
            self._Krr_blk, self._Kss_blk = _Krr_blk, _Kss_blk
            self._Rg1, self._Cg1 = _Rg1, _Cg1
            self._Rg2, self._Cg2 = _Rg2, _Cg2
            self._Rg12, self._Cg12 = _Rg12, _Cg12

            self._T_macro = self._T_macro_wedge
            if not _full_ring:
                # ── SECTOR torque via UNFOLD to the validated full-ring formula ──
                # The wedge-native (half-integer twist) torque above emits SPURIOUS
                # harmonics (measured on the 1/4: even 10/16 + a 5× order-6 on the
                # tensor mesh, odd 1/3/11 on the geo mesh; the ripple GROWS with
                # steps/period) while the FIELD is provably fine (ψ and mean torque
                # match the full ring).  So keep the K-coupling, and evaluate the
                # per-frame torque the provably-equivalent way instead: the (anti-)
                # periodic BC determines the WHOLE ring from the wedge samples,
                #     A_full[j·Nw + k] = σ^j · A_wedge[k],   σ = _bc_sign
                # (σ^S = 1 always: anti-periodic wedges come in an even count), so
                # unfold both rings, apply the VALIDATED full-ring virtual-work
                # formula on N_full samples, and return the wedge share (÷S — the
                # caller multiplies by NS).  No twist algebra involved.
                _sgnM = -1.0 if float(_bc_sign) < 0 else 1.0
                _spowM = _sgnM ** np.arange(_SfacM)          # σ^j per sector copy
                _GfM = 2.0 * math.pi / (MU0 * _NfullM)
                _kfM = np.arange(_NfullM)
                _kfoldF = np.minimum(_kfM, _NfullM - _kfM)
                _mu_rs_f = np.empty(_NfullM)
                for _j in range(_NfullM):
                    _mu_rs_f[_j] = _GfM * self._Qk_gap(float(_kfoldF[_j]))[1]
                for _j in range(_NfullM):                    # unpaired bins once
                    if _kfM[_j] == 0 or 2 * _kfM[_j] == _NfullM:
                        _mu_rs_f[_j] *= 0.5
                _jfreq_f = _kfM.astype(float)
                _jfreq_f[_jfreq_f > _NfullM / 2] -= _NfullM
                self._spowM = _spowM
                self._mu_rs_f = _mu_rs_f
                self._jfreq_f = _jfreq_f
                self._T_macro = self._T_macro_sector

        _Ka, _ua, _va, _ArA, _rcA = _tri_template(
            [_pol(_r1_m, 0.0), _pol(_r2_m, 0.0), _pol(_r2_m, _dphi_b)])
        _Kb, _ub, _vb, _ArB, _rcB = _tri_template(
            [_pol(_r1_m, 0.0), _pol(_r2_m, _dphi_b), _pol(_r1_m, _dphi_b)])
        if _full_ring:
            _kk_b  = np.arange(Nring)               # closed: N quads
            _kk1_b = (np.arange(Nring) + 1) % Nring
        else:
            _kk_b  = np.arange(Nring - 1)           # open sector: N−1 quads
            _kk1_b = _kk_b + 1
        _ones_b = np.ones(len(_kk_b))
        self._Ka, self._ua, self._va, self._ArA, self._rcA = _Ka, _ua, _va, _ArA, _rcA
        self._Kb, self._ub, self._vb, self._ArB, self._rcB = _Kb, _ub, _vb, _ArB, _rcB
        self._kk_b, self._kk1_b, self._ones_b = _kk_b, _kk1_b, _ones_b
        self.n_quads = len(_kk_b)

    # ── harmonic macroelement ────────────────────────────────────────────────
    def _Qk_gap(self, k: float) -> Tuple[float, float, float]:
        # per-harmonic 2×2 [[Q11(rotor),Q12],[Q12,Q22(stator)]] for the gap
        # energy u^T Q u with u=(A@r1, A@r2); r1=rotor ring, r2=stator ring.
        # Closed form in ρ=r1/r2<1 (the raw r^{±2k} form overflows for the
        # large k reached at N~1008): Q11=Q22=k(1+ρ^2k)/(1−ρ^2k),
        # Q12=−2k·ρ^k/(1−ρ^2k).  ρ^k→0 for high k → self-stiffness ~k, no
        # cross-coupling (the gap low-passes the surface field), as expected.
        _r1M, _r2M = self.r1_m, self.r2_m
        if k == 0:
            c = 1.0 / math.log(_r2M / _r1M)
            return c, -c, c
        rhok = (_r1M / _r2M) ** k
        rho2k = rhok * rhok
        den = 1.0 - rho2k
        q11 = k * (1.0 + rho2k) / den
        return q11, -2.0 * k * rhok / den, q11

    def _circ_of(self, mu: np.ndarray) -> np.ndarray:
        # (skew-)circulant C[i,j]=col[(i−j)%Nw]
        _NwM, _moffM = self._NwM, self._moffM
        col = (self._twd * np.fft.ifft(mu)).real
        return col[self._ii_m] * np.where(
            (np.arange(_NwM)[:, None] - np.arange(_NwM)[None, :]) < 0,
            (-1.0 if _moffM else 1.0), 1.0)

    def _K_gap_macro(self, m) -> "np.ndarray":
        # rotor↔stator block at rotor shift m: phase e^{i·κ·φ_m},
        # φ_m = 2π·m/Nfull, κ = SIGNED physical order per bin (_jfreq).
        # m may be FRACTIONAL — the phase is analytic in the rotor angle
        # (no node pairing), so any steps/period is exact.  The order
        # MUST be signed: the unsigned bin form e^{i·S(j+moff)·φ_m}
        # differs from the true e^{iκφ_m} by e^{i·2πm} on every
        # negative-frequency bin — invisible for whole-node m, but a
        # fractional shift then cycles a huge spurious harmonic with
        # the period of frac(m) (measured: T_avg 30→10, order-24 ×50 at
        # 2/3 node per step).
        _NwM, _moffM, _NfullM = self._NwM, self._moffM, self._NfullM
        ph = np.exp(1j * self._jfreq * (2.0*np.pi*float(m)/_NfullM))
        colm = (self._twd * np.fft.ifft(self._mu_rs * ph))
        _krs = colm.real[self._ii_m] * np.where(
            (np.arange(_NwM)[:, None] - np.arange(_NwM)[None, :]) < 0,
            (-1.0 if _moffM else 1.0), 1.0)
        # forward block  K[gR1[a],gR2[b]] = _krs[a,b]  and its symmetric
        # transpose K[gR2[b],gR1[a]] = _krs[a,b] (NOT _krs[b,a] — _krs is
        # asymmetric for m≠0, so a literal .T here made the global matrix
        # non-symmetric → garbage solve at non-integer-pole shifts).
        rows = np.concatenate([self._Rg1.ravel(), self._Rg2.ravel(),
                               self._Rg12.ravel(), self._Cg12.ravel()])
        cols = np.concatenate([self._Cg1.ravel(), self._Cg2.ravel(),
                               self._Cg12.ravel(), self._Rg12.ravel()])
        data = np.concatenate([self._Krr_blk.ravel(), self._Kss_blk.ravel(),
                               _krs.ravel(), _krs.ravel()])
        return _coo((data, (rows, cols)), shape=(self.n, self.n)).tocsr()

    def _T_macro_wedge(self, m, Avec: np.ndarray) -> float:
        # virtual work: T = −∂(L·w_gap)/∂φ ; only the rotor↔stator term depends
        # on φ.  w_rs(per-unit) = (1/Nw) Σ μ_rs(j) e^{i·k·φ} conj(Ûr) Ûs with
        # Û = FFT of the moff-twisted ring samples; ∂/∂φ brings i·k_signed
        # (PHYSICAL order).  Returns the WEDGE torque (1/S of the machine),
        # matching _T_band's wedge convention — the caller's sector scaling
        # applies unchanged.  Real torque scales by the stack length _stkM.
        _twn, _NwM, _NfullM, _stkM = self._twn, self._NwM, self._NfullM, self._stkM
        Ur = np.fft.fft(Avec[self._gR1M] * _twn); Us = np.fft.fft(Avec[self._gR2M] * _twn)
        # SIGNED order in the phase (see _K_gap_macro) — exact for
        # fractional m; identical to the old unsigned form for whole m.
        ph = np.exp(1j * self._jfreq * (2.0*np.pi*float(m)/_NfullM))
        return float(-(_stkM/_NwM) * np.sum(
            (1j*self._jfreq) * self._mu_rs * ph * np.conj(Ur) * Us).real)

    def _T_macro_sector(self, m, Avec: np.ndarray) -> float:
        _NfullM, _SfacM, _stkM = self._NfullM, self._SfacM, self._stkM
        _jfreq_f, _mu_rs_f = self._jfreq_f, self._mu_rs_f
        Urf = np.fft.fft(np.concatenate(
            [_s * Avec[self._gR1M] for _s in self._spowM]))
        Usf = np.fft.fft(np.concatenate(
            [_s * Avec[self._gR2M] for _s in self._spowM]))
        # SIGNED order (see _K_gap_macro) — exact for fractional m
        ph = np.exp(1j * _jfreq_f * (2.0*np.pi*float(m)/_NfullM))
        return float(-(_stkM / _NfullM) * np.sum(
            (1j * _jfreq_f) * _mu_rs_f * ph
            * np.conj(Urf) * Usf).real) / _SfacM

    # ── closed-form triangle strip ───────────────────────────────────────────
    def _band_idx(self, m) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        Nring, _kk_b, _ones_b = self.Nring, self._kk_b, self._ones_b
        _bc_sign = self.bc_sign
        if self.full_ring:
            j  = (_kk_b + int(m)) % Nring        # periodic, no sign
            j1 = (_kk_b + int(m) + 1) % Nring
            return j.astype(int), j1.astype(int), _ones_b, _ones_b
        j = _kk_b + int(m); j1 = j + 1
        sj = np.ones(Nring - 1); sj1 = np.ones(Nring - 1)
        while np.any(j > Nring - 1):
            w = j > Nring - 1
            j = np.where(w, j - (Nring - 1), j)
            sj = np.where(w, sj * _bc_sign, sj)
        while np.any(j1 > Nring - 1):
            w = j1 > Nring - 1
            j1 = np.where(w, j1 - (Nring - 1), j1)
            sj1 = np.where(w, sj1 * _bc_sign, sj1)
        return j.astype(int), j1.astype(int), sj, sj1

    def K_band(self, m) -> "np.ndarray":
        j, j1, sj, sj1 = self._band_idx(m)
        _gR1, _gR2 = self._gR1, self._gR2
        _kk_b, _kk1_b, _ones_b = self._kk_b, self._kk1_b, self._ones_b
        rows = []; cols = []; data = []
        for Kl, dofs, sgs in (
            (self._Ka, (_gR1[_kk_b], _gR2[j],  _gR2[j1]),
                   (_ones_b, sj, sj1)),
            (self._Kb, (_gR1[_kk_b], _gR2[j1], _gR1[_kk1_b]),
                   (_ones_b, sj1, _ones_b)),
        ):
            for pq in range(9):
                pp, qq = divmod(pq, 3)
                rows.append(dofs[pp]); cols.append(dofs[qq])
                data.append(Kl[pp, qq] * sgs[pp] * sgs[qq])
        return _coo((np.concatenate(data),
                     (np.concatenate(rows), np.concatenate(cols))),
                    shape=(self.n, self.n)).tocsr()

    def T_band(self, m, Avec: np.ndarray) -> float:
        j, j1, sj, sj1 = self._band_idx(m)
        _gR1, _gR2 = self._gR1, self._gR2
        _kk_b, _kk1_b = self._kk_b, self._kk1_b
        Aa = np.vstack([Avec[_gR1[_kk_b]],
                        Avec[_gR2[j]] * sj, Avec[_gR2[j1]] * sj1])
        Ab = np.vstack([Avec[_gR1[_kk_b]],
                        Avec[_gR2[j1]] * sj1, Avec[_gR1[_kk1_b]]])
        s = (self._ArA * self._rcA * (self._ua @ Aa) * (self._va @ Aa)
             + self._ArB * self._rcB * (self._ub @ Ab) * (self._vb @ Ab))
        # Arkkio over the STRIP alone — normalise by the strip's radial
        # width (r2−r1).  The strip is the consistently-COUPLED region
        # (rotor ring ↔ stator ring), so its stress is artifact-free; the
        # half-mesh gap fields are sheared (rotor at θ=0 vs coupled stator)
        # and carry a spurious DC torque, so they are NOT used.
        return float(np.sum(s)) * self.stack_length / (MU0 * (self.r2_m - self.r1_m))

    # ── the interface the frame loop uses ────────────────────────────────────
    def K(self, m) -> "np.ndarray":
        """Gap stiffness at rotor shift ``m`` (macro or strip)."""
        return self._K_gap_macro(m) if self.use_macro else self.K_band(m)

    def torque(self, m, Avec: np.ndarray) -> float:
        """Per-sector gap torque of field ``Avec`` at rotor shift ``m``."""
        return self._T_macro(m, Avec) if self.use_macro else self.T_band(m, Avec)
