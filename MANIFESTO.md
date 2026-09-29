# Manifesto: an open engineering portal for electric drives and the systems they move

*MOTRES d.o.o. — live application: https://aerostator.com · company: https://emotres.com*

## Why

Electric motors, their controllers, batteries and propulsors decide how far a
drone flies, how long a boat runs, how hard a robot joint can work. Yet the
tools engineers use to design them are closed, expensive, and split into
silos: one package for the magnetic field, another for heat, another for the
inverter, another for the vehicle. Numbers cross between them by hand, lose
their assumptions on the way, and nobody can say afterwards where a figure
came from or how far to trust it.

System questions — *which motor, controller and battery give the longest
flight for this mission, and which part fails first?* — need honest physics
at every level and a way to connect components that come from different
people and different companies. That is what we are building.

## What

An **open engineering portal**: a core plus modules, where

- **physics is honest first.** No smoothing filters that hide a bad result, no
  silent clamping. Every input is validated loudly; an impossible machine is
  rejected, not solved. Every number carries its provenance.
- **motors and controllers come first**, because that is where we have depth
  today (see below).
- **modules from any vendor connect through typed ports** — shaft, DC and AC
  electrical, signal, heat, body motion, environment — so a motor from us, an
  inverter from another company and a propeller from a third can be assembled
  into one system graph.
- **whole systems are simulated through real missions**: a quadcopter hover
  and flight profile, an electric boat or underwater vehicle, a wheeled robot or
  vehicle on a drive cycle, a robot joint on a trajectory, a stationary
  generator or pump on a S1–S9 duty cycle. One contract for air, water, land and
  stationary use; the environment and the vehicle are modules, not special cases
  in the core.

### What already works

The current application (https://aerostator.com) already provides:

- a **2-D finite-element electromagnetic solver** with transient time stepping,
  a sliding air-gap band, eddy currents in magnets, sleeves and conductors,
  iron/copper/magnet losses and demagnetisation checks;
- a **coupled electromagnetic–thermal–mechanical loop**: losses feed the
  thermal model, temperatures feed back into material properties, and rotor
  stress, retention, modes and critical speeds are checked on the same point;
- a **controller (inverter) module** with device models from datasheets and
  SPICE, conduction/switching losses and junction temperature, and **PWM
  coupling** so the real switched voltage drives the FEM solution;
- **3-D end-effect passports** that correct the 2-D model per machine;
- **reports and datasheets** generated from recorded runs;
- **catalogs with provenance** (datasheet / measured / estimate / derived per
  field, with status and revision);
- **multi-user workspaces**;
- an **MCP server** so AI agents can drive the same workflows as people
  (see `docs/MCP_2026-09-28.md`).

Results are validated against measurements; cross-checks against independent
tools are kept private.

## Principles

1. **Honest physics.** Energy-based quantities, averaged stresses with stated
   conventions, no hidden fudge factors. If a model is out of its validity
   range, it says so.
2. **Provenance on every number.** Module and version, geometry hash, solver
   commit, mesh, source data. A result without its origin is not a result.
3. **Validation against measurements.** Models are checked against test-bench
   data; the error is recorded and shown next to the result.
4. **Open contracts.** Ports, module manifests, card formats and result formats
   are public, versioned and documented.
5. **Vendor IP is respected.** A vendor can join with a black-box module —
   performance maps, an FMU, or a remote service behind the contract — without
   disclosing internal design.
6. **Human in the loop for orders.** The portal computes and recommends;
   purchasing, manufacturing and commitments are confirmed by a person.
7. **AI agents are first-class users.** Everything a person can do through the
   interface, an agent can do through MCP, under the same validation and
   permissions.
8. **Users own their data.** Your geometry, measurements and results are
   yours; you can export them in open formats and take them elsewhere.

## How (in short)

- **Core**: projects, system graph, solver scheduling, catalogs, provenance,
  users and permissions, API and MCP.
- **Module contract**: a module declares a manifest — its typed ports, the
  calculations it offers, its cards (materials, devices, maps), its version.
- **Fidelity ladder**: each module can answer at several levels.
  **L0** — maps (microseconds per point); **L1** — reduced models such as
  thermal networks and d-q models (milliseconds per step); **L2** — full
  coupled FEM or SPICE (minutes to hours). Missions run on L0/L1; L2 *builds*
  those maps with full provenance and re-checks the most stressed points of a
  mission, so the error of the fast model is always known.
- **Own geometry**: parametric machine families, DXF import with region
  recognition and material assignment, and a step-by-step editor — all
  producing one machine description and passing one validation.

## Open core model

**Open** (planned licence below): the core, the solvers, the module contract
and data formats, and the base modules (motor, controller, thermal, basic
propulsors and vehicles).

**Not open**: the hosted service and computing cluster, proprietary models
supplied by vendors, customers' data, and MOTRES's own product designs.

## Roadmap

Stages, in order; we commit only to "next", not to dates.

1. **Own modules on the contract** — motor and controller moved onto the port
   contract without changing a single user-visible number; system solver;
   versioned API.
2. **Own geometry** — common machine description, then DXF import.
3. **A third module** — propeller on maps; the first system
   battery → controller → motor → propeller; simple system builder; MCP tools
   to build and simulate systems.
4. **External vendors** — first through maps, then FMU (FMI 3.0) in isolated
   runners, then remote services.
5. **Missions** — quadcopter hover; thermal transients along a mission and
   "which block limits"; full 3-DOF flight profile; land and water; robot joints
   and stationary duty cycles; batch runs and system optimisation; FMU and 6-DOF.
6. **Later** — more machine types (SPM, outrunner, axial flux, induction,
   synchronous reluctance, wound-field), STEP import, CFD modules as sources of
   coefficients.

## How to contribute

- **Issues** — bug reports, wrong physics, missing validation: open an issue
  with inputs and the result you expected.
- **Discussions** — ideas for modules, ports and formats.
- **Pull requests** — welcome; a Contributor License Agreement (CLA) is planned
  and will be required before contributions can be merged.

## Licence (PLANNED — not yet in effect)

**Planned licence: GNU AGPL-3.0 for the open core, with a Contributor License
Agreement; commercial licences available from MOTRES d.o.o.**

Why: AGPL keeps improvements to the open core open, including when it is run
as a network service, while the CLA and commercial licences let companies
integrate the core into closed products and let us fund the work.

This licence is **not yet applied**. There is no LICENSE file in this
repository yet; until there is, no licence is granted. Before it is applied,
one dependency with a non-commercial licence must become optional.

## Contact

MOTRES d.o.o. — https://emotres.com
