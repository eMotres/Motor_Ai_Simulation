// node --test - THE MASSES OF THE LOADED BUILD ARE THE SERVER'S (owner 2026-10-19).  40 L20 showed MASS 0.211 / ACTIVE 0.181 kg
// against the server's 0.1810 / 0.1506: the page scaled from the passport's stored mass0_kg (= the FULL mass on the 40 mm cards)
// and added the non-active parts again.  With the loaded preset's server masses as the anchor, the tiles equal them at that
// build and only scale from them when the user moves the build.  Also: a preset load never reads "changed" because of an older
// propeller pick stored under the TARGET configuration's key (signed-in accounts keep picks per configuration).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

let M = null, PR = null;
try { M = await import('../motorScaling.ts'); PR = await import('../configuratorPresets.ts'); } catch { /* old Node */ }
const t = M && PR ? test : test.skip;
const panel = readFileSync(new URL('../../components/compare/ConfiguratorPanel.tsx', import.meta.url), 'utf8');

// the 40 mm card as served before the fix: a FULL mass stored as the passport's active base
const P = {
  L0_mm: 20, wireH0_mm: 0.6, I0_A: 40, rpm0: 13000, nP0: 1, N0: 7, T0_Nm: 0.9, Vemf0_peak_V: 10, R0_ohm: 0.01,
  endWindFrac: 0.3, Pfe0_W: 10, Pmag0_W: 1, mass0_kg: 0.1807, mass_cu0_kg: 0.033, mass_partial: false, mass_fixed_kg: 0.0304,
};
const knobs = (L, N = 7, H = 0.6) => ({ N, L_mm: L, wireH_mm: H, nP: 1, I_A: 40, rpm: 13000 });
const preset = (config, L, mass) => ({ config, die: 'CIANO14 40 new', knobs: { L_mm: L, N: 7, wireH_mm: 0.6, split: null, nP: 1,
  I_A: 40, rpm: 13000 }, battery: null, pwm_variants: [], drive_variant: null, mass });
const SERVER = {           // modules.motor_mass.mass_summary of the public configurations
  'CIANO14 40 new|L12': { full_kg: 0.1233, active_kg: 0.0945 }, 'CIANO14 40 new|L20': { full_kg: 0.1810, active_kg: 0.1506 },
  'CIANO14 50 edited|L15': { full_kg: 0.2255, active_kg: 0.1770 }, 'CIANO28 85 20SW1200|L15': { full_kg: 0.4422, active_kg: 0.4262 },
};
const anchored = (p, pr) => ({ ...p, mass_anchor: PR.presetMassAnchor(knobs(p.L0_mm), pr) });

t('at the loaded build the tiles ARE the server masses, whatever the passport stored', () => {
  for (const [key, m] of Object.entries(SERVER)) {
    for (const L of [12, 15, 20]) {
      const pr = preset(key.split('|')[1], L, { ...m, partial: false });
      const r = M.scaleMotor(anchored(P, pr), knobs(L), 14);
      assert.ok(Math.abs(r.mass_kg - m.full_kg) < 1e-12, `${key} @${L}: ${r.mass_kg}`);
      assert.ok(Math.abs(r.mass_active_kg - m.active_kg) < 1e-12, `${key} @${L}: ${r.mass_active_kg}`);
      assert.equal(r.mass_partial, false);
      assert.ok(Math.abs(r.torque_per_mass - r.T_Nm / m.full_kg) < 1e-9);
      assert.ok(Math.abs(r.power_per_mass_W_kg - r.P_mech_W / m.full_kg) < 1e-6);
    }
  }
});

t('moving the build scales from the anchor (40 L12 12 -> 13 mm: about 0.13 / 0.10 kg, not 0.161 / 0.132)', () => {
  const pr = preset('L12', 12, { ...SERVER['CIANO14 40 new|L12'], partial: false });
  const r = M.scaleMotor(anchored(P, pr), knobs(13), 14);
  assert.ok(r.mass_active_kg > 0.0945 && r.mass_active_kg < 0.103, String(r.mass_active_kg));
  assert.ok(Math.abs(r.mass_kg - r.mass_active_kg - (0.1233 - 0.0945)) < 1e-12);          // the non-active parts once
  assert.ok(r.mass_kg > 0.123 && r.mass_kg < 0.132, String(r.mass_kg));
});

t('an incomplete full mass shows the active mass, flagged; no anchor keeps the old passport path', () => {
  const pr = preset('L15', 15, { full_kg: 0.4262, active_kg: 0.4262, partial: true, partial_reason: 'x' });
  const r = M.scaleMotor(anchored(P, pr), knobs(15), 14);
  assert.equal(r.mass_partial, true);
  assert.ok(Math.abs(r.mass_kg - 0.4262) < 1e-12 && r.mass_kg === r.mass_active_kg);
  assert.equal(PR.presetMassAnchor(knobs(20), preset('L20', 20, null)), null);
  assert.equal(PR.presetMassAnchor(knobs(20), null), null);
  const legacy = M.scaleMotor(P, knobs(20), 14);
  assert.ok(Math.abs(legacy.mass_active_kg - 0.1807) < 1e-12);
});

t('the panel anchors the result and the base on the loaded preset, and a preset load clears the target key\'s propeller', () => {
  assert.match(panel, /const massAnchor = useMemo\(\(\) => presetMassAnchor\(baseKnobs\(p\), massPreset\)/);
  assert.match(panel, /const result {2}= useMemo\(\(\) => scaleMotor\(pMass, scaleKnobs, ref\.poles\)/);
  assert.match(panel, /const baseRes = useMemo\(\(\) => scaleMotor\(pMass, refKnobs \?\? baseKnobs\(p\), ref\.poles\)/);
  assert.match(panel, /const targetKey = coolChoiceKey\(identity, refId, pr\.config\);/);
  assert.match(panel, /writeCoolChoice\(readLs\(PROP_CHOICE_LS\), targetKey, \{ propId: null \}\)/);
});
