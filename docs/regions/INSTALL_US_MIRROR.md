# Install note: US catalog mirror, directory sync, cross-region compute, US backups

For the coordinator (stage 2, `docs/regions/DESIGN.md` sections 7, 9, 12). Nothing here is installed by a deploy: every step
is a manual one on the named server. Everything stays **off** until the steps for it are done.

**Prerequisites**: WireGuard up (EU `10.77.0.1`, US `10.77.0.2`); `MOTRES_REGION`, `MOTRES_PEER_URL` (the peer's WireGuard
address and API port) and `MOTRES_PEER_HMAC_SECRET[_FILE]` set on both; this build deployed on both; `rsync` installed on both.

## 1. EU: the read-only library module

```bash
sudo install -m 644 deploy/regions/rsyncd-motres.conf deploy/regions/rsyncd-motres.include /etc/motres/
sudo cp deploy/systemd/motres-rsyncd-library.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo ufw allow in on wg0 from 10.77.0.2 to 10.77.0.1 port 873 proto tcp     # WireGuard only, never the public interface
sudo systemctl enable --now motres-rsyncd-library.service
```

Check from US: `rsync --list-only rsync://10.77.0.1/motres-library/materials_library.yaml` (a directory listing is refused on purpose).

## 2. US: the mirror and the directory sync

```bash
sudo touch /etc/motres/region-us        # the units run only where this marker exists
sudo cp deploy/systemd/motres-catalog-mirror.{service,timer} deploy/systemd/motres-directory-sync.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
# FIRST RUN, by hand: look at the plan, then adopt the copy that is already on US.
sudo bash /opt/motres/app/scripts/mirror_catalog_us.sh            # refuses with exit 3 and says why (no state file yet)
sudo bash /opt/motres/app/scripts/mirror_catalog_us.sh --init --prune-leftovers
sudo systemctl enable --now motres-catalog-mirror.timer motres-directory-sync.timer    # every 15 min / every 5 min
```

`--prune-leftovers` deletes files of dies EU does **not** mirror (what the earlier hand copy left behind: closed motors).
Run it without the flag first if unsure: the plan line lists the number of leftovers. After the first run US carries only the
open catalog; its `.duty_results.json` rows, `end_effect_passports.json` entries and `die_access.json` entries come from EU.

Behaviour to know: exit **2** = the US copy has local edits or a file collides with a mirrored path (nothing was changed; the
unit shows *failed* until someone looks; the plan names the files in the journal). Exit 75 = EU unreachable (retried, not a
failure). The job only deletes files **it installed** (`/srv/motres/identity/mirror/catalog_mirror_state.json`); closed motors
of US accounts that live on US are never touched.

## 3. Cross-region compute (optional, off by default)

```bash
sudo install -d -o 10001 -g 10001 -m 0700 /srv/motres/run/foreign_jobs          # both servers; mounted by deploy/docker-compose.yml
# add MOTRES_CROSS_REGION_COMPUTE=1 to /etc/motres/api.env on BOTH servers, then restart the worker when it is idle
```

Only kinds with a registered handler can cross (today `selftest.cpu`); routes whose work is a closure keep running at home.
Foreign jobs wait in `run/foreign_jobs/` for at most 24 h and are in **no** backup. Switch off = remove the variable.

## 4. US backups (Backblaze B2, US region; own repository, own key)

```bash
sudo apt install restic
sudo install -m 600 -o root -g root deploy/backup/backup-us.env.example /etc/motres/backup-us.env     # then the owner fills it
sudo install -m 644 deploy/backup/restic-include-us.txt deploy/backup/restic-exclude.txt deploy/backup/restic-exclude-us.txt /etc/motres/
set -a; . /etc/motres/backup-us.env; set +a; sudo -E restic init                                       # once
sudo cp deploy/systemd/motres-backup-us.{service,timer} /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl enable --now motres-backup-us.timer
```

**The owner fills in `/etc/motres/backup-us.env`** (0600, root only; never in git, a unit file or a log):

| Variable | Value |
|---|---|
| `B2_ACCOUNT_ID` | key ID of a B2 application key restricted to the one backup bucket (not the master key) |
| `B2_ACCOUNT_KEY` | that application key |
| `RESTIC_REPOSITORY` | `b2:<bucket>:<path>`, e.g. `b2:motres-us-backup:restic` |
| `RESTIC_PASSWORD` | repository encryption key (`openssl rand -base64 48`); keep a copy in a password manager |

The mirrored library is not backed up (restorable from EU); `run/foreign_jobs/` never is. Do a restore rehearsal once after
the first hourly run.

## Rollback

`systemctl disable --now motres-catalog-mirror.timer motres-directory-sync.timer motres-backup-us.timer`; remove
`MOTRES_CROSS_REGION_COMPUTE`. The mirrored files and merged rows stay where they are.
