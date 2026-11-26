import test from 'node:test';
import assert from 'node:assert/strict';

let M;
try { M = await import('../propellerMission.ts'); } catch { /* old Node */ }
const t = M ? test : test.skip;

const motors = [
  { id: 'cat_a', name: 'CIANO14 40 L12', diameter_mm: 40, die: 'CIANO14 40', config: 'L12', kind: 's1', reason: null },
  { id: 'cat_b', name: 'TEST EST 40', diameter_mm: 40, die: null, config: null, kind: 'estimate', reason: null },
  { id: 'cat_c', name: 'NO KV 30', diameter_mm: 30, die: null, config: null, kind: 'none', reason: 'no Kv (no-load EMF) in the stored passport' },
  { id: null, name: 'OLD DIE L1', diameter_mm: null, die: 'OLD DIE', config: 'L1', kind: 's1', reason: null },
];

t('defaults: 70 km/h cruise, free Kv with 15 % headroom, range objective, stage 2 on', () => {
  const f = M.DEFAULT_FORM;
  assert.equal(f.speedKmh, '70');
  assert.equal(f.kvMode, 'free');
  assert.equal(f.headroom, '15');
  assert.equal(f.objective, 'range_km');
  assert.equal(f.refine, true);
  assert.equal(f.pop, '12');
  assert.equal(f.generations, '6');
});

t('km/h <-> m/s', () => {
  assert.ok(Math.abs(M.kmhToMs(70) - 19.4444444) < 1e-6);
  assert.ok(Math.abs(M.msToKmh(M.kmhToMs(70)) - 70) < 1e-9);
  assert.equal(M.msToKmh(0), 0);
});

t('the request body is SI: speed in m/s, percentages as fractions, empty optional fields left out, the motor by card id or die/config', () => {
  const r = M.buildBody({ ...M.DEFAULT_FORM, motorKey: 'cat_a', speedKmh: '72', headroom: '15', usable: '85', chords: '1, 1.15', blades: '2,3' }, motors);
  assert.equal(r.ok, true);
  assert.ok(Math.abs(r.body.flight.cruise_speed_ms - 20) < 1e-9);
  assert.ok(Math.abs(r.body.kv.headroom - 0.15) < 1e-12);
  assert.ok(Math.abs(r.body.battery.usable - 0.85) < 1e-12);
  assert.deepEqual(r.body.candidates.chords, [1, 1.15]);
  assert.deepEqual(r.body.candidates.blades, [2, 3]);
  assert.equal(r.body.motor.card_id, 'cat_a');
  assert.equal('v_nom' in r.body.battery, false);
  assert.equal('turns0' in r.body.kv, false);
  const s1 = M.buildBody({ ...M.DEFAULT_FORM, motorKey: 's1:OLD DIE|L1' }, motors);
  assert.equal(s1.ok, true);
  assert.equal(s1.body.motor.die, 'OLD DIE');
  assert.equal('card_id' in s1.body.motor, false);
  const withPreset = M.buildBody({ ...M.DEFAULT_FORM, motorKey: 'cat_a', vNom: '3,7', turns0: '7' }, motors);
  assert.equal(withPreset.body.battery.v_nom, 3.7);
  assert.equal(withPreset.body.kv.turns0, 7);
});

t('a bad field is named; a greyed or missing motor is refused', () => {
  const bad = M.buildBody({ ...M.DEFAULT_FORM, motorKey: 'cat_a', cells: 'six' }, motors);
  assert.equal(bad.ok, false);
  assert.equal(bad.field, 'cells');
  assert.equal(M.buildBody({ ...M.DEFAULT_FORM, motorKey: 'cat_c' }, motors).ok, false);
  assert.equal(M.buildBody({ ...M.DEFAULT_FORM, motorKey: '' }, motors).field, 'motorKey');
  assert.equal(M.buildBody({ ...M.DEFAULT_FORM, motorKey: 'cat_a', chords: '' }, motors).field, 'chords');
});

t('the motor picker groups by die, searches name/die/config, keeps greyed motors after the usable ones', () => {
  const all = M.motorGroups(motors, '');
  assert.deepEqual(all.map((g) => g.die), ['30 mm', '40 mm', 'CIANO14 40', 'OLD DIE'].sort((a, b) => a.localeCompare(b)));
  const q = M.motorGroups(motors, 'ciano');
  assert.equal(q.length, 1);
  assert.equal(q[0].motors[0].name, 'CIANO14 40 L12');
  assert.equal(M.motorGroups(motors, 'old die l1')[0].motors[0].die, 'OLD DIE');
  assert.equal(M.motorGroups(motors, 'zzz').length, 0);
  const g40 = all.find((g) => g.die === '40 mm');
  assert.equal(g40.motors[0].kind, 'estimate');
  assert.equal(M.motorKey(motors[3]), 's1:OLD DIE|L1');
});

const row = (id, over) => ({ id, name: id, source: 'own', D_in: 12, pitch_ratio: 0.3, chord_scale: 1, blades: 2, objectives: {}, objective_value: null, failures: [], passed: true, ...over });
const rows = [
  row('low', { objective_value: 10, objectives: { range_km: 10, top_speed_ms: 25 } }),
  row('high', { objective_value: 30, objectives: { range_km: 30, top_speed_ms: 20 } }),
  row('failed-best', { objective_value: 99, objectives: { range_km: 99 }, passed: false, failures: [{ rule: 'tw_peak', detail: 'x' }] }),
  row('none', { objective_value: null, objectives: {} }),
];

t('sort: a failed row is always below a passing one, a row without a value after the rows with one, either direction', () => {
  assert.deepEqual(M.sortRows(rows, 'objective', 'desc').map((r) => r.id), ['high', 'low', 'none', 'failed-best']);
  assert.deepEqual(M.sortRows(rows, 'objective', 'asc').map((r) => r.id), ['low', 'high', 'none', 'failed-best']);
  assert.deepEqual(M.sortRows(rows, 'top_speed_ms', 'desc').map((r) => r.id).slice(0, 2), ['low', 'high']);
  assert.deepEqual(M.sortRows(rows, 'name', 'asc').map((r) => r.id), ['high', 'low', 'none', 'failed-best']);
  assert.equal(rows[0].id, 'low');                       // the input is not reordered
});

t('table row: speeds in km/h, strip text, failure rules and strip warnings', () => {
  const x = M.tableRow(row('r', { turns: 8, kv_rpm_per_V: 380.4, strip: { thickness_mm: 0.5, width_mm: 2.5, stock_kg: 0.5, check: true, low_stock: true },
    objectives: { top_speed_ms: 20, range_km: 41.2 }, objective_value: 41.2, passed: false, failures: [{ rule: 'tw_cont', detail: 'low' }] }));
  assert.equal(x.top_speed_kmh, 72);
  assert.equal(x.strip, '0.5 x 2.5');
  assert.deepEqual(x.reasons, ['tw_cont']);
  assert.deepEqual(x.stripFlags, ['low_stock', 'check']);
  assert.equal(M.tableRow(row('q', {})).top_speed_kmh, null);
});

t('chart: one line per P/D with the best value per diameter, catalogue as dots, failed rows not drawn', () => {
  const d = M.chartData([
    row('a', { D_in: 11, pitch_ratio: 0.3, objectives: { range_km: 20 } }), row('b', { D_in: 11, pitch_ratio: 0.3, chord_scale: 1.15, objectives: { range_km: 24 } }),
    row('c', { D_in: 12, pitch_ratio: 0.3, objectives: { range_km: 26 } }), row('d', { D_in: 12, pitch_ratio: 0.4, objectives: { range_km: 22 } }),
    row('e', { D_in: 12, pitch_ratio: 0.4, objectives: { range_km: 90 }, passed: false }),
    row('cat', { source: 'catalogue', pitch_ratio: 0.5, D_in: 12, objectives: { range_km: 18 } }),
  ], 'range_km');
  assert.deepEqual(d.lines.map((l) => l.pitch), [0.3, 0.4]);
  assert.deepEqual(d.lines[0].points, [{ D: 11, value: 24 }, { D: 12, value: 26 }]);
  assert.deepEqual(d.lines[1].points, [{ D: 12, value: 22 }]);
  assert.deepEqual(d.dots, [{ D: 12, value: 18, name: 'cat' }]);
});

t('gain in percent and the draft name', () => {
  assert.equal(M.gainPct(40, 44), 10);
  assert.equal(M.gainPct(null, 44), null);
  assert.equal(M.gainPct(0, 5), null);
  assert.equal(M.draftName('mission', 'msn-0a1b2c3d4e', 1), 'mission_0a1b2c3d4e_1');
  assert.ok(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,59}$/.test(M.draftName('mission', 'msn-0a1b2c3d4e', 2)));
});

t('field-level validation: every bad field is reported with its kind, ranges and whole numbers are checked', () => {
  const r = M.buildBody({ ...M.DEFAULT_FORM, motorKey: 'cat_a', cells: '2.5', capacity: '-1', speedKmh: 'fast', pop: '99', pdRange: '0.6-0.2', blades: '4' }, motors);
  assert.equal(r.ok, false);
  assert.equal(r.errors.cells, 'int');
  assert.equal(r.errors.capacity, 'range');
  assert.equal(r.errors.speedKmh, 'number');
  assert.equal(r.errors.pop, 'range');
  assert.equal(r.errors.pdRange, 'range');
  assert.equal(r.errors.blades, 'range');
  assert.equal(Object.keys(r.errors).length, 6);
  const ok = M.buildBody({ ...M.DEFAULT_FORM, motorKey: 'cat_a', pdRange: '0.3 - 0.45', allowUnverified: true, battCurrent: '60' }, motors);
  assert.equal(ok.ok, true);
  assert.equal(ok.body.candidates.pd_min, 0.3);
  assert.equal(ok.body.candidates.pd_max, 0.45);
  assert.equal(ok.body.kv.allow_unverified, true);
  assert.equal(ok.body.battery.cont_current_A, 60);
  assert.equal('allow_unverified' in M.buildBody({ ...M.DEFAULT_FORM, motorKey: 'cat_a' }, motors).body.kv, true);
  assert.equal(M.buildBody({ ...M.DEFAULT_FORM, motorKey: 'cat_a' }, motors).body.kv.allow_unverified, false);
  assert.deepEqual(M.parseRange('0.25–0.6'), [0.25, 0.6]);
  assert.equal(M.parseRange('abc'), null);
});

t('a remembered form is sanitised: unknown keys dropped, wrong types and bad enums replaced by the defaults', () => {
  const f = M.sanitizeForm({ cells: '8', evil: 'x', capacity: 5, kvMode: 'weird', objective: 'nope', own: 'yes', chords: 'x'.repeat(500), enduranceMode: 'best_speed' });
  assert.equal(f.cells, '8');
  assert.equal('evil' in f, false);
  assert.equal(f.capacity, M.DEFAULT_FORM.capacity);
  assert.equal(f.kvMode, 'free');
  assert.equal(f.objective, 'range_km');
  assert.equal(f.own, true);
  assert.equal(f.chords, M.DEFAULT_FORM.chords);
  assert.equal(f.enduranceMode, 'best_speed');
  assert.deepEqual(M.sanitizeForm(null), M.DEFAULT_FORM);
  assert.deepEqual(M.sanitizeForm('x'), M.DEFAULT_FORM);
});

t('remembered run id and row are validated; a missing row falls back silently', () => {
  assert.equal(M.validRunId('msn-0a1b2c3d4e'), 'msn-0a1b2c3d4e');
  assert.equal(M.validRunId('msn-0a1b2c3d4e5f'), 'msn-0a1b2c3d4e5f');
  assert.equal(M.validRunId('../x'), null);
  assert.equal(M.validRunId(5), null);
  assert.equal(M.pickRow([{ id: 'a' }, { id: 'b' }], 'b'), 'b');
  assert.equal(M.pickRow([{ id: 'a' }], 'gone'), null);
  assert.equal(M.pickRow([{ id: 'a' }], null), null);
});

t('statuses and errors map to translation keys; an unknown status never shows as raw text', () => {
  assert.equal(M.statusKey('stopped'), 'propMissionStatus_stopped');
  assert.equal(M.statusKey('queued'), 'propMissionStatus_queued');
  assert.equal(M.statusKey('weird'), 'propMissionStatus_interrupted');
  assert.equal(M.errorKey(422), 'propMissionErr_422');
  assert.equal(M.errorKey(503), 'propMissionErr_503');
  assert.equal(M.errorKey(418), 'propMissionErr_other');
  assert.equal(M.serverFieldKey('battery.cells_s must be between 1 and 30'), 'cells');
  assert.equal(M.serverFieldKey('candidates.pd_max must be at least pd_min'), 'pdRange');
  assert.equal(M.serverFieldKey('something else'), null);
});

test('second review: hidden-bound errors land on the visible field, order is checked, one P/D value on screen, unknown strip is empty', () => {
  const motors = [{ id: 'cat_a', name: 'A', kind: 'full' }];
  const r = M.buildBody({ ...M.DEFAULT_FORM, motorKey: 'cat_a', maxD: '9', minD: '10' }, motors);
  assert.equal(r.ok, false);
  assert.equal(r.errors.maxD, 'order');
  assert.equal(r.errors.minD, undefined);
  const r2 = M.buildBody({ ...M.DEFAULT_FORM, motorKey: 'cat_a', minD: 'x' }, motors);
  assert.equal(r2.errors.maxD, 'number');
  assert.equal(M.serverFieldKey('airframe.min_diameter_in must be at most max'), 'maxD');
  assert.equal(M.pdShown('0.25-0.6'), '0.6');
  assert.equal(M.pdLow('0.25-0.6'), '0.25');
  assert.equal(M.withPdHigh('0.25-0.6', '0.5'), '0.25-0.5');
  assert.equal(M.stripText({ thickness_mm: null, width_mm: null }), null);
  assert.equal(M.stripText({ thickness_mm: 0.45, width_mm: 2.5 }), '0.45 × 2.5 mm');
});

test('third review: failure messages are translation keys with parameters, never the server text', () => {
  assert.deepEqual(M.failureMessage({ rule: 'tw_cont', detail: 'English', params: { value: 1.2, limit: 1.5 } }), { key: 'propMissionRuleMsg_tw_cont', params: { value: 1.2, limit: 1.5 } });
  assert.equal(M.failureMessage({ rule: 'hover_end', detail: 'x', params: { reason: 'motor_voltage_infeasible', V: null } }).key, 'propMissionPointReason_motor');
  assert.equal(M.failureMessage({ rule: 'no_curve', detail: 'English' }), null);
});
