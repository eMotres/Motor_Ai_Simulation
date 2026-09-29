// Helper for source-text tests that pin a UI string (docs/I18N.md).
//
// Since the zh-CN mirror (#53) a literal like `<MenuItem value="x">steady state</MenuItem>`
// may be written `<MenuItem value="x">{tx('steadyState')}</MenuItem>` with the English
// text in web/src/locales/en/<ns>.json.  `uiForms(before, text, after)` returns every
// spelling the source may use for the same English string, so a test pins the TEXT
// (via the EN locale) and not how it is spelled in the .tsx.
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const EN_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'locales', 'en');

function flatten(obj, prefix, out) {
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object') flatten(v, key, out);
    else out.push([key, String(v)]);
  }
  return out;
}

let _pairs = null;
function enPairs() {
  if (_pairs) return _pairs;
  _pairs = [];
  for (const f of readdirSync(EN_DIR)) {
    if (!f.endsWith('.json')) continue;
    flatten(JSON.parse(readFileSync(join(EN_DIR, f), 'utf8')), '', _pairs);
  }
  return _pairs;
}

/** Keys whose English text is exactly `text`. */
export function enKeysFor(text) {
  return enPairs().filter(([, v]) => v === text).map(([k]) => k);
}

/**
 * Every spelling of `before + text + after` the source may carry.
 * `quoted` = the text sits inside an attribute's double quotes (label="Carrier"),
 * so the tx() form replaces the quotes too (label={tx('carrier')}).
 */
export function uiForms(before, text, after = '', { quoted = false } = {}) {
  const forms = [quoted ? `${before}"${text}"${after}` : `${before}${text}${after}`];
  for (const k of enKeysFor(text)) {
    forms.push(`${before}{tx('${k}')}${after}`, `${before}{t('${k}')}${after}`);
  }
  return forms;
}

/** Index of the first spelling found in `src` (-1 when none). */
export function indexOfUi(src, forms, from = 0) {
  let best = -1;
  for (const f of forms) {
    const i = src.indexOf(f, from);
    if (i > -1 && (best === -1 || i < best)) best = i;
  }
  return best;
}

/** Does `src` carry any spelling of the string? */
export function hasUi(src, forms) {
  return forms.some((f) => src.includes(f));
}
