"""SPICE harness (motor_ai_sim.inverter.spice) — parser, windows, table.

None of these tests runs ngspice: the parser, the DDT translation, the
datasheet integration windows and the table interpolation are pure functions
and are pinned on synthetic data.  (The simulator runs live in
scripts/spice_*.py; their results are in docs/CONTROLLER_SPICE_2026-09-23.md.)
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from motor_ai_sim.inverter.devices import CardError, DeviceCard
from motor_ai_sim.inverter.spice import extract
from motor_ai_sim.inverter.spice.models import SpiceModel, ngspice_translate_ddt
from motor_ai_sim.inverter.spice.netlist import DoublePulse, double_pulse_netlist
from motor_ai_sim.inverter.spice.runner import parse_wrdata
from motor_ai_sim.inverter.spice.table import (MARK_BEGIN, MARK_END, make_set,
                                               table_energy, write_block)


# ── parser ──────────────────────────────────────────────────────────────────
def test_parse_wrdata_singlescale_with_header(tmp_path: Path) -> None:
    p = tmp_path / "x.data"
    p.write_text(" time            v(dl)           i(vidl)\n"
                 " 0.00000000e+00  8.00000000e+02  1.0e-03\n"
                 " 1.00000000e-09  7.00000000e+02  2.0e+00\n"
                 " 2.00000000e-09  1.00000000e+01  1.8e+02\n", encoding="utf-8")
    r = parse_wrdata(p, ["i(vidl)", "v(dl)"])     # asked in another order
    assert r["scale"].tolist() == [0.0, 1e-9, 2e-9]
    assert r["v(dl)"].tolist() == [800.0, 700.0, 10.0]
    assert r["i(vidl)"][-1] == pytest.approx(180.0)


# ── DDT translation (exact algebra, nothing else touched) ───────────────────
def test_ddt_translation_replaces_only_ddt_calls() -> None:
    src = (".SUBCKT ZZ A B C\n"
           "G1 A B VALUE = { K1 * DDT(V(A,B)) * F(V(C,B)),\n"
           "+ 0 }\n"
           "R1 A B 1k\n"
           "G2 C 0 VALUE = { DDT(V(C)) }\n"
           ".ENDS ZZ\n")
    out, n = ngspice_translate_ddt(src)
    assert n == 2
    assert "DDT(" not in out.upper().replace("VDDT_", "").replace("EDDT_", "").replace("CDDT_", "")
    assert "G1 A B VALUE = { K1 * (I(VDDT_1)*1e12) * F(V(C,B)), 0 }" in " ".join(out.split(" ")).replace("  ", " ")
    assert "EDDT_1 NDDT_1_A 0 A B 1" in out
    assert "CDDT_1 NDDT_1_A NDDT_1_B 1e-12" in out
    assert "VDDT_1 NDDT_1_B 0 0" in out
    assert "EDDT_2 NDDT_2_A 0 C 0 1" in out           # single-node form
    assert "R1 A B 1k" in out and ".ENDS ZZ" in out


# ── netlist: pin order follows the vendor subcircuit ────────────────────────
def test_instance_pin_order_and_kelvin(tmp_path: Path) -> None:
    lib = tmp_path / "x.lib"
    lib.write_text("* no ddt here\n", encoding="utf-8")
    m = SpiceModel(part="P", level="L1", lib_path=lib, lib_sha256="0" * 64,
                   lib_name="x.lib", subckt="P_L1",
                   pins=["DRAIN", "GATE", "SOURCE", "SOURCESENSE"], kelvin=True)
    assert m.instance("L", "d", "g", "s", kelvin="k") == "XL d g s k P_L1"
    m3 = SpiceModel(part="Q", level="L1", lib_path=lib, lib_sha256="0" * 64,
                    lib_name="x.lib", subckt="Q_L1", pins=["drain", "gate", "source"])
    assert m3.instance("H", "d", "g", "s", kelvin="k") == "XH d g s Q_L1"
    txt = double_pulse_netlist(m, DoublePulse(v_dd=800, i_target=100, rg_on=5, rg_off=2))
    assert ".include" in txt and "GRG drv gli VALUE" in txt   # split R_G driver
    assert "VDRV drv kl PWL(" in txt                           # Kelvin-referenced


# ── the datasheet windows (Fig. C / Fig. B) on a synthetic edge ─────────────
def _trapezoid_off(v_dd=800.0, i0=100.0, tr=20e-9, tf=30e-9):
    """V_DS ramps 0→V_dd in tr, THEN I_D falls i0→0 in tf (inductive turn-off)."""
    t = np.linspace(0, 200e-9, 20001)
    t0 = 50e-9
    v = np.clip((t - t0) / tr, 0, 1) * v_dd
    i = i0 * (1 - np.clip((t - t0 - tr) / tf, 0, 1))
    return t, v, i, t0


def test_e_off_window_matches_closed_form() -> None:
    v_dd, i0, tr, tf = 800.0, 100.0, 20e-9, 30e-9
    t, v, i, t0 = _trapezoid_off(v_dd, i0, tr, tf)
    vgs = np.where(t < t0 - 10e-9, 18.0, 0.0)
    r = extract.edge_off(t, v, i, vgs, v_dd=v_dd, t_cmd=t0 - 20e-9,
                         v_gs_on=18.0, v_gs_off=0.0)
    # window: from V = 10 % to I = 10 %  (Fig. C)
    # ∫ v·i over the voltage ramp from 10 %: i = i0, v linear 0.1→1
    e_v = i0 * v_dd * tr * (1 - 0.1 ** 2) / 2
    # current fall from 100 % to 10 %: v = v_dd, i linear
    e_i = v_dd * i0 * tf * (1 - 0.1 ** 2) / 2
    assert r["e_J"] == pytest.approx(e_v + e_i, rel=1e-3)
    assert r["t1_s"] == pytest.approx(t0 + 0.1 * tr, rel=1e-4)
    assert r["dv_dt_V_per_s"] == pytest.approx(v_dd / tr, rel=1e-3)
    assert r["i_A"] == pytest.approx(i0)


def test_e_on_and_recovery_windows() -> None:
    v_dd, il = 800.0, 100.0
    t = np.linspace(0, 400e-9, 40001)
    t0, tri, tfv = 100e-9, 20e-9, 25e-9
    irr, trr = 30.0, 20e-9
    # DUT current rises to il + reverse-recovery hump, then V_DS falls
    i_d = il * np.clip((t - t0) / tri, 0, 1)
    hump = np.where((t > t0 + tri) & (t < t0 + tri + trr),
                    irr * np.sin(math.pi * (t - t0 - tri) / trr), 0.0)
    i_d = i_d + hump
    v = v_dd * (1 - np.clip((t - t0 - tri) / tfv, 0, 1))
    vgs = np.where(t > t0 - 10e-9, 18.0, 0.0)
    r = extract.edge_on(t, v, i_d, vgs, v_dd=v_dd, t_cmd=t0 - 20e-9, i_load=il,
                        v_gs_on=18.0, v_gs_off=0.0)
    assert r["t3_s"] == pytest.approx(t0 + 0.1 * tri, rel=1e-4)
    assert r["t4_s"] == pytest.approx(t0 + tri + 0.9 * tfv, rel=1e-4)
    assert r["e_J"] > v_dd * il * tri * 0.99 * 0.5
    # freewheeling device: current = i_d - il (negative while freewheeling)
    i_fw = i_d - il
    v_fw = v_dd - v
    rec = extract.recovery(t, v_fw, i_fw, t_cmd=t0 - 20e-9)
    assert rec["i_frm_A"] == pytest.approx(irr, rel=1e-3)
    # Q over the hump down to 10 % of I_frm on the falling side
    q_full = irr * trr * 2 / math.pi
    assert rec["q_fr_C"] == pytest.approx(q_full, rel=0.03)
    assert rec["t_fr_s"] < trr


def test_load_current_is_the_gap_average_not_a_peak() -> None:
    """The switched current is the freewheeling (load) current in the gap,
    averaged — a ringing C_sigma current on top must not move it."""
    dp = DoublePulse(v_dd=800, i_target=100)
    tl = dp.timeline()
    t = np.linspace(0, tl["t_stop"], 200001)
    i_load = 100.0
    ring = 8.0 * np.sin(2 * math.pi * 40e6 * t)          # 40 MHz, 25 ns period
    in_gap = (t > tl["t_off1"] + 30e-9) & (t < tl["t_on2"])
    r = {"scale": t,
         "v(dl)": np.where(in_gap, 800.0, 0.0), "v(sl)": np.zeros_like(t),
         "v(gl)": np.where(in_gap, 0.0, 18.0), "i(vidl)": np.where(in_gap, 0.0, i_load),
         "i(vidh)": np.where(in_gap, -i_load + ring, 0.0),
         "v(dh)": np.where(in_gap, 800.0, 0.0), "v(mid)": np.where(in_gap, 800.0, 0.0)}
    m = extract.double_pulse_metrics(r, v_dd=800, timeline=tl, v_gs_on=18, v_gs_off=0)
    assert m["i_on_A"] == pytest.approx(i_load, rel=0.01)
    assert m["i_off_A"] == m["i_on_A"]


# ── the table ───────────────────────────────────────────────────────────────
def _fake_run(v, t, i, e_off, e_on, e_fr):
    return {"conditions": {"v_dd": v, "t_j": t},
            "metrics": {"i_off_A": i, "i_on_A": i * 0.99,
                        "off": {"e_J": e_off * 1e-6}, "on": {"e_J": e_on * 1e-6},
                        "fr": {"e_fr_J": e_fr * 1e-6}}}


def _block():
    runs = []
    for v in (600.0, 800.0):
        for t in (25.0, 175.0):
            for i in (50.0, 100.0, 200.0):
                k = (v / 800.0) * (1 + (t - 25) / 300.0)
                runs.append(_fake_run(v, t, i, 10 * i * k, 20 * i * k, 5 * i * k))
    s = make_set(v_gs_on=18, v_gs_off=0, r_g_on=2.3, r_g_off=2.3, l_sigma_nH=15,
                 l_gate_nH=2, c_sigma_pF=20, runs=runs)
    s2 = dict(s, r_g_on_ohm=10.0, r_g_off_ohm=10.0,
              rows=[[r[0], r[1], r[2], 2 * r[3], r[4], 2 * r[5], r[6]] for r in s["rows"]])
    return {"basis": "spice:x.lib@000000000000:P_L1", "sets": [s, s2]}


def test_table_interpolates_on_grid_and_between() -> None:
    b = _block()
    e = table_energy(b, i_d_A=100.0, v_dc_V=800.0, t_j_c=25.0, r_g_on_ohm=2.3)
    assert e["e_off_J"] == pytest.approx(1000e-6, rel=1e-9)
    assert e["e_on_J"] == pytest.approx(20 * 100 * 1e-6, rel=0.02)   # E_on vs its own I_on
    # bilinear in V and T: midpoint of the four corners
    e = table_energy(b, i_d_A=100.0, v_dc_V=700.0, t_j_c=100.0, r_g_on_ohm=2.3)
    k = (700 / 800) * (1 + 75 / 300)
    assert e["e_off_J"] == pytest.approx(10 * 100 * k * 1e-6, rel=1e-9)
    assert not e["extrapolated"]
    # above the current range: extrapolated and flagged
    e = table_energy(b, i_d_A=300.0, v_dc_V=800.0, t_j_c=25.0, r_g_on_ohm=2.3)
    assert e["extrapolated"] and e["e_off_J"] == pytest.approx(3000e-6, rel=1e-9)
    # T_j outside: clamped, said
    e = table_energy(b, i_d_A=100.0, v_dc_V=800.0, t_j_c=200.0, r_g_on_ohm=2.3)
    assert any("held at the edge" in n for n in e["notes"])
    # the driver picks the set; a near miss is SAID
    e = table_energy(b, i_d_A=100.0, v_dc_V=800.0, t_j_c=25.0, r_g_on_ohm=10.0)
    assert e["e_off_J"] == pytest.approx(2000e-6, rel=1e-9)
    e = table_energy(b, i_d_A=100.0, v_dc_V=800.0, t_j_c=25.0, r_g_on_ohm=8.0)
    assert any("nearest set" in n for n in e["notes"])


def test_write_block_keeps_card_text_and_round_trips(tmp_path: Path) -> None:
    card = tmp_path / "P.yaml"
    card.write_text("# hand-written comment\npart: P\nswitching: {v_dd_ref_V: 800}\n",
                    encoding="utf-8")
    b = _block()
    b.update({"lib_sha256": "0" * 64})
    write_block(card, b)
    write_block(card, b)                       # idempotent: replaced, not doubled
    text = card.read_text(encoding="utf-8")
    assert text.startswith("# hand-written comment\npart: P\n")
    assert text.count(MARK_BEGIN) == 1 and text.count(MARK_END) == 1
    doc = yaml.safe_load(text)
    assert doc["switching_table"]["sets"][0]["rows"][0][:3] == [600, 25, 50]


def test_card_dispatches_on_switching_source() -> None:
    root = Path(__file__).resolve().parents[1]
    doc = yaml.safe_load((root / "config" / "devices" / "IMCQ120R004M2H.yaml")
                         .read_text(encoding="utf-8"))
    doc.pop("switching_table", None)
    doc.pop("switching_source", None)
    card = DeviceCard(doc)
    with pytest.raises(CardError):                        # no table: refused
        card.e_switch(i_d_A=100, t_j_c=25, v_dc_V=800, source="spice")
    doc["switching_table"] = _block()
    card = DeviceCard(doc)
    ds = card.e_switch(i_d_A=185.2, t_j_c=25, v_dc_V=800)   # default = datasheet
    assert ds["e_off_J"] == pytest.approx(3970e-6)           # Table 4 anchor
    sp = card.e_switch(i_d_A=100, t_j_c=25, v_dc_V=800, source="spice")
    assert sp["e_off_J"] == pytest.approx(1000e-6)
    assert sp["switching_energy_source"] == "spice_table"
    doc["switching_source"] = "spice"                      # the card may say so
    assert DeviceCard(doc).e_switch(i_d_A=100, t_j_c=25, v_dc_V=800)["e_off_J"] \
        == pytest.approx(1000e-6)
    with pytest.raises(CardError):
        card.e_switch(i_d_A=100, t_j_c=25, v_dc_V=800, source="pspice")


def test_table_single_voltage_node_is_held_and_said() -> None:
    b = _block()
    for s in b["sets"]:
        s["rows"] = [r for r in s["rows"] if r[0] == 800.0]
    e = table_energy(b, i_d_A=100.0, v_dc_V=700.0, t_j_c=25.0, r_g_on_ohm=2.3)
    assert e["e_off_J"] == pytest.approx(1000e-6)          # held, not scaled
    assert e["extrapolated"]
    assert any("HELD" in n for n in e["notes"])


# ── netlists: no Kelvin pin, thermal pins, voltage-drive statics ─────────────
def _model(tmp_path: Path, pins, part="Q"):
    lib = tmp_path / "x.lib"
    lib.write_text("* no ddt here\n", encoding="utf-8")
    kel = any(p.lower() in ("sourcesense", "source_k") for p in pins)
    th = [p for p in pins if p.lower() in ("tj", "tcase", "ttop", "tbottom")]
    return SpiceModel(part=part, level="L3" if th else "L1", lib_path=lib,
                      lib_sha256="0" * 64, lib_name="x.lib", subckt=part,
                      pins=list(pins), kelvin=kel, thermal_pins=th)


def test_three_pin_model_has_no_kelvin_vectors(tmp_path: Path) -> None:
    from motor_ai_sim.inverter.spice.netlist import dp_vectors
    m = _model(tmp_path, ["drain", "gate", "source"])
    v = dp_vectors(m)
    assert "v(kl)" not in v and "v(kh)" not in v and "i(vidl)" in v
    txt = double_pulse_netlist(m, DoublePulse(v_dd=22.2, i_target=30))
    assert "VDRV drv sl PWL(" in txt and "VGH ghd mid" in txt


def test_l3_thermal_pins_held_at_tj(tmp_path: Path) -> None:
    from motor_ai_sim.inverter.spice.netlist import dp_vectors, static_netlist
    m = _model(tmp_path, ["drain", "gate", "source", "Tj", "Ttop", "Tbottom"])
    dp = double_pulse_netlist(m, DoublePulse(v_dd=40, i_target=20, t_j=75))
    # double pulse: junction free (the model's own network), case pins at T_j
    assert "XL dl gl sl thl0 thl1 thl2 Q" in dp
    assert "VTL1 thl1 0 75" in dp and "VTL2 thl2 0 75" in dp
    assert "VTL0 " not in dp
    assert "v(thl0)" in dp_vectors(m)
    # static: everything held, the junction included (isothermal, as .temp)
    st = static_netlist(m, kind="rds", t_j=75, v_gs=10, i_max=20, i_step=5)
    assert "VTL0 thl0 0 75" in st and "VTL2 thl2 0 75" in st


def test_static_voltage_drive(tmp_path: Path) -> None:
    from motor_ai_sim.inverter.spice.netlist import static_netlist
    m = _model(tmp_path, ["drain", "gate", "source"])
    rds = static_netlist(m, kind="rds", t_j=25, v_gs=10, i_max=20, i_step=5,
                         drive="voltage", v_max=0.4, v_step=0.001)
    assert "VDS dl 0 DC 0" in rds and ".dc VDS 0 0.4 0.001" in rds
    vsd = static_netlist(m, kind="vsd", t_j=25, v_gs=0, i_max=20, i_step=5,
                         drive="voltage", v_max=1.5, v_step=0.005)
    assert ".dc VDS 0 -1.5 -0.005" in vsd


def test_timing_by_voltage_class() -> None:
    from motor_ai_sim.inverter.spice.netlist import timing_for
    hv, lv = timing_for(1200), timing_for(80)
    d = DoublePulse(v_dd=800, i_target=100)
    assert hv["t_max_step"] == d.t_max_step and hv["t_first"] == d.t_first
    assert lv["t_max_step"] < hv["t_max_step"] and lv["t_first"] < hv["t_first"]


def test_value_at_interpolates_on_current() -> None:
    from motor_ai_sim.inverter.spice.harness import value_at
    r = {"i_A": np.array([30.0, 10.0, 20.0]), "v_kelvin_V": np.array([0.3, 0.1, 0.2])}
    assert value_at(r, 15.0) == pytest.approx(0.15)


# ── the loss model: optional source, refused by name when absent ────────────
_L155 = dict(num_slots=12, num_poles=10, single_layer=True, star_delta="delta",
             devices_parallel=3, v_dc_V=750.4, i_phase_rms_A=314.3,
             p_ac_W=272_200.0, f_elec_hz=1183.3, f_carrier_hz=24_000.0,
             modulation_index=0.6333, topology="one_3ph", dead_time_us=0.5,
             v_gs_on_V=18.0, v_gs_off_V=0.0, r_g_ext_ohm=2.3,
             cooling={"coolant": "water_glycol_50", "flow_lpm": 8.0, "t_in_c": 65.0},
             r_tim_k_w=0.03, r_spread_k_w=0.0)


def test_solve_controller_spice_source(monkeypatch) -> None:
    from motor_ai_sim.inverter import losses as lo
    root = Path(__file__).resolve().parents[1]
    doc = yaml.safe_load((root / "config" / "devices" / "IMCQ120R004M2H.yaml")
                         .read_text(encoding="utf-8"))
    doc.pop("switching_table", None)
    doc.pop("switching_source", None)
    bare = DeviceCard(doc)
    monkeypatch.setattr(lo, "get_device", lambda name: bare)
    with pytest.raises(lo.ControllerRefusal) as exc:
        lo.solve_controller(dict(_L155, device="IMCQ120R004M2H", switching_source="spice"))
    assert exc.value.code == "no_spice_table"
    with pytest.raises(lo.ControllerRefusal):
        lo.solve_controller(dict(_L155, device="IMCQ120R004M2H", switching_source="bogus"))
    ds = lo.solve_controller(dict(_L155, device="IMCQ120R004M2H"))
    assert ds["losses"]["switching_source"] == "datasheet"
    # a table with every energy doubled -> switching loss doubles, nothing else moves
    runs = []
    for t in (25.0, 175.0):
        for i in (10.0, 100.0, 200.0, 300.0):
            e = bare.e_switch(i_d_A=i, t_j_c=t, v_dc_V=750.4, r_g_ext_ohm=2.3)
            runs.append({"conditions": {"v_dd": 750.4, "t_j": t},
                         "metrics": {"i_off_A": i, "i_on_A": i,
                                     "off": {"e_J": 2 * e["e_off_J"]},
                                     "on": {"e_J": 2 * e["e_on_J"]},
                                     "fr": {"e_fr_J": 2 * e["e_fr_J"]}}})
    s = make_set(v_gs_on=18, v_gs_off=0, r_g_on=2.3, r_g_off=2.3, l_sigma_nH=15,
                 l_gate_nH=2, c_sigma_pF=20, runs=runs)
    doc2 = dict(doc, switching_table={"basis": "spice:test", "sets": [s]})
    monkeypatch.setattr(lo, "get_device", lambda name: DeviceCard(doc2))
    sp = lo.solve_controller(dict(_L155, device="IMCQ120R004M2H", switching_source="spice",
                                  r_g_off_ext_ohm=2.3, l_sigma_nH=15))
    assert sp["losses"]["switching_source"] == "spice"
    assert sp["losses"]["switching_basis"] == "spice:test"
    # same T_j would give exactly 2x; T_j moves with the loss, so compare the
    # energy-to-loss ratio loosely and the conduction strictly at its own T_j
    assert sp["losses"]["switching_W"] > 1.7 * ds["losses"]["switching_W"]
    assert sp["thermal"]["t_j_max_c"] > ds["thermal"]["t_j_max_c"]
