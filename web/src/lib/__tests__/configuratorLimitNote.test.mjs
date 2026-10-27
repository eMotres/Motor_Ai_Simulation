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

// ── the bearings / hand-set maximum in the note (owner 2026-10-28) ──
const BRG = { set: true, rpm: 6300, lubrication: 'grease', cards: ['61814-2RZ'] };

t('the bearing basis names the cards, the lubrication and the maximum', () => {
  const n = L.speedLimitNote(tx, { basis: 'bearing', max: '6300', packV: '50', m: '0.89', bearing: BRG });
  assert.ok(n.text.startsWith('configureLimits.rpmBearing|') && n.text.includes('max=6300') && n.text.includes('cards=61814-2RZ'));
  assert.ok(n.text.includes('lube=configureLimits.lube_grease') && n.tip.length > 0);
  const oil = L.speedLimitNote(tx, { basis: 'bearing', max: '9000', packV: '50', m: '0.89', bearing: { ...BRG, lubrication: 'oil_air', cards: ['A', 'B'] } });
  assert.ok(oil.text.includes('lube=configureLimits.lube_oil_air') && oil.text.includes('cards=A + B'));
});

t('the hand-set basis is named; the pack basis ends with "bearings not set" / customer\'s, never both', () => {
  assert.ok(L.speedLimitNote(tx, { basis: 'hand', max: '5000', packV: '50', m: '0.89' }).text.startsWith('configureLimits.rpmHand|'));
  const pack = L.speedLimitNote(tx, { basis: 'envelope', max: '9000', packV: '50', m: '0.89' });
  assert.ok(!pack.text.includes('Bearings'));                                            // the server said nothing: no suffix
  const unset = L.speedLimitNote(tx, { basis: 'envelope', max: '9000', packV: '50', m: '0.89', bearing: { set: false, reason: 'none' } });
  assert.ok(unset.text.startsWith(pack.text) && unset.text.endsWith('configureLimits.rpmBearingsUnset|'));
  assert.ok(unset.tip.includes('rpmBearingsUnsetTip'));
  const kit = L.speedLimitNote(tx, { basis: 'envelope', max: '9000', packV: '50', m: '0.89', bearing: { set: false, customer_owned: true } });
  assert.ok(kit.text.endsWith('configureLimits.rpmBearingsCustomer|') && !kit.text.includes('Unset'));
  // when the bearings are the limit, the note is theirs alone
  assert.ok(!L.speedLimitNote(tx, { basis: 'bearing', max: '1', packV: '1', m: '1', bearing: BRG }).text.includes('Unset'));
});
