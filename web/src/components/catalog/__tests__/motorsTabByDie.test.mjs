// node --test: the MOTORS TAB itself (MotorsCatalog): since 2026-11-17 the one by-die catalog for every role (owner: "the
// catalog view must be the same for everybody"; #730 / #731 had it behind an admin switch).  esbuild bundles the real tab; it is
// rendered server-side with the real EN locale files, the tree answered by a stubbed fetch and held in lib/familyTree's
// memo, the remembered view in a stubbed localStorage.  No DOM library is in the repository, so the click itself is not
// simulated (there is nothing to click: no switch any more).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, '..', '..', '..');
const tmp = mkdtempSync(join(tmpdir(), 'motorstab-test-'));
const out = join(tmp, 'bundle.mjs');
const rel = (p) => resolve(src, p).split('\\').join('/');

const store = new Map();
globalThis.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k) };
const TREE = { can_write: true, dies: [
  { name: 'CILN28', slots: 24, poles: 28, stator_diameter: 200, locked: true, layer: 'shared', family: 'CILN28 200', configs: [
    { name: 'G2-L40', role: 'motor', card_date: '2026-11-01', duties: [{ name: 'rated', power_kw: 1, torque_nm: 2 }], stack_mm: 40,
      designation: 'CILN28 200_40 24F4SY', designation_complete: true, family: 'CILN28 200', beside: { steel: 'B15AHV950M' } }] },
  { name: 'CIANO28 80_11 vadim 5', slots: 24, poles: 28, stator_diameter: 80, layer: 'shared', family: 'CIANO28 80', configs: [
    { name: 'L12-F475', role: 'motor', card_date: '2026-11-01', duties: [{ name: 'rated', power_kw: 0.5, torque_nm: 1 }], stack_mm: 12,
      designation: null, designation_complete: false, display_name: 'CIANO28 80_12', family: 'CIANO28 80' }] }] };
// the few browser globals the tab's modules read at import time (no DOM is built; the page renders server-side)
globalThis.window = { location: { href: 'http://app.test/motors', origin: 'http://app.test', pathname: '/motors', search: '', hash: '' },
  addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; }, setTimeout, clearTimeout, setInterval, clearInterval,
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }), localStorage: globalThis.localStorage,
  __fetchAuthInstalled: true };
globalThis.fetch = async (url) => ({ ok: true, status: 200, json: async () => (String(url).includes('/api/family/tree') ? TREE : {}) });

writeFileSync(join(tmp, 'entry.jsx'), `
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nextProvider } from 'react-i18next';
import appI18n from '${rel('i18n/index.ts')}';
import { AuthCtx } from '${rel('contexts/AuthContext.tsx')}';
import MotorsCatalog from '${rel('components/catalog/MotorsCatalog.tsx')}';
export { fetchFamilyTree } from '${rel('lib/familyTree.ts')}';
// the APP's i18n instance: the tab's module-scope translators (nsT) read it, so the test must render with it
export const makeI18n = () => appI18n;
const auth = (isAdmin, who) => ({ user: { email: (who || (isAdmin ? 'admin' : 'member')) + '@example.test' }, loading: false,
  enabled: true, role: isAdmin ? 'admin' : (who === 'oem' ? 'oem' : 'user'), isAdmin, enforced: true, defaultMotor: null, canSolve: isAdmin, resolved: true });
export const renderTab = (i18n, isAdmin, who) => renderToStaticMarkup(React.createElement(I18nextProvider, { i18n },
  React.createElement(AuthCtx.Provider, { value: auth(isAdmin, who) }, React.createElement(MotorsCatalog))));
`);
await build({
  entryPoints: [join(tmp, 'entry.jsx')], bundle: true, platform: 'node', format: 'esm',
  outfile: out, jsx: 'automatic', logLevel: 'error', nodePaths: [resolve(src, '..', 'node_modules')],
  define: { 'import.meta.env': '{"DEV":false,"VITE_API_URL":"http://api.test"}' },
  banner: { js: "import { createRequire } from 'module'; const require = createRequire(import.meta.url);" },
});
const m = await import(pathToFileURL(out).href);
process.on('exit', () => { try { rmSync(tmp, { recursive: true, force: true }); } catch { /* */ } });
const ready = (i) => new Promise((r) => (i.isInitialized ? r() : i.on('initialized', () => r())));
await m.fetchFamilyTree();
const i18nReady = async (i) => { await ready(i); await i.changeLanguage('en'); await i.loadNamespaces(['common', 'motors', 'controller', 'development']); };                                   // the tree in the memo, as after the tab's first load

// owner 2026-11-17: "the catalog view must be the SAME for everybody" - one by-die catalog, no switch, no Ø folds
for (const [who, isAdmin] of [['admin', true], ['member', false], ['oem', false]]) {
  test(`${who}: the tab is the one by-die catalog, family headers by OD, no switch, no Ø folds`, async () => {
    const i18n = m.makeI18n(); await i18nReady(i18n);
    store.set('view.motors.byDie', 'false');                 // an old remembered "off" changes nothing any more
    const html = m.renderTab(i18n, isAdmin, who);
    assert.ok(!html.includes('by-die-switch'), html);
    assert.equal((html.match(/data-testid="catalog-by-die"/g) || []).length, 1);
    assert.ok(!html.includes('Ø 200 mm') && !html.includes('Ø 80 mm'), html);
    const a = html.indexOf('>CIANO28 80<'), b = html.indexOf('>CILN28 200<');
    assert.ok(a > 0 && b > a, 'family headers, 80 mm before 200 mm');
    assert.ok(html.includes('>CILN28 200_40 24F4SY<'), html);
    assert.ok(!html.includes('vadim') && !html.includes('G2-L40'), html);              // no stored die name, no named motor's config
    if (isAdmin) {
      assert.ok(html.includes('>L12-F475<'), html);                                   // admins only: the incomplete motor, greyed
    } else {
      // a member / the factory: no stored name with a suffix anywhere, the family and length instead (server display_name)
      assert.ok(!html.includes('L12-F475') && html.includes('>CIANO28 80_12<'), html);
    }
  });
}
