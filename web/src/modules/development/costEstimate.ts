/** Project cost estimate (owner 2026-11-02, simplified the same day): ADMIN ONLY.  Each iteration has exactly four lines -
 *  tooling, motor manufacturing (one motor x the motors), testing, shipping (DHL).  Types and pure helpers of the wizard
 *  (steps, line edits, source chips, the review table), tested without a browser.  The schema is the server's
 *  `cost_estimate_schema.json`; the server computes the totals PER CURRENCY and, at the estimate's dated exchange rates
 *  (`estimate.fx`: ECB or manual), also in the DISPLAY currency (`computed.converted`), which follows the UI language
 *  (en -> EUR, zh-CN -> CNY) unless the owner picks one.  Every line keeps its own entered currency. */

export type CostLang = 'en' | 'zh-CN';
export type Bilingual = Record<CostLang, string>;
export type LineId = 'tooling' | 'manufacturing' | 'testing' | 'shipping';
export const LINE_IDS: LineId[] = ['tooling', 'manufacturing', 'testing', 'shipping'];
export type LineStatus = 'filled' | 'missing';
export type LineSource = 'production' | 'estimate' | 'manual' | 'converted' | 'rule';
export type Bench = 'dyno' | 'propeller' | 'both' | 'none';
export type Money = Record<string, number>;

export interface CostField { id: string; type: 'bool' | 'int' | 'number' | 'enum'; min?: number; max?: number; options?: string[]; option_labels?: Record<string, Bilingual>; default?: unknown; label: Bilingual; help: Bilingual }
export interface CostLineDef { id: LineId; label: Bilingual; help: Bilingual; per_motor?: boolean }
export interface CostSchema {
  schema_version: number; currencies: string[]; default_currency: string; display_currency_by_locale?: Record<CostLang, string>; factor_limits: [number, number];
  settings: CostField[]; iteration_fields: CostField[]; lines: CostLineDef[];
}
export interface SourceRef { production?: string; project?: string; title?: string; iteration?: string; analog_mass_kg?: number; target_mass_kg?: number }
export interface CostLine {
  status: LineStatus; amount: number | null; currency: string; pct: number | null; source: LineSource | null;
  source_ref: SourceRef | null; factor: number | null; date: string | null; by: string | null; note: string; hint: string;
}
export interface CostIteration { index: number; motors: number; bench: Bench; lines: Record<LineId, CostLine> }
export interface CostSettings { complex: boolean; iterations: number; currency: string; contingency_pct: number; display_currency?: string | null; target_motor: { die: string; config: string } | null; [key: string]: unknown }
export interface CostMigration { from_version: number; date: string | null; dropped: string[]; note: string }
/** Dated exchange rates, EUR base: `rates` = units of each currency per 1 EUR (effective: a manual rate wins over the ECB one). */
export interface CostFx {
  base: 'EUR'; date: string | null; rates: Record<string, number>; source: 'ECB' | 'manual';
  ecb: { date: string | null; rates: Record<string, number> } | null; manual: Record<string, number>; manual_date: string | null;
}
export interface FxStatus { state: 'ok' | 'stale' | 'none'; date: string | null; manual: string[]; error: string | null }
export interface CostEstimate {
  version: number; schema_version: number; project_id: string; revision: number; settings: CostSettings; fx?: CostFx | null;
  iterations: CostIteration[]; migration: CostMigration | null; updated_at: string | null; updated_by: string | null;
}
export interface LineResult { amount: Money | null; origin: string }
export interface IterationResult {
  index: number; motors: number; bench: Bench; tooling: Money | null; manufacturing: Money | null; testing: Money | null; shipping: Money | null;
  subtotal: Money; lines: Record<LineId, LineResult>;
}
export interface MissingLine { iteration: number; line: LineId; reason: string }
/** The lines and totals in the display currency; `available` is false when a rate is unknown (`missing_rates`): the
 *  per-currency figures are then the only totals.  A line / total that cannot be converted is null. */
export interface CostConverted {
  currency: string; available: boolean; missing_rates: string[];
  iterations: Array<{ index: number; lines: Record<LineId, number | null>; subtotal: number | null }>;
  subtotal: number | null; contingency: number | null; total: number | null;
}
export interface CostComputed {
  iterations: IterationResult[]; subtotal: Money; contingency_pct: number; contingency: Money; total: Money; currencies: string[];
  converted?: CostConverted; missing: MissingLine[]; missing_count: number; warnings: string[];
}
export interface CostSnapshot { id: string; n: number; note: string; created_at: string; created_by: string; revision: number; parent: string | null; totals: { total: Money; converted?: CostConverted; missing_count: number }; diff: Array<{ path: string; was: unknown; now: unknown }> }
export interface CostEstimateAnswer {
  estimate: CostEstimate; computed: CostComputed; schema: CostSchema; stored: boolean; fx_status?: FxStatus;
  linked_motors: Array<{ die: string; config: string; status: string }>;
  changes?: Array<{ path: string; was: unknown; now: unknown }>;
  report?: { filled_count: number; skipped: Array<{ iteration: number; line: LineId; reason: string }>; notes: string[];
    analogs: Record<'manufacturing' | 'tooling', { ref: string | null; factor?: number; reason?: string }>; target_mass_kg: number | null };
}

export type T = (key: string, args?: Record<string, unknown>) => string;
export const langOf = (language: string | undefined): CostLang => (String(language || '').toLowerCase().startsWith('zh') ? 'zh-CN' : 'en');
export const txt = (b: Bilingual | undefined, lang: CostLang): string => (b ? b[lang] || b.en || '' : '');

/** Every amount is entered and shown in EUR (owner 2026-11-02); only the final table may be shown in USD / CNY. */
export const DISPLAY_BY_LANG: Record<CostLang, string> = { en: 'EUR', 'zh-CN': 'EUR' };
export const defaultDisplayCurrency = (lang: CostLang, schema?: Pick<CostSchema, 'display_currency_by_locale'> | null): string =>
  schema?.display_currency_by_locale?.[lang] ?? DISPLAY_BY_LANG[lang];
/** The owner's own pick (stored in the estimate) wins over the language default. */
export const displayCurrencyOf = (settings: Pick<CostSettings, 'display_currency'> | undefined, lang: CostLang, schema?: Pick<CostSchema, 'display_currency_by_locale'> | null): string =>
  settings?.display_currency || defaultDisplayCurrency(lang, schema);

export function setDisplayCurrency(e: CostEstimate, value: string | null): CostEstimate {
  const next = clone(e);
  next.settings.display_currency = value || null;
  return next;
}

/** The owner types an exchange rate (units of `currency` per 1 EUR) or clears it (back to the ECB rate).  Only the manual
 *  rate is sent to the server; it takes the ECB rates from its own cache. */
export function setManualRate(e: CostEstimate, currency: string, rate: number | null): CostEstimate {
  const next = clone(e);
  const manual = { ...(next.fx?.manual ?? {}) };
  if (rate === null || !(rate > 0)) delete manual[currency]; else manual[currency] = rate;
  const base: CostFx = next.fx ?? { base: 'EUR', date: null, rates: {}, source: 'ECB', ecb: null, manual: {}, manual_date: null };
  next.fx = { ...base, manual, source: Object.keys(manual).length ? 'manual' : 'ECB', rates: { ...(base.ecb?.rates ?? {}), ...manual } };
  return next;
}

/** A frozen snapshot's total, as it was shown then: in its display currency at the rates it kept, else per currency. */
export function snapshotTotalText(s: Pick<CostSnapshot, 'totals'>): string {
  const cv = s.totals.converted;
  return cv && cv.available && cv.total !== null ? formatMoney({ [cv.currency]: cv.total }) : formatMoney(s.totals.total);
}

const rateNum = (r: number): string => String(Number(r.toFixed(4)));
/** "8 CNY, 1.25 USD" - the rates of the line under the review totals; a manual one is tagged. */
export function ratesText(fx: CostFx, manualTag = 'manual'): string {
  return Object.entries(fx.rates).map(([cur, r]) => `${rateNum(r)} ${cur}${cur in (fx.manual ?? {}) ? ` (${manualTag})` : ''}`).join(', ');
}
/** "converted at ECB 2026-11-02 (1 EUR = 8 CNY, 1.25 USD)" or "manual rates 2026-11-05 (1 EUR = ...)"; null when no rate is known. */
export function fxLine(fx: CostFx | null | undefined, t: T): string | null {
  if (!fx || !Object.keys(fx.rates ?? {}).length) return null;
  const rates = ratesText(fx, t('costEstimate.fx.manualTag'));
  return fx.source === 'manual' ? t('costEstimate.fx.manual', { date: fx.manual_date || fx.date || '', rates }) : t('costEstimate.fx.ecb', { date: fx.date || '', rates });
}

/** The money figures of the header / review: in the display currency when the server could convert (`converted.available`),
 *  else per currency, as before.  `original` = the per-currency originals (for a tooltip). */
export interface TotalsView { converted: boolean; currency: string; subtotal: string; contingency: string; total: string; original: string; missingRates: string[] }
export function totalsView(c: CostComputed): TotalsView {
  const cv = c.converted;
  const ok = !!cv && cv.available && cv.total !== null;
  const one = (v: number | null | undefined) => (cv ? formatMoney(v === null || v === undefined ? null : { [cv.currency]: v }) : '—');
  return {
    converted: ok, currency: cv?.currency ?? '', missingRates: cv?.missing_rates ?? [], original: formatMoney(c.total),
    subtotal: ok ? one(cv!.subtotal) : formatMoney(c.subtotal), contingency: ok ? one(cv!.contingency) : formatMoney(c.contingency), total: ok ? one(cv!.total) : formatMoney(c.total),
  };
}

/** The right-hand total of an iteration card: always EUR (every amount is in EUR; only the final table is converted). */
export function iterationSubtotal(c: CostComputed, index: number): string {
  return formatMoney(c.iterations.find((x) => x.index === index)?.subtotal);
}

/** A line's total (EUR; per motor x the motors for the manufacturing line). */
export function lineTotal(c: CostComputed, index: number, id: LineId): string {
  return formatMoney(c.iterations.find((it) => it.index === index)?.lines[id]?.amount);
}

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

/** The owner edits a line.  A number makes it `manual` - by him, today; clearing the amount makes it missing.  A note alone
 *  keeps where the number came from.  Every amount is in EUR (owner 2026-11-02: «все цены в евро»): there is no currency
 *  choice, a typed amount makes the line EUR (an old converted CNY line included). */
export function editLine(e: CostEstimate, ref: LineRef, patch: { amount?: number | null; note?: string }, who: string, today: string): CostEstimate {
  const next = clone(e);
  const line = lineAt(next, ref);
  if (!line) return e;
  if (patch.note !== undefined) line.note = patch.note;
  if (!('amount' in patch)) return next;
  const value = patch.amount ?? null;
  line.amount = value; line.pct = null; line.currency = 'EUR';
  line.factor = null; line.source_ref = null; line.hint = '';
  if (value === null) { line.status = 'missing'; line.source = null; line.by = null; line.date = null; }
  else { line.status = 'filled'; line.source = 'manual'; line.by = who; line.date = today; }
  return next;
}

export function defaultIteration(schema: CostSchema, settings: CostSettings, index: number): CostIteration {
  const field = (id: string) => schema.iteration_fields.find((f) => f.id === id)?.default;
  const lines = Object.fromEntries(LINE_IDS.map((id) => [id, blankLine(settings.currency)])) as Record<LineId, CostLine>;
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
export interface ReviewRow { key: string; iteration: number; tooling: string; manufacturing: string; testing: string; shipping: string; subtotal: string; original?: string }
/** With the totals converted (`converted.available`) every cell is in the display currency and `original` keeps the
 *  per-currency subtotal for a tooltip; otherwise the cells are per currency, as the lines were entered. */
export function reviewRows(c: CostComputed, missing = 'missing'): ReviewRow[] {
  const cv = c.converted;
  if (cv && cv.available && cv.total !== null) {
    const cur = (v: number | null | undefined) => (v === null || v === undefined ? missing : formatMoney({ [cv.currency]: v }));
    return c.iterations.map((it) => {
      const conv = cv.iterations.find((x) => x.index === it.index);
      const empty = (id: LineId) => it[id] !== null && Object.keys(it[id] ?? {}).length === 0;          // no bench: nothing to test
      const cell = (id: LineId) => (empty(id) ? formatMoney(null) : cur(conv?.lines[id]));
      return { key: `it${it.index}`, iteration: it.index, tooling: cell('tooling'), manufacturing: cell('manufacturing'), testing: cell('testing'), shipping: cell('shipping'),
        subtotal: cur(conv?.subtotal ?? 0), original: formatMoney(it.subtotal) };
    });
  }
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

// ── revision conflict (409): reload the latest estimate and carry the user's pending edits over it ───────────────────────

/** One field of the estimate, as the wizard names it to the user. */
export type FieldRef =
  | { kind: 'setting'; id: string }
  | { kind: 'iterField'; iteration: number; id: 'motors' | 'bench' }
  | { kind: 'line'; iteration: number; line: LineId }
  | { kind: 'fx'; currency: string };

const same = (a: unknown, b: unknown): boolean => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
const copy = <V,>(v: V): V => (v === undefined ? v : (JSON.parse(JSON.stringify(v)) as V));

/** Three-way merge after a 409.  `synced` = the server version the user's edits started from, `mine` = what is on screen
 *  (synced + pending edits), `latest` = the server's version now.  Result: `latest` with every field the user changed
 *  (synced -> mine) written on top of it, so changes made in another session stay unless they touch the same field.
 *  `overridden` lists the fields the user changed that were ALSO changed elsewhere to another value: his value was kept. */
export function mergeEdits(synced: CostEstimate, mine: CostEstimate, latest: CostEstimate): { merged: CostEstimate; overridden: FieldRef[] } {
  let merged = copy(latest);
  const overridden: FieldRef[] = [];
  /** true when the user changed this field; records a clash with a change made elsewhere */
  const edited = (s: unknown, m: unknown, l: unknown, ref: FieldRef): boolean => {
    if (same(s, m)) return false;
    if (!same(l, s) && !same(l, m)) overridden.push(ref);
    return true;
  };
  for (const k of new Set([...Object.keys(synced.settings), ...Object.keys(mine.settings)])) {
    if (edited(synced.settings[k], mine.settings[k], latest.settings[k], { kind: 'setting', id: k })) (merged.settings as Record<string, unknown>)[k] = copy(mine.settings[k]);
  }
  const n = Math.max(1, Math.min(5, Math.round(Number(merged.settings.iterations) || 1)));
  merged.iterations = Array.from({ length: n }, (_, i) => {
    const index = i + 1;
    const S = synced.iterations.find((x) => x.index === index);
    const M = mine.iterations.find((x) => x.index === index);
    const L = merged.iterations.find((x) => x.index === index);
    if (!M) return L ?? copy(S as CostIteration);
    if (!L) return copy(M);
    if (edited(S?.motors, M.motors, L.motors, { kind: 'iterField', iteration: index, id: 'motors' })) L.motors = M.motors;
    if (edited(S?.bench, M.bench, L.bench, { kind: 'iterField', iteration: index, id: 'bench' })) L.bench = M.bench;
    for (const id of LINE_IDS) if (edited(S?.lines[id], M.lines[id], L.lines[id], { kind: 'line', iteration: index, line: id })) L.lines[id] = copy(M.lines[id]);
    return L;
  });
  for (const cur of new Set([...Object.keys(synced.fx?.manual ?? {}), ...Object.keys(mine.fx?.manual ?? {})])) {
    if (edited(synced.fx?.manual?.[cur], mine.fx?.manual?.[cur], latest.fx?.manual?.[cur], { kind: 'fx', currency: cur })) merged = setManualRate(merged, cur, mine.fx?.manual?.[cur] ?? null);
  }
  return { merged, overridden };
}

/** What the wizard does on a 409 (saved elsewhere).  No unsaved local edits: show the latest estimate, nothing to save, no
 *  note.  Unsaved edits: the latest with those edits re-applied, to be saved again; `overridden` (empty most of the time)
 *  names the re-applied fields that had also been changed elsewhere - the only case that gets a note. */
export function reconcile(dirty: boolean, synced: CostEstimate | null, mine: CostEstimate | null, latest: CostEstimate): { estimate: CostEstimate; save: boolean; overridden: FieldRef[] } {
  if (!dirty || !synced || !mine) return { estimate: latest, save: false, overridden: [] };
  const { merged, overridden } = mergeEdits(synced, mine, latest);
  return { estimate: merged, save: true, overridden };
}

/** The user-facing name of a field, for the "your change to <field> was kept" note. */
export function fieldLabel(ref: FieldRef, schema: Pick<CostSchema, 'settings' | 'iteration_fields' | 'lines'>, lang: CostLang, t: T): string {
  const it = (n: number) => t('costEstimate.iteration', { n });
  if (ref.kind === 'setting') {
    const f = schema.settings.find((x) => x.id === ref.id);
    return f ? txt(f.label, lang) : ref.id === 'target_motor' ? t('costEstimate.targetMotor') : ref.id === 'display_currency' ? t('costEstimate.displayCurrency') : ref.id;
  }
  if (ref.kind === 'iterField') return `${it(ref.iteration)} · ${txt(schema.iteration_fields.find((x) => x.id === ref.id)?.label, lang) || ref.id}`;
  if (ref.kind === 'line') return `${it(ref.iteration)} · ${txt(schema.lines.find((x) => x.id === ref.line)?.label, lang) || ref.line}`;
  return `${t('costEstimate.fx.rateLabel')} ${ref.currency}`;
}
