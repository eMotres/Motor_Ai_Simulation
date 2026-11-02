/** Project cost estimate (owner 2026-11-02): ADMIN-ONLY questionnaire, estimated from other projects.  Types and pure
 *  helpers of the wizard (steps, line edits, source chips, the review table), tested without a browser.  The line
 *  catalogue is the server's `cost_estimate_schema.json`; totals are computed by the server and are PER CURRENCY. */

export type CostLang = 'en' | 'zh-CN';
export type Bilingual = Record<CostLang, string>;
export type LineStatus = 'filled' | 'not_applicable' | 'missing';
export type LineSource = 'analog' | 'test_history' | 'model' | 'manual' | 'rule';
export type Money = Record<string, number>;

export interface CostItem {
  id: string; label: Bilingual; help: Bilingual; unit: string;
  qty?: { rule: string; mass?: string; buy_factor?: number; value?: number };
  later?: { mode: string; pct: number }; first?: string; applies_if?: Record<string, unknown>;
  analog?: { list: string; match: string[]; scale: string; exponent?: number }; history?: boolean; scale?: string;
  amount_rule?: { pct_of: string; exclude_sections: string[] }; hint?: number; per_iteration?: boolean;
}
export interface CostSection { id: string; label: Bilingual; items: CostItem[] }
export interface CostGroup { id: 'manufacturing' | 'testing' | 'logistics' | 'project'; scope: 'iteration' | 'project'; label: Bilingual; sections: CostSection[] }
export interface CostField { id: string; type: 'bool' | 'int' | 'number' | 'enum'; min?: number; max?: number; options?: string[]; option_labels?: Record<string, Bilingual>; default?: unknown; label: Bilingual; help: Bilingual }
export interface CostSchema {
  schema_version: number; currencies: string[]; default_currency: string; units: Record<string, Bilingual>;
  scale_rules: Record<string, { basis: string | null; label: Bilingual }>; basis_fields: string[];
  settings: CostField[]; iteration_settings: CostField[]; groups: CostGroup[];
  scale_limits?: { min: number; max: number }; scale_check?: { low: number; high: number };
}
export interface CostLine {
  status: LineStatus; qty: number | null; unit: string; unit_cost: number | null; currency: string;
  source: LineSource | null; source_ref: { project?: string; production?: string; row?: string; iteration?: string; label?: string; updated_at?: string } | null;
  scale: { rule: string; factor: number; target?: number | null; analog?: number | null; exponent?: number } | null;
  derive: { pct: number; from_iteration: number } | null; date: string | null; note: string; by: string | null;
}
export interface CostIteration { index: number; settings: Record<string, unknown>; lines: Record<string, CostLine> }
export interface CostSettings {
  complex: boolean; iterations: number; motors_per_iteration: number; currency: string; contingency_pct: number;
  liquid_cooling: boolean; high_voltage: boolean; target_motor: { die: string; config: string } | null;
  analogs: string[]; basis_overrides: Record<string, number>; [key: string]: unknown;
}
export interface CostEstimate {
  version: number; schema_version: number; project_id: string; revision: number; settings: CostSettings;
  iterations: CostIteration[]; project_lines: Record<string, CostLine>; basis: Record<string, number | string>;
  updated_at: string | null; updated_by: string | null;
}
export interface LineResult { amount: Money | null; origin: string }
export interface IterationResult { index: number; manufacturing: Money; testing: Money; logistics: Money; subtotal: Money; lines: Record<string, LineResult> }
export interface MissingLine { scope: 'iteration' | 'project'; iteration: number | null; group: string; section: string; item: string; reason: string }
export interface CostComputed {
  iterations: IterationResult[]; project: { total: Money; lines: Record<string, LineResult> };
  subtotal: Money; contingency_pct: number; contingency: Money; total: Money; currencies: string[];
  missing: MissingLine[]; missing_count: number;
  milestones: Array<{ iteration: number; pct: number | null; suggested_pct: number | null; amount: Money | null }>; warnings: string[];
}
export interface CostSnapshot { id: string; n: number; note: string; created_at: string; created_by: string; revision: number; parent: string | null; totals: { total: Money; missing_count: number }; diff: Array<{ path: string; was: unknown; now: unknown }> }
export interface CostEstimateAnswer {
  estimate: CostEstimate; computed: CostComputed; schema: CostSchema; stored: boolean;
  linked_motors: Array<{ die: string; config: string; status: string }>;
  changes?: Array<{ path: string; was: unknown; now: unknown }>;
  report?: SuggestReport;
}
/** What a suggest did: every filled line with its analog and scale factor (`check` = factor outside `scale_check`), the
 *  skipped lines and the warnings (an implausible price per kg, no analog inside the scale limits, no matching row). */
export interface SuggestReport {
  filled_count: number; check_count?: number;
  filled: Array<{ item: string; where: string; source: LineSource; status: LineStatus; analog: string | null; factor: number | null; rule: string | null; check: boolean; price_per_kg?: number; currency?: string }>;
  skipped: Array<{ item: string; reason: string; where?: string; analog?: string }>;
  warnings?: Array<{ item: string; where: string; text: string }>;
  analogs: Array<{ ref: string; title: string }>;
}

export type T = (key: string, args?: Record<string, unknown>) => string;
export const langOf = (language: string | undefined): CostLang => (String(language || '').toLowerCase().startsWith('zh') ? 'zh-CN' : 'en');
export const txt = (b: Bilingual | undefined, lang: CostLang): string => (b ? b[lang] || b.en || '' : '');

/** One wizard step: the project settings, one group of one iteration, the project-wide lines, the review. */
export interface CostStep { id: string; kind: 'settings' | 'group' | 'review'; group?: CostGroup; iteration?: number }

export function buildCostSteps(schema: CostSchema, estimate: CostEstimate): CostStep[] {
  const steps: CostStep[] = [{ id: 'settings', kind: 'settings' }];
  const perIteration = schema.groups.filter((g) => g.scope === 'iteration');
  for (const it of estimate.iterations) for (const g of perIteration) steps.push({ id: `it${it.index}-${g.id}`, kind: 'group', group: g, iteration: it.index });
  for (const g of schema.groups.filter((x) => x.scope === 'project')) steps.push({ id: g.id, kind: 'group', group: g });
  steps.push({ id: 'review', kind: 'review' });
  return steps;
}

/** The lines a step shows, in catalogue order: `[item, line]`. */
export function stepLines(step: CostStep, estimate: CostEstimate): Array<{ section: CostSection; item: CostItem; line: CostLine }> {
  if (step.kind !== 'group' || !step.group) return [];
  const lines = step.iteration ? estimate.iterations.find((it) => it.index === step.iteration)?.lines : estimate.project_lines;
  if (!lines) return [];
  return step.group.sections.flatMap((section) => section.items.filter((item) => lines[item.id]).map((item) => ({ section, item, line: lines[item.id] })));
}

export type StepState = 'done' | 'partial' | 'empty';
/** done = no line missing; partial = some filled; empty = nothing filled yet. */
export function stepState(step: CostStep, estimate: CostEstimate): { state: StepState; missing: number; total: number } {
  const rows = stepLines(step, estimate);
  const missing = rows.filter((r) => r.line.status === 'missing').length;
  const filled = rows.filter((r) => r.line.status === 'filled').length;
  return { state: rows.length && missing === 0 ? 'done' : filled > 0 ? 'partial' : 'empty', missing, total: rows.length };
}

export interface LineRef { iteration: number | null; item: string }

const cloneEstimate = (e: CostEstimate): CostEstimate => JSON.parse(JSON.stringify(e)) as CostEstimate;

function lineAt(e: CostEstimate, ref: LineRef): CostLine | undefined {
  return ref.iteration === null ? e.project_lines[ref.item] : e.iterations.find((it) => it.index === ref.iteration)?.lines[ref.item];
}

/** The owner types a number: the line becomes `manual` (by him, today), `filled` once quantity and unit cost are there (a
 *  percentage line needs only its %), and a derived "modification" line keeps its % when only the % is edited. */
export function editLine(e: CostEstimate, ref: LineRef, patch: Partial<Pick<CostLine, 'qty' | 'unit_cost' | 'currency' | 'note'>> & { derivePct?: number | null }, who: string, today: string, pctLine = false): CostEstimate {
  const next = cloneEstimate(e);
  const line = lineAt(next, ref);
  if (!line) return e;
  const { derivePct, ...fields } = patch;
  Object.assign(line, fields);
  if (derivePct !== undefined) line.derive = derivePct === null ? null : { pct: derivePct, from_iteration: 1 };
  else if ('qty' in patch || 'unit_cost' in patch) line.derive = null;
  const onlyNote = Object.keys(patch).every((k) => k === 'note');
  if (!onlyNote) {
    line.source = 'manual'; line.by = who; line.date = today; line.scale = null; line.source_ref = null;
  }
  const complete = line.derive ? line.derive.pct !== null : pctLine ? line.qty !== null : line.qty !== null && line.unit_cost !== null;
  if (!onlyNote) line.status = complete ? 'filled' : 'missing';
  return next;
}

/** Not applicable / back to missing - the owner's decision, so `suggest` never touches a not-applicable line. */
export function setLineStatus(e: CostEstimate, ref: LineRef, status: LineStatus, who: string, today: string): CostEstimate {
  const next = cloneEstimate(e);
  const line = lineAt(next, ref);
  if (!line) return e;
  line.status = status;
  if (status === 'not_applicable') { line.source = 'manual'; line.by = who; line.date = today; }
  if (status === 'missing') { line.source = null; line.derive = null; line.scale = null; line.source_ref = null; }
  return next;
}

export function setSetting(e: CostEstimate, key: string, value: unknown): CostEstimate {
  const next = cloneEstimate(e);
  (next.settings as Record<string, unknown>)[key] = value;
  if (key === 'complex' && value === true && next.settings.iterations < 3) next.settings.iterations = 3;
  return next;
}

export function setIterationSetting(e: CostEstimate, index: number, key: string, value: unknown): CostEstimate {
  const next = cloneEstimate(e);
  const it = next.iterations.find((x) => x.index === index);
  if (it) it.settings = { ...it.settings, [key]: value };
  return next;
}

/** "EUR 1 200.00 · CNY 300.00" - never one number over several currencies. */
export function formatMoney(m: Money | null | undefined, empty = '—'): string {
  const entries = Object.entries(m ?? {}).filter(([, v]) => Number.isFinite(v));
  if (!entries.length) return empty;
  return entries.map(([cur, v]) => `${cur} ${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).replace(/,/g, ' ')}`).join(' · ');
}

/** The chip of a line: which source (or status) and the one-line "where it came from" for its tooltip. */
export function originChip(line: CostLine, schema: CostSchema, lang: CostLang, t: T): { kind: LineSource | LineStatus; label: string; tip: string } {
  if (line.status === 'not_applicable') return { kind: 'not_applicable', label: t('costEstimate.status.not_applicable'), tip: t('costEstimate.status.not_applicable') };
  if (line.status === 'missing' && !line.source) return { kind: 'missing', label: t('costEstimate.status.missing'), tip: line.note || t('costEstimate.status.missingTip') };
  const src = (line.source ?? 'manual') as LineSource;
  const ref = line.source_ref ?? {};
  const rule = line.scale && line.scale.rule !== 'one_to_one' ? schema.scale_rules[line.scale.rule] : undefined;
  const scaled = rule && line.scale ? t('costEstimate.origin.scaled', { rule: txt(rule.label, lang), factor: Number(line.scale.factor.toFixed(3)) }) : '';
  let tip: string;
  if (line.derive) tip = t('costEstimate.origin.derive', { pct: line.derive.pct });
  else if (src === 'manual') tip = t('costEstimate.origin.manual', { by: line.by || t('costEstimate.origin.owner'), date: line.date || '' });
  else if (src === 'analog') tip = ref.production ? t('costEstimate.origin.analogProduction', { ref: ref.production, label: ref.label || '', scaled }) : t('costEstimate.origin.analogProject', { ref: ref.project || '?', scaled });
  else if (src === 'test_history') tip = t('costEstimate.origin.testHistory', { ref: ref.project || '?', scaled });
  else if (src === 'model') tip = t('costEstimate.origin.model', { ref: ref.production || ref.project || t('costEstimate.origin.noPrice') });
  else tip = t('costEstimate.origin.rule');
  if (line.note && src !== 'manual') tip = `${tip} · ${line.note}`;
  return { kind: src, label: t(`costEstimate.source.${src}`), tip };
}

/** The review table: one row per iteration (manufacturing / testing / logistics / subtotal) plus project-wide. */
export function reviewRows(c: CostComputed): Array<{ key: string; iteration: number | null; manufacturing: string; testing: string; logistics: string; subtotal: string }> {
  const rows: Array<{ key: string; iteration: number | null; manufacturing: string; testing: string; logistics: string; subtotal: string }> = c.iterations.map((it) => ({ key: `it${it.index}`, iteration: it.index as number | null, manufacturing: formatMoney(it.manufacturing), testing: formatMoney(it.testing), logistics: formatMoney(it.logistics), subtotal: formatMoney(it.subtotal) }));
  rows.push({ key: 'project', iteration: null, manufacturing: '', testing: '', logistics: '', subtotal: formatMoney(c.project.total) });
  return rows;
}

/** The step a missing line is on (the review links to it). */
export function stepIndexOfMissing(steps: CostStep[], m: MissingLine): number {
  const id = m.scope === 'project' ? m.group : `it${m.iteration}-${m.group}`;
  return steps.findIndex((s) => s.id === id);
}

export interface CheckLine extends LineRef { factor: number; rule: string; analog: string; group: string }

/** The review's "check" marks: filled lines whose scale factor is outside `scale_check` (default x0.5 .. x2), read from
 *  the estimate itself so they stay after a reload. */
export function linesToCheck(e: CostEstimate, schema: CostSchema): CheckLine[] {
  const low = schema.scale_check?.low ?? 0.5;
  const high = schema.scale_check?.high ?? 2;
  const groupOf = new Map(schema.groups.flatMap((g) => g.sections.flatMap((s) => s.items.map((it) => [it.id, g.id] as const))));
  const out: CheckLine[] = [];
  const add = (iteration: number | null, lines: Record<string, CostLine>) => {
    for (const [item, l] of Object.entries(lines)) {
      const f = l.scale?.factor;
      if (l.status !== 'filled' || l.derive || typeof f !== 'number' || !(f < low || f > high)) continue;
      const ref = l.source_ref ?? {};
      out.push({ iteration, item, factor: f, rule: l.scale!.rule, analog: ref.production || (ref.project ? `project ${ref.project}` : '?'), group: groupOf.get(item) ?? '' });
    }
  };
  for (const it of e.iterations) add(it.index, it.lines);
  add(null, e.project_lines);
  return out;
}

/** How many lines of the whole estimate come from each source (the review's chips). */
export function sourceCounts(e: CostEstimate): Record<string, number> {
  const out: Record<string, number> = {};
  const all = [...e.iterations.flatMap((it) => Object.values(it.lines)), ...Object.values(e.project_lines)];
  for (const l of all) {
    const key = l.status === 'filled' ? (l.source ?? 'manual') : l.status;
    out[key] = (out[key] ?? 0) + 1;
  }
  return out;
}

export const todayIso = (): string => new Date().toISOString().slice(0, 10);
