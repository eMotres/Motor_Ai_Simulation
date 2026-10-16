# Trace-only P2 mortar prototype and run16-sized algebra benchmark

This is an isolated sparse/algebraic benchmark, not a FEM solve. The synthetic
sector has 505 ring vertices, 504 P2 edges, 17,299 total dofs, signed cut
endpoints, and angular step `pi/504`. Production files, geometry, and physics
were not changed; no coefficient was filtered by magnitude.

Run from the repository root with
`python -m scripts.continuous_p2_mortar_trace_prototype` (direct script
execution is also supported).

`scripts/continuous_p2_mortar_trace_prototype.py` assembles constant `Mrr`
as a sparse P2 edge mass matrix, factorises it once with SuperLU, and builds
`Mrs(theta)` and its analytic derivative only over the 1,008 stator trace
coordinates. Every rotor/stator edge intersection is integrated separately by
three-point Gauss. The resulting transfer and derivative are embedded into
global sparse `P`/`P'`, omitting only exact zero entries. Algebraic tests against
the earlier dense-global reference cover integer, positive fractional,
negative fractional, and wrapped angles for a full ring and signed sectors.
All 29 focused tests passed in 1.24 s.

| 505-node synthetic sector | Measurement |
|---|---:|
| Reduced global coordinates | 16,289 |
| Rotor / stator trace coordinates | 1,008 / 1,008 |
| `Mrr` / `Mrs` / `Mrs'` nonzeros | 4,032 / 6,048 / 6,048 |
| `P` / `P'` nonzeros | 1,033,362 / 1,017,072 |
| `Mrr` / `P` / `P'` sparse storage | 0.052 / 12.470 / 12.274 MB |
| Init / one angle build, no memory tracing | 0.212 / 0.341 s |
| One angle build with tracing / traced Python+NumPy peak | 1.718 s / 101.8 MB |

These are one local machine run, not a speed claim. `tracemalloc` does not
prove the total process RSS or native solver peak; the benchmark excludes FEM
stiffness, nonlinear iterations, eddy blocks, and the user's live jobs.

| Representation | Consequence at this size |
|---|---|
| Old dense global `Mrs`, `Mrs'`, transfer, derivative | One 1,008×16,289 float64 matrix is 131.35 MB; four are 525.42 MB before outputs. **NO-GO**. |
| Dense trace-only transfer | One 1,008×1,008 matrix is 8.13 MB; four are 32.51 MB. The measured prototype stays below the 0.5 GB target. **GO for isolated algebra testing**. |
| Threshold-free sparse global `P`, `P'` | 24.74 MB together, but `Mrr^{-1}Mrs` is nearly dense (about 1.0 million entries each). Sparse storage alone does not make angle builds cheap. **NO-GO for production without frame-cost validation**. |
| Saddle/dual mortar | `Mrr` and `Mrs` have only about 10,080 nonzeros combined; keeping them as constraints could avoid a dense transfer, at the price of about 1,008 additional unknowns and an indefinite system. A stable dual basis or bordered solver and energy derivative need separate proof and timing. **Unproven**. |

The polygonal chord sag at `r=9.1 mm`, `delta=pi/504` is
`r*(1-cos(delta/2)) = 44.20 nm`. This bounds the radial departure of **one**
chord from its circle; it does not by itself certify torque/ripple error or the
gap between two fractionally rotated polygonal traces. The reference-angle
mortar still requires geometry and energy validation before production use.
