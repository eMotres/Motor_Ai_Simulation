// node --test: the one Kv / Kt definition (owner 2026-11-05) and its consumers.
import test from 'node:test';
import assert from 'node:assert/strict';

let K = null;
try { K = await import('../kvKt.ts'); } catch { /* old Node */ }
const t = K ? test : test.skip;

t('Kt follows Kv exactly: G2 no-load Kv 6.02 gives Kt 1.94, 83 N·m at 49 A is Kt at load 1.70', () => {
  assert.ok(Math.abs(K.KT_TIMES_KV - 11.695) < 0.0005);
  assert.ok(Math.abs(K.ktFromKv(6.02) - 1.94) < 0.005);
  assert.ok(Math.abs(83 / 49 - 1.70) < 0.01);
  assert.ok(Math.abs(K.kvFromKt(K.ktFromKv(6.02)) - 6.02) < 1e-9);
  assert.ok(Math.abs(K.ktFromKv(6.02, true) - K.ktFromKv(6.02) * Math.sqrt(3)) < 1e-9);
  assert.equal(K.ktPerLineAmp(6.02), K.ktFromKv(6.02));
  for (const bad of [null, undefined, 0, -1, NaN]) assert.equal(K.ktFromKv(bad), null);
});

t('the result tiles: Kt comes from the KV beside it, with the load figure in its own tile; an older run still works', async () => {
  let E = null;
  try { E = await import('../emResultTiles.ts'); } catch { /* old Node */ }
  if (!E) return;
  const base = { rpm: 1000, I_phase_rms_A: 14, T_em_avg_Nm: 0.42, KV_noload_rpm_per_V_line: 6.02, KV_rpm_per_V_line: 5.5 };
  const one = (s, id) => E.emTileRows(s, { emSource: 'sine' }).flat().find((x) => x.id === id);
  assert.ok(Math.abs(one(base, 'kt').value - K.ktFromKv(6.02)) < 1e-9);
  assert.equal(one(base, 'kt').tipKey, 'emTiles.ktTip');
  assert.ok(Math.abs(one(base, 'ktLoad').value - 0.03) < 1e-9);
  const old = { ...base, KV_noload_rpm_per_V_line: undefined };                // an older run: only the loaded waveform-peak KV
  assert.ok(Math.abs(one(old, 'kt').value - K.ktFromKv(5.5)) < 1e-9);
  assert.equal(one(old, 'kt').tipKey, 'emTiles.ktTipPeak');
  assert.equal(one(old, 'kv').tipKey, 'emTiles.kvTipLoaded');
});
