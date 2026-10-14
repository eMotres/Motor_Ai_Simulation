// node --test: when the working-time heartbeat runs (lib/activityBeat.ts).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createActivityBeat, newSessionId, BEAT_INTERVAL_MS, IDLE_LIMIT_MS } from '../activityBeat.ts';

function rig({ visible = true, tab = 'motors' } = {}) {
  const s = { t: 1_000_000, visible, tab, sent: [], timers: new Map(), next: 1 };
  const hb = createActivityBeat({
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
  s.advance(BEAT_INTERVAL_MS + 5_000);
  const n = s.sent.length;
  s.visible = false; hb.onVisibility();
  assert.equal(s.sent.length, n + 1);
  assert.equal(s.sent.at(-1).unload, true);
  assert.equal(hb.isRunning(), false);
  s.advance(BEAT_INTERVAL_MS * 2);
  assert.equal(s.sent.length, n + 1, 'nothing while hidden');
  s.visible = true; hb.onVisibility();
  assert.equal(s.sent.length, n + 2);
  assert.equal(s.sent.at(-1).unload, false);
  assert.equal(hb.isRunning(), true);
});

test('pagehide flushes once, and only when running', () => {
  const { s, hb } = rig();
  hb.start();
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
