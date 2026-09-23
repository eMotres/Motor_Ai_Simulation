# B-H curve array preparation microbenchmark — 2026-09-24

## Fixture and checks

`scratch_perf/bench_bh_curve_prepare.py` calls the production
`field_ops._mu_r_from_bh_vec` and compares it with the same interpolation and
clamping operations given read-only `hs`/`bs` NumPy arrays prepared once per
curve. The real 21-point curve is `Somaloy_700HR_5P` from the checked-in
`config/materials_library.yaml`; a three-point curve is copied from
`tests/test_p2_nonlinear.py`. Test B vectors include zero and tiny-cutoff
values, every knot, interval midpoints, interior values, and upper
extrapolation. Lengths were 23,138 (the saved profile's free-DOF count) and
69,414 (three times that count, an explicit quadrature-size proxy, not a
measured solver array).

For both curves and sizes, all finite production/candidate values were
bitwise equal (maximum absolute difference 0), and NaN/positive-infinity/
negative-infinity classifications matched. The production helper does not
reject non-finite input; this check records that the candidate has identical
behavior. The arrays were immutable during timing.

One numerical-library thread; each median is 100 calls × 7 repetitions after
warm-up. Times are milliseconds per helper call:

| Curve, B count | Production | Prepared arrays | Array conversion only | `np.interp` only |
|---|---:|---:|---:|---:|
| 21 points, 23,138 | 0.128568 | 0.126924 | 0.002107 | 0.068519 |
| 21 points, 69,414 | 0.604810 | 0.506073 | 0.001815 | 0.204549 |
| 3 points, 23,138 | 0.124234 | 0.124244 | 0.000858 | 0.068869 |
| 3 points, 69,414 | 0.440656 | 0.445952 | 0.000873 | 0.526097 |

## Decision

The 21-point, 69,414-sample comparison shows a roughly 99 μs production vs
prepared difference, far larger than its separately measured 1.8 μs array
construction cost. The smaller-array result is consistent with conversion
cost, while the three-point cases show no gain. Treat the large discrepancy as
timing instability/host noise, not a speedup claim.

The existing `scratch_perf/r_base_4_both.json` profile reports 992 `Kpw` and
434 `tangent2` calls over 10 solved frames, but each method loops over
`sat_sub`, so it does not establish the number of BH-helper invocations. At
about 2 μs saved per helper call and one active sub-basis per method call, the
estimated saving is about 2.9 ms over the profile's 119.893 s, or roughly
0.0024%. More active sub-bases scale that estimate, but their count was not
captured.

**NO-GO for a production change.** If later profiling directly shows a
material cost, prepare immutable H/B arrays locally on each `P2Nonlinear`
instance and bind them to that instance's unchanged material curves. Rebuild
the instance when its curves change; do not use a global cache keyed by
mutable lists. `scratch_perf/bench_bh_curve_prepare.py` is an untracked
reproducibility aid. No production source was edited and no FEM was run.
Codex Luna; no escalation.
