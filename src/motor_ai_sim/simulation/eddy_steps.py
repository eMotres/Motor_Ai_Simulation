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


def resolve_steps_per_period(value: Optional[Any], *, eddy: bool) -> int:
    """The step count a transient solves: the caller's, else the default.

    ``None`` (or ``""``) = not named -> 72 with the eddy solve on, 60 without.
    Anything named is validated and returned unchanged.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return (EDDY_DEFAULT_STEPS_PER_PERIOD if bool(eddy)
                else STATIC_DEFAULT_STEPS_PER_PERIOD)
    return validate_steps_per_period(value)
