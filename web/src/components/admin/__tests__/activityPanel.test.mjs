// node --test: Admin > Activity helpers (activityPanel.ts).
import test from 'node:test';
import assert from 'node:assert/strict';
import { formatActive, formatTopTabs, sortRows, dayChartData, totalSeconds } from '../activityPanel.ts';

const row = (user, o = {}) => ({
  user, today_s: 0, d7_s: 0, d30_s: 0, sessions_7d: 0, sessions_30d: 0, last_seen: 0,
  top_tabs: [], runs_7d: 0, runs_30d: 0, ...o,
});

test('formatActive', () => {
  assert.equal(formatActive(0), '0 min');
  assert.equal(formatActive(10), '< 1 min');
  assert.equal(formatActive(60), '1 min');
  assert.equal(formatActive(2700), '45 min');
  assert.equal(formatActive(11100), '3 h 05 min');
  assert.equal(formatActive(undefined), '0 min');
});

test('formatTopTabs uses the label mapper', () => {
  const tabs = [{ tab: 'compare', seconds: 7800 }, { tab: 'motors', seconds: 2400 }];
  assert.equal(formatTopTabs(tabs), 'compare 2 h 10 min, motors 40 min');
  assert.equal(formatTopTabs(tabs, (t) => t.toUpperCase()), 'COMPARE 2 h 10 min, MOTORS 40 min');
  assert.equal(formatTopTabs([]), '');
});

test('sortRows: numeric desc/asc, text, ties broken by user, input untouched', () => {
  const rows = [row('b@x', { d7_s: 100 }), row('a@x', { d7_s: 300 }), row('c@x', { d7_s: 100 })];
  assert.deepEqual(sortRows(rows, 'd7_s', 'desc').map((r) => r.user), ['a@x', 'b@x', 'c@x']);
  assert.deepEqual(sortRows(rows, 'd7_s', 'asc').map((r) => r.user), ['b@x', 'c@x', 'a@x']);
  assert.deepEqual(sortRows(rows, 'user', 'desc').map((r) => r.user), ['c@x', 'b@x', 'a@x']);
  assert.deepEqual(rows.map((r) => r.user), ['b@x', 'a@x', 'c@x']);
});

test('dayChartData and totalSeconds', () => {
  const days = [
    { day: '2027-01-14', active_s: 3600, sessions: 1, first_ts: 1, last_ts: 2, tabs: {} },
    { day: '2027-01-15', active_s: 90, sessions: 1, first_ts: 1, last_ts: 2, tabs: {} },
  ];
  assert.deepEqual(dayChartData(days), [{ label: '01-14', minutes: 60 }, { label: '01-15', minutes: 2 }]);
  assert.equal(totalSeconds(days), 3690);
  assert.deepEqual(dayChartData([]), []);
});
