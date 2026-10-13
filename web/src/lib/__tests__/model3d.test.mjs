// node --test - the pure helpers behind the 3D model tab (lib/model3d.ts): picker, part toggles, exploded view,
// section plane, measure, download names.  Imports the .ts itself (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let L = null;
try { L = await import('../model3d.ts'); } catch { /* old Node */ }
const t = L ? test : test.skip;

const models = [
  { die: 'CIANO14 40 new', config: 'L12', motor_id: 'a' },
  { die: 'CIANO14 40 new', config: 'L20', motor_id: 'b' },
];

t('the picker opens on the remembered model when it is still offered, else the first, else nothing', () => {
  assert.equal(L.pickModel(models, L.modelKey(models[1])).config, 'L20');
  assert.equal(L.pickModel(models, 'gone\u0000X').config, 'L12');
  assert.equal(L.pickModel(models, null).config, 'L12');
  assert.equal(L.pickModel([], 'x'), null);
  assert.equal(L.modelLabel(models[0]), 'CIANO14 40 new · L12');
});

t('part toggles group nodes by family in file order, and a hidden family hides all its nodes', () => {
  const groups = [
    { name: 'Core', family: 'Core', explode: 0 },
    { name: 'PhaseBoard_FR4', family: 'PhaseBoard', explode: -2.2 },
    { name: 'PhaseBoard_copper', family: 'PhaseBoard', explode: -2.2 },
    { name: 'Shaft', family: 'Shaft', explode: 2.2 },
  ];
  const rows = L.togglesOf(groups);
  assert.deepEqual(rows.map((r) => r.family), ['Core', 'PhaseBoard', 'Shaft']);
  assert.deepEqual(rows[1].nodes, ['PhaseBoard_FR4', 'PhaseBoard_copper']);
  const hidden = new Set(['PhaseBoard']);
  assert.deepEqual(groups.filter((g) => L.isVisible(hidden, g)).map((g) => g.name), ['Core', 'Shaft']);
});

t('exploded view: factor x motor length x slider, slider clamped to 0..1', () => {
  assert.equal(L.explodeShift(2, 12, 0), 0);
  assert.equal(L.explodeShift(2, 12, 0.5), 12);
  assert.equal(L.explodeShift(-1.5, 20, 1), -30);
  assert.equal(L.explodeShift(2, 12, 7), 24);
  assert.equal(L.explodeShift(2, 12, -3), 0);
});

t('section plane keeps the half-space axis < position and its range covers the box', () => {
  const p = L.sectionPlane('z', 3);
  assert.deepEqual(p.normal, [0, 0, -1]);
  // three.js: distance = n.x + c; a point at z = 2 is kept (>= 0), z = 5 is clipped (< 0)
  const dist = (z) => p.normal[2] * z + p.constant;
  assert.ok(dist(2) > 0 && dist(5) < 0);
  assert.deepEqual(L.sectionPlane('x', 1).normal, [-1, 0, 0]);
  assert.deepEqual(L.sectionPlane('y', 1).normal, [0, -1, 0]);
  const box = { min: [-20, -20, -17], max: [20, 20, 33] };
  const r = L.sectionRange(box, 'z');
  assert.ok(r.min < -17 && r.max > 33 && r.max - r.min < 51);
});

t('measure: distance, formatting and the box of a point list', () => {
  assert.equal(L.distance([0, 0, 0], [3, 4, 12]), 13);
  assert.equal(L.formatMm(12.34567), '12.346 mm');
  assert.equal(L.formatMm(123.456), '123.46 mm');
  const b = L.boxOf([[1, 2, 3], [-1, 5, 0]]);
  assert.deepEqual(b, { min: [-1, 2, 0], max: [1, 5, 3] });
});

t('download names come from the server stamp; a hostile or missing header falls back', () => {
  const fb = 'fallback.step';
  assert.equal(L.filenameFromDisposition('attachment; filename="CIANO14_40_new_L12_ann-muller_20261013.step"', fb),
    'CIANO14_40_new_L12_ann-muller_20261013.step');
  assert.equal(L.filenameFromDisposition(null, fb), fb);
  assert.equal(L.filenameFromDisposition('attachment; filename="../../evil.step"', fb), fb);
  assert.equal(L.filenameFromDisposition('attachment', fb), fb);
  assert.equal(L.formatBytes(4647560), '4.4 MB');
  assert.equal(L.formatBytes(2048), '2 kB');
  assert.equal(L.formatBytes(null), '');
});
