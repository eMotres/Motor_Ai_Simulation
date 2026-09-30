# Time-periodic (TDM) eddy steady state: prototype, measurements (2026-09-30)

Claude Opus 5.5 (`claude-opus-5-5`), one agent, no delegation, no escalation.
Branch `feat/tdm-prototype` from `pre-migration-freeze-2026-09-15` (f852bd2).
Input: `GPU_TDM_STUDY_2026-09-29.md` §4 (branch `perf/profiling-gpu-tdm`),
`EDDY_SHAFT_SETTLE_2026-09-29.md` (TP-EEC), `CHOLESKY_SPD_2026-09-29.md`.

**Status: prototype complete, validated on the three machines; draft PR, not
merged, not deployed.** The progress log at the end is the resume point.

## Summary

- **Method:** Newton on the (anti)periodic space-time system of the BDF2 eddy
  march; each Newton system reduced exactly to the conductor "wrap" state and
  solved by GMRES over forward sweeps of stored per-frame Cholesky factors,
  preconditioned by a rotor-frame DC coarse correction (the 0th harmonic of a
  multiharmonic coarse space = the TP-EEC operator). Half period where the
  winding is half-wave symmetric (all three machines). Frame factorisation,
  residuals and the static start run in parallel over frames.
- **Accuracy (TDM with the full demag pre-pass vs today's march):** torque ≤ 4e-6,
  ripple ≤ 0.0002 pp, total loss ≤ 3e-5, Br map identical to ≤ 0.0014 per
  element, on Ø40, L13 rated/peak and L155. Against 40-period asymptotes TDM is
  closer than the march (Ø40 shaft: 4e-6 against +4.6 %).
- **Speed (one worker, 6 threads, same box):** L155 **3.9×** with demag (full
  pre-pass), **6.1×** with the shortcut, **5.0×** without demag (5.7× with 3–6
  workers); L13 peak 1.7× / 2.6× (shortcut); L13 rated 1.1× / 1.3×; Ø40 1.6×
  without demag, ≈ 1× with demag. Where the march settles in 3 periods, the demag
  pre-pass and the ratchet's re-solves in the reported window (identical in both
  methods) dominate.
- **Demag shortcut (owner's):** ⌈N/6⌉ frames around the worst (magnet, instant)
  of the pristine orbit, that magnet's Br mapped to every pole: torque within
  0.15 %, ripple within 0.21 pp, total loss within 1e-4, Br kept within 0.04 pp
  on all four duties (all fractional-slot). Its per-element map differs (max
  0.09–0.28) because it gives every magnet the same map where the one-period
  pre-pass gives each its own history segment — and neither is the q-period
  asymptote.
- **Recommendation:** GO for the optimizer and passports as an opt-in
  (`eddy_method="tdm"`) on eddy current-drive points, with the full pre-pass
  where demag matters and the shortcut where 0.2 pp of ripple is acceptable;
  keep "march" the default until a routine A/B on more machines. Not for voltage
  drive / PWM (refused), series strand paths (refused), full-ring models
  (refused). Memory: 2.5–3× the march's peak RSS (per-frame factors).

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

**Newton and Krylov behaviour (Ø40).** From the static start the first Newton
step takes the frame residual from 0.13 to 2e-5 (the eddy reaction is nearly
linear); after that the space-time Newton contracts ×6 per iteration: 4
iterations to 5.6e-8. The rate is the same with the clamped tangent, the
unclamped one and an ANALYTIC dν/dB² of the table (`SB_TDM_TANGENT=analytic`,
`dnu_dB2`, tested against differences off the knots), and the same with a
Krylov forcing of 1e-2·rrel or a fixed 0.05: it is set by the piecewise-linear
H(B) table (quadrature points crossing knots), not by the linear algebra. The
synthetic smooth problem converges quadratically (tests). So the forcing is a
fixed 0.01 by default: 23 GMRES iterations in all instead of 62, same Newton count.

| variant (Ø40, demag off, one worker) | period | Newton | GMRES total | static start [s] | Newton [s] |
|---|---|---:|---:|---:|---:|
| clamped tangent, η = 1e-2·rrel, sequential exact static start | half, 18 | 4 | 107 | 8.5 | 7.4 |
| same, start = frame 0 projected (no per-frame static) | half | 18 | 271 | 1.3 | 21.4 |
| exact (unclamped) tangent | half | 4 | 81 | 8.0 | 6.0 |
| analytic tangent | full, 36 | 4 | 62 | 16.0 | 11.2 |
| η = 0.05 fixed | full | 4 | 19 | 16.0 | 8.2 |
| η = 0.01 fixed | full | 4 | 23 | 16.0 | 8.9 |

(The "full" rows ran before the half-period symmetry test was relaxed from 1e-9 to
1e-4 of the magnet source: the Ø40 mesh's element magnetisation is pole
antisymmetric to 6.3e-6 only. Full and half periods give the same orbit.)

### 2.2 Stage 1: Ø40 with demag (the duty's own setting)

Reference: the production march with its demag pre-pass (146 frames: 74 warm-up,
36 pre-pass, 36 reported). All at 6 threads, one worker; the box was shared with
a 4-thread shaftloss job (load 5–11), so the walls carry ±15 % noise.

| quantity | march | TDM, full pre-pass | TDM, owner's shortcut |
|---|---:|---:|---:|
| T_avg [N·m] | 0.6218693 | 0.6218688 (−7.6e-7) | 0.6220011 (+2.1e-4) |
| ripple [%] | 5.642602 | 5.644492 (+0.002 pp) | 5.558303 (−0.084 pp) |
| V_peak [V] | 10.99277 | 10.99277 (−4e-7) | 10.98384 (−8e-4) |
| P_mag 2-D [W] | 3.093844 | 3.094255 (+1.3e-4) | 3.097247 (+1.1e-3) |
| P_shaft [W] | 0.0085229 | 0.0082894 (−2.7 %) | 0.0083421 (−2.1 %) |
| P_cu AC [W] | 4.438217 | 4.438434 (+4.9e-5) | 4.438592 (+8.4e-5) |
| P_fe [W] | 9.139391 | 9.139300 (−1.0e-5) | 9.143353 (+4.3e-4) |
| total loss [W] | 65.789 | 65.789 (0) | 65.796 (+1.1e-4) |
| Br kept [% vol] | 99.301 | 99.301 (0) | 99.305 (+0.004 pp) |
| Br map vs march: max / mean |ΔBr| (de-rated elements) | – | 0.0014 / 8e-5 | 0.283 / 0.011 |
| frames: warm-up / pre-pass / reported | 74 / 36 / 36 | 0 / 36 / 36 | 0 / 7 / 36 |
| wall [s] | 88 | 96 | 85 |
| of it: static start, Newton, pre-pass, re-solve, reported period | – | 8.4, 6.4, 40.7, 8.0, 19.1 | 11.0, 9.3, 9.7, 8.7, 27.6 |

- **TDM + full pre-pass reproduces the march element for element** once the
  pre-pass runs on the march's own pre-pass positions (θ < 0). A first version
  marched it on the reported positions (0 ≤ θ < 360°el): on a fractional-slot
  rotor that hands every magnet a different part of its history (the
  rotor-frame period is q = 7 electrical periods here), and the Br maps then
  differ per element by up to 0.28 while Br kept agreed to 0.015 pp.
- **The shortcut** (magnet tag 106's window, frames 25–31, 7 frames, its Br copied
  to all 7 magnets): torque +0.02 %, ripple −0.08 pp, total loss +0.01 %. The per
  element map differs (max 0.28) because it gives every magnet the SAME map
  while the one-period pre-pass gives each magnet its own segment of history.
- **The one-period pre-pass is not the fractional-slot asymptote either**: over
  q periods every magnet sees what its images saw in one; the element-wise image
  minimum of the full pre-pass is up to 0.28 lower at single elements (0.05 %
  of the magnet area on average). The reported window keeps the ratchet on, so it
  still moves (the march-vs-orbit check reads 8.9e-4 at the last frame) — in
  the march exactly as in TDM.
- **No wall-time gain on this machine with demag**: its warm-up is only 3
  periods; the demag pre-pass and the ratchet's re-solves in the reported window
  (12 solves per frame: the ratchet trips on almost every pass) cost the same in
  both methods.

### 2.3 Stage 2: L13 (CIANO28 85 20SW1200 / L13), demag on (the duty's setting)

24s28p, NS = 4 (7 poles per sector), 40 steps: TDM half period, 20 frames. From
here on every TDM run stops in the owner's terms (§2.6) with the forcing 0.01.

**Rated (26.1 A, 1000 rpm):**

| quantity | march | TDM, full pre-pass | TDM, shortcut |
|---|---:|---:|---:|
| T_avg [N·m] | 5.399462 | 5.399443 (−3.5e-6) | 5.407786 (+1.5e-3) |
| ripple [%] | 5.280835 | 5.280785 (−0.0000 pp) | 5.073569 (−0.21 pp) |
| V_peak [V] | 15.88827 | 15.88881 (+3.4e-5) | 15.84684 (−2.6e-3) |
| P_mag 2-D [W] | 0.440848 | 0.441547 (+1.6e-3) | 0.440348 (−1.1e-3) |
| P_shaft [W] | 0.439172 | 0.441768 (+5.9e-3) | 0.435612 (−8.1e-3) |
| P_cu AC [W] | 2.302353 | 2.303297 (+4.1e-4) | 2.312500 (+4.4e-3) |
| P_fe [W] | 2.478466 | 2.479051 (+2.4e-4) | 2.484744 (+2.5e-3) |
| total loss [W] | 198.717 | 198.722 (+2.5e-5) | 198.729 (+6.0e-5) |
| Br kept [% vol] | 98.927 | 98.927 (0) | 98.963 (+0.036 pp) |
| Br map vs march: max / mean abs ΔBr | – | 0.0013 / 8e-5 | 0.150 / 0.017 |
| frames: warm-up / pre-pass / reported | 122 / 40 / 40 | 0 / 40 / 40 | 0 / 7 / 40 |
| Newton (orbit + re-solve), GMRES, factorisations | – | 6 + 4, 29, 200 | 6 + 4, 29, 200 |
| wall [s] | 160 | 145 | 120 |
| of it: static, Newton, pre-pass, re-solve, reported period | – | 7.0, 8.2, 60.3, 4.8, 49.4 | 9.9, 11.1, 14.6, 6.1, 56.4 |
| peak RSS [MB] | 433 | 1340 | 1346 |

**Peak (45.96 A, 1000 rpm):**

| quantity | march | TDM, full pre-pass | TDM, shortcut |
|---|---:|---:|---:|
| T_avg [N·m] | 9.384038 | 9.384020 (−1.8e-6) | 9.383612 (−4.5e-5) |
| ripple [%] | 6.428616 | 6.428737 (+0.0001 pp) | 6.428133 (−0.0005 pp) |
| V_peak [V] | 22.22790 | 22.22857 (+3.0e-5) | 22.19807 (−1.3e-3) |
| P_mag 2-D [W] | 0.889268 | 0.889607 (+3.8e-4) | 0.889510 (+2.7e-4) |
| P_shaft [W] | 1.806457 | 1.807053 (+3.3e-4) | 1.805266 (−6.6e-4) |
| P_cu AC [W] | 3.101832 | 3.101584 (−8.0e-5) | 3.100902 (−3.0e-4) |
| P_fe [W] | 2.820227 | 2.820252 (+8.7e-6) | 2.820236 (+3.0e-6) |
| total loss [W] | 682.776 | 682.777 (+1.5e-6) | 682.774 (−2.9e-6) |
| Br kept [% vol] | 99.764 | 99.764 (0) | 99.764 (0) |
| Br map vs march: max / mean abs ΔBr | – | 0.0005 / 5e-5 | 0.087 / 0.007 |
| frames: warm-up / pre-pass / reported | 282 / 40 / 40 | 0 / 40 / 40 | 0 / 7 / 40 |
| Newton (orbit + re-solve), GMRES, factorisations | – | 8 + 3, 29, 220 | 8 + 3, 29, 220 |
| wall [s] | 159 | 93 (1.7×) | 61 (2.6×) |
| of it: static, Newton, pre-pass, re-solve, reported period | – | 6.1, 9.2, 43.0, 3.1, 18.2 | 6.0, 9.2, 8.2, 3.0, 20.7 |
| peak RSS [MB] | 497 | 1335 | 1340 |

### 2.5 Stage 3: L155 (CIANO10 200 opt / L155 motor, rated 1x9 mm), demag on

12s10p, NS = 2 (5 poles per sector), 36 steps: TDM half period, 18 frames. The
march is today's (image-mean start + TP-EEC DC correction, #63): 614 frames,
settled. The 40-period asymptotes of `EDDY_SHAFT_SETTLE_2026-09-29.md` §3 are
quoted for the slow bodies.

| quantity | march | TDM, full pre-pass | TDM, shortcut | 40-period asymptote (#63) |
|---|---:|---:|---:|---:|
| T_avg [N·m] | 187.9337 | 187.9342 (+2.7e-6) | 187.9371 (+1.8e-5) | – |
| ripple [%] | 1.564382 | 1.564419 (+0.0000 pp) | 1.564213 (−0.0002 pp) | – |
| V_peak [V] | 436.6521 | 436.6516 (−1.2e-6) | 436.6589 (+1.6e-5) | – |
| P_mag reported [W] | 92.319 | 92.334 (+1.6e-4) | 92.339 (+2.2e-4) | 92.320 |
| P_shaft [W] | 3.9944 | 4.0703 (+1.9 %) | 4.0686 (+1.9 %) | 4.009 |
| P_sleeve [W] | 9.93144 | 9.93231 (+8.7e-5) | 9.93255 (+1.1e-4) | 9.9315 |
| P_cu AC [W] | 609.8097 | 609.8176 (+1.3e-5) | 609.8263 (+2.7e-5) | 609.795 |
| P_fe [W] | 1444.685 | 1444.619 (−4.6e-5) | 1444.665 (−1.4e-5) | – |
| total loss [W] | 3820.339 | 3820.373 (+8.9e-6) | 3820.431 (+2.4e-5) | – |
| Br kept [% vol] | 99.213 | 99.213 (0) | 99.213 (0) | – |
| Br map vs march: max abs ΔBr | – | < 1e-5 (identical) | see §3 | – |
| frames: warm-up / pre-pass / reported | 578 / 36 / 36 | 0 / 36 / 36 | 0 / 7 / 36 | 1476 |
| Newton (orbit + re-solve), GMRES, factorisations | – | 5 + 2, 32, 126 | 5 + 2, 32, 126 | – |
| wall [s], one worker | 338 | 87 (**3.9×**) | 55 (**6.1×**) | – |
| of it: static, Newton, pre-pass, re-solve, reported period | – | 12.9, 11.3, 18.2, 2.9, 8.5 | 8.3, 8.6, 2.8, 2.5, 9.2 | – |
| wall [s], 3 workers × 2 MKL threads | – | – | 55 | – |
| peak RSS [MB] | 627 | 1757 | 1732 (1859 with 3 workers) | – |

**Without demag** (the pure eddy steady state; `--demag off` on both):

| quantity | march (578 frames) | TDM, 1 worker | TDM, 3 × 2 | TDM, 6 × 1 |
|---|---:|---:|---:|---:|
| T_avg [N·m] | 188.8432 | 188.8435 (+1.2e-6) | same | same |
| ripple [%] | 1.612974 | 1.613127 (+0.0002 pp) | same | same |
| V_peak [V] | 438.1814 | 438.1812 (−5.4e-7) | same | same |
| P_mag reported [W] | 91.559 | 91.577 (+2.0e-4) | same | same |
| P_shaft [W] | 4.00782 | 4.02079 (+0.32 %) | same | same |
| P_sleeve [W] | 9.83514 | 9.83642 (+1.3e-4) | same | same |
| P_cu AC [W] | 613.5301 | 613.5448 (+2.4e-5) | same | same |
| P_fe [W] | 1457.723 | 1457.723 (+1.4e-7) | 1457.718 (−3.2e-6) | 1457.718 (−3.2e-6) |
| total loss [W] | 3836.255 | 3836.302 (+1.2e-5) | 3836.297 (+1.1e-5) | 3836.297 (+1.1e-5) |
| wall [s] | 263 | 53 (**5.0×**) | 46 (**5.7×**) | 46 (**5.8×**) |
| of it: static, Newton, reported period | – | 9.8, 9.7, 8.1 | 8.1, 7.3, 7.0 | 6.6, 7.8, 7.0 |
| Newton's parts: Jacobians, residuals, sweeps (GMRES) | – | 4.2, 1.7, 3.6 | 2.0, 0.8, 4.2 | 1.6, 0.7, 5.2 |
| peak RSS [MB] | 633 | 1727 | 1832 | 1935 |

- The orbit is solved in 5 Newton iterations (0.12 → 3.8e-4 → 1.3e-4 → 4.7e-5 →
  1.4e-5 → 4.6e-6), 28 GMRES iterations in all; the owner's terms stopped it
  (torque moved 5e-7, ripple 0.0002 pp, eddy loss 6e-6 between the last two).
- **Wall time outside TDM** (mesh, materials, the frozen-permeability Ld/Lq probe,
  iron loss, the frequency-domain cross-check, …) is ≈ 25 s here and is the same
  in both methods; the eddy part itself went from ≈ 238 s to ≈ 27 s (≈ 9×).
- **Parallel in time pays little at this size:** the frame-parallel parts
  (Jacobians 4.2 → 1.6 s, residuals, the static start 9.8 → 6.6 s) scale 1.5–2.7×
  with threads (the Python assembly holds the GIL part of the time), but the
  sequential sweeps and the reported period's march do not, and the fixed 25 s
  does not move. 4–8 concurrent steps would need process workers (fork) to go
  further; not worth it before the reported march is parallelised (§5).
- **The shaft** (4 W of 3.8 kW) reads 0.3–1.9 % above the march, whose own value
  is 0.4 % below the 40-period asymptote. The owner's stop does not wait for the
  slowest body's DC mode; the state-residual stop (`SB_TDM_STOP=residual`) is
  measured in §2.7.

### 2.6 Output path, stopping rule and torque method (coordinator, 2026-09-30)

- **One post-processing path.** TDM never reports a frame it solved itself: the
  reported period is marched by the unchanged frame loop from the orbit, so the
  frame structure (rotor angle, slip projection, A, currents, ψ) and every
  per-frame quantity — the shipped torque, a future Coulomb virtual-work torque,
  Maxwell, ψ/EMF, the loss split, iron loss, demag — come out of the same code as
  after a march. Nothing torque-specific is computed inside the TDM iteration
  except the stopping monitor below.
- **Stopping in the owner's terms** (`SB_TDM_STOP=owner`, the default): after each
  Newton iterate the monitor evaluates the SHIPPED torque of the orbit (the frame
  loop's `_torque2` Maxwell series and `_psi2` flux linkage through
  `sb_postproc.space_vector_hybrid_torque`, i.e. `energy_mean+maxwell_ripple`) and
  the conductor loss (the march's σE² integrand); the Newton stops when two
  iterates differ by < 0.1 % in the mean torque, < 0.05 pp in the ripple and
  < 0.5 % in the eddy loss, on an iterate whose state residual is < 1e-5; or when
  the state residual meets the march's 1e-7. `SB_TDM_STOP=residual` keeps only
  the latter. Both are recorded (`tdm.solve.stopped_by`, the per-iterate monitor).
- **Torque method**: every T_avg / ripple in these tables is the shipped
  `energy_mean+maxwell_ripple` (flux-linkage space-vector mean, raw Maxwell AC),
  the method the result carries for every eddy run (`torque_method`).

## Progress log

- 12:05 Stage 0: module, integration, synthetic tests (5 passed locally and on the
  server image: the TDM orbit equals a 150-period march to 1e-7, the half-period
  anti-periodic orbit likewise, the coarse space cuts the Krylov count). Sandbox
  `/opt/motres/compute/tdm-20260930` (queue `runq.sh`, jobs in `jobs.txt`).
