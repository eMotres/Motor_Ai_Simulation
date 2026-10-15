import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { staleCatalogDutyNames } from '../../../lib/catalogCardStaleness.ts';

test('a duty result on a different current build makes the card stale', () => {
  assert.deepEqual(staleCatalogDutyNames('new', [
    { name: 'rated', result: { build_sig: 'new' } },
    { name: 'peak', result: { build_sig: 'old' } },
  ]), ['peak']);
});

test('an explicitly stale secondary run makes the whole card stale', () => {
  assert.deepEqual(staleCatalogDutyNames('same', [
    { name: 'rated', result: { build_sig: 'same' }, runs: [{ stale: false }, { stale: true }] },
  ]), ['rated']);
});

test('unknown and empty build signatures do not invent staleness', () => {
  assert.deepEqual(staleCatalogDutyNames(undefined, [
    { name: 'unknown-current', result: { build_sig: 'old' } },
    { name: 'unknown-result', result: {}, runs: [{ stale: null }] },
  ]), []);
  assert.deepEqual(staleCatalogDutyNames('current', [
    { name: 'empty-result', result: { build_sig: '' } },
  ]), []);
});

test('FamilyCatalog wires stale duties to both visible card badge levels', () => {
  const family = readFileSync(new URL('../FamilyCatalog.tsx', import.meta.url), 'utf8');
  const badge = readFileSync(new URL('../../common/CardBadge.tsx', import.meta.url), 'utf8');
  assert.match(family, /olderBuildConfigs=\{die\.configs\.filter\(/);
  assert.match(family, /olderBuild=\{curStaleDuties\.length > 0\}/);
  assert.match(family, /olderBuild=\{staleCatalogDutyNames\(c\.build_sig, c\.duties\)\.length > 0\}/);
  assert.match(badge, /olderBuild \? AMBER : GREEN/);
  assert.match(badge, /s\.state === 'all' && !olderBuild \? GREEN : AMBER/);
});