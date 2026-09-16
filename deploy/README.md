# Deploy — motor_ai_sim engineering portal

Target: **Hetzner AX42** (Ryzen 7 PRO 8700GE, 8 c / 16 t, 64 GB, 2 × 512 GB
NVMe), Ubuntu 24.04, Docker. One node, two containers plus a one-shot:

| service | what it is |
|---|---|
| `api` | FastAPI + the FEM solver, `:8001`, non-root (`motres`, uid 10001) |
| `web` | the built frontend behind nginx, which also proxies `/api` — one origin, no CORS |
| `motres-seed` | a one-shot that builds the three-layer state tree from a copy of `config/` |

The bare-metal alternative (systemd units, no Docker) is in
[`systemd/`](systemd/) and is documented at the bottom.

---

## The state tree

```
/srv/motres/
  shared/        materials_library.yaml  bearings_library.yaml
                 fusion_param_map.yaml   motor_presets.json
                 dies/<die>/die.yaml  <cfg>.yaml       READ-ONLY to the app
  published/<ws_id>/<die>/…                            community layer
  workspaces/<ws_id>/  motor_config.yaml  .last_*  .duty_results.json
                       dies/<die>/runs/…              per-account overlay
  identity/      users.json  .auth_secret  .sessions.json
  logs/          api.log (the app's own rotation, under journald)
```

`shared` is mounted **read-only** in the API container. That is not belt and
braces: if the overlay direction is ever wrong, the failure has to be a visible
`EROFS` in a log and not a customer's solve quietly rewriting the vendor
catalog.

```bash
sudo mkdir -p /srv/motres/{shared,published,workspaces,identity,logs}
sudo chown -R 10001:10001 /srv/motres      # the image's `motres` user
```

### The identity trap

`users.json`, `.auth_secret` and `.sessions.json` are **not** workspace-scoped
and never should be. They resolve from the folder of `MOTOR_AI_SIM_CONFIG`.
Pointing the process config at `/srv/motres/identity/motor_config.yaml` is what
puts the identity layer where the backup set expects it — get this wrong and the
accounts land inside somebody's workspace, where the next `rm -rf` of a stale
workspace takes every login with it.

---

## The env contract

Two files, deliberately different in kind:

| file | when | mode | holds |
|---|---|---|---|
| `/etc/motres/api.env` | **runtime** — `env_file:` / `EnvironmentFile=` | `root:root 0600` | everything below |
| `deploy/.env` | **build** — compose interpolation only | `0640` | `GOOGLE_CLIENT_ID`, `ADMIN_EMAILS`, `SEED_SRC` |

The frontend bakes `VITE_GOOGLE_CLIENT_ID` into its bundle, so that value has to
exist at `docker build` time and cannot come from the runtime file. An OAuth
client id is public by design (the browser sends it) — which is why this split
is safe, and why the client id is the only thing in both.

Template with the full rationale: [`scripts/api.env.example`](../scripts/api.env.example).
The same file, same variable names, is what `scripts/start_api.cmd` reads on the
Windows workstation, so a value proven there is the value that ships.

| variable | default | what it decides |
|---|---|---|
| `WORKSPACES_ROOT` | *(unset)* | **the master switch.** Unset = single-user: every path is the process config directory, exactly as before the migration. Set = per-account workspaces. |
| `PUBLISHED_ROOT` | `<WORKSPACES_ROOT>/../published` | the community layer |
| `SHARED_ROOT` | *(process config dir)* | the read-only catalog + libraries |
| `MOTOR_AI_SIM_CONFIG` | repo `config/motor_config.yaml` | the process workspace **and the identity layer** — see the trap above |
| `QUEUE_WORKERS` | `2` | concurrent solves. Physical cores / 4 (§2.4): **2** on an AX42, 4 on an AX102. Measured: a pool of 10 pinned workers stretched a 60 s eval to a 231 s median. |
| `AUTH_SECRET` | *generated* | HS256 session key. **Set it explicitly.** Losing the generated file signs every account out permanently and `users.py` refuses by design to mint a replacement. `openssl rand -hex 32` |
| `GOOGLE_CLIENT_ID` | *(empty)* | Google sign-in. Must equal the frontend's `VITE_GOOGLE_CLIENT_ID`, and the production origin must be listed under *Authorized JavaScript origins* in the Google console **before** cutover. |
| `ADMIN_EMAILS` | — | comma-separated, always admin, overrides `users.json` |
| `AUTH_ENFORCE` | `1` in the image | `1` = real tiers; `0` = everyone is admin (dev only) |
| `PUBLIC_EXHIBIT` | `1` | `1` = the anonymous public exhibit (tree + geometry + report of the passported dies) — the workstation default. **`0` on any internet-facing host**: no credentials ⇒ 401 on every `/api` route but `/api/health`, `/api/me` (anonymous shape, so the SPA renders its login screen) and `/api/auth/login|google|logout`. Registered accounts are unaffected — grants still decide what each sees. |
| `ALLOWED_ORIGINS` | *(empty)* | extra CORS origins. Only needed if the frontend is served from a **different** origin than the API; the same-origin nginx default needs nothing. |
| `ANTHROPIC_API_KEY` | *(empty)* | in-app support assistant; empty = a flagged mock reply |

The API refuses to boot, or warns, on three of these at startup —
`src/motor_ai_sim/startup_checks.py`:

* **fatal** — two dies under any layer differing only by case. On NTFS they were
  one folder; on ext4 they are two dies that intermittently answer with each
  other's geometry, and there is no correct fallback.
  Check before the transfer: `python scripts/check_case_collisions.py config/dies`
* **warn** — `AUTH_SECRET` implicit while `WORKSPACES_ROOT` is set.
* **warn** — `reportlab` / `python-docx` / `triangle` missing.

---

## First deploy on the AX42

**0. Provision.** Ubuntu 24.04, ext4 on the NVMe mirror, `ufw` 22/80/443,
unattended-upgrades, a non-root admin user, Docker from the official repo.

**1. Transfer the catalog, bytes intact.**

```bash
# on the Windows box (a COPY of config/, never the live one)
python scripts/catalog_manifest.py config/dies -o before.json
python scripts/check_case_collisions.py config/dies        # must exit 0
tar -cf config-copy.tar config/                            # tar, NOT zip
```

`tar`/`rsync` and **not** a tool that NFD-normalises (macOS `zip`, some SMB
paths): real die folders are called `100 mm · 24s-28p mid-torque`, and a
decomposed copy is a *different directory* with a listing that still looks
right.

```bash
# on the server, after unpacking to /srv/motres/seed-src
python scripts/catalog_manifest.py /srv/motres/seed-src/dies -o after.json
python scripts/catalog_manifest.py --compare before.json after.json   # exit 0
```

**2. Secrets.**

```bash
sudo install -d -m 700 /etc/motres
sudo install -m 600 scripts/api.env.example /etc/motres/api.env
sudo editor /etc/motres/api.env        # AUTH_SECRET, GOOGLE_CLIENT_ID, the roots
cp .env.example deploy/.env            # GOOGLE_CLIENT_ID, ADMIN_EMAILS, SEED_SRC
```

Carry the **existing** `config/.auth_secret` across, or set `AUTH_SECRET` to its
contents. Either works; neither is optional — skipping both signs the owner and
every invited account out, irreversibly.

**3. Seed the tree.** Dry run first; the script writes nothing without
`--apply`.

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env \
  run --rm motres-seed                                   # prints the plan
docker compose -f deploy/docker-compose.yml --env-file deploy/.env \
  run --rm motres-seed \
    python scripts/migrate_to_workspaces.py --from /seed-src --to /srv/motres \
      --owner vadim@motresres.com --apply \
      --manifest /srv/motres/.seed_manifest.json
```

This is an explicit one-shot on purpose. The old compose seeded `config/` into
the `motor_config` **named volume**, and a named volume is only seeded when it
is *empty* — so the second deploy silently produced a server with no catalog and
no error anywhere.

**4. Verify the migration answers the Stage 0 questions.**

```bash
docker compose ... run --rm motres-seed \
  python scripts/migrate_to_workspaces.py --to /srv/motres \
    --verify /seed-src/reference_answers.json
python scripts/check_case_collisions.py --tree /srv/motres      # must exit 0
```

13 dies visible, the owner's `num_poles`/`num_slots` unchanged, one duty's
`.npz` loading with the torque the reference recorded.

**5. Build and start.**

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
docker compose -f deploy/docker-compose.yml logs -f api
curl -s localhost:8080/api/health          # {"status":"healthy"}
curl -s localhost:8080/api/me              # the real readiness probe
```

**6. TLS.** `certbot` + host nginx in front, proxying to `127.0.0.1:8080`, HSTS,
auto-renew timer. **Not** Cloudflare's orange cloud: the free tier cuts a
proxied request at 100 s and a Ø200 PWM transient runs 93 minutes. Cloudflare is
fine for DNS (grey cloud).

**7. Backups, before the first invite.** See below — and rehearse a restore.

---

## Update procedure

```bash
cd /opt/motres/app
git pull
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

State lives in host bind mounts under `/srv/motres`, so an image rebuild cannot
touch it — that is the whole reason the named volume went away. The seed
one-shot is **not** re-run on an update; it is only for a fresh tree or a
deliberate re-import.

If `requirements.txt` changed, the build reinstalls the whole native stack
(gmsh + OCP + MKL, ~5 min and ~1.5 GB). Nothing else about an update is slow.

## Rollback

1. **The image.** `git checkout <previous tag> && docker compose … up -d --build`.
   The state tree is untouched by a rollback, so this is safe in both directions
   as long as no migration stage changed the on-disk layout between the two.
2. **The state.** `restic restore latest --target /srv/motres.restored`, then
   swap the directories. Hourly snapshots mean at most an hour of a user's work.
3. **The whole server.** The Windows workstation stays running read-only for two
   weeks after cutover (§ Stage 11). Point DNS back; it is still the same
   `.auth_secret`, so sessions survive.

Rolling back **across** a layout change (single `config/` ⇄ three layers) is not
a rollback, it is a re-migration: run `migrate_to_workspaces.py` again against a
copy, and verify before pointing DNS.

---

## Backups

`systemd/motres-backup.{service,timer}` — restic to a Hetzner Storage Box over
SFTP, hourly, retention 24 hourly / 30 daily / 12 monthly, plus
`restic check --read-data-subset=5%` weekly
(`motres-backup-check.{service,timer}`).

Every store in this codebase is written atomically (tmp + `os.replace`), so a
copy taken mid-solve is at worst one write behind, never half a file. That is
what makes an hourly hot backup legal here.

* config: [`backup/restic.env.example`](backup/restic.env.example)
* what is saved: [`backup/restic-include.txt`](backup/restic-include.txt)
* what is skipped (reproducible caches): [`backup/restic-exclude.txt`](backup/restic-exclude.txt)

```bash
sudo apt install restic
sudo install -m 600 deploy/backup/restic.env.example /etc/motres/restic.env
sudo install -m 644 deploy/backup/restic-{include,exclude}.txt /etc/motres/
sudo -E restic init
sudo systemctl enable --now motres-backup.timer motres-backup-check.timer
```

**Where the secret lives.** Two of them, and both are outside the backup they
protect:

* `AUTH_SECRET` — in `/etc/motres/api.env` (`root:root 0600`), which *is* in the
  backup set, and in a password manager, which is the copy that survives losing
  the server.
* `RESTIC_PASSWORD` — in `/etc/motres/restic.env`, and in the password manager.
  A repository key stored only on the machine it protects protects nothing.

Neither is ever in git, in an image layer, or in a compose file.

## Monitoring

* `systemd/motres-health.{service,timer}` — a minute-by-minute probe of
  `/api/me` **through the public edge**. `/api/health` is liveness only: it is
  an `async def` that answers unconditionally, so it stays green while a wedged
  worker serves nothing. `/api/me` is a sync handler that reads `users.json` —
  a 200 means the loop dispatched, a pool thread was free and the identity store
  parses.
* Alert on: health failing > 300 s (five consecutive runs), queue depth > 2N for
  10 min, disk > 80 %, a backup timer failure.

---

## Bare metal (systemd, no Docker)

Containers are the recommendation — the native dependency set is exactly what
you do not want to reproduce by hand on a rebuild, and the image pins it. Use
[`systemd/motres-api.service`](systemd/motres-api.service) when you need
pinning finer than compose's `cpuset`, or on a host where Docker is unwanted.
It expects a venv at `/opt/motres/venv` built from the same `requirements.txt`.

`Restart=always` covers a crash. `WatchdogSec=300` covers the **hang** — process
alive, port open, nothing ever answering — which is the case
`scripts/backend_watchdog.ps1` was written for and which would otherwise be lost
in the port. `src/motor_ai_sim/watchdog_notify.py` is the heartbeat half: it
probes the **event loop** and never the threadpool, because a threadpool
saturated with 90-minute solves is a healthy busy server and killing it would
turn a liveness probe into an outage. It does nothing at all unless systemd sets
`NOTIFY_SOCKET`.

```bash
sudo cp deploy/systemd/motres-*.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now motres-api.service
sudo systemctl enable --now motres-health.timer motres-backup.timer \
                            motres-backup-check.timer
```

---

## Sizing

The FEM transient is CPU-bound (MKL PARDISO): real cores matter, 2–4 GB RAM per
concurrent solve at 200 mm / 2 mm mesh. On the AX42 that is `QUEUE_WORKERS=2`
and `cpuset: "0-11"`, leaving four threads for nginx, restic and the host. On an
AX102: `QUEUE_WORKERS=4`, `cpuset: "0-27"`.

## Testing the image

[`CONTAINER_TEST.md`](CONTAINER_TEST.md) — build the `test` target, run the
suite inside it, and render an L155 report through the container's own
reportlab/python-docx. Run it once on the first server before inviting anyone:
it is the check that the three newly-pinned deps actually work on Linux and that
a Cyrillic duty name renders instead of becoming black boxes.
