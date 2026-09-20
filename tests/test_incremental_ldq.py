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
    NOLOAD_LDQ_TEMP_C, fem_transient_sliding_band, frozen_permeability_ldq)

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

    def solve_ff(self, Kff, rhs):
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
              "spread_pct", "reciprocity_pct", "superposition_pct", "method"):
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
    assert s["ldq_method"] == "frozen-permeability incremental at the point"
    assert s["Ld_mH"] is not None and s["Lq_mH"] is not None
    assert s["Ld_mH"] == s["Ld_inc_mH"]
    assert s["Lq_mH"] == s["Lq_inc_mH"]
    assert s["Ldq_inc_mH"] is not None
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


# ═══════════════════════════════════════════════════════════════════════════
#  The catalogue block: Ld0 / Lq0 at 20 C and no load, beside KV
# ═══════════════════════════════════════════════════════════════════════════

def test_the_cold_pass_carries_the_catalogue_inductances(monkeypatch):
    from motor_ai_sim.routes import coupled as C

    probe = {
        "Ld_mH": 0.0405, "Lq_mH": 0.0495, "Ldq_mH": 0.0002,
        "spread_pct": {"Ld": 0.4, "Lq": 0.6}, "reciprocity_pct": 0.01,
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
                "spread_pct": {"Ld": 0.2, "Lq": 1.5}, "reciprocity_pct": 0.0}

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
         "psi_pm_Wb": 0.0706, "psi_pm_frozen_Wb": 0.0663,
         "psi_pm_sag_pct": 6.14, "gamma_deg": -15.0,
         "ldq_method": "frozen-permeability incremental at the point"})}
    assert "Cross-saturation Ldq" in rows
    assert rows["Cross-saturation Ldq"][1].startswith("-0.0027")
    # …and the ψ_PM row says in one clause what the load did to the magnets
    pm = rows["Magnet flux linkage Psi_PM"][2]
    assert "loaded iron" in pm and "-6.1" in pm


def test_cold_is_twenty_degrees_in_both_modules():
    """The solver's probe temperature and the coupled block's are ONE number."""
    from motor_ai_sim.routes.coupled import COLD_CONSTANTS_C
    assert NOLOAD_LDQ_TEMP_C == COLD_CONSTANTS_C == 20.0
