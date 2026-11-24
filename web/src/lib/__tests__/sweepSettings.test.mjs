// node --test - the settings a stored sweep ran with: one reader, labels from the stamp, fail-closed comparison
import test from 'node:test';
import assert from 'node:assert/strict';
import { sweepSettingsOf, sweepSettingsLabel, sweepSettingsMismatch, sweepSettingsMetrics, sweepMetricsComparable, isSweepRow } from '../sweepSettings.ts';

const stamp = (over = {}) => ({ mesh_preset: { name: 'optimization', custom: false, sig: 111, sig_scope: 2,
  effective: { steps_per_period: 48, gap_layers: 1 }, effective_source: { steps_per_period: 'solved', gap_layers: 'solved' }, ...over } });

test('a stamped sweep is labelled by its preset and carries its digest', () => {
  const s = sweepSettingsOf(stamp());
  assert.equal(s.sig, 111);
  assert.equal(sweepSettingsLabel(s, 'Optimization'), 'Optimization \u00b7 48 steps \u00b7 1 gap row');
});

test('the label is recomputed from the stamp: custom, solved-differs and mixed never read as the preset', () => {
  const c = sweepSettingsOf(stamp({ custom: true, name: 'optimization' }));
  assert.equal(c.preset, 'custom');
  const d = sweepSettingsOf(stamp({ solved_differs: true, effective: { steps_per_period: 52, gap_layers: 4 } }));
  assert.equal(d.preset, 'custom'); assert.equal(d.sig, null);
  const m = sweepSettingsOf(stamp({ mixed: true, effective: { steps_per_period: null, gap_layers: 1 },
    effective_source: { steps_per_period: 'mixed', gap_layers: 'solved' } }));
  assert.equal(sweepSettingsLabel(m), 'mixed'); assert.equal(m.sig, null);
});

test('a legacy sweep is labelled by its stored request and has no digest', () => {
  const s = sweepSettingsOf({ steps_per_period: 108, scan_params: { gap_layers: 1 } });
  assert.equal(s.source, 'stored_request'); assert.equal(s.sig, null);
  assert.equal(sweepSettingsLabel(s), '108 steps \u00b7 1 gap row');
});

test('a server list row is read the same way', () => {
  const s = sweepSettingsOf({ settings: { preset: 'custom', custom: true, steps_per_period: 108, gap_layers: 2, source: 'preset', sig: 5, sig_scope: 2,
    effective_source: { steps_per_period: 'solved', gap_layers: 'solved' } } });
  assert.equal(s.preset, 'custom'); assert.equal(s.sig, 5);
});

test('mismatch fails closed: unknown on either side is a mismatch, equal digests match', () => {
  const a = sweepSettingsOf(stamp()), b = sweepSettingsOf(stamp({ sig: 222 }));
  const legacy = sweepSettingsOf({ steps_per_period: 108 });
  assert.equal(sweepSettingsMismatch(a, a), false);
  assert.equal(sweepSettingsMismatch(a, b), true);
  assert.equal(sweepSettingsMismatch(legacy, legacy), true);
  assert.equal(sweepSettingsMismatch(a, legacy), true);
});

test('Compare: different steps-and-gap digests are not comparable; nulls and mixed sweeps fail closed', () => {
  const a = sweepSettingsMetrics(sweepSettingsOf(stamp()));
  const same = { ...a };
  const otherMesh = sweepSettingsMetrics(sweepSettingsOf(stamp({ sig: 222 })));   // another (steps, gap) digest
  assert.equal(sweepMetricsComparable([a, same]), true);
  assert.equal(sweepMetricsComparable([a, otherMesh]), false);
  const mixed = sweepSettingsMetrics(sweepSettingsOf(stamp({ mixed: true })));
  assert.equal('sweep_sig' in mixed, false);
  assert.equal(sweepMetricsComparable([a, mixed]), false);
  assert.equal(sweepMetricsComparable([{ sweep_origin: 1, sweep_sig: null }, { sweep_origin: 1, sweep_sig: null }]), false);
  assert.equal(sweepMetricsComparable([a, { T_avg_Nm: 1 }]), false);      // a sweep point is not ranked beside an ordinary run
});

test('an identity without a solve behind it, or with a null value, is unknown', () => {
  const unverified = sweepSettingsOf(stamp({ effective_source: { steps_per_period: 'requested', gap_layers: 'requested' } }));
  assert.equal(unverified.sig, null);
  const nul = sweepSettingsOf(stamp({ effective: { steps_per_period: null, gap_layers: 1 } }));
  assert.equal(nul.sig, null);
});

test('Compare rejects unknown sweep rows BEFORE the count shortcut: a lone unknown sweep row, legacy rows, a sweep beside an ordinary run', () => {
  const known = sweepSettingsMetrics(sweepSettingsOf(stamp()));
  const legacyApplied = { T_avg_Nm: 1, raw_samples_per_cogging_cycle: 12 };       // an applied sweep point from before the mark
  assert.equal(isSweepRow(legacyApplied), true);
  assert.equal(isSweepRow({ T_avg_Nm: 1 }), false);
  assert.equal(sweepMetricsComparable([legacyApplied]), false);                    // a lone unknown sweep row
  assert.equal(sweepMetricsComparable([legacyApplied, { ...legacyApplied }]), false);
  assert.equal(sweepMetricsComparable([known, { T_avg_Nm: 2 }]), false);           // sweep point beside an ordinary run
  assert.equal(sweepMetricsComparable([{ T_avg_Nm: 1 }, { T_avg_Nm: 2 }]), true);   // no sweep rows: nothing to refuse
  assert.equal(sweepMetricsComparable([known, { ...known }]), true);
});

test('a list row needs BOTH numbers solved: requested sources never give an identity', () => {
  const row = (es) => ({ settings: { preset: 'optimization', source: 'preset', sig: 5, sig_scope: 2, steps_per_period: 48, gap_layers: 1, effective_source: es } });
  assert.equal(sweepSettingsOf(row({ steps_per_period: 'solved', gap_layers: 'solved' })).sig, 5);
  assert.equal(sweepSettingsOf(row({ steps_per_period: 'requested', gap_layers: 'requested' })).sig, null);
  assert.equal(sweepSettingsOf(row({ steps_per_period: 'solved', gap_layers: 'requested' })).sig, null);
  assert.equal(sweepSettingsOf({ settings: { preset: 'optimization', source: 'preset', sig: 5, steps_per_period: 48, gap_layers: 1 } }).sig, null);
  assert.equal(sweepSettingsOf(stamp({ effective_source: { steps_per_period: 'solved', gap_layers: 'requested' } })).sig, null);
});

test('a stamp without the digest scope (stored before the preset version and wire cell joined it) has no digest', () => {
  assert.equal(sweepSettingsOf(stamp({ sig_scope: undefined })).sig, null);
  assert.equal(sweepSettingsOf(stamp({ sig_scope: 1 })).sig, null);
  assert.equal(sweepSettingsMismatch(sweepSettingsOf(stamp({ sig_scope: undefined })), sweepSettingsOf(stamp())), true);
});

test('the preset version and the wire cell are in the label; different digests are a mismatch', () => {
  const v2 = sweepSettingsOf(stamp({ version: 2, coil_rel: 2, sig: 333 }));
  assert.equal(v2.presetVersion, 2); assert.equal(v2.coilRel, 2);
  assert.equal(sweepSettingsLabel(v2, 'Optimization'), 'Optimization v2 \u00b7 48 steps \u00b7 1 gap row \u00b7 wire 2h');
  const v1 = sweepSettingsOf(stamp({ version: 1, coil_rel: 1, sig: 222 }));
  assert.equal(sweepSettingsMismatch(v1, v2), true);
  assert.equal(sweepSettingsMismatch(v2, sweepSettingsOf(stamp({ version: 2, coil_rel: 2, sig: 333 }))), false);
});

test('an applied row stores the digest scope; Compare ranks only rows that carry it (a row stored before has an unknown identity)', () => {
  const m = sweepSettingsMetrics(sweepSettingsOf(stamp({ sig: 333 })));
  assert.equal(m.sweep_sig, 333); assert.equal(m.sweep_sig_scope, 2);
  assert.equal(sweepMetricsComparable([m, { ...m }]), true);
  const old = { sweep_origin: 1, sweep_sig: 333 };            // applied before the scope existed
  assert.equal(sweepMetricsComparable([old, { ...old }]), false);
  assert.equal(sweepMetricsComparable([m, old]), false);
});

test('a list row without the digest scope has no digest (fail closed in the reader)', () => {
  const row = (scope) => ({ settings: { preset: 'optimization', source: 'preset', sig: 5, sig_scope: scope, steps_per_period: 48, gap_layers: 1,
    effective_source: { steps_per_period: 'solved', gap_layers: 'solved' } } });
  assert.equal(sweepSettingsOf(row(2)).sig, 5);
  assert.equal(sweepSettingsOf(row(undefined)).sig, null);
  assert.equal(sweepSettingsOf(row(1)).sig, null);
});
