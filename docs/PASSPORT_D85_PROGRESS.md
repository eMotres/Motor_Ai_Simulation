# Ø85 passport (CIANO28 85 20SW1200 / L13) — finish run progress

Branch `feat/passport-d85-finish`. Model: Claude Opus 5.5. Server sandbox
`/opt/motres/compute/passport-d85-finish/` (deployed API image + mounted branch code,
`--cpus 8` lane 1 / `--cpus 6` lane 2, nice 19, idle IO). One line per finished item.

| time (UTC) | item | result |
|---|---|---|
| 2026-10-10 19:10 | reuse inventory | 10-05/06 Claude run recovered from `/srv/motres/archive/compute_2026-10-10` (`passport-d85/`): 20 °C set, cooling studies (robotics + propeller S1 / peak), Stage A, rotor stress, demag-vs-temperature, Si 48 kHz motor-PWM FEM anchor (finished 10-06 09:39 incl. its same-mesh sine reference: ΔP = −0.7 W). Pipeline commits cherry-picked from `salvage/motor_ai_sim_wt_d85`; its web cooling selector dropped (the deployed Configure already has one). |
| 2026-10-10 19:15 | rated-duty hot grid | STOPPED (owner rule): no in-card steady state at 25.88 A / 1000 rpm with either cooling (robotics coupled loop 2026-09-28 refused at magnet 242.9 °C; S1 = 8.52 A robotics, 4.3 A propeller air). Hot set re-based on the converged robotics S1 state (magnet 69.2 °C, winding 75.1 °C), fixed operating angle γ_ref 1.9505°. |
| 2026-10-10 21:05 | hot re-base launched | lane 1: noload_pinned, hot (fixed angle), demag, loss, extra, checks, refine, loss2, loss3; 20 °C set, d-axis and gap level seeded from the 10-05/06 run. |
| 2026-10-10 21:20 | Stage D (loaded 3-D k_T) | failed twice, stopped: gap element 0.55 mm < ring pitch 0.609 mm; at 0.65 mm the 3-D mesh has no `shaft` elements. k_T = k_ψ (Stage A). |
| 2026-10-10 21:35 | hot static grid (69.2 / 75.1 °C, γ_ref 1.95°) | 60 points, 0 failed; virgin T at I0 5.536 N·m; fixed angle vs per-level vertex ≤ 0.55 % below at 2.5·I0. |
| 2026-10-10 21:40 | propellers | T-Motor G28×9.2 added (torque measured, U8 II KV100 12S, 1896–3709 rpm); D85 list now G28/G30/G32/G36/G40. Live cooling options resolve [robotics, propeller_air]; deployed Configure shows the robotics/propeller toggle for such a die (`hasPropellerCooling`), reached through Codex's card `CIANO28 85 20SW1200 L13`. |
| 2026-10-10 21:40 | Codex 20 °C grid | 15 settled points (12.94/25.88/38.82 A × 250–1500 rpm, γ_ref, 120 steps, demag) extracted from the archive into the card. |
