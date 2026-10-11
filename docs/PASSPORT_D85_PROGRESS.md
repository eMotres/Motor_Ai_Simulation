# Ø85 passport (CIANO28 85 20SW1200 / L13) — finish run progress

Branch `feat/passport-d85-finish`. Model: Claude Opus 5.5. Server sandbox
`/opt/motres/compute/passport-d85-finish/` (deployed API image + mounted branch code,
`--cpus 8` lane 1 / `--cpus 6` lane 2, nice 19, idle IO). One line per finished item.

| time (CEST) | item | result |
|---|---|---|
| 2026-10-10 19:10 | reuse inventory | 10-05/06 Claude run recovered from `/srv/motres/archive/compute_2026-10-10` (`passport-d85/`): 20 °C set, cooling studies (robotics + propeller S1 / peak), Stage A, rotor stress, demag-vs-temperature, Si 48 kHz motor-PWM FEM anchor (finished 10-06 09:39 incl. its same-mesh sine reference: ΔP = −0.7 W). Pipeline commits cherry-picked from `salvage/motor_ai_sim_wt_d85`; its web cooling selector dropped (the deployed Configure already has one). |
| 2026-10-10 19:15 | rated-duty hot grid | STOPPED (owner rule): no in-card steady state at 25.88 A / 1000 rpm with either cooling (robotics coupled loop 2026-09-28 refused at magnet 242.9 °C; S1 = 8.52 A robotics, 4.3 A propeller air). Hot set re-based on the converged robotics S1 state (magnet 69.2 °C, winding 75.1 °C), fixed operating angle γ_ref 1.9505°. |
| 2026-10-10 21:05 | hot re-base launched | lane 1: noload_pinned, hot (fixed angle), demag, loss, extra, checks, refine, loss2, loss3; 20 °C set, d-axis and gap level seeded from the 10-05/06 run. |
| 2026-10-10 21:20 | Stage D (loaded 3-D k_T) | failed twice, stopped: gap element 0.55 mm < ring pitch 0.609 mm; at 0.65 mm the 3-D mesh has no `shaft` elements. k_T = k_ψ (Stage A). |
| 2026-10-10 21:35 | hot static grid (69.2 / 75.1 °C, γ_ref 1.95°) | 60 points, 0 failed; virgin T at I0 5.536 N·m; fixed angle vs per-level vertex ≤ 0.55 % below at 2.5·I0. |
| 2026-10-10 21:40 | propellers | T-Motor G28×9.2 added (torque measured, U8 II KV100 12S, 1896–3709 rpm); D85 list now G28/G30/G32/G36/G40. Live cooling options resolve [robotics, propeller_air]; deployed Configure shows the robotics/propeller toggle for such a die (`hasPropellerCooling`), reached through Codex's card `CIANO28 85 20SW1200 L13`. |
| 2026-10-10 21:40 | Codex 20 °C grid | 15 settled points (12.94/25.88/38.82 A × 250–1500 rpm, γ_ref, 120 steps, demag) extracted from the archive into the card. |
| 2026-10-10 22:50 | hot demag probes + loss trajectory | Br kept on the fixed-angle line: 99.93 / 99.76 / 99.35 / 98.61 % at 1 / 1.5 / 2 / 2.5·I0 (99.5 % knee 47.0 A); 15 settled loss points, 0 failed; at 2000 rpm the trajectory field-weakens (γ 7.9 / 19.2 / 31.2° at 12.9 / 25.9 / 46.0 A). |
| 2026-10-10 23:25 | demag current limit (≤ 1 % rated-torque drop) | overload 51.76 A → −0.94 %, 64.70 A → −2.13 % ⇒ limit ≈ 52.4 A rms (2.02·I0) at magnet 69.2 °C; the peak 45.96 A is below it. |
| 2026-10-10 23:25 | rated duty, coupled limits studies (evidence for the stopped hot grid) | robotics: 40 s from cold to the 70 °C housing limit; the steady state would be housing 190 / winding 233 / magnet 165 °C (> 150 °C card). Propeller air (FEM lower bound): 83 s to the winding limit, no steady state. |
| 2026-10-10 23:26 | motor PWM anchors at 2000 rpm launched | lane 2: Si IQE036N08NM6SC ×2 and GaN IGC016K10S2 ×2 (20 ns), 48 kHz, I0, 4 samples/carrier (412 steps/period), new hot state; 5 h guard. |
| 2026-10-11 00:47 | lane 1 finished | 12 892 s, 0 failed runs: checks, refinement, loss3 (48-node drive-variant grid, 7 infeasible beyond γ 80°). Static off-grid 9/10 (chk_top_2.4_20 at 62.1 A: ψq 0.58 % > 0.5 %, torque −0.66 %); loss off-grid 6/6 within 2.2 %. |
| 2026-10-11 02:21 | 2000 rpm PWM anchors | REJECTED: `passport_full._hot_map` used the Ø40's 7 pole pairs, so the task kept the fixed angle at 2000 rpm and over-modulated (m 1.13) instead of field-weakening at 19.1°; ΔP −0.4 W (Si and GaN). Bug fixed; re-queued 08:44 on lane 2 (deployed image afd759eb, the earlier image was removed by the #118 deploy). |
| 2026-10-11 08:50 | card assembled | record `docs/data/passport_d85/passport_L13.json`, store `config/passports/CIANO28 85 20SW1200/L13.json` (passes the Configure store test), HTML in the owner's Downloads. |

## Card values (hot = robotics S1 state, magnet 69.2 °C / winding 75.1 °C; γ_ref 1.95°; m 0.89)

| quantity | value |
|---|---|
| rated (25.88 A, 1000 rpm) torque card / operating / virgin map | 5.30 / 5.49 / 5.54 N·m (k_T = k_ψ 0.965; k_state 0.993, demag share 0.999) |
| rated shaft power, η shaft (sine, no bearings) | 575 W, 78.3 % (EM loss 159 W) |
| peak (45.96 A, 1000 rpm) torque card | 8.75 N·m, η 65.9 % |
| 20 °C: Kt I0 / 0.25·I0, Km, Kv 2-D / ÷k_ψ, ψ_PM, R phase | 0.2189 / 0.2235 N·m/A, 0.506 N·m/√W, 52.4 / 54.3 rpm/V, 7.52 mWb, 62.3 mΩ |
| 20 °C: Ld / Lq small-signal, cogging, characteristic I, steady SC @1000 rpm | 63.7 / 71.9 µH, 155 mN·m, 50.4 A, 47.4 A |
| demag limit (≤ 1 % rated-torque drop) | 52.4 A rms (2.02·I0); 99.5 % Br knee 47.0 A |
| rotor stress SF = 1 | 19 754 rpm (SF 25.8 at 1000 rpm) |
| S1 robotics @1000 rpm (coupled FEM / lumped) | 8.52 A, 1.77 N·m, 185 W, housing 70 °C / 8.57 A, magnet-limited |
| S1 propeller air (Configure lumped) @1000/1500/2000 rpm | 9.67 / 10.05 / 9.95 A → 209 / 328 / 431 W (FEM lower bound 4.3 / 3.9 / 2.9 A) |
| rated duty from cold | 40 s (robotics, housing 70 °C), 83 s (propeller, winding) |
