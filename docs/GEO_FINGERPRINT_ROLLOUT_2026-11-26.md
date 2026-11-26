# Machine fingerprint: staged rollout of the canonical print (2026-11-26)

`src/motor_ai_sim/geo_fingerprint.py` knows two prints of one machine: `legacy49` (every geometry key hashed, what records
written since 2026-11-15 carry) and `canonical` (post-baseline keys at their default left out, equal to the pre-2026-11-15
print of a machine at defaults). Both come from ONE snapshot of the geometry (`routes.simulation._geometry_fingerprint_pair`)
and are recorded as equivalent in a process-local, symmetric book.

During a deploy the API and the worker can run different builds for a few minutes. A build without these readers compares
the legacy49 print literally. So the change goes in two deploys:

| Stage | Writers store | Readers accept | Who may run beside it |
|---|---|---|---|
| 1 (PR #891, `WRITE_CANONICAL = False`) | legacy49, bit-identical to the build before | legacy49 and canonical, either direction (`same`, `accepted`; also the measured k_T reader `end3d_factors.measured_k_T`) | the build before (it reads every record stage 1 writes) |
| 2 (a later deploy: `WRITE_CANONICAL = True`) | canonical | unchanged from stage 1 | stage 1 only, never a build before stage 1 |

Stage 2 is deployed only after both servers have run stage 1 for every process (API and worker restarted on it). Nothing
stored is rewritten in either stage. Rolling back stage 2 to stage 1 is safe: stage 1 readers accept canonical records.
