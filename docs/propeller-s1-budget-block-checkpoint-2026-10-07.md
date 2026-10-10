# Propeller S1 / D85: interrupted verification checkpoint

Resume note: owner confirmed spend limits renewed. The two previously refused read checks now executed successfully: point-3 gzip and decompressed JSON hashes match the recorded values; own container remains stopped, no own supervisor/docker wait process found, and production commit/API start remain unchanged. Historical cap failure below is retained as provenance. Implementation drafts remain unverified pending the resumed workers' focused checks; no FEM relaunched or live changes performed. Official dataset ingestion is being retried after correcting a missing top-level model field.

The official logger subsequently ingested `propeller-s1-budget-block-20261007` successfully. Parent verified the completed fatal-abort/raw-preservation regressions: 8 unittest checks in 0.028 s and 48 point/search pytest checks in 0.23 s passed, with injected responses only. Subsequent source review found actual delivered power still needed to be propagated into Evaluation; correction/regression was assigned before accepting a source commit. Sonnet 5.5's new job/store draft failed the first parent run (2 pass, 1 fail), with additional cancellation, composition, input-boundary and immutable-storage gaps. It was transferred to GPT-6 for correction. These findings affect only offline preparation; no physical solve, customer change or readiness declaration follows.

Date: 2026-10-07. Project: motor_ai_sim. Validation: partial software preparation; D85 physical result rejected. Models: GPT-6 orchestrator and reused workers; no escalation.

The owner requested continuation and notification when the propeller coupling can be tested. Customer Configure and the owner's API on 8001 remain untouched. The ordinary propeller-cooling candidate is offline at 2b16717b; the pure RPM search foundation is offline at 4f40b76c550d806a905af5bf910b002e840c1149. Neither establishes installed maximum-propeller S1 behavior.

## Physical rejection and preserved evidence

Own D85 attempt `eddy_true_03`, container `codex-d85-configure-eddy03`, reached a mandatory refusal at point 3: 6.47 A RMS, 1000 RPM, 60 steps, coil 180 C, magnet 150 C, eddy requested, demagnetisation requested. Nonlinear and eddy settlement passed; `steady_state=false` and `demag_settled=false`. After 32 prepass periods, torque drift was 0.00213 against 0.001 tolerance; ripple drift was 0.027 percentage points against 0.127. This is a convergence acceptance failure, not proof of general physical instability.

The original generator caught the owned harness's Exception and continued points 4/5, then began point 6. The orchestrator stopped only this owned container with a 20-second timeout. The recorded stop was exit 137, FinishedAt 2026-10-07T08:58:04.212241842Z. Do not classify the stop as OOM or as another physical solve failure. Do not relaunch a large FEM run from this checkpoint.

Local evidence in `scratch_s1_20261006/d85_finish_20261007/`:

- `configure_eddy03_rejected_candidate.json`: acceptance false, unpublished, blocked_by_solver_rejection.
- `configure_eddy03_solve003.response.json.gz`: complete refused response.
- `configure_eddy03_solves_after_rejection.jsonl`: subsequent completed point ledger and interrupted point 6 start.

Recorded source/request provenance: solver SHA256 cca2567aee37e2face2bfde85ca9ee2007427275ad7702ffdcb67b6b3ccef86d; snapshot ce4d6412caa901d18e0ee2615073b523883e172b023d06f56776a2baac2c5f77; geometry 744e408997d27c8878362229e864ee6e440cccb818dbe5485202d053e37bf021; materials 7cae09c102b712be855a6fe991b4b5138087929a81e60583ea23660a336ad877; failed request 8a2ad9ca2015ceb2176cf687e7cd453741e07b086fd4edb25225ea8c7f068f94. Recorded checkpoint gzip SHA256 8171353593740c74f9a6e87d4b8ec5a0fb2a20d307bd055b502edd3821168742 and JSON SHA256 ea219a2a088ec682ef39769493e62597ba55d44d49d75764c1cc2adf2fe3a2af. Independent rehash was requested but not executed because approval review failed; do not claim it passed.

## Unfinished work and exact verification boundary

The uncommitted fixed-RPM torque/load adapter and pure search had 47 focused tests passing in 0.26 s before this interruption. Review then found some early refusals retaining only a raw-response hash instead of the parsed response. The worker was assigned to fix preservation and the owned generator's swallowed fatal refusal, with a fake generator regression test. That follow-up ended with a spend-cap error: inspect current edits before assuming completion or rerunning its focused checks.

A second worker created an uncommitted private bridge draft. It must invoke the unchanged canonical coupled runner in a fresh private process, with real isolated authorization/context and exact build/source checks. No authorized private auth-context bundle was prepared. The orchestrator required fail-closed behavior and fixture-only tests, prohibited copying live auth or fabricating authorization, and received a spend-cap error before completion. Its draft is not reviewed, tested, committed, installed or ready for real FEM.

Automatic approval review refused both the local Python hash check and the SSH read-only status check: workspace owner spend cap reached; review could not be completed. Neither action executed. This was not a safety determination. Do not bypass approval through another tool or shell. Both reused workers also ended on this cap. User was asked to increase the workspace limit.

Production last observed commit was 464bbecff77fa03f98e3edb03995f1dc8342b95b, API StartedAt 2026-10-06T18:32:33.74619841Z. A fresh read was blocked, so these are last observations, not a renewed check. No deployment or restart was performed by this task.

Once the cap is resolved: inspect drafts, complete owned fatal-refusal/raw-preservation tests, independently verify the private bridge boundary, rehash refused raw evidence, confirm own supervisor exited without relaunch, and log this failed/partial task through the official dataset logger. Pending candidate is in `coordination/dataset-pending-propeller-s1-budget-block-2026-10-07.json`; it has not been ingested. Route/job integration, exact-build persistence, final qualification and installation remain unfinished. Automation-2 remains active because readiness has not been achieved; repeated unchanged state should stay quiet.

## Read-only draft review, heartbeat 09:24 UTC

Permitted local source reads continued without rerunning either refused command, launching agents, touching live services, or claiming a new test pass. The review found partial edits from before the workers' spend-cap failure:

- The owned D85 harness now declares `_SolverAcceptanceAbort(BaseException)`, persists refusal before raising, and handles that signal at its outer boundary. A new fake generator test catches ordinary Exception internally and asserts only the first callback ran and its rejected raw checkpoint survived. This is source evidence only: the follow-up regression has not been executed or accepted. The fixed-RPM point module now passes parsed raw responses through the previously identified missing-coupling/converged-false refusal branches; new behavior still needs its focused tests.
- The private bridge draft exists, but `tests/test_propeller_s1_private_bridge.py` is absent. The worker did not finish the requested test suite. The draft must not be committed as verified or used to call a solver.
- The bridge stamps the caller's frozen identity after checking file hashes and family selection, without reading back and comparing the actual resolved geometry, winding, material cards, mesh, drive, and pack used by the canonical runner. A hashed copied config alone does not bind its contents to that supplied identity. This violates the assigned exact-build contract and is a release blocker, even with valid auth.
- `_dispatch_and_envelope` creates the same `attempt_root` with `exist_ok=False` on every sample, instead of an attempt-specific subdirectory. A second current sample would refuse before dispatch. Fix per-point output layout and test two calls without permitting overwrite.
- `_check_request` treats thermal settings as wholly frozen, while the point adapter updates `airSpeed`, propeller source fields and position/context for each requested RPM. Unless the template already equals each derived value, the actual point/bridge combination refuses. Permit only independently verified per-point propeller-derived changes; never allow arbitrary cooling or build changes.
- The draft's default-context check only checks an explicit caller environment path. It does not yet prove that an absent environment cannot admit the default application config. The job root itself is resolved before its original components are checked, and the declared private release/workspace boundary is not sufficient proof that the job root is a genuinely isolated owned job. Add explicit live/default-root refusal and path fixtures before any child import.
- The child rechecks selection and propeller guard but does not repeat all manifest/body/identity admission checks. Failed worker exits discard their diagnostic JSON instead of retaining a sanitized refusal record. The envelope also intentionally returns no physical limits (`gates={}`), so a successful worker call would still remain unknown for qualified S1.

No new physical conclusion follows from this draft review. The missing safe auth-context bundle, actual limit evidence, job/persistence integration and deployment remain separate blockers. Do not attempt a real FEM run to discover these software contract failures. GPT-6 orchestrator performed the static review; no model escalation. Extend the existing pending dataset record with this continuation after the official logger is available.
