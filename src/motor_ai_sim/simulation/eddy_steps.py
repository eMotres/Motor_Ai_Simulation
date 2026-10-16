"""Default time resolution of an eddy-current (sigma*dA/dt) run.

Owner decision 2026-09-26: a run with the coupled eddy solve ON defaults to 72
steps per electrical period, not 36.  Measured
(``docs/EDDY_TIME_INTEGRATION_2026-09-25.md`` section 1, L155 rated, BDF2 with the
midpoint loss): the magnet loss reads -4.3 % against the step-converged limit at
36 steps and -1.05 % at 72; shaft -3.1 / -0.7 %, copper AC -1.1 / -0.3 %.

It is a DEFAULT and nothing more: a step count the caller names is always the
one solved (the slip-ring snap in the solver may still move it to a divisor of
the ring, and the result says so in ``n_steps_per_period_requested`` /
``steps_snapped``).  Optimizer screening does not use this number — its
candidates carry ``sampling_purpose="optimization"`` and their own count.
"""
from __future__ import annotations

from typing import Any, Optional

#: steps per electrical period of an eddy-on run whose request names none
EDDY_DEFAULT_STEPS_PER_PERIOD = 72
#: the transient route's default for an eddy-OFF run (unchanged)
STATIC_DEFAULT_STEPS_PER_PERIOD = 60


#: owner 2026-09-27: reported torque ripple / cogging needs at least this many
#: samples per cogging cycle (L155, 12 cycles/period: 108 steps is within 1.3 %
#: of the 216-step ripple; docs/EDDY_PERIODIC_ACCEL_2026-09-26.md)
RIPPLE_SAMPLES_PER_COGGING_CYCLE = 9


def cogging_cycles_per_period(num_slots: Any, num_poles: Any) -> int:
    """Cogging cycles per ELECTRICAL period, lcm(Q, 2p) / p; 0 if unknown."""
    import math
    try:
        q, p2 = int(round(float(num_slots))), int(round(float(num_poles)))
    except (TypeError, ValueError):
        return 0
    if q < 1 or p2 < 2:
        return 0
    return (q * p2 // math.gcd(q, p2)) // (p2 // 2)


def ripple_grade_steps(num_slots: Any, num_poles: Any) -> int:
    """Report-grade default: max(eddy default, 9 x cogging cycles/period)."""
    return max(EDDY_DEFAULT_STEPS_PER_PERIOD,
               RIPPLE_SAMPLES_PER_COGGING_CYCLE
               * cogging_cycles_per_period(num_slots, num_poles))


def validate_steps_per_period(value: Any, *, name: str = "n_steps_per_period") -> int:
    """A named step count, checked: a whole number >= 1, never a bool."""
    if isinstance(value, bool):
        raise ValueError("%s must be a whole number of steps; got %r" % (name, value))
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be a whole number of steps; got %r" % (name, value))
    if f != f or f in (float("inf"), float("-inf")) or f != int(f):
        raise ValueError("%s must be a whole number of steps; got %r" % (name, value))
    if int(f) < 1:
        raise ValueError("%s must be >= 1; got %r" % (name, value))
    return int(f)


def resolve_steps_per_period(value: Optional[Any], *, eddy: bool,
                             num_slots: Any = None, num_poles: Any = None) -> int:
    """The step count a transient solves: the caller's, else the default.

    ``None`` (or ``""``) = not named -> with the eddy solve on, the
    ripple-grade count when the slots/poles are given (>= 72), else 72; 60
    without the eddy solve.
    Anything named is validated and returned unchanged.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return (ripple_grade_steps(num_slots, num_poles) if bool(eddy)
                else STATIC_DEFAULT_STEPS_PER_PERIOD)
    return validate_steps_per_period(value)
