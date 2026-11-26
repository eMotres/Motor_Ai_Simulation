# Design note: gear / planetary-gearbox module (v1) - 2026-11-25

Read-only design, for Astra's review before any code. Model: Claude Opus 5.5. Repository code was read at
`origin/pre-migration-freeze-2026-09-15` @ 0b750746.

Owner's request (2026-11-25): a module that models planetary gear trains, and gears in general, and computes all their
parameters: torques, efficiency, life and so on. Uses: the own Robomotor (motor + gearbox + controller for robots),
and a customer joint that needs 9.8 N·m at 247 rpm with a ratio of about 12.9.

## 1. Scope

**In v1:**
- spur and helical involute pairs, external and internal;
- a simple planetary stage (sun, N planets, ring, carrier), with any one member fixed;
- compound (stepped-planet) and two-stage planetaries, and stacked stages of any of these.

**Out of v1:** cycloidal, strain-wave, bevel and worm gears.

**How the structure leaves room for them.**
- A gear set is a `Stage` with a `kind` ("spur_pair" | "planetary" | "compound_planetary", later "cycloidal",
  "strain_wave", "bevel", "worm").
- Every stage implements one interface: kinematics (ratio, member speeds), statics (member torques, forces), losses
  (W by group), ratings (safety factors, life) and properties (inertia, stiffness, backlash, mass).
- A `Gearbox` is an ordered list of stages, joined shaft to shaft.
- A new kind adds one module and its rating method. The chain, storage, routes and UI do not change.

## 2. Inputs (SI; names carry units)

- **Per gear:** z, module m_n, face width b, profile shift x, tooth-thickness allowance (for backlash), tip / root
  modification.
- **Per mesh:** normal pressure angle α_n (default 20°), helix angle β, centre distance a (computed from the shifts,
  or given and the shifts solved).
- **Per stage:** N planets, carrier and planet-bearing cards (`config/bearings_library.yaml`, reused as is),
  load-sharing factor K_γ, floating member yes/no.
- **Material:** a new gear-material card file, `config/gear_materials.yaml`. It is structured like
  `materials_library.yaml`, with `sources:` / `prov:` per value. It holds σ_Hlim, σ_Flim, hardness, heat treatment
  (through-hardened / case-carburised / nitrided / induction), material quality class, E, ν, density, and the S-N
  slopes of ISO 6336-6. The ISO 6336-5 values are entered per card with a citation; the standard's tables are not
  copied wholesale.
- **Quality and lubrication:** ISO 1328-1 accuracy grade; flank and root roughness Ra/Rz; oil (ν40, ν100, density)
  or grease (base-oil viscosity and an "estimate" flag); operating temperature; ambient.
- **Duty:** a torque–speed spectrum, either from the motor configuration's duties (shaft torque and speed of each
  duty, with a time share) or from the project specification (output torque and speed points with hours). Plus peak
  torque and the number of peak events, for the static check.

## 3. Outputs

- **Geometry and assembly** (ISO 21771): ratio; tip and base diameters; transverse and overlap contact ratio
  ε_α, ε_β; undercut and pointed-tip checks; planet spacing (equal spacing needs (z_s + z_r)/N to be an integer,
  otherwise the non-equal angles are reported); neighbouring planets clear; interference of the internal mesh
  (tip / trochoid).
- **Kinematics and statics:** speed and torque of every member (Willis equation); tooth forces F_t, F_r, F_a per
  mesh; planet- and carrier-bearing loads; load per planet = K_γ × the mean.
- **Efficiency:** per mesh, per stage and overall at every duty point. Loss groups:
  - mesh friction: P_VZ = P × μ_mz × H_V (Niemann / Winter), with μ_mz from the ISO/TR 14179-2 relation;
  - bearings: the existing SKF model (`bearings.py`);
  - no-load churning / grease drag: ISO/TR 14179-2 for oil splash; an input drag torque (datasheet / measured,
    flagged) for grease.
  - The planetary efficiency follows from the stationary-carrier efficiency by the Kreines / Müller relation
    (VDI 2157).
  - Validity is stated per result (pitch-line speed, load, viscosity range of the friction relation). Grease
    churning is outside the model and marked "estimate".
- **Ratings:**
  - tooth-root bending S_F (ISO 6336-3) and flank pitting S_H (ISO 6336-2) at each spectrum bin;
  - static (peak) S_F and S_H with the static life factors;
  - life in hours at the spectrum: Palmgren–Miner, ISO 6336-6;
  - bearing L10 and L10h: ISO 281 basic rating life (a_ISO = 1 in v1, flagged).
- **Properties:**
  - backlash at the output, from the allowances and the centre-distance tolerance;
  - torsional stiffness at the output, from the mesh stiffness c_γ (ISO 6336-1); shafts and housing are excluded
    and this is said;
  - reflected inertia at the motor shaft, Σ J_k / i_k²;
  - mass and envelope from the gear and carrier volumes (housing an input).
- **Contract output:** a `TransmissionIR` (`contracts/drivetrain_ir.py:280`) with the loss map over (input speed,
  input torque), so the existing drivetrain / component path takes the gearbox with no new contract. The reference
  plugin `modules/component_plugins/gearbox_single_stage.py` stays as the example.

## 4. Methods and reference tests

**Methods, and what is simplified:**
- **ISO 6336-1** load factors:
  - K_A is an input;
  - K_v by method B;
  - K_Hβ / K_Fβ by method C, from the face-load distribution of a stiff carrier, with the input f_ma;
  - K_Hα / K_Fα by method B;
  - K_γ for planets is an input, with a default per planet count from AGMA 6123-C16 to be fixed in stage 1;
  - c', c_γ by method B.
- **ISO 6336-2 (pitting):**
  - Z_H, Z_E, Z_ε, Z_β, Z_B/D by method B;
  - Z_L, Z_v, Z_R (lubricant, speed, roughness) by method B;
  - Z_W = 1 and Z_X = 1 in v1 (flagged);
  - Z_NT from the life curve of the material class.
- **ISO 6336-3 (bending):**
  - Y_F, Y_S by method B (30° tangent, hob-cut external teeth);
  - internal teeth by the rack approximation of the standard (flagged);
  - Y_β, Y_δrelT, Y_RrelT, Y_X, Y_NT.
- **ISO 6336-6:** Miner sum over the spectrum bins; endurance-limit treatment as the standard's method; damage per
  flank and root; life = the time the sum reaches 1.
- **ISO 281:** basic L10 = (C/P)^p with p = 3 (ball) or 10/3 (roller); the equivalent load from X, Y; the life
  modification factor deferred (flagged).
- **Efficiency:** mesh loss per ISO/TR 14179-2 (μ_mz, H_V); the planetary stage per VDI 2157 / Müller's
  stationary-carrier method; bearings by the SKF model already validated in the repo (61811 case, 3 %).

**Reference tests, one test file per standard part.** The expected values are transcribed from the source in stage 1;
only the numbers each test needs are kept, with a citation.
1. **ISO/TR 6336-30** (calculation examples for parts 1, 2, 3, 5): its spur and helical examples. Every factor and
   S_H / S_F, within the stated rounding.
2. **ISO 21771 contact ratio**, closed form, computed for this note: m = 1, z = 20 / 40, α = 20°, x = 0, a = 30 gives
   ε_α = 1.635 (tolerance 1e-3). The undercut limit z_min = 2 / sin²20° = 17.10.
3. **Planetary kinematics (Willis), hand cases:**
   - z_s / z_p / z_r = 20 / 16 / 52, ring fixed: i = 1 + 52/20 = 3.600; N = 3 can be equally spaced
     (72 / 3 = 24).
   - 19 / 15 / 49: i = 3.579; N = 3 cannot (68 / 3 is not an integer), N = 4 can.
   - The two stacked: i = 12.884, which suits the joint (247 rpm at the output gives 3182 rpm at the motor).
   - Every other fixed member, and the sign of the ratio.
4. **Planetary efficiency (Müller / VDI 2157), hand case:** stationary-carrier efficiency η0 = 0.97 with
   z_r / z_s = 2.6 gives η = (1 + η0 × 2.6) / 3.6 = 0.9783 (sun in, carrier out, ring fixed). Plus the published
   worked example of Müller, "Epicyclic Drive Trains" (1982), ch. 6.
5. **ISO 281:** the SKF catalogue L10 for the cards in `bearings_library.yaml` at a given load (C/P)³.
6. **Miner (ISO 6336-6):** a two-bin spectrum hand case; damage = Σ n_i / N_i from the part-6 slope.
7. **Regression against a textbook example** (Niemann / Winter, Maschinenelemente II, the planetary worked
   example): ratio, forces, S_H, S_F within 2 %. Used only as a cross-check if its edition is available.

## 5. Where it lives

- **Package:** a pure-Python package `src/motor_ai_sim/gears/`, no FEM:
  - `geometry.py` (ISO 21771), `kinematics.py` (Willis, stacked stages), `loads.py`;
  - `rating_iso6336.py` (parts 1-3, 6), `losses.py` (14179-2, VDI 2157, the bearings bridge), `bearings_life.py`
    (ISO 281);
  - `gearbox.py` (stage list, chain, `to_transmission_ir()`), `materials.py` (the card reader).
  - Numpy only.
- **Storage:**
  - Gear-material cards: `config/gear_materials.yaml` (public, cited).
  - A gearbox design is a JSON record in `shared_root()/gearboxes/<id>.json`. That is closed design data, the same
    rule as die data, never in the public repository; admin and engineer routes, like the other server stores.
  - A motor configuration names its gearbox as the key `gearbox: {id, stages_override?}` in
    `config/dies/<die>/<cfg>.yaml`, written exactly like `bearings` (`PATCH /api/family/config/{die}/{cfg}/gearbox`).
  - A project's specification already has "gearbox" as a shaft output (`development/schema.json:2101`). The project
    then links the motor configuration and its gearbox; customer access follows the project rules.
- **Routes:**
  - `POST /api/gears/evaluate` (stateless: a design plus a duty gives the full result);
  - `GET` / `PUT /api/gears/gearbox/{id}`;
  - `GET /api/gears/materials`;
  - `GET /api/family/config/{die}/{cfg}/drivetrain`: the motor duties carried through the gearbox.
- **Motor + gearbox operating points.**
  - **Motor duty to output:** each motor duty point (shaft torque, speed) gives T_out = T_in × i × η_gb(T_in, n_in)
    and n_out = n_in / i. The chain efficiency is η_motor,shaft × η_gb, and the losses are listed by group (motor
    groups from the summary, then the gearbox groups).
  - **Specification to motor:** a project output point is mapped back to the motor shaft, solving
    T_in = T_out / (i × η_gb(T_in)) by fixed point (2-4 iterations). The joint gives 9.8 N·m / 247 rpm →
    about 0.80 N·m / 3182 rpm at the motor (with η_gb ≈ 0.95).
  - **Shaft power:** the gearbox input uses the motor's SHAFT torque. When a summary carries the marker
    `fe_on_shaft_k3d_v1`, the input is `T_shaft_em_Nm` / `P_mech_W` through `shaft_convention.py`; without it, the
    legacy T × ω, flagged. This follows ~/reports/PLAN_shaft_power_convention_2026-11-25.md, which is in flight.
    The gear module reads shaft power only through that helper and never re-derives it.
- **UI v1:** one "Gearbox" page under the configuration:
  - inputs (stages, gears, materials, lubrication, duty source);
  - a results table (geometry checks, member torques and speeds, efficiency per point, S_F, S_H, life, L10h,
    backlash, stiffness, J reflected, mass);
  - the chained motor + gearbox table;
  - a red flag on every value below the rule. No caption lines; tooltips; EN and zh-CN.
- **Proposed safety-factor rule, to be confirmed:**

| check | proposed minimum |
|---|---|
| S_F, bending (at the duty) | 1.5 |
| S_H, pitting (at the duty) | 1.1 |
| static (peak) S_F | 1.4 |
| static (peak) S_H | 1.0 |
| gear life at the spectrum | ≥ the specified life (project field; else 10 000 h) |
| bearing L10h | ≥ the same |

## 6. Stages, models, effort

| PR | content | model | estimate |
|---|---|---|---|
| 1 | geometry + kinematics + statics + assembly checks; tests 2, 3 | Opus 5.5 | 1.5 d |
| 2 | ISO 6336-1/2/3 rating + gear-material cards; test 1 (TR 6336-30) | Opus 5.5 | 2.5 d |
| 3 | losses (14179-2, VDI 2157, bearings bridge), ISO 281, ISO 6336-6 life, stiffness, backlash, inertia, mass; tests 4-7; `to_transmission_ir` | Opus 5.5 | 2 d |
| 4 | storage, routes, config key, drivetrain chain with the shaft convention | Sonnet 5.5 | 1.5 d |
| 5 | UI page (EN / zh-CN) + node tests | Sonnet 5.5 | 1.5 d |

- Total is about 9 agent-days.
- Each PR is separate and goes to independent review. The core PRs (1-3) go to Astra.
- CPU: unit tests only, no campaigns.

## 7. Questions for the owner

1. The safety-factor and life rule above (S_F 1.5 / S_H 1.1 at the duty, static 1.4 / 1.0, 10 000 h default):
   accept, or give the values.
2. v1 rates our own gear designs only. Should it also take catalogue gearboxes from datasheet data (ratio, efficiency
   map, limits only; no ISO 6336 rating)?
3. Which duty is authoritative for gearbox life: the project specification's spectrum, or the motor configuration's
   duties?
