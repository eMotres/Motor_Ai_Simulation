// node --test - the top tab bar on a phone (owner 2026-11-11, 390 px: the labels overlapped and were cut).
// The variant was chosen by a JS media query ('fullWidth' on a desktop width); a window that narrowed kept it and each tab
// was squeezed to 26 px.  The bar is now always scrollable and CSS shares a wide bar evenly (App.tsx TOP_TAB_SX).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const app = readFileSync(new URL('../../App.tsx', import.meta.url), 'utf8');

test('the top bar is always scrollable, never a width-dependent fullWidth', () => {
  assert.match(app, /variant="scrollable"/);
  assert.doesNotMatch(app, /variant=\{narrow \? 'scrollable' : 'fullWidth'\}/);
});

test('a tab never shrinks below its label, and a wide bar is shared evenly', () => {
  const sx = app.match(/const TOP_TAB_SX = \{([\s\S]*?)\} as const;/);
  assert.ok(sx, 'TOP_TAB_SX is defined');
  assert.match(sx[1], /minWidth: 'max-content'/);
  assert.match(sx[1], /flexGrow: 1/);
  assert.match(sx[1], /flexShrink: 0/);
  assert.match(sx[1], /flexBasis: 0/);
  assert.doesNotMatch(sx[1], /minWidth: 0/);
  assert.match(app, /sx=\{TOP_TAB_SX\}/, 'the Simulations tab');
  assert.match(app, /sx=\{\{ \.\.\.TOP_TAB_SX,/, 'every other tab');
});
