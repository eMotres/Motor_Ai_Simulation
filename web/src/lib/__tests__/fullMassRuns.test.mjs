// node --test - THE MASS A USER SEES IS THE FULL MOTOR MASS (owner 2026-10-20: "the full mass everywhere").
// The Simulation tab's mass tile, its per-mass tiles and the Compare table print the server's full mass of the loaded
// configuration (mass_full_kg = the run's active mass + the non-active parts, stamped by the API) and the per-mass figures over
// it.  A run with no CAD build prints its ACTIVE mass labelled "Active mass (no CAD build)"; a run stored before the stamp
// existed prints "Active mass"; never an unlabelled "Mass".  The browser only chooses between what the server sent.
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
          if (/^\.{1,2}\//.test(specifier) && !/\.[cm]?[jt]sx?$/.test(specifier)) return nextResolve(`${specifier}.ts`, context);
          throw err;
        }
      },
    });
  }
} catch { /* no hooks: the imports below will just skip */ }

let E = null, F = null;
try { E = await import('../emResultTiles.ts'); F = await import('../fullMass.ts'); } catch { /* old Node */ }
const t = E && F ? test : test.skip;

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const read = (f) => readFileSync(join(SRC, f), 'utf8');
const loc = (l) => JSON.parse(read(`locales/${l}/controller.json`));
const one = (rows, id) => rows.flat().find((x) => x.id === id);

// a run of a geometry whose ACTIVE mass is 0.165 kg; the loaded configuration adds 0.0304 kg of non-active parts
const RUN = {
  T_em_avg_Nm: 0.42, T_ripple_pct: 3.2, P_mech_W: 132, P_loss_total_W: 18, loss_density_W_kg: 109.1,
  mass_total_kg: 0.165, mass_active_kg: 0.14, torque_per_mass_Nm_kg: 2.545, power_per_mass_W_kg: 800,
  Km_Nm_sqrtW: 0.021, Km_per_mass_Nm_sqrtW_kg: 0.127, op_mode: 'motor', rpm: 3000,
};
const FULL = {
  ...RUN, mass_partial: false, mass_basis: 'CIANO14 40 new / L20', mass_scope: 'full motor', mass_non_active_kg: 0.0304,
  mass_full_kg: 0.1954, torque_per_mass_full_Nm_kg: 2.149, power_per_mass_full_W_kg: 675.5, loss_density_full_W_kg: 92.1,
  Km_per_mass_full_Nm_sqrtW_kg: 0.1075,
};
const NO_CAD = { ...RUN, mass_partial: true, mass_partial_reason: 'no CAD bill of materials', mass_basis: 'X / Y' };

t('with the server full mass the mass tile IS the full mass and says "Mass"; the active mass rides in the tooltip', () => {
  const rows = E.emTileRows(FULL);
  const m = one(rows, 'mass');
  assert.equal(m.labelKey, 'configure.mass');
  assert.equal(m.value, 0.1954);
  assert.equal(m.tipKey, 'emTiles.massFullTip');
  assert.equal(m.tipVars.active, '0.17');            // the run's active mass (2 decimals), kept visible secondary
  assert.equal(m.tipVars.basis, 'CIANO14 40 new / L20');
});

t('the per-mass tiles divide by the full mass (the server\'s figures), consistently with Configure', () => {
  const rows = E.emTileRows(FULL);
  assert.equal(one(rows, 'tPerMass').value, 2.149);
  assert.equal(one(rows, 'tPerMass').labelKey, 'configure.tPerMass');
  assert.ok(Math.abs(one(rows, 'pPerMass').value - 0.6755) < 1e-9);
  assert.equal(one(rows, 'pPerMass').labelKey, 'configure.pPerMass');
  assert.equal(one(rows, 'lossDensity').value, 92.1);
});

t('no CAD build: the ACTIVE mass, labelled "Active mass (no CAD build)", per-mass figures labelled active', () => {
  const rows = E.emTileRows(NO_CAD);
  const m = one(rows, 'mass');
  assert.equal(m.labelKey, 'configure.massNoCad');
  assert.equal(m.value, 0.165);
  assert.equal(m.tipKey, 'emTiles.massNoCadTip');
  assert.equal(m.tipVars.reason, 'no CAD bill of materials');
  assert.equal(one(rows, 'tPerMass').labelKey, 'configure.tPerMassActive');
  assert.equal(one(rows, 'tPerMass').value, 2.545);
  assert.equal(one(rows, 'pPerMass').labelKey, 'configure.pPerMassActive');
  assert.equal(one(rows, 'lossDensity').labelKey, 'configure.lossDensityActive');
});

t('a run stored before the stamp existed: "Active mass", never an unlabelled "Mass"', () => {
  const m = one(E.emTileRows(RUN), 'mass');
  assert.equal(m.labelKey, 'configure.massActive');
  assert.equal(m.value, 0.165);
  assert.notEqual(m.labelKey, 'configure.mass');
});

t('a partial flag wins over a leftover full mass (a full mass of another configuration is never shown)', () => {
  const m = one(E.emTileRows({ ...FULL, mass_partial: true }), 'mass');
  assert.equal(m.labelKey, 'configure.massNoCad');
  assert.equal(m.value, 0.165);
});

t('Km / mass follows the same rule', () => {
  const k = (s) => E.emTileRows({ ...s, coupling: undefined }).flat().find((x) => x.id === 'kmPerMass');
  const a = k(FULL);
  assert.equal(a.value, 0.1075); assert.equal(a.labelKey, "configure.kmPerMass");
  const b = k(NO_CAD);
  assert.equal(b.value, 0.127); assert.equal(b.labelKey, "configure.kmPerMassActive");
});

t('lib/fullMass: kind, shown mass and per-mass choose between what the server sent', () => {
  assert.equal(F.massKind(FULL), 'full');
  assert.equal(F.massKind(NO_CAD), 'active-no-cad');
  assert.equal(F.massKind(RUN), 'active');
  assert.equal(F.massKind(null), 'active');
  assert.deepEqual(F.shownMass(FULL), { kg: 0.1954, activeKg: 0.165, kind: 'full' });
  assert.deepEqual(F.shownMass(NO_CAD), { kg: 0.165, activeKg: 0.165, kind: 'active-no-cad' });
  assert.deepEqual(F.perMass(FULL, 'torque'), { value: 2.149, full: true });
  assert.deepEqual(F.perMass(NO_CAD, 'torque'), { value: 2.545, full: false });
  assert.deepEqual(F.perMass(FULL, 'km'), { value: 0.1075, full: true });
  // a full mass without its per-mass figure falls back to the active one, flagged
  assert.deepEqual(F.perMass({ ...FULL, torque_per_mass_full_Nm_kg: undefined }, 'torque'), { value: 2.545, full: false });
  // a zero or missing full mass is no full mass
  assert.equal(F.massKind({ ...FULL, mass_full_kg: 0 }), 'active');
});

t('every new label and tooltip key exists in EN and ZH', () => {
  for (const l of ['en', 'zh-CN']) {
    const d = loc(l);
    for (const k of ['massNoCad', 'tPerMassActive', 'pPerMassActive', 'lossDensityActive', 'kmPerMassActive'])
      assert.ok(typeof d.configure[k] === 'string' && d.configure[k].length > 0, `${l} configure.${k}`);
    for (const k of ['massFullTip', 'massNoCadTip', 'tPerMassFullTip', 'pPerMassFullTip', 'lossDensityFullTip', 'kmPerMassFullTip'])
      assert.ok(typeof d.emTiles[k] === 'string' && d.emTiles[k].length > 0, `${l} emTiles.${k}`);
  }
  const en = loc('en');
  assert.equal(en.configure.massNoCad, 'Active mass (no CAD build)');
  assert.match(en.emTiles.massFullTip, /\{active\}/);
  assert.match(loc('zh-CN').emTiles.massFullTip, /\{active\}/);       // placeholders survive the translation
  // bare one-phrase tooltips (#359): at most 120 characters
  for (const l of ['en', 'zh-CN']) {
    for (const k of ['massFullTip', 'massNoCadTip', 'tPerMassFullTip', 'pPerMassFullTip', 'lossDensityFullTip', 'kmPerMassFullTip'])
      assert.ok(loc(l).emTiles[k].length <= 120, `${l} ${k}`);
  }
});

t('SummaryTable carries the full-mass fields and flux-scales the per-mass figures like the torque', () => {
  const st = read('components/simulation/SummaryTable.tsx');
  for (const k of ['mass_full_kg', 'mass_non_active_kg', 'mass_partial', 'torque_per_mass_full_Nm_kg', 'power_per_mass_full_W_kg',
    'Km_per_mass_full_Nm_sqrtW_kg']) assert.match(st, new RegExp(k));
  assert.match(st, /torque_per_mass_full_Nm_kg: summary\.torque_per_mass_full_Nm_kg != null/);
  assert.match(st, /power_per_mass_full_W_kg: summary\.power_per_mass_full_W_kg != null/);
  assert.match(st, /Km_per_mass_full_Nm_sqrtW_kg: summary\.Km_per_mass_full_Nm_sqrtW_kg != null/);
});

t('Compare: Mass (full where the row has it), Active mass kept as its own column, TD / PD on the full mass, "*" for active rows', () => {
  const cp = read('components/compare/ComparePanel.tsx');
  assert.match(cp, /key: 'mass_full',\s+label: 'Mass'/);
  assert.match(cp, /key: 'mass_total_kg',\s+label: 'Active mass'/);
  assert.match(cp, /perMass\(r, 'torque'\)/);
  assert.match(cp, /perMass\(r, 'power'\)/);
  assert.match(cp, /shownMass\(r\)/);
  assert.match(cp, /ACTIVE_BASIS_TIP/);
  assert.match(cp, /Active mass \(no CAD build\)/);
  // the old PD divisor (the active mass) survives only as the fallback of a row with no per-mass figure
  assert.doesNotMatch(cp, /derive: r => Number\(r\.T_em_avg_Nm\) \* 2 \* Math\.PI \* Number\(r\.rpm\) \/ 60 \/ Number\(r\.mass_total_kg\) \}/);
});

t('the optimizer / sweep tables name their mass as the ACTIVE mass', () => {
  for (const f of ['components/sweep/AutoPointsTable.tsx', 'components/sweep/SweepStudyPanel.tsx'])
    assert.match(read(f), /label: 'active mass \(kg\)'/);
  assert.match(read('components/sweep/DescentPanel.tsx'), />Active mass</);
});
