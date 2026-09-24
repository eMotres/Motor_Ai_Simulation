# Review of 68de0ca..0f973bb — what Codex should finish (2026-09-24)

Reviews: Fable 5.1 (68de0ca..821f3df) and Opus 5.5 (..0f973bb + optimizer speed),
full report `docs/OPTIMIZER_SPEED_REVIEW_2026-09-24.md` (df4caac).
Production = branch `deploy/2026-09-24` (68de0ca + 199e08c admin shared-die delete).
Nothing below ships until fixed and re-reviewed.

## Accepted as is
Docs/audits, 1402fcf, 7273fc2, a06b8cb, f12df2e, 7f57cf9, f64e118, f83e60f,
d7e1db7, mortar/state-capture diagnostics.

## To fix
1. **909b014 torque selector** — for runs that are NOT eligible for the
   terminal-work mean (eddy / demag / voltage = every client report run) keep
   the flux-linkage (space-vector) mean, not the raw Maxwell mean
   (L155 rated 187.855 → 184.451 N·m, −1.81 %; Ø40 −1.34 %). House rule:
   energy / flux-linkage torque only.
2. **c582449 raw-window core loss** — no filter is right, but the rotor window
   is open: raw rotor loss grows with Nyquist (×1.18 @12 → ×3.35 @120 samples;
   L155 rotor 56.9 → 108.2 W). Evaluate the rotor iron on a commensurate window
   (lcm-based; 7 electrical periods for 12s/14p); stator stays on one period;
   no legacy ramp guard on closed windows. Show rotor convergence vs steps.
3. **8997bb3 cogging ≥ 6 samples/cycle** — not as the default for every run.
   Make it an opt-in "cogging quality" mode (and the dedicated no-load cogging
   run). INTERNAL probes (ψ_PM no-load probe, d-axis calibration, Ld/Lq probe
   and bench) must be exempt — today they are inflated 11 → 74 s per candidate.
   821f3df follows whatever 8997bb3 becomes.
4. **a88ae64 virtual-work torque** — behind an env flag (diagnostic).
5. **110352a winner re-check** — one failing finalist must drop out, not kill
   the run (need both baselines + ≥ 1 finalist); run the seeded re-solves in
   parallel after the first (m12 404 → 254 s); stored descent/auto runs and
   picked points need an on-demand re-check so Apply works for them.
6. **0f973bb sweep Apply** — `test_every_optimizer_eval_and_cache_call_explicitly_names_purpose`
   fails (`_subprocess_eval(**args)` hides the purpose); a second point of the
   same sweep gets 409 because applying the first changes the full config
   fingerprint — use the fingerprint without the swept variables; existing
   stored sweeps return 422 (incl. the owner's 22 Sep sweep) — must stay
   appliable or be re-checked on demand.

## Done by the orchestrator's agents in parallel (do not duplicate)
Optimizer speed-ups A + B + C (owner-approved 2026-09-24), implemented on
`deploy/2026-09-24` and ported to this branch:
A) optimizer candidates skip the ψ_PM no-load probe; B) candidates reuse the
run's baseline d-axis calibration (re-calibrated in the final validation and for
d-axis-moving overrides); C) default worker count from the machine (8 on the
owner's workstation), `FEM_SCAN_WORKERS` overrides. Files: refine_proc.py,
routes/optimization.py, fem_solver_2d.py call sites. Rebase on them.

## Re-pin
`tests/test_physics_regression.py` only after 1–3, one commit stating each
contribution.
