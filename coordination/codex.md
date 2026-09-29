# Codex — solver audit fixes

2026-09-17 checkpoint: user explicitly requested committing the work. Preparing
a path-limited commit of the verified application batch and this journal.
Pending CAD/P2 source patch, three tests, hashes and numerical evidence are
preserved in docs/solver-audit-checkpoint-2026-09-17 without activation.
All 20 application code/test/baseline hashes still match the tested snapshots.
Foreign configs, logs, reports and scratch work are excluded. No push requested.

## Latest diagnostic: complete P2 radial-cut constraints (sandbox only)

The canonical CAD and P2 radial-cut candidates remain isolated pending the
combined gates. The actual half/quarter meshes contain 55 radial-cut facets,
but the old radius-adjacency enumeration constrained only 53 midpoints.
Coincident, distinct rotor/stator slip vertices interleave in the radius list.
Enumerating actual facets through the matched vertex map restores all 55;
a missing slave counterpart now raises explicitly. Existing slip-wrap logic
and the full-ring projection are unchanged.

Identical new tests on old source: 9 failed (7 synthetic + 2 actual meshes).
Patched projection suite: 25 passed, including existing analytic field checks.
Cold field capture: omitted midpoint jumps up to 2.7412e-4 T m become zero;
half/quarter moment difference falls from 1.90008e-4 to 6.27e-13 N m.
Completed no-load 72-point curves, against the full circle with common outer
air discretization: half max pointwise difference 8.2211e-13 N m; quarter
1.1598e-12 N m. All angles match exactly; raw range is 0.273733075787 N m.
This proves sector parity on this mesh, not physical mesh convergence.

Completed: half/quarter zero and loaded curves (4 passed, 795.10 s), common-
mesh full loaded reference (1 passed, 508.09 s). At 46 A the raw ranges agree:
full 0.2638129591315188, half 0.26381295913198954, quarter
0.2638129591317515 N m. All raw samples and all 37 spectral bins are saved in
solver-ripple-review/waveform-comparison. Full outer-air uniformization was a
diagnostic runtime override, not a production mesh change.

Combined CAD/mesh/P2 regression: 7 passed, 1 failed, 584.89 s. Sole failure
p2_noload reproduces the earlier CAD-only values exactly: mean and Maxwell
mean 3.921709386350385e-5 N m vs 4.1481998567548255e-5; ripple
154.01844415169822% vs 128.72201908278132%. No new regression pins promoted.
CAD and radial-cut P2 candidates remain UNINTEGRATED; reviewed five-file
bundle: solver-integration-cad-cut (manifest, before/after, review.patch).

User requires keeping 20% weekly usage in reserve; latest check has 25% left.
All started numerical runs have finished; agents are idle. Post-fix radial
convergence is prepared but NOT launched. Resume with the regression-pin
review/integration and physical convergence, not a fresh audit. General mean
torque/1 A selector remain open. The regression log also shows existing
losses.py leakage-ramp removal in iron-loss processing; the raw-torque fix
does NOT remove that separate processing. Audit it before claiming nothing
is discarded throughout the solver. No commit/push/API/restart/deployment.

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
`web/src/components/simulation/TransientCharts.tsx` raw-only cleanup is now
integrated after the HMR notice and baseline-hash verification. No browser action.
Report files are owned by another agent and will not be overwritten.
No commit/push/deploy/restart. Direct lead messaging still unavailable.

Lead integration received: previous 21-file batch committed as
`d2d3d4395cc6643752218037d2880b36e6dbcb05` and pushed by the lead.
Current raw-only and symmetry work is a NEW batch on top of that commit.
The new spectrum uses the entire retained waveform, including fractional
windows, with actual fractional electrical orders; no first-period truncation.
Raw-waveform/FFT tests: 54 passed; chart selection tests: 3 passed.
Isolated TypeScript comparison: 139 diagnostics before and after, no new
diagnostics; 9 pre-existing diagnostics remain in TransientCharts (not clean).
Native and symmetry runs are in progress; these results are not yet an
accuracy claim. Report-owner handoff: `report.py` currently formats the dominant
spectral order with `int(...)` / `%d`, truncating fractional orders. Preserve
numeric fractional orders in that label when integrating the new spectrum.

Raw-only + sector-guard batch is now INTEGRATED, UNCOMMITTED (16 code/test paths,
plus this journal and validation-plan documentation). All production inputs
matched the reviewed baseline hashes immediately before copying; all outputs
match `solver-raw-symmetry-integrated/integration-hashes.json` afterward.
Combined FEM SHA256: 9F14E064A04A7467BA6BFE9537D98880C63E34F132BF7035DB071AB0B8037105.
Sector guard adds `simulation/geometry_2d.py::validate_sector_symmetry` and
`tests/test_sector_symmetry_guard.py`; private probes choose the largest
certified sector or Full. Explicit invalid public sectors raise before solving.
Historical route-cache returns and mesh preview are outside this guard's scope
and remain follow-up items. The missing winding identity in calibration caches
has now been fixed by the separately reviewed follow-up below.

Final combined isolated gate: 124 passed, 1 unrelated slow calibration test
deselected, 50.81 s (includes two native FEM solves); 3 chart tests passed.
Earlier raw-only native + full physics-regression module: 16 passed, 650.03 s;
that process imported the first-period spectrum revision. After the user's
whole-window instruction, focused 54 tests and native 2 tests passed, followed
by the final combined gate above. Physics baselines and tolerances unchanged.
First combined attempt collected no tests because the copied calibration test
was absent; restored the unchanged test (hash equals live) and reran successfully.
Path-limited whitespace check passed. No commit/push/API/restart/deploy by Codex.

Winding-cache follow-up INTEGRATED, UNCOMMITTED: FEM cache keys now include the
resolved phase/sign basis (`w2-` hash; `psipm_v2_` namespace). Manual-angle
validation uses the same winding identity. Old unproven entries miss lazily;
no live cache is cleared or modified by integration. The next normal request
may calibrate once. Actual automatic and equivalent explicit layouts share
identity, while phase relabeling, sign inversion and orientation changes do not.
Verified 89 tests (1 slow deselected), plus 7 existing PM-linkage/scaling/stale
cache tests (25 unrelated deselected); no field solve required for these keys.
Final FEM hash after this follow-up:
491C41972632CACD48E1D9C5A1AB84492C590AD42260CCD162293CDD2CC2B576.
New own test: `tests/test_winding_calibration_cache.py`; total current batch
17 code/test paths plus two documentation paths. No baseline changes.

Gap-tie follow-up is now INTEGRATED, UNCOMMITTED; CAD remains SANDBOX-ONLY:
- `solver-gap-ties`: narrow `_weld_belt_into_half` roundoff-tie comparison in
  `simulation/mesher.py`. Exact real-mesh replay: 58 differing rotor triangles
  (29 diagonal flips) -> zero, with all coordinates unchanged. Corrected 58
  unit cases + replay pass; the identical original-source suite fails 30 cases.
  Initial 4 coordinate-only fixture failures were corrected using an explicit
  16*machine-epsilon*radius roundoff bound, not a changed topology tolerance.
  Eight unchanged physics pins finished: 7 passed, 1 failed, 840.83 s. The
  only mismatch is p2_noload T_avg_maxwell_Nm: stored rounded 0.0 versus
  full-precision 4.1482e-5, matching the existing T_avg_Nm pin. This is the
  intentional removal of output rounding, not evidence of a field change.
  Targeted retry plus import-provenance gate: 2 passed, 13 deselected, 21.11 s.
  Actual module origins and runtime/source equivalence verified in the sandbox
  before and after the solve (35 motor modules at finish). Old traceback paths
  can persist in copied bytecode metadata; the retry checks actual origins.
  Exactly one baseline value is now updated: p2_noload T_avg_maxwell_Nm from
  0.0 to its existing T_avg_Nm value 4.1481998567548255e-5. No tolerance changes.
  This documents removal of rounding, not a change in the calculated field.
  Actual corrected quarter/half no-load runs pass, but their peak-to-peak
  discrepancy remains: 0.323345 vs 0.295980 N m. This is NOT the complete
  cogging fix; the new motor campaign is not promoted to a physical baseline.
  Mesher and new tests/test_gap_zipper_ties.py matched reviewed hashes before
  and after integration; original live mesher and baseline hashes were checked.
  Current new batch: 20 code/test/baseline paths plus two documentation paths.
- `solver-cad-symmetry`: investigating `cadquery_geometry.py` slot-mouth circle
  polygons (only centres rotate; 27-gon orientations do not) and coordinate-
  dependent ring weld representatives. Production CAD and mesher paths were
  clean when these candidates started; no production edits to them yet.
  Saved stage proof finds 84 differing stator vertices. Four identical cells
  already classify differently before snapping because the exported CAD
  contour itself is not invariant under the intended 90-degree repetition.
  No changes to the owner's geometry/config, API or browser for these probes.

Mouth-only historical CAD gate: 88 passed, 1 skipped, 5 failed in 45.44 s.
Four failures are Windows asyncio socketpair creation blocked by the isolated
network guard before in-process API requests. One frozen-default rotor contour
has a 3.90% local chord ratio against the existing 4% gate. Identical failure
was reproduced with original source and the same frozen config (both sides:
1 failed, 1 passed, about 1.2 s, including the actual mouth-export A/B proof).
All named-preset shared-boundary quality cases passed.
No CAD candidate integration yet; no tolerance was relaxed.

Wire-split historical gate: 11 passed, 1 failed, 132.06 s. Native winding/series
checks passed; the thermal-map case is blocked by the same Windows socketpair
network guard before its in-process route request. This is not a clean full
API/thermal gate; no live API was used to work around the guard.

Process-only canonical primitive cleanup experiment: 1 passed in 1.13 s after
correcting a test-only package import (first attempt did not collect). For the
100mm fixture, post-placement close runs drop 48 -> 0, complete stator R90
symmetry is roundoff-exact (3.5e-14 mm Hausdorff), shared points match and repeat
cleanup is identical. This is a bounded geometry correction, NOT only roundoff:
final stator area changes by -0.416232 mm2 and boundary by 0.00920022 mm; the
existing weld tolerance is 0.025 mm. Cross-preset verification and guarded
implementation remain isolated, pending integration.

Final CAD candidate SHA17D39AF02B85E5CB228C4F82A9C2903A35D61FB58552FB6E31441FEC58239EBA
is independently reviewed. Its 25 new tests and historical contour tests give
53 passed, 1 unchanged default-rotor quality failure (17.48 s). Two initial
negative-radius test errors assumed Polygon rather than MultiPolygon; only
the topology assertion was corrected, not production behavior or tolerances.
Source-frame cleanup is accepted only within original weld displacement and
Hausdorff budgets with component/hole topology preserved; otherwise it keeps
the original primitive. Generic sanitizer remains unchanged.

Actual Full/Half/Quarter mesh gate: strict global equality failed in 23.68 s,
but localization proves EVERY mismatch is in full-ring outer air at radii
50-65 mm. Half/quarter match everywhere; full rotor and inner stator match
all quarter blocks, including exact triangle connectivity and material labels.
Available classifier PM polarity and winding phase/sign parity checks pass.
The full-only outer grading makes 75 outer chords versus a sector-equivalent
600, with different radial rows and discrete boundary area. This is a mesh
path difference, not yet an identified field-error magnitude.
Process-only uniform-outer experiment (no production edit) passed all 12
strict mesh/material comparisons in 11.93 s. Saved-only localization/boundary
diagnostics passed in 1.77/0.26 s. A common-outer full field comparison is being
prepared; native half/quarter comparison and 8 unchanged physics pins are
running in separate guarded copies. CAD remains unintegrated until these
effects are assessed; the new campaign does not change any baseline.

IMPORTANT: identical-mesh half/quarter field campaign finished (2 passed,
496.71 s), but physical parity FAILED: raw no-load ranges are 0.296219408240
and 0.323507065150 N m, means -0.008328877949/-0.007503380162 N m. Nonlinear
convergence and all CREATED projection constraints pass; those checks alone
do not certify completeness. At angle 5 mechanical degrees the torque differs
by 0.017468373918 N m; at zero by 0.000190008337 N m.

Independent saved-mesh torque-operator audit passed (2.31 s): quarter/half
select 1284/2568 matching gap elements; radial threshold margins exceed 82 um,
quadrature geometry/weights/rotation tensor differ only at roundoff. A separate
actual-cut coverage audit passed (1.34 s) and found a concrete second P2 tear:
there are 55 geometric radial-cut facets but only 53 midpoint constraints.
The missing stator and rotor belt edges both touch the duplicated slip radius.
Sorted neighboring cut vertices do NOT enumerate all actual mesh facets.
All 57 cut-vertex pairs are correct, no stator/rotor crossing, no origin vertex
(minimum radius 20.7 mm). New actual-facet enumeration and single-frame field
A/B are being prepared in separate copies; this P2 fix is NOT integrated yet.

CAD mouth-only candidate: 15 tests plus 2 diagnostic-census checks passed
in 1.06 s; independent source review approved. Local right cutter retains its
sampling, the complete geometry is mirrored and then rotated in both export
paths. Sanitizer is UNCHANGED. Its remaining coordinate-dependent weld still
breaks final stator symmetry: R90 symmetric-difference area 0.393012 mm2,
Hausdorff distance 0.00920022 mm on the 100mm fixture after the mouth fix.
All 48 observed short runs (24 stator + 24 matching out-band boundary copies)
remain unresolved by the conservative corner classifier; no averaging applied.

All baseline sector solves completed. At 46 A and the common 72-step grid:
Full/Half/Quarter raw peak-to-peak = 0.264085 / 0.287864 / 0.313877 N m;
reported means = 6.029726 / 6.032464 / 6.035131 N m. Quarter differs from Full
by about 18.9% in ripple range despite about 0.09% in mean. These unconverged
meshes are diagnostic evidence, not an accuracy certification or new baseline.

## Completed: integer P2 sector midpoint coupling (2026-09-16)

Integrated in lead commit `d2d3d43`. `simulation/p2_projection.py::SlipProjection.build`
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

## Low-current torque selector audit (2026-09-23)

GPT-6 Sol implemented the read-only review script, five analytic checks, and
the audit note. No escalation was needed. The parent independently reran the
five tests and the archived-data review.

On the fixed GEO30 12-slot/14-pole NS2 fixture, the selected mean torque drops
by 0.000081312055 Nm between 1.000 and 1.001 A peak per branch. Over the same
increment, raw Maxwell rises by 0.000005119930 Nm and terminal/space-vector
work rises by 0.000005140365 Nm. The existing hard 1 A method switch therefore
introduces a nonphysical discontinuity. The selector was intentionally left
unchanged pending the bounded 1 A frozen-current virtual-work discriminator.

Artifacts: `scripts/torque_low_current_review.py`,
`tests/test_torque_low_current_review.py`, and
`docs/torque-low-current-threshold-2026-09-23.md`.
Verification: 5 passed in 0.12 s; archived review reproduced all four saved
cases and hashes. No live API access, restart, deployment, configuration edit,
or push.

## Raw measured-surface core-loss selection (2026-09-23)

GPT-6 Luna implemented the bounded change; no model escalation was needed.
For steels with a measured P(B,f) surface, the selected iron-loss result now
uses the DFT of the captured raw field window. The former linear-ramp
detrending remains available only as an explicitly labelled legacy diagnostic.
The selected total, fundamental contribution, time series, loss balance and
efficiency therefore all use the same unfiltered candidate. Top-level raw and
legacy totals remain exposed for comparison.

Sol independently reviewed the routing and found one edge inconsistency: when
all surface harmonics were below the amplitude floor, the selected surface was
0 W but a tiny classical derivative series could remain. The series and its
eddy term are now zeroed with that selected 0 W total, covered by a dedicated
sub-floor test. Verification: the parent reran the three focused suites,
87 passed in 5.93 s.
No FEM run, live API access, restart, configuration edit, deployment, or push.
The core-loss commit was held until the other agent committed its overlapping
`fem_solver_2d.py` work as `1c23f12`; the remaining raw-loss hunks were then
committed separately as `c582449`.

## One-ampere frozen-current torque discriminator (2026-09-23)

Sol prepared and reviewed the guarded run and offline analyzer; Astra launched
the bounded solve in an isolated clean checkout. No model escalation was
needed. The first three attempts stopped before any field solve on provenance
guards (clone ownership, one missing pinned config, then a post-guard Git
call); the runner was hardened before the successful attempt.

The successful run saved 96 converged equilibria (48 center-current pairs at
one slip cell either side) for the GEO30 12-slot/14-pole NS2 case at 1 A peak
per branch, mesh 1.4/0.35 mm. All source hashes were unchanged during the run.
The 48 frozen-current coenergy secants have mean 0.005138892113 Nm. Independent
all-bin terminal work from the archived raw currents, linkages and signed
angles is 0.005140465326 Nm, 0.000001573213 Nm higher. Raw Maxwell mean is
0.005226917746 Nm, 0.000088025633 Nm higher than the secants.

Source/linkage mismatch was at most 1.63e-19 Wb; PM field/source pairing agreed
within 3.33e-16 J; the reconstructed H law agreed to 4.66e-10 A/m with no
permeability-floor activation. The integer-weld spatial error remains
unbounded, and run15's mixed-EOL field_ops bytes were not archived, so this is
strong diagnostic support for all-bin terminal work, not certification.

Verification: both focused review files passed 7 tests in 0.49 s; the analyzer
reproduced the saved result. No live API/config access, deployment or push.
