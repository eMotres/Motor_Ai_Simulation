// node --test - the settings a stored sweep ran with, and the mismatch rule
import test from 'node:test';
import assert from 'node:assert/strict';
import { sweepSettingsOf, sweepSettingsLabel, sweepSettingsMismatch, sweepSettingsMetrics, sweepMetricsComparable } from '../sweepSettings.ts';

test('a stamped sweep is labelled by its preset', () => {
  const s = sweepSettingsOf({ mesh_preset: { name: 'optimization', effective: { steps_per_period: 48, gap_layers: 1 } } });
  assert.equal(s.source, 'preset');
  assert.equal(sweepSettingsLabel(s, 'Optimization'), 'Optimization · 48 steps · 1 gap row');
});

test('an old sweep is labelled by its stored request', () => {
  const s = sweepSettingsOf({ steps_per_period: 108, scan_params: { gap_layers: 1 } });
  assert.equal(s.source, 'stored_request');
  assert.equal(sweepSettingsLabel(s), '108 steps · 1 gap row');
});

test('a server list row carries its settings', () => {
  const s = sweepSettingsOf({ settings: { preset: null, steps_per_period: 108, gap_layers: 2, source: 'stored_request' } });
  assert.deepEqual([s.steps, s.gapLayers], [108, 2]);
  assert.equal(sweepSettingsLabel(s), '108 steps · 2 gap rows');
});

test('108 and 48 steps mismatch, equal settings do not, unknown never matches known', () => {
  const a = sweepSettingsOf({ steps_per_period: 108, scan_params: { gap_layers: 1 } });
  const b = sweepSettingsOf({ mesh_preset: { name: 'optimization', effective: { steps_per_period: 48, gap_layers: 1 } } });
  assert.equal(sweepSettingsMismatch(a, b), true);
  assert.equal(sweepSettingsMismatch(b, b), false);
  assert.equal(sweepSettingsMismatch(sweepSettingsOf({}), b), true);
});

test('a custom stamp is labelled custom, not Optimization', () => {
  const s = sweepSettingsOf({ settings: { preset: 'custom', steps_per_period: 52, gap_layers: 4, source: 'preset' } });
  assert.equal(sweepSettingsLabel(s, 'Optimization'), 'custom \u00b7 52 steps \u00b7 4 gap rows');
  assert.equal(sweepSettingsMetrics(s).sweep_preset_code, 0);
});

test('Compare: 108-step and 48-step sweep results are not comparable, equal ones are, unknown ones never', () => {
  const a = sweepSettingsMetrics(sweepSettingsOf({ steps_per_period: 108, scan_params: { gap_layers: 1 } }));
  const b = sweepSettingsMetrics(sweepSettingsOf({ mesh_preset: { name: 'optimization', effective: { steps_per_period: 48, gap_layers: 1 } } }));
  assert.equal(sweepMetricsComparable([a, b]), false);
  assert.equal(sweepMetricsComparable([b, { ...b }]), true);
  assert.equal(sweepMetricsComparable([b, { T_avg_Nm: 1 }]), true);          // a non-sweep row is not a sweep row
  assert.equal(sweepMetricsComparable([b, { sweep_preset_code: 1 }]), false);  // unknown steps matches nothing
});
