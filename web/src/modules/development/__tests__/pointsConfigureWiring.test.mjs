// The points-limited Configure draws the ONE information panel (components/common/ResultsPanel) and keeps the battery block, the
// variant controls and the stars (owner 2026-10-26; the tile list itself is lib/emResultTiles.ts since 2026-11-01).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { highlightIndex, isConfigRecommended } from '../projectFocus.ts';

test('the Motors tab marks the recommended variant of the highlighted project', () => {
  const idx = highlightIndex([{ die: 'A', config: 'c1', recommended: { by: 'x' } }, { die: 'A', config: 'c2' }]);
  assert.equal(isConfigRecommended(idx, 'a', 'C1'), true);
  assert.equal(isConfigRecommended(idx, 'A', 'c2'), false);
});

test('wiring: the points-limited Configure uses the shared panel and the battery block, the custom DC bus field is gone, stars are drawn', () => {
  const src = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
  const panel = src('../../../components/compare/PointsConfigure.tsx');
  assert.match(panel, /<ResultsPanel panel=\{panel\}/);
  assert.match(panel, /<BatteryPanel vDc=\{dcBus\}/);
  assert.match(panel, /specBatteryView\(/);
  assert.doesNotMatch(panel, /busText|busHeadroom|points\.bus/);
  assert.match(panel, /<VariantControls projectId=\{focus\.id\}/);
  assert.match(panel, /data-testid="step-star"/);
  assert.match(src('../../../components/compare/StepsChart.tsx'), /data-testid="steps-chart-star"/);
  assert.match(src('../../../components/catalog/FamilyCatalog.tsx'), /isConfigRecommended\(projectIndex, die\.name, b\.config\)/);
});
