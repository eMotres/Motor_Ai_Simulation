# Torque angular-sampling audit (2026-09-23)

This is a read-only offline comparison of two archived NS4 runs on the same
G150 24-slot/28-pole mesh: run13 has 60 even slip shifts (0,2,...,118), and
run14 has 60 odd shifts (1,3,...,119). The audit validates both review JSONs
and all 120 frame NPZ metadata rows, then reconstructs the exact shift sequence
0..119. It checks solver/config/material hashes, geometry and mesh counts,
effective NS=4, anti-periodic BC sign -1, angles, slip spacing, strict Newton
flags, residuals, phase-current records, and each raw sector torque multiplied
by four against the full-machine-scaled review torque. The run14 embedded
combined arrays and 120-point mean/range are checked against the independent
merge.

The recorded currents are also checked against the prescribed 60 A RMS
three-phase law at each mechanical angle, with electrical angle equal to
14 times mechanical angle and phase cosines at +60°, −60°, and 180°.

The 120-point array is the finest archived integer-slip-cell grid, not a true
solution or continuum-converged reference. The provisional gate never certifies
continuum or sub-cell angular convergence, even when its checks pass. For
N=20/30/40/60/120, strides are
6/4/3/2/1. Every phase offset 0 through stride−1 is evaluated, so no offset
can hide extrema by favorable alignment. Each phase grid exports every sample
selected by its exact decimation and its complete rFFT; taken together, all
offsets cover all 120 source samples. The source array stays unchanged, and no
samples or harmonic bins are filtered. Each transform is
`rfft(raw full-machine Maxwell torque)/N`, not
demeaned; DC and target Nyquist amplitudes are undoubled, interior amplitudes
are doubled.

## Full 120-point reference grid

Mean torque is **40.247457 Nm**, minimum **37.378510 Nm** at source shift 54,
maximum **42.955031 Nm** at shift 4, and peak-to-peak **5.576521 Nm**. This is
only a reference for sampling sensitivity among archived integer shifts.

## Sensitivity over every phase offset

The provisional gates are max absolute mean difference ≤0.01 Nm, peak-to-peak
difference ≤1% of the 120-grid range, and sampled min/max error ≤0.01 Nm. These
are explicit review thresholds, not validated error bounds. Mean and ripple
are reported and gated separately.

| N | Offsets | Mean range across offsets (Nm) | Max mean error vs 120 (Nm) | Peak-to-peak range across offsets (Nm) | Worst abs p-p error (Nm, %) | Worst sampled extrema error (Nm) |
|---:|---:|---:|---:|---:|---:|---:|
| 20 | 6 | 40.242563–40.251074 | 0.004894 | 5.370384–5.556189 | 0.206136 (3.70%) | 0.133384 |
| 30 | 4 | 40.244601–40.250386 | 0.002929 | 5.141684–5.422223 | 0.434837 (7.80%) | 0.434837 |
| 40 | 3 | 40.246819–40.248367 | 0.000910 | 5.475019–5.556189 | 0.101502 (1.82%) | 0.081078 |
| 60 | 2 | 40.244785–40.250129 | 0.002672 | 5.472383–5.576521 | 0.104138 (1.87%) | 0.057360 |
| 120 | 1 | 40.247457 | 0 | 5.576521 | 0 (0%) | 0 |

All coarse mean offsets pass the provisional mean limit. Every coarse grid has
at least one phase offset outside the peak-to-peak and/or extrema limits, so
the gate reports `mean_reproducibility_passed=true`,
`ripple_sampling_converged=false`, overall `passed=false`, and CLI exit code 2.
N=20 offset 0 includes the 120-grid minimum and looks close in peak-to-peak;
offsets 1–5 demonstrate why that one alignment is not evidence of convergence.
N=30 also misses extrema differently by phase. The 120-grid agreement with
itself is identity, not an independent convergence demonstration.

## Aliasing

Let `X[h]` be the normalized 120-point full complex DFT. For target size N,
stride D=120/N and offset r, the exact decimated coefficient is
`Y[k] = sum(X[h] * exp(+2πi*h*r/120))` over source bins with `h mod N = k`.
The report lists these exact alias groups for every target rFFT bin for each
N=20/30/40/60/120; indices above 60 denote negative-frequency bins in ordinary
full-DFT order. Phase-offset factors matter for complex coefficient sums.
Target Nyquist bins are retained and left undoubled; their alias classes can
combine multiple signed source orders. The common Nyquist order across the
coarse grids is 10. Bins beyond order 10 must not be read as the same physical
harmonics across grids, and even bins at or below that order contain aliases
unless the source is band-limited.

Both archives carry matching solver SHA-256
`67b714f726e89a696014d25a59aa593aa41fffc70f04422b19629446548c12e7`, config
SHA-256 `f86e751dd57ca0cfa8e9d8dac46620dc723d11e446a4ce962ea5d4be4c6caba8`,
material-library SHA-256
`80b269b6ab344458409fd7c56ee9e0e84c58fed0af4211f4af14f022d9b80136`, 28,661
DOFs, 13,734 elements, and 1,680 slip-ring nodes. The calculation is
implemented by `scripts/torque_angular_sampling_review.py`; synthetic tests
are in `tests/test_torque_angular_sampling_review.py`.
