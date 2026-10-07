import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  catalogKindForMaterialsView,
  MATERIALS_CATALOG_VIEWS,
} from '../materialsCatalogViews.ts';

test('materials menu exposes the five requested read-only catalogue views', () => {
  assert.deepEqual(MATERIALS_CATALOG_VIEWS.map((item) => item.id), [
    'materials', 'bearing', 'lubricant', 'wire', 'device',
  ]);
  assert.equal(catalogKindForMaterialsView('materials'), null);
  assert.equal(catalogKindForMaterialsView('wire'), null);
  assert.equal(catalogKindForMaterialsView('bearing'), 'bearing');
  assert.equal(catalogKindForMaterialsView('lubricant'), 'lubricant');
  assert.equal(catalogKindForMaterialsView('device'), 'device');
});

test('every materials menu label and catalogue hint is translated in EN and zh-CN', () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../../locales');
  const required = [
    'materialsViewMenu', 'materialsViewMaterials', 'materialsViewBearings',
    'materialsViewLubricants', 'materialsViewWireStock',
    'materialsViewPowerDevices', 'materialsViewCatalogHelp',
  ];
  for (const locale of ['en', 'zh-CN']) {
    const messages = JSON.parse(readFileSync(resolve(root, locale, 'motors.json'), 'utf8'));
    for (const key of required) assert.equal(typeof messages[key], 'string', `${locale}.${key}`);
  }
});
