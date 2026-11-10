# Moving one account to its home region (stage 3 runbook)

Design: `docs/regions/DESIGN.md` sections 5, 8, 13. Code: `src/motor_ai_sim/regions/migrate.py`,
CLI `scripts/migrate_account_region.py`. Tests: `tests/test_regions_stage3_migrate.py`, `tests/test_regions_stage3_auth.py`,
`tests/test_regions_stage3_review.py`.

Every step is a **dry run unless `--apply`** is given, prints a report, and exits 1 when something blocks it. Run every
step inside the API container of the named server (`docker compose exec api-blue ...` or the live colour), where
`/srv/motres/{identity,shared,workspaces,published}` are host bind mounts (read-write) and the peer env is set —
**except the transfer, which runs on the host**: the API image has no rsync or ssh.

**Where the bundle lives**: `/srv/motres/identity/region_bundles/<bundle id>/` (0700; override `--out` or
`MOTRES_REGION_BUNDLES_DIR`). It is a host bind mount, so the bundle survives a blue/green switch and the host can rsync
it. Never use `/srv/motres/run` or any path that is not mounted into the container: the bundle (it holds the password
hash, IP addresses of the sign-in log, verification documents) would land in the container layer and be lost — or
linger — with it. The US copy is deleted by a successful `import`; the EU copy by `delete-source`.

## Before the first account

1. Both servers run the same build, the peer channel works (Admin -> Servers -> Regions shows both up), and
   `/region-handoff` is served by the US web as the single-page app (the same fallback as every app path).
2. Classifier dry run on EU, owner reviews the list: `python scripts/classify_regions.py`. After his OK:
   `python scripts/classify_regions.py --apply` (or the admin override per account). This moves nothing.
3. The global `MOTRES_REGION_GUARD` stays **off** on both servers. The guard comes on per account in step 5.
4. A quiet window for the account (no jobs of its own). If the account is to be told, the mail text needs the owner's
   OK first.

## One account, `A` = its e-mail, `B` = `/srv/motres/identity/region_bundles/<bundle id>`

```bash
# 1. EU container, dry run: what moves (workspace, published, owned projects, their dies + derived dies, production,
#    tests, CAD, verification case, NDA, directory person/company, acceptance/consent/audit lines) and what blocks it
python scripts/migrate_account_region.py export --email A
#    a die that belongs to the account but is not linked by its projects (for example the base die of a variant):
python scripts/migrate_account_region.py export --email A --die "DIE NAME" --die "DIE NAME variant"

# 2. EU container: freeze the account (every write: 423, incl. its MCP keys / OAuth tokens and the password and reset
#    routes; reads go on) and build the bundle: sha256 per file, root hash signed with the peer secret -> prints B
python scripts/migrate_account_region.py export --email A --die ... --apply

# 3. EU container (dry run) prints the exact command; run it on the EU HOST, as the user that owns /srv/motres/identity
python scripts/migrate_account_region.py transfer --bundle B --to 10.77.0.2:/srv/motres/identity/region_bundles
rsync -a --checksum --chmod=D700,F600 -e ssh B/ 10.77.0.2:/srv/motres/identity/region_bundles/<bundle id>/

# 4. US container: check the hashes and the signature, install, create the account (same password hash, role, grants,
#    home_region us); a pending receipt first, done at the end; then the US copy of the bundle is deleted
python scripts/migrate_account_region.py verify --bundle B
python scripts/migrate_account_region.py import --bundle B
python scripts/migrate_account_region.py import --bundle B --apply

# 5. EU container: re-hash every bundle file on EU, compare the password and disabled state with the bundle, read the US
#    receipt over the signed peer channel; then home region, guard on, agent keys + OAuth grants on EU revoked, dies
#    pinned, directory entry, freeze lifted
python scripts/migrate_account_region.py finish --email A --bundle B
python scripts/migrate_account_region.py finish --email A --bundle B --apply
```

`finish` refuses when anything of the account changed on EU after the export (a staff edit of a moved project, a
password change, a disable): then `unfreeze`, remove the US copy (step "Abort"), export again.

**Abort** before step 5: `python scripts/migrate_account_region.py unfreeze --email A --apply` (lifts the freeze, nothing
else changes). If the US import already ran, remove the US account and files by hand before the next attempt (owner's
OK; the receipt under `/srv/motres/identity/region_migrations/` names the bundle). An interrupted import (receipt
`pending`) can simply be run again: the account it created is accepted when it equals the bundle's.

After step 5 the account signs in on EU and lands on US without a second password (the EU copy still holds the same
password hash, so the login 421 carries a hand-off ticket); an open EU tab is moved on its next API call the same way.
Its AI apps must reconnect on US (keys and grants are per server and were revoked on EU).

## Later: delete the EU copy

Only after the US backup has run once with the account in it (owner decides; EU backups age out by rotation):

```bash
python scripts/migrate_account_region.py delete-source --email A --bundle B --target-backup-confirmed
python scripts/migrate_account_region.py delete-source --email A --bundle B --target-backup-confirmed --apply
```

It deletes every EU file whose bytes are still the bundle's, the die access entries of the moved dies and the directory
person, purges the account (`account_lifecycle.purge`, reason `region_migrated`) and deletes the bundle folder B.

* A file **outside a die** that changed on EU since the export blocks the step (listed): nothing written after the
  export is deleted silently. Review it with the owner, then fix the cause.
* A **die** is kept whole on EU (all its files and its access entry; listed under `kept_dies` with the reason) when any
  of its files changed, or — checked again at this moment — a project that stays links it or another account is in its
  access list.
* Append-only logs (admin audit, verification events, NDA signature lines, duty-results ledger rows) stay; the purge
  pseudonymises what it pseudonymises for every deletion.

From then on the EU directory (synced from US) sends the person to US; a password login there has no ticket any more
(the person types the password on US).

## What does not move

Sessions, agent keys and OAuth grants (tokens are per server); anything of another account; a project the account only
views.

## Blockers and their switches (owner's decision each time)

| Blocker | Switch |
|---|---|
| a moved project has other non-staff members (they would keep writing to the EU copy) | `--allow-project-members` |
| a moved die is also linked by a project that stays, or another account is in its access list | `--allow-shared-dies` (delete-source then keeps that die on EU) |
| a moved die is open in the public catalog | `--allow-open-dies` |
| the account has running or queued jobs | `--allow-active-jobs` (better: wait) |
| staff account, home region not yet `us`, already guarded, no configured peer | none: fix the cause |
