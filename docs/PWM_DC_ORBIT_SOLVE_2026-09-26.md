# PWM settle: the DC offset solved, not anchored — and Stop during set-up (2026-09-26)

Task 3 of the pre-migration freeze, on branch `task3-pwm-dc-solve-and-stop` from
`pre-migration-freeze-2026-09-15` (549997e). Follows `docs/NO_FILTERS_2026-09-24.md`
item 5, which stopped at three options for the PWM drive's period-mean DC anchor; this is
option (c).

## 1. What was wrong with the anchor

Under an imposed voltage the phase currents are circuit state. The phasor initialiser
lands near the periodic orbit, and the modulator switching on (after the coarse,
sinusoid-only part of the mixed settle) kicks the state off it again by about half the
ripple amplitude. Both leave a stationary-frame DC that decays with τ_e. On the L155 that
is 27 electrical periods, so a settle cannot wait it out.

The anchor measured each settling period's mean current and subtracted it from the circuit
state at the period's end. The gain was learned from its own previous firing and clamped to
[0.2, 3]. Its model had **no free decay**: it assumed the DC measured over a period is
still all there at the period's end. On a short-τ_e machine most of it has already decayed,
so the anchor over-corrected, and the reported window opened on its last correction.
Measured here on the 30 mm regression fixture (τ_e ≈ 0.94 periods by the phasor's L/R):
**0.55 A of DC left, 0.68 A with a 12-period settle** — "however long the settle", as the
NO_FILTERS doc found on the Ø40 (1.1-1.2 A).

## 2. What is solved instead (`simulation/dc_orbit.py`)

Sum the solved Crank–Nicolson line-to-line rows over one whole electrical period and the
flux telescopes:

    y(end) − y(start) = Σ D·v·Δt − R·Σ S·ī·Δt,     y = (ψ_A − ψ_B, ψ_B − ψ_C)

The steady state is the fixed point y* of the **period map** of the boundary flux. For a
magnetostatic field that map is R² → R² exactly: the line-to-line flux is the circuit
state, and the field follows from it. With x = y − y* and M the period Jacobian:

    drift δ = (M − I)·x(start)          correction  w = −x(end) = M·(I − M)⁻¹·δ

This is a Newton step of a shooting method, in **flux**:

- **δ is exact.** It is the converged frame's flux minus the CN state the period started
  from. It carries no estimator and no carrier aliasing. For a symmetric bridge (Σv·Δt = 0
  per period, the sinusoid and the synchronous regular-sampled PWM, checked to 1e-13 V·s)
  the orbit has zero net DC. In general it has exactly the DC the identity allows, and
  nothing assumes which.
- **M is computed, not guessed or learned.** It is the product over the period of the
  linearised CN rows (the variational equation):

      δy_k = (I + R·h_k·S·L_k⁻¹)⁻¹ · (I − R·h_k·S·L_{k−1}⁻¹) · δy_{k−1},   h_k = Δt_k/2

  L_k = D·[∂ψ/∂i_A, ∂ψ/∂i_B] is frame k's **incremental** inductance: the columns its own
  Newton already solved for, so there is no extra solve. That matters. On the fixture the
  phasor's apparent Ld/Lq predict a DC decay of 0.28 per period; the machine's incremental
  one is 0.06. A first version of this solve built on the former over-shot ten-fold.
  Independent check, long-τ case below with corrections off: the measured period-to-period
  DC ratio is **0.9127** on the coarse orbit and 0.909 on the fine one; the Jacobian's
  eigenvalues are **0.9128** and 0.909-0.913.
- **The CN current memory moves with the flux** through the same columns. It enters the
  next step only through R·Δt/2.
- **The last settling period is never corrected.** It runs free, and its drift and DC are
  the solve's verification (`v_dc_orbit.periods[-1]`). The reported window follows it with
  no state jump in front of it.
- A period whose Jacobian is not contractive (spectral radius ≥ 1) is **refused**: it is not
  corrected, and it is logged as a warning and counted. A DC mode that grows is not a
  circuit with a resistance in it.

### 2.1 The modulator's turn-on (mixed coarse/fine settle)

The fine orbit is the coarse one plus the switching ripple's periodic flux, and at the
boundary that flux is not zero. With Ψ(t) = ∫(v_pwm − v_fund)·dt from the boundary, the CN
period rows make the ripple current's trapezoidal mean vanish, so the ripple flux at the
boundary is

    c = −⟨Ψ⟩

This is exact for a constant inductance and R·T ≪ L. It is computed from the modulator
alone, on its exact per-step volt-seconds over one whole fine-grid period, and applied at
the coarse→fine boundary as the Newton **predictor**. The first fine period's drift
measures what it missed (saturation, saliency, dead time), and the Newton step at its end
takes that out. Synthetic check: it removes the kick to O(R·T/L), saliency or not
(`tests/test_dc_orbit.py`). FEM: 94 % of the kick on the fixture, 98 % on the long-τ case.

### 2.2 A schedule defect this exposed: the handover step was one coarse step long

In the mixed schedule the last coarse frame sits at nP − Δθ_c and the fine periods end on
nP − Δθ_f. The first fine frame was placed at nP, one **coarse**-length step on, so the
first fine "period" spanned P + Δθ_f, not a whole period. Its flux drift carried the
orbit's own motion over the extra step: measured 1.1e-4 Wb, against 6e-6 Wb from its DC.
The old anchor measured its DC over the same crooked window. The r − 1 fine frames that
complete the last coarse step are now on the fine grid. That is +1 frame at the usual
ratio 2; every frame from nP on, the reported window included, keeps its angle. Every
solved period is whole: `tests/test_pwm_dc_orbit_fem.py` checks the CN identity
δ = −R·T·S·d̄ on every period of a real PWM run.

## 3. Evidence

Synthetic (`tests/test_dc_orbit.py`; salient linear machine on the same CN rows, exact
orbit by an affine solve). Start 40 A off the orbit. The window's DC is **< 1e-9 A** for
decays of 2e-4 to 0.98 per period, with and without the coarse→fine handover. The
variational M equals the finite-difference Jacobian of the marched period map to 1e-6.

FEM, 30 mm regression fixture, PWM 20 V bus, 7.0 V pk / +10°, solver-direct, cold,
magnetostatic. Three operating speeds put the DC mode's decay at 0.06, ~0.76 and 0.91 per
period. Harness in the session scratchpad; each result asserts the module path it ran.

### 3.1 Short τ — 15 000 rpm, 72 steps, 9 carriers (8/carrier), adaptive settle 3 periods

| run | settle | DC left [A] | T [N·m] | ripple pp [N·m] | P_cu [W] | I_rms [A] | wall [s] |
|---|---|---:|---:|---:|---:|---:|---:|
| **reference**: all-fine, 6 free periods, no handover | 6 fine | −0.000 | 0.238101 | 0.155515 | 82.8096 | 50.6286 | 665 |
| **new** (this change) | 1 c + 2 f | 0.000 | 0.238101 | 0.155514 | 82.8097 | 50.6284 | 242 |
| free, mixed schedule (solve off) | 1 c + 2 f | 0.025 | 0.238075 | 0.155777 | 82.7825 | 50.6252 | 379 |
| free, 12 periods | 10 c + 2 f | 0.024 | 0.238075 | 0.155769 | 82.7829 | 50.6256 | 522 |
| old anchor | 1 c + 2 f | **0.554** | 0.237598 | 0.161602 | 82.2178 | 50.5537 | 455 |
| old anchor, 12 periods | 10 c + 2 f | **0.678** | 0.237497 | 0.162970 | 82.0922 | 50.5376 | 736 |

New vs reference: T 0.0000 %, P_cu +0.0002 %, I_rms −0.0004 %, ripple −0.0006 %. Old
anchor vs reference: T −0.21 %, P_cu −0.72 %, ripple +3.9 %. The free mixed runs are not
the reference either, because their window opens 2 fine periods after an uncorrected
turn-on kick: P_cu −0.03 %. (Walls were taken with 2-3 jobs sharing 4 cores and are
indicative only.)

Per-period trace of the new run: coarse DC 4.79 A → corrected (predictor 6.0e-5 Wb
included) → first fine period 0.35 A → corrected → verification **0.0005 A**, drift
7e-10 Wb.

### 3.2 Long τ — 600 000 rpm (voltage, bus and carrier ×40): λ = 0.913/period

This keeps the same mesh and per-frame cost, with the decay of an L155-class machine
(τ ≈ 11 periods by the Jacobian; adaptive settle 12 = 10 coarse + 2 fine).

| run | DC left [A] | T [N·m] | ripple pp [N·m] | P_cu [W] | I_rms [A] |
|---|---:|---:|---:|---:|---:|
| **new**, 2 fine periods (default) | −0.001 | 0.178373 | 0.162644 | 5318.003 | 63.4269 |
| new, 3 fine periods | −0.000 | 0.178373 | 0.162643 | 5318.003 | 63.4269 |
| free (solve off) | **18.8** | 0.178465 | 0.251574 | 5414.868 | 64.8453 |
| old anchor | **2.56** | 0.178338 | 0.175407 | 5320.136 | 63.4329 |

Without a solve the turn-on kick (~23 A) stays in the window: ripple +55 %, P_cu +1.8 %.
The old anchor learned a scale of 0.554 and over-corrected the kick (−8/−15/+23 A →
+9/+10/−19 A), leaving 2.56 A, over the 0.5 A tolerance. The new run's coarse Newton goes
1.72 → 0.73 → 0.013 → 0.0004 A (then at the Newton-tolerance floor). The predictor puts
the first fine period at 0.45 A, the fine Newton takes it to 0.006 A, and the window
reads 0.001 A. Two fine periods (Newton + verification) agree with three to 7 digits, so
`SB_PWM_FINE_SETTLE` stays at 2.

### 3.3 Medium τ — 150 000 rpm (×10), against an all-fine free reference

Decay by the Jacobian 0.70 per period; adaptive settle 12 = 10 coarse + 2 fine.

| run | DC left [A] | T [N·m] | ripple pp [N·m] | P_cu [W] | I_rms [A] | wall [s] |
|---|---:|---:|---:|---:|---:|---:|
| **reference**: all-fine, 30 free periods, no handover | 0.001 | 0.185954 | 0.161958 | 593.5421 | 62.70045 | 2045 |
| **new** | −0.000 | 0.185955 | 0.161957 | 593.5424 | 62.70044 | 603 |
| free, mixed (solve off) | **9.57** | 0.185604 | 0.211724 | 597.4541 | 62.85247 | 611 |
| old anchor | **4.53** | 0.185779 | 0.185902 | 593.4558 | 62.67926 | 620 |

New vs reference: T +0.0005 %, P_cu +0.00005 %, ripple −0.0006 % — the same orbit, in 30 %
of the reference's wall time. Old anchor vs reference: T −0.09 %, ripple +14.8 %; free:
ripple +30.7 %, P_cu +0.66 %. The reference's own free decay, period to period (DC in
phase C 29.19 → 20.05 → 13.96 → 9.80 A: ratios 0.687, 0.697, 0.702) is the Jacobian's
0.698-0.702 — the second independent check of M.

## 4. Stop during set-up

The Stop button is honoured by the progress callback raising (the route's `_RunCancelled`,
a BaseException). The callback fired for the first time at frame 0, so everything before
it ran deaf: the CAD polygons and the mesh, the per-tag assembly, the sliding-band
projections, the phasor initialiser's Picard, and the static start field. That is up to
~30 s on a large machine. Each stage now starts with a checkpoint, and so does every
phasor sweep and every demag re-solve of a frame. The checkpoint calls the callback with
done = total = None, which every consumer reads as "keep what the bar shows"
(`ProgressTracker.update`; the route's `_sb_progress` was taught the same). The d-axis
calibration's nested solve passes its own checkpoints through. `tests/test_setup_cancel.py`
stops the real solver at named stages (start, mesh, projections, phasor sweep 0, first time
step, static start field) and asserts that no frame was solved after the Stop. At the first
checkpoint the whole call returns in < 5 s with nothing built.

## 5. Payload and knobs

- `v_dc_orbit` replaces `v_dc_anchor_applied`. It holds the method, `correcting`,
  `corrections`, `refused`, and per period: frame, resolution, exact drift [Wb], trapezoidal
  DC per phase [A], correction [Wb] (None on the verification period), verdict, the
  Jacobian's eigenvalues, and the turn-on predictor where applied.
  `v_drive_diag.dc_anchor_A` is gone.
- `pwm.mixed_settle.dc_orbit_corrections` replaces `dc_anchors_applied`.
  `fine_settle_frames` counts the handover frame(s).
- `SB_V_DC_SOLVE=0` measures and reports without correcting. It is for a reference run's
  long free settle, never for a result. Any value other than `0`/`1` is refused at import.
- No UI change: nothing in `web/src` read the anchor fields.

## 6. Scope and what did not move

- The sinusoidal voltage drive keeps its Δ² anchor (every pinned voltage number was made
  with it). `tests/test_physics_regression.py -k p2_voltage`: 3 passed, unchanged. No PWM
  case is pinned in `tests/physics_baseline.json`.
- Imposed-current drives are untouched: they have no circuit state.
- Coupled eddy + PWM: the columns carry one step's eddy reaction (the transient inductance),
  and the eddy history's memory is not in M. The error that leaves points toward
  under-correcting, and the next boundary's drift measures it. Measured on the fixture
  (eddy + rotor eddy, 72 steps, 9 carriers, adaptive settle 3). The fine Newton step cut
  the drift ~30× (3.1e-6 → 1.0e-7 Wb), not to noise, as predicted. Result against an
  all-fine 6-period free reference:

  | run | DC left [A] | T [N·m] | ripple pp [N·m] | P_cu [W] | I_rms [A] |
  |---|---:|---:|---:|---:|---:|
  | reference (all-fine, 6 free periods) | 0.008 | 0.239273 | 0.161579 | 82.4765 | 50.6033 |
  | new | −0.010 | 0.239298 | 0.160672 | 82.4885 | 50.5965 |

  The DC is at the reference's level. What moves the other numbers (ripple −0.56 %,
  T +0.01 %, P_cu +0.015 %) is the eddy start-up transient, not the DC: the new run's own
  warm-up gauge reports 2.01 % left after its 3 settle periods, and the reference marched 6.
  That settle length is the eddy gauge's question (NO_FILTERS §7), unchanged here.
