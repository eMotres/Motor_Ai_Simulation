# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""ComponentCard: schema, conformance gate, adapters over real store items, the motor privacy rule,
the library module and the route + its gate.  Closed-form, no solver."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from motor_ai_sim import auth
from motor_ai_sim import propeller as pp
from motor_ai_sim import propeller_s1 as s1
from motor_ai_sim import users as U
from motor_ai_sim.api import app
from motor_ai_sim.contracts import (BatteryIR, ComponentCard, ControllerIR, MotorPerformanceIR, Provenance,
                                    RigidBodyIR)
from motor_ai_sim.contracts.component_ir import (CadRef, CadRefs, Envelope, Frame, MassProperties, Port,
                                                 box_inertia, cylinder_inertia)
from motor_ai_sim.contracts.conformance import ConformanceError, assert_component_card
from motor_ai_sim.contracts.drivetrain_ir import OutOfValidity, PropulsorIR, TransmissionIR
from motor_ai_sim.modules import component_library as L
from motor_ai_sim.modules.bootstrap import default_registry
from motor_ai_sim.modules.component_adapters import (battery_card_from_block, controller_card_from_drive,
                                                     motor_card_from_s1_model, propeller_card, transmission_card)
from motor_ai_sim.modules.component_plugins.gearbox_single_stage import GearboxSingleStage

client = TestClient(app)



@pytest.fixture(scope="module", autouse=True)
def stand_in_dies(tmp_path_factory):
    """The motors' mass comes from the app's mass model on their die: give it a stand-in catalog (tests/_component_dies.py)."""
    from tests._component_dies import make_dies
    mp = pytest.MonkeyPatch()
    mp.setenv("MOTRES_DIES_DIR", str(make_dies(tmp_path_factory.mktemp("catalog"))))
    L.get_library(refresh=True)
    yield
    mp.undo()
    L._LIB = None                                                         # noqa: SLF001


@pytest.fixture(scope="module")
def model():
    return s1.load_model("CIANO14 40 new", "L12")


@pytest.fixture(scope="module")
def motor(model):
    return motor_card_from_s1_model(model, mass_kg=0.09, mass_basis="test")


def _roundtrip(card: ComponentCard) -> None:
    assert_component_card(card)
    assert ComponentCard.model_validate_json(card.model_dump_json()).model_dump() == card.model_dump()


# -- adapters over real store items ------------------------------------------------------------

def test_motor_adapter_real_passport(motor):
    _roundtrip(motor)
    assert motor.kind == "motor" and isinstance(motor.model, MotorPerformanceIR)
    assert motor.model.s1_available and motor.model.limits.speed_max_rpm > 20000
    shaft, phase = motor.port("shaft"), motor.port("phase")
    assert shaft.direction == "out" and shaft.max_speed_rpm == motor.model.limits.speed_max_rpm
    assert shaft.max_torque_Nm == pytest.approx(max(motor.model.torque_peak_Nm))
    assert phase.type == "phase_ac" and phase.voltage_max_V == motor.model.limits.dc_voltage_max_V
    assert motor.envelope.shape == "cylinder" and motor.envelope.diameter_mm == 40.0 and motor.envelope.length_mm == 12.0
    # mass properties: inertia of the envelope cylinder, flagged as an estimate
    mp = motor.mass_properties
    assert mp.estimated and mp.mass_kg == 0.09
    assert mp.inertia_kg_m2[2][2] == pytest.approx(0.5 * 0.09 * 0.02 ** 2)
    assert motor.model.mass_kg == mp.mass_kg


def test_motor_card_leaks_no_design_geometry(model, motor):
    text = motor.model_dump_json()
    # every key of the passport model's geometry block, and the block itself, stays out of the card
    for key in model["thermal_geom"]:
        if key not in ("statorOD_mm", "stackLength_mm"):
            assert key not in text, key
    assert "thermal_geom" not in text
    # nothing else of the card is geometry: only the envelope (a plain cylinder) and two derived frames
    assert set(motor.envelope.model_dump(exclude_none=True)) <= {"shape", "diameter_mm", "length_mm", "axis",
                                                                 "centre_mm", "basis"}
    assert [f.name for f in motor.frames] == ["mount", "shaft"]
    # and the gate walks the dumped card against the project's denylist
    from motor_ai_sim.mcp_tools import GEOMETRY_DENYLIST, assert_no_geometry
    assert_no_geometry(motor.model_dump(mode="json"))
    assert GEOMETRY_DENYLIST  # the denylist is importable, so the gate really ran


def test_motor_gate_rejects_geometry_beyond_the_envelope(motor):
    # 1) a public STEP on a motor
    leaked = motor.model_copy(update={"cad": CadRefs(step=CadRef(uri="https://example.com/m.step"))})
    with pytest.raises(ConformanceError, match="nda"):
        assert_component_card(leaked)
    ok = motor.model_copy(update={"cad": CadRefs(step=CadRef(uri="nda://ciano14_40_new_l12.step", access="nda"))})
    assert_component_card(ok)
    # 2) a denylisted key smuggled through a free-text dict
    prov = Provenance(module="x", contracts_version="0.1.0", notes={"slot_height": "6.1"})
    with pytest.raises(ConformanceError, match="design geometry"):
        assert_component_card(motor.model_copy(update={"provenance": prov}))
    # 3) the schema has no field for it at all
    doc = motor.model_dump(mode="json")
    for extra in ({"geometry": {"slots": 12}}, {"winding": {"turns": 9}}):
        with pytest.raises(ValidationError):
            ComponentCard.model_validate({**doc, **extra})
    mdoc = dict(doc["model"], slot_height_mm=6.1)
    with pytest.raises(ValidationError):
        ComponentCard.model_validate({**doc, "model": mdoc})


def test_propeller_adapter_real_catalogue():
    prop = pp.get_propeller("tmotor_fpv_10x5")
    c = propeller_card(prop)
    _roundtrip(c)
    assert c.kind == "propeller" and isinstance(c.model, PropulsorIR) and c.licence.startswith("LicenseRef-")
    assert c.mass_properties.mass_kg == pytest.approx(0.0265)
    sh = c.port("shaft")
    assert sh.direction == "in" and sh.rating_kind == "demand"
    lp = c.model.evaluate(sh.max_speed_rpm)
    assert sh.max_torque_Nm == pytest.approx(lp.torque_Nm)
    # a catalogue entry without usable coefficients is refused, not guessed
    bad = next(p for p in pp.list_propellers() if not p.selectable)
    with pytest.raises(Exception):
        propeller_card(bad)


def test_gearbox_adapter():
    ir = GearboxSingleStage().run({})
    c = transmission_card(ir)
    _roundtrip(c)
    assert isinstance(c.model, TransmissionIR) and c.component_id == "gearbox_single_stage"
    assert c.port("shaft_in").max_speed_rpm == ir.limits.speed_in_max_rpm
    assert c.port("shaft_out").max_speed_rpm == pytest.approx(ir.limits.speed_in_max_rpm / ir.ratio)
    assert c.port("shaft_out").interface.diameter_mm == 12.0
    assert c.mass_properties.mass_kg == ir.mass_kg and c.mass_properties.estimated


def test_battery_adapter(model):
    c = battery_card_from_block(model["battery"], component_id="pack_test", n_parallel=2, capacity_ah_per_string=5.0,
                                i_continuous_A=70.0, mass_kg=0.85, n_outputs=3)
    _roundtrip(c)
    ir = c.model
    assert isinstance(ir, BatteryIR) and (ir.cells_series, ir.cells_parallel) == (6, 2)
    assert ir.capacity_Ah == pytest.approx(10.0)
    # battery.py: R_pack = NS * r_int / NP = 6 * 12 mOhm / 2
    assert ir.r_internal_at(0.5) == pytest.approx(6 * 0.012 / 2)
    assert ir.terminal_voltage(0.5, 20.0) == pytest.approx(22.2 - 20.0 * 0.036)
    with pytest.raises(OutOfValidity):
        ir.r_internal_at(1.2)
    assert [p.name for p in c.ports if p.type == "dc_bus"] == ["dc_out_1", "dc_out_2", "dc_out_3"]
    # with nothing given the battery.py placeholders apply and the card says so
    d = battery_card_from_block(model["battery"], component_id="pack_default")
    assert "PLACEHOLDER" in d.model.basis["i_continuous_max_A"] and d.model.capacity_Ah == pytest.approx(10.0)


def test_controller_adapter(model):
    c = controller_card_from_drive(model, mass_kg=0.015)
    _roundtrip(c)
    ir = c.model
    assert isinstance(ir, ControllerIR) and ir.loss_model.kind == "device_card"
    assert ir.loss_model.device_part == model["drives"][model["default_drive"]]["device"]
    lim = model["drives"][model["default_drive"]]["i_board_limit_A"]
    assert ir.phase_current_continuous_A == pytest.approx(min(lim.values()))
    assert (ir.bus_voltage_min_V, ir.bus_voltage_max_V) == (18.0, 25.2)
    assert ir.t_junction_max_C and ir.t_junction_max_C > 100            # from the device card
    assert c.port("dc_in").direction == "in" and c.port("phase").direction == "out"
    with pytest.raises(ValueError):
        controller_card_from_drive(model, "no_such_drive")


def test_library_lists_every_adapter_and_rejects_nothing():
    lib = L.build_library()
    assert lib.rejected == []
    kinds = {e.card.kind for e in lib.entries.values()}
    assert {"motor", "propeller", "transmission", "controller", "battery", "frame"} <= kinds
    assert "motor_ciano14_40_new_l12" in lib.entries and "tmotor_fpv_10x5" in lib.entries
    assert lib.entries["motor_ciano14_40_new_l12"].die == "CIANO14 40 new"
    # the motor mass is the APP's mass (stand-in catalog here): known, and it grows with the stack length
    m12 = lib.entries["motor_ciano14_40_new_l12"].card.mass_properties
    m20 = lib.entries["motor_ciano14_40_new_l20"].card.mass_properties
    assert m12 is not None and m20 is not None and m20.mass_kg > m12.mass_kg > 0.05
    for e in lib.entries.values():
        _roundtrip(e.card)
    # the placeholders are marked
    assert "placeholder" in lib.entries["example_quad_frame_450"].flags
    # a die that is not in the catalog: the card is listed with NO mass and the flag (unknown, never 0)
    d85 = lib.entries["motor_ciano28_85_20sw1200_l13"]
    assert d85.card.mass_properties is None and "mass_unknown" in d85.flags and not d85.card.parts


# -- the schema ----------------------------------------------------------------------------------

def _frame_card(**over) -> ComponentCard:
    mp = MassProperties(mass_kg=1.0, inertia_kg_m2=box_inertia(1.0, 0.1, 0.1, 0.1), basis="test")
    ir = RigidBodyIR(id="x", source={"kind": "analytical", "reference": "test"})
    kw = dict(component_id="test_frame", kind="frame", name="t", licence="Apache-2.0",
              provenance=Provenance(module="t", contracts_version="0.1.0"), mass_properties=mp,
              frames=[Frame(name="a")], ports=[Port(name="m1", type="mount", direction="bidirectional", frame="a")],
              model=ir)
    kw.update(over)
    return ComponentCard(**kw)


def test_schema_rules():
    assert_component_card(_frame_card())
    with pytest.raises(ValidationError):                         # extra fields are refused
        _frame_card(colour="red")
    with pytest.raises(ValidationError, match="other major"):
        _frame_card(schema_version="1.0.0")
    with pytest.raises(ValidationError, match="semver"):
        _frame_card(schema_version="one")
    with pytest.raises(ValidationError, match="cannot carry"):   # a frame cannot carry a motor model
        _frame_card(kind="motor")
    with pytest.raises(ValidationError, match="needs mass_properties"):
        _frame_card(mass_properties=None)
    with pytest.raises(ValidationError, match="unknown frame"):
        _frame_card(ports=[Port(name="m1", type="mount", direction="bidirectional", frame="nope")])
    with pytest.raises(ValidationError, match="unique"):
        _frame_card(frames=[Frame(name="a"), Frame(name="a")])
    with pytest.raises(ValidationError, match="SPDX"):
        _frame_card(licence="my own licence!")
    with pytest.raises(ValidationError, match="unit quaternion"):
        Frame(name="a", rotation_wxyz=(1.0, 1.0, 0.0, 0.0))


def test_mass_properties_are_validated():
    ok = box_inertia(2.0, 0.2, 0.1, 0.05)
    MassProperties(mass_kg=2.0, inertia_kg_m2=ok)
    with pytest.raises(ValidationError, match="symmetric"):
        MassProperties(mass_kg=2.0, inertia_kg_m2=((0.01, 0.002, 0), (0.0, 0.01, 0), (0, 0, 0.01)))
    with pytest.raises(ValidationError, match="positive definite"):
        MassProperties(mass_kg=2.0, inertia_kg_m2=((0.01, 0, 0), (0, 0, 0), (0, 0, 0.01)))
    with pytest.raises(ValidationError, match="positive definite"):
        MassProperties(mass_kg=2.0, inertia_kg_m2=((0.01, 0.02, 0), (0.02, 0.01, 0), (0, 0, 0.01)))
    with pytest.raises(ValidationError, match="not physical"):    # Ia + Ib < Ic
        MassProperties(mass_kg=2.0, inertia_kg_m2=((0.001, 0, 0), (0, 0.001, 0), (0, 0, 0.01)))
    assert cylinder_inertia(1.0, 0.2, 0.1, "x")[0][0] == pytest.approx(0.5 * 0.1 ** 2)


def test_port_ratings_follow_the_type():
    with pytest.raises(ValidationError, match="needs max_torque_Nm"):
        Port(name="s", type="shaft", direction="out", max_speed_rpm=1000)
    with pytest.raises(ValidationError, match="not a rating of a shaft"):
        Port(name="s", type="shaft", direction="out", max_speed_rpm=1000, max_torque_Nm=1, voltage_max_V=12)
    with pytest.raises(ValidationError, match="voltage_min_V above"):
        Port(name="b", type="dc_bus", direction="in", voltage_min_V=30, voltage_max_V=20, current_max_A=5)
    with pytest.raises(ValidationError, match="bidirectional"):
        Port(name="m", type="mount", direction="in")
    assert Port(name="s", type="shaft", direction="out", max_speed_rpm=1000, max_torque_Nm=1).rating_kind == "limit"


def test_cad_and_envelope_validation():
    with pytest.raises(ValidationError, match="sha256"):
        CadRef(uri="a.step", sha256="abc")
    CadRef(uri="a.step", sha256="0" * 64)
    with pytest.raises(ValidationError):
        Envelope(shape="box", diameter_mm=10, length_mm=10)
    with pytest.raises(ValidationError):
        Envelope(shape="cylinder", size_mm=(1, 1, 1))


# -- registry, module, route -----------------------------------------------------------------

def test_module_is_registered_and_runs():
    reg = default_registry()
    mod = reg.provider("component.library")
    assert mod.manifest().outputs == ["ComponentCard"]
    rows = mod.run({"kind": "propeller"})
    assert rows and all(r["kind"] == "propeller" for r in rows)
    card = mod.run({"id": "tmotor_fpv_10x5"})
    assert isinstance(card, ComponentCard)
    with pytest.raises(KeyError):
        mod.run({"id": "nope"})


@pytest.fixture()
def accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {"owner@example.com"})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    out = {}
    for who, role in (("owner@example.com", "admin"), ("eng@example.com", "engineer"), ("usr@example.com", "user")):
        U.create_user(who, "password-" + role, role=role, name=role)
        out[role] = {"Authorization": f"Bearer {U.issue_token(who)}"}
    return out


def test_route_gate_and_content(accounts):
    for url in ("/api/components", "/api/components/tmotor_fpv_10x5"):
        assert client.get(url, headers=accounts["engineer"]).status_code == 200
        assert client.get(url, headers=accounts["admin"]).status_code == 200
        assert client.get(url, headers=accounts["user"]).status_code == 403
        assert client.get(url).status_code == 401
    adm = accounts["admin"]
    r = client.get("/api/components", params={"kind": "propeller"}, headers=adm).json()
    assert r["count"] > 3 and {c["kind"] for c in r["components"]} == {"propeller"}
    assert client.get("/api/components", params={"kind": "toaster"}, headers=adm).status_code == 400
    one = client.get("/api/components/motor_ciano14_40_new_l12", headers=adm)
    assert one.status_code == 200
    card = ComponentCard.model_validate(one.json()["card"])
    assert card.kind == "motor"
    assert client.get("/api/components/nope", headers=adm).status_code == 404
    # nothing that leaves the server carries design geometry
    from motor_ai_sim.mcp_tools import assert_no_geometry
    assert_no_geometry(json.loads(one.text))
