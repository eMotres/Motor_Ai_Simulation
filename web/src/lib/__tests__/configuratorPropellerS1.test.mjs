// node --test — the Configure line for the continuous (S1) limit with a propeller (owner 2026-10-11).
// The answer is the backend's (/api/propellers/{id}/s1); this pins only what the line says and when
// it says nothing (no answer, or an answer for another propeller / configuration).
import test from 'node:test';
import assert from 'node:assert/strict';

let S = null;
try { S = await import('../configuratorPropellerS1.ts'); } catch { /* old Node */ }
const t = S ? test : test.skip;

const LIMIT = {
  rpm: 8479, thrust_N: 16.95, shaft_power_W: 265, electrical_power_W: 292, battery_power_W: 302,
  current_A: 21, T_winding_C: 156.2, T_magnet_C: 150, T_housing_C: 144, air_speed_ms: 9.1,
  binding: 'magnet', extrapolated: false, power_estimated: false, below_loss_grid: false,
};
const R = {
  propeller_id: 'tmotor_fpv_10x5', die: 'CIANO14 40 new', config: 'L12', ambient_c: 25,
  limit: LIMIT, no_continuous_point: false, first_failure: 'magnet', monotone: true,
  limits: { winding_C: 200, magnet_C: 150, winding_basis: 'class N', magnet_basis: 'card' },
};

t('a limit reads speed, thrust in kgf, shaft power and the binding key', () => {
  const l = S.s1Line(R, 'tmotor_fpv_10x5', 'L12');
  assert.equal(l.kind, 'limit');
  assert.equal(l.rpm, 8479);
  assert.ok(Math.abs(l.thrust_kgf - 16.95 / 9.80665) < 1e-12);
  assert.equal(l.shaft_W, 265);
  assert.equal(l.bindingKey, 'configurePropeller.s1BindMagnet');
  assert.deepEqual(l.flagKeys, []);
});

t('no line for another propeller, another configuration, or no answer', () => {
  assert.equal(S.s1Line(R, 'tmotor_p12x4', 'L12'), null);
  assert.equal(S.s1Line(R, 'tmotor_fpv_10x5', 'L20'), null);
  assert.equal(S.s1Line(null, 'tmotor_fpv_10x5', 'L12'), null);
  assert.equal(S.s1Line(R, null, 'L12'), null);
});

t('the catalogue and grid flags become tooltip sentences', () => {
  const r = { ...R, monotone: false, limit: { ...LIMIT, extrapolated: true, power_estimated: true, below_loss_grid: true } };
  assert.deepEqual(S.s1Line(r, 'tmotor_fpv_10x5', 'L12').flagKeys, [
    'configurePropeller.s1FlagExtrapolated', 'configurePropeller.s1FlagEstimated',
    'configurePropeller.s1FlagBelowGrid', 'configurePropeller.s1FlagBands']);
});

t('field weakening and a calibrated thermal model are stated', () => {
  const r = { ...R, model: { thermal_calibration: { housing_film_factor: 0.17 } },
    limit: { ...LIMIT, fw_floor: true } };
  assert.deepEqual(S.s1Line(r, 'tmotor_fpv_10x5', 'L12').flagKeys, [
    'configurePropeller.s1FlagFwFloor', 'configurePropeller.s1FlagCalibrated']);
  assert.deepEqual(S.s1Line({ ...R, model: { thermal_calibration: null } }, 'tmotor_fpv_10x5', 'L12').flagKeys, []);
});

t('a limit outside the calibration anchors\' slipstream range says so', () => {
  const cal = { model: { thermal_calibration: { housing_film_factor: 0.5 } } };
  const out = { ...R, ...cal, limit: { ...LIMIT, calibration_in_range: false } };
  assert.deepEqual(S.s1Line(out, 'tmotor_fpv_10x5', 'L12').flagKeys, ['configurePropeller.s1FlagCalibratedOutside']);
  const inside = { ...R, ...cal, limit: { ...LIMIT, calibration_in_range: true } };
  assert.deepEqual(S.s1Line(inside, 'tmotor_fpv_10x5', 'L12').flagKeys, ['configurePropeller.s1FlagCalibrated']);
});

t('no continuous point names what failed first', () => {
  const l = S.s1Line({ ...R, limit: null, no_continuous_point: true, first_failure: 'winding' }, 'tmotor_fpv_10x5', 'L12');
  assert.deepEqual(l, { kind: 'none', bindingKey: 'configurePropeller.s1BindWinding' });
  for (const [b, k] of [['current', 's1BindCurrent'], ['voltage', 's1BindVoltage'], ['speed', 's1BindVoltage'],
    ['speed_range', 's1BindGrid'], ['runaway', 's1BindRunaway'], [null, 's1BindUnknown'],
    ['controller', 's1BindController'], ['battery_current', 's1BindBattery'], ['demag', 's1BindDemag'],
    ['loss_data', 's1BindLossData'], ['nonconverged', 's1BindNonconverged']]) {
    assert.equal(S.s1BindingKey(b), `configurePropeller.${k}`);
  }
});

t('a limit is an upper edge unless the scan ended still passing: then a lower bound with its reason', () => {
  const l = S.s1Line(R, 'tmotor_fpv_10x5', 'L12');
  assert.equal(l.lowerBound, false);
  assert.equal(l.rangeEndKey, null);
  const lb = S.s1Line({ ...R, limit: { ...LIMIT, binding: 'speed_range', lower_bound: true, range_end: 'propeller_data' } },
    'tmotor_fpv_10x5', 'L12');
  assert.equal(lb.lowerBound, true);
  assert.equal(lb.rangeEndKey, 'configurePropeller.s1EndProp');
  assert.equal(S.s1RangeEndKey('motor_speed_grid'), 'configurePropeller.s1EndGrid');
  assert.equal(S.s1RangeEndKey('requested'), 'configurePropeller.s1EndRequested');
  // an older answer without lower_bound: speed_range still reads as a lower bound
  assert.equal(S.s1Line({ ...R, limit: { ...LIMIT, binding: 'speed_range' } }, 'tmotor_fpv_10x5', 'L12').lowerBound, true);
});

t('the backend qualification codes become tooltip sentences, unknown codes are skipped', () => {
  const cal = { model: { thermal_calibration: { housing_film_factor: 0.17 } } };
  const flags = ['slipstream_uncalibrated', 'pwm_omitted', 'mech_omitted', 'voltage_deeper_fw',
    'demag_reference_only', 'drive_unknown', 'battery_current_unknown', 'below_loss_grid_current', 'something_new'];
  const l = S.s1Line({ ...R, ...cal, limit: { ...LIMIT, flags } }, 'tmotor_fpv_10x5', 'L12');
  assert.deepEqual(l.flagKeys, [
    'configurePropeller.s1FlagCalibrated', 'configurePropeller.s1FlagSlipstream',
    'configurePropeller.s1FlagPwmOmitted', 'configurePropeller.s1FlagMechOmitted',
    'configurePropeller.s1FlagVoltageFw', 'configurePropeller.s1FlagDemagRef',
    'configurePropeller.s1FlagDriveUnknown', 'configurePropeller.s1FlagBatteryUnknown',
    'configurePropeller.s1FlagBelowGridCurrent']);
  // thermal extrapolation replaces the plain "calibrated"; an uncalibrated network says so
  const ex = S.s1Line({ ...R, ...cal, limit: { ...LIMIT, flags: ['thermal_extrapolated'] } }, 'tmotor_fpv_10x5', 'L12');
  assert.deepEqual(ex.flagKeys, ['configurePropeller.s1FlagCalibratedOutside']);
  const un = S.s1Line({ ...R, limit: { ...LIMIT, flags: ['thermal_uncalibrated', 'drive_failed'] } }, 'tmotor_fpv_10x5', 'L12');
  assert.deepEqual(un.flagKeys, ['configurePropeller.s1FlagThermalUncalibrated', 'configurePropeller.s1FlagDriveFailed']);
});

t('a bench-calibrated or transferred slipstream and an external ESC are stated', () => {
  const flags = ['slipstream_bench', 'esc_external', 'esc_loss_omitted', 'pwm_omitted'];
  const l = S.s1Line({ ...R, limit: { ...LIMIT, flags } }, 'tmotor_fpv_10x5', 'L12');
  assert.deepEqual(l.flagKeys, ['configurePropeller.s1FlagSlipstreamBench', 'configurePropeller.s1FlagEscExternal',
    'configurePropeller.s1FlagEscLoss', 'configurePropeller.s1FlagPwmOmitted']);
  const tr = S.s1Line({ ...R, limit: { ...LIMIT, flags: ['slipstream_transferred'] } }, 'tmotor_fpv_10x5', 'L12');
  assert.deepEqual(tr.flagKeys, ['configurePropeller.s1FlagSlipstreamTransferred']);
});

t('the S1 controller: the Drive block drive, or an external ESC with its typed bus current', () => {
  // the board cooling is never part of the request: the server decides it from the machine
  assert.deepEqual(S.s1DriveRequest('drive', 'si_48k', '40'), { drive: 'si_48k', escBusA: null });
  assert.deepEqual(S.s1DriveRequest('drive', 'sine', null), { drive: 'sine', escBusA: null });
  assert.deepEqual(S.s1DriveRequest('external', 'si_48k', '55'), { drive: S.EXTERNAL_ESC, escBusA: 55 });
  assert.deepEqual(S.s1DriveRequest('external', 'sine', '62,5'), { drive: 'external_esc', escBusA: 62.5 });
  // blank / nonsense / non-positive: no current sent, the backend takes the model's bench ESC
  for (const x of ['', '  ', 'abc', '0', '-3', null, undefined]) {
    assert.deepEqual(S.s1DriveRequest('external', 'sine', x), { drive: 'external_esc', escBusA: null });
  }
});

t('the board build rides in the request as a drive id; an external ESC ignores it', () => {
  assert.deepEqual(S.s1DriveRequest('drive', 'si_48k', null, 'x2'), { drive: 'si_48k_x2', escBusA: null });
  assert.deepEqual(S.s1DriveRequest('drive', 'si_48k', '40', 'x2_board2'), { drive: 'si_48k_x2_board2', escBusA: null });
  assert.deepEqual(S.s1DriveRequest('drive', 'sine', null, 'x2_board2'), { drive: 'sine_x2_board2', escBusA: null });
  assert.deepEqual(S.s1DriveRequest('external', 'si_48k', '55', 'x2'), { drive: 'external_esc', escBusA: 55 });
});

t('the two-device builds are offered only where the S1 model carries their drive ids', () => {
  const avail = ['sine', 'si_48k', 'si_48k_x2', 'si_48k_x2_board2', 'gan_48k_IGC019S06S1', 'external_esc'];
  assert.deepEqual(S.s1BoardBuilds(avail, 'si_48k'), ['as_built', 'x2', 'x2_board2']);
  assert.deepEqual(S.s1BoardBuilds(['sine', 'si_48k', 'si_48k_x2'], 'si_48k'), ['as_built', 'x2']);
  // no ×2 for this device (e.g. L20), or nothing learned yet: as built only, selector hidden
  assert.deepEqual(S.s1BoardBuilds(avail, 'gan_48k_IGC019S06S1'), ['as_built']);
  assert.deepEqual(S.s1BoardBuilds(avail, 'sine'), ['as_built']);
  assert.deepEqual(S.s1BoardBuilds(null, 'si_48k'), ['as_built']);
  assert.deepEqual(S.s1BoardBuilds([], 'si_48k'), ['as_built']);
  // the ids round-trip: the model's variant id + `_x2` / `_x2_board2`
  assert.equal(S.s1BuildDriveId('si_48k', 'as_built'), 'si_48k');
  assert.equal(S.s1BuildDriveId('si_48k', 'x2'), 'si_48k_x2');
  assert.equal(S.s1BuildDriveId('si_48k', 'x2_board2'), 'si_48k_x2_board2');
  assert.equal(S.s1BoardBuildOfId('si_48k_x2_board2'), 'x2_board2');
  assert.equal(S.s1BoardBuildOfId('si_48k_x2'), 'x2');
  assert.equal(S.s1BoardBuildOfId('sine'), 'as_built');
  assert.equal(S.s1BoardBuildOfId(null), 'as_built');
});

t('there is no board cooling choice: no option helpers, only the label of the server class', () => {
  assert.equal(S.s1BoardCoolingOptions, undefined);
  assert.equal(S.s1BoardCoolingChoice, undefined);
  assert.equal(S.isBoardCooling, undefined);
  assert.equal(S.s1BoardCoolingKey('slipstream'), 'configurePropeller.s1CoolSlipstream');
  assert.equal(S.s1BoardCoolingKey('favourable'), 'configurePropeller.s1CoolFavourable');
  assert.equal(S.s1BoardCoolingKey(null), 'configurePropeller.s1CoolFavourable');
});

t('the cache key follows every parameter that changes the answer', () => {
  const k = (drive = 'si_48k', escBusA = null, pack = { min: 18, max: 25.2 }) =>
    S.s1CacheKey('tmotor_fpv_10x5', 'CIANO14 40 new', 'L12', 25, drive, pack, escBusA);
  assert.equal(k(), k('si_48k', null, { min: 18, max: 25.2 }));
  assert.notEqual(k(), k('si_48k_x2'));                      // the board build's drive id
  assert.notEqual(k(), k('si_48k', 55));                     // the external ESC's bus current
  assert.notEqual(k(), k('si_48k', null, null));             // no pack window
  assert.notEqual(S.s1CacheKey('tmotor_fpv_10x5', 'CIANO14 40 new', 'L12', 25, 'si_48k', null, null),
    S.s1CacheKey('tmotor_fpv_10x5', 'CIANO14 40 new', 'L20', 25, 'si_48k', null, null));
});

t('every new S1 key exists in both locales', async () => {
  const { readFileSync } = await import('node:fs');
  const { join, dirname } = await import('node:path');
  const { fileURLToPath } = await import('node:url');
  const here = dirname(fileURLToPath(import.meta.url));
  const keys = ['s1FlagSlipstreamBench', 's1FlagSlipstreamTransferred', 's1FlagEscExternal', 's1FlagEscLoss',
    's1Ctrl', 's1CtrlTip', 's1CtrlDrive', 's1EscExternal', 's1EscBus', 's1EscBusUnit', 's1EscBusTip',
    's1EscBusTipDefault', 's1TipEsc',
    's1BoardCooling', 's1BoardCoolingTip', 's1CoolFavourable', 's1CoolSlipstream',
    's1BoardBuild', 's1BoardBuildTip', 's1BuildX1', 's1BuildX2', 's1BuildX2Board2', 's1Estimate', 's1TipBoard'];
  for (const loc of ['en', 'zh-CN']) {
    const j = JSON.parse(readFileSync(join(here, '..', '..', 'locales', loc, 'controller.json'), 'utf8'));
    for (const k of keys) assert.ok(typeof j.configurePropeller?.[k] === 'string' && j.configurePropeller[k], `${loc} ${k}`);
  }
});
