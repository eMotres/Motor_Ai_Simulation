// node --test — the pure turns rule of lib/motorScaling.ts `turnsFactor`,
// copied verbatim (the repo's convention for node tests: `node --test` cannot
// load the TS modules, so the pure function under test is re-stated here and
// kept in sync).
//
// What it pins (user 2026-09-08): a `wire_split` row's strips are consecutive
// SERIES turns, so a machine split N ways has N× the turns of the same rows
// unsplit.  Configure matches a reference passport by CROSS-SECTION, not by
// build, so the passport's split and the loaded machine's are two different
// numbers and both have to be in the ratio.
import test from 'node:test';
import assert from 'node:assert/strict';

function turnsFactor(p, k) {
  if (!p.N0) return 1;
  const kPar0 = Math.max(1, Math.round(p.wire_parallel0 ?? 1));
  const kPar = kPar0;
  const s0 = Math.max(1, Math.round(p.wire_split0 ?? 1));
  const s = Math.max(1, Math.round(k.split ?? s0));
  return ((k.N / kPar) * s) / ((p.N0 / kPar0) * s0);
}

const P = (over = {}) => ({ N0: 12, ...over });

test('the base point is the passport itself — ratio 1, split or not', () => {
  assert.equal(turnsFactor(P(), { N: 12 }), 1);
  assert.equal(turnsFactor(P({ wire_split0: 3 }), { N: 12 }), 1);
  assert.equal(turnsFactor(P({ wire_parallel0: 3 }), { N: 12 }), 1);
});

test('rows alone still scale the turns when the build is unsplit', () => {
  assert.equal(turnsFactor(P(), { N: 24 }), 2);
  assert.equal(turnsFactor(P(), { N: 6 }), 0.5);
});

test('the split MULTIPLIES the turns: an unsplit passport on a split machine', () => {
  // the very case the flag removal makes real: passport measured on one solid
  // bar per row, machine built with two strips per row = twice the turns
  assert.equal(turnsFactor(P(), { N: 12, split: 2 }), 2);
  assert.equal(turnsFactor(P(), { N: 6, split: 2 }), 1);
});

test('a split passport scaled onto an unsplit machine loses those turns', () => {
  assert.equal(turnsFactor(P({ wire_split0: 2 }), { N: 12, split: 1 }), 0.5);
});

test('a split that matches the passport cancels, whatever it is', () => {
  for (const s of [1, 2, 4]) {
    assert.equal(turnsFactor(P({ wire_split0: s }), { N: 24, split: s }), 2);
  }
});

test('absent means 1 on both sides, and an absent knob adopts the passport', () => {
  // no split anywhere = the plain row ratio every caller had before the split
  assert.equal(turnsFactor({ N0: 12 }, { N: 18 }), 1.5);
  // a caller that does not speak for a machine must not silently unsplit one
  assert.equal(turnsFactor(P({ wire_split0: 2 }), { N: 12 }), 1);
});

test('strands in hand are fixed by the build, so they cancel', () => {
  assert.equal(turnsFactor(P({ wire_parallel0: 3 }), { N: 24 }), 2);
  // …and they compose with the split rather than fighting it
  assert.equal(turnsFactor(P({ wire_parallel0: 3 }), { N: 12, split: 2 }), 2);
});
