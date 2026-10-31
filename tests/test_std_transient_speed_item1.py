"""Standard-transient speed-up, item 1 (2026-10-31): the six-phase L_xy probe
reused across the coupled passes of one machine, and ``get_2d_polygons``
memoised on the machine's parameters."""
from __future__ import annotations

import pytest


def _same(x, y):
    if isinstance(x, dict):
        return x.keys() == y.keys() and all(_same(x[k], y[k]) for k in x)
    if isinstance(x, (list, tuple)):
        return len(x) == len(y) and all(_same(p, q) for p, q in zip(x, y))
    if hasattr(x, "equals_exact"):
        return x.equals_exact(y, 0.0)
    return x == y


def test_polygons_are_memoised_on_the_parameters(monkeypatch):
    from motor_ai_sim import cadquery_geometry as cq
    monkeypatch.setattr(cq, "_POLY_CACHE", type(cq._POLY_CACHE)())
    calls = []
    real = cq.CadQueryMotor._get_2d_polygons_built

    def _counted(self, rotor_angle_deg=0.0):
        calls.append(float(rotor_angle_deg))
        return real(self, rotor_angle_deg)
    monkeypatch.setattr(cq.CadQueryMotor, "_get_2d_polygons_built", _counted)

    a = cq.CadQueryMotor().get_2d_polygons(0.0)
    m2 = cq.CadQueryMotor()
    b = m2.get_2d_polygons(0.0)
    assert calls == [0.0]                                  # one build, one hit
    assert _same(a, b)
    # fresh containers: a caller editing its dict cannot reach the memo
    b["magnets"].clear()
    assert len(cq.CadQueryMotor().get_2d_polygons(0.0)["magnets"]) == len(
        a["magnets"])
    # the builder's side attributes are restored on a hit
    for k in cq._POLY_SIDE_ATTRS:
        assert hasattr(m2, k)
    # another angle, another parameter set: built again
    cq.CadQueryMotor().get_2d_polygons(5.0)
    m3 = cq.CadQueryMotor()
    m3.parameters = dict(m3.parameters, air_gap=float(
        m3.parameters.get("air_gap", 1.0)) + 0.1)
    m3.get_2d_polygons(0.0)
    assert calls == [0.0, 5.0, 0.0]
    # SB_POLY_CACHE=0: the builder every time
    monkeypatch.setenv("SB_POLY_CACHE", "0")
    cq.CadQueryMotor().get_2d_polygons(0.0)
    assert calls == [0.0, 5.0, 0.0, 0.0]


def test_lxy_probe_is_reused_only_at_its_own_operating_point():
    from motor_ai_sim.routes import simulation as S
    blk = {"inductances": {"Lxy_mH": 0.12}, "probe": {"frames": 4},
           "probe_op": {"I_A": 157.0, "gamma_deg": 2.8, "magnet_temp_c": 64.2}}
    ctx = {"mode": "reuse", "six_phase": blk, "source": "coupled pass 1"}
    hit = S._lxy_reuse_block(ctx, i_a=158.0, gamma_deg=3.5,
                             magnet_temp_c=66.0)
    assert hit["inductances"] == {"Lxy_mH": 0.12}
    assert hit["inductances_reused"]["source"] == "coupled pass 1"
    assert S._lxy_reuse_block(ctx, i_a=163.0, gamma_deg=2.8,
                              magnet_temp_c=64.2) is None          # +3.8 %
    assert S._lxy_reuse_block(ctx, i_a=157.0, gamma_deg=6.0,
                              magnet_temp_c=64.2) is None          # 3.2 deg
    assert S._lxy_reuse_block(ctx, i_a=157.0, gamma_deg=2.8,
                              magnet_temp_c=75.0) is None          # 10.8 K
    assert S._lxy_reuse_block({"mode": "skip"}, i_a=157.0, gamma_deg=2.8,
                              magnet_temp_c=64.2) is None
    # a block from before `probe_op` existed is never reused
    old = {"inductances": {"Lxy_mH": 0.12}}
    assert S._lxy_reuse_block({"mode": "reuse", "six_phase": old}, i_a=157.0,
                              gamma_deg=2.8, magnet_temp_c=64.2) is None


def test_the_coupled_loop_offers_the_previous_pass_probe(monkeypatch):
    from motor_ai_sim.routes import coupled as cp
    from motor_ai_sim.routes import simulation as S
    assert cp._lxy_from({}, "x") is None
    assert cp._lxy_from({"summary": {"six_phase": {"set1_paths": 1}}},
                        "x") is None                     # no probe in it
    em = {"summary": {"six_phase": {"inductances": {"Lxy_mH": 0.3},
                                    "probe_op": {"I_A": 1.0, "gamma_deg": 0.0,
                                                 "magnet_temp_c": None}}}}
    lxy = cp._lxy_from(em, "coupled pass 1")
    assert lxy["mode"] == "reuse" and lxy["source"] == "coupled pass 1"
    seen = []

    def _inner(body, **kw):
        seen.append(S._LXY_REUSE_CTX.get())
        return {"ok": True}
    monkeypatch.setattr(cp, "_em_run_in", _inner)
    cp._em_run({}, coil_temp_c=20.0, magnet_temp_c=None, lxy=lxy)
    cp._em_run({}, coil_temp_c=20.0, magnet_temp_c=None)
    assert seen == [lxy, None]
    assert S._LXY_REUSE_CTX.get() is None                 # reset after the call
