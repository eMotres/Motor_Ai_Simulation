/** Pure logic of the Admin configuration-visibility toggle and config-grant picker (`configVisibility.ts` has no runtime imports). */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  grantKey, splitGrantKey, isPrivateConfig, withConfigVisibility, grantRows, toggleKey,
  configsBody, motorsPutBody, privateCount,
} from '../configVisibility.ts';

const DIES = [
  { name: 'CIANO28 85 20SW1200', config_names: ['L13', 'L15'], private_configs: ['L13'] },
  { name: 'CIANO14 40 new', config_names: ['L12', 'L20'], private_configs: [] },
  { name: 'Plain die', config_names: ['L10'] },
];

test('grant keys round trip, even with slashes or spaces in the die name', () => {
  assert.equal(grantKey('CIANO28 85 20SW1200', 'L13'), 'CIANO28 85 20SW1200/L13');
  assert.deepEqual(splitGrantKey('CIANO28 85 20SW1200/L13'), { die: 'CIANO28 85 20SW1200', config: 'L13' });
  assert.deepEqual(splitGrantKey('a/b/L1'), { die: 'a/b', config: 'L1' });          // the config is after the LAST slash
  for (const bad of ['', 'nothing', '/L1', 'die/']) assert.equal(splitGrantKey(bad), null);
});

test('the toggle updates one configuration of one die and nothing else', () => {
  const on = withConfigVisibility(DIES, 'CIANO28 85 20SW1200', 'L15', 'private');
  assert.deepEqual(on[0].private_configs, ['L13', 'L15']);
  assert.equal(on[1], DIES[1]);                                                    // other dies untouched (same object)
  assert.deepEqual(DIES[0].private_configs, ['L13']);                              // the input is not mutated
  const off = withConfigVisibility(on, 'CIANO28 85 20SW1200', 'L13', 'public');
  assert.deepEqual(off[0].private_configs, ['L15']);
  assert.deepEqual(withConfigVisibility(DIES, 'Plain die', 'L10', 'private')[2].private_configs, ['L10']);   // a die with no list yet
  assert.deepEqual(withConfigVisibility(on, 'CIANO28 85 20SW1200', 'L15', 'private')[0].private_configs, ['L13', 'L15']);   // idempotent
});

test('isPrivateConfig / privateCount read the list the server sends', () => {
  assert.equal(isPrivateConfig(DIES[0], 'L13'), true);
  assert.equal(isPrivateConfig(DIES[0], 'L15'), false);
  assert.equal(isPrivateConfig(DIES[2], 'L10'), false);
  assert.deepEqual(DIES.map(privateCount), [1, 0, 0]);
});

test('picker rows: every private configuration, plus the grants the account already holds', () => {
  assert.deepEqual(grantRows(DIES, []).map((r) => r.key), ['CIANO28 85 20SW1200/L13']);
  assert.deepEqual(grantRows(null, []), []);
  const rows = grantRows(DIES, ['CIANO28 85 20SW1200/L15', 'Gone die/L1', 'CIANO28 85 20SW1200/L13', 'malformed']);
  assert.deepEqual(rows.map((r) => [r.key, r.isPrivate, r.exists]), [
    ['CIANO28 85 20SW1200/L13', true, true],
    ['CIANO28 85 20SW1200/L15', false, true],        // public now, still revocable
    ['Gone die/L1', false, false],                   // the die is gone, still revocable
  ]);
});

test('toggling a grant never mutates the set it was given', () => {
  const a = new Set(['x/L1']);
  const b = toggleKey(a, 'x/L2');
  assert.deepEqual([...a], ['x/L1']);
  assert.deepEqual([...b].sort(), ['x/L1', 'x/L2']);
  assert.deepEqual([...toggleKey(b, 'x/L1')], ['x/L2']);
});

test('the PUT body: sorted unique well-formed configs, always present', () => {
  assert.deepEqual(configsBody(['b/L2', 'a/L1', 'b/L2', 'junk', '/x']), ['a/L1', 'b/L2']);
  assert.deepEqual(configsBody([]), []);
  const body = motorsPutBody({ all: false, dies: new Set(['d']), configs: new Set(['d/L13']), defaultMotor: null });
  assert.deepEqual(body, { all: false, dies: ['d'], configs: ['d/L13'], default: null });
  assert.deepEqual(motorsPutBody({ all: true, dies: [], configs: [], defaultMotor: { die: 'd', config: 'L1' } }),
    { all: true, dies: [], configs: [], default: { die: 'd', config: 'L1' } });
});
