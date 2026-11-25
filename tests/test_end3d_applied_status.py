"""Follow-up of the k_flux agreement check: the adopted store entry stores the APPLIED factor with the real k_flux beside
it; auto stack-length factors; the residual's degrees of freedom; the k_flux_status a reader shows.  Derived numbers
only (a 12s14p 40 mm machine at 12 mm: k_flux 0.93812, k_flux_self 0.96476)."""
from __future__ import annotations

import math

import pytest

from motor_ai_sim import end3d_verdict as V
from motor_ai_sim.regions import catalog_manifest as CM
from motor_ai_sim.routes import static3d as S
from motor_ai_sim.simulation.static3d import end_effect as EE
from tests.test_kflux_stack_length import B1_2D, REF_MM, TABLE, stubbed  # noqa: F401  (fixture)

KF, KS = 0.93812, 0.96476


def _sa(**over):
    sa = {"geometry_fingerprint": "abc123def456", "generated_utc": "2026-11-25T10:00:00Z", "k_flux": KF,
          "k_flux_self": KS, "version": "static3d-stageA-3",
          "machine": {"num_slots": 12, "num_poles": 14, "stator_od_mm": 40.0, "stack_mm": 12.0},
          "l_stack_curve": [{"stack_mm": 12.0, "k_flux": KF}]}
    sa.update(over)
    return sa


def _two_d(verdict, status=None):
    t = {"verdict": verdict, "ratio_3d_mid_over_2d": KF / KS, "picard_converged": True,
         "agreement_check": {"verdict": verdict}}
    if status:
        t["k_flux_status"] = status
    return t


# --------------------------------------------------------------------------
# 1. adopt_entry stores the applied factor
# --------------------------------------------------------------------------

def test_a_failed_check_stores_k_flux_self_declared_with_the_real_k_flux_beside_it():
    _fp, e = S.adopt_entry(_sa(two_d=_two_d("FAIL", "failed"), k_flux_basis="2-D check failed: ..."))
    assert e["k_flux"] == KS and e["k_flux_raw"] == KF and e["k_flux_applied"] == "k_flux_self"
    assert e["k_flux_status"] == "failed" and e["k_flux_basis"].endswith("k_flux_self applied (the real k_flux is k_flux_raw)")
    assert V.substitution_of(e) is None                                   # declared, not a silent substitution


@pytest.mark.parametrize("two_d, status", [(_two_d("PASS", "verified"), "verified"),
                                           (_two_d("NOT CHECKED", "unverified"), "unverified"),
                                           (None, "unverified")])
def test_a_usable_k_flux_is_stored_unchanged(two_d, status):
    _fp, e = S.adopt_entry(_sa(two_d=two_d))
    assert e["k_flux"] == KF and e["k_flux_raw"] == KF and e["k_flux_applied"] == "k_flux"
    assert e["k_flux_status"] == status


def test_the_region_mirror_keeps_the_new_keys():
    _fp, e = S.adopt_entry(_sa(two_d=_two_d("FAIL", "failed")))
    out = CM.clean_passport(e, {})
    for k in ("k_flux_raw", "k_flux_applied", "k_flux_verdict", "k_flux_status"):
        assert out[k] == e[k], k


@pytest.mark.parametrize("two_d, st", [
    (None, "unverified"), ({"verdict": "PASS"}, "verified"), ({"verdict": "INVALID"}, "invalid"),
    ({"verdict": "FAIL"}, "unverified"),                                  # old mid-plane FAIL: not a model check
    ({"verdict": "FAIL", "agreement_check": {}}, "failed"),
    ({"verdict": "PASS", "k_flux_status": "verified"}, "verified")])
def test_status_of(two_d, st):
    assert V.status_of(two_d) == st


def test_the_run_label_says_the_scalar_is_raw():
    assert "raw" in EE.k_flux_basis_label({"verdict": "FAIL", "agreement_check": {}})


# --------------------------------------------------------------------------
# 2. auto stack-length factors
# --------------------------------------------------------------------------

def test_auto_factors():
    tau = math.pi * 24.8 / 14                                             # 5.565 mm
    assert EE.auto_l_factors(12.0, tau) == (1.0, 1.5, 2.0)               # 12 mm >= 2 pole pitches
    f = EE.auto_l_factors(6.0, tau)                                       # 6 mm < 11.1 mm
    assert f[0] == 1.0 and f[1] * 6.0 == pytest.approx(2 * tau, rel=1e-6)
    assert f[3] / f[1] == pytest.approx(2.0, rel=1e-6) and len(f) == 4
    assert EE.auto_l_factors(12.0, None) == (1.0, 1.5, 2.0)


def test_run_stage_a_auto_gives_three_usable_rows(stubbed, monkeypatch):  # noqa: F811
    base = EE.spill_profile
    k12 = TABLE[REF_MM][1] * TABLE[REF_MM][0] / B1_2D
    d12 = REF_MM * (1 - k12)

    def prof(ss, n_theta=180, **kw):
        L = round(ss.stack_mm, 6)
        if L in (18.0, 24.0):
            r = 0.99
            return dict(z_m=[0.0], z_over_half=[0.0], B1_T=[r * B1_2D], phase=[0.0], profile=[1.0],
                        B1_mid_T=r * B1_2D, k_flux_self=(1 - d12 / L) / r, stack_half_m=0.5e-3 * L)
        return base(ss, n_theta=n_theta, **kw)
    monkeypatch.setattr(EE, "spill_profile", prof)
    pp = EE.run_stage_a(l_factors="auto", do_bracket=False, do_2d=True, verbose=False)
    assert [r["stack_mm"] for r in pp["l_stack_curve"]] == [12.0, 18.0, 24.0]
    chk = pp["two_d"]["agreement_check"]
    assert chk["n_rows"] == 3 and chk["span_ratio"] == pytest.approx(2.0) and chk["verdict"] == "PASS"


def test_a_string_other_than_auto_is_refused():
    with pytest.raises(ValueError, match="auto"):
        EE.run_stage_a(l_factors="1,2", do_bracket=False, do_2d=False, verbose=False)


# --------------------------------------------------------------------------
# 3. residual with n - 2 degrees of freedom
# --------------------------------------------------------------------------

def test_the_residual_uses_n_minus_2_degrees_of_freedom():
    rows = [(90.0, 0.96942381), (135.0, 0.98104254), (180.0, 0.98661238)]
    c = EE.end_deficit_check([{"stack_mm": L, "k_flux": k, "ratio_3d_mid_over_2d": 1.0} for L, k in rows], 90.0)
    D = [L * (1 - k) for L, k in rows]
    fit = [c["D0_mm"] + c["eps"] * L for L, _ in rows]
    assert c["residual_dof"] == 1
    assert c["residual_rms_mm"] == pytest.approx(math.sqrt(sum((a - b) ** 2 for a, b in zip(D, fit)) / 1), rel=1e-9)
    assert c["verdict"] == "PASS"
