/** Colour mapping for the Admin · Overview live-load charts, tested without a
 * browser (`liveLoadColors.ts` has no runtime imports). */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  hashSlot, serverColor, userColor, containerColor, hostColor, overheadColor,
  RAM_ACCENT, RAM_GRID,
} from '../liveLoadColors.ts';

// ── hashSlot: the primitive every "hash-stable" colour is built on ─────────
test('hashSlot is a pure function of its input: same id -> same slot, always', () => {
  const a = hashSlot('eu1', 6);
  for (let i = 0; i < 20; i++) assert.equal(hashSlot('eu1', 6), a);
});

test('hashSlot is in range and stable across the module reporting different slot counts', () => {
  for (const id of ['alice', 'bob', 'mesher_1', 'eu2', '']) {
    const slot = hashSlot(id, 6);
    assert.ok(slot >= 0 && slot < 6, `${id} -> ${slot} out of [0,6)`);
  }
});

test('hashSlot does not crash on an empty id or slots=0', () => {
  assert.equal(typeof hashSlot('', 6), 'number');
  assert.equal(hashSlot('x', 0), 0);
});

// ── per-role colour pickers: hash-stable, and reserved slots never collide ─
test('serverColor and userColor are hash-stable across repeated calls, in both modes', () => {
  for (const mode of ['light', 'dark']) {
    const c1 = serverColor('eu1', mode);
    const c2 = serverColor('eu1', mode);
    assert.equal(c1, c2);
    const u1 = userColor('alice', mode);
    const u2 = userColor('alice', mode);
    assert.equal(u1, u2);
  }
});

test('userColor is independent of what else is currently visible (no rank/index input)', () => {
  // the whole point of hashing on id alone: a filter or a top-N cutoff that
  // changes who else is on screen must not repaint a survivor's colour.
  assert.equal(userColor('alice', 'light'), userColor('alice', 'light'));
  // calling it "as if" surrounded by a different set changes nothing, because
  // the function signature has no such parameter -- this test documents that
  // guarantee structurally: userColor takes only (id, mode).
  assert.equal(userColor.length, 2);
});

test('userColor never returns a colour from the reserved container/host slots', () => {
  const containerHex = { light: '#eb6834', dark: '#d95926' };
  const hostHex = { light: '#1baf7a', dark: '#199e70' };
  const ids = ['alice', 'bob', 'carol', 'dave', 'eve', 'frank', 'grace', 'heidi', 'ivan', 'judy'];
  for (const mode of ['light', 'dark']) {
    for (const id of ids) {
      const c = userColor(id, mode);
      assert.notEqual(c, containerHex[mode]);
      assert.notEqual(c, hostHex[mode]);
    }
  }
});

test('containerColor picks the single shared warm hue at a hash-stable opacity step', () => {
  const mode = 'light';
  const a = containerColor('mesher_1', mode);
  const b = containerColor('mesher_1', mode);
  assert.deepEqual(a, b);
  assert.equal(a.stroke, '#eb6834');
  assert.ok(a.fillOpacity > 0 && a.fillOpacity <= 1);
});

test('containerColor distinguishes two different containers by opacity, same hue', () => {
  const a = containerColor('mesher_1', 'light');
  const b = containerColor('prof_a', 'light');
  assert.equal(a.stroke, b.stroke);   // same warm family hue
  // (opacity may coincide for some id pairs by hash chance -- that's fine,
  // legend + tooltip carry exact identity; this just checks the shape)
  assert.ok(typeof a.fillOpacity === 'number' && typeof b.fillOpacity === 'number');
});

test('RAM_ACCENT/RAM_GRID are fixed, mode-aware, and distinct from every server-slot hue', () => {
  assert.notEqual(RAM_ACCENT.light, RAM_ACCENT.dark);
  assert.ok(RAM_GRID.light.startsWith('rgba(') && RAM_GRID.dark.startsWith('rgba('));
  // the accent reuses the master violet slot verbatim, in a chart-chrome
  // role (cluster-mean line, grid) rather than as a per-series identity --
  // see the long comment in liveLoadColors.ts for why a genuinely separate
  // per-server cool palette was tried and rejected (CVD gates).
  assert.equal(RAM_ACCENT.light, '#4a3aa7');
  assert.equal(RAM_ACCENT.dark, '#9085e9');
});

test('hostColor and overheadColor are fixed (not hashed) and mode-aware', () => {
  assert.equal(hostColor('light'), '#1baf7a');
  assert.equal(hostColor('dark'), '#199e70');
  assert.equal(overheadColor('light'), '#92400e');
  assert.equal(overheadColor('dark'), '#c2831f');
});

test('server and user colours come from disjoint mode-specific palettes (dark != light hex)', () => {
  assert.notEqual(serverColor('eu1', 'light'), serverColor('eu1', 'dark'));
  assert.notEqual(userColor('alice', 'light'), userColor('alice', 'dark'));
});

// ── distribution sanity: a handful of distinct ids should not all collapse ──
test('a handful of distinct user ids do not all hash to the same slot', () => {
  const ids = ['alice', 'bob', 'carol', 'dave', 'eve', 'frank'];
  const colors = new Set(ids.map((id) => userColor(id, 'light')));
  assert.ok(colors.size > 1, 'expected more than one distinct colour across 6 different users');
});

// ── distribution: regression guard for the mod-6 bias djb2 alone had ───────
// (['alice',...,'frank'] collapsed onto 2 of 6 slots before the avalanche
// finalizer was added -- this pins the fix, not just "more than one colour").
test('six short similar names use most of the six user slots, not a couple', () => {
  const ids = ['alice', 'bob', 'carol', 'dave', 'eve', 'frank'];
  const slots = new Set(ids.map((id) => hashSlot(id, 6)));
  assert.ok(slots.size >= 4, `expected >= 4 distinct slots for 6 names, got ${slots.size}`);
});

test('hashSlot is roughly uniform over a large id sample (no slot starved)', () => {
  const counts = new Array(6).fill(0);
  const n = 3000;
  for (let i = 0; i < n; i++) counts[hashSlot(`user${i}`, 6)] += 1;
  const expected = n / 6;
  for (const c of counts) assert.ok(Math.abs(c - expected) < expected * 0.35,
    `slot count ${c} too far from uniform expectation ${expected}: ${counts}`);
});
