# NS1 full-ring matched-period comparison — 2026-09-23

One isolated P2 run compared the full-ring (`n_sectors=1`) template solution
with the existing NS2 half-sector run15 and NS4 quarter-sector run13 at the
same 20 full-electrical-period angles. It sampled slip shifts `0,6,…,114` on
the 1680-node ring (`0.214285714286°` spacing), covering one endpoint-excluded
period for 14 pole pairs. Mechanical sample angles were `0, 1.285714…, …,
24.428571°`; the excluded endpoint is `25.714286°`. The imposed balanced
current was `60√2 A` peak with electrical phase `14θ_mech + 60°` (60 A RMS
per phase). No filtering or torque smoothing was used.

Run16 reused the same explicit 24-slot/28-pole, 150 mm template geometry,
B15AHV950M steel and F45SH_120C magnets, 4.0/0.3 mm mesh-size inputs,
structured gap, one gap layer, 2S connection, `n_parallel=1`, 15000 rpm, and
no eddy, rotor eddy or demagnetization. `geo_mesh=False`. Solver SHA-256 was
`67b714f726e89a696014d25a59aa593aa41fffc70f04422b19629446548c12e7`, config
SHA-256 `f86e751dd57ca0cfa8e9d8dac46620dc723d11e446a4ce962ea5d4be4c6caba8`,
and material-library SHA-256
`80b269b6ab344458409fd7c56ee9e0e84c58fed0af4211f4af14f022d9b80136`. The
source config hash matched its scratch copy before the run and was rechecked
unchanged after exit. Git HEAD was
`ad3d0f12e3bf52c90973002f89ebf2a2d7e7653f`.

| Same 20 angles | NS1 full ring | NS2 half sector | NS4 quarter sector |
|---|---:|---:|---:|
| Sampled mean torque | 40.2549339 N·m | 40.2496124 N·m | 40.2457296 N·m |
| Sampled minimum | 37.4553903 N·m | 37.4143837 N·m | 37.3785102 N·m |
| Sampled maximum | 42.9530539 N·m | 42.9445769 N·m | 42.9346990 N·m |
| Sampled peak-to-peak | 5.4976636 N·m | 5.5301932 N·m | 5.5561888 N·m |

These are sector-scaled Maxwell callback values on the shared sample grid.
The NS1 mean differs from NS4 by `0.00920432 N·m` (`0.02287%` of the NS4
mean), and from NS2 by `0.00532149 N·m`. Maximum pointwise torque difference
was `0.0768801 N·m` (`0.20568%` relative) versus NS4 and `0.0410066 N·m`
(`0.10960%`) versus NS2. Maximum phase-flux difference was `3.00723e-5 Wb`
(`0.12700%`) versus NS4 and `1.32834e-5 Wb` (`0.05610%`) versus NS2. All 20
Newton steps were accepted; maximum residual was `8.9937e-8`.

The actual ring remained 1680 nodes with `0.214285714286°` slip spacing for
all sectors. NS1 contained 54,096 elements and 112,156 P2 DOFs; NS2 had 27,468
elements and 57,210 DOFs; NS4 had 13,734 elements and 28,661 DOFs. Geometry
and mesh-size inputs match, but sector meshes are distinct discrete problems.
The NS1 child exited zero in `194.39 s` wall / `192.73 s` CPU, below the
240-second cap, at one thread and BelowNormal priority. PID 38020 was confirmed
absent after exit. Raw A and scalar data were saved for all 20 frames; full
quadrature B, mesh, tags and per-element material arrays were retained at
frames 0, 1, 10 and 19.

Geometry sanitization reported coincident-point merges in rotor, magnets,
stator and outer band, and dropped the degenerate one-point `rotor[1]` ring at
`(-39.8101,-39.8101)`. No source, shared config, material or production solver
file was changed. Runner: `scratchpad/torque_full_period_probe_20260923.py`;
raw output, run log, provenance and comparison are under
`scratchpad/torque_full_period_20260923_run16/ns1/`.

This is a comparison of three sector meshes on one 20-angle sampling grid, not
a continuum or ripple convergence result. The existing NS4 20/30/60-point
study shows that nested-grid means can be close while the 30-point grid misses
a sampled trough by `0.4348 N·m`. The 20-point sampled extrema and peak-to-peak
values above therefore do not certify the true ripple. The analytic/template
`geo_mesh=False` setup is not a CAD-fidelity comparison. No finer spatial mesh
was run.
