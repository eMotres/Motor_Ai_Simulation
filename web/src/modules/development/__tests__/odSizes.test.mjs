// node --test: the outer-diameter choice of the specification (4_1, owner 2026-11-25).  Made-up values.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { acceptedOf, defaultAccepted, odModeOf, preferredBounds, sizeKey, sizeText, toggleSize } from '../odSizes.ts';
import { missingAnyGroups, propertyRequired } from '../wizardState.ts';
import { formatDiffLabel, semanticSpecificationDiff } from '../specificationDiff.ts';

const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const schema = JSON.parse(read('../../../../../src/motor_ai_sim/development/schema.json'));
const en = JSON.parse(read('../../../locales/en/development.json'));
const zh = JSON.parse(read('../../../locales/zh-CN/development.json'));
const f41 = schema.sections.flatMap((s) => s.fields).find((f) => f.id === '4_1');
const tr = (key, args = {}) => (en.odSizes[key.replace('odSizes.', '')] ?? key).replace(/\{(\w+)\}/g, (_, k) => String(args[k] ?? ''));

test('a stored answer without the choice reads as custom', () => {
  assert.equal(odModeOf({ outer_diameter_mm: 80 }), 'custom');
  assert.equal(odModeOf(undefined), 'custom');
  assert.equal(odModeOf({ od_mode: 'existing' }), 'existing');
  assert.equal(odModeOf({ od_mode: 'fixed' }), 'custom');
});

test('default selection, toggling and the stored shape', () => {
  const sizes = [{ od_mm: 80, slots: 12, poles: 14, stack_mm: [10, 15] }, { od_mm: 120, slots: 24, poles: 20, stack_mm: [] }];
  assert.deepEqual(defaultAccepted(sizes, 100), [{ od_mm: 80, slots: 12, poles: 14 }]);
  assert.equal(defaultAccepted(sizes).length, 2);
  const one = toggleSize([], { od_mm: 80, slots: 12, poles: 14 }, true);
  assert.deepEqual(toggleSize(one, { od_mm: 80, slots: 12, poles: 14 }, false), []);
  assert.deepEqual(acceptedOf([{ od_mm: 80, slots: 12, poles: 14 }, { od_mm: 'x' }, null]), [{ od_mm: 80, slots: 12, poles: 14 }]);
  assert.equal(sizeKey({ od_mm: 80, slots: 12, poles: 14 }), '80|12|14');
  assert.deepEqual(preferredBounds({ minimum: 70, maximum: 90 }), { min: 70, max: 90 });
  assert.deepEqual(preferredBounds(75), { min: 75, max: 75 });
  assert.match(sizeText(sizes[0], tr), /80 mm, 12 slots \/ 14 poles, stack 10, 15 mm/);
  assert.doesNotMatch(sizeText(sizes[1], tr), /stack/);
});

test('completeness: custom needs a diameter or length, existing needs ticked sizes', () => {
  assert.equal(missingAnyGroups(f41, { outer_diameter_mm: 90 }).length, 0);                 // old stored answer, no od_mode
  assert.equal(missingAnyGroups(f41, {}).length, 1);
  assert.equal(missingAnyGroups(f41, { od_mode: 'custom', od_accepted_sizes: [{ od_mm: 80, slots: 12, poles: 14 }] }).length, 1);   // stale sizes do not count
  assert.equal(missingAnyGroups(f41, { od_mode: 'existing', od_accepted_sizes: [{ od_mm: 80, slots: 12, poles: 14 }] }).length, 0);
  const sizes = f41.properties.find((p) => p.id === 'od_accepted_sizes');
  assert.equal(propertyRequired(sizes, { od_mode: 'existing' }), true);
  assert.equal(propertyRequired(sizes, { od_mode: 'custom' }), false);
  assert.equal(propertyRequired(sizes, {}), false);
});

test('the spec diff names the choice and the sizes', () => {
  const before = { schema_version: 2, fields: { '4_1': { status: 'filled', value: { outer_diameter_mm: 90 } } } };
  const after = { schema_version: 2, fields: { '4_1': { status: 'filled', value: { outer_diameter_mm: 90, od_mode: 'existing', od_accepted_sizes: [{ od_mm: 80, slots: 12, poles: 14 }] } } } };
  const diff = semanticSpecificationDiff(before, after, { before: schema, after: schema });
  assert.ok(diff.some((e) => e.fieldId === '4_1' && e.path.includes('od_mode') && e.after === 'existing'), JSON.stringify(diff));
  assert.ok(diff.some((e) => e.path.includes('od_accepted_sizes')));
  assert.match(formatDiffLabel(diff.find((e) => e.path.includes('od_mode')), schema, 'en'), /Outer diameter/);
});

test('EN and zh-CN carry the same odSizes keys and the schema labels', () => {
  assert.deepEqual(Object.keys(en.odSizes).sort(), Object.keys(zh.odSizes).sort());
  for (const p of f41.properties.filter((q) => ['od_mode', 'od_preferred_mm', 'od_accepted_sizes'].includes(q.id))) {
    assert.ok(p.label.en && p.label['zh-CN'], p.id);
    for (const o of p.options ?? []) assert.ok(o.label.en && o.label['zh-CN']);
  }
  const text = JSON.stringify([en.odSizes, f41]);
  assert.doesNotMatch(text, /\b(prices?|costs?|quotes?|budget|days?)\b/i);          // no money or time words
});
