"""SVPWM / third-harmonic injection in the Controller's modulator (2026-09-26).

The Controller used plain sine-triangle only, linear to m = 1, which
under-reads the fundamental a bridge can build on a given link by 2/sqrt(3)
against space-vector PWM (docs/CONTROLLER_MODULE_2026-09-22.md §10.2).  The
choice is now ``pwm_modulation`` = ``sine`` (default, unchanged) | ``svpwm``
(min-max zero sequence) | ``third_harmonic`` (1/6), read by the loss model
AND by the ``drive: "inverter"`` voltage source.

What is pinned here, on the physics rather than on the plumbing:

* the LINE voltage: the zero sequence cancels pulse by pulse, so below m = 1
  every carrier's line volt-seconds are the sine modulator's exactly, and up
  to m = 2/sqrt(3) the line fundamental stays sqrt(3)*m*V_dc/2 where sine
  clips and loses it;
* star / delta: the injection is a common mode — a star machine's floating
  neutral removes it and a delta branch (a line-to-line difference) never
  sees it; the delta bridge's leg currents stay blind to a circulating
  triplen, which remains the winding's own;
* devices and losses: same leg current, same conduction and switching, same
  per-switch split, same DC-link mean — the modulation changes the reachable
  m and the ripple, not the silicon's bill at a given point;
* every input validates loudly.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from motor_ai_sim.inverter import losses as lo
from motor_ai_sim.inverter import waveforms as wf
from motor_ai_sim.inverter.coupling import (DeviceDrop, InverterVoltageSource,
                                            build_inverter_source, leg_currents,
                                            pole_error_volts)
from motor_ai_sim.simulation.excitation import make_source
from motor_ai_sim.simulation.pwm import (MAX_MODULATION_INDEX, SVPWM_LINEAR_LIMIT,
                                         ExcitationError, PwmVoltageSource,
                                         build_pwm_source, modulation_ceiling)

REAL = "IMCQ120R004M2H"
V_DC = 750.4
F_EL = 1183.3            # L155 rated, 14 200 rpm, 5 pole pairs
F_SW = 24000.0
INJECTED = ("svpwm", "third_harmonic")


def _src(m, modulation, carriers=48, v_bus=2.0):
    return PwmVoltageSource(pole_pairs=1, daxis_deg=0.0, v_delta_deg=0.0,
                            v_bus=v_bus, carriers=carriers, m=m,
                            modulation=modulation)


def _poles(src, n_per_carrier=64):
    """Exact per-interval pole means of the three legs over one period."""
    n = src.carriers * n_per_carrier
    d = 360.0 / n
    out = {}
    for k, s in zip("ABC", (0.0, -120.0, 120.0)):
        out[k] = np.array([src._pole_mean(i * d, (i + 1) * d, s)
                           for i in range(n)])
    th = np.radians((np.arange(n) + 0.5) * d)
    return out, th


def _harm(x, th, h):
    return 2.0 * abs(np.mean(x * np.exp(-1j * h * th)))


# ---------------------------------------------------------------------------
# 1 · the modulator the coupled drive marches (simulation/pwm.py)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mod", INJECTED)
def test_line_volt_seconds_per_carrier_are_the_sine_modulators(mod):
    """Below m = 1 nothing clips, so the zero sequence — sampled at the same
    instant for all three legs — cancels from every line voltage exactly,
    carrier by carrier."""
    m = 0.9
    sine, inj = _src(m, "sine"), _src(m, mod)
    d = 360.0 / sine.carriers
    for j in range(sine.carriers):
        a, b = j * d, (j + 1) * d
        ll_s = sine._pole_mean(a, b, 0.0) - sine._pole_mean(a, b, -120.0)
        ll_i = inj._pole_mean(a, b, 0.0) - inj._pole_mean(a, b, -120.0)
        assert ll_i == pytest.approx(ll_s, abs=1e-12)
    # …while the POLE voltages do differ: the injection is really there.
    assert (abs(inj._pole_mean(0.0, d, 0.0) - sine._pole_mean(0.0, d, 0.0))
            > 1e-3)


@pytest.mark.parametrize("m", [0.9, 1.1, 1.15])
@pytest.mark.parametrize("mod", INJECTED)
def test_line_voltage_amplitude_is_root3_m_half_bus_up_to_two_over_root3(m, mod):
    src = _src(m, mod)
    v, th = _poles(src)
    v1_ll = _harm(v["A"] - v["B"], th, 1)
    # Unit half-bus (v_bus = 2): the line fundamental is sqrt(3)*m.  The
    # regular-sampled hold costs sinc(pi/(2*48)) ~ 0.02 %.
    assert v1_ll == pytest.approx(math.sqrt(3.0) * m, rel=5e-4)
    # No low-order distortion on the line: the 5th and 7th stay under 0.1 %.
    assert _harm(v["A"] - v["B"], th, 5) < 1e-3 * v1_ll
    assert _harm(v["A"] - v["B"], th, 7) < 1e-3 * v1_ll


def test_sine_clips_where_svpwm_does_not():
    """The same reference at m = 1.15: sine loses > 4 % of its line
    fundamental to the duty clamp and grows a 5th; SVPWM keeps both."""
    m = 1.15
    vs, th = _poles(_src(m, "sine"))
    vv, _ = _poles(_src(m, "svpwm"))
    want = math.sqrt(3.0) * m
    ll_s, ll_v = vs["A"] - vs["B"], vv["A"] - vv["B"]
    assert _harm(ll_s, th, 1) < 0.96 * want
    assert _harm(ll_v, th, 1) == pytest.approx(want, rel=5e-4)
    assert _harm(ll_s, th, 5) > 20.0 * _harm(ll_v, th, 5)


@pytest.mark.parametrize("mod", INJECTED)
def test_the_injection_is_zero_sequence_and_a_star_neutral_removes_it(mod):
    """STAR: the common mode (v_A+v_B+v_C)/3 carries the injected triplen at
    the fundamental's third harmonic, and the phase-to-neutral voltage
    v_A - CM — what the floating-neutral star circuit integrates — has the
    same per-carrier means as the sine modulator's."""
    m = 0.9
    vi, th = _poles(_src(m, mod))
    vs, _ = _poles(_src(m, "sine"))
    cm_i = (vi["A"] + vi["B"] + vi["C"]) / 3.0
    cm_s = (vs["A"] + vs["B"] + vs["C"]) / 3.0
    assert _harm(cm_i, th, 3) > 0.1 * m          # the injection, at 3·f_el
    assert _harm(cm_s, th, 3) < 1e-3
    ph_i, ph_s = vi["A"] - cm_i, vs["A"] - cm_s
    n = 64                                          # per carrier
    per_i = ph_i.reshape(-1, n).mean(axis=1)
    per_s = ph_s.reshape(-1, n).mean(axis=1)
    assert per_i == pytest.approx(per_s, abs=1e-12)
    # …and the phase fundamental is m·V_dc/2, triplen-free.
    assert _harm(ph_i, th, 1) == pytest.approx(m, rel=5e-4)
    assert _harm(ph_i, th, 3) < 1e-3


@pytest.mark.parametrize("mod", INJECTED)
def test_a_delta_branch_never_sees_the_injection(mod):
    """DELTA: each branch sees a LINE voltage.  Their zero sequence
    (v_AB + v_BC + v_CA)/3 is identically zero, so the modulator drives no
    circulating current — the triplen that circulates in a delta is the
    winding's own (back-EMF), never the controller's injection."""
    vi, th = _poles(_src(1.1, mod))
    vab, vbc, vca = vi["A"] - vi["B"], vi["B"] - vi["C"], vi["C"] - vi["A"]
    assert np.max(np.abs(vab + vbc + vca)) < 1e-12
    for v in (vab, vbc, vca):
        assert _harm(v, th, 3) < 1e-9
        assert _harm(v, th, 9) < 1e-9


def test_the_bridge_outside_a_delta_does_not_carry_a_circulating_triplen():
    """A circulating (zero-sequence) branch current stays inside the delta:
    the leg currents the device model reads are unchanged by it, whatever
    the modulation."""
    th = np.radians(np.arange(0.0, 360.0, 7.5))
    for t in th:
        base = {"A": 300.0 * math.cos(t), "B": 300.0 * math.cos(t - 2.094395),
                "C": 300.0 * math.cos(t + 2.094395)}
        circ = 40.0 * math.cos(3.0 * t)                 # the triplen
        with_circ = {k: v + circ for k, v in base.items()}
        a = leg_currents(base, star_delta="delta")
        b = leg_currents(with_circ, star_delta="delta")
        for k in "ABC":
            assert b[k] == pytest.approx(a[k], abs=1e-9)


def test_the_ceiling_is_the_modulations_own():
    assert modulation_ceiling("sine") == MAX_MODULATION_INDEX == 1.15
    for mod in INJECTED:
        assert modulation_ceiling(mod) == pytest.approx(2.0 / math.sqrt(3.0))
    m = 1.154                                   # 99.94 % of 2/sqrt(3)
    kw = dict(pole_pairs=1, daxis_deg=0.0, v_phase_peak=0.5 * m * V_DC,
              v_delta_deg=0.0, v_bus=V_DC, f_switch_hz=48 * F_EL,
              f_elec_hz=F_EL)
    with pytest.raises(ExcitationError, match="linear-modulation limit"):
        build_pwm_source(**kw)                            # sine: refused
    src = build_pwm_source(**kw, modulation="svpwm")
    assert src.modulation == "svpwm"
    assert src.applied_fundamental()[0] == pytest.approx(0.5 * m * V_DC,
                                                         rel=1e-4)
    with pytest.raises(ExcitationError, match="svpwm"):
        build_pwm_source(**dict(kw, v_phase_peak=0.5 * 1.16 * V_DC),
                         modulation="svpwm")


@pytest.mark.parametrize("carriers", [7, 14, 20])
@pytest.mark.parametrize("mod", INJECTED)
def test_the_compensated_fundamental_is_the_one_the_winding_receives(carriers,
                                                                     mod):
    """On a carrier count that is not a multiple of 3 the held zero sequence
    leaks a common-mode fundamental into the POLE; the factory must hit the
    requested fundamental on the DIFFERENTIAL part, v_A - CM, which is what a
    star neutral or a delta branch actually passes on."""
    v1 = 0.5 * 1.1 * V_DC
    src = build_pwm_source(pole_pairs=1, daxis_deg=0.0, v_phase_peak=v1,
                           v_delta_deg=13.0, v_bus=V_DC,
                           f_switch_hz=carriers * F_EL, f_elec_hz=F_EL,
                           modulation=mod)
    assert src.carriers == carriers
    v, th = _poles(src)
    ph = v["A"] - (v["A"] + v["B"] + v["C"]) / 3.0
    assert _harm(ph, th, 1) == pytest.approx(v1, rel=2e-4)


def test_an_unknown_modulation_is_refused_by_name():
    with pytest.raises(ExcitationError, match="spwm"):
        build_pwm_source(pole_pairs=1, daxis_deg=0.0, v_phase_peak=300.0,
                         v_delta_deg=0.0, v_bus=V_DC, f_switch_hz=F_SW,
                         f_elec_hz=F_EL, modulation="spwm")


def test_the_default_modulator_is_bit_identical():
    """Default = sine: the source built without the argument IS the old one."""
    kw = dict(pole_pairs=5, daxis_deg=12.0, v_phase_peak=300.0,
              v_delta_deg=-20.0, v_bus=V_DC, f_switch_hz=F_SW, f_elec_hz=F_EL)
    a, b = build_pwm_source(**kw), build_pwm_source(**kw, modulation="sine")
    assert a == b
    assert a.modulation == "sine"


# ---------------------------------------------------------------------------
# 2 · the Controller's bridge in the coupled drive (inverter/coupling.py)
# ---------------------------------------------------------------------------

def _drop():
    return DeviceDrop(r_ds_ohm=0.002, v_sd_v0_V=3.0, v_sd_rd_ohm=0.005,
                      dead_time_s=0.5e-6, device="TEST", t_j_c=125.0)


@pytest.mark.parametrize("mod", ("sine",) + INJECTED)
@pytest.mark.parametrize("i_leg", [+400.0, -400.0])
def test_the_dead_time_identity_holds_on_the_injected_edges(mod, i_leg):
    """The clamp reads the SAME comparator's edges, so the textbook
    -sign(i)·t_d·f_sw·(V_dc + 2·V_SD) holds for every modulation."""
    src = build_pwm_source(pole_pairs=1, daxis_deg=0.0,
                           v_phase_peak=0.5 * 1.1 * V_DC if mod != "sine"
                           else 0.4 * V_DC,
                           v_delta_deg=0.0, v_bus=V_DC, f_switch_hz=F_SW,
                           f_elec_hz=F_EL, modulation=mod)
    drop = _drop()
    deg_per_s = 360.0 * F_EL
    edges = np.linspace(0.0, 360.0, src.carriers * 20 + 1)
    acc = sum(pole_error_volts(modulator=src, drop=drop, phase="A",
                               i_leg_A=i_leg, psi_a_deg=float(a),
                               psi_b_deg=float(b), v_dc_real_V=V_DC,
                               deg_per_s=deg_per_s) * (b - a)
              for a, b in zip(edges[:-1], edges[1:])) / 360.0
    want = (-math.copysign(1.0, i_leg) * drop.dead_time_s * src.carriers * F_EL
            * (V_DC + 2.0 * drop.v_sd(i_leg)) - i_leg * drop.r_ds_ohm)
    assert acc == pytest.approx(want, rel=1e-3)


def test_the_factory_carries_the_controllers_modulation_to_the_solver():
    src = make_source(
        "inverter", pole_pairs=5, daxis_deg=0.0, I_phase_rms=314.0,
        v_phase_peak=0.5 * 1.12 * V_DC * math.sqrt(3.0), v_delta_deg=-10.0,
        v_bus=V_DC * math.sqrt(3.0), v_bus_real=V_DC, f_switch=F_SW,
        f_elec=F_EL, star_delta="delta",
        inverter_nonideal={"r_ds_ohm": 0.0025, "v_sd_v0_V": 3.1,
                           "v_sd_rd_ohm": 0.004, "dead_time_s": 5e-7,
                           "device": REAL, "modulation": "svpwm"})
    assert isinstance(src, InverterVoltageSource)
    assert src.modulator.modulation == "svpwm"
    # m = 1.12 on the REAL link: past sine's linear 1, inside SVPWM's.
    assert src.modulator.m == pytest.approx(1.12, rel=2e-3)
    d = src.describe({"f_elec": F_EL, "n_periods": 1.0,
                      "n_steps_per_period": 400})
    assert d["pwm"]["modulation"] == "svpwm"
    assert "zero-sequence" in d["pwm"]["modulator"]
    # …and without the key it is the sine bridge it always was.
    src0 = make_source(
        "inverter", pole_pairs=5, daxis_deg=0.0, v_phase_peak=300.0,
        v_bus=V_DC, f_switch=F_SW, f_elec=F_EL,
        inverter_nonideal={"r_ds_ohm": 0.0025, "v_sd_v0_V": 3.1,
                           "v_sd_rd_ohm": 0.004, "dead_time_s": 5e-7})
    assert src0.modulator.modulation == "sine"
    assert "sine-triangle WITH" in src0.describe({})["pwm"]["modulator"]


def test_the_injection_changes_no_current_the_ideal_machine_draws():
    """A star R-L-EMF load marched on the ideal bridge: the zero sequence
    falls on the floating neutral, so the phase current is the sine
    modulator's."""
    r, l_h, e_pk = 0.02, 40e-6, 180.0
    out = {}
    for mod in ("sine", "svpwm"):
        src = build_inverter_source(
            pole_pairs=1, daxis_deg=0.0, v_phase_peak=300.0, v_delta_deg=0.0,
            v_bus_model=V_DC, v_dc_real=V_DC, f_switch_hz=F_SW, f_elec_hz=F_EL,
            drop=DeviceDrop(r_ds_ohm=0.0, v_sd_v0_V=0.0, v_sd_rd_ohm=0.0,
                            dead_time_s=0.0),
            star_delta="star", modulation=mod)
        mod_src = src.modulator
        n = mod_src.carriers * 40
        dt = 1.0 / (F_EL * n)
        i = np.zeros(3)
        rec = []
        for per in range(8):
            for k in range(n):
                a, b = k * 360.0 / n, (k + 1) * 360.0 / n
                v = mod_src.mean_voltages(a, b)
                vv = np.array([v["A"], v["B"], v["C"]])
                vv -= vv.mean()                          # floating neutral
                th = math.radians(0.5 * (a + b))
                e = e_pk * np.array([math.cos(th - 0.5),
                                     math.cos(th - 0.5 - 2.094395),
                                     math.cos(th - 0.5 + 2.094395)])
                i = (i + dt / l_h * (vv - e)) / (1.0 + dt * r / l_h)
                if per == 7:
                    rec.append(i[0])
        out[mod] = np.array(rec)
    rms_s = math.sqrt(np.mean(out["sine"] ** 2))
    rms_v = math.sqrt(np.mean(out["svpwm"] ** 2))
    th = np.radians((np.arange(out["sine"].size) + 1.0) * 360.0
                    / out["sine"].size)
    assert _harm(out["svpwm"], th, 1) == pytest.approx(
        _harm(out["sine"], th, 1), rel=2e-3)
    assert rms_v == pytest.approx(rms_s, rel=5e-3)


# ---------------------------------------------------------------------------
# 3 · the loss model (inverter/losses.py, inverter/waveforms.py)
# ---------------------------------------------------------------------------

def _req(**kw):
    req = dict(num_slots=12, num_poles=10, single_layer=True,
               star_delta="delta", device=REAL, devices_parallel=5,
               v_dc_V=V_DC, i_phase_rms_A=314.25, p_ac_W=272.2e3,
               f_elec_hz=F_EL, f_carrier_hz=F_SW, modulation_index=0.6333,
               topology="one_3ph", dead_time_us=0.5)
    req.update(kw)
    return req


@pytest.mark.parametrize("mod", INJECTED)
def test_device_currents_and_losses_are_the_sine_modulators(mod):
    """Same point, same load current: conduction and switching do not see
    the zero sequence (the leg current is always in a channel; one hard
    on/off per carrier either way)."""
    a = lo.solve_controller(_req())
    b = lo.solve_controller(_req(pwm_modulation=mod))
    for k in ("conduction_W", "third_quadrant_W", "switching_W", "total_W"):
        assert b["losses"][k] == a["losses"][k]
    assert b["thermal"]["t_j_max_c"] == a["thermal"]["t_j_max_c"]
    for la, lb in zip(a["bridges"][0]["legs"], b["bridges"][0]["legs"]):
        for k in ("i_leg_rms_A", "i_switch_rms_A", "i_device_rms_A",
                  "i_device_peak_A"):
            assert lb[k] == la[k]
    assert a["point"]["pwm_modulation"] == "sine"
    assert b["point"]["pwm_modulation"] == mod
    assert b["settings"]["pwm_modulation"] == mod
    assert b["bridges"][0]["modulation"] == mod
    assert b["point"]["modulation_linear_limit"] == pytest.approx(
        2.0 / math.sqrt(3.0), abs=1e-4)
    assert any(mod in n and "CONTINUOUS" in n for n in b["model_notes"])


@pytest.mark.parametrize("mod", INJECTED)
def test_the_dc_link_mean_is_the_same_power_the_ripple_is_the_modulations(mod):
    """sum(v0 * i) = v0 * sum(i) = 0: the injection moves no mean power, so
    the bus mean is P/V_dc for both on a grid fine enough to resolve it."""
    a = lo.solve_controller(_req(samples_per_carrier=400))
    b = lo.solve_controller(_req(samples_per_carrier=400, pwm_modulation=mod))
    p_over_v = 272.2e3 / V_DC
    assert a["dc_link"]["i_dc_mean_A"] == pytest.approx(p_over_v, rel=2e-3)
    assert b["dc_link"]["i_dc_mean_A"] == pytest.approx(p_over_v, rel=2e-3)


@pytest.mark.parametrize("mod", INJECTED)
def test_the_two_switches_of_a_leg_still_share_its_conduction_equally(mod):
    """The loss model halves a leg's loss between its switches.  The zero
    sequence is half-wave symmetric (v0(x+180) = -v0(x)), so the high side's
    share of i^2 stays exactly one half."""
    s = wf.ModulatorSetup(f_elec_hz=F_EL, f_carrier_hz=F_SW, v_dc_V=V_DC,
                          modulation_index=1.1, samples_per_carrier=200,
                          modulation=mod)
    u = s.grid()
    i = 500.0 * np.sin(2.0 * math.pi * u - 0.4)
    st = wf.switch_states(s, u, 0.0)
    share = float(np.sum(st * i ** 2) / np.sum(i ** 2))
    assert share == pytest.approx(0.5, abs=2e-3)


@pytest.mark.parametrize("mod", INJECTED)
def test_the_loss_models_switching_functions_give_the_full_line_voltage(mod):
    """m = 1.15 on the loss model's own switching functions: the line
    fundamental is sqrt(3)·m·V_dc/2 with injection, clipped without."""
    m = 1.15
    out = {}
    for mm in ("sine", mod):
        s = wf.ModulatorSetup(f_elec_hz=F_EL, f_carrier_hz=F_SW, v_dc_V=V_DC,
                              modulation_index=m, samples_per_carrier=200,
                              modulation=mm)
        u = s.grid()
        v_ab = V_DC * (wf.switch_states(s, u, 0.0)
                       - wf.switch_states(s, u, -120.0))
        out[mm] = 2.0 * abs(np.mean(v_ab * np.exp(-2j * math.pi * u)))
    want = math.sqrt(3.0) * m * V_DC / 2.0
    assert out[mod] == pytest.approx(want, rel=2e-3)
    assert out["sine"] < 0.96 * want


def test_overmodulation_is_judged_against_the_modulations_linear_limit():
    m = 1.1                                   # past 1, inside 2/sqrt(3)
    sine = lo.solve_controller(_req(modulation_index=m))
    svp = lo.solve_controller(_req(modulation_index=m, pwm_modulation="svpwm"))
    assert any("OVERMODULATED" in w for w in sine["warnings"])
    assert not any("OVERMODULATED" in w or "linear limit" in w
                   for w in svp["warnings"])
    over = lo.solve_controller(_req(modulation_index=1.2,
                                    pwm_modulation="svpwm"))
    assert any("OVERMODULATED for svpwm" in w for w in over["warnings"])


def test_svpwm_carries_the_power_sine_cannot_at_the_same_current():
    """The §10.2 gap, closed: at a fixed current and power factor the power
    the bridge can deliver in its linear range grows by 2/sqrt(3), at the
    SAME device losses."""
    i_ph, pf = 314.25, 0.95
    i_leg = i_ph * math.sqrt(3.0)
    p_max = {mod: 3.0 * lim * V_DC / (2.0 * math.sqrt(2.0)) * i_leg * pf
             for mod, lim in lo.PWM_LINEAR_LIMIT.items()}
    assert p_max["svpwm"] / p_max["sine"] == pytest.approx(2.0 / math.sqrt(3.0))
    p = 0.999 * p_max["svpwm"]
    sine = lo.solve_controller(_req(modulation_index=None, power_factor=pf,
                                    p_ac_W=p))
    svp = lo.solve_controller(_req(modulation_index=None, power_factor=pf,
                                   p_ac_W=p, pwm_modulation="svpwm"))
    assert svp["point"]["modulation_index"] == pytest.approx(
        0.999 * SVPWM_LINEAR_LIMIT, rel=1e-3)
    assert any("OVERMODULATED" in w for w in sine["warnings"])
    assert not any("OVERMODULATED" in w for w in svp["warnings"])
    assert svp["losses"]["total_W"] == sine["losses"]["total_W"]


def test_a_bad_modulation_is_refused_by_name():
    with pytest.raises(lo.ControllerRefusal) as exc:
        lo.solve_controller(_req(pwm_modulation="spwm"))
    assert "pwm_modulation" in str(exc.value) and "spwm" in str(exc.value)


def test_an_h_bridge_topology_refuses_a_three_phase_zero_sequence():
    with pytest.raises(lo.ControllerRefusal) as exc:
        lo.solve_controller(_req(topology="h_bridge", pwm_modulation="svpwm"))
    assert exc.value.code == "modulation_needs_three_phase"


def test_the_h_bridge_waveform_keeps_its_own_sine():
    """coil_waveforms builds H-bridge legs on sine whatever the setup says."""
    s = wf.ModulatorSetup(f_elec_hz=F_EL, f_carrier_hz=F_SW, v_dc_V=V_DC,
                          modulation_index=0.6, samples_per_carrier=40,
                          modulation="svpwm")
    u = s.grid()
    from dataclasses import replace
    a = wf.switch_states(replace(s, modulation="sine"), u, 0.0)
    b = wf.switch_states(s, u, 0.0)
    assert not np.array_equal(a, b)            # the injection is real…

    class _Leg:
        def __init__(self, name, coils):
            self.name, self.coils = name, coils

    class _HB:
        id, kind, coils = "HB1", "h_bridge", [1]
        legs = [_Leg("P", [1]), _Leg("N", [1])]

    legs = {("HB1", "P"): {"i_peak_A": 100.0, "i_phase_deg": 0.0,
                           "v_phase_deg": 0.0},
            ("HB1", "N"): {"i_peak_A": -100.0, "i_phase_deg": 0.0,
                           "v_phase_deg": 180.0}}
    w_inj = wf.coil_waveforms(bridges=[_HB()], setup=s, legs=legs,
                              r_ds_ohm=0.0, v_sd=lambda x: 0.0 * x,
                              dead_samples=0)
    w_sin = wf.coil_waveforms(bridges=[_HB()],
                              setup=replace(s, modulation="sine"), legs=legs,
                              r_ds_ohm=0.0, v_sd=lambda x: 0.0 * x,
                              dead_samples=0)
    assert w_inj["coils"]["1"]["v_V"] == w_sin["coils"]["1"]["v_V"]


# ---------------------------------------------------------------------------
# 4 · the routes: validation, keys, and the coupled loop's ceiling
# ---------------------------------------------------------------------------

def test_the_transient_route_validates_and_keys_the_modulation():
    import inspect
    from motor_ai_sim.routes import simulation as S
    src = inspect.getsource(S.get_fem_transient)
    assert 'inv_modulation:      str   = "sine"' in src
    # Keyed only off sine, so every key written before the choice is unchanged.
    assert 'if _inv_mod != "sine":\n            _inv_key += "/" + _inv_mod' in src
    assert '"modulation": _inv_mod,' in src
    assert "_MAX_M = _mod_ceiling(_inv_mod)" in src


def test_the_controller_loop_passes_the_modulation_and_keys_it(monkeypatch):
    from motor_ai_sim.routes.coupled import _ControllerLoop
    cfg = {
        "device": REAL, "topology": "one_3ph", "devices_parallel": 3,
        "dead_time_us": 0.5, "v_gs_on_V": 18.0, "v_gs_off_V": 0.0,
        "r_g_ext_ohm": None, "e_oss_policy": "included_in_eon",
        "set_split": "series_split", "h_bridge_modulation": "unipolar",
        "cooling": {}, "r_tim_k_w": 0.03, "t_j_start_c": 130.0, "notes": [],
        "sources": {},
    }
    inv = {"v_dc_V": V_DC, "f_carrier_hz": F_SW, "target_I_phase_rms_A": 314.3}
    sine = _ControllerLoop(cfg, inverter=inv, star_delta="delta", rpm=14200.0,
                           pole_pairs=5)
    svp = _ControllerLoop(dict(cfg, pwm_modulation="svpwm"), inverter=inv,
                          star_delta="delta", rpm=14200.0, pole_pairs=5)
    assert "inv_modulation" not in sine.run_kwargs()       # unchanged
    assert svp.run_kwargs()["inv_modulation"] == "svpwm"
    assert svp.snap_excitation() == sine.snap_excitation() + "/svpwm"


def test_the_coupled_ceiling_follows_the_controllers_modulation(monkeypatch):
    """The regulator's voltage ceiling in the coupled loop is the modulation's:
    2/sqrt(3) with SVPWM, and the sampled-hold gain alone (no clamp loss)."""
    from motor_ai_sim.routes import coupled as C
    monkeypatch.setattr(C, "_duty_controller_record", lambda: {})
    from motor_ai_sim.inverter import drive_source as DS
    monkeypatch.setattr(DS, "controller_block_for", lambda *a, **k: {})
    assert C._controller_pwm_modulation({}) == (
        "sine", "the module default (sine-triangle)")
    assert C._controller_pwm_modulation(
        {"controller": {"pwm_modulation": "SVPWM"}})[0] == "svpwm"
    with pytest.raises(Exception) as exc:
        C._controller_pwm_modulation({"controller": {"pwm_modulation": "x"}})
    assert "pwm_modulation" in str(getattr(exc.value, "detail", exc.value))
    monkeypatch.setattr(DS, "controller_block_for",
                        lambda *a, **k: {"pwm_modulation": "third_harmonic"})
    assert C._controller_pwm_modulation({}) == (
        "third_harmonic",
        "the Controller settings saved with the configuration")
    g_sine = C._modulator_gain(20, 0.0)
    g_svp = C._modulator_gain(20, 0.0, "svpwm")
    assert g_sine < 0.97                  # sine at 1.15 is clipping
    assert g_svp == pytest.approx(1.0, abs=2e-3)   # the sampled hold alone
