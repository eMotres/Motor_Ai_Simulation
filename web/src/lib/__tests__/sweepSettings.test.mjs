// node --test - the settings a stored sweep ran with, and the mismatch rule
import test from 'node:test';
import assert from 'node:assert/strict';
import { sweepSettingsOf, sweepSettingsLabel, sweepSettingsMismatch } from '../sweepSettings.ts';

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
