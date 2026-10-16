# Reuse PyPardiso's exact MKL discovery for optimizer subprocesses

Model: GPT-6 Sol, no model escalation. No solver formulas or native package
files were changed; no live API/configuration access or FEM run was performed.

The existing P2 solver already publishes its loaded MKL filename after its
first `pypardiso` import. This avoids repeated discovery when constructing
handles inside that process, but a fresh optimizer child still repeats the
recursive installation scan when the parent has never loaded PyPardiso.

`simulation/pardiso_runtime.py::pardiso_subprocess_env` lets the original
package discover its library once in the parent, under a lock, and returns a
fresh child environment containing the same existing absolute library path.
There is no hardcoded machine path or independent library-selection algorithm.
The parent environment, installed package and persistent configuration/cache
files remain untouched. A loaded library name without an absolute existing
filename is not propagated. Explicit overrides, including an empty value,
remain authoritative; `SB_NO_PARDISO=1` avoids the import. Failed discovery,
unknown package internals or removal of a cached library leave the normal
child loader behavior intact.

Implemented integration in `_base_eval_env` in `routes/optimization.py`:

```python
from motor_ai_sim.simulation.pardiso_runtime import pardiso_subprocess_env
return pardiso_subprocess_env(env)
```

The call runs after the existing workspace/thread/seed environment construction.
The final-validation agent integrated it sequentially through the parent;
the resulting import and return call were checked in the working-tree diff.
The helper is
for children using the same Python/runtime installation, as the existing
`sys.executable -m ...refine_proc` launch does.

## Verification

12 focused tests passed in 0.59 s. They cover one discovery for repeated and
concurrent launches, unchanged input/parent environments, preserved overrides
and disabled backend, failure/unsupported-path fallbacks, and deleted cached
files. Tests use doubles and do not import a native library or configuration.

The bounded startup benchmark alternated three fresh interpreter runs per
mode. Each child also solved the same 2x2 sparse system and freed native memory.

| Measurement | Result |
|---|---:|
| Parent discovery, paid once | 2.7591 s |
| Median child import, normal discovery | 2.7956 s |
| Median child import, inherited exact path | 0.33185 s |
| Import-only speedup | 8.42× |
| Import time saved per subsequent child | 2.4638 s |
| Sparse residual, all six children | 0 |

All six children loaded the same actual DLL and returned bit-identical
solutions. The first job also pays the parent discovery, so this is an
amortized worker-startup improvement, not a claim that a first request or full
FEM solve is 8.42× faster. The prior profile's approximately 5.2 s scan was not
reproduced as that exact duration: this measurement used warm OS filesystem
caches and observed approximately 2.8 s total import.

Provenance and reproduction: `scripts/bench_pardiso_startup.py`; raw result in
`C:/Users/vadim/.codex/visualizations/2026/09/16/01a0aaf0-a84f-7d23-9c5b-02df5147e1d6/solver-mkl-startup-2026-09-24/startup-benchmark.json`.
The result records Python version, executable, actual DLL, helper SHA256,
per-trial timings, thread count and sparse-solve checks. No long FEM suite is
needed for this environment-only change.
