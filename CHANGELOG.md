# Changelog

All notable changes to **Motor AI Simulator** are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com); versioning follows [SemVer](https://semver.org).
The single source of truth for the current version is the `VERSION` file at the repo root;
cut a release with `scripts/release.ps1` (see `docs/RELEASES.md`).

## [Unreleased]

### Added
- **Default propeller per configuration** (owner 2026-10-05): `config/cooling_options.yaml` `defaults:` under
  the die (CIANO14 40 new: L12 -> `tmotor_fpv_10x5`, L20 -> `tmotor_p13x4_4`), served as `default_propeller`
  (and the die's `defaults` map) by `/api/propellers/cooling-options` and `configure_context.cooling`. A default
  the configuration may not use, or the catalogue cannot compute, is withheld and named in `bad_defaults`.
  Configure's picker takes the configuration's default on loading it and on "reset to preset" (the user's pick is
  dropped); with no default it takes the first allowed propeller with torque data. The preset "modified" check and
  the preset highlight include the propeller; a saved configuration keeps the user's choice (and shows it in the
  table). Server install: copy `config/cooling_options.yaml` into `<shared>/`.
- **Configure with a propeller** (owner 2026-10-05, Ø40 drone motors). A die whose only cooling is
  `propeller_air` (`config/cooling_options.yaml`) gets a **Propeller** picker (its allowed propellers;
  geometry-only ones listed disabled, "no test data") and one ambient-air field; the separate Thermal block
  is gone for it. **Load from the propeller** (default; "Manual" gives the current back): the speed knob
  gives the propeller torque, the phase current is DERIVED from the passport and shown read-only, and a
  torque the motor cannot make is refused loudly. **ONE temperatures row** in the result tiles (winding,
  magnet, housing, cooling air, film h), present from the first render; over a limit (winding 180 degC class H
  default, magnet = its card's limit) a tile reads `> 180` in red instead of an absurd number, and a single
  red line above the tiles says "overheats at this current - lower the current or use thicker wire".
  **Thermal zones** on the speed and current knobs (green below both limits with this propeller, red beyond),
  recomputed live. "Beyond tested rpm" is marked on the speed title. Backend: `GET /api/propellers/{id}/series`
  (the propeller on an rpm grid with the housing film h per sample - the browser only interpolates),
  `configure_context` now carries `cooling`, `thermal_limits`. Not done: `/api/thermal/field`, `/coupled` and the
  duty cycle do not take the propeller yet (Configure does not need them).
- **Configure Drive: the same result tiles in Sine and PWM**: every drive tile exists in both modes at a fixed
  position and width; a refusal turns values into a dash inside the tiles and never inserts a block. MOTOR PWM
  EXTRA LOSS and CONTROLLER LOSS (conduction / switching / dead time in the tooltip) end the loss row, **0 W in
  Sine**; TOTAL LOSS = motor losses + PWM extra + controller loss. T_J, DRIVE efficiency, shaft efficiency with
  drive and P cont. max stay in the fixed drive row ("no inverter model" in Sine).
- **PWM picker = two dropdowns**: transistor, then PWM frequency (only the carriers computed for that transistor
  in this motor). Changing the transistor keeps the frequency when that pair exists, else the nearest computed one.
  The pair names exactly one variant; it is remembered per machine with the id, carried by presets, and "modified
  from preset" compares the pair. Variant details (technology, dead time, parallel count, modulation, bus) are in
  the tooltip only.
- **Clean tile titles**: no `3-D flux` / `2-D` / `(rated)` / `analytical` / `lumped, no FEM` tags; the method goes
  into the tile's tooltip.
- **Propeller catalogue, propeller load and slipstream cooling air** (owner 2026-10-05, Ø40 drone motors).
  `config/propellers/tmotor/*.yaml` (12 T-Motor entries of 10-13 in with source URL + access date per
  table; 5 with measured torque, 2 thrust-only with an explicit estimated C_P, 5 geometry-only that the
  model refuses to compute), `motor_ai_sim.propeller` (static C_T/C_P fits, thrust/torque/shaft power,
  ISA air density, momentum-theory slipstream speed x a stated, to-be-calibrated motor-position factor,
  rpm-for-torque and motor-curve equilibrium), read-only `GET /api/propellers`, `/{id}`, `/{id}/point`,
  `/cooling-options`, per-die `config/cooling_options.yaml` (CIANO14 40 new = `propeller_air`), and
  `air_speed_source="propeller"` in `solve_thermal_field` (default `manual`: every other mode
  bit-identical). Electrical power is never used as shaft power. See
  `docs/PROPELLER_CATALOG_2026-10-05.md`.
- **Configure: Drive menu, Sine | PWM** (owner 2026-10-05). PWM lists only the drive
  variants computed for the motor (`pwm_variants` in its passport: a device at a
  carrier frequency) and reads between their computed points; it refuses outside the
  computed envelope, off the loaded build, and beyond the device's bus, current or
  junction limits. No variants = PWM disabled with a "request calculation" note. Shows
  motor loss, inverter loss split, T_j, drive efficiency battery to shaft; the saved
  configuration records the drive. Sine is the default and unchanged (scaleMotor is
  untouched). EN + ZH strings in the `controller` namespace.
- **Configure: presets, battery under the sliders, lean loading** (owner 2026-10-05). One
  preset per configuration of the machine's die (read from the machines, none hard-coded;
  `GET /api/catalog/{id}/configure_context` -> `presets`): choosing one restores every knob,
  its saved pack and its default drive; "modified from <preset>" appears on the title row once
  anything differs; saved configurations now carry the battery, the drive and the preset and
  are saved under a name. The Battery block sits right under the sliders, opens on the machine's
  own pack (6S for L12, 12S for L20), the user's edits are kept per machine, "reset to machine
  pack" returns; everything bus-dependent (full-battery speed, the 2S/2P voltage warning, the
  motor marker) follows the edited pack, and a PWM variant is refused outside its computed bus
  range. Limit captions sit on the title row, not under the sliders. `GET /api/catalog/references`
  serves Configure without the 12 MB of thumbnails; "no configurator model" no longer flashes
  before the catalogue answers.
- **Configure reads the passport pilot's computed drive variants** (#106): the catalogue
  response (`GET /api/catalog`, `GET /api/catalog/{id}/passport`) now carries `pwm_variants`
  for a card whose machine has a v1 record in the versioned store
  `config/passports/<die>/<config>.json` (`<shared>/passports/` on the server wins, like
  device cards; `scripts/export_passport_store.py` writes a file from a pilot record).
  Matched by card name, else die name + stack length; ambiguous = none. Cards without a
  record are served exactly as before. CIANO14 40 new L12 / L20 ship with the pilot's data.
- **Configure: slider ranges are physical limits** (owner 2026-10-05), per machine and live
  with the knobs they depend on. Stack-length max is set by hand per motor (admin,
  `PATCH /api/catalog/{id}/configure_limits`, else the default rule); wire min 0.2 mm, step
  0.1 mm; turns max = rows that fit the slot at the chosen wire (same inequality as the
  solver); phase-current max = the machine's inverter device x devices in parallel (also in
  Sine; "no controller set" otherwise); speed max = the voltage envelope at the pack maximum,
  else Kv x V_max x m. The 2S/2P connection is free with a line-voltage warning. Admin range
  edits only narrow. `GET /api/catalog/{id}/configure_context` serves the machine's limits.
  The old measured-delta "Excitation" toggle and its code (`pwmDeltas`, the `pwm_*` result fields) are removed (PWM = computed variants only). The
  whole Configure tree is fully Chinese in ZH (a node test scans the source and the locale).

### Changed
- **Licence: Apache License 2.0** (owner decision 2026-10-03), replacing
  AGPL-3.0-or-later and the planned MKL section 7 exception (PR #93). `LICENSE`
  is the Apache-2.0 text, `NOTICE` added, SPDX identifiers `Apache-2.0`.
  Contributions stay under the DCO. Dependency model in THIRD_PARTY_NOTICES.md:
  permissive and LGPL (Netgen) libraries in process, gmsh (GPL) only as the
  separate gmsh worker program, Intel MKL optional, Triangle removed.
- **Cholesky for the SPD P2 systems** (docs/CHOLESKY_SPD_2026-09-29.md). The secant
  stiffness, the Newton Jacobian and the bordered eddy matrix are SPD by construction and
  are now factorised with PARDISO mtype 2 instead of the unsymmetric LU, guarded per solve
  (symmetry probe, positive diagonal) with a loud LU fallback; series strand paths stay on
  LU. Numbers identical to 1e-12; `SB_PARDISO_SPD=0` restores LU.
- **Licensing: pure AGPL-3.0-or-later, contributions under the DCO.** No
  commercial licensing and no CLA: `CLA.md` and the CLA Assistant workflow are
  replaced by `DCO.md` (Developer Certificate of Origin 1.1) and a `DCO`
  pull-request check (`Signed-off-by` on every commit, `git commit -s`).
- **`triangle` optional.** Shewchuk's Triangle forbids commercial use, so it
  is no longer a default dependency and not part of the AGPL distribution
  (`requirements-triangle.txt`, extra `[triangle]`, Docker
  `--build-arg WITH_TRIANGLE=1`). Installed, the geometry-driven mesher works
  and stays the default exactly as before; absent, the mesher uses gmsh (one
  log line) and the earcut fallback uses shapely. Staged transition to gmsh:
  `docs/MESHER_TRANSITION.md`.
- **`pypardiso` / Intel MKL optional.** Not a default dependency any more
  (`requirements-pardiso.txt`, extra `[pardiso]`, or
  `--build-arg WITH_PARDISO=1`); every solver falls back to SciPy SuperLU.
  Dependency audit recorded in `THIRD_PARTY_NOTICES.md`.

### Fixed
- **Configure showed "NMC 100 cells / 370 V" beside the 6S L12 machine** (owner 2026-10-05, live
  e7b1ba2). Two reference cards tie on L12's cross-section and build (the real "CIANO14 40 new"
  and an older duplicate "CIANO14 40_12" with no pack or controller); the first of the tie won,
  which on the live catalogue is the duplicate. `/api/catalog/references` now flags
  `has_machine`, and the pick goes build, then geometry, then "is a machine", then order
  (`pickReference`). A stored battery "edit" equal to the stock 100-cell default is never an edit
  (never written, dropped once on load). The motor marker (sqrt(3) x phase peak, 18 V rated / 20 V
  peak at the catalogue duty points, pinned by a test) now names the operating point it is for.
  The geometry pictures sit right of the Battery block with their subtitles on the title row, and
  the speed / efficiency charts are hidden behind one flag (`SHOW_CONFIGURE_CHARTS`, off).
- **L155 eddy: the shaft now settles, 0.4 % from its asymptote** (docs/EDDY_SHAFT_SETTLE_2026-09-29.md).
  The oscillating shaft gauge was a 5-period beat of non-pole-pair-periodic DC patterns that
  the one-angle static start froze into the solid wall; under it the wall's rotor-frame DC
  diffuses with τ = 21 periods. The ring conductors now start from their pole-pair image
  mean, and the slow DC error is corrected after periods 2 and 4 by one static solve of the
  period-averaged exact Jacobian (TP-EEC), verified by the unchanged gauge plus a tail test
  at the operator's slowest λ. L155: 650 frames capped (+3.0 % shaft) → 614 settled (−0.4 %).
  `SB_EDDY_START_IMAGE_MEAN=0`, `SB_EDDY_EEC=0` restore the previous march.
- **PWM settle: the DC offset is solved, not anchored** (docs/NO_FILTERS_2026-09-24.md
  item 5, option (c); `docs/PWM_DC_ORBIT_SOLVE_2026-09-26.md`). The period-mean DC
  anchor had no free decay in its model: on a short-τ_e machine it over-corrected
  and the reported window opened on its own last correction (30 mm fixture:
  0.55 A of DC left, T −0.20 %, P_cu −0.68 % against a free settle; Ø40: 1.1 A,
  P_cu −0.9 %). It is replaced by a Newton shooting solve of the line-to-line
  flux's period map (`simulation/dc_orbit.py`): the exact whole-period flux
  drift, the period Jacobian from each frame's own incremental ∂ψ/∂i (no extra
  solve), the modulator's turn-on flux predicted from its volt-seconds, and the
  last settling period left free as the verification. The mixed coarse/fine
  schedule's handover step was one COARSE step long, so the first fine
  "period" (which the anchor measured too) was P + Δθ_f; the r − 1 fine frames
  that complete it are now on the fine grid (+1 frame at ratio 2; the reported
  window's angles are unchanged). 30 mm fixture, 72 steps/9 carriers: DC left
  0.554 A (anchor) / 0.025 A (free) → 0.000 A. Payload: `v_dc_orbit`
  (per-period drift, DC, correction, Jacobian eigenvalues) replaces
  `v_dc_anchor_applied`; `SB_V_DC_SOLVE=0` measures without correcting (for a
  reference run's long free settle).
- **Stop during the solver's set-up** is honoured before frame 0. The progress
  callback that carries the Stop button fired for the first time at frame 0, so
  the mesh, the assembly, the sliding-band projections, the phasor initialiser
  and the static start field ran deaf to it (up to ~30 s on a large machine).
  Each stage now starts with a checkpoint (`progress_cb(None, None)`, which
  keeps the bar), and so does each demag re-solve of a frame.

### Changed
- **The duty cycle is behind a feature flag, off by default** (owner 2026-09-17:
  *«let's remove the duty cycle from Thermal for now, keep only the standard
  coupling»*). The Thermal tab is the cooling and the coupled EM↔thermal loop
  again; the Duty cycle block, the catalog's S1/S3 chip and every ED term a
  coupled answer could print are hidden behind the build flag `VITE_DUTY_CYCLE`,
  and the loop's own ED search behind the backend env var `DUTY_CYCLE_ENABLED`.
  With the backend flag off `POST /api/coupled/run` runs the **standard** loop
  for every duty — a stored S2/S3 block is read as the continuous point, the
  temperatures iterate to their fixed point, no `duty_cycle` sub-block is written
  and no cycle record is filed. Nothing was deleted: `1` on either flag restores
  that half exactly. The standalone `POST /api/thermal/duty_cycle`, the heat
  paths, the `robotics` cooling mode and every duty-cycle record already filed
  are untouched — the report still prints its cycle section for a stored record.
  Rationale and the operator's view: `deploy/README.md` § *The duty cycle is
  behind a flag*.

### Added
- **A second optimizer search: screening descent** (`POST /api/optimization/auto`
  with `mode: "screen"`; the Optimize card's **Explore / Refine** switch). The
  one-click CMA-ES run spent 434 evals on the CIANO20 150_35 and its best
  candidate inside the 5 % ripple gate scored **F = −0.0173** on the run's own
  perpendicular-baseline metric — it never beat the design it started from. The
  user then beat it **by hand** at the same fixed operating point, touching at
  most four parameters at a time, reaching **F = +0.00221**. The defect is in
  the search, not the physics: CMA-ES must estimate an N×N covariance (171 free
  parameters at N = 18) before its distribution carries any shape, and at ~10 min
  per honest FEM eval that budget does not exist. So the new mode does what the
  engineer does — perturb **every** variable by ±δ (0.2 mm on a length, 0.02
  dimensionless, 1 on an integer) to see which way each one moves the machine,
  descend the most influential few (k chosen by the gap in the table, line
  search over α ∈ {0.5, 1, 2, 4} in one parallel wave), then polish with the
  rest in groups of ≤ 4. Everything that decides whether a number is honest is
  shared with the CMA route: the same eval subprocess, the same in-process
  geometry pre-fence, the same ripple penalty and continuation ramp, the same
  eval cache and the same progress channel. The run publishes its **ranked
  sensitivity table** with a measured noise floor — which knobs matter, which
  way, and which are inert — which outlives the run and is something CMA-ES
  never produces. **Acceptance run, from the same 08/04 baseline the CMA-ES run
  started from and at the same fixed operating point**: F = **+0.002311** in 220
  evals (10.808 Nm/kg, 96.451 %, ripple **4.30 %**) — it beats the user's
  hand-tuned design (+0.002213, 10.509 Nm/kg, ripple 5.04 %) by 4.4 % on F while
  sitting comfortably *inside* the ripple gate the hand design was marginally
  over, and it stopped on the eval budget while still improving. 90 min wall
  clock on 10 workers, unattended. Honest limitation, stated in
  `docs/SCREENING_DESCENT.md`: it finds the nearest local optimum and cannot
  change basin, so *Explore* remains the right tool for a design nobody has
  optimised yet.
- **P2 (second-order / quadratic) elements now run the full sliding-band
  transient** on the gap-resolving structured belt (`element_order=2`,
  `structured_gap=True`, full ring `n_sectors=-1`). B = curl A is LINEAR per
  element instead of piecewise-constant, so the air-gap Arkkio torque is
  physically smooth where P1 staircases — no filters. The historical blocker,
  pairing the P2 **edge-midpoint** DOFs across the moving slip cut, is solved:
  the signed union-find that welds the belt now welds each interface ring
  vertex AND its ring-edge midpoint to the partner as the rotor shifts by *m*
  slip nodes (validated: all ring-edge midpoints paired). Assembled on the
  single stitched mesh with a facet-based outer Dirichlet BC so the P2 boundary
  midpoints are pinned too. Real measured wins (40 mm 12s14p, structured belt):
  no-load cogging mean −0.015→**+0.001 Nm** (P2 restores the physical zero),
  p-p 0.073→**0.030 Nm** (2.5×), staircase jitter 0.034→**0.016** (2.1×);
  loaded (I=30, γ=−20) ripple 24.9→**14.8 %**, forbidden-order noise floor
  3.9→**1.1 %** (3.6× less numerical staircase). The P1 default
  (`element_order=1`) is byte-for-byte unchanged. Not yet on P2: the
  eddy/voltage/demag coupling — these raise a clear `NotImplementedError`. See
  `P2_NOTES.md`.
- **P2 works on the anti-periodic SECTOR wedge (`n_sectors≥2`), not just the
  full ring.** The belt projection now also welds the radial-cut vertices AND
  cut-edge midpoints with the anti-periodic sign, and uses the open-wedge ring
  wrap map. Validated: `n_sectors=2` P2 T_avg matches full-ring P2 to **0.3 %**
  (0.3771 vs 0.3761 Nm, loaded), and is ~2.3× faster (half the mesh). This is
  the symmetry the default config (`n_sectors=2`) uses.
- **P2 convergence proven.** No-load cogging at mesh 1.4/1.0/0.7 mm (full ring,
  fixed ring density): the P2 forbidden-order torque noise floor CONVERGES
  0.0042→0.0034→**0.0023 Nm** (toward 0) while P1 stays flat at ~0.014 Nm
  (mesh-independent staircase) — at 0.7 mm P2's floor is 5.8× lower. Confirms P2
  is physically correct, not merely different from P1.
- **`element_order` wired through the app** — `GET /physics/fem_transient` takes
  an `element_order` query param (default 1); requesting 2 (a "high-fidelity
  ripple" mode) auto-forces the structured belt + current-drive magnetostatics.
  The frontend transient request passes `element_order` from a `mesh.p2HiFi`
  flag (default P1); no UI toggle was added.
- **`element_order` threaded through the Sweep study AND the descent optimizer.**
  The Sweep chart previously always ran P1, so its ripple showed the P1 staircase
  (inflated) while the Simulation P2 toggle showed the honest low value. Now the
  scan path (`ScanRequest.element_order` → `_scan_worker` → `_subprocess_eval` →
  `refine_proc.run_one`) and the descent optimizer path (`DescentRequest` /
  `BaselineRequest.element_order` → `_descent_worker`/`_cmaes_worker`/
  `_mtpa_gamma_sweep` → `_subprocess_eval`) both carry it, `run_one` applying the
  same P2 self-consistency coercions the Simulation route does (force structured
  belt, disable macro/demag, keep rotor_eddy, auto-use the natural-symmetry
  sector). Added to the eval cache key so P1/P2 results don't collide. Frontends
  (`SweepStudyPanel.tsx`, `motorStore.ts` descent/baseline) send
  `element_order = mesh.p2HiFi ? 2 : 1` — the same flag the Simulation P2 toggle
  sets. Validated (40 mm 12s14p, I=30 γ=−20): a P2 sweep point reports the honest
  **14.9 %** ripple vs P1's **24.4 %** at matched T_avg (0.413 vs 0.416),
  matching a Simulation P2 run. Default `element_order=1` (P1) is unchanged. A P2
  sweep/optimization is ~2× slower per point (the user wants honest ripple).
- **P2 uses Newton-Raphson for the BH saturation (default).** The damped Picard
  needs ~40 sweeps/frame; Newton with the differential-reluctivity tangent
  (pointwise ν(|B|²), J = K(ν) + 2(dν/dB²)(∇A·∇u)(∇A·∇v)) converges in ~13,
  giving 1.5–2.2× less wall-time (loaded sector 3.82→1.77 s/frame). It converges
  the field residual to 1e-7 — the exact per-frame magnetostatic solution — so it
  is also MORE accurate than the Picard (which stalled at ν-change 6e-3): the true
  fixed-point ripple is 16.4 % (matching the P1 result 17.2 %), vs the
  under-converged Picard's 20.3 %; mean torque and losses match to <1 %. Robust
  across no-load→heavy load and sector/full-ring (fallback to damped Picard on any
  frame Newton can't globalise; never triggered in testing). `SB_NO_NEWTON=1`
  forces Picard. Not wired into P1 (the same tangent would accelerate it later).
- **P2 solve uses MKL PARDISO (pypardiso) when available.** The P2 per-frame cost
  is factorization-bound; benchmarked on the real Picard sweep sequence, a
  persistent `PyPardisoSolver` (symbolic reuse across same-pattern sweeps) beats
  SuperLU 1.8–2.4× on the ~28k full-ring system the app uses (≈1.1× on the small
  sector). Wired into the P2 solve with a try/except SuperLU fallback (never
  breaks a run) and an `SB_NO_PARDISO=1` debug gate; NOT wired into P1 (kept on
  scipy to guarantee byte-for-byte P1 output). Integrated: P2 full-ring 7.97→6.58
  s/frame, sector 3.51→2.87 s/frame; mean torque identical, ripple <0.6%. Exact
  to 3e-12.
- **P2 transient sped up ~1.8× (converged) via ν warm-start + stiffness split.**
  The P2 branch reset the BH-saturation ν to base every frame (re-converging from
  cold, ~70 Picard sweeps) and re-assembled the whole mesh each sweep. Now: ν
  warm-starts from the previous frame (only frame 0 pays the cold ~70; later
  frames ~40), the constant-ν stiffness is assembled once with only the saturable
  iron re-assembled per sweep, and the free-DOF solve uses a precomputed slice.
  Same fixed point (torque/ripple/loss match to <1 %, no-load mean stays ~0), but
  to reach it P2 is ~1.8× faster (3.5 vs 6.4 s/frame at mesh 1.5) and now actually
  reports converged. The per-frame floor is the direct factorization of the 2×
  larger P2 system (LU reuse was tried and rejected — ν moves too much between
  sweeps). GUIDANCE: run P2 at a COARSER mesh than P1 (≈1.5–2.0 mm) — its value is
  coarse-mesh accuracy, and cost scales with DOFs.
- **P2 now reports real eddy/iron/copper losses** (`rotor_eddy=True`), so a P2
  loaded sim gives efficiency, not zeros. Magnet + shaft eddy come from the same
  honest reaction-included rotor solve P1 uses (`honest_rotor_eddy`) on the P2
  rotor A(t) history; iron from Bertotti on dB/dt; copper from I²R. Validated
  (I=30, γ=−20, app-default mesh): P2 magnet/shaft/iron eddy match P1 within ~7 %
  (0.55 vs 0.58 W magnet, 0.12 vs 0.12 W shaft, 5.9 vs 5.9 W iron) while P2 keeps
  ~2× cleaner ripple (10.4 % vs 21.3 %). The route keeps `rotor_eddy` on for P2;
  only the opt-in coupled-eddy J-view, voltage drive and demag pre-pass stay
  gated (the app transient uses none of them — P1's app path is `eddy=False`
  too).

### Fixed
- **Optimizer eval subprocesses are now actually pinned to one core**, as the
  design has always claimed. The pool runs `FEM_SCAN_WORKERS` evals at once,
  but nothing was limiting the BLAS/LAPACK thread pool inside each subprocess,
  so every one of them sized itself to the whole machine and N concurrent evals
  asked for ~N× the cores that exist. Measured on a 12-physical-core box with
  10 workers (CIANO20 150_35, 48 frames): a 10-eval wave returned **nothing in
  18 minutes** while a single eval running alongside it finished in 5.7 — the
  pool was thrashing, not computing. With `MKL/OMP/OPENBLAS/NUMEXPR_NUM_THREADS
  = 1` in the eval subprocess and the parallelism left to the pool, the same
  eval takes ~2 min. It also makes an eval **bit-reproducible** — a threaded
  BLAS reduction sums in thread-completion order, so the same solve could differ
  in the last few ulp run to run — which is what lets the screening descent
  treat its finite differences as exact instead of paying for replicates.
- **Geo mesh honours "Max element size" as the actual element edge.** The CDT
  cell area was derived LINEARLY from the requested size (0.3·L instead of
  0.433·L²), so iron interiors always meshed ~2× finer than the slider said.
- **Rotor teeth now mesh at stator quality (q20) — root cause was a µm
  "zipper", not the sharp corner.** CadQuery discretises the shared
  iron-pocket/magnet boundary INDEPENDENTLY for the two polygons, leaving the
  two polylines 2–4 µm apart (a point-to-SEGMENT offset the PSLG vertex weld
  cannot see). Triangle was forced to bridge the µm strip with a fringe of
  micro-triangles along the wall — this is what blew up q-refinement
  (~5 500 Steiner points per pole), forced the area-only fallback (ragged fan
  texture), produced zero-area slivers and even a NaN solve crash. With corner
  fillets the same interleave concentrated ~2 300 micro-triangles at EVERY
  magnet top corner. Fix, two parts: (1) weld the magnet outline onto the iron
  chain (snap + residual point-to-segment projection with existing-vertex
  preference); (2) the shared pocket walls are then NOT re-added to the PSLG
  at all — the iron chain alone delimits them, and only the magnet's
  air-facing runs are added (`_air_facing_runs`), so every wall has exactly
  ONE sampling. Result (24s20p, Max = 2.75 mm, ¼ wedge, fillet 1.0): rotor
  45 116 → **2 390** triangles, median aspect 1.98 / p90 3.30 (stator:
  2.40 / 3.16), zero degenerates, zero micro-triangles, q20 converges with
  ARmax = 5 — also at a 0-fillet corner. Safety nets kept: budgeted q20 with
  area-only fallback, chord-clip defeaturing of <15° iron corners, edge-collapse
  pass for zero-area slivers (slip/shaft grid rings pinned for the belt weld).

### Added
- **"Air element size" slider** (Mesh → Solver Domain): element size for the
  open air (far-field, slot pockets, shaft core), auto = coarse; same store as
  the per-part "Outer air" field.

### Fixed (parity)
- **Full disk (n_sectors=1) and 1/4 sector now agree.** The transient solved the
  air gap with DIFFERENT band models depending on the sector count: the full
  ring silently forced the "moving" band (R1/R2 rings + one closed-form strip
  row) while sectors solved the "merged" single slip ring — two different gap
  couplings, so ns=1 vs ns=4 disagreed systematically (24s20p @ 100 A:
  torque −6.7 %, V_peak −22 %, efficiency +1.3 pp on the ring side). The band
  mode no longer depends on n_sectors: MERGED is the sole default for every
  sector count ("moving" remains opt-in via the harmonic macro or
  SB_MOVING_BAND=1). After the fix (structured gap, steps=120): torque
  29.20 vs 29.10 Nm (0.36 %), efficiency 93.89 vs 93.90 %, V_peak 71.4 vs
  71.0 V. Torque-spectrum comparison shows the ¼ wedge is the spectrally
  CLEAN solve (non-6k noise floor 0.02 % vs the ring's 1.66 %); the remaining
  ripple gap (19.9 vs 22.2 %) sits in the h24/h36 cogging orders which the
  ring's broadband numeric noise damps — see PARITY_FINDINGS_band_mode.md (private data repository).
  Verified on the geo (CDT) pipeline too: means within 0.6 %, h12/h30 within
  1–2 %. Tightening the ring's saturation Picard is NOT a fix (fixed-recipe
  iteration; 28 iters shifts T_avg +2.5 % and doubles the noise floor) — the
  ¼ sector is the ripple reference; the full ring stays valid for means and
  field maps.

## [0.1.9] — 2026-07-01

### Changed
- Mapped/structured air-gap mesh: the gap-facing iron boundary now conforms
  directly to the transfinite ring arcs — the ε-retract and the free-meshed
  filler strips are removed. Exact 2K uniform rings; the structured mean-torque
  deficit vs the free mesh drops from ~−20 % to −1..−6 % (the retract had been
  blunting the tooth tips). Free mode (structured gap off) is byte-for-byte
  unchanged.
- Unified section-header styling across the Geometry / Cost / Optimization
  panels via a shared `SectionLabel` token (were blue / light-grey).

### Added
- `GEO_UNBOUNDED` backend env flag lifts all geometry parameter min/max caps for
  large-motor exploration (default off → production caps unchanged).

## [0.1.8] — 2026-06-25

### Changed
- **Thermal cooling: pick Air or Liquid, with a physical model for each.** The Temp
  view's cooling control is now a method selector. **Air** adds a *blow-speed*
  selector (still / 2 / 5 / 10 / 20 / 30 m/s) → the housing convection h is computed
  from it (Churchill–Bernstein). **Liquid** takes the coolant + **inlet and outlet
  temperatures**: the outer contour (housing) is held at the **outlet** temp, and the
  **flow rate is computed automatically** from the energy balance ṁ = P_loss/(cp·ΔT)
  and shown read-only (smaller chosen ΔT → more flow). Replaces the old fixed-h preset
  dropdown.
- **Thermal map uses the full colour range (Fusion-style).** The temperature view
  now renders the commercial-FEM-style blue→cyan→green→yellow→red rainbow and, by default,
  **histogram-equalises** it — each node is coloured by its rank in the temperature
  distribution, so the whole spectrum lands on the structure even when the motor is
  a tight hot plateau (most of it within a few °C). The colour bar is labelled at the
  temperature quantiles so a colour still reads as a real °C. A new **equalised /
  linear** toggle (next to the Temp view) switches back to a faithful linear scale.
- **Air-gap thermal conductivity is now physical + speed-dependent.** It was a
  hardcoded 0.10 W/m·K; it's now computed from the gap Taylor number (Becker–Kaye
  Nusselt correlation) using the **rotor speed from the Simulation tab** and the gap
  geometry. At rest / low speed the gap is still-air conduction (~0.03 W/m·K); as the
  rotor spins fast enough, Taylor vortices stir the gap and raise the effective k.
  Bigger radius / wider gap / higher rpm → more enhancement (e.g. a thin-gap small
  motor stays laminar to ~25k rpm; a large machine enhances at a few thousand rpm).
  Pass `gap_k>0` to override with a fixed value.

### Fixed
- **Thermal: windings now run hot, as they should.** The slot was meshed ~1 element
  across, which thermally *shorted* the copper to the iron — the coil↔tooth ΔT was a
  dead ~1 °C no matter the insulation. The coil region is now auto-refined (~4 elements
  across the slot) so the winding gradient resolves, and the slot's effective
  conductivity is computed from the **real wire stack** — a volume-weighted *series*
  ("layered") mean of the stacked conductors and air-dominated inter-wire gaps — giving
  **≈0.18 W/m·K** instead of the old hardcoded 1.5 (a Maxwell copper-inclusion estimate
  would over-state it at ~0.4). Result: the coils are now the clear hotspot (e.g. 94 °C),
  ~5 °C above the slot-adjacent iron and ~17 °C above the cooled outer skin. Pass
  `slot_k>0` to override the auto value manually.

## [0.1.7] — 2026-06-25

### Fixed
- **Sweep-study chart now draws its points.** Under recharts v3 the objective chart
  rendered an empty plot (points + curves positioned outside the axes); it now drives
  the axes from the explicit data extent, so all designs + curves show.
- **Saved-motor card uses the Simulation result you see.** Creating/overwriting a
  motor now stamps the card with the live Simulation summary (sent as metrics),
  instead of falling back to a stale on-disk last-transient that could differ from
  the displayed (applied) result.

### Changed
- **Unified Apply buttons** across Optimize + Sweep — all green outlined with a ▶
  icon and the same "Apply picked point to geometry" wording.
- **Sweep table torque → 2 decimals** (0.57 / 0.60 / 0.61 … instead of all "0.6").
- **Removed the "Save this simulation" snapshot card** — motors are saved through
  the motor flow (Save as new / Overwrite), which captures geometry + the current
  simulation, so the second save path was redundant.

## [0.1.6] — 2026-06-25

### Added
- **Baseline-line objective for the optimizer — no more guessing the weights.** Two
  FEM sims of the start geometry (at the Simulation current I and at I·(1+bump%))
  define a "current-only" trade-off line in (torque/mass, efficiency) space. The
  optimizer now maximises the signed *perpendicular distance above* that line, so a
  design only wins if it beats what you'd get by just cranking current. The eff vs
  torque/mass weights come from the line's slope automatically (efficiency weighted
  by the T/mass gained per +current, T/mass by the efficiency lost per +current).
  It's the default objective; the legacy η × T/mass is a toggle.
- **"Draw baseline" button** — draws that reference line on the objective-space chart
  up-front (just the 2 sims), before launching a full optimization, with its A/B
  endpoints labelled by current and the auto-derived weights shown.

### Changed
- **Optimization variables now lay out in two columns** (was a single tall stack);
  the operating-point / ripple pane is narrowed to make room, and the stale
  "optimizer targets a torque" text is gone (it runs at the fixed Simulation current).

### Fixed
- **Simulation summary flags a stale operating point.** The Physics Dashboard doesn't
  recompute when you change the current/γ (by design — no surprise FEM), so it could
  show an old run's numbers and look like an optimizer↔Simulation mismatch. It now
  shows an amber banner — "shown for I = X A_rms, differs from the current setting
  (I = Z A) — press Run Simulation" — whenever the displayed result is for a
  different operating point than what's set. (At the *same* current the optimizer and
  Simulation are byte-identical; the mismatch was only the stale display.)

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
- **|B| field view matches the commercial FEM scale** — discrete blue->red bands in mTesla
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

## 2026-07-20 (late): ripple gauge fixed

- Saturation Picard: decaying damping (α=0.5 → 3/(it+1), floor 0.05)
  wired into the nu-update in fem_solver_2d.py. At n_pic≈40–100 the solution converges:
  mean(I=0) → 0, the torque spectrum is clean (6k family).
- Diagnosed by layer: (1) Picard non-convergence = 5–8 N·m of noise; (2) the sliding
  band adds ~60 % to h6 compared to the analytical macro-element;
  (3) the residual h6≈1.5 is real saturation cogging from the 12x stator
  (12 main + 12 auxiliary teeth, orders that are multiples of 60/rev).
- Honest figures (macro, n_pic=100): no-load p-p 2.75 N·m; load I=85
  γ=32: mean 27.4 N·m, ripple 11.7 % (band: 29.6 N·m, 17.6 %).
- Steel (JFE vs B15) and the coarse geometry of the reference point are not
  factors (±4 %).
- Details: PARITY_FINDINGS_band_mode.md (private data repository).

## 2026-07-21: ripple parity with the reference reached

- The config rotor was brought to the reference geometry (magnet_height 16,
  up_gap 2, fill_up 0.46, fill_radius 1, rotor_fill_r 2) — by the user.
- Honest measurement (macro + n_pic=100): load I=85 γ=32 → ripple 4.15 %,
  no-load cogging p-p 1.57 N·m (the reference comparison is kept private).
  The ripple discrepancy is CLOSED; the main driver was the rotor.
- Open: the macro mean torque is ~7 % below the band — extraction calibration.

## 2026-07-21: honest defaults — no filters, no recipes

- Saturation Picard: the fixed "14-iteration recipe" REMOVED. The loop now
  stops on the nu fixed-point residual (< 1e-3 for two sweeps in a row),
  ceiling 100. Diagnostics in every result: picard_iters_mean/max,
  picard_resid_max, picard_converged — the honesty of each run is visible,
  not assumed. Same for the demag pre-pass and the phasor vdrive init.
- Torque filter (6k band) OFF by default everywhere: the solver,
  em_transient_eval, simulation routes, the optimizer (run_one, DescentRequest,
  scan, refine), the frontend (checkbox, localStorage defaults). The headline
  T_ripple_pct is now raw. The filter remains only as an explicit UI option.
- WARNING: on existing browsers the checkbox may have persisted as enabled in
  localStorage ('torqueFilter') — uncheck it once in Simulation.

## 2026-07-21 (continued): adaptive Aitken relaxation in Picard

- The damping schedule (0.5 → 3/(it+1)) was replaced by Irons–Tuck
  relaxation (vector Aitken Δ²): the step is derived from the actual
  residuals, with no tuning constants. Anderson(m=4) was tried and dropped —
  at the B-H kink the secant model destabilizes the iteration.
- Validation (ring, macro, no-load, mesh 2.8): h6=0.5483 (ref. 0.549),
  mean=−0.0007, iters_mean=58.8, resid_max=1.4e-3 (tol 1e-3 — frames that did not
  reach tol at the 100 ceiling honestly report converged=False).
  Cost ~4× versus the old "14-recipe"; the physics is converged.
