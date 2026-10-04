# Physics-regression re-pin, 2026-10-04 (integration branch of #99 / #87)

`tests/physics_baseline.json` was last pinned on 2026-09-29 (materials library,
#65) on the deployed branch, with Triangle as the geometry mesher and the
hybrid (energy mean + Maxwell AC) reported torque. Since then the deployed
branch took the **Coulomb virtual-work torque as the default (#88)** without a
re-pin, and this integration branch adds **Netgen as the CDT mesher (Triangle
removed), gmsh out of process, and TDM as the default eddy method (#87,
91edbe5)**. All eight `test_case_matches_baseline[p2_*]` cases moved.

**Verdict.** Every move is attributed. Seven cases are re-pinned.
`p2_voltage_eddy_rotor` is **held** (not re-pinned): its mean torque moves
-1.25 %, beyond the owner's 1 % torque limit, and -1.18 % of that is the
Coulomb default (#88) alone — an owner decision on the deployed branch whose
pin was never updated. The integration-branch part of that move is -0.08 %.
The owner decides whether the Coulomb pin is accepted; until then that case
stays red, exactly as it already is on the deployed branch.

## Runs (server sandbox, image from 70915ca, `UPDATE_PHYSICS_BASELINE=1`, 4 threads, nice 19)

| Run | Tree | Mesher | Eddy | Torque |
|---|---|---|---|---|
| pins | baseline 2026-09-29 | Triangle | march | hybrid |
| R0 | deployed 009b4ca, `triangle` installed | Triangle | march | **Coulomb** (default) |
| R0b | deployed 009b4ca, `triangle` installed | Triangle | march | hybrid (forced) |
| R6 | `feat/tdm-prototype` 91edbe5, `triangle` installed | Triangle | TDM | Coulomb |
| R5 | integration 24554bf (before the 2026-10-04 merges: TDM 1e41f51) | Netgen | TDM | Coulomb |
| R1 | **integration 70915ca (default)** | **Netgen** | **TDM** | **Coulomb** |
| R2 | 70915ca, `MOTOR_AI_SIM_GEO_CDT=gmsh` (worker) | gmsh | TDM | Coulomb |
| R3 | 70915ca, `SB_EDDY_METHOD=march` | Netgen | march | Coulomb |
| R4 | 70915ca, torque forced to hybrid | Netgen | TDM | hybrid |

A probe on 70915ca confirmed that `p2_eddy` and `p2_demag_eddy` are solved by
TDM (`eddy_method = "tdm"`, no note) on the Netgen mesh; the voltage cases are
marched by rule (voltage drive is not in the TDM wrap).

## How each cause was isolated

* **R0b = pins exactly** (every quantity +0.000 %): nothing on the deployed
  branch except the torque default moved this fixture. #92 (Mesh-tab-only mesh
  settings, gap rule) and #102 (G2-L40 sector fix, mechanical only) do not
  touch it.
* **R0 − R0b = Coulomb default (#88).**
* **R6 = R0 on every case except `p2_demag_eddy`**: the TDM branch is
  bit-equivalent to the march on `p2_eddy` (to < 1e-5) and changes only the
  demag + eddy case (its demag pre-pass rework: shared fixed point, image
  history). **R3 (march) = R1 (TDM)** on the integration branch as well, to
  ≤ 0.04 % on `p2_demag_eddy` ripple_pp and exactly elsewhere: the eddy method
  itself moves no pin; the demag-code change acts on both methods.
* **R5 = R1 except `p2_demag_eddy`**: the TDM update 1e41f51 → 91edbe5 is the
  only difference between them, on that case only.
* **R4 − R0b = the mesher (Triangle → Netgen)** plus the integration branch's
  other code, which R5/R6 show touches only `p2_demag_eddy`. **R2 (gmsh)**
  moves the same quantities by similar amounts with different signs: a
  discretisation dependence, not a solver change.

## Attribution table (relative change vs the 2026-09-29 pins; ripple % in pp)

| Quantity | Coulomb #88 (R0−pins) | Netgen mesh (R4−R0b) | TDM-branch demag code | Total R1 | Owner limit | Verdict |
|---|---|---|---|---|---|---|
| T_avg, current cases | −0.53 % (p2_eddy) | +0.05 % / −0.22 % | −0.10 % (p2_demag_eddy only) | −0.48 … −0.78 % | ≤ 1 % | within |
| T_avg, p2_voltage_eddy | −0.74 % | −0.08 % | 0 | −0.85 % | ≤ 1 % | within |
| **T_avg, p2_voltage_eddy_rotor** | **−1.18 %** | −0.05 % | 0 | **−1.25 %** | ≤ 1 % | **outside — held** |
| T_avg, p2_noload | 0 → 1.6e-4 N·m | → 2.1e-4 N·m | 0 | 2.1e-4 N·m (0.05 % of the 0.41 N·m rated) | ≤ 1 % of rated | within (Coulomb's no-load bias; the hybrid mean is identically 0) |
| T_ripple_pct | ≤ 0.03 pp | ≤ 0.09 pp | ≤ 0.06 pp | ≤ 0.093 pp | max(0.5 pp, 10 %) | within |
| T_ripple_pct, p2_noload | None → defined | | | 40 % of a 2e-4 N·m mean (pp 8.5e-5 N·m) | — | pinned as a number; meaningless as a percentage at no load |
| T_ripple_pp_Nm | −1.8 … +3.7 % | −4.5 … +35 % (p2_demag: 1.07e-3 → 1.44e-3 N·m) | +0.6 % | | via ripple % | within (≤ 0.093 pp) |
| P_fe_W | 0 | +0.58 … +0.95 % | 0 | +0.58 … +0.95 % | total loss ≤ 5 % | within |
| P_cu_W (voltage cases) | 0 | −0.55 … −0.64 % | 0 | same | total loss ≤ 5 % | within |
| P_mag_linear_W | 0 | +1.5 … +2.0 % | −0.5 % (p2_demag_eddy) | +1.5 … +2.0 % | small group; total loss ≤ 5 % | within (total loss moves < 1 %) |
| P_cu_ac_solve_W | 0 | −0.5 % | −0.25 % | −0.52 % | total loss ≤ 5 % | within |
| I_A_thd_pct (voltage) | 0 | +1.9 … +2.5 % rel (1.78 → 1.82 %) | 0 | same | — | within (mesh) |
| demag_br_mean | 0 | −0.64 % / −0.82 % | −0.31 % | −0.64 % / −0.82 % | — | within (mesh) |
| demag_br_min | 0 | −74 % (p2_demag), −87 % (p2_demag_eddy) | −4 %; the 91edbe5 update takes p2_demag_eddy to 0 | 0.029 / 0.000 | — | explained: the minimum is ONE worst element at the magnet corner; gmsh gives −79 % / −100 % too. Per-element demag is a warning since the owner's 2026-10-03 decision; the mean (above) is the physics. |
| T_avg_maxwell_Nm, p2_noload | 0 | 3.9e-5 → 9.7e-5 N·m | 0 | | — | within (the no-load Maxwell mean is numerical zero; gmsh 4.9e-5) |

Total loss (P_cu + P_fe + solid) moves by less than 1 % on every case.

## What was re-pinned

`p2_demag`, `p2_demag_eddy`, `p2_eddy`, `p2_load`, `p2_noload`, `p2_voltage`
and `p2_voltage_eddy` take the R1 values: the `UPDATE_PHYSICS_BASELINE=1` output
of run R1 on 70915ca, which is the code of this commit.
`p2_voltage_eddy_rotor` keeps its 2026-09-29 pins. The non-case tests of
`tests/test_physics_regression.py` pass on 70915ca (R1: 5 passed).

## For the owner

Accept the Coulomb torque on `p2_voltage_eddy_rotor` (−1.18 % vs the hybrid
pin on this coarse 12-step voltage + rotor-eddy case), or keep the case on the
hybrid torque. Re-pinning it is one command once decided:

    UPDATE_PHYSICS_BASELINE=1 python -m pytest -s tests/test_physics_regression.py -k p2_voltage_eddy_rotor

## After merging the final TDM (ccb960b)

Re-run on the merge (ac1585f), update mode against these pins: **no pin
moves**; only the held `p2_voltage_eddy_rotor` differs, by the same amounts as
above. Eddy method actually used per case (probe on ac1585f): `p2_eddy` and
`p2_demag_eddy` run **TDM** (no note, no fallback); `p2_voltage_eddy` and
`p2_voltage_eddy_rotor` are marched by rule ("TDM not applicable: voltage /
PWM drive"). The slow-mode safeguard did not send any pin case back to the
march.
