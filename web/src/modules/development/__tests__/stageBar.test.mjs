// node --test: the five-stage bar of a project variant and the project line (owner 2026-10-27).  Synthetic data only.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  STAGE_ORDER, parseActivity, projectPercent, readSeen, segmentFill, stageAria, stageTooltip, stagesForMotor, summaryLine, summaryTooltip,
  variantEndText, variantPercent, writeSeen,
} from '../stageBar.ts';

const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const en = JSON.parse(read('../../../locales/en/development.json'));
const zh = JSON.parse(read('../../../locales/zh-CN/development.json'));
const get = (obj, path) => path.split('.').reduce((o, k) => o?.[k], obj);
const translator = (dict) => (key, o = {}) => {
  const v = get(dict, key);
  assert.ok(typeof v === 'string', `missing string ${key}`);
  return v.replace(/\{(\w+)\}/g, (_, name) => String(o[name]));
};
const tEn = translator(en), tZh = translator(zh);

const NOW = Date.parse('2026-10-27T12:00:00Z');
const at = (min) => new Date(NOW + min * 60_000).toISOString();
const UTC = {
  time: (ms) => new Date(ms).toISOString().slice(11, 16),
  day: (ms) => new Date(ms).toISOString().slice(0, 10),
  dayTime: (ms) => new Date(ms).toISOString().slice(5, 16).replace('T', ' '),
};
const eta = (finishMin, startMin = null, confidence = 'medium') => ({ expected_start: startMin === null ? null : at(startMin), expected_finish: finishMin === null ? null : at(finishMin), confidence, basis: 'queue' });

const stage = (id, state, over = {}) => ({ id, state, done: 0, total: 4, fraction: 0, position: null, ahead: null, eta: null, finished_at: null, detail: [], ...over });
const variant = (over = {}) => ({
  die: 'DieA', config: 'v3', percent: 30, stage_index: 2, stage_count: 5, complete: false, runs_running: 0, runs_queued: 0, eta: null, last_finished_at: null,
  stages: [stage('em', 'done', { done: 4, fraction: 1 }), stage('thermal', 'computing', { done: 1, fraction: 0.4, eta: eta(65, null, 'high') }),
    stage('mechanics', 'queued', { position: 3, ahead: 2, eta: eta(125, 80) }), stage('end3d', 'not_started', { total: 1 }), stage('report', 'not_started', { total: 1 })],
  ...over,
});

// ── segment fill / percent ─────────────────────────────────────────────────────────────────────

test('a segment is full when done, filled to its share while computing or queued, empty when not started, outlined when failed', () => {
  assert.deepEqual(segmentFill({ state: 'done', fraction: 1 }), { pct: 100, hatch: false, outline: false, active: false });
  assert.deepEqual(segmentFill({ state: 'computing', fraction: 0.4 }), { pct: 40, hatch: false, outline: false, active: true });
  assert.deepEqual(segmentFill({ state: 'queued', fraction: 0.5 }), { pct: 50, hatch: true, outline: false, active: false });
  assert.deepEqual(segmentFill({ state: 'queued', fraction: 0 }), { pct: 0, hatch: true, outline: false, active: false });
  assert.deepEqual(segmentFill({ state: 'not_started', fraction: 0 }), { pct: 0, hatch: false, outline: false, active: false });
  assert.deepEqual(segmentFill({ state: 'partial', fraction: 0.75 }), { pct: 75, hatch: false, outline: false, active: false });
  assert.deepEqual(segmentFill({ state: 'failed', fraction: 0.25 }), { pct: 25, hatch: false, outline: true, active: false });
  assert.equal(segmentFill({ state: 'computing', fraction: 7 }).pct, 100);                  // clamped
  assert.equal(segmentFill({ state: 'computing', fraction: -1 }).pct, 0);
  assert.equal(segmentFill({ state: 'computing', fraction: NaN }).pct, 0);
  assert.equal(segmentFill({ state: 'not_started', fraction: 0.9 }).pct, 0);                // an empty stage never shows a fill
});

test('overall percent: the server number, else the mean of the segments; the project is the mean of its variants', () => {
  assert.equal(variantPercent(variant()), 30);
  assert.equal(variantPercent({ percent: undefined, stages: variant().stages }), Math.round(100 * (1 + 0.4 + 0 + 0 + 0) / 5));
  assert.equal(variantPercent({ percent: 140, stages: [] }), 100);
  assert.equal(variantPercent({ percent: undefined, stages: [] }), 0);
  assert.equal(projectPercent([variant({ percent: 20 }), variant({ percent: 60 })]), 40);
  assert.equal(projectPercent([]), 0);
  assert.deepEqual(STAGE_ORDER, ['em', 'thermal', 'mechanics', 'end3d', 'report']);
});

test('the stages of a variant are found by die + configuration, case-insensitively, canonical die or alias', () => {
  const list = [variant(), variant({ config: 'v4' })];
  assert.equal(stagesForMotor(list, { die: 'diea', config: 'V3' }), list[0]);
  assert.equal(stagesForMotor(list, { die: 'Alias', canonical_die: 'DieA', config: 'v4' }), list[1]);
  assert.equal(stagesForMotor(list, { die: 'DieA', config: 'none' }), null);
});

// ── tooltips ───────────────────────────────────────────────────────────────────────────────────

test('tooltip of a computing segment: state and percent, counts, estimate with its confidence', () => {
  const lines = stageTooltip(variant().stages[1], NOW, tEn, UTC);
  assert.deepEqual(lines, ['Thermal: computing · 40 %', '1 of 4 points', 'results ~13:05 · good estimate']);
});

test('tooltip of a queued segment: the place in the queue, runs ahead, start and finish', () => {
  const lines = stageTooltip(variant().stages[2], NOW, tEn, UTC);
  assert.deepEqual(lines, ['Mechanics: queued', '0 of 4 points', 'queue place 3 · 2 ahead', 'starts ~13:20', 'results ~14:05 · fair estimate']);
  const next = stageTooltip(stage('mechanics', 'queued', { position: 1, ahead: 0, eta: eta(null, 5, 'low') }), NOW, tEn, UTC);
  assert.ok(next.includes('queue place 1 · next in line') && next.includes('starts ~12:05') && next.includes('estimate after the first runs'));
});

test('tooltip of a done segment carries its date; per-duty lines appear for several duties; the report says who checked it', () => {
  const done = stage('em', 'done', { done: 2, total: 2, fraction: 1, finished_at: at(-120), detail: [{ duty: 'rated', state: 'done', progress: null }, { duty: 'peak', state: 'computing', progress: 0.5 }] });
  const lines = stageTooltip(done, NOW, tEn, UTC);
  assert.deepEqual(lines, ['EM points: done', '2 of 2 points', 'done 10-27 10:00', 'rated: done', 'peak: computing 50 %']);
  assert.deepEqual(stageTooltip(stage('report', 'done', { total: 1, done: 1, fraction: 1 }), NOW, tEn, UTC), ['Report: done', 'checked by our engineers']);
  assert.deepEqual(stageTooltip(stage('report', 'not_started', { total: 1, note: 'awaiting_review' }), NOW, tEn, UTC), ['Report: not started', "waiting for our engineers' check"]);
  assert.deepEqual(stageTooltip(stage('end3d', 'failed', { total: 1 }), NOW, tEn, UTC), ['3-D end effect: failed, we are looking into it']);
});

test('the aria label of a segment names stage, state and percent', () => {
  assert.equal(stageAria(variant().stages[1], tEn), 'Thermal: computing, 40 %');
  assert.equal(stageAria(variant().stages[0], tEn), 'EM points: done, 100 %');
});

// ── right end of the bar and the project line ──────────────────────────────────────────────────

test('the variant bar ends with the percent and the expected results time; complete variants say so', () => {
  const live = variant({ runs_running: 1, runs_queued: 1, eta: eta(125, 80) });
  assert.equal(variantEndText(live, NOW, tEn, UTC), '30 % · results ~14:05');
  assert.equal(variantEndText(variant({ runs_running: 1, eta: eta(null) }), NOW, tEn, UTC), '30 % · estimate after the first runs');
  assert.equal(variantEndText(variant(), NOW, tEn, UTC), '30 %');                                 // nothing runs: no estimate to give
  assert.equal(variantEndText(variant({ complete: true }), NOW, tEn, UTC), 'Complete');
});

const summary = (over = {}) => ({ state: 'computing', runs_running: 2, runs_queued: 0, expected_all_done: at(185), confidence: 'medium', basis: 'history', last_finished_at: null, ...over });

test('the project line: computing, queued, ready, idle', () => {
  const rows = [];
  const two = [variant({ runs_running: 1 }), variant({ config: 'v4', runs_running: 1, stage_index: 3 })];
  assert.equal(summaryLine(summary(), rows, two, NOW, tEn, UTC), 'Simulation: computing 2 runs · results expected ~15:05');
  assert.equal(summaryLine(summary({ runs_running: 1 }), rows, [variant({ runs_running: 1 })], NOW, tEn, UTC), 'Simulation: computing 1 run · stage 2 of 5 · results expected ~15:05');
  assert.equal(summaryLine(summary({ expected_all_done: null }), rows, two, NOW, tEn, UTC), 'Simulation: computing 2 runs · estimate after the first runs');
  const queuedRows = [{ state: 'queued', position: 4, ahead: 3, eta: eta(185, 90) }, { state: 'queued', position: 2, ahead: 1, eta: eta(60, 30) }];
  assert.equal(summaryLine(summary({ state: 'queued', runs_running: 0, runs_queued: 2 }), queuedRows, two, NOW, tEn, UTC),
    'Simulation: queued 2 runs · 1 ahead · starts ~12:30 · results expected ~15:05');
  assert.equal(summaryLine(summary({ state: 'results_ready', runs_running: 0 }), rows, [], NOW, tEn, UTC), 'Results ready');
  assert.equal(summaryLine(summary({ state: 'idle', runs_running: 0 }), rows, [], NOW, tEn, UTC), null);
  assert.equal(summaryLine(null, rows, [], NOW, tEn, UTC), null);                                // an older server: no line at all
});

test('the project line tooltip: confidence of the estimate and the server load as counts only', () => {
  assert.deepEqual(summaryTooltip(summary({ server: { running: 2, queued: 7 } }), tEn), ['fair estimate', 'On the server now: 2 running, 7 waiting']);
  assert.deepEqual(summaryTooltip(summary({ expected_all_done: null }), tEn), []);
  assert.deepEqual(summaryTooltip(null, tEn), []);
});

test('Chinese strings exist for every key the bar uses and format the same way', () => {
  assert.equal(summaryLine(summary({ runs_running: 1 }), [], [variant({ runs_running: 1 })], NOW, tZh, UTC), '仿真：正在计算 1 项计算 · 第 2 / 5 阶段 · 预计出结果 ~15:05');
  assert.equal(summaryLine(summary({ state: 'results_ready' }), [], [], NOW, tZh, UTC), '结果已就绪');
  const keys = ['label', 'names.em', 'names.thermal', 'names.mechanics', 'names.end3d', 'names.report', 'state.not_started', 'state.queued', 'state.computing',
    'state.partial', 'state.done', 'state.failed', 'points', 'reviewed', 'awaitingReview', 'doneOn', 'complete', 'stageOf'];
  for (const k of keys) { assert.ok(get(en, `stages.${k}`), `EN stages.${k}`); assert.ok(get(zh, `stages.${k}`), `zh stages.${k}`); }
  for (const k of ['computing', 'queued', 'runOne', 'runMany', 'expected', 'ready', 'server']) { assert.ok(get(en, `statusLine.${k}`), `EN statusLine.${k}`); assert.ok(get(zh, `statusLine.${k}`), `zh statusLine.${k}`); }
  assert.equal(stageTooltip(variant().stages[1], NOW, tZh, UTC)[0], '热: 计算中 · 40 %');
});

// ── parsing and the last-visit memory ──────────────────────────────────────────────────────────

test('an older or broken answer degrades to "no stages, no line"', () => {
  assert.deepEqual(parseActivity({ activity: [{ die: 'A' }] }), { rows: [{ die: 'A' }], summary: null, stages: [] });
  assert.deepEqual(parseActivity(null), { rows: [], summary: null, stages: [] });
  assert.deepEqual(parseActivity({ stages: [null, { die: 'A' }, { die: 'B', stages: [] }] }).stages.map((v) => v.die), ['B']);
});

test('the last visit is remembered per project; a store that throws never breaks the page', () => {
  const mem = new Map();
  const store = { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, v) };
  assert.equal(readSeen(store, 'alpha'), null);
  writeSeen(store, 'alpha', NOW);
  assert.equal(readSeen(store, 'alpha'), new Date(NOW).toISOString());
  assert.equal(readSeen(store, 'beta'), null);
  mem.set('activity.seen.gamma', 'garbage');
  assert.equal(readSeen(store, 'gamma'), null);
  const broken = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } };
  assert.equal(readSeen(broken, 'alpha'), null);
  assert.doesNotThrow(() => writeSeen(broken, 'alpha', NOW));
  assert.equal(readSeen(null, 'alpha'), null);
});

// ── the components and the page ────────────────────────────────────────────────────────────────

test('the bar is accessible and quiet: progressbar roles with values, brand fill on a grey track, labels that never wrap', () => {
  const src = read('../StageBar.tsx');
  assert.match(src, /role="progressbar"/);
  assert.match(src, /aria-valuenow=\{fill\.pct\}/);
  assert.match(src, /aria-label=\{stageAria\(stage, t\)\}/);
  assert.match(src, /role="group" aria-label=\{t\('stages\.label'\)\}/);
  assert.match(src, /bgcolor: 'var\(--brand\)'/);
  assert.match(src, /prefers-reduced-motion/);
  assert.match(src, /noWrap/);
  assert.equal((src.match(/#[0-9a-fA-F]{6}\b/g) ?? []).join(','), '#ef4444');                // the only literal colour: the failed outline
  assert.doesNotMatch(src, /<Chip/);                                                         // no chip walls
});

test('the page shows the project line under the tabs on both views and one bar under each variant header', () => {
  const page = read('../components/DevelopmentPage.tsx');
  assert.match(page, /\(view === 'specification' \|\| view === 'motors'\) && selected && <ProjectStatusBar data=\{activity\} t=\{tr\} \/>/);
  assert.match(page, /\{live && <VariantStageBar data=\{activity\} motor=\{live\} t=\{tr\} \/>\}/);
  assert.equal((page.match(/useProjectActivity\(/g) ?? []).length, 1);
  const hook = read('../useProjectActivity.ts');
  assert.match(hook, /readSeen\(storage\(\), projectId\)/);
  assert.match(hook, /writeSeen\(storage\(\), projectId, Date\.now\(\)\)/);
});
