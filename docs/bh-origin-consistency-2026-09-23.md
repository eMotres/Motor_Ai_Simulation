# Low-field BH origin consistency — 2026-09-23

For a BH curve whose first positive-B sample omits the origin, static3d
co-energy already treats `(H, B) = (0, 0)` as an implicit point. The scalar and
vector permeability helpers previously clamped H to the first sample below
that point, so their low-field constitutive reading could disagree with the
co-energy curve. For example, the existing linear fixture starting at 0.1 T
gave μr=500 at 0.05 T in the vector helper, while the origin-to-first-sample
segment implies μr=1000.

The scalar and vector permeability helpers now interpolate along the straight
segment from the origin to the first knot only when the curve begins at B>0 and
the requested field is below that knot. Explicit-origin curves, values at and
above the first knot, the high-field μ0 continuation, zero/tiny-field cutoffs,
and negative-B demagnetisation curves retain their existing behavior. The
vector path gates its added mask on `bs[0] > 0`, so ordinary explicit-origin
curves do not incur an extra full-array pass.

`tests/test_bh_origin_consistency.py` covers linear and nonlinear monotone
missing-origin curves against both permeability helpers and the co-energy
functional, plus first-knot continuity, explicit-origin equivalence, the
high-field tail, and zero/tiny fields. Shipped steel curves currently include
an explicit origin; this closes a supported-input consistency case exercised
by a synthetic curve fixture. It does not establish or claim an overall torque
change.

Implementation model: GPT-6 Luna. No escalation. No FEM solve, live config,
API, or browser use.
