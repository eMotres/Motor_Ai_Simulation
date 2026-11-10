# Install note: US catalog mirror, directory sync, cross-region compute, US backups

For the coordinator (stage 2, `docs/regions/DESIGN.md` sections 7, 9, 12). Nothing here is installed by a deploy: every step
is a manual one on the named server. Everything stays **off** until the steps for it are done.

**Prerequisites**: WireGuard up (EU `10.77.0.1`, US `10.77.0.2`); `MOTRES_REGION`, `MOTRES_PEER_URL` (the peer's WireGuard
address and API port) and `MOTRES_PEER_HMAC_SECRET[_FILE]` set on both; this build deployed on both; `rsync` installed on both.
The application user inside the containers is uid/gid **10001**.

## 1. EU: the read-only library module (runs as 10001, shows only the manifest files)

The module shows **exactly the files of the current manifest**: the API rewrites its include file
(`/srv/motres/identity/mirror/rsync-library.include`) on every manifest build, and `exclude = *` hides everything else, so a closed
die's folder is not even listable. The module runs as uid 10001, never as root.

```bash
sudo install -m 644 deploy/regions/rsyncd-motres.conf /etc/motres/
sudo install -d -o 10001 -g 10001 -m 0755 /srv/motres/identity/mirror        # the API writes the include file here
sudo cp deploy/systemd/motres-rsyncd-library.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo ufw allow in on wg0 from 10.77.0.2 to 10.77.0.1 port 873 proto tcp      # WireGuard only, never the public interface
sudo systemctl enable --now motres-rsyncd-library.service
```

**Acceptance check (do not skip; run it after the first manifest request, i.e. after step 2's first run, from US):**

```bash
rsync -r --list-only rsync://10.77.0.1/motres-library/dies/        # only the mirrored dies appear
rsync -r --list-only rsync://10.77.0.1/motres-library/dies/<a-closed-die>/   # must fail / list nothing
```

Without a generated include file (no manifest built yet) the module shows nothing.

## 2. US: the mirror and the directory sync

```bash
sudo touch /etc/motres/region-us        # the units run only where this marker exists
sudo chown -R 10001:10001 /srv/motres/shared      # the worker (10001) must be able to delete what the job installed
sudo cp deploy/systemd/motres-catalog-mirror.{service,timer} deploy/systemd/motres-directory-sync.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
# FIRST RUN, by hand: look at the plan, then adopt the copy that is already on US.
sudo bash /opt/motres/app/scripts/mirror_catalog_us.sh            # refuses with exit 3 and says why (no state file yet)
sudo bash /opt/motres/app/scripts/mirror_catalog_us.sh --init --prune-leftovers
sudo systemctl enable --now motres-catalog-mirror.timer motres-directory-sync.timer    # every 15 min / every 5 min
```

The job creates `/srv/motres/identity/mirror` for 10001 and fetches with `rsync --chown=10001:10001 --chmod=D755,F644`, so no file
it installs is root-owned.

`--init` adopts the copy already on US and removes, from `.duty_results.json`, `end_effect_passports.json` and `die_access.json`,
every die that EU does not mirror and that has no folder of its own on US (US-local dies, i.e. closed motors of US accounts,
are never touched). `--prune-leftovers` also deletes the **files** of closed dies that an earlier hand copy left under
`dies/`, `passports/` and `cad/`. EU sends no die name for this: it sends an HMAC (peer secret) of each excluded die name and US
matches them against its own folders. Run the first plan without `--prune-leftovers` if unsure: the output shows the number of
leftovers.

Behaviour to know:

| Exit | Meaning |
|---|---|
| 2 | the US copy has local edits, or a mirrored path collides with a file the job did not install. Nothing was changed; the unit shows *failed* until somebody looks. |
| 7 | a file or row that must go could not be removed (permissions), or a US store is corrupt. The unit shows *failed*; nothing is lost, the next run retries. A de-published die cannot silently stay on US. |
| 75 | EU or rsync did not answer (retried at the next tick, not a failure). |

The job deletes only files **it installed** (`/srv/motres/identity/mirror/catalog_mirror_state.json`). A corrupt US
`.duty_results.json`, `end_effect_passports.json` or `die_access.json` is never overwritten.

## 3. Cross-region compute (optional, off by default)

```bash
sudo install -d -o 10001 -g 10001 -m 0700 /srv/motres/run/foreign_jobs          # both servers; mounted by deploy/docker-compose.yml
# add MOTRES_CROSS_REGION_COMPUTE=1 to /etc/motres/api.env on BOTH servers, then restart the worker when it is idle
```

Only job kinds with a registered handler **and** an input allow-list (`regions/foreign.INPUT_KEYS`, today `selftest.cpu` with the
input `n`) can cross; any other kind, or a body with an input outside the list, runs at home unchanged. Foreign jobs wait in
`run/foreign_jobs/` for at most 24 h and are in **no** backup. Expiry, hand-back retries and the 24 h sweep run in the worker
even when the switch is later turned off. Switch off = remove the variable.

## 4. US backups (Backblaze B2, US region; own repository, own key)

```bash
sudo apt install restic
sudo install -m 600 -o root -g root deploy/backup/backup-us.env.example /etc/motres/backup-us.env     # then the owner fills it
sudo install -m 644 deploy/backup/restic-include-us.txt deploy/backup/restic-exclude.txt deploy/backup/restic-exclude-us.txt /etc/motres/
sudo bash -c 'set -a; . /etc/motres/backup-us.env; set +a; restic init'                                 # once, as root: the file is 0600
sudo cp deploy/systemd/motres-backup-us.{service,timer} deploy/systemd/motres-backup-us-prune.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now motres-backup-us.timer motres-backup-us-prune.timer
```

The hourly unit only backs up. `forget --prune` (24 hourly / 30 daily / 12 monthly) and `restic check` run **daily** in
`motres-backup-us-prune.service`. The upload cap is passed as `--limit-upload` (restic has no environment variable for it).

**The owner fills in `/etc/motres/backup-us.env`** (0600, root only; never in git, a unit file or a log):

| Variable | Value |
|---|---|
| `B2_ACCOUNT_ID` | key ID of a B2 application key restricted to the one backup bucket (not the master key) |
| `B2_ACCOUNT_KEY` | that application key |
| `RESTIC_REPOSITORY` | `b2:<bucket>:<path>`, e.g. `b2:motres-us-backup:restic` |
| `RESTIC_PASSWORD` | repository encryption key (`openssl rand -base64 48`); keep a copy in a password manager |
| `LIMIT_UPLOAD_KIB` | upload cap in KiB/s, a number (default 20480; 0 = unlimited) |

The mirrored library is not backed up (restorable from EU); `run/foreign_jobs/` never is. Do a restore rehearsal once after
the first hourly run.

## Rollback

`systemctl disable --now motres-catalog-mirror.timer motres-directory-sync.timer motres-backup-us.timer motres-backup-us-prune.timer`;
remove `MOTRES_CROSS_REGION_COMPUTE`. The mirrored files and merged rows stay where they are.
