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
