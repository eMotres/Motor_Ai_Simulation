"""k_flux is never silently replaced by k_flux_self; the read-only list finds the entries where it was.

Numbers: a 12s14p 40 mm machine at a 12 mm stack (real k_flux 0.93812, k_flux_self 0.96476, mid-plane / 2-D 0.9724:
the old 2 % mid-plane check said FAIL), derived values only.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from motor_ai_sim import end3d_verdict as V

ROOT = Path(__file__).resolve().parents[1]
KF, KS = 0.93812, 0.96476


def _two_d(verdict, new_rule=False, ratio=KF / KS):
    t = {"B1_T": 1.68, "verdict": verdict, "ratio_3d_mid_over_2d": ratio,
         "k_flux_usable": verdict == "PASS"}
    if new_rule:
        t["agreement_check"] = {"verdict": verdict, "rows_mm": [12.0]}
    return t


# --------------------------------------------------------------------------
# the one decision
# --------------------------------------------------------------------------

@pytest.mark.parametrize("two_d, basis, verdict", [
    (_two_d("PASS"), "k_flux", "PASS"),
    (_two_d("FAIL"), "k_flux", V.VERDICT_OLD_NOT_CHECKED),          # old mid-plane FAIL: end physics
    (_two_d("PASS", True), "k_flux", "PASS"),
    (_two_d("NOT CHECKED", True), "k_flux", "NOT CHECKED"),
    (_two_d("FAIL", True), "k_flux_self", "FAIL"),
    (_two_d("INVALID", ratio=None), "k_flux_self", "INVALID"),
    (None, "k_flux", "NOT CHECKED (no 2-D leg)"),
])
def test_applied_factor_by_verdict(two_d, basis, verdict):
    a = V.applied_flux_factor(KF, KS, two_d)
    assert a["basis"] == basis and a["verdict"] == verdict
    assert a["value"] == (KF if basis == "k_flux" else KS)
    assert a["k_flux"] == KF and a["k_flux_self"] == KS            # the real k_flux always travels
    if verdict == V.VERDICT_OLD_NOT_CHECKED:
        assert "0.9724" in a["note"]


def test_missing_numbers_fall_back_without_inventing_one():
    assert V.applied_flux_factor(None, KS, _two_d("PASS"))["value"] == KS
    assert V.applied_flux_factor(KF, None, _two_d("FAIL", True))["value"] == KF
    assert V.applied_flux_factor(None, None, None)["value"] is None


def test_substitution_mark():
    assert V.is_silent_substitution({"k_flux": KS, "k_flux_self": KS})
    assert not V.is_silent_substitution({"k_flux": KF, "k_flux_self": KS})
    assert not V.is_silent_substitution({"k_flux": KS, "k_flux_self": KS, "k_flux_basis": "k_flux_self"})
    assert not V.is_silent_substitution(None)


# --------------------------------------------------------------------------
# the full-passport writer
# --------------------------------------------------------------------------

def _stage2(tmp_path, two_d):
    from motor_ai_sim.passport_v1.fullcard import stage2_block
    d = tmp_path / "full"
    d.mkdir()
    (d / "stage_a_L12.json").write_text(json.dumps({
        "k_flux": KF, "k_flux_self": KS, "two_d": two_d,
        "spill_profile": {"B1_mid_T": 1.6354}, "solves": [{"picard_converged": True}]}), encoding="utf-8")
    return stage2_block(d, ROOT, 12.0)


def test_writer_applies_k_flux_after_an_old_mid_plane_fail_and_says_so(tmp_path):
    kp = _stage2(tmp_path, _two_d("FAIL"))["k_psi"]
    assert kp["value"] == KF and kp["basis"] == "k_flux"
    assert kp["k_flux"] == KF and kp["k_flux_self"] == KS
    assert kp["verdict"] == V.VERDICT_OLD_NOT_CHECKED and "old mid-plane check not applied" in kp["labels"]


def test_writer_keeps_k_flux_self_on_a_real_fail_with_the_real_k_flux_beside_it(tmp_path):
    kp = _stage2(tmp_path, _two_d("FAIL", True))["k_psi"]
    assert kp["value"] == KS and kp["basis"] == "k_flux_self" and kp["k_flux"] == KF
    assert "fail" in kp["labels"][-1] and "is FAIL" in kp["method"]


def test_writer_pass_is_unchanged(tmp_path):
    kp = _stage2(tmp_path, _two_d("PASS", ratio=0.995))["k_psi"]
    assert kp["value"] == KF and kp["labels"] == ["3-D", "no-load", "quick fidelity"]


# --------------------------------------------------------------------------
# the read-only list
# --------------------------------------------------------------------------

def _script():
    spec = importlib.util.spec_from_file_location("e3sub", ROOT / "scripts" / "end3d_substitution_list.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _digest(p: Path) -> str:
    return hashlib.sha256(b"".join(f.read_bytes() for f in sorted(p.rglob("*")) if f.is_file())).hexdigest()


def test_list_finds_the_substitution_and_writes_nothing(tmp_path, capsys):
    store = tmp_path / "end_effect_passports.json"
    store.write_text(json.dumps({
        "aaa": {"k_flux": KS, "k_flux_self": KS, "k_flux_vs_L": {"12.0": KF}, "machine": "12s/14p OD 40",
                "stack_mm": 12.0},
        "bbb": {"k_flux": 0.96269, "k_flux_self": 0.96963, "k_flux_vs_L": {"20.0": 0.96269}}}), encoding="utf-8")
    die = tmp_path / "root" / "dies" / "DIE A"
    die.mkdir(parents=True)
    (die / "die.yaml").write_text("geometry: {motor_length: 12}\n", encoding="utf-8")
    (die / "L12.yaml").write_text(
        "geometry: {motor_length: 12}\nduties:\n- summary:\n    end3d: {k_flux: %r, k_flux_self: %r, "
        "k_flux_vs_L: {'12.0': %r}}\n" % (KS, KS, KF), encoding="utf-8")
    before = _digest(tmp_path)
    m = _script()
    assert m.main(["--store", str(store), "--root", str(tmp_path / "root")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert _digest(tmp_path) == before                                  # read-only
    assert out["read_only"] is True and out["errors"] == []
    keys = sorted(h["source"] for h in out["substituted"])
    assert keys == ["catalog", "store"]
    for h in out["substituted"]:
        assert h["applied_k_flux"] == KS and h["real_k_flux_own_stack"] == KF
        assert h["applied_over_real_pct"] == pytest.approx(100 * (KS / KF - 1), abs=1e-3)   # +2.84 %
        assert h["verdict_old_mid_plane_rule"] == "FAIL" and h["verdict_end_deficit_rule"] == "NOT CHECKED"
    assert len(out["unique"]) == 1 and len(out["unique"][0]["carried_by"]) == 2


def test_list_needs_an_explicit_source():
    with pytest.raises(SystemExit):
        _script().main([])


@pytest.mark.parametrize("kf, ks, ratio", [(0.88685, 0.96100, 0.9228),    # 28-pole 80 mm, 8 mm reference
                                           (0.92270, 0.96404, 0.9571)])   # same machine, 12 mm reference
def test_short_stack_runs_with_an_old_fail_flag_apply_their_real_k_flux(tmp_path, kf, ks, ratio):
    """Two real runs after the own-ratio fix: the old flag says FAIL / k_flux_usable False on both, yet k_flux is right
    (the 8 mm run's k_flux at 12 mm, 0.92271, matches the 12 mm run's 0.92270).  The card must carry k_flux, not
    k_flux_self (about 8 % on T and V at 8 mm)."""
    from motor_ai_sim.passport_v1.fullcard import stage2_block
    d = tmp_path / "full"
    d.mkdir()
    (d / "stage_a_L8.json").write_text(json.dumps({
        "k_flux": kf, "k_flux_self": ks, "two_d": {"B1_T": 1.7558, "verdict": "FAIL", "k_flux_usable": False,
                                                    "ratio_3d_mid_over_2d": ratio, "agreement_tolerance": 0.02},
        "spill_profile": {"B1_mid_T": 1.62}, "solves": [{"picard_converged": True}]}), encoding="utf-8")
    kp = stage2_block(d, ROOT, 8.0)["k_psi"]
    assert kp["value"] == kf and kp["basis"] == "k_flux" and kp["k_flux_self"] == ks
    assert kp["verdict"] == V.VERDICT_OLD_NOT_CHECKED
