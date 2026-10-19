// node --test - the server's S1 thermal model in the browser (lib/s1Thermal.ts).  The parity with the server itself is
// tests/test_configure_thermal_parity.py (it runs this module under Node against propeller_s1).
// Imports lib/s1Thermal.ts + lib/thermalEstimate.ts (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let S = null; let T = null;
try { S = await import('../s1Thermal.ts'); T = await import('../thermalEstimate.ts'); } catch { /* old Node */ }
const t = S ? test : test.skip;

const geom = { statorOD_mm: 40, stackLength_mm: 12, numSlots: 12, slotHeight_mm: 6.1, slotWidth_mm: 2.84,
  insulation_mm: 0.07, coreThickness_mm: 1.9, airGap_mm: 0.25, magnetOD_mm: 23.5 };
const NONE = { housing_film_factor: 1, winding_air_area_m2: 0, rotor_air_area_m2: 0, gap_resistance_factor: 1 };

t('uncalibrated, the network IS the lumped estimate (same constants, same paths)', () => {
  const n = S.networkTemperatures(geom, 36, 3, 53, 69.6, 25, NONE);
  const l = T.estimateThermal(geom, { P_cu_W: 36, P_fe_W: 14, P_mag_W: 3 }, { h_Wm2K: 69.6, ambient_C: 25 });
  assert.ok(Math.abs(n.housing - l.T_housing_C) < 1e-9);
  assert.ok(Math.abs(n.winding - l.T_winding_C) < 1e-9);
  assert.ok(Math.abs(n.magnet - l.T_magnet_C) < 1e-9);
});

t('the calibrated paths to the air cool the winding and the rotor', () => {
  const cal = { housing_film_factor: 0.196, winding_air_area_m2: 0.0043, rotor_air_area_m2: 0.0028, gap_resistance_factor: 1 };
  const a = S.networkTemperatures(geom, 36, 3, 53, 69.6, 25, NONE);
  const b = S.networkTemperatures(geom, 36, 3, 53, 69.6, 25, cal);
  assert.ok(b.magnet < a.magnet && b.winding < a.winding);
});

const model = {
  schema: 'configure-thermal-1', R_phase_ohm: 0.01, R_temp_C: 20, torque_factor: 1, flux_temp_coeff_per_K: -0.001,
  magnet_ref_temp_C: 20, I_cap_A: 60,
  grid: { rpm: [1000, 4000, 8000], I_A: [10, 30, 60],
    T_Nm: [[0.3, 0.9, 1.8], [0.3, 0.9, 1.8], [0.3, 0.9, 1.8]],
    loss_W: { fe_stator: [[1, 1.1, 1.2], [6, 6.5, 7], [18, 19, 20]], cu_ac: [[0.1, 0.5, 2], [0.2, 1, 4], [0.4, 2, 8]] } },
  mech_W: { rpm: [1000, 8000], W: [0.1, 2] }, drives: {}, thermal_geom: geom, calibration: NONE, calibrated: false,
  slipstream_factor: null, limits: { winding_C: 200, magnet_C: 150 },
};

t('thermal_at: more current, hotter; the copper loss follows its own temperature', () => {
  const a = S.s1ThermalAt(model, 4000, 20, 300, 25, null);
  const b = S.s1ThermalAt(model, 4000, 40, 300, 25, null);
  assert.ok(a && b && b.T_winding_C > a.T_winding_C);
  const r20 = 3 * 40 * 40 * 0.01;
  assert.ok(b.losses_W.cu_dc > r20);                              // R(T_w) > R(20 C)
  assert.equal(S.s1ThermalAt(model, 9000, 20, 60, 25, null), null); // above the loss grid: nothing shown
});

t('load_point: the current makes the load torque at the magnets\' own temperature', () => {
  const lp = S.s1LoadPoint(model, 4000, 0.9, 300, 25, null);
  assert.ok(lp && lp.current_A > 30);                              // hot magnets: more than the cold 30 A
  assert.equal(S.s1LoadPoint(model, 4000, 5, 300, 25, null), null); // beyond the current cap
});
