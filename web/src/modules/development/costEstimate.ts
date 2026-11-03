/** Project cost estimate (owner 2026-11-02, simplified the same day): ADMIN ONLY.  Each iteration has exactly four lines -
 *  tooling, motor manufacturing (one motor x the motors), testing, shipping (DHL).  Types and pure helpers of the wizard
 *  (steps, line edits, source chips, the review table), tested without a browser.  The schema is the server's
 *  `cost_estimate_schema.json`; the server computes the totals PER CURRENCY and, at the estimate's dated exchange rates
 *  (`estimate.fx`: ECB or manual), also in the DISPLAY currency (`computed.converted`), which follows the UI language
 *  (en -> EUR, zh-CN -> CNY) unless the owner picks one.  Every line keeps its own entered currency.
 *  Owner 2026-11-02 (later the same day): a one-table view (default; the Steps view stays for rollback), a duration in weeks
 *  for every stage (the iteration runs them in sequence, the owner may override it; the project is the sum), and the
 *  shipping line suggested from the dated DHL lane table (`dhl_lanes`). */

export type CostLang = 'en' | 'zh-CN';
export type Bilingual = Record<CostLang, string>;
export type LineId = 'tooling' | 'manufacturing' | 'testing' | 'shipping';
export const LINE_IDS: LineId[] = ['tooling', 'manufacturing', 'testing', 'shipping'];
export type LineStatus = 'filled' | 'missing';
export type LineSource = 'production' | 'estimate' | 'manual' | 'converted' | 'rule' | 'dhl';
export type Bench = 'dyno' | 'propeller' | 'both' | 'none';
export type Money = Record<string, number>;

export interface CostField { id: string; type: 'bool' | 'int' | 'number' | 'enum' | 'text' | 'place'; min?: number; max?: number; options?: string[]; option_labels?: Record<string, Bilingual>; default?: unknown; label: Bilingual; help: Bilingual }
export interface CostLineDef { id: LineId; label: Bilingual; help: Bilingual; per_motor?: boolean }
/** One dated DHL quote: the shipping suggestion scales its price per kg to the shipment's weight. */
export interface DhlLane {
  from: string; to_country: string; mode: string; quoted_on: string; weight_kg: number; price: number; currency: string;
  transit_days: number | null; source: string; note: string;
}
export interface CostShippingSchema {
  settings: CostField[]; housing_factor: number; active_to_complete: number; crate_kg: number; round_to_kg: number; customs_weeks: number;
  lane_limit: number; excluded: Bilingual; dhl_lanes: DhlLane[];
}
export interface CostDurationsSchema { unit: 'weeks'; line_min: number; line_max: number; override_max: number; label: Bilingual; help: Bilingual }
export interface CostSchema {
  schema_version: number; currencies: string[]; default_currency: string; display_currency_by_locale?: Record<CostLang, string>; factor_limits: [number, number];
  settings: CostField[]; iteration_fields: CostField[]; lines: CostLineDef[]; durations?: CostDurationsSchema; shipping?: CostShippingSchema;
}
/** How a DHL shipping suggestion was made (the cell's tooltip). */
export interface DhlRef {
  quoted_on?: string; from?: string; to?: string; mode?: string; lane_weight_kg?: number; lane_price?: number; currency?: string;
  weight_kg?: number; margin_pct?: number; price?: number; rate?: number; motor_mass_kg?: number; mass_basis?: string; transit_days?: number; source?: string;
  /** the EUR price before it was rounded up to a multiple of `round_eur` (0 / absent = not rounded) */
  price_eur_unrounded?: number; round_eur?: number;
}
export interface SourceRef { production?: string; project?: string; title?: string; iteration?: string; analog_mass_kg?: number; target_mass_kg?: number; dhl?: DhlRef }
export interface CostLine {
  status: LineStatus; amount: number | null; currency: string; pct: number | null; source: LineSource | null;
  source_ref: SourceRef | null; factor: number | null; date: string | null; by: string | null; note: string; hint: string;
  /** the stage's duration in weeks (tooling lead time, manufacturing, testing, shipping transit); null = not given */
  weeks?: number | null;
}
export interface CostIteration { index: number; motors: number; bench: Bench; lines: Record<LineId, CostLine>; weeks_override?: number | null }
export interface ShipTo { country: string; city: string }
export interface CostSettings {
  complex: boolean; iterations: number; currency: string; contingency_pct: number; display_currency?: string | null; target_motor: { die: string; config: string } | null;
  ship_from?: string; ship_to?: ShipTo | null; ship_margin_pct?: number; ship_round_eur?: number; [key: string]: unknown;
}
export interface CostMigration { from_version: number; date: string | null; dropped: string[]; note: string }
/** Dated exchange rates, EUR base: `rates` = units of each currency per 1 EUR (effective: a manual rate wins over the ECB one). */
export interface CostFx {
  base: 'EUR'; date: string | null; rates: Record<string, number>; source: 'ECB' | 'manual';
  ecb: { date: string | null; rates: Record<string, number> } | null; manual: Record<string, number>; manual_date: string | null;
}
export interface FxStatus { state: 'ok' | 'stale' | 'none'; date: string | null; manual: string[]; error: string | null }
export interface CostEstimate {
  version: number; schema_version: number; project_id: string; revision: number; settings: CostSettings; fx?: CostFx | null;
  iterations: CostIteration[]; dhl_lanes?: DhlLane[]; migration: CostMigration | null; updated_at: string | null; updated_by: string | null;
}
export interface LineResult { amount: Money | null; origin: string }
export interface IterationResult {
  index: number; motors: number; bench: Bench; tooling: Money | null; manufacturing: Money | null; testing: Money | null; shipping: Money | null;
  subtotal: Money; lines: Record<LineId, LineResult>; weeks?: WeeksBreakdown;
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
  weeks_total?: number; weeks_missing?: Array<{ iteration: number; line: LineId }>;
}
export interface CostSnapshot { id: string; n: number; note: string; created_at: string; created_by: string; revision: number; parent: string | null; totals: { total: Money; converted?: CostConverted; missing_count: number; weeks_total?: number }; diff: Array<{ path: string; was: unknown; now: unknown }> }
/** What Suggest WOULD put in an empty cell (a grey placeholder): an amount with its source, or why there is none; and a
 *  duration when the line's weeks are empty. */
export interface SuggestionCell { amount?: number; source?: LineSource; source_ref?: SourceRef | null; factor?: number | null; origin?: string; hint?: string; weeks?: number }
/** What the server would make of an AUTOMATIC DHL shipping line now (`hint` = why it could not be recomputed: it keeps its last value). */
export interface AutoCell { amount: number | null; weeks: number | null; source_ref: SourceRef | null; hint: string }
export interface CostSuggestions {
  cells: Record<string, Partial<Record<LineId, SuggestionCell>>>; motor_mass_kg: number | null; motor_mass_basis: string;
  /** per iteration: the automatic DHL line as the server would recompute it (only the lines that differ) */
  auto?: Record<string, AutoCell>;
  analogs?: Record<string, { ref: string | null; factor?: number; reason?: string }>; notes?: string[];
}
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
  return { status: 'missing', amount: null, currency, pct: null, source: null, source_ref: null, factor: null, date: null, by: null, note: '', hint: '', weeks: null };
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
  return { index, motors: Number(field('motors') ?? 2), bench: (field('bench') as Bench) ?? 'dyno', weeks_override: null, lines };
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

export function setIterationField(e: CostEstimate, index: number, key: 'motors' | 'bench' | 'weeks_override', value: number | Bench | null): CostEstimate {
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
  else if (src === 'dhl') tip = dhlOrigin(ref.dhl, t);
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
  | { kind: 'iterField'; iteration: number; id: 'motors' | 'bench' | 'weeks_override' }
  | { kind: 'lanes' }
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
    if (edited(S?.weeks_override ?? null, M.weeks_override ?? null, L.weeks_override ?? null, { kind: 'iterField', iteration: index, id: 'weeks_override' })) L.weeks_override = M.weeks_override ?? null;
    for (const id of LINE_IDS) if (edited(S?.lines[id], M.lines[id], L.lines[id], { kind: 'line', iteration: index, line: id })) L.lines[id] = copy(M.lines[id]);
    return L;
  });
  if (edited(synced.dhl_lanes, mine.dhl_lanes, latest.dhl_lanes, { kind: 'lanes' })) merged.dhl_lanes = copy(mine.dhl_lanes);
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
export function fieldLabel(ref: FieldRef, schema: Pick<CostSchema, 'settings' | 'iteration_fields' | 'lines' | 'shipping'>, lang: CostLang, t: T): string {
  const it = (n: number) => t('costEstimate.iteration', { n });
  if (ref.kind === 'setting') {
    const f = schema.settings.find((x) => x.id === ref.id);
    const ship = schema.shipping?.settings.find((x) => x.id === ref.id);
    return f ? txt(f.label, lang) : ship ? txt(ship.label, lang) : ref.id === 'target_motor' ? t('costEstimate.targetMotor') : ref.id === 'display_currency' ? t('costEstimate.displayCurrency') : ref.id;
  }
  if (ref.kind === 'lanes') return t('costEstimate.dhl.lanes');
  if (ref.kind === 'iterField' && ref.id === 'weeks_override') return `${it(ref.iteration)} · ${t('costEstimate.weeksOverride')}`;
  if (ref.kind === 'iterField') return `${it(ref.iteration)} · ${txt(schema.iteration_fields.find((x) => x.id === ref.id)?.label, lang) || ref.id}`;
  if (ref.kind === 'line') return `${it(ref.iteration)} · ${txt(schema.lines.find((x) => x.id === ref.line)?.label, lang) || ref.line}`;
  return `${t('costEstimate.fx.rateLabel')} ${ref.currency}`;
}

// ── durations (owner 2026-11-02: «обязательно указывать время выполнения каждого этапа») ─────────────────────────────────

export const WEEKS_MAX = 104;            // per line (server: 0..104)
export const WEEKS_OVERRIDE_MAX = 416;   // per iteration
/** A duration the server accepts: null (cleared) or a finite number of weeks 0..max. */
export const validWeeks = (v: number | null | undefined, max = WEEKS_MAX): boolean => v === null || v === undefined || (Number.isFinite(v) && v >= 0 && v <= max);

export interface WeeksBreakdown { tooling: number | null; manufacturing: number | null; testing: number | null; shipping: number | null; sum: number; override: number | null; total: number; missing: LineId[] }
/** One iteration's duration: the four stages in SEQUENCE (their sum), testing not counted without a bench; the owner's
 *  override wins.  `missing` = the needed stages without a duration (0 in the sum).  Same rule as the server. */
export function iterationWeeks(it: CostIteration): WeeksBreakdown {
  const per = {} as Record<LineId, number | null>;
  const missing: LineId[] = [];
  for (const id of LINE_IDS) {
    if (!lineNeeded(it, id)) { per[id] = null; continue; }
    const w = it.lines[id]?.weeks;
    per[id] = w === null || w === undefined ? null : Number(w);
    if (per[id] === null) missing.push(id);
  }
  const sum = Number(LINE_IDS.reduce((a, id) => a + (per[id] ?? 0), 0).toFixed(2));
  const override = it.weeks_override === null || it.weeks_override === undefined ? null : Number(it.weeks_override);
  return { ...per, sum, override, total: override ?? sum, missing };
}
/** The project's duration: the sum of the iterations. */
export const projectWeeks = (e: CostEstimate): number => Number(e.iterations.reduce((a, it) => a + iterationWeeks(it).total, 0).toFixed(2));
export const weeksText = (w: number | null | undefined): string => (w === null || w === undefined ? '—' : String(Number(Number(w).toFixed(1))));

export function setLineWeeks(e: CostEstimate, ref: LineRef, weeks: number | null): CostEstimate {
  if (!validWeeks(weeks)) return e;
  const next = clone(e);
  const line = lineAt(next, ref);
  if (line) line.weeks = weeks;
  return next;
}
export function setWeeksOverride(e: CostEstimate, index: number, weeks: number | null): CostEstimate {
  if (!validWeeks(weeks, WEEKS_OVERRIDE_MAX)) return e;
  return setIterationField(e, index, 'weeks_override', weeks);
}

// ── the one-table view ───────────────────────────────────────────────────────────────────────────────────────────────

export type CostView = 'table' | 'steps';
const viewKey = (who: string) => `costEstimate.view:${who || 'admin'}`;
/** The viewer's choice of view (Table is the default; Steps stays for rollback).  Storage may be missing or throw
 *  (private window, blocked site data): then Table. */
export function readView(storage: Pick<Storage, 'getItem'> | null | undefined, who: string): CostView {
  try { return storage?.getItem(viewKey(who)) === 'steps' ? 'steps' : 'table'; } catch { return 'table'; }
}
export function writeView(storage: Pick<Storage, 'setItem'> | null | undefined, who: string, view: CostView): void {
  try { storage?.setItem(viewKey(who), view); } catch { /* a per-viewer convenience only */ }
}

export const MIN_ITERATIONS = 1;
export const MAX_ITERATIONS = 5;
/** "+ iteration" under the table: a new last iteration with the defaults (at most 5). */
export function addIteration(e: CostEstimate, schema: CostSchema): CostEstimate {
  if (e.iterations.length >= MAX_ITERATIONS) return e;
  const next = clone(e);
  next.iterations.push(defaultIteration(schema, next.settings, next.iterations.length + 1));
  next.settings.iterations = next.iterations.length;
  return next;
}
/** The row's remove control: the iteration goes, the later ones move up (re-numbered); at least one stays. */
export function removeIteration(e: CostEstimate, index: number): CostEstimate {
  if (e.iterations.length <= MIN_ITERATIONS || !e.iterations.some((it) => it.index === index)) return e;
  const next = clone(e);
  next.iterations = next.iterations.filter((it) => it.index !== index).map((it, i) => ({ ...it, index: i + 1 }));
  next.settings.iterations = next.iterations.length;
  return next;
}

const eur = (line: CostLine | undefined): number | null => (line && line.amount !== null && line.amount !== undefined && Number.isFinite(line.amount) ? Number(line.amount) : null);
const r2 = (x: number): number => Math.round(x * 100) / 100;

export interface TableRow {
  index: number; motors: number; bench: Bench; testingNeeded: boolean;
  tooling: number | null; perMotor: number | null; manufacturing: number | null; testing: number | null; shipping: number | null;
  weeks: WeeksBreakdown; total: number; missing: number;
}
export interface TableModel {
  rows: TableRow[]; subtotal: number; contingencyPct: number; contingency: number; total: number; weeksTotal: number; missing: number;
  /** the Total row only: in the display currency at the estimate's rates (null = no rate known: EUR stays) */
  display: { currency: string; total: number | null; rate: number | null };
  canAdd: boolean; canRemove: boolean;
}
/** Everything the table shows, from the estimate alone (EUR; manufacturing = per motor x motors; testing 0 without a bench).
 *  Only the Total row is converted to `displayCurrency`. */
export function tableModel(e: CostEstimate, displayCurrency = 'EUR'): TableModel {
  const rows = e.iterations.map((it): TableRow => {
    const testingNeeded = lineNeeded(it, 'testing');
    const perMotor = eur(it.lines.manufacturing);
    const vals = { tooling: eur(it.lines.tooling), manufacturing: perMotor === null ? null : r2(perMotor * (it.motors || 0)), testing: testingNeeded ? eur(it.lines.testing) : 0, shipping: eur(it.lines.shipping) };
    const missing = LINE_IDS.filter((id) => lineNeeded(it, id) && it.lines[id].status === 'missing').length;
    return { index: it.index, motors: it.motors, bench: it.bench, testingNeeded, perMotor, ...vals,
      weeks: iterationWeeks(it), total: r2((vals.tooling ?? 0) + (vals.manufacturing ?? 0) + (vals.testing ?? 0) + (vals.shipping ?? 0)), missing };
  });
  const subtotal = r2(rows.reduce((a, r) => a + r.total, 0));
  const contingencyPct = Number(e.settings.contingency_pct) || 0;
  const contingency = r2(subtotal * contingencyPct / 100);
  const total = r2(subtotal + contingency);
  const cur = displayCurrency || 'EUR';
  const rate = cur === 'EUR' ? 1 : (e.fx?.rates?.[cur] ?? null);
  return { rows, subtotal, contingencyPct, contingency, total, weeksTotal: projectWeeks(e), missing: rows.reduce((a, r) => a + r.missing, 0),
    display: { currency: cur, total: rate ? r2(total * rate) : null, rate }, canAdd: rows.length < MAX_ITERATIONS, canRemove: rows.length > MIN_ITERATIONS };
}

/** Accept a grey suggestion (Enter or one click): the amount with its source (by the viewer, today) when the line is empty,
 *  and the duration when the line's weeks are empty.  A filled line or set weeks are never overwritten. */
export function acceptSuggestion(e: CostEstimate, ref: LineRef, cell: SuggestionCell | undefined, who: string, today: string): CostEstimate {
  if (!cell) return e;
  const next = clone(e);
  const line = lineAt(next, ref);
  if (!line) return e;
  let changed = false;
  if (line.status === 'missing' && cell.amount !== undefined && cell.amount !== null && Number.isFinite(cell.amount)) {
    Object.assign(line, { status: 'filled', amount: cell.amount, currency: 'EUR', pct: null, source: cell.source ?? 'manual', source_ref: cell.source_ref ?? null,
      factor: cell.factor ?? null, date: today, by: who, hint: '' });
    changed = true;
  }
  if ((line.weeks === null || line.weeks === undefined) && cell.weeks !== undefined && validWeeks(cell.weeks)) { line.weeks = cell.weeks; changed = true; }
  return changed ? next : e;
}
export const suggestionAt = (s: CostSuggestions | null | undefined, ref: LineRef): SuggestionCell | undefined => s?.cells?.[String(ref.iteration)]?.[ref.line];

// ── shipping (DHL) ───────────────────────────────────────────────────────────────────────────────────────────────────

/** "DHL quote 2026-11-02, CN Shanghai→AR, 85 kg, +20 %" */
export function dhlOrigin(d: DhlRef | undefined, t: T): string {
  return t('costEstimate.origin.dhl', { date: d?.quoted_on ?? '?', from: d?.from ?? '?', to: d?.to ?? '?', weight: weeksText(d?.weight_kg ?? null), margin: weeksText(d?.margin_pct ?? 0) });
}
/** The extra tooltip lines of a DHL suggestion: the weight and how it was made, the quote, and what is NOT included. */
export function dhlDetails(d: DhlRef | undefined, motors: number, t: T, excluded: string): string[] {
  if (!d) return [excluded];
  const out: string[] = [];
  if (d.weight_kg !== undefined) out.push(t('costEstimate.dhl.weight', { weight: weeksText(d.weight_kg), motors, mass: weeksText(d.motor_mass_kg ?? null), basis: d.mass_basis ?? '' }));
  if (d.lane_price !== undefined) out.push(t('costEstimate.dhl.quote', { price: String(Number(Number(d.lane_price).toFixed(2))), currency: d.currency ?? '', weight: weeksText(d.lane_weight_kg ?? null), days: d.transit_days ?? '—', mode: d.mode ?? '' }));
  if (d.price_eur_unrounded !== undefined && Number(d.round_eur) > 0) out.push(t('costEstimate.dhl.rounded', { price: String(Number(Number(d.price_eur_unrounded).toFixed(2))), step: String(Number(d.round_eur)) }));
  out.push(excluded);
  return out;
}

// ── automatic DHL shipping (owner 2026-11-03) ─────────────────────────────────────────────────────────────────────────
// A shipping line whose source is `dhl` follows the iteration's motors: the server recomputes it on every save, and the
// preview endpoint (`/suggestions`, field `auto`) gives the same numbers at once so the cell changes the moment the motors do.

/** The automatic DHL lines (filled, source `dhl`) of an estimate. */
export const autoDhlIterations = (e: CostEstimate): number[] =>
  e.iterations.filter((it) => it.lines.shipping.status === 'filled' && it.lines.shipping.source === 'dhl').map((it) => it.index);

/** What an automatic line depends on: refetch the preview only when this changes (amounts of the automatic lines are NOT part of it). */
export function autoKey(e: CostEstimate): string {
  const s = e.settings;
  return JSON.stringify([s.target_motor, s.ship_from, s.ship_to, s.ship_margin_pct, s.ship_round_eur, e.fx?.manual ?? {}, e.dhl_lanes ?? [],
    e.iterations.map((it) => [it.index, it.motors, it.lines.shipping.status === 'filled' && it.lines.shipping.source === 'dhl'])]);
}

/** Lay the server's recomputed automatic DHL lines over the estimate on screen.  Only a line that is still automatic is
 *  touched; the duration is taken only when the server gave one (it never overwrites the owner's weeks).  The same estimate
 *  object comes back when nothing changes. */
export function applyAuto(e: CostEstimate, auto: Record<string, AutoCell> | undefined): CostEstimate {
  if (!auto) return e;
  let next: CostEstimate | null = null;
  for (const [key, cell] of Object.entries(auto)) {
    const idx = e.iterations.findIndex((it) => String(it.index) === key);
    const cur = idx < 0 ? undefined : e.iterations[idx].lines.shipping;
    if (!cur || cur.status !== 'filled' || cur.source !== 'dhl') continue;
    const patch: Partial<CostLine> = { hint: cell.hint ?? '' };
    if (cell.amount !== null && cell.amount !== undefined) { patch.amount = cell.amount; patch.source_ref = cell.source_ref ?? null; patch.currency = 'EUR'; }
    if (cell.weeks !== null && cell.weeks !== undefined) patch.weeks = cell.weeks;
    if ((Object.keys(patch) as Array<keyof CostLine>).every((k) => same(cur[k], patch[k]))) continue;
    next = next ?? clone(e);
    Object.assign(next.iterations[idx].lines.shipping, patch);
  }
  return next ?? e;
}

/** Put a filled manual / converted shipping line back to automatic DHL: the source becomes `dhl` (the amount stays until the
 *  server's numbers arrive - the caller then applies `applyAuto`, or drops this candidate when DHL cannot be computed). */
export function setDhlAuto(e: CostEstimate, ref: LineRef): CostEstimate {
  const line = lineAt(e, ref);
  if (!line || ref.line !== 'shipping' || line.status !== 'filled' || line.source === 'dhl') return e;
  const next = clone(e);
  Object.assign(lineAt(next, ref)!, { source: 'dhl', source_ref: null, factor: null, hint: '' });
  return next;
}

export function setShipTo(e: CostEstimate, country: string, city: string): CostEstimate {
  const next = clone(e);
  const c = country.trim().toUpperCase();
  next.settings.ship_to = /^[A-Z]{2}$/.test(c) ? { country: c, city: city.trim().slice(0, 80) } : null;
  return next;
}
export const shipToText = (s: ShipTo | null | undefined): string => (s?.country ? `${s.country}${s.city ? ` ${s.city}` : ''}` : '');

export function blankLane(e: CostEstimate, today: string): DhlLane {
  return { from: e.settings.ship_from || 'CN Shanghai', to_country: e.settings.ship_to?.country ?? '', mode: 'DHL Air Freight (Global Forwarding)', quoted_on: today,
    weight_kg: 0, price: 0, currency: 'CNY', transit_days: null, source: 'dhl.com get-a-quote', note: '' };
}
/** The fields of a lane the server would refuse (so a half-typed lane is never autosaved). */
export function laneErrors(l: DhlLane, currencies: string[] = ['EUR', 'CNY', 'USD']): Array<keyof DhlLane> {
  const bad: Array<keyof DhlLane> = [];
  if (!String(l.from || '').trim()) bad.push('from');
  if (!/^[A-Z]{2}$/.test(String(l.to_country || '').trim().toUpperCase())) bad.push('to_country');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(l.quoted_on || ''))) bad.push('quoted_on');
  if (!(Number(l.weight_kg) > 0 && Number(l.weight_kg) <= 100000)) bad.push('weight_kg');
  if (!(Number(l.price) > 0 && Number(l.price) <= 1e9)) bad.push('price');
  if (!currencies.includes(String(l.currency || '').toUpperCase())) bad.push('currency');
  if (l.transit_days !== null && l.transit_days !== undefined && !(Number.isInteger(Number(l.transit_days)) && Number(l.transit_days) >= 0 && Number(l.transit_days) <= 365)) bad.push('transit_days');
  return bad;
}
/** Add (`index` null) or replace a lane; refused (the estimate unchanged) while the lane has an error. */
export function upsertLane(e: CostEstimate, lane: DhlLane, index: number | null, currencies?: string[]): CostEstimate {
  if (laneErrors(lane, currencies).length) return e;
  const next = clone(e);
  const clean: DhlLane = { ...lane, to_country: lane.to_country.trim().toUpperCase(), currency: lane.currency.toUpperCase(), weight_kg: Number(lane.weight_kg), price: Number(lane.price),
    transit_days: lane.transit_days === null || lane.transit_days === undefined || String(lane.transit_days) === '' ? null : Number(lane.transit_days) };
  const lanes = [...(next.dhl_lanes ?? [])];
  if (index === null || index < 0 || index >= lanes.length) { if (lanes.length >= 50) return e; lanes.push(clean); } else lanes[index] = clean;
  next.dhl_lanes = lanes;
  return next;
}
/** What the grey suggestions depend on: refetch them only when this changes (the motor, the destination and margin, the
 *  lanes, the manual rates, the motors / bench of each iteration, which cells are empty). */
export function suggestionKey(e: CostEstimate): string {
  const s = e.settings;
  return JSON.stringify([s.target_motor, s.ship_from, s.ship_to, s.ship_margin_pct, s.ship_round_eur, e.fx?.manual ?? {}, e.dhl_lanes ?? [],
    e.iterations.map((it) => [it.index, it.motors, it.bench, LINE_IDS.map((id) => [it.lines[id].status === 'missing', it.lines[id].weeks === null || it.lines[id].weeks === undefined])])]);
}

export function removeLane(e: CostEstimate, index: number): CostEstimate {
  if (!e.dhl_lanes || index < 0 || index >= e.dhl_lanes.length) return e;
  const next = clone(e);
  next.dhl_lanes = next.dhl_lanes!.filter((_, i) => i !== index);
  return next;
}
