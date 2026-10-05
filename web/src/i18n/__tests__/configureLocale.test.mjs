// node --test — Configure's Chinese must be Chinese (owner rule 2026-10-05:
// "do not mix Chinese with English", extended the same day to the WHOLE Configure
// tree: result tiles, header, saved-configuration columns, charts, battery,
// charging, thermal, cooling, drafts).
//
// What it pins:
//   * ZH has every EN key of the Configure groups, nothing extra, and every ZH
//     value is fully Chinese — the only Latin allowed is a part number
//     (IQE018N06NM6SC ...), a unit (mm, A, rpm, kHz ...), a technology / standard
//     name (Si, SiC, GaN, PWM, SVPWM, MOSFET, AWG), a quantity symbol (Ld, Kt, KV,
//     T_j, V_oc ...).  EN has no Chinese; a ZH value is never a copy of the EN one.
//   * every tx('key') used anywhere in the Configure tree exists in EN and ZH;
//   * the tree's SOURCE carries no hard-coded English: no sentence-like string
//     literal, no JSX text, no title / label / placeholder literal and no canvas
//     text outside tx().  (Data that comes from outside — device part numbers,
//     motor names, variant provenance — is not source and is shown beside a ZH label.)
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const load = (lng) => JSON.parse(readFileSync(join(SRC, 'locales', lng, 'controller.json'), 'utf8'));
const EN = load('en');
const ZH = load('zh-CN');
const GROUPS = ['configureDrive', 'configureLimits', 'configure'];

const flat = (o, p = '') => Object.entries(o).flatMap(([k, v]) =>
  v && typeof v === 'object' ? flat(v, p ? `${p}.${k}` : k) : [[p ? `${p}.${k}` : k, v]]);
const get = (o, key) => key.split('.').reduce((a, k) => (a == null ? a : a[k]), o);

const ALLOWED = new Set([
  // technologies / standards / chemistries
  'PWM', 'SVPWM', 'MOSFET', 'Si', 'SiC', 'GaN', 'AWG', 'NMC', 'LFP', 'LiFePO', 'XY', 'TS',
  // units
  'mm', 'cm', 'km', 'A', 'V', 'W', 'kW', 'rpm', 'kHz', 'Hz', 'ns', 'rms', 'K', 'N', 'm', 'k', 'kg', 'Ah', 'Wb', 'mWb', 'mH', 'L', 'min', 's',
  // an insulation class letter (class F)
  'F',
  // quantity symbols (docs/I18N.md: never translated)
  'T_j', 'Ld', 'Lq', 'KV', 'Kt', 'Km', 'PM', 'P', 'I', 'J', 'R', 'h', 'C', 'dq', 'ICU',
]);
const PART = /^[A-Z]{2,5}\d{2,}[A-Z0-9]*$/;                 // IQE018N06NM6SC, IGC016K10S2
const SYMBOL = /^[A-Za-z]{1,2}(_[A-Za-z0-9]+)+$/;            // V_oc, R_pack, P_charge, k_T, k_flux
const CJK = /[㐀-鿿＀-￯]/;

/** Latin words of a string that are NOT allowed (ICU placeholders removed). */
export function stray(text) {
  const t = String(text).replace(/\{[^{}]*\}/g, ' ').replace(/°C/g, ' ').replace(/\d+[SP]\b/g, ' ');
  return (t.match(/[A-Za-z][A-Za-z0-9_]*/g) || [])
    .filter((w) => !ALLOWED.has(w) && !PART.test(w) && !SYMBOL.test(w));
}

test('the checker itself: part numbers, units, symbols and Si/SiC/GaN pass; words fail', () => {
  assert.deepEqual(stray('器件 IQE018N06NM6SC · 48 kHz · SiC'), []);
  assert.deepEqual(stray('上限 {max} A rms，T_j {tj} °C，GaN 100 ns，V_oc、R_pack、Lq / Ld'), []);
  assert.deepEqual(stray('请 request calculation'), ['request', 'calculation']);
  assert.deepEqual(stray('Configure 不会计算'), ['Configure']);
  assert.deepEqual(stray('{count} 并联 default'), ['default']);
  assert.deepEqual(stray('接法 2S / 2P'), []);
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

// ── the source of the whole Configure tree ──────────────────────────────────
const read = (rel) => readFileSync(join(SRC, rel), 'utf8');
export const TREE = [
  'components/compare/ConfiguratorPanel.tsx',
  'components/compare/MyAgentDraftsBlock.tsx',
  'components/compare/GeometryProjections.tsx',
  'components/compare/BatteryPanel.tsx',
  'components/compare/PerformanceCharts.tsx',
  'components/compare/EfficiencyMap.tsx',
  'components/compare/ConfiguratorThermal.tsx',
  'components/compare/ChargePanel.tsx',
  'components/thermal/CoolingControls.tsx',
  'lib/configuratorDrive.ts',
  'lib/configuratorLimits.ts',
];

const stripComments = (code) => code
  .replace(/\/\*[\s\S]*?\*\//g, ' ')                  // block + JSX comments
  .replace(/(^|[^:'"`\\])\/\/.*$/gm, '$1 ');           // line comments (not inside URLs)

test('every tx() key used by the Configure tree exists in EN and ZH', () => {
  const missing = [];
  for (const f of TREE) {
    const code = stripComments(read(f));
    const keys = new Set();
    for (const m of code.matchAll(/\btx\(\s*'([A-Za-z0-9_.]+)'/g)) keys.add(m[1]);
    for (const m of code.matchAll(/'(configure(?:Drive|Limits)?\.[A-Za-z0-9_.]+)'/g)) {
      if (!m[1].includes('myAgentDrafts')) keys.add(m[1]);      // that one is a localStorage key
    }
    for (const k of keys) {
      for (const [lng, d] of [['en', EN], ['zh-CN', ZH]]) {
        if (typeof get(d, k) !== 'string') missing.push(`${lng}: ${k} (${f})`);
      }
    }
  }
  assert.deepEqual([...new Set(missing)], []);
});

const CSSISH = /\b(px|solid|dashed|rgba?|var\(|sans-serif|monospace|auto|none|center|nowrap|ellipsis|column|row|flex|grid|pointer|help|bold|uppercase|hidden|block|relative|absolute|translate|rotate|inherit|transparent)\b/;
/** words that begin JS statements, so `} else {` is not mistaken for JSX text */
const JS_WORDS = new Set(['else', 'return', 'const', 'let', 'if', 'for', 'try', 'catch', 'finally', 'break', 'continue', 'void', 'await', 'new', 'null', 'true', 'false', 'undefined']);

/** Hard-coded English in the SOURCE of one file (empty = clean). */
export function hardCoded(code0) {
  const code = stripComments(code0);
  const found = [];
  const bad = (t) => stray(t.replace(/[⚠·×=—–●○→←∶:,.;()%+\-–]/g, ' ')).filter((w) => !JS_WORDS.has(w));
  // 1. JSX text: between '>' and '<' / '{' (or '}' and '<' / '{'), free of code punctuation
  for (const m of code.matchAll(/(?<![=\-])[>}]([^<>{}\n;=()[\]`'"]*)(?=[<{])/g)) {
    const t = m[1];
    if (!t.trim() || /:|\w\.\w|^\s*(,|as\b)/.test(t)) continue;      // code: `a ? b : c`, `x.y`, `as T`, `, key:`
    if (bad(t).length) found.push(`jsx text: ${t.trim()}`);
  }
  // multi-line JSX text (a sentence wrapped over lines)
  for (const m of code.matchAll(/>\s*\n\s*([A-Za-z][^<>{}\n;=()[\]`'"]*(?:\n[^<>{}\n;=()[\]`'"]+)*)\s*\n?\s*</g)) {
    if (!/:|\w\.\w/.test(m[1]) && bad(m[1]).length) found.push(`jsx text: ${m[1].trim().slice(0, 60)}`);
  }
  // 2. user-facing attribute / property literals, any length
  for (const m of code.matchAll(/\b(?:title|label|placeholder|aria-label|helperText)\s*=\s*(?:"([^"]*)"|\{\s*'([^']*)'\s*\}|\{\s*`([^`]*)`\s*\})/g)) {
    const t = m[1] ?? m[2] ?? m[3] ?? '';
    if (bad(t.replace(/\$\{[^}]*\}/g, ' ')).length) found.push(`attr: ${t}`);
  }
  for (const m of code.matchAll(/\b(?:title|label|text|tip|hint|note|name)\s*:\s*(?:'([^']*)'|"([^"]*)"|`([^`]*)`)/g)) {
    const t = m[1] ?? m[2] ?? m[3] ?? '';
    if (bad(t.replace(/\$\{[^}]*\}/g, ' ')).length) found.push(`prop: ${t}`);
  }
  // 3. canvas text
  for (const m of code.matchAll(/fillText\(\s*(?:'([^']*)'|"([^"]*)"|`([^`]*)`)/g)) {
    const t = m[1] ?? m[2] ?? m[3] ?? '';
    if (bad(t.replace(/\$\{[^}]*\}/g, ' ')).length) found.push(`canvas: ${t}`);
  }
  // 4. sentence-like string literals (two or more Latin words) anywhere
  for (const m of code.matchAll(/'([^'\n]*)'|"([^"\n]*)"|`([^`]*)`/g)) {
    const t = (m[1] ?? m[2] ?? m[3] ?? '').replace(/\$\{[^}]*\}/g, ' ');
    if (!/[A-Za-z]{2,} +[A-Za-z]{2,}/.test(t)) continue;               // a sentence has words next to words
    if (CSSISH.test(t)) continue;
    if (/^configure(Drive|Limits)?\./.test(t)) continue;               // a locale key
    const words = bad(t);
    if (words.length >= 2) found.push(`sentence: ${t.slice(0, 70)}`);
  }
  return [...new Set(found)];
}

test('the source scanner flags hard-coded English and leaves translated code alone', () => {
  assert.ok(hardCoded(`<Typography>Performance across speed</Typography>`).length);
  assert.ok(hardCoded(`<Box title="Rename" />`).length);
  assert.ok(hardCoded("const a = { label: 'Torque', unit: 'N·m' };").length);
  assert.ok(hardCoded("ctx.fillText('Speed (rpm)', 1, 2);").length);
  assert.ok(hardCoded("const m = 'the pack would be pushed above its maximum';").length);
  assert.deepEqual(hardCoded(`<Typography>{tx('configure.perfTitle')}</Typography>
    <Box title={tx('configure.x')} sx={{ border: '1px solid var(--line)', fontFamily: 'monospace' }} />
    const u = { label: tx('configure.colTorque'), unit: 'N·m' }; ctx.fillText(tx('a.b'), 1, 2);
    <Box>{x ? 'a' : 'b'} mm · kW</Box> } else { <Box>A V W</Box>`), []);
});

for (const f of TREE) {
  test(`no hard-coded English in ${f}`, () => {
    assert.deepEqual(hardCoded(read(f)), []);
  });
}
