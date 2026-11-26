// node --test - the 3-D correction of the tiles: the efficiency of a GENERATOR is (P_in - losses) / P_in, a motor's P / (P + losses);
// a sweep point is scaled by its OWN k.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { apply3dToSummary } from '../apply3d.ts';
import { pointApply3dK, validK } from '../sweepApply3d.ts';

const base = { T_em_avg_Nm: 10, P_mech_W: 1000, P_loss_total_W: 100, efficiency: 0.9091, end3d: { k_flux: 0.9 } };

test('a motor keeps the motor formula P / (P + losses) on the scaled power', () => {
  const s = apply3dToSummary({ ...base, op_mode: 'motor' });
  assert.ok(Math.abs(s.efficiency - 900 / 1000) < 1e-12);
  assert.ok(Math.abs(apply3dToSummary(base).efficiency - 900 / 1000) < 1e-12);          // no op_mode = a motor, as before
});

test('a generator takes the shaft power IN: (P - losses) / P on the scaled power', () => {
  const s = apply3dToSummary({ ...base, op_mode: 'generator' });
  assert.ok(Math.abs(s.efficiency - (900 - 100) / 900) < 1e-12);
  assert.ok(s.efficiency < 1 && s.efficiency > 0.88);
  // the input does not cover the losses: 0, like the server
  assert.equal(apply3dToSummary({ ...base, op_mode: 'generator', P_mech_W: 50 }).efficiency, 0);
  // missing losses: the summary's own value stays
  assert.equal(apply3dToSummary({ ...base, op_mode: 'generator', P_loss_total_W: null }).efficiency, base.efficiency);
});

test('no factor: the summary is returned as it is', () => {
  const s = { ...base, end3d: null, op_mode: 'generator' };
  assert.equal(apply3dToSummary(s), s);
});

test('a sweep point is scaled by its OWN k, else the loaded machine\'s, and the switch turns it off for every point', () => {
  assert.equal(pointApply3dK({ end3d: { k_flux: 0.95 } }, 0.9, true), 0.95);
  assert.equal(pointApply3dK({}, 0.9, true), 0.9);
  assert.equal(pointApply3dK({ end3d: null }, 0.9, true), 0.9);
  assert.equal(pointApply3dK({ end3d: { k_flux: 1.3 } }, 0.9, true), 0.9);              // not a factor: the machine's
  assert.equal(pointApply3dK({ end3d: { k_flux: 0.95 } }, 0.9, false), null);
  assert.equal(pointApply3dK(undefined, null, true), null);
  assert.equal(validK('0.97'), 0.97); assert.equal(validK(null), null); assert.equal(validK(1.3), null);
  const src = readFileSync(new URL('../../components/sweep/SweepStudyPanel.tsx', import.meta.url), 'utf8');
  assert.ok(src.includes('pointApply3dK(p, machineK, readApply3dOn())'));
  assert.equal((src.match(/kOfPoint\(/g) || []).length, 3);                              // the three scaling sites
  assert.ok(!/scale3d\(k3dChart,/.test(src) && /const k3d = kOfPoint\(v, readApply3dK\(\)\)/.test(src));
});
