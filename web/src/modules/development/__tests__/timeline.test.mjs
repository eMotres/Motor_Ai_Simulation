import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  addIteration, barGeometry, dayNumber, ganttRange, isoOfDay, iterationDated, laneTip, monthTicks, putBody, removeLastIteration,
  setIterationStart, setLaneField, setPropeller, statusColor, TIMELINE_LANES, timelineWeeks,
} from '../timeline.ts';

const locale = (lang) => JSON.parse(readFileSync(fileURLToPath(new URL(`../../../locales/${lang}/development.json`, import.meta.url)), 'utf8'));
const t = (key, args) => (args ? `${key}|${JSON.stringify(args)}` : key);
const src = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');

const lane = (id, o = {}) => ({ lane: id, weeks: 2, plan_start: null, plan_end: null, forecast_start: null, forecast_end: null, status: 'planned', readiness_pct: null, note: '', ...o });
const view = {
  today: '2026-11-03', motor: { en: 'Test motor', zh: '测试电机' }, baseline_on: null,
  iterations: [{ index: 1, start_date: '2026-11-09', lanes: [
    lane('tooling', { plan_start: '2026-11-09', plan_end: '2026-12-07', forecast_start: '2026-11-09', forecast_end: '2026-12-01' }),
    lane('motor', { plan_start: '2026-11-09', plan_end: '2026-12-07', forecast_start: '2026-11-09', forecast_end: '2026-12-21', status: 'late', readiness_pct: 50, note: 'Waiting for magnets' }),
  ] }, { index: 2, start_date: null, lanes: [lane('tooling'), lane('shipping', { weeks: null })] }],
};

test('the lanes come in the fixed order; dates map to days and back', () => {
  assert.deepEqual(TIMELINE_LANES, ['tooling', 'motor', 'free_run', 'dyno', 'propeller', 'shipping']);
  assert.equal(isoOfDay(dayNumber('2026-11-03')), '2026-11-03');
  assert.equal(dayNumber('2026-12-01') - dayNumber('2026-11-01'), 30);
});

test('the Gantt range spans every date and today with padding; bars sit on it', () => {
  const r = ganttRange(view);
  assert.equal(isoOfDay(r.start), '2026-10-27');                 // today - 7
  assert.equal(isoOfDay(r.end), '2027-01-04');                   // the latest forecast + 14
  assert.equal(r.days, r.end - r.start);
  assert.deepEqual(barGeometry('2026-10-28', '2026-10-30', r.start, 6), { x: 6, w: 12 });
  assert.equal(barGeometry(null, '2026-10-30', r.start, 6), null);
  assert.deepEqual(monthTicks(r), ['2026-11-01', '2026-12-01', '2027-01-01']);
  assert.deepEqual(monthTicks({ start: dayNumber('2026-12-20'), end: dayNumber('2027-02-02') }), ['2027-01-01', '2027-02-01']);
});

test('an iteration without a start date shows the durations only', () => {
  assert.equal(iterationDated(view.iterations[0]), true);
  assert.equal(iterationDated(view.iterations[1]), false);
  assert.equal(timelineWeeks(null), '—');
  assert.equal(timelineWeeks(2.5), '2.5');
});

test('late is red, done green; the tooltip carries the dates, readiness and the note in one line', () => {
  assert.equal(statusColor('late'), '#ef4444');
  assert.equal(statusColor('done'), '#10b981');
  const tip = laneTip(view.iterations[0].lanes[1], t);
  assert.ok(tip.startsWith('timeline.status.late · '));
  assert.ok(tip.includes('timeline.readiness|{"n":50}') && tip.endsWith('Waiting for magnets'));
  assert.ok(!tip.includes('\n'));
});

test('the admin edits stay pure and the PUT body never carries the baseline', () => {
  const doc = { version: 1, revision: 4, production: null, baseline: { frozen_at: 'x', frozen_by: 'y' }, iterations: [] };
  let d = addIteration(doc);
  assert.equal(d.iterations[0].lanes.free_run.plan_weeks, 1);
  assert.equal(doc.iterations.length, 0);
  d = setIterationStart(d, 1, '2026-11-09');
  d = setLaneField(d, 1, 'motor', 'plan_weeks', '3,5');
  d = setLaneField(d, 1, 'motor', 'done_date', '');
  d = setLaneField(d, 1, 'motor', 'note', 'On track');
  d = setPropeller(d, 1, true);
  assert.equal(d.iterations[0].start_date, '2026-11-09');
  assert.deepEqual(d.iterations[0].lanes.motor, { plan_weeks: 3.5, start: null, end: null, done_date: null, note: 'On track' });
  assert.equal(d.iterations[0].lanes.propeller.plan_weeks, 1);
  assert.equal(setLaneField(d, 1, 'motor', 'plan_weeks', '').iterations[0].lanes.motor.plan_weeks, null);
  assert.equal(setPropeller(d, 1, false).iterations[0].lanes.propeller, undefined);
  assert.equal(removeLastIteration(d).iterations.length, 0);
  assert.deepEqual(Object.keys(putBody(d)), ['revision', 'production', 'iterations']);
});

test('EN and zh-CN have every timeline string', () => {
  const en = locale('en'); const zh = locale('zh-CN');
  const keys = (o, p = '') => Object.entries(o).flatMap(([k, v]) => (v && typeof v === 'object' ? keys(v, `${p}${k}.`) : [`${p}${k}`]));
  assert.deepEqual(keys(zh.timeline).sort(), keys(en.timeline).sort());
  for (const id of TIMELINE_LANES) assert.ok(en.timeline.lanes[id] && zh.timeline.lanes[id], id);
  for (const loc of [en, zh]) assert.ok(loc.tabs.timeline);
});

test('the customer view is read-only and the tab follows the server flag; edit controls are admin-only', () => {
  const view = src('../components/ProjectTimeline.tsx');
  assert.ok(!/helperText|caption/i.test(view.replace(/\/\*\*[\s\S]*?\*\//g, '')));
  assert.match(view, /isAdmin && admin && doc && <Editor/);
  assert.ok(!/supplier|price|part_name|bom/i.test(view.replace(/\/\*\*[\s\S]*?\*\//g, '')));
  const page = src('../components/DevelopmentPage.tsx');
  assert.match(page, /selected\.timeline_ready \|\| isAdmin \? \[\{ id: 'timeline'/);
});
