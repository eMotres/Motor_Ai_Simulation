// node --test — Configure's Chinese must be Chinese (owner rule 2026-10-05:
// "do not mix Chinese with English").  In the ZH locale every string of the
// Configure Drive and Limits groups must be fully Chinese; the only Latin
// allowed is a part number (IQE018N06NM6SC ...), a unit (mm, A, rpm, kHz ...) and
// the technology / standard names (Si, SiC, GaN, PWM, SVPWM, MOSFET) and symbols
// (T_j).  In EN there must be no Chinese.
//
// It also guards the code: every tx('key') used by the Configure panel and its
// library must exist in BOTH languages, and the marked region of the panel
// (between "i18n-guard:begin" and "i18n-guard:end") must carry no hard-coded
// English text, title or label — everything there goes through tx().
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const load = (lng) => JSON.parse(readFileSync(join(SRC, 'locales', lng, 'controller.json'), 'utf8'));
const EN = load('en');
const ZH = load('zh-CN');
const GROUPS = ['configureDrive', 'configureLimits'];

const flat = (o, p = '') => Object.entries(o).flatMap(([k, v]) =>
  v && typeof v === 'object' ? flat(v, p ? `${p}.${k}` : k) : [[p ? `${p}.${k}` : k, v]]);
const get = (o, key) => key.split('.').reduce((a, k) => (a == null ? a : a[k]), o);

const ALLOWED = new Set(['PWM', 'SVPWM', 'MOSFET', 'Si', 'SiC', 'GaN', 'T_j',
  'mm', 'A', 'V', 'W', 'kW', 'rpm', 'kHz', 'Hz', 'ns', 'rms', 'K', 'x', 'N', 'm', 'P']);
const PART = /^[A-Z]{2,5}\d{2,}[A-Z0-9]*$/;                 // IQE018N06NM6SC, IGC016K10S2
const CJK = /[㐀-鿿＀-￯]/;

/** Latin words of a string that are NOT allowed (ICU placeholders removed). */
export function stray(text) {
  const t = String(text).replace(/\{[^{}]*\}/g, ' ').replace(/°C/g, ' ');
  return (t.match(/[A-Za-z][A-Za-z0-9_]*/g) || []).filter((w) => !ALLOWED.has(w) && !PART.test(w));
}

test('the checker itself: part numbers, units and Si/SiC/GaN pass; words fail', () => {
  assert.deepEqual(stray('器件 IQE018N06NM6SC · 48 kHz · SiC'), []);
  assert.deepEqual(stray('上限 {max} A rms，T_j {tj} °C，GaN 100 ns'), []);
  assert.deepEqual(stray('请 request calculation'), ['request', 'calculation']);
  assert.deepEqual(stray('Configure 不会计算'), ['Configure']);
  assert.deepEqual(stray('{count} 并联 default'), ['default']);
});

for (const g of GROUPS) {
  test(`${g}: ZH has every EN key, nothing extra`, () => {
    const en = flat(EN[g]).map(([k]) => k).sort();
    const zh = flat(ZH[g] ?? {}).map(([k]) => k).sort();
    assert.ok(en.length > 10);
    assert.deepEqual(zh, en);
  });

  test(`${g}: ZH is fully Chinese (no stray English words)`, () => {
    const bad = flat(ZH[g]).filter(([, v]) => stray(v).length)
      .map(([k, v]) => `${g}.${k}: ${JSON.stringify(v)} -> ${stray(v).join(', ')}`);
    assert.deepEqual(bad, []);
  });

  test(`${g}: a ZH entry is not a copy of the English text`, () => {
    const en = new Map(flat(EN[g]));
    const same = flat(ZH[g]).filter(([k, v]) => {
      const e = String(en.get(k));
      return v === e && stray(e).length > 0;       // identical AND it has words to translate
    }).map(([k]) => `${g}.${k}`);
    assert.deepEqual(same, []);
  });

  test(`${g}: ZH carries Chinese wherever the English has words; EN has no Chinese`, () => {
    const en = new Map(flat(EN[g]));
    for (const [k, v] of flat(ZH[g])) {
      if (stray(en.get(k)).length) assert.ok(CJK.test(v), `${g}.${k} has no Chinese: ${v}`);
    }
    for (const [k, v] of en) assert.ok(!CJK.test(String(v)), `EN ${g}.${k} contains Chinese`);
  });
}

const read = (rel) => readFileSync(join(SRC, rel), 'utf8');
const FILES = ['components/compare/ConfiguratorPanel.tsx', 'lib/configuratorDrive.ts'];

test('every tx() key used by the Configure code exists in EN and ZH', () => {
  const missing = [];
  for (const f of FILES) {
    const code = read(f);
    for (const m of code.matchAll(/\btx\(\s*'([A-Za-z0-9_.]+)'/g)) {
      for (const [lng, d] of [['en', EN], ['zh-CN', ZH]]) {
        if (typeof get(d, m[1]) !== 'string') missing.push(`${lng}: ${m[1]} (${f})`);
      }
    }
    for (const m of code.matchAll(/'(configure(?:Drive|Limits)\.[A-Za-z0-9_.]+)'/g)) {
      for (const [lng, d] of [['en', EN], ['zh-CN', ZH]]) {
        if (typeof get(d, m[1]) !== 'string') missing.push(`${lng}: ${m[1]} (${f})`);
      }
    }
  }
  assert.deepEqual([...new Set(missing)], []);
});

test('no hard-coded English text/title/label in the guarded region of the panel', () => {
  const code = read('components/compare/ConfiguratorPanel.tsx');
  const a = code.indexOf('i18n-guard:begin');
  const b = code.indexOf('i18n-guard:end');
  assert.ok(a > 0 && b > a, 'guard markers missing');
  const region = code.slice(a, b)
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, ' ')             // JSX comments
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/^\s*\/\/.*$/gm, ' ');
  const found = [];
  // JSX text between > and < (no braces: expressions are code, not text)
  for (const m of region.matchAll(/>([^<>{}]*)</g)) {
    if (/\)\s*:|\?\s*\(/.test(m[1])) continue;      // a ternary between two elements, not text
    const w = stray(m[1].replace(/[⚠·×=—–]/g, ' '));
    if (w.length) found.push(`text: ${m[1].trim()}`);
  }
  // literal attribute values that users read
  for (const m of region.matchAll(/\b(?:title|label|aria-label|placeholder)="([^"]*)"/g)) {
    if (stray(m[1]).length) found.push(`attr: ${m[1]}`);
  }
  assert.deepEqual(found, []);
});
