# Changelog

All notable changes to **Motor AI Simulator** are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com); versioning follows [SemVer](https://semver.org).
The single source of truth for the current version is the `VERSION` file at the repo root;
cut a release with `scripts/release.ps1` (see `docs/RELEASES.md`).

## [Unreleased]

### Added
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
  ring's broadband numeric noise damps — see PARITY_FINDINGS_band_mode.md.
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
  now renders the Ansys-style blue→cyan→green→yellow→red rainbow and, by default,
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

## 2026-07-20 (поздно): починен измеритель пульсаций

- Пикар насыщения: затухающее демпфирование (α=0.5 → 3/(it+1), пол 0.05)
  вжито в nu-update fem_solver_2d.py. При n_pic≈40–100 решение сходится:
  mean(I=0) → 0, спектр момента чистый (семейство 6k).
- Диагноз слоями: (1) несходимость Пикара = 5–8 Н·м шума; (2) скользящая
  полоса добавляет ~60 % к h6 против аналитического макроэлемента;
  (3) остаток h6≈1.5 — реальный сатурационный коггинг 12-кратного статора
  (12 главных + 12 вспомогательных зубьев, порядки кратны 60/об).
- Честные цифры (макро, n_pic=100): no-load p-p 2.75 Н·м; нагрузка I=85
  γ=32: mean 27.4 Н·м, ripple 11.7 % (полоса: 29.6 Н·м, 17.6 %; ANSYS:
  29.37 Н·м, 3.44 %).
- Сталь (JFE vs B15) и крупная геометрия точки ANSYS — не факторы (±4 %).
- Детали: PARITY_FINDINGS_band_mode.md.

## 2026-07-21: паритет пульсаций с ANSYS достигнут

- Ротор конфига приведён к ANSYS (magnet_height 16, up_gap 2, fill_up 0.46,
  fill_radius 1, rotor_fill_r 2) — пользователь.
- Честный замер (макро + n_pic=100): нагрузка I=85 γ=32 → ripple 4.15 %
  (ANSYS 3.44 %), no-load коггинг p-p 1.57 Н·м (ANSYS ~1.09 с их шумом).
  Расхождение по пульсациям ЗАКРЫТО; главный драйвер был ротор.
- Открыто: mean момента макро на ~7 % ниже полосы — калибровка экстракции.

## 2026-07-21: честные дефолты — без фильтров и рецептов

- Пикар насыщения: фиксированный «рецепт 14 итераций» УДАЛЁН. Теперь цикл
  останавливается по невязке неподвижной точки nu (< 1e-3 два свипа подряд),
  потолок 100. Диагностика в каждом результате: picard_iters_mean/max,
  picard_resid_max, picard_converged — честность каждого прогона видна,
  а не предполагается. То же для demag-препасса и фазорного инита vdrive.
- Фильтр момента (6k-полоса) по умолчанию ВЫКЛЮЧЕН везде: солвер,
  em_transient_eval, маршруты симуляции, оптимизатор (run_one, DescentRequest,
  scan, refine), фронтенд (чекбокс, localStorage-дефолты). Заголовочная
  T_ripple_pct — теперь сырая. Фильтр остался только как явная опция UI.
- ВНИМАНИЕ: у существующих браузеров чекбокс мог сохраниться включённым в
  localStorage ('torqueFilter') — снять галку один раз в Simulation.

## 2026-07-21 (продолжение): адаптивная релаксация Айткена в Пикаре

- Расписание демпфирования (0.5 → 3/(it+1)) заменено релаксацией
  Иронса–Така (векторный Aitken Δ²): шаг подбирается из фактических
  невязок, настроечных констант нет. Anderson(m=4) испытан и отброшен —
  на изломе B-H секущая модель разносит итерацию.
- Валидация (кольцо, макро, no-load, сетка 2.8): h6=0.5483 (реф. 0.549),
  mean=−0.0007, iters_mean=58.8, resid_max=1.4e-3 (tol 1e-3 — кадры, не
  дошедшие до tol на потолке 100, честно репортят converged=False).
  Стоимость ~4× против старого «рецепта 14»; физика сошедшаяся.
