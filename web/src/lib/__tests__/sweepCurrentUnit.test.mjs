// node --test — the pure rules of lib/sweepCurrentUnit.ts, restated here
// verbatim (the repo's convention for node tests: `node --test` cannot load
// the TS modules — see sweepResumeNotice.test.mjs for the same pattern).
//
// Bug this exists for (owner, 2026-09-19): the Sweep study's "Picked:" line
// printed the raw STORED current — Arms — with no unit ("I = 49.4975 A"),
// right next to the range card above it reading "70 A peak" for the exact
// same operating point (49.4975 * sqrt(2) = 70). One number, two readings,
// nothing on screen said which was which.
import test from 'node:test';
import assert from 'node:assert/strict';

function currentDisplay(rmsA, unit) {
  const kU = unit === 'peak' ? Math.SQRT2 : 1;
  const v = (Number.isFinite(rmsA) ? rmsA : 0) * kU;
  return { value: v, unit: unit === 'peak' ? 'A peak' : 'Arms' };
}

function formatCurrent(rmsA, unit) {
  const { value, unit: label } = currentDisplay(rmsA, unit);
  return `${value.toFixed(2)} ${label}`;
}

test('49.4975 Arms displays as 70.00 A peak — the exact owner report', () => {
  const d = currentDisplay(49.4975, 'peak');
  assert.ok(Math.abs(d.value - 70) < 0.01, `expected ~70, got ${d.value}`);
  assert.equal(d.unit, 'A peak');
  assert.equal(formatCurrent(49.4975, 'peak'), '70.00 A peak');
});

test('the same stored value in Arms mode is unconverted and labelled Arms', () => {
  const d = currentDisplay(49.4975, 'arms');
  assert.ok(Math.abs(d.value - 49.4975) < 1e-9);
  assert.equal(d.unit, 'Arms');
  assert.equal(formatCurrent(49.4975, 'arms'), '49.50 Arms');
});

test('peak and Arms of the SAME stored current always relate by sqrt(2)', () => {
  const rms = 85;
  const peak = currentDisplay(rms, 'peak').value;
  const arms = currentDisplay(rms, 'arms').value;
  assert.ok(Math.abs(peak / arms - Math.SQRT2) < 1e-9);
});

test('round trip: card writes min/max as stored/kU, display multiplies back by kU', () => {
  // Mirrors SweepConfigPanel's SweepVarCard: onChange stores n/kU, the "now"
  // line displays stored*kU — the two must be inverse of one another or a
  // typed peak value drifts on every render.
  const typedPeak = 70;
  const kU = Math.SQRT2;
  const stored = typedPeak / kU;             // what the card PUTs
  const shownBack = currentDisplay(stored, 'peak').value;   // what it re-shows
  assert.ok(Math.abs(shownBack - typedPeak) < 1e-9);
});

test('non-finite input never produces NaN in the label', () => {
  for (const bad of [NaN, undefined, null, Infinity]) {
    const text = formatCurrent(bad, 'peak');
    assert.ok(!text.includes('NaN'), `formatCurrent(${bad}) => "${text}"`);
  }
});
