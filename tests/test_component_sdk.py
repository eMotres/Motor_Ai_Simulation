# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Component SDK: IR contracts, conformance gates, registry discovery, reference
gearbox and the motor -> transmission -> load system solve (docs/COMPONENT_SDK.md).

Closed-form, no FEM: runs in a few seconds.  The motor side reuses the synthetic
passport model and propeller of ``test_propeller_s1``.
"""
from __future__ import annotations

import copy
import math

import pytest

from motor_ai_sim import propeller as pp
from motor_ai_sim import propeller_s1 as s1
from motor_ai_sim.contracts import (CONTRACTS_VERSION, ModelSource, MotorLimits, MotorPerformanceIR,
                                    OutOfValidity, PropulsorIR, ShaftInterface, ShaftLoadIR, TransmissionIR)
from motor_ai_sim.contracts.conformance import (ConformanceError, assert_component_provider,
                                                assert_motor_performance_ir, assert_propulsor_ir,
                                                assert_shaft_load_ir, assert_transmission_ir,
                                                assert_transmission_provider)
from motor_ai_sim.modules.base import ModuleManifest
from motor_ai_sim.modules.bootstrap import default_registry
from motor_ai_sim.modules.component_plugins import discover_component_plugins
from motor_ai_sim.modules.component_plugins.gearbox_single_stage import GearboxSingleStage
from motor_ai_sim.modules.drivetrain import (chain_at, motor_ir_from_s1_model, propulsor_from_propeller,
                                             solve_operating_point, solve_s1)

from .test_propeller_s1 import _model, _prop

W = math.pi / 30.0


def _gearbox(**params) -> TransmissionIR:
    return GearboxSingleStage().run({"params": params})


def _direct(ratio: float = 1.0) -> TransmissionIR:
    """A lossless coupling (ratio 1) or ideal gear: the regression reference."""
    n_ax, t_ax = [0.0, 12000.0], [0.0, 2.0]
    return TransmissionIR(
        id="ideal", kind="gearbox", ratio=ratio, speed_in_rpm=n_ax, torque_in_Nm=t_ax,
        loss_W={"none": [[0.0, 0.0], [0.0, 0.0]]}, inertia_in_kg_m2=0.0, inertia_out_kg_m2=0.0, mass_kg=0.1,
        limits={"torque_in_max_Nm": 2.0, "speed_in_max_rpm": 12000.0},
        source=ModelSource(kind="analytical", reference="test: ideal gear"),
        basis={"ratio": "test", "loss_W": "test: lossless", "inertia": "test", "mass": "test"})


def _motor(torque=(1.0, 0.05), speeds=(0.0, 12000.0), **kw) -> MotorPerformanceIR:
    kw.setdefault("basis", {"torque_peak_Nm": "test data"})
    return MotorPerformanceIR(
        id="test:motor", speed_rpm=list(speeds), torque_peak_Nm=list(torque), peak_basis="test envelope",
        limits=MotorLimits(speed_max_rpm=speeds[-1]), source=ModelSource(kind="analytical", reference="test motor"),
        **kw)


# ---------------------------------------------------------------------------
# reference gearbox + discovery
# ---------------------------------------------------------------------------

def test_reference_gearbox_passes_its_conformance_gate():
    ir = assert_transmission_provider(GearboxSingleStage())
    assert ir.id == "gearbox_single_stage" and ir.ratio == 3.0 and ir.kind == "gearbox"
    assert set(ir.loss_W) == {"drag_constant", "drag_speed_dependent", "mesh_load"}
    assert TransmissionIR.model_validate_json(ir.model_dump_json()).model_dump() == ir.model_dump()


def test_gearbox_loss_and_efficiency_follow_the_documented_formula():
    ir = _gearbox()
    n, t = 6000.0, 1.0                                    # both are map nodes: the interpolation is exact
    w = n * W
    expect = (0.004 + 0.0015 * 6.0) * w + 0.015 * t * w
    assert ir.loss_W_at(n, t) == pytest.approx(expect, rel=1e-12)
    assert ir.efficiency_at(n, t) == pytest.approx(1.0 - expect / (t * w), rel=1e-12)
    assert ir.loss_W_at(0.0, 1.0) == 0.0                  # no motion, no heat
    split = ir.loss_split_W(n, t)
    assert sum(split.values()) == pytest.approx(expect)


def test_input_torque_for_output_inverts_the_loss_map():
    ir = _gearbox()
    for n, t_out in ((3000.0, 0.8), (9000.0, 3.0), (6000.0, 0.0)):
        t_in = ir.input_torque_for_output(n, t_out)
        assert t_in * n * W - ir.loss_W_at(n, t_in) == pytest.approx(t_out * n * W / ir.ratio, abs=1e-9)
    assert ir.input_torque_for_output(0.0, 3.0) == pytest.approx(1.0)          # standstill: pure ratio
    with pytest.raises(OutOfValidity):
        ir.input_torque_for_output(6000.0, 7.0)                                # beyond the torque axis
    with pytest.raises(OutOfValidity):
        ir.loss_W_at(13000.0, 1.0)                                             # beyond the speed axis


def test_registry_lists_the_reference_gearbox_and_api_modules_shows_it():
    reg = default_registry()
    caps = reg.capabilities()
    assert "transmission.gearbox_single_stage" in caps and "geometry.2d" in caps      # nothing displaced
    assert [m.name for m in reg.components()] == ["transmission-gearbox-single-stage"]
    assert reg.components(status="verified") == [] and len(reg.components(status="reference")) == 1
    assert [type(m).__name__ for m in reg.providers_with_prefix("transmission.")] == ["GearboxSingleStage"]
    assert reg.rejected == []
    from motor_ai_sim.routes.modules import list_modules
    out = list_modules()
    row = next(m for m in out["modules"] if m["capability"] == "transmission.gearbox_single_stage")
    assert row["status"] == "reference" and row["outputs"] == ["TransmissionIR"] and row["author"]
    assert out["rejected_components"] == []
    core = next(m for m in out["modules"] if m["capability"] == "geometry.2d")
    assert core["status"] == "core"


def test_kernel_runs_a_component_like_any_module():
    from motor_ai_sim.modules.kernel import Kernel
    r = Kernel().run("transmission.gearbox_single_stage", {"params": {"ratio": 4.0}})
    assert r["ok"] and r["result"].ratio == 4.0
    bad = Kernel().run("transmission.gearbox_single_stage", {"params": {"ratioo": 4.0}})
    assert not bad["ok"] and "unknown parameters" in bad["error"]


def test_discovery_rejects_a_module_that_fails_conformance(tmp_path, monkeypatch):
    import motor_ai_sim.modules.component_plugins as pkg
    (tmp_path / "bad_box.py").write_text(
        "from motor_ai_sim.modules.component_plugins.gearbox_single_stage import GearboxSingleStage\n"
        "class BadBox(GearboxSingleStage):\n"
        "    CAPABILITY = 'transmission.bad_box'\n"
        "    def manifest(self):\n"
        "        m = super().manifest(); m.capability = 'transmission.bad_box'; m.status = 'verified-ish'; return m\n",
        encoding="utf-8")
    (tmp_path / "broken_import.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    monkeypatch.setattr(pkg, "__path__", [str(tmp_path)])
    accepted, rejected = discover_component_plugins()
    assert accepted == []
    reasons = {r["module"]: r["reason"] for r in rejected}
    assert "status" in reasons["bad_box.BadBox"] and "boom" in reasons["broken_import"]


def test_provider_gate_checks_capability_status_and_id():
    class Wrong(GearboxSingleStage):
        CAPABILITY = "transmission.other_id"

        def manifest(self):
            m = super().manifest()
            m.capability = "transmission.other_id"
            return m
    with pytest.raises(ConformanceError, match="must equal the capability suffix"):
        assert_transmission_provider(Wrong())

    class WrongStatus(GearboxSingleStage):
        def manifest(self):
            m = super().manifest()
            m.status = "trusted"
            return m
    with pytest.raises(ConformanceError, match="status"):
        assert_transmission_provider(WrongStatus())

    class NotAComponent(GearboxSingleStage):
        def manifest(self):
            m = super().manifest()
            m.capability = "geometry.2d.x"
            return m
    with pytest.raises(ConformanceError):
        assert_component_provider(NotAComponent())


# ---------------------------------------------------------------------------
# conformance: what a transmission / load / motor IR must not do
# ---------------------------------------------------------------------------

def _mut(ir, **over):
    d = ir.model_dump()
    d.update(over)
    return type(ir).model_validate(d)


def test_transmission_conformance_rejects_unphysical_maps():
    ok = _gearbox()
    assert_transmission_ir(ok)
    # units tag
    with pytest.raises(ConformanceError, match="units"):
        assert_transmission_ir(_mut(ok, units="US-customary"))
    # heat at standstill
    bad = copy.deepcopy(ok.loss_W)
    bad["drag_constant"][0] = [1.0] * len(bad["drag_constant"][0])
    with pytest.raises(ConformanceError, match="standstill"):
        assert_transmission_ir(_mut(ok, loss_W=bad))
    # loss falls with speed
    bad = copy.deepcopy(ok.loss_W)
    bad["mesh_load"][-1] = [0.0] * len(bad["mesh_load"][-1])
    with pytest.raises(ConformanceError, match="falls with speed"):
        assert_transmission_ir(_mut(ok, loss_W=bad))
    # loss larger than the input power (efficiency < 0)
    bad = copy.deepcopy(ok.loss_W)
    bad["mesh_load"] = [[1e6 * (j > 0) * (i > 0) for j in range(len(r))] for i, r in enumerate(bad["mesh_load"])]
    with pytest.raises(ConformanceError, match="faster than the input power|exceeds the input power"):
        assert_transmission_ir(_mut(ok, loss_W=bad))
    # a gear cannot deliver more torque than in * ratio
    lim = ok.limits.model_dump()
    lim["torque_out_max_Nm"] = ok.limits.torque_in_max_Nm * ok.ratio * 2
    with pytest.raises(ConformanceError, match="torque_out_max_Nm"):
        assert_transmission_ir(_mut(ok, limits=lim))
    # implausible ratio (a 1/ratio slip) and missing provenance
    with pytest.raises(ConformanceError, match="ratio"):
        assert_transmission_ir(_mut(ok, ratio=5000.0))
    with pytest.raises(ConformanceError, match="provenance"):
        assert_transmission_ir(_mut(ok, basis={"ratio": "x"}))
    # the map must cover the declared range
    lim = ok.limits.model_dump()
    lim["speed_in_max_rpm"] = 20000.0
    with pytest.raises(ConformanceError, match="cover"):
        assert_transmission_ir(_mut(ok, limits=lim))


def test_schema_errors_are_loud_at_construction():
    ok = _gearbox().model_dump()
    d = copy.deepcopy(ok)
    d["speed_in_rpm"] = [0.0, 5.0, 5.0] + d["speed_in_rpm"][3:]
    with pytest.raises(ValueError, match="strictly increasing"):
        TransmissionIR.model_validate(d)
    d = copy.deepcopy(ok)
    d["loss_W"]["mesh_load"] = d["loss_W"]["mesh_load"][:-1]
    with pytest.raises(ValueError, match="table"):
        TransmissionIR.model_validate(d)
    d = copy.deepcopy(ok)
    d["surprise"] = 1
    with pytest.raises(ValueError):
        TransmissionIR.model_validate(d)                          # extra="forbid": no smuggled fields


def test_motor_ir_is_performance_only_and_checked():
    m = _motor(mass_kg=0.2, rotor_inertia_kg_m2=1e-5, basis={"torque_peak_Nm": "test data", "mass_kg": "test data", "rotor_inertia_kg_m2": "test data"})
    assert_motor_performance_ir(m)
    with pytest.raises(Exception):
        MotorPerformanceIR.model_validate({**m.model_dump(), "stator_diameter": 40.0})   # no such field
    leaky = _mut(m, basis={"torque_peak_Nm": "test data", "mass_kg": "test data", "rotor_inertia_kg_m2": "test data", "slot_height": "6 mm"})
    with pytest.raises(ConformanceError, match="geometry field"):
        assert_motor_performance_ir(leaky)
    with pytest.raises(ConformanceError, match="implausible"):
        assert_motor_performance_ir(_mut(m, mass_kg=200000.0))          # grams written as kg


def test_motor_ir_from_a_passport_model_has_no_geometry():
    m = _model()
    ir = motor_ir_from_s1_model(m, mass_kg=0.05, rotor_inertia_kg_m2=1e-6,
                                shaft=ShaftInterface(kind="bore", diameter_mm=5.0))
    assert_motor_performance_ir(ir)
    assert "thermal_geom" not in ir.model_dump_json() and "statorOD" not in ir.model_dump_json()
    assert ir.limits.speed_max_rpm == 12000.0 and ir.s1_available
    assert max(ir.torque_peak_Nm) == pytest.approx(0.03 * 60.0, rel=0.02)    # KT * I_max
    etas = [v for row in ir.efficiency.eta for v in row if v is not None]
    assert etas and all(0.0 < v < 1.0 for v in etas)


def test_propulsor_ir_matches_the_catalogue_model(tmp_path):
    prop = _prop(tmp_path)
    ir = propulsor_from_propeller(prop)
    assert_propulsor_ir(ir)
    lo, hi = prop.rpm_range
    for rpm in (lo, 0.5 * (lo + hi), 0.93 * hi, hi):
        p = ir.evaluate(rpm, rho=1.225)
        assert p.output == pytest.approx(pp.thrust_N(prop, rpm, 1.225), rel=1e-3)
        assert p.torque_Nm == pytest.approx(pp.torque_Nm(prop, rpm, 1.225), rel=1e-3)
        assert p.wake_speed_ms == pytest.approx(pp.wake_velocity_ms(prop, rpm, 1.225), rel=1e-3)
        assert not p.extrapolated
    assert ir.evaluate(1.3 * hi).extrapolated                             # held at the edge, and flagged
    with pytest.raises(OutOfValidity, match="static"):
        ir.evaluate(0.5 * (lo + hi), airspeed_ms=5.0)                     # static data say nothing about flight


def test_propulsor_conformance_catches_swapped_tables():
    ir = PropulsorIR(
        id="flight_prop", diameter_m=0.25, speed_rpm=[3000.0, 8000.0], advance_ratio=[0.0, 0.5],
        ct=[[0.1, 0.05], [0.1, 0.05]], cp=[[0.045, 0.04], [0.045, 0.04]],
        source=ModelSource(kind="measured", reference="test"), basis={"ct": "test data", "cp": "test data", "diameter_m": "test data"})
    assert_propulsor_ir(ir)
    flight = ir.evaluate(5000.0, airspeed_ms=0.2 * (5000.0 / 60.0) * 0.25)         # J = 0.2
    assert flight.output > 0
    bad = _mut(ir, ct=[[0.1, 0.3], [0.1, 0.3]])                                   # J*C_T/C_P = 0.5*0.3/0.04 > 1
    with pytest.raises(ConformanceError, match="propulsive efficiency"):
        assert_propulsor_ir(bad)


def test_generic_shaft_load():
    ir = ShaftLoadIR(id="fan", kind="fan", speed_rpm=[0.0, 1000.0, 2000.0], torque_Nm=[[0.0], [0.1], [0.4]],
                     source=ModelSource(kind="measured", reference="test"), basis={"torque_Nm": "test data"})
    assert_shaft_load_ir(ir)
    assert ir.evaluate(1500.0).torque_Nm == pytest.approx(0.25)
    with pytest.raises(OutOfValidity):
        ir.evaluate(2500.0)                                                       # generic loads never extrapolate


# ---------------------------------------------------------------------------
# system solve: operating point
# ---------------------------------------------------------------------------

def test_direct_operating_point_equals_the_existing_equilibrium(tmp_path):
    prop = _prop(tmp_path, d_m=0.5)
    motor = _motor(torque=(2.0, 0.6))
    ref = pp.equilibrium_rpm(prop, motor.speed_rpm, motor.torque_peak_Nm)
    assert ref["limited_by"] == "torque_equilibrium"
    out = solve_operating_point(motor, None, propulsor_from_propeller(prop))
    assert out["limited_by"] == "torque_equilibrium"
    assert out["point"]["motor_rpm"] == pytest.approx(ref["rpm"], rel=2e-3)
    assert out["point"]["load_output"]["value"] == pytest.approx(ref["thrust_N"], rel=3e-3)
    assert out["point"]["motor_torque_Nm"] == pytest.approx(ref["motor_torque_at_point_Nm"], rel=3e-3)


def test_operating_point_through_the_gearbox_balances_power(tmp_path):
    prop = _prop(tmp_path, d_m=0.6)
    load = propulsor_from_propeller(prop)
    motor = _motor(torque=(2.0, 0.6))
    out = solve_operating_point(motor, _gearbox(), load)
    p = out["point"]
    assert out["limited_by"] == "torque_equilibrium"
    assert p["motor_rpm"] == pytest.approx(3.0 * p["output_rpm"])
    assert p["motor_torque_Nm"] == pytest.approx(p["motor_torque_peak_Nm"], rel=1e-6)           # full throttle
    assert p["motor_shaft_power_W"] - p["transmission_loss_W"] == pytest.approx(p["load_shaft_power_W"], rel=1e-9)
    assert p["load_shaft_power_W"] == pytest.approx(load.evaluate(p["output_rpm"], rho=pp.RHO_ISA).shaft_power_W)
    assert 0.8 < p["transmission_efficiency"] < 1.0
    assert p["load_output"]["name"] == "thrust" and p["load_output"]["value"] > 0
    names = {c["name"] for c in out["checks"]}
    assert {"transmission_torque_in", "transmission_speed_in", "transmission_heat",
            "interface_motor_to_transmission"} <= names
    assert out["mass_kg_total"] is None and out["inertia_reflected_to_motor_kg_m2"] is None   # unknown masses stay unknown
    # the same chain without the gearbox spins the prop at the motor speed: a different point
    d = solve_operating_point(motor, None, load)
    assert d["point"]["motor_rpm"] != pytest.approx(p["motor_rpm"], rel=0.05)


def test_operating_point_reports_when_a_declared_range_ends_first(tmp_path):
    prop = _prop(tmp_path, d_m=0.25)                       # light prop: no crossing in the motor's range
    load = propulsor_from_propeller(prop)
    out = solve_operating_point(_motor(torque=(2.0, 1.5)), None, load)
    assert out["limited_by"] == "motor_speed_limit" and out["point"]["motor_rpm"] == pytest.approx(12000.0)
    assert out["point"]["extrapolated"] is True            # above the tested rpm range: held and flagged
    # the same chain through a gearbox whose loss maps stop at 6000 rpm
    short = _mut(_gearbox(), speed_in_rpm=[0.0, 6000.0], loss_W={k: [v[0], v[6]] for k, v in _gearbox().loss_W.items()},
                 limits={**_gearbox().limits.model_dump(), "speed_in_max_rpm": 6000.0, "speed_out_max_rpm": 2000.0})
    out = solve_operating_point(_motor(torque=(2.0, 1.5)), short, load)
    assert out["point"]["motor_rpm"] <= 6000.0 + 1e-6


def test_interface_mismatch_and_missing_data_are_reported():
    load = ShaftLoadIR(id="fan", kind="fan", speed_rpm=[0.0, 12000.0], torque_Nm=[[0.0], [0.5]],
                       shaft_interface=ShaftInterface(kind="keyed", diameter_mm=10.0),
                       source=ModelSource(kind="measured", reference="test"), basis={"torque_Nm": "test data"})
    motor = _motor(shaft=ShaftInterface(kind="bore", diameter_mm=8.0))
    out = solve_operating_point(motor, _gearbox(), load)
    by = {c["name"]: c for c in out["checks"]}
    assert by["interface_motor_to_transmission"]["ok"] is True          # 8 mm into the gearbox's 8 mm input
    assert by["interface_transmission_to_load"]["ok"] is False          # 12 mm output vs a 10 mm load shaft
    assert out["ok"] is False
    assert by["transmission_torque_out"]["ok"] is True
    out2 = solve_operating_point(_motor(), None, load)
    assert [c["ok"] for c in out2["checks"] if c["name"].startswith("interface")] == [None]
    assert out2["unverified"] == ["interface_motor_to_load"]


# ---------------------------------------------------------------------------
# system solve: S1 through a transmission
# ---------------------------------------------------------------------------

def test_s1_without_transmission_reproduces_the_existing_rating(tmp_path):
    m, prop = _model(), _prop(tmp_path)
    ref = s1.s1_rating(m, prop, rpm_min=1000.0, rpm_max=12000.0)
    mine = solve_s1(m, None, propulsor_from_propeller(prop), rpm_min=1000.0, rpm_max=12000.0)
    assert ref["limit"] is not None and mine["limit"] is not None
    assert mine["limit"]["motor_rpm"] == pytest.approx(ref["limit"]["rpm"], abs=3.0)
    assert mine["limit"]["binding"] == ref["limit"]["binding"]
    assert mine["limit"]["T_winding_C"] == pytest.approx(ref["limit"]["T_winding_C"], abs=0.5)
    assert mine["monotone"] == ref["monotone"]


def test_s1_with_an_ideal_one_to_one_coupling_is_the_same_as_none(tmp_path):
    m, prop = _model(), _prop(tmp_path)
    load = propulsor_from_propeller(prop)
    a = solve_s1(m, None, load, rpm_min=1000.0, rpm_max=12000.0)
    b = solve_s1(m, _direct(1.0), load, rpm_min=1000.0, rpm_max=12000.0)
    assert b["limit"]["motor_rpm"] == pytest.approx(a["limit"]["motor_rpm"], abs=2.0)
    assert b["limit"]["transmission_efficiency"] == pytest.approx(1.0)


def test_s1_through_the_gearbox_adds_its_loss_and_checks_its_rating(tmp_path):
    m, prop = _model(), _prop(tmp_path, d_m=0.5)
    load = propulsor_from_propeller(prop)
    gb = _gearbox(torque_in_max_Nm=2.0)
    r = solve_s1(m, gb, load, rpm_min=1200.0, rpm_max=12000.0, winding_limit_c=400.0, magnet_limit_c=400.0)
    row = next(x for x in r["rows"] if x["ok"])
    t = row["transmission"]
    assert row["output_rpm"] == pytest.approx(row["rpm"] / 3.0)
    assert row["torque_prop_Nm"] == pytest.approx(t["torque_in_Nm"])               # the motor carries the torque AFTER the loss
    assert t["torque_in_Nm"] > t["torque_out_Nm"] / 3.0                            # loss makes the motor work harder
    assert t["loss_W"] == pytest.approx(gb.loss_W_at(row["rpm"], t["torque_in_Nm"]))
    # a gearbox that cannot reject its own heat binds the chain, whatever the motor can do
    hot = solve_s1(m, _gearbox(loss_continuous_max_W=1.0), load, rpm_min=1200.0, rpm_max=12000.0,
                   winding_limit_c=400.0, magnet_limit_c=400.0)
    assert hot["limit"] is not None and hot["limit"]["binding"] == "transmission_heat"
    assert hot["limit"]["transmission_loss_W"] <= 1.0 + 1e-6
    assert hot["limit"]["motor_rpm"] < (r["limit"]["motor_rpm"] if r["limit"]["binding"] == "speed_range" else 12001.0)
    # chain_at is the shared building block
    c = chain_at(6000.0, gb, load)
    assert c["n_out_rpm"] == pytest.approx(2000.0) and c["loss_W"] > 0 and 0 < c["efficiency"] < 1


def test_s1_hook_default_is_the_old_path(tmp_path):
    m, prop = _model(), _prop(tmp_path)
    lims = s1.limits_of(m)
    rho = pp.RHO_ISA
    a = s1.solve_point(m, prop, 5000.0, ambient_c=25.0, rho=rho, lims=lims)
    b = s1.solve_point(m, prop, 5000.0, ambient_c=25.0, rho=rho, lims=lims, load=None)
    assert a == b


def test_contracts_version_is_stamped():
    assert _gearbox().provenance.contracts_version == CONTRACTS_VERSION
