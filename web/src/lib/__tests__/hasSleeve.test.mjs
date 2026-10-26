// A machine with no retaining sleeve says nothing about one (owner 2026-10-26): the web mirror of motor_ai_sim/sleeve.py.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { hasSleeve, sleeveThicknessMm, withSleevePart } from '../hasSleeve.ts';

test('thickness decides; a material assignment alone is not a sleeve', () => {
  assert.equal(hasSleeve(1.2), true);
  assert.equal(hasSleeve('0.5'), true);
  assert.equal(hasSleeve({ sleeve_thickness: 2 }), true);
  assert.equal(hasSleeve({ geometry: { sleeve_thickness_mm: 0.8 } }), true);
  for (const none of [0, 0.0, -1, '0', '', null, undefined, true, Number.NaN, {}, { sleeve_thickness: 0, materials: { sleeve: 'M40X_UD_60' } }]) {
    assert.equal(hasSleeve(none), false, String(JSON.stringify(none)));
  }
  assert.equal(sleeveThicknessMm({ sleeve_thickness: '1.5' }), 1.5);
  assert.equal(sleeveThicknessMm({}), null);
});

test('a solved result says so when it carries no geometry', () => {
  assert.equal(hasSleeve({ has_sleeve: true }), true);
  assert.equal(hasSleeve({ has_sleeve: false, parts: { sleeve: {} } }), false);
  assert.equal(hasSleeve({ parts: { sleeve: { hoop_max_mpa: 300 } } }), true);
  assert.equal(hasSleeve({ parts: { magnet: {} } }), false);
});

test('materials list row: the sleeve row exists only with a sleeve, however the assignment reads', () => {
  const parts = [{ key: 'stator_core' }, { key: 'magnet' }];
  const sleeve = { key: 'sleeve' };
  assert.deepEqual(withSleevePart(parts, sleeve, 0).map((p) => p.key), ['stator_core', 'magnet']);
  assert.deepEqual(withSleevePart(parts, sleeve, undefined).map((p) => p.key), ['stator_core', 'magnet']);
  assert.deepEqual(withSleevePart(parts, sleeve, 1.2).map((p) => p.key), ['stator_core', 'magnet', 'sleeve']);
});

test('wiring: the materials panel and the Compare mechanical row ask the helper, not the assignment', () => {
  const src = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
  const panel = src('../../components/materials/MotorAssignmentPanel.tsx');
  assert.match(panel, /withSleevePart\(PARTS, SLEEVE_PART, sleeveT\)/);
  assert.doesNotMatch(panel, /sleeveAssigned/);
  const rows = src('../../components/compare/resultRows.ts');
  assert.match(rows, /if \(withSleeve\) put\(out, 'sleeve_mat'/);
  assert.match(rows, /if \(withSleeve\) \{\s*put\(out, 'sleeve_c'/);
});

test('a run summary without geometry is read by its own sleeve signals (mirror of sleeve.py)', () => {
  assert.equal(hasSleeve({ mass_components: [{ name: 'Sleeve (CFRP)', mass_kg: 0.02 }] }), true);
  assert.equal(hasSleeve({ mass_components: [{ name: 'Sleeve', mass_kg: 0 }, { name: 'Magnets', mass_kg: 0.1 }] }), false);
  assert.equal(hasSleeve({ rotor_inertia: { sleeve: 1e-5 } }), true);
  assert.equal(hasSleeve({ rotor_inertia: { sleeve: 0 } }), false);
  assert.equal(hasSleeve({ P_sleeve_W: 0.0012 }), true);
  assert.equal(hasSleeve({ P_sleeve_W: 0 }), false);
  assert.equal(hasSleeve({ sleeve_hoop: { sigma_hoop_MPa: 400 } }), true);
  assert.equal(hasSleeve({ rotor_stress: { parts: { sleeve: {} } } }), true);
  assert.equal(hasSleeve({}), false);
});

test('wiring: the thermal tiles, the mechanical temperature badge and the sleeve-loss tile ask the helper', () => {
  const src = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
  assert.match(src('../../components/thermal/ThermalPanel.tsx'), /p\.key !== 'sleeve' \|\| sleeved/);
  assert.match(src('../../components/mechanical/MechanicalPanel.tsx'), /tempBadge\(res\.thermal, res\.has_sleeve \?\? hasSleeveGeo\)/);
  assert.match(src('../emResultTiles.ts'), /\.\.\.\(hasSleeve\(s\) \? \[tile\(\{ id: 'sleeveLoss'/);
});
