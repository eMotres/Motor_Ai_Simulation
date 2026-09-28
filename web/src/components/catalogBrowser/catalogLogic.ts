/**
 * The common catalogue browser's LOGIC — search, filters, provenance badges,
 * the compare table.  Pure and import-free (types only), so
 * `__tests__/catalogLogic.test.mjs` loads this very module under Node's type
 * stripping — no verbatim copy to drift from it.
 *
 * The shapes are what `GET /api/catalog/cards/...` sends
 * (src/motor_ai_sim/catalog/envelope.py): one ENVELOPE around each kind's body.
 */

export type CatalogKind = 'bearing' | 'lubricant' | 'device';
export type CardStatus = 'draft' | 'active' | 'validated' | 'deprecated';
export type ProvType = 'datasheet' | 'measured' | 'estimate' | 'derived';

export interface CardSource {
  id: string; doc?: string; url?: string | null; file?: string | null;
  page?: string; rev?: string; read?: string;
}

export interface FieldProv {
  type?: ProvType; src?: string | null; verify?: boolean; note?: string;
  default?: boolean;
}

export interface CardSummary {
  id: string; kind: CatalogKind;
  manufacturer?: string | null; part_number?: string | null;
  description?: string | null; status?: CardStatus;
  flags?: { n_verify: number; n_estimate: number; n_measured: number };
  has_validation?: boolean;
  cols?: Record<string, unknown>;
  error?: string;
}

export interface CardEnvelope extends CardSummary {
  sources: CardSource[];
  units: Record<string, string>;
  body: Record<string, unknown>;
  prov: Record<string, FieldProv>;
  validation: Array<Record<string, unknown>>;
  revision: { n?: number; date?: string; by?: string; why?: string };
  file?: string | null;
}

export interface CatalogFilter {
  q?: string;
  manufacturer?: string;   // '' = any
  status?: string;         // '' = any
  type?: string;           // '' = any; bearing type / lubricant kind / device technology
}

/** Case-insensitive match over id, part number, manufacturer, description and
 *  the kind's type column.  Every filter left empty passes. */
export function filterCards<T extends CardSummary>(cards: T[], f: CatalogFilter): T[] {
  const q = (f.q ?? '').trim().toLowerCase();
  return cards.filter((c) => {
    if (f.manufacturer && (c.manufacturer ?? '') !== f.manufacturer) return false;
    if (f.status && (c.status ?? '') !== f.status) return false;
    if (f.type && String(c.cols?.type ?? '') !== f.type) return false;
    if (!q) return true;
    const hay = [c.id, c.part_number, c.manufacturer, c.description,
                 c.cols?.type, c.cols?.package]
      .map((v) => String(v ?? '').toLowerCase()).join(' ');
    return q.split(/\s+/).every((w) => hay.includes(w));
  });
}

/** The distinct, sorted values of one facet — the filter dropdown's options. */
export function facet(cards: CardSummary[], key: 'manufacturer' | 'status' | 'type'): string[] {
  const vals = cards.map((c) => (key === 'type' ? c.cols?.type : c[key]))
    .filter((v) => v != null && v !== '').map(String);
  return Array.from(new Set(vals)).sort();
}

/** D / M / E / ∂ — the proposal's badge letters; '' for an unknown type. */
export function provBadge(p: FieldProv | undefined | null): string {
  switch (p?.type ?? 'datasheet') {
    case 'datasheet': return 'D';
    case 'measured': return 'M';
    case 'estimate': return 'E';
    case 'derived': return '∂';
    default: return '';
  }
}

/** The provenance of one dotted field, the envelope's default rule applied:
 *  no entry = datasheet from the FIRST source. */
export function provOf(env: Pick<CardEnvelope, 'prov' | 'sources'>, field: string): FieldProv {
  const p = env.prov?.[field];
  if (p) return p;
  return { type: 'datasheet', src: env.sources?.[0]?.id ?? null, default: true };
}

/** A block-level prov answers for its fields (a device's `r_ds_on` note
 *  covers `r_ds_on.curves`). Walks the dotted path upward. */
export function provFor(env: Pick<CardEnvelope, 'prov' | 'sources'>, field: string): FieldProv {
  const parts = field.split('.');
  for (let i = parts.length; i > 0; i--) {
    const p = env.prov?.[parts.slice(0, i).join('.')];
    if (p) return p;
  }
  return provOf(env, field);
}

/** The body's fields, one nesting level deep, as `[dotted name, value]` —
 *  the granularity `prov` is written at.  Lists and deep blocks stay whole. */
export function flattenBody(body: Record<string, unknown>, depth = 1): Array<[string, unknown]> {
  const out: Array<[string, unknown]> = [];
  const walk = (o: Record<string, unknown>, prefix: string, d: number) => {
    for (const [k, v] of Object.entries(o)) {
      if (v && typeof v === 'object' && !Array.isArray(v) && d > 0) {
        walk(v as Record<string, unknown>, `${prefix}${k}.`, d - 1);
      } else {
        out.push([`${prefix}${k}`, v]);
      }
    }
  };
  walk(body ?? {}, '', depth);
  return out;
}

/** One value as the card shows it: numbers compact, null = "not published". */
export function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) return String(v);
    const a = Math.abs(v);
    return (a !== 0 && (a >= 1e6 || a < 1e-3)) ? v.toExponential(3).replace('e+', 'e')
      : String(Number(v.toPrecision(6)));
  }
  if (typeof v === 'string') return v;
  const s = JSON.stringify(v);
  return s.length > 120 ? `${s.slice(0, 117)}…` : s;
}

export interface CompareRow { field: string; values: string[]; differs: boolean }

/** 2–3 cards side by side: the union of their scalar fields, in first-seen
 *  order, with `differs` when the shown values are not all equal.  Long
 *  descriptive text (description/note) is left out — it always differs. */
export function compareRows(envs: Array<Pick<CardEnvelope, 'body'>>,
                            skip: string[] = ['description', 'note', 'notes']): CompareRow[] {
  const order: string[] = [];
  const maps = envs.map((e) => {
    const m = new Map<string, unknown>();
    for (const [k, v] of flattenBody(e.body)) {
      if (skip.includes(k)) continue;
      if (v !== null && typeof v === 'object') continue;   // curves/tables: not a cell
      m.set(k, v);
      if (!order.includes(k)) order.push(k);
    }
    return m;
  });
  return order.map((field) => {
    const values = maps.map((m) => (m.has(field) ? fmtValue(m.get(field)) : '—'));
    return { field, values, differs: new Set(values).size > 1 };
  });
}

/** Toggle one id in the compare set, never more than `max` (the oldest drops). */
export function toggleCompare(ids: string[], id: string, max = 3): string[] {
  if (ids.includes(id)) return ids.filter((x) => x !== id);
  const next = [...ids, id];
  return next.length > max ? next.slice(next.length - max) : next;
}

/** The link a source opens: its URL, else nothing (a local file path is shown
 *  as text — the app serves no repo documents). */
export function sourceHref(s: CardSource | undefined | null): string | null {
  const u = s?.url ?? null;
  return u && /^https?:\/\//i.test(u) ? u : null;
}
