# Voltage drive on P2 — what it needs

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
