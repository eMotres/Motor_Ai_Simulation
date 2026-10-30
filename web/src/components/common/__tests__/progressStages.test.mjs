/**
 * The rules of the one-row stage strip, tested without a browser.
 *
 * `progressStages.ts` has no React and no `import.meta.env`, so unlike
 * `progressStrip.test.mjs` it is imported as shipped (Node strips the types).
 * What is pinned: a finished stage is full whatever its counts say, a pending or
 * skipped one is empty, a running one is done / planned clamped to 0…1; an
 * adaptive running stage prints a GROWING count (the warm-up's total rises until
 * the transient settles) and its ETA is a lower bound; a running stage with
 * nothing planned has no count and no fill (the strip animates it instead).
 *
 * The locale keys the row reads exist in EN and zh-CN, and every stage key the
 * backend can send has a name and a tooltip in both.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  KNOWN_STAGE_KEYS, etaIsLowerBound, formatStageSeconds, hasStages,
  isIndeterminate, isKnownStage, stageCount, stageFraction, stageUnit,
} from '../progressStages.ts';

const st = (o) => ({ key: 'final', name: 'final periods', state: 'running',
  done: 0, planned: 0, adaptive: false, unit: 'frames', elapsed_s: 0, ...o });

/* ── the bar ─────────────────────────────────────────────────────────────── */

test('a finished stage is full whatever its counts say; pending and skipped are empty', () => {
  assert.equal(stageFraction(st({ state: 'done', done: 12, planned: 12 })), 1);
  assert.equal(stageFraction(st({ state: 'done', done: 0, planned: 0 })), 1);
  assert.equal(stageFraction(st({ state: 'pending', done: 3, planned: 10 })), 0);
  assert.equal(stageFraction(st({ state: 'skipped', done: 0, planned: 40 })), 0);
});

test('a running stage fills by its own done / planned and never leaves 0…1', () => {
  assert.equal(stageFraction(st({ done: 25, planned: 100 })), 0.25);
  assert.equal(stageFraction(st({ done: 130, planned: 100 })), 1);
  assert.equal(stageFraction(st({ done: -4, planned: 100 })), 0);
  assert.equal(stageFraction(st({ done: 5, planned: 0 })), 0);   // nothing planned: no fill
});

test('a running stage with nothing planned is indeterminate, a stopped one is not', () => {
  assert.equal(isIndeterminate(st({ planned: 0 })), true);
  assert.equal(isIndeterminate(st({ planned: 8 })), false);
  assert.equal(isIndeterminate(st({ state: 'stopped', planned: 0 })), false);
  assert.equal(isIndeterminate(st({ state: 'pending', planned: 0 })), false);
});

/* ── the count ───────────────────────────────────────────────────────────── */

test('an adaptive running stage prints a growing count; once done it is exact', () => {
  const warm = st({ key: 'warm_up', adaptive: true, done: 19, planned: 20 });
  assert.deepEqual(stageCount(warm), { kind: 'growing', done: 19, planned: 20 });
  // the backend snaps planned to what the stage took when it ends
  assert.deepEqual(stageCount({ ...warm, state: 'done', done: 20, planned: 20 }),
    { kind: 'exact', done: 20, planned: 20 });
});

test('a fixed stage prints n / N; pending and skipped print nothing', () => {
  assert.deepEqual(stageCount(st({ done: 3, planned: 144 })),
    { kind: 'exact', done: 3, planned: 144 });
  assert.equal(stageCount(st({ state: 'pending', planned: 144 })).kind, 'none');
  assert.equal(stageCount(st({ state: 'skipped' })).kind, 'none');
});

test('the count never reads done > planned, and an unplanned running stage shows just its done', () => {
  assert.deepEqual(stageCount(st({ done: 9, planned: 8 })),
    { kind: 'exact', done: 9, planned: 9 });
  assert.deepEqual(stageCount(st({ done: 6, planned: 0, adaptive: true })),
    { kind: 'open', done: 6, planned: 0 });
  assert.equal(stageCount(st({ done: 0, planned: 0 })).kind, 'none');   // "0 frames" would be noise
});

test('coupled stages are counted in steps, everything else in frames', () => {
  assert.equal(stageUnit({ unit: 'steps' }), 'steps');
  assert.equal(stageUnit({ unit: 'frames' }), 'frames');
  assert.equal(stageUnit({}), 'frames');
});

/* ── the row and the ETA ─────────────────────────────────────────────────── */

test('the row shows only when the backend sent stages', () => {
  assert.equal(hasStages(undefined), false);
  assert.equal(hasStages(null), false);
  assert.equal(hasStages([]), false);
  assert.equal(hasStages([st({})]), true);
});

test('the ETA is a lower bound exactly while an adaptive stage is running', () => {
  const warm = st({ key: 'warm_up', adaptive: true, done: 5, planned: 10 });
  const fin = st({ key: 'final', state: 'pending', planned: 144 });
  assert.equal(etaIsLowerBound([warm, fin]), true);
  assert.equal(etaIsLowerBound([{ ...warm, state: 'done' }, { ...fin, state: 'running' }]), false);
  assert.equal(etaIsLowerBound([]), false);
  assert.equal(etaIsLowerBound(undefined), false);
});

test('seconds print as the strip prints them', () => {
  assert.equal(formatStageSeconds(4.24), '4.2 s');
  assert.equal(formatStageSeconds(83.4), '83 s');
  assert.equal(formatStageSeconds(125), '2 m 05 s');
  assert.equal(formatStageSeconds(-3), '0.0 s');
});

/* ── the words ───────────────────────────────────────────────────────────── */

const LOCALES = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'locales');
const common = (lng) => JSON.parse(readFileSync(join(LOCALES, lng, 'common.json'), 'utf8'));

for (const lng of ['en', 'zh-CN']) {
  test(`${lng}: every stage the backend can send has a name and a tooltip; every state a word`, () => {
    const p = common(lng).progress;
    for (const k of KNOWN_STAGE_KEYS) {
      assert.ok(isKnownStage(k));
      assert.ok(p.name[k], `${lng}: progress.name.${k}`);
      assert.ok(p.tip[k], `${lng}: progress.tip.${k}`);
    }
    for (const s of ['pending', 'running', 'done', 'skipped', 'stopped']) {
      assert.ok(p.state[s], `${lng}: progress.state.${s}`);
    }
    for (const k of ['label', 'elapsed', 'eta', 'adaptiveHint', 'count', 'countGrowing', 'countOpen']) {
      assert.ok(p[k], `${lng}: progress.${k}`);
    }
  });
}

test('the stage keys the backend sends are exactly the ones the row knows', () => {
  const py = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..', '..',
    'src', 'motor_ai_sim', 'progress_stages.py'), 'utf8');
  const block = py.match(/^_NAMES = \{([\s\S]*?)^\}/m)[1];
  const sent = [...block.matchAll(/"([a-z_]+)":/g)].map((m) => m[1]);
  assert.deepEqual(sent.sort(), [...KNOWN_STAGE_KEYS].sort());
});
