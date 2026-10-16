# Voltage drive on P2 — what it needs

> **Status: DONE.** Every item below is implemented in
> `simulation/fem_solver_2d.py` (`_v_newton`, `_v_picard`, `_circ_r`, `_circ_M`,
> the P2 phasor initialiser, the `_vskip` strip in the P2 branch). The P2 guard
> now rejects only `eddy=True`. `tests/test_physics_regression.py` pins the
> `p1_voltage` / `p2_voltage` pair. What the port measured is at the bottom.
> The frontend still auto-selects P1 for voltage drive — removing that fallback
> is a separate decision.

Written from a code read, not from memory, so the next session starts from a map
instead of a search. Line numbers are approximate and will drift; the names are
what to grep for.

## Why it matters

P2 is the calculation basis now — it is the only element order with an
energy-consistent mean torque *and* a mesh-convergent ripple, and since
`a1aedad` it does irreversible demagnetisation too. Voltage drive and the
coupled eddy J-view are the last two things that still force P1.

Until then the frontend picks P1 automatically when the user selects a voltage
drive (`TransientCharts.tsx`, `element_order`). That is deliberate: the route
used to silently coerce `drive = "current"` under P2, so the user asked for a
voltage drive and got a current-drive answer with nothing on screen saying so.
An honest fallback beats a silent substitution — but it is still a fallback.

## What P2 already has

Both building blocks are in place:

* `f_coil2['A'|'B'|'C']` — per-phase unit-current source vectors on the P2 basis
* `_psi2(A2)` — per-phase flux linkage of a P2 field

## What is missing

Everything below exists in the P1 path and has no P2 counterpart:

| P1 name | what it is | note for the port |
|---|---|---|
| `xa`, `xb` | unit-current field columns, `K x = f_coil` | needs the P2 stiffness and the frame's `Pro` |
| `A_pm` | PM-only field, so `A = A_pm + i_A*xa + i_B*xb` | P2 rebuilds `f_mag2` when demag fires — recompute `A_pm` with it |
| `_Ldd`, `_Lqq`, `_Ldq`, `_Lqd`, `_psi_pm_d` | phasor init: dq inductances measured AT the operating point | Lq changes ~5x from no-load to full load, so an i=0 estimate leaves a large DC |
| `_Mc`, `_bc` | per-frame Crank–Nicolson circuit solve, line-to-line form | the LL form is load-bearing: phase-voltage equations short the zero-sequence EMF through the tiny zero-seq inductance and produce ~43 % fake triplen current |
| `_vskip`, `_v_nspp`, `_v_bpsi` | 10 settling periods + iterated Aitken anchoring on the period-boundary flux | the electrical time constant spans many periods on a low-R machine |
| `_iv_prev` | previous-frame currents for the CN step | |

## Order of work

1. `xa`, `xb`, `A_pm` on the P2 basis, per frame (the slip pairing `Pro` changes
   every frame, so the columns must be re-solved or re-projected).
2. Phasor init — port `_assemble0` / `_fac0` to P2.
3. The per-frame circuit solve. Keep the line-to-line formulation.
4. Settling periods + Aitken. P2 already strips the demag settling period; the
   voltage one is a second, longer skip — do not let the two double-count.

## How to know it works

The same way the demag port was verified, and the only way that caught three
silent failures in one day: **run P1 and P2 at the same operating point and
compare**. A circuit is physics; it cannot depend on element order.

Compare, in this order of sensitivity:
* phase current waveform and its THD (the whole point of voltage drive)
* `v_dc_residual_A` — mean phase current over the reported window, ~0 on a
  converged periodic orbit. A DC residual means the settling did not settle.
* torque mean and ripple
* `v_drive_diag` residuals per frame

Add a `p2_voltage` case to `tests/test_physics_regression.py` before touching
anything else. Three improvements (iron loss, demag, the settling pass) were
added to P1 and silently missed P2, every time because that branch returns
before the code that was added. Nothing warns you — the numbers just quietly
come out on a different basis.

## Trap to avoid

The demag hook was first placed in the P2 Picard and never fired: P2 solves by
pointwise Newton and the damped Picard is only a fallback that does not run on
this machine. Anything that must see a converged field belongs after the
nonlinear solve, not inside one of the two solvers.

## What the port actually did, and what it measured

P1 gets the exact superposition `A = A_pm + i_A·xa + i_B·xb` for free because
its Picard freezes ν inside a sweep. P2 solves by POINTWISE Newton, where that
superposition is not exact, so `xa`/`xb`/`A_pm` are not the right primitives
there. Instead the circuit is closed on the ACTUAL ψ(A) of the converged field
and `(A, i_A, i_B)` are solved as ONE coupled Newton system: ∂A/∂i is the
tangent back-solve `J⁻¹·P` (the DIFFERENTIAL inductance), which is the exact
Jacobian of ψ(A(i)). One factorization, three back-solves per iteration. The
`_v_picard` fallback IS the P1 superposition recipe, kept for `frozen_nu` /
`SB_NO_NEWTON` / a collapsed line search.

30 mm 12s14p, F45SH_120C at 120 °C, 24 steps/period, mesh 1.4 mm, V = 7.0 V pk
at δ = +10 °el (≈ i_d = 0):

| | P1 | P2 |
|---|---|---|
| I_A fundamental | 82.67 A pk | 71.22 A pk |
| THD I_A | 5.79 % | 2.67 % |
| triplen share of I_A | 0.02 % | 0.00 % |
| `v_dc_residual_A` | −0.001 A | 0.000 A |
| circuit residual, max over frames | 2.3e-14 V | 1.8e-14 V |
| T_avg | 0.2550 Nm | 0.2365 Nm |

The circuit itself is identical on both orders (residual at machine precision,
no DC left on the orbit). The 14 % current gap is NOT a circuit difference: it
is the ~2.5 % P1-vs-P2 flux-linkage difference amplified by an operating point
where |V| ≈ |E|. Measured on P1, dI/dV at this point is **5.45** in relative
terms (+2.57 % on V gave +14.0 % on I), and 2.49 % of ωψ₁ is 0.149 V, which
predicts −10.2 A against the −11.4 A observed. Same amplification within P2
alone: switching only the ν model (pointwise Newton vs element-mean Picard,
`SB_NO_NEWTON=1`) moves T_avg 1.8 % and the current 5.6 %.

Two things worth knowing before trusting a voltage-drive loss number:

* `P_cu` still comes from `copper_loss_W(I_phase_rms)` — the CONFIG current, not
  the current the circuit solved. Both orders inherit this from P1.
* `v_drive_diag` is not trimmed by the settling strip, so its arrays are longer
  than every other reported series. Also inherited from P1.
