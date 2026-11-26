# Machine fingerprint: staged rollout of the canonical print (2026-11-26)

`src/motor_ai_sim/geo_fingerprint.py` knows two prints of one machine: `legacy49` (every geometry key hashed, what records
written since 2026-11-15 carry) and `canonical` (post-baseline keys at their default left out, equal to the pre-2026-11-15
print of a machine at defaults). Both come from ONE snapshot of the geometry (`routes.simulation._geometry_fingerprint_pair`)
and are recorded as equivalent in a process-local, symmetric book.

During a deploy the API and the worker can run different builds for a few minutes. A build without these readers compares
the legacy49 print literally. So the change goes in two deploys:

| Stage | Writers store | Readers accept |
|---|---|---|
| 1 (PR #891, `WRITE_CANONICAL = False`) | legacy49, bit-identical to the build before | legacy49 and canonical, either direction (`same`, `accepted`, the passport lookup `_end3d_pick`, the measured k_T reader `end3d_factors.measured_k_T`) |
| 2 (`WRITE_CANONICAL = True`) | canonical | unchanged |

Winner between equivalent records (stage 1): measured k_T - the record under the queried print, an equivalent one only when
no literal record exists; end-effect passport - the workspace layer over the shared one across the alias group, inside a
layer the literal print first. Nothing stored is rewritten.

## Conditions for the flip (independent review of 2026-11-26)

Stage 2 is NOT approved. It needs its own pull request and its own independent review, and is proposed only when all of
these hold:

- every consumer and producer is inventoried and upgraded: both regions, API processes, compute workers, the us1 job
  images and queued or restarted jobs, offline tools, export / import consumers and cached clients. An old reader cannot
  infer canonical <-> legacy49; restarting the API alone is not enough;
- the equivalence is reproducible from durable record provenance (a verified stored pair, the original hash inputs or
  compatible dual keys), not from the process-local book and the configuration loaded now; checked both ways in separate
  cold processes with different loaded machines, after eviction / restart, and on exported files;
- the remaining literal consumers and the separate hash dialects are audited (for example
  `scripts/promote_admin_catalog.py`; the static3d material / cache hashes are a different dialect, not aliases);
- the winner rule for duplicate aliases is defined for every store (including duplicates inside one layer) and a return to
  stage 1 is proven with real saved and exported records: a canonical-only record and an old reader stay incompatible;
- the real stored fingerprints are inventoried with their original contexts, with a reversible migration / rollback plan.
