"""drive = "inverter" under ``solve_to`` = limits / continuous (2026-09-25).

The extra electromagnetic passes those two modes add beyond the loop — the ONE
pass "at the limit" and the S1 verification passes — called ``_em_run``
WITHOUT ``controller=``, and ``_em_run`` then silently falls back to the IDEAL
two-level bridge (no R_DS(on), no body diode, no dead time).  The record's
``controller`` block meanwhile still described the loop's last pass (its
current, its T_j).  What is pinned here:

  (a) every EM pass of both paths receives the controller;
  (b) the controller is re-solved on the limit pass and on each S1 pass, so
      the record's ``controller`` block describes the SAME state as the
      reported machine (``state.phase``, its current);
  (c) the S1 verification thermal solve is handed the run's OWN loss map
      (``_pwm_loss_map``), never a lookup keyed on a sine current, and the
      controller's diode fit is re-placed at the S1 current (``reseed``);
  (d) a verification pass that refuses leaves the controller as the last
      pass that solved;
  (e) ``_ControllerLoop.record`` states which pass it describes and flags a
      record whose current is not the one the devices were solved at.

Both halves of the loop are faked exactly as ``test_coupled_limited_state``
fakes them; nothing here solves a field.
"""
from __future__ import annotations

import pytest

from tests.test_coupled_continuous_solve_to import _RATING_BLOCK, _fake_rating
from tests.test_coupled_limited_state import LOOP_BODY, _fake, _ttl_block


class _FakeCtl:
    """Stands in for ``coupled._ControllerLoop``; records every call."""

    instances: list = []

    def __init__(self, cfg=None, **kw):
        self.t_j_c = 100.0
        self.solve = {}
        self.passes = []
        self.steps = []
        self.reseeds = []
        _FakeCtl.instances.append(self)

    def run_kwargs(self):
        return {}

    def snap_excitation(self):
        return "fake"

    def reseed(self, i):
        self.reseeds.append(float(i))

    def state(self):
        return (self.t_j_c, dict(self.solve), len(self.passes),
                list(self.reseeds))

    def restore(self, st):
        self.t_j_c, self.solve, n, _r = st
        del self.passes[n:]

    def step(self, em, *, it, phase="loop"):
        i = em.get("I_phase_rms_solved_A")
        self.steps.append((phase, i))
        self.t_j_c += 1.0
        self.solve = {"losses": {"total_W": 5.0 + len(self.steps)},
                      "efficiency": {"wall_to_shaft": 0.9}}
        self.passes.append({"iter": it, "phase": phase, "i_phase_rms_A": i,
                            "d_t_j_K": 1.0})
        return 1.0

    def record(self, em):
        return {"t_j_c": self.t_j_c,
                "state": {"phase": self.passes[-1]["phase"] if self.passes
                          else None,
                          "I_phase_rms_A": (self.passes[-1]["i_phase_rms_A"]
                                            if self.passes else None)},
                "losses": dict((self.solve or {}).get("losses") or {})}


_INV = {"f_carrier_hz": 24000.0, "v_phase_peak_V": 12.0, "v_dc_V": 22.2,
        "carriers_per_period": 16, "n_steps_per_period": 96,
        "target_I_phase_rms_A": 20.0, "i_tol_pct": 1.0,
        "v_phase_peak_max_V": 14.0, "v_phase_peak_max_uncompensated_V": 14.5,
        "schedule": "mixed", "v_delta_deg": 0.0, "modulation_index": 0.8,
        "waveform": "svpwm", "f_elec_hz": 1500.0, "record_as": "",
        "sources": {}}


def _inverter_drive(monkeypatch, seen_ctl_kw: list, maps: list):
    """Put the loop on drive=inverter with a fake controller, and record the
    ``controller`` kwarg of every electromagnetic pass."""
    from motor_ai_sim.routes import coupled as cp

    _FakeCtl.instances.clear()
    monkeypatch.setattr(cp, "_inverter_settings",
                        lambda body, *, rpm: dict(_INV), raising=True)
    monkeypatch.setattr(cp, "_controller_settings",
                        lambda body, *, rpm, inverter: {"device": "fake"},
                        raising=True)
    monkeypatch.setattr(cp, "_ControllerLoop", _FakeCtl, raising=True)
    monkeypatch.setattr(cp, "_pwm_dc_verdict",
                        lambda inv, s: (True, True, 0.0, 1.0, ""), raising=True)

    def _map(body, em, inv, *, coil_temp_c, magnet_temp_c, controller=None):
        maps.append({"controller": controller,
                     "I": float(body.get("I_phase_rms") or 0.0)})
        return {"map_of": float(body.get("I_phase_rms") or 0.0)}, {
            "kind": "pwm_run"}
    monkeypatch.setattr(cp, "_pwm_loss_map", _map, raising=True)
    monkeypatch.setattr(cp, "_regulate_v1", lambda inv, i, pts: None,
                        raising=True)
    real_em = cp._em_run

    def _em(body, **kw):
        seen_ctl_kw.append(kw.get("controller"))
        return real_em(body, **kw)
    monkeypatch.setattr(cp, "_em_run", _em, raising=True)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


def _run(client, **body):
    r = client.post("/api/coupled/run",
                    json={**LOOP_BODY, "max_iter": 3, "tol_k": 1.0,
                          "cold_constants": False, "drive": "inverter",
                          **body})
    assert r.status_code == 200, r.text[:800]
    return r.json()["coupling"]


# ---------------------------------------------------------------------------
# (a) + (b) limits
# ---------------------------------------------------------------------------

def test_limit_pass_is_solved_on_the_controller_and_the_block_is_that_pass(
        client, monkeypatch):
    seen = _fake(monkeypatch, ttl=_ttl_block())
    ctl_kw: list = []
    maps: list = []
    _inverter_drive(monkeypatch, ctl_kw, maps)

    c = _run(client, solve_to="limits")
    assert c["mode"] == "limited"
    assert seen["coil_in"] == [120.0, 200.0]          # loop pass + limit pass
    ctl = _FakeCtl.instances[-1]
    # EVERY electromagnetic pass got the controller — the limit pass too.
    assert len(ctl_kw) == 2 and all(k is ctl for k in ctl_kw)
    # …and the devices were re-solved on the limit pass itself.
    assert [p for p, _i in ctl.steps] == ["loop", "limit"]
    assert c["limited"]["drive_held"] == "inverter"
    assert c["limited"]["controller_t_j_residual_K"] == 1.0
    assert c["history"][-1]["phase"] == "limit"
    assert c["history"][-1]["T_junction_c"] == ctl.t_j_c
    assert c["controller"]["state"]["phase"] == "limit"


# ---------------------------------------------------------------------------
# (a) + (b) + (c) continuous
# ---------------------------------------------------------------------------

def test_s1_verification_passes_run_on_the_controller(client, monkeypatch):
    seen = _fake(monkeypatch, ttl=_ttl_block())
    ctl_kw: list = []
    maps: list = []
    _inverter_drive(monkeypatch, ctl_kw, maps)
    _fake_rating(monkeypatch, block=_RATING_BLOCK)
    th_kw: list = []
    from motor_ai_sim.routes import coupled as cp
    real_th = cp._thermal_solve

    def _th(body, cooling, **kw):
        th_kw.append(kw)
        return real_th(body, cooling, **kw)
    monkeypatch.setattr(cp, "_thermal_solve", _th, raising=True)

    c = _run(client, solve_to="continuous")
    ctl = _FakeCtl.instances[-1]
    # loop pass + limit pass + TWO S1 verification passes (the fake map's
    # magnet reads 95 °C against a 149.7 °C card, so pass 1 misses and one
    # correction pass follows) — every one of them on the controller.
    assert len(ctl_kw) == 4 and all(k is ctl for k in ctl_kw)
    assert [p for p, _i in ctl.steps] == ["loop", "limit", "s1_verify",
                                          "s1_verify"]
    # the diode fit was re-placed at each verification pass's own current
    assert ctl.reseeds and ctl.reseeds[0] == pytest.approx(34.36)
    # the S1 thermal solves were handed THEIR OWN run's loss map
    s1_th = th_kw[-2:]
    assert all(k.get("em_map") and k["em_map"]["map_of"] > 0 for k in s1_th)
    assert all(k.get("n_steps_per_period") == 96 for k in s1_th)
    assert all(m["controller"] is ctl for m in maps)
    cr = c["continuous_rating"]
    assert cr["record_is_s1"] is True
    assert cr["controller"]["t_j_c"] == ctl.t_j_c
    # the record's controller block is the S1 pass's
    assert c["controller"]["state"]["phase"] == "s1_verify"
    assert c["history"][-1]["phase"] == "s1_verify"
    assert c["history"][-1]["T_junction_c"] == ctl.t_j_c


# ---------------------------------------------------------------------------
# (d) a refused verification pass
# ---------------------------------------------------------------------------

def test_refused_s1_pass_restores_the_controller(monkeypatch):
    from fastapi import HTTPException

    from motor_ai_sim.routes import coupled as cp

    ctl = _FakeCtl()
    ctl.passes.append({"iter": 1, "phase": "loop", "i_phase_rms_A": 20.0,
                       "d_t_j_K": 0.5})
    calls = {"n": 0}

    def _em(body, **kw):
        calls["n"] += 1
        assert kw.get("controller") is ctl
        if calls["n"] == 2:
            raise HTTPException(status_code=422, detail="refused")
        return {"summary": {"I_phase_rms_A": float(body["I_phase_rms"])},
                "I_phase_rms_solved_A": float(body["I_phase_rms"])}
    monkeypatch.setattr(cp, "_em_run", _em, raising=True)
    monkeypatch.setattr(cp, "_pwm_loss_map",
                        lambda *a, **k: ({"m": 1}, {"kind": "pwm_run"}),
                        raising=True)
    monkeypatch.setattr(
        cp, "_thermal_solve",
        lambda body, cooling, **kw: {"components": {"magnet": {"max": 120.0}}},
        raising=True)
    out = cp._s1_verify({"I_phase_rms": 30.0}, cooling={"ambient_temp": 30.0},
                        rpm=1000.0, inverter=dict(_INV), i_estimate=30.0,
                        coil_temp_c_guess=100.0, magnet_temp_c_guess=100.0,
                        limiting_part="magnet", limit_c=150.0, controller=ctl)
    # pass 1 solved and stepped the controller; pass 2 refused — the
    # controller is back at pass 1, never at a drop fitted for pass 2.
    assert out["passes"] == 1 and out["verified"] is False
    assert [p for p, _i in ctl.steps] == ["s1_verify"]
    assert ctl.passes[-1]["phase"] == "s1_verify"
    assert out["controller_t_j_c"] == ctl.t_j_c


def test_s1_verify_without_a_controller_is_unchanged(monkeypatch):
    """Sine / pwm drives: no controller, no loss-map hand-over on sine."""
    from motor_ai_sim.routes import coupled as cp

    seen = []
    monkeypatch.setattr(
        cp, "_em_run",
        lambda body, **kw: seen.append(kw) or {
            "summary": {"I_phase_rms_A": 30.0}, "I_phase_rms_solved_A": 30.0},
        raising=True)
    th = []
    monkeypatch.setattr(
        cp, "_thermal_solve",
        lambda body, cooling, **kw: th.append(kw) or {
            "components": {"magnet": {"max": 150.0}}}, raising=True)
    out = cp._s1_verify({"I_phase_rms": 30.0}, cooling={}, rpm=1000.0,
                        inverter=None, i_estimate=30.0,
                        coil_temp_c_guess=100.0, magnet_temp_c_guess=100.0,
                        limiting_part="magnet", limit_c=150.0)
    assert out["verified"] is True
    assert seen[0].get("controller") is None
    assert th[0].get("em_map") is None
    assert "controller_t_j_c" not in out


# ---------------------------------------------------------------------------
# (e) the record says which pass it is, and whether it matches
# ---------------------------------------------------------------------------

class _Drop:
    device = "IQE050N08NM5SC"
    devices_parallel = 1
    dead_time_s = 2e-7

    def as_dict(self):
        return {"t_j_c": 101.0}


def _bare_ctl(passes):
    from motor_ai_sim.routes import coupled as cp

    c = cp._ControllerLoop.__new__(cp._ControllerLoop)
    c.cfg = {"sources": {}}
    c.inverter = {"v_dc_V": 22.2}
    c.t_j_c = 101.0
    c.solve = {"losses": {"total_W": 3.0}}
    c.passes = list(passes)
    c.warnings = []
    c.drop = _Drop()
    return c


def test_record_state_matches_the_reported_machine():
    c = _bare_ctl([{"iter": 3, "phase": "s1_verify", "i_phase_rms_A": 31.5,
                    "d_t_j_K": 0.4}])
    rec = c.record({"I_phase_rms_solved_A": 31.5})
    assert rec["state"] == {"phase": "s1_verify", "iter": 3,
                            "I_phase_rms_A": 31.5, "matches_record": True}
    assert rec["t_j_residual_K"] == 0.4


def test_record_flags_a_controller_block_from_another_state():
    c = _bare_ctl([{"iter": 2, "phase": "loop", "i_phase_rms_A": 43.8,
                    "d_t_j_K": 0.4}])
    rec = c.record({"I_phase_rms_solved_A": 31.5})
    assert rec["state"]["matches_record"] is False
    assert any("does not describe the reported state" in w
               for w in rec["warnings"])
