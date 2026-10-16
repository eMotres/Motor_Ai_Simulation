// node --test — Configure presets (owner 2026-10-05): one per configuration of the die,
// restoring every knob + pack + drive, and "modified from" as soon as anything differs.
// Imports lib/configuratorPresets.ts itself (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let P = null;
try { P = await import('../configuratorPresets.ts'); } catch { /* old Node */ }
const t = P ? test : test.skip;

const mk = (config, L, N, h, nP, I, rpm, cells, variant) => ({
  config, die: 'D', duty: 'rated',
  knobs: { L_mm: L, N, wireH_mm: h, split: 1, nP, I_A: I, rpm },
  battery: cells ? { cells, v_nom: cells * 3.7 } : null,
  pwm_variants: variant ? [{ id: variant }] : [], drive_variant: variant ?? null,
});
const L12 = mk('L12', 12, 7, 0.6, 1, 42.78, 13000, 6, 'si_48k');
const L20 = mk('L20', 20, 8, 0.45, 2, 80.61, 25000, 12, null);
const BAT = (cells) => ({ cells, nom: 3.7, max: 4.2, min: 3.0 });

const user = { N: 5, L_mm: 30, wireH_mm: 0.9, nP: 1, I_A: 10, rpm: 2000, split: 1, drive: 'sine' };

t('choosing a preset restores EVERY knob and its drive', () => {
  const k = P.presetKnobs(user, L12);
  assert.deepEqual(k, { N: 7, L_mm: 12, wireH_mm: 0.6, nP: 1, I_A: 42.78, rpm: 13000, split: 1,
                        drive: 'pwm', drive_variant: 'si_48k' });
  const k2 = P.presetKnobs({ ...user, drive: 'pwm', drive_variant: 'x' }, L20);   // no variants: Sine
  assert.equal(k2.drive, 'sine'); assert.ok(!('drive_variant' in k2));
  assert.equal(k2.nP, 2); assert.equal(k2.L_mm, 20);
});

t('a knob the configuration does not state stays where it is', () => {
  const pr = { ...L12, knobs: { ...L12.knobs, I_A: null, rpm: null } };
  const k = P.presetKnobs(user, pr);
  assert.equal(k.I_A, 10); assert.equal(k.rpm, 2000); assert.equal(k.L_mm, 12);
});

t('exactly the preset -> no difference; anything changed -> named', () => {
  const k = P.presetKnobs(user, L12);
  assert.deepEqual(P.presetDiff(L12, k, BAT(6), BAT(6)), []);
  assert.deepEqual(P.presetDiff(L12, { ...k, rpm: 9000 }, BAT(6), BAT(6)), ['speed']);
  assert.deepEqual(P.presetDiff(L12, { ...k, I_A: 30 }, BAT(6), BAT(6)), ['current']);
  assert.deepEqual(P.presetDiff(L12, { ...k, N: 8 }, BAT(6), BAT(6)), ['build']);
  assert.deepEqual(P.presetDiff(L12, k, BAT(7), BAT(6)), ['battery']);
  assert.deepEqual(P.presetDiff(L12, { ...k, drive: 'sine' }, BAT(6), BAT(6)), ['drive']);
  assert.deepEqual(P.presetDiff(L12, { ...k, drive_variant: 'other' }, BAT(6), BAT(6)), ['drive']);
  assert.deepEqual(P.presetDiff(L12, { ...k, L_mm: 15, rpm: 1 }, BAT(9), BAT(6)).sort(), ['battery', 'build', 'speed']);
  // a preset with no pack of its own cannot differ in battery
  assert.deepEqual(P.presetDiff(L20, P.presetKnobs(user, L20), BAT(99), null), []);
});

t('the base preset of a loaded machine is the configuration with its build', () => {
  const list = [L12, L20];
  assert.equal(P.presetOfBuild(list, { N: 7, L_mm: 12, wireH_mm: 0.6, nP: 1 }).config, 'L12');
  assert.equal(P.presetOfBuild(list, { N: 8, L_mm: 20, wireH_mm: 0.45, nP: 2 }).config, 'L20');
  assert.equal(P.presetOfBuild(list, { N: 7, L_mm: 13, wireH_mm: 0.6, nP: 1 }), null);
  assert.equal(P.presetOfBuild([], { N: 7, L_mm: 12, wireH_mm: 0.6, nP: 1 }), null);
});

t('any number of configurations: 1, 3 or 5 presets are all handled the same way', () => {
  for (const n of [1, 3, 5]) {
    const list = Array.from({ length: n }, (_, i) => mk(`C${i}`, 10 + 5 * i, 6 + i, 0.5, 1, 20 + i, 1000 * (i + 1), 6, null));
    assert.equal(list.length, n);
    list.forEach((pr, i) => {
      assert.equal(P.presetOfBuild(list, { N: 6 + i, L_mm: 10 + 5 * i, wireH_mm: 0.5, nP: 1 }).config, `C${i}`);
      assert.deepEqual(P.presetDiff(pr, P.presetKnobs(user, pr), BAT(6), BAT(6)), []);
    });
  }
});
