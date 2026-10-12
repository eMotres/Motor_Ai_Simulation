// node --test — the Electromagnetic tab shows Configure's result tile panel, fed from the FEM run
// summary (owner 2026-10-12: «Сделай точно такую же панель в Electromagnetic»).  What is pinned:
//   * the run summary maps onto Configure's rows and tiles (labels, units, order);
//   * a tile whose value the run lacks is OMITTED — no empty tile, no zero — and an empty row
//     goes with it (so a plain magnetostatic run shows no temperature / controller tiles);
//   * the judgements and limits are Configure's (efficiency 92/85, ripple 5/15, J 12/25,
//     fill 60/75, T_j 25 K of its limit, the shared temperature judge);
//   * the temperature tiles ARE the ones Configure builds (one judge, both panels);
//   * every label / tooltip key the tiles use exists in EN and ZH.
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

let E = null, C = null;
try {
  E = await import('../emResultTiles.ts');
  C = await import('../configuratorTiles.ts');
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
  demag: { loss_pct: 0.4 }, saturation: { droop_pct: 6 },
  coupling: {
    coil_temp_c: 185, magnet_temp_c: 110,
    controller: { t_j_c: 120, losses: { total_W: 9.5 }, efficiency: { shaft: 0.88 }, thermal: { t_j_max_c: 175 } },
    time_to_limit: { limits_c: { winding: 200, magnet: 150 } },
    continuous_rating: {
      ok: true, verified: true, feasible: true, trustworthy: true,
      I_cont_A_rms: 11.2, power: { P_shaft_W: 980 }, limits_c: { winding: 200, magnet: 150 },
    },
  },
};

t('the run maps onto Configure\'s rows and tiles, in Configure\'s order', () => {
  const rows = E.emTileRows(RUN, { rf: 1.5, k3dBasis: '3-D' });
  assert.deepEqual(rows.map((r) => r.map((x) => x.id)), [
    ['torque', 'power', 'mass', 'efficiency', 'tRipple', 'tPerMass', 'pPerMass'],
    ['totalLoss', 'ironLoss', 'copperLoss', 'magnetLoss', 'statorHeat', 'rotorHeat', 'lossDensity',
      'invLoss', 'tj', 'motorEff', 'pContMax'],
    ['tWinding', 'tMagnet'],
    ['dcBusMin', 'vLinePeak', 'vPhasePeak', 'vLineRms', 'vPhaseRms', 'currDensity'],
    ['phaseSection', 'fillFactor', 'leadCable', 'rPhase', 'rLineLine'],
    ['ld', 'lq', 'psiPm', 'lqLd'],
    ['kv', 'kt', 'km', 'kmPerMass'],
    ['demagKoef', 'saturationKoef', 'totalKoef'],
  ]);
  // the PWM-loss and housing / air tiles are deliberately absent: the run summary has no such field
  for (const id of ['motorLoss', 'pwmLoss', 'tHousing', 'airSpeed']) assert.equal(one(rows, id), undefined, id);
});

t('every value is a field of the summary (or an arithmetic of fields on the same tiles)', () => {
  const rows = E.emTileRows(RUN, { rf: 1.5, k3dBasis: '3-D' });
  assert.equal(one(rows, 'torque').value, 0.42);
  near(one(rows, 'power').value, 0.132, 'kW');
  assert.equal(one(rows, 'mass').value, 1.2);
  near(one(rows, 'efficiency').value, 100 * 132 / 150, 'efficiency');       // P / (P + total loss)
  assert.equal(one(rows, 'tRipple').value, 3.2);
  assert.equal(one(rows, 'tPerMass').value, 0.35);
  assert.equal(one(rows, 'pPerMass').value, 0.11);
  assert.equal(one(rows, 'totalLoss').value, 18);
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
    ['torque', 'power', 'totalLoss']);                     // a real 0 is a value; a missing field is not
  // a plain magnetostatic run: no coupled block at all
  const ms = E.emTileRows({ T_em_avg_Nm: 1.2, P_mech_W: 300, P_loss_total_W: 40 });
  assert.deepEqual(ms.map((r) => r.map((x) => x.id)), [['torque', 'power', 'efficiency'], ['totalLoss']]);
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
  const eff = (P, L) => lvl('efficiency', { P_mech_W: P, P_loss_total_W: L });
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
  for (const rows of [E.emTileRows(RUN, { rf: 1.5, k3dBasis: '3-D' }), E.emTileRows({ KV_rpm_per_V_line: 1 }),
    E.emTileRows({ P_loss_rotor_W: 1, P_loss_split_measured: false })]) {
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
