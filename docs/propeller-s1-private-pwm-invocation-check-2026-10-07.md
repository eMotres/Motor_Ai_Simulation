# Private STAR PWM bridge checkpoint — 2026-10-07

The private bridge now accepts only an explicitly frozen `pwm` or `inverter` STAR build. At admission it verifies the supplied full runtime-v2 identity hash, then requires the job identity to already equal its stable PWM projection. That makes the job identity digest stable across trial RPM and current changes. Unsupported drive/connection changes still refuse before the canonical callback.

Within each isolated authenticated trial workspace, the bridge re-resolves the saved build and binds the requested RPM, winding current, PWM terminal-current target, sourced DC bus, carrier, modulation and controller readback. It calls the existing `routes.coupled._run` seam only after those checks, retains the full candidate runtime identity plus its digest in private run provenance, then re-resolves and compares the post-run invariant identity. The authorization value is transient in memory and anonymous stdin only; it is never persisted. Only sanitized solver provenance and raw response checkpoints are written in the private attempt path; no live credentials are read or copied.

The voltage mapper now verifies the full candidate runtime identity hash, its invariant projection, and a per-point binding digest covering request/body hashes, RPM/current, and the private trial-config hash before using candidate-dependent ceiling or gain values. Effective point controls and candidate target must match. The reported compensated/uncompensated voltage ceilings and modulator gain must match the candidate readback; the modulation-index limit comes from the admitted `simulation/pwm.py` implementation, whose file hash is checked against the full identity code manifest. The release identity manifest now includes the mapper and PWM source module. Missing or inconsistent candidate evidence returns unknown. Delta remains unknown. Fixed-bus motor evidence does not establish loaded-pack or controller-system qualification; mechanical evidence remains unknown unless separately sourced.

Focused checks, using only injected fake `coupled._run` responses and no FEM or live API:

- `tests/test_propeller_s1_private_bridge.py` plus `tests/test_propeller_s1_voltage_evidence.py`: **82 passed, 1 skipped**. The skipped case requires symlink creation unavailable on this Windows host.
- The composed bridge-to-voltage-mapper fixture passes the electrical fixed-bus gate while controller-system, loaded-pack and mechanical claims remain unknown.
- Added fail-closed cases cover a changed reported ceiling despite recomputed response hashes, invalid candidate identity digest, applied voltage beyond the candidate ceiling, and modulation index beyond the source ceiling.

This is software-boundary evidence only. The canonical solver was stubbed; no actual coupled/FEM runtime was executed, no maximum was certified, and nothing was published or deployed. The implementation was done by a GPT-6-family model without a model escalation.


