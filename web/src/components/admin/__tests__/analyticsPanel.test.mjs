// node --test: Admin > Analytics helpers (analyticsPanel.ts).
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  ANALYTICS_RANGES, formatDuration, formatPercent, bucketLabel, chartData, withShare,
  countryName, analyticsErrorKind,
} from '../analyticsPanel.ts';

test('ranges are 24 h, 7 d, 30 d', () => {
  assert.deepEqual([...ANALYTICS_RANGES], ['24h', '7d', '30d']);
});

test('formatDuration', () => {
  assert.equal(formatDuration(0), '0 s');
  assert.equal(formatDuration(42), '42 s');
  assert.equal(formatDuration(185), '3 min 05 s');
  assert.equal(formatDuration(3720), '1 h 02 min');
  assert.equal(formatDuration(undefined), '0 s');
  assert.equal(formatDuration(-5), '0 s');
});

test('formatPercent', () => {
  assert.equal(formatPercent(40), '40.0 %');
  assert.equal(formatPercent(null), '0.0 %');
  assert.equal(formatPercent('x'), '–');
});

test('bucketLabel: hours and days, unknown shapes pass through', () => {
  assert.equal(bucketLabel('2026-10-14 13:00:00', 'hour'), '13:00');
  assert.equal(bucketLabel('2026-10-14 00:00:00', 'day'), '10-14');
  assert.equal(bucketLabel('2026-10-14T05:00:00Z', 'hour'), '05:00');
  assert.equal(bucketLabel('weird', 'day'), 'weird');
});

test('chartData maps the series and tolerates an empty one', () => {
  const rows = chartData({ unit: 'day', series: [{ t: '2026-10-13 00:00:00', pageviews: 70, visitors: 25 }] });
  assert.deepEqual(rows, [{ label: '10-13', pageviews: 70, visitors: 25 }]);
  assert.deepEqual(chartData({ unit: 'day', series: [] }), []);
});

test('withShare: percentages of the list total, zeros safe', () => {
  assert.deepEqual(withShare([{ name: 'a', count: 3 }, { name: 'b', count: 1 }]).map((r) => r.share), [75, 25]);
  assert.deepEqual(withShare([{ name: 'a', count: 0 }]).map((r) => r.share), [0]);
  assert.deepEqual(withShare([]), []);
});

test('countryName: known code, unknown stays raw', () => {
  assert.equal(countryName('SI'), 'Slovenia');
  assert.equal(countryName('ZZZZ'), 'ZZZZ');
});

test('analyticsErrorKind', () => {
  assert.equal(analyticsErrorKind(503), 'unavailable');
  assert.equal(analyticsErrorKind(403), 'forbidden');
  assert.equal(analyticsErrorKind(500), 'error');
});
