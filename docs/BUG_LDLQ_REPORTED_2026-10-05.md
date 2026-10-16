# Bug report — solver `inc_ldq` Ld/Lq is not the small-signal inductance (2026-10-05)

Status: **fixed 2026-10-11** (branch `fix/ldlq-small-signal`) — see "Fix" at the end.
Found by: Ø40 passport pilot, stage 1 (PR #106). Model: Claude Opus 5.5.

## What is wrong

`em_transient_eval(..., inc_ldq=True)` reports `Ld_inc_mH` / `Lq_inc_mH`
("frozen-permeability incremental at the point"): the converged loaded field's
per-element ν is frozen and a unit d- and q-current is solved on it. At a
small current, that frozen ν is the SECANT reluctivity of the bias field, not
the DIFFERENTIAL one, so the result is a secant-type inductance. It is
quoted in the run summary and the report as if it were the inductance a
bench LCR meter or a current controller sees.

## Evidence (same mesh, same solver, same day)

| Machine | Quantity | Δψ/Δi, ±2 A pairs (differential) | `inc_ldq` at 2 A (cold) | ratio | `inc_ldq` at hot I0 |
|---|---|---|---|---|---|
| L12 | Ld µH | 3.382 (cold) / 3.775 (hot) | 8.263 | 2.4× | 9.126 |
| L12 | Lq µH | 5.411 / 5.703 | 9.163 | 1.7× | 9.718 |
| L20 | Ld µH | 5.639 / 6.354 | 13.78 | 2.4× | 15.41 |
| L20 | Lq µH | 9.014 / 9.543 | 15.29 | 1.7× | 16.26 |

- Δψ/Δi: four runs per temperature, symmetric ±2 A on the d and q axes
  (`hot_ss_g±0/180/±90`, `cold_ss_*`), magnetostatic, Coulomb torque basis.
- The older bench probe of this die (3.7 / 5.3 µH) agrees with Δψ/Δi, not
  with `inc_ldq`.
- The coupled EM-thermal rated run (2026-10-05) again prints Ld 8.7 µH /
  Lq 9.4 µH at the rated point (`ldq_method: frozen-permeability incremental`).

## Where it matters

- Current-loop tuning, PWM ripple current (ΔI ∝ 1/L) and the controller's
  dead-time distortion use the small-signal value; `inc_ldq` would under-read
  the ripple by 1.7–2.4×.
- The passport card quotes the Δψ/Δi value and labels the solver value
  (owner instruction 2026-10-05). The PWM FEM runs are unaffected: they solve
  the circuit with the field, no Ld/Lq is used.

## Suggested fix (for a separate solver branch)

Freeze the DIFFERENTIAL reluctivity (ν + B·dν/dB along B, i.e. the tangent
tensor) instead of the secant ν, or report Δψ/Δi from a ± current pair at the
point. Add a test: on a linear-iron model both must agree; on a saturated
model the tangent result must match a ±ΔI finite difference within 2 %.

## Fix (2026-10-11, Claude Opus 5.5)

`frozen_permeability_ldq` (`simulation/fem_solver_2d.py`) now solves the unit
d- and q-currents on the TANGENT operator J = K(ν) + T of the converged frame,
T = 2·(dν/dB²)(∇A·∇u)(∇A·∇v) — the reluctivity tensor ν·I + 2ν'·B⊗B, i.e. the
Newton Jacobian (`P2Nonlinear.tangent2`, unclamped; a non-monotone B-H table
falls back to the clamped tangent, stamps `ldq_method` "regularized …" and
every consumer withholds the value). Magnets stay
linear (μ_rec, in the constant part of K). ψ is integrated exactly as in the
main run; L = ∂ψ/∂i, cross term Ldq kept.

- `inc_ldq.Ld_mH` / `Lq_mH` / `Ldq_mH` and the summary's `Ld_inc_mH` /
  `Lq_inc_mH`: differential, `ldq_method: "differential (tangent reluctivity)"`.
- The old values stay as `Ld_fp_secant_mH` / `Lq_fp_secant_mH`
  (`"frozen-permeability secant"`); ψ*_PM and the superposition self-check
  still come from that secant solve (only there is ψ = ψ*_PM + L·i an identity).
- A stored run without the secant keys is reported under the secant label, with
  `Ld_inc_mH` empty (report: "re-run"). Summary shape v20; catalogue Ld0/Lq0
  cache key `ldq0_v2`.
- Independent review (Codex Astra, `REVIEW_LDLQ_ASTRA.md`) follow-ups: one
  provenance rule (`simulation/ldq_provenance.py`) read by every consumer — the
  controller's PWM ripple refuses secant or regularized duty inductances with a
  re-run note; stored catalogue `constants_20c` Ld0/Lq0 are labelled from their
  own `ldq0_method` (legacy = secant); the probe frames are chosen inside the
  reported window (after the voltage-settling and demag prefixes, which earlier
  runs of those schedules sampled). Values are 2-D magnetostatic per winding:
  no end-winding inductance, no 3-D correction, no frequency-dependent eddy
  effect. The forward-difference dν/dB² of `tangent2` is kept (the Newton
  shares it; changing it would move every pinned solve).

### Validation — Ø40 CIANO14 40 new, same mesh, magnetostatic, 2 A

Δψ/Δi: symmetric ±2 A pairs on d and q (60° window, 24 positions). New
`inc_ldq`: mean of the four 2 A runs, probe on all 24 positions; the
production default (4 positions, q-axis 2 A run) in brackets. d-axis pinned at
59.9911°, static gap layers 2 (L12) / 3 (L20). Runs: server container,
`/opt/motres/compute/ldlq-fix`, 2026-10-11.

| Machine | Temp | Axis | Δψ/Δi µH | new `inc_ldq` µH | Δ | old (secant) µH |
|---|---|---|---:|---:|---:|---:|
| L12 | cold 20 °C | Ld | 3.382 | 3.376 (3.369) | −0.2 % (−0.4 %) | 8.262 |
| L12 | cold 20 °C | Lq | 5.411 | 5.407 (5.398) | −0.1 % (−0.2 %) | 9.162 |
| L12 | hot 107/101 °C | Ld | 3.775 | 3.768 (3.762) | −0.2 % (−0.3 %) | 8.956 |
| L12 | hot 107/101 °C | Lq | 5.703 | 5.699 (5.693) | −0.1 % (−0.2 %) | 9.703 |
| L20 | cold 20 °C | Ld | 5.639 | 5.630 (5.616) | −0.1 % (−0.4 %) | 13.78 |
| L20 | cold 20 °C | Lq | 9.014 | 9.009 (8.990) | −0.0 % (−0.3 %) | 15.29 |
| L20 | hot 114/200 °C | Ld | 6.354 | 6.342 (6.334) | −0.2 % (−0.3 %) | 15.04 |
| L20 | hot 114/200 °C | Lq | 9.543 | 9.535 (9.529) | −0.1 % (−0.1 %) | 16.27 |

Rated point (hot, I0 at the pilot's MTPA γ, 24 positions):

| Machine | i_d / i_q A | Ld new / old µH | Lq new / old µH | Ldq new µH | L(θ) spread d / q |
|---|---|---:|---:|---:|---:|
| L12 | −6.68 / 60.13 | 4.07 / 9.13 | 5.66 / 9.72 | −0.78 | 14 % / 11 % |
| L20 | −9.64 / 73.69 | 7.18 / 15.41 | 9.64 / 16.25 | −1.71 | 17 % / 11 % |

The Δψ/Δi pairs and the old secant values (cold 2 A, hot rated) reproduce the
evidence table at the top within 0.05 %. Reciprocity and superposition checks 0.0 % on every run; no
tangent was clamped. Unit tests (`tests/test_incremental_ldq.py`): linear iron
— differential equals secant to round-off; saturated P2 square (median
|B| 1.85 T, secant/differential 2.2–2.5×) — tangent equals a ±Δi finite
difference of converged nonlinear solves within 1 % (measured 0.1 %).
