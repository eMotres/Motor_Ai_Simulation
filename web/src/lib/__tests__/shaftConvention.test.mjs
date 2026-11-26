// node --test - the shaft-side convention (src/motor_ai_sim/shaft_convention.py, owner 2026-11-25) as the tiles read it:
// the server already put the torque factor (field part only) and the core loss on the shaft into P_mech_W, the efficiency,
// the per-mass figures, Kt and Km, so the browser must not apply the factor a second time; a motor at no load shows "—";
// a generator's power tile is the INPUT power; Compare never ranks two conventions together.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { stripTypeScriptTypes } from 'node:module';

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
} catch { /* no hooks */ }

const A = await import('../apply3d.ts');
const B = await import('../shaftBalance.ts');
const W = await import('../sweepSettings.ts');
const D = await import('../dutyEfficiency.ts');
let E = null;
try { E = await import('../emResultTiles.ts'); } catch { /* old Node */ }
const te = E ? test : test.skip;

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const read = (f) => readFileSync(join(SRC, f), 'utf8');
const near = (a, b, what = '') => assert.ok(Math.abs(a - b) < 1e-9, `${what}: ${a} vs ${b}`);
const one = (rows, id) => rows.flat().find((x) => x.id === id);

// a motor on the new convention: field torque 10 N*m (2-D) at 1000 rpm, k 0.9, core loss 30 W on the shaft
const w = 2 * Math.PI * 1000 / 60;
const kq = 0.9, fe = 30, loss = 100;
const pShaft = kq * 10 * w - fe;
const MOTOR = {
  shaft_convention: 'fe_on_shaft_k3d_v1', op_mode: 'motor', rpm: 1000,
  T_em_avg_Nm: 10, T_field_k_Nm: 9, T_shaft_Nm: pShaft / w, P_shaft_drag_W: fe,
  P_mech_W: pShaft, P_loss_total_W: loss, efficiency: pShaft / (pShaft + loss),
  torque_per_mass_Nm_kg: (pShaft / w) / 2, power_per_mass_W_kg: pShaft / 2, Km_Nm_sqrtW: 0.5,
  V_line_peak_V: 100, KV_rpm_per_V_line: 10, psi_pm_Wb: 0.02, end3d: { k_flux: kq },
};

test('apply3d: a marked summary keeps its shaft-side numbers; only the field torque and the flux quantities are scaled', () => {
  const s = A.apply3dToSummary(MOTOR);
  near(s.P_mech_W, MOTOR.P_mech_W, 'P_mech_W not scaled again');
  near(s.efficiency, MOTOR.efficiency, 'efficiency not recomputed');
  near(s.torque_per_mass_Nm_kg, MOTOR.torque_per_mass_Nm_kg, 'T/kg');
  near(s.power_per_mass_W_kg, MOTOR.power_per_mass_W_kg, 'P/kg');
  near(s.Km_Nm_sqrtW, MOTOR.Km_Nm_sqrtW, 'Km');
  near(s.T_em_avg_Nm, 9, 'the 2-D field torque gets the factor');
  near(s.V_line_peak_V, 90, 'voltages keep the flux factor');
  near(s.KV_rpm_per_V_line, 10 / kq, 'KV');
  near(s.psi_pm_Wb, 0.018, 'psi_pm');
  // an unmarked summary is scaled exactly as before
  const old = A.apply3dToSummary({ ...MOTOR, shaft_convention: undefined });
  near(old.P_mech_W, pShaft * kq, 'legacy summary: power x k');
});

test('shaftBalance: a marked motor at no load keeps its negative shaft power and has no efficiency', () => {
  const b = B.shaftBalance({ shaft_convention: 'fe_on_shaft_k3d_v1', op_mode: 'motor', P_mech_W: -25, P_loss_total_W: 40 }, null);
  assert.equal(b.noOutput, true);
  assert.equal(b.pShaft, -25);
  assert.equal(b.eta, null);
  near(b.pElec, 15, 'P_elec = P_mech + loss');
  // the earlier convention is unchanged: magnitudes
  const o = B.shaftBalance({ op_mode: 'motor', P_mech_W: -25, P_loss_total_W: 40 }, null);
  assert.equal(o.pRotor, 25);
  assert.ok(!o.noOutput);
});

te('tiles: the torque tile is the shaft torque; no load shows "—" with the reason; a generator shows its input', () => {
  const rows = E.emTileRows(MOTOR, { mech: null });
  near(one(rows, 'torque').value, pShaft / w, 'shaft torque on the tile');
  assert.equal(one(rows, 'torque').tipKey, 'emTiles.torqueShaftTip');
  assert.equal(one(rows, 'power').tipKey, 'emTiles.powerShaftTip');
  near(one(rows, 'power').value, pShaft / 1000, 'power tile = P_mech_W');
  near(one(rows, 'efficiency').value, 100 * pShaft / (pShaft + loss), 'efficiency tile');

  const idle = { ...MOTOR, T_em_avg_Nm: 0.01, T_shaft_Nm: -0.2, P_mech_W: -0.2 * w, efficiency: 0 };
  const ri = E.emTileRows(idle, { mech: null });
  assert.equal(one(ri, 'power').display, '—');
  assert.equal(one(ri, 'power').tipKey, 'emTiles.powerNoLoadTip');
  assert.equal(one(ri, 'efficiency'), undefined, 'no efficiency tile at no load');

  const pin = kq * 10 * w + fe;
  const gen = { ...MOTOR, op_mode: 'generator', T_shaft_Nm: pin / w, P_mech_W: pin, efficiency: (pin - loss) / pin };
  const rg = E.emTileRows(gen, { mech: null });
  assert.equal(one(rg, 'power').labelKey, 'configure.powerInput');
  assert.equal(one(rg, 'torque').labelKey, 'configure.torqueInput');
  near(one(rg, 'power').value, pin / 1000, 'input power = k*T*w + D');
  near(one(rg, 'efficiency').value, 100 * (pin - loss) / pin, 'generator efficiency');
});

test('tiles: every new locale key exists in EN and zh-CN', () => {
  for (const l of ['en', 'zh-CN']) {
    const c = JSON.parse(read(`locales/${l}/controller.json`));
    for (const k of ['powerInput', 'torqueInput']) assert.ok(c.configure[k], `${l} configure.${k}`);
    for (const k of ['torqueShaftTip', 'torqueShaftTipGen', 'powerShaftTip', 'powerShaftTipGen', 'powerNoLoadTip']) {
      assert.ok(c.emTiles[k], `${l} emTiles.${k}`);
    }
    assert.ok(JSON.parse(read(`locales/${l}/simulation.json`)).shaftConvMixed, `${l} simulation.shaftConvMixed`);
  }
});

test('Compare: rows of two shaft conventions are not comparable; one convention is', () => {
  assert.equal(W.shaftConventionComparable([{ shaft_conv: 1 }, { shaft_conv: 1 }]), true);
  assert.equal(W.shaftConventionComparable([{}, { efficiency: 0.9 }]), true);
  assert.equal(W.shaftConventionComparable([{ shaft_conv: 1 }, {}]), false);
  assert.equal(W.shaftConventionComparable([{ shaft_convention: 'fe_on_shaft_k3d_v1' }, { shaft_conv: 1 }]), true);
  assert.equal(W.shaftConventionComparable([{ shaft_convention: 'fe_on_shaft_k3d_v1' }, {}]), false);
  // ComparePanel applies the rule to the ranking, the colours and the delta row
  const panel = read('components/compare/ComparePanel.tsx');
  assert.match(panel, /shaftConventionComparable\(scope\.map/);
  assert.match(panel, /if \(selShaftMixed\) return NaN;/);
});

test('duty efficiency: a marked motor at no load has none; a delivered output keeps the formula', () => {
  assert.equal(D.dutyEfficiencyPct({ shaft_convention: 'fe_on_shaft_k3d_v1', op_mode: 'motor', P_loss_total_W: 40 }, -10), 0);
  near(D.dutyEfficiencyPct({ shaft_convention: 'fe_on_shaft_k3d_v1', op_mode: 'motor', P_loss_total_W: 100 }, 900),
    90, 'P/(P+L)');
});

te('tiles: with bearings known the torque tile is at the SAME boundary as the power tile (the coupling)', () => {
  const rows = E.emTileRows(MOTOR, { mech: { bearings_W: 15, windage_W: 5, extra_W: 20 } });
  const pw = one(rows, 'power').value * 1000;
  near(pw, pShaft - 20, 'power at the coupling');
  near(one(rows, 'torque').value, pw / w, 'torque = power / w');
});

test('sweep: a marked point is never scaled again, not even by the loaded machine factor', async () => {
  const S3 = await import('../sweepApply3d.ts');
  assert.equal(S3.pointApply3dK({ shaft_convention: 'fe_on_shaft_k3d_v1', end3d: { k_flux: 0.9 } }, 0.88, true), null);
  assert.equal(S3.pointApply3dK({ end3d: { k_flux: 0.9 } }, 0.88, true), 0.9);
  const f = S3.shaftPointFields({ shaft_convention: 'fe_on_shaft_k3d_v1', T_shaft_Nm: 1.2, op_mode: 'motor', x: 1 });
  assert.equal(f.T_shaft_Nm, 1.2); assert.equal(f.x, undefined);
  assert.deepEqual(S3.shaftPointFields({ T_shaft_Nm: 1.2 }), {});
  assert.match(read('components/sweep/SweepStudyPanel.tsx'), /\.\.\.\(p\.sc \|\| \{\}\)/);
});

test('duty-cycle torque: a marked run gives the shaft torque, a generator its input', async () => {
  const DC = await import('../../components/thermal/dutyCycleTorque.ts');
  assert.equal(DC.dutyTorqueNm({ summary: { shaft_convention: 'fe_on_shaft_k3d_v1', op_mode: 'motor', T_shaft_Nm: 8.7,
    T_field_k_Nm: 9, T_em_avg_Nm: 10, end3d: { k_flux: 0.9 } } }), 8.7);
  // a generator's duty-chart torque is its shaft INPUT (Astra r2), not the field torque 9
  assert.equal(DC.dutyTorqueNm({ summary: { shaft_convention: 'fe_on_shaft_k3d_v1', op_mode: 'generator', T_shaft_Nm: 9.3,
    T_field_k_Nm: 9, T_em_avg_Nm: 10 } }), 9.3);
  assert.equal(DC.dutyTorqueNm({ summary: { T_em_avg_Nm: 10, end3d: { k_flux: 0.9 } } }), 9);
});

// ── Astra review round 2 ──
te('tiles: a negative shaft power keeps the coupling boundary too (-50 W -> -0.5 N*m at 100 rad/s)', () => {
  const rpm100 = 100 * 60 / (2 * Math.PI);
  const idle = { shaft_convention: 'fe_on_shaft_k3d_v1', op_mode: 'motor', rpm: rpm100, T_em_avg_Nm: 0.0,
    T_shaft_Nm: -0.3, P_mech_W: -30, P_loss_total_W: 31, end3d: null };
  const rows = E.emTileRows(idle, { mech: { bearings_W: 15, windage_W: 5, extra_W: 20 } });
  near(one(rows, 'torque').value, -0.5, 'coupling torque at no load');
  near(one(rows, 'power').value * 1000, -50, 'coupling power at no load');
});

test('apply3d is idempotent', () => {
  const once = A.apply3dToSummary({ T_em_avg_Nm: 10, P_mech_W: 1000, P_loss_total_W: 100, V_line_peak_V: 100, end3d: { k_flux: 0.9 } });
  const twice = A.apply3dToSummary(once);
  near(twice.T_em_avg_Nm, 9, 'one factor'); near(twice.V_line_peak_V, 90, 'flux once');
  const m = A.apply3dToSummary(A.apply3dToSummary(MOTOR));
  near(m.T_em_avg_Nm, 9, 'marked: one factor');
});

test('mechanical: the shown torque of a marked run is the shaft torque at the coupling', async () => {
  const M = await import('../shaftCoupling.ts');
  const rpm100 = 100 * 60 / (2 * Math.PI);
  const g = { shaft_convention: 'fe_on_shaft_k3d_v1', op_mode: 'generator', rpm: rpm100, P_mech_W: 930, T_em_avg_Nm: 9 };
  near(M.shaftCouplingTorqueNm(g), 9.3, 'generator input');
  near(M.shaftCouplingTorqueNm({ ...g, P_mech_extra_W: 20 }), 9.5, 'generator input at the coupling');
  near(M.shaftCouplingTorqueNm({ ...g, op_mode: 'motor', P_mech_W: 870, P_mech_extra_W: 20 }), 8.5, 'motor at the coupling');
  assert.equal(M.shaftCouplingTorqueNm({ T_em_avg_Nm: 9, P_mech_W: 900, rpm: rpm100 }), null);
});

test('development steps table: two conventions are marked and not ranked', async () => {
  const R = await import('../../modules/development/comparisonRows.ts');
  const mk = (conv) => ({ id: String(conv), variant: 'v', motor: {}, result: {}, hasResult: true, inputs: {}, history: false,
    estimate: false, values: { efficiency: 90, keyPowerKw: 1, keyTorque: 1, torqueMass: 1, shaftConv: conv } });
  const t = (k) => k;
  const rows = [mk(1), mk(0)];
  const cols = R.developmentComparisonColumns(t, rows);
  const eff = cols.find((c) => c.key === 'efficiency');
  assert.equal(eff.format(90, rows[1]).endsWith('†'), true);
  assert.equal(eff.format(90, rows[0]).endsWith('†'), false);
  assert.deepEqual(eff.rankValues(rows), []);
  assert.match(eff.title(90, rows[1]), /common:shaftConvOld/);
  const single = R.developmentComparisonColumns(t, [mk(1), mk(1)]).find((c) => c.key === 'efficiency');
  assert.equal(single.format(90, rows[0]).endsWith('†'), false);
});

test('catalog by die: efficiency of two conventions sorts in two groups', async () => {
  const C = await import('../catalogByDie.ts');
  const m = (e, c) => ({ values: { efficiency_pct: e, shaft_conv: c } });
  const sorted = C.sortMotors([m(95, 0), m(90, 1), m(92, 1)], 'eff', 'desc');
  assert.deepEqual(sorted.map((x) => [x.values.efficiency_pct, x.values.shaft_conv]), [[92, 1], [90, 1], [95, 0]]);
});

test('steps feed: a marked light panel renders its own power (870 W), never a second factor (783 W)', async () => {
  const R = await import('../resultsPanel.ts');
  const rpm100 = 100 * 60 / (2 * Math.PI);
  const light = { summary: { shaft_convention: 'fe_on_shaft_k3d_v1', op_mode: 'motor', rpm: rpm100, T_em_avg_Nm: 10,
    T_shaft_Nm: 8.7, T_field_k_Nm: 9, P_mech_W: 870, P_loss_total_W: 100, efficiency: 870 / 970, end3d: { k_flux: 0.9 } } };
  const c = R.finalComparison(light, {});
  near(c.power_w, 870, 'power'); near(c.torque_nm, 8.7, 'torque'); assert.equal(c.shaft_conv, 1);
});

test('a variant whose duties carry two conventions has no average efficiency', async () => {
  const G = await import('../../modules/development/motorGroups.ts');
  const same = { results: [{ comparison: { efficiency_pct: 90, shaft_conv: 1 } }, { comparison: { efficiency_pct: 88, shaft_conv: 1 } }] };
  near(G.averageEfficiency(same), 89, 'avg');
  const mixed = { results: [{ comparison: { efficiency_pct: 90, shaft_conv: 1 } }, { comparison: { efficiency_pct: 95 } }] };
  assert.equal(G.averageEfficiency(mixed), null);
});

test('shaftBalance (r4 C2): a marked motor with 0 < P < X keeps the signed net, -14 W, efficiency 0', () => {
  const b = B.shaftBalance({ shaft_convention: 'fe_on_shaft_k3d_v1', op_mode: 'motor', P_mech_W: 6, P_loss_total_W: 31 }, 20);
  near(b.pShaft, -14, 'net'); near(b.pShaft / 100, -0.14, 'torque at 100 rad/s');
  assert.equal(b.etaShaft, 0); assert.equal(b.noOutput, true);
  const old = B.shaftBalance({ op_mode: 'motor', P_mech_W: 6, P_loss_total_W: 31 }, 20);
  assert.equal(old.pShaft, 0); assert.equal(old.noOutput, undefined);
});

test('dutyLocalApply (r4 C5): a marked duty row without its summary is never rebuilt from requested targets', () => {
  const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'dutyLocalApply.ts'), 'utf8');
  const guard = src.indexOf('if (Number((rr as any).shaft_conv) === 1) {');
  const fallback = src.indexOf('if (rr.efficiency_pct != null || dd.torque_nm != null) {');
  assert.ok(guard > 0 && fallback > guard, 'the marked guard precedes the target fallback');
  assert.ok(!src.includes("shaft_convention: 'fe_on_shaft_k3d_v1', T_shaft_Nm: T"), 'no marker is stamped on targets');
});

test('r5 R3/R4: rebuilt and compact points never invent shaft power or receive another k', async () => {
  const S = await import('../sweepApply3d.ts');
  const P = await import('../autoPointPower.ts');
  const rebuilt = { shaft_convention: 'fe_on_shaft_k3d_v1', torque_per_mass_Nm_kg: 4.35, efficiency: .8969 };
  assert.equal(S.pointApply3dK(rebuilt, .9, true), null);
  const marked = { shaft_conv: 1, torque: 10, T_shaft_Nm: 8.7, P_mech_W: 870 };
  assert.equal(P.autoPointPowerKw(marked, 100), .87);
  assert.equal(P.autoPointFieldTorque(marked), 10);
  assert.ok(Number.isNaN(P.autoPointPowerKw({ ...marked, P_mech_W: null }, 100)));
  assert.ok(Number.isNaN(P.autoPointFieldTorque({ shaft_conv: 1, td: 4.35, mass: 2 })));
  assert.equal(P.autoPointPowerKw({ torque: 10 }, 100), 1);
  assert.equal(P.autoPointPowerKw({ ...marked, P_mech_W: 0 }, 100), 0);
  const panel = read('components/sweep/SweepStudyPanel.tsx');
  assert.equal((panel.match(/shaft_convention === 'fe_on_shaft_k3d_v1' \? NaN/g) || []).length, 3);
  assert.match(panel, /throw new Error\(tx\('shaftPowerMissing'\)\)/);
});

test('r5 R5: point efficiency tooltip names its convention; targets remain targets', async () => {
  const S = await import('../simPoints.ts');
  for (const lang of ['en', 'zh-CN']) {
    const c = JSON.parse(read(`locales/${lang}/common.json`));
    const basis = (marked) => c[marked ? 'shaftBasisCurrent' : 'shaftBasisEarlier'];
    for (const marked of [false, true]) {
      const d = { name: 'rated', power_kw: 1, torque_nm: 10, result: { efficiency_pct: 89.69, ...(marked ? { shaft_conv: 1 } : {}) } };
      const tip = S.pointTip(d, { duties: [d] }, x => x, undefined, basis);
      assert.ok(tip.includes(basis(marked)));
      assert.ok(tip.includes('10 N·m') && tip.includes('1.0 kW'));
    }
    const different = S.pointTip({ name: 'rated', result: { efficiency_pct: 90 },
      terminal_loss: { efficiency_at_terminals: .85, shaft_conv: 1, P_leads_board_W: 10 } }, {}, x => x, undefined, basis);
    assert.ok(different.includes(basis(false)) && different.includes(basis(true)));
    const sim = JSON.parse(read(`locales/${lang}/simulation.json`));
    for (const key of ['fieldTorque', 'shaftPower', 'shaftPowerMissing', 'mixedShaftTraining']) assert.ok(sim[key]);
    const ctrl = JSON.parse(read(`locales/${lang}/controller.json`));
    for (const key of ['femShaftBasis', 'femEarlierBasis']) assert.ok(ctrl.configure[key]);
  }
  assert.match(read('components/compare/ConfiguratorPanel.tsx'), /<Tooltip title=\{draftHeadline.shaft_conv === 1/);
});

test('audit: mechanical readers keep missing marked torque missing across stored sources', async () => {
  const { shaftCouplingTorqueNm } = await import('../shaftCoupling.ts');
  const source = read('components/mechanical/api.ts');
  const names = ['readSimSetting', 'readSimTorqueNm', 'readShownTorqueNm', 'coupledPoint'];
  const selected = names.map(name => {
    const match = source.match(new RegExp(`export function ${name}\\b[\\s\\S]*?^}`, 'm'));
    assert.ok(match, name);
    return match[0].replace(/^export /, '');
  }).join('\n');
  const compiled = stripTypeScriptTypes(selected);
  const saved = new Map();
  const localStorage = { getItem: k => saved.get(k) ?? null };
  const exports = {};
  new Function('exports', 'localStorage', 'shaftCouplingTorqueNm', compiled + `\nObject.assign(exports, {${names.join(',')}});`)(exports, localStorage, shaftCouplingTorqueNm);
  saved.set('sim.coupled', JSON.stringify(true));
  saved.set('sim.rpm', '1000');
  saved.set('sim.lastTransient', JSON.stringify({ T_avg_Nm: 10 }));
  const marked = { shaft_convention: 'fe_on_shaft_k3d_v1', T_em_avg_Nm: 10, rpm: 1000 };
  saved.set('sim.lastSummary', JSON.stringify(marked));
  assert.equal(exports.readShownTorqueNm(), null);
  assert.equal(exports.coupledPoint().torque, null);
  saved.set('sim.lastTransient', JSON.stringify({ summary: marked, T_avg_Nm: 10 }));
  assert.equal(exports.readSimTorqueNm(), null);
  saved.set('sim.lastSummary', JSON.stringify({ T_em_avg_Nm: 10 }));
  assert.equal(exports.coupledPoint().torque, 10);
});

test('audit: bilingual basis labels exist in every namespace used by the readers', () => {
  for (const ns of ['common', 'controller', 'simulation']) {
    const en = JSON.parse(read(`locales/en/${ns}.json`));
    const zh = JSON.parse(read(`locales/zh-CN/${ns}.json`));
    for (const k of ['shaftBasisCurrent', 'shaftBasisEarlier']) {
      assert.ok(en[k]); assert.match(zh[k], /[\u3400-\u9fff]/);
    }
  }
});


test('r6 B2: Compare uses stored marked power and densities; legacy formulas are unchanged', async () => {
  const { perMass, shownMass } = await import('../fullMass.ts');
  const source = read('components/compare/ComparePanel.tsx');
  const block = source.match(/const RESULT_COLS: ResultCol\[\] = \[[\s\S]*?^\];/m);
  assert.ok(block);
  const cols = new Function('perMass', 'shownMass', stripTypeScriptTypes(block[0]) + '\nreturn RESULT_COLS;')(perMass, shownMass);
  const power = cols.find(c => c.key === 'P_mech_W');
  const density = cols.find(c => c.key === 'power_per_mass_W_kg');
  const row = { shaft_convention: 'fe_on_shaft_k3d_v1', T_em_avg_Nm: 10,
    rpm: 100 * 60 / (2 * Math.PI), P_mech_W: 870, mass_total_kg: 2, power_per_mass_W_kg: 435 };
  near(power.derive(row) * power.scale, .87);
  near(density.derive(row) * density.scale, .435);
  near(density.derive({ ...row, mass_full_kg: 3, power_per_mass_full_W_kg: 290 }), 290);
  for (const missing of [null, undefined]) {
    assert.ok(Number.isNaN(power.derive({ ...row, P_mech_W: missing })));
    assert.ok(Number.isNaN(density.derive({ ...row, power_per_mass_W_kg: missing })));
  }
  near(power.derive({ ...row, P_mech_W: 0 }), 0);
  near(density.derive({ ...row, power_per_mass_W_kg: 0 }), 0);
  const old = { ...row, shaft_convention: undefined };
  near(power.derive(old) * power.scale, 1);
  near(density.derive({ ...old, power_per_mass_W_kg: undefined }), 500);
  const title = source.match(/title: \(_v, s\) => \{ if \(r.key === 'T_em_avg_Nm'[\s\S]*? \},/);
  assert.ok(title);
  const tip = new Function('r', 'resultSrc', 'txSim', 'resultVal', 'ACTIVE_BASIS_TIP', 'return ({' + title[0] + '}).title;')(
    { key: 'T_em_avg_Nm' }, (_r, s) => s.results, x => x, () => 10, '');
  assert.equal(tip(10, { results: row }), 'fieldTorque');
  assert.equal(tip(10, { results: old }), undefined);
  for (const lang of ['en', 'zh-CN']) assert.ok(JSON.parse(read(`locales/${lang}/simulation.json`)).fieldTorque);
});

test('r6 sweep Apply: old stored zero falls back to field power, marked zero and missing stay distinct', () => {
  const source = read('components/sweep/SweepStudyPanel.tsx');
  const expr = source.match(/const Pmech = ([\s\S]*?);/);
  assert.ok(expr);
  const power = new Function('p', 'r2', 'T', 'omega', 'return ' + expr[1]);
  for (const value of [0, null, undefined]) near(power({}, { P_mech_W: value }, 10, 100), 1000);
  near(power({}, { P_mech_W: 870 }, 10, 100), 870);
  for (const point of [{ shaft_conv: 1 }, { sc: { shaft_convention: 'fe_on_shaft_k3d_v1' } }]) {
    near(power(point, { P_mech_W: 0 }, 10, 100), 0);
    near(power(point, { P_mech_W: 870 }, 10, 100), 870);
    assert.ok(Number.isNaN(power(point, {}, 10, 100)));
  }
});
