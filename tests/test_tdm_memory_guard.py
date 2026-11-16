import types

import pytest

from motor_ai_sim.simulation import time_periodic as tp


class _Fac:
    def __init__(self, b):
        self._b = b

    def kept_bytes(self):
        return self._b


def _self(n):
    return types.SimpleNamespace(N=n, _mem_abort=None)


def test_guard_raises_when_all_factors_exceed_the_budget(monkeypatch):
    monkeypatch.setattr(tp, "memory_budget_bytes", lambda: 5 * 2**30)
    me = _self(144)
    with pytest.raises(tp.TdmMemoryExceeded):
        tp.TimePeriodicEddy._mem_guard(me, _Fac(int(0.07 * 2**30)))
    assert "tdm_memory" in me._mem_abort


def test_guard_passes_when_they_fit(monkeypatch):
    monkeypatch.setattr(tp, "memory_budget_bytes", lambda: 40 * 2**30)
    tp.TimePeriodicEddy._mem_guard(_self(144), _Fac(int(0.07 * 2**30)))


def test_guard_is_off_without_a_budget_or_a_factor_size(monkeypatch):
    monkeypatch.setattr(tp, "memory_budget_bytes", lambda: None)
    tp.TimePeriodicEddy._mem_guard(_self(144), _Fac(10**12))
    monkeypatch.setattr(tp, "memory_budget_bytes", lambda: 1)
    tp.TimePeriodicEddy._mem_guard(_self(144), _Fac(0))


def test_switch_off(monkeypatch):
    monkeypatch.setenv("SB_TDM_MEM_GUARD", "0")
    monkeypatch.setattr(tp, "memory_budget_bytes", lambda: 1)
    tp.TimePeriodicEddy._mem_guard(_self(144), _Fac(10**12))
