# P2 slip-projection cache review — 2026-09-23

Read-only caller and schedule audit for a possible per-solve memo of
`SlipProjection.build(m_shift)`. No solver code, FEM run, test, API request, or
browser action was performed.

## Caller mutation audit

There are three direct call sites in `fem_solver_2d.py`:

- Voltage phasor initialization builds shift 0 at line 5240. The projection is
  only read for transpose-times-vector, sparse stiffness projection, and
  padding the solved vectors (5241–5262).
- The transient frame loop builds its rounded integer shift at line 5852. The
  matrix is only read by sparse products and `Pro.multiply(Pro)` when mapping
  the previous field to the current constraint manifold (5866 onward, notably
  5899–5901). `outer_red` is only read by `np.setdiff1d` at 5869.
- The dq diagnostic builds integer probe shifts at line 7458. Its projection
  is only read by sparse products and field padding (7459–7475); its outer
  columns likewise only feed `np.setdiff1d`.

No caller mutates `Pro` or the returned outer-column array in place. This makes
sharing an immutable cached `(Pro, outer_red)` result safe for the inspected
call sites. The cache must remain attached to the one `SlipProjection`
instance: its topology, dof maps, ring pairing, sector mode, and sign are all
captured by that instance.

## Scheduler repeat counts

The normal non-mixed schedule is uniform (`fem_solver_2d.py:4573–4579`), and
the frame shift is `round(theta / spacing)` (`5773–5777`). The mesh snaps the
requested steps to a divisor of nodes per electrical period (`3538–3584`).
With the low-level function defaults (`gap_layers=3`, `n_steps_per_period=12`,
`n_periods=1`, `n_sectors=-1`), the documented formula gives 240 nodes per
electrical period and 1680 nodes around a full ring (`3480–3512`,
`8212–8213`). The 14-pole motor has seven pole pairs, so shifts advance by 20
ring nodes per frame:

`0, 20, 40, …, 220` — 12 distinct shifts, zero repeats in the frame loop.

This is an arithmetic derivation from the checked-in defaults and snap rules,
not a measured mesh run. For a general full-ring run, repeats occur only when
the sampled span wraps the ring or two successive rounded positions coincide;
the normal snapped schedule avoids the latter. A one-entry last-value cache
therefore has no hit in the default current-drive schedule. It does give a
single definite reuse in voltage drive: initialization's `build(0)` at 5240
and the first frame's `build(0)` at 5852 are separated by no other `_proj.build`
call. That is a correctness-safe but narrow opportunity. The dq probe calls
occur after the frame loop, so a last-entry cache would only hit a probe if its
shift equals the final frame shift; no such overlap is established by the
default schedule.

The PWM mixed-resolution scheduler also advances angle in whole snapped ring
increments (coarse/fine steps are derived from the same period grid). Whether
its coarse and fine sets overlap depends on their configured step counts. No
configured default PWM schedule was available in this read-only audit, so no
repeat count is claimed for that mode.

## Recommendation

Do not add a generic multi-entry sparse-matrix cache based on this evidence:
the default current-drive schedule has no repeated shifts, and retaining many
full projections could raise memory use. If a future change targets the
voltage path, a one-entry cache is sufficient to reuse shift 0 between phasor
initialization and the first frame. Before implementation, confirm actual
`Nring`, spacing, and schedule values in a cheap non-FEM scheduler/projection
fixture and preserve exact sparse matrices and outer columns. Any cache key
must use the exact integer shift unless canonicalizing full-ring or signed
sector turns is separately proven equivalent. Cache lifetime must end with the
`SlipProjection` instance.

The earlier candidate is not a general default-schedule speedup: under the
checked-in 12-step, one-period current-drive defaults, its expected cache-hit
count is zero. The voltage-drive shift-0 reuse is a limited exception.
