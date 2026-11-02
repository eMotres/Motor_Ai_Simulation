import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  buildCostSteps, defaultDisplayCurrency, DISPLAY_BY_LANG, displayCurrencyOf, editLine, formatMoney, fxLine, langOf, LINE_IDS, originChip,
  reviewRows, setDisplayCurrency, setIterationField, setManualRate, setSetting, snapshotTotalText, sourceCounts, stepIndexOfMissing,
  stepState, totalsView, iterationSubtotal, lineTotal, mergeEdits, reconcile, fieldLabel,
} from '../costEstimate.ts';

// the real schema the server serves
const schema = JSON.parse(readFileSync(fileURLToPath(new URL('../../../../../src/motor_ai_sim/development/cost_estimate_schema.json', import.meta.url)), 'utf8'));
const locale = (lang) => JSON.parse(readFileSync(fileURLToPath(new URL(`../../../locales/${lang}/development.json`, import.meta.url)), 'utf8')).costEstimate;
const t = (key, args) => (args ? `${key}|${JSON.stringify(args)}` : key);

const blank = () => ({ status: 'missing', amount: null, currency: 'EUR', pct: null, source: null, source_ref: null, factor: null, date: null, by: null, note: '', hint: '' });
const iteration = (index) => ({ index, motors: 2, bench: 'dyno', lines: { tooling: blank(), manufacturing: blank(), testing: blank(), shipping: blank() } });
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
  assert.deepEqual(stepState(steps[2], estimate()), { state: 'empty', missing: 4 });
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

test('tooling is the same window in every iteration: an amount, no % of iteration 1', () => {
  let e = estimate(3);
  for (const it of e.iterations) assert.deepEqual([it.lines.tooling.status, it.lines.tooling.pct, it.lines.tooling.source], ['missing', null, null]);
  e = editLine(e, { iteration: 2, line: 'tooling' }, { amount: 2000 }, 'o', '2026-11-02', 'EUR');
  const t2 = e.iterations[1].lines.tooling;
  assert.deepEqual([t2.status, t2.amount, t2.currency, t2.source, t2.pct], ['filled', 2000, 'EUR', 'manual', null]);
  // the schema has no later_pct_default any more, and the wizard has no % mode
  assert.equal(schema.lines.find((l) => l.id === 'tooling').later_pct_default, undefined);
  const src = readFileSync(fileURLToPath(new URL('../components/costEstimate/CostEstimateWizard.tsx', import.meta.url)), 'utf8');
  assert.ok(!/pctMode|ToggleButton|ofIterationOne/.test(src));
  for (const lang of ['en', 'zh-CN']) for (const k of ['pctMode', 'amountMode', 'ofIterationOne']) assert.equal(locale(lang)[k], undefined, `${lang}: ${k}`);
});

test('settings: the iteration list follows the count; complex makes it at least 3; motors and bench per iteration', () => {
  let e = setSetting(estimate(2), schema, 'complex', true);
  assert.equal(e.settings.iterations, 3);
  assert.equal(e.iterations.length, 3);
  assert.equal(e.iterations[2].motors, 2);
  assert.equal(e.iterations[2].lines.tooling.status, 'missing');
  e = setSetting(e, schema, 'iterations', 1);
  assert.deepEqual(e.iterations.map((it) => it.index), [1]);
  assert.equal(setSetting(e, schema, 'iterations', 9).iterations.length, 5);
  e = setIterationField(estimate(), 2, 'bench', 'none');
  assert.equal(e.iterations[1].bench, 'none');
  assert.deepEqual(stepState({ kind: 'iteration', iteration: 2 }, e), { state: 'empty', missing: 3 });   // testing not needed
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
  assert.equal(originChip(estimate().iterations[1].lines.tooling, estimate().iterations[1], 'tooling', t).kind, 'missing');
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
  assert.deepEqual(counts, { missing: 7 });
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
  // ICU plural {count, plural, one {# motor} other {# motors}} (English one/other), then plain {placeholders}
  const plural = s.replace(/\{(\w+), plural, one \{([^}]*)\} other \{([^}]*)\}\}/g, (_m, k, one, other) => (args[k] === 1 ? one : other).replace('#', String(args[k])));
  return plural.replace(/\{(\w+)\}/g, (_m, k) => String(args[k]));
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

test('the final table is in EUR unless the owner picks USD / CNY; the UI language does not change it', () => {
  assert.deepEqual(DISPLAY_BY_LANG, schema.display_currency_by_locale);           // the web default is the server schema's
  assert.deepEqual(DISPLAY_BY_LANG, { en: 'EUR', 'zh-CN': 'EUR' });
  for (const lang of ['en', 'zh-CN']) assert.equal(defaultDisplayCurrency(lang), 'EUR');
  assert.equal(defaultDisplayCurrency(langOf('en-US')), 'EUR');
  assert.equal(defaultDisplayCurrency(langOf('zh-CN')), 'EUR');
  assert.equal(defaultDisplayCurrency(langOf('zh')), 'EUR');
  assert.equal(defaultDisplayCurrency(langOf(undefined), schema), 'EUR');
  const e = estimate();
  assert.equal(displayCurrencyOf(e.settings, 'en', schema), 'EUR');
  assert.equal(displayCurrencyOf(e.settings, 'zh-CN', schema), 'EUR');
  // the owner's pick (final table only) is stored in the estimate and wins in both languages; null goes back to EUR
  const usd = setDisplayCurrency(e, 'USD');
  assert.equal(displayCurrencyOf(usd.settings, 'en', schema), 'USD');
  assert.equal(displayCurrencyOf(usd.settings, 'zh-CN', schema), 'USD');
  assert.equal(displayCurrencyOf(setDisplayCurrency(usd, null).settings, 'zh-CN', schema), 'EUR');
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
});

test('with a rate missing the totals stay per currency, as before', () => {
  const c = computedEur(false);
  const tv = totalsView(c);
  assert.equal(tv.converted, false);
  assert.equal(tv.total, 'EUR 1 100.00 · CNY 880.00 · USD 137.50');
  assert.deepEqual(tv.missingRates, ['CNY', 'USD']);
  assert.equal(reviewRows(c, 'missing')[0].manufacturing, 'CNY 800.00');
  assert.equal(reviewRows(c, 'missing')[0].original, undefined);
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
    for (const k of ['displayCurrency', 'displayCurrencyTip', 'originalsTip']) assert.equal(typeof l[k], 'string', `${lang}: ${k}`);
    for (const k of ['ecb', 'manual', 'manualTag', 'none', 'noneFor', 'stale', 'settingsTitle', 'settingsTip', 'rateLabel', 'useEcb', 'manualChip', 'ecbChip', 'noRate']) assert.equal(typeof l.fx[k], 'string', `${lang}: fx.${k}`);
  }
});


// ── owner feedback 2026-11-02: label, plural, no scary banner on a 409, converted iteration total ───────────────────────

test('the motors field reads "Number of motors" and the multiplier is a plural', () => {
  const f = schema.iteration_fields.find((x) => x.id === 'motors');
  assert.equal(f.label.en, 'Number of motors');
  assert.equal(f.label['zh-CN'], '电机数量');
  const en = realT('en'); const zh = realT('zh-CN');
  assert.equal(en('costEstimate.timesMotors', { count: 1 }), '× 1 motor');
  assert.equal(en('costEstimate.timesMotors', { count: 2 }), '× 2 motors');
  assert.equal(en('costEstimate.timesMotors', { count: 12 }), '× 12 motors');
  assert.equal(zh('costEstimate.timesMotors', { count: 1 }), '× 1 台');
  assert.equal(zh('costEstimate.timesMotors', { count: 2 }), '× 2 台');
  assert.match(locale('en').timesMotors, /plural, one \{# motor\} other \{# motors\}/);
});

// synced = what the server had when the user started; latest = the server now (saved elsewhere, revision 4)
const withEdit = (e, patch) => editLine(e, { iteration: 1, line: 'manufacturing' }, patch, 'me', '2026-11-02');
const bumped = (e) => ({ ...e, revision: e.revision + 1 });

test('a 409 with no unsaved local edits: the latest estimate is shown as it is, nothing to save, no note', () => {
  const synced = estimate();
  const latest = bumped(withEdit(synced, { amount: 700 }));      // somebody else typed 700
  const r = reconcile(false, synced, synced, latest);
  assert.deepEqual(r.estimate, latest);
  assert.equal(r.save, false);
  assert.deepEqual(r.overridden, []);
  assert.equal(r.estimate.revision, 4);
});

test('a 409 with unsaved edits: the latest with the edits re-applied on top, saved again; no note when nothing clashes', () => {
  const synced = estimate();
  const mine = withEdit(synced, { amount: 450 });                                   // my pending edit: manufacturing 450
  const latest = bumped(editLine(synced, { iteration: 1, line: 'shipping' }, { amount: 90 }, 'other', '2026-11-02'));   // elsewhere: shipping 90
  const r = reconcile(true, synced, mine, latest);
  assert.equal(r.save, true);
  assert.equal(r.estimate.revision, 4, 'saved against the latest revision');
  assert.equal(r.estimate.iterations[0].lines.manufacturing.amount, 450, 'my edit is re-applied');
  assert.equal(r.estimate.iterations[0].lines.shipping.amount, 90, 'the other session\'s change stays');
  assert.deepEqual(r.overridden, []);
});

test('a re-applied field that was changed elsewhere too is named; mine wins', () => {
  const synced = estimate();
  const mine = setIterationField(withEdit(synced, { amount: 450 }), 1, 'motors', 3);
  const latest = bumped(setIterationField(withEdit(synced, { amount: 700 }), 2, 'motors', 4));   // elsewhere: same line 700, iteration 2 motors 4
  const r = mergeEdits(synced, mine, latest);
  assert.equal(r.merged.iterations[0].lines.manufacturing.amount, 450);
  assert.equal(r.merged.iterations[0].motors, 3);
  assert.equal(r.merged.iterations[1].motors, 4, 'a change elsewhere to a field I did not touch stays');
  assert.deepEqual(r.overridden, [{ kind: 'line', iteration: 1, line: 'manufacturing' }]);
  // an elsewhere change to the very same value is no clash
  assert.deepEqual(mergeEdits(synced, mine, bumped(mine)).overridden, []);
  const name = fieldLabel(r.overridden[0], schema, 'en', realT('en'));
  assert.equal(name, 'Iteration 1 · Motor manufacturing');
  assert.equal(realT('en')('costEstimate.keptNote', { field: name }), 'Updated from another session; your change to Iteration 1 · Motor manufacturing was kept.');
  assert.match(realT('zh-CN')('costEstimate.keptNote', { field: 'x' }), /x/);
  assert.equal(fieldLabel({ kind: 'iterField', iteration: 2, id: 'motors' }, schema, 'en', realT('en')), 'Iteration 2 · Number of motors');
});

test('settings and manual rates edited locally survive a 409 too', () => {
  const synced = estimate(2);
  let mine = setSetting(synced, schema, 'iterations', 3);
  mine = setManualRate(mine, 'CNY', 7.5);
  const latest = bumped(setDisplayCurrency(synced, 'USD'));
  const { merged, overridden } = mergeEdits(synced, mine, latest);
  assert.equal(merged.iterations.length, 3);
  assert.equal(merged.settings.display_currency, 'USD', 'changed elsewhere, untouched here');
  assert.equal(merged.fx.manual.CNY, 7.5);
  assert.equal(merged.fx.rates.CNY, 7.5);
  assert.deepEqual(overridden, []);
});

// what the server computes when every line is EUR (the only currency of a line)
function computedAllEur() {
  const lines = { tooling: { amount: money('EUR', 1000), origin: '' }, manufacturing: { amount: money('EUR', 800), origin: '' }, testing: { amount: money('EUR', 100), origin: '' }, shipping: { amount: null, origin: '' } };
  const iteration = { index: 1, motors: 2, bench: 'dyno', tooling: money('EUR', 1000), manufacturing: money('EUR', 800), testing: money('EUR', 100), shipping: null, subtotal: money('EUR', 1900), lines };
  const conv = (currency, k) => ({ currency, available: true, missing_rates: [], iterations: [{ index: 1, lines: { tooling: 1000 * k, manufacturing: 800 * k, testing: 100 * k, shipping: null }, subtotal: 1900 * k }], subtotal: 1900 * k, contingency: 190 * k, total: 2090 * k });
  return { iterations: [iteration], subtotal: money('EUR', 1900), contingency_pct: 10, contingency: money('EUR', 190), total: money('EUR', 2090), currencies: ['EUR'], converted: conv('EUR', 1),
    missing: [{ iteration: 1, line: 'shipping', reason: 'not filled' }], missing_count: 1, warnings: [], _usd: conv('USD', 1.25) };
}

test('in the iteration steps every amount and total is EUR, whatever the final table is shown in', () => {
  const c = computedAllEur();
  assert.equal(iterationSubtotal(c, 1), 'EUR 1 900.00');
  assert.equal(lineTotal(c, 1, 'manufacturing'), 'EUR 800.00');          // per motor x the motors
  assert.equal(lineTotal(c, 1, 'shipping'), '—');
  assert.equal(lineTotal(c, 1, 'tooling'), 'EUR 1 000.00');
  // the owner picks USD for the final table: the iteration cards stay EUR, only the review table follows
  const usd = { ...c, converted: c._usd };
  assert.equal(iterationSubtotal(usd, 1), 'EUR 1 900.00');
  assert.equal(lineTotal(usd, 1, 'manufacturing'), 'EUR 800.00');
  assert.equal(reviewRows(usd, 'missing')[0].subtotal, 'USD 2 375.00');
  assert.equal(totalsView(usd).total, 'USD 2 612.50');
  assert.equal(reviewRows(c, 'missing')[0].subtotal, 'EUR 1 900.00');
});

// ── every amount is EUR ───────────────────────────────────────────────────────────────────────────────────────────────

test('a typed amount is EUR: an old converted CNY line becomes EUR, and there is no currency choice on a line', () => {
  const base = estimate();
  const ref = { iteration: 1, line: 'manufacturing' };
  const converted = { ...base.iterations[0].lines.manufacturing, status: 'filled', amount: 1000, currency: 'CNY', source: 'converted', note: 'was CNY 8000.00' };
  const withConverted = { ...base, iterations: [{ ...base.iterations[0], lines: { ...base.iterations[0].lines, manufacturing: converted } }, base.iterations[1]] };
  const typed = editLine(withConverted, ref, { amount: 30000 }, 'me', '2026-11-02');
  const line = typed.iterations[0].lines.manufacturing;
  assert.deepEqual([line.amount, line.currency, line.source, line.by], [30000, 'EUR', 'manual', 'me']);
  // a missing line preset to another currency takes EUR too; a note alone leaves the line as it is
  const missing = { ...base, iterations: [{ ...base.iterations[0], lines: { ...base.iterations[0].lines, shipping: { ...blank(), currency: 'CNY' } } }, base.iterations[1]] };
  assert.equal(editLine(missing, { iteration: 1, line: 'shipping' }, { amount: 90 }, 'me', '2026-11-02').iterations[0].lines.shipping.currency, 'EUR');
  assert.equal(editLine(withConverted, ref, { note: 'x' }, 'me', '2026-11-02').iterations[0].lines.manufacturing.currency, 'CNY');
  // the wizard: no currency selector on the lines, the display-currency selector only in the review
  const src = readFileSync(fileURLToPath(new URL('../components/costEstimate/CostEstimateWizard.tsx', import.meta.url)), 'utf8');
  assert.ok(!/costEstimate\.currency'/.test(src), 'no per-line currency label');
  assert.equal((src.match(/costEstimate\.displayCurrency'\)\}<\/Typography>/g) || []).length, 1, 'one display-currency selector');
  assert.ok(src.indexOf("t('costEstimate.byIteration')") < src.indexOf("t('costEstimate.displayCurrency')"), 'the selector sits in the review card');
  assert.ok(src.indexOf("t('costEstimate.displayCurrency')") > src.indexOf('const reviewStep'));
});

test('the rates block lists USD and CNY (ECB or manual); the schema has no per-line currency setting', () => {
  assert.deepEqual(schema.currencies.filter((c) => c !== 'EUR'), ['CNY', 'USD']);
  assert.equal(schema.line_currency, 'EUR');
  assert.ok(!schema.settings.some((f) => f.id === 'currency'));
});
