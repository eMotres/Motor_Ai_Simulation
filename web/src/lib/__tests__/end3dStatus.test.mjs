// node --test: the 3-D factor's 2-D check status label (lib/end3dStatus.ts) and its locale keys (EN + zh-CN).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { end3dStatusOf, end3dStatusView } from '../end3dStatus.ts';

const en = JSON.parse(readFileSync(new URL('../../locales/en/results.json', import.meta.url), 'utf8'));
const zh = JSON.parse(readFileSync(new URL('../../locales/zh-CN/results.json', import.meta.url), 'utf8'));

test('known statuses are read, anything else shows nothing', () => {
  for (const s of ['verified', 'unverified', 'failed', 'invalid', 'fallback', 'recompute']) assert.equal(end3dStatusOf({ k_flux_status: s }), s);
  assert.equal(end3dStatusOf({ k_flux_status: ' Failed ' }), 'failed');
  assert.equal(end3dStatusOf({ k_flux_status: 'weird' }), null);
  assert.equal(end3dStatusOf({}), null);
  assert.equal(end3dStatusOf(null), null);
});

test('every status has a short label and a tooltip in EN and zh-CN', () => {
  for (const s of ['verified', 'unverified', 'failed', 'invalid', 'fallback', 'recompute']) {
    const v = end3dStatusView(s);
    for (const d of [en, zh]) {
      assert.ok(d[v.labelKey] && d[v.labelKey].length <= 20, `${v.labelKey} short`);
      assert.ok(d[v.tipKey] && d[v.tipKey].length > 20, v.tipKey);
    }
  }
  assert.notEqual(end3dStatusView('failed').color, end3dStatusView('verified').color);
});

test('SummaryTable shows it as a label with a tooltip, not a caption line', () => {
  const src = readFileSync(new URL('../../components/simulation/SummaryTable.tsx', import.meta.url), 'utf8');
  assert.match(src, /<Tooltip title=\{tx\(v\.tipKey\)\}>/);
  assert.doesNotMatch(src, /variant\s*=\s*["']caption["']/);
});

test('the web statuses are the python STATUSES', () => {
  const py = readFileSync(new URL('../../../../src/motor_ai_sim/end3d_verdict.py', import.meta.url), 'utf8');
  const m = py.match(/STATUSES = \(([^)]*)\)/);
  assert.ok(m, 'STATUSES found');
  const pyList = [...m[1].matchAll(/"([a-z]+)"/g)].map((x) => x[1]);
  assert.deepEqual(pyList, ['verified', 'unverified', 'failed', 'invalid', 'fallback', 'recompute']);
});
