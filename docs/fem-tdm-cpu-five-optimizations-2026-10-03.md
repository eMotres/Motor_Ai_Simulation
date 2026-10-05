# L155: five CPU acceleration items

Status: complete; four-file patch applied to the clean TDM worktree, uncommitted. Author: Codex; no Sonnet/subagents/model escalation. Owner authorized all five items. Only CPU changes: no new solver, GPU, FP32, altered physics, mesh/tolerances/sampling/window policy, filtering or magnet-cell exclusions. Live API8001 and production untouched.

## Source and provenance

TDM is in `C:/Users/vadim/Projects/motor_ai_sim_wt_tdm`, branch `feat/tdm-prototype`, base `1e41f518db30350b7908706447f049b34c8d7152`. Shared main checkout has an older pipeline without TDM; these changes must not be transplanted wholesale there. Candidate initially developed and tested in an isolated complete source copy.

Baseline source SHA256: `d31d218e175c1c188c081a77674bfccb50705e4f6e4a8eeee8c9417070f41091`.
Final candidate source SHA256: `36459a882dee00d4f34f9f6f960f9e0b0390c99b9da1457b7bb8b5da0bc2a1ff`.
Geometry SHA256: `2f3147b12198050d50e60372641cb9038f157399b69e2e378c4209f331db574c`.

Frozen input set: `scratch_perf/tdm_l155_inputs_20261002_02/`. CIANO10 200 opt / L155 motor, delta terminal current converted to winding phase current. Rated324.5095337526054Arms,14200rpm,gamma15°,coil97.8°C,magnet104.2°C. Peak444.8309722481421Arms,20000rpm,gamma15°,coil139.2°C,magnet165.1°C. Mesh4mm/min0.3mm/two sectors, full demag reference. Rated automatically raises gap layers1→4 under the original Coulomb gate; complete timings include both attempts. Peak original uses1. Manifest/result files retain hashes, temperatures/materials, actual snapped sampling, native thread settings, convergence/gates and every raw field/cell.

Windows Python3.11.9 / pypardiso0.4.7 / FP64. Diagnostic jobs sequential, BelowNormal, hidden,900s each. Some jobs exceed5minCPU; approved by orchestrator under explicit owner request. No multi-hour geometry sweep.

## Outcome by item

| Item | Implementation / decision | Limit |
|---|---|---|
|1. Fixed CSR assembly|Cache immutable reduction plans for stiffness and tangent separately. Reuse exact row/column reduction order; per-call values and owned CSR buffers.|Exact zero masks can change with saturation. Keep first two plans per assembler, then original COO fallback; never force a common sparsity pattern.|
|2. Fewer temporary arrays|One per-call quadrature scratch array with `multiply(out=...)` and `sum(out=...)`; flatten view instead of unconditional copy.|No shared writable buffers between TDM workers; preserve original multiplication and reduction order.|
|3. PARDISO analysis reuse|Inspected actual phases/patterns and existing guards. Existing same-pattern numeric reuse remains unchanged.|No consecutive same-pattern analysis found in initial capture. Revisiting an older pattern requires analysis of current handle; no speculative phase22 substitution.|
|4. Geometric projections|Run/mesh-local cache of projection and free DOFs keyed by exact integer slip.|No modulo/angle approximation. Currents, excitation, history, material and demag states still computed every call. Negative/multi-turn slips remain distinct.|
|5. Worker/native threading|Compare final-source2×1,4×1,2×2 plus matched late baseline. Validate fastest observed setting on peak.|No blanket global default change: outer-job concurrency and workstation hardware affect safe thread budget.|

`csr_scatter.py` uses SciPy native sparse conversion/sort helpers to reproduce duplicate-summation order exactly. It skips sorting already-sorted rows, just as original SciPy does. Native private interfaces therefore need the provided regression tests whenever SciPy is upgraded. Zero contributions removed before duplicate summation as originally; a stored zero caused by cancellation of nonzero contributions stays stored. No physical signal filtering is introduced.

## Verification and rejected variants

Final candidate:52 tests passed31.58s —47 unchanged original nonlinear/P2/projection/TDM tests plus5 new exact scatter tests (mask changes, fallback, cancellation zeros, empty rows, owned buffers, parallel calls, already-sorted duplicate sums).12 actual captured L155 skeleton matrices replayed with bit-identical pattern and entries. Warm local kernel medians1.49–2.28x faster; this is **not** whole-transient speedup. Constructor timings exclude lazy first-plan construction. Sample cached-plan storage2.1–3.4MB/skeleton; separate assemblers can retain additional plans.

Rejected original-order `bincount`: entries differed~1e-16 from SciPy summation order. Initial prototype test failure: new `_Skeleton` slot missing (12failed/35passed); repaired before changing real source. Additional sorted-duplicate adversarial test caught unconditional resorting (difference0.001953125 in an artificial ill-scaled case); corrected, regression retained.

First combined prototype full-rated:446.045s wall603.188sCPU vs initial control381.642/501.500; bit-identical raw physics, but no speed gain accepted. Second prototype separated caches and stopped rebuilding:446.175/589.844; bit-identical raw physics;20 plans created for14385 stiffness/tangent calls. These timings are preliminary: short regression tests overlapped, and unchanged native PARDISO time itself drifted~24%. Final thread ranking uses the later isolated final-source series only; negative results remain archived.

Initial PARDISO capture:245 phase11,399 phase12,1040 phase22,636 phase23;0 consecutive same-pattern analysis;227 earlier-pattern revisits. Initial capture uses object ids (possible lifetime reuse); later capture assigns unique owners. Native total includes5327 phase33 solves and148 frees. Matrices44212–57235DOF /275928–371245NNZ. Current per-frame and SPD guards already reuse symbolic analysis. Removing required analysis or retaining more handles without a memory study would be a different change.

## Completed final measurements

| Full rated case | FEM wall, s | Process CPU, s | Peak working set, bytes |
|---|---:|---:|---:|
|Late original control2×1|412.906|537.469|4338212864|
|Final candidate2×1|291.833|378.375|4464496640|
|Final candidate4×1|365.893|596.953|4407328768|
|Final candidate2×2|301.933|772.094|4485652480|

Primary same-thread comparison: **29.32% less observed elapsed time**, CPU537.469→378.375s. Original controls varied381.642→412.906s; workstation load/temperature not isolated. These are measured runs, not a universal/server speedup guarantee. Day/night thread rankings are not controlled enough to establish a universally best configuration. On this workstation prefer **2 TDM workers /1 native thread**: final2×1 was fastest observed, used much less CPU and matched physical output bit-for-bit.2×2 is numerically validated and was faster than4×1 in the earlier pair, but did not beat the later2×1. Retain explicit `SB_TDM_WORKERS` / `SB_TDM_MKL_THREADS` settings; do not change global defaults or oversubscribe an outer optimization worker pool.

Same-thread2×1 physical comparison: all36 raw torque/flux/voltage/current/loss samples,28608 per-triangle demag coefficients,172905 demag-field numeric entries and14 summary entries **exactly equal**. Rated mean184.48509823133952Nm, raw ripple1.5100994656566917%, mean losses3820.097W. Gates passed; no unconverged-frame exclusions or sample filtering.

Matched kernel instrumentation (accumulated inclusive thread wall, not additive elapsed-time components): projection builds362→222,48.613→3.290s; stiffness8262→8265 calls,46.126→16.791s; tangent6135 calls,34.624→19.010s. Cache-plan setup20 calls/0.0398s. Counts can vary slightly with existing memo scheduling; no additional physics steps are removed. Peak working set rises~126MB in the matched2×1 pair. Changed zero masks use the original COO fallback; cache size is bounded.

Rated4×1:365.893s wall /596.953sCPU /peak working set4407328768bytes. All compared raw samples, all28608 per-triangle coefficients and172905 demag-field numeric entries exactly equal to control.

Rated2×2:301.933s wall /772.094sCPU /peak working set4485652480bytes. Mean torque delta5.172751116333529e-12Nm; raw ripple delta3.716804641840099e-12percentage points. Max raw torque-sample delta6.767209015379194e-11Nm; flux1.0595690991266338e-14Wb; voltage7.576161920042068e-10V; raw total-loss samples6.758591553079896e-8W; magnet coefficients3.4416913763379853e-15. Reported mean losses unchanged. Gates passed. Faster elapsed time costs more accumulated CPU than4×1; do not infer multi-job throughput from single-job wall time.

Peak final2×2:169.177s FEM wall,174.909s external clock including bootstrap; gate passed. Mean torque delta5.5706550483591855e-12Nm, raw ripple3.4940939031002927e-12pp, max raw torque-sample delta6.332356861094013e-11Nm, demag coefficient2.0161650127192843e-13. Mean reported losses unchanged. Prior peak reference337.572s was on a different run/day; do not call this a controlled50% gain. No second peak at2×1 was run: assembly exactness and complete rated2×1 verify source equivalence; complete peak2×2 verifies the higher-risk native-thread rounding case.

An earlier late-control attempt crossed a clock jump12:06→21:23 (probable laptop suspension). Exact own parent/harness/worker stopped and partial outputs preserved; its timing rejected. Restarted control as`late_baseline_02`. Diagnostic parent now checks actual wall-clock time and stops its exact worker at900s even if Windows wait timeouts exclude sleep. No multi-hour awake solve was intentionally scheduled. Final summary explicitly distinguishes completed runs and the rejected interruption.

Applied paths: `src/motor_ai_sim/simulation/p2_nonlinear.py`, `fem_solver_2d.py`, new`csr_scatter.py`, `tests/test_csr_scatter.py` in the TDM worktree. Patch checked against unchanged source hashes before application. Source text and parsed AST match the validated candidate; two existing files byte-identical, new helper has final CRLF normalized to LF. Separate actual/candidate hashes recorded. New test imported the actual applied module:5passed0.68s. Existing52-test candidate result retained. No other files, commit/push/deploy/restart touched.

| Item | Benefit (potential) | Complexity | Numerical risk | Evidence / remaining check |
|---|---|---|---|---|
|CSR plans|MEDIUM|MEDIUM|LOW after equivalence checks|Exact52-test suite and full fields; rerun tests on SciPy upgrades/other platforms.|
|Temporary buffers|MEDIUM|LOW|LOW|Original arithmetic order, owned buffers, raw curve equivalence.|
|Symbolic reuse inspection|LOW additional gain|LOW inspection|HIGH if forced incorrectly|Existing guards work; no additional reuse implemented.|
|Exact projection cache|MEDIUM|LOW|LOW|362→222 builds; exact raw rated output.|
|Worker/native tuning|MEDIUM|LOW|LOW numerical, MEDIUM resource|Full3-setting rated and peak2×2; hardware/outer-job throughput needs separate measured tuning.|

## Artifacts

Harnesses: `scripts/check_tdm_cpu5_20261003.py`, `scripts/tdm_cpu5_capture_20261003.py`, `scripts/bench_tdm_cpu5_replay_20261003.py`, `scripts/compare_tdm_cpu5_20261003.py`, `scripts/run_tdm_cpu5_series_20261003.py`.
Final-source results: `scratch_perf/tdm_cpu5_rated_final_4x1_20261003_01/`, `scratch_perf/tdm_cpu5_rated_final_2x2_20261003_01/`, `scratch_perf/tdm_cpu5_rated_late_baseline_20261003_02/`, `scratch_perf/tdm_cpu5_rated_final_2x1_20261003_01/`, `scratch_perf/tdm_cpu5_peak_final_20261003_01/`. Earlier baseline/prototypes and interrupted`late_baseline_01` preserved. Summary: `scratch_perf/tdm_cpu5_final_summary_20261003.json`; applied hashes:`scratch_perf/tdm_cpu5_applied_files_20261003.json`; review patch:`scratch_perf/tdm_cpu5_final.patch`. Journal: `coordination/agent-run-journal-2026-10-03-tdm-cpu5.md`.
