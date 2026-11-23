// The controller specification (controller PR2): its schema is a data file on the server (`controller_schema.json`); this
// module is the pure part of the form - reading and writing one answer, per build or not, and what a save changed.
const API = (import.meta.env?.VITE_API_URL ?? 'http://localhost:8001') as string;

export type Lang = { en: string; 'zh-CN'?: string };
export type ValueStatus = 'requirement' | 'model' | 'measured';
export type FieldKind = 'text' | 'longtext' | 'number' | 'range' | 'choice' | 'multi' | 'list' | 'thresholds' | 'rows';
export interface CtlOption { value: string; label: Lang }
export interface CtlField {
  id: string; label: Lang; kind: FieldKind; unit?: string; per_build?: boolean; tip?: Lang;
  options?: CtlOption[]; columns?: { id: string; label: Lang; unit?: string }[];
}
export interface CtlSchema { schema_version: number; title: Lang; statuses: CtlOption[]; sections: { id: string; title: Lang; fields: CtlField[] }[] }
export interface CtlAnswer { value: unknown; status?: ValueStatus | null; ref?: string }
export type CtlStored = CtlAnswer | { builds: Record<string, CtlAnswer> };
export interface CtlChange { field: string; before: unknown; after: unknown }
export interface CtlSpec {
  schema_version: number; revision: number; updated_at: string | null; updated_by: string | null;
  fields: Record<string, CtlStored>; schema: CtlSchema; can_edit: boolean;
  history: { revision: number; at: string; by: string; changes: CtlChange[] }[]; changed?: string[];
}

export const localized = (l: Lang | undefined, lang: string): string => (l ? (lang.startsWith('zh') && l['zh-CN']) || l.en : '');

/** The kinds whose value carries a status (requirement / model / measured) and a source reference. */
export const hasStatus = (kind: FieldKind): boolean => kind === 'number' || kind === 'range';

/** The builds named in the Supply step ("6S", "12S" ...): the per-build fields hold one answer for each. */
export function buildsOf(fields: Record<string, CtlStored>): string[] {
  const v = (fields.builds as CtlAnswer | undefined)?.value;
  return Array.isArray(v) ? v.filter((b): b is string => typeof b === 'string' && !!b.trim()) : [];
}

/** One answer: the field's own, or the one of `build` for a per-build field. */
export function answerOf(fields: Record<string, CtlStored>, field: CtlField, build?: string): CtlAnswer {
  const stored = fields[field.id];
  if (field.per_build) return (stored && 'builds' in stored ? stored.builds[build ?? ''] : undefined) ?? { value: null };
  return (stored && !('builds' in stored) ? stored : undefined) ?? { value: null };
}

/** A copy of `fields` with one answer replaced (per build when the field is per build). */
export function withAnswer(fields: Record<string, CtlStored>, field: CtlField, answer: CtlAnswer, build?: string): Record<string, CtlStored> {
  if (!field.per_build) return { ...fields, [field.id]: answer };
  const stored = fields[field.id];
  const builds = stored && 'builds' in stored ? stored.builds : {};
  return { ...fields, [field.id]: { builds: { ...builds, [build ?? '']: answer } } };
}

/** What is sent: per-build answers only for the builds that exist now (a removed build drops its answers). */
export function payloadFields(fields: Record<string, CtlStored>, schema: CtlSchema): Record<string, CtlStored> {
  const builds = new Set(buildsOf(fields));
  const perBuild = new Set(schema.sections.flatMap((s) => s.fields).filter((f) => f.per_build).map((f) => f.id));
  const out: Record<string, CtlStored> = {};
  for (const [id, stored] of Object.entries(fields)) {
    if (perBuild.has(id) && stored && 'builds' in stored) {
      out[id] = { builds: Object.fromEntries(Object.entries(stored.builds).filter(([b]) => builds.has(b))) };
    } else if (!perBuild.has(id)) out[id] = stored;
  }
  return out;
}

/** A number typed in a field: `null` for empty, the number, or `undefined` for text that is not a number (kept out). */
export function parseNumber(text: string): number | null | undefined {
  const t = text.trim().replace(',', '.');
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : undefined;
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, { method, headers: body === undefined ? undefined : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await r.json().catch(() => null);
  if (!r.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    const message = typeof detail === 'string' ? detail : typeof (detail as { message?: unknown } | null)?.message === 'string' ? (detail as { message: string }).message : `HTTP ${r.status}`;
    throw Object.assign(new Error(message), { status: r.status });
  }
  return data as T;
}
const at = (id: string) => `/api/projects/${encodeURIComponent(id)}/controller-spec`;
export const controllerSpecApi = {
  get: (projectId: string) => call<CtlSpec>('GET', at(projectId)),
  save: (projectId: string, fields: Record<string, CtlStored>, revision: number) => call<CtlSpec>('PUT', at(projectId), { fields, revision }),
};

/** The form after a successful save (Astra round 1 of #832): fields AND revision from the SAME answer, so the next save is
 *  checked against what this form really holds.  An edit typed while the save was in flight is kept on top (still unsaved). */
export function afterSave(local: Record<string, CtlStored>, saved: Pick<CtlSpec, 'fields' | 'revision'>, editedWhileSaving: boolean):
  { fields: Record<string, CtlStored>; revision: number; dirty: boolean } {
  return editedWhileSaving
    ? { fields: local, revision: saved.revision, dirty: true }
    : { fields: saved.fields ?? {}, revision: saved.revision, dirty: false };
}

/** Is this error the server's "somebody saved in between" (409)? */
export const isConflict = (e: unknown): boolean => (e as { status?: unknown } | null)?.status === 409;
