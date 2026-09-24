# Frozen angular-sampling screen: runner prepared, FEM not yet run

`scripts/bench_angular_sampling.py` freezes the 40 mm 12s14p source and
geometry from `scratch_perf/motor_config_frozen.yaml` and `geo_frozen.json`
into a **new private output directory**. It copies the material library and
complete `src` tree, verifies hashes, and gives each case its own workspace.
It copies no sweep/job/descent resume state and removes those files from the
private snapshot/workspaces if present. No API or live `config/` file is read
by a worker. Its six workers run **sequentially**, each BelowNormal on Windows,
with MKL/OpenMP/OpenBLAS/NumExpr/Gmsh restricted to one thread and a hard
300-second per-worker timeout. The command below has **not** been executed.

```powershell
& 'C:\Users\vadim\AppData\Local\Programs\Python\Python311\python.exe' scripts/bench_angular_sampling.py run-all --output-dir scratch_perf/angular_screen_821f3df_01 --timeout-s 300 --profile-case A_loaded_optimization
```

Run from `C:\Users\vadim\Projects\motor_ai_sim` only while the owner's machine
is idle. Use a new directory name for a repeat; the runner refuses a nonempty
directory. `--profile-case` is optional; the example saves one cProfile
`profile.pstats` and readable `profile_top.txt`. To re-analyze saved runs without
FEM:

```powershell
& 'C:\Users\vadim\AppData\Local\Programs\Python\Python311\python.exe' scripts/bench_angular_sampling.py analyze --output-dir scratch_perf/angular_screen_821f3df_01
```

The worker calls `em_transient_eval` directly on frozen inputs; it does not
exercise the optimizer HTTP/subprocess route. Its purpose parameter and
geometry/operating inputs match the planned optimizer comparison, while route
overhead and cache behavior are outside this benchmark.

The case matrix is A (`magnet_fill_up=0.4`) and B (`0.3`) at 43.8 A RMS,
γ=10°, 13,000 rpm, plus A at 0 A. Each runs with explicit `optimization`
and `standard` purpose after requesting 12 steps/period. On the frozen ring,
the expected actual counts are 36 and 72 respectively; a mismatch is saved
and then rejected. Eddy, demag and rotor eddy are off. The exact remaining
mesh, winding and source arguments are recorded in each result. A case
contains SHA-256 hashes of frozen source/config/base and changed geometry,
materials and runner, plus Git HEAD, actual count, convergence/mesh-build
provenance, all raw torque and Maxwell series, harmonics, per-frame/scalar
losses, and broad **inclusive** function timers. The progress callback only
marks each main frame's start. `wall_minus_sum_inclusive_s` is arithmetic,
not a partition of wall time: nested timers can overlap. Use the optional
cProfile output or explicit stage boundaries to attribute remaining cost.

The analyzer computes **raw Maxwell peak-to-peak directly from every saved
sample** and separately compares the solver's reported unfiltered ripple,
mean torque and losses. Screening gates are |Δmean|≤0.5% for loaded cases,
|ΔMaxwell pp|≤1%, |Δreported raw ripple|≤1%, and unchanged A/B ordering for
mean torque, pp, reported ripple and total loss. The no-load mean and ripple
percentage gates are labelled inapplicable near zero; its raw pp is checked.
These are candidate-ranking screens, **not final-quality certification**.
There is no filtering, harmonic deletion or sample thinning.

The old frozen 36-frame plain profile took 43.9 s and the demag+eddy profile
74 solved frames/156.3 s, but neither predicts these six new runs accurately:
the current HEAD, 72-frame standard path and per-case cold d-axis calibration
are different. Budget roughly 1–3 minutes per plain case as a planning
estimate, with an enforced maximum of 5 minutes **each**; the full sequential
matrix can take longer than five minutes in total. If a case exceeds the cap,
the runner stops without reporting a false completed comparison.
