/** Project cost estimate (owner 2026-11-02, simplified the same day): ADMIN ONLY.  Each iteration has exactly four lines -
 *  tooling, motor manufacturing (one motor x the motors), testing, shipping (DHL).  Types and pure helpers of the wizard
 *  (steps, line edits, source chips, the review table), tested without a browser.  The schema is the server's
 *  `cost_estimate_schema.json`; totals are computed by the server and are PER CURRENCY. */

export type CostLang = 'en' | 'zh-CN';
export type Bilingual = Record<CostLang, string>;
export type LineId = 'tooling' | 'manufacturing' | 'testing' | 'shipping';
export const LINE_IDS: LineId[] = ['tooling', 'manufacturing', 'testing', 'shipping'];
export type LineStatus = 'filled' | 'missing';
export type LineSource = 'production' | 'estimate' | 'manual' | 'converted' | 'rule';
export type Bench = 'dyno' | 'propeller' | 'both' | 'none';
export type Money = Record<string, number>;

export interface CostField { id: string; type: 'bool' | 'int' | 'number' | 'enum'; min?: number; max?: number; options?: string[]; option_labels?: Record<string, Bilingual>; default?: unknown; label: Bilingual; help: Bilingual }
export interface CostLineDef { id: LineId; label: Bilingual; help: Bilingual; per_motor?: boolean; later_pct_default?: number }
export interface CostSchema {
  schema_version: number; currencies: string[]; default_currency: string; factor_limits: [number, number];
  settings: CostField[]; iteration_fields: CostField[]; lines: CostLineDef[];
}
export interface SourceRef { production?: string; project?: string; title?: string; iteration?: string; analog_mass_kg?: number; target_mass_kg?: number }
export interface CostLine {
  status: LineStatus; amount: number | null; currency: string; pct: number | null; source: LineSource | null;
  source_ref: SourceRef | null; factor: number | null; date: string | null; by: string | null; note: string; hint: string;
}
export interface CostIteration { index: number; motors: number; bench: Bench; lines: Record<LineId, CostLine> }
export interface CostSettings { complex: boolean; iterations: number; currency: string; contingency_pct: number; target_motor: { die: string; config: string } | null; [key: string]: unknown }
export interface CostMigration { from_version: number; date: string | null; dropped: string[]; note: string }
export interface CostEstimate {
  version: number; schema_version: number; project_id: string; revision: number; settings: CostSettings;
  iterations: CostIteration[]; migration: CostMigration | null; updated_at: string | null; updated_by: string | null;
}
export interface LineResult { amount: Money | null; origin: string }
export interface IterationResult {
  index: number; motors: number; bench: Bench; tooling: Money | null; manufacturing: Money | null; testing: Money | null; shipping: Money | null;
  subtotal: Money; lines: Record<LineId, LineResult>;
}
export interface MissingLine { iteration: number; line: LineId; reason: string }
export interface CostComputed {
  iterations: IterationResult[]; subtotal: Money; contingency_pct: number; contingency: Money; total: Money; currencies: string[];
  missing: MissingLine[]; missing_count: number; warnings: string[];
}
export interface CostSnapshot { id: string; n: number; note: string; created_at: string; created_by: string; revision: number; parent: string | null; totals: { total: Money; missing_count: number }; diff: Array<{ path: string; was: unknown; now: unknown }> }
export interface CostEstimateAnswer {
  estimate: CostEstimate; computed: CostComputed; schema: CostSchema; stored: boolean;
  linked_motors: Array<{ die: string; config: string; status: string }>;
  changes?: Array<{ path: string; was: unknown; now: unknown }>;
  report?: { filled_count: number; skipped: Array<{ iteration: number; line: LineId; reason: string }>; notes: string[];
    analogs: Record<'manufacturing' | 'tooling', { ref: string | null; factor?: number; reason?: string }>; target_mass_kg: number | null };
}

export type T = (key: string, args?: Record<string, unknown>) => string;
export const langOf = (language: string | undefined): CostLang => (String(language || '').toLowerCase().startsWith('zh') ? 'zh-CN' : 'en');
export const txt = (b: Bilingual | undefined, lang: CostLang): string => (b ? b[lang] || b.en || '' : '');

/** The wizard: Settings -> Iteration 1 -> Iteration 2 ... -> Review. */
export interface CostStep { id: string; kind: 'settings' | 'iteration' | 'review'; iteration?: number }

export function buildCostSteps(estimate: CostEstimate): CostStep[] {
  return [{ id: 'settings', kind: 'settings' }, ...estimate.iterations.map((it) => ({ id: `it${it.index}`, kind: 'iteration' as const, iteration: it.index })), { id: 'review', kind: 'review' }];
}

/** Lines that count: testing is not needed when the iteration has no bench. */
export const lineNeeded = (it: CostIteration, id: LineId): boolean => !(id === 'testing' && it.bench === 'none');

export type StepState = 'done' | 'partial' | 'empty';
export function stepState(step: CostStep, estimate: CostEstimate): { state: StepState; missing: number } {
  const it = estimate.iterations.find((x) => x.index === step.iteration);
  if (!it) return { state: 'empty', missing: 0 };
  const ids = LINE_IDS.filter((id) => lineNeeded(it, id));
  const missing = ids.filter((id) => it.lines[id].status === 'missing').length;
  return { state: missing === 0 ? 'done' : missing < ids.length ? 'partial' : 'empty', missing };
}

export interface LineRef { iteration: number; line: LineId }
const clone = (e: CostEstimate): CostEstimate => JSON.parse(JSON.stringify(e)) as CostEstimate;
const lineAt = (e: CostEstimate, ref: LineRef): CostLine | undefined => e.iterations.find((it) => it.index === ref.iteration)?.lines[ref.line];

export function blankLine(currency: string): CostLine {
  return { status: 'missing', amount: null, currency, pct: null, source: null, source_ref: null, factor: null, date: null, by: null, note: '', hint: '' };
}

/** The owner edits a line.  A number (amount or %) makes it `manual` - by him, today; clearing the amount makes it
 *  missing.  A note or a currency alone keeps where the number came from. */
export function editLine(e: CostEstimate, ref: LineRef, patch: { amount?: number | null; pct?: number | null; currency?: string; note?: string }, who: string, today: string): CostEstimate {
  const next = clone(e);
  const line = lineAt(next, ref);
  if (!line) return e;
  if (patch.note !== undefined) line.note = patch.note;
  if (patch.currency !== undefined) line.currency = patch.currency;
  const numeric = 'amount' in patch ? 'amount' : 'pct' in patch ? 'pct' : null;
  if (!numeric) return next;
  const value = (numeric === 'amount' ? patch.amount : patch.pct) ?? null;
  if (numeric === 'amount') { line.amount = value; line.pct = null; } else { line.pct = value; line.amount = null; }
  line.factor = null; line.source_ref = null; line.hint = '';
  if (value === null) { line.status = 'missing'; line.source = null; line.by = null; line.date = null; }
  else { line.status = 'filled'; line.source = 'manual'; line.by = who; line.date = today; }
  return next;
}

/** A later iteration's tooling: "% of iteration 1" (the default rule) or an amount. */
export function setToolingMode(e: CostEstimate, index: number, mode: 'pct' | 'amount', defaultPct: number): CostEstimate {
  const next = clone(e);
  const line = lineAt(next, { iteration: index, line: 'tooling' });
  if (!line || index < 2) return e;
  const note = line.note;
  Object.assign(line, blankLine(line.currency), { note });
  if (mode === 'pct') Object.assign(line, { status: 'filled', pct: defaultPct, source: 'rule' });
  return next;
}

export const toolingMode = (line: CostLine): 'pct' | 'amount' => (line.pct !== null ? 'pct' : 'amount');

export function defaultIteration(schema: CostSchema, settings: CostSettings, index: number): CostIteration {
  const field = (id: string) => schema.iteration_fields.find((f) => f.id === id)?.default;
  const pct = schema.lines.find((l) => l.id === 'tooling')?.later_pct_default ?? 20;
  const lines = Object.fromEntries(LINE_IDS.map((id) => [id, blankLine(settings.currency)])) as Record<LineId, CostLine>;
  if (index > 1) Object.assign(lines.tooling, { status: 'filled', pct, source: 'rule' });
  return { index, motors: Number(field('motors') ?? 2), bench: (field('bench') as Bench) ?? 'dyno', lines };
}

/** Settings; the iteration list follows the count at once (new iterations get the defaults, extra ones are cut);
 *  ticking "complex" makes it at least 3. */
export function setSetting(e: CostEstimate, schema: CostSchema, key: string, value: unknown): CostEstimate {
  const next = clone(e);
  (next.settings as Record<string, unknown>)[key] = value;
  if (key === 'complex' && value === true && next.settings.iterations < 3) next.settings.iterations = 3;
  const n = Math.max(1, Math.min(5, Math.round(Number(next.settings.iterations) || 1)));
  next.settings.iterations = n;
  next.iterations = Array.from({ length: n }, (_, i) => next.iterations.find((it) => it.index === i + 1) ?? defaultIteration(schema, next.settings, i + 1));
  return next;
}

export function setIterationField(e: CostEstimate, index: number, key: 'motors' | 'bench', value: number | Bench): CostEstimate {
  const next = clone(e);
  const it = next.iterations.find((x) => x.index === index);
  if (it) (it as unknown as Record<string, unknown>)[key] = value;
  return next;
}

/** "EUR 1 200.00 · CNY 300.00" - never one number over several currencies. */
export function formatMoney(m: Money | null | undefined, empty = '—'): string {
  const entries = Object.entries(m ?? {}).filter(([, v]) => Number.isFinite(v));
  if (!entries.length) return empty;
  return entries.map(([cur, v]) => `${cur} ${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).replace(/,/g, ' ')}`).join(' · ');
}

const num = (x: number | null | undefined): number => Number(Number(x ?? 0).toFixed(3));

/** The chip of a line: which source (or missing) and the one-line "where it came from" for its tooltip. */
export function originChip(line: CostLine, it: CostIteration, lineId: LineId, t: T): { kind: LineSource | 'missing' | 'none'; label: string; tip: string } {
  if (!lineNeeded(it, lineId)) return { kind: 'none', label: t('costEstimate.noBench'), tip: t('costEstimate.noBenchTip') };
  if (line.pct !== null) {
    const tip = line.source === 'manual' ? t('costEstimate.origin.pctSet', { pct: num(line.pct), by: line.by || '', date: line.date || '' }) : t('costEstimate.origin.pct', { pct: num(line.pct) });
    return { kind: line.source === 'manual' ? 'manual' : 'rule', label: t(`costEstimate.source.${line.source === 'manual' ? 'manual' : 'rule'}`), tip };
  }
  if (line.status === 'missing') return { kind: 'missing', label: t('costEstimate.status.missing'), tip: line.hint || t('costEstimate.status.missingTip') };
  const src = (line.source ?? 'manual') as LineSource;
  const ref = line.source_ref ?? {};
  let tip: string;
  if (src === 'production') tip = t('costEstimate.origin.production', { ref: ref.production || '?', factor: num(line.factor) });
  else if (src === 'estimate') tip = t('costEstimate.origin.estimate', { ref: ref.title || ref.project || '?', iteration: ref.iteration || '' });
  else if (src === 'converted') tip = t('costEstimate.origin.converted', { note: line.note || '' });
  else if (src === 'rule') tip = t('costEstimate.origin.rule');
  else tip = t('costEstimate.origin.manual', { by: line.by || t('costEstimate.origin.owner'), date: line.date || '' });
  return { kind: src, label: t(`costEstimate.source.${src}`), tip };
}

/** The review table: one row per iteration (tooling / manufacturing / testing / shipping / subtotal); a missing line
 *  shows `missing`. */
export interface ReviewRow { key: string; iteration: number; tooling: string; manufacturing: string; testing: string; shipping: string; subtotal: string }
export function reviewRows(c: CostComputed, missing = 'missing'): ReviewRow[] {
  const cell = (m: Money | null) => (m === null ? missing : formatMoney(m));
  return c.iterations.map((it) => ({ key: `it${it.index}`, iteration: it.index, tooling: cell(it.tooling), manufacturing: cell(it.manufacturing), testing: cell(it.testing), shipping: cell(it.shipping), subtotal: formatMoney(it.subtotal) }));
}

export function stepIndexOfMissing(steps: CostStep[], m: MissingLine): number {
  return steps.findIndex((s) => s.kind === 'iteration' && s.iteration === m.iteration);
}

/** How many lines come from each source (the review's chips). */
export function sourceCounts(e: CostEstimate): Record<string, number> {
  const out: Record<string, number> = {};
  for (const it of e.iterations) for (const id of LINE_IDS) {
    if (!lineNeeded(it, id)) continue;
    const l = it.lines[id];
    const key = l.status === 'filled' ? (l.source ?? 'manual') : 'missing';
    out[key] = (out[key] ?? 0) + 1;
  }
  return out;
}

export const todayIso = (): string => new Date().toISOString().slice(0, 10);
