import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
let M;
try { M = await import('../internal/model3d.ts'); } catch { /* older Node */ }
let C;
try { C = await import('../internal/navigationCubeMath.ts'); } catch { /* older Node */ }
const t = M ? test : test.skip;
const cubeTest = C ? test : test.skip;

t('section plane keeps the lower side of each selected coordinate axis', () => {
  assert.deepEqual(M.sectionPlane('x', 3), { normal: [-1, 0, 0], constant: 3 });
  assert.deepEqual(M.sectionPlane('y', -2), { normal: [0, -1, 0], constant: -2 });
  assert.deepEqual(M.sectionPlane('z', 8), { normal: [0, 0, -1], constant: 8 });
  const r = { min: [-10, -20, -30], max: [10, 20, 30] };
  assert.deepEqual(M.sectionRange(r, 'x'), { min: -10.1, max: 10.1 });
  assert.deepEqual(M.sectionRange(r, 'z'), { min: -30.3, max: 30.3 });
});

t('view cube faces map to six unit CAD directions', () => {
  const expected = { front: [0, -1, 0], back: [0, 1, 0], left: [-1, 0, 0], right: [1, 0, 0], top: [0, 0, 1], bottom: [0, 0, -1] };
  for (const [view, dir] of Object.entries(expected)) assert.deepEqual(M.standardViewDirection(view), dir);
});

t('all twelve cube edges map to normalized diagonal directions', () => {
  const edges = ['frontTop', 'frontBottom', 'backTop', 'backBottom', 'leftTop', 'leftBottom', 'rightTop', 'rightBottom',
    'frontLeft', 'frontRight', 'backLeft', 'backRight'];
  assert.equal(edges.length, 12);
  for (const view of edges) {
    const d = M.standardViewDirection(view);
    assert.ok(Math.abs(Math.hypot(...d) - 1) < 1e-12, view);
    assert.equal(d.filter((v) => Math.abs(v) > 0).length, 2, view);
  }
});

cubeTest('cube projection exposes 6 face and 12 edge mappings across representative views', () => {
  const samples = [[0, -1, 0], [0, 0, 1], [1, -1, 1]];
  const faces = new Set();
  const edges = new Set();
  for (const direction of samples) {
    const projected = C.projectNavigationCube(direction);
    assert.equal(projected.edges.length, 12);
    for (const face of projected.faces) faces.add(face.view);
    for (const edge of projected.edges) edges.add(edge.view);
  }
  assert.equal(edges.size, 12);
  // Opposite axial samples complete the six face mappings without relying on tangent, zero-area faces.
  for (const direction of [[0, 1, 0], [0, 0, -1], [-1, 1, -1]]) {
    for (const face of C.projectNavigationCube(direction).faces) faces.add(face.view);
  }
  assert.equal(faces.size, 6);
});

cubeTest('front and top projections mark only the frontmost coincident edges pointer-visible', () => {
  const front = C.projectNavigationCube([0, -1, 0]);
  assert.deepEqual(front.faces.map((f) => f.view), ['front']);
  assert.equal(front.edges.find((e) => e.view === 'frontTop').visible, true);
  assert.equal(front.edges.find((e) => e.view === 'backTop').visible, false);
  const top = C.projectNavigationCube([0, 0, 1]);
  assert.deepEqual(top.faces.map((f) => f.view), ['top']);
  assert.ok(top.edges.filter((e) => e.visible).every((e) => e.view.endsWith('Top') || e.view.startsWith('front') || e.view.startsWith('back')));
});

cubeTest('camera screen-up preserves roll and produces finite projected vertices', () => {
  const direction = [0, -1, 0];
  const zUp = C.projectNavigationCube(direction, [0, 0, 1]);
  const xUp = C.projectNavigationCube(direction, [1, 0, 0]);
  assert.notDeepEqual(zUp.faces[0].points, xUp.faces[0].points);
  for (const projection of [zUp, xUp]) {
    for (const face of projection.faces) for (const point of face.points) {
      assert.equal(Number.isFinite(point[0]) && Number.isFinite(point[1]), true);
    }
    for (const edge of projection.edges) for (const point of [edge.from, edge.to]) {
      assert.equal(Number.isFinite(point[0]) && Number.isFinite(point[1]), true);
    }
  }
});

t('explosion shift clamps the shared slider and preserves per-group factors', () => {
  assert.equal(M.explodeShift(2, 12, 0), 0);
  assert.equal(M.explodeShift(2, 12, 0.5), 12);
  assert.equal(M.explodeShift(-1.5, 20, 1), -30);
  assert.equal(M.explodeShift(2, 12, 7), 24);
  assert.equal(M.explodeShift(2, 12, -3), 0);
});

t('measurement distance and millimetre formatting retain precision', () => {
  assert.equal(M.distance([0, 0, 0], [3, 4, 12]), 13);
  assert.equal(M.formatMm(12.34567), '12.346 mm');
  assert.equal(M.formatMm(123.456), '123.46 mm');
});

t('simplified effective scene settings remove full-access tools without mutating source state', () => {
  const s = { hidden: new Set(['Coils']), explode: 0.7, section: { on: true, axis: 'y', position: 3 }, measure: true };
  assert.deepEqual(M.effectiveSettings(true, s), { ...s, hints: true });
  const plain = M.effectiveSettings(false, s);
  assert.equal(plain.hidden.size, 0); assert.equal(plain.explode, 0);
  assert.equal(plain.section.on, false); assert.equal(plain.measure, false); assert.equal(plain.hints, false);
  assert.deepEqual(s.hidden, new Set(['Coils']));
});

t('all shared cube, projection, section, and tree labels have English and Chinese translations', () => {
  const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
  const read = (lang, ns) => JSON.parse(readFileSync(join(root, 'locales', lang, `${ns}.json`), 'utf8'));
  const enModel = read('en', 'model3d'); const zhModel = read('zh-CN', 'model3d');
  for (const key of ['home', 'sectionOffset', 'orthographic', 'perspective']) assert.ok(enModel[key] && zhModel[key], key);
  const views = ['title', 'front', 'back', 'left', 'right', 'top', 'bottom', 'frontTop', 'frontBottom', 'backTop', 'backBottom',
    'leftTop', 'leftBottom', 'rightTop', 'rightBottom', 'frontLeft', 'frontRight', 'backLeft', 'backRight'];
  for (const key of views) assert.ok(enModel.cube[key] && zhModel.cube[key], `cube.${key}`);
  const enTree = read('en', 'geometry'); const zhTree = read('zh-CN', 'geometry');
  for (const key of ['assemblyHide', 'assemblyShow', 'assemblyHideAll', 'assemblyCollapse', 'assemblyExpand', 'assemblyAbsent'])
    assert.ok(enTree[key] && zhTree[key], key);
  for (const key of ['stator', 'rotor', 'coils', 'coil', 'magnets', 'magnet', 'shaft', 'sleeve', 'insulation', 'enamel', 'inBand', 'outBand'])
    assert.ok(enTree.activeTree[key] && zhTree.activeTree[key], `activeTree.${key}`);
  for (const key of ['included', 'reference', 'excluded', 'includedTip', 'referenceTip', 'excludedTip', 'shaftAlways', 'excludedRisk', 'nextExcludedRisk'])
    assert.ok(enTree.partState[key] && zhTree.partState[key], `partState.${key}`);
});
