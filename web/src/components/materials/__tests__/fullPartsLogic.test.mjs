import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  formatMass, groupChoices, isEditable, mergeOverlay, overlayKey, rowTag,
  rowsForMode, scaledMass,
} from '../fullPartsLogic.ts';
import { SCOPES, parseScope } from '../../../lib/constructionScope.ts';

const row = (key, em, over = {}) => ({
  key, name: key, role: 'structural', em, material_id: em ? '20SW1200' : 'Aluminium_6061',
  default_material_id: em ? null : 'Aluminium_6061', user_set: false, mass_kg: 0.01, counted: em,
  status: 'computed', basis: '', ...over,
});
const ROWS = [row('holder_front', false), row('stator_core', true), row('screws', false, { material_id: 'Stainless_A2_70', default_material_id: 'Stainless_A2_70' }),
  row('magnet', true)];

test('the mode switch is the shared construction scope: exactly Active and Full, Active by default', () => {
  assert.deepEqual(SCOPES.map((m) => m.id), ['active', 'full']);
  assert.equal(parseScope(undefined), 'active');
  assert.equal(parseScope(null), 'active');
  assert.equal(parseScope('garbage'), 'active');
  assert.equal(parseScope('full'), 'full');
});

test('Active shows only the electromagnetic parts, Full shows the whole motor with the EM parts first', () => {
  assert.deepEqual(rowsForMode(ROWS, 'active').map((r) => r.key), ['stator_core', 'magnet']);
  assert.deepEqual(rowsForMode(ROWS, 'full').map((r) => r.key), ['stator_core', 'magnet', 'holder_front', 'screws']);
  assert.equal(rowsForMode(ROWS, 'full').length, ROWS.length);
});

test('only a part the EM simulation does not use can be re-assigned in Full', () => {
  assert.equal(isEditable(row('x', true)), false);
  assert.equal(isEditable(row('x', false)), true);
  assert.equal(rowTag(row('x', true)), 'em');
  assert.equal(rowTag(row('x', false)), 'notEm');
});

test('a client overlay touches non-active parts only and never the active assignment', () => {
  const dens = (id) => ({ Aluminium_6061: 2700, Aluminium_7075: 2810, '20SW1200': 7650, copper: 8890 })[id] ?? null;
  const out = mergeOverlay(ROWS, { holder_front: 'Aluminium_7075', stator_core: 'copper' }, dens);
  const by = Object.fromEntries(out.map((r) => [r.key, r]));
  assert.equal(by.holder_front.material_id, 'Aluminium_7075');
  assert.equal(by.holder_front.user_set, true);
  assert.ok(Math.abs(by.holder_front.mass_kg - 0.01 * 2810 / 2700) < 1e-12);
  assert.equal(by.stator_core.material_id, '20SW1200');          // the EM part ignores the overlay
  assert.equal(by.stator_core.mass_kg, 0.01);
  assert.equal(by.screws.material_id, 'Stainless_A2_70');         // untouched
  // the choice equal to the default is not "user set"
  assert.equal(mergeOverlay(ROWS, { holder_front: 'Aluminium_6061' }, dens)[0].user_set, false);
  // without densities the mass is kept, never zeroed
  assert.equal(mergeOverlay(ROWS, { holder_front: 'Aluminium_7075' })[0].mass_kg, 0.01);
});

test('mass scales by density, an unknown mass stays unknown', () => {
  const dens = (id) => ({ a: 2, b: 4 })[id] ?? null;
  assert.equal(scaledMass(row('x', false, { material_id: 'a', mass_kg: 1 }), dens, 'b'), 2);
  assert.equal(scaledMass(row('x', false, { material_id: 'a', mass_kg: null }), dens, 'b'), null);
  assert.equal(scaledMass(row('x', false, { material_id: 'a', mass_kg: 1 }), dens, 'zzz'), 1);
});

test('the picker groups come in a fixed order and unknown groups fall into other', () => {
  const g = groupChoices([
    { id: 'z', name: 'z', group: 'insulator', origin: 'x', density_kg_m3: 1 },
    { id: 'b', name: 'b', group: 'metal', origin: 'x', density_kg_m3: 1 },
    { id: 'a', name: 'a', group: 'metal', origin: 'x', density_kg_m3: 1 },
    { id: 'q', name: 'q', group: 'weird', origin: 'x', density_kg_m3: 1 },
  ]);
  assert.deepEqual(g.map((x) => x.group), ['metal', 'insulator', 'other']);
  assert.deepEqual(g[0].items.map((c) => c.id), ['a', 'b']);
});

test('masses are shown in grams below a kilogram and a missing mass is a dash, never zero', () => {
  assert.equal(formatMass(null), '-');
  assert.equal(formatMass(undefined), '-');
  assert.equal(formatMass(0.00151), '1.51 g');
  assert.equal(formatMass(0.0917), '91.7 g');
  assert.equal(formatMass(0.3967), '396.7 g');
  assert.equal(formatMass(1.5), '1.500 kg');
  assert.equal(overlayKey('CIANO14 40 new'), 'materials.partOverlay.v1.CIANO14 40 new');
});

test('every Full-mode string is translated in EN and zh-CN', () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../../locales');
  const required = [
    'materialsModeMenu', 'materialsModeActive', 'materialsModeFull', 'materialsModeActiveTip', 'materialsModeFullTip',
    'materialsFullHint', 'materialsFullHintTip', 'materialsFullPart', 'materialsFullRole', 'materialsFullMaterial',
    'materialsFullMass', 'materialsFullEm', 'materialsFullEmTip', 'materialsFullNotEm', 'materialsFullNotEmTip',
    'materialsFullPickTitle', 'materialsFullDefaultTip', 'materialsFullChosenTip', 'materialsFullAssumedTip',
    'materialsFullTotal', 'materialsFullTotalTip', 'materialsFullNoMotor', 'materialsFullUnavailable', 'materialsFullNda',
    'materialsFullNoCad', 'materialsFullPartial', 'materialsFullPartialTip', 'materialsFullActive', 'materialsFullActiveTip', 'materialsFullLoading', 'materialsFullLocal', 'materialsGroupMetal', 'materialsGroupSteel',
    'materialsGroupMagnet', 'materialsGroupInsulator', 'materialsGroupOther',
    ...['Structural', 'Magnetic', 'Conductor', 'Insulation', 'Fastener', 'Electronic', 'Cell', 'Other'].map((r) => `materialsRole${r}`),
  ];
  for (const locale of ['en', 'zh-CN']) {
    const m = JSON.parse(readFileSync(resolve(root, locale, 'motors.json'), 'utf8'));
    for (const key of required) assert.equal(typeof m[key], 'string', `${locale}.${key}`);
  }
  const zh = JSON.parse(readFileSync(resolve(root, 'zh-CN', 'motors.json'), 'utf8'));
  const en = JSON.parse(readFileSync(resolve(root, 'en', 'motors.json'), 'utf8'));
  // every part name the server can send has a translated label (the panel falls back to the server's English name)
  for (const k of ['stator_core', 'rotor_core', 'magnet', 'slot', 'shaft', 'sleeve', 'slot_insulation', 'wire_insulation',
    'holder_front', 'holder_back', 'holder', 'carrier_front', 'carrier_rear', 'rings', 'prop_fix', 'bearings', 'screws', 'board', 'pins']) {
    assert.equal(typeof en[`materialsPart_${k}`], 'string', `en.materialsPart_${k}`);
    assert.match(zh[`materialsPart_${k}`], /[一-鿿]/, `zh.materialsPart_${k}`);
  }
  assert.match(zh.materialsModeFull, /[一-鿿]/);                  // really Chinese, not a copy of the English
  assert.match(zh.materialsFullNotEm, /[一-鿿]/);
});
