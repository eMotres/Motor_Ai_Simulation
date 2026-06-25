# Changelog

All notable changes to **Motor AI Simulator** are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com); versioning follows [SemVer](https://semver.org).
The single source of truth for the current version is the `VERSION` file at the repo root;
cut a release with `scripts/release.ps1` (see `docs/RELEASES.md`).

## [Unreleased]

## [0.1.5] — 2026-06-25

### Changed
- **Optimizer ≡ Simulation (computational integrity).** A design picked in
  Optimize now reproduces exactly when re-run in Simulation. The optimizer's FEM
  evaluation calls the *same* solver with byte-identical parameters — the previously
  dropped air-gap layers, coil temperature, outer-air factor, demag flag and
  component-mesh are now threaded through, and efficiency uses the Simulation
  formula (P_mech / P_elec). Applying a point also restores that run's evaluation
  parameters into the Simulation tab, so the point reproduces even if settings
  changed in between.
- **Simpler optimizer operating point.** The optimizer runs at the Simulation
  tab's fixed current / speed / γ and varies *only* the selected geometry
  variables — removed the "target torque / auto-solve current" mode and the
  inverter voltage limit (set voltage by hand instead). `current` stays selectable
  as a variable: unselected it's the fixed Simulation current, selected the
  optimizer varies it. The ★ best point is now click-selectable and applicable.
- **Mesh symmetry defaults to Full** (full disk — the accurate, canonical mesh)
  everywhere: Simulation, Mesh, Optimize, Sweep, DOE and the animation viewer.

### Added
- **Real-geometry thumbnails + full metrics on saved-motor cards.** Saving a motor
  now renders its actual cross-section as an inline thumbnail and fills the card
  exactly like the prebuilt catalog — torque, power, efficiency, voltage, magnet,
  steel, stack length and wire spec.

## [0.1.4] — 2026-06-24

### Changed
- **Optimization is now 2-criteria (efficiency × torque/mass); ripple is a visual
  filter, not a constraint.** Removed the pre-run "Torque ripple constraint" slider
  and the ripple penalty in the optimizer cost — the descent/CMA-ES search purely
  maximises efficiency × torque-density. Run the optimization, then trim pulsation
  with the on-chart "ripple ≤ X%" slider and pick the design you want. (The inverter
  voltage budget is still enforced, so designs stay drivable.)

## [0.1.3] — 2026-06-24

### Added
- **Optimization chart — pick and apply any design:** an on-chart "ripple <= X%"
  slider trims high-pulsation points without re-running; click a scatter point to
  select it and **Apply** loads that exact geometry + operating point (not just the
  auto-best). Objective-space axes now auto-fit to the extreme torque-density (X)
  and efficiency (Y) of the displayed points, re-fitting live as the slider hides/
  shows points.

### Fixed
- **Optimize efficiency now matches Simulation:** the descent evaluation left out
  rotor (magnet) eddy losses and the end-winding factor, so Optimize reported a
  higher efficiency than Simulation for the same design; both are now forwarded
  (single source = Simulation).
- **|B| field view matches the Ansys scale** — discrete blue->red bands in mTesla
  over the real field range, instead of a continuous jet clipped at 1.8 T that
  amplified per-element saturation noise.
- **Eddy-current (J) field view shows the whole motor** — the route forced an
  illegal 4-sector wedge for the 12-slot/14-pole motor and rendered only 3 of 12
  coils; it now honours the full disk like the main Simulation.
- **Materials tab crash** (an undefined reference), plus a per-tab error boundary
  so one failing tab no longer blanks the whole app.

## [0.1.2] — 2026-06-23

### Added
- **Materials management — shared admin library + per-user "My Materials":** the
  materials library is now built-in **+** an admin-managed **global** layer
  (Firestore `materials_global`) **+** each signed-in user's personal **mine** layer
  (`users/{uid}/materials`). Admins add / edit / delete shared materials; any user
  copies a material to their own library and edits its properties. Custom materials
  **resolve in the FEM solve** — global server-side, mine/global via a stateless
  per-request override — and material assignments persist per-user.
- **Insulation is assignable:** selecting the slot liner / wire enamel in the
  component tree now opens the material bar (insulator + coolant categories added).
- **Configure → Thermal (analytical estimate):** steady-state winding / magnet / housing
  temperatures computed from the configured losses + the **same cooling inputs as
  Simulation** (air / water / glycol / oil, ambient, speed/flow, live h). Lumped
  resistance model — instant, no FEM; warns when the winding (~155 °C, class F) or magnet
  (~150 °C) limit is exceeded.

### Changed
- **Access control:** the Motors catalog stays open to everyone, but **Configure,
  Materials + the engineering tabs now require sign-in**. Anonymous visitors browse
  motors only; "Load" prompts Google sign-in (previously anon could open Configure and work).
- Catalog: first diameter bucket **5 mm → 12 mm**.

### Removed
- Redundant "Sign in to save your motor designs" prompt in the catalog — the header
  already has a Sign-in button.

## [0.1.1] — 2026-06-23

### Changed
- **Rebrand → AeroStator Core** — a motor technology portal: header title, browser
  title/meta, motor-catalog copy (positioned for **aerospace, robotics, EV, marine**;
  select → configure → price → request manufacturing), version-badge tooltip, and the
  support assistant's identity. Internal package name (`motor_ai_sim`) unchanged.

## [0.1.0] — 2026-06-23
First tracked release. Establishes app versioning + a coordinated release process
(frontend + backend deployed together, version stamped into both, skew detected at runtime).

### Added
- **Multi-user isolation (P1–P4):** per-request `?geo=` so each signed-in user computes their
  OWN design instead of a shared global config; per-user active workspace persisted to Firestore
  (`users/{uid}/workspace/active`, restored on sign-in); `AUTH_ENFORCE` + tiers live
  (anonymous = configurator-only, heavy FEM gated, owner = admin).
- **Slot-derived winding connections** (2S/2P … 8S/4S-2P/8P) — single source `/api/winding/config`.
- **Versioning:** `VERSION` file, `GET /api/version`, in-app version badge with
  frontend↔backend **skew detection**, this changelog, and a coordinated release script.

### Fixed
- **3D viewer:** stuck "Building…" indicator (the updating flag now clears on every fetch
  settle); FEM sliding-band air domains no longer obscure the motor (default off + migration).
- **40 mm geometry:** `tooth_width` schema minimum lowered (4 → 1) so small motors are
  editable in the UI and build without a degenerate slot fillet.
