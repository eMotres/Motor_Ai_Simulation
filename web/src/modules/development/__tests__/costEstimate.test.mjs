import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  buildCostSteps, defaultDisplayCurrency, DISPLAY_BY_LANG, displayCurrencyOf, editLine, formatMoney, fxLine, langOf, LINE_IDS, originChip,
  reviewRows, setDisplayCurrency, setIterationField, setManualRate, setSetting, snapshotTotalText, sourceCounts, stepIndexOfMissing,
  stepState, totalsView, iterationSubtotal, lineTotal, mergeEdits, reconcile, fieldLabel,
  acceptSuggestion, addIteration, blankLane, dhlDetails, dhlOrigin, iterationWeeks, laneErrors, projectWeeks, readView, removeIteration, removeLane,
  setLineWeeks, setShipTo, setWeeksOverride, suggestionAt, suggestionKey, tableModel, upsertLane, validWeeks, weeksText, writeView,
  applyAuto, autoDhlIterations, autoKey, setDhlAuto,
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
  const src = ['../components/costEstimate/CostEstimateWizard.tsx', '../components/costEstimate/CostEstimateTable.tsx', '../components/costEstimate/CostShipping.tsx', '../costEstimate.ts']
    .map((f) => readFileSync(fileURLToPath(new URL(f, import.meta.url)), 'utf8')).join('\n');
  const used = new Set([...src.matchAll(/'costEstimate\.([A-Za-z.]+)'/g)].map((m) => m[1]));
  for (const k of ['production', 'estimate', 'manual', 'converted', 'rule', 'dhl']) used.add(`source.${k}`);
  // keys built from a variable: the view toggle, the stage durations, the table and lane column heads
  for (const k of ['table', 'steps']) used.add(`view.${k}`);
  for (const k of LINE_IDS) used.add(`durations.${k}`);
  for (const k of ['iteration', 'tooling', 'motors', 'testing', 'shipping', 'duration', 'total']) used.add(`table.col.${k}`);
  for (const k of ['from', 'to_country', 'quoted_on', 'weight_kg', 'price', 'currency', 'transit_days', 'mode', 'source', 'note']) used.add(`dhl.col.${k}`);
  assert.ok(used.size > 80, `scanned ${used.size} keys`);
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

// ── owner 2026-11-02 (later): one-table view, durations, DHL shipping suggestion ─────────────────────────────────────────

const fill = (e, iteration, line, amount) => editLine(e, { iteration, line }, { amount }, 'me', '2026-11-02');
function priced() {
  let e = { ...estimate(2), fx: ecbFx };
  e = fill(e, 1, 'tooling', 1000); e = fill(e, 1, 'manufacturing', 400); e = fill(e, 1, 'testing', 500); e = fill(e, 1, 'shipping', 300);
  e = fill(e, 2, 'tooling', 200); e = fill(e, 2, 'manufacturing', 100);
  e = setIterationField(e, 2, 'motors', 3);
  e = setIterationField(e, 2, 'bench', 'none');
  return e;
}

test('the table: one row per iteration, motors x price, no-bench testing is 0, totals with contingency', () => {
  const m = tableModel(priced());
  assert.equal(m.rows.length, 2);
  assert.deepEqual([m.rows[0].tooling, m.rows[0].perMotor, m.rows[0].manufacturing, m.rows[0].testing, m.rows[0].shipping, m.rows[0].total], [1000, 400, 800, 500, 300, 2600]);
  assert.deepEqual([m.rows[1].manufacturing, m.rows[1].testing, m.rows[1].testingNeeded, m.rows[1].shipping, m.rows[1].total, m.rows[1].missing], [300, 0, false, null, 500, 1]);
  assert.deepEqual([m.subtotal, m.contingencyPct, m.contingency, m.total, m.missing], [3100, 10, 310, 3410, 1]);
  // the same numbers the server computes (EUR lines)
  assert.equal(m.rows[0].total, 1000 + 2 * 400 + 500 + 300);
  assert.equal(setSetting(priced(), schema, 'contingency_pct', 20) && tableModel(setSetting(priced(), schema, 'contingency_pct', 20)).total, 3720);
});

test('the currency choice converts the Total row only, at the stored rate; without a rate it stays EUR', () => {
  const e = priced();
  const usd = tableModel(e, 'USD');
  assert.deepEqual(usd.display, { currency: 'USD', total: 4262.5, rate: 1.25 });            // 3410 x 1.25
  assert.equal(usd.subtotal, 3100); assert.equal(usd.rows[0].total, 2600); assert.equal(usd.total, 3410);   // everything else is EUR
  assert.deepEqual(tableModel(e, 'CNY').display.total, 27280);
  assert.deepEqual(tableModel(e).display, { currency: 'EUR', total: 3410, rate: 1 });
  assert.deepEqual(tableModel({ ...e, fx: null }, 'USD').display, { currency: 'USD', total: null, rate: null });
  // the table file has exactly one currency selector, on the Total row; the cells carry no currency choice
  const src = readFileSync(fileURLToPath(new URL('../components/costEstimate/CostEstimateTable.tsx', import.meta.url)), 'utf8');
  assert.equal((src.match(/schema\.currencies\.map/g) || []).length, 1);
  assert.ok(src.indexOf('schema.currencies.map') > src.indexOf("t('costEstimate.total')"), 'the selector sits on the Total row');
});

test('+ iteration adds a default row (at most 5); remove renumbers the later rows (at least 1 stays)', () => {
  let e = priced();
  e = addIteration(e, schema);
  assert.deepEqual(e.iterations.map((it) => it.index), [1, 2, 3]);
  assert.equal(e.settings.iterations, 3);
  assert.equal(e.iterations[2].lines.tooling.status, 'missing');
  e = addIteration(addIteration(e, schema), schema);
  assert.equal(e.iterations.length, 5);
  assert.equal(addIteration(e, schema), e, 'no sixth iteration');
  assert.equal(tableModel(e).canAdd, false);
  const removed = removeIteration(priced(), 1);
  assert.deepEqual(removed.iterations.map((it) => it.index), [1]);
  assert.equal(removed.settings.iterations, 1);
  assert.equal(removed.iterations[0].lines.tooling.amount, 200, 'iteration 2 moved up');
  assert.equal(removeIteration(removed, 1), removed, 'the last iteration stays');
  assert.equal(tableModel(removed).canRemove, false);
  assert.equal(priced().iterations.length, 2, 'the input is not mutated');
});

test('the view: Table by default, Steps remembered per viewer; a broken storage never breaks the page', () => {
  const store = () => { const m = new Map(); return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) }; };
  const s = store();
  assert.equal(readView(s, 'a@example.test'), 'table');
  writeView(s, 'a@example.test', 'steps');
  assert.equal(readView(s, 'a@example.test'), 'steps');
  assert.equal(readView(s, 'b@example.test'), 'table', 'another viewer keeps his own');
  writeView(s, 'a@example.test', 'table');
  assert.equal(readView(s, 'a@example.test'), 'table');
  const broken = { getItem: () => { throw new Error('denied'); }, setItem: () => { throw new Error('denied'); } };
  assert.equal(readView(broken, 'a'), 'table');
  assert.doesNotThrow(() => writeView(broken, 'a', 'steps'));
  assert.equal(readView(null, 'a'), 'table');
  const src = readFileSync(fileURLToPath(new URL('../components/costEstimate/CostEstimateWizard.tsx', import.meta.url)), 'utf8');
  assert.match(src, /try \{ return window\.localStorage; \} catch \{ return null; \}/);
  assert.match(src, /view === 'table' \? <Stack/, 'both views render from the same estimate');
});

test('durations: the stages in sequence, testing not counted without a bench, the override wins, the project is the sum', () => {
  let e = estimate(2);
  for (const [line, w] of [['tooling', 6], ['manufacturing', 4], ['testing', 2], ['shipping', 3]]) e = setLineWeeks(e, { iteration: 1, line }, w);
  e = setLineWeeks(e, { iteration: 2, line: 'manufacturing' }, 5);
  e = setLineWeeks(e, { iteration: 2, line: 'testing' }, 9);
  e = setIterationField(e, 2, 'bench', 'none');
  const w1 = iterationWeeks(e.iterations[0]);
  assert.deepEqual([w1.sum, w1.override, w1.total, w1.missing], [15, null, 15, []]);
  const w2 = iterationWeeks(e.iterations[1]);
  assert.deepEqual([w2.sum, w2.testing, w2.total, w2.missing], [5, null, 5, ['tooling', 'shipping']]);
  assert.equal(projectWeeks(e), 20);
  e = setWeeksOverride(e, 2, 8);
  assert.equal(iterationWeeks(e.iterations[1]).total, 8);
  assert.equal(iterationWeeks(e.iterations[1]).sum, 5);
  assert.equal(projectWeeks(e), 23);
  assert.equal(tableModel(e).weeksTotal, 23);
  assert.equal(projectWeeks(setWeeksOverride(e, 2, null)), 20, 'cleared: back to the sum');
  assert.equal(weeksText(2.25), '2.3'); assert.equal(weeksText(null), '—');
});

test('durations are validated like the server: 0..104 per line, 0..416 per iteration', () => {
  assert.ok(validWeeks(0) && validWeeks(104) && validWeeks(null) && validWeeks(2.5));
  assert.ok(!validWeeks(-1) && !validWeeks(105) && !validWeeks(Number.NaN) && !validWeeks(Infinity));
  const e = estimate(1);
  assert.equal(setLineWeeks(e, { iteration: 1, line: 'tooling' }, 200), e, 'refused: unchanged');
  assert.equal(setWeeksOverride(e, 1, 500), e);
  assert.equal(setWeeksOverride(e, 1, 300).iterations[0].weeks_override, 300);
  assert.equal(schema.durations.line_max, 104);
  assert.equal(schema.durations.override_max, 416);
});

test('an old estimate without durations, lanes or shipping settings still works', () => {
  const old = estimate(2);                              // the test fixture has no weeks, no weeks_override, no dhl_lanes
  assert.equal(old.iterations[0].lines.tooling.weeks, undefined);
  const w = iterationWeeks(old.iterations[0]);
  assert.deepEqual([w.sum, w.total, w.missing.length], [0, 0, 4]);
  assert.equal(projectWeeks(old), 0);
  assert.equal(tableModel(old).rows.length, 2);
  assert.ok(suggestionKey(old));
  const { merged } = mergeEdits(old, setLineWeeks(old, { iteration: 1, line: 'tooling' }, 3), old);
  assert.equal(merged.iterations[0].lines.tooling.weeks, 3);
});

test('a 409 keeps my duration override and lane edits on top of the latest estimate', () => {
  const synced = { ...estimate(2), dhl_lanes: [schema.shipping.dhl_lanes[0]] };
  let mine = setWeeksOverride(synced, 1, 12);
  mine = upsertLane(mine, { ...schema.shipping.dhl_lanes[0], price: 21000 }, 0);
  const latest = bumped(setWeeksOverride(synced, 2, 4));
  const { merged, overridden } = mergeEdits(synced, mine, latest);
  assert.equal(merged.iterations[0].weeks_override, 12);
  assert.equal(merged.iterations[1].weeks_override, 4, 'changed elsewhere, untouched here');
  assert.equal(merged.dhl_lanes[0].price, 21000);
  assert.deepEqual(overridden, []);
  const clash = mergeEdits(synced, mine, bumped(removeLane(synced, 0)));
  assert.deepEqual(clash.overridden, [{ kind: 'lanes' }]);
  assert.equal(fieldLabel({ kind: 'lanes' }, schema, 'en', realT('en')), 'DHL quotes');
  assert.equal(fieldLabel({ kind: 'iterField', iteration: 1, id: 'weeks_override' }, schema, 'en', realT('en')), 'Iteration 1 · Override');
});

// what the server's preview returns for an empty shipping cell (made-up numbers)
const dhlRef = { quoted_on: '2026-11-02', from: 'CN Shanghai', to: 'AR Buenos Aires', mode: 'DHL Air Freight (Global Forwarding)', lane_weight_kg: 150, lane_price: 20169.14,
  currency: 'CNY', weight_kg: 85, margin_pct: 20, price: 13715.02, motor_mass_kg: 20, mass_basis: 'complete', transit_days: 16 };
const suggestions = { cells: { 1: { shipping: { amount: 1714.38, source: 'dhl', source_ref: { dhl: dhlRef }, origin: 'DHL quote 2026-11-02, CN Shanghai→AR Buenos Aires, 85 kg, +20 %', weeks: 4 },
  manufacturing: { hint: 'the project\'s motor has no simulated active mass' } } }, motor_mass_kg: 20, motor_mass_basis: 'complete' };

test('a grey suggestion is accepted with its source; a filled line or set weeks are never overwritten', () => {
  const e = estimate(1);
  const ref = { iteration: 1, line: 'shipping' };
  const cell = suggestionAt(suggestions, ref);
  assert.equal(cell.amount, 1714.38);
  assert.equal(suggestionAt(suggestions, { iteration: 2, line: 'shipping' }), undefined);
  const got = acceptSuggestion(e, ref, cell, 'me', '2026-11-02').iterations[0].lines.shipping;
  assert.deepEqual([got.status, got.amount, got.currency, got.source, got.by, got.date, got.weeks], ['filled', 1714.38, 'EUR', 'dhl', 'me', '2026-11-02', 4]);
  assert.equal(got.source_ref.dhl.weight_kg, 85);
  const mine = setLineWeeks(fill(e, 1, 'shipping', 900), ref, 6);
  assert.equal(acceptSuggestion(mine, ref, cell, 'me', '2026-11-02'), mine, 'nothing to accept');
  assert.equal(acceptSuggestion(e, { iteration: 1, line: 'manufacturing' }, suggestionAt(suggestions, { iteration: 1, line: 'manufacturing' }), 'me', 'd'), e, 'a hint is not a value');
  assert.equal(acceptSuggestion(e, ref, undefined, 'me', 'd'), e);
});

test('the DHL tooltip says where the price comes from, the weight, and that duties and import VAT are not included', () => {
  const en = realT('en');
  assert.equal(dhlOrigin(dhlRef, en), 'DHL quote 2026-11-02, CN Shanghai→AR Buenos Aires, 85 kg, +20 %');
  const excluded = schema.shipping.excluded.en;
  const lines = dhlDetails(dhlRef, 2, en, excluded);
  assert.match(lines[0], /85 kg = 2 × \(motor 20 kg, complete, × 1\.3 housing \+ 15 kg crate\)/);
  assert.match(lines[1], /CNY 20169\.14 for 150 kg, 16 days/);
  assert.equal(lines[2], 'Duties and import VAT are not included.');
  assert.deepEqual(dhlDetails(undefined, 2, en, excluded), [excluded]);
  const chip = originChip({ ...blank(), status: 'filled', amount: 1714.38, source: 'dhl', source_ref: { dhl: dhlRef } }, estimate().iterations[0], 'shipping', en);
  assert.equal(chip.kind, 'dhl');
  assert.equal(chip.label, 'DHL quote');
  assert.match(realT('zh-CN')('costEstimate.origin.dhl', { date: 'd', from: 'a', to: 'b', weight: 1, margin: 20 }), /DHL 报价 d，a→b/);
});

test('the lane table: add, edit and remove; a lane with an error is never saved', () => {
  const e = { ...estimate(1), settings: { ...estimate(1).settings, ship_from: 'CN Shanghai', ship_to: null }, dhl_lanes: [] };
  const draft = blankLane(setShipTo(e, 'ar', 'Buenos Aires'), '2026-11-02');
  assert.deepEqual([draft.from, draft.to_country, draft.quoted_on, draft.currency], ['CN Shanghai', 'AR', '2026-11-02', 'CNY']);
  assert.deepEqual(laneErrors(draft), ['weight_kg', 'price']);
  assert.equal(upsertLane(e, draft, null), e, 'refused while weight and price are missing');
  const lane = { ...draft, weight_kg: 150, price: 20169.14, transit_days: 16 };
  let next = upsertLane(e, lane, null);
  assert.equal(next.dhl_lanes.length, 1);
  next = upsertLane(next, { ...lane, price: 21000, to_country: 'de' }, 0);
  assert.deepEqual([next.dhl_lanes.length, next.dhl_lanes[0].price, next.dhl_lanes[0].to_country], [1, 21000, 'DE']);
  assert.deepEqual(laneErrors({ ...lane, to_country: 'Argentina', quoted_on: '02.11.2026', currency: 'GBP', transit_days: 400, from: ' ' }),
    ['from', 'to_country', 'quoted_on', 'currency', 'transit_days']);
  assert.deepEqual(removeLane(next, 0).dhl_lanes, []);
  assert.equal(removeLane(next, 5), next);
  assert.deepEqual(laneErrors(schema.shipping.dhl_lanes[0]), [], 'the seeded lane is valid');
  // ship to: a 2-letter country, else no destination
  assert.deepEqual(setShipTo(e, 'ar', ' Buenos Aires ').settings.ship_to, { country: 'AR', city: 'Buenos Aires' });
  assert.equal(setShipTo(e, 'Arg', 'x').settings.ship_to, null);
  assert.equal(setShipTo(e, '', 'x').settings.ship_to, null);
});

test('the suggestions are fetched again only when what they depend on changes', () => {
  const e = estimate(2);
  const k = suggestionKey(e);
  assert.equal(suggestionKey(editLine(e, { iteration: 1, line: 'tooling' }, { note: 'x' }, 'me', 'd')), k, 'a note does not change them');
  assert.notEqual(suggestionKey(fill(e, 1, 'tooling', 5)), k, 'a cell that is no longer empty');
  assert.equal(suggestionKey(fill(fill(e, 1, 'tooling', 5), 1, 'tooling', 6)), suggestionKey(fill(e, 1, 'tooling', 5)), 'typing more digits does not');
  assert.notEqual(suggestionKey(setIterationField(e, 1, 'motors', 4)), k);
  assert.notEqual(suggestionKey(setShipTo(e, 'AR', '')), k);
  assert.notEqual(suggestionKey(setLineWeeks(e, { iteration: 1, line: 'shipping' }, 2)), k);
});

test('the schema carries the durations and the shipping block in both languages', () => {
  for (const f of schema.shipping.settings) assert.ok(f.label.en && f.label['zh-CN'] && f.help.en && f.help['zh-CN'], f.id);
  assert.deepEqual(schema.shipping.settings.map((f) => f.id), ['ship_from', 'ship_to', 'ship_margin_pct', 'ship_round_eur']);
  assert.equal(schema.shipping.settings[0].default, 'CN Shanghai');
  assert.equal(schema.shipping.settings[2].default, 20);
  assert.equal(schema.shipping.settings[3].default, 100, 'DHL is rounded up to whole 100 EUR by default');
  assert.ok(schema.shipping.excluded['zh-CN'] && schema.durations.help['zh-CN']);
  for (const lang of ['en', 'zh-CN']) assert.match(realT(lang)('costEstimate.weeksMissing', { count: 2 }), /2/);
  assert.equal(realT('en')('costEstimate.weeksMissing', { count: 1 }), '1 stage without weeks');
});

// ── automatic DHL shipping (owner 2026-11-03) ─────────────────────────────────────────────────────────────────────────

const autoLine = (amount, extra = {}) => ({ ...blank(), status: 'filled', amount, source: 'dhl', source_ref: { dhl: { weight_kg: 85, lane_price: 20169.14, price_eur_unrounded: 1714.38, round_eur: 100 } }, weeks: 4, ...extra });
function withShipping(line, motors = 2) {
  const e = estimate(2);
  e.iterations[0].motors = motors;
  e.iterations[0].lines.shipping = line;
  return e;
}

test('the automatic DHL line takes the server numbers; a manual line and the owner weeks are never touched', () => {
  const e = withShipping(autoLine(1800));
  assert.deepEqual(autoDhlIterations(e), [1]);
  const cell = { amount: 5100, weeks: null, source_ref: { dhl: { weight_kg: 250, price_eur_unrounded: 5042.28, round_eur: 100 } }, hint: '' };
  const got = applyAuto({ ...e, iterations: [{ ...e.iterations[0], motors: 6 }, e.iterations[1]] }, { 1: cell }).iterations[0].lines.shipping;
  assert.deepEqual([got.amount, got.source, got.source_ref.dhl.weight_kg, got.weeks, got.hint], [5100, 'dhl', 250, 4, ''], 'weeks stay when the server gives none');
  assert.equal(applyAuto(e, { 1: { ...cell, weeks: 7 } }).iterations[0].lines.shipping.weeks, 7);
  // nothing differs -> the same object (no needless re-render / save)
  assert.equal(applyAuto(e, { 1: { amount: 1800, weeks: 4, source_ref: e.iterations[0].lines.shipping.source_ref, hint: '' } }), e);
  assert.equal(applyAuto(e, undefined), e);
  // a manual line is never overwritten, even if the server names it
  const manual = withShipping({ ...blank(), status: 'filled', amount: 999, source: 'manual' });
  assert.equal(applyAuto(manual, { 1: cell }), manual);
  assert.deepEqual(autoDhlIterations(manual), []);
  // DHL cannot be computed: the last value stays and the hint is shown
  const stale = applyAuto(e, { 1: { amount: null, weeks: null, source_ref: null, hint: 'no DHL quote for BR; add one' } }).iterations[0].lines.shipping;
  assert.deepEqual([stale.amount, stale.hint], [1800, 'no DHL quote for BR; add one']);
  assert.deepEqual(e.iterations[0].lines.shipping, autoLine(1800), 'the input is not mutated');
});

test('the preview is refetched when the motors (or anything the DHL price depends on) change, not when an amount does', () => {
  const e = withShipping(autoLine(1800));
  const k = autoKey(e);
  assert.notEqual(autoKey(setIterationField(e, 1, 'motors', 6)), k);
  assert.notEqual(autoKey(setShipTo(e, 'BR', '')), k);
  assert.notEqual(autoKey(setSetting(e, schema, 'ship_round_eur', 0)), k);
  assert.notEqual(autoKey(setSetting(e, schema, 'ship_margin_pct', 30)), k);
  assert.equal(autoKey(applyAuto(e, { 1: { amount: 5100, weeks: null, source_ref: null, hint: '' } })), k, 'a recomputed amount does not trigger another preview');
  assert.equal(autoKey(editLine(e, { iteration: 1, line: 'tooling' }, { amount: 5 }, 'me', 'd')), k);
  assert.notEqual(suggestionKey(setSetting(e, schema, 'ship_round_eur', 0)), suggestionKey(e));
});

test('a manual shipping line can be put back to automatic DHL; nothing else can', () => {
  const ref = { iteration: 1, line: 'shipping' };
  const manual = withShipping({ ...blank(), status: 'filled', amount: 999, source: 'manual', by: 'me', date: 'd', note: 'kept' });
  const next = setDhlAuto(manual, ref);
  const line = next.iterations[0].lines.shipping;
  assert.deepEqual([line.source, line.amount, line.source_ref, line.note, line.status], ['dhl', 999, null, 'kept', 'filled'], 'the amount stays until the server numbers arrive');
  assert.deepEqual(manual.iterations[0].lines.shipping.source, 'manual');
  const auto = withShipping(autoLine(1800));
  assert.equal(setDhlAuto(auto, ref), auto, 'already automatic');
  const empty = estimate(2);
  assert.equal(setDhlAuto(empty, ref), empty, 'a missing line has its grey suggestion instead');
  assert.equal(setDhlAuto(manual, { iteration: 1, line: 'tooling' }), manual, 'only shipping');
});

test('the DHL tooltip names the rounding and the unrounded quote', () => {
  const en = realT('en');
  const lines = dhlDetails({ ...dhlRef, price_eur_unrounded: 1736.71, round_eur: 100 }, 2, en, 'excl');
  assert.equal(lines.at(-2), 'Quote 1736.71 EUR, rounded up to a multiple of 100 EUR.');
  assert.equal(lines.at(-1), 'excl');
  assert.ok(!dhlDetails({ ...dhlRef, price_eur_unrounded: 1714.38, round_eur: 0 }, 2, en, 'excl').some((l) => /multiple of/.test(l)), 'no rounding, no line');
  for (const lang of ['en', 'zh-CN']) for (const k of ['rounded', 'autoTip', 'autoOnTip', 'autoFailed']) assert.ok(locale(lang).dhl[k], `${lang} ${k}`);
});
