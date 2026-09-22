"""The controller module — cards, topology, losses, waveform, route, report.

Every physics assertion here is against something that can be checked BY HAND:
the datasheet's own table values, a closed-form conduction integral, a counted
number of switching events, and the sign of the dead-time voltage error.  The
loss model is exercised on a SYNTHETIC card (constant R, zero switching energy)
precisely so the arithmetic is falsifiable — a test written against the real
Infineon card could only ever compare the code with itself.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
import pytest
import yaml

from motor_ai_sim.inverter import devices as dv
from motor_ai_sim.inverter import losses as lo
from motor_ai_sim.inverter import topology as tp
from motor_ai_sim.inverter import waveforms as wf
from motor_ai_sim.inverter import schematic as sc


# ---------------------------------------------------------------------------
# The real card — against the datasheet's own tables
# ---------------------------------------------------------------------------

REAL = "IMCQ120R004M2H"


def test_real_card_loads_and_matches_datasheet_tables():
    c = dv.get_device(REAL)
    assert c.part == REAL
    assert c.v_dss_V == 1200
    assert c.t_j_max_c == 175
    # Table 1: R_th(j-c) typ 0.07 / max 0.1 — the model derates on the MAX.
    assert c.r_th_jc_k_w == pytest.approx(0.1)
    assert c.r_th_jc_basis == "max"
    # Table 2: I_DDC 403 A at 25 degC, 287 A at 100 degC.
    assert c.i_d_continuous(25.0) == pytest.approx(403.0)
    assert c.i_d_continuous(100.0) == pytest.approx(287.0)
    # Table 4: R_DS(on) 3.7 / 7.6 / 8.9 mOhm at 25 / 150 / 175 degC, V_GS 18 V.
    assert c.r_ds_on_ohm(25.0, 18.0) * 1e3 == pytest.approx(3.7)
    assert c.r_ds_on_ohm(150.0, 18.0) * 1e3 == pytest.approx(7.6)
    assert c.r_ds_on_ohm(175.0, 18.0) * 1e3 == pytest.approx(8.9)
    # …and the 15 V curve is a different curve, not an interpolation.
    assert c.r_ds_on_ohm(25.0, 15.0) * 1e3 == pytest.approx(4.7)


def test_real_card_switching_energies_hit_the_table_points():
    c = dv.get_device(REAL)
    # Table 4/6 at V_DD = 800 V, I_D = 185.2 A, T_vj = 175 degC, V_GS = 0/18 V.
    e = c.e_switch(i_d_A=185.2, t_j_c=175.0, v_dc_V=800.0, v_gs_off_V=0.0)
    assert e["e_on_J"] * 1e3 == pytest.approx(4.92, rel=1e-3)
    assert e["e_off_J"] * 1e3 == pytest.approx(4.78, rel=1e-3)
    assert e["e_fr_J"] * 1e3 == pytest.approx(2.99, rel=1e-3)
    # …and at 25 degC, where the card carries only the table point and borrows
    # the 175 degC curve's SHAPE.
    e25 = c.e_switch(i_d_A=185.2, t_j_c=25.0, v_dc_V=800.0, v_gs_off_V=0.0)
    assert e25["e_on_J"] * 1e3 == pytest.approx(3.79, rel=1e-3)
    assert e25["e_off_J"] * 1e3 == pytest.approx(3.97, rel=1e-3)
    # A -5 V gate-off is a genuinely different device: Table 4 gives 2.31 mJ.
    e5 = c.e_switch(i_d_A=185.2, t_j_c=25.0, v_dc_V=800.0, v_gs_off_V=-5.0)
    assert e5["e_off_J"] * 1e3 == pytest.approx(2.31, rel=1e-3)


def test_real_card_voltage_scaling_is_the_stated_rule():
    c = dv.get_device(REAL)
    a = c.e_switch(i_d_A=100.0, t_j_c=175.0, v_dc_V=800.0)
    b = c.e_switch(i_d_A=100.0, t_j_c=175.0, v_dc_V=400.0)
    assert b["e_on_J"] == pytest.approx(0.5 * a["e_on_J"])
    assert any("800" in n for n in a["notes"])


def test_real_card_eoss_reproduces_the_datasheet_value():
    """AN2025-10 eq. (11): C_o(er) = 2 E_oss / V^2, so 0.5 C_o(er) V^2 must
    give back the 242 uJ Table 4 publishes at 800 V."""
    c = dv.get_device(REAL)
    assert c.e_oss_J(800.0) * 1e6 == pytest.approx(242.0, rel=0.01)


def test_real_card_third_quadrant_anchor():
    """Table 6: V_SD = 4.0 V at I_SD = 185.2 A, V_GS = 0 V, T_vj = 175 degC."""
    c = dv.get_device(REAL)
    assert c.v_sd_V(185.2, 175.0, 0.0) == pytest.approx(4.0, rel=1e-3)
    # A -5 V gate-off pushes the body diode further on.
    assert c.v_sd_V(185.2, 175.0, -5.0) > c.v_sd_V(185.2, 175.0, 0.0)


def test_card_validator_names_what_is_missing():
    bad = dv.validate_card({"part": "X"})
    joined = " ".join(bad)
    for want in ("package", "ratings", "r_ds_on", "switching",
                 "third_quadrant", "thermal"):
        assert want in joined
    assert dv.validate_card("not a mapping") == ["the card must be a YAML mapping"]


def test_unknown_device_lists_what_there_is():
    with pytest.raises(dv.CardError) as exc:
        dv.get_device("NO_SUCH_PART")
    assert REAL in str(exc.value)


# ---------------------------------------------------------------------------
# A synthetic card — so the loss arithmetic is checkable by hand
# ---------------------------------------------------------------------------

R_SYN_MOHM = 10.0
E_SYN_UJ = 1000.0


def _synthetic_card(*, e_uj: float = 0.0) -> dict:
    return {
        "part": "SYNTH", "package": "TEST",
        "ratings": {"source": "synthetic", "v_dss_V": 1200,
                    "t_j_max_c": 175,
                    "i_d_continuous": [{"t_case_c": 25, "i_a": 1000.0},
                                       {"t_case_c": 175, "i_a": 1000.0}]},
        "r_ds_on": {"source": "synthetic",
                    "curves": [{"v_gs_on_V": 18,
                                "points": [{"t_j_c": -50, "r_mohm": R_SYN_MOHM},
                                           {"t_j_c": 250, "r_mohm": R_SYN_MOHM}]}]},
        "switching": {"source": "synthetic", "v_dd_ref_V": 800.0,
                      "i_d_ref_A": 100.0, "r_g_ext_ref_ohm": 2.3,
                      "scaling": {"voltage_exponent": 1.0},
                      "curves": [{"v_gs_off_V": 0, "t_j_c": 25,
                                  "e_on_uJ": {"points": [[0, e_uj], [2000, e_uj]]},
                                  "e_off_uJ": {"points": [[0, e_uj], [2000, e_uj]]}},
                                 {"v_gs_off_V": 0, "t_j_c": 250,
                                  "e_on_uJ": {"points": [[0, e_uj], [2000, e_uj]]},
                                  "e_off_uJ": {"points": [[0, e_uj], [2000, e_uj]]}}]},
        "third_quadrant": {"source": "synthetic",
                           "curves": [{"v_gs_off_V": 0, "t_j_c": 175,
                                       "points": [[0.0, 0.0], [1.0, 1000.0]]}]},
        "capacitance": {"source": "synthetic", "at_v_ds_V": 800, "c_o_er_pF": 0.0},
        "thermal": {"source": "synthetic",
                    "r_th_jc_k_w": {"typ": 0.05, "max": 0.05}},
    }


@pytest.fixture()
def synth_dir(tmp_path, monkeypatch):
    """A devices folder holding the synthetic card AND the real one."""
    d = tmp_path / "devices"
    d.mkdir()
    (d / "SYNTH.yaml").write_text(yaml.safe_dump(_synthetic_card()),
                                  encoding="utf-8")
    real = dv.devices_dir() / f"{REAL}.yaml"
    (d / f"{REAL}.yaml").write_text(real.read_text(encoding="utf-8"),
                                    encoding="utf-8")
    monkeypatch.setattr(dv, "_DIR", d)
    dv._CACHE.clear()
    yield d
    dv._CACHE.clear()


def _synth_request(**kw):
    req = dict(
        num_slots=12, num_poles=10, single_layer=True, star_delta="star",
        device="SYNTH", devices_parallel=1, v_dc_V=800.0,
        i_phase_rms_A=100.0, p_ac_W=100_000.0, f_elec_hz=1000.0,
        f_carrier_hz=20_000.0, power_factor=0.9, efficiency_shaft=0.97,
        topology="one_3ph", dead_time_us=0.0,
        cooling={"coolant": "water", "flow_lpm": 10.0, "t_in_c": 40.0,
                 "r_override_k_w": 0.0},
        r_tim_k_w=0.0, r_spread_k_w=0.0,
    )
    req.update(kw)
    return req


def test_conduction_loss_is_the_closed_form(synth_dir):
    """Three legs, one device each, no dead time, no switching energy:
    P = 3 * I_leg_rms^2 * R_DS(on).  Star, so the leg current IS the phase
    current."""
    out = lo.solve_controller(_synth_request())
    expect = 3.0 * 100.0 ** 2 * R_SYN_MOHM * 1e-3
    assert out["losses"]["conduction_W"] == pytest.approx(expect, rel=1e-6)
    assert out["losses"]["switching_W"] == pytest.approx(0.0, abs=1e-9)
    assert out["losses"]["third_quadrant_W"] == pytest.approx(0.0, abs=1e-9)


def test_delta_puts_the_line_current_through_the_bridge(synth_dir):
    """A delta machine's bridge sits OUTSIDE the delta, so the leg carries
    sqrt(3) x the phase current — and the conduction loss is 3x."""
    star = lo.solve_controller(_synth_request(star_delta="star"))
    delta = lo.solve_controller(_synth_request(star_delta="delta"))
    assert (delta["losses"]["conduction_W"]
            == pytest.approx(3.0 * star["losses"]["conduction_W"], rel=1e-6))
    assert delta["point"]["i_leg_rms_3ph_A"] == pytest.approx(100.0 * math.sqrt(3.0),
                                                              abs=0.05)


def test_parallel_devices_divide_the_conduction_loss(synth_dir):
    one = lo.solve_controller(_synth_request(devices_parallel=1))
    four = lo.solve_controller(_synth_request(devices_parallel=4))
    assert (four["losses"]["conduction_W"]
            == pytest.approx(one["losses"]["conduction_W"] / 4.0, rel=1e-6))


def test_switching_events_are_counted_per_carrier_period(synth_dir, tmp_path,
                                                         monkeypatch):
    """With a current-independent E, the switching loss is exactly
    ``n_legs * f_sw * N * (E_on + E_off)`` — one hard turn-on and one hard
    turn-off per carrier period per leg."""
    (synth_dir / "SYNTH.yaml").write_text(
        yaml.safe_dump(_synthetic_card(e_uj=E_SYN_UJ)), encoding="utf-8")
    dv._CACHE.clear()
    for n_par in (1, 3):
        out = lo.solve_controller(_synth_request(devices_parallel=n_par))
        expect = 3 * 20_000.0 * n_par * 2 * E_SYN_UJ * 1e-6
        assert out["losses"]["switching_W"] == pytest.approx(expect, rel=1e-6)


def test_dead_time_moves_conduction_into_the_body_diode(synth_dir):
    """The dead-time fraction comes OUT of the channel and goes INTO the
    diode; the synthetic diode is 1 mOhm-equivalent (1 V at 1000 A)."""
    none = lo.solve_controller(_synth_request(dead_time_us=0.0))
    dead = lo.solve_controller(_synth_request(dead_time_us=1.0))
    f_dt = 2 * 1e-6 * 20_000.0
    assert dead["losses"]["conduction_W"] == pytest.approx(
        none["losses"]["conduction_W"] * (1.0 - f_dt), rel=1e-6)
    assert dead["losses"]["third_quadrant_W"] > 0.0


def test_junction_temperature_is_the_stated_chain(synth_dir):
    """T_j = T_coolant_mean + P_total*R_plate + P_device*(R_jc + R_TIM)."""
    out = lo.solve_controller(_synth_request(
        devices_parallel=2, r_tim_k_w=0.02,
        cooling={"coolant": "water", "flow_lpm": 10.0, "t_in_c": 40.0,
                 "r_override_k_w": 0.001}))
    T = out["thermal"]
    p_dev = max(l["p_device_W"] for b in out["bridges"] for l in b["legs"])
    assert T["converged"] is True
    assert T["t_case_c"] == pytest.approx(
        T["t_coolant_mean_c"] + out["losses"]["total_W"] * 0.001, abs=0.05)
    assert T["t_j_max_c"] == pytest.approx(
        T["t_case_c"] + p_dev * (0.05 + 0.02), abs=0.2)


def test_thermal_iteration_converges_on_the_real_card(synth_dir):
    out = lo.solve_controller(_synth_request(
        device=REAL, devices_parallel=6, i_phase_rms_A=300.0,
        p_ac_W=250_000.0, dead_time_us=0.5, cooling={},
        r_tim_k_w=None, r_spread_k_w=None))
    assert out["thermal"]["converged"] is True
    assert out["thermal"]["iterations"] < lo._MAX_ITER
    # R_DS(on) really did follow the junction temperature.
    r = out["bridges"][0]["legs"][0]["r_ds_on_mohm"]
    assert 3.5 < r < 11.0


def test_overheating_is_a_loud_violation_not_a_silent_number(synth_dir):
    out = lo.solve_controller(_synth_request(
        device=REAL, devices_parallel=1, i_phase_rms_A=500.0,
        p_ac_W=400_000.0, star_delta="delta", dead_time_us=1.0))
    assert out["ok"] is False
    assert any("junction temperature" in v.lower()
               or "runaway" in v.lower() for v in out["violations"])


def test_bus_above_vdss_is_refused_by_name(synth_dir):
    with pytest.raises(lo.ControllerRefusal) as exc:
        lo.solve_controller(_synth_request(v_dc_V=1500.0))
    assert exc.value.code == "bus_over_vdss"
    assert "v_dc_V" in exc.value.fields


def test_no_modulation_and_no_power_factor_is_refused(synth_dir):
    req = _synth_request()
    req.pop("power_factor")
    with pytest.raises(lo.ControllerRefusal) as exc:
        lo.solve_controller(req)
    assert exc.value.code == "missing_modulation"


def test_efficiency_names_both_numbers(synth_dir):
    out = lo.solve_controller(_synth_request())
    e = out["efficiency"]
    assert e["shaft"] == pytest.approx(0.97)
    assert e["wall_to_shaft"] == pytest.approx(e["inverter"] * 0.97, rel=1e-4)
    assert "shaft" in e["note"]


# ---------------------------------------------------------------------------
# Topology — the owner's actual requirement
# ---------------------------------------------------------------------------

def test_l155_winding_gives_six_coils_one_per_two_slots():
    coils = tp.coils_from_winding(12, 10, single_layer=True)
    assert len(coils) == 6
    assert [c.phase for c in coils] == ["A", "B", "C", "A", "B", "C"]
    assert [c.slot_go for c in coils] == [1, 3, 5, 7, 9, 11]


@pytest.mark.parametrize("preset,bridges,switches", [
    ("one_3ph", 1, 6),
    ("two_3ph", 2, 12),
    ("h_bridge", 6, 24),
])
def test_presets_produce_the_right_amount_of_silicon(preset, bridges, switches):
    coils = tp.coils_from_winding(12, 10)
    t = tp.build_topology(preset=preset, coils=coils, star_delta="delta",
                          devices_parallel=3)
    assert len(t.bridges) == bridges
    assert t.n_switches == switches
    assert t.n_devices == switches * 3
    # Every coil is driven by exactly one BRIDGE, whatever the preset (an
    # H-bridge's coil is on both of ITS legs — those are its two ends).
    per_bridge = {}
    for m in t.as_dict()["mapping"]:
        per_bridge.setdefault(m["coil"], set()).add(m["bridge"])
    assert sorted(per_bridge) == [1, 2, 3, 4, 5, 6]
    assert all(len(v) == 1 for v in per_bridge.values())


def test_two_inverters_report_the_measured_electrical_shift():
    coils = tp.coils_from_winding(12, 10)
    t = tp.build_topology(preset="two_3ph", coils=coils)
    assert t.bridges[1].phase_shift_deg != 0.0
    assert any("deg electrical" in n for n in t.notes)


def test_custom_mapping_refuses_a_coil_driven_twice():
    """Two different BRIDGES over one coil, and a three-phase leg that has a
    coil on two of its legs — both are wiring nobody can build."""
    coils = tp.coils_from_winding(12, 10)
    mapping = [{"coil": c.index, "bridge": "INV1", "leg": c.phase} for c in coils]
    mapping.append({"coil": 1, "bridge": "INV2", "leg": "A"})
    mapping.append({"coil": 3, "bridge": "INV2", "leg": "B"})
    mapping.append({"coil": 5, "bridge": "INV2", "leg": "C"})
    with pytest.raises(tp.TopologyError) as exc:
        tp.build_topology(preset="custom", coils=coils, mapping=mapping)
    msg = str(exc.value)
    assert "coil 1 is driven by" in msg and "INV1" in msg and "INV2" in msg

    twice_on_one_bridge = [{"coil": 1, "bridge": "INV1", "leg": "A"},
                           {"coil": 1, "bridge": "INV1", "leg": "B"},
                           {"coil": 2, "bridge": "INV1", "leg": "C"}]
    with pytest.raises(tp.TopologyError) as exc2:
        tp.build_topology(preset="custom", coils=coils,
                          mapping=twice_on_one_bridge)
    assert "exactly one" in str(exc2.value)


def test_custom_mapping_refuses_a_coil_nobody_drives():
    coils = tp.coils_from_winding(12, 10)
    mapping = [{"coil": c.index, "bridge": "INV1", "leg": c.phase}
               for c in coils[:3]]
    with pytest.raises(tp.TopologyError) as exc:
        tp.build_topology(preset="custom", coils=coils, mapping=mapping)
    assert "not connected to any bridge" in str(exc.value)


def test_custom_mapping_accepts_a_complete_one():
    coils = tp.coils_from_winding(12, 10)
    mapping = [{"coil": c.index, "bridge": "INV1", "leg": c.phase} for c in coils]
    t = tp.build_topology(preset="custom", coils=coils, mapping=mapping,
                          star_delta="delta")
    assert t.n_switches == 6
    assert t.bridges[0].connection == "delta"


def test_h_bridge_per_coil_is_available_for_the_stage_3_study():
    coils = tp.coils_from_winding(12, 10)
    t = tp.build_topology(preset="h_bridge", coils=coils,
                          h_bridge_modulation="unipolar")
    assert [b.id for b in t.bridges] == [f"HB{i}" for i in range(1, 7)]
    assert all(b.connection == "independent" for b in t.bridges)
    assert all(len(b.legs) == 2 for b in t.bridges)


# ---------------------------------------------------------------------------
# The waveform — dead-time distortion is the shape test
# ---------------------------------------------------------------------------

def test_dead_time_pushes_the_output_down_while_the_current_leaves_the_leg():
    """With both switches off the terminal follows the CURRENT: low while the
    current leaves the leg, high while it enters.  So the dead time subtracts
    volts on the positive half and adds them on the negative half, and the
    error steps sign at the zero crossing."""
    s = wf.ModulatorSetup(f_elec_hz=100.0, f_carrier_hz=5_000.0, v_dc_V=600.0,
                          modulation_index=0.8, samples_per_carrier=64,
                          dead_time_s=4e-6)
    u = s.grid()
    i = 100.0 * np.sin(2.0 * math.pi * u)
    st = wf.switch_states(s, u, 0.0)
    dead = wf.dead_samples_for(s)
    assert dead >= 1
    v_ideal = wf.leg_terminal_voltage(s, u, st, i, r_ds_ohm=0.0,
                                      v_sd=lambda a: np.full_like(a, 4.0),
                                      dead_samples=0)
    v_dead = wf.leg_terminal_voltage(s, u, st, i, r_ds_ohm=0.0,
                                     v_sd=lambda a: np.full_like(a, 4.0),
                                     dead_samples=dead)
    pos, neg = i > 1.0, i < -1.0
    assert v_dead[pos].mean() < v_ideal[pos].mean()
    assert v_dead[neg].mean() > v_ideal[neg].mean()
    # …and the analytic error has the same sign and the right order.
    err = wf.dead_time_error_V(s, i, 4.0)
    assert err[i > 0].mean() < 0 < err[i < 0].mean()
    assert abs(err[1]) == pytest.approx(
        4e-6 * s.f_carrier_eff_hz * (600.0 + 8.0), rel=1e-6)


def test_device_drops_are_in_the_terminal_voltage():
    s = wf.ModulatorSetup(f_elec_hz=100.0, f_carrier_hz=2_000.0, v_dc_V=600.0,
                          modulation_index=0.5, samples_per_carrier=32)
    u = s.grid()
    i = np.full_like(u, 200.0)
    st = np.ones_like(u)
    v = wf.leg_terminal_voltage(s, u, st, i, r_ds_ohm=0.005,
                               v_sd=lambda a: np.zeros_like(a), dead_samples=0)
    assert v == pytest.approx(np.full_like(u, 300.0 - 200.0 * 0.005))


def test_dc_link_current_is_the_switching_functions_times_the_currents():
    s = wf.ModulatorSetup(f_elec_hz=100.0, f_carrier_hz=5_000.0, v_dc_V=600.0,
                          modulation_index=0.9, samples_per_carrier=40)
    u = s.grid()
    states, currents = [], []
    for k in range(3):
        ph = -120.0 * k
        currents.append(100.0 * np.sin(2.0 * math.pi * u + math.radians(ph)))
        states.append(wf.switch_states(s, u, ph))
    dc = wf.dc_link_current(states, currents)
    assert dc["i_dc_rms_A"] >= abs(dc["i_dc_mean_A"])
    assert dc["i_cap_rms_A"] == pytest.approx(
        math.sqrt(dc["i_dc_rms_A"] ** 2 - dc["i_dc_mean_A"] ** 2), rel=1e-6)
    # A balanced three-phase bridge draws a POSITIVE mean current from the bus.
    assert dc["i_dc_mean_A"] > 0.0


def test_the_solve_exports_one_period_per_coil(synth_dir):
    out = lo.solve_controller(_synth_request(topology="h_bridge"))
    w = out["waveforms"]
    assert sorted(w["coils"]) == [str(i) for i in range(1, 7)]
    n = len(w["t_s"])
    for c in w["coils"].values():
        assert len(c["v_V"]) == n and len(c["i_A"]) == n
        assert c["bridge"].startswith("HB")


# ---------------------------------------------------------------------------
# The schematic
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("preset", ["one_3ph", "two_3ph", "h_bridge"])
def test_schematic_draws_every_topology_with_the_coil_labels(preset):
    coils = tp.coils_from_winding(12, 10)
    t = tp.build_topology(preset=preset, coils=coils, star_delta="delta",
                          devices_parallel=4)
    svg = sc.schematic_svg(t, v_dc_V=750.0, device="X")
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "DC link" in svg and "750 V" in svg and "C_dc" in svg
    assert "×4" in svg                      # the parallel device count
    assert "S1" in svg                      # the standard switch numbering
    for c in coils:
        assert f"L{c.index}" in svg


def test_schematic_draws_a_transistor_with_its_body_diode_per_switch():
    """The owner's reference drawing: every switch is a transistor symbol WITH
    its antiparallel diode, so the picture says which path carries the current
    while the leg is in dead time."""
    coils = tp.coils_from_winding(12, 10)
    t = tp.build_topology(preset="one_3ph", coils=coils, star_delta="delta")
    svg = sc.schematic_svg(t)
    # one filled source arrow and one open diode triangle per switch
    assert svg.count('fill="currentColor"/>') >= 6          # source arrows
    assert svg.count('fill="none" stroke="currentColor" stroke-width="1.2"') >= 6
    for s in ("S1", "S2", "S3", "S4", "S5", "S6"):
        assert f">{s}<" in svg


@pytest.mark.parametrize("sd,marker,absent", [("star", ">N<", "Δ"),
                                              ("delta", "Δ", ">N<")])
def test_schematic_draws_the_winding_the_duty_is_wound_as(sd, marker, absent):
    """Owner 2026-09-22: «дельту и звезду тоже надо рисовать на картинке»."""
    coils = tp.coils_from_winding(12, 10)
    t = tp.build_topology(preset="one_3ph", coils=coils, star_delta=sd)
    svg = sc.schematic_svg(t)
    assert marker in svg
    assert absent not in svg


@pytest.mark.parametrize("sd", ["star", "delta"])
@pytest.mark.parametrize("slots,poles", [(12, 10), (12, 14)])
def test_the_three_terminals_sit_120_degrees_apart(sd, slots, poles):
    """The textbook symbols, and the reason the phase lines do not cross.

    Owner 2026-09-22, on the first two attempts: *«я бы повернул и треугольник,
    и звезду на 60 градусов»*, then *«нарисуй нормальную звезду»*.  What both
    corrections come down to is one property, and it is the one pinned here:
    the three terminals are 120 degrees apart — an equilateral triangle, or a
    Y of three equal arms — placed upper-left, lower-left and right, so L1 and
    L2 arrive straight and L3 is taken under.
    """
    import math
    from motor_ai_sim.inverter.schematic import MOTOR_R
    coils = tp.coils_from_winding(slots, poles)
    t = tp.build_topology(preset="one_3ph", coils=coils, star_delta=sd)
    svg = sc.schematic_svg(t)
    # The terminal dots are the r="2.4" circles.
    # (the SVG rounds every coordinate to 0.1 px, hence the absolute
    # tolerances below)
    pts = [(float(x), float(y)) for x, y in
           re.findall(r'<circle cx="([-\d.]+)" cy="([-\d.]+)" r="2.4"', svg)]
    assert len(pts) == 3, "three terminals, one per leg"
    cx = sum(p[0] for p in pts) / 3.0
    cy = sum(p[1] for p in pts) / 3.0
    radii = [math.hypot(x - cx, y - cy) for x, y in pts]
    for r in radii:
        assert r == pytest.approx(MOTOR_R, abs=0.2), "equal arms / equal sides"
    angles = sorted(math.degrees(math.atan2(cy - y, x - cx)) % 360.0
                    for x, y in pts)
    assert angles == pytest.approx([0.0, 120.0, 240.0], abs=0.3)
    # …and the coil labels are beside the terminals, one per leg.
    for lbl in ("L1 ", "L2 ", "L3 "):
        assert lbl in svg


def test_per_bridge_parallel_counts_override_the_common_one():
    coils = tp.coils_from_winding(12, 10)
    t = tp.build_topology(preset="two_3ph", coils=coils, devices_parallel=3,
                          devices_parallel_by_bridge={"INV2": 7})
    assert [b.devices_parallel for b in t.bridges] == [3, 7]
    assert t.n_devices == 6 * 3 + 6 * 7
    svg = sc.schematic_svg(t)
    assert "×3" in svg and "×7" in svg
    with pytest.raises(tp.TopologyError) as exc:
        tp.build_topology(preset="two_3ph", coils=coils,
                          devices_parallel_by_bridge={"INV9": 2})
    assert "INV9" in str(exc.value)


def test_package_outline_and_size_reach_the_catalogue_row():
    card = dv.get_device(REAL)
    size = card.package_size()
    # Datasheet Figure 1, PG-HDSOP-22-U03, MAX column: D / E / A.
    assert size["length_mm"] == pytest.approx(15.10)
    assert size["width_mm"] == pytest.approx(21.11)
    assert size["height_mm"] == pytest.approx(2.35)
    row = card.row()
    assert row["weight_g"] is None            # not published in revision 1.10
    assert row["package_svg"].startswith("<svg")
    from motor_ai_sim.inverter import packages as pk
    assert pk.family_for("PG-HDSOP-22-U03", "Q-DPAK") == "q-dpak"
    assert pk.family_for("something nobody has heard of") == "generic"


def test_suggested_parallel_is_the_current_rating_and_says_so():
    """ceil(I_switch_rms / I_DDC at a 100 degC case) — 287 A for this part."""
    card = dv.get_device(REAL)
    assert card.suggested_parallel(287.0) == 1
    assert card.suggested_parallel(288.0) == 2
    assert card.suggested_parallel(900.0) == 4
    assert card.suggested_parallel(0.0) is None
    assert card.row(i_switch_rms_A=385.0)["suggested_parallel"] == 2


# ---------------------------------------------------------------------------
# The route, and the duty record it writes
# ---------------------------------------------------------------------------

def test_route_solves_caches_and_lists(synth_dir, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from motor_ai_sim.routes import controller as rc

    app_key = "controller.solve"
    monkeypatch.setattr(rc, "_HISTORY", rc._RH.history_for(app_key))

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(rc.router)
    c = TestClient(app)

    rows = c.get("/api/controller/devices").json()["devices"]
    assert {r["part"] for r in rows} == {"SYNTH", REAL}

    body = {k: v for k, v in _synth_request().items()}
    r1 = c.post("/api/controller/solve?fresh=true", json=body)
    assert r1.status_code == 200, r1.text
    assert r1.json()["cached"] is False
    r2 = c.post("/api/controller/solve", json=body)
    assert r2.json()["cached"] is True
    assert r2.json()["history_key"] == r1.json()["history_key"]

    last = c.get("/api/controller/last").json()
    assert last["device"] == "SYNTH"
    assert c.get("/api/controller/history").json()["entries"]


def test_route_refuses_an_unwireable_mapping(synth_dir):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from motor_ai_sim.routes import controller as rc
    app = FastAPI(); app.include_router(rc.router)
    c = TestClient(app)
    r = c.post("/api/controller/schematic", json={
        "num_slots": 12, "num_poles": 10, "single_layer": True,
        "topology": "custom",
        "mapping": [{"coil": 1, "bridge": "INV1", "leg": "A"},
                    {"coil": 2, "bridge": "INV1", "leg": "B"},
                    {"coil": 3, "bridge": "INV1", "leg": "C"}]})
    assert r.status_code == 422
    assert "not connected to any bridge" in r.json()["detail"]["message"]


def test_route_add_device_validates(synth_dir):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from motor_ai_sim.routes import controller as rc
    app = FastAPI(); app.include_router(rc.router)
    c = TestClient(app)
    r = c.post("/api/controller/devices", json={"card": {"part": "HALF"}})
    assert r.status_code == 422
    assert "ratings" in r.json()["detail"]["message"]
    ok = c.post("/api/controller/devices",
                json={"card_yaml": yaml.safe_dump(_synthetic_card()),
                      "overwrite": True})
    assert ok.status_code == 200 and ok.json()["ok"] is True


def test_duty_record_is_compact_and_drops_the_waveform(synth_dir):
    from motor_ai_sim import duty_results as dr
    out = lo.solve_controller(_synth_request())
    out["schematic_svg"] = "<svg/>"
    e = dr.compact_controller(out)
    assert "waveforms" not in e
    assert e["device"] == "SYNTH"
    assert e["efficiency"]["wall_to_shaft"] is not None
    assert "controller" in dr.KINDS
    assert "controller" in dr.kinds_present({"controller": e})


# ---------------------------------------------------------------------------
# Report and datasheet
# ---------------------------------------------------------------------------

def test_report_section_numbering_makes_room_for_the_controller():
    from motor_ai_sim import report as R
    plain = R.section_numbers(False, False)
    with_ctrl = R.section_numbers(False, True)
    assert "controller" not in plain
    assert with_ctrl["controller"] == plain["mech"]
    assert with_ctrl["mech"] == plain["mech"] + 1
    assert with_ctrl["notes"] == plain["notes"] + 1
    assert R.SECTION_TITLES["controller"] == "Controller"
    assert "controller" in R.contents_line(with_ctrl)


def test_report_controller_rows_name_both_efficiencies(synth_dir):
    from motor_ai_sim import duty_results as dr
    from motor_ai_sim import report as R
    rec = dr.compact_controller(lo.solve_controller(_synth_request()))
    col = {"duty": "rated", "res": {"controller": rec}}
    assert R.controller_record(col) is rec
    names = [r[0] for r in R.controller_rows(rec)]
    for want in ("Conduction loss", "Third-quadrant loss", "Switching loss",
                 "Inverter efficiency", "Shaft efficiency",
                 "Wall-to-shaft efficiency", "Junction temperature",
                 "DC-link ripple current"):
        assert want in names
    assert "SYNTH" in R.controller_device_text(rec)
    assert R.controller_bridge_rows(rec)[0][0] == "Bridge / leg"
    assert len(R.controller_bridge_rows(rec)) == 4      # header + three legs
    assert "dead time" in R.controller_assumption_text(rec)


def test_report_controller_section_is_absent_without_a_record():
    from motor_ai_sim import report as R
    assert R.controller_record({"duty": "x", "res": {}}) is None
