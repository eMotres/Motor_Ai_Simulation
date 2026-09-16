# Torque model limitations and validation plan

Status: the general harmonic/energy torque limitation and the legacy 1 A selector remain **unresolved**. The documentation correction does not change the numerical formula, selector, returned method strings or existing reference results. Geometry caching is a separate performance change and does not fix these physics limitations.

## Present model and counterexample

`simulation.sb_postproc.hybrid_torque` selects

    mean(T) = (3/2)*p*n_parallel*mean(psi_alpha*i_beta - psi_beta*i_alpha)

when peak per-branch current exceeds 1 A and its existing terminal-data check passes. It shifts the raw Maxwell waveform to that mean. The AC retained from Maxwell can contain physical ripple and discretization artifacts; convergence must be established independently. This is a fundamental space-vector approximation to the general FEM problem. It is a torque identity in a rotationally covariant sinusoidal-winding dq model, which can accommodate arbitrary temporal currents. Explicit spatial harmonics and cogging require additional coenergy-angle terms.

For three phases with offsets `phi_k = 0, +/-2*pi/3`, electrical angle `x_k = theta_e - phi_k`, and seven pole pairs, prescribe position-dependent PM flux and currents:

    psi_k = .02*cos(x_k) + .002*cos(5*x_k)
    i_k   = -10*sin(x_k) - 2*sin(5*x_k)

Fixed-current virtual work gives the mean

    p*sum_k mean(i_k*d(psi_k)/d(theta_e))
      = (3/2)*7*(.02*10 + 5*.002*2) = 2.310 N m.

The fifth harmonic becomes negative sequence in the Clarke plane, so the present cross product instead gives `(3/2)*7*(.02*10 - .002*2) = 2.058 N m`. The missing spatial-derivative weight is a model limitation, not numerical noise.

The 1 A cutoff has no physical conservation basis and acts on branch current, so changing parallel branches can change the selected method at the same phase current. Replacing it with another threshold, selecting the space-vector mean for every nonzero current, or blending Maxwell and energy estimates does not supply a validated cogging/eddy-drag model.

## Energy balance and general implementation

For a lossless, hysteresis-free magnetic subsystem at fixed irreversible magnet state,

    sum_k i_k*d(psi_k)/dt = dW_m/dt + T*omega_m.

A truly periodic magnetic state at constant nonzero speed therefore permits a mean torque from terminal flux work. This includes reversible saturation, but total flux differentiation is not an instantaneous torque formula: it also measures magnetic storage changes. These conditions follow from [MIT's lossless MQS energy/coenergy principle](https://web.mit.edu/6.013_book/www/chapter11/11.7.html).

A blind spectral derivative replacement is not universal. Eddy-current winding redistribution requires conjugate port voltage/current and solved Joule-loss accounting; demagnetization and unsettled windows can change stored/internal energy. Aliased harmonics, noninteger periods, nonuniform time samples and zero speed need explicit handling. Zero terminal work cannot provide a cogging waveform or by itself identify no-load braking torque. Solved conductor losses and separately postprocessed iron losses must be distinguished; see [COMSOL's motor power-balance verification](https://www.comsol.com/blogs/computing-loss-temperature-and-efficiency-in-electric-motors/).

The target is `T = partial W'_m/partial theta_m` at fixed instantaneous currents and irreversible material state. Nonlinear magnetic energy uses `integral H dB`; coenergy is its Legendre counterpart. Substituting `0.5*A.T*K(A)*A` for nonlinear energy is incorrect. Primary descriptions are the [COMSOL energy/coenergy theory](https://doc.comsol.com/6.4/doc/com.comsol.help.acdc/acdc_ug_theory.05.79.html) and [FEMM constant-current coenergy displacement procedure](https://www.femm.info/doku/lib/exe/fetch.php?media=upload%3Afiles%3Amanual.pdf).

The solver has P2 fields/quadrature, B-H data, PM sources and conductor currents. Its missing component is differentiable motion consistent with the discrete field problem. `SlipProjection.build` performs integer signed welds; its reduced column identities can change, so `P(m+1)*a` and `P(m-1)*a` with the same reduced vector are not a valid virtual displacement.

Develop continuous-angle P2 air-gap/mortar coupling with fixed master coordinates and derivative `P'`. For `A=P(theta)*a`, the stationary magnetic potential is

    Phi(a,theta) = integral w(curl(A)) dOmega - f_PM.T*A - f_J.T*A.

With all explicit angle dependence correctly represented in P, the envelope derivative gives `T = -partial Phi/partial theta|a = -r.T*P'*a`, scaled by stack length and sector count. Validate its sign and mechanical-radian units. An analytic annulus alternative uses `-0.5*a_gap.T*K'_gap*a_gap`. The older P1 macroelement offers reference algebra, but that mode is not implemented on production P2. [FEMM's rotor-motion model](https://www.femm.info/doku/doku.php?id=rotormotion) illustrates continuous coupling with differentiable interpolation.

During an eddy snapshot perturbation, freeze instantaneous physical current distribution and irreversible magnet state; do not replay time integration or demagnetization. The sigma/dt mass term is not magnetic stored energy. An air-gap energy shape derivative equivalent to Arkkio with the same weighting can retain the same gap-field errors; relabeling the integral does not repair them. Historical fixed-bias claims also need current evidence: `SOLVER_TRIALS_2026-07-30.md` attributes F4/F5 discrepancies to a coil-source normalization bug fixed after the July 23 torque-methodology memory entry.

## Validated integer-sector coupling correction (2026-09-16)

`SlipProjection.build` previously inferred a shifted edge from its vertex
representatives. At a sector wrap, the duplicate cut endpoint caused one P2
midpoint weld to be omitted. Actual generated periodic and antiperiodic motor
meshes confirmed one extra independent DOF and a discontinuous interface trace.
Mapping the interval with `divmod(a + m, Nring - 1)` and the sector wrap sign
restores the complete quadratic trace. Full-ring projections and zero-shift
sector projections are unchanged. This corrects integer coupling; it does not
implement differentiable motion or replace the torque model above.

The independent annular Laplace tests in `tests/test_p2_projection_fields.py`
recover an exactly representable quadratic field to roundoff (previous relative
energy error 1.0267%) and demonstrate field/gradient convergence for periodic
fourth-order and antiperiodic sixth-order harmonic fields. The companion
projection tests check all radial-cut, vertex and midpoint constraints.

Cold motor A/B comparisons found identical full-ring metrics and sector loaded
mean torque changes of about -2.73e-7 N m. The coarse `p2_noload` reference needs
exactly two corrected values: mean 4.25362258440225e-5 ->
4.1481998567548255e-5 N m, ripple 143.7852690020971 -> 128.72201908278132%.
The original implementation reproduces the former values; all other reference
values and regression tolerances are retained.

This 12-step no-load case is a reproducibility check, not a cogging-accuracy
reference. On the identical mesh, 48 steps resolve a 12th electrical harmonic
of amplitude 0.0026128 N m that aliases into DC at 12 steps. Peak-to-peak raw
torque changes from 5.33965e-5 to 0.00982333 N m when sampling is refined.
These two resolutions do not establish converged cogging accuracy; percentage
ripple relative to a near-zero mean is especially sensitive.

## Required validation gates

1. Exact analytic harmonic examples, including the 2.310 N m case, fundamental limit, fifth/seventh harmonics, sequence/sign reversal, zero sequence and parallel-branch scaling.
2. Conservative nonlinear and position-dependent-inductance toy models: fixed-current coenergy derivative, correct instantaneous/storage distinction and periodic mean power agreement.
3. Continuous P2 coupling: stable reduced coordinates, integer-shift equivalence, midpoint traces, anti-periodic wrap, full-ring/sector parity, P' versus central differences and mesh/angle convergence.
4. Genuine no-load cogging and positive/negative small-current sweeps across zero and the old 1 A boundary. Verify lossless cogging mean convergence without an arbitrary blend.
5. Eddy virtual work versus independent port work, solved Joule loss and storage balance; time-step convergence, no-load braking sign and series-path current sharing. Keep empirical iron-loss treatment separate.
6. Preserve validated sinusoidal operating points within demonstrated numerical accuracy; use independent reference cases rather than tuning a mean to a reference.

An immediate bounded addition could expose model/branch diagnostics and a separate terminal-work diagnostic for eligible periodic lossless trajectories. Numerical promotion of that diagnostic or removal of the selector requires the relevant gates above; it must not silently extend to eddy, demag or unresolved PWM regimes.
