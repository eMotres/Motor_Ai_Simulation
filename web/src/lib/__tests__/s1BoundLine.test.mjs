// node --test - a side S1 limit that is a BOUND still gets its line on the load chart (owner 2026-10-19: «для этого
// пропеллера нет ограничения S1 на графике», 40 L20 + P13x4.4: limit_motor {rpm 9091.5, speed_range, lower_bound}).
// Imports lib/configuratorPropellerS1.ts (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

let S = null;
try { S = await import('../configuratorPropellerS1.ts'); } catch { /* old Node */ }
const t = S ? test : test.skip;
const panel = readFileSync(new URL('../../components/compare/ConfiguratorPanel.tsx', import.meta.url), 'utf8');
const chart = readFileSync(new URL('../../components/compare/PropellerLoadChart.tsx', import.meta.url), 'utf8');

t('why a bound ends: 1.5 x the propeller\'s top tested speed, else the passport speed range', () => {
  // T-Motor P13x4.4: tested to 6061 rpm -> the scan stops at 9091.5
  assert.equal(S.s1BoundEnd(9091.5, { limit: null, rpm_range_tested: [3400, 6061], limits: { rpm_max: 45000 } }), 'propeller_data');
  assert.equal(S.s1BoundEnd(22495, { limit: null, rpm_range_tested: [3000, 20000], limits: { rpm_max: 22495 } }), 'motor_speed_grid');
  assert.equal(S.s1BoundEnd(5000, { limit: { range_end: 'requested' }, rpm_range_tested: null, limits: {} }), 'requested');
  assert.equal(S.s1RangeEndKey(S.s1BoundEnd(9091.5, { limit: null, rpm_range_tested: [3400, 6061], limits: {} })),
    'configurePropeller.s1EndProp');
});

t('the panel draws the motor line for a bound too, labelled as a bound; the supply line likewise', () => {
  assert.match(panel, /const s1MotorRpm = s1Motor \? s1Motor\.rpm :/);                 // no `!lower_bound` filter any more
  assert.match(panel, /s1MotorLabel = s1Motor && s1MotorBound \? boundLabel\('propLoad\.motorBoundLabel', s1Motor\.rpm\)/);
  assert.match(panel, /s1SupplyLabel = s1Supply && s1SupplyBound \? boundLabel\('propLoad\.supplyBoundLabel', s1Supply\.rpm\)/);
  assert.match(panel, /s1Rpm=\{s1MotorRpm\} s1Label=\{s1MotorLabel\}/);
  assert.match(chart, /value: s1Label \?\? \(tx\('propLoad\.motorLabel'/);
  assert.match(chart, /value: supplyLabel \?\? \(tx\('propLoad\.supplyLabel'/);
  // the rated cap still takes only a real (non-bound) S1 limit
  assert.match(panel, /const s1CapRpm = s1\?\.limit_motor && s1\.limit_motor\.rpm != null && !s1\.limit_motor\.lower_bound/);
});
