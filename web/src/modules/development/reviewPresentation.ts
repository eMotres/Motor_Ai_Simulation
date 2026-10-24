import type { SpecDraft } from './wizardState';

/** Preserve historic section:field and bare-field references after regrouping. */
export function matchesFieldReference(stored: string | undefined, current: string): boolean {
  if (!stored) return false;
  if (stored === current) return true;
  if (current.startsWith('mission_profile:')) return false;
  const id = current.replace(/^(fields|production)\./, '');
  return stored === id || stored.endsWith(`:${id}`);
}

const unwrap = (v: unknown): unknown => (v && typeof v === 'object' && 'value' in (v as object)) ? unwrap((v as { value: unknown }).value) : v;
/** Scalars compare by value; ranges compare by their normalised minimum/maximum. */
function sameAnswer(x: unknown, y: unknown): boolean {
  const a = unwrap(x), b = unwrap(y);
  if (a === b) return true;
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object') return false;
  const ra = a as Record<string, unknown>, rb = b as Record<string, unknown>;
  const lo = (r: Record<string, unknown>) => r.minimum ?? r.min ?? r.from ?? null, hi = (r: Record<string, unknown>) => r.maximum ?? r.max ?? r.to ?? null;
  return lo(ra) === lo(rb) && hi(ra) === hi(rb);
}

/** Keep commercial requirements and launch plans distinct; never choose a winner. */
export function deliveryConflicts(draft: SpecDraft): string[] {
  const result: string[] = [];
  const production = draft.production as Record<string, { status?: string; value?: unknown }> | undefined;
  for (const [field, plan, keys] of [
    ['6_1', 'quantities', ['prototypes', 'pre_series', 'series_per_year']],
    ['6_2', 'timeline', ['simulation_date', 'prototype_date', 'series_date']],
  ] as const) {
    const requirement = draft.fields?.[field], launch = production?.[plan];
    if (requirement?.status !== 'filled' || launch?.status !== 'filled') continue;
    const a = requirement.value as Record<string, unknown> | undefined;
    const b = launch.value as Record<string, unknown> | undefined;
    if (a && b && keys.some(key => a[key] != null && b[key] != null && !sameAnswer(a[key], b[key]))) result.push(plan);
  }
  return result;
}
