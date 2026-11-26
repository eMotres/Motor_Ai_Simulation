// The summary tile table and the charts under it share ONE width (owner 2026-11-26): not 100 % of the page, but the same cap, so the
// charts line up with the table above and stack in a single full-width column on a phone.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { SIM_BLOCK_MAX_WIDTH, SIM_BLOCK_SX } from '../simLayout.ts';

const src = (f) => readFileSync(new URL(`../${f}`, import.meta.url), 'utf8');

test('one shared cap: full width up to it, so a narrow screen gets the whole width', () => {
  assert.equal(SIM_BLOCK_SX.maxWidth, SIM_BLOCK_MAX_WIDTH);
  assert.equal(SIM_BLOCK_SX.width, '100%');
  assert.ok(SIM_BLOCK_MAX_WIDTH >= 960 && SIM_BLOCK_MAX_WIDTH <= 1600);
});

test('the summary table, the charts and the read-only project view all use that one contract', () => {
  for (const f of ['SummaryTable.tsx', 'TransientCharts.tsx', 'ReadOnlySimulationView.tsx']) {
    const s = src(f);
    assert.match(s, /import \{ SIM_BLOCK_SX \} from '\.\/simLayout';/, f);
    assert.match(s, /\.\.\.SIM_BLOCK_SX/, f);
  }
  // no chart panel keeps its own full-page width
  assert.doesNotMatch(src('TransientCharts.tsx'), /<Paper sx=\{\{ bgcolor/);
});
