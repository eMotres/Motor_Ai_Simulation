# Motor passport pilot — Ø40 CIANO14 40 new, stage 1 (2026-10-05)

Stage 1 = plain 2-D, sine current drive, no 3-D end correction, no PWM, no controller losses; every value below carries these labels in the record.

Records: `docs/data/passport_pilot_d40/passport_<M>.json` (full precision, method + run ids per value). Spec: `docs/PASSPORT_ALGORITHM.md` v1.1.

Verdict: 23/23 static off-grid checks pass on ψ and T (2 needed one P04 refinement; 1 ripple miss), 13/13 evaluated loss checks pass (L12 peak duty only after the analytic copper-temperature correction; 3 refused as outside the trajectory); Coulomb vs terminal work ≤ 0.03 %, 60° window vs full period ≤ 0.02 %. Main open item: map (virgin) vs operating torque, 1.7–1.9 %.

## Machines and frozen inputs (M0)

|  | pack | bus min/nom/max | rated (I0, n0) | peak | hot magnet / coil | k_end | mesh / min / sectors | gap layers map / loss | d-axis ° | snapshot |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| L12 | 6S | 18 / 22.2 / 25.2 V | 42.78 A @ 13000 rpm | 48.79 A @ 14400 rpm | 107.2 / 100.6 °C | 1.759 | 1 mm / 0.3 / 2 sect. | 2 / 1 | 59.9911 | `685206ed5b28e44c` |
| L20 | 12S | 36 / 44.4 / 50.4 V | 52.55 A @ 13000 rpm | 59.93 A (assumed) | 113.6 / 199.6 °C | 1.456 | 1 mm / 0.3 / 2 sect. | 3 / 1 | 59.9911 | `0a8612075047ec23` |

Materials: F52SH_120C, 20SW1200 stator + rotor, Steel_42CrMo4_QT shaft, copper, Nomex liner, polyimide enamel (deployed library; PR #51 not merged).

## Card values

| quantity | unit | L12 | L20 | method |
|---|---:|---:|---:|---:|
| rated torque, operating (hot) | N·m | 0.6164 | 1.242 | settled TDM + demag FEM at n0, I0, operating γ |
| rated torque, map (hot, virgin) | N·m | 0.6269 | 1.267 | static ψ-map, same (I0, γ) |
| rated k_state = demag × rest | - | 0.98336 = 0.99242 × 0.99087 | 0.98055 = 0.98710 × 0.99337 | operating / map; demag-off TDM run splits it |
| rated γ / mode | ° | 6.34 / mtpa | 7.45 / mtpa | v_nom, m = 0.95 |
| rated shaft power | W | 839.1 | 1691 | T·ω − P_mech |
| rated EM loss | W | 66.13 | 160.2 | Cu DC+AC, iron, magnet, shaft |
| rated mech loss | W | 0.1409 | 0.1866 | bearings + windage, analytic |
| rated η at the shaft | % | 92.68 | 91.34 | one efficiency |
| peak current | A rms | 48.79 | 59.93 | owner duty (L12) / assumption (L20) |
| peak speed | rpm | 14400 | 13000 |  |
| peak γ / mode | ° | 7.01 / mtpa | 8.23 / mtpa | v_nom, m = 0.95 |
| peak torque, map (hot, virgin) | N·m | 0.7109 | 1.430 | map at operating γ |
| peak torque, operating (hot) | N·m | 0.6994 (k_state 0.98378) | 1.400 (k_state 0.97885) | map × loss-trajectory state factor |
| peak η at the shaft | % | 92.64 | 90.44 | operating T + interpolated losses |
| ψ_PM cold / hot | mWb | 1.030 / 1.004 | 1.717 / 1.668 | no-load, fundamental; ÷k_3d not applied |
| Kv (line pk) cold / hot | rpm/V | 764.4 / 784.7 | 458.8 / 472.1 | no-load, fundamental; ÷k_3d not applied |
| Kt cold 0.25·I0 / cold I0 / hot I0 | N·m/A | 0.01530 / 0.01510 / 0.01465 | 0.02548 / 0.02500 / 0.02411 | MTPA vertices |
| Km cold | N·m/√W | 0.1058 | 0.1491 | T/√P_cu,DC at 20 °C |
| R phase 20 °C / hot | mΩ | 6.793 / 8.945 | 9.371 / 15.99 | incl. end windings (k_end) |
| Ld / Lq bench small-signal cold / hot | µH | 3.382 / 5.411 ; 3.775 / 5.703 | 5.639 / 9.014 ; 6.354 / 9.543 | ±2 A pairs, Δψ/Δi (differential) |
| Ld / Lq solver inc_ldq at 2 A (cold) | µH | 8.263 / 9.163 | 13.78 / 15.29 | frozen secant ν — not the bench value |
| Ld / Lq differential (hot I0) | µH | 4.072 / 5.679 | 7.194 / 9.682 | ∂ψ/∂i of the map |
| Ld / Lq incremental (hot I0) | µH | 9.126 / 9.718 | 15.41 / 16.26 | frozen permeability at the point |
| cogging p-p cold / hot | mN·m | 74.20 / 66.75 | 123.5 / 110.7 | no-load full period |
| characteristic current (cold) | A rms | 113.5 | 113.4 | ψd = 0 on the d-axis |
| steady SC current @ n0 (cold) | A rms | 116.9 | 119.3 | both dq equations with R |
| base speed @ I0, v_min / v_nom / v_max | rpm | 12559 / 15655 / 17867 | 14927 / 18623 / 21263 | MTPA voltage = limit |
| max speed @ I_peak, v_min / v_nom / v_max | rpm | 20847 / 25820 / 29370 | 31004 / 38453 / 43772 | FW to γ 80° (grid limit) |
| cold bus-crossing speed @ v_max | rpm | 19262 | 23121 | Kv_cold·v_max — not a mechanical runaway |
| mechanical speed limit | rpm | 45000 | 45000 | bearing rating only (critical speed / stress not in stage 1) |

Hot = rated-duty magnet / winding temperature; cold = 20 °C. Map torque = virgin magnets; operating torque = demag steady state + rotor eddy reaction.

## Hot ψ-map: MTPA line (FEM-confirmed vertices)

**L12** — 70 map points; dq identity ≤ 0.14 %; window 60° el, 24 positions

| I/I0 | I A | γ_MTPA ° | T N·m | vertex vs parabola |
|---|---:|---:|---:|---:|
| 0.250 | 10.70 | 1.65 | 0.159310 | +0.0005 % |
| 0.500 | 21.39 | 3.28 | 0.317562 | +0.0008 % |
| 0.750 | 32.09 | 4.86 | 0.473724 | +0.0011 % |
| 1.00 | 42.78 | 6.34 | 0.626868 | +0.0014 % |
| 1.50 | 64.17 | 8.71 | 0.917059 | +0.0019 % |
| 2.00 | 85.56 | 11.1 | 1.17174 | +0.0027 % |
| 2.50 | 107.0 | 13.3 | 1.38165 | +0.0034 % |

**L20** — 72 map points; dq identity ≤ 0.40 %; window 60° el, 24 positions

| I/I0 | I A | γ_MTPA ° | T N·m | vertex vs parabola |
|---|---:|---:|---:|---:|
| 0.250 | 13.14 | 2.03 | 0.325177 | +0.0007 % |
| 0.500 | 26.27 | 4.00 | 0.647012 | +0.0012 % |
| 0.750 | 39.41 | 5.85 | 0.962405 | +0.0016 % |
| 1.00 | 52.55 | 7.45 | 1.26713 | +0.0023 % |
| 1.50 | 78.82 | 10.2 | 1.81790 | -0.0018 % |
| 2.00 | 105.1 | 13.0 | 2.26151 | -0.0037 % |
| 2.50 | 131.4 | 14.7 | 2.59444 | +0.0036 % |

## Operating envelope at the bus (hot map, I ≤ I_peak, FW to 80°, m = 0.95)

**L12** — max virgin-map torque [N·m] vs speed (× k_state ≈ 0.98 for operating)

| n rpm | v_min | v_nom | v_max |
|---|---:|---:|---:|
| 3250.0 | 0.7109 (MTPA) | 0.7109 (MTPA) | 0.7109 (MTPA) |
| 6500.0 | 0.7109 (MTPA) | 0.7109 (MTPA) | 0.7109 (MTPA) |
| 9750.0 | 0.7109 (MTPA) | 0.7109 (MTPA) | 0.7109 (MTPA) |
| 13000 | 0.6989 (FW 17.1°) | 0.7109 (MTPA) | 0.7109 (MTPA) |
| 16250 | 0.5039 (FW 48.9°) | 0.6985 (FW 17.2°) | 0.7109 (MTPA) |
| 19500 | 0.2656 (FW 70.1°) | 0.5465 (FW 44.2°) | 0.6684 (FW 25.9°) |
| 26000 | — | — | 0.3573 (FW 62.6°) |
| 32500 | — | — | — |
| 39000 | — | — | — |

**L20** — max virgin-map torque [N·m] vs speed (× k_state ≈ 0.98 for operating)

| n rpm | v_min | v_nom | v_max |
|---|---:|---:|---:|
| 3250.0 | 1.430 (MTPA) | 1.430 (MTPA) | 1.430 (MTPA) |
| 6500.0 | 1.430 (MTPA) | 1.430 (MTPA) | 1.430 (MTPA) |
| 9750.0 | 1.430 (MTPA) | 1.430 (MTPA) | 1.430 (MTPA) |
| 13000 | 1.430 (MTPA) | 1.430 (MTPA) | 1.430 (MTPA) |
| 16250 | 1.365 (FW 24.4°) | 1.430 (MTPA) | 1.430 (MTPA) |
| 19500 | 1.120 (FW 43.7°) | 1.404 (FW 18.6°) | 1.430 (MTPA) |
| 26000 | 0.6502 (FW 65.8°) | 1.020 (FW 49.1°) | 1.221 (FW 37.2°) |
| 32500 | — | 0.6448 (FW 66.0°) | 0.8748 (FW 56.2°) |
| 39000 | — | — | 0.5484 (FW 69.8°) |

## Loss trajectory (hot, settled TDM + demag, v_nom, m = 0.95)

**L12** — n_max 25841 rpm (electrical, v_nom); I_peak 48.79 A (owner peak duty)

| n rpm | I A | γ ° | T N·m | k_state | Cu W | iron W | magnet+shaft W | total W | η shaft % | demag |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 3250.0 | 21.39 | 3.28 | 0.3141 | 0.98925 | 12.50 | 1.262 | 0.1880 | 13.95 | 88.44 | 99.524 %Br |
| 3250.0 | 42.78 | 6.34 | 0.6202 | 0.98937 | 49.40 | 1.268 | 0.2032 | 50.87 | 80.58 | 99.284 %Br |
| 3250.0 | 48.79 | 7.01 | 0.7028 | 0.98857 | 64.19 | 1.272 | 0.2118 | 65.67 | 78.45 | 99.178 %Br |
| 6500.0 | 21.39 | 3.28 | 0.3129 | 0.98546 | 13.18 | 3.281 | 0.7495 | 17.21 | 92.51 | 99.524 %Br |
| 6500.0 | 42.78 | 6.34 | 0.6189 | 0.98734 | 50.25 | 3.300 | 0.8065 | 54.36 | 88.56 | 99.284 %Br |
| 6500.0 | 48.79 | 7.01 | 0.7015 | 0.98674 | 65.12 | 3.311 | 0.8386 | 69.27 | 87.32 | 99.178 %Br |
| 13000 | 21.39 | 3.28 | 0.3106 | 0.97806 | 15.78 | 9.272 | 2.993 | 28.05 | 93.75 | 99.524 %Br |
| 13000 | 42.78 | 6.34 | 0.6164 | 0.98336 | 53.58 | 9.331 | 3.215 | 66.13 | 92.68 | 99.283 %Br |
| 13000 | 48.79 | 7.01 | 0.6990 | 0.98315 | 68.73 | 9.367 | 3.339 | 81.44 | 92.10 | 99.178 %Br |
| 19500 | 21.39 | — | — | — | — | — | — | — | — | infeasible |
| 19500 | 42.78 | 47.5 | 0.4470 | 0.98479 | 57.99 | 13.91 | 4.307 | 76.20 | 92.27 | 99.191 %Br |
| 19500 | 48.79 | 44.2 | 0.5393 | 0.98608 | 73.65 | 13.77 | 4.391 | 91.81 | 92.28 | 99.070 %Br |
| 25841 | 21.39 | — | — | — | — | — | — | — | — | infeasible |
| 25841 | 42.78 | — | — | — | — | — | — | — | — | infeasible |
| 25841 | 48.79 | 80.0 | 0.1308 | 0.96137 | 79.75 | 16.84 | 3.580 | 100.2 | 77.84 | 99.093 %Br |

**L20** — n_max 38461 rpm (electrical, v_nom); I_peak 59.93 A (ASSUMPTION — no owner peak duty: L12 owner peak/rated current ratio 48.79/42.78 applied to I0 (labelled assumption; the L20 card's peak needs an owner duty))

| n rpm | I A | γ ° | T N·m | k_state | Cu W | iron W | magnet+shaft W | total W | η shaft % | demag |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 3250.0 | 26.27 | 4.00 | 0.6395 | 0.98841 | 33.41 | 2.089 | 0.3151 | 35.81 | 85.86 | 99.285 %Br |
| 3250.0 | 52.55 | 7.45 | 1.248 | 0.98492 | 132.8 | 2.110 | 0.3652 | 135.3 | 75.84 | 98.804 %Br |
| 3250.0 | 59.93 | 8.22 | 1.406 | 0.98281 | 172.7 | 2.123 | 0.4002 | 175.2 | 73.19 | 98.592 %Br |
| 6500.0 | 26.27 | 4.00 | 0.6378 | 0.98571 | 34.29 | 5.434 | 1.256 | 40.98 | 91.36 | 99.285 %Br |
| 6500.0 | 52.55 | 7.45 | 1.246 | 0.98346 | 134.1 | 5.492 | 1.444 | 141.0 | 85.74 | 98.804 %Br |
| 6500.0 | 59.93 | 8.22 | 1.404 | 0.98148 | 174.1 | 5.530 | 1.575 | 181.2 | 84.06 | 98.592 %Br |
| 13000 | 26.27 | 4.00 | 0.6343 | 0.98038 | 37.75 | 15.35 | 5.014 | 58.12 | 93.67 | 99.285 %Br |
| 13000 | 52.55 | 7.45 | 1.242 | 0.98055 | 138.9 | 15.54 | 5.742 | 160.2 | 91.34 | 98.804 %Br |
| 13000 | 59.93 | 8.22 | 1.400 | 0.97885 | 179.5 | 15.65 | 6.247 | 201.4 | 90.44 | 98.592 %Br |
| 19500 | 26.27 | 7.08 | 0.6303 | 0.97553 | 43.24 | 29.04 | 11.09 | 83.37 | 93.89 | 99.280 %Br |
| 19500 | 52.55 | 16.6 | 1.223 | 0.97945 | 146.6 | 27.94 | 11.65 | 186.1 | 93.05 | 98.736 %Br |
| 19500 | 59.93 | 18.7 | 1.373 | 0.97784 | 188.0 | 27.65 | 12.41 | 228.1 | 92.46 | 98.500 %Br |
| 38461 | 26.27 | — | — | — | — | — | — | — | — | infeasible |
| 38461 | 52.55 | — | — | — | — | — | — | — | — | infeasible |
| 38461 | 59.93 | 80.0 | 0.2681 | 0.96555 | 224.3 | 46.16 | 8.896 | 279.3 | 79.35 | 98.215 %Br |

k_state = settled operating torque / virgin static map at the same (I, γ).

## Off-grid checks — static map (interpolated vs direct FEM)

**L12** — targets ψ ≤ 0.5 %, T ≤ 1 %, ripple ≤ max(0.5 pp, 10 %)

| point | I, γ | max ψ err | T err (T-map) | T err (from ψ) | ripple int / FEM % | first pass ψ / T | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|
| chk_ridge_0.375 | 16.04 A, 2.46° | +0.002 % | -0.009 % | -0.028 % | 27.4 / 27.5 | +0.00 % / -0.01 % | pass |
| chk_ridge_0.875 | 37.43 A, 5.60° | +0.004 % | -0.002 % | +0.011 % | 9.18 / 9.15 | +0.00 % / -0.00 % | pass |
| chk_ridge_1.25 | 53.48 A, 7.53° | +0.043 % | -0.029 % | -0.003 % | 3.47 / 3.66 | +0.04 % / -0.03 % | pass |
| chk_ridge_1.75 | 74.87 A, 9.88° | +0.011 % | +0.052 % | +0.023 % | 2.13 / 2.13 | +0.01 % / +0.05 % | pass |
| chk_fw_1.25_72.5 | 53.48 A, 72.5° | +0.047 % | +0.006 % | +0.002 % | 11.1 / 11.3 | +0.06 % / -0.10 % | pass |
| chk_fw_2.25_57.5 | 96.25 A, 57.5° | +0.126 % | -0.146 % | -0.073 % | 6.33 / 6.46 | +0.18 % / -0.21 % | pass |
| chk_top_2.4_20 | 102.7 A, 20.0° | +0.276 % | -0.294 % | -0.198 % | 3.38 / 3.39 | +0.26 % / -0.26 % | pass |
| chk_top_2.25_42.5 | 96.25 A, 42.5° | +0.123 % | -0.130 % | -0.044 % | 5.03 / 5.07 | +0.14 % / -0.11 % | pass |
| chk_peak | 48.79 A, 7.01° | +0.027 % | -0.013 % | +0.003 % | 4.79 / 4.91 | +0.03 % / -0.01 % | pass |
| chk_duty_rated_g | 42.78 A, 10.0° | +0.000 % | -0.001 % | +0.010 % | 6.70 / 6.70 | +0.00 % / -0.00 % | pass |
| chk_audit_I0.5 | 20.33 A, 10.0° | +0.002 % | -0.009 % | -0.018 % | 20.9 / 21.0 | +0.00 % / -0.01 % | pass |
| chk_audit_I1.0 | 40.66 A, 10.0° | +0.009 % | -0.001 % | +0.008 % | 7.54 / 7.50 | +0.01 % / -0.00 % | pass |
| chk_audit_I1.3 | 52.86 A, 10.0° | +0.045 % | -0.035 % | +0.002 % | 3.51 / 3.74 | +0.04 % / -0.03 % | pass |

**L20** — targets ψ ≤ 0.5 %, T ≤ 1 %, ripple ≤ max(0.5 pp, 10 %)

| point | I, γ | max ψ err | T err (T-map) | T err (from ψ) | ripple int / FEM % | first pass ψ / T | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|
| chk_ridge_0.375 | 19.71 A, 3.02° | +0.003 % | -0.019 % | -0.049 % | 21.8 / 21.8 | +0.00 % / -0.02 % | pass |
| chk_ridge_0.875 | 45.98 A, 6.65° | +0.007 % | +0.009 % | -0.010 % | 5.54 / 5.65 | +0.01 % / +0.01 % | pass |
| chk_ridge_1.25 | 65.69 A, 8.83° | +0.029 % | -0.075 % | -0.037 % | 2.46 / 1.69 | +0.03 % / -0.08 % | pass (ripple ✘) |
| chk_ridge_1.75 | 91.96 A, 11.6° | +0.070 % | -0.102 % | -0.070 % | 3.46 / 3.54 | +0.07 % / -0.10 % | pass |
| chk_fw_1.25_72.5 | 65.69 A, 72.5° | +0.015 % | -0.053 % | -0.044 % | 8.98 / 9.38 | +0.09 % / -0.05 % | pass |
| chk_fw_2.25_57.5 | 118.2 A, 57.5° | +0.453 % | -0.337 % | -0.232 % | 5.76 / 5.62 | +0.52 % / -0.36 % | pass |
| chk_top_2.4_20 | 126.1 A, 20.0° | +0.139 % | -0.114 % | -0.015 % | 2.46 / 2.35 | +0.12 % / -0.10 % | pass |
| chk_top_2.25_42.5 | 118.2 A, 42.5° | +0.497 % | -0.325 % | -0.179 % | 4.21 / 4.12 | +1.00 % / -0.80 % | pass |
| chk_peak | 59.93 A, 8.22° | +0.019 % | -0.043 % | -0.028 % | 2.75 / 2.28 | +0.02 % / -0.04 % | pass |
| chk_duty_rated_g | 52.55 A, 10.0° | +0.002 % | +0.004 % | -0.012 % | 3.70 / 3.70 | +0.00 % / +0.00 % | pass |

First pass = grid before the P04 cell refinement (FW arm halved to 7.5° between 35° and 65° at 2.0 and 2.5·I0, 4 points); the check points are never grid points.

## Off-grid checks — losses (trajectory interpolation vs direct FEM)

**L12** — target total loss ≤ 5 %

| point | n, I, γ (FEM) | FEM W | passport W | error | verdict |
|---|---:|---:|---:|---:|---:|
| lchk_0.75n_0.75I | 9750.0 rpm, 32.09 A, 4.86° | 37.56 | 37.68 | +0.33 % | pass |
| lchk_0.35n_peak | 4550.0 rpm, 48.79 A, 7.01° | 66.90 | 66.92 | +0.03 % | pass |
| lchk_1.25n_mid | 16250 rpm, 45.78 A, 16.4° | 80.53 | 78.51 | -2.51 % | pass |
| duty_rated | 13000 rpm, 42.78 A, 10.0° | 65.83 | 66.13 | +0.45 % | pass |
| duty_peak | 14400 rpm, 48.79 A, 10.0° | 101.8 | 83.57 | -17.91 % → +0.64 % Cu-T corr. | pass |
| audit_rpm1.5 | 19500 rpm, 40.66 A, 10.0° | 78.16 | — | — | refused (outside the trajectory domain) |
| audit_base | 13000 rpm, 40.66 A, 10.0° | 60.90 | 61.25 | +0.57 % | pass |
| audit_rpm0.5 | 6500.0 rpm, 40.66 A, 10.0° | 49.48 | 49.58 | +0.21 % | pass |
| audit_I0.5 | 13000 rpm, 20.33 A, 10.0° | 26.55 | — | — | refused (outside the trajectory domain) |
| audit_I1.3 | 13000 rpm, 52.86 A, 10.0° | 92.71 | — | — | refused (outside the trajectory domain) |
| lchk_rated_72steps | 13000 rpm, 42.78 A, 6.34° | 66.35 | 66.13 | -0.34 % | pass |

- duty_peak: FEM at coil 200.0 °C / magnet card vs passport hot coil 100.6 °C / magnet 107.2 °C: copper corrected analytically (k_R = 1.3174), magnet temperature not corrected

**L20** — target total loss ≤ 5 %

| point | n, I, γ (FEM) | FEM W | passport W | error | verdict |
|---|---:|---:|---:|---:|---:|
| lchk_0.75n_0.75I | 9750.0 rpm, 39.41 A, 5.85° | 90.44 | 90.71 | +0.30 % | pass |
| lchk_0.35n_peak | 4550.0 rpm, 59.93 A, 8.22° | 177.3 | 177.3 | +0.02 % | pass |
| lchk_1.25n_mid | 16250 rpm, 56.24 A, 7.84° | 193.8 | 192.4 | -0.70 % | pass |
| duty_rated | 13000 rpm, 52.55 A, 10.0° | 159.8 | 160.2 | +0.26 % | pass |
| lchk_rated_72steps | 13000 rpm, 52.55 A, 7.45° | 160.6 | 160.2 | -0.23 % | pass |

Audit / duty rows sit at γ = 10° (off the MTPA trajectory): their error includes the trajectory-reuse limit (spec P10).

## Independent torque (B1), static window (P05), time step (B4)

|  | point | T Coulomb (full period) | T terminal work | Coulomb vs work | 60° window vs full | ψd 60° vs full | ripple 60° / full % |
|---|---:|---:|---:|---:|---:|---:|---:|
| L12 | full_rated_mtpa | 0.626743 | 0.626929 | -0.030 % | +0.020 % | +0.001 % | 6.91 / 6.97 |
| L12 | full_peak | 0.710907 | 0.711099 | -0.027 % | +0.018 % | +0.001 % | 4.91 / 4.96 |
| L20 | full_rated_mtpa | 1.26689 | 1.26695 | -0.005 % | +0.019 % | +0.001 % | 3.76 / 3.81 |
| L20 | full_peak | 1.43096 | 1.43099 | -0.002 % | +0.008 % | +0.000 % | 2.28 / 2.30 |

|  | steps/period | T | total loss | groups |
|---|---:|---:|---:|---:|
| L12 | 36 vs 72 | +0.005 % | -0.34 % | cu_ac -0.77 % · fe -1.80 % · mag -0.62 % · shaft -4.32 % |
| L20 | 36 vs 72 | +0.015 % | -0.23 % | cu_ac -0.67 % · fe -1.80 % · mag -0.73 % · shaft -3.10 % |

## Saved duties re-solved today vs the stored duty result

|  | duty | T today | T stored | ΔT | loss today W | loss stored W | Δloss | stored at |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| L12 | duty_rated | 0.6154 | 0.6230 | -1.22 % | 65.83 | 65.90 | -0.11 % | 2026-09-09T08:12:23 |
| L12 | duty_peak | 0.6894 | 0.7000 | -1.51 % | 101.8 | 101.9 | -0.10 % | 2026-08-31T16:52:27 |
| L20 | duty_rated | 1.242 | 1.242 | -0.03 % | 159.8 | 159.8 | +0.00 % | 2026-10-01T09:28:06 |

Stored duties were solved by older baselines (torque method, mesher, eddy settle); the passport uses today's.

## Audit 2026-09-30 comparison (L12)

| I A | γ_MTPA audit | γ_MTPA passport | T audit | T passport | ΔT |
|---|---:|---:|---:|---:|---:|
| 20.33 | 2.60 | 3.12 | 0.2993 | 0.3019 | +0.88 % |
| 40.66 | 5.40 | 6.05 | 0.5902 | 0.5968 | +1.12 % |
| 60.99 | 7.90 | 8.36 | 0.8635 | 0.8756 | +1.41 % |
| 81.32 | 10.7 | 10.6 | 1.104 | 1.125 | +1.91 % |

Audit static runs: card magnet temperature (120 °C), older solver (Maxwell-based mean, Triangle mesher); passport: 107.2 °C magnets, Coulomb, Netgen.

## Demag safe surface (TDM, rated speed, owner steadiness rule)

|  | probe | I/I0 | γ | Br kept % | per-magnet spread % | steady |
|---|---:|---:|---:|---:|---:|---:|
| L12 | dm_I1_gmtpa | 1.00 | 6.34 | 99.283 | 0.0710 | pass |
| L12 | dm_I1.5_gmtpa | 1.50 | 8.71 | 98.837 | 0.0740 | pass |
| L12 | dm_I2_gmtpa | 2.00 | 11.1 | 98.165 | 0.0600 | pass |
| L12 | dm_I2.5_gmtpa | 2.50 | 13.3 | 97.046 | 0.0570 | pass |
| L12 | dm_I1.5_g+80 | 1.50 | 80.0 | 98.680 | 0.0180 | pass |
| L12 | dm_I2.5_g+80 | 2.50 | 80.0 | 94.503 | 0.124 | pass |
| L12 | dm_I1_g+90 | 1.00 | 90.0 | 99.212 | 0.0310 | pass (T≈0, §1 floor) |
| L20 | dm_I1_gmtpa | 1.00 | 7.45 | 98.804 | 0.0870 | pass |
| L20 | dm_I1.5_gmtpa | 1.50 | 10.2 | 97.908 | 0.144 | pass |
| L20 | dm_I2_gmtpa | 2.00 | 13.0 | 95.336 | 0.122 | pass |
| L20 | dm_I2.5_gmtpa | 2.50 | 14.7 | 91.514 | 0.0760 | pass |
| L20 | dm_I1.5_g+80 | 1.50 | 80.0 | 96.501 | 0.0180 | pass |
| L20 | dm_I2.5_g+80 | 2.50 | 80.0 | 87.916 | 0.733 | pass |
| L20 | dm_I1_g+90 | 1.00 | 90.0 | 98.614 | 0.0360 | pass |

L12: 99.5 % Br knee on the MTPA line at 29.83 A rms — already below the first probe (I0).
L20: 99.5 % Br knee on the MTPA line at 21.97 A rms — already below the first probe (I0).

## Budget

|  | FEM runs (ok) | failed | container wall h | Σ single-thread FEM h |
|---|---:|---:|---:|---:|
| L12 | 152 | 0 | 1.10 | 4.75 |
| L20 | 145 | 0 | 1.22 | 4.39 |

Server sandbox, deployed image `deploy-api` (3ba0f9b), 6 single-thread workers in one container (`--cpus 8`, nice 19, ionice idle), one container at a time.
Total 2.32 h container wall, 297 FEM runs, 0 failed; plus one restart of L12 after 2 min (18 static points solved at the wrong gap level, discarded).
Code: L12 runs f5240a3, L20 runs 503912b (identical computation modules), extra/refine/assemble 32dcd5a–2595399.

## Owner defaults used (not decided yet)

| item | value used | status |
|---|---|---|
| controller voltage margin m | 0.95 | placeholder (P24) |
| materials library | deployed `/srv/motres/shared/materials_library.yaml` | PR #51 not merged (B8) |
| settle rule | TDM periodic orbit + full demag pre-pass with the owner's steadiness rule; §3.0.1 tail-bound rule = proposal | owner decision pending (B4/P06) |
| loss trajectory bus | v_nom (typical map); envelope also at v_min / v_max | spec 3.5 |
| demag safe-surface threshold | 99.5 % Br kept (today's tuner rule) | reported, not used as the current limit |
| L20 peak current | L12 owner ratio 48.79/42.78 × I0 | assumption — needs an owner duty |

## Open issues

| # | issue | evidence | needs |
|---|---|---|---|
| 1 | Map (virgin, static) vs operating torque | rated k_state L12 0.983 (demag 0.992 × rest 0.991), L20 0.981 (0.987 × 0.993) — 1.7–1.9 %, above the 1 % torque target if the map is read raw | owner: card torque = operating (proposal); tuner applies k_state(n, I) |
| 2 | 99.5 % retention knee lies below I0 | Br kept at I0: L12 99.28 %, L20 98.80 % (knee 29.8 A / 22.0 A) | owner: a torque-loss-based safe-surface rule |
| 3 | Solver `inc_ldq` ≠ differential L (P20) | frozen secant ν gives Ld 2.1–2.4×, Lq 1.7× the Δψ/Δi value; the old bench probe (3.7 / 5.3 µH) agrees with Δψ/Δi | summary / report should quote the differential value |
| 4 | Gap rule refines per point | L12 map 2–3 layers, L20 3–4 layers; measured effect ≤ 0.05 % torque | B3: one gap level per map |
| 5 | Magnetostatic demag path not steady | 0 of 7 pre-pass periods; safe surface probed on TDM at 7 points only | full retention column ≈ 70 TDM runs per machine |
| 6 | Ripple interpolation of p-p is weak near low-ripple ridge points | L20 (1.25·I0, ridge): 2.46 vs 1.69 % (0.77 pp) | interpolate torque harmonics, not p-p (P02) |
| 7 | L20 top-current FW margin is thin | (2.25·I0, 42.5°) ψ 0.497 % after the P04 refinement (1.0 % before) | one more refinement ring at 2.25·I0, or cap the L20 domain at 2·I0 |
| 8 | Stored L12 rated-duty temperatures unconverged | stored coupled loop residual 155 K coil / 118 K magnet | rerun the coupled loop; passport HOT follows the duty |
| 9 | Stored duties vs today | L12 ΔT −1.2 / −1.5 % (older torque mean, Triangle, fillet r1 0.1 → 0.15 mm); losses −0.1 % | re-save the duties on today's baseline |
| 10 | Mechanical limit = bearing rating only | critical speed / rotor stress not evaluated | §5.5 |
| 11 | Not in stage 1 | stator B spectra (§4), per-row slot field map (§6.3), thermal loop / I_thermal, generator quadrant | later stages |

## Stage 2 (3-D) needs

| item | for L12 (12 mm) and L20 (20 mm) |
|---|---|
| k_flux(L), k_T(L), k_L(L) | Stage A/B at 4 lengths around each stack (night) |
| apply | k_T on torque, k_flux on Kv / voltage map / speed limits, k_L on L |
| magnet eddy | axial segmentation factor (solid magnets today) |

## Stage 3 (PWM + controller from PCB_CIANO14_40) needs

| item | input |
|---|---|
| carriers | the controller class's f_sw (two carriers at rated + two rpm neighbours) |
| device losses | MOSFET cards, R_DS(on)(T_j), body diode, dead time, modulation (PCB_CIANO14_40 BOM) |
| bus | pack model (6S / 12S), DC-link, bus coupling |
| output | PWM loss deltas per group, controller conduction + switching losses, drive efficiency |
