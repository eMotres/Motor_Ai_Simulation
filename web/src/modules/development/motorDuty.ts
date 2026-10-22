export type ProjectMotorDuty = string | { name?: string | null };

/** Select the first saved operating point, falling back to the motor's default. */
export function firstProjectMotorDuty(duties: ProjectMotorDuty[] | undefined, defaultDuty?: string | null): string | null {
  return duties?.map((item) => typeof item === 'string' ? item : item.name)
    .find((name) => typeof name === 'string' && name.trim()) ?? defaultDuty ?? null;
}
