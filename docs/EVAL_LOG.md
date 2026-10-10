# Optimizer evaluation log (schema 2)

Two files per workspace record what the optimizer and the sweep evaluated. Both
are **append-only**, one JSON object per line, written with a single `O_APPEND`
write per row (whole lines under parallel workers; a torn last line from a
crash is not glued onto the next row). Code: `src/motor_ai_sim/optimization/eval_log.py`
(pure Python, no solver) wired into `routes/optimization.py`
(`_log_eval`, `_store_eval`, `_log_eval_failure_cache`).

| file | what it is | readers |
| --- | --- | --- |
| `.opt_dataset.jsonl` | the **log**: every evaluation outcome, ok or not | surrogate / warm start (ok rows only), dataset exporter (all rows) |
| `.scan_cache.jsonl` | the sweep **cache** `{"k","v"}`, plus provenance and failed-point log lines | the eval cache (only lines with a healthy `v`), dataset exporter |

Logging only: the search, the objective and the solver are unchanged.

## Row keys (`.opt_dataset.jsonl`)

Schema-1 keys are unchanged on an `ok` row (`overrides`, `current_a`,
`gamma_deg`, `ripple`, `torque`, `eff`, `td`, `mass`, `v_peak`, `p_loss`,
`thd_ll`, `kt`, `cfg_fp`). A row without `schema` is schema 1 and reads as
`status: ok`. New optional keys:

- `schema` (2), `ts` (UTC ISO, ms), `status` = `ok` | `infeasible` | `failed`
- `campaign_id` (one per optimisation or sweep run, e.g. `descent-20261011T013000Z-a1b2c3`,
  shared by every eval of the run including parallel pool workers), `campaign_kind`
  (`scan`, `descent`, `auto`, `refine`), `stage` (`free_search`, `seeded_refinement`,
  `final_resolve`, `sweep`; `final_resolve` is inferred from the sampling purpose)
- `objective` (name, e.g. `baseline_line`), `objective_value` (for `baseline_line`:
  the signed perpendicular distance above the baseline line, the same expression as
  `_descent_cost`), `baseline_line` (the line it was measured against: `td_a`,
  `eff_a`, `td_b`, `eff_b`, `w_td`, `w_eff`, `norm`, `bump_pct`, `current_a/b`).
  The two evals that *define* the line (A and B) have `objective_value: null`.
- `constraints`: `ripple_pct`/`ripple_max_pct`/`ripple_ok`, `v_peak_v`/`v_peak_limit_v`/`v_peak_ok`,
  `nonlinear_converged`, `eddy_settled`, `steady_state`, `demag_settled`, `demag_warning`/`demag_ok`,
  `qualified`, and `feasible` (all known verdicts true, else null when none known).
  Mechanics (SF) and thermal are **not** computed inside an eval, so they are absent.
- `solve` (steps, n_periods, mesh, n_sectors, gap layers, rotor_eddy, demag, rpm, ...),
  `sampling_purpose`, `elapsed_s`, `build` (`git_sha` from `APP_GIT_SHA`, `version`, `built_at`)
- failures only: `error_class`, `error` (one short line, paths removed, 300 chars max),
  `pre_solve: true` when the design was refused before any FEM started. A failed
  row has the input parameters (`overrides`, `current_a`, `gamma_deg`, `cfg_fp`)
  and none of the metric keys.

`status`: **ok** solved and healthy (the constraint verdicts may still be false);
**infeasible** the design was refused (geometry invalid, winding does not fit,
mesh budget); **failed** no usable answer (exception, worker crash, timeout,
non-convergence, non-physical result). A Stop (cancel) is not logged.

## Scan cache lines

`{"k","v"}` (schema 1) now also carries `"m"` (the provenance block above, with
`status: ok`). A failed or pre-solve-rejected sweep point is appended as
`{"k","m","inputs"}` **without `"v"`**. The loader serves a line only if it has a
`v` dict and `m.status` is not `failed`/`infeasible` (`eval_log.servable_cache_record`),
so a failure can never be a cache hit.

## Not logged

DOE samples (`_log=False` by design: they would pollute the surrogate) and cache
hits (the eval was logged in the run that paid for it).

Known limit: when the solve pool is off, a Stop kills in-flight workers and each
such eval is logged as `failed` / `worker_crash` (the pool path reports it as
cancelled and logs nothing). Filter on `error` text or on the run's end time if
that matters.
