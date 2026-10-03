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
                '"eddy_method_note": (', '"tdm": _tdm_info', '"tdm_experimental"',
                '"qualified"', '"steady_state_note"'):
        assert key in txt, key
    assert 'march: TDM failed (' in txt and 'march: TDM not applicable (' in txt


# ── second Codex review (2026-10-03) ─────────────────────────────────────────
def test_a_rejected_attempt_leaves_nothing_referenced(monkeypatch):
    """Finding 9: the wrapper keeps a rejected attempt's record and message
    only — not its traceback frames, its __cause__ or its arrays — so the
    retry and the march run without the failed attempt's memory."""
    import gc
    import weakref
    import numpy as np
    refs = []
    seen_alive = []

    def fake(**kw):
        gc.collect()
        seen_alive.append([r() is not None for r in refs])
        ctl = kw.get("_tdm_ctl") or {}
        if "fallback" in ctl:
            return {"eddy_method": "march", "note": ctl["fallback"]["note"],
                    "attempts": ctl["fallback"]["attempts"]}
        big = np.ones(1_000_000)               # the attempt's "orbit"
        refs.append(weakref.ref(big))

        def inner():
            local_copy = big                   # noqa: F841 — held by the frame
            raise RuntimeError("PARDISO error -4 in frame 3")
        try:
            inner()
        except RuntimeError as e:
            raise FS.TdmAttemptFailed("newton" if ctl else "report_gate", str(e),
                                      info={"t": 1.0},
                                      retry_residual=not ctl) from e
    monkeypatch.setattr(FS, "_fem_transient_sliding_band_once", fake)
    out = FS.fem_transient_sliding_band(eddy=True)
    assert out["eddy_method"] == "march"
    assert [a["stage"] for a in out["attempts"]] == ["report_gate", "newton"]
    assert out["note"].startswith("march: TDM failed (newton: PARDISO error -4")
    # attempt 2 started with attempt 1's array gone; the march with both gone
    assert seen_alive == [[], [False], [False, False]], seen_alive


def test_the_optimizer_result_keeps_the_steady_state_verdict(monkeypatch):
    """Finding 8 (fast half; the real solve is in test_tdm_entry_points):
    refine_proc.run_one carries steady_state, its note, demag_settled, the
    method, its note and the qualified flag from the solver's payload."""
    import math
    from motor_ai_sim.contracts.result_ir import ResultIR
    from motor_ai_sim.optimization import refine_proc as R
    n = 8
    ph = [2 * math.pi * k / n for k in range(n)]
    raw = {"T_avg_Nm": 1.0, "T_ripple_pct": 2.0, "P_cu_W": [5.0] * n,
           "P_fe_W": [1.0] * n, "P_mag_eddy_W": [0.1] * n, "P_shaft_eddy_W": [0.01] * n,
           "V_peak": 10.0, "picard_converged": True, "n_steps": n,
           "V_A": [10 * math.sin(p) for p in ph],
           "V_B": [10 * math.sin(p - 2.0944) for p in ph],
           "V_C": [10 * math.sin(p + 2.0944) for p in ph],
           "steady_state": False,
           "steady_state_note": "demag NOT settled: 0.002 at one element",
           "demag_settled": False, "eddy_method": "tdm", "eddy_method_requested": "tdm",
           "eddy_method_note": "tdm: EXPERIMENTAL demag shortcut",
           "tdm_experimental": True, "qualified": False}

    class _K:
        def run(self, capability, payload):
            return {"ok": True, "capability": capability,
                    "result": ResultIR(physics="em_transient", ok=True, raw=raw)}
    monkeypatch.setattr(R, "_kernel", lambda: _K())
    out = R.run_one({}, 50.0, n, 100.0, n_periods=1.0, gamma_deg=0.0,
                    mesh_size_mm=4.0, min_size_mm=0.3, n_sectors=4,
                    element_order=2, rpm=3000.0)
    for k in ("steady_state", "steady_state_note", "demag_settled", "eddy_method",
              "eddy_method_requested", "eddy_method_note", "tdm_experimental",
              "qualified"):
        assert out[k] == raw[k], k
