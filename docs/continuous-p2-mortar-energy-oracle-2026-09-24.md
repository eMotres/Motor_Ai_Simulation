# Conservative-energy oracle for the isolated P2 mortar (2026-09-24)

This is a small algebraic validation of `P(theta)` and `P'(theta)` from the
trace-only prototype, not an electromagnetic FEM run. The full positive
diagonal stiffness `K` and prescribed source `f` stay fixed as angle changes.
At each angle, solve the reduced equilibrium of

```
Phi(A) = 0.5 A^T K A - f^T A,    A = P(theta) a,
P(theta)^T (K A - f) = 0.
```

With `r=K A-f`, stationarity removes the `a'(theta)` term from the derivative:

```
dPhi*/dtheta = r^T P'(theta) a.
```

For a prescribed linear generalized current/source, define coenergy
`W' = f^T A - 0.5 A^T K A = -Phi*` at equilibrium. With positive mechanical
angle and positive torque in the same direction, the test convention is
`T = dW'/dtheta = -r^T P'a`. This fixes the sign explicitly; no sector
multiplier is included. A physical full-machine torque would require the
appropriate sector scaling after a physically valid sector model is proved.

The tests use fixed full `K,f` and solve exactly in the reduced space. Across
full-ring and periodic/antiperiodic sector examples at positive, negative, and
wrapped angles, `||P^T r|| < 1e-11` while `||r|| > 1e-3`. Central differences
of independently re-solved minimum energy and coenergy agree with the
analytic envelope and torque at `rtol=2e-7`, `atol=2e-9`. Reversing the **entire
pure source** reverses the state but leaves quadratic torque unchanged;
scaling that source by 1.8 scales torque by `1.8^2`. This is not a claim that
reversing only motor current in the presence of a fixed magnet source leaves
torque unchanged.

A separate convex diagonal oracle uses
`Phi(A)=sum(0.5*k*A^2+0.25*beta*A^4)-f^T A`, with positive `k,beta` and exact
residual `r=k*A+beta*A^3-f`. It satisfies the same envelope identity after a
converged reduced Newton solve. It is not a nonlinear B-H FEM model. Fourteen
focused energy tests passed in 0.75 s.

The result validates the algebraic derivative for fixed full energy/source.
In a real motor, any explicit angular dependence of `K`, `f`, geometry,
materials, or circuit variables adds its own derivative. Eddy-current
transients are not represented by this static conservative oracle. The
polygonal-interface limitation and run16-size cost remain as documented in
the preceding mortar audits. Production files were not changed.
