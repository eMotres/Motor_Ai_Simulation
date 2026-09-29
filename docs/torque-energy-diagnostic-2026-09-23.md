# Periodic terminal flux work diagnostic — 2026-09-23

Added a pure, standalone helper that reports the periodic line integral
`sum(i dψ) / Δθ` as mean terminal flux work per mechanical radian. It is an
eligibility-gated diagnostic only; it is not a validated motor torque, a
replacement for the reported torque, or a no-load cogging estimate. With zero
current, the integral contains no cogging work, so the helper rejects such
inputs.

The helper requires caller-certified periodicity, settling, and conservative
lossless behavior; imposed-current drive; finite nonzero speed; positive
integer pole-pair and electrical-period counts; uniform angle samples spanning
that signed integer-period window; matching finite phase-current and flux
arrays; and at least 16 even samples whose full-vs-half-resolution periodic
trapezoids differ by less than the configured coarsening-sensitivity limit.
That comparison is a sensitivity indicator only: aliasing can make both grids
agree without proving convergence. It rejects coupled or rotor eddy runs,
demagnetization, voltage/PWM drives, nonuniform or fractional-period windows, zero speed,
zero-current data, and malformed arrays. These gates only decide whether this
separate diagnostic returns a number. They do not remove or change any solved
waveform.

For conservative periodic behavior, the cycle integral of terminal flux work
equals electromagnetic mechanical work after the magnetic-storage derivative
integrates to zero. Shaft output additionally depends on mechanical losses;
this diagnostic does not account for bearings or windage.
The checked analytic nonlinear model tests compare against an independently
differentiated co-energy expression. They demonstrate error reduction as the
sample grid is refined. Separate fifth- and seventh-harmonic tests compare the
line integral with independently differentiated analytic means (2.310 and
0.441 N m); tests also cover parallel-branch scaling, reversal of path
orientation for negative speed, and current-sign response. This analytic
identity does not prove the production P2
states are stationary points of an identical discrete co-energy functional;
the integer slip projection and accepted nonlinear state remain a known
limitation. The helper is not integrated into the solver and no motor result or
torque accuracy claim follows.

Implementation model: GPT-6 Luna. No escalation. Standalone helper tests only;
no production FEM, API, configuration, or browser access.
