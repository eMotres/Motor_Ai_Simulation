/**
 * The landing page's BRANCHES, tested without a browser.
 *
 * Two of them decide what a visitor sees before they have an account, and both
 * are easy to get wrong in a way nobody notices from a signed-in session:
 *
 *   • `shouldShowLanding` — the landing must appear ONLY on a backend that
 *     enforces auth and only while nobody is signed in.  Get it wrong the one
 *     way and the local dev server boots into a poster instead of the app; get
 *     it wrong the other way and production shows the empty shell again.
 *   • the way in — the primary buttons say "Create free account ...", one line
 *     says it is free and instant, "Request access" is gone (it only ever opened
 *     the create-account form), and both locales carry the same keys.
 *
 * The third check is the assets: every card names a PNG, and a card whose
 * picture 404s is worse than no card.  So the three files are opened here, at
 * the path the component asks the server for, and weighed against the 300 KB
 * ceiling the page was designed to.
 *
 * The helpers are re-implemented verbatim below rather than imported: the
 * module is TypeScript and pulls in `import.meta.env` through `lib/localAuth`,
 * which `node --test` cannot load (same reason as common/progressStrip.test).
 * Changing `Landing.tsx` therefore has to change this file too — and that is
 * the moment someone has to justify the new behaviour.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
/** web/ — four levels up from src/components/landing/__tests__/ */
const WEB = path.resolve(HERE, '..', '..', '..', '..');

/* ── verbatim copies of the shipped helpers ──────────────────────────────── */

function shouldShowLanding(enforced, hasUser) {
  return enforced && !hasUser;
}

/** App.tsx: nothing is decided — and NO `/api/*` call is made — until
 *  `/api/me` has answered, unless a session was restored from localStorage. */
function authPending(authResolved, hasUser) {
  return !authResolved && !hasUser;
}

/** contexts/AuthContext.tsx `apiReady` — the gate every boot-time fetch is
 *  behind (mesh config, materials library, assignments, part states, module
 *  manifests, the working-motor POST). */
function apiReady(resolved, hasUser, enforced) {
  if (!resolved && !hasUser) return false;
  return !enforced || hasUser;
}

/** The card list, kept in step with Landing.tsx's `FEATURES` (key + src; the
 *  label, alt and hint prose live in locales/<lng>/landing.json). */
const FEATURES = [
  { key: 'em', src: '/landing/em-field.webp', w: 884, h: 771 },
  { key: 'thermal', src: '/landing/thermal-map.webp', w: 884, h: 772 },
  { key: 'mech', src: '/landing/rotor-displacement.webp', w: 884, h: 749 },
];

/* ── who gets the landing ────────────────────────────────────────────────── */

test('an anonymous visitor on the production backend gets the landing', () => {
  assert.equal(shouldShowLanding(true, false), true);
});

test('a signed-in user never gets the landing', () => {
  assert.equal(shouldShowLanding(true, true), false);
});

test('the local dev server (auth not enforced) never gets the landing', () => {
  // `signedIn = !enforced || !!user` — with enforcement off the app boots
  // straight into the workspace, signed in or not, exactly as it always did.
  assert.equal(shouldShowLanding(false, false), false);
  assert.equal(shouldShowLanding(false, true), false);
});

/* ── nothing before /api/me has answered ─────────────────────────────────── */

test('an anonymous first paint waits for /api/me', () => {
  // `enforced` starts false, so acting on it before the answer boots the
  // workspace at a closed server and 401s in the visitor's console.
  assert.equal(authPending(false, false), true);
  assert.equal(authPending(true, false), false);
});

test('a restored session never waits — a signed-in boot is unchanged', () => {
  assert.equal(authPending(false, true), false);
  assert.equal(authPending(true, true), false);
});

/* ── the gate the boot fetches sit behind ────────────────────────────────── */

test('an anonymous visit calls nothing — before or after /api/me answers', () => {
  // Before the answer (`enforced` still false, its useless default) …
  assert.equal(apiReady(false, false, false), false);
  // … and after it, on a server that publishes nothing: six 401s measured on
  // one anonymous load, 2026-09-16 (parts, materials, materials/library,
  // modules, mesh/config, POST presets).
  assert.equal(apiReady(true, false, true), false);
});

test('signing in opens the gate', () => {
  assert.equal(apiReady(true, true, true), true);
});

test('a restored session calls immediately, and the dev server always does', () => {
  // `user` comes back from localStorage synchronously — no wait, so a
  // signed-in boot is byte for byte what it was …
  assert.equal(apiReady(false, true, true), true);
  // … and an UNENFORCED backend (local dev) opens the moment /api/me answers,
  // signed in or not, exactly as it always did.
  assert.equal(apiReady(true, false, false), true);
});

/* ── what the buttons promise ────────────────────────────────────────────── */

const heroOf = (lng) => JSON.parse(fs.readFileSync(
  path.join(WEB, 'src', 'locales', lng, 'landing.json'), 'utf8')).hero;

test('the primary buttons are free-account buttons, with one line saying it is free', () => {
  const h = heroOf('en');
  assert.equal(h.createGoogle, 'Create free account with Google');
  assert.equal(h.createEmail, 'Create free account with e-mail');
  assert.equal(h.free, 'Free · instant · no approval needed');
  assert.equal(h.haveAccount, 'Already have an account?');
  assert.equal(h.signInLink, 'Sign in');
});

test('"Request access" and the old sign-in buttons are gone from both locales', () => {
  for (const lng of ['en', 'zh-CN']) {
    const h = heroOf(lng);
    for (const k of ['request', 'signIn', 'signInGoogle', 'signInEmail']) {
      assert.equal(k in h, false, `${lng}: hero.${k} should be removed`);
    }
  }
});

test('the zh-CN hero mirrors the English keys exactly', () => {
  assert.deepEqual(Object.keys(heroOf('zh-CN')).sort(), Object.keys(heroOf('en')).sort());
});

test('the landing never prints "Request access" or a mailto for signing up', () => {
  const src = fs.readFileSync(path.join(WEB, 'src', 'components', 'landing', 'Landing.tsx'), 'utf8');
  assert.doesNotMatch(src, /hero\.request/);
  assert.match(src, /hero\.createEmail/);
  assert.match(src, /hero\.free/);
});

/* ── the pictures are really shipped ─────────────────────────────────────── */

test('three cards, each with a short label in both locales', () => {
  assert.equal(FEATURES.length, 3);
  for (const lng of ['en', 'zh-CN']) {
    const sim = JSON.parse(fs.readFileSync(
      path.join(WEB, 'src', 'locales', lng, 'landing.json'), 'utf8')).sim;
    for (const f of FEATURES) {
      const label = sim[f.key];
      assert.ok(label && label.length <= 24, `${lng}: "${label}" is not a short label`);
      assert.ok(sim[f.key + 'Alt'] && sim[f.key + 'Hint'], `${lng}: ${f.key} lacks alt/hint`);
    }
  }
});

test('every card picture exists under public/, is a WebP under 150 KB and matches its declared size', async () => {
  const { webpSize } = await import('../../../../scripts/prerender-landing.mjs');
  for (const f of FEATURES) {
    assert.ok(f.src.startsWith('/landing/'), `${f.src} is not served from /landing/`);
    assert.ok(f.src.endsWith('.webp'), `${f.src} is not a WebP`);
    const p = path.join(WEB, 'public', f.src.replace(/^\//, ''));
    assert.ok(fs.existsSync(p), `missing asset: ${p}`);
    const buf = fs.readFileSync(p);
    const kb = buf.length / 1024;
    assert.ok(kb <= 150, `${f.src} is ${kb.toFixed(0)} KB, over the 150 KB budget`);
    // A WebP, not an HTML error page renamed: RIFF....WEBP, and the size the <img> declares.
    assert.equal(buf.toString('latin1', 0, 4), 'RIFF');
    assert.equal(buf.toString('latin1', 8, 12), 'WEBP');
    assert.deepEqual(webpSize(buf), { w: f.w, h: f.h }, `${f.src}: Landing.tsx declares another size`);
  }
  // Landing.tsx lists exactly these files with exactly these sizes.
  const tsx = fs.readFileSync(path.join(WEB, 'src', 'components', 'landing', 'Landing.tsx'), 'utf8');
  for (const f of FEATURES) {
    assert.ok(tsx.includes(`src: '${f.src}', w: ${f.w}, h: ${f.h}`), `Landing.tsx FEATURES out of step for ${f.key}`);
  }
});

test('each card names a different picture', () => {
  assert.equal(new Set(FEATURES.map((f) => f.src)).size, FEATURES.length);
});

test('nothing is left behind in public/landing', () => {
  // The Campbell diagram was the third card until 2026-09-16 and is not shipped
  // any more (user: show the rotor with its sleeve instead).  An asset nothing
  // references is 16 KB every visitor's browser is never told about, and the
  // next reader cannot tell whether it is dead or a card someone forgot.
  const dir = path.join(WEB, 'public', 'landing');
  const named = new Set(FEATURES.map((f) => path.basename(f.src)));
  // The concept diagram's centre is a static render of the CIANO14 40 L12 model
  // (never the GLB itself): it must be named by ConceptDiagram.tsx.
  const diagram = fs.readFileSync(path.join(WEB, 'src', 'components', 'landing', 'ConceptDiagram.tsx'), 'utf8');
  assert.ok(diagram.includes('/landing/core-l12.webp'), 'ConceptDiagram must show core-l12.webp');
  named.add('core-l12.webp');
  for (const f of fs.readdirSync(dir)) {
    assert.ok(named.has(f), `${f} is in public/landing but no card names it`);
  }
});

/* ── Root: the small landing shell vs the lazy workspace ─────────────────── */
const { showsLandingShell } = await import('../../../lib/rootGate.ts');

test('Root: a signed-out visitor boots the landing shell, before and after /api/me', () => {
  assert.equal(showsLandingShell(false, false, false), true, 'before the answer: landing at once');
  assert.equal(showsLandingShell(false, true, true), true, 'enforced backend: landing stays');
});

test('Root: a signed-in user and the unenforced dev server get the (lazy) workspace', () => {
  assert.equal(showsLandingShell(true, false, false), false, 'restored session: no wait');
  assert.equal(showsLandingShell(true, true, true), false);
  assert.equal(showsLandingShell(false, true, false), false, 'local dev server');
});

test('the first-load bundle: main.tsx imports only Root; App and the heavy tabs are lazy', () => {
  const main = fs.readFileSync(path.join(WEB, 'src', 'main.tsx'), 'utf8');
  assert.match(main, /from '\.\/Root'/);
  assert.doesNotMatch(main, /from '\.\/App/);
  const root = fs.readFileSync(path.join(WEB, 'src', 'Root.tsx'), 'utf8');
  assert.match(root, /lazy\((watchChunkImport\()?\(\) => import\('\.\/App'\)\)/);
  // the workspace itself: Motors and Configure are static, every other tab body is its own chunk
  const app = fs.readFileSync(path.join(WEB, 'src', 'App.tsx'), 'utf8');
  const appStatic = [...app.matchAll(/^import[^;]*from '([^']+)'/gm)].map((m) => m[1]);
  for (const heavy of ['three', 'recharts', 'firebase', 'viewer3d/', 'simulation/', 'static3d/', 'model3d/', 'mechanical/', 'thermal/',
    'controller/', 'system/', 'sweep/', 'cost/', 'mesh/', 'admin/', 'geometry/', 'production/', 'development/', 'designer/']) {
    assert.ok(!appStatic.some((p) => p.includes(heavy)), `App.tsx must not import ${heavy} statically`);
  }
  assert.ok(appStatic.includes('./components/catalog/MotorsCatalog') && appStatic.includes('./components/compare/CompareTab'));
  const shell = fs.readFileSync(path.join(WEB, 'src', 'components', 'landing', 'LandingShell.tsx'), 'utf8');
  const imports = [...shell.matchAll(/^import[^;]*from '([^']+)'/gm)].map((m) => m[1]).join(' ');
  for (const heavy of ['three', 'recharts', '@xyflow', 'motorStore', 'AdminPanel', 'MotorScene']) {
    assert.ok(!imports.includes(heavy), `LandingShell must not import ${heavy}`);
  }
});

/* ── /api/me cannot be read: never Local Mode for a signed-out visitor in production ── */
const { meFailureAction, meRetryDelayMs, meResponseUsable } = await import('../../../lib/authFallback.ts');

test('/api/me failure: a production build retries (landing stays), only a dev build goes Local Mode', () => {
  assert.equal(meFailureAction(false), 'retry', 'production: no invented answer');
  assert.equal(meFailureAction(true), 'local-mode', 'dev server keeps working without the API');
});

test('/api/me failure keeps a signed-out visitor on the landing, with a backoff that is capped', () => {
  // unresolved + no user -> landing shell, whatever the retry count is
  assert.equal(showsLandingShell(false, false, false), true);
  assert.deepEqual([0, 1, 2, 3, 4, 5, 9].map(meRetryDelayMs), [2000, 4000, 8000, 16000, 30000, 30000, 30000]);
  assert.equal(meRetryDelayMs(-3), 2000);
});

test('/api/me: a gateway error is a failure, an explicit {enforced:false} answer is a real answer', () => {
  for (const st of [500, 502, 503, 504]) assert.equal(meResponseUsable(st), false, String(st));
  for (const st of [200, 401, 403]) assert.equal(meResponseUsable(st), true, String(st));
  // the server's own explicit unenforced answer (resolved=true, enforced=false) opens the workspace
  assert.equal(showsLandingShell(false, true, false), false);
});

test('AuthContext: the catch branch asks authFallback; Local Mode is not a network-failure fallback', () => {
  const src = fs.readFileSync(path.join(WEB, 'src', 'contexts', 'AuthContext.tsx'), 'utf8');
  const catchAt = src.search(/\} catch \{\s*\/\/ COULD NOT ASK/);
  assert.ok(catchAt > 0, 'the catch that decides the failure is missing');
  const block = src.slice(catchAt, src.indexOf('}, []);', catchAt));
  assert.match(block, /meFailureAction\(import\.meta\.env\.DEV\) === 'local-mode'/);
  assert.match(block, /meRetryDelayMs\(/);
  // setEnforced(false)+setResolved(true) only inside the dev-only branch
  const unconditional = block.replace(/if \(meFailureAction[\s\S]*?\} else \{/, '');
  assert.ok(!/setEnforced\(false\)/.test(unconditional), 'setEnforced(false) outside the dev-only branch');
  assert.match(src, /meResponseUsable\(r\.status\)/);
});
