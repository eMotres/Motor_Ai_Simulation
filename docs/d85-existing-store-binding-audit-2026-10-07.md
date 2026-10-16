# D85 existing passport-store binding audit — 2026-10-07

This is a read-only inventory and source review, not a publication or physical qualification. GPT-6 performed it without model escalation. Production, customer Configure and shared data were not changed; no FEM was launched.

The authenticated production references response contains the older `cat_ciano28_85`, named `CIANO28 85`, with `card=null` and `has_machine=false`. Separately, the actual passport store contains `CIANO28 85 20SW1200/L13.json`. Thus the missing card association is not evidence that the server has no L13 store record. The older catalog name does not resolve the exact existing store identity.

The existing store record was copied byte-for-byte into the owned evidence directory as `scratch_s1_20261006/d85_finish_20261007/d85-live-passport-store-L13-1843.json`. Its 84,422 bytes hash to `3605ffd9a95aa37cfc3edc87b7f72385b48386f808d7030225afe40dc127fe53`; host, container and local hashes agree. Its schema is `passport-v1-full-1`, but that schema name does not establish physics qualification.

All three original variants (Si 48 kHz, GaN 48 kHz and GaN 100 kHz) bind to 13 mm, geometry `744e408997d27c8878362229e864ee6e440cccb818dbe5485202d053e37bf021` and snapshot `ce4d6412caa901d18e0ee2615073b523883e172b023d06f56776a2baac2c5f77`, matching the independently pinned replay04 build. Their original provenance explicitly reports no motor-side PWM FEM anchors and excludes extra motor PWM loss. The nominal 20-point maps must not be described as measured PWM anchors or propeller S1 results. The named original source `docs/data/passport_d85/passport_L13.json` is absent from `/opt/motres/app`; this absence is retained rather than silently substituting another file.

## Older assembled source boundary

The separately retained `assembled_passport_L13.json` hashes to `ad4a96dada8afea475849b859c31dcfefa6c9c895717121290ef1eb3a1c5718c`. It contains a richer sine-current map: 72 hot points, four cold MTPA entries, one cold field-weakening check, and ten static off-grid checks. Those are source-reported inventory counts, not a new independent validation of their raw runs.

Its `chk_top_2.25_42.5` explicitly has `pass=false`: the quadrature flux error is -0.5148869347361896%, beyond the owner's 0.5% off-grid flux criterion. Nine other recorded static checks have `pass=true`; the aggregate `checks.audit` is null. The original failed point must be preserved. The separate owner acceptance of replay04's exact solve003 mean-torque drift does not waive this different flux interpolation check.

The richer record also combines baseline commits `aadcb00` and `0031447` with different source trees. Its stored-duty comparisons include different temperatures and an older geometry/baseline; they cannot qualify the new exact reference by an unlabelled merge. Its labels explicitly say 2-D, sine current, no PWM, no controller losses and no 3-D end correction.

The next safe step is an isolated exact-L13 candidate/store binding and ordinary-user consumer check. A live no-solve import operation, full applicable Configure qualification and maximum S1 along an assigned propeller load remain separate unfinished gates.

## Accepted offline staging and consumer check

Private source commit `230e4d35` adds the pure staging adapter and its consumer tests. The parent reviewed its source and independently ran the adapter plus existing catalog/shared-reference, last-motor and granted-user load tests: **71 passed**, with seven upstream deprecation warnings. The actual original passport-store bytes are written only into temporary pytest storage, where their SHA is asserted. Existing routes prove exact L13 card/context, attached original variants, saved `ref_id` for an ordinary granted user, and denial for an ungranted user. Authentication and grants are fixture seams; no real customer account or frontend rendering was exercised by these tests.

The parent also independently rebuilt the pre-run manifest and produced an offline inspection stage through `verify_d85_import_stage.py`. The parent staging review hashes to `588d83de73db882f26f30d7736b77daf00299a1ce3dc869d3aaf4ab3b1c997d9`; the inspection stage hashes to `1563ec09d50208ac93d809be8e3e2be82095d4a12c8095e5424474f2ea9c6483`. The stage uses an empty isolated catalog for inspection and retains `qualification_claim=false`, `s1_claim=false`, `publication_allowed=false`, and `published=false`. Its decision explicitly does not authorize a live import. No route has been registered to write this stage into the shared catalog.
