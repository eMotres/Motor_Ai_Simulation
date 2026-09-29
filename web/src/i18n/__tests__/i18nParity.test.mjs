// node --test — locale key parity (docs/I18N.md).
//
// EN is the source.  Every locale mirrors its namespaces and key sets:
//   • a zh-CN key MISSING vs EN is REPORTED (console.warn), not fatal — the
//     runtime falls back to English, and translation batches land later;
//   • a zh-CN key that EN does not have is an ERROR (a typo or a stale key);
//   • every message in every locale must parse as ICU MessageFormat, and a
//     translation must use exactly the placeholders its English source uses.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from '@formatjs/icu-messageformat-parser';

const LOCALES = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'locales');
const load = (lng, ns) => JSON.parse(readFileSync(join(LOCALES, lng, ns), 'utf8'));
const flat = (o, p = '') => Object.entries(o).flatMap(([k, v]) =>
  v && typeof v === 'object' ? flat(v, p ? `${p}.${k}` : k) : [[p ? `${p}.${k}` : k, v]]);
const args = (ast, out = new Set()) => {
  for (const el of ast) {
    if (el.value !== undefined && el.type !== 0) out.add(el.value);  // type 0 = literal
    if (el.options) for (const o of Object.values(el.options)) args(o.value, out);
  }
  return out;
};

const enFiles = readdirSync(join(LOCALES, 'en')).filter((f) => f.endsWith('.json'));
const others = readdirSync(LOCALES, { withFileTypes: true })
  .filter((d) => d.isDirectory() && d.name !== 'en').map((d) => d.name);

test('there is at least one mirror locale and EN has namespaces', () => {
  assert.ok(enFiles.length >= 2);
  assert.ok(others.includes('zh-CN'));
});

for (const lng of others) {
  test(`${lng}: same namespaces, no extra keys, ICU-valid, same placeholders`, () => {
    const files = new Set(readdirSync(join(LOCALES, lng)).filter((f) => f.endsWith('.json')));
    const missing = [];
    for (const ns of enFiles) {
      const en = new Map(flat(load('en', ns)));
      if (!files.has(ns)) { missing.push(`${ns} (whole namespace)`); continue; }
      const tr = new Map(flat(load(lng, ns)));
      for (const k of en.keys()) if (!tr.has(k)) missing.push(`${ns}:${k}`);
      const extra = [...tr.keys()].filter((k) => !en.has(k));
      assert.deepEqual(extra, [], `${lng}/${ns}: keys not in EN`);
      for (const [k, v] of tr) {
        assert.equal(typeof v, 'string', `${lng}/${ns}:${k} must be a string`);
        const a = args(parse(v, { ignoreTag: true }));
        const e = args(parse(en.get(k), { ignoreTag: true }));
        assert.deepEqual([...a].sort(), [...e].sort(), `${lng}/${ns}:${k} placeholders differ from EN`);
      }
    }
    for (const ns of files) assert.ok(enFiles.includes(ns), `${lng}/${ns} has no EN source`);
    if (missing.length) console.warn(`[i18n] ${lng}: ${missing.length} key(s) missing, English is shown:\n  ${missing.join('\n  ')}`);
  });
}

test('EN messages all parse as ICU MessageFormat', () => {
  for (const ns of enFiles) for (const [k, v] of flat(load('en', ns))) {
    assert.doesNotThrow(() => parse(v, { ignoreTag: true }), `en/${ns}:${k}`);
  }
});
