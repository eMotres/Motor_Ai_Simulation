// node --test — the Tests section's pure table model (formatting, delta bands, the measured | model | delta lines, pasted
// bench rows).  Imports tableModel.ts itself (Node >= 22.18 / 24; else skipped).  No network.
import test from 'node:test';
import assert from 'node:assert/strict';

let M = null;
try { M = await import('../tableModel.ts'); } catch { /* old Node */ }
const t = M ? test : test.skip;

t('values and deltas are formatted with sensible digits, missing = em dash', () => {
  assert.equal(M.fmtValue(1286.4), '1286');
  assert.equal(M.fmtValue(513.61), '513.6');
  assert.equal(M.fmtValue(17.2946), '17.29');
  assert.equal(M.fmtValue(0.67457), '0.675');
  assert.equal(M.fmtValue(null), '—');
  assert.equal(M.fmtDelta(-4.0967), '-4.1');
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
  assert.equal(M.deltaLevel(-4.1), 'ok');
  assert.equal(M.deltaLevel(12), 'warn');
  assert.equal(M.deltaLevel(-30), 'bad');
  assert.equal(M.deltaLevel(null), null);
});

const row = (o) => ({ key: 'x', label: 'x', params: {}, unit: '', measured: null, model: null, delta_pct: null,
  status: 'info', extra: {}, ...o });

t('one table per test: group headers, rows, the empty info rows dropped, notes attached', () => {
  const cmp = { test_id: 'a', kind: 'parameters', model_available: true, groups: [
    { key: 'resistance', label: 'Resistance', params: {}, tip: 'resistance', rows: [
      row({ key: 'r_ll_mean', measured: 18.0333, model: 17.2946, delta_pct: -4.0967, status: 'ok', extra: { remaining_mohm: 0.7387 } }),
      row({ key: 'r_pair', measured: 18.13 }),
      row({ key: 'nothing' }),
    ] },
    { key: 'insulation', label: 'Insulation', params: {}, rows: [row({ key: 'surge', params: { result: 'ok' } })] },
    { key: 'empty', label: 'Empty', params: {}, rows: [row({})] },
  ] };
  const lines = M.tableLines(cmp);
  assert.deepEqual(lines.map((l) => l.type === 'group' ? `#${l.key}` : l.row.key), ['#resistance', 'r_ll_mean', 'r_pair', '#insulation', 'surge']);
  const r = lines[1];
  assert.equal(r.measured, '18.03'); assert.equal(r.model, '17.29'); assert.equal(r.delta, '-4.1'); assert.equal(r.level, 'ok');
  assert.deepEqual(r.note, { key: 'remaining', params: { mohm: '0.739' } });
  assert.deepEqual(M.summary(cmp), { compared: 1, worst: -4.0967 });
  assert.deepEqual(M.tableLines(null), []);
});

t('row notes: unexplained loss, no model point (with the compute action), frequency flag', () => {
  assert.deepEqual(M.rowNote(row({ key: 'loss', extra: { unexplained_w: 412.4 } })), { key: 'unexplained', params: { w: '412.4' } });
  assert.equal(M.rowNote(row({ status: 'no_model', action: { kind: 'create_duty', body: {} } })).key, 'noModelCompute');
  assert.equal(M.rowNote(row({ status: 'no_model' })).key, 'noModel');
  assert.deepEqual(M.rowNote(row({ extra: { high_freq: true, freq_hz: 1000 } })), { key: 'freqFlag', params: { hz: 1000 } });
  assert.equal(M.rowNote(row({})), null);
});

t('pasted free-run rows in the slash format, "-" = not measured, a bad row reported by its line', () => {
  const p = M.parseRows('613/75/74/26.7/45.2, 812/111/101.5/35.2,\n4786/-/576/202.8, x/1', 'free_run');
  assert.deepEqual(p.rows, [
    { rpm: 613, shaft_power_w: 75, v_ll_pp_v: 74, v_ll_rms_v: 26.7, temp_c: 45.2 },
    { rpm: 812, shaft_power_w: 111, v_ll_pp_v: 101.5, v_ll_rms_v: 35.2 },
    { rpm: 4786, v_ll_pp_v: 576, v_ll_rms_v: 202.8 },
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
  const rows = [{ rpm: 613, shaft_power_w: 75, v_ll_pp_v: 74 }, { rpm: 4786, v_ll_pp_v: 576, v_ll_rms_v: 202.8 }];
  const text = M.rowsToText(rows, 'free_run');
  assert.equal(text, '613/75/74\n4786/-/576/202.8');
  assert.deepEqual(M.parseRows(text, 'free_run').rows, rows);
});

t('number lists and the newest-first order of a sub-menu', () => {
  assert.deepEqual(M.parseNumberList('18.13 / 18.03 / 17,94'), [18.13, 18.03, 17.94]);
  const tests = [{ id: 'a', kind: 'parameters', date: '2026-10-18' }, { id: 'b', kind: 'free_run', date: '2026-10-21' },
    { id: 'c', kind: 'parameters', date: '2026-10-26' }];
  assert.deepEqual(M.testsOfKind(tests, 'parameters').map((x) => x.id), ['c', 'a']);
});
