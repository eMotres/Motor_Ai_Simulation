# Exact template and numerical preview — 20:23 UTC continuation

GPT-6 workers completed both private components successfully without new model escalation. Backend `d9cb43d74e4c8cfeb75c47f132ad585f4dbd050b` adds the exact-body context check; descendant `7e9cfdcea4cbeae37a959e52cbaee922726ebcb9` adds a pure numerical preview decoder. Customer Configure and production were not changed. Earlier binding/current-context checks are preserved in the two preceding review notes.

## Current options must match the admitted calculation

The disabled, unregistered private router now accepts `POST /api/coupled/propeller_s1/{run_id}/context` with exactly `{body_template: ...}`. A finite JSON copy freezes caller options before a queue wait. Authority/path/credential fields and malformed objects refuse. Inside the normal queue slot, canonical snapshot JSON bytes and their SHA must match the admitted solver template before a new snapshot or bootstrap can run. Exact submitted options then flow into the current authenticated snapshot; existing owner, saved-selection, source/code, curve and runtime guards remain mandatory. GET retains its saved-build plus admitted-body scope.

No numeric coercion occurs. In particular, `true` cannot substitute for `n_parallel=1`; the hash catches Python's boolean/numeric equality. Different canonical JSON number representations, such as1000 versus1000.0, may conservatively refuse rather than reuse another template. This endpoint compares a submitted canonical calculation body; it does not independently reconstruct a user's unsent UI values or authorize scaling.

Parent queue/routes tests: **26 passed in2.61s**. Coverage includes exact match, drive/pack/cooling/winding/RPM/gamma changes, boolean substitution, malformed/extra/missing fields, nonfinite values, nested authority, disabled feature, invalid auth and foreign ownership. Changed templates refuse before snapshot/bootstrap. No search, result-store read, real process or FEM is started by these tests.

## Numerical preview is explicitly unqualified

`propellerS1Preview.ts` layers a strict numerical decoder on the accepted binding matcher. It requires an independently supplied curve ID/hash, measured RPM bounds and torque/power tolerances. These cannot be inferred from the result payload. The bound curve must match, the declared search domain must be inside those bounds, and the candidate bracket must meet the declared positive resolution. Candidate RPM must exactly match the evaluation speed and lie in both domains. All operating values must be finite actual numbers with the required sign, all seven solver/physical flags literal true, and torque/power residuals must meet the independent tolerances. Shaft power is checked against required propeller torque times2πRPM/60. The candidate must be mirrored exactly by one recorded `verified_feasible` evaluation row.

Unresolved, budget-limited, infeasible, domain-limited or malformed results refuse. Even success returns only `unqualified_operating_preview` with run ID, RPM, current, shaft power and motor/required torque, plus literal false qualification/publication/global-maximum claims. It does not set a continuous rating or prove a global maximum. There is not yet an authoritative policy/curve delivery seam or real Configure integration for this decoder.

Parent **9 Node tests passed without skips**; strict standalone TypeScript5.9.3 compilation passed with `allowImportingTsExtensions` and no emit. One initial test expectation was corrected: a positive out-of-range RPM properly reaches the domain refusal, not the malformed-number refusal. Source logic was unchanged for that correction. These checks are not a full production build or frontend render.

## Actual serialized composition, synthetic physical evidence

The parent harness runs the existing bounded Python search, real private result store and temporary HTTP routes, then feeds the exact current-template context and result JSON into the new TypeScript decoder. Its **synthetic callback** uses torque0.5×(RPM/1000)², current3+RPM/1000, a thermal gate at1450RPM, and emulated convergence/measured-coefficient flags. These are test fixtures, not a motor simulation or measured propeller dataset. Independent policy comes from the original fixture request and declared fixture curve domain/source, not the result.

The search produced a synthetic preview at1425RPM,4.425A,151.5106688271496W and1.0153125Nm. These numbers verify serialization and arithmetic only. One preview passed;14 mutations (power/torque residual, RPM mismatch, false gates, NaN, unresolved/domain-limited status, missing row, range/bracket, binding/publication/source mismatch) refused. Four changed templates (parallel grouping, boolean substitution, delta and speed) refused before rebuilding. Context POST succeeded with `PrivateAdmissionIndex.read_result` deliberately disabled. No canonical callback ran.

Harness/proof: `scratch_s1_20261006/d85_finish_20261007/verify_s1_preview_http_to_ts.py`, accompanying `.mjs`, and `s1-preview-parent-proof-2023.json`. Proof includes source hashes and explicit false real-auth/bootstrap/solver, measured-data and physical-qualification fields.

## Remaining release boundary

No public API registration, customer Configure edits, browser use, server action, restart, deploy or FEM occurred. The separate Linux process-smoke action previously rejected by automatic approval review was not retried; destination/payload permission remains unanswered. Real same-point torque/load and measured-domain thermal/voltage/mechanical qualification, authoritative current policy delivery, full applicable motor gates and exact consumer/build checks remain mandatory. D85's completed22 records and narrow owner003 exception are unchanged, and the separate old flux/PWM/source failures remain unresolved. No ready-for-customer-run statement follows.

Worker entries: `prop_s1_context_post_20261007_d9cb43d7` and `motor_ai_sim_propeller_s1_preview_decoder_20261007_codex_d85_acceptance_audit`. The backend worker's first logger attempt missed requiredid/model and appended nothing; its corrected entry succeeded once. The parent review is separately logged.
