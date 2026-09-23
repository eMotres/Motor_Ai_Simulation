# Terminal-work mean spectral attribution — 2026-09-24

## Result

The full signed-bin Parseval sums reproduce the current production `terminal_work_mean` result for each archived symmetry mode to floating-point precision. The maximum absolute difference is `1.42e−14 N·m` (tolerance `2e−12 N·m`). The arithmetic gate passes; `certified=false` remains explicit because this identity only decomposes the helper's sampled arithmetic.

| Mode | Production mean (N·m) | Parseval sum (N·m) | Parseval minus production (N·m) |
|---|---:|---:|---:|
| NS1 | 40.4959963688813 | 40.4959963688813 | −7.11e−15 |
| NS2 | 40.5037502795759 | 40.5037502795759 | 0 |
| NS4 | 40.5131774314255 | 40.5131774314255 | +1.42e−14 |

The contribution comparison localizes the selected-mean differences to the two signed first electrical-order bins on this sampled grid:

| Candidate vs NS1 | Bin −1 contribution delta (N·m) | Bin +1 contribution delta (N·m) | Sum, all 20 signed bins (N·m) |
|---|---:|---:|---:|
| NS2 | +0.00387695535 | +0.00387695535 | +0.00775391069 |
| NS4 | +0.00859053127 | +0.00859053127 | +0.01718106254 |

In each mode, phases A/B/C contribute respectively `13.5000687285 / 13.4977338993 / 13.4981937411 N·m` for NS1, `13.5053871774 / 13.5000590444 / 13.4983040577 N·m` for NS2, and `13.5110241426 / 13.5034940667 / 13.4986592221 N·m` for NS4. Their all-bin sums match the production totals. All 20 bins, all three phases and each complex term are retained in the JSON; the table above highlights the measured contributors and does not remove the remaining roundoff-level entries.

## Method and interpretation

The script reuses `torque_sector_parity_review.py` to validate the exact common 20 raw samples, NS4 nested indices, angle/current grid, convergence, BC signs, and archive provenance. It then uses the validated NPZ-derived 20-point phase-current and phase-linkage arrays and calls the current production helper. For each real sample series it computes normalized full complex FFT coefficients `X_k = FFT(x)/N`. The effective derivative coefficients come from the production real-grid derivative `D = FFT(real(IFFT(jω FFT(ψ))))/N`. For each phase and every signed bin it forms `C_k = conjugate(I_k) D_k`; summing the bins and phases gives `mean(Σ i·dψ/dθ_m)` by Parseval. Both members of every positive/negative bin pair are shown. `N_parallel=1`, and no extra sector multiplier is applied to the saved terminal linkages.

The sampled current is concentrated at electrical orders `+1` and `−1`, apart from floating-point residual coefficients. Consequently, linkage orders orthogonal to those bins make only roundoff-scale contributions to the cycle mean on this exact grid. These tiny contributions are reported, not filtered. This sampled orthogonality does not establish that the underlying continuous linkage has no other harmonics: source orders separated by multiples of 20 alias onto the same grid bin. A synthetic 21st-order linkage term in the tests aliases exactly to order 1 at `N=20`, and therefore contributes at the same bins as the sampled fundamental.

At the even-grid Nyquist bin 10, the linkage coefficient is retained. Its formal `jω` derivative is purely imaginary for a real cosine input and cannot be represented by the sampled real grid; taking `real(IFFT(...))`, as production does, makes the effective derivative and torque contribution zero. This is the production convention, not bin deletion.

The result isolates **where the NS mean delta appears in the finite sampled calculation**. It does not explain why the archived mode changes the underlying linkage, identify aliased continuous harmonics, validate stored-energy closure, or certify physical torque. The prior selected mean deltas remain `+0.00775391069 N·m` for NS2 and `+0.01718106254 N·m` for NS4 relative to NS1; the separate provisional mean limit of `0.01 N·m` therefore still fails NS4.

## Provenance and reproduction

The source archives and matched-angle validation are the same as in [the sector terminal-work review](torque-terminal-work-sector-audit-2026-09-24.md): G150 24s28p; raw sector modes NS1/NS2/NS4 with boundary signs `+1/+1/−1`; common shifts `0,6,...,114`; NS4 source count 60 and matched indices `0,3,...,57`. The archive predates the current solver source; this is an offline arithmetic decomposition of saved arrays, not a current-source FEM rerun. The CLI output records the current `sb_postproc.py` SHA-256 and archived solver provenance. `passed=true` means only Parseval arithmetic closure; `certified=false` is unconditional.

```powershell
& 'C:\Users\vadim\AppData\Local\Programs\Python\Python311\python.exe' scripts/torque_terminal_work_spectral_review.py
& 'C:\Users\vadim\AppData\Local\Programs\Python\Python311\python.exe' -m pytest tests/test_torque_terminal_work_spectral_review.py --noconftest -o addopts= -p no:cacheprovider -q
```

Synthetic tests cover signed-bin Parseval closure, an independent positive/negative analytic torque sign, per-phase and aggregate deltas, exact aliasing from order 21 to order 1 at `N=20`, an orthogonal order retained with near-zero contribution, the Nyquist convention, and provenance rejection. This archive uses an increasing signed mechanical-angle grid; the test checks its radian derivative sign, not a separate reverse-rotation solve. No FEM, API or configuration was used or changed; no commit was made.
