import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const shared = fs.readFileSync(new URL('../../compare/SharedComparisonTable.tsx', import.meta.url), 'utf8');
const local = fs.readFileSync(new URL('../LocalCompareTable.tsx', import.meta.url), 'utf8');

test('shared unit styling preserves Compare defaults and LocalCompareTable original styling', () => {
  assert.match(shared, /unitColor = 'var\(--text-3\)'/);
  assert.match(shared, /unitFontWeight = 500/);
  assert.match(shared, /sx=\{\{ color: unitColor, fontWeight: unitFontWeight \}\}/);
  assert.match(local, /unitColor="var\(--line\)" unitFontWeight=\{400\}/);
});
