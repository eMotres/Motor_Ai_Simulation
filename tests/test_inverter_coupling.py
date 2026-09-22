"""Controller Stage 2 — the bridge that drives the electromagnetic solver.

Four things are pinned here and they are the four things that could be wrong:

1. **the dead-time distortion is the textbook one** — integrated over a whole
   electrical period, :func:`pole_error_volts` must equal
   ``−sign(i)·t_d·f_sw·(V_dc + 2·V_SD) − i·R_DS(on)`` exactly, at a fine step
   AND at one step per carrier (the loss integral and the picture must not be
   two different opinions about the same clamp);
2. **the iteration reaches the right current** — a synthetic R–L–EMF machine
   driven by this source, marched with the same feedback the FEM hands it,
   must land on the fundamental current the phasor algebra predicts: exactly
   for an ideal bridge, and lower by the dead-time error's own fundamental
   ``(4/π)·ΔV`` plus the channel drop for the real one;
3. **the record shape** — what a coupled run writes about its controller;
4. **the old path is untouched** — ``drive: "pwm"`` is still resolved, still
   builds the ideal source, and ``"inverter"`` is no longer an alias for it.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from motor_ai_sim.inverter.coupling import (DeviceDrop, InverterVoltageSource,
                                            build_inverter_source,
                                            fit_device_drop, leg_currents,
                                            pole_error_volts)
from motor_ai_sim.simulation.excitation import Feedback, make_source
from motor_ai_sim.simulation.pwm import build_pwm_source

F_EL = 1183.3          # L155 motor rated, 5 pole pairs at 14 200 rpm
F_SW = 24000.0
V_DC = 750.4


def _modulator(m_v1=0.4):
    return build_pwm_source(pole_pairs=1, daxis_deg=0.0,
                            v_phase_peak=m_v1 * V_DC, v_delta_deg=0.0,
                            v_bus=V_DC, f_switch_hz=F_SW, f_elec_hz=F_EL)


def _drop(dead_us=0.5, r_ds=0.002, v0=3.0, rd=0.005):
    return DeviceDrop(r_ds_ohm=r_ds, v_sd_v0_V=v0, v_sd_rd_ohm=rd,
                      dead_time_s=dead_us * 1e-6, device="TEST", t_j_c=125.0,
                      devices_parallel=1)


def _period_mean_error(mod, drop, i_leg, n_sub):
    """Mean pole-voltage error over one electrical period, on ``n_sub`` steps."""
    deg_per_s = 360.0 * F_EL
    edges = np.linspace(0.0, 360.0, n_sub + 1)
    acc = 0.0
    for a, b in zip(edges[:-1], edges[1:]):
        acc += pole_error_volts(
            modulator=mod, drop=drop, phase="A", i_leg_A=i_leg,
            psi_a_deg=float(a), psi_b_deg=float(b), v_dc_real_V=V_DC,
            deg_per_s=deg_per_s) * (b - a)
    return acc / 360.0


# ---------------------------------------------------------------------------
# 1 · the dead-time distortion, against the closed form
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("i_leg", [+400.0, -400.0, +40.0])
@pytest.mark.parametrize("n_sub_per_carrier", [1, 20, 200])
def test_dead_time_error_matches_the_closed_form(i_leg, n_sub_per_carrier):
    mod, drop = _modulator(), _drop()
    f_sw_eff = mod.carriers * F_EL
    want = (-math.copysign(1.0, i_leg) * drop.dead_time_s * f_sw_eff
            * (V_DC + 2.0 * drop.v_sd(i_leg))
            - i_leg * drop.r_ds_ohm)
    got = _period_mean_error(mod, drop, i_leg,
                             mod.carriers * n_sub_per_carrier)
    assert got == pytest.approx(want, rel=1e-3, abs=1e-3)


def test_the_error_is_a_square_wave_in_the_sign_of_the_current():
    """Opposite currents give opposite dead-time errors and opposite drops."""
    mod, drop = _modulator(), _drop()
    a = _period_mean_error(mod, drop, +400.0, mod.carriers * 20)
    b = _period_mean_error(mod, drop, -400.0, mod.carriers * 20)
    assert a < 0.0 < b
    assert a == pytest.approx(-b, rel=1e-6)
    # …and it is worth what the note says it is worth on this link.
    assert 8.0 < abs(a) < 12.0


def test_no_dead_time_leaves_only_the_channel_drop():
    mod = _modulator()
    drop = _drop(dead_us=0.0)
    got = _period_mean_error(mod, drop, 400.0, mod.carriers * 20)
    assert got == pytest.approx(-400.0 * drop.r_ds_ohm, rel=1e-9)


def test_an_ideal_device_changes_nothing():
    """Zero resistance, zero dead time: the non-ideal source IS the ideal one."""
    mod = _modulator()
    drop = DeviceDrop(r_ds_ohm=0.0, v_sd_v0_V=0.0, v_sd_rd_ohm=0.0,
                      dead_time_s=0.0)
    assert _period_mean_error(mod, drop, 400.0, mod.carriers * 8) == 0.0


# ---------------------------------------------------------------------------
# 2 · the leg current the devices really carry
# ---------------------------------------------------------------------------

def test_leg_current_in_star_is_the_branch_current_times_the_paths():
    out = leg_currents({"A": 10.0, "B": -4.0, "C": -6.0}, star_delta="star",
                       n_parallel=3)
    assert out == {"A": 30.0, "B": -12.0, "C": -18.0}


def test_leg_current_in_delta_is_root_three_and_thirty_degrees():
    """The bridge sits OUTSIDE the delta: i_leg_A = i_AB − i_CA."""
    peak = 100.0
    th = np.radians(np.arange(0.0, 360.0, 0.25))
    legs = [leg_currents(
        {"A": peak * math.cos(t), "B": peak * math.cos(t - 2 * math.pi / 3),
         "C": peak * math.cos(t + 2 * math.pi / 3)},
        star_delta="delta")["A"] for t in th]
    arr = np.asarray(legs)
    assert float(np.max(arr)) == pytest.approx(math.sqrt(3.0) * peak, rel=1e-3)
    # …and it LAGS the branch by 30°: the peak sits at +30° of rotor angle.
    assert float(th[int(np.argmax(arr))]) == pytest.approx(
        math.radians(30.0), abs=math.radians(0.5))
    # The three legs sum to zero — a three-wire bridge, always.
    for t in th[::37]:
        lg = leg_currents(
            {"A": peak * math.cos(t), "B": peak * math.cos(t - 2 * math.pi / 3),
             "C": peak * math.cos(t + 2 * math.pi / 3)}, star_delta="delta")
        assert sum(lg.values()) == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# 3 · the iteration, on a synthetic R–L–EMF machine
# ---------------------------------------------------------------------------

def _march_rl_emf(src, *, r_ohm, l_h, e_peak, e_phase_deg, periods=14,
                  steps_per_period=None):
    """March ``L·di/dt + R·i = v_phase − e`` with the source in the loop.

    An isolated-neutral star circuit, so only the DIFFERENCES of the applied
    pole voltages drive anything: the common mode is removed exactly as the
    solver's own circuit removes it.  Trapezoidal, which is the solver's
    Crank–Nicolson on a linear circuit, and the source is asked for the step
    MEAN and handed the PREVIOUS step's current — the same contract
    ``fem_transient_sliding_band`` honours.

    Returns the last period's phase-A current samples.
    """
    n = int(steps_per_period or (src.carriers * 20))
    dt = 1.0 / (F_EL * n)
    dth = 360.0 / n                      # pole_pairs = 1 → mech = elec
    i = {k: 0.0 for k in ("A", "B", "C")}
    out = []
    k = 0
    for rep in range(periods):
        for j in range(n):
            th0 = (rep * n + j) * dth
            fb = Feedback(k=k, theta_prev_deg=th0, theta_deg=th0 + dth,
                          t0_s=k * dt, t1_s=(k + 1) * dt, fine=True,
                          i_abc=dict(i), psi_abc=None, v_bus=V_DC)
            v = src.mean_over(fb)
            cm = (v["A"] + v["B"] + v["C"]) / 3.0
            th_mid = math.radians(th0 + 0.5 * dth)
            nxt = {}
            for idx, ph in enumerate(("A", "B", "C")):
                e = e_peak * math.cos(th_mid - idx * 2 * math.pi / 3
                                      + math.radians(e_phase_deg))
                drv = (v[ph] - cm) - e
                # trapezoidal: (L/dt + R/2)·i1 = (L/dt − R/2)·i0 + drv
                nxt[ph] = ((l_h / dt - 0.5 * r_ohm) * i[ph] + drv) / \
                          (l_h / dt + 0.5 * r_ohm)
            i = nxt
            if rep == periods - 1:
                out.append(i["A"])
            k += 1
    return np.asarray(out)


def _fundamental(series):
    """(rms, phase[deg]) of the fundamental of one settled period."""
    n = series.size
    th = 2.0 * math.pi * np.arange(n) / n
    c = 2.0 / n * float(np.sum(series * np.cos(th)))
    s = 2.0 / n * float(np.sum(series * np.sin(th)))
    return math.hypot(c, s) / math.sqrt(2.0), math.degrees(math.atan2(-s, c))


def _analytic_rms(v1_peak, e_peak, r_ohm, x_ohm, *, dv_peak=0.0):
    """``|V₁ − E₁| / |Z|``, with the dead time as the RESISTANCE it really is.

    The dead-time error is a square wave in the SIGN of the current, so its own
    fundamental ``(4/π)·ΔV`` sits in antiphase with the current — not with the
    applied voltage.  On an inductive machine those are nearly 90° apart, so
    subtracting it from ``V₁`` as a scalar is simply the wrong sum: what it
    does is add

        ``R_dt = (4/π)·ΔV / Î``

    in series, and ``Î`` is itself the answer.  Two fixed-point passes settle
    it to well under a per cent (the correction is a few per cent of ``|Z|``).
    """
    z0 = abs(complex(r_ohm, x_ohm))
    dv = abs(float(v1_peak) - float(e_peak))
    i_pk = dv / z0
    for _ in range(40):
        r_dt = (dv_peak / i_pk) if (dv_peak and i_pk > 0.0) else 0.0
        i_new = dv / abs(complex(r_ohm + r_dt, x_ohm))
        if abs(i_new - i_pk) < 1e-9 * max(i_pk, 1.0):
            i_pk = i_new
            break
        i_pk = i_new
    return i_pk / math.sqrt(2.0)


def test_ideal_bridge_reaches_the_analytic_fundamental_current():
    """No device: the marched current is the phasor answer, to under a per cent."""
    r, l = 0.02, 40e-6
    x = 2.0 * math.pi * F_EL * l
    v1, e1 = 300.0, 180.0
    src = build_inverter_source(
        pole_pairs=1, daxis_deg=0.0, v_phase_peak=v1, v_delta_deg=0.0,
        v_bus_model=V_DC, v_dc_real=V_DC, f_switch_hz=F_SW, f_elec_hz=F_EL,
        drop=DeviceDrop(r_ds_ohm=0.0, v_sd_v0_V=0.0, v_sd_rd_ohm=0.0,
                        dead_time_s=0.0),
        star_delta="star", n_parallel=1)
    got, _ = _fundamental(_march_rl_emf(src, r_ohm=r, l_h=l, e_peak=e1,
                                        e_phase_deg=0.0))
    want = _analytic_rms(v1, e1, r, x)
    assert got == pytest.approx(want, rel=0.01)


def test_the_device_costs_exactly_the_dead_time_and_the_channel():
    """The same machine on a real bridge draws less, by the predicted amount."""
    r, l = 0.02, 40e-6
    x = 2.0 * math.pi * F_EL * l
    v1, e1 = 300.0, 180.0
    drop = _drop(dead_us=0.5, r_ds=0.003, v0=3.0, rd=0.0)
    src = build_inverter_source(
        pole_pairs=1, daxis_deg=0.0, v_phase_peak=v1, v_delta_deg=0.0,
        v_bus_model=V_DC, v_dc_real=V_DC, f_switch_hz=F_SW, f_elec_hz=F_EL,
        drop=drop, star_delta="star", n_parallel=1)
    got, _ = _fundamental(_march_rl_emf(src, r_ohm=r, l_h=l, e_peak=e1,
                                        e_phase_deg=0.0))
    ideal = _analytic_rms(v1, e1, r, x)
    assert got < ideal                      # the device can only cost current

    f_sw_eff = src.modulator.carriers * F_EL
    dv = drop.dead_time_s * f_sw_eff * (V_DC + 2.0 * drop.v_sd_v0_V)
    # The square wave's fundamental, plus the channel folded into the loop
    # resistance (the drop is −i·R on the pole, i.e. one more series ohm).
    want = _analytic_rms(v1, e1, r + drop.r_ds_ohm, x,
                         dv_peak=4.0 / math.pi * dv)
    assert got == pytest.approx(want, rel=0.01)
    # …and the cost is worth naming: ~1 % of the current on this machine.
    assert 0.005 < (ideal - got) / ideal < 0.05


def test_the_source_reports_the_leg_current_it_saw():
    drop = _drop()
    src = build_inverter_source(
        pole_pairs=1, daxis_deg=0.0, v_phase_peak=300.0, v_delta_deg=0.0,
        v_bus_model=V_DC, v_dc_real=V_DC, f_switch_hz=F_SW, f_elec_hz=F_EL,
        drop=drop, star_delta="star", n_parallel=2)
    _march_rl_emf(src, r_ohm=0.02, l_h=40e-6, e_peak=180.0, e_phase_deg=0.0,
                  periods=4)
    m = src.measured()
    assert m["fine_steps"] > 0
    assert m["i_leg_peak_A"] > 0.0
    d = src.describe({"f_elec": F_EL, "n_periods": 1.0,
                      "n_steps_per_period": src.carriers * 20})
    ni = d["pwm"]["nonideal"]
    assert ni["source"] == "controller"
    assert ni["device"] == "TEST"
    assert ni["dead_time_us"] == pytest.approx(0.5)
    assert "dead time" in d["pwm"]["modulator"]


# ---------------------------------------------------------------------------
# 4 · the card, reduced to what a time loop can afford
# ---------------------------------------------------------------------------

def test_the_body_diode_fit_tracks_the_card():
    card = pytest.importorskip(
        "motor_ai_sim.inverter.devices").get_device("IMCQ120R004M2H")
    drop = fit_device_drop(card, t_j_c=130.0, n_parallel=3,
                           i_leg_peak_A=770.0, dead_time_s=0.5e-6)
    assert drop.devices_parallel == 3
    assert drop.r_ds_ohm == pytest.approx(
        card.r_ds_on_ohm(130.0, 18.0) / 3.0, rel=1e-9)
    # The straight line must track the card's own curve over the span the
    # dead-time windows visit — the number the record prints.
    assert drop.v_sd_fit_max_err_V < 0.1
    assert drop.v_sd_fit_from_A == pytest.approx(0.1 * 770.0 / 3.0)
    for i in (100.0, 200.0, 500.0, 700.0):
        assert drop.v_sd(i) == pytest.approx(
            card.v_sd_V(i / 3.0, 130.0, 0.0), abs=0.1)
    # The TOE is outside the fit and the model says so by over-reading it —
    # which is the direction a clamp model must err in, and it is worth
    # ~2·v0 of a 750 V link while the current crosses zero.
    assert drop.v_sd(0.0) > card.v_sd_V(0.0, 130.0, 0.0)


def test_a_hotter_junction_is_a_higher_resistance():
    card = pytest.importorskip(
        "motor_ai_sim.inverter.devices").get_device("IMCQ120R004M2H")
    cold = fit_device_drop(card, t_j_c=60.0, n_parallel=1, i_leg_peak_A=400.0)
    hot = fit_device_drop(card, t_j_c=160.0, n_parallel=1, i_leg_peak_A=400.0)
    assert hot.r_ds_ohm > cold.r_ds_ohm * 1.3


# ---------------------------------------------------------------------------
# 5 · the factory, and the old path
# ---------------------------------------------------------------------------

def test_the_factory_builds_the_controller_bridge():
    src = make_source(
        "inverter", pole_pairs=5, daxis_deg=0.0, I_phase_rms=314.0,
        gamma_deg=0.0, n_parallel=1, v_phase_peak=300.0, v_delta_deg=-10.0,
        v_bus=V_DC, f_switch=F_SW, f_elec=F_EL, star_delta="star",
        inverter_nonideal={"r_ds_ohm": 0.0025, "v_sd_v0_V": 3.1,
                           "v_sd_rd_ohm": 0.004, "dead_time_s": 5e-7,
                           "device": "IMCQ120R004M2H", "t_j_c": 132.0,
                           "devices_parallel": 3, "topology": "one_3ph"})
    assert isinstance(src, InverterVoltageSource)
    assert src.name == "inverter"
    assert src.kind == "V"
    assert src.drop.device == "IMCQ120R004M2H"
    assert src.drop.devices_parallel == 3


def test_the_factory_refuses_an_inverter_with_no_device():
    with pytest.raises(Exception) as exc:
        make_source("inverter", pole_pairs=5, daxis_deg=0.0,
                    v_phase_peak=300.0, v_bus=V_DC, f_switch=F_SW,
                    f_elec=F_EL)
    assert "device" in str(exc.value)


def test_the_delta_substitution_keeps_the_device_on_the_real_link():
    """The drops are computed on V_dc, never on the √3 model bus."""
    src = make_source(
        "inverter", pole_pairs=5, daxis_deg=0.0, v_phase_peak=520.0,
        v_delta_deg=0.0, v_bus=V_DC * math.sqrt(3.0), v_bus_real=V_DC,
        f_switch=F_SW, f_elec=F_EL, star_delta="delta",
        inverter_nonideal={"r_ds_ohm": 0.0025, "v_sd_v0_V": 3.1,
                           "v_sd_rd_ohm": 0.004, "dead_time_s": 5e-7})
    assert src.v_dc_real_V == pytest.approx(V_DC)
    assert src.modulator.v_bus == pytest.approx(V_DC * math.sqrt(3.0))
    assert src.star_delta == "delta"


def test_the_ideal_pwm_path_is_untouched():
    """``pwm_voltage`` still builds the ideal source and still says so."""
    src = make_source("pwm_voltage", pole_pairs=5, daxis_deg=0.0,
                      v_phase_peak=300.0, v_delta_deg=0.0, v_bus=V_DC,
                      f_switch=F_SW, f_elec=F_EL)
    assert src.name == "pwm_voltage"
    assert not isinstance(src, InverterVoltageSource)
    d = src.describe({"f_elec": F_EL, "n_periods": 1.0,
                      "n_steps_per_period": 400})
    assert d["pwm"]["modulator"].startswith("ideal two-level")
    assert "nonideal" not in d["pwm"]


def test_inverter_is_no_longer_an_alias_of_pwm_in_the_coupled_loop():
    from motor_ai_sim.routes.coupled import _coupled_drive
    assert _coupled_drive({"drive": "pwm"}) == "pwm"
    assert _coupled_drive({"drive": "pwm_voltage"}) == "pwm"
    assert _coupled_drive({"drive": "sine"}) == "current"
    assert _coupled_drive({}) == "current"
    assert _coupled_drive({"drive": "inverter"}) == "inverter"
    assert _coupled_drive({"drive": "controller"}) == "inverter"


# ---------------------------------------------------------------------------
# 6 · the record a coupled run writes about its controller
# ---------------------------------------------------------------------------

def _loop(monkeypatch=None):
    from motor_ai_sim.routes.coupled import _ControllerLoop
    cfg = {
        "device": "IMCQ120R004M2H", "topology": "one_3ph",
        "devices_parallel": 3, "dead_time_us": 0.5, "dead_time_floor_ns": 106.3,
        "v_gs_on_V": 18.0, "v_gs_off_V": 0.0, "r_g_ext_ohm": None,
        "e_oss_policy": "included_in_eon", "set_split": "series_split",
        "h_bridge_modulation": "unipolar", "cooling": {"t_in_c": 65.0,
                                                       "flow_lpm": 8.0},
        "r_tim_k_w": 0.03, "t_j_start_c": 130.0, "notes": [],
        "sources": {"device": "the request"},
    }
    inv = {"v_dc_V": V_DC, "f_carrier_hz": F_SW,
           "target_I_phase_rms_A": 314.3}
    return _ControllerLoop(cfg, inverter=inv, star_delta="delta",
                           rpm=14200.0, pole_pairs=5)


def test_the_controller_loop_feeds_the_run_four_scalars():
    lp = _loop()
    kw = lp.run_kwargs()
    assert set(kw) == {"inv_r_ds_ohm", "inv_v_sd_v0_V", "inv_v_sd_rd_ohm",
                       "inv_dead_time_us", "inv_device",
                       "inv_devices_parallel", "inv_t_j_c", "inv_topology"}
    assert kw["inv_dead_time_us"] == pytest.approx(0.5)
    assert kw["inv_devices_parallel"] == 3
    assert kw["inv_device"] == "IMCQ120R004M2H"
    assert kw["inv_r_ds_ohm"] > 0.0
    # The snapshot key must carry the DEVICE, or two runs whose silicon sits at
    # different temperatures would be served for each other.
    assert lp.snap_excitation().startswith("750.4/24000/")
    assert lp.snap_excitation().count("/") == 5


def test_the_record_carries_the_excitation_and_the_passes():
    lp = _loop()
    em = {
        "summary": {"I1_phase_rms_A": 314.3, "efficiency_shaft": 0.9771,
                    "P_loss_total_incl_mech_W": 6230.0},
        "I_phase_rms_solved_A": 314.3,
        "pwm": {"modulation_index": 0.633,
                "nonideal": {"source": "controller", "device": "IMCQ120R004M2H",
                             "dead_time_us": 0.5, "dead_time_error_V": 9.8}},
    }
    d_tj = lp.step(em, it=1)
    assert d_tj is not None
    rec = lp.record(em)
    assert rec["source"] == "controller"
    assert rec["stage"] == 2 and rec["coupled"] is True
    assert rec["excitation"]["dead_time_error_V"] == 9.8
    assert rec["settings_resolved"]["device"] == "IMCQ120R004M2H"
    assert rec["settings_resolved"]["devices_parallel"] == 3
    assert rec["t_j_c"] == pytest.approx(lp.t_j_c, abs=0.01)
    assert len(rec["passes"]) == 1
    assert rec["device_drop"]["device"] == "IMCQ120R004M2H"
    # The Stage 1 shape is kept whole — the report reads the same keys.
    for k in ("losses", "thermal", "efficiency", "limits", "bridges", "point",
              "device_row", "provenance", "settings"):
        assert k in rec, k
    # …and the waveform arrays are NOT in a record — only their summary.
    assert "waveforms" not in rec
    ws = rec["waveform_summary"]
    assert ws["dead_time_us"] == pytest.approx(0.5)
    assert ws["f_elec_hz"] == pytest.approx(1183.33, abs=0.5)
    assert ws["dead_time_error_V"] > 0.0
    # One rms per COIL: six on this winding, and they must all be there.
    assert set(ws["v_coil_rms_V"]) == {"1", "2", "3", "4", "5", "6"}
    assert all(v is not None for v in ws["v_coil_rms_V"].values())
    assert set(ws["i_coil_rms_A"]) == set(ws["v_coil_rms_V"])
    assert not any(isinstance(v, list) for v in ws.values())
    assert rec["efficiency"]["wall_to_shaft"] is not None


def test_a_hotter_pass_moves_the_device_the_next_run_is_solved_with():
    lp = _loop()
    r0 = lp.drop.r_ds_ohm
    em = {
        "summary": {"I1_phase_rms_A": 420.0, "efficiency_shaft": 0.97,
                    "P_loss_total_incl_mech_W": 9000.0},
        "I_phase_rms_solved_A": 420.0,
        "pwm": {"modulation_index": 0.7},
    }
    lp.step(em, it=1)
    assert lp.t_j_c != 130.0
    assert lp.drop.r_ds_ohm != r0          # the card was re-read at the new T_j
    assert lp.drop.t_j_c == pytest.approx(lp.t_j_c)


# ---------------------------------------------------------------------------
# 7 · the report rows
# ---------------------------------------------------------------------------

def _col_with(controller_block, *, em=None, sine=None, standalone=None):
    res = {"coupled": {"drive": "inverter", "em": em or {},
                       "controller": controller_block}}
    if sine is not None:
        res["coupled"]["reference_sine"] = sine
    if standalone is not None:
        res["controller"] = standalone
    return {"duty": "rated 1x9 mm", "res": res}


def test_the_coupled_controller_block_wins_over_the_tab_solve():
    from motor_ai_sim import report as R
    col = _col_with({"coupled": True, "device": "A"}, standalone={"device": "B"})
    assert R.controller_record(col)["device"] == "A"
    assert R.controller_is_coupled(R.controller_record(col))
    # …and with no coupled block the tab's solve is still the record.
    col2 = {"duty": "d", "res": {"controller": {"device": "B"}}}
    assert R.controller_record(col2)["device"] == "B"
    assert not R.controller_is_coupled(R.controller_record(col2))


def test_the_coupled_rows_difference_the_sine_reference():
    from motor_ai_sim import report as R
    em = {"P_stranded_W": 3000.0, "P_core_W": 1800.0, "P_mag_W": 40.0,
          "P_sleeve_W": 5.0, "P_shaft_W": 10.0, "P_loss_total_W": 4855.0,
          "T_em_avg_Nm": 178.9, "I1_phase_rms_A": 314.3, "efficiency": 0.9786}
    sine = {"em": {"P_stranded_W": 2400.0, "P_core_W": 1500.0, "P_mag_W": 39.0,
                   "P_sleeve_W": 5.0, "P_shaft_W": 10.0,
                   "P_loss_total_W": 3954.0, "T_em_avg_Nm": 179.4,
                   "I1_phase_rms_A": 314.1, "efficiency": 0.9824}}
    rec = {"coupled": True, "losses": {"total_W": 4151.0},
           "efficiency": {"inverter": 0.985, "shaft": 0.9771,
                          "wall_to_shaft": 0.9624},
           "passes": [{"iter": 1, "d_t_j_K": 12.0}], "t_j_c": 132.0,
           "t_j_tol_K": 2.0,
           "excitation": {"dead_time_us": 0.5, "dead_time_error_V": 9.8,
                          "r_ds_on_mohm_device": 6.6, "t_j_c": 132.0}}
    rows = R.controller_coupled_rows(rec, _col_with(rec, em=em, sine=sine))
    assert rows[0] == ["Quantity", "Sine reference",
                       "On the controller's waveform", "Difference"]
    flat = {r[0]: r for r in rows[1:]}
    assert "2,400" in flat["Copper"][1] and "3,000" in flat["Copper"][2]
    assert flat["Copper"][3].startswith("+600")
    assert "4,151" in flat["Inverter loss"][2]
    assert "96.24" in flat["Wall-to-shaft efficiency"][2]
    # A standalone record has nothing to compare and says so by staying empty.
    assert R.controller_coupled_rows({"coupled": False}, _col_with({})) == []


def test_the_excitation_and_convergence_lines_name_the_numbers():
    from motor_ai_sim import report as R
    rec = {"coupled": True, "t_j_c": 132.0, "t_j_tol_K": 2.0,
           "passes": [{"iter": 1}, {"iter": 2}],
           "t_j_residual_K": -0.8,
           "excitation": {"dead_time_us": 0.5, "dead_time_error_V": 9.8,
                          "r_ds_on_mohm_device": 6.6, "t_j_c": 132.0}}
    ex = R.controller_excitation_text(rec)
    assert "0.5 µs dead time" in ex and "9.8 V" in ex
    cv = R.controller_convergence_text(rec)
    assert "2 pass" in cv and "132" in cv and "0.8 K" in cv
    assert R.controller_excitation_text({"coupled": True}) == ""


def test_a_stored_pwm_record_still_reads_as_a_bridge():
    """Item 5 of the plan: old records keep their answer."""
    from motor_ai_sim import report as R
    from motor_ai_sim import duty_results as DR
    assert R.record_drive({"drive": "pwm"}) == "pwm"
    assert R.record_drive({"drive": "pwm_voltage"}) == "pwm"
    assert R.record_drive({"drive": "inverter"}) == "pwm"
    assert R.record_drive({}) == "sine"
    assert DR._entry_drive({"drive": "inverter"}) == "pwm"
    assert DR._entry_drive({"drive": "pwm"}) == "pwm"


def test_the_ac_power_points_the_right_way_on_a_generator():
    """A generator's shaft efficiency is P_ac/P_shaft, not the other way."""
    lp = _loop()
    base = {"I1_phase_rms_A": 314.3, "efficiency_shaft": 0.98,
            "P_loss_total_incl_mech_W": 6000.0}
    pwm = {"modulation_index": 0.63}
    mot = lp._solve_request({"summary": dict(base), "pwm": pwm})
    gen = lp._solve_request({"summary": {**base, "op_mode": "generator"},
                             "pwm": pwm})
    # Motor: the AC side is the bigger number (it carries the losses).
    assert mot["p_ac_W"] == pytest.approx(6000.0 * 0.98 / 0.02 + 6000.0)
    # Generator: the AC side is the smaller one — the shaft carries them.
    assert gen["p_ac_W"] == pytest.approx(6000.0 * 0.98 / 0.02)
    assert gen["p_ac_W"] < mot["p_ac_W"]


# ---------------------------------------------------------------------------
# 8 · the keys — two passes of one loop are two different runs
# ---------------------------------------------------------------------------

def _key_fields(**over):
    """The transient's own cache-key fields, read off the shipped source.

    The key is built inline in `get_fem_transient` (it closes over thirty
    locals), so the test pins the CONTRACT rather than calling it: the device's
    four physics numbers must be in the key, and they must be empty on every
    other drive.
    """
    import inspect
    from motor_ai_sim.routes import simulation as S
    return inspect.getsource(S.get_fem_transient)


def test_the_device_is_in_the_run_key():
    src = _key_fields()
    assert '("inverter_device", _inv_key)' in src, (
        "two passes of one coupled loop differ in R_DS(on) and in nothing "
        "else — without the device in the key the second is served the first")
    # …and the snapshot key carries it too, or the thermal half would read the
    # previous pass's per-element loss map.
    assert 'excitation=(_inv_key if _drive == "inverter"' in src
    # It is EMPTY on every other drive, so their keys are unchanged.
    assert '_inv_key = ""' in src
    assert 'if _drive == "inverter":' in src


def test_the_key_changes_with_the_junction_temperature():
    """The same duty at two device temperatures gives two different keys."""
    from motor_ai_sim.inverter.coupling import fit_device_drop
    from motor_ai_sim.inverter.devices import get_device
    card = get_device("IMCQ120R004M2H")

    def key(t_j):
        d = fit_device_drop(card, t_j_c=t_j, n_parallel=3, i_leg_peak_A=770.0,
                            dead_time_s=0.5e-6)
        return ("%g/%g/%g/%g/%g/%g"
                % (V_DC, F_SW, d.r_ds_ohm, d.v_sd_v0_V, d.v_sd_rd_ohm,
                   d.dead_time_s * 1e6))

    assert key(120.0) != key(140.0)
    assert key(132.0) == key(132.0)


def test_a_run_with_no_modulation_index_is_declined_not_guessed():
    """The bridge's duty cycle is not derivable from the current alone."""
    lp = _loop()
    em = {"summary": {"I1_phase_rms_A": 314.3, "efficiency_shaft": 0.9771,
                      "P_loss_total_incl_mech_W": 6230.0},
          "I_phase_rms_solved_A": 314.3}          # …and no `pwm` block at all
    assert lp._solve_request(em) is None
    assert lp.step(em, it=1) is None
    assert any("modulation index" in w for w in lp.warnings)
    assert lp.passes == []
