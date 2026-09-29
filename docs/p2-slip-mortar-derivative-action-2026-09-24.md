# Integer-slip P2 mortar motion derivative action (2026-09-24)

`SlipProjection.build(m)` is unchanged. The new reusable utility in
`p2_projection.py` is deliberately not called by `fem_solver_2d.py`:

```python
action = SlipMortarDerivativeAction(projection, spacing_rad)
dA_dtheta = action.motion_derivative(full_field_A, m_shift)
```

`spacing_rad` is the **mechanical radians** per slip-ring interval, passed
explicitly. `m_shift` must be an integer, including negative and multiply
wrapped shifts. `full_field_A` must satisfy the current integer projection and
sector cut constraints. The result is a full P2 vector with nonzero entries
only on rotor slip-ring vertices and edge midpoints. Its meaning is the L2
mortar action `P'(theta)a` at `theta=m_shift*spacing_rad`; it is not a
derivative of the discrete integer-only `build(m)` function.

Preparation applies the existing signed radial-cut union to the rotor trace,
assembles its exact P2 one-dimensional mass matrix `Mrr` (nine local terms per
edge), and factors the sparse matrix once. At each call, the signed stator P2
trace on each aligned interval supplies the exact local action
`integral Nr^T dNs/dtheta ds`. A single trace solve
`Mrr * dr = Mrs'(theta) * stator_trace` gives the moving rotor coefficients;
they are scattered back with the cut signs. No dense `P`, `P'`, global mixed
matrix, coefficient filtering, or full FEM solve is involved. The geometry and
full-machine sector multiplier are untouched.

Five focused tests compare the action to the independently assembled
trace-only exact-overlap mortar derivative for full ring and sector signs ±,
including positive, negative, and multiple wraps. They also verify exactly
zero non-trace rows and the conservative quadratic energy finite difference
at an integer shift. The focused tests passed in 0.78 s.

One synthetic run16-sized benchmark (505 ring vertices, 17,299 total dofs,
1,008 rotor trace coordinates, `spacing_rad=pi/504`) measured:

| Quantity | Result |
|---|---:|
| Untraced setup | 0.030 s |
| Action median, 20 calls | 1.254 ms |
| `Mrr` nonzeros / CSC storage | 4,032 / 52,420 bytes |
| Full output vector | 138,392 bytes |
| Traced Python/NumPy peak during setup and action | 1.56 MB |
| Nonzero entries outside rotor trace | 0 |

The peak omits possible native SuperLU allocations and the rest of a real FEM
job. These timing and memory observations are not motor physics validation.
This action only addresses the projection motion term at existing integer
frames. A real torque derivative may also contain explicit derivatives of the
field energy/source, material state, circuit state, and geometry. The
fractionally rotated polygonal interface remains unvalidated, and this helper
does not change production torque or ripple calculations.
