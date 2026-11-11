// node --test — the browser / phone Back walks UP one level (mobile audit 2026-11).
// A fake history with the browser's semantics: pushState truncates the forward entries, back() is ASYNC (a task later).
import test from 'node:test';
import assert from 'node:assert/strict';

let H = null;
try { H = await import('../historyLevels.ts'); } catch { /* old Node */ }
const t = H ? test : test.skip;

function fakeHistory() {
  const h = {
    entries: [{ state: null }], index: 0, queued: 0, onpop: null,
    get state() { return h.entries[h.index].state; },
    pushState(data) { h.entries = h.entries.slice(0, h.index + 1); h.entries.push({ state: data }); h.index += 1; },
    back() { h.queued += 1; },
    /** run the queued traversals, then a user Back (`user`), firing popstate like a browser */
    flush() { while (h.queued > 0) { h.queued -= 1; if (h.index > 0) { h.index -= 1; h.onpop?.(h.state); } } },
    userBack() { if (h.index > 0) { h.index -= 1; h.onpop?.(h.state); return true; } return false; },
  };
  return h;
}

t('Back closes the open level instead of leaving the page', () => {
  const h = fakeHistory(); const lv = new H.HistoryLevels(h); h.onpop = (s) => lv.onPop(s);
  let closed = 0;
  lv.open(() => { closed += 1; });
  assert.equal(h.entries.length, 2);
  assert.equal(H.levelOf(h.state), 1);
  assert.ok(h.userBack());
  assert.equal(closed, 1);
  assert.equal(lv.depth, 0);
  assert.equal(h.index, 0, 'one Back, one level: the next Back is the browser\'s own');
});

t('closing a level from the page removes its entry, so Back is never a dead press', () => {
  const h = fakeHistory(); const lv = new H.HistoryLevels(h); h.onpop = (s) => lv.onPop(s);
  let closed = 0;
  const release = lv.open(() => { closed += 1; });
  release();                       // the page's own back arrow
  h.flush();
  assert.equal(closed, 0, 'the page closed itself; Back must not run close again');
  assert.equal(h.index, 0);
  assert.equal(lv.depth, 0);
});

t('nested levels close one per Back, the deepest first', () => {
  const h = fakeHistory(); const lv = new H.HistoryLevels(h); h.onpop = (s) => lv.onPop(s);
  const order = [];
  lv.open(() => order.push('project'));
  lv.open(() => order.push('tests'));
  h.userBack();
  assert.deepEqual(order, ['tests']);
  h.userBack();
  assert.deepEqual(order, ['tests', 'project']);
});

t('a release that arrives after Back already closed the level does nothing', () => {
  const h = fakeHistory(); const lv = new H.HistoryLevels(h); h.onpop = (s) => lv.onPop(s);
  const release = lv.open(() => release());   // the page's close handler unmounts the level, which releases it
  h.userBack();
  h.flush();
  assert.equal(h.queued, 0);
  assert.equal(h.index, 0);
});

t('a remount in the same tick (React StrictMode) leaves exactly one open level that Back closes', () => {
  const h = fakeHistory(); const lv = new H.HistoryLevels(h); h.onpop = (s) => lv.onPop(s);
  let closed = 0;
  const r1 = lv.open(() => { closed += 1; });
  r1();
  lv.open(() => { closed += 1; });
  h.flush();
  assert.equal(lv.depth, 1);
  assert.ok(h.userBack());
  assert.equal(closed, 1);
});

t('levelOf reads only our marker', () => {
  assert.equal(H.levelOf(null), 0);
  assert.equal(H.levelOf({ __lvl: 3 }), 3);
  assert.equal(H.levelOf({ __lvl: 'x' }), 0);
  assert.equal(H.levelOf({ other: 1 }), 0);
});
