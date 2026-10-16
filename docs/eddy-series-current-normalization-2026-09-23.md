# Eddy conductor-current normalization for series windings

The k=1 series and transposed winding cases were imposing different currents
on the same physical conductors. `_coil_con` previously multiplied each
conductor's direction by `n_wires * meshed_area / analytic_slot_area`. That
made a turn's imposed eddy current depend on its triangle area. In series mode,
the path equations instead use a representative body to establish one current
through all turns in that path. Small per-tag area differences therefore made
the old series and transposed constraints inconsistent.

The conductor descriptor now uses `Iunit = direction` in
`src/motor_ai_sim/simulation/fem_solver_2d.py:4048-4086`. The phase current is
already divided by the effective parallel-path count upstream; `wire_split`
adds physical series conductors and does not divide their current. The
area-normalized magnetostatic slot-current density is unchanged. This edits
only the imposed current used for the eddy conductor constraints.

The focused no-FEM test `tests/test_p2_eddy_current_normalization.py` exercises
the actual P2 bordered solve with twelve k=1 series conductors of unequal tag
areas, shows the old area-weighted assignment disagrees with one physical
series path, checks the source mapping, and checks parallel-strand and split
counting. It passed with:

```powershell
& 'C:\Users\vadim\AppData\Local\Programs\Python\Python311\python.exe' tests/test_p2_eddy_current_normalization.py
```

The guarded motor comparison used the saved GEO_30MM fixture, with both
formulations run in fresh one-thread, BELOW_NORMAL Python processes. Inputs
were identical: Ø30 mm geometry, 12 steps over one period, 1.4 mm / 0.35 mm
mesh settings, P2, eddy on, rotor eddy and demagnetization off, 60 A RMS,
15,000 rpm, 2S connection, one parallel path, `wire_parallel=1`,
`wire_split=1`, and the explicitly pinned materials in the run metadata. The
series and transposed runs each completed 14 eddy calls and did not use a warm
seed. Pair wall time was 41.6 s. The raw returned result was saved before
metric extraction for each mode.

| Result | Series | Transposed |
| --- | ---: | ---: |
| AC copper solve loss | 3.277 W | 3.277 W |
| Total copper solve loss | 65.509 W | 65.509 W |
| Mean reported torque | 0.4097107687612754 Nm | 0.4097107687612779 Nm |
| Raw Maxwell mean torque | 0.4074796008778701 Nm | 0.4074796008778667 Nm |
| Torque ripple | 0.3969468581301024% | 0.3969468581322543% |

The saved pre-fix k=1 comparison was 3.240 W AC copper for series versus
3.277 W for transposed. In the corrected comparison the parent independently
checked the raw payloads: phase-current arrays were identical, flux linkage
arrays differed by at most `2.8623e-17 Wb`, raw Maxwell torque by at most
`2.2704e-14 Nm`, and reported mean torque by `2.5535e-15 Nm`. The series path
was exercised on all 14 eddy calls; the maximum reported path-current
conservation error was 0 A. The maximum observed eddy Newton relative residual
was `8.19e-8`.

This A/B result supports consistency for this pinned k=1 fixture; it is not a
general torque validation or an accuracy estimate for other geometries or
windings. No filtering, discarded physics, mesh changes, or tolerance tuning
were used.

Full raw results and run metadata are preserved at:

`C:\Users\vadim\.codex\visualizations\2026\09\16\01a0aaf0-a84f-7d23-9c5b-02df5147e1d6\solver-k1-normalized-20260923\outputs\`

The run used source SHA256
`e96d4571696a3a602a18feb15844c023ea962ace7fbc9c450481b8f6ef34f60d` and
workspace commit `e33d4c3f8407c65473f62aae3ba7e6fd9b0382d5` (working tree
changes were present).
