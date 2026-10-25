import type { Completeness, SchemaField, SchemaSection } from './types';
import type { SpecificationField } from './specificationLayout';

export interface FieldAnswer { value?: unknown; status?: 'filled' | 'not_known_yet' | 'missing'; not_known_yet?: boolean }
export type SpecDraft = Record<string, unknown> & { fields?: Record<string, FieldAnswer>; mission_profile?: Array<Record<string, unknown>>; mission_profile_status?: 'filled' | 'not_known_yet' | 'missing' };
export function fieldAnswer(draft: SpecDraft, id: string): FieldAnswer {
  const value = draft.fields?.[id];
  return value && typeof value === 'object' && !Array.isArray(value) ? value as FieldAnswer : { value };
}
export function setFieldAnswer(draft: SpecDraft, id: string, answer: FieldAnswer): SpecDraft {
  return { ...draft, fields: { ...(draft.fields ?? {}), [id]: answer } };
}
/** Recalculate the display-only S1 power or remove a stale derived value when either input is blank.
 *  Every other property (the stated `power_W`, `power_basis`) is kept as entered. */
export function withS1DerivedPower(value: Record<string, unknown>): Record<string, unknown> {
  const torque = value.torque_Nm, speed = value.speed_rpm;
  if (typeof torque === 'number' && Number.isFinite(torque) && typeof speed === 'number' && Number.isFinite(speed)) {
    return { ...value, shaft_power_W: torque * 2 * Math.PI * speed / 60 };
  }
  const next = { ...value }; delete next.shaft_power_W; return next;
}
export function isRequired(f: SchemaField): boolean { return f.required === true || f.required_flag === 'E'; }
export function isConditionallyRequired(f: SchemaField, draft: SpecDraft): boolean {
  const condition = (f as SchemaField & { required_when?: { field: string; in: unknown[] } }).required_when;
  if (!condition) return false;
  const answer = fieldAnswer(draft, condition.field);
  return answer.status === 'filled' && condition.in.includes(answer.value);
}
export function fieldRequired(f: SchemaField, draft: SpecDraft): boolean { return isRequired(f) || isConditionallyRequired(f, draft); }
export function answerNamespace(section: SchemaSection, field: SchemaField): 'fields' | 'production' {
  return (field as SpecificationField).storage_namespace ?? (section.id === 'production' || section.production ? 'production' : 'fields');
}
export function fieldValueValid(field: SchemaField, value: unknown): boolean {
  if (value === undefined || value === null || value === '') return false;
  if (field.allow_range && typeof value === 'object') {
    const bounds = value as Record<string, unknown>, lo = Number(bounds.minimum), hi = Number(bounds.maximum);
    return bounds.minimum !== null && bounds.minimum !== undefined && bounds.minimum !== '' && bounds.maximum !== null && bounds.maximum !== undefined && bounds.maximum !== '' && Number.isFinite(lo) && Number.isFinite(hi) && lo <= hi;
  }
  if (field.type === 'number') {
    const n = typeof value === 'number' ? value : Number(value);
    return typeof value !== 'boolean' && Number.isFinite(n) && (field.min === undefined || n >= field.min) && (field.max === undefined || n <= field.max);
  }
  const options = field.options ?? field.choices;
  if (options?.length) return options.some((o) => (typeof o === 'string' ? o : o.value) === value);
  return true;
}
export function answerComplete(a: FieldAnswer): boolean {
  if (a.status === 'not_known_yet' || a.not_known_yet === true) return true;
  if (a.value && typeof a.value === 'object' && !Array.isArray(a.value)) {
    const values = Object.values(a.value as Record<string, unknown>);
    return values.length > 0 && values.every((v) => v !== undefined && v !== null && String(v).trim() !== '');
  }
  return a.value !== undefined && a.value !== null && String(a.value).trim() !== '';
}

/** Groups of `required_any` with no complete property: e.g. S1 needs torque OR power. */
export function missingAnyGroups(field: { required_any?: string[][]; properties?: Array<{ id: string; type?: string; min?: number; max?: number; allow_range?: boolean }> }, value: unknown): string[][] {
  const record = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
  return (field.required_any ?? []).filter((group) => !group.some((id) => { const property = field.properties?.find((p) => p.id === id); return !!property && schemaValueComplete(property, record[id]); }));
}

export function schemaValueComplete(schema: { type?: string; min?: number; max?: number; required?: boolean; allow_range?: boolean; required_any?: string[][]; properties?: Array<{ id: string; required?: boolean; required_flag?: string; type?: string; min?: number; max?: number; allow_range?: boolean; properties?: any[]; items?: any }>; items?: { type?: string; properties?: Array<{ id: string; required?: boolean; type?: string; min?: number; max?: number; allow_range?: boolean; properties?: any[]; items?: any }> }, min_filled_properties?: number }, value: unknown): boolean {
  if (value === undefined || value === null || (typeof value === 'string' && !value.trim())) return false;
  if (schema.allow_range && typeof value === 'object') {
    const bounds = value as Record<string, unknown>;
    const min = Number(bounds.minimum), max = Number(bounds.maximum);
    return bounds.minimum !== null && bounds.minimum !== undefined && bounds.minimum !== '' && bounds.maximum !== null && bounds.maximum !== undefined && bounds.maximum !== '' && Number.isFinite(min) && Number.isFinite(max) && min <= max && (schema.min === undefined || min >= schema.min) && (schema.max === undefined || max <= schema.max);
  }
  if (schema.type === 'array') {
    if (!Array.isArray(value)) return false;
    if (schema.required && value.length === 0) return false;
    const item = schema.items;
    if (item?.type === 'object' && item.properties) return value.every((row) => row && typeof row === 'object' && item.properties!.filter((p) => p.required === true).every((p) => schemaValueComplete(p, (row as Record<string, unknown>)[p.id])));
    if (item?.type === 'number') return value.every((x) => x !== '' && Number.isFinite(Number(x)));
    return true;
  }
  if ((schema.type === 'object' || schema.properties?.length) && schema.properties?.length) {
    if (typeof value !== 'object' || Array.isArray(value)) return false;
    const record = value as Record<string, unknown>;
    const required = schema.properties.filter((p) => p.required === true || p.required_flag === 'E');
    return required.every((p) => schemaValueComplete(p, record[p.id])) && missingAnyGroups(schema, record).length === 0;
  }
  if (schema.type === 'number') return typeof value !== 'boolean' && (typeof value === 'number' || (typeof value === 'string' && value.trim() !== '')) && Number.isFinite(Number(value)) && (schema.min === undefined || Number(value) >= schema.min) && (schema.max === undefined || Number(value) <= schema.max);
  return true;
}

export function fieldAnswerComplete(field: SchemaField, answer: FieldAnswer): boolean {
  if (answer.status === 'not_known_yet' || answer.not_known_yet === true) return true;
  if (answer.status !== 'filled' || !fieldValueValid(field, answer.value)) return false;
  const value = answer.value && typeof answer.value === 'object' ? answer.value as Record<string, unknown> : {};
  const required = field.properties?.filter((property) => property.required === true || property.required_flag === 'E') ?? [];
  if (field.properties?.length && !required.every((property) => schemaValueComplete(property, value[property.id]))) return false;
  if (field.properties?.length && missingAnyGroups(field, value).length) return false;
  const minimum = field.min_filled_properties ?? 0;
  const filled = Object.values(value).filter((part) => part !== undefined && part !== null && (typeof part !== 'string' || !!part.trim()) && (!Array.isArray(part) || part.length > 0)).length;
  if (minimum && filled < minimum) return false;
  if (field.type === 'array' && !schemaValueComplete({ type: 'array', required: field.required, items: field.items }, answer.value)) return false;
  if (field.type === 'object' && !schemaValueComplete(field, answer.value)) return false;
  return field.properties?.length ? true : answerComplete(answer);
}

/** A range stored before the one-value rule (owner 2026-10-25: every field holds one number, the maximum allowed /
 *  worst case for modelling) reads as its maximum; the minimum only when no maximum was given. Null for a plain value. */
export function rangeWorst(shape: { allow_range?: boolean }, value: unknown): { worst: number | ''; min: unknown; max: unknown } | null {
  if (!shape.allow_range || !value || typeof value !== 'object' || Array.isArray(value)) return null;
  const r = value as Record<string, unknown>;
  const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : typeof v === 'string' && v.trim() && Number.isFinite(Number(v)) ? Number(v) : null);
  return { worst: num(r.maximum) ?? num(r.minimum) ?? '', min: r.minimum, max: r.maximum };
}

/** A range-capable value keeps the range editor unless it is a single number (stored ranges are never flattened). */
export function usesRangeEditor(shape: { allow_range?: boolean }, value: unknown): boolean {
  return !!shape.allow_range && !(typeof value === 'number' || (typeof value === 'string' && !!value.trim()));
}

/** The "not known yet" toggle: the stored value is always kept, only the status changes. */
export function answerWithUnknown(field: SchemaField, old: FieldAnswer, unknown: boolean): FieldAnswer {
  return { ...old, status: unknown ? 'not_known_yet' : fieldAnswerComplete(field, { value: old.value, status: 'filled' }) ? 'filled' : 'missing' };
}

export function missionProfileComplete(draft: SpecDraft): boolean {
  const rows = draft.mission_profile ?? [];
  return rows.length > 0 && rows.every((row) => typeof row.phase === 'string' && !!row.phase.trim() && ['torque_Nm', 'speed_rpm'].every((key) => typeof row[key] === 'number' && Number.isFinite(row[key])) && ['duration_s', 'battery_voltage_V'].every((key) => typeof row[key] === 'number' && Number.isFinite(row[key]) && row[key] >= 0));
}

export interface SectionAnswerCounts { answered: number; unknown: number; missing: number; invalid: number; required: number; filled: number }
export function sectionAnswerCounts(section: SchemaSection, draft: SpecDraft): SectionAnswerCounts {
  const counts: SectionAnswerCounts = { answered: 0, unknown: 0, missing: 0, invalid: 0, required: 0, filled: 0 };
  for (const field of (section.fields ?? []).filter((f) => fieldRequired(f, draft) && !f.handled_by)) {
    counts.required++;
    if (field.id === 'mission_profile') {
      if (draft.mission_profile_status === 'not_known_yet') counts.unknown++;
      else if (missionProfileComplete(draft)) counts.answered++;
      else if ((draft.mission_profile?.length ?? 0) > 0) counts.invalid++;
      else counts.missing++;
      continue;
    }
    const source = answerNamespace(section, field) === 'production' ? draft.production as Record<string, FieldAnswer> | undefined : draft.fields;
    const raw = source?.[field.id];
    const answer = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw as FieldAnswer : { value: raw };
    if (answer.status === 'not_known_yet' || answer.not_known_yet === true) counts.unknown++;
    else if (answer.status === 'missing') counts.missing++;
    else if (fieldAnswerComplete(field, answer)) counts.answered++;
    else if (answer.value !== undefined && answer.value !== null && answer.value !== '') counts.invalid++;
    else counts.missing++;
  }
  counts.filled = counts.answered + counts.unknown;
  return counts;
}
export function sectionCompleteness(section: SchemaSection, draft: SpecDraft): { filled: number; required: number } {
  const counts = sectionAnswerCounts(section, draft);
  return { required: counts.required, filled: counts.filled };
}
export function wizardCompleteness(sections: SchemaSection[], draft: SpecDraft): Completeness {
  const counts = Object.fromEntries(sections.map((section) => { const count = sectionCompleteness(section, draft); return [section.id, { ...count, total: count.required, complete: count.filled === count.required }]; }));
  const answerCounts = sections.map((section) => sectionAnswerCounts(section, draft));
  const unknown = answerCounts.reduce((n, c) => n + c.unknown, 0);
  const invalid = answerCounts.reduce((n, c) => n + c.invalid, 0);
  const answered = answerCounts.reduce((n, c) => n + c.answered, 0);
  const filled = Object.values(counts).reduce((n, c) => n + c.filled, 0);
  const required = Object.values(counts).reduce((n, c) => n + c.required, 0);
  return { filled, required, filled_required: filled, total_required: required, answered, unknown, missing: Math.max(0, required - answered - unknown - invalid), invalid, sections: counts };
}
export function requiredFieldIds(sections: SchemaSection[], draft: SpecDraft): string[] {
  return sections.flatMap((s) => (s.fields ?? []).filter((f) => fieldRequired(f, draft) && !f.handled_by).filter((f) => {
    if (f.id === 'mission_profile') return draft.mission_profile_status !== 'not_known_yet' && !(draft.mission_profile_status === 'filled' && missionProfileComplete(draft));
    const src = answerNamespace(s, f) === 'production' ? draft.production as Record<string, FieldAnswer> | undefined : draft.fields;
    const raw = src?.[f.id];
    return !fieldAnswerComplete(f, raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : { value: raw });
  }).map((f) => f.id));
}
export function shallowDiff(a: unknown, b: unknown): Array<{ path: string; before: unknown; after: unknown }> {
  const out: Array<{ path: string; before: unknown; after: unknown }> = [];
  const walk = (x: unknown, y: unknown, prefix: string) => {
    if (JSON.stringify(x) === JSON.stringify(y)) return;
    if (x && y && typeof x === 'object' && typeof y === 'object' && !Array.isArray(x) && !Array.isArray(y)) {
      const keys = new Set([...Object.keys(x as object), ...Object.keys(y as object)]);
      for (const key of keys) walk((x as Record<string, unknown>)[key], (y as Record<string, unknown>)[key], prefix ? `${prefix}.${key}` : key);
    } else out.push({ path: prefix || 'specification', before: x, after: y });
  };
  walk(a, b, ''); return out;
}

/** Counts each required specification field as answered, explicitly unknown, or missing. */
export function sectionStatus(section: SchemaSection, draft: SpecDraft): 'complete' | 'unknown' | 'missing' {
  const c = sectionAnswerCounts(section, draft);
  return c.missing || c.invalid ? 'missing' : c.unknown ? 'unknown' : 'complete';
}

export function groupSpecificationSections(source: SchemaSection[], groups: Array<{ id: string; title: string; fieldIds: string[] }>): SchemaSection[] {
  const allFields = source.flatMap((section) => section.fields ?? []);
  const production = source.find((section) => section.id === 'production' || section.production);
  const assigned = new Set<string>();
  const laidOut = groups.map((group) => {
    const fields = group.fieldIds.map((id) => allFields.find((field) => field.id === id)).filter((field): field is SchemaField => !!field);
    fields.forEach((field) => assigned.add(field.id));
    return { id: group.id, title: group.title, fields, production: false } as SchemaSection;
  });
  const delivery = laidOut.find((section) => section.id === 'delivery');
  if (delivery && production) {
    delivery.fields = [...(production.fields ?? [])];
    delivery.production = true;
    for (const field of production.fields ?? []) assigned.add(field.id);
  }
  for (const section of source) {
    if (section === production) continue;
    const extras = (section.fields ?? []).filter((field) => !assigned.has(field.id) && field.id !== '1_3' && field.id !== 'mission_profile' && !field.handled_by);
    if (!extras.length) continue;
    const matching = laidOut.find((target) => target.fields?.some((field) => (section.fields ?? []).some((candidate) => candidate.id === field.id)));
    if (matching) { matching.fields = [...(matching.fields ?? []), ...extras]; extras.forEach((field) => assigned.add(field.id)); }
  }
  return [...laidOut, { id: 'review', title: 'wizard.review', fields: [], kind: 'review' }];
}

/** Semantic diff includes wrapper status and values while ignoring object key order. */
export function specificationDiff(a: unknown, b: unknown): Array<{ path: string; before: unknown; after: unknown }> {
  const normalize = (draft: unknown): unknown => {
    if (!draft || typeof draft !== 'object' || Array.isArray(draft)) return draft;
    const root = structuredClone(draft as Record<string, unknown>);
    if (Array.isArray(root.mission_profile)) root.mission_profile = Object.fromEntries((root.mission_profile as Array<Record<string, unknown>>).map((row, index) => [String(row.row_id ?? `legacy-${index}`), row]));
    return root;
  };
  return shallowDiff(normalize(a), normalize(b));
}


/** Serializes draft writes, rejects stale project tokens, and advances revision only after a successful save. */
export function createDraftSaveCoordinator<T>(save: (project: string, draft: T, revision: number) => Promise<{ revision: number }>) {
  let queue: Promise<void> = Promise.resolve();
  const revisions = new Map<string, number>();
  return {
    setRevision(project: string, revision: number) { revisions.set(project, revision); },
    revision(project: string) { return revisions.get(project) ?? 0; },
    enqueue(project: string, draft: T, isCurrent: () => boolean) {
      const task = queue.catch(() => {}).then(async () => {
        if (!isCurrent()) return;
        const result = await save(project, draft, revisions.get(project) ?? 0);
        if (isCurrent()) revisions.set(project, result.revision);
      });
      queue = task;
      return task;
    },
    async flush() { await queue; },
    submit<R extends { revision?: number }>(project: string, draft: T, isCurrent: () => boolean, send: (revision: number) => Promise<R>): Promise<R> {
      const task = queue.catch(() => {}).then(async () => {
        if (!isCurrent()) throw new Error('Project changed before submit');
        const saved = await save(project, draft, revisions.get(project) ?? 0);
        if (isCurrent()) revisions.set(project, saved.revision);
        if (!isCurrent()) throw new Error('Project changed before submit');
        const result = await send(revisions.get(project) ?? saved.revision);
        if (isCurrent() && typeof result.revision === 'number') revisions.set(project, result.revision);
        return result;
      });
      queue = task.then(() => {}, () => {});
      return task;
    },
  };
}
