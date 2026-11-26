// The optional misalignment inputs f_ma_um / sun_planet_f_ma_um / planet_ring_f_ma_um (core PR #930): absent stays absent, 0 is "supplied",
// they ride through the template body, the saved record round trip and the editor fields.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { defaultStage, humanoidJoint, toPlainBody, F_MA_KEYS } from '../plain.ts';

const src = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');

test('absent f_ma is never sent: the default body is exactly what it was before', () => {
  const body = toPlainBody(humanoidJoint(), 'point');
  for (const s of body.stages) for (const k of ['f_ma_um', 'sun_planet_f_ma_um', 'planet_ring_f_ma_um']) assert.equal(k in s, false);
});

test('a supplied value is sent per stage kind; 0 is a supplied value; a negative or non-finite value is not sent', () => {
  const state = humanoidJoint();
  state.stages[0] = { ...state.stages[0], sun_planet_f_ma_um: 1, planet_ring_f_ma_um: 0, f_ma_um: 3 };   // f_ma_um is not a planetary key
  state.stages[1] = { ...state.stages[1], sun_planet_f_ma_um: -2, planet_ring_f_ma_um: Number.NaN };
  const pair = { ...defaultStage('pair', 'p'), f_ma_um: 2.5, sun_planet_f_ma_um: 9 };
  state.stages.push(pair);
  const body = toPlainBody(state, 'point');
  assert.deepEqual([body.stages[0].sun_planet_f_ma_um, body.stages[0].planet_ring_f_ma_um, 'f_ma_um' in body.stages[0]], [1, 0, false]);
  assert.deepEqual(['sun_planet_f_ma_um' in body.stages[1], 'planet_ring_f_ma_um' in body.stages[1]], [false, false]);
  assert.deepEqual([body.stages[2].f_ma_um, 'sun_planet_f_ma_um' in body.stages[2]], [2.5, false]);
  assert.deepEqual(F_MA_KEYS.pair, ['f_ma_um']);
  assert.deepEqual(F_MA_KEYS.compound_planetary, ['sun_planet_f_ma_um', 'planet_ring_f_ma_um']);
});

test('the editor shows the fields beside the stage header, optional (emptying clears), EN + zh-CN labels and tooltips', () => {
  const ed = src('../components/StageEditor.tsx');
  assert.match(ed, /F_MA_KEYS\[s\.kind\]\.map/);
  assert.match(ed, /onClear=\{\(\) => setStage\(i, \{ \[k\]: undefined \}\)\}/);
  const parts = src('../components/parts.tsx');
  assert.match(parts, /onClear\?: \(\) => void/);
  for (const lang of ['en', 'zh-CN']) {
    const loc = JSON.parse(readFileSync(new URL(`../../../locales/${lang}/gears.json`, import.meta.url), 'utf8'));
    for (const k of ['f_ma_um', 'sun_planet_f_ma_um', 'planet_ring_f_ma_um']) { assert.ok(loc.l[k], `${lang} l.${k}`); assert.ok(loc.tip[k], `${lang} tip.${k}`); }
  }
});

test('a saved record brings the values back into the editor (absent stays absent)', () => {
  const saved = src('../saved.ts');
  assert.match(saved, /for \(const k of \['f_ma_um', 'sun_planet_f_ma_um', 'planet_ring_f_ma_um'\] as const\)/);
});
