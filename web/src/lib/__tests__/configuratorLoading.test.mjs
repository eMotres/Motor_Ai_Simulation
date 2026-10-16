// node --test — Configure must not flash "no configurator model" before it has looked
// (owner 2026-10-05: "why does it first write this and only then load everything").
// Imports lib/configuratorGuard.ts itself (type stripping, Node >= 22.18 / 24); on an
// older Node the suite is skipped.
//
// The empty state is only ever reached through  loading -> blocked  AFTER the
// references fetch was answered and the loaded machine was matched against it.
import test from 'node:test';
import assert from 'node:assert/strict';

let G = null;
try { G = await import('../configuratorGuard.ts'); } catch { /* old Node */ }
const t = G ? test : test.skip;

const S = (o) => G.modelState({ refsAnswered: false, matchChecked: false, draftOpen: false,
  hasDraftTarget: false, liveMatched: false, ...o });

t('before the fetch is answered it is loading — never the error', () => {
  assert.equal(S({}), 'loading');
  // even a "matched" flag cannot short-circuit a fetch that has not answered
  assert.equal(S({ matchChecked: true, liveMatched: true }), 'loading');
  assert.equal(S({ draftOpen: true, hasDraftTarget: false }), 'loading');
});

t('answered but the loaded machine is not matched yet: still loading', () => {
  assert.equal(S({ refsAnswered: true }), 'loading');
});

t('loading -> loaded: a matched machine is ready', () => {
  assert.equal(S({ refsAnswered: true, matchChecked: true, liveMatched: true }), 'ready');
});

t('loading -> genuinely none: the empty state only after fetch + match found nothing', () => {
  const seq = [
    S({}),                                                        // fetch in flight
    S({ refsAnswered: true }),                                    // answered, matching
    S({ refsAnswered: true, matchChecked: true }),                // answered, no match
  ];
  assert.deepEqual(seq, ['loading', 'loading', 'blocked']);
});

t('an answered-but-empty catalogue is a real "none", not a reason to keep loading', () => {
  assert.equal(S({ refsAnswered: true, matchChecked: true, liveMatched: false }), 'blocked');
});

t('an opened draft needs the answered references, then follows its own target', () => {
  assert.equal(S({ refsAnswered: true, draftOpen: true, hasDraftTarget: true }), 'ready');
  assert.equal(S({ refsAnswered: true, draftOpen: true, hasDraftTarget: false }), 'blocked');
});

t('the older guard is unchanged', () => {
  assert.equal(G.isBlocked({ draftOpen: false, hasDraftTarget: false, liveMatched: true }), false);
  assert.equal(G.isBlocked({ draftOpen: true, hasDraftTarget: false, liveMatched: true }), true);
});
