"""The controller PWM final pass of a coupled ``drive: "inverter"`` run
(CR-HF-75 G1-L170 generator, server runs 2026-10-29): the rotor iron it hands
to the thermal solve, the pass its limits are judged on, and the direction the
bridge is solved in.  ``motor_ai_sim.coupled_pwm_final`` and its wiring in
``routes/coupled.py`` / ``inverter/losses.py``.
"""
from __future__ import annotations

import pytest

from motor_ai_sim import coupled_pwm_final as pf

# The G1-L170 100 kW-cont PWM pass, as the server reported it.
OPEN = {"P_core_rotor_W": 639.0, "P_core_W": 2000.0, "P_loss_total_W": 5000.0,
        "P_loss_rotor_W": 1500.0, "P_elec_in_W": -95000.0,
        "P_mech_W": 100000.0, "op_mode": "generator", "efficiency": 0.95,
        "demag_settled": False, "eddy_settled": True,
        "P_core_terms": {"rotor": {
            "window_closed": False, "window_method": "open_one_period_window",
            "surface_selected_candidate": "raw_window_unfiltered",
            "surface_raw_window_candidate_W": 639.0,
            "surface_detrended_candidate_W": 120.0}}}


def _sine(w):
    return {"P_core_rotor_W": w, "demag_settled": True,
            "P_core_terms": {"rotor": {"window_closed": True}}}


# ── 1. which rotor iron ────────────────────────────────────────────────────

def test_open_window_takes_the_larger_settled_candidate_never_the_raw():
    s = pf.rotor_iron_selection(OPEN, _sine(110.0))
    assert s["candidate"] == "pwm_detrended_open_window"
    assert s["selected_W"] == pytest.approx(120.0)
    assert s["factor"] == pytest.approx(120.0 / 639.0)
    assert s["raw_W"] == 639.0 and s["window_closed"] is False
    assert s["settled"] is True and s["refuse"] is False
    assert s["demag_settled"] is False and s["sine_demag_settled"] is True
    assert "OPEN" in s["reason"] and "639.0" in s["reason"]

    s2 = pf.rotor_iron_selection(OPEN, _sine(130.0))
    assert s2["candidate"] == "sine_state_closed_window"
    assert s2["selected_W"] == pytest.approx(130.0)


def test_closed_pwm_window_is_used_as_solved():
    closed = {**OPEN, "P_core_terms": {"rotor": {"window_closed": True,
                                                 "window_method": "x"}}}
    s = pf.rotor_iron_selection(closed, _sine(110.0))
    assert s["candidate"] == "pwm_closed_window" and s["factor"] == 1.0
    assert s["selected_W"] == 639.0


def test_open_window_without_a_settled_candidate_refuses():
    no_det = {**OPEN, "P_core_terms": {"rotor": {"window_closed": False}}}
    s = pf.rotor_iron_selection(no_det, {"P_core_rotor_W": 100.0,
                                         "P_core_terms": {"rotor": {
                                             "window_closed": False}}})
    assert s["refuse"] is True and s["candidate"] is None
    assert "not fed to the thermal solve" in s["reason"]


def test_summary_carries_the_selected_rotor_iron_and_its_sums():
    s = pf.rotor_iron_selection(OPEN, _sine(110.0))
    out = pf.apply_to_summary(OPEN, s)
    d = 120.0 - 639.0
    assert out["P_core_rotor_W"] == pytest.approx(120.0)
    assert out["P_core_rotor_open_window_raw_W"] == 639.0
    assert out["P_core_W"] == pytest.approx(2000.0 + d)
    assert out["P_loss_total_W"] == pytest.approx(5000.0 + d)
    assert out["P_loss_rotor_W"] == pytest.approx(1500.0 + d)
    assert out["P_elec_in_W"] == pytest.approx(-95000.0 + d)
    # generator: eta = (P_in - losses) / P_in
    assert out["efficiency"] == pytest.approx((100000.0 - 4481.0) / 100000.0,
                                              abs=1e-4)
    assert out["P_core_rotor_selection"]["candidate"] == \
        "pwm_detrended_open_window"
    assert OPEN["P_core_rotor_W"] == 639.0            # the input is untouched


def test_loss_map_rotor_iron_scaled_only_on_the_rotor_core():
    from motor_ai_sim.simulation.sb_domains import DOM_ROTOR, DOM_STATOR

    s = pf.rotor_iron_selection(OPEN, _sine(110.0))
    m = {"domain_per_tri": [DOM_ROTOR, DOM_STATOR, DOM_ROTOR],
         "loss_density_per_tri": [10.0, 3.0, 5.0],
         "P_fe_W": 2000.0, "P_loss_total_exact_W": 5000.0}
    out = pf.scale_map_rotor_iron(m, s, rotor_tag=DOM_ROTOR)
    f = 120.0 / 639.0
    assert out["loss_density_per_tri"] == pytest.approx([10.0 * f, 3.0, 5.0 * f])
    assert out["P_fe_W"] == pytest.approx(2000.0 - 519.0)
    assert out["P_loss_total_exact_W"] == pytest.approx(5000.0 - 519.0)
    assert out["rotor_iron_selection"]["candidate"] == \
        "pwm_detrended_open_window"
    assert m["loss_density_per_tri"] == [10.0, 3.0, 5.0]   # cache untouched


# ── 2. the verdict on the last pass ────────────────────────────────────────

_WITHIN = {"within_limits": True, "limiting_part": None, "judged": ["magnet"],
           "limits_c": {"magnet": 180.0}, "at_point_c": {"magnet": 121.0},
           "note": "... so it may be held for ever"}
_OVER = {"within_limits": False, "limiting_part": "magnet",
         "over_parts": ["magnet"], "over_by_K": {"magnet": 60.0},
         "limits_c": {"magnet": 180.0}, "at_point_c": {"magnet": 240.0},
         "time_to_limit_s": 900.0, "note": "runs 15 m, then the magnet"}


def test_the_final_pass_verdict_replaces_the_sine_one():
    out = pf.judge_final_state(_OVER, _WITHIN, final_pass="pwm_final")
    assert out["within_limits"] is False and out["judged_on"] == "pwm_final"
    assert out["sine_state"]["within_limits"] is True
    assert "sine state was inside every limit" in out["note"]
    assert list(pf.over_limit_parts(out)) == ["magnet"]


def test_unjudgeable_final_map_is_read_against_the_sine_limits():
    field = {"components": {"magnet": {"avg": 238.6, "max": 241.0}}}
    out = pf.judge_final_state(None, _WITHIN, final_pass="pwm_final",
                               final_field=field)
    assert out["within_limits"] is False
    assert out["over_by_K"] == {"magnet": pytest.approx(61.0)}
    assert out["judged_on"] == "sine_state"
    assert "held for ever" not in out["note"]
    ok = pf.judge_final_state(None, _WITHIN, final_pass="pwm_final",
                              final_field={"components": {"magnet": {
                                  "max": 150.0}}})
    assert ok["within_limits"] is True


# ── 3. the bridge's direction ──────────────────────────────────────────────

@pytest.mark.parametrize("summary,mode,want", [
    ({"P_elec_in_W": -95000.0, "op_mode": "generator"}, None, "generator"),
    ({"P_elec_in_W": 12000.0, "op_mode": "motor"}, "generator", "motor"),
    ({"op_mode": "generator"}, None, "generator"),
    ({}, "generator", "generator"),
    ({}, None, "motor"),
])
def test_direction_follows_the_machine_power_flow(summary, mode, want):
    d, src = pf.bridge_power_direction(summary, mode)
    assert d == want and src


def test_controller_request_carries_the_generator_direction():
    from motor_ai_sim.routes import coupled as cp

    c = cp._ControllerLoop.__new__(cp._ControllerLoop)
    c.cfg = {"device": "d", "devices_parallel": 1, "topology": "one_3ph",
             "set_split": "x", "h_bridge_modulation": "unipolar",
             "dead_time_us": 0.2, "v_gs_on_V": 18.0, "v_gs_off_V": -3.0,
             "e_oss_policy": "p", "r_tim_k_w": 0.1}
    c.inverter = {"v_dc_V": 750.0, "f_carrier_hz": 20000.0}
    c.star_delta, c.rpm, c.pole_pairs, c.t_j_c = "star", 4000.0, 4, 120.0
    c.warnings = []
    c.body_mode = None
    req = c._solve_request({"summary": {"P_loss_total_W": 4000.0,
                                        "efficiency": 0.96,
                                        "P_elec_in_W": -96000.0,
                                        "op_mode": "generator"},
                            "I_phase_rms_solved_A": 164.0,
                            "pwm": {"modulation_index": 0.85}})
    assert req["power_direction"] == "generator"
    # generator: P_ac = P_loss * eta / (1 - eta) — the AC side, not the shaft
    assert req["p_ac_W"] == pytest.approx(4000.0 * 0.96 / 0.04)
    assert c.power_direction[0] == "generator"


def test_generator_efficiency_is_shaft_to_dc():
    from motor_ai_sim.inverter import losses as lo
    from tests.test_inverter_ripple import _sa

    ge = lo.solve_controller(_sa(power_direction="generator",
                                 efficiency_shaft=0.96))
    mo = lo.solve_controller(_sa(power_direction="motor",
                                 efficiency_shaft=0.96))
    assert ge["dc_link"]["power_flow"] == "machine -> link"
    assert ge["efficiency"]["wall_to_shaft_direction"] == "shaft -> DC link"
    assert ge["efficiency"]["shaft_to_dc"] == ge["efficiency"]["wall_to_shaft"]
    assert ge["efficiency"]["wall_to_shaft"] == pytest.approx(
        ge["efficiency"]["inverter"] * 0.96, abs=1e-5)
    assert "shaft-to-DC" in ge["efficiency"]["note"]
    assert mo["efficiency"]["wall_to_shaft_direction"] == "DC link -> shaft"
    assert "shaft_to_dc" not in mo["efficiency"]


# ── the route, end to end on the faked loop ────────────────────────────────

from tests.test_coupled_sine_comparison import _post, client, rig  # noqa: E402,F401


def test_final_pass_feeds_a_settled_rotor_and_judges_itself(client, rig,
                                                            monkeypatch):
    from motor_ai_sim.routes import coupled as cp
    from motor_ai_sim.simulation.sb_domains import DOM_ROTOR, DOM_STATOR

    real_em = cp._em_run

    def _em(body, **kw):
        out = real_em(body, **kw)
        s = dict(out["summary"])
        if kw.get("controller") is not None:            # the PWM pass
            s.update({k: OPEN[k] for k in ("P_core_rotor_W", "P_core_terms",
                                           "demag_settled")})
        else:
            s.update(_sine(110.0))
        return dict(out, summary=s)
    monkeypatch.setattr(cp, "_em_run", _em, raising=True)

    def _map(body, em, inv, *, coil_temp_c, magnet_temp_c, controller=None):
        return ({"domain_per_tri": [DOM_ROTOR, DOM_STATOR],
                 "loss_density_per_tri": [639.0, 3.0]}, {"kind": "pwm_run"})
    monkeypatch.setattr(cp, "_pwm_loss_map", _map, raising=True)

    maps_seen: list = []

    def _th(body, cooling, *, coil_temp_c, magnet_temp_c, rpm, **k):
        pwm = k.get("em_map") is not None
        if pwm:
            maps_seen.append(k["em_map"])
        m = 240.0 if pwm else 121.0
        return {"ok": True,
                "components": {"winding": {"avg": 150.0, "max": 160.0},
                               "magnet": {"avg": m, "max": m + 2.0}}}
    monkeypatch.setattr(cp, "_thermal_solve", _th, raising=True)

    def _ttl(body, cooling, em_summary, field, **_k):
        hot = ((field or {}).get("components") or {}).get("magnet", {}) \
            .get("avg", 0.0) > 180.0
        return dict(_OVER if hot else _WITHIN)
    monkeypatch.setattr(cp, "_ttl_step", _ttl, raising=True)

    c = _post(client, drive="inverter", fresh=True)
    blk = c["pwm_final"]
    # 1. the thermal solve saw the selected rotor iron, never the raw window
    assert maps_seen and all(
        m["loss_density_per_tri"][0] == pytest.approx(120.0)
        for m in maps_seen)
    assert blk["rotor_iron"]["candidate"] == "pwm_detrended_open_window"
    assert blk["rotor_iron"]["raw_W"] == 639.0
    assert all(p["rotor_iron_candidate"] == "pwm_detrended_open_window"
               and p["P_core_rotor_raw_W"] == 639.0
               for p in blk["passes"])
    assert c["em"].get("P_core_rotor_W", 120.0) == pytest.approx(120.0)
    # 2. the verdict is the PWM pass's, the sine one beside it
    ttl = c["time_to_limit"]
    assert ttl["within_limits"] is False and ttl["judged_on"] == "pwm_final"
    assert ttl["sine_state"]["within_limits"] is True
    assert blk["over_limit_parts"] == ["magnet"]
    assert blk["within_limits"] is False
