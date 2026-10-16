# Exact Linux lifecycle-smoke package for owner approval — 2026-10-07

Validation: static source review and syntax/hash verification only. Reused GPT-6 implementer and GPT-6 independent reviewer succeeded without new model escalation. No transfer, child process, application bootstrap, FEM, live API access, restart or deployment occurred. This package does not qualify a motor or a maximum propeller S1 point.

## Concrete proposal

Four files are prepared under `scratch_s1_20261006/d85_finish_20261007/linux-process-smoke-approval-2026-10-07-2120/`: the exact accepted process helper, standalone stdlib harness, README and manifest. Proposed host: `176.9.84.229` (aerostator.com server). Proposed staging location: a NEW owner-owned mode-700 directory `/tmp/codex-propeller-process-smoke-20261007-2120-<16 lowercase hex>`. The harness creates a different, initially absent runtime directory with the same approved prefix and a separate nonce. No production, shared catalog, ERP or API directories are written.

The manifest contains the reviewable command. It verifies the staged directory's name, parent, ownership, mode and absence of links/old logs; checks exact source hashes; enables noclobber; and invokes isolated `/usr/bin/python3 -I -S -B` with an empty parent environment. No Python installation is proposed. Four tiny Python process cases check success, forced timeout, forced cancellation, and cleanup when a leader leaves a descendant. A 30-second watchdog is followed by bounded cleanup of the remaining owned groups; this is not a guaranteed total-duration measurement. Checkpoints, bounded child streams and summary are retained only in those new directories.

| File | SHA-256 |
|---|---|
| Process helper | `56b918e16ffe252fb090dc5d8cc1789edc26a8af2aac9d7761576edeb7cb9d5a` |
| Harness | `5798659948932b88bfd767588ce59c520b66b6455a98db69f358d1b0cfa27e96` |
| README | `e488956a6b6bc99c6c51c011bd150bbf9c0f60154ee970d4c96b153b70fec7cd` |
| Manifest | `c2727e5da891fcbc93c7441d6b4404efdeead108e9189877937c5c3df0450afa` |

## Review and rejected drafts

The legacy proposed harness used a stale v1 marker and hidden SOURCE_B64 input. Its copied source remains outside the transferable bundle. The first new draft was rejected before execution for missing module registration, daemon-thread cleanup races, a non-global deadline and cancellation before the child installed its signal handler. Further review corrected exit status after summary-write failure, checkpoint return-code consistency, isolated startup, historical PID/PGID retirement, unsafe staging/log overwrite preconditions and README hash drift. Negative findings and the initial static proof are retained separately.

The final harness uses the v2 marker and executes the exact bytes it hashes. Child ready markers and actual return code -9 are required for the forced timeout/cancel cases. Main-thread alarm and unconditional final cleanup replace daemon threads. Reaped, observed-gone groups are retired, so final cleanup does not revisit historical identifiers. Actual cleanup failures, including zombie/liveness failures, remain failures. The README explicitly limits cleanup authority to acquired Popen handles: if process start fails before a handle is returned, standard-library Popen owns start-failure cleanup.

Parent verification compiled two Python sources without importing/executing them, parsed the manifest and independently matched all three payload pins. The helper is byte-identical to the unchanged private source in `92ed69b91b1341a10e27c18ff5844e1551929151`. Proof: `linux-smoke-approval-parent-static-proof-2120.json`; source-only review: `linux-process-smoke-approval-2120-review.md`. No previously accepted backend/TS suite was rerun.

## Approval boundary and remaining work

The earlier separate remote transfer/execution was rejected by automatic approval review because explicit destination/payload consent was missing. This preparation does not retry or bypass that action. The owner is asked only to authorize the exact four-file package at the host and new temporary-directory pattern above, then its four lifecycle cases. Authorization does not cover application/auth bootstrap, a motor solve, a public API registration, deployment or a client Configure change. Until a human answer arrives, no dependent transfer or execution is allowed; repeated unchanged heartbeat notifications should stay quiet.

Even a successful future smoke establishes process lifecycle only. Actual canonical authenticated bootstrap, same-point motor/propeller torque balance, measured-domain thermal/electrical/mechanical evidence, exact Configure materialization/consumer and full applicable D85 flux/PWM/source gates remain. D85's 22 completed records and narrow owner003 mean-torque exception are unchanged; maximum S1 is uninstalled and unqualified.

Official records: worker `motor_ai_sim_linux_smoke_approval_package_20261007_gpt6_01`, independent review `motor_ai_sim_linux_smoke_approval_audit_20261007_codex_d85_acceptance_audit`, parent `motor_ai_sim_heartbeat_2120_linux_smoke_package_review_20261007_01`. These record static preparation, not a performed Linux test.
