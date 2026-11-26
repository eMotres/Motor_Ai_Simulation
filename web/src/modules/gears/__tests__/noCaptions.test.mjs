// UI text rules (AGENTS.md), same guard as modules/development: no caption / helper lines on the Gears page.
// One short label + a tooltip; validation errors stay loud (red, role="alert").
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const files = (dir) => readdirSync(dir).flatMap((n) => {
  const p = join(dir, n);
  if (n === '__tests__') return [];
  return statSync(p).isDirectory() ? files(p) : /\.(tsx?|jsx?)$/.test(n) ? [p] : [];
});

test('no caption variant, helperText or FormHelperText in the Gears module', () => {
  const bad = [];
  for (const f of files(root)) {
    const src = readFileSync(f, 'utf8');
    const rel = relative(root, f).split(sep).join('/');
    if (/variant=["'{]+\s*['"]?caption/.test(src)) bad.push(`${rel}: variant caption`);
    if (/helperText\s*=/.test(src)) bad.push(`${rel}: helperText`);
    if (/FormHelperText/.test(src)) bad.push(`${rel}: FormHelperText`);
  }
  assert.deepEqual(bad, []);
});

test('no min-max range strings in the Gears locale', () => {
  for (const lng of ['en', 'zh-CN']) {
    const loc = readFileSync(fileURLToPath(new URL(`../../../locales/${lng}/gears.json`, import.meta.url)), 'utf8');
    assert.equal(/\d+\s*[-–]\s*\d+\s*(mm|rpm|N)/.test(loc), false, lng);
  }
});

test('every static translation key used by the module exists in EN and zh-CN', () => {
  const load = (lng) => JSON.parse(readFileSync(fileURLToPath(new URL(`../../../locales/${lng}/gears.json`, import.meta.url)), 'utf8'));
  const has = (o, key) => key.split('.').reduce((a, k) => (a && typeof a === 'object' ? a[k] : undefined), o) !== undefined;
  const en = load('en'), zh = load('zh-CN');
  const missing = [];
  for (const f of files(root)) {
    const src = readFileSync(f, 'utf8');
    for (const m of src.matchAll(/\bt\(\s*'([A-Za-z0-9_.]+)'/g)) for (const [n, o] of [['en', en], ['zh-CN', zh]]) if (!has(o, m[1])) missing.push(`${n}: ${m[1]} (${relative(root, f)})`);
    for (const m of src.matchAll(/<Lbl k="([A-Za-z0-9_]+)"/g)) for (const [n, o] of [['en', en], ['zh-CN', zh]]) {
      if (!has(o, `l.${m[1]}`)) missing.push(`${n}: l.${m[1]}`);
      if (!has(o, `tip.${m[1]}`)) missing.push(`${n}: tip.${m[1]}`);
    }
  }
  assert.deepEqual([...new Set(missing)], []);
});
