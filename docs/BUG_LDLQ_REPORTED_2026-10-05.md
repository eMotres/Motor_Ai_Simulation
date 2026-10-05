# Bug report — solver `inc_ldq` Ld/Lq is not the small-signal inductance (2026-10-05)

Status: **reported, not fixed** (owner: do not fix the solver in the passport branch).
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
