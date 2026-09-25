# Eddy time integration, segmentation width, frequency-domain cross-check, bisected magnets (2026-09-25)

Claude Opus 5.5 (`claude-opus-5-5`), one sub-agent, no escalation. Branch
`pre-migration-freeze-2026-09-15`, base 77f4b94. Items from
`CONDUCTIVE_BODY_MESH_CONVERGENCE_2026-09-24.md` §7-8. Owner's rule: only real physics, no
filters; results must not depend on the discretisation.

Harness (scratchpad `eti/`): `run_case.py` (saved duty, solver-direct, cold), `regen.py`
(physics-regression case), `drive.py`/`drive_regen.py` (parallel jobs, 4 BLAS threads, HEAD =
worktree of 77f4b94, new = snapshots; `motor_ai_sim.__file__` stored in every result),
`conv.py` (observed order + extrapolation). Results `eti/res/*.json`.

## Summary

| # | item | done | client-visible |
|---|---|---|---|
| 1 | σ∂A/∂t time integration | **BDF2** (variable step, BE start step), loss sampled at the step midpoint; circuit stays Crank–Nicolson; `SB_EDDY_BE=1` = old march bit-for-bit | L155 magnets +7.3 % (2-D), L180 +8.0 %, copper AC +1-2 %, shaft +2-5 %, sleeve +1.3-1.6 % |
| 2 | segmentation width | CAD outline (area principal axes), not mesh nodes | L155/L180 reported P_mag +1.5 % (32.72 → 32.46 mm) |
| 3 | frequency-domain cross-check | solver's per-element ν and BCs implemented; the shaft figure is flagged a linear estimate | cross-check keys only (`P_*_honest_W`) |
| 4 | bisected magnet | one exact ∫J = 0 row per physical magnet (paired pieces), synthetic proof | none (no machine has one) |

## 1. Second-order time integration

### Scheme and why

- **BDF2 for the eddy term.** A-stable and stiffly decaying (L-stable), so the σ = 0 algebraic rows,
  the saturating iron and the switching PWM voltage are damped, never rung. Written as one
  effective step: ∂A/∂t|_k = (A_k − A_hist)/Δt_eff with Δt_eff = h(1+ω)/(1+2ω),
  A_hist = [(1+ω)²A_{k−1} − ω²A_{k−2}]/(1+2ω), ω = h/h_prev (`P2Drive.bdf2_history`). The
  bordered system keeps its exact shape and symmetry. Current drive, voltage drive, PWM
  (coarse → fine steps), strand paths, demag re-solves use it unchanged.
- **Start and restarts:** one backward-Euler step when there is no second level (static cold
  start, voltage initialiser, cold retry, step ratio > 1.8). Warm-up extensions and the demag
  pre-pass carry **both** levels through the exact pole-pair map. The warm cache stores the
  second level (`A2`) and a scheme key (`ts`); a BE state is refused by a BDF2 run.
- **Crank–Nicolson rejected, measured.** Trapezoidal on the eddy term (derivative-state form,
  `SB_EDDY_TS=cn` during the study, removed): L155 sleeve 37.0 W at 36 steps against 9.8-10.2 W
  by every other scheme, and the 30 mm fixture magnets 8.23 W at 12 steps against 1.6 W with the
  warm-up never settling (206 frames) — the undamped (−1)^k mode of the stiff conductors.
- **The circuit stays Crank–Nicolson.** It integrates each step's MEAN applied voltage
  (exact volt-seconds for the piecewise-constant inverter); BDF2 would need point samples of a
  chopped voltage. It was already second order (NO_FILTERS §2); the constraint rows close both
  at t_k. So field and circuit are both second order and meet at one instant.
- **Where the loss is sampled.** BDF2's derivative at t_k over-reads an under-resolved harmonic
  (measured: L155 magnets +7.0 % at 36 steps). The Joule loss is sampled at t_{k−½}: centred
  difference (A_k − A_{k−1})/h of the BDF2 states, U_b from the body's own row with the net
  currents interpolated to t_{k−½} (quadratic through three levels). Second order, the
  midpoint convention of the CN step voltage. The step itself is unchanged
  (`SB_EDDY_LOSS_AT=step` = BDF2 at t_k).

Synthetic check (`tests/test_cut_bodies.py`, travelling field on a conducting ring,
production `P2Drive.eddy_solve`), error against a 384-step reference, 24/48/96 steps:
BE −3.06/−1.42/−0.68 % (order 1.1, 1.0); BDF2 at t_k +4.37/+1.11/+0.28 % (2.0); BDF2 midpoint
−0.71/−0.17/−0.04 % (2.1).

### Convergence (loss vs steps/period)

Observed order p from three step counts; limit = Richardson with p = 1 (BE) or 2 (BDF2) from the
two finest; errors against that limit. Magnets = 2-D solid σE² (before segmentation).

**L155 rated** (4-period cap, same march length at every step count; 36/72/108):

| quantity | BE | order | BDF2 (midpoint) | order | limit BE / BDF2 |
|---|---|---:|---|---:|---|
| magnets [W] | 3906.8 / 4134.8 / 4213.8 (−10.6/−5.4/−3.6 %) | 0.93 | 4190.3 / 4332.1 / 4357.7 (−4.3/−1.05/−0.47 %) | 2.04 | 4372 / 4378 |
| shaft [W] | 6.853 / 7.038 / 7.109 | 0.76 | 7.061 / 7.235 / 7.264 (−3.1/−0.7/−0.3 %) | 2.20 | 7.25 / 7.29 |
| sleeve [W] | 9.787 / 10.038 / 10.098 | 1.56 | 9.913 / 10.111 / 10.149 (−2.6/−0.7/−0.3 %) | 1.93 | 10.22 / 10.18 |
| copper AC [W] | 600.5 / 608.5 / 611.1 (−2.6 %) | 1.05 | 609.6 / 614.4 / 615.3 (−1.1/−0.3/−0.1 %) | 1.88 | 616.2 / 616.1 |
| V_peak [V] | 434.15 / 436.25 / 436.60 | | 436.73 / 437.47 / 437.48 | | |
| ripple [%] | 1.54 / 1.90 / 1.88 | | 1.58 / 1.92 / 1.89 | | |

Both schemes extrapolate to the same limit (magnets 4372 vs 4378 W, 0.15 %). The torque
ripple at 36 steps is still 15 % low with BDF2: it is the SAMPLING of a ripple whose dominant
harmonic (6th electrical) has only 6 samples per cycle, not the eddy term (the BDF2 and BE
torque series differ by 3 %).

**L13 rated** (40/60/120, snapped): magnets BDF2 −0.90/−0.41/−0.10 % (order 1.87), BE
−1.25/−0.72/−0.36 %; shaft BDF2 −3.4/−1.7/−0.4 % (1.69), BE −7.0/−4.7/−2.4 % (0.95); copper AC
BDF2 −0.53/−0.23/−0.06 % (2.10), BE −1.6/−1.0/−0.5 % (1.15).

**Ø40 L12 rated** (36/72/144): magnets BDF2 −0.87/−0.21/−0.05 % (2.05), BE −1.49/−0.60/−0.30 %;
shaft BDF2 −6.2/−1.4/−0.4 % (2.13), BE −7.8/−3.5/−1.7 % (1.30); copper AC BDF2
−0.97/−0.24/−0.06 % (2.04), BE −3.1/−1.5/−0.8 % (1.06).

**Ø40 sine voltage drive** (12.5 V, 20°, eddy + rotor eddy; 36/72/144): magnets BDF2
−0.46/−0.11/−0.03 % (2.01), BE −0.98/−0.43/−0.21 % (1.38); shaft BDF2 −4.0/−1.1/−0.3 % (1.75),
BE −7.0/−3.9/−2.0 % (0.65). Copper AC converges at order ~1.5 on both (the circuit's current
harmonics themselves move with the step count; T ripple 6.86 → 7.83 → 7.95 %).

**Ø40 PWM** (`pwm_voltage`, 30 V bus, 16.68 kHz = 11 carriers, eddy + rotor eddy, all-fine
settle; 48/72/144 steps = 4.4/6.5/13 per carrier; 144 is the ring's maximum for this machine):
no scheme is in its asymptotic range — every loss is dominated by how much of the carrier
ripple the step count resolves (the solver itself warns below 16 per carrier). Magnets BDF2
7.80/8.41/9.70 W, BE 7.88/8.47/9.66 W; copper AC BDF2 13.5/15.4/19.1 W, BE 14.2/16.1/19.3 W.
At 144 steps the two schemes agree within 0.4 % (magnets) and 0.8 % (copper). A PWM order study
needs ≥ 16 steps per carrier (a ring divisor ≥ 176) — open item.

Fixture (30 mm, p2_eddy, 12/24/48/96/192): magnets new 1.495/1.604/1.631/1.636/1.624 W
against BE (HEAD) 1.487/1.596/1.626/1.633/1.622 W; copper AC new 3.366/3.544/3.602/3.614/3.615
against BE 3.293/3.473/3.552/3.577/3.600.

### Cost

Paired runs (same box, same time, same frames): per frame identical within noise — L155 36
steps BE 624.8 s vs BDF2 620.3 s (218 frames), Ø40 621.7 vs 612.9 s, L13 960 vs 969 s. The only
additions are one vector combination and one σ-mass product per frame. The frame count is
unchanged on every duty (the gauge closes on the same periods).

## 2. Segmentation width from the CAD polygon

`losses.polygon_char_width_m`: principal axes of the outline's AREA (Green's theorem), extent
of the outline along them. `cut_bodies.match_outlines` finds each meshed magnet's outline (the
polygon containing its area centroid; a cut piece gets the whole block). The report says
`width_source: cad_polygon`; a body without an outline falls back to its nodes and says so.

| duty | width mesh nodes (HEAD) → CAD | factor | reported P_mag |
|---|---|---|---|
| L155 | 32.724 → 32.456 mm | 0.021684 → 0.022007 | +1.49 % |
| L180 | same die | 0.021226 → 0.021547 | +1.51 % |
| L13, Ø40 | 7.075 → 7.033, 5.716 → 5.709 mm | solid (1.0) | 0 |

The previous doc's two meshes read 32.548 and 32.724 mm; the CAD reads 32.456 mm (all five
bodies within 0.004 mm). Tests: `tests/test_magnet_segmentation_width.py`.

## 3. Frequency-domain rotor cross-check

Implemented option (a): `eddy_solver_2d.rotor_eddy_solver_bc` solves on the coupled solve's
terms — window-mean per-element secant ν (frozen permeability), the (anti)periodic cut
(`sector_projection`), U ≡ 0 on the cut shaft ring (full ring: ∫J = 0 row), a bisected magnet as
one body, constraint columns from each body's own σ-mass. Cost 3-7 s per run (was 5-11 s).

| duty | P_shaft cross-check HEAD → new [W] | coupled [W] | P_mag cross-check → new [W] (2-D) | coupled 2-D |
|---|---|---|---|---|
| L155 | 48.7 → 0.44 | 5.26 | 51.0 → 65.4 (×1/0.022) | 92.3 |
| L180 | 58.7 → 0.56 | 9.86 | 157.7 → 202.7 | 292 |
| L13 | 0.336 → 0.251 | 0.439 | 0.233 → 0.321 | 0.441 |
| Ø40 | 0.063 → 0.002 | 0.009 | 1.28 → 2.14 | 3.09 |

(The previous agent's offline route D read 0.85-1.26 W on L155; the 0.44 W here uses the
window-mean ν instead of the last frame's.)

**Verdict: consistent inputs, still not a cross-check of the shaft.** With the solver's μ and
BCs the linear route reads the L155 shaft 12× LOW instead of 9× high, and the Ø40 shaft 4× low: the coupled shaft loss is carried by the μ(B) modulation
(DC-biased saturating wall and back iron generate rotor-frame harmonics) which a fixed-μ phasor
model has not got. Even the 30 mm fixture's aluminium shaft reads 0.001 W linear against
0.016 W coupled, because the back iron in front of it saturates. So the result now carries
`P_rotor_eddy_honest_window.shaft_is_linear_estimate` (True whenever the shaft or the back iron
is saturable — every catalogue machine) and the log calls it "frequency-domain LINEAR route"
with that note, not "honest". The magnet figure remains a meaningful linear cross-check
(magnets μ ≈ 1, resistance-limited): 69-73 % of the coupled 2-D value on all four duties. The keys `P_mag_honest_W`/`P_shaft_honest_W` keep their names (API); renaming them is
an owner call.

## 4. Bisected magnet at the sector cut

A magnet across the cut θ = 0 appears in the wedge as its 0⁺ piece and the image of its other
piece below θ = φ, whose field is s·A (s = −1 anti-periodic). One physical magnet: one U, the
image piece carrying s·U, one row g = g₀ + s·g_φ, S = S₀ + S_φ (`simulation/cut_bodies.py`).
The old "U ≡ 0 on both halves" fixed U instead of the net current; exact only for a closed ring
(shaft, sleeve: their own image, U = 0 by symmetry — unchanged).

Detection by geometry, not area: a piece has an element edge on a cut ray, is smaller than its
CAD outline, and has a partner with identical cut-face radii on the other ray. Unpaired
cut-touching bodies (a whole magnet whose face lies on the ray) get their own row. Result key
`bisected_magnets`. The same pairing feeds the frequency-domain route.

**Synthetic proof** (`test_bisected_magnet_paired_row_matches_the_full_ring`): conducting ring
with four magnets in a travelling field, two straddling the half-model cut. Full ring vs half
model with the paired row: A identical to 1e-9 of max, per-step loss to 7.6e-12 relative. The
old U = 0 rule: +1048 % on the same machine. All four catalogue duties: 0 pairs (as before).

## A/B vs HEAD 77f4b94 (cold, saved duty, reported window, default steps)

| quantity | L155 rated | L180 gen rated | L13 rated | L13 peak | Ø40 L12 rated |
|---|---|---|---|---|---|
| T mean [N·m] | 187.788 → 187.939 (+0.08 %) | 238.868 → 239.224 (+0.15 %) | 5.3989 → 5.3995 | 9.3822 → 9.3840 | 0.621778 → 0.621868 |
| ripple [%] | 1.531 → 1.570 (+2.6 %) | 3.141 → 3.294 (+4.9 %) | 5.272 → 5.281 | 6.409 → 6.429 | 5.660 → 5.643 |
| V_peak [V] | 434.03 → 436.61 (+0.59 %) | 986.65 → 992.80 (+0.62 %) | 15.887 → 15.888 | 22.223 → 22.228 | 10.991 → 10.993 |
| V_LL peak [V] | 402.6 → 403.0 | 870.8 → 873.2 | 24.8 → 24.8 | 32.1 → 32.1 | 18.3 → 18.3 |
| **P_mag reported [W]** | **84.76 → 92.26 (+8.85 %)** | **266.54 → 292.10 (+9.59 %)** | 0.4401 → 0.4408 | 0.8877 → 0.8893 | 3.0776 → 3.0939 (+0.53 %) |
| P_shaft [W] | 5.096 → 5.255 (+3.1 %) | 9.383 → 9.859 (+5.1 %) | 0.4227 → 0.4391 (+3.9 %) | 1.732 → 1.806 (+4.3 %) | 0.00869 → 0.00886 (+2.0 %) |
| P_sleeve [W] | 9.796 → 9.923 (+1.3 %) | 33.61 → 34.16 (+1.6 %) | – | – | – |
| P_cu AC [W] | 600.68 → 609.73 (+1.5 %) | 2063.1 → 2103.2 (+1.9 %) | 2.277 → 2.302 (+1.1 %) | 3.073 → 3.102 (+0.9 %) | 4.344 → 4.438 (+2.2 %) |
| P_fe [W] | 1443.6 → 1445.3 | 3463.2 → 3470.4 | 2.478 → 2.478 | 2.819 → 2.820 | 9.142 → 9.144 |
| total loss [W] | 3803.6 → 3822.0 (+0.49 %) | 8370.5 → 8444.5 (+0.88 %) | 198.67 → 198.72 | 682.67 → 682.78 | 65.68 → 65.79 |
| P_mech [W] | 277 701 → 277 917 | 519 025 → 519 770 | 562.03 → 562.07 | 977.07 → 977.18 | 834.23 → 834.34 |
| frames solved | 650 → 650 (capped) | 650 → 650 (capped) | 282 → 282 | 282 → 282 | 218 → 218 |
| P_mag / P_shaft cross-check [W] | 51.0/48.7 → 65.4/0.44 | 157.7/58.7 → 202.7/0.56 | 0.233/0.336 → 0.321/0.251 | 0.547/1.039 → 0.663/1.292 | 1.284/0.063 → 2.141/0.002 |

Every move over 0.5 %:
- **P_mag L155 +8.85 %, L180 +9.59 %**: item 1 (2-D solid +7.25 % / +7.95 %: BE's −10.6 %
  error becomes −4.3 % at 36 steps) × item 2 (+1.49 % / +1.51 %). Ø40 +0.53 % item 1.
- **P_shaft +2-5 %, P_sleeve +1.3-1.6 %, P_cu AC +0.9-2.2 %**: item 1 (each converges from below;
  §1 tables).
- **Ripple +2.6 % L155, +4.9 % L180**: item 1 (the eddy reaction at the slot harmonics is less
  damped). The ripple itself is still sampling-limited at 36 steps (§1).
- **V_peak +0.6 % L155/L180**: item 1 — the step-mean voltage converges from below (BE 434.15,
  BDF2 436.73, limit ≈ 437.5 V; §1 table).
- **Total loss +0.5 %/+0.9 %**: the sum of the above. **Cross-check** columns: item 3.
- Wall-time differences in the raw A/B (HEAD −33 to −44 % slower) are box load (HEAD ran with
  more concurrent jobs); the paired timing above is the cost.

## Physics-regression re-pin (30 mm fixture, 12 steps)

Only the four eddy cases move; the others are untouched. Lines beyond 0.5 %:

| case | metric | pin → new | items 2-4 | item 1 |
|---|---|---|---:|---:|
| p2_eddy | P_cu_ac_solve_W | 3.293 → 3.366 | 0 | +2.22 % |
| | P_mag_solve_W | 1.487 → 1.495 | 0 | +0.54 % |
| | P_shaft_solve_W | 0.012 → 0.013 (inside its abs floor) | 0 | +8 % |
| | T_ripple_pct / pp | 0.930 → 0.902 | 0 | −2.97 % |
| | P_mag_honest_W | 0.840 → 1.104 | +31.1 % (3) | +0.3 % |
| | P_shaft_honest_W | 0.306 → 0.001 | −99.7 % (3) | 0 |
| p2_demag_eddy | P_cu_ac_solve_W | 3.012 → 3.076 | 0 | +2.12 % |
| | P_shaft_solve_W | 0.014 → 0.016 | 0 | +14 % (abs floor) |
| | P_mag_honest_W / P_shaft_honest_W | 0.783/0.298 → 1.063/0.001 | +35.4 %/−99.7 % (3) | +0.3 %/0 |
| p2_voltage_eddy | P_cu_ac_solve_W | 2.777 → 2.838 | 0 | +2.20 % |
| | T_ripple_pct / pp | 1.597 → 1.672 | 0 | +4.72 % |
| | I_A_thd_pct | 1.821 → 1.788 | 0 | −1.80 % |
| p2_voltage_eddy_rotor | P_cu_ac_solve_W | 2.777 → 2.836 | 0 | +2.12 % |
| | I_A_thd_pct | 1.833 → 1.783 | 0 | −2.75 % |
| | P_mag_honest_W / P_shaft_honest_W | 0.885/0.183 → 1.138/0.000 | +28.5 %/−100 % (3) | +0.1 % |

Items 2 and 4 move nothing in the fixture (solid magnets, no cut magnet).

## Code, tests, commits

- `simulation/p2_drive.py`: `bdf2_history`, `eddy_ops`, `eddy_solve(dte=)`, `ve_newton(dte=)`.
- `simulation/fem_solver_2d.py`: two-level history and splices, midpoint loss sampling,
  `eddy_time_scheme` in the result, warm-cache `ts`/`A2`; CAD-outline widths; bisected-magnet
  pairing (`bisected_magnets`); cross-check on the solver's terms.
- `simulation/cut_bodies.py` (new), `simulation/eddy_solver_2d.py` (`rotor_eddy_solver_bc`,
  `sector_projection`), `simulation/losses.py` (`polygon_char_width_m`).
- Tests: `tests/test_cut_bodies.py` (pairing vs full ring, orders BE/BDF2/midpoint, variable-step
  coefficients, BE identity), `tests/test_magnet_segmentation_width.py`; re-pinned
  `tests/physics_baseline.json`.
- Knobs: `SB_EDDY_BE=1` (backward Euler, the pre-change time march bit-for-bit: the
  fixture reproduces every HEAD digit of the coupled solve), `SB_EDDY_LOSS_AT=step`.

## Open decisions for the owner

1. **Default steps for eddy duties.** BDF2 at 36 steps still reads the L155 magnets −4.3 %
   (72: −1.05 %, 108: −0.47 %), the Ø40/L13 ≤ 1 %. 72 steps on L155/L180 halves the residual to
   1 % at 1.5× wall (620 → 910 s at the 4-period cap).
2. **Torque ripple** is sampling-limited at 36 steps on L155 (−15 %) — a steps × ripple study.
3. **PWM convergence** needs ≥ 176 steps (16/carrier), above the Ø40 ring's 144 divisor limit.
4. **Cross-check keys** `P_*_honest_W`: rename (e.g. `P_*_linear_W`) or keep; the shaft figure
   is flagged `shaft_is_linear_estimate`.
