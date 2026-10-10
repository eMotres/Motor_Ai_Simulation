# Private propeller S1 result binding — parent review, 2026-10-07

GPT-6-family workers succeeded on the private software boundary, with no new model escalation. Backend source is `d049819bf638d27741f5ac45b472c482a77d1b5b`; the standalone TypeScript matcher is `5d99df638f47acad591d13cfc635c3cb48a3cd83`, its direct descendant. Both are in `scratch_s1_20261006/default40-release`, not production.

## Why this binding exists

A saved S1 result must not be reused for another reference, winding, material set, solver/mesh, drive, battery pack, cooling setup or measured propeller curve. The coordinator now emits a redacted receipt only after fresh owner/admission/source/runtime checks and verified immutable result-store lookup, while holding the normal queue slot. It binds exact selection (nullable ref_id is valid), propeller ID and curve hash, full identity hash, nine identity-block hashes, template hash, and the combined source/code manifest hash. The newly added helper is itself pinned in snapshot code manifests. Owner identifiers, authorization, paths and raw build fields are absent from this receipt.

The TypeScript helper strictly matches that receipt against independently supplied current context, loaded selection and assigned propeller. The outer HTTP result identity must also match the receipt. Missing context, stale build blocks, wrong reference/propeller, malformed hashes or any missing/true qualification claim refuse. Successful matching returns only an `unqualified_reference` run ID. It does not consume, validate or return candidate RPM/torque/power or a continuous rating. Its synthetic NaN candidate fixture tests this intentional boundary, not acceptance of numerical evidence.

## Independent verification

- Backend binding/queue/HTTP/snapshot/admission-store: **65 passed, 2 Windows symlink skips**, 12.13s. The first parent command referenced a nonexistent `test_propeller_s1_store.py`; pytest collected nothing. Correcting it to `test_propeller_s1_admission_store.py` produced the result above.
- Standalone Node matcher suite: **8 passed, zero skips**, Node v24.13.0. Parent review required an additional outer-result identity hash guard; the worker repaired it before acceptance.
- Parent actual HTTP-to-TypeScript composition: **1 positive and 14 refusal cases**. A mounted temporary FastAPI router used the real queue/result store and fake account/bootstrap/worker/bridge seams. Expected context was independently hashed from the admitted snapshot/runtime fixture, not decoded from the returned receipt. The actual HTTP JSON was then passed to the TypeScript matcher. All nine block changes, foreign config/propeller, altered outer hash, true S1 claim and missing context refused. No canonical process callback ran.
- Static TypeScript checking remains **unavailable**: no TypeScript compiler in either checkout, bundled runtime or checked local cache. The cache's deprecated npm `tsc` package2.0.4 is not the compiler and was not executed. Node execution does not establish static type correctness or a production build.

The backend worker's initial seven fixture failures (four empty cooling blocks; three newline-sensitive body hashes) were repaired and retained in its dataset entry. None was hidden or treated as physical evidence. The Python/Node composition harness and its source hashes are under `scratch_s1_20261006/d85_finish_20261007/verify_s1_binding_http_to_ts.*` and `s1-binding-parent-proof-1942.json`.

## Release boundary and remaining work

The public router remains unregistered and disabled by default. This helper has not been wired into customer Configure; there is no independent current-context endpoint or candidate/rating consumer yet. No API, web deployment, live catalog edit, browser session, real worker process or FEM run occurred in this continuation. Last observed production remains7adf720b, with ordinary Thermal propeller cooling and the Materials/Propellers catalog already installed.

Maximum S1 still requires reviewed public admission/result integration, independent current-context delivery, actual process validation, torque/load balance across the measured propeller domain, all same-point thermal/voltage/mechanical gates, and consumer/build verification. The previously rejected separate Linux process-smoke transfer/execution was not retried; its destination/payload approval remains unanswered. This does not change the completed and separately authorized D85 batch.

D85 replay04 remains22 completed records/44 checkpoint rows, exit0/OOMfalse. The exact owner003 reference-preparation exception remains limited to its pinned record. Old assembled passport off-grid psi_q failure (-0.514886934736% against0.5%), absent PWM anchors, mixed source trees and all false qualification/publication claims remain intact. No motor or S1 rating is published by this work.
