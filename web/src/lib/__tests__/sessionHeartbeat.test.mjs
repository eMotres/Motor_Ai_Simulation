// node --test: when the session heartbeat is sent (lib/sessionHeartbeat.ts).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createSessionHeartbeat, SESSION_BEAT_MIN_GAP_MS } from '../sessionHeartbeat.ts';

function rig({ visible = true } = {}) {
  const s = { t: 1_000_000, visible, sent: 0, timers: new Map(), next: 1 };
  const hb = createSessionHeartbeat({
    now: () => s.t,
    visible: () => s.visible,
    send: () => { s.sent += 1; },
    setTimer: (fn, ms) => { const id = s.next++; s.timers.set(id, { fn, at: s.t + ms }); return id; },
    clearTimer: (id) => s.timers.delete(id),
  });
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

test('no input, no beat', () => {
  const { s } = rig();
  s.advance(10 * 60_000);
  assert.equal(s.sent, 0);
});

test('the first input after a quiet spell beats at once, further input within a minute waits for the rest of it', () => {
  const { s, hb } = rig();
  hb.touch(); s.advance(0);
  assert.equal(s.sent, 1);
  s.advance(10_000); hb.touch(); s.advance(1_000);
  assert.equal(s.sent, 1);                         // too soon
  s.advance(SESSION_BEAT_MIN_GAP_MS);              // the minute is up: the pending input is sent
  assert.equal(s.sent, 2);
});

test('at most one beat a minute however much input there is', () => {
  const { s, hb } = rig();
  for (let i = 0; i < 600; i++) { hb.touch(); s.advance(1_000); }   // 10 minutes of constant input
  assert.ok(s.sent <= 11 && s.sent >= 9, `sent ${s.sent}`);
});

test('input only counts once: no new input, no new beat', () => {
  const { s, hb } = rig();
  hb.touch(); s.advance(0);
  s.advance(30 * 60_000);
  assert.equal(s.sent, 1);
});

test('a hidden tab sends nothing and forgets input made before it was hidden', () => {
  const { s, hb } = rig();
  hb.touch(); s.advance(0);                        // beat 1
  s.advance(5_000); hb.touch();                    // pending, timer armed
  s.visible = false; hb.onVisibility();
  s.advance(5 * 60_000);
  assert.equal(s.sent, 1);
  hb.touch(); s.advance(60_000);                   // input events of a hidden tab are ignored
  assert.equal(s.sent, 1);
  s.visible = true; hb.onVisibility();
  s.advance(5 * 60_000);
  assert.equal(s.sent, 1);                         // becoming visible is not input
  hb.touch(); s.advance(0);
  assert.equal(s.sent, 2);
});
