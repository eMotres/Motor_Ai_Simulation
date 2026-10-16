import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { catalogCopperBreakdown } from '../catalogCopperBreakdown.ts';

test('copper tooltip breakdown shows three-phase DC I²R and AC/proximity residual', () => {
  const split = catalogCopperBreakdown(24.177, 0.0196, 39);
  assert.ok(split);
  assert.ok(Math.abs(split.dcW - 34.3702) < 0.0001);
  assert.ok(Math.abs(split.acProximityW - (39 - split.dcW)) < 1e-12);
});

test('invalid copper inputs are omitted instead of inventing a breakdown', () => {
  assert.equal(catalogCopperBreakdown(NaN, 0.02, 40), null);
  assert.equal(catalogCopperBreakdown(2, -0.02, 40), null);
});

test('Configure copper tile presents the calculated breakdown in its tooltip', () => {
  const panel = readFileSync(new URL('../../components/compare/ConfiguratorPanel.tsx', import.meta.url), 'utf8');
  assert.match(panel, /catalogCopperBreakdown\(scaleKnobs\.I_A, result\.R_ohm, result\.P_cu_W\)/);
  assert.match(panel, /goodHi=\{false\} tip=\{copperBreakdownTip\}/);
});