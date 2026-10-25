import type { ProjectMotor } from './types';

/** Link availability comes only from the batched server projection. Missing flags fail closed.
 *  ``loadDuty``: a duty with no solved result can still be LOADED into Simulation (geometry + operating point,
 *  ready to Run) - owner 2026-10-25: the load buttons must not vanish while a configuration is not computed yet. */
export function motorLinkAvailability(motor: ProjectMotor, duty?: string, allowMotorDefault = false) {
  const result = duty ? motor.results?.find((item) => item.duty === duty) : undefined;
  const loadDuty = duty && result && motor.available !== false && result.can_open_simulation !== true
    && typeof result.duty === 'string' && result.duty.trim() ? result.duty : null;
  const configureDuty = duty
    ? result?.can_open_configure === true && typeof result.duty === 'string' ? result.duty : null
    : allowMotorDefault && motor.can_open_configure === true && typeof motor.configure_duty === 'string' && motor.configure_duty.trim()
      ? motor.configure_duty : null;
  // Points-only Configure (owner 2026-10-26): any stored duty result opens the card, no passport needed.
  // Offered only where the full Configure is not (the full path stays untouched).
  const hasPoint = (item: { has_result?: boolean; duty?: string } | undefined) => item?.has_result === true && typeof item.duty === 'string' && item.duty.trim() !== '';
  const pointsDuty = configureDuty || motor.available === false ? null
    : duty
      ? (hasPoint(result) ? result!.duty! : null)
      : allowMotorDefault && motor.can_open_points === true ? (motor.results?.find(hasPoint)?.duty ?? null) : null;
  return {
    configureDuty,
    pointsDuty,
    simulationDuty: duty
      ? result?.can_open_simulation === true && typeof result.duty === 'string' ? result.duty : null
      : allowMotorDefault && motor.can_open_simulation === true && typeof motor.simulation_duty === 'string' && motor.simulation_duty.trim()
        ? motor.simulation_duty : null,
    loadDuty,
  };
}
