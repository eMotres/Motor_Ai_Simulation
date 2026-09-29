# Angular sampling purpose for FEM optimization (2026-09-24)

The transient solver now receives an explicit `sampling_purpose`. Manual Simulation,
baseline and final runs default to `standard`; optimizer subprocess evaluations
pass `optimization` through the kernel and route to the FEM solver. Neither the
job owner nor the ambient environment selects the policy.

The number of cogging cycles in one electrical period is
`lcm(num_slots, num_poles) / pole_pairs`. Standard sampling requires at least
**6 raw angular samples per cycle**. Optimization requires **3 raw samples per
cycle** for coarse candidate ranking. For a whole-node sliding band, the final
step count is the smallest divisor of the *existing* slip-ring nodes per
electrical period that meets the selected minimum. For continuous angles, it is
the maximum of the requested and required counts. The solver retains every
sample; it neither filters torque nor changes the spatial mesh for this policy.

| Geometry | Slip-ring nodes/period | Optimization | Standard |
| --- | ---: | ---: | ---: |
| 12 slots, 14 poles | 144 | 36 | 72 |
| 24 slots, 28 poles | 120 | 40 | 120 |

Result metadata reports purpose, target and actual raw samples per cycle,
whether the selected purpose was satisfied, and separately whether the
6-sample final-quality requirement was satisfied. If the existing ring is too
small, the solver keeps all available samples and marks the result insufficient.
Optimizer and standard evaluations have distinct cache keys; old cache records
are retired by a key-version change. Sweep cache seeding accepts only records
that state the sampling purpose.

**A coarse optimization ripple or cogging value is not a final-quality
measurement.** Selected candidates must be re-solved with `standard` before
accepting their ripple or cogging value. This policy reduces candidate cost; it
does not claim 3 samples/cycle are converged. In the 12s14p no-load fixture,
the earlier raw 48-to-72-frame comparison still changed the peak-to-peak
Maxwell torque by about 0.4%, motivating the 6-sample final policy. Independent
PWM/carrier resolution guards continue to apply after the cogging decision.
