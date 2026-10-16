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

### Phase A — route existing modules through the kernel — ✅ CLOSED as a non-gap (2026-06-23)
Investigated and found this is mostly NOT a real gap, and forcing it would be wrong:
- The kernel HTTP layer (`routes/kernel.py:_serialize`) deliberately **summarizes MeshIR**
  (keeps counts/tags, drops vertices/triangles) and is not a heavy-field transport. The
  transient already runs through the kernel for its **scalars/series**, while the heavy
  field **frames** stay on the direct route — by design.
- **A1/A2** (mesh build, FEM field maps): the COMPUTE is already a capability
  (`mesh`, `solver.em_static`/`solver.thermal`, used in studies). The direct routes are the
  legitimate heavy-render/transport layer; routing them through the kernel would drop the
  arrays the viewer needs. **Leave direct.**
- **A3** (optimization): the per-eval solve already runs through the `em_transient` module
  in a subprocess; the orchestration is a long streaming job (progress/cancel) unsuited to a
  synchronous kernel call. **Already modular at the compute level.**
→ Real modularization starts at Phase B.

### Phase B — Materials as a module + contract — M
- **B1 ✅ (2026-06-23, `39c3e89`)** — `MaterialIR` / `MaterialProps` contract + `materials`
  capability (resolves the config assignment + library into a typed IR) + conformance gate,
  registered in bootstrap + `_selfcheck`. Additive — no consumer touched. Verified direct ==
  kernel path (full props preserved; MaterialIR is light + JSON-serializable, a good kernel fit).
- **B2** — make consumers read props *through* the module. `fem_solver_2d.build_materials`
  resolves the assignment itself today (`get_material_assignments` + `mat_lib.get_material`,
  helpers `_bh_for` / `_mu_r_for`). NOTE: it needs the **B-H curve** (nonlinear steel) and the
  magnet **2nd-quadrant demag curve** — so B2 first extends `MaterialProps` with `bh_curve`
  (+ magnet demag points), then points `_bh_for` / `_mu_r_for` at a single resolved
  `MaterialIR`. Hot path → refactor must be behavior-preserving; verify with a real solve
  before/after (identical torque/loss). Then the analytical path likewise.

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
- [x] A — closed as a non-gap (kernel summarizes heavy payloads by design; compute already a capability; verified 2026-06-23)
- [~] B materials module (B1 ✅ contract + capability + kernel-verified; B2 consumer refactor pending)
- [ ] C Configure module · [ ] D geometry-3d + mechanical · [ ] E workers/queue
