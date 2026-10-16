import type { PwmVariant } from './motorScaling';

type DataRecord = Record<string, unknown>;

const record = (value: unknown): DataRecord | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as DataRecord
    : null;

const isUsableVariant = (value: unknown): value is PwmVariant => {
  const item = record(value);
  const points = record(item?.points);
  return typeof item?.id === 'string' && item.id.length > 0
    && typeof item.device === 'string' && item.device.length > 0
    && Number(item.carrier_hz) > 0
    && points !== null && Object.keys(points).length > 0;
};

/** A reference-only/unqualified card cannot contribute a drive model. */
export function isReferenceOnlyCard(card: unknown): boolean {
  const root = record(card);
  const wrapper = record(root?.passport);
  const passport = record(wrapper?.passport);
  const provenance = record(wrapper?.reference_provenance)
    ?? record(passport?.reference_provenance);
  return [root, wrapper, passport, provenance].some((item) =>
    item?.reference_only === true || item?.qualification_claim === false);
}

/** Select the same preset-first variants as Configure, but fail closed for references. */
export function selectConfigureVariants(
  referenceOnly: boolean,
  presetVariants: unknown,
  passportVariants: unknown,
): PwmVariant[] {
  if (referenceOnly) return [];
  const preset = Array.isArray(presetVariants) ? presetVariants.filter(isUsableVariant) : [];
  if (Array.isArray(presetVariants) && presetVariants.length > 0) return preset;
  return Array.isArray(passportVariants) ? passportVariants.filter(isUsableVariant) : [];
}
