# Component card

One card describes every part of a product: ours or contributed, a motor or a frame. It is stage 1 of the
[product composer](PRODUCT_COMPOSER_ROADMAP.md): the base that the assembly (stage 2) and the mission simulation
(stage 3) plug into. The card reuses the drive-train IRs of the [Component SDK](COMPONENT_SDK.md); it does not copy them.

Code: `contracts/component_ir.py` (card), `contracts/assembly_ir.py` (assembly document, validator, mass properties),
`contracts/conformance/component.py` (gate), `modules/component_adapters.py` (store to card),
`modules/component_library.py` (the library), `routes/components.py` (API), `part_materials.py` + `config/part_materials.yaml` (the material cards), `modules/motor_mass.py` (the motor's mass as the app computes it). Tests: `tests/test_component_card.py`,
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
| parts | `parts`: list of `ComponentPart` (section 7): name, `material_id`, `mass_kg`, `role`, `status`, `counted`; the parts add up to the card mass |
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

## 7. Parts and materials (schema 0.2.0)

Every card lists its **parts**: what the component is made of and how heavy each piece is. This is the input of the later
load studies (stress, vibration, thermal): each part's `material_id` resolves to a **material card** with density, Young's
modulus, Poisson ratio, yield and ultimate strength, fatigue limit, thermal expansion, conductivity, specific heat and the
maximum service temperature.

`ComponentPart`: `name`, `material_id`, `mass_kg`, optional `volume_cm3` (never on a motor part), `role` (`structural`,
`magnetic`, `conductor`, `insulation`, `fastener`, `electronic`, `cell`, `other`), `status` (`computed` by the app's mass model,
`catalogue`, `measured`, `assumed` = a documented default, `placeholder` = an example number), `counted` (`false` = a real part
that is not inside the card mass, listed so a load study knows it exists) and `basis`. The counted parts must add up to
`mass_properties.mass_kg` within 2 %; the conformance gate refuses a `material_id` that does not resolve.

**Material cards** (`part_materials.resolve(material_id)`, `GET /api/components/materials/{id}`): one card per material, two
files behind it.

* `config/materials_library.yaml` (the solver's library) supplies every steel, magnet, conductor and insulator card the motors
  use (stator and rotor steel `20SW1200`, magnets `F52SH_120C`, `copper`, shaft steel `Steel_42CrMo4_QT`, `Aluminium_6061`,
  `Aluminium_7075`, carbon sleeve `T800_UD_60`, `Nomex`, ...). The values and their sources are the library's own (a field without
  a `prov` entry is `datasheet` from `sources[0]`). The library is not changed.
* `config/part_materials.yaml` adds the cards the library does not have - `CFRP_QI_T800_epoxy` (frame plates, carbon propellers),
  `PA66_GF30` (glass-fibre nylon propellers), `PA66_CF30`, `Stainless_A2_70` (screws), `Bearing_steel_100Cr6`, `FR4_glass_epoxy`
  (boards, G10-type rings), `Li_ion_pouch_cell_effective` - and `library_extensions`: values a library card lacks (ultimate
  strength, fatigue limit, service temperature of `Aluminium_6061`, `Aluminium_7075`, `Steel_42CrMo4_QT`, `copper`). Extensions fill gaps
  only; the library wins.

Every property carries its **source** (a standard, a supplier data sheet or a handbook; nothing is taken from a commercial FEM
package's material library), a **type** (`standard`, `datasheet`, `handbook`, `derived`, `estimate`) and a **`verify`** flag
(`true` = entered from the cited reference without re-reading the document on the day the card was written; check it before it
feeds a safety-relevant result). A property nobody has a figure for is **missing** (`null`), not guessed: a Li-ion cell has no
modulus here. The quasi-isotropic carbon laminate is derived from the library lamina `T800_UD_60` by classical lamination theory
(a test recomputes it).

### Where the parts come from

| Component | Parts and masses | Status |
|---|---|---|
| Motor | the **app's own mass model** (`masses.compute_masses` on the passport's die and configuration, `modules/motor_mass.py`): stator core, rotor core, magnets, winding copper, shaft, retaining sleeve, each with the material the configuration assigns (a part it does not name takes the shipped default of `config/motor_config.yaml`, and the part says so). The card mass is the total the app shows in Configure and the datasheet. | `computed` |
| Motor, beyond the app mass | holders, rings, bearings, screws, phase board, pins, propeller fixing plate - only where the server has built a CAD bill of materials (`<cad>/<die>/<config>/*_BOM.csv`); only part name, material and mass are read. Listed with `counted = false`. | `computed` / `assumed` |
| Propeller | catalogue `mass_g`, or single-blade mass x blades; the material from the catalogue's `material` text (`glass fiber nylon` -> `PA66_GF30`, `CF+Epoxy` -> `CFRP_QI_T800_epoxy`, `polymer + carbon fiber` -> `PA66_CF30`), the grade or layup **assumed**; no statement -> a default by vendor/series, flagged in the basis. No catalogue mass -> no mass and no parts. | `assumed` |
| Gearbox, battery, controller, frame | documented splits of the placeholder total (housing / gears / bearings; cells / wiring / case; board / copper / heat spreader; plates / standoffs / screws) | `placeholder` |

**Active and Full assignment (owner 2026-10-14).** The material assignment the electromagnetic simulation reads covers only the
*active* parts (stator core, rotor core, magnets, winding copper, insulation, shaft, retaining sleeve) and is unchanged. The
Materials tab has a switch **Active | Full**: Active is the tab as it was; Full lists the whole motor - the active parts
(read-only there) plus every other part of the assembled motor, each with a material, a mass and the tag "not EM". The part
list of the non-active parts is the motor's CAD bill of materials (`<cad>/<die>/<config>/*_BOM.csv`, written by the CAD build; keys and
default materials in `config/part_materials.yaml` `mechanical_parts`); a motor without a built BOM lists only what its
configuration names (its bearings card), never an invented part. A choice is saved **per die** (`die.yaml` `part_materials`, the
block the electromagnetic solver never reads) through `PUT /api/family/die/{die}/part_materials` (same permission rule as every
catalog write; `GET` lists the rows and the choices). The CAD volume is fixed, so a part given another material changes its mass
by the density ratio. The motor card's `parts` and mass breakdown use this Full assignment (the card mass is still the app's
active mass; the non-active parts stay `counted = false`). Test: `tests/test_family_part_materials.py` (route, persistence,
and the isolation test that a non-active choice changes no electromagnetic input: geometry, its signature, the CAD parameter
fingerprint, the active assignment, the active masses, the configuration file).

A die that is not in the catalog (a private die on a public checkout) gives a motor card **without mass and parts** and the
library flag `mass_unknown`; the mass is never a duty-record number and never a placeholder.

Motor parts carry **material and mass only**: no volume, no dimension, no topology. The conformance gate refuses a motor part
with a volume, and the dumped card is still walked against the geometry denylist.

**CAD-only assembly clearances** (`offset_rotor`, the pocket fillet `magnet_fill_radius + offset_rotor`, `offset_bearing`, ...) exist
only in the 3-D model and are not simulated, by design (docs/CAD_GENERATOR.md): the magnet mass is the nominal magnet, the
clearance is air.

`contracts.assembly_ir.parts_summary(asm, cards)` lists every part of every instance and the mass per material of an assembly.

Parts the stores hold no numbers for (the example battery pack, the controller board mass, the example frame, the reference
gearbox) are listed with the flag `placeholder`; replace them with measured values.
