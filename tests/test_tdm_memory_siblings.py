"""TDM memory guard aware of the sibling evals of a sweep pool, and the plain
reason code of a march that replaces a TDM attempt.  Payload only: fake memory
files, fake solver, no FEM."""
import io
import math

import pytest

from motor_ai_sim.simulation import fem_solver_2d as FS
from motor_ai_sim.simulation import time_periodic as tp

GB = 2**30


def _fake_open(limit_gb, avail_gb, rss_gb):
    files = {
        "/sys/fs/cgroup/memory.max": "%d\n" % int(limit_gb * GB),
        "/proc/meminfo": "MemAvailable: %d kB\n" % int(avail_gb * GB / 1024),
        "/proc/self/status": "VmRSS: %d kB\n" % int(rss_gb * GB / 1024),
    }

    def _open(path, *a, **k):
        if path in files:
            return io.StringIO(files[path])
        raise OSError(path)
    return _open


def _budget(monkeypatch, sibs, limit_gb=48, avail_gb=200, rss_gb=1):
    monkeypatch.setattr(tp, "open", _fake_open(limit_gb, avail_gb, rss_gb),
                        raising=False)
    if sibs is None:
        monkeypatch.delenv("SB_TDM_SIBLINGS", raising=False)
    else:
        monkeypatch.setenv("SB_TDM_SIBLINGS", sibs)
    return tp.memory_budget_bytes()


def test_unknown_siblings_keep_the_old_budget(monkeypatch):
    old = int(0.85 * 48 * GB) - 1 * GB
    for v in (None, "", "x", "0", "-3", "1"):
        assert _budget(monkeypatch, v) == old, v


def test_the_container_limit_is_shared_by_the_siblings(monkeypatch):
    assert _budget(monkeypatch, "6") == int(0.85 * 8 * GB) - 1 * GB
    assert tp.sibling_evals() == 6


def test_free_memory_is_not_divided(monkeypatch):
    # the free-memory reading is the smaller one: unchanged by the sibling count
    one = _budget(monkeypatch, None, limit_gb=500, avail_gb=20)
    six = _budget(monkeypatch, "6", limit_gb=500, avail_gb=20)
    assert one == six == int(0.85 * 20 * GB) - 1 * GB


class _Fac:
    def __init__(self, b):
        self._b = b

    def kept_bytes(self):
        return self._b


def _guard(n_frames, per_gb):
    import types
    me = types.SimpleNamespace(N=n_frames, _mem_abort=None)
    tp.TimePeriodicEddy._mem_guard(me, _Fac(int(per_gb * GB)))


def test_guard_decision_fits_alone_but_not_with_five_siblings(monkeypatch):
    # 108 frames x 0.08 GB = 8.6 GB in a 48 GB container
    _budget(monkeypatch, None)
    _guard(108, 0.08)                                   # alone: fits
    _budget(monkeypatch, "6")
    with pytest.raises(tp.TdmMemoryExceeded):
        _guard(108, 0.08)                               # 6 evals: 8 GB each, no
    _budget(monkeypatch, "6")
    _guard(48, 0.08)                                    # 48 steps: 3.8 GB, fits


def test_the_refusal_is_a_coded_attempt_failure_then_a_march(monkeypatch):
    """A guard stop becomes TdmAttemptFailed(code='memory_guard'); the march
    that follows carries that code, never the exception text."""
    seen = []

    def fake(**kw):
        ctl = kw.get("_tdm_ctl") or {}
        seen.append(ctl)
        if "fallback" in ctl:
            return {"eddy_method": "march", "code": ctl["fallback"]["code"]}
        raise FS.TdmAttemptFailed("newton", "TdmMemoryExceeded: tdm_memory: x",
                                  code="memory_guard")
    monkeypatch.setattr(FS, "_fem_transient_sliding_band_once", fake)
    out = FS.fem_transient_sliding_band(eddy=True)
    assert out == {"eddy_method": "march", "code": "memory_guard"}
    assert len(seen) == 2          # one failed attempt, one march (no TDM retry)


def test_an_ordinary_failure_has_no_code(monkeypatch):
    def fake(**kw):
        ctl = kw.get("_tdm_ctl") or {}
        if "fallback" in ctl:
            return {"code": ctl["fallback"]["code"]}
        raise FS.TdmAttemptFailed("newton", "boom")
    monkeypatch.setattr(FS, "_fem_transient_sliding_band_once", fake)
    assert FS.fem_transient_sliding_band(eddy=True) == {"code": None}


def test_the_result_payload_has_the_reason_only_after_a_fallback():
    from pathlib import Path
    txt = Path(FS.__file__).read_text(encoding="utf-8")
    assert '"eddy_method_fallback_reason": _tdm_fb["code"]' in txt
    assert 'if (_tdm_fb and _tdm_fb.get("code")) else {}' in txt


def _run_one(monkeypatch, extra):
    from motor_ai_sim.contracts.result_ir import ResultIR
    from motor_ai_sim.optimization import refine_proc as R
    n = 8
    ph = [2 * math.pi * k / n for k in range(n)]
    raw = {"T_avg_Nm": 1.0, "T_ripple_pct": 2.0, "P_cu_W": [5.0] * n,
           "P_fe_W": [1.0] * n, "P_mag_eddy_W": [0.1] * n,
           "P_shaft_eddy_W": [0.01] * n, "V_peak": 10.0, "picard_converged": True,
           "n_steps": n, "V_A": [10 * math.sin(p) for p in ph],
           "V_B": [10 * math.sin(p - 2.0944) for p in ph],
           "V_C": [10 * math.sin(p + 2.0944) for p in ph],
           "eddy_method": "march", "eddy_method_requested": "tdm", **extra}

    class _K:
        def run(self, capability, payload):
            return {"ok": True, "capability": capability,
                    "result": ResultIR(physics="em_transient", ok=True, raw=raw)}
    monkeypatch.setattr(R, "_kernel", lambda: _K())
    return R.run_one({}, 50.0, n, 100.0, n_periods=1.0, gamma_deg=0.0,
                     mesh_size_mm=4.0, min_size_mm=0.3, n_sectors=4,
                     element_order=2, rpm=3000.0)


def test_refine_proc_carries_the_reason_and_adds_no_key_without_it(monkeypatch):
    out = _run_one(monkeypatch, {"eddy_method_fallback_reason": "memory_guard"})
    assert out["eddy_method"] == "march"
    assert out["eddy_method_fallback_reason"] == "memory_guard"
    assert "eddy_method_fallback_reason" not in _run_one(monkeypatch, {})


def test_the_pool_env_names_the_siblings_only_inside_a_fan_out(monkeypatch):
    from motor_ai_sim.routes import optimization as opt
    monkeypatch.delenv("SB_TDM_SIBLINGS", raising=False)
    assert "SB_TDM_SIBLINGS" not in opt._eval_env_for(None)
    tok = opt._EVAL_SIBLINGS.set(1)
    try:
        assert "SB_TDM_SIBLINGS" not in opt._eval_env_for(None)
    finally:
        opt._EVAL_SIBLINGS.reset(tok)
    tok = opt._EVAL_SIBLINGS.set(6)
    try:
        assert opt._eval_env_for(None)["SB_TDM_SIBLINGS"] == "6"
    finally:
        opt._EVAL_SIBLINGS.reset(tok)
    monkeypatch.setenv("SB_TDM_SIBLINGS", "9")        # a stray server value
    assert "SB_TDM_SIBLINGS" not in opt._eval_env_for(None)


def _grow(monkeypatch, n_frames, per_gb, limit_gb, sibs, base_rss_gb=1.0):
    """Build the frames one by one with an RSS that grows by one factor each,
    calling the guard after every factor, as `_jac_all` does. Returns the
    frame at which the guard stopped, or None when all were built."""
    import types
    monkeypatch.setenv("SB_TDM_SIBLINGS", str(sibs))
    me = types.SimpleNamespace(N=n_frames, _mem_abort=None)
    for k in range(1, n_frames + 1):
        monkeypatch.setattr(
            tp, "open", _fake_open(limit_gb, 500, base_rss_gb + k * per_gb),
            raising=False)
        try:
            tp.TimePeriodicEddy._mem_guard(me, _Fac(int(per_gb * GB)))
        except tp.TdmMemoryExceeded:
            return k
    # a second Newton pass refactors every frame: all are in the RSS already
    for k in range(n_frames):
        tp.TimePeriodicEddy._mem_guard(me, _Fac(int(per_gb * GB)))
    return None


def test_growing_rss_is_not_double_counted(monkeypatch):
    # 48 x 0.08 GB, 1 GB base, 6 siblings in 48 GB: the finished eval holds
    # 4.84 GB and fits its 6.8 GB share, so it must not be refused midway
    assert _grow(monkeypatch, 48, 0.08, 48, 6) is None


def test_eu_numbers_48_steps_stay_on_tdm_108_march(monkeypatch):
    # EU worker limit 36 GB, 6 sweep workers = 6 GB each
    assert _grow(monkeypatch, 48, 0.08, 36, 6) is None
    assert _grow(monkeypatch, 108, 0.08, 36, 6) is not None


def test_a_refused_eval_carries_the_memory_guard_code(monkeypatch):
    """The refusal reaches the solver as TdmMemoryExceeded; the handler in
    fem_transient_sliding_band maps it to code memory_guard (see the
    coded-fallback test above)."""
    assert _grow(monkeypatch, 108, 0.08, 36, 6) == 1
