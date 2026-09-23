# Torque angular-sampling audit (2026-09-23)

This read-only offline audit uses the 60 raw full-machine-scaled torque values
from `scratchpad/torque_period_grid_20260923_run13/ns4/period_grid_review.json`
and checks them against `frame_0.npz` through `frame_59.npz`. It does not run
FEM or modify the archive. The 60-point result is only the finest retained
archive grid; it is not represented as the true or converged torque solution.

The source shifts are 0, 2, ..., 118 (60 points). Nested samples are selected
without interpolation or mutation: N=20 uses source indices 0, 3, ..., 57; N=30
uses 0, 2, ..., 58; N=60 uses every index. Each grid gets its own raw sample
mean, min, max, peak-to-peak range, and complete rFFT. The transform is
`rfft(raw torque)/N`, without demeaning; DC and Nyquist amplitudes are not
doubled, and interior single-sided amplitudes are doubled.

## Measured results

| Samples | Mean (Nm) | Minimum (Nm) | Source index / shift | Maximum (Nm) | Peak-to-peak (Nm) |
|---:|---:|---:|---:|---:|---:|
| 20 | 40.245730 | 37.378510 | 27 / 54 | 42.934699 | 5.556189 |
| 30 | 40.244601 | 37.813347 | 16 / 32 | 42.955031 | 5.141684 |
| 60 | 40.244785 | 37.378510 | 27 / 54 | 42.955031 | 5.576521 |

The means are stable: differences against N=60 are +0.000944 Nm (N=20) and
-0.000184 Nm (N=30), both inside the 0.01 Nm reproducibility gate. Ripple
range does not show monotonic convergence. N=30 misses the minimum by
0.434837 Nm and understates peak-to-peak by 0.434837 Nm (7.80% of the N=60
range). N=20 happens to include the N=60 minimum and is closer in range by
0.020332 Nm, but this accidental agreement does not establish convergence.
The tool therefore reports `mean_reproducibility_passed=true`,
`ripple_sampling_converged=false`, overall `passed=false`, and exits 2. It does
not let a passing mean gate stand in for a ripple gate.

## Aliasing and interpretation

For decimation from 60 points to N points, the normalized complex DFT
coefficient in target bin k is the sum of the 60-point full-complex DFT
coefficients whose indices satisfy `j mod N = k`. Thus 60→20 combines three
source bins per target bin; 60→30 combines two. Examples: target bin 2 maps to
source full-DFT bins `[2,22,42]` for N=20 and `[2,32]` for N=30. Negative
frequencies use ordinary full-DFT indexing and contribute through these same
congruence classes. All rFFT bins are retained for all three sample counts.
The common Nyquist order is 10, but even bins at or below 10 contain aliased
higher source orders unless the waveform is band-limited. Bins above order 10
have no counterpart on the 20-point grid; none of these nested transforms
alone identifies the continuum harmonic spectrum.

The archive identifies effective NS=4 with anti-periodic boundary sign -1,
1680 slip-ring nodes, spacing 0.214286 degrees, and converged flags on all 60
frames. Per-frame scaled torque matches four times `torque_sector_Nm`. The
recorded hashes are solver
`67b714f726e89a696014d25a59aa593aa41fffc70f04422b19629446548c12e7`, config
`f86e751dd57ca0cfa8e9d8dac46620dc723d11e446a4ce962ea5d4be4c6caba8`, and
material library
`80b269b6ab344458409fd7c56ee9e0e84c58fed0af4211f4af14f022d9b80136`.

The computation is implemented in `scripts/torque_angular_sampling_review.py`
with synthetic-only tests in `tests/test_torque_angular_sampling_review.py`.
