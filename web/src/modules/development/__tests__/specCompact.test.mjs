// node --test: the compact specification — "torque OR power" requirements, and the data risks
// (stored values never lost, hidden or recomputed).  All values are made up.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as W from '../wizardState.ts';
import { productionFallback } from '../reviewPresentation.ts';
import { specBatteryChemistry, specBatteryFieldEdit, specBatteryPackEdit, specBatteryView, specChemistryType } from '../specBattery.ts';

const schema = JSON.parse(readFileSync(new URL('../../../../../src/motor_ai_sim/development/schema.json', import.meta.url), 'utf8'));
const field = (id) => schema.sections.flatMap((s) => s.fields ?? []).find((f) => f.id === id);
const prop = (fid, pid) => field(fid).properties.find((p) => p.id === pid);
const filled = (value) => ({ status: 'filled', value });
const allowed = (fid) => new Set(field(fid).properties.map((p) => p.id));

test('S1 needs a speed and a torque OR a power', () => {
  const s1 = field('1_4');
  assert.equal(W.fieldAnswerComplete(s1, filled({ torque_Nm: 12, speed_rpm: 3000 })), true);
  assert.equal(W.fieldAnswerComplete(s1, filled({ power_W: 2500, speed_rpm: 3000, power_basis: 'electrical_input' })), true);
  assert.equal(W.fieldAnswerComplete(s1, filled({ speed_rpm: 3000 })), false);
  assert.equal(W.fieldAnswerComplete(s1, filled({ power_W: 2500 })), false);
  assert.deepEqual(W.missingAnyGroups(s1, { speed_rpm: 3000 }), [['torque_Nm', 'power_W']]);
});

test('peak is optional; when given it needs a torque OR an output power', () => {
  const peak = field('1_5');
  assert.equal(W.fieldRequired(peak, { fields: {} }), false);
  assert.equal(W.fieldAnswerComplete(peak, filled({ output_power_W: 4000, duration_s: 30, period_s: 600 })), true);
  assert.equal(W.fieldAnswerComplete(peak, filled({ torque_Nm: { minimum: 20, maximum: 25 } })), true);
  assert.equal(W.fieldAnswerComplete(peak, filled({ duration_s: 30 })), false);
  // the whole spec stays complete with an empty peak row ("no peak")
  const counts = W.wizardCompleteness([{ id: '1', fields: [peak] }], { fields: { '1_5': { status: 'missing', value: { torque_Nm: '', output_power_W: '' } } } });
  assert.equal(counts.required, 0);
});

test('envelope, quantities and timeline accept any one value; battery only the nominal voltage; ambient only the maximum', () => {
  assert.equal(W.fieldAnswerComplete(field('4_1'), filled({ outer_diameter_mm: 80 })), true);
  assert.equal(W.fieldAnswerComplete(field('4_1'), filled({ length_mm: 40 })), true);
  assert.equal(W.fieldAnswerComplete(field('4_1'), filled({})), false);
  assert.equal(W.fieldAnswerComplete(field('6_1'), filled({ prototypes: 3 })), true);
  assert.equal(W.fieldAnswerComplete(field('6_1'), filled({ prototypes: '', pre_series: '' })), false);
  assert.equal(W.fieldAnswerComplete(field('6_2'), filled({ prototype_date: '2031-02-03' })), true);
  assert.equal(W.fieldAnswerComplete(field('2_1'), filled({ voltage_nominal_V: 48 })), true);
  assert.equal(W.fieldAnswerComplete(field('2_1'), filled({ chemistry: 'LiPo', series_cells: 12 })), false);
  assert.equal(W.fieldAnswerComplete(field('3_2'), filled({ temperature_max_C: 45 })), true);
  assert.equal(W.fieldAnswerComplete(field('3_2'), filled({ temperature_min_C: -30, temperature_max_C: 45 })), true);
});

test('the derived S1 power never drops the stated power or its basis', () => {
  const point = W.withS1DerivedPower({ speed_rpm: 2000, power_W: 1500, power_basis: 'dc_output' });
  assert.equal(point.power_W, 1500); assert.equal(point.power_basis, 'dc_output'); assert.equal(point.shaft_power_W, undefined);
  const both = W.withS1DerivedPower({ torque_Nm: 5, speed_rpm: 2000, power_W: 1500 });
  assert.equal(both.power_W, 1500); assert.equal(both.shaft_power_W, 5 * 2 * Math.PI * 2000 / 60);
});

test('"not known yet" keeps the stored value; undoing it restores the filled status', () => {
  const old = filled({ maximum_rpm: 9000, overspeed_rpm: { minimum: 10000, maximum: 11000 }, proof_or_runaway: '1.2 x max for 2 min' });
  const unknown = W.answerWithUnknown(field('1_6'), old, true);
  assert.equal(unknown.status, 'not_known_yet'); assert.deepEqual(unknown.value, old.value);
  const back = W.answerWithUnknown(field('1_6'), unknown, false);
  assert.equal(back.status, 'filled'); assert.deepEqual(back.value, old.value);
  assert.equal(W.answerWithUnknown(field('1_6'), { status: 'not_known_yet', value: { overspeed_rpm: 10000 } }, false).status, 'missing');
});

test('stored ranges keep the range editor; single numbers keep the number editor', () => {
  const productionSeries = schema.sections.find((s) => s.id === 'production').fields.find((f) => f.id === 'quantities').properties.find((p) => p.id === 'series_per_year');
  for (const shape of [prop('1_5', 'duration_s'), prop('1_8', 'torque_ripple_pct_pkpk'), prop('1_5', 'output_power_W'), prop('1_8', 'cogging_torque_Nm'), productionSeries, prop('1_6', 'runaway_rpm')]) {
    assert.equal(W.usesRangeEditor(shape, { minimum: 1, maximum: 2 }), true, shape.id);
    assert.equal(W.usesRangeEditor(shape, { minimum: 1, maximum: null }), true, shape.id);
    assert.equal(W.usesRangeEditor(shape, 7), false, shape.id);
  }
  assert.equal(W.usesRangeEditor(prop('1_5', 'period_s'), { minimum: 1, maximum: 2 }), false);
});

test('empty 6_1 / 6_2 show the production plan read-only and never copy it', () => {
  const draft = { fields: { '6_1': { status: 'missing', value: { prototypes: '' } } }, production: { quantities: filled({ prototypes: 4, series_per_year: { minimum: 100, maximum: 500 } }), timeline: filled({ series_date: '2032-01-01' }) } };
  const before = JSON.stringify(draft);
  assert.deepEqual(productionFallback(draft, '6_1'), [{ key: 'prototypes', value: 4 }, { key: 'series_per_year', value: { minimum: 100, maximum: 500 } }]);
  assert.deepEqual(productionFallback(draft, '6_2'), [{ key: 'series_date', value: '2032-01-01' }]);
  assert.equal(JSON.stringify(draft), before);
  assert.deepEqual(productionFallback({ ...draft, fields: { '6_1': filled({ pre_series: 10 }) } }, '6_1'), []);
  assert.deepEqual(productionFallback(draft, '1_1'), []);
});

test('battery: an unknown chemistry is shown and selects nothing until the user picks', () => {
  for (const text of ['LiPo', 'NMC, Example cell 21700']) {
    const view = specBatteryView({ chemistry: text, series_cells: 12 });
    assert.equal(view.selected, null); assert.equal(view.chemistryNote, text);
  }
  assert.equal(specChemistryType('NMC'), 'NMC'); assert.equal(specChemistryType('LiFePO4'), 'LFP'); assert.equal(specChemistryType('lfp'), 'LFP');
  const picked = specBatteryChemistry({ chemistry: 'LiPo', series_cells: 10, cell_voltage_max_V: 4.35, voltage_max_V: 43 }, 'NMC', allowed('2_1'));
  assert.equal(picked.chemistry, 'NMC');
  assert.equal(picked.cell_voltage_max_V, 4.35);          // stored cell voltage kept
  assert.equal(picked.voltage_max_V, 43);                 // stored pack voltage kept
  assert.equal(picked.cell_voltage_nominal_V, 3.7);       // empty values filled from the preset
  assert.equal(picked.voltage_nominal_V, 37);
});

test('battery: stored pack voltages are never recomputed on load', () => {
  const stored = { chemistry: 'NMC', series_cells: 96, voltage_min_V: 317, voltage_nominal_V: 356, voltage_max_V: 403 };
  const view = specBatteryView(stored);
  assert.deepEqual(view.pack, { min: 317, nom: 356, max: 403 });
  assert.equal(view.bat.cells, 96);
  // blank cell voltages are SHOWN as pack ÷ cells (owner 2026-10-25), the stored answer stays as it was
  assert.deepEqual([view.bat.min, view.bat.nom, view.bat.max], [3.302, 3.708, 4.198]); assert.equal(view.bat.draft, undefined);
  assert.deepEqual(stored, { chemistry: 'NMC', series_cells: 96, voltage_min_V: 317, voltage_nominal_V: 356, voltage_max_V: 403 });
});

test('battery: cells x cell voltage = pack voltage, either way round', () => {
  const A = allowed('2_1');
  const erium = { chemistry: 'LiPo', series_cells: 192, voltage_min_V: 633, voltage_nominal_V: 710, voltage_max_V: 806 };
  const packEdit = specBatteryPackEdit(erium, 'nom', 720, A);
  assert.equal(packEdit.voltage_nominal_V, 720); assert.equal(packEdit.cell_voltage_nominal_V, 3.75);
  assert.equal(specBatteryPackEdit({ voltage_min_V: 1 }, 'min', 600, A).cell_voltage_min_V, undefined);   // no cell count, nothing derived
  const cells = specBatteryFieldEdit({ voltage_min_V: 330, voltage_nominal_V: 370, voltage_max_V: 420 }, 'cells', '100', A);
  assert.deepEqual([cells.cell_voltage_min_V, cells.cell_voltage_nominal_V, cells.cell_voltage_max_V], [3.3, 3.7, 4.2]);
  assert.deepEqual([cells.voltage_min_V, cells.voltage_nominal_V, cells.voltage_max_V], [330, 370, 420]);   // the stated pack is kept exactly
});

test('battery: pack-only data displays, and only cell edits derive the pack values they determine', () => {
  const packOnly = specBatteryView({ voltage_nominal_V: 400 });
  assert.equal(packOnly.pack.nom, 400); assert.equal(packOnly.bat.draft.cells, '');
  const stored = { series_cells: 96, voltage_min_V: 317, voltage_nominal_V: 356, voltage_max_V: 403 };
  const minEdit = specBatteryFieldEdit(stored, 'min', '3.3', allowed('2_1'));
  assert.equal(minEdit.cell_voltage_min_V, 3.3); assert.equal(minEdit.voltage_min_V, 316.8);
  assert.equal(minEdit.voltage_nominal_V, 356); assert.equal(minEdit.voltage_max_V, 403);
  const cellsEdit = specBatteryFieldEdit({ ...minEdit, cell_voltage_nominal_V: 3.7, cell_voltage_max_V: 4.2 }, 'cells', '10', allowed('2_1'));
  assert.equal(cellsEdit.series_cells, 10);
  assert.deepEqual([cellsEdit.voltage_min_V, cellsEdit.voltage_nominal_V, cellsEdit.voltage_max_V], [33, 37, 42]);
  const cleared = specBatteryFieldEdit(stored, 'cells', '', allowed('2_1'));
  assert.equal(cleared.series_cells, ''); assert.equal(cleared.voltage_nominal_V, 356);
  // a schema without per-cell voltages (v1) never receives keys it would reject
  const v1 = new Set(['chemistry', 'series_cells', 'voltage_min_V', 'voltage_nominal_V', 'voltage_max_V']);
  assert.deepEqual(specBatteryFieldEdit({ series_cells: 4 }, 'nom', '3.7', v1), { series_cells: 4 });
  assert.ok(Object.keys(specBatteryChemistry({}, 'LFP', v1)).every((k) => v1.has(k)));
});

test('free text that carries structure stays text, new properties start empty', () => {
  for (const [fid, pid] of [['1_6', 'proof_or_runaway'], ['2_9', 'redundancy'], ['3_1', 'cooling_type']]) assert.equal(prop(fid, pid).type, 'string');
  assert.equal(field('1_2').type, 'string');
  assert.equal(prop('3_1', 'flow_rate').unit, 'L/min');
  assert.equal(prop('3_2', 'temperature_min_C').min, undefined);
  for (const [fid, pid] of [['1_4', 'power_W'], ['1_4', 'power_basis'], ['1_5', 'period_s'], ['1_6', 'runaway_rpm'], ['2_1', 'cell_voltage_nominal_V'], ['2_3', 'charge_A'], ['2_4', 'count'], ['2_9', 'independent_sets']]) {
    assert.equal(prop(fid, pid).required, false, `${fid}.${pid}`);
    assert.ok(prop(fid, pid).label.en && prop(fid, pid).label['zh-CN'], `${fid}.${pid} label`);
  }
  assert.deepEqual(prop('1_4', 'power_basis').options.map((o) => o.value), ['shaft', 'electrical_input', 'dc_output']);
  assert.ok(['turbine', 'robot_joint'].every((v) => field('load_type').options.some((o) => o.value === v && o.label['zh-CN'])));
});
