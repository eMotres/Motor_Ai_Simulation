// node --test — the default propeller per configuration (owner 2026-10-05: L12 -> FPV 10x5, L20 -> P13x4.4).
//
// The picker takes the default of the configuration on loading it and on "reset to preset"; the preset's
// "modified" check includes the propeller; a saved configuration keeps the user's own choice; with no
// default the first allowed propeller that has torque data is taken.  The yaml is the REAL
// config/cooling_options.yaml (the same file the backend serves).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

let P = null, R = null;
try {
  P = await import('../configuratorPropeller.ts');
  R = await import('../configuratorPresets.ts');
} catch { /* old Node */ }
const t = P ? test : test.skip;

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..');
const yaml = readFileSync(join(ROOT, 'config', 'cooling_options.yaml'), 'utf8');

const sel = (id, power = 'measured_torque', ok = true) => ({ id, vendor: 'T-Motor', model: id, selectable: ok, power_data: power });
const ALLOWED = [
  sel('tmotor_fpv_10x5'), sel('tmotor_cf10x3_3', 'estimated'), sel('tmotor_cf11x3_7', 'estimated'),
  sel('tmotor_p12x4'), sel('tmotor_p13x4_4'), sel('tmotor_fpv_13x10'), sel('tmotor_fpv_13x12'),
];
const COOLING = { restricted: true, cooling_options: ['propeller_air'], propellers: ALLOWED.map((x) => x.id),
  config: 'L12', default_propeller: 'tmotor_fpv_10x5', defaults: { L12: 'tmotor_fpv_10x5', L20: 'tmotor_p13x4_4' } };

t('the repo yaml states the owner\'s defaults', () => {
  assert.match(yaml, /defaults:\s*\n\s*L12: tmotor_fpv_10x5[^\n]*\n\s*L20: tmotor_p13x4_4/);
});

t('each configuration opens on its own default', () => {
  assert.equal(P.defaultPropellerFor(COOLING, 'L12', ALLOWED), 'tmotor_fpv_10x5');
  assert.equal(P.defaultPropellerFor(COOLING, 'L20', ALLOWED), 'tmotor_p13x4_4');
});

t('no default (or a default that is not allowed / has no data): the first allowed propeller with torque data', () => {
  assert.equal(P.defaultPropellerFor({ ...COOLING, defaults: {} }, 'L12', ALLOWED), 'tmotor_fpv_10x5');
  assert.equal(P.defaultPropellerFor(COOLING, 'L99', ALLOWED), 'tmotor_fpv_10x5');
  assert.equal(P.defaultPropellerFor(null, 'L12', ALLOWED), 'tmotor_fpv_10x5');
  assert.equal(P.defaultPropellerFor(COOLING, null, ALLOWED), 'tmotor_fpv_10x5');           // no config: the single default
  const noTorqueFirst = [sel('x', 'estimated'), sel('y'), sel('z')];
  assert.equal(P.defaultPropellerFor({ defaults: {} }, 'L12', noTorqueFirst), 'y');          // measured torque beats the list order
  assert.equal(P.defaultPropellerFor({ defaults: { L12: 'x' } }, 'L12', [sel('x', 'none', false), sel('q')]), 'q');   // geometry-only default: skipped
  assert.equal(P.defaultPropellerFor({ defaults: { L12: 'gone' } }, 'L12', ALLOWED), 'tmotor_fpv_10x5');
  assert.equal(P.defaultPropellerFor(COOLING, 'L12', []), null);
});

t('the picker shows the user\'s own pick, else the configuration\'s default', () => {
  assert.equal(P.effectivePropeller({}, ALLOWED, 'tmotor_p13x4_4'), 'tmotor_p13x4_4');
  assert.equal(P.effectivePropeller({ propId: 'tmotor_p12x4' }, ALLOWED, 'tmotor_p13x4_4'), 'tmotor_p12x4');
  assert.equal(P.effectivePropeller({ propId: null }, ALLOWED, 'tmotor_p13x4_4'), 'tmotor_p13x4_4');   // "reset to preset" drops the pick
  assert.equal(P.effectivePropeller({ propId: 'gone' }, ALLOWED, 'tmotor_p13x4_4'), 'tmotor_p13x4_4');
  assert.equal(P.effectivePropeller({}, ALLOWED, 'gone'), 'tmotor_fpv_10x5');                           // bad preferred: fallback
  assert.equal(P.effectivePropeller({}, ALLOWED), 'tmotor_fpv_10x5');
});

t('"reset to preset" drops the stored pick (null is not stored), so the default shows again', () => {
  let raw = P.writeCoolChoice(null, 'cat:m', { propId: 'tmotor_p12x4', ambient: 30 });
  assert.equal(P.readCoolChoice(raw, 'cat:m').propId, 'tmotor_p12x4');
  raw = P.writeCoolChoice(raw, 'cat:m', { propId: null });
  assert.deepEqual(P.readCoolChoice(raw, 'cat:m'), { ambient: 30 });                                    // ambient survives, the pick does not
});

// ── the preset "modified" check includes the propeller ───────────────────────────────────────
const KN = { N: 7, L_mm: 12, wireH_mm: 0.6, nP: 1, I_A: 45, rpm: 13000, split: 1 };
const PRESET = (config) => ({ config, die: 'CIANO14 40 new', duty: 'rated', battery: null, device: null,
  knobs: { L_mm: 12, N: 7, wireH_mm: 0.6, split: 1, nP: 1, I_A: 45, rpm: 13000 }, pwm_variants: [], drive_variant: null });

t('presetDiff: a different propeller than the preset opens on is a difference', () => {
  const pr = PRESET('L12');
  const k = R.presetKnobs(KN, pr);
  assert.deepEqual(R.presetDiff(pr, k, null, null, { current: 'tmotor_fpv_10x5', wanted: 'tmotor_fpv_10x5' }), []);
  assert.deepEqual(R.presetDiff(pr, k, null, null, { current: 'tmotor_p12x4', wanted: 'tmotor_fpv_10x5' }), ['propeller']);
  // not propeller-cooled: nothing is asked of the propeller (the argument is absent)
  assert.deepEqual(R.presetDiff(pr, k, null, null), []);
  assert.deepEqual(R.presetDiff(pr, k, null, null, { current: null, wanted: null }), []);
  // it reports next to the other differences
  assert.deepEqual(R.presetDiff(pr, { ...k, I_A: 50 }, null, null, { current: 'a', wanted: 'b' }), ['current', 'propeller']);
});

t('the preset buttons highlight the one whose BUILD and PROPELLER match (L12 vs L20 defaults differ)', () => {
  const defaults = COOLING.defaults;
  const hit = (cfgs, k, propId) => cfgs.find((pr) => R.presetDiff(pr, k, null, null,
    { current: propId, wanted: defaults[pr.config] }).length === 0)?.config ?? null;
  const L12 = PRESET('L12');
  const L20 = { ...PRESET('L20'), knobs: { ...PRESET('L20').knobs, L_mm: 20, I_A: 33 } };
  const k12 = R.presetKnobs(KN, L12), k20 = R.presetKnobs(KN, L20);
  assert.equal(hit([L12, L20], k12, 'tmotor_fpv_10x5'), 'L12');
  assert.equal(hit([L12, L20], k20, 'tmotor_p13x4_4'), 'L20');
  assert.equal(hit([L12, L20], k12, 'tmotor_p13x4_4'), null);        // L12's build with L20's propeller is neither preset
  assert.equal(hit([L12, L20], k20, 'tmotor_fpv_10x5'), null);       // and the other way round
  // same build, only the propeller differs: a preset whose default propeller is the picked one wins
  const L12b = { ...PRESET('L20'), knobs: PRESET('L12').knobs };      // a second configuration with L12's knobs and L20's default
  assert.equal(hit([L12, L12b], k12, 'tmotor_p13x4_4'), 'L20');
});

// ── the panel ────────────────────────────────────────────────────────────────────────────────
const panel = readFileSync(join(ROOT, 'web', 'src', 'components', 'compare', 'ConfiguratorPanel.tsx'), 'utf8');

t('the panel: default on load and on reset, "modified" includes the propeller, saved configurations keep it', () => {
  assert.match(panel, /effectivePropeller\(coolChoice, allowedProps, propDefaultFor\(baseConfig\)\)/);   // load: the base configuration's default
  assert.match(panel, /defaultPropellerFor\(currentContext\?\.cooling, config, allowedProps\)/); // context must belong to this catalog card
  assert.match(panel, /if \(cooled\) updateCool\(\{ propId: null \}\);/);                                  // applyPreset AND reset
  assert.equal((panel.match(/if \(cooled\) updateCool\(\{ propId: null \}\);/g) || []).length, 2);
  assert.match(panel, /propModified = cooled && propId !== effectivePropeller\(\{\}, allowedProps, propDefaultFor\(baseConfig\)\)/);
  assert.match(panel, /\|\| propModified/);
  assert.match(panel, /cooled \? \{ current: propId, wanted: propDefaultFor\(pr\.config\) \} : undefined/);
  assert.match(panel, /propeller: cooled && propSummary \? \{ id: propSummary\.id/);                       // saved with the user's choice
  assert.match(panel, /if \(cooled && c\.propeller\?\.id\) updateCool\(\{ propId: c\.propeller\.id \}\);/);   // and restored
});

for (const l of ['en', 'zh-CN']) {
  t(`the saved-configurations table has a propeller column (${l})`, () => {
    const c = JSON.parse(readFileSync(join(ROOT, 'web', 'src', 'locales', l, 'controller.json'), 'utf8'));
    assert.ok(c.configurePropeller.title);
    assert.match(panel, /label: tx\('configurePropeller\.title'\), get: \(c\) => c\.propeller\?\.label \?\? '—'/);
  });
}
