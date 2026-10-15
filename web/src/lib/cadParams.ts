// Geometry tab "Full": the editable CAD-only construction table - types and the pure logic (drafts, validation, rebuild badge).
// The server (motor_ai_sim/cad_params.py, routes/construction.py) is the authority; this only keeps the form honest and
// tells the person what is going on, in the same words and units.

import type { PartNames } from './partTree';

export interface CadRow {
  name: string;
  /** short human-readable label ("Glass-fibre ring - thickness"); the technical template name is `name` */
  label: PartNames;
  /** false = shown read-only (bearing internals come with the bearing card) */
  editable: boolean;
  group: string;
  unit: '' | 'mm' | 'deg';
  kind: 'float' | 'int';
  min: number;
  max: number;
  tip: PartNames;
  cad_only: boolean;
  /** value without any override (the reference) */
  default: number;
  /** value in the model on the server */
  built: number;
  /** value with the saved override: what the next build uses */
  value: number;
  overridden: boolean;
}

export type RebuildState = 'none' | 'current' | 'pending' | 'building' | 'failed';

export interface RebuildStatus {
  state: RebuildState;
  stale: boolean;
  requested: boolean;
  message?: string;
  layer?: string;
  requested_at?: string | null;
  requested_by?: string | null;
}

export interface BearingChoice { id: string; d: number; D: number; B: number; description?: string }

export interface CadParamsData {
  available: boolean;
  reason?: string;
  can_write: boolean;
  rows: CadRow[];
  groups: Array<{ id: string; name: PartNames }>;
  rebuild: RebuildStatus;
  bearing?: { override: string | null; config_card: string | null; model: Record<string, number>; choices: BearingChoice[] };
  model?: { motor_id?: string; built_at?: string; template?: string };
  cad_only_label?: PartNames;
  read_only_note?: PartNames;
}

/** A typed value ("1,5" and "1.5" both work); null when it is not a finite number. */
export function parseDraft(text: string): number | null {
  const s = String(text ?? '').trim().replace(',', '.');
  if (!s || !/^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(s)) return null;
  const x = Number(s);
  return Number.isFinite(x) ? x : null;
}

export type DraftError = 'nan' | 'int' | 'range';

/** What is wrong with a typed value, before the server says it again (the template pre-check stays the server's). */
export function draftError(row: Pick<CadRow, 'kind' | 'min' | 'max'>, text: string): DraftError | null {
  const x = parseDraft(text);
  if (x === null) return 'nan';
  if (row.kind === 'int' && Math.abs(x - Math.round(x)) > 1e-9) return 'int';
  if (x < row.min - 1e-12 || x > row.max + 1e-12) return 'range';
  return null;
}

/** The edit to send: only rows whose typed value differs from the saved one; a value equal to the reference is a reset (null). */
export function changesOf(rows: readonly CadRow[], drafts: Readonly<Record<string, string>>): Record<string, number | null> {
  const out: Record<string, number | null> = {};
  for (const r of rows) {
    if (!r.editable) continue;
    const txt = drafts[r.name];
    if (txt === undefined) continue;
    const x = parseDraft(txt);
    if (x === null || Math.abs(x - r.value) <= 1e-9) continue;
    out[r.name] = Math.abs(x - r.default) <= 1e-9 ? null : x;
  }
  return out;
}

/** Names of the rows with a typed value that cannot be sent. */
export function invalidDrafts(rows: readonly CadRow[], drafts: Readonly<Record<string, string>>): string[] {
  return rows.filter((r) => drafts[r.name] !== undefined && draftError(r, drafts[r.name]) !== null).map((r) => r.name);
}

/** The model badge: 'current' shows nothing; a stale model keeps being shown, with a badge: "rebuilding" (the build runs), "queued"
 *  (a rebuild is requested and waits for the CAD runtime), "stale" (nothing requested yet - the person can request it), "failed". */
export type ModelBadge = 'none' | 'rebuilding' | 'queued' | 'stale' | 'failed';

export function modelBadge(s: RebuildStatus | null | undefined): ModelBadge {
  if (!s || s.state === 'none' || s.state === 'current') return 'none';
  if (s.state === 'building') return 'rebuilding';
  if (s.state === 'failed') return 'failed';
  return s.requested ? 'queued' : 'stale';
}

/** Should the panel keep polling the rebuild status (the new model is on its way)? */
export const shouldPoll = (s: RebuildStatus | null | undefined): boolean =>
  !!s && (s.state === 'building' || (s.state === 'pending' && s.requested));

/** Rows of a group, in the server's order. */
export function rowsOfGroup(rows: readonly CadRow[], group: string): CadRow[] {
  return rows.filter((r) => r.group === group);
}

/** The violations of a refused save (the server answers {detail: {error, violations}}), or the plain detail text. */
export function refusalLines(detail: unknown): string[] {
  if (typeof detail === 'string') return [detail];
  if (detail && typeof detail === 'object') {
    const d = detail as { error?: unknown; violations?: unknown };
    const v = Array.isArray(d.violations) ? d.violations.map(String) : [];
    return v.length ? v : (typeof d.error === 'string' ? [d.error] : []);
  }
  return [];
}

/** "12.5" for a length, "10" for a count: no trailing zeros, at most 4 decimals. */
export function formatValue(x: number): string {
  if (!Number.isFinite(x)) return '';
  return String(Number(x.toFixed(4)));
}
