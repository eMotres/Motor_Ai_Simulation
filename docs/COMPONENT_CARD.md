# Component card

One card describes every part of a product: ours or contributed, a motor or a frame. It is stage 1 of the
[product composer](PRODUCT_COMPOSER_ROADMAP.md): the base that the assembly (stage 2) and the mission simulation
(stage 3) plug into. The card reuses the drive-train IRs of the [Component SDK](COMPONENT_SDK.md); it does not copy them.

Code: `contracts/component_ir.py` (card), `contracts/assembly_ir.py` (assembly document, validator, mass properties),
`contracts/conformance/component.py` (gate), `modules/component_adapters.py` (store to card),
`modules/component_library.py` (the library), `routes/components.py` (API). Tests: `tests/test_component_card.py`,
`tests/test_assembly_ir.py`. Example: [examples/quadcopter_ciano14_40.assembly.json](examples/quadcopter_ciano14_40.assembly.json).

## 1. The schema

A `ComponentCard` is a pydantic model with `extra="forbid"`, `ir_schema = "component_card-1"`, a semver `schema_version`
(same major = readable) and the family unit tag `units = "SI-rpm-v1"` (N*m, rpm, W, kg, kg*m^2, degC, V, A). Lengths on a
card are in mm (frames, envelope); the centre of mass is in m. Every field name carries its unit.

| Block | Fields |
|---|---|
| identity | `component_id` (lower snake / dots / dashes), `kind`, `name`, `version` (semver), `licence` (SPDX expression), `description`, `provenance` |
| kinds | `motor`, `propeller`, `transmission`, `controller`, `battery`, `frame`, `link`, `wheel`, `other` |
| shape | `cad` (optional `step`, `gltf`: `uri`, `sha256`, `access`), `envelope` (a `box` or a `cylinder`, mm), `frames` (named; `position_mm` and `rotation_wxyz`, a unit quaternion) |
| mass | `mass_properties`: `mass_kg`, `com_m`, `inertia_kg_m2` (3 x 3 about the centre of mass, axes parallel to the body frame), `estimated`, `basis`. Validated: symmetric, positive definite, principal moments obey Ia + Ib >= Ic |
| ports | list of `Port` (section 2) |
| model | the reduced-order model, one of the tagged IRs below (`ir_schema` selects it) |

Which model a kind may carry (`KIND_MODELS`): motor `motor_performance-1`; propeller `propulsor-1` or `shaft_load-1`;
transmission `transmission-1`; controller `controller-1`; battery `battery-1`; frame, link, wheel `rigid_body-1`; `other` any.

| Model | Where | Content |
|---|---|---|
| `MotorPerformanceIR` | `drivetrain_ir.py` (existing) | torque-speed envelope, efficiency map, limits, mass, inertia, shaft |
| `TransmissionIR` | `drivetrain_ir.py` (existing) | ratio, loss maps, inertia, mass, limits |
| `PropulsorIR`, `ShaftLoadIR` | `drivetrain_ir.py` (existing) | C_T / C_P over (rpm, J), or a torque map |
| `BatteryIR` (new) | `component_ir.py` | cells in series / parallel, nominal / min / max V, capacity Ah, internal resistance (ohm) over the state of charge, optional open-circuit voltage table, continuous and peak current, mass |
| `ControllerIR` (new) | `component_ir.py` | bus voltage range, continuous / peak phase current (and per cooling class), loss model (a device-card reference or a table), junction-temperature limit, mass |
| `RigidBodyIR` (new) | `component_ir.py` | frame, link, wheel: nothing but "this is a rigid body" (mass properties and mounts are on the card) |

Mass that the stores do not hold is **unknown**: `mass_properties` is `null` and an assembly refuses to add it
(`MassUnknown`); it is never 0.

## 2. Ports

A port is a typed connection point: `name`, `type`, `direction` (`in`, `out`, `bidirectional`), optional `frame` (a frame of the
card), `required` (the assembly must connect it) and the ratings of its type. A rating that does not belong to the type is
refused at construction.

| Type | Ratings | Directions | Meaning |
|---|---|---|---|
| `shaft` | `max_torque_Nm`, `max_speed_rpm`, `rating_kind`, optional `interface` (diameter, kind) | out = a source (motor shaft, gearbox output), in = a sink | `rating_kind = limit`: a mechanical limit (gearbox input). `demand`: what a load draws at its top speed (propeller) |
| `dc_bus` | `voltage_min_V`, `voltage_max_V`, `current_max_A` (continuous), `current_peak_A` | out = battery, in = controller | the pack rating is for the whole pack: several outputs share it |
| `phase_ac` | `voltage_max_V` (the DC link the drive runs from), `current_max_A` (rms per phase) | out = controller, in = motor | |
| `thermal` | `heat_flow_max_W`, `temperature_max_C` | any | reserved for the thermal coupling (stage 3) |
| `signal` | `protocol` | any | command and feedback |
| `mount` | `load_max_N`, `moment_max_Nm` | bidirectional | rigid joint to a frame, link or body |

## 3. Assembly checks (`validate_assembly`)

The assembly document (`AssemblyIR`) lists instances (a card reference plus a placement frame) and connections port to
port. `validate_assembly(asm, cards)` returns a report with errors (invalid) and warnings:

* errors: unknown card or port, card changed against its pin, port used twice, port types differ, in-in or out-out,
  a required port not connected, `dc_bus` voltage ranges that do not overlap, a controller DC link above the phase port
  rating of the motor, a shaft source above a mechanical limit of the sink (speed or torque);
* warnings: a source range not inside the sink range, a sink that may draw more current than the source gives (alone or
  together with the other sinks on one battery), a controller that can deliver more current than the motor port is rated for,
  a motor that can overspeed a load unloaded, a source that cannot give the torque a load draws at its top speed, different shaft
  diameters.

`mass_properties(asm, cards)` adds the masses, centres of mass and inertia tensors of the components (rotated by the placement,
parallel-axis theorem) into total mass, centre of mass and inertia about it. It is pure math and tested against hand calculations.

## 4. Writing a card

1. Build the reduced-order model (`MotorPerformanceIR`, `TransmissionIR`, `PropulsorIR`, `BatteryIR`, ...) and make it pass its own
   gate. Every number carries its origin in `basis`; a number nobody measured says so.
2. Describe the part: `envelope`, the `frames` its ports refer to, `mass_properties` (state `estimated = true` if the inertia
   comes from the envelope and not from CAD), and the `ports` with ratings.
3. Add identity: `component_id`, `licence` (SPDX; `LicenseRef-...` for published vendor data), `provenance`.
4. Run the gate: `assert_component_card(card)` from `motor_ai_sim.contracts.conformance`. It checks the schema, a JSON round trip,
   the gate of the carried IR, mass sanity and the privacy rule below. A card that fails is not listed in the library.
5. To list it in this repository, write an adapter in `modules/component_adapters.py` and call it from
   `modules/component_library.py`. Community modules live in `aerostator-modules` (see the Component SDK) and reach the portal
   after a maintainer has read them.

The adapters in this repository: motor (passport S1 model), propeller (catalogue entry), transmission (the gearbox plugin),
battery (a family `battery:` block through `simulation/battery.py`), controller (a passport drive variant and its power-device card),
and a placeholder frame.

## 5. Privacy rule for motors

Motor design data is private by default. A motor card carries **performance** (the `MotorPerformanceIR`), **mass properties** and
the **outer envelope** (a plain box or cylinder) and nothing else:

* the schema has no field for stator, rotor, winding or material design and refuses unknown fields; no field is called
  `geometry` (a key on the project denylist, `mcp_tools.GEOMETRY_DENYLIST`);
* the conformance gate walks the dumped card against that denylist, accepts motor CAD (`step`, `gltf`) only with `access = "nda"`
  (released through the NDA click-through, never served by the library), and allows a plain box or cylinder as the envelope;
* the motor adapter reads exactly two numbers from the geometry block of the passport model (stator OD and stack length), to size
  the envelope, and copies nothing else of it;
* the library route lists a motor only to callers who may see its die (`motor_access.may_see_die`).

## 6. Library service

* `GET /api/components?kind=` lists summaries (id, kind, name, version, licence, model, mass, flags, digest, ports).
* `GET /api/components/{id}` returns the full card.
* Both are read-only and open to engineer and admin accounts, like the System graph. The module `component-library`
  (capability `component.library`) is registered, so the System graph shows it.

Parts the stores hold no numbers for (the example battery pack, the controller board mass, the example frame, the reference
gearbox) are listed with the flag `placeholder`; replace them with measured values.
