// node --test - the direct link /gears opens the Gears tab (not the last used tab).
import test from 'node:test';
import assert from 'node:assert/strict';

let G = null; let M = null;
try { G = await import('../gearsRoute.ts'); M = await import('../simulationsMenu.ts'); } catch { /* old Node */ }
const t = G ? test : test.skip;

t('/gears and ?tab=gears select the Gears tab', () => {
  for (const u of ['https://aerostator.com/gears', 'https://aerostator.com/gears/', '/GEARS', 'https://x.test/?tab=gears', 'https://x.test/?tab=Gears&a=1', 'https://x.test/gears?x=1#h'])
    assert.equal(G.isGearsRoute(u), true, u);
});
t('other addresses do not', () => {
  for (const u of ['https://x.test/', '/schemas', '/gearshift', '/gears/extra', '?tab=schemas', '/production/gears'])
    assert.equal(G.isGearsRoute(u), false, u);
});

t('Gears is a Simulations member: the direct link selects it, the bar shows Simulations (no top-level Gears tab), Schemas stays last', () => {
  assert.equal(M.isSimulationTab('gears'), true);
  assert.equal(M.barValue('gears'), 'simulations');
  const ids = [...M.SIMULATION_TAB_IDS];
  assert.equal(ids[ids.length - 1], 'schemas');
  assert.equal(ids.indexOf('gears'), ids.indexOf('schemas') - 1);
  const tabs = [{ id: 'simulation', label: 'Electromagnetic', order: 50 }, { id: 'gears', label: 'Gears', order: 62 }, { id: 'schemas', label: 'Schemas', order: 61 }, { id: 'compare', label: 'Configure', order: 75 }];
  const bar = M.barEntries(tabs);
  assert.deepEqual(bar.map((e) => e.id), ['simulations', 'compare']);
  assert.deepEqual(bar[0].members.map((m) => m.id), ['simulation', 'gears', 'schemas']);
  // the remembered "last member" accepts gears (its validator is the member list), so a reload keeps the sub-view
  assert.equal(M.simulationsTarget('gears', bar[0].members), 'gears');
  assert.equal(M.simulationsTarget('thermal', bar[0].members), 'simulation');
  assert.equal(M.simulationsLabel('Simulations', 'gears', bar[0].members), 'Simulations · Gears');
});
