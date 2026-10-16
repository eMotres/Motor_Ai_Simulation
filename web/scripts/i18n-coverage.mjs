// Rough i18n coverage: counts user-visible string literals left in .tsx files
// (JSX text with letters, and label/title/placeholder/helperText/tooltip string
// props) versus t()/<Trans> call sites.  A heuristic for tracking the extraction
// batches (docs/I18N.md), not a gate.   node scripts/i18n-coverage.mjs [--files]
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const root = new URL('../src/', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const walk = (d) => readdirSync(d).flatMap((f) => {
  const p = join(d, f);
  return statSync(p).isDirectory() ? (f === '__tests__' ? [] : walk(p)) : p.endsWith('.tsx') ? [p] : [];
});
const RAW = [
  />\s*([A-Za-z][A-Za-z0-9 ,.'’()\-:/?!&%]{2,})\s*</g,                     // JSX text
  /\b(?:label|title|placeholder|helperText|aria-label|tooltip)=\s*["']([^"'{}]*[A-Za-z]{2,}[^"'{}]*)["']/g,
  /\b(?:label|title|placeholder|helperText)=\{\s*["'`]([^"'`]*[A-Za-z]{2,}[^"'`]*)["'`]\s*\}/g,
];
const TR = /\b(?:t|tx|tr)\(\s*['"`]|<Trans\b|i18nKey=/g;
let raw = 0, tr = 0; const rows = [];
for (const f of walk(root)) {
  const s = readFileSync(f, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  let r = 0; for (const rx of RAW) r += (s.match(rx) || []).length;
  const k = (s.match(TR) || []).length;
  raw += r; tr += k; rows.push([relative(root, f).split('\\').join('/'), r, k]);
}
const pct = (100 * tr / Math.max(1, tr + raw)).toFixed(1);
console.log(`translated call sites: ${tr}   literal UI strings left: ${raw}   coverage ≈ ${pct} %`);
if (process.argv.includes('--files')) {
  rows.filter((r) => r[1] > 0).sort((a, b) => b[1] - a[1]).forEach((r) => console.log(`${String(r[1]).padStart(5)} left ${String(r[2]).padStart(4)} done  ${r[0]}`));
}
