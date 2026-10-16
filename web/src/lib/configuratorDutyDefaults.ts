import type { Preset } from './configuratorPresets';

export interface ActiveDutyIdentity { die: string; config: string; duty: string; }
export interface DutyRpmDefault { rpm: number | null; key: string; source: 'active-duty' | 'rated' | 'preset'; }

/** Manual keeps the live operating speed; only Propeller load adopts catalog speed. */
export function initialRpmForLoadMode(mode: 'prop' | 'manual', liveRpm: number,
                                      fallbackRpm: number, catalogRpm: number | null): number {
  if (mode === 'prop' && catalogRpm != null && Number.isFinite(catalogRpm) && catalogRpm > 0) return catalogRpm;
  if (Number.isFinite(liveRpm) && liveRpm > 0) return liveRpm;
  return fallbackRpm;
}

/**
 * Propeller mode's starting speed comes from the exact loaded catalog duty.
 * If there is no matching active duty, prefer the configuration's rated point,
 * then its legacy preset RPM. Manual mode does not call this resolver.
 */
export function propellerDutyRpmDefault(preset: Preset | null | undefined,
                                        active: ActiveDutyIdentity | null): DutyRpmDefault {
  if (!preset) return { rpm: null, key: 'none', source: 'preset' };
  const sameConfig = !!active && active.die === preset.die && active.config === preset.config;
  const activePoint = sameConfig
    ? preset.duty_points?.find((d) => d.name === active!.duty)
    : undefined;
  const ratedPoint = preset.duty_points?.find((d) => d.name.trim().toLowerCase() === 'rated');
  const selected = activePoint?.rpm != null && Number.isFinite(activePoint.rpm)
    ? { point: activePoint, source: 'active-duty' as const }
    : ratedPoint?.rpm != null && Number.isFinite(ratedPoint.rpm)
      ? { point: ratedPoint, source: 'rated' as const }
      : null;
  const rpm = selected?.point.rpm ?? (Number.isFinite(preset.knobs.rpm) ? preset.knobs.rpm : null);
  const source = selected?.source ?? 'preset';
  const key = [preset.die, preset.config, selected?.point.name ?? 'preset', rpm ?? 'unknown'].join('|');
  return { rpm, key, source };
}
