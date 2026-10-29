// node --test — the Electromagnetic tab shows Configure's result tile panel, fed from the FEM run
// summary (owner 2026-10-12: «Сделай точно такую же панель в Electromagnetic»).  What is pinned:
//   * the run summary maps onto Configure's rows and tiles (labels, units, order);
//   * a tile whose value the run lacks is OMITTED — no empty tile, no zero — and an empty row
//     goes with it (so a plain magnetostatic run shows no temperature / controller tiles);
//   * the judgements and limits are Configure's (efficiency 92/85, ripple 5/15, J 12/25,
//     fill 60/75, T_j 25 K of its limit, the shared temperature judge);
//   * the temperature tiles ARE the ones Configure builds (one judge, both panels);
//   * every label / tooltip key the tiles use exists in EN and ZH;
//   * ONE results panel (owner 2026-10-13): the old card of small cells is gone from SummaryTable, every
//     value it carried lives in a tile, the part-mass row is the last row of the same tiles, and the
//     efficiency is the ONE shaft efficiency (bearings + windage in), not the electromagnetic one.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

// Node strips types but does not guess extensions for relative ESM imports; the tile modules
// import each other the way Vite does (no '.ts').  Resolve them for the tests only.
try {
  const m = await import('node:module');
  if (typeof m.registerHooks === 'function') {
    m.registerHooks({
      resolve(specifier, context, nextResolve) {
        try { return nextResolve(specifier, context); }
        catch (err) {
          if (/^\.{1,2}\//.test(specifier) && !/\.[cm]?[jt]sx?$/.test(specifier)) {
            return nextResolve(`${specifier}.ts`, context);
          }
          throw err;
        }
      },
    });
  }
} catch { /* no hooks: the imports below will just skip */ }

let E = null, C = null, S = null;
try {
  E = await import('../emResultTiles.ts');
  C = await import('../configuratorTiles.ts');
  S = await import('../shaftBalance.ts');
} catch { /* old Node */ }
const t = E ? test : test.skip;

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const read = (f) => readFileSync(join(SRC, f), 'utf8');
const loc = (l) => JSON.parse(read(`locales/${l}/controller.json`));
const near = (a, b, what = '') => assert.ok(Math.abs(a - b) < 1e-9, `${what}: ${a} ≈ ${b}`);
const one = (rows, id) => rows.flat().find((x) => x.id === id);

// a typical coupled run: everything the tiles can read is there (numbers are illustrative —
// the tiles only ever show fields of the summary, never a derived physics of their own)
const RUN = {
  T_em_avg_Nm: 0.42, T_ripple_pct: 3.2, torque_method: 'energy_flux',
  P_mech_W: 132, P_loss_total_W: 18, P_core_W: 6.5, P_stranded_W: 9, P_mag_W: 1.2,
  P_loss_stator_W: 15, P_loss_rotor_W: 3, P_loss_split_measured: true, loss_density_W_kg: 24,
  mass_total_kg: 1.2, mass_active_kg: 0.9, torque_per_mass_Nm_kg: 0.35, power_per_mass_W_kg: 110,
  V_phase_peak_V: 24.1, V_phase_rms_V: 16.4, V_line_peak_V: 41.7, V_line_rms_V: 28.5,
  V_line_peak_solved_V: 42, V_line_rms_solved_V: 28.8, star_delta: 'star',
  J_coil_A_per_mm2: 9.2, A_phase_mm2: 1.5, slot_fill_pct: 55,
  R_phase_ohm: 0.25, R_line_line_ohm: 0.5, coil_temp_C: 120, I_phase_rms_A: 14,
  Ld_mH: 0.12, Lq_mH: 0.19, ldq_method: 'differential', ldq_tangent_clamped: false,
  saliency_Lq_over_Ld: 1.58, psi_pm_Wb: 0.005,
  KV_rpm_per_V_line: 900, KV_noload_rpm_per_V_line: 950,
  Km_Nm_sqrtW: 0.021, Km_per_mass_Nm_sqrtW_kg: 0.0175,
  demag: { loss_pct: 0.4, area_derated_pct: 1.5, br_corner: { br_pct: 91.2, r_mm: 12.5, theta_deg: 3.1 } },
  saturation: { droop_pct: 6, T_linear_Nm: 0.45 },
  // what the OLD card carried and the first tile panel lacked
  op_mode: 'motor', P_shaft_W: 0.4, P_sleeve_W: 0.0012, B_gap_mean_T: 0.82,
  wire_parallel: 2, wire_split: 1, turns_per_coil: 9, n_parallel_eff: 4,
  rotor_inertia: { J_kg_cm2: 3.21, J_kg_m2: 3.21e-4 }, end3d: { k_flux: 0.962, T_corrected_Nm: 0.4, inherited: false },
  six_phase: { sets: [{ set: 1, I_phase_rms_A: 7, V1_phase_peak_V: 24 }], inductances: { Lxy_mH: 0.0123 } },
  P_core_terms: { stator: { hysteresis_W: 2, eddy_W: 1.5, excess_W: 1, model: 'bertotti' } },
  magnet_segmentation: { slice_mm: 5, stack_mm: 20, width_mm: 8, factor: 0.8 },
  mass_components: [
    { name: 'Stator core (B10AHV900M)', mass_kg: 0.6 }, { name: 'Copper windings (Cu)', mass_kg: 0.3 },
    { name: 'Magnets (N52UH_150C)', mass_kg: 0.1 }, { name: 'Shaft (S355)', mass_kg: 0, state: 'reference', mass_modelled_kg: 0.2 },
  ],
  coupling: {
    coil_temp_c: 185, magnet_temp_c: 110,
    controller: { t_j_c: 120, losses: { total_W: 9.5 }, efficiency: { shaft: 0.88, wall_to_shaft: 0.85 },
      thermal: { t_j_max_c: 175 } },
    time_to_limit: { limits_c: { winding: 200, magnet: 150 } },
    continuous_rating: {
      ok: true, verified: true, feasible: true, trustworthy: true,
      I_cont_A_rms: 11.2, power: { P_shaft_W: 980 }, limits_c: { winding: 200, magnet: 150 },
    },
  },
};

// the mechanical half of a machine WITH bearings (analytic SKF block): 3 W bearings + 1 W windage
const MECH = { bearings_W: 3, windage_W: 1, extra_W: 4, cards: '2 × 6805', temp_C: 60, gap_W: 0.7, faces_W: 0.3 };
const NO_BRG = { bearings_W: null, windage_W: 0.9, extra_W: null };
const VIEW = { rf: 1.5, k3dBasis: '3-D', mech: MECH };

t('the run maps onto Configure\'s rows and tiles, in Configure\'s order', () => {
  const rows = E.emTileRows(RUN, VIEW);
  assert.deepEqual(rows.map((r) => r.map((x) => x.id)), [
    ['torque', 'power', 'elecPower', 'mass', 'efficiency', 'tRipple', 'tPerMass', 'pPerMass'],
    ['totalLoss', 'ironLoss', 'copperLoss', 'magnetLoss', 'shaftLoss', 'sleeveLoss', 'bearings', 'windage',
      'statorHeat', 'rotorHeat', 'lossDensity', 'invLoss', 'tj', 'motorEff', 'wallToShaft', 'pContMax'],
    ['tWinding', 'tMagnet'],
    ['dcBusMin', 'vLinePeak', 'vPhasePeak', 'vLineRms', 'vPhaseRms', 'currDensity'],
    ['phaseSection', 'fillFactor', 'leadCable', 'rPhase', 'rLineLine', 'turnsCoil'],
    ['ld', 'lq', 'psiPm', 'lqLd', 'bGapMean', 'lxy', 'iSet', 'vSet'],
    ['kv', 'kt', 'km', 'kmPerMass', 'rotorInertia'],
    ['demagKoef', 'brCorner', 'saturationKoef', 'totalKoef', 'end3d'],
    ['part0', 'part1', 'part2', 'part3'],
  ]);
  // the PWM-loss and housing / air tiles are deliberately absent: the run summary has no such field
  for (const id of ['motorLoss', 'pwmLoss', 'tHousing', 'airSpeed']) assert.equal(one(rows, id), undefined, id);
});

t('every value is a field of the summary (or an arithmetic of fields on the same tiles)', () => {
  const rows = E.emTileRows(RUN, VIEW);
  assert.equal(one(rows, 'torque').value, 0.42);
  near(one(rows, 'power').value, 0.128, 'kW at the shaft');                 // 132 W rotor − 4 W bearings + windage
  near(one(rows, 'elecPower').value, 0.150, 'kW at the terminals');         // 132 W + 18 W electromagnetic loss
  assert.equal(one(rows, 'mass').value, 1.2);
  near(one(rows, 'efficiency').value, 100 * 128 / 150, 'efficiency');       // SHAFT power / electrical power
  assert.equal(one(rows, 'tRipple').value, 3.2);
  assert.equal(one(rows, 'tPerMass').value, 0.35);
  assert.equal(one(rows, 'pPerMass').value, 0.11);
  assert.equal(one(rows, 'totalLoss').value, 22);                           // 18 W electromagnetic + 4 W bearings + windage
  assert.equal(one(rows, 'shaftLoss').value, 0.4);
  assert.equal(one(rows, 'sleeveLoss').value, 0.0012);
  // a machine with no sleeve has no sleeve-loss tile at all (owner 2026-10-26)
  const bare = E.emTileRows({ ...RUN, P_sleeve_W: 0, mass_components: [{ name: 'Magnets', mass_kg: 0.1 }] }, VIEW);
  assert.equal(one(bare, 'sleeveLoss'), undefined);
  assert.ok(one(bare, 'shaftLoss'));
  assert.equal(one(rows, 'bearings').value, 3);
  assert.equal(one(rows, 'windage').value, 1);
  near(one(rows, 'wallToShaft').value, 85, 'wall to shaft');
  assert.equal(one(rows, 'ironLoss').value, 6.5);
  assert.equal(one(rows, 'copperLoss').value, 9);
  assert.equal(one(rows, 'magnetLoss').value, 1.2);
  assert.equal(one(rows, 'statorHeat').value, 15);
  assert.equal(one(rows, 'rotorHeat').value, 3);
  assert.equal(one(rows, 'lossDensity').value, 24);
  assert.equal(one(rows, 'invLoss').value, 9.5);
  assert.equal(one(rows, 'tj').value, 120);
  near(one(rows, 'motorEff').value, 88, 'motor eta');                       // controller.efficiency.shaft × 100
  assert.equal(one(rows, 'pContMax').value, 0.98);                          // P_shaft_W / 1000
  assert.equal(one(rows, 'dcBusMin').value, 42);                            // the solved peak line voltage
  assert.equal(one(rows, 'vLinePeak').value, 42);
  assert.equal(one(rows, 'vPhasePeak').value, 24.1);
  assert.equal(one(rows, 'vLineRms').value, 28.8);
  assert.equal(one(rows, 'vPhaseRms').value, 16.4);
  assert.equal(one(rows, 'currDensity').value, 9.2);
  assert.equal(one(rows, 'phaseSection').value, 1.5);
  assert.equal(one(rows, 'fillFactor').value, 55);
  assert.equal(one(rows, 'leadCable').kind, 'leadCable');
  assert.equal(one(rows, 'rPhase').value, 0.25 * 1000 * 1.5);               // mΩ at the card's R basis
  assert.equal(one(rows, 'rLineLine').value, 0.5 * 1000 * 1.5);
  assert.deepEqual(one(rows, 'rPhase').tipExtraKeys, ['emTiles.rAt25']);    // rf ≠ 1: the R@25 view
  assert.equal(one(rows, 'ld').value, 0.12);
  assert.equal(one(rows, 'lq').value, 0.19);
  near(one(rows, 'psiPm').value, 5, 'mWb');                                 // psi_pm_Wb × 1000
  assert.equal(one(rows, 'lqLd').value, 1.58);
  assert.equal(one(rows, 'bGapMean').value, 0.82);
  assert.equal(one(rows, 'lxy').value, 0.0123);
  assert.deepEqual([one(rows, 'iSet').value, one(rows, 'vSet').value], [7, 24]);
  assert.equal(one(rows, 'turnsCoil').value, 9);
  assert.equal(one(rows, 'rotorInertia').value, 3.21);
  assert.equal(one(rows, 'brCorner').value, 91.2);
  assert.deepEqual([one(rows, 'end3d').value, one(rows, 'end3d').display, one(rows, 'end3d').level], [0.962, '×0.962', 'warn']);
  assert.equal(one(rows, 'kv').value, 950);                                 // the no-load KV when the run has one
  assert.equal(one(rows, 'kv').tipKey, 'emTiles.kvTip');
  near(one(rows, 'kt').value, 0.42 / 14, 'Kt');                             // T / I phase RMS, measured here
  assert.equal(one(rows, 'kt').d, 4);
  assert.equal(one(rows, 'km').value, 0.021);
  assert.equal(one(rows, 'kmPerMass').value, 0.0175);
  near(one(rows, 'demagKoef').value, 99.6, 'demag');                        // 100 − loss_pct
  near(one(rows, 'saturationKoef').value, 94, 'saturation');                // 100 − droop_pct
  near(one(rows, 'totalKoef').value, 99.6 * 94 / 100, 'total');             // the product convention
});

t('a tile the run has no value for is OMITTED — no empty tile, no zero; empty rows go too', () => {
  assert.deepEqual(E.emTileRows({}), []);
  assert.deepEqual(E.emTileRows({ T_em_avg_Nm: 0, P_mech_W: 0, P_loss_total_W: 0 }).flat().map((x) => x.id),
    ['torque', 'power', 'elecPower', 'totalLoss']);        // a real 0 is a value; a missing field is not
  // a plain magnetostatic run: no coupled block at all, no mechanical block either
  const ms = E.emTileRows({ T_em_avg_Nm: 1.2, P_mech_W: 300, P_loss_total_W: 40 });
  assert.deepEqual(ms.map((r) => r.map((x) => x.id)), [['torque', 'power', 'elecPower', 'efficiency'], ['totalLoss']]);
  // the bearings / windage tiles exist only when the caller has the mechanical block
  assert.equal(one(ms, 'bearings'), undefined);
  // a delta winding shows the current in ONE winding; a star winding does not
  assert.equal(one(E.emTileRows({ star_delta: 'delta', I_winding_rms_A: 8 }), 'iWinding').value, 8);
  assert.equal(one(E.emTileRows({ star_delta: 'star', I_winding_rms_A: 8 }), 'iWinding'), undefined);
  // turns per coil only when the winding is several wires in hand / split into strips
  assert.equal(one(E.emTileRows({ turns_per_coil: 9, wire_parallel: 1, wire_split: 1 }), 'turnsCoil'), undefined);
  assert.equal(one(E.emTileRows({ turns_per_coil: 9, wire_parallel: 1, wire_split: 3 }), 'turnsCoil').value, 9);
  // delta connection: no line RMS in the summary → no tile (never a made-up number)
  const d = E.emTileRows({ V_line_rms_V: 28, V_phase_rms_V: 16, star_delta: 'delta' });
  assert.deepEqual(d.flat().map((x) => x.id), ['vPhaseRms']);
  // mass falls back to the EM-active mass only when the total is missing
  assert.equal(one(E.emTileRows({ mass_active_kg: 0.9 }), 'mass').value, 0.9);
  assert.equal(one(E.emTileRows({ mass_active_kg: 0.9 }), 'mass').tipVars.active, '0.9');
  // the loaded KV is shown under the no-load label only with the fallback said in the tooltip
  const kv = one(E.emTileRows({ KV_rpm_per_V_line: 900 }), 'kv');
  assert.deepEqual([kv.value, kv.tipKey], [900, 'emTiles.kvTipLoaded']);
});

t('the judgements and limits are the Configure ones', () => {
  const lvl = (id, s) => one(E.emTileRows(s), id)?.level;
  const eff = (P, L) => E.emTileRows({ P_mech_W: P, P_loss_total_W: L }, { mech: { bearings_W: 0, windage_W: 0, extra_W: 0 } })
    .flat().find((x) => x.id === 'efficiency')?.level;
  assert.equal(eff(920, 80), 'ok'); assert.equal(eff(850, 150), 'warn'); assert.equal(eff(800, 200), 'bad');
  const rip = (v) => lvl('tRipple', { T_ripple_pct: v });
  assert.equal(rip(5), 'ok'); assert.equal(rip(5.1), 'warn'); assert.equal(rip(15), 'warn'); assert.equal(rip(15.1), 'bad');
  const J = (v) => lvl('currDensity', { J_coil_A_per_mm2: v });
  assert.equal(J(12), 'ok'); assert.equal(J(12.1), 'warn'); assert.equal(J(25), 'warn'); assert.equal(J(25.1), 'bad');
  const F = (v) => lvl('fillFactor', { slot_fill_pct: v });
  assert.equal(F(60), 'ok'); assert.equal(F(60.1), 'warn'); assert.equal(F(75), 'warn'); assert.equal(F(75.1), 'bad');
  const tj = (v, max) => lvl('tj', { coupling: { controller: { t_j_c: v, thermal: { t_j_max_c: max } } } });
  assert.equal(tj(150, 175), 'ok'); assert.equal(tj(151, 175), 'warn'); assert.equal(tj(151, null), 'ok');
  const s1 = (o) => one(E.emTileRows({ coupling: { continuous_rating: { power: { P_shaft_W: 980 }, ...o } } }), 'pContMax');
  assert.equal(s1({ ok: true, verified: true }).level, 'ok');
  assert.equal(s1({ ok: true, verified: true, trustworthy: false }).level, 'warn');
  assert.equal(s1({ ok: true, verified: false }).level, 'warn');
  assert.ok(s1({ ok: true, verified: false }).tipExtraKeys.includes('emTiles.unverified'));
  assert.equal(s1({ ok: true, verified: true }).tipExtraKeys, undefined);
  // an unknown friction (no bearings) or an unsolved rotor eddy turns the efficiency amber, with a marker
  const noB = one(E.emTileRows({ P_mech_W: 920, P_loss_total_W: 80 }, { mech: NO_BRG }), 'efficiency');
  assert.deepEqual([noB.level, noB.display], ['warn', '92.00*']);
  const part = one(E.emTileRows({ P_mech_W: 920, P_loss_total_W: 80, solid_loss_not_solved: true }, { mech: MECH }), 'efficiency');
  assert.equal(part.level, 'warn'); assert.ok(part.tipExtraKeys.includes('emTiles.partial'));
  // the coefficients: kept-percent, Configure's thresholds
  assert.equal(lvl('demagKoef', { demag: { loss_pct: 0.5 } }), 'ok');
  assert.equal(lvl('demagKoef', { demag: { loss_pct: 2 } }), 'warn');
  assert.equal(lvl('demagKoef', { demag: { loss_pct: 6 } }), 'bad');
  assert.equal(lvl('saturationKoef', { saturation: { droop_pct: 4 } }), 'ok');
  assert.equal(lvl('saturationKoef', { saturation: { droop_pct: 10 } }), 'warn');
  assert.equal(lvl('saturationKoef', { saturation: { droop_pct: 20 } }), 'bad');
  // an unmeasured loss split: the rotor-heat tile says so and turns amber
  const split = one(E.emTileRows({ P_loss_rotor_W: 3, P_loss_split_measured: false }), 'rotorHeat');
  assert.deepEqual([split.level, split.tipExtraKeys], ['warn', ['emTiles.splitUnknown']]);
});

t('the temperature tiles are the SAME tiles Configure builds (one judge, both panels)', () => {
  for (const [T, limit, want] of [[120, 200, 'ok'], [185, 200, 'warn'], [210, 200, 'bad']]) {
    const em = one(E.emTileRows({ coupling: { coil_temp_c: T, time_to_limit: { limits_c: { winding: limit, magnet: 150 } } } }), 'tWinding');
    const cfg = C.tempRowTiles({ T_winding_C: T, T_magnet_C: 60, T_housing_C: 40 }, { winding_C: limit, magnet_C: 150 })
      .find((x) => x.id === 'tWinding');
    assert.equal(em.level, want);
    for (const k of ['value', 'display', 'level', 'unit', 'd', 'labelKey', 'tipKey']) {
      assert.deepEqual(em[k], cfg[k], `T=${T} ${k}`);
    }
    assert.deepEqual(em.tipVars, { limit: String(limit) });                 // the tooltip's {limit}
    // and it comes out of the shared builder itself, field for field
    const spec = C.tempTileSpec('tWinding', 'configurePropeller.tWinding', 'configurePropeller.tWindingTip', T, limit);
    for (const k of ['value', 'display', 'level', 'unit', 'd', 'labelKey', 'tipKey']) {
      assert.deepEqual(em[k], spec[k], `T=${T} shared ${k}`);
    }
  }
  assert.match(read('lib/emResultTiles.ts'), /tempTileSpec\(/);             // not a second temperature judge
});

t('the one warning line follows the run temperatures and the RUN\'s limits', () => {
  assert.equal(E.emWarning({}), null);
  assert.equal(E.emWarning({ coupling: { coil_temp_c: 150, magnet_temp_c: 110 } }), null);
  const both = E.emWarning({ coupling: { coil_temp_c: 205, magnet_temp_c: 151 } });
  assert.deepEqual([both.windingOver, both.magnetOver], [true, true]);
  const one2 = E.emWarning({ coupling: { coil_temp_c: 205 } });
  assert.deepEqual([one2.windingOver, one2.magnetOver], [true, false]);
  assert.equal(E.emWarning({ coupling: { magnet_temp_c: 151 } }).magnetOver, true);
  // the run's own limits win over the project defaults (180 K winding here)
  assert.equal(E.emWarning({ coupling: { coil_temp_c: 185, time_to_limit: { limits_c: { winding: 180, magnet: 150 } } } }).windingOver, true);
  assert.equal(E.emTempLimits({ coupling: { time_to_limit: { limits_c: { winding: 180, magnet: 140 } } } }).magnet_C, 140);
  assert.deepEqual([E.emTempLimits({}).winding_C, E.emTempLimits({}).magnet_C], [200, 150]);
});

t('the dq / torque tiles say which method produced them (the meaning differs from Configure)', () => {
  assert.equal(E.ldqMethodKey({ Ld_mH: 1, ldq_method: 'differential' }, true), 'emTiles.mDiff');
  assert.equal(E.ldqMethodKey({ Ld_mH: 1, ldq_method: 'differential', ldq_tangent_clamped: true }, true), 'emTiles.mSecant');
  assert.equal(E.ldqMethodKey({ Ld_mH: 1, ldq_method: 'secant' }, true), 'emTiles.mSecant');
  assert.equal(E.ldqMethodKey({ bench_ldq: { Ld_mH: 1, Lq_mH: 2, Lq_over_Ld: 2 } }, true), 'emTiles.mBench');
  assert.equal(E.torqueMethodKey({ torque_method: 'coulomb_virtual_work' }), 'emTiles.mCoulomb');
  assert.equal(E.torqueMethodKey({ torque_method: 'maxwell_stress' }), 'emTiles.mMaxwell');
  assert.equal(E.torqueMethodKey({ torque_method: 'energy_flux' }), 'emTiles.mHybrid');
  assert.equal(E.torqueMethodKey({}), null);
  const rows = E.emTileRows(RUN, { k3dBasis: '3-D' });
  assert.deepEqual(one(rows, 'ld').tipExtraKeys, ['emTiles.mDiff']);
  assert.deepEqual(one(rows, 'torque').tipExtraKeys, ['configure.ktTip3d', 'emTiles.mHybrid']);
  assert.deepEqual(one(rows, 'tRipple').tipExtraKeys, ['emTiles.mHybrid']);
  // the saliency tile carries the method of the SOURCE that fed it
  assert.deepEqual(one(rows, 'lqLd').tipExtraKeys, ['emTiles.mDiff']);
  const bench = E.emTileRows({ bench_ldq: { Ld_mH: 1, Lq_mH: 2, Lq_over_Ld: 2 } });
  assert.deepEqual(one(bench, 'lqLd').tipExtraKeys, ['emTiles.mBench']);
  // the R tiles say which temperature basis they carry
  assert.deepEqual(one(E.emTileRows(RUN, { rf: 1 }), 'rPhase').tipExtraKeys, ['emTiles.rAtSolve']);
});

t('every label and tooltip key the tiles use exists in EN and ZH', () => {
  const get = (o, k) => k.split('.').reduce((a, x) => (a == null ? a : a[x]), o);
  const keys = new Set();
  for (const rows of [E.emTileRows(RUN, VIEW), E.emTileRows(RUN, { ...VIEW, k3dBasis: null }), E.emTileRows({ KV_rpm_per_V_line: 1 }),
    E.emTileRows({ P_loss_rotor_W: 1, P_loss_split_measured: false }),
    // the variants that switch other tips on: no bearings, a generator, an unsolved / unsettled rotor eddy,
    // a delta winding, a surface-measured iron model, secant / bench inductances, an inherited 3-D factor
    E.emTileRows({ ...RUN, op_mode: 'generator', solid_loss_not_solved: true, star_delta: 'delta', I_winding_rms_A: 9,
      Ld_fp_secant_mH: 0.1, Lq_fp_secant_mH: 0.2, bench_ldq: { Ld_mH: 0.11, Lq_mH: 0.21, Lq_over_Ld: 1.9 },
      end3d: { k_flux: 0.99, inherited: true, T_corrected_Nm: 0.41 },
      P_core_terms: { stator: { hysteresis_W: 1, eddy_W: 1, excess_W: 1, model: 'measured_surface' } } }, { mech: NO_BRG }),
    E.emTileRows({ ...RUN, eddy_settled: false }, VIEW)]) {
    for (const x of rows.flat()) {
      if (x.labelKey) keys.add(x.labelKey);
      if (x.tipKey) keys.add(x.tipKey);
      for (const k of x.tipExtraKeys ?? []) keys.add(k);
    }
  }
  assert.ok(keys.size > 30, `${keys.size} keys`);
  for (const k of keys) {
    for (const [l, d] of [['en', loc('en')], ['zh-CN', loc('zh-CN')]]) {
      assert.equal(typeof get(d, k), 'string', `${l}: ${k}`);
    }
  }
});

t('Configure tiles are unchanged by the extraction (same ids, same judge, shared tile)', () => {
  assert.deepEqual([...C.DRIVE_ROW_IDS], ['tj', 'motorEff', 'pContMax']);
  assert.deepEqual([...C.LOSS_TAIL_IDS], ['motorLoss', 'invLoss']);
  assert.deepEqual([...C.TEMP_ROW_IDS], ['tWinding', 'tMagnet', 'tHousing', 'airSpeed']);
  const row = Object.fromEntries(C.tempRowTiles({ T_winding_C: 120, T_magnet_C: 100, T_housing_C: 60, air_speed_ms: 9.5, h_W_m2K: 61 },
    { winding_C: 200, magnet_C: 150 }).map((x) => [x.id, x]));
  assert.deepEqual([row.tWinding.value, row.tWinding.level], [120, 'ok']);
  assert.deepEqual([row.tMagnet.value, row.tMagnet.level], [100, 'ok']);
  const over = Object.fromEntries(C.tempRowTiles({ ...{ T_housing_C: 60 }, T_winding_C: 2263, T_magnet_C: 151 },
    { winding_C: 200, magnet_C: 150 }).map((x) => [x.id, x]));
  assert.deepEqual([over.tWinding.display, over.tWinding.level], ['> 200', 'bad']);
  // the tile itself is ONE component: Configure and the EM panel render the same MetricTile
  const tiles = read('components/common/ResultTiles.tsx');
  const panel = read('components/compare/ConfiguratorPanel.tsx');
  const em = read('components/simulation/EmResultTiles.tsx');
  for (const f of [panel, em]) assert.match(f, /from '\.\.\/common\/ResultTiles'/);
  for (const what of ['MetricTile', 'TileRow', 'LeadCableTile', 'TILE_W']) assert.match(tiles, new RegExp(`export const ${what}`));
  for (const what of ['MetricTile', 'LeadCableTile', 'pctDelta', 'PANEL', 'LABEL']) {
    assert.doesNotMatch(panel, new RegExp(`const ${what}\\b`), `ConfiguratorPanel keeps a local ${what}`);
  }
  assert.match(panel, /<LeadCableTile section_mm2=\{A_phase_mm2\} \/>/);
  assert.match(em, /<LeadCableTile/);
  // and the Electromagnetic tab mounts it above the detailed table
  assert.match(read('components/simulation/SummaryTable.tsx'), /<EmResultTiles summary=\{s\}/);
});

// ── ONE results panel, ONE efficiency (owner 2026-10-13) ─────────────────────────────────────────
t('the efficiency is the ONE shaft efficiency, bearings and windage in (the L155 screenshot numbers)', () => {
  // CIANO10 200 opt L155: 267.51 kW at the rotor, 3.83 kW electromagnetic loss, 349 W bearings, 89 W windage.
  // The first tile panel read 98.6 % (P / (P + EM loss)); the old card read 98.43 % (the shaft's).
  const s = { T_em_avg_Nm: 110, P_mech_W: 267510, P_loss_total_W: 3830, op_mode: 'motor' };
  const mech = { bearings_W: 349, windage_W: 89, extra_W: 438, cards: '2 x 6314', temp_C: 70 };
  const rows = E.emTileRows(s, { mech });
  near(one(rows, 'efficiency').value, 100 * (267510 - 438) / (267510 + 3830), 'shaft eta');
  assert.equal(one(rows, 'efficiency').value.toFixed(2), '98.43');               // the old card's figure, now the only one
  assert.notEqual(one(rows, 'efficiency').value.toFixed(1), '98.6');
  assert.equal(one(rows, 'efficiency').display, undefined);                      // known friction: no marker, no amber
  assert.equal(one(rows, 'efficiency').tipKey, 'emTiles.efficiencyTip');
  // POWER is the shaft power, ELEC POWER the terminal power, TOTAL LOSS every loss: the tiles add up
  near(one(rows, 'power').value, (267510 - 438) / 1000, 'shaft kW');
  near(one(rows, 'elecPower').value, (267510 + 3830) / 1000, 'terminal kW');
  near(one(rows, 'totalLoss').value, 3830 + 438, 'all losses');
  const P = one(rows, 'power').value, L = one(rows, 'totalLoss').value / 1000;
  near(one(rows, 'efficiency').value, 100 * P / (P + L), 'eta = P / (P + total loss)');
  // and the mechanical tiles carry their own watts
  assert.deepEqual([one(rows, 'bearings').value, one(rows, 'windage').value], [349, 89]);
  assert.deepEqual(one(rows, 'bearings').tipVars, { cards: '2 x 6314', temp: '70' });
  // the formula is report.shaft_view's, in the shared lib
  const B = S.shaftBalance(s, 438);
  near(100 * B.etaShaft, 100 * (267510 - 438) / (267510 + 3830), 'lib');
  near(100 * B.etaEm, 100 * 267510 / (267510 + 3830), 'em eta stays in the lib / tooltip');
});

t('a generator runs the balance the other way (report.shaft_view)', () => {
  const s = { P_mech_W: 500000, P_loss_total_W: 8000, op_mode: 'generator' };
  const rows = E.emTileRows(s, { mech: { bearings_W: 300, windage_W: 100, extra_W: 400 } });
  // the prime mover supplies the friction too; the terminals give the rotor power less the EM loss
  near(one(rows, 'power').value, 500.4, 'shaft kW');
  near(one(rows, 'elecPower').value, 492, 'terminal kW');
  near(one(rows, 'efficiency').value, 100 * 492 / 500.4, 'eta = elec / shaft');
  near(one(rows, 'totalLoss').value, 8400, 'all losses');
  near(one(rows, 'power').value - one(rows, 'totalLoss').value / 1000, one(rows, 'elecPower').value, 'P - L = elec');
});

t('no bearings = the friction is UNKNOWN, not zero: rotor power, the EM efficiency, flagged', () => {
  const rows = E.emTileRows({ P_mech_W: 1000, P_loss_total_W: 100 }, { mech: NO_BRG });
  near(one(rows, 'power').value, 1, 'rotor kW');
  const eff = one(rows, 'efficiency');
  near(eff.value, 100 * 1000 / 1100, 'electromagnetic eta');
  assert.deepEqual([eff.display, eff.level, eff.tipKey], ['90.91*', 'warn', 'emTiles.efficiencyTipNoMech']);
  assert.equal(one(rows, 'totalLoss').tipKey, 'emTiles.totalLossTipEm');
  assert.equal(one(rows, 'totalLoss').value, 100);
  // the bearings tile stays, as a dash with the reason, instead of silently disappearing; windage (no
  // bearings needed) keeps its number
  const b = one(rows, 'bearings');
  assert.deepEqual([b.value, b.blankTipKey], [null, 'emTiles.noBearings']);
  assert.equal(one(rows, 'windage').value, 0.9);
});

t('ONE panel: the old card of small cells is gone from SummaryTable, the charging card is not', () => {
  const st = read('components/simulation/SummaryTable.tsx');
  // none of the old card's cells or its row comments
  for (const gone of ['mechPower', 'elecPower', 'emLosses', 'coreLamination', 'strandedCopper', 'solidMagnets',
    'torqueDensity', 'powerDensity', 'shaftLoss', 'sleeveLoss', 'bGapMean', 'rotorInertia', 'brCorner', 'windage',
    'allLosses', 'wallToShaft', 'turnsCoil', 'jCoil', 'phaseSection', 'leadCable']) {
    assert.doesNotMatch(st, new RegExp(`tx\\('${gone}'\\)`), `the old card still has ${gone}`);
  }
  assert.doesNotMatch(st, /Row [1-7][a-z]? \u2014/);                             // the row banners of the old card
  assert.doesNotMatch(st, /mass_components\.map/);                              // the old part-mass strip
  assert.doesNotMatch(st, /const etaOne\b|const pShaft\b|const lossAll\b/);     // no second efficiency formula
  // the tile panel is mounted exactly once and the only Cells left are the battery-charging card's
  assert.equal(st.match(/<EmResultTiles /g).length, 1);
  assert.match(st, /mech=\{mechView\}/);
  // (the three coupled-run status lines above the panel are their own thing; nothing BELOW it but charging)
  const below = st.slice(st.indexOf('<EmResultTiles '));
  assert.ok(below.indexOf('<Cell ') > below.indexOf('CHARGING: generator'), 'a Cell outside the charging card');
  // and the panel renders the mass row through the same tile component
  assert.match(read('components/simulation/EmResultTiles.tsx'), /width=\{t\.width\}/);
});

t('the part masses are the LAST row, in the same tiles: "<part> (<material>)  <kg> / <share>"', () => {
  const rows = E.emTileRows(RUN, VIEW);
  const last = rows[rows.length - 1];
  assert.deepEqual(last.map((x) => x.id), ['part0', 'part1', 'part2', 'part3']);
  assert.deepEqual(last.slice(0, 3).map((x) => x.label), ['Stator core (B10AHV900M)', 'Copper windings (Cu)', 'Magnets (N52UH_150C)']);
  assert.deepEqual(last.slice(0, 3).map((x) => x.value), [0.6, 0.3, 0.1]);
  assert.deepEqual(last.slice(0, 3).map((x) => x.unit), ['kg \u00b7 50.0 %', 'kg \u00b7 25.0 %', 'kg \u00b7 8.3 %']);   // of 1.2 kg
  assert.deepEqual(last.map((x) => x.d), [3, 3, 3, 3]);
  // the customer's (reference) part is kept and says it is not billed
  const ref = last[3];
  assert.deepEqual([ref.value, ref.unit, ref.tipKey], [0.2, 'kg modelled', 'emTiles.partRefTip']);
  // two ordinary tiles wide, so the row keeps the grid of the rows above (TILE_W + the 6 px gap)
  const w = Number(read('components/common/ResultTiles.tsx').match(/export const TILE_W = (\d+)/)[1]);
  assert.equal(E.EM_WIDE_TILE_W, 2 * w + 6);
  assert.ok(last.every((x) => x.width === E.EM_WIDE_TILE_W));
  // a run without parts has no such row
  assert.equal(E.emTileRows({ T_em_avg_Nm: 1 }).length, 1);
});

t('every tile of the OLD card has a home in the one panel (nothing dropped silently)', () => {
  // old cell (SummaryTable label) -> the tile that carries it now
  const MAP = {
    'Torque T_em': 'torque', 'Mech power': 'power', 'Elec power': 'elecPower', 'Mass': 'mass',
    'Efficiency': 'efficiency', 'wall-to-shaft': 'wallToShaft', 'T ripple': 'tRipple',
    'Torque density': 'tPerMass', 'Power density': 'pPerMass',
    'EM losses': 'totalLoss',            // inside Total loss (its tooltip carries the EM share)
    'Core (lamination)': 'ironLoss', 'Stranded (copper)': 'copperLoss',
    'Solid (magnets)': 'magnetLoss',     // = magnet + shaft + sleeve eddy: the three tiles beside it
    'Shaft loss': 'shaftLoss', 'Magnet loss': 'magnetLoss', 'Sleeve loss': 'sleeveLoss', 'Loss density': 'lossDensity',
    'Stator heat': 'statorHeat', 'Rotor heat': 'rotorHeat', 'Bearings': 'bearings', 'Windage': 'windage',
    'All losses': 'totalLoss',
    'V_phase peak': 'vPhasePeak', 'V_line peak': 'vLinePeak', 'V_phase RMS': 'vPhaseRms', 'V_line RMS': 'vLineRms',
    'I_winding RMS': 'iWinding', 'J coil': 'currDensity',
    'Phase section': 'phaseSection', 'Fill factor': 'fillFactor', 'Lead cable': 'leadCable', 'R phase': 'rPhase',
    'R line-line': 'rLineLine', 'Turns/coil': 'turnsCoil',
    'Ld': 'ld', 'Lq': 'lq', 'psi_PM': 'psiPm', 'B gap mean': 'bGapMean', 'Lq/Ld': 'lqLd', 'L_xy': 'lxy',
    'Per set': 'iSet',                   // + vSet
    'KV': 'kv', 'Kt': 'kt', 'Km': 'km', 'Km/mass': 'kmPerMass', 'Rotor inertia': 'rotorInertia',
    'Demag koef': 'demagKoef', 'Br corner': 'brCorner', 'Saturation koef': 'saturationKoef',
    '3D end-effect': 'end3d', 'Total koef': 'totalKoef',
  };
  const rows = E.emTileRows({ ...RUN, star_delta: 'delta', I_winding_rms_A: 8 }, VIEW);
  const ids = new Set(rows.flat().map((x) => x.id));
  for (const [oldCell, id] of Object.entries(MAP)) assert.ok(ids.has(id), `${oldCell} -> ${id}`);
  assert.ok(ids.has('vSet'));
  // the details that lived in the old tooltips survive in the new ones
  assert.ok(one(rows, 'ironLoss').tipExtraKeys.includes('emTiles.ironSplitStator'));
  assert.ok(one(rows, 'magnetLoss').tipExtraKeys.includes('emTiles.magnetSeg'));
  assert.ok(one(rows, 'end3d').tipExtraKeys.includes('emTiles.end3dCorrected'));
});

t('the shared balance is a pure lib that SummaryTable no longer duplicates', () => {
  assert.match(read('lib/emResultTiles.ts'), /shaftBalance\(/);
  assert.match(read('lib/shaftBalance.ts'), /report\.py/);                       // says which formula it mirrors
});

// ── ripple accuracy (owner 2026-10-29, option 1) ───────────────────────────
// The configured gap layers run.  A ripple whose air-gap ring check failed is
// marked NOT ACCURATE (amber, "*", the ring error in the tooltip) until the
// engineer refines it; the refined value then IS the tile.
t('a ripple whose ring check failed is marked; the refined one replaces it', () => {
  const bad = { ...RUN, T_ripple_pct: 3.3, ripple_mesh_limited: true,
                ripple_self_check_rel: 0.52, gap_layers_effective: 1 };
  let tile = one(E.emTileRows(bad, VIEW), 'tRipple');
  assert.equal(tile.value, 3.3);
  assert.equal(tile.display, '3.3*');
  assert.equal(tile.level, 'warn');
  assert.equal(tile.tipKey, 'emTiles.tRippleNotAccurateTip');
  assert.equal(tile.tipVars.err, '52');
  assert.equal(tile.tipVars.layers, '1');
  const refined = { ...bad, ripple_refined: { T_ripple_pct: 1.9, passed: true,
    gap_layers_per_side: 3, base_gap_layers_per_side: 1, self_check_rel_to_ripple_scale: 0.03 } };
  tile = one(E.emTileRows(refined, VIEW), 'tRipple');
  assert.equal(tile.value, 1.9);
  assert.equal(tile.display, undefined);
  assert.equal(tile.level, 'ok');
  assert.equal(tile.tipKey, 'emTiles.tRippleRefinedTip');
  assert.deepEqual([tile.tipVars.layers, tile.tipVars.base, tile.tipVars.baseLayers], ['3', '3.3', '1']);
  // a passing run is quoted as it always was
  tile = one(E.emTileRows({ ...RUN, T_ripple_pct: 2, ripple_mesh_limited: false }, VIEW), 'tRipple');
  assert.equal(tile.tipKey, 'emTiles.tRippleTip');
  assert.equal(tile.display, undefined);
});

// ── the magnet temperature a plain run used (owner 2026-10-29) ─────────────
t('a plain run shows the magnet temperature it was solved at, typed or card', () => {
  const base = { ...RUN };
  delete base.coupling;
  const typed = one(E.emTileRows({ ...base, magnet_temp_used_c: 90, magnet_temp_source: 'typed',
    magnet_card: 'N52UH_150C', magnet_card_temp_c: 150, magnet_Br_used_T: 1.3099,
    magnet_knee_used_kA_m: -1280 }, VIEW), 'tMagnet');
  assert.equal(typed.value, 90);
  assert.equal(typed.labelKey, 'emTiles.tMagnetTyped');
  assert.equal(typed.tipKey, 'emTiles.tMagnetUsedTip');
  assert.deepEqual([typed.tipVars.card, typed.tipVars.cardT, typed.tipVars.br, typed.tipVars.knee],
    ['N52UH_150C', '150', '1.310', '-1280']);
  const card = one(E.emTileRows({ ...base, magnet_temp_used_c: 150, magnet_temp_source: 'card' }, VIEW), 'tMagnet');
  assert.equal(card.labelKey, 'emTiles.tMagnetCard');
  // a run that says nothing about it shows no tile (omission rule)
  assert.equal(one(E.emTileRows(base, VIEW), 'tMagnet'), undefined);
});
