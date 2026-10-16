# Ø40 passport — full card (stages 1–3), L12 and L20 — 2026-10-05

Branch `feat/passport-pilot-d40` (PR #106). Model: Claude Opus 5.5. Deliverable: HTML
cards `L12.html`, `L20.html`, `compare.html` (owner's Downloads folder,
`Passport_D40_2026-10-05`); records `docs/data/passport_pilot_d40/full/passport_{L12,L20}.json`.
Generator: `scripts/passport_card_d40.py` (post-processing only) on the runs of
`scripts/passport_pilot_d40.py` and `scripts/passport_full_d40.py`.

## Decisions taken (defaults pending owner, labelled on every number)

| # | default | what was done |
|---|---|---|
| 1 | card torque = operating torque | operating (settled TDM, demag) × k_T(3-D); the static-map value and k_state are shown beside it |
| 2 | demag current limit = highest current after which the rated-point torque drops ≤ 1 % | FEM: overload on the hot MTPA line, then the rated point on the SAME damaged magnet (the solver's sweep seed carries the Br ratchet); plus Br-volume retention 99.5 / 99 / 98 % |
| 3 | L20 peak = L12 peak/rated ratio | 59.93 A at 13 000 rpm |
| 4 | L12 hot temperatures from a converged coupled EM-thermal loop | converged in 3 iterations: coil 111.2 °C, magnet 53.5 °C (stage 1 had 100.6 / 107.2 °C) — the whole L12 hot passport was re-solved at these temperatures; stage 1 kept as `L12_stage1` |
| 5 | m = 0.89 | the loss trajectory re-planned at 0.89 (`loss_plan2`); FW points re-solved |

## What changed against stage 1, and why

- **L12 re-based**: magnet 107.2 → 53.5 °C moves torque and losses; the
  d-axis and the gap-layer level (geometry/mesh only) and the 20 °C set are
  carried over; everything hot was re-solved (hot grid, demag probes, loss
  grid, checks; 13/13 static and all loss checks pass).
- **3-D**: the warm-started 7-point Stage A sweep cost 69 min per stack length
  under load and was stopped after 6 mm; the card uses the 12 mm reference
  (Picard 2.2e-3 < 3e-3) and a separate 20 mm run with its 2-D leg.
  k_T and k_L are the stored Stage B values (2026-08 geometry, other
  materials), scaled by the end-region law — labelled "inherited".
- **PWM FEM**: the PWM step count (20 samples per carrier) raises the slip
  ring to ≥ 640 nodes/period; netgen then rejects the iron-template wedge and
  the build falls back to gmsh (solver-flagged DEGRADED; the shaft skin layer
  is not resolved) whose rotor mesh is not pole-periodic, so the route's sine
  reference (TDM) was refused. Both the PWM run and its sine reference now
  march (`SB_EDDY_METHOD=march`) on that same mesh — the extra loss is a
  same-mesh, same-method difference. First attempt kept as `attempt1_*`.
- **SPICE (Si)**: OptiMOS 6 double-pulse tables generated in ngspice-44.2 on
  the server with the controller's own gate drive and loop; the Si cards
  now carry `switching_source: spice`. **GaN**: Infineon publishes no SPICE
  model for IGC019S06S1 / IGC016K10S2 and no documents at all for
  IGD015S10S1 — GaN switching stays on the datasheet times-and-charges model
  + E_oss (labelled); manifests record `not_published`.
- **Board model** (estimate): one node calibrated on the controller
  project's Si ratings (26.1 / 20.2 A at the board limit 110 °C, heatsink
  95 °C, T_j 110 °C; board copper from the R30 study + coil links). It gives
  T_j, the board-limit current per cooling class and the continuous power for
  every device on the same board. Points above the board limit have no steady
  state: their losses are evaluated at the design T_j 110 °C and flagged
  `continuous_ok: false`.

## Results (hot; m = 0.89; nominal bus)

| | L12 (6S, 12 mm) | L20 (12S, 20 mm) |
|---|---:|---:|
| hot magnet / winding | 53.5 / 111.2 °C (coupled loop) | 113.6 / 199.6 °C (rated duty) |
| rated torque: card / operating 2-D / virgin map | 0.620 / 0.633 / 0.640 N·m | 1.227 / 1.243 / 1.267 N·m |
| rated shaft power, η shaft (sine) | 862 W, 92.6 % | 1691 W, 91.3 % |
| peak (card) | 0.703 N·m at 14 400 rpm, 48.79 A | 1.383 N·m at 13 000 rpm, 59.93 A |
| k_ψ / k_T / k_L | 0.947 / 0.979 / 1.063 | 0.968 / 0.988 / 1.038 |
| Kv cold 2-D / ÷k_ψ | 764.4 / 806.9 rpm/V | 458.8 / 473.9 rpm/V |
| demag limit (≤ 1 % rated-torque drop) | ≥ 107 A (2.5·I0: −0.37 %) | 79.3 A (1.5·I0: −0.95 %, 2·I0: −3.6 %) |
| rotor stress SF = 1 / SF at rated | 41 637 rpm / 7.2 | 41 693 rpm / 6.7 |
| critical speeds | none forward up to 39 000 rpm (beam: default pending owner) | same |
| motor PWM extra, 48 kHz (FEM, rated / peak) | 5.3 / 5.0 W | 12.3 / 12.5 W |
| same, GaN 48 kHz 20 ns dead time | 5.3 / 5.0 W | 12.3 W |
| inverter loss at rated (Si / GaN 48 kHz) | 16.0 / 14.5 W | 47.0 / 23.0 W |
| board-limit current, favourable airflow (Si / GaN 48 / GaN 100) | 26.1 / 27.1 / 26.3 A | 20.2 / 25.0 / 23.5 A |
| continuous shaft power at rated speed (Si / GaN 48) | 526 / 547 W | 665 / 821 W |

System limit: the motor's rated current (42.8 / 52.6 A) is far above the
board's continuous limit (26.1 / 20.2 A with the Si build) — the controller
board, not the motor, sets the continuous rating. GaN raises the 12S board
limit by ~24 % (conduction halves); at 6S the gain is ~4 %.

Server budget: stage 1 2.32 h container wall + full card 4.75 h (one container,
`--cpus 8`, nice 19) = 7.1 h. SPICE: 416 double pulses (one failed, uniform
set). 100 kHz PWM FEM: two runs stopped after 2 h — not stuck in meshing
(corrected 2026-10-05 evening): a ~(steps/period)² cost cliff, see
`docs/BUG_PWM_100K_COST_2026-10-05.md`; estimate used, labelled.

## Addendum — drive-variant grid (2026-10-05 evening, feat/passport-d40-pwm-grid)

Configure refused below 42.8 A (variants covered only rated..peak). The loss
trajectory now spans the static current levels 0.25…2.5·I0 up to the demag
limit (L12 10.7…107 A, L20 13.1…78.8 A) at every loss-grid speed + the peak
duty speed (`loss_plan3`, settled FEM points); every variant carries the full
(rpm × I_A) grid. Motor PWM extra loss: FEM anchors at 0.5·I0, I0, peak and the
top level (Si 48 kHz and GaN 48 kHz), k = dP/HDF(m) linear in I. Infeasible
nodes (voltage limit beyond γ = 80°, e.g. L12 I0 at 22 207 rpm — that speed is
the peak current's limit) carry a `status`; where the board-limit current
cannot run at a speed, `p_cont_max_status` says so.

## Rejected

- Using the solver's `inc_ldq` as the small-signal inductance (bug note
  `docs/BUG_LDLQ_REPORTED_2026-10-05.md`).
- Interpolating the PWM extra loss from the matched-sine TDM run on the
  normal mesh (cross-mesh, cross-method difference).
- 100 kHz motor-side FEM: ≈ 5–7 h per run (cost ∝ (steps/period)², see the
  bug note); the 100 kHz motor PWM loss is the 48 kHz FEM × (48/100)^1.5 with the
  range (48/100)^2…(48/100)^1 stated on every point (ESTIMATE label).
- Warm-started 7-length Stage A sweep (69 min per length under load).
