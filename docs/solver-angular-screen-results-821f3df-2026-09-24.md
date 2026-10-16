# Angular-screen benchmark: 36 versus 72 raw frames

Read-only audit of `scratch_perf/angular_screen_821f3df_01` at solver HEAD
`821f3df`. The runner used one thread and the frozen 40 mm, 12-slot/14-pole
fixture; A has `magnet_fill_up=0.4`, B has `0.3`. Loaded current was 43.8 A rms
at 13,000 rpm and gamma 10 degrees; the no-load pair used 0 A. Each worker
requested 12 frames; optimization selected 36 (3 per cogging cycle), standard
selected 72 (6 per cycle). All six results report convergence and no mesh
build events. No new FEM run was made for this audit.

| Pair | 36-frame wall s | 72-frame wall s | Total speedup | 36-frame pre-main s | 72-frame pre-main s | Post-calibration main+post speedup |
|---|---:|---:|---:|---:|---:|---:|
| A loaded | 68.001 | 104.896 | 1.543x | 34.609 | 46.154 | 1.759x (33.392/58.742 s) |
| A no-load | 44.672 | 60.075 | 1.345x | 24.699 | 26.322 | 1.690x (19.973/33.753 s) |
| B loaded | 63.162 | 63.366 | 1.003x | 42.307 | 27.670 | 1.712x (20.855/35.696 s) |

`Pre-main` is wall time to the first main-frame callback. It includes startup,
preparation, and the separate 24-frame d-axis calibration. `Main+post` is
remaining wall time, including final loss/harmonic postprocessing. The three
main+post pairs collectively took 74.220 versus 128.191 s (1.727x). Whole
worker totals were 175.835 versus 228.337 s (1.299x), but this aggregate
mixes three different workloads and must not be sold as a per-candidate
guarantee. The 24 calibration frames explain why halving the main samples does
not halve total work.

| Pair | solve_ff calls 36/72 | tangent2 calls 36/72 | Kpw calls 36/72 | solve_ff inclusive s 36/72 | Kpw inclusive s 36/72 | tangent2 inclusive s 36/72 |
|---|---:|---:|---:|---:|---:|---:|
| A loaded | 563/785 | 539/761 | 1202/1718 | 31.107/53.268 | 8.408/13.339 | 5.856/9.445 |
| A no-load | 567/819 | 543/795 | 1208/1784 | 22.274/30.765 | 5.465/7.585 | 3.661/5.073 |
| B loaded | 596/825 | 572/801 | 1269/1799 | 28.105/32.167 | 6.822/7.991 | 4.693/5.439 |

These are whole-worker counts, including calibration. There were 60 versus
96 sliding-band builds (24 calibration plus 36 or 72 main frames) in each
pair. Stage timers are inclusive and are not a partition of wall time.

The sole cProfile sample is A-loaded optimization: 11.38 million calls over
67.935 s. Native `pypardiso._call_pardiso` consumed 30.791 s (625 calls);
`solve_ff` 31.107 s; `Kpw` 8.408 s; `tangent2` 5.856 s. Within `Kpw` and
`tangent2`, sparse assembly/multiplication is the next material cost. Lazy
`pypardiso` import took 5.206 s, including a recursive `glob`/filesystem walk
of 5.184 s. Mesh build was 0.535 s and slip projection 1.370 s. These
figures overlap; adding them would double-count. cProfile adds Python call
bookkeeping, and only one case was profiled, so its overhead is not
identifiable from this matrix. Even unprofiled B had 42.307 versus 27.670 s
to the first main frame. Consequently the main+post ratios are more informative
than whole-worker ratios, but still only one observation per case.

The 3x screen is **not final-quality output**: A-loaded raw Maxwell peak-to-peak
and ripple differ from 6x by -1.745%; A-loaded iron loss by -6.55%. B-loaded
peak-to-peak/ripple agree within 0.002%, but iron loss differs by -7.39%.
No-load cogging peak-to-peak differs by -0.274%, while iron loss differs by
-6.26%. A/B ordering remained the same on the measured mean torque, raw
peak-to-peak, ripple, and total loss, but this two-candidate test cannot prove
ranking of close candidates. The screening analyzer correctly returns
`screening_pass=false`.

## Implementation decision for a later change

Prioritize profiling/fixing linear-solve reuse and sparse `Kpw`/tangent
assembly before mesh or slip projection. Investigate the repeated 5.2 s
`pypardiso` DLL-search/import cost in isolated subprocesses without changing
numeric behavior. A process pool could amortize import/calibration but carries
state-isolation and cache risks; it needs its own A/B proof. Do not infer an
optimization opportunity from the inclusive timer remainder.

Current optimizer completion publishes coarse metrics immediately:
`_auto_worker` at `routes/optimization.py:5510-5539`, `_screen_worker` at
`:6094-6129`, manual gradient at `:3530-3542`, and manual CMA at
`:3915-3926`. `_auto_compare_point` at `:6185-6246` files the coarse winner.
The web store's `applyDescentBest` at `web/src/stores/motorStore.ts:1103` can
apply the interim best, while `applyDescentPoint` at `:1148` can apply any
coarse chart point.

The smallest *safe* two-stage design is a shared final-validation helper
called before publishing a final result: preserve 3x screening metrics and
provenance; re-evaluate a shortlist of finalists with explicit
`sampling_purpose="standard"`, exactly the same pinned geometry/operating
point/physics, and require convergence plus 6x final-quality sufficiency.
Also re-evaluate baseline A and current-bump B at 6x before recalculating the
baseline line and objective F, otherwise the finalist is scored against a
3x reference. Select only among validated finalists, mark unvalidated
interim results as screening, and do not automatically save a Compare winner
or expose Apply Best as certified on validation failure. A top-N shortlist
reduces the risk of 3x/6x rank reversal but cannot mathematically guarantee a
global 6x optimum without re-evaluating all candidates; state that limit.
Protect explicit picked-point Apply separately by labeling it as coarse and
offering a standard Simulation re-solve. Recheck the config fingerprint before
final validation to catch a workspace edit during a long search.
