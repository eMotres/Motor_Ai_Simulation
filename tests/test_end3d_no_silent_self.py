"""k_flux is never silently replaced by k_flux_self; the read-only list finds the entries where it was.

Numbers (derived only): a 12s14p 40 mm machine at a 12 mm stack (real k_flux 0.93812, k_flux_self 0.96476, mid-plane /
2-D 0.9724: the old 2 % mid-plane check said FAIL); a 28-pole 80 mm machine at 8 and 12 mm (0.88685 / 0.96100, ratio
0.9228; 0.92270 / 0.96404, ratio 0.9571).
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
GEN = "2026-11-18T13:06:16Z"           # after the 2-D source fix of 2026-08-04
OLD_CARD_TEXT = "Stage A k_flux_self at the own stack (2-D check failed, raw k_flux not used)"


def _two_d(verdict, new_rule=False, ratio=KF / KS, converged=True):
    t = {"B1_T": 1.68, "verdict": verdict, "ratio_3d_mid_over_2d": ratio, "picard_converged": converged,
         "k_flux_usable": verdict != "FAIL" and verdict != "INVALID"}
    if new_rule:
        t["agreement_check"] = {"verdict": verdict, "rows_mm": [12.0]}
    return t


# --------------------------------------------------------------------------
# the one decision
# --------------------------------------------------------------------------

@pytest.mark.parametrize("two_d, basis, verdict", [
    (_two_d("PASS"), "k_flux", "PASS"),
    (_two_d("FAIL"), "k_flux", V.VERDICT_OLD_NOT_CHECKED),          # old mid-plane FAIL, short-stack signature
    (_two_d("PASS", True), "k_flux", "PASS"),
    (_two_d("NOT CHECKED", True), "k_flux", "NOT CHECKED"),
    (_two_d("FAIL", True), "k_flux_self", "FAIL"),
    (_two_d("INVALID", ratio=None), "k_flux_self", "INVALID"),
    (None, "k_flux", "NOT CHECKED (no 2-D leg)"),
])
def test_applied_factor_by_verdict(two_d, basis, verdict):
    a = V.applied_flux_factor(KF, KS, two_d, generated_utc=GEN)
    assert a["basis"] == basis and a["verdict"] == verdict
    assert a["value"] == (KF if basis == "k_flux" else KS)
    assert a["k_flux"] == KF and a["k_flux_self"] == KS            # the real k_flux always travels
    if verdict == V.VERDICT_OLD_NOT_CHECKED:
        assert "0.9724" in a["note"]


@pytest.mark.parametrize("kf, ks, ratio", [(KF, KS, 0.9724), (0.88685, 0.96100, 0.9228), (0.92270, 0.96404, 0.9571)])
def test_measured_short_stack_old_fails_pass_the_plausibility_guard(kf, ks, ratio):
    a = V.applied_flux_factor(kf, ks, _two_d("FAIL", ratio=ratio), generated_utc=GEN)
    assert a["basis"] == "k_flux" and a["value"] == kf and a["verdict"] == V.VERDICT_OLD_NOT_CHECKED


@pytest.mark.parametrize("two_d, why", [
    (_two_d("FAIL", converged=False), "unconverged 2-D Picard (the 11 % low case)"),
    (_two_d("FAIL", converged=None), "convergence not recorded"),
    (_two_d("FAIL", ratio=0.80), "ratio below 0.85: a model fault, not a short stack"),
    (_two_d("FAIL", ratio=1.05), "3-D above 2-D"),
    (_two_d("FAIL", ratio=None), "no ratio"),
])
def test_an_implausible_old_fail_keeps_k_flux_self_and_asks_for_a_recompute(two_d, why):
    a = V.applied_flux_factor(KF, KS, two_d, generated_utc=GEN)
    assert a["basis"] == "k_flux_self" and a["value"] == KS, why
    assert a["verdict"] == V.VERDICT_OLD_RECOMPUTE and "recompute" in a["note"]


def test_an_old_fail_with_k_flux_above_k_flux_self_is_not_the_short_stack_signature():
    a = V.applied_flux_factor(0.97, 0.96, _two_d("FAIL", ratio=0.95), generated_utc=GEN)
    assert a["basis"] == "k_flux_self" and a["verdict"] == V.VERDICT_OLD_RECOMPUTE


def test_the_passport_version_marks_the_new_rule_and_invalid_has_its_own_rule_label():
    a = V.applied_flux_factor(KF, KS, {"verdict": "FAIL", "ratio_3d_mid_over_2d": 0.97, "picard_converged": True},
                              version="static3d-stageA-3")
    assert a["rule"] == V.RULE_END_DEFICIT and a["basis"] == "k_flux_self"         # a new-rule FAIL
    assert V.applied_flux_factor(KF, KS, _two_d("INVALID"))["rule"] == V.RULE_INVALID


def test_an_unknown_verdict_honours_k_flux_usable_false():
    a = V.applied_flux_factor(KF, KS, {"verdict": "WEIRD", "k_flux_usable": False})
    assert a["basis"] == "k_flux_self" and a["verdict"].startswith("UNKNOWN")
    assert V.applied_flux_factor(KF, KS, {"k_flux_usable": True})["basis"] == "k_flux"


def test_numbers_outside_the_factor_band_are_refused_and_fallbacks_say_so():
    a = V.applied_flux_factor(0.0, KS, _two_d("PASS"))
    assert a["k_flux"] is None and a["value"] == KS and "k_flux is missing" in a["note"]
    a = V.applied_flux_factor(KF, -1.0, _two_d("FAIL", True))
    assert a["k_flux_self"] is None and a["value"] == KF and "k_flux_self is missing" in a["note"]
    assert V.applied_flux_factor(None, None, None)["value"] is None


# --------------------------------------------------------------------------
# finding the old substitution
# --------------------------------------------------------------------------

def test_the_old_card_shape_is_found_by_its_text():
    old_card = {"k_flux": KS, "k_flux_vs_L": None, "fidelity": OLD_CARD_TEXT, "k_T": 0.97}
    assert "old writer text" in V.substitution_of(old_card)
    new_card = {"k_flux": KF, "k_flux_vs_L": None, "fidelity": "Stage A own stack: k_flux (2-D PASS)",
                "k_flux_applied": "k_flux", "k_flux_raw": KF, "k_flux_self": KS}
    assert V.substitution_of(new_card) is None


def test_the_old_full_passport_shape_is_found_by_its_own_stack_row():
    old_full = {"stage2_3d": {"L_mm": 12.0,
                              "k_psi": {"value": KS, "method": "...", "B1_2d_T": 1.68,
                                        "labels": ["3-D", "no-load", "quick fidelity", "k_flux_self (2-D check failed)"]},
                              "k_psi_curve": [{"stack_mm": 12.0, "k_flux_self": KS, "k_flux_raw": KF,
                                               "k_flux_usable": False, "k_psi": KS}]}}
    assert V.substitution_of(old_full).startswith("full passport")
    assert "old writer text" in V.substitution_of(old_full["stage2_3d"]["k_psi"])     # its labels


def test_store_entries_equal_by_invalid_or_no_2d_runs_are_not_hits():
    assert V.substitution_of({"k_flux": KS, "k_flux_self": KS}) is None                  # no table row
    assert V.substitution_of({"k_flux": KS, "k_flux_self": KS, "k_flux_vs_L": {"12.0": KS}}) is None
    assert V.substitution_of({"k_flux": KS, "k_flux_self": KS, "k_flux_vs_L": {"12.0": KF}}) is not None


# --------------------------------------------------------------------------
# the full-passport writer
# --------------------------------------------------------------------------

def _stage2(tmp_path, two_d, kf=KF, ks=KS, L=12.0):
    from motor_ai_sim.passport_v1.fullcard import stage2_block
    d = tmp_path / "full"
    d.mkdir()
    (d / ("stage_a_L%g.json" % L)).write_text(json.dumps({
        "k_flux": kf, "k_flux_self": ks, "two_d": two_d, "generated_utc": GEN,
        "spill_profile": {"B1_mid_T": 1.6354}, "solves": [{"picard_converged": True}]}), encoding="utf-8")
    return stage2_block(d, ROOT, L)


def test_writer_applies_k_flux_after_a_plausible_old_fail_and_says_so(tmp_path):
    kp = _stage2(tmp_path, _two_d("FAIL"))["k_psi"]
    assert kp["value"] == KF and kp["basis"] == "k_flux"
    assert kp["k_flux"] == KF and kp["k_flux_self"] == KS
    assert kp["verdict"] == V.VERDICT_OLD_NOT_CHECKED and "old mid-plane check not applied" in kp["labels"]
    assert V.substitution_of(kp) is None


def test_writer_keeps_k_flux_self_on_a_real_fail_with_the_real_k_flux_beside_it(tmp_path):
    kp = _stage2(tmp_path, _two_d("FAIL", True))["k_psi"]
    assert kp["value"] == KS and kp["basis"] == "k_flux_self" and kp["k_flux"] == KF
    assert "fail" in kp["labels"][-1] and "is FAIL" in kp["method"]


def test_writer_keeps_k_flux_self_on_an_implausible_old_fail(tmp_path):
    kp = _stage2(tmp_path, _two_d("FAIL", converged=False))["k_psi"]
    assert kp["value"] == KS and kp["verdict"] == V.VERDICT_OLD_RECOMPUTE


def test_writer_pass_is_unchanged(tmp_path):
    kp = _stage2(tmp_path, _two_d("PASS", ratio=0.995))["k_psi"]
    assert kp["value"] == KF and kp["labels"] == ["3-D", "no-load", "quick fidelity"]


@pytest.mark.parametrize("kf, ks, ratio", [(0.88685, 0.96100, 0.9228), (0.92270, 0.96404, 0.9571)])
def test_short_stack_runs_with_an_old_fail_flag_apply_their_real_k_flux(tmp_path, kf, ks, ratio):
    """Two real runs after the own-ratio fix: the old flag says FAIL / k_flux_usable False, yet k_flux is right
    (the 8 mm run's k_flux at 12 mm, 0.92271, matches the 12 mm run's 0.92270)."""
    two_d = {"B1_T": 1.7558, "verdict": "FAIL", "k_flux_usable": False, "picard_converged": True,
             "ratio_3d_mid_over_2d": ratio, "agreement_tolerance": 0.02}
    kp = _stage2(tmp_path, two_d, kf, ks, 8.0)["k_psi"]
    assert kp["value"] == kf and kp["basis"] == "k_flux" and kp["k_flux_self"] == ks
    assert kp["verdict"] == V.VERDICT_OLD_NOT_CHECKED


def test_the_card_builder_block_is_declared_and_short(tmp_path):
    spec = importlib.util.spec_from_file_location("bcc", ROOT / "scripts" / "build_catalog_card_from_passport.py")
    src = spec.loader.get_data(str(ROOT / "scripts" / "build_catalog_card_from_passport.py")).decode()
    assert '"k_flux_applied": basis' in src and "[:80]" in src
    assert ("Stage A own stack: %s (2-D %s)" % ("k_flux_self", V.VERDICT_OLD_RECOMPUTE))[:80] == \
        "Stage A own stack: k_flux_self (2-D NOT CHECKED (old rule, recompute))"


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


def test_list_finds_every_old_shape_and_writes_nothing(tmp_path, capsys):
    store = tmp_path / "end_effect_passports.json"
    store.write_text(json.dumps({
        "aaa": {"k_flux": KS, "k_flux_self": KS, "k_flux_vs_L": {"12.0": KF}, "machine": "12s/14p OD 40",
                "stack_mm": 12.0},
        "invalid": {"k_flux": 0.95, "k_flux_self": 0.95, "machine": "x"},
        "ok": {"k_flux": 0.96269, "k_flux_self": 0.96963, "k_flux_vs_L": {"20.0": 0.96269}}}), encoding="utf-8")
    die = tmp_path / "root" / "DIE A"                                     # a bundle-style root (no dies/ level)
    die.mkdir(parents=True)
    (die / "die.yaml").write_text("geometry: {motor_length: 12}\n", encoding="utf-8")
    (die / "L12.yaml").write_text(
        "geometry: {motor_length: 12}\nduties:\n- summary:\n    end3d: {k_flux: %r, k_flux_self: %r, "
        "k_flux_vs_L: {'12.0': %r}}\n" % (KS, KS, KF), encoding="utf-8")
    cat = tmp_path / "motor_catalog.json"
    cat.write_text(json.dumps({"motors": [{"id": "m1", "passport": {"passport": {
        "L0_mm": 12.0, "end3d": {"k_flux": KS, "k_flux_vs_L": None, "fidelity": OLD_CARD_TEXT, "k_T": 0.97}}}}]}),
        encoding="utf-8")
    full = tmp_path / "passport_L12.json"
    full.write_text(json.dumps({"stage2_3d": {"L_mm": 12.0, "k_psi": {"value": KS, "labels": ["3-D"]},
                                              "k_psi_curve": [{"stack_mm": 12.0, "k_flux_self": KS,
                                                               "k_flux_raw": KF}]}}), encoding="utf-8")
    before = _digest(tmp_path)
    m = _script()
    assert m.main(["--store", str(store), "--root", str(tmp_path / "root"), "--catalog", str(cat),
                   "--full", str(full)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert _digest(tmp_path) == before                                  # read-only
    assert out["read_only"] is True and out["errors"] == []
    by = {h["source"]: h for h in out["substituted"]}
    assert set(by) == {"store", "catalog", "motor_catalog", "full_passport"}
    for src in ("store", "catalog", "full_passport"):
        h = by[src]
        assert h["applied_k_flux"] == KS and h["real_k_flux_own_stack"] == KF
        assert h["applied_over_real_pct"] == pytest.approx(100 * (KS / KF - 1), abs=1e-3)   # +2.84 %
        assert h["verdict_old_mid_plane_rule"] == "FAIL" and h["verdict_end_deficit_rule"] == "not evaluated"
    assert "old writer text" in by["motor_catalog"]["found_by"] and by["motor_catalog"]["real_k_flux_own_stack"] is None
    assert [e["key"] for e in out["equal_not_substitution"]] == ["invalid"]


def test_list_needs_an_explicit_source():
    with pytest.raises(SystemExit):
        _script().main([])



# --------------------------------------------------------------------------
# follow-up of the #878 review (2026-11-25)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("gen", [None, "", "2026-08-03T13:32:51Z"])
def test_an_old_fail_before_the_source_fix_or_undated_keeps_k_flux_self(gen):
    a = V.applied_flux_factor(KF, KS, _two_d("FAIL"), generated_utc=gen)
    assert a["basis"] == "k_flux_self" and a["verdict"] == V.VERDICT_OLD_RECOMPUTE and "2026-08-04" in a["note"]
    assert V.applied_flux_factor(KF, KS, _two_d("FAIL"), generated_utc="2026-08-05T00:00:00Z")["basis"] == "k_flux"


def test_a_text_hit_on_an_old_card_asks_to_verify_against_the_full_passport():
    how = V.substitution_of({"k_flux": KS, "k_flux_vs_L": None, "fidelity": OLD_CARD_TEXT, "labels": 7})
    assert "verify against the full passport" in how


def test_an_inherited_table_is_not_read_at_the_configurations_own_length():
    # lent from a passport measured at 20 mm: its 12 mm row is a sweep row, not this configuration's own stack
    e = {"k_flux": KS, "k_flux_self": KS, "k_flux_vs_L": {"12.0": KF, "20.0": KS}, "inherited": True}
    assert V.substitution_of(e, stack_mm=12.0) is None
    assert V.substitution_of(dict(e, inherited=False), stack_mm=12.0) is not None


def test_broken_full_passport_parts_do_not_raise():
    assert V.substitution_of({"stage2_3d": {"k_psi": 3, "k_psi_curve": [1, None, {"stack_mm": "x"}]}}) is None


def test_the_writer_says_why_a_pass_run_gets_k_flux_self(tmp_path):
    kp = _stage2(tmp_path, _two_d("PASS", ratio=0.995), kf=1.7)["k_psi"]  # k_flux outside the factor band
    assert kp["value"] == KS and kp["basis"] == "k_flux_self"
    assert "not a usable factor" in kp["method"] and "as the run itself instructs" not in kp["method"]


def test_the_list_survives_broken_records_and_reads_the_right_stack(tmp_path, capsys):
    store = tmp_path / "s.json"
    store.write_text(json.dumps({"bad": {"k_flux": KS, "stage2_3d": {"k_psi": "x"}},
                                 "nan": {"k_flux": float("nan"), "k_flux_self": float("nan")}}), encoding="utf-8")
    cat = tmp_path / "motor_catalog.json"                # L0_mm sits in the passport, not in its end3d block
    cat.write_text(json.dumps({"motors": [{"passport": {"passport": {
        "L0_mm": 12.0, "end3d": {"k_flux": KS, "k_flux_self": KS, "k_flux_vs_L": {"12.0": KF}}}}}]}), encoding="utf-8")
    full = tmp_path / "full"
    full.mkdir()
    (full / "passport_L12.json").write_text(json.dumps({"stage2_3d": {"L_mm": 12.0, "k_psi": {"value": KS},
                                            "k_psi_curve": [{"stack_mm": 12.0, "k_flux_raw": KF}]}}), encoding="utf-8")
    (full / "stage_a_L12.json").write_text(json.dumps({"k_flux": KS, "k_flux_self": KS,
                                                       "k_flux_vs_L": {"12.0": KF}}), encoding="utf-8")
    m = _script()
    assert m.main(["--store", str(store), "--catalog", str(cat), "--full", str(full)]) == 0
    out = json.loads(capsys.readouterr().out)
    src = sorted(h["source"] for h in out["substituted"])
    assert src == ["full_passport", "motor_catalog"]                      # stage_a_L12.json is not read by --full
    assert next(h for h in out["substituted"] if h["source"] == "motor_catalog")["stack_mm"] == 12.0
    assert all("nan" not in json.dumps(h).lower() for h in out["substituted"])
    assert m._num(float("nan")) is None and m._num(float("inf")) is None
