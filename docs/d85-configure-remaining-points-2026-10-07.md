# D85 Configure reference: recovered completed points and remaining work

**Audit type:** local artifact/source validation only; no FEM or server calls in this task. Parent retrieved the missing compressed responses from the already preserved isolated attempt. This audit verifies local copies and describes opt-in exact replay; no generator was run.

**Model:** GPT-6; no escalation. **Attempt:** `eddy_true_03`, CIANO28 85 20SW1200 / L13, frozen solver source SHA256 `cca2567aee37e2face2bfde85ca9ee2007427275ad7702ffdcb67b6b3ccef86d`.

## Current disposition

The isolated harness now has an explicit `--replay-completed-responses` mode for the four previously completed ordinary calls (indices 1, 2, 4, 5). It accepts only fixed, independently pinned response files whose full request digest, source/snapshot/geometry/material identity, started/completed checkpoint provenance, original status gates, raw gzip hash, and decompressed JSON hash all match. The checkpoint and compiled replay manifest also have canonical-content pins. A mismatch refuses replay; a different request remains on the normal ledger/solver path. This feature has only been validated with offline unit fixtures; it has not run the passport generator.

Solve003 remains a separate exact owner-authorized preparation exception. Its specific mean-torque residual was accepted for reference preparation only. Its archived `steady_state=false` and `demag_settled=false` remain unchanged; neither solver qualification nor publication is made true by that disposition. Output exposes this as `reference_preparation_disposition`, separate from solver status and aggregate qualification.

## Archived evidence and exact replay coverage

All five completed response archives now exist locally and match their checkpoint and fixed manifest hashes. The table gives request / gzip / decompressed-JSON SHA256 prefixes; the harness stores and checks the full digests.

| Index | Point and recorded outcome | Exact artifact hashes (request / gzip / JSON) | Treatment |
|---|---|---|---|
| 1 | 25.88 A, 1000 rpm, 120 steps, demag/eddy/rotor eddy on. All recorded status gates true. | `55a6c8db…21d0621` / `b1b19c11…58bee76f` / `369b590f…4d6bad64` | Exact replay allowed |
| 2 | 0 A, 1000 rpm, 120 steps, demag off, coupled eddy on, rotor eddy off. Required status gates true; demag not requested. | `e6faa5ec…e269f51` / `af9f9e0e…382116f1` / `17cc6959…30fbce6` | Exact replay allowed |
| 3 | 6.47 A, 1000 rpm, 60 steps, demag/eddy on, rotor eddy off. Torque drift 0.00212845 vs original 0.001; owner allowed only this residual for this exact request. | `8a2ad9ca…f068f94` / `81713535…21168742` / `ea219a2a…fe3a2af` | Separate owner replay; original false flags retained |
| 4 | 12.94 A, 250 rpm, 120 steps, eddy/rotor eddy on, no demag request. Required status gates true. | `e7dc33d5…22838667` / `e9a561b5…0857b7d` / `8c2b5a8a…d33640a8` | Exact replay allowed |
| 5 | 12.94 A, 500 rpm, 120 steps, eddy/rotor eddy on, no demag request. Required status gates true. | `98d30266…11568b4a` / `de9c1fb5…8f441e77` / `80d209ab…d2b0eed` | Exact replay allowed |
| 6 | 12.94 A, 1000 rpm, 120 steps, eddy/rotor eddy on, no demag request. Only `solve_started`; no response/completion exists. | — | Unfinished; must be solved or match an exact ledger entry |

Validation binds indices 1, 2, 4, and 5 to `configure_eddy03_solves_after_rejection.jsonl`, including both checkpoint events and complete request provenance: frozen machine identity, snapshot, geometry/material signatures and assignment, effective shaft `included` state, solver source, coupled-eddy setting, and raw response digests. The fixed manifest additionally pins the exact request and response hashes. The checkpoint file is pinned by canonical parsed content (`ff5a0f94…a30d667b`) so platform newline conversion is immaterial; replay output also records the checkpoint’s actual byte hash. The compiled replay manifest has its own canonical pin (`21753ad8…b8e39951`), while actual response archive hashes remain byte-exact. Mutating the checkpoint, manifest, any raw response, source identity, or status gates rejects replay.

The owner record for point 3 remains byte/canonical pinned and binds its own exact request and raw response. It does not alter the ordinary replay manifest or general acceptance tolerances.

## Remaining calculations

The generator defines the loaded base at 25.88 A / 1000 rpm and the 0 A no-load anchor; three initial demagnetisation samples at 6.47, 12.94, and 38.82 A / 1000 rpm (with the 25.88 A base serving the middle current point); and a 3×5 loss surface at 12.94, 25.88, and 38.82 A across 250, 500, 1000, 1500, and 2000 rpm. The base point is not interchangeable with a loss-grid request when request settings differ.

Given the exact archived calls and owner-authorized point 3, prior work is reusable for the base point, no-load anchor, 6.47 A current-sweep point, and two loss-grid cells (12.94 A at 250 and 500 rpm). The current-sweep points at 12.94 and 38.82 A / 1000 rpm were not completed in the interrupted attempt; any data-dependent extension/refinement remains to be determined from the sweep. For the loss grid, 12.94 A / 1000 rpm (index 6) and 1500/2000 rpm remain; all five 25.88 A cells and all five 38.82 A cells also remain: 13 grid requests in total, including index 6. Any exact live ledger hit must still be established by the unchanged canonical key lookup at run time; this audit does not infer one from similar historical entries.

The replay switch must be used only with a fresh attempt output directory: old checkpoints and outputs are immutable. On an eventual full run, each of the four ordinary archive entries must be consumed exactly once; point 3's owner exception must likewise match exactly once if enabled. No outcome from offline replay unit tests establishes a completed Configure passport.

## Qualification boundary and checks

The current candidate remains incomplete and unpublishable: point 6 has no response and the remaining sweep/grid points have not been calculated in this task. Even after exact archive reuse, point 3’s original strict solver flags remain false, so aggregate all-steady/all-demag qualification is not satisfied. The owner disposition only says the one point is accepted for reference preparation; it is not a global solver tolerance change, a global qualification pass, or approval to publish.

Focused offline verification for the harness change: `python -m unittest discover -s scratch_s1_20261006/d85_finish_20261007 -p test_configure_reference_status.py -v` (28 tests passed), followed by `python -m py_compile` on the runner and test module. These are replay/helper tests, not an execution of the full generator. No solver, API, SSH, server, or deployment operation was performed by this audit.

## Sources

- `scratch_s1_20261006/d85_finish_20261007/run_configure_reference.py` — fixed-hash archive manifest, exact callback matching, owner-only disposition, output qualification gates.
- `scratch_s1_20261006/d85_finish_20261007/configure_eddy03_solves_after_rejection.jsonl` — original attempt checkpoint and request provenance.
- `scratch_s1_20261006/d85_finish_20261007/configure_eddy03_solve00N.response.json.gz` (N = 1–5) — preserved raw solver replies.
- `scratch_s1_20261006/d85_finish_20261007/configure_eddy03_solve003.owner_acceptance.json` — exact point-specific owner disposition.
- `scratch_s1_20261006/d85_finish_20261007/passport.py` — base, current sweep, and loss-grid request definitions.
