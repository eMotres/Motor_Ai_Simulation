import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

let M = null;
try { M = await import('../referenceOnlyPassport.ts'); } catch { /* old Node */ }
const t = M ? test : test.skip;

const v1 = [{ id: 'gan_48k', device: 'example', carrier_hz: 48000, points: { p: {} } }];

t('reference-only is explicit; s1=false alone does not suppress qualified cards', () => {
  assert.equal(M.isReferenceOnlyCard({ reference_only: true }), true);
  assert.equal(M.isReferenceOnlyCard({ passport: { reference_provenance: { qualification_claim: false } } }), true);
  assert.equal(M.isReferenceOnlyCard({ passport: { passport: { qualification_claim: false } } }), true);
  assert.equal(M.isReferenceOnlyCard({ s1_claim: false, passport: { passport: { s1_claim: false } } }), false);
  assert.equal(M.isReferenceOnlyCard(null), false);
});

t('reference-only cards suppress variants even when server or preset supplies them', () => {
  assert.deepEqual(M.selectConfigureVariants(true, v1, v1), []);
  assert.deepEqual(M.selectConfigureVariants(true, null, v1), []);
});

t('qualified cards preserve preset-first and passport-fallback behavior', () => {
  const preset = [{ ...v1[0], id: 'preset-variant' }];
  assert.deepEqual(M.selectConfigureVariants(false, preset, v1), preset);
  assert.deepEqual(M.selectConfigureVariants(false, [], v1), v1);
  assert.deepEqual(M.selectConfigureVariants(false, null, null), []);
});

t('reference-only notice exists in both shipped Configure locales', () => {
  const en = JSON.parse(readFileSync(new URL('../../locales/en/controller.json', import.meta.url), 'utf8'));
  const zh = JSON.parse(readFileSync(new URL('../../locales/zh-CN/controller.json', import.meta.url), 'utf8'));
  assert.equal(en.configure.referenceOnlyNotice, 'Reference only · not a continuous rating.');
  assert.equal(zh.configure.referenceOnlyNotice, '仅供参考，不代表连续额定值。');
});
