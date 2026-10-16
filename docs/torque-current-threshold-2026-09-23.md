# Audit: 1 A selector in hybrid torque

This is a bounded source and synthetic-helper audit. It is not a motor result,
does not validate Maxwell stress, and does not select a replacement torque
method. No solver or live API was run.

## What switches and where it goes

`src/motor_ai_sim/simulation/sb_postproc.py:91-149` checks peak absolute
**per-branch** phase current and selects the space-vector mean only when that
peak is strictly greater than 1 A, the A-phase flux array is nonempty, and its
length matches the A-phase current array. At 1 A and below it returns the raw
Maxwell series. Above 1 A it keeps the raw Maxwell AC component and replaces
its DC mean with the space-vector mean. `n_parallel` multiplies that candidate
mean. This threshold is not a physical eligibility criterion.

The helper is called by `src/motor_ai_sim/simulation/fem_solver_2d.py:7345`.
The solver exposes selected `T_em_Nm`/`T_avg_Nm`, raw Maxwell
`T_em_maxwell_Nm`/`T_avg_maxwell_Nm`, and `torque_method` around lines
7841-7844. Solver-trial diagnostics record both candidate means and the method
(`scripts/solver_trials.py:323-330`). The simulation chart consumes the chosen
waveform/mean (`web/src/components/simulation/TransientCharts.tsx:925, 1210,
1387-1397`); it does not show method uncertainty. Reports likewise plot the
selected torque series. The distinct 1 N·m display condition at chart line 1387
is a UI ripple-format choice, not this 1 A selector.

## Reproduction

Run `scratchpad/torque_current_threshold_20260923.py` with Python 3.11. It
loads only the production helper directly and uses a one-harmonic analytic
three-phase PM linkage/current pair. Its independent frozen-current coenergy
derivative is `n_parallel * sum(i_branch * d(psi_branch)/d(theta_mech))`.
The Maxwell-like series is set to that exact analytic torque plus a synthetic
0.25 N·m DC stress offset and 0.02 N·m sinusoidal ripple. The injected offset
is deliberately artificial and is not an estimate of real Maxwell error.

For one parallel path, exact mean is 0.21000 N·m at 1.000 A and 0.21021 N·m
at 1.001 A. The selected means are 0.46000 and 0.21021 N·m respectively, a
0.24979 N·m jump over 0.001 A. With two parallel paths the analytic means are
0.42000 and 0.42042 N·m, while selected means are 0.67000 and 0.42042 N·m:
a 0.24958 N·m jump. At 0 and 0.999 A the method is Maxwell; at 1.000 A it is
still Maxwell; at 1.001 A it switches. The absolute peak-to-peak ripple stays
0.04000 N·m because the helper preserves the same AC waveform. The zero-current
case returns the injected 0.25 N·m offset by construction; it is not evidence
of cogging or a no-load torque.

## Safe interpretation and next step

Changing `> 1.0` to `> 0`, removing the cutoff, or always choosing Maxwell
would merely make one candidate universal without establishing its validity.
The helper's own docstring limits the space-vector mean to rotationally
covariant sinusoidal-winding dq. Existing `tests/test_torque_energy_validation.py`
shows why that scope matters: for its analytic fifth-spatial-harmonic case the
fixed-current/coenergy derivative mean is 2.31 N·m, while the helper returns
2.058 N·m. This is a constructed counterexample, not a motor error estimate.
Zero current also does not eliminate PM cogging, and transient energy methods
need stored-energy and loss terms when eddy currents or irreversible changes
are present.

Therefore the smallest defensible next implementation is **no numerical
selector change yet**. First validate one unified mean-torque method against
independent virtual work over low/no-load and loaded states, including spatial
harmonics/cogging, nonlinear material behavior, parallel scaling, and relevant
loss or demagnetization modes. Until those gates pass, retain both candidate
series/means and make their method/eligibility uncertainty explicit in the
solver diagnostics; do not silently promote either estimate or trim waveform
data. This audit makes no UI proposal and claims no torque fix.

Model: Codex Luna; no escalation. Standalone reproduction passed; no FEM,
API, configuration, or production source was changed.
