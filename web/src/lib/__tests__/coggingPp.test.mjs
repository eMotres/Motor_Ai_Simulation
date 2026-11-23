import test from 'node:test';
import assert from 'node:assert/strict';
const hooks = await import('node:module');
if (typeof hooks.registerHooks === 'function') {
  hooks.registerHooks({
    resolve(specifier, context, nextResolve) {
      try { return nextResolve(specifier, context); } catch (err) {
        if (/^\.{1,2}\//.test(specifier) && !/\.[cm]?[jt]sx?$/.test(specifier)) return nextResolve(`${specifier}.ts`, context);
        throw err;
      }
    },
  });
}
const { coggingOf, coggingText, coggingSettingsKey, coggingMetrics, coggingTip } = await import('../coggingPp.ts');

const pt = (o = {}) => ({ cogging_pp_Nm: 0.3, cogging_final: false, cogging_steps_per_period: 48, cogging_magnet_temp_c: 20,
  cogging_basis: 'no_load_full_strength', cogging_demag_at_load: true, ...o });
const tr = (k) => k;

test('a sub-final value is labelled ranking only, a missing one is a dash with its reason', () => {
  assert.match(coggingText(coggingOf(pt()), 'ranking only'), /ranking only/);
  assert.equal(coggingText(coggingOf(pt({ cogging_final: true })), 'x'), '0.3000');
  const m = coggingOf(pt({ cogging_pp_Nm: null, cogging_error: 'why' }));
  assert.equal(coggingText(m, 'x'), '-');
  assert.equal(coggingTip(m, tr), 'why');
});

test('the basis is never silent when the loaded points have demag on', () => {
  assert.match(coggingTip(coggingOf(pt({ cogging_final: true })), tr), /coggingPpBasis/);
  assert.equal(coggingTip(coggingOf(pt({ cogging_final: true, cogging_demag_at_load: false })), tr), undefined);
});

test('values with a different basis or settings never share a comparison key', () => {
  const a = coggingSettingsKey(coggingOf(pt()));
  assert.notEqual(a, coggingSettingsKey(coggingOf(pt({ cogging_demag_at_load: false }))));
  assert.notEqual(a, coggingSettingsKey(coggingOf(pt({ cogging_basis: 'other' }))));
  assert.notEqual(a, coggingSettingsKey(coggingOf(pt({ cogging_steps_per_period: 96 }))));
  assert.equal(a, coggingSettingsKey(coggingOf(pt())));
});

test('the applied metrics carry the basis', () => {
  const m = coggingMetrics(coggingOf(pt()));
  assert.equal(m.cogging_basis_code, 1);
  assert.equal(m.cogging_demag_at_load, 1);
});
