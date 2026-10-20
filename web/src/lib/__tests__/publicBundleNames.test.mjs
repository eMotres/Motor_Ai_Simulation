// node --test — the PUBLIC frontend bundle names no private machine.
//
// Why (independent review F4, 2026-10-20): the landing tooltip called the simulation picture
// a "200 mm machine" and Configure shipped a built-in 200 mm reference passport.  API
// visibility cannot protect strings and constants delivered in the JS bundle, so the shipped
// SOURCE (locales, non-test .ts/.tsx/.mjs/.js code with comments removed, and public/ text
// files) must not carry the names of closed machines.
//
// Public machines: CIANO14 40 L12/L20, CIANO14 50 L15, CIANO28 85 L15.
// Private (never in the bundle): the 200 mm family (CIANO10 200 / L155 / L180 / M1 850 Nm /
// NUAA), CIANO28 150_35, 85 L13, CILN28 / G2.
//
// Comments and test files are NOT shipped and are not scanned.
import test from 'node:test';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..');

const PRIVATE_NAMES = [
  /\b200\s?mm\b/i, /Ø\s?200\b/, /\bCIANO\s?10\b/i, /150[_-]35/, /\bL155\b/, /\bL180\b/,
  /\bNUAA\b/i, /\bCILN28\b/i, /\bG2[-_ ]L40\b/i, /\bM1[-_ ]?850/i,
  // 85 L13 (hidden): its name in any spelling.  Its card id and audited-record hashes are matched by DIGEST below
  // (this test file is public, so it must not hold them).
  /\bL13\b/i, /_l13\b/i, /\bD85\b/i,
];

// SHA-256 digests of lower-cased tokens that must not appear in the shipped bundle: the hidden card id, and the
// first 16 hex characters of its candidate pin and of its source-record hash.  Digests only: the guard is public.
const PRIVATE_TOKEN_DIGESTS = new Set([
  '4d79db9394d5c6d51b6ba800d0523a2936cc9477b662a15bde9f4d8bc38d6acd',
  '7ed32ad2136389ff8cce7cc9ddafbbf472c23752223d1f6b24311bdd74cc003d',
  'b7a7db0e459b724ed9e35c9dc4462082763acec5979ebab66f3009cf3faa1ee0',
]);
const sha = (x) => createHash('sha256').update(x).digest('hex');

function hasPrivateToken(line) {
  for (const m of line.toLowerCase().matchAll(/[a-z0-9_]+/g)) {
    const tok = m[0];
    if (PRIVATE_TOKEN_DIGESTS.has(sha(tok))) return true;
    if (/^[0-9a-f]{16,}$/.test(tok) && PRIVATE_TOKEN_DIGESTS.has(sha(tok.slice(0, 16)))) return true;
  }
  return false;
}

// Reviewed exceptions (file relative to web/ -> why).  Empty on purpose: a private name in the
// shipped bundle is fixed at the source (the server supplies the data), not allow-listed.
const KNOWN_EXCEPTIONS = {};

const TEST_FILE = /(^|[\/])(__tests__|__mocks__)[\/]|\.(test|spec)\.[cm]?[jt]sx?$/;

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.name === 'node_modules' || e.name === 'dist') continue;
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out); else out.push(p);
  }
  return out;
}

/** Remove // and block comments, keeping string / template / regex-free code (good enough for a name scan). */
export function stripComments(src) {
  let out = '';
  let i = 0;
  const n = src.length;
  let quote = null;       // ' " `
  let tplDepth = [];      // brace depth stack for `${` inside templates
  while (i < n) {
    const c = src[i], d = src[i + 1];
    if (quote) {
      out += c;
      if (c === String.fromCharCode(92)) { out += src[i + 1] ?? ''; i += 2; continue; }
      if (quote === '`' && c === '$' && d === '{') { out += '{'; i += 2; tplDepth.push(1); quote = null; continue; }
      if (c === quote) quote = null;
      i++; continue;
    }
    if (c === '/' && d === '*') { const j = src.indexOf('*/', i + 2); i = j < 0 ? n : j + 2; out += ' '; continue; }
    if (c === '/' && d === '/' && src[i - 1] !== ':') { while (i < n && src[i] !== '\n') i++; continue; }
    if (c === '"' || c === "'" || c === '`') { quote = c; out += c; i++; continue; }
    if (tplDepth.length) {
      if (c === '{') tplDepth[tplDepth.length - 1]++;
      else if (c === '}') { tplDepth[tplDepth.length - 1]--; if (tplDepth[tplDepth.length - 1] === 0) { tplDepth.pop(); quote = '`'; } }
    }
    out += c; i++;
  }
  return out;
}

function shippedFiles() {
  const files = [];
  for (const f of walk(path.join(WEB, 'src'))) {
    const rel = path.relative(WEB, f).replace(new RegExp(String.fromCharCode(92)+String.fromCharCode(92),'g'), '/');
    if (TEST_FILE.test(rel)) continue;
    if (/\.(json)$/.test(rel) && rel.startsWith('src/locales/')) files.push({ rel, f, code: false });
    else if (/\.(tsx?|mjs|js|jsx)$/.test(rel)) files.push({ rel, f, code: true });
  }
  if (fs.existsSync(path.join(WEB, 'index.html'))) files.push({ rel: 'index.html', f: path.join(WEB, 'index.html'), code: false });
  const pub = path.join(WEB, 'public');
  if (fs.existsSync(pub)) {
    for (const f of walk(pub)) {
      const rel = path.relative(WEB, f).replace(new RegExp(String.fromCharCode(92)+String.fromCharCode(92),'g'), '/');
      if (/\.(txt|json|webmanifest|html|xml|svg)$/.test(rel)) files.push({ rel, f, code: false });
    }
  }
  return files;
}

export function findHits(text) {
  const hits = [];
  text.split(/\r?\n/).forEach((line, i) => {
    if (PRIVATE_NAMES.some((re) => re.test(line)) || hasPrivateToken(line)) hits.push(`${i + 1}: ${line.trim().slice(0, 120)}`);
  });
  return hits;
}

test('stripComments drops comments but keeps strings', () => {
  const s = "const a = 'L155'; // L180\n/* NUAA */ const b = `x ${'CILN28'} y`; const u = 'http://x';";
  const t = stripComments(s);
  assert.ok(t.includes("'L155'") && t.includes("'CILN28'"));
  assert.ok(!t.includes('L180') && !t.includes('NUAA'));
  assert.ok(t.includes('http://x'));
});

test('the detector flags every private name', () => {
  for (const s of ['a 200 mm machine', 'Ø200 rotor', 'CIANO10 200 opt', 'CIANO28 150_35', 'L155 motor', 'L180 gen',
    'NUAA 1 MW', 'CILN28 / x', 'G2-L40', 'M1 850Nm', 'the Ø85 L13', 'D85 card']) {
    assert.ok(findHits(s).length > 0, s);
  }
  // the digest list: a synthetic token and a synthetic 16-hex prefix are caught once their digests are listed
  const synthId = 'x9_synthetic_card_id';
  const synthHash = 'deadbeefdeadbeef0123456789';
  assert.equal(findHits(`id: ${synthId} h: ${synthHash}`).length, 0);
  PRIVATE_TOKEN_DIGESTS.add(sha(synthId));
  PRIVATE_TOKEN_DIGESTS.add(sha(synthHash.slice(0, 16)));
  try {
    assert.ok(findHits(`id: ${synthId}`).length > 0);
    assert.ok(findHits(`h: ${synthHash}`).length > 0);
  } finally {
    PRIVATE_TOKEN_DIGESTS.delete(sha(synthId));
    PRIVATE_TOKEN_DIGESTS.delete(sha(synthHash.slice(0, 16)));
  }
  for (const s of ['CIANO14 40 L12', 'CIANO14 50 L15', 'CIANO28 85 L15', 'a 40 mm motor', '2000 mm cable']) {
    assert.equal(findHits(s).length, 0, s);
  }
});

test('shipped locales, code and public text name no private machine', () => {
  const bad = [];
  const files = shippedFiles();
  assert.ok(files.length > 50, 'the scan found suspiciously few files');
  for (const { rel, f, code } of files) {
    if (rel in KNOWN_EXCEPTIONS) continue;
    let text = fs.readFileSync(f, 'utf8');
    if (code) text = stripComments(text);
    for (const h of findHits(text)) bad.push(`${rel}:${h}`);
  }
  assert.deepEqual(bad, [], `private machine names in the public bundle:\n${bad.join('\n')}`);
});

test('no reviewed exception is left', () => {
  assert.deepEqual(Object.keys(KNOWN_EXCEPTIONS), []);
});
