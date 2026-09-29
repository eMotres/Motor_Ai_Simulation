# P2 performance checkpoint at 821f3df (read-only audit)

The reusable frozen six-case runner prepared after this audit is described in
`solver-angular-sampling-screen-runner-2026-09-24.md`. The hand-copy command
below remains historical planning context; use the reusable runner for the
paired optimization/standard comparison.

No FEM or API call was made for this checkpoint. The saved profiles predate the
explicit 3-sample optimization purpose, so they show a comparable 36-frame
workload, **not a measured speedup from the new policy on this HEAD**. Their
motor/geometry hashes and code versions must be checked before any A/B claim.

## What is measured

`scratch_perf/C_all_plain.json` records a frozen 40 mm, 12s14p current-drive
case: 36 solved frames in 43.90 s, 1.220 s/frame. The instrumented disjoint
primitives were `solve_ff` 10.34 s (360 calls), `Kpw` 8.27 s (735 calls), and
`tangent2` 5.81 s (348 calls): 24.42 s or 55.6% of the wall time together.
`asmK` was 0.24 s and `elemB` 0.29 s. The **remaining 19.48 s is unassigned**;
it includes geometry/mesh, constraint construction, other Newton bookkeeping,
postprocessing and Python overhead. It cannot honestly be called mesh time.

The same saved geometry (`18de320229be0596`) with demag+eddy in
`C_all_both.json` solved 74 frames (warm-up/prepass included) in 156.27 s.
`solve_ff` 29.06 s, `Kpw` 25.96 s, `tangent2` 18.61 s, and `elemB` 2.87 s
account for 76.50 s, 49.0%; 79.77 s is unassigned. The nominal 36-frame
request did **not** mean 36 solves. `r_base_4_both.json` is an older 10-frame
capture (119.89 s, 23,138 reduced DOF, 274,982 nnz) and exposes 4.895 s of
sparse multiplication, but it is not the current 36-frame baseline.

The solver result already reports `n_steps_per_period_requested`, effective
`n_steps_per_period`, `n_frames_solved`, `solve_wall_s`, sampling purpose and
sufficiency, `picard_iters_mean/max`, convergence/fallback lists and residuals.
The frame-history scalar channel has per-frame `picard_iterations`. The info
log has total frames/wall and `P2 cost`: number of linear solves, symbolic
analyses, Kpw assemblies/memo hits and perturbed pivots. The saved
`run_user.py` profiler adds wall, physics metrics, and total/call count for
`Kpw`, `tangent2`, `solve_ff`, `asmK`, `elemB`; it does not time geometry/mesh or
postprocessing separately. `bench_frame.py --profile 1` has a cProfile view,
but reads the active config and therefore must not be run unmodified here.

## Ranked next steps

1. **Measure the unassigned time on this HEAD** with cProfile and explicit
   boundaries around geometry/mesh, one-time assembly, frame loop, and loss/map
   postprocessing. Fewer frames make fixed setup cost a larger fraction, but
   the current saved profiles do not resolve it. Low physics risk to measure.
2. **Within the frame loop, inspect nonlinear work and linear solves.** The
   three measured primitives already take 56% of the plain 36-frame run.
   Compare per-frame iteration counts, Kpw memo hits, PARDISO analyses/solves,
   and fallback frames before changing convergence logic. Assembly-kernel
   improvements that preserve the same matrices have lower physics risk than
   changing Newton tolerances or skipping solves.
3. **Sparse projection/reduction** is a bounded secondary candidate. A saved
   coupled 10-frame run assigns 4.1% to sparse multiplication. The trace-only
   cached `Q` microbenchmark was positive on synthetic matrices, but the real
   P2 projection and free set have not been captured; validate exact sparse
   values and full solved fields before production use.
4. **Geometry/mesh and postprocessing** may matter more at 36 frames, but no
   isolated stage measurements exist. Profile before proposing reuse. The
   geometry is different at every optimizer candidate, so a cross-candidate
   mesh cache must key every shape/material/sector input and cannot be assumed
   safe. Rotor-eddy/loss maps should be timed separately, not inferred from
   `solve_wall_s`.

Existing work already removed the first obvious factorization and assembly
costs: `P2Nonlinear.solve_ff` reuses PARDISO symbolic phase 11 while the exact
matrix pattern holds; `Kpw` has a content-keyed one-entry memo and a cached
geometry skeleton. A previous FAST_LA eddy improvement measured 1.62× wall on
an older 200 mm fixture. Do not propose these again as unimplemented ideas.

Rejected/unsupported shortcuts: fully broadcasting `_Skeleton.stiff` was
8.8% slower and allocated a 14.16 MB temporary on the synthetic fixture;
preparing B-H arrays had no credible whole-run benefit; a general slip-matrix
cache has no hits on the ordinary monotone angle schedule; dense-global P2
mortar allocates over 0.5 GB and the near-dense sparse transfer has no frame
timing. Parallel cold frames lose the previous frame's nonlinear warm start,
and eddy/demag state is sequential. Coarsening wire geometry, relaxing
convergence, filtering torque or dropping angle samples would change the
physics and are not performance fixes.

## Next isolated profile, one run at a time (hard cap 300 s)

`scratch_perf/run_user.py` is the available frozen-geometry timer. Its current
default is `standard`, which would snap a 36-frame request up to the 6-sample
minimum. For a comparable **optimization** profile, make a disposable copy,
change only that copy to pass the explicit purpose, and keep its caches beside
the copied frozen config. In a dedicated PowerShell session while the owner's
machine is idle:

```powershell
$repo = 'C:\Users\vadim\Projects\motor_ai_sim'
$case = Join-Path $repo 'scratch_perf\profile_821f3df'
New-Item -ItemType Directory -Force -Path $case | Out-Null
Copy-Item "$repo\scratch_perf\run_user.py","$repo\scratch_perf\motor_config_frozen.yaml","$repo\scratch_perf\geo_frozen.json","$repo\config\materials_library.yaml" -Destination $case
$runner = Join-Path $case 'run_user.py'
$source = Get-Content -LiteralPath $runner -Raw
$source = $source.Replace('ROOT = Path(__file__).resolve().parents[1]', 'ROOT = Path(__file__).resolve().parents[2]')
$source = $source.Replace('n_steps_per_period=args.steps, n_periods=1.0, gamma_deg=10.0,', 'n_steps_per_period=args.steps, sampling_purpose="optimization", n_periods=1.0, gamma_deg=10.0,')
$source = $source.Replace('"wall_s": round(wall, 2), "n_frames_solved": ns,', '"wall_s": round(wall, 2), "n_frames_solved": ns, "effective_steps": res.get("n_steps_per_period"), "sampling_purpose": res.get("cogging_sampling_purpose"), "sampling_sufficient": res.get("cogging_sampling_sufficient"), "picard_converged": res.get("picard_converged"),')
Set-Content -LiteralPath $runner -Value $source -Encoding utf8
$env:MOTOR_AI_SIM_CONFIG = Join-Path $case 'motor_config_frozen.yaml'
$env:MKL_NUM_THREADS = '1'; $env:OMP_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'; $env:NUMEXPR_NUM_THREADS = '1'
$env:SB_NO_WARM_CACHE = '1'
Remove-Item Env:WORKSPACES_ROOT -ErrorAction SilentlyContinue
$python = 'C:\Users\vadim\AppData\Local\Programs\Python\Python311\python.exe'
$out = Join-Path $case 'plain.json'
$p = Start-Process -FilePath $python -ArgumentList @('-m','cProfile','-s','cumulative',$runner,'--mode','plain','--steps','36','--tag','821f3df-opt-plain','--out',$out) -WorkingDirectory $repo -RedirectStandardOutput (Join-Path $case 'plain.profile.log') -RedirectStandardError (Join-Path $case 'plain.err') -WindowStyle Hidden -PassThru
$p.PriorityClass = 'BelowNormal'
if (-not $p.WaitForExit(300000)) { Stop-Process -Id $p.Id -Force; throw 'profile exceeded 300 s' }
if ($p.ExitCode -ne 0) { throw "profile failed: $($p.ExitCode)" }
```

This is a plan, not a run performed for this note. The copied runner retains
the frozen `geo_frozen.json` and reports its hash. First verify 36 actual
reported frames, 36 solved frames for plain current drive, purpose
`optimization`, convergence, and the frozen geometry hash before comparing
timings. A separate `--mode both` run can use the same command with different
output names if the owner is idle; demag+eddy may solve more than 36 frames.
Each launch gets its own 300 s cap. cProfile adds overhead, so use the
instrumented JSON to rank code within the run and a second unprofiled run only
if an end-to-end wall comparison is needed. Never use the live API or
`config/motor_config.yaml` for this measurement.
