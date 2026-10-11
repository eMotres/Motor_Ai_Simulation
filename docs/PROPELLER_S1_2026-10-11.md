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
fit, 1.34 × the highest fitted speed) lands within −0.4 / +1.5 K.

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

## Left open
- Ø85: one or two coupled FEM anchors (housed frame, propeller air) — they need the
  forced-air end faces on the housed path first (the D85 finding), then the same calibration.
- The slipstream factor 0.4 stays uncalibrated (needs a measurement: thermocouple or
  anemometer behind the hub).
- The S1 answer keeps the Si 48 kHz PWM extra loss of the passport drive variant (Ø40:
  4.4–9.2 W, +12…20 K); the anchors are sine-driven.
