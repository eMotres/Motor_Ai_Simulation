# NS2 half-period-sector full-electrical-period check — 2026-09-23

One isolated P2 run compared the half-sector (`n_sectors=2`) template solution
to the existing NS4 quarter-sector period grid at 20 common angles. It sampled
slip shifts `0,6,…,114` on the same 1680-node ring (`0.214285714286°` per slip
cell), covering one endpoint-excluded 360° electrical period for 14 pole pairs.
Mechanical angles were `0, 1.285714…, …, 24.428571°`; the endpoint is
25.714286°. The prescribed balanced current was `60√2 A` peak with electrical
phase `14θ_mech + 60°` (60 A RMS per phase). No waveform filtering or torque
smoothing was used.

The run reused the exact run12–14 template geometry, B15AHV950M steel and
F45SH_120C magnet assignments, 4.0/0.3 mm mesh-size inputs, one thread, P2,
15000 rpm, 2S, `n_parallel=1`, eddy off, rotor eddy off and demagnetization
off. `geo_mesh=False`. Current solver SHA-256 was
`67b714f726e89a696014d25a59aa593aa41fffc70f04422b19629446548c12e7`, config
SHA-256 `f86e751dd57ca0cfa8e9d8dac46620dc723d11e446a4ce962ea5d4be4c6caba8`,
and material-library SHA-256
`80b269b6ab344458409fd7c56ee9e0e84c58fed0af4211f4af14f022d9b80136`. The
copied config hash matched the source before the solve; a post-run source hash
check also matched. Git HEAD was
`ad3d0f12e3bf52c90973002f89ebf2a2d7e7653f`.

| Same 20 angles | NS2 half-sector | NS4 quarter-sector |
|---|---:|---:|
| Sampled mean torque | 40.2496124 N·m | 40.2457296 N·m |
| Sampled minimum | 37.4143837 N·m | 37.3785102 N·m |
| Sampled maximum | 42.9445769 N·m | 42.9346990 N·m |
| Sampled peak-to-peak | 5.5301932 N·m | 5.5561888 N·m |

The NS2 sampled mean is higher by `0.00388284 N·m` (`0.00965%` of the NS4
mean). Across the matched points, maximum sector-scaled torque difference is
`0.0358735 N·m` (`0.09597%` relative); maximum phase-flux difference is
`1.67889e-5 Wb` (`0.07090%` relative). All 20 Newton steps were accepted;
maximum residual was `9.3971e-8`. The NS2 mesh contained 27,468 elements and
57,210 P2 DOFs, versus 13,734 elements and 28,661 DOFs for run13 NS4. Both
reported the same 1680-node slip ring and actual slip spacing. These remain
different discrete meshes despite identical geometry and mesh-size inputs.

The half-sector child exited zero in `102.21 s` wall / `100.39 s` CPU under
the 180 s limit, one thread at BelowNormal priority; PID 21124 was confirmed
absent after exit. The scratch audit recorded each frame's scalar data and an
emergency A vector before postchecks. Full P2 quadrature B, mesh and per-element
material arrays were retained at frames 0, 1, 10 and 19. Geometry sanitization
reported merged coincident points in the rotor, magnets, stator and outer band,
and dropped a degenerate one-point `rotor[1]` ring at `(-39.8101,-39.8101)`.
The source config hash was rechecked after exit and remained unchanged. No
source/config/material files were changed.

The comparison is limited to the same **20 sampled angles**. The existing NS4
study shows why this cannot establish ripple convergence: its 30-point grid
missed a trough that appeared on the 60-point grid, and the 20-point grid also
does not reproduce that trough exactly. The close sampled means and pointwise
values therefore support only a same-grid NS2/NS4 comparison; they are not a
continuum-error bound, a ripple certification or evidence for NS1/full-sector
agreement. The geometry is the analytic/template `geo_mesh=False` route, not a
CAD-fidelity comparison.

Runner: `scratchpad/torque_half_period_probe_20260923.py`. Raw frames,
provenance and matched-grid review are under
`scratchpad/torque_half_period_20260923_run15/ns2/`.
