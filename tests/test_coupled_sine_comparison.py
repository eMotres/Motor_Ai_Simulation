"""Sine | Inverter | Δ on a drive="inverter" coupled run (owner 2026-09-25:
«нужно давать сравнение, как изменились характеристики мотора с контроллером
по сравнению с синусоидой, и тоже указывать это в отчёте»).

The record block (``coupling.sine_comparison``), the extra sine pass the
full-PWM loop makes for it, its compaction into the duty record, and the
report's table + caption.  The loop is faked exactly as
``test_coupled_limited_state`` fakes it.
"""
from __future__ import annotations

import pytest

from tests.test_coupled_inverter_limits_s1 import _FakeCtl, _inverter_drive
from tests.test_coupled_limited_state import LOOP_BODY, _fake, _ttl_block


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


def _post(client, **body):
    r = client.post("/api/coupled/run",
                    json={**LOOP_BODY, "max_iter": 3, "tol_k": 1.0,
                          "cold_constants": False, **body})
    assert r.status_code == 200, r.text[:800]
    return r.json()["coupling"]


@pytest.fixture()
def rig(monkeypatch):
    """Both halves faked, the inverter drive wired to a fake controller; the
    sine-state history emptied so every test starts from nothing."""
    from motor_ai_sim.routes import coupled as cp

    _h = getattr(cp, "_SINE_STATE_HISTORY", None)
    for e in (_h.list() if _h is not None else []):
        _h.delete(e["key"])
    seen = _fake(monkeypatch, ttl=_ttl_block())
    ctl_kw: list = []
    maps: list = []
    _inverter_drive(monkeypatch, ctl_kw, maps)
    # The PWM's own loss map heats the winding 20 K more than the sine's —
    # so the first PWM pass moves the temperatures and a second one follows.
    def _th(body, cooling, *, coil_temp_c, magnet_temp_c, rpm, **k):
        w = 420.0 if k.get("em_map") is not None else 400.0
        return {"ok": True,
                "components": {"winding": {"avg": w, "max": w + 30.0},
                               "magnet": {"avg": 93.0, "max": 95.0}}}
    monkeypatch.setattr(cp, "_thermal_solve", _th, raising=True)
    inv_kw: list = []
    real = cp._em_run

    def _em(body, **kw):
        inv_kw.append(kw.get("inverter"))
        return real(body, **kw)
    monkeypatch.setattr(cp, "_em_run", _em, raising=True)
    return {"seen": seen, "ctl": ctl_kw, "inv": inv_kw, "maps": maps}


def test_full_pwm_loop_gets_one_extra_sine_pass_at_its_own_state(client, rig):
    """Every loop pass on the controller's PWM; then ONE background pass on
    the ideal sine current at the reported state's temperatures."""
    c = _post(client, drive="inverter", inverter_coupling="full", fresh=True)
    ctl = _FakeCtl.instances[-1]
    loop = rig["ctl"][:-1]
    assert loop and all(k is ctl for k in loop)
    assert c.get("inverter_coupling") in (None, "full")
    assert "pwm_final" not in c
    # the item-3 comparison pass: one extra EM pass on the ideal sine
    assert rig["ctl"][-1] is None and rig["inv"][-1] is None
    assert c["sine_comparison"]["state"] == "loop"


def test_sine_cmp_rows_state_pct_and_pp():
    from motor_ai_sim.routes import coupled as cp

    rows = cp._sine_cmp_rows(
        {"P_loss_total_W": 100.0, "T_ripple_pct": 2.0, "T_em_avg_Nm": None},
        {"P_loss_total_W": 146.0, "T_ripple_pct": 30.8, "T_em_avg_Nm": 1.0})
    by = {r["key"]: r for r in rows}
    assert by["P_loss_total_W"]["delta"] == pytest.approx(46.0)
    assert by["P_loss_total_W"]["delta_kind"] == "pct"
    assert by["T_ripple_pct"]["delta"] == pytest.approx(28.8)
    assert by["T_ripple_pct"]["delta_kind"] == "pp"
    assert by["T_em_avg_Nm"]["delta"] is None           # no sine value


def test_report_table_and_caption_from_the_block():
    from motor_ai_sim import report as R

    sc = {"algorithm": "final_pass", "state": "loop", "has_corrected": True,
          "basis": {"coil_temp_c": 97.8, "magnet_temp_c": 104.2,
                    "corrected_coil_temp_c": 114.1,
                    "corrected_magnet_temp_c": 128.9},
          "rows": [{"key": "P_loss_total_W", "label": "Motor loss, total",
                    "unit": "W", "sine": 3830.3, "inverter": 5600.5,
                    "inverter_corrected": 5712.0, "delta": 46.215,
                    "delta_kind": "pct"}],
          "inverter": {"P_inverter_W": 4014.4, "eta_inverter_pct": 98.48,
                       "eta_wall_to_shaft_pct": 96.22}}
    col = {"res": {"coupled": {"em": {"P_loss_total_W": 5712.0},
                               "sine_comparison": sc}}}
    rows = R.controller_coupled_rows({"coupled": True}, col)
    assert rows[0] == ["Quantity", "Sine", "Inverter, same T",
                       "Inverter, own T", "Δ same T"]
    assert rows[1] == ["Motor loss, total", "3,830.3 W", "5,600.5 W",
                       "5,712 W", "+46.2 %"]
    assert rows[-1][0] == "Wall-to-shaft efficiency"
    assert all(len(r) == 5 for r in rows)
    cap = R.controller_coupled_caption(col)
    assert cap.startswith("Sine loop vs the controller's PWM")
    assert "114.1" in cap


def test_compact_coupled_keeps_controller_and_comparison():
    from motor_ai_sim import duty_results as DR

    out = {"coupling": {"drive": "inverter",
                        "controller": {"t_j_c": 101.0, "coupled": True},
                        "sine_comparison": {"rows": [{"key": "x"}]}}}
    c = DR.compact_coupled(out)
    assert c["controller"]["t_j_c"] == 101.0
    assert c["sine_comparison"]["rows"] == [{"key": "x"}]
