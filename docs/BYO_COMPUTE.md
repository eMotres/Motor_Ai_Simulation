# Bring your own compute: user-owned compute nodes

Status: design + Stage 1 implementation (2026-09-29).
Code: `src/motor_ai_sim/compute_nodes.py`, `src/motor_ai_sim/routes/compute_nodes.py`,
`deploy/compute-worker/`, `web/src/components/auth/ComputeNodesDialog.tsx`,
tests in `tests/test_compute_nodes.py`.

## 1. Why

Solver time is the platform's main running cost, and heavy users (optimizer
campaigns, duty sweeps, 3-D passports) want more of it than a fair-use share.
Instead of the platform buying hardware for everybody, a user can rent their own
server (any Linux VM or dedicated box) and attach it to their account. Their
jobs then run on their machine, with the same solver code, and the results land
in their workspace as if the platform had computed them. The platform keeps
the UI, storage, scheduling and verification; the user pays their provider for
the CPU.

## 2. Model in one picture

```
 user's server (behind NAT is fine)             platform (api)
 ┌──────────────────────────────┐   HTTPS out    ┌───────────────────────────┐
 │ compute worker               │ ─────────────▶ │ /api/nodes/heartbeat      │
 │  - heartbeat + host sample   │                │ /api/nodes/jobs/lease     │
 │  - lease a job (owner only)  │ ◀───────────── │   signed bundle           │
 │  - verify signature/version  │                │ /api/nodes/jobs/{id}/     │
 │  - run SAME solver code      │ ─────────────▶ │   progress (renew, Stop)  │
 │  - post result + provenance  │                │   complete / fail         │
 └──────────────────────────────┘                │ → owner's workspace       │
                                                 │ → usage (own_node = 1)    │
                                                 └───────────────────────────┘
```

Pull model: the worker always initiates. No inbound port, no VPN, no SSH access
for the platform to the user's machine, and the platform never executes anything
the node sends.

## 3. Identity and tokens

* A node is created by a signed-in account on **My compute nodes** (account
  menu). The server returns a token `mcnode_<32 random bytes>` **once**; only its
  sha256 is stored (`<cluster dir>/user_nodes.json`, mode 0600).
* A token belongs to exactly one owner. Every worker route resolves
  token → (node, owner) and filters by owner. A node asking about a run of
  another owner gets **404** (not 403), so it cannot even learn the run exists.
* Tokens are revocable from the same page (or by an admin). Revocation puts any
  job leased to that node back in the queue at once.
* The admin metrics tokens of PR #34 (`mnode_...`) are a different credential
  and are rejected on every lease route; a user token is rejected on the admin
  ingest.
* Organisation sharing (Stage 3): a node may be marked "org"; it then leases
  jobs of members of the owner's organisation. The owner check becomes
  "owner or same org and node shared". Until then: owner only.

## 4. Job lifecycle

States: `queued → leased → done | failed | cancelled`.

1. **Submit.** A job of a kind with a registered handler (`jobs.HANDLERS`) goes
   through `jobs.run_job`. Its routing (section 5) decides platform or node. A
   node job is stored in the remote job store with the owner, the owner's
   workspace id and root (resolved from the owner's identity at submit time,
   never from anything a node says), the kind, the JSON body and the platform
   version. The caller receives `202 {run_id, poll}` exactly like the async
   queue mode.
2. **Lease.** `POST /api/nodes/jobs/lease {version}`. Refused with **409** when
   the worker's version differs from the platform's. Otherwise the best queued
   job of the node's owner (priority, then arrival) is leased for `LEASE_S`
   (120 s) and returned as a **bundle** `{run_id, kind, body, platform_version,
   lease_s, attempt}` plus its signature.
3. **Run.** The worker verifies the signature and the version, then runs
   `jobs.HANDLERS[kind](body)` in-process (Stage 1) or in the pinned container
   (Stage 2). The bundle holds only this owner's machine description, config and
   duty inputs; no other account's data is ever in it.
4. **Progress.** Every ~10 s `POST .../progress {frac, phase, eta_s, cpu_s}`.
   This renews the lease and its answer carries `cancel: true|false`, which is
   how Stop reaches a machine with no inbound port.
5. **Finish.** `POST .../complete {result, provenance, signature}` or
   `POST .../fail {error, cpu_s}`. The platform validates, files the result in
   the owner's workspace (`<ws>/remote_results/<run_id>.json`), records usage
   and writes an audit line.
6. **Lease expiry.** A lease not renewed in `LEASE_S` is reaped: the job goes
   back to `queued` (another node of the same owner may take it). After
   `MAX_ATTEMPTS` (3) lost leases it is `failed` with the reason.
7. **Cancel.** `POST /api/jobs/{run_id}/cancel` (the same Stop every route
   already uses; owner-checked, admin allowed). Queued → cancelled at once.
   Leased → flagged; the worker sees the flag on its next progress call, sets the
   local cancel flag every solver loop already polls (`jobs.check_cancelled`),
   and reports `fail {error: "cancelled"}`, which is recorded as cancelled.

## 5. Scheduling preferences

Per user (and overridable per job):

| preference | behaviour |
|---|---|
| `platform_only` (default) | never uses the user's nodes |
| `own_first` | a node job if one of the user's nodes is online with the right version, else the platform (fair use applies) |
| `own_only` | always a node job; it waits in the queue until one of the user's nodes leases it |

Stage 2 adds a queue-time fallback for `own_first` (a node job not leased within
N minutes moves to the platform) and per-job overrides from the UI.

## 6. Same code, pinned version

* The platform version is the repo-root `VERSION` (what `/api/version` reports).
* Every bundle carries it; the lease is refused and the worker refuses the
  bundle when versions differ. A result must also report the same version.
* Stage 1 installs the code at tag `v<VERSION>` into a venv
  (`install.sh`). Stage 2 publishes `ghcr.io/emotres/motres-worker:<VERSION>`
  per release (`deploy/compute-worker/Dockerfile`), and provenance carries the
  image digest. The job runs in the container with no network except the
  platform URL, a read-only root filesystem and a scratch volume.
* On a platform upgrade, online workers see `version_ok: false` in the
  heartbeat answer, stop receiving leases, and the page marks the node red until
  the owner re-runs the install command (or Watchtower pulls the new tag).

## 7. Security

* **Scope.** Token → one owner; every query is filtered by owner; foreign run
  ids are 404.
* **Signed jobs.** Bundle signature = HMAC-SHA256 over canonical JSON, keyed by
  sha256(token), which only the platform (stored hash) and the node (holds the
  token) know. A tampered bundle is refused by the worker. The result is signed
  the same way and verified before it is filed. Stage 2: platform Ed25519 key
  for bundles (the worker pins the public key), so even a leaked token hash
  cannot forge jobs.
* **No code from nodes.** The platform only parses JSON from a node: result
  schema check (object, `provenance.cpu_s`/`wall_s` non-negative numbers,
  string fields truncated), size cap 8 MB, string redaction on progress text.
  Nothing a node returns is imported, unpickled or executed. Paths are never
  taken from a node; the result file location comes from the owner's
  workspace resolved at submit time.
* **No other users' data.** A bundle is the owner's own inputs. Shared catalog
  items a job references are embedded as values, not as paths into the shared
  store.
* **The user's server is the user's.** A worker runs as an unprivileged system
  user under systemd hardening (`NoNewPrivileges`, `ProtectSystem=strict`,
  `ProtectHome`, private tmp); the token file is 0600.
* **Audit log.** `<cluster dir>/node_audit.jsonl`: node create/revoke,
  preference changes, submit, lease, refused lease (version), requeue, bad
  signature, complete (with CPU-seconds), fail, cancel. Admin view:
  `GET /api/admin/user-nodes/audit`.
* **Abuse.** A user node only ever computes its owner's jobs, so a malicious
  node can only corrupt its owner's own results. Results from nodes are marked
  with their provenance in the job table and in the result file; reports can
  show "computed on user node X" and the platform can re-verify a sample of them
  on its own hardware (Stage 3, spot checks).

## 8. Accounting

`job_usage` gets a column `own_node`. A node job is recorded with
`client = "node"`, `node = "u:<node id>"`, the CPU-seconds and wall time reported
by the worker, and `own_node = 1`. `cpu_hours(user, ..., fair_use=True)` (the
fair-use question) leaves those rows out; `fair_use=False` counts everything for
the owner's own statistics. The admin cluster summary (platform capacity) also
leaves them out.

## 9. Install

On **My compute nodes**: Add node → the token and two commands appear once.

Script (Debian/Ubuntu, systemd):

```
curl -fsSL https://<platform>/api/nodes/worker/install.sh | sudo bash -s -- \
  --url https://<platform> --token mcnode_...
```

It reads the platform version, clones the code at `v<version>` into
`/opt/motres-worker`, creates the `motres-worker` user, writes
`/etc/motres-compute-worker.env` (0600) and starts
`motres-compute-worker.service`.

Docker (Stage 2 image):

```
docker run -d --name motres-worker --restart unless-stopped \
  -e MOTRES_URL=https://<platform> -e MOTRES_NODE_TOKEN=mcnode_... \
  ghcr.io/emotres/motres-worker:<version>
```

Smoke test: `MOTRES_URL=... MOTRES_NODE_TOKEN=... python motres_compute_worker.py --once`
leases and runs at most one job (the `selftest.cpu` kind proves the path in a
second).

## 10. Requirements (guidance per job kind)

| job kind | cores | RAM | notes |
|---|---|---|---|
| selftest / static field views | 1 | 1 GB | seconds |
| 2-D transient (one operating point) | 4 | 4 GB | minutes |
| coupled EM-thermal duty | 4-8 | 8 GB | 10-60 min |
| optimizer / sweep campaign | 8-16 | 16-32 GB | hours; one eval per 4 cores |
| 3-D static passport | 16+ | 64 GB+ | hours; night runs |

x86-64 Linux, Python 3.10+ (script install) or Docker; outbound HTTPS to the
platform; 20 GB disk. One job at a time per worker in Stage 1; run several
workers (one token each) on a big box, or wait for Stage 2 slots.

## 11. How it fits the portal architecture

The worker protocol is deliberately the `RedisQueue` contract of `jobs.py`
(record = JSON, handler lookup by `kind`, progress + cancel keys, liveness,
lease re-queue) carried over HTTPS instead of Redis. The same pull protocol
serves three kinds of nodes:

* **platform nodes** (the Hetzner pool) lease everybody's platform jobs;
* **user nodes** lease only their owner's jobs (this document);
* **vendor nodes**: a vendor module (a proprietary loss model, a drive
  simulator) can run on the vendor's own node. The platform sends it only the
  inputs that module needs, gets back the module's outputs, and never holds the
  vendor's code. Scoping is by module id instead of owner; everything else
  (tokens, signatures, versions, audit) is the same.

## 12. Stages

* **Stage 1 (this PR).** Node registry per owner, token shown once, install
  command, node list with status/cores/CPU/RAM (the #34 sample pipeline keyed
  `u:<id>`), routing preference per user, worker script (in-process runner),
  lease/progress/complete/fail/cancel routes, HMAC-signed bundles and results,
  version pinning, result filing in the owner's workspace, usage with
  `own_node`, job table `where`, admin view of all nodes/jobs/audit.
* **Stage 2.** Published worker image + digest provenance, container
  isolation per job, Ed25519 platform signing key, several concurrent slots per
  node, `own_first` queue-time fallback, progress streaming into the normal
  progress registry (so the Simulation strip shows node jobs live), large-result
  upload in chunks, handlers registered for the real job kinds (coupled duty,
  optimizer eval, static3d).
* **Stage 3.** Organisation-shared nodes, spot-check re-verification on
  platform hardware, vendor nodes, a pricing plan that credits own-node use.

## 13. API summary

Account (signed in): `GET /api/nodes`, `POST /api/nodes {name}`,
`POST /api/nodes/{id}/revoke`, `DELETE /api/nodes/{id}`,
`GET /api/nodes/{id}/history`, `PUT /api/nodes/prefs {pref}`,
`GET /api/nodes/jobs/mine`, `POST /api/nodes/jobs/submit {kind, body, pref}`.

Worker (`Authorization: Bearer mcnode_...`): `POST /api/nodes/heartbeat
{sample, version}`, `POST /api/nodes/jobs/lease {version}`,
`POST /api/nodes/jobs/{id}/progress`, `.../complete`, `.../fail`;
public installer files `GET /api/nodes/worker/install.sh`,
`GET /api/nodes/worker/motres_compute_worker.py`.

Admin: `GET /api/admin/user-nodes`, `GET /api/admin/user-nodes/jobs`,
`GET /api/admin/user-nodes/audit`.

Jobs table: `GET /api/jobs` now includes node jobs, and every row has
`where` = `platform` or `node:<id>`; `POST /api/jobs/{id}/cancel` stops node
jobs too.
