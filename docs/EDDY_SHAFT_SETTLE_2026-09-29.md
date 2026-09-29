# L155 shaft settle: what the gauge saw, and a DC error correction (2026-09-29)

Claude Opus 5.5 (`claude-opus-5-5`), one agent, no escalation. Branch
`perf/l155-shaft-settle` from `pre-migration-freeze-2026-09-15` (6ed3641). Input: the
profiling study (PR #56, `SOLVER_PROFILING_2026-09-29.md` / `GPU_TDM_STUDY_2026-09-29.md`
§4.5): on L155 eddy 614 of 650 frames are warm-up and the run hits the 16-period cap
because the SHAFT gauge oscillates (15 → 36 → 47 → 105 → 108 → 12 → 33 %) while magnets,
copper and sleeve settle in about 6 periods. Owner's rules: only real physics, no
filters, no loosened tolerance.

## Summary

| | frames | settled | P_shaft [W] | vs P∞ = 4.009 W |
|---|---:|---|---:|---:|
| HEAD (RRE accelerator, cap 16) | 650 | no (3.3 %) | 4.131 | +3.0 % |
| HEAD start, plain march, 40 periods | 1514 | no (16 %) | 4.324 | +7.9 % |
| **this branch** | **614** | **yes (0.36 %)** | **3.994** | **−0.4 %** |

- **The oscillation is real, and it is not the shaft's slow mode.** It is a 5-period beat of
  rotor-fixed DC current patterns that are *not* pole-pair periodic, imprinted into the
  solid wall by the one-angle static start. Their loss is a 1.2·f_e line that no
  electrical-period mean can cancel.
- **Under it is a genuinely slow physical mode:** the rotor-frame DC magnetisation of the
  5 mm 42CrMo4 wall relaxes by magnetic diffusion with τ = 21 periods (18 ms). The spectrum
  is the slab ladder 1 : 4 : 9. A plain march needs about 70 periods to be within 2 %. No
  gauge change can honestly shorten that.
- **Fix, in two parts:**
  1. Start the rotor conductors from the mean of their pole-pair images. This removes the
     imprint and the beat.
  2. After periods 2 and 4, correct the rotor-frame DC error of the slow ring conductors with
     one static solve of the period-averaged exact Jacobian. This is a time-periodic
     explicit error correction (TP-EEC). It is verified afterwards by the unchanged gauge
     plus a slow-mode tail test at the operator's slowest λ.
- **Result:** L155 now settles, at 614 frames, with the shaft 0.4 % from its asymptote.
  Every other reported value is within 0.002 % of the 40-period march's asymptote.
- **The honest limit.** The frame count falls only 5.5 %. After the correction the only
  transient left is a small residue of the non-periodic patterns (|λ| ≈ 0.95, a ±0.1 %
  beat). The strict tail test (×23 on the last change) takes 11 periods to accept it.

## 1. Measurements (L155 rated, 36 steps, BDF2, cold, solver-direct)

Server sandbox `/opt/motres/compute/shaft-chol-20260929`: a container per run from the
`deploy-api` image, `nice 19`, `ionice -c3`, 6 MKL threads, one run at a time. The duties are
the owner's dies copied from the PR #56 sandbox, with the same material library. A snapshot of
the solver with an opt-in dump (per-frame group losses, and the rotor state at every period
decision) was used for diagnosis only. It is not in the repository.

### 1.1 The per-period shaft loss of a plain march (accelerator off, 40 periods)

Period means [W], HEAD start:
`28.0 14.9 11.1 8.55 7.37 7.08 7.25 6.89 6.17 5.72 5.70 5.92 5.79 5.38 5.11 5.13 5.32 5.26
4.97 4.77 4.81 4.96 4.93 4.71 4.56 4.60 4.73 4.70 4.53 4.41 4.45 4.55 4.54 4.41 4.31 4.34
4.43 4.42 4.31 4.23`

The changes repeat with a period of 5 (+, −, −, −, +). The gauge sees sign changes, assumes
q = 0.9, and reports 9× the last change: 16 % at period 40.

**Spectrum of the per-frame shaft loss** (periods 11–40, 1080 frames), in cycles per
electrical period:

| line | 0.2 | 1.0 | **1.2** | 6.0 | 12.0 |
|---|---:|---:|---:|---:|---:|
| amplitude [W] | 0.005 | 0.009 | **0.917** | 0.538 | 0.057 |

The 6th and 12th lines are the steady slotting ripple. The 1.2·f_e line (= 6·f_mech) exists
only in the transient: it is the loss that the non-periodic DC patterns cause as the stator
field sweeps over them. It is not a multiple of f_e, so a whole-period mean aliases it to
0.2 cycles per period, which is the beat.

**The rotor state per period** (4215 shaft dofs, split into the eigen-sectors of the exact
pole-pair image map S, q = 5, σ = +1), in the σ-norm of the period change:

| sector | change, p1 → p38 | ratio per period | rotation per period |
|---|---|---|---|
| m0 (pole-pair periodic) | 1.1e-3 → 7.7e-5 (modulated ×0.3…×2.5 at the beat) | 0.94 envelope | – |
| m1 (rotating pair) | **1.6e-2** → 3.8e-3 | 0.939 → **0.967** | cos = 0.309 = cos 2π/5, every period |
| m2 | 1.0e-3 → 2.4e-4 | 0.97 | – |

- The m1 pair carries 15–50× the m0 change.
- It rotates exactly 72° per period. This is the relabelling of a pattern that is fixed in
  the rotor material: the pattern is a DC current distribution in the shaft rotor frame.
- It decays with |λ| = 0.965 (τ ≈ 28 periods).
- A fit of the period means from period 11, P∞ + a·λⁿ + b·μⁿ·cos(2πn/5 + φ), gives
  λ = 0.92, |μ| = 0.953, a beat amplitude of 0.30 W, P∞ = 3.98 W and 13 mW rms.

### 1.2 Where the beat comes from

- On the periodic orbit, the rotor-frame DC field of a conductor is its time mean over the
  q periods the image map needs to close. That mean is invariant under S (sector m0).
- The non-invariant content of the orbit is AC in the rotor frame, and the skin layer
  screens it.
- The one-angle static start instead freezes the instantaneous field into the whole wall,
  including its non-invariant part: slotting and MMF harmonics at that angle. This is the
  RL "switch-on DC offset" of every rotor-frame harmonic.
- In a thick magnetic wall that offset decays only by DC diffusion.

**Test: start from the pole-pair image mean.** Replace the rotor-conductor history of the
static start by its mean over the 5 images (the projection on sector m0; 15 % of the
σ-weighted state is removed). This experiment averaged the magnets too; the shipped start
averages only the shaft and sleeve (§4). Plain march, 40 periods:

- Period means [W]: `13.1 9.50 8.22 7.49 6.98 6.61 6.34 6.12 5.94 5.78 … 4.41 4.39 4.37 4.36 4.34`.
  They are monotone, with no beat.
- m1 change at period 1: 1.9e-4 against 1.6e-2 (83× smaller). m2: 15× smaller.
- Gauge at period 40: 2.5 % (HEAD start: 16 %).

### 1.3 The slow mode that is left: DC diffusion through the wall

DMD of the m0 period changes (periods 9–39, rank 3) gives λ = 0.954, 0.831 and 0.654. As
time constants that is τ = 21.3, 5.4 and 2.4 periods, in the ratio **1 : 3.95 : 8.9**: the
k² ladder of magnetic diffusion across a slab with the field at both faces.

- τ₁ = 18.0 ms means μ_diff ≈ 1300 for d = 5 mm and σ = 4.4 MS/m. That is the low-field
  permeability of 42CrMo4.
- The period means fit P∞ + Σₖ aₖλₖⁿ with these three λ at 0.7–1.5 mW rms. The fit gives
  P∞ = 4.009 W (4.006–4.011 for fit starts at periods 6–15) and a₁ = 2.17 W (54 % of P∞ at
  n = 0).
- The same fit gives the other asymptotes:
  - magnets, 2-D solid: 4161.03 W (+0.017 % over period 40)
  - sleeve: 9.8353 W
  - copper group: 1838.229 W
- A plain march therefore needs ≈ ln(2.17/0.08)/0.047 ≈ 70 periods to bring the shaft within
  2 %. This is physics (18 ms in the real machine too), not the gauge.

### 1.4 The hypotheses, one by one

| hypothesis | verdict (evidence) |
|---|---|
| long L/R time constant of the solid shaft | **yes**, the m0 mode: τ = 21 periods, slab ladder 1:4:9 (§1.3) |
| sub-harmonic from the commensurate window / slot-harmonic beating across periods | **yes**, the oscillation. Rotor-fixed non-periodic DC patterns, q = 5 rotation, 1.2·f_e loss line (§1.1). Excited by the start, not by the orbit (§1.2) |
| DC / zero-sequence component | the "DC" is the rotor-frame DC magnetisation of the wall. The stator has none: the copper gauge is 1e-5 % |
| numerical noise from BDF2 | **no**: smooth deterministic decays, 72.0° rotation every period, mode fit at mW rms |
| gauge definition on a noisy per-period mean | **no**: the means are exact integrals and the transient is real. The gauge's q cap (0.9) *under-reads* a λ = 0.954 tail by 2.3×, hence the added slow-mode test |

### 1.5 The periodic-state accelerator (RRE, `EDDY_PERIODIC_ACCEL_2026-09-26.md`) on this machine

- **HEAD.** Period 5: RRE on m1/m2. Period 9: a single-mode m0 step with λ = 0.9386 (true
  0.954, biased by the second wall mode). The secant step never ran: 3 periods after the
  jump the measured-q rule read the kick as a fast decay (0.003 %), so no slow body was
  named, and after that there was no room. Result: capped at 16 periods, 3.3 %, shaft 4.131
  W and rising.
- **With the image-mean start:** λ estimates of 0.83–0.87. Capped, 4.4 %, shaft 4.42 W.
- **Offline, on the 40-period states:** RRE (4–7 states), the single-mode step, DMD and a
  slab-structured fit from windows ending by period 10 remove only 40–75 % of the m0 error.
  λ₂ = λ₁⁴ cannot be separated from λ₁ in a few periods, and the step λ/(1−λ) ≈ 21
  amplifies any error in λ by 470×. Extrapolating from the march alone is not accurate
  enough here.

## 2. The fix

### 2.1 Start from the pole-pair image mean (`fem_solver_2d._rotor_image_mean`)

- The cold static start keeps the whole field. Only the rotor conductors' history (shaft,
  sleeve, magnets) is replaced by its mean over the pole-pair images.
- `periodic_accel.pole_pair_image_mean` is the projection on the eigenvalue-1 space of S.
  With σ = −1 that space does not exist and the mean is 0.
- Nothing the orbit's slow part has is touched: the invariant sector is kept exactly.
- The result records it in `eddy_cold_start.rotor_image_mean` (q, σ, dofs, share removed).
- `SB_EDDY_START_IMAGE_MEAN=0` gives the one-angle start of before.

### 2.2 DC error correction (`periodic_accel.dc_error_correction`)

The slow part of a solid ring conductor relaxes as σM·ẋ + J̄·(x − x*) = 0, where J̄ is the
exact Jacobian averaged over one electrical period. The average removes the fast AC of the
skin layer, and the non-conducting region is eliminated by a full static solve. One period
changes the state by u = (e^{−TA} − I)(x − x*), with A = (σM)⁻¹J̄, so

    x* = x + (1 − e^{−TA})⁻¹u = x + J̄⁻¹σM·u/T + u/2 + O(μT)·u

- **One static solve corrects all the wall's slow modes at once.** The remainder is μT/12
  of each mode's correction: 0.4 % for the slow mode.
- **Averaging.** J̄ is the mean of K + T over the 36 frames of the period that ends in the
  correction. T is the *unclamped* tangent (`P2Nonlinear.tangent2(clamp=False)`): it is the
  exact linearised dynamics, not a Newton step.
- **Only the periodic (m0) part is corrected.**
  - u and the correction are projected on sector m0. On the orbit the rotor-frame DC is m0.
  - J̄ is taken at one stator position, so J̄⁻¹ of an m0 vector is not m0. Its image mean
    is what the average over all positions gives, from one solve.
  - Measured: without the projection, the correction injected 7× the rotating content and
    the run capped at 16 periods.
  - With the operator symmetrised over the images and all sectors corrected, the run
    capped at 24 periods (938 frames) with a ±1 % beat. Both were rejected.
- **The slow-mode λ.** Inverse iteration on J̄⁻¹σM (m0) gives the slowest periodic mode:
  λ = 0.956 and 0.960 on L155 (DMD of the march: 0.954). That λ raises the cap to 24 for a
  slow body (the existing slow-body cap rule). The post-correction slow-mode tail test then uses it: the
  last change ×λ/(1−λ), ≤ 2 % of the body's own loss. This is stricter than the gauge's q
  ≤ 0.9, never looser.
- **When it runs.**
  - Bodies: the closed rings cut by the (anti)periodic boundary (shaft, sleeve: no ∫J
    row, U ≡ 0) that the gauge calls unsettled.
  - Only when that body is judged on its own level (≥ 1 W or ≥ 2 % of the machine loss).
    A milliwatt shaft is left alone and the period average is not even assembled.
  - Only after extension periods 2 and 4 (`SB_EDDY_EEC_AT`), and never in a full-ring
    model.
- **Honesty guards (unchanged from the RRE accelerator).** Only the discarded prefix moves. The gauge
  record restarts at each correction. ≥ 4 whole continuous periods are needed after the
  last correction, plus the slow-mode test. Every correction is recorded in
  `eddy_settle_gauge.accelerator.jumps`, and `eddy_settled_via_accelerator` says so.
- `SB_EDDY_EEC=0` switches back to the RRE accelerator. With `SB_EDDY_START_IMAGE_MEAN=0`
  as well, the march is the one of before, bit for bit.

### 2.3 L155 with the fix

Shaft period means [W]:

| after | shaft period means [W] |
|---|---|
| periods 1–2 (image-mean start) | 12.88, 9.47 |
| correction 1 (λ 0.956) | 4.279, 4.198 |
| correction 2 (λ 0.960) | **4.035, 4.011, 4.010, 4.014, 4.014, 4.007, 4.003, 4.005, 4.009, 4.010, 4.007** |

- Settled after 11 continuous periods. The m0 change fell from 8e-4 to 2–10e-6 after the
  second correction.
- What the tail test waits for is the m1/m2 residue of the start (1e-4, |λ| ≈ 0.95, a
  ±0.005 W beat).

## 3. Reported values (L155 rated)

| quantity | HEAD (650, capped) | this branch (614, settled) | 40-period march (plain, image-mean start) | its asymptote (§1.3) |
|---|---:|---:|---:|---:|
| T_avg [N·m] | 187.93429 | 187.93367 | 187.93507 | – |
| ripple [%] | 1.5644 | 1.5644 | 1.5646 | – |
| V_peak [V] | 436.652 | 436.652 | 436.652 | – |
| P_mag reported [W] | 92.312 | 92.319 | 92.304 | 92.320 (+0.017 %) |
| **P_shaft [W]** | **4.131** | **3.994** | 4.299 | **4.009** |
| P_sleeve [W] | 9.9304 | 9.9314 | 9.9291 | 9.9315 |
| P_cu AC [W] | 609.799 | 609.810 | 609.787 | 609.795 |
| total loss [W] | 3820.45 | 3820.34 | 3820.59 | – |

- Torque, ripple and V_peak: within 7e-6.
- P_mag, P_sleeve, P_cu: within 0.002 % of the asymptotes.
- The shaft: −0.4 % against +3.0 % at HEAD. The change (0.14 W) is 5e-7 of the 280 kW
  output, invisible in η, but the run now reports settled instead of capped.

Wall: 695 s against 668 s at HEAD. The box was shared with a mesher campaign (load 9–18),
so these two are not a paired timing. Per frame, the correction adds the J̄ average over 72
frames and two correction solves plus the inverse iteration, a few seconds per run. The
quiet paired timing is in the PR description.

## 4. Other machines

Ø40 L12 rated and L13 rated (36 / 40 steps, eddy, cold). HEAD against this branch, the
same box and settings:

| | Ø40 HEAD | Ø40 branch | L13 HEAD | L13 branch |
|---|---:|---:|---:|---:|
| frames / settled | 146 / yes | 146 / yes | 162 / yes | 162 / yes |
| T_avg | 0.6218699 | 0.6218693 (−1.1e-6) | 5.399457 | 5.399462 (+8e-7) |
| ripple [%] | 5.64371 | 5.64260 (−2e-4) | 5.28066 | 5.28083 (+3e-5) |
| V_peak | 10.992827 | 10.992773 | 15.888275 | 15.888272 |
| P_mag / P_shaft [W] | 3.094 / 0.009 | 3.094 / 0.009 | 0.441 / 0.439 | 0.441 / 0.439 |
| total loss [W] | 65.795 | 65.789 | 198.717 | 198.717 |
| settle residual | 0.67 % | 0.66 % | 0.63 % | 0.16 % |

- The shafts carry milliwatts or less than 2 % of the machine loss. The DC correction does
  not engage there, and neither does its Jacobian average.
- Only the start differs. The values move inside the settle tolerance they were always
  reported at.
- A first version also image-averaged the magnets. It kicked them: Ø40 needed one more
  period (182 frames). Magnets settle inside a period, so they now keep the static start.

## 5. Tests

- `tests/test_periodic_accel.py`:
  - the image mean is sector m0, invariant, a projection, and 0 without an eigenvalue 1
  - the DC correction lands on the fixed point of an S-equivariant slow diffusion to 3 %
    of the marched error (the u/2 term is worth 10×)
  - its λ is the slowest periodic mode to 1e-6
  - with a one-position operator the correction stays pole-pair periodic
- Also run: `tests/test_eddy_period_gauge.py`, `tests/test_eddy_settled_flag.py`,
  `tests/test_eddy_settle_stop_rule.py`, `tests/test_p2_nonlinear.py`,
  `tests/test_physics_regression.py`: 53 passed. The 9 baseline cases fail, but they fail
  identically at HEAD, with the same drift lines at the printed six digits (+2.5 % torque
  on the magnetostatic cases, for example). The pinned baseline no longer matches the repo
  config at 6ed3641. That is flagged separately and was not re-pinned here. The 30 mm
  fixture's shaft is a minor body, so the correction does not engage there.

## 6. Open for the owner

1. Of the 614 frames, 11 periods are verification of a ±0.1 % non-periodic residue. A
   start that also matches the orbit's non-periodic *skin* content, or a correction of the
   rotating sectors with an operator averaged over the q periods of a full relative
   revolution, would remove it. Neither is attempted here: one period of J̄ is not enough
   (§2.2, measured).
2. The slow-mode test uses the slowest periodic mode of J̄ (0.960). The mode the march
   actually excites is 0.954 (DMD). The stricter choice is kept.
