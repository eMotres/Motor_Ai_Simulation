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
