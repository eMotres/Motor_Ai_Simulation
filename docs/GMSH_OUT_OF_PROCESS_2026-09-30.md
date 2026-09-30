# gmsh out of process (2026-09-30)

## Why

gmsh is GPL-2.0-or-later. Its own exception list does not cover Intel MKL,
which this API process links (via `pypardiso`) under an AGPL section 7
exception we are adding for our own code (PR #93). For that exception to
hold, the API process must never `import gmsh` or link `libgmsh` in-process —
gmsh has to run as a separate program, communicating at arm's length over
files/pipes/CLI args. This also makes the platform more robust: a gmsh/OCC
native crash (a C++ exception, or a real segfault) can no longer take the API
process down with it.

## What changed

New module pair:

- `src/motor_ai_sim/simulation/gmsh_worker.py` — the API-process side. Never
  imports gmsh. Spawns/reuses `python -m
  motor_ai_sim.simulation.gmsh_worker_main` and talks to it over
  length-prefixed pickle frames on stdin/stdout. `call(func_ref, args,
  kwargs, timeout)` blocks for the answer; a crash raises
  `WorkerCrashError`, a timeout raises `WorkerTimeoutError` (both
  `WorkerError`) — there is no silent fallback to another mesher.
- `src/motor_ai_sim/simulation/gmsh_worker_main.py` — the worker process.
  The only file in the codebase allowed to `import gmsh`. Dynamically
  imports and calls whatever `"module:function"` it is asked to run.

Every function that used to `import gmsh` and build a mesh directly in the
API process was split in two, keeping the public name as a thin forwarding
wrapper and renaming the untouched original body to `..._impl`:

| Public wrapper (API process) | Worker-side impl (subprocess) | File |
|---|---|---|
| `_mesh_single_polygon` | `_mesh_single_polygon_impl` | `simulation/mesher.py` |
| `build_mesh_from_polygons` | `_build_mesh_from_polygons_impl` | `simulation/mesher.py` |
| `build_stator_mesh` | `_build_stator_mesh_impl` | `simulation/mechanical/modal.py` |
| `_build_rotor_mesh` | `_build_rotor_mesh_impl` | `simulation/mechanical/rotor_stress.py` |
| `_mesh_piece` | `_mesh_piece_impl` | `simulation/static3d/band.py` |
| `build_section_mesh_2d` | `_build_section_mesh_2d_impl` | `simulation/static3d/motor_mesh.py` |
| `sphere_in_box`, `cylinder_in_box`, `sphere_with_iron_shell`, `tube_in_box`, `cylinder_with_iron_ring` | matching `..._impl` | `simulation/static3d/meshes.py` |

The `..._impl` bodies are **byte-for-byte the pre-existing gmsh code** —
this was a boundary change, not a rewrite of the meshing logic. Every
`import gmsh` in the tree is now lexically inside one of those `_impl`
functions (or the small `_gmsh_start`/`_handshake` plumbing helpers, or
`gmsh_worker_main.py` itself); `tests/test_gmsh_process_boundary.py` asserts
this with an AST walk so a regression (a future PR adding a direct gmsh call
back into API-process code) fails CI instead of silently reopening the
licence gap.

## Wire format: pickle, not pure JSON

The brief sketched a JSON geometry/size-field protocol for a future CDT
backend. The *existing* functions here already pass and return native
Python objects — Shapely polygons, dicts of them, scikit-fem `MeshTri`,
numpy arrays, small project dataclasses (`MotorSection`, `RegionSpec`,
`TaggedTetMesh`). Re-serializing that whole object graph to JSON would have
been a much larger and riskier rewrite than the process boundary itself, for
no isolation benefit — the worker is our own trusted subprocess, never fed
untrusted external input. `gmsh_worker.py` documents this trade-off; a JSON
protocol can be layered on top of the same subprocess later without
changing its public surface.

**One real bug this flushed out**: `build_mesh_from_polygons`'s third return
value used to be a closure (`def _classify(x, y): ...` nested inside the
function, capturing `small_polys` and the radial bounds). Closures are not
picklable, so the very first non-trivial mesh crashed the worker trying to
send the response back (`AttributeError: Can't pickle local object
'_build_mesh_from_polygons_impl.<locals>._classify'`). Fixed by turning it
into `_RadialDomainClassifier`, a module-level class with the same captured
state as plain attributes and a `__call__` — same behaviour, picklable. This
is exactly the kind of latent issue the out-of-process boundary is supposed
to catch early rather than have a caller in production discover it.

## Determinism / provenance

- The worker subprocess is spawned with `OMP_NUM_THREADS=1`,
  `MKL_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`, `GMSH_NUM_THREADS=1`,
  `NUMEXPR_NUM_THREADS=1` set in its environment, so gmsh/OCC stay
  single-threaded — the same determinism promise as before (bit-identical
  output for the same input in the same environment), now enforced at the
  process boundary instead of relying on every call site remembering it.
- `gmsh_worker.handshake()` reports the worker's `gmsh.GMSH_API_VERSION` and
  interpreter version without meshing anything, for provenance logging.

## Performance (measured on this workstation, Windows, gmsh 4.15.2)

| Path | Time |
|---|---|
| Fresh subprocess: process start + `import gmsh` (`measure_cold_start_s`, 3 samples) | 1.06 s / 1.13 s / 1.77 s |
| Persistent worker, first call (spawns + imports gmsh) | ~0.92–0.98 s |
| Persistent worker, reused call (pipe round trip only, no gmsh work) | 0–16 ms |

Cold start is far above the ~0.3 s figure in the brief — on this machine,
Windows process creation plus `import gmsh` is closer to a second, not tens
of milliseconds. That number makes the persistent-worker default (already
the default in `gmsh_worker.py`; `MOTOR_AI_SIM_GMSH_WORKER_MODE=fresh`
forces a new subprocess per call, used only by the crash/timeout tests) the
right call for the API process: the cost is paid once per API process
lifetime, not once per mesh, and the steady-state overhead (single-digit to
low-double-digit milliseconds) is negligible against an actual mesh
generate (the fixture meshes in the test suite run 1–20+ s). Numbers should
be re-measured on the target Linux server (`ssh -i ~/.ssh/motres_ax42
root@176.9.84.229`) where process spawn is typically cheaper than on
Windows; nothing in the design depends on the exact number, only on it being
non-negligible against a single Python interpreter start, which is why
persistent reuse is the default rather than a tunable.

## Fail-closed behaviour (tested)

`tests/test_gmsh_process_boundary.py` exercises, against the real worker
subprocess (gmsh installed, not mocked):

- a normal Python exception in the worker → `WorkerError` with the original
  message and traceback text;
- a hard crash (`os._exit(1)`, standing in for an OCC/gmsh native crash a
  `try/except` cannot catch) → `WorkerCrashError`;
- a call that never returns → `WorkerTimeoutError` after the given timeout,
  and the stuck subprocess is killed, not left running;
- an unresolvable `func_ref` → `WorkerError`, proving `call()` never
  silently substitutes another mesher or returns a degraded result.

Plus the static AST guards (no gmsh installation needed) described above,
and an end-to-end mesh call that asserts `'gmsh' not in sys.modules` in the
calling process afterward.

## Existing gmsh mesher tests, run through the worker

All pass unchanged (no test file edits beyond the new
`test_gmsh_process_boundary.py` and `_gmsh_worker_crash_helpers.py`):
`test_mesh_needle_repair.py`, `test_mesh_feature_floor.py`,
`test_mesh_shaft_region.py`, `test_conductor_skin_mesh.py`,
`test_family_activate_mesh_sync.py`, `test_mechanical_modal.py`,
`test_mechanical_rotor_stress.py`, `test_mechanical_runaway.py`,
`test_static3d_stage_a.py`, `test_static3d_stage_b.py`,
`test_static3d_nedelec.py`, `test_static3d_spike.py` — 230+ cases, 0
regressions once the `_classify` pickling bug above was fixed.

## How this merges with PR #91 (gmsh-as-default mesher)

PR #91 (`feat/gmsh-default`, rebasing #49's `geo_mesh_gmsh.py` CDT backend)
changes *which geometry pipeline* produces the mesh — it is not based on
this branch, so `geo_mesh_gmsh.py` does not exist here yet. This branch
changes *where* the gmsh call runs, for both the legacy `mesher.py` path and
(once `geo_mesh_gmsh.py` lands) the new CDT backend — the same
`gmsh_worker.call(...)` pattern applies there: rename its entry
function(s) to `..._impl`, add a wrapper of the same name. The two PRs touch
different functions in `mesher.py` (this one only renames/wraps, does not
change meshing behaviour) and disjoint new files
(`geo_mesh_gmsh.py` vs `gmsh_worker*.py`), so the conflict surface on rebase
should be small; whichever lands second should rebase the other's entry
points onto `gmsh_worker.call` rather than leaving a second in-process gmsh
call site.
