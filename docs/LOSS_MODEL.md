# Loss model (LOSS MODEL) — methodology and validation

*Updated: August 2026. Code: `src/motor_ai_sim/simulation/fem_solver_2d.py`
(transient `fem_transient_sliding_band`), `src/motor_ai_sim/simulation/losses.py`
(iron model), `src/motor_ai_sim/core_loss_surface.py` (interpolation of the
measured P(B, f) surface), `src/motor_ai_sim/materials.py` (fit).*

All losses are computed from the **real data of the assigned materials**
(`config/materials_library.yaml`, transcribed from the owner's personal material
library): measured loss curves P(B, f), BH curves, conductivities σ.

---

## 1. Iron (lamination) — direct interpolation of the measured P(B, f) surface

**Two models, selected by the material's record** (`core_loss_model` in
`config/materials_library.yaml`):

| `core_loss_model` | what is computed | which steels |
|---|---|---|
| `measured_surface` | the manufacturer's own table P(B, f), interpolated directly | B15AHV950M, B10AHV900M, 20RSW175 (data cross-checked against the datasheet) |
| `bertotti` (default) | three coefficients fitted to the curves | every other library record |

Opt-in **per record**, not "has curves": 20SW1200 also has 10 curves and 269 points,
but the table's provenance was never checked against a datasheet, and the path on which it
has been validated must not drift out from under it.

### 1.0 Why a surface, not three coefficients

The three-term form carries a **fixed B² power**:

```
P [W/m³_steel] = kh·f·B_s² + kc·f²·B_s² + ke·f^1.5·B_s^1.5
                 hysteresis  classical   excess
```

Real isotropic steel **bends upward** above the knee, and no three
constants can hit both the bend and the low-induction region at once.
Measured on B15AHV950M against the real field of the 150-mm machine at 933 Hz: the fit gives
53.6 W of loss per cycle where the manufacturer's own surface gives 62.3 W
(**-16.4 %**), because 43 % of the stator's losses lie above 1.5 T. At the point
1.5 T / 933 Hz the fit underestimates by 13 %.

### 1.1 Interpolation scheme

Log-log, C1 along both axes (the optimizer must not see kinks —
those are spurious gradients):

1. **Over B, at each measured frequency** — PCHIP (monotone cubic
   Fritsch-Carlson Hermite) through (ln B, ln P). Passes **exactly** through each
   measured point, C1, and produces no overshoots between nodes, unlike an
   ordinary spline. Beyond the curve's range — a straight line in log-log, i.e. a power law with
   the slope at the edge (P -> 0 as B -> 0, the bend continues upward).
2. **Over f** — PCHIP again, through (ln f_i, ln P_i(B)). PCHIP's slopes are local
   (node i uses only i-1, i, i+1), so over the interval [f_i, f_{i+1}]
   four curves suffice — the result is identical to a global PCHIP, and
   the cost does not depend on the number of curves in the record.

Result: at **every one** of the 1128 measured points across the three steels, the model returns
the datasheet number to float precision (verified:
`tests/test_core_loss_surface.py::TestAnchors`).

### 1.2 The measured envelope and what lies beyond it

The tables form a **staircase**, not a rectangle: 0.15-mm steel is measured up to 1.886 T
at 50 Hz, but only up to 1.597 T at 1 kHz and up to 0.531 T at 10 kHz (the test
rig cannot drive a saturated flux through a thin sheet at 10 kHz). The envelope
`B_env(f)` is a PCHIP through the curves' top points; at 933 Hz it equals **1.63 T**.

Beyond the envelope (B above `B_env(f)`, or f outside the table's range), the answer is
**blended** with the Bertotti extrapolation, rather than switched to it outright:

```
ln P = (1 - s)·ln P_surface + s·ln P_bertotti,   s = 3u^2 - 2u^3
```

where `u` is the outward distance, 0 at the boundary and 1 at the far edge of the blend
band (a factor of 1.35 in B, one octave in f). `s` and `ds/du` are both zero at the boundary, so
the value **and its first derivative** are continuous — no jump, no kink. Inside the
envelope, the answer is exactly the measurement (`s ≡ 0`).

The blend band's width in B is set by **monotonicity**, not taste: crossing from a curve
that reads higher to one that reads lower "costs" a slope, and with too narrow a
band the losses start to **fall** as the induction rises (measured on
20RSW175 at 933 Hz: a 1.15 band gives a dip between 1.62 and 1.68 T). 1.25 is already
clean across all three records over 20 Hz-40 kHz and 0.02-2.6 T; 1.35 gives the same with margin.
Shifting the band across 1.15...1.45 changes the 150-mm machine's losses by 0.9 %.

Going past the envelope is **logged loudly**, once per half per run, with
the fraction of watts that came from extrapolation: on the 150 mm that's 8.1 % of the stator and 2.6 %
of the rotor; on the 40-mm machine (1517 Hz, whose table only reaches 1.48 T) —
**34.9 % of the stator**, and that needs to be known when reading its number.

### 1.3 Summing over harmonics

A measured point P(B, f) is a **sinusoid**. B(t) in the machine is not sinusoidal,
so each element's locus is decomposed into a series and the surface is
sampled at every harmonic:

```
P = Sum_over_axes Sum_{m>=1} P_meas(B_m / k_f, m·f_elec/n_periods)
```

**This is an assumption: superposition of losses over harmonics.** It is the
standard one (commercial FEM packages offer the same harmonic-loss option),
imperfect, and its error has a known direction: hysteresis is a process with
memory, so the loss of a sum of harmonics is not the sum of their losses. But it is strictly better than a single
"equivalent" amplitude, which does not see the slot ripple at all.
The classical (eddy) part adds up **exactly** (Parseval), so it matches the
integral of ⟨(dB/dt)²⟩ over the time series.

What summing over harmonics gives versus "first harmonic only":
**+22.6 %** on the 150 mm and **+21.6 %** on the 40 mm. The split by half tells
more than the overall number: +10 % on the stator, but **8x** on the rotor (0.33 -> 2.71 W
per sector), because the field in the rotor frame consists almost entirely
of slot harmonics, with almost no first harmonic in it.

**Protection against spectral leakage.** The DFT requires the window
to close on itself. On the stator it does. On the rotor it does not: with 24 slots and
14 pole pairs, a tooth passes a rotor point 24/14 = 1.71 times per
electrical period, i.e. the window breaks mid-cycle. A rectangular DFT
reads this **discontinuity** as broadband content, and since losses grow with frequency,
the spurious tail gets paid for out to 19 kHz: measured, 30 % of the raw rotor
sum sat in harmonics k >= 8 and **grew** toward Nyquist. So the step
is estimated and removed with a linear ramp before the DFT, **per element and with a smooth
threshold**:

- the step is `x[N] - x[0]`, where `x[N]` is extrapolated one sample forward
  quadratically from the last three. The naive difference `x[N-1] - x[0]` is one sample
  of **slope**, not a step: on a discretized sinusoid it gives 1.2e-2 of
  amplitude versus 9e-4 for the quadratic one, and subtracting such a "ramp" ate 2 % of
  the stator's losses, which was periodic to begin with;
- it is removed with a smoothstep weight over `|step|/range` between 0.15 and 0.30 (C1 in the
  data, so that no element crosses the threshold as a jump).

Measured: mean protection weight 0.00 (150 mm) and 0.06 (40 mm) on the stator — i.e.
it does nothing there — versus 0.75 and 0.81 on the rotor, where it removes 37 %
of the raw sum and drops the k >= 8 tail from 30 % to 8 %. Shifting the thresholds to (0.10, 0.25)
or (0.20, 0.40) changes the 150-mm machine's result by 0.2 % — this is not a tuning knob.

**The cost of this**: the removed ramp is a real flux excursion (a beat of a
non-integer frequency against the window). So the rotor harmonic sum is a **lower**
bound, and the raw sum is an upper one: 10.8 W versus 17.1 W on the 150-mm rotor. Closing
this gap requires a capture window that is an integer multiple of the tooth pass (seven electrical periods
on this machine), not a change to the loss model.

### 1.4 The Bertotti fit — still in play (fallback and blending)

The three coefficients haven't gone anywhere: they compute losses for **every** steel without
opt-in, and serve as the extrapolation beyond the envelope for those with the opt-in. So their
quality is still checked (`tests/test_materials_fit.py`), including the
pinned underestimate in saturation — it now describes the **fallback**, not
what is billed.

- `materials.fit_bertotti_from_curves(steel)` — an NNLS fit across **all** measured
  points weighted by 1/P (minimizing relative error across the whole range of
  frequencies/inductions, not just in the high-loss corner).
- For 20SW1200: 10 curves (30-800 Hz), 269 points ->
  **kh=115.3, kc=0.0799, ke=3.84; mean error 6.4 %, p90=15.6 %**.
  (The hand-written YAML coefficients had no excess term at all: ke=0.)
- **Manufacturer data (2026-08)**: three steels moved onto full manufacturer
  curve sets + datasheet. The depth of verification differs, and deliberately so:
  **B15AHV950M** — the active 150-mm machine's steel, full staircase
  (`tests/test_materials_fit.py::TestB15AtTheOperatingPoint`: every measured
  frequency, guaranteed datasheet points, the 933 Hz operating point, and the
  pinned fit underestimate in saturation). The other two are **ingested** for
  selection in the UI — smoke level (`TestIngestedRecords`: data arrived whole,
  B(H) is invertible, the fit is sane, coefficients belong to their own curves). Their full
  validation happens once a machine is built on them, against its operating point:

  | steel | thickness | k_f | rho | curves | points | kh / kc / ke | mean error |
  |---|---|---|---|---|---|---|---|
  | B15AHV950M (Baosteel NGO) | 0.15 mm | 0.92 | 7600 | 15 (50 Hz-10 kHz) | 217 | 111.0 / 0.0530 / 2.076 | 6.8 % |
  | B10AHV900M (Baosteel NGO) | 0.10 mm | 0.92 | 7600 | 12 (50 Hz-10 kHz) | 406 | 120.3 / 0.0364 / 1.650 | 4.5 % |
  | 20RSW175 (Shougang VHs)   | 0.20 mm | 0.95 | 7750 | 23 (50 Hz-20 kHz) | 505 | 119.1 / 0.1078 / 5.948 | 5.6 % |

  Before this, the B15AHV950M record carried **5 curves out of 15** and a description of a different steel
  (Nippon Steel 23ZDKH75, grain-oriented). The full refit shifted the losses at the operating
  point of 933 Hz by only -1.5...-3 %: the frequency range was **not** an extrapolation gap —
  the record already covered 933 Hz with the 800/1000 Hz curves.
- **Lamination factor (k_f) — the algebra**. The magnetic problem is solved on homogenized
  iron, so the solver returns `B_homog = k_f·B_steel`. The coefficients, however, are
  W per m^3 of **steel** at the induction **in the steel**. So the losses must be computed at
  `B/k_f` over a volume of `k_f·V`:

  ```
  P = k_f·V·[kh·f·(B/k_f)^2 + kc/(2*pi^2)·<(dB/dt)^2>/k_f^2 + ke·f^1.5·(B/k_f)^1.5]
    = V·[kh·f·B^2/k_f + kc/(2*pi^2)·<(dB/dt)^2>/k_f + ke·f^1.5·B^1.5/sqrt(k_f)]
  ```

  Previously k_f was applied only to the volume (downward) and not to the field (upward) —
  an underestimate of `1/k_f^2 = 1.18` on the B^2 terms and `1/k_f^1.5 = 1.12` on the excess term.
  **The algebra is the same for both models**: the surface is also sampled at
  `B/k_f` and billed over `k_f·V`.
- Application over time (per mesh element, from its B(t) over a period) — the
  **Bertotti path**:
  - classical: `kc/(2*pi^2)·<(dB/dt)^2>` — from the **time series** of dB/dt of both
    components, correct for ALL harmonics (slot ripple and minor loops
    enter automatically, with no sinusoidal equivalent needed);
  - hysteresis: `kh·f·B_ac^2`, where B_ac is half the swing, **major loop
    only**, one cycle per electrical period. There is no rainflow count of minor
    loops — this is a deliberate choice (the classical Bertotti split), and it makes
    the hysteresis term a **lower bound** wherever the slot ripple is strong:
    on the 24s28p the rotor iron completes 1.81 cycles per electrical period
    (measured), i.e. its hysteresis is undercounted by ~1.8x (approx +1.6 W out of 74.7 W);
  - excess: `ke·f^1.5·B_ac^1.5`.
- **The surface path** bills ONE measured total (§1.3), and the split into
  hysteresis/eddy/excess in this case is a derived quantity:
  the eddy term remains the same honest integral ⟨(dB/dt)^2⟩ (which is also
  the shape of the P_fe(t) time ripple, the only instantaneous part of the losses), and the remainder
  is split in the **fit's proportion**. The Core tile's tooltip and `P_fe_terms.model` say
  this: there is no separate measurement of hysteresis and excess in the table.
  Summing over harmonics already includes minor loops at their own frequencies,
  so the "major loop only" caveat does not apply to this path.
- **Rotational losses**: the field's two components are treated as two independent
  alternating loops, and the contributions are summed (the standard axis-decomposition
  approximation). At the tooth roots the locus rotates — classically that is
  underestimated by up to ~1.5-2x. On the 24s28p, 19 % (by loss weight) of the
  volume has a locus semi-axis ratio > 0.5. No correction coefficient is introduced: this is a stated
  underestimate, not a tunable multiplier.
- The 466.7 Hz operating point lies **inside** the measured range (400/500/600 Hz).
- The material's BH curve is used by the solver for saturation (per-element
  Picard/Newton). The manufacturer BH data from the xlsx needs tail cleanup:
  the measurement reaches a plateau and then **drops** (B15AHV950M: 1.945 T at 49 kA/m -> 1.939 T
  at 80 kA/m — noise on a saturated sample), and before the drop there are stretches with
  `dB/dH < mu0` (B15: 3.9e-7; B10: 1e-7 — this is already B quantized to 3 digits).
  Saturated steel approaches mu0 **from above** and cannot be "stiffer than vacuum".
  Rule: measured points are kept while B is increasing **and** `dB/dH >= mu0`; beyond that
  the tail continues with a mu0 slope. If a mu0 tail were glued on AFTER stretches with
  `dB/dH < mu0`, the differential permeability would drop and then **rise** again — a kink
  on which Newton's tangent stumbles: the coupled eddy case stalled at
  rrel 1.39e-7 against a tolerance of 1e-7 (measured, 2026-08-04). B15AHV950M: measured up to
  28.8 kA/m / 1.933 T, then mu0 -> 1.997 T at 80 kA/m.
- Fallback: if a steel has no measured curves, the YAML coefficients are used.
  For the three steels above, the YAML coefficients **equal** the fit from their own curves, so
  the fallback does not give a second answer.
- Tests: `tests/test_core_loss_surface.py` (surface: anchors, envelope,
  blending, harmonics, leakage protection), `tests/test_materials_fit.py` (fit),
  `tests/test_losses.py` (k_f algebra and split).

### 1.5 What remains between calculation and measurement (24s28p, 150 mm, 94 A, 4000 rpm)

The comparison with an independent calculation is kept in the private data repository.
Our honest calculation: **93.40 W** (was 74.72 on the Bertotti fit, and 67.9 before that).
Torque and voltage did not move by a single digit (31.0587 N·m, 70.742 V) — this is
post-processing of the field, not a different field.

Breakdown of the +25.0 % increase:

| contribution | 150 mm | 40 mm |
|---|---|---|
| surface instead of fit, first harmonic only | +2.0 % (74.72 -> 76.22 W) | +8.6 % (5.97 -> 6.48 W) |
| summing over harmonics | **+22.6 %** (76.22 -> 93.40 W) | **+21.6 %** (6.48 -> 7.88 W) |
| total | **74.72 -> 93.40 W (+25.0 %)** | **5.97 -> 7.88 W (+32.1 %)** |

Split by half (150 mm, whole machine): stator 67.83 -> 82.56 W,
rotor 6.89 -> 10.84 W.

**Agreement with the reference is a coincidence, not a validation.** Below are
the under-estimates that REMAIN in the model and the factor that is not in it at
all; their sum is non-zero, and the total landing on the reference figure does
not cancel any of them:

| source | magnitude | status |
|---|---|---|
| Bertotti shape above B > 1.5 T | **closed** | the surface is sampled directly; the fit remains only as the extrapolation beyond the envelope (8.1 % of stator watts, 2.6 % of rotor watts on this machine) |
| rotational locus | underestimate | 19 % of the volume (by loss weight) has a semi-axis ratio > 0.5 — the axis decomposition does not see this |
| rotor harmonic sum | **lower** bound | the capture window is not an integer multiple of the tooth pass; the leakage protection removes the ramp, the raw sum gives 17.1 W instead of 10.8 W on the rotor |
| beyond the envelope | +-0.9 % | watts computed by Bertotti extrapolation; sensitivity to the blend band width |
| manufacturing degradation (stamping, clamping, welding) | 1.5-2x | **outside the model**: usually covered by a separate production factor |

On the 40-mm machine the number needs to be read more carefully: its stator at 1517 Hz goes
past the top of the table (1.48 T at that frequency), and **34.9 %** of its watts came from
extrapolation, not measurement. The run log states this explicitly.

A note on step-count convergence: P_fe grows from 137 W @24 to 183 W @72 steps —
72 steps resolve the slot dB/dt more fully. The sliding-band path's maximum resolution
is 72 slip-ring positions per electrical period (1008/14), i.e. 72 steps
is the converged value. **This matters more now that we use the surface**:
the harmonic sum sees exactly N/2 harmonics, and everything above aliases down, like
any other quantity from this snapshot. 40 steps = 20 harmonics; for a machine where
a noticeable share of the losses sits above that, the step count must be raised, and the difference
between 40 and 72 steps is resolution, not noise.

## 2. Magnets — losses from the real field distribution

**Physics**: `J = sigma·(-dA/dt + U_m)` per magnet element;
`U_m` is a per-magnet constant enforcing `Integral(J dA) = 0` (an isolated
conductor, no axial return current). For magnet halves cut by a sector boundary,
U = 0 — their (anti)periodic image cancels the net current by
symmetry. sigma comes from the assigned magnet material (F45SH_120C: 6.25e5 S/m).

**Implementation — post-processing** on the A(t) histories of the magnetostatic solution:
- the rotor mesh in the sliding-band formulation is the **rotor's material frame**
  (the rotation lives in the slip pair), so dA/dt at a rotor node is already
  a material derivative, no convective term needed;
- the derivative is taken with the same smoothed mechanism used for B-fields
  (`_angle_ddt_2d`: savgol over the unique slip-node positions) — the physics
  (slot ripple, 1-2 cycles per period) passes through, node-stitching noise
  is damped;
- `P(t) = Sum_over_magnets sigma Sum_e (dA/dt_e - U_m)^2·area_e * L_stack * N_sectors`.

**Why NOT a coupled (in-loop) solver**: it was implemented and dropped.
The raw (A_k-A_{k-1})/dt eats fixed-amplitude per-node slip-stitching noise
-> P proportional to (deltaA/dt)^2 grows with the number of steps: P_mag = 142 W @24 -> 516 W
@72 (divergence!), plus a +4 % torque distortion. The smoothed post-processing gives
**42.8 @24 -> 43.4 W @72 — 1.4 % convergence**, torque and EMF
unaffected (the solution remains magnetostatic), compute time +0 %.
The coupled bordered mode is kept only for the **Eddy J-map**
(`/fem_eddy_field2d`, eddy=True + rotor_eddy=True): there the J view shows
eddy currents in the copper, magnets and shaft (a qualitative picture).

**Limitations (as with any 2-D transient, including Maxwell 2D):**
- resistance-limited (eddy reaction/skin effect in the magnet is not accounted for):
  skin depth at the slot frequency is approx 12 mm against a magnet width of approx 14 mm -> the estimate
  is slightly conservative (overestimates);
- the magnet is continuous along the axis (no axial segmentation): segmenting into N pieces
  reduces losses by approximately N^2 — outside the 2-D model.

The old slab estimate (`sigma·d^2/12·<dB/dt>^2` with the tangential width) overestimated by
~1.5x (64 W versus 43): the average B over the magnet times the width does not see the real
distribution (the losses are concentrated at the gap-side face). Slab remains the
fallback when the checkbox is off.

## 3. Shaft — slab with a skin cap and real sigma

The shaft sits deep inside the rotor iron and sees a small ripple. The estimate
`sigma·(d_eff^2/12)·<(dB/dt)^2>` with `d_eff = min(2r, 2*delta)` (skin cap) is the correct
order of magnitude for deep skin. **Critically**: sigma and mu_r are taken from the assigned
material. The old hardcoded constants (steel 4.5e6 S/m, mu_r=200) underestimated the
aluminum shaft's losses (2.58e7 S/m, mu_r=1) by ~200x: 0.24 W versus the real
approx 50 W (the coupled field solver gave approx 30 W — the slab is conservative by ~1.7x,
as expected). An aluminum shaft in a spoke machine is a known source of heating.

## 4. Copper — DC I^2R + proximity (stranded)

The winding is physically stranded — there is no bar-skin effect, so:
- DC: `rho_Cu(T)·J^2·V_cu·k_end` (temperature-dependent rho, end-windings via k_end);
- AC proximity: the classical estimate with the field split into radial and
  tangential components, each with its own strand cross-section.
The diagnostic Cu(solve) in the Eddy view (the full Integral(sigma*F^2) over the copper bars), after
a units fix (×L_stack) = 400.7 W ≈ the slot DC copper (P_cu/k_end ≈ 408 W) —
consistent; for a stranded winding, DC+proximity, not bar-skin, is
the physical model.

## 5. Validation (n=2, I=120 A rms, 466.7 Hz)

| Quantity | 24 steps | 72 steps | Comment |
|---|---|---|---|
| T_avg, N·m | 63.97 | 63.68 | = magnetostatics (losses don't distort torque) |
| P_fe, W | 137 | 183 | fit coefficients; 72 steps resolve the slot dB/dt |
| P_mag, W | 42.8 | 43.4 | **1.4 % convergence** |
| P_shaft, W | 47 | 54 | real aluminum sigma |
| time | 28 s | 70 s | = the slab path (+0 %) |

Mesh: values at mesh_size 4.0 and 3.0 mm are identical (the sliding-band path
clamps the iron to 2 mm and the gap to 0.1 mm independent of the requested size).

## 6. Controls

- UI: Simulation -> checkbox **"Field-based magnet/shaft losses (FEM eddy)"**
  (ON by default; stored in localStorage `sim.fieldLosses`; part of the
  Save button's staleness guard).
- API: `GET /api/simulation/physics/fem_transient?...&rotor_eddy=true|false`
  (part of the cache key).
- Save snapshots write `field_losses` into the parameters — in Compare, slab/field
  runs are distinguishable.
