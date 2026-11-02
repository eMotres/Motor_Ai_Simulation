import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  buildCostSteps, editLine, formatMoney, langOf, LINE_IDS, originChip, reviewRows, setIterationField, setSetting, setToolingMode,
  sourceCounts, stepIndexOfMissing, stepState, toolingMode,
} from '../costEstimate.ts';

// the real schema the server serves
const schema = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../src/motor_ai_sim/development/cost_estimate_schema.json', import.meta.url)), 'utf8'));
const locale = (lang) => JSON.parse(readFileSync(fileURLToPath(new URL(`../../../locales/${lang}/development.json`, import.meta.url)), 'utf8')).costEstimate;
const t = (key, args) => (args ? `${key}|${JSON.stringify(args)}` : key);

const blank = () => ({ status: 'missing', amount: null, currency: 'EUR', pct: null, source: null, source_ref: null, factor: null, date: null, by: null, note: '', hint: '' });
const iteration = (index) => ({ index, motors: 2, bench: 'dyno', lines: { tooling: index > 1 ? { ...blank(), status: 'filled', pct: 20, source: 'rule' } : blank(), manufacturing: blank(), testing: blank(), shipping: blank() } });
function estimate(n = 2) {
  return { version: 2, schema_version: 2, project_id: 'alpha', revision: 3, settings: { complex: false, iterations: n, currency: 'EUR', contingency_pct: 10, target_motor: null },
    iterations: Array.from({ length: n }, (_, i) => iteration(i + 1)), migration: null, updated_at: null, updated_by: null };
}

test('the schema is four lines, and the steps are settings, one per iteration, review', () => {
  assert.deepEqual(schema.lines.map((l) => l.id), LINE_IDS);
  for (const e of [...schema.lines, ...schema.settings, ...schema.iteration_fields]) assert.ok(e.label.en && e.label['zh-CN'] && e.help.en && e.help['zh-CN'], e.id);
  const steps = buildCostSteps(estimate(3));
  assert.deepEqual(steps.map((s) => s.id), ['settings', 'it1', 'it2', 'it3', 'review']);
  assert.equal(stepIndexOfMissing(steps, { iteration: 2, line: 'shipping', reason: 'not filled' }), 2);
  assert.deepEqual(stepState(steps[1], estimate()), { state: 'empty', missing: 4 });
  assert.deepEqual(stepState(steps[2], estimate()), { state: 'partial', missing: 3 });
});

test('typing an amount makes the line manual and filled; clearing it makes it missing; a note keeps the source', () => {
  let e = editLine(estimate(), { iteration: 1, line: 'manufacturing' }, { amount: 450 }, 'owner@example.test', '2026-11-02');
  let line = e.iterations[0].lines.manufacturing;
  assert.equal(line.status, 'filled');
  assert.equal(line.source, 'manual');
  assert.equal(line.by, 'owner@example.test');
  assert.equal(line.date, '2026-11-02');
  e = editLine(e, { iteration: 1, line: 'manufacturing' }, { amount: null }, 'o', '2026-11-02');
  line = e.iterations[0].lines.manufacturing;
  assert.equal(line.status, 'missing');
  assert.equal(line.source, null);
  const sug = estimate();
  Object.assign(sug.iterations[0].lines.shipping, { status: 'filled', amount: 420, source: 'estimate', source_ref: { project: 'other', iteration: '1' } });
  const noted = editLine(sug, { iteration: 1, line: 'shipping' }, { note: 'DHL quote' }, 'o', '2026-11-02');
  assert.equal(noted.iterations[0].lines.shipping.source, 'estimate');
  assert.equal(noted.iterations[0].lines.shipping.note, 'DHL quote');
  assert.equal(estimate().iterations[0].lines.manufacturing.amount, null);      // the input is not mutated
});

test('later-iteration tooling: % of iteration 1 by default, an amount on request, the % editable', () => {
  let e = estimate();
  assert.equal(toolingMode(e.iterations[1].lines.tooling), 'pct');
  e = editLine(e, { iteration: 2, line: 'tooling' }, { pct: 35 }, 'o', '2026-11-02');
  assert.equal(e.iterations[1].lines.tooling.pct, 35);
  assert.equal(e.iterations[1].lines.tooling.source, 'manual');
  e = setToolingMode(e, 2, 'amount', 20);
  assert.equal(toolingMode(e.iterations[1].lines.tooling), 'amount');
  assert.equal(e.iterations[1].lines.tooling.status, 'missing');
  e = setToolingMode(e, 2, 'pct', 20);
  assert.deepEqual([e.iterations[1].lines.tooling.pct, e.iterations[1].lines.tooling.source], [20, 'rule']);
  assert.equal(setToolingMode(estimate(), 1, 'pct', 20).iterations[0].lines.tooling.pct, null);   // iteration 1 has no %
});

test('settings: the iteration list follows the count; complex makes it at least 3; motors and bench per iteration', () => {
  let e = setSetting(estimate(2), schema, 'complex', true);
  assert.equal(e.settings.iterations, 3);
  assert.equal(e.iterations.length, 3);
  assert.equal(e.iterations[2].motors, 2);
  assert.equal(e.iterations[2].lines.tooling.pct, 20);
  e = setSetting(e, schema, 'iterations', 1);
  assert.deepEqual(e.iterations.map((it) => it.index), [1]);
  assert.equal(setSetting(e, schema, 'iterations', 9).iterations.length, 5);
  e = setIterationField(estimate(), 2, 'bench', 'none');
  assert.equal(e.iterations[1].bench, 'none');
  assert.deepEqual(stepState({ kind: 'iteration', iteration: 2 }, e), { state: 'partial', missing: 2 });   // testing not needed
  assert.equal(setIterationField(estimate(), 1, 'motors', 4).iterations[0].motors, 4);
});

test('source chips say where the number came from', () => {
  const it = estimate().iterations[0];
  const prod = originChip({ ...blank(), status: 'filled', amount: 4500, source: 'production', factor: 1.5, source_ref: { production: 'MID/b' } }, it, 'manufacturing', t);
  assert.equal(prod.kind, 'production');
  assert.match(prod.tip, /origin\.production/);
  assert.match(prod.tip, /MID\/b/);
  assert.match(prod.tip, /1\.5/);
  const man = originChip({ ...blank(), status: 'filled', amount: 1, source: 'manual', by: 'owner@example.test', date: '2026-11-02' }, it, 'shipping', t);
  assert.match(man.tip, /owner@example.test/);
  const est = originChip({ ...blank(), status: 'filled', amount: 1, source: 'estimate', source_ref: { project: 'other', title: 'Other', iteration: '2' } }, it, 'testing', t);
  assert.match(est.tip, /origin\.estimate.*Other.*"iteration":"2"/);
  const miss = originChip({ ...blank(), hint: 'nearest analog MID/b is x5 by active mass, outside 0.25..4: enter it' }, it, 'manufacturing', t);
  assert.equal(miss.kind, 'missing');
  assert.match(miss.tip, /x5/);
  assert.equal(originChip(estimate().iterations[1].lines.tooling, estimate().iterations[1], 'tooling', t).kind, 'rule');
  assert.equal(originChip(blank(), { ...it, bench: 'none' }, 'testing', t).kind, 'none');
  assert.equal(langOf('zh-CN'), 'zh-CN');
  assert.equal(langOf('en-US'), 'en');
});

test('money stays per currency in the review table; a missing line says so', () => {
  assert.equal(formatMoney({ EUR: 1200, CNY: 300.5 }), 'EUR 1 200.00 · CNY 300.50');
  assert.equal(formatMoney({}), '—');
  const rows = reviewRows({ iterations: [{ index: 1, motors: 2, bench: 'dyno', tooling: { EUR: 10 }, manufacturing: null, testing: {}, shipping: { USD: 5 }, subtotal: { EUR: 10, USD: 5 }, lines: {} }],
    subtotal: {}, contingency_pct: 10, contingency: {}, total: {}, currencies: [], missing: [], missing_count: 1, warnings: [] }, 'missing');
  assert.deepEqual(rows[0], { key: 'it1', iteration: 1, tooling: 'EUR 10.00', manufacturing: 'missing', testing: '—', shipping: 'USD 5.00', subtotal: 'EUR 10.00 · USD 5.00' });
  const counts = sourceCounts(setIterationField(estimate(2), 2, 'bench', 'none'));
  assert.deepEqual(counts, { missing: 6, rule: 1 });
});

test('every key the wizard uses exists in en and zh-CN', () => {
  const src = readFileSync(fileURLToPath(new URL('../components/costEstimate/CostEstimateWizard.tsx', import.meta.url)), 'utf8')
    + readFileSync(fileURLToPath(new URL('../costEstimate.ts', import.meta.url)), 'utf8');
  const used = new Set([...src.matchAll(/'costEstimate\.([A-Za-z.]+)'/g)].map((m) => m[1]));
  for (const k of ['production', 'estimate', 'manual', 'converted', 'rule']) used.add(`source.${k}`);
  const get = (o, path) => path.split('.').reduce((a, k) => (a == null ? a : a[k]), o);
  for (const lang of ['en', 'zh-CN']) for (const key of used) assert.equal(typeof get(locale(lang), key), 'string', `${lang}: costEstimate.${key}`);
});
