// node --test - Configure presets = configuration x mode (owner 2026-10-13).
// Imports lib/configuratorModes.ts + lib/configuratorPresets.ts (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let M = null; let P = null;
try { M = await import('../configuratorModes.ts'); P = await import('../configuratorPresets.ts'); } catch { /* old Node */ }
const t = M ? test : test.skip;

const pt = (name, I, rpm, gamma) => ({ name, current_A: I, rpm, gamma_deg: gamma ?? null, mode: 'motor' });
const mk = (config, L, N, h, points) => ({
  config, die: 'D', duty: 'rated',
  knobs: { L_mm: L, N, wireH_mm: h, split: 1, nP: 1, I_A: 1, rpm: 1 },
  duty_points: points, battery: null, pwm_variants: [], drive_variant: null,
});
const L12 = mk('L12', 12, 7, 0.6, [pt('peak', 48.8, 14400, 20), pt('rated', 42.8, 13000, 10)]);
const L20 = mk('L20', 20, 8, 0.45, [pt('S1 prop', 30, 9000), pt('peak', 90, 25000), pt('rated', 80.6, 25000)]);

t('buttons are configuration x mode, rated then peak then the rest', () => {
  const b = M.buildModePresets([L12, L20]);
  assert.deepEqual(b.map((x) => x.label), ['L12 · rated', 'L12 · peak', 'L20 · rated', 'L20 · peak', 'L20 · S1 prop']);
  assert.deepEqual(b.map((x) => x.key), ['L12|rated', 'L12|peak', 'L20|rated', 'L20|peak', 'L20|S1 prop']);
});

t('a button carries the configuration build AND the mode point', () => {
  const peak = M.buildModePresets([L12]).find((x) => x.duty === 'peak');
  assert.equal(peak.knobs.L_mm, 12); assert.equal(peak.knobs.N, 7);
  assert.equal(peak.knobs.I_A, 48.8); assert.equal(peak.knobs.rpm, 14400);
  assert.equal(peak.gamma_deg, 20);
  const k = P.presetKnobs({ N: 1, L_mm: 1, wireH_mm: 1, nP: 1, I_A: 1, rpm: 1, split: 1 }, peak);
  assert.equal(k.I_A, 48.8); assert.equal(k.rpm, 14400); assert.equal(k.L_mm, 12);
});

t('only modes with data are offered: no usable point = no button', () => {
  const c = mk('X', 10, 5, 0.5, [pt('rated', 10, 1000), pt('peak', null, 2000), pt('gen', 5, 0), { name: '', current_A: 1, rpm: 1 }]);
  assert.deepEqual(M.buildModePresets([c]).map((x) => x.label), ['X · rated']);
});

t('a configuration with no saved mode keeps one button, named by the configuration', () => {
  const c = mk('Solo', 10, 5, 0.5, []);
  const b = M.buildModePresets([c]);
  assert.equal(b.length, 1); assert.equal(b[0].label, 'Solo');
});

t('the state IS a button only while the build and the point both match', () => {
  const bs = M.buildModePresets([L12, L20]);
  const k = { N: 7, L_mm: 12, wireH_mm: 0.6, nP: 1, split: 1, I_A: 42.8, rpm: 13000, drive: 'sine' };
  const match = (kk) => bs.find((b) => P.presetDiff(b, kk, null, null).length === 0)?.key ?? null;
  assert.equal(match(k), 'L12|rated');
  assert.equal(match({ ...k, I_A: 48.8, rpm: 14400 }), 'L12|peak');
  assert.equal(match({ ...k, I_A: 50 }), null);             // a knob moved: "changed from L12 . rated"
});

t('the "changed from" label names the mode button the state started from', () => {
  const bs = M.buildModePresets([L12, L20]);
  assert.equal(M.baseModeLabel(bs, 'L12', 'rated'), 'L12 · rated');
  assert.equal(M.baseModeLabel(bs, 'L20', null), 'L20 · rated');
  assert.equal(M.baseModeLabel(bs, null, null), null);
});

t('a loaded machine on a mode point is recognised as that mode', () => {
  const bs = M.buildModePresets([L12, L20]);
  assert.equal(M.modeOfPoint(bs, 'L12', 48.8, 14400), 'peak');
  assert.equal(M.modeOfPoint(bs, 'L12', 42.8, 13000), 'rated');
  assert.equal(M.modeOfPoint(bs, 'L12', 1, 1), null);
  assert.equal(M.modeOfPoint(bs, 'L20', 48.8, 14400), null);
});

t('rated is never short-time and a rated variant is never a button (owner 2026-10-19)', () => {
  const d85 = mk('L15', 15, 18, 0.3, [
    { ...pt('rated_short_time', 25.88, 1000, 2), duty_class: 'short_time' },
    { ...pt('peak', 45.962, 1000, 2), duty_class: 'short_time' },
    { ...pt('rated', 6.3, 1000, 0), duty_class: 'short_time' },   // even if a stored record says so
  ]);
  const b = M.buildModePresets([d85]);
  assert.deepEqual(b.map((x) => x.label), ['L15 · rated', 'L15 · peak']);
  assert.deepEqual(b.map((x) => x.short_time), [false, true]);         // said in the tooltip, never on the button
  assert.equal(b[0].knobs.I_A, 6.3);                               // the default load lands on rated
  assert.equal(M.isPresetMode('Rated short time'), false);
  assert.equal(M.isPresetMode('rated'), true);
  assert.equal(M.isPresetMode('peak'), true);
});

t('the buttons show only the modes of the loaded configuration, by the mode alone (owner 2026-10-19)', () => {
  const bs = M.buildModePresets([L12, L20]);
  const txt = (cfg) => M.presetButtons(bs, cfg).map((x) => x.text);
  assert.deepEqual(txt('L12'), ['rated', 'peak']);                  // no 'L20 · rated' on the L12 motor
  assert.deepEqual(txt('L20'), ['rated', 'peak', 'S1 prop']);
  assert.ok(M.presetButtons(bs, 'L12').every((x) => x.preset.config === 'L12'));
  assert.ok(bs.every((x) => !/short/i.test(x.buttonLabel)));
  // nothing loaded yet / a configuration without buttons: every mode, named with its configuration
  assert.deepEqual(txt(null), ['L12 · rated', 'L12 · peak', 'L20 · rated', 'L20 · peak', 'L20 · S1 prop']);
  assert.deepEqual(txt('L99'), txt(null));
});
