import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const rd = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
const en = JSON.parse(rd('../../locales/en/simulation.json'));
const zh = JSON.parse(rd('../../locales/zh-CN/simulation.json'));
const SRC = ['../../components/propellers/DesignerPage.tsx', '../../components/propellers/DesignerParts.tsx', '../../components/propellers/PropellersPanel.tsx'].map(rd).join('\n');
const LIB = rd('../propellerDesign.ts');

test('EN and zh-CN simulation.json keep the same key set', () => {
  assert.deepEqual(Object.keys(en).sort(), Object.keys(zh).sort());
});

test('every static t(...) key of the Propellers page exists in both locales', () => {
  const keys = new Set([...SRC.matchAll(/\bt\(\s*'(prop[A-Za-z0-9_]+)'/g)].map((m) => m[1]));
  for (const k of [...LIB.matchAll(/'(propDesignGroup[A-Za-z]+)'/g)].map((m) => m[1])) keys.add(k);
  for (const k of ['propDesignDiameter', 'propDesignBlades', 'propDesignMotor']) keys.add(k);
  assert.ok(keys.size > 40, 'found ' + keys.size);
  for (const k of keys) { assert.ok(k in en, 'en ' + k); assert.ok(k in zh, 'zh ' + k); }
  // the table headers are listed as strings in arrays
  for (const k of [...SRC.matchAll(/'(prop(?:Zone|Run)[A-Za-z]*)'/g)].map((m) => m[1])) { assert.ok(k in en, 'en ' + k); assert.ok(k in zh, 'zh ' + k); }
});

test('the dynamic key families have an entry for every value the backend can send', () => {
  const fam = {
    propFlag_: ['root_flow_not_validated', 'design_inputs_unconfirmed', 'manufacturing_limits_unconfirmed', 'airfoil_choice_not_resolved_by_polar', 'high_tip_mach', 'rpm_capped'],
    propFlagTip_: ['root_flow_not_validated', 'design_inputs_unconfirmed', 'manufacturing_limits_unconfirmed', 'airfoil_choice_not_resolved_by_polar', 'high_tip_mach', 'rpm_capped'],
    propConstraint_: ['torque_within_limit', 'tip_mach_within_limit', 'bemt_converged'],
    propZone_: ['root', 'mid', 'tip'],
    propLimit_: ['wall_min_mm', 'thickness_ratio_min', 'le_radius_min_mm', 'te_thickness_min_mm'],
  };
  for (const [pfx, vals] of Object.entries(fam)) for (const v of vals) { assert.ok(pfx + v in en, pfx + v); assert.ok(pfx + v in zh, pfx + v); }
  for (const k of ['propDesignGroupInner', 'propDesignGroupOuter', 'propDesignGroupAirfoil', 'propDesignGroupTopology']) assert.ok(k in en && k in zh);
  for (const q of ['measured', 'powerEstimated', 'geometryOnly']) assert.ok(`propellersQuality_${q}` in en && `propellersQuality_${q}Tip` in zh);
});

test('the page is the Designer tab of Simulations > Propellers and says what the numbers are', () => {
  const panel = rd('../../components/propellers/PropellersPanel.tsx');
  assert.match(panel, /<DesignerPage \/>/);
  assert.match(panel, /id: 'designer'/);
  const page = rd('../../components/propellers/DesignerPage.tsx');
  assert.match(page, /data-testid="prop-banner"/);
  assert.match(page, /api\.evaluate\(vector, op\)/);
  assert.match(en.propDesignBanner, /not validated/);
  assert.match(en.propFlagTip_root_flow_not_validated, /anemometer/);
});

test('no caption or helper lines on the page (AGENTS.md UI text rules)', () => {
  assert.doesNotMatch(SRC, /variant="caption"|helperText=|FormHelperText/);
});

test('a run is added by a deliberate press, never by typing', () => {
  const page = rd('../../components/propellers/DesignerPage.tsx');
  assert.match(page, /onClick=\{onEvaluate\}/);
  assert.equal((page.match(/addRun\(/g) || []).length, 1);          // the one call, inside onEvaluate
});
