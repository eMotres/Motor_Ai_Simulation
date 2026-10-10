# D85/L13 Configure replay04 audit — 2026-10-07

## Outcome

The completed `owner_replay_04` run passes the strict offline candidate audit for preparation of a Configure reference entry. The audit checked 22 solve records: 21 ordinary responses passed the gates requested for their points; one response is the narrowly accepted owner disposition for the exact archived solve003 point. Four ordinary archived responses were replayed once each from the previously pinned replay manifest. The staged legacy wrapper remains reference-only.

This is not an S1/continuous-rating result and is not publishable. The candidate's own qualification claim is false. The independent audit wrapper also has `qualification_claim=false`, `s1_claim=false`, `publication_allowed=false`, and `published=false`.

## Owner-disposition boundary

The owner exception applies only to request SHA `8a2ad9ca2015ceb2176cf687e7cd453741e07b086fd4edb25225ea8c7f068f94`, solve003, at 6.47 A and 1000 rpm. Its original response remains unchanged: `steady_state=false` and `demag_settled=false`; nonlinear convergence and eddy settling remain true. The recorded mean-torque residual was 0.00212845 against the run's 0.001 criterion. This owner disposition accepts that one point for Configure reference preparation only. It changes no solver tolerance or verdict and does not extend to another request or point.

## Provenance and verification

The expected input manifest was independently rebuilt from the pre-run `replay04-preflight-before-run.json` pin, frozen `snapshot_L13.json` and `state_L13.json`, pinned runner/source hashes, the original checkpoint, four original archived replies, and the separate owner record/raw response. The final candidate was not used to derive expected identity or replay inputs. The raw owner request stores geometry as serialized JSON while the owner record stores it as an object; they were compared using the runner's existing geometry normalization, with both original request hashes retained.

The terminal supervisor record reports exited, exit code 0, `OOMKilled=false`, and not running or paused. Its unhealthy container health status is separate from the completed solver run. The exact terminal state was cross-checked against the persisted `container-terminal-state.json`.

| Artifact | SHA-256 |
|---|---|
| Final candidate | `7d333cee34b8783a4b581d19f1ca7733659e0fb79e758ca0f2d53be10cf056df` |
| Final checkpoint | `e31e5852cb2e52a2e375da8e1db34512891d01599ae342655c59560c1a82cf9d` |
| Staged offline audit receipt | `8c59499ca94782f6139ee528b38241efb9956160681114fa5b59637a3e0f4bc6` |
| Terminal-state file | `dcaa14671f4e16eb114ce060c2e15445d4210a6fc0481579ef0e27a1d4da963a` |

The receipt is `scratch_s1_20261006/d85_finish_20261007/replay04-final-evidence/d85-configure-audit-staged.json`. Its successful recomputation matched the saved receipt exactly.

The local copy of the preflight's workspace `motor_config.yaml` bytes was unavailable. Its recorded preflight hash was retained and checked against the run's provenance, but it could not be rehashed from a local workspace copy. No FEM, API call, catalog write, or publication was performed during this audit.

## Checks

The focused auditor and manifest-preparation tests passed: 26 passed, 2 skipped because this Windows environment did not permit creation of symlink fixtures. After the real archived-owner test was separated from the synthetic-fixture class, the focused auditor file passed independently: 17 passed, 2 skipped. The full audit was also recomputed read-only from the pinned inputs and terminal files and matched the saved receipt.
