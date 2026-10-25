// UI text rules guard (owner 2026-10-26, "a thousand times"): no small caption / helper lines anywhere the customer looks.
// One short label + a tooltip; no helper lines under fields or panels; no min-max ranges (one worst value).
// This test scans the customer-facing sources (Development module, Configure) and fails on
//   - Typography variant="caption" (or 'caption'),
//   - helperText= / FormHelperText (a grey explanation under a field),
//   - a "{min}–{max}" range in a visible Configure pack/summary string.
// What a caption said belongs in the tooltip of the field / tile label (Tooltip title or HelpTip).
// A real value, a unit, a table header or a RED validation error is not a caption and is not affected.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const src = fileURLToPath(new URL('../../../', import.meta.url));
const SWEPT = ['modules/development', 'components/compare'];

// Explicit allow-list: file (relative to web/src, '/' separators) -> { pattern name -> allowed count } + why.
const ALLOWED = {
  // The load-inertia field shows its RED validation error (negative inertia) as the TextField's error text; that is a
  // loud validation message, not a helper line.  Nothing else in the file may use helperText.
  'modules/development/components/LoadSection.tsx': { helperText: 1 },
};

const PATTERNS = {
  caption: /variant\s*[=:]\s*\{?\s*["']caption["']/g,
  helperText: /\bhelperText\s*=/g,
  formHelperText: /\bFormHelperText\b/g,
};

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) { if (name !== '__tests__') yield* walk(path); }
    else if (/\.(tsx|ts)$/.test(name)) yield path;
  }
}

test('no caption variant, helperText or FormHelperText in the customer-facing sources', () => {
  const problems = [];
  let files = 0;
  for (const root of SWEPT) {
    for (const path of walk(join(src, root))) {
      files++;
      const rel = relative(src, path).split(sep).join('/');
      const text = readFileSync(path, 'utf8');
      for (const [name, re] of Object.entries(PATTERNS)) {
        const found = (text.match(re) ?? []).length;
        const allowed = ALLOWED[rel]?.[name] ?? 0;
        if (found > allowed) problems.push(`${rel}: ${found} x ${name} (allowed ${allowed}) - put it in a tooltip or remove it`);
      }
    }
  }
  assert.ok(files > 10, 'the swept folders were found');
  assert.deepEqual(problems, []);
});

test('the allow-list only names files that exist and still need the exception', () => {
  for (const [rel, counts] of Object.entries(ALLOWED)) {
    const text = readFileSync(join(src, rel), 'utf8');
    for (const [name, allowed] of Object.entries(counts)) {
      assert.equal((text.match(PATTERNS[name]) ?? []).length, allowed, `${rel}: ${name} allow-list is stale`);
    }
  }
});

test('Configure shows the battery pack as one voltage; the range lives in the tooltip', () => {
  for (const lang of ['en', 'zh-CN']) {
    const strings = JSON.parse(readFileSync(join(src, `locales/${lang}/controller.json`), 'utf8')).configure;
    assert.equal(strings.packSummary, undefined, 'the old "nom · min–max" caption is gone');
    assert.doesNotMatch(strings.packVoltage, /\{min\}|\{max\}/, 'the visible pack label has no range');
    assert.match(strings.packTip, /\{min\}.*\{max\}|\{max\}.*\{min\}/, 'the tooltip carries the extremes');
  }
  const battery = readFileSync(join(src, 'components/compare/BatteryPanel.tsx'), 'utf8');
  assert.match(battery, /configure\.packVoltage/);
  assert.doesNotMatch(battery, /configure\.packSummary/);
});
