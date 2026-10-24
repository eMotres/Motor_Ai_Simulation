import test from 'node:test';
import assert from 'node:assert/strict';
import { appendSamples, isValidLoadCurve, markCatalogRowsStale, refreshCatalogRows, selectCatalogSamples, shaftPowerW, updateMissionRow } from '../missionProfileState.ts';
import { readLoadDraft, writeLoadDraft } from '../loadDraftAdapter.ts';

test('shaft power uses SI speed conversion and preserves signed shaft power', () => {
  assert.ok(Math.abs(shaftPowerW(1, 60) - 2 * Math.PI) < 1e-12);
  assert.ok(Math.abs(shaftPowerW(-1, 60) + 2 * Math.PI) < 1e-12);
});

test('append is idempotent per catalog content and preserves manual rows', () => {
  const manual = { row_id: 'manual-1', phase: 'custom', speed_rpm: 500, torque_Nm: 2, duration_s: 4, battery_voltage_V: 24, source: 'manual' };
  const snap = { catalog_id: 'prop-a', content_digest: 'digest-a', temperature_C: 25, altitude_m: 0, pressure_Pa: 101325 };
  const once = appendSamples([manual], [{ rpm: 1000, torque_Nm: 0.2 }], snap, { duration_s: 5, voltage_V: 48 });
  const twice = appendSamples(once, [{ rpm: 1000, torque_Nm: 0.2 }], snap, { duration_s: 5, voltage_V: 48 });
  assert.equal(twice.length, 2); assert.equal(twice[0], manual); assert.notEqual(twice[1].row_id, '');
  assert.equal(Object.isFrozen(twice[1].source_snapshot), true);
  assert.equal(twice[1].source_snapshot.digest, 'digest-a');
});

test('catalog changes mark imported rows stale and refresh keeps overrides and updates sample identity', () => {
  const snap = { catalog_id: 'prop-a', content_digest: 'old', temperature_C: 25, altitude_m: 0, pressure_Pa: null };
  const [original] = appendSamples([], [{ rpm: 1000, torque_Nm: 0.2 }], snap);
  const edited = updateMissionRow([original], original.row_id, { torque_Nm: 0.25 })[0];
  assert.equal(edited.override.torque_Nm, true);
  const stale = markCatalogRowsStale([edited], 'prop-a', { temperature_C: 30, altitude_m: 0, pressure_Pa: null, content_digest: 'new' });
  assert.equal(stale[0].stale, true);
  const refreshed = refreshCatalogRows(stale, [{ rpm: 1000, torque_Nm: 0.3 }], { ...snap, catalog_type: 'air_propeller', calculation_model: 'server_propeller_series', content_digest: 'new', temperature_C: 30, rpm_range_tested: [1000, 5000], pitch_in: 7 });
  assert.equal(refreshed[0].row_id, original.row_id); assert.equal(refreshed[0].torque_Nm, 0.25);
  assert.equal(refreshed[0].stale, false); assert.equal(refreshed[0].source_snapshot.digest, 'new');
  assert.deepEqual(refreshed[0].source_snapshot.rpm_range_tested, [1000, 5000]); assert.equal(refreshed[0].source_snapshot.pitch_in, 7);
  assert.equal(appendSamples(refreshed, [{ rpm: 1000, torque_Nm: 0.3 }], { catalog_id: 'prop-a', content_digest: 'new', catalog_type: 'air_propeller', calculation_model: 'server_propeller_series', temperature_C: 30, altitude_m: 0, pressure_Pa: null }).length, 1);
});

test('sample identity includes conditions, type and shaft metadata', () => {
  const base = { catalog_id: 'prop-a', digest: 'same', catalog_type: 'air_propeller', calculation_model: 'server_propeller_series', shaft_side: 'load', temperature_C: 25, altitude_m: 0, pressure_Pa: null };
  const first = appendSamples([], [{ rpm: 1000, torque_Nm: 0.2 }], base);
  const changed = appendSamples(first, [{ rpm: 1000, torque_Nm: 0.25 }], { ...base, temperature_C: 30 });
  assert.equal(changed.length, 2);
  assert.equal(appendSamples(changed, [{ rpm: 1000, torque_Nm: 0.25 }], { ...base, temperature_C: 30 }).length, 2);
});

test('catalog sample preview stays within the tested domain and checks manual curve order', () => {
  const points = Array.from({ length: 81 }, (_, i) => ({ rpm: i * 500 }));
  const selected = selectCatalogSamples([...points, { rpm: 4000 }, { rpm: 31000 }], [5000, 30000], 6);
  assert.equal(selected.length, 6);
  assert.ok(selected.every((p) => p.rpm >= 5000 && p.rpm <= 30000));
  assert.ok(isValidLoadCurve([{ rpm: 0, torque_Nm: 0 }, { rpm: 1000, torque_Nm: 1 }]));
  assert.equal(isValidLoadCurve([{ rpm: 1000, torque_Nm: 1 }, { rpm: 1000, torque_Nm: 2 }]), false);
});

test('load editor round-trips v2 wrappers and keeps samples and unrelated answers', () => {
  const initial = { fields: { '4_1': { status: 'filled', value: 'mount data' } }, mission_profile: [] };
  const load = { load_type: 'air_propeller', source: 'catalog', catalog_id: 'prop-a', load_inertia_kg_m2: 0.001,
    startup_mode: 'soft_start', shaft_side: 'load', series: { rpm: [0, 1000], torque_Nm: [0, 0.2], shaft_power_W: [0, 20.94], extrapolated: [false, false], quality: 'measured_torque', ambient_C: 25, altitude_m: 100, pressure_Pa: null, tested_domain_rpm: [1000, 12000], content_digest: 'sha256:fixture', pitch_in: 5, source_urls: ['https://example.invalid/catalog'], fit: { ct: { c_ref: 0.01, rpm_ref: 5000, k: 0, rpm_min: 1000, rpm_max: 12000, rms_rel: 0.02, n_used: 7 }, cp: { c_ref: 0.02, rpm_ref: 5000, k: 0, rpm_min: 1000, rpm_max: 12000, rms_rel: 0.03, n_used: 7 } } } };
  const saved = writeLoadDraft(initial, load);
  assert.equal(saved.fields['4_1'].value, 'mount data'); assert.equal(saved.fields.load_type.value, 'air_propeller');
  assert.equal(saved.fields.load_characteristic.value.samples[0].rpm, 1000);
  assert.deepEqual(Object.keys(saved.fields.load_characteristic.value).sort(), ['calculation_model', 'catalog_id', 'catalog_type', 'conditions', 'content_digest', 'medium', 'samples', 'shaft_side', 'source_snapshot', 'units', 'valid_domain'].sort());
  assert.equal(saved.fields.load_characteristic.value.calculation_model_version, undefined);
  assert.equal(saved.fields.load_characteristic.value.shaft_side, 'load');
  const restored = readLoadDraft(saved);
  assert.equal(restored.series.torque_Nm[0], 0.2); assert.equal(restored.series.tested_domain_rpm[0], 1000); assert.equal(restored.startup_mode, 'soft_start');
  assert.equal(restored.series.pitch_in, 5); assert.equal(restored.series.fit.ct.c_ref, 0.01); assert.deepEqual(restored.series.source_urls, ['https://example.invalid/catalog']);
});

test('unknown wrappers retain their stored value during unrelated edits', () => {
  const draft = { fields: { load_inertia_kg_m2: { status: 'not_known_yet', value: 0.004 }, startup_mode: { status: 'filled', value: 'loaded' } } };
  const loaded = readLoadDraft(draft);
  const saved = writeLoadDraft(draft, { ...loaded, startup_mode: 'unloaded' });
  assert.equal(saved.fields.load_inertia_kg_m2.status, 'not_known_yet'); assert.equal(saved.fields.load_inertia_kg_m2.value, 0.004);
  assert.equal(saved.fields.startup_mode.value, 'unloaded');
});

test('unknown load characteristic stays inactive but can be marked known without losing its frozen samples', () => {
  const characteristic = { catalog_id: 'prop-a', catalog_type: 'air_propeller', medium: 'air', content_digest: 'digest', calculation_model: 'server_propeller_series', units: 'rpm,N·m,W', shaft_side: 'load', conditions: { temperature_C: 25, altitude_m: 0 }, requested_conditions: { temperature_C: 30, altitude_m: 0 }, valid_domain: { min_rpm: 1000, max_rpm: 5000 }, samples: [{ rpm: 1000, torque_Nm: 0.2, shaft_power_W: 20.94, quality: 'measured_torque', extrapolated: false }] };
  const draft = { fields: { load_type: { status: 'filled', value: 'air_propeller' }, load_source: { status: 'filled', value: 'catalog' }, load_characteristic: { status: 'not_known_yet', value: characteristic } } };
  const unknown = readLoadDraft(draft);
  assert.equal(unknown.series.rpm[0], 1000); assert.equal(unknown.answer_status.load_characteristic, 'not_known_yet');
  const known = { ...unknown, answer_status: { ...unknown.answer_status, load_characteristic: 'filled' } };
  const saved = writeLoadDraft(draft, known);
  assert.equal(saved.fields.load_characteristic.status, 'filled'); assert.equal(saved.fields.load_characteristic.value.samples[0].torque_Nm, 0.2);
  assert.equal(saved.fields.load_characteristic.value.conditions.temperature_C, 25);
  assert.equal(saved.fields.load_characteristic.value.requested_conditions.temperature_C, 30);
});

test('unknown load source retains curve as an inactive wrapper', () => {
  const curve = { catalog_id: 'prop-a', catalog_type: 'air_propeller', medium: 'air', content_digest: 'fixture', calculation_model: 'server_propeller_series', units: 'rpm,N·m,W', shaft_side: 'load', conditions: { temperature_C: 25, altitude_m: 0 }, valid_domain: { min_rpm: 1000, max_rpm: 1000 }, samples: [{ rpm: 1000, torque_Nm: 0.2, shaft_power_W: 20.94, quality: 'measured_torque', extrapolated: false }] };
  const draft = { fields: { load_type: { status: 'not_known_yet', value: 'air_propeller' }, load_source: { status: 'not_known_yet', value: 'catalog' }, load_characteristic: { status: 'not_known_yet', value: curve } } };
  const loaded = readLoadDraft(draft);
  assert.equal(loaded.load_type, 'air_propeller'); assert.equal(loaded.source, 'catalog');
  const saved = writeLoadDraft(draft, loaded);
  assert.equal(saved.fields.load_characteristic.status, 'not_known_yet');
  assert.equal(saved.fields.load_characteristic.value.samples[0].torque_Nm, 0.2);
  const restored = readLoadDraft(saved);
  assert.equal(restored.source, 'catalog'); assert.equal(restored.load_type, 'air_propeller');
  const known = { ...restored, answer_status: { ...restored.answer_status, load_source: 'filled', load_type: 'filled', load_characteristic: 'filled' } };
  const recovered = writeLoadDraft(saved, known);
  assert.equal(recovered.fields.load_type.value, 'air_propeller'); assert.equal(recovered.fields.load_source.value, 'catalog');
  assert.equal(recovered.fields.load_characteristic.status, 'filled');
});

test('unknown load source deactivates an otherwise filled characteristic as missing, then restores it', () => {
  const characteristic = { catalog_id: 'prop-a', catalog_type: 'air_propeller', medium: 'air', content_digest: 'digest', calculation_model: 'server_propeller_series', units: 'rpm,N·m,W', shaft_side: 'load', conditions: { temperature_C: 25, altitude_m: 0 }, samples: [{ rpm: 1000, torque_Nm: 0.2, shaft_power_W: 20.94, quality: 'measured_torque', extrapolated: false }] };
  const draft = { fields: { load_type: { status: 'filled', value: 'air_propeller' }, load_source: { status: 'filled', value: 'catalog' }, load_characteristic: { status: 'filled', value: characteristic } } };
  const loaded = readLoadDraft(draft);
  const unknown = writeLoadDraft(draft, { ...loaded, answer_status: { ...loaded.answer_status, load_source: 'not_known_yet' } });
  assert.equal(unknown.fields.load_source.value, 'catalog');
  assert.equal(unknown.fields.load_characteristic.status, 'missing');
  assert.deepEqual(unknown.fields.load_characteristic.value, characteristic);
  const back = readLoadDraft(unknown);
  const known = writeLoadDraft(unknown, { ...back, answer_status: { ...back.answer_status, load_source: 'filled' } });
  assert.equal(known.fields.load_characteristic.status, 'filled');
  assert.equal(known.fields.load_characteristic.value.samples[0].rpm, 1000);
});
