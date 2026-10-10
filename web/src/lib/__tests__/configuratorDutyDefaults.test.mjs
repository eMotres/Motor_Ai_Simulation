import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

let defaults = null;
try { defaults = await import('../configuratorDutyDefaults.ts'); } catch { /* old Node */ }
const t = defaults ? test : test.skip;
const panel = readFileSync(new URL('../../components/compare/ConfiguratorPanel.tsx', import.meta.url), 'utf8');

const preset = {
  die: 'CIANO14 40 new', config: 'L12', knobs: { rpm: 13000 },
  duty_points: [
    { name: 'rated', rpm: 13000, current_A: 42.78 },
    { name: 'peak', rpm: 14400, current_A: 48.79 },
  ],
};

t('Propeller default follows exact active catalog duty and retains rated fallback', () => {
  assert.deepEqual(defaults.propellerDutyRpmDefault(preset,
    { die: 'CIANO14 40 new', config: 'L12', duty: 'peak' }),
  { rpm: 14400, key: 'CIANO14 40 new|L12|peak|14400', source: 'active-duty' });
  assert.equal(defaults.propellerDutyRpmDefault(preset,
    { die: 'CIANO14 40 new', config: 'L12', duty: 'rated' }).rpm, 13000);
  assert.equal(defaults.propellerDutyRpmDefault(preset, null).rpm, 13000);
});

t('a duty from another build cannot seed this preset; missing rated falls back to preset value', () => {
  assert.equal(defaults.propellerDutyRpmDefault(preset,
    { die: 'CIANO14 40 new', config: 'L20', duty: 'peak' }).rpm, 13000);
  const noRated = { ...preset, duty_points: [{ name: 'peak', rpm: 14400, current_A: 48.79 }] };
  assert.equal(defaults.propellerDutyRpmDefault(noRated, null).rpm, 13000);
});

t('Manual retains its live RPM while Propeller initializes from its catalog point', () => {
  assert.equal(defaults.initialRpmForLoadMode('manual', 1000, 2000, 13000), 1000);
  assert.equal(defaults.initialRpmForLoadMode('prop', 1000, 2000, 14400), 14400);
  assert.equal(defaults.initialRpmForLoadMode('prop', 1000, 2000, 13000), 13000);
  assert.equal(defaults.initialRpmForLoadMode('prop', 1000, 2000, null), 1000);
});

t('applying a preset seeds its RPM and seed identity from the same normalized catalog duty', () => {
  assert.match(panel, /const nk = \{ \.\.\.nk0, rpm: initialRpmForLoadMode\(propLoad \? 'prop' : 'manual',/);
  assert.match(panel, /propRpmSeedRef\.current = `\$\{catId \?\? refId\}\|\$\{catalogPoint\?\.key/);
});
