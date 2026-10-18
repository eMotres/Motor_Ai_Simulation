# Deploy stage 2: a separate compute worker and a blue-green API

Owner's request, 2026-10-18 (approved "Yes"): restarting or updating the API must never kill or wait for a running
computation, the computations move into their own process that is restarted only when it is idle, and the API is switched
between two instances so that users see no 502 and no slow-load fallback.

Stage 1 (live) gave `deploy/plan_deploy.py` + `deploy/deploy_module.sh`: atomic web builds in `/srv/motres/web/builds/<sha>`,
an idle-aware API restart through `GET /api/admin/live`, and the update banner through `/api/notice`. Stage 2 builds on it.

* Branch `feat/deploy-stage2` (base `pre-migration-freeze-2026-09-15`). Written by Sonnet 5.5; not yet run against a real Docker.
* Everything below the first two sections is the analysis the owner asked for **before** the code, then the runbook for the
  cutover, then the daily operation and the risks.

---

## 1. What runs where

```
 browser ──► host nginx (TLS) ──► web container (nginx, 127.0.0.1:8080)
                                     │
        pages /srv/web/current ◄─────┤
                                     ├── worker paths (regex = roles.WORKER_PREFIXES) ──► worker:8002      MOTRES_ROLE=worker
                                     │        resolved per request through Docker DNS; 502 -> friendly 503
                                     └── everything else (/api, /oauth, /.well-known) ──► upstream motres_api
                                              = ONE line in /srv/motres/web/upstream/api.conf:
                                                `server api-blue:8001;`  or  `server api-green:8001;`
                                              (MOTRES_ROLE=api; the other colour is stopped except during a switch)
```

One image (`deploy-api:<sha>`), three services, the same `/etc/motres/api.env`, the same bind mounts (identity, workspaces,
shared, published, logs). `MOTRES_ROLE` (set in `docker-compose.yml`, never in `api.env`) selects the behaviour; the default
`all` is today's single process (workstation, BYO installs, the test suite) and is unchanged.

### Why an HTTP-level split, and not a queue with workers that claim jobs

The brief asked for "a worker entrypoint that claims jobs". The queue (`jobs.InProcessQueue`) is an admission gate **inside
the request**: in the default blocking mode the HTTP request thread *is* the worker - the route body is a Python closure
(`jobs.queued`), the answer is the HTTP response of a request that stays open for minutes to an hour, progress / cancel /
last-result live in module state of the same process (`progress.registry`, `routes.*._LAST`, the optimizer's `_scan_state`,
the geometry singleton and the FEM caches per workspace), and exactly one handler (`selftest.cpu`) is registered in
`jobs.HANDLERS` - the "replay a record in another process" seam (`RedisQueue`) is a documented stub. Moving the *workers*
without moving the *routes* would require rewriting ~25 routes (and every reader of their in-memory state) into
serialisable handlers, which is not a thing to do the night before the launch week.

So the process boundary is placed where the code already has its boundary - the HTTP route. Every route that runs, watches or
stops a computation, **and** every route that reads or writes the live workbench state next to it, is served by the
worker process; nginx picks the process from the path. Inside the worker everything is the queue you know (priority, fairness
per account, `QUEUE_WORKERS` slots, cancel hooks, the job-usage meter). What the worker adds is:

* a **drain flag** (`POST /api/worker/drain`): no new job is admitted (503 + `Retry-After`), queued and running ones finish;
* a **state endpoint** (`GET /api/worker/state`): jobs, requests in flight, build, drain flag - read by the API for Admin and by
  the deploy script;
* **stale-record recovery** at start (`jobs.recover_stale_records`);
* the **admission guard** in the API role: `jobs.InProcessQueue.submit` raises `ComputeNotHere` (HTTP 503) in
  `MOTRES_ROLE=api`, so a misrouted request or a code path that was not moved can never start a job in a process that the
  next update switches out. This is the "the API stops running queue workers when the flag says so" requirement, made a hard
  guarantee: `jobs.queued`, `jobs.admit`, `jobs.as_job` and the MCP `simulate` all go through `submit`.

A real queue handoff (records in Redis, workers claim) stays the documented Stage-4 seam; nothing here prevents it.

### Which paths are the worker's

`motor_ai_sim/roles.py` is the single source (`WORKER_PREFIXES`, `WORKER_EXACT`, `WORKER_PATTERNS`); `deploy/nginx.conf`
carries `roles.nginx_regex()` and `tests/test_deploy_stage2_roles.py` fails if they disagree, if a route tagged `@queued`
is not a worker path, or if a worker prefix names no route.

| worker (147 of 555 routes) | API (408) |
|---|---|
| `/api/simulation`, `/api/static3d`, `/api/mechanical`, `/api/thermal`, `/api/coupled`, `/api/optimization`, `/api/controller`, `/api/system`, `/api/kernel` - the solvers and their progress / cancel / last-result routes | `/api/auth`, `/api/me`, `/oauth`, `/.well-known`, `/api/admin`, `/api/account`, `/api/nda`, `/api/verification`, `/api/newsletter`, `/api/support`, `/api/design-agent`, `/api/production`, directory |
| `/api/jobs`, `/api/history`, `/api/sims` - read the in-memory queue / progress, or hold the runs | `/api/family` (catalog tree, configurations, datasheet, payload, publish ...), `/api/catalog` (except the passport), `/api/presets`, `/api/my_motors`, `/api/model3d`, `/api/propellers`, `/api/components`, `/api/bearings`, `/api/materials/library`, `/api/panel_settings`, `/api/sweep/config`, `/api/nodes` (BYO control), `/api/cluster`, `/api/activity` |
| `/api/geometry`, `/api/config`, `/api/winding`, `/api/mesh`, `/api/parts`, `/api/materials` (the assignment) - they read and write the workspace's live in-memory machine that the solvers read | `/api/health`, `/api/version`, `/api/notice`, `/api/admin/live` |
| `/api/family/report` - reads the four solver routers' last-result stores | |
| `/api/catalog/{id}/passport` - minutes of FEM inside the request | |
| `/mcp` - an agent's `simulate` queues a job and `get_job` reads it back from the same queue | |

Consequences the owner should know: (a) the whole simulation workbench (and MCP) is served by the process that is restarted
only when idle, so a code change in a solver waits for idle - that is the request; (b) the pages of those tabs depend on the
worker, so during a worker restart they get a friendly `503 {"code":"worker.unavailable"}` + `Retry-After: 15` (nginx
`@worker_down`), shown after the 2-minute banner and a wait for idle; (c) the catalog, accounts, admin, production, support and
sign-in - what most fixes touch - are on the API colours and update with no wait and no gap.

---

## 2. Single-instance assumptions: what they were, and what makes each safe now

Rule used throughout: state that lives in a process's memory stays in exactly **one kind** of process (the worker, for the
workbench; the API for the web-facing counters), state that lives in a file is made safe for several writers, and work that
may run only once runs in exactly one place.

### 2.1 In-memory job registries and run state

| what | where (code) | made safe by |
|---|---|---|
| the job queue: waiting / running / cancelled sets, per-account fairness counters, 512 records | `jobs.InProcessQueue` | exists **only in the worker** (admission guard refuses in the API role). `GET /api/jobs`, cancel and every progress route are worker paths, so a poll always reaches the process that holds the run |
| job record files `<ws>/.jobs.json` (written on submit / finish; a crash leaves `queued` / `running`) | `jobs._save`, `live_status._jobs` | atomic replace; the worker marks leftovers `interrupted` at start (`recover_stale_records`) so a crash can never hold the next deploy's idle check for ever; the worker's live status counts its in-memory queue, not the files |
| progress registry, field-view gate and dedupe, solve pool (child processes), job-usage meter and its sampler threads | `progress`, `field_jobs`, `solve_pool`, `job_usage` | all created lazily by a job -> worker only. Admin -> Servers asks the worker for its queue (`cluster_monitor.jobs_view` -> `GET /api/worker/state`); CPU accounting now sees a process tree that contains only jobs |
| optimizer campaign slots (`_scan_state`, `_scan_lock`, DOE / descent / auto state), static-3D solve worker, report progress | `routes/optimization`, `routes/static3d`, `report_progress` | worker paths (start, progress, cancel) - one process holds the state and its three routes |
| the "last result" slots, the geometry singleton, the FEM / thermal / mechanical caches (per workspace) | `workspace.WorkspaceState` | live in the worker; the routes that read or write them are worker paths; **cross-process changes are announced** - see 2.3 |
| BYO remote jobs (`remote_jobs.json`) | `compute_nodes` | file store, lock now cross-process (2.4) |
| MCP agent jobs (`agent.*`) | `agent_designs.simulate` -> `queue().submit(block=False)` | `/mcp` is a worker path, so the job, its status and its result are read from the same queue |

### 2.2 Background threads, schedulers, leader-only work

| item | runs | rule |
|---|---|---|
| `sweep_resume.resume_incomplete_sweeps` (lifespan) | single-user mode only; a no-op on the server (`WORKSPACES_ROOT` set) | additionally gated by role: the API role never calls it (it would start a job) |
| stale job-record recovery | worker start only | the worker is the only process that can know nothing of its own is running |
| job-usage sampler + attribution flusher | started by the first metered job -> worker | n/a in the API |
| optimizer / static-3D / design-agent **threads** | optimizer, static-3D: worker. Design assistant answer thread (`design_agent.runner`): API | a thread is not a request, so the blue-green drain would not see it: the answer thread now registers with `live_status.background("design-agent")`, and the drain waits for `busy_total = requests + background` |
| short mail / notify / DNS-check threads (`auth-mail`, `nda-mail`, `notify-send`, `email-domain-check`, `trusted-proxy-resolve`) | API | seconds long; the drain keeps the old colour at least 30 s after the switch |
| `routes.geometry._warm_live_mesh` (a boot-time timer that builds viewer meshes) | worker and `all`; skipped in the API role | the geometry routes are the worker's |
| `watchdog_notify` (systemd `NOTIFY_SOCKET`) | no-op in containers | unchanged |
| MCP session manager (`mcp_app.lifespan`) | every process | stateless streamable HTTP, nothing shared |
| host timers: retention, usage, backup, health; `motres-cad-rebuild` (host service + its queue files) | host systemd | they `exec` into the live API colour through `bluegreen.sh exec api ...` (units updated); one writer each |
| ERP sync | the ERP pulls `/api/erp-export/...`; no schedule inside the app | API |
| node agent | host systemd, posts to `/api/admin/nodes/metrics` | API; unchanged |

Nothing in the application is an in-process cron: there is no "leader" election to build - the only leader-only work (sweep
resume, stale-record recovery) is tied to the role that can legitimately run it.

### 2.3 Caches and the workbench state that a run or an edit refreshes

* **File-backed caches** key on `stat` (mtime, size) and are safe across processes: `config._config_cache` (follows the file),
  `routes.catalog._REF_CACHE`, `inverter.devices._CACHE`, `motor_access._CFG_VIS_CACHE`, `part_materials._CACHE`.
* **In-memory workbench state** (geometry singleton, mesh / field / transient caches, last results) is per process. Before stage
  2 a config write cleared it with `routes.simulation.clear_simulation_caches` and `geometry_service.*`. Two processes would
  each hold their own copy, so a preset applied through the API would leave the worker solving the previous machine.
  **`state_epoch.py`**: every workspace has a file `<ws>/.state_epoch`; the funnels that already clear the caches
  (`clear_simulation_caches`, `update_current_geometry`, `reset_geometry`) bump it; `workspace.WorkspaceMiddleware` compares it
  on each request of that workspace and evicts the *other* process's in-memory state when it moved and the move was not its
  own. Eviction costs time, never data (`WorkspaceState.evict` documents it). Off unless the role is `api` / `worker`.
* "Every Run replaces all caches" (memory note) stays true: runs and their caches are in the worker only.
* The caches are keyed on the geometry fingerprint anyway (`clear_simulation_caches` calls itself "a belt, not the braces"),
  so a missed bump is a stale entry that the next request with different inputs does not hit - the pre-existing situation when
  an optimizer subprocess edits the YAML.

### 2.4 File writes without cross-process locks

The stores were "load, change one entry, write the whole file" under a `threading.RLock`; the atomic `os.replace` protects a
reader but two interleaved read-modify-write cycles lose one edit, and several used a fixed `*.tmp` name that two processes
can collide on. Several processes now write them (two API colours during a switch, the API and the worker).

`file_lock.StoreLock` is a drop-in for the `RLock` (`with lock:`): the same thread lock, then an `flock` on a sidecar
`.<name>.lock`, released in the reverse order (so lock order between stores is unchanged). Off outside the multi-process roles
(no sidecar files on a workstation). Bounded wait (20 s, then it logs and goes on - never wedges a request). Verified with real
`flock` under Linux (WSL): 3 processes x 150 read-modify-write cycles = exactly 450 with the lock, lost updates without.

| store | writers in several processes? | status |
|---|---|---|
| `users.json`, `.sessions.json` (every authenticated request touches `last_seen`, in whichever process serves it) | API colours (switch), API + worker | **locked** (`users._LOCK`, `sessions._LOCK`) |
| `json_store` stores: `motor_catalog.json`, `motor_presets.json`, the design-agent conversations, ... (101 call sites) | API + worker | **locked** (`json_store.lock_for` returns a `StoreLock`) |
| `.duty_results.json` (a run files its answer; `POST /api/family/duty_result` too) | worker + API | **locked** (`duty_results._STORE_LOCK`) |
| `remote_jobs.json`, `user_nodes.json`, `node_prefs.json` (BYO) | worker queues, API leases | **locked** (`compute_nodes._LOCK`) |
| `newsletter.json`, `comm_prefs.json`, `admin_audit.jsonl` | API colours (switch) | **locked** |
| `activity.sqlite`, `history.sqlite` (usage, cluster) | sqlite, `timeout=5` | multi-process safe by SQLite |
| append-only logs (`auth_events.jsonl`, `mcp_audit.jsonl`, `api.log`) | all | `O_APPEND` single-line writes; the "trim to the second half" at 20 MB can race with an append - a lost audit line, accepted |
| per-workspace stores of the solvers (`.last_*`, ledger, scan cache) | worker only | single writer |
| `production/` store, `verification/` store, `support_store` tickets / chats, `nda_signatures.jsonl`, `data_sharing_audit`, `agent_keys.json`, `oauth_grants.json` | API only (colours overlap only while the old one drains in-flight requests) | **not locked**: the overlap is the drain window and the old colour receives no new request; residual risk, see 6 |
| `motor_config.yaml` of a workspace: `PATCH /api/materials|parts|winding|mesh` (worker) vs `family.activate -> update_mesh_config` (API, admin only) | both | not locked; admin-only operations, seconds apart; residual risk |

### 2.5 SSE, websockets, long polls

There are none (`StreamingResponse` is a file download in `production/routes`; `pipeline/stream` belongs to an optional module
that is not installed; MCP answers plain JSON). What exists is the **blocking solve request**: an HTTP request that stays open
up to an hour. Those requests are the worker's (`proxy_read_timeout 3600s` there), so they are never on a colour that gets
switched out. nginx `reload` lets the old workers finish their connections; the drain waits for requests and background
threads before the old colour stops.

### 2.6 Rate limits

All in memory, per process: sign-in / sign-up / link / mail limiters (`auth_email.Limiter`), the anonymous support-chat caps
(`routes/support._ip_hits`, `_global_hits` - these protect a paid provider), communication-preference token limiter, activity
beat caps, MCP per-minute quota (`agent_keys._Q_*`, now in the worker). A switch gives the new API colour empty counters - the
same as every API restart before - so a lock-out or a daily cap is forgotten at a deploy. Acceptable for the sign-in
limiters; for the support-bot daily cap it means a deploy grants a fresh daily budget (bounded by the number of deploys a day;
no regression versus stage 1). Persisting those counters is a follow-up, not tonight's work.

### 2.7 Live status (heartbeats per tab, in-flight counters)

* Heartbeats (`activity.record_beat` -> `live_status.note_tab`) go to `/api/activity/beat` -> the API colour; the counters of
  requests in flight are per process (`InflightMiddleware`). `GET /api/admin/live` on the API **merges the worker's**
  (`live_status.merge_worker`: jobs from the worker's in-memory queue, requests per module added, `worker` block with
  reachable / draining / build / job rows), `?local=1` is the process alone (what the drain asks the old colour). The Admin
  "Now on the site" panel shows a worker chip (ready / updating / not reachable) and the worker's jobs.
* A freshly switched API colour has not heard the open tabs yet (one beat a minute), so an empty "users" answer is not evidence.
  `bluegreen.sh switch` records `SWITCHED_AT`; the worker update waits until `SWITCHED_AT + 75 s` (`API_SETTLE_S`) before it
  trusts the idle check.
* If the worker cannot be asked, the API keeps the stage-1 record-file view of the jobs and reports `worker.reachable=false`;
  the deploy then treats it as busy (it never restarts what it cannot see).

### 2.8 Startup migrations and boot-time work

`_lifespan`: `run_startup_checks` is read-only (case-collision check, secret warning, report deps) and runs in every process;
`workspace.provision(email)` at sign-in creates the workspace layout idempotently (the API creates it, the worker finds it);
`sweep_resume` as in 2.2; no schema migration runs at boot (`migrate_derived_geometry`, the seed and the catalog scripts are
explicit one-shots). Two colours starting next to each other therefore race on nothing. The one-shot `motres-seed` service now
uses the built image.

---

## 3. Memory and CPU budget (AX42: 16 threads, 64 GB, shared with the ERP in `/srv/erp`)

`ppg2-*` containers and the ERP are never named by any script here: the scripts only address `deploy-web-1`,
`deploy-worker-1`, `deploy-api-blue-1`, `deploy-api-green-1` (and `deploy-api-1` at the cutover). Do not use
`--remove-orphans`, `compose down` or any prune on the server.

| service | cpuset | quota | scheduler weight | memory ceiling | notes |
|---|---|---|---|---|---|
| worker | 0-11 | 12 cpus | `cpu_shares` 512 (ERP 2048) | **36 GB** (stage 1: 40 GB in the one process) | `QUEUE_WORKERS=2`, 2 GB `/dev/shm` for PARDISO, `stop_grace_period` 120 s |
| api-blue / api-green | **12-15** (never the solver's cores) | 3 cpus | 512 | **5 GB each** | light work now; both exist only during a switch |
| web | 12-13 | 1 cpu | 512 | 256 MB | unchanged |

* Steady state ceiling: 36 + 5 + 0.25 = **41.25 GB** (stage 1: 40.25) - the API now has its own cap, so a solver OOM kills only
  the worker and a request storm cannot take memory from a running solve.
* Worst case during a switch: 36 + 5 + 5 + 0.25 = **46.25 GB**, leaving 17.75 GB for the ERP, restic, the host and the page cache
  (stage 1 left 23.75 GB). These are ceilings, not reservations; the idle colour of an API that serves pages and catalog rows
  is expected to sit below 1.5 GB. `tests/test_deploy_stage2_roles.py` pins: worker cpuset 0-11 unchanged, API cpuset disjoint
  from it, steady <= 41.5 GB, worst case leaves >= 17 GB.
* cpuset 12-15 for the API colours shares threads with the host and the ERP (the stage-1 "four threads for nginx, restic and
  the host"); their weight (512 vs the ERP's 2048) keeps the ERP first under contention, as before.
* **Measure before trusting the 36 GB** (not measured here - no server access): the largest process footprint the old single
  process reached in 30 days is in the job meter (it is the whole tree's peak RSS):
  ```bash
  docker exec deploy-api-1 python -c "import time; from motor_ai_sim import job_usage as U; r=U.jobs(time.time()-30*86400, time.time(), limit=100000); print(len(r), 'jobs; peak', round(max([x['peak_rss'] for x in r] or [0])/2**30,1), 'GB')"
  ```
  If that says more than ~30 GB, raise `mem_limit` of the worker in the compose file *before* the cutover and take the
  difference from the API's 5 GB (e.g. 3 GB each) - the sum, not the split, is the constraint.
* How the old fences changed: `cpuset 0-11 / cpus 12 / cpu_shares 512 / shm 2 g` moved unchanged from `api` to `worker`;
  `mem_limit` 40 g -> 36 g; the API colours are new (above); `web` unchanged.

---

## 4. Cutover runbook for tonight (the orchestrator runs it; nothing here was run against the server)

Time: **about 25-40 minutes of work, one 2-3 s blip.** Active steps: build 5-12 min (the requirements layers are cached; web npm
build 2-4 min), waiting for idle (0 to MAX_WAIT x 3), worker + api-blue start 1-3 min (the app imports for ~20-60 s), recreate
web 3 s, verification 1 min, draining the old API at least 30 s (usually under 2 min). The only user-visible interruption is
the web container being recreated (2-3 s of 502 on the edge) - choose a quiet minute. A running job blocks the cutover (ABORT 4,
nothing changed): the old API is the single process that holds them.

**Prerequisites (from the PC, repo at the stage-2 commit `<SHA>`, merged to the deploy branch):**

```bash
SSH="ssh -i ~/.ssh/motres_ax42 root@176.9.84.229"
PREV=$($SSH cat /opt/motres/app/.deployed_commit)
git archive --format=tar <SHA> -o /tmp/deploy_main.tar
scp -i ~/.ssh/motres_ax42 /tmp/deploy_main.tar root@176.9.84.229:/tmp/deploy_main.tar
```

**Step 1 - look before touching (read-only):**

```bash
$SSH 'free -g; df -h /var/lib/docker /srv/motres; docker ps --format "{{.Names}}\t{{.Status}}"'
$SSH 'docker stats --no-stream --format "{{.Name}} {{.MemUsage}} {{.CPUPerc}}"'          # ERP + ppg2-* + the old api: what is there
$SSH 'docker exec deploy-api-1 python -c "import time; from motor_ai_sim import job_usage as U; r=U.jobs(time.time()-30*86400, time.time(), limit=100000); print(len(r), \"jobs; peak\", round(max([x[\"peak_rss\"] for x in r] or [0])/2**30,1), \"GB\")"'
$SSH 'docker exec deploy-api-1 curl -s http://127.0.0.1:8001/api/admin/live' | python -m json.tool | head -30   # jobs, users
```

Stop and decide if the peak is above ~30 GB (section 3) or the box has less than 12 GB free.

**Step 2 - preflight (changes nothing):**

```bash
$SSH 'cd /tmp && rm -rf cutover_src && mkdir cutover_src && tar -x -C cutover_src -f /tmp/deploy_main.tar deploy \
      && sed -i "s/\r$//" cutover_src/deploy/*.sh && bash cutover_src/deploy/cutover_stage2.sh --check <SHA> '"$PREV"
```

Expect `PREFLIGHT OK`. Every `FAIL` line names its fix (not root, wrong deployed commit, a stage-2 container already exists,
less than 12 GB available, ...).

**Step 3 - go:**

```bash
$SSH 'cd /tmp/cutover_src && bash deploy/cutover_stage2.sh --go <SHA> '"$PREV"' 20'
```

What it does and prints, in order (details in the script header): preflight; tags the running image `deploy-api:stage1`;
unpacks and builds `deploy-api:<SHA>` and `deploy-web`; **`nginx -t` of the new web image and `compose config` of the new file must
pass**; waits for the old API to be idle (ABORT 4 if a job runs for 3 x MAX_WAIT; shows the 2-minute banner first when only users
are present); swaps `/opt/motres/app`; writes `state.env` and the upstream (`api-blue`); starts the worker, waits for its
health (`build == <SHA>`, `role == worker`); starts api-blue, waits healthy, warms it up; **recreates the web container** (the
blip); verifies through the edge: `/api/health` says `role api, instance blue`; a worker route (`/api/simulation/config`)
answers with `X-Motres-Instance: worker`; `/api/version` with `blue`; `/build.json` 200; `/api/worker/state` is closed (not
reachable from outside); then drains the old API (requests + jobs, 30 s minimum) and stops and removes it (its image stays as
`deploy-api:stage1`); finally publishes the pages of `<SHA>` through `deploy_module.sh <SHA> <SHA> web`.

**Step 4 - verify by hand (2 minutes):**

```bash
$SSH 'bash /opt/motres/app/deploy/bluegreen.sh state'                  # active=blue, worker running, upstream api-blue
curl -s https://aerostator.com/api/health                              # role api, instance blue, build <SHA>
curl -sI https://aerostator.com/api/simulation/config | grep -i x-motres-instance      # worker
curl -sI https://aerostator.com/api/version            | grep -i x-motres-instance      # blue
$SSH 'bash /opt/motres/app/deploy/bluegreen.sh exec worker python -m motor_ai_sim.worker state' | head -20
$SSH 'docker ps --format "{{.Names}}\t{{.Status}}\t{{.Image}}"'        # web, worker, api-blue (+ the ERP, ppg2-* untouched)
```

In the browser (the owner's admin account): Admin -> "Now on the site" shows the **compute worker: ready** chip and the job
rows; Admin -> Servers lists the queue; start one short transient in the Simulation tab: it must queue, run and finish, with the
progress bar moving (that is the whole worker path: nginx -> worker -> queue -> progress poll); the Catalog / Configure tab
loads; sign out and in.

**Step 5 - housekeeping after a green cutover:**

```bash
$SSH 'cp /opt/motres/app/deploy/systemd/motres-retention.service /etc/systemd/system/ && systemctl daemon-reload'   # exec via bluegreen.sh
# motres-usage.sh is read from /opt/motres/app/deploy/systemd/ and already uses bluegreen.sh
$SSH 'systemctl start motres-retention.service && journalctl -u motres-retention -n 5 --no-pager'                    # dry check
```

**Rollback:**

| when | command | effect |
|---|---|---|
| any failure in `--go` before step 8 | automatic (`do_rollback`) | the old tree and the stage-1 `nginx.conf` are put back (web recreated), the new containers stopped, `state.env` and `api.conf` removed; stage 1 was never stopped |
| after `--go`, the old API still exists (only when its drain timed out) | `bash cutover_stage2.sh --rollback` | same as above |
| after the old API was removed | `bash cutover_stage2.sh --rollback` | re-creates it from `deploy-api:stage1` with the stage-1 compose file, recreates web with the stage-1 conf; the stage-2 containers are stopped |
| an API problem found later (not the cutover) | `bash deploy/bluegreen.sh rollback` | nginx back to the previous colour (instant while it still runs, else it is started with its previous image first) |

Do not: run a bare `docker compose up -d` (starts only `web`, harmless but confusing), `--remove-orphans`, `compose down`,
`docker system prune`.

---

## 5. Daily operation after the cutover

| change | `plan_deploy.py` says | what happens | who waits |
|---|---|---|---|
| pages only | `web` | publish + symlink switch (stage 1) | nobody |
| admin / accounts / production / support / mail ... | `api` | build, start idle colour, warm, switch, drain, stop | nobody; no 502 |
| catalog / family / presets / shared helpers | `back` | API as above; the **worker** restarts when idle | the worker restart, not the users of the API |
| solver code | `back` + narrow `MODULES` | same | worker restart waits for idle (all jobs) and for users of the touched tabs |
| compose file, `VERSION`, Dockerfile, requirements | `full` / `back` with `all` | all parts | the worker waits for the whole site to be idle |

```bash
python deploy/plan_deploy.py <PREV> <SHA> [<WORKER_PREV>]     # "<MODE> <MODULES>"; WORKER_PREV = /opt/motres/app/.worker_commit
bash deploy/deploy_module.sh <SHA> <PREV> <MODE> [MODULES] [MAX_WAIT_MIN]
```

* `MODE=worker` is only the **follow-up** after `WORKER PENDING` (exit 7): the API and pages are already on `<SHA>`, a job kept
  the worker busy past `MAX_WAIT_MIN x 3`, nothing was restarted. Repeat `deploy_module.sh <SHA> <SHA> worker` when it is idle.
  `plan_deploy.py` judges the worker from `.worker_commit`, so a pending update is never forgotten.
* The worker restart: wait for idle -> `drain on` (new jobs get 503 + Retry-After) -> check idle again (a request that slipped in
  is waited for, `drain off`) -> `compose up -d --no-build --no-deps worker` -> health `build == <SHA>` -> else automatic
  rollback to the previous image.
* API rollback after the fact: `bash deploy/bluegreen.sh rollback`. The previous colour is stopped after a successful drain;
  rollback starts it with its recorded image first.
* Exit codes of `deploy_module.sh`: 0 done, 2 usage, 3 not the deployed commit / no cutover yet, 5 build or start
  failed (nothing switched), 6 a check failed after the switch (rolled back), 7 api/web done, worker pending.

---

## 6. Risks

1. **Never run against a real Docker/nginx by the author.** Covered: bash scripts run against a fake `docker` under Windows
   (git-bash) and real Linux (WSL), real `flock` under Linux, the compose file parsed and its fences asserted, the nginx regex
   asserted against the application's real routes. Not covered: `nginx -t` of the new config (the cutover runs it on the new web
   image before switching), the compose `<<:` merge on the server's compose version, the real image build, DNS resolution of
   `worker` by nginx. The cutover's gates are exactly those: a failure there changes nothing.
2. **A workbench route that is not in the table.** The table was derived from the code (queued routes, the module-level state
   they read, and the routes that reach it); 147 of 555 routes are the worker's. A route that reads in-memory solver state
   from the API process would show stale or empty data until the epoch evicts it. The test catches queued routes and dead prefixes,
   not a *reader* added later. Mitigation: the first week, watch Admin -> "Now on the site" and the logs for "another process changed
   the machine ... dropped"; fix by adding the prefix to `roles.py` (one line, one nginx regex line, a test).
3. **Page boot and the worker.** `/api/config`, `/api/geometry*`, `/api/jobs` are worker paths, so a worker restart (rare, after
   the banner and a wait for idle) shows the friendly 503 for ~1 minute to someone opening the Simulation tab. Pages that need only
   the catalog, sign-in and Configure are unaffected.
4. **MCP now depends on the worker** (restart window as above; agents retry).
5. **The one blip at the cutover** (web recreate, 2-3 s) and the memory ceilings (not measured here, section 3).
6. **Cross-process locks fail open after 20 s** by design; the unlocked stores of 2.4 rely on the old colour receiving no new
   request after the switch; a request already in flight on the old colour can still write at the same instant as the new one.
7. **Mixed versions while the worker is pending.** After `WORKER PENDING` the API and pages run `<SHA>` while the worker runs the
   previous commit, possibly for hours. Do not ship a change that alters a request / response contract between a page and a
   worker route in a way the old worker cannot serve without also waiting for the worker.
8. **State lost at each API switch** (rate-limit counters, the tab heartbeats for ~1 minute) - same as every restart before.
9. **`docker compose` defaults**: a manual command that interpolates the compose file without `state.env` falls back to
   `deploy-api:latest` for all three services; use the scripts (the API/worker services are behind the `stack` profile so a bare
   `up -d` cannot restart them).

## 7. Tests (this branch)

`tests/test_deploy_stage2_roles.py` (path table vs nginx vs the real routes, compose fences and budget),
`tests/test_deploy_stage2_worker.py` (admission guard, drain, state / drain routes, the internal key, live merge, background
work), `tests/test_deploy_stage2_state.py` (cross-process lock, state epoch), `tests/test_deploy_stage2_plan.py`
(`plan_deploy.py`), `tests/test_deploy_stage2_bluegreen.py` (`bluegreen.sh`, `deploy_module.sh` worker part and the cutover
guards against a fake `docker`). Also touched: `tests/test_mcp_plain_http.py` (the nginx upstream name; the old assertion looked for a
location that no longer existed).
