# Deploy — motor_ai_sim engineering portal

One node, two containers: `api` (FastAPI + FEM solver) and `web` (static
frontend + nginx, proxying `/api` to the api container — one origin, no CORS).
Only the working app ships: no Firebase, no torch/Modulus, no experiments.

## First deploy

```bash
git clone <repo> && cd motor_ai_sim
cp .env.example deploy/.env      # fill: ADMIN_EMAILS, GOOGLE_CLIENT_ID, AUTH_SECRET
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

- `ADMIN_EMAILS=vadim@motresres.com` — owner account(s), always admin.
- `GOOGLE_CLIENT_ID` — the OAuth client (docs/GOOGLE_SIGNIN_SETUP.md). Add the
  production origin (e.g. `https://emotres.com`) to the client's Authorized
  JavaScript origins in the Google console.
- `AUTH_SECRET` — any long random string; set it explicitly so session tokens
  survive container rebuilds (`openssl rand -hex 32`).
- `AUTH_ENFORCE=1` is baked in: anonymous visitors browse the catalog, `free`
  accounts get the configurator, `pro` runs FEM on their own motor copy, only
  admins touch the shared config/catalog.

TLS: terminate in front (simplest on Hetzner: `caddy` or host nginx + certbot
proxying to :80 of the `web` container), then put the https origin into
`ALLOWED_ORIGINS` only if the frontend is served from a DIFFERENT origin than
the API (same-origin default needs nothing).

## State

Everything persistent lives in the `motor_config` volume (`/app/config`):
`users.json` (accounts), `.auth_secret`, `motor_config.yaml`, the family
catalog (`dies/`), materials. Back it up:

```bash
docker run --rm -v motor_config:/c -v "$PWD":/b alpine tar czf /b/config-backup.tgz -C /c .
```

## Updates

```bash
git pull
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

The config volume is untouched by updates (the image's `config/` seed is only
copied into an EMPTY volume on first start).

## Sizing

The FEM transient is CPU-bound (MKL PARDISO): ~real cores matter, RAM ~2-4 GB
per concurrent solve at 200 mm / 2 mm mesh. Hetzner AX52/AX102 per the
hosting notes; scale the optimizer's worker pool via cpus in compose.
