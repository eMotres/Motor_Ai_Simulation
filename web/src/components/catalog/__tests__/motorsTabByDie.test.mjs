// node --test: the MOTORS TAB itself (MotorsCatalog) with the by-die switch (coordinator's browser finding on #730: the
// switch lived only in the non-embedded catalog, which the tab never renders).  esbuild bundles the real tab; it is
// rendered server-side with the real EN locale files, the tree answered by a stubbed fetch and held in lib/familyTree's
// memo, the remembered view in a stubbed localStorage.  No DOM library is in the repository, so the click itself is not
// simulated: the switch's state is the remembered value a click writes (`view.motors.byDie`).
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
  { name: 'CILN28', slots: 24, poles: 28, stator_diameter: 200, locked: true, layer: 'shared', configs: [
    { name: 'G2-L40', role: 'motor', duties: [], stack_mm: 40, designation: 'CILN28 200_40 24F4SY', designation_complete: true,
      family: 'CILN28 200', beside: { steel: 'B15AHV950M', magnet: 'N52UH_150C' } }] },
  { name: 'CIANO28 80_12', slots: 24, poles: 28, stator_diameter: 80, layer: 'shared', configs: [
    { name: 'L12-F475', role: 'motor', duties: [], stack_mm: 12, designation: 'CIANO28 80_12 60R4SY', designation_complete: true,
      family: 'CIANO28 80', beside: { steel: '20SW1200' } }] }] };
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
const auth = (isAdmin) => ({ user: isAdmin ? { email: 'admin@example.test' } : { email: 'member@example.test' }, loading: false,
  enabled: true, role: isAdmin ? 'admin' : 'user', isAdmin, enforced: true, defaultMotor: null, canSolve: isAdmin, resolved: true });
export const renderTab = (i18n, isAdmin) => renderToStaticMarkup(React.createElement(I18nextProvider, { i18n },
  React.createElement(AuthCtx.Provider, { value: auth(isAdmin) }, React.createElement(MotorsCatalog))));
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
const i18nReady = async (i) => { await ready(i); await i.changeLanguage('en'); await i.loadNamespaces(['common', 'motors', 'controller']); };                                   // the tree in the memo, as after the tab's first load

test('an admin finds the switch on the TAB; off, the tab shows the Ø sections', async () => {
  const i18n = m.makeI18n(); await i18nReady(i18n);
  store.delete('view.motors.byDie');
  const html = m.renderTab(i18n, true);
  assert.match(html, /data-testid="by-die-switch"[^>]*aria-pressed="false"|aria-pressed="false"[^>]*data-testid="by-die-switch"/);
  assert.ok(html.includes('Ø 200 mm') && html.includes('Ø 80 mm') && !html.includes('data-testid="catalog-by-die"'), html);
});

test('with the switch on, ONE view holds every die: family headers across the diameters, sorted by OD, and the designations', async () => {
  const i18n = m.makeI18n(); await i18nReady(i18n);
  store.set('view.motors.byDie', 'true');                    // what the click writes
  const html = m.renderTab(i18n, true);
  assert.match(html, /aria-pressed="true"/);
  assert.equal((html.match(/data-testid="catalog-by-die"/g) || []).length, 1);
  assert.ok(!html.includes('Ø 200 mm') && !html.includes('Ø 80 mm'), html);   // no diameter folds in this view
  const a = html.indexOf('>CIANO28 80<'), b = html.indexOf('>CILN28 200<');
  assert.ok(a > 0 && b > a, 'family headers, 80 mm before 200 mm');
  assert.ok(html.includes('>CILN28 200_40 24F4SY<') && html.includes('>CIANO28 80_12 60R4SY<'), html);
});

test('a non-admin finds no switch, and a stored "on" does not switch the view for them', async () => {
  const i18n = m.makeI18n(); await i18nReady(i18n);
  store.set('view.motors.byDie', 'true');
  const html = m.renderTab(i18n, false);
  assert.ok(!html.includes('by-die-switch') && !html.includes('catalog-by-die'), html);
});
