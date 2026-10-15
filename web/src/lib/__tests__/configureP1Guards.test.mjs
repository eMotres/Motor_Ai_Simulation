import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

import * as H from '../thermalEstimate.ts';
import * as T from '../configuratorTiles.ts';
import * as B from '../configuratorBattery.ts';
import * as M from '../motorScaling.ts';
import * as L from '../configuratorLimits.ts';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const panelSource = () => readFileSync(join(SRC, 'components', 'compare', 'ConfiguratorPanel.tsx'), 'utf8');

const baseHeat = { P_cu_W: 7, P_fe_W: 3, P_mag_W: 1, stator_W: 9, rotor_W: 2 };
const geom = { statorOD_mm: 40, stackLength_mm: 20, numSlots: 12, slotHeight_mm: 6,
  slotWidth_mm: 2.8, insulation_mm: 0.06, coreThickness_mm: 2, airGap_mm: 0.2, magnetOD_mm: 24 };
const cooling = { h_Wm2K: 100, ambient_C: 25 };

test('Configure thermal attribution preserves Sine and includes PWM motor heat', () => {
  const sine = H.configureMotorHeat('sine', true, baseHeat, 0);
  assert.deepEqual(sine, { stator_W: 9, rotor_W: 2, totalMotor_W: 11,
    thermalLosses: { P_cu_W: 7, P_fe_W: 3, P_mag_W: 1, P_extra_W: 0, P_extra_stator_W: 0 } });
  assert.deepEqual(H.estimateThermal(geom, sine.thermalLosses, cooling),
    H.estimateThermal(geom, baseHeat, cooling));
  const pwm = H.configureMotorHeat('pwm', true, baseHeat, 12.5);
  assert.equal(pwm.stator_W, 21.5);
  assert.equal(pwm.rotor_W, 2);
  assert.equal(pwm.totalMotor_W, 23.5);
  assert.equal(pwm.thermalLosses.P_extra_W, 12.5);
  assert.equal(pwm.thermalLosses.P_extra_stator_W, 12.5);
  assert.ok(H.estimateThermal(geom, pwm.thermalLosses, cooling).T_winding_C
    > H.estimateThermal(geom, sine.thermalLosses, cooling).T_winding_C);
  assert.equal(H.configureMotorHeat('pwm', false, baseHeat, null), null);
  assert.equal(H.configureMotorHeat('pwm', true, null, 12.5), null);
});

test('loss model availability follows the model source precedence and withholds unknown totals', () => {
  assert.deepEqual(T.lossMapAvailability({}), { iron: false, copper: false, magnet: false, complete: false });
  assert.deepEqual(T.lossMapAvailability({ Pfe0_W: 2, Pmag0_W: 1, R0_ohm: 0.02, endWindFrac: 0.3 }),
    { iron: true, copper: true, magnet: true, complete: true });
  const activeGridMissingMag = { I_A: [1, 2], rpm: [10, 20], Pfe_W: [[1, 2], [1, 2]] };
  assert.deepEqual(T.lossMapAvailability({ Pfe0_W: 2, Pmag0_W: 1, R0_ohm: 0.02, endWindFrac: 0.3, loss_grid: activeGridMissingMag }),
    { iron: true, copper: true, magnet: false, complete: false });
  assert.equal(T.totalLossShown(null, 'sine', null), null);
  assert.equal(T.systemEfficiency('sine', null, 100, null).value, null);
  assert.equal(T.lossDensityShown(10, null, 'sine', null), null);
});

test('missing copper provenance and malformed active axes make aggregate loss unavailable', () => {
  const scalar = { Pfe0_W: 2, Pmag0_W: 1, R0_ohm: 0.02, endWindFrac: 0.3 };
  assert.equal(T.lossMapAvailability({ ...scalar, R0_ohm: null }).copper, false);
  assert.equal(T.lossMapAvailability({ ...scalar, endWindFrac: null }).copper, false);
  assert.equal(T.lossMapAvailability({ ...scalar, R0_ohm: NaN }).complete, false);
  const malformedGrid = { I_A: [1, NaN], rpm: [10, 20], Pfe_W: [[1, 2], [1, 2]], Pmag_W: [[1, 2], [1, 2]] };
  assert.deepEqual(T.lossMapAvailability({ ...scalar, loss_grid: malformedGrid }),
    { iron: false, copper: true, magnet: false, complete: false });
  const malformedCurve = { rpm: [10, Infinity], Pfe_W: [1, 2], Pmag_W: [1, 2] };
  assert.deepEqual(T.lossMapAvailability({ ...scalar, speed: malformedCurve }),
    { iron: false, copper: true, magnet: false, complete: false });
});

test('one point display gate withholds every refused drive state and Sine pack-speed refusal', () => {
  const accepted = T.pointDisplayGate({ batteryValid: true, speedAccepted: true,
    driveMode: 'pwm', driveAccepted: true, motorLossesComputed: true });
  assert.deepEqual(accepted, { accepted: true, showMotorLosses: true, showController: true, showBatteryDerived: true });
  for (const kind of ['current', 'speed-grid', 'build', 'bus', 'device']) {
    const refused = T.pointDisplayGate({ batteryValid: true, speedAccepted: true,
      driveMode: 'pwm', driveAccepted: false, motorLossesComputed: true });
    assert.equal(refused.accepted, false, kind);
    assert.equal(refused.showMotorLosses, false, kind);
    assert.equal(refused.showController, false, kind);
  }
  const sinePackSpeed = T.pointDisplayGate({ batteryValid: true, speedAccepted: false,
    driveMode: 'sine', driveAccepted: true, motorLossesComputed: true });
  assert.equal(sinePackSpeed.accepted, false);
  assert.equal(sinePackSpeed.showMotorLosses, false);
  const invalidBattery = T.pointDisplayGate({ batteryValid: false, speedAccepted: true,
    driveMode: 'sine', driveAccepted: true, motorLossesComputed: true });
  assert.deepEqual(invalidBattery, { accepted: false, showMotorLosses: false, showController: false, showBatteryDerived: false });
});

test('the panel imports the pure gate for heat, loss tiles, compact state, snapshot and saved output', () => {
  const source = panelSource();
  assert.match(source, /const pointGate = pointDisplayGate\(/);
  assert.match(source, /configureMotorHeat\(driveMode, pointGate\.accepted,/);
  assert.match(source, /pointGate\.showMotorLosses \? totalLossShown/);
  assert.match(source, /T_Nm: pointGate\.accepted \? result\.T_Nm : null/);
  assert.match(source, /torque_Nm: pointGate\.accepted \? result\.T_Nm : null/);
  assert.match(source, /disabled=\{!pointGate\.accepted\}/);
  assert.match(source, /if \(!pointGate\.showBatteryDerived \|\| !propellerCoolingActive \|\| !propId\) return/);
  assert.match(source, /\{pointGate\.showBatteryDerived && cooled && propLoad && \(/);
  assert.match(source, /presetDiff\(pr, knobs, batteryNumbers,/);
});

test('invalid battery cell voltages are identified by the nearby input', () => {
  const valid = { cells: 12, min: 3, nom: 3.7, max: 4.2 };
  assert.deepEqual(B.batteryInputIssues(valid), []);
  assert.deepEqual(B.batteryInputIssues({ ...valid, nom: 2.9 }), ['min', 'nom']);
  assert.deepEqual(B.batteryInputIssues({ ...valid, max: 3.6 }), ['nom', 'max']);
  assert.deepEqual(B.batteryInputIssues({ ...valid, min: 0 }), ['min']);
  assert.deepEqual(B.batteryInputIssues({ ...valid, cells: 2.5 }), ['cells']);
});

test('battery editing preserves empty/nonfinite drafts, gates immediately, and restores deliberately', () => {
  const start = { type: 'NMC', cells: 12, min: 3, nom: 3.7, max: 4.2 };
  const empty = B.editBatteryField(start, 'nom', '');
  assert.equal(B.batteryFieldText(empty, 'nom'), '');
  assert.deepEqual(B.batteryInputIssues(empty), ['nom']);
  assert.equal(B.validBatteryNumbers(empty), null);
  const nonfinite = B.editBatteryField(empty, 'nom', '1e309');
  assert.deepEqual(B.batteryInputIssues(nonfinite), ['nom']);
  const ordered = B.editBatteryField(nonfinite, 'nom', '5');
  assert.deepEqual(B.batteryInputIssues(ordered), ['nom', 'max']);
  const fixed = B.editBatteryField(ordered, 'nom', '3.8');
  assert.deepEqual(B.validBatteryNumbers(fixed), { cells: 12, min: 3, nom: 3.8, max: 4.2 });
  const raw = B.writeBatteryEdit(null, 'machine', empty);
  assert.deepEqual(B.readBatteryEdit(raw, 'machine'), empty);
  assert.equal(B.sameBattery(start, empty), false);
  assert.equal(B.isStockBattery(B.editBatteryField(B.STOCK_BATTERY, 'nom', '')), false);
  assert.equal(B.dropStockEdits(B.writeBatteryEdit(null, 'stock', B.editBatteryField(B.STOCK_BATTERY, 'nom', ''))),
    B.writeBatteryEdit(null, 'stock', B.editBatteryField(B.STOCK_BATTERY, 'nom', '')));
});

test('missing electromagnetic loss scalars remain uncomputed through motor scaling', () => {
  const p = { L0_mm: 20, wireH0_mm: 0.6, I0_A: 35, rpm0: 13000, nP0: 1, N0: 7,
    T0_Nm: 0.5, Vemf0_peak_V: 10, R0_ohm: 0.01, endWindFrac: 0.3,
    Pfe0_W: null, Pmag0_W: null, mass0_kg: 0.1 };
  const k = { N: 7, L_mm: 20, wireH_mm: 0.6, nP: 1, I_A: 35, rpm: 13000 };
  const r = M.scaleMotor(p, k, 14);
  assert.equal(Number.isNaN(r.P_fe_W), true);
  assert.equal(Number.isNaN(r.P_mag_W), true);
  assert.equal(Number.isNaN(r.P_loss_W), true);
  assert.equal(Number.isNaN(r.P_loss_stator_W), true);
  assert.equal(Number.isNaN(r.P_loss_rotor_W), true);
});

test('missing copper provenance never becomes a physical zero', () => {
  const p = { L0_mm: 20, wireH0_mm: 0.6, I0_A: 35, rpm0: 13000, nP0: 1, N0: 7,
    T0_Nm: 0.5, Vemf0_peak_V: 10, R0_ohm: 0.01, endWindFrac: 0.3,
    Pfe0_W: 2, Pmag0_W: 1, mass0_kg: 0.1 };
  const k = { N: 7, L_mm: 20, wireH_mm: 0.6, nP: 1, I_A: 35, rpm: 13000 };
  for (const broken of [{ ...p, R0_ohm: null }, { ...p, endWindFrac: null }, { ...p, R0_ohm: NaN }]) {
    const r = M.scaleMotor(broken, k, 14);
    assert.equal(Number.isNaN(r.R_ohm), true);
    assert.equal(Number.isNaN(r.P_cu_W), true);
    assert.equal(Number.isNaN(r.P_loss_W), true);
  }
});

test('malformed active loss axes stay unknown through scaling', () => {
  const base = { L0_mm: 20, wireH0_mm: 0.6, I0_A: 35, rpm0: 13000, nP0: 1, N0: 7,
    T0_Nm: 0.5, Vemf0_peak_V: 10, R0_ohm: 0.01, endWindFrac: 0.3,
    Pfe0_W: 2, Pmag0_W: 1, mass0_kg: 0.1 };
  const k = { N: 7, L_mm: 20, wireH_mm: 0.6, nP: 1, I_A: 35, rpm: 13000 };
  const grid = { I_A: [10, NaN], rpm: [1000, 2000],
    Pfe_W: [[1, 2], [2, 3]], Pmag_W: [[0.1, 0.2], [0.2, 0.3]] };
  const fromGrid = M.scaleMotor({ ...base, loss_grid: grid }, k, 14);
  assert.equal(Number.isNaN(fromGrid.P_fe_W), true);
  assert.equal(Number.isNaN(fromGrid.P_mag_W), true);
  const speed = { rpm: [1000, Infinity], Pfe_W: [1, 2], Pmag_W: [0.1, 0.2] };
  const fromCurve = M.scaleMotor({ ...base, speed }, k, 14);
  assert.equal(Number.isNaN(fromCurve.P_fe_W), true);
  assert.equal(Number.isNaN(fromCurve.P_mag_W), true);
});

test('a smaller valid pack explicitly refuses a speed above its new full-charge limit', () => {
  const limitRpm = 10183;
  assert.equal(L.speedRequestAccepted(13000, limitRpm), false);
  assert.equal(L.speedRequestAccepted(limitRpm, limitRpm), true);
  assert.equal(L.speedRequestAccepted(13000, null), true); // no known pack speed limit
  assert.equal(H.configureMotorHeat('sine', L.speedRequestAccepted(13000, limitRpm), baseHeat, 0), null);
  assert.equal(H.configureMotorHeat('pwm', L.speedRequestAccepted(13000, limitRpm), baseHeat, 10), null);
});

test('invalid battery is distinct from a machine with no battery', () => {
  const common = { vMax: null, m: 0.89, kvRpmPerV: 500, v0: null, v1000: null };
  assert.deepEqual(L.speedLimit({ ...common, batteryValid: false }), { rpm: null, basis: 'invalid_battery' });
  assert.deepEqual(L.speedLimit({ ...common, batteryValid: true }), { rpm: null, basis: 'no_battery' });
  const ranges = L.physicalRanges({ p: { L0_mm: 20, N0: 7, wireH0_mm: 0.6, I0_A: 35, rpm0: 13000 },
    fit: { slotHeight_mm: 6, insulation_mm: 0.1, wireSpacingY_mm: 0.05 }, N: 7, wireH_mm: 0.6,
    lMaxMm: null, iMaxA: null, speed: L.speedLimit({ ...common, batteryValid: false }) });
  assert.equal(ranges.rpm.basis, 'invalid_battery');
});
