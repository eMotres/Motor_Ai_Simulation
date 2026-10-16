// node --test: when the working-time heartbeat runs (lib/activityBeat.ts).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createActivityBeat, newSessionId, BEAT_INTERVAL_MS, IDLE_LIMIT_MS, FINAL_MIN_GAP_MS } from '../activityBeat.ts';

function rig({ visible = true, tab = 'motors', s: shared = null } = {}) {
  // `s` = an earlier rig's state: the SAME page (clock, timers, store, sent list), a fresh scheduler = a remount
  const s = shared ?? { t: 1_000_000, visible, tab, sent: [], timers: new Map(), next: 1, store: { lastSentAt: -Infinity } };
  const hb = createActivityBeat({
    store: s.store,
    now: () => s.t,
    visible: () => s.visible,
    send: (body, unload) => s.sent.push({ ...body, unload }),
    getTab: () => s.tab,
    sid: 'sess-testtest',
    setTimer: (fn, ms) => { const id = s.next++; s.timers.set(id, { fn, at: s.t + ms }); return id; },
    clearTimer: (id) => s.timers.delete(id),
  });
  // advance the fake clock, firing due timers in order
  s.advance = (ms) => {
    const end = s.t + ms;
    for (;;) {
      const due = [...s.timers.entries()].filter(([, v]) => v.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      s.timers.delete(due[0]); s.t = due[1].at; due[1].fn();
    }
    s.t = end;
  };
  return { s, hb };
}

test('a visible, freshly opened page beats at once, then every 60 s', () => {
  const { s, hb } = rig();
  hb.start();
  assert.equal(s.sent.length, 1);
  s.advance(BEAT_INTERVAL_MS * 3);
  hb.touch();
  assert.equal(s.sent.length, 4);
  assert.deepEqual(Object.keys(s.sent[0]).sort(), ['sid', 'tab', 'ts', 'unload']);
  assert.equal(s.sent[0].sid, 'sess-testtest');
  assert.equal(s.sent[0].unload, false);
});

test('a hidden page never starts', () => {
  const { s, hb } = rig({ visible: false });
  hb.start(); hb.touch();
  s.advance(BEAT_INTERVAL_MS * 3);
  assert.equal(s.sent.length, 0);
  assert.equal(hb.isRunning(), false);
});

test('idle for more than 5 minutes stops the beats; a touch restarts them', () => {
  const { s, hb } = rig();
  hb.start();                                   // t0: beat 1
  s.advance(IDLE_LIMIT_MS + BEAT_INTERVAL_MS);  // no interaction at all
  const before = s.sent.length;
  assert.ok(before <= 6, 'beats only while the last interaction is under 5 minutes old');
  assert.equal(hb.isRunning(), false);
  s.advance(BEAT_INTERVAL_MS * 10);
  assert.equal(s.sent.length, before, 'nothing while idle');
  hb.touch();
  assert.equal(s.sent.length, before + 1, 'the touch restarts the beats');
  assert.equal(hb.isRunning(), true);
});

test('interaction keeps it running past the idle limit', () => {
  const { s, hb } = rig();
  hb.start();
  for (let i = 0; i < 20; i++) { s.advance(BEAT_INTERVAL_MS); hb.touch(); }
  assert.equal(s.sent.length, 21);
  assert.equal(hb.isRunning(), true);
});

test('hiding the tab sends one final beat (unload transport) and stops; showing it restarts', () => {
  const { s, hb } = rig();
  hb.start();
  s.advance(BEAT_INTERVAL_MS + 30_000);
  const n = s.sent.length;
  s.visible = false; hb.onVisibility();
  assert.equal(s.sent.length, n + 1);
  assert.equal(s.sent.at(-1).unload, true);
  assert.equal(hb.isRunning(), false);
  s.advance(BEAT_INTERVAL_MS * 2);
  assert.equal(s.sent.length, n + 1, 'nothing while hidden');
  s.visible = true; hb.onVisibility();       // the last beat is long ago: the stretch restarts with a beat at once
  assert.equal(s.sent.length, n + 2);
  assert.equal(s.sent.at(-1).unload, false);
  assert.equal(hb.isRunning(), true);
});

test('pagehide flushes once, and only when running', () => {
  const { s, hb } = rig();
  hb.start();
  s.advance(FINAL_MIN_GAP_MS + 1_000);
  hb.onPageHide();
  assert.equal(s.sent.length, 2);
  assert.equal(s.sent.at(-1).unload, true);
  hb.onPageHide();
  assert.equal(s.sent.length, 2, 'not running: no second flush');
});

test('coming back after a long hidden spell without touching sends nothing until a touch', () => {
  const { s, hb } = rig();
  hb.start();
  s.visible = false; hb.onVisibility();
  s.advance(IDLE_LIMIT_MS * 2);
  const n = s.sent.length;
  s.visible = true; hb.onVisibility();
  assert.equal(s.sent.length, n, 'idle > 5 min: the page being visible is not work');
  hb.touch();
  assert.equal(s.sent.length, n + 1);
});

test('the tab id is read at send time; stop() ends everything', () => {
  const { s, hb } = rig();
  hb.start();
  s.tab = 'compare'; s.advance(BEAT_INTERVAL_MS);
  assert.equal(s.sent.at(-1).tab, 'compare');
  hb.stop();
  s.advance(BEAT_INTERVAL_MS * 3); hb.touch();
  assert.equal(s.sent.at(-1).tab, 'compare');
  assert.equal(hb.isRunning(), false);
});

test('session ids have the shape the server accepts', () => {
  assert.match(newSessionId(), /^[A-Za-z0-9_-]{8,64}$/);
  assert.match(newSessionId(() => 0.5), /^[A-Za-z0-9_-]{8,64}$/);
});

// ---- incident 2026-10-15: hundreds of beats in a row; exactly one per 60 s per tab ----
const beatsOf = (s) => s.sent.filter((b) => !b.unload).length;

test('10 minutes of constant interaction: one beat per 60 s, not one per touch', () => {
  const { s, hb } = rig();
  hb.start();
  for (let i = 0; i < 6000; i++) { s.advance(100); hb.touch(); }   // a touch every 100 ms for 10 min
  assert.ok(beatsOf(s) >= 10 && beatsOf(s) <= 11, `beats: ${beatsOf(s)}`);
});

test('re-mounts (StrictMode double effect, sign-in state changes) never add a timer or an early beat', () => {
  const first = rig();
  first.hb.start();
  let hb = first.hb;
  for (let i = 0; i < 50; i++) {          // 50 remounts within the first 10 s
    hb.stop();
    ({ hb } = rig({ s: first.s }));
    hb.start();
    first.s.advance(200);
  }
  assert.equal(first.s.sent.length, 1, 'only the very first start beat');
  assert.equal(first.s.timers.size, 1, 'exactly one pending timer');
  for (let i = 0; i < 10; i++) { first.s.advance(60_000); hb.touch(); }   // 10 minutes, active
  assert.ok(beatsOf(first.s) >= 10 && beatsOf(first.s) <= 11, `beats: ${beatsOf(first.s)}`);
  assert.equal(first.s.timers.size, 1);
});

test('a remount long after the last beat beats at once; a remount soon after waits out the minute', () => {
  const a = rig(); a.hb.start(); a.hb.stop();
  a.s.advance(20_000);
  const b = rig({ s: a.s }); b.hb.start();
  assert.equal(a.s.sent.length, 1);
  a.s.advance(40_000 - 1); assert.equal(a.s.sent.length, 1);
  a.s.advance(1); assert.equal(a.s.sent.length, 2, 'the rest of the 60 s, then the beat');
});

test('visibility flapping every second for 10 minutes: regular beats stay one per minute, finals are rate-limited', () => {
  const { s, hb } = rig();
  hb.start();
  for (let i = 0; i < 600; i++) {
    s.advance(1_000);
    s.visible = !s.visible; hb.onVisibility(); hb.touch();
  }
  s.visible = true; hb.onVisibility();
  const regular = beatsOf(s), finals = s.sent.length - regular;
  assert.ok(regular <= 11, `regular beats: ${regular}`);
  assert.ok(finals <= 10, `final beats: ${finals}`);
  assert.ok(s.sent.length <= 21, `all beats: ${s.sent.length}`);
  assert.ok(s.timers.size <= 1);
});

test('idle -> touch after hours: one beat, not a burst', () => {
  const { s, hb } = rig();
  hb.start();
  s.advance(3 * 3_600_000);
  const n = s.sent.length;
  for (let i = 0; i < 200; i++) hb.touch();
  assert.equal(s.sent.length, n + 1);
  assert.equal(s.timers.size, 1);
});

test('browser wiring: starting twice (re-mount) leaves ONE scheduler, one listener set, one session id', async () => {
  const { startActivityBeat } = await import('../activityBeat.ts');
  const timers = new Map(); let nextId = 1; let now = 5_000_000;
  const listeners = new Map();                       // "target:event" -> count
  const track = (name) => ({
    addEventListener: (e) => listeners.set(`${name}:${e}`, (listeners.get(`${name}:${e}`) ?? 0) + 1),
    removeEventListener: (e) => listeners.set(`${name}:${e}`, (listeners.get(`${name}:${e}`) ?? 0) - 1),
  });
  const calls = [];
  const realNow = Date.now;
  Date.now = () => now;
  globalThis.window = { ...track('w'),
    setTimeout: (fn, ms) => { const id = nextId++; timers.set(id, { fn, at: now + ms }); return id; },
    clearTimeout: (id) => timers.delete(id) };
  globalThis.document = { visibilityState: 'visible', ...track('d') };
  const realFetch = globalThis.fetch;
  globalThis.fetch = (url, init) => { calls.push(JSON.parse(init.body)); return Promise.resolve({}); };
  try {
    const opts = { apiBase: 'http://x', getTab: () => 'motors', getToken: () => 'tok' };
    const stops = [];
    for (let i = 0; i < 20; i++) { stops.push(startActivityBeat(opts)); }   // 20 overlapping starts, no stop in between
    assert.equal(calls.length, 1, 'one beat');
    assert.equal(timers.size, 1, 'one timer');
    for (const [k, v] of listeners) if (k.startsWith('w:pointermove')) assert.equal(v, 1, 'one listener set');
    assert.equal(new Set(calls.map((c) => c.sid)).size, 1);
    for (const st of stops) st();                    // stopping the stale ones is harmless
    for (const st of stops) st();
    assert.equal(timers.size, 0);
    const again = startActivityBeat(opts);           // a remount soon after: the same sid, no early beat
    assert.equal(calls.length, 1);
    assert.equal(timers.size, 1);
    again();
  } finally {
    Date.now = realNow; globalThis.fetch = realFetch;
    delete globalThis.window; delete globalThis.document;
  }
});
