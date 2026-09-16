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
 *   • `signInLabel` — a deployment with no Google client id must not print
 *     "Sign in with Google" over a dialog that can only offer a password.
 *
 * The third check is the assets: every card names a PNG, and a card whose
 * picture 404s is worse than no card.  So the three files are opened here, at
 * the path the component asks the server for, and weighed against the 300 KB
 * ceiling the page was designed to.
 *
 * The two helpers are re-implemented verbatim below rather than imported: the
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

function signInLabel(clientId) {
  return clientId ? 'Sign in with Google' : 'Sign in';
}

function shouldShowLanding(enforced, hasUser) {
  return enforced && !hasUser;
}

/** App.tsx: nothing is decided — and NO `/api/*` call is made — until
 *  `/api/me` has answered, unless a session was restored from localStorage. */
function authPending(authResolved, hasUser) {
  return !authResolved && !hasUser;
}

/** The card list, kept in step with Landing.tsx's `FEATURES` (labels + srcs;
 *  the alt/hint prose lives in the component and is checked there by eye). */
const FEATURES = [
  { label: 'Electromagnetic FEM', src: '/landing/em-field.png' },
  { label: 'Thermal & duty cycle', src: '/landing/thermal-map.png' },
  { label: 'Mechanical simulation', src: '/landing/rotor-displacement.png' },
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

/* ── what the button promises ────────────────────────────────────────────── */

test('with a Google client id the button says so', () => {
  assert.equal(signInLabel('1234-abc.apps.googleusercontent.com'),
    'Sign in with Google');
});

test('without one it promises nothing it cannot do', () => {
  assert.equal(signInLabel(''), 'Sign in');
  assert.equal(signInLabel(undefined), 'Sign in');
  assert.equal(signInLabel(null), 'Sign in');
});

/* ── the pictures are really shipped ─────────────────────────────────────── */

test('three cards, each with a short label', () => {
  assert.equal(FEATURES.length, 3);
  for (const f of FEATURES) {
    assert.ok(f.label.length > 0 && f.label.length <= 24,
      `"${f.label}" is not a short label`);
  }
});

test('every card picture exists under public/ and is under 300 KB', () => {
  for (const f of FEATURES) {
    assert.ok(f.src.startsWith('/landing/'), `${f.src} is not served from /landing/`);
    const p = path.join(WEB, 'public', f.src.replace(/^\//, ''));
    assert.ok(fs.existsSync(p), `missing asset: ${p}`);
    const kb = fs.statSync(p).size / 1024;
    assert.ok(kb <= 300, `${f.src} is ${kb.toFixed(0)} KB — over the 300 KB budget`);
    // A PNG, not an HTML error page renamed: the 8-byte signature.
    const sig = fs.readFileSync(p).subarray(0, 8);
    assert.deepEqual([...sig], [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a],
      `${f.src} is not a PNG`);
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
  for (const f of fs.readdirSync(dir)) {
    assert.ok(named.has(f), `${f} is in public/landing but no card names it`);
  }
});
