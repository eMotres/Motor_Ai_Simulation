// node --test — the Configure tab's "one machine at a time" guard, copied
// verbatim from lib/configuratorGuard.ts (the repo's convention for node
// tests: `node --test` cannot load the TS modules, so the pure functions
// under test are re-stated here and kept in sync).
//
// What it pins (owner 2026-09-29, "a complete mess — three different motors
// on one page"): a `?design=` draft link must never compute result tiles
// from some OTHER machine's passport — not the previously-selected
// reference, not a "last matched" fallback — and the same rule applies to
// the loaded/open machine when it has no matched passport of its own.
import test from 'node:test';
import assert from 'node:assert/strict';

function sigMatches(a, b) {
  return Number.isFinite(a.numSlots) && Number.isFinite(a.numPoles)
      && Number.isFinite(b.numSlots) && Number.isFinite(b.numPoles)
      && a.numSlots === b.numSlots && a.numPoles === b.numPoles;
}

function resolveDraftTarget(refs, referenceMotorId, startingPoint) {
  if (!referenceMotorId) return undefined;
  const t = refs.find((r) => r.id === `cat:${referenceMotorId}`);
  if (!t) return undefined;
  const known = Number.isFinite(startingPoint.slots) && Number.isFinite(startingPoint.poles);
  if (known && !sigMatches(t.geo, { numSlots: startingPoint.slots, numPoles: startingPoint.poles })) {
    return undefined;
  }
  return t;
}

function isBlocked(opts) {
  return opts.draftOpen ? !opts.hasDraftTarget : !opts.liveMatched;
}

const REFS = [
  { id: 'cat:ciano14-40-l12', geo: { numSlots: 12, numPoles: 14 } },
  { id: 'ref-200-20p24s', geo: { numSlots: 24, numPoles: 20 } },
];

test('sigMatches: agrees only when both signatures are known and equal', () => {
  assert.equal(sigMatches({ numSlots: 24, numPoles: 20 }, { numSlots: 24, numPoles: 20 }), true);
  assert.equal(sigMatches({ numSlots: 24, numPoles: 20 }, { numSlots: 12, numPoles: 14 }), false);
  // an unknown side is never a match, even against itself
  assert.equal(sigMatches({ numSlots: null, numPoles: null }, { numSlots: null, numPoles: null }), false);
  assert.equal(sigMatches({ numSlots: 24, numPoles: undefined }, { numSlots: 24, numPoles: 20 }), false);
});

test('resolveDraftTarget: the draft resolves its own reference card', () => {
  const t = resolveDraftTarget(REFS, 'ciano14-40-l12', { slots: 12, poles: 14 });
  assert.equal(t?.id, 'cat:ciano14-40-l12');
});

test('resolveDraftTarget: no reference_motor_id at all -> no target', () => {
  assert.equal(resolveDraftTarget(REFS, null, { slots: 12, poles: 14 }), undefined);
  assert.equal(resolveDraftTarget(REFS, undefined, { slots: 12, poles: 14 }), undefined);
});

test('resolveDraftTarget: reference_motor_id names a card that is not in the list -> no target, no fallback', () => {
  assert.equal(resolveDraftTarget(REFS, 'nonexistent', { slots: 12, poles: 14 }), undefined);
});

test('resolveDraftTarget: a signature mismatch is refused even though the id resolved', () => {
  // the exact production bug: the draft's own card id points at a passport
  // whose slot/pole count does not match the draft's own starting_point —
  // must never be treated as "the model", only as "no model".
  assert.equal(resolveDraftTarget(REFS, 'ciano14-40-l12', { slots: 24, poles: 20 }), undefined);
});

test('resolveDraftTarget: an unknown starting_point signature (older draft) trusts the id', () => {
  assert.equal(resolveDraftTarget(REFS, 'ciano14-40-l12', { slots: null, poles: null })?.id,
    'cat:ciano14-40-l12');
});

test('isBlocked: viewing an opened draft is gated on ITS OWN target, not liveMatched', () => {
  assert.equal(isBlocked({ draftOpen: true, hasDraftTarget: true, liveMatched: false }), false);
  assert.equal(isBlocked({ draftOpen: true, hasDraftTarget: false, liveMatched: true }), true);
});

test('isBlocked: otherwise gated on the live machine having a matched passport', () => {
  assert.equal(isBlocked({ draftOpen: false, hasDraftTarget: false, liveMatched: true }), false);
  assert.equal(isBlocked({ draftOpen: false, hasDraftTarget: true, liveMatched: false }), true);
});
