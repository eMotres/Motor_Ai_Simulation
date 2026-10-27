// node --test — the "Torque held" line of the coupled result (owner 2026-10-27):
// the current the loop corrected so the duty's torque is made at the magnet
// temperature it found (coupledApi.heldTorqueValues + the SummaryTable row).
//
// The repo's convention for node tests: `node --test` cannot load the TS
// modules, so the pure function under test is re-stated here verbatim and kept
// in sync; the wiring (the row, its keys in EN and zh-CN) is pinned by reading
// the shipped source.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const webSrc = (...p) => join(here, '..', '..', '..', ...p);

// ── verbatim from coupledApi.ts ───────────────────────────────────────────
function heldTorqueValues(c) {
  const h = c?.hold_torque;
  if (!h || h.basis !== 'held_torque') return null;
  const n = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
  const i = n(h.I_phase_rms_A); const t = n(h.target_nm);
  if (i == null || t == null) return null;
  const m = n(h.magnet_temp_c); const ir = n(h.I_requested_A);
  const e = n(h.torque_error_pct);
  return { i: i.toFixed(1), t: t.toFixed(1), m: m == null ? '—' : m.toFixed(0),
           iReq: ir == null ? '—' : ir.toFixed(1), held: h.held === true,
           err: e == null ? '—' : `${e >= 0 ? '+' : ''}${e.toFixed(2)}`,
           tol: String(n(h.tol_pct) ?? 0.5) };
}

test('the shipped helper is the one re-stated here', () => {
  const src = readFileSync(webSrc('components', 'simulation', 'coupledApi.ts'), 'utf8');
  const body = heldTorqueValues.toString().split('\n').slice(1, -1)
    .map((l) => l.trim()).filter(Boolean);
  for (const line of body) {
    const core = line.replace(/\s+/g, ' ');
    assert.ok(src.replace(/\s+/g, ' ').includes(core.replace(/\(v\)/, '(v: unknown)')),
      `coupledApi.ts no longer contains: ${line}`);
  }
});

test('a held record gives the numbers of its one line', () => {
  const v = heldTorqueValues({ hold_torque: {
    basis: 'held_torque', target_nm: 1500, I_phase_rms_A: 270.43,
    I_requested_A: 308.1, magnet_temp_c: 83.2, held: true,
    torque_error_pct: -0.21, tol_pct: 0.5 } });
  assert.deepEqual(v, { i: '270.4', t: '1500.0', m: '83', iReq: '308.1',
                        held: true, err: '-0.21', tol: '0.5' });
});

test('a fixed-current record, or none, prints no line', () => {
  assert.equal(heldTorqueValues({ hold_torque: { basis: 'fixed_current' } }), null);
  assert.equal(heldTorqueValues({}), null);
  assert.equal(heldTorqueValues(null), null);
});

test('the row is wired, and every key it uses exists in EN and zh-CN', () => {
  const st = readFileSync(webSrc('components', 'simulation', 'SummaryTable.tsx'), 'utf8');
  assert.match(st, /heldTorqueValues\(s\.coupling\)/);
  for (const lng of ['en', 'zh-CN']) {
    const j = JSON.parse(readFileSync(webSrc('locales', lng, 'results.json'), 'utf8'));
    for (const k of ['torqueHeld', 'torqueHeldLine', 'torqueNotHeldLine', 'torqueHeldTip']) {
      assert.ok(typeof j[k] === 'string' && j[k].length > 0, `${lng}: ${k}`);
      assert.match(st, new RegExp(`'${k}'`));
    }
    for (const ph of ['{{i}}', '{{t}}', '{{m}}']) {
      assert.ok(j.torqueHeldLine.includes(ph), `${lng}: torqueHeldLine ${ph}`);
    }
  }
});
