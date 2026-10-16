import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const L = await import('../model3d.ts');

const models = [
  { die: 'CIANO14 40 new', config: 'L12', motor_id: 'a' },
  { die: 'CIANO14 40 new', config: 'L20', motor_id: 'b' },
];

test('host model picker and route resolution use the remembered or configured model', () => {
  assert.equal(L.pickModel(models, L.modelKey(models[1])).config, 'L20');
  assert.equal(L.pickModel(models, 'gone\u0000X').config, 'L12');
  assert.equal(L.pickModel([], 'x'), null);
  assert.equal(L.modelLabel(models[0]), 'CIANO14 40 new · L12');
  assert.deepEqual(L.resolveModel(models, null, { die: models[1].die, config: models[1].config }), { model: models[1], preparing: false });
  assert.deepEqual(L.resolveModel(models, null, { die: 'missing', config: 'L13' }), { model: null, preparing: true });
});

test('download names and sizes are host helpers', () => {
  const fb = 'fallback.step';
  assert.equal(L.filenameFromDisposition('attachment; filename="CIANO14_L12.step"', fb), 'CIANO14_L12.step');
  assert.equal(L.filenameFromDisposition('attachment; filename="../../evil.step"', fb), fb);
  assert.equal(L.filenameFromDisposition(null, fb), fb);
  assert.equal(L.filenameFromDisposition('attachment', fb), fb);
  assert.equal(L.formatBytes(4647560), '4.4 MB');
  assert.equal(L.formatBytes(null), '');
});

test('the model viewer copy avoids obsolete Envelope terminology', () => {
  const root = new URL('../../', import.meta.url);
  const read = (p) => readFileSync(new URL(p, root), 'utf8');
  for (const file of ['components/model3d/Model3DPanel.tsx', 'modules/viewer3d/internal/modelViewer.ts', 'lib/model3d.ts',
    'locales/en/model3d.json', 'locales/zh-CN/model3d.json']) assert.ok(!/envelope/i.test(read(file)), file);
});

test('model tab locale strings exist in English and Chinese', () => {
  const root = new URL('../../', import.meta.url);
  const read = (p) => JSON.parse(readFileSync(new URL(p, root), 'utf8'));
  for (const lang of ['en', 'zh-CN']) {
    const locale = read(`locales/${lang}/model3d.json`);
    assert.ok(locale.downloadLocked && locale.nda && locale.cube);
  }
});

test('every literal model tab translation key remains present in EN and zh-CN', () => {
  const root = new URL('../../', import.meta.url);
  const read = (p) => readFileSync(new URL(p, root), 'utf8');
  const flatten = (o, prefix='') => Object.entries(o).flatMap(([k,v]) => v && typeof v === 'object' ? flatten(v, `${prefix}${k}.`) : [`${prefix}${k}`]);
  const locales = Object.fromEntries(['en','zh-CN'].map(lang => [lang,new Set(flatten(JSON.parse(read(`locales/${lang}/model3d.json`))))]));
  const used = new Set();
  for (const file of ['components/model3d/Model3DPanel.tsx','components/model3d/NdaDialog.tsx'])
    for (const match of read(file).matchAll(/\bt\('([A-Za-z0-9_.]+)'/g)) used.add(match[1]);
  for (const key of used) for (const lang of ['en','zh-CN']) assert.ok(locales[lang].has(key), `${lang}/model3d.json lacks ${key}`);
});
