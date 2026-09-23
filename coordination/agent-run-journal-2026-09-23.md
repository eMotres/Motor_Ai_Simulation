# Agent run journal — 2026-09-23

| Task | Model | Scope | Outcome | Escalation |
|---|---|---|---|---|
| solver_audit_inventory | GPT-6 Luna | Read-only code/test inventory of torque, symmetry and filtering | See audit checkpoint and parent report | None |
| Audit coordination and analytic reproduction | Codex, parent | Rules, current source review, direct helper check, evidence note | Harmonic limitation and 1 A discontinuity reproduced | Python execution permission only; no model escalation |

Haiku/Sonnet/Opus are not exposed by this session's subagent launcher.
No production solver files claimed or edited; no deployment or live API use.

## Ordered continuation

- GPT-6 Sol, `torque_gate`: analytic energy/virtual-work verification gates;
  owns only tests/test_torque_energy_validation.py and
  docs/torque-energy-gates-2026-09-23.md. Four standalone analytic tests passed;
  parent independently reran them (0.080 s). Review caught and corrected a
  tautological sign/storage check and an unreachable 12-branch selector case.
  No model escalation. Follow-up assessing an actual isolated FEM gate.
- GPT-6 Luna, `solver_speed_inventory`: bounded read-only review of redundant
  computation and existing performance work. Completed, no model escalation.
  Projection cache candidate rejected for default current-drive schedule:
  no repeated shifts. Narrow initial voltage-drive reuse only; no speedup claimed.
- Parent: architecture review and independent verification; no live API work.
  Work order remains torque model, symmetry/ripple, retained data, SERIES versus
  TRANSPOSED, remaining PARDISO lifetime. Performance opportunities are assessed
  alongside each item without reducing resolution or removing physical content.

- Sol follow-up: added no-CAD linear P2 torn-annulus test using real Banded2D;
  parent rerun passed (0.057 s). Rotor weld displacement and fixed-weld source
  rotation agree to roundoff. This validates discrete covariance, not production
  torque accuracy. Nonlinear/source-work check completed: two FEM tests passed
  on parent rerun in 0.269 s. Independent source-work derivative agrees with
  small-angle energy derivative to 3.7e-7 relative; a full-cell difference is
  4.73% lower. First audit item remains open for production integration.
- Luna follow-up: checking low-field B-H interpolation consistency between
  field and coenergy helpers, read-only; completed. Positive-first-B curves
  differ below their first knot; all 11 shipped steel/iron curves explicitly
  contain the origin, so no shipped-iron impact demonstrated. No model escalation.

## Economical continuation after usage feedback

- Reused GPT-6 Luna (`solver_speed_inventory`) for one bounded implementation:
  align missing-origin low-B scalar/vector field interpolation with existing
  coenergy convention. Owns field_ops.py, new test_bh_origin_consistency.py and
  a short evidence note. Complete; no model escalation. Parent reviewed diff:
  only missing-origin interval changes, explicit-origin hot path avoids added
  array operations. Parent isolated pytest --noconftest on test_field_ops.py
  and test_bh_origin_consistency.py: 24 passed in 1.09 s. No FEM campaign,
  restart, commit or deploy. Analytical torque selector remains unresolved.

- Reused GPT-6 Luna for isolated periodic terminal-work diagnostic helper and
  analytic gates, no production wiring yet. Purpose: independent mean-work
  comparison before changing the torque selector; preserve all waveform samples.
  Caller must certify lossless periodic settled state; no diagnostic for eddy,
  demag or unsupported windows. Completed by Luna, no model escalation.
  Parent standalone run: 6 tests passed in 0.003 s on installed Python311;
  bundled Python attempt lacked yaml. Tests cover nonlinear work, fifth/seventh
  harmonics, parallel paths, signed rotation and ineligible inputs. Coarsening
  sensitivity is not proof of convergence. No production integration or speedup
  claimed; diagnostic needs certified periodic conservative motor data next.

- Reused Luna to inspect existing isolated motor waveform artifacts for a
  provenance-supported comparison (no FEM launch). Completed: no eligible
  archived phase-current/flux/provenance record found; owns only
  docs/torque-motor-comparison-2026-09-23.md. Parent corrected diagnostic note:
  electromagnetic mechanical work is not shaft output including mechanical loss.

- Luna follow-up: one isolated fixed-fixture motor probe, one thread,
  BELOW_NORMAL child, hard 120 s wall timeout. Parent authorized only this
  bounded attempt; no API/sweep/optimization/config writes. Must capture raw
  phase inputs/linkages and full provenance, no invented periodic certification.
  Completed with one parent-authorized retry, no model escalation. First solve
  completed but harness lost results due missing NumPy import. Fixed import,
  immediate raw serialization and synthetic extraction preflight before retry.
  Retry: 37.5534 s wall, 36.671875 s CPU, 48 samples, child exited, live config
  hash unchanged. Hybrid 0.4096659527 N m; Maxwell 0.4081357575 N m.
  Phase-current/flux repetition recorded; full-field periodicity uncertified,
  therefore terminal-work helper not called. No correctness claim on mean torque.

- Luna reused for run02 numerical-work comparison and periodicity assessment.
  No new FEM: trapezoidal integral coarsening changes 3.5279%, so another run
  at the same angular resolution would not resolve the integration limitation.
  Parent requested unfiltered Fourier quadrature and sinusoidal-current residual
  on existing arrays to distinguish quadrature bias from physics; completed.
  Parent independent NumPy check: spectral fine 0.40966595268145173 N m-equivalent,
  stride2 0.4091551187373683; current sinusoid fit max error 2.34479e-13 A.
  Fine matches hybrid by its sinusoidal-current algebra, NOT independent field
  validation. Coarsening 0.1247% still fails 0.1% sensitivity criterion.
  No new FEM, no eligible helper call; full-field periodicity remains unproved.

- Luna angular refinement completed in 58.30 s, but derived path-work analysis
  failed review (duplicate pole-pair division and Nyquist handling). Raw data
  preserved; derived metadata invalidated. Escalated to GPT-6 Sol.
- Sol independent recomputation succeeded: mean change -0.00432%, raw ripple
  range +18.12%. Spectral work equals hybrid algebraically, not independent
  physics validation. Saved frames lack full P2 edge DOFs.
- Sol reused for one bounded isolated full-P2 capture via diagnostic wrapper;
  no production edits, one thread, BelowNormal, 180 s cap. Outcome pending.
- Sol full-P2 capture succeeded (~58 s): 17,299 DOFs, selected frames
  0/1/48/49, quadrature fields. Raw waveforms byte-identical to run03.
  Energy certification remains open: rotor physical permutation and nonlinear
  energy including PM sources still needed. No production or live changes.
- Continued GPT-6 Sol on saved run04 only: physical rotor symmetry mapping
  and nonlinear-energy evidence review. No new solve authorized. Reused agent;
  no further model escalation. Production formula and 1 A selector unchanged.
- Sol saved-state mapping completed; parent independently reran script (0.82 s,
  exit 0). Exact rotor coordinate permutation; mapped B max difference 6.309 mT
  (0.1645% peak), all quadrature points mapped. Energy remains uncertified due
  missing constitutive/PM source capture. No new FEM or live changes; no escalation.
- Reused GPT-6 Sol for exact material/PM reconstruction from saved run04.
  No new FEM authorized; production/live state read-only. Must verify input
  hashes and energy-functional derivative before reporting stored energies.
- Sol energy reconstruction: matching provenance; controlled derivatives pass.
  Parent caught zero-field helper bug; fixed and independently verified.
- FAILED capture run05: hook patched wrong alias; unintentionally ran isolated
  48x2 solve (~58s) instead of assembly-only. No live changes. Process exited.
- Corrected run06 with hard solver-entry/factorization stops succeeded under
  30s cap, no FEM frame. Exact mesh/basis equality; captured material/PM arrays.
  Parent reproduced energy integration (2.82s): closure +1.0902 and +0.8861 uJ.
  Sol checked all 73224 nonlinear quadrature values against production H law
  (max 2.33e-10 A/m difference); permeability floor inactive along integration.
  Partial overall audit; independent torque and symmetry convergence still open.
  Same GPT-6 Sol, no escalation. Failure preserved in docs/run05.
- Reused GPT-6 Sol for actual-motor virtual-work feasibility and minimal
  frozen-current displacement diagnostic. Saved fields/materials first;
  no new FEM authorized at this stage, no production/live changes.
- Sol frozen-current gate completed: run07 scope failure before solving;
  run08 solved4 but postcheck lost raw data; run09 corrected write-before-check
  and persisted4 states. Failures retained, no live/production changes.
  T1=0.4093778320 Nm; T3=0.4081688826 Nm; sensitivity0.295314%.
  Parent recomputed secants and checked raw A shape4x17299, exit0 (0.28s).
  All states converged. Source-vs-reported linkage difference5.65e-9Wb remains
  unexplained; no tolerance relaxation or physics certification. Same Sol,
  no escalation. Next: explain linkage mismatch and discretization sensitivity.
- Reused GPT-6 Sol for source/linkage mismatch attribution from saved run09;
  no new FEM/assembly authorized. Own new diagnostic/note only; source fixes
  pending exact cause and shared-worktree ownership check.
- Sol attributed ALL run09 source/linkage differences to equal-tag versus
  slot-area weights (reproduction <=1.08e-18 Wb), not quadrature noise.
  Scoped no-eddy P2 psi now uses assembled f_coil2 dot A, matching documented
  uniform-slot-J model; eddy branch unchanged pending physical-port review.
  Parent verified7-line diff and ran focused tests:11passed,12subtests,1.10s.
  Saved-state terminal virtual-work change ~-1.09e-6 Nm; no new motor solve.
  No escalation; no commit/deploy. Source/hash changed only after diagnostics.
- Commit d785a4c:19 owned files (solver fixes, tests, audit notes);37tests and
  12subtests passed in1.65s. Foreign files/config/scratch outputs excluded.
- Continued Sol read-only symmetry audit:12s14p allows full/half, rejects quarter;
  24s28p standard winding fixture supports1/2/4 (half periodic,quarter anti).
  Parent checked existing guard tests/source; no FEM or guard-suite execution.
  New note records common-angle/mesh requirements and limitations. No escalation.
