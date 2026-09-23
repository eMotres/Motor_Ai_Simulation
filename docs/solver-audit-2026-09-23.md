# Solver audit checkpoint — 2026-09-23

Source revision: d943b1b1c4cf8de4e3e4afba5edffe858793f637.
Read-only solver audit; no live API access, restart, configuration writes or FEM runs.

Scope note: the paragraphs below describe the initial checkpoint. Subsequent
isolated FEM probes, full-P2 capture and angular-sampling evidence are recorded
in `torque-motor-comparison-2026-09-23.md`. They do not yet certify the
production mean formula or full/half/quarter symmetry convergence.

## Reproduced limitations

Executed the actual `sb_postproc.hybrid_torque` helper via importlib, using
720 uniformly spaced samples and the analytic example already documented in
`solver-torque-validation-plan.md`. Seven pole pairs, PM flux components
0.02*cos(x)+0.002*cos(5*x), current -10*sin(x)-2*sin(5*x).
Fixed-current virtual work gives 2.3100000000000005 N m; the helper returns
2.058 N m, a -10.9091% difference. This is an analytic counterexample to the
general formula, NOT an estimate of error on the owner's motor.

With sinusoidal 0.02 Wb linkage and a deliberately zero synthetic Maxwell
input, branch-current peaks 0.999 A and 1.000 A return 0 N m with
`maxwell_stress`; 1.001 A returns 0.21021 N m with
`energy_mean+maxwell_ripple`. This isolates selector discontinuity; the zero
Maxwell input is a test fixture, not a physically solved field.

The loaded waveform retains Maxwell AC but replaces its DC. Absolute
peak-to-peak ripple is therefore unchanged by this shift, while percentage
ripple and mechanical power depend on the selected mean. Removing the 1 A
threshold alone cannot repair the missing coenergy-angle terms.

## Remaining work and evidence limits

The 2026-09-16 symmetry results are historical evidence, not a validation of
this revision. Full/half/quarter equality must be distinguished from mesh and
angular convergence. No new motor convergence study was run today.

Warm-up samples are still trimmed by `drop_settling_frames` at the two call
sites in fem_solver_2d.py. The raw FFT policy preserves the retained window;
it does not mean every internally computed warm-up sample is returned.
This needs explicit treatment against the owner's no-discard requirement.

Next physics gate: independently validated discrete virtual work and periodic
port-energy balance before changing the production mean formula. Do not tune
the answer or filter torque harmonics to make agreement appear better.

Execution: initial Python command unavailable on PATH; explicit Python was
blocked by sandbox permissions, then the same read-only check passed with
approved execution. No full test suite was launched.
