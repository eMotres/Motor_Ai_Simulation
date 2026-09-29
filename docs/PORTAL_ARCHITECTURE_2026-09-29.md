# Engineering portal: core + contracted modules (v3, 2026-09-29)

Base: `motor_ai_sim`, branch `origin/pre-migration-freeze-2026-09-15` (production, 3d914fc), plus the open PRs #40 (licence: AGPL-3.0-or-later + DCO), #41 (private split), #44 (bring-your-own compute, `docs/BYO_COMPUTE.md`) and the MCP stages (`docs/MCP_2026-09-28.md`). The separate ERP project `motres_erp` was read (not changed) to draw the integration boundary. This is a document; no code was changed.

## What changed from v2

| Area | v3 |
|---|---|
| Language | English only (owner rule: no Russian in the repository or in documents for others). |
| Commercial content | **Removed.** The project is non-commercial for now: pure AGPL-3.0-or-later + DCO, no pricing, no tiers (only `user` and `admin` roles), no billing, no revenue share, no listing fees, no paid features. Fair-use limits instead of plans; users may attach their own compute nodes (BYO compute). |
| Restructure | Owner decision: the whole project moves onto this architecture, stage by stage (M0…), never breaking results: bit-identical acceptance on L155 motor, L180 generator and L13. |
| New scope | Parties and organizations (section 6), manufacturing documentation (section 7), sourcing and orders (section 8): everything can be done in the portal "up to drawings and orders of finished products". Money and contracts stay outside the platform; it carries documents, statuses and communication. |
| Decisions | D1–D23 kept (D9 restated as non-commercial, D12 was never used); new D24–D37; D46–D56 on data protection and residency (section 10A). |
| Roadmap | New stages M8–M12 placed after the core restructure (M0–M3) and the data model (M4–M7). |
| Physical port contract (update 2026-09-29) | New section 2.7 per the Codex review (point 6, owner agreed): acausal across/through ports, **positive = into the module** (D2 restated), SI with K and m at the boundary (D3 restated), per-module energy balance with explicit storage states, PWM fidelity levels, coolant stream semantics, consistency checks with tolerances, worked battery–controller–motor–load example, two separate validation tracks (D11 restated, D57–D61). |
| Priority order (update 2026-09-29) | Roadmap reordered per the review: publication fix → data loading and versions → motor + controller in contracts → verify old results → simple battery–controller–motor–load system; orders, NDA and missions later (D37 restated, D62). |
| Open standards (update 2026-09-29) | New section 10B per the owner principle "be compatible with open standards": every interface mapped to an open standard with import/export, roadmap stage, conformance test and what is not adopted (D63–D69). |
| Open RFQ board (update 2026-09-29) | New section 8.9 per the owner idea: category RFQ templates from released revisions, invite-only or verified-supplier board, NDA-gated watermarked drawings, structured quotes, sealed bids, comparison matrix, award or split, supplier capability profiles with hard/soft matching (UNSPSC/ECLASS), freelance-style supplier profiles, job feed, status milestones and two-way blind reviews, direct work after award; no fees, no payments (D70–D82). |

## Goal

The owner's goal: a portal to which modules from other makers are later attached (gears and gearboxes, CFD, propellers, batteries, drones). The foundation is laid now on motors and controllers so that the system does not have to be broken later.

**End goal** (owner, 2026-09-29): assemble a *whole system* (a drone, a boat or AUV, a wheeled robot or vehicle, a robot joint, a generator), simulate it **in motion / over its duty cycle** with every block taken into account (energy, drives, heat, limits), and then take it **all the way to manufacturing documents and orders**: drawings, BOM, RFQs to suppliers, purchase and production orders, order tracking. Flight is one example, not a special case hard-wired into code.

Main idea: a **core** (who, where, on what compute, what is in the catalog, who may see what) plus **modules** that talk to the core and to each other only through a **contract**: typed ports, catalog cards and declared calculations. A system is a graph of modules connected by ports. The core finds the operating point along the chain. Today's hard-wired motor ↔ controller ↔ thermal coupling becomes the first special case of that graph. On top of the engineering graph sit **parties** (organizations and their roles), **released design revisions** that generate **manufacturing documents**, and a **sourcing layer** (RFQ → quote → order → production → delivery) that links to MOTRES's own ERP instead of duplicating it.

---

## 1. What exists and what becomes the core

### 1.1 Map "file → core service"

| Core service | Exists today | Readiness | Missing |
|---|---|---|---|
| **Identity** | `users.py` (users.json, argon2id, 30-day tokens), `auth.py`, `oauth.py` (Google) | good | **No organizations.** The legacy plan names `free/pro/team` are retired: only roles `user` and `admin` remain (non-commercial). Organizations with member roles are needed (section 6). |
| **Permissions** | `motor_access.py`: die visibility `private/public/selected` + personal grants (`die_access.json`); `auth._GATED_PREFIX` | works for dies | Permissions are tied to a *die*. Needed: permissions on an object of *any kind* (card, module, system, project, design revision, document, RFQ). |
| **Workspaces** | `workspace.py`: layers `workspace / published / shared`, tombstones of deleted shared dies, `MAX_WORKSPACES=32` | good | Workspace = user. Needed: organization workspace and projects inside it. |
| **Job queue** | `jobs.py`: `Priority`, `register_handler(kind, fn)`, per-user limit, `SB_FIELD_MAX_CONCURRENT`, cancel, owner | good, already generic | Jobs run inside the API process. Third-party modules need an **isolated executor** (container / node). |
| **Compute nodes** | `cluster_monitor.py` (admin metrics tokens `mnode_`); **PR #44 BYO compute**: user-owned nodes, tokens `mcnode_`, pull model (heartbeat → lease → signed bundle → progress → complete), owner-only leasing, org sharing planned for its stage 3 | monitoring + BYO stage 1 | Platform nodes still only *observed*; the lease protocol of #44 becomes the one dispatch path for all nodes. |
| **Usage accounting** | `job_usage.py` (CPU·h and RSS per job, node, user, client; SQLite), `usage_stats.py` (events, storage, monthly report) | good | Row lacks `module / module_version / vendor_org / own_node` → needed for fair-use limits, capacity planning and per-module audit, **not** for billing. The monthly euro basis becomes an internal cost report only. |
| **Catalog** | `catalog/envelope.py` (stage 1: `id, kind, status draft/active/validated/deprecated, sources, per-field prov (datasheet/measured/estimate/derived), validation, revision`), adapters `bearings.py`, `devices.py`, `used_by.py`; `docs/CATALOG_STAGE1_2026-09-28.md` | **right foundation** | `KINDS = bearing, lubricant, device`. Materials, wire, battery, motors not in the envelope yet. No `owner_org`, `license`, `supplier_offers`. |
| **Units** | file-level `catalog_units:`; in code units live in field names (`_kn`, `_mm`, `_c`) | partial | One unit registry for ports (SI inside, engineering units in the UI). |
| **Versions and provenance** | config history (`/api/family/history`), card `revision`, geometry fingerprint + solver commit in run records, `contracts.Provenance` | good for our runs | Module version in every result; immutable published versions; **released design revisions** that documents and orders pin. |
| **Module contract** | **Skeleton exists:** `modules/base.py` (`ModuleManifest`: `capability`, `depends_on`, IR-typed `inputs/outputs`, `contracts_version`, `UIContribution`), `modules/registry.py`, `modules/kernel.py`, `contracts/` (`GeometryIR, MeshIR, MaterialIR, ResultIR, Excitation/ControlSignal/MachineState`, `CONTRACTS_VERSION="0.1.0"`, conformance tests), routes `/api/modules`, `/api/kernel` | **skeleton, barely used in production** (only `_call_filtered` is used from `coupled.py`) | This is the contract *inside* the motor (geometry → mesh → solver). Missing: the **system** level (physical ports: shaft, bus, heat) and declared calculations. |
| **Coupled calculation** | `routes/coupled.py` (7.7 k lines): EM ↔ thermal, `DEFAULT_TOL_K=2`, bearing seat `BEARING_TOL_K=5`, `DEFAULT_MAX_ITER=6`, drive `current / pwm / inverter`; `inverter/coupling.py` (`fit_device_drop`, `InverterVoltageSource`) | mature physics | Coupling is hard-wired: motor, controller and thermal know each other directly. |
| **Controller** | `inverter/` (devices, coil→bridge topology, losses, T_j, waveforms, spice), `routes/controller.py`, `docs/CONTROLLER_MODULE_2026-09-22.md` | good | Lives inside the motor configuration (`PATCH …/controller`), not as a separate system node. |
| **Data model** | `routes/family.py`: die → configuration (cfg) → duty; battery, controller and bearings stored *inside* cfg | works | No **project** or **system**; battery embedded in cfg instead of referencing a card. |
| **MCP** | `mcp_app.py` (streamable HTTP, `McpGate`: key `emk_`, scopes, quotas, audit), `mcp_tools.py` (field whitelist, `GEOMETRY_DENYLIST`), `agent_designs.py` (drafts, sandbox, `simulate`) | good, **already the IP-protection pattern** | Tools are motor-bound; no `build_system` / `simulate_system`, no catalog-sourcing or RFQ-draft tools. |
| **Notifications** | newsletter + in-app notices (PR #36), admin tab | works | Needed: per-event notifications for approvals, RFQs, quotes, order status (section 8.7). |
| **Fusion 360 sync** | `/api/fusion` six-column parameter CSV, in-Fusion script `scripts/fusion360_sync_params/` | works | Becomes one of the CAD sources for manufacturing documents (section 7.4). |
| **Licence and repo split** | PR #40: AGPL-3.0-or-later + DCO; PR #41: ANSYS cross-checks, customer case data and NDA material moved to a private repo | open PRs | Portal code is public AGPL; customer data, NDA material and supplier price data never enter the public repo. |

### 1.2 Conclusion

60–70 % of the core already exists. It must be **renamed in our heads**, not rewritten. The real gaps:

1. **Organizations and projects** (today: user → die), including cross-org grants.
2. **Physical ports and the system graph** (today: coupling hard-wired in `coupled.py`).
3. **Isolated execution of foreign code + per-module accounting** (today: everything in the API process), dispatched through the BYO-compute lease protocol.
4. **Released design revisions → manufacturing documents → sourcing and orders** (today: none in the portal; MOTRES's internal side exists in `motres_erp`).

The `modules/` + `contracts/` skeleton is kept: it becomes the "inner" contract level (how a module is built inside). The system level (ports between products) is added above it.

---

## 2. Module contract

Module = **manifest** (what I can do) + **cards** (concrete products) + **calculations** (what to compute for a card and the port values).

### 2.1 Ports

A port is a typed connection point. Everything is SI inside; engineering units in the UI.

| Port type | Variables (SI) | Sign | Effort / flow pair |
|---|---|---|---|
| `mech.shaft` | τ [N·m], ω [rad/s] (rpm in the UI), P = τ·ω; inertia J is module state (2.7.5) | τ acts on the module; P > 0: power **enters** the module | ω / τ |
| `elec.dc` | V [V], I [A], R_src [Ω] (property); C_link is state of the owning module | I > 0: current **enters** the module | V / I |
| `elec.ac3` | phase v_abc [V], i_abc [A] (`series`) or fundamental phasors + harmonic power (`scalar`); V_ll_rms, I_rms, cos φ, THD informational only (2.7.4) | as DC; star/delta is a property | v / i |
| `thermal.heat` | Q [W], T [K] (°C in the UI) | Q > 0: heat **enters** the module | T / Q |
| `thermal.coolant` | ṁ [kg/s], p [Pa], h [J/kg] (T_in / T_out [K]), Δp [Pa], `fluid` (card reference) | ṁ > 0 into the module; stream enthalpy (2.7.6) | p, h / ṁ |
| `mech.linear` | F [N], v [m/s], m_eff [kg] (property): propeller thrust, wheel rim force, track, waterjet, linear actuator | F·v > 0: power enters | F / v |
| `body.motion` (vehicle body) | 6 DOF: force F⃗ [N] and moment M⃗ [N·m] at the mount point + body state (position, velocity, attitude, angular rate) | body frame; mount point is a link property | F⃗,M⃗ / v⃗,ω⃗ |
| `env` (environment, non-energetic) | medium `air/water/ground`; ρ, T, p, viscosity; wind/current/waves (vector, gusts); slope, rolling coefficient, grip, surface | — | set by the Environment module, read by all |
| `energy.store` (property + state of the source on `elec.dc`) | SoC [–], stored energy [J], H₂/fuel [kg], T [K], SoH | — | battery, fuel cell, supercap, engine-generator deliver it on `elec.dc` |
| `envelope` | outline (OD, L, mm), mass [kg], J, centre of mass, attachment (flange/shaft: interface card) | — | non-energetic: compatibility check, not solve |
| `signal.cmd` | setpoint (torque/speed/current), limits | — | — |

**Single sign rule (restated 2026-09-29, D2):** positive through variable and positive power on a port always mean "**into** the module" (Modelica convention). At every node the through variables sum to 0 and the across variables are equal; each module satisfies its own energy balance with storage (2.7.2). Efficiency = |output| / |input| of the current mode, without "if generator" special cases. The full physical contract is in 2.7.

**Three value forms per port** (a port declares which forms it accepts and delivers):

| Form | Example | When |
|---|---|---|
| `scalar` | T = 120 N·m at 3000 rpm | operating point |
| `map` | η(T, n), P_loss(T, n, V_dc), thrust C_T(J) | fast system solve, vendor black box |
| `series` | i_abc(t), T(t), mission profile | transients, cycles, co-simulation |

**State** is separate from form: a module with memory (thermal network temperatures, battery SoC, body position/velocity, magnet Br ratchet) declares its state variables with units and initial values. The core stores state between steps and passes it back. A stateless module is a pure function of its ports. This is the precondition for missions (section 3A).

### 2.2 Card kinds

Extend `catalog/envelope.py` without changing the format (`KINDS` + fields `owner_org`, `license`, `module`, and in section 8 `supplier_offers`):

| kind | Owner | Body | Default ports |
|---|---|---|---|
| `motor` (= published cfg) | MOTRES / customer | geometry (hidden), winding, materials, passport | `mech.shaft`, `elec.ac3`, `thermal.*`, `envelope` |
| `controller` | MOTRES | topology, `device` references, cooling | `elec.dc`, `elec.ac3`, `thermal.*`, `envelope` |
| `device`, `bearing`, `lubricant` | as today | as today | — (parts inside a module) |
| `battery_cell` / `battery_pack` | vendor / MOTRES | chemistry, NS/NP, OCV(SoC, T), R(SoC, T) | `elec.dc`, `thermal.heat`, `envelope` |
| `propeller` | vendor | D, pitch, C_T(J), C_P(J), tables by Re | `mech.shaft`, `mech.linear`, `env`, `envelope` |
| `gearbox` | vendor | i, η(T, n, T_oil), J, backlash | 2× `mech.shaft`, `thermal.heat`, `envelope` |
| `wheel` / `track` / `waterjet` / `joint` | vendor | radius, rolling resistance, grip(slip); waterjet efficiency; joint stiffness/backlash | `mech.shaft`, `mech.linear` (or 2× `mech.shaft`), `env`, `envelope` |
| `fuel_cell`, `supercap`, `genset` | vendor | V(I), η, H₂/fuel store; C, ESR | `elec.dc`, `thermal.heat`, `energy.store` |
| `vehicle` (object dynamics) | MOTRES / vendor / user | mass, J, drive mount geometry; aero/hydro/road coefficients (C_x·S, added masses, f_r) | N× `body.motion`, `env`, energy summary |
| `environment` | MOTRES | ISA atmosphere, water (salinity, T), ground/surfaces, wind/wave models | `env` |
| `controller_logic` (flight controller, thrust allocation, ABS, robot trajectory) | MOTRES / vendor | algorithm "demand → drive commands" | `signal.cmd` ×N |
| `material`, `wire`, `coolant` | as in the catalog proposal | — | — |
| `map` (result card) | producing org | L0 map + provenance of the runs that built it | as the source module |

### 2.3 Calculations (declared in the manifest)

| Calculation | Input | Output | Who must support it |
|---|---|---|---|
| `operating_point` | values on some ports (e.g. ω and T on the shaft, V on the bus) | remaining port values + losses + temperatures | **every module** (minimum) |
| `efficiency_map` | grid (T, n) or (J), V | `map` on ports | motor, controller, gearbox, propeller |
| `transient` | `series` in | `series` out | optional (FMU, our FEM) |
| `thermal_steady` / `duty_cycle` | heat on ports, cooling, S1/S2/S3 cycle | temperatures, time to limit | motor, controller, battery |
| `envelope_check` | neighbouring `envelope` | compatibility (fit, flange, mass) | all |
| `manufacturing_docs` (new, optional) | released design revision | drawing set, BOM, STEP (section 7) | motor, controller; later any module that is manufactured |

### 2.4 Manifest (capability declaration)

```yaml
module: aerostator.motor              # unique name (org.module)
version: 2.3.0                        # semver; major = contract break
contract: portal/1.0                  # core contract version
vendor_org: motres
license: AGPL-3.0-or-later            # code licence; card data licence is per card
card_kinds: [motor]
ports:
  shaft:   {type: mech.shaft,  forms: [scalar, map, series]}
  phases:  {type: elec.ac3,    forms: [scalar, series]}
  heat:    {type: thermal.heat, forms: [scalar]}
  coolant: {type: thermal.coolant, forms: [scalar], optional: true}
  body:    {type: envelope}
state: [{name: T_winding, unit: K, init: env, energy: C_th*T}, {name: T_magnet, unit: K, init: env, energy: C_th*T},
        {name: omega_rotor, unit: rad/s, init: 0, energy: 0.5*J*omega^2}]
limits: [{name: T_winding_max, unit: K, value: 453.15, source: insulation_class_H}]
calculations:
  operating_point:    {cost: "FEM 20-200 s", fidelity: fem_2d}
  efficiency_map:     {cost: "minutes", fidelity: fem_2d}
  duty_cycle:         {fidelity: lumped}
  manufacturing_docs: {outputs: [lamination_dxf, bom, winding_spec]}
execution: {kind: native}             # native | map | fmu | remote
exposes: [ratings, losses, temperatures, od_mm, length_mm, mass_kg]  # whitelist (as in MCP)
validation: [{ref: ANSYS, case: L200, torque_delta_pct: 0.12}]
```

`cost` is compute time for scheduling and fair use, never a price.

### 2.5 Versions and provenance

- A published module version and card revision is **immutable**. An edit = new `revision` (card) or new `version` (module).
- Every result carries `{module@version, card@revision, contract, solver_commit, geometry_hash, mesh, materials, convergence}`. Our run records already do this; only `module@version` is added.
- A system pins versions (like a lock file). A vendor update does not change old results: the user sees "v2.4 available" and re-runs himself.
- A **released design revision** (section 7.3) pins the system lock plus the machine description hash; every drawing, BOM, RFQ and order references that revision.

### 2.6 Contract examples

| Module | Ports | Required minimum | Execution |
|---|---|---|---|
| **Motor** (ours) | shaft, phases, heat/coolant, body | operating_point (FEM), efficiency_map, duty_cycle | native (our FEM) |
| **Controller** (ours) | dc_in, phases, heat/coldplate, body, cmd | operating_point: from V_dc and I/f on phases → losses, T_j, η; series: PWM voltage `InverterVoltageSource` | native |
| **Propeller** | shaft, thrust, air, body | operating_point: ω, V_inflow, ρ → T_shaft, F; map C_T(J), C_P(J) | map (vendor table) |
| **Gearbox** | shaft_in, shaft_out, heat, body | T_out = i·η·T_in, ω_out = ω_in/i; η(T, n, T_oil) | map → later FMU |
| **Battery** | dc_out, heat, body | V = OCV(SoC, T) − I·R(SoC, T); I²R losses; SoC(t) for missions | map / equivalent circuit (`simulation/battery.py` exists) |

### 2.7 Physical port contract (fixed before M0 is implemented)

Owner decision 2026-09-29, after the Codex structure review (`docs/project-structure-review-2026-09-29-codex.md`, "Contracts to fix before M0 freezes"). The contract follows acausal physical connectors (Modelica): every energetic port carries one **across** (potential) and one **through** (flow) variable; at a connection node the across variables are **equal** and the through variables **sum to zero**. Connections (links) are ideal: they carry no storage and no loss. Anything that stores or dissipates energy is a module.

#### 2.7.1 Sign convention and units

- **Positive through variable = into the module** (Modelica convention). Port power `P_k` is positive when energy flows **into** the module. This replaces the earlier draft "positive = leaves" (D2 restated).
- A source (battery discharging, motor shaft driving a load) therefore shows **negative** power on its delivering port. Efficiency is computed from the magnitudes of the input and output ports of the current operating mode, never from signs.
- **Canonical units at the new boundary are pure SI:** K (not °C), m (not mm), rad/s, N·m, W, J, kg, Pa, s. °C, mm, rpm and kW stay in the UI, reports and legacy files; adapters convert explicitly at the boundary (a unit-tested `legacy_units` table per adapter). Old solver arithmetic is not changed just to rename units.
- Every port value is a typed quantity `{value, unit, form, basis}`; the core rejects an unknown or dimensionally wrong unit (2.7.8).

#### 2.7.2 Energy balance per module

For every module, with `P_k` the power on port k (positive in):

    sum_k P_k = dE_stored/dt + P_loss,env

`E_stored` is the sum of the module's declared **storage states**. `P_loss,env` is loss leaving the modelled system without a thermal port; it is allowed only for modules without a thermal port and must be declared (`loss_sink: ambient`). For a module **with** a thermal port the balance closes completely:

    sum_(energetic ports) P_k + Q_th,in = dE_stored/dt

Electrical and mechanical losses are therefore not a separate term: they leave as heat on the thermal port (`Q_th,in < 0`) or raise the module's own thermal state. A loss is never counted twice (on the port and in an internal thermal mass).

Storage is always **module state**, declared in the manifest with unit, initial value and energy function. Connectors carry no storage.

| Module | Storage state | E_stored |
|---|---|---|
| Motor | winding, stator iron, magnet, rotor temperatures (thermal network nodes); rotor speed ω | Σ C_i·T_i ; ½·J·ω² |
| Controller | junction / case / heatsink temperatures; DC-link capacitor voltage (if the capacitor belongs to the controller) | Σ C_i·T_i ; ½·C·V² |
| Battery | SoC, cell thermal mass, optional RC polarisation voltages | Q_nom·∫OCV dSoC ; C_th·T ; ½·C_RC·V_RC² |
| Gearbox | oil/housing temperature; optional shaft twist (compliance) | C_th·T ; ½·k·Δθ² |
| Load / vehicle | inertia, or body kinetic + potential energy | ½·J·ω², or ½·m·v² + m·g·h |
| DC link (explicit `dc_link` module) | capacitor voltage | ½·C·V² |

Irreversible states (magnet Br ratchet, SoH) are declared as states without an energy term; they are committed only on an accepted time step, never on a trial evaluation (review point 6).

#### 2.7.3 Port types: variables, units, forms

| Port | Across | Through (+ into the module) | Power into the module | Notes |
|---|---|---|---|---|
| `elec.dc` | V [V] | I [A] | P = V·I | instantaneous in `series`; period mean in `scalar`, basis stated |
| `elec.ac3` | phase voltages v_a, v_b, v_c [V] | phase currents i_a, i_b, i_c [A] | p(t) = Σ v_x·i_x | which power is carried: 2.7.4 |
| `mech.shaft` | φ [rad] / ω [rad/s] | τ [N·m] acting on the module | P = τ·ω | ω positive in the shaft's declared positive direction |
| `mech.linear` | x [m] / v [m/s] | F [N] on the module | P = F·v | |
| `thermal.heat` | T [K] | Q [W] | Q (already W) | T·Q is **not** power |
| `thermal.coolant` | p [Pa]; specific enthalpy h [J/kg] (T [K] via the `fluid` card) | ṁ [kg/s] | ṁ·h (+ hydraulic ṁ·p/ρ, included or declared negligible) | stream semantics, 2.7.6 |
| `body.motion` | v⃗ [m/s], ω⃗ [rad/s] | F⃗ [N], M⃗ [N·m] on the module | F⃗·v⃗ + M⃗·ω⃗ | body frame; mount point is a link property |
| `signal.cmd`, `env`, `envelope`, `energy.store` | non-energetic | — | — | never enter the energy balance |

**Value forms and time basis.** Each port value declares a form and a basis:

| Form | Basis (mandatory) | Use |
|---|---|---|
| `scalar` | `instant`; `mean@T` (averaged over a stated period: electrical period, PWM period, mechanical revolution); `rms@T`; `fundamental` (complex phasor of the 1st harmonic, or dq in a stated frame) | operating point |
| `map` | basis of the tabulated value + the validated domain box | η(τ, ω, V), P_loss(τ, ω, V, T) |
| `series` | sample times t_i [s] from the scenario start, uniform `dt` or explicit | transients, cycles |

Series rules: a module states its maximum usable `dt` and whether it needs aligned samples; the core resamples only with a declared method (zero-order hold for switched quantities, linear for states) and never across an event; energies are compared by trapezoid integration of P(t) on the finer grid. Mixing bases at one node (e.g. an `rms` current into a `mean` power balance) is a validation error. Maps never extrapolate silently: a query outside the validated box returns `out_of_domain`, and the solve fails loudly or falls back to a declared higher-fidelity calculation (review point 6).

#### 2.7.4 Electrical and PWM: power across the inverter

The inverter is where representations change, so the contract fixes what each side carries.

- **DC side:** `elec.dc`, `scalar` with basis `mean@T_pwm` or `mean@T_el`, or instantaneous `series`. With resolved ripple P_dc is the mean of the product V·I, not the product of the means.
- **AC side:** instantaneous phase quantities (`series`) or, in `scalar`, **fundamental phasors** per phase (or dq in a stated frame) plus a declared harmonic-power term. RMS + cos φ is an informational field only; it is **not** a valid power representation for PWM or unbalanced operation.
- **Balance across the inverter** (mean sense): P_dc = P_ac,1 + P_ac,h + P_loss,inv, where P_ac,h is harmonic power delivered to the motor, which becomes motor harmonic loss (AC copper, iron, magnet eddy) on the motor's heat port.
- **Fidelity levels** (declared per calculation):

| Level | AC representation | Ripple / harmonic losses | Today's code |
|---|---|---|---|
| `avg_fundamental` | fundamental phasor, ideal averaged switch | none; device losses from mean/RMS currents | `drive: sine` + `inverter/losses.py` |
| `pwm_averaged` | fundamental + carrier-harmonic loss **maps** built from switching-resolved runs | harmonic loss as a declared term | PWM loss map in the thermal coupling |
| `switching_resolved` | instantaneous PWM phase voltages, `series` with dt ≤ T_pwm/20 | explicit in the FEM | `drive: pwm`, `InverterVoltageSource` |

- **DC bus with capacitor:** the DC-link capacitance is a storage state (½·C·V²) owned by exactly one module (the controller by default, or an explicit `dc_link` module); the battery–bus connection is an ideal node. Capacitor ripple current and ESR loss go to that module's heat port. In steady `mean` operation dE_C/dt = 0.

#### 2.7.5 Mechanical

- Across ω [rad/s] (angle φ in `series` when compliance is modelled), through τ [N·m] acting on the module. Each shaft port declares its positive rotation direction; a link checks that both ends agree.
- **Motoring:** electrical port power > 0 (in), shaft power < 0 (out). **Generating:** signs flip. No "if generator" branch: the mode is the sign pattern, efficiency = |out| / |in|.
- Inertia J [kg·m²] is module state (½·J·ω²), not a port property. Rigidly linked inertias are merged by the core (J_total) to avoid an algebraic loop.
- **Gearbox:** ratio i (ω_out = ω_in / i), loss map to the heat port; **optional states** torsional compliance k [N·m/rad] with damping d (state Δθ, energy ½·k·Δθ²) and backlash b [rad] (dead zone on Δθ, handled as an event in `series`). Without them the gearbox is rigid and algebraic-lossy.

#### 2.7.6 Thermal and coolant

- `thermal.heat`: across T [K], through Q [W] into the module; connection = equal T, ΣQ = 0. Conductances and capacities live in modules, never in links.
- `thermal.coolant`: across p [Pa] and the stream's specific enthalpy h [J/kg]; through ṁ [kg/s] into the module. Each port carries the enthalpy of the fluid **leaving** through it (stream variable); a module uses the upstream enthalpy for the actual flow direction (Modelica `inStream`), and mixing at a node is ṁ-weighted. Reverse flow is allowed only in modules that declare it; otherwise ṁ < 0 into an inlet is a validation error.
- Each coolant module reports T_in, T_out [K], Δp = p_in − p_out [Pa] and the heat taken Q = ṁ·(h_out − h_in). Pump power (ṁ·Δp/ρ) is declared negligible or carried on the pump's own electrical port.

#### 2.7.7 Worked example: battery → controller → motor → load

Steady mean operating point (basis `mean@T_el`), all thermal ports to a coolant node. Numbers are illustrative (L155 class); only the balance matters.

| Module | Port powers into the module [W] | Loss → heat port [W] | dE/dt [W] |
|---|---|---|---|
| Battery | dc −103 000; heat −2 100 | I²R 2 100 | dE_chem/dt = −105 100 (SoC falls) |
| Controller | dc +103 000; ac3 −101 000; heat −2 000 | switching + conduction + DC-link ESR 2 000 | 0 |
| Motor | ac3 +101 000; shaft −97 500; heat −3 500 | Cu + Fe + magnet + mechanical 3 500 (incl. P_ac,h) | 0 |
| Load | shaft +97 500 | absorbed by the load | ½·J·ω² constant |

Checks the core performs:

1. **Nodes:** DC −103 000 + 103 000 = 0; AC −101 000 + 101 000 = 0; shaft −97 500 + 97 500 = 0; the coolant node receives 2 100 + 2 000 + 3 500 = 7 600 W.
2. **Modules:** battery −103 000 − 2 100 = −105 100 = dE_chem/dt; controller 103 000 − 101 000 − 2 000 = 0; motor 101 000 − 97 500 − 3 500 = 0.
3. **System:** −dE_chem/dt = P_load + Q_coolant: 105 100 = 97 500 + 7 600.

In a transient (acceleration) the same equations hold with dE/dt ≠ 0: at the shaft τ_motor = J_total·dω/dt + τ_load, SoC and every thermal node are integrated, the DC-link ½·C·V² is included, and the check runs on energies integrated over each accepted step.

#### 2.7.8 Consistency checks on every system solve

| Check | Rule | Tolerance |
|---|---|---|
| Units | every port value has a known SI unit matching its port type; basis consistent per node | exact (error, no solve) |
| Node conservation | Σ through = 0 per node, across equal | ≤ 1e-9·max|term| for algebraic nodes; ≤ loop tolerance for iterated nodes |
| Module energy residual | r = Σ P_k − dE/dt − P_loss,env | steady: ≤ 1e-3·P_ref for FEM modules, ≤ 1e-6·P_ref for maps/analytic (P_ref = largest port power); transient: integrated over the step ≤ 1e-3·E_step |
| System residual | Σ of module residuals | ≤ 2e-3·P_ref |
| Convergence | residuals on **all** coupled quantities (V, I, τ, ω, T, Q), not voltage only | declared per loop; failure = no result |
| Map domain | query inside the validated box | exact (`out_of_domain` = error or fidelity fallback) |
| State commit | trial evaluation separate from committed step; irreversible states only on accept | exact |

Residuals and tolerances are stored in the result provenance (2.5); a result with a failed check is not stored as valid.

#### 2.7.9 Two independent validation tracks

| Track | What changes | Acceptance |
|---|---|---|
| **A. Adapters (restructure, M0–M3)** | code moves behind the contract; same legacy call, inputs and runtime | **bit-identical** (`repr` of numerical fields) on L155 motor, L180 generator, L13; timestamps, timings and job ids excluded |
| **B. Numerical changes** (mesher triangle → gmsh, a new solver runtime) | numbers change by design | physically justified tolerances (e.g. torque ±0.5 %, losses ±2 %, temperatures ±2 K), mesh-convergence study (≥ 3 refinements, Richardson estimate), re-check on the ANSYS cases (40, 150, 200 mm), full provenance |

The tracks never share a PR: a restructure PR changes no number, a numerical PR moves no code across the contract.

---

## 3. System graph and solve

### 3.1 Architecture diagram

```mermaid
flowchart LR
  subgraph Core["PORTAL CORE"]
    ID[Identity: users, orgs, roles, grants, audit]
    WS[Projects / systems / scenarios / workspaces]
    Q[Job queue + nodes + usage: platform and BYO compute]
    CAT[Catalog: cards + provenance + supplier offers]
    SOL[System solver: operating point along the chain]
    MCP[MCP + API v2]
    NOTE[Notifications: in-app + e-mail]
    FS[File store: drawings, datasheets, attachments]
  end
  subgraph Eng["Engineering graph (one system)"]
    BAT[Battery] -- elec.dc --> CTL[Controller]
    CTL -- elec.ac3 --> MOT[Motor]
    MOT -- mech.shaft --> GB[Gearbox]
    GB -- mech.shaft --> PROP[Propeller]
    PROP -- body.motion --> VEH[(Vehicle / mission)]
    ENV[Environment] -. env .-> PROP & VEH
    MOT -- thermal.heat --> COOL[Cooling loop]
    CTL -- thermal.heat --> COOL
  end
  subgraph Mfg["Manufacturing + sourcing"]
    REV[Released design revision]
    DOCS[Drawings, BOM, STEP, winding spec]
    RFQ[RFQ -> quote -> order -> production -> delivery]
  end
  SOL -. drives .-> BAT & CTL & MOT & GB & PROP & COOL
  WS --> REV --> DOCS --> RFQ
  CAT --> RFQ
  RFQ <-- API: orders, items, passports --> ERP[(motres_erp: MOTRES inventory, production, invoices, shipping)]
  ID --> SUP[Supplier / manufacturer / customer orgs]
  SUP <--> RFQ
  RFQ --> NOTE
```

ASCII fallback (for readers without Mermaid):

```
 users + orgs (roles, grants, NDA policies, audit)
        |
 project -> system (graph of cards@rev) -> scenarios -> results (+provenance)
        |                                     |
        |                         jobs -> platform nodes / BYO nodes / isolated FMU nodes
        v
 released design revision --> drawings + BOM + STEP (+ approvals)
        v
 RFQ --> quotes --> PO / production order --> production --> delivery
   ^  suppliers, manufacturers, customers (other orgs)      |
   |                                                         v
 catalog cards + supplier offers          motres_erp (MOTRES system of record) via API
```

### 3.2 How the operating point is solved

1. **Demand** at one end (propeller thrust at flight speed, or shaft torque, or a mission profile).
2. **Pass "load → source"** on maps: propeller → (T, ω) on the shaft → gearbox → (T, ω) on the motor → motor → (I, V, f) on phases → controller → (I, V) on the bus → battery → V_bus(I, SoC). Cheap: milliseconds per point.
3. **Voltage check:** if V_bus after sag is insufficient for the required ω → field weakening / limiting. Another pass until V_bus converges (usually 2–4 iterations, 0.5 % tolerance).
4. **Refinement with exact models:** only at the converged point is the motor FEM and the controller loss model run (today's coupled loop); the result replaces the maps at that point.
5. **Thermal pass:** heat of all modules → cooling loop → temperatures → re-evaluated losses (copper resistance, Br, R_DS(on)(T_j)). Convergence as today: 2 K on winding and magnet, 5 K on the bearing seat, up to 6 iterations.

This is **Gauss–Seidel over the graph** (successive substitution): exactly how `coupled.py` already works, only with three fixed participants. A Newton solve over the whole graph is not needed until stiff loops appear (e.g. two batteries in parallel).

### 3.3 Maps or co-simulation

| Mode | When | Cost | Gives |
|---|---|---|---|
| **Maps** (quasi-static) | selection, drone mission, variant comparison | ms/point | efficiency, consumption, mass, endurance |
| **Point refinement** (today's loop) | passport, report | minutes | FEM accuracy at chosen points |
| **Co-simulation** (FMI 3.0 co-simulation, core step) | PWM ripple, transients, start-up, short circuit | hours | `series` on ports |

FMI co-simulation is not needed for the first two roadmap steps. It is only reserved in the contract (`forms: [series]`, `execution: fmu`).

### 3.4 Where today's passes go

| Today's mechanism | In the new scheme |
|---|---|
| `drive: current` | motor solved from a current setpoint; controller absent (port `phases` ideal) |
| `drive: pwm` | ideal bridge as the "built-in default controller" |
| `drive: inverter` (`InverterVoltageSource`, `fit_device_drop`) | `elec.ac3` link between controller and motor in `series` form: controller delivers voltage with dead time and switch drops |
| EM ↔ thermal loop, bearing seat | thermal pass (step 5) |
| Modes and critical speeds | `mechanical_check` of the motor module; the shaft passes J and stiffness to the gearbox |

---

## 3A. Mission and motion simulation (end goal)

### 3A.1 One scheme for air, water, ground and stationary

A mission = **the system graph run in time** under a demand. No aerodynamics "in the core": everything that depends on the medium and the object is a module of kind `environment`, `vehicle`, propulsor/wheel and `controller_logic`, with the same port contract.

```
 Mission profile --> controller_logic --signal.cmd--> controllers --elec.ac3--> motors
 (speed/altitude/      (thrust allocation,           ^ elec.dc                 | mech.shaft
  depth/slope/          trajectory)                  |                         v
  force/S1-S9 cycle)          ^                 energy.store              gearbox / shaft
                              | body state      (battery, fuel cell,           |
                              |                  supercap)                     v
                         vehicle (1..6 DOF) <--body.motion-- propulsor: propeller in air/water,
                              ^                              wheel, track, waterjet, link
                              +------------- env (environment: ISA, water, ground, wind, waves)
 Heat of all blocks --thermal.heat--> cooling loop (transient thermal networks)
```

**Mission profile** is universal: a demand over time or path of any quantity (speed, altitude, depth, slope, force/torque, point trajectory), plus external conditions (wind/current/waves, medium T and ρ, payload) and events (payload drop, rotor failure). Ready libraries: flight segments (take-off, climb, hover, cruise, manoeuvre, landing), drive cycles WLTP/NEDC/UDDS, sea states, duty cycles S1–S9 per IEC 60034-1, robot trajectories.

### 3A.2 Examples: one contract for all

| Object | Graph | Environment / demand | Output |
|---|---|---|---|
| **Quadcopter** | battery → 4× (controller → motor → propeller) → `vehicle` 3-DOF (point mass), then 6-DOF; `controller_logic` = thrust allocation | ISA + wind/gusts; take-off–hover–cruise–landing; payload | endurance/range, power(t), T of winding/magnet/T_j/battery(t), margins, which block limits |
| **E-boat / AUV** | battery → controller → motor → (gearbox) → marine propeller in **water** (same `propeller` card, `env.medium=water`, K_T/K_Q) → `vehicle` with hull drag and added masses | water, current, waves; speed/depth profile | range, speed, cavitation margin, heat with sea-water cooling |
| **Wheeled robot / car / AGV / e-bike** | battery (+supercap) → controllers → motors → gearbox → wheel (`wheel`: radius, f_r, grip) → `vehicle` (longitudinal dynamics 1-DOF, then 3-DOF) | slope, surface, wind; WLTP or AGV route | range, regeneration, overheating on climbs, wheel slip |
| **Robot joint** | controller → motor → gearbox (strain-wave/planetary) → `joint` → link (`vehicle` = manipulator, 1…6 DOF) | trajectory, payload, S3/S6 | peak/RMS torque, winding T over the cycle, demagnetization at peak |
| **Generator / pump** (stationary) | prime mover/load (`mech.shaft`) → motor-generator → controller → bus/battery | load cycle S1–S9 | cycle efficiency, thermal margin, run-up/load rejection |

Ports cover rotation (`mech.shaft`), translation (`mech.linear`), medium loads (`env` + `body.motion` via propulsor/hull), energy storage (`energy.store` on `elec.dc`: battery, fuel cell, supercap, generator) and heat (`thermal.*`).

### 3A.3 What a mission computes

- **Object dynamics:** start with a point mass (1-DOF for a car/joint, 3-DOF for a drone and boat), 6-DOF later; same `body.motion` port, only the `vehicle` module changes.
- **Medium loads:** propulsor maps (C_T/C_P, K_T/K_Q), hull/airframe coefficients (C_x·S); later coefficients from CFD modules (CFD computes tables ahead, the mission consumes the table).
- **Control:** `controller_logic` turns the demand (thrust, speed, trajectory) into drive commands; later a vendor flight controller as an FMU.
- **Energy:** SoC, voltage sag through R_int(SoC, T), battery temperature; fuel cell V(I) and H₂ consumption.
- **Thermal transients** of motors, controllers, batteries along the mission (thermal networks, not steady state).
- **Limits and failures** (checked every step; the first violation is recorded with time and block): winding/insulation T, magnet T and demagnetization (our worst-element rule), switch T_j, current/voltage, SoC_min, rotor stresses (SF on averaged stresses), speed limit.
- **Results:** run time / range; power, current, temperature, SoC plots; margins for every limit; **which block limits** and when.

### 3A.4 Fidelity ladder

Each module offers several fidelity levels of the same calculation:

| Level | Motor (ours) | Controller (ours) | Propulsor / object | Speed |
|---|---|---|---|---|
| L0: maps | η(T, n, V_dc, **T_winding, T_magnet**), P_loss by type, T_max(n) | P_loss(I, V, f_sw, T_j) | C_T/C_P, f_r, C_x·S | µs/point |
| L1: reduced model | thermal network (lumped, as `thermal_duty_cycle.py`, `coupled_duty_cycle.py`), d-q model with L_d/L_q(I) | switch Z_th(t) | 3-DOF | ms/step |
| L2: full | coupled EM ↔ thermal FEM (`coupled.py`), PWM through `InverterVoltageSource` | SPICE (`inverter/spice`) | 6-DOF, CFD | minutes–hours |

**Missions always run on L0/L1.** L2 is used to **build and check** L0/L1:

- our coupled FEM is run over a grid of points (T, n, V_dc, temperature), which our sweeps and `continuous_rating` already do → a map with **provenance** (`module@version`, geometry_hash, solver_commit, mesh, point grid);
- the map is stored as a result card in the catalog (`kind: map`, referencing its source runs);
- **accuracy is tracked:** for each map, control points re-computed by FEM off the grid (interpolation error, %), and comparison with ANSYS/bench where available (L200 +0.12 % torque, L40 +3.6 %). The error goes into `validation` and is shown next to the mission result ("±2 % on motor losses, ±5 % on propeller: datasheet");
- at the end of a mission the core can **re-check the most stressed points on L2** (usually 3–5 points: thermal peak, current peak, V minimum) and show the difference; if it exceeds tolerance the map is refined in that region.

### 3A.5 Time integration and co-simulation

- **Stiffness:** electrical processes (µs–ms) and mechanics (ms–s) versus heat (s–hours). In a mission, electrics are **quasi-static** (L0 map every step); mechanics and heat are integrated. PWM ripple is not part of a mission; its contribution is already inside the loss maps (PWM pass).
- **Steps:** mechanics 1–10 ms (drone) / 10–100 ms (car, boat); heat 0.1–1 s: **multirate** integration (heat updated less often). Our own modules: variable step (BDF/Radau from SciPy for thermal networks); exchange with foreign modules: fixed macro step.
- **Co-simulation master (FMI 3.0):** the core steps with macro step Δt, passes port inputs to each FMU, `doStep(Δt)`, collects outputs; for stiff couplings (heat ↔ losses) the step is repeated with state rollback if the module supports `getFMUState/setFMUState`, otherwise extrapolation and a smaller Δt. Model-exchange FMUs are integrated directly by our integrator.
- **Fast on our cluster and on BYO nodes:** one map-based mission = one process, seconds; **batch/parametric missions** (motor × propeller × battery × mass sweeps, Monte Carlo on wind) and **optimization** ("choose motor+propeller+battery for maximum endurance") run as `jobs.py` kinds `mission_batch` / `mission_opt`, leased to platform or user-owned nodes through the BYO-compute protocol, and accounted in `job_usage` (CPU·h, module@version, node owner) for fair use. Our two-stage optimization (free search → local refinement) moves to system level; L2 re-checks only for finalists.

### 3A.6 What this means for the contract NOW

To avoid breaking things later:

1. **State in the contract from day one** (M0): motor and controller thermal state, battery SoC/T, body state: declared variables with units; the core stores and returns them. Stateless modules remain allowed.
2. **Every energy port admits the `series` form** already in `portal/1.0`, even if the first implementation only delivers `scalar`.
3. **Time and environment are explicit inputs**: `t`, `Δt`, an `env` reference. Today's ambient/coolant T from the Thermal tab becomes a value of the `env` / `thermal.coolant` port, not a constant.
4. **The motor must deliver** (by the end of roadmap step 1): efficiency and loss-by-type maps (copper DC/AC, iron, magnets, mechanical) **dependent on winding and magnet temperature and on V_dc**; torque limit T_max(n, V_dc, T); an L1 thermal network (nodes: winding, magnet, stator, rotor, housing, bearing) with C and R from our `thermal_capacities.py` / `thermal_heat_paths.py`; demagnetization thresholds by T; rotor J.
5. **The controller must deliver:** P_loss(I, V_dc, f_sw, T_j), Z_th(t) switch → cold plate, I and T_j limits.
6. **The battery** (today `simulation/battery.py`) becomes a module with SoC and T state right away.
7. **Limits are part of the manifest** (`limits:` with name, unit, value and source) so the core checks them the same way for any module.

---

## 4. Moving motor and controller without breakage (full restructure)

Owner decision (2026-09-29): **the whole project is restructured onto the portal architecture**, step by step, without breaking results. Principle: **wrap first, then move.** The new path calls the old code and must produce **bit-identical** numbers. Old URLs live until they are no longer used.

### 4.1 Stages

| Stage | What | Acceptance |
|---|---|---|
| **M0. Contract** (`contracts/ports.py`, `portal/1.0`) | Pydantic port types, units, signs, forms, state, limits; manifest v2 (extend `ModuleManifest`: `ports`, `calculations`, `execution`, `exposes`, `state`, `limits`). Types + conformance tests only | schema tests; `CONTRACTS_VERSION` not broken |
| **M1. Motor adapter** | `modules/motor_adapter.py`: `operating_point(ports) →` calls the same code as `/api/coupled/run`; `machine/1.0` schema + IPM yaml → machine description converter | **L155 motor, L180 gen, L13**: adapter results == `/api/coupled/run` bit-identical (`repr` comparison, as in `tests/fixtures/catalog_golden/`); golden files captured before work starts; geometry hash unchanged |
| **M2. Controller adapter** | `modules/controller_adapter.py` over `inverter/losses.py`, `coupling.py` | losses, T_j, η on L155 + IMCQ120R004M2H == `/api/controller/*` bit-identical |
| **M3. System solver** (`portal/system_solver.py`) | two-node graph (controller → motor) + heat; successive substitution **calls** the existing loop, does not copy it | system "L155 + controller" == `drive: inverter` on L155/L180/L13 bit-identical; same iteration count |
| **M4. Data model** | `org`, `project` and `system` **beside** dies: system = `{nodes: [card@revision], links: [port → port]}`. Every existing cfg gets an **implicit system** "battery(cfg.battery) → controller(cfg.controller) → motor(cfg)" without rewriting files; every user gets a personal org | `family.tree()` unchanged; implicit system computes the same as cfg |
| **M5. Battery as a card** | `cfg.battery` → reference to a `battery_pack` card (adapter reads the old field if no reference) | GF Myriad 200S3P 750 V on CILN28: same V_min/nom/max |
| **M6. New routes** | `/api/v2/systems/*`, `/api/v2/modules/*`, `/api/v2/orgs/*`. Old `/api/family/*`, `/api/coupled/*`, `/api/controller/*` stay as thin wrappers | old web tests green; web untouched until M7 |
| **M7. Retire the old** | old routes get a `Deprecation` header; after 2 releases with no calls (seen in `usage_stats.note_request`) they are removed. MCP tools are never removed without a version bump (`list_machines` stays) | usage log = 0 calls in 30 days |

Stages M8–M12 (parties, documents, sourcing) follow in section 11.3.

### 4.2 What we do NOT change

- Files `config/dies/<die>/<cfg>.yaml`, die keys, `?mat=` URLs for duties.
- MCP tool names and their output whitelist (`GEOMETRY_DENYLIST`).
- Cards `device/bearing/lubricant` (catalog stage 1 bodies are already immutable).
- No `git stash` in `motor_ai_sim`, no "while we are at it" refactor of the 7.7 k lines of `coupled.py`.

### 4.3 Old → new model

| Today | Later |
|---|---|
| user | user ∈ organization (everyone has a personal default org) |
| die | family card `motor_family` (lamination, stamping die) |
| configuration (cfg) | `motor` card (product) |
| duty | system **scenario** (operating point / cycle) |
| cfg.controller / cfg.battery | system nodes referencing cards |
| die_access grants | object grants (any kind, any org) |
| saved cfg "for production" | **released design revision** (section 7.3) |

---

## 4A. Motor geometry: sources and machine types

Owner decision (2026-09-29): offer geometry import, material assignment and simulation (the simplest variant) now in the structure but not as the main priority; later a step-by-step motor editor; the key thing now is to lay all these capabilities into the structure. A customer must be able to build **their own** motor geometry, not only use ours.

Today: one parametric family (IPM, radial flux, 33 parameters) + parameter import from a Fusion CSV; users already create their own dies (one customer already has).

### 4A.1 One contract, many geometry sources

The motor module is one from the outside (section 2 ports). Inside are **geometry sources**, each producing the same **machine description** (4A.2); solvers and ports read only that.

| Source | What | When |
|---|---|---|
| **1. Parametric families (generator plugins)** | IPM today; later SPM, outer rotor (outrunner), axial flux, induction, synchronous reluctance, wound field (EESM); distributed and concentrated windings. Each family is a plugin `generate(params) → machine description` | IPM exists; new ones one at a time, on demand |
| **2. 2-D section import** | DXF (STEP/sketch later) → region recognition (stator/rotor steel, magnets with magnetization direction, slots/coils, shaft, air) → the user assigns catalog materials and winding (phases/coils, turns, parallel paths) → symmetry/periodicity detection → loud validation → the same solvers | early simple milestone after M0/M1 |
| **3. Step-by-step editor** | wizard: topology → dimensions → winding → materials → cooling → validation → solve; writes the same machine description (calls a family generator or edits an imported section) | later |

### 4A.2 Machine description (`machine/1.x`): the common format

| Field | Content |
|---|---|
| `schema` | `machine/1.0`; minor = fields added only, major = parallel support + migrator |
| `topology` | `radial_inner`, `radial_outer`, `axial`; type `ipm/spm/im/synrm/eesm`; poles, slots, stack length |
| `regions[]` | closed contours (mm, section plane) with a role: `stator_steel`, `rotor_steel`, `magnet`, `coil_side`, `shaft`, `sleeve`, `air`, `airgap` |
| `materials{}` | role/region → catalog card@revision (steel, magnet `<grade>_<T>C`, copper, insulation) |
| `magnets[]` | region → magnetization direction (angle or parallel/radial), polarity |
| `winding` | phases, coils → slot sides (+/−), turns, parallel paths, Y/Δ, pitch, slot fill, strand model (strip/strand) |
| `symmetry` | period (poles/slots in the model), boundary conditions (periodic/anti-periodic), verified flag |
| `mesh_hints` | airgap element size, skin depth ("mesh follows physical scales") |
| `mechanical` (v3) | axial data needed for drawings: stack length, lamination thickness and count, skew, shaft/housing/bearing seats with nominal sizes and tolerance classes (section 7) |
| `provenance` | source (`generator:ipm@ver` + parameters / `import:dxf` + file hash / `editor`), author, date, geometry hash |
| `owner`, `visibility` | owner org; `private` by default; grants |

Link to today's data: die = family `motor_family` + common lamination (steel regions, slots, pockets); cfg = product (length, magnets, winding, materials). For IPM the machine description is **computed** from yaml by the generator; files do not change; the geometry hash stays bit-identical ("geometry built carefully").

### 4A.3 Validation (one for all sources)

Closed contours; overlaps and gaps; slivers and thin features relative to mesh size; every region has a material; every magnet a direction; ampere-turns balanced over phases; symmetry matches pole/slot count. Errors are loud and name the region; an impossible machine is never solved (client-facing validation rule).

### 4A.4 Ownership and access

| Rule | How |
|---|---|
| Customer geometry | `private` by default, visible only to the owner org; shared by grant (like die_access) |
| MCP | never returns lamination geometry of someone else's machine (`GEOMETRY_DENYLIST`); own geometry only to the owner |
| Export of own geometry | the owner may download their own (DXF / machine description); for our families only results and parameters, not lamination contours, unless granted |
| Other module vendors | receive port values only, never geometry |
| Suppliers / manufacturers | receive only the **document package** released to them in an RFQ or order (section 8), never the live model |

### 4A.5 What to lay into the structure NOW

1. Schema `machine/1.0` + converter "IPM yaml → machine description" with bit-identical check on L155/L180/L13 (in M1).
2. Generator plugin interface: `manifest` (parameters, limits, topology) + `generate(params)`; IPM is the first plugin.
3. Solvers read only the machine description (through the adapter), never IPM parameters directly: new path, old code untouched.
4. One validation pipeline (4A.3) used by generator, import and editor.
5. One material/winding assignment UI component for every source (reused from the Motors tab).
6. `owner/visibility/provenance` in the machine description from day one.

---

## 5. Third-party modules (non-commercial)

### 5.1 Onboarding a module vendor

1. **Vendor organization** (role `module_vendor`, section 6) accepts the contributor terms: module **code** contributed to the platform is AGPL-3.0-or-later with a DCO sign-off; **data** (cards, maps) carries a per-card data licence chosen by the vendor (`view_only` or `download`). No contract with money, no revenue share.
2. **Cards** in the catalog envelope with status `draft`. A source is mandatory: datasheet (PDF in the file store), table/figure number, `basis: table|figure`, as already done for MOSFET cards.
3. **Validation against the datasheet:** the core recomputes 3–5 published points (e.g. propeller C_T at 3 J values, gearbox efficiency at rated point) and records `validation` with the deviation. Threshold → status `active`.
4. **Manifest check:** contract conformance tests (ports, units, signs, map monotonicity, energy conservation: output ≤ input at every point).
5. **Publication** → the card is visible in the catalog with a quality badge.

### 5.2 Execution options

| Option | Vendor provides | Isolation | When |
|---|---|---|---|
| **Maps** (tables) | CSV/YAML: η(T, n), C_T(J)… | none needed (data, not code) | **Step 2**, most vendors |
| **FMU** (FMI 2.0/3.0) | binary `.fmu` (compiled model, sources hidden) | container without network, CPU/RAM/time limits, read-only, separate node | **Step 3b**; gearboxes with thermal model, batteries |
| **Remote service** | vendor HTTPS endpoint answering per contract | model stays with the vendor; we send **only port values** | **Step 3c**; vendors that do not release a model (CFD) |

A binary FMU is not source code; accepting one does not conflict with the platform's own AGPL licence (it is data executed in a sandbox, like a user upload). The owner's Windows workstation blocks native `.pyd` (WDAC); FMU execution is therefore only on Linux nodes (Hetzner or BYO Linux nodes that opt in), never locally.

### 5.3 Security and IP protection

- **Foreign code never runs in the API process.** Only a separate executor (container: `--network none`, seccomp, limits, temporary FS), dispatched through `jobs.py` and the node lease protocol.
- **Minimum data out:** a module receives only its port values. Our motor geometry never goes to the propeller vendor. Same principle as the MCP whitelist.
- **Vendor IP protection:** maps and FMUs are visible in the UI only as results; a card cannot be downloaded as a whole unless `license: download`. A remote service gives maximum protection.
- **Audit:** every call to a foreign module is a log row (as `config/mcp_audit.jsonl`): who, which module@version, how many seconds.
- **Remote service:** mTLS / signed requests, time-out, retries, cache by input hash.

### 5.4 Quality and fair use (replaces "money and quality")

- Add `module`, `module_version`, `vendor_org`, `own_node` to the `job_usage` row. `usage_stats.monthly` then gives CPU·h and call counts per module: used for **fair-use limits, capacity planning and vendor feedback**, not for billing.
- **Fair use:** per-user concurrency and monthly CPU·h soft limits on platform nodes; jobs leased to the user's own nodes (BYO) do not count against them. Admins can raise limits per user. No paid tiers.
- **Quality badges:** `datasheet` (checked against the datasheet), `measured` (there is a measurement), `validated by MOTRES` (our bench/ANSYS), `estimate`. The badge is computed from `prov`/`validation`, never set by hand.

---

## 6. Parties and organizations

### 6.1 Why now

Suppliers, manufacturers and customers are other legal entities. RFQs, quotes, orders and NDAs are between **organizations**, not users. v2 already recommended a minimal org model (D4); v3 makes it the foundation of sections 7–8.

### 6.2 Organization roles

An organization can hold several roles at once (MOTRES is engineering team, manufacturer, supplier of motors and module vendor).

| Org role | Typical party | Can do |
|---|---|---|
| `engineering` | design team (MOTRES, a customer's R&D) | projects, systems, designs, simulations, releases, RFQs |
| `customer` | buyer of finished products | view shared designs/datasheets, request quotes, place and track orders for finished products |
| `supplier` | components, materials, equipment (steel, magnets, wire, bearings, power devices, controllers, gearboxes, propellers, batteries, test equipment) | maintain supplier offers on catalog cards, answer RFQs, confirm orders, update delivery status |
| `manufacturer` | contract fab (lamination stamping/laser/EDM, winding, machining, assembly, PCB) | receive document packages, quote, accept production orders, report production status, upload inspection reports |
| `module_vendor` | maker of simulation modules/cards | publish modules and cards (section 5) |
| `platform_admin` | platform operator | global moderation, fair-use limits, org verification |

User roles on the platform stay `user` and `admin`; everything else is an org membership role.

### 6.3 Memberships and permissions

| Member role (inside an org) | Rights |
|---|---|
| `owner` | everything, incl. members, grants, NDA policies, deleting the org |
| `approver` | approve design releases and outgoing RFQs/orders (section 7.5, 8.4) |
| `engineer` | create/edit projects, systems, designs, drafts of documents and RFQs |
| `buyer` | create and send RFQs/orders (after approval where required), manage quotes |
| `sales` | (supplier/manufacturer side) answer RFQs, send quotes, confirm orders, update statuses |
| `viewer` | read only |

A permission check is `(user, org membership role, object, action) → allow/deny`, with object grants layered on top. `motor_access.py` die grants become the first case of object grants.

### 6.4 Cross-org sharing

- Every object (project, system, design revision, document package, card, RFQ, order) has an **owner org** and `visibility: private | org | granted | public`.
- A **grant** = `{object, grantee_org (or user), actions [view, comment, download, quote], scope (this revision only / follow new revisions), expires_at, nda_policy}`. Grants are explicit, revocable and listed on the object.
- Sharing a design with a supplier inside an RFQ creates a **scoped grant on the released document package only** (drawings, BOM lines relevant to that supplier), not on the live model or the simulation.
- Customers see a released **product card** (datasheet, passport numbers through the MCP-style whitelist), never lamination geometry unless granted.

### 6.5 NDAs as data-access policies

The platform does not sign or enforce contracts; it enforces **data access**. An NDA is recorded as a policy object:

| Field | Meaning |
|---|---|
| `parties` | the orgs covered |
| `reference` | external NDA identifier + optional uploaded PDF (private file store; never in the public repo, per the private split of PR #41) |
| `valid_from / valid_to` | grants that reference the policy expire with it |
| `allows` | classes of data: `drawings`, `bom`, `step`, `simulation_results`, `geometry` |
| `watermark` | downloads stamped with recipient org, user and date |
| `download` | allowed / view-only |

A grant that needs a class not allowed by an active policy between the two orgs is refused with a clear message.

### 6.5A NDA workflow (automatic signing)

The platform provides the **mechanism** for generating and signing NDAs, not legal advice. Every template must be reviewed by the org's counsel; the owner must have the MOTRES templates checked by a lawyer before the flow goes live.

**Templates**

| Aspect | Rule |
|---|---|
| Kinds | `mutual` (default) and `one_way` (disclosing → receiving party) |
| Ownership | per organization; the MOTRES mutual template is the platform default until an org uploads its own |
| Versioning | immutable versions (`v1`, `v2`, …); an instance pins the template version; a new version never alters signed NDAs |
| Variables | parties (legal names, registry numbers, addresses), purpose, data classes (`drawings`, `bom`, `step`, `simulation_results`, `geometry`), term and survival period, governing law and jurisdiction, signatories |
| Status | `draft → counsel_reviewed → published`; only published templates can generate instances (the "counsel reviewed" flag is set by the org owner, the platform does not verify it) |

**Automatic flow**

1. Org A shares a project / design revision / document package marked `nda_required` with org B, or org B requests access to such an object.
2. If no active NDA policy between A and B covers the requested data classes, the platform **generates an NDA instance** from A's published template (or the mutual default), pre-filled from both org profiles and the requested data classes; the requested grant is created in state `pending_nda`.
3. The instance is sent to the **authorised signatories** of both orgs (member role `owner` or `approver` with the `sign_nda` flag). Notifications go in-app and by e-mail.
4. **Click-to-sign:** identity comes from the account (verified e-mail, verified org membership); re-authentication at signing, with 2FA when the user has it enabled (orgs may require 2FA for signatories). The signer sees the full rendered text and its SHA-256 before signing.
5. **The access grant activates only after all required signatures.** Until then the recipient sees only the object's title and owner.
6. The platform produces the **signed PDF** with an embedded audit trail page: names, member roles, orgs, timestamps (UTC), hashed IP address, document SHA-256, template id@version, signature method. The PDF is stored immutably (content-addressed object store, write-once) and e-mailed to both parties.
7. **Expiry or termination** (by either party per the terms, or by the term end date) → all grants referencing the policy are revoked automatically; both parties get notice (30 and 7 days before expiry, and on revocation). Survival obligations stay in the text; the platform only stops access.
8. **Alternative:** an existing NDA signed outside the platform is uploaded manually (PDF + parties + validity + data classes), confirmed by the owners of both orgs, and then acts as the same access policy.

**Legal level.** Default is an **eIDAS simple electronic signature (SES)**: click-to-sign with account identity and audit trail. Advanced (AdES) or qualified (QES) signatures come later through an external qualified trust-service provider via API (decision D39). The platform makes no claim about enforceability; the owner must have the templates checked by a lawyer for the governing laws in use.

**Data model**

| Entity | Key fields |
|---|---|
| `NdaTemplate` | owner_org, kind (`mutual`/`one_way`), version, body (with variable placeholders), variables schema, status, counsel_reviewed_by/at |
| `NdaInstance` | template@version, parties (orgs + legal data snapshot), purpose, data classes, term, governing law, rendered text SHA-256, state, requested grants, signed PDF file id, valid_from/to, terminated_at/by |
| `Signature` | instance, signer user, org, member role, method (`ses` / `ades` / `qes`), signed_at, ip_hash, auth factors used (password, 2FA), document SHA-256 signed, provider reference (for AdES/QES) |
| `AccessPolicy` link | the `nda_policy` of 6.5 references `NdaInstance` (or a manual upload); grants reference the policy |

**State machine**

```
 draft --send--> sent --first signature--> partially_signed --all signatures--> active
 draft | sent | partially_signed --> withdrawn | declined
 active --term end--> expired ; active --notice--> terminated
 expired | terminated --> grants revoked automatically (with notice)
```

**Notifications** reuse the in-app notices and e-mail infrastructure (section 8.7): signature requested, signed by the other party, active (with the PDF), expiring soon, expired/terminated and access revoked.

**MCP:** agents may call `propose_nda(object, counterparty_org, data_classes)` (scope `nda:draft`) to prepare an instance in state `draft` and `get_nda_status(id)` (`nda:read`). No tool can send or sign; only humans sign.

### 6.6 Audit

Every cross-org event is logged (append-only, same pattern as `config/mcp_audit.jsonl`): grant created/revoked, document viewed/downloaded (with watermark id), RFQ sent, quote received, order status changed, approval given. Each org sees the audit of its own objects; admins see all.

### 6.7 Org verification and trust

A new org is `unverified` (can use engineering features, cannot send RFQs to many suppliers). Verification = admin check of domain e-mail + company registry number. Suppliers/manufacturers show a verified badge. No fees.

---

## 7. Manufacturing documentation

### 7.1 Pipeline

```
 machine description (machine/1.x) + system lock + mechanical data
        |
        v
 released design revision (immutable, approved)
        |
        +--> generators: lamination DXF/PDF, stator/rotor/shaft/housing drawings,
        |                winding specification, BOM, STEP, inspection plan
        +--> uploads from CAD (Fusion 360 etc.): drawings/STEP of parts not generated
        v
 document package (per revision; each file versioned, hashed, watermark on download)
        v
 RFQ / order attachments (section 8)
```

The drawing generator reads only the machine description and the system (never IPM parameters directly), so every geometry source (family plugin, DXF import, editor) gets drawings.

### 7.2 Generated vs uploaded

| Document | Generated by the portal | Uploaded from CAD |
|---|---|---|
| **Lamination DXF** (stator and rotor, 1:1, closed polylines, layer per role) | **yes, first** (contours already exist in the machine description) | optional override |
| **Lamination drawing PDF** (dimensions, material, thickness, burr side, stacking factor, tolerance class) | yes | optional |
| **BOM** (multi-level: motor → stator pack, rotor pack, magnets, winding, shaft, housing, bearings, fasteners, insulation) | **yes, first** (from materials, winding, bearing and catalog cards) | merged with CAD BOM for bought/structural parts |
| **Winding specification** (scheme, turns, parallel paths, wire/strip card, Y/Δ, insulation class, test voltages) | yes | — |
| **Magnet drawing/spec** (grade card, dimensions, magnetization direction, coating, tolerances) | yes | — |
| **Stator/rotor pack drawings** (stack length, skew, weld/bond, balancing grade) | yes (simple 2-D views) | often from CAD |
| **Shaft, housing, end shields, cooling jacket** | only parametric simple forms later | **mostly uploaded** (Fusion 360 via the existing parameter sync; STEP + PDF) |
| **STEP 3-D** | yes for laminations/packs/magnets (extrusion of the 2-D section) | full assembly from CAD |
| **Controller** (PCB Gerbers, BOM, enclosure) | BOM from the controller card | Gerbers/enclosure uploaded (PCB work lives in `Projects/Controller_CIANO14_40_60V`) |
| **Inspection / test plan** (bench tests vs the simulated passport) | yes (from the passport: KV, R, L, no-load loss, EMF) | — |

### 7.3 Revisions

- A **design revision** (`A`, `B`, …) = immutable snapshot `{machine description hash, system lock, card@revisions, mechanical data, passport run ids}`.
- States: `draft → in_review → released → superseded | obsolete`. Only `released` revisions can be attached to an RFQ or order.
- Each document in a package has its own file revision and a hash; the package lists `design_revision`, `generator@version` and the source files, so a drawing is always traceable to the simulation that justified it.
- The ERP already models `item_revision` with `draft/released/obsolete` and BOM on the revision: the portal revision maps 1:1 to an ERP `item_revision` for MOTRES products (section 8.6).

### 7.4 CAD round trip

- **Fusion 360:** the existing `/api/fusion` parameter CSV sync stays; uploaded STEP/PDF drawings attach to a design revision with a declared role (`shaft_drawing`, `housing_step`, …). The portal checks that key interface dimensions in the upload match the machine description (bore, OD, stack length) and warns loudly on mismatch.
- Other CAD: STEP AP242 + PDF upload; DXF for 2-D.
- Generated STEP is exported for the user's CAD so that housings are designed around the true electromagnetic parts.

### 7.5 Approval workflow

| Step | Who | Effect |
|---|---|---|
| Submit for review | engineer | revision `in_review`, documents frozen |
| Automatic checks | core | validation pipeline (4A.3), BOM completeness (every line resolves to a card or an uploaded part), interface-dimension check, passport present for the revision |
| Approve / reject with comments | approver(s), configurable 1 or 2 signatures | `released` or back to `draft` |
| Supersede | engineer + approver | new revision released; open RFQs/orders on the old one are flagged, never silently switched |

Approvals are recorded (who, when, what hash). No electronic-signature legal claims; it is an engineering approval record.

### 7.6 Drawing standards (options per org)

| Option | Values |
|---|---|
| Drawing rules | ISO 128 (presentation), ISO 129-1 (dimensioning), ISO 7200 (title block) |
| General tolerances | ISO 2768-1 (m/f/c/v) and ISO 2768-2 (H/K/L) |
| GPS / geometric tolerances | ISO 1101, ISO 8015 (independence) |
| Fits | ISO 286 (e.g. bearing seats k5/j6, housing H7) |
| Surface texture | ISO 21920 (Ra/Rz) |
| Projection | first angle (ISO) or third angle (ASME Y14 as an option) |
| Units | mm (default), inch as an option |
| Title block | org logo, part number, revision, material card, scale, sheet, approver |

### 7.7 Export formats

DXF (R2013+) and PDF for 2-D, STEP AP242 for 3-D, BOM as CSV/XLSX and JSON (ERP import), winding spec as PDF + JSON, package as a ZIP with a manifest (`package.json`: files, hashes, revision, generator versions).

### 7.8 File storage

Drawings and attachments are binary, versioned and access-controlled: an **S3-compatible object store** per region (MinIO on the Hetzner server first, same pattern the ERP plans for Phase 2), content-addressed by sha256, with metadata and grants in the portal database. Never in git; never in the public repo.

---

## 8. Sourcing and orders

### 8.1 Scope and boundary

The portal carries **documents, statuses and communication** between organizations: RFQs, quotes, purchase and production orders, delivery tracking, customer orders for finished products. **Payments, contracts and invoices happen outside the platform**; no money flows through it. MOTRES's own inventory, production, invoicing and shipping stay in `motres_erp` (section 8.6).

### 8.2 Supplier catalogues linked to catalog cards

A catalog card describes **what a thing is** (physics: a magnet grade, a wire, a bearing, a device). A **supplier offer** describes **who can deliver it**:

| Entity | Fields |
|---|---|
| `supplier_offer` | card@revision (or card family), supplier_org, supplier part number, pack/MOQ, lead time (days), incoterms, country of origin, datasheet file, `valid_to`, optional indicative price (visible only to the supplier and orgs it chooses; never public, never used by the platform for any charge) |

Card kinds with offers: `material` (electrical steel, sleeve materials), `magnet` grades, `wire`, `bearing`, `lubricant`, `device` (power semiconductors), `controller`, `gearbox`, `propeller`, `battery_cell/pack`, `coolant`, plus `service` cards for manufacturers (lamination laser/stamping/EDM, winding, machining, assembly, testing) with capability fields (max OD, thickness range, tolerance class). A card can have many offers; the catalog shows "N suppliers" and, when the user is allowed, their lead times.

### 8.3 Data model (portal side)

| Entity | Key fields |
|---|---|
| `org` | name, roles[], country, registry_no, verified, contacts |
| `membership` | org, user, role |
| `grant` | object, grantee, actions, scope, expires_at, nda_policy |
| `nda_policy` | parties, reference, validity, allows, watermark, download |
| `design_revision` | project, system lock, machine hash, state, approvals |
| `document` / `document_package` | revision, role, file (sha256), generator@version, file revision |
| `supplier_offer` | as 8.2 |
| `rfq` | owner_org, design_revision (optional), state, due_date, recipients[], lines[], package grants |
| `rfq_line` | card@rev or BOM line or document ref, qty, unit, needed_by, notes |
| `quote` | rfq, supplier_org, state, valid_to, lines (qty, lead time, optional price text), attachments, revision |
| `order` | type `purchase` or `production` or `customer`, buyer_org, seller_org, quote (optional), design_revision, lines, state, requested/confirmed dates, external refs (ERP numbers, buyer PO number) |
| `order_event` | order, state change, who, when, note, attachments (inspection report, CoC, shipping docs, tracking number) |
| `thread` / `message` | attached to rfq/quote/order; author, body, attachments, read receipts |
| `notification` | user, event, channel (in-app/e-mail), delivered_at |

### 8.4 State machines

**RFQ** (buyer side):

```
 draft --(approval if org requires)--> approved --send (human)--> sent
 sent --> quoting (≥1 quote received) --> awarded | closed_no_award | cancelled
```

**Quote** (supplier side):

```
 requested --> draft --send--> submitted --> (revised --> submitted)* --> accepted | declined | expired
```

**Order** (purchase, production or customer order):

```
 draft --approve--> issued (human sends) --> acknowledged --> confirmed (seller dates)
   --> in_production --> ready --> shipped --> delivered --> closed
 any open state --> on_hold | cancelled ; delivered --> disputed --> closed
```

Every transition is an `order_event` with who/when; the seller updates production/shipping states, the buyer confirms delivery. Dates slip visibly (confirmed vs requested). An order always pins the released design revision; a superseded revision flags the order, never changes it.

### 8.5 Customers ordering finished products

- MOTRES (or any manufacturer org) publishes a **product** from a released design revision: product card = datasheet + passport numbers (whitelisted), available variants (winding/voltage/cooling), indicative lead time.
- A customer org requests a quote or places a **customer order** for N units of product@revision (optionally with a customer-specific variant, which creates a design revision in a shared project under NDA policy).
- The seller confirms, the order moves through production/shipping states; bench test results per serial (from the ERP's measured-vs-simulated passport) can be shared with the customer as order attachments.
- Price, payment terms and invoices are agreed and exchanged outside the platform (the order carries only an external reference).

### 8.6 Integration with `motres_erp` (API, no duplication)

`motres_erp` (FastAPI + PostgreSQL, per-region stacks, Firebase Auth, accounting in Minimax) is MOTRES's **system of record** for items and revisions, BOM and routing, the double-entry stock ledger, lots and serials, suppliers, RFQs and purchase orders, receipts, customers, sales orders, production orders, shipping (DHL/FedEx) and invoicing via Minimax, plus `sim_passport` (measured vs simulated per serial, keyed by the geometry and physics fingerprints). The portal does **not** re-implement any of that.

| Flow | Direction | What |
|---|---|---|
| Released design revision → ERP item revision | portal → ERP | item code, revision, BOM JSON, document package link, passport fingerprints (`sim_passport` key) |
| Portal RFQ to an external supplier awarded (MOTRES as buyer) | portal → ERP | creates/updates ERP `rfq`/`purchase_order` draft; ERP remains the place that posts receipts and stock |
| Customer order to MOTRES confirmed | portal → ERP | creates ERP `sales_order` draft (and `production_order` if make-to-order) with the external reference |
| Status back | ERP → portal | production order progress, shipment + tracking number, delivered; mapped to `order_event` |
| Measured vs simulated | ERP → portal | per-serial bench results attached to the customer order and to the design revision's validation |
| Inventory / invoices | stay in ERP / Minimax | the portal shows at most "in stock / lead time" flags if MOTRES chooses to expose them |

Mechanism: a service account on each side, signed webhooks + idempotent REST calls, one mapping table (`portal_id ↔ erp_id`). The ERP stays private (not part of the AGPL portal code); only the connector in the portal is public, and it is optional (other manufacturers can use the portal without any ERP). Other orgs may later connect their own ERPs through the same connector interface.

### 8.7 Messaging and notifications

- **In-app threads** on each RFQ/quote/order are the record (audited, attachments versioned, visible to the parties only).
- **E-mail** is a notification channel: "you have a new RFQ / quote / status change" with a link; reply-by-e-mail is not parsed in the first version. Reuses the newsletter/e-mail infrastructure of PR #36 and the in-app notices; per-user notification settings.
- Daily digest option; admins see delivery failures in the Admin tab.

### 8.8 MCP tools for agents

A human always sends and approves; agents only read and draft.

| Tool | Scope | Does |
|---|---|---|
| `search_catalog(kind, filters)` | `catalog:read` | cards of any kind (existing plan) |
| `list_supplier_offers(card_id)` | `sourcing:read` | offers visible to the caller (lead time, MOQ; price only if the supplier shared it with the caller's org) |
| `get_design_revision(id)` / `list_documents(revision)` | `designs:read` | revision metadata and document list (whitelisted; no foreign geometry) |
| `generate_documents(revision, kinds)` | `designs:write` | queues the drawing/BOM generator for a **draft** revision |
| `draft_rfq(revision, lines, suppliers)` | `rfq:draft` | creates an RFQ in state `draft`; cannot send |
| `compare_quotes(rfq_id)` | `rfq:read` | table of quotes (lead time, terms, deviations) |
| `get_order_status(order_id)` | `orders:read` | state + events |

No MCP tool can send an RFQ, accept a quote, issue an order, release a revision or create a grant. Those need a signed-in human with the right member role.

### 8.9 Open RFQ / quotation board (update 2026-09-29)

Owner idea: a customer posts a request (CNC part, motor coils, magnets), suppliers quote, the customer picks the best offer and then works with the supplier directly. This extends 8.3–8.4 (the `rfq`/`quote` rows there become the entities below) and stays **non-commercial** (section 10): the platform takes no fee, handles no payments and is not a party to the deal. It hosts documents, statuses and communication.

#### 8.9.1 RFQ types and category templates

Each RFQ item uses a category template. The template lists required fields and validates them loudly before publishing.

| Category | Required inputs | Optional / typical |
|---|---|---|
| CNC / machined parts | drawing (PDF) + model (STEP AP242), material and condition, general tolerances ISO 2768 (class) and fits ISO 286 on toleranced features, surface finish (Ra), heat treatment, qty tiers (e.g. 10/100/1000) | coating/plating, inspection level (FAI, CMM report), marking, packaging |
| Laminations | DXF (from the released revision), steel grade and thickness, coating class, stacking method (interlock, welded, bonded, backlack), stack height and tolerance, qty tiers | burr limit, cutting method (stamping, laser, wire EDM with the recast note), annealing |
| Coils / windings | winding spec (generated, section 7), wire (type, size, strands), insulation class, impregnation (VPI, trickle, none), turns and connection, test requirements (resistance, hipot, surge, partial discharge), qty tiers | lead finishing, embedded sensors (NTC/PT1000), packaging |
| Magnets | grade (e.g. N52UH) and temperature class, dimensions and tolerances, coating (NiCuNi, epoxy), magnetisation direction (drawing), segmentation, **test report required** (B-H at temperature, flux per piece) | grain-boundary diffusion, pole marking, packaging for shipping magnetised |
| Bearings / electronics / other | part number or spec sheet, qty tiers, acceptable alternates | free-form spec with attachments |

Attachments are **generated from a released design revision** (section 7): the RFQ pins `design_revision@rev` and its package (drawings, DXF, STEP, winding spec, BOM lines). A superseded revision flags the RFQ, never changes it. Hand uploads are allowed and marked "uploaded" (7.2).

#### 8.9.2 Supplier capability profile and matching

Owner rule: every supplier states at registration what it can make, and receives RFQs by those criteria.

**Profile.** Filled when a supplier org registers; **required before it receives any RFQ** (invite or board). Editable, **versioned** (every change is a new profile revision; a quote pins the profile revision it was made under) and verifiable.

| Capability family | Structured parameters (per capability) |
|---|---|
| Machining: CNC milling, turning, 5-axis | materials, max part envelope (mm), achievable tolerance (ISO 2768 class, IT grade per ISO 286), best Ra |
| EDM: wire, sinker | max workpiece (mm), max height, tolerance, materials; lamination-stack EDM with recast note |
| Laminations: laser, stamping, etching | steel grades and thicknesses, max OD (mm), burr limit, die making in house |
| Stack joining: bonding (backlack), welding, interlock | stack height range, grades, annealing |
| Coil winding: concentrated, distributed, hairpin, litz, foil | wire sizes (min/max mm or AWG), strands, insulation classes, max coil envelope |
| Impregnation / potting | VPI, trickle, potting; resin systems; thermal class |
| Magnets | production vs trading, grades (e.g. N35–N55, H–AH classes, SmCo), max dimensions, coatings, magnetisation (in house, direction types), GBD, test reports offered |
| Bearings, PCB/PCBA, power electronics | product ranges, layer counts / IPC class, voltage/current range |
| Heat treatment, surface finishing | processes, max size, standards |
| Assembly, testing | assembly types; tests (hipot, surge, PD, B-H, dynamometer) |

Common to every capability: min/max quantity, typical lead time (days), capacity (units or hours per month, optional), certifications (ISO 9001, IATF 16949, AS9100, ISO 14001) with files and expiry, export regions served, languages. Optional: equipment list (machine, model, envelope), sample photos and case studies (watermarked, public only if the supplier chooses).

**Taxonomy.** Services and processes are classified with **UNSPSC** (segments 73 "Industrial production and manufacturing services" and 23 for machinery), products with **ECLASS** (the same property dictionary as 10B.4, D64), and the org itself carries its **NACE Rev. 2** code (ISIC-compatible) for statistics and registry checks only. Reason: UNSPSC is the only open, widely used code set that names manufacturing *services* at process level (machining, winding, heat treatment); NACE/ISIC classify companies, not what they can make; ECLASS is already our product dictionary. Our own capability ids map onto these codes; parameters stay ours where no standard property exists.

**Matching engine.** Each RFQ item's template (8.9.1) yields requirements; the engine compares them with capability profiles:

- **Hard constraints must match**: capability family and process, material/grade, envelope ≥ part, achievable tolerance ≤ required, required certifications valid on the deadline date, quantity within min/max, export region allowed, NDA policy acceptable.
- **Soft constraints are scored** (0–100): lead time vs needed_by, capacity, verification level, language, past performance (later, 8.9.6), distance/region preference.
- Board visibility and auto-invites go **only to suppliers passing all hard constraints**, ranked by score; each match stores its **reasons** (which constraints matched, which soft ones cost points).
- The customer can still **invite manually** any supplier (with a warning listing failed hard constraints). Suppliers see **why they received** an RFQ (the matched capabilities and parameters), and can mark "not a fit" to improve matching.

**Verification levels** (badges on profile and in the comparison matrix): **self-declared** → **documents checked** (certificates, registry extract checked by the platform or a verified customer) → **audited** (on-site or remote audit report uploaded by an auditor org). Each capability carries its own level; expired certificates drop the level automatically.

#### 8.9.3 Visibility

- **Invite-only**: the customer picks supplier orgs; only they see the RFQ.
- **Board**: published to **verified** supplier orgs (6.7) that pass the hard constraints of the matching engine (8.9.2); the board shows a summary only (category, qty tiers, region, deadline, no drawings).
- **NDA gate**: drawings and models become visible only after the supplier signs the RFQ's NDA policy (6.5A); the grant activates on signature and expires with the NDA or at award + N days.
- **Watermarking**: every viewed or downloaded file is stamped with the viewing org, user and date; download can be disabled (view only).
- **Region and export control**: the RFQ carries `region` and an export-control flag (10A); the board hides it from suppliers in disallowed jurisdictions, and controlled items are invite-only.

#### 8.9.4 Quotation

A quote is structured, not a free PDF:

- per item and qty tier: unit price, currency; tooling/NRE (one-off); lead time (days from order or from drawing approval); MOQ;
- Incoterms 2020 term and named place; validity date; payment terms as text (the platform does not process them);
- certifications (ISO 9001, IATF 16949, ISO 14001) with certificate files; material certificates offered (EN 10204 3.1);
- deviations/exceptions per item (explicit list; empty means "as specified");
- attachments (own drawings, process sheets).

**Q&A**: suppliers ask clarifications in the RFQ thread; the customer answers privately or **publicly**; a public answer is broadcast to all bidders, anonymised (the asker is not revealed). A material change of the RFQ (new revision, new deadline) notifies all bidders and marks existing quotes "may need revision". Suppliers may **revise** a quote until the deadline; all revisions are kept.

#### 8.9.5 Sealed bids, comparison, award

- **Sealed until the deadline** (default): bidders never see each other's prices or identities; the customer sees who has submitted but not the prices until the deadline. For invite-only RFQs the customer may choose "open as received".
- After the deadline, a **comparison matrix** per item and qty tier: unit price normalised to one currency (**rate source and date stated** on the matrix), tooling amortised over the qty tier, **landed-cost estimate** (price + Incoterms-dependent freight/duty estimate, marked as an estimate), lead time, MOQ, certifications, deviations and **risk flags** (single bid, first-time supplier, expired certificate, deviations present, validity ending soon).
- Actions: shortlist; ask **best-and-final** from the shortlist (one extra sealed round); **award** to one supplier or **split** items/quantities between suppliers; **decline** the others with a reason code (price, lead time, technical, other). Every bidder is notified of the outcome; declined bidders see the reason code, not the winning price.
- Cancel with a reason at any state before award; bidders are notified.

#### 8.9.6 After award

- Contact exchange between the two orgs (named contacts); optional **order document** in UBL (8, 10B.7) generated from the awarded quote, carrying the RFQ and quote references.
- Work continues **directly between the parties**: off-platform, or through the portal's order tracking (8.4, `order` linked to the award). MOTRES as buyer pushes the award to `motres_erp` as a PO draft (8.6).
- Later: **supplier performance notes and ratings** (on time, quality, communication) from awarded orders only, moderated, with the supplier's right of reply.

#### 8.9.7 Anti-abuse

- Only verified orgs publish to or bid on the board; unverified orgs can use invite-only RFQs.
- Rate limits on RFQ publishing, quotes and messages per org; **reporting** of RFQs, quotes and orgs, moderated in the Admin tab.
- No scraping: board listings paginated and authenticated, no bulk export, API scopes limited to the caller's own RFQs and invitations.
- **Audit log** of every view, download, NDA signature, quote submission and revision, award and decline (6.6).

#### 8.9.8 Data model

| Entity | Key fields |
|---|---|
| `CapabilityProfile` | supplier_org, revision, state (`draft`/`active`), languages, export_regions, NACE code, created_by, updated_at |
| `Capability` | profile, family + process id, UNSPSC / ECLASS codes, parameters (JSON per family schema), min/max qty, lead time, capacity, verification level |
| `Certification` | supplier_org, standard (ISO 9001, IATF 16949, AS9100, ISO 14001), certificate file, issuer, expiry, verification level |
| `Equipment` / `CaseStudy` | profile; machine, model, envelope / photos, description, public flag |
| `Match` | rfq_item, supplier_org, profile revision, hard pass (bool), score, reasons[], invited (auto/manual), supplier feedback |
| `Rfq` | owner_org, title, category, visibility (`invite`/`board`), design_revision (optional), nda_policy, region, export_control, display currency, deadline, sealed (bool), state, revision |
| `RfqItem` | rfq, category template id + filled fields, card@rev / BOM line / document refs, qty tiers[], needed_by |
| `Invitation` | rfq, supplier_org, state (`invited`/`nda_pending`/`nda_signed`/`declined_to_bid`), grant id |
| `Quote` | rfq, supplier_org, state, revision, Incoterms + place, validity, payment_terms_text, certifications[], attachments[], submitted_at |
| `QuoteLine` | quote, rfq_item, qty tier, unit_price, currency, tooling_nre, lead_time_days, moq, deviations[] |
| `Clarification` | rfq, asker_org (hidden from others), question, answer, visibility (`private`/`public`), answered_at |
| `Award` | rfq, rfq_item + qty (split allowed), quote, decline reasons for the others, FX rate source + date, order (optional), awarded_by |

#### 8.9.9 State machines

```
 RFQ:   draft --publish (human)--> published --first bid/NDA--> bidding
        bidding --deadline--> closed --award (human)--> awarded
        closed --best-and-final (shortlist)--> bidding (one round)
        any state before awarded --> cancelled

 Quote: draft --submit--> submitted --> (revised --> submitted)* --> accepted | declined
        submitted/revised --> withdrawn (before the deadline); past validity --> expired
```

#### 8.9.10 MCP

Agents may **draft** RFQs from a revision (`draft_rfq`, filling the category template), read clarifications and **compare** quotes (`compare_quotes` returns the normalised matrix and risk flags). Agents never publish, answer publicly, award or decline; those are human actions with the right member role (8.8).

#### 8.9.11 Roadmap

Part of **M10 (Sourcing)**, after M8 organizations and NDA signing: supplier capability profiles with taxonomy and verification levels + hard-constraint matching (~2 wk), category templates, invite-only RFQs and structured quotes first (~3 wk), then board visibility, sealed-bid comparison, best-and-final and split award (~2 wk), then performance notes (later, with moderation). Supplier profile page, job feed, milestones and two-way reviews (8.9.12) follow the board (~2 wk). Decisions D70–D82.

#### 8.9.12 Supplier side modelled on freelance marketplaces (update 2026-09-29)

Owner: "like freelancers". The supplier experience follows Upwork/Freelancer/Fiverr patterns adapted to manufacturing, still **non-commercial: no escrow, no fees, no payments through the platform**.

- **Public supplier profile page**: capabilities and parameters (8.9.2), certifications with verification badges, equipment, portfolio/case studies with photos, sample parts, regions served, languages, **median response time**, reputation metrics below. The supplier chooses what is public; drawings of customers never appear.
- **Job feed**: RFQs the supplier matches (board RFQs passing hard constraints, plus invitations), filters (category, material, qty, region, deadline), **saved searches** with in-app/e-mail notifications for new matching RFQs.
- **Proposals** = quotations (8.9.4) plus a short **cover note** (approach, similar parts made, questions).
- **Messaging per RFQ** (8.7 threads; public clarifications 8.9.4).
- **Milestones on an awarded job, status only, no money**: samples approved → first article inspection (FAI) → production → shipped → received. Each milestone has a due date, who confirms it (supplier sets, customer confirms samples/FAI/received) and attachments (FAI report, CoC, tracking). Milestones map onto the order states of 8.4.
- **Reputation only from completed platform jobs**: **two-way reviews** after completion (customer rates supplier: quality, on-time, communication; supplier rates customer: spec clarity, responsiveness, fairness), and **metrics computed by the platform**: on-time delivery % (received vs confirmed date), quality/NCR rate (non-conformances reported on received milestones), response rate and time on RFQs, repeat-customer share, jobs completed. Verification badges from 8.9.2.
- **Dispute / report flow**: either party opens a dispute on a job or review; moderators (Admin tab) see the thread and milestones, can hide a review that breaks the rules or annotate it; no financial arbitration (money is outside the platform).
- **Anti-gaming**: reviews only on **awarded and completed** jobs, **one review per side per job**, **blind** (visible after both sides submit or after a 14-day deadline), no editing after publication (a reply is allowed), jobs between orgs sharing members or owners are excluded from metrics, outlier and burst detection flags for moderation.

Data model additions (8.9.8): `SupplierPage` (profile revision, public fields, response-time stat), `SavedSearch` (user, filters, channel), `Proposal` = `Quote` + `cover_note`, `Milestone` (award/order, kind, due, state, confirmed_by, attachments), `Review` (job, author_org, subject_org, scores, text, submitted_at, published_at, hidden_by_moderator), `ReputationMetric` (org, metric, value, window, computed_at, job count), `Dispute` (job or review, opened_by, state, moderator notes).

---

## 9. UI and agents

### 9.1 Screens

| Screen | Content |
|---|---|
| **Project** | systems, scenarios (duties), results, members, design revisions |
| **System builder** | canvas: drag modules from the catalog, connect ports; incompatible ports do not connect (type + units); operating-point summary on each link (T, n, V, I, Q) |
| **Module page** | manifest in plain words, cards, badges, validation, compute cost (time, not money) |
| **Catalog** | one search/filter/compare across all card kinds, source for every number, supplier offers |
| **Design revision** | document package, approvals, checks, "send RFQ" |
| **Sourcing** | RFQs, quotes, orders, threads, status timeline |
| **Organization** | members, roles, grants, NDA policies, audit, compute nodes |
| **Today's tabs** (Motors, Simulation, Controller, Thermal) | stay as the "module page" of motor/controller: deep configuration of one node |

The "no text walls" rule stays: one line + tooltip.

### 9.2 MCP (engineering tools)

| Tool | Scope | Does |
|---|---|---|
| `list_modules`, `search_catalog(kind, filters)` | `catalog:read` | modules and cards of any kind |
| `build_system(nodes, links)` | `systems:write` | draft system (like `start_design` for a motor), port checks |
| `simulate_system(system_id, scenario)` | `systems:simulate` | queues a job, fair-use quotas as `simulate` |
| `get_system_result(id)` | `systems:read` | port values, losses, temperatures through the whitelist |

A customer agent works like this: "need a 25 kg drone, 40 min": `search_catalog` propellers/batteries → `build_system` → `simulate_system` on maps → pick 2–3 variants → queue the exact solve → (after a human releases the design) `generate_documents` → `draft_rfq`. Everything runs under the owner's key with his rights, quotas and audit, as today.

---

## 10. Licence, compute and non-commercial operation

- **Licence:** platform code is AGPL-3.0-or-later; contributions under the DCO (`git commit -s`), no CLA (PR #40). Solver components with non-commercial licences are optional (e.g. `triangle` removed, `pypardiso` optional).
- **Private split (PR #41):** ANSYS cross-checks, customer case data and NDA material live in a private repository; the portal's runtime data (orgs, NDAs, drawings, RFQs) lives in the database and object store, never in git.
- **No money in the platform:** no pricing, tiers, billing, revenue share, listing fees or paid features. Roles are `user` and `admin`; limits are fair-use.
- **BYO compute (PR #44):** users attach their own Linux nodes (pull model, `mcnode_` tokens, owner-only leasing, signed job bundles, same solver code, results with provenance, `own_node` flag in usage). Stage 3 of that plan adds org-shared nodes, which fits section 6 directly (a node owned by an org leases jobs of its members). FMU/foreign code runs only on nodes that opt into the sandbox profile.
- **AGPL and network use:** because the portal is offered over a network, users are entitled to the source of the running version; the footer links the exact commit.

---

## 10A. Customer data protection and data residency

Owner (2026-09-29): customer data must be stored with great care, and it must be possible to separate it by country in the future. This section sets the rules now so that organizations (M8), the object store (D30) and BYO compute do not have to be retrofitted later. The current state was audited the same day (read-only; `data_protection_audit_2026-09-29.md`); its findings drive the "NOW" list in 10A.10.

### 10A.1 Data classification

Every stored object carries one class. Class decides encryption, who may see it, whether it may leave its region and how long it is kept.

| Class | Examples | Rules |
|---|---|---|
| `public` | published catalog cards, marketing site, AGPL source | may be cached anywhere (CDN) |
| `internal` | platform logs without PII, metrics, shared materials library | platform staff only; region-pinned where it contains customer references |
| `customer-confidential` (default for customer objects) | geometry, machine descriptions, duties, results, fields, reports, drawings, BOMs, RFQs | org members with grants only; encrypted at rest with the org key; never leaves the org's region without a policy |
| `nda-restricted` | anything shared under an NDA policy (6.5) | as confidential + NDA gating, watermark, download rules, full access audit |
| `personal` (GDPR Art. 4) | account e-mail, name, sessions (IP, user agent), auth events, newsletter consent, support tickets and chats, MCP audit, usage stats | minimised, purpose-bound, retention schedule, subject rights automated; IPs stored hashed with a rotating salt or truncated |

Export-controlled technical data (see 10A.4) is a **flag** on top of the class (`export_control: none | eu_dual_use | ear | itar_suspected`), set by the owning org, never inferred by the platform.

### 10A.2 Tenancy and region as first-class attributes

- **Tenant = organization** (D24). Every object has `org_id` and `region`; personal data of a user belongs to the user's home region.
- **`region`** is set when an org is created (`eu` first, the current Hetzner FSN1 server; later e.g. `us`, `apac`, `cn`), is immutable except through a documented migration job, and is copied onto every object, job, file, backup and log line that references customer data.
- **Region-pinned storage, compute and backups:** each region has its own database, object store bucket (MinIO, D30), job queue, solver nodes, backup repository and key hierarchy. No shared database across regions; the global layer holds only the org directory (`org_id → region`, display name) and public catalog data.
- **Region router in the API:** the global entry point (aerostator.com) authenticates, looks up the caller's org region and routes (or redirects) to the regional API (`eu1.…`, `us1.…`). A request that would read an object of another region fails closed unless a cross-region grant exists. In the single-region phase the router is a no-op check (`region == "eu"`) that is already present in every store call, so the second region is configuration, not a rewrite.
- **Cross-region transfer only by explicit policy:** a `transfer_policy` object (source region, destination region, data classes, legal basis, approver, expiry) must exist and be approved by the data owner's org owner; every transfer is audited.

### 10A.3 Legal frame per region (awareness, not legal advice)

- **EU/EEA (GDPR):** MOTRES d.o.o. is controller for accounts and platform data, processor for customer engineering data. Transfers out of the EEA need Chapter V basis: adequacy decision (e.g. EU–US Data Privacy Framework for certified recipients) or Standard Contractual Clauses plus a transfer impact assessment.
- **China (PIPL, Data Security Law):** personal information and "important data" of Chinese users should stay in a `cn` region operated with a local partner; export needs a security assessment, certification or the standard contract. A `cn` region is a separate deployment, not a bucket in the EU.
- **US / export control:** motor, drive and drone propulsion technology can fall under EU Dual-Use Regulation 2021/821, US EAR (ECCN) and in edge cases ITAR. The platform does not classify; it lets an org flag objects, blocks sharing flagged objects to orgs in embargoed countries or on sanctions lists, and records who accessed what. A US region may be needed for customers who require US-person-only storage.
- Counsel reviews the DPA, the SCCs and the export-control wording before the first external customer org and before any second region.

### 10A.4 Encryption

- **In transit:** TLS 1.2/1.3 only, HSTS with `includeSubDomains` on every portal host (already on emotres.com), internal traffic between regional services on a private network or mTLS; BYO nodes and remote modules use mTLS (3c).
- **At rest, layer 1 (disk):** LUKS full-disk encryption on every server that holds customer data (today the EU server has **none**, see audit). Remote unlock (dropbear-initramfs or Tang/Clevis) so reboots stay unattended.
- **At rest, layer 2 (per-org envelope keys):** each org has a data key (DEK) that encrypts its objects in the object store and its confidential DB columns; DEKs are wrapped by a regional key-encryption key (KEK). First implementation: KEK in a root-only file on the regional host, separate from backups; later a KMS (HashiCorp Vault/OpenBao on a separate host, or a cloud KMS in that region). Deleting an org's DEK = crypto-shredding its data, including copies inside backups.
- **Key rotation:** KEK yearly and on staff change; DEKs re-wrapped (not re-encrypted) on KEK rotation; `AUTH_SECRET`/session signing keys with a key id (`kid`) so rotation does not sign everyone out; SMTP, LLM and backup credentials rotated at least yearly and on suspicion.

### 10A.5 Backups

- **Per region, never across regions**; the EU backup target is a Hetzner Storage Box in the EU (the backup unit is written for it but currently points at a local repository on the same disk).
- **Encrypted** (restic repository key, stored outside the backed-up host as well, in the owner's password manager), **off-site** (different machine, ideally different Hetzner location), hourly/daily/monthly retention as today (24/30/12).
- **Restore tested:** quarterly restore rehearsal into a scratch VM, with a written result; automated weekly `restic check --read-data-subset`.
- **Deletion and backups:** an erasure request is honoured in live data at once; backup copies age out within the retention window (12 months max) and are never restored for the deleted subject (a restore replays the deletion log). With per-org keys, deleting the DEK makes backup copies unreadable immediately.

### 10A.6 GDPR operations

- **Records of processing (Art. 30):** one maintained register (purpose, data, subjects, recipients, retention, transfers) in the private repository; updated with every new feature that stores personal data.
- **DPA template** for customer orgs (Art. 28), with the sub-processor list as an annex and 30-day notice of changes.
- **Sub-processor list (current):** Hetzner (hosting, backups; DE), Google Workspace (SMTP mail, Google sign-in; US parent, DPF/SCCs), Cloudflare (DNS; US parent), Firebase/Google (marketing site hosting), GitHub (source code; no customer data), Anthropic and Google Gemini (support assistant, if customer text is sent). Each needs a signed DPA and a transfer basis; LLM calls must not carry customer-confidential data unless the org opts in.
- **Data subject rights automated:** `GET /api/account/export` (ZIP of account record, sessions, events, tickets, workspace) and `DELETE /api/account` (account, sessions, workspace, published items or their transfer, tickets, newsletter consent, pseudonymised audit entries), both logged; admin tool for requests received by e-mail. Answer within one month.
- **Retention schedule:** sessions 30 days after expiry; auth events 12 months; API access logs 30 days (no bodies, no tokens); MCP audit and admin audit 24 months; support tickets 24 months after closure; newsletter consent until withdrawal plus proof for 3 years; usage stats aggregated after 90 days; deleted accounts purged from live data in 30 days, from backups by retention.
- **Breach process:** detection → owner notified at once → assessment → supervisory authority (Slovenia: Information Commissioner) within 72 h if a risk to persons exists → affected customers/subjects without undue delay; written runbook and an incident log.

### 10A.7 Admin access

- Least privilege: platform `admin` sees account metadata and can manage grants, but **does not read customer-confidential content by default**. Reading a customer workspace needs a **break-glass** action: reason, time-boxed (e.g. 1 h), optional customer consent, logged to an append-only audit and shown to the org owner.
- Every admin action (tier/role change, grant, invite, delete, session revoke, support read) goes to the admin audit with actor, target, time and reason.
- Server access: named SSH keys per person (no shared root key), `root` login disabled in favour of a named sudo user, SSH from allow-listed addresses or a VPN, fail2ban, hardware-key (FIDO) SSH keys for the owner. Users in the `docker` group are root-equivalent and count as admins.

### 10A.8 BYO compute, vendor modules, MCP and agents

- **Data minimisation:** a job bundle carries only what the solve needs (machine description, duty, materials used); no account data, no other objects; results come back signed; the node deletes the bundle after the job (policy + attestation in the lease protocol).
- **Region rule:** a platform node belongs to one region; a BYO node declares its country; the scheduler never sends a job of a region-pinned object to a node outside the allowed countries unless the owning org's `transfer_policy` allows it. Export-flagged objects never go to foreign vendor modules.
- **Vendor modules** receive port values only (D23, 5.3), never geometry.
- **MCP/agents** act with the calling user's grants and region, never the platform's; tools cannot cross orgs or regions; MCP audit stays in the region.

### 10A.9 Logging without secrets or PII

- Structured logs; a redaction filter drops `Authorization`, cookies, tokens, passwords, API keys and form bodies; e-mails in logs are replaced by `user_id`; IPs hashed or truncated in application logs (raw IPs only in the web server log, 14 days, for abuse handling).
- Logs are region-pinned and follow the retention schedule; log files are not world-readable.

### 10A.10 Roadmap placement

| When | What |
|---|---|
| **NOW (before more external customers)** | point backups to the off-site Storage Box and test a restore; LUKS on the EU server at the next maintenance window (or a second server with LUKS and migrate); tighten file modes (identity, logs, backups 600/700); account deletion that removes workspace, sessions, tickets, newsletter entry; account export; admin action audit; retention jobs (sessions, logs, auth events); privacy notice + sub-processor list + DPAs signed with Hetzner, Google, Cloudflare; breach runbook; remove the `erp` user from the `docker` group or treat it as admin; named SSH keys, root login off; `region="eu"` field on users/workspaces/jobs from now on |
| **With M4/M8 (organizations)** | `org_id` + `region` + `class` on every object; per-org DEKs in the object store; region check in every store call (single-region router); break-glass admin access; DPA template for customer orgs; transfer_policy object; export-control flag; BYO/job scheduler honouring region and flags |
| **Later (second region)** | global org directory + region router; regional deployments (DB, MinIO, queue, nodes, backups, keys); KMS; region migration job; SCC/TIA package; `cn` only with a local partner and a separate deployment |

### 10A.11 Risks added

| Risk | Mitigation |
|---|---|
| Server theft/decommissioned disk exposes all customer data | LUKS, per-org keys, Hetzner disk wipe on cancel |
| Backup on the same disk: one failure loses data and backups together | off-site Storage Box, restore rehearsals |
| Customer data crosses a border without basis | region attribute everywhere, router, transfer_policy, node country check |
| An admin or agent reads customer designs silently | break-glass with audit visible to the org owner; MCP with caller's grants only |
| Export-controlled design shared to a sanctioned party | export-control flag, country checks on sharing and on BYO nodes |

---

## 10B. Open standards

Owner principle (2026-09-29): **the portal must be compatible with open standards.** Every interface where data leaves or enters the portal maps to a published standard; our own formats (`machine/1.x`, the port contract of 2.7, card schemas) are profiles of such a standard or are exported to one. Status legend: **Adopted** = in use now or a fixed convention; **Planned Mx / step n** = enters with that roadmap stage (11.3); **Not adopted** = considered and deliberately left out, with the reason.

### 10B.1 Principles

1. **Open on the boundary, free inside.** Internal storage may stay our own JSON/tables; everything crossing an org boundary, an API or a file download has an open-standard form.
2. **Import is wider than export.** We export only formats we pass a conformance test for (10B.9); the rest is "import, best effort".
3. **Our contract is the source of truth; the standard is the wire.** The port contract (2.7) uses Modelica connector semantics and is exported as FMI/SSP; it is not redefined by them.
4. **Freely implementable wire formats.** Paywalled ISO/IEC documents are followed as conventions; the machine-readable formats we require must be implementable without licence fees.

### 10B.2 Models and co-simulation

| Interface | Standard | Import / export | Status | Notes |
|---|---|---|---|---|
| Physical port semantics | Modelica connector semantics (across/through, flow sum = 0, `stream` for coolant enthalpy) | internal contract | **Adopted** (2.7, D57–D60) | Semantics only, no Modelica compiler in the portal |
| Reduced models of our modules (motor L0 maps, controller loss/T_j model, thermal RC networks) | **FMI 3.0** | **export** FMU | **Planned step 3b** (motor L0 map FMU may come right after P5) | **Model exchange** for smooth ODE models (thermal networks, L0 maps): the importer's solver integrates them. **Co-simulation** for models with their own stepping or internal state (PWM, FEM-backed, state machines) |
| Third-party models (flight controller, gearbox, battery, vehicle) | **FMI 3.0** (FMI 2.0 accepted) | **import** FMU into the isolated executor (5.2, 5.3) | **Planned step 3b / 4.7** | Binary FMUs run only in the sandbox |
| System graph and parameter sets | **SSP 2.0** (SSD structure, SSV parameter values, SSM mappings) | import + export | **Planned step 2** (first export: the P5 battery–controller–motor–load system) | `.ssp` archive carries the FMUs; SSV = parameter set of a configuration/duty |
| Embedded controller code from models | eFMI | — | **Considered, not adopted now** | Revisit only if the controller module generates target code |
| Model source | Modelica `.mo` | — | **Not adopted** | Needs a Modelica tool chain; FMI/SSP cover exchange |

### 10B.3 Geometry and drawings

| Interface | Standard | Import / export | Status | Notes |
|---|---|---|---|---|
| 3-D parts and assemblies with PMI | **STEP AP242** (ISO 10303-242) | import + export | Import **planned "Geometry, further"**; export **planned M9b** (laminations, packs) | AP203/AP214 accepted on import; semantic PMI where the generator knows tolerances |
| Laminations and 2-D profiles | **DXF** (ASCII, R2013+) | export + import | Export **adopted** (lamination DXF); import **planned 1A** | Units and layer convention published with the export |
| Drawings | **ISO 128** (presentation), **ISO 129-1** (dimensioning), **ISO 1101** (GD&T), **ISO 2768** (general tolerances); PDF/A for archive | export PDF + DXF | **Planned M9** (7.6 options per org) | ASME Y14.5 as an org option |
| 3-D viewer | **glTF 2.0** (`.glb`) | export (server tessellation) | **Planned M9b** | Display only, never the manufacturing master |
| JT, IGES | ISO 14306, IGES | import only | **Considered, import on demand** | Not exported: legacy / weak open tooling |
| Native CAD | Fusion Parameter I/O CSV | both | **Kept as convenience** | STEP is the exchange of record |

### 10B.4 Component data and catalogs

| Interface | Standard | Import / export | Status | Notes |
|---|---|---|---|---|
| Catalog field definitions (magnet, steel, wire, semiconductor, bearing…) | **IEC 61360 / IEC CDD**, **ECLASS** IRDIs | each card field carries an optional `irdi` | **Planned M4** (data model) | Meaning, unit, datatype from the dictionary; own fields allowed but marked |
| Product and component data with suppliers and customers | **Asset Administration Shell** (IEC 63278-1, IDTA): **Digital Nameplate**, **Technical Data**, **Carbon Footprint** submodels; AASX + AAS REST API | import (supplier components) + export (released products) | **Planned M10 (import) / M12 (export)** | Motor datasheet → Technical Data submodel with IEC CDD properties; serial data per product |
| Motor ratings | **IEC 60034-1** (ratings, duties S1–S10), **60034-2-1** (efficiency), **60034-30-1** (classes) | datasheet convention | **Adopted** as convention | We say "simulated, duty S3 per 60034-1", never "rated" without a test |
| Magnetic materials | **IEC 60404** (-8-1 magnets, -8-4/-8-7 electrical steel) | card convention | **Adopted** as convention | Measurement standard recorded in provenance |
| Semiconductors | **IEC 60747 / 60749**, **JEDEC** (JESD51 thermal) | card convention | **Adopted where applicable** (Controller device cards) | R_th per JESD51 |

### 10B.5 Units, data and APIs

| Interface | Standard | Status | Notes |
|---|---|---|---|
| Units | **SI** (2.7.1); **UCUM** codes in every API field that carries a unit | SI **adopted**; UCUM **planned M0** | K at the port boundary (D3) |
| Payload contracts | **JSON Schema 2020-12** for cards, manifests, ports, results | **Planned M0** | Versioned with card kinds and `machine/1.x` |
| REST API | **OpenAPI 3.1**, generated and published per release | **Planned P2/M0** | Linted and diffed in CI |
| Agent tools | **MCP** | **Adopted** (9.2) | Tool input schemas = the same JSON Schemas |
| Results | **CSV** (small tables), **Parquet** (maps, sweeps), **HDF5** (fields, waveforms, meshes) | **Planned M5–M7** | Internal pickles never leave the server |
| Provenance | **W3C PROV** concepts (Entity / Activity / Agent), PROV-JSON export | Model **planned M0**, export later | Result = Entity, solve = Activity, user/agent/module = Agent |
| Time, ids | ISO 8601 / RFC 3339 (UTC), UUID | **Adopted** | — |

### 10B.6 Identity, security, licences

| Interface | Standard | Status | Notes |
|---|---|---|---|
| Login and API auth | **OpenID Connect**, **OAuth 2.1** (authorization code + PKCE) | OIDC **adopted** (Google); OAuth 2.1 for the public API **planned M4** | The portal stores no passwords |
| Service to service | mTLS, TLS 1.3 | **Planned 3c** | — |
| Licences | **SPDX licence identifiers** in manifests and file headers | **Planned M0** | `AGPL-3.0-or-later`; vendor modules declare theirs (5.1) |
| SBOM | **CycloneDX** (primary) or SPDX SBOM per release, portal and every module image | **Planned P2** | Generated in CI, stored with the release |
| Signatures on NDAs and approvals | **eIDAS** (simple/advanced; qualified only on request), PAdES signed PDFs | **Planned M8** (6.5A) | Legal effect is the parties' responsibility |

### 10B.7 Manufacturing and ERP exchange

| Interface | Standard | Status | Notes |
|---|---|---|---|
| RFQ, quote, order, despatch documents | **UBL 2.x** document types; **Peppol BIS** only where a counterparty requires it | **Planned M10–M11** | Document exchange only; the portal stays non-commercial, invoicing stays in `motres_erp` |
| BOM | STEP AP242 assembly structure + CSV | **Planned M9** | — |
| Inspection plans and results | **QIF 3.0** (ISO 23952) | **Optional, M9b** | Measured vs nominal feeds the passport |

### 10B.8 Messaging and IoT (test benches, later)

| Interface | Standard | Status | Notes |
|---|---|---|---|
| Live bench data | **OPC UA** and **MQTT 5** (Sparkplug B payloads) | **Planned M11 / Later** | Import only, mapped to the port variables of 2.7.3 to compare simulated vs measured; the portal never commands a bench |
| Offline bench data | CSV / Parquet / HDF5 with UCUM units; ASAM MDF4 on import | **Planned M11** | — |

### 10B.9 Conformance tests (CI, per release)

| Standard | Test |
|---|---|
| FMI 3.0 export | `fmpy validate` + simulation in fmpy; modelDescription against the FMI XSD; results in FMI Cross-Check layout |
| FMI import | Modelica Association Reference-FMUs run in the executor |
| SSP 2.0 | SSD/SSV against the SSP XSDs; export → import round trip gives the identical system graph |
| STEP AP242 | Re-import (OCCT) with identical volume and mass; syntax check; PMI checked where emitted |
| DXF | Re-import (ezdxf): closed contours, units flag, area = solved region area |
| glTF | Khronos glTF-Validator, zero errors |
| AAS | AASX validated (aas-core / Eclipse BaSyx); submodels against IDTA templates |
| JSON Schema, OpenAPI | Golden examples validate; OpenAPI linted (Spectral) and diffed for breaking changes |
| UCUM | Every unit string parses with a UCUM library |
| SBOM | CycloneDX / SPDX validators |
| UBL, QIF | XSD validation of exported documents |

### 10B.10 Deliberately not adopted (now)

- **Modelica source exchange / in-portal compiler**: FMI + SSP cover exchange at a fraction of the cost.
- **eFMI**: only if the controller module ever generates embedded code.
- **JT / IGES export**: legacy or weak open tooling; STEP AP242 suffices.
- **Peppol e-invoicing, payments**: the portal is non-commercial; commercial documents stay in `motres_erp`.
- **Native CAD formats as exchange of record**: Fusion CSV remains a convenience.

## 11. Risks, decisions, roadmap

### 11.1 Risks

| Risk | Mitigation |
|---|---|
| The restructure breaks L155/L180/L13 numbers | bit-identical golden tests before work starts; adapters call old code |
| Over-architecture: a year on the core, zero modules | strict order: M0–M3 → propeller on maps → only then FMU; M8+ only after M3 is accepted |
| Wrong contract chosen after vendors join | `contract: portal/1.x`; a major version = parallel support of two versions for 12 months |
| Foreign code breaks/loads the server | isolated nodes only, limits, never in the API process, never on the owner's workstation |
| Our geometry leaks to a vendor or supplier | modules get ports only; suppliers get released packages only through scoped grants, watermarks, audit |
| Bad vendor data hurts reputation | `draft` until validated; badges; "vendor model" labelled in reports |
| One system becomes slow (many FEM runs) | maps by default, FEM only at the converged point |
| Sourcing duplicates the ERP and the two drift | ERP is the system of record for MOTRES; portal stores only cross-org documents/statuses + external refs; one mapping table |
| A drawing does not match the simulated machine | generator reads the same machine description; package pins the revision hash; interface check on CAD uploads |
| An agent sends something on its own | MCP tools stop at `draft`; send/accept/issue/release need a human member role |
| Legal exposure from documents (NDA, drawings) | NDA-policy gating, watermarking, audit, private file store; platform makes no contract claims |
| Non-commercial load grows beyond hardware | fair-use limits + BYO compute |

### 11.2 Decisions for the owner

D1–D23 from v2 (D9 restated; D12 was never assigned):

| # | Decision | Recommendation |
|---|---|---|
| D1 | Build the system/port level on top of `modules/` + `contracts/` or from scratch? | **On top**: extend `ModuleManifest`, keep the skeleton |
| D2 | Power sign on ports (restated 2026-09-29) | **"Positive = into the module"** for every port (Modelica convention, across/through pairs); node Σ through = 0; balance per module with storage (2.7) |
| D3 | Internal units (restated) | **Pure SI at the new boundary (K, m, rad/s)**; engineering units only in UI/reports/legacy files; explicit, tested conversions in adapters; old solver arithmetic untouched |
| D4 | Organizations now or at the first vendor? | **Now, minimal** (see D24) |
| D5 | Die/cfg/duty → project/system/scenario: rename data? | **No.** New entities beside; cfg gets an implicit system; files untouched |
| D6 | Third module for the pilot | **Propeller on maps** (C_T/C_P): first "drone" system; gearbox second |
| D7 | Foreign model format | **Maps → FMU 3.0 (co-sim) → remote service**, in that order |
| D8 | Where foreign code runs | **Only separate Linux nodes in containers** (Hetzner or opted-in BYO), never in the API, never locally |
| D9 | Vendor model at start (restated) | **Non-commercial:** vendors publish modules/cards for free under AGPL (code) and a per-card data licence; no listing fees, no revenue share |
| D10 | Who may view/download a vendor card | default **`view_only`** (results only); download at the vendor's choice |
| D11 | "Migration done" criterion (restated) | Track A (adapters): **bit-identical L155 motor, L180 gen, L13** through the new path, timestamps/ids excluded; otherwise no merge. Numerical changes (e.g. gmsh) are track B with their own tolerances (2.7.9, D61) |
| D13 | Object dynamics and environment: core or modules? | **Modules** (`vehicle`, `environment`, propulsors) with the same contract; the core knows only time, state and ports |
| D14 | State (SoC, thermal network, body) and `series` form in the contract now? | **Yes, in M0**, even if the first implementation is scalar |
| D15 | Mission fidelity | **L0/L1** (maps + thermal networks); FEM builds and checks maps, re-check of 3–5 worst points |
| D16 | First mission | **Quadcopter hover** on our motor+controller maps + propeller map + battery |
| D17 | Motor map grid | T × n × V_dc × (T_winding, T_magnet): night runs (night-campaign rule), never by day on the live API |
| D18 | Where the system builder lives: aerostator.com or emotres.com? | per the 2026-09-28 domain strategy: **aerostator.com = application/portal** (system builder here), emotres.com = site and shop; one API |
| D19 | Customer's own geometry | **Yes, in the structure now**: common `machine/1.0` written by every source (4A) |
| D20 | DXF import + materials + solve | **Early simple milestone after M0/M1**, not the first priority |
| D21 | Step-by-step editor | **Later**, on the same machine description and validation |
| D22 | New topologies (SPM, outrunner, axial, IM, SynRM, EESM) | **Generator plugins**, one at a time on demand; core and solvers unchanged |
| D23 | Access to customer geometry | **`private` by default**, grants; MCP never returns foreign geometry; owners export their own |

**New decisions (v3):**

| # | Decision | Recommendation |
|---|---|---|
| D24 | Org model: now or later? | **Now, in M4**: `org`, `membership`, `grant` tables; every user gets a personal org; multi-role orgs; UI only for members and grants at first. Retrofitting orgs after RFQs exist would mean migrating every object's owner |
| D25 | User roles vs org roles | Platform roles only **`user` / `admin`**; everything else is an org membership role (`owner/approver/engineer/buyer/sales/viewer`); legacy `free/pro/team` removed |
| D26 | First scope of the drawing generator | **Lamination DXF + PDF and the multi-level BOM first** (M9), then winding spec and magnet spec; shaft/housing drawings stay CAD uploads; STEP of laminations/packs after |
| D27 | Drawing standards default | **ISO** (ISO 128/129/7200, ISO 2768-mK, ISO 286 fits, first-angle, mm), per-org switchable to ASME/third-angle |
| D28 | ERP integration boundary | **ERP = MOTRES's system of record** (items, BOM, stock, lots/serials, POs, receipts, sales/production orders, shipping, invoices via Minimax). Portal = cross-org documents, statuses, messages; syncs by API with external refs; never stores stock or invoices |
| D29 | Messaging: e-mail or in-app? | **In-app threads are the record; e-mail only notifies** (link back). No reply-by-e-mail parsing in v1 |
| D30 | File storage for drawings and attachments | **S3-compatible object store per region (MinIO on Hetzner)**, content-addressed (sha256), metadata + grants in the DB; never git |
| D31 | Who may send RFQs/orders and release designs | **Humans only**, with `buyer`/`approver` roles; configurable two-signature release; MCP tools stop at `draft` |
| D32 | NDAs | **Data-access policies** (parties, validity, data classes, watermark, download) that gate grants; the platform stores the reference/PDF privately and makes no legal claims |
| D33 | Supplier prices | **Optional free text visible only to the parties** the supplier chooses; the platform never computes, aggregates or charges on prices |
| D34 | Revision model | **Design revision A/B/… = immutable snapshot**; only `released` revisions go to RFQs/orders; supersede flags open orders, never switches them; maps 1:1 to ERP `item_revision` for MOTRES products |
| D35 | Supplier/manufacturer onboarding | **Verified orgs** (admin check of domain + registry no.); unverified orgs can engineer but not broadcast RFQs |
| D36 | Customer orders of finished products | From a **released product card** only; price/payment/contract outside; order carries external refs; per-serial bench results can be shared as attachments |
| D37 | Order of the new stages (restated: only after P1–P5, D62) | **After M0–M3 are accepted bit-identical** and M4 (orgs) is in: M8 parties UI → M9 drawings + BOM → M10 sourcing → M11 ERP connector → M12 customer orders; engineering steps 2–4 continue in parallel |
| D38 | Automatic NDA signing | **Yes, in M8**: generated from org templates, signed in the portal; the platform supplies the mechanism, not legal advice; templates reviewed by counsel before use |
| D39 | Signature level | **eIDAS SES now** (click-to-sign, account identity, re-auth/2FA, audit trail); **AdES/QES later** through an external qualified trust-service provider API, only when a party requires it |
| D40 | Default template | **Mutual NDA** (MOTRES template, lawyer-checked) as the platform default; one-way and org-specific templates optional, versioned |
| D41 | When access starts | **Only after all required signatures**; before that the recipient sees title and owner only |
| D42 | Expiry and termination | **Auto-revoke all grants** on expiry/termination, notices at 30 and 7 days and on revocation |
| D43 | 2FA at signing | **Re-authentication always; 2FA when enabled**, and an org may make 2FA mandatory for its signatories |
| D44 | Agents and NDAs | Agents may **propose drafts only** (`nda:draft`); sending and signing are human-only |
| D45 | Existing paper NDAs | **Manual upload** accepted as an equivalent access policy after both org owners confirm |
| D46 | Tenancy and region | **Organization is the tenant; `region` is a first-class, immutable attribute of every org and every stored object**; EU (Hetzner FSN1) is the only region now |
| D47 | Region isolation | **Separate regional deployments** (DB, object store, queue, nodes, backups, keys); global layer only for the org directory and public catalog; a region check in every store call from M4 on |
| D48 | Cross-region transfer | **Only through an approved `transfer_policy`** (classes, legal basis, approver, expiry), audited; default deny |
| D49 | Encryption at rest | **LUKS on every data server now; per-org envelope keys (DEK/KEK) with M8**; KMS when the second region starts; crypto-shredding on org deletion |
| D50 | Backups | **Per region, encrypted, off-site, restore rehearsed quarterly**; retention 24 h / 30 d / 12 mo; deletions replayed after any restore |
| D51 | Data subject rights | **Self-service export and delete** in the account page before more external users; admin tool for e-mailed requests |
| D52 | Retention | **Adopt the schedule in 10A.6** and enforce it with daily jobs |
| D53 | Admin access to customer content | **Break-glass only** (reason, time-box, append-only audit visible to the org owner) |
| D54 | LLM support assistant | **No customer-confidential data to external LLMs unless the org opts in**; LLM vendors listed as sub-processors |
| D55 | Export control | **Org-set flag per object**; platform blocks sharing/compute to disallowed countries, makes no classification itself |
| D56 | China | **Separate `cn` deployment with a local partner**, only when there is demand; never a bucket in the EU region |

**Physical contract and order (update 2026-09-29, Codex review, owner agreed):**

| # | Decision | Recommendation |
|---|---|---|
| D57 | Port physics | **Acausal across/through ports**; links ideal (no storage, no loss); storage only as declared module state with an energy function; losses leave on the thermal port (2.7.1–2.7.2) |
| D58 | Electrical power across the inverter | **Fundamental phasors + declared harmonic power, or instantaneous series**; RMS + cos φ informational only; fidelity levels `avg_fundamental` / `pwm_averaged` / `switching_resolved`; DC-link capacitor owned by one module (2.7.4) |
| D59 | Time basis | Every value declares form and basis (`instant`, `mean@T`, `rms@T`, `fundamental`); mixing bases at a node is an error; maps never extrapolate silently (2.7.3) |
| D60 | Consistency checks | Units, node conservation, module and system energy residuals, all-quantity convergence, map domain, trial/commit state, **on every system solve**, tolerances as in 2.7.8, stored in provenance |
| D61 | Validation tracks | **Adapters = bit-identical; numerical changes (triangle → gmsh) = justified tolerances + convergence study**; never in one PR (2.7.9) |
| D62 | Order of work | **Publication fix → data loading and versions → motor + controller in contracts → verify old results → battery–controller–motor–load system**; orders, NDA and missions later (11.3) |
| D63 | Module and system exchange format | **FMI 3.0 for modules (export our motor/controller/thermal reduced models, import third-party FMUs in the sandbox) + SSP 2.0 for the system graph and parameter sets**; Modelica connector semantics stay the internal contract (10B.2) |
| D64 | Product and component data | **Asset Administration Shell** (Nameplate, Technical Data, Carbon Footprint) with **IEC CDD / ECLASS** property IRDIs on catalog fields (10B.4) |
| D65 | API contract | **JSON Schema 2020-12 for every payload + OpenAPI 3.1 for the REST API + UCUM units**; MCP tools reuse the same schemas; breaking changes caught in CI (10B.5) |
| D66 | Geometry exchange | **STEP AP242 is the exchange of record**, DXF for laminations, glTF for viewing only, drawings to ISO 128/129/1101/2768; JT/IGES import only (10B.3) |
| D67 | Supply chain and identity | **SPDX licence ids + CycloneDX (or SPDX) SBOM per release**; OIDC/OAuth 2.1; eIDAS/PAdES for signatures (10B.6) |
| D68 | Commercial and bench data | UBL for RFQ/order documents only (non-commercial portal, no Peppol invoicing); QIF optional; OPC UA / MQTT import only for measured-vs-simulated (10B.7–10B.8) |
| D69 | Conformance | **No export format ships without its conformance test in CI** (fmpy/FMI XSD, SSP XSD, STEP re-import, glTF-Validator, AAS validator, OpenAPI lint) (10B.9) |
| D70 | Bidding mode | **Sealed bids by default** until the deadline; "open as received" only for invite-only RFQs by the customer's choice (8.9.5) |
| D71 | Drawings on RFQs | **NDA-gated and watermarked**; the board shows summaries only; download can be disabled (8.9.3) |
| D72 | Who may use the public board | **Verified orgs only** publish and bid on the board; unverified orgs use invite-only RFQs (8.9.3, 8.9.7) |
| D73 | Fees | **No fees ever**: no listing, success or payment fees; the platform is not a party to the deal (8.9, section 10) |
| D74 | Currency normalisation | **ECB euro reference rates** (daily, public), rate and date printed on the matrix; the customer may override with a stated rate (8.9.5) |
| D75 | Clarifications | Public answers broadcast to all bidders **anonymised**; material RFQ changes notify all bidders and flag quotes for revision (8.9.4) |
| D76 | Supplier ratings | Only from awarded orders, **moderated, with right of reply**, after the board runs (8.9.6) |
| D77 | Supplier capability profile | **Mandatory before any RFQ is received**, structured per capability family, versioned, verification level per capability (self-declared / documents checked / audited) (8.9.2) |
| D78 | Matching | **Hard constraints must pass** (process, material, envelope, tolerance, certifications, quantity, region); soft ones scored; reasons stored and shown to both sides; manual invite still allowed with a warning (8.9.2) |
| D79 | Capability taxonomy | **UNSPSC for processes/services, ECLASS for products, NACE Rev. 2 for the org only**; own capability ids mapped onto them (8.9.2) |
| D80 | Supplier UX model | **Freelance-marketplace pattern** (public profile, job feed with saved searches, proposals with cover note, per-RFQ messaging, status milestones), **no escrow, no fees, no payments** (8.9.12) |
| D81 | Reviews | **Two-way, blind** (visible after both submit or 14 days), one per side per awarded and completed job, reply allowed, moderated (8.9.12) |
| D82 | Reputation metrics | **Computed only from platform jobs** (on-time %, NCR rate, response rate/time, repeat customers); related-party jobs excluded; never self-reported (8.9.12) |

### 11.3 Roadmap (rough, weeks of one engineering agent + owner review)

**Priority order (2026-09-29, Codex review, owner agreed, D62).** Nothing further down starts before these five steps pass their acceptance checklist; readiness percentages and calendar estimates are planning assumptions, not measured readiness.

| Step | Content | Acceptance |
|---|---|---|
| **P1. Fix publication** | correct and test the publication boundary, fail-closed validation, operation recovery before any Admin data move | public/private split tests pass; a failed validation publishes nothing |
| **P2. Stabilise data loading and versions** | one integration branch and deployment source; source precedence; immutable identity/revision references; complete public demo install; runtime customer storage separate from reference repositories | code SHA + data revision recorded; the same inputs load the same objects on every node |
| **P3. Motor + controller in contracts** | M0 port contract exactly as 2.7 (units, basis, state, checks); M1 motor adapter; M2 controller adapter, both calling existing code | contract schema and consistency checks unit-tested |
| **P4. Verify old results** | golden captures before work; adapters vs old routes | track A: bit-identical L155 motor, L180 gen, L13 (2.7.9) |
| **P5. Simple system** | battery → controller → motor → load through the system solver (M3), steady and one short transient | balance of 2.7.7 closes within 2.7.8 tolerances; motor point == old coupled loop |

Deferred until P1–P5 and data isolation pass acceptance: RFQs and orders (M10–M12), NDA signing (NDA part of M8), vendor modules (3a–3c) and full mission families (step 4). Manufacturing revisions/BOMs and the ERP boundary stay in the design.


| Step | Content | Effort |
|---|---|---|
| **NOW. Data protection baseline** | off-site backups + restore test, LUKS, file modes, account export/delete, retention jobs, admin audit, privacy notice + DPAs, breach runbook, SSH hardening, `region` field (10A.10) | ~2–3 wk, before more external customers |
| **1. Core restructure: own modules on the contract** | M0 port contract (1 wk) · M1 motor adapter + `machine/1.0` + golden tests (1–2 wk) · M2 controller (1 wk) · M3 system solver calling the old loop (2 wk) · M4–M5 org/project/system beside cfg, battery card (2 wk) · M6 `/api/v2` (1 wk) · `module@version`/`own_node` in `job_usage` (0.5 wk); M7 retirement runs in the background | **~9–10 wk** |
| **1A. Own geometry (import)** | `machine/1.0` + IPM plugin (in M1), then DXF import → region recognition → material/winding assignment → validation → solve | ~3–4 wk (after M1, not first) |
| **M8. Parties and organizations** | org UI, memberships and roles, object grants generalizing die_access, NDA policies, audit view, org verification; BYO org-shared nodes; **NDA workflow** (6.5A): templates, generation, SES click-to-sign with re-auth/2FA, signed PDF with audit trail, grant activation after signatures, auto-revoke on expiry, manual upload | ~5 wk (3 + 2 for NDA) |
| **M9. Manufacturing documents v1** | design revisions + approval workflow; object store; generators: lamination DXF/PDF, multi-level BOM, winding spec; package ZIP; Fusion/STEP uploads with interface check | ~5–6 wk |
| **2. Third module (propeller on maps)** | `propeller` card, C_T/C_P map, datasheet validation, system "battery→controller→motor→propeller", simple builder UI, MCP `build_system/simulate_system` | **~5–6 wk** (can run parallel to M8–M9) |
| **M10. Sourcing** | supplier offers on cards, RFQ → quote → order state machines, **open RFQ / quotation board** (8.9: capability profiles + matching, templates, NDA gate, sealed bids, comparison, award), threads, attachments, notifications (in-app + e-mail), MCP read/draft tools | ~5 wk |
| **M11. ERP connector** | revision → item revision/BOM; awarded RFQ → ERP PO draft; customer order → ERP sales/production order; status and tracking back; measured-vs-simulated back | ~3 wk |
| **M12. Customer orders of finished products** | product cards from released revisions, customer RFQ/order flow, per-serial results sharing | ~2–3 wk |
| **M9b. Documents v2** | magnet spec, pack drawings, STEP of laminations/packs, inspection plan from the passport, controller BOM | ~3 wk |
| **3a. External vendors: maps** | vendor org onboarding, draft→active, badges, `license` | ~3 wk |
| **3b. FMU** | isolated executor on nodes, dispatch through the lease protocol, FMI 3.0 co-sim, limits | ~6 wk |
| **3c. Remote service** | contract protocol, mTLS, cache, retries | ~4 wk |
| **4. Missions (end goal)** | milestones below | ~14–20 wk total |

**Step 4 milestones: mission and motion simulation**

| Milestone | Content | Acceptance |
|---|---|---|
| **4.1 Quadcopter hover** | our L0 motor maps (from FEM, T-dependent) + controller maps + propeller map + battery model; point-mass `vehicle`; profile "hover to SoC_min" | hover time; hand calculation on the same maps ±1 %; L2 re-check of 3 points |
| **4.2 Thermal transients along the mission** | L1 thermal networks of motor/controller/battery; limits and "which block limits" | motor S1/S3 cycle == `coupled_duty_cycle` on L155 within tolerance |
| **4.3 Full 3-DOF flight profile** | take-off–climb–cruise–landing, wind/gusts, ISA; thrust allocation | energy balance closes every step: node sums = 0, module residual Σ P − dE/dt within 2.7.8 tolerances |
| **4.4 Ground and water** | `wheel` + WLTP/AGV route; marine propeller in water + hull drag | same engines, different modules, no core change (**contract generality check**) |
| **4.5 Robot joint and stationary** | `joint` + trajectory, generator on S1–S9 | peak/RMS torque, winding T over the cycle |
| **4.6 Batches and optimization** | `mission_batch`, `mission_opt` on platform + BYO nodes, per-module accounting | "best motor+propeller+battery for endurance" overnight |
| **4.7 FMU and 6-DOF** | FMI 3.0 master, vendor flight controller/gearbox as FMU, 6-DOF | cross-check with a reference FMU |
| **Geometry, further** | step-by-step editor; SPM/outrunner/axial/IM/SynRM/EESM plugins; STEP import | on demand |
| **Later** | CFD modules as coefficient sources, Newton for stiff loops, reply-by-e-mail, third-party ERP connectors | on demand |

Suggested order (restated 2026-09-29): **P1 → P2 → P3–P5 (= step 1, M0–M3) with the data-protection baseline alongside → M4–M7 → (1A, 2, M8 orgs without NDA signing) → M9 → only after acceptance: M8 NDA → M10 → M11 → M12 → 3a → 4.1–4.3 → 3b/3c → 4.4–4.7**. Step 1 changes no number and no URL for users; it runs in parallel with ongoing motor work.
