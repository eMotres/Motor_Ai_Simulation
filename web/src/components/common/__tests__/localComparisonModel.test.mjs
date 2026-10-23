import test from 'node:test';
import assert from 'node:assert/strict';
import { buildComparisonModel } from '../../compare/comparisonModel.ts';
import { localComparisonColumns } from '../localComparisonModel.ts';

test('Local comparison retains text and false verdicts while ranking only real numbers', () => {
  const rows = [
    { id: 'a', name: 'A', at: '', inputs: { gap: 1 }, results: { verdict: 'held', passed: false, numeric_text: '3', torque: 4 } },
    { id: 'b', name: 'B', at: '', inputs: { gap: 1.04 }, results: { verdict: 'open', passed: true, numeric_text: '4', torque: 5 } },
  ];
  const columns = localComparisonColumns(rows, [
    { key: 'gap', label: 'Gap', kind: 'input', d: 1 },
    { key: 'verdict', label: 'Verdict', kind: 'result' },
    { key: 'passed', label: 'Passed', kind: 'result' },
    { key: 'numeric_text', label: 'Stored text', kind: 'result', better: 'hi' },
    { key: 'torque', label: 'Torque', kind: 'result', better: 'hi' },
  ]);
  const model = buildComparisonModel(rows, columns, (a, b) => String(a ?? '') === String(b ?? ''));
  assert.deepEqual(model.differingInputKeys, [], 'formatted 1.0 inputs remain collapsed as before');
  assert.deepEqual(model.resultColumns.map((column) => column.key), ['result:verdict', 'result:passed', 'result:numeric_text', 'result:torque']);
  assert.equal(model.extents['result:verdict'], null);
  assert.equal(model.extents['result:passed'], null);
  assert.equal(model.extents['result:numeric_text'], null);
  assert.deepEqual(model.extents['result:torque'], { min: 4, max: 5 });
  const passed = columns.find((column) => column.key === 'result:passed');
  assert.equal(passed.format(false, rows[0]), 'no');
  assert.equal(passed.noColor(rows[0]), true);
});
