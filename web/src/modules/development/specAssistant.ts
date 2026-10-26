// Specification assistant (owner 2026-10-27): the pure logic behind the side panel - proposal cards, the value
// text with units, the "recalculations" line, who sees Apply, the chat history sent to the server and the Apply error
// mapping.  No React and no fetch here, so `node --test` covers it (synthetic data only).
import type { DevelopmentSchema, Localized, SchemaField, SchemaProperty } from './types';

export type Lang = 'en' | 'zh-CN';
export interface ProposalRecalc { needed: boolean; kind: 'thermal_mechanics' | 'mechanics' | 'none'; text: { en: string; 'zh-CN': string } }
export interface Proposal {
  id: string; field_id: string; value: unknown; reason: string; recalc: ProposalRecalc; by: string; created_at: string;
  status: 'open' | 'applied' | 'dismissed'; applied_by?: string | null; applied_revision?: number | null;
  current?: { value: unknown; status: string };
}
export interface ChatMessage { role: 'user' | 'assistant'; content: string }
type Obj = Record<string, unknown>;

export const langOf = (raw: string | undefined | null): Lang => String(raw ?? '').toLowerCase().startsWith('zh') ? 'zh-CN' : 'en';
export const localized = (value: Localized | undefined, lang: Lang): string => {
  if (typeof value === 'string') return value;
  return value?.[lang] ?? value?.en ?? '';
};

const isObj = (v: unknown): v is Obj => !!v && typeof v === 'object' && !Array.isArray(v);

export function findField(schema: DevelopmentSchema | null | undefined, fieldId: string): SchemaField | undefined {
  for (const section of schema?.sections ?? []) for (const f of section.fields ?? []) if (f.id === fieldId) return f;
  return undefined;
}

export function fieldLabel(schema: DevelopmentSchema | null | undefined, fieldId: string, lang: Lang): string {
  const f = findField(schema, fieldId);
  return f ? (localized(f.short_label, lang) || localized(f.label, lang) || fieldId) : fieldId;
}

const optionLabel = (options: SchemaField['options'] | undefined, value: unknown, lang: Lang): string => {
  for (const o of options ?? []) {
    if (typeof o === 'string') { if (o === value) return o; continue; }
    if (o.value === value) return localized(o.label, lang) || String(o.value);
  }
  return String(value);
};

function num(v: unknown): string {
  return typeof v === 'number' && Number.isFinite(v) ? String(Math.round(v * 1e6) / 1e6) : String(v);
}

function scalarText(v: unknown, unit: string | undefined, lang: Lang, options?: SchemaField['options']): string {
  if (v === null || v === undefined || v === '') return '-';
  if (isObj(v) && ('minimum' in v || 'maximum' in v)) {
    const body = `${v.minimum ?? '?'}–${v.maximum ?? '?'}`;
    return unit ? `${body} ${unit}` : body;
  }
  if (typeof v === 'boolean') return lang === 'zh-CN' ? (v ? '是' : '否') : (v ? 'yes' : 'no');
  const text = options?.length ? optionLabel(options, v, lang) : typeof v === 'number' ? num(v) : String(v);
  return unit && typeof v === 'number' ? `${text} ${unit}` : text;
}

/** One line for a stored / proposed value: "Balance grade: G2.5; Runout: 0.05 mm". */
export function formatProposalValue(schema: DevelopmentSchema | null | undefined, fieldId: string, value: unknown, lang: Lang): string {
  const field = findField(schema, fieldId);
  if (value === null || value === undefined) return '-';
  if (Array.isArray(value)) {
    const items = field?.items?.properties;
    return value.map((row) => (isObj(row) && items ? objectText(row, items, lang) : String(row))).join(' | ') || '-';
  }
  if (isObj(value) && field?.properties && !('minimum' in value)) return objectText(value, field.properties, lang);
  return scalarText(value, field?.unit, lang, field?.options);
}

function objectText(value: Obj, props: SchemaProperty[], lang: Lang): string {
  const byId = new Map(props.map((p) => [p.id, p]));
  const parts: string[] = [];
  for (const [key, v] of Object.entries(value)) {
    const prop = byId.get(key);
    if (prop?.ui_hidden || v === null || v === undefined || v === '') continue;
    const label = prop ? (localized(prop.short_label, lang) || localized(prop.label, lang) || key) : key;
    parts.push(`${label}: ${scalarText(v, prop?.unit, lang, prop?.options)}`);
  }
  return parts.join('; ') || '-';
}

/** The "recalculations" text of a proposal in the UI language (the server sends both). */
export function recalcText(p: Pick<Proposal, 'recalc'>, lang: Lang): string {
  return p.recalc?.text?.[lang] ?? p.recalc?.text?.en ?? '';
}

export interface ProposalCard {
  id: string; fieldId: string; title: string; proposed: string; current: string | null; reason: string;
  recalc: string; recalcNeeded: boolean;
}

export function cardView(p: Proposal, schema: DevelopmentSchema | null | undefined, lang: Lang): ProposalCard {
  const filled = p.current && p.current.status === 'filled';
  return {
    id: p.id, fieldId: p.field_id, title: fieldLabel(schema, p.field_id, lang),
    proposed: formatProposalValue(schema, p.field_id, p.value, lang),
    current: filled ? formatProposalValue(schema, p.field_id, p.current!.value, lang) : null,
    reason: p.reason, recalc: recalcText(p, lang), recalcNeeded: !!p.recalc?.needed,
  };
}

/** What a card offers the viewer: spec editors Apply / Dismiss, everybody else a hint (owner 2026-10-27). */
export type CardAction = 'apply' | 'owner' | 'none';
export function cardAction(p: Pick<Proposal, 'status'>, canApply: boolean): CardAction {
  if (p.status !== 'open') return 'none';
  return canApply ? 'apply' : 'owner';
}

/** Open proposals only, oldest first; a closed one never shows as a card. */
export const openProposals = (rows: Proposal[]): Proposal[] =>
  rows.filter((p) => p.status === 'open').sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));

/** The chat history the server accepts: alternating roles from a user turn, bounded (the server cuts it again). */
export function historyForServer(messages: ChatMessage[], max = 20): ChatMessage[] {
  const out = messages.filter((m) => (m.role === 'user' || m.role === 'assistant') && m.content.trim()).slice(-max);
  while (out.length && out[0].role !== 'user') out.shift();
  return out;
}

/** An Apply failure as a translation key + the revision the server reports, so the panel can offer a reload. */
export function applyFailure(status: number, detail: unknown): { key: string; reload: boolean } {
  if (status === 409) {
    const msg = isObj(detail) ? String(detail.message ?? '') : String(detail ?? '');
    return /already/i.test(msg) ? { key: 'specAssistant.alreadyClosed', reload: false } : { key: 'specAssistant.stale', reload: true };
  }
  if (status === 403) return { key: 'specAssistant.ownerOnly', reload: false };
  if (status === 404) return { key: 'specAssistant.gone', reload: false };
  if (status === 422) return { key: 'specAssistant.invalid', reload: true };
  return { key: 'specAssistant.applyFailed', reload: false };
}
