// node --test - the direct link /gears opens the Gears tab (not the last used tab).
import test from 'node:test';
import assert from 'node:assert/strict';

let G = null;
try { G = await import('../gearsRoute.ts'); } catch { /* old Node */ }
const t = G ? test : test.skip;

t('/gears and ?tab=gears select the Gears tab', () => {
  for (const u of ['https://aerostator.com/gears', 'https://aerostator.com/gears/', '/GEARS', 'https://x.test/?tab=gears', 'https://x.test/?tab=Gears&a=1', 'https://x.test/gears?x=1#h'])
    assert.equal(G.isGearsRoute(u), true, u);
});
t('other addresses do not', () => {
  for (const u of ['https://x.test/', '/schemas', '/gearshift', '/gears/extra', '?tab=schemas', '/production/gears'])
    assert.equal(G.isGearsRoute(u), false, u);
});
