// node --test - Configure reload = one consistent state (owner 2026-10-15).
// Imports lib/configureRestore.ts (+ the preset libraries; Node >= 22.18 / 24, else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let R = null; let P = null; let M = null;
try { R = await import('../configureRestore.ts'); P = await import('../configuratorPresets.ts'); M = await import('../configuratorModes.ts'); } catch { /* old Node */ }
const t = R ? test : test.skip;

const pt = (name, I, rpm) => ({ name, current_A: I, rpm, gamma_deg: null, mode: 'motor' });
const mk = (config, L, points, cells) => ({
  config, die: 'CIANO14 40 new', duty: 'rated',
  knobs: { L_mm: L, N: 7, wireH_mm: 0.6, split: 1, nP: 2, I_A: 1, rpm: 1 },
  duty_points: points, battery: { cells, v_cell_nom: 3.7, v_cell_max: 4.2, v_cell_min: 3.0 },
  pwm_variants: [], drive_variant: null,
});
const L12 = mk('L12', 12, [pt('rated', 40.7, 13000), pt('peak', 48.8, 14400)], 6);
const L20 = mk('L20', 20, [pt('rated', 52.55, 13000)], 8);
const presets = [L12, L20];
const build = (L, I, rpm) => ({ N: 7, L_mm: L, wireH_mm: 0.6, nP: 2, I_A: I, rpm });

t('the base is the configuration whose BUILD the knobs hold - not the one guessed earlier', () => {
  // the L20 stack adopted from the loaded machine, current following a propeller
  const s = R.seedBase(presets, build(20, 24, 13000), { die: 'CIANO14 40 new', config: 'L20', duty: 'rated' });
  assert.equal(s.config, 'L20');
  assert.equal(s.mode, 'rated');            // from the loaded duty: the current is the propeller's
  // the same panel with the passport's L12 base point would have been L12
  assert.equal(R.seedBase(presets, build(12, 40.7, 13000)).config, 'L12');
});

t('the mode is the saved point the knobs sit on, else the loaded duty, else unknown', () => {
  assert.equal(R.seedBase(presets, build(12, 48.8, 14400)).mode, 'peak');
  assert.equal(R.seedBase(presets, build(12, 30, 13000), { die: 'CIANO14 40 new', config: 'L12', duty: 'peak' }).mode, 'peak');
  // a duty of ANOTHER configuration says nothing about this one
  assert.equal(R.seedBase(presets, build(12, 30, 13000), { die: 'CIANO14 40 new', config: 'L20', duty: 'rated' }).mode, null);
  assert.equal(R.seedBase(presets, build(12, 30, 13000)).mode, null);
});

t('a build that is no configuration has no base', () => {
  assert.equal(R.seedBase(presets, build(33, 10, 5000)), null);
  assert.equal(R.seedBase([], build(20, 10, 5000)), null);
});

t('remembered knobs are restored only for the machine they were stamped with', () => {
  const k = { N: 7, L_mm: 20, wireH_mm: 0.6, nP: 2, I_A: 24, rpm: 13000 };
  const raw = R.stampKnobs(k, 'cat_a');
  assert.deepEqual(R.readStampedKnobs(raw, 'cat_a'), k);
  assert.equal(R.readStampedKnobs(raw, 'cat_b'), null);
  assert.equal(R.readStampedKnobs(JSON.stringify(k), 'cat_a'), null);   // legacy, unstamped
  assert.equal(R.readStampedKnobs('not json', 'cat_a'), null);
  assert.equal(R.readStampedKnobs(null, 'cat_a'), null);
});

t('a restored state above the pack is a misfit: voltage and speed, each named', () => {
  // the owner's case: 29.7 V line on a 22.2 V pack, 13000 rpm over a 10183 rpm full-pack maximum
  assert.deepEqual(R.restoreMisfit({ lineVpeak: 29.7, packNomV: 22.2, rpm: 13000, speedMaxRpm: 10183 }), ['voltage', 'speed']);
  assert.deepEqual(R.restoreMisfit({ lineVpeak: 29.7, packNomV: 22.2, rpm: 9000, speedMaxRpm: 10183 }), ['voltage']);
  assert.deepEqual(R.restoreMisfit({ lineVpeak: 20, packNomV: 22.2, rpm: 13000, speedMaxRpm: 10183 }), ['speed']);
  assert.deepEqual(R.restoreMisfit({ lineVpeak: 20, packNomV: 22.2, rpm: 10183, speedMaxRpm: 10183 }), []);
});

t('no pack to judge by = nothing to refuse', () => {
  assert.deepEqual(R.restoreMisfit({ lineVpeak: 99, packNomV: null, rpm: 99999, speedMaxRpm: null }), []);
});

t('with the propeller as the load the preset matches whatever current the propeller draws', () => {
  const b = R.seedBase(presets, build(20, 24, 13000));
  assert.ok(b);
  const l20 = { ...L20, knobs: { ...L20.knobs, I_A: 52.55, rpm: 13000 } };
  const k = { ...build(20, 24, 13000), split: 1 };
  assert.deepEqual(P.presetDiff(l20, k, null, null), ['current']);
  assert.deepEqual(P.presetDiff(l20, k, null, null, undefined, { ignoreCurrent: true }), []);
});

t('the loaded selection names its mode button: its duty, else the first mode of the configuration', () => {
  const mps = M.buildModePresets(presets);
  const sel = { die: 'CIANO14 40 new', config: 'L12', duty: 'peak' };
  assert.equal(R.selectionPreset(mps, sel).key, 'L12|peak');
  assert.equal(R.selectionPreset(mps, { ...sel, duty: null }).key, 'L12|rated');
  assert.equal(R.selectionPreset(mps, { ...sel, config: 'L20', duty: 'peak' }).key, 'L20|rated');
  assert.equal(R.selectionPreset(mps, { ...sel, config: 'L99' }), null);
  assert.equal(R.selectionPreset(mps, { ...sel, die: 'OTHER' }), null);
});

t('reload on L20 rated: knobs or reference knobs on the L12 build -> restore the L20 preset', () => {
  const mps = M.buildModePresets(presets);
  const sel = { die: 'CIANO14 40 new', config: 'L20', duty: 'rated' };
  // the owner state: L20 stack in the sliders, the reference still the L12 point
  assert.equal(R.reconcilePlan(mps, sel, build(20, 24, 13000), build(12, 40.7, 13000)).action, 'apply');
  // the reference not adopted yet
  assert.equal(R.reconcilePlan(mps, sel, build(20, 24, 13000), null).action, 'apply');
  // both on the loaded build: keep them, only name the base from the selection
  const keep = R.reconcilePlan(mps, sel, build(20, 24, 13000), build(20, 52.55, 13000));
  assert.equal(keep.action, 'keep'); assert.equal(keep.preset.key, 'L20|rated');
  assert.equal(R.reconcilePlan(mps, { ...sel, config: 'L99' }, build(20, 1, 1), build(20, 1, 1)), null);
});
