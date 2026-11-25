// node --test - the shaft-side convention (src/motor_ai_sim/shaft_convention.py, owner 2026-11-25) as the tiles read it:
// the server already put the torque factor (field part only) and the core loss on the shaft into P_mech_W, the efficiency,
// the per-mass figures, Kt and Km, so the browser must not apply the factor a second time; a motor at no load shows "—";
// a generator's power tile is the INPUT power; Compare never ranks two conventions together.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

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
