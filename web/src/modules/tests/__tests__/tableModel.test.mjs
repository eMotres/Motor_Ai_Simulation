// node --test — the Tests section's pure table model (formatting, delta bands, the measured | model | delta lines, pasted
// bench rows).  Imports tableModel.ts itself (Node >= 22.18 / 24; else skipped).  No network.
import test from 'node:test';
import assert from 'node:assert/strict';

let M = null;
try { M = await import('../tableModel.ts'); } catch { /* old Node */ }
const t = M ? test : test.skip;

t('values and deltas are formatted with sensible digits, missing = em dash', () => {
  assert.equal(M.fmtValue(1234.4), '1234');
  assert.equal(M.fmtValue(512.61), '512.6');
  assert.equal(M.fmtValue(19.424), '19.42');
  assert.equal(M.fmtValue(0.71674), '0.717');
  assert.equal(M.fmtValue(null), '—');
  assert.equal(M.fmtDelta(-2.88), '-2.9');
  assert.equal(M.fmtDelta(2), '+2.0');
  assert.equal(M.fmtDelta(undefined), '—');
});

t('units print as symbols', () => {
  assert.equal(M.unitLabel('mOhm'), 'mΩ');
  assert.equal(M.unitLabel('uH'), 'µH');
  assert.equal(M.unitLabel('C'), '°C');
  assert.equal(M.unitLabel('W/krpm^2'), 'W/krpm²');
  assert.equal(M.unitLabel('V'), 'V');
  assert.equal(M.unitLabel(undefined), '');
});

t('delta bands: 5 % ok, 15 % warn, beyond bad', () => {
  assert.equal(M.deltaLevel(-2.9), 'ok');
  assert.equal(M.deltaLevel(12), 'warn');
  assert.equal(M.deltaLevel(-30), 'bad');
  assert.equal(M.deltaLevel(null), null);
});

const row = (o) => ({ key: 'x', label: 'x', params: {}, unit: '', measured: null, model: null, delta_pct: null,
  status: 'info', extra: {}, ...o });

t('one table per test: group headers, rows, the empty info rows dropped, notes attached', () => {
  const cmp = { test_id: 'a', kind: 'parameters', model_available: true, groups: [
    { key: 'resistance', label: 'Resistance', params: {}, tip: 'resistance', rows: [
      row({ key: 'r_ll_mean', measured: 20.0, model: 19.424, delta_pct: -2.88, status: 'ok', extra: { remaining_mohm: 0.576 } }),
      row({ key: 'r_pair', measured: 20.1 }),
      row({ key: 'nothing' }),
    ] },
    { key: 'insulation', label: 'Insulation', params: {}, rows: [row({ key: 'surge', params: { result: 'ok' } })] },
    { key: 'empty', label: 'Empty', params: {}, rows: [row({})] },
  ] };
  const lines = M.tableLines(cmp);
  assert.deepEqual(lines.map((l) => l.type === 'group' ? `#${l.key}` : l.row.key), ['#resistance', 'r_ll_mean', 'r_pair', '#insulation', 'surge']);
  const r = lines[1];
  assert.equal(r.measured, '20.00'); assert.equal(r.model, '19.42'); assert.equal(r.delta, '-2.9'); assert.equal(r.level, 'ok');
  assert.deepEqual(r.note, { key: 'remaining', params: { mohm: '0.576' } });
  assert.deepEqual(M.summary(cmp), { compared: 1, worst: -2.88 });
  assert.deepEqual(M.tableLines(null), []);
});

t('a group with a speed per row is a series for the chart; resistance, inductance and one load point are table-only', () => {
  const emf = { key: 'emf_peak', label: 'EMF', params: {}, basis: 'magnets at 60 C, 3-D x0.950 (Stage A)', basis_key: 'emf_3d',
    basis_params: { mag: '60', k: '0.950', src: 'Stage A' }, rows: [
      row({ key: 'emf_peak', unit: 'V', params: { rpm: 3000 }, measured: 120, model: 128.2, delta_pct: 6.8, status: 'ok' }),
      row({ key: 'emf_peak', unit: 'V', params: { rpm: 1000 }, measured: 40, model: 42.7, delta_pct: 6.8, status: 'ok' }),
      row({ key: 'emf_peak', unit: 'V', params: { rpm: 2000 }, measured: 80, model: null, status: 'no_model' }),
    ] };
  const s = M.seriesOf(emf);
  assert.equal(s.unit, 'V');
  assert.deepEqual(s.points.map((p) => p.rpm), [1000, 2000, 3000]);                  // sorted by rpm
  assert.deepEqual(s.points[1], { rpm: 2000, measured: 80, model: null, delta: null });
  assert.equal(M.seriesOf({ key: 'one', label: 'x', params: {}, rows: [row({ params: { rpm: 3000 }, measured: 1 })] }), null);   // one speed only
  assert.equal(M.seriesOf({ key: 'resistance', label: 'R', params: {}, rows: [row({ params: { n: 3 }, measured: 20, model: 19 }),
    row({ params: { pair: '1-2' }, measured: 20 })] }), null);
  const load = { key: 'load_point', label: '3000 rpm', params: { rpm: 3000, current: 100 }, rows: [
    row({ key: 'torque', measured: 29, model: 30, status: 'ok' }), row({ key: 'eff', measured: 94, model: 95, status: 'ok' })] };
  const blocks = M.tableBlocks({ test_id: 'a', kind: 'free_run', model_available: true, groups: [emf, load] });
  assert.deepEqual(blocks.map((b) => [b.key, b.series !== null, b.lines.length]), [['emf_peak', true, 3], ['load_point', false, 2]]);
  assert.equal(blocks[0].basisKey, 'emf_3d'); assert.deepEqual(blocks[0].basisParams, { mag: '60', k: '0.950', src: 'Stage A' });
  assert.equal(blocks[1].basis, null);
});

t('row notes: unexplained loss, no model point (with the compute action), frequency flag', () => {
  assert.deepEqual(M.rowNote(row({ key: 'loss', extra: { unexplained_w: 412.4 } })), { key: 'unexplained', params: { w: '412.4' } });
  assert.equal(M.rowNote(row({ status: 'no_model', action: { kind: 'create_duty', body: {} } })).key, 'noModelCompute');
  assert.equal(M.rowNote(row({ status: 'no_model' })).key, 'noModel');
  assert.deepEqual(M.rowNote(row({ extra: { high_freq: true, freq_hz: 1000 } })), { key: 'freqFlag', params: { hz: 1000 } });
  assert.equal(M.rowNote(row({})), null);
});

t('pasted free-run rows in the slash format, "-" = not measured, a bad row reported by its line', () => {
  const p = M.parseRows('1000/140/80/28.3/40, 1500/240/120/42.4,\n4000/-/320/113.2, x/1', 'free_run');
  assert.deepEqual(p.rows, [
    { rpm: 1000, shaft_power_w: 140, v_ll_pp_v: 80, v_ll_rms_v: 28.3, temp_c: 40 },
    { rpm: 1500, shaft_power_w: 240, v_ll_pp_v: 120, v_ll_rms_v: 42.4 },
    { rpm: 4000, v_ll_pp_v: 320, v_ll_rms_v: 113.2 },
  ]);
  assert.equal(p.errors.length, 1);
  assert.equal(M.parseRows('1/2/3/4/5/6', 'free_run').errors.length, 1);        // more fields than columns
});

t('pasted load rows from a sheet: tabs or spaces, decimal comma', () => {
  const p = M.parseRows('3000\t29,5\t100\t48\t70\t93,1\n2000 10 50', 'load');
  assert.deepEqual(p.rows[0], { rpm: 3000, torque_nm: 29.5, i_phase_a: 100, v_dc_v: 48, i_dc_a: 70, eff_pct: 93.1 });
  assert.deepEqual(p.rows[1], { rpm: 2000, torque_nm: 10, i_phase_a: 50 });
  assert.equal(p.errors.length, 0);
});

t('rows round-trip to the slash text the editor shows', () => {
  const rows = [{ rpm: 1000, shaft_power_w: 140, v_ll_pp_v: 80 }, { rpm: 4000, v_ll_pp_v: 320, v_ll_rms_v: 113.2 }];
  const text = M.rowsToText(rows, 'free_run');
  assert.equal(text, '1000/140/80\n4000/-/320/113.2');
  assert.deepEqual(M.parseRows(text, 'free_run').rows, rows);
});

t('number lists and the newest-first order of a sub-menu', () => {
  assert.deepEqual(M.parseNumberList('20.0 / 20.1 / 19,9'), [20.0, 20.1, 19.9]);
  const tests = [{ id: 'a', kind: 'parameters', date: '2026-10-18' }, { id: 'b', kind: 'free_run', date: '2026-10-21' },
    { id: 'c', kind: 'parameters', date: '2026-10-26' }];
  assert.deepEqual(M.testsOfKind(tests, 'parameters').map((x) => x.id), ['c', 'a']);
});
