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
          "l_stack_curve": [{"stack_mm": 12.0, "k_flux": KF, "k_flux_self": KS},
                            {"stack_mm": 18.0, "k_flux": 0.95600, "k_flux_self": 0.97300}],
          "k_flux_vs_L_method": "own_mid_ratio"}
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
    # the table is what is APPLIED: k_flux_self rows, the raw rows beside it
    assert e["k_flux_vs_L"] == {"12.0": KS, "18.0": 0.973} and e["k_flux_vs_L_method"] == "self_applied"
    assert e["k_flux_vs_L_raw"] == {"12.0": KF, "18.0": 0.956}


@pytest.mark.parametrize("two_d, status", [(_two_d("PASS", "verified"), "verified"),
                                           (_two_d("NOT CHECKED", "unverified"), "unverified"),
                                           (None, "unverified")])
def test_a_usable_k_flux_is_stored_unchanged(two_d, status):
    _fp, e = S.adopt_entry(_sa(two_d=two_d))
    assert e["k_flux"] == KF and e["k_flux_raw"] == KF and e["k_flux_applied"] == "k_flux"
    assert e["k_flux_status"] == status
    assert e["k_flux_vs_L"] == {"12.0": KF, "18.0": 0.956} and e["k_flux_vs_L_method"] == "own_mid_ratio"
    assert "k_flux_vs_L_raw" not in e


def test_the_region_mirror_keeps_the_new_keys():
    _fp, e = S.adopt_entry(_sa(two_d=_two_d("FAIL", "failed")))
    out = CM.clean_passport(e, {})
    for k in ("k_flux_raw", "k_flux_applied", "k_flux_verdict", "k_flux_status", "k_flux_vs_L_raw"):
        assert out[k] == e[k], k


@pytest.mark.parametrize("two_d, st", [
    (None, "unverified"), ({"verdict": "PASS"}, "unverified"),           # an old mid-plane PASS is not the new check
    ({"verdict": "INVALID"}, "invalid"),
    ({"verdict": "FAIL"}, "unverified"),                                  # old mid-plane FAIL: not a model check
    ({"verdict": "FAIL", "agreement_check": {}}, "failed"),
    ({"verdict": "PASS", "agreement_check": {}}, "verified"),
    ({"verdict": "PASS", "k_flux_status": "verified"}, "unverified")])    # the run's own word is not taken
def test_status_of(two_d, st):
    assert V.status_of(two_d) == st


@pytest.mark.parametrize("args, st", [
    ((KF, KS, {"verdict": "PASS", "agreement_check": {}}), "verified"),
    ((KF, KS, {"verdict": "PASS"}, "static3d-stageA-3"), "verified"),    # version >= 3 without agreement_check
    ((KF, KS, {"verdict": "PASS"}), "unverified"),                        # old-rule PASS
    ((1.7, KS, {"verdict": "PASS", "agreement_check": {}}), "fallback"),  # PASS forced to k_flux_self: not verified
    ((KF, None, {"verdict": "FAIL", "agreement_check": {}}), "failed"),    # FAIL even when k_flux_self is missing
    ((KF, KS, {"verdict": "FAIL", "agreement_check": {}}), "failed"),
    ((KF, KS, {"verdict": "FAIL", "ratio_3d_mid_over_2d": 0.80, "picard_converged": True}), "recompute"),
    ((KF, KS, {"verdict": "INVALID"}), "invalid"),
    ((KF, KS, None), "unverified")])
def test_status_follows_what_is_applied(args, st):
    af = V.applied_flux_factor(*args[:3], version=(args[3] if len(args) > 3 else None),
                               generated_utc="2026-11-25T10:00:00Z")
    assert V.status_of(args[2], af) == st


def test_the_run_label_says_the_scalar_is_raw():
    assert "raw" in EE.k_flux_basis_label({"verdict": "FAIL", "agreement_check": {}})


# --------------------------------------------------------------------------
# 2. auto stack-length factors
# --------------------------------------------------------------------------

def test_auto_factors():
    tau = math.pi * 24.8 / 14                                             # 5.565 mm
    assert EE.auto_l_factors(12.0, tau) == (1.0, 1.5, 2.0)               # 12 mm >= 2 pole pitches
    f = EE.auto_l_factors(6.0, tau)                                       # 6 mm < 11.1 mm
    assert f[0] == 1.0 and f[1] == 2 * tau / 6.0                         # not rounded
    assert f[3] / f[1] == pytest.approx(2.0, rel=1e-12) and len(f) == 4
    assert EE.auto_l_factors(12.0, None) == (1.0, 1.5, 2.0)
    assert EE.auto_l_factors(200.0, 10.0) == (1.0, 1.25, 1.5)            # long stack: the longest model is 1.5 x
    assert EE.auto_l_factors(11.0, tau)[1] == pytest.approx(1.05)        # f0 never closer than 1.05


def test_auto_factors_always_give_three_fitted_rows():
    import random
    rnd = random.Random(20261125)
    for _ in range(2000):
        L, tau = rnd.uniform(2.0, 300.0), rnd.uniform(1.0, 40.0)
        curve = [{"stack_mm": float(f) * L, "k_flux": 1 - 0.9 / (float(f) * L), "ratio_3d_mid_over_2d": 1.0}
                 for f in EE.auto_l_factors(L, tau)]
        c = EE.end_deficit_check(curve, L, pole_pitch_mm=tau)
        assert c["n_rows"] == 3 and c["span_ratio"] >= 1.5 - 1e-9, (L, tau, c["rows_mm"], c["dropped_rows"])


def test_run_stage_a_auto_gives_three_usable_rows(stubbed, monkeypatch):  # noqa: F811
    base = EE.spill_profile
    k12 = TABLE[REF_MM][1] * TABLE[REF_MM][0] / B1_2D
    d12 = REF_MM * (1 - k12)

    def prof(ss, n_theta=180, **kw):
        L = round(ss.stack_mm, 6)
        if L in (9.0, 18.0, 24.0):
            r = 0.99
            return dict(z_m=[0.0], z_over_half=[0.0], B1_T=[r * B1_2D], phase=[0.0], profile=[1.0],
                        B1_mid_T=r * B1_2D, k_flux_self=(1 - d12 / L) / r, stack_half_m=0.5e-3 * L)
        return base(ss, n_theta=n_theta, **kw)
    monkeypatch.setattr(EE, "spill_profile", prof)
    pp = EE.run_stage_a(l_factors="auto", do_bracket=False, do_2d=True, verbose=False)
    assert [r["stack_mm"] for r in pp["l_stack_curve"]] == [12.0, 18.0, 24.0]
    pp = EE.run_stage_a(l_factors="auto", auto_extra=(0.75,), do_bracket=False, do_2d=True, verbose=False)
    assert [r["stack_mm"] for r in pp["l_stack_curve"]] == [12.0, 18.0, 24.0, 9.0]       # the catalog's row below L0
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



# --------------------------------------------------------------------------
# blocker 1: every reader of a FAIL entry agrees with its scalar
# --------------------------------------------------------------------------

def test_readers_of_a_failed_entry_agree_with_its_k_flux_self_scalar():
    from motor_ai_sim.charge_analytic import _k_end
    from motor_ai_sim.end3d_factors import k_flux_table
    from motor_ai_sim.routes.simulation import _end3d_rec_points
    _fp, e = S.adopt_entry(_sa(two_d=_two_d("FAIL", "failed")))
    assert e["k_flux"] == KS
    # charge_analytic: the scalar
    assert _k_end({"end3d": e, "L0_mm": 12.0}) == pytest.approx(KS)
    # datasheet calculator and Configure read k_flux_table / its twin: the self rows, not stale, reference row = scalar
    rows, stale = k_flux_table(e)
    assert stale is False and rows[12.0] == pytest.approx(KS, abs=5e-6) and rows[18.0] == 0.973
    # lending: the points are the applied (self) rows, and the own-stack scalar agrees with its row
    pts = dict(_end3d_rec_points(e))
    assert pts[12.0] == pytest.approx(KS, abs=5e-6) and pts[18.0] == 0.973


def test_the_datasheet_calculator_reads_the_self_rows(tmp_path):
    import json
    from pathlib import Path
    fx = Path(__file__).resolve().parents[1] / "web/src/lib/__tests__/fixtures/live_l12_reference_cards.json"
    if not fx.is_file():
        pytest.skip("web fixture not present in this checkout")
    from openpyxl import Workbook
    from motor_ai_sim.datasheet import _build_calculator
    _fp, e = S.adopt_entry(_sa(two_d=_two_d("FAIL", "failed")))
    pp = dict(json.loads(fx.read_text(encoding="utf-8"))["cat_ciano14_40_new"]["passport"]["passport"])
    pp["end3d"] = e
    wb = Workbook()
    _build_calculator(wb, pp=pp, geo={"num_poles": 14}, wind={}, duties=[])
    rows = [[c.value for c in r] for ws in wb.worksheets for r in ws.iter_rows()]
    kk = next(r for r in rows if r and r[0] == "k_flux")
    assert [v for v in kk[1:] if isinstance(v, (int, float))] == pytest.approx([KS, 0.973], abs=1e-4)


def test_a_lent_factor_does_not_carry_the_sources_status(monkeypatch):
    from motor_ai_sim import end3d_store
    from motor_ai_sim.routes import simulation as SIM
    _fp, e = S.adopt_entry(_sa(two_d=_two_d("PASS", "verified")))
    monkeypatch.setattr(end3d_store, "load", lambda: {"src": e})
    monkeypatch.setattr(SIM, "_machine_desc", lambda geo=None: e["machine"])
    monkeypatch.setattr(SIM, "_geo_stack_mm", lambda geo=None: 15.0)
    r = SIM._end3d_lookup("other-geometry", {})
    assert r["inherited"] is True and r["k_flux_status"] == "unverified"
    for k in ("k_flux_applied", "k_flux_raw", "k_flux_verdict", "k_flux_vs_L_raw"):
        assert k not in r



# --------------------------------------------------------------------------
# follow-ups of the #884 review
# --------------------------------------------------------------------------

def test_the_statuses_are_the_documented_set():
    assert V.STATUSES == ("verified", "unverified", "failed", "invalid", "fallback", "recompute")


def test_charge_analytic_beyond_five_percent_reads_the_applied_rows_without_a_jump():
    from motor_ai_sim.charge_analytic import k_end_info
    _fp, e = S.adopt_entry(_sa(two_d=_two_d("FAIL", "failed")))
    inside, _ = k_end_info({"end3d": e, "L0_mm": 12.6})                  # within 5 % of the own 12 mm: the scalar
    beyond, _ = k_end_info({"end3d": e, "L0_mm": 12.7})                  # beyond: the (self) table at 12.7
    assert inside == pytest.approx(KS)
    assert beyond == pytest.approx(KS + (0.973 - KS) * 0.7 / 6.0, abs=1e-6)
    assert abs(beyond - inside) < 1e-3                                    # no jump by the k_flux / k_flux_self gap


def test_the_catalog_store_fields():
    sa = _sa(two_d=_two_d("FAIL", "failed"))
    f = V.store_fields(sa)
    assert f["k_flux"] == KS and f["k_flux_raw"] == KF and f["k_flux_applied"] == "k_flux_self"
    assert f["k_flux_status"] == "failed" and f["k_flux_vs_L_method"] == "self_applied"
    assert f["k_flux_vs_L"] == {"12.0": KS, "18.0": 0.973} and f["k_flux_vs_L_raw"] == {"12.0": KF, "18.0": 0.956}
    g = V.store_fields(_sa(two_d=_two_d("PASS", "verified")))
    assert g["k_flux"] == KF and g["k_flux_status"] == "verified" and "k_flux_vs_L_raw" not in g
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src/motor_ai_sim/routes/catalog.py").read_text(encoding="utf-8")
    assert "return store_fields(sa)" in src                               # the catalogue passport uses it


def test_a_lent_factor_drops_the_basis_label_and_says_when_bases_mix(monkeypatch):
    from motor_ai_sim import end3d_store
    from motor_ai_sim.routes import simulation as SIM
    _fp, ok = S.adopt_entry(_sa(two_d=_two_d("PASS", "verified"), k_flux_basis="2-D check passed"))
    _fp, bad = S.adopt_entry(_sa(two_d=_two_d("FAIL", "failed"), geometry_fingerprint="other0000001",
                                 machine={"num_slots": 12, "num_poles": 14, "stator_od_mm": 40.0, "stack_mm": 30.0},
                                 l_stack_curve=[{"stack_mm": 30.0, "k_flux": 0.95, "k_flux_self": 0.975}]))
    monkeypatch.setattr(SIM, "_machine_desc", lambda geo=None: ok["machine"])
    monkeypatch.setattr(SIM, "_geo_stack_mm", lambda geo=None: 15.0)
    monkeypatch.setattr(end3d_store, "load", lambda: {"a": ok})
    r = SIM._end3d_lookup("g", {})
    assert "k_flux_basis" not in r and "mixes passports" not in r["inherited_note"]
    monkeypatch.setattr(end3d_store, "load", lambda: {"a": ok, "b": bad})
    r = SIM._end3d_lookup("g", {})
    assert "k_flux_basis" not in r and r["k_flux_status"] == "unverified"
    assert "mixes passports applied on different bases (k_flux, k_flux_self)" in r["inherited_note"]
