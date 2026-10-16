# -*- coding: utf-8 -*-
"""Incremental (frozen-permeability) d-q inductances.

WHY THIS FILE EXISTS.  A client reviewing the L180 generator report asked why
this machine has Ld > Lq — *"这个电机 Ld > Lq? 好像和一般的电机不太一样"*.  It
does not: the report was printing the CHORD (ψd − ψ_PM)/i_d taken at γ = −15°,
where the numerator is mostly the magnet flux the loaded iron moved and only a
little of it is flux-per-amp.  The answer is the standard frozen-permeability
incremental measurement, and these tests pin its two halves:

  * the ALGEBRA, against a synthetic machine whose d-q inductance matrix is
    known exactly — Park, the unit perturbation, the parallel-path scaling and
    the chord identity are all checkable without solving a field;
  * the SOLVER, on the pinned 30 mm case — the block is produced, its own
    self-checks (reciprocity, superposition) pass, and the summary carries the
    incremental values with the chord kept under its own name.
"""
from __future__ import annotations

import math
from typing import Any, Dict

import numpy as np
import pytest
import scipy.sparse as sp

from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.simulation.drive import inverse_park, park
from motor_ai_sim.simulation.fem_solver_2d import (
    LDQ_METHOD_DIFFERENTIAL, LDQ_METHOD_REGULARIZED, LDQ_METHOD_SECANT,
    NOLOAD_LDQ_TEMP_C,
    _ipark_dq, _park_dq, fem_transient_sliding_band, frozen_permeability_ldq)

from tests.test_physics_regression import (CONNECTION, GEO_30MM, OVERRIDE, RPM)


# ═══════════════════════════════════════════════════════════════════════════
#  A synthetic machine with a KNOWN d-q inductance matrix
# ═══════════════════════════════════════════════════════════════════════════

def _T(th: float) -> np.ndarray:
    """abc → dq0, the amplitude-invariant transform ``drive.park`` implements."""
    c = [math.cos(th - k) for k in (0.0, 2 * math.pi / 3, -2 * math.pi / 3)]
    s = [math.sin(th - k) for k in (0.0, 2 * math.pi / 3, -2 * math.pi / 3)]
    return (2.0 / 3.0) * np.array([c, [-x for x in s], [0.5, 0.5, 0.5]])


def _Tinv(th: float) -> np.ndarray:
    """dq0 → abc, the inverse ``drive.inverse_park`` implements."""
    c = [math.cos(th - k) for k in (0.0, 2 * math.pi / 3, -2 * math.pi / 3)]
    s = [math.sin(th - k) for k in (0.0, 2 * math.pi / 3, -2 * math.pi / 3)]
    return np.array([[c[i], -s[i], 1.0] for i in range(3)])


class _FakeP2:
    """The three primitives ``frozen_permeability_ldq`` uses, over a 3-dof
    "mesh" whose one nodal quantity IS the phase flux linkage.

    ``solve_ff`` really solves the reduced matrix the function builds, so the
    projection and the free-dof slice are exercised, not stubbed out."""

    def __init__(self, K: np.ndarray) -> None:
        self._K = sp.csr_matrix(K)

    def Kpw(self, A):                       # noqa: N802 — the solver's name
        return self._K, None

    def solve_ff(self, Kff, rhs, spd=False):
        return np.linalg.solve(np.asarray(Kff.todense()), np.asarray(rhs))

    def pad2(self, Pro, free, xf):
        return np.asarray(xf).ravel()


def _machine(th: float, Ld: float, Lq: float, Ldq: float = 0.0,
             psi_pm_star: float = 0.0, n_parallel: int = 1):
    """A linear machine with exactly this d-q matrix at rotor angle ``th``.

    Returns everything ``frozen_permeability_ldq`` takes.  ``n_parallel`` is
    folded into the operator the way the real solver folds it: the coil sources
    are driven with the BRANCH current while ``psi_of`` returns the PHASE flux
    linkage, so an operator scaled by n_parallel is what makes the returned
    matrix the phase inductance.
    """
    L_dq0 = np.array([[Ld, Ldq, 0.0], [Ldq, Lq, 0.0], [0.0, 0.0, 0.31 * Ld]])
    L_abc = float(n_parallel) * (_Tinv(th) @ L_dq0 @ _T(th))
    K = np.linalg.inv(L_abc)
    psi_pm_abc = _Tinv(th) @ np.array([psi_pm_star, 0.0, 0.0])
    return dict(
        p2=_FakeP2(K),
        Pro=sp.identity(3, format="csr"),
        free=np.array([0, 1, 2]),
        A2=np.zeros(3),
        f_mag=K @ psi_pm_abc,
        Pa=np.array([1.0, 0.0, -1.0]),      # unit i_A with i_C folded in
        Pb=np.array([0.0, 1.0, -1.0]),      # unit i_B, likewise
        psi_of=(lambda x: (float(x[0]), float(x[1]), float(x[2]))),
        th_dq=th,
        n_parallel=n_parallel,
    )


TH = 0.7            # an arbitrary rotor position; nothing may depend on it


def test_the_matrix_is_recovered_exactly():
    """Unit d- and q-axis solves on a known machine return its own Ld/Lq/Ldq."""
    out = frozen_permeability_ldq(**_machine(TH, 4.0e-4, 6.5e-4, 0.9e-4))
    assert out["Ld_H"] == pytest.approx(4.0e-4, rel=1e-10)
    assert out["Lq_H"] == pytest.approx(6.5e-4, rel=1e-10)
    assert out["Ldq_H"] == pytest.approx(0.9e-4, rel=1e-10)
    # no saturable iron: the tangent operator IS the secant one
    for k in ("Ld", "Lq", "Ldq"):
        assert out[k + "_H"] == out[k + "_fp_H"], k
    assert out["tangent_clamped"] is False


def test_it_does_not_depend_on_the_rotor_position_convention():
    """The same machine, read at four rotor angles, is the same machine."""
    for th in (0.0, 1.1, -2.4, 5.9):
        out = frozen_permeability_ldq(**_machine(th, 4.0e-4, 6.5e-4, 0.9e-4))
        assert out["Ld_H"] == pytest.approx(4.0e-4, rel=1e-9), th
        assert out["Lq_H"] == pytest.approx(6.5e-4, rel=1e-9), th


def test_the_matrix_is_symmetric():
    """Reciprocity of a LINEAR magnetic circuit — checked, never assumed."""
    out = frozen_permeability_ldq(**_machine(TH, 4.0e-4, 6.5e-4, 1.7e-4))
    assert out["reciprocity_pct"] == pytest.approx(0.0, abs=1e-6)


def test_the_magnet_flux_of_the_frozen_iron_is_measured():
    """ψ*_PM comes back on d, and nothing of it lands on q."""
    out = frozen_permeability_ldq(
        **_machine(TH, 4.0e-4, 6.5e-4, 0.9e-4, psi_pm_star=0.081))
    assert out["psi_d_pm_frozen_Wb"] == pytest.approx(0.081, rel=1e-10)
    assert out["psi_q_pm_frozen_Wb"] == pytest.approx(0.0, abs=1e-12)


def test_parallel_paths_do_not_multiply_the_inductance():
    """The returned matrix is the PHASE inductance, for any n_parallel."""
    for npar in (1, 2, 4):
        out = frozen_permeability_ldq(
            **_machine(TH, 4.0e-4, 6.5e-4, 0.9e-4, n_parallel=npar))
        assert out["Ld_H"] == pytest.approx(4.0e-4, rel=1e-9), npar
        assert out["Lq_H"] == pytest.approx(6.5e-4, rel=1e-9), npar


# ── the chord, against the incremental value ────────────────────────────────

def _chord(Ld, Lq, Ldq, psi_pm_star, psi_pm_noload, i_d, i_q):
    """What the OLD extraction reads off the same synthetic machine."""
    psi_d = psi_pm_star + Ld * i_d + Ldq * i_q
    psi_q = Ldq * i_d + Lq * i_q
    return ((psi_d - psi_pm_noload) / i_d, psi_q / i_q)


def test_on_linear_iron_the_incremental_values_ARE_the_chord():
    """The validation case: no ψ_PM shift, no cross term ⇒ the two agree.

    This is what makes the new number a replacement rather than a different
    quantity — it only departs from the chord where the chord stopped being an
    inductance."""
    Ld, Lq = 4.0e-4, 6.5e-4
    out = frozen_permeability_ldq(
        **_machine(TH, Ld, Lq, 0.0, psi_pm_star=0.081))
    cd, cq = _chord(Ld, Lq, 0.0, 0.081, 0.081, i_d=-120.0, i_q=480.0)
    assert cd == pytest.approx(out["Ld_H"], rel=1e-9)
    assert cq == pytest.approx(out["Lq_H"], rel=1e-9)


def test_under_saturation_the_chord_is_the_sag_and_the_cross_term():
    """The L180 case, in arithmetic.

    The L180 generator's own order of magnitude: ψ*_PM (the magnets in the
    LOADED iron) sits 8.7 % under the no-load ψ_PM the chord divides by, and at
    γ = −15° i_d carries a quarter of the current.  The chord Ld then reads
    ~2.5× the real one — and the excess is EXACTLY the two terms of the
    identity, which is what makes it a diagnosis and not a suspicion.  The
    chord Lq is over-read by the cross term alone."""
    Ld, Lq, Ldq = 4.0e-5, 5.0e-5, -2.0e-6
    psi_nl, sag = 0.084, 0.087
    psi_star = psi_nl * (1.0 - sag)
    i_d, i_q = -127.0, 473.0              # 490 A peak at gamma = -15 deg
    out = frozen_permeability_ldq(
        **_machine(TH, Ld, Lq, Ldq, psi_pm_star=psi_star))
    cd, cq = _chord(Ld, Lq, Ldq, psi_star, psi_nl, i_d, i_q)

    # (1) the incremental values are the machine's own, whatever the chord says
    assert out["Ld_H"] == pytest.approx(Ld, rel=1e-9)
    assert out["Lq_H"] == pytest.approx(Lq, rel=1e-9)
    # (2) the chord is not, on either axis, and on d it is the larger of the two
    assert cd > 2.0 * Ld
    assert cd > cq                        # "Ld > Lq" — the client's question
    # (3) …and the whole difference is the identity
    assert cd == pytest.approx(Ld + ((psi_star - psi_nl) + Ldq * i_q) / i_d,
                               rel=1e-9)
    # (4) the q axis: the chord is over-read by the cross term alone
    assert out["Lq_H"] < cq
    assert cq == pytest.approx(Lq + Ldq * i_d / i_q, rel=1e-9)
    # (5) the physics the client expected, restored: Lq above Ld
    assert out["Lq_H"] / out["Ld_H"] > 1.0


# ═══════════════════════════════════════════════════════════════════════════
#  DIFFERENTIAL vs SECANT on a real P2 field (docs/BUG_LDLQ_REPORTED_2026-10-05)
# ═══════════════════════════════════════════════════════════════════════════
#  A unit square, P2, A = 0 on the boundary: saturable iron everywhere but
#  three phase coils (non-magnetic) across the middle, the iron carrying a DC
#  "magnet" source that biases it up its B-H curve — the coils' flux closes
#  through that iron, as a stator's does.  ~2 k dofs, solved with SuperLU.

def _square(curve, j_bias):
    from skfem import (Basis, ElementTriP0, ElementTriP2, LinearForm, MeshTri,
                       asm)
    from motor_ai_sim.simulation.field_ops import MU0
    from motor_ai_sim.simulation.p2_nonlinear import P2Nonlinear, stiff_nu2

    class _Log:
        def warning(self, *a, **k): pass
        def info(self, *a, **k): pass
        def debug(self, *a, **k): pass

    m = MeshTri().refined(4)
    b = Basis(m, ElementTriP2())
    b0 = b.with_element(ElementTriP0())
    cx, cy = m.p[:, m.t].mean(axis=1)
    band = (cy > 0.4) & (cy < 0.6)
    coils = [band & (cx > a) & (cx < a + 0.25) for a in (0.06, 0.375, 0.69)]
    iron = np.flatnonzero(~(coils[0] | coils[1] | coils[2]))
    nu_c = np.full(m.t.shape[1], 1.0 / MU0)
    nu_c[iron] = 0.0
    K_const = asm(stiff_nu2, b, nu=b0.interpolate(nu_c)).tocsr()
    sb = Basis(m, ElementTriP2(), elements=iron)
    p2 = P2Nonlinear(basis=b, n_dof=b.N, K_const=K_const,
                     sat=[(iron, curve)],
                     sat_sub=[(sb, sb.with_element(ElementTriP0()), iron, curve)],
                     pardiso=None, log=_Log())

    @LinearForm
    def _src(v, w):
        return w["j"] * v

    def load(sel):
        w = np.zeros(m.t.shape[1])
        w[sel] = 1.0
        return np.asarray(asm(_src, b, j=b0.interpolate(w))).ravel()

    fA, fB, fC = (load(np.flatnonzero(c)) for c in coils)
    return dict(p2=p2, Pro=sp.identity(b.N, format="csr"),
                free=b.complement_dofs(b.get_dofs()),
                f_mag=j_bias * load(iron), Pa=fA - fC, Pb=fB - fC,
                psi_of=lambda A: (float(A @ fA), float(A @ fB), float(A @ fC)))


def _solve(pr, i_d, i_q, th):
    """The converged nonlinear field at (i_d, i_q): Newton with a
    backtracking line search, to |R|/|f| < 1e-10 (the linear case's
    round-off floor is ~4e-12)."""
    p2, free = pr["p2"], pr["free"]
    iA, iB, _ = _ipark_dq(i_d, i_q, th)
    f = pr["f_mag"] + iA * pr["Pa"] + iB * pr["Pb"]
    A = np.zeros(f.size)
    fn = float(np.linalg.norm(f[free]))
    for _ in range(80):
        K, info = p2.Kpw(A)
        r = (K @ A - f)[free]
        rn = float(np.linalg.norm(r))
        if rn < 1e-10 * fn:
            return A
        J = (K + p2.tangent2(info)).tocsr()[free][:, free].tocsc()
        dA = np.zeros_like(A)
        dA[free] = p2.solve_ff(J, -r)
        lam = 1.0
        for _ls in range(30):
            At = A + lam * dA
            Kt, _ = p2.Kpw(At)
            if np.linalg.norm((Kt @ At - f)[free]) < rn:
                break
            lam *= 0.5
        A = At
    raise AssertionError("test Newton did not converge (|R|/|f| = %.2e)"
                         % (rn / fn))


def _probe(pr, A, th):
    return frozen_permeability_ldq(pr["p2"], pr["Pro"], pr["free"], A,
                                   pr["f_mag"], pr["Pa"], pr["Pb"],
                                   pr["psi_of"], th)


def test_on_linear_iron_the_differential_IS_the_secant():
    """Constant μ_r: dν/dB² = 0, the tangent operator is the secant one, and
    the new Ld/Lq equal the old (frozen-permeability) ones to round-off."""
    from motor_ai_sim.simulation.field_ops import MU0
    pr = _square([(0.0, 0.0), (1.0e7, 800.0 * MU0 * 1.0e7)], 2.0e6)
    out = _probe(pr, _solve(pr, 0.0, 0.0, TH), TH)
    for k in ("Ld", "Lq", "Ldq"):
        assert out[k + "_H"] == pytest.approx(
            out[k + "_fp_H"], rel=1e-9, abs=1e-12 * abs(out["Ld_fp_H"])), k
    assert out["tangent_clamped"] is False


def test_saturated_differential_matches_a_finite_difference():
    """The bug's case: the iron sits up the knee under a DC bias.  The tangent
    result must equal the symmetric ±Δi difference Δψ/Δi of fully converged
    nonlinear solves, while the frozen-permeability secant over-reads it."""
    from motor_ai_sim.simulation.field_ops import MU0
    H = np.linspace(0.0, 2.0e5, 2001)
    smooth = [(h, MU0 * h + 1.6 * np.tanh(MU0 * 800.0 * h / 1.6)) for h in H]
    pr = _square(smooth, 2.0e6)
    A0 = _solve(pr, 0.0, 0.0, TH)
    Bm = pr["p2"].elemB(A0)[pr["p2"].sat[0][0]]
    assert np.median(Bm) > 1.5, np.median(Bm)          # really saturated
    out = _probe(pr, A0, TH)
    assert out["tangent_clamped"] is False
    di = 1.0e3                                          # 5e-4 of the bias
    L = {}
    for ax, (dd, dq) in (("d", (di, 0.0)), ("q", (0.0, di))):
        pp = _park_dq(*pr["psi_of"](_solve(pr, dd, dq, TH)), TH)
        pm = _park_dq(*pr["psi_of"](_solve(pr, -dd, -dq, TH)), TH)
        L[ax] = ((pp[0] - pm[0]) / (2 * di), (pp[1] - pm[1]) / (2 * di))
    assert out["Ld_H"] == pytest.approx(L["d"][0], rel=0.01)
    assert out["Lq_H"] == pytest.approx(L["q"][1], rel=0.01)
    assert out["Ldq_H"] == pytest.approx(0.5 * (L["d"][1] + L["q"][0]),
                                         abs=0.01 * abs(out["Ld_H"]))
    # …and the secant is NOT the small-signal value here
    # (2.2x / 2.5x on this square, the bug report's 1.7-2.4x)
    assert out["Ld_fp_H"] > 1.5 * out["Ld_H"]
    assert out["Lq_fp_H"] > 1.5 * out["Lq_H"]


# ═══════════════════════════════════════════════════════════════════════════
#  …and on the solver, on the pinned 30 mm machine
# ═══════════════════════════════════════════════════════════════════════════
#: Coarse in time and space on purpose — a shape and self-check test, not a
#: physics pin (the physics pin is tests/test_physics_regression.py, which this
#: change may not move).
RUN = dict(n_steps_per_period=4, n_periods=1.0, mesh_size_mm=1.4,
           min_size_mm=0.35, gap_layers=1.0, n_sectors=2, structured_gap=True,
           iron_template=True, geo_mesh=True, coil_temp_c=120.0,
           element_order=2, demag=False, eddy=False, rotor_eddy=False,
           I_phase_rms=60.0, gamma_deg=10.0, rpm=RPM, connection=CONNECTION)


@pytest.fixture(scope="module")
def solved() -> Dict[str, Any]:
    set_request_materials(OVERRIDE)
    try:
        return fem_transient_sliding_band(geo_override=dict(GEO_30MM),
                                          inc_ldq=True, **RUN)
    finally:
        set_request_materials(None)


@pytest.fixture(scope="module")
def summary(solved) -> Dict[str, Any]:
    from motor_ai_sim.routes.simulation import _build_transient_summary
    set_request_materials(OVERRIDE)
    try:
        return _build_transient_summary(
            solved, I_phase_rms=float(RUN["I_phase_rms"]),
            gamma_deg=float(RUN["gamma_deg"]),
            coil_temp_c=float(RUN["coil_temp_c"]),
            geo_override=dict(GEO_30MM))
    finally:
        set_request_materials(None)


def test_the_solver_ships_the_block(solved):
    b = solved.get("inc_ldq")
    assert isinstance(b, dict) and b, solved.get("inc_ldq")
    for k in ("Ld_mH", "Lq_mH", "Ldq_mH", "psi_d_pm_frozen_Wb", "samples",
              "spread_pct", "reciprocity_pct", "superposition_pct", "method",
              "Ld_fp_secant_mH", "Lq_fp_secant_mH", "ldq_method"):
        assert k in b, (k, sorted(b))
    assert b["samples"] == min(4, int(RUN["n_steps_per_period"]))
    assert b["Ld_mH"] > 0.0 and b["Lq_mH"] > 0.0, b


def test_the_frozen_operator_reproduces_the_frame(solved):
    """ψd = ψ*_PM + Ld·i_d + Ldq·i_q must hold EXACTLY on a magnetostatic
    frame — it is the same linear system, read twice.  Anything but ~0 here
    means the operator that was frozen is not the one that solved the frame."""
    b = solved["inc_ldq"]
    assert b["superposition_pct"] < 0.5, b
    assert b["reciprocity_pct"] < 1.0, b


def test_no_run_is_charged_for_it_unless_it_asked():
    """The probe is opt-in: the default run carries no block at all."""
    set_request_materials(OVERRIDE)
    try:
        r = fem_transient_sliding_band(geo_override=dict(GEO_30MM),
                                       **dict(RUN, n_steps_per_period=2))
    finally:
        set_request_materials(None)
    assert "inc_ldq" not in r


def test_the_summary_reports_the_incremental_values(summary):
    s = summary
    assert s["ldq_method"] == LDQ_METHOD_DIFFERENTIAL
    assert s["Ld_mH"] is not None and s["Lq_mH"] is not None
    assert s["Ld_mH"] == s["Ld_inc_mH"]
    assert s["Lq_mH"] == s["Lq_inc_mH"]
    assert s["Ldq_inc_mH"] is not None
    # the secant values ride beside them, under their own name
    assert s["Ld_fp_secant_mH"] is not None and s["Lq_fp_secant_mH"] is not None
    # the saliency is the ratio of the two values shown, and nothing else.
    # (This fixture is the 30 mm SURFACE-magnet machine, whose axes are nearly
    # equal — the ratio is checked for consistency, not for a value.)
    # (rel, not abs: the two inductances are stored to four decimals, which on
    # a sub-0.1 mH machine is a coarser grid than the ratio's own three)
    assert s["saliency_Lq_over_Ld"] == pytest.approx(
        s["Lq_mH"] / s["Ld_mH"], rel=0.02)


def test_the_chord_is_kept_but_not_called_an_inductance(summary):
    s = summary
    assert "Ld_chord_mH" in s and "Lq_chord_mH" in s
    # the chord is a DIFFERENT number — that is the whole finding
    if s["Ld_chord_mH"] is not None:
        assert s["Ld_chord_mH"] != s["Ld_mH"]
    assert s["psi_pm_sag_pct"] is not None
    assert s["psi_pm_frozen_Wb"] is not None


def test_a_run_without_the_block_says_so_instead_of_guessing(summary, solved):
    """An old stored run rebuilt through the summary builder must not silently
    fall back to the chord."""
    from motor_ai_sim.routes.simulation import _build_transient_summary
    old = {k: v for k, v in solved.items() if k != "inc_ldq"}
    set_request_materials(OVERRIDE)
    try:
        s = _build_transient_summary(
            old, I_phase_rms=float(RUN["I_phase_rms"]),
            gamma_deg=float(RUN["gamma_deg"]),
            coil_temp_c=float(RUN["coil_temp_c"]),
            geo_override=dict(GEO_30MM))
    finally:
        set_request_materials(None)
    assert s["Ld_mH"] is None and s["Lq_mH"] is None
    assert s["ldq_method"] is None
    assert "re-run" in (s["dq_note"] or ""), s["dq_note"]
    # …and the chord is still there, under its own name
    assert s["Lq_chord_mH"] is not None


def test_a_secant_only_block_is_not_reported_as_small_signal(solved):
    """A run solved before 2026-10-11 carries the secant value as Ld_mH: the
    rebuilt summary labels it so and leaves Ld_inc_mH empty, which is what the
    report and the controller read as "small-signal Ld/Lq"."""
    from motor_ai_sim.routes.simulation import _build_transient_summary
    new = solved["inc_ldq"]
    blk = {k: v for k, v in new.items()
           if "fp_secant" not in k and k != "ldq_method"}
    blk["Ld_mH"], blk["Lq_mH"] = new["Ld_fp_secant_mH"], new["Lq_fp_secant_mH"]
    set_request_materials(OVERRIDE)
    try:
        s = _build_transient_summary(
            dict(solved, inc_ldq=blk), I_phase_rms=float(RUN["I_phase_rms"]),
            gamma_deg=float(RUN["gamma_deg"]),
            coil_temp_c=float(RUN["coil_temp_c"]),
            geo_override=dict(GEO_30MM))
    finally:
        set_request_materials(None)
    assert s["ldq_method"] == LDQ_METHOD_SECANT
    assert s["Ld_inc_mH"] is None and s["Lq_inc_mH"] is None
    assert s["Ld_fp_secant_mH"] == pytest.approx(blk["Ld_mH"], abs=1e-4)


# ═══════════════════════════════════════════════════════════════════════════
#  The catalogue block: Ld0 / Lq0 at 20 C and no load, beside KV
# ═══════════════════════════════════════════════════════════════════════════

def test_the_cold_pass_carries_the_catalogue_inductances(monkeypatch):
    from motor_ai_sim.routes import coupled as C

    probe = {
        "Ld_mH": 0.0405, "Lq_mH": 0.0495, "Ldq_mH": 0.0002,
        "spread_pct": {"Ld": 0.4, "Lq": 0.6}, "reciprocity_pct": 0.01,
        "ldq_method": LDQ_METHOD_DIFFERENTIAL,
    }
    monkeypatch.setattr("motor_ai_sim.routes.simulation.catalogue_ldq0",
                        lambda *a, **k: dict(probe))
    out = C._cold_ldq0({"summary": {"daxis_deg": 12.5, "connection": "2P"}}, {})
    assert out["Ld0_mH"] == 0.0405
    assert out["Lq0_mH"] == 0.0495
    assert out["saliency0_Lq_over_Ld"] == pytest.approx(1.222, abs=5e-4)
    assert "20" in out["ldq0_method"] and "i=0" in out["ldq0_method"]


def test_the_cold_pass_reads_the_result_level_daxis_stamp(monkeypatch):
    """The transient route stamps ``daxis_deg`` on the RESULT, beside
    ``summary`` — never inside it.  A cold pass that looked only in the summary
    never probed on a real run (2026-09-20, L180 rated through
    ``POST /api/coupled/constants_20c``: KV/Kt/Km present, Ld0/Lq0 absent)."""
    from motor_ai_sim.routes import coupled as C

    seen = {}

    def _probe(ov, *, daxis_deg, connection=None, magnet_temp_c=20.0):
        seen["daxis_deg"] = daxis_deg
        return {"Ld_mH": 0.0805, "Lq_mH": 0.0819, "Ldq_mH": -0.0027,
                "spread_pct": {"Ld": 0.2, "Lq": 1.5}, "reciprocity_pct": 0.0,
                "ldq_method": LDQ_METHOD_DIFFERENTIAL}

    monkeypatch.setattr("motor_ai_sim.routes.simulation.catalogue_ldq0", _probe)
    out = C._cold_ldq0({"daxis_deg": 120.0138, "daxis_source": "calibrated",
                        "summary": {"connection": "2P"}}, {})
    assert seen["daxis_deg"] == pytest.approx(120.0138)
    assert out["Ld0_mH"] == 0.0805 and out["Lq0_mH"] == 0.0819
    assert out["saliency0_Lq_over_Ld"] == pytest.approx(1.017, abs=5e-4)


def test_no_probe_means_no_catalogue_row(monkeypatch):
    """A machine whose no-load probe failed gets NOTHING — never a loaded
    chord dressed up as a catalogue constant."""
    from motor_ai_sim.routes import coupled as C
    monkeypatch.setattr("motor_ai_sim.routes.simulation.catalogue_ldq0",
                        lambda *a, **k: None)
    assert C._cold_ldq0({"summary": {"daxis_deg": 12.5}}, {}) is None
    # …and a run with no d-axis stamp does not even probe
    assert C._cold_ldq0({"summary": {}}, {}) is None


def test_the_catalogue_table_quotes_the_no_load_inductances():
    """§4's 20 °C sub-table prints Ld/Lq on the SAME basis as KV — no load,
    20 °C — and never the duty's loaded pair."""
    from motor_ai_sim import report as R

    rows = {r[0]: r for r in R.cold_constant_rows({"constants_20c": {
        "point": {"rpm": 20900, "I_phase_rms": 600.4, "gamma_deg": -15.0},
        "two_d": {"Kt_Nm_per_Arms": 0.4}, "k3d": {},
        "Ld0_mH": 0.0792, "Lq0_mH": 0.0830,
        "saliency0_Lq_over_Ld": 1.048,
    }})}
    assert "Ld at no load [mH]" in rows and "Lq at no load [mH]" in rows
    assert rows["Ld at no load [mH]"][1].startswith("0.0792")
    assert "zero current and 20 °C" in rows["Ld at no load [mH]"][2]
    assert "Saliency Lq/Ld at no load" in rows
    # a record from before the probe simply has no such rows
    bare = {r[0] for r in R.cold_constant_rows({"constants_20c": {
        "two_d": {}, "k3d": {}}})}
    assert not any(r.startswith("Ld at no load") for r in bare)


def test_section_four_prints_the_cross_term():
    from motor_ai_sim import report as R

    rows = {r[0]: r for r in R.em_constant_rows(
        {"Ld_mH": 0.0798, "Lq_mH": 0.0835, "Ldq_inc_mH": -0.0027,
         "Ld_inc_mH": 0.0798, "Lq_inc_mH": 0.0835, "psi_pm_Wb": 0.0706, "psi_pm_frozen_Wb": 0.0663,
         "psi_pm_sag_pct": 6.14, "gamma_deg": -15.0,
         "ldq_method": "frozen-permeability incremental at the point"})}
    assert "Cross-saturation Ldq" in rows
    assert rows["Cross-saturation Ldq"][1].startswith("-0.0027")
    # …and the ψ_PM row says in one clause what the load did to the magnets
    pm = rows["Magnet flux linkage Psi_PM"][2]
    assert "loaded iron" in pm and "-6.1" in pm


def test_section_four_names_the_differential_method():
    """A record solved since 2026-10-11 says in its note that Ld/Lq are the
    small-signal values; an older one keeps its frozen-permeability label."""
    from motor_ai_sim import report as R

    rec = {"Ld_mH": 0.0405, "Lq_mH": 0.0495,
           "Ld_inc_mH": 0.0405, "Lq_inc_mH": 0.0495,
           "ldq_method": LDQ_METHOD_DIFFERENTIAL}
    notes = {r[0]: r[2] for r in R.em_constant_rows(rec)}
    assert "differential (small-signal)" in notes["Ld"], notes["Ld"]
    assert "frozen permeability" not in notes["Ld"]
    old = {r[0]: r[2] for r in R.em_constant_rows(
        dict(rec, ldq_method="frozen-permeability incremental at the point"))}
    assert "frozen permeability" in old["Ld"], old["Ld"]


def test_probe_frames_never_sit_in_a_settling_or_demag_prefix():
    """Review finding 2: the probe frames are spread over the REPORTED window
    [_vskip + _dmskip, n_total), never over the prefixes the series trimming
    drops.  240 settling + 24 reported frames used to probe 0/66/132/198."""
    from motor_ai_sim.simulation.fem_solver_2d import _inc_probe_frames
    # fixed voltage settling: 240-frame prefix, 24 reported
    assert _inc_probe_frames(240, 264) == {240, 246, 252, 258}
    # demag ratchet prefix of one period (24 steps) before one reported period
    assert min(_inc_probe_frames(24, 48)) >= 24
    # no prefix: the old selection, unchanged
    assert _inc_probe_frames(0, 24) == {0, 6, 12, 18}
    # a window shorter than the sample count probes every reported frame
    assert _inc_probe_frames(10, 12) == {10, 11}


def test_a_demag_run_probes_only_its_reported_window():
    """The demag schedule prepends one ratchet period; the probes must land
    after it (frame index >= n_steps_per_period)."""
    nspp = 4
    set_request_materials(OVERRIDE)
    try:
        r = fem_transient_sliding_band(geo_override=dict(GEO_30MM), inc_ldq=True,
                                       **dict(RUN, demag=True,
                                              n_steps_per_period=nspp))
    finally:
        set_request_materials(None)
    pos = r["inc_ldq"]["rotor_positions"]
    assert pos and min(pos) >= nspp, pos


def test_a_non_monotone_curve_is_reported_regularized_not_differential():
    """Review finding 4: where dH/dB <= 0 the Newton's clamped tangent is used
    — that is no derivative of the material law, and the block, the summary
    and every consumer must say so instead of quoting it as small-signal."""
    from motor_ai_sim.simulation.ldq_provenance import (
        is_differential, record_status)
    bad = [(0.0, 0.0), (400.0, 0.5), (200.0, 0.8), (2000.0, 1.5), (1e6, 2.7)]
    pr = _square(bad, 1.0)
    x, y = pr["p2"].basis.doflocs
    A = 0.5 * np.sin(np.pi * x) * np.sin(np.pi * y)     # sweeps 0.5-0.8 T
    out = _probe(pr, A, TH)
    assert out["tangent_clamped"] is True
    assert out["dhdb_min_rel"] <= 0.0
    assert np.isfinite(out["Ld_H"]) and np.isfinite(out["Lq_H"])
    blk = {"Ld_mH": 1.0, "Lq_mH": 1.0, "Ld_fp_secant_mH": 2.0,
           "Lq_fp_secant_mH": 2.0, "ldq_method": LDQ_METHOD_REGULARIZED,
           "tangent_clamped": True}
    assert record_status(blk) == "regularized" and not is_differential(blk)
    # a label that forgot the flag is still caught by it
    assert record_status(dict(blk, ldq_method=LDQ_METHOD_DIFFERENTIAL)) \
        == "regularized"


def test_a_regularized_block_is_withheld_in_the_summary(solved):
    from motor_ai_sim.routes.simulation import _build_transient_summary
    blk = dict(solved["inc_ldq"], ldq_method=LDQ_METHOD_REGULARIZED,
               tangent_clamped=True)
    set_request_materials(OVERRIDE)
    try:
        s = _build_transient_summary(
            dict(solved, inc_ldq=blk), I_phase_rms=float(RUN["I_phase_rms"]),
            gamma_deg=float(RUN["gamma_deg"]),
            coil_temp_c=float(RUN["coil_temp_c"]),
            geo_override=dict(GEO_30MM))
    finally:
        set_request_materials(None)
    assert s["ldq_method"] == LDQ_METHOD_REGULARIZED
    assert s["ldq_tangent_clamped"] is True
    assert s["Ld_mH"] is None and s["Ld_inc_mH"] is None
    assert s["Ld_fp_secant_mH"] is not None
    assert "B-H" in s["dq_note"]


def test_a_regularized_no_load_probe_gives_no_catalogue_numbers(monkeypatch):
    from motor_ai_sim.routes import coupled as C
    monkeypatch.setattr(
        "motor_ai_sim.routes.simulation.catalogue_ldq0",
        lambda *a, **k: {"Ld_mH": 0.04, "Lq_mH": 0.05,
                         "ldq_method": LDQ_METHOD_REGULARIZED,
                         "tangent_clamped": True})
    out = C._cold_ldq0({"summary": {"daxis_deg": 12.5}}, {})
    assert out["Ld0_mH"] is None and out["Lq0_mH"] is None
    assert out["ldq0_status"] == "regularized"
    assert "B-H" in out["ldq0_note"]


_C20 = {"point": {"rpm": 20900, "I_phase_rms": 600.4, "gamma_deg": -15.0},
        "two_d": {"Kt_Nm_per_Arms": 0.4}, "k3d": {},
        "Ld0_mH": 0.0792, "Lq0_mH": 0.0830, "saliency0_Lq_over_Ld": 1.048}


@pytest.mark.parametrize("method,expect,absent", [
    ("differential (tangent reluctivity) at i=0, 20 °C", "differential",
     "SECANT"),
    ("frozen-permeability incremental at i=0, 20 °C", "SECANT",
     "small-signal)"),
    (None, "SECANT", "small-signal)"),           # unlabelled = the old probe
])
def test_stored_catalogue_ldq0_is_labelled_from_its_own_provenance(
        method, expect, absent):
    """Review finding 3: a constants_20c block saved before 2026-10-11 holds
    the secant value; the report labels it so, never as small-signal."""
    from motor_ai_sim import report as R
    blk = dict(_C20, **({"ldq0_method": method} if method else {}))
    rows = {r[0]: r for r in R.cold_constant_rows({"constants_20c": blk})}
    note = rows["Ld at no load [mH]"][2]
    assert expect in note and absent not in note, note
    assert rows["Ld at no load [mH]"][1].startswith("0.0792")


def test_a_regularized_catalogue_block_is_withheld_in_the_report():
    from motor_ai_sim import report as R
    blk = dict(_C20, Ld0_mH=None, Lq0_mH=None, saliency0_Lq_over_Ld=None,
               ldq0_status="regularized", ldq0_method=LDQ_METHOD_REGULARIZED)
    rows = {r[0]: r for r in R.cold_constant_rows({"constants_20c": blk})}
    assert "Ld at no load [mH]" not in rows
    assert "B-H" in rows["Ld / Lq at no load [mH]"][2]


@pytest.mark.parametrize("method,expect", [
    ("differential (tangent reluctivity) at i=0, 20 °C", "differential"),
    ("frozen-permeability incremental at i=0, 20 °C", "SECANT"),
])
def test_the_datasheet_labels_stored_ldq_from_provenance(tmp_path, method,
                                                         expect):
    """Same rule on the Excel card — the catalogue block AND the loaded duty
    row (which used to call the loaded values "chord")."""
    from openpyxl import load_workbook
    from motor_ai_sim.datasheet import build_datasheet
    from tests.test_coupled_cold_constants import BLOCK

    loaded = ("differential (tangent reluctivity)" if expect == "differential"
              else "frozen-permeability incremental at the point")
    die_doc = {"geometry": {"num_poles": 28, "num_slots": 24,
                            "stator_diameter": 85.0, "motor_length": 13.0}}
    cfg_doc = {"winding": {"star_delta": "star"},
               "duties": [{"name": "peak", "current_arms": 45.96, "rpm": 1000.0,
                           "gamma_deg": 2.0,
                           "summary": {"T_em_avg_Nm": 7.57,
                                       "I_phase_rms_A": 45.96,
                                       "R_phase_ohm": 0.05, "Ld_mH": 0.05,
                                       "Lq_mH": 0.06, "ldq_method": loaded}}]}
    blob = build_datasheet(
        die="D", cfg="L13", die_doc=die_doc, cfg_doc=cfg_doc,
        coupled={"peak": {"constants_20c": dict(
            BLOCK, Ld0_mH=0.0792, Lq0_mH=0.0830, ldq0_method=method)}})
    p = tmp_path / "card.xlsx"
    p.write_bytes(blob)
    ws = load_workbook(p).active
    notes = {str(ws.cell(row=r, column=1).value or ""):
             " ".join(str(ws.cell(row=r, column=c).value or "")
                      for c in range(2, ws.max_column + 1))
             for r in range(1, ws.max_row + 1)}
    assert expect in notes["Ld at 20 °C, no load (mH)"], notes
    ld_load = notes["Ld at load (mH)"]
    assert "chord" not in ld_load and "2-D magnetostatic" in ld_load, ld_load
    if expect == "differential":
        assert "differential (small-signal)" in ld_load
    else:
        assert "secant" in ld_load


def test_cold_is_twenty_degrees_in_both_modules():
    """The solver's probe temperature and the coupled block's are ONE number."""
    from motor_ai_sim.routes.coupled import COLD_CONSTANTS_C
    assert NOLOAD_LDQ_TEMP_C == COLD_CONSTANTS_C == 20.0
