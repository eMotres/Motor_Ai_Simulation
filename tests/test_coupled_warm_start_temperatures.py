"""WHERE A COUPLED LOOP STARTS, and the Br map it may carry (owner 2026-11-03).

A water-cooled generator's coupled run with no temperatures in the body started
at the magnet card's own 175 °C.  Its first pass demagnetised the magnets
(1.8-5 % of Br) at a temperature the machine never reaches, the carried Br map
kept that loss through every colder pass, and the run held 75.6 of 83.3 N·m.

The start is now: the body's overrides > the stored warm state of THIS
configuration (its last converged coupled record) > sink + a modest rise; and a
Br map is never carried into a run from a lineage that was ratcheted hotter.

No FEM here: the loop's two halves are faked, the demagnetisation rule is the
real ``MagnetDemag`` driven by a synthetic field on real magnet cards.
"""
from __future__ import annotations

import types

import numpy as np
import pytest

from tests.test_coupled import COOLING as PANEL_COOLING, EM_BODY


# ---------------------------------------------------------------------------
# the start order
# ---------------------------------------------------------------------------

def _stored_rows(**duties):
    """``duty_results.get`` rows: {duty: {"coupled": entry}}."""
    return {d: {"coupled": e} for d, e in duties.items()}


def _converged(coil, mag, rpm=3000.0, i=40.0, **kw):
    return {"converged": True, "runaway": False, "coil_temp_c": coil,
            "magnet_temp_c": mag, "point": {"rpm": rpm, "I_phase_rms": i},
            "computed_at": "2026-11-02T10:00:00Z", **kw}


@pytest.fixture
def ctx(monkeypatch):
    """A catalogued machine: die/config/duty, a magnet card at 175 °C, and a
    stored-results store the test fills."""
    from motor_ai_sim import duty_results as dr
    from motor_ai_sim import winding_limit as wl
    from motor_ai_sim.routes import coupled as cp

    state = {"rows": {}}
    monkeypatch.setattr(dr, "active_context", lambda: ("DIE", "CFG", "rated"))
    monkeypatch.setattr(dr, "get", lambda die, cfg: (
        state["rows"] if (die, cfg) == ("DIE", "CFG") else {}))
    monkeypatch.setattr(wl, "solved_config", lambda body=None: ("DIE", "CFG"))
    monkeypatch.setattr(cp, "_magnet_reference_temp_c", lambda: 175.0)
    monkeypatch.setattr(cp, "_winding_limit_for", lambda body: 180.0)
    monkeypatch.setattr(cp, "_start_magnet_limit_c", lambda: 150.0)
    return state


WATER = {"cooling_mode": "liquid", "ambient_temp": 40.0,
         "fluid_temp_in_c": 50.0}


def test_overrides_win_over_everything(ctx):
    from motor_ai_sim.routes import coupled as cp
    ctx["rows"] = _stored_rows(rated=_converged(101.0, 88.0))
    s = cp._coupled_start_temperatures(
        {"coil_temp_c": 120.0, "magnet_temp_c": 110.0}, WATER)
    assert (s["coil_temp_c"], s["magnet_temp_c"]) == (120.0, 110.0)
    assert s["source"] == "override"
    assert s["coil_source"] == s["magnet_source"] == "override"
    assert "stored" not in s


def test_stored_warm_state_of_this_duty_is_the_start(ctx):
    from motor_ai_sim.routes import coupled as cp
    ctx["rows"] = _stored_rows(rated=_converged(101.3, 88.6),
                               peak=_converged(140.0, 120.0, i=80.0))
    s = cp._coupled_start_temperatures({"I_phase_rms": 80.0, "rpm": 3000.0},
                                       WATER)
    assert s["source"] == "stored_warm"
    assert (s["coil_temp_c"], s["magnet_temp_c"]) == (101.3, 88.6)
    assert s["stored"]["duty"] == "rated"
    assert s["stored"]["basis"] == "same duty"
    assert s["magnet_temp_c"] != 175.0


def test_nearest_stored_point_when_this_duty_has_none(ctx):
    from motor_ai_sim.routes import coupled as cp
    ctx["rows"] = _stored_rows(
        cruise=_converged(90.0, 70.0, i=30.0),
        peak=_converged(140.0, 120.0, i=80.0),
        # not converged / runaway / limit search: never a start
        broken=_converged(400.0, 300.0, i=75.0, converged=False),
        hot=_converged(380.0, 290.0, i=78.0, runaway=True))
    s = cp._coupled_start_temperatures({"I_phase_rms": 75.0, "rpm": 3000.0},
                                       WATER)
    assert s["source"] == "stored_warm"
    assert (s["coil_temp_c"], s["magnet_temp_c"]) == (140.0, 120.0)
    assert s["stored"] == {"die": "DIE", "config": "CFG", "duty": "peak",
                           "basis": "nearest stored point",
                           "computed_at": "2026-11-02T10:00:00Z"}


def test_a_limit_search_record_is_not_a_start(ctx):
    from motor_ai_sim.routes import coupled as cp
    ctx["rows"] = {"rated": {"coupled_limit": _converged(170.0, 150.0)}}
    s = cp._coupled_start_temperatures({"I_phase_rms": 40.0}, WATER)
    assert s["source"] == "estimate"


def test_nothing_stored_starts_at_sink_plus_rise_not_at_175(ctx):
    from motor_ai_sim.routes import coupled as cp
    s = cp._coupled_start_temperatures({"I_phase_rms": 40.0}, WATER)
    assert s["source"] == "estimate"
    assert s["sink"] == "coolant inlet" and s["sink_c"] == 50.0
    assert s["magnet_temp_c"] == pytest.approx(50.0 + cp.START_MAGNET_RISE_K)
    assert s["coil_temp_c"] == pytest.approx(50.0 + cp.START_COIL_RISE_K)
    assert s["magnet_temp_c"] < 175.0
    # air cooling: the ambient is the sink
    s = cp._coupled_start_temperatures(
        {}, {"cooling_mode": "air", "ambient_temp": 25.0})
    assert s["sink"] == "ambient"
    assert s["magnet_temp_c"] == pytest.approx(65.0)
    assert s["coil_temp_c"] == pytest.approx(85.0)


def test_the_estimate_is_capped_below_the_part_limits(ctx, monkeypatch):
    from motor_ai_sim.routes import coupled as cp
    monkeypatch.setattr(cp, "_start_magnet_limit_c", lambda: 100.0)
    monkeypatch.setattr(cp, "_winding_limit_for", lambda body: 120.0)
    s = cp._coupled_start_temperatures(
        {}, {"cooling_mode": "liquid", "ambient_temp": 40.0,
             "fluid_temp_in_c": 70.0})
    assert s["magnet_temp_c"] == pytest.approx(100.0 - cp.START_LIMIT_MARGIN_K)
    assert s["coil_temp_c"] == pytest.approx(120.0 - cp.START_LIMIT_MARGIN_K)


def test_one_override_and_one_stored_half_is_mixed(ctx):
    from motor_ai_sim.routes import coupled as cp
    ctx["rows"] = _stored_rows(rated=_converged(101.3, 88.6))
    s = cp._coupled_start_temperatures({"coil_temp_c": 130.0}, WATER)
    assert (s["coil_temp_c"], s["magnet_temp_c"]) == (130.0, 88.6)
    assert s["coil_source"] == "override"
    assert s["magnet_source"] == "stored_warm"
    assert s["source"] == "mixed"


def test_no_magnet_card_leaves_the_magnet_alone(ctx, monkeypatch):
    from motor_ai_sim.routes import coupled as cp
    monkeypatch.setattr(cp, "_magnet_reference_temp_c", lambda: None)
    s = cp._coupled_start_temperatures({}, WATER)
    assert s["magnet_temp_c"] is None and s["magnet_source"] == "none"
    assert s["source"] == "estimate"


# ---------------------------------------------------------------------------
# the loop actually starts there, and says so
# ---------------------------------------------------------------------------

LOOP_BODY = {k: v for k, v in EM_BODY.items()
             if k not in ("coil_temp_c", "magnet_temp_c")}
LOOP_BODY.update(thermal_settings=PANEL_COOLING, mechanical=False,
                 cold_constants=False, fresh=True)


def _fake_loop(monkeypatch):
    from motor_ai_sim.routes import coupled as cp
    seen = []

    def _em(body, *, coil_temp_c, magnet_temp_c, **_k):
        seen.append((round(float(coil_temp_c), 3),
                     None if magnet_temp_c is None
                     else round(float(magnet_temp_c), 3)))
        i = float(body["I_phase_rms"])
        return {"summary": {"T_em_avg_Nm": 10.0, "P_loss_total_W": 2.0 * i * i,
                            "rpm": 1000.0, "I_phase_rms_A": i,
                            "coil_temp_C": float(coil_temp_c)}}

    def _th(body, cooling, *, coil_temp_c, magnet_temp_c, rpm, **_k):
        return {"ok": True,
                "components": {"winding": {"avg": 95.0, "max": 99.0},
                               "magnet": {"avg": 80.0, "max": 82.0}}}

    monkeypatch.setattr(cp, "_em_run", _em, raising=True)
    monkeypatch.setattr(cp, "_thermal_solve", _th, raising=True)
    monkeypatch.setattr(cp, "_ttl_step", lambda *a, **k: None, raising=True)
    monkeypatch.setattr(cp, "_attach_coupling", lambda em, block: False,
                        raising=True)
    monkeypatch.setattr(cp, "_remember_last", lambda out, **k: None,
                        raising=True)
    monkeypatch.setattr(cp, "_magnet_reference_temp_c", lambda: 175.0)
    return seen


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


def _coupling(client, **body):
    r = client.post("/api/coupled/run",
                    json={**LOOP_BODY, "max_iter": 1, "record": False, **body})
    assert r.status_code == 200, r.text[:800]
    return r.json()["coupling"]


def test_loop_without_overrides_starts_from_the_stored_warm_state(
        client, monkeypatch):
    from motor_ai_sim.routes import coupled as cp
    seen = _fake_loop(monkeypatch)
    monkeypatch.setattr(cp, "_stored_warm_start", lambda body: {
        "coil_temp_c": 101.3, "magnet_temp_c": 88.6, "die": "DIE",
        "config": "CFG", "duty": "rated", "basis": "same duty",
        "computed_at": None})
    c = _coupling(client)
    assert seen[0] == (101.3, 88.6)
    assert c["start"]["source"] == "stored_warm"
    assert c["start"]["magnet_temp_c"] == 88.6


def test_loop_with_nothing_stored_starts_at_the_estimate(client, monkeypatch):
    from motor_ai_sim.routes import coupled as cp
    seen = _fake_loop(monkeypatch)
    monkeypatch.setattr(cp, "_stored_warm_start", lambda body: None)
    monkeypatch.setattr(cp, "_start_magnet_limit_c", lambda: None)
    monkeypatch.setattr(cp, "_winding_limit_for", lambda body: None)
    c = _coupling(client)
    sink = float(c["start"]["sink_c"])
    assert c["start"]["source"] == "estimate"
    assert seen[0] == (round(sink + cp.START_COIL_RISE_K, 1),
                       round(sink + cp.START_MAGNET_RISE_K, 1))
    assert seen[0][1] != 175.0


def test_loop_overrides_still_win(client, monkeypatch):
    from motor_ai_sim.routes import coupled as cp
    seen = _fake_loop(monkeypatch)
    monkeypatch.setattr(cp, "_stored_warm_start", lambda body: {
        "coil_temp_c": 101.3, "magnet_temp_c": 88.6, "duty": "rated"})
    c = _coupling(client, coil_temp_c=120.0, magnet_temp_c=110.0)
    assert seen[0] == (120.0, 110.0)
    assert c["start"]["source"] == "override"


# ---------------------------------------------------------------------------
# the Br map is never carried down from a hotter magnet
# ---------------------------------------------------------------------------

def test_hot_parent_refusal_rule():
    from motor_ai_sim.simulation import fem_solver_2d as fs
    assert fs._br_carry_hot_refusal(175.0, 110.0)          # hotter parent
    assert fs._br_carry_hot_refusal(110.0, 175.0) is None  # colder parent
    assert fs._br_carry_hot_refusal(110.5, 110.0) is None  # within the band
    assert fs._br_carry_hot_refusal(None, 110.0) is None   # legacy state
    assert fs._br_carry_hot_refusal(175.0, None) is None
    assert fs._br_lineage_temp_c({"br_tmax_c": 150.0}) == 150.0
    assert fs._br_lineage_temp_c({"lib": {"br_tmax_c": 140.0}}) == 140.0
    assert fs._br_lineage_temp_c({"lib": {}}) is None
    assert fs._br_lineage_temp_c({"br_tmax_c": float("nan")}) is None


def test_the_lineage_temperature_survives_the_warm_cache_file(tmp_path,
                                                              monkeypatch):
    from motor_ai_sim.simulation import fem_solver_2d as fs
    p = tmp_path / ".warm_cache.npz"
    monkeypatch.setattr(fs, "_warm_cache_path", lambda: p)
    monkeypatch.setattr(fs, "_SB_WARM_CACHE", {})
    n = 6
    wc = {"doflocs": np.zeros((n, 2)), "A": np.zeros(n), "Ued": np.zeros(2),
          "solid": np.zeros(3),
          "meta": {"nspp": 36, "npd": 1.0, "conn": "np1", "temp": 120.0,
                   "mscale": 1.0},
          "I": 40.0, "rpm": 3000.0, "gam": 10.0, "geo_fp": "g", "nsect": 2,
          "br_val": np.ones(4), "br_cen": np.zeros((4, 2)),
          "br_tag": np.ones(4, int), "br_tmax_c": 175.0}
    fs._warm_cache_store(wc)
    monkeypatch.setattr(fs, "_SB_WARM_CACHE", {})      # force the disk read
    back = fs._warm_cache_load()
    assert back is not None and back["br_tmax_c"] == 175.0


def test_the_lineage_temperature_survives_the_warm_library(tmp_path,
                                                           monkeypatch):
    from motor_ai_sim.simulation import fem_solver_2d as fs
    from motor_ai_sim.simulation import warm_library as wl
    n = 6
    wc = {"doflocs": np.zeros((n, 2)), "A": np.zeros(n), "Ued": np.zeros(2),
          "solid": np.zeros(3),
          "meta": {"nspp": 36, "npd": 1.0, "conn": "np1", "temp": 120.0,
                   "mscale": 1.0},
          "I": 40.0, "rpm": 3000.0, "gam": 10.0, "geo_fp": "g", "nsect": 2,
          "br_val": np.ones(4), "br_cen": np.zeros((4, 2)),
          "br_tag": np.ones(4, int)}
    p = wl.store_at(tmp_path / "warm.npz", wc, key="k", op="o",
                    origin="interactive", br_repro_op="o",
                    extra_meta={"br_tmax_c": 175.0})
    assert p is not None
    back = wl.load({"_path": str(p), "_catalog": True})
    assert fs._br_lineage_temp_c(back) == 175.0


def _magnet_at(name, t_c):
    """The FEM material of a magnet card at ``t_c`` (what ``build_materials``
    hands the rotor half): magnetisation, recoil μ and the 2nd-quadrant curve."""
    from motor_ai_sim.materials import magnet_at
    from motor_ai_sim.simulation.field_ops import MU0
    m = magnet_at(name, float(t_c))
    return types.SimpleNamespace(Mx=float(m.Br) / MU0, My=0.0,
                                 mu_r=float(m.mu_rec),
                                 bh_curve=[(float(h), float(b))
                                           for h, b in m.bh_curve])


#: The stator's demagnetising field on the magnet [A/m]: past the F45SH knee
#: at 175 °C (≈ -264 kA/m), well inside the linear part at 110 °C (≈ -801).
H_LOAD = -400e3
CARD = "F45SH"


def _pass(t_c, parent):
    """ONE electromagnetic pass at ``t_c``: the carry rule decides whether the
    parent's map (``(br, lineage_tmax)``) seeds the magnet, the real ratchet
    de-rates it under H_LOAD, and the published lineage temperature follows the
    solver's own rule (this pass's, or the carried parent's if hotter)."""
    from motor_ai_sim.simulation import fem_solver_2d as fs
    from motor_ai_sim.simulation.demag import MagnetDemag
    from motor_ai_sim.simulation.field_ops import MU0
    mat = _magnet_at(CARD, t_c)
    idx = np.arange(8)
    carried = (parent is not None
               and fs._br_carry_hot_refusal(parent[1], t_c) is None)
    br = parent[0].copy() if carried else np.ones(idx.size)
    dm = MagnetDemag({1: idx}, {1: mat}, None, br)
    # B along M that makes the solver's own inversion read H_LOAD on a magnet
    # at its present remanence (demag.py: H = (B·M̂ − Br0·br) / (μ0·μ_rec))
    br0 = mat.Mx * MU0
    dm.update(MU0 * mat.mu_r * H_LOAD + br0 * br, np.zeros(idx.size))
    tmax = max(t_c, parent[1]) if carried else t_c
    return br, tmax


def test_a_hot_start_leaves_no_permanent_loss_at_the_settled_temperature():
    """Start 175 °C, the loop settles at 110 °C."""
    pristine_110, _ = _pass(110.0, None)
    # 110 °C alone: no irreversible loss (the 0.02 % is the card's own recoil
    # slope against its declared mu_rec, see demag._descriptor)
    assert float(pristine_110.min()) > 0.999
    p1 = _pass(175.0, None)
    assert float(p1[0].min()) < 0.98               # the hot start did burn
    p2 = _pass(130.0, p1)
    p3 = _pass(110.0, p2)
    np.testing.assert_allclose(p3[0], pristine_110, atol=1e-12)
    assert p3[1] == 110.0
    # …whereas the map carried regardless (the rule before 2026-11-03) keeps
    # the start's loss for ever: min() never heals
    assert float(np.minimum(p1[0], pristine_110).min()) < 0.98


def test_a_machine_that_really_runs_hot_keeps_its_loss():
    """Start below, the loop settles at 175 °C: the loss is the 175 °C one,
    exactly as a cold run at 175 °C gives it."""
    pristine_175, _ = _pass(175.0, None)
    p1 = _pass(110.0, None)
    p2 = _pass(150.0, p1)
    p3 = _pass(175.0, p2)
    assert float(p3[0].min()) < 0.98
    # the same loss a cold run at 175 °C finds (to the 0.02 % recoil-slope
    # trace the colder passes left on the carried map)
    np.testing.assert_allclose(p3[0], pristine_175, rtol=1e-3)
    assert p3[1] == 175.0


def test_a_warming_loop_is_unchanged():
    """A start at or below the settled temperature carries every map exactly
    as before (the rule never refuses a colder parent)."""
    from motor_ai_sim.simulation import fem_solver_2d as fs
    temps = [80.0, 95.0, 104.0, 108.5, 109.6, 110.0]
    parent_t = None
    for t in temps:
        assert fs._br_carry_hot_refusal(parent_t, t) is None
        parent_t = t if parent_t is None else max(parent_t, t)
