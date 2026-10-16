# Propeller S1: PWM trial binding boundary

This is a pure request/config binding layer for future fixed-bus PWM point
trials. It does not enable PWM in the private runtime bridge, call the coupled
solver, or establish feasibility or an S1 result.

## Source-backed controls

`routes/coupled.py::_coupled_drive` accepts only explicit `current`, `pwm`, or
`inverter` mode (line 539). `_inverter_settings` resolves carrier and bus from
the Controller/request and battery source, computes electrical frequency from
RPM and pole pairs, derives steps/carrier counts, enforces modulation mode,
and reads `inverter.target_I_phase_rms_A` (lines 738–930). In ideal `pwm` mode
the route uses sine modulation; `inverter` mode resolves modulation through
`_controller_settings`, which requires a named device and carries its actual
settings and sources (lines 2867–2910 onward). No mode, bus, carrier, or
Controller is invented by the new binding helper.

There is an important source-precedence split: `_inverter_settings` first
resolves `_drive_carrier` and `_drive_v_dc`, but an explicit
`body.inverter.f_carrier_hz` or `body.inverter.v_dc_V` then wins as a request
override (around lines 794–799). The runtime-v2 builder currently records
`carrier_readback` and `dc_link_readback` directly from `_drive_carrier` /
`_drive_v_dc` (private bridge lines 301–310), separately from
`inverter_readback.sources`. Those records can therefore disagree when the
request overrides the Controller or battery source. The binding requires both
readbacks and sources to agree and refuses such an envelope until runtime
identity construction binds the effective override consistently; matching
numeric values with different sources are not silently treated as equivalent.

`_pwm_final_passes` adjusts the applied fundamental to pursue the requested
terminal-current target (line 1913 onward); `_point_error_pct` compares the
resolved target with the solved terminal current (line 2686 onward). The helper
therefore admits star only, where the candidate winding RMS and terminal phase
RMS share a coordinate. Delta is rejected pending resolution of its convention.

## Binding contract

`bind_pwm_trial` takes a full runtime-v2 identity, a private copied config that
has already been materialized for one candidate, and that candidate's request.
It requires explicit identity and request drive mode (`pwm` or `inverter`), a
star winding, a named fixed DC bus and carrier, resolved modulation and target,
and—on `inverter` mode—a resolved Controller device/count and modulation
source. Config `simulation.rpm`, `max_current`, and `current_a` must match the
request RPM and candidate current; request `rpm`, `I_phase_rms`, and connection
must match as well. The only returned-body edit is retargeting
`inverter.target_I_phase_rms_A` to that star winding current. The body’s
runtime-v2 solver-controls projection must then match the frozen identity.

The full-build comparison projection preserves geometry, winding, materials,
mesh, cooling, pack, bus/carrier/modulation sources, Controller identity and
settings, and all other controls. It removes only candidate-dependent request
target/setpoint values and resolved quantities: target current, RPM-derived
electrical frequency/carriers/samples, automatically derived step count, and
the carrier-count-dependent modulation gain and compensated voltage ceiling.
The uncompensated ceiling stays invariant with its bus/modulation source. Those
candidate-dependent quantities are retained separately as trial readbacks.
Explicit request controls remain in the invariant projection, so a changed
explicit step count, bus, carrier, controller, or other saved setting still
changes the comparison.

The existing trial-context materializer changes only the copied config's RPM
and mirrored current controls (`coupled_propeller_s1_trial_context.py:155–305`).
The PWM binder consumes its copied config output and never edits that
materializer or the source workspace.
The caller remains responsible for providing the materializer's complete
manifest-verified copy and for proving that copied geometry/material inputs
match the runtime identity. This binding helper checks config operating-point
consistency and body/identity projection only; it does not certify the config
tree manifest or independently hash its physical source files.

## Integration blocker and limit

`coupled_propeller_s1_private_bridge.py::_resolve_runtime_identity` currently
refuses every drive mode except `current` at its preflight (around line 299).
Its runtime-v2 builder (`build_runtime_identity_v2`, lines 455–576) already
records resolved drive data and projects point-dependent RPM/current/propeller
fields out of the solver controls, but the child does not yet resolve a PWM
identity. A follow-on bridge change must create the candidate body first,
resolve the actual selected drive/controller/bus under the authenticated
private context, build and independently read back that identity, then pass
the bound body to the unchanged coupled route. This helper alone is not a
runtime integration or electrical gate.

Validation so far is limited to pure source-shaped fixtures. No FEM,
subprocess, API, or live workspace was used; no operating point is qualified.
