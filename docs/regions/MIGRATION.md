# Moving one account to its home region (stage 3 runbook)

Design: `docs/regions/DESIGN.md` sections 5, 8, 13. Code: `src/motor_ai_sim/regions/migrate.py`,
CLI `scripts/migrate_account_region.py`. Tests: `tests/test_regions_stage3_migrate.py`, `tests/test_regions_stage3_auth.py`.

Every step is a **dry run unless `--apply`** is given, prints a report, and exits 1 when something blocks it. Run each
step inside the API container of the named server (`docker compose exec api-blue ...` or the live colour), where
`/srv/motres/{identity,shared,workspaces,published}` are mounted read-write and the peer env is set.

## Before the first account

1. Both servers run the same build (this PR), the peer channel works (Admin -> Servers -> Regions shows both up), and
   `/region-handoff` is served by the US web as the single-page app (the same fallback as every app path).
2. Classifier dry run on EU, owner reviews the list: `python scripts/classify_regions.py`. After his OK:
   `python scripts/classify_regions.py --apply` (or the admin override per account). This moves nothing.
3. The global `MOTRES_REGION_GUARD` stays **off** on both servers. The guard comes on per account in step 5.
4. A quiet window for the account (no jobs of its own). If the account is to be told, the mail text needs the owner's
   OK first.

## One account, `A` = its e-mail, `B` = the bundle folder

```bash
# 1. EU, dry run: what moves (workspace, published, owned projects, their dies + derived dies, production, tests,
#    CAD, verification case, NDA, directory person/company, acceptance/consent/audit lines) and what blocks it
python scripts/migrate_account_region.py export --email A
#    a die that belongs to the account but is not linked by its projects (for example the base die of a variant):
python scripts/migrate_account_region.py export --email A --die "DIE NAME" --die "DIE NAME variant"

# 2. EU: freeze the account's writes (reads go on, writes get 423) and build the bundle with a sha256 per file
python scripts/migrate_account_region.py export --email A --die ... --out /srv/motres/run/migrations --apply
#    -> prints bundle_dir B=/srv/motres/run/migrations/mig-<time>-<ws>

# 3. EU: copy B to the US host over WireGuard (rsync -a --checksum, modes 700/600); dry run first
python scripts/migrate_account_region.py transfer --bundle B --to 10.77.0.2:/srv/motres/run/migrations
python scripts/migrate_account_region.py transfer --bundle B --to 10.77.0.2:/srv/motres/run/migrations --apply

# 4. US: verify every hash, install, create the account (same password hash, role, grants, home_region us)
python scripts/migrate_account_region.py verify --bundle B
python scripts/migrate_account_region.py import --bundle B
python scripts/migrate_account_region.py import --bundle B --apply

# 5. EU: read the US receipt over the signed peer channel, compare the root hash, then guard on + unfreeze
python scripts/migrate_account_region.py finish --email A --bundle B
python scripts/migrate_account_region.py finish --email A --bundle B --apply
```

Abort at any point before step 5: `python scripts/migrate_account_region.py unfreeze --email A --apply` (lifts the
freeze, nothing else changes; an imported US copy is then removed by hand on US before the next attempt).

After step 5 the account signs in on EU and lands on US without a second password (the EU copy still holds the same
password hash, so the login 421 carries a hand-off ticket); an open EU tab is moved on its next API call the same way.

## Later: delete the EU copy

Only after the US backup has run once with the account in it (owner decides; EU backups age out by rotation):

```bash
python scripts/migrate_account_region.py delete-source --email A --bundle B --target-backup-confirmed
python scripts/migrate_account_region.py delete-source --email A --bundle B --target-backup-confirmed --apply
```

It deletes every EU file whose bytes are still the bundle's, the die access entries
of the moved dies and the directory person, then purges the account (`account_lifecycle.purge`, reason
`region_migrated`). Append-only logs (admin audit, verification events, NDA signature lines, duty-results ledger rows)
stay; the purge pseudonymises what it pseudonymises for every deletion. From then on the EU directory (synced from US)
sends the person to US; a password login there has no ticket any more (the person types the password on US).
A file changed on EU since the export blocks the step (listed); `--allow-changed` goes on, keeping changed shared files
while the workspace and published folders go with the purge.

## What does not move

Sessions, agent keys and OAuth grants (tokens are per server: the person signs in on US and reconnects AI apps there);
anything of another account; a project the account only views. Projects with several owners move with this account
(the plan warns).

## Blockers and their switches (owner's decision each time)

| Blocker | Switch |
|---|---|
| a moved die is also linked by a project that stays | `--allow-shared-dies` (the other project then loses the die on EU after delete-source) |
| a moved die is open in the public catalog | `--allow-open-dies` |
| the account has running or queued jobs | `--allow-active-jobs` (better: wait) |
| staff account, home region not yet `us`, already guarded | none: fix the cause |
