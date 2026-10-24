import test from 'node:test';
import assert from 'node:assert/strict';
import { developmentComparisonColumns, developmentComparisonRows } from '../comparisonRows.ts';
import { buildComparisonModel } from '../../../components/compare/comparisonModel.ts';

test('Development row model includes every linked variant and stored duty with absent metrics left missing', () => {
  const motors = [
    { die: 'Synthetic-A', config: 'cfg-1', duties: ['cruise'], results: [
      { duty: 'cruise', has_result: true, operating_point: { current_arms: 12, rpm: 800, gamma_deg: 20 }, comparison: { torque_nm: 4, p_loss_total_w: 9, loss_total_partial: true, demag: false, temperatures_c: { winding: 77, sleeve: 66 } } },
      { duty: 'climb', has_result: true, operating_point: { current_arms: 15, rpm: 700 }, comparison: { torque_nm: 5, efficiency_pct: 0, p_loss_total_w: 12 } },
    ] },
    { die: 'Synthetic-B', config: 'cfg-2', results: [{ duty: 'cruise', has_result: true, comparison: { torque_nm: 6, mass_kg: 0.4 } }] },
    { die: 'Synthetic-C', config: 'cfg-3', duties: ['pending'], results: [] },
  ];
  const rows = developmentComparisonRows(motors);
  assert.equal(rows.length, 4);
  assert.deepEqual(rows.map((r) => r.inputs.duty), ['cruise', 'climb', 'cruise', 'pending']);
  assert.equal(rows.filter((r) => r.hasResult).length, 3);
  assert.equal(rows[0].inputs.current, 12);
  assert.equal(rows[0].values.demag, false);
  assert.equal(rows[0].values.mass, undefined);
  assert.equal(rows[1].values.efficiency, 0);
  assert.equal(rows[0].values.coilTemp, 77);
  assert.equal(rows[0].values['temperature:sleeve'], 66);
  assert.equal(rows[3].hasResult, false);

  const t = (key, args) => args?.part ? `${key}:${args.part}` : key === 'comparison.no' ? 'No' : key === 'comparison.yes' ? 'Yes' : key;
  const columns = developmentComparisonColumns(t, rows);
  const byKey = Object.fromEntries(columns.map((column) => [column.key, column]));
  assert.ok(byKey.pMech);
  assert.ok(byKey['temperature:sleeve']);
  assert.equal(byKey['temperature:sleeve'].label, 'comparison.componentTemp:comparison.temperaturePart.sleeve');
  assert.equal(columns.some((column) => column.key === 'temperature:winding'), false, 'fixed winding temperature is not duplicated');
  assert.equal(byKey.demag.format(rows[0].values.demag, rows[0]), 'No');
  assert.equal(byKey.mass.format(rows[0].values.mass, rows[0]), '-');
  assert.equal(byKey.efficiency.format(rows[1].values.efficiency, rows[1]), '0');
  assert.equal(byKey.losses.format(rows[0].values.losses, rows[0]), '9*');
  assert.equal(byKey.losses.title(rows[0].values.losses, rows[0]), 'comparison.partialLosses');
  assert.deepEqual(byKey.losses.rankValues(rows).filter((value) => value !== undefined), [12]);
  assert.equal(byKey.losses.noColor(rows[0], rows), true);

  const activeMass = { ...rows[0], values: { ...rows[0].values, mass: 1, torqueMass: 4, massPartial: true } };
  const noMassPlaceholder = { ...rows[3], values: { ...rows[3].values, mass: undefined, torqueMass: undefined, massPartial: false } };
  assert.deepEqual(byKey.mass.rankValues([activeMass, noMassPlaceholder]), [1]);
  assert.deepEqual(byKey.torqueMass.rankValues([activeMass, noMassPlaceholder]), [4]);
  assert.equal(byKey.mass.noColor(activeMass, [activeMass, noMassPlaceholder]), false);
  assert.equal(byKey.torqueMass.noColor(activeMass, [activeMass, noMassPlaceholder]), false);
});

test('unsolved rows cannot display or rank comparison metrics even if a stale payload includes them', () => {
  const rows = developmentComparisonRows([{ die: 'Synthetic', config: 'cfg', results: [
    { duty: 'target', has_result: false, comparison: { torque_nm: 12, power_w: 1200, v_dc_v: 48 } },
    { duty: 'legacy-supply', has_result: true, comparison: { v_dc_v: 48 } },
    { duty: 'solved', has_result: true, comparison: { torque_nm: 6, power_w: 400, v_dc_required_v: 62 } },
  ] }]);
  assert.equal(rows[0].values.torque, undefined);
  assert.equal(rows[0].values.power, undefined);
  assert.equal(rows[0].values.vDc, undefined);
  assert.equal(rows[1].values.vDc, undefined, 'legacy supplied-bus field is not needed voltage');
  const columns = Object.fromEntries(developmentComparisonColumns((key) => key, rows).map((column) => [column.key, column]));
  const model = buildComparisonModel(rows, Object.values(columns));
  // torque / power are key-point columns now (unranked); a stale payload of an unsolved row still never shows
  assert.equal(rows[0].values.keyTorque, undefined);
  assert.equal(rows[0].values.keyPowerKw, undefined);
  assert.equal(rows[2].values.keyTorque, 6);
  assert.deepEqual(model.extents.vDc, { min: 62, max: 62 });
});


test('delta PWM model-branch peaks are blank, unranked, and explained', () => {
  const motors = [{ die: 'Synthetic-delta', config: 'cfg', results: [
    { duty: 'delta-pwm', has_result: true, comparison: {
      v_line_peak_v: 55.426, line_peak_physical: false,
      line_peak_provenance: 'delta_equivalent_star', line_peak_reason: 'model_branch_peak',
    } },
    { duty: 'other-flagged', has_result: true, comparison: {
      v_line_peak_v: 51, line_peak_provenance: 'model_branch_peak',
    } },
  ] }];
  const rows = developmentComparisonRows(motors);
  assert.equal(rows[0].values.vLine, null, 'a flagged numeric payload is cleared in the web row model');
  assert.equal(rows[1].values.vLine, null, 'unknown nonphysical provenance is fail-closed');
  const columns = developmentComparisonColumns((key) => key, rows);
  const linePeak = columns.find((column) => column.key === 'vLine');
  const model = buildComparisonModel(rows, columns);
  assert.ok(model.resultColumns.some((column) => column.key === 'vLine'), 'column remains present when all rows are blocked');
  assert.equal(model.extents.vLine, null, 'blocked values do not enter comparison extents');
  assert.equal(linePeak.value(rows[0]), null);
  assert.equal(linePeak.format(linePeak.value(rows[0]), rows[0]), '-');
  assert.equal(linePeak.title(linePeak.value(rows[0]), rows[0]), 'comparison.linePeakUnavailable');
  assert.deepEqual(linePeak.rankValues?.(rows) ?? rows.map((row) => linePeak.value(row)), [null, null]);
});

test('physical override and ordinary zero line peaks remain visible and rankable', () => {
  const rows = developmentComparisonRows([{ die: 'Synthetic', config: 'cfg', results: [
    { duty: 'physical-override', has_result: true, comparison: {
      v_line_peak_v: 48, line_peak_physical: true,
      line_peak_provenance: 'physical_terminal_peak', line_peak_source: 'summary',
    } },
    { duty: 'ordinary-zero', has_result: true, comparison: { v_line_peak_v: 0 } },
  ] }]);
  const columns = developmentComparisonColumns((key) => key, rows);
  const linePeak = columns.find((column) => column.key === 'vLine');
  const model = buildComparisonModel(rows, columns);
  assert.equal(rows[0].values.vLine, 48);
  assert.equal(rows[1].values.vLine, 0);
  assert.deepEqual(model.extents.vLine, { min: 0, max: 48 });
  assert.equal(linePeak.format(0, rows[1]), '0');
  assert.equal(linePeak.title(48, rows[0]), undefined);
});

test('history snapshots appear as distinct rows with history metadata', () => {
  const rows = developmentComparisonRows([
    { die: 'Synthetic-A', config: 'cfg', results: [{ duty: 'rated', has_result: true, comparison: { torque_nm: 3 } }] },
    { die: 'Synthetic-A', config: 'cfg', kind: 'history', snapshot_id: 'snapshot-1', note: 'point A', author_display_name: 'Engineer', created_at: '2026-10-23T00:00:00Z', results: [{ duty: 'rated', has_result: true, comparison: { torque_nm: 4 } }] },
    { die: 'Synthetic-A', config: 'cfg', kind: 'history', snapshot_id: 'snapshot-2', results: [{ duty: 'rated', has_result: true, comparison: { torque_nm: 5 } }] },
  ]);
  assert.equal(rows.length, 3);
  assert.deepEqual(rows.map((row) => row.id), ['motor/Synthetic-A/cfg/rated/0', 'snapshot-1/Synthetic-A/cfg/rated/0', 'snapshot-2/Synthetic-A/cfg/rated/0']);
  assert.deepEqual(rows.map((row) => row.history), [false, true, true]);
  assert.equal(rows[1].motor.author_display_name, 'Engineer');
  assert.equal(rows[1].values.torque, 4);
});

test('comparison row keeps a short variant label, keeps full note in metadata, and marks estimates', () => {
  const noteEn = 'A'.repeat(240), noteZh = '\u6ce8\u91ca'.repeat(120);
  const rows = developmentComparisonRows([
    { die: 'Synthetic', config: 'cfg', kind: 'history', snapshot_id: 'en', note: noteEn,
      results: [{ duty: 'rated', has_result: true, result_provenance: 'configure_interpolation', comparison: { torque_nm: 3 } }] },
    { die: 'Synthetic', config: 'cfg', kind: 'history', snapshot_id: 'zh', note: noteZh,
      results: [{ duty: 'rated', has_result: true, provenance: { kind: 'estimate', source: 'configure_interpolation' }, comparison: { torque_nm: 4 } }] },
    { die: 'Synthetic', config: 'cfg', kind: 'history', snapshot_id: 'solve', note: noteEn,
      results: [{ duty: 'rated', has_result: true, provenance: { kind: 'solve' }, comparison: { torque_nm: 5 } }] },
  ]);
  assert.equal(rows[0].variant, 'Synthetic / cfg');
  assert.equal(rows[1].variant, rows[0].variant);
  assert.equal(rows.every((row) => row.variant.length < 40), true);
  assert.deepEqual(rows.map((row) => row.estimate), [true, true, false]);
  assert.equal(rows[0].motor.note, noteEn);
  assert.equal(rows[1].motor.note, noteZh);
});

test('every row shows power, torque, speed, current and phase peak; targets until solved, no die/config copies', () => {
  const rows = developmentComparisonRows([{ die: 'D', config: 'C', results: [
    { duty: 'takeoff', has_result: false, operating_point: { current_arms: 213, rpm: 900, gamma_deg: 3.5, torque_nm: 1100 } },
    { duty: 'cruise', has_result: true, operating_point: { current_arms: 152, rpm: 600, torque_nm: 800 },
      comparison: { torque_nm: 805, power_w: 50580, efficiency_pct: 96, v_phase_peak_v: 301.2 } },
  ] }]);
  const columns = developmentComparisonColumns((key) => key, rows);
  const keys = columns.map((column) => column.key);
  assert.deepEqual(keys.slice(0, 7), ['duty', 'keyPowerKw', 'keyTorque', 'rpm', 'current', 'vPhase', 'gamma']);
  assert.equal(keys.includes('die'), false);
  assert.equal(keys.includes('config'), false);
  const byKey = Object.fromEntries(columns.map((column) => [column.key, column]));
  assert.equal(byKey.keyTorque.format(rows[0].values.keyTorque, rows[0]), '1100*');
  assert.equal(byKey.keyTorque.title(rows[0].values.keyTorque, rows[0]), 'comparison.targetValue');
  assert.equal(byKey.keyPowerKw.format(rows[0].values.keyPowerKw, rows[0]), '103.7*');
  assert.equal(byKey.vPhase.format(rows[0].values.vPhase, rows[0]), '-');
  assert.equal(byKey.keyTorque.format(rows[1].values.keyTorque, rows[1]), '805');
  assert.equal(byKey.keyPowerKw.format(rows[1].values.keyPowerKw, rows[1]), '50.6');
  assert.equal(byKey.vPhase.format(rows[1].values.vPhase, rows[1]), '301.2');
  assert.equal(rows[0].values.torque, undefined, 'targets never leak into the solved result columns');
});
