# Held solver items 909b014 / c582449 / a88ae64 / 8997bb3 — fixes (2026-09-24)

Done by Claude Opus 5.5 (`claude-opus-5-5`), one sub-agent, no escalation. It works on branch
`pre-migration-freeze-2026-09-15`, on top of dd54235. The review items come from
`coordination/codex-review-2026-09-24.md` and `docs/OPTIMIZER_SPEED_REVIEW_2026-09-24.md`.
The optimizer-side items (110352a, 0f973bb) and speed-ups A+B+C belong to another agent and are
not touched here. `refine_proc.py` and `routes/optimization.py` are unchanged.

Owner rule, restated 2026-09-24 ("фильтры бы я убрал"): no ramp removal, detrending, smoothing,
windowing or harmonic truncation may feed any selected or reported value. Section 6 lists every
filter-like operation that is still on a reported path. They are listed only, not removed.

## 1. 909b014 torque selector: flux-linkage mean for every ineligible run

- **Eligible runs keep Codex's terminal-work mean.** These are imposed-current runs with no eddy,
  no rotor eddy, no demag and no frozen ν, all frames converged, and a uniform window of a whole
  number of periods.
- **Every other run uses 68de0ca's space-vector mean again.** That covers eddy, demag, voltage and
  PWM, which is every client report. The mean is `(3/2)·p·n_par·<ψα iβ − ψβ iα>`, and
  `sb_postproc.space_vector_hybrid_torque` computes it with the same arithmetic as 68de0ca,
  including its 1 A per-branch gate. Below 1 A the raw Maxwell series is kept, exactly as before.
- **Failure fallback.** If terminal work raises on an eligible run, the space-vector mean is used,
  not the Maxwell mean.
- **Ripple and cogging stay the raw Maxwell AC in both branches.** Codex's zero-mean handling is
  kept: absolute peak-to-peak ripple, and `T_ripple_pct = None` near a zero mean.
- **New result field `T_mean_method`.** It is `terminal_work`, `flux_linkage_space_vector` or
  `raw_maxwell`, next to the existing `torque_method`.
- **Evidence.**
  - L155 rated (eddy + demag, delta, 36 steps): **187.855 N·m**, bit-identical to 68de0ca, with
    method `energy_mean+maxwell_ripple`.
  - Ø40 L12 rated: **0.623364 N·m**, identical to 68de0ca.
  - Every ineligible regression case reproduces its 68de0ca pin to 6 digits (section 5).
- **Tests.** `tests/test_torque_method_diagnostics.py` pinned the Maxwell fallback at 0.5 A, which
  the 68de0ca gate still gives. It was split, and a new loaded (2 A) case pins the space-vector
  branch for every ineligible flag.

## 2. c582449 raw-window core loss: commensurate rotor window, no filter

### Why one period is open for the rotor

After one electrical period the rotor has turned one pole pair. That is a rotor symmetry and the
currents repeat, so the **stator-frame** field repeats: the stator window is closed. A **rotor**
element, however, has slid 12/5 = 2.4 slot pitches (12s/10p) or 12/7 = 1.71 (12s/14p and 24s/28p).
Its one-period B(t) therefore ends partway through a slot-passing cycle. The DFT, the periodic
dB/dt and the peak-to-peak of that open window all read the end-to-start step as broadband loss,
which grows with the step count. 68de0ca hid this with the linear-ramp "leakage guard" (a filter).
c582449 removed the guard and exposed the growth.

### The exact cure at no solve cost (`simulation/rotor_window.py`)

The rotor mesh is fixed in rotor coordinates y, so the stator-frame periodicity gives

    G(y, θ + k·Θw) = R(−k·Θw) · G(R(k·Θw) y, θ)          (Θw = captured window, M periods)

What element y sees k windows later is what its pole-pair **image** sees now, rotated back. An
image outside the modelled sector (angle Φ, boundary sign s) folds back as
`s^f · R(fΦ − kΘw) · B_z`. The rotor history over q windows is therefore assembled from the solved
frames of the images, with no extra solve and no interpolation.

The only precondition is that the rotor mesh is pole-pair periodic. This is checked per element:
every rotated iron centroid must hit a centroid within 1e-6 of the smallest element size, and the
match must be a bijection. The iron-template and geo meshes the product builds all pass to about
1e-13 of the element size:

- 30 mm fixture, both sector and full ring;
- Ø40 L12;
- L155;
- all rotor elements, not just the iron.

q is the smallest whole number of windows after which the rotor is at a model-equivalent place:
`q·M·NS/p = f` with `s^f = +1`. For a one-period capture:

| machine | model | q (electrical periods) | physical rotor period, read off the data |
|---|---|---:|---|
| 12s/14p (Ø40, 30 mm) | 1/2 sector, s = −1 | **7** | 7/6 T_e (brute-force 7-period run) |
| 12s/10p (L155) | 1/2 sector, s = −1 | **5** | **5/6 T_e** (observed in every L155 run) |
| 24s/28p | 1/4 sector, s = −1 | **7** | 7/6 T_e (derived) |
| any, full ring | NS = 1 | p (24s/28p: 14 = 2 × minimum; extra bins are empty) | |

The physical period is shorter than q because the winding has a phase-permutation symmetry: a
2-slot shift maps A→−B→C…. For example, 12s/10p rotates 60° mech = 5/6 T_e. The smallest **whole**
number of electrical periods over which the rotor field repeats is therefore 7 (12s/14p, 24s/28p)
and 5 (12s/10p). These equal q for the sector models. Every run reports
`P_fe_rotor_window.observed_period_electrical`, which is the gcd of the populated DFT bins.

- On 12s/10p it reads 5/6.
- On the 12s/14p mapped windows it reads 7, because a residual mesh asymmetry at the seams puts
  about 1.5e-3 of the amplitude into off-grid bins (see below).
- The brute-force 7-period run reads 7/6.

**Why mapping rather than extending the capture.** Extending the capture would cost q−1 extra
periods: +4 on L155 and +6 on 12s/14p, that is +144 or +216 frames at 36 steps, up to about 3×
the reported window, and more on eddy runs. The stator-symmetry alternative, solving 7/6 of a
period, would cost only +N/6 frames on 12s/14p. But it needs N divisible by 6 and a per-winding
derivation of the permutation symmetry, including the rotation direction. It is kept as the
fallback idea (section 7).

- **What changed.** Stator: one period (closed), no legacy comparator. Rotor: the commensurate
  window, raw DFT, raw periodic dB/dt and raw peak-to-peak, with no ramp, detrend or taper. The
  per-frame P_fe series averages the q chained copies back onto the reported frames. Because the
  image map is a bijection of the iron, every copy carries the same instantaneous rotor total, so
  this is exact. It also replaces the open window's false wrap-around dB/dt at frames 0 and N−1.
  The loss-density map uses the same rotor window.
- **What remains, diagnostic only.** `P_fe_rotor_open_window_diagnostic` is marked
  `selected: False`. It holds the one-period open-window raw value (what c582449 selected) and the
  legacy ramp-removed value (what 68de0ca selected). `P_fe_detrended_candidate_avg_W` is that
  68de0ca-style total. Neither feeds any reported number, and neither is a fallback.
  `surface_loss_density(select_raw_window=False)` now raises.
- **The legacy guard never fires on a closed window.** Stator and commensurate rotor are evaluated
  with `legacy_comparator=False`. This matters at coarse steps. On the 30 mm fixture at 12 steps,
  the 68de0ca guard fired on the closed STATOR window: the quadratic end-point extrapolation fails
  near Nyquist, and the subtracted ramp adds a sawtooth. That billed **4.435 W instead of the raw
  4.193 W (+5.8 %)**. At 36 steps (L155) the same effect is 0.015 %.
- **Fallback.** If the mesh is not pole-pair periodic, or the window is not whole periods or not
  uniform, the rotor keeps the raw one-period window. It is flagged `closed: False` with the
  reason, and a warning is logged. It is never filtered.

### Proof: exactness and convergence

- **Exactness.** A mapped one-period run was compared with a brute-force 7-period run, the 30 mm
  fixture, 12 steps, same d-axis. The brute-force window is already commensurate (q = 1).

  | run | rotor iron, mapped from 1 period | rotor iron, 7 periods solved | Δ |
  |---|---:|---:|---:|
  | p2_load (magnetostatic) | 0.415450 W | 0.415452 W | 5e-6 |
  | p2_eddy (coupled eddy) | 0.416973 W | 0.417013 W | 1e-4 |

  The stator was identical: 4.193 W and 4.195 W on both runs. Per element, the mapped and solved
  histories agree to a mean of 2.5e-5 of max|B|. The worst element is 5.6 mT, which is 0.17 % of
  max|B|, and it sits in the innermost iron ring (r = 3.9 mm). That is a discretization-level
  asymmetry of the half-model: the stator-frame field itself repeats period-to-period to only
  1.6e-4. The effect on the rotor total is shown in the table above.
- **Convergence.** Current drive, rated operating point, no eddy and no demag, rotor iron in W
  (machine totals). The 120-step request snapped to 108.

  | steps/period | L155 rotor, commensurate | L155 rotor, open raw (c582449) | L155 rotor, legacy detrended (68de0ca) | Ø40 rotor, commensurate | Ø40 rotor, open raw |
  |---:|---:|---:|---:|---:|---:|
  | 12 | 42.68 | 44.35 | 56.62 | 0.525 | 0.529 |
  | 36 | 45.80 | 63.51 | 50.33 | 0.698 | 0.826 |
  | 72 | 46.35 | 93.54 | 50.20 | 0.735 | 1.000 |
  | 108 | **46.38** | 122.18 | 50.75 | – | – |

  - The commensurate L155 rotor loss converges: +7.3 %, +1.2 %, +0.05 %.
  - The open raw window grows almost linearly with Nyquist (×2.75 from 12 to 108).
  - The legacy detrended value never converges to the right number. It stays 8–10 % high at every
    resolution, because a linear ramp is not the leakage.
  - The Ø40 steel (20SW1200) is a Bertotti steel. Its remaining 36 → 72 rise (+5 %) is the central
    difference resolving the slot harmonics, not leakage: the open window grows +21 % over the same
    step.
- **Cost.** Post-processing only: an index gather over q·N frames and one extra diagnostic rotor
  evaluation of the open window. On the A/B cases this is below timing noise (L155 rated
  160 vs 166 s).

## 3. a88ae64 virtual-work torque: opt-in, zero cost when off

`SB_P2_VIRTUAL_WORK=1` enables it. When it is off, no mortar trace factorization is built and no
frame re-assembles the pointwise stiffness. The per-frame series keeps its length (null values)
with the reason `disabled_set_SB_P2_VIRTUAL_WORK=1`, and `virtual_work_diagnostics.status` says
"disabled". When it is on, it runs exactly as Codex wrote it. The new AST test in
`tests/test_p2_virtual_work_diagnostic.py` pins both guards.

## 4. 8997bb3 / 821f3df cogging sampling: opt-in "cogging quality"

`sampling_purpose` now takes four values:

| purpose | frames | record / warning |
|---|---|---|
| `standard` (default) | requested (snapped) steps, as 68de0ca | below 6 samples per cogging cycle: `cogging_sampling_reason = requested_steps_kept_below_cogging_target`, `sufficient = False`, one warning |
| `optimization` (821f3df) | requested steps | same, judged against 3 samples per cycle |
| `cogging_quality` (new, opt-in; route `/fem_transient?sampling_purpose=cogging_quality`) | raised to ≥ 6 samples per cycle on an existing slip-ring divisor (8997bb3's rule) | `raised_to_existing_slip_ring_divisor` |
| `internal_probe` | never raised, never warned | `internal_probe_exempt` |

- **Internal probes are exempt.** The ψ_PM probe (`noload_psi_pm`), the 20 °C Ld/Lq probe
  (`noload_incremental_ldq`), the d-axis calibration (also exempt through its TLS flag) and the
  bench (`routes/simulation._bench_compute`) all pass `internal_probe`. They are back to 6 frames:
  the review measured them at 96/120 frames, +50–70 s per new geometry.
- **Evidence.**
  - Ø40 no-load, standard, 36 steps: 36 frames, raw pp 0.0649029 N·m.
  - The same run in `cogging_quality`: 72 frames, pp 0.0649030 N·m, 56 s vs 48 s.
  - L155 and Ø40 standard runs solve the same frame counts as 68de0ca (111 and 75 frames solved).
- **Tests.** `tests/test_cogging_frame_policy.py` was rewritten. The raise tests now pass
  `cogging_quality`, because they pinned 8997bb3's every-run default, which is intentionally gone.
  New tests cover standard/optimization keep, above-target, probe exemption, and an AST check that
  every probe call site names `internal_probe`.

## 5. A/B against 68de0ca, and the re-pin

### Solver-direct A/B

- **Setup.** Saved duties, the review's harness `run_case.py`, each run alone at 4 BLAS threads,
  Normal priority. `motor_ai_sim.__file__` was asserted per variant. Base is the 68de0ca worktree
  (`scratchpad/held/wt_68de`); new is this tree.
- **Timing caveat.** L155 was timed twice. The first base run hit outside load (282 s), so the
  table shows the repeat.

| case | T mean (N·m) | ripple pp (N·m) | P_fe total (W) | stator / rotor (W) | frames solved | wall (s) |
|---|---|---|---|---|---|---|
| L155 rated (eddy + demag, Δ, 36 st) | 187.855 → 187.855 | 2.67983 → 2.67983 | 1447.61 → 1442.61 (−0.35 %) | 1390.73 / 56.88 → 1390.52 / 52.09 | 111 → 111 | 166 → 160 |
| Ø40 L12 rated (eddy + demag, 36 st) | 0.623364 → 0.623364 | 0.0380 → 0.0380 | 9.300 → 9.172 (−1.38 %) | 8.461 / 0.839 → 8.461 / 0.710 | 75 → 75 | 126 → 124 |
| Ø40 no-load (cogging, 36 st) | 1.03e-4 (Maxwell) → 0 (terminal work) | 0.0649029 → 0.0649029 | 9.414 → 9.281 (−1.41 %) | 8.587 / 0.827 → 8.587 / 0.693 | 36 → 36 | 49 → 48 |
| Ø40 sine voltage (36 st) | 0.933469 → 0.933492 | 0.06855 → 0.06652 (−2.97 %) | 12.304 → 12.129 (−1.42 %) | 11.182 / 1.122 → 11.182 / 0.947 | 396 → 180 | 475 → 230 |
| Ø40 conservative current (36 st) | 0.625436 → 0.625436 | 0.0348 → 0.0348 | 9.331 → 9.202 (−1.38 %) | 8.505 / 0.826 → 8.505 / 0.698 | 36 → 36 | 49 → 44 |

Every other EM number is bit-identical (V_peak, ψ, P_cu AC, P_mag, P_shaft, Br), except in the
voltage case. Client-visible moves above 0.5 %, explained:

- **P_fe −1.4 % on every Ø40 case: rotor window, item 2.** The Ø40 steel 20SW1200 is a Bertotti
  steel. Its rotor classical term fell from 0.531 to 0.394 W: the open window's periodic central
  difference had spanned the end-to-start step at frames 0 and N−1. Hysteresis and excess rose 3 %,
  because the closed window sees the element's full peak-to-peak. The stator is unchanged.
- **Ø40 no-load T mean 1e-4 → 0 and T_ripple_pct 62761 % → undefined.** This is 909b014's
  accepted eligible branch: a conservative current run at I = 0 has zero terminal work. The
  absolute pp ripple is unchanged. This is not item 1.
- **Ø40 voltage ripple −2.97 % and frames 396 → 180.** This is f83e60f, accepted: the sine-voltage
  settle went from 10 to 4 periods. The T mean moves +0.002 %.
- **L155 P_fe −0.35 %, rotor 56.88 → 52.09 W.** The legacy ramp removal left about 9 % of leakage
  in the rotor; the commensurate window has none. That is below 0.5 %.

### Physics regression

`tests/test_physics_regression.py` was re-pinned in one commit (30 mm fixture, 12 steps). The
figures below split each pin's move in this order: item 4 first (72 → 12 frames, under the old
mean and window rules), then item 1 (mean), then item 2 (rotor window).

| case | T_avg old → new | item 4 | item 1 | T mean method | 68de0ca pin | P_fe old → new | item 4 | item 2 | 68de0ca pin |
|---|---|---:|---:|---|---:|---|---:|---:|---:|
| p2_demag | 0.391011 → 0.395095 | +0.77 % | +0.28 % | space vector | 0.395096 | 5.3186 → 4.4560 | −14.24 % | −1.98 % | 4.7373 |
| p2_demag_eddy | 0.388947 → 0.395946 | +0.82 % | +0.98 % | space vector | 0.395945 | 5.3279 → 4.4590 | −14.33 % | −1.98 % | 4.7357 |
| p2_eddy | 0.405972 → 0.410051 | +0.24 % | +0.77 % | space vector | 0.410051 | 5.5941 → 4.6111 | −15.56 % | −2.01 % | 4.9303 |
| p2_load | 0.409663 → 0.409168 | −0.12 % | 0 | terminal work | 0.409168 | 5.5810 → 4.6074 | −15.44 % | −2.01 % | 4.9303 |
| p2_noload | 0 → 0 | – | 0 | terminal work | 4.1e-5 | 4.9024 → 4.1178 | −14.15 % | −1.86 % | 4.3378 |
| p2_voltage | 0.236932 → 0.243781 | +2.62 % | +0.27 % | space vector | 0.243786 | 6.4504 → 5.5122 | −12.68 % | −1.86 % | 5.8566 |
| p2_voltage_eddy | 0.235642 → 0.244539 | +2.77 % | +1.00 % | space vector | 0.244533 | 6.4487 → 5.5096 | −12.70 % | −1.87 % | 5.8543 |
| p2_voltage_eddy_rotor | 0.235167 → 0.245173 | +2.85 % | +1.41 % | space vector | 0.245167 | 6.4489 → 5.5089 | −12.71 % | −1.87 % | 5.8536 |

- **Item 4 (8997bb3 default removed, 72 → 12 frames) moved every other pin.** The values return to
  their 68de0ca-era 12-frame values:
  - T_ripple −85 to −90 %;
  - V_peak −1.9 to +2.8 % (voltage cases −6.5 %);
  - P_cu_ac_solve −7 to −9 %;
  - P_mag_solve −4 to −9 %, P_mag_honest −18 to −20 %;
  - Br mean +3.8 % and Br min +11.7 %: the 12-frame grid visits fewer field extrema;
  - voltage currents I_A_fund +6 % and P_cu +11 %;
  - p2_noload pp 0.00979 → 0.0000534 N·m. At 1 sample per cogging cycle the no-load pin is
    aliased; that is what `cogging_quality` is for.
- **Item 1 moved T_avg only,** by +0.28 to +1.41 %, on the six ineligible cases. It is 0 on
  p2_load and p2_noload, which are eligible and keep terminal work.
- **Item 2 moved P_fe only,** by −1.9 to −2.0 %. That is the rotor going from the open raw window
  to the commensurate window.
- **New pins against the 68de0ca pins.**
  - T_avg is identical to 6 digits. The voltage cases differ by 2e-5 relative, which is f83e60f's
    shorter settle.
  - P_fe is 5.5–6.5 % lower. About 1.3 points of that is the rotor (commensurate vs legacy
    detrended). About 5.5 points is the stator: 68de0ca's guard fired on the closed stator window
    at 12 frames (4.435 → 4.193 W, section 2). At the owner's 36+ steps that effect is 0.015 %.
- **Item 3 moved nothing.**

## 6. Filter-like operations still on a reported path (listed, not removed)

| where | what it does | value it affects |
|---|---|---|
| `simulation/eddy_solver_2d.py:343` `honest_rotor_eddy(n_harm=17)` | truncates the rotor-frame A(t) spectrum at k ≤ 16 per electrical period | `P_mag_honest_W`, `P_shaft_honest_W`, which are reported as magnet and shaft loss when rotor_eddy is on and the coupled eddy solve is off |
| `simulation/eddy_solver_2d.py:295,326` `region_eddy_from_history(amp_floor=5e-4)` | skips harmonics below 5e-4 of the largest boundary amplitude | same |
| `simulation/fem_solver_2d.py:2408` `_spectral_ddt_series`, called with `_Kv2 = min(2·k_slot+1, N/2−1)` (line 8099) | truncates dψ/dt above the second slot harmonic | V_A/B/C, `V_peak`, back-EMF waveform and THD, and `P_elec_in_W` (⟨v·i⟩) |
| `simulation/field_ops.py:154` `_smooth_demag_H` (used by `demag.py:142`) | area-weighted nodal smoothing of H inside each magnet before the Br ratchet | `demag_br_*`, and through the de-rated Br all torque, EMF and losses of demag runs |
| `simulation/losses.py:30` `HARMONIC_FLOOR_T = 1e-3` | skips measured-surface harmonics below 1 mT everywhere in a half (about 1e-6 of the loss by its own note) | `P_fe` (measured-surface steels) |
| `simulation/fem_solver_2d.py:1024` `_period_dc` (vdrive DC anchor) | subtracts the measured period-mean DC from the circuit state at settle-period boundaries | voltage/PWM currents, torque ripple and copper loss (settling only, not the reported window) |
| `simulation/fem_solver_2d.py:7634` `np.maximum(_P_fe_t, 0)` | clamps the per-frame iron loss at 0 | `P_fe_W` series (inert: every term is ≥ 0) |
| `simulation/angle_ddt.py` (savgol + C0 wrap-detrend) and `losses.declip` (median ± 5 MAD) | P1-only derivative and outlier clip | none on P2 (P1 retired); still in the tree |

Removed or neutralised in this change: the ramp-removal "leakage guard" (`losses.py:
harmonic_amplitudes`) is now diagnostic only. It no longer runs on closed windows, and the default
`surface_loss_density` and the loss map no longer select it.

`honest_rotor_eddy` works on the same open one-period rotor-frame window. Its k ≤ 16 cap was
"measured necessary" because the loss grew with the step count, and that is the signature of
open-window leakage. The commensurate map (section 2) applies to nodal A as a scalar (image
node, sign `s^f`). It would probably let the cap go. That needs its own measurement.

## 7. Open decisions for the orchestrator

1. **110352a final validation vs item 4.** `routes/optimization._standard_quality` rejects a
   re-solve unless `cogging_sampling_purpose == "standard"` and
   `cogging_sampling_final_quality_sufficient` is true. Standard runs no longer raise to 6 samples
   per cycle, so validation fails on any run below 6 per cycle (m12 at 48 steps: 4 per cycle).
   The fix, which belongs to item 5's owner, is for the validation re-solves to request
   `sampling_purpose="cogging_quality"` and for `_standard_quality` to accept it. `refine_proc`
   and the optimizer's purpose checks would then need the third value. It is not done here, per
   the file ownership.
2. **Remove the honest-eddy cap?** Apply the commensurate map to `_histA_rot2` and re-measure the
   k ≤ 16 cap (section 6).
3. **`_spectral_ddt_series` truncation** feeds V_peak and ⟨v·i⟩. It needs an owner decision, for
   example a raw periodic spectral derivative, with its jitter measured.
4. **Exactness margin of the rotor map.** The half-model has an asymmetry of up to 0.17 % of |B|
   locally at the rotor bore. Rotor totals agree with a brute-force 7-period solve to 5e-6
   (magnetostatic) and 1e-4 (eddy). If a zero-seam window is ever required, the fallback is to
   solve the missing N/6 frames of the 7/6 T_e stator-symmetry period (12s/14p). This needs
   6 | N and a per-winding permutation derivation.
5. **Observed-period diagnostic.** It reads 7 instead of 7/6 on 12s/14p mapped windows because of
   the asymmetry above. It is harmless, since the window is commensurate either way, but it could
   be tightened with a relative floor of 3e-3.

## Commits

| commit | item |
|---|---|
| 8a9e43d | 1: flux-linkage mean on ineligible runs (909b014) |
| 503c5b3 | 3: virtual work opt-in (a88ae64) |
| 1883ba7 | 4: `cogging_quality` opt-in and probe exemption (8997bb3 / 821f3df) |
| 19b3b35 | 2: commensurate rotor window (c582449) |
| 94b843d | re-pin of `tests/physics_baseline.json`, per-item attribution in the message |

**Verification.**

- `tests/test_physics_regression.py`: 14 passed.
- Eddy, strand, wire and voltage suites (`test_eddy_*`, `test_p2_eddy_current_normalization`,
  `test_p2_strand_conservation`, `test_voltage_*`, `test_wire_*`, `test_harmonic_eddy_cache`):
  132 passed.
- The item tests plus `test_losses`, `test_core_loss_surface`, `test_solver_guards`,
  `test_loss_heat_split`, `test_bench_angular_sampling` and `test_torque_*`: 203 passed.

**Failing tests not caused by this change.**

- `tests/test_sampling_purpose_flow.py::test_every_optimizer_eval_and_cache_call_explicitly_names_purpose`
  is the 0f973bb failure already known from the review.
- Three tests in `tests/test_screening_descent.py` fail against the owner's current live
  `config/motor_config.yaml` with or without these commits. They pass with this code on the
  dd54235 worktree's config.

## Reproduction

The scripts are in the orchestrator scratchpad, `held/`:

- `run_case2.py` and `drive.py` run the A/B and convergence cases.
- `val_window.py`, `dump_window*.py` and `cmp*.py` run the mapped-vs-brute-force check.
- `regen.py`, `drive_regen.py` and `decomp.py` regenerate the pins and split them by item.
- `probe_mesh*.py` runs the mesh periodicity probe.

Results are in `res_*.json`, `rg_v1_*.json` and `vw_*.json`.
