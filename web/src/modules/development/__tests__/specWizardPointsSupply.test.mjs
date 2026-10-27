// node --test: owner 2026-10-27 changes of the wizard - (1) torque / speed / power of an operating point are linked by P = T·ω,
// (2) ONE power supply block (battery or DC source) instead of a voltage per point, (3) the phase current limits and the phase
// count are not asked from the customer.  All values are made up.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { applyPointEdit, autoPowerUnit, displayPower, formatSig, parseDecimal, shaftPowerOf, torqueFromPower, usableSpeed } from '../components/specWizard/operatingPoints.ts';
import { legacyPointVoltages, legacyVoltageText, supplyEmpty, supplySummary, supplyType, withDcSourceV, withSupplyType } from '../components/specWizard/supply.ts';
import { buildWizardSteps } from '../components/specWizard/steps.ts';
import { reviewItems } from '../components/specWizard/reviewSummary.ts';
import { CONTINUOUS, blankKeyPoint, withKeyPoints } from '../keyPoints.ts';
import { batteryMaxV, controllerLimits, controllerLimitsLine } from '../controllerCatalog.ts';
import * as W from '../wizardState.ts';

const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const schema = JSON.parse(read('../../../../../src/motor_ai_sim/development/schema.json'));
const en = JSON.parse(read('../../../locales/en/development.json'));
const zh = JSON.parse(read('../../../locales/zh-CN/development.json'));
const fieldOf = (id) => schema.sections.flatMap((s) => s.fields ?? []).find((f) => f.id === id);
const filled = (value) => ({ status: 'filled', value });
const draft = (fields = {}) => ({ schema_version: 2, fields, mission_profile: [], production: {} });
const get = (obj, path) => path.split('.').reduce((o, k) => o?.[k], obj);
const tr = (key, args = {}) => { const v = get(en, key); return typeof v === 'string' ? v.replace(/\{(\w+)\}/g, (_, k) => String(args[k] ?? '')) : key; };
const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps * Math.max(1, Math.abs(b)), `${a} vs ${b}`);
const OMEGA = (rpm) => 2 * Math.PI * rpm / 60;

// ── 1. torque / speed / power ──────────────────────────────────────────────────────────────────────────────────────────
test('power edit recalculates torque, torque edit recalculates power, a speed edit keeps the torque', () => {
  const row = { torque_Nm: 10, speed_rpm: 3000 };
  const byPower = applyPointEdit(row, { kind: 'power', value: 5000 }, null);
  near(byPower.change.torque_Nm, 5000 / OMEGA(3000)); assert.equal(byPower.pending, null);
  near(shaftPowerOf(byPower.change.torque_Nm, 3000), 5000);                               // round trip
  const byTorque = applyPointEdit(row, { kind: 'torque', value: 20 }, null);
  assert.deepEqual(byTorque.change, { torque_Nm: 20 }); near(shaftPowerOf(20, 3000), 20 * OMEGA(3000));
  const bySpeed = applyPointEdit(row, { kind: 'speed', value: 6000 }, null);
  assert.deepEqual(bySpeed.change, { speed_rpm: 6000 });                                  // torque kept, power follows
  near(shaftPowerOf(10, 6000), 10 * OMEGA(6000));
});

test('a power typed before the speed waits; the speed then gives the torque (only for an empty torque)', () => {
  const empty = { torque_Nm: null, speed_rpm: null };
  const first = applyPointEdit(empty, { kind: 'power', value: 1500 }, null);
  assert.deepEqual(first, { change: {}, pending: 1500 });
  assert.equal(displayPower(empty, first.pending), 1500);
  const next = applyPointEdit(empty, { kind: 'speed', value: 1500 }, first.pending);
  near(next.change.torque_Nm, 1500 / OMEGA(1500)); assert.equal(next.change.speed_rpm, 1500); assert.equal(next.pending, null);
  // a point that already has a torque keeps it when the speed arrives
  const kept = applyPointEdit({ torque_Nm: 4, speed_rpm: null }, { kind: 'speed', value: 1500 }, 1500);
  assert.deepEqual(kept.change, { speed_rpm: 1500 }); assert.equal(kept.pending, null);
});

test('no recalculation and no NaN for an empty, zero, negative or non-finite speed or power', () => {
  for (const rpm of [null, 0, -100, NaN, Infinity]) {
    assert.equal(usableSpeed(rpm), false);
    assert.equal(torqueFromPower(1000, rpm), null);
    const r = applyPointEdit({ torque_Nm: 7, speed_rpm: rpm }, { kind: 'power', value: 1000 }, null);
    assert.deepEqual(r.change, {}); assert.equal(r.pending, 1000);
  }
  for (const bad of [-5, NaN, Infinity]) {
    const r = applyPointEdit({ torque_Nm: 7, speed_rpm: 1000 }, { kind: 'power', value: bad }, 12);
    assert.deepEqual(r.change, {}); assert.equal(r.pending, 12);
  }
  assert.equal(torqueFromPower(Number.MAX_VALUE, 1e-300), null);                          // overflow writes nothing
  assert.equal(shaftPowerOf(NaN, 1000), null);
});

test('clearing the power clears the torque it came from; power 0 gives torque 0; a negative stored torque keeps its sign', () => {
  assert.deepEqual(applyPointEdit({ torque_Nm: 7, speed_rpm: 1000 }, { kind: 'power', value: null }, null).change, { torque_Nm: null });
  assert.deepEqual(applyPointEdit({ torque_Nm: 7, speed_rpm: null }, { kind: 'power', value: null }, 30), { change: {}, pending: null });
  assert.deepEqual(applyPointEdit({ torque_Nm: 7, speed_rpm: 1000 }, { kind: 'power', value: 0 }, null).change, { torque_Nm: 0 });
  const generator = applyPointEdit({ torque_Nm: -3, speed_rpm: 1000 }, { kind: 'power', value: 600 }, null);
  near(generator.change.torque_Nm, -600 / OMEGA(1000));
  near(displayPower({ torque_Nm: generator.change.torque_Nm, speed_rpm: 1000 }, null), 600);   // the box shows the magnitude
});

test('typed text parses to a number, an empty box or a half-typed value; display keeps sensible digits only', () => {
  assert.equal(parseDecimal('12.5'), 12.5); assert.equal(parseDecimal('12,5'), 12.5); assert.equal(parseDecimal('-3'), -3); assert.equal(parseDecimal('1e3'), 1000);
  assert.equal(parseDecimal(''), 'empty'); assert.equal(parseDecimal('  '), 'empty');
  for (const half of ['-', '.', '1e', '1e-', 'abc', '1.2.3']) assert.equal(parseDecimal(half), 'partial', half);
  assert.equal(formatSig(15.9154943091895), '15.9155'); assert.equal(formatSig(5000.0000000001), '5000'); assert.equal(formatSig(1234567), '1234567');
  assert.equal(formatSig(0), '0'); assert.equal(formatSig(null), ''); assert.equal(formatSig(NaN), '');
  assert.equal(autoPowerUnit(999), 'W'); assert.equal(autoPowerUnit(1000), 'kW'); assert.equal(autoPowerUnit(null), 'W');
});

test('the stored point keeps full precision and every other stored key (including an old per-point voltage); load changes nothing', () => {
  const rows = [{ ...blankKeyPoint('Takeoff'), torque_Nm: 10, speed_rpm: 3000, duration_s: CONTINUOUS, voltage_V: 48 }];
  const before = JSON.stringify(rows);
  const edited = applyPointEdit(rows[0], { kind: 'power', value: 2500 }, null);
  const d = withKeyPoints(draft(), rows.map((r) => ({ ...r, ...edited.change })));
  const stored = d.fields.key_points.value[0];
  assert.equal(stored.voltage_V, 48);                                                     // hidden in the UI, untouched in the data
  assert.equal(stored.torque_Nm, 2500 / OMEGA(3000));                                     // full precision, not the displayed 7.95775
  assert.equal(JSON.stringify(rows), before);
  assert.deepEqual(Object.keys(edited.change), ['torque_Nm']);                            // no power property is written
});

// ── 2. power supply ────────────────────────────────────────────────────────────────────────────────────────────────────
test('schema: 2_1 gains supply_type and dc_source_V only (additive); the battery properties are unchanged', () => {
  const f = fieldOf('2_1');
  assert.equal(schema.schema_version, 2);
  const ids = f.properties.map((p) => p.id);
  assert.deepEqual(ids.slice(0, 8), ['chemistry', 'series_cells', 'voltage_min_V', 'voltage_nominal_V', 'voltage_max_V', 'cell_voltage_min_V', 'cell_voltage_nominal_V', 'cell_voltage_max_V']);
  assert.deepEqual(ids.slice(8), ['supply_type', 'dc_source_V']);
  assert.deepEqual(f.properties.find((p) => p.id === 'supply_type').options.map((o) => o.value), ['battery', 'dc_source']);
  for (const p of f.properties.filter((x) => ['supply_type', 'dc_source_V'].includes(x.id))) { assert.ok(p.label.en && p.label['zh-CN']); }
  assert.ok(f.short_label.en === 'Power supply' && f.short_label['zh-CN']);
});

test('completeness: a battery (as before) or a DC source voltage completes the block; neither half-way', () => {
  const f = fieldOf('2_1'), done = (v) => W.fieldAnswerComplete(f, filled(v));
  assert.equal(done({ voltage_nominal_V: 48 }), true);                                    // earlier answers stay complete
  assert.equal(done({ chemistry: 'NMC', series_cells: 192, voltage_min_V: 633, voltage_nominal_V: 710, voltage_max_V: 806 }), true);
  assert.equal(done({ chemistry: 'NMC', series_cells: 192 }), false);                    // cells alone are not a voltage
  assert.equal(done({ supply_type: 'dc_source', dc_source_V: 48 }), true);
  assert.equal(done({ supply_type: 'dc_source' }), false);
  assert.equal(done({ supply_type: 'dc_source', dc_source_V: '' }), false);
  assert.equal(done({ supply_type: 'dc_source', dc_source_V: 0 }), false);                // not a supply
  assert.equal(done({ supply_type: 'dc_source', voltage_nominal_V: 710 }), false);        // the leftover battery does not complete a DC source
  assert.equal(done({ supply_type: 'dc_source', voltage_nominal_V: 710, dc_source_V: 48 }), true);
  assert.equal(done({ supply_type: 'battery', voltage_nominal_V: 710, dc_source_V: 48 }), true);
  assert.equal(done({ supply_type: 'battery', dc_source_V: 48 }), false);                 // a battery needs its pack voltage
  const nominal = f.properties.find((p) => p.id === 'voltage_nominal_V'), dc = f.properties.find((p) => p.id === 'dc_source_V');
  assert.equal(W.propertyRequired(nominal, {}), true); assert.equal(W.propertyRequired(nominal, { supply_type: 'dc_source' }), false);
  assert.equal(W.propertyRequired(dc, {}), false); assert.equal(W.propertyRequired(dc, { supply_type: 'dc_source' }), true);
});

test('the block helpers keep every other stored value; battery is the default; empty reads empty', () => {
  const stored = { chemistry: 'NMC', series_cells: 192, voltage_nominal_V: 710, dc_source_V: 48 };
  assert.equal(supplyType(stored), 'battery'); assert.equal(supplyType({ supply_type: 'dc_source' }), 'dc_source'); assert.equal(supplyType(null), 'battery');
  assert.deepEqual(withSupplyType(stored, 'dc_source'), { ...stored, supply_type: 'dc_source' });
  assert.deepEqual(withDcSourceV(stored, 24), { ...stored, supply_type: 'dc_source', dc_source_V: 24 });
  assert.equal(withDcSourceV(stored, NaN).dc_source_V, ''); assert.equal(withDcSourceV(stored, null).dc_source_V, '');
  assert.deepEqual(stored, { chemistry: 'NMC', series_cells: 192, voltage_nominal_V: 710, dc_source_V: 48 });
  assert.equal(supplyEmpty({}), true); assert.equal(supplyEmpty({ chemistry: '', series_cells: null }), true); assert.equal(supplyEmpty({ supply_type: 'battery' }), false);
});

test('Review: ONE line - "Battery 192S NMC, 633 / 710 / 806 V" or "DC source 48 V"', () => {
  assert.equal(supplySummary({ chemistry: 'NMC', series_cells: 192, voltage_min_V: 633, voltage_nominal_V: 710, voltage_max_V: 806 }, tr), 'Battery 192S NMC, 633 / 710 / 806 V');
  assert.equal(supplySummary({ chemistry: 'LiFePO4', series_cells: 100, cell_voltage_nominal_V: 3.2 }, tr), 'Battery 100S LiFePO4, 320 V');
  assert.equal(supplySummary({ voltage_nominal_V: 22.2 }, tr), 'Battery 22.2 V');
  assert.equal(supplySummary({ supply_type: 'dc_source', dc_source_V: 48 }, tr), 'DC source 48 V');
  assert.equal(supplySummary({ supply_type: 'dc_source' }, tr), ''); assert.equal(supplySummary({}, tr), '');
  const supply = buildWizardSteps(schema).find((s) => s.id === 'supply');
  const items = (v) => reviewItems(supply, draft({ '2_1': filled(v) }), { lang: 'en', t: tr, profile: 'industrial' }).filter((i) => i.id === '2_1');
  assert.deepEqual(items({ supply_type: 'dc_source', dc_source_V: 48 }).map((i) => i.value), ['DC source 48 V']);
  assert.equal(items({ chemistry: 'NMC', series_cells: 192, voltage_min_V: 633, voltage_nominal_V: 710, voltage_max_V: 806 }).length, 1);
});

test('the per-point voltage is no longer shown; an old one is offered once as a hint while the supply block is empty', () => {
  const rows = [{ ...blankKeyPoint('Takeoff'), voltage_V: 48 }, { ...blankKeyPoint('Cruise') }, { ...blankKeyPoint('Peak'), voltage_V: 50 }];
  const d = withKeyPoints(draft(), rows);
  assert.deepEqual(legacyPointVoltages(d), [{ name: 'Takeoff', volts: 48 }, { name: 'Peak', volts: 50 }]);
  assert.equal(legacyVoltageText(legacyPointVoltages(d)), '48 V (Takeoff), 50 V (Peak)');
  assert.deepEqual(legacyPointVoltages(draft()), []);
  const src = read('../components/specWizard/OperatingStep.tsx');
  assert.doesNotMatch(src, /voltage_V|wizard\.op\.voltage/);                                // no per-point voltage input
  assert.equal(fieldOf('key_points').items.properties.find((p) => p.id === 'voltage_V').ui_hidden, true);   // kept in the data
});

test('a DC source feeds the controller supply check like a pack', () => {
  assert.equal(batteryMaxV(draft({ '2_1': filled({ supply_type: 'dc_source', dc_source_V: 48, voltage_max_V: 806 }) })), 48);
  assert.equal(batteryMaxV(draft({ '2_1': filled({ voltage_max_V: 806 }) })), 806);
});

// ── 3. phase currents come from the controller ─────────────────────────────────────────────────────────────────────────
test('2_6 / 2_9 are never required and the customer wizard does not ask them', () => {
  for (const id of ['2_6', '2_9']) {
    const f = fieldOf(id);
    assert.ok(!f.required);
    assert.ok(f.properties.every((p) => !p.required), id);
    assert.ok(!buildWizardSteps(schema).some((s) => s.fields.some((x) => x.id === id)), id);
  }
  const all = buildWizardSteps(schema);
  assert.equal(W.wizardCompleteness(all, draft()).total_required, W.wizardCompleteness(buildWizardSteps(schema, undefined, { staff: true }), draft()).total_required);
  // a half-filled stored 2_6 (a catalogue pre-fill) is complete, not an error
  assert.equal(W.fieldAnswerComplete(fieldOf('2_6'), filled({ continuous_Arms: 365 })), true);
});

test('the controller line: stated values from the linked catalogue entry, else the note that we set them', () => {
  const entry = { id: 'x', display_name: 'DTI HV-550', source: { document: 'DTI datasheet' }, prefill: { '2_6.continuous_Arms': { value: 364.9 }, '2_6.peak_Arms': { value: 390 }, '2_6.peak_duration_s': { value: 30 } }, phases: { phase_count: { value: 3 } } };
  const limits = controllerLimits(entry);
  assert.deepEqual([limits.continuous, limits.peak, limits.peakDuration, limits.phases, limits.source], [364.9, 390, 30, 3, 'DTI datasheet']);
  assert.equal(controllerLimitsLine(limits, tr), 'From the controller: max 364.9 A rms continuous, 390 A peak for 30 s, 3 phases');
  const partial = controllerLimits({ ...entry, prefill: { '2_6.peak_Arms': { value: 390 } }, phases: {} });
  assert.equal(controllerLimitsLine(partial, tr), 'From the controller: 390 A peak');
  assert.equal(controllerLimits({ ...entry, prefill: {}, phases: {} }), null); assert.equal(controllerLimits(undefined), null);
  assert.match(en.wizard.controllerLimits.none, /simulation/);
});

// ── locales and source guards ──────────────────────────────────────────────────────────────────────────────────────────
test('EN and zh-CN carry every new string; no leftover keys of the removed line and input', () => {
  for (const k of ['wizard.op.power', 'wizard.op.powerGenerator', 'wizard.op.linkTip', 'wizard.supply.battery', 'wizard.supply.dc_source', 'wizard.supply.reviewDc', 'wizard.supply.legacyPoint',
    'wizard.controllerLimits.from', 'wizard.controllerLimits.continuous', 'wizard.controllerLimits.peak', 'wizard.controllerLimits.peakFor', 'wizard.controllerLimits.phases', 'wizard.controllerLimits.join', 'wizard.controllerLimits.none', 'wizard.controllerLimits.tip']) {
    assert.ok(get(en, k) && get(zh, k), k);
    assert.equal((get(en, k).match(/\{\w+\}/g) ?? []).sort().join(), (get(zh, k).match(/\{\w+\}/g) ?? []).sort().join(), k);
  }
  for (const k of ['wizard.op.derived', 'wizard.op.derivedGenerator', 'wizard.op.voltage']) assert.equal(get(en, k), undefined, k);
  assert.ok(/[一-鿿]/.test(zh.wizard.controllerLimits.none));
});

test('every touched source file still parses (a raw newline inside a string literal breaks the production build)', () => {
  const require = createRequire(import.meta.url);
  let esbuild;
  try { esbuild = require('esbuild'); } catch { return; }                                  // no esbuild in this checkout: the vite build is the gate
  for (const name of ['OperatingStep.tsx', 'operatingPoints.ts', 'ui.tsx', 'useSpecFields.tsx', 'supply.ts', 'reviewSummary.ts', 'steps.ts', 'StepBody.tsx']) {
    const loader = name.endsWith('x') ? 'tsx' : 'ts';
    assert.doesNotThrow(() => esbuild.transformSync(read(`../components/specWizard/${name}`), { loader }), name);
  }
  for (const [rel, loader] of [['../controllerCatalog.ts', 'ts'], ['../wizardState.ts', 'ts'], ['../../../components/compare/BatteryPanel.tsx', 'tsx'], ['../../../components/compare/PointsConfigure.tsx', 'tsx']]) {
    assert.doesNotThrow(() => esbuild.transformSync(read(rel), { loader }), rel);
  }
});
