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

t('a cogging check with the bench value still to come shows the model and says pending', () => {
  const r = row({ key: 'cogging_pkpk', measured: null, model: 4.745, extra: { pending: true } });
  assert.deepEqual(M.rowNote(r), { key: 'benchPending', params: {} });
  const lines = M.tableLines({ test_id: 'c', kind: 'parameters', model_available: true,
    groups: [{ key: 'cogging', label: 'No-load cogging', params: {}, rows: [r] }] });
  assert.deepEqual(lines.map((l) => l.type === 'group' ? `#${l.key}` : l.row.key), ['#cogging', 'cogging_pkpk']);
  assert.equal(lines[1].model, '4.75'); assert.equal(lines[1].measured, '—');
});

t('a model above the R reading is said as such, never as a negative board-and-joints remainder', () => {
  const over = M.rowNote(row({ key: 'r_ll_mean', measured: 140.6, model: 258.2, extra: { remaining_mohm: -117.6 } }));
  assert.deepEqual(over, { key: 'modelAbove', params: { mohm: '117.6' } });
  const under = M.rowNote(row({ key: 'r_ll_mean', measured: 140.6, model: 134.5, extra: { remaining_mohm: 6.1 } }));
  assert.deepEqual(under, { key: 'remaining', params: { mohm: '6.10' } });
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

t('a load sweep series: cleaned, sorted by rpm, gaps stay null, empty quantities dropped, unknown key falls back', () => {
  const cmp = { test_id: 'l', kind: 'load', model_available: true, groups: [], series: { x: 'rpm', quantities: [
    { key: 'torque', label: 'Torque', unit: 'N·m', tip: 'load', points: [
      { rpm: 2700, current: 100, measured: 29, model: 30, delta_pct: 3.4 },
      { rpm: 1200, current: 50, measured: 10, model: null },
      { rpm: 1800, current: 70, measured: null, model: null },            // nothing to plot: skipped, never a 0
      { rpm: NaN, measured: 5, model: 5 },
    ] },
    { key: 'i_dc', label: 'DC current', unit: 'A', points: [{ rpm: 1200, measured: 20, model: null }] },       // one point
    { key: 't_housing', label: 'Housing temperature', unit: 'C', points: [] },
  ] } };
  const opts = M.loadSeriesOptions(cmp);
  assert.deepEqual(opts.map((q) => q.key), ['torque', 'i_dc']);
  assert.deepEqual(opts[0].points.map((p) => p.rpm), [1200, 2700]);
  assert.equal(opts[0].points[0].model, null); assert.equal(opts[0].points[0].delta_pct, null);
  assert.equal(M.pickQuantity(opts, 'i_dc').key, 'i_dc');
  assert.equal(M.pickQuantity(opts, 'gone').key, 'torque');                    // stale selection -> the first quantity
  assert.equal(M.pickQuantity([], 'torque'), null);
  const s = M.quantitySeries(opts[1]);
  assert.deepEqual(s, { unit: 'A', points: [{ rpm: 1200, measured: 20, model: null, delta: null, current: null }] });
  assert.deepEqual(M.loadSeriesOptions({ test_id: 'a', kind: 'free_run', model_available: true, groups: [] }), []);   // no series block
  assert.deepEqual(M.loadSeriesOptions(null), []);
  assert.equal(M.blocksHeight([{ lines: [1, 2, 3] }, { lines: [1, 2] }]), (3 + 2) * M.TABLE_ROW_PX + 8 + (2 + 2) * M.TABLE_ROW_PX + 8);
});

t('row notes: unexplained loss, no model point (with the compute action), frequency flag', () => {
  assert.deepEqual(M.rowNote(row({ key: 'loss', extra: { unexplained_w: 412.4 } })), { key: 'unexplained', params: { w: '412.4' } });
  assert.equal(M.rowNote(row({ status: 'no_model', action: { kind: 'create_duty', body: {} } })).key, 'noModelCompute');
  assert.equal(M.rowNote(row({ status: 'no_model' })).key, 'noModel');
  assert.deepEqual(M.rowNote(row({ extra: { high_freq: true, freq_hz: 1000 } })), { key: 'freqFlag', params: { hz: 1000 } });
  assert.equal(M.rowNote(row({})), null);
});

t('Kv / Kt rows: a flagged EMF row and a correction factor that is not the current one say so, a clean row says nothing', () => {
  const flagged = row({ key: 'emf_peak', measured: 523, model: 540, extra: { in_slope: true, slope_flagged: true, slope_dev_pct: -4.04 } });
  assert.deepEqual(M.rowNote(flagged), { key: 'slopeFlagged', params: { pct: '-4.0' } });
  assert.equal(M.rowNote(row({ key: 'emf_peak', extra: { in_slope: true, slope_flagged: false, slope_dev_pct: 0.2 } })), null);
  const inh = row({ key: 'kv', measured: 5.8, model: 6.77, extra: { corrected: true, end3d: { inherited: true, current: true } } });
  assert.equal(M.rowNote(inh).key, 'inheritedFactor');
  assert.equal(M.rowNote(row({ key: 'kt', extra: { corrected: true, end3d: { inherited: false, current: false } } })).key, 'storedFactor');
  assert.equal(M.rowNote(row({ key: 'kt', extra: { corrected: true, end3d: { inherited: false, current: true } } })), null);
  // an uncorrected row never carries the factor note, and Kv / Kt rows form no speed series (no chart)
  assert.equal(M.rowNote(row({ key: 'kv', extra: { corrected: false, end3d: { inherited: true } } })), null);
  const g = { key: 'kv_kt', label: 'x', params: { n: 6 }, rows: [row({ key: 'kv', measured: 5.8, model: 6.44, delta_pct: 11 })] };
  assert.equal(M.seriesOf(g), null);
  assert.equal(M.fmtValue(5.8), '5.80');
  assert.equal(M.fmtValue(2.016), '2.02');
});

// ── the tabs: Resistance | Inductance | Constants | Bearings | Free-run | Loads | Parameters ──────────────────────────

const TABS = ['resistance', 'inductance', 'bearings', 'free_run', 'loads', 'parameters'];
const grp = (key, rows = [row({ key: 'r', measured: 1, model: 1 })]) => ({ key, label: key, params: {}, rows });
const cmpOf = (kind, keys) => ({ test_id: 'x', kind, model_available: true, groups: keys.map((k) => grp(k)) });

t('every group lands in exactly one tab: the row-to-tab map', () => {
  const map = {
    parameters: { resistance: 'resistance', inductance: 'inductance', cogging: 'parameters', lcr: 'parameters',
      insulation: 'parameters', mass: 'parameters' },
    free_run: { emf_peak: 'parameters', emf_rms: 'parameters', kv_kt: 'parameters', kv_kt_ex: 'parameters',
      bearings_none: 'bearings', bearing_sum: 'bearings', loss: 'free_run', fit: 'free_run', no_model: 'free_run' },
    load: { load_point: 'loads', no_model: 'loads' },
  };
  for (const [kind, groups] of Object.entries(map)) {
    for (const [key, tab] of Object.entries(groups)) assert.equal(M.tabOfGroup(kind, key), tab, `${kind}/${key}`);
  }
  // the rules are per kind: a load record never lands in Constants or Resistance, whatever its group is called
  assert.equal(M.tabOfGroup('load', 'resistance'), 'loads');
  assert.equal(M.tabOfGroup('load', 'emf_peak'), 'loads');
  assert.equal(M.tabOfGroup('free_run', 'resistance'), 'free_run');
});

t('a group kind nobody mapped yet falls back to its record kind default tab, never to no tab', () => {
  assert.equal(M.tabOfGroup('parameters', 'some_new_group'), 'parameters');
  assert.equal(M.tabOfGroup('free_run', 'some_new_group'), 'free_run');
  assert.equal(M.tabOfGroup('load', 'some_new_group'), 'loads');
  assert.equal(M.defaultTabOfKind('parameters'), 'parameters');
  assert.equal(M.defaultTabOfKind('free_run'), 'free_run');
  assert.equal(M.defaultTabOfKind('load'), 'loads');
  for (const kind of ['parameters', 'free_run', 'load']) assert.ok(TABS.includes(M.tabOfGroup(kind, 'whatever')));
});

t('a record is reachable: it appears in every tab it has a group for, and a record with no group stays in its default tab', () => {
  const par = cmpOf('parameters', ['resistance', 'inductance', 'cogging', 'insulation']);
  assert.deepEqual(M.recordTabs('parameters', par).sort(), ['inductance', 'parameters', 'resistance']);
  const fr = cmpOf('free_run', ['emf_peak', 'emf_rms', 'kv_kt', 'bearing_sum', 'loss', 'fit']);
  assert.deepEqual(M.recordTabs('free_run', fr).sort(), ['bearings', 'free_run', 'parameters']);
  assert.deepEqual(M.recordTabs('free_run', cmpOf('free_run', ['emf_peak'])), ['parameters']);
  for (const kind of ['parameters', 'free_run', 'load']) {
    assert.deepEqual(M.recordTabs(kind, cmpOf(kind, [])), [M.defaultTabOfKind(kind)]);
    assert.deepEqual(M.recordTabs(kind, undefined), [M.defaultTabOfKind(kind)]);
    assert.deepEqual(M.recordTabs(kind, null), [M.defaultTabOfKind(kind)]);
  }
});

t('nothing is lost: the groups of all tabs together are exactly the groups of the record', () => {
  const keys = ['resistance', 'inductance', 'cogging', 'lcr', 'insulation', 'mass', 'brand_new'];
  const cmp = cmpOf('parameters', keys);
  const seen = [];
  for (const tab of TABS) seen.push(...M.cmpForTab(cmp, 'parameters', tab).groups.map((g) => g.key));
  assert.deepEqual(seen.sort(), [...keys].sort());
  const frKeys = ['emf_peak', 'kv_kt', 'kv_kt_ex', 'bearing_sum', 'bearings_none', 'loss', 'fit', 'no_model', 'brand_new'];
  const fr = cmpOf('free_run', frKeys);
  const seenFr = [];
  for (const tab of TABS) seenFr.push(...M.cmpForTab(fr, 'free_run', tab).groups.map((g) => g.key));
  assert.deepEqual(seenFr.sort(), [...frKeys].sort());
});

t('the tab view keeps the load chart only on Loads; tabs list their records newest first', () => {
  const load = { ...cmpOf('load', ['load_point']), series: { x: 'rpm', quantities: [{ key: 'torque', label: 'T', unit: 'N', points: [] }] } };
  assert.ok(M.cmpForTab(load, 'load', 'loads').series);
  assert.equal(M.cmpForTab(load, 'load', 'bearings').series, null);
  assert.equal(M.cmpForTab(load, 'load', 'bearings').groups.length, 0);
  assert.equal(M.cmpForTab(undefined, 'load', 'loads'), undefined);
  const tests = [
    { id: 'p1', kind: 'parameters', date: '2026-01-01' }, { id: 'p2', kind: 'parameters', date: '2026-02-01' },
    { id: 'f1', kind: 'free_run', date: '2026-03-01' }, { id: 'l1', kind: 'load', date: '2026-04-01' },
    { id: 'e1', kind: 'free_run', date: '2026-05-01' },
  ];
  const compare = {
    p1: cmpOf('parameters', ['resistance']), p2: cmpOf('parameters', ['resistance', 'insulation']),
    f1: cmpOf('free_run', ['emf_peak', 'loss']), l1: cmpOf('load', ['load_point']), e1: undefined,
  };
  assert.deepEqual(M.testsOfTab(tests, compare, 'resistance').map((x) => x.id), ['p2', 'p1']);
  assert.deepEqual(M.testsOfTab(tests, compare, 'parameters').map((x) => x.id), ['f1', 'p2']);
  assert.deepEqual(M.testsOfTab(tests, compare, 'free_run').map((x) => x.id), ['e1', 'f1']);   // e1: no compare at all
  assert.deepEqual(M.testsOfTab(tests, compare, 'loads').map((x) => x.id), ['l1']);
  assert.deepEqual(M.testsOfTab(tests, compare, 'bearings'), []);
  for (const rec of tests) assert.ok(TABS.some((tab) => M.testsOfTab(tests, compare, tab).includes(rec)), rec.id);
});

t('a test added from a tab gets the record kind that tab belongs to', () => {
  assert.equal(M.kindOfTab('resistance'), 'parameters');
  assert.equal(M.kindOfTab('inductance'), 'parameters');
  assert.equal(M.kindOfTab('parameters'), 'parameters');
  for (const tab of ['bearings', 'free_run']) assert.equal(M.kindOfTab(tab), 'free_run');
  assert.equal(M.kindOfTab('loads'), 'load');
});

t('the no-bearings empty state has its own note, not "no model point"', () => {
  assert.equal(M.rowNote(row({ key: 'no_bearings', status: 'no_model' })).key, 'noBearings');
});

t('every tab has an English and a Chinese label and tooltip, and the bearings tooltip says simulation', async () => {
  const { readFileSync } = await import('node:fs');
  const load = (l) => JSON.parse(readFileSync(new URL(`../../../locales/${l}/production.json`, import.meta.url), 'utf8')).tests;
  const en = load('en'); const zh = load('zh-CN');
  for (const tab of TABS) {
    for (const d of [en, zh]) {
      assert.ok(d.tabs[tab], `tabs.${tab}`);
      assert.ok(d.tabsTip[tab], `tabsTip.${tab}`);
    }
  }
  assert.match(en.tabsTip.bearings, /SIMULATION values/);
  assert.match(en.tabsTip.bearings, /only measured figure is the total free-run loss/);
  assert.equal(en.tabs.constants, undefined); assert.equal(zh.tabsTip.constants, undefined);   // the Constants tab is gone
  assert.ok(en.basis.bearings_model && zh.basis.bearings_model);
  assert.equal(en.basis.bearings_measured, undefined);
  for (const k of ['bearing_sum', 'bearings_none']) {
    assert.ok(en.group[k] && zh.group[k], k);
  }
  assert.match(en.group.bearing_sum, /simulation/);
  // one simulation value: no 2-D / 3-D / model wording and no "×k" in any visible Tests text, EN or zh-CN
  const all = (o) => Object.values(o).flatMap((v) => (typeof v === 'string' ? [v] : all(v)));
  for (const text of all(en)) assert.doesNotMatch(text, /2-D|3-D|model|×\{k\}/i, text);
  for (const text of all(zh)) assert.doesNotMatch(text, /模型|二维|三维|2-D|3-D|×\{k\}/, text);
  assert.equal(en.col.model, 'Simulation');
  assert.equal(zh.col.model, '仿真');
});

t('pasted free-run rows in the slash format, "-" = not measured, a bad row reported by its line', () => {
  const p = M.parseRows('1000/140/80/28.3/40, 1500/240/120/42.4,\n4000/-/320/113.2, x/1', 'free_run');
  assert.deepEqual(p.rows, [
    { rpm: 1000, shaft_power_w: 140, v_ll_pp_v: 80, v_ll_rms_v: 28.3, temp_c: 40 },
    { rpm: 1500, shaft_power_w: 240, v_ll_pp_v: 120, v_ll_rms_v: 42.4 },
    { rpm: 4000, v_ll_pp_v: 320, v_ll_rms_v: 113.2 },
  ]);
  assert.equal(p.errors.length, 1);
  assert.equal(M.parseRows('1/2/3/4/5/6/7', 'free_run').errors.length, 1);      // more fields than columns
  assert.deepEqual(M.parseRows('1000/-/-/-/-/166.7', 'free_run').rows, [{ rpm: 1000, v_ll_fund_pk_v: 166.7 }]);   // the optional fundamental
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

const act = (rpm, cur) => ({ kind: 'create_duty', body: { die: 'D', config: 'C',
  duty: { name: `test ${rpm} rpm ${cur} A`, mode: 'motor', rpm, current_arms: cur, gamma_deg: 5, kind: 'test' } } });

t('"Compute all missing": one action per point, rpm rounded to 10 and current to 0.5 A, in table order', () => {
  const cmp = { test_id: 'x', kind: 'load', model_available: true, groups: [
    { key: 'g1', label: 'g1', params: {}, rows: [row({ key: 'torque', status: 'no_model', action: act(1491, 13.4) }), row({ key: 'eff' })] },
    { key: 'g2', label: 'g2', params: {}, rows: [row({ key: 'torque', status: 'no_model', action: act(1494, 13.3) })] },
    { key: 'g3', label: 'g3', params: {}, rows: [row({ key: 'torque', status: 'no_model', action: act(2000, 13.4) })] },
    { key: 'g4', label: 'g4', params: {}, rows: [row({ key: 'torque', status: 'no_model', action: act(1491, 13.9) })] }] };
  assert.deepEqual(M.missingActions(cmp).map((a) => a.body.duty.name),
    ['test 1491 rpm 13.4 A', 'test 2000 rpm 13.4 A', 'test 1491 rpm 13.9 A']);
  assert.equal(M.actionKey(act(1491, 13.4)), M.actionKey(act(1494, 13.3)));
  assert.deepEqual(M.missingActions(null), []);
});

t('compute progress from the job list: waits while queued / running / unlisted, reports failures', () => {
  const jobs = [{ run_id: 'a', state: 'done' }, { run_id: 'b', state: 'running' }, { run_id: 'c', state: 'failed', error: 'boom' }];
  const mid = M.computeProgress(['a', 'b', 'c', 'd'], jobs);
  assert.deepEqual([mid.total, mid.done, mid.failed, mid.running, mid.unlisted, mid.finished], [4, 1, 1, 2, 1, false]);
  assert.equal(mid.error, 'boom');
  const end = M.computeProgress(['a', 'c'], jobs);
  assert.deepEqual([end.done, end.failed, end.running, end.finished], [1, 1, 0, true]);
  assert.equal(M.computeProgress([], jobs).finished, false);
});

// ── per-speed load plots ────────────────────────────────────────────────────────────────────────────────────────
const SWEEP = [
  { rpm: 1000, current: 32, torque: 48, v_dc: 540, i_dc: 11 },
  { rpm: 2000, current: 64, torque: 80.96, v_dc: 540, i_dc: 34.9 },
  { rpm: 1000, current: 59, torque: 81.7, v_dc: 540, i_dc: 18.4 },
  { rpm: 2000, current: 40, torque: 40.08, v_dc: 540, i_dc: 17.2 },
];

t('two-speed sweep: one series per speed, sorted by x, never joined across speeds', () => {
  assert.equal(M.isPerSpeedSweep(SWEEP), true);
  const plot = M.perSpeedSeries(SWEEP, 'torque_current');
  assert.deepEqual(plot.speeds.map((s) => s.rpm), [1000, 2000]);
  assert.deepEqual(plot.speeds[0].measured, [{ x: 32, y: 48 }, { x: 59, y: 81.7 }]);
  assert.deepEqual(plot.speeds[1].measured, [{ x: 40, y: 40.08 }, { x: 64, y: 80.96 }]);
  assert.equal(plot.speeds[0].model.length, 0);                                // no model value is invented
  const { data, lines } = M.speedChartData(plot);
  assert.equal(lines.length, 2);
  assert.deepEqual(data.map((d) => d.x), [32, 40, 59, 64]);
});

t('loss and system efficiency are formed from the DC link and the shaft power, per speed, against torque', () => {
  const loss = M.perSpeedSeries(SWEEP, 'loss_torque');
  const eff = M.perSpeedSeries(SWEEP, 'eff_torque');
  const at = (plot, rpm, x) => plot.speeds.find((s) => s.rpm === rpm).measured.find((p) => p.x === x).y;
  assert.ok(Math.abs(at(loss, 1000, 81.7) - 1380) < 1);
  assert.ok(Math.abs(at(loss, 2000, 80.96) - 1890) < 1);
  assert.equal(at(eff, 1000, 81.7).toFixed(1), '86.1');
  assert.equal(at(eff, 2000, 80.96).toFixed(1), '90.0');
  assert.equal(M.perSpeedSeries(SWEEP, 'idc_torque').speeds[1].measured[1].y, 34.9);
  assert.deepEqual(M.perSpeedSeries(SWEEP, 'idc_torque').speeds[0].measured.map((p) => p.x), [48, 81.7]);
});

t('the plot menu lists only plots with data; a missing value is a gap, not 0; speeds group by rounded rpm', () => {
  assert.deepEqual(M.loadPlotOptions(SWEEP), ['torque_current', 'loss_torque', 'eff_torque', 'idc_torque']);
  const rows = [{ rpm: 1000.2, current: 10, torque: 5 }, { rpm: 999.8, current: 20, torque: 9, i_dc: 3, v_dc: 50, t_winding: 40 }];
  assert.equal(M.isPerSpeedSweep(rows), true);
  assert.deepEqual(M.loadPlotOptions(rows), ['torque_current', 'loss_torque', 'eff_torque', 'idc_torque', 't_winding_torque']);
  const p = M.perSpeedSeries(rows, 'loss_torque');
  assert.equal(p.speeds.length, 1);
  assert.equal(p.speeds[0].measured.length, 1);                                // the row without a DC link is a gap
  assert.equal(M.perSpeedSeries(rows, 'unknown'), null);
});

t('the model side shows only where the stored duty gave a value; one row per speed keeps the rpm chart', () => {
  const rows = [{ rpm: 1000, current: 50, torque: 10, model: { torque: 11 } }, { rpm: 1000, current: 60, torque: 12 }];
  const p = M.perSpeedSeries(rows, 'torque_current');
  assert.deepEqual(p.speeds[0].model, [{ x: 50, y: 11 }]);
  assert.deepEqual(M.speedChartData(p).lines.map((l) => [l.id, l.kind]), [['m0', 'measured'], ['d0', 'model']]);
  assert.equal(M.isPerSpeedSweep([{ rpm: 1000 }, { rpm: 2000 }, { rpm: 3000 }]), false);
  assert.deepEqual(M.loadPlotRows({ series: { x: 'rpm', quantities: [], points: [{ rpm: 1 }, { rpm: NaN }] } }), [{ rpm: 1 }]);
  assert.deepEqual(M.loadPlotRows(null), []);
});

// -- simulation curves on the per-speed plots -----------------------------------------------------------------------
const CURVES = [
  { rpm: 1000, points: [{ current_a: 20, torque_nm: 30, loss_w: 700, eff_system_pct: 85, i_dc_a: 7 },
    { current_a: 45, torque_nm: 66, loss_w: 1100, eff_system_pct: 87, i_dc_a: 15 },
    { current_a: 70, torque_nm: 95, i_dc_a: 22 }, { torque_nm: 99, loss_w: null }] },
  { rpm: 3000, points: [{ current_a: 30, torque_nm: 40, loss_w: 900, eff_system_pct: 92, i_dc_a: 20 }] },
];

t('a curve is drawn per speed in the plot mapping, points with a missing x or y are skipped and never 0', () => {
  const cur = M.simulationCurves({ series: { x: 'rpm', quantities: [], simulation_curves: [{ rpm: NaN, points: [{ torque_nm: 1 }] }, ...CURVES] } });
  assert.equal(cur.length, 2);                                                          // the curve without a finite rpm is dropped
  const tc = M.perSpeedSeries(SWEEP, 'torque_current', cur);
  assert.deepEqual(tc.speeds.find((s) => s.rpm === 1000).simulation,
    [{ x: 20, y: 30 }, { x: 45, y: 66 }, { x: 70, y: 95 }]);                          // x current_a, y torque_nm
  const loss = M.perSpeedSeries(SWEEP, 'loss_torque', cur).speeds.find((s) => s.rpm === 1000).simulation;
  assert.deepEqual(loss, [{ x: 30, y: 700 }, { x: 66, y: 1100 }]);                      // the points with no loss_w are skipped
  assert.deepEqual(M.perSpeedSeries(SWEEP, 'eff_torque', cur).speeds.find((s) => s.rpm === 1000).simulation,
    [{ x: 30, y: 85 }, { x: 66, y: 87 }]);
  assert.deepEqual(M.perSpeedSeries(SWEEP, 'idc_torque', cur).speeds.find((s) => s.rpm === 1000).simulation,
    [{ x: 30, y: 7 }, { x: 66, y: 15 }, { x: 95, y: 22 }]);
  assert.ok(M.perSpeedSeries(SWEEP, 't_winding_torque', cur) === null);                 // temperature plots: no curve, no bench data
});

t('a speed that only a curve has is still listed; curves alone give a plot menu', () => {
  const cur = M.simulationCurves({ series: { simulation_curves: CURVES } });
  const p = M.perSpeedSeries(SWEEP, 'torque_current', cur);
  assert.deepEqual(p.speeds.map((s) => s.rpm), [1000, 2000, 3000]);
  assert.equal(p.speeds[2].measured.length, 0); assert.equal(p.speeds[2].simulation.length, 1);
  assert.deepEqual(M.loadPlotOptions([], cur), ['torque_current', 'loss_torque', 'eff_torque', 'idc_torque']);
  assert.equal(M.usesSpeedPlots([], cur), true);
  assert.equal(M.usesSpeedPlots([{ rpm: 1 }, { rpm: 2 }], []), false);
  assert.equal(M.usesSpeedPlots(SWEEP, []), true);
});

t('curve x values that differ from the bench rows still give one continuous line per set (own ids, sorted data)', () => {
  const cur = M.simulationCurves({ series: { simulation_curves: [CURVES[0]] } });
  const { data, lines } = M.speedChartData(M.perSpeedSeries(SWEEP, 'torque_current', cur));
  assert.deepEqual(lines.map((l) => [l.id, l.kind, l.label, l.color]),
    [['m0', 'measured', 'measured', 0], ['s0', 'simulation', 'simulation', 0], ['m1', 'measured', 'measured', 1]]);
  assert.deepEqual(data.map((d) => d.x), [20, 32, 40, 45, 59, 64, 70]);                   // one datum per point, no x shared
  for (const d of data) assert.equal(Object.keys(d).length, 2);                           // x + exactly one value: the chart joins over the gaps
  assert.ok(data.every((d) => Object.values(d).every(Number.isFinite)));                  // no null, no 0 placeholder
  // a stored-duty point set at a speed that also has a curve is named as a point, not as the simulation
  const rows = [{ rpm: 1000, current: 50, torque: 10, model: { torque: 11 } }, { rpm: 1000, current: 60, torque: 12 }];
  const l2 = M.speedChartData(M.perSpeedSeries(rows, 'torque_current', cur)).lines;
  assert.deepEqual(l2.map((l) => [l.kind, l.label]), [['measured', 'measured'], ['model', 'point'], ['simulation', 'simulation']]);
});

t('Constants is merged into Parameters: Kv / Kt on top, an old `constants` tab value falls back to Parameters, counts cover both', () => {
  const fr = cmpOf('free_run', ['emf_peak', 'emf_rms', 'kv_kt', 'loss']);
  assert.deepEqual(M.cmpForTab(fr, 'free_run', 'parameters').groups.map((g) => g.key), ['kv_kt', 'emf_peak', 'emf_rms']);
  assert.deepEqual(M.cmpForTab(fr, 'free_run', 'free_run').groups.map((g) => g.key), ['loss']);
  assert.equal(M.normalizeTab('constants'), 'parameters');
  assert.equal(M.normalizeTab('load'), 'loads');
  assert.equal(M.normalizeTab('nonsense'), 'resistance');
  assert.equal(M.normalizeTab('bearings'), 'bearings');
  const tests = [{ id: 'p', kind: 'parameters', date: '2026-01-01' }, { id: 'f', kind: 'free_run', date: '2026-02-01' }];
  const compare = { p: cmpOf('parameters', ['cogging']), f: fr };
  assert.equal(M.testsOfTab(tests, compare, 'parameters').length, 2);            // the parameters test and the free-run Kv / Kt
  assert.equal(M.kindOfTab('parameters'), 'parameters');
});

t('power list: load tests only, each at its row of highest torque x speed, highest power first', () => {
  const tests = [
    { id: 'a', kind: 'load', date: '2026-11-05', title: 'Low', rows: [{ rpm: 1000, torque_nm: 50, v_dc_v: 600, i_dc_a: 10 }, { rpm: 2000, torque_nm: 100, v_dc_v: 600, i_dc_a: 39 }] },
    { id: 'b', kind: 'load', date: '2026-11-05', title: 'High', rows: [{ rpm: 3000, torque_nm: 120, temp_winding_c: 78, eff_pct: 90.5 }] },
    { id: 'c', kind: 'parameters', date: '2026-11-05', title: 'No power', rows: [{ rpm: 3000, torque_nm: 500 }] },
    { id: 'd', kind: 'load', date: '2026-11-05', title: 'No torque', rows: [{ rpm: 3000, eff_pct: 90 }] },
  ];
  const r = M.powerSummary(tests);
  assert.deepEqual(r.map((l) => l.id), ['b', 'a']);
  assert.ok(Math.abs(r[0].powerKw - 37.7) < 0.05);
  assert.equal(r[0].effPct, 90.5);
  assert.equal(r[0].tempC, 78);
  assert.equal(r[1].rpm, 2000);
  assert.ok(Math.abs(r[1].effPct - 89.5) < 0.1);       // shaft over DC when motoring
  assert.equal(r[1].tempC, null);
});

t('efficiency reads the smaller power over the larger, so a generator row with DC below shaft stays under 100 %', () => {
  const [l] = M.powerSummary([{ id: 'g', kind: 'load', date: '', title: '', rows: [{ rpm: 3000, torque_nm: 100, v_dc_v: 600, i_dc_a: 47.6 }] }]);
  assert.ok(l.effPct > 90 && l.effPct < 91);
});

t('the power tab is a known tab and a test added from it is a load test', () => {
  assert.equal(M.normalizeTab('power'), 'power');
  assert.equal(M.kindOfTab('power'), 'load');
});

t('load points fold into one table: a row per point, a column per quantity, Simulation columns only where a point has them', () => {
  const r = (key, measured, model = null) => ({ key, label: key, params: {}, unit: '', measured, model, delta_pct: null, status: 'ok', extra: {} });
  const g = (label, rows) => ({ key: 'load_point', label, params: {}, rows });
  const blocks = M.tableBlocks({ test_id: 'a', kind: 'load', model_available: true, groups: [
    g('1000 rpm', [r('torque', 100), r('eff', 85, 86)]), g('2000 rpm', [r('torque', 101), r('eff', 90)]),
    { key: 'other', label: 'x', params: {}, rows: [r('z', 1)] }] });
  const table = M.loadTable(blocks);
  assert.deepEqual(table.columns.map((c) => [c.key, c.hasModel]), [['torque', false], ['eff', true]]);
  assert.equal(table.points.length, 2);
  assert.equal(table.points[1].cells.eff.measured, M.fmtValue(90));
  assert.deepEqual(M.otherBlocks(blocks).map((b) => b.key), ['other']);
  assert.equal(M.loadTable(M.otherBlocks(blocks)), null);
});

t('table height follows the compact row', () => {
  assert.equal(M.blocksHeight([{ lines: [1, 2] }]), (2 + 2) * M.TABLE_ROW_PX + 8);
});

t('the folded load table counts one row per point; a note shared by two quantities of a point shows once', () => {
  const r = (key) => ({ key, label: key, params: {}, unit: '', measured: 1, model: null, delta_pct: null, status: 'no_model', extra: {} });
  const g = (label) => ({ key: 'load_point', label, params: {}, rows: [r('torque'), r('eff')] });
  const blocks = M.tableBlocks({ test_id: 'a', kind: 'load', model_available: true, groups: [g('a'), g('b'), g('c')] });
  assert.equal(M.foldedHeight(blocks), 4 * M.TABLE_ROW_PX + 8);
  const cells = { x: { note: { key: 'n1', params: {} } }, y: { note: { key: 'n1', params: {} } }, z: { note: null } };
  assert.deepEqual(M.pointNotes(cells).map((n) => n.key), ['n1']);
});

t('a note every point has is said once above the column; a note of one point stays on its row', () => {
  const r = (key, tag) => ({ note: { key: tag, params: { key } } });
  const shared = { note: { key: 'no_point', params: {} } };
  const table = { columns: [], points: [{ cells: { a: shared, b: r('b', 'flag') } }, { cells: { a: shared } }] };
  assert.deepEqual(M.commonNotes(table).map((n) => n.key), ['no_point']);
  assert.deepEqual(M.ownNotes(table, table.points[0].cells).map((n) => n.key), ['flag']);
  assert.deepEqual(M.ownNotes(table, table.points[1].cells), []);
});

t('a long load table is capped at the height of the tallest chart', () => {
  const r = { key: 'torque', label: 't', params: {}, unit: '', measured: 1, model: null, delta_pct: null, status: 'no_model', extra: {} };
  const groups = Array.from({ length: 45 }, () => ({ key: 'load_point', label: 'p', params: {}, rows: [r] }));
  assert.equal(M.foldedHeight(M.tableBlocks({ test_id: 'a', kind: 'load', model_available: true, groups })), M.LOAD_TABLE_MAX_PX + 8);
});

t('a column head drops the bracket, in both languages; a name without one stays', () => {
  assert.equal(M.shortHead('System efficiency (DC in, shaft out)'), 'System efficiency');
  assert.equal(M.shortHead('系统效率（直流输入，轴输出）'), '系统效率');
  assert.equal(M.shortHead('Torque'), 'Torque');
});

t('a video is told by its file name; a sheet or a note is not one, and the type follows the file', () => {
  assert.equal(M.isVideoName('2026-11-05_G2_33kW_3000rpm_video.mp4'), true);
  assert.equal(M.isVideoName('clip.MOV'), true);
  assert.equal(M.isVideoName('table.png'), false);
  assert.equal(M.isVideoName(null), false);
  assert.equal(M.videoMime('a.webm'), 'video/webm');
  assert.equal(M.videoMime('a.mov'), 'video/mp4');
});

