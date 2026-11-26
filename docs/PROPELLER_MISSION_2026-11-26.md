# Propellers > Mission (2026-11-26)

Owner request: choose a motor of the catalogue, set the mission (fixed battery, airframe, flight), get an optimised propeller; the default goal is the
maximum range, other goals are selectable. This note is for reviewers who cannot read the pull request text.

## What the tab does
1. **Motor.** The picker lists the motors of the catalogue (`GET /api/catalog` through `routes/catalog.get_catalog`: read-only, the caller's
   visibility), grouped by die and searchable. Each motor has a model kind:
   - *full flight model*: a propeller S1 model exists for it (`propeller_s1.model_dirs()`, matched by the name `<die> <config>`) and the caller may see
     that configuration (`motor_access.may_see_config`);
   - *estimate, not an S1 model*: a simple S1-schema model built from the stored passport of the card (Kv from the no-load EMF, phase resistance at its
     temperature, poles, current sweep or Kt from Kv, peak current, iron loss from the base point scaled with rpm^1.5 or omitted and flagged, magnet
     eddy loss with rpm^2, thermal geometry from the card or generic and flagged, voltage by a simple rule with the HOT resistance);
   - greyed with the reason when even Kv / R / poles / turns are missing.
2. **Stage 1.** A listed set of propellers: catalogue propellers with usable data (reference rows) and the own family on an explicit grid (diameter in half
   inches, P/D, chord scale, blades). For every propeller the motor turns N (strip from `config/wire_stock.yaml`, coil not taller than today's) are chosen by
   an inner step: the largest N whose peak point is reachable at the end-of-discharge pack voltage under load, with a voltage headroom. Forward flight by the
   momentum / energy method (rpm ~ P^(1/3)). Hard constraints: T/W continuous and peak, board current cap, winding / magnet temperature, demagnetisation,
   tip Mach, voltage headroom, battery current (when given), controller bus window (when known). A failed row is never ranked above a passing row.
   Objectives: range (default), endurance, top speed, hover g/W.
3. **Stage 2.** The EXISTING `propeller_optimize.optimize` (NSGA-II) refines the top 1-3 own candidates through the opt-in `mission_objective` (a callback
   that returns the mission value and the failed mission rules). Diameter and blade count stay (the optimiser's rule). The result is the optimised blade
   with its mission numbers next to the stage-1 best, the gain, the winding it needs and its geometry; "Save as Designer draft" stores it through the existing
   drafts path. Nothing is written to the catalogue.
4. A run is a **job of the server's queue** (kind `propeller_mission`, `jobs.run_job`): the caller's workspace, the per-user limit and the drain checks of the
   queue apply, Stop is the queue's cancel with its owner check, the poll reads the job record and the run file. The last 100 runs of a workspace are kept.

## Model limits (also on the page)
- Estimate: forward flight is momentum / energy theory on the STATIC curve, no edgewise blade-element solution; numbers rank candidates, absolute values are not
  measured. "Maximum" means the best sampled / refined value at the chosen speed, not a proven global optimum.
- Motor efficiency is computed from stored torque / loss / flux data and a thermal fixed point (the S1 model), with winding / Kv scaling by turns; no new FEM map.
- The estimate motor model has no field weakening or inductance and no magnet-temperature effect on Kv; it is labelled so in the picker, the run and the result.
- An S1 model without a voltage map cannot prove the headroom: by default such a row fails (`voltage_unverified`); "Allow unverified voltage" is an explicit
  opt-in and the result then shows the check as unverified.
- Pack: E = S * Ah * Vnom * usable; internal resistance R = S * r_cell_ohm_ah / Ah (editable preset); linear open-circuit voltage between the cell's max and min.

## The four points of the pull request text
1. Runs executed in a daemon thread inside the API process -> now a job of the queue on the compute worker (finding 1).
2. The gate was a copy of the `/design` rule -> the central entry in `auth._GATED_PREFIX` (PR 919, DELETE: PR 920); the local gate is removed.
3. Physics fix: forward-flight rpm ~ P^(1/3) (the study scripts executed P^(1/6)).
4. Estimates and open points: battery R default, estimate motor without field weakening, card <-> S1 name matching, S1 files without `winding` / `kv_bench_rpm_per_V`.

## How each finding of the independent review (PR 915, 2026-11-26) was addressed
1. *API computation outside the queue.* `propeller_mission_api.start_run` submits `jobs.run_job("propeller_mission", ..., block=False)`: no own thread, no registry,
   no `MAX_RUNNING`; admission, per-user limit and drain checks are the queue's; state is the job record plus the run file; a refused submission removes its files;
   retention keeps 100 runs per workspace (oldest removed on write); `DELETE /api/propellers/mission/runs/{id}` removes a finished run (409 while it runs).
   No handler is registered for the kind (a remote node / the other region must never get a run that needs the caller's catalogue access).
2. *Hidden configurations.* `motor_access.catalog_access(authorization)` is carried into the picker and the resolver; `may_see_config` is applied before listing
   or reading any S1 model, including card-name matches and direct `{die, config}`; a hidden model answers like a missing one (same 422 text, the card falls to
   the estimate path); the picker row is an allow-list (`ENTRY_FIELDS`), tested.
3. *Central gate.* Done by PR 919 / 920 (`WORKER_PREFIXES`, nginx rule, `_GATED_PREFIX`); the local `_mission_gate` is removed; `_ROUTES_PENDING` removed from
   `tests/test_deploy_stage2_roles.py`.
4. *Stop.* Stop = `jobs.cancel_run` with the owner check; only the caller's own workspace is searched (foreign id = 404); `MissionCancelled` (not a `DesignError`)
   is checked inside the rpm, speed, turn and motor-pack loops and before the final recomputation; the terminal state is `stopped` with the partial results and the
   stage; a late stop also ends as `stopped` (results complete). Tests: foreign id, both stages, late stop, stop through the queue.
5. *Inconsistent voltages.* `solve_pack_point` iterates motor losses / current and pack sag to convergence (non-converged or impossible points are rejected); its
   voltage is used for flight, peak, thrust-limit and Kv checks; the required hover and cruise points are rechecked at the END voltage with headroom and their
   failures go into the candidate (`hover_end`, `cruise_end`). Regression tests: the 800 W / 24 V / 0.05 ohm / 50 % case (20.000 V, not 21.695 V), high R, mean-pass /
   end-fail.
6. *Unverified shown as passing.* `verification` per point (`verified` / `estimate` / `unverified`); an unverified voltage never counts as headroom; the result
   lists every check (voltage, bus window, board current, battery current, demagnetisation) as verified / unknown beside the numbers; the estimate model uses the
   hot resistance; a known drive bus window and the battery current limit (input) are enforced.
7. *Evaluations and provenance.* Every stage-2 evaluation (failures included) is stored by the optimiser's own record format in `<run>.opt/seed<k>/<opt-run-id>.jsonl`
   and linked (`optimizer_run_id`); the run keeps inputs, the stage-1 vector and curve source of every row, and `provenance` (code commit, hashes of the design file,
   the polar tables and the wire stock, the motor model sha256, solver identities).
8. *UI.* Explanations are label tooltips and values sit in labelled cells; min / max pairs show one field (lowest cell voltage, largest diameter, one P/D range)
   with the others in the tooltip; statuses and errors are translated (EN, zh-CN); validation is per field with its reason; the opened run and the selected row are
   remembered and validated (a missing target falls back silently); stale polls are ignored; the action is named "Save as Designer draft" (the Designer page is not
   changed, so it cannot open the draft itself).

## Optimiser compatibility
`propeller_optimize.py` changes only its objective check: `mission_objective` is accepted when an `evaluate` callback is passed. `tests/test_propeller_optimize_golden.py`
compares every deterministic field of two existing `optimize()` inputs with the result of the BASE code stored in `tests/data/propeller_optimize_golden_mission_pr.json`
(run id, elapsed time and store path excluded).

## Tests
`tests/test_propeller_mission.py`, `tests/test_propeller_optimize_golden.py`, the unchanged `tests/test_propeller_optimize.py`, `tests/test_deploy_stage2_roles.py`;
`web/src/lib/__tests__/propellerMission.test.mjs`. Synthetic S1 model and closed-form propeller curves; no private model data, no UIUC or APC files.

## Second review (#922, 2026-11-26) and how it was met (commit after 3f26ec26)
- **B (P1) loaded bus voltage:** `solve_pack_point` compares the converged LOADED pack voltage `st["V_V"]` with the drive's `bus_v`
  window before it returns a point; every motor point (hover, cruise, both at the end of discharge, peak/Kv check, thrust limits,
  speed scans) goes through it. `checks.bus_window` is `fail` on a loaded failure; the full-charge open-circuit upper bound check stays.
  Tests: the review's 18.06 V case (loaded about 17.9 V fails), a valid window passes, open-circuit-pass / loaded-fail fails the candidate.
- **E unknown winding:** identity scaling inside; `turns`, strip sizes and `winding.turns0 / strip_*` are `null` with `winding_known: false`.
- **A retention / terminal write:** no pruning before admission; the progress file is removed only after the terminal file is written,
  a failed write leaves it as `failed` with the reason (GET still finds the run); retention counts ended progress-only records
  (stopped / interrupted / failed), deduplicated by run id, never an active job; one list row per run.
- **D UI:** P/D shows one value (largest) with the smallest in the tooltip, stored range kept; the "checks unavailable" note and all
  server details (failure detail, run error, request error) moved into tooltips, visible text translated only; hidden-bound errors
  (`minD`) and the server's `airframe.min_diameter_in` go to the visible diameter field; min > max refused before submit; a remembered
  motor that is no longer offered is cleared.
- **C dataset collector (for the coordinator, collector lives outside this repo):** files of a run in the caller's workspace:
  `<workspace>/propeller_designs/mission_runs/<run_id>.run.json` (terminal record: inputs, motor + model kind + sha256, results,
  provenance), `<run_id>.progress.json` (while queued/running, or a run whose terminal write failed), and the stage-2 optimiser
  records `<run_id>.opt/seed<k>/<opt run id>.jsonl` (`propeller-eval-1`, one line per evaluation incl. failures) + `.run.json`,
  linked from the mission result by `refined[k].optimizer_run_id`. Retention removes the oldest beyond 100 ended runs per workspace.
