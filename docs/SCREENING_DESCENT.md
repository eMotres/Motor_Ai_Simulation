# Screening descent — the second optimizer mode

`POST /api/optimization/auto` with `mode: "screen"`.
UI: the Optimize card's **Explore / Refine** switch (Refine = this mode).

## Why it exists

Not a hypothesis. On the CIANO20 150_35 (24 slots / 20 poles, 150 mm, 35 mm
stack) the one-click CMA-ES run spent **434 evaluations, 359 of them
informative**, and its best candidate inside the 5 % ripple gate scored
**F = −0.0173** on the run's own perpendicular-baseline metric — meaning it never
beat the design it started from, let alone beat simply raising the current.

The user then beat it **by hand**, at the same fixed operating point
(78.49 A / 4000 rpm / γ 10°), working with at most four parameters at a time:

| | torque density | efficiency | ripple | F |
|---|---|---|---|---|
| start (08/04) | 9.917 Nm/kg | 96.410 % | 4.76 % | 0 |
| by hand (08/05) | 10.508 Nm/kg | 96.505 % | 5.04 % | **+0.00221** |
| CMA-ES, 434 evals | — | — | — | −0.0173 |

Both numbers re-measured in-process at identical eval settings (48 steps/period,
n_sectors 4, P2, geometry-driven mesh, template iron). A human beat 359 machine
evaluations. That is a defect in the **search**, not in the physics.

The cause is not subtle. CMA-ES has to estimate an N×N covariance — 171 free
parameters at N = 18 — before its sampling distribution carries any shape at all.
At roughly ten minutes per honest FEM evaluation that budget does not exist.
Finite differences buy the gradient outright for 2N evaluations and spend
everything after that moving.

## The method

The user's own words:

> сначала сделал бы первоначальные отклонения по всем переменным в районе 0.2 mm
> или 0.02 для безразмерных и понял бы какая куда отклоняет систему, а потом уже
> использовал самые влиятельные и доводку делал оставшимися

Mechanised as four phases, all on the same fixed operating point and the same
honest eval path the CMA route uses:

**1. Baseline (2 evals).** The current design at I, and the same geometry at
I × 1.10. Those two points define the current-only baseline line; the objective
is the signed perpendicular distance above it (standing rule — this mode does
not get its own objective).

**2. Screening (2N evals, one parallel wave).** Every variable perturbed by ±δ:

| variable kind | δ |
|---|---|
| length (unit = mm) | 0.2 mm |
| dimensionless (fill fractions, rotor_hole) | 0.02 |
| integer (turns per slot) | 1 |

Produces the ranked sensitivity table: for each variable the slope dcost/dx, the
**influence** (|Δcost| over one δ — δ-scaled, so a millimetre knob and a
dimensionless one are comparable), the direction that lowers the cost, and the
**jitter**, |c₊ + c₋ − 2c₀| / 2.

*On the noise floor.* **Measured, not assumed** (2026-08-05): the same
cross-section solved twice in two separate subprocesses at the production eval
settings returned **bit-identical** metrics on all twelve reported quantities
(torque, torque density, efficiency, ripple, mass, every loss term, V_peak,
THD, Kt). So there is no statistical noise to average away, replicating the
unperturbed point measures nothing, and this mode does not pay for replicates.
(Determinism depends on the single-thread pinning of the eval subprocess — a
threaded BLAS reduction sums in thread-completion order. That pinning is in
`_subprocess_eval`; if it is ever removed, this claim goes with it.)

What actually limits the gradient is that the mesh is rebuilt per candidate, so
the objective is piecewise-smooth with small jumps at the remeshing seams. The
disagreement between the two one-sided slopes (`jitter`) is exactly that effect
plus real curvature, and the **median jitter over all variables** is the floor:
an influence smaller than the typical disagreement between the two ways of
measuring it is not evidence. On the CIANO20 150_35 the opening screen put that
floor at 6.4e-06, which correctly marked `stator_fillet_r`, `cut_width` and
`rotor_fill_r` inert while keeping variables three orders of magnitude above it.

A variable whose −δ would leave the physical half-line, or whose cross-section
will not build on one side, is screened one-sided rather than dropped. A variable
with no usable side is reported **unmeasured**, which is not the same as inert
and must never be rendered as such.

**3. Descent.** Steepest descent restricted to the top-k influential variables.
k is chosen **by the gap**: among the variables above the noise floor, cut at the
largest ratio drop between consecutive influences (k ≤ 6; if there is no drop
above 1.5× anywhere, there is no honest place to cut and everything above the
floor is influential). The step is steepest descent in δ-scaled coordinates,
normalised so the most influential variable moves exactly one δ — which makes the
line-search multiplier read directly as "how many screening deviations".

α ∈ {0.5, 1, 2, 4} are four independent points, so the line search costs **one
parallel wave, not four**. The best α is accepted only if it lowers the cost.

After each accepted step the **active set is re-screened** (2k evals): the
gradient rotates as the design moves, and taking a second step along a stale one
is how a descent walks off a ridge. When the active set stalls, everything is
re-screened once before it is abandoned — the influential set at the new point
need not be the old one.

**4. Polish.** The remaining variables in groups of ≤ 4 — the user's own working
set — same δ, same line search. A polish round that improves something sends the
run back to descent; a round that improves nothing shrinks δ by half (floored at
0.1 mm / 0.01, which is both the user's manufacturing-noise floor and the mm
quantisation grid) and tries once more, then stops.

## What is shared with the CMA route, deliberately

Everything that decides whether a number is honest:

- `_subprocess_eval` — the same P2 sliding-band transient, the same rejects.
- `_auto_prefence` — the geometry validator, run in-process before the
  subprocess, so an unbuildable perturbation costs milliseconds instead of ten
  minutes. Same functions `refine_proc.run_one` calls, in the same order.
- `_descent_cost` + `_RIPPLE_PEN_LAM` + `_ripple_ramp_step` — the same ripple
  constraint and the same penalty continuation. A change to constraint handling
  cannot apply to one mode and not the other.
- The persistent eval cache. A screening descent re-screens around a moving
  point, so it lands on cross-sections it has already solved constantly; the memo
  and the on-disk cache are not an optimisation here, they are the budget. **A
  cache hit is not charged to the eval budget** and is reported separately.
- `_descent_state` / `/api/optimization/descent/progress` — so every existing
  chart, the Apply path and the eval-parameter restore keep working unchanged.

Costs are recomputed from stored **metrics** on every comparison rather than
cached as numbers, so a ripple-penalty ramp re-scores the whole run consistently
instead of leaving old costs measured at an old λ.

## When to prefer which

**Refine (screening descent)** when:
- the machine has already been designed and you are looking for the last few
  percent — a real cross-section someone chose on purpose;
- the eval is expensive relative to the budget (anything over a minute at
  N ≳ 10);
- you want to *know which knobs matter*. The ranked table is the durable output;
  the winning geometry is the by-product. CMA-ES never produces one.

**Explore (CMA-ES)** when:
- the design is new, arbitrary, or came from a catalogue — there may be a better
  basin and a local method will never find it;
- the objective is expected to be multi-modal in the variables you opened;
- you can afford O(N²) evaluations.

The honest limitation: **screening descent finds the nearest local optimum and
cannot change basin.** It is a hill climb with a measured gradient. On a machine
that is already good, that is exactly what is wanted; on a machine that is in the
wrong place entirely, it will polish the wrong design very efficiently.

## Cost model

For N variables, per the plan the route quotes before it runs:

```
2                  baseline pair
2N                 opening screen              (one wave at ≤ workers)
per descent step   4 (line search) + 2k (re-screen of the active set)
per polish group   2·|group| (screen) + 4 (line search)
```

The budget default is the same rule CMA-ES gets (`max(120, 24·N)`), so the two
modes are compared over the same money. It is a **cap, not a target**: the run
stops when a full descent + polish round measures no improvement, and the result
says which of the two ended it (`stop_reason`). The cap is enforced *inside* a
wave, not only between waves — a trimmed screening pass leaves those variables
unmeasured, and the table says so.

## Reading the result

`GET /api/optimization/auto/status` adds, for a screening run:

- `mode` — `"screen"`.
- `sensitivity` — the ranked table with its noise floor. Keep it; it outlives the
  run.
- `active_set` / `active_why` — which variables the descent moved and the gap
  that justified the choice.
- `trajectory` — F, cost, td, efficiency, ripple, the FEM-eval count and the full
  geometry after every accepted step.
- `stop_reason` — `converged`, `budget`, or `cancelled`.
- `F` and `above_baseline_line` — unchanged in meaning. F < 0 still means the
  design does not beat simply raising the current, and that verdict is stated,
  not left as a number to interpret.

## The acceptance run (2026-08-05)

Same starting point as the CMA-ES run that failed — the CIANO20 150_35 as saved
in the Compare row of 08/04 — same fixed operating point (78.49 A / 4000 rpm /
γ 10°), same 5 % ripple gate, same honest eval path (P2, 48 frames/period,
n_sectors 4, geometry mesh, template iron, rotor eddy, per-candidate auto
`k_end`).

| | Nm/kg | efficiency | ripple | **F** |
|---|---|---|---|---|
| baseline, 08/04 | 9.9168 | 96.410 % | 4.76 % | 0 |
| the user, by hand | 10.5086 | 96.505 % | 5.04 % | +0.002213 |
| CMA-ES, 434 evals | — | — | — | −0.0173 |
| **screening descent, 220 evals** | **10.8075** | **96.451 %** | **4.30 %** | **+0.002311** |

**It beats the hand-tuned design** — by 4.4 % on F, with 0.30 Nm/kg more torque
density and, unlike the hand design, comfortably *inside* the ripple gate
(4.30 % against 5.04 %, which was marginally over). 220 FEM evals, 90 minutes
wall clock on 10 workers, unattended. 22 of 222 candidates were rejected (9.9 %,
all in the FEM — 4 more were caught by the in-process geometry pre-fence at no
eval cost). The run stopped on the **budget**, not on convergence: it was still
accepting steps when the quota ran out, so the number above is a floor.

Honest accounting of the cost: the user reached their point in roughly a dozen
solves. The machine needed ~18× more. What it bought is that nobody had to be
in the room, and it kept going past where the human stopped.

### Three things the run showed that are worth keeping

**1. The polish phase did all the work; the descent phase did none.** Every one
of the ten accepted steps came from a group of ≤ 4, not from the steepest-descent
direction on the influential set. The opening screen ranked `wire_height`
(influence 0.041) and `wire_width` (0.037) two to three times above anything
else — but both were **one-sided**: the other side of each is unbuildable (the
winding no longer fits), so their "gradient" was a forward difference taken at a
wall, and every line search along it was rejected. Meanwhile the leftovers, each
individually an order of magnitude weaker, paid every round when moved together
in fours. The lesson is not to distrust the ranking; it is that a large
one-sided influence at a feasibility boundary is not the same evidence as a
large two-sided one, and the group cycling is what rescues the run when the top
of the table is standing on a wall.

**2. The local gradient pointed the opposite way to the user on `tooth_width`.**
At the baseline the screening says increase it (dcost/dx < 0 going up); the user
decreased it by a full millimetre. Both are right: at 9.2 mm the ripple jumps
from 4.76 % to 6.82 %, so the penalty repels the descent from exactly the
direction the human took by eye. The run reached 9.2 mm eventually, from the
other side, once the rest of the cross-section had moved. A local method cannot
step over a constraint ridge; it can only walk around it, and here that was
enough.

**3. The best design came from a screening perturbation, not from an accepted
step.** The last accepted line search left F at +0.002186; the reported best is
+0.002311, found in the final (budget-truncated) screening wave. Screening
points are fully-paid-for designs, and the rule that the incumbent may never be
worse than something already evaluated is what turned a measurement into the
result.

### What it agreed with the user about

Of the 18 variables, the algorithm reproduced the user's value on 3
(`slot_height`, `wire_width`, `wire_height` — all left at the baseline) and
chose differently on 15. It is a genuinely different machine of slightly better
merit, not a rediscovery of the same one: notably it went the *other* way on
`stator_fillet_r1` (0.4 vs the user's 1.8), `magnet_fill_radius` (1.4 vs 0.6)
and `cut_width` (7.5 vs 6.0), and added a turn (`num_wires_per_slot` 17 → 18)
that the user never touched.
