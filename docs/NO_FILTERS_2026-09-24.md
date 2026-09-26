# No filters on reported values (2026-09-24)

Done by Claude Opus 5.5 (`claude-opus-5-5`) as one sub-agent, no escalation, on branch
`pre-migration-freeze-2026-09-15` on top of f0f5486. Owner's rule: «Убираем все фильтры —
считаем только реальную физику.» No smoothing, truncation, harmonic cap, amplitude floor,
detrending, windowing or anchor may shape a reported value; where a filter hid a numerical
defect, the defect is fixed instead.

Evidence harness (scratchpad `nf/`): `run_case.py` (saved duty, solver-direct), `regen.py`
(physics-regression case), `drive.py` (parallel jobs, variants = worktrees/snapshots with the
module path asserted in every result), `tab.py`. Results `nf/res/*.json`, logs `nf/logs/`.
Variants: **base** = 68de0ca worktree, **head** = f0f5486 worktree, **new** = snapshots of this
tree (`s1` items 1-4/6, `s5` all items).

## Summary

| # | filter / defect | what it hid | fix | client-visible effect |
|---|---|---|---|---|
| 1 | `honest_rotor_eddy` k ≤ 16 cap + 5e-4 amplitude floor, on the OPEN one-period rotor window | open-window leakage (grew with steps) | commensurate rotor window on the nodal A (pole-pair images, exact, no solve); every bin to Nyquist | `P_mag_honest_W` / `P_shaft_honest_W` (reported magnet/shaft loss when rotor eddy runs without the coupled solve; cross-check otherwise) now converge with steps |
| 2 | `_spectral_ddt_series` (dψ/dt cut above the 2nd slot harmonic) | — | Crank–Nicolson step voltage R·ī + Δψ/Δt (the circuit row the voltage drive solves) | V_peak −0.2 %, THD_LL −0.07 pp at 36 steps; ⟨v·i⟩ balances T·ω + solved losses to O(Δt²) |
| 3 | `_smooth_demag_H` (nodal smoothing of H in each magnet) | mesh bias of the corner elements | removed; element-mean H of the solved field | Br kept −0.3 to −0.7 pp at default mesh, converges with the magnet mesh to the same limit the smoothed code reaches only when refined |
| 4 | `HARMONIC_FLOOR_T` 1 mT (and `B_FLOOR_T` 1 µT) in the measured-surface core loss | — | every nonzero amplitude billed | < 1e-3 W (identical to 3 decimals on L155/Ø40) |
| 5 | voltage-drive anchors (Aitken Δ², period-mean DC) | settling | Aitken: proven inert, kept. PWM DC anchor: **stopped** — biases a short-τ machine (1.1 A DC left, P_cu −0.9 %), needed on the L155; options in §5 | unchanged |
| 6 | inventory | | THD 25th-harmonic cap, delta circulating h ∈ {3, 9, 15}, two inert clamps removed | THD now to Nyquist; P_cu_circulating over every zero-sequence bin |
| 7 | eddy settling | 3-sample probe verdict, per-splice kicks, cold start from A = 0 | whole-period per-body gauge, exact pole-pair remap at every splice, static cold start | L155 rated P_shaft 29.1 → 7.2 W (and flagged when capped), L13 no-demag 0.83 → 0.41 W |
| 8 | optimizer E | | `OPT_FINAL_WARM_START` default ON; winner validation requires `eddy_settled` | — |

## 1. Honest rotor eddy: commensurate window, no cap, no floor

**Defect.** `honest_rotor_eddy` takes the DFT of every rotor boundary node's A(t). A rotor node
slides a non-integer number of slot pitches per electrical period (12/5 on 12s/10p, 12/7 on
12s/14p), so its one-period history is OPEN and the DFT bills the end-to-start step as broadband
content up to Nyquist — exactly the rotor-iron leakage of the held-item fix. The k ≤ 16 cap
("measured necessary", 2.13 vs 2.35 W) was hiding that leakage.

**Fix.** `rotor_window.commensurate_rotor_potential_window` chains the solved window with its
q − 1 pole-pair images on the rotor NODES (A_z is a scalar under the in-plane rotation: image
value × sector sign s^f; nodes on the two sector boundaries may share an image). The honest
solve then takes every bin up to Nyquist (Nyquist bin of an even window at |C|, a bin skipped
only when its whole drive is exactly zero). Node match on the real meshes: 5.8e-18 m against a
1.7e-11 m tolerance. `P_rotor_eddy_honest_window` reports method, q, frames, harmonics, wall.
Cost: 1-6 s per run (30-504 harmonics).

**Convergence** (rotor eddy WITHOUT the coupled solve, so the honest numbers are the reported
magnet/shaft loss; current drive, rated point, no demag):

| steps | L155 P_mag head (open, k≤16) | L155 P_mag new | L155 P_shaft head | L155 P_shaft new | Ø40 P_mag head | Ø40 P_mag new | Ø40 P_shaft head | Ø40 P_shaft new |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 12 | 78.39 | 73.56 | 93.11 | 96.43 | 2.726 | 1.817 | 0.141 | 0.099 |
| 36 | 84.07 | 73.45 | 91.13 | 94.42 | 3.770 | 1.900 | 0.147 | 0.111 |
| 72 | 80.82 | 73.43 | 90.00 | 94.37 | 3.528 | 1.917 | 0.143 | 0.113 |
| 144 (L155 snaps to 108) | – | 73.44 | – | 94.39 | – | 1.922 | – | 0.113 |

The new value converges (L155 magnets −0.15 %, −0.03 %, +0.01 %; Ø40 +4.6 %, +0.9 %, +0.3 %);
the capped open window wanders (Ø40 +38 %, −6 %). Ø40 moves −50 % at 36 steps: that is how much
of the old number was leakage. With the coupled solve on (the owner's duties) the honest value
is only the cross-check beside the reported coupled σE²; L155 rated 71.16 → 61.1 W, Ø40
3.675 → 1.832 W (coupled: 85.5 W, 3.06 W — the two routes are different models, see
`HONEST_EDDY_SOLVER.md`).

## 2. Terminal voltage: the Crank–Nicolson step voltage

**Old.** V = R·i + spectral derivative of ψ, truncated above the 2nd slot harmonic (a filter),
paired with frame currents in ⟨v·i⟩.

**New.** `fem_solver_2d._step_voltage_series`: for the step (t_{k−1}, t_k]

    v_{k−½} = R·(i_k + i_{k−1})/2 + (ψ_k − ψ_{k−1})/Δt_k

— the circuit row the voltage drive SOLVES (`drive.circuit_residual_ll`), i.e. on a voltage run
the applied voltage itself, and on a current run the voltage a CN drive would apply to reproduce
the solved currents. Δψ/Δt is the exact step-mean EMF (Faraday). The first step is measured
against the frame actually solved before the window (settling prefix / eddy warm-up), or closed
periodically on an imposed-current window. ⟨v·i⟩ pairs each step voltage with the step-mean
current (a lossless inductor exchanges exactly zero energy over a period). Each sample sits at its
step MIDPOINT: `V_rotor_angle_deg`, used by `postproc.complex_fundamental` / the voltage seed so
phasors are not shifted by half a step. New result `power_balance`: P_in = R·⟨ī²⟩ + field input,
field input vs T_mean·ω + the field's own solved eddy losses (the post-processed iron loss is
outside the field).

**Convergence** (L155 / Ø40, current drive, rated, magnetostatic + honest rotor eddy):

| steps | L155 V_peak head | new | THD_LL head | new | P_in new [kW] | balance residual new | Ø40 V_peak head | new | Ø40 balance new |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 12 | 423.29 | 408.54 | 1.97 | 1.47 | 266.48 | −4.71 % | 11.249 | 10.782 | −4.47 % |
| 36 | 423.88 | 424.20 | 2.51 | 2.42 | 277.61 | −0.51 % | 11.048 | 11.043 | −0.48 % |
| 72 | 424.67 | 424.40 | 2.50 | 2.48 | 278.67 | −0.13 % | 11.075 | 11.076 | −0.12 % |
| 108/144 | – | 425.04 | – | 2.49 | 278.87 | −0.056 % | – | 11.080 | −0.03 % |

The residual falls as Δt² (×9.3, ×4.0) — it is the CN discretisation's, not a model gap, and
P_in − R·ī² converges onto T_mean·ω. The old spectral P_in (279.02 kW at 36 steps) was already
near its limit because a spectral derivative of a smooth series is spectrally accurate; the CN
value is the scheme's own and is 0.5 % low at 36 steps (it is a diagnostic: efficiency is
T·ω / (T·ω + losses), never P_in). THD: every harmonic to Nyquist (§6).

## 3. Demag: element-level H, no smoothing

`field_ops._smooth_demag_H` (area-weighted nodal averaging of H inside each magnet before the
ratchet) is deleted; `demag.MagnetDemag.update` judges each element on its own mean H (the P2
caller integrates B over the element's quadrature, so this is the exact element average).
Convergence with the MAGNET element size (current drive, demag, no eddy, rated point):

| machine | magnet mesh | rotor cell tris | Br kept vol % new | area de-rated % new | Br worst % new | T [N·m] new | Br kept head (smoothed) | T head |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Ø40 L12 | default | 348 | 99.303 | 3.31 | 11.7 | 0.621306 | 99.577 | 0.622676 |
| | 0.2 mm | 1506 | 99.160 | 4.23 | 11.7 | 0.620659 | | |
| | 0.1 mm | | 99.145 | 4.14 | 11.7 | 0.620467 | | |
| | 0.07 mm | | 99.146 | 4.07 | 11.7 | 0.620415 | 99.189 | 0.620529 |
| L13 (24s/28p) | default | 364 | 98.874 | 3.17 | 11.7 | 5.385425 | 99.545 | 5.435525 |
| | 0.5 mm | 684 | 98.722 | 4.78 | 11.7 | 5.371666 | | |
| | 0.25 mm | | 98.658 | 4.80 | 11.7 | 5.366769 | | |
| | 0.15 mm | | 98.648 | 4.90 | 11.7 | 5.365762 | 98.691 | 5.368855 |
| L155 | default | 1126 | 99.211 | 99.5 | 98.0 | 186.13204 | 99.212 | 186.13716 |
| | 1.0 mm | 3344 | 99.208 | 99.11 | 93.3 | 186.13007 | | |
| | 0.5 mm | | 99.204 | 99.21 | 78.6 | 186.13005 | | |
| | 0.35 mm (whole mesh re-built) | | 99.194 | 100.0 | 98.3 | 186.23571 | 99.193 | 186.23644 |

- **Converged quantities** (Br kept by volume, torque): the unsmoothed element-level rule converges
  (Ø40 −0.14, −0.015, +0.001 pp; L13 −0.15, −0.064, −0.010 pp) to the limit the smoothed code
  approaches only when refined (Ø40 at 0.07 mm: 99.146 new vs 99.189 smoothed; L13 at 0.15 mm:
  98.648 vs 98.691; the smoothing
  stencil shrinks with the mesh, so its bias does). At the DEFAULT mesh the new number is the
  closer one: Ø40 0.16 pp from the fine limit against 0.43 pp smoothed; L13 0.23 against 0.90 pp.
  The torque follows (L13 default 5.3854 new vs 5.4355 smoothed vs ~5.366 converged).
- **Br worst element** is a singularity gauge, not a converging quantity, with or without
  smoothing: on Ø40/L13 it sits on the material curve's floor (11.7 %) at every mesh; on L155 it
  depends on which element touches the sharp corner (98.0, 93.3, 78.6 %). Like the unaveraged
  stress in the mechanical module, it should be read as "a corner is at the knee", not as a
  magnet-level number. Reported unchanged (`br_worst_pct`), no filter applied to it.

## 4. Measured-surface amplitude floors

`losses.HARMONIC_FLOOR_T` (1 mT everywhere in a half) and the 1 µT per-element `B_FLOOR_T` in
`core_loss_surface.w_per_m3` are removed: every nonzero amplitude is billed (below the lowest
measured point the curve continues as its measured power law; B = 0 is the only point excluded,
its limit is zero loss). Effect, head vs new on identical fields (eddy off, no demag):
L155 36 steps 1437.891 → 1437.891 W, 72 steps 1446.449 → 1446.450 W; Ø40 36 steps 9.202 → 9.202 W —
below 1e-3 W, as the floor's own comment predicted (1e-6 of the loss). Cost: the per-harmonic
surface evaluation now covers every bin (below timing noise). The round-off lines above the
table's frequencies put ~1e-30 of the watts in the extrapolation; the share is reported exactly
(`envelope_out_frac`), only the log level treats < 1e-6 as "inside".

## 5. Voltage-drive anchors (settling accelerators)

Both act on the circuit STATE during the discarded settling prefix only.

- **Sine drive, Δ² flux anchor (`aitken`).** Ø40 sine voltage: it fired 0 times at the default
  4 settle periods and 0 times at 40 (`v_anchor_applied` 0). Default vs anchor off: identical to
  every printed digit (T 0.933492, ripple 7.126 %, P_cu 265.92 W, I1 136.093 A). It is inert here.
  The default 4-period settle (f83e60f) itself is not fully settled on ripple: 40 periods give
  T 0.933453 (−0.004 %), ripple 7.202 % (+1.1 %), I1 136.089 A, DC residual 0.004 A — a settle-length
  question, not an anchor one; noted for the owner.
  **Verdict: kept** — proven identical with and without it, at the default and at a 10× settle.
- **PWM, period-mean DC anchor (`dc_anchor`). STOPPED — not proven, evidence and options for the
  owner; code unchanged.** Ø40 PWM (30 V bus, 16.68 kHz, 88 steps = 8 per carrier, mixed
  schedule, 5 adaptive settle periods) against 40 settle periods:

  | run | anchors fired | DC left in the window [A] | T [N·m] | P_cu [W] | I rms [A] | ripple pp [N·m] |
  |---|---:|---:|---:|---:|---:|---:|
  | anchor on, 5 periods (default) | 5 | 1.10 | 0.934493 | 306.09 | 98.164 | 0.29448 |
  | anchor on, 40 periods | 40 | 1.19 | 0.934373 | 305.86 | 98.129 | 0.29506 |
  | anchor off, 5 periods | 0 | 0.025 | 0.935841 | 308.87 | 98.595 | 0.28910 |
  | anchor off, 40 periods | 0 | 0.025 | 0.935841 | 308.87 | 98.595 | 0.28910 |

  Without the anchor this machine is settled at 5 periods (5 and 40 agree to every digit). With
  it the reported window opens on the anchor's own last correction, which leaves 1.1-1.2 A of DC
  (above the solver's own 0.5 A acceptance) and moves T −0.14 %, P_cu −0.9 %, I_rms −0.44 %,
  however long the settle — the period-mean it measures at 8 steps per carrier carries aliased
  carrier ripple, and it is applied at the last settle boundary with no time left to decay. So on
  a short-τ_e machine it is a bias, not an accelerator. On the L155 (τ_e ≈ 27 electrical periods)
  it is what removes a 43 A DC that 12 settle periods cannot shed (PWM study B5), so removing it
  there leaves the DC in the window. It therefore cannot simply be removed, and it cannot be kept
  as "identical". Options: (a) remove it and settle from τ_e (≈ 5·τ_e/T_e periods on the coarse
  schedule — L155 PWM ~135 coarse periods, hours); (b) keep it only while ≥ several τ_e remain
  before the window (L155: never enough), otherwise off; (c) replace it with an exact
  periodic-orbit solve of the circuit DC mode (two extra periods probing the period map of the DC
  state, then the 2×2 linear solve, verified by a following period) — the honest accelerator,
  measured against a long no-anchor reference on the L155 before it ships. The Ø40 numbers above
  are the first half of that proof.

## 6. Filter-like operations on the reported path — inventory

Searched: `simulation/*` (static3d/mechanical excluded as not on the EM report path),
`sb_postproc.py`, `losses.py`, `field_ops.py`, `postproc.py`, `routes/*`, `report.py`,
`passport_pwm.py`, `datasheet.py`, `duty_results.py` for savgol, smooth, filter, window, taper,
detrend, clip, floor, cap, truncat, lowpass, convolve, median, rfft/irfft with zeroed bins.

| where | what | verdict |
|---|---|---|
| `eddy_solver_2d.honest_rotor_eddy` / `region_eddy_from_history` k ≤ 16 cap, 5e-4 floor | truncation + amplitude floor | **removed** (item 1) |
| `fem_solver_2d._spectral_ddt_series` | dψ/dt truncated above 2nd slot harmonic | **removed** (item 2) |
| `field_ops._smooth_demag_H` | nodal smoothing of H | **removed** (item 3) |
| `losses.HARMONIC_FLOOR_T`, `core_loss_surface.B_FLOOR_T` in `w_per_m3` | amplitude floors | **removed** (item 4) |
| `postproc.voltage_harmonics` / `current_harmonics` `h_max=25` | THD / THD_LL / THD_I stopped at the 25th harmonic | **removed**: every order to Nyquist (Nyquist bin at \|X\|/N); `h_max` kept only as an explicit band argument; `passport_pwm` note updated |
| `fem_solver_2d` delta zero-sequence loss `for _h in (3, 9, 15)` | harmonic list | **removed**: every bin of the zero-sequence flux (bin m = harmonic m/n_periods) |
| `fem_solver_2d` `np.maximum(_P_fe_t, 0)` | clamp | **removed** (inert: every term ≥ 0) |
| `losses.proximity_loss_series` default `post = max(·, 0)` | clamp | **removed** (inert: sum of squares) |
| `eddy_settle_resid` block trend on 3 probe samples | a gauge that decided "settled" | **replaced** by `eddy_period_resid` (item 7); kept only for a voltage prefix shorter than 2 periods |
| `losses.harmonic_amplitudes` legacy ramp (`WRAP_TAPER`) | detrend | not selected: diagnostic comparator on OPEN windows only (held-item fix), never feeds a value |
| `P_fe_rotor_open_window_diagnostic`, `P_fe_detrended_candidate_avg_W` | open/legacy windows | diagnostics, `selected: False` |
| `field_ops.band_limit_torque`, `torque_metrics` | "band limit" | not a filter: returns the raw series (deprecated alias) |
| `losses.declip`, `angle_ddt.py` (savgol, wrap-detrend) | clip / smoother | dead code (P1 retired), called nowhere |
| `demag` `np.clip(Br/Br0, 0, 1)`, `np.minimum(cur, new)` | bounds | physics: a de-rating factor in [0, 1], irreversible (monotone) |
| `fem_solver_2d` eddy-body check `np.median` | area check | a guard that refuses a wrong winding, not a value |
| `losses.loss_density_map` stray-element zeroing | index-bug guard | map only, logged as an error, never physics |
| `loss_density_map` normalisation to reported watts | shape × total | the picture, not the watts; the shape is the raw model |
| `core_loss_surface.outsideness` `max(B, B_FLOOR_T)` (now 1e-300) | log() guard | decides nothing (above-envelope test only) |
| voltage anchors (Aitken, period-mean DC) | state accelerators | item 5 |
| `routes/*`, `report.py` (torque spectrum rfft), `datasheet.py` | — | no filter found; report spectra are raw rfft |

## 7. Eddy settling in standard runs

**Finding: the owner's standard runs were NOT settled although they reported `eddy_settled`
True.** Cold runs on current code (head), against the same duty marched 5 continuous periods
(`SB_EDDY_WARM`), reported window:

| duty | P_shaft default → 5 periods | other movers | old verdict |
|---|---|---|---|
| L155 rated (Δ, eddy+demag) | 29.06 → 14.99 W | P_mag 84.94 → 85.25, T −0.02 % | settled 0.8 % |
| L13 rated | 0.454 → 0.441 W | | settled 0.74 % (after 3 frames) |
| L13 rated, demag off | 0.826 → 0.411 W | P_cu_ac 2.379 → 2.391 | settled 0.74 % (after 3 frames) |
| Ø40 L12 rated | 0.020 → 0.013 W | | settled 1.3 % (after 3 frames) |
| Ø40 L12, demag off | 0.044 → 0.013 W | P_cu_ac 4.368 → 4.424 (+1.3 %) | settled 1.3 % (after 3 frames) |
| L13 peak | 1.741 → 1.739 W | | settled |

Three defects, each fixed without a filter:

1. **The gauge.** Three probe samples, or block means over half a period of the magnet+shaft SUM,
   cannot see a slow body behind a fast dominant one (L155: 3.9 kW of magnets, 15-30 W of shaft).
   New `sb_postproc.eddy_period_resid`: means over WHOLE electrical periods (on the periodic orbit
   every period has the same mean, so ripple, slotting and synchronous carriers cancel exactly),
   PER conductor group (magnets, shaft, sleeve, copper), each against its own level floored at
   1e-3 of the total; geometric tail from the last two period ratios (four periods), a 0.9 ratio
   assumed with fewer. A probe can pass only on a seed's same-angle reference (now per group).
2. **The splices.** Every extension and the demag pre-pass jumped the rotor back one electrical
   period and kept the per-dof eddy history — the history of a different rotor position, a fresh
   kick per splice. Now `rotor_window.period_shift_map` carries the rotor-dof state across the
   period exactly (state(θ − Θe) at dof y = s^f · state(θ) at its pole-pair image), so the march is
   continuous in time; extensions repeat period by period up to `SB_EDDY_MAX_PERIODS` (16), then
   the run says `eddy_capped`. Proof of exactness: L13 demag-off, 3 remapped periods vs 5
   continuous: P_shaft 0.4107 vs 0.411 W, T 5.491091 vs 5.491087, every other value identical.
3. **The cold start.** A cold march started from A = 0, switching the whole field on in one Δt.
   `p2_drive.eddy_static_state` now starts it from the ∂A/∂t = 0 field one step before the first
   frame (uniform wire currents, no current in floating bodies) — the DC flux is in place as on the
   orbit. `eddy_cold_start` in the result says so. The verdict is reported in `eddy_settle_gauge`
   (method, whole periods, per-group residuals, extension periods, whether the remap applied).

Results, cold standard runs (all items), reported window:

| duty | P_shaft old → new [W] | frames solved old → new | verdict new |
|---|---|---|---|
| L155 rated | 28.98 → 5.69 | 111 → 650 | **capped** at 16 extension periods, residual 45 % (shaft) |
| L13 rated | 0.436 → 0.441 | 83 → 242 | settled (1.2 %), 4 periods + pre-pass |
| L13 peak | 1.725 → 1.734 | 83 → 282 | settled (0.2 %) |
| Ø40 L12 rated | 0.020 → 0.013 | 75 → 254 | settled (0.7 %) |
| 30 mm fixture p2_eddy | 0.013 → 0.015 | | settled, 4 periods |

The new L13 value equals the 5-continuous-period reference to 3 digits (0.441), so the three
fixes reproduce a long continuous march at a fraction of its length.

**The L155 shaft.** It carries a slow mode. From the static start its period means run
28.6, 14.7, 11.0, 8.7, 7.6, 7.4, 7.6, 7.2, 6.5, 6.1, 6.1, 6.4, 6.2, 5.8, 5.5, 5.6 W (periods
1-16): a decay modulated with a period of ~5 electrical periods — the model's rotor period
(q = 5 on 12s/10p): a transient pattern bound to the rotor dissipates differently as it passes
the slots, so it returns only after the rotor does. Peak to peak over 5 periods the decay is
×0.84-0.88, τ ≈ 30 electrical periods ≈ 25 ms. No warm-up within the cap settles it to 2 %; the run
is reported capped (`eddy_settled` False, `eddy_capped` True) with the value at the cap. The
previous 29 W was mostly start-up transient and was labelled settled. (A one-step flattening at
periods 5-6, −0.14 W, is what made the first version of the gauge read "settled"; the gauge now
needs two consecutive small changes, see `eddy_period_resid`.)

## 8. Optimizer E on; validation requires a settled eddy result

- `routes/optimization._FINAL_WARM_START_DEFAULT = True` (`OPT_FINAL_WARM_START=0` still turns it
  off).
- `_standard_quality` requires `eddy_settled is True` (refine_proc stamps True when no eddy march
  ran), like the Sweep Apply check and the on-demand re-check; an unsettled cold baseline A now
  fails the validation closed.
- Legacy unpinned optimizer runs keep being re-checked on the current machine with
  `provenance: "legacy_unpinned"` (unchanged, test kept).
- Note: with item 7 a seeded final solve no longer passes on the 3-sample trend; it passes on the
  per-group same-angle reference (same operating point) or after whole periods — E's saving is
  smaller than measured in `OPTIMIZER_ITEMS_DE_2026-09-24.md`, the answer is settled.

## A/B against 68de0ca

Solver-direct, saved duties (`run_case.py`), cold (`SB_NO_WARM_CACHE=1`), base = 68de0ca
worktree, head = f0f5486 worktree, new = this tree (snapshot `s5`); `motor_ai_sim.__file__`
asserted in every result. Walls were taken on a box running 8-10 jobs at once and are only
indicative; solo timings below.

| quantity | L155 rated base → new | Ø40 L12 rated | Ø40 sine voltage | L13 rated | L13 peak |
|---|---|---|---|---|---|
| T mean [N·m] | 187.8547 → 187.7744 (−0.04 %) | 0.623364 → 0.621992 (−0.22 %) | 0.933469 → 0.933492 | 5.446473 → 5.395695 (**−0.93 %**) | 9.412527 → 9.384404 (−0.30 %) |
| ripple pp [N·m] | 2.6798 → 2.6504 (−1.1 %) | 0.03800 → 0.03673 (−3.4 %) | 0.06855 → 0.06652 (−3.0 %) | 0.29388 → 0.28429 (−3.3 %) | 0.61662 → 0.60410 (−2.0 %) |
| V_peak [V] | 434.64 → 433.85 (−0.18 %) | 10.999 → 11.002 | 17.123 → 17.156 (+0.19 %) | 16.019 → 15.894 (−0.78 %) | 22.294 → 22.228 (−0.30 %) |
| V_LL peak [V] | 403.5 → 402.6 | 18.5 → 18.3 (−1.1 %) | 21.7 → 21.6 | 24.8 → 24.7 | 32.3 → 32.1 (−0.6 %) |
| THD / THD_LL [%] | 9.08/2.00 → 9.02/1.94 | 15.28/4.38 → 15.06/4.21 | 38.45/0.10 → 38.06/0.00 | 14.83/4.05 → 14.53/3.80 | 23.61/5.29 → 23.35/5.09 |
| P_fe stator / rotor [W] | 1390.73/56.88 → 1389.78/53.77 | 8.461/0.839 → 8.437/0.712 | 11.182/1.122 → 11.182/0.947 | 2.292/0.218 → 2.265/0.207 | 2.551/0.284 → 2.545/0.269 |
| P_mag reported [W] | 84.94 → 85.56 (+0.73 %) | 3.081 → 3.060 (−0.7 %) | – | 0.438 → 0.439 | 0.880 → 0.884 |
| P_mag honest [W] | 71.16 → 61.19 (−14 %) | 3.675 → 1.832 (−50 %) | – | 0.587 → 0.293 (−50 %) | 1.342 → 0.663 (−51 %) |
| P_shaft reported [W] | 28.98 → 5.69 (**−80 %**, capped) | 0.020 → 0.013 | – | 0.436 → 0.441 (+1.2 %) | 1.725 → 1.734 |
| P_cu AC solved [W] | 599.52 → 600.67 (+0.19 %) | 4.381 → 4.347 (−0.8 %) | – | 2.346 → 2.273 (−3.1 %) | 3.096 → 3.075 (−0.7 %) |
| Br kept / worst el. [%] | 99.215/98.4 → 99.214/97.9 | 99.577/13.5 → 99.303/11.7 | – | 99.540/11.7 → 98.860/11.7 | 99.934/11.7 → 99.772/16.9 |
| η (solver, T·ω / (T·ω + losses)) | 0.98640 → 0.98648 | 0.92696 → 0.92704 | 0.81895 → 0.81906 | 0.74041 → 0.73870 (−0.23 %) | 0.58946 → 0.58875 |
| P_in ⟨v·i⟩ [W] | 280174 → 278635 (−0.55 %) | 897.7 → 891.3 (−0.7 %) | 1519.2 → 1510.6 (−0.6 %) | 763.4 → 754.8 (−1.1 %) | 1659.8 → 1648.8 (−0.7 %) |
| frames solved | 111 → 650 | 75 → 254 | 396 → 180 | 83 → 242 | 83 → 282 |
| eddy settled (residual) | True (0.8 %) → **False (45 %, capped)** | True (1.3 %) → True (0.7 %) | – | True (0.7 %) → True (1.2 %) | True (1.4 %) → True (0.2 %) |

Every client-visible move above 0.5 %, with its cause (head = f0f5486 isolates this change from
the earlier held-item fixes; base → head moves are 19b3b35/f83e60f, see
`SOLVER_HELD_ITEMS_FIX_2026-09-24.md`):

- **T −0.93 % L13 rated, −0.30 % L13 peak, −0.22 % Ø40** — item 3. Br kept 99.54 → 98.86 % on
  L13: the corner elements are judged on their own solved H instead of a neighbour average.
  The mesh study (§3) shows the new default-mesh value is the closer one to the converged limit
  (L13 converged ≈ 5.366 N·m at the finest magnet mesh; smoothed default 5.4355).
- **Ripple −1 to −3.4 %** — item 3 (a weaker corner changes the cogging/ripple spectrum; Ø40 no-
  demag runs keep their ripple) and on L155 item 7 (a settled eddy field); Ø40 voltage −3 % is
  f83e60f (base → head).
- **V_peak −0.78 % L13** — item 3 (the weaker magnet); the voltage estimator itself moves V_peak
  ≤ 0.2 % at 36-40 steps (item 2). **V_LL −1.1 % Ø40**: the line peak of step voltages (item 2),
  0.2 V on a 1-decimal display.
- **THD / THD_LL −1 to −6 %** (≤ 0.3 pp absolute) — item 2: each harmonic is the step mean,
  i.e. sinc-weighted and converging from below (§2); plus item 3 on L13.
- **P_fe rotor** −5 to −15 % against base is the held-item commensurate window (base → head);
  head → new ≤ +3 % (L155 +1.7 W: the settled rotor eddy field of item 7). Stator −1.2 % on L13 —
  item 3 (lower Br).
- **P_mag honest −14 to −51 %** — item 1: that was the open-window leakage (§1).
- **P_mag reported +0.73 % L155, P_shaft −80 % L155 / +1.2 % L13 / −35 % Ø40** — item 7: the
  old values carried start-up transient (§7); L155 is now reported capped.
- **P_cu AC −3.1 % L13, −0.8 % Ø40** — item 3 (lower Br → less slot leakage).
- **η −0.23 % L13** — the lower torque (item 3).
- **P_in −0.55 to −1.1 %** — item 2: the CN step power converges from below as Δt² (§2); a
  diagnostic, never in η.
- **Frames**: item 7 — whole-period verdicts (≥ 4 continuous periods) and, on L155, the cap.

Solo timings (one job on the box, 4 BLAS threads, cold; numbers identical to the loaded runs):

| duty | base 68de0ca | new | why |
|---|---:|---:|---|
| Ø40 L12 rated | 103 s (75 frames) | 167 s (254 frames) | ≥ 4 continuous whole periods before the verdict (item 7) |
| L155 rated | 141 s (111 frames) | 534 s (650 frames) | the shaft's slow mode runs the march to the 16-period cap (item 7, open decision 2) |

The honest rotor eddy solve (item 1) costs 2-6 s of that; items 2-4 are below timing noise.

## Physics-regression re-pin

`tests/physics_baseline.json` re-pinned in one commit (30 mm fixture, 12 steps). Split by item
with three extra variants of each case: `s1` (items 1-4, 6), `s1sm` (the same with the demag
smoothing restored) and the final tree. Lines moving beyond the suite's 0.5 % tolerance:

| case | metric | pin → new | items 1/2/4 | item 3 | item 7 |
|---|---|---|---:|---:|---:|
| p2_load | V_peak | 7.8908 → 7.3488 (−6.87 %) | −6.87 % (2) | – | 0 |
| p2_noload | V_peak | 5.3944 → 5.2650 (−2.40 %) | −2.40 % (2) | – | 0 |
| p2_voltage | V_peak | 8.1007 → 8.5626 (+5.70 %) | +5.70 % (2) | – | 0 |
| p2_voltage_eddy | V_peak | 8.0977 → 8.5573 (+5.68 %) | +5.68 % (2) | – | 0 |
| p2_voltage_eddy_rotor | V_peak | 8.0985 → 8.5557 (+5.65 %) | +5.65 % (2) | – | 0 |
| | P_mag_honest_W | 1.739 → 1.100 (−36.8 %) | −36.8 % (1) | – | 0 |
| | P_shaft_honest_W | 0.385 → 0.300 (−22.1 %) | −22.1 % (1) | – | 0 |
| p2_eddy | V_peak | 7.9017 → 7.3387 (−7.12 %) | −7.12 % (2) | – | 0 |
| | P_mag_honest_W | 1.643 → 1.046 (−36.3 %) | −36.3 % (1) | – | 0 |
| | P_shaft_honest_W | 0.566 → 0.502 (−11.3 %) | −11.3 % (1) | – | 0 |
| | P_cu_ac_solve_W | 3.274 → 3.291 (+0.52 %) | 0 | – | +0.52 % |
| | P_shaft_solve_W | 0.013 → 0.015 (inside its 0.05 W floor) | 0 | – | +15 % |
| p2_demag | T_avg_Nm | 0.395095 → 0.390640 (−1.13 %) | 0 | −1.13 % | 0 |
| | T_ripple_pct | 0.597 → 0.804 (+34.6 %) | 0 | +34.6 % | 0 |
| | V_peak | 7.7497 → 7.2895 (−5.94 %) | −5.63 % (2) | −0.33 % | 0 |
| | P_fe_W | 4.4560 → 4.4111 (−1.01 %) | 0 | −1.01 % | 0 |
| | demag_br_mean / min | 0.8280 / 0.1390 → 0.8062 / 0.1235 | 0 | −2.6 % / −11.2 % | 0 |
| p2_demag_eddy | T_avg_Nm | 0.395946 → 0.391478 (−1.13 %) | 0 | −1.12 % | 0 |
| | T_ripple_pct | 0.606 → 0.796 (+31.5 %) | 0 | +26.8 % | +3.7 % |
| | V_peak | 7.7611 → 7.2809 (−6.19 %) | −5.88 % (2) | −0.32 % | 0 |
| | P_mag_honest / P_shaft_honest | 1.560 / 0.562 → 0.982 / 0.493 | −36 % / −11 % (1) | −1.4 % / −1.0 % | 0 |
| | P_mag_solve_W / P_cu_ac_solve_W | 1.434 / 3.086 → 1.423 / 3.007 | 0 | −0.8 % / −2.5 % | 0 / 0 |
| | P_fe_W | 4.4590 → 4.4139 (−1.01 %) | 0 | −1.01 % | 0 |
| | demag_br_mean / min | 0.8271 / 0.1383 → 0.8051 / 0.1226 | 0 | −2.7 % / −11.4 % | 0 |

- Item 4 moved nothing beyond 1e-5 relative; items 5, 6 (THD, circulating loss) and 8 are not
  pinned here.
- V_peak moves by −7 % / +5.7 % at the fixture's 12 steps: the step voltage at 30° steps
  (sinc(π/12) on the fundamental, more on the harmonics that make the peak). On current runs the
  old spectral estimator read the peak of an interpolant between frames; on voltage runs the new
  value is the voltage the CN circuit actually applied to the winding, which the old estimator
  under-read by 5.7 %. At 36+ steps the two agree within 0.2 % (§2).
- The demag ripple +27-35 % is the 12-frame ratchet sampling (the corner elements are now judged
  on their own field; `test_demag_reproducible` documents the sampling spread, 0.36 % smoothed →
  0.64 % now at 12 frames).

## Commits and verification

| commit | content |
|---|---|
| 719a849 | item 8: E default on, `_standard_quality` requires `eddy_settled` |
| 3045465 | items 1-4, 6, 7 (solver) + tests |
| 715ff9a | physics-regression re-pin, per-item split in the message |
| (this doc) | docs |

- Targeted tests (touched modules, eddy/strand/wire/voltage/core-loss/demag, optimizer
  `test_optimizer_items_de`, `test_optimizer_final_validation`, `test_scan_standard_validation`,
  new `test_eddy_period_gauge`): 363 + 7 passed; then `test_physics_regression` (re-pinned),
  `test_demag_reproducible`, `test_wire_parallel`, `test_solver_guards`: 50 passed.
- Tests whose expectations changed, and why: `test_harmonic_eddy_cache` (oracle without cap and
  floor), `test_surface_loss_candidates` (no floor; the "sub-floor" test is now "small amplitudes
  are billed"), `test_core_loss_surface` (round-off lines logged quietly), `test_wire_parallel`
  loaded V ratios at 6 steps (CN step voltage, 3 → 7 % band; no-load exact), 
  `test_demag_reproducible` per-magnet spread 0.5 → 0.8 % (12-frame sampling, unsmoothed),
  `test_solver_guards` (the warm-up group lists), `test_physics_regression` (zero-start knob for
  the transient reproduction).
- No push, no deploy, no API restart. Worktrees removed after the runs.

## Open decisions for the owner

1. **PWM DC anchor** (§5): remove + settle from τ_e, gate it, or replace it with an exact
   periodic-orbit solve of the circuit DC mode. Nothing was changed; today it biases short-τ
   machines (Ø40: 1.1 A DC left, P_cu −0.9 %).
2. **Cost of honest settling.** Cold coupled-eddy runs now march ≥ 4 continuous periods plus the
   pre-pass (Ø40 75 → 254 frames, L13 83 → 242). The L155 shaft (τ ≈ 30 electrical periods) hits
   the 16-period cap (111 → 650 frames) and is reported NOT settled. `SB_EDDY_MAX_PERIODS` sets
   the cap; lower is faster and less settled, still flagged. A seeded rerun at the same point
   continues the state and passes on the per-group same-angle reference. Whether the slow L155
   shaft mode is physical (a 2-D floating body with ∫J = 0 in a magnetic, conducting shaft) or a
   2-D artefact is worth a separate look.
3. **Sine-voltage default settle (4 periods, f83e60f)** leaves 1 % on the ripple (7.126 → 7.202 %
   at 40 periods; T −0.004 %). *Addressed 2026-09-26: the fixed count is replaced by a
   period-to-period convergence criterion with a cap (`voltage_settle` in the result) — see
   `docs/BR_CORNER_AND_SINE_SETTLE_2026-09-26.md`.*
4. **Br worst element** is a corner-singularity gauge (§3); consider presenting it like the
   unaveraged stress (a flag, not a magnet-level number). *Addressed 2026-09-26:
   `demag_summary.br_corner` (value + location, flagged); the report, datasheet and web summary
   show it as a corner diagnostic with no limit — same doc.*
5. **Optimizer E** is on; with the honest gauge a final solve that continues an optimization
   state still needs whole periods unless the seed is at the same operating point, so E's measured
   saving (−12 %) shrinks. The validation is now correct (settled) rather than fast.
