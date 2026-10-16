// node --test — undo/redo of the OPTIMIZATION VARIABLES list and the keyboard
// guard (lib/optVarsHistory.ts, loaded directly: Node strips the TS types).
//
// Owner 2026-10-15: "+ Add all geometry" by accident, only "Remove all" to get
// back (which also deleted his earlier variables).  Pinned here:
//   - add all -> undo restores the previous list EXACTLY (same objects), redo
//     brings the 16 back;
//   - a new action after an undo drops the redo branch;
//   - at most HISTORY_LIMIT states are kept (oldest dropped);
//   - typing in one range field is ONE step (coalesced), a different field or a
//     pause starts a new one;
//   - Ctrl+Z inside a text field is the browser's, never ours.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  emptyHistory, record, undo, redo, canUndo, canRedo, undoLabel, redoLabel,
  HISTORY_LIMIT, COALESCE_MS, undoKeyAction, isEditableTarget,
} from '../optVarsHistory.ts';

const mk = (names, mode = 'optimize') =>
  Object.fromEntries(names.map(n => [n, { mode, min: 1, max: 2, step: 0.1 }]));

test('add all -> undo restores the previous list exactly, redo re-applies', () => {
  const before = { ...mk(['a', 'b']), c: { mode: 'fixed', min: 3, max: 3, step: 1 } };
  const geo = Array.from({ length: 16 }, (_, i) => `g${i}`);
  const after = { ...before, ...mk(geo) };
  let h = record(emptyHistory(), before, 'Add all geometry (16 variables)');
  assert.ok(canUndo(h));
  assert.equal(undoLabel(h), 'Add all geometry (16 variables)');
  const u = undo(h, after);
  assert.ok(u);
  assert.equal(u.vars, before);                       // the very same map
  assert.deepEqual(u.vars, before);
  assert.ok(!canUndo(u.history) && canRedo(u.history));
  assert.equal(redoLabel(u.history), 'Add all geometry (16 variables)');
  const r = redo(u.history, u.vars);
  assert.ok(r);
  assert.equal(r.vars, after);
  assert.equal(Object.keys(r.vars).length, 19);
  assert.ok(canUndo(r.history) && !canRedo(r.history));
});

test('remove all (which wipes earlier variables) is undoable too', () => {
  const before = mk(['a', 'b', 'c']);
  const removed = Object.fromEntries(Object.entries(before).map(([k, v]) => [k, { ...v, mode: 'fixed' }]));
  const h = record(emptyHistory(), before, 'Remove all (3 variables)');
  assert.deepEqual(undo(h, removed).vars, before);
});

test('nothing to undo / redo on an empty history', () => {
  assert.equal(undo(emptyHistory(), {}), null);
  assert.equal(redo(emptyHistory(), {}), null);
  assert.equal(undoLabel(emptyHistory()), null);
});

test('a new action after an undo clears the redo branch', () => {
  const s0 = mk([]), s1 = mk(['a']), s2 = mk(['a', 'b']);
  let h = record(emptyHistory(), s0, 'add a');
  h = record(h, s1, 'add b');
  const u = undo(h, s2);
  assert.ok(canRedo(u.history));
  const h2 = record(u.history, s1, 'add c');
  assert.ok(!canRedo(h2));
  assert.equal(h2.past.length, 2);
});

test('several undos walk back in order', () => {
  const s = [mk([]), mk(['a']), mk(['a', 'b']), mk(['a', 'b', 'c'])];
  let h = emptyHistory();
  for (let i = 0; i < 3; i++) h = record(h, s[i], `step ${i + 1}`);
  let cur = s[3];
  for (let i = 2; i >= 0; i--) {
    const u = undo(h, cur);
    assert.equal(u.vars, s[i]);
    assert.equal(u.label, `step ${i + 1}`);
    h = u.history; cur = u.vars;
  }
  assert.equal(undo(h, cur), null);
});

test('history is limited to the last 30 states', () => {
  let h = emptyHistory();
  for (let i = 0; i < 45; i++) h = record(h, { n: i }, `a${i}`);
  assert.equal(HISTORY_LIMIT, 30);
  assert.equal(h.past.length, 30);
  assert.equal(h.past[0].label, 'a15');               // the oldest 15 are gone
  assert.equal(h.past[29].label, 'a44');
  assert.equal(h.past[0].vars.n, 15);
});

test('typing in one field is ONE undo step; another field or a pause starts a new one', () => {
  const t0 = 1_000_000;
  const s0 = { a: { min: 1 } };
  let h = record(emptyHistory(), s0, 'edit a', { key: 'a:min', now: t0 });
  // keystrokes 2 and 3 land within the window: still the original snapshot
  h = record(h, { a: { min: 7 } }, 'edit a', { key: 'a:min', now: t0 + 300 });
  h = record(h, { a: { min: 7.4 } }, 'edit a', { key: 'a:min', now: t0 + 700 });
  assert.equal(h.past.length, 1);
  assert.equal(h.past[0].vars, s0);                  // undo goes back to BEFORE the typing
  // a different field -> a new step
  h = record(h, { a: { min: 7.45 } }, 'edit a', { key: 'a:max', now: t0 + 900 });
  assert.equal(h.past.length, 2);
  // the same field after a pause -> a new step
  h = record(h, { a: { min: 7.45 } }, 'edit a', { key: 'a:max', now: t0 + 900 + COALESCE_MS + 1 });
  assert.equal(h.past.length, 3);
  // an unkeyed action never coalesces
  h = record(h, {}, 'x', { now: t0 + 10_000 });
  h = record(h, {}, 'x', { now: t0 + 10_001 });
  assert.equal(h.past.length, 5);
});

// ── keyboard guard ───────────────────────────────────────────────────────────

const key = (k, extra = {}) => ({ key: k, ctrlKey: true, ...extra });
const BTN = { tagName: 'BUTTON' };
const DIV = { tagName: 'DIV' };

test('Ctrl/Cmd+Z undo, Ctrl+Shift+Z and Ctrl+Y redo outside text fields', () => {
  assert.equal(undoKeyAction(key('z'), BTN), 'undo');
  assert.equal(undoKeyAction(key('Z', { shiftKey: true }), DIV), 'redo');
  assert.equal(undoKeyAction(key('y'), DIV), 'redo');
  assert.equal(undoKeyAction({ key: 'z', metaKey: true }, DIV), 'undo');
  assert.equal(undoKeyAction({ key: 'z', metaKey: true, shiftKey: true }, DIV), 'redo');
  assert.equal(undoKeyAction(key('z'), null), 'undo');
});

test('inside an editable field the browser keeps its own undo', () => {
  for (const t of [
    { tagName: 'INPUT', type: 'text' },
    { tagName: 'INPUT', type: 'number' },
    { tagName: 'INPUT', type: '' },
    { tagName: 'INPUT' },
    { tagName: 'TEXTAREA' },
    { tagName: 'DIV', isContentEditable: true },
  ]) {
    assert.equal(undoKeyAction(key('z'), t), null, JSON.stringify(t));
    assert.equal(undoKeyAction(key('z', { shiftKey: true }), t), null);
    assert.equal(undoKeyAction(key('y'), t), null);
    assert.ok(isEditableTarget(t));
  }
});

test('a checkbox / button / disabled field is not an editing context', () => {
  assert.equal(undoKeyAction(key('z'), { tagName: 'INPUT', type: 'checkbox' }), 'undo');
  assert.equal(undoKeyAction(key('z'), { tagName: 'INPUT', type: 'text', disabled: true }), 'undo');
  assert.ok(!isEditableTarget({ tagName: 'SELECT' }));
});

test('other keys and combinations are ignored', () => {
  assert.equal(undoKeyAction({ key: 'z' }, DIV), null);                       // no modifier
  assert.equal(undoKeyAction(key('x'), DIV), null);
  assert.equal(undoKeyAction(key('z', { altKey: true }), DIV), null);
  assert.equal(undoKeyAction(key('y', { shiftKey: true }), DIV), null);
  assert.equal(undoKeyAction({ key: 'y', metaKey: true }, DIV), null);        // Cmd+Y is not redo on mac
});
