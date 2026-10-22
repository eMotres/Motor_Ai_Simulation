import type { Completeness, SchemaField, SchemaSection } from './types';

export interface FieldAnswer { value?: unknown; status?: 'filled' | 'not_known_yet' | 'missing'; not_known_yet?: boolean }
export type SpecDraft = Record<string, unknown> & { fields?: Record<string, FieldAnswer>; mission_profile?: Array<Record<string, unknown>>; mission_profile_status?: 'filled' | 'not_known_yet' | 'missing' };
export function fieldAnswer(draft: SpecDraft, id: string): FieldAnswer {
  const value = draft.fields?.[id];
  return value && typeof value === 'object' && !Array.isArray(value) ? value as FieldAnswer : { value };
}
export function setFieldAnswer(draft: SpecDraft, id: string, answer: FieldAnswer): SpecDraft {
  return { ...draft, fields: { ...(draft.fields ?? {}), [id]: answer } };
}
export function isRequired(f: SchemaField): boolean { return f.required === true || f.required_flag === 'E'; }
export function answerComplete(a: FieldAnswer): boolean {
  if (a.status === 'not_known_yet' || a.not_known_yet === true) return true;
  if (a.value && typeof a.value === 'object' && !Array.isArray(a.value)) {
    const values = Object.values(a.value as Record<string, unknown>);
    return values.length > 0 && values.every((v) => v !== undefined && v !== null && String(v).trim() !== '');
  }
  return a.value !== undefined && a.value !== null && String(a.value).trim() !== '';
}

export function schemaValueComplete(schema: { type?: string; required?: boolean; allow_range?: boolean; properties?: Array<{ id: string; required?: boolean; required_flag?: string; type?: string; allow_range?: boolean; properties?: any[]; items?: any }>; items?: { type?: string; properties?: Array<{ id: string; required?: boolean; type?: string; allow_range?: boolean; properties?: any[]; items?: any }> }, min_filled_properties?: number }, value: unknown): boolean {
  if (value === undefined || value === null || (typeof value === 'string' && !value.trim())) return false;
  if (schema.allow_range && typeof value === 'object') {
    const bounds = value as Record<string, unknown>;
    return bounds.minimum != null && bounds.maximum != null && bounds.minimum !== '' && bounds.maximum !== '';
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
    return required.every((p) => schemaValueComplete(p, record[p.id]));
  }
  if (schema.type === 'number') return Number.isFinite(Number(value));
  return true;
}

export function fieldAnswerComplete(field: SchemaField, answer: FieldAnswer): boolean {
  if (answer.status === 'not_known_yet' || answer.not_known_yet === true) return true;
  if (answer.status !== 'filled') return false;
  const value = answer.value && typeof answer.value === 'object' ? answer.value as Record<string, unknown> : {};
  const required = field.properties?.filter((property) => property.required === true || property.required_flag === 'E') ?? [];
  if (field.properties?.length && !required.every((property) => schemaValueComplete(property, value[property.id]))) return false;
  const minimum = field.min_filled_properties ?? 0;
  const filled = Object.values(value).filter((part) => part !== undefined && part !== null && (typeof part !== 'string' || !!part.trim()) && (!Array.isArray(part) || part.length > 0)).length;
  if (minimum && filled < minimum) return false;
  if (field.type === 'array' && !schemaValueComplete({ type: 'array', required: field.required, items: field.items }, answer.value)) return false;
  if (field.type === 'object' && !schemaValueComplete(field, answer.value)) return false;
  return field.properties?.length ? true : answerComplete(answer);
}

export function missionProfileComplete(draft: SpecDraft): boolean {
  const rows = draft.mission_profile ?? [];
  return rows.length > 0 && rows.every((row) => ['phase', 'torque_Nm', 'speed_rpm', 'duration_s', 'battery_voltage_V'].every((key) => row[key] !== undefined && row[key] !== null && String(row[key]).trim() !== ''));
}

export function sectionCompleteness(section: SchemaSection, draft: SpecDraft): { filled: number; required: number } {
  const fields = (section.fields ?? []).filter((f) => isRequired(f) && !f.handled_by);
  return { required: fields.length, filled: fields.filter((f) => {
    if (f.id === 'mission_profile') return draft.mission_profile_status === 'not_known_yet' || (draft.mission_profile_status === 'filled' && missionProfileComplete(draft));
    const src = section.id === 'production' ? draft.production as Record<string, FieldAnswer> | undefined : draft.fields;
    const raw = src?.[f.id];
    const answer = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : { value: raw };
    return fieldAnswerComplete(f, answer);
  }).length };
}
export function wizardCompleteness(sections: SchemaSection[], draft: SpecDraft): Completeness {
  const counts = Object.fromEntries(sections.map((section) => { const count = sectionCompleteness(section, draft); return [section.id, { ...count, total: count.required, complete: count.filled === count.required }]; }));
  const totals = Object.values(counts).reduce((a, b) => ({ filled: a.filled + b.filled, required: a.required + b.required }), { filled: 0, required: 0 });
  return { ...totals, filled_required: totals.filled, total_required: totals.required, sections: counts };
}
export function requiredFieldIds(sections: SchemaSection[], draft: SpecDraft): string[] {
  return sections.flatMap((s) => (s.fields ?? []).filter((f) => isRequired(f) && !f.handled_by).filter((f) => {
    if (f.id === 'mission_profile') return draft.mission_profile_status !== 'not_known_yet' && !(draft.mission_profile_status === 'filled' && missionProfileComplete(draft));
    const src = s.id === 'production' ? draft.production as Record<string, FieldAnswer> | undefined : draft.fields;
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
