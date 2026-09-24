# Optimizer speed-up A + B + C (2026-09-24)

Claude Opus 5.5 (`claude-opus-5-5`) implemented this as a sub-agent, with no escalation. The owner approved
A + B + C from `docs/OPTIMIZER_SPEED_REVIEW_2026-09-24.md` («Запускай A + B + C»). The goal was a real
optimizer speed-up while leaving everything the optimizer ranks unchanged.

| line | commit |
|---|---|
| `deploy/2026-09-24` (production: 68de0ca + 199e08c) | `272f034` |
| `pre-migration-freeze-2026-09-15` (port) | `890fe7a` |

## What changed

**A. No ψ_PM probe for optimizer candidates.**
- The Simulation summary ran a no-load ψ_PM solve for every new geometry. On 68de0ca it had 6 frames and
  cost 11–13 s. On the freeze line 8997bb3 raises it to 96/120 frames, about 70 s.
- Its result feeds only the chord Ld/Lq and the saturation-droop row, and `refine_proc` reads neither.
- A candidate now skips the probe. The summary says so: `psi_pm_probe_skipped: true`, with a `dq_note`.
- The skip applies only when the result is **not** stored as the live machine (ledger, last transient).
  A baseline eval with `{}` overrides therefore still computes it, so no stored Simulation card loses
  its Ld/Lq.
- The bench Ld/Lq probe was already skipped for candidates.

**B. Candidates reuse the run's baseline d-axis.**
- The baseline is the config machine the candidate overrides. Its calibrated d-axis comes from the
  memory or disk cache that the Simulation tab and earlier candidates fill, under the baseline's own key.
- **Physics.** θ* is the rotor angle where the no-load ψ_A peaks. The rotor pole is mirror-symmetric
  about the d-axis, and the stator slot and phase belt are mirror-symmetric about the phase axis. So
  ψ_A(θ) is even about θ*, and a dimension change that keeps both mirrors cannot move the peak.
  `cadquery_geometry` draws every magnet, pocket, tooth and slot feature as ±x about its own axis (for
  example `_create_magnets` p1…p6). There is no skew, pole-offset or asymmetric-pole parameter.
- **Guard.** `fem_solver_2d.daxis_reuse_blockers(base, candidate)` compares the two *merged* geometries.
  A differing key outside the whitelist `_DAXIS_SYMMETRIC_KEYS` makes that candidate calibrate its
  own axis. The whitelist has only the stator, rotor and magnet dimensions, the wire keys and the stack
  length. These keys force a candidate's own calibration:
  - num_poles, num_slots, num_seg and the *_per_segment counts
  - the angles and pitches derived from them
  - `magnet_lamination_tan`, which splits magnets in plane
  - **any key not on the list**, such as a future skew. The list is a whitelist on purpose.
- A pinned DAXIS stays pinned. A nested calibration solve never takes the candidate path.
- **Provenance** is in every candidate result:
  - `daxis_deg`
  - `daxis_source`: `baseline`, `calibrated` or `manual`
  - `daxis_policy`: `{daxis_calibration: baseline|own, daxis_reason}`
  - `optimizer_candidate`
  - `psi_pm_probe_skipped`
- **Scope.** A `ContextVar` (`fem_solver_2d.optimizer_candidate_scope`) is set by
  `refine_proc.run_one(optimizer_candidate=True)` around the one solve. It defaults to off, so Simulation,
  coupled, passport, report and validation solves are unchanged. A Simulation solve on another API
  thread never inherits it (tested).
- `_subprocess_eval` sends `optimizer_candidate=True` by default. Every caller on the deploy line is a
  sweep, descent, CMA, auto, screen or DOE candidate.
- On the freeze line a candidate must also have `sampling_purpose="optimization"`. A standard eval
  (winner final validation, Sweep Apply) keeps the probe and re-calibrates. This is checked in both
  `_subprocess_eval` and `run_one`. Codex's cogging-policy code is not modified. It no longer reaches a
  candidate's ψ_PM probe, because that probe is skipped.

**C. Default worker count** (`routes/optimization._scan_worker_count`):

```
workers = min(8, max(2, physical_cores_available − 2))      # FEM_SCAN_WORKERS overrides
```

- *physical_cores_available* counts physical cores inside the process's CPU affinity. In a container
  that is the cpuset, mapped to distinct cores through sysfs `core_id`. A cgroup CPU quota caps it.
  SMT siblings do not count: a candidate is a memory-bound single-thread FEM solve.
- *−2* keeps the earlier reserve for uvicorn, the descent daemon and the owner's interactive run.
- *≤ 8* is the measured ceiling on the Ryzen AI 9 HX 370 (12 cores / 24 threads, dual-channel LPDDR5x).
  Past about 8 concurrent solves, the shared L3 and the memory channels are the limit:
  - review: 8 workers 238 cand/h, 12 workers 208 cand/h
  - A+B: 575 cand/h against 560
  - 6 × 2 BLAS threads was worse than either
- **Workstation:** min(8, 12 − 2) = **8**. It was 10.
- **Hetzner AX42** (read-only check). The host is an AMD Ryzen 7 PRO 8700GE with 8 cores / 16 threads
  and 61 GB. `deploy-api-1` has cpuset 0-11 and quota 12 CPUs. sysfs maps CPUs n and n+8 to core n, so
  the cpuset covers all 8 cores. Result: min(8, 8 − 2) = **6**, the same as before.
  - 6 is a reasoned number, not a load test. The server has the same dual-channel memory limit, and
    two cores stay free for the API, the database and the owner's session there.
  - `QUEUE_WORKERS=2` in `/etc/motres/api.env` lets two queued jobs run at once. Two concurrent
    optimizations would then run 12 children on 8 cores. That is unchanged by this commit, but see
    the owner items below.

## Verification (deploy line, real `refine_proc` child)

**Harness** (scratch `abc_work/abc_driver.py`):
- "Before" is a worktree at 199e08c. "After" is the committed change.
- The module path was asserted per run.
- Each child gets the env of `_base_eval_env`: 1 BLAS thread, sandbox `MOTOR_AI_SIM_CONFIG`,
  `SB_SEED_FROM_PREVIOUS=1`.
- Every run starts from an identical copy of the review's sandbox config, which holds the warm seed and
  the baseline d-axis in its cache.
- Machines and duty settings are the review's:
  - **m12** is the owner's Ø30 12s/14p optimisation config: 48 steps, P2, 1 mm, 2 sectors, eddy and
    demag, 15000 rpm.
  - **m24** is CIANO28 85 L13, 24s/28p: 40 steps, 1.22 mm, 4 sectors, eddy and demag, 1000 rpm.
- Every candidate was seeded (`warm_seeded`, warm-up 3 frames).

**Load caveat.** Another agent's solver A/B ran on the workstation during most of these runs, using
13–30 % of the CPU before each start (82 % before one throughput wave). The before and after runs are
paired back to back, and the throughput pairs were repeated in reverse order.

### Per-candidate wall time (solo, paired, new geometry)

| machine | geometry | before | after | saved |
|---|---|---:|---:|---:|
| m12 | magnet_fill_up +0.03 | 56.0 s | 21.5 s | −34.5 s |
| m12 | tooth_width +0.1 | 63.5 s | 37.5 s | −26.0 s |
| m12 | tooth2_width −0.1 | 72.6 s | 42.0 s | −30.6 s |
| **m12 mean** | | **64.0 s** | **33.7 s** | **−47 %** |
| m24 | magnet_fill_up +0.03 | 103.6 s | 70.0 s | −33.6 s |
| m24 | tooth_width +0.2 | 130.6 s | 80.5 s | −50.1 s |
| m24 | tooth2_width −0.1 | 145.6 s | 103.6 s | −42.0 s |
| **m24 mean** | | **126.6 s** | **84.7 s** | **−33 %** |

The saving matches the review's phase split: d-axis calibration of 23–30 s plus the ψ_PM probe of
11–13 s. On the freeze line the ψ_PM part is about 70 s, so the saving there is larger.

### Throughput, one wave of 8 new-geometry candidates, shared workspace, 8 workers (the new default)

| machine | before cand/h (run 1, run 2) | after cand/h (run 1, run 2) | gain |
|---|---:|---:|---:|
| m12 | 154, 180 (mean 167) | 468, 426 (mean 447) | **2.7×** |
| m24 | 121, 110 (mean 116) | 186, 166 (mean 176) | **1.5×** |

For comparison, the review's clean-machine figure for 68de0ca at 8 workers on m12 was 238 cand/h, and
it emulated A+B at 575 cand/h. The m24 gain is smaller because its loaded solve is heavier: 8
concurrent solves stretch it more than m12.

### Accuracy: 12 geometries per machine, before vs after

Each geometry changes a different key (magnet_fill_up, magnet_fill_down, magnet_height, air_gap,
tooth_width, slot_height, magnet_up_gap, core_thickness, tooth2_width, rotor_house_height,
magnet_fill_radius, and one combination). All 12 ran concurrently, each from a private copy of the
same state.

| machine | T | η | ripple | P_fe | P_cu | P_mag (magnet eddy) | calibrated d-axis vs baseline |
|---|---|---|---|---|---|---|---|
| m12 | 12/12 identical | 12/12 identical | 11/12 identical; tooth_width +0.1: 4.22 → 4.23 % | 12/12 identical | 12/12 identical | 12/12 identical | 59.976–60.012° vs 60.006° (max 0.030° el) |
| m24 | 12/12 identical | 12/12 identical | 12/12 identical | 12/12 identical | 12/12 identical | 12/12 identical | 59.999–60.018° vs 60.013° (max 0.014° el) |

Values are identical to refine_proc's reported precision (T 3 decimals, η 5, ripple 2, losses 0.1 W).
The six solo pairs above are also identical in every metric. The d-axis each "before" candidate
calibrated (read from its private `.daxis_cache.json`) sits within ±0.03° el of the baseline on 11
different dimension keys. That is solver noise, and it confirms the symmetry argument beyond
magnet_fill_up.

Full tables: scratch `abc_work/acc_{m12,m24}_{before,after}.json`, `solo_*.json`, `tp_*_N8*.json`.

### Tests

- `tests/test_optimizer_speedup_abc.py` (38 tests, both lines) covers:
  - the candidate scope: default off, resets, thread isolation
  - `run_one` opens the scope only when asked, and on the freeze line never for `standard`
  - the guard: dimension keys pass; counts, segments, `magnet_lamination_tan` and unknown keys block;
    float noise is ignored
  - the solver's d-axis branch, stopped before the mesh: a standard solve calibrates its own; a
    candidate uses the baseline; a blocking key forces its own; a nested calibration is unaffected
  - the summary: a candidate never calls `noload_psi_pm`, a standard summary still does
  - the worker rule, including the AX42 sysfs/cpuset/quota case and the env override
- Deploy line: the new file plus `test_screening_descent`, `test_optimizer_honesty` and
  `test_auto_optimization`: 38 + 170 passed.
- Freeze line: the same files plus `test_sampling_purpose_flow`, 214 passed and 2 failed. Both
  failures are pre-existing Codex items that also fail on a clean dd54235:
  - `test_auto_optimization::…missing_the_scored_fields…` (the picard stamp fail-closed of 110352a)
  - `test_sampling_purpose_flow::…explicitly_names_purpose` (the `_subprocess_eval(**args)` of
    0f973bb, already in the review)

## Not changed / owner decisions

- The optimizer eval-cache key was **not** bumped. Cached evals stay valid because the metrics are
  identical to reported precision. A cached pre-change eval simply lacks the new `daxis_*` fields.
- **AX42.** Consider `FEM_SCAN_WORKERS` together with `QUEUE_WORKERS=2`. Two optimizations at once
  would run 2 × 6 children on 8 cores. Either accept that, or set `FEM_SCAN_WORKERS=4`, or let only
  one optimization run at a time.
- The freeze line still carries 8997bb3's inflation of the internal probes and of every standard
  solve (review item 1, second half). This change removes it from optimizer candidates only, and
  Codex's held logic was left alone.
