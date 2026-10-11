// node --test — the upper limit is always in the speed / propeller-current label line.
import test from 'node:test';
import assert from 'node:assert/strict';

let L = null;
try { L = await import('../configuratorLimits.ts'); } catch { /* old Node */ }
const t = L ? test : test.skip;

const tx = (k, v = {}) => `${k}|${Object.entries(v).map(([a, b]) => `${a}=${b}`).join(',')}`;

t('speed names its maximum for every basis', () => {
  for (const basis of ['envelope', 'kv', 'no_battery']) {
    const n = L.speedLimitNote(tx, { basis, max: '12950', packV: '50.4', m: '0.89' });
    assert.ok(n.text.includes('max=12950'), basis);
    assert.ok(n.tip.length > 0);
  }
});

t('speed note follows the live max', () => {
  const a = L.speedLimitNote(tx, { basis: 'envelope', max: '9000', packV: '50', m: '0.89' });
  const b = L.speedLimitNote(tx, { basis: 'envelope', max: '8000', packV: '50', m: '0.89' });
  assert.notEqual(a.text, b.text);
});

t('propeller current shows the max and keeps the basis tip', () => {
  const n = L.propellerCurrentNote(tx, { max: '41', basisTip: 'BASIS' });
  assert.ok(n.text.startsWith('configurePropeller.currentFromPropMax'));
  assert.ok(n.text.includes('max=41'));
  assert.ok(n.tip.includes('currentFromPropTip') && n.tip.endsWith('BASIS'));
});
