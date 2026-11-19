# Optimisation on the US server at 80 % (owner 2026-11-16)

## 1. Does an optimisation started by an EU account run on US?

No - it runs on EU only. `regions/foreign.py` ships only job kinds that have a pure `body -> result` handler in
`jobs.HANDLERS` AND an entry in `foreign.INPUT_KEYS`; today that is the single kind `selftest.cpu`. The optimiser kinds
are route closures (`routes/optimization._optimizer_job`) that call `refine_proc.run_one` in local eval subprocesses, so
`jobs.run_job -> foreign.route` returns False for them and they run at home.

Smallest safe way to use the US worker (NOT built, needs the owner's yes):
- a handler `fem.eval_batch`: input = one wave of candidates (overrides + the machine's geometry / winding / materials /
  operating point, engineering numbers only, no names), output = the metric rows; registered in `INPUT_KEYS` with exactly
  those keys, so `compute.job_payload` strips identifiers as for every foreign job;
- the campaign (home) sends a WAVE per foreign job, not single evals: the peer loop ticks every 20 s, takes one job at a
  time and hands back with a 15 s first delay, so one-eval jobs would be slower than computing them;
- the peer runs the wave with its own eval pool (the 24-thread worker below); the result goes home, the home campaign
  files it exactly like a local eval row; the peer deletes its copy on the confirmed hand-back (<= 24 h otherwise).
- results must be bit-identical: the same `run_one` code and image on both servers, a golden check of one wave computed
  on both.
Build time: about 3-4 working days with the golden check (handler + wave split + tests + a two-server trial).

## 2. Fences per server (this PR, opt-in)

`deploy/docker-compose.yml` reads `WORKER_CPUSET`, `WORKER_CPUS`, `WORKER_MEM`, `API_CPUSET`, `API_CPUS`, `API_MEM`,
`WEB_CPUSET`, `WEB_CPUS`, with today's EU numbers as defaults (EU: no change, byte-identical resolved config).

US (9950X: cpu N and N+16 are SMT siblings; checked on us1) - lines for the US `deploy/.env`:

```
WORKER_CPUSET=0-11,16-27
WORKER_CPUS=24
WORKER_MEM=40g
FEM_SCAN_SMT=1
FEM_SCAN_WORKERS_CAP=22
FEM_SOLO_SEED_THREADS_CAP=8
```
API (12-15), web (12-13) and the host keep `12-15,28-31` free: 8 threads. 24 of 32 threads = 75 %, 25 with the
API's quota in use = 78 %.

Sharing with the agent jobs (agentjobs.slice, now 25 cpus / 72 GB, not pinned): pin the slice to the SAME 24 threads
(`AllowedCPUs=0-11,16-27`), CPUQuota 2400 %, MemoryMax 32 GB. The optimiser and the agent jobs then share one
24-thread, 72 GB (40 + 32) envelope, the sum can never pass 75 % of the CPU or 79 % of the 91 GB, and the scheduler
weights decide under contention (worker `cpu_shares` 512).

## 3. Eval pool

`FEM_SCAN_WORKERS_CAP` (default 8), `FEM_SOLO_SEED_THREADS_CAP` (default 8) and `FEM_SCAN_SMT` (default off) replace the
two hard-coded 8s and the physical-core count; the rule is unchanged: workers = min(cap, max(2, units - 2)), units =
physical cores in the cpuset (default) or hardware threads with `FEM_SCAN_SMT=1`. EU sets none of them: 8 / 8 / physical,
as today. US with the lines above: 24 threads - 2 = 22 workers, shared between concurrent optimiser jobs by the existing
budget (QUEUE_WORKERS=2: 11 + 11), no per-job override needed. Evals are one BLAS thread each. The measured ceiling on
dual-channel boxes was ~8 solves (a 12-worker pool was 12 % slower than 8 on the workstation), and the 9950X is
dual-channel too, so measure one wave at 12 / 16 / 22 on US before the owner relies on 22; if 22 is not faster, lower
`FEM_SCAN_WORKERS_CAP` in the US .env - no code change.

Memory: ~310 MB per child on a normal eval, up to ~1.7 GB per solve on a new-mesh coupled solve (docs/
SOLVE_POOL_2026-09-29.md): 22 evals are 7 GB typical, 37 GB worst case - inside the 40 GB worker ceiling of the 91 GB box.
No result changes: the candidates and the numbers are the same, only more run at once.
