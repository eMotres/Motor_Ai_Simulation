// node --test: live activity of a project (owner 2026-10-27) - which variant is being computed on the server right now - and the
// "Latest" view picking the most recently linked variant.  Synthetic data only.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { newestLinked, latestAndRecommended } from '../motorGroups.ts';
import { ACTIVITY_POLL_MS, activityChip, activityForMotor, createActivityPoller, elapsedMinutes } from '../projectActivity.ts';

const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const en = JSON.parse(read('../../../locales/en/development.json'));
const zh = JSON.parse(read('../../../locales/zh-CN/development.json'));
const get = (obj, path) => path.split('.').reduce((o, k) => o?.[k], obj);
/** a stand-in for i18next's ICU interpolation: the messages here only use `{name}` placeholders */
const translator = (dict) => (key, o = {}) => String(get(dict, key)).replace(/\{(\w+)\}/g, (_, name) => String(o[name]));
const tEn = translator(en), tZh = translator(zh);

const NOW = Date.parse('2026-10-27T12:00:00Z');
const minutesAgo = (m) => new Date(NOW - m * 60_000).toISOString();
const row = (over = {}) => ({ die: 'DieA', config: 'v3', duty: 'rated', kind: 'coupled.run', label: 'Coupled thermal run', state: 'running', started_at: minutesAgo(12), progress: null, ...over });

// ── Latest = the most recently linked current variant ───────────────────────────────────────────

const m = (config, over = {}) => ({ die: 'DieA', config, ...over });

test('Latest picks the most recently linked current variant, not the last row of the list', () => {
  const rows = [m('old', { linked_at: '2026-10-20T10:00:00' }), m('newest', { linked_at: '2026-10-27T09:00:00' }), m('mid', { linked_at: '2026-10-25T10:00:00' })];
  assert.equal(newestLinked(rows).config, 'newest');
  assert.deepEqual(latestAndRecommended(rows).map((x) => x.config), ['newest']);
});

test('rows without a stamp keep their order and rank older than any stamped row; the recommended one still shows', () => {
  assert.equal(newestLinked([m('a'), m('b'), m('c')]).config, 'c');                         // legacy only: the last one, as before
  assert.equal(newestLinked([m('a'), m('stamped', { linked_at: '2026-10-01T00:00:00' }), m('b')]).config, 'stamped');
  assert.equal(newestLinked([m('stamped', { linked_at: '2026-10-01T00:00:00' }), m('legacy')]).config, 'stamped');
  assert.equal(newestLinked([m('x', { linked_at: '2026-10-01T00:00:00' }), m('y', { linked_at: '2026-10-01T00:00:00' })]).config, 'y');   // a tie: the later row
  assert.equal(newestLinked([m('x', { linked_at: 'not a date' }), m('y')]).config, 'y');
  const rec = { by: 'staff@example.test', at: '2026-10-26T10:00:00' };
  const rows = [m('best', { recommended: rec, linked_at: '2026-10-20T10:00:00' }), m('new', { linked_at: '2026-10-27T09:00:00' }), m('trial', { status: 'trial', linked_at: '2026-10-27T11:00:00' })];
  assert.deepEqual(latestAndRecommended(rows).map((x) => x.config), ['best', 'new']);       // a trial is never the latest current variant
});

// ── the chip ───────────────────────────────────────────────────────────────────────────────────

test('a running variant: "Computing on the server: <label> · <duty> · <N> min", with a tooltip line per run', () => {
  const chip = activityChip([row()], NOW, tEn);
  assert.equal(chip.state, 'running');
  assert.equal(chip.text, 'Computing on the server: Coupled thermal run · rated · 12 min');
  assert.deepEqual(chip.lines, ['Computing: Coupled thermal run · rated · 12 min']);
  const noDuty = activityChip([row({ duty: '' })], NOW, tEn);
  assert.equal(noDuty.text, 'Computing on the server: Coupled thermal run · 12 min');
  assert.equal(activityChip([row({ started_at: minutesAgo(0.4) })], NOW, tEn).text, 'Computing on the server: Coupled thermal run · rated · <1 min');
  assert.equal(activityChip([row({ started_at: null })], NOW, tEn).text, 'Computing on the server: Coupled thermal run · rated');   // start unknown: no time
  assert.equal(activityChip([row({ started_at: minutesAgo(-5) })], NOW, tEn).text, 'Computing on the server: Coupled thermal run · rated · <1 min');   // clock skew never goes negative
});

test('a queued variant: "Queued: <label>"; running beats queued; more runs add +N; progress shows in the tooltip', () => {
  const queued = row({ state: 'queued', started_at: null, label: 'Continuous rating (S1)', duty: '' });
  assert.equal(activityChip([queued], NOW, tEn).text, 'Queued: Continuous rating (S1)');
  assert.equal(activityChip([queued], NOW, tEn).state, 'queued');
  const both = activityChip([queued, row({ progress: 0.4 })], NOW, tEn);
  assert.equal(both.state, 'running');
  assert.equal(both.text, 'Computing on the server: Coupled thermal run · rated · 12 min +1');
  assert.deepEqual(both.lines, ['Queued: Continuous rating (S1)', 'Computing: Coupled thermal run · rated · 12 min · 40 %']);
  assert.equal(activityChip([], NOW, tEn), null);
});

test('the Chinese strings mirror the English ones and format the same way', () => {
  const chip = activityChip([row()], NOW, tZh);
  assert.equal(chip.text, '服务器正在计算：Coupled thermal run · rated · 12 分钟');
  assert.equal(activityChip([row({ state: 'queued', started_at: null })], NOW, tZh).text, '排队中：Coupled thermal run');
  for (const key of ['running', 'queued', 'lineRunning', 'lineQueued', 'minutes', 'underMinute', 'more']) {
    assert.ok(get(en, `activity.${key}`), `EN activity.${key}`);
    assert.ok(get(zh, `activity.${key}`), `zh-CN activity.${key}`);
  }
});

test('rows are matched to a variant by die + configuration, case-insensitively, canonical die or alias', () => {
  const rows = [row(), row({ config: 'v4' }), row({ die: 'Other' }), row({ die: 'diea', config: 'V3', kind: 'transient' })];
  assert.equal(activityForMotor(rows, { die: 'DieA', config: 'v3' }).length, 2);
  assert.equal(activityForMotor(rows, { die: 'Alias', canonical_die: 'DieA', config: 'v4' }).length, 1);
  assert.equal(activityForMotor(rows, { die: 'DieA', config: 'none' }).length, 0);
  assert.equal(elapsedMinutes('garbage', NOW), null);
  assert.equal(elapsedMinutes(minutesAgo(61), NOW), 61);
});

// ── the poller: one request per project every 30 s while visible ───────────────────────────────

function harness({ visible = true, fail = false } = {}) {
  const h = { visible, fail, fetches: 0, delivered: [], timers: [], listeners: [], cleared: 0 };
  h.poller = createActivityPoller({
    fetchRows: async () => { h.fetches += 1; if (h.fail) throw new Error('offline'); return [row({ config: `v${h.fetches}` })]; },
    onRows: (rows) => h.delivered.push(rows),
    isVisible: () => h.visible,
    onVisibility: (fn) => { h.listeners.push(fn); return () => { h.listeners = h.listeners.filter((x) => x !== fn); }; },
    setTimer: (fn, ms) => { const t = { fn, ms, live: true }; h.timers.push(t); return t; },
    clearTimer: (t) => { t.live = false; h.cleared += 1; },
  });
  h.pending = () => h.timers.filter((t) => t.live);
  h.fire = async () => { const t = h.pending()[0]; t.live = false; t.fn(); await new Promise((r) => setImmediate(r)); };
  h.settle = () => new Promise((r) => setImmediate(r));
  return h;
}

test('polling: one request at once, then one every 30 s', async () => {
  const h = harness();
  h.poller.start();
  await h.settle();
  assert.equal(h.fetches, 1);
  assert.deepEqual(h.pending().map((t) => t.ms), [ACTIVITY_POLL_MS]);
  assert.equal(ACTIVITY_POLL_MS, 30_000);
  await h.fire();
  assert.equal(h.fetches, 2);
  assert.equal(h.delivered.length, 2);
  assert.equal(h.pending().length, 1);                                                      // exactly one timer, never a pile
  h.poller.start();                                                                         // a second start changes nothing
  await h.settle();
  assert.equal(h.fetches, 2);
});

test('polling stops while the page is hidden and refreshes at once when it is visible again', async () => {
  const h = harness();
  h.poller.start();
  await h.settle();
  h.visible = false;
  h.listeners.forEach((fn) => fn());
  assert.equal(h.pending().length, 0);                                                      // the timer is cancelled
  assert.equal(h.fetches, 1);
  h.visible = true;
  h.listeners.forEach((fn) => fn());
  await h.settle();
  assert.equal(h.fetches, 2);                                                               // refreshed immediately
  assert.equal(h.pending().length, 1);
  const hidden = harness({ visible: false });
  hidden.poller.start();
  await hidden.settle();
  assert.equal(hidden.fetches, 0);                                                          // a page opened in a background tab asks nothing
});

test('stop() ends everything; a failed request keeps the last rows and is retried at the next tick', async () => {
  const h = harness();
  h.poller.start();
  await h.settle();
  h.fail = true;
  await h.fire();
  assert.equal(h.delivered.length, 1);                                                      // nothing delivered on a failure: the old rows stay
  assert.equal(h.pending().length, 1);                                                      // and the next tick is scheduled
  h.poller.stop();
  assert.equal(h.pending().length, 0);
  assert.equal(h.listeners.length, 0);
  assert.equal(h.fetches, 2);
});

// ── the page ───────────────────────────────────────────────────────────────────────────────────

test('the page polls once per project on the Motors tab and shows the chip on each variant header and on the toggle row', () => {
  const page = read('../components/DevelopmentPage.tsx');
  assert.match(page, /const activityRows = useProjectActivity\(view === 'motors' \? selected\?\.id : null\)/);
  assert.match(page, /\{live && <ActivityChip rows=\{activityRows\} motor=\{live\} t=\{tr\} \/>\}/);
  assert.match(page, /<ActivityChip rows=\{activityRows\} t=\{tr\} \/>/);
  assert.equal((page.match(/useProjectActivity\(/g) ?? []).length, 1);                      // one hook call: one request per project, not per row
  const api = read('../api.ts');
  assert.match(api, /activity: async \(id: string\)/);
  assert.match(api, /\$\{p\(id\)\}\/activity/);
  const hook = read('../useProjectActivity.ts');
  assert.match(hook, /visibilitychange/);
  assert.match(hook, /\[projectId\]/);
});
