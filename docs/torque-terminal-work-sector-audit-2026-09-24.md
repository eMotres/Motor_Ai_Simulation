# Terminal-work sector sensitivity on archived parity grid — 2026-09-24

## Result

The current `sb_postproc.terminal_work_mean` helper was applied directly to the validated archived phase-current, phase-linkage and actual mechanical-angle arrays from NS1, NS2 and NS4. All 20 common samples and all 20 full-DFT bins per signal/phase are retained in the JSON output. The raw NS4 archive remains 60 samples; matched indices are `[0, 3, 6, ..., 57]`, mapping shifts `[0, 6, 12, ..., 114]`. There is no waveform filtering, bin deletion, or mesh refinement.

The provisional terminal-work mean comparison **fails for NS4** against the `0.01 N·m` mean-delta limit reused from the raw torque parity gate. NS2 passes this mean-only check. This is archived same-grid sensitivity only; it does not certify terminal-work energy closure, alias-free ripple, continuum torque, or another operating point.

| Mode | `terminal_work_mean` (N·m) | Raw Maxwell mean, diagnostic (N·m) | Terminal minus Maxwell (N·m) | Terminal delta vs NS1 (N·m) | Provisional mean gate |
|---|---:|---:|---:|---:|---|
| NS1 | 40.49599637 | 40.25493388 | +0.24106249 | — | reference |
| NS2 | 40.50375028 | 40.24961239 | +0.25413789 | +0.00775391 | pass |
| NS4 | 40.51317743 | 40.24572955 | +0.26744788 | +0.01718106 | **fail** |

The underlying raw Maxwell parity review passed its own provisional same-grid gates. This separate terminal-work calculation therefore exposes a larger sector-mode mean sensitivity than the raw Maxwell means alone show. It uses the same 24-slot/28-pole G150 archived case at 60 A RMS, 15,000 rpm and 20 common angles. Maximum current and angle mismatches are zero in the recorded matched arrays. The source review records effective NS and boundary signs as NS1 `+1`, NS2 `+1` and NS4 `−1`.

## What was evaluated

The tool first calls `scripts/torque_sector_parity_review.py` to validate the immutable archive metadata, run parameters, material/config/solver hashes, matched shifts, convergence, current law, angle grid, BC sign and sector scaling of the raw Maxwell series. It then loads the actual frame NPZ `currents_A`, `psi_Wb`, `angle_deg`, `torque_sector_Nm` and `bc_sign` values and checks the phase currents, linkages and angles against the corresponding review JSON before evaluation.

The terminal linkage is passed unchanged into the production helper with `pole_pairs=14` and `n_parallel=1`. The solver's linkage convention is `stack_length * effective_NS / n_parallel` times the winding source pairing (`fem_solver_2d.py:5277`); no extra sector multiplier is applied in this review. The helper differentiates on signed mechanical radians and computes `n_parallel * mean(Σ i·dψ/dθ_m)` (`sb_postproc.py:212-274`); it does not introduce another pole-pair multiplier. The captured frame NPZs do not contain per-phase coil source vectors, so this task checks raw linkage identity against review records and applies the documented convention, but does not independently recompute `A·f_coil` for every frame.

For each phase and mode, the output retains the original 20 current/linkage values, 20 derivative samples, and every full complex DFT bin `0..19` for current, linkage, derivative-operator output, and the effective real-grid derivative. No mean is subtracted. At the even-grid Nyquist bin, the spectral multiplier produces an imaginary derivative coefficient that is not representable on the real sample grid; as in production, taking `real(ifft(...))` yields zero effective real Nyquist derivative. The synthetic test includes an explicit Nyquist input and verifies this convention against the production helper.

## Provenance and limits

The reused raw parity validator identifies solver parent `ad3d0f12e3bf52c90973002f89ebf2a2d7e7653f`, archived solver SHA-256 `67b714f726e89a696014d25a59aa593aa41fffc70f04422b19629446548c12e7`, config SHA-256 `f86e751dd57ca0cfa8e9d8dac46620dc723d11e446a4ce962ea5d4be4c6caba8`, and material library SHA-256 `80b269b6ab344458409fd7c56ee9e0e84c58fed0af4211f4af14f022d9b80136`. This is not a rerun of the current FEM solver; the parity review documents that the archive predates `909b014`, whose changes did not alter the raw per-frame Maxwell sample calculation or its effective-NS multiplier. The production helper actually called in this review is the current `sb_postproc.py`, SHA-256 recorded in the output JSON.

The selector's numeric result is directly reproducible from these archived samples, but the terminal-linkage data were not regenerated under the current solver source. The 20-angle grid has Nyquist order 10; higher orders alias into its bins, including bins at or below order 10. The `0.01 N·m` limit is provisional and applies only to the difference in sampled selected means. The failed NS4 comparison is not a certified error estimate. It does not justify a production selector change by itself.

## Reproduction

```powershell
& 'C:\Users\vadim\AppData\Local\Programs\Python\Python311\python.exe' scripts/torque_terminal_work_sector_review.py
& 'C:\Users\vadim\AppData\Local\Programs\Python\Python311\python.exe' -m pytest tests/test_torque_terminal_work_sector_review.py --noconftest -o addopts= -p no:cacheprovider -q
```

The CLI emits JSON and returns nonzero because the NS4 mean delta exceeds the provisional limit. The focused synthetic tests passed. No FEM, API, configuration change or commit was made.
