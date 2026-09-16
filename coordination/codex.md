# Codex — solver audit fixes

## Active: raw cogging/ripple and symmetry validation (2026-09-16)

User requests convergence of cogging/ripple including full/half/quarter symmetry.
User explicitly requires NO torque filters: keep all computed content and fix
discretization/constraints rather than deleting harmonics. Previous filter
preservation tests are superseded by this explicit requirement.
Campaign sandbox: `solver-ripple-symmetry`; common-template 24s28p 100mm fixture
permits full/half/quarter, with fixed ring for angular refinement. A separate
12s14p control permits full/half only. No use of live defaults, API or stores.
Check winding/current symmetry as well as geometry; do not compare default
full geo-mesh against fallback template sectors as if their meshes were equal.

Raw-torque implementation sandbox: `solver-raw-torque`. Reserved narrow regions:
`simulation/field_ops.py` torque statistics/deprecated comb helper;
`simulation/fem_solver_2d.py` torque postprocessing/result fields;
`simulation/sb_postproc.py::torque_harmonics`; `contracts/adapters.py` torque
selection; `routes/simulation.py` legacy ripple/noise summary fields; corresponding
tests. Legacy filtered output aliases, if retained, must contain RAW samples.
No numerical-noise estimate is inferred merely from non-6k orders.
`web/src/components/simulation/TransientCharts.tsx` cleanup is prepared only in
the sandbox at this stage; do not mutate the owner's live browser or run server.
Report files are owned by another agent and will not be overwritten.
No commit/push/deploy/restart. Direct lead messaging still unavailable.

## Completed: integer P2 sector midpoint coupling (2026-09-16)

Integrated, uncommitted. `simulation/p2_projection.py::SlipProjection.build`
now maps sector intervals directly with divmod and the signed wrap count.
The previous vertex-derived edge pair omitted one midpoint weld at the cut,
including periodic sectors where the pair named a nonexistent facet. Actual
generated motor meshes confirmed a torn trace and one extra independent DOF.
Full-ring projections and zero-shift sector projections remain exactly equal.
No continuous-angle interpolation or torque formula change.

Owned additions: `tests/test_p2_projection.py`,
`tests/test_p2_projection_fields.py`; updated validation-plan documentation.
Exactly two existing no-load pins changed intentionally in
`tests/physics_baseline.json`: T_avg_Nm 4.25362258440225e-5 ->
4.1481998567548255e-5; T_ripple_pct 143.7852690020971 -> 128.72201908278132.
The cold original reproduces the former values; independent exact-field
verification establishes the corrected topology. Every other pin and all
RTOL/ATOL values remain unchanged. Independent review approved this scope.

Parent verification in isolated Python 3.11 processes, one thread and
BELOW_NORMAL priority, redirected state and write/network guards:
- Actual generated mesh gate: 3 passed, 15.47 s. Anti/periodic sector rings
  have 505 nodes; full ring 1008. All eight signed/multi-turn shifts satisfy
  complete fixed traces; original shifted sectors lose one midpoint relation.
- Portable unit + analytic field tests: 16 passed, 2.16 s.
- Independent old/new analytic oracle: 14 small solves passed in 0.77 s.
  Quadratic harmonic relative energy error 1.0267% -> 2.01e-15; L2 error
  0.01433 -> 7.38e-16. Fourth/sixth-order harmonic fields converge on refinement.
  First attempt failed JSON serialization of np.int32 metadata; fixed only
  artifact casts and reran successfully, with no numerical code change.
- Initial eight physics cases: 7 passed, 1 failed (no-load two pins above),
  6 unrelated tests deselected, 676.06 s. After the reviewed two-value reference
  correction, ONLY that affected case was rerun: 1 passed, 31.14 s.
- Four cold original/fixed motor A/B cases: 4 passed, 125.68 s (load, no-load,
  eddy, full-ring load). Actual maximum midpoint discontinuity ~5.24e-6 to
  6.19e-6 Wb/m -> zero; vertex/cut relations stay exact. Full-ring metrics
  identical. Loaded mean torque changes -2.73e-7 N m (~-0.000067%).
- Fixed no-load 12/48-step diagnostic: passed, 35.04 s. Mesh SHA256 identical.
  The coarse pin aliases the 12th electrical cogging order; 48-step amplitude
  is 0.0026128 N m and raw peak-to-peak 0.00982333 N m versus 5.33965e-5 at 12.
  This is a reproducibility pin, NOT a converged cogging-accuracy reference.

Immediately before integration, original projection SHA256
D2BD295FB7B240B821A0BD4D5D69C4C11FBF59798AF06F0201E6AD9ECA8E1380 and baseline
E244747BDFB70C710AD7990396C95F69B57F3610CD9725755DFFE8F533D934C7 still matched;
new test paths were absent. All four integrated files match tested bytes.
Manifest: `solver-projection-fix/integrated-hashes.json`; A/B, mesh and sampling
evidence in that folder. Analytic comparison: sibling `solver-projection-oracle`.
Path-limited git diff --check passed. An earlier unrestricted check encountered
foreign live-log whitespace and could not read uvicorn_8001.out; logs untouched.

No live API, project-config writes, restart, commit, push or deployment. Direct
Simulation_new messaging unavailable; this journal is not acknowledged delivery.

## Completed read-only report audit (2026-09-16)

User supplied `C:\Users\vadim\Downloads\CIANO10 200 opt L180 gen report.docx`.
Dedicated agent inspected all text, 45 OOXML tables and 35 embedded images;
parent checked principal text evidence, arithmetic and original SHA256.
Original unchanged: 93D096FC802B9B5B80F07E1DE3DFA7C0AB6D8F70CA7DC4C5AF4C1FC89B83B121.
Findings: peak convergence prose contradicts residuals; PWM loss comparison
is not a matched operating point; internal/bridge voltage labels are mixed;
peak current is RMS times sqrt(2), not waveform maximum; Lq/Ld is inconsistent;
contact opening threshold means 50% open, not first opening. These do not
establish solver faults. Main power/loss/mass/SF arithmetic reconciles.
Full evidence and limitations:
`C:\Users\vadim\.codex\visualizations\2026\09\16\01a0aaf0-a84f-7d23-9c5b-02df5147e1d6\report-audit\findings.md`.
Page layout unverified because LibreOffice renderer is unavailable.
No report generator edits, DOCX changes, new solves or live API actions.

## Completed optimization batch (2026-09-16)

Working in separate sandboxes on fractional-window torque filtering and cached
fixed geometry for the P2 torque integral. Reserved narrow regions:
`fem_solver_2d.py` torque-filter call / P2 torque setup and call,
`field_ops.py` P2 torque integration helper, new focused test files.
Only prior Codex modifications are present on the solver paths at start.
Independent physics review of nonsinusoidal torque and the 1 A switch is also
underway; no replacement physics is being integrated without validation.
No commit/push/restart/deploy. Direct messaging to Simulation_new unavailable.
Continuation also reserves `p2_drive.py::ve_newton` (fixed frame projection),
`eddy_solver_2d.py` (per-region frequency-independent assembly),
`sb_postproc.py` documentation only and new validation-plan/test files.
The batch is integrated, uncommitted. Every existing target still matched its
expected original hash immediately before integration; all seven new paths
were absent. Five integrated source files, six new tests and the design note
match the verified sandbox. Only two explanatory comment blocks in transient
were then corrected in both copies; no executable statements changed there.
git diff --check passed. Foreign changes were not overwritten.

Changes:
- Filter receives retained sample count times the scheduled angular step,
  not rounded requested periods. Fractional windows preserve their raw series.
- Prepare P2 annulus Basis and polar geometry once per transient call; every
  frame still computes a fresh field and the identical torque integral.
- Collect per-element eddy density history only for return_field=True.
  First/last snapshots retain the full reported averaging window; animation
  alone never consumed that history and still carries its A/B payload.
- Assemble conductor G via sparse CSC columns -> CSR, avoiding dense NxB stack.
- ve_newton uses per-call fixed free projection and projected mass matrix
  under existing SB_FAST_LA; disabled path preserves original expressions.
- Frequency-domain region histories reuse K/M, body constraints and free mask;
  every frequency still builds its own complex system and factors it anew.
  Public solve_harmonic_eddy wrapper and all output loss fields are preserved.
  The redundant frequency diagnostic was not silently disabled.
- Corrected hybrid_torque's universal energy/ANSYS/ripple claims in docs only.
  Its executable behavior and API labels remain unchanged.

Validation (separate Python 3.11 processes, one thread, BELOW_NORMAL, redirected
state, write/network guards; no live API):
- Combined main gate: 70 passed, 1 deselected, 89 existing skfem deprecation
  warnings, 661.68 s. Includes 57 field/filter/density/G/cache/voltage-eddy tests
  and 13 physics tests (all eight stored baseline cases plus warm-up/segmentation
  checks). Only the unrelated winding-source test was deselected; pins unchanged.
- Actual full-transient filter spy: 3 passed in 39.10 s. Requested 1.5/1.01/2
  periods give 18/12/24 frames and filter spans 1.5/1.01/2; fractional output
  equals raw torque/ripple, integer behavior unchanged.
- Harmonic cache independent oracle: 17 passed in 1.86 s. Complete combined
  follow-up: 19 passed in 68.08 s (those 17 + four cold map/animation solves in
  one check + p2_eddy physical baseline). All numeric scalar/series outputs
  compared across map modes match exactly; first/last cycle-averaged density
  maps match exactly and animation-only behavior is preserved.
- Independent source/math review of all six changes found no blocking issues.
- Sparse G traced extra allocation: 28.075 -> 1.061 MiB at 30k DOFs/120 bodies,
  18k nonzeros; CSR data/indices/indptr identical. Resident source vectors are
  excluded from this measurement.
- Torque helper microbenchmark: 12,800 triangles/25,921 DOFs, 20 different fields,
  7 alternating repeats; median 130.67 -> 5.63 ms (~23.2x), one-time preparation
  19.42 ms. Field results exactly equal. This is a local helper timing, NOT a
  measured whole-solver speedup. Background work affects wall-clock comparisons.
  First benchmark attempt passed numerical equality but failed writing numpy
  int32 to JSON; artifact writer fixed to int and rerun passed.

Combined verification folder: `solver-filter-fix` beside prior sandboxes;
raw files `filter-native-results.json`, `sparse-g-memory.json`,
`loss-map-native-results.json`, `integrated-hashes.json`. Timing artifact:
`solver-torque-cache/torque-cache-benchmark.json`. Harmonic original preserved
in `solver-harmonic-cache/original/eddy_solver_2d.py`.

Audit #3/#4 remain UNRESOLVED numerically: the fundamental space-vector torque
model does not cover general spatial harmonics, and the legacy 1 A selector
is not a general physical criterion. See `docs/solver-torque-validation-plan.md`.
Research-only continuous-trace prototype passed derivative tests (max relative
error 1.26e-9); exact production projection class on 45 synthetic topology cases
confirmed a shifted-sector constraint-range difference. Real-mesh torque impact
was not validated in that batch; the later integer-only correction is recorded
above. No continuous projection or torque-model change was integrated. Research
artifacts are in `solver-torque-physics`, outside the repository.

Commit: none. Push: none. Deployment/restart: none. Handoff not acknowledged
(direct Claude messaging unavailable). Separate read-only audit of the user's
DOCX report is complete; report generation and project configuration are untouched.

## Completed: series-strand current conservation (2026-09-16)

User asked to continue the audit list. Fixed the false zero-current rejection
in `src/motor_ai_sim/simulation/p2_drive.py`; added
`tests/test_p2_strand_conservation.py`. Both files are integrated, not committed.
Immediately before integration the drive source hash still matched the isolated
baseline and the new test path was absent. Both integrated files match the
tested copies (normalized line endings); git diff --check passed.

Each coil keeps its own Kirchhoff check with tolerance
`1e-12 A + 1e-8*abs(imposed) + 64*eps*n_paths*sum(abs(path currents))`.
This permits cancellation roundoff at zero current without borrowing tolerance
from a heavily loaded neighbouring coil. Nonfinite values/overflow are rejected.
The bordered equations, Newton convergence thresholds, solved currents, torque,
loss calculations and public settings are unchanged. Implementation by GPT-6
Astra; independent GPT-5.6 Sol source/test review found no blocking issues.

Verification sandbox:
`C:\Users\vadim\.codex\visualizations\2026\09\16\01a0aaf0-a84f-7d23-9c5b-02df5147e1d6\solver-series-fix`

- `verify.py tests/test_p2_strand_conservation.py -q`: 44 passed in 1.46 s.
  Actual small bordered systems, zero/tiny/positive/negative current,
  circulation, distinct coil scales, deliberately corrupted solve, k=1
  algebraic equivalence, both FAST_LA modes, NaN/Inf/overflow.
- `tests/check_series_motor.py` is an isolated audit artifact (not integrated).
  The final five geometry A/B checks passed across targeted invocations:
  original k=3 no-load failure reproduction; patched k=3 no-load completion;
  loaded k=3 before/after; loaded k=1 before/after with nonlinear and frozen-nu
  modes. Fixed 30 mm/12-slot/14-pole fixture, 15000 rpm, 0 or 60 A, 12 steps per
  period, cold eddy state, fixed material/geometry/winding arguments.
- Original zero-current run failed after two checked frames at an imbalance
  of 5.551e-17 A (old tolerance 1e-38 A). Patched run completed 27 checked
  frames; max imbalance 1.776e-15 A, peak circulating path current 5.784 A.
- All 30 saved metric comparisons across three original/patched loaded cases
  were exactly equal, including torque, voltage and losses.
- Native runs emitted existing skfem DiscreteField deprecation warnings.
  All execution was one thread, BELOW_NORMAL, isolated state and write/network
  guards. No live API access, restart, deployment, commit or push.
- Full eight-case physics-baseline suite was not rerun for this invariant-only
  change: those default single-strand/transposed cases do not exercise the
  changed series branch. Direct real-geometry series A/B checks were used.

Additional audit observation, not fixed here: nonlinear k=1 SERIES versus
TRANSPOSED returned copper AC loss 3.240 versus 3.277 W on this fixture.
A diagnostic asserting strict equality with frozen_nu=True FAILED:
T_avg 0.4788667779084834 versus 0.47885807498575894 Nm (8.703e-6 Nm difference).
Frozen-nu still converges its FIRST frame independently and then freezes each
mode's permeability, so it does not establish a common linear operator.
The modes have different residual scales/extra constraint rows; their stopping
behaviour is a plausible explanation, not proven causation. Original-versus-
patched k=1 is exactly unchanged in both modes of permeability. The invalid
cross-formulation exact-equality experiment was replaced by those strict A/B
tests; no production tolerance was widened to make it pass. Investigate this
preexisting cross-formulation difference separately with a common linear
operator or iteration traces. Raw observations: `real-motor-results.json`.

Simulation_new: direct messaging remains unavailable; this file does not claim
that the handoff was read. Previous PARDISO work below remains in the checkout.

## Completed PARDISO task

2026-09-16. User assigned the confirmed PARDISO native-memory leak.

Status: implemented, independently reviewed, verified in an isolated copy,
and integrated into this checkout. Not committed or deployed.

Owned paths changed in this task:
- `src/motor_ai_sim/simulation/p2_nonlinear.py`
- `src/motor_ai_sim/simulation/fem_solver_2d.py` (import, decorator, allocation only)
- `src/motor_ai_sim/simulation/pardiso_lifetime.py` (new)
- `tests/test_pardiso_lifetime.py` (new, 20 cases)

Simulation_new: please note this task and flag any overlap. Direct Claude
SendMessage is unavailable to this Codex session; this file does not claim
that a message has been delivered or read.

Scope: release native PARDISO memory on normal completion, exceptions and
SuperLU fallback; preserve symbolic reuse and all numerical expressions.
No live API calls, restarts, state writes, branch changes or deployment.

Implementation: a ContextVar/ExitStack per transient call releases native
PARDISO memory with free_memory(everything=True) on return or exception,
including exceptions before P2 construction. Failed PARDISO is released
immediately before SuperLU fallback and not released twice at scope exit.
Nested calls and worker threads have independent ownership. Cleanup errors
are logged without replacing the original solve error. Numerical expressions,
the public signature and symbolic factorization reuse are unchanged.

Verification (Python 3.11.9, one MKL/OMP/OpenBLAS thread, BELOW_NORMAL priority):
- `verify.py`: 36 passed in 6.86 s (16 existing P2 tests + 20 lifecycle cases).
- `verify.py tests/test_physics_regression.py -q -k test_case_matches_baseline`:
  8 passed, 6 deselected in 518.94 s. All eight stored physical baselines pass;
  baselines were not changed. 492 skfem DiscreteField deprecation warnings.
- `verify_memory.py`: original vs patched actual P2/PARDISO, 12 independent
  lifetimes, two solves per lifetime, 6400 unknowns / 31680 nonzeros.
  Original private memory 54.64 -> 128.23 MiB (+73.59); fixed 55.56 -> 54.77
  MiB (-0.79, allocator noise; no accumulating trend). Residual max 1.99e-15,
  solution sums identical, one symbolic analysis and two solves each time.
  Raw data: `memory-ab.json` in the verification folder below.
- GPT-6 Astra implemented; GPT-5.6 Sol independently reviewed source/AST/tests:
  no blocking findings. Parent ran tests and native memory comparison.
- Immediately before integration, hashes of both original solver files and
  the existing P2 tests still matched baseline-hashes.json; new paths absent.
  Integrated four files match the tested copies after normalizing line endings;
  git diff --check passed. Other agents' files were not changed by this task.

The first test-launch attempt was blocked by our own write guard because pytest
opened Windows NUL for logging; redirecting pytest's log inside the sandbox
resolved it. No application failure. All solver tests used redirected state,
a write guard and blocked network access; no live API access or restart.
This change fixes accumulating memory; single-solve speedup was not measured.

Isolated implementation and verification folder:
`C:\Users\vadim\.codex\visualizations\2026\09\16\01a0aaf0-a84f-7d23-9c5b-02df5147e1d6\solver-pardiso-fix`

Commit: none (handoff to integration lead). Deployment: none. Restart: none.
