# D85 point settling diagnostic — 2026-10-07

## Finding and owner disposition

The archived D85 point is the exact 6.47 A RMS, 1000 RPM case at coil 180 °C and magnet 150 °C, 60 steps per electrical period, eddy on, rotor eddy off, demagnetisation on. Its original solver result remains `steady_state=false`, `demag_settled=false`, `acceptance=false`, `published=false`: after 32 demagnetisation pre-pass periods, adjacent-period mean-torque drift is `0.0021284549` relative, above the solver’s unchanged `0.001` limit. Ripple drift passes at `0.0269879` percentage points against `0.127066` pp. Aggregate Br movement and image-history checks pass. The final pre-pass torque is 1.2380947554 Nm; the preceding one is 1.2407299843 Nm.

The owner has explicitly directed accepting the **residual drift for this exact point only**, describing roughly 0.2% as very small. Preserve the measured `0.212845%` residual and the original false solver flags in the record; this is an owner disposition, not a claim that the solver’s criterion passed. It does not change the global tolerance, qualify other operating points, or authorize publication/broad rating claims. The parent is recording this as a separate exact-hash owner acceptance.

## What the retained periods show

The raw reply contains 32 per-pass records. The reported Br image cycle is seven periods. Over passes 19–32, adjacent-period drift ranges up to `0.00248361` relative in torque (mean `0.00154166`) and `0.45064` pp in ripple (mean `0.14395` pp). Comparing each pass with the one seven positions earlier over this same window gives maximum/mean torque differences of `4.36e-6` / `1.63e-6` relative and maximum ripple difference `0.000845` pp. Passes 25–32 likewise match passes 18–25 closely. The evidence supports a repeatable seven-phase sequence in the stored aggregate torque/ripple data; it does not prove the cause or override the adjacent-period rule.

Aggregate Br movement is small by the later passes and the result says `moved_settled=true`. The reply does not contain per-magnet/per-element Br snapshots at every pre-pass, so it cannot localize which magnet or field feature drives the phase-to-phase torque differences. It records only the final `tdm.demag.br_map` alongside per-pass aggregate Br deltas and image-gap summaries. Do not infer a physical mechanism from those aggregate values.

The solver’s `cycle_periods=7` is computed from the Br relabel-map permutation cycle. Its TDM branch runs each pre-pass across one electrical period; if another pre-pass is needed, it relabels Br and carries the BDF2 eddy state through `_period_shift`. The archive reports `demag_seeded=false` and no seed parent. Source comments constrain Br-map continuation to sweep/coordinator use with compatible warm-cache and geometry; this record is not evidence of a standalone full-state continuation checkpoint.

## Provenance

The point request SHA256 is `8a2ad9ca2015ceb2176cf687e7cd453741e07b086fd4edb25225ea8c7f068f94`. The gzip response SHA256 is `8171353593740c74f9a6e87d4b8ec5a0fb2a20d307bd055b502edd3821168742`; decompressed JSON SHA256 is `ea219a2a088ec682ef39769493e62597ba55d44d49d75764c1cc2adf2fe3a2af`. These match [the preserved rejection audit](d85-eddy03-rejection-audit-2026-10-07.md). The solver SHA recorded for the run (`cca2567aee37e2face2bfde85ca9ee2007427275ad7702ffdcb67b6b3ccef86d`) was verified against the preserved `scratch_s1_20261006/d85_finish_20261007/code5_fem_solver_2d.py` and release snapshot `src/motor_ai_sim/simulation/fem_solver_2d.py`; this is not the current root solver hash.

The archived solver reports 60 steps requested and used, no snap, 120 slip nodes per period, and a cogging minimum of 72 steps. The source’s existing whole-node rule permits divisors of the slip-node ring, so 120 is a source-compatible temporal-resolution comparison on that ring if a separate discretisation question later arises. It would be a different run and is not needed to resolve the owner’s explicit acceptance of this exact residual.

## Decision

No further solver run is proposed for this exact point. Keep the raw response and original false solver flags immutable; attach the owner’s acceptance only to the exact request/source/response hashes above. Any later use must still respect all other recorded physical, nonlinear, thermal, mechanical, and provenance gates. Do not widen `SB_DEMAG_SETTLE_TOL`, rewrite the stored result, or infer a maximum/rating from this one point.

## Source locations checked

Matching preserved solver source: pre-pass tolerance/cap around lines 7400–7427; image-cycle and adjacent drift/acceptance logic around 7525–7610; sweep-only Br seed around 5650–5710; TDM pre-pass and continued eddy-state handling around 10420–10480; result seed/status fields around 13060–13075. Saved result: `scratch_s1_20261006/d85_finish_20261007/configure_eddy03_solve003.response.json.gz`.

This was a read-only audit. No solver, API, or FEM run was launched.
