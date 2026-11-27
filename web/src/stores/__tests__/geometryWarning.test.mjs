// The geometry routes attach `_warnings` (owner 2026-11-27: the machine is drawn, the window says what does not fit).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { splitWarnings } from '../geometryWarningStore.ts';

test('the warnings are split off the mesh answer; the mesh parts stay', () => {
  const answer = { stator_core: { vertices: [0] }, _warnings: [{ code: 'wire_too_wide_for_slot', field: 'wire_split', message: 'wire split too wide', turns: 6, series_turns: 12 }] };
  const { warnings, rest } = splitWarnings(answer);
  assert.equal(warnings.length, 1);
  assert.equal(warnings[0].series_turns, 12);
  assert.deepEqual(Object.keys(rest), ['stator_core']);
});

test('no warning, a malformed warning or a non-array value is an empty list', () => {
  assert.deepEqual(splitWarnings({ a: 1 }).warnings, []);
  assert.deepEqual(splitWarnings({ a: 1, _warnings: 'x' }).warnings, []);
  assert.deepEqual(splitWarnings({ a: 1, _warnings: [{}, null, { message: 3 }] }).warnings, []);
});

test('the viewer hooks use it for the extruded and the flat mesh, cached meshes bring their warning back, and the banner is in the scene', () => {
  const mesh = readFileSync(new URL('../../components/viewer3d/ApiMotorMesh.tsx', import.meta.url), 'utf8');
  assert.equal((mesh.match(/splitWarnings\(/g) || []).length, 2);
  assert.match(mesh, /warningCache\.get\(`ext:\$\{geometryKey\}`\)/);
  assert.match(mesh, /warningCache\.get\(`2d:\$\{geometryKey\}`\)/);
  const scene = readFileSync(new URL('../../components/viewer3d/MotorScene.tsx', import.meta.url), 'utf8');
  assert.match(scene, /<GeometryWarningBanner \/>/);
  assert.match(scene, /data-testid="geometry-warning"/);
});
