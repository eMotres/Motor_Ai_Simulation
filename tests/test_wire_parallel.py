"""``wire_parallel`` — strands in hand — is an extra parallel-path factor.

The claim under test, stated once so the numbers below are checkable:

    Winding k wires IN HAND per turn changes nothing physical about the slot.
    The same ``num_wires_per_slot`` conductors sit in the same copper, the CAD
    is byte-identical and the mass does not move.  What changes is the WIRING:
    only ``num_wires_per_slot / k`` of those conductors are in SERIES, and k of
    them carry each turn's current side by side.  Electrically that is exactly
    one more factor on the parallel-path count, so at the SAME PHASE CURRENT

        turns/coil ÷ k      psi_PM ÷ k      back-EMF, V_phase ÷ k
        Kt, torque  ÷ k     KV  × k         R_phase, Ld, Lq  ÷ k²
        P_cu(DC)    ÷ k²    J per strand ÷ k        mass unchanged

Every FEM case here is solver-direct (``fem_transient_sliding_band`` with a
``geo_override``) on the 30 mm regression geometry, so nothing reads — or
touches — the user's live config.  ``daxis_deg`` is PASSED rather than
calibrated: the two runs must sit in the SAME electrical frame for a ratio to
mean anything, and a per-fingerprint calibration would give them two.
"""
from __future__ import annotations

import math
from typing import Any, Dict

import numpy as np
import pytest

from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band
from motor_ai_sim.winding import (n_parallel_effective, turns_per_coil,
                                  wire_parallel_from_geo)

from tests.test_physics_regression import GEO_30MM, OVERRIDE, RPM, CONNECTION

#: k under test.  3 divides the 30 mm machine's 6 wires per slot, and it is not
#: 2 — a factor-2 bug that reads as a sign, a half or a doubling somewhere else
#: would still pass at k = 2.
K = 3

#: LOW current on purpose.  Torque ÷ k is exact only in the linear iron: with k
#: strands the slot MMF drops by k, so a saturated k = 1 run is compared against
#: an unsaturated k = 3 one and the ratio drifts.  4 A on this machine (its own
#: pinned point is 60 A) keeps both runs in the same iron.
I_PROBE = 4.0

#: The 12s/14p d-axis this machine calibrates to (config/.daxis_cache.json holds
#: 59.87…59.95 across every 12s14p cross-section measured).  Passed, not
#: measured, so the two runs share one frame — and so the suite never starts a
#: 24-frame calibration or writes a cache under config/.
DAXIS_DEG = 59.9

_RUN = dict(n_steps_per_period=6, n_periods=1.0, mesh_size_mm=1.4,
            min_size_mm=0.35, gap_layers=1.0, n_sectors=2,
            structured_gap=True, iron_template=True, geo_mesh=True,
            coil_temp_c=120.0, rotor_eddy=False, element_order=2,
            demag=False, daxis_deg=DAXIS_DEG)


def _geo(k: int) -> Dict[str, Any]:
    """The regression geometry with the split wire and k strands in hand.

    ``wire_split`` is set on BOTH so the pair differs in exactly one key: the
    two are independent constructs (a split lays N strips of wire_width side by
    side across the slot; strands in hand are k wires wound as one turn) and the
    point of carrying both is that neither shadows the other.

    The split's strips are SERIES turns, so it contributes a constant factor 2
    to ``turns_per_coil`` on both members of the pair — and nothing at all to
    ``n_parallel_effective`` — and therefore cancels out of every ratio this
    file measures.
    """
    g = dict(GEO_30MM)
    g["wire_split"] = 2
    g["wire_parallel"] = int(k)
    return g


def _solve(k: int, current_a: float) -> Dict[str, Any]:
    set_request_materials(OVERRIDE)
    try:
        return fem_transient_sliding_band(
            geo_override=_geo(k), rpm=RPM, connection=CONNECTION,
            I_phase_rms=float(current_a), gamma_deg=0.0, **_RUN)
    finally:
        set_request_materials(None)


def _amp(series) -> float:
    """Peak-to-peak/2 of a periodic series — the amplitude a ratio can use."""
    a = np.asarray(series, float)
    return float((a.max() - a.min()) / 2.0) if a.size else 0.0


def _mean(v) -> float:
    return float(np.mean(np.asarray(v, float))) if isinstance(v, list) else float(v)


# ─────────────────────────────────────────────────────────────────────────────
# Cheap: the parameter itself
# ─────────────────────────────────────────────────────────────────────────────

def test_absent_means_one_strand_in_hand():
    assert wire_parallel_from_geo({}) == 1
    assert wire_parallel_from_geo({"wire_parallel": None}) == 1
    assert wire_parallel_from_geo(dict(GEO_30MM)) == 1
    assert turns_per_coil(dict(GEO_30MM)) == GEO_30MM["num_wires_per_slot"]
    assert n_parallel_effective(2, dict(GEO_30MM)) == 2


def test_turns_and_effective_paths_divide_and_multiply():
    g = _geo(K)
    # turns are the WIRE ROWS / k, TIMES the geometry's wire_split = 2 — those
    # strips are consecutive SERIES turns, not strands.
    assert turns_per_coil(g) == (GEO_30MM["num_wires_per_slot"] // K) * 2 == 4
    # …and they are NOT a divider on the branch current: only the k strands in
    # hand multiply the paths (winding.n_parallel_effective).
    assert n_parallel_effective(1, g) == K
    assert n_parallel_effective(4, g) == 4 * K


def test_strands_that_do_not_divide_the_wires_are_refused_by_name():
    """7 wires wound 2-in-hand is 3.5 turns — a rounded turn count would be a
    machine the user never asked for, so it refuses and names BOTH numbers."""
    with pytest.raises(ValueError) as ei:
        wire_parallel_from_geo({"num_wires_per_slot": 7, "wire_parallel": 2})
    msg = str(ei.value)
    assert "7" in msg and "2" in msg
    assert "num_wires_per_slot" in msg and "wire_parallel" in msg

    from motor_ai_sim.geometry_validation import validate_parameter_values
    bad = validate_parameter_values({"num_wires_per_slot": 7, "wire_parallel": 2})
    assert [b["field"] for b in bad] == ["wire_parallel"]
    assert "7" in bad[0]["message"] and "2" in bad[0]["message"]
    assert bad[0]["kind"] == "derived"      # the PAIR is broken, not one field


def test_zero_and_fractional_strands_are_refused():
    for v in (0, -1, 1.5, "two"):
        with pytest.raises(ValueError):
            wire_parallel_from_geo({"num_wires_per_slot": 6, "wire_parallel": v})


def test_the_solver_refuses_an_indivisible_pair():
    g = dict(GEO_30MM); g["num_wires_per_slot"] = 7; g["wire_parallel"] = 2
    with pytest.raises(ValueError) as ei:
        fem_transient_sliding_band(geo_override=g, rpm=RPM,
                                   connection=CONNECTION, I_phase_rms=1.0,
                                   gamma_deg=0.0, **_RUN)
    assert "7" in str(ei.value) and "2" in str(ei.value)


def test_psi_pm_and_bench_caches_cannot_answer_across_k():
    """psi_PM ÷ k, so a cache shared between two k would hand a k = 1 psi_PM to a
    k = 3 machine and ship a silently wrong Ld — the same failure the connection
    already caused once (see psipm_cache_key).

    The BENCH cache keys on the whole merged geometry (`_geometry_fingerprint`),
    which carries every geometry knob, so holding wire_parallel in the geometry
    block is what separates its entries.

    The ψ_PM cache does NOT: it keys on `_daxis_geo_fingerprint`, which is
    deliberately blind to the winding knobs because none of them can move the
    ANGLE θ* that fingerprint exists for.  So the ψ_PM key carries the winding
    SCALE — the series turns and the effective parallel paths — explicitly.
    (Until 2026-09-08 it did not, and a k = 1 ψ_PM really was served to a k = 3
    machine.)"""
    from motor_ai_sim.simulation.fem_solver_2d import (_daxis_geo_fingerprint,
                                                       psipm_cache_key)
    from motor_ai_sim.routes.simulation import _geometry_fingerprint
    w = {"connection": CONNECTION, "n_parallel": 1, "layers": 1}
    assert _geometry_fingerprint(_geo(1)) != _geometry_fingerprint(_geo(K))
    assert psipm_cache_key(_geo(1), w) != psipm_cache_key(_geo(K), w)
    # …and the SPLIT, whose series strips scale ψ_PM by N
    g_split = dict(_geo(1)); g_unsplit = dict(g_split, wire_split=1)
    assert psipm_cache_key(g_unsplit, w) != psipm_cache_key(g_split, w)
    # the ANGLE, on the other hand, is genuinely the same for all of them — the
    # inert set is right, and this is the reason the key above had to carry the
    # scale itself
    assert _daxis_geo_fingerprint(_geo(1)) == _daxis_geo_fingerprint(_geo(K))
    assert _daxis_geo_fingerprint(g_unsplit) == _daxis_geo_fingerprint(g_split)


def test_mass_and_copper_section_do_not_move_with_strands_in_hand():
    """The slot holds the same wires either way — the CAD cannot see k."""
    from motor_ai_sim.masses import cad_areas_m2

    a1 = cad_areas_m2(_geo(1))
    ak = cad_areas_m2(_geo(K))
    assert a1 and ak
    assert set(a1) == set(ak)
    for part, v in a1.items():
        assert math.isclose(v, ak[part], rel_tol=1e-12, abs_tol=0.0), part


# ─────────────────────────────────────────────────────────────────────────────
# The FEM ratios
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def loaded() -> Dict[int, Dict[str, Any]]:
    return {k: _solve(k, I_PROBE) for k in (1, K)}


@pytest.fixture(scope="module")
def noload() -> Dict[int, Dict[str, Any]]:
    return {k: _solve(k, 0.0) for k in (1, K)}


@pytest.mark.slow
def test_solver_reports_the_effective_winding(loaded):
    d1, dk = loaded[1], loaded[K]
    assert d1["wire_parallel"] == 1 and dk["wire_parallel"] == K
    # ×2 on both: the fixture's wire_split = 2 makes every wire row TWO series
    # turns, so 6 rows are 12 turns at k = 1 and 4 at k = K.
    assert d1["turns_per_coil"] == 12 and dk["turns_per_coil"] == 4
    # The CONNECTION's own paths are untouched by the strands (2S -> 1 path);
    # the effective divider is their product.  The split is NOT in it — its
    # strips are turns, and each carries the branch current whole.
    assert d1["n_parallel"] == dk["n_parallel"] == 1
    assert d1["n_parallel_eff"] == 1 and dk["n_parallel_eff"] == K
    assert d1["connection"] == dk["connection"] == CONNECTION


@pytest.mark.slow
def test_psi_pm_and_back_emf_divide_by_k(noload):
    """At I = 0 the field is the magnet's alone: it cannot see k, so the whole
    ratio is the SERIES TURNS — the cleanest 1/k in the suite."""
    d1, dk = noload[1], noload[K]
    r_psi = _amp(dk["psi_A_Wb"]) / _amp(d1["psi_A_Wb"])
    r_v = float(dk["V_peak"]) / float(d1["V_peak"])
    assert math.isclose(r_psi, 1.0 / K, rel_tol=2e-3), r_psi
    assert math.isclose(r_v, 1.0 / K, rel_tol=2e-3), r_v


@pytest.mark.slow
def test_torque_kt_and_kv_at_the_same_phase_current(loaded):
    d1, dk = loaded[1], loaded[K]
    t1, tk = _mean(d1["T_avg_Nm"]), _mean(dk["T_avg_Nm"])
    assert abs(t1) > 1e-4, "probe torque is noise — the ratio would be too"
    assert math.isclose(tk / t1, 1.0 / K, rel_tol=1e-2), tk / t1

    # Kt = T / I_phase and KV = rpm / V: the same two numbers read the other way
    # up, and the pair is what a customer is quoted.
    kt1, ktk = t1 / I_PROBE, tk / I_PROBE
    assert math.isclose(ktk / kt1, 1.0 / K, rel_tol=1e-2)
    # KV is 1/V, so it carries the same R·I offset the loaded terminal voltage
    # does, with the sign the other way up (see test_terminal_voltage_divides_
    # by_k for the measured size and why the split's SERIES strips doubled it).
    kv1 = RPM / float(d1["V_peak"])
    kvk = RPM / float(dk["V_peak"])
    # 7 %: measured +2.13 % with the sandbox's steel shaft and +2.55 % once the
    # shared OVERRIDE pinned the aluminium shaft the pins were generated at
    # (2026-09-08) — the R·I share of the series strips, on both legs — and
    # +5.8 % since the terminal voltage is the Crank–Nicolson STEP voltage
    # (no-filter pass 2026-09-24): at this fixture's 6 steps/period the step
    # mean of the EMF is sinc(π/6) = 0.955 of its sinusoid while the R·I term
    # sits at the step-mean current, so the resistive share of the PEAK grows.
    # The no-load ratio (pure EMF) is still exact to 2e-3, see above.
    assert math.isclose(kvk / kv1, float(K), rel_tol=7e-2), kvk / kv1


@pytest.mark.slow
def test_resistance_and_dc_copper_divide_by_k_squared(loaded):
    """R and P_cu(DC) pick up 1/k² from BOTH halves at once: k times fewer turns
    in series, k strands sharing each turn's current."""
    d1, dk = loaded[1], loaded[K]
    r_R = float(dk["R_phase_ohm"]) / float(d1["R_phase_ohm"])
    assert math.isclose(r_R, 1.0 / K ** 2, rel_tol=1e-9), r_R
    p1, pk = _mean(d1["P_cu_dc_W"]), _mean(dk["P_cu_dc_W"])
    assert p1 > 0.0
    assert math.isclose(pk / p1, 1.0 / K ** 2, rel_tol=1e-9), pk / p1


@pytest.mark.slow
def test_terminal_voltage_divides_by_k(loaded):
    """V = R·I + dpsi/dt.  Only the EMF is a pure 1/k — the resistive term falls
    faster, so the ratio approaches 1/k FROM BELOW as R·I shrinks and is never
    exactly 1/k at a finite current.  Measured at the 4 A probe: 0.32623 against
    1/3, i.e. −2.13 %, which is the R·I share of the terminal voltage and not a
    scaling error.  The no-load case above is the exact 1/k.

    That share DOUBLED on 2026-09-08 (it was −0.88 %) and nothing about
    wire_parallel moved: the fixture carries wire_split = 2, and once those
    strips became SERIES turns instead of parallel strands the SAME machine has
    2× the turns and 4× the copper path — EMF ×2 against R ×4, so R·I is twice
    the fraction of the terminal volt it used to be.  Both legs of the ratio
    took it, which is why the ratio itself is still 1/k to 2 %."""
    d1, dk = loaded[1], loaded[K]
    r_v = float(dk["V_peak"]) / float(d1["V_peak"])
    # −2.13 % measured with the sandbox's steel shaft, −2.55 % with the
    # aluminium shaft the shared OVERRIDE now pins (2026-09-08): the resistive
    # share moves with the field the shaft's permeability shapes, the ratio's
    # purpose (1/k) does not.  −5.5 % since 2026-09-24: the CN step voltage at
    # 6 steps/period (see test_torque_kt_and_kv_at_the_same_phase_current).
    assert math.isclose(r_v, 1.0 / K, rel_tol=7e-2), r_v


@pytest.mark.slow
def test_inductance_divides_by_k_squared(loaded, noload):
    """The ARMATURE half of the flux linkage — psi(I) − psi(0), the same two
    numbers the bench Ld/Lq is a quotient of — falls by k², because it is the
    only quantity that takes BOTH divisions: the MMF that makes the field ÷ k,
    and the turns that link it ÷ k.  psi_PM takes only the second (see the
    no-load case), which is exactly why L ∝ 1/k² and psi_PM ∝ 1/k.

    Measured off the RAW psi_A series, not the dq stamp: psi_d/psi_q are rounded
    to 6 decimals in the payload and psi_q at this probe current is 2e-5 Wb, so
    a ratio of the stamped values would be reading the rounding.  The phase
    currents themselves do NOT move — they are terminal quantities.
    """
    d1, dk = loaded[1], loaded[K]
    for ax in ("d", "q"):
        i1 = float(d1[f"i_{ax}_A"]); ik = float(dk[f"i_{ax}_A"])
        assert math.isclose(ik, i1, rel_tol=2e-2, abs_tol=1e-2), (ax, i1, ik)
    a1 = _amp(np.asarray(d1["psi_A_Wb"]) - np.asarray(noload[1]["psi_A_Wb"]))
    ak = _amp(np.asarray(dk["psi_A_Wb"]) - np.asarray(noload[K]["psi_A_Wb"]))
    assert a1 > 1e-9
    # 1 % of tolerance for the iron: the k = 1 run carries k times the MMF, so
    # it sits slightly deeper in saturation than its k = 3 twin.
    assert math.isclose(ak / a1, 1.0 / K ** 2, rel_tol=1e-2), ak / a1


@pytest.mark.slow
def test_current_density_per_strand_divides_by_k(loaded):
    """The summary's J is per PHYSICAL wire, and each of the k in hand carries
    I_coil/k."""
    d1, dk = loaded[1], loaded[K]
    a_mm2 = float(GEO_30MM["wire_width"]) * float(GEO_30MM["wire_height"])
    j1 = I_PROBE / d1["n_parallel_eff"] / a_mm2
    jk = I_PROBE / dk["n_parallel_eff"] / a_mm2
    assert math.isclose(jk / j1, 1.0 / K, rel_tol=1e-12)
