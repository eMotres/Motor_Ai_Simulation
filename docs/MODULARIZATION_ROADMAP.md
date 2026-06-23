# Modularization Roadmap — closing the remaining gaps

The compute spine is already modular: contracts IR + kernel + modules
(geometry / mesh / solvers / cost) + plugin-SDK; Simulation's transient, the coupled
EM↔thermal, and Cost run through the kernel. This roadmap closes what is **not** yet
modular. Background: memory `modular-portal-architecture`; audit done 2026-06-23.

## Principle — draw the line first
Modular = **compute capabilities** (physics/calculation through contracts). Persistence
and platform orchestration stay as plain services — forcing CRUD into kernel/contracts is
ceremony with no benefit.

- **Modularize** (capability + IR contract): analytical (Configure), materials, FEM
  field-maps via kernel, optimization orchestration, geometry-3d, mechanical.
- **Stay platform services (NOT modules):** catalog, presets, saved-sims, sweep-config,
  `/api/me`, admin, support/tickets, CadQuery+AI pipeline, version/health.

## Per-module recipe (one template for all)
`contract (IR, semver)` → `module (manifest: capability / depends_on / inputs→outputs +
its CLAUDE.md + conformance test)` → `route delegates to the module` → `frontend calls via
kernel` → `verify: direct path == kernel path`.

## Phases (dependency + risk order)

### Phase A — quick wins: route EXISTING modules through the kernel — S, low risk
Modules already exist; no new contracts needed (like the transient already does).
- **A1** — mesh build (`/api/simulation/mesh/build2d*`) → `mesh` capability (MeshIR).
- **A2** — FEM field maps (`fem_field2d`→`solver.em_static`, `thermal_field2d`→`solver.thermal`) → kernel.
- **A3** — optimization endpoints (scan/descent/DOE) → `optimization` capability via kernel.

### Phase B — Materials as a module + contract — M
`MaterialIR` (library + per-region assignment) + `materials` capability; solvers and the
analytical path read material properties through it. Removes direct YAML edits.

### Phase C — ⭐ Configure as a module — M–L  (the portal centerpiece)
Analytical-result contract (extend `ResultIR` / a passport IR) + `analytical.scale`
capability; serve passports from the backend (drop the hardcoded array in
`referencePassports.ts`). Keep client-side math for instant UX **but conform to the shared
contract** (single source = contract + backend passports). Depends on B.

### Phase D — real solvers for the declared stubs — L
geometry-3d (extrude/revolve the 2D GeometryIR → 3D IR) and mechanical (structural
stress / modal) — today `NotImplementedError` stubs with real manifests.

### Phase E — platform hardening (architecture Phase 2/3) — L
Generic kernel workers (subprocess isolation for every solver) → separate Cloud Run per
heavy solver + a job queue. When load / reliability demand it.

## Cross-cutting discipline
- Contracts are **semver** (`CONTRACTS_VERSION`) — change deliberately.
- Each phase ships as a release `0.1.x` — frontend + backend together (`scripts/release.ps1`).
- Extend **Admin → Platform Modules** into a coverage map (which routes run via kernel vs direct)
  so the order doesn't drift.

## Status
- [ ] A1 mesh→kernel · [ ] A2 field-maps→kernel · [ ] A3 optimization→kernel
- [ ] B materials module · [ ] C Configure module · [ ] D geometry-3d + mechanical · [ ] E workers/queue
