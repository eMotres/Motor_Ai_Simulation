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

test('every spelling of the current build counts: a stamp taken before the default materials were named is current (owner 2026-11-11)', () => {
  assert.deepEqual(staleCatalogDutyNames(['named', 'unnamed'], [
    { name: 'rated', result: { build_sig: 'unnamed' } },
    { name: 'peak', result: { build_sig: 'named' } },
    { name: 'old', result: { build_sig: 'older' } },
  ]), ['old']);
  assert.deepEqual(staleCatalogDutyNames([], [{ name: 'x', result: { build_sig: 'a' } }]), []);
});

test('the older-build information lives in the configuration button tooltip, not in a chip in the row', () => {
  const family = readFileSync(new URL('../FamilyCatalog.tsx', import.meta.url), 'utf8');
  assert.match(family, /staleDuties: staleCatalogDutyNames\(c\.build_sig_aliases \?\? c\.build_sig, c\.duties\)/);
  assert.match(family, /configCardTip\(die, b\.config\)/);
  assert.doesNotMatch(family, /CardBadge|CardSummary/);
  // the shared badge component stays for the admin pickers and Configure
  const badge = readFileSync(new URL('../../common/CardBadge.tsx', import.meta.url), 'utf8');
  assert.match(badge, /olderBuild \? AMBER : GREEN/);
});
