# Admin → Servers (cluster load monitor)

The Admin tab's **Servers** panel shows the load of every host in the project
and what the API is doing. Multi-node from day one: each host runs the same
small agent and reports to the API with its own token.

## Pieces

| piece | where | what |
|---|---|---|
| node agent | `deploy/node-agent/motres_node_agent.py` | stdlib Python, reads `/proc` + `docker ps/stats`; POSTs a sample every 15 s |
| systemd unit | `deploy/node-agent/motres-node-agent.service` | `Nice=10`, `CPUQuota=5%`, `MemoryMax=64M` |
| installer | `deploy/node-agent/install.sh` | copies the agent to `/opt/motres-node-agent`, token to `/etc/motres-node-agent.env` (0600), enables the unit |
| store | `src/motor_ai_sim/cluster_monitor.py` | node registry (sha256 token hashes), latest sample in memory, SQLite history |
| routes | `src/motor_ai_sim/routes/cluster.py` | `/api/admin/nodes*`, `/api/admin/cluster/*` |
| panel | `web/src/components/admin/ServersPanel.tsx` | node cards, 24 h / 7 d charts, top processes, jobs with Stop |

Data lives in `<identity dir>/cluster/` (the folder of `users.json`, i.e.
`/srv/motres/identity/cluster/` on the server); override with
`CLUSTER_MONITOR_DIR`. History: 1-min averages kept 24 h, 15-min averages kept
7 d. A node with no sample for 60 s is **offline**.

## Sample contents

CPU total and per core, load average, RAM, swap, disk per mount, network
rx/tx rate (physical interfaces), uptime, top processes (name, user, nice,
CPU %, RSS, container name via cgroup), docker containers with CPU/RAM and an
approximate "created" timestamp (from `docker ps`, used for the outside-app
Now table's uptime column). `cores` is the logical/thread count (one entry
per `/proc/stat` `cpuN` line, i.e. SMT/hyperthreading counted separately);
`cores_physical` is the distinct (physical id, core id) count from
`/proc/cpuinfo` (0 if unreadable -- older node agents before this field
existed, or a host whose `/proc/cpuinfo` lacks those ids, also read as
"unknown" since `cores_physical` then falls back to the thread count).
The agent sends process **names only** — never command lines or environments.
The API additionally drops any `env`/`cmdline`/`args` keys and redacts
token-, password- and key-like strings before storing.

## API

| route | auth |
|---|---|
| `POST /api/admin/nodes/metrics` | node bearer token (`mnode_…`) |
| `GET /api/admin/nodes` | admin — nodes, latest sample, status, cluster totals |
| `POST /api/admin/nodes` `{name}` | admin — mint token (plaintext returned once; same name = re-key) |
| `POST /api/admin/nodes/{id}/revoke`, `DELETE /api/admin/nodes/{id}` | admin |
| `GET /api/admin/nodes/{id}/history?range=24h\|7d` | admin |
| `GET /api/admin/cluster/app` | admin — API p50/p95 (5 min), MCP calls/min + 429s, job queue |
| `POST /api/admin/cluster/jobs/{run_id}/stop` | admin |

## Install on a server

1. Admin tab → Servers → **Add server** → name (e.g. `eu1`) → **Create token**.
   Copy the token; it is shown once.
2. Copy `deploy/node-agent/` to the host and run the installer as root:

   ```bash
   scp -r deploy/node-agent root@<host>:/tmp/
   ssh root@<host> 'bash /tmp/node-agent/install.sh --url <API base> --token <mnode_…>'
   ```

   `<API base>`: on the host that runs the app stack use
   `http://127.0.0.1:8080` (the web container, which proxies `/api`); on any
   other host use the public origin, e.g. `https://eu1.emotres.com`.
3. The card turns green within ~15 s. Check with
   `systemctl status motres-node-agent` / `journalctl -u motres-node-agent`.

Requirements: `python3` (3.8+); `docker` CLI optional (for container names).
Rotate a token: Add server with the same name, re-run the installer.
Remove: `systemctl disable --now motres-node-agent`, then Revoke in the panel.

Dry run (prints one sample, sends nothing):
`python3 /opt/motres-node-agent/motres_node_agent.py --once`

## Out-of-app CPU (Admin -> Overview live load)

Heavy work (agent studies: `mesher_*`, `prof_*`, `stagea_*`, and other
sandboxed processes) often runs in its own docker containers or host
processes, outside the app's job queue that `job_usage` meters — that showed
up as the server sitting at real CPU % while the "CPU by user" chart stayed
empty. `outside_app_series`/`outside_app_now` in `cluster_monitor.py` turn the
SAME per-container/host samples the node agent already sends into a series
next to the per-user one:

- real docker containers, by name (top N by CPU + an "other" bucket);
- `host` — processes outside any container (the node agent's own top-N
  process sample, so a host with many small non-top processes can be
  undercounted; good enough for the picture, not exact accounting);
- `app-overhead` — the app's own containers (`deploy-api-1`/`deploy-web-1`,
  override with `CLUSTER_APP_CONTAINERS`) minus the job CPU `job_usage`
  already attributed to users that minute (`CLUSTER_JOB_CONTAINER`, default
  `deploy-api-1`) — nginx/uvicorn/GC, not a user's job, but needed so the two
  series add up to the container's measured CPU. Clamped at 0 (logged) if a
  sampling-skew minute reads more job CPU than container CPU.

History lives in `container_fine`/`container_coarse` (same two resolutions
and retention as `fine`/`coarse`). `GET /api/admin/load/live` returns it as
`outside_app` (`items`, `app_overhead_key`, `series`) alongside `user_load` --
its overflow bucket is `"outside-other"`, not `"other"`, since the web panel
merges the two series into one chart by ts (`mergeLoadSeries`) and
`user_load_series` already uses `"other"` for its own overflow,
plus `outside_app.now` (currently running non-app containers: name, node,
CPU %, RAM, approx uptime) and `monitoring_since` (oldest registered node,
for a "collecting since HH:MM" hint on a short history instead of an
empty-looking chart). The web panel never offers to stop a container — only
job-queue rows get a Stop button.

`GET /api/admin/load/live` also returns `nodes_now` (id, name, status,
`cores` — threads, `cores_physical`, `cpu` now, `mem_total`), the per-server
snapshot the panel's legend/tooltips and the compact strip above the charts
use for "eu1 (16 threads / 8 cores)" labels and "threads used"
(`cores × CPU % / 100`); `cores_physical` is `null` for an older node agent
or a host whose `/proc/cpuinfo` lacks physical/core ids.

**Layout** (owner feedback, "too grey/dull" -> three separate charts, a
responsive grid, three across on wide screens and stacked on narrow):
CPU % per server and RAM % per server are now two separate gradient-filled
area charts (one colour per server, shared between its CPU and RAM series;
a dashed cluster-mean line in each), and CPU by user and process is its own
stacked area chart. All three read `web/src/components/admin/LiveLoadPanel.tsx`
+ `liveLoadSeries.ts` (series shaping) + `liveLoadColors.ts` (colour).

**Colour** (`liveLoadColors.ts`): built from the dataviz skill's validated
categorical eight, re-checked with `validate_palette.js` against this app's
own chart surfaces (`--panel-2`: `#ffffff` light / `#0b1220` dark, not the
skill's generic ones) — all checks pass both modes. Users get 6 of the 8
slots, hash-assigned (`hashSlot`: djb2 + a Thomas-Wang-style avalanche
finalizer, needed because djb2's low bits alone are biased under a small
modulo) so a user keeps the same colour across refreshes and reloads
regardless of who else is in the current top-N. The other two slots are
*reserved*, not hashed into: orange for the outside-app container family
(one hue, hash-stable opacity per container — the "family is the signal,
legend/tooltip carry the individual name" composite encoding the skill
recommends past ~3 simultaneous series) and aqua for "host" — so a user's
hash draw can never land on the same colour as the outside-app family. A new
muted-warm pair (not one of the eight) is "app (idle/overhead)"; "other" (in
either family) stays flat muted grey (`--text-4`). The panel requests
`top=6` to match the 6-slot user palette exactly. A user with a currently
running agent/MCP-submitted job gets a 🤖 marker in the legend/tooltip name
(from the live queue snapshot, not per-minute history — an honest
limitation).

## Usage accounting (machine time per client)

Every job the queue runs is metered (`src/motor_ai_sim/job_usage.py`, hook in
`jobs.InProcessQueue._run_admitted`) and one row per finished job is stored in
`history.sqlite` table `usage`: account, client (`web`, or the MCP key / OAuth
client name), kind, machine/duty, node (`NODE_NAME` or hostname), wall s,
CPU s, peak RSS, status (`done`/`stopped`/`failed`). No backfill.

CPU is **measured**: CPU time of the API process tree (psutil: process +
live descendants + reaped children). A 2 s sampler gives each interval's delta
to the jobs running in it: one job gets it all (`exclusive`); several split
it by each job thread's own CPU time (`apportioned`). Peak RSS is the tree's
peak while the job ran, so jobs running at the same time share it.
Running jobs show their live CPU seconds in the Servers job table.

| route | |
|---|---|
| `GET /api/admin/usage?by=user\|client&days=1\|7\|30` or `start`/`end` (epoch s) | totals: jobs, CPU-h, wall-h, peak RAM, % of jobs CPU, % of cluster capacity |
| `GET /api/admin/usage/jobs?user=…\|client=…` | drill-down job list |
| `GET /api/admin/usage.csv?by=…&detail=summary\|jobs` | CSV |

Groundwork for billing/quotas: `job_usage.cpu_hours(user, start, end)`.
MCP quotas still count runs per day; nothing has been switched to CPU minutes yet.

## Pricing data (record only, no billing)

`src/motor_ai_sim/usage_stats.py` collects the signals a pricing decision needs.
It stores aggregate counts only, never file contents, and every route is admin-only.

- **Per account per day** (from the `usage` table): CPU s and wall s by kind
  (em / coupled / thermal / sweep / optimizer / controller / mechanical / report / other),
  jobs, failed, stopped, agent runs, queue wait, peak concurrent jobs.
- **Activity** (table `activity`, counted by the HTTP middleware on 2xx):
  report PDFs, datasheet exports, Fusion import/export, catalog views; MCP calls
  by tool and 429s (from the MCP audit). Logins come from the session event log.
- **Storage** (table `storage`, daily): bytes per workspace in dies / configs /
  results / reports / other.
- **Account attributes**: plan (the `plan` field if set; `internal` for admins
  and the owner, `free` otherwise), e-mail domain, created date.

**Cost basis** (Admin → Usage → Pricing data, `cost_basis.json`). The default is eu1 =
Hetzner AX42-1 (FSN1) at €100/month (the owner's figure; confirm it from the invoice),
16 threads, and storage at €0/GB-month.
`€ per CPU-hour = €/month ÷ (threads × 730 h)` (default ≈ €0.0086).
The cost of an account is `CPU-h × €/CPU-h + mean GB × €/GB-month`.

| route | |
|---|---|
| `GET/PUT /api/admin/usage/cost_basis` | read / edit the cost basis |
| `GET /api/admin/usage/monthly?month=YYYY-MM[&format=csv]` | per-account monthly table |
| `GET /api/admin/usage/daily?days=7` | per-account per-day rows |

**Daily job** on the host: `deploy/systemd/motres-usage.{sh,service,timer}` runs at
00:20 UTC. It takes the storage sample and writes
`/srv/motres/usage/usage_YYYY-MM.{json,csv}`: the current month to date, plus the
finished previous month on the 1st.
Install:
```bash
cp /opt/motres/app/deploy/systemd/motres-usage.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now motres-usage.timer
```
Manual run: `docker compose exec -T api python -m motor_ai_sim.usage_stats monthly 2026-09 [--csv]`.
