# Ld / Lq: frozen-permeability incremental, and the catalogue pair at 20 °C

**2026-09-20.** Raised by a Chinese client reviewing the CIANO10 200 opt / L180
generator report: *«这个电机 Ld > Lq? 好像和一般的电机不太一样»* — "this motor has
Ld > Lq? that looks unlike an ordinary machine". It does not. The report was
printing a chord, and the chord was not an inductance.

## What was wrong

§4 printed `Ld = (ψd − ψ_PM)/i_d` and `Lq = ψq/i_q` at the duty's own operating
point (600.4 A, γ = −15°), with `ψ_PM` from the cached **no-load** probe. Under
load the iron moves the magnets' own flux linkage, and that whole difference
lands in the chord's numerator. Where `i_d` carries only a quarter of the
current, it dominates it. The solver already said so on the PWM run and withheld
Ld there; the sine run's chord went into the document unchallenged.

## The method now used

At a converged frame the field satisfies `K(ν(|B|))·A = f_PM + Σ i_k f_k`.
**Freeze** the per-element reluctivity at the loaded field's own state — keep
`K* ≡ K(ν*)` and stop letting ν follow B — and the system is linear, so

```
A   = A*_PM + Σ i_k x_k ,     K*·A*_PM = f_PM ,   K*·x_k = f_k
ψdq = ψ*_PM,dq + L·i_dq ,     L = [[∂ψd/∂i_d, ∂ψd/∂i_q],
                                   [∂ψq/∂i_d, ∂ψq/∂i_q]]
```

`L` is read off two linear solves of `K*` with a unit d- and a unit q-axis
current, Park-transformed at that rotor position. The perturbation size does not
enter — the frozen operator is linear, so `Δψ/Δi` is exact for any `Δi`, and
solving `(i + Δi)` against `(i)` with the magnets on gives the same numbers (the
magnet term cancels in the difference). A third solve with the magnet source
alone measures `ψ*_PM`, the magnets' flux **in the loaded iron**, so the term
that contaminated the chord becomes a number of its own.

Three self-checks ride with every measurement:

| check | what it asserts | measured |
|---|---|---|
| `reciprocity_pct` | `L` is symmetric (linear magnetic circuit) | 0.00 % on both duties |
| `superposition_pct` | `ψdq = ψ*_PM,dq + L·i_dq` on both axes | 0.9 % (L180), 1.3 % (L155) — coupled-eddy runs |
| `spread_pct` | how far `L(θ)` moves over the four sampled rotor positions | Ld 0.2 %, Lq 1.5 % |

On linear iron `ν* = ν`, the decomposition is exact for the machine itself, and
the incremental values **equal** the chord — the method only departs from the
chord where the chord stopped being an inductance
(`tests/test_incremental_ldq.py`).

`Ld/Lq` are now the **magnetostatic** incremental values. On a coupled-eddy run
the field's own `ψ/i` additionally carries the AC redistribution in the slot
conductors, which is impedance, not inductance; that is why the chord Lq sits
well below the incremental one on these duties.

## Measured (sandboxed re-runs of the stored duties, 2026-09-20)

Winding values, delta, at each duty's own mesh and temperatures.

| | **L180 gen · rated** | **L155 motor · rated** |
|---|---|---|
| point | 600.4 A, 20 900 rpm, γ = −15°, magnets 150 °C | 562.1 A, 14 200 rpm, γ = +15°, magnets 104.2 °C |
| torque | 239.33 N·m | 187.86 N·m |
| **Ld0 / Lq0** (20 °C, no load) | **0.0792 / 0.0830 mH** | **0.0578 / 0.0606 mH** |
| **saliency0 Lq/Ld** | **1.048** | **1.047** |
| Ld / Lq incremental at the point | 0.0798 / 0.0835 mH | 0.0602 / 0.0617 mH |
| saliency at the point | 1.047 | 1.025 |
| cross term Ldq | −0.0027 mH | −0.0014 mH |
| chord (ψd − ψ_PM)/i_d, ψq/i_q | 0.0396 / 0.0648 mH | 0.0390 / 0.0495 mH |
| ψ_PM (no load) | 0.0706 Wb | 0.0552 Wb |
| ψ*_PM in the loaded iron, d / q | 0.0663 / −0.0075 Wb | 0.0578 / −0.0053 Wb |
| what the load did to ψ_PM | sagged it 6.1 % | raised it 4.7 % |

**Conclusions.**

1. `Lq > Ld` on both machines, at load and at no load — the client's
   expectation, restored. The inversion was an artefact of the chord.
2. The saliency is ~1.05, not the 1.2–1.4 of a flux-concentrating rotor: this is
   a surface-magnet rotor under a retention sleeve, whose magnetic gap (magnet +
   clearance + sleeve) dominates both axes. The iron's saturation therefore
   barely moves either inductance between no load and 600 A — which is itself
   the answer to "does Lq collapse at rated current?" for this machine.
3. The q-axis component of `ψ*_PM` is not negligible under cross-saturation
   (−7.5 mWb on the L180). It sits inside `ψq`, so the chord `ψq/i_q` is not Lq
   either — both chord axes were contaminated, not just the d.

## What the documents print

* §4, 20 °C catalogue sub-table: **Ld / Lq at no load, 20 °C** and their
  saliency — the same basis as KV beside them (owner, 2026-09-20: *«Ld/Lq нужно
  указывать тоже для 20 градусов и без тока, как для KV»*). Stored in the
  coupled record's `constants_20c` as `Ld0_mH` / `Lq0_mH` /
  `saliency0_Lq_over_Ld` / `ldq0_method`.
* §4, machine constants: the **incremental** Ld / Lq at the point, the cross
  term `Ldq`, and one clause on the ψ_PM row saying what the load did to the
  magnet flux. The chord appears inside the Ld/Lq notes as a comparison, never
  as "Ld".
* The datasheet prints Ld/Lq at 20 °C no load next to KV.
* The Simulation card shows the incremental values with the method in the
  HelpTip and the chord quoted there for comparison.

Summary keys: `Ld_mH` / `Lq_mH` (**now the incremental values**), `Ld_inc_mH`,
`Lq_inc_mH`, `Ldq_inc_mH`, `ldq_method`, `Ld_chord_mH`, `Lq_chord_mH`,
`psi_pm_frozen_Wb`, `psi_pm_q_frozen_Wb`, `psi_pm_sag_pct`, and the solver's own
`inc_ldq` block. A run made before this change carries none of them, and the
summary says so rather than falling back to the chord.
