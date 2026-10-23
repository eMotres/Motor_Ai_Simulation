import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
import { shouldSuppressMixedBasisColor } from '../comparisonModel.ts';

const panel = fs.readFileSync(new URL('../ComparePanel.tsx', import.meta.url), 'utf8');
const adapterSource = panel.slice(panel.indexOf('  const fullBasisRows ='), panel.indexOf('  const selectedComparisonModel ='));
const adapterJs = ts.transpileModule(`${adapterSource}\nreturn { resultModelColumns, comparisonPercentValue };`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText;
const makeAdapter = new Function('sims', 'selected', 'resultCols', 'resultSrc', 'resultVal', 'colId', 'shouldSuppressMixedBasisColor', adapterJs);

function measure(rowsCount, mixed) {
  let basisReads = 0;
  const sims = Array.from({ length: rowsCount }, (_, i) => ({ active: mixed && i < rowsCount / 2, value: i + 1 }));
  const resultCols = ['mass', 'torqueDensity', 'powerDensity'].map(key => ({
    key, label: key, activeBasis: row => { basisReads++; return row.active; },
  }));
  const { resultModelColumns: columns, comparisonPercentValue } = makeAdapter(sims, sims, resultCols, (_col, row) => row, (_col, row) => row.value,
    col => col.key, shouldSuppressMixedBasisColor);
  const suppressed = columns[0].noColor(sims[0], sims);
  const fullRow = sims.find(row => !row.active);
  const fullNotSuppressed = fullRow ? !columns[0].noColor(fullRow, sims) : true;
  for (const row of sims) for (const column of columns) column.noColor(row, sims);
  for (const row of sims) for (const column of resultCols) comparisonPercentValue(column, row);
  return { basisReads, suppressed, fullNotSuppressed };
}

test('Compare adapter checks library basis presence once per column and scales linearly', (t) => {
  const measurements = {};
  for (const mixed of [false, true]) {
    const small = measure(1000, mixed);
    const large = measure(2000, mixed);
    measurements[mixed ? 'mixed_full_and_active' : 'all_full'] = {
      basis_reads_1000: small.basisReads,
      basis_reads_2000: large.basisReads,
    };
    assert.ok(small.basisReads <= 12_000, `1,000 rows should stay linear, got ${small.basisReads}`);
    assert.ok(large.basisReads <= 24_000, `2,000 rows should stay linear, got ${large.basisReads}`);
    assert.ok(large.basisReads <= small.basisReads * 2 + 6,
      `doubling rows should approximately double basis reads (${small.basisReads} -> ${large.basisReads})`);
    if (mixed) {
      assert.equal(small.suppressed, true, 'active-basis values are unranked when library has full-basis rows');
      assert.equal(small.fullNotSuppressed, true, 'full-basis rows keep their ranking');
    } else {
      assert.equal(small.suppressed, false, 'active-basis values rank when the library has no full-basis row');
    }
  }
  t.diagnostic(`basis accessor counts: ${JSON.stringify(measurements)}`);
});

test('Compare model callers compute only the extents and keys they consume', () => {
  assert.match(panel, /buildComparisonModel\(selected, paramModelColumns, valEq\)/,
    'selected model supplies differing input keys only');
  assert.match(panel, /buildComparisonModel\(sims, resultModelColumns, valEq\)/,
    'library model supplies result extents only');
});
