# Propeller-loaded continuous (S1) rating — Stage A (2026-10-11)

A drone motor cooled only by its own propeller has one free variable, the speed n.
`motor_ai_sim.propeller_s1` finds the highest n at which the steady winding and magnet
temperatures stay inside their limits with a chosen propeller as both the load and the
cooling. No field solve: it reads a compact per-configuration model extracted from the
passport (`config/propeller_s1/<die>/<config>.json`, written by
`scripts/export_propeller_s1_model.py`) and the propeller catalogue.

## Method
At each n: Q_em = Q_prop(n) + P_mech(n)/ω; current from the passport torque map
(k_T(3-D) × T_grid(n, I) × k_ψ(T_magnet), the smallest I that makes Q_em); losses from the
passport loss grid (linear in I, power law between speeds) with DC copper 3I²R(T_w) and
AC copper scaled by ρ_ref/ρ(T_w); slipstream air v = 0.4 · sqrt(2T/(ρA)) (`behind_hub`);
housing film h = `cooling_models.outer_air`; temperatures from Configure's lumped estimate
(`thermalEstimate.ts`, same constants; rotor iron + magnet/shaft eddy cross the gap).
Iterated to the thermal fixed point (0.01 K); a scan of 41 speeds, then bisection to 1 rpm
at the last passing speed. Controller losses (inverter + board copper of the passport drive
variant) go to the battery power only.

Limits: winding 180 °C (class H, owner default); magnet 150 °C = F52SH_120C card
`max_working_temp_c` (SH class); current ≤ the passport demagnetisation current limit
(Ø40: 2.5·I0, no knee found at the hot magnet temperature) and ≤ the top of the grid; speed
≤ the passport voltage limit / end of its speed grid.

## Model sources
| configuration | torque / losses | R | flux vs T_magnet |
|---|---|---|---|
| Ø40 L12 | full v1 passport loss grid (settled TDM, demag), × k_T 0.979 | 9.227 mΩ @ 111.2 °C | ψ hot/cold → −0.027 %/K |
| Ø40 L20 | full v1 passport loss grid, × k_T 0.988 | 15.99 mΩ @ 199.6 °C | ψ hot/cold → −0.030 %/K |
| Ø85 L13 | passport STORE record only (the full record is lost): T_op and total sine loss of drive variant si_48k, computed at coil 180 °C / magnet 150 °C (checked: no negative non-copper rest) | 93.96 mΩ @ 149.3 °C (duty summary) | magnet card α(Br) −0.12 %/K |

## Result (25 °C air, ISA, behind_hub 0.4)
| motor / propeller | n S1 | thrust | P_shaft | P_motor in | I | T_w / T_m | binding | flags |
|---|---|---|---|---|---|---|---|---|
| Ø40 L12 + FPV 10×5 3-blade | 8 479 rpm | 1.73 kgf | 265 W | 292 W | 21.0 A | 156 / 150 °C | magnet | — |
| Ø40 L20 + P13×4.4 | 9 001 rpm | 2.42 kgf | 362 W | 395 W | 16.3 A | 150 / 150 °C | magnet | prop extrapolated (tested ≤ 6 061 rpm) |
| Ø85 L13 + G30×10.5 | ≥ 2 000 rpm | 4.30 kgf | 364 W | 393 W | 8.6 A | 99 / 92 °C | end of passport speed grid | prop extrapolated (tested ≥ 2 679) |
| Ø85 L13 + G32×11 (default) | ≥ 2 000 rpm | 5.31 kgf | 448 W | 493 W | 11.1 A | 138 / 126 °C | end of passport speed grid | prop extrapolated (tested ≥ 2 485) |
| Ø85 L13 + G36×11.5 | 1 775 rpm | 5.35 kgf | 420 W | 471 W | 12.1 A | 165 / 150 °C | magnet | prop extrapolated |
| Ø85 L13 + G40×13.1 | 1 367 rpm | 4.76 kgf | 318 W | 365 W | 11.8 A | 164 / 150 °C | magnet | prop extrapolated |

## What this is not
- The lumped network puts the magnet at about the housing temperature (all rotor heat
  crosses the gap). The Ø40 L12 passport's coupled FEM point (other cooling) had the
  magnet 58 K BELOW the winding, so the magnet binding here is probably conservative; it
  is the first thing Stage B must check.
- The slipstream factor 0.4 is an uncalibrated assumption (propeller.py).
- Ø85: the passport grid ends at 2 000 rpm, below every G-series tested range; the G30/G32
  answers are "at least 2 000 rpm". A passport extension above 2 000 rpm is needed.

## Stage B (2–3 coupled FEM anchors per motor)
At ≈ 0.7·n_S1, n_S1 and 1.15·n_S1: coupled EM↔thermal run with `air_speed_source:
propeller` at the request rpm (now allowed: the coupled loop takes the request rpm for the
EM solve, the loss-map lookup and the slipstream). Compare T_w / T_m and the current with
the S1 rows; fit one correction per motor (housing film multiplier or rotor-path
conductance), then re-run the bisection.

# Stage B — coupled FEM anchors and calibration (2026-10-11)

## Anchors (Ø40, server sandbox, `scripts/passport_full.py propanchor`)
Each anchor is the full coupled EM↔thermal loop (`routes.coupled.run`, `solve_to: steady`)
with the propeller as load and cooling: air mode, `air_speed_source: propeller` (factor 0.4,
behind_hub) at the request rpm, the machine's OPEN frame (end turns, slot channels, rotor
faces and the ventilated gap in the same wash, `open_air_speed_mps = 0`), no bore cooling,
25 °C air, eddy / rotor eddy / demag and 36 steps as saved on the machine, sine current
drive at the passport's operating angle. Current = the S1 model's; one correction step
(I·Q/T) when the 3-D torque (2-D × k_T) missed Q_prop + bearing/windage torque by > 2 %.

| motor | n rpm | I A | T_3D / Q | FEM W / M / stator °C | Stage A W / M | Stage A sine W / M | Stage B sine W / M | Stage B (+PWM) W / M |
|---|---|---|---|---|---|---|---|---|
| L12 | 5 935 | 9.74 | +1.0 % | 45.7 / 42.9 / 46.0 | 90.2 / 90.5 | 65.1 / 65.5 | 45.7 / 41.9 | 59.4 / 52.8 |
| L12 | 8 479 | 20.48 | −0.1 % | 72.6 / 59.7 / 72.0 | 156.2 / 150.0 | 127.8 / 122.4 | 73.3 / 59.7 | 87.3 / 70.2 |
| L12 | 9 751 | 27.70 | −0.0 % | 99.4 / 74.3 / 97.1 | 241.3 / 222.6 | 207.1 / 190.5 | 100.2 / 75.3 | 114.4 / 85.6 |
| L12 (validation) | 11 370 | 39.00 | −0.6 % | 164.4 / 108.6 / 157.0 | runaway | runaway | 164.0 / 110.1 | 180.0 / 120.9 |
| L20 | 6 301 | 7.81 | +0.7 % | 52.4 / 52.9 / 52.8 | 92.0 / 93.7 | 70.1 / 71.9 | 51.3 / 51.5 | 64.4 / 63.9 |
| L20 | 9 001 | 15.81 | +0.0 % | 77.2 / 75.3 / 77.3 | 149.7 / 150.0 | 117.8 / 118.6 | 76.3 / 74.2 | 94.2 / 90.8 |
| L20 | 10 351 | 21.03 | +0.1 % | 97.6 / 91.8 / 96.9 | 201.8 / 198.4 | 162.1 / 159.8 | 96.6 / 91.3 | 117.4 / 110.3 |

W = winding hot spot, M = hottest magnet element; each model column at its own operating
point (its own current, ≈ the FEM's). "Sine" = the S1 model without the drive variant's PWM
extra loss — the like-for-like comparison, since the FEM anchors are sine-driven; the S1
answer itself keeps the Si 48 kHz PWM extra (4.4–9.2 W, +12…20 K), the last column.
Stage B sine is within ±1.5 K of every anchor, the validation point included.
The loss grid matched the FEM at the same (n, I, T): within 1 % on L12, −2…−7 % on L20.

**Finding.** The losses were right; the lumped network was 20–110 K too hot. On the open
frame most heat leaves through the end turns in the wash (4.0 of 6.8 W at 5 935 rpm), the
slot channels, the rotor faces and the gap duct — paths Configure's housing-only network
does not have. The magnet is not at the housing temperature (Stage A's assumption): it
sits 0–25 K below the winding.

## Calibration (`thermal_calibration` in the S1 model; absent = Stage A exactly)
Three nodes (housing, winding, rotor) with two direct paths to the air besides the housing
surface, both scaling with the slipstream film h: end turns / slot channels `G_wa = h·A_w`,
rotor faces / gap duct `G_ra = h·A_r`, and the housing film × f_h. Fitted by least squares
(winding hot spot, hottest magnet, stator mean × 0.5) to the three anchors per configuration,
the network fed each anchor's own FEM losses (`scripts/export_propeller_s1_model.py calibrate`).

| config | f_h | A_w m² | A_r m² | max residual |
|---|---|---|---|---|
| L12 | 0.1729 | 4.328e-3 | 2.615e-3 | 0.9 K |
| L20 | 0.5185 | 4.106e-3 | 1.464e-3 | 1.1 K |

A_w is what the FEM's own end-turn film implies (L12: 1.34e-3 m² × h_end 145 / h_housing
45 = 4.3e-3 m²). The slipstream factor stays 0.4: the anchors use the same slipstream model,
so they cannot constrain it (a measurement can). The L12 anchor at 11 370 rpm (not in the
fit, 1.166 × the highest fitted speed 9 751 rpm — corrected 2026-10-11, the earlier "1.34 ×" was
wrong) lands within −0.4 / +1.5 K.

## S1 after calibration (25 °C air, ISA, behind_hub 0.4)
| motor / propeller | Stage A | Stage B | binding | I | T_w / T_m |
|---|---|---|---|---|---|
| Ø40 L12 + FPV 10×5 | 8 479 rpm, 1.73 kgf | 11 370 rpm, 3.19 kgf, 662 W shaft | winding 180 °C | 39.0 A | 180 / 121 °C |
| Ø40 L20 + P13×4.4 | 9 001 rpm, 2.42 kgf | ≥ 9 092 rpm, 2.47 kgf | end of the scan (1.5 × the prop's tested 6 061 rpm) | 16.3 A | 96 / 92 °C |

At 11 370 rpm the FEM shows the magnets partly past their knee at one corner (volume Br kept
99.32 %, i.e. < 1 % — inside the passport's demag rule).

## Ø85 L13 above 2 000 rpm (passport stage `loss4`, server sandbox)
The S1 model is now exported from the full 10-11 passport record
(`docs/data/passport_d85/passport_L13.json`: split loss groups at the card's hot state, magnet
69.2 °C / winding 75.1 °C, k_T 0.965, flux −0.032 %/K from ψ hot/cold, demag current limit
52.4 A). Stage A had read the OLD 10-06 store record (hot 180 / 150 °C, total loss only).
The loss grid is extended by 38 settled TDM + demag FEM points (60 steps/period, same hot
state, m 0.89 at 44.4 V, MTPA else fixed-current field weakening to 80°) at 2 125, 2 250,
2 375, 2 500, 2 750, 3 000, 3 500, 4 000 and 4 500 rpm × 6.47…45.96 A, 0 failed
(`docs/data/propeller_s1_stage_b/L13_loss4_points.json`).

**Voltage.** At 44.4 V / m 0.89 the back EMF reaches the voltage limit at ≈ 2 100 rpm. Above
it no current below a floor can hold the voltage: 0.85 / 4.95 / 8.15 / 10.75 / 17.85 /
25.8 A at 2 125 / 2 250 / 2 375 / 2 500 / 3 000 / 4 000 rpm (hot ψ-map,
`L13_floors.json`). The S1 model carries it as `grid.I_floor_A`: below the floor the point
is infeasible, the torque goes to 0 at the floor (conservative) and the losses follow the
first feasible interval; between two speed rows the larger floor applies. Configure flags
such a limit ("the drive carries field-weakening current"). The FEM points also show the
cost of that d-axis current: AC copper 19–72 W and shaft eddy loss up to 91 W at
3 000–4 500 rpm.

| propeller (tested rpm) | Stage A | Stage B S1 | thrust | shaft | I | T_w / T_m | binding |
|---|---|---|---|---|---|---|---|
| G28×9.2 (1 896–3 709) | ≥ 2 000 (grid end) | 2 290 rpm | 3.17 kgf | 251 W | 9.7 A | 159 / 150 °C | magnet, field weakening |
| G30×10.5 (2 679–4 654) | ≥ 2 000 | 2 125 rpm | 4.85 kgf | 436 W | 10.9 A | 161 / 150 °C | magnet, field weakening, prop extrapolated |
| G32×11 (2 485–4 397) | ≥ 2 000 | 2 017 rpm | 5.40 kgf | 459 W | 11.1 A | 162 / 150 °C | magnet, prop extrapolated |
| G36×11.5 (2 595–4 328) | 1 775 | 1 760 rpm | 5.26 kgf | 409 W | 11.1 A | 162 / 150 °C | magnet, prop extrapolated |
| G40×13.1 (1 843–3 546) | 1 367 | 1 381 rpm | 4.86 kgf | 328 W | 11.2 A | 162 / 150 °C | magnet, prop extrapolated |

25 °C air, housed frame, the uncalibrated lumped network (no Ø85 FEM anchor in this stage;
the Ø40 calibration belongs to an open frame and is not transferred). Battery power is not
given above 2 000 rpm: the drive variant's controller-loss grid ends there.

## Item 2 (forced-air end faces) — not needed for these anchors
The Ø40 runs in the OPEN-frame propeller path, which already puts the end turns, the slot
channels, the rotor end faces and the gap duct in the slipstream (`open_air_speed_mps = 0` =
the propeller's housing air speed). The housing-only limitation the D85 run found is the
HOUSED frame's (Ø85); it matters only for an Ø85 FEM anchor, which this stage did not run.

# Stage C — Ø85 housed frame: slipstream end caps + FEM calibration (2026-10-11)

## End caps in the slipstream (thermal solver)
A housed machine cooled by its own propeller (`cooling_mode = air`, `air_speed_source =
propeller`, `frame = housed`) now also cools its two END CAPS (`cooling_models.
housing_end_faces_slipstream`); every other mode (typed air speed, robotics, liquid, open
frame) is unchanged and keys the cache as before.

    A_face  = π/4 · (D_housing² − d_shaft²)          (Ø85: D 85 mm, d 49.2 mm → 3.77e-3 m² per face)
    h_front = outer_air(v, D_housing)                 (the housing side's own Churchill–Bernstein film)
    h_rear  = outer_air(0.5 · v, D_housing)           (the wake behind the motor — an assumption)
    G       = (h_front + h_rear) · A_face             (whole machine, W/K)

v is the slipstream speed at the run's rpm (the housing side's). The caps are taken at the
housing temperature (fin efficiency 1: aluminium cap, mL ≈ 0.3), so G is a second film on
the housing facets of the 2-D section (spread over the facets the mesh really has), reported
as `cooling.housing_end_faces` (per-face speed, h, area, watts) and on its own budget lines
(`housing_end_faces_W`); the budget closes with it. On the Ø85 the faces are 2.2 × the
side area: they carry 61–62 % of the heat at every anchor.

## Anchors (`passport_full.py propanchor --frame housed`, server sandbox; records in `docs/data/propeller_s1_stage_c/`)
L13, G32×11 unless stated, 25 °C air, factor 0.4 behind_hub, eddy / rotor eddy / demag on,
60 steps per period (the passport's settled-loss setting; the duty's 120 doubles the cost),
sine drive at the passport's operating angle, `solve_to = steady`.

| n rpm | I A | T_3D / Q | FEM W / M / stator °C | before (Stage B) W / M | after W / M | role |
|---|---|---|---|---|---|---|
| 1 250 | 4.16 | +0.1 % | 81.4 / 85.2 / 79.5 | 65.2 / 64.1 | 81.7 / 85.7 | fit |
| 1 412 | 5.22 | −1.3 % | 94.1 / 98.2 / 91.6 | 74.5 / 72.5 | 94.3 / 98.6 | fit |
| 1 600 | 6.76 | −1.6 % | 114.0 / 117.9 / 110.3 | 89.1 / 85.5 | 114.0 / 118.0 | fit |
| 1 800 | 9.23 | −3.5 % (1 correction) | 148.2 / 150.5 / 142.4 | 114.6 / 107.2 | 148.2 / 149.7 | fit |
| 2 017 | 11.09 | −35 % (demag) | 180.4 / 186.0 / 172.1 | 138.4 / 129.2 | 179.7 / 185.1 | check, out of range |
| G28 2 290 | 9.70 | −6.5 % (demag) | 165.9 / 169.7 / 159.0 | 127.3 / 119.4 | 165.7 / 169.1 | check (G28) |
| 2 320 | 20.7 | — | runaway (> 400 °C) | — | — | no steady state |

Network columns are fed each anchor's own FEM losses (like for like). The brief's 1.0× and
1.15× of the Stage B S1 (2 017 / 2 320 rpm) have no steady state inside the magnet card: at
2 017 the hot magnets demagnetise (Br down to 0.12 × on 88 % of the elements) and the torque
falls 35 % short; 2 320 runs away. The fit anchors were therefore moved to 1 250–1 800 rpm,
around the new S1; 2 017 and G28 2 290 stay as checks (their first pass is a converged
thermal state).

## Calibration — housed form
The open form (f_h, A_w, A_r) cannot fit a housed frame: the FEM magnet sits ABOVE the
stator (6–8 K at 1–2 W of rotor heat, ≈ 5.7 K/W against the lumped gap's 1.9 K/W) and A_r
can only cool the rotor (the open fit drives A_r to 0 and misses the stator by 5 K). The
housed form keeps f_h and A_w and replaces A_r by a gap resistance factor f_g (the rotor's
only exit is the gap): `export_propeller_s1_model.py calibrate --form housed`.

| f_h | A_w m² | f_g | max residual (fit) | checks |
|---|---|---|---|---|
| 0.4571 | 3.147e-3 | 3.040 | 0.8 K | ≤ 1.1 K |

f_h < 1: the lumped surface counts two full discs at the side's film; the FEM has two cap
annuli, the rear in the wake. A_w is a fit term (the lumped slot resistance is too high
against the meshed slot), not an air path. Slipstream factor kept at 0.4 (not constrained).

## S1 after calibration (25 °C air, ISA, behind_hub 0.4)
| propeller | Stage B | Stage C | thrust | shaft | I | T_w / T_m | binding |
|---|---|---|---|---|---|---|---|
| G28×9.2 | 2 290 rpm | 2 142 rpm | 2.77 kgf | 207 W | 5.9 A | 140 / 150 °C | magnet, field weakening |
| G30×10.5 | 2 125 | 1 861 | 3.72 kgf | 293 W | 7.6 A | 144 / 150 °C | magnet, prop extrapolated |
| G32×11 | 2 017 | 1 722 | 3.93 kgf | 286 W | 8.0 A | 145 / 150 °C | magnet, prop extrapolated |
| G36×11.5 | 1 760 | 1 511 | 3.88 kgf | 259 W | 8.2 A | 147 / 150 °C | magnet, prop extrapolated |
| G40×13.1 | 1 381 | 1 204 | 3.69 kgf | 217 W | 8.6 A | 150 / 150 °C | magnet, prop extrapolated |

The FEM itself reaches the magnet limit at ≈ 1 780 rpm with the G32 (1 800 rpm: 150.5 °C,
torque 3.5 % short), so the S1 answer is ≈ 3 % conservative. The reason is the loss grid,
not the network: at the anchors' own current and temperature its AC copper reads 4.7–8.3 W
against the FEM's ≈ 5 W and grows with speed (total loss +3 % at 1 250, +16 % at 1 800 rpm).

**Configure flag.** The model now carries `anchor_air_speed_ms` (3.25–4.69 m/s); the S1 limit
says `calibration_in_range` (± 20 %), and Configure shows "calibrated" inside it and
"calibrated at other slipstream speeds" outside. All five G limits are inside. The Ø40 models
carry no range (exported before) and keep the plain flag.

## Left open
- Ø85 loss grid AC copper vs the coupled anchors (above): a loss-side correction would lift
  the S1 by ≈ 3 %.
- The rear-face speed factor 0.5 and the slipstream factor 0.4 are assumptions (need a
  measurement: thermocouple or anemometer behind the hub).
- The S1 answer keeps the Si 48 kHz PWM extra loss of the passport drive variant (Ø40:
  4.4–9.2 W, +12…20 K); the anchors are sine-driven.

# Review fixes — a system (motor + controller + battery) rating (2026-10-11)

Independent review: `docs/reviews/REVIEW_PROPELLER_S1_ASTRA_2026-10-11.md` (8 findings). Model schema
`propeller-s1-model-2`; the three models re-exported with `scripts/export_propeller_s1_model.py` (loss
grids, torque, floors and calibration constants unchanged; L20 flux slope magnitude +2.9 %).

| # | finding | fix |
|---|---|---|
| 1 | controller / battery limits dropped | every computed drive variant exported (`drives`: board continuous current per cooling class, controller-loss and PWM-extra grids on the variant's own grid, ok flags, bus range) + the die's controller block for sine (`sine_controller`) + the passport pack. The route takes `drive` (Configure's selected variant, default `sine`), `board_cooling` (default favourable), `pack_v_min/max`, `pack_i_max_A`. New bindings `controller`, `battery_current`; `drive_feasibility` checked / unknown (motor-only) / failed (pack outside the drive's bus range). |
| 2 | electrical limits frozen at the reference state | at every iteration the grid's operating point is re-checked on the passport's hot psi-map (exported as a regular (I, γ) table) at the MINIMUM pack voltage, R(T_w) and the PM flux at T_m; if only a deeper field-weakening angle fits, the smallest current that still makes the torque is used (`voltage_deeper_fw`), else `voltage`. Demag limit at T_m: the probe value held below its temperature, scaled above it by the magnet card's knee margin 0.97·HcJ(T) − Br(T)/(μ0(μ_rec + Pc)), Pc = 2 (`demag_basis: reference temperature only`). |
| 3 | qualifications lost | `limit.flags` + `limit.qualification` (thermal calibrated / domain, slipstream factor + position uncalibrated, propeller extrapolated / estimated, PWM / mech / controller losses included or omitted, loss-grid extrapolation, every drive check) and `limit.cooling`; the Configure S1 tooltip lists them (EN + zh-CN). |
| 4 | calibration domain | `thermal_calibration.fit_domain` / `validation_domain` (rpm, air speed, propellers, max residual); `thermal_domain` = fit / validated / extrapolated, no ±20 % margin any more. L12 fit 5 935–9 751 rpm, check 11 370 rpm (residual 2.1 K); L20 fit only; L13 fit 1 250–1 800, checks 2 017 / G28 2 290. The 1.34 × above is 1.166 ×. |
| 5 | lower bound shown as "up to" | `limit.lower_bound` + `range_end` (propeller_data / motor_speed_grid / requested); Configure: "≥ N rpm · limit not reached (end of propeller data)". |
| 6 | missing losses become 0 | the exporter writes null for a missing required term (`loss_groups` lists grid / lumped / omitted); a null cell refuses the point (`loss_data`); `below_loss_grid_speed` and `_current` flagged separately (L12 at 3.70 A is now flagged). |
| 7 | nonconvergence = runaway | `nonconverged` (a second, damped attempt first) separate from `runaway`; the scan adds both sides of every loss-/drive-grid speed and the prop's tested-range ends, bisects every pass/fail edge and looks inside failing pairs with different reasons. |
| 8 | flux slope normalisation | `(ψ_h − ψ_c)/(ψ_h (T_h − T_c))`; both reference endpoints reproduced (test). |

## S1 before → after (25 °C air, ISA, behind_hub 0.4, passport pack, favourable board)
"Before" = the deployed model (motor only, si_48k PWM extra). "After" = sine drive, then each computed
PWM variant (same binding unless stated).

| motor + propeller | before | after, sine | after, PWM variants |
|---|---|---|---|
| L12 + FPV 10×5 | 11 680 (41.4 A, winding 200 °C) | 9 487 (26.1 A, controller) | si_48k 9 475 · GaN 48 k IGC016K10S2 9 638 / IGC019S06S1 9 648 · GaN 100 k 9 500 / 9 512 — all controller |
| L20 + P13×4.4 | ≥ 9 092 (end of prop data) | ≥ 9 092 (16.2 A) | si_48k, GaN 48 k, GaN 100 k: ≥ 9 092 |
| L13 + G28×9.2 | 2 142 (5.9 A, magnet) | 1 804 (6.7 A, magnet, deeper FW at 36 V) | 1 804 (all three) |
| L13 + G30×10.5 | 1 861 (7.6 A, magnet) | 1 731 (7.8 A, magnet, deeper FW) | 1 731 |
| L13 + G32×11 | 1 722 (8.0 A, magnet) | 1 686 (8.0 A, magnet, deeper FW) | 1 686 |
| L13 + G36×11.5 | 1 511 (8.2 A, magnet) | 1 511 (8.2 A, magnet) | 1 511 |
| L13 + G40×13.1 | 1 204 (8.6 A, magnet) | 1 202 (8.5 A, demag at 149 °C) | 1 202 |

- L12: the controller (26.1 A Si / 27.1 A GaN 48 kHz, favourable board) binds ~2 200 rpm below the old
  winding limit, well inside the thermal fit domain. Motor-only (no controller) the limit would be
  11 714 rpm, demag-bound (41.6 A at a 122 °C magnet, scaled from the 53.5 °C probe) and thermally
  extrapolated (beyond the 11 370 rpm check).
- L13: above ≈ 1 750 rpm the 36 V minimum pack (hot magnets) needs field weakening the 44.4 V grid did
  not; the extra d-axis current heats the magnets sooner. The Ø85 variants' motor PWM extra is 0 W
  (below the FEM's resolution) and their 34–40 A boards do not bind, so PWM = sine.
- Flags on every answer: slipstream 0.4 uncalibrated; battery continuous current not checked (no rating
  in the pack); L13 bearings/windage omitted, demag scaled from one probe temperature.
