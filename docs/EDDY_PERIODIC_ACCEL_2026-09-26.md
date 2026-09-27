# Periodic-state accelerator for slow eddy bodies; L155 ripple sampling study (2026-09-26)

Claude Opus 5.5 (`claude-opus-5-5`), one agent, no sub-agents, no escalation. Branch
`feat/eddy-periodic-accel` from `pre-migration-freeze-2026-09-15` (5af2c19). Harness in the
scratchpad `pa/`: `run_case.py` (saved duty, solver-direct, cold, `motor_ai_sim.__file__` in every
result), `drive.py` (parallel jobs, normal priority), `fit.py`, `dmd.py`, `sec*.py`, `post*.py`
(offline analysis of dumped period states), `rip.py` (ripple). Results `pa/res/*.json`.

Owner's rules applied: only real physics, nothing filtered, every reported value comes from the
continuous march after the last jump; loud validation.

## Summary

| item | outcome |
|---|---|
| A. accelerator | **Implemented, ON by default** (`SB_EDDY_ACCEL=0` turns it off). Symmetry-sector RRE for the rotating slow modes + single-mode step with a secant calibration for the pole-pair-periodic DC mode. L155/L180 shaft at the default 16-period cap: 5.26 → **4.09 W** and 9.86 → **7.10 W** (70-period march: 4.08 / 7.00 W). Same 650 frames. It still reports **capped** at 16 periods (the gauge sees the last jump's small transient); at a 24-period cap both runs settle. No-op on Ø40 and L13: bit-identical. |
| B. ripple study | The −15 % at 36 steps is **angular sampling of the peak-to-peak**, not time integration. At the same rotor angles the 36- and 108-step BDF2 torques agree to 0.04 %. Proposal: steps/period ≥ 9 × cogging cycles/period for a reported ripple (108 on L155). Nothing implemented. |

## A. Accelerator

### A.1 What the slow state is (measured, not assumed)

The L155 period map (the state at θ = −dθ, carried back one period by the exact pole-pair map)
was dumped for 26 periods with no acceleration. Dynamic-mode decomposition of the shaft state:

| mode | \|λ\| per period | angle | share of the period change (σ-norm) |
|---|---:|---|---|
| rotating pair | 0.963 | ±2π/5 | dominant (~95 %) |
| rotating pair | 0.949 | ±4π/5 | small |
| real | 0.90-0.97 (several) | 0 | ~3 % of the change, but it carries the loss decay |

- The angles are **exactly** multiples of 2π/q, q = 5 (12s/10p, NS = 2).
- The pairs are rotor-fixed DC patterns without pole-pair periodicity. The per-period relabelling
  turns each of them by one q-th of a turn.
- The real mode is the pole-pair-periodic rotor-frame DC magnetisation of the wall. It is the
  mechanism EDDY_LOADED_STATIC_START found, acting through μ(B).
- In the σ-norm the pairs are about 30× larger than the real mode. So one least-squares fit over the
  whole state removes the pairs and leaves the real mode untouched. Measured: the extrapolated
  state's error along it equalled the marched state's. That is why plain RRE (the first
  implementation, both whole state and slow bodies only) cut the 5-period modulation but not the
  loss decay: 5.94 → 5.49 W, same slope as without it.

### A.2 Method (chosen after measuring the alternatives)

1. **Symmetry sectors.** The image map S is an exact symmetry, S^q = ±I on the rotor dofs. The state
   splits into S's eigenspaces (a DFT over the q images; conjugate pairs merged, real arithmetic)
   with no approximation. Tail dofs are 30 of 4215 on the L155 shaft: slaved copies on the cut,
   which the map sends into a cycle. They go whole into the first sector.
   `periodic_accel.shift_cycles`, `sector_components`.
2. **Rotating sectors (m ≠ 0): RRE per sector.** RRE is used, not MPE, because its least squares is
   always posed. It is applied only when its own linear model explains the sector
   (‖Σγu‖ ≤ 0.3‖u_last‖), which gives no kick. First jump after period 5, from 4 states: m1 error
   0.016 → 0.0008, m2 0.00064 → 0.00007. Measured against a long-window reference built from 26
   periods; that reference agreed with itself to 0.3 %.
3. **Pole-pair-periodic sector (m0): one real mode.**
   - Once the pairs are gone, the m0 changes are aligned: cos 0.986-1.000 between consecutive
     periods.
   - A **single-mode step** is taken: x* = x + λ/(1−λ)·u, with λ the pooled ratio of change norms
     over 3 states.
   - The pooled λ is biased low: 0.936-0.939 against a true 0.95-0.96. RRE and MPE did worse on the
     same data: RRE removed 20-50 % of the error, MPE 60-75 %. They trade the long step against the
     few-per-cent non-modal part of u.
4. **Secant calibration of that step.**
   - The jump itself is a lever arm. One period change measured two periods after it gives
     r = λ^{n+1}(1 − S_p/S), and from it the true step factor S = λ/(1−λ).
   - The correction is then applied **along the clean pre-jump direction**:
     x* = x + λ^m(S − S_p)·u_pre. So the small post-jump change sets only one scalar, and its noise
     is not multiplied by S. `secant_step_scale`.
   - Calibrated S: L155 16.2 (λ = 0.942), L180 18.3 (λ = 0.948). The 70-period fits give
     τ = 17-18 and 22-26 periods.
5. **Schedule within the 16-period cap.**

   | step | period |
   |---|---|
   | skip (first-period transient) | 1 |
   | states 2-5, RRE jump of the rotating sectors | 5 |
   | skip (the jump's kick) | 6 |
   | states 7-9, single-mode m0 jump | 9 |
   | skip | 10 |
   | states 11-12, secant m0 jump | 12 |
   | verification | 13-16 |

   A machine that settles before period 5 never reaches a jump.
6. **Honesty guards.**
   - Only the discarded prefix moves.
   - The gauge's record restarts at every jump.
   - The verdict needs **≥ 4 whole continuous periods solved after the last jump**, judged by the
     unchanged per-body gauge (`sb_postproc.eddy_period_resid`).
   - An **additional** slow-mode check: the same whole-period tail, with q equal to the λ the
     accelerator measured on the state (≥ 0.9). The gauge caps q at 0.9, which under-reads a
     λ = 0.95 tail by 2×. `slow_tail_resid`.
   - Every jump is recorded in `eddy_settle_gauge.accelerator`: method per sector, λ, S, applied or
     refused, and why.

Measured and rejected:

- **Whole-state RRE.** It removes the pairs only (A.1).
- **DC Newton** (time-periodic explicit error correction). One static solve K_s⁻¹Mu/T with the
  frame's tangent stiffness, plus a secant rescale. Its operator was 1.5× too slow on both machines
  (S 27.6 vs 18; 39 vs 24): the DC state's μ(B) coupling to the harmonic currents is missing from a
  snapshot tangent. Its mode shape was also wrong enough that the secant rescale left 70-80 % of
  the error.
- **5-period moving averages and a pair deflation filter before RRE.** 50-70 % error left.

### A.3 Validation against the 70-period references

Cold, saved duty, 36 steps, BDF2, solver-direct.

- Reference: base 5af2c19 marched 70 continuous periods (2594 frames).
- P∞: fit P∞ + B·e^{−t/τ} on 5-period block means of the warm-up period means, starting from period
  20 and from period 35.
- "Asymptote": the 70-period reported value minus the drift of its warm-up means, (warm mean at
  period 70) − P∞.

**L155 rated**

| run | frames | eddy_settled | P_shaft reported [W] | P_mag [W] | P_cu AC [W] | T [N·m] | wall [s] |
|---|---:|---|---:|---:|---:|---:|---:|
| base, default cap 16 | 650 | False (41 %) | 5.255 | 92.262 | 609.728 | 187.93905 | 2025 ¹ |
| **accel, cap 16** | 650 | False (3.7 %) | **4.088** | 92.314 | 609.803 | 187.93410 | 908 ² |
| accel, cap 24 | 866 | **True** (0.35 %, slow check 0.9 %) | 4.103 | 92.313 | 609.801 | 187.93417 | 1146 ² |
| base, 70 periods | 2594 | False (4.8 %) | 4.077 | 92.315 | 609.801 | 187.93424 | 6952 ¹ |
| P∞ of the warm-up means / asymptote | | | 4.02-4.04 / ≈ 4.06 | | | | |

**L180 gen rated**

| run | frames | eddy_settled | P_shaft reported [W] | P_mag [W] | P_cu AC [W] | T [N·m] | wall [s] |
|---|---:|---|---:|---:|---:|---:|---:|
| base, default cap 16 | 650 | False (49 %) | 9.859 | 292.100 | 2103.244 | 239.22439 | 2258 ¹ |
| **accel, cap 16** | 650 | False (2.3 %) | **7.098** | 292.231 | 2103.463 | 239.22240 | 994 ² |
| accel, cap 24 | 902 | **True** (0.20 %, slow check 1.1 %) | 7.143 | 292.229 | 2103.459 | 239.22245 | 1234 ² |
| base, 70 periods | 2594 | False (8.6 %) | 6.999 | 292.242 | 2103.464 | 239.22272 | 7138 ¹ |
| P∞ of the warm-up means / asymptote | | | 5.88-5.97 / ≈ 6.86 | | | | |

¹ Four jobs × 3 BLAS threads at once. ² Four jobs × 2 threads at once. So wall times are
comparable only within a group; the paired timing is below.

- **Magnets, sleeve, copper and torque are unchanged** against the 70-period reference: within
  0.004 % for P_mag, 0.0003 % for copper and 0.0001 % for T. The default-cap base run differs from
  the reference by −0.06 % on P_mag (L155) and −0.05 % (L180). The accelerated run does not.
- **Shaft at the default cap:**
  - L155: +0.3 % against the 70-period march, about +0.7 % against its asymptote. Today's default
    run is +29 %.
  - L180: +1.4 % against the 70-period march, about +3.5 % against its asymptote. Today's default
    run is +41 %.
- **Settling:**
  - At the 16-period cap both runs remain **capped**. The four verification periods still show the
    last jump's transient returning: L155 warm-up means 4.042 → 4.091 W.
  - With a 24-period cap both are **settled** by the gauge and the slow-mode check, at 22 and
    23 periods.
  - L180 settles about 4 % above its estimated asymptote. See the risk below.
- **Risk, found and stated.** A jump can excite a slower wall mode (τ of 50-80 periods is possible
  in a 5 mm magnetic wall at low B, per EDDY_LOADED_STATIC_START §1) that the cold transient barely
  had. Four verification periods cannot see such a mode.
  - On the first L180 attempt a badly-fitted m0 jump did exactly that. It was taken before the
    residual gate was made absolute, and it left a 67 W kick and a flat, wrong plateau that the
    gauge called settled (7.66 W).
  - Guards added since: the absolute residual gate for RRE, the alignment test for the single-mode
    step, and the slow-mode check.
  - The remaining +4 % on L180 at cap 24 is the same kind of effect, much smaller. The owner should
    know it before trusting a cap-24 "settled" on a slow body.

### A.4 No-op on machines that settle

Paired, simultaneous, one thread each:

| duty | frames base / accel | every reported value | T series | wall [s] |
|---|---|---|---|---|
| Ø40 L12 rated | 218 / 218 | identical | max \|ΔT\| = 0.0 | 424 / 423 |
| L13 rated | 282 / 282 | identical | max \|ΔT\| = 0.0 | 751 / 748 |

On both machines the gauge passes before the first jump could be taken, and `accelerator` is
null in the result.

### A.5 Cost

- The accelerator adds per jump: one sector decomposition (q = 5 images of ≤ 10 k dofs) and a
  least-squares problem of at most 5 columns. That is milliseconds against about 1.4 s per frame.
- The frame count at the default cap is unchanged (650). Paired timing, L155 rated, base against
  accelerator, 4 threads each, simultaneous: **667 s against 670 s** (+0.4 %, noise). The shaft
  goes 5.255 → 4.088 W.
- The gain is in the value, not the time. To settle, the accelerator needs about 22 periods,
  against more than 70 for the plain march.

### A.6 Open decisions for the owner

1. **Cap for slow bodies.** The default of 16 stays. With the accelerator a cap of 24 settles
   L155/L180 in 866/902 frames (1.33× / 1.39× today). Otherwise the default 16 gives the closer
   value, but capped.
2. **Trust in a "settled" reached through jumps.** L180 at cap 24 is about 4 % above its
   asymptote (A.3 risk). The alternative is to keep `eddy_settled` False whenever a jump was taken
   unless ≥ 8 verification periods pass. That is safer, but it costs 4 more periods.
3. **L180 slow-mode λ.** The secant gives λ = 0.948 against 0.955-0.962 from the 70-period fit. A
   second secant cycle would need 4 more periods.

## B. L155 torque-ripple angular sampling (analysis only)

Setup:
- L155 rated at 36 / 54 / 108 / 216 steps per period, BDF2 and BE, with the default ring (720 ring
  edges in the half model).
- Eddy runs use a 4-period cap, so every step count has the same march length.
- The same study was also run magnetostatic (eddy off), which has no time integration at all, with
  rings of 540 / 720 / 1080 edges (`SB_SLIP_PER_PERIOD` 216 / default / 432).
- 108 is a divisor of the ring. 216 raises nothing, because it is a divisor too. 72 and 144 are not
  divisors of the default ring, so 54 and 216 were used instead.

| steps/period | eddy BDF2 ripple [%] | eddy BE [%] | static [%] | static, ring 1080 [%] | 6th / 12th / 18th [% of T] (eddy) |
|---:|---:|---:|---:|---:|---|
| 36 | 1.584 | 1.541 | 1.267 | 1.314 | 0.787 / 0.364 / 0.058 (Nyquist) |
| 54 | 1.902 | – | 1.422 | – | 0.775 / 0.340 / 0.108 |
| 108 | 1.888 | 1.882 | 1.472 | 1.522 | 0.771 / 0.333 / 0.110 |
| 216 | 1.912 | – | 1.486 | 1.541 | 0.769 / 0.332 / 0.110 |

Separating the contributions, all against 216 steps (1.912 %):

- **Time scheme: about 0.4 %.**
  - The 36-step BDF2 torque at its 36 angles equals the 108-step torque at the same angles
    (offset 0) to max |ΔT| = 0.078 N·m (0.04 %). The ripple at those 36 angles is 1.577 %
    (108-step run) against 1.584 % (36-step run).
  - BE is 2.7 % lower than BDF2 at 36 steps and 0.3 % lower at 108.
- **Angular sampling of the peak-to-peak: about −17 %.**
  - Static case: the 36-step run equals the 108-step run at the same angles to 0.0017 N·m. Its
    ripple (1.267 %) is simply the peak-to-peak of the waveform at those angles.
  - The other two 36-angle sub-sets of the same 108-step waveform give 1.229 % and 1.422 %
    (static), and 1.867 % and 1.877 % (eddy).
  - So the 36-step value depends on where the samples fall relative to the 12th/18th-harmonic
    peaks. At 36 samples the 18th harmonic sits exactly at Nyquist, and its amplitude halves in
    the DFT (0.058 against 0.110).
- **Ring spacing: about +3.5 %.**
  - Ring 540 vs 720 edges: identical (1.5842 vs 1.5843 %).
  - Ring 1080: +3.4 % at 36, 108 and 216 steps (static). It is systematic, not converged, and it
    is the same ring-density scatter the solver comment records (−5 to −11 % on another die). Mean
    torque moves by 0.004 %.
- The 6th-harmonic amplitude itself (0.77-0.79 %) is resolved at every step count to 2 %. The
  deficit is the peak-to-peak metric, not the harmonic content.

**Proposal (not implemented; owner's call):**
- The rule for a *reported* ripple: **steps/period ≥ 9 × cogging (slot-passing) cycles per
  electrical period**, snapped up to a ring divisor.
  - L155 (12 cycles/period): 108 steps. The ripple is then within 1.3 % of 216 steps.
  - Wall time at a 4-period cap: 849 s against 657 s at 36 steps.
- Losses stay at 36 steps unless the owner's step-count decision (EDDY_TIME_INTEGRATION open item 1)
  changes them.
- The cheap alternative (keep 36 steps and flag the ripple as sampling-limited, with the
  sub-sampling spread as its uncertainty) is honest but less useful.
- The existing `_cogging_frame_policy` already computes cycles/period and warns below 6/cycle, so
  the rule would sit there.
- Ring density (+3.5 % at 1.5× the ring) is a separate, smaller question for a ring study.

## Code, tests

- `simulation/periodic_accel.py` (new):
  - `rre_extrapolate`
  - `shift_cycles`, `sector_components`, `restrict_shift`
  - `single_mode_extrapolate`, `secant_step_scale`
  - `extrapolate_by_sector`, `slow_tail_resid`
- `simulation/fem_solver_2d.py`: the accelerator at the extension decision (θ = −dθ), the ≥ 4
  verification periods, the slow-mode check, and `eddy_settle_gauge.accelerator` in the result.
- `tests/test_periodic_accel.py`, 11 tests:
  - exactness on affine maps
  - sector decomposition sums and eigen-components
  - solver map convention
  - single-mode exact / refuses rotation
  - by-sector removal of rotating pairs plus a 30× smaller real mode
  - secant exactness
  - slow tail
  - refusals
- Also run: `tests/test_eddy_period_gauge.py` and `tests/test_eddy_settled_flag.py` (24 passed).
- Knobs:
  - `SB_EDDY_ACCEL=0` turns it off; the march is then bit-identical to before.
  - `SB_EDDY_ACCEL_SKIP`, `_K`, `_POST_SKIP`, `_K2` set the schedule. The defaults are 1 / 2 / 1 / 1.
- Physics-regression pins: the 30 mm fixture settles within 4 periods, before any jump, so it is
  untouched. `test_eddy_settled_flag` was run. The full `physics_regression` suite was not run: CPU
  rule.
