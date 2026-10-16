/** Pure logic of the Admin configuration access rows and config-grant picker (`configVisibility.ts` has no runtime imports). */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  grantKey, splitGrantKey, grantRows, toggleKey, configsBody, motorsPutBody,
  configAccessOf, openConfigs, configPutBody, canSaveConfig, withConfigAccess,
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

test('configuration access: from the server rows, or derived from private_configs on an older server', () => {
  const rows = [{ name: 'L13', status: 'private', clients: [] }, { name: 'L15', status: 'public', clients: [] }];
  assert.equal(configAccessOf({ name: 'd', config_access: rows }), rows);
  assert.deepEqual(configAccessOf(DIES[0]), [
    { name: 'L13', status: 'private', clients: [] }, { name: 'L15', status: 'inherit', clients: [] }]);
  assert.deepEqual(configAccessOf({ name: 'empty' }), []);
});

test('open configurations: Public and Selected clients only', () => {
  const die = { name: 'd', config_access: [
    { name: 'A', status: 'inherit', clients: [] }, { name: 'B', status: 'private', clients: [] },
    { name: 'C', status: 'public', clients: [] }, { name: 'D', status: 'selected', clients: ['x@y.z'] }] };
  assert.deepEqual(openConfigs(die).map((c) => c.name), ['C', 'D']);
  assert.deepEqual(openConfigs(DIES[0]), []);
});

test('the configuration PUT body and its save guard', () => {
  assert.deepEqual(configPutBody('inherit', ['a@b.c']), { status: 'inherit' });
  assert.deepEqual(configPutBody('public', []), { status: 'public' });
  assert.deepEqual(configPutBody('selected', [' B@x.io ', 'a@x.io', 'b@x.io', '']), { status: 'selected', clients: ['a@x.io', 'b@x.io'] });
  assert.equal(canSaveConfig('selected', []), false);
  assert.equal(canSaveConfig('selected', ['  ']), false);
  assert.equal(canSaveConfig('selected', ['a@x.io']), true);
  for (const s of ['inherit', 'private', 'public']) assert.equal(canSaveConfig(s, []), true);
});

test('updating one configuration touches that configuration of that die only', () => {
  const on = withConfigAccess(DIES, 'CIANO28 85 20SW1200', 'L15', 'selected', ['a@x.io']);
  assert.deepEqual(on[0].config_access, [
    { name: 'L13', status: 'private', clients: [] }, { name: 'L15', status: 'selected', clients: ['a@x.io'] }]);
  assert.deepEqual(on[0].private_configs, ['L13', 'L15']);                          // selected is stored private
  assert.equal(on[1], DIES[1]);                                                    // other dies untouched (same object)
  assert.deepEqual(DIES[0].private_configs, ['L13']);                              // the input is not mutated
  const pub = withConfigAccess(on, 'CIANO28 85 20SW1200', 'L13', 'public', []);
  assert.deepEqual(pub[0].private_configs, ['L15']);
  assert.equal(pub[0].config_access[0].status, 'public');
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
