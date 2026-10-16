# Unified catalogues — stage 1 (bearings, lubricants, power devices), 2026-09-28

Owner-approved stage 1 of `catalogs_unified_proposal_2026-09-28.md`.

## Decisions
- **Envelope next to the body, not around it.** Each bearing/lubricant entry keeps its
  fields where `motor_ai_sim.bearings` has always read them; the envelope lives in a
  sibling `catalog:` block (manufacturer, part_number, status, sources, prov,
  validation, revision) that the loader ignores. File-level `catalog_sources:` and
  `catalog_units:` hold the source table and units. Why: loaders, file locations and
  ids (the keys machines reference) must not change; a `body:` wrapper would have
  forced a loader change for zero gain.
- **Device provenance is derived, not duplicated.** Device cards already carried a
  `source` per block, `basis: table|figure` per point and `null`+note for unpublished
  values. `motor_ai_sim.catalog.devices` derives `sources`/`prov`/`validation`
  (SPICE-vs-datasheet) from them; each card only gained a `catalog:` block with
  status + revision.
- **Comments moved into data.** All 45 inline "catalogue value to verify" /
  "APPROXIMATE" comments of `bearings_library.yaml` are now `prov.<field>.verify: true`
  or `type: estimate` with the original text as `note`; the block comments that
  flagged values (61814-2RS1 seal constants, the task-supplied angular-contact
  R1/R2/R3/S1/S2) became verify flags too. A test fails if any comes back as a comment.
- **Editing is admin-only.** `POST /api/controller/devices` was open to any caller;
  it now requires admin (and so does the new `POST /api/catalog/cards/device`). Both
  are also in `auth._GATED_PREFIX` ahead of `/api/catalog`, so workspace layering
  cannot relax them.

## Gate
`tests/test_catalog_envelope.py`: per card, old loader == adapter == the numbers the old
loader gave on the pre-envelope file (floats by `repr`, frozen in
`tests/fixtures/catalog_golden/`); body hashes equal the pre-envelope entries;
61811-2RS1 stays `validated` with 0.387 ± 0.025 N·m measured vs 0.400 model.

## Not in stage 1
PDF import to `draft`; a bearing/lubricant writer (the YAML stays git-edited by an
admin); `draft` cards are not yet excluded from solves (no draft card exists).
