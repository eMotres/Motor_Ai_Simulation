# Time-periodic (TDM) eddy steady state: prototype, measurements (2026-09-30)

Claude Opus 5.5 (`claude-opus-5-5`), one agent, no delegation, no escalation.
Branch `feat/tdm-prototype` from `pre-migration-freeze-2026-09-15` (f852bd2).
Input: `GPU_TDM_STUDY_2026-09-29.md` §4 (branch `perf/profiling-gpu-tdm`),
`EDDY_SHAFT_SETTLE_2026-09-29.md` (TP-EEC), `CHOLESKY_SPD_2026-09-29.md`.

**Status: in progress** — this file is the resume point; see "Progress log" at the end.

## 1. What was built

`eddy_method="tdm"` (option of `fem_transient_sliding_band` / `em_transient_eval`,
default `"march"`, which is unchanged: its payload does not even gain a key).
Module `src/motor_ai_sim/simulation/time_periodic.py`.

### 1.1 The problem solved

The march's frame k solves, with BDF2 on the uniform step
(dte = 2Δt/3, A_hist = (4A_{k−1} − A_{k−2})/3),

    P_kᵀ[(K(A_k) + σM/dte)A_k − G U_k − f_mag − (σM/dte) A_hist] = 0
    S·dte·U_k − Gᵀ A_k − dte·I_k + Gᵀ A_hist = 0

(`P2Drive.eddy_solve`). TDM solves all N frames of one period at once, with the
history of the first two frames closed by the orbit's own symmetry:

    A_{−1} = H(A_{N−1}),  A_{−2} = H(A_{N−2})

- **Half period (anti-periodic)** where the currents are half-wave antisymmetric,
  half a period is a whole number of slip nodes and the rotor mesh is pole periodic:
  H = the rotor dofs carried one POLE back (`rotor_window.period_shift_map` with half
  the period's rotation) and every value negated; N = steps/2.
- **Full period** otherwise: H = the pole-pair map of the march's splices
  (`_period_shift`), stator unchanged; N = steps.

Each frame's residual is exactly the march's (same normalisation, same 1e-7), so the
orbit is a fixed point of the march. The reported window is then **marched from the
orbit** by the unchanged frame loop — every per-frame quantity (energy torque, ψ, EMF,
the loss split, iron loss, demag) is computed by the production code, and the march
itself verifies the orbit: `tdm.march_vs_orbit_last_frame`.

### 1.2 The method (one, justified)

Newton on the space-time system; the Newton system solved iteratively in time.

1. **Per frame, in parallel over frames** (thread pool, one PARDISO handle per frame,
   thread-local MKL threads): residual, Jacobian K + T (the clamped tangent of the
   march's Newton), the bordered matrix and its Cholesky factor (LU fallback with the
   `_solve_spd` guards). The analysis is kept across Newton iterations.
2. The frames couple only through σM and Gᵀ on the **conductor dofs**, so the Newton
   system reduces exactly to the wrap state w = (δA_{−2}, δA_{−1}) on those dofs:
   (I − T)w = g, T = H∘(linear forward sweep of the period). One sweep = N back-solves
   with the stored factors (block forward substitution in time). This is the shooting /
   Schur-complement form of the cyclic block system, solved by GMRES (right
   preconditioned). The sequential part is back-solves only.
3. **Coarse correction (0th harmonic of a multiharmonic coarse space).** For a slow mode
   of T (e^{−μT}, x = μT ≪ 1: the L155 shaft wall, τ = 21 periods) (I − T)⁻¹ = 1/x + 1/2 +
   O(x). The preconditioner adds 1/x = J̄⁻¹σM/T on the rotor-frame DC of the solid ring
   conductors (shaft, sleeve): ONE static solve with the period-averaged Jacobian,
   projected on the H-invariant (pole-image mean) space and applied to the principal
   BDF2 temporal mode only (α = (3r_{−1} − r_{−2})/2; the spurious root 1/3 mode is left
   alone). The preconditioned eigenvalue of every real mode is (1 − e^{−x})(1 + 1/x) ∈
   [1, 1.3]. The same operator as the march's TP-EEC correction.
4. Globalised by a backtracking line search on the whole space-time residual.

Why not the alternatives:
- **Direct block solve**: memory (study §4.3).
- **Block-Jacobi in time + GMRES**: the BDF2 Jacobi iteration in time diverges (spectral
  radius (5/3)/(1 + 2μΔt/3) > 1 on the (−1)^k mode), and a Krylov method over it needs
  O(N) iterations just to carry information across the period.
- **Parareal (PP-IC)**: its periodic wrap converges like the march per iteration on the
  slow mode; PP-PC needs a coarse periodic solver anyway.
- The sweep form keeps the expensive parts (assembly, factorisation: 85–90 % of a march
  frame) parallel over frames and the sequential part to back-solves.

### 1.3 Demag

The irreversible ratchet is not periodic. TDM solves the orbit on the pristine magnet
(ratchet frozen, as the march's warm-up), then runs the pre-pass ON the orbit
(`time_periodic.demag_march`: the march's own re-entrant frame solve with the ratchet
active), then re-solves the orbit on the ratcheted magnet (warm start) and marches the
reported period with the ratchet active (as the march does).

- `SB_TDM_DEMAG=full` (default): one whole electrical period, like the march's pre-pass.
- `SB_TDM_DEMAG=shortcut` (the owner's): on the pristine orbit, the (magnet, instant)
  with the largest predicted Br loss is located (`predicted_demag`, the ratchet rule
  without the update); only a window of ⌈N_steps/6⌉ frames around it is marched with
  the ratchet; that magnet's Br map is copied to every other magnet through the pole
  image map (`magnet_image_maps`, `map_br_from_reference`).
- Diagnostic of the full mode: `image_min_gap` = how far the one-period pre-pass is from
  the fractional-slot asymptote (element-wise minimum over the pole images: over q
  periods every magnet sees what its images saw in one).

### 1.4 Scope and refusals (loud, `NotImplementedError`)

Current drive (sine; custom/BLDC currents take the full period unless half-wave
antisymmetric). Refused: voltage drive and PWM (the circuit state is not in the wrap;
PWM is out of scope: hundreds of steps per period), series strand paths (saddle-point
bordered matrix), frozen_nu, backward Euler, a mixed schedule, a fractional window,
a full-ring model.

Knobs: `SB_TDM_WORKERS` (frames factorised concurrently), `SB_TDM_MKL_THREADS` (MKL
threads per factorisation), `SB_TDM_TOL` (1e-7), `SB_TDM_HALF=0`, `SB_TDM_COARSE=0`,
`SB_TDM_DEMAG=full|shortcut`, `SB_TDM_DEMAG_WINDOW` (fraction of the period, 1/6).

## 2. Validation

Server sandbox `/opt/motres/compute/tdm-20260930`: a throwaway container per run
from `motres-api:test`, `--cpus 8`, `nice 19`, `ionice -c3`, one run at a time,
never overlapping another agent's multi-CPU container (the queue `runq.sh` waits).
Harness `code/tdm_run.py` = PR #56's `profile_fem_run.py` + `--eddy-method`,
`--demag duty|on|off`, the `l13_peak` case, peak RSS and the Br map. Duties are
the owner's dies copied read-only (`dies/`), cold, `SB_NO_WARM_CACHE=1`.
Serial runs: MKL 8 threads. Parallel runs: `SB_TDM_WORKERS=4`,
`SB_TDM_MKL_THREADS=2` (factorisations), the sequential sweeps on all 8.

### 2.1 Stage 1: Ø40 (CIANO14 40 new / L12 rated), demag off

12s14p, NS = 2 (7 poles per sector), 36 steps; TDM took the half period
(18 frames). Reference: the same code's march pinned to 40 warm-up periods
(`SB_EDDY_WARM=1440`, 1476 frames, plain march, no accelerator).

| quantity | 40-period march | production march (adaptive) | TDM | TDM vs 40-period |
|---|---:|---:|---:|---:|
| T_avg [N·m] | 0.6260005 | 0.6260007 | 0.6260005 | +4.6e-9 |
| ripple [%] | 5.238374 | 5.238452 | 5.238375 | +0.0000 pp |
| V_peak [V] | 11.04809 | 11.04810 | 11.04809 | −7.2e-8 |
| P_mag (2-D) [W] | 3.156654 | 3.156669 | 3.156654 | +6.9e-8 |
| P_shaft [W] | 0.0080848 | 0.0084534 (+4.6 %) | 0.0080848 | +3.6e-6 |
| P_cu AC [W] | 4.516491 | 4.516484 | 4.516492 | +7.7e-8 |
| P_fe [W] | 9.227515 | 9.227683 | 9.227461 | −5.8e-6 |
| total loss [W] | 66.018 | 66.018 | 66.018 | 0 |
| frames solved | 1476 | 110 (74 warm-up) | 36 reported | |
| wall [s] | 414.6 | 54.0 | 31.0 serial, 29.4 parallel | |

TDM is closer to the asymptote than the production march on every quantity
(the march stops at its 2 % gauge; its milliwatt shaft is +4.6 % there).

## Progress log

- 12:05 Stage 0: module, integration, synthetic tests (5 passed locally and on the
  server image: the TDM orbit equals a 150-period march to 1e-7, the half-period
  anti-periodic orbit likewise, the coarse space cuts the Krylov count). Sandbox
  `/opt/motres/compute/tdm-20260930` (queue `runq.sh`, jobs in `jobs.txt`).
