// Execute the actual chart useMemo callbacks; no duplicated selection logic.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';

const require = createRequire(import.meta.url);
const ts = require('typescript');
const source = readFileSync(new URL('../TransientCharts.tsx', import.meta.url), 'utf8');

function chartMemo(name) {
  const start = source.indexOf(`  const ${name} = React.useMemo`);
  assert.ok(start >= 0);
  const endMarker = '\n  }, [data]);';
  const end = source.indexOf(endMarker, start);
  assert.ok(end > start);
  const declaration = source.slice(start, end + endMarker.length);
  const js = ts.transpileModule(
    `(data, torqueFilter) => { ${declaration}; return ${name}; }`,
    { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  return runInNewContext(js, { React: { useMemo: callback => callback() } });
}

const torque = chartMemo('Tshown');
const harmonics = chartMemo('harmRows');
const plain = value => JSON.parse(JSON.stringify(value));

test('chart preserves raw samples even if old filter preference is true', () => {
  const data = { T_em_raw_Nm: [1, 3], T_em_Nm: [1, 3], T_em_filt_Nm: [2, 2] };
  assert.deepEqual(plain(torque(data, true)), [1, 3]);
  assert.deepEqual(plain(torque(data, false)), [1, 3]);
});

test('chart accepts legacy raw key but never filtered-only samples', () => {
  assert.deepEqual(plain(torque({ T_em_Nm: [1, 3], T_em_filt_Nm: [2, 2] }, true)), [1, 3]);
  assert.deepEqual(plain(torque({ T_em_filt_Nm: [2, 2] }, true)), []);
});

test('chart retains every fractional order and tiny amplitudes', () => {
  const data = { T_harm_order: [2 / 3, 6 / 1.001, 43], T_harm_amp: [1e-5, .2, .3], T_avg_Nm: 10 };
  const rows = plain(harmonics(data, true));
  assert.deepEqual(rows.map(row => row.amp), [1e-5, .2, .3]);
  assert.deepEqual(rows.map(row => row.order), [2 / 3, 6 / 1.001, 43]);
  assert.deepEqual(rows, plain(harmonics(data, false)));
});
