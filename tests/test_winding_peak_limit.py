"""A separate PEAK winding limit per configuration (owner 2026-10-27).

``limits.winding_peak_c`` is what a SHORT pull (the S2 time-to-limit, a single S2 cycle, the "peak held" requirement) is
judged against; every continuous verdict keeps ``limits.winding_c``.  Pure builders on synthetic records - no FEM, no
store, no server; the numbers are made up.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.test_customer_report import EM_CONT, EM_PEAK, REQ, _col, _rows_by_label

BOTH = {"limits": {"winding_c": 180, "winding_peak_c": 200}}


# ---------------------------------------------------------------------------
# the readers
# ---------------------------------------------------------------------------

def test_the_peak_limit_falls_back_to_the_continuous_one_and_never_raises():
    from motor_ai_sim import winding_limit as wl

    assert wl.config_winding_limit_c(BOTH) == 180.0
    assert wl.config_winding_peak_limit_c(BOTH) == 200.0
    # one limit only: the peak limit IS the continuous one
    assert wl.config_winding_peak_limit_c({"limits": {"winding_c": 180}}) == 180.0
    # a peak limit alone is a peak limit, the continuous stays unstated
    only_peak = {"limits": {"winding_peak_c": 200}}
    assert wl.config_winding_peak_limit_c(only_peak) == 200.0 and wl.config_winding_limit_c(only_peak) is None
    # nothing, garbage, an out-of-window number: None (= the project defaults), never an exception
    for bad in (None, {}, "x", {"limits": None}, {"limits": {"winding_peak_c": "hot"}},
                {"limits": {"winding_peak_c": 9999}}):
        assert wl.config_winding_peak_limit_c(bad) is None
    # an invalid peak value falls back to the continuous limit
    assert wl.config_winding_peak_limit_c({"limits": {"winding_c": 180, "winding_peak_c": 9999}}) == 180.0


def test_validate_names_what_it_checked_and_basis_words_name_the_peak():
    from motor_ai_sim import winding_limit as wl

    assert wl.validate(None, "peak winding limit") == (None, None)
    assert wl.validate(200, "peak winding limit") == (200.0, None)
    v, why = wl.validate(5, "peak winding limit")
    assert v is None and why.startswith("peak winding limit must be")
    assert wl.validate(5)[1].startswith("winding limit must be")
    assert wl.basis_words(180.0) == "the project's winding limit (180 °C)"
    assert wl.basis_words(200.0, peak=True) == "the project's peak winding limit (200 °C)"


def test_the_solved_configuration_peak_limit_is_read_once_per_file_change(monkeypatch, tmp_path):
    import yaml
    from motor_ai_sim import winding_limit as wl
    from motor_ai_sim.routes import family as fam

    f = tmp_path / "C.yaml"
    f.write_text("limits:\n  winding_c: 180\n  winding_peak_c: 200\n", encoding="utf-8")
    reads = []

    def _doc(die, cfg):
        reads.append((die, cfg))
        return yaml.safe_load(f.read_text(encoding="utf-8"))

    monkeypatch.setattr(fam, "_cfg_file", lambda die, cfg: f)
    monkeypatch.setattr(fam, "config_doc", _doc)
    wl._CACHE.clear()
    assert wl.for_config("D", "C") == 180.0
    assert wl.peak_for_config("D", "C") == 200.0
    assert wl.peak_for_solve({"die": "D", "config": "C"}) == 200.0
    assert len(reads) == 1                      # both limits come from one cached read
    assert wl.peak_for_config("", "") is None
    wl._CACHE.clear()


# ---------------------------------------------------------------------------
# PATCH /config/{die}/{cfg}/limits
# ---------------------------------------------------------------------------

@pytest.fixture
def patch_env(monkeypatch):
    from motor_ai_sim.routes import family as fam

    store = {"doc": {"name": "C", "limits": {"winding_c": 180.0}}}
    monkeypatch.setattr(fam, "_require_die_write", lambda *a, **k: None)
    monkeypatch.setattr(fam, "_cfg_file", lambda die, cfg: "unused.yaml")
    monkeypatch.setattr(fam, "_load_yaml", lambda p, what: store["doc"])
    monkeypatch.setattr(fam, "_save_yaml", lambda p, d: store.update(doc=d))
    return fam, store


def _patch(fam, **body):
    return fam.set_limits("D", "C", fam.LimitsPatch(**body), {})


def test_patch_leaves_an_unsent_field_unchanged_and_an_explicit_null_clears(patch_env):
    fam, store = patch_env
    # the existing caller: only winding_c
    assert _patch(fam, winding_c=170)["limits"] == {"winding_c": 170.0}
    # only the peak: winding_c is NOT touched, the full limits dict comes back
    out = _patch(fam, winding_peak_c=200)
    assert out["limits"] == {"winding_c": 170.0, "winding_peak_c": 200.0}
    assert store["doc"]["limits"] == out["limits"]
    # winding_c alone again: the peak stays
    assert _patch(fam, winding_c=180)["limits"] == {"winding_c": 180.0, "winding_peak_c": 200.0}
    # an explicit null clears just that field
    assert _patch(fam, winding_peak_c=None)["limits"] == {"winding_c": 180.0}
    assert _patch(fam, winding_c=None)["ok"] is True
    assert "limits" not in store["doc"]


def test_patch_refuses_a_nonsense_or_inverted_peak_limit(patch_env):
    from fastapi import HTTPException

    fam, store = patch_env
    _patch(fam, winding_c=180)
    with pytest.raises(HTTPException) as ei:
        _patch(fam, winding_peak_c=5)
    assert ei.value.status_code == 422 and "peak winding limit" in ei.value.detail
    with pytest.raises(HTTPException) as ei:
        _patch(fam, winding_peak_c=170)
    assert ei.value.status_code == 422 and "must not be below" in ei.value.detail
    assert store["doc"]["limits"] == {"winding_c": 180.0}


# ---------------------------------------------------------------------------
# the duty cycle: a single S2 pull is a peak, an S3 keeps the continuous limit
# ---------------------------------------------------------------------------

def test_a_cycle_model_judges_s2_at_the_peak_limit_and_s3_pulls_at_the_peak_limit():
    from motor_ai_sim import coupled_duty_cycle as cdc
    from motor_ai_sim import thermal_duty_cycle as tdc

    pull = tdc.default_limits(150.0, 200.0)
    s2 = SimpleNamespace(kind="S2", limits=pull, pull_limits=pull, hot_spot_offset_k=3.0)
    s3 = SimpleNamespace(kind="S3", limits=tdc.default_limits(150.0, 180.0), pull_limits=pull, hot_spot_offset_k=3.0)
    for m, cycle_lim in ((s2, 200.0), (s3, 180.0)):
        blk = cdc._limits_block(m, {})
        assert blk["winding_limit_c"] == cycle_lim and blk["winding_peak_limit_c"] == 200.0
    assert cdc._limit_words(s3, "winding") == "180 °C" and cdc._limit_words(s3, "winding", pull=True) == "200 °C"


def test_the_stored_s2_limit_is_the_one_the_requirements_check_compares():
    from motor_ai_sim import report_customer as rc

    col = _col("peak", EM_PEAK, {"duty_cycle": {"limits": {
        "s2_time_to_limit_s": 70.0, "winding_limit_c": 180.0, "winding_peak_limit_c": 200.0}}})
    assert rc._s2_time(col)[:2] == (70.0, 200.0)
    # a record from before the peak limit existed keeps its single winding limit
    old = _col("peak", EM_PEAK, {"duty_cycle": {"limits": {"s2_time_to_limit_s": 70.0, "winding_limit_c": 180.0}}})
    assert rc._s2_time(old)[:2] == (70.0, 180.0)


# ---------------------------------------------------------------------------
# the customer report: "peak held" at the peak limit, the S1 rows at the continuous one
# ---------------------------------------------------------------------------

def test_the_peak_duration_row_is_judged_at_the_peak_limit_s1_at_the_continuous_one():
    from motor_ai_sim import report_customer as rc

    s1 = {"continuous_rating": {"I_cont_A_rms": 110.0, "verified": True, "limits_c": {"winding": 180.0},
                                "power": {"T_em_Nm": 104.0}}}
    dc = {"limits": {"s2_time_to_limit_s": 75.0, "winding_limit_c": 200.0, "winding_peak_limit_c": 200.0}}
    cols = [_col("cont", dict(EM_CONT, T_em_avg_Nm=104.0), {"coupled_limit": s1}),
            _col("peak", EM_PEAK, {"duty_cycle": dc})]
    by = _rows_by_label(rc.requirements_rows(REQ, cols, winding_limit_c=180.0, winding_peak_limit_c=200.0))
    assert by["100 N·m held continuously, winding ≤ 180 °C"][3] == "met"
    held = by["Peak held 60 s, winding ≤ 200 °C"]
    assert held[1] == "75 s to 200 °C" and held[3] == "met"
    # judged at the continuous limit the same record would be "not computed at 180 °C": no peak limit, no change
    by0 = _rows_by_label(rc.requirements_rows(REQ, cols, winding_limit_c=180.0))
    assert by0["Peak held 60 s, winding ≤ 180 °C"][3] == "not computed at 180 °C"


# ---------------------------------------------------------------------------
# the report's per-duty verdicts
# ---------------------------------------------------------------------------

def _ctx(col, peak=200.0):
    from motor_ai_sim import report as R

    kw = {"ins_peak_lim": peak, "ins_peak_note": "the project's peak winding limit (%g °C)" % peak} if peak else {}
    return R._warning_context(col, mats={}, batt={}, brg=None, max_speed_rpm=None, mag_lim=None, mag_note="",
                              ins_lim=180.0, ins_note="the project's winding limit (180 °C)", cold_k=1.0,
                              cold_note="", **kw)


def test_the_insulation_limit_has_a_peak_variant():
    from motor_ai_sim import report as R

    assert R._insulation_limit({}, BOTH) == (180.0, "the project's winding limit (180 °C)")
    assert R._insulation_limit({}, BOTH, peak=True) == (200.0, "the project's peak winding limit (200 °C)")
    # one limit: the peak variant is the continuous one, in the continuous words
    assert R._insulation_limit({}, {"limits": {"winding_c": 180}}, peak=True) == (
        180.0, "the project's winding limit (180 °C)")
    # no limit stated: the class default for both
    assert R._insulation_limit({}, {}, peak=True)[0] == R.PROJECT_INSULATION_C


def test_a_limit_stopped_record_is_judged_at_the_peak_limit():
    cp = {"mode": "limited", "coil_temp_c": 200.0,
          "limited": {"t_cold_s": 44.0, "at_limit_c": {"winding": 200.0}, "line": "stopped"}}
    col = _col("peak", EM_PEAK, {"coupled": cp})
    ctx = _ctx(col)
    assert ctx["winding_temp_c"] == 200.0 and ctx["winding_limit_c"] == 200.0
    assert "peak winding limit (200" in ctx["winding_limit_note"]
    # the continuous limit is still on the context under its own name
    assert ctx["winding_cont_limit_c"] == 180.0 and ctx["winding_peak_limit_c"] == 200.0
    # without a separate peak limit the record stopped at 200 is "another limit" again: not judged
    assert _ctx(col, peak=None)["winding_temp_c"] is None


def _dc_col(kind):
    dc = {"spec": {"kind": kind}, "cycle": {"winding_hot_peak_c": 199.0, "peak_c": {"winding": 190.0}},
          "limits": {"winding_limit_c": 200.0 if kind == "S2" else 180.0, "winding_peak_limit_c": 200.0}}
    return _col("peak", EM_PEAK, {"duty_cycle": dc})


def test_a_single_s2_pull_is_judged_at_the_peak_limit_an_s3_cycle_at_the_continuous_one():
    s2, s3 = _ctx(_dc_col("S2")), _ctx(_dc_col("S3"))
    assert s2["winding_limit_c"] == 200.0 and "peak winding limit" in s2["winding_limit_note"]
    assert s3["winding_limit_c"] == 180.0 and "peak winding limit" not in s3["winding_limit_note"]
    # no steady-state / S3 judgement moves; the limits table still prints the continuous one
    assert s2["winding_cont_limit_c"] == s3["winding_cont_limit_c"] == 180.0


def test_the_limits_table_prints_the_peak_limit_beside_the_continuous_one():
    from motor_ai_sim import report as R

    rows = R._limit_rules_rows_full(_ctx(_dc_col("S2")))
    by = {r[0]: r for r in rows[1:]}
    assert by["Winding temperature"][1] == "180 °C"
    assert by["Hot spot in the machine"][1] == "180 °C"
    assert by["Winding temperature, short peak (S2)"][1] == "200 °C"
    # one limit: no extra row
    rows1 = R._limit_rules_rows_full(_ctx(_dc_col("S2"), peak=None))
    assert "Winding temperature, short peak (S2)" not in {r[0] for r in rows1[1:]}
