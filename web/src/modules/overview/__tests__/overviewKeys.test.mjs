// node --test: every literal i18n key the Overview page renders is a STRING in EN and zh-CN.
// Hotfix 2026-11-17: overview.stage (the column header) became an object -> React error 31 on the live page.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const page = readFileSync(new URL('../OverviewPage.tsx', import.meta.url), 'utf8');
const keys = [...new Set([...page.matchAll(/\bt\('((?:overview|displayState|designation)\.[A-Za-z0-9_.]+)'/g)].map((m) => m[1]))];
const locale = (lang) => JSON.parse(readFileSync(new URL(`../../../locales/${lang}/common.json`, import.meta.url), 'utf8'));
const lookup = (obj, key) => key.split('.').reduce((o, k) => (o && typeof o === 'object' ? o[k] : undefined), obj);

test('the page renders literal keys', () => {
  assert.ok(keys.includes('overview.stage') && keys.length > 15, keys.join(' '));
});

for (const lang of ['en', 'zh-CN']) {
  test(`${lang}: every literal key of the Overview page is a string`, () => {
    const d = locale(lang);
    const bad = keys.filter((k) => typeof lookup(d, k) !== 'string');
    assert.deepEqual(bad, []);
  });
  test(`${lang}: the derived stage names exist`, () => {
    for (const s of ['in_production', 'computed', 'specification', 'simulation', 'confirmation', 'finished']) {
      assert.equal(typeof lookup(locale(lang), `overview.stageName.${s}`), 'string', s);
    }
  });
}
