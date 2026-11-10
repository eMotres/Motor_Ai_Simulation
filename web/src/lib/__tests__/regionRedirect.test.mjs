/** Two regions (docs/regions/DESIGN.md): the web's answer to the server's 421 `wrong_region`. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { wrongRegionTarget, regionHostAllowed } from '../regionRedirect.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const body = (base) => ({ detail: { code: 'wrong_region', region: 'us', base_url: base } });

test('a wrong_region 421 moves the tab to the home server, same path, query and hash', () => {
  assert.equal(wrongRegionTarget(421, body('https://us.aerostator.com'), 'https://aerostator.com/projects/p1?tab=spec#x'),
    'https://us.aerostator.com/projects/p1?tab=spec#x');
  assert.equal(wrongRegionTarget(421, body('https://aerostator.com/'), 'https://us.aerostator.com/'), 'https://aerostator.com/');
});

test('anything else is ignored: other status, other code, a foreign or plain-http host, a loop onto the same host', () => {
  const here = 'https://aerostator.com/x';
  assert.equal(wrongRegionTarget(409, body('https://us.aerostator.com'), here), null);
  assert.equal(wrongRegionTarget(421, { detail: { code: 'other', base_url: 'https://us.aerostator.com' } }, here), null);
  assert.equal(wrongRegionTarget(421, body('https://evil.example.com'), here), null);
  assert.equal(wrongRegionTarget(421, body('https://aerostator.com.evil.example'), here), null);
  assert.equal(wrongRegionTarget(421, body('http://us.aerostator.com'), here), null);
  assert.equal(wrongRegionTarget(421, body('https://aerostator.com'), here), null);
  assert.equal(wrongRegionTarget(421, body('not a url'), here), null);
  assert.equal(wrongRegionTarget(421, null, here), null);
  assert.ok(regionHostAllowed('cn.aerostator.com') && !regionHostAllowed('aerostator.co'));
});

test('the fetch wrapper uses it on a 421 only', () => {
  const src = readFileSync(join(HERE, '..', 'apiAuth.ts'), 'utf8');
  assert.match(src, /import \{ wrongRegionTarget \} from '\.\/regionRedirect';/);
  assert.match(src, /res\.status === 421 && isApiUrl/);
  assert.match(src, /window\.location\.assign\(target\)/);
});

// ── stage 3: the hand-off ticket and the sign-in redirect ─────────────────────
import { handoffFromLocation, safeNextPath, HANDOFF_PATH } from '../regionRedirect.ts';

const TICKET = 'v1.' + 'eyJlIjoiYUBiLmMifQ'.padEnd(40, 'A') + '.' + 'a'.repeat(64);

test('a 421 with a hand-off ticket goes to /region-handoff on the home server, path kept in the fragment', () => {
  const t = wrongRegionTarget(421, { detail: { code: 'wrong_region', base_url: 'https://us.aerostator.com', handoff: TICKET } },
    'https://aerostator.com/projects/p1?tab=spec#x');
  assert.equal(t, `https://us.aerostator.com${HANDOFF_PATH}#t=${TICKET}&next=${encodeURIComponent('/projects/p1?tab=spec')}`);
  const bad = wrongRegionTarget(421, { detail: { code: 'wrong_region', base_url: 'https://us.aerostator.com', handoff: 'v1.x.y' } },
    'https://aerostator.com/a');
  assert.equal(bad, 'https://us.aerostator.com/a');                // a malformed ticket is ignored, the move still happens
  const evil = wrongRegionTarget(421, { detail: { code: 'wrong_region', base_url: 'https://evil.example', handoff: TICKET } },
    'https://aerostator.com/a');
  assert.equal(evil, null);                                          // never a ticket onto a foreign host
});

test('a sign-in refusal opens the sign-in dialog on the home server', () => {
  const t = wrongRegionTarget(421, { detail: { code: 'wrong_region', base_url: 'https://us.aerostator.com', signin: true } },
    'https://aerostator.com/production/x?since=1');
  assert.equal(t, 'https://us.aerostator.com/production/x?since=1&signin=1');
});

test('the hand-off page reads the ticket from the fragment and only same-origin next paths', () => {
  assert.deepEqual(handoffFromLocation(HANDOFF_PATH, `#t=${TICKET}&next=%2Fprojects%2Fp1`), { ticket: TICKET, next: '/projects/p1' });
  assert.equal(handoffFromLocation('/other', `#t=${TICKET}`), null);
  assert.equal(handoffFromLocation(HANDOFF_PATH, '#t=garbage'), null);
  assert.equal(handoffFromLocation(HANDOFF_PATH, `#t=${TICKET}&next=%2F%2Fevil.example`).next, '/');
  for (const bad of ['//evil.example', 'https://evil.example', '/' + String.fromCharCode(92) + 'evil', 'relative', `${HANDOFF_PATH}#t=x`, '']) {
    assert.equal(safeNextPath(bad), '/', bad);
  }
  assert.equal(safeNextPath('/a?b=1'), '/a?b=1');
});

test('the app consumes a hand-off before its first render', () => {
  const main = readFileSync(join(HERE, '..', '..', 'main.tsx'), 'utf8');
  assert.match(main, /consumeRegionHandoff\(confirmHandoff\)\.finally\(\(\) => createRoot/);
  const la = readFileSync(join(HERE, '..', 'localAuth.ts'), 'utf8');
  assert.match(la, /post\('\/api\/auth\/region-handoff', \{ ticket: got\.ticket \}\)/);
  assert.match(la, /window\.history\.replaceState\(null, '', got\.next\)/);
});

// ── review fix: a hand-off never silently replaces another signed-in account (login CSRF) ──
import { ticketEmail, handoffNeedsConfirm } from '../regionRedirect.ts';

const ticketFor = (email) => {
  const body = Buffer.from(JSON.stringify({ e: email, to: 'us', n: 'x'.repeat(20) })).toString('base64url');
  return `v1.${body}.${'b'.repeat(64)}`;
};

test('the ticket names its account; another signed-in account must confirm', () => {
  assert.equal(ticketEmail(ticketFor('A@Example.com')), 'a@example.com');
  assert.equal(ticketEmail('garbage'), '');
  assert.equal(handoffNeedsConfirm(null, ticketFor('a@example.com')), false);           // nobody signed in
  assert.equal(handoffNeedsConfirm('a@example.com', ticketFor('a@example.com')), false); // the same account
  assert.equal(handoffNeedsConfirm('b@example.com', ticketFor('a@example.com')), true);  // another account: ask
  assert.equal(handoffNeedsConfirm('b@example.com', 'garbage'), true);                    // unreadable: ask
});

test('the app ALWAYS asks before a hand-off signs in, and changes nothing on no', () => {
  const la = readFileSync(join(HERE, '..', 'localAuth.ts'), 'utf8');
  assert.doesNotMatch(la, /if \(handoffNeedsConfirm/);                      // no "only when signed in" shortcut
  assert.match(la, /ok = confirmContinue \? await confirmContinue\(current, ticketEmail\(got\.ticket\)\) : false/);
  assert.match(la, /if \(!ok\) return false;\s+try \{\s+const \{ token, user \} = await post\('\/api\/auth\/region-handoff'/);
  const main = readFileSync(join(HERE, '..', '..', 'main.tsx'), 'utf8');
  assert.match(main, /window\.confirm\(i18n\.t\('common:auth\.handoffContinue'/);
  assert.match(main, /window\.confirm\(i18n\.t\('common:auth\.handoffReplace'/);
  for (const lang of ['en', 'zh-CN']) {
    const common = JSON.parse(readFileSync(join(HERE, '..', '..', 'locales', lang, 'common.json'), 'utf8'));
    assert.match(common.auth.handoffReplace, /\{current\}.*\{incoming\}/s, lang);
    assert.match(common.auth.handoffContinue, /\{incoming\}/, lang);
  }
});
