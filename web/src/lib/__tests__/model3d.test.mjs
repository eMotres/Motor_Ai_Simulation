// node --test - the pure helpers behind the 3D model tab (lib/model3d.ts): picker, part toggles, exploded view,
// section plane, measure, download names.  Imports the .ts itself (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

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

// -- what the tab shows: ONE flag (can_download) decides ---------------------------------------------

const TOOLS = ['parts', 'explode', 'section', 'measure'];

t('without NDA: the model, the picker, Fit, the note and the Sign button - no side panel, no tools, no download', () => {
  const p = L.panelParts(false);
  assert.deepEqual(p, ['viewer', 'motor', 'fit', 'signNote', 'signButton']);
  for (const x of [...TOOLS, 'download', 'yourNda', 'version']) assert.ok(!p.includes(x), x);
});

t('with NDA (and owner): the full panel plus the download, no sign note or button', () => {
  const p = L.panelParts(true);
  for (const x of ['viewer', 'motor', 'fit', ...TOOLS, 'download', 'yourNda', 'version']) assert.ok(p.includes(x), x);
  assert.ok(!p.includes('signNote') && !p.includes('signButton'));
});

t('the scene gets the plain model without NDA, whatever tool state is left over (a revoked NDA resets at once)', () => {
  const s = { hidden: new Set(['Coils']), explode: 0.7, section: { on: true, axis: 'y', position: 3 }, measure: true };
  assert.deepEqual(L.effectiveSettings(true, s), s);
  const plain = L.effectiveSettings(false, s);
  assert.equal(plain.hidden.size, 0);
  assert.equal(plain.explode, 0);
  assert.equal(plain.section.on, false);
  assert.equal(plain.measure, false);
  assert.deepEqual(s.hidden, new Set(['Coils']));                       // the input is not mutated
});

t('no "Envelope" wording is left in the tab, its viewer, its locale files or its library', () => {
  const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
  for (const f of ['../../components/model3d/Model3DPanel.tsx', '../../components/model3d/modelViewer.ts', '../model3d.ts',
    '../../locales/en/model3d.json', '../../locales/zh-CN/model3d.json']) {
    assert.ok(!/envelope/i.test(read(f)), f);
  }
});

// -- the tab opens on the motor open in Configure (client-view batch 2026-10-14) ------------
t('resolveModel: the open motor\'s model, or "in preparation" - never another motor\'s canvas', () => {
  const built = [{ die: 'CIANO14 40 new', config: 'L12' }, { die: 'CIANO14 40 new', config: 'L20' }];
  assert.deepEqual(L.resolveModel(built, null, { die: 'CIANO14 40 new', config: 'L20' }),
    { model: built[1], preparing: false });
  // CIANO28 85 L13 has no built model
  assert.deepEqual(L.resolveModel(built, null, { die: 'CIANO28 85 20SW1200', config: 'L13' }),
    { model: null, preparing: true });
  // no motor open: remembered, else first (unchanged behaviour)
  assert.equal(L.resolveModel(built, L.modelKey(built[1]), null).model, built[1]);
  assert.equal(L.resolveModel(built, null, null).model, built[0]);
  // nothing built at all: the "none" line, not "in preparation"
  assert.deepEqual(L.resolveModel([], null, { die: 'A', config: 'B' }), { model: null, preparing: false });
});

t('every key the 3D model tab prints exists in en and zh-CN (no raw "downloadLocked" / "nda.sign")', () => {
  const dir = new URL('../../', import.meta.url);
  const read = (p) => readFileSync(new URL(p, dir), 'utf8');
  const flat = (o, pre = '') => Object.entries(o).flatMap(([k, v]) => (v && typeof v === 'object' ? flat(v, `${pre}${k}.`) : [`${pre}${k}`]));
  const en = new Set(flat(JSON.parse(read('locales/en/model3d.json'))));
  const zh = new Set(flat(JSON.parse(read('locales/zh-CN/model3d.json'))));
  const used = new Set();
  for (const f of ['components/model3d/Model3DPanel.tsx', 'components/model3d/NdaDialog.tsx'])
    for (const m of read(f).matchAll(/\bt\('([A-Za-z0-9_.]+)'/g)) used.add(m[1]);
  assert.ok(used.has('downloadLocked') && used.has('nda.sign') && used.has('inPreparation'));
  for (const k of used) {
    assert.ok(en.has(k), `en/model3d.json lacks ${k}`);
    assert.ok(zh.has(k), `zh-CN/model3d.json lacks ${k}`);
  }
});
