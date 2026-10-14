# Product Composer: from components to a finished product

**Goal.** Anyone who uses AeroStator can build a finished product from its components and simulate it as a whole, with
every component's real properties taken into account:

- a quadcopter: frame + 4 motors + propellers + controllers + battery, flown along a given trajectory;
- a robot arm: links + joint motors + gearboxes + controller, moving through a given path with a payload;
- a ground vehicle: chassis + wheel motors + gearboxes + battery, driving a given route.

What sets this apart is that the motor inside the product is not a catalogue number. It is the FEM-based motor
passport: efficiency, losses, field weakening, demagnetisation and thermal limits, coupled to the propeller, controller
and battery models we already have.

## What exists today (the building blocks)

| Block | Where | State |
|---|---|---|
| Motor performance (passport: torque-speed, efficiency and loss maps, thermal limits, mass, inertia) | `contracts/drivetrain_ir.py` `MotorPerformanceIR`, passports | live |
| Transmission (gearbox, belt, chain) | `TransmissionIR`, `modules/component_plugins/gearbox_single_stage.py` | live (one plugin) |
| Propeller / shaft load (C_T, C_P over rpm and J) | `PropulsorIR`, `ShaftLoadIR`, `propeller.py`, propeller catalogue | live |
| Motor + propeller S1 rating with slipstream cooling | `propeller_s1.py` | live |
| Controller (losses, junction temperature, continuous current limit) | `modules/controller.py` | live |
| Battery pack | `simulation/battery.py` | live |
| Drive-train solve (motor -> transmission -> load) | `modules/drivetrain/` | live |
| Duty cycles (S1/S2/S3, lumped and coupled thermal) | `thermal_duty_cycle.py`, `coupled_duty_cycle.py` | live |
| 3-D geometry of the motor (CAD generator, STEP after NDA) | `cad/` | live |
| Module registry, kernel, Component SDK, contribution rules | `modules/`, `docs/COMPONENT_SDK.md`, `docs/MODULE_CONTRIBUTION_RULES.md` | live |
| System graph (view of how the simulations are chained) | `system_graph.py`, Simulations > System, `docs/SYSTEM_GRAPH.md` | stage 1 live |

## Stages

### Stage 1: one component card for everything
Every component, ours or contributed, is described by the same card:

- **identity:** kind (motor, propeller, gearbox, controller, battery, frame, link, wheel, ...), name, version, licence, provenance;
- **geometry:** STEP (and glTF for the viewer), mounting interfaces with named frames;
- **mass properties:** mass, centre of mass, inertia tensor;
- **ports:** typed connection points: `shaft` (torque, speed), `dc_bus` (voltage, current), `phase` (AC), `thermal` (heat
  flow, temperature), `signal` (command, feedback), `mount` (rigid joint);
- **reduced-order model:** a fast model valid over a declared range, refusing outside it (the drive-train IR rule). For
  the motor this is the passport.

Done when: the existing IRs (motor, transmission, propeller) plus new ones (battery, controller, frame/link) validate
against one `ComponentCard` schema, and the component library lists them.

**Status:** the software foundation is in place (`contracts/component_ir.py` `ComponentCard`, `contracts/assembly_ir.py`
`AssemblyIR` with `validate_assembly` and `mass_properties`, adapters for motor, propeller, gearbox, battery and controller,
`GET /api/components`, the example `docs/examples/quadcopter_ciano14_40.assembly.json`); see
[COMPONENT_CARD.md](COMPONENT_CARD.md). Since 2026-10-14 every card lists its parts with materials (`parts`, material cards with sources; COMPONENT_CARD.md section 7) and a
motor card carries the mass the app computes. Open: real masses and materials for the battery, controller, gearbox and frame
placeholders, `verify` checks of the new material cards, CAD references (STEP after the NDA, glTF), and the card editor in the UI.

### Stage 2: assembly
The System graph becomes an editor:

- the user drops component cards on the canvas and connects ports (port types must match);
- the assembly yields total mass, centre of mass and inertia, a combined 3-D model, and a validity report (unconnected
  ports, voltage or current mismatches, a propeller beyond the motor's speed range, and so on);
- quick static answers: a quadcopter's hover thrust margin, hover current and flight time; a robot joint's continuous
  and peak torque against the payload; a vehicle's gradeability and range.

Done when: a CIANO14 40 quadcopter (4 x motor + propeller + controller, battery, frame) is assembled in the UI and its
hover point matches a hand calculation.

### Stage 3: mission simulation
- **Mission input:** a trajectory (waypoints and timing), a joint path, or a drive cycle.
- **Rigid-body dynamics:** an existing open engine, not our own. Candidates: MuJoCo (Apache-2.0) or Drake (BSD-3) for
  robots and multibody; a 6-DOF quadrotor model with a standard cascaded controller for multirotors.
- **Coupling per time step:** the dynamics give each motor its torque and speed. The motor passport gives current and
  losses, the controller and battery models give bus voltage and state of charge, and the lumped/coupled thermal model
  gives temperatures.
- **Limits checked continuously:** winding and magnet temperature, demagnetisation, controller current, battery current
  and voltage.
- **Results:** time series (position, attitude, motor currents and temperatures, battery state of charge), the first
  limit hit and when, energy used.

Done when: the CIANO14 40 quadcopter flies a simple trajectory, and the motor temperatures over the flight agree with the
S1/S2 results of the same motor.

### Stage 4: open exchange
- **Export:** URDF/SDF for robots, FMI 3.0 FMUs of our motor + controller models for use in other tools, STEP of the
  assembly (after the NDA, as for motors).
- **Import:** FMUs and URDF from users, so they bring their own frames, gearboxes and controllers.
- **Agents:** the assembly and mission are JSON documents available through the MCP server, so users' AI agents can
  compose and evaluate products.

## Principles
- **One unit system and refusal outside validity**, as in the drive-train IRs.
- **Permissive dependencies only** (MIT, BSD, Apache-2.0), in line with the project's free-software principle.
- **Motor design data stays private.** A component card carries performance, mass properties and the outer envelope,
  never stator or rotor design detail. The STEP of a motor is released only under the NDA flow.
- **Contributed modules** follow `docs/MODULE_CONTRIBUTION_RULES.md`. Open modules are integrated; closed ones may
  connect through the API.

## First concrete step
A quadcopter built on CIANO14 40 L12: hover point and flight time (stage 2), then a short trajectory with motor heating
(stage 3).
