"""TP-EEC: a fast DC mode is left to the march (2026-10-31).

The rotor-frame DC error correction (`periodic_accel.dc_error_correction`) is
an accelerator for a SLOW solid ring (L155 shaft wall: slowest periodic DC mode
lambda 0.956-0.960 per period).  An applied correction restarts the settle
gauge and starts the MIN_VERIFY_PERIODS count after a jump.  When the slowest
mode it measured decays faster than ``SB_EDDY_EEC_LAMBDA_MIN`` (0.5) per
period, the march settles it by itself within a period or two.  The L90 cold
run measured lambda 6.6e-5 (tau 0.10 period), and the applied correction cost
360 of 1082 frames for results identical to 0.014 %.  Pure unit tests of the
decision, plus a static guard on the solver's use of it.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from motor_ai_sim.simulation import fem_solver_2d as FS


def test_slow_modes_are_corrected_as_before():
    for lam in (0.956, 0.960, 0.9386, 0.83, 0.5):          # L155-type shafts
        ok, why = FS._eec_apply_decision(lam, None, 0.5)
        assert ok is True and why is None, lam


def test_a_fast_mode_is_left_to_the_march():
    ok, why = FS._eec_apply_decision(6.627e-05, None, 0.5)  # the L90 cold run
    assert ok is False
    assert "decays by itself" in why and "6.63e-05" in why
    tau = -1.0 / math.log(6.627e-05)
    assert ("tau %.2f" % tau) in why                         # 0.10 period


def test_what_was_refused_stays_refused():
    for lam, ref in ((None, None), (0.0, None), (-0.2, None), (0.999, None),
                     (1.2, None), (0.9, "not converged")):
        ok, why = FS._eec_apply_decision(lam, ref, 0.5)
        assert ok is False and why is None, (lam, ref)


def test_lambda_min_zero_applies_every_correction_as_before():
    assert FS._eec_apply_decision(6.627e-05, None, 0.0) == (True, None)


def test_the_switch(monkeypatch):
    monkeypatch.delenv("SB_EDDY_EEC_LAMBDA_MIN", raising=False)
    assert FS._eec_lambda_min() == FS.EEC_LAMBDA_MIN_DEFAULT == 0.5
    monkeypatch.setenv("SB_EDDY_EEC_LAMBDA_MIN", "0")
    assert FS._eec_lambda_min() == 0.0
    monkeypatch.setenv("SB_EDDY_EEC_LAMBDA_MIN", "0.7")
    assert FS._eec_lambda_min() == 0.7
    monkeypatch.setenv("SB_EDDY_EEC_LAMBDA_MIN", "junk")
    assert FS._eec_lambda_min() == 0.5


def test_the_solver_decides_with_it_and_restarts_the_gauge_only_when_applied():
    src = (Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim"
           / "simulation" / "fem_solver_2d.py").read_text(encoding="utf-8")
    i_dec = src.index("_ok_e, _skip_e = _eec_apply_decision(")
    i_app = src.index("if _ok_e:", i_dec)
    i_rst = src.index("_warm_grp = {}          # the gauge restarts", i_app)
    i_rec = src.index('"applied": _ok_e})', i_rst)
    assert i_dec < i_app < i_rst < i_rec
    assert "_eec_lam_min = _eec_lambda_min()" in src
