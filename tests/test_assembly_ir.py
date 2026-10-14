# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""AssemblyIR: validation (each mismatch caught), mass properties against hand calculations,
and the quadcopter example.  Closed-form, no solver."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from motor_ai_sim.contracts import Provenance, RigidBodyIR
from motor_ai_sim.contracts.assembly_ir import (AssemblyComponent, AssemblyIR, CardRef, Connection, MassUnknown,
                                                Placement, PortRef, mass_properties, quat_to_matrix,
                                                validate_assembly)
from motor_ai_sim.contracts.component_ir import (ComponentCard, Frame, MassProperties, Port, box_inertia,
                                                 card_digest, diag_inertia)
from motor_ai_sim.modules import component_library as L

EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "examples" / "quadcopter_ciano14_40.assembly.json"
PROV = Provenance(module="test", contracts_version="0.1.0")


# -- tiny synthetic cards for the mismatch tests ------------------------------------------------

def _card(cid, kind, ports, mass=None, model=None) -> ComponentCard:
    mp = None if mass is None else MassProperties(mass_kg=mass, inertia_kg_m2=diag_inertia(0.01, 0.01, 0.01), basis="t")
    if kind in ("frame", "link", "wheel") and mp is None:
        mp = MassProperties(mass_kg=1.0, inertia_kg_m2=diag_inertia(0.01, 0.01, 0.01), basis="t")
    return ComponentCard(component_id="c_" + cid, kind=kind, name=cid, licence="Apache-2.0", provenance=PROV,
                         mass_properties=mp, ports=ports,
                         model=RigidBodyIR(id="c_" + cid, source={"kind": "analytical", "reference": "test"}))


def _bus(name, direction, lo, hi, amps, required=True):
    return Port(name=name, type="dc_bus", direction=direction, required=required, voltage_min_V=lo,
                voltage_max_V=hi, current_max_A=amps)


def _shaft(name, direction, kind, torque, rpm, required=True):
    return Port(name=name, type="shaft", direction=direction, required=required, rating_kind=kind,
                max_torque_Nm=torque, max_speed_rpm=rpm)


def _asm(cards, conns):
    comps = [AssemblyComponent(instance=k, card=CardRef(component_id=c.component_id)) for k, c in cards.items()]
    return AssemblyIR(id="t", components=comps,
                      connections=[Connection(a=PortRef(instance=a.split(".")[0], port=a.split(".")[1]),
                                              b=PortRef(instance=b.split(".")[0], port=b.split(".")[1]))
                                   for a, b in conns])


def _check(cards, conns):
    lib = {c.component_id: c for c in cards.values()}
    return validate_assembly(_asm(cards, conns), lib)


def test_clean_assembly_is_ok():
    cards = {"src": _card("src", "other", [_bus("o", "out", 18, 25, 60)]),
             "snk": _card("snk", "other", [_bus("i", "in", 18, 25, 30)])}
    rep = _check(cards, [("src.o", "snk.i")])
    assert rep.ok and rep.warnings == []


def test_port_types_must_match():
    cards = {"a": _card("a", "other", [_bus("o", "out", 18, 25, 60)]),
             "b": _card("b", "other", [_shaft("s", "in", "limit", 1, 1000)])}
    assert _check(cards, [("a.o", "b.s")]).codes("error") == ["type_mismatch"]


def test_directions_must_oppose():
    cards = {"a": _card("a", "other", [_bus("o", "out", 18, 25, 60)]), "b": _card("b", "other", [_bus("o", "out", 18, 25, 60)])}
    rep = _check(cards, [("a.o", "b.o")])
    assert "direction" in rep.codes("error") and not rep.ok


def test_required_ports_must_be_connected():
    cards = {"a": _card("a", "other", [_bus("o", "out", 18, 25, 60), _bus("o2", "out", 18, 25, 60, required=False)]),
             "b": _card("b", "other", [_bus("i", "in", 18, 25, 30)])}
    rep = _check(cards, [("a.o2", "b.i")])
    assert rep.codes("error") == ["unconnected_port"] and rep.errors[0].where == "a.o"


def test_no_port_used_twice():
    cards = {"a": _card("a", "other", [_bus("o", "out", 18, 25, 60)]),
             "b": _card("b", "other", [_bus("i", "in", 18, 25, 30)]),
             "c": _card("c", "other", [_bus("i", "in", 18, 25, 30)])}
    rep = _check(cards, [("a.o", "b.i"), ("a.o", "c.i")])
    assert rep.codes("error") == ["port_reused"] and rep.errors[0].where == "a.o"


def test_dc_voltage_ranges_must_overlap():
    cards = {"a": _card("a", "other", [_bus("o", "out", 18, 25.2, 60)]),
             "b": _card("b", "other", [_bus("i", "in", 40, 50, 30)])}
    assert "dc_voltage" in _check(cards, [("a.o", "b.i")]).codes("error")
    # overlapping but not contained: a warning, not an error
    cards["b"] = _card("b", "other", [_bus("i", "in", 20, 50, 30)])
    rep = _check(cards, [("a.o", "b.i")])
    assert rep.ok and rep.codes("warning") == ["dc_voltage_range"]
    # a sink that may draw more than the source gives
    cards["b"] = _card("b", "other", [_bus("i", "in", 18, 25.2, 80)])
    assert _check(cards, [("a.o", "b.i")]).codes("warning") == ["dc_current", "dc_current_sum"]


def test_shaft_limits_are_errors_and_loads_are_warnings():
    motor = _card("m", "other", [_shaft("s", "out", "limit", 2.0, 20000)])
    gearbox = _card("g", "other", [_shaft("i", "in", "limit", 1.0, 12000)])
    rep = _check({"m": motor, "g": gearbox}, [("m.s", "g.i")])
    assert rep.codes("error") == ["shaft_speed", "shaft_torque"]
    ok = _card("g", "other", [_shaft("i", "in", "limit", 2.0, 20000)])
    assert _check({"m": motor, "g": ok}, [("m.s", "g.i")]).ok
    # a propeller (demand): over-speed and a torque the motor cannot give are warnings
    prop = _card("p", "other", [_shaft("s", "in", "demand", 3.0, 15000)])
    rep = _check({"m": motor, "p": prop}, [("m.s", "p.s")])
    assert rep.ok and rep.codes("warning") == ["shaft_speed", "shaft_torque"]


def test_phase_voltage_is_an_error_current_a_warning():
    def ph(d, v, a):
        return Port(name="p", type="phase_ac", direction=d, required=True, voltage_max_V=v, current_max_A=a)
    cards = {"c": _card("c", "other", [ph("out", 50.4, 30)]), "m": _card("m", "other", [ph("in", 25.2, 20)])}
    assert _check(cards, [("c.p", "m.p")]).codes("error") == ["phase_voltage"]
    cards["c"] = _card("c", "other", [ph("out", 25.2, 30)])
    rep = _check(cards, [("c.p", "m.p")])
    assert rep.ok and rep.codes("warning") == ["phase_current"]


def test_sum_of_sinks_on_one_battery_is_warned():
    bat = _card("bat", "other", [_bus("o1", "out", 18, 25, 40), _bus("o2", "out", 18, 25, 40)])
    snk = _card("snk", "other", [_bus("i", "in", 18, 25, 30)])
    cards = {"bat": bat, "s1": snk, "s2": snk}
    rep = _check(cards, [("bat.o1", "s1.i"), ("bat.o2", "s2.i")])
    assert rep.ok and rep.codes("warning") == ["dc_current_sum"]       # 30 A + 30 A on a 40 A pack
    small = _card("snk", "other", [_bus("i", "in", 18, 25, 15)])
    assert _check({"bat": bat, "s1": small, "s2": small}, [("bat.o1", "s1.i"), ("bat.o2", "s2.i")]).warnings == []


def test_unknown_references_and_pins():
    cards = {"a": _card("a", "other", [_bus("o", "out", 18, 25, 60)])}
    asm = _asm(cards, [("a.o", "a.nope")])
    rep = validate_assembly(asm, {"c_a": cards["a"]})
    assert "unknown_port" in rep.codes("error")
    assert validate_assembly(asm, {}).codes("error")[0] == "unknown_component"
    pinned = asm.model_copy(update={"components": [AssemblyComponent(
        instance="a", card=CardRef(component_id="c_a", sha256="0" * 64))]})
    assert "card_changed" in validate_assembly(pinned, {"c_a": cards["a"]}).codes("error")
    good = pinned.model_copy(update={"components": [AssemblyComponent(
        instance="a", card=CardRef(component_id="c_a", sha256=card_digest(cards["a"]), version="0.1.0"))]})
    assert "card_changed" not in validate_assembly(good, {"c_a": cards["a"]}).codes("error")
    with pytest.raises(ValidationError, match="unknown instance"):
        AssemblyIR(id="x", components=[], connections=[Connection(a=PortRef(instance="z", port="p"),
                                                                  b=PortRef(instance="z", port="q"))])


# -- mass properties against hand calculations -----------------------------------------------------

def test_mass_properties_parallel_axis_hand_calculation():
    """A 4-arm quadcopter: plate frame at the origin, four equal point-like motors at (+-a, +-a, h)."""
    a_mm, h_mm = 150.0, 20.0
    m_frame, m_motor = 0.4, 0.1
    i_frame = box_inertia(m_frame, 0.3, 0.3, 0.02)
    frame = ComponentCard(component_id="plate", kind="frame", name="plate", licence="Apache-2.0", provenance=PROV,
                          mass_properties=MassProperties(mass_kg=m_frame, inertia_kg_m2=i_frame, basis="t"),
                          model=RigidBodyIR(id="plate", source={"kind": "analytical", "reference": "test"}))
    i_m = 2e-5
    motor = ComponentCard(component_id="blob", kind="frame", name="blob", licence="Apache-2.0", provenance=PROV,
                          mass_properties=MassProperties(mass_kg=m_motor, inertia_kg_m2=diag_inertia(i_m, i_m, i_m), basis="t"),
                          model=RigidBodyIR(id="blob", source={"kind": "analytical", "reference": "test"}))
    comps = [AssemblyComponent(instance="frame", card=CardRef(component_id="plate"))]
    for k, (sx, sy) in enumerate([(1, 1), (-1, 1), (-1, -1), (1, -1)]):
        comps.append(AssemblyComponent(instance=f"m{k}", card=CardRef(component_id="blob"),
                                       placement=Placement(position_mm=(sx * a_mm, sy * a_mm, h_mm))))
    asm = AssemblyIR(id="quad", components=comps)
    res = mass_properties(asm, {"plate": frame, "blob": motor})

    a, h = a_mm / 1000.0, h_mm / 1000.0
    M = m_frame + 4 * m_motor
    zc = 4 * m_motor * h / M                                    # by hand
    assert res.mass_kg == pytest.approx(M) and res.mass_kg == pytest.approx(0.8)
    assert res.com_m == pytest.approx((0.0, 0.0, zc), abs=1e-12) and zc == pytest.approx(0.01)
    # hand inertia about the common CoM (axes parallel to the assembly frame)
    dz_f, dz_m = -zc, h - zc
    ixx = i_frame[0][0] + m_frame * dz_f ** 2 + 4 * (i_m + m_motor * (a ** 2 + dz_m ** 2))
    iyy = i_frame[1][1] + m_frame * dz_f ** 2 + 4 * (i_m + m_motor * (a ** 2 + dz_m ** 2))
    izz = i_frame[2][2] + 4 * (i_m + m_motor * (2 * a ** 2))
    I = res.inertia_kg_m2
    assert I[0][0] == pytest.approx(ixx, rel=1e-12) and I[1][1] == pytest.approx(iyy, rel=1e-12)
    assert I[2][2] == pytest.approx(izz, rel=1e-12)
    # products of inertia cancel by symmetry
    assert abs(I[0][1]) < 1e-15 and abs(I[0][2]) < 1e-15 and abs(I[1][2]) < 1e-15
    # numbers: izz = 0.4*0.3^2/6 + 4*(2e-5 + 0.1*2*0.15^2) = 0.006 + 0.018 + 8e-5
    assert izz == pytest.approx(0.02408)


def test_rotation_of_a_component():
    """A bar (Ix small, Iy = Iz large) rotated 90 deg about z swaps Ix and Iy."""
    bar = ComponentCard(component_id="bar", kind="link", name="bar", licence="Apache-2.0", provenance=PROV,
                        mass_properties=MassProperties(mass_kg=1.0, inertia_kg_m2=diag_inertia(0.002, 0.05, 0.05), basis="t"),
                        model=RigidBodyIR(id="bar", source={"kind": "analytical", "reference": "test"}))
    s = math.sqrt(0.5)
    asm = AssemblyIR(id="r", components=[AssemblyComponent(instance="b", card=CardRef(component_id="bar"),
                                                           placement=Placement(rotation_wxyz=(s, 0, 0, s)))])
    I = mass_properties(asm, {"bar": bar}).inertia_kg_m2
    assert I[0][0] == pytest.approx(0.05) and I[1][1] == pytest.approx(0.002) and I[2][2] == pytest.approx(0.05)
    r = quat_to_matrix((s, 0, 0, s))
    assert r[0][1] == pytest.approx(-1.0) and r[1][0] == pytest.approx(1.0)


def test_unknown_mass_is_refused_not_zero():
    nomass = _card("nm", "other", [])
    asm = _asm({"x": nomass}, [])
    with pytest.raises(MassUnknown, match="x"):
        mass_properties(asm, {nomass.component_id: nomass})


# -- the quadcopter example -------------------------------------------------------------------------

@pytest.fixture(scope="module")
def example():
    return AssemblyIR.model_validate_json(EXAMPLE.read_text(encoding="utf-8"))


def test_example_passes_validate_assembly(example):
    rep = validate_assembly(example, L.resolve)
    assert rep.ok, rep.as_dict()
    inst = {c.instance: c.card.component_id for c in example.components}
    assert sum(1 for v in inst.values() if v == "motor_ciano14_40_new_l12") == 4
    assert sum(1 for v in inst.values() if v == "tmotor_fpv_10x5") == 4
    assert sum(1 for v in inst.values() if v.startswith("ctrl_")) == 4
    # every shaft, phase and dc connection of the four drive chains is present
    pairs = {(str(c.a), str(c.b)) for c in example.connections}
    for k in range(1, 5):
        assert (f"motor_{k}.shaft", f"prop_{k}.shaft") in pairs
        assert (f"esc_{k}.phase", f"motor_{k}.phase") in pairs
        assert (f"battery.dc_out_{k}", f"esc_{k}.dc_in") in pairs


def test_example_mass_properties_equal_a_hand_sum(example):
    cards = L.get_library().cards()
    res = mass_properties(example, L.resolve)
    # mass by hand from the card masses: frame + battery + 4 x (motor + propeller + controller)
    m = lambda cid: cards[cid].mass_properties.mass_kg                                # noqa: E731
    total = (m("example_quad_frame_450") + m("pack_ciano14_40_new_l12_example")
             + 4 * (m("motor_ciano14_40_new_l12") + m("tmotor_fpv_10x5") + m("ctrl_ciano14_40_new_l12_si_48k")))
    assert total == pytest.approx(0.25 + 0.85 + 4 * (0.09 + 0.0265 + 0.015)) == pytest.approx(1.626)
    assert res.mass_kg == pytest.approx(total)
    # centre of mass: x, y vanish by the four-fold symmetry; z is the mass-weighted height, by hand
    z = {c.instance: c.placement.position_mm[2] / 1000.0 for c in example.components}
    zc = (m("pack_ciano14_40_new_l12_example") * z["battery"]
          + 4 * (m("motor_ciano14_40_new_l12") * z["motor_1"] + m("tmotor_fpv_10x5") * z["prop_1"]
                 + m("ctrl_ciano14_40_new_l12_si_48k") * z["esc_1"])) / total
    assert res.com_m[0] == pytest.approx(0.0, abs=1e-12) and res.com_m[1] == pytest.approx(0.0, abs=1e-12)
    assert res.com_m[2] == pytest.approx(zc, rel=1e-12)
    # roll and pitch agree to a few percent (the four propeller blades are all modelled along x) and
    # the yaw inertia is the largest; the products of inertia cancel by the four-fold symmetry
    I = res.inertia_kg_m2
    assert I[0][0] == pytest.approx(I[1][1], rel=0.05) and I[2][2] > I[0][0]
    assert abs(I[0][1]) < 1e-12 and abs(I[0][2]) < 1e-12 and abs(I[1][2]) < 1e-12
    # the file is valid JSON of the documented shape
    doc = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert doc["ir_schema"] == "assembly-1" and len(doc["connections"]) == 21
