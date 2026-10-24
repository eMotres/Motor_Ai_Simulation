import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

test('a duty load sends the six-phase split with the connection, and phases 3 otherwise', () => {
  const src = readFileSync(new URL('../dutyApply.ts', import.meta.url), 'utf8');
  const patch = src.slice(src.indexOf('/api/winding/config'), src.indexOf('/api/winding/config') + 900);
  assert.match(patch, /phases: Number\(p\.winding\?\.phases\) === 6 \? 6 : 3/);
  assert.match(patch, /set1_paths: String\(p\.winding\?\.set1_paths/);
  assert.match(patch, /set_neutrals: String\(p\.winding\?\.set_neutrals \|\| 'isolated'\)/);
});
