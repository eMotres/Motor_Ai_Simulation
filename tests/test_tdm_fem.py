"""Time-periodic eddy steady state (the default since 2026-09-30) on the
sandbox 30 mm 12s14p: it reproduces the march, a failure falls back to the
march loudly, what it cannot serve is marched with a note, and Coulomb torque
works on its frames.  Real solves (the 12-step p2_eddy case of the physics
regression, a few seconds to a minute each)."""
from __future__ import annotations

from typing import Any, Dict

import pytest

from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.simulation import fem_solver_2d as F
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

from tests.test_physics_regression import (CASES, CONNECTION, GEO_30MM,
                                           OVERRIDE, RPM)

pytestmark = pytest.mark.slow


def _cold():
    F._SB_WARM_CACHE.clear()
    try:
        p = F._warm_cache_path()
        if p.exists():
            p.unlink()
    except Exception:      # noqa: BLE001
        pass


def _run(**over: Any) -> Dict[str, Any]:
    kw = dict(CASES["p2_eddy"]); kw.update(over)
    _cold()
    set_request_materials(OVERRIDE)
    try:
        return fem_transient_sliding_band(geo_override=dict(GEO_30MM), rpm=RPM,
                                          connection=CONNECTION, **kw)
    finally:
        set_request_materials(None)
        _cold()


@pytest.fixture(scope="module")
def march():
    return _run(eddy_method="march")


@pytest.fixture(scope="module")
def tdm():
    return _run()                       # the default


def _mean(d, k):
    v = d.get(k) or []
    return sum(v) / len(v) if v else 0.0


def test_the_default_is_tdm_and_it_reproduces_the_march(march, tdm):
    assert tdm["eddy_method"] == "tdm" and tdm["eddy_method_note"] is None
    assert tdm["eddy_method_requested"] == "tdm"
    assert march["eddy_method"] == "march"
    info = tdm["tdm"]
    assert info["solve"]["converged"]
    assert tdm["eddy_warmup_frames"] == 0 and tdm["eddy_settled"]
    # the owner's accuracy terms, with a wide margin
    assert tdm["T_avg_Nm"] == pytest.approx(march["T_avg_Nm"], rel=1e-3)
    assert abs(tdm["T_ripple_pct"] - march["T_ripple_pct"]) < 0.05
    assert tdm["P_loss_total_avg_W"] == pytest.approx(march["P_loss_total_avg_W"],
                                                      rel=5e-3)
    for k in ("P_cu_ac_W", "P_mag_eddy_W"):
        assert _mean(tdm, k) == pytest.approx(_mean(march, k), rel=1e-2), k
    # the reported period marched from the orbit stayed on it
    assert info["march_vs_orbit_last_frame"] < 1e-3


def test_a_tdm_failure_is_marched_loudly(monkeypatch, march):
    monkeypatch.setenv("SB_TDM_MAX_NEWTON", "0")     # no Newton step allowed
    d = _run()
    assert d["eddy_method"] == "march"
    assert d["eddy_method_requested"] == "tdm"
    assert d["eddy_method_note"].startswith("march: TDM failed (")
    assert d["tdm"]["failed"]
    # a clean march: the warm-up ran and the numbers are the march's
    assert d["eddy_warmup_frames"] > 0
    assert d["T_avg_Nm"] == pytest.approx(march["T_avg_Nm"], rel=1e-6)
    assert d["P_loss_total_avg_W"] == pytest.approx(march["P_loss_total_avg_W"],
                                                    rel=1e-6)


def test_what_tdm_cannot_serve_is_marched_with_a_note():
    d = _run(frozen_nu=True)
    assert d["eddy_method"] == "march"
    assert d["eddy_method_note"].startswith("march: TDM not applicable (")
    assert "frozen_nu" in d["eddy_method_note"]
    assert d["tdm"] is None


def test_march_stays_selectable_by_environment(monkeypatch):
    monkeypatch.setenv("SB_EDDY_METHOD", "march")
    d = _run()
    assert d["eddy_method"] == "march" and d["eddy_method_note"] is None


def test_coulomb_torque_on_the_tdm_frames(march):
    c = _run(torque_method="coulomb")
    assert c["eddy_method"] == "tdm"
    ct = c["coulomb_torque"]
    assert ct["available"] and ct["layer_self_check"]["max_abs_diff_Nm"] is not None
    assert c["T_avg_Nm"] == pytest.approx(c["T_avg_coulomb_Nm"], rel=1e-12)
    assert c["T_avg_coulomb_Nm"] == pytest.approx(march["T_avg_coulomb_Nm"], rel=1e-3)
    mon = c["tdm"]["solve"]["newton"][-1]["monitor"]
    assert mon["torque_method"] == "coulomb_virtual_work"
