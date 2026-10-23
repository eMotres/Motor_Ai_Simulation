import type { ProjectMotor } from './types';

/** Link availability comes only from the batched server projection. Missing flags fail closed. */
export function motorLinkAvailability(motor: ProjectMotor, duty?: string, allowMotorDefault = false) {
  const result = duty ? motor.results?.find((item) => item.duty === duty) : undefined;
  const configureDuty = duty
    ? result?.can_open_configure === true && typeof result.duty === 'string' ? result.duty : null
    : allowMotorDefault && motor.can_open_configure === true && typeof motor.configure_duty === 'string' && motor.configure_duty.trim()
      ? motor.configure_duty : null;
  return {
    configureDuty,
    simulationDuty: duty
      ? result?.can_open_simulation === true && typeof result.duty === 'string' ? result.duty : null
      : allowMotorDefault && motor.can_open_simulation === true && typeof motor.simulation_duty === 'string' && motor.simulation_duty.trim()
        ? motor.simulation_duty : null,
  };
}
