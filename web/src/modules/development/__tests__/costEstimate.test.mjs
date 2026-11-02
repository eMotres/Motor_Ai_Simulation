import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  buildCostSteps, defaultDisplayCurrency, DISPLAY_BY_LANG, displayCurrencyOf, editLine, formatMoney, fxLine, langOf, LINE_IDS, lineConverted, originChip,
  reviewRows, setDisplayCurrency, setIterationField, setManualRate, setSetting, setToolingMode, snapshotTotalText, sourceCounts, stepIndexOfMissing,
  stepState, toolingMode, totalsView,
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

// ── display currency and exchange rates (made-up rates: 1 EUR = 8 CNY = 1.25 USD) ────────────────────────────────────

// a tiny t(): the real en / zh-CN strings with {placeholders} filled
const realT = (lang) => (key, args = {}) => {
  const s = key.replace(/^costEstimate\./, '').split('.').reduce((a, k) => a?.[k], locale(lang));
  assert.equal(typeof s, 'string', `${lang}: ${key}`);
  return s.replace(/\{(\w+)\}/g, (_m, k) => String(args[k]));
};
const ecbFx = { base: 'EUR', date: '2026-11-02', rates: { CNY: 8, USD: 1.25 }, source: 'ECB', ecb: { date: '2026-11-02', rates: { CNY: 8, USD: 1.25 } }, manual: {}, manual_date: null };
const money = (cur, v) => ({ [cur]: v });
// what the server computes for: tooling EUR 1000, manufacturing CNY 800 (= EUR 100), testing USD 125 (= EUR 100); shipping missing
function computedEur(available = true) {
  const iteration = { index: 1, motors: 2, bench: 'dyno', tooling: money('EUR', 1000), manufacturing: money('CNY', 800), testing: money('USD', 125), shipping: null,
    subtotal: { EUR: 1000, CNY: 800, USD: 125 },
    lines: { tooling: { amount: money('EUR', 1000), origin: '' }, manufacturing: { amount: money('CNY', 800), origin: '' }, testing: { amount: money('USD', 125), origin: '' }, shipping: { amount: null, origin: '' } } };
  return { iterations: [iteration], subtotal: { EUR: 1000, CNY: 800, USD: 125 }, contingency_pct: 10, contingency: { EUR: 100, CNY: 80, USD: 12.5 }, total: { EUR: 1100, CNY: 880, USD: 137.5 }, currencies: ['EUR', 'CNY', 'USD'],
    converted: available
      ? { currency: 'EUR', available: true, missing_rates: [], iterations: [{ index: 1, lines: { tooling: 1000, manufacturing: 100, testing: 100, shipping: null }, subtotal: 1200 }], subtotal: 1200, contingency: 120, total: 1320 }
      : { currency: 'EUR', available: false, missing_rates: ['CNY', 'USD'], iterations: [{ index: 1, lines: { tooling: 1000, manufacturing: null, testing: null, shipping: null }, subtotal: null }], subtotal: null, contingency: null, total: null },
    missing: [{ iteration: 1, line: 'shipping', reason: 'not filled' }], missing_count: 1, warnings: [] };
}

test('the display currency follows the UI language until the owner picks one', () => {
  assert.deepEqual(DISPLAY_BY_LANG, schema.display_currency_by_locale);           // the web default is the server schema's
  assert.equal(defaultDisplayCurrency('en'), 'EUR');
  assert.equal(defaultDisplayCurrency('zh-CN'), 'CNY');
  assert.equal(defaultDisplayCurrency(langOf('en-US')), 'EUR');
  assert.equal(defaultDisplayCurrency(langOf('zh-CN')), 'CNY');
  assert.equal(defaultDisplayCurrency(langOf('zh')), 'CNY');
  assert.equal(defaultDisplayCurrency(langOf(undefined), schema), 'EUR');
  const e = estimate();
  assert.equal(displayCurrencyOf(e.settings, 'en', schema), 'EUR');
  assert.equal(displayCurrencyOf(e.settings, 'zh-CN', schema), 'CNY');
  // the owner's pick is stored in the estimate and wins in both languages; "auto" (null) goes back to the language
  const usd = setDisplayCurrency(e, 'USD');
  assert.equal(displayCurrencyOf(usd.settings, 'en', schema), 'USD');
  assert.equal(displayCurrencyOf(usd.settings, 'zh-CN', schema), 'USD');
  assert.equal(displayCurrencyOf(setDisplayCurrency(usd, null).settings, 'zh-CN', schema), 'CNY');
  assert.equal(e.settings.display_currency, undefined);                            // the input is not mutated
});

test('converted totals are shown in the display currency, the originals stay for the tooltip', () => {
  const c = computedEur();
  const tv = totalsView(c);
  assert.equal(tv.converted, true);
  assert.equal(tv.total, 'EUR 1 320.00');
  assert.equal(tv.subtotal, 'EUR 1 200.00');
  assert.equal(tv.contingency, 'EUR 120.00');
  assert.equal(tv.original, 'EUR 1 100.00 · CNY 880.00 · USD 137.50');
  const rows = reviewRows(c, 'missing');
  assert.deepEqual(rows[0], { key: 'it1', iteration: 1, tooling: 'EUR 1 000.00', manufacturing: 'EUR 100.00', testing: 'EUR 100.00', shipping: 'missing',
    subtotal: 'EUR 1 200.00', original: 'EUR 1 000.00 · CNY 800.00 · USD 125.00' });
  // the line entered in CNY / USD shows its converted value in grey; one already in the display currency shows nothing
  assert.equal(lineConverted(c, 1, 'manufacturing'), 'EUR 100.00');
  assert.equal(lineConverted(c, 1, 'testing'), 'EUR 100.00');
  assert.equal(lineConverted(c, 1, 'tooling'), null);
  assert.equal(lineConverted(c, 1, 'shipping'), null);
});

test('with a rate missing the totals stay per currency, as before', () => {
  const c = computedEur(false);
  const tv = totalsView(c);
  assert.equal(tv.converted, false);
  assert.equal(tv.total, 'EUR 1 100.00 · CNY 880.00 · USD 137.50');
  assert.deepEqual(tv.missingRates, ['CNY', 'USD']);
  assert.equal(reviewRows(c, 'missing')[0].manufacturing, 'CNY 800.00');
  assert.equal(reviewRows(c, 'missing')[0].original, undefined);
  assert.equal(lineConverted(c, 1, 'manufacturing'), null);                      // cannot be converted: nothing to show
  const noConverted = { ...computedEur(), converted: undefined };                // an answer of an older server
  assert.equal(totalsView(noConverted).total, 'EUR 1 100.00 · CNY 880.00 · USD 137.50');
});

test('the review line names the rates: ECB with its date, or manual', () => {
  const en = realT('en');
  assert.equal(fxLine(ecbFx, en), 'converted at ECB 2026-11-02 (1 EUR = 8 CNY, 1.25 USD)');
  assert.equal(fxLine(ecbFx, realT('zh-CN')), '按欧洲央行 2026-11-02 汇率换算（1 EUR = 8 CNY, 1.25 USD）');
  const manual = setManualRate({ ...estimate(), fx: ecbFx }, 'CNY', 7.5);
  assert.equal(manual.fx.source, 'manual');
  assert.deepEqual(manual.fx.rates, { CNY: 7.5, USD: 1.25 });
  assert.equal(fxLine({ ...manual.fx, manual_date: '2026-11-05', date: '2026-11-05' }, en), 'manual rates 2026-11-05 (1 EUR = 7.5 CNY (manual), 1.25 USD)');
  assert.equal(fxLine(null, en), null);
  assert.equal(fxLine({ ...ecbFx, rates: {} }, en), null);
});

test('a manual rate is set and cleared; clearing falls back to the ECB rate, with no rate known there is none', () => {
  const e = { ...estimate(), fx: ecbFx };
  const set = setManualRate(e, 'CNY', 7.5);
  assert.deepEqual(set.fx.manual, { CNY: 7.5 });
  assert.equal(e.fx.manual.CNY, undefined);                                      // the input is not mutated
  const cleared = setManualRate(set, 'CNY', null);
  assert.equal(cleared.fx.source, 'ECB');
  assert.deepEqual(cleared.fx.manual, {});
  assert.deepEqual(cleared.fx.rates, { CNY: 8, USD: 1.25 });
  assert.deepEqual(setManualRate(set, 'CNY', 0).fx.manual, {});                  // 0 is not a rate
  const fromNothing = setManualRate(estimate(), 'USD', 1.3);                     // no ECB rate known: only his own
  assert.deepEqual(fromNothing.fx.rates, { USD: 1.3 });
  assert.deepEqual(setManualRate(fromNothing, 'USD', null).fx.rates, {});
});

test('a frozen snapshot shows its total as it was frozen', () => {
  assert.equal(snapshotTotalText({ totals: { total: { EUR: 1100, CNY: 880 }, converted: computedEur().converted, missing_count: 0 } }), 'EUR 1 320.00');
  assert.equal(snapshotTotalText({ totals: { total: { EUR: 1100, CNY: 880 }, converted: computedEur(false).converted, missing_count: 0 } }), 'EUR 1 100.00 · CNY 880.00');
  assert.equal(snapshotTotalText({ totals: { total: { EUR: 5 }, missing_count: 0 } }), 'EUR 5.00');                      // an older snapshot
});

test('the new strings exist in both languages', () => {
  for (const lang of ['en', 'zh-CN']) {
    const l = locale(lang);
    for (const k of ['displayCurrency', 'displayCurrencyTip', 'displayAuto', 'originalsTip']) assert.equal(typeof l[k], 'string', `${lang}: ${k}`);
    for (const k of ['ecb', 'manual', 'manualTag', 'none', 'noneFor', 'stale', 'settingsTitle', 'settingsTip', 'rateLabel', 'useEcb', 'manualChip', 'ecbChip', 'noRate']) assert.equal(typeof l.fx[k], 'string', `${lang}: fx.${k}`);
  }
});
