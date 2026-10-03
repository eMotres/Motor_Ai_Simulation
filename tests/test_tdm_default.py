"""eddy_method: TDM is the default for every steady-state eddy run
(owner 2026-09-30), "march" stays selectable, and what TDM cannot serve goes
to the march with a reason.  Fast tests: no FEM solve (the solves are in
tests/test_tdm_fem.py)."""
import inspect
import re
from pathlib import Path

import pytest

from motor_ai_sim.simulation import fem_solver_2d as FS
from motor_ai_sim.simulation import time_periodic as TP

SRC = Path(FS.__file__).resolve().parents[1]      # src/motor_ai_sim


def test_default_is_tdm_and_march_stays_selectable():
    assert TP.resolve_eddy_method(None, {}, {}) == "tdm"
    assert TP.resolve_eddy_method("march", {}, {}) == "march"
    assert TP.resolve_eddy_method("TDM", {}, {}) == "tdm"
    # precedence: argument > SB_EDDY_METHOD > config > default
    assert TP.resolve_eddy_method(None, {"eddy_method": "march"}, {}) == "march"
    assert TP.resolve_eddy_method(None, {"eddy_method": "march"},
                                  {"SB_EDDY_METHOD": "tdm"}) == "tdm"
    assert TP.resolve_eddy_method("march", {}, {"SB_EDDY_METHOD": "tdm"}) == "march"
    with pytest.raises(ValueError):
        TP.resolve_eddy_method("parareal", {}, {})


def test_both_solver_entry_points_default_to_none_meaning_tdm():
    for fn in (FS.fem_transient_sliding_band, FS.em_transient_eval):
        p = inspect.signature(fn).parameters
        assert p["eddy_method"].default is None, fn.__name__
        assert p["tdm_demag"].default is None, fn.__name__


def test_em_transient_eval_forwards_the_choice(monkeypatch):
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return {}
    monkeypatch.setattr(FS, "fem_transient_sliding_band", fake)
    FS.em_transient_eval(n_steps_per_period=12, n_periods=1, gamma_deg=0.0,
                         I_phase_rms=10.0, eddy=True)
    assert seen["eddy_method"] is None and seen["eddy"] is True
    FS.em_transient_eval(n_steps_per_period=12, n_periods=1, gamma_deg=0.0,
                         I_phase_rms=10.0, eddy=True, eddy_method="march",
                         tdm_demag="shortcut")
    assert seen["eddy_method"] == "march" and seen["tdm_demag"] == "shortcut"


def test_solve_pool_drop_in_forwards_unchanged(monkeypatch):
    from motor_ai_sim import solve_pool as SP
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return {}
    monkeypatch.setattr(FS, "em_transient_eval", fake)
    monkeypatch.setattr(SP, "enabled", lambda: False)
    SP.em_transient_eval(n_steps_per_period=12, n_periods=1, gamma_deg=0.0,
                         I_phase_rms=10.0, eddy=True)
    assert "eddy_method" not in seen          # the solver's default decides


def test_no_entry_point_pins_the_march():
    """The EM tab, the coupled loop, sweeps / optimizer (refine_proc), agent
    drafts / MCP and passports all reach the solver through
    em_transient_eval without an eddy_method: none may pin "march"."""
    pat = re.compile(r"""eddy_method\s*=\s*['"]march['"]""")
    hits = []
    for p in SRC.rglob("*.py"):
        if p.name in ("fem_solver_2d.py", "time_periodic.py"):
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        if pat.search(txt):
            hits.append(str(p))
    assert not hits, hits


BASE = dict(eddy=True, voltage_drive=False, series_paths=False, frozen_nu=False,
            mixed_schedule=False, bdf2=True, n_periods=1.0, full_ring=False,
            source_name="current", external_excitation=False, six_phase=False)


def test_a_sine_current_sector_run_is_tdm():
    assert TP.tdm_refusals(**BASE) == []
    assert TP.tdm_refusals(**dict(BASE, source_name="custom_current")) == []
    assert TP.tdm_refusals(**dict(BASE, source_name="bldc_current")) == []
    assert TP.tdm_refusals(**dict(BASE, n_periods=2.0)) == []


@pytest.mark.parametrize("over, word", [
    (dict(voltage_drive=True, source_name="voltage"), "voltage"),
    (dict(voltage_drive=True, source_name="pwm_voltage"), "PWM"),
    (dict(series_paths=True), "strand"),
    (dict(frozen_nu=True), "frozen"),
    (dict(mixed_schedule=True), "mixed"),
    (dict(bdf2=False), "Euler"),
    (dict(n_periods=1.5), "fractional"),
    (dict(full_ring=True), "full-ring"),
    (dict(external_excitation=True), "external"),
    (dict(six_phase=True), "six-phase"),
    (dict(eddy=False), "eddy is off"),
    (dict(n_steps_per_period=1), "fewer than 6 steps"),
])
def test_what_tdm_cannot_serve_is_marched_with_a_reason(over, word):
    why = TP.tdm_refusals(**dict(BASE, **over))
    assert why and any(word in w for w in why), why


def test_the_result_carries_the_method_and_the_note():
    """The payload keys the fallbacks promise (read by the UI / reports)."""
    txt = Path(FS.__file__).read_text(encoding="utf-8")
    for key in ('"eddy_method": _eddy_method', '"eddy_method_requested"',
                '"eddy_method_note": _tdm_note', '"tdm": _tdm_info'):
        assert key in txt, key
    assert 'march: TDM failed (' in txt and 'march: TDM not applicable (' in txt
