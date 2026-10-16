"""The three silent loss bugs of the passport extraction (audit 2026-09-30,
confirmed and sharpened by the independent review of the same day).

1. End-winding split: an explicit k_end handed unchanged to the 1.5·L0 solve
   made R ∝ L, so ``endWindFrac`` came out 0 on every passport whose preset
   carried a factor (copper −35 % / +37 % at 0.5× / 2× stack on the Ø40).
   The solver's copper IS R = C·(L + ell) with ell = (k_end − 1)·L0 (one-side
   equivalent), so the end share is e = 1 − 1/k_end of the base solve.
2. Delta cuAC: the solved copper was divided by 3·I_line²·R_winding, three
   times the winding's real DC loss, so every delta cell sat below 1 and the
   tuner dropped the AC copper entirely (L155: 897 W → 0 W).  The loss grid
   and the PWM solves now also get the terminal connection explicitly, and the
   DC and AC watts are stored beside the ratio.
3. EMF: the no-load waveform PEAK of a coarse solve was stored as the EMF
   (−14 % on the L155 coarse passport); it is now E1 = ω_e·|ψ1| from the
   solved flux linkage.

No FEM here: the helpers are pure, and ``generate_passport`` is driven end to
end by a stub solver that follows the real copper model (R ∝ L·k_end, DC loss
of a delta winding = I_line²·R_w) and returns known flux-linkage waveforms.
"""
from __future__ import annotations

import json
import math

import pytest

from motor_ai_sim import passport as pp


# ── 1. end-winding split ────────────────────────────────────────────────────

def test_end_factor_holds_the_one_side_end_length_fixed():
    k0, L0 = 2.19, 12.0
    for L in (6.0, 18.0, 24.0):
        k = pp.end_factor_at(k0, L0, L)
        assert (k - 1.0) * L == pytest.approx((k0 - 1.0) * L0)   # ell = 14.28 mm
    assert (k0 - 1.0) * L0 == pytest.approx(14.28)
    assert pp.end_factor_at(k0, L0, L0) == pytest.approx(k0)
    assert pp.end_factor_at(0.0, L0, 18.0) == 0.0          # auto passes through


def _R_solver(L, k_end):
    """The solver's copper model (field_ops.copper_loss_W): R ∝ L·k_end."""
    return 1e-3 * L * k_end


def test_the_review_numbers_for_the_40mm():
    """k_end 2.19: e = 0.543379, R(L/2)/R0 = 0.771689, R(2L)/R0 = 1.456621
    (independent review 2026-09-30) — from the split AND from the solver law
    with the fixed end length, which must agree."""
    k0, L0 = 2.19, 12.0
    e = 1.0 - 1.0 / k0
    assert e == pytest.approx(0.543379, abs=1e-6)
    R0 = _R_solver(L0, k0)
    for f, want in ((0.5, 0.771689), (2.0, 1.456621)):
        law = (1.0 - e) * f + e                          # what the tuner applies
        solver = _R_solver(f * L0, pp.end_factor_at(k0, L0, f * L0)) / R0
        assert law == pytest.approx(want, abs=1e-6)
        assert solver == pytest.approx(want, abs=1e-6)


def test_split_from_two_solves_with_the_fixed_end_length():
    k0, L0 = 2.19, 12.0
    R_A = _R_solver(L0, k0)
    R_C = _R_solver(1.5 * L0, pp.end_factor_at(k0, L0, 1.5 * L0))
    assert pp.end_winding_fraction(R_A, R_C, 1.5) == pytest.approx(1 - 1 / k0)


def test_the_old_call_read_zero_end_winding():
    """Regression witness: the same k at 1.5·L0 is what produced 0."""
    k0, L0 = 2.19, 12.0
    assert pp.end_winding_fraction(_R_solver(L0, k0),
                                   _R_solver(1.5 * L0, k0), 1.5) == 0.0


# ── 2. delta cuAC ───────────────────────────────────────────────────────────

def test_ac_factor_star():
    I, Rw = 40.0, 0.0118
    P = 1.25 * 3 * I * I * Rw
    assert pp.dc_copper_W(I, Rw, "star") == pytest.approx(3 * I * I * Rw)
    assert pp.ac_copper_factor(P, I, Rw, "star") == pytest.approx(1.25)


def test_ac_factor_delta_normalises_by_the_equivalent_star():
    I_line, Rw = 562.0, 0.00525
    I_w = I_line / math.sqrt(3.0)
    P_dc = 3 * I_w * I_w * Rw                    # = I_line²·Rw = 3·I_line²·Rw/3
    assert pp.dc_copper_W(I_line, Rw, "delta") == pytest.approx(P_dc)
    assert pp.ac_copper_factor(1.5 * P_dc, I_line, Rw, "delta") == pytest.approx(1.5)
    # the old formula: a third of it, below 1 → the tuner's AC term vanished
    assert (1.5 * P_dc) / (3 * I_line * I_line * Rw) == pytest.approx(0.5)


# ── 3. EMF through the flux ─────────────────────────────────────────────────

def _flux_run(n, psi1=0.01, psi5=0.0012, f_e=500.0, periods=1.0):
    """Balanced flux linkages with a 5th harmonic, n samples per period, and
    the midpoint-difference voltages the solver would derive from them."""
    out = {"n_periods": periods, "f_elec_Hz": f_e}
    N = int(round(n * periods))
    dt = 1.0 / (f_e * n)
    for ph, sh in (("A", 0.0), ("B", -2 * math.pi / 3), ("C", 2 * math.pi / 3)):
        th = [2 * math.pi * k / n + sh for k in range(N + 1)]
        psi = [psi1 * math.cos(t) + psi5 * math.cos(5 * t + 0.4) for t in th]
        out[f"psi_{ph}_Wb"] = psi[:N]
        out[f"V_{ph}"] = [-(psi[k + 1] - psi[k]) / dt for k in range(N)]
    return out


def test_emf_is_omega_times_the_flux_fundamental():
    d = _flux_run(24)
    assert pp.emf_fundamental(d) == pytest.approx(2 * math.pi * 500.0 * 0.01, rel=1e-9)


def test_six_steps_still_exact_through_the_flux():
    """The review's point: the voltage series is a midpoint difference and
    passes only sin(π/6)/(π/6) = 0.955 of the fundamental at 6 steps — the
    flux route does not care; the 5th harmonic aliases onto the fundamental of
    the FLUX at 6 samples, which is why run B is solved at ≥ 24 steps."""
    d = _flux_run(6, psi5=0.0)
    assert pp.emf_fundamental(d) == pytest.approx(2 * math.pi * 500.0 * 0.01, rel=1e-9)
    from motor_ai_sim.simulation.postproc import voltage_harmonics
    v1 = voltage_harmonics(d)["V1_phase_V"]
    assert v1 / (2 * math.pi * 500.0 * 0.01) == pytest.approx(
        math.sin(math.pi / 6) / (math.pi / 6), rel=1e-3)


def test_voltage_fallback_undoes_the_midpoint_sinc():
    d = _flux_run(24, psi5=0.0)
    for k in ("psi_A_Wb", "psi_B_Wb", "psi_C_Wb"):
        d.pop(k)
    assert pp.emf_fundamental(d) == pytest.approx(2 * math.pi * 500.0 * 0.01, rel=2e-3)


def test_no_series_no_fundamental():
    assert pp.emf_fundamental({"summary": {}}) is None


# ── end to end: generate_passport on a stub solver ──────────────────────────

class _StubSolver:
    """Follows the real models the three fixes depend on."""

    PSI1 = 0.02        # winding phase flux fundamental at the base stack [Wb]
    R_PER_MM = 4e-4    # winding resistance per mm of active copper
    AC = 1.30          # true solved-copper / DC ratio at every grid point
    F_E = 400.0

    def __init__(self, star_delta, L0):
        self.sd, self.L0 = star_delta, L0
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)
        geo = json.loads(kw["geo"]) if kw.get("geo") else {}
        L = float(geo.get("motor_length", self.L0))
        k_end = float(kw.get("end_winding_factor") or 1.0)
        Rw = self.R_PER_MM * L * k_end
        I = float(kw.get("I_phase_rms") or 0.0)
        steps = int(kw.get("n_steps_per_period") or 12)
        delta = str(kw.get("star_delta") or "star").startswith("d")
        I_w = I / math.sqrt(3.0) if delta else I
        P_dc = 3.0 * I_w * I_w * Rw
        scale = (L / self.L0) * (1.2 if I > 0 else 1.0)
        ser = _flux_run(steps, self.PSI1 * scale, 0.1 * self.PSI1 * scale, self.F_E)
        peak = max(max(abs(x) for x in ser[k]) for k in ("V_A", "V_B", "V_C"))
        return {
            **ser,
            "T_avg_Nm": 0.01 * I_w * L / self.L0, "R_phase_ohm": Rw,
            "V_peak": peak, "rpm": kw.get("rpm") or 1000.0,
            "P_cu_W": [self.AC * P_dc], "P_fe_W": [5.0], "P_mag_eddy_W": [1.0],
            "P_shaft_eddy_W": [0.0], "end_winding_factor": k_end,
            # the summary rounds k_end to 0.01 for display — the passport
            # must read the unrounded solver value
            "summary": {"end_winding_factor": round(k_end, 1), "V_phase_peak_V": peak,
                        "P_core_W": 5.0, "P_solid_W": 1.0, "mass_total_kg": 1.0,
                        "T_ripple_pct": 5.0, "demag": {"loss_pct": 0.0}},
        }


@pytest.mark.parametrize("sd", ["star", "delta"])
@pytest.mark.parametrize("k0", [2.19, 0.0])
def test_generate_passport_end_to_end(monkeypatch, sd, k0):
    from motor_ai_sim.config import get_config
    from motor_ai_sim.routes import simulation as sim

    geo = dict(get_config().get("geometry") or {})
    L0 = float(geo.get("motor_length") or 12.0)
    stub = _StubSolver(sd, L0)
    monkeypatch.setattr(sim, "get_fem_transient", stub)
    out = pp.generate_passport(
        machine={"geometry": geo, "connection": None, "star_delta": sd,
                 "materials": {}, "end_winding_factor": k0},
        I0=30.0, gamma_deg=10.0, rpm0=1000.0, rpms=[500.0, 1000.0],
        base_steps=6, sweep_steps=6, pwm="off")
    p = out["passport"]

    # 1. end winding: e = 1 − 1/k_end of the base solve; no 1.5·L0 solve needed
    k_used = k0 if k0 > 0 else 1.0          # the stub's "auto" reports 1.0
    assert p["endWindFrac"] == pytest.approx(1 - 1 / k_used, abs=1e-12)
    assert not [c for c in stub.calls
                if abs(float(json.loads(c["geo"]).get("motor_length", L0))
                       - 1.5 * L0) < 1e-6]

    # 2. every solve is told the terminal connection; cuAC and the watts are true
    assert all(str(c.get("star_delta")) == sd for c in stub.calls)
    lg = p["loss_grid"]
    for r, I in enumerate(lg["I_A"]):
        for c, a in enumerate(lg["cuAC"][r]):
            assert a == pytest.approx(_StubSolver.AC, rel=1e-9)
            assert lg["Pcu_ac_W"][r][c] == pytest.approx(
                (_StubSolver.AC - 1.0) * lg["Pcu_dc_W"][r][c], rel=1e-9)

    # 3. EMF = ω_e·ψ1 of the no-load solve (star-equivalent in delta)
    kV = 1 / math.sqrt(3) if sd == "delta" else 1.0
    E1 = 2 * math.pi * _StubSolver.F_E * _StubSolver.PSI1
    assert p["emf_basis"] == "fundamental"
    assert p["Vemf0_peak_V"] == pytest.approx(E1 * kV, rel=1e-9)
    nl = [c for c in stub.calls if float(c.get("I_phase_rms") or 0) == 0.0]
    assert nl and all(int(c["n_steps_per_period"]) >= 24 for c in nl)
    assert p["Vload0_fund_V"] == pytest.approx(1.2 * E1 * kV, rel=1e-3)
    assert p["extraction_rev"] == 2
    # stored unrounded
    assert p["R0_ohm"] == pytest.approx(
        _StubSolver.R_PER_MM * L0 * (k0 or 1.0) * (1 / 3 if sd == "delta" else 1),
        rel=1e-12)


# ── P22: the torque factor on Kt/Km, the flux factor on KV / ψ_PM, once ────

def _cold(end3d):
    from motor_ai_sim.routes import coupled
    s = {"Kt_Nm_per_Arms": 0.1, "Km_Nm_sqrtW": 0.4, "psi_pm_Wb": 0.01,
         "KV_noload_rpm_per_V_line": 50.0, "star_delta": "star",
         "end3d": end3d}
    return coupled._cold_constants({"summary": s}, body={}, rpm=1000.0,
                                   drive="current")


def test_cold_constants_use_k_T_when_measured():
    k = _cold({"k_flux": 0.952, "k_T": 0.9795})
    assert k["k3d"]["Kt_Nm_per_Arms"] == pytest.approx(0.1 * 0.9795, abs=1e-9)
    assert k["k3d"]["Km_Nm_sqrtW"] == pytest.approx(0.4 * 0.9795, abs=1e-9)
    assert k["k3d"]["psi_pm_Wb"] == pytest.approx(0.01 * 0.952, abs=1e-9)
    assert k["k3d"]["KV_noload_rpm_per_V_line"] == pytest.approx(50.0 / 0.952, abs=1e-6)
    assert k["torque_basis"] == "3-D"


def test_cold_constants_use_k_flux_without_a_measured_k_T():
    """Owner 2026-09-30 (refining #90): an existing 3-D result is used."""
    k = _cold({"k_flux": 0.952})
    assert k["k3d"]["Kt_Nm_per_Arms"] == pytest.approx(0.1 * 0.952, abs=1e-9)
    assert k["torque_basis"] == "3-D, flux factor"
    assert k["k_torque"] == pytest.approx(0.952)
