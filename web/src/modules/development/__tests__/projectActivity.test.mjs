// node --test: live activity of a project (owner 2026-10-27) - which variant is being computed on the server right now - and the
// "Latest" view picking the most recently linked variant.  Synthetic data only.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { newestLinked, latestAndRecommended } from '../motorGroups.ts';
import { ACTIVITY_POLL_MS, activityChip, activityForMotor, createActivityPoller, elapsedMinutes, etaClock } from '../projectActivity.ts';

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

/** a fixed clock (UTC) so the tests do not depend on the machine time zone or language */
const UTC = {
  time: (ms) => new Date(ms).toISOString().slice(11, 16),
  day: (ms) => new Date(ms).toISOString().slice(0, 10),
  dayTime: (ms) => new Date(ms).toISOString().slice(5, 16).replace('T', ' '),
};
const at = (minutes) => new Date(NOW + minutes * 60_000).toISOString();
const eta = (finishMin, startMin = null, confidence = 'medium') => ({ expected_start: startMin === null ? null : at(startMin), expected_finish: finishMin === null ? null : at(finishMin), confidence, basis: 'queue' });

test('a running variant: "Computing on the server: <label> · <duty> · <N> min · results ~15:05", with a tooltip line per run', () => {
  const chip = activityChip([row({ eta: eta(65) })], NOW, tEn, UTC);
  assert.equal(chip.state, 'running');
  assert.equal(chip.text, 'Computing on the server: Coupled thermal run · rated · 12 min · results ~13:05');
  assert.deepEqual(chip.lines, ['Computing: Coupled thermal run · rated · 12 min · results ~13:05 · fair estimate']);
  const noDuty = activityChip([row({ duty: '', eta: eta(65) })], NOW, tEn, UTC);
  assert.equal(noDuty.text, 'Computing on the server: Coupled thermal run · 12 min · results ~13:05');
  assert.equal(activityChip([row({ started_at: minutesAgo(0.4), eta: eta(65) })], NOW, tEn, UTC).text, 'Computing on the server: Coupled thermal run · rated · <1 min · results ~13:05');
  assert.equal(activityChip([row({ started_at: null, eta: eta(65) })], NOW, tEn, UTC).text, 'Computing on the server: Coupled thermal run · rated · results ~13:05');   // start unknown: no elapsed time
  assert.equal(activityChip([row({ started_at: minutesAgo(-5), eta: eta(65) })], NOW, tEn, UTC).text, 'Computing on the server: Coupled thermal run · rated · <1 min · results ~13:05');   // clock skew never goes negative
});

test('no history yet: no number, the page says the estimate comes after the first runs', () => {
  assert.equal(activityChip([row()], NOW, tEn, UTC).text, 'Computing on the server: Coupled thermal run · rated · 12 min · estimate after the first runs');
  assert.equal(activityChip([row({ eta: eta(null) })], NOW, tEn, UTC).text, 'Computing on the server: Coupled thermal run · rated · 12 min · estimate after the first runs');
  const q = activityChip([row({ state: 'queued', started_at: null, position: 2, ahead: 1, eta: { expected_start: at(10), expected_finish: null, confidence: 'low', basis: 'no_history' } })], NOW, tEn, UTC);
  assert.equal(q.text, 'Queued: Coupled thermal run · 1 ahead · starts ~12:10 · estimate after the first runs');
});

test('a queued variant: "Queued: <label> · 3 ahead · starts ~14:20 · results ~15:05"; next in line when none is ahead', () => {
  const queued = row({ state: 'queued', started_at: null, label: 'Continuous rating (S1)', duty: '', position: 4, ahead: 3, eta: eta(125, 80) });
  const chip = activityChip([queued], NOW, tEn, UTC);
  assert.equal(chip.text, 'Queued: Continuous rating (S1) · 3 ahead · starts ~13:20 · results ~14:05');
  assert.equal(chip.state, 'queued');
  assert.deepEqual(chip.lines, ['Queued: Continuous rating (S1) · queue place 4 · 3 ahead · starts ~13:20 · results ~14:05 · fair estimate']);
  assert.equal(activityChip([{ ...queued, position: 1, ahead: 0 }], NOW, tEn, UTC).text, 'Queued: Continuous rating (S1) · next in line · starts ~13:20 · results ~14:05');
  assert.equal(activityChip([{ ...queued, position: undefined, ahead: undefined }], NOW, tEn, UTC).text, 'Queued: Continuous rating (S1) · starts ~13:20 · results ~14:05');   // an older server: no place, no word about it
});

test('running beats queued; more runs add +N; the nearest queued run speaks for the queue; progress shows in the tooltip', () => {
  const far = row({ state: 'queued', started_at: null, label: 'Far', duty: '', position: 5, ahead: 4, eta: eta(300, 200) });
  const near = row({ state: 'queued', started_at: null, label: 'Near', duty: '', position: 2, ahead: 1, eta: eta(100, 40) });
  assert.match(activityChip([far, near], NOW, tEn, UTC).text, /^Queued: Near · 1 ahead · starts ~12:40 · results ~13:40 \+1$/);
  const both = activityChip([near, row({ progress: 0.4, eta: eta(65) })], NOW, tEn, UTC);
  assert.equal(both.state, 'running');
  assert.equal(both.text, 'Computing on the server: Coupled thermal run · rated · 12 min · results ~13:05 +1');
  assert.ok(both.lines[1].startsWith('Computing: Coupled thermal run · rated · 12 min · 40 % · results ~13:05'));
  assert.equal(activityChip([], NOW, tEn, UTC), null);
});

test('a time on another day carries its date; the tilde is never dropped', () => {
  assert.equal(etaClock(at(65), NOW, UTC), '~13:05');
  assert.equal(etaClock(at(60 * 20), NOW, UTC), '~10-28 08:00');
  assert.equal(etaClock(null, NOW, UTC), null);
  assert.equal(etaClock('garbage', NOW, UTC), null);
});

test('the Chinese strings mirror the English ones and format the same way', () => {
  const chip = activityChip([row({ eta: eta(65) })], NOW, tZh, UTC);
  assert.equal(chip.text, '服务器正在计算：Coupled thermal run · rated · 12 分钟 · 预计出结果 ~13:05');
  assert.equal(activityChip([row({ state: 'queued', started_at: null, position: 3, ahead: 2, eta: eta(65, 20) })], NOW, tZh, UTC).text,
    '排队中：Coupled thermal run · 前面还有 2 个 · 预计开始 ~12:20 · 预计出结果 ~13:05');
  for (const key of ['running', 'queued', 'lineRunning', 'lineQueued', 'minutes', 'underMinute', 'more', 'ahead', 'nextInLine', 'position', 'starts', 'results', 'noEstimate', 'confidence.low', 'confidence.medium', 'confidence.high']) {
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
  assert.match(page, /const activity = useProjectActivity\(view === 'motors' \|\| view === 'specification' \? selected\?\.id : null\)/);
  assert.match(page, /const activityRows = activity\.rows;/);
  assert.match(page, /\{live && <ActivityChip rows=\{activityRows\} motor=\{live\} t=\{tr\} \/>\}/);
  assert.match(page, /<ActivityChip rows=\{activityRows\} t=\{tr\} \/>/);
  assert.equal((page.match(/useProjectActivity\(/g) ?? []).length, 1);                      // one hook call: one request per project, not per row
  const api = read('../api.ts');
  assert.match(api, /activity: async \(id: string, since\?: string \| null\)/);
  assert.match(api, /\$\{p\(id\)\}\/activity/);
  const hook = read('../useProjectActivity.ts');
  assert.match(hook, /visibilitychange/);
  assert.match(hook, /\[projectId\]/);
});
