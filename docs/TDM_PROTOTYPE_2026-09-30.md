# Time-periodic (TDM) eddy steady state: prototype, measurements (2026-09-30)

Claude Opus 5.5 (`claude-opus-5-5`), one agent, no delegation, no escalation.
Branch `feat/tdm-prototype` from `pre-migration-freeze-2026-09-15` (f852bd2).
Input: `GPU_TDM_STUDY_2026-09-29.md` §4 (branch `perf/profiling-gpu-tdm`),
`EDDY_SHAFT_SETTLE_2026-09-29.md` (TP-EEC), `CHOLESKY_SPD_2026-09-29.md`.

**Second Codex review (2026-10-03): §4** — closure march, hard per-group state
gates, round-off half period, per-element demag settle, map checks, bounded inexact
Newton, shortcut behind an explicit argument, optimizer verdicts, memory hygiene.

**Status:** validated on the three machines; TDM is the default (§0); the Coulomb,
demag-shortcut and gap-layer checks are done (§2.5–2.7). The Codex review's findings
are answered and fixed, and the fixes re-validated (§3). Draft PR #87, not merged,
not deployed. The progress log at the end is the resume point.

**After the Codex review (§3), in short:**

- The reported period is accepted only if it reproduces the orbit in torque, ripple
  and every conductor group's loss (the gate). A rejected attempt is thrown away
  whole and the request is solved again: strictly, then as a march.
- The full period is the default.
- The demag pre-pass repeats to a fixed point in BOTH methods, and a reported period
  in which Br still moves is labelled `steady_state: false`.
- Re-validated on five duties plus the severe-demag gap-layer case. March and TDM
  agree to ≤ 1.8e-6 in torque and ≤ 0.0004 pp in ripple, and the gate never fired.
- Speed now: L155 3.5×, L13 peak 1.4×, the others about 1×.
- Peak RSS is up to 4.5 GB (L155).

The numbers in §2 were measured before the review: half period, one pre-pass
period.

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
  0.15 %, total loss within 6e-4, Br kept within 0.04 pp on six duties (all
  fractional-slot); ripple within 0.21 pp at rated/peak but **0.54 pp (9 %) low in
  deep field weakening** (Ø40, γ 60°, 20 000 rpm, §2.6) — not qualified, stays an
  option. Its per-element map differs (max 0.09–0.33) because it gives every
  magnet the same map where the one-period pre-pass gives each its own history
  segment — and neither is the q-period asymptote.
- **TDM + Coulomb** (§2.5): Coulomb mean within 3e-6 and ripple within 0.002 pp of
  the march's, layer self-check identical, on Ø40, L13 and L155.
- **Gap layers** (§2.7): the Coulomb agent's nine cases with TDM: Ø40 and L155
  equal to the march (torque ≤ 1e-5, ripple ≤ 0.0005 pp, total ≤ 4.4e-4; the shaft
  where the march is unsettled), L155 5.5–6.9× faster. L155 at 2 per side, "not
  settled" by the march, settles with TDM and keeps its +4.1 % iron and 18 %
  self-check — a steady-state property of that ring, not a transient. L13 (Br
  kept 20 %) differs in period 1 only because the one-period demag pre-pass has
  not converged the ratchet in either method; three periods agree to 1e-4.
- **Recommendation: GO**, and the owner switched it on (§0). TDM is the default eddy
  method of every steady-state eddy run: full period, the full demag pre-pass to its
  fixed point, and the acceptance gate. The shortcut is EXPERIMENTAL. Voltage drive /
  PWM, series strand paths and full-ring models are marched automatically with a
  note, and a rejected TDM attempt is re-solved as a clean march.
- **Memory:** 3–6× the march's peak RSS (per-frame factors, full period); the solve
  pool's estimate follows (4.6 GB).

## 0. Default switch (owner's decision, 2026-09-30)

"TDM + Coulomb for all runs": `eddy_method` now defaults to `"tdm"` for every
steady-state eddy run — the EM tab, the coupled loop, sweeps and the optimizer
(`refine_proc`), agent drafts / MCP simulate and passports all reach the solver
through `em_transient_eval` without choosing, so they all get TDM.

- `eddy_method=None` resolves to the argument, else `SB_EDDY_METHOD`, else the
  config's `simulation.eddy_method`, else `"tdm"` (`time_periodic.
  resolve_eddy_method`). `"march"` stays selectable by any of the three.
- **Automatic march, with a one-line note.** A run TDM cannot serve
  (`time_periodic.tdm_refusals`: voltage and PWM drive, series strand paths, a
  full-ring model, frozen ν, a six-phase winding, an external/closed-loop
  excitation object, a fractional window, backward Euler) is marched; the result
  says `eddy_method: "march"`, `eddy_method_requested: "tdm"` and
  `eddy_method_note: "march: TDM not applicable (…)"`.
- **Transactional attempts (since the Codex review, §3).** A TDM attempt that
  fails at any stage — or whose reported period fails the acceptance gate — is
  NOT repaired in place: the whole solve is discarded (`TdmAttemptFailed`) and
  `fem_transient_sliding_band` solves the request again from scratch: after a
  gate failure on the owner's-terms stop, TDM once more with the strict 1e-7
  state-residual stop; otherwise, or if that fails too, a march:
  `eddy_method_note: "march: TDM failed (<stage>: …) — marched instead"`,
  `tdm.attempts` records every attempt. A Stop (a BaseException) is not caught.
- **The acceptance gate (§3.4).** The reported period, marched from the orbit by
  the unchanged frame loop, must reproduce the orbit frame for frame in the
  owner's observables (torque mean 0.1 %, ripple 0.05 pp, every conductor
  group's loss 0.5 %); `tdm.gate` records it.
- **Demag fixed point (§3.3, both methods).** The pre-pass repeats, continuous in
  time, until one period moves Br by ≤ 1e-3 (worst magnet, area mean); a
  reported period in which Br still moves is labelled `demag_settled: false`,
  `steady_state: false` — never reported as a steady state.
- `tdm_demag="full"` (default, the pre-pass above on the orbit) or `"shortcut"`
  (the owner's 1/6-period window: **EXPERIMENTAL**, not qualified, announced in
  `eddy_method_note`), argument or `SB_TDM_DEMAG`; no workflow sets it.
- The FULL period is the default; the half period is opt-in (`SB_TDM_HALF=1`,
  §3.2).
- With `torque_method="coulomb"` (the default since #88) the stopping monitor uses the Coulomb
  virtual-work torque of each iterate's frames (`virtual_work_torque.
  frame_torques`, the loop's own call); the reported period's Coulomb series,
  mean, ripple and layer self-check come out of the frame loop as for the march.
- Solve pool: the fallback per-solve RSS estimate goes 1500 → 2500 MB (TDM keeps
  a factor per frame: 1.0–1.9 GB measured); six parallel solves need 15 GB of the
  API container's 40 GB. The adaptive estimate (largest recent child) is unchanged.
- The progress strip names the TDM stages ("eddy warm-up (time-periodic steady
  state, TDM): Newton k, residual …", the demag pre-pass frame by frame); a Stop
  is honoured at each.
- Tests that pin the MARCH's own behaviour (the warm-up, its settle verdict, the
  warm seed, seed reproducibility) now request `eddy_method="march"`.
- The Simulation summary (what the UI and reports read) carries `eddy_method`,
  `eddy_method_requested`, `eddy_method_note`, `demag_settled` and `steady_state`.

## 1. What was built

`eddy_method="tdm"` (option of `fem_transient_sliding_band` / `em_transient_eval`).
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

- `SB_TDM_DEMAG=full` (default): whole electrical periods, like the march's pre-pass,
  REPEATED to the demag fixed point since the Codex review (§3.3): each further period
  is the next one in time (Br relabelled one period back by the eddy splice's own
  pole-pair map, the orbit re-solved on it), until a period moves Br by ≤
  `SB_DEMAG_SETTLE_TOL` (1e-3), at most `SB_DEMAG_PREPASS_MAX` (8) periods.
- `SB_TDM_DEMAG=shortcut` (the owner's, **EXPERIMENTAL** — not qualified, §2.6): on the pristine orbit, the (magnet, instant)
  with the largest predicted Br loss is located (`predicted_demag`, the ratchet rule
  without the update); only a window of ⌈N_steps/6⌉ frames around it is marched with
  the ratchet; that magnet's Br map is copied to every other magnet through the pole
  image map (`magnet_image_maps`, `map_br_from_reference`).
- Diagnostic of the full mode: `image_min_gap` = how far the one-period pre-pass is from
  the fractional-slot asymptote (element-wise minimum over the pole images: over q
  periods every magnet sees what its images saw in one).

### 1.4 Scope, and what is marched instead (with a note, §0)

Current drive (sine; custom/BLDC currents take the full period unless half-wave
antisymmetric). Marched: voltage drive and PWM (the circuit state is not in the
wrap; PWM is out of scope: hundreds of steps per period), series strand paths
(saddle-point bordered matrix), frozen_nu, backward Euler, a mixed schedule, a
fractional window, a full-ring model, a six-phase winding, an external excitation
object.

**Voltage drive — designed, not built.** The wrap state would gain the two
line-to-line currents; ψ is a linear functional of the coil dofs, which are
conductor dofs, so the sweep already carries what the circuit rows need; each
frame's solve would take the two circuit rows by the `ve_newton` Schur complement
(three back-solves and a 2×2). The reported window would have to start without
the voltage settle prefix (the DC-orbit solve and the Aitken anchors live in it).
The periodic wrap removes the flux DC mode (τ = L/R ≈ 20 periods) the 10-period
settle exists for, so the gain would be large; it is a separate change of the
voltage path.

Knobs: `SB_EDDY_METHOD`, `SB_TDM_DEMAG=full|shortcut`, `SB_TDM_STOP=owner|residual`,
`SB_TDM_ETA` (0.01; `adaptive`), `SB_TDM_TANGENT=clamped|exact|analytic`,
`SB_TDM_START=static_seq|static_par|static|project`, `SB_TDM_WORKERS` (frames
factorised concurrently), `SB_TDM_MKL_THREADS` (MKL threads per factorisation),
`SB_TDM_TOL` (1e-7), `SB_TDM_MAX_NEWTON` (25), `SB_TDM_HALF=1` (opt-in half period,
taken only at round-off symmetry since §4), `SB_TDM_COARSE=0`, `SB_TDM_DEMAG_WINDOW`
(fraction of the period, 1/6); both methods `SB_DEMAG_SETTLE_TOL` (1e-3),
`SB_DEMAG_SETTLE_ELEMENT_TOL` (1e-2, §4), `SB_DEMAG_PREPASS_MAX` (8); tests only
`SB_TDM_FAULT=<stage>`. Since the second review (§4) the closure and report gates
are constants (`time_periodic.CLOSURE_GATE`, `REPORT_GATE`) with no environment knob,
`SB_TDM_HALF_TOL` is gone, and `SB_TDM_DEMAG` can no longer select the shortcut.

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
here on every TDM run stops in the owner's terms (§2.9) with the forcing 0.01.

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

### 2.4 Stage 3: L155 (CIANO10 200 opt / L155 motor, rated 1x9 mm), demag on

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
  measured in §2.8.

### 2.5 TDM + Coulomb end to end (`torque_method="coulomb"`)

Same duties, demag as the duty says (full pre-pass), 4 threads, code b0474ba.
The reported torque is `coulomb_virtual_work` in both methods; the layer
self-check (`coulomb_torque.layer_self_check`) is present in both.

| machine | Coulomb T_avg: march → TDM | Coulomb ripple [%]: march → TDM | self-check (max ring difference / p-p): march / TDM | total loss Δ | wall: march → TDM |
|---|---|---|---|---:|---|
| Ø40 rated | 0.6155447 → 0.6155441 (−8.9e-7) | 5.730637 → 5.732552 (+0.0019 pp) | 2.466 % / 2.465 % | 0 | 98 → 90 s |
| L13 rated | 5.356449 → 5.356433 (−3.1e-6) | 5.371824 → 5.371773 (−0.0001 pp) | 2.519 % / 2.519 % | +2.5e-5 | 156 → 139 s |
| L155 rated | 184.42804 → 184.42842 (+2.1e-6) | 1.943823 → 1.943828 (+0.0000 pp) | 66.45 % / 66.45 % | +8.9e-6 | 329 → 76 s (**4.3×**) |

The stopping monitor used Coulomb on these runs (`tdm.solve.newton[*].monitor.
torque_method = "coulomb_virtual_work"`). The L155 self-check (66 % of the p-p,
`ripple_mesh_limited`) is the air-gap mesh at the duty's gap layers, identical in
both methods: the gap-layer study (§2.7) is the answer to it, not TDM.

### 2.6 The demag shortcut at peak duty and in deep field weakening

Ø40 L12, Coulomb torque, 4 threads. "Peak": the duty (48.79 A, γ 10°, 14 400 rpm).
"Deep FW": 48.79 A at γ 60° and 20 000 rpm. L13 peak (hybrid torque) is in §2.3.

| point | quantity | march | TDM, full pre-pass | TDM, shortcut |
|---|---|---:|---:|---:|
| Ø40 peak | T_avg [N·m] | 0.6900492 | 0.6900480 (−1.8e-6) | 0.6902509 (+2.9e-4) |
| | ripple [%] | 3.696523 | 3.699563 (+0.003 pp) | 3.642864 (−0.054 pp) |
| | total loss [W] | 101.757 | 101.758 (+9.8e-6) | 101.778 (+2.1e-4) |
| | Br kept [% vol] | 98.580 | 98.580 (0) | 98.597 (+0.017 pp) |
| | Br map: max / mean abs ΔBr | – | 0.0004 / 4e-5 | 0.325 / 0.014 |
| | wall [s] | 120 | 110 | 78 |
| Ø40 deep FW | T_avg [N·m] | 0.3785175 | 0.3785144 (−8.2e-6) | 0.3788628 (+9.1e-4) |
| | ripple [%] | 5.930361 | 5.929643 (−0.0007 pp) | 5.386467 (**−0.544 pp, −9.2 %**) |
| | total loss [W] | 105.486 | 105.487 (+9.5e-6) | 105.543 (+5.4e-4) |
| | Br kept [% vol] | 98.418 | 98.418 (0) | 98.442 (+0.024 pp) |
| | Br map: max / mean abs ΔBr | – | 0.0031 / 6e-5 | 0.143 / 0.012 |
| | wall [s] | 127 | 117 | 81 |

- **Full pre-pass on the orbit = the march**, at peak and in deep FW alike
  (torque ≤ 8e-6, ripple ≤ 0.003 pp, Br map ≤ 0.003 per element).
- **The shortcut is NOT yet qualified**: torque and total loss stay within
  0.1 %, but in deep field weakening the ripple comes out 0.54 pp (9 %) low —
  outside the 0.5 pp line, inside the 10 % one. The shortcut gives every magnet the
  worst magnet's map; the one-period pre-pass gives each magnet its own segment of
  history, and the per-magnet spread is what the ripple sees. So FULL stays the
  default (as advised); the shortcut stays an option (`tdm_demag="shortcut"`).
- Neither is the fractional-slot asymptote: the element-wise image minimum of the
  full pre-pass is up to 0.25–0.32 lower at single elements (0.12 % of the magnet
  area on average). Qualifying the shortcut needs that asymptote as the reference
  (a q-period march, 7 periods here), not the one-period pre-pass.

### 2.7 Gap-layer study cases with TDM (Coulomb torque)

The Coulomb agent's cases (`docs/GAP_LAYERS_CASES.md`, `scripts/gap_layers_study/`
on `feat/coulomb-default-gap3`, inputs and config of `54f33f2`), unchanged, with
`SB_EDDY_METHOD=tdm` and `--torque-method coulomb`; code of this branch (b0474ba),
4 threads. The march columns are `COULOMB_TORQUE_2026-09-30.md` §6.1. Losses are
the solver's solved values [W].

| quantity | Ø40 1/side | Ø40 2/side | Ø40 3/side | L13 1/side | L13 2/side | L13 3/side | L155 1/side | L155 2/side | L155 3/side |
|---|---|---|---|---|---|---|---|---|---|
| Coulomb mean torque N·m | 0.616654 | 0.616667 | 0.616729 | 0.788373 | 0.788663 | 0.789082 | 184.59274 | 184.60566 | 184.60849 |
| ripple p-p N·m (%) | 0.040749 (6.61) | 0.039227 (6.36) | 0.040572 (6.58) | 0.245660 (31.16) | 0.245722 (31.16) | 0.245676 (31.13) | 3.636424 (1.97) | 3.782933 (2.05) | 3.775024 (2.04) |
| self-check (gate 5 %) | 2.13 % | 0.81 % | 0.055 % | 1.07 % | 1.87 % | 0.92 % | 1.17 % | **18.3 %** | **6.8 %** |
| iron W | 9.231 | 9.235 | 9.232 | 2.073 | 2.073 | 2.073 | 1459.502 | 1518.562 | 1460.340 |
| magnet W | 3.360 | 3.362 | 3.360 | 0.530 | 0.529 | 0.530 | 106.907 | 106.847 | 107.298 |
| shaft W | 0.008 | 0.008 | 0.008 | 11.895 | 11.893 | 11.891 | 3.804 | 3.807 | 3.826 |
| sleeve W | 0 | 0 | 0 | 0 | 0 | 0 | 10.039 | 10.035 | 10.058 |
| copper DC W | 49.109 | 49.109 | 49.109 | 259.793 | 259.793 | 259.793 | 1659.599 | 1659.599 | 1659.599 |
| copper AC W | 4.475 | 4.464 | 4.472 | 0.113 | 0.113 | 0.113 | 614.010 | 615.180 | 614.830 |
| **total loss W** | 66.184 | 66.179 | 66.181 | 274.404 | 274.402 | 274.400 | 3853.863 | 3914.030 | 3855.951 |
| frames marched (demag pre-pass) + Newton iterations | 96 (48) + 5 | 96 (48) + 5 | 96 (48) + 5 | 120 (60) + 22 | 120 (60) + 22 | 120 (60) + 22 | 144 (72) + 5 | 144 (72) + 5, **settled** (5.1e-6) | 168 (84) + 5 |
| wall time s | 163 | 204 | 281 | 317 | 357 | 355 | 135 | 210 | 191 |

**Δ TDM − march** (relative unless marked):

| quantity | Ø40 1 | Ø40 2 | Ø40 3 | L13 1 | L13 2 | L13 3 | L155 1 | L155 2 | L155 3 |
|---|---|---|---|---|---|---|---|---|---|
| Coulomb mean | −1.1e-6 | −7.5e-7 | −1.1e-6 | +1.3e-4 | −1.6e-4 | +1.3e-4 | +1.0e-6 | +9.8e-6 | +1.2e-6 |
| ripple | −0.0001 pp | +0.0005 pp | +0.0002 pp | +0.20 pp | +0.20 pp | +0.20 pp | +0.0001 pp | +0.0004 pp | +0.0001 pp |
| self-check | 0 | 0 | 0 | −0.01 pp | −0.01 pp | −0.01 pp | 0 | 0 | 0 |
| iron | −1.1e-4 | −1.1e-4 | 0 | 0 | 0 | 0 | +3.9e-4 | +2.9e-4 | +4.4e-4 |
| magnet | +1.4e-4 | +1.6e-4 | +1.8e-4 | +4.2 % | +4.2 % | +4.2 % | +2.3e-5 | −1.4e-4 | +4e-5 |
| shaft | −3.9 % | −3.9 % | −4.0 % | −2.4 % | −2.4 % | −2.4 % | +0.3 % | +3.6 % | +0.3 % |
| sleeve | – | – | – | – | – | – | +7e-6 | −1.5e-4 | +1e-4 |
| copper AC | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| total loss | +1.5e-5 | 0 | 0 | −9.8e-4 | −9.6e-4 | −9.7e-4 | +1.5e-4 | +1.4e-4 | +1.7e-4 |
| wall, march / TDM | 1.0× | 1.0× | 0.9× | 0.9× | 0.8× | 0.9× | **5.5×** | **6.9×** | **5.5×** |

The wall times are not paired (the march rows ran earlier under another load); the
paired numbers are §2.4–2.5. Reading the differences:

- **Ø40 and L155: TDM = march** in torque (≤ 1e-5), ripple (≤ 0.0005 pp),
  self-check, iron, magnet, sleeve, copper and total loss (≤ 4.4e-4). The shaft
  differs where the march has not settled its slowest conductor: Ø40 (8 mW) is the
  §2.1 case — there the march's 3-period shaft is +4.6 % above the 40-period
  asymptote and TDM is on it to 4e-6.
- **L155 at 2 per side settles with TDM.** The march hit its 24-period warm-up cap
  (residual 21.6 %, "not settled"); TDM converges the orbit to a 1.6e-6 state
  residual, the reported march stays on it (1.3e-6), the settle gauge reads 5.1e-6
  — and it gives the **same** +4.1 % iron (1518.6 W, series p-p 209 W against 38 W
  at 1 and 3 per side), +1.6 % total loss and 18.3 % self-check. So those are
  properties of the periodic steady state on the 288-node ring, **not an
  unfinished transient** as §6.1 of the Coulomb note reads them; only its shaft
  (+3.6 %) was unsettled in the march. This matters for the measured gap rule,
  which skips refining "unsettled" runs: under TDM this run is settled and fails
  the 5 % gate.
- **L13 (magnets at 210.7 °C, Br kept 20 % after the pre-pass, T 4.95 → 0.79 N·m)
  is not a TDM/march difference but the demag ratchet not being converged by the
  one-period pre-pass** in either method. Referee: both methods run for 3
  reported periods (the ratchet stays active):

  | run | period | Coulomb mean | ripple [%] | magnet W | shaft W |
  |---|---:|---:|---:|---:|---:|
  | march | 1 | 0.7882697 | 30.960 | 0.50807 | 12.1855 |
  | march | 2 | 0.7771526 | 30.618 | 0.49929 | 12.0679 |
  | march | 3 | 0.7742524 | 30.984 | 0.49868 | 12.0924 |
  | TDM | 1 | 0.7883675 | 31.160 | 0.52966 | 11.8952 |
  | TDM | 2 | 0.7771500 | 30.631 | 0.49928 | 12.0623 |
  | TDM | 3 | 0.7741644 | 30.980 | 0.49868 | 12.0926 |

  By the third period the two agree to 1.1e-4 in torque, 0.004 pp in ripple and
  2e-5 in the rotor losses. The first-period difference is what each method
  carries into the reported window after Br collapses by 80 % in the pre-pass —
  the march an eddy transient of the collapse, TDM the steady state of the
  pre-pass Br that the ratchet then keeps cutting — and **both first-period
  values are 1.8 % off in torque** from the third. A duty that demagnetises this far
  needs the ratchet iterated to its fixed point (more pre-pass periods), whichever
  eddy method runs; that is a demag-procedure item, not a TDM one. Here TDM is not
  faster: 166–186 s of its 232–261 s go to the pre-pass ratchet re-solves.

### 2.8 Checks: the march is unchanged; half against full period; stopping rules

- **The march path is unchanged.** Ø40 no-demag march with the base code (f852bd2)
  and with this branch (`eddy_method="march"`): every quantity equal to ≤ 7e-14
  (T_avg −2.8e-15, P_mag −5.4e-14, P_shaft −7.4e-14): MKL round-off.
- **Half against full period on L155** (no demag, both converged to the march's
  1e-7 state residual): the FULL-period orbit is the march's fixed point to
  2.1e-8 (σ-norm of the conductors at the last reported frame); the HALF-period
  one is 2.6e-4 off it, because the L155 mesh's magnet source is pole
  antisymmetric to 2.2e-5 only (Ø40 6.3e-6, L13 1.3e-6; the test accepts < 1e-4).
  Effect on the reported numbers: torque −4e-8 (full) / +1e-6 (half), P_mag
  +1.5e-5 / +2.0e-4, P_sleeve +6e-5 / +1.3e-4, total loss +3e-6 / +1e-5 — the half
  period is kept (twice cheaper); `SB_TDM_HALF=0` gives the exact one.
- **The L155 shaft** (4 W of 3.8 kW) reads +0.2 % (no demag) and +1.9 % (demag)
  above the march; with the full period and the residual stop it is +0.2 %. The
  owner's stop and the residual stop give the same shaft (4.0703 / 4.0701 W with
  demag), so it is the half-period symmetry, not the stopping rule.
- **Paired timing at 4 threads** (a 4-thread job of another agent sharing the box):
  L155 with demag, march 323 s, TDM 75 s (**4.3×**).

### 2.9 Output path, stopping rule and torque method (coordinator, 2026-09-30)

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
- **Torque method**: every T_avg / ripple in §2.1–2.4 and §2.8 is the shipped
  `energy_mean+maxwell_ripple` (flux-linkage space-vector mean, raw Maxwell AC);
  every T_avg / ripple in §2.5–2.7 is the Coulomb virtual-work torque
  (`torque_method="coulomb"`, #86), and the stopping monitor used it there. The
  result carries the method of every eddy run (`torque_method`).

## 3. Codex review of PR #87 (2026-09-30): verdicts, fixes, re-validation

Codex, reviewing the diff, called the TDM default BLOCKING. Every claim was checked
against the code and, where it was about numbers, measured. The fixes are in
`fem_transient_sliding_band` (now the transactional wrapper),
`_fem_transient_sliding_band_once` (the solver body) and `time_periodic.py`. The
re-validation ran on the server sandbox with this branch (snapshot T21), 4 threads,
Coulomb torque (the default since #88).

### 3.1 Verdicts

| # | Codex finding | Verdict | Fix |
|---|---|---|---|
| 1 | The owner-terms stop accepts rrel < 1e-5; no bounded state error (Blocker) | **Real** (by design, not certified) | A hard ACCEPTANCE GATE on the reported period (§3.4). A failure re-solves with the strict 1e-7 residual stop, then marches |
| 2 | The half period is knowingly approximate (Blocker) | **Real**, and larger than reported on the 30 mm fixture: its discrete orbit is not half-period symmetric (shaft state 8 % off the anti-periodic image, shaft loss +0.56 % half against full) | The FULL period is the default. The half period is opt-in (`SB_TDM_HALF=1`) with the source test tightened from 1e-4 to 1e-5. Half-vs-full regression on Ø40, L13, L155 and the fixture (§3.2) |
| 3 | Active demag: the reported period is not steady (Blocker) | **Real, and shared with the march** | A demag FIXED POINT for both methods; `demag_settled` / `steady_state` in the result (§3.3) |
| 4 | The rollback is not transactional (High) | **Partly real.** The in-place restore covered Br, the magnet source, the demag diagnostics and the eddy histories; the factors were closed in `finally` and the warm cache is written only after a run returns. None of this was demonstrated | TRANSACTIONAL: a rejected attempt raises `TdmAttemptFailed`, its whole solve is discarded, and the request is solved again from scratch. Every solve runs in its own PARDISO scope. Fault injection at set-up, static start, Newton, demag, re-solve, splice and the gate, each followed by a march equal to a clean march (§3.5) |
| 5 | Thread safety of parallel frames (High) | **Not real on the default path.** Frames are serial by default (`SB_TDM_WORKERS=1`). With workers, each frame owns its factor and its PARDISO handle; `Kpw`'s memo is an immutable tuple keyed on content, so a racing thread gets its own K; the tangent's `last_dhdb_min_rel` is only a statistic | Tests: synthetic parallel = serial bit for bit (3 repeats); FEM parallel = serial to the Newton tolerance, repeats identical to round-off; open PARDISO handles 0 after every solve (§3.6) |
| 6 | GMRES stops at maxiter without a status; record the true residual (Medium) | **Real** for the status. The recorded residual was already the true one (recomputed from x after each cycle) | A `converged` flag; one restart from where it stopped; a step whose true residual is more than 10× its forcing term is rejected (the Newton stops unconverged, and the run is marched). The true residual of each step and its maximum are recorded |
| 7 | Integration tests for every entry point (Medium) | **Real**, and they found a gap: the route's summary did not carry `eddy_method` / `eddy_method_note` | The summary carries `eddy_method`, `eddy_method_requested`, `eddy_method_note`, `demag_settled`, `steady_state`. New `tests/test_tdm_entry_points.py` (§3.7) |
| 8 | The shortcut is exposed (Medium) | **Real** | EXPERIMENTAL label: `eddy_method_note` says so, plus `tdm.demag.experimental` and a log warning. No workflow sets it (AST test) |
| 9 | Correctness of the anti-periodic map (High) | Checked. The gate verifies the closure of both BDF2 history levels on every run. The forward map (used only for the starts of later frames) is compared with the back map (`maps_inverse_dev`: 0 on the fixture orbit). Half against full is measured (§3.2) | — |
| 10 | Coverage: slow conductors, LU fallback, failed factorisation, parallel workers, fallback equivalence (Medium) | Real in part | Tests for the LU fallback, fault injection and parallel workers. Slow conductors against 40-period marches were already in §2.1 |

### 3.2 Half against full period

TDM, demag off: the default (full period) against `SB_TDM_HALF=1`. The L155 needs the
old 1e-4 source tolerance to take the half period. Relative differences:

| machine | T_avg full → half | ripple [%] full → half | P_mag | P_shaft | P_sleeve | P_cu AC | P_fe | total | wall full → half | magnet-source asymmetry |
|---|---|---|---|---|---|---|---|---|---|---|
| d40 | 0.6197595 → 0.6197595 (+3.3e-09) | 5.3186 → 5.3186 (+0.0000 pp) | -3.8e-08 | +5.5e-05 | – | -1.7e-07 | +8.2e-08 | +0.0e+00 | 45 → 34 s | 6.3e-06 (half_antiperiodic, gate True/True) |
| l13 | 5.453091 → 5.453091 (-1.2e-10) | 6.2296 → 6.2296 (-0.0000 pp) | +2.9e-08 | +1.1e-07 | – | -7.7e-09 | -4.1e-09 | +0.0e+00 | 53 → 41 s | 1.3e-06 (half_antiperiodic, gate True/True) |
| l155 | 185.4165 → 185.4165 (+7.0e-08) | 1.5582 → 1.5582 (+0.0000 pp) | +4.8e-06 | +2.7e-06 | +7.4e-06 | +1.7e-06 | +1.2e-07 | +5.2e-07 | 157 → 103 s | 2.2e-05 (half_antiperiodic, gate True/True) |

On these three machines the half period is exact to ≤ 5.5e-5 in every quantity. It is
also 25–35 % faster.

The 30 mm fixture is different (`tests/test_tdm_fem.py`). There the half period gives
torque 1.7e-7, ripple 2e-5 pp, copper AC +1e-4, magnet +9e-6, **shaft +0.56 %** and
total 9e-6. In BOTH orbits the reported torque of the second half-period differs from
the first by up to 0.3 %; the full-period orbit reproduces the same reported period to
1e-6. So the discrete model is not exactly half-period symmetric there. The gate
compares the reported period with the orbit frame for frame, so it could certify only
the first half of a half-period orbit.

Hence the full period is the default, and the half period is an opt-in for machines
where this table holds.

### 3.3 Demag fixed point (shared by both methods)

The pre-pass is repeated. Each further period is continuous in time: the splice's
pole-pair map carries the eddy state one period back, and the same map, applied to the
magnet-element centroids, relabels the Br map; TDM re-solves the orbit on it. The
repetition stops when one period moves Br by ≤ `SB_DEMAG_SETTLE_TOL` = 1e-3 (worst
magnet, area mean of |ΔBr|/Br0, so the magnet flux and hence torque moves by ≤ 0.1 %
in a period), after at most `SB_DEMAG_PREPASS_MAX` = 8 periods.

The REPORTED period is measured the same way and recorded as:

- `demag_settle`: the change per pre-pass period and in the report;
- `demag_settled`;
- `steady_state`: the eddy field settled AND Br not moving.

A run above the tolerance in its reported period is labelled `demag_settled: false` and
`steady_state: false`, with a warning. The gate then keeps it: a Br transient is not a
TDM defect.

| case | pre-pass periods (march / TDM) | Br moved per pre-pass period (TDM; the march's agree to two digits) | Br moved in the reported period (march / TDM) | Br kept [% vol] (march / TDM) |
|---|---|---|---|---|
| Ø40 rated | 2 / 2 | 0.0068, 0.00098 | 1.7e-4 / 1.7e-4 | 99.296 / 99.296 |
| L13 rated | 4 / 4 | 0.010, 0.0039, 0.0013, 0.00046 | 2.1e-4 / 2.1e-4 | 98.818 / 98.818 |
| L13 peak | 2 / 2 | 0.0023, 0.00093 | 6.0e-5 / 6.0e-5 | 99.761 / 99.761 |
| L155 rated | 2 / 2 | 0.0079, 3.6e-7 | 7e-10 / 3e-10 | 99.213 / 99.213 |
| Ø40 deep FW | 4 / 4 | 0.015, 0.0046, 0.0014, 0.00032 | 7.3e-5 / 7.3e-5 | 98.339 / 98.339 |
| L13 gap-layer 1/side | 4 / 4 | 0.822, 0.022, 0.0036 (march 0.0031), < 1e-3 | settled / settled | 19.05 / 19.04 |

- **Periods needed.** Every duty needs at least a second period: the first, from the
  pristine magnet, always moves Br by the whole loss. L13 rated and Ø40 deep FW need
  four. The L13 gap-layer case (magnets at 210.7 °C, Br kept 19 %) needs four too:
  0.822, 0.022, 0.0031–0.0036, then < 1e-3.
- **All settled.** In every case the reported period then moves Br by ≤ 2.1e-4.
- **Results move** where the one-period pre-pass was not a steady state. Ø40 deep FW:
  ripple 5.93 → 5.00 %, torque +0.11 %. L13 gap-layer case: torque 0.7883 → 0.7726 N·m
  (the 3-period referee of §2.7 had reached 0.7743 in its third period).
- **March and TDM agree on the fixed point:** the same number of periods, the same Br
  kept to 0.01 %, torque within 1.7e-4 on the L13 gap-layer case and within 1.8e-6
  elsewhere.
- **The demag shortcut** (§2.6) was compared with a one-period pre-pass. It is not
  re-qualified here and stays experimental.

### 3.4 The acceptance gate

The unchanged frame loop marches the reported period from the orbit. Its frames (A, U)
are compared with the orbit's, frame for frame over the orbit's period, in the owner's
observables, computed the same way for both:

- the REPORTED torque method's mean, within 0.1 %;
- its ripple, within 0.05 pp;
- every conductor group's σE² loss, within 0.5 % of the group — or within 1e-4 of the
  whole conductor loss for a group too small to move the total or the efficiency.

The check is `time_periodic.window_gate`; the result records it in `tdm.gate`. A
failure raises; the wrapper re-solves with the strict 1e-7 state residual, then
marches.

How often it fired: **never on a real run.** That is 0 of the 14 TDM solves of the
re-validation (§3.8: 6 duties, 2 gap-rule re-solves of the L155, 6 half/full runs) and
0 of the real solves in `tests/test_tdm_fem.py`. The margins are in §3.8; the worst
were torque 1.7e-4, ripple 0.013 pp and one group 7e-4. It does fire on the injected
fault: both attempts, then the march.

Before the full-period default, it fired on every half-period solve of the 30 mm
fixture, on the second half-period window (§3.2). That is why the default changed.

### 3.5 Transactional attempts, fault injection

`SB_TDM_FAULT=<stage>` (tests only) raises inside the attempt at `setup`,
`static_start`, `newton`, `demag`, `resolve` or `splice`, or fails the `gate`. Each test
asserts:

- `eddy_method: "march"`, `eddy_method_requested: "tdm"`, a note naming the stage, and
  `tdm.attempts`;
- a real warm-up;
- torque, total loss, copper AC, magnet and shaft equal to a clean march to 1e-9;
- a demag history equal to the clean march's;
- no live PARDISO handle.

The gate case runs both attempts (owner stop, then strict) before the march.

### 3.6 Parallel frames

Serial is the default.

- `tests/test_time_periodic.py`: 3 workers against 1, three repeats. The orbits and the
  Newton counts are bitwise equal.
- `tests/test_tdm_fem.py`: 3 workers × 1 MKL thread, two repeats. The runs equal the
  serial run to 1e-6 (the parallel default start differs: every frame starts from frame
  0's static field) and each other to 1e-12. The gate passes and no handle is left.

### 3.7 Entry points

`tests/test_tdm_entry_points.py` covers every door:

- **The EM-tab route** (`get_fem_transient`), run for real up to the solver with a stub
  FEM (as in `test_run_ledger`). It passes no `eddy_method` or `tdm_demag`, and its
  summary returns the solver's method, note, `demag_settled` and `steady_state` in three
  cases: TDM, a rejected TDM that was marched, and a demag transient.
- **The other doors go through that route:** the coupled loop, passports, agent drafts /
  MCP (via `agent_designs`) and the optimizer (`refine_proc` → kernel
  `solver.em_transient` → `modules.solvers`).
- **An AST scan of `src/`** finds no call passing a constant method or the shortcut, and
  nothing setting `SB_EDDY_METHOD` / `SB_TDM_*`.
- **The solve pool's child:** its environment carries `SB_EDDY_METHOD` /
  `SB_TDM_DEMAG`, and a real child process resolves the argument, then the environment,
  then the config, then the default.

The real solves (TDM, every fallback, the notes, a clean march after each) are the 17
tests of `tests/test_tdm_fem.py`, including `test_through_em_transient_eval`.

### 3.8 Re-validation (march against TDM, same code, Coulomb torque, demag as the duty says)

| case | T_avg: march → TDM | ripple [%] | P_mag | P_shaft | P_sleeve | P_cu AC | P_fe | total | wall march → TDM | demag periods (march / TDM) | Br moved in the report (march / TDM) | steady (march / TDM) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Ø40 rated | 0.6156234 → 0.6156231 (-4.2e-07) | 5.56 → 5.5603 (+0.0003 pp) | +3.7e-05 | -1.7e-02 | – | -5.5e-06 | -1.1e-05 | +0.0e+00 | 130 → 126 s (**1.0×**) | 2 / 2 | 0.00017 / 0.00017 | True / True |
| L13 rated | 5.343147 → 5.343145 (-3.2e-07) | 5.2027 → 5.2027 (+0.0001 pp) | +3.3e-05 | +1.7e-05 | – | -3.7e-05 | -1.4e-07 | +0.0e+00 | 296 → 302 s (**1.0×**) | 4 / 4 | 0.00021 / 0.00021 | True / True |
| L13 peak | 9.319912 → 9.319911 (-1.2e-07) | 6.5211 → 6.5211 (+0.0000 pp) | -5.3e-06 | -2.2e-05 | – | -1.0e-05 | -4.0e-07 | +0.0e+00 | 230 → 170 s (**1.4×**) | 2 / 2 | 6e-05 / 6e-05 | True / True |
| L155 rated | 184.4848 → 184.4851 (+1.8e-06) | 1.51 → 1.5101 (+0.0001 pp) | -9.8e-06 | +1.6e-02 | -3.8e-05 | -7.6e-06 | -5.3e-05 | -4.7e-06 | 803 → 228 s (**3.5×**) | 2 / 2 | 7e-10 / 2.7e-10 | True / True |
| Ø40 deep FW | 0.3789513 → 0.3789509 (-1.0e-06) | 4.9956 → 4.9951 (-0.0004 pp) | -5.8e-05 | -3.4e-02 | – | -2.0e-05 | +1.8e-05 | -1.9e-05 | 227 → 230 s (**1.0×**) | 4 / 4 | 7.3e-05 / 7.3e-05 | True / True |
| L13 gap-layer 1/side (magnets 210.7 °C, Br kept 19 %) | 0.7725991 → 0.7724687 (-1.7e-04) | 31.067 → 31.064 (-0.0034 pp) | +1.5e-02 | -1.6e-03 | – | – | – | -4.4e-05 | 421 → 529 s (**0.8×**) | 4 / 4 | settled / settled | True / True |

| TDM run | method | period | gate | gate T / ripple / worst group | attempt | stop | Newton | worst GMRES rel. resid | march-vs-orbit | pre-pass per period |
|---|---|---|---|---|---|---|---|---|---|---|
| Ø40 rated | tdm | full | True | 8.6e-05 / 0.009 pp / 0.00034 | 1 | owner_terms | 2 | 0.008 | 6.2e-05 | 0.0068, 0.00098 |
| L13 rated | tdm | full | True | 0.00017 / 0.013 pp / 0.00071 | 1 | owner_terms | 6 | 0.0099 | 0.00025 | 0.01, 0.0039, 0.0013, 0.00046 |
| L13 peak | tdm | full | True | 4.7e-05 / 0.0051 pp / 0.00014 | 1 | owner_terms | 8 | 0.0049 | 5.6e-05 | 0.0023, 0.00093 |
| L155 rated | tdm | full | True | 3.7e-08 / 8.8e-06 pp / 2.9e-06 | 1 | owner_terms | 5 | 0.0097 | 1.3e-07 | 0.0079, 3.6e-07 |
| Ø40 deep FW | tdm | full | True | 6.5e-05 / 0.0064 pp / 0.00019 | 1 | owner_terms | 3 | 0.0065 | 0.00014 | 0.015, 0.0046, 0.0014, 0.00032 |
| L13 gap-layer 1/side | tdm | full | True | 1.2e-04 / 0.0068 pp / 4.4e-05 | 1 | owner_terms | – | – | – | 0.822, 0.022, 0.0036, < 1e-3 |

Peak RSS (the one-process harness; the L155 includes the gap rule's second solve): TDM
1.6 GB (Ø40), 2.2–2.3 GB (L13), 4.5 GB (L155) against the march's 0.4–0.75 GB — the
full period keeps twice the half period's factors (L155 half 2.6 GB). The solve pool's
fallback estimate goes to 4.6 GB (six parallel L155 solves: 28 GB of the API
container's 40 GB).

Speed (4 threads, one job next to at most one other): L155 **3.5×**, L13 peak 1.4×;
Ø40, L13 rated and Ø40 deep FW about 1×. The extra demag periods (both methods) and the
full period (TDM) cost what the half period and the single pre-pass used to save. Where
the march settles in three periods, TDM is no faster.

The shaft differences (L155 +1.6 %, Ø40 −1.7 %, Ø40 FW −3.4 %) come from the march's
unsettled slowest conductor. TDM's reported period reproduces its own orbit there to
≤ 4e-5 (the gate). On the Ø40 without demag, TDM matched the 40-period march asymptote
to 4e-6 where the march was +4.6 % off (§2.1).

### 3.9 Tests, and the physics-regression pins

- **Fast set on the server image**: 116 passed — `test_time_periodic` (15),
  `test_tdm_default`, `test_tdm_entry_points`, `test_solve_pool`,
  `test_virtual_work_torque`.
- **`test_tdm_fem`**: 17 real solves. Results in the PR.
- **March tests** `test_demag_reproducible`, `test_warm_seed`: 16 passed. The two
  pre-pass-length assertions now read "a whole number of periods, as many as
  `demag_settle` says". Two identical runs still agree.
- **`test_physics_regression -k eddy` fails on the BASE branch too** (fb2c4e7, march
  default): T_avg −0.5 to −1.2 % on p2_eddy, p2_demag_eddy, p2_voltage_eddy and
  p2_voltage_eddy_rotor. That is the Coulomb default of #88, not this PR.
- **What this PR moves, against the base:**
  - p2_eddy (TDM against the march): T 2.5e-6.
  - p2_demag_eddy with one pre-pass period (`SB_DEMAG_PREPASS_MAX=1`): TDM equals
    the base march (T 0.399219 against 0.39922).
  - p2_demag_eddy with the fixed point (three periods): T −0.10 %, ripple p-p
    +7.6 %, P_mag_linear −0.5 %. That is the demag fixed point, intended.
- **Not re-pinned here:** the pins need #88's re-pin and this one, justified line by
  line, by the owner of the baseline.

## 4. Second Codex review (2026-10-03)

Codex re-reviewed 1e41f51 and kept "blocking before making TDM the default". The
fixes are on `feat/tdm-prototype` on top of 38fab6a (the bit-identical CPU speed-up
is kept). Claude Opus 5.5, one agent. Every run below went to the server sandbox
`/opt/motres/compute/tdm-fix-20261003` (throwaway image `motres-api:tdmfix` =
`motres-api:test` without intel-openmp / intel-cmplr-lib-ur,
`MKL_THREADING_LAYER=SEQUENTIAL`, `SB_GEO_MESH=1`, `--cpus 8`, `nice 19`, `ionice
-c3`, one container at a time under `/opt/motres/compute/.runq.lock`).

### 4.1 Findings, fixes, tests

| # (review) | Finding | Fix | Test |
|---|---|---|---|
| 1 (#1, blocker) | The owner-terms stop accepts rrel < 1e-5 without a state-closure bound | **Closure march**: after the final orbit, one whole period is marched from the orbit's start state (both BDF2 history levels) with the march's own bordered Newton (`P2Drive.eddy_solve`) and the Br map frozen. Accepted only if, per conductor group, both history levels at the end of the period are within 1e-4 (relative σ-norm) of the orbit's, and the marched window reproduces the orbit's mean torque (1e-4), ripple (0.01 pp) and every group's loss (1e-3, floor 1e-5 of the conductor loss): `time_periodic.CLOSURE_GATE`, `tdm.closure`. A failure (or an error) rejects the attempt; the wrapper retries ONCE with the strict 1e-7 state-residual stop on the full period, then marches with a note | `test_the_default_is_tdm_and_it_reproduces_the_march`, `test_a_failed_closure_retries_strictly_then_marches`, `test_a_fault_at_any_stage…[closure]`, `test_state_closure_per_group_and_level` |
| 2 (#6, blocker) | `march_vs_orbit_last_frame` and the per-group closure were diagnostic and their errors swallowed | The **report gate** now judges EVERY reported period (both halves of a half-period orbit) on the state — per group, both history levels, ≤ 1e-3 — and on the observables (torque 1e-3, ripple 0.05 pp, group loss 5e-3). `time_periodic.REPORT_GATE` constants; the `SB_TDM_GATE_*` knobs are gone. Any exception fails the gate; a failure is kept only when it is a threshold miss in a window whose Br is still moving (labelled `steady_state: false`), never an error | `test_the_default_is_tdm…` (gate keys), `test_a_failed_gate_retries_strictly_then_marches` |
| 3 (#2, high) | The opt-in half period accepted a 1e-5 source mismatch and was checked on its first half only | The half period is taken only when the source, the stiffness and σ-mass bilinear forms, the conductor bodies, the inverse and the constrained spaces match "one pole back, negated" to **1e-12** (`HALF_SYMMETRY_RTOL`: the image map is a signed permutation on rotated coordinates, so an exact mesh gives a few ulps). Otherwise the full period, with `eddy_method_note = "tdm: half period refused (…) — full period"`. Both halves are validated (the closure march covers the whole period; the report gate every orbit window). A rejected half-period attempt retries on the full period | `test_half_period_is_taken_only_at_round_off`, `test_a_forced_half_period_is_validated_on_both_halves` |
| 4 (#3, high) | The demag fixed point used only the area-mean Br change | The pre-pass (both methods) now repeats until the worst magnet's area mean ≤ 1e-3 **and** the largest single-element change ≤ 1e-2 of Br0 (`DEMAG_SETTLE_ELEMENT_TOL`, `SB_DEMAG_SETTLE_ELEMENT_TOL`); the reported period is judged by both, and a miss gives `demag_settled: false`, `steady_state: false` and `steady_state_note` with both numbers. The ratchet physics is unchanged | `test_demag_pre_pass_iterates_to_a_fixed_point_in_both_methods`, `test_demag_settled_needs_both_the_mean_and_the_element` |
| 5 (#7, high) | The inverse map deviation was a diagnostic | **Map checks at set-up** (`time_periodic.period_map_checks`, `tdm.map_check`): H⁻¹H = I and HH⁻¹ = I on arbitrary and constrained vectors; H maps range(P_{N−1}) into range(P_{−1}) and H⁻¹ range(P_0) into range(P_N) (BC signs, slip welds); stiffness and σ-mass invariant on the constrained space; every conductor body onto a whole body of equal conductance; magnet source. A deviation above `MAP_CHECK_RTOL` refuses TDM at set-up (marched, with the failing checks named) | `test_the_default_is_tdm…` (fixture numbers), `test_a_map_with_a_wrong_bc_sign_refuses_tdm_at_setup`, `test_period_map_checks_pass_an_exact_map_and_catch_each_defect` |
| 6 (#8, medium) | GMRES non-convergence within 10× forcing still proceeded | Kept as a **bounded inexact Newton**, justified: a step whose true linear residual is ≤ 10·η = 0.1 is an inexact-Newton step with forcing < 1 (Dembo–Eisenstat–Steihaug), the line search checks the merit, and nothing is accepted on the GMRES (the residual stop and the two gates decide). The true unpreconditioned residual is now **recomputed by the solver** from the returned w (one extra sweep), recorded (`gmres_rel_resid`, GMRES's own as `gmres_reported_rel_resid`) and used for the decision (`gmres_accepted_inexact`) | `test_bounded_inexact_newton_accepts_a_step_within_the_factor`, `…_rejects_a_step_beyond_the_factor` |
| 7 (#11, medium) | The experimental demag shortcut was reachable through `SB_TDM_DEMAG` | Only the explicit argument `tdm_demag="shortcut"` selects it (`resolve_tdm_demag`); `SB_TDM_DEMAG=shortcut` is ignored with a note. No route, payload or workflow passes the argument (AST test). A shortcut result carries `tdm_experimental: true`, `qualified: false` (solver, route summary, optimizer), and the optimizer's final certification refuses it | `test_the_shortcut_is_never_taken_from_the_environment`, `test_the_environment_cannot_select_the_shortcut`, `test_the_demag_shortcut_is_labelled_experimental`, `test_no_caller_pins_a_method_or_the_shortcut` |
| 8 (new, high) | The optimizer output dropped `steady_state`, `demag_settled` and the notes | `refine_proc.run_one` carries `steady_state`, `steady_state_note`, `demag_settled`, `eddy_method`, `eddy_method_requested`, `eddy_method_note`, `tdm_experimental`, `qualified`; the eval cache keeps them (`_RES_KEYS`); `_standard_quality` refuses a non-steady or experimental result | `test_the_optimizer_result_keeps_the_steady_state_verdict`, `test_the_optimizer_result_path_solves_the_fixture_with_tdm_for_real` |
| 9 (new, high) | `_last` kept failed attempts' tracebacks and arrays alive during retry / fallback | `_drop_attempt`: the wrapper keeps the record and the message only; it clears the traceback frames of the exception and of its `__cause__` / `__context__` chain and cuts the links before the next attempt | `test_a_rejected_attempt_leaves_nothing_referenced` (weakref), `test_a_rejected_attempt_is_not_kept_alive_during_the_retry` (gc: 0 TDM frames / solvers / factors alive at each attempt, real solve) |
| 10 (#9/#10, medium) | Source-string entry-point checks; no failed-factorization regression | Real calls on the 30 mm fixture through the EM route (`get_fem_transient`, unstubbed) and through the optimizer path (`refine_proc.run_one` → kernel → module → route → solver), asserting the requested / effective method, notes and verdicts. A PARDISO failure injected into every frame factorisation is marched with the reason | `test_the_em_route_solves_the_fixture_with_tdm_for_real`, `test_the_optimizer_result_path_solves_the_fixture_with_tdm_for_real`, `test_a_failed_factorization_is_marched_loudly` |

### 4.2 Measurements (30 mm fixture, server, 12 steps)

Before = 38fab6a, after = this change; same server image, one run each, Coulomb
torque (the default).

| case | quantity | before | after |
|---|---|---:|---:|
| p2_eddy (rated, demag off) | TDM wall [s] | 18.9 | 21.1 (closure march +1.8) |
| | T_avg [N·m] | 0.407975381 | 0.407975381 (identical) |
| | ripple [%] | 0.900787 | 0.900787 (identical) |
| | total loss [W] | 116.545 | 116.545 |
| p2_demag_eddy | TDM wall [s] | 44.8 | 58.5 (two more pre-pass periods +12.5, closure +1.4) |
| | pre-pass periods | 3 | 5 (the per-element rule) |
| | Br moved in the reported period: area mean / element | 1.2e-4 / 0.0101 | 1.8e-5 / 0.0029 |
| | T_avg [N·m] | 0.3988322 | 0.3988291 (−7.7e-6) |
| | ripple [%] | 0.71243 | 0.70005 (−0.012 pp) |
| | total loss [W] | 116.318 | 116.318 |
| | the march, same code (after) | – | T 0.3988301, ripple 0.69988 %, total 116.318 |

TDM against the march after the change (demag case): torque −2.5e-6, ripple
+0.0002 pp, total loss 0. The demag numbers moved because the reported period now
starts from a Br map that is settled element by element, in both methods.

**The gates on the fixture (after):**

- Closure march (frozen Br, 12 frames): state worst 7.3e-6 (shaft, frame 10;
  copper 4e-7, magnets 6e-7); torque 2.8e-7, ripple 2.9e-5 pp, group losses ≤ 9.6e-6.
  Demag case: state worst 1.5e-6.
- Report gate: state worst 6.7e-6 (shaft) without demag; 3.1e-5 (copper) with the
  ratchet active in the window; torque 2.8e-7 / 1.7e-5.
- Map checks (full period): inverse 0, constrained 1.5e-19, bodies 9e-15 (43
  bodies, 7 moved), source 9.1e-6, operators 9.4e-4. Recorded only: the inverse
  over all dofs of constrained vectors 0.017, of arbitrary vectors 0.055 — the
  non-conducting sector-edge rotor dofs, which no equation reads through the map.

**Half period on the fixture.** Asked for (`SB_TDM_HALF=1`): refused (operators
9.4e-4, source 7.8e-6 > 1e-12), full period, note. Forced (the round-off rule
bypassed in a test): the closure march rejects it on the SECOND half — shaft state
8.4 %, ripple 0.13 pp, magnet loss 0.16 % off, while the first half closes to
1e-5 — and the one retry (full period, strict stop) is accepted, 4e-9 from the
default run. This is the asymmetry §3.2 found, now caught by a hard gate.

### 4.3 Tests (server image, 2026-10-03)

| set | result |
|---|---|
| fast: `test_time_periodic`, `test_tdm_default`, `test_csr_scatter`, `test_p2_nonlinear`, `test_p2_projection`, `test_tdm_entry_points -m "not slow"` | 85 passed |
| `test_tdm_fem` (real solves, 30 mm fixture) | 24 passed (12 min 58 s) |
| `test_tdm_entry_points -m slow` (real EM route, real optimizer path) | 2 passed |
| march-pinned `test_demag`, `test_demag_reproducible`, `test_warm_seed` | 31 passed |

Not run (as instructed): the full suite and the physics-regression pins (§3.9).

### 4.4 What a reviewer should still know

(Superseded in part by §5: the big machines WERE re-run against 40-period marches
— the validation table for Ø40, L155 and L13 is §5.2 — and the per-element demag
change is now a warning, not a verdict.)

- The closure bounds the one-period defect. For a mode decaying as e^{−T/τ} the
  distance to the true orbit can be up to τ/T times larger; that concerns the
  ring bodies' DC mode (the coarse space's target), e.g. the L155 shaft (τ ≈ 21
  periods: ≤ 2e-3 of a 4 W group under the 1e-4 bound).
- The big machines (Ø40, L13, L155) were not re-run here (owner rule: one server
  job at a time, this change's verification set only). The closure and report
  thresholds are 10–100× above the fixture's values; on a miss the cost is one
  strict retry, then a loud march — never an unproven result.
- The per-element demag rule adds pre-pass periods where a single element still
  moves > 1 % of Br0 per period (the fixture: +2 periods, +12.5 s), in both
  methods.
- The operator check on rough vectors (9.4e-4 on the fixture) is mesh-limited;
  its threshold (1e-2) catches map defects, not mesher round-off. The half period
  needs 1e-12 and so is refused on every mesh measured so far.

## 5. Third Codex review (2026-10-04)

Codex re-reviewed 768d600: findings #2, #4–#8, #11 and both round-2 issues resolved;
still blocking on three. Fixed here, on the server sandboxes
`/opt/motres/compute/tdm-fix-20261004` and `…-20261004b` (images `motres-api:tdmfix3`
/ `tdmfix4`, one container at a time under the shared run lock, `--cpus 8`,
`nice 19`, `ionice -c3`).

**Owner decision (2026-10-04): demag steadiness is judged by its effect on the
observables.** The steady decision (both methods) is the per-magnet area-mean Br
change, the observable drift between the last two pre-pass periods and the
rotor-image history. The per-element change (1 % of Br0, §4) is a WARNING
(`demag_warning`, `demag_settle.warning`), never a reason for `steady_state: false`.
It had held both methods "not steady" on the L13 rated duty for one element at
1.08 % while torque drifted 1.3e-4 and ripple 0.019 pp (§5.2).

### 5.1 Findings, fixes, tests

| # | Finding | Fix | Tests |
|---|---|---|---|
| 1 (blocker, #1) | The 1e-4 closure bounds the one-period DEFECT, not the orbit error (×τ/T on a slow mode); the loss gates exclude iron | **Certified orbit error.** After the closure march: the linearised period map T is factorised AT the final orbit (`TimePeriodicEddy.refresh_jacobian`); its dominant eigenvalue ρ by Arnoldi (15 steps) from the defect d; the orbit error e = (I − T)⁻¹d by the preconditioned GMRES; the error taken as the larger of ‖e‖ and ‖d‖/(1 − ρ). That perturbation is propagated through the period (linear frame response) and the owner's observables are evaluated on the perturbed orbit: mean torque, ripple, every conductor group's loss, and the relative change of the iron flux density r_B (iron loss ≤ 2·r_B·P_fe to first order). After the frame loop, with P_fe and the TOTAL loss known, the bound plus the reported period's own deviation from the orbit must be below **1/10 of the owner's terms** (torque 1 %, ripple max(0.5 pp, 10 %), total loss 5 %: `CERT_SAFETY`, `certify_observables`). Otherwise one strict retry, then a march with a note. ρ, the bound, the scale and the sensitivities are in `tdm.certify` | TP `test_the_certified_orbit_error_sees_the_slow_mode` (a slow ring: the defect hides the error by ×>3, ρ equals the dense T's spectral radius, e recovers the true error, ‖d‖/(1−ρ) bounds it), `test_certify_observables_against_the_owner_terms`; TF `test_the_default_is_tdm…` (certify keys), `test_a_failed_certification_retries_strictly_then_marches` |
| 2 (high, #3) | `image_min_gap` was diagnostic; a per-period Br change does not bound the drift across the rotor-image history | The demag fixed point (both methods) now also requires (a) the **image history complete**: L pre-pass periods (L = order of the period relabelling on the magnet elements, 7 on a 12s14p sector, 5 on the L155) or Br equal to its element-wise image minimum on the area mean, and (b) the **observable drift** between the last two pre-pass periods (mean and ripple of the frame torque — Coulomb when the run reports it) below 1/10 of the owner's terms. The cap is max(8, L). A reported period missing any rule is `steady_state: false` with the numbers in `steady_state_note`; the per-element change is a warning (owner decision above). Ratchet physics unchanged | TF `test_demag_pre_pass_iterates_to_a_fixed_point_in_both_methods` (cycle 7, image record, drift record, warning), TP `test_demag_settled_is_the_mean_and_the_element_is_a_warning` |
| 3 (high, new) | A refused PWM / voltage run could get `_warm_quiet = None` and then `steady_state: true` through `is not False` | **Affirmative only**: `eddy_settled` is True only when the settle was measured and passed (or the run has no eddy march), False when it failed, **None when unknown** (unmeasured voltage settle prefix, PWM gauge that cannot judge) with a note; `steady_state` requires `eddy_settled is True` and the demag verdict True. `refine_proc` keeps None (no `bool(... default True)`). Grep: no other `is not False` on EM settle flags (`report.py` uses one on the THERMAL loop's temperatures — a different verdict) | TF `test_an_unknown_settle_is_never_reported_steady` (p2_voltage_eddy: march, `eddy_settled` None, `steady_state` False, note) |
| 4 (#1/#3/#10) | Validation against long asymptotes, big machines, RSS | §5.2 | – |
| 5 (medium, #9) | Coupled / passport / MCP paths without behavioural coverage | The coupled loop carries each EM pass's verdict on its history rows, on its block (`em`) and as `em_steady_state`; a passport carries `solve_status` (every solve's method, notes, steady verdict); the MCP headline (`agent_designs.headline`) carries `steady_state`, its note, the method and its note, `qualified` | TE `test_the_coupled_loop_carries_each_passs_verdict` (the real loop, halves faked), `test_a_passport_reports_its_solves_verdicts` (real `generate_passport`, stub solver), `test_mcp_headlines_carry_the_verdict` |

Also found and fixed on the way: the warm cache was published before the
certification verdict, so a march replacing a rejected attempt could start from
the rejected attempt's field (P_mag differed by 1.5e-9 from a clean march); it is
now published only by an accepted solve.

### 5.2 Validation against long marched asymptotes (server, Coulomb torque, duty settings)

TDM with its default settings against a LONG march from cold: the eddy warm-up
pinned to 40 whole periods of the plain march (`SB_EDDY_WARM` = 40 × steps), then
the demag pre-pass to its fixed point and the reported period, as the production
march does. Duties are read-only copies of `/srv/motres/shared/dies`; the L155 (Δ)
is driven with its WINDING current 562.07/√3 = 324.51 A. Harness
`val_run.py` (the §2 harness composition, scratch); one container at a time.

| machine | method | T_avg [N·m] | ripple [%] | total loss [W] | wall [s] | peak RSS [MB] | ρ | certified bound: torque / ripple / total | steady |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| Ø40 (CIANO14 40 new / L12 rated, demag) | TDM | 0.6156275 | 5.5475 | 65.783 | 166 | 1714 | 0.844 | 1.9e-10 / 1.7e-8 pp / 6.7e-9 | yes |
| | 40-period march | 0.6156541 | 5.5361 | 65.783 | 630 | 378 | – | – | yes |
| | TDM − march | −0.004 % | +0.011 pp | 0 | 3.8× | | | | |
| L155 (CIANO10 200 opt / L155 motor rated, Δ, demag) | TDM | 184.48510 | 1.5101 | 3820.10 | 284 | 4590 | 0.960 | 8.5e-8 / 8.9e-6 pp / 7.2e-6 | yes |
| | 40-period march | 184.42968 | 1.9441 | 3820.59 | 852 | 532 | – | – | **no**: eddy residual 0.026 > 0.02 |
| | TDM − march | +0.030 % | −0.434 pp | −0.013 % | 3.0× | | | | |
| L13 (CIANO28 85 20SW1200 / L13 rated, demag) — after the owner's decision | TDM | 5.285939 | 4.2548 | 194.414 | 281 | 2338 | 0.151 | 1.5e-4 / 0.025 pp / 5.5e-6 | yes, warning: one element 1.8 % |
| | 40-period march | 5.285963 | 4.2531 | 194.414 | 923 | 419 | – | – | yes, warning: one element 1.8 % |
| | TDM − march | −0.0004 % | +0.002 pp | 0 | 3.3× | | | | |
| L13, before the decision (element rule a verdict, 8 pre-pass periods) | TDM / march | 5.284085 / 5.284137 | 4.2359 / 4.2422 | 194.411 / 194.411 | 342 / 1010 | 2332 / 417 | 0.151 | 2.2e-9 / 2.7e-7 pp / 5.1e-8 | no / no (one element 1.08 %) |

- **Ø40 and L13: TDM = the long march** to ≤ 0.004 % in torque, ≤ 0.011 pp in
  ripple and 0 in total loss, 3.3–3.8× faster. Both report the same demag fixed
  point (7 resp. 5 pre-pass periods; L13 drift between the last two: torque 3.3e-4,
  ripple 0.021 / 0.005 pp).
- **L155: the 40-period march is NOT the asymptote.** Its own settle gauge reads
  0.026 > 0.02 after 40 periods (the shaft ring's τ ≈ 24 periods: ρ = 0.960 =
  e^(−1/24.5)), and its ripple is 0.43 pp off. TDM's certified orbit error, with
  that slow mode amplified ×15 (`scale`), is 8.5e-8 in torque and 8.9e-6 pp in
  ripple — the slow mode is exactly what the certification is for.
- **Peak RSS**: TDM 1.7 / 2.3 / 4.6 GB against the march's 0.4 / 0.4 / 0.5 GB (the
  factors of a full period), unchanged from §3.8.
- The L13 rows before/after the owner's decision differ by +0.035 % in torque and
  +0.019 pp in ripple: five pre-pass periods instead of eight (the element rule
  had forced the cap). Both are inside the owner's terms by 30× and 25×.

### 5.3 Tests (server image `motres-api:tdmfix4`, 2026-10-04)

| set | result |
|---|---|
| fast: `test_time_periodic`, `test_tdm_default`, `test_csr_scatter`, `test_p2_nonlinear`, `test_p2_projection`, `test_tdm_entry_points -m "not slow"` | 90 passed |
| `test_tdm_fem` (real solves, 30 mm fixture) | 26 passed |
| `test_tdm_entry_points -m slow` (real EM route, real optimizer path) | 2 passed |
| `test_demag`, `test_demag_reproducible`, `test_warm_seed` (march) | 31 passed |

The three `test_tdm_fem` failures of the first round-3 run are fixed: the default
test compared the computed limit 0.1 × 0.05 to 5e-3 exactly (now `approx`); the
certification fault key collided with the `certify` stage marker (renamed
`certify_gate`); the experimental shortcut's report window (a demag transient,
labelled not steady) was charged to the orbit's certification (now judged on the
orbit alone there); and the warm cache was published before the certification
verdict, so the replacing march started from the rejected attempt's field (now
published only by an accepted solve). No tolerance was loosened.

## Progress log

- 12:05 Stage 0: module, integration, synthetic tests (5 passed locally and on the
  server image: the TDM orbit equals a 150-period march to 1e-7, the half-period
  anti-periodic orbit likewise, the coarse space cuts the Krylov count). Sandbox
  `/opt/motres/compute/tdm-20260930` (queue `runq.sh`, jobs in `jobs.txt`).
- Stage 1–3 (Ø40, L13 rated/peak, L155; demag full / shortcut / off): §2.1–2.4.
- Default switch (b0474ba … b759cad): TDM default, refusals and failure fallback,
  Coulomb monitor, solve-pool estimate, progress strip; targeted tests on the
  server image: `test_time_periodic` 8, `test_tdm_default` 19, `test_tdm_fem` 5
  (FEM, 30 mm fixture), `test_solve_pool` and the march-pinned tests pass; after
  the rebase onto #88/#89 (1d6b3af): fast set incl. `test_virtual_work_torque` 96
  passed, `test_tdm_fem` 5 passed.
- TDM + Coulomb end to end (§2.5), the demag shortcut at peak and in deep field
  weakening (§2.6), the gap-layer cases with TDM plus a 3-period L13 referee
  (§2.7). Draft PR #87; not merged, not deployed. Sandbox removed at the end.
- Codex review (§3): the fixes (a transactional wrapper, the acceptance gate, the full
  period by default, the demag fixed point in both methods, the GMRES status, the
  summary keys, the experimental label) and their tests. Re-validation on the server
  (T21): 5 duties × 2 methods, the severe-demag gap-layer case × 2, half against full
  on 3 machines. Before the switch to the full period, the gate caught the fixture's
  half-period asymmetry.
- **PAUSED 2026-09-30 (weekly usage limit); resume after 2026-10-03.** Done: every
  review fix is committed (cc4aa47… amended to 5f6c169, rebased on fb2c4e7). The
  re-validation (§3.8) and the fast tests (116 passed) are complete. `test_tdm_fem`
  passed 16/17 on T21; its one failure (the parallel test's mW rounding) is fixed in
  5f6c169. The final server run of `test_tdm_fem` and of the march tests on 5f6c169
  (T24) was killed at the pause.
  **Next:**
  1. Re-run `tests/test_tdm_fem.py` and `test_demag_reproducible` /
     `test_warm_seed` / `test_eddy_settled_flag` on 5f6c169 (sandbox
     `/opt/motres/compute/tdm-20260930c`, queue `jobs.txt` from line 35, `qpos` = 34;
     `rm stopq` and start `tdmq_runner.sh` with setsid).
  2. Fill FAST/FEM/MARCH/REG in the PR body (scratchpad `tdm2/pr_body.md`) and update
     PR #87.
  3. Log to the dataset, delete the sandbox, and report to the coordinator, who
     re-runs Codex.
- **2026-09-30 resume (Windows workstation, Python 3.11, pypardiso 0.4.7 + mkl 2026.1,
  `MKL_THREADING_LAYER=SEQUENTIAL`, no intel-openmp).** On b01277c:
  `test_time_periodic`, `test_tdm_default`, `test_demag`, `test_demag_reproducible`,
  `test_warm_seed` passed. `test_tdm_fem` passed 17/17 and `test_tdm_entry_points`,
  `test_eddy_settled_flag` passed (52 passed in total with the march-pinned tests) once
  the geometry-driven mesher was on (`requirements-triangle.txt`, `SB_GEO_MESH=1`).
  Without it, 14 of the 17 `test_tdm_fem` tests fail: on the gmsh path the 30 mm
  fixture's rotor mesh is not pole-pair periodic, TDM refuses it (setup stage) and
  marches with a note, as designed. No solver change and no tolerance change were
  needed; the test module header now says which environment it needs.
- **2026-10-03 second Codex review (§4).** Fixes on 38fab6a (kept). Every run on the
  server sandbox `/opt/motres/compute/tdm-fix-20261003` (image `motres-api:tdmfix`,
  one container at a time under the shared run lock); one baseline bench had run on
  the workstation before the owner's server-only rule and was repeated on the server.
  Verification 142 passed (§4.3). Sandbox, containers and image removed at the end.
- **2026-10-04 third Codex review (§5).** Certified orbit error, demag image
  history and observable drift, affirmative-only settle verdicts, verdicts through
  the coupled loop / passports / MCP; the owner's decision makes the per-element
  demag change a warning. The owner's PC shut down mid-run: the orchestrator
  committed the worktree as `7378750` (wip); this round finished on top of it.
  Validation against 40-period marches on Ø40, L155 and L13 (§5.2); tests 149
  passed on the server (§5.3). Sandboxes and images removed.
