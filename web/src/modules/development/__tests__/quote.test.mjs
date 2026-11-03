import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { convertPrice, QUOTE_LINES, quoteCurrencies, quoteMoney, quoteWeeks, sharedStatus, shareIsStale } from '../quote.ts';

const locale = (lang) => JSON.parse(readFileSync(fileURLToPath(new URL(`../../../locales/${lang}/development.json`, import.meta.url)), 'utf8'));
const t = (key, args) => (args ? `${key}|${JSON.stringify(args)}` : key);
const src = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');

const quote = {
  currency: 'EUR', total_price: 3209, total_weeks: 25, delivery: 'DAP Buenos Aires', payment_terms: '70 % / 30 %', shared_on: '2026-11-03',
  exchange_rates: { date: '2026-11-02', per_eur: { CNY: 8, USD: 1.25 } },
  iterations: [{ index: 1, motors: 2, bench: 'dyno', price: 2169, weeks: 15,
    lines: { tooling: { price: 1074, weeks: 6 }, manufacturing: { price: 717, weeks: 4 }, testing: { price: 269, weeks: 2 }, shipping: { price: 109, weeks: 3 } } }],
};

test('the display switch offers EUR and only the currencies the share carried a rate for', () => {
  assert.deepEqual(QUOTE_LINES, ['tooling', 'manufacturing', 'testing', 'shipping']);
  assert.deepEqual(quoteCurrencies(quote), ['EUR', 'USD', 'CNY']);
  assert.deepEqual(quoteCurrencies({ exchange_rates: null }), ['EUR']);
  assert.deepEqual(quoteCurrencies({ exchange_rates: { date: 'x', per_eur: { USD: 1.1 } } }), ['EUR', 'USD']);
});

test('prices are whole units; an unknown rate keeps EUR', () => {
  assert.equal(quoteMoney(3209, 'EUR', quote), 'EUR 3 209');
  assert.equal(quoteMoney(3209, 'USD', quote), 'USD 4 011');           // 3209 x 1.25 = 4011.25
  assert.equal(quoteMoney(3209, 'CNY', quote), 'CNY 25 672');
  assert.deepEqual(convertPrice(100, 'CNY', { exchange_rates: null }), { currency: 'EUR', value: 100 });
  assert.equal(quoteWeeks(null), '—');
  assert.equal(quoteWeeks(2.5), '2.5');
});

test('the admin status line names the shared revision and date; a later revision makes it stale', () => {
  const mark = { snapshot_n: 3, revision: 40, shared_at: '2026-11-03T09:12:00+00:00', shared_by: 'a' };
  assert.equal(sharedStatus(mark, t), 'costEstimate.share.status|{"rev":40,"date":"2026-11-03"}');
  assert.equal(sharedStatus(null, t), null);
  assert.equal(shareIsStale(mark, 40), false);
  assert.equal(shareIsStale(mark, 41), true);
  assert.equal(shareIsStale(null, 41), false);
  const en = locale('en').costEstimate.share.status.replace('{rev}', '40').replace('{date}', '2026-11-03');
  assert.equal(en, 'Shared rev 40 on 2026-11-03');
});

test('EN and zh-CN have every quote string', () => {
  const en = locale('en'); const zh = locale('zh-CN');
  const keys = (o, p = '') => Object.entries(o).flatMap(([k, v]) => (v && typeof v === 'object' ? keys(v, `${p}${k}.`) : [`${p}${k}`]));
  assert.deepEqual(keys(zh.customerQuote).sort(), keys(en.customerQuote).sort());
  assert.deepEqual(keys(zh.costEstimate.share).sort(), keys(en.costEstimate.share).sort());
  for (const loc of [en, zh]) assert.ok(loc.tabs.quote && loc.costEstimate.paymentTerms && loc.costEstimate.paymentTermsTip);
});

test('the Quote view is read-only: no inputs but the currency switch, no sources, notes or captions', () => {
  const view = src('../components/ProjectQuote.tsx');
  assert.ok(!/TextField|NumCell|onCommit|placeholder/.test(view));
  assert.ok(!/source|note|hint|contingency|caption|helperText/i.test(view.replace(/\/\*\*[\s\S]*?\*\//g, '')));
  // the tab exists only when the server says a quote is shared; the admin tab stays admin-only
  const page = src('../components/DevelopmentPage.tsx');
  assert.match(page, /selected\.quote_shared \? \[\{ id: 'quote'/);
  assert.match(page, /isAdmin \? \[\{ id: 'cost'/);
});
