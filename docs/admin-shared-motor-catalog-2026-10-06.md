# Admin motor catalog saves are shared — 2026-10-06

## Owner requirement

All motor copies and catalog saves made by an administrator must be common to users. Ordinary users retain isolated workspaces and private copies. Common catalog changes must become visible without requiring a browser reload. Duty tables in the same die must align across configurations.

## Observed failure

Production 1162f16: the administrator's workspace L20 contains rated13000rpm52.55A with the revised build. Shared L20 still contains peak25000rpm80.61A with the older build, matching the ordinary-user screenshot. Existing YAML saves deliberately copy shared documents into the caller's workspace; admin controls did not override that default. Production API also mounted shared storage read-only. The existing publish endpoint writes community author namespaces, not the curated shared catalog, and its best-effort sidecar helpers cannot safely implement curated migration.

## Implementation decision

Change the catalog storage contract for verified signed-in administrators only: motor definitions, saved duties, recorded results and fields go to shared catalog storage. Runtime geometry, solver caches, personal preferences and record:false calculations remain in the normal workspace. Admin catalog reads prefer shared; ordinary-user workspace-first rules remain. Anonymous/no-identity calls cannot gain shared write authority. API shared mount becomes writable intentionally for this requirement; existing authorization at write boundaries remains mandatory.

Migrate existing administrative motor saves through a bounded dry-run-first maintenance tool with verified administrative source identity, preflight, provenance and backups. Do not blindly overwrite a newer shared configuration with a stale unedited copy-on-write document. Preserve workspace copies and unrelated user data. The known administrative source candidate contains six dies and fifteen YAML documents; trusted ownership was verified against production ADMIN_EMAILS by hashing the configured administrative identity to workspace c309c100cd421858; no addresses or credentials were printed. Apply still requires migration preflight and backups.

Use one fixed twelve-column layout for every configuration's duty table. Refresh visible shared catalog data within five seconds; refreshing catalog metadata must not overwrite an ordinary user's current geometry edits.

Models: GPT-6 Luna implementation, GPT-6 Luna independent backend review. No escalation. No production data migration executed at design stage.


Verification checkpoint: first bounded backend run: 70 passed / 3 failed. A per-kind merge violated existing per-duty precedence and must be corrected; do not mix an old thermal result into a re-solved duty from another build. Both sweep geometry failures reproduced on untouched production 1162f16 under the identical sandbox config (2 failures, 3.16 s); they are baseline limitations. A separate old test_duty_fields fixture unexpectedly launched FEM and was stopped in the agent-owned disposable container; new focused field save/load coverage is used instead.

Core checks: 73 backend cases passed (two known sweep geometry baseline failures deselected after reproduction on untouched HEAD); 13 report progress frontend cases passed; Vite build passed in14.08s; zero TypeScript diagnostics in the two touched components (138 baseline lines overall); compose config valid. Migration tests initially9passed then scopedauthoritative10passed. Owner explicitly selected stator_fillet_r1=0.15mm as authoritative for the whole CIANO14 40 new family. L12 source and shared YAML are byte-identical. L20 source current-run build c0eedf92ff29 matches the authoritative build and payload exists; four source field maps carry geometry fingerprint037040daebda801f. Legacy scalar records use geometry_fingerprint rather than build_sig; migration must preserve and validate this existing schema rather than drop thermal/coupled/mechanical records or fabricate new stamps. Promotion/deployment pending.
