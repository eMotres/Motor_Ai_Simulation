# Optimizer items 110352a / 0f973bb, D, E and server concurrency (2026-09-24)

Claude Opus 5.5 (`claude-opus-5-5`) did this as a sub-agent, with no escalation.
The owner decided that the orchestrator's agents finish Codex's held commits
(`coordination/codex-review-2026-09-24.md`, items 5 and 6) together with
speed items D and E from `docs/OPTIMIZER_SPEED_REVIEW_2026-09-24.md`.

- Branch: `perf/optimizer-abc-freeze`, rebased onto `pre-migration-freeze-2026-09-15`
  at `32a33ad`. That commit already has the solver agent's held-item fixes
  8a9e43d, 19b3b35, 503c5b3, 1883ba7 and 94b843d.
- Files changed: `routes/optimization.py`, `optimization/refine_proc.py`, two small
  separate functions in `simulation/fem_solver_2d.py` (the E hook), four web
  files, tests and this document.

## What a final re-solve asks for after 1883ba7

1883ba7 made six raw samples per cogging cycle an opt-in purpose,
`sampling_purpose="cogging_quality"`. The `standard` and `optimization` purposes
now keep the requested steps: 48 frames on m12 and 40 on m24, the same frames
the candidates are ranked on.

A winner validation or Apply check at `standard` would therefore repeat the
candidate solve. It would certify no better angular sampling than the search
already had, and the final-quality ripple is the only thing 110352a adds.

**Decision:** every final re-solve now requests `cogging_quality` (constant
`_FINAL_PURPOSE`). That covers the winner shortlist in all four workers, the
Sweep Apply check and the new on-demand re-check.

`_standard_quality` still certifies on the flag, not the label. It requires
`cogging_sampling_final_quality_sufficient` on a result whose purpose is
`cogging_quality`, or `standard` when that run already carries the flag
(`_FINAL_OK_PURPOSES`).

`refine_proc`, `_subprocess_eval` and `_eval_cache_key` accept the third value.
Only an `optimization` eval can be an A+B candidate. The web Apply check
accepts either final purpose.

## Item 5 (110352a): winner re-check

- **(a) Drop-out.** A finalist whose final solve fails now drops out instead of
  failing the run.
  - It is listed in `final_validation.failed` with its overrides, current and
    reason, and counted in `dropped_count`.
  - The run fails closed only if baseline A or B fails, or if no finalist passes.
  - The Descent card shows "N finalist(s) failed 6× and dropped out", with the
    reasons in the tooltip.
- **(b) D: parallel final re-solves.** Baseline A is solved alone first. It is
  the one solve that may start cold, and it publishes a final-resolution warm
  state.
  - Baseline B and every finalist then run together in one wave of up to
    `_job_workers()` threads.
  - They are the same solves with the same inputs. The difference is the seed:
    each continues A's state instead of a chain B → f1 → f2.
  - Results are gathered by index, so the winner does not depend on completion
    order.
  - `final_validation.timings` records `baseline_a_s`, `parallel_wave_s` and
    the worker count.
- **(c) On-demand re-check.** `POST /api/optimization/descent/validate_point`
  takes `{run_id, target: "best"|"point", overrides, current_a}`.
  - It works on the stored optimizer run in the workspace state:
    - a run stored before 110352a
    - a run whose final validation failed (its `screening_best` is used)
    - any picked cloud point outside the validated shortlist
  - The server looks the point up in the stored run. A geometry that is not a
    stored point returns 422.
  - It re-solves the point at `cogging_quality` with the run's pinned
    `eval_params`, operating point, MTPA γ and winding.
  - It returns the final-quality numbers as the apply-eligible point, and keeps
    the last 50 re-checks in `rechecks`.
  - New runs stamp `machine_fp`, the config fingerprint with the run's own
    variables excluded. A machine change returns 409.
  - Older runs have no stamp. They are re-checked on the machine as it is now,
    which is the machine Apply writes into, and the answer carries
    `provenance: "legacy_unpinned"`.
  - UI: the Descent and Auto cards replace the disabled Apply with
    "Verify at 6× & apply". The store's `verifyAndApplyDescentPoint` applies
    only the verified point. `applyDescentPoint` accepts a point with
    `apply_eligible` from the server even when the run is not certified.
- **Picard stamp (test_auto_optimization).** `refine_proc` now checks the four
  scored fields before the `picard_converged` stamp. A thin payload is reported
  by the field it lacks. A scored payload without the stamp is still rejected,
  and a new test covers that.

## Item 6 (0f973bb): Sweep Apply

- **Purpose test.** The call is now
  `_subprocess_eval(sampling_purpose="cogging_quality", **args)`.
  - `test_every_optimizer_eval_and_cache_call_explicitly_names_purpose` now
    allows the literal final purpose only inside `scan_validate_point` and
    `descent_validate_point`.
  - The workers pass it through their `purpose` argument.
- **409 on a second point.** The check no longer compares the full fingerprint
  (`scan_params.cfg_fp`). Applying point 1 writes its swept values into the
  config, which changes the full fingerprint although only the swept keys
  moved.
  - The identity test is now the sweep's machine stamp (swept keys excluded)
    and the point's own `source_cfg_fp` (its override keys excluded).
  - A test applies point 1, changes the full fingerprint, then verifies point 2.
- **Legacy sweeps.** Sweeps without `validation_provenance_version`, such as the
  owner's 22 Sep sweep (production 68de0ca), are re-checked on demand instead of
  returning 422.
  - They need the 16 solver knobs every sweep since 2026-09 stores, a single
    feasible point and a matching machine stamp.
  - The answer says `provenance: "legacy_machine_stamp"` and the panel says
    "older sweep, re-checked on this machine".
  - A point stamped since 0f973bb still needs its `source_cfg_fp`.
- **Test window.** `test_applied_design_autosave`'s 8000-character window already
  failed on 0f973bb, because the archive call sat at 8775 characters. It is now
  10000.

## Item 4: one CPU budget for concurrent optimizer jobs

The server config was checked read-only. The AX42 runs `deploy-api-1` with
cpuset 0-11, quota 12 CPUs and `QUEUE_WORKERS=2`, and `FEM_SCAN_WORKERS` is not
set. So one optimizer job gets 6 workers under the A+B+C rule, and two gave 12
children on 8 cores.

Now:

```
budget = min(8, max(2, physical_cores_available - 2))   # the fix-C rule
share  = max(1, budget // active_optimizer_jobs)
```

- Enforcement is per eval. Each eval subprocess of a registered job (sweep,
  descent, CMA, auto, screen) takes a slot before its clock starts and returns
  it in a `finally`.
- A job holds at most `share` slots, and the share is re-evaluated at every eval
  start:
  - When a second job starts, the first drains to its share as its running evals
    finish. Nothing is killed.
  - When the second job ends, the first grows back.
- Pool sizes, wave arithmetic and the `/scan` "workers" field use the share the
  job sees at its start.
- Evals outside a job are single solves and are not throttled, like an
  interactive Simulation run. These are the Sweep Apply check, the baseline-line
  button and the on-demand re-check.
- `FEM_SCAN_WORKERS` still overrides. When set, it is every job's own worker
  count and no sharing applies.
- **AX42:** 1 job gets 6, 2 jobs get 3 + 3. **Workstation:** 1 job gets 8,
  2 jobs get 4 + 4.

## E: the final re-solve continues the optimization run's warm state

**Problem.** The eddy warm seed is keyed on the solved steps per period, a hard
refusal in `_warm_seed_accept`. So the first final-quality solve after a search
found only a 48- or 40-step state and started cold. That is the winner
validation's A, every Sweep Apply and every re-check.

**Solver hook.** Two separate functions in `fem_solver_2d.py`, active only under
`SB_SEED_ACROSS_STEPS=1`:

- `routes/optimization` sets the variable for `cogging_quality` evals when
  `OPT_FINAL_WARM_START` is on. The Simulation tab never sets it.
- `_seed_across_steps_ok` accepts a cached state whose meta differs only in
  `nspp`. Every other term is still a hard refusal: periods, winding, coil
  temperature, magnet scale, sectors and speed.
- The same-angle reference is withheld automatically, because its sample count
  is the parent's.
- `_br_withheld_across_steps` refuses to continue the Br ratchet map of such a
  state. The final run pays its own demag pre-pass on a fresh magnet, exactly as
  a cold run does.

**Why the Br map is withheld (first A/B, pre-merge code, m12, 72-frame final).**
Continuing the optimization lineage's Br map ("E full") saved the most time, but
it changed the answer:

- T 0.216 → 0.214 N·m (−0.9 %) on 3 of 3 geometries
- ripple 3.87 → 3.93 %, 3.80 → 4.19 % and 4.23 → 4.12 %
- P_cu −0.1 W
- P_mag −0.1 W

The optimization Br map is the ratchet accumulated over many candidates at
other currents, not the fresh magnet plus one pre-pass that a cold final run
solves. The eddy-history-only variant, below, was identical there.

### A/B on the merged code

- **Harness:** `e_ab.py` in the scratchpad `de_work` folder.
- **Solve:** the real `refine_proc` child with `sampling_purpose="cogging_quality"`
  and `_base_eval_env`: 1 BLAS thread and `SB_SEED_FROM_PREVIOUS=1`.
- **Sandbox:** each run gets a private copy of the review sandbox, whose warm
  state is an optimization state (48 steps on m12, 40 on m24).
- **Code:** a snapshot of the rebased tree. The module path was asserted.
- **Comparison:** cold (today) against warm (`SB_SEED_ACROSS_STEPS=1`).
- **Geometries:** baseline, magnet_fill_up +0.03, and tooth_width +0.1 (m12) or
  +0.2 (m24).
- **Values:** as refine_proc reports them (T to 3 decimals, η to 5, ripple to 2,
  losses to 0.1 W).

| machine | geometry | T [N·m] | η | ripple [%] | P_fe [W] | P_cu [W] | P_mag [W] | P_shaft [W] | eddy settle residual cold → warm |
|---|---|---|---|---|---|---|---|---|---|
| m12 | baseline | 0.219 = | 0.90068 = | 3.80 = | 4.1 = | 32.5 = | 1.1 = | 0.3 = | 2.48 % (capped) → 0.46 % |
| m12 | magnet_fill_up +0.03 | 0.218 = | 0.89984 = | 3.73 = | 4.2 = | 32.6 = | 1.1 = | 0.3 = | 2.47 % (capped) → 0.48 % |
| m12 | tooth_width +0.1 | 0.220 = | 0.90129 = | 4.17 = | 4.1 = | 32.4 = | 1.1 = | 0.3 = | 3.11 % (capped) → 0.48 % |
| m24 | baseline | 5.331 = | 0.73756 = | 5.17 = | 2.5 = | 195.2 = | 0.4 = | 0.5 = | 31.4 % (capped) → 0.70 % |
| m24 | magnet_fill_up +0.03 | 5.431 = | 0.74093 = | 5.76 = | 2.5 = | 195.5 = | 0.5 = | 0.4 = | 19.6 % (capped) → 0.72 % |
| m24 | tooth_width +0.2 | 5.370 = | 0.73904 = | **9.37 → 9.38** | 2.5 = | 195.2 = | 0.4 = | 0.5 = | 22.9 % (capped) → 0.76 % |

`=` means cold and warm are identical to the reported precision. In all, 41 of
42 values are identical. The exception is one ripple, 9.37 → 9.38 %, a change in
the last digit.

Frames solved before the reported window:

| machine | cold | warm |
|---|---|---|
| m12 | 75 eddy warm-up + 72 demag pre-pass | 3 + 72 |
| m24 | 123 eddy warm-up + 120 demag pre-pass | 3 + 120 |

Wall time, solo (nothing else running, CPU 5–7 % before each start), baseline
geometry:

| machine | cold | warm (E) | saved |
|---|---:|---:|---:|
| m12 | 144.6 s | 125.1 s | −19.5 s (−13 %) |
| m24 | 578.0 s | 510.4 s | −67.6 s (−12 %) |

The review targeted about 65 s and 120 s. Those targets assumed the Br map is
continued too, which is the variant rejected above. Withholding it keeps the
answer and keeps the whole demag pre-pass, so about 12 % remains.

**Verdict:** E is not enabled by default (`_FINAL_WARM_START_DEFAULT = False`).
One of 42 values differs in its last digit, and the brief's rule is "identical,
or behind a flag". Turn it on with `OPT_FINAL_WARM_START=1`.

**Finding the owner has to weigh.** At these settings a cold final-quality solve
never settles its eddy transient:

- m12: residual 2.5–3.1 % against a 2 % tolerance, capped after two periods.
- m24: residual 20–31 %, capped.
- Each warm run settled at the first probe.

The Sweep Apply check and the new re-check both refuse an unsettled coupled-eddy
result (`eddy_settled` must be true, a 0f973bb rule). With E off:

- every Sweep Apply and every picked-point re-check on these two machines
  starts cold and returns **422 "not settled"**;
- the winner validation does not check `eddy_settled` and certifies the capped
  cold baseline A.

With E on, both run on a settled field. The small 1-of-42 difference comes from
the cold side's unsettled transient.

## D and E end to end (m12, real route code, solver-direct)

**Harness:** `e2e.py` in the scratchpad `de_work` folder.

- `auto_start(...)` (POST `/auto`) is called as a function in a separate
  process. The sandbox config is a fresh copy of the m12 review sandbox: the
  owner's Ø30 12s/14p optimisation config (15000 rpm, 32 A, γ 6°, 48 steps, P2,
  1 mm, 2 sectors, eddy + demag) with an optimization warm state.
- Only the plan is shrunk: two variables (magnet_fill_up, tooth_width),
  population 4, one generation.
- The flow is: baseline A and B, one wave of 4 candidates, then final
  validation of A, B and 3 finalists.
- Evals are the real `refine_proc` children. The live API on 8001 was not
  touched. The machine was otherwise idle.

| run | code | baseline A+B | 4-candidate wave | final validation | total | winner |
|---|---|---:|---:|---:|---:|---|
| before | branch base 38f3469 (final = 8997bb3 "standard" 72 frames, ψ_PM probe 96 frames, sequential) | 71.6 s | 48.1 s | **561.6 s** | 682.5 s | tooth_width 2.3, magnet_fill_up 0.4123 |
| after, D off (FEM_SCAN_WORKERS=1) | rebased branch, sequential | 72.6 s | 125.5 s (1 worker) | **390.8 s** (A 174.6 + 216.2) | 589.5 s | same |
| after, D (E off, default) | rebased branch | 68.8 s | 47.4 s | **265.8 s** (A 171.2 + wave 94.5) | 383.3 s | same |
| after, D + E (OPT_FINAL_WARM_START=1) | rebased branch | 69.0 s | 45.7 s | **242.9 s** (A 150.0 + wave 92.9) | 358.3 s | same |

How the final-phase saving divides up:

- **561.6 → 390.8 s** is the solver line under the rebase. Mainly, 1883ba7 no
  longer runs the ψ_PM probe of a final solve at 96 frames. It also includes
  the other held-item fixes. This part is not from this change.
- **390.8 → 265.8 s (−32 %)** is **D**. The four re-solves after A take one
  94.5 s wave instead of 216.2 s in sequence. The review predicted 404 → 254 s.
- **265.8 → 242.9 s** is **E**: baseline A takes 150 s instead of 171 s.

Winner metrics:

- The D and D+E runs certify the same winner with identical final numbers:
  T 0.219 N·m, η 0.90084, ripple 3.79 %, 5.0472 N·m/kg.
- The sequential run's finalists continue each other's state
  (B → f1 → f2 → f3) instead of A's. It reports η 0.90079, ripple 3.84 % and
  5.0442 N·m/kg for the same geometry.
- That seed-lineage spread was already in the sequential design. D removes the
  order dependence, because every finalist continues baseline A.
- Values differ from "before" (T 0.216) because of the solver agent's torque
  selector and rotor window, not because of this change.

## Owner decisions

1. **Turn E on?** One ripple value differs in its last digit, 9.37 → 9.38 %, so
   it is behind the flag. With it off:
   - Sweep Apply and picked-point re-checks on m12 and m24 return 422, because
     the cold final solve is capped unsettled.
   - The winner validation certifies an unsettled baseline A.
   - One line enables it: `OPT_FINAL_WARM_START=1` in the api env, or
     `_FINAL_WARM_START_DEFAULT = True`.
2. **Should `_standard_quality` also require `eddy_settled`?** The Apply and
   re-check paths already do. The winner validation does not. This matters only
   with E off, because the cold A is then unsettled.
3. **Legacy optimizer runs** are re-checked on the machine as it is now, since
   nothing identifies their machine. Alternatively, refuse them.

## Reproduction

Everything is in the orchestrator scratchpad `de_work/`:

- Harnesses: `e_ab.py` and `e2e.py`.
- Raw results:
  - `e_acc_cq_{m12,m24}.json` (merged A/B)
  - `e_solo_{m12,m24}.json` (solo timings)
  - `e_acc_m12.json` and `e_warmacc_e2_m12.json` (pre-merge E-full and E, m12)
  - `e2e_{before,after_E0,after_E1,after_seq_E0}.json`
- Code snapshots: `src_merged` is the rebased tree and `base_tree` is 38f3469.

## Tests

- **Targeted set on the rebased branch: 288 passed.** The files are
  `test_optimizer_final_validation`, `test_scan_standard_validation`,
  `test_sampling_purpose_flow`, `test_screening_descent`, `test_optimizer_honesty`,
  `test_auto_optimization`, `test_optimizer_speedup_abc`, `test_pardiso_runtime`,
  the new `test_optimizer_items_de` and `test_applied_design_autosave`. The
  solver agent's `test_cogging_frame_policy` also passes.
- **New `tests/test_optimizer_items_de.py`** covers:
  - the budget split and the override
  - the live-share limiter, including drain and regrow
  - slot release
  - the E environment only on final-purpose evals
  - the solver hook relaxing only `nspp`
  - final quality being the 6-sample flag
  - every worker validating at `_FINAL_PURPOSE`
  - D ordering and concurrency, with A alone and B plus 3 finalists overlapping
  - the re-check: best, picked, forged, MTPA γ, pinned or foreign machine,
    refusals, legacy runs
  - the picard stamp
- **Web:** `npx tsc -p tsconfig.app.json --noEmit` shows no errors in the touched
  files. `motorStore.ts` lines 513 and 573 (TS2352) are pre-existing and not
  touched.
