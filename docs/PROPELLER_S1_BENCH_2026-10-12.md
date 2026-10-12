# Propeller S1 — bench calibration of the slipstream cooling (L12, 2026-10-12)

The deployed S1 model said the CIANO14 40_12 (die `CIANO14 40 new`, configuration L12, open frame)
runs away at the 1 kW point and reaches 242 °C at ~800 W; the published bench runs say 150 °C at
770 W (30 min) and 201 °C at 1 014 W. This stage finds the bench data, puts the bench propeller in
the catalogue, fits the slipstream factor on the two continuous runs, checks it with coupled FEM at
both bench points and adds an external-ESC drive to the S1. Records: `docs/data/propeller_s1_bench/`.

## 1. Bench data (`docs/data/propeller_s1_bench/bench_L12.json`)
The `.xlsx` files named in the brief are BOMs; the test plan `.docx` is a plan for a ZC5By
dynamometer, not results. The data are in `MOTRES_ProofOfTRL_CIANO14.pdf` (2026-08-21), the
comparison report and emotres.com (`/news`, `/shop/ciano-40-12`):

| run | propeller | drive | what is logged |
|---|---|---|---|
| 2026-06-16 sweep | HQ1050-3 (10.5×5, 3 blades) | 80 A trapezoidal, 24 V | 10 rows rpm / V / A / thrust / torque / temp (ramp) / P; air 31.3 °C, 74.7 % RH, 100.7 kPa |
| 2026-07-09 sweep | 1045 2-blade | FOC, 22 V | 12 rows, no temperature |
| 2026-08-12 sweep | AERONAUT 12×5 2-blade | FOURnamics nCTRL, 24.5 V | 10 rows, no temperature, P_el under-reads < 60 % |
| 2026-06-23, 30 min | not stated (falls on the HQ1050-3 curve) | not stated | 3 455 g, 770 W shaft, 23.7 V / 43.85 A, 150 °C "stabilised" (sensor not stated) |
| 2026-08-13, 1 kW | "3-blade 10×5" (= HQ1050-3 curve within 2 %) | nCTRL FOC | 14 400 rpm, 4 300 g, 1 014 W, 24.6 V / 55 A, 201 °C IR max on the coil |

Missing: the raw logs of both continuous runs, the 30-min run's rpm / propeller / ESC / sensor /
ambient, the 1 kW run's duration / ambient / torque, phase currents everywhere. The product page's
"13 081 rpm, 0.56 N·m" continuous line is an interpolation of the 06-16 table, not a log.

## 2. Propeller
The bench propeller is the HQ1050-3, not T-Motor FPV 10×5: at equal rpm it makes 16–19 % less
thrust and absorbs 23–26 % less power; at equal shaft power the thrust is within 1 % but the speed is
~9 % higher (770 W: 13 200 vs 12 100 rpm), so torque and current are ~9 % lower. (The T-Motor curve
is not extrapolated at 13–14 krpm: its 3120 tables reach 16 069 rpm.) New catalogue entry
`motres_bench_hq1050_3` (`config/propellers/motres/hq1050_3_bench.yaml`, `bench_measured: true`,
torque measured, ρ 1.1375 kg/m³ from the logged air; the 1 kW point stored as a held-out row). It is
offered for the Ø40 die and is L12's default propeller (`config/cooling_options.yaml`).

## 3. Slipstream factor
The S1 fixed point driven with the bench load (shaft power at the bench rpm, wake of the bench
thrust), the passport's losses, 30 °C air (assumed), the measured temperature = winding hot spot,
least squares over both points (`fit_rows.json`):

| network | factor | residual 30 min / 1 kW |
|---|---|---|
| Stage B (3 anchors ≤ 12.3 m/s) | 0.55 | −3.4 / +2.1 K |
| refitted (7 anchors to 19.8 m/s, below) — **adopted** | **0.53** | **−3.6 / +1.8 K** |

One factor explains both points. Uncertainty ±0.04 (air 25–35 °C, shaft power ±3 %, sensor 10 K
below the hot spot). Loss check: bench P_el − P_shaft (motor + ESC) is 269 / 339 W against the
passport's 64 / 100 W motor loss; if even 25 W more sat in the winding the factor would be 0.84, and
with 100 W no factor ≤ 1.5 fits. The factor therefore rests on the passport losses being right — the
coupled FEM (63 / 95 W) and the 08-12 FOURnamics sweep (977 W shaft, 143 W total incl. ESC) agree
with them; the 06-23 and 08-13 electrical inputs do not, and are not explained here.

## 4. Coupled FEM at the bench points (`propanchor --factor --ambient 30 --shaft-w --thrust-g`)
| point | factor | I A | T_3D/Q | loss W | FEM winding / magnet °C | bench °C | network (refit) W / M |
|---|---|---|---|---|---|---|---|
| 30 min, 13 196 rpm | 0.55 | 39.2 | +0.7 % | 62.9 | 138.1 / 86.1 | 150 | 142.3 / 93.4 |
| 30 min | 0.40 | 39.6 | −0.5 % | 67.6 | 179.2 / 123.6 | 150 | 174.6 / 116.5 |
| 1 kW, 14 400 rpm | 0.55 | 47.6 (1 corr.) | −0.2 % | 95.3 | 186.8 / 104.2 | 201 | 192.2 / 115.1 |
| 1 kW | 0.40 | 46.6 (first pass) | −11 % | 96.0 | 227.8 / 145.0 | 201 | 225.4 / 139.9 |

The 1 kW / 0.40 correction pass (52 A) was stopped after 30 min: the hot magnets demagnetised
(Br down to 0.12 × on 1 080 of 1 127 elements, not settling) — at factor 0.40 the bench load has no
steady state, so the factor must be above 0.40. At 0.55 the FEM is 12 / 14 K below the bench (inside 15 K); linear in the factor, the FEM alone
matches the bench at ≈ 0.51 on both points. The 1 kW FEM keeps 99.32 % of the magnets' Br (< 1 %,
inside the passport rule) at 47.6 A / 104 °C magnet: the S1's demagnetisation limit, scaled from the
53.5 °C probe, is conservative there.

Why the old network missed at high speed: on the open frame 60 % of the heat leaves through the end
turns in the wash (35.5 of 63 W at the 30-min point), then the housing (9.9), slot channels (7.1), gap
flow (6.1) and rotor faces (4.2). The FEM's end-turn film (Ø3.3 mm bundle, Re ≈ 3 700) and the
transitional slot channels grow faster with air speed than the single housing-film law the lumped
network scales every path with; fitted only to 6–12 m/s, the network read 6–8 K (winding) and
11–16 K (magnet) hot at 18–20 m/s with the FEM's own losses. Refitted on 3 + 4 anchors: f_h 0.196,
A_w 4.31e-3 m², A_r 2.83e-3 m², max residual 5.4 K winding / 10.9 K magnet (hot at 0.55, cool at
0.40 — conservative at the bench speeds), held-out 11 370 rpm check −4.9 K.

## 5. External ESC
`drive=external_esc` (route: `esc_i_bus_max_A`, `esc_i_phase_max_A`): no board model; the ESC's
continuous BUS current is checked against the battery current at the pack minimum (binding
`controller`), its losses and the motor PWM extra are unknown (flags `esc_external`,
`esc_loss_omitted`, `pwm_omitted`). The L12 model carries the bench ESC as default (nCTRL, 55 A bus,
demonstrated 2026-08-13, not a datasheet rating). Configure: "S1 controller" = Drive block | External
ESC + A field (empty = the model's ESC). EN + zh-CN.

## 6. S1 old → new (25 °C air, passport pack 18–25.2 V, favourable board)
| motor + propeller, drive | old (factor 0.4) | new (factor 0.53) |
|---|---|---|
| L12 + FPV 10×5, sine / Si 48k / GaN 48k | 9 487 / 9 475 / 9 638 rpm, controller | 9 502 / 9 492 / 9 657 rpm, controller (winding 12–15 K cooler) |
| L12 + FPV 10×5, external ESC 55 A | 11 714 rpm, demag 41.6 A, 186 / 122 °C | 12 241 rpm, 3.72 kgf, 833 W, demag 47.8 A, 199 / 115 °C |
| L12 + HQ1050-3, sine / Si 48k / GaN 48k | 10 747 / 10 733 / 10 922 rpm, controller | 10 764 / 10 754 / 10 944 rpm, controller |
| L12 + HQ1050-3, external ESC 55 A | 12 685 rpm, demag | 13 156 rpm, 3.62 kgf, 794 W, demag 46.9 A, 197 / 116 °C |
| L12 + HQ1050-3, external ESC, 24.6 V bus (bench) | 13 228 rpm, demag | 14 202 rpm, 4.27 kgf, 1 005 W, 197 / 115 °C, 44.9 A bus (bench: 14 400 rpm, 1 014 W, 201 °C) |
| L20 + P13×4.4, all drives | ≥ 9 092 (end of prop data) | ≥ 9 092 (8–12 K cooler), factor transferred |
| L13 + G28 / G30 / G32 / G36 / G40 | 1 804 / 1 731 / 1 686 / 1 511 / 1 202 | unchanged (factor 0.4 kept) |

## 7. Which models take the factor
The factor is a property of the INSTALLATION — air at the motor relative to the wake, plus the
turbulence of the wash on exposed coils — not of the slipstream formula. L20 is the same die, frame,
stator holder and coil exposure, so it takes 0.53 as `transferred` (flagged; its 13-in propellers put
the motor deeper into the hub core, so its true factor may be lower). L13 is a housed Ø85 frame with
end caps under 28–40 in propellers: no evidence, kept at 0.4 (with 0.55 its limits would rise 2.4–7 %).

## Left open
- The bench electrical input exceeds shaft power + passport losses + a plausible ESC loss by
  ~165 / 185 W at the two continuous points (06-16 trapezoidal and 07-09 FOC sweeps show the same
  ~140 W excess; the 08-12 nCTRL sweep does not). If part of it is motor heat (ripple / harmonic
  copper and iron loss), the true factor is higher. Raw logs and a phase-current / temperature log
  with a thermocouple on a coil would settle it.
- The demag limit at temperature is scaled from one probe; the FEM says it is conservative at the
  1 kW point. A demag probe at ~110 °C would lift the external-ESC S1.
