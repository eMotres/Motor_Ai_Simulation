# AeroStator Engineering Portal — Architecture

A motor-design portal where a user/company configures a machine to target specs.
Built around AeroStator Core today; designed so **any geometry** and new physics
plug in later. The guiding rule: **modules couple only through versioned contracts**,
never to each other — get the contracts right and the physical layout (one process
today; isolated workers / separate services later) can evolve without rewrites.

## The model: modular monolith → services
Not microservices-from-day-1. A modular monolith with **hard interfaces**, peeling
off services only where it pays (solvers first — heavy + crash-prone). Autonomy
comes from the contracts + fault isolation, not from process count.

## Layers
```
Portal (React)  →  Kernel (orchestrator)  →  Modules (by capability)
                         │                        │
                         └──────── Contracts (versioned IR) ────────┘
```
- **Contracts** (`src/motor_ai_sim/contracts/`, `CONTRACTS_VERSION`): the only thing
  modules share. `GeometryIR`, `MeshIR`, `ResultIR`, `CostIR`, and the closed-loop
  signals `Excitation` / `ControlSignal` / `MachineState`. Plus `Provenance`.
- **Modules** (`src/motor_ai_sim/modules/`): each implements `manifest()` + `run(payload)`.
  The manifest declares `capability`, `depends_on` (upstream capabilities), explicit
  **`inputs`/`outputs`** (contract types = the I/O interface), and a `ui` panel
  (web interfaces are modules too). Each module owns one job and one agent (its CLAUDE.md).
- **Registry** (`registry.py`): routes by capability; `check_dependencies()` enforces
  every `depends_on` has a provider; `pipeline_compatible()` checks a pipeline's stages
  line up by port type (upstream outputs cover downstream inputs).
- **Kernel** (`kernel.py`): `run(capability, payload)` runs one module with **fault
  isolation** (stub/crash/missing → graceful `{ok:false}`, never propagates);
  `run_study(steps)` chains capabilities, threading each result to the next under
  `payload['upstream']`. Exposed at `POST /api/kernel/run` and `/api/kernel/study`.

## Module catalog (12) + dependency graph
| capability | module | in → out | status |
|---|---|---|---|
| `geometry.2d` | geometry-2d-aerostator | ParameterSet → GeometryIR | real |
| `geometry.3d` | geometry-3d-aerostator | GeometryIR → GeometryIR(dim=3) | roadmap stub (→ geometry.2d) |
| `mesh` | mesh-skfem | GeometryIR → MeshIR | real (gmsh+skfem) (→ geometry.2d) |
| `solver.em_static` | solver-em-static | MeshIR,Excitation → ResultIR | wrapper (→ mesh) |
| `solver.em_transient` | solver-em-transient | MeshIR,Excitation → ResultIR,MachineState | wrapper (→ mesh) |
| `solver.thermal` | solver-thermal | MeshIR,ResultIR → ResultIR | wrapper (→ em_transient) |
| `solver.mechanical` | solver-mechanical | MeshIR → ResultIR | roadmap stub (→ mesh) |
| `controller` | controller-foc | MachineState → ControlSignal | real (FOC/MTPA) |
| `surrogate` | surrogate-rf | ParameterSet → ResultIR | wrapper |
| `optimization` | optimization-descent | ParameterSet → ParameterSet,ResultIR | wrapper (→ surrogate,em_transient) |
| `cost` | cost-basic | GeometryIR → CostIR | real |
| `users` | users | — → UserContext | service/ui |

## Closed loop: controller ↔ simulation
```
solver.em_transient ──out MachineState──▶ controller ──out ControlSignal──┐
        ▲ in Excitation ◀── ControlSignal.to_excitation() ◀───────────────┘
```
The controller reads machine state (rotor angle, speed, torque) and writes a drive
command; `to_excitation()` maps it into the solver's input. Interfaces are typed and
declared — not ad-hoc dicts.

## Fault isolation
Kernel catches every module error → graceful `{ok:false}` (e.g. a roadmap stub or a
crashed solver degrades a study, the rest still runs). `ResultIR.failed(...)` is the
solver-side payload. Hard timeouts + true process isolation arrive with the worker
split (see roadmap).

## What's real vs wrapper vs stub (honest)
- **Real, verified on data:** geometry-2d (117 regions), mesh (4836 nodes), cost
  ($), controller (i/γ), the kernel + study pipeline, the geometry.2d→cost and
  geometry.2d→mesh handoffs.
- **Wrappers:** solvers / surrogate / optimization `run()` delegates to the proven
  route functions; result-adapters are minimal so far.
- **Stubs (roadmap):** geometry-3d, solver-mechanical (manifest + graph present;
  `run()` raises NotImplementedError).

## Per-module agents
Each module gets its own `CLAUDE.md` (agent brief: the contract it implements, what
it must NOT do, the test command). Conformance suites in `contracts/conformance/`
gate any implementation — a new geometry/solver from another agent plugs in iff its
IR validates. Self-checks: `python -m motor_ai_sim.{contracts,modules}._selfcheck`
and `._kernel_check`.

## Roadmap (in order, non-breaking)
1. **mesh → solver handoff** — refactor a solver to accept a prebuilt `MeshIR`
   (today the route functions rebuild internally). Heavy/FEM — do carefully.
2. **Full study** `geometry → mesh → em_transient → thermal → cost` through the kernel.
3. **Native GeometryIR mesher** — remove the reconstruct-polys bridge (currently
   ~18% coarser at low resolution).
4. **Migrate live tabs** to call the kernel (Geometry → Simulation), verifying
   identical output, then point the frontend tab list at the manifests.
5. **Worker/process isolation + hard timeouts** for the crash-prone solvers;
   split to separate Cloud Run services where scaling/fault-isolation demands.
6. Plugin SDK + published contract for external geometries/solvers.

## Operational note
The dev box is tight on virtual memory (paging file). Each `import motor_ai_sim`
loads torch (large CUDA address-space reservation) — don't spawn many python
processes; reuse one backend (`uvicorn motor_ai_sim.api:app --port 8001`, no
`--reload` → restart to pick up new routes), verify with `curl`, and clean up
orphans (`taskkill`, keeping the :8001 PID).
