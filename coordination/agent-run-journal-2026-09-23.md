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
- Sol reused to prepare bounded numerical24s28p sector comparison (1/2/4),
  matched physical angles/current and slip density. No full waveform/sweep;
  execution pending parent review of stop/persistence preflight and CPU cap.
- Sol run10 safely stopped pre-mesh due early package import before config
  isolation; no FEM/live writes. Corrected run11 completed NS4/2/1 sequentially
  (15.57/24.22/43.22s), one angle0deg, fixed currents, no more solves.
  T=40.9584901/40.9523109/40.9426034Nm; all residuals<3.7e-8,14iterations.
  Parent independently checked common current/angle/slip/source/config hashes:
  half vsfull +0.023710%, quarter vsfull +0.038803%. Raw full fields persisted.
  Analytic template geometry, coarse distinct meshes; not ripple convergence
  or material-region equivalence certification. GPT-6 Sol,no escalation.
- Sol reused for bounded common-angle sector shape comparison after run11;
  prescribe matching synchronous currents (not frozen-current ripple), raw data
  retained, no production change. Execution pending preflight/cost review.
- Sol run12 completed5 synchronous-current angles per sector (NS4/2/1),
  wall21.25/43.75/65.42s, all15 Newton states accepted residual<=7.173e-8.
  Raw fields/material tags/nu/Hc/BH persisted per frame; no failed executions.
  Parent independently checked max torque differences vsfull:
  NS2 .0316983Nm(.07465%);NS4 .0616551Nm(.14521%). Same angle/current/hashes.
  Frame0 reproduces run11. Source law checked against production Excitation.
  Covers36electrical degrees, template geometry;not full ripple certification.
  Same GPT-6 Sol,no escalation;no production/live edits.
- Sol reused for full-electrical-period NS4 gate:authorized60samples on fixed
  1680-node ring,shifts0:2:118,raw waveform retained. Nested20/30/60 sensitivity
  without duplicate solves.180s wall cap,one thread,BelowNormal,stop on failure.
- Sol run13 NS4 completed60samples/full electrical period in110.31s,
  all60 accepted residual<=9.9601e-8;raw waveform+emergency A retained.
  Parent independently recomputed nested grids20/30/60:means40.24572955,
  40.24460079,40.24478513Nm;p2p5.55618878,5.14168407,5.57652082Nm.
  30point sampled range7.7976% below60point despite stable mean. This is
  sampling sensitivity,not continuum convergence or field-period certification.
  Same Sol,no escalation,no other sectors/extra solves/live changes.
- Sol reused:authorized missing odd slip angles1:2:119 only,NS4,same run13
  setup;180s cap,one thread,BelowNormal. Merge retained evens+odds into120point
  diagnostic,no filtered/discarded samples,no full/half or mesh changes.
- Sol run14 completed60missing odd shifts in95.91s;allconverged,raw preserved.
  Exact mesh/basis/material equality to run13. Parent independently merged by
  shift:120unique samples,mean40.2474570472Nm,p2p5.5765208241Nm unchanged;
  min shift54,max4. Mean+0.00664% vs60. Covers every integer slip cell only;
  spatial/continuum convergence and full/half period comparison remain open.
  Same GPT-6 Sol,no escalation,no new production/live edits.
- Owner declined finer spatial mesh; preserve this constraint. Owner explicitly
  requested Luna for next check. Reused GPT-6 Luna solver_speed_inventory for
  read-only saved-data/provenance review and economical full-period NS2/NS1
  plan. No FEM or assembly authorized in this subtask; no escalation.
- GPT-6 Luna independently implemented guarded NS2 matched20point run15,
  succeeded first FEM attempt in102.21s(100.39CPU),all20 accepted,
  residual<=9.3970e-8. No model escalation. Parent recomputed saved comparison:
  means40.24961239 vsNS4 40.24572955Nm,+0.0096478%;maxpoint diff.03587345Nm.
  Same1680ring,source/config/material hashes;no finer mesh,no NS1 yet.
  Raw retained;20point sample does not certify ripple. Sanitizer degenerate
  rotor-ring warning recorded in note;no production/live writes.
- Owner approved remaining full-ring comparison;reused GPT-6 Luna for NS1
  same20angles/ring/material/current asNS2run15 andNS4run13. Authorized single
  invocation240s wall,one thread,BelowNormal,no finer mesh,no live changes.
- Luna run16 NS1 completed20frames first invocation194.39s,allaccepted,
  residual<=8.9936e-8,child exited. No escalation. Parent independently checked
  means full/half/quarter40.25493388/40.24961239/40.24572955Nm and sampled
  ranges5.49766361/5.53019325/5.55618878Nm. Quarter range+1.06455% vsfull;
  maxpoint difference.07688007Nm. Same20angles;not true-extrema certification.
  No finer mesh,no live/source changes. Raw fields/provenance retained.
- Owner approved Luna1A selector audit. Reused GPT-6 Luna for call-site and
  synthetic discontinuity review;no new FEM or production edits authorized.
  Must distinguish arbitrary threshold from general energy-formula validity.
- Luna threshold audit completed,no escalation. Parent reran standalone actual
  helper reproduction exit0(.34s). Synthetic Maxwell DC offset+0.25Nm gives
  selected mean jump~0.2498Nm at1->1.001A;NOT measured motor error. Raw p2p
  unchanged. Method threshold applies per branch;report consumers use selected
  mean. No source change:unified physics-valid replacement remains unresolved.
- Reused Luna for executable method-evidence audit. No production periodic
  energy certificate exists. Authorized additive P2 diagnostics exposing both
  candidate means and explicit uncertified status,without changing selector or
  waveform. sb_postproc/fem clean before scope;focused tests,no FEM/live writes.
- Luna implemented additive uncertified torque-method diagnostics in
  sb_postproc/P2 result. Both means,delta,branch peak and legacy-selector state
  exported;selected output unchanged. Parent caught finite-input overflow risk;
  fixed with guarded arithmetic/finite output checks and JSON-safety test.
  Parent focused suite16passed18subtests1.52s,diffcheckpassed.NoFEM,noescalation.
  Threshold replacement still unresolved;this adds evidence,not certification.
- Owner requested continue untilfix. Luna traced k1SERIES/TRANSPOSED difference
  to per-body area-weighted Iunit versus single path current. Parent rejected
  premature mode-canonicalization; authorized physical eddy Iunit=orientation
  (effective branch division already upstream),multi-body tests and one guarded
  corrected-mode pair30mm12steps60A <=120s,total1thread,BelowNormal. No livewrites.
- Luna fixed eddy physical per-conductor current coefficient toorientation
  only;effective branch count already upstream. Unequal mesh areas no longer
  perturb series-turn currents. Earlier suggestion tocanonicalize k1 rejected
  by parent until physical source mismatch proved. No model escalation.
- Corrected real30mm12steps k1 A/B pair completed~41.6s,cold separate guarded
  processes. Both modes copperAC3.277W,total65.509W(oldseries3.240/65.473).
  Series path exercised14frames,current conservation error0. Parent verified
  raw IABC identical,psi differences<=2.863e-17Wb,Tmaxwell<=2.271e-14Nm.
  54tests6subtests passed1.60s,diffcheckpassed. Earlier hash-case preflight
  failure stopped beforeFEM,then fixed. No livewrites/filter/finer mesh/deploy.
