# Isolated continuous-angle P2 L2 mortar prototype (2026-09-24)

The prototype is `scripts/continuous_p2_mortar_prototype.py`; production
`p2_projection.py` and `fem_solver_2d.py` are unchanged. No FEM solve, API call,
mesh refinement, or harmonic filtering was used.

The reduced coordinate set is fixed: apply the existing signed P2 radial-cut
relations once, retain all other non-rotor trace classes, and eliminate the
rotor ring vertices **and** edge midpoints. At slip angle `theta`, extend the
stator trace periodically for a full ring or with `bc_sign` per sector wrap.
For rotor and shifted stator P2 trace bases `Nr(s)` and `Ns(s+theta)`, assemble

```
Mrr        = integral Nr(s)^T Nr(s) ds
Mrs(theta) = integral Nr(s)^T Ns(s+theta) ds
T(theta)   = solve(Mrr, Mrs(theta))
P(theta)   = [static cut-reduced rows; signed rotor rows of T(theta)]
P'(theta)  = [zero static rows; signed solve(Mrr, Mrs'(theta)) rows].
```

The integral is split wherever a shifted stator vertex crosses a rotor edge.
On each overlap interval, three-point Gauss integrates the product of two P2
traces (degree four) exactly. The derivative uses analytic derivatives of the
stator shape functions; moving split-point boundary terms cancel because the
signed stator trace is continuous, including at a sector wrap. `Mrr` is
constant and Cholesky-factorised once. No small coefficients are discarded.

The isolated tests check all mathematically required properties: `Mrr` is
symmetric positive definite; integer shifts have the same signed constraint
space, reduced dimension, and Dirichlet columns as `SlipProjection.build(m)`;
the weak residual `Mrr*r-Mrs*s` is below `3e-13` for random positive and
negative fractional and wrapped shifts; the analytic derivative agrees with a
finite difference away from knots; full-ring periodic and sector ± signs hold
after positive and negative full wraps. The
unit-basis collocation case with a 0.0456 interior pointwise trace mismatch
has a nonzero collocation weak residual, while the mortar residual is below
`1e-13`. **Weak equality does not imply pointwise equality.** Twenty-seven
prototype and existing projection tests passed in 1.64 s.

This is a one-dimensional reference-trace result, not a validated physical
continuous-rotation solver. The existing P2 slip ring is a polygon of straight
chords: rotating one polygon by a fractional slot does not make its boundary
geometrically coincide with the fixed stator polygon. The prototype couples
their common uniform slot parameter and uses the constant arclength factor
that cancels in `T`; it does not quantify the geometric error of that mapping.
Before production, the actual mesh geometry/interface representation, torque
energy derivative (including `P'`), and nonlinear/eddy parity must be
validated. Dense Cholesky and the generally dense transfer rows are also an
unmeasured cost at real ring size; a sparse or local implementation would need
its own benchmark. No speed or physics-accuracy claim follows from these tests.
