# Torque symmetry audit: sector scaling and parity (2026-09-23)

Read-only audit of the current P2 sliding-band torque path. No FEM runs were performed. Line references below point to the checkout audited on 2026-09-23.

## Verified code invariants

- Pole and slot counts resolve from explicit `num_poles` / `num_slots`, falling back to segment products only when the explicit counts are absent (`src/motor_ai_sim/simulation/fem_solver_2d.py:2377-2381`).
- `n_sectors <= 1` selects the full ring (`NS=1`). Sector requests with the normal geometry/template path can select the template wedge; experimental geometry wedges are separately gated (`fem_solver_2d.py:3201-3239`). Before calibration or mesh construction, the resolved phase basis is checked for slot/pole divisibility, the current paired-stator two-slot repeat, and phase/sign agreement across the sector boundary (`simulation/geometry_2d.py:508-544`; call at `fem_solver_2d.py:3235-3240`).
- The anti-periodic sign is selected when the sector contains an odd pole count, otherwise the cut is periodic (`fem_solver_2d.py:3241-3249`). This sign is passed into the signed slip projection (`fem_solver_2d.py:4952-4957`). For 24s28p, NS=2 means 14 poles/sector and periodic sign; NS=4 means 7 poles/sector and anti-periodic sign. For 12s14p, NS=2 means 7 poles/sector and anti-periodic sign; NS=4 fails divisibility and must be rejected.
- The generated winding basis is star-of-slots with phase and direction per slot (`simulation/geometry_2d.py:468-505`); a valid explicit layout is used as supplied. The sector validation checks actual phase and sign, including zero-current use (`geometry_2d.py:508-544`).
- The raw Arkkio/Maxwell integral returns sector torque (`simulation/field_ops.py:305-315`); each frame multiplies it by effective `NS` (`fem_solver_2d.py:6710-6714`). The winding flux linkage uses `stack_length * NS / n_parallel` (`fem_solver_2d.py:5276-5298`), so the selected space-vector mean is also on full-machine scale. The selected waveform retains Maxwell AC around that mean; this is documented in `simulation/sb_postproc.py:91-149`.
- The solver marches one electrical period in mechanical angle, `360/pole_pairs` (`fem_solver_2d.py:4607`), and trims settling frames before constructing returned angles and torque diagnostics. Returned angle window and time period metadata are formed at `fem_solver_2d.py:7732,8158-8160`.
- Harmonics are calculated on every retained raw Maxwell torque sample, using the scheduled mechanical step normalized by one electrical period (`fem_solver_2d.py:7635-7642`). `torque_harmonics` retains all resolved bins and does not truncate, demean, or classify them as noise (`simulation/sb_postproc.py:152-175`). Raw peak-to-peak ripple is calculated from the unchanged series as `(max-min)/abs(mean)` (`simulation/field_ops.py:351-363`). **No torque samples or harmonic bins are filtered or discarded.** Near-zero no-load mean makes ripple percent ill-conditioned; assess no-load error in N·m as well.

## Evidence gap and risks

Checked-in `tests/test_sector_symmetry_guard.py:20-68,96-154` tests admissibility and rejection, and `tests/test_p2_projection.py` / `tests/test_p2_projection_fields.py` test signed coupling constraints and analytic annular fields. These establish the guard and projection behavior, not parity of actual machine torque across NS values. `tests/test_physics_regression.py:145` contains a sector-run reference, but does not compare the same physical solve across full and sector modes. No checked-in test was found that compares actual full-ring and wedge Maxwell mean, selected mean, ripple waveform/range, or harmonics.

Code inspection shows consistent `NS` scaling for sector Maxwell torque and winding flux linkage. It does not prove that the actual CAD, material assignment, winding source, and moving-band discretization reproduce a rotationally equivalent machine. The logged full/half/quarter results in `docs/solver-torque-validation-plan.md` are unconverged diagnostics and show that similar loaded means do not establish ripple parity.

The result records requested sector-related values in some metadata, but validation evidence must record the **effective built NS** and the actual **BC sign** after geometry/template routing, so a request cannot be mistaken for the discretization that ran. Also retain the realized build mode. No mesh refinement belongs in the initial parity comparison; it would confound symmetry error with resolution error.

## Minimal parity validation matrix

Use identical geometry, winding basis, materials, operating point, source, mesh settings, slip-ring density, and shared mechanical rotor-angle samples within each comparison. Keep mesh and angular resolution fixed for this first comparison; do not introduce finer meshes. Record requested NS, effective built NS, build mode, BC sign, retained rotor angles/window, convergence flags, and raw arrays.

| Machine | Requested/effective NS | Expected sector status | Purpose |
|---|---:|---|---|
| 24s28p | 1 | Full ring; no sector BC | Reference |
| 24s28p | 2 | 14 poles/sector; periodic (+1) | Half-ring parity |
| 24s28p | 4 | 7 poles/sector; anti-periodic (−1) | Quarter-ring parity |
| 12s14p | 1 | Full ring; no sector BC | Reference |
| 12s14p | 2 | 7 poles/sector; anti-periodic (−1) | Half-ring parity |
| 12s14p | 4 | Must reject before calibration/mesh | Guard check |

For every valid mode, compare at least no-load, small positive current, small negative current, and the same rated-load point. Compare in this order:

1. Phase/sign winding map at sector boundaries; effective NS, BC sign, build mode, and solver convergence.
2. Raw Maxwell waveform sample by sample on shared rotor angles; Maxwell mean and absolute range in N·m.
3. Selected torque mean and waveform, separately from Maxwell, to expose any selector change; raw ripple in N·m and ripple percent.
4. Full raw harmonic order/amplitude arrays, including every resolved bin; no bin deletion or filtering. Compare harmonics in their shared electrical-order bins and retain unequal/fractional-window resolution explicitly.

Acceptance should be specified before numerical runs: sector and full-ring phase traces must satisfy the signed boundary relation; sampled torque waveforms and means must agree within a tolerance justified by the same-mesh discretization; no-load torque differences must be judged in N·m rather than percent ripple; and ripple/harmonic parity must be judged independently of mean torque. Do not accept mean-only agreement as evidence of waveform or cogging accuracy. Once same-mesh parity is established, a separate later study can vary mesh, slip-ring density, and angular sampling one at a time.

Verification after the audit: `test_sector_symmetry_guard.py`,
`test_p2_projection.py`, and `test_p2_projection_fields.py` passed 69 tests in
4.30 s. These are guard/projection checks; they do not close the numerical
full-ring-versus-sector torque parity gap described above.

## Archived raw-torque parity gate (2026-09-23)

An offline, read-only matched-grid review is implemented in
`scripts/torque_sector_parity_review.py`; its synthetic regression tests are in
`tests/test_torque_sector_parity_review.py`. The CLI reads only the frozen
run16/15/13 archives. It does not invoke the solver or alter archive files.
Source NS4 remains a 60-sample series at shifts 0, 2, ..., 118. The report
records its selected nested indices `[0, 3, ..., 57]` for the shared 20 angles
0, 6, ..., 114; this is a matched-grid diagnostic, not waveform filtering. The
other 40 NS4 source samples remain in the archive but are outside this comparison.
No samples from the matched 20-point series or DFT bins are discarded, and no
finer mesh is used in this first comparison.
The report retains all 20 raw matched torque values per mode and every complex
`rfft/20` coefficient for bins 0..10; DC and Nyquist amplitudes are left
undoubled, and interior single-sided amplitudes are doubled.

The archived data passes the provisional limits encoded by the script.
Full-machine Maxwell results on the shared grid are:

| Mode | Mean (Nm) | Peak-to-peak (Nm) | Max pointwise delta vs NS1 (Nm) | Mean delta (Nm) | Pp delta (Nm) | Max complex-bin delta (Nm) | Max amplitude-bin delta (Nm) |
|---|---:|---:|---:|---:|---:|---:|---:|
| NS1 | 40.254934 | 5.497664 | — | — | — | — | — |
| NS2 | 40.249612 | 5.530193 | 0.041007 | -0.005321 | +0.032530 | 0.009034 | 0.016042 |
| NS4 | 40.245730 | 5.556189 | 0.076880 | -0.009204 | +0.058525 | 0.017536 | 0.028111 |

Both comparisons also have zero measured angle/current mismatch. The NS4
comparison is close to several provisional limits; this pass only describes
these archived samples and the 20-point grid. It does not establish finer
harmonics, continuum/cogging accuracy, or parity for other operating points.

Provenance caveat: these archives were produced at solver parent commit
`ad3d0f12e3bf52c90973002f89ebf2a2d7e7653f`, solver SHA-256
`67b714f726e89a696014d25a59aa593aa41fffc70f04422b19629446548c12e7`.
Commit `909b014` is not the archived solver revision. Inspection of its diff
shows it changed selected torque-mean handling/diagnostics and zero-mean ripple
percent reporting, but not the frame-level raw Maxwell sample computation or
its multiplication by effective NS. Thus the raw archived comparison remains
informative for the sector scaling path, while it is not a rerun of the current
checkout.
