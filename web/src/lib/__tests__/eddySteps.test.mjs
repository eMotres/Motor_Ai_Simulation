// node --test — the pure rules of lib/eddySteps.ts, restated verbatim (the
// repo's convention for node tests: `node --test` cannot load the TS modules,
// so the pure functions under test are re-stated here and kept in sync).
//
// Owner 2026-09-26: eddy runs default to 72 steps per electrical period, the
// picker stays editable everywhere, and a count the user picked is always the
// count that is asked for.  Where the slip-ring rule moves the count, one line
// says so.
import test from 'node:test';
import assert from 'node:assert/strict';

/* ── copied verbatim from lib/eddySteps.ts ───────────────────────────────── */
const EDDY_DEFAULT_STEPS = 72;
const LEGACY_DEFAULT_STEPS = [36, 40];
const isStepsSource = (v) => v === 'eddy_default' || v === 'user';

function snapStepsToRing(v, ring) {
  if (!(ring > 0) || !(v > 0)) return v;
  if (v > ring) return v;
  if (ring % v === 0) return v;
  let best = ring;
  for (let d = 1; d <= ring; d++)
    if (ring % d === 0 && (Math.abs(d - v) < Math.abs(best - v)
        || (Math.abs(d - v) === Math.abs(best - v) && d > best))) best = d;
  return best;
}

function adoptSteps(stored, source) {
  const n = typeof stored === 'number' && Number.isFinite(stored) && stored > 0
    ? Math.round(stored) : null;
  if (source === 'user' && n !== null)
    return { steps: n, source: 'user', migratedFrom: null };
  if (source === 'eddy_default')
    return { steps: n ?? EDDY_DEFAULT_STEPS, source: 'eddy_default', migratedFrom: null };
  if (n === null)
    return { steps: EDDY_DEFAULT_STEPS, source: 'eddy_default', migratedFrom: null };
  if (LEGACY_DEFAULT_STEPS.includes(n) && n !== EDDY_DEFAULT_STEPS)
    return { steps: EDDY_DEFAULT_STEPS, source: 'eddy_default', migratedFrom: n };
  return { steps: n, source: 'user', migratedFrom: null };
}

function stepsNote(requested, ran, ring, migratedFrom = null) {
  if (migratedFrom !== null) {
    return `Steps ${migratedFrom} → ${ran}: eddy runs now default to ${EDDY_DEFAULT_STEPS}`
      + (ran !== EDDY_DEFAULT_STEPS
        ? ` (${ran} = nearest divisor of the ${ring}-node slip ring)` : '')
      + '.';
  }
  if (requested === ran) return null;
  return `Steps ${requested} → ${ran}: the count must divide this machine's `
    + `${ring}-node slip ring.`;
}
/* ─────────────────────────────────────────────────────────────────────────── */

test('the eddy default is 72', () => {
  assert.equal(EDDY_DEFAULT_STEPS, 72);
});

test('a fresh profile (nothing stored) gets the eddy default', () => {
  assert.deepEqual(adoptSteps(null, undefined),
    { steps: 72, source: 'eddy_default', migratedFrom: null });
});

test('an old factory default with no record moves to 72, and says from what', () => {
  for (const old of [36, 40]) {
    const a = adoptSteps(old, undefined);
    assert.deepEqual(a, { steps: 72, source: 'eddy_default', migratedFrom: old });
  }
});

test('a count the user picked is always kept — recorded or not', () => {
  for (const n of [12, 24, 36, 40, 48, 64, 72, 96, 120, 500]) {
    assert.deepEqual(adoptSteps(n, 'user'), { steps: n, source: 'user', migratedFrom: null });
  }
  // no record, not an old default → a choice someone made
  for (const n of [12, 24, 48, 60, 64, 72, 96, 144]) {
    assert.deepEqual(adoptSteps(n, undefined), { steps: n, source: 'user', migratedFrom: null });
  }
});

test('a recorded eddy default is adopted as is', () => {
  assert.deepEqual(adoptSteps(72, 'eddy_default'),
    { steps: 72, source: 'eddy_default', migratedFrom: null });
});

test('the source guard accepts exactly the two sources', () => {
  assert.ok(isStepsSource('user'));
  assert.ok(isStepsSource('eddy_default'));
  for (const v of [null, undefined, '', 'auto', 72]) assert.ok(!isStepsSource(v));
});

test('snap: a divisor is kept, a non-divisor goes to the nearest (ties up), above the ring kept', () => {
  assert.equal(snapStepsToRing(72, 144), 72);
  assert.equal(snapStepsToRing(72, 288), 72);
  assert.equal(snapStepsToRing(72, 192), 64);   // 14-pole, gap_layers 2
  assert.equal(snapStepsToRing(72, 240), 80);   // 12-pole
  assert.equal(snapStepsToRing(36, 192), 32);
  assert.equal(snapStepsToRing(500, 192), 500);
});

test('one line when the ring moves the count, none when it does not', () => {
  assert.equal(stepsNote(72, 72, 144), null);
  assert.equal(stepsNote(72, 64, 192),
    "Steps 72 → 64: the count must divide this machine's 192-node slip ring.");
  assert.equal(stepsNote(40, 72, 144, 40), 'Steps 40 → 72: eddy runs now default to 72.');
  assert.equal(stepsNote(72, 64, 192, 40),
    'Steps 40 → 64: eddy runs now default to 72 (64 = nearest divisor of the 192-node slip ring).');
  for (const l of [stepsNote(72, 64, 192), stepsNote(72, 64, 192, 40)])
    assert.ok(!l.includes('\n'));
});
