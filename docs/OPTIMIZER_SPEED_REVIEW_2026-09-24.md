# Optimizer speed review of 68de0ca..0f973bb (2026-09-24)

This review is by Claude Opus 5.5 (`claude-opus-5-5`), a sub-agent with no escalation. It continues the earlier
68de0ca..821f3df review (`coordination/codex-review-2026-09-24.md`). The verdicts on
c582449, 909b014, 8997bb3, a88ae64, f83e60f and 7f57cf9 were settled there. The new commits do not
touch the solver files, so those verdicts stand. Production runs 68de0ca.

## How it was measured

- **Evaluation path.** Every timing is the optimizer's own evaluation path. It uses a fresh
  `python -m motor_ai_sim.optimization.refine_proc` child with the JSON spec on stdin. The environment
  matches `_base_eval_env`: one BLAS thread, `SB_SEED_FROM_PREVIOUS=1`, a sandbox `MOTOR_AI_SIM_CONFIG`
  and, on 0f973bb, the parent's `PYPARDISO_MKL_RT`. Wall time runs from Popen to the result, so it
  includes subprocess start. Both commits ran from their own worktree with `PYTHONPATH` pinned, and the
  module path was asserted.
- **Workstation.** Ryzen AI 9 HX 370 (4 Zen5 + 8 Zen5c cores, 24 threads). Each single-candidate
  timing ran alone and back-to-back with its pair.
- **m12, the machine the owner currently optimises.** This is the owner's live server workspace config
  (CIANO14 30_10 / L10, Ø30, 12s/14p, copied read-only). Settings match his sweeps: 48 steps/period,
  P2, 1.0 mm mesh, 2 sectors, coupled eddy and demag, 15000 rpm, 32 A, γ 6°.
- **m24.** CIANO28 85 20SW1200 / L13 rated (24s/28p) with its own duty settings: 40 steps, 1.22 mm,
  4 sectors, eddy and demag, 1000 rpm, 26.09 A, γ 2°.
- **Typical candidate.** It is seeded from the previous point's warm state and has a new geometry
  (magnet_fill_up + k·0.01), as in every descent, CMA or screen wave.

## 1. Per-candidate cost, old vs new (solo, paired)

| machine | 68de0ca | 0f973bb | change |
|---|---:|---:|---:|
| m12 seeded, new geometry (pair x8) | 75.1 s | 125.6 s | **+67 %** |
| m12 (pair x7) | 49.1 s | 88.4 s | +80 % |
| m24 seeded, new geometry (pair x8) | 76.3 s | 133.6 s | **+75 %** |
| m24 (pair x7) | 98.5 s | 210.5 s | +114 % |
| m12 first (cold) point of a run | 174.3 s | 336.0 s | +93 % |
| m24 first (cold) point | 288.2 s | 584.9 s* | +103 % |
| m12 seeded, same geometry (no calibrations) | 40.0 s | 34.4 s | −14 % |
| m24 seeded, same geometry | 78.3 s | 95.6 s | +22 % (noise) |

\* Taken while a profile run was also running.

Phase split from the solver's own INFO lines, solo pair x8:

| phase | m12 old | m12 new | m24 old | m24 new |
|---|---:|---:|---:|---:|
| start-up, imports, MKL | ~3 s + 3.5 s MKL scan | ~3 s | ~3 s + 3.5 s | ~3 s |
| d-axis calibration (24 no-load frames on the 0.5 mm calibration mesh) | 29.8 s | 23.4 s | 23.6 s | 24.8 s |
| **loaded solve (48 or 40 frames + 3 settle)** | 30.6 s | 25.3 s | 34.6 s | 35.9 s |
| **ψ_PM no-load probe (Ld/Lq summary row)** | **11.0 s (6 frames)** | **73.9 s (96 frames)** | **13.3 s (6)** | **68.1 s (120)** |
| summary and post-processing | ~1 s | ~1 s | ~1.5 s | ~1.5 s |

Findings:

- **The whole regression is 8997bb3.** Its cogging policy also applies to the solver's internal
  6-frame no-load probes, `noload_psi_pm` (and `noload_incremental_ldq` and the bench) in
  `fem_solver_2d.py`. It raises them to 96 frames (144-node ring) or 120 frames (24s/28p). The
  d-axis calibration is exempt (`internal_daxis_calibration`), but ψ_PM is not. Run log:
  `SB: cogging resolution raised to 96 raw frames/period (requested 6 ...)`.
- **The optimizer throws the probe's result away.** The probe runs inside `_build_transient_summary`
  for every new geometry. It only feeds Ld/Lq, and `refine_proc.run_one` never reads either value.
- **The loaded solve itself is unchanged**, within ±20 % run-to-run Newton-iteration scatter.
- **Probes are 40–70 % of a candidate** even on 68de0ca. The d-axis calibration and ψ_PM probe cost
  41 s of 75 s on m12.

## 2. Added final-quality re-solves at 0f973bb

A standard solve runs at 6 samples/cycle: 72 frames on m12, 120 frames on m24. The eddy warm seed is
keyed on the effective steps/period (`_warm_seed_accept`, a hard refusal). So the first standard solve
after optimisation-purpose evaluations is always cold:

- m12: **155.5 s**, with warm-up 147 frames and demag pre-pass 72.
- m24: **551.5 s**, with warm-up 243 frames and pre-pass 120.

A seeded standard solve takes 62–67 s on m12 and 117 s on m24.

| event | m12 | m24 |
|---|---:|---:|
| Winner validation per descent, CMA, auto or screen run (A cold + B + 3 finalists, sequential as coded) | ~404 s | ~1020 s |
| Same, if B and the finalists ran in parallel after A (measured N=4 wave: 98 s) | ~254 s | ~730 s (est.) |
| Every Sweep "Verify and apply" (one standard solve; the warm cache holds the sweep's optimisation state, so it is cold) | ~155 s | ~550 s |
| Before this block | 0 | 0 |

- **Warm cache after a standard solve.** A standard solve publishes its 72/120-step state. The next
  optimisation evaluation in that workspace then starts cold. This was observed twice in the harness.
- **821f3df saves nothing at the owner's settings.** The optimizer takes `steps_per_period` from the
  Simulation tab (48 on m12, 40 on m24), which is already at least 3 samples/cycle (36). 821f3df only
  lowers the 72/120 that 8997bb3 forced. Candidates solve 48/40 frames on both commits.

## 3. Throughput (one wave of N new-geometry candidates, 1 BLAS thread each, m12)

| N | 68de0ca cand/h (wave) | 0f973bb cand/h (wave) | 0f973bb + fixes A+B cand/h (wave) |
|---:|---:|---:|---:|
| 1 | 48 (75 s) | 29 (126 s) | 116 (31 s) |
| 4 | 162 (89 s) | 82 (176 s) | – |
| 8 | **238** (121 s) | **130** (221 s) | **575** (50 s) |
| 12 | 208 (207 s) | 116 (372 s) | 560 (77 s) |
| 6 × 2 threads | – | 81 (266 s) | – |

- **Saturation is at about 8 workers.** Going from 8 to 12 loses 10–12 % throughput because the Zen5c
  cores and memory are shared.
- **Two BLAS threads per child is worse** than one thread with more children.
- **RSS is about 310 MB per child**, 3.7 GB at N=12, so memory is not the limit.
- **Default worker count.** `_SCAN_WORKERS` is 10 on this box (physical − 2); `FEM_SCAN_WORKERS=8` is
  the measured optimum.
- **Server (AX42).** It has 8C/16T, and the default of 6 workers was not load-tested here. MKL
  discovery in the `deploy-api-1` container takes 0.27 s and resolves to
  `/usr/local/lib/libmkl_rt.so.3` (absolute path, file exists). The 110352a hint cuts a child's import
  to 0.155 s, which is safe but worth only about 0.1 s there.

## 4. Commit decisions (68de0ca..0f973bb)

See the table in section 6. In short:

- **Real speed-up:** only the 110352a MKL hint, −3.5 s per candidate on Windows (child start 5.2 →
  1.65 s, measured three times each way). f83e60f helps voltage-drive runs, not the optimizer, which
  uses current drive.
- **Cost time:** 8997bb3 (+50–55 s per candidate through the ψ_PM probe, ×2–3 frames on every standard
  run), 110352a validation (+7 min per run on m12, +17 min on m24), and 0f973bb Apply (+2.5 min or
  +9 min per click).

## 5. Ranked speed-ups for the optimizer path (measured where marked)

**Fixes A and B were emulated inside the child only** (no repository change). Fix A replaces
`noload_psi_pm` with a constant. Fix B makes `_resolve_daxis_shift` return the baseline geometry's
calibrated DAXIS. On the same 12 geometries, T, η, ripple and P_fe matched the calibrated run to
reported precision; the largest difference was one ripple value, 4.50 → 4.51 %. The DAXIS values seen
across candidates were 59.970–60.014°.

1. **A — no ψ_PM probe in optimizer evaluations.** Gain: −74 s per candidate on 0f973bb, −11 to −13 s
   on 68de0ca. Risk: none to the ranked metrics, because Ld/Lq are not in the refine_proc result.
   Also exempt every internal no-load probe (ψ_PM, ldq0, bench) from the cogging policy. That is the
   8997bb3 side-effect, and it hits every Simulation run of a new geometry too.
2. **B — reuse the run's baseline d-axis for candidates, and re-calibrate only in final validation.**
   Gain: −23 to −30 s per candidate. Risk: very low. The measured θ* spread is ±0.04° electrical and
   metrics were identical. Keep calibration per candidate for die-topology changes, or when a
   variable moves magnets by more than a set threshold.
   - **A+B measured:** m12 31 s per candidate, against 75 s on 68de0ca and 126 s on 0f973bb. At N=8
     that is 575 cand/h against 238 and 130: **2.4× production and 4.4× the current HEAD**.
3. **C — `FEM_SCAN_WORKERS=8` on this workstation** instead of 10 or 12. Gain: +5 to +12 %
   throughput, measured between 8 and 12. Risk: none.
4. **D — parallel final validation** (A first so it seeds, then B and the 3 finalists together).
   Gain: m12 404 → 254 s per run (measured 98 s wave), m24 about 1020 → 730 s. Risk: none (same
   solves).
5. **E — let a standard solve seed from the optimisation-state warm cache** by re-projecting the eddy
   history to the new time step. The settle probe already decides whether to extend the march. Gain:
   cold standard 155 → about 65 s (m12) and 552 → about 120 s (m24), for every validation and every
   Sweep Apply. Risk: medium (new projection code) and needs an A/B.
6. **F — one warm worker process per slot** instead of a subprocess per candidate. Gain: about 1.5 s
   of imports per candidate after the MKL hint, 1–2 %. Risk: state leakage (caches, TLS, Br maps).
   Not worth it now.
7. **G — mesh/template reuse across candidates.** Gain: CAD, mesh and projection build is about
   2–4 s per candidate (cProfile: `get_2d_polygons` 2.0 s, `p2_projection.build` 2.0 s), 3–5 %.
   Risk: the cache key must cover every shape key. Low priority.
8. **Not recommended:**
   - Fewer settle periods: seeded candidates already settle in 3 frames, and the cold warm-up is paid
     once per run.
   - Screening without coupled eddy: eddy is about 70 % of the loaded solve, but at 15000 rpm it moves
     η, the ranking metric.
   - More BLAS threads per child: measured slower.

## 6. Decision table

| commit | what changes for client / owner | verdict | deployable independently? |
|---|---|---|---|
| 1402fcf, 7273fc2, a06b8cb | P2 energy-state and terminal-work diagnostics | accept | yes |
| f12df2e | raw/detrended core-loss candidates exposed; selection unchanged | accept | yes |
| 99b9bd0, bebe509, ef05c51, 538676f, 94501ba, 153ea73, c5c2a98, c75d3f9, 3d8e2ea, 6087430, 1196efd, a0a8dde, 0f01f8f, 9330125, dce107e, b529a0c, 59741f0, 7b2ebd9, 5b3b457 | docs, audits, benchmarks (no `src/`) | accept | yes |
| 1c23f12 | refuses cut/clipped eddy conductor bodies; history snapshot hardened; sleeve loss settle-trimmed (voltage/PWM eddy P_sleeve 8.23 → 8.33 W) | accept | yes |
| c582449 | raw-window core loss: L155 P_fe +3.5 %, rotor 56.9 → 108.2 W | **reject as is**; accept with a commensurate rotor window | after f12df2e |
| 909b014 | torque selector: eddy/demag/voltage runs fall back to the Maxwell mean (L155 −1.81 %; consistent with m12 here, 0.219 → 0.216 N·m, not isolated) | **accept with change**: ineligible runs keep the space-vector mean | yes |
| 90fffa4 | controller SPICE switching source, default stays datasheet | accept (not solver) | yes |
| 7f57cf9 | PARDISO RLock | accept | yes |
| f64e118 | sparse P2 mortar derivative (diagnostic) | accept | yes |
| a88ae64 | virtual-work torque on every conservative frame | accept with change: env opt-in | yes |
| 8997bb3 | 6 samples/cycle for standard runs; **also inflates the internal ψ_PM/ldq0/bench probes 16–20×**, +67–75 % per optimizer candidate | **reject as default**; accept as an opt-in cogging-quality mode with internal probes exempt | yes |
| f83e60f | plain sine-voltage settle 10 → 4 | accept | yes |
| 821f3df | optimizer 3 samples/cycle; eval-cache key v4 (old cache retired) | accept only together with 8997bb3; no speed-up at 48/40 steps | needs 8997bb3 |
| d7e1db7 | benchmark docs and script | accept | yes |
| 110352a | winners re-solved at standard; picard stamp fail-closed; Apply disabled for preliminary results; MKL hint | **accept with changes**: (a) a failed finalist should drop out, not fail the whole run, as long as A, B and at least one finalist pass; (b) run the validation solves in parallel after A; (c) add on-demand validation for descent/auto picked points, because old stored runs and picked points cannot be applied at all; (d) ship the MKL hint separately, now | validation needs 8997bb3 + 821f3df; MKL hint independent |
| 0f973bb | Sweep Apply re-solves the point at standard first | **accept with changes**: (a) `tests/test_sampling_purpose_flow.py::test_every_optimizer_eval_and_cache_call_explicitly_names_purpose` **fails** because `_subprocess_eval(**args)` hides the purpose; pass `sampling_purpose="standard"` explicitly and update the test; (b) the `sp["cfg_fp"] != _config_fingerprint()` check uses the un-excluded fingerprint, so once one point is applied every other point of that sweep returns 409; compare the swept-excluded fingerprint; (c) old sweeps without `validation_provenance_version` return 422 and must be rerun | needs 110352a |

Checks:

- `tests/test_sampling_purpose_flow.py`, `test_screening_descent.py`, `test_optimizer_honesty.py`,
  `test_optimizer_final_validation.py`, `test_scan_standard_validation.py`, `test_pardiso_runtime.py`
  and `test_bench_angular_sampling.py` at 0f973bb: **112 passed, 1 failed** (above). The failing
  test passes at 110352a.
- Proxy timeouts for the minute-long `/scan/validate_point` are 3600 s on both the host and
  container nginx, which is enough.

## Reproduction

The harness is in the orchestrator scratchpad `rev0924/spd/`:

- `spd_driver.py`: solo, paired and profile runs.
- `tp_driver.py`: throughput waves and the fix emulation.
- `import_bench.py`: MKL start-up.
- `prof_split.py`: cProfile phase split.

Results are in `res_*.json`, `tp_*.json`, `log_*.err` and `split_*.txt`.
