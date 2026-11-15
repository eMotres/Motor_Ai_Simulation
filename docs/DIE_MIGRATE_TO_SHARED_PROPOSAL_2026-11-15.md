# Proposal: "move a workspace-only die to the shared catalog" (2026-11-15, low priority; proposal first, code after the owner's word)

## The defect (found by the production session on the CIANO10 12_40)
`PATCH /api/family/die/<name> {"name": ...}` by a verified admin on a die that exists ONLY in his own workspace answers
409 "'<die>' is still a workspace-only motor; migrate its complete catalog copy before saving it to the shared catalog".
Every admin catalog write is routed to the shared catalog by `routes/family.py::_admin_shared_die_dir` (:1452), which refuses a
workspace-only die on purpose (a single YAML write would create a half-shadowed shared die and strand its results) and
names a migration that NO route performs. So such a die can neither be renamed nor saved to the shared catalog by its admin
owner.

## What exists to build on
- `duplicate_die` (`routes/family.py` ~3340): copies die.yaml, every configuration, `runs/` (die-relative payloads).
- `die_rename.py`: the transaction runner (steps with dry run, undo closures, audit line) and the list of every store keyed by the die
  name (duty results, active context, workspace memory, catalog cards, grants, die access list, projects, production, motor
  revisions, motor tests, CAD, manual files).

## Proposed action
`POST /api/family/die/{die}/migrate-to-shared` body `{"name": optional new name, "dry_run": bool}`, verified admin only
(`require_catalog_write` + `is_verified_admin_request`), audited (`die.migrate_to_shared`).

1. Preconditions, each a 409 with a message that names the fix: the die exists in the caller's workspace and NOT in the shared
   catalog; the target name (new name or the same) is free in the shared catalog (name clash = refuse, never merge); the name passes
   `die_names` rules; the die is not a published community copy.
2. Complete copy as ONE transaction: die.yaml (name field, `locked` kept), every configuration yaml, `runs/` tree, duty results and
   history sidecars, passports and catalog card, the CAD models of the die, production / projects that link it (only when the
   workspace owns them). Verification: file list + sha256 of every copied file, compared after the copy; failure = undo (remove the
   shared copy), nothing else touched.
3. With a new name the die_rename steps rewrite the keyed stores in the COPY (reuse, not a second list); a rename inside the same
   migration is therefore one operation, not "migrate then rename".
4. The workspace original is ARCHIVED, not deleted: moved to `<workspace>/dies/.migrated/<die>-<timestamp>/` with a
   `MIGRATED_TO_SHARED.json` (target name, time, actor, file digests); the name stops resolving to the workspace, so reads fall
   through to the shared copy. Restoring = moving the folder back (documented, no route).
5. `dry_run` returns the plan (files, bytes, keyed stores touched, clash result) and writes nothing.
6. Error text of `_admin_shared_die_dir` names the action: "... migrate it with POST /api/family/die/<die>/migrate-to-shared
   (admin), then save".

## Tests (temp workspace, no catalog data)
dry run writes nothing; migration copies every file and the digests match; the keyed stores follow a rename; a name clash refuses
and changes nothing; a failing step (injected) rolls back and leaves the workspace original in place; the original is archived and
the name resolves to the shared copy; a non-admin gets 403; the 409 text names the action; the 12_40-like case (workspace-only
die, admin PATCH rename) works after the migration.

## Size and risk
Medium: one route, one module (`die_migrate.py`) reusing the rename steps, one error text. Risk: moving data between layers; hence the
dry run, the digests, the undo and the archive instead of a delete. No existing behaviour changes (the 409 stays for the old paths
until the action is used).
