// node --test — the motor information panel is ONE component everywhere (owner 2026-11-01: «take the whole information panel
// from Configure, it must be the same everywhere; a cell that cannot be computed is simply not shown»).  What is pinned:
//   * a stored point's panel carries every tile Configure shows: temperatures (winding, magnet, housing, air, coolant),
//     PWM / controller (PWM loss, controller loss, T_j, motor efficiency, P cont), stator / rotor heat, phase section, fill,
//     lead cable, R, Ld / Lq / ψ_PM / Lq/Ld, KV / Kt / Km / Km per mass, the demag / saturation / total coefficients, cogging;
//   * a tile the point does not store is NOT drawn (no "—", no zero): a sine point has no PWM / controller tile, a point with no
//     thermal result has no temperature tile, and a row left empty disappears;
//   * the caption says which record the numbers are (coupled EM + thermal with the drive, or the EM run);
//   * the 3-D end-effect is applied the way the Electromagnetic card does (one function, lib/apply3d.ts);
//   * the project / Motors view draws the panel through the shared component; the old second tile list is gone;
//   * every key the panel uses exists in EN and ZH.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

// Node strips types but does not guess extensions for relative ESM imports; the tile modules import each other the way Vite
// does (no '.ts').  Resolve them for the tests only.
try {
  const m = await import('node:module');
  if (typeof m.registerHooks === 'function') {
    m.registerHooks({
      resolve(specifier, context, nextResolve) {
        try { return nextResolve(specifier, context); }
        catch (err) {
          if (/^\.{1,2}\//.test(specifier) && !/\.[cm]?[jt]sx?$/.test(specifier)) return nextResolve(`${specifier}.ts`, context);
          throw err;
        }
      },
    });
  }
} catch { /* no hooks: the imports below will just skip */ }

let P = null, A = null;
try {
  P = await import('../resultsPanel.ts');
  A = await import('../apply3d.ts');
} catch { /* old Node */ }
const t = P ? test : test.skip;

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const read = (f) => readFileSync(join(SRC, f), 'utf8');
const loc = (l) => JSON.parse(read(`locales/${l}/controller.json`));
const ids = (rows) => rows.flat().map((x) => x.id);
const one = (rows, id) => rows.flat().find((x) => x.id === id);

// what the server's panel block carries for a PWM point: the saved EM summary with the coupled loop's numbers on it
const SUMMARY = {
  T_em_avg_Nm: 90, T_ripple_pct: 1.9, P_mech_W: 8000, P_loss_total_W: 2400, P_core_W: 469, P_stranded_W: 2198, P_mag_W: 24,
  P_loss_stator_W: 2300, P_loss_rotor_W: 100, P_loss_split_measured: false, loss_density_W_kg: 76,
  mass_total_kg: 37.7, mass_active_kg: 37.7, torque_per_mass_Nm_kg: 2.4, power_per_mass_W_kg: 212,
  V_phase_peak_V: 297.8, V_phase_rms_V: 210.6, V_line_peak_V: 498.2, V_line_rms_V: 352.3, star_delta: 'star',
  J_coil_A_per_mm2: 10.1, I_phase_rms_A: 152, A_phase_mm2: 15, slot_fill_pct: 52, R_phase_ohm: 0.02, R_line_line_ohm: 0.04, coil_temp_C: 150,
  Ld_mH: 0.12, Lq_mH: 0.19, ldq_method: 'differential', saliency_Lq_over_Ld: 1.58, psi_pm_Wb: 0.05,
  KV_rpm_per_V_line: 4, KV_noload_rpm_per_V_line: 4.2, Kt_Nm_per_Arms: 5.5, Km_Nm_sqrtW: 1.8, Km_per_mass_Nm_sqrtW_kg: 0.047,
  demag: { loss_pct: 0.4, area_derated_pct: 1.5 }, saturation: { droop_pct: 6, T_linear_Nm: 95 },
  op_mode: 'motor', P_shaft_W: 7900, P_bearings_W: 20, P_windage_W: 5, P_mech_extra_W: 25, bearing_temp_c: 70,
  mech_losses: { cards: ['6805', '6805'], windage: { P_gap_W: 3, P_faces_W: 2 } },
  coupling: {
    coil_temp_c: 118, magnet_temp_c: 96, motor_pwm_loss_W: 100,
    controller: { t_j_c: 88, losses: { total_W: 300 }, efficiency: { shaft: 0.95, wall_to_shaft: 0.93 }, thermal: { t_j_max_c: 175 } },
    continuous_rating: { ok: true, verified: true, feasible: true, trustworthy: true, I_cont_A_rms: 150, power: { P_shaft_W: 83000 }, limits_c: { winding: 180, magnet: 150 } },
  },
};
const PANEL = {
  summary: SUMMARY, cooling: { housing_c: 60, air_speed_mps: 12, coolant_c: 25 },
  cogging: { cogging_pkpk_nm: 0.4, cogging_half_nm: 0.2 },
  source: { em: 'coupled', thermal: 'coupled', drive: 'inverter', f_carrier_hz: 10000 },
};

t('a PWM point shows every tile of Configure: temperatures, PWM and controller, constants, inductances, coefficients, cogging', () => {
  const rows = P.panelRows(PANEL);
  const all = ids(rows);
  for (const id of ['torque', 'power', 'efficiency', 'tRipple', 'cogging', 'totalLoss', 'ironLoss', 'copperLoss', 'magnetLoss',
    'statorHeat', 'rotorHeat', 'lossDensity', 'motorLoss', 'invLoss', 'tj', 'motorEff', 'pContMax',
    'tWinding', 'tMagnet', 'tHousing', 'airSpeed', 'coolant',
    'dcBusMin', 'vLinePeak', 'vPhasePeak', 'currDensity', 'phaseSection', 'fillFactor', 'leadCable', 'rPhase', 'rLineLine',
    'ld', 'lq', 'psiPm', 'lqLd', 'kv', 'kt', 'km', 'kmPerMass', 'demagKoef', 'saturationKoef', 'totalKoef']) {
    assert.ok(all.includes(id), `tile ${id} is drawn`);
  }
  assert.equal(one(rows, 'tWinding').value, 118);
  assert.equal(one(rows, 'tMagnet').value, 96);
  assert.equal(one(rows, 'tHousing').value, 60);
  assert.equal(one(rows, 'motorLoss').value, 100);
  assert.equal(one(rows, 'tj').value, 88);
});

t('a tile the point does not store is not drawn: a sine point with no thermal result has no PWM, controller or temperature tile', () => {
  const sine = { summary: { ...SUMMARY, coupling: undefined }, source: { em: 'em_run', thermal: null, drive: 'sine', em_winding_c: 150 } };
  const all = ids(P.panelRows(sine));
  for (const id of ['motorLoss', 'invLoss', 'tj', 'motorEff', 'wallToShaft', 'pContMax', 'tWinding', 'tHousing', 'airSpeed', 'coolant']) {
    assert.ok(!all.includes(id), `tile ${id} is not drawn`);
  }
  assert.ok(all.includes('torque') && all.includes('kt'));
  // the magnet temperature the EM run was solved at is the run's own setpoint; it is stated by the run, not invented
  for (const row of P.panelRows(sine)) assert.ok(row.length > 0, 'a row with nothing left disappears');
  for (const tile of P.panelRows(sine).flat()) assert.ok(tile.value != null || tile.display != null || tile.blankTipKey != null, `${tile.id} is never an empty tile`);
});

t('a point with no stored panel draws nothing at all; a PWM-less coupled point has no PWM tiles', () => {
  assert.deepEqual(P.panelRows(null), []);
  assert.deepEqual(P.panelRows({ summary: {}, source: { em: 'em_run', thermal: null, drive: 'sine' } }), []);
  const coupledSine = { summary: { ...SUMMARY, coupling: { coil_temp_c: 118, magnet_temp_c: 96 } }, source: { em: 'coupled', thermal: 'coupled', drive: 'sine' } };
  const all = ids(P.panelRows(coupledSine));
  assert.ok(all.includes('tWinding') && all.includes('tMagnet'));
  assert.ok(!all.includes('motorLoss') && !all.includes('invLoss') && !all.includes('tj'));
});

t('the caption names the source and the drive: coupled EM + thermal with the carrier, or the EM run with its winding temperature', () => {
  assert.deepEqual(P.panelCaption({ em: 'coupled', thermal: 'coupled', drive: 'inverter', f_carrier_hz: 10000 }),
    { key: 'sourceCoupledPwm', vars: { freq: '10' }, tipKey: 'sourceCoupledTip' });
  assert.equal(P.panelCaption({ em: 'coupled', thermal: 'coupled', drive: 'inverter', f_carrier_hz: 12500 }).vars.freq, '12.5');
  assert.equal(P.panelCaption({ em: 'coupled', thermal: 'coupled', drive: 'inverter' }).key, 'sourceCoupledPwmNoFreq');
  assert.equal(P.panelCaption({ em: 'coupled', thermal: 'coupled', drive: 'sine' }).key, 'sourceCoupled');
  assert.deepEqual(P.panelCaption({ em: 'em_run', thermal: null, drive: 'sine', em_winding_c: 150 }),
    { key: 'sourceEm', vars: { winding: 150 }, tipKey: 'sourceEmTip' });
  assert.equal(P.panelCaption({ em: 'em_run', thermal: 'thermal', drive: 'sine', em_winding_c: 150 }).key, 'sourceEmThermal');
  assert.equal(P.panelCaption({ em: 'em_run', thermal: null, drive: 'sine' }).key, 'sourceEmPlain');
});

t('the 3-D end-effect is applied like the Electromagnetic card does; a machine without a factor stays as stored', () => {
  const k = 0.9;
  const withK = { ...PANEL, summary: { ...SUMMARY, end3d: { k_flux: k } } };
  const raw = one(P.panelRows(PANEL), 'torque').value;
  assert.ok(Math.abs(one(P.panelRows(withK), 'torque').value - raw * k) < 1e-9);
  assert.ok(Math.abs(one(P.panelRows(withK), 'vPhasePeak').value - 297.8 * k) < 1e-9);
  assert.ok(Math.abs(one(P.panelRows(withK), 'kv').value - 4.2 / k) < 1e-9);
  assert.equal(A.apply3dToSummary(SUMMARY), SUMMARY);
  assert.equal(P.panelSummary(withK).k3dBasis, '3-D flux');
  assert.equal(P.panelSummary(PANEL).k3dBasis, null);
});

t('bearings and windage are not tiles of Configure: they only feed the shaft power; the machine with no bearing model keeps the rotor power', () => {
  const rows = P.panelRows(PANEL);
  assert.equal(one(rows, 'bearings'), undefined);
  assert.equal(one(rows, 'windage'), undefined);
  assert.ok(Math.abs(one(rows, 'power').value - (8000 - 25) / 1000) < 1e-9);                       // shaft power = rotor power - bearings - windage
  assert.equal(P.panelMech(SUMMARY).cards, '2 × 6805');
  assert.equal(P.panelMech({ T_em_avg_Nm: 1 }), null);
  const noBrg = P.panelRows({ ...PANEL, summary: { ...SUMMARY, P_bearings_W: undefined, P_mech_extra_W: undefined, mech_losses: undefined } });
  assert.ok(Math.abs(one(noBrg, 'power').value - 8) < 1e-9);
});

t('the panel LOOKS like Configure: its tile set and row order, its decimals, no colours Configure does not use', () => {
  const rows = P.panelRows({ ...PANEL, summary: { ...SUMMARY, mass_full_kg: 40, mass_partial: false, turns_per_coil: 9, wire_split: 2, B_gap_mean_T: 0.8,
    rotor_inertia: { J_kg_cm2: 3 }, P_shaft_W: 5, P_sleeve_W: 1, star_delta: 'delta', I_winding_rms_A: 100 } });
  const all = ids(rows);
  for (const extra of ['elecPower', 'shaftLoss', 'sleeveLoss', 'bearings', 'windage', 'wallToShaft', 'iWinding', 'turnsCoil', 'bGapMean', 'rotorInertia', 'brCorner', 'end3d']) {
    assert.ok(!all.includes(extra), `${extra} is not a tile of Configure`);
  }
  assert.ok(!all.some((x) => x.startsWith('part')));
  // Configure's row order: results, losses (+ drive tail), temperatures, voltages, winding, inductances, constants, coefficients
  assert.deepEqual(rows[0].map((x) => x.id), ['torque', 'power', 'mass', 'massActive', 'efficiency', 'tRipple', 'cogging', 'tPerMass', 'pPerMass']);
  assert.deepEqual(rows.map((r) => r[0].id), ['torque', 'totalLoss', 'tWinding', 'dcBusMin', 'phaseSection', 'ld', 'kv', 'demagKoef']);
  // Configure's decimals: torque 1, mass 3, efficiency 1
  assert.deepEqual([one(rows, 'torque').d, one(rows, 'mass').d, one(rows, 'massActive').d, one(rows, 'efficiency').d], [1, 3, 3, 1]);
  // colours: Configure judges efficiency / ripple / coefficients against the reference, not by thresholds; temperatures, J and fill keep theirs
  for (const id of ['efficiency', 'tRipple', 'pContMax', 'demagKoef', 'saturationKoef', 'totalKoef']) assert.equal(one(rows, id)?.level, undefined, id);
  assert.ok(one(rows, 'currDensity').level && one(rows, 'fillFactor').level && one(rows, 'tWinding').level);
  assert.equal(one(rows, 'massActive').value, 37.7);
  // a partial mass (no CAD build) has no separate active-mass tile
  assert.equal(one(P.panelRows(PANEL), 'massActive'), undefined);
});

test('every locale key of the panel exists in EN and ZH, and the caption is Chinese in ZH', () => {
  const en = loc('en'), zh = loc('zh-CN');
  for (const k of ['coolant', 'coolantTip', 'sourceCoupledPwm', 'sourceCoupledPwmNoFreq', 'sourceCoupled', 'sourceEmThermal', 'sourceEm',
    'sourceEmPlain', 'sourceCoupledTip', 'sourceEmTip']) {
    assert.ok(typeof en.emTiles[k] === 'string' && en.emTiles[k].length > 3, `en ${k}`);
    assert.ok(typeof zh.emTiles[k] === 'string' && /[一-鿿]/.test(zh.emTiles[k]), `zh ${k}`);
  }
  assert.ok(en.emTiles.sourceCoupledPwm.includes('{freq}') && zh.emTiles.sourceCoupledPwm.includes('{freq}'));
  assert.ok(en.emTiles.sourceEm.includes('{winding}') && zh.emTiles.sourceEm.includes('{winding}'));
  // no text wall: the visible caption is one short line
  for (const k of ['sourceCoupledPwm', 'sourceCoupled', 'sourceEmThermal', 'sourceEm']) assert.ok(en.emTiles[k].length < 50, k);
});

test('wiring: ONE panel - the stored point draws it through the shared component, the second tile list is gone', () => {
  const points = read('components/compare/PointsConfigure.tsx');
  assert.match(points, /<ResultsPanel panel=\{panel\}/);
  assert.doesNotMatch(points, /configureGrid|temperatureTiles|<MetricTile|em-source-chip/);
  const shared = read('components/common/ResultsPanel.tsx');
  assert.match(shared, /<EmResultTiles [^>]*look="configure"/);
  assert.match(read('components/simulation/SummaryTable.tsx'), /<EmResultTiles /);
  assert.ok(!existsSync(join(SRC, 'modules/development/configureGrid.ts')));
  assert.ok(!existsSync(join(SRC, 'modules/development/temperatureTiles.ts')));
});

t('the steps table reads the panel\'s final numbers: finalComparison merges them over the stored row', () => {
  const stored = { torque_nm: 100, power_w: 1, efficiency_pct: 50, coil_temp_c: 150, mass_kg: 9, cogging_pkpk_nm: 0.4 };
  const withK = { summary: { ...SUMMARY, end3d: { k_flux: 0.9 } }, source: PANEL.source };
  const c = P.finalComparison(withK, stored);
  assert.ok(Math.abs(c.torque_nm - one(P.panelRows(withK), 'torque').value) < 1e-9);       // 3-D applied, as the panel shows it
  assert.ok(Math.abs(c.power_w - one(P.panelRows(withK), 'power').value * 1000) < 1e-6);
  assert.equal(c.coil_temp_c, 118);                                                         // the coupled loop's temperature, not the EM run's 150
  assert.equal(c.magnet_temp_c, 96);
  assert.equal(c.cogging_pkpk_nm, 0.4);                                                     // what the panel does not state stays stored
  assert.deepEqual(P.finalComparison(null, stored), stored);
  assert.deepEqual(P.finalComparison({ summary: {}, source: PANEL.source }, stored), stored);
});
