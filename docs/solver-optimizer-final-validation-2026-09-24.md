# Optimizer final angular validation

At solver HEAD `821f3df`, optimization candidates use three raw samples per
cogging cycle. The six-case frozen comparison showed this is a screening
resolution: A-loaded raw ripple peak-to-peak differed from six-sample standard
by 1.745%, and iron loss by 6.55%. Candidate metrics therefore remain
preliminary until a separate standard evaluation passes.

The four geometry-search completion paths (auto CMA, auto screening descent,
manual gradient, manual CMA) now re-evaluate baseline A, current-bump B, and
the coarse incumbent plus up to two other top candidates at explicit
`sampling_purpose="standard"`. Both baseline points are recalculated before
the perpendicular objective F is rescored. The final winner is selected from
the validated shortlist, not copied from the coarse best. A failed solve,
unconfirmed nonlinear convergence, missing standard purpose, or insufficient
six-sample final-quality flag fails closed: no certified winner, no automatic
Compare save, and no Apply Best. Coarse points remain visible with a
preliminary label; only standard-validated finalists can be applied as picked
points. The status records that validation considered a shortlist, not the
entire cloud; it cannot certify a global six-sample optimum.

The one-off scan/sweep points and baseline preview are explicitly preliminary.
The Sweep chart and table disable Apply/archive for them, including old saved
results without a quality stamp. This fail-closed gate prevents publishing
three-sample values as a finished motor; on-demand standard validation for a
picked sweep point is still needed to restore that workflow. Standalone
`/refine` and `/descent/baseline` remain coarse optimization results and are
not covered by winner finalization. A missing `picard_converged` stamp now
fails the FEM evaluation rather than being inferred as successful.
No FEM or live API was run for this implementation.
