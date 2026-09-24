# P2 virtual-work torque diagnostic

The P2 transient now records `T_em_virtual_work_diagnostic_Nm` beside the
existing torque output. The existing `T_em_Nm`, mean torque, ripple, torque
selector, and regression baseline are unchanged. This new series is explicitly
**uncertified** and must not be presented as a validated machine torque.

For an eligible solved frame, the calculation is

\[
T_{vw}=-N_s L\,[K_{pw}(A)A-f]^T\frac{dA}{d\theta_m}.
\]

Here `Kpw` is the pointwise nonlinear stiffness at the final accepted field,
`f` is that frame's imposed-current magnetic and coil source, `N_s` is the
number of equivalent sectors, `L` is active stack length in metres, and
`theta_m` is **mechanical radians**. The derivative uses the sparse L2 rotor
trace mass solve at the frame's integer slip shift. No coefficient is filtered
or truncated, and no mesh or production slip constraint is changed.

Only imposed-current, magnetostatic, non-demagnetising, non-frozen frames with
an accepted pointwise Newton solve are evaluated. All others record `null` in
the torque series and a matching reason in
`virtual_work_diagnostics.frame_reasons`. A diagnostic mean is returned only
when every reported frame has a value. Both per-frame arrays undergo exactly
the same voltage/demag settling trims as the Maxwell torque series; the scalar
history snapshot also retains the virtual-work values before trimming.

The trace derivative is that of a continuous-angle **L2 mortar**, whereas the
field itself is still solved using the existing integer-shift nodal slip
projection. Their constrained spaces coincide at integer shifts, but equality
of the derivative of the full discrete magnetic energy has not been verified
on the production mesh. The polygonal band geometry is fixed, so geometric
shape derivatives of curved rotor/stator domains are outside this diagnostic.
The nonlinear material energy consistency, magnet source motion, sign against
measurement or a fully independent energy calculation, and convergence under
mesh refinement still need validation. In particular, a discrepancy with
Maxwell stress does not by itself establish which torque is correct.

Unit verification is deliberately small and does not run FEM: a fake pointwise
operator checks residual sign and sector/stack scale; AST guards check frame
alignment, trim participation, and scalar-history registration; existing
projection and quadratic-energy oracle tests check the isolated L2 derivative.

## First production-mesh observation

A four-frame run of the existing `tests/test_airgap_mean_b.py` 30 mm fixture
was made after the small tests.  It used the fixture's unchanged settings:
60 A rms, 15,000 rpm, two sectors, 10 mm active length, B15AHV950M iron,
F45SH at 120 °C, 1.4/0.35 mm mesh controls, one structured gap layer, current
drive, no eddy current and no demagnetisation.  All four frames completed by
the eligible pointwise-Newton path.

| quantity | Maxwell | virtual work diagnostic |
| --- | ---: | ---: |
| mean torque | 0.40893450 Nm | 0.41036554 Nm |
| peak-to-peak over four frames | 0.00164635 Nm | 0.00163339 Nm |

The diagnostic mean is 0.34995% above Maxwell; the largest per-frame absolute
difference is 0.00143753 Nm.  Both series have the same sign and nearly the
same four-sample variation.  This is encouraging integration evidence, not a
certification: four angular samples cannot validate torque harmonics or
cogging, and both calculations share the same FEM field and polygonal mesh.

## Raw angular sampling of cogging torque

On the existing 12-slot, 14-pole no-load fixture, the measured peak-to-peak
torque depends strongly on the **number of solved rotor angles**:

| Frames/electrical period | Raw samples/cogging cycle | Maxwell pp (N·m) | Virtual-work pp (N·m) |
| ---: | ---: | ---: | ---: |
| 12 | 1 | 0.0000533965 | 0.0000906583 |
| 24 | 2 | 0.00645013 | 0.00648410 |
| 48 | 4 | 0.00982333 | 0.00987170 |
| 72 | 6 | 0.00978534 | 0.00984232 |

The 48-to-72 changes are about 0.39% (Maxwell) and 0.30% (virtual work),
whereas 12 or 24 frames severely alias the waveform. This is one fixture,
not a mesh-convergence or experimental validation. The solver now requires at
least six **raw** samples per cogging cycle, where cycles per electrical period
equal `lcm(num_slots, num_poles) / pole_pairs`. Both 12s14p and 24s28p have
12 cycles and therefore need at least 72 frames per electrical period.

For the whole-node band, the actual snapped step count is raised only to the
smallest divisor of the **existing** slip-ring nodes per electrical period
that meets this minimum. The mesh is never refined for this policy. The
continuous-angle macro branch (currently unsupported by the P2 field solve)
selects the exact minimum. If the existing ring
cannot meet it, the solver keeps every available sample and reports an
explicit insufficient flag and reason. No torque samples or Fourier orders
are filtered or discarded. The private d-axis calibration solve is exempt:
it samples flux linkage to locate an angle, and changing its sample grid can
change its peak-ambiguity gate; it is not a reported cogging-torque run.

The complete pinned physics baseline was then regenerated once with the new
policy. All eight cases completed, and the voltage-drive circuit residuals
remained below `5.5e-14 V`. The principal before/after changes are:

| case | torque ripple before | torque ripple at 72 frames | mean torque change |
| --- | ---: | ---: | ---: |
| current load | 0.4047% | 3.7804% | +0.12% |
| eddy current load | 0.3856% | 3.8320% | -0.24% |
| demag | 0.5988% | 4.1200% | -0.76% |
| demag + eddy | 0.6117% | 4.0913% | -0.81% |
| voltage | 1.5089% | 2.6750% | -2.56% |
| voltage + eddy | 1.6121% | 2.8075% | -2.70% |
| voltage + eddy rotor | 1.5746% | 2.7856% | -2.77% |

At no load the raw Maxwell peak-to-peak cogging torque changed from
`0.00005340 N·m` to `0.00978534 N·m`. The higher angular resolution also
changed quantities derived from temporal harmonics: across the pinned cases
iron loss increased by roughly 14.5-18.4%, magnet eddy loss by up to 24.6%,
and AC copper loss by roughly 7.3-9.7%. In demagnetising runs the area-weighted
mean retained Br fell by about 3.6% and the minimum by about 10.4%, because the
additional rotor positions expose field extrema that the 12-frame grid never
visited. These are intended physics changes from retaining more raw states,
not a change to material laws, spatial mesh, or post-processing filters.
