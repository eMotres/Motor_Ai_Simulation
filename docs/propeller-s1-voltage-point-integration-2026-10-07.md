# Propeller S1 point adapter and PWM voltage evidence — 2026-10-07

This note records the offline point-adapter integration. It does not report a
motor result, does not expose a route, and does not establish a published S1
rating or a global maximum.

## Qualification path

`coupled_propeller_s1_point.evaluate_propeller_rpm` still evaluates a bounded
current sweep at each requested RPM through its injected canonical callback.
The search interval remains a computational bound. It is never inferred to be
motor ampacity.

For `drive.mode == "pwm"` or `"inverter"`, the point adapter now requires the
independent voltage mapper for **every** sample, even if the frozen identity
also contains a sourced current/controller ceiling. It accepts a motor-only
fixed-bus electrical gate only when all of these agree: star winding; intact
`propeller-s1-runtime-v2` identity and solver build digest; nested resolved
inverter bus, source, carrier, and modulation readbacks; callback identity and
request hashes; hash-covered RPM/current/connection, propeller load, and
propeller cooling inputs; and the same-point canonical coupled PWM response,
including explicit convergence, PWM target/on-point, current, bus, delivered
shaft torque, and torque match. Delta remains unknown because the target/current
coordinate convention is unresolved. Flat legacy bus aliases cannot qualify
through this point-adapter path.

The PWM target is the point adapter's requested current candidate. The solved
current remains a separate observation and must first satisfy the adapter's
explicit absolute current-match tolerance. The voltage mapper then compares
that actual readback with the requested target using the frozen effective
`inverter.i_tol_pct` (or the route's documented 1% default when absent/zero);
it cannot move the expected target to the output current or widen either band.

If a declared current ceiling is also supplied, it must still match the frozen
identity and have a sourced same-point current observation. The motor electrical
gate combines that cap evidence with the fixed-bus mapper; neither can substitute
for the other. Controller-system and loaded-pack claims remain separate and are
not inferred from motor-only fixed-bus feasibility. For current-drive identities
without a sourced current ceiling, the existing behavior remains: retain raw
torque/current observations, but leave the electrical gate unknown. A generic
callback `electrical_within_limit=true` cannot promote that path.

## Evidence sources and limitations

The mapper in `coupled_propeller_s1_voltage_evidence.py` uses the response shape
from `routes/coupled.py::_inverter_record` and the delivered shaft-power convention
from `routes/simulation.py` (`transient.summary.P_shaft_net_W`). It does not use
`P_shaft_W`, which is shaft eddy loss, as output power. PWM feasibility is only
at the resolved fixed DC bus; pack sag is not calculated. A PWM point with a
missing, contradictory, off-point, nonconverged, or incomplete readback remains
unknown, not a physical voltage failure. Even complete point evidence is only a
candidate input to the outer RPM search; finite sampled points cannot certify a
global maximum and publication qualification remains false.

The private runtime bridge still needs its own reviewed voltage-fed invocation
and readback support before this adapter can be exercised on an actual build.
The test fixtures here are source-shaped fake responses; no FEM solver or live
API was called for these tests.

## Verification

The focused offline suite covered the point adapter, voltage evidence mapper,
job lifecycle, and RPM search. It passed with one existing skipped test:

```text
128 passed, 1 skipped
```
