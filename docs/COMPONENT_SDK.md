# Component SDK: drive-train modules (transmission, load)

[PLUGIN_SDK.md](PLUGIN_SDK.md) lets anyone add a motor *geometry*. This SDK lets
anyone add what a motor drives: a **gearbox or belt** and a **load** (a propeller,
a fan, a pump, a flywheel). A contributor models their own part; we check it and
connect it to our motors. The rules for contributors and their AI agents are in
[MODULE_CONTRIBUTION_RULES.md](MODULE_CONTRIBUTION_RULES.md); a copy-and-fill
template is in [module_template/](module_template/).

**Component cards.** Every part of a product, including the drive-train IRs below, is also described by one
[component card](COMPONENT_CARD.md) (identity, envelope, mass properties, typed ports, the reduced-order model);
the card embeds these IRs and adds `BatteryIR`, `ControllerIR` and `RigidBodyIR`.

Status of this document: contracts `0.1.0`, **motoring only** (power flows motor to
load). Nothing here changes Configure or any existing result; integration into
Configure comes later (section 9).

## 1. The rule that shapes everything: performance only

Motor design data is private by default. A component module therefore sees a motor
**only through its performance**: the torque-speed envelope, the efficiency map,
thermal limits, mass, inertia and the shaft interface (what mates, not what is
inside). It never receives stator or rotor geometry, windings, materials or any
AeroStator Core design data, and nothing in the contracts has a field for them.
`MotorPerformanceIR` is `extra="forbid"`, and its conformance gate also walks the
dumped IR against the project's geometry denylist (`mcp_tools.GEOMETRY_DENYLIST`).

## 2. The chain

```text
 MotorPerformanceIR            TransmissionIR              ShaftLoadIR / PropulsorIR
 (ours, read-only)             (a module)                  (a module)
 n_in, T_in  --- motor shaft ---> [ ratio i, losses ] --- load shaft ---> torque Q(n_out), thrust
```

* `ratio = speed_in / speed_out` (greater than 1 is a reduction); `n_out = n_in / ratio`.
* Input side = the motor side. The transmission's loss maps are over `(n_in, T_in)`.
* `T_in * w_in - loss(n_in, T_in) = T_out * w_out`, with `w_out = w_in / ratio`.
* A direct coupling is "no transmission" (`None` in the solve) or `ratio = 1` with zero loss.

Every IR is a pydantic model (like the other IRs in `contracts/`) and travels as
JSON. Units for the **whole family** (`units = "SI-rpm-v1"`, checked): N*m, rpm, W,
kg, kg*m^2, degC, m/s, kg/m^3. Shaft dimensions in mm, a propeller diameter in m.
Every field name carries its unit (`torque_in_Nm`, `speed_rpm`, `inertia_kg_m2`).

## 3. The contracts (`src/motor_ai_sim/contracts/drivetrain_ir.py`)

### 3.1 `MotorPerformanceIR` (our side)

Built by the portal from the motor passport (`modules/drivetrain/adapters.py:
motor_ir_from_s1_model`). Modules may read it (a sizing module picks its ratings
from it); they never produce it.

| Field | Unit | Meaning and validity |
|---|---|---|
| `id`, `name` | - | opaque handle, e.g. `ciano14_40_new:L12` |
| `speed_rpm`, `torque_peak_Nm` | rpm, N*m | envelope: the most shaft torque at each speed (short time). Held below the first speed, refused above the last |
| `peak_basis` | text | which limits shaped the envelope (current cap, temperatures). Today the voltage limit is **not** in it; S1 applies it point by point |
| `torque_continuous_Nm`, `continuous_basis` | N*m, text | optional; the cooling it assumes. Continuous torque depends on cooling, so it is honest only with its basis |
| `efficiency` | - | optional map `eta[speed][torque]` at the shaft, `null` where unreachable; `basis` names temperatures and drive (controller losses excluded) |
| `limits` | rpm, degC, A, V | `speed_max_rpm` (required), `winding_C`, `magnet_C`, `current_max_A`, `dc_voltage_min/max_V` |
| `mass_kg`, `rotor_inertia_kg_m2` | kg, kg*m^2 | optional; if absent the system result says "unknown", never 0 |
| `shaft` | mm | `ShaftInterface`: `kind` (keyed, spline, clamp, flange, bore, press_fit, other), `diameter_mm`, `length_mm`, `torque_rating_Nm` |
| `s1_available` | bool | the portal can run the propeller-loaded S1 rating for this motor |
| `source`, `basis`, `provenance` | - | `ModelSource(kind, reference, validated_against)`, per-quantity origin, producing module stamp |

### 3.2 `TransmissionIR` (gearbox, belt, chain)

| Field | Unit | Meaning and validity |
|---|---|---|
| `id`, `kind`, `name` | - | `id` equals the capability suffix; `kind` in gearbox, belt, chain, direct, other |
| `ratio`, `reverses_direction` | -, bool | `speed_in / speed_out`, plausible range 0.01 to 1000 |
| `speed_in_rpm`, `torque_in_Nm` | rpm, N*m | loss-map axes, strictly increasing, may start at 0. **They are the validity range**: outside them the solve refuses |
| `loss_W` | W | named groups, each `[speed][torque]`, heat dissipated. Efficiency is **derived** (`1 - loss / P_in`), never stored, so map and efficiency cannot disagree. Split the groups by mechanism (mesh, churning, bearings, seals) |
| `inertia_in_kg_m2`, `inertia_out_kg_m2` | kg*m^2 | reflected to the respective shaft |
| `mass_kg` | kg | |
| `backlash_deg` | deg | at the output shaft; 0 for a belt |
| `limits` | N*m, rpm, W, degC | `torque_in_max_Nm`, `speed_in_max_rpm` (required), `torque_out_max_Nm`, `speed_out_max_rpm`, `loss_continuous_max_W` (heat it can reject at `ambient_ref_C`), `housing_temp_max_C` |
| `input_shaft`, `output_shaft` | mm | `ShaftInterface`; the solve compares diameters with the neighbours |
| `backdrivable` | bool | v0.1 covers motoring only; regeneration through a transmission is a later contract version |
| `validity`, `source`, `basis`, `provenance` | - | ambient range and notes; where the numbers come from; origin of `ratio`, `loss_W`, `inertia`, `mass` (all four keys required) |

### 3.3 `ShaftLoadIR` and `PropulsorIR` (what absorbs the power)

`ShaftLoadIR` is the generic load: a torque map `torque_Nm[speed][condition]`
(condition = `airspeed_ms` or none) with an optional useful output (thrust,
flow). It never extrapolates.

`PropulsorIR` is a propeller by its coefficients over `(rpm, advance ratio J)`:

```text
T = C_T * rho * n^2 * D^4        P = C_P * rho * n^3 * D^5        Q = P / (2 pi n)
J = V / (n D)   (n in rev/s)     v_wake = sqrt(V^2 + 2 T / (rho A)),  A = pi D^2 / 4
```

| Field | Meaning and validity |
|---|---|
| `diameter_m`, `blades` | the diameter the coefficients are reduced with (disc area from it) |
| `speed_rpm`, `advance_ratio`, `ct`, `cp` | tables `[rpm][J]`. `rpm` carries the Reynolds trend (one row is allowed). `J = [0.0]` means **static data only** |
| `test_density_kg_m3`, `cp_estimated` | density of the bench data; `cp_estimated` true when C_P is an estimate, not measured torque |
| `inertia_kg_m2`, `mass_kg`, `shaft_interface` | as for the other IRs |

Validity behaviour: outside the **rpm** axis a coefficient is held at the edge
and the result carries `extrapolated = true` (this is exactly what
`motor_ai_sim.propeller` does today). Outside the **J** axis the IR refuses
(`OutOfValidity`); static data say nothing about forward flight.

**Mapping onto the existing propeller model.** `propulsor_from_propeller(prop)`
samples the catalogue fit `C_T(rpm)`, `C_P(rpm)` of a `config/propellers/` entry
over its tested rpm range at `J = 0` and carries `cp_estimated` over. The formulas
above are the ones in `propeller.py`, so thrust, torque and wake speed agree with
`thrust_N`, `torque_Nm` and `wake_velocity_ms` to the interpolation error (tested,
within 0.1 %). Shaft power is never taken from electrical power.

## 4. How modules are discovered

A component module is a class with three things:

```python
class MyBox:
    CAPABILITY = "transmission.my_box"        # or "load.my_prop"
    def manifest(self) -> ModuleManifest: ...   # outputs=["TransmissionIR"] or ["ShaftLoadIR"/"PropulsorIR"]
    def run(self, payload) -> TransmissionIR:   # a DESCRIPTION built from payload["params"], pure and fast
```

* **Capability names:** `transmission.<id>` and `load.<id>`, with `<id>` lower
  snake case (`[a-z][a-z0-9_]{1,47}`) and equal to `ir.id`.
* **Manifest:** the usual `ModuleManifest` plus two optional fields added for
  components: `status` (`reference`, `community`, `verified`, `integrated`; the
  portal's own modules say `core`) and `author`. A module may *request* a status;
  the maintainers *set* it (rules, section 3).
* **Registration:** `default_registry()` imports every file in
  `modules/component_plugins/`, instantiates each class whose `CAPABILITY` starts
  with `transmission.` or `load.`, runs its **conformance gate**, and registers it
  only if it passes. A module that fails is skipped and listed with the reason in
  `registry.rejected` and in `GET /api/modules` under `rejected_components`.
* **Lookup:** `registry.providers_with_prefix("transmission.")`,
  `registry.components(status="verified")`. The kernel runs a component like any
  module (`Kernel().run("transmission.my_box", {"params": {...}})`), fault-isolated.
* Components appear in `GET /api/modules` and the Admin module list automatically.

The reference module is `modules/component_plugins/gearbox_single_stage.py`
(single-stage gearbox: ratio, constant drag torque plus a speed-dependent drag
torque, a mesh loss proportional to input power, inertia, mass). Its defaults are
generic example values and its status is `reference`, outside the trust ladder.

### Where modules live (decided)

Community modules live in a **separate public repository, `aerostator-modules`**,
not in this repository. This repository keeps the contracts, the conformance
gates, the system solve and the one reference module. `aerostator-modules` holds
one folder per module (layout and template: [module_template/](module_template/)),
runs the conformance gate in CI against a pinned core release, and a module reaches
the portal only after a maintainer has read it (section 7 of the rules).

Components currently load **in the API process**, so only code a maintainer has
read and merged may sit in `component_plugins/`. Running unreviewed third-party
code on the hosted instance needs the isolated runner described in the rules
(sandbox with CPU, memory and time limits); until it exists, `community` modules
run on a developer's own machine, not on the hosted service.

### Closed modules over the API (decided)

A contributor may keep a private, proprietary model outside every repository and
connect it through the API: the portal sends performance data only (a
`MotorPerformanceIR`), the remote service answers with the same IR JSON, and the
answer passes the same conformance gate. Such a module is usable for the
contributor's own studies but is **never integrated or offered in Configure**; only
open Apache-2.0 modules are. The remote-component endpoint itself is not part of
this PR (section 9).

## 5. Conformance gates (`contracts/conformance/drivetrain.py`)

Real checks that raise `ConformanceError` (not `assert`, so `python -O` cannot
disable them). `assert_component_provider(provider)` runs the provider gate and
then the IR gate for its type.

**Provider gate:** capability prefix and id pattern; suffix equals `ir.id`;
`contracts_version` stamped; `status` in the allowed set; `outputs` within the
component types; non-empty `summary`; `run()` finishes within 5 s and is
deterministic for the same payload.

**All IRs:** schema tag; `units == "SI-rpm-v1"`; JSON round trip returns an equal
IR; `extra="forbid"` (an unknown field is an error); axes strictly increasing and
tables rectangular and finite; `basis` states the origin of each key number.

**TransmissionIR (physical):** ratio, mass, inertia, backlash in plausible ranges
(catches grams written as kg and a ratio written as its inverse); the loss maps
cover the declared speed and torque ranges; no negative loss; **zero heat at
standstill** (a drag torque gives loss = torque x speed); loss never falls with
speed or torque; the loss rises more slowly than the input power (so output power
rises with input torque and the solve has one answer) and never exceeds the input
power at the top torque; `torque_out_max <= torque_in_max * ratio` and
`speed_out_max <= speed_in_max / ratio` (efficiency cannot exceed 1).

**PropulsorIR / ShaftLoadIR:** `0 <= C_T <= 1`, `0 < C_P <= 1`, and
**`J * C_T < C_P`** at every node (propulsive efficiency below 1, which catches
swapped or mis-scaled tables); density in an air range; generic torque
non-negative.

**MotorPerformanceIR:** no geometry key anywhere; peak torque non-negative;
continuous within peak; efficiency in [0, 1]; mass and inertia plausible.

## 6. The system solve (`modules/drivetrain/system.py`)

Pure functions of the IRs; nothing is written.

**Operating point**, `solve_operating_point(motor, transmission, load, rho, airspeed_ms)`:
the highest motor speed at which the motor's peak torque still covers the load seen
through the transmission (full throttle):

```text
n_out = n_in / i;   Q = load(n_out);   T_in from  T_in w_in - loss(n_in, T_in) = Q w_out
find the highest n_in with  T_peak(n_in) >= T_in(n_in)       (grid + bisection, as equilibrium_rpm)
```

`limited_by` says why it stopped: `torque_equilibrium`, `motor_speed_limit`,
`no_motor_torque`, or **`transmission_validity` / `load_validity`** when a declared
range ends first (a loud answer, not a clamp). The result lists thrust, powers,
loss split by group, efficiencies, total mass and reflected inertia (or `null`
when a mass or inertia is unknown), and **checks**: motor and transmission speed,
torque and heat ratings, and shaft-interface diameters (`ok = null` means the
data to check are absent and is listed under `unverified`, never passed silently).
With no transmission and a catalogue propeller it agrees with
`propeller.equilibrium_rpm` (tested).

**S1 through a gearbox**, `solve_s1(model, transmission, load, ...)`: the
continuous rating of `propeller_s1.py` (loss grid, voltage re-check, magnet and
winding temperatures iterated to the fixed point, controller and demagnetisation
limits) with the transmission between motor and propeller. It reuses
`propeller_s1.solve_point` through its new optional `load` hook, which replaces the
propeller call with a function returning the load *as the motor shaft sees it*
(torque after the transmission loss, shaft power, thrust, slipstream cooling speed
from the propeller wake). With `load=None` the old code path runs unchanged
(tested), and `solve_s1(model, None, propulsor_from_propeller(prop))` reproduces
`s1_rating`'s limit (tested). Rows are over **motor** speed. A row passes only if the
motor holds all its limits **and** the transmission holds its torque, speed and
heat ratings; the S1 limit is the upper edge of the highest passing band, found by
the same bisection. The transmission's heat is checked against its own rating and is
not added to the motor's thermal network (it is a separate body); this is stated in
the result's `assumptions`.

Directions of travel for a new author: write the IR, run the gate, then
`solve_operating_point` with a motor IR from `motor_ir_from_s1_model` (the
synthetic model in `tests/test_propeller_s1.py` is enough to try it).

## 7. Writing a transmission, in five steps

1. Copy [module_template/](module_template/) (or `gearbox_single_stage.py`).
2. Fill the parameters; build `loss_W` from your data, one group per mechanism.
3. Write `basis` for ratio, loss, inertia and mass: where each number comes from.
4. Run `assert_transmission_provider(MyBox())`; fix what it names.
5. Put your validation data in `validation/`, state in `PROVENANCE.md` which points
   fitted the model and which were held out, and open a pull request.

A propeller is the same with `PropulsorIR` (`load.<id>`): fill `ct`, `cp` over rpm
(and `J` if you have forward-flight data), declare the tested rpm range by the axis,
say whether `C_P` is measured torque or an estimate.

## 8. Honesty rules the contracts enforce

* No extrapolation of a loss or torque map; a point outside the axes is refused and
  named. The one inherited exception (a propeller coefficient held at the rpm edge)
  is flagged on every result.
* No silent zero: an unknown mass, inertia or interface is `null` and shows up as
  `unverified` or `null` in the system result.
* Provenance on every number: `source`, `basis` per key quantity, `validated_against`
  (null means *not validated* and the review says so).
* Units are one family, named in every field and checked.

## 9. Not in this version

* Regeneration and back-driving; transients (the inertias are carried, but no
  spin-up or torque-step solve yet); gearbox thermal coupling into the motor.
* Forward-flight cooling: the wake speed formula includes airspeed, but the motor
  cooling calibration is static only.
* Configure integration (config x transmission x load buttons) and an MCP tool that
  returns a `MotorPerformanceIR` for a granted machine. Today an agent can read the
  headline ratings through `get_machine_performance`; the full envelope IR is a
  follow-up. A `system.drivetrain` capability wrapping the solve is also a follow-up.
* The remote-component endpoint for closed modules, the isolated runner, and signed
  releases.
* Multi-stage and multi-load chains (a gearbox followed by a belt is two transmissions
  composed by the caller today).
